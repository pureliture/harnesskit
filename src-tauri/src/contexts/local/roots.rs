//! Adapter-owned authorized root registry.

use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};
use std::os::fd::{FromRawFd, IntoRawFd};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::process::ExitStatusExt;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use rustix::fd::{AsFd, BorrowedFd, OwnedFd};
use rustix::ffi::{CStr, CString};
use rustix::fs::{fstat, open, openat, statat, AtFlags, Dir, FileType, Mode, OFlags, Stat};
use rustix::io::{pread, Errno};
use rustix::process::{getppid, Signal};
use serde::{Deserialize, Serialize};
use serde_yaml::Value as YamlValue;
use sha2::{Digest, Sha256};

use crate::projection::root_identity as destination_root_identity;

use super::domain::Scope;
use super::project_ignore::{ProjectIgnoreSource, ProjectScanScope};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RootEnumeratorId {
    CodexHomeMarkersV1,
    ClaudeHomeMarkersV2,
    AntigravityHomeMarkersV2,
    AntigravityCliHomeMarkersV2,
    HermesExternalDirsV1,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RootSpec {
    pub adapter_id: String,
    pub adapter_version: String,
    pub tool_id: String,
    pub surface_id: String,
    pub scope: Scope,
    pub enumerator_id: RootEnumeratorId,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorizedRoot {
    pub root_id: String,
    pub correlation_root_identity: String,
    pub adapter_id: String,
    pub adapter_version: String,
    pub tool_id: String,
    pub surface_id: String,
    pub scope: Scope,
    pub project_id: Option<String>,
    pub canonical_root: PathBuf,
    pub root_device: u64,
    pub root_inode: u64,
    pub root_owner_uid: u32,
    pub project_scan_scopes: Vec<ProjectScanScope>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AuthorizedRootRegistry {
    pub roots: Vec<AuthorizedRoot>,
    pub issues: Vec<RootIssue>,
    pub coverage_issues: Vec<RootCoverageIssue>,
    pub pre_root_skips: Vec<PreRootSkippedPath>,
    pub(crate) project_ignored_paths: BTreeMap<String, BTreeSet<PathBuf>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RootIssue {
    pub project_id: Option<String>,
    pub code: String,
    pub safe_message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct PreRootSkippedPath {
    pub safe_relative_locator: String,
    pub reason_code: String,
    pub safe_message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct RootCoverageIssue {
    pub adapter_id: String,
    pub surface_id: String,
    pub scope: Scope,
    pub issue_codes: Vec<String>,
}

impl AuthorizedRootRegistry {
    pub fn discover(home: &Path, specs: &[RootSpec]) -> Result<Self, String> {
        let metadata = std::fs::symlink_metadata(home)
            .map_err(|_| "Local HOME root is unavailable".to_string())?;
        if metadata.file_type().is_symlink() {
            return Err("Local HOME root symlink is not authorized".to_string());
        }
        let canonical_home = std::fs::canonicalize(home)
            .map_err(|_| "Local HOME root is unavailable".to_string())?;
        if !canonical_home.is_dir() {
            return Err("Local HOME root is unavailable".to_string());
        }

        let mut roots = Vec::new();
        let mut issues = Vec::new();
        let mut coverage_issues = Vec::new();
        let mut seen = BTreeSet::new();
        let project_enumerators = specs
            .iter()
            .filter(|spec| {
                spec.scope == Scope::Project
                    && spec.enumerator_id != RootEnumeratorId::HermesExternalDirsV1
            })
            .map(|spec| spec.enumerator_id)
            .collect::<BTreeSet<_>>();
        let project_index =
            ProjectMarkerIndex::discover_once(&canonical_home, &project_enumerators)?;
        issues.extend(project_index.issues.iter().cloned());
        for spec in specs {
            let issue_start = issues.len();
            let mut indexed_issue_codes = BTreeSet::new();
            let candidates = match spec.scope {
                Scope::User => user_candidates(&canonical_home, spec.enumerator_id, &mut issues),
                Scope::Project if spec.enumerator_id == RootEnumeratorId::HermesExternalDirsV1 => {
                    hermes_external_candidates(&canonical_home, &mut issues)
                }
                Scope::Project => {
                    indexed_issue_codes.extend(
                        project_index
                            .issue_codes_for(spec.enumerator_id)
                            .iter()
                            .cloned(),
                    );
                    project_index.candidates_for(spec.enumerator_id)
                }
            };
            for candidate in candidates {
                let Some((canonical_root, root_device, root_inode, root_owner_uid)) =
                    authorize_candidate(&canonical_home, &candidate, &mut issues)
                else {
                    continue;
                };
                let project_id =
                    (spec.scope == Scope::Project).then(|| project_id(&canonical_root));
                let key = (
                    spec.tool_id.clone(),
                    spec.surface_id.clone(),
                    spec.scope,
                    project_id.clone(),
                    canonical_root.clone(),
                );
                if !seen.insert(key) {
                    continue;
                }
                roots.push(AuthorizedRoot {
                    root_id: root_id(spec, project_id.as_deref(), &canonical_root),
                    correlation_root_identity: destination_root_identity(
                        root_device,
                        root_inode,
                        &canonical_root,
                    ),
                    adapter_id: spec.adapter_id.clone(),
                    adapter_version: spec.adapter_version.clone(),
                    tool_id: spec.tool_id.clone(),
                    surface_id: spec.surface_id.clone(),
                    scope: spec.scope,
                    project_id,
                    canonical_root,
                    root_device,
                    root_inode,
                    root_owner_uid,
                    project_scan_scopes: candidate.project_scan_scopes.clone(),
                });
            }
            let issue_codes = indexed_issue_codes
                .into_iter()
                .chain(issues[issue_start..].iter().map(|issue| issue.code.clone()))
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<_>>();
            if !issue_codes.is_empty() {
                coverage_issues.push(RootCoverageIssue {
                    adapter_id: spec.adapter_id.clone(),
                    surface_id: spec.surface_id.clone(),
                    scope: spec.scope,
                    issue_codes,
                });
            }
        }
        roots.sort_by(|left, right| {
            left.tool_id
                .cmp(&right.tool_id)
                .then_with(|| left.surface_id.cmp(&right.surface_id))
                .then_with(|| {
                    path_bytes(&left.canonical_root).cmp(path_bytes(&right.canonical_root))
                })
                .then_with(|| left.project_id.cmp(&right.project_id))
        });
        issues.sort_by(|left, right| {
            left.code
                .cmp(&right.code)
                .then_with(|| left.safe_message.cmp(&right.safe_message))
        });
        issues.dedup();
        coverage_issues.sort();
        coverage_issues.dedup();
        Ok(Self {
            roots,
            issues,
            coverage_issues,
            pre_root_skips: project_index.pre_root_skips,
            project_ignored_paths: project_index.excluded_paths,
        })
    }
}

const MAX_HOME_DEPTH: usize = 12;
const MAX_VISITED_ENTRIES: usize = 500_000;

#[derive(Debug)]
struct RootCandidate {
    path: PathBuf,
    provenance: RootProvenance,
    expected_identity: Option<(u64, u64, u32)>,
    project_scan_scopes: Vec<ProjectScanScope>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RootProvenance {
    HomeDeclared,
    WorkerVerified,
    ClaudeConfigEnvironment,
    HermesConfig,
}

impl RootCandidate {
    fn home_declared(path: PathBuf) -> Self {
        Self {
            path,
            provenance: RootProvenance::HomeDeclared,
            expected_identity: None,
            project_scan_scopes: Vec::new(),
        }
    }

    fn discovered(
        path: PathBuf,
        device: u64,
        inode: u64,
        owner_uid: u32,
        project_scan_scopes: Vec<ProjectScanScope>,
    ) -> Self {
        Self {
            path,
            provenance: RootProvenance::WorkerVerified,
            expected_identity: Some((device, inode, owner_uid)),
            project_scan_scopes,
        }
    }
}

fn user_candidates(
    home: &Path,
    enumerator: RootEnumeratorId,
    issues: &mut Vec<RootIssue>,
) -> Vec<RootCandidate> {
    match enumerator {
        RootEnumeratorId::CodexHomeMarkersV1 => [".codex", ".agents/skills"]
            .into_iter()
            .map(|relative| RootCandidate::home_declared(home.join(relative)))
            .collect(),
        RootEnumeratorId::ClaudeHomeMarkersV2 => match std::env::var_os("CLAUDE_CONFIG_DIR") {
            Some(config_dir) => {
                let path = PathBuf::from(config_dir);
                if !path.is_absolute() {
                    issues.push(RootIssue {
                        project_id: None,
                        code: "claude_config_dir_rejected".to_string(),
                        safe_message: "Claude config root is not an absolute path".to_string(),
                    });
                    Vec::new()
                } else {
                    vec![RootCandidate {
                        path,
                        provenance: RootProvenance::ClaudeConfigEnvironment,
                        expected_identity: None,
                        project_scan_scopes: Vec::new(),
                    }]
                }
            }
            None => vec![RootCandidate::home_declared(home.join(".claude"))],
        },
        RootEnumeratorId::AntigravityHomeMarkersV2
        | RootEnumeratorId::AntigravityCliHomeMarkersV2 => {
            vec![RootCandidate::home_declared(home.join(".gemini"))]
        }
        RootEnumeratorId::HermesExternalDirsV1 => {
            vec![RootCandidate::home_declared(home.join(".hermes"))]
        }
    }
}

#[derive(Debug, Default)]
struct ProjectMarkerIndex {
    candidates: BTreeMap<RootEnumeratorId, BTreeMap<PathBuf, (u64, u64)>>,
    owner_uids: BTreeMap<PathBuf, u32>,
    project_scope_chains: BTreeMap<PathBuf, Vec<ProjectScanScope>>,
    project_scopes: BTreeMap<PathBuf, ProjectScanScope>,
    project_scope_sources: BTreeMap<PathBuf, ProjectIgnoreSource>,
    project_roots_by_id: BTreeMap<String, PathBuf>,
    excluded_paths: BTreeMap<String, BTreeSet<PathBuf>>,
    issues_by_enumerator: BTreeMap<RootEnumeratorId, Vec<RootIssue>>,
    issues: Vec<RootIssue>,
    pre_root_skips: Vec<PreRootSkippedPath>,
}

impl ProjectMarkerIndex {
    fn discover_once(
        home: &Path,
        enumerators: &BTreeSet<RootEnumeratorId>,
    ) -> Result<Self, String> {
        let mut supervisor = MarkerWalkSubtreeSupervisor::default_for_current_process();
        Self::discover_with_supervisor(home, enumerators, &mut supervisor)
    }

    fn discover_with_supervisor(
        home: &Path,
        enumerators: &BTreeSet<RootEnumeratorId>,
        supervisor: &mut dyn MarkerWalkSubtreeRunner,
    ) -> Result<Self, String> {
        let mut index = Self::default();
        if enumerators.is_empty() {
            return Ok(index);
        }
        let home_fd = match open(
            home,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::empty(),
        ) {
            Ok(fd) => fd,
            Err(_) => {
                index.record_issue(
                    enumerators.iter().copied(),
                    RootIssue {
                        project_id: None,
                        code: "root_enumeration_denied".to_string(),
                        safe_message: "Project marker enumeration could not open HOME safely"
                            .to_string(),
                    },
                );
                return Ok(index);
            }
        };
        let names = match sorted_directory_names(home_fd.as_fd()) {
            Ok(names) => names,
            Err(_) => {
                index.record_issue(
                    enumerators.iter().copied(),
                    RootIssue {
                        project_id: None,
                        code: "root_enumeration_denied".to_string(),
                        safe_message: "Project marker enumeration could not read HOME safely"
                            .to_string(),
                    },
                );
                return Ok(index);
            }
        };
        let mut remaining_entries = MAX_VISITED_ENTRIES;
        for name in names {
            if remaining_entries == 0 {
                index.record_issue(
                    enumerators.iter().copied(),
                    RootIssue {
                        project_id: None,
                        code: "root_enumeration_bound_reached".to_string(),
                        safe_message: "Project marker enumeration reached its deterministic bound"
                            .to_string(),
                    },
                );
                break;
            }
            // The original HOME walk counted every direct child, including ignored ones.
            // Charge it before filtering so independent workers retain the same global bound.
            remaining_entries -= 1;
            let bytes = name.as_bytes();
            if bytes.starts_with(b".") {
                continue;
            }
            let name_os = std::ffi::OsString::from_vec(bytes.to_vec());
            if fixed_ignore(&name_os.to_string_lossy()) {
                continue;
            }
            let entry_limit = remaining_entries;
            match supervisor.walk_subtree(home_fd.as_fd(), &name, enumerators, entry_limit) {
                Ok(MarkerWalkSubtreeOutcome::Completed(report)) => {
                    let Some(visited_entries) =
                        checked_worker_visit_count(report.visited_entries, entry_limit)
                    else {
                        remaining_entries = 0;
                        index.record_subtree_issue(
                            enumerators.iter().copied(),
                            &name,
                            "marker_subtree_worker_protocol_failed",
                            "A project marker worker report could not be verified",
                            true,
                        );
                        continue;
                    };
                    remaining_entries -= visited_entries;
                    match Self::from_worker_report(home, &name, enumerators, report) {
                        Ok(child_index) => index.merge(child_index),
                        Err(()) => {
                            // Do not reuse unconsumed entries after a malformed worker
                            // report: its claimed count is no longer trustworthy.
                            remaining_entries = 0;
                            index.record_subtree_issue(
                                enumerators.iter().copied(),
                                &name,
                                "marker_subtree_worker_protocol_failed",
                                "A project marker worker report could not be verified",
                                true,
                            );
                        }
                    }
                }
                Ok(MarkerWalkSubtreeOutcome::DeadlineExceeded { visited_entries }) => {
                    remaining_entries = remaining_entries.saturating_sub(visited_entries);
                    index.record_subtree_issue(
                        enumerators.iter().copied(),
                        &name,
                        "marker_subtree_deadline_exceeded",
                        "A project marker subtree exceeded its inactivity deadline",
                        true,
                    );
                }
                Ok(MarkerWalkSubtreeOutcome::Failed { visited_entries }) => {
                    remaining_entries = remaining_entries.saturating_sub(visited_entries);
                    index.record_subtree_issue(
                        enumerators.iter().copied(),
                        &name,
                        "marker_subtree_worker_failed",
                        "A project marker worker could not complete safely",
                        true,
                    );
                }
                Ok(MarkerWalkSubtreeOutcome::ProtocolFailed { visited_entries }) => {
                    remaining_entries = remaining_entries.saturating_sub(visited_entries);
                    index.record_subtree_issue(
                        enumerators.iter().copied(),
                        &name,
                        "marker_subtree_worker_protocol_failed",
                        "A project marker worker report could not be verified",
                        true,
                    );
                }
                Ok(MarkerWalkSubtreeOutcome::CapacityExceeded) => {
                    index.record_subtree_issue(
                        enumerators.iter().copied(),
                        &name,
                        "marker_subtree_reaper_capacity_exhausted",
                        "Project marker workers reached their bounded recovery capacity",
                        true,
                    );
                }
                Err(MarkerWalkSupervisorError::WorkerAborted) => {
                    return Err("worker_aborted".to_string())
                }
            }
        }
        Ok(index)
    }

    fn record_subtree_issue(
        &mut self,
        enumerators: impl IntoIterator<Item = RootEnumeratorId>,
        name: &CStr,
        code: &str,
        safe_message: &str,
        record_skip: bool,
    ) {
        let issue = RootIssue {
            project_id: None,
            code: code.to_string(),
            safe_message: safe_message.to_string(),
        };
        // A pre-root problem is represented to snapshots by the locator-bearing
        // `PreRootSkippedPath` below. Keep its per-enumerator coverage signal, but
        // do not also add a generic locator-less issue to the registry.
        for enumerator in enumerators {
            self.issues_by_enumerator
                .entry(enumerator)
                .or_default()
                .push(issue.clone());
        }
        if record_skip {
            self.pre_root_skips.push(PreRootSkippedPath {
                safe_relative_locator: redacted_home_child_locator(name),
                reason_code: code.to_string(),
                safe_message: safe_message.to_string(),
            });
        }
    }

    fn merge(&mut self, other: Self) {
        for (enumerator, candidates) in other.candidates {
            self.candidates
                .entry(enumerator)
                .or_default()
                .extend(candidates);
        }
        self.owner_uids.extend(other.owner_uids);
        self.project_scope_chains.extend(other.project_scope_chains);
        self.project_scopes.extend(other.project_scopes);
        self.project_scope_sources
            .extend(other.project_scope_sources);
        self.project_roots_by_id.extend(other.project_roots_by_id);
        for (project_id, paths) in other.excluded_paths {
            self.excluded_paths
                .entry(project_id)
                .or_default()
                .extend(paths);
        }
        for (enumerator, issues) in other.issues_by_enumerator {
            self.issues_by_enumerator
                .entry(enumerator)
                .or_default()
                .extend(issues);
        }
        self.issues.extend(other.issues);
        self.pre_root_skips.extend(other.pre_root_skips);
    }

    fn from_worker_report(
        home: &Path,
        dispatched_child: &CStr,
        allowed_enumerators: &BTreeSet<RootEnumeratorId>,
        report: MarkerWorkerReport,
    ) -> Result<Self, ()> {
        validate_marker_worker_report_budget(&report)?;
        let mut index = Self::default();
        let mut scopes = BTreeMap::new();
        for policy in report.policies {
            if scopes.contains_key(&policy.id) {
                return Err(());
            }
            let relative =
                checked_worker_subtree_relative_path(dispatched_child, &policy.root_components)?;
            let verified_root = home.join(&relative);
            let source_revision = ProjectScanScope::transient_ignore_source_revision(
                &verified_root,
                policy.device,
                policy.inode,
                &policy.exact_text,
                policy.missing,
            );
            let scope = ProjectScanScope::from_transient_ignore_source(
                &project_id(&verified_root),
                &verified_root,
                policy.device,
                policy.inode,
                policy.owner_uid,
                &policy.exact_text,
                policy.missing,
                &source_revision,
            )
            .map_err(|_| ())?;
            if index
                .project_scopes
                .insert(verified_root, scope.clone())
                .is_some()
            {
                return Err(());
            }
            scopes.insert(policy.id, scope);
        }
        for candidate in report.candidates {
            if candidate.owners.is_empty()
                || candidate
                    .owners
                    .iter()
                    .any(|owner| !allowed_enumerators.contains(owner))
                || candidate.owners.windows(2).any(|pair| pair[0] >= pair[1])
            {
                return Err(());
            }
            let relative =
                checked_worker_subtree_relative_path(dispatched_child, &candidate.components)?;
            let path = home.join(relative);
            let chain = candidate
                .scope_ids
                .iter()
                .map(|scope_id| scopes.get(scope_id).cloned().ok_or(()))
                .collect::<Result<Vec<_>, _>>()?;
            let mut unique_scope_ids = BTreeSet::new();
            if !candidate
                .scope_ids
                .iter()
                .all(|scope_id| unique_scope_ids.insert(*scope_id))
            {
                return Err(());
            }
            if chain
                .iter()
                .any(|scope| !path.starts_with(&scope.verified_root))
                || chain.windows(2).any(|pair| {
                    pair[0].verified_root == pair[1].verified_root
                        || !pair[1].verified_root.starts_with(&pair[0].verified_root)
                })
            {
                return Err(());
            }
            if index
                .owner_uids
                .insert(path.clone(), candidate.owner_uid)
                .is_some_and(|owner_uid| owner_uid != candidate.owner_uid)
            {
                return Err(());
            }
            if index
                .project_scope_chains
                .insert(path.clone(), chain)
                .is_some()
            {
                return Err(());
            }
            for owner in candidate.owners {
                let existing = index
                    .candidates
                    .entry(owner)
                    .or_default()
                    .insert(path.clone(), (candidate.device, candidate.inode));
                if existing.is_some_and(|identity| identity != (candidate.device, candidate.inode))
                {
                    return Err(());
                }
            }
        }
        for excluded in report.excluded_paths {
            let scope = scopes.get(&excluded.scope_id).ok_or(())?;
            let relative = checked_worker_relative_path(&excluded.relative_components)?;
            index
                .excluded_paths
                .entry(scope.policy.project_id.clone())
                .or_default()
                .insert(relative);
        }
        for (enumerator, issues) in report.issues_by_enumerator {
            if !allowed_enumerators.contains(&enumerator) {
                return Err(());
            }
            for issue in issues {
                let (code, safe_message) = canonical_worker_issue(&issue.code).ok_or(())?;
                let scope_project_id = issue
                    .scope_id
                    .map(|scope_id| {
                        scopes
                            .get(&scope_id)
                            .map(|scope| scope.policy.project_id.clone())
                            .ok_or(())
                    })
                    .transpose()?;
                let path_project_id = issue
                    .project_components
                    .as_deref()
                    .map(|components| {
                        checked_worker_subtree_relative_path(dispatched_child, components)
                    })
                    .transpose()?
                    .map(|relative| project_id(&home.join(relative)));
                if scope_project_id.is_some()
                    && path_project_id.is_some()
                    && scope_project_id != path_project_id
                {
                    return Err(());
                }
                index.record_issue(
                    [enumerator],
                    RootIssue {
                        project_id: scope_project_id.or(path_project_id),
                        code: code.to_string(),
                        safe_message: safe_message.to_string(),
                    },
                );
            }
        }
        Ok(index)
    }

    fn candidates_for(&self, enumerator: RootEnumeratorId) -> Vec<RootCandidate> {
        self.candidates
            .get(&enumerator)
            .into_iter()
            .flat_map(|candidates| candidates.iter())
            .map(|(path, (device, inode))| {
                RootCandidate::discovered(
                    path.clone(),
                    *device,
                    *inode,
                    self.owner_uids.get(path).copied().unwrap_or_default(),
                    self.project_scope_chains
                        .get(path)
                        .cloned()
                        .unwrap_or_default(),
                )
            })
            .collect()
    }

    fn issue_codes_for(&self, enumerator: RootEnumeratorId) -> BTreeSet<String> {
        self.issues_by_enumerator
            .get(&enumerator)
            .into_iter()
            .flat_map(|issues| issues.iter().map(|issue| issue.code.clone()))
            .collect()
    }

    fn record_issue(
        &mut self,
        enumerators: impl IntoIterator<Item = RootEnumeratorId>,
        issue: RootIssue,
    ) {
        for enumerator in enumerators {
            self.issues_by_enumerator
                .entry(enumerator)
                .or_default()
                .push(issue.clone());
        }
        self.issues.push(issue);
    }
}

const MARKER_WORKER_MODE: &str = "--harnesskit-marker-walk-v1";
const MARKER_WORKER_VIRTUAL_HOME: &str = "/harnesskit-marker-worker";
const MARKER_WORKER_INACTIVITY_DEADLINE: Duration = Duration::from_secs(2);
const MAX_MARKER_WORKER_REPORT_BYTES: usize = 8 * 1024 * 1024;
const MARKER_WORKER_HEARTBEAT_INTERVAL: Duration = Duration::from_millis(50);
const MARKER_WORKER_VISIT_CHECKPOINT_ENTRIES: usize = 1_024;
const MARKER_WORKER_PARENT_LIVENESS_POLL_INTERVAL: Duration = Duration::from_millis(25);
const MAX_MARKER_WORKER_REPORT_RECORDS: usize = 16_384;
const MARKER_WORKER_REPORT_OVERHEAD_BYTES: usize = 1_024;
const MAX_OUTSTANDING_MARKER_WORKERS: usize = 4;

fn checked_worker_visit_count(visited_entries: u64, entry_limit: usize) -> Option<usize> {
    let visited_entries = usize::try_from(visited_entries).ok()?;
    (visited_entries <= entry_limit).then_some(visited_entries)
}

fn deadline_worker_visit_charge(reported_entries: u64, entry_limit: usize) -> usize {
    let reported_entries =
        checked_worker_visit_count(reported_entries, entry_limit).unwrap_or(entry_limit);
    // Worker-side checkpointing guarantees it cannot pass another checkpoint without
    // reporting. Charge one unresolved checkpoint after a timeout so later siblings
    // cannot silently exceed the original global entry bound.
    reported_entries
        .saturating_add(MARKER_WORKER_VISIT_CHECKPOINT_ENTRIES)
        .min(entry_limit)
}

#[derive(Debug, Serialize, Deserialize)]
struct MarkerWorkerReport {
    visited_entries: u64,
    policies: Vec<MarkerWorkerPolicy>,
    candidates: Vec<MarkerWorkerCandidate>,
    excluded_paths: Vec<MarkerWorkerExcludedPath>,
    issues_by_enumerator: BTreeMap<RootEnumeratorId, Vec<MarkerWorkerIssue>>,
}

#[derive(Debug, Serialize, Deserialize)]
struct MarkerWorkerPolicy {
    id: u64,
    root_components: Vec<Vec<u8>>,
    device: u64,
    inode: u64,
    owner_uid: u32,
    exact_text: String,
    missing: bool,
}

#[derive(Debug, Serialize, Deserialize)]
struct MarkerWorkerCandidate {
    components: Vec<Vec<u8>>,
    device: u64,
    inode: u64,
    owner_uid: u32,
    owners: Vec<RootEnumeratorId>,
    scope_ids: Vec<u64>,
}

#[derive(Debug, Serialize, Deserialize)]
struct MarkerWorkerExcludedPath {
    scope_id: u64,
    relative_components: Vec<Vec<u8>>,
}

#[derive(Debug, Serialize, Deserialize)]
struct MarkerWorkerIssue {
    scope_id: Option<u64>,
    project_components: Option<Vec<Vec<u8>>>,
    code: String,
    safe_message: String,
}

#[derive(Debug, Serialize, Deserialize)]
enum MarkerWorkerTerminal {
    Completed(MarkerWorkerReport),
    Failed { visited_entries: u64 },
}

impl MarkerWorkerTerminal {
    fn visited_entries(&self) -> u64 {
        match self {
            Self::Completed(report) => report.visited_entries,
            Self::Failed { visited_entries } => *visited_entries,
        }
    }
}

enum MarkerWorkerPipeEvent {
    Heartbeat,
    Terminal(MarkerWorkerTerminal),
    ProtocolFailed,
    Closed,
}

enum MarkerWalkSubtreeOutcome {
    Completed(MarkerWorkerReport),
    DeadlineExceeded { visited_entries: usize },
    Failed { visited_entries: usize },
    ProtocolFailed { visited_entries: usize },
    CapacityExceeded,
}

#[derive(Debug, Clone, Copy)]
struct MarkerSubtreeRunError {
    visited_entries: usize,
}

enum MarkerWalkSupervisorError {
    WorkerAborted,
}

enum MarkerWalkSubtreeSupervisor {
    InProcess,
    Process,
}

trait MarkerWalkSubtreeRunner {
    fn walk_subtree(
        &mut self,
        home_fd: BorrowedFd<'_>,
        name: &CStr,
        enumerators: &BTreeSet<RootEnumeratorId>,
        entry_limit: usize,
    ) -> Result<MarkerWalkSubtreeOutcome, MarkerWalkSupervisorError>;
}

impl MarkerWalkSubtreeSupervisor {
    fn default_for_current_process() -> Self {
        if running_under_rust_test_harness() {
            Self::InProcess
        } else {
            Self::Process
        }
    }
}

impl MarkerWalkSubtreeRunner for MarkerWalkSubtreeSupervisor {
    fn walk_subtree(
        &mut self,
        home_fd: BorrowedFd<'_>,
        name: &CStr,
        enumerators: &BTreeSet<RootEnumeratorId>,
        entry_limit: usize,
    ) -> Result<MarkerWalkSubtreeOutcome, MarkerWalkSupervisorError> {
        match self {
            Self::InProcess => {
                match run_marker_subtree(home_fd, name, enumerators, entry_limit, &mut |_| {}) {
                    Ok(report) => Ok(MarkerWalkSubtreeOutcome::Completed(report)),
                    Err(error) => Ok(MarkerWalkSubtreeOutcome::Failed {
                        visited_entries: error.visited_entries,
                    }),
                }
            }
            Self::Process => {
                run_marker_subtree_in_process_worker(home_fd, name, enumerators, entry_limit)
            }
        }
    }
}

fn running_under_rust_test_harness() -> bool {
    std::env::current_exe()
        .ok()
        .and_then(|path| path.file_name().map(|name| name.to_owned()))
        .is_some_and(|name| {
            !matches!(
                name.to_string_lossy().as_ref(),
                "harness-desktop" | "HarnessKit"
            )
        })
}

struct MarkerWorkerCapacityPermit {
    transferred_to_reaper: bool,
}

struct MarkerWorkerReapTask {
    child: std::process::Child,
    reader: Option<thread::JoinHandle<()>>,
}

static MARKER_WORKER_OUTSTANDING: AtomicUsize = AtomicUsize::new(0);
static MARKER_WORKER_REAPER: OnceLock<mpsc::Sender<MarkerWorkerReapTask>> = OnceLock::new();

impl MarkerWorkerCapacityPermit {
    fn try_acquire() -> Option<Self> {
        let mut outstanding = MARKER_WORKER_OUTSTANDING.load(Ordering::Acquire);
        loop {
            if outstanding >= MAX_OUTSTANDING_MARKER_WORKERS {
                return None;
            }
            match MARKER_WORKER_OUTSTANDING.compare_exchange_weak(
                outstanding,
                outstanding + 1,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => {
                    return Some(Self {
                        transferred_to_reaper: false,
                    })
                }
                Err(observed) => outstanding = observed,
            }
        }
    }

    fn transfer_to_reaper(
        mut self,
        child: std::process::Child,
        reader: Option<thread::JoinHandle<()>>,
    ) {
        self.transferred_to_reaper = true;
        let task = MarkerWorkerReapTask { child, reader };
        if let Err(error) = marker_worker_reaper_sender().send(task) {
            // The primary reaper has no fallible work, but retain the same bounded
            // capacity if its thread is unavailable rather than leaking a child.
            thread::spawn(move || reap_marker_worker_task(error.0));
        }
    }
}

impl Drop for MarkerWorkerCapacityPermit {
    fn drop(&mut self) {
        if !self.transferred_to_reaper {
            release_marker_worker_capacity();
        }
    }
}

fn marker_worker_reaper_sender() -> &'static mpsc::Sender<MarkerWorkerReapTask> {
    MARKER_WORKER_REAPER.get_or_init(|| {
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            while let Ok(task) = receiver.recv() {
                reap_marker_worker_task(task);
            }
        });
        sender
    })
}

fn reap_marker_worker_task(mut task: MarkerWorkerReapTask) {
    let _ = task.child.wait();
    if let Some(reader) = task.reader.take() {
        let _ = reader.join();
    }
    release_marker_worker_capacity();
}

fn release_marker_worker_capacity() {
    let mut outstanding = MARKER_WORKER_OUTSTANDING.load(Ordering::Acquire);
    while outstanding != 0 {
        match MARKER_WORKER_OUTSTANDING.compare_exchange_weak(
            outstanding,
            outstanding - 1,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => return,
            Err(observed) => outstanding = observed,
        }
    }
}

fn run_marker_subtree_in_process_worker(
    home_fd: BorrowedFd<'_>,
    name: &CStr,
    enumerators: &BTreeSet<RootEnumeratorId>,
    entry_limit: usize,
) -> Result<MarkerWalkSubtreeOutcome, MarkerWalkSupervisorError> {
    let executable = match std::env::current_exe() {
        Ok(path) => path,
        Err(_) => return Ok(MarkerWalkSubtreeOutcome::Failed { visited_entries: 0 }),
    };
    let Some(mask) = marker_worker_enumerator_mask(enumerators) else {
        return Ok(MarkerWalkSubtreeOutcome::Failed { visited_entries: 0 });
    };
    let Some(worker_permit) = MarkerWorkerCapacityPermit::try_acquire() else {
        return Ok(MarkerWalkSubtreeOutcome::CapacityExceeded);
    };
    let stdin = match rustix::io::dup(home_fd) {
        Ok(fd) => {
            let raw_fd = fd.into_raw_fd();
            // SAFETY: `dup` gives this function unique ownership of `raw_fd`.
            let file = unsafe { std::fs::File::from_raw_fd(raw_fd) };
            Stdio::from(file)
        }
        Err(_) => return Ok(MarkerWalkSubtreeOutcome::Failed { visited_entries: 0 }),
    };
    let mut child = match Command::new(executable)
        .arg(MARKER_WORKER_MODE)
        .arg(hex_encode(name.to_bytes()))
        .arg(mask.to_string())
        .arg(entry_limit.to_string())
        .arg(std::process::id().to_string())
        .env_clear()
        .stdin(stdin)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(child) => child,
        Err(_) => return Ok(MarkerWalkSubtreeOutcome::Failed { visited_entries: 0 }),
    };
    let Some(stdout) = child.stdout.take() else {
        let _ = child.kill();
        reap_marker_worker(child, None, worker_permit);
        return Ok(MarkerWalkSubtreeOutcome::Failed { visited_entries: 0 });
    };
    let (event_tx, event_rx) = mpsc::channel();
    let worker_progress = Arc::new(AtomicU64::new(0));
    let reader_progress = Arc::clone(&worker_progress);
    let reader = thread::spawn(move || {
        read_marker_worker_events(stdout, event_tx, reader_progress, entry_limit)
    });
    let mut last_activity = Instant::now();
    let mut terminal = None;
    loop {
        let protocol_failed = match event_rx.recv_timeout(Duration::from_millis(50)) {
            Ok(MarkerWorkerPipeEvent::Heartbeat) => {
                last_activity = Instant::now();
                false
            }
            Ok(MarkerWorkerPipeEvent::Terminal(response)) => {
                last_activity = Instant::now();
                terminal = Some(response);
                false
            }
            Ok(MarkerWorkerPipeEvent::ProtocolFailed) => true,
            Ok(MarkerWorkerPipeEvent::Closed) | Err(mpsc::RecvTimeoutError::Disconnected) => false,
            Err(mpsc::RecvTimeoutError::Timeout) => false,
        };
        if protocol_failed {
            let _ = child.kill();
            reap_marker_worker(child, Some(reader), worker_permit);
            return Ok(MarkerWalkSubtreeOutcome::ProtocolFailed {
                visited_entries: entry_limit,
            });
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                return finish_marker_worker(
                    status,
                    reader,
                    &event_rx,
                    &mut terminal,
                    &worker_progress,
                    entry_limit,
                );
            }
            Ok(None) => {}
            Err(_) => {
                let visited_entries = deadline_worker_visit_charge(
                    worker_progress.load(Ordering::Relaxed),
                    entry_limit,
                );
                let _ = child.kill();
                reap_marker_worker(child, Some(reader), worker_permit);
                return Ok(MarkerWalkSubtreeOutcome::Failed { visited_entries });
            }
        }
        if last_activity.elapsed() >= MARKER_WORKER_INACTIVITY_DEADLINE {
            // Resolve the exit/terminal race before sending a kill signal. A worker
            // that completed at this boundary must retain its completed result.
            let drained_events = drain_marker_worker_events(&event_rx, &mut terminal);
            if drained_events.protocol_failed {
                let _ = child.kill();
                reap_marker_worker(child, Some(reader), worker_permit);
                return Ok(MarkerWalkSubtreeOutcome::ProtocolFailed {
                    visited_entries: entry_limit,
                });
            }
            if drained_events.activity_seen {
                last_activity = Instant::now();
                continue;
            }
            if let Ok(Some(status)) = child.try_wait() {
                return finish_marker_worker(
                    status,
                    reader,
                    &event_rx,
                    &mut terminal,
                    &worker_progress,
                    entry_limit,
                );
            }
            let visited_entries =
                deadline_worker_visit_charge(worker_progress.load(Ordering::Relaxed), entry_limit);
            let killed = child.kill().is_ok();
            if let Ok(Some(status)) = child.try_wait() {
                if !worker_was_killed_at_deadline(killed, status) {
                    return finish_marker_worker(
                        status,
                        reader,
                        &event_rx,
                        &mut terminal,
                        &worker_progress,
                        entry_limit,
                    );
                }
                let _ = reader.join();
                return Ok(MarkerWalkSubtreeOutcome::DeadlineExceeded { visited_entries });
            }
            reap_marker_worker(child, Some(reader), worker_permit);
            return Ok(MarkerWalkSubtreeOutcome::DeadlineExceeded { visited_entries });
        }
    }
}

fn worker_was_killed_at_deadline(kill_requested: bool, status: std::process::ExitStatus) -> bool {
    kill_requested && status.signal() == Some(Signal::KILL.as_raw())
}

struct MarkerWorkerEventDrain {
    activity_seen: bool,
    protocol_failed: bool,
}

fn drain_marker_worker_events(
    event_rx: &mpsc::Receiver<MarkerWorkerPipeEvent>,
    terminal: &mut Option<MarkerWorkerTerminal>,
) -> MarkerWorkerEventDrain {
    let mut activity_seen = false;
    let mut protocol_failed = false;
    while let Ok(event) = event_rx.try_recv() {
        match event {
            MarkerWorkerPipeEvent::Heartbeat => activity_seen = true,
            MarkerWorkerPipeEvent::Terminal(response) => {
                activity_seen = true;
                *terminal = Some(response);
            }
            MarkerWorkerPipeEvent::Closed => {}
            MarkerWorkerPipeEvent::ProtocolFailed => protocol_failed = true,
        }
    }
    MarkerWorkerEventDrain {
        activity_seen,
        protocol_failed,
    }
}

fn finish_marker_worker(
    status: std::process::ExitStatus,
    reader: thread::JoinHandle<()>,
    event_rx: &mpsc::Receiver<MarkerWorkerPipeEvent>,
    terminal: &mut Option<MarkerWorkerTerminal>,
    worker_progress: &AtomicU64,
    entry_limit: usize,
) -> Result<MarkerWalkSubtreeOutcome, MarkerWalkSupervisorError> {
    let _ = reader.join();
    if !status.success() {
        return Err(MarkerWalkSupervisorError::WorkerAborted);
    }
    if drain_marker_worker_events(event_rx, terminal).protocol_failed {
        return Ok(MarkerWalkSubtreeOutcome::ProtocolFailed {
            visited_entries: entry_limit,
        });
    }
    Ok(marker_worker_terminal_outcome(
        terminal.take(),
        worker_progress,
        entry_limit,
    ))
}

fn marker_worker_terminal_outcome(
    terminal: Option<MarkerWorkerTerminal>,
    worker_progress: &AtomicU64,
    entry_limit: usize,
) -> MarkerWalkSubtreeOutcome {
    match terminal {
        Some(MarkerWorkerTerminal::Completed(report)) => {
            match verified_terminal_worker_visit_count(
                report.visited_entries,
                worker_progress,
                entry_limit,
            ) {
                Some(_) => MarkerWalkSubtreeOutcome::Completed(report),
                None => MarkerWalkSubtreeOutcome::ProtocolFailed {
                    visited_entries: entry_limit,
                },
            }
        }
        Some(MarkerWorkerTerminal::Failed { visited_entries }) => {
            match verified_terminal_worker_visit_count(
                visited_entries,
                worker_progress,
                entry_limit,
            ) {
                Some(visited_entries) => MarkerWalkSubtreeOutcome::Failed { visited_entries },
                None => MarkerWalkSubtreeOutcome::ProtocolFailed {
                    visited_entries: entry_limit,
                },
            }
        }
        None => MarkerWalkSubtreeOutcome::Failed {
            visited_entries: deadline_worker_visit_charge(
                worker_progress.load(Ordering::Relaxed),
                entry_limit,
            ),
        },
    }
}

fn verified_terminal_worker_visit_count(
    terminal_entries: u64,
    worker_progress: &AtomicU64,
    entry_limit: usize,
) -> Option<usize> {
    let terminal_entries = checked_worker_visit_count(terminal_entries, entry_limit)?;
    let reported_entries =
        checked_worker_visit_count(worker_progress.load(Ordering::Relaxed), entry_limit)?;
    (terminal_entries >= reported_entries).then_some(terminal_entries)
}

fn reap_marker_worker(
    child: std::process::Child,
    reader: Option<thread::JoinHandle<()>>,
    worker_permit: MarkerWorkerCapacityPermit,
) {
    worker_permit.transfer_to_reaper(child, reader);
}

fn read_marker_worker_events(
    mut stdout: std::process::ChildStdout,
    event_tx: mpsc::Sender<MarkerWorkerPipeEvent>,
    worker_progress: Arc<AtomicU64>,
    entry_limit: usize,
) {
    let mut last_reported_entries = 0_u64;
    loop {
        let mut tag = [0_u8; 1];
        if stdout.read_exact(&mut tag).is_err() {
            let _ = event_tx.send(MarkerWorkerPipeEvent::Closed);
            return;
        }
        match tag[0] {
            b'H' => {
                let mut bytes = [0_u8; 8];
                if stdout.read_exact(&mut bytes).is_err() {
                    let _ = event_tx.send(MarkerWorkerPipeEvent::Closed);
                    return;
                }
                let visited_entries = u64::from_be_bytes(bytes);
                if checked_worker_visit_count(visited_entries, entry_limit).is_none()
                    || visited_entries < last_reported_entries
                {
                    let _ = event_tx.send(MarkerWorkerPipeEvent::ProtocolFailed);
                    return;
                }
                last_reported_entries = visited_entries;
                worker_progress.store(visited_entries, Ordering::Relaxed);
                let _ = event_tx.send(MarkerWorkerPipeEvent::Heartbeat);
            }
            b'R' => {
                let mut bytes = [0_u8; 4];
                if stdout.read_exact(&mut bytes).is_err() {
                    let _ = event_tx.send(MarkerWorkerPipeEvent::Closed);
                    return;
                }
                let length = u32::from_be_bytes(bytes) as usize;
                if length == 0 || length > MAX_MARKER_WORKER_REPORT_BYTES {
                    let _ = event_tx.send(MarkerWorkerPipeEvent::ProtocolFailed);
                    return;
                }
                let mut payload = vec![0_u8; length];
                if stdout.read_exact(&mut payload).is_err() {
                    let _ = event_tx.send(MarkerWorkerPipeEvent::Closed);
                    return;
                }
                let Ok(terminal) = serde_json::from_slice(&payload) else {
                    let _ = event_tx.send(MarkerWorkerPipeEvent::ProtocolFailed);
                    return;
                };
                let _ = event_tx.send(MarkerWorkerPipeEvent::Terminal(terminal));
                return;
            }
            _ => {
                let _ = event_tx.send(MarkerWorkerPipeEvent::ProtocolFailed);
                return;
            }
        }
    }
}

fn worker_parent_is_alive(expected_parent_process_id: u32) -> bool {
    getppid().is_some_and(|actual_parent_process_id| {
        u32::try_from(actual_parent_process_id.as_raw_nonzero().get()).ok()
            == Some(expected_parent_process_id)
    })
}

pub fn run_marker_walk_worker_if_requested() -> bool {
    let mut args = std::env::args_os();
    let _ = args.next();
    if args.next().as_deref() != Some(std::ffi::OsStr::new(MARKER_WORKER_MODE)) {
        return false;
    }
    let Some(encoded_name) = args.next() else {
        return true;
    };
    let Some(mask) = args
        .next()
        .and_then(|value| value.to_string_lossy().parse::<u8>().ok())
    else {
        return true;
    };
    let Some(entry_limit) = args
        .next()
        .and_then(|value| value.to_string_lossy().parse::<usize>().ok())
    else {
        return true;
    };
    let Some(parent_process_id) = args
        .next()
        .and_then(|value| value.to_string_lossy().parse::<u32>().ok())
        .filter(|process_id| *process_id != 0)
    else {
        return true;
    };
    if args.next().is_some() {
        return true;
    }
    let Some(name) = decode_worker_component(encoded_name.as_os_str()) else {
        return true;
    };
    let Some(enumerators) = marker_worker_enumerators(mask) else {
        return true;
    };
    // SAFETY: worker mode is entered only in the child process whose stdin is a duplicated HOME fd.
    let home_fd = unsafe { OwnedFd::from_raw_fd(0) };
    let _parent_liveness_monitor = thread::spawn(move || {
        loop {
            if !worker_parent_is_alive(parent_process_id) {
                // A direct-parent identity mismatch means the parent ended. Exit
                // from this dedicated thread so a main thread blocked inside a
                // filesystem syscall cannot outlive its parent.
                std::process::exit(0);
            }
            thread::sleep(MARKER_WORKER_PARENT_LIVENESS_POLL_INTERVAL);
        }
    });
    let mut stdout = std::io::stdout().lock();
    let terminal = {
        let mut last_heartbeat = Instant::now()
            .checked_sub(MARKER_WORKER_HEARTBEAT_INTERVAL)
            .unwrap_or_else(Instant::now);
        let mut last_reported = None;
        let mut heartbeat = |visited_entries: usize| {
            let checkpoint_due = last_reported != Some(visited_entries)
                && visited_entries % MARKER_WORKER_VISIT_CHECKPOINT_ENTRIES == 0;
            if !checkpoint_due && last_heartbeat.elapsed() < MARKER_WORKER_HEARTBEAT_INTERVAL {
                return;
            }
            let visit_count = visited_entries;
            let Ok(visited_entries) = u64::try_from(visit_count) else {
                return;
            };
            let _ = stdout.write_all(b"H");
            let _ = stdout.write_all(&visited_entries.to_be_bytes());
            let _ = stdout.flush();
            last_heartbeat = Instant::now();
            last_reported = Some(visit_count);
        };
        match run_marker_subtree(
            home_fd.as_fd(),
            &name,
            &enumerators,
            entry_limit,
            &mut heartbeat,
        ) {
            Ok(report) => MarkerWorkerTerminal::Completed(report),
            Err(error) => MarkerWorkerTerminal::Failed {
                visited_entries: u64::try_from(error.visited_entries).unwrap_or(u64::MAX),
            },
        }
    };
    if write_marker_worker_terminal(&mut stdout, &terminal).is_err() {
        let fallback = MarkerWorkerTerminal::Failed {
            visited_entries: terminal.visited_entries(),
        };
        let _ = write_marker_worker_terminal(&mut stdout, &fallback);
    }
    true
}

fn write_marker_worker_terminal(
    stdout: &mut dyn Write,
    terminal: &MarkerWorkerTerminal,
) -> Result<(), ()> {
    let mut payload = BoundedMarkerWorkerBuffer::new(MAX_MARKER_WORKER_REPORT_BYTES);
    serde_json::to_writer(&mut payload, terminal).map_err(|_| ())?;
    let payload = payload.into_inner();
    if payload.is_empty() || payload.len() > MAX_MARKER_WORKER_REPORT_BYTES {
        return Err(());
    }
    stdout.write_all(b"R").map_err(|_| ())?;
    stdout
        .write_all(&(payload.len() as u32).to_be_bytes())
        .map_err(|_| ())?;
    stdout.write_all(&payload).map_err(|_| ())?;
    stdout.flush().map_err(|_| ())
}

struct BoundedMarkerWorkerBuffer {
    bytes: Vec<u8>,
    max_len: usize,
}

impl BoundedMarkerWorkerBuffer {
    fn new(max_len: usize) -> Self {
        Self {
            bytes: Vec::new(),
            max_len,
        }
    }

    fn into_inner(self) -> Vec<u8> {
        self.bytes
    }
}

impl Write for BoundedMarkerWorkerBuffer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        let Some(length) = self.bytes.len().checked_add(bytes.len()) else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::WriteZero,
                "marker worker report is too large",
            ));
        };
        if length > self.max_len {
            return Err(std::io::Error::new(
                std::io::ErrorKind::WriteZero,
                "marker worker report is too large",
            ));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn run_marker_subtree(
    home_fd: BorrowedFd<'_>,
    name: &CStr,
    enumerators: &BTreeSet<RootEnumeratorId>,
    entry_limit: usize,
    heartbeat: &mut dyn FnMut(usize),
) -> Result<MarkerWorkerReport, MarkerSubtreeRunError> {
    heartbeat(0);
    let observed = match statat(home_fd, name, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(observed) => observed,
        Err(Errno::NOENT) => {
            return marker_worker_report(
                &ProjectMarkerIndex::default(),
                Path::new(MARKER_WORKER_VIRTUAL_HOME),
                0,
            )
            .map_err(|_| MarkerSubtreeRunError { visited_entries: 0 })
        }
        Err(_) => {
            let mut index = ProjectMarkerIndex::default();
            index.record_issue(
                enumerators.iter().copied(),
                RootIssue {
                    project_id: None,
                    code: "root_enumeration_changed".to_string(),
                    safe_message: "A project marker path changed during enumeration".to_string(),
                },
            );
            return marker_worker_report(&index, Path::new(MARKER_WORKER_VIRTUAL_HOME), 0)
                .map_err(|_| MarkerSubtreeRunError { visited_entries: 0 });
        }
    };
    let file_type = FileType::from_raw_mode(observed.st_mode);
    if file_type == FileType::Symlink || file_type != FileType::Directory {
        return marker_worker_report(
            &ProjectMarkerIndex::default(),
            Path::new(MARKER_WORKER_VIRTUAL_HOME),
            0,
        )
        .map_err(|_| MarkerSubtreeRunError { visited_entries: 0 });
    }
    heartbeat(0);
    let child = match open_verified_directory(home_fd, name, &observed) {
        Ok(child) => child,
        Err(Errno::LOOP) => {
            let mut index = ProjectMarkerIndex::default();
            index.record_issue(
                enumerators.iter().copied(),
                RootIssue {
                    project_id: None,
                    code: "symlink_marker_rejected".to_string(),
                    safe_message: "A project marker directory symlink was rejected".to_string(),
                },
            );
            return marker_worker_report(&index, Path::new(MARKER_WORKER_VIRTUAL_HOME), 0)
                .map_err(|_| MarkerSubtreeRunError { visited_entries: 0 });
        }
        Err(_) => {
            let mut index = ProjectMarkerIndex::default();
            index.record_issue(
                enumerators.iter().copied(),
                RootIssue {
                    project_id: None,
                    code: "root_enumeration_denied".to_string(),
                    safe_message: "A project marker directory could not be opened safely"
                        .to_string(),
                },
            );
            return marker_worker_report(&index, Path::new(MARKER_WORKER_VIRTUAL_HOME), 0)
                .map_err(|_| MarkerSubtreeRunError { visited_entries: 0 });
        }
    };
    let worker_home = Path::new(MARKER_WORKER_VIRTUAL_HOME);
    let relative = PathBuf::from(std::ffi::OsString::from_vec(name.to_bytes().to_vec()));
    let mut index = ProjectMarkerIndex::default();
    let mut walk = MarkerWalk {
        home: worker_home,
        enumerators,
        visited: 0,
        entry_limit,
        index: &mut index,
        heartbeat,
    };
    walk.walk_marker_directory(&child, &relative, 1, &[]);
    let visited_entries = walk.visited;
    drop(walk);
    marker_worker_report(&index, worker_home, visited_entries)
        .map_err(|_| MarkerSubtreeRunError { visited_entries })
}

fn marker_worker_report(
    index: &ProjectMarkerIndex,
    worker_home: &Path,
    visited_entries: usize,
) -> Result<MarkerWorkerReport, ()> {
    let visited_entries = u64::try_from(visited_entries).map_err(|_| ())?;
    let mut budget = MarkerWorkerReportBudget::new();
    let mut policies = Vec::new();
    let mut policy_ids_by_root = BTreeMap::new();
    let mut policy_ids_by_project_id = BTreeMap::new();
    for (offset, (root, scope)) in index.project_scopes.iter().enumerate() {
        let source = index.project_scope_sources.get(root).ok_or(())?;
        let id = u64::try_from(offset).map_err(|_| ())?;
        let components = worker_relative_components(worker_home, root)?;
        budget.reserve_record()?;
        budget.reserve_components(&components)?;
        budget.reserve_text(&source.exact_text)?;
        policy_ids_by_root.insert(root.clone(), id);
        policy_ids_by_project_id.insert(scope.policy.project_id.clone(), id);
        policies.push(MarkerWorkerPolicy {
            id,
            root_components: components,
            device: scope.root_device,
            inode: scope.root_inode,
            owner_uid: scope.owner_uid,
            exact_text: source.exact_text.clone(),
            missing: source.missing,
        });
    }
    let mut candidate_builders = BTreeMap::<PathBuf, MarkerWorkerCandidateBuilder>::new();
    for (owner, roots) in &index.candidates {
        for (path, (device, inode)) in roots {
            if !candidate_builders.contains_key(path) {
                let scope_ids = index
                    .project_scope_chains
                    .get(path)
                    .into_iter()
                    .flatten()
                    .map(|scope| {
                        policy_ids_by_root
                            .get(&scope.verified_root)
                            .copied()
                            .ok_or(())
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                candidate_builders.insert(
                    path.clone(),
                    MarkerWorkerCandidateBuilder {
                        device: *device,
                        inode: *inode,
                        owner_uid: index.owner_uids.get(path).copied().ok_or(())?,
                        scope_ids,
                        owners: BTreeSet::new(),
                    },
                );
            }
            let builder = candidate_builders.get_mut(path).ok_or(())?;
            if builder.device != *device || builder.inode != *inode {
                return Err(());
            }
            builder.owners.insert(*owner);
        }
    }
    let mut candidates = Vec::new();
    for (path, builder) in candidate_builders {
        let components = worker_relative_components(worker_home, &path)?;
        budget.reserve_record()?;
        budget.reserve_components(&components)?;
        budget.reserve_numbers(builder.owners.len() + builder.scope_ids.len())?;
        candidates.push(MarkerWorkerCandidate {
            components,
            device: builder.device,
            inode: builder.inode,
            owner_uid: builder.owner_uid,
            owners: builder.owners.into_iter().collect(),
            scope_ids: builder.scope_ids,
        });
    }
    let mut excluded_paths = Vec::new();
    for (project_id, paths) in &index.excluded_paths {
        let scope_id = policy_ids_by_project_id
            .get(project_id)
            .copied()
            .ok_or(())?;
        for path in paths {
            let relative_components = path_components(path)?;
            budget.reserve_record()?;
            budget.reserve_components(&relative_components)?;
            excluded_paths.push(MarkerWorkerExcludedPath {
                scope_id,
                relative_components,
            });
        }
    }
    let mut issues_by_enumerator = BTreeMap::new();
    for (enumerator, issues) in &index.issues_by_enumerator {
        let mut worker_issues = Vec::new();
        for issue in issues {
            let project_components = issue
                .project_id
                .as_ref()
                .and_then(|project_id| index.project_roots_by_id.get(project_id))
                .map(|root| worker_relative_components(worker_home, root))
                .transpose()?;
            budget.reserve_record()?;
            if let Some(components) = &project_components {
                budget.reserve_components(components)?;
            }
            budget.reserve_text(&issue.code)?;
            budget.reserve_text(&issue.safe_message)?;
            worker_issues.push(MarkerWorkerIssue {
                scope_id: issue
                    .project_id
                    .as_ref()
                    .and_then(|project_id| policy_ids_by_project_id.get(project_id))
                    .copied(),
                project_components,
                code: issue.code.clone(),
                safe_message: issue.safe_message.clone(),
            });
        }
        issues_by_enumerator.insert(*enumerator, worker_issues);
    }
    Ok(MarkerWorkerReport {
        visited_entries,
        policies,
        candidates,
        excluded_paths,
        issues_by_enumerator,
    })
}

struct MarkerWorkerReportBudget {
    remaining_bytes: usize,
    records: usize,
}

impl MarkerWorkerReportBudget {
    fn new() -> Self {
        Self {
            remaining_bytes: MAX_MARKER_WORKER_REPORT_BYTES - MARKER_WORKER_REPORT_OVERHEAD_BYTES,
            records: 0,
        }
    }

    fn reserve_record(&mut self) -> Result<(), ()> {
        self.records = self.records.checked_add(1).ok_or(())?;
        if self.records > MAX_MARKER_WORKER_REPORT_RECORDS {
            return Err(());
        }
        // Reserve JSON field names, punctuation, numeric identity fields and map keys.
        self.reserve(256)
    }

    fn reserve_text(&mut self, value: &str) -> Result<(), ()> {
        self.reserve(value.len().checked_mul(6).ok_or(())?)
    }

    fn reserve_components(&mut self, components: &[Vec<u8>]) -> Result<(), ()> {
        let byte_len = components.iter().try_fold(0_usize, |total, component| {
            total.checked_add(component.len()).ok_or(())
        })?;
        self.reserve(byte_len.checked_mul(6).ok_or(())?)?;
        self.reserve(components.len().checked_mul(16).ok_or(())?)
    }

    fn reserve_numbers(&mut self, count: usize) -> Result<(), ()> {
        self.reserve(count.checked_mul(24).ok_or(())?)
    }

    fn reserve(&mut self, bytes: usize) -> Result<(), ()> {
        self.remaining_bytes = self.remaining_bytes.checked_sub(bytes).ok_or(())?;
        Ok(())
    }
}

fn validate_marker_worker_report_budget(report: &MarkerWorkerReport) -> Result<(), ()> {
    let mut budget = MarkerWorkerReportBudget::new();
    for policy in &report.policies {
        budget.reserve_record()?;
        budget.reserve_components(&policy.root_components)?;
        budget.reserve_text(&policy.exact_text)?;
    }
    for candidate in &report.candidates {
        budget.reserve_record()?;
        budget.reserve_components(&candidate.components)?;
        budget.reserve_numbers(candidate.owners.len() + candidate.scope_ids.len())?;
    }
    for excluded in &report.excluded_paths {
        budget.reserve_record()?;
        budget.reserve_components(&excluded.relative_components)?;
    }
    for issues in report.issues_by_enumerator.values() {
        for issue in issues {
            budget.reserve_record()?;
            if let Some(components) = &issue.project_components {
                budget.reserve_components(components)?;
            }
            budget.reserve_text(&issue.code)?;
            budget.reserve_text(&issue.safe_message)?;
        }
    }
    Ok(())
}

struct MarkerWorkerCandidateBuilder {
    device: u64,
    inode: u64,
    owner_uid: u32,
    scope_ids: Vec<u64>,
    owners: BTreeSet<RootEnumeratorId>,
}

fn worker_relative_components(worker_home: &Path, path: &Path) -> Result<Vec<Vec<u8>>, ()> {
    let relative = path.strip_prefix(worker_home).map_err(|_| ())?;
    path_components(relative)
}

fn path_components(path: &Path) -> Result<Vec<Vec<u8>>, ()> {
    let mut components = Vec::new();
    for component in path.components() {
        let Component::Normal(component) = component else {
            return Err(());
        };
        let bytes = component.as_bytes();
        if bytes.is_empty() || bytes.contains(&b'/') || bytes.contains(&0) {
            return Err(());
        }
        components.push(bytes.to_vec());
    }
    if components.is_empty() {
        return Err(());
    }
    Ok(components)
}

fn checked_worker_relative_path(components: &[Vec<u8>]) -> Result<PathBuf, ()> {
    if components.is_empty() || components.len() > MAX_HOME_DEPTH {
        return Err(());
    }
    let mut path = PathBuf::new();
    for component in components {
        if component.is_empty()
            || component.contains(&b'/')
            || component.contains(&0)
            || matches!(component.as_slice(), b"." | b"..")
        {
            return Err(());
        }
        path.push(std::ffi::OsString::from_vec(component.clone()));
    }
    Ok(path)
}

fn checked_worker_subtree_relative_path(
    dispatched_child: &CStr,
    components: &[Vec<u8>],
) -> Result<PathBuf, ()> {
    if components
        .first()
        .is_none_or(|component| component.as_slice() != dispatched_child.to_bytes())
    {
        return Err(());
    }
    checked_worker_relative_path(components)
}

fn canonical_worker_issue(code: &str) -> Option<(&'static str, &'static str)> {
    match code {
        "root_enumeration_denied" => Some((
            "root_enumeration_denied",
            "A project marker directory could not be read safely",
        )),
        "root_enumeration_changed" => Some((
            "root_enumeration_changed",
            "A project marker path changed during enumeration",
        )),
        "symlink_marker_rejected" => Some((
            "symlink_marker_rejected",
            "A project marker symlink was rejected",
        )),
        "root_enumeration_bound_reached" => Some((
            "root_enumeration_bound_reached",
            "Project marker enumeration reached its deterministic bound",
        )),
        "project_ignore_invalid" => {
            Some(("project_ignore_invalid", "Project ignore rules are invalid"))
        }
        "project_ignore_unreadable" => Some((
            "project_ignore_unreadable",
            "Project ignore rules could not be read",
        )),
        "project_ignore_changed_during_read" => Some((
            "project_ignore_changed_during_read",
            "Project ignore rules changed during reading",
        )),
        "project_root_changed" => Some((
            "project_root_changed",
            "Project root changed during marker enumeration",
        )),
        "project_ignore_stale" => Some((
            "project_ignore_stale",
            "Project ignore rules must be reloaded",
        )),
        "project_ignore_write_failed" => Some((
            "project_ignore_write_failed",
            "Project ignore rules could not be saved",
        )),
        _ => None,
    }
}

fn redacted_home_child_locator(name: &CStr) -> String {
    let digest = digest(&[name.to_bytes()]);
    format!("home-child:{}", &digest[..16])
}

fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn decode_worker_component(encoded: &std::ffi::OsStr) -> Option<CString> {
    let encoded = encoded.to_string_lossy();
    if encoded.is_empty() || encoded.len() % 2 != 0 || encoded.len() > 512 {
        return None;
    }
    let bytes = encoded
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            std::str::from_utf8(pair)
                .ok()
                .and_then(|pair| u8::from_str_radix(pair, 16).ok())
        })
        .collect::<Option<Vec<_>>>()?;
    if bytes.is_empty() || bytes.contains(&b'/') || bytes.contains(&0) || bytes.starts_with(b".") {
        return None;
    }
    CString::new(bytes).ok()
}

fn marker_worker_enumerator_mask(enumerators: &BTreeSet<RootEnumeratorId>) -> Option<u8> {
    let mut mask = 0_u8;
    for enumerator in enumerators {
        let bit = match enumerator {
            RootEnumeratorId::CodexHomeMarkersV1 => 1,
            RootEnumeratorId::ClaudeHomeMarkersV2 => 2,
            RootEnumeratorId::AntigravityHomeMarkersV2 => 4,
            RootEnumeratorId::AntigravityCliHomeMarkersV2 => 8,
            RootEnumeratorId::HermesExternalDirsV1 => return None,
        };
        mask |= bit;
    }
    (mask != 0).then_some(mask)
}

fn marker_worker_enumerators(mask: u8) -> Option<BTreeSet<RootEnumeratorId>> {
    if mask == 0 || mask & !0b1111 != 0 {
        return None;
    }
    let mut enumerators = BTreeSet::new();
    for (bit, enumerator) in [
        (1, RootEnumeratorId::CodexHomeMarkersV1),
        (2, RootEnumeratorId::ClaudeHomeMarkersV2),
        (4, RootEnumeratorId::AntigravityHomeMarkersV2),
        (8, RootEnumeratorId::AntigravityCliHomeMarkersV2),
    ] {
        if mask & bit != 0 {
            enumerators.insert(enumerator);
        }
    }
    Some(enumerators)
}

struct MarkerWalk<'a> {
    home: &'a Path,
    enumerators: &'a BTreeSet<RootEnumeratorId>,
    visited: usize,
    entry_limit: usize,
    index: &'a mut ProjectMarkerIndex,
    heartbeat: &'a mut dyn FnMut(usize),
}

struct CurrentProjectRegistration {
    scope: Option<ProjectScanScope>,
}

impl MarkerWalk<'_> {
    fn walk_marker_directory(
        &mut self,
        directory: &OwnedFd,
        relative: &Path,
        depth: usize,
        inherited_scopes: &[ProjectScanScope],
    ) {
        (self.heartbeat)(self.visited);
        if self.visited >= self.entry_limit {
            self.bound_issue();
            return;
        }
        let names = match sorted_directory_names(directory.as_fd()) {
            Ok(names) => names,
            Err(_) => {
                self.record_all(RootIssue {
                    project_id: None,
                    code: "root_enumeration_denied".to_string(),
                    safe_message: "A project marker directory could not be read".to_string(),
                });
                return;
            }
        };
        let current_project = self.register_current_project(directory, relative, &names);
        let mut active_scopes = inherited_scopes.to_vec();
        active_scopes.extend(
            current_project
                .as_ref()
                .and_then(|project| project.scope.clone()),
        );
        if current_project.is_some() {
            self.index
                .project_scope_chains
                .insert(self.home.join(relative), active_scopes.clone());
        }
        for name in names {
            (self.heartbeat)(self.visited);
            if self.visited >= self.entry_limit {
                self.bound_issue();
                return;
            }
            self.visited += 1;
            // Checkpoint after consuming the entry and before filesystem work that
            // can block. The worker-side emitter throttles ordinary progress while
            // preserving exact bounded checkpoints.
            (self.heartbeat)(self.visited);
            let bytes = name.as_bytes();
            let name_os = std::ffi::OsString::from_vec(bytes.to_vec());
            if fixed_ignore(&name_os.to_string_lossy()) {
                continue;
            }
            let observed = match statat(directory.as_fd(), &name, AtFlags::SYMLINK_NOFOLLOW) {
                Ok(stat) => stat,
                Err(_) => {
                    self.record_all(RootIssue {
                        project_id: None,
                        code: "root_enumeration_changed".to_string(),
                        safe_message: "A project marker path changed during enumeration"
                            .to_string(),
                    });
                    continue;
                }
            };
            let file_type = FileType::from_raw_mode(observed.st_mode);
            let child_relative = relative.join(&name_os);
            let absolute_child = self.home.join(&child_relative);
            let mut ignored = false;
            for scope in active_scopes.iter().filter(|scope| {
                scope.matches_absolute_descendant(&absolute_child, file_type == FileType::Directory)
            }) {
                ignored = true;
                if let Ok(policy_relative) = absolute_child.strip_prefix(&scope.verified_root) {
                    self.index
                        .excluded_paths
                        .entry(scope.policy.project_id.clone())
                        .or_default()
                        .insert(policy_relative.to_path_buf());
                }
            }
            if ignored {
                continue;
            }
            if file_type == FileType::Symlink {
                continue;
            }
            if file_type != FileType::Directory || bytes.starts_with(b".") {
                continue;
            }
            if depth >= MAX_HOME_DEPTH {
                self.bound_issue();
                continue;
            }
            (self.heartbeat)(self.visited);
            match open_verified_directory(directory.as_fd(), &name, &observed) {
                Ok(child) => {
                    self.walk_marker_directory(&child, &child_relative, depth + 1, &active_scopes);
                }
                Err(Errno::LOOP) => self.record_all(RootIssue {
                    project_id: None,
                    code: "symlink_marker_rejected".to_string(),
                    safe_message: "A project marker directory symlink was rejected".to_string(),
                }),
                Err(_) => self.record_all(RootIssue {
                    project_id: None,
                    code: "root_enumeration_denied".to_string(),
                    safe_message: "A project marker directory could not be opened safely"
                        .to_string(),
                }),
            }
        }
    }

    fn register_current_project(
        &mut self,
        directory: &OwnedFd,
        relative: &Path,
        names: &[CString],
    ) -> Option<CurrentProjectRegistration> {
        if relative.as_os_str().is_empty() {
            return None;
        }
        let mut owners = BTreeSet::new();
        for name in names {
            let marker_owners = self.marker_owners(name.as_bytes());
            if marker_owners.is_empty() {
                continue;
            }
            match statat(directory.as_fd(), name, AtFlags::SYMLINK_NOFOLLOW) {
                Ok(stat) if FileType::from_raw_mode(stat.st_mode) != FileType::Symlink => {
                    owners.extend(marker_owners);
                }
                Ok(_) => self.index.record_issue(
                    marker_owners,
                    RootIssue {
                        project_id: None,
                        code: "symlink_marker_rejected".to_string(),
                        safe_message: "A project marker symlink was rejected".to_string(),
                    },
                ),
                Err(_) => self.index.record_issue(
                    marker_owners,
                    RootIssue {
                        project_id: None,
                        code: "root_enumeration_changed".to_string(),
                        safe_message: "A project marker changed during enumeration".to_string(),
                    },
                ),
            }
        }
        if owners.is_empty() {
            return None;
        }
        let parent = match fstat(directory) {
            Ok(parent) => parent,
            Err(_) => {
                self.index.record_issue(
                    owners,
                    RootIssue {
                        project_id: None,
                        code: "root_enumeration_changed".to_string(),
                        safe_message: "A project marker parent identity could not be verified"
                            .to_string(),
                    },
                );
                return None;
            }
        };
        let path = self.home.join(relative);
        let project_identity = project_id(&path);
        let owner_uid = parent.st_uid as u32;
        self.index
            .project_roots_by_id
            .insert(project_identity.clone(), path.clone());
        let scope = ProjectScanScope::from_verified_directory_with_source(
            &project_identity,
            &path,
            directory.as_fd(),
            parent.st_dev as u64,
            parent.st_ino,
            owner_uid,
        );
        let scope = match scope {
            Ok((scope, source)) => {
                self.index
                    .project_scopes
                    .insert(path.clone(), scope.clone());
                self.index
                    .project_scope_sources
                    .insert(path.clone(), source);
                Some(scope)
            }
            Err(error) => {
                self.index.record_issue(
                    owners.iter().copied(),
                    RootIssue {
                        project_id: Some(project_identity.clone()),
                        code: error.code.to_owned(),
                        safe_message: error.safe_message.to_owned(),
                    },
                );
                None
            }
        };
        self.index.owner_uids.insert(path.clone(), owner_uid);
        for enumerator in owners {
            self.index
                .candidates
                .entry(enumerator)
                .or_default()
                .entry(path.clone())
                .or_insert((parent.st_dev as u64, parent.st_ino));
        }
        Some(CurrentProjectRegistration { scope })
    }

    fn marker_owners(&self, name: &[u8]) -> Vec<RootEnumeratorId> {
        self.enumerators
            .iter()
            .copied()
            .filter(|enumerator| project_markers(*enumerator).contains(&name))
            .collect()
    }

    fn record_all(&mut self, issue: RootIssue) {
        self.index
            .record_issue(self.enumerators.iter().copied(), issue);
    }

    fn bound_issue(&mut self) {
        self.record_all(RootIssue {
            project_id: None,
            code: "root_enumeration_bound_reached".to_string(),
            safe_message: "Project marker enumeration reached its deterministic bound".to_string(),
        });
    }
}

fn project_markers(enumerator: RootEnumeratorId) -> &'static [&'static [u8]] {
    match enumerator {
        RootEnumeratorId::CodexHomeMarkersV1 => &[b".codex", b".agents", b"AGENTS.md"],
        RootEnumeratorId::ClaudeHomeMarkersV2 => &[b".claude", b"CLAUDE.md", b"CLAUDE.local.md"],
        RootEnumeratorId::AntigravityHomeMarkersV2 => &[b".agents", b"_agents", b".agent"],
        RootEnumeratorId::AntigravityCliHomeMarkersV2 => &[b".agents", b"GEMINI.md", b"AGENTS.md"],
        RootEnumeratorId::HermesExternalDirsV1 => &[],
    }
}

fn authorize_candidate(
    home: &Path,
    candidate: &RootCandidate,
    issues: &mut Vec<RootIssue>,
) -> Option<(PathBuf, u64, u64, u32)> {
    let canonical = match normalize_absolute_path(&candidate.path) {
        Ok(path) => path,
        Err(error) => {
            push_root_open_issue(issues, error);
            return None;
        }
    };
    let provenance_authorizes_external = matches!(
        candidate.provenance,
        RootProvenance::ClaudeConfigEnvironment | RootProvenance::HermesConfig
    );
    if !(canonical.starts_with(home) || provenance_authorizes_external) {
        return None;
    }
    if matches!(candidate.provenance, RootProvenance::WorkerVerified) {
        return candidate
            .expected_identity
            .map(|(device, inode, owner_uid)| (canonical, device, inode, owner_uid));
    }
    let (_fd, stat) = match open_absolute_directory_nofollow(&canonical) {
        Ok(opened) => opened,
        Err(RootOpenError::Missing) => return None,
        Err(error) => {
            push_root_open_issue(issues, error);
            return None;
        }
    };
    if candidate
        .expected_identity
        .is_some_and(|(device, inode, owner_uid)| {
            device != stat.st_dev as u64 || inode != stat.st_ino || owner_uid != stat.st_uid as u32
        })
    {
        issues.push(RootIssue {
            project_id: None,
            code: "root_identity_changed".to_string(),
            safe_message: "A discovered project root changed before authorization".to_string(),
        });
        return None;
    }
    Some((
        canonical,
        stat.st_dev as u64,
        stat.st_ino,
        stat.st_uid as u32,
    ))
}

#[derive(Debug, Clone, Copy)]
enum RootOpenError {
    Missing,
    Permission,
    SymlinkComponent,
    NonDirectory,
    Changed,
    Invalid,
    Unavailable,
}

fn normalize_absolute_path(path: &Path) -> Result<PathBuf, RootOpenError> {
    if !path.is_absolute() {
        return Err(RootOpenError::Invalid);
    }
    let mut normalized = PathBuf::from("/");
    for component in path.components() {
        match component {
            Component::RootDir => {}
            Component::CurDir => {}
            Component::ParentDir => {
                if normalized == Path::new("/") || !normalized.pop() {
                    return Err(RootOpenError::Invalid);
                }
            }
            Component::Normal(segment) => normalized.push(segment),
            Component::Prefix(_) => return Err(RootOpenError::Invalid),
        }
    }
    Ok(normalized)
}

fn open_absolute_directory_nofollow(path: &Path) -> Result<(OwnedFd, Stat), RootOpenError> {
    let mut directory = open(
        "/",
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| RootOpenError::Unavailable)?;
    for component in path.components() {
        let Component::Normal(segment) = component else {
            continue;
        };
        let name = CString::new(segment.as_bytes()).map_err(|_| RootOpenError::Invalid)?;
        let observed =
            statat(directory.as_fd(), &name, AtFlags::SYMLINK_NOFOLLOW).map_err(map_root_errno)?;
        match FileType::from_raw_mode(observed.st_mode) {
            FileType::Symlink => return Err(RootOpenError::SymlinkComponent),
            FileType::Directory => {}
            _ => return Err(RootOpenError::NonDirectory),
        }
        directory =
            open_verified_directory(directory.as_fd(), &name, &observed).map_err(map_root_errno)?;
    }
    let stat = fstat(&directory).map_err(|_| RootOpenError::Unavailable)?;
    if FileType::from_raw_mode(stat.st_mode) != FileType::Directory {
        return Err(RootOpenError::NonDirectory);
    }
    Ok((directory, stat))
}

fn map_root_errno(error: Errno) -> RootOpenError {
    match error {
        Errno::NOENT => RootOpenError::Missing,
        Errno::ACCESS | Errno::PERM => RootOpenError::Permission,
        Errno::LOOP => RootOpenError::SymlinkComponent,
        Errno::NOTDIR => RootOpenError::NonDirectory,
        Errno::INVAL | Errno::NAMETOOLONG => RootOpenError::Invalid,
        _ => RootOpenError::Changed,
    }
}

fn push_root_open_issue(issues: &mut Vec<RootIssue>, error: RootOpenError) {
    let (code, safe_message) = match error {
        RootOpenError::Missing | RootOpenError::Changed => (
            "root_changed_during_authorization",
            "A discovery root changed during authorization",
        ),
        RootOpenError::Permission => (
            "root_permission_denied",
            "A declared discovery root could not be opened with read-only permission",
        ),
        RootOpenError::SymlinkComponent => (
            "symlink_root_component_rejected",
            "A symlink component in a discovery root was rejected",
        ),
        RootOpenError::NonDirectory => (
            "non_directory_root_rejected",
            "A discovery root component was not a directory",
        ),
        RootOpenError::Invalid => (
            "invalid_root_path_rejected",
            "A discovery root path was invalid",
        ),
        RootOpenError::Unavailable => (
            "root_identity_unavailable",
            "A discovery root identity could not be verified",
        ),
    };
    issues.push(RootIssue {
        project_id: None,
        code: code.to_string(),
        safe_message: safe_message.to_string(),
    });
}

fn hermes_external_candidates(home: &Path, issues: &mut Vec<RootIssue>) -> Vec<RootCandidate> {
    let mut candidates = BTreeSet::new();
    for config in secure_hermes_configs(home, issues) {
        let value = match serde_yaml::from_slice::<YamlValue>(&config.bytes) {
            Ok(value) => value,
            Err(_) => {
                issues.push(RootIssue {
                    project_id: None,
                    code: "hermes_external_dirs_malformed".to_string(),
                    safe_message: "Hermes external skill roots could not be parsed".to_string(),
                });
                continue;
            }
        };
        let Some(external_dirs) = value
            .get("skills")
            .and_then(|skills| skills.get("external_dirs"))
            .and_then(YamlValue::as_sequence)
        else {
            continue;
        };
        for external_dir in external_dirs {
            let Some(external_dir) = external_dir.as_str() else {
                issues.push(RootIssue {
                    project_id: None,
                    code: "hermes_external_dir_rejected".to_string(),
                    safe_message: "A Hermes external skill root was not a path string".to_string(),
                });
                continue;
            };
            if external_dir.as_bytes().contains(&0) {
                issues.push(RootIssue {
                    project_id: None,
                    code: "hermes_external_dir_rejected".to_string(),
                    safe_message: "A Hermes external skill root was invalid".to_string(),
                });
                continue;
            }
            let path = PathBuf::from(external_dir);
            let resolved = if path.is_absolute() {
                path
            } else {
                config.base.join(path)
            };
            candidates.insert(resolved);
        }
    }
    candidates
        .into_iter()
        .map(|path| RootCandidate {
            path,
            provenance: RootProvenance::HermesConfig,
            expected_identity: None,
            project_scan_scopes: Vec::new(),
        })
        .collect()
}

const MAX_ROOT_CONFIG_BYTES: usize = 1024 * 1024;

struct ConfigSource {
    base: PathBuf,
    bytes: Vec<u8>,
}

#[derive(Debug, Clone, Copy)]
enum ConfigReadError {
    Symlink,
    NonRegular,
    TooLarge,
    Changed,
    Unreadable,
}

fn secure_hermes_configs(home: &Path, issues: &mut Vec<RootIssue>) -> Vec<ConfigSource> {
    let mut configs = Vec::new();
    let home_fd = match open(
        home,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    ) {
        Ok(fd) => fd,
        Err(_) => {
            push_config_issue(issues, ConfigReadError::Unreadable);
            return configs;
        }
    };
    let hermes_name = CString::new(".hermes").expect("static path segment");
    let Some(hermes_fd) = open_optional_directory(home_fd.as_fd(), &hermes_name, issues) else {
        return configs;
    };
    let hermes = home.join(".hermes");
    let config_name = CString::new("config.yaml").expect("static path segment");
    match read_optional_config(hermes_fd.as_fd(), &config_name) {
        Ok(Some(bytes)) => configs.push(ConfigSource {
            base: hermes.clone(),
            bytes,
        }),
        Ok(None) => {}
        Err(error) => push_config_issue(issues, error),
    }

    let profiles_name = CString::new("profiles").expect("static path segment");
    let Some(profiles_fd) = open_optional_directory(hermes_fd.as_fd(), &profiles_name, issues)
    else {
        return configs;
    };
    let names = match sorted_directory_names(profiles_fd.as_fd()) {
        Ok(names) => names,
        Err(_) => {
            push_config_issue(issues, ConfigReadError::Unreadable);
            return configs;
        }
    };
    for name in names {
        let observed = match statat(profiles_fd.as_fd(), &name, AtFlags::SYMLINK_NOFOLLOW) {
            Ok(stat) => stat,
            Err(_) => {
                push_config_issue(issues, ConfigReadError::Changed);
                continue;
            }
        };
        match FileType::from_raw_mode(observed.st_mode) {
            FileType::Symlink => {
                push_config_issue(issues, ConfigReadError::Symlink);
                continue;
            }
            FileType::Directory => {}
            _ => continue,
        }
        let profile_fd = match open_verified_directory(profiles_fd.as_fd(), &name, &observed) {
            Ok(fd) => fd,
            Err(Errno::LOOP) => {
                push_config_issue(issues, ConfigReadError::Symlink);
                continue;
            }
            Err(_) => {
                push_config_issue(issues, ConfigReadError::Changed);
                continue;
            }
        };
        match read_optional_config(profile_fd.as_fd(), &config_name) {
            Ok(Some(bytes)) => configs.push(ConfigSource {
                base: hermes
                    .join("profiles")
                    .join(std::ffi::OsString::from_vec(name.as_bytes().to_vec())),
                bytes,
            }),
            Ok(None) => {}
            Err(error) => push_config_issue(issues, error),
        }
    }
    configs
}

fn open_optional_directory(
    parent: BorrowedFd<'_>,
    name: &CStr,
    issues: &mut Vec<RootIssue>,
) -> Option<OwnedFd> {
    let observed = match statat(parent, name, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(stat) => stat,
        Err(Errno::NOENT) => return None,
        Err(_) => {
            push_config_issue(issues, ConfigReadError::Unreadable);
            return None;
        }
    };
    match FileType::from_raw_mode(observed.st_mode) {
        FileType::Symlink => {
            push_config_issue(issues, ConfigReadError::Symlink);
            None
        }
        FileType::Directory => match open_verified_directory(parent, name, &observed) {
            Ok(fd) => Some(fd),
            Err(Errno::LOOP) => {
                push_config_issue(issues, ConfigReadError::Symlink);
                None
            }
            Err(_) => {
                push_config_issue(issues, ConfigReadError::Changed);
                None
            }
        },
        _ => {
            push_config_issue(issues, ConfigReadError::NonRegular);
            None
        }
    }
}

fn open_verified_directory(
    parent: BorrowedFd<'_>,
    name: &CStr,
    observed: &Stat,
) -> Result<OwnedFd, Errno> {
    let fd = openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )?;
    let opened = fstat(&fd)?;
    if FileType::from_raw_mode(opened.st_mode) != FileType::Directory
        || !same_stat_identity(observed, &opened)
    {
        return Err(Errno::NOTDIR);
    }
    Ok(fd)
}

fn read_optional_config(
    parent: BorrowedFd<'_>,
    name: &CStr,
) -> Result<Option<Vec<u8>>, ConfigReadError> {
    let observed = match statat(parent, name, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(stat) => stat,
        Err(Errno::NOENT) => return Ok(None),
        Err(_) => return Err(ConfigReadError::Unreadable),
    };
    match FileType::from_raw_mode(observed.st_mode) {
        FileType::Symlink => return Err(ConfigReadError::Symlink),
        FileType::RegularFile => {}
        _ => return Err(ConfigReadError::NonRegular),
    }
    let fd = openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|error| {
        if error == Errno::LOOP {
            ConfigReadError::Symlink
        } else {
            ConfigReadError::Unreadable
        }
    })?;
    let before = fstat(&fd).map_err(|_| ConfigReadError::Unreadable)?;
    if FileType::from_raw_mode(before.st_mode) != FileType::RegularFile
        || !same_stat_identity(&observed, &before)
    {
        return Err(ConfigReadError::Changed);
    }
    let size = usize::try_from(before.st_size).map_err(|_| ConfigReadError::Changed)?;
    if size > MAX_ROOT_CONFIG_BYTES {
        return Err(ConfigReadError::TooLarge);
    }
    let mut bytes = Vec::with_capacity(size);
    let mut scratch = [0_u8; 16 * 1024];
    let mut offset = 0_u64;
    loop {
        let remaining = MAX_ROOT_CONFIG_BYTES + 1 - bytes.len();
        let wanted = remaining.min(scratch.len());
        let read = loop {
            match pread(&fd, &mut scratch[..wanted], offset) {
                Err(Errno::INTR) => continue,
                Err(_) => return Err(ConfigReadError::Unreadable),
                Ok(read) => break read,
            }
        };
        if read == 0 {
            break;
        }
        bytes.extend_from_slice(&scratch[..read]);
        offset += read as u64;
        if bytes.len() > MAX_ROOT_CONFIG_BYTES {
            return Err(ConfigReadError::TooLarge);
        }
    }
    let after = fstat(&fd).map_err(|_| ConfigReadError::Unreadable)?;
    if !stable_stat(&before, &after) || usize::try_from(after.st_size).ok() != Some(bytes.len()) {
        return Err(ConfigReadError::Changed);
    }
    Ok(Some(bytes))
}

fn sorted_directory_names(directory: BorrowedFd<'_>) -> Result<Vec<CString>, Errno> {
    let mut stream = Dir::read_from(directory)?;
    let mut names = Vec::new();
    while let Some(entry) = stream.read() {
        let entry = entry?;
        let name = entry.file_name();
        if name.to_bytes() == b"." || name.to_bytes() == b".." {
            continue;
        }
        if name.to_bytes().is_empty() || name.to_bytes().contains(&b'/') {
            return Err(Errno::INVAL);
        }
        names.push(name.to_owned());
    }
    names.sort_unstable_by(|left, right| left.as_bytes().cmp(right.as_bytes()));
    Ok(names)
}

fn same_stat_identity(left: &Stat, right: &Stat) -> bool {
    left.st_dev == right.st_dev
        && left.st_ino == right.st_ino
        && FileType::from_raw_mode(left.st_mode) == FileType::from_raw_mode(right.st_mode)
}

fn stable_stat(left: &Stat, right: &Stat) -> bool {
    same_stat_identity(left, right)
        && left.st_mode == right.st_mode
        && left.st_nlink == right.st_nlink
        && left.st_size == right.st_size
        && left.st_mtime == right.st_mtime
        && left.st_mtime_nsec == right.st_mtime_nsec
        && left.st_ctime == right.st_ctime
        && left.st_ctime_nsec == right.st_ctime_nsec
}

fn push_config_issue(issues: &mut Vec<RootIssue>, error: ConfigReadError) {
    let (code, safe_message) = match error {
        ConfigReadError::Symlink => (
            "config_provenance_symlink_rejected",
            "A Hermes root-authority config symlink was rejected",
        ),
        ConfigReadError::NonRegular => (
            "config_provenance_non_regular_rejected",
            "A Hermes root-authority config was not a regular file",
        ),
        ConfigReadError::TooLarge => (
            "config_provenance_size_limit_exceeded",
            "A Hermes root-authority config exceeded its deterministic size bound",
        ),
        ConfigReadError::Changed => (
            "config_provenance_changed",
            "A Hermes root-authority config changed while it was being read",
        ),
        ConfigReadError::Unreadable => (
            "config_provenance_unreadable",
            "A Hermes root-authority config could not be read safely",
        ),
    };
    issues.push(RootIssue {
        project_id: None,
        code: code.to_string(),
        safe_message: safe_message.to_string(),
    });
}

fn fixed_ignore(name: &str) -> bool {
    matches!(
        name,
        "Library"
            | ".Trash"
            | ".git"
            | ".hg"
            | ".svn"
            | "node_modules"
            | "vendor"
            | "target"
            | "dist"
            | "build"
            | ".venv"
            | "__pycache__"
            | "cache"
            | "caches"
    )
}

fn root_id(spec: &RootSpec, project_id: Option<&str>, root: &Path) -> String {
    digest(&[
        spec.adapter_id.as_bytes(),
        spec.adapter_version.as_bytes(),
        spec.tool_id.as_bytes(),
        spec.surface_id.as_bytes(),
        match spec.scope {
            Scope::User => b"user",
            Scope::Project => b"project",
        },
        project_id.unwrap_or("").as_bytes(),
        path_bytes(root),
    ])
}

fn project_id(root: &Path) -> String {
    let mut digest = Sha256::new();
    digest.update(path_bytes(root));
    hex_digest(digest.finalize())
}

fn digest(parts: &[&[u8]]) -> String {
    let mut digest = Sha256::new();
    for part in parts {
        digest.update((*part).len().to_be_bytes());
        digest.update(part);
    }
    hex_digest(digest.finalize())
}

fn hex_digest(bytes: impl AsRef<[u8]>) -> String {
    bytes
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn path_bytes(value: &Path) -> &[u8] {
    value.as_os_str().as_encoded_bytes()
}

#[cfg(test)]
mod project_identity_tests {
    use std::sync::{Arc, Mutex, OnceLock};

    use super::*;
    use tempfile::tempdir;

    struct TimeoutOneChildSupervisor;

    struct CapacityOneChildSupervisor;

    static MARKER_WORKER_CAPACITY_TEST_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

    struct EntryBudgetSupervisor {
        visited_entries: usize,
        entry_limits: Vec<usize>,
    }

    impl MarkerWalkSubtreeRunner for TimeoutOneChildSupervisor {
        fn walk_subtree(
            &mut self,
            home_fd: BorrowedFd<'_>,
            name: &CStr,
            enumerators: &BTreeSet<RootEnumeratorId>,
            entry_limit: usize,
        ) -> Result<MarkerWalkSubtreeOutcome, MarkerWalkSupervisorError> {
            if name.to_bytes() == b"a-blocked" {
                return Ok(MarkerWalkSubtreeOutcome::DeadlineExceeded { visited_entries: 0 });
            }
            match run_marker_subtree(home_fd, name, enumerators, entry_limit, &mut |_| {}) {
                Ok(report) => Ok(MarkerWalkSubtreeOutcome::Completed(report)),
                Err(error) => Ok(MarkerWalkSubtreeOutcome::Failed {
                    visited_entries: error.visited_entries,
                }),
            }
        }
    }

    impl MarkerWalkSubtreeRunner for CapacityOneChildSupervisor {
        fn walk_subtree(
            &mut self,
            home_fd: BorrowedFd<'_>,
            name: &CStr,
            enumerators: &BTreeSet<RootEnumeratorId>,
            entry_limit: usize,
        ) -> Result<MarkerWalkSubtreeOutcome, MarkerWalkSupervisorError> {
            if name.to_bytes() == b"a-capacity" {
                return Ok(MarkerWalkSubtreeOutcome::CapacityExceeded);
            }
            match run_marker_subtree(home_fd, name, enumerators, entry_limit, &mut |_| {}) {
                Ok(report) => Ok(MarkerWalkSubtreeOutcome::Completed(report)),
                Err(error) => Ok(MarkerWalkSubtreeOutcome::Failed {
                    visited_entries: error.visited_entries,
                }),
            }
        }
    }

    impl MarkerWalkSubtreeRunner for EntryBudgetSupervisor {
        fn walk_subtree(
            &mut self,
            _home_fd: BorrowedFd<'_>,
            _name: &CStr,
            _enumerators: &BTreeSet<RootEnumeratorId>,
            entry_limit: usize,
        ) -> Result<MarkerWalkSubtreeOutcome, MarkerWalkSupervisorError> {
            self.entry_limits.push(entry_limit);
            Ok(MarkerWalkSubtreeOutcome::Completed(MarkerWorkerReport {
                visited_entries: u64::try_from(self.visited_entries).expect("bounded test count"),
                policies: Vec::new(),
                candidates: Vec::new(),
                excluded_paths: Vec::new(),
                issues_by_enumerator: BTreeMap::new(),
            }))
        }
    }

    #[test]
    fn marker_subtree_deadline_preserves_healthy_claude_marker_discovery() {
        let home = tempdir().expect("temporary HOME");
        std::fs::create_dir_all(home.path().join("a-blocked/.claude"))
            .expect("blocked fixture child");
        let healthy = home.path().join("b-healthy");
        std::fs::create_dir_all(healthy.join(".claude")).expect("healthy Claude marker");
        let enumerators = BTreeSet::from([
            RootEnumeratorId::CodexHomeMarkersV1,
            RootEnumeratorId::ClaudeHomeMarkersV2,
        ]);
        let mut supervisor = TimeoutOneChildSupervisor;

        let index = ProjectMarkerIndex::discover_with_supervisor(
            home.path(),
            &enumerators,
            &mut supervisor,
        )
        .expect("deadline is a partial marker result, not a scan failure");

        assert!(index
            .issue_codes_for(RootEnumeratorId::ClaudeHomeMarkersV2)
            .contains("marker_subtree_deadline_exceeded"));
        assert!(!index
            .issues
            .iter()
            .any(|issue| issue.code == "marker_subtree_deadline_exceeded"));
        assert_eq!(index.pre_root_skips.len(), 1);
        let skip = &index.pre_root_skips[0];
        assert_eq!(skip.reason_code, "marker_subtree_deadline_exceeded");
        assert!(!skip.safe_relative_locator.contains("a-blocked"));
        assert!(!skip
            .safe_relative_locator
            .contains(home.path().to_string_lossy().as_ref()));
        let discovered = index
            .candidates_for(RootEnumeratorId::ClaudeHomeMarkersV2)
            .into_iter()
            .map(|candidate| candidate.path)
            .collect::<BTreeSet<_>>();
        assert_eq!(discovered, BTreeSet::from([healthy]));

        let mut responsive_supervisor = MarkerWalkSubtreeSupervisor::InProcess;
        let retry = ProjectMarkerIndex::discover_with_supervisor(
            home.path(),
            &enumerators,
            &mut responsive_supervisor,
        )
        .expect("a later scan must retry the previously timed-out child");
        assert!(retry
            .candidates_for(RootEnumeratorId::ClaudeHomeMarkersV2)
            .into_iter()
            .any(|candidate| candidate.path == home.path().join("a-blocked")));
    }

    #[test]
    fn sibling_workers_share_the_original_global_entry_budget() {
        let home = tempdir().expect("temporary HOME");
        std::fs::create_dir(home.path().join("a-first")).expect("first child");
        std::fs::create_dir(home.path().join("b-second")).expect("second child");
        let enumerators = BTreeSet::from([RootEnumeratorId::ClaudeHomeMarkersV2]);
        let consumed_per_worker = 17;
        let mut supervisor = EntryBudgetSupervisor {
            visited_entries: consumed_per_worker,
            entry_limits: Vec::new(),
        };

        ProjectMarkerIndex::discover_with_supervisor(home.path(), &enumerators, &mut supervisor)
            .expect("worker reports are valid");

        assert_eq!(
            supervisor.entry_limits,
            vec![
                MAX_VISITED_ENTRIES - 1,
                MAX_VISITED_ENTRIES - 1 - consumed_per_worker - 1,
            ]
        );
    }

    #[test]
    fn exhausted_reaper_capacity_keeps_later_healthy_claude_discovery() {
        let home = tempdir().expect("temporary HOME");
        std::fs::create_dir_all(home.path().join("a-capacity")).expect("capacity fixture child");
        let healthy = home.path().join("b-healthy");
        std::fs::create_dir_all(healthy.join(".claude")).expect("healthy Claude marker");
        let enumerators = BTreeSet::from([RootEnumeratorId::ClaudeHomeMarkersV2]);
        let mut supervisor = CapacityOneChildSupervisor;

        let index = ProjectMarkerIndex::discover_with_supervisor(
            home.path(),
            &enumerators,
            &mut supervisor,
        )
        .expect("capacity exhaustion is a partial marker result");

        assert!(index
            .issue_codes_for(RootEnumeratorId::ClaudeHomeMarkersV2)
            .contains("marker_subtree_reaper_capacity_exhausted"));
        assert!(index
            .pre_root_skips
            .iter()
            .any(|skip| skip.reason_code == "marker_subtree_reaper_capacity_exhausted"));
        assert!(index
            .candidates_for(RootEnumeratorId::ClaudeHomeMarkersV2)
            .into_iter()
            .any(|candidate| candidate.path == healthy));
    }

    #[test]
    fn worker_report_cannot_escape_its_dispatched_home_child() {
        let home = tempdir().expect("temporary HOME");
        let dispatched_child = CString::new("child").expect("static child name");
        let enumerators = BTreeSet::from([RootEnumeratorId::ClaudeHomeMarkersV2]);
        let report = MarkerWorkerReport {
            visited_entries: 0,
            policies: Vec::new(),
            candidates: vec![MarkerWorkerCandidate {
                components: vec![b"other".to_vec(), b".claude".to_vec()],
                device: 1,
                inode: 1,
                owner_uid: 1,
                owners: vec![RootEnumeratorId::ClaudeHomeMarkersV2],
                scope_ids: Vec::new(),
            }],
            excluded_paths: Vec::new(),
            issues_by_enumerator: BTreeMap::new(),
        };

        assert!(ProjectMarkerIndex::from_worker_report(
            home.path(),
            dispatched_child.as_c_str(),
            &enumerators,
            report,
        )
        .is_err());
    }

    #[test]
    fn terminal_count_behind_heartbeat_is_a_protocol_failure() {
        let terminal = MarkerWorkerTerminal::Failed { visited_entries: 4 };
        let worker_progress = AtomicU64::new(5);

        assert!(matches!(
            marker_worker_terminal_outcome(Some(terminal), &worker_progress, 6),
            MarkerWalkSubtreeOutcome::ProtocolFailed { visited_entries: 6 }
        ));
    }

    #[test]
    fn queued_worker_progress_at_the_deadline_resets_inactivity() {
        let (event_tx, event_rx) = mpsc::channel();
        event_tx
            .send(MarkerWorkerPipeEvent::Heartbeat)
            .expect("receiver remains available");
        let mut terminal = None;

        let drained = drain_marker_worker_events(&event_rx, &mut terminal);

        assert!(drained.activity_seen);
        assert!(!drained.protocol_failed);
        assert!(terminal.is_none());
    }

    #[test]
    fn only_the_requested_sigkill_is_a_deadline_exit() {
        use std::os::unix::process::ExitStatusExt as _;

        let sigkill = std::process::ExitStatus::from_raw(Signal::KILL.as_raw());
        let worker_panic = std::process::ExitStatus::from_raw(101_i32 << 8);

        assert!(worker_was_killed_at_deadline(true, sigkill));
        assert!(!worker_was_killed_at_deadline(true, worker_panic));
        assert!(!worker_was_killed_at_deadline(false, sigkill));
    }

    #[test]
    fn bounded_reaper_capacity_recovers_after_reaping_a_worker() {
        let capacity_lock = MARKER_WORKER_CAPACITY_TEST_LOCK
            .get_or_init(|| Mutex::new(()))
            .lock()
            .expect("capacity test lock");
        assert_eq!(
            MARKER_WORKER_OUTSTANDING.load(Ordering::Acquire),
            0,
            "tests must not overlap a process worker"
        );

        let child = Command::new("/bin/sh")
            .args(["-c", "exit 0"])
            .spawn()
            .expect("short-lived worker fixture");
        let permit = MarkerWorkerCapacityPermit::try_acquire().expect("first permit");
        reap_marker_worker(child, None, permit);

        let deadline = Instant::now() + Duration::from_secs(2);
        while MARKER_WORKER_OUTSTANDING.load(Ordering::Acquire) != 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(
            MARKER_WORKER_OUTSTANDING.load(Ordering::Acquire),
            0,
            "reaped worker must release its capacity permit"
        );

        let mut permits = (0..MAX_OUTSTANDING_MARKER_WORKERS)
            .map(|_| MarkerWorkerCapacityPermit::try_acquire().expect("bounded permit"))
            .collect::<Vec<_>>();
        assert!(
            MarkerWorkerCapacityPermit::try_acquire().is_none(),
            "the fifth worker must not be spawned while all four permits are outstanding"
        );
        drop(permits.pop().expect("one permit to release"));
        let retry =
            MarkerWorkerCapacityPermit::try_acquire().expect("capacity recovers after release");
        drop(retry);
        drop(permits);
        assert_eq!(MARKER_WORKER_OUTSTANDING.load(Ordering::Acquire), 0);
        drop(capacity_lock);
    }

    #[test]
    fn marker_worker_terminal_serialization_stops_at_the_buffer_limit() {
        let terminal = MarkerWorkerTerminal::Completed(MarkerWorkerReport {
            visited_entries: 0,
            policies: vec![MarkerWorkerPolicy {
                id: 0,
                root_components: vec![b"fixture".to_vec()],
                device: 1,
                inode: 1,
                owner_uid: 1,
                exact_text: "x".repeat(256),
                missing: false,
            }],
            candidates: Vec::new(),
            excluded_paths: Vec::new(),
            issues_by_enumerator: BTreeMap::new(),
        });
        let mut buffer = BoundedMarkerWorkerBuffer::new(64);

        assert!(serde_json::to_writer(&mut buffer, &terminal).is_err());
        assert!(buffer.into_inner().len() <= 64);
    }

    #[test]
    fn one_canonical_folder_is_one_project_across_ai_tools() {
        let root = Path::new("/fixture-home/Projects/routine-harness");

        assert_eq!(project_id(root), project_id(root));
        assert_ne!(
            project_id(root),
            project_id(Path::new("/fixture-home/Archive/routine-harness"))
        );
    }

    #[test]
    fn project_ignore_excludes_descendant_marker_root_but_not_its_owner() {
        let home = tempdir().expect("temporary HOME");
        let owner = home.path().join("owner");
        let ignored_child = owner.join("generated/child");
        std::fs::create_dir_all(owner.join(".codex")).expect("owner marker");
        std::fs::create_dir_all(ignored_child.join(".codex")).expect("child marker");
        std::fs::write(owner.join(".harnesskitignore"), "generated/\n").expect("project ignore");
        let owner = std::fs::canonicalize(owner).expect("canonical owner");
        let ignored_child = std::fs::canonicalize(ignored_child).expect("canonical child");
        let spec = RootSpec {
            adapter_id: "codex".to_owned(),
            adapter_version: "1".to_owned(),
            tool_id: "codex".to_owned(),
            surface_id: "project".to_owned(),
            scope: Scope::Project,
            enumerator_id: RootEnumeratorId::CodexHomeMarkersV1,
        };

        let registry = AuthorizedRootRegistry::discover(home.path(), &[spec]).expect("registry");
        let roots = registry
            .roots
            .iter()
            .map(|root| root.canonical_root.as_path())
            .collect::<BTreeSet<_>>();

        assert!(
            roots.contains(owner.as_path()),
            "owner root must remain editable"
        );
        assert!(
            !roots.contains(ignored_child.as_path()),
            "ignored descendant marker must not become an authorized root"
        );
    }

    #[test]
    fn ancestor_project_ignore_remains_active_below_an_intervening_project_marker() {
        let home = tempdir().expect("temporary HOME");
        let owner = home.path().join("owner");
        let nested = owner.join("nested");
        let ignored_grandchild = nested.join("generated/child");
        std::fs::create_dir_all(owner.join(".codex")).expect("owner marker");
        std::fs::create_dir_all(nested.join(".codex")).expect("nested marker");
        std::fs::create_dir_all(ignored_grandchild.join(".codex")).expect("grandchild marker");
        std::fs::create_dir_all(nested.join("other/child/.codex")).expect("nested ignored marker");
        std::fs::write(owner.join(".harnesskitignore"), "nested/generated/\n")
            .expect("owner ignore source");
        std::fs::write(nested.join(".harnesskitignore"), "other/\n").expect("nested ignore source");
        let owner = std::fs::canonicalize(owner).expect("canonical owner");
        let nested = std::fs::canonicalize(nested).expect("canonical nested");
        let ignored_grandchild =
            std::fs::canonicalize(ignored_grandchild).expect("canonical grandchild");
        let spec = RootSpec {
            adapter_id: "codex".to_owned(),
            adapter_version: "1".to_owned(),
            tool_id: "codex".to_owned(),
            surface_id: "project".to_owned(),
            scope: Scope::Project,
            enumerator_id: RootEnumeratorId::CodexHomeMarkersV1,
        };

        let registry = AuthorizedRootRegistry::discover(home.path(), &[spec]).expect("registry");

        assert!(registry
            .roots
            .iter()
            .all(|root| root.canonical_root != ignored_grandchild));
        let owner_root = registry
            .roots
            .iter()
            .find(|root| root.canonical_root == owner)
            .expect("owner root");
        let nested_root = registry
            .roots
            .iter()
            .find(|root| root.canonical_root == nested)
            .expect("nested root");
        assert_eq!(owner_root.project_scan_scopes.len(), 1);
        assert_eq!(nested_root.project_scan_scopes.len(), 2);
        assert!(Arc::ptr_eq(
            &owner_root.project_scan_scopes[0].policy,
            &nested_root.project_scan_scopes[0].policy,
        ));
        assert_eq!(
            owner_root.project_scan_scopes[0].policy_revision,
            nested_root.project_scan_scopes[0].policy_revision,
        );
        assert_eq!(
            registry.project_ignored_paths,
            BTreeMap::from([
                (
                    project_id(&owner),
                    BTreeSet::from([PathBuf::from("nested/generated")]),
                ),
                (
                    project_id(&nested),
                    BTreeSet::from([PathBuf::from("other")]),
                ),
            ])
        );
    }

    #[test]
    fn project_surfaces_share_the_marker_index_policy_arc() {
        let home = tempdir().expect("temporary HOME");
        let owner = home.path().join("owner");
        std::fs::create_dir_all(owner.join(".codex")).expect("owner marker");
        std::fs::write(owner.join(".harnesskitignore"), "generated/\n").expect("project ignore");
        let specs = ["project-agents", "project-prompts"].map(|surface_id| RootSpec {
            adapter_id: "codex".to_owned(),
            adapter_version: "1".to_owned(),
            tool_id: "codex".to_owned(),
            surface_id: surface_id.to_owned(),
            scope: Scope::Project,
            enumerator_id: RootEnumeratorId::CodexHomeMarkersV1,
        });

        let registry = AuthorizedRootRegistry::discover(home.path(), &specs).expect("registry");
        let scopes = registry
            .roots
            .iter()
            .filter_map(|root| root.project_scan_scopes.last())
            .collect::<Vec<_>>();

        assert_eq!(scopes.len(), 2);
        assert!(Arc::ptr_eq(&scopes[0].policy, &scopes[1].policy));
        assert_eq!(scopes[0].policy_revision, scopes[1].policy_revision);
    }

    #[test]
    fn invalid_and_symlink_sources_publish_safe_project_issues() {
        use std::os::unix::fs::symlink;

        let home = tempdir().expect("temporary HOME");
        let invalid_owner = home.path().join("invalid-owner");
        let symlink_owner = home.path().join("symlink-owner");
        std::fs::create_dir_all(invalid_owner.join(".codex")).expect("invalid marker");
        std::fs::create_dir_all(symlink_owner.join(".codex")).expect("symlink marker");
        std::fs::write(invalid_owner.join(".harnesskitignore"), "../outside\n")
            .expect("invalid source");
        let external = home.path().join("external-ignore");
        std::fs::write(&external, "generated/\n").expect("external source");
        symlink(&external, symlink_owner.join(".harnesskitignore")).expect("ignore symlink");
        let spec = RootSpec {
            adapter_id: "codex".to_owned(),
            adapter_version: "1".to_owned(),
            tool_id: "codex".to_owned(),
            surface_id: "project".to_owned(),
            scope: Scope::Project,
            enumerator_id: RootEnumeratorId::CodexHomeMarkersV1,
        };

        let registry = AuthorizedRootRegistry::discover(home.path(), &[spec]).expect("registry");
        let codes = registry
            .issues
            .iter()
            .map(|issue| issue.code.as_str())
            .collect::<BTreeSet<_>>();

        assert!(codes.contains("project_ignore_invalid"));
        assert!(codes.contains("project_ignore_unreadable"));
        assert_eq!(
            registry.roots.len(),
            2,
            "owners remain available for repair"
        );
        let project_ids = registry
            .roots
            .iter()
            .filter_map(|root| root.project_id.as_deref())
            .collect::<BTreeSet<_>>();
        assert!(registry
            .issues
            .iter()
            .filter(|issue| {
                matches!(
                    issue.code.as_str(),
                    "project_ignore_invalid" | "project_ignore_unreadable"
                )
            })
            .all(|issue| issue
                .project_id
                .as_deref()
                .is_some_and(|project_id| project_ids.contains(project_id))));
        assert!(registry
            .roots
            .iter()
            .all(|root| root.project_scan_scopes.is_empty()));
        assert!(registry.issues.iter().all(|issue| {
            !issue
                .safe_message
                .contains(home.path().to_string_lossy().as_ref())
        }));
    }
}
