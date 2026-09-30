//! Project-local `.harnesskitignore` authority. Exact text stays outside snapshots.

use std::fs::File;
use std::io::Write;
use std::os::unix::ffi::OsStrExt;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use ignore::gitignore::{Gitignore, GitignoreBuilder};
use rustix::fd::{AsFd, BorrowedFd, OwnedFd};
use rustix::ffi::CString;
use rustix::fs::{
    fchmod, fstat, fsync, open, openat, renameat, statat, unlinkat, AtFlags, FileType, Mode,
    OFlags, Stat,
};
use rustix::io::{pread, Errno};
use rustix::process::getuid;
use sha2::{Digest, Sha256};

use crate::projection::root_identity;

use super::domain::ProjectLocationRecord;

pub const PROJECT_IGNORE_FILE: &str = ".harnesskitignore";
pub const MAX_PROJECT_IGNORE_BYTES: usize = 256 * 1024;

static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectIgnoreSource {
    pub project_id: String,
    pub root_identity: String,
    pub source_revision: String,
    pub exact_text: String,
    pub byte_len: usize,
    pub missing: bool,
}

pub struct ProjectIgnorePolicy {
    pub project_id: String,
    pub root_identity: String,
    pub source_revision: String,
    pub rule_count: usize,
    matcher: Gitignore,
}

impl PartialEq for ProjectIgnorePolicy {
    fn eq(&self, other: &Self) -> bool {
        self.project_id == other.project_id
            && self.root_identity == other.root_identity
            && self.source_revision == other.source_revision
            && self.rule_count == other.rule_count
    }
}

impl Eq for ProjectIgnorePolicy {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectScanScope {
    pub verified_root: PathBuf,
    pub root_identity: String,
    pub root_device: u64,
    pub root_inode: u64,
    pub owner_uid: u32,
    pub policy_revision: String,
    pub policy: Arc<ProjectIgnorePolicy>,
}

impl ProjectScanScope {
    pub fn for_project(project: &ProjectLocationRecord) -> Result<Self, ProjectIgnoreError> {
        let root = open_project_root(project)?;
        Self::from_verified_directory(
            &project.project_id,
            &project.canonical_path,
            root.as_fd(),
            project.root_device,
            project.root_inode,
            project.owner_uid,
        )
    }

    pub(crate) fn from_verified_directory(
        project_id: &str,
        verified_root: &Path,
        directory: BorrowedFd<'_>,
        root_device: u64,
        root_inode: u64,
        owner_uid: u32,
    ) -> Result<Self, ProjectIgnoreError> {
        Self::from_verified_directory_with_source(
            project_id,
            verified_root,
            directory,
            root_device,
            root_inode,
            owner_uid,
        )
        .map(|(scope, _)| scope)
    }

    pub(crate) fn from_verified_directory_with_source(
        project_id: &str,
        verified_root: &Path,
        directory: BorrowedFd<'_>,
        root_device: u64,
        root_inode: u64,
        owner_uid: u32,
    ) -> Result<(Self, ProjectIgnoreSource), ProjectIgnoreError> {
        let (source, policy) = load_at_directory(
            project_id,
            verified_root,
            directory,
            root_device,
            root_inode,
            owner_uid,
        )?;
        let scope = Self::from_policy(verified_root, root_device, root_inode, owner_uid, policy);
        Ok((scope, source))
    }

    pub(crate) fn from_transient_ignore_source(
        project_id: &str,
        verified_root: &Path,
        root_device: u64,
        root_inode: u64,
        owner_uid: u32,
        exact_text: &str,
        missing: bool,
        expected_source_revision: &str,
    ) -> Result<Self, ProjectIgnoreError> {
        if owner_uid != getuid().as_raw() {
            return Err(root_changed());
        }
        if exact_text.len() > MAX_PROJECT_IGNORE_BYTES || (missing && !exact_text.is_empty()) {
            return Err(invalid());
        }

        let root_identity = root_identity(root_device, root_inode, verified_root);
        let observed_source_revision =
            source_revision(&root_identity, exact_text.as_bytes(), missing);
        let policy = Arc::new(compile_policy(
            project_id,
            verified_root,
            &root_identity,
            &observed_source_revision,
            exact_text,
        )?);
        if observed_source_revision != expected_source_revision {
            return Err(stale());
        }
        Ok(Self::from_policy(
            verified_root,
            root_device,
            root_inode,
            owner_uid,
            policy,
        ))
    }

    pub(crate) fn transient_ignore_source_revision(
        verified_root: &Path,
        root_device: u64,
        root_inode: u64,
        exact_text: &str,
        missing: bool,
    ) -> String {
        source_revision(
            &root_identity(root_device, root_inode, verified_root),
            exact_text.as_bytes(),
            missing,
        )
    }

    fn from_policy(
        verified_root: &Path,
        root_device: u64,
        root_inode: u64,
        owner_uid: u32,
        policy: Arc<ProjectIgnorePolicy>,
    ) -> Self {
        Self {
            verified_root: verified_root.to_path_buf(),
            root_identity: policy.root_identity.clone(),
            root_device,
            root_inode,
            owner_uid,
            policy_revision: policy.source_revision.clone(),
            policy,
        }
    }

    pub(crate) fn matches_absolute_descendant(&self, path: &Path, is_dir: bool) -> bool {
        path.strip_prefix(&self.verified_root)
            .ok()
            .is_some_and(|relative| self.policy.matches_descendant(relative, is_dir))
    }
}

impl std::fmt::Debug for ProjectIgnorePolicy {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ProjectIgnorePolicy")
            .field("project_id", &self.project_id)
            .field("root_identity", &self.root_identity)
            .field("source_revision", &self.source_revision)
            .field("rule_count", &self.rule_count)
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectIgnoreError {
    pub code: &'static str,
    pub safe_message: &'static str,
    pub line: Option<usize>,
    pub column: Option<usize>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectIgnoreSave {
    pub source: ProjectIgnoreSource,
}

impl ProjectIgnorePolicy {
    pub fn matches_descendant(&self, relative: &Path, is_dir: bool) -> bool {
        if relative.as_os_str().is_empty()
            || relative.is_absolute()
            || relative
                .components()
                .any(|part| !matches!(part, Component::Normal(_)))
        {
            return false;
        }
        self.matcher
            .matched_path_or_any_parents(relative, is_dir)
            .is_ignore()
    }
}

pub fn load_project_ignore(
    project: &ProjectLocationRecord,
) -> Result<(ProjectIgnoreSource, Arc<ProjectIgnorePolicy>), ProjectIgnoreError> {
    let root = open_project_root(project)?;
    load_at_directory(
        &project.project_id,
        &project.canonical_path,
        root.as_fd(),
        project.root_device,
        project.root_inode,
        project.owner_uid,
    )
}

pub fn save_project_ignore(
    project: &ProjectLocationRecord,
    expected_revision: &str,
    exact_text: &str,
) -> Result<ProjectIgnoreSave, ProjectIgnoreError> {
    if exact_text.len() > MAX_PROJECT_IGNORE_BYTES {
        return Err(invalid());
    }
    validate_ignore_text(exact_text)?;
    let root = open_project_root(project)?;
    let current = read_source_at(
        &project.project_id,
        &project.canonical_path,
        root.as_fd(),
        project.root_device,
        project.root_inode,
        project.owner_uid,
    )?;
    if current.source.source_revision != expected_revision {
        return Err(stale());
    }
    compile_policy(
        &project.project_id,
        &project.canonical_path,
        &current.source.root_identity,
        &current.source.source_revision,
        exact_text,
    )?;

    let mode = current.mode.unwrap_or(0o644);
    let (temporary, temporary_fd, temporary_stat) = create_temporary(root.as_fd(), mode)?;
    let write_result = (|| -> Result<ProjectIgnoreSource, ProjectIgnoreError> {
        let mut file = File::from(temporary_fd);
        file.write_all(exact_text.as_bytes())
            .map_err(|_| write_failed())?;
        file.sync_all().map_err(|_| write_failed())?;
        drop(file);

        let latest = read_source_at(
            &project.project_id,
            &project.canonical_path,
            root.as_fd(),
            project.root_device,
            project.root_inode,
            project.owner_uid,
        )?;
        if latest.source.source_revision != expected_revision
            || !same_source_entry(current.entry_stat.as_ref(), latest.entry_stat.as_ref())
        {
            return Err(stale());
        }
        revalidate_project_root(project, root.as_fd())?;
        // Portable POSIX has no conditional rename; recheck at the last boundary before replace.
        revalidate_source_entry(root.as_fd(), latest.entry_stat.as_ref())?;
        renameat(
            root.as_fd(),
            temporary.as_str(),
            root.as_fd(),
            PROJECT_IGNORE_FILE,
        )
        .map_err(|_| write_failed())?;
        fsync(root.as_fd()).map_err(|_| write_failed())?;
        Ok(ProjectIgnoreSource {
            project_id: project.project_id.clone(),
            root_identity: current.source.root_identity.clone(),
            source_revision: source_revision(
                &current.source.root_identity,
                exact_text.as_bytes(),
                false,
            ),
            exact_text: exact_text.to_owned(),
            byte_len: exact_text.len(),
            missing: false,
        })
    })();

    if write_result.is_err()
        && statat(root.as_fd(), temporary.as_str(), AtFlags::SYMLINK_NOFOLLOW)
            .is_ok_and(|current| same_identity(&current, &temporary_stat))
    {
        let _ = unlinkat(root.as_fd(), temporary.as_str(), AtFlags::empty());
    }
    write_result.map(|source| ProjectIgnoreSave { source })
}

struct LoadedSource {
    source: ProjectIgnoreSource,
    mode: Option<u32>,
    entry_stat: Option<Stat>,
}

fn load_at_directory(
    project_id: &str,
    root: &Path,
    directory: BorrowedFd<'_>,
    root_device: u64,
    root_inode: u64,
    owner_uid: u32,
) -> Result<(ProjectIgnoreSource, Arc<ProjectIgnorePolicy>), ProjectIgnoreError> {
    let loaded = read_source_at(
        project_id,
        root,
        directory,
        root_device,
        root_inode,
        owner_uid,
    )?;
    let policy = compile_policy(
        project_id,
        root,
        &loaded.source.root_identity,
        &loaded.source.source_revision,
        &loaded.source.exact_text,
    )?;
    Ok((loaded.source, Arc::new(policy)))
}

fn read_source_at(
    project_id: &str,
    root: &Path,
    directory: BorrowedFd<'_>,
    root_device: u64,
    root_inode: u64,
    owner_uid: u32,
) -> Result<LoadedSource, ProjectIgnoreError> {
    let root_stat = fstat(directory).map_err(|_| root_changed())?;
    validate_root_stat(root, &root_stat, root_device, root_inode, owner_uid, None)?;
    let root_identity = root_identity(root_device, root_inode, root);
    let observed = match statat(directory, PROJECT_IGNORE_FILE, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(stat) => Some(stat),
        Err(Errno::NOENT) => None,
        Err(_) => return Err(unreadable()),
    };
    let Some(observed) = observed else {
        let source_revision = source_revision(&root_identity, &[], true);
        return Ok(LoadedSource {
            source: ProjectIgnoreSource {
                project_id: project_id.to_owned(),
                root_identity,
                source_revision,
                exact_text: String::new(),
                byte_len: 0,
                missing: true,
            },
            mode: None,
            entry_stat: None,
        });
    };
    if FileType::from_raw_mode(observed.st_mode) != FileType::RegularFile
        || observed.st_uid as u32 != owner_uid
    {
        return Err(unreadable());
    }
    if observed.st_size < 0 || observed.st_size as usize > MAX_PROJECT_IGNORE_BYTES {
        return Err(invalid());
    }
    let file = openat(
        directory,
        PROJECT_IGNORE_FILE,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| unreadable())?;
    let before = fstat(file.as_fd()).map_err(|_| unreadable())?;
    if !stable_stat(&observed, &before)
        || FileType::from_raw_mode(before.st_mode) != FileType::RegularFile
        || before.st_uid as u32 != owner_uid
        || before.st_size < 0
        || before.st_size as usize > MAX_PROJECT_IGNORE_BYTES
    {
        return Err(unreadable());
    }

    let mut bytes = Vec::with_capacity(before.st_size.max(0) as usize);
    let mut scratch = [0_u8; 16 * 1024];
    let mut offset = 0_u64;
    loop {
        let remaining = MAX_PROJECT_IGNORE_BYTES + 1 - bytes.len();
        let wanted = remaining.min(scratch.len());
        let read = loop {
            match pread(file.as_fd(), &mut scratch[..wanted], offset) {
                Err(Errno::INTR) => continue,
                Err(_) => return Err(unreadable()),
                Ok(read) => break read,
            }
        };
        if read == 0 {
            break;
        }
        bytes.extend_from_slice(&scratch[..read]);
        offset += read as u64;
        if bytes.len() > MAX_PROJECT_IGNORE_BYTES {
            return Err(invalid());
        }
    }
    let after = fstat(file.as_fd()).map_err(|_| unreadable())?;
    if !stable_stat(&before, &after) || after.st_size as usize != bytes.len() {
        return Err(changed());
    }
    let exact_text = String::from_utf8(bytes).map_err(|_| invalid())?;
    validate_ignore_text(&exact_text)?;
    let source_revision = source_revision(&root_identity, exact_text.as_bytes(), false);
    Ok(LoadedSource {
        source: ProjectIgnoreSource {
            project_id: project_id.to_owned(),
            root_identity,
            source_revision,
            byte_len: exact_text.len(),
            exact_text,
            missing: false,
        },
        mode: Some(before.st_mode as u32 & 0o777),
        entry_stat: Some(after),
    })
}

fn revalidate_source_entry(
    directory: BorrowedFd<'_>,
    expected: Option<&Stat>,
) -> Result<(), ProjectIgnoreError> {
    match (
        expected,
        statat(directory, PROJECT_IGNORE_FILE, AtFlags::SYMLINK_NOFOLLOW),
    ) {
        (None, Err(Errno::NOENT)) => Ok(()),
        (Some(expected), Ok(current)) if stable_stat(expected, &current) => Ok(()),
        _ => Err(stale()),
    }
}

fn compile_policy(
    project_id: &str,
    root: &Path,
    root_identity: &str,
    source_revision: &str,
    exact_text: &str,
) -> Result<ProjectIgnorePolicy, ProjectIgnoreError> {
    validate_ignore_text(exact_text)?;
    let source_path = root.join(PROJECT_IGNORE_FILE);
    let mut builder = GitignoreBuilder::new(root);
    let mut rule_count = 0;
    for (line_index, line) in exact_text.lines().enumerate() {
        if line.trim().is_empty() || line.starts_with('#') {
            continue;
        }
        rule_count += 1;
        builder
            .add_line(Some(source_path.clone()), line)
            .map_err(|_| invalid_at(line_index + 1, 1))?;
    }
    let matcher = builder.build().map_err(|_| invalid())?;
    Ok(ProjectIgnorePolicy {
        project_id: project_id.to_owned(),
        root_identity: root_identity.to_owned(),
        source_revision: source_revision.to_owned(),
        rule_count,
        matcher,
    })
}

fn open_project_root(project: &ProjectLocationRecord) -> Result<OwnedFd, ProjectIgnoreError> {
    let root = open_absolute_directory_nofollow(&project.canonical_path)?;
    revalidate_project_root(project, root.as_fd())?;
    Ok(root)
}

fn open_absolute_directory_nofollow(path: &Path) -> Result<OwnedFd, ProjectIgnoreError> {
    if !path.is_absolute() {
        return Err(root_changed());
    }
    let mut directory = open(
        "/",
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| root_changed())?;
    for component in path.components() {
        match component {
            Component::RootDir => {}
            Component::Normal(segment) => {
                let name = CString::new(segment.as_bytes()).map_err(|_| root_changed())?;
                let observed = statat(directory.as_fd(), &name, AtFlags::SYMLINK_NOFOLLOW)
                    .map_err(|_| root_changed())?;
                if FileType::from_raw_mode(observed.st_mode) != FileType::Directory {
                    return Err(root_changed());
                }
                let child = openat(
                    directory.as_fd(),
                    &name,
                    OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
                    Mode::empty(),
                )
                .map_err(|_| root_changed())?;
                let opened = fstat(child.as_fd()).map_err(|_| root_changed())?;
                if !same_identity(&observed, &opened) {
                    return Err(root_changed());
                }
                directory = child;
            }
            Component::CurDir | Component::ParentDir | Component::Prefix(_) => {
                return Err(root_changed())
            }
        }
    }
    Ok(directory)
}

fn revalidate_project_root(
    project: &ProjectLocationRecord,
    directory: BorrowedFd<'_>,
) -> Result<(), ProjectIgnoreError> {
    let stat = fstat(directory).map_err(|_| root_changed())?;
    validate_root_stat(
        &project.canonical_path,
        &stat,
        project.root_device,
        project.root_inode,
        project.owner_uid,
        Some(&project.root_identity),
    )
}

fn validate_root_stat(
    root: &Path,
    stat: &Stat,
    expected_device: u64,
    expected_inode: u64,
    expected_owner_uid: u32,
    expected_root_identity: Option<&str>,
) -> Result<(), ProjectIgnoreError> {
    let current_identity = root_identity(stat.st_dev as u64, stat.st_ino, root);
    if FileType::from_raw_mode(stat.st_mode) != FileType::Directory
        || stat.st_dev as u64 != expected_device
        || stat.st_ino != expected_inode
        || stat.st_uid as u32 != expected_owner_uid
        || expected_owner_uid != getuid().as_raw()
        || expected_root_identity.is_some_and(|expected| expected != current_identity)
    {
        return Err(root_changed());
    }
    Ok(())
}

fn create_temporary(
    directory: BorrowedFd<'_>,
    mode: u32,
) -> Result<(String, OwnedFd, Stat), ProjectIgnoreError> {
    for _ in 0..128 {
        let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let name = format!(".harnesskitignore.{}.{}.tmp", std::process::id(), sequence);
        match openat(
            directory,
            name.as_str(),
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::from(mode as u16),
        ) {
            Ok(file) => {
                let validation = (|| {
                    fchmod(file.as_fd(), Mode::from(mode as u16)).map_err(|_| write_failed())?;
                    let stat = fstat(file.as_fd()).map_err(|_| write_failed())?;
                    if FileType::from_raw_mode(stat.st_mode) != FileType::RegularFile
                        || stat.st_uid != getuid().as_raw()
                    {
                        return Err(write_failed());
                    }
                    Ok(stat)
                })();
                match validation {
                    Ok(stat) => return Ok((name, file, stat)),
                    Err(error) => {
                        drop(file);
                        let _ = unlinkat(directory, name.as_str(), AtFlags::empty());
                        return Err(error);
                    }
                }
            }
            Err(Errno::EXIST) => continue,
            Err(_) => return Err(write_failed()),
        }
    }
    Err(write_failed())
}

fn source_revision(root_identity: &str, bytes: &[u8], missing: bool) -> String {
    let mut digest = Sha256::new();
    digest.update(b"harnesskit-ignore-v1\0");
    digest.update(root_identity.as_bytes());
    digest.update([missing as u8]);
    digest.update(bytes);
    format!("{:x}", digest.finalize())
}

fn validate_ignore_text(text: &str) -> Result<(), ProjectIgnoreError> {
    for (line_index, raw_line) in text.lines().enumerate() {
        let line = raw_line.strip_prefix('!').unwrap_or(raw_line).trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let windows_absolute = line.len() >= 3
            && line.as_bytes()[0].is_ascii_alphabetic()
            && line.as_bytes()[1] == b':'
            && matches!(line.as_bytes()[2], b'/' | b'\\');
        let valid = !line.starts_with("//")
            && !line.starts_with("\\\\")
            && !windows_absolute
            && !line.split(['/', '\\']).any(|part| part == "..");
        if !valid {
            return Err(invalid_at(line_index + 1, 1));
        }
    }
    Ok(())
}

#[cfg(test)]
fn valid_ignore_text(text: &str) -> bool {
    validate_ignore_text(text).is_ok()
}

fn same_identity(left: &Stat, right: &Stat) -> bool {
    left.st_dev == right.st_dev
        && left.st_ino == right.st_ino
        && FileType::from_raw_mode(left.st_mode) == FileType::from_raw_mode(right.st_mode)
}

fn stable_stat(left: &Stat, right: &Stat) -> bool {
    same_identity(left, right)
        && left.st_mode == right.st_mode
        && left.st_nlink == right.st_nlink
        && left.st_size == right.st_size
        && left.st_mtime == right.st_mtime
        && left.st_mtime_nsec == right.st_mtime_nsec
        && left.st_ctime == right.st_ctime
        && left.st_ctime_nsec == right.st_ctime_nsec
}

fn same_source_entry(left: Option<&Stat>, right: Option<&Stat>) -> bool {
    match (left, right) {
        (None, None) => true,
        (Some(left), Some(right)) => stable_stat(left, right),
        _ => false,
    }
}

fn invalid() -> ProjectIgnoreError {
    ProjectIgnoreError {
        code: "project_ignore_invalid",
        safe_message: "제외 규칙 형식이 올바르지 않습니다.",
        line: None,
        column: None,
    }
}

fn invalid_at(line: usize, column: usize) -> ProjectIgnoreError {
    ProjectIgnoreError {
        code: "project_ignore_invalid",
        safe_message: "제외 규칙 형식이 올바르지 않습니다.",
        line: Some(line),
        column: Some(column),
    }
}

fn unreadable() -> ProjectIgnoreError {
    ProjectIgnoreError {
        code: "project_ignore_unreadable",
        safe_message: "제외 규칙을 읽지 못했습니다.",
        line: None,
        column: None,
    }
}

fn changed() -> ProjectIgnoreError {
    ProjectIgnoreError {
        code: "project_ignore_changed_during_read",
        safe_message: "제외 규칙이 읽는 동안 변경되었습니다.",
        line: None,
        column: None,
    }
}

fn root_changed() -> ProjectIgnoreError {
    ProjectIgnoreError {
        code: "project_root_changed",
        safe_message: "프로젝트 위치가 스캔 이후 변경되었습니다.",
        line: None,
        column: None,
    }
}

fn stale() -> ProjectIgnoreError {
    ProjectIgnoreError {
        code: "project_ignore_stale",
        safe_message: "제외 규칙이 변경되어 다시 불러와야 합니다.",
        line: None,
        column: None,
    }
}

fn write_failed() -> ProjectIgnoreError {
    ProjectIgnoreError {
        code: "project_ignore_write_failed",
        safe_message: "제외 규칙을 저장하지 못했습니다.",
        line: None,
        column: None,
    }
}

#[cfg(test)]
mod tests {
    use std::os::unix::fs::MetadataExt;

    use super::*;
    use tempfile::tempdir;

    fn project(root: &Path) -> ProjectLocationRecord {
        let canonical_path = std::fs::canonicalize(root).expect("canonical project root");
        let metadata = std::fs::metadata(&canonical_path).expect("project metadata");
        ProjectLocationRecord {
            project_id: "project".to_owned(),
            display_name: "project".into(),
            root_identity: root_identity(metadata.dev(), metadata.ino(), &canonical_path),
            root_device: metadata.dev(),
            root_inode: metadata.ino(),
            owner_uid: metadata.uid(),
            canonical_path,
        }
    }

    #[test]
    fn comments_globs_negation_and_boundary_rules_are_deterministic() {
        let directory = tempdir().expect("temporary project");
        std::fs::write(
            directory.path().join(PROJECT_IGNORE_FILE),
            "# generated output\n/generated/\n*.log\n!keep.log\n",
        )
        .expect("ignore source");
        let scope = ProjectScanScope::for_project(&project(directory.path())).expect("scope");

        assert!(scope
            .policy
            .matches_descendant(Path::new("generated"), true));
        assert!(scope
            .policy
            .matches_descendant(Path::new("generated/item.yml"), false));
        assert!(scope
            .policy
            .matches_descendant(Path::new("trace.log"), false));
        assert!(!scope
            .policy
            .matches_descendant(Path::new("keep.log"), false));
        assert!(!scope.policy.matches_descendant(Path::new(""), true));
        assert!(!scope
            .policy
            .matches_descendant(Path::new("../outside"), false));
    }

    #[test]
    fn verified_directory_returns_scope_and_exact_source_together() {
        let directory = tempdir().expect("temporary project");
        let exact_text = "generated/\n";
        std::fs::write(directory.path().join(PROJECT_IGNORE_FILE), exact_text)
            .expect("ignore source");
        let project = project(directory.path());
        let root = open_project_root(&project).expect("verified root");

        let (scope, source) = ProjectScanScope::from_verified_directory_with_source(
            &project.project_id,
            &project.canonical_path,
            root.as_fd(),
            project.root_device,
            project.root_inode,
            project.owner_uid,
        )
        .expect("scope and source");

        assert_eq!(source.exact_text, exact_text);
        assert_eq!(scope.policy_revision, source.source_revision);
        assert!(scope
            .policy
            .matches_descendant(Path::new("generated/item.yml"), false));
    }

    #[test]
    fn transient_ignore_source_rebuilds_scope_without_reopening_the_project() {
        let directory = tempdir().expect("temporary project");
        let project = project(directory.path());
        let exact_text = "generated/\n";
        let expected_source_revision =
            source_revision(&project.root_identity, exact_text.as_bytes(), false);
        let project_root = project.canonical_path.clone();
        drop(directory);

        let scope = ProjectScanScope::from_transient_ignore_source(
            &project.project_id,
            &project_root,
            project.root_device,
            project.root_inode,
            project.owner_uid,
            exact_text,
            false,
            &expected_source_revision,
        )
        .expect("scope from transient source");

        assert_eq!(scope.root_identity, project.root_identity);
        assert_eq!(scope.policy_revision, expected_source_revision);
        assert!(scope.matches_absolute_descendant(&project_root.join("generated/item.yml"), false));

        let missing_revision = source_revision(&project.root_identity, &[], true);
        let missing_scope = ProjectScanScope::from_transient_ignore_source(
            &project.project_id,
            &project_root,
            project.root_device,
            project.root_inode,
            project.owner_uid,
            "",
            true,
            &missing_revision,
        )
        .expect("missing transient source");
        assert_eq!(missing_scope.policy.rule_count, 0);
        assert_eq!(missing_scope.policy_revision, missing_revision);
    }

    #[test]
    fn transient_ignore_source_rejects_mismatched_or_invalid_observations() {
        let directory = tempdir().expect("temporary project");
        let project = project(directory.path());
        let exact_text = "generated/\n";
        let expected_source_revision =
            source_revision(&project.root_identity, exact_text.as_bytes(), false);
        let reconstruct = |root_device, owner_uid, text, missing, revision| {
            ProjectScanScope::from_transient_ignore_source(
                &project.project_id,
                &project.canonical_path,
                root_device,
                project.root_inode,
                owner_uid,
                text,
                missing,
                revision,
            )
        };

        let stale = reconstruct(
            project.root_device,
            project.owner_uid,
            exact_text,
            false,
            "different-revision",
        )
        .expect_err("revision mismatch rejected");
        assert_eq!(stale.code, "project_ignore_stale");

        let root_mismatch = reconstruct(
            project.root_device.wrapping_add(1),
            project.owner_uid,
            exact_text,
            false,
            &expected_source_revision,
        )
        .expect_err("root identity mismatch rejected");
        assert_eq!(root_mismatch.code, "project_ignore_stale");

        let malformed = "../outside\n";
        let malformed_revision =
            source_revision(&project.root_identity, malformed.as_bytes(), false);
        let invalid = reconstruct(
            project.root_device,
            project.owner_uid,
            malformed,
            false,
            &malformed_revision,
        )
        .expect_err("invalid source rejected");
        assert_eq!(invalid.code, "project_ignore_invalid");

        let missing_with_text = reconstruct(
            project.root_device,
            project.owner_uid,
            exact_text,
            true,
            &expected_source_revision,
        )
        .expect_err("missing source cannot carry text");
        assert_eq!(missing_with_text.code, "project_ignore_invalid");

        let oversized = "x".repeat(MAX_PROJECT_IGNORE_BYTES + 1);
        let too_large = reconstruct(
            project.root_device,
            project.owner_uid,
            &oversized,
            false,
            &expected_source_revision,
        )
        .expect_err("oversized source rejected");
        assert_eq!(too_large.code, "project_ignore_invalid");

        let wrong_owner = reconstruct(
            project.root_device,
            project.owner_uid.wrapping_add(1),
            exact_text,
            false,
            &expected_source_revision,
        )
        .expect_err("unverified owner rejected");
        assert_eq!(wrong_owner.code, "project_root_changed");
    }

    #[test]
    fn anchored_pattern_is_allowed_but_parent_and_absolute_escape_are_rejected() {
        assert!(valid_ignore_text("/generated/\n*.log\n"));
        assert!(!valid_ignore_text("../outside\n"));
        assert!(!valid_ignore_text("!../../outside\n"));
        assert!(!valid_ignore_text("C:\\outside\n"));
        assert!(!valid_ignore_text("\\\\server\\share\n"));

        let boundary = validate_ignore_text("valid/\n../outside\n").expect_err("boundary error");
        assert_eq!(boundary.line, Some(2));
        assert_eq!(boundary.column, Some(1));
        let syntax = compile_policy(
            "project",
            Path::new("/project"),
            "root",
            "revision",
            "foo\\\n",
        )
        .expect_err("glob syntax error");
        assert_eq!(syntax.line, Some(1));
        assert_eq!(syntax.column, Some(1));
    }

    #[test]
    fn same_bytes_in_a_replaced_source_entry_are_still_stale() {
        let directory = tempdir().expect("temporary project");
        let first_path = directory.path().join("first");
        let second_path = directory.path().join("second");
        std::fs::write(&first_path, "build/\n").expect("first source");
        std::fs::write(&second_path, "build/\n").expect("second source");
        let first = open(&first_path, OFlags::RDONLY, Mode::empty()).expect("first fd");
        let second = open(&second_path, OFlags::RDONLY, Mode::empty()).expect("second fd");
        let first = fstat(&first).expect("first stat");
        let second = fstat(&second).expect("second stat");

        assert!(!same_source_entry(Some(&first), Some(&second)));
    }

    #[test]
    fn symlink_source_is_rejected_without_following_it() {
        use std::os::unix::fs::symlink;

        let directory = tempdir().expect("temporary project");
        let outside = directory.path().join("outside");
        std::fs::write(&outside, "ignored/\n").expect("outside source");
        symlink(&outside, directory.path().join(PROJECT_IGNORE_FILE)).expect("ignore symlink");

        let error = load_project_ignore(&project(directory.path())).expect_err("symlink rejected");
        assert_eq!(error.code, "project_ignore_unreadable");
    }

    #[test]
    fn exact_text_save_rejects_stale_revision_and_preserves_bytes_on_rejection() {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};

        let directory = tempdir().expect("temporary project");
        let project = project(directory.path());
        let (source, _) = load_project_ignore(&project).expect("missing source");
        let saved =
            save_project_ignore(&project, &source.source_revision, "build/\n").expect("first save");
        assert_eq!(saved.source.exact_text, "build/\n");
        let ignore_path = directory.path().join(PROJECT_IGNORE_FILE);
        assert_eq!(
            std::fs::metadata(&ignore_path).expect("new mode").mode() & 0o777,
            0o644
        );
        std::fs::set_permissions(&ignore_path, std::fs::Permissions::from_mode(0o600))
            .expect("restrict existing mode");
        let mode_saved =
            save_project_ignore(&project, &saved.source.source_revision, "generated/\n")
                .expect("mode-preserving save");
        assert_eq!(
            std::fs::metadata(&ignore_path)
                .expect("preserved mode")
                .mode()
                & 0o777,
            0o600
        );

        let stale = save_project_ignore(&project, &source.source_revision, "other/\n")
            .expect_err("stale revision rejected");
        assert_eq!(stale.code, "project_ignore_stale");
        let invalid =
            save_project_ignore(&project, &mode_saved.source.source_revision, "../outside\n")
                .expect_err("boundary escape rejected");
        assert_eq!(invalid.code, "project_ignore_invalid");
        assert_eq!(
            std::fs::read_to_string(ignore_path).expect("preserved source"),
            "generated/\n"
        );
    }

    #[test]
    fn editor_rejects_a_replaced_project_root_identity() {
        let parent = tempdir().expect("temporary parent");
        let root = parent.path().join("project");
        std::fs::create_dir(&root).expect("project root");
        let project = project(&root);
        std::fs::rename(&root, parent.path().join("original")).expect("move original root");
        std::fs::create_dir(&root).expect("replacement root");

        let error = load_project_ignore(&project).expect_err("replacement rejected");
        assert_eq!(error.code, "project_root_changed");
    }
}
