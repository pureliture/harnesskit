//! Rust-owned, identity-checked install apply and verify.

use std::collections::{BTreeMap, BTreeSet};
use std::ffi::OsStr;
use std::fmt;
use std::os::fd::{AsFd, BorrowedFd, OwnedFd};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use rustix::fs::{
    fchmod, fstat, fsync, mkdirat, open, openat, renameat_with, statat, unlinkat, AtFlags,
    FileType, Mode, OFlags, RenameFlags, Stat,
};
use rustix::io::{pread, Errno};
use sha2::{Digest, Sha256};

use crate::projection::{
    normalized_relative_locator, root_identity, DestinationKey, DestinationScope,
};

use super::merge::{exact_copy, merge_json_deep, merge_managed_block, merge_toml_agents};
use super::plan::{PlanArtifact, ValidatedPlan};
use super::target_contract::embedded_target_contract;
use super::workspace::InstallWorkspace;

pub const INSTALL_WRITER_VERSION: u32 = 1;
pub const INSTALL_VERIFIER_VERSION: u32 = 1;
const WRITER_DEADLINE_MILLIS: u64 = 180_000;
const VERIFIER_DEADLINE_MILLIS: u64 = 60_000;
const MAX_INSTALL_FILE_BYTES: u64 = 8 * 1024 * 1024;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(1);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InstallOperationStatus {
    Complete,
    Partial,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DestinationApplyState {
    Applied,
    Unchanged,
    AppliedWithWarning,
    Failed,
    NotAttempted,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallDestinationOutcome {
    pub target: String,
    pub destination: String,
    pub state: DestinationApplyState,
    pub code: Option<String>,
    pub content_sha256: Option<String>,
    pub mode: Option<u32>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallApplyReport {
    status: InstallOperationStatus,
    destinations: Vec<InstallDestinationOutcome>,
    validated_fingerprint: String,
    target_root_identities: BTreeMap<String, (u64, u64)>,
}

impl InstallApplyReport {
    pub fn status(&self) -> InstallOperationStatus {
        self.status
    }

    pub fn destinations(&self) -> &[InstallDestinationOutcome] {
        &self.destinations
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DestinationVerifyState {
    Verified,
    Failed,
    NotApplied,
    NotAttempted,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifyDestinationOutcome {
    pub target: String,
    pub destination: String,
    pub state: DestinationVerifyState,
    pub code: Option<String>,
    pub content_sha256: Option<String>,
    pub correlation_keys: Vec<DestinationKey>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallVerifyReport {
    status: InstallOperationStatus,
    destinations: Vec<VerifyDestinationOutcome>,
}

impl InstallVerifyReport {
    pub fn status(&self) -> InstallOperationStatus {
        self.status
    }

    pub fn destinations(&self) -> &[VerifyDestinationOutcome] {
        &self.destinations
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallTargetRoots {
    roots: BTreeMap<String, PathBuf>,
    expected_identities: BTreeMap<String, (u64, u64)>,
}

impl InstallTargetRoots {
    pub fn new(
        roots: BTreeMap<String, PathBuf>,
        expected_identities: BTreeMap<String, (u64, u64)>,
    ) -> Result<Self, InstallWriterError> {
        if roots.is_empty()
            || roots.keys().ne(expected_identities.keys())
            || roots
                .iter()
                .any(|(target, path)| target.is_empty() || !canonical_absolute_path(path))
        {
            return Err(InstallWriterError::new("target_root_invalid"));
        }
        Ok(Self {
            roots,
            expected_identities,
        })
    }

    fn target_ids(&self) -> BTreeSet<String> {
        self.roots.keys().cloned().collect()
    }
}

pub trait InstallClock: Send + Sync + 'static {
    fn now_millis(&self) -> u64;
}

pub trait InstallWriteHook: Send + Sync + 'static {
    fn before_replace(&self, target: &str, destination: &str);

    fn after_destination_check(&self, _target: &str, _destination: &str) {}
}

pub trait InstallVerifyHook: Send + Sync + 'static {
    fn before_correlation_keys(&self, _target: &str, _destination: &str) {}
}

struct SystemInstallClock {
    origin: Instant,
}

impl Default for SystemInstallClock {
    fn default() -> Self {
        Self {
            origin: Instant::now(),
        }
    }
}

impl InstallClock for SystemInstallClock {
    fn now_millis(&self) -> u64 {
        self.origin.elapsed().as_millis().min(u64::MAX as u128) as u64
    }
}

struct NoopInstallWriteHook;
struct NoopInstallVerifyHook;

impl InstallWriteHook for NoopInstallWriteHook {
    fn before_replace(&self, _target: &str, _destination: &str) {}
}

impl InstallVerifyHook for NoopInstallVerifyHook {}

pub struct InstallWriter {
    clock: Arc<dyn InstallClock>,
    hook: Arc<dyn InstallWriteHook>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct InstallArtifactAuthorityRecord {
    pub component_id: String,
    pub target: String,
    pub scope: String,
    pub destination: String,
    pub source: String,
    pub merge_strategy: Option<String>,
    pub config_entry_locator: Option<String>,
    pub content_sha256: String,
    pub exact_bytes: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct InstallArtifactAuthority {
    pub records: Vec<InstallArtifactAuthorityRecord>,
}

impl Default for InstallWriter {
    fn default() -> Self {
        Self {
            clock: Arc::new(SystemInstallClock::default()),
            hook: Arc::new(NoopInstallWriteHook),
        }
    }
}

impl InstallWriter {
    pub fn with_clock(clock: Arc<dyn InstallClock>) -> Self {
        Self {
            clock,
            hook: Arc::new(NoopInstallWriteHook),
        }
    }

    pub fn with_ports(clock: Arc<dyn InstallClock>, hook: Arc<dyn InstallWriteHook>) -> Self {
        Self { clock, hook }
    }

    pub(crate) fn validate_artifact_authority(
        &self,
        validated: &ValidatedPlan,
        workspace: &InstallWorkspace,
        authority: &InstallArtifactAuthority,
    ) -> Result<(), InstallWriterError> {
        validate_artifact_authority(validated, workspace, authority)
    }

    pub fn apply(
        &self,
        validated: &ValidatedPlan,
        workspace: &InstallWorkspace,
        target_roots: &InstallTargetRoots,
    ) -> Result<InstallApplyReport, InstallWriterError> {
        validate_apply_inputs(validated, workspace, target_roots)?;
        let started = self.clock.now_millis();
        let (opened_roots, root_errors) = open_target_roots(target_roots);
        if root_errors
            .values()
            .any(|code| *code == "target_root_identity_changed")
        {
            return Err(InstallWriterError::new("target_root_identity_changed"));
        }
        let mut destinations = Vec::with_capacity(validated.artifact_count());
        let mut deadline_reached = false;

        for artifact in validated.artifacts() {
            if deadline_reached
                || elapsed(self.clock.now_millis(), started) >= WRITER_DEADLINE_MILLIS
            {
                deadline_reached = true;
                destinations.push(apply_failure(
                    artifact,
                    DestinationApplyState::NotAttempted,
                    "writer_deadline_exceeded",
                ));
                continue;
            }
            let outcome = match opened_roots.get(&artifact.target) {
                Some(root) => self.apply_one(workspace, root, artifact, started),
                None => Err(root_errors
                    .get(&artifact.target)
                    .copied()
                    .unwrap_or("destination_root_unavailable")),
            };
            destinations.push(match outcome {
                Ok(applied) => InstallDestinationOutcome {
                    target: artifact.target.clone(),
                    destination: artifact.destination.clone(),
                    state: applied.state,
                    code: applied.code.map(str::to_string),
                    content_sha256: Some(applied.content_sha256),
                    mode: Some(applied.mode),
                },
                Err(code) => apply_failure(artifact, DestinationApplyState::Failed, code),
            });
        }
        Ok(InstallApplyReport {
            status: summarize_apply(&destinations),
            destinations,
            validated_fingerprint: validated.semantic_fingerprint().to_string(),
            target_root_identities: opened_roots
                .iter()
                .map(|(target, root)| (target.clone(), root.identity))
                .collect(),
        })
    }

    fn apply_one(
        &self,
        workspace: &InstallWorkspace,
        target_root: &OpenedTargetRoot,
        artifact: &PlanArtifact,
        started: u64,
    ) -> Result<AppliedDestination, &'static str> {
        let source = read_source(workspace.root_fd(), artifact)?;
        let (parent, leaf) = open_destination_parent(&target_root.fd, &artifact.destination, true)?;
        let existing = read_destination(parent.as_fd(), &leaf)?;
        let output = render_destination(artifact, existing.as_ref(), &source.bytes)?;
        self.ensure_before_deadline(started)?;
        let mode = match (&existing, artifact.merge_strategy.as_deref()) {
            (Some(existing), Some(_)) => existing.mode,
            _ => source.mode,
        };
        if existing
            .as_ref()
            .is_some_and(|current| current.bytes == output && current.mode == mode)
        {
            self.ensure_before_deadline(started)?;
            if !destination_unchanged(parent.as_fd(), &leaf, existing.as_ref())? {
                return Err("destination_changed");
            }
            return Ok(AppliedDestination {
                state: DestinationApplyState::Unchanged,
                code: None,
                content_sha256: sha256(&output),
                mode,
            });
        }
        let mut temp = TempSiblingGuard::create(parent.as_fd())?;
        temp.write(&output, mode)?;
        self.hook
            .before_replace(&artifact.target, &artifact.destination);
        if !destination_unchanged(parent.as_fd(), &leaf, existing.as_ref())? {
            return Err("destination_changed");
        }
        self.hook
            .after_destination_check(&artifact.target, &artifact.destination);
        self.ensure_before_deadline(started)?;
        let replacement_warning =
            atomic_replace(parent.as_fd(), &leaf, existing.as_ref(), &mut temp)?;
        let content_sha256 = sha256(&output);
        match (replacement_warning, fsync(parent.as_fd())) {
            (None, Ok(())) => Ok(AppliedDestination {
                state: DestinationApplyState::Applied,
                code: None,
                content_sha256,
                mode,
            }),
            (warning, _) => Ok(AppliedDestination {
                state: DestinationApplyState::AppliedWithWarning,
                code: warning.or(Some("destination_directory_sync_failed")),
                content_sha256,
                mode,
            }),
        }
    }

    fn ensure_before_deadline(&self, started: u64) -> Result<(), &'static str> {
        (elapsed(self.clock.now_millis(), started) < WRITER_DEADLINE_MILLIS)
            .then_some(())
            .ok_or("writer_deadline_exceeded")
    }
}

pub struct InstallVerifier {
    clock: Arc<dyn InstallClock>,
    hook: Arc<dyn InstallVerifyHook>,
}

impl Default for InstallVerifier {
    fn default() -> Self {
        Self {
            clock: Arc::new(SystemInstallClock::default()),
            hook: Arc::new(NoopInstallVerifyHook),
        }
    }
}

impl InstallVerifier {
    pub fn with_clock(clock: Arc<dyn InstallClock>) -> Self {
        Self {
            clock,
            hook: Arc::new(NoopInstallVerifyHook),
        }
    }

    pub fn with_ports(clock: Arc<dyn InstallClock>, hook: Arc<dyn InstallVerifyHook>) -> Self {
        Self { clock, hook }
    }

    pub fn verify(
        &self,
        validated: &ValidatedPlan,
        workspace: &InstallWorkspace,
        target_roots: &InstallTargetRoots,
        apply_report: &InstallApplyReport,
    ) -> Result<InstallVerifyReport, InstallWriterError> {
        validate_apply_inputs(validated, workspace, target_roots)?;
        if apply_report.validated_fingerprint != validated.semantic_fingerprint() {
            return Err(InstallWriterError::new("apply_report_mismatch"));
        }
        let apply_outcomes = apply_report
            .destinations
            .iter()
            .map(|outcome| {
                (
                    (outcome.target.as_str(), outcome.destination.as_str()),
                    outcome,
                )
            })
            .collect::<BTreeMap<_, _>>();
        let started = self.clock.now_millis();
        let (opened_roots, root_errors) = open_target_roots(target_roots);
        let current_root_identities = opened_roots
            .iter()
            .map(|(target, root)| (target.clone(), root.identity))
            .collect::<BTreeMap<_, _>>();
        if apply_report.target_root_identities != current_root_identities {
            return Err(InstallWriterError::new("apply_report_root_mismatch"));
        }
        let mut destinations = Vec::with_capacity(validated.artifact_count());
        let mut deadline_reached = false;

        for artifact in validated.artifacts() {
            if deadline_reached
                || elapsed(self.clock.now_millis(), started) >= VERIFIER_DEADLINE_MILLIS
            {
                deadline_reached = true;
                destinations.push(verify_failure(
                    artifact,
                    DestinationVerifyState::NotAttempted,
                    "verifier_deadline_exceeded",
                ));
                continue;
            }
            let Some(applied) = apply_outcomes
                .get(&(artifact.target.as_str(), artifact.destination.as_str()))
                .copied()
            else {
                destinations.push(verify_failure(
                    artifact,
                    DestinationVerifyState::NotApplied,
                    "destination_not_applied",
                ));
                continue;
            };
            if !matches!(
                applied.state,
                DestinationApplyState::Applied
                    | DestinationApplyState::Unchanged
                    | DestinationApplyState::AppliedWithWarning
            ) {
                destinations.push(verify_failure(
                    artifact,
                    DestinationVerifyState::NotApplied,
                    "destination_not_applied",
                ));
                continue;
            }
            let outcome = match opened_roots.get(&artifact.target) {
                Some(root) => {
                    self.verify_one(validated, workspace, root, artifact, applied, started)
                }
                None => Err(root_errors
                    .get(&artifact.target)
                    .copied()
                    .unwrap_or("destination_root_unavailable")),
            };
            destinations.push(match outcome {
                Ok((content_sha256, correlation_keys)) => VerifyDestinationOutcome {
                    target: artifact.target.clone(),
                    destination: artifact.destination.clone(),
                    state: DestinationVerifyState::Verified,
                    code: None,
                    content_sha256: Some(content_sha256),
                    correlation_keys,
                },
                Err(code) => verify_failure(artifact, DestinationVerifyState::Failed, code),
            });
        }
        Ok(InstallVerifyReport {
            status: summarize_verify(&destinations),
            destinations,
        })
    }

    fn verify_one(
        &self,
        validated: &ValidatedPlan,
        workspace: &InstallWorkspace,
        target_root: &OpenedTargetRoot,
        artifact: &PlanArtifact,
        applied: &InstallDestinationOutcome,
        started: u64,
    ) -> Result<(String, Vec<DestinationKey>), &'static str> {
        let source = read_source(workspace.root_fd(), artifact)?;
        let opened = open_verified_destination(validated, target_root, artifact)?;
        let current =
            read_destination(opened.parent.as_fd(), &opened.leaf)?.ok_or("destination_missing")?;
        self.ensure_before_deadline(started)?;
        let content_sha256 = sha256(&current.bytes);
        if applied.content_sha256.as_deref() != Some(content_sha256.as_str())
            || applied.mode != Some(current.mode)
        {
            return Err("verification_hash_mismatch");
        }
        let fixed_point = render_destination(artifact, Some(&current), &source.bytes)?;
        self.ensure_before_deadline(started)?;
        if fixed_point != current.bytes {
            return Err("verification_fixed_point_mismatch");
        }
        self.hook
            .before_correlation_keys(&artifact.target, &artifact.destination);
        let correlation_keys = verified_correlation_keys(target_root, &opened.correlation_roots)?;
        Ok((content_sha256, correlation_keys))
    }

    fn ensure_before_deadline(&self, started: u64) -> Result<(), &'static str> {
        (elapsed(self.clock.now_millis(), started) < VERIFIER_DEADLINE_MILLIS)
            .then_some(())
            .ok_or("verifier_deadline_exceeded")
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallWriterError {
    code: &'static str,
}

impl InstallWriterError {
    fn new(code: &'static str) -> Self {
        Self { code }
    }

    pub fn code(&self) -> &'static str {
        self.code
    }
}

impl fmt::Display for InstallWriterError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code)
    }
}

impl std::error::Error for InstallWriterError {}

struct AppliedDestination {
    state: DestinationApplyState,
    code: Option<&'static str>,
    content_sha256: String,
    mode: u32,
}

struct OpenedTargetRoot {
    fd: OwnedFd,
    identity: (u64, u64),
    canonical_path: PathBuf,
}

struct OpenedVerifiedDestination {
    parent: OwnedFd,
    leaf: String,
    correlation_roots: Vec<OpenedCorrelationRoot>,
}

struct OpenedCorrelationRoot {
    tool_id: String,
    surface_id: String,
    scope: DestinationScope,
    authorized_root_prefix: Option<String>,
    fd: OwnedFd,
    identity: (u64, u64),
    canonical_path: PathBuf,
    normalized_relative_locator: String,
}

struct CorrelationRoute {
    tool_id: String,
    surface_id: String,
    scope: DestinationScope,
    authorized_root_prefix: Option<String>,
    prefix_component_count: usize,
    canonical_path: PathBuf,
    normalized_relative_locator: String,
}

struct SecureFile {
    bytes: Vec<u8>,
    identity: FileIdentity,
    mode: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FileIdentity {
    device: u64,
    inode: u64,
    file_type: FileType,
    mode: u32,
    size: u64,
    modified_seconds: i64,
    modified_nanoseconds: i64,
    changed_seconds: i64,
    changed_nanoseconds: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct EntryIdentity {
    device: u64,
    inode: u64,
    file_type: FileType,
}

fn entry_identity(stat: &Stat) -> EntryIdentity {
    EntryIdentity {
        device: stat.st_dev as u64,
        inode: stat.st_ino,
        file_type: FileType::from_raw_mode(stat.st_mode),
    }
}

fn file_entry_identity(identity: FileIdentity) -> EntryIdentity {
    EntryIdentity {
        device: identity.device,
        inode: identity.inode,
        file_type: identity.file_type,
    }
}

fn same_file_after_rename(stat: &Stat, identity: FileIdentity) -> bool {
    let current = file_identity(stat);
    current.device == identity.device
        && current.inode == identity.inode
        && current.file_type == identity.file_type
        && current.mode == identity.mode
        && current.size == identity.size
        && current.modified_seconds == identity.modified_seconds
        && current.modified_nanoseconds == identity.modified_nanoseconds
}

struct TempSiblingGuard {
    parent: OwnedFd,
    name: String,
    file: Option<OwnedFd>,
    entry_identity: EntryIdentity,
    content_identity: FileIdentity,
    committed: bool,
}

impl TempSiblingGuard {
    fn create(parent: BorrowedFd<'_>) -> Result<Self, &'static str> {
        let parent = rustix::io::dup(parent).map_err(|_| "destination_write_failed")?;
        for _ in 0..128 {
            let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
            let name = temporary_name(parent.as_fd(), sequence)?;
            match openat(
                parent.as_fd(),
                name.as_str(),
                OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
                Mode::from(0o600),
            ) {
                Ok(file) => {
                    let stat = fstat(file.as_fd()).map_err(|_| "destination_write_failed")?;
                    return Ok(Self {
                        parent,
                        name,
                        file: Some(file),
                        entry_identity: entry_identity(&stat),
                        content_identity: file_identity(&stat),
                        committed: false,
                    });
                }
                Err(Errno::EXIST) => continue,
                Err(_) => return Err("destination_write_failed"),
            }
        }
        Err("destination_write_failed")
    }

    fn write(&mut self, bytes: &[u8], mode: u32) -> Result<(), &'static str> {
        let file = self.file.as_ref().ok_or("destination_write_failed")?;
        write_all(file.as_fd(), bytes)?;
        fchmod(file.as_fd(), Mode::from(mode as u16)).map_err(|_| "destination_write_failed")?;
        fsync(file.as_fd()).map_err(|_| "destination_write_failed")?;
        self.content_identity =
            file_identity(&fstat(file.as_fd()).map_err(|_| "destination_write_failed")?);
        Ok(())
    }

    fn entry_is_original(&self) -> bool {
        statat(
            self.parent.as_fd(),
            self.name.as_str(),
            AtFlags::SYMLINK_NOFOLLOW,
        )
        .is_ok_and(|stat| entry_identity(&stat) == self.entry_identity)
    }
}

impl Drop for TempSiblingGuard {
    fn drop(&mut self) {
        if !self.committed && self.entry_is_original() {
            let _ = unlinkat(self.parent.as_fd(), self.name.as_str(), AtFlags::empty());
        }
        self.file.take();
    }
}

struct NamedEntryGuard {
    parent: OwnedFd,
    name: String,
    file: Option<OwnedFd>,
    identity: FileIdentity,
    committed: bool,
}

impl NamedEntryGuard {
    fn create(parent: BorrowedFd<'_>, name: &str) -> Result<Self, &'static str> {
        let parent = rustix::io::dup(parent).map_err(|_| "destination_write_failed")?;
        let file = openat(
            parent.as_fd(),
            name,
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::from(0o600),
        )
        .map_err(|_| "destination_changed")?;
        let identity = file_identity(&fstat(file.as_fd()).map_err(|_| "destination_write_failed")?);
        Ok(Self {
            parent,
            name: name.to_string(),
            file: Some(file),
            identity,
            committed: false,
        })
    }

    fn entry_is_original(&self) -> bool {
        statat(
            self.parent.as_fd(),
            self.name.as_str(),
            AtFlags::SYMLINK_NOFOLLOW,
        )
        .is_ok_and(|stat| entry_identity(&stat) == file_entry_identity(self.identity))
    }
}

impl Drop for NamedEntryGuard {
    fn drop(&mut self) {
        if !self.committed && self.entry_is_original() {
            let _ = unlinkat(self.parent.as_fd(), self.name.as_str(), AtFlags::empty());
        }
        self.file.take();
    }
}

fn atomic_replace(
    parent: BorrowedFd<'_>,
    leaf: &str,
    existing: Option<&SecureFile>,
    temp: &mut TempSiblingGuard,
) -> Result<Option<&'static str>, &'static str> {
    if !temp.entry_is_original()
        || !statat(parent, temp.name.as_str(), AtFlags::SYMLINK_NOFOLLOW)
            .is_ok_and(|stat| file_identity(&stat) == temp.content_identity)
    {
        return Err("temporary_file_changed");
    }
    let mut placeholder = if existing.is_none() {
        Some(NamedEntryGuard::create(parent, leaf)?)
    } else {
        None
    };
    let displaced_identity = existing
        .map(|current| current.identity)
        .or_else(|| placeholder.as_ref().map(|entry| entry.identity))
        .ok_or("destination_atomicity_failed")?;

    renameat_with(
        parent,
        temp.name.as_str(),
        parent,
        leaf,
        RenameFlags::EXCHANGE,
    )
    .map_err(|_| "destination_atomic_replace_unavailable")?;

    let destination_after = statat(parent, leaf, AtFlags::SYMLINK_NOFOLLOW);
    let displaced_after = statat(parent, temp.name.as_str(), AtFlags::SYMLINK_NOFOLLOW);
    let exchange_matches = destination_after
        .is_ok_and(|stat| same_file_after_rename(&stat, temp.content_identity))
        && displaced_after.is_ok_and(|stat| same_file_after_rename(&stat, displaced_identity));
    if !exchange_matches {
        let rollback_entries_match = statat(parent, leaf, AtFlags::SYMLINK_NOFOLLOW)
            .is_ok_and(|stat| entry_identity(&stat) == file_entry_identity(temp.content_identity))
            && statat(parent, temp.name.as_str(), AtFlags::SYMLINK_NOFOLLOW)
                .is_ok_and(|stat| entry_identity(&stat) == file_entry_identity(displaced_identity));
        if !rollback_entries_match {
            return Err("destination_atomicity_failed");
        }
        return match renameat_with(
            parent,
            temp.name.as_str(),
            parent,
            leaf,
            RenameFlags::EXCHANGE,
        ) {
            Ok(())
                if statat(parent, leaf, AtFlags::SYMLINK_NOFOLLOW).is_ok_and(|stat| {
                    entry_identity(&stat) == file_entry_identity(displaced_identity)
                }) && statat(parent, temp.name.as_str(), AtFlags::SYMLINK_NOFOLLOW)
                    .is_ok_and(|stat| {
                        entry_identity(&stat) == file_entry_identity(temp.content_identity)
                    }) =>
            {
                Err("destination_changed")
            }
            Ok(()) => Err("destination_atomicity_failed"),
            Err(_) => Err("destination_atomicity_failed"),
        };
    }

    temp.committed = true;
    if let Some(placeholder) = placeholder.as_mut() {
        placeholder.committed = true;
    }
    let cleanup_matches = statat(parent, temp.name.as_str(), AtFlags::SYMLINK_NOFOLLOW)
        .is_ok_and(|stat| same_file_after_rename(&stat, displaced_identity));
    if !cleanup_matches || unlinkat(parent, temp.name.as_str(), AtFlags::empty()).is_err() {
        return Ok(Some("destination_cleanup_failed"));
    }
    Ok(None)
}

fn temporary_name(parent: BorrowedFd<'_>, sequence: u64) -> Result<String, &'static str> {
    let parent = fstat(parent).map_err(|_| "destination_write_failed")?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "destination_write_failed")?
        .as_nanos();
    let mut digest = Sha256::new();
    digest.update(std::process::id().to_be_bytes());
    digest.update(sequence.to_be_bytes());
    digest.update((parent.st_dev as u64).to_be_bytes());
    digest.update(parent.st_ino.to_be_bytes());
    digest.update(now.to_be_bytes());
    let token = format!("{:x}", digest.finalize());
    Ok(format!(".harnesskit-install-{}.tmp", &token[..24]))
}

fn validate_apply_inputs(
    validated: &ValidatedPlan,
    workspace: &InstallWorkspace,
    target_roots: &InstallTargetRoots,
) -> Result<(), InstallWriterError> {
    if validated.mode() != "apply" {
        return Err(InstallWriterError::new("validated_plan_not_apply"));
    }
    if validated.contract_hash().is_empty()
        || validated.request_scope().is_empty()
        || target_roots.target_ids() != *validated.request_targets()
    {
        return Err(InstallWriterError::new("target_root_set_mismatch"));
    }
    workspace
        .validate_root_identity()
        .map_err(|_| InstallWriterError::new("install_workspace_unavailable"))
}

fn validate_artifact_authority(
    validated: &ValidatedPlan,
    workspace: &InstallWorkspace,
    authority: &InstallArtifactAuthority,
) -> Result<(), InstallWriterError> {
    if validated.mode() != "apply" {
        return Err(InstallWriterError::new("validated_plan_not_apply"));
    }
    workspace
        .validate_root_identity()
        .map_err(|_| InstallWriterError::new("install_workspace_unavailable"))?;

    let mut matched_records = BTreeSet::new();
    for artifact in validated.artifacts() {
        let expected_components = if artifact.component_ids.is_empty() {
            BTreeSet::from([artifact.component_id.as_str()])
        } else {
            artifact
                .component_ids
                .iter()
                .map(String::as_str)
                .collect::<BTreeSet<_>>()
        };
        let expected_scope = artifact
            .scope
            .as_deref()
            .unwrap_or(validated.request_scope());
        let expected_config_locator = artifact
            .json_merge_key
            .as_deref()
            .or(artifact.toml_merge_key.as_deref());
        let matching = authority
            .records
            .iter()
            .enumerate()
            .filter(|(_, record)| {
                record.target == artifact.target
                    && record.scope == expected_scope
                    && record.destination == artifact.destination
                    && record.source == artifact.source
                    && record.merge_strategy == artifact.merge_strategy
                    && record.config_entry_locator.as_deref() == expected_config_locator
            })
            .collect::<Vec<_>>();
        let actual_components = matching
            .iter()
            .map(|(_, record)| record.component_id.as_str())
            .collect::<BTreeSet<_>>();
        if matching.is_empty()
            || matching.len() != actual_components.len()
            || actual_components != expected_components
        {
            return Err(InstallWriterError::new("artifact_authority_mismatch"));
        }

        let source = read_source(workspace.root_fd(), artifact)
            .map_err(|_| InstallWriterError::new("artifact_authority_mismatch"))?;
        for (index, record) in matching {
            if artifact.source_sha256.as_deref() != Some(record.content_sha256.as_str())
                || sha256(&record.exact_bytes) != record.content_sha256
                || source.bytes != record.exact_bytes
            {
                return Err(InstallWriterError::new("artifact_authority_mismatch"));
            }
            matched_records.insert(index);
        }
    }

    let has_unmatched_relevant_record =
        authority.records.iter().enumerate().any(|(index, record)| {
            record.scope == validated.request_scope()
                && validated.request_targets().contains(&record.target)
                && !matched_records.contains(&index)
        });
    if has_unmatched_relevant_record {
        return Err(InstallWriterError::new("artifact_authority_mismatch"));
    }
    Ok(())
}

fn open_target_roots(
    target_roots: &InstallTargetRoots,
) -> (
    BTreeMap<String, OpenedTargetRoot>,
    BTreeMap<String, &'static str>,
) {
    let mut opened = BTreeMap::new();
    let mut errors = BTreeMap::new();
    for (target, path) in &target_roots.roots {
        match open_absolute_directory(path) {
            Ok(root)
                if target_roots.expected_identities.get(target).copied() == Some(root.identity) =>
            {
                opened.insert(target.clone(), root);
            }
            Ok(_) => {
                errors.insert(target.clone(), "target_root_identity_changed");
            }
            Err(code) => {
                errors.insert(target.clone(), code);
            }
        }
    }
    (opened, errors)
}

fn open_absolute_directory(path: &Path) -> Result<OpenedTargetRoot, &'static str> {
    if !canonical_absolute_path(path) {
        return Err("destination_root_unavailable");
    }
    let mut current = open(
        "/",
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| "destination_root_unavailable")?;
    for component in path.components() {
        let Component::Normal(name) = component else {
            continue;
        };
        current = open_verified_directory(current.as_fd(), name, false)?;
    }
    let stat = fstat(current.as_fd()).map_err(|_| "destination_root_unavailable")?;
    Ok(OpenedTargetRoot {
        fd: current,
        identity: (stat.st_dev as u64, stat.st_ino),
        canonical_path: path.to_path_buf(),
    })
}

fn canonical_absolute_path(path: &Path) -> bool {
    path.is_absolute()
        && path
            .components()
            .all(|component| matches!(component, Component::RootDir | Component::Normal(_)))
}

fn read_source(
    workspace_root: BorrowedFd<'_>,
    artifact: &PlanArtifact,
) -> Result<SecureFile, &'static str> {
    let source = read_relative_regular(workspace_root, &artifact.source, "source_unavailable")?;
    let expected_hash = artifact
        .source_sha256
        .as_deref()
        .ok_or("source_hash_missing")?;
    let expected_mode = artifact.mode.ok_or("source_mode_missing")?;
    if sha256(&source.bytes) != expected_hash || source.mode != expected_mode {
        return Err("source_changed");
    }
    Ok(source)
}

fn read_relative_regular(
    root: BorrowedFd<'_>,
    relative: &str,
    unavailable: &'static str,
) -> Result<SecureFile, &'static str> {
    let (parent, leaf) = open_relative_parent(root, relative, false, unavailable)?;
    read_regular(parent.as_fd(), &leaf, unavailable)
}

fn read_destination(
    parent: BorrowedFd<'_>,
    leaf: &str,
) -> Result<Option<SecureFile>, &'static str> {
    match statat(parent, leaf, AtFlags::SYMLINK_NOFOLLOW) {
        Err(Errno::NOENT) => Ok(None),
        Err(_) => Err("destination_unsafe"),
        Ok(stat) if FileType::from_raw_mode(stat.st_mode) != FileType::RegularFile => {
            Err("destination_unsafe")
        }
        Ok(_) => read_regular(parent, leaf, "destination_read_failed").map(Some),
    }
}

fn read_regular(
    parent: BorrowedFd<'_>,
    leaf: &str,
    unavailable: &'static str,
) -> Result<SecureFile, &'static str> {
    let before = statat(parent, leaf, AtFlags::SYMLINK_NOFOLLOW).map_err(|_| unavailable)?;
    if FileType::from_raw_mode(before.st_mode) != FileType::RegularFile {
        return Err(unavailable);
    }
    let file = openat(
        parent,
        leaf,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| unavailable)?;
    let opened = fstat(file.as_fd()).map_err(|_| unavailable)?;
    if file_identity(&before) != file_identity(&opened) {
        return Err(unavailable);
    }
    let bytes = read_bounded(file.as_fd(), opened.st_size, unavailable)?;
    let after = fstat(file.as_fd()).map_err(|_| unavailable)?;
    let current = statat(parent, leaf, AtFlags::SYMLINK_NOFOLLOW).map_err(|_| unavailable)?;
    if file_identity(&opened) != file_identity(&after)
        || file_identity(&after) != file_identity(&current)
        || u64::try_from(after.st_size).ok() != Some(bytes.len() as u64)
    {
        return Err(unavailable);
    }
    Ok(SecureFile {
        bytes,
        identity: file_identity(&after),
        mode: after.st_mode as u32 & 0o777,
    })
}

fn read_bounded(
    file: BorrowedFd<'_>,
    declared_size: i64,
    unavailable: &'static str,
) -> Result<Vec<u8>, &'static str> {
    let size = u64::try_from(declared_size).map_err(|_| unavailable)?;
    if size > MAX_INSTALL_FILE_BYTES {
        return Err(unavailable);
    }
    let mut bytes = Vec::with_capacity(size as usize);
    let mut scratch = [0_u8; 16 * 1024];
    let mut offset = 0_u64;
    while offset < size {
        let wanted = (size - offset).min(scratch.len() as u64) as usize;
        let read = loop {
            match pread(file, &mut scratch[..wanted], offset) {
                Err(Errno::INTR) => continue,
                Err(_) => return Err(unavailable),
                Ok(read) => break read,
            }
        };
        if read == 0 {
            return Err(unavailable);
        }
        bytes.extend_from_slice(&scratch[..read]);
        offset += read as u64;
    }
    Ok(bytes)
}

fn open_destination_parent(
    root: &OwnedFd,
    destination: &str,
    create: bool,
) -> Result<(OwnedFd, String), &'static str> {
    open_relative_parent(root.as_fd(), destination, create, "destination_unsafe")
}

fn open_verified_destination(
    validated: &ValidatedPlan,
    target_root: &OpenedTargetRoot,
    artifact: &PlanArtifact,
) -> Result<OpenedVerifiedDestination, &'static str> {
    let destination_parts = relative_parts(&artifact.destination).ok_or("destination_unsafe")?;
    let leaf = destination_parts
        .last()
        .ok_or("destination_unsafe")?
        .to_string();
    let contract = embedded_target_contract().map_err(|_| "correlation_contract_unavailable")?;
    let mut routes = Vec::new();
    for binding in contract.correlation_bindings(
        &artifact.target,
        validated.request_scope(),
        &artifact.destination,
    ) {
        let prefix_parts = match binding.authorized_root_prefix.as_deref() {
            Some(prefix) => relative_parts(prefix).ok_or("correlation_contract_unavailable")?,
            None => Vec::new(),
        };
        if prefix_parts.len() >= destination_parts.len()
            || !destination_parts.starts_with(prefix_parts.as_slice())
        {
            return Err("correlation_contract_unavailable");
        }
        let locator_path = destination_parts[prefix_parts.len()..].iter().fold(
            PathBuf::new(),
            |mut path, component| {
                path.push(*component);
                path
            },
        );
        let normalized_relative_locator =
            normalized_relative_locator(&locator_path).ok_or("correlation_contract_unavailable")?;
        let scope = match binding.scope.as_str() {
            "user" => DestinationScope::User,
            "project" => DestinationScope::Project,
            _ => return Err("correlation_contract_unavailable"),
        };
        let canonical_path = binding
            .authorized_root_prefix
            .as_deref()
            .map(|prefix| target_root.canonical_path.join(prefix))
            .unwrap_or_else(|| target_root.canonical_path.clone());
        routes.push(CorrelationRoute {
            tool_id: binding.tool_id.clone(),
            surface_id: binding.surface_id.clone(),
            scope,
            authorized_root_prefix: binding.authorized_root_prefix.clone(),
            prefix_component_count: prefix_parts.len(),
            canonical_path,
            normalized_relative_locator,
        });
    }

    let mut current = rustix::io::dup(target_root.fd.as_fd()).map_err(|_| "destination_unsafe")?;
    let mut correlation_roots = Vec::with_capacity(routes.len());
    capture_correlation_roots(&routes, 0, current.as_fd(), &mut correlation_roots)?;
    for (index, component) in destination_parts[..destination_parts.len() - 1]
        .iter()
        .enumerate()
    {
        current = open_verified_directory(current.as_fd(), OsStr::new(component), false)
            .map_err(|_| "destination_unsafe")?;
        capture_correlation_roots(&routes, index + 1, current.as_fd(), &mut correlation_roots)?;
    }
    if correlation_roots.len() != routes.len() {
        return Err("correlation_contract_unavailable");
    }
    correlation_roots.sort_by(|left, right| {
        left.tool_id
            .cmp(&right.tool_id)
            .then_with(|| left.surface_id.cmp(&right.surface_id))
            .then_with(|| {
                left.authorized_root_prefix
                    .cmp(&right.authorized_root_prefix)
            })
    });
    Ok(OpenedVerifiedDestination {
        parent: current,
        leaf,
        correlation_roots,
    })
}

fn capture_correlation_roots(
    routes: &[CorrelationRoute],
    opened_component_count: usize,
    directory: BorrowedFd<'_>,
    captured: &mut Vec<OpenedCorrelationRoot>,
) -> Result<(), &'static str> {
    for route in routes
        .iter()
        .filter(|route| route.prefix_component_count == opened_component_count)
    {
        let fd = rustix::io::dup(directory).map_err(|_| "correlation_root_unsafe")?;
        let stat = fstat(fd.as_fd()).map_err(|_| "correlation_root_unsafe")?;
        if FileType::from_raw_mode(stat.st_mode) != FileType::Directory {
            return Err("correlation_root_unsafe");
        }
        captured.push(OpenedCorrelationRoot {
            tool_id: route.tool_id.clone(),
            surface_id: route.surface_id.clone(),
            scope: route.scope,
            authorized_root_prefix: route.authorized_root_prefix.clone(),
            fd,
            identity: (stat.st_dev as u64, stat.st_ino),
            canonical_path: route.canonical_path.clone(),
            normalized_relative_locator: route.normalized_relative_locator.clone(),
        });
    }
    Ok(())
}

fn verified_correlation_keys(
    target_root: &OpenedTargetRoot,
    roots: &[OpenedCorrelationRoot],
) -> Result<Vec<DestinationKey>, &'static str> {
    let mut keys = Vec::with_capacity(roots.len());
    for root in roots {
        let held = fstat(root.fd.as_fd()).map_err(|_| "correlation_root_unsafe")?;
        let current = match root.authorized_root_prefix.as_deref() {
            Some(prefix) => open_relative_directory(target_root.fd.as_fd(), prefix)?,
            None => {
                rustix::io::dup(target_root.fd.as_fd()).map_err(|_| "correlation_root_unsafe")?
            }
        };
        let current = fstat(current.as_fd()).map_err(|_| "correlation_root_unsafe")?;
        let held_identity = (held.st_dev as u64, held.st_ino);
        let current_identity = (current.st_dev as u64, current.st_ino);
        if FileType::from_raw_mode(held.st_mode) != FileType::Directory
            || FileType::from_raw_mode(current.st_mode) != FileType::Directory
            || held_identity != root.identity
            || current_identity != root.identity
        {
            return Err("correlation_root_unsafe");
        }
        keys.push(DestinationKey {
            tool_id: root.tool_id.clone(),
            surface_id: root.surface_id.clone(),
            scope: root.scope,
            root_identity: root_identity(root.identity.0, root.identity.1, &root.canonical_path),
            normalized_relative_locator: root.normalized_relative_locator.clone(),
            config_entry_locator: None,
        });
    }
    keys.sort();
    keys.dedup();
    Ok(keys)
}

fn open_relative_directory(root: BorrowedFd<'_>, relative: &str) -> Result<OwnedFd, &'static str> {
    let parts = relative_parts(relative).ok_or("correlation_root_unsafe")?;
    let mut current = rustix::io::dup(root).map_err(|_| "correlation_root_unsafe")?;
    for component in parts {
        current = open_verified_directory(current.as_fd(), OsStr::new(component), false)
            .map_err(|_| "correlation_root_unsafe")?;
    }
    Ok(current)
}

fn open_relative_parent(
    root: BorrowedFd<'_>,
    relative: &str,
    create: bool,
    unavailable: &'static str,
) -> Result<(OwnedFd, String), &'static str> {
    let parts = relative_parts(relative).ok_or(unavailable)?;
    let leaf = parts.last().ok_or(unavailable)?.to_string();
    let mut current = rustix::io::dup(root).map_err(|_| unavailable)?;
    for component in &parts[..parts.len() - 1] {
        current = open_verified_directory(current.as_fd(), OsStr::new(component), create)
            .map_err(|_| unavailable)?;
    }
    Ok((current, leaf))
}

fn open_verified_directory(
    parent: BorrowedFd<'_>,
    name: &OsStr,
    create: bool,
) -> Result<OwnedFd, &'static str> {
    let before = match statat(parent, name, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(stat) => stat,
        Err(Errno::NOENT) if create => {
            match mkdirat(parent, name, Mode::from(0o755)) {
                Ok(()) | Err(Errno::EXIST) => {}
                Err(_) => return Err("destination_unsafe"),
            }
            statat(parent, name, AtFlags::SYMLINK_NOFOLLOW).map_err(|_| "destination_unsafe")?
        }
        Err(_) => return Err("destination_unsafe"),
    };
    if FileType::from_raw_mode(before.st_mode) != FileType::Directory {
        return Err("destination_unsafe");
    }
    let directory = openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| "destination_unsafe")?;
    let opened = fstat(directory.as_fd()).map_err(|_| "destination_unsafe")?;
    let after =
        statat(parent, name, AtFlags::SYMLINK_NOFOLLOW).map_err(|_| "destination_unsafe")?;
    if entry_identity(&before) != entry_identity(&opened)
        || entry_identity(&opened) != entry_identity(&after)
    {
        return Err("destination_unsafe");
    }
    Ok(directory)
}

fn relative_parts(relative: &str) -> Option<Vec<&str>> {
    let parts = relative.split('/').collect::<Vec<_>>();
    (!relative.is_empty()
        && !relative.starts_with('/')
        && !relative.contains('\0')
        && parts
            .iter()
            .all(|part| !part.is_empty() && *part != "." && *part != ".."))
    .then_some(parts)
}

fn render_destination(
    artifact: &PlanArtifact,
    existing: Option<&SecureFile>,
    source: &[u8],
) -> Result<Vec<u8>, &'static str> {
    let current = existing
        .map(|file| file.bytes.as_slice())
        .unwrap_or_default();
    match artifact.merge_strategy.as_deref() {
        None => Ok(exact_copy(source)),
        Some("managed-block") => merge_managed_block(
            std::str::from_utf8(current).map_err(|_| "destination_invalid_utf8")?,
            std::str::from_utf8(source).map_err(|_| "source_invalid_utf8")?,
            artifact
                .begin_marker
                .as_deref()
                .ok_or("merge_contract_mismatch")?,
            artifact
                .end_marker
                .as_deref()
                .ok_or("merge_contract_mismatch")?,
        )
        .map(String::into_bytes)
        .map_err(|_| "merge_failed"),
        Some("json-deep-merge") => merge_json_deep(
            std::str::from_utf8(current).map_err(|_| "destination_invalid_utf8")?,
            std::str::from_utf8(source).map_err(|_| "source_invalid_utf8")?,
            artifact
                .json_merge_key
                .as_deref()
                .ok_or("merge_contract_mismatch")?,
        )
        .map(String::into_bytes)
        .map_err(|_| "merge_failed"),
        Some("toml-agents-merge") => merge_toml_agents(
            std::str::from_utf8(current).map_err(|_| "destination_invalid_utf8")?,
            std::str::from_utf8(source).map_err(|_| "source_invalid_utf8")?,
            artifact
                .toml_merge_key
                .as_deref()
                .ok_or("merge_contract_mismatch")?,
        )
        .map(String::into_bytes)
        .map_err(|_| "merge_failed"),
        Some(_) => Err("merge_contract_mismatch"),
    }
}

fn destination_unchanged(
    parent: BorrowedFd<'_>,
    leaf: &str,
    existing: Option<&SecureFile>,
) -> Result<bool, &'static str> {
    match (existing, statat(parent, leaf, AtFlags::SYMLINK_NOFOLLOW)) {
        (None, Err(Errno::NOENT)) => Ok(true),
        (None, _) => Ok(false),
        (Some(expected), Ok(current)) => Ok(expected.identity == file_identity(&current)),
        (Some(_), Err(_)) => Ok(false),
    }
}

fn write_all(file: BorrowedFd<'_>, bytes: &[u8]) -> Result<(), &'static str> {
    let mut offset = 0_u64;
    while offset < bytes.len() as u64 {
        let written = loop {
            match rustix::io::pwrite(file, &bytes[offset as usize..], offset) {
                Err(Errno::INTR) => continue,
                Err(_) => return Err("destination_write_failed"),
                Ok(written) => break written,
            }
        };
        if written == 0 {
            return Err("destination_write_failed");
        }
        offset += written as u64;
    }
    Ok(())
}

fn file_identity(stat: &Stat) -> FileIdentity {
    FileIdentity {
        device: stat.st_dev as u64,
        inode: stat.st_ino,
        file_type: FileType::from_raw_mode(stat.st_mode),
        mode: stat.st_mode as u32 & 0o777,
        size: u64::try_from(stat.st_size).unwrap_or(u64::MAX),
        modified_seconds: stat.st_mtime,
        modified_nanoseconds: stat.st_mtime_nsec,
        changed_seconds: stat.st_ctime,
        changed_nanoseconds: stat.st_ctime_nsec,
    }
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn elapsed(now: u64, start: u64) -> u64 {
    now.saturating_sub(start)
}

fn apply_failure(
    artifact: &PlanArtifact,
    state: DestinationApplyState,
    code: &str,
) -> InstallDestinationOutcome {
    InstallDestinationOutcome {
        target: artifact.target.clone(),
        destination: artifact.destination.clone(),
        state,
        code: Some(code.to_string()),
        content_sha256: None,
        mode: None,
    }
}

fn verify_failure(
    artifact: &PlanArtifact,
    state: DestinationVerifyState,
    code: &str,
) -> VerifyDestinationOutcome {
    VerifyDestinationOutcome {
        target: artifact.target.clone(),
        destination: artifact.destination.clone(),
        state,
        code: Some(code.to_string()),
        content_sha256: None,
        correlation_keys: Vec::new(),
    }
}

fn summarize_apply(destinations: &[InstallDestinationOutcome]) -> InstallOperationStatus {
    let applied = destinations
        .iter()
        .filter(|outcome| {
            matches!(
                outcome.state,
                DestinationApplyState::Applied
                    | DestinationApplyState::Unchanged
                    | DestinationApplyState::AppliedWithWarning
            )
        })
        .count();
    let clean = destinations.iter().all(|outcome| {
        matches!(
            outcome.state,
            DestinationApplyState::Applied | DestinationApplyState::Unchanged
        )
    });
    if clean {
        InstallOperationStatus::Complete
    } else if applied == 0 {
        InstallOperationStatus::Failed
    } else {
        InstallOperationStatus::Partial
    }
}

fn summarize_verify(destinations: &[VerifyDestinationOutcome]) -> InstallOperationStatus {
    let verified = destinations
        .iter()
        .filter(|outcome| outcome.state == DestinationVerifyState::Verified)
        .count();
    if verified == destinations.len() {
        InstallOperationStatus::Complete
    } else if verified == 0 {
        InstallOperationStatus::Failed
    } else {
        InstallOperationStatus::Partial
    }
}
