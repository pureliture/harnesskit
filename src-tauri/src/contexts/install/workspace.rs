//! Isolated staging workspace for `install_workspace_inputs_v1`.

use rustix::fs::{
    fchmod, fstat, mkdirat, open, openat, readlinkat, statat, unlinkat, AtFlags, FileType, Mode,
    OFlags, Stat,
};
use rustix::io::Errno;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::fd::{AsFd, BorrowedFd, OwnedFd};
#[cfg(target_os = "macos")]
use std::os::fd::{FromRawFd, IntoRawFd};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

const ALLOWED_INPUT_ROOTS: &[&str] = &[
    "components",
    "profiles",
    "adapters",
    "schemas",
    "scripts/adapters",
    "scripts/install",
    "scripts/profiles",
];
static WORKSPACE_SEQUENCE: AtomicU64 = AtomicU64::new(1);

#[derive(Debug)]
pub struct InstallWorkspace {
    root: PathBuf,
    parent_fd: OwnedFd,
    root_name: OsString,
    root_fd: OwnedFd,
    root_device: u64,
    root_inode: u64,
    source_manifest: SourceManifest,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct TreeManifest {
    pub files: SourceManifest,
    pub directories: BTreeSet<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum StageWritePolicy {
    ReadOnly,
    DistOnly,
}

#[derive(Debug)]
pub(crate) struct DirectoryAuthority {
    pub path: PathBuf,
    parent_fd: OwnedFd,
    name: OsString,
    fd: OwnedFd,
    device: u64,
    inode: u64,
    require_owner_only: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceManifest {
    pub entries: Vec<SourceManifestEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceManifestEntry {
    pub relative_path: String,
    pub sha256: String,
    pub mode: u32,
    pub size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SourceRevisionManifest {
    regular_entries: Vec<SourceManifestEntry>,
    opaque_entries: Vec<SourceRevisionOpaqueEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct SourceRevisionOpaqueEntry {
    relative_path: Vec<u8>,
    entry_type: SourceRevisionEntryType,
    payload_sha256: Option<String>,
    mode: u32,
    size: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SourceRevisionEntryType {
    RegularFile,
    Symlink,
    Fifo,
    Socket,
    CharacterDevice,
    BlockDevice,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InstallWorkspaceError {
    PreviewStale,
    WorkspaceUnavailable,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct FileIdentity {
    device: u64,
    inode: u64,
    size: u64,
    modified_seconds: i64,
    modified_nanoseconds: i64,
    mode: u32,
}

struct CapturedSource {
    bytes: Vec<u8>,
    entry: SourceManifestEntry,
    identity: FileIdentity,
}

impl InstallWorkspace {
    pub fn create(source_root: &Path, app_temp_root: &Path) -> Result<Self, InstallWorkspaceError> {
        let source_fd = open_directory_lineage(source_root, InstallWorkspaceError::PreviewStale)?;
        let (root, parent_fd, root_name, root_fd, root_device, root_inode) =
            create_owner_only_workspace(app_temp_root)?;
        let mut workspace = Self {
            root,
            parent_fd,
            root_name,
            root_fd,
            root_device,
            root_inode,
            source_manifest: SourceManifest {
                entries: Vec::new(),
            },
        };

        let relative_files = collect_allowlisted_files(&source_fd)?;
        let mut entries = Vec::with_capacity(relative_files.len());
        for relative in relative_files {
            let before = capture_source(&source_fd, &relative)?;
            copy_captured(&workspace.root_fd, &relative, &before)?;
            let after = capture_source(&source_fd, &relative)?;
            if before.identity != after.identity || before.entry != after.entry {
                return Err(InstallWorkspaceError::PreviewStale);
            }
            entries.push(before.entry);
        }
        entries.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
        workspace.source_manifest = SourceManifest { entries };

        if scan_source_manifest_fd(&source_fd)? != workspace.source_manifest {
            return Err(InstallWorkspaceError::PreviewStale);
        }
        Ok(workspace)
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn source_manifest(&self) -> &SourceManifest {
        &self.source_manifest
    }

    pub(crate) fn root_fd(&self) -> BorrowedFd<'_> {
        self.root_fd.as_fd()
    }

    pub(crate) fn directory_authority(&self) -> Result<DirectoryAuthority, InstallWorkspaceError> {
        Ok(DirectoryAuthority {
            path: self.root.clone(),
            parent_fd: rustix::io::dup(&self.parent_fd)
                .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?,
            name: self.root_name.clone(),
            fd: rustix::io::dup(&self.root_fd)
                .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?,
            device: self.root_device,
            inode: self.root_inode,
            require_owner_only: true,
        })
    }

    pub(crate) fn ensure_runtime_tmp_authority(
        &self,
    ) -> Result<DirectoryAuthority, InstallWorkspaceError> {
        self.ensure_child_directory(".install-runtime-tmp")
    }

    pub(crate) fn ensure_dist_authority(
        &self,
    ) -> Result<DirectoryAuthority, InstallWorkspaceError> {
        self.ensure_child_directory("dist")
    }

    fn ensure_child_directory(
        &self,
        name: &str,
    ) -> Result<DirectoryAuthority, InstallWorkspaceError> {
        match mkdirat(&self.root_fd, name, Mode::from(0o700)) {
            Ok(()) | Err(Errno::EXIST) => {}
            Err(_) => return Err(InstallWorkspaceError::WorkspaceUnavailable),
        }
        let fd = openat(&self.root_fd, name, directory_flags(), Mode::empty())
            .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
        fchmod(&fd, Mode::from(0o700)).map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
        let stat = fstat(&fd).map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
        let authority = DirectoryAuthority {
            path: self.root.join(name),
            parent_fd: rustix::io::dup(&self.root_fd)
                .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?,
            name: OsString::from(name),
            fd,
            device: stat.st_dev as u64,
            inode: stat.st_ino as u64,
            require_owner_only: true,
        };
        authority.revalidate()?;
        Ok(authority)
    }

    pub(crate) fn snapshot_stage_manifest(&self) -> Result<TreeManifest, InstallWorkspaceError> {
        scan_tree_manifest_fd(&self.root_fd, Some(".install-runtime-tmp"))
    }

    pub(crate) fn validate_stage_transition(
        &self,
        before: &TreeManifest,
        policy: StageWritePolicy,
    ) -> Result<(), InstallWorkspaceError> {
        let after = self.snapshot_stage_manifest()?;
        let valid = match policy {
            StageWritePolicy::ReadOnly => &after == before,
            StageWritePolicy::DistOnly => {
                outside_dist_files(&after.files) == outside_dist_files(&before.files)
                    && outside_dist_directories(&after.directories)
                        == outside_dist_directories(&before.directories)
            }
        };
        if valid {
            Ok(())
        } else {
            Err(InstallWorkspaceError::PreviewStale)
        }
    }

    pub fn verify_source(&self, source_root: &Path) -> Result<(), InstallWorkspaceError> {
        let source_fd = open_directory_lineage(source_root, InstallWorkspaceError::PreviewStale)?;
        if scan_source_manifest_fd(&source_fd)? == self.source_manifest {
            Ok(())
        } else {
            Err(InstallWorkspaceError::PreviewStale)
        }
    }

    pub(crate) fn validate_captured_source(&self) -> Result<(), InstallWorkspaceError> {
        if scan_source_manifest_fd(&self.root_fd)? == self.source_manifest {
            Ok(())
        } else {
            Err(InstallWorkspaceError::PreviewStale)
        }
    }

    pub fn validate_root_identity(&self) -> Result<(), InstallWorkspaceError> {
        validate_directory_mapping(
            &self.parent_fd,
            &self.root_name,
            &self.root_fd,
            self.root_device,
            self.root_inode,
            true,
        )?;
        validate_path_identity(&self.root, self.root_device, self.root_inode, true)
    }
}

impl Drop for InstallWorkspace {
    fn drop(&mut self) {
        if validate_open_directory(&self.root_fd, self.root_device, self.root_inode, true).is_err()
        {
            return;
        }
        let _ = remove_directory_contents_fd(&self.root_fd);
        if self.validate_root_identity().is_ok() {
            let _ = unlinkat(&self.parent_fd, &self.root_name, AtFlags::REMOVEDIR);
        }
    }
}

impl DirectoryAuthority {
    pub(crate) fn revalidate(&self) -> Result<(), InstallWorkspaceError> {
        validate_directory_mapping(
            &self.parent_fd,
            &self.name,
            &self.fd,
            self.device,
            self.inode,
            self.require_owner_only,
        )?;
        validate_path_identity(&self.path, self.device, self.inode, self.require_owner_only)
    }

    pub(crate) fn tree_manifest(
        &self,
        excluded_top_level: Option<&str>,
    ) -> Result<TreeManifest, InstallWorkspaceError> {
        scan_tree_manifest_fd(&self.fd, excluded_top_level)
    }

    pub(crate) fn try_clone(&self) -> Result<Self, InstallWorkspaceError> {
        Ok(Self {
            path: self.path.clone(),
            parent_fd: rustix::io::dup(&self.parent_fd)
                .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?,
            name: self.name.clone(),
            fd: rustix::io::dup(&self.fd)
                .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?,
            device: self.device,
            inode: self.inode,
            require_owner_only: self.require_owner_only,
        })
    }
}

pub(crate) fn directory_authority_from_path(
    path: &Path,
) -> Result<DirectoryAuthority, InstallWorkspaceError> {
    let name = path
        .file_name()
        .ok_or(InstallWorkspaceError::WorkspaceUnavailable)?
        .to_os_string();
    let parent = path
        .parent()
        .ok_or(InstallWorkspaceError::WorkspaceUnavailable)?;
    let parent_fd = open_directory_lineage(parent, InstallWorkspaceError::WorkspaceUnavailable)?;
    let fd = openat(&parent_fd, &name, directory_flags(), Mode::empty())
        .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
    let stat = fstat(&fd).map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
    let authority = DirectoryAuthority {
        path: path.to_path_buf(),
        parent_fd,
        name,
        fd,
        device: stat.st_dev as u64,
        inode: stat.st_ino as u64,
        require_owner_only: false,
    };
    authority.revalidate()?;
    Ok(authority)
}

impl SourceManifest {
    pub fn canonical_bytes(&self) -> Vec<u8> {
        let mut bytes = b"install_workspace_inputs_v1\n".to_vec();
        for entry in &self.entries {
            let path = entry.relative_path.as_bytes();
            bytes.extend_from_slice(path.len().to_string().as_bytes());
            bytes.push(b':');
            bytes.extend_from_slice(path);
            bytes.push(0);
            bytes.extend_from_slice(entry.sha256.as_bytes());
            bytes.push(0);
            bytes.extend_from_slice(format!("{:03o}", entry.mode).as_bytes());
            bytes.push(0);
            bytes.extend_from_slice(entry.size.to_string().as_bytes());
            bytes.push(b'\n');
        }
        bytes
    }

    pub fn sha256(&self) -> String {
        hex_sha256(&self.canonical_bytes())
    }
}

impl SourceRevisionManifest {
    pub(crate) fn observe_root(source_root: &Path) -> Result<Self, InstallWorkspaceError> {
        let source_fd = open_directory_lineage(source_root, InstallWorkspaceError::PreviewStale)?;
        scan_source_revision_manifest_fd(&source_fd)
    }

    pub(crate) fn canonical_bytes(&self) -> Vec<u8> {
        let mut bytes = SourceManifest {
            entries: self.regular_entries.clone(),
        }
        .canonical_bytes();
        if self.opaque_entries.is_empty() {
            return bytes;
        }
        bytes.extend_from_slice(b"source_revision_opaque_entries_v1\n");
        for entry in &self.opaque_entries {
            bytes.extend_from_slice(entry.relative_path.len().to_string().as_bytes());
            bytes.push(b':');
            bytes.extend_from_slice(&entry.relative_path);
            bytes.push(0);
            bytes.extend_from_slice(entry.entry_type.tag());
            bytes.push(0);
            match &entry.payload_sha256 {
                Some(payload_sha256) => bytes.extend_from_slice(payload_sha256.as_bytes()),
                None => bytes.push(b'-'),
            }
            bytes.push(0);
            bytes.extend_from_slice(format!("{:04o}", entry.mode).as_bytes());
            bytes.push(0);
            bytes.extend_from_slice(entry.size.to_string().as_bytes());
            bytes.push(b'\n');
        }
        bytes
    }

    pub(crate) fn sha256(&self) -> String {
        hex_sha256(&self.canonical_bytes())
    }
}

impl SourceRevisionEntryType {
    fn tag(self) -> &'static [u8] {
        match self {
            Self::RegularFile => b"regular",
            Self::Symlink => b"symlink",
            Self::Fifo => b"fifo",
            Self::Socket => b"socket",
            Self::CharacterDevice => b"character-device",
            Self::BlockDevice => b"block-device",
            Self::Unknown => b"unknown",
        }
    }
}

impl InstallWorkspaceError {
    pub const fn code(self) -> &'static str {
        match self {
            Self::PreviewStale => "preview_stale",
            Self::WorkspaceUnavailable => "install_workspace_unavailable",
        }
    }
}

fn create_owner_only_workspace(
    app_temp_root: &Path,
) -> Result<(PathBuf, OwnedFd, OsString, OwnedFd, u64, u64), InstallWorkspaceError> {
    let app_fd =
        open_directory_lineage(app_temp_root, InstallWorkspaceError::WorkspaceUnavailable)?;
    let app_stat = fstat(&app_fd).map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
    if FileType::from_raw_mode(app_stat.st_mode) != FileType::Directory
        || app_stat.st_mode as u32 & 0o077 != 0
    {
        return Err(InstallWorkspaceError::WorkspaceUnavailable);
    }
    for _ in 0..128 {
        let sequence = WORKSPACE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let name = format!("install-workspace-{}-{sequence}", std::process::id());
        match mkdirat(&app_fd, name.as_str(), Mode::from(0o700)) {
            Ok(()) => {
                let root_fd = openat(&app_fd, name.as_str(), directory_flags(), Mode::empty())
                    .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
                fchmod(&root_fd, Mode::from(0o700))
                    .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
                let stat =
                    fstat(&root_fd).map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
                return Ok((
                    app_temp_root.join(&name),
                    app_fd,
                    OsString::from(name),
                    root_fd,
                    stat.st_dev as u64,
                    stat.st_ino as u64,
                ));
            }
            Err(Errno::EXIST) => continue,
            Err(_) => return Err(InstallWorkspaceError::WorkspaceUnavailable),
        }
    }
    Err(InstallWorkspaceError::WorkspaceUnavailable)
}

fn open_directory_lineage(
    path: &Path,
    error: InstallWorkspaceError,
) -> Result<OwnedFd, InstallWorkspaceError> {
    let mut current = open(
        if path.is_absolute() {
            Path::new("/")
        } else {
            Path::new(".")
        },
        directory_flags(),
        Mode::empty(),
    )
    .map_err(|_| error)?;
    for component in path.components() {
        match component {
            Component::RootDir | Component::CurDir => continue,
            Component::Normal(name) => {
                current =
                    openat(&current, name, directory_flags(), Mode::empty()).map_err(|_| error)?;
            }
            Component::ParentDir | Component::Prefix(_) => return Err(error),
        }
    }
    Ok(current)
}

fn directory_flags() -> OFlags {
    OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC
}

fn collect_allowlisted_files(root_fd: &OwnedFd) -> Result<Vec<PathBuf>, InstallWorkspaceError> {
    let mut files = Vec::new();
    for allowed in ALLOWED_INPUT_ROOTS {
        let relative = Path::new(allowed);
        if let Some(directory_fd) = open_relative_directory(root_fd, relative)? {
            collect_directory_fd(&directory_fd, relative, &mut files)?;
        }
    }
    files.sort_by_key(|left| relative_string(left));
    files.dedup();
    Ok(files)
}

fn open_relative_directory(
    root_fd: &OwnedFd,
    relative: &Path,
) -> Result<Option<OwnedFd>, InstallWorkspaceError> {
    let mut current = openat(root_fd, ".", directory_flags(), Mode::empty())
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    for component in relative.components() {
        let Component::Normal(name) = component else {
            return Err(InstallWorkspaceError::PreviewStale);
        };
        match openat(&current, name, directory_flags(), Mode::empty()) {
            Ok(next) => current = next,
            Err(Errno::NOENT) => return Ok(None),
            Err(_) => return Err(InstallWorkspaceError::PreviewStale),
        }
    }
    Ok(Some(current))
}

fn collect_directory_fd(
    directory_fd: &OwnedFd,
    relative_directory: &Path,
    files: &mut Vec<PathBuf>,
) -> Result<(), InstallWorkspaceError> {
    let mut names = directory_names(directory_fd)?;
    names.sort_by(|left, right| {
        left.as_os_str()
            .as_bytes()
            .cmp(right.as_os_str().as_bytes())
    });

    for name in names {
        let stat = statat(directory_fd, name.as_os_str(), AtFlags::SYMLINK_NOFOLLOW)
            .map_err(|_| InstallWorkspaceError::PreviewStale)?;
        let relative = relative_directory.join(&name);
        match FileType::from_raw_mode(stat.st_mode) {
            FileType::Directory => {
                let child = openat(
                    directory_fd,
                    name.as_os_str(),
                    directory_flags(),
                    Mode::empty(),
                )
                .map_err(|_| InstallWorkspaceError::PreviewStale)?;
                collect_directory_fd(&child, &relative, files)?;
            }
            FileType::RegularFile => {
                validate_relative(&relative)?;
                files.push(relative);
            }
            _ => return Err(InstallWorkspaceError::PreviewStale),
        }
    }
    Ok(())
}

#[cfg(target_os = "macos")]
#[repr(C)]
struct DarwinDir {
    _private: [u8; 0],
}

#[cfg(target_os = "macos")]
#[repr(C)]
struct DarwinDirent {
    d_ino: u64,
    d_seekoff: u64,
    d_reclen: u16,
    d_namlen: u16,
    d_type: u8,
    d_name: [std::os::raw::c_char; 1024],
}

#[cfg(target_os = "macos")]
extern "C" {
    fn fdopendir(fd: std::os::raw::c_int) -> *mut DarwinDir;
    fn readdir(directory: *mut DarwinDir) -> *mut DarwinDirent;
    fn closedir(directory: *mut DarwinDir) -> std::os::raw::c_int;
    fn __error() -> *mut std::os::raw::c_int;
}

#[cfg(target_os = "macos")]
struct DarwinDirectoryStream(*mut DarwinDir);

#[cfg(target_os = "macos")]
impl Drop for DarwinDirectoryStream {
    fn drop(&mut self) {
        // SAFETY: `self.0` is a unique non-null stream returned by `fdopendir`.
        let _ = unsafe { closedir(self.0) };
    }
}

#[cfg(target_os = "macos")]
fn directory_names(directory_fd: &OwnedFd) -> Result<Vec<OsString>, InstallWorkspaceError> {
    // Open a fresh directory description rather than `dup`-ing the retained authority fd.
    // `dup` shares the directory offset, which makes repeated manifest scans observe EOF.
    let scan_fd = openat(directory_fd, ".", directory_flags(), Mode::empty())
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    let raw_fd = scan_fd.into_raw_fd();
    // SAFETY: ownership of the fresh scan fd is transferred to `fdopendir` on success.
    let stream = unsafe { fdopendir(raw_fd) };
    if stream.is_null() {
        // SAFETY: `fdopendir` failed, so ownership of `raw_fd` remains with this function.
        drop(unsafe { OwnedFd::from_raw_fd(raw_fd) });
        return Err(InstallWorkspaceError::PreviewStale);
    }
    let _stream_guard = DarwinDirectoryStream(stream);
    let mut names = Vec::new();
    loop {
        // SAFETY: `__error` returns the current thread's errno location on macOS.
        unsafe { *__error() = 0 };
        // SAFETY: `stream` stays valid for the lifetime of `_stream_guard`.
        let entry = unsafe { readdir(stream) };
        if entry.is_null() {
            // SAFETY: errno is read from the current thread after `readdir` returned null.
            if unsafe { *__error() } != 0 {
                return Err(InstallWorkspaceError::PreviewStale);
            }
            break;
        }
        // SAFETY: `entry` is valid until the next `readdir`; `d_namlen` bounds `d_name`.
        let length = unsafe { (*entry).d_namlen as usize };
        if length == 0 || length > 1024 {
            return Err(InstallWorkspaceError::PreviewStale);
        }
        // SAFETY: the length check above keeps this slice inside `d_name`.
        let bytes =
            unsafe { std::slice::from_raw_parts((*entry).d_name.as_ptr().cast::<u8>(), length) };
        if bytes != b"." && bytes != b".." {
            names.push(OsString::from_vec(bytes.to_vec()));
        }
    }
    Ok(names)
}

#[cfg(not(target_os = "macos"))]
fn directory_names(_directory_fd: &OwnedFd) -> Result<Vec<OsString>, InstallWorkspaceError> {
    Err(InstallWorkspaceError::PreviewStale)
}

fn validate_relative(relative: &Path) -> Result<(), InstallWorkspaceError> {
    if relative.as_os_str().is_empty()
        || relative
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
        || relative.to_str().is_none()
    {
        return Err(InstallWorkspaceError::PreviewStale);
    }
    Ok(())
}

fn capture_source(
    root_fd: &OwnedFd,
    relative: &Path,
) -> Result<CapturedSource, InstallWorkspaceError> {
    validate_relative(relative)?;
    let mut file = open_relative_regular(root_fd, relative)?;
    let before = file
        .metadata()
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    if !before.file_type().is_file() {
        return Err(InstallWorkspaceError::PreviewStale);
    }
    let before_identity = identity(&before);
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    let after = file
        .metadata()
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    if before_identity != identity(&after) || before.len() != bytes.len() as u64 {
        return Err(InstallWorkspaceError::PreviewStale);
    }
    Ok(CapturedSource {
        entry: SourceManifestEntry {
            relative_path: relative_string(relative),
            sha256: hex_sha256(&bytes),
            mode: before.mode() & 0o777,
            size: before.len(),
        },
        bytes,
        identity: before_identity,
    })
}

fn open_relative_regular(
    root_fd: &OwnedFd,
    relative: &Path,
) -> Result<File, InstallWorkspaceError> {
    let mut current = openat(root_fd, ".", directory_flags(), Mode::empty())
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    let components = relative.components().collect::<Vec<_>>();
    for (index, component) in components.iter().enumerate() {
        let Component::Normal(name) = component else {
            return Err(InstallWorkspaceError::PreviewStale);
        };
        let is_last = index + 1 == components.len();
        let flags = OFlags::RDONLY
            | OFlags::NOFOLLOW
            | OFlags::CLOEXEC
            | if is_last {
                OFlags::empty()
            } else {
                OFlags::DIRECTORY
            };
        current = openat(&current, *name, flags, Mode::empty())
            .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    }
    Ok(File::from(current))
}

fn copy_captured(
    workspace_fd: &OwnedFd,
    relative: &Path,
    captured: &CapturedSource,
) -> Result<(), InstallWorkspaceError> {
    let parent = relative
        .parent()
        .ok_or(InstallWorkspaceError::WorkspaceUnavailable)?;
    let parent_fd = ensure_destination_directories(workspace_fd, parent)?;
    let file_name = relative
        .file_name()
        .ok_or(InstallWorkspaceError::WorkspaceUnavailable)?;
    let fd = openat(
        &parent_fd,
        file_name,
        OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::from(0o600),
    )
    .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
    let mut file = File::from(fd);
    file.write_all(&captured.bytes)
        .and_then(|_| file.sync_all())
        .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
    file.set_permissions(fs::Permissions::from_mode(captured.entry.mode))
        .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)
}

fn ensure_destination_directories(
    root_fd: &OwnedFd,
    relative: &Path,
) -> Result<OwnedFd, InstallWorkspaceError> {
    let mut current = openat(root_fd, ".", directory_flags(), Mode::empty())
        .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
    for component in relative.components() {
        let Component::Normal(name) = component else {
            return Err(InstallWorkspaceError::WorkspaceUnavailable);
        };
        match mkdirat(&current, name, Mode::from(0o700)) {
            Ok(()) | Err(Errno::EXIST) => {}
            Err(_) => return Err(InstallWorkspaceError::WorkspaceUnavailable),
        }
        current = openat(&current, name, directory_flags(), Mode::empty())
            .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
        fchmod(&current, Mode::from(0o700))
            .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
    }
    Ok(current)
}

fn scan_source_manifest_fd(root_fd: &OwnedFd) -> Result<SourceManifest, InstallWorkspaceError> {
    let mut entries = collect_allowlisted_files(root_fd)?
        .into_iter()
        .map(|relative| capture_source(root_fd, &relative).map(|captured| captured.entry))
        .collect::<Result<Vec<_>, _>>()?;
    entries.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    Ok(SourceManifest { entries })
}

fn scan_source_revision_manifest_fd(
    root_fd: &OwnedFd,
) -> Result<SourceRevisionManifest, InstallWorkspaceError> {
    let mut regular_entries = Vec::new();
    let mut opaque_entries = Vec::new();
    for allowed in ALLOWED_INPUT_ROOTS {
        collect_revision_allowed_root(
            root_fd,
            Path::new(allowed),
            &mut regular_entries,
            &mut opaque_entries,
        )?;
    }
    regular_entries.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    for pair in regular_entries.windows(2) {
        if pair[0].relative_path == pair[1].relative_path && pair[0] != pair[1] {
            return Err(InstallWorkspaceError::PreviewStale);
        }
    }
    regular_entries.dedup();
    opaque_entries.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    for pair in opaque_entries.windows(2) {
        if pair[0].relative_path == pair[1].relative_path && pair[0] != pair[1] {
            return Err(InstallWorkspaceError::PreviewStale);
        }
    }
    opaque_entries.dedup();
    let regular_paths = regular_entries
        .iter()
        .map(|entry| entry.relative_path.as_bytes().to_vec())
        .collect::<BTreeSet<_>>();
    if opaque_entries
        .iter()
        .any(|entry| regular_paths.contains(&entry.relative_path))
    {
        return Err(InstallWorkspaceError::PreviewStale);
    }
    Ok(SourceRevisionManifest {
        regular_entries,
        opaque_entries,
    })
}

fn collect_revision_allowed_root(
    root_fd: &OwnedFd,
    allowed_root: &Path,
    regular_entries: &mut Vec<SourceManifestEntry>,
    opaque_entries: &mut Vec<SourceRevisionOpaqueEntry>,
) -> Result<(), InstallWorkspaceError> {
    let mut current = openat(root_fd, ".", directory_flags(), Mode::empty())
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    let components = allowed_root.components().collect::<Vec<_>>();
    let mut relative = PathBuf::new();
    for (index, component) in components.iter().enumerate() {
        let Component::Normal(name) = component else {
            return Err(InstallWorkspaceError::PreviewStale);
        };
        relative.push(name);
        let stat = match statat(&current, *name, AtFlags::SYMLINK_NOFOLLOW) {
            Ok(stat) => stat,
            Err(Errno::NOENT) => return Ok(()),
            Err(_) => return Err(InstallWorkspaceError::PreviewStale),
        };
        if FileType::from_raw_mode(stat.st_mode) != FileType::Directory {
            collect_revision_leaf(
                &current,
                name,
                &relative,
                &stat,
                regular_entries,
                opaque_entries,
            )?;
            return Ok(());
        }
        let child = openat(&current, *name, directory_flags(), Mode::empty())
            .map_err(|_| InstallWorkspaceError::PreviewStale)?;
        let descriptor = fstat(&child).map_err(|_| InstallWorkspaceError::PreviewStale)?;
        if !same_revision_identity(&stat, &descriptor) {
            return Err(InstallWorkspaceError::PreviewStale);
        }
        if index + 1 == components.len() {
            collect_revision_directory_fd(&child, &relative, regular_entries, opaque_entries)?;
            return Ok(());
        }
        current = child;
    }
    Ok(())
}

fn collect_revision_directory_fd(
    directory_fd: &OwnedFd,
    relative_directory: &Path,
    regular_entries: &mut Vec<SourceManifestEntry>,
    opaque_entries: &mut Vec<SourceRevisionOpaqueEntry>,
) -> Result<(), InstallWorkspaceError> {
    let mut names = directory_names(directory_fd)?;
    names.sort_by(|left, right| {
        left.as_os_str()
            .as_bytes()
            .cmp(right.as_os_str().as_bytes())
    });
    for name in names {
        let before = statat(directory_fd, &name, AtFlags::SYMLINK_NOFOLLOW)
            .map_err(|_| InstallWorkspaceError::PreviewStale)?;
        let relative = relative_directory.join(&name);
        if FileType::from_raw_mode(before.st_mode) == FileType::Directory {
            let child = openat(directory_fd, &name, directory_flags(), Mode::empty())
                .map_err(|_| InstallWorkspaceError::PreviewStale)?;
            let descriptor = fstat(&child).map_err(|_| InstallWorkspaceError::PreviewStale)?;
            if !same_revision_identity(&before, &descriptor) {
                return Err(InstallWorkspaceError::PreviewStale);
            }
            collect_revision_directory_fd(&child, &relative, regular_entries, opaque_entries)?;
            let after = statat(directory_fd, &name, AtFlags::SYMLINK_NOFOLLOW)
                .map_err(|_| InstallWorkspaceError::PreviewStale)?;
            if !same_revision_stat(&before, &after) {
                return Err(InstallWorkspaceError::PreviewStale);
            }
        } else {
            collect_revision_leaf(
                directory_fd,
                &name,
                &relative,
                &before,
                regular_entries,
                opaque_entries,
            )?;
        }
    }
    Ok(())
}

fn collect_revision_leaf(
    directory_fd: &OwnedFd,
    name: &std::ffi::OsStr,
    relative: &Path,
    before: &Stat,
    regular_entries: &mut Vec<SourceManifestEntry>,
    opaque_entries: &mut Vec<SourceRevisionOpaqueEntry>,
) -> Result<(), InstallWorkspaceError> {
    let entry_type = SourceRevisionEntryType::from(FileType::from_raw_mode(before.st_mode));
    if entry_type == SourceRevisionEntryType::RegularFile && relative.to_str().is_some() {
        regular_entries.push(capture_source_from(
            directory_fd,
            &name.to_os_string(),
            relative,
        )?);
        return Ok(());
    }
    let payload_sha256 = match entry_type {
        SourceRevisionEntryType::RegularFile => {
            Some(capture_revision_regular_sha256(directory_fd, name, before)?)
        }
        SourceRevisionEntryType::Symlink => {
            let target = readlinkat(directory_fd, name, Vec::new())
                .map_err(|_| InstallWorkspaceError::PreviewStale)?;
            Some(hex_sha256(target.to_bytes()))
        }
        _ => None,
    };
    let after = statat(directory_fd, name, AtFlags::SYMLINK_NOFOLLOW)
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    if !same_revision_stat(before, &after) {
        return Err(InstallWorkspaceError::PreviewStale);
    }
    opaque_entries.push(SourceRevisionOpaqueEntry {
        relative_path: relative.as_os_str().as_bytes().to_vec(),
        entry_type,
        payload_sha256,
        mode: before.st_mode as u32 & 0o7777,
        size: u64::try_from(before.st_size).unwrap_or(u64::MAX),
    });
    Ok(())
}

fn capture_revision_regular_sha256(
    directory_fd: &OwnedFd,
    name: &std::ffi::OsStr,
    before_path: &Stat,
) -> Result<String, InstallWorkspaceError> {
    let fd = openat(
        directory_fd,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    let opened = fstat(&fd).map_err(|_| InstallWorkspaceError::PreviewStale)?;
    if !same_revision_identity(before_path, &opened)
        || FileType::from_raw_mode(opened.st_mode) != FileType::RegularFile
    {
        return Err(InstallWorkspaceError::PreviewStale);
    }
    let mut file = File::from(fd);
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    let after = fstat(&file).map_err(|_| InstallWorkspaceError::PreviewStale)?;
    if !same_revision_stat(&opened, &after)
        || u64::try_from(after.st_size).ok() != Some(bytes.len() as u64)
    {
        return Err(InstallWorkspaceError::PreviewStale);
    }
    Ok(hex_sha256(&bytes))
}

fn same_revision_identity(left: &Stat, right: &Stat) -> bool {
    left.st_dev == right.st_dev
        && left.st_ino == right.st_ino
        && FileType::from_raw_mode(left.st_mode) == FileType::from_raw_mode(right.st_mode)
}

fn same_revision_stat(left: &Stat, right: &Stat) -> bool {
    same_revision_identity(left, right)
        && left.st_mode == right.st_mode
        && left.st_size == right.st_size
        && left.st_mtime == right.st_mtime
        && left.st_mtime_nsec == right.st_mtime_nsec
        && left.st_ctime == right.st_ctime
        && left.st_ctime_nsec == right.st_ctime_nsec
}

impl From<FileType> for SourceRevisionEntryType {
    fn from(file_type: FileType) -> Self {
        match file_type {
            FileType::RegularFile => Self::RegularFile,
            FileType::Symlink => Self::Symlink,
            FileType::Fifo => Self::Fifo,
            FileType::Socket => Self::Socket,
            FileType::CharacterDevice => Self::CharacterDevice,
            FileType::BlockDevice => Self::BlockDevice,
            FileType::Directory | FileType::Unknown => Self::Unknown,
        }
    }
}

pub(crate) fn scan_tree_manifest_fd(
    root_fd: &OwnedFd,
    excluded_top_level: Option<&str>,
) -> Result<TreeManifest, InstallWorkspaceError> {
    let first = scan_tree_manifest_once(root_fd, excluded_top_level)?;
    let second = scan_tree_manifest_once(root_fd, excluded_top_level)?;
    if first == second {
        Ok(first)
    } else {
        Err(InstallWorkspaceError::PreviewStale)
    }
}

fn scan_tree_manifest_once(
    root_fd: &OwnedFd,
    excluded_top_level: Option<&str>,
) -> Result<TreeManifest, InstallWorkspaceError> {
    let mut files = Vec::new();
    let mut directories = BTreeSet::new();
    collect_tree_fd(
        root_fd,
        Path::new(""),
        excluded_top_level,
        &mut files,
        &mut directories,
    )?;
    files.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    Ok(TreeManifest {
        files: SourceManifest { entries: files },
        directories,
    })
}

fn collect_tree_fd(
    directory_fd: &OwnedFd,
    relative_directory: &Path,
    excluded_top_level: Option<&str>,
    files: &mut Vec<SourceManifestEntry>,
    directories: &mut BTreeSet<String>,
) -> Result<(), InstallWorkspaceError> {
    let mut names = directory_names(directory_fd)?;
    names.sort_by(|left, right| {
        left.as_os_str()
            .as_bytes()
            .cmp(right.as_os_str().as_bytes())
    });
    for name in names {
        let relative = relative_directory.join(&name);
        if relative_directory.as_os_str().is_empty()
            && excluded_top_level.is_some_and(|excluded| name == excluded)
        {
            let stat = statat(directory_fd, &name, AtFlags::SYMLINK_NOFOLLOW)
                .map_err(|_| InstallWorkspaceError::PreviewStale)?;
            if FileType::from_raw_mode(stat.st_mode) != FileType::Directory {
                return Err(InstallWorkspaceError::PreviewStale);
            }
            continue;
        }
        let stat = statat(directory_fd, &name, AtFlags::SYMLINK_NOFOLLOW)
            .map_err(|_| InstallWorkspaceError::PreviewStale)?;
        match FileType::from_raw_mode(stat.st_mode) {
            FileType::Directory => {
                validate_relative(&relative)?;
                directories.insert(relative_string(&relative));
                let child = openat(directory_fd, &name, directory_flags(), Mode::empty())
                    .map_err(|_| InstallWorkspaceError::PreviewStale)?;
                collect_tree_fd(&child, &relative, excluded_top_level, files, directories)?;
            }
            FileType::RegularFile => {
                files.push(capture_source_from(directory_fd, &name, &relative)?);
            }
            _ => return Err(InstallWorkspaceError::PreviewStale),
        }
    }
    Ok(())
}

fn capture_source_from(
    directory_fd: &OwnedFd,
    name: &OsString,
    relative: &Path,
) -> Result<SourceManifestEntry, InstallWorkspaceError> {
    validate_relative(relative)?;
    let fd = openat(
        directory_fd,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    let mut file = File::from(fd);
    let before = file
        .metadata()
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    if !before.file_type().is_file() {
        return Err(InstallWorkspaceError::PreviewStale);
    }
    let before_identity = identity(&before);
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    let after = file
        .metadata()
        .map_err(|_| InstallWorkspaceError::PreviewStale)?;
    if before_identity != identity(&after) || before.len() != bytes.len() as u64 {
        return Err(InstallWorkspaceError::PreviewStale);
    }
    Ok(SourceManifestEntry {
        relative_path: relative_string(relative),
        sha256: hex_sha256(&bytes),
        mode: before.mode() & 0o777,
        size: before.len(),
    })
}

fn outside_dist_files(manifest: &SourceManifest) -> Vec<&SourceManifestEntry> {
    manifest
        .entries
        .iter()
        .filter(|entry| !is_dist_path(&entry.relative_path))
        .collect()
}

fn outside_dist_directories(directories: &BTreeSet<String>) -> Vec<&str> {
    directories
        .iter()
        .map(String::as_str)
        .filter(|path| !is_dist_path(path))
        .collect()
}

fn is_dist_path(path: &str) -> bool {
    path == "dist" || path.starts_with("dist/")
}

fn validate_open_directory(
    fd: &OwnedFd,
    device: u64,
    inode: u64,
    require_owner_only: bool,
) -> Result<(), InstallWorkspaceError> {
    let descriptor = fstat(fd).map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
    if FileType::from_raw_mode(descriptor.st_mode) == FileType::Directory
        && descriptor.st_dev as u64 == device
        && descriptor.st_ino as u64 == inode
        && (!require_owner_only || descriptor.st_mode as u32 & 0o077 == 0)
    {
        Ok(())
    } else {
        Err(InstallWorkspaceError::WorkspaceUnavailable)
    }
}

fn validate_directory_mapping(
    parent_fd: &OwnedFd,
    name: &OsString,
    fd: &OwnedFd,
    device: u64,
    inode: u64,
    require_owner_only: bool,
) -> Result<(), InstallWorkspaceError> {
    validate_open_directory(fd, device, inode, require_owner_only)?;
    let path = statat(parent_fd, name, AtFlags::SYMLINK_NOFOLLOW)
        .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
    if FileType::from_raw_mode(path.st_mode) == FileType::Directory
        && path.st_dev as u64 == device
        && path.st_ino as u64 == inode
        && (!require_owner_only || path.st_mode as u32 & 0o077 == 0)
    {
        Ok(())
    } else {
        Err(InstallWorkspaceError::WorkspaceUnavailable)
    }
}

fn validate_path_identity(
    path: &Path,
    device: u64,
    inode: u64,
    require_owner_only: bool,
) -> Result<(), InstallWorkspaceError> {
    let path_fd = open_directory_lineage(path, InstallWorkspaceError::WorkspaceUnavailable)?;
    validate_open_directory(&path_fd, device, inode, require_owner_only)
}

fn remove_directory_contents_fd(directory_fd: &OwnedFd) -> Result<(), InstallWorkspaceError> {
    for name in directory_names(directory_fd)? {
        let stat = statat(directory_fd, &name, AtFlags::SYMLINK_NOFOLLOW)
            .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
        if FileType::from_raw_mode(stat.st_mode) == FileType::Directory {
            let child = openat(directory_fd, &name, directory_flags(), Mode::empty())
                .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
            remove_directory_contents_fd(&child)?;
            unlinkat(directory_fd, &name, AtFlags::REMOVEDIR)
                .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
        } else {
            unlinkat(directory_fd, &name, AtFlags::empty())
                .map_err(|_| InstallWorkspaceError::WorkspaceUnavailable)?;
        }
    }
    Ok(())
}

fn identity(metadata: &fs::Metadata) -> FileIdentity {
    FileIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
        size: metadata.len(),
        modified_seconds: metadata.mtime(),
        modified_nanoseconds: metadata.mtime_nsec(),
        mode: metadata.mode(),
    }
}

fn relative_string(path: &Path) -> String {
    path.to_str()
        .unwrap_or_default()
        .replace(std::path::MAIN_SEPARATOR, "/")
}

fn hex_sha256(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}
