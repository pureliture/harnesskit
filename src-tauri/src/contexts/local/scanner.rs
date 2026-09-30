//! Deterministic read-only Local filesystem scanner.

use std::collections::{BTreeMap, BTreeSet};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::path::{Component, Path};

use rustix::fd::{AsFd, BorrowedFd, OwnedFd};
use rustix::ffi::{CStr, CString};
use rustix::fs::{fstat, open, openat, statat, AtFlags, Dir, FileType, Mode, OFlags, Stat};
use rustix::io::{pread, Errno};
use sha2::{Digest, Sha256};

use crate::projection::{
    normalized_relative_locator, root_identity as destination_root_identity, DestinationKey,
    DestinationScope,
};

use super::adapter::{ParserId, SurfaceKind, ToolId};
use super::catalog::CatalogAdapter;
use super::context::LocalScanProgress;
use super::domain::{
    CoverageRecord, CoverageStatus, FileIdentity, FileIdentityType, InstanceHandle, LocalInstance,
    LocalScanResult, ParseState, ProjectIgnoreSummary, ProjectLocationRecord, SafeIssue, Scope,
    SkippedPath, StableIdentity, SurfacePresence,
};
use super::parser::parse_document;
use super::project_ignore::ProjectScanScope;
use super::roots::{AuthorizedRoot, AuthorizedRootRegistry, RootSpec};

pub struct LocalScanner;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LocalScanOutput {
    pub result: LocalScanResult,
    pub instance_handles: BTreeMap<String, InstanceHandle>,
    pub project_locations: BTreeMap<String, ProjectLocationRecord>,
}

const MAX_SCAN_DEPTH: usize = 12;
const MAX_SCAN_ENTRIES: usize = 500_000;
const MAX_HARNESS_FILE_BYTES: u64 = 4 * 1024 * 1024;

impl LocalScanner {
    pub fn scan(home: &Path, adapters: &[CatalogAdapter]) -> Result<LocalScanResult, String> {
        Self::scan_with_handles(home, adapters).map(|output| output.result)
    }

    pub fn scan_with_handles(
        home: &Path,
        adapters: &[CatalogAdapter],
    ) -> Result<LocalScanOutput, String> {
        let mut ignore_progress = |_progress: &LocalScanProgress| {};
        Self::scan_with_handles_and_progress(home, adapters, &mut ignore_progress)
    }

    pub fn scan_with_handles_and_progress(
        home: &Path,
        adapters: &[CatalogAdapter],
        progress: &mut dyn FnMut(&LocalScanProgress),
    ) -> Result<LocalScanOutput, String> {
        let specs = adapters
            .iter()
            .flat_map(|adapter| {
                adapter.descriptor.surfaces.iter().map(|surface| RootSpec {
                    adapter_id: adapter.descriptor.adapter_id.clone(),
                    adapter_version: adapter.descriptor.adapter_version.clone(),
                    tool_id: tool_id_text(adapter.descriptor.tool_id).to_string(),
                    surface_id: surface.surface_id.clone(),
                    scope: surface.scope,
                    enumerator_id: surface.root_enumerator_id,
                })
            })
            .collect::<Vec<_>>();
        let registry = AuthorizedRootRegistry::discover(home, &specs)?;
        let project_locations = collect_project_locations(&registry.roots)?;
        let mut project_ignored_paths = registry.project_ignored_paths.clone();
        let canonical_home = std::fs::canonicalize(home)
            .map_err(|_| "Local HOME root is unavailable".to_string())?;
        let mut result = LocalScanResult::default();
        let mut instance_handles = BTreeMap::new();
        result
            .skipped_paths
            .extend(registry.pre_root_skips.iter().map(|skip| SkippedPath {
                root_id: None,
                safe_relative_locator: skip.safe_relative_locator.clone(),
                reason_code: skip.reason_code.clone(),
            }));
        result
            .issues
            .extend(registry.pre_root_skips.iter().map(|skip| SafeIssue {
                project_id: None,
                code: skip.reason_code.clone(),
                safe_message: skip.safe_message.clone(),
                safe_relative_locator: Some(skip.safe_relative_locator.clone()),
            }));
        result
            .issues
            .extend(registry.issues.iter().cloned().map(|issue| SafeIssue {
                project_id: issue.project_id,
                code: issue.code,
                safe_message: issue.safe_message,
                safe_relative_locator: None,
            }));
        for adapter in adapters {
            let mut budget = ScanBudget::default();
            for surface in &adapter.descriptor.surfaces {
                let surface_coverage_id = surface_progress_coverage_id(
                    &adapter.adapter_id,
                    &adapter.descriptor.adapter_version,
                    &surface.surface_id,
                );
                let surface_item_start = result.instances.len();
                progress(&LocalScanProgress {
                    adapter_id: adapter.adapter_id.clone(),
                    surface_id: surface.surface_id.clone(),
                    coverage_id: surface_coverage_id.clone(),
                    item_count: 0,
                });
                let discovery_issue_codes = registry
                    .coverage_issues
                    .iter()
                    .filter(|issue| {
                        issue.adapter_id == adapter.descriptor.adapter_id
                            && issue.surface_id == surface.surface_id
                            && issue.scope == surface.scope
                    })
                    .flat_map(|issue| issue.issue_codes.iter().cloned())
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect::<Vec<_>>();
                let roots = registry
                    .roots
                    .iter()
                    .filter(|root| {
                        root.adapter_id == adapter.descriptor.adapter_id
                            && root.surface_id == surface.surface_id
                    })
                    .collect::<Vec<_>>();
                let pre_root_skipped_count = (surface.scope == Scope::Project
                    && discovery_issue_codes
                        .iter()
                        .any(|code| code.starts_with("marker_subtree_")))
                .then_some(registry.pre_root_skips.len())
                .unwrap_or_default();
                if roots.is_empty() {
                    result.coverage.push(CoverageRecord {
                        adapter_id: adapter.adapter_id.clone(),
                        adapter_version: adapter.descriptor.adapter_version.clone(),
                        surface_id: surface.surface_id.clone(),
                        root_id: None,
                        scope: surface.scope,
                        project_id: None,
                        status: if discovery_issue_codes.is_empty() {
                            CoverageStatus::Complete
                        } else if discovery_issue_codes
                            .iter()
                            .any(|code| code.starts_with("marker_subtree_"))
                        {
                            CoverageStatus::Partial
                        } else {
                            CoverageStatus::Failed
                        },
                        presence: if discovery_issue_codes.is_empty() {
                            SurfacePresence::NotPresent
                        } else {
                            SurfacePresence::Unknown
                        },
                        item_count: 0,
                        skipped_count: pre_root_skipped_count,
                        issue_codes: discovery_issue_codes.clone(),
                    });
                }
                for root in roots {
                    let item_start = result.instances.len();
                    let skipped_start = result.skipped_paths.len();
                    let issue_start = result.issues.len();
                    let root_failed = scan_root(
                        &canonical_home,
                        root,
                        &root.project_scan_scopes,
                        adapter,
                        &mut budget,
                        &mut project_ignored_paths,
                        &mut instance_handles,
                        &mut result,
                    );
                    let skipped_count = result.skipped_paths.len() - skipped_start;
                    let issue_codes = result.issues[issue_start..]
                        .iter()
                        .map(|issue| issue.code.clone())
                        .chain(
                            result.skipped_paths[skipped_start..]
                                .iter()
                                .map(|skipped| skipped.reason_code.clone()),
                        )
                        .chain(discovery_issue_codes.iter().cloned())
                        .collect::<BTreeSet<_>>()
                        .into_iter()
                        .collect::<Vec<_>>();
                    let item_count = result.instances.len() - item_start;
                    result.coverage.push(CoverageRecord {
                        adapter_id: adapter.adapter_id.clone(),
                        adapter_version: adapter.descriptor.adapter_version.clone(),
                        surface_id: surface.surface_id.clone(),
                        root_id: Some(root.root_id.clone()),
                        scope: root.scope,
                        project_id: root.project_id.clone(),
                        status: if root_failed {
                            CoverageStatus::Failed
                        } else if issue_codes.is_empty() {
                            CoverageStatus::Complete
                        } else {
                            CoverageStatus::Partial
                        },
                        presence: if root_failed {
                            SurfacePresence::Unknown
                        } else {
                            SurfacePresence::Present
                        },
                        item_count,
                        skipped_count,
                        issue_codes,
                    });
                }
                progress(&LocalScanProgress {
                    adapter_id: adapter.adapter_id.clone(),
                    surface_id: surface.surface_id.clone(),
                    coverage_id: surface_coverage_id,
                    item_count: (result.instances.len() - surface_item_start) as u64,
                });
            }
        }
        result
            .instances
            .sort_by(|left, right| left.instance_id.cmp(&right.instance_id));
        result.coverage.sort_by(|left, right| {
            left.adapter_id
                .cmp(&right.adapter_id)
                .then_with(|| left.surface_id.cmp(&right.surface_id))
                .then_with(|| left.root_id.cmp(&right.root_id))
        });
        result.skipped_paths.sort_by(|left, right| {
            left.root_id
                .cmp(&right.root_id)
                .then_with(|| left.safe_relative_locator.cmp(&right.safe_relative_locator))
        });
        result.issues.sort_by(|left, right| {
            left.code
                .cmp(&right.code)
                .then_with(|| left.safe_relative_locator.cmp(&right.safe_relative_locator))
        });
        result.project_ignore_summaries =
            collect_project_ignore_summaries(&registry.roots, &project_ignored_paths);
        if instance_handles.len() != result.instances.len() {
            return Err("identity_collision".to_string());
        }
        Ok(LocalScanOutput {
            result,
            instance_handles,
            project_locations,
        })
    }
}

pub(crate) fn surface_progress_coverage_id(
    adapter_id: &str,
    adapter_version: &str,
    surface_id: &str,
) -> String {
    let mut hasher = Sha256::new();
    for field in [
        b"local-progress-coverage-v1".as_slice(),
        adapter_id.as_bytes(),
        adapter_version.as_bytes(),
        surface_id.as_bytes(),
    ] {
        hasher.update((field.len() as u64).to_be_bytes());
        hasher.update(field);
    }
    format!("{:x}", hasher.finalize())
}

fn collect_project_locations(
    roots: &[AuthorizedRoot],
) -> Result<BTreeMap<String, ProjectLocationRecord>, String> {
    let mut locations = BTreeMap::<String, ProjectLocationRecord>::new();
    for root in roots.iter().filter(|root| root.scope == Scope::Project) {
        let project_id = root
            .project_id
            .clone()
            .ok_or_else(|| "project_identity_missing".to_string())?;
        let display_name = root
            .canonical_root
            .file_name()
            .ok_or_else(|| "project_display_name_unavailable".to_string())?
            .to_os_string();
        let record = ProjectLocationRecord {
            project_id: project_id.clone(),
            display_name,
            root_identity: root.correlation_root_identity.clone(),
            root_device: root.root_device,
            root_inode: root.root_inode,
            owner_uid: root.root_owner_uid,
            canonical_path: root.canonical_root.clone(),
        };
        if let Some(existing) = locations.get(&project_id) {
            if existing.canonical_path != record.canonical_path
                || existing.root_identity != record.root_identity
                || existing.root_device != record.root_device
                || existing.root_inode != record.root_inode
                || existing.owner_uid != record.owner_uid
            {
                return Err("project_identity_collision".to_string());
            }
            continue;
        }
        locations.insert(project_id, record);
    }
    Ok(locations)
}

fn collect_project_ignore_summaries(
    roots: &[AuthorizedRoot],
    excluded_paths: &BTreeMap<String, BTreeSet<std::path::PathBuf>>,
) -> Vec<ProjectIgnoreSummary> {
    let mut summaries = BTreeMap::new();
    for scope in roots
        .iter()
        .flat_map(|root| root.project_scan_scopes.iter())
    {
        summaries
            .entry(scope.policy.project_id.clone())
            .or_insert_with(|| ProjectIgnoreSummary {
                project_id: scope.policy.project_id.clone(),
                source_revision: scope.policy.source_revision.clone(),
                rule_count: scope.policy.rule_count,
                excluded_path_count: excluded_paths
                    .get(&scope.policy.project_id)
                    .map_or(0, BTreeSet::len),
            });
    }
    summaries.into_values().collect()
}

#[derive(Debug, Default)]
struct ScanBudget {
    visited: usize,
    exhausted: bool,
}

#[derive(Debug, Clone, Copy)]
struct FileObservation {
    size: u64,
    modified_unix_millis: Option<u64>,
}

struct FileTarget {
    locator: String,
    correlation_locator: String,
    parser_id: ParserId,
    kind: SurfaceKind,
    raw_relative_components: Vec<Vec<u8>>,
    scan_file_identity: FileIdentity,
}

enum SecureReadError {
    TooLarge(FileObservation),
    Changed(FileObservation),
    NotRegular(FileObservation),
    Os,
}

fn scan_root(
    home: &Path,
    root: &AuthorizedRoot,
    project_scopes: &[ProjectScanScope],
    adapter: &CatalogAdapter,
    budget: &mut ScanBudget,
    project_ignored_paths: &mut BTreeMap<String, BTreeSet<std::path::PathBuf>>,
    instance_handles: &mut BTreeMap<String, InstanceHandle>,
    result: &mut LocalScanResult,
) -> bool {
    let root_fd = match open_scan_root_nofollow(&root.canonical_root) {
        Ok(fd) => fd,
        Err(error) => {
            let (code, message) = root_open_error(error);
            record_skip(root, String::new(), code, message, result);
            return true;
        }
    };
    let stat = match fstat(&root_fd) {
        Ok(stat) => stat,
        Err(_) => {
            record_skip(
                root,
                String::new(),
                "root_identity_unavailable",
                "A discovery root identity could not be verified",
                result,
            );
            return true;
        }
    };
    if !scan_root_authority_matches(root, &stat) {
        record_skip(
            root,
            String::new(),
            "root_identity_changed",
            "A discovery root changed after authorization",
            result,
        );
        return true;
    }

    walk_directory(
        home,
        root,
        project_scopes,
        adapter,
        &root_fd,
        Path::new(""),
        0,
        budget,
        project_ignored_paths,
        instance_handles,
        result,
    );
    false
}

fn open_scan_root_nofollow(path: &Path) -> Result<OwnedFd, Errno> {
    if !path.is_absolute() {
        return Err(Errno::INVAL);
    }
    let mut directory = open(
        "/",
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )?;
    for component in path.components() {
        match component {
            Component::RootDir => {}
            Component::Normal(segment) => {
                let name = CString::new(segment.as_bytes()).map_err(|_| Errno::INVAL)?;
                let observed = statat(directory.as_fd(), &name, AtFlags::SYMLINK_NOFOLLOW)?;
                if FileType::from_raw_mode(observed.st_mode) != FileType::Directory {
                    return Err(Errno::NOTDIR);
                }
                directory = open_directory_component(directory.as_fd(), &name, &observed)?;
            }
            Component::CurDir | Component::ParentDir | Component::Prefix(_) => {
                return Err(Errno::INVAL)
            }
        }
    }
    Ok(directory)
}

fn scan_root_authority_matches(root: &AuthorizedRoot, stat: &Stat) -> bool {
    FileType::from_raw_mode(stat.st_mode) == FileType::Directory
        && stat.st_dev as u64 == root.root_device
        && stat.st_ino == root.root_inode
        && stat.st_uid as u32 == root.root_owner_uid
        && destination_root_identity(stat.st_dev as u64, stat.st_ino, &root.canonical_root)
            == root.correlation_root_identity
}

#[allow(clippy::too_many_arguments)]
fn walk_directory(
    home: &Path,
    root: &AuthorizedRoot,
    project_scopes: &[ProjectScanScope],
    adapter: &CatalogAdapter,
    directory: &OwnedFd,
    relative: &Path,
    depth: usize,
    budget: &mut ScanBudget,
    project_ignored_paths: &mut BTreeMap<String, BTreeSet<std::path::PathBuf>>,
    instance_handles: &mut BTreeMap<String, InstanceHandle>,
    result: &mut LocalScanResult,
) {
    if budget.exhausted {
        return;
    }
    let names = match sorted_names(directory.as_fd()) {
        Ok(names) => names,
        Err(_) => {
            record_skip(
                root,
                stable_locator(home, root, relative),
                "path_unreadable",
                "A harness directory could not be read",
                result,
            );
            return;
        }
    };
    for name in names {
        if budget.visited >= MAX_SCAN_ENTRIES {
            budget.exhausted = true;
            record_skip(
                root,
                stable_locator(home, root, relative),
                "scan_entry_bound_reached",
                "A discovery adapter reached its deterministic entry bound",
                result,
            );
            return;
        }
        budget.visited += 1;
        let name_os = std::ffi::OsString::from_vec(name.as_bytes().to_vec());
        if fixed_ignore(&name_os.to_string_lossy()) {
            continue;
        }
        let child = relative.join(&name_os);
        let policy = policy_locator(home, root, &child);
        let descend = allowed_directory(adapter.descriptor.tool_id, root.scope, &policy);
        let classification = classify(adapter.descriptor.tool_id, root.scope, &policy);
        if !descend && classification.is_none() {
            continue;
        }
        let observed = match statat(directory.as_fd(), &name, AtFlags::SYMLINK_NOFOLLOW) {
            Ok(stat) => stat,
            Err(_) => {
                record_skip(
                    root,
                    stable_locator(home, root, &child),
                    "path_changed_during_scan",
                    "A harness path changed during discovery",
                    result,
                );
                continue;
            }
        };
        let file_type = FileType::from_raw_mode(observed.st_mode);
        let absolute_child = root.canonical_root.join(&child);
        let mut ignored = false;
        for scope in project_scopes.iter().filter(|scope| {
            scope.matches_absolute_descendant(&absolute_child, file_type == FileType::Directory)
        }) {
            ignored = true;
            if let Ok(policy_relative) = absolute_child.strip_prefix(&scope.verified_root) {
                project_ignored_paths
                    .entry(scope.policy.project_id.clone())
                    .or_default()
                    .insert(policy_relative.to_path_buf());
            }
        }
        if ignored {
            continue;
        }
        match file_type {
            FileType::Directory if descend => {
                if depth + 1 >= MAX_SCAN_DEPTH {
                    record_skip(
                        root,
                        stable_locator(home, root, &child),
                        "scan_depth_bound_reached",
                        "A discovery root reached its deterministic depth bound",
                        result,
                    );
                    continue;
                }
                match open_directory_component(directory.as_fd(), &name, &observed) {
                    Ok(child_fd) => walk_directory(
                        home,
                        root,
                        project_scopes,
                        adapter,
                        &child_fd,
                        &child,
                        depth + 1,
                        budget,
                        project_ignored_paths,
                        instance_handles,
                        result,
                    ),
                    Err(error) => {
                        let (code, message) = component_open_error(error);
                        record_skip(
                            root,
                            stable_locator(home, root, &child),
                            code,
                            message,
                            result,
                        );
                    }
                }
            }
            FileType::RegularFile => {
                if let Some((parser_id, kind)) = classification {
                    let Some(raw_relative_components) = raw_relative_components(&child) else {
                        record_skip(
                            root,
                            stable_locator(home, root, &child),
                            "invalid_relative_component",
                            "A harness path contained an invalid relative component",
                            result,
                        );
                        continue;
                    };
                    scan_regular_file(
                        directory.as_fd(),
                        &name,
                        &observed,
                        root,
                        adapter,
                        FileTarget {
                            locator: stable_locator(home, root, &child),
                            correlation_locator: normalized_relative_locator(&child).expect(
                                "walked Local file paths are validated relative components",
                            ),
                            parser_id,
                            kind,
                            raw_relative_components,
                            scan_file_identity: file_identity(&observed),
                        },
                        instance_handles,
                        result,
                    );
                } else if descend {
                    record_skip(
                        root,
                        stable_locator(home, root, &child),
                        "non_directory_component_or_race",
                        "A declared discovery directory was not a directory",
                        result,
                    );
                }
            }
            FileType::Symlink => record_skip(
                root,
                stable_locator(home, root, &child),
                "symlink_component_rejected",
                "A symlink in a declared discovery path was rejected",
                result,
            ),
            _ => record_skip(
                root,
                stable_locator(home, root, &child),
                "non_regular_component_rejected",
                "A declared discovery path was not a regular file or directory",
                result,
            ),
        }
    }
}

fn sorted_names(directory: BorrowedFd<'_>) -> Result<Vec<CString>, Errno> {
    let mut stream = Dir::read_from(directory)?;
    let mut names = Vec::new();
    while let Some(entry) = stream.read() {
        let entry = entry?;
        let name = entry.file_name();
        if name.to_bytes() == b"." || name.to_bytes() == b".." {
            continue;
        }
        if !valid_segment(name) {
            return Err(Errno::INVAL);
        }
        names.push(name.to_owned());
    }
    names.sort_unstable_by(|left, right| left.as_bytes().cmp(right.as_bytes()));
    Ok(names)
}

fn valid_segment(name: &CStr) -> bool {
    let bytes = name.to_bytes();
    !bytes.is_empty() && bytes != b"." && bytes != b".." && !bytes.contains(&b'/')
}

fn open_directory_component(
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
        || !same_identity(observed, &opened)
    {
        return Err(Errno::NOTDIR);
    }
    Ok(fd)
}

fn open_regular_component(
    parent: BorrowedFd<'_>,
    name: &CStr,
    observed: &Stat,
) -> Result<OwnedFd, Errno> {
    let fd = openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC,
        Mode::empty(),
    )?;
    let opened = fstat(&fd)?;
    if FileType::from_raw_mode(opened.st_mode) != FileType::RegularFile
        || !same_identity(observed, &opened)
    {
        return Err(Errno::INVAL);
    }
    Ok(fd)
}

fn same_identity(left: &Stat, right: &Stat) -> bool {
    left.st_dev == right.st_dev
        && left.st_ino == right.st_ino
        && FileType::from_raw_mode(left.st_mode) == FileType::from_raw_mode(right.st_mode)
}

fn stable_during_read(left: &Stat, right: &Stat) -> bool {
    same_identity(left, right)
        && left.st_mode == right.st_mode
        && left.st_nlink == right.st_nlink
        && left.st_size == right.st_size
        && left.st_mtime == right.st_mtime
        && left.st_mtime_nsec == right.st_mtime_nsec
        && left.st_ctime == right.st_ctime
        && left.st_ctime_nsec == right.st_ctime_nsec
}

fn read_bounded(
    fd: BorrowedFd<'_>,
) -> Result<(Vec<u8>, FileObservation, FileIdentity), SecureReadError> {
    let before = fstat(fd).map_err(|_| SecureReadError::Os)?;
    let observation = file_observation(&before);
    if FileType::from_raw_mode(before.st_mode) != FileType::RegularFile {
        return Err(SecureReadError::NotRegular(observation));
    }
    if observation.size > MAX_HARNESS_FILE_BYTES {
        return Err(SecureReadError::TooLarge(observation));
    }
    let mut bytes = Vec::with_capacity(observation.size as usize);
    let mut scratch = [0_u8; 16 * 1024];
    let mut offset = 0_u64;
    loop {
        let remaining = MAX_HARNESS_FILE_BYTES as usize + 1 - bytes.len();
        let wanted = remaining.min(scratch.len());
        let read = loop {
            match pread(fd, &mut scratch[..wanted], offset) {
                Err(Errno::INTR) => continue,
                Err(_) => return Err(SecureReadError::Os),
                Ok(read) => break read,
            }
        };
        if read == 0 {
            break;
        }
        bytes.extend_from_slice(&scratch[..read]);
        offset += read as u64;
        if bytes.len() > MAX_HARNESS_FILE_BYTES as usize {
            return Err(SecureReadError::TooLarge(observation));
        }
    }
    let after = fstat(fd).map_err(|_| SecureReadError::Os)?;
    let after_observation = file_observation(&after);
    if !stable_during_read(&before, &after) || after_observation.size != bytes.len() as u64 {
        return Err(SecureReadError::Changed(after_observation));
    }
    Ok((bytes, after_observation, file_identity(&after)))
}

#[allow(clippy::too_many_arguments)]
fn scan_regular_file(
    parent: BorrowedFd<'_>,
    name: &CStr,
    observed: &Stat,
    root: &AuthorizedRoot,
    adapter: &CatalogAdapter,
    target: FileTarget,
    instance_handles: &mut BTreeMap<String, InstanceHandle>,
    result: &mut LocalScanResult,
) {
    let Some(surface) = adapter
        .descriptor
        .surfaces
        .iter()
        .find(|surface| surface.surface_id == root.surface_id)
    else {
        return;
    };
    if !surface.parser_ids.contains(&target.parser_id) {
        return;
    }
    let fd = match open_regular_component(parent, name, observed) {
        Ok(fd) => fd,
        Err(error) => {
            let (code, message) = component_open_error(error);
            push_unreadable(
                root,
                adapter,
                UnreadableRecord {
                    locator: target.locator,
                    correlation_locator: target.correlation_locator,
                    kind: target.kind,
                    parser_id: target.parser_id,
                    metadata: Some(file_observation(observed)),
                    raw_relative_components: target.raw_relative_components,
                    scan_file_identity: target.scan_file_identity,
                    code,
                    safe_message: message,
                },
                instance_handles,
                result,
            );
            return;
        }
    };
    let (bytes, metadata, scan_file_identity) = match read_bounded(fd.as_fd()) {
        Ok(value) => value,
        Err(error) => {
            let (code, message, metadata) = match error {
                SecureReadError::TooLarge(metadata) => (
                    "file_size_limit_exceeded",
                    "A harness definition exceeded the deterministic file-size bound",
                    Some(metadata),
                ),
                SecureReadError::Changed(metadata) => (
                    "source_identity_changed",
                    "A harness definition changed while it was being read",
                    Some(metadata),
                ),
                SecureReadError::NotRegular(metadata) => (
                    "non_regular_component_rejected",
                    "A harness definition was not a regular file",
                    Some(metadata),
                ),
                SecureReadError::Os => (
                    "unreadable_document",
                    "A harness definition could not be read",
                    None,
                ),
            };
            push_unreadable(
                root,
                adapter,
                UnreadableRecord {
                    locator: target.locator,
                    correlation_locator: target.correlation_locator,
                    kind: target.kind,
                    parser_id: target.parser_id,
                    metadata,
                    raw_relative_components: target.raw_relative_components,
                    scan_file_identity: target.scan_file_identity,
                    code,
                    safe_message: message,
                },
                instance_handles,
                result,
            );
            return;
        }
    };
    let batch = parse_document(target.parser_id, &target.locator, target.kind, &bytes);
    let content_hash = hash(&bytes);
    let scan_content_sha256: [u8; 32] = Sha256::digest(&bytes).into();
    result.issues.extend(batch.issues);
    for item in batch.items {
        if !surface.supported_kinds.contains(&item.kind) {
            record_skip(
                root,
                item.stable_source_locator,
                "unsupported_parser_kind",
                "A parser emitted a kind outside its declared discovery surface",
                result,
            );
            continue;
        }
        let identity = StableIdentity {
            identity_namespace: adapter.descriptor.identity_namespace.clone(),
            tool_id: adapter.descriptor.tool_id,
            surface_id: root.surface_id.clone(),
            scope: root.scope,
            project_id: root.project_id.clone(),
            locator_version: adapter.descriptor.locator_version,
            stable_source_locator: item.stable_source_locator.clone(),
        };
        let instance_id = derive_instance_id(&identity);
        let config_entry_locator = item
            .stable_source_locator
            .strip_prefix(&target.locator)
            .filter(|suffix| !suffix.is_empty())
            .map(str::to_owned);
        instance_handles.insert(
            instance_id.clone(),
            InstanceHandle {
                canonical_root: root.canonical_root.clone(),
                root_device: root.root_device,
                root_inode: root.root_inode,
                raw_relative_components: target.raw_relative_components.clone(),
                config_entry_locator: config_entry_locator.clone(),
                source_parser_id: target.parser_id,
                scan_content_sha256: Some(scan_content_sha256),
                scan_file_identity,
            },
        );
        result.instances.push(LocalInstance {
            instance_id,
            root_id: root.root_id.clone(),
            adapter_id: adapter.adapter_id.clone(),
            adapter_version: adapter.descriptor.adapter_version.clone(),
            tool_id: adapter.descriptor.tool_id,
            surface_id: root.surface_id.clone(),
            scope: root.scope,
            project_id: root.project_id.clone(),
            stable_source_locator: item.stable_source_locator,
            correlation_key: Some(DestinationKey {
                tool_id: adapter.descriptor.tool_id.as_str().to_string(),
                surface_id: root.surface_id.clone(),
                scope: destination_scope(root.scope),
                root_identity: root.correlation_root_identity.clone(),
                normalized_relative_locator: target.correlation_locator.clone(),
                config_entry_locator,
            }),
            kind: item.kind,
            name: item.name,
            description: item.description,
            description_source: item.description_source,
            settings: item.settings,
            content_hash: Some(content_hash.clone()),
            size: Some(metadata.size),
            modified_unix_millis: metadata.modified_unix_millis,
            parse_state: item.parse_state,
            issue_codes: item.issue_codes,
        });
    }
}

struct UnreadableRecord<'a> {
    locator: String,
    correlation_locator: String,
    kind: SurfaceKind,
    parser_id: ParserId,
    metadata: Option<FileObservation>,
    raw_relative_components: Vec<Vec<u8>>,
    scan_file_identity: FileIdentity,
    code: &'a str,
    safe_message: &'a str,
}

fn push_unreadable(
    root: &AuthorizedRoot,
    adapter: &CatalogAdapter,
    record: UnreadableRecord<'_>,
    instance_handles: &mut BTreeMap<String, InstanceHandle>,
    result: &mut LocalScanResult,
) {
    let identity = StableIdentity {
        identity_namespace: adapter.descriptor.identity_namespace.clone(),
        tool_id: adapter.descriptor.tool_id,
        surface_id: root.surface_id.clone(),
        scope: root.scope,
        project_id: root.project_id.clone(),
        locator_version: adapter.descriptor.locator_version,
        stable_source_locator: record.locator.clone(),
    };
    let instance_id = derive_instance_id(&identity);
    instance_handles.insert(
        instance_id.clone(),
        InstanceHandle {
            canonical_root: root.canonical_root.clone(),
            root_device: root.root_device,
            root_inode: root.root_inode,
            raw_relative_components: record.raw_relative_components,
            config_entry_locator: None,
            source_parser_id: record.parser_id,
            scan_content_sha256: None,
            scan_file_identity: record.scan_file_identity,
        },
    );
    result.instances.push(LocalInstance {
        instance_id,
        root_id: root.root_id.clone(),
        adapter_id: adapter.adapter_id.clone(),
        adapter_version: adapter.descriptor.adapter_version.clone(),
        tool_id: adapter.descriptor.tool_id,
        surface_id: root.surface_id.clone(),
        scope: root.scope,
        project_id: root.project_id.clone(),
        stable_source_locator: record.locator.clone(),
        correlation_key: Some(DestinationKey {
            tool_id: adapter.descriptor.tool_id.as_str().to_string(),
            surface_id: root.surface_id.clone(),
            scope: destination_scope(root.scope),
            root_identity: root.correlation_root_identity.clone(),
            normalized_relative_locator: record.correlation_locator,
            config_entry_locator: None,
        }),
        kind: record.kind,
        name: None,
        description: None,
        description_source: None,
        settings: Vec::new(),
        content_hash: None,
        size: record.metadata.map(|metadata| metadata.size),
        modified_unix_millis: record
            .metadata
            .and_then(|metadata| metadata.modified_unix_millis),
        parse_state: ParseState::Unreadable,
        issue_codes: vec![record.code.to_string()],
    });
    record_skip(
        root,
        record.locator,
        record.code,
        record.safe_message,
        result,
    );
}

fn destination_scope(scope: Scope) -> DestinationScope {
    match scope {
        Scope::User => DestinationScope::User,
        Scope::Project => DestinationScope::Project,
    }
}

fn record_skip(
    root: &AuthorizedRoot,
    locator: String,
    code: &str,
    safe_message: &str,
    result: &mut LocalScanResult,
) {
    result.skipped_paths.push(SkippedPath {
        root_id: Some(root.root_id.clone()),
        safe_relative_locator: locator.clone(),
        reason_code: code.to_string(),
    });
    result.issues.push(SafeIssue {
        project_id: None,
        code: code.to_string(),
        safe_message: safe_message.to_string(),
        safe_relative_locator: Some(locator),
    });
}

fn file_observation(stat: &Stat) -> FileObservation {
    let size = u64::try_from(stat.st_size).unwrap_or(u64::MAX);
    let seconds = stat.st_mtime as i128;
    let nanos = stat.st_mtime_nsec as i128;
    let modified_unix_millis = (seconds >= 0 && nanos >= 0).then(|| {
        (seconds as u128)
            .saturating_mul(1_000)
            .saturating_add((nanos as u128) / 1_000_000)
            .min(u64::MAX as u128) as u64
    });
    FileObservation {
        size,
        modified_unix_millis,
    }
}

fn file_identity(stat: &Stat) -> FileIdentity {
    FileIdentity {
        device: stat.st_dev as u64,
        inode: stat.st_ino,
        file_type: FileIdentityType::Regular,
        mode: stat.st_mode as u32 & 0o7777,
        owner_uid: stat.st_uid as u32,
        owner_gid: stat.st_gid as u32,
        link_count: stat.st_nlink as u64,
        size: u64::try_from(stat.st_size).unwrap_or(u64::MAX),
        mtime_ns: i128::from(stat.st_mtime)
            .saturating_mul(1_000_000_000)
            .saturating_add(i128::from(stat.st_mtime_nsec)),
        ctime_ns: i128::from(stat.st_ctime)
            .saturating_mul(1_000_000_000)
            .saturating_add(i128::from(stat.st_ctime_nsec)),
    }
}

fn raw_relative_components(path: &Path) -> Option<Vec<Vec<u8>>> {
    let mut raw = Vec::new();
    for component in path.components() {
        let Component::Normal(segment) = component else {
            return None;
        };
        let bytes = segment.as_bytes();
        if bytes.is_empty()
            || bytes == b"."
            || bytes == b".."
            || bytes.contains(&b'/')
            || bytes.contains(&0)
        {
            return None;
        }
        raw.push(bytes.to_vec());
    }
    (!raw.is_empty()).then_some(raw)
}

fn root_open_error(error: Errno) -> (&'static str, &'static str) {
    match error {
        Errno::ACCESS | Errno::PERM => (
            "root_permission_denied",
            "A discovery root could not be opened with read-only permission",
        ),
        Errno::LOOP => (
            "symlink_root_rejected",
            "A discovery root symlink was rejected",
        ),
        Errno::NOENT => (
            "root_changed_during_scan",
            "A discovery root changed after authorization",
        ),
        _ => (
            "root_open_failed",
            "A discovery root could not be opened safely",
        ),
    }
}

fn component_open_error(error: Errno) -> (&'static str, &'static str) {
    match error {
        Errno::ACCESS | Errno::PERM => (
            "path_permission_denied",
            "A declared discovery path could not be read",
        ),
        Errno::LOOP => (
            "symlink_component_rejected",
            "A symlink in a declared discovery path was rejected",
        ),
        Errno::NOENT => (
            "path_changed_during_scan",
            "A declared discovery path changed during scan",
        ),
        Errno::NOTDIR => (
            "non_directory_component_or_race",
            "A declared discovery directory changed or was not a directory",
        ),
        _ => (
            "path_open_failed",
            "A declared discovery path could not be opened safely",
        ),
    }
}

fn classify(tool: ToolId, scope: Scope, path: &str) -> Option<(ParserId, SurfaceKind)> {
    let lower = path.to_ascii_lowercase();
    let markdown = lower.ends_with(".md");
    if markdown && (lower.contains("/skills/") || lower.ends_with("/skill.md")) {
        return Some((ParserId::SkillFrontmatterV1, SurfaceKind::Skill));
    }
    if tool == ToolId::Codex && lower.contains(".codex/agents/") && lower.ends_with(".toml") {
        return Some((ParserId::CodexTomlV1, SurfaceKind::Agent));
    }
    if markdown
        && lower.contains("/agents/")
        && (tool != ToolId::Codex || lower.contains(".codex/agents/"))
    {
        return Some((ParserId::MarkdownRuleV1, SurfaceKind::Agent));
    }
    if markdown && (lower.contains("/commands/") || lower.contains("/prompts/")) {
        return Some((ParserId::MarkdownRuleV1, SurfaceKind::Command));
    }
    if lower.contains("/workflows/") {
        return Some((
            if tool == ToolId::ClaudeCode && lower.ends_with(".js") {
                ParserId::ClaudeWorkflowJsMetadataV1
            } else {
                ParserId::MarkdownRuleV1
            },
            SurfaceKind::Workflow,
        ));
    }
    if markdown
        && (lower.contains("/rules/")
            || lower.contains("/output-styles/")
            || lower.ends_with("agents.md")
            || lower.ends_with("claude.md")
            || lower.ends_with("claude.local.md")
            || lower.ends_with("gemini.md")
            || lower.ends_with("soul.md"))
    {
        return Some((ParserId::MarkdownRuleV1, SurfaceKind::Rule));
    }
    if lower.ends_with("hooks.json") {
        return Some((ParserId::HookJsonV1, SurfaceKind::Hook));
    }
    if tool == ToolId::Hermes && lower.contains("/hooks/") && lower.ends_with(".yaml") {
        return Some((ParserId::HermesYamlV1, SurfaceKind::Hook));
    }
    if tool == ToolId::ClaudeCode
        && (lower.ends_with("settings.json") || lower.ends_with("settings.local.json"))
    {
        return Some((ParserId::ClaudeSettingsV1, SurfaceKind::Hook));
    }
    if tool == ToolId::Codex && lower.ends_with("config.toml") {
        return Some((ParserId::CodexTomlV1, SurfaceKind::Unclassified));
    }
    if tool == ToolId::AntigravityCli
        && (lower.ends_with("settings.json") || lower.ends_with("import_manifest.json"))
    {
        return Some((ParserId::AntigravityCliJsonV1, SurfaceKind::Unclassified));
    }
    if tool == ToolId::Hermes && lower.ends_with("config.yaml") {
        return Some((ParserId::HermesYamlV1, SurfaceKind::Unclassified));
    }
    if tool == ToolId::Hermes && scope == Scope::Project && markdown {
        return Some((ParserId::SkillFrontmatterV1, SurfaceKind::Skill));
    }
    None
}

fn allowed_directory(tool: ToolId, scope: Scope, path: &str) -> bool {
    if path.is_empty() || (tool == ToolId::Hermes && scope == Scope::Project) {
        return true;
    }
    if tool == ToolId::Hermes && scope == Scope::User {
        if path == ".hermes/profiles" {
            return true;
        }
        if let Some(profile_path) = path.strip_prefix(".hermes/profiles/") {
            let parts = profile_path.split('/').collect::<Vec<_>>();
            return parts.len() == 1 || (parts.len() >= 2 && parts[1] == "skills");
        }
    }
    let recursive_roots: &[&str] = match (tool, scope) {
        (ToolId::Codex, Scope::User) => &[
            ".codex/skills",
            ".codex/agents",
            ".codex/prompts",
            ".agents/skills",
        ],
        (ToolId::Codex, Scope::Project) => &[".codex/agents", ".codex/prompts", ".agents/skills"],
        (ToolId::ClaudeCode, Scope::User) => &[
            ".claude/skills",
            ".claude/agents",
            ".claude/commands",
            ".claude/rules",
            ".claude/workflows",
            ".claude/output-styles",
        ],
        (ToolId::ClaudeCode, Scope::Project) => &[
            ".claude/skills",
            ".claude/agents",
            ".claude/commands",
            ".claude/rules",
            ".claude/workflows",
            ".claude/output-styles",
        ],
        (ToolId::Antigravity, Scope::User) => &[".gemini/config/skills", ".gemini/config/plugins"],
        (ToolId::AntigravityCli, Scope::User) => &[
            ".gemini/antigravity-cli/skills",
            ".gemini/antigravity-cli/plugins",
        ],
        (ToolId::Antigravity, Scope::Project) | (ToolId::AntigravityCli, Scope::Project) => &[
            ".agents/skills",
            ".agents/agents",
            ".agents/rules",
            ".agents/workflows",
            ".agents/plugins",
            "_agents/plugins",
            ".agent/skills",
            ".agent/rules",
        ],
        (ToolId::Hermes, Scope::User) => &[".hermes/skills", ".hermes/hooks"],
        (ToolId::Hermes, Scope::Project) => &[],
    };
    recursive_roots.iter().any(|root| {
        path == *root
            || path.starts_with(&format!("{root}/"))
            || root.starts_with(&format!("{path}/"))
    })
}

fn policy_locator(home: &Path, root: &AuthorizedRoot, relative: &Path) -> String {
    if root.scope == Scope::Project {
        encode_path(relative)
    } else if root.canonical_root.starts_with(home) {
        encode_path(
            root.canonical_root
                .strip_prefix(home)
                .unwrap()
                .join(relative)
                .as_path(),
        )
    } else if root.tool_id == "claude_code" || root.tool_id == "claude-code" {
        format!(".claude/{}", encode_path(relative))
    } else {
        encode_path(relative)
    }
}

fn stable_locator(home: &Path, root: &AuthorizedRoot, relative: &Path) -> String {
    if root.scope == Scope::User && root.canonical_root.starts_with(home) {
        encode_path(
            root.canonical_root
                .strip_prefix(home)
                .unwrap()
                .join(relative)
                .as_path(),
        )
    } else {
        encode_path(relative)
    }
}

fn encode_path(path: &Path) -> String {
    path.as_os_str()
        .as_encoded_bytes()
        .split(|byte| *byte == b'/')
        .map(encode_segment)
        .collect::<Vec<_>>()
        .join("/")
}

fn encode_segment(segment: &[u8]) -> String {
    if secret_like_segment(segment) {
        let digest = Sha256::digest(segment);
        let token = digest[..6]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        return format!("[redacted-{token}]");
    }
    segment
        .iter()
        .map(|byte| match byte {
            b'a'..=b'z' | b'A'..=b'Z' | b'0'..=b'9' | b'.' | b'_' | b'-' => {
                (*byte as char).to_string()
            }
            _ => format!("%{byte:02X}"),
        })
        .collect()
}

fn secret_like_segment(segment: &[u8]) -> bool {
    let lower = segment
        .iter()
        .map(u8::to_ascii_lowercase)
        .collect::<Vec<_>>();
    [
        b"secret".as_slice(),
        b"token".as_slice(),
        b"password".as_slice(),
        b"credential".as_slice(),
        b"api_key".as_slice(),
        b"apikey".as_slice(),
        b"private_key".as_slice(),
    ]
    .iter()
    .any(|needle| lower.windows(needle.len()).any(|window| window == *needle))
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

fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

pub fn derive_instance_id(identity: &StableIdentity) -> String {
    let mut digest = Sha256::new();
    update_field(&mut digest, identity.identity_namespace.as_bytes());
    update_field(&mut digest, tool_id(identity.tool_id));
    update_field(&mut digest, identity.surface_id.as_bytes());
    update_field(
        &mut digest,
        match identity.scope {
            Scope::User => b"user",
            Scope::Project => b"project",
        },
    );
    match &identity.project_id {
        Some(project_id) => {
            digest.update([1]);
            update_field(&mut digest, project_id.as_bytes());
        }
        None => digest.update([0]),
    }
    digest.update(identity.locator_version.to_be_bytes());
    update_field(&mut digest, identity.stable_source_locator.as_bytes());
    digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn update_field(digest: &mut Sha256, value: &[u8]) {
    digest.update(value.len().to_be_bytes());
    digest.update(value);
}

fn tool_id(tool_id: ToolId) -> &'static [u8] {
    tool_id_text(tool_id).as_bytes()
}

fn tool_id_text(tool_id: ToolId) -> &'static str {
    match tool_id {
        ToolId::Codex => "codex",
        ToolId::ClaudeCode => "claude_code",
        ToolId::Antigravity => "antigravity",
        ToolId::AntigravityCli => "antigravity_cli",
        ToolId::Hermes => "hermes",
    }
}

#[cfg(test)]
mod project_ignore_scan_tests {
    use std::os::unix::fs::MetadataExt;
    use std::os::unix::fs::PermissionsExt;

    use super::*;
    use crate::contexts::local::AdapterCatalog;
    use tempfile::tempdir;

    fn skill(name: &str) -> String {
        format!("---\nname: {name}\ndescription: {name} skill\n---\n# {name}\n")
    }

    #[test]
    fn adapter_traversal_uses_the_marker_registry_policy() {
        let home = tempdir().expect("temporary HOME");
        let project = home.path().join("project");
        let keep = project.join(".agents/skills/keep/SKILL.md");
        let ignored = project.join(".agents/skills/ignored/SKILL.md");
        std::fs::create_dir_all(keep.parent().expect("keep parent")).expect("keep tree");
        std::fs::create_dir_all(ignored.parent().expect("ignored parent")).expect("ignored tree");
        std::fs::write(&keep, skill("Keep")).expect("keep skill");
        std::fs::write(&ignored, skill("Ignored")).expect("ignored skill");
        std::fs::write(
            project.join(".harnesskitignore"),
            ".agents/skills/ignored/\n",
        )
        .expect("project ignore");
        let adapter = AdapterCatalog::load_embedded()
            .expect("embedded catalog")
            .for_tool(ToolId::Codex)
            .expect("Codex adapter")
            .clone();

        let output = LocalScanner::scan_with_handles(home.path(), &[adapter]).expect("Local scan");
        let names = output
            .result
            .instances
            .iter()
            .filter_map(|instance| instance.name.as_deref())
            .collect::<BTreeSet<_>>();

        assert!(names.contains("Keep"));
        assert!(!names.contains("Ignored"));
        assert_eq!(output.project_locations.len(), 1);
        assert_eq!(output.result.project_ignore_summaries.len(), 1);
        let summary = &output.result.project_ignore_summaries[0];
        assert_eq!(summary.rule_count, 1);
        assert_eq!(summary.excluded_path_count, 1);
        assert_eq!(summary.source_revision.len(), 64);
        let serialized = serde_json::to_string(summary).expect("safe summary");
        assert!(!serialized.contains(".agents/skills/ignored"));
        assert!(!serialized.contains(".harnesskitignore"));
    }

    #[test]
    fn nested_project_traversal_keeps_ancestor_and_own_policy_scopes() {
        let home = tempdir().expect("temporary HOME");
        let owner = home.path().join("owner");
        let nested = owner.join("nested");
        let keep = nested.join(".agents/skills/keep/SKILL.md");
        let ignored = nested.join(".agents/skills/ignored/SKILL.md");
        std::fs::create_dir_all(owner.join(".codex")).expect("owner marker");
        std::fs::create_dir_all(keep.parent().expect("keep parent")).expect("keep tree");
        std::fs::create_dir_all(ignored.parent().expect("ignored parent")).expect("ignored tree");
        std::fs::write(&keep, skill("NestedKeep")).expect("keep skill");
        std::fs::write(&ignored, skill("NestedIgnored")).expect("ignored skill");
        std::fs::write(
            owner.join(".harnesskitignore"),
            "nested/.agents/skills/ignored/\n",
        )
        .expect("owner ignore");
        std::fs::write(nested.join(".harnesskitignore"), "other/\n").expect("nested ignore");
        let adapter = AdapterCatalog::load_embedded()
            .expect("embedded catalog")
            .for_tool(ToolId::Codex)
            .expect("Codex adapter")
            .clone();

        let output = LocalScanner::scan_with_handles(home.path(), &[adapter]).expect("Local scan");
        let names = output
            .result
            .instances
            .iter()
            .filter_map(|instance| instance.name.as_deref())
            .collect::<BTreeSet<_>>();

        assert!(names.contains("NestedKeep"));
        assert!(!names.contains("NestedIgnored"));
        assert_eq!(output.project_locations.len(), 2);
        assert_eq!(output.result.project_ignore_summaries.len(), 2);
        assert!(output
            .result
            .project_ignore_summaries
            .iter()
            .any(|summary| summary.excluded_path_count == 1));
    }

    #[test]
    fn scan_root_authority_revalidates_owner_and_derived_identity() {
        let directory = tempdir().expect("temporary root");
        let canonical_root =
            std::fs::canonicalize(directory.path()).expect("canonical temporary root");
        let metadata = std::fs::metadata(&canonical_root).expect("root metadata");
        let fd = open(
            &canonical_root,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::empty(),
        )
        .expect("root fd");
        let stat = fstat(&fd).expect("root stat");
        let mut root = AuthorizedRoot {
            root_id: "root".to_string(),
            correlation_root_identity: destination_root_identity(
                metadata.dev(),
                metadata.ino(),
                &canonical_root,
            ),
            adapter_id: "adapter".to_string(),
            adapter_version: "1".to_string(),
            tool_id: "codex".to_string(),
            surface_id: "surface".to_string(),
            scope: Scope::User,
            project_id: None,
            canonical_root,
            root_device: metadata.dev(),
            root_inode: metadata.ino(),
            root_owner_uid: metadata.uid(),
            project_scan_scopes: Vec::new(),
        };

        assert!(scan_root_authority_matches(&root, &stat));
        root.root_owner_uid = root.root_owner_uid.wrapping_add(1);
        assert!(!scan_root_authority_matches(&root, &stat));
        root.root_owner_uid = metadata.uid();
        root.correlation_root_identity = "changed".to_string();
        assert!(!scan_root_authority_matches(&root, &stat));
    }

    #[test]
    fn scan_root_reopen_rejects_an_intermediate_symlink() {
        use std::os::unix::fs::symlink;

        let directory = tempdir().expect("temporary root");
        let real_parent = directory.path().join("real-parent");
        let project = real_parent.join("project");
        std::fs::create_dir_all(&project).expect("project tree");
        let linked_parent = directory.path().join("linked-parent");
        symlink(&real_parent, &linked_parent).expect("parent symlink");

        assert!(open_scan_root_nofollow(&linked_parent.join("project")).is_err());
    }

    #[test]
    fn scan_identity_captures_strict_metadata_without_debug_values() {
        let home = tempdir().expect("temporary HOME");
        let source = home.path().join(".codex/skills/strict/SKILL.md");
        std::fs::create_dir_all(source.parent().expect("skill parent")).expect("skill tree");
        std::fs::write(&source, skill("Strict")).expect("skill source");
        let mut permissions = std::fs::metadata(&source)
            .expect("skill metadata")
            .permissions();
        permissions.set_mode(0o640);
        std::fs::set_permissions(&source, permissions).expect("set deterministic mode");
        let metadata = std::fs::metadata(&source).expect("strict metadata");
        let adapter = AdapterCatalog::load_embedded()
            .expect("embedded catalog")
            .for_tool(ToolId::Codex)
            .expect("Codex adapter")
            .clone();

        let output = LocalScanner::scan_with_handles(home.path(), &[adapter]).expect("Local scan");
        let handle = output
            .instance_handles
            .values()
            .find(|handle| handle.raw_relative_components.last() == Some(&b"SKILL.md".to_vec()))
            .expect("scanned skill handle");
        let identity = handle.scan_file_identity;

        assert_eq!(identity.mode, metadata.mode() & 0o7777);
        assert_eq!(identity.owner_uid, metadata.uid());
        assert_eq!(identity.owner_gid, metadata.gid());
        assert_eq!(identity.link_count, metadata.nlink());
        assert_eq!(
            identity.ctime_ns,
            i128::from(metadata.ctime())
                .saturating_mul(1_000_000_000)
                .saturating_add(i128::from(metadata.ctime_nsec()))
        );
        let debug = format!("{identity:?}");
        assert!(debug.contains("captured"));
        assert!(!debug.contains("mode"));
        assert!(!debug.contains("owner_uid"));
        assert!(!debug.contains("owner_gid"));
        assert!(!debug.contains("link_count"));
        assert!(!debug.contains("ctime_ns"));
        assert!(!debug.contains(&metadata.ino().to_string()));
        assert!(!debug.contains(&metadata.uid().to_string()));
    }
}
