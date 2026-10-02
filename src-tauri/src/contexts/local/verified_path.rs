//! Descriptor-bound resolution for Local inventory paths.

use std::ffi::OsString;
use std::fmt;
use std::os::unix::ffi::OsStringExt;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use rustix::fd::{AsFd, BorrowedFd, OwnedFd};
use rustix::ffi::{CStr, CString};
use rustix::fs::{
    fchmod, fstat, fsync, open, openat, renameat_with, statat, unlinkat, AtFlags, FileType, Mode,
    OFlags, RenameFlags, Stat,
};
use rustix::io::{pread, pwrite, Errno};
use serde_json::Value;
use sha2::{Digest, Sha256};

use super::domain::{FileIdentity, FileIdentityType, InstanceHandle};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerificationPolicy {
    StrictSnapshotIdentity,
    CurrentSourceAtLocator,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifiedPathError {
    pub code: String,
}

const MAX_REMOVAL_SOURCE_BYTES: usize = 8 * 1024 * 1024;
static REMOVAL_TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(1);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum VerifiedReadError {
    Io,
}

impl VerifiedPathError {
    fn stale() -> Self {
        Self {
            code: "stale_path_handle".to_string(),
        }
    }

    fn removal_source_too_large() -> Self {
        Self {
            code: "removal_source_too_large".to_string(),
        }
    }

    fn removal_write_failed() -> Self {
        Self {
            code: "removal_write_failed".to_string(),
        }
    }

    fn removal_state_indeterminate() -> Self {
        Self {
            code: "removal_state_indeterminate".to_string(),
        }
    }

    fn is_indeterminate(&self) -> bool {
        self.code == "removal_state_indeterminate"
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct OpenedFileIdentity {
    public: FileIdentity,
    ctime_ns: i128,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct StableMutationIdentity {
    device: u64,
    inode: u64,
    file_type: FileType,
    mode: u32,
    owner_uid: u32,
    owner_gid: u32,
    link_count: u64,
    size: u64,
    mtime_ns: i128,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ParentMutationIdentity {
    device: u64,
    inode: u64,
    mode: u32,
    owner_uid: u32,
    owner_gid: u32,
}

#[derive(Default)]
pub struct VerifiedLocalPathResolver;

pub struct VerifiedLocalFile {
    lineage: Vec<OwnedFd>,
    edge_names: Vec<CString>,
    authorized_root_index: usize,
    leaf_name: CString,
    file: OwnedFd,
    canonical_path: PathBuf,
    expected_root: (u64, u64),
    scan_identity: FileIdentity,
    scan_content_sha256: Option<[u8; 32]>,
    opened_identity: OpenedFileIdentity,
}

impl fmt::Debug for VerifiedLocalFile {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VerifiedLocalFile")
            .field("canonical_path", &"<redacted-path>")
            .field(
                "metadata_changed_since_snapshot",
                &self.metadata_changed_since_snapshot(),
            )
            .field("current_identity", &self.opened_identity.public)
            .finish()
    }
}

impl VerifiedLocalPathResolver {
    pub fn resolve(
        &self,
        handle: &InstanceHandle,
        policy: VerificationPolicy,
    ) -> Result<VerifiedLocalFile, VerifiedPathError> {
        if handle.raw_relative_components.is_empty() {
            return Err(VerifiedPathError::stale());
        }

        let filesystem_root = open(
            "/",
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::empty(),
        )
        .map_err(|_| VerifiedPathError::stale())?;
        let mut lineage = vec![filesystem_root];
        let mut edge_names = Vec::new();

        for component in handle.canonical_root.components() {
            match component {
                Component::RootDir => {}
                Component::Normal(segment) => {
                    let name = checked_name(segment.as_encoded_bytes())?;
                    let directory = open_verified_directory(
                        lineage.last().expect("filesystem root is retained").as_fd(),
                        &name,
                    )?;
                    lineage.push(directory);
                    edge_names.push(name);
                }
                _ => return Err(VerifiedPathError::stale()),
            }
        }
        let authorized_root_index = lineage.len() - 1;
        let root_stat =
            fstat(&lineage[authorized_root_index]).map_err(|_| VerifiedPathError::stale())?;
        if FileType::from_raw_mode(root_stat.st_mode) != FileType::Directory
            || root_stat.st_dev as u64 != handle.root_device
            || root_stat.st_ino != handle.root_inode
        {
            return Err(VerifiedPathError::stale());
        }

        let mut canonical_path = handle.canonical_root.clone();
        for component in &handle.raw_relative_components[..handle.raw_relative_components.len() - 1]
        {
            let name = checked_name(component)?;
            let directory = open_verified_directory(
                lineage.last().expect("authorized root is retained").as_fd(),
                &name,
            )?;
            canonical_path.push(OsString::from_vec(component.clone()));
            lineage.push(directory);
            edge_names.push(name);
        }

        let leaf_component = handle
            .raw_relative_components
            .last()
            .expect("non-empty relative components were checked");
        let leaf_name = checked_name(leaf_component)?;
        canonical_path.push(OsString::from_vec(leaf_component.clone()));
        let (file, opened_identity) = open_verified_file(
            lineage.last().expect("target parent is retained").as_fd(),
            &leaf_name,
        )?;
        if policy == VerificationPolicy::StrictSnapshotIdentity
            && opened_identity.public != handle.scan_file_identity
        {
            return Err(VerifiedPathError::stale());
        }

        let verified = VerifiedLocalFile {
            lineage,
            edge_names,
            authorized_root_index,
            leaf_name,
            file,
            canonical_path,
            expected_root: (handle.root_device, handle.root_inode),
            scan_identity: handle.scan_file_identity,
            scan_content_sha256: handle.scan_content_sha256,
            opened_identity,
        };
        verified.verify_unchanged()?;
        Ok(verified)
    }
}

impl VerifiedLocalFile {
    pub fn canonical_path(&self) -> &Path {
        &self.canonical_path
    }

    pub(crate) fn verify_single_link(&self) -> Result<(), VerifiedPathError> {
        self.verify_unchanged()?;
        if fstat(&self.file)
            .map_err(|_| VerifiedPathError::stale())?
            .st_nlink
            != 1
        {
            return Err(VerifiedPathError::stale());
        }
        Ok(())
    }

    pub fn current_identity(&self) -> FileIdentity {
        self.opened_identity.public
    }

    pub fn metadata_changed_since_snapshot(&self) -> bool {
        self.scan_identity != self.opened_identity.public
    }

    pub fn verify_unchanged(&self) -> Result<(), VerifiedPathError> {
        self.verify_parent_unchanged()?;

        let current = statat(
            self.lineage
                .last()
                .expect("target parent is retained")
                .as_fd(),
            &self.leaf_name,
            AtFlags::SYMLINK_NOFOLLOW,
        )
        .map_err(|_| VerifiedPathError::stale())?;
        let opened = fstat(&self.file).map_err(|_| VerifiedPathError::stale())?;
        if opened_file_identity(&current) != Some(self.opened_identity)
            || opened_file_identity(&opened) != Some(self.opened_identity)
        {
            return Err(VerifiedPathError::stale());
        }
        Ok(())
    }

    fn verify_parent_unchanged(&self) -> Result<(), VerifiedPathError> {
        for (index, name) in self.edge_names.iter().enumerate() {
            let current = statat(self.lineage[index].as_fd(), name, AtFlags::SYMLINK_NOFOLLOW)
                .map_err(|_| VerifiedPathError::stale())?;
            let opened = fstat(&self.lineage[index + 1]).map_err(|_| VerifiedPathError::stale())?;
            if FileType::from_raw_mode(current.st_mode) != FileType::Directory
                || FileType::from_raw_mode(opened.st_mode) != FileType::Directory
                || !same_object(&current, &opened)
            {
                return Err(VerifiedPathError::stale());
            }
        }
        let root = fstat(&self.lineage[self.authorized_root_index])
            .map_err(|_| VerifiedPathError::stale())?;
        if FileType::from_raw_mode(root.st_mode) != FileType::Directory
            || root.st_dev as u64 != self.expected_root.0
            || root.st_ino != self.expected_root.1
        {
            return Err(VerifiedPathError::stale());
        }
        Ok(())
    }

    pub(crate) fn read_at(
        &self,
        buffer: &mut [u8],
        offset: u64,
    ) -> Result<usize, VerifiedReadError> {
        loop {
            match pread(self.file.as_fd(), &mut *buffer, offset) {
                Err(Errno::INTR) => continue,
                Err(_) => return Err(VerifiedReadError::Io),
                Ok(read) => return Ok(read),
            }
        }
    }

    pub(crate) fn borrowed_fd(&self) -> BorrowedFd<'_> {
        self.file.as_fd()
    }

    /// Reads the already identity-checked regular file without reopening it by
    /// pathname. Batch-removal callers use this only while holding their
    /// exclusive mutation lease.
    pub(crate) fn read_all_for_removal(&self) -> Result<Vec<u8>, VerifiedPathError> {
        if self.scan_identity != self.opened_identity.public {
            return Err(VerifiedPathError::stale());
        }
        self.verify_unchanged()?;
        let size = usize::try_from(self.opened_identity.public.size)
            .map_err(|_| VerifiedPathError::removal_source_too_large())?;
        if size > MAX_REMOVAL_SOURCE_BYTES {
            return Err(VerifiedPathError::removal_source_too_large());
        }

        let mut bytes = Vec::with_capacity(size);
        let mut scratch = [0_u8; 16 * 1024];
        let mut offset = 0_u64;
        loop {
            let wanted = scratch
                .len()
                .min(MAX_REMOVAL_SOURCE_BYTES + 1 - bytes.len());
            let read = loop {
                match pread(self.file.as_fd(), &mut scratch[..wanted], offset) {
                    Err(Errno::INTR) => continue,
                    Err(_) => return Err(VerifiedPathError::stale()),
                    Ok(read) => break read,
                }
            };
            if read == 0 {
                break;
            }
            bytes.extend_from_slice(&scratch[..read]);
            if bytes.len() > MAX_REMOVAL_SOURCE_BYTES {
                return Err(VerifiedPathError::removal_source_too_large());
            }
            offset += read as u64;
        }
        self.verify_unchanged()?;
        if bytes.len() != size {
            return Err(VerifiedPathError::stale());
        }
        Ok(bytes)
    }

    /// Atomically replaces this verified leaf with `bytes` in the same
    /// directory. The resolver retains the verified parent descriptor and
    /// never accepts a caller-supplied path.
    pub(crate) fn replace_atomically_for_removal(
        &self,
        bytes: &[u8],
    ) -> Result<(), VerifiedPathError> {
        let expected_semantics: Value =
            serde_json::from_slice(bytes).map_err(|_| VerifiedPathError::removal_write_failed())?;
        let expected_digest: [u8; 32] = Sha256::digest(bytes).into();
        self.verify_removal_source_unchanged()?;
        let parent = self
            .lineage
            .last()
            .expect("target parent is retained")
            .as_fd();
        let parent_identity = parent_mutation_identity(parent)?;
        let expected_identity =
            stable_mutation_identity(&fstat(&self.file).map_err(|_| VerifiedPathError::stale())?)?;
        let (temporary, temporary_file) = create_removal_temporary(parent, expected_identity.mode)?;
        let result = (|| {
            write_all_at(temporary_file.as_fd(), bytes)?;
            fchmod(
                temporary_file.as_fd(),
                Mode::from(expected_identity.mode as u16),
            )
            .map_err(|_| VerifiedPathError::removal_write_failed())?;
            fsync(temporary_file.as_fd()).map_err(|_| VerifiedPathError::removal_write_failed())?;
            let replacement_identity = stable_mutation_identity(
                &fstat(&temporary_file).map_err(|_| VerifiedPathError::removal_write_failed())?,
            )?;
            if replacement_identity.size != bytes.len() as u64
                || replacement_identity.mode != expected_identity.mode
                || replacement_identity.owner_uid != expected_identity.owner_uid
                || replacement_identity.owner_gid != expected_identity.owner_gid
                || replacement_identity.link_count != 1
            {
                return Err(VerifiedPathError::removal_write_failed());
            }
            self.verify_removal_source_unchanged()?;
            verify_parent_identity(parent, parent_identity)?;
            exchange_rewritten_leaf_checked(
                parent,
                &self.leaf_name,
                &temporary,
                self.file.as_fd(),
                expected_identity,
                self.scan_content_sha256
                    .ok_or_else(VerifiedPathError::stale)?,
                temporary_file.as_fd(),
                replacement_identity,
                bytes,
                expected_digest,
                &expected_semantics,
                parent_identity,
            )?;

            if let Err(error) =
                unlink_name_if_matches(parent, &temporary, self.file.as_fd(), expected_identity)
            {
                rollback_known_exchange(
                    parent,
                    &self.leaf_name,
                    &temporary,
                    replacement_identity,
                    expected_identity,
                    parent_identity,
                )?;
                return Err(error);
            }

            // Some supported macOS filesystems reject directory fsync even after
            // successful namespace operations. Reopen/digest/semantic validation
            // is the success gate, so retain the durable file fsync without
            // turning an already-applied replace into a false failure.
            let _ = fsync(parent);
            Ok(())
        })();
        if result.is_err() {
            let _ = unlink_name_if_matches(
                parent,
                &temporary,
                temporary_file.as_fd(),
                stable_mutation_identity(
                    &fstat(&temporary_file)
                        .map_err(|_| VerifiedPathError::removal_write_failed())?,
                )?,
            );
        }
        result
    }

    /// Removes only the already-opened verified regular-file leaf. It never
    /// receives a path from the caller and never requests directory removal.
    pub(crate) fn unlink_for_removal(&self) -> Result<(), VerifiedPathError> {
        self.verify_removal_source_unchanged()?;
        let parent = self
            .lineage
            .last()
            .expect("target parent is retained")
            .as_fd();
        let parent_identity = parent_mutation_identity(parent)?;
        let expected_identity =
            stable_mutation_identity(&fstat(&self.file).map_err(|_| VerifiedPathError::stale())?)?;
        let (guard, guard_file) = create_removal_temporary(parent, 0o600)?;
        fsync(guard_file.as_fd()).map_err(|_| VerifiedPathError::removal_write_failed())?;
        let guard_identity = stable_mutation_identity(
            &fstat(&guard_file).map_err(|_| VerifiedPathError::removal_write_failed())?,
        )?;
        let result = (|| {
            self.verify_removal_source_unchanged()?;
            verify_parent_identity(parent, parent_identity)?;
            exchange_leaf_checked(
                parent,
                &self.leaf_name,
                &guard,
                expected_identity,
                guard_identity,
                parent_identity,
            )?;

            if let Err(error) = verify_fd_digest(
                self.file.as_fd(),
                self.scan_content_sha256
                    .ok_or_else(VerifiedPathError::stale)?,
            ) {
                rollback_known_exchange(
                    parent,
                    &self.leaf_name,
                    &guard,
                    guard_identity,
                    expected_identity,
                    parent_identity,
                )?;
                return Err(error);
            }

            let retired = match move_verified_leaf_to_unique_quarantine(
                parent,
                &self.leaf_name,
                guard_identity,
                parent_identity,
            ) {
                Ok(retired) => retired,
                Err(error) if error.is_indeterminate() => return Err(error),
                Err(error) => {
                    rollback_known_exchange(
                        parent,
                        &self.leaf_name,
                        &guard,
                        guard_identity,
                        expected_identity,
                        parent_identity,
                    )?;
                    return Err(error);
                }
            };

            if let Err(error) =
                unlink_name_if_matches(parent, &guard, self.file.as_fd(), expected_identity)
            {
                restore_delete_staging(
                    parent,
                    &self.leaf_name,
                    &guard,
                    &retired,
                    guard_identity,
                    expected_identity,
                    parent_identity,
                )?;
                return Err(error);
            }
            // The expected inode unlink is the commit point. The public leaf
            // absence and parent identity were already validated while both
            // rollback entries still existed. Retired guard cleanup is
            // post-commit and therefore must not turn a committed deletion
            // into a misleading failed_unchanged outcome.
            let _ = unlink_name_if_matches(parent, &retired, guard_file.as_fd(), guard_identity);
            Ok(())
        })();
        let _ = fsync(parent);
        if result.is_err() {
            let _ = unlink_name_if_matches(parent, &guard, guard_file.as_fd(), guard_identity);
        }
        result
    }

    fn verify_removal_source_unchanged(&self) -> Result<(), VerifiedPathError> {
        if self.scan_identity != self.opened_identity.public {
            return Err(VerifiedPathError::stale());
        }
        self.verify_unchanged()?;
        let expected_digest = self
            .scan_content_sha256
            .ok_or_else(VerifiedPathError::stale)?;
        verify_fd_digest(self.file.as_fd(), expected_digest)
    }
}

fn create_removal_temporary(
    parent: BorrowedFd<'_>,
    mode: u32,
) -> Result<(CString, OwnedFd), VerifiedPathError> {
    for _ in 0..128 {
        let sequence = REMOVAL_TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let name = CString::new(format!(
            ".harnesskit-removal.{}.{}.tmp",
            std::process::id(),
            sequence
        ))
        .map_err(|_| VerifiedPathError::removal_write_failed())?;
        match openat(
            parent,
            &name,
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::from(mode as u16),
        ) {
            Ok(file) => {
                let stat = fstat(&file).map_err(|_| VerifiedPathError::removal_write_failed())?;
                if FileType::from_raw_mode(stat.st_mode) != FileType::RegularFile {
                    let _ = unlinkat(parent, &name, AtFlags::empty());
                    return Err(VerifiedPathError::removal_write_failed());
                }
                return Ok((name, file));
            }
            Err(Errno::EXIST) => continue,
            Err(_) => return Err(VerifiedPathError::removal_write_failed()),
        }
    }
    Err(VerifiedPathError::removal_write_failed())
}

fn exchange_leaf_checked(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
    temporary: &CStr,
    expected_leaf: StableMutationIdentity,
    expected_temporary: StableMutationIdentity,
    expected_parent: ParentMutationIdentity,
) -> Result<(), VerifiedPathError> {
    // On macOS rustix maps EXCHANGE to renameatx_np(RENAME_SWAP). The kernel
    // swaps both directory entries atomically, which lets the displaced leaf
    // serve as the compare-and-swap observation. There is deliberately no
    // renameat/unlinkat fallback when the volume does not support RENAME_SWAP.
    renameat_with(parent, temporary, parent, leaf, RenameFlags::EXCHANGE)
        .map_err(|_| VerifiedPathError::removal_write_failed())?;

    validate_exchanged_leaf(
        parent,
        leaf,
        temporary,
        expected_leaf,
        expected_temporary,
        expected_parent,
    )
}

fn validate_exchanged_leaf(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
    temporary: &CStr,
    expected_leaf: StableMutationIdentity,
    expected_temporary: StableMutationIdentity,
    expected_parent: ParentMutationIdentity,
) -> Result<(), VerifiedPathError> {
    let leaf_after = observed_name_identity(parent, leaf)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    // A competitor replaced the canonical entry after the atomic exchange.
    // Swapping now would hide or overwrite that external leaf, so leave both
    // names untouched and surface an outcome that must not be called unchanged.
    if leaf_after != expected_temporary {
        return Err(VerifiedPathError::removal_state_indeterminate());
    }
    let temporary_after = observed_name_identity(parent, temporary)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    let exchange_matches = leaf_after == expected_temporary
        && temporary_after == expected_leaf
        && verify_parent_identity(parent, expected_parent).is_ok();
    if exchange_matches {
        return Ok(());
    }

    rollback_observed_exchange(
        parent,
        leaf,
        temporary,
        leaf_after,
        temporary_after,
        expected_parent,
    )
    .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    Err(VerifiedPathError::stale())
}

fn rollback_known_exchange(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
    temporary: &CStr,
    leaf_identity: StableMutationIdentity,
    temporary_identity: StableMutationIdentity,
    expected_parent: ParentMutationIdentity,
) -> Result<(), VerifiedPathError> {
    rollback_observed_exchange(
        parent,
        leaf,
        temporary,
        leaf_identity,
        temporary_identity,
        expected_parent,
    )
}

fn rollback_observed_exchange(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
    temporary: &CStr,
    leaf_after_exchange: StableMutationIdentity,
    temporary_after_exchange: StableMutationIdentity,
    expected_parent: ParentMutationIdentity,
) -> Result<(), VerifiedPathError> {
    let current_leaf = observed_name_identity(parent, leaf)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    let current_temporary = observed_name_identity(parent, temporary)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    if current_leaf != leaf_after_exchange || current_temporary != temporary_after_exchange {
        return Err(VerifiedPathError::removal_state_indeterminate());
    }
    renameat_with(parent, temporary, parent, leaf, RenameFlags::EXCHANGE)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    let restored_leaf = observed_name_identity(parent, leaf)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    let restored_temporary = observed_name_identity(parent, temporary)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    if restored_leaf != temporary_after_exchange || restored_temporary != leaf_after_exchange {
        return Err(VerifiedPathError::removal_state_indeterminate());
    }
    verify_parent_identity(parent, expected_parent)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())
}

#[allow(clippy::too_many_arguments)]
fn exchange_rewritten_leaf_checked(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
    temporary: &CStr,
    original_fd: BorrowedFd<'_>,
    original_identity: StableMutationIdentity,
    original_digest: [u8; 32],
    replacement_fd: BorrowedFd<'_>,
    replacement_identity: StableMutationIdentity,
    expected_bytes: &[u8],
    expected_digest: [u8; 32],
    expected_semantics: &Value,
    expected_parent: ParentMutationIdentity,
) -> Result<(), VerifiedPathError> {
    exchange_leaf_checked(
        parent,
        leaf,
        temporary,
        original_identity,
        replacement_identity,
        expected_parent,
    )?;
    let validation = verify_fd_digest(original_fd, original_digest).and_then(|()| {
        validate_rewritten_leaf(
            parent,
            leaf,
            replacement_fd,
            replacement_identity,
            expected_bytes,
            expected_digest,
            expected_semantics,
            expected_parent,
        )
    });
    if let Err(error) = validation {
        rollback_known_exchange(
            parent,
            leaf,
            temporary,
            replacement_identity,
            original_identity,
            expected_parent,
        )?;
        return Err(error);
    }
    Ok(())
}

fn validate_rewritten_leaf(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
    replacement_fd: BorrowedFd<'_>,
    expected_identity: StableMutationIdentity,
    expected_bytes: &[u8],
    expected_digest: [u8; 32],
    expected_semantics: &Value,
    expected_parent: ParentMutationIdentity,
) -> Result<(), VerifiedPathError> {
    verify_parent_identity(parent, expected_parent)?;
    let (reopened, _) =
        open_verified_file(parent, leaf).map_err(|_| VerifiedPathError::removal_write_failed())?;
    let reopened_identity = stable_mutation_identity(
        &fstat(&reopened).map_err(|_| VerifiedPathError::removal_write_failed())?,
    )?;
    let descriptor_identity = stable_mutation_identity(
        &fstat(replacement_fd).map_err(|_| VerifiedPathError::removal_write_failed())?,
    )?;
    if reopened_identity != expected_identity || descriptor_identity != expected_identity {
        return Err(VerifiedPathError::removal_write_failed());
    }
    let reopened_bytes = read_fd_bounded(reopened.as_fd())?;
    let reopened_digest: [u8; 32] = Sha256::digest(&reopened_bytes).into();
    if reopened_digest != expected_digest || reopened_bytes.len() != expected_bytes.len() {
        return Err(VerifiedPathError::removal_write_failed());
    }
    let reopened_semantics: Value = serde_json::from_slice(&reopened_bytes)
        .map_err(|_| VerifiedPathError::removal_write_failed())?;
    if reopened_semantics != *expected_semantics {
        return Err(VerifiedPathError::removal_write_failed());
    }
    verify_parent_identity(parent, expected_parent)
}

fn move_verified_leaf_to_unique_quarantine(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
    expected_guard: StableMutationIdentity,
    expected_parent: ParentMutationIdentity,
) -> Result<CString, VerifiedPathError> {
    let current_leaf = observed_name_identity(parent, leaf)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    if current_leaf != expected_guard {
        return Err(VerifiedPathError::removal_state_indeterminate());
    }
    verify_parent_identity(parent, expected_parent)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;

    let retired = move_leaf_to_unique_quarantine_unchecked(parent, leaf)?;
    validate_quarantined_leaf_or_restore_external(
        parent,
        leaf,
        &retired,
        expected_guard,
        expected_parent,
    )?;
    Ok(retired)
}

fn move_leaf_to_unique_quarantine_unchecked(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
) -> Result<CString, VerifiedPathError> {
    // The public locator is never unlinked by name. After the verified swap it
    // contains only our held guard inode; moving that inode with NOREPLACE
    // creates an absence gate without overwriting or deleting another leaf.
    for _ in 0..128 {
        let sequence = REMOVAL_TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let name = CString::new(format!(
            ".harnesskit-removal.{}.{}.retired",
            std::process::id(),
            sequence
        ))
        .map_err(|_| VerifiedPathError::removal_write_failed())?;
        match renameat_with(parent, leaf, parent, &name, RenameFlags::NOREPLACE) {
            Ok(()) => return Ok(name),
            Err(Errno::EXIST) => continue,
            Err(_) => return Err(VerifiedPathError::removal_write_failed()),
        }
    }
    Err(VerifiedPathError::removal_write_failed())
}

fn validate_quarantined_leaf_or_restore_external(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
    retired: &CStr,
    expected_guard: StableMutationIdentity,
    expected_parent: ParentMutationIdentity,
) -> Result<(), VerifiedPathError> {
    let retired_identity = observed_name_identity(parent, retired)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    if retired_identity != expected_guard {
        return restore_unexpected_quarantined_leaf(parent, leaf, retired, retired_identity);
    }
    if !leaf_is_absent(parent, leaf) || verify_parent_identity(parent, expected_parent).is_err() {
        // The guard is safely retired, but a competitor now owns the canonical
        // locator. Never move, swap, or unlink that external entry.
        return Err(VerifiedPathError::removal_state_indeterminate());
    }
    Ok(())
}

fn restore_unexpected_quarantined_leaf(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
    retired: &CStr,
    external_identity: StableMutationIdentity,
) -> Result<(), VerifiedPathError> {
    if !leaf_is_absent(parent, leaf) {
        return Err(VerifiedPathError::removal_state_indeterminate());
    }
    renameat_with(parent, retired, parent, leaf, RenameFlags::NOREPLACE)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    let restored = observed_name_identity(parent, leaf)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    if restored != external_identity || !leaf_is_absent(parent, retired) {
        return Err(VerifiedPathError::removal_state_indeterminate());
    }
    Err(VerifiedPathError::removal_state_indeterminate())
}

fn restore_delete_staging(
    parent: BorrowedFd<'_>,
    leaf: &CStr,
    guard: &CStr,
    retired: &CStr,
    guard_identity: StableMutationIdentity,
    expected_identity: StableMutationIdentity,
    expected_parent: ParentMutationIdentity,
) -> Result<(), VerifiedPathError> {
    let retired_identity = observed_name_identity(parent, retired)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    let staged_original = observed_name_identity(parent, guard)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    if !leaf_is_absent(parent, leaf)
        || retired_identity != guard_identity
        || staged_original != expected_identity
    {
        return Err(VerifiedPathError::removal_state_indeterminate());
    }
    renameat_with(parent, retired, parent, leaf, RenameFlags::NOREPLACE)
        .map_err(|_| VerifiedPathError::removal_state_indeterminate())?;
    rollback_known_exchange(
        parent,
        leaf,
        guard,
        guard_identity,
        expected_identity,
        expected_parent,
    )
}

fn unlink_name_if_matches(
    parent: BorrowedFd<'_>,
    name: &CStr,
    expected_fd: BorrowedFd<'_>,
    expected_identity: StableMutationIdentity,
) -> Result<(), VerifiedPathError> {
    let descriptor_identity = stable_mutation_identity(
        &fstat(expected_fd).map_err(|_| VerifiedPathError::removal_write_failed())?,
    )?;
    if expected_identity.file_type != FileType::RegularFile
        || descriptor_identity != expected_identity
        || observed_name_identity(parent, name)? != expected_identity
    {
        return Err(VerifiedPathError::removal_write_failed());
    }
    unlinkat(parent, name, AtFlags::empty()).map_err(|_| VerifiedPathError::removal_write_failed())
}

fn verify_fd_digest(
    file: BorrowedFd<'_>,
    expected_digest: [u8; 32],
) -> Result<(), VerifiedPathError> {
    let bytes = read_fd_bounded(file)?;
    let digest: [u8; 32] = Sha256::digest(bytes).into();
    (digest == expected_digest)
        .then_some(())
        .ok_or_else(VerifiedPathError::stale)
}

fn read_fd_bounded(file: BorrowedFd<'_>) -> Result<Vec<u8>, VerifiedPathError> {
    let before = fstat(file).map_err(|_| VerifiedPathError::removal_write_failed())?;
    let before_identity =
        opened_file_identity(&before).ok_or_else(VerifiedPathError::removal_write_failed)?;
    let size = usize::try_from(before_identity.public.size)
        .map_err(|_| VerifiedPathError::removal_source_too_large())?;
    if size > MAX_REMOVAL_SOURCE_BYTES {
        return Err(VerifiedPathError::removal_source_too_large());
    }
    let mut bytes = vec![0_u8; size];
    let mut offset = 0_u64;
    while offset < size as u64 {
        let read = loop {
            match pread(file, &mut bytes[offset as usize..], offset) {
                Err(Errno::INTR) => continue,
                Err(_) => return Err(VerifiedPathError::removal_write_failed()),
                Ok(read) => break read,
            }
        };
        if read == 0 {
            return Err(VerifiedPathError::removal_write_failed());
        }
        offset += read as u64;
    }
    let after = fstat(file).map_err(|_| VerifiedPathError::removal_write_failed())?;
    if opened_file_identity(&after) != Some(before_identity) {
        return Err(VerifiedPathError::stale());
    }
    Ok(bytes)
}

fn observed_name_identity(
    parent: BorrowedFd<'_>,
    name: &CStr,
) -> Result<StableMutationIdentity, VerifiedPathError> {
    let stat = statat(parent, name, AtFlags::SYMLINK_NOFOLLOW)
        .map_err(|_| VerifiedPathError::removal_write_failed())?;
    stable_mutation_identity(&stat)
}

fn leaf_is_absent(parent: BorrowedFd<'_>, leaf: &CStr) -> bool {
    matches!(
        statat(parent, leaf, AtFlags::SYMLINK_NOFOLLOW),
        Err(Errno::NOENT)
    )
}

fn stable_mutation_identity(stat: &Stat) -> Result<StableMutationIdentity, VerifiedPathError> {
    Ok(StableMutationIdentity {
        device: stat.st_dev as u64,
        inode: stat.st_ino,
        file_type: FileType::from_raw_mode(stat.st_mode),
        mode: stat.st_mode as u32 & 0o7777,
        owner_uid: stat.st_uid,
        owner_gid: stat.st_gid,
        link_count: u64::from(stat.st_nlink),
        size: u64::try_from(stat.st_size).unwrap_or(u64::MAX),
        mtime_ns: i128::from(stat.st_mtime)
            .saturating_mul(1_000_000_000)
            .saturating_add(i128::from(stat.st_mtime_nsec)),
    })
}

fn parent_mutation_identity(
    parent: BorrowedFd<'_>,
) -> Result<ParentMutationIdentity, VerifiedPathError> {
    let stat = fstat(parent).map_err(|_| VerifiedPathError::stale())?;
    if FileType::from_raw_mode(stat.st_mode) != FileType::Directory {
        return Err(VerifiedPathError::stale());
    }
    Ok(ParentMutationIdentity {
        device: stat.st_dev as u64,
        inode: stat.st_ino,
        mode: stat.st_mode as u32 & 0o7777,
        owner_uid: stat.st_uid,
        owner_gid: stat.st_gid,
    })
}

fn verify_parent_identity(
    parent: BorrowedFd<'_>,
    expected: ParentMutationIdentity,
) -> Result<(), VerifiedPathError> {
    (parent_mutation_identity(parent)? == expected)
        .then_some(())
        .ok_or_else(VerifiedPathError::stale)
}

fn write_all_at(file: BorrowedFd<'_>, bytes: &[u8]) -> Result<(), VerifiedPathError> {
    let mut offset = 0_u64;
    while offset < bytes.len() as u64 {
        let written = loop {
            match pwrite(file, &bytes[offset as usize..], offset) {
                Err(Errno::INTR) => continue,
                Err(_) => return Err(VerifiedPathError::removal_write_failed()),
                Ok(written) => break written,
            }
        };
        if written == 0 {
            return Err(VerifiedPathError::removal_write_failed());
        }
        offset += written as u64;
    }
    Ok(())
}

fn open_verified_directory(
    parent: BorrowedFd<'_>,
    name: &CStr,
) -> Result<OwnedFd, VerifiedPathError> {
    let before =
        statat(parent, name, AtFlags::SYMLINK_NOFOLLOW).map_err(|_| VerifiedPathError::stale())?;
    if FileType::from_raw_mode(before.st_mode) != FileType::Directory {
        return Err(VerifiedPathError::stale());
    }
    let directory = openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| VerifiedPathError::stale())?;
    let opened = fstat(&directory).map_err(|_| VerifiedPathError::stale())?;
    let after =
        statat(parent, name, AtFlags::SYMLINK_NOFOLLOW).map_err(|_| VerifiedPathError::stale())?;
    if FileType::from_raw_mode(opened.st_mode) != FileType::Directory
        || FileType::from_raw_mode(after.st_mode) != FileType::Directory
        || !same_object(&before, &opened)
        || !same_object(&opened, &after)
    {
        return Err(VerifiedPathError::stale());
    }
    Ok(directory)
}

fn open_verified_file(
    parent: BorrowedFd<'_>,
    name: &CStr,
) -> Result<(OwnedFd, OpenedFileIdentity), VerifiedPathError> {
    let before =
        statat(parent, name, AtFlags::SYMLINK_NOFOLLOW).map_err(|_| VerifiedPathError::stale())?;
    let file = openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| VerifiedPathError::stale())?;
    let opened = fstat(&file).map_err(|_| VerifiedPathError::stale())?;
    let after =
        statat(parent, name, AtFlags::SYMLINK_NOFOLLOW).map_err(|_| VerifiedPathError::stale())?;
    let Some(before_identity) = opened_file_identity(&before) else {
        return Err(VerifiedPathError::stale());
    };
    let Some(opened_identity) = opened_file_identity(&opened) else {
        return Err(VerifiedPathError::stale());
    };
    let Some(after_identity) = opened_file_identity(&after) else {
        return Err(VerifiedPathError::stale());
    };
    if before_identity != opened_identity || opened_identity != after_identity {
        return Err(VerifiedPathError::stale());
    }
    Ok((file, opened_identity))
}

fn checked_name(bytes: &[u8]) -> Result<CString, VerifiedPathError> {
    if bytes.is_empty()
        || bytes == b"."
        || bytes == b".."
        || bytes.contains(&b'/')
        || bytes.contains(&0)
    {
        return Err(VerifiedPathError::stale());
    }
    CString::new(bytes).map_err(|_| VerifiedPathError::stale())
}

fn same_object(left: &Stat, right: &Stat) -> bool {
    left.st_dev == right.st_dev
        && left.st_ino == right.st_ino
        && FileType::from_raw_mode(left.st_mode) == FileType::from_raw_mode(right.st_mode)
}

fn opened_file_identity(stat: &Stat) -> Option<OpenedFileIdentity> {
    (FileType::from_raw_mode(stat.st_mode) == FileType::RegularFile).then(|| OpenedFileIdentity {
        public: FileIdentity {
            device: stat.st_dev as u64,
            inode: stat.st_ino,
            file_type: FileIdentityType::Regular,
            size: u64::try_from(stat.st_size).unwrap_or(u64::MAX),
            mtime_ns: i128::from(stat.st_mtime)
                .saturating_mul(1_000_000_000)
                .saturating_add(i128::from(stat.st_mtime_nsec)),
            mode: stat.st_mode as u32 & 0o7777,
            owner_uid: stat.st_uid as u32,
            owner_gid: stat.st_gid as u32,
            link_count: stat.st_nlink as u64,
            ctime_ns: i128::from(stat.st_ctime)
                .saturating_mul(1_000_000_000)
                .saturating_add(i128::from(stat.st_ctime_nsec)),
        },
        ctime_ns: i128::from(stat.st_ctime)
            .saturating_mul(1_000_000_000)
            .saturating_add(i128::from(stat.st_ctime_nsec)),
    })
}

#[cfg(test)]
mod removal_mutation_tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    use crate::contexts::local::ParserId;
    use tempfile::tempdir;

    fn open_test_parent(path: &Path) -> OwnedFd {
        open(
            path,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::empty(),
        )
        .expect("open test parent")
    }

    fn open_test_file(parent: BorrowedFd<'_>, name: &CStr) -> OwnedFd {
        openat(
            parent,
            name,
            OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::empty(),
        )
        .expect("open test file")
    }

    fn test_identity(file: BorrowedFd<'_>) -> StableMutationIdentity {
        stable_mutation_identity(&fstat(file).expect("test file stat")).expect("test identity")
    }

    fn snapshot_handle(root: &Path, leaf: &str, source: &[u8]) -> InstanceHandle {
        let canonical_root = fs::canonicalize(root).expect("canonical root");
        let root_metadata = fs::metadata(&canonical_root).expect("root metadata");
        let parent = open_test_parent(&canonical_root);
        let leaf_name = CString::new(leaf).expect("test leaf");
        let file = open_test_file(parent.as_fd(), &leaf_name);
        let scan_file_identity = opened_file_identity(&fstat(file.as_fd()).expect("source stat"))
            .expect("regular source")
            .public;

        InstanceHandle {
            canonical_root,
            root_device: root_metadata.dev(),
            root_inode: root_metadata.ino(),
            raw_relative_components: vec![leaf.as_bytes().to_vec()],
            config_entry_locator: None,
            source_parser_id: ParserId::SkillFrontmatterV1,
            scan_content_sha256: Some(Sha256::digest(source).into()),
            scan_file_identity,
        }
    }

    fn set_mode(path: &Path, mode: u32) {
        let mut permissions = fs::metadata(path).expect("source metadata").permissions();
        permissions.set_mode(mode);
        fs::set_permissions(path, permissions).expect("set source mode");
    }

    #[test]
    fn strict_snapshot_rejects_mode_change_after_scan_with_unchanged_content() {
        let fixture = tempdir().expect("temporary fixture");
        let source = fixture.path().join("source.json");
        let bytes = br#"{\"source\":\"original\"}"#;
        fs::write(&source, bytes).expect("source");
        set_mode(&source, 0o600);
        let handle = snapshot_handle(fixture.path(), "source.json", bytes);

        set_mode(&source, 0o640);

        let error = VerifiedLocalPathResolver
            .resolve(&handle, VerificationPolicy::StrictSnapshotIdentity)
            .expect_err("mode change must invalidate strict scan identity");
        assert_eq!(error.code, "stale_path_handle");
        assert_eq!(fs::read(&source).unwrap(), bytes);
    }

    #[test]
    fn removal_rechecks_mode_change_before_mutating_source() {
        let fixture = tempdir().expect("temporary fixture");
        let source = fixture.path().join("source.json");
        let bytes = br#"{\"source\":\"original\"}"#;
        fs::write(&source, bytes).expect("source");
        set_mode(&source, 0o600);
        let handle = snapshot_handle(fixture.path(), "source.json", bytes);
        let file = VerifiedLocalPathResolver
            .resolve(&handle, VerificationPolicy::StrictSnapshotIdentity)
            .expect("scan identity is initially current");

        set_mode(&source, 0o640);

        let error = file
            .unlink_for_removal()
            .expect_err("mode change must block removal before mutation");
        assert_eq!(error.code, "stale_path_handle");
        assert_eq!(fs::read(&source).unwrap(), bytes);
    }

    #[test]
    fn exchange_cas_rolls_back_when_leaf_was_swapped_after_verification() {
        let fixture = tempdir().expect("temporary fixture");
        let target = fixture.path().join("target.json");
        let held_original = fixture.path().join("held-original.json");
        let attacker = fixture.path().join("attacker.json");
        let replacement = fixture.path().join("replacement.json");
        fs::write(&target, br#"{"source":"original"}"#).expect("original");
        fs::write(&attacker, br#"{"source":"attacker"}"#).expect("attacker");
        fs::write(&replacement, br#"{"source":"replacement"}"#).expect("replacement");

        let parent = open_test_parent(fixture.path());
        let leaf_name = CString::new("target.json").unwrap();
        let replacement_name = CString::new("replacement.json").unwrap();
        let original_fd = open_test_file(parent.as_fd(), &leaf_name);
        let replacement_fd = open_test_file(parent.as_fd(), &replacement_name);
        let original_identity = test_identity(original_fd.as_fd());
        let replacement_identity = test_identity(replacement_fd.as_fd());
        let parent_identity = parent_mutation_identity(parent.as_fd()).unwrap();

        fs::rename(&target, &held_original).expect("move verified leaf away");
        fs::rename(&attacker, &target).expect("swap attacker into locator");

        let error = exchange_leaf_checked(
            parent.as_fd(),
            &leaf_name,
            &replacement_name,
            original_identity,
            replacement_identity,
            parent_identity,
        )
        .expect_err("CAS must reject the wrong displaced leaf");
        assert_eq!(error.code, "stale_path_handle");
        assert_eq!(fs::read(&target).unwrap(), br#"{"source":"attacker"}"#);
        assert_eq!(
            fs::read(&held_original).unwrap(),
            br#"{"source":"original"}"#
        );
        assert_eq!(
            fs::read(&replacement).unwrap(),
            br#"{"source":"replacement"}"#
        );
    }

    #[test]
    fn exchange_validation_never_rolls_back_over_a_post_exchange_competitor_leaf() {
        let fixture = tempdir().expect("temporary fixture");
        let target = fixture.path().join("target.json");
        let replacement = fixture.path().join("replacement.json");
        let displaced_replacement = fixture.path().join("displaced-replacement.json");
        let competitor = fixture.path().join("competitor.json");
        fs::write(&target, br#"{"source":"original"}"#).expect("original");
        fs::write(&replacement, br#"{"source":"replacement"}"#).expect("replacement");
        fs::write(&competitor, br#"{"source":"competitor"}"#).expect("competitor");

        let parent = open_test_parent(fixture.path());
        let leaf_name = CString::new("target.json").unwrap();
        let replacement_name = CString::new("replacement.json").unwrap();
        let original_fd = open_test_file(parent.as_fd(), &leaf_name);
        let replacement_fd = open_test_file(parent.as_fd(), &replacement_name);
        let original_identity = test_identity(original_fd.as_fd());
        let replacement_identity = test_identity(replacement_fd.as_fd());
        let parent_identity = parent_mutation_identity(parent.as_fd()).unwrap();

        renameat_with(
            parent.as_fd(),
            &replacement_name,
            parent.as_fd(),
            &leaf_name,
            RenameFlags::EXCHANGE,
        )
        .expect("initial atomic exchange");
        fs::rename(&target, &displaced_replacement).expect("competitor moves replacement");
        fs::rename(&competitor, &target).expect("competitor claims canonical leaf");

        let error = validate_exchanged_leaf(
            parent.as_fd(),
            &leaf_name,
            &replacement_name,
            original_identity,
            replacement_identity,
            parent_identity,
        )
        .expect_err("post-exchange competitor must make state indeterminate");
        assert_eq!(error.code, "removal_state_indeterminate");
        assert_eq!(fs::read(&target).unwrap(), br#"{"source":"competitor"}"#);
        assert_eq!(fs::read(&replacement).unwrap(), br#"{"source":"original"}"#);
        assert_eq!(
            fs::read(&displaced_replacement).unwrap(),
            br#"{"source":"replacement"}"#
        );
    }

    #[test]
    fn quarantine_post_precheck_race_restores_the_external_leaf_to_canonical() {
        let fixture = tempdir().expect("temporary fixture");
        let target = fixture.path().join("target.json");
        let displaced_guard = fixture.path().join("displaced-guard.json");
        let competitor = fixture.path().join("competitor.json");
        fs::write(&target, br#"{"source":"guard"}"#).expect("guard");
        fs::write(&competitor, br#"{"source":"competitor"}"#).expect("competitor");

        let parent = open_test_parent(fixture.path());
        let leaf_name = CString::new("target.json").unwrap();
        let guard_fd = open_test_file(parent.as_fd(), &leaf_name);
        let guard_identity = test_identity(guard_fd.as_fd());
        let parent_identity = parent_mutation_identity(parent.as_fd()).unwrap();
        assert_eq!(
            observed_name_identity(parent.as_fd(), &leaf_name).unwrap(),
            guard_identity,
            "production precheck"
        );

        fs::rename(&target, &displaced_guard).expect("competitor moves guard");
        fs::rename(&competitor, &target).expect("competitor claims canonical leaf");
        let retired = move_leaf_to_unique_quarantine_unchecked(parent.as_fd(), &leaf_name)
            .expect("racing rename moved competitor");
        let error = validate_quarantined_leaf_or_restore_external(
            parent.as_fd(),
            &leaf_name,
            &retired,
            guard_identity,
            parent_identity,
        )
        .expect_err("unexpected retired leaf must be restored and rejected");

        assert_eq!(error.code, "removal_state_indeterminate");
        assert_eq!(fs::read(&target).unwrap(), br#"{"source":"competitor"}"#);
        assert_eq!(
            fs::read(&displaced_guard).unwrap(),
            br#"{"source":"guard"}"#
        );
        assert!(leaf_is_absent(parent.as_fd(), &retired));
    }

    #[test]
    fn quarantine_postrename_competitor_leaf_is_never_overwritten_or_deleted() {
        let fixture = tempdir().expect("temporary fixture");
        let target = fixture.path().join("target.json");
        let competitor = fixture.path().join("competitor.json");
        fs::write(&target, br#"{"source":"guard"}"#).expect("guard");
        fs::write(&competitor, br#"{"source":"competitor"}"#).expect("competitor");

        let parent = open_test_parent(fixture.path());
        let leaf_name = CString::new("target.json").unwrap();
        let guard_fd = open_test_file(parent.as_fd(), &leaf_name);
        let guard_identity = test_identity(guard_fd.as_fd());
        let parent_identity = parent_mutation_identity(parent.as_fd()).unwrap();
        let retired = move_leaf_to_unique_quarantine_unchecked(parent.as_fd(), &leaf_name)
            .expect("guard moved to retired name");
        fs::rename(&competitor, &target).expect("competitor claims absent canonical leaf");

        let error = validate_quarantined_leaf_or_restore_external(
            parent.as_fd(),
            &leaf_name,
            &retired,
            guard_identity,
            parent_identity,
        )
        .expect_err("postrename competitor must make state indeterminate");
        assert_eq!(error.code, "removal_state_indeterminate");
        assert_eq!(fs::read(&target).unwrap(), br#"{"source":"competitor"}"#);
        assert_eq!(
            fs::read(&fixture.path().join(retired.to_string_lossy().as_ref())).unwrap(),
            br#"{"source":"guard"}"#
        );
    }

    #[test]
    fn postwrite_digest_failure_rolls_back_the_original_leaf() {
        let fixture = tempdir().expect("temporary fixture");
        let target = fixture.path().join("target.json");
        let replacement = fixture.path().join("replacement.json");
        let original_bytes = br#"{"source":"original"}"#;
        let replacement_bytes = br#"{"source":"replacement"}"#;
        let expected_bytes = br#"{"source":"different"}"#;
        fs::write(&target, original_bytes).expect("original");
        fs::write(&replacement, replacement_bytes).expect("replacement");

        let parent = open_test_parent(fixture.path());
        let leaf_name = CString::new("target.json").unwrap();
        let replacement_name = CString::new("replacement.json").unwrap();
        let original_fd = open_test_file(parent.as_fd(), &leaf_name);
        let replacement_fd = open_test_file(parent.as_fd(), &replacement_name);
        let original_identity = test_identity(original_fd.as_fd());
        let replacement_identity = test_identity(replacement_fd.as_fd());
        let parent_identity = parent_mutation_identity(parent.as_fd()).unwrap();
        let original_digest: [u8; 32] = Sha256::digest(original_bytes).into();
        let expected_digest: [u8; 32] = Sha256::digest(expected_bytes).into();
        let expected_semantics: Value = serde_json::from_slice(expected_bytes).unwrap();

        let error = exchange_rewritten_leaf_checked(
            parent.as_fd(),
            &leaf_name,
            &replacement_name,
            original_fd.as_fd(),
            original_identity,
            original_digest,
            replacement_fd.as_fd(),
            replacement_identity,
            expected_bytes,
            expected_digest,
            &expected_semantics,
            parent_identity,
        )
        .expect_err("postwrite mismatch must rollback");
        assert_eq!(error.code, "removal_write_failed");
        assert_eq!(fs::read(&target).unwrap(), original_bytes);
        assert_eq!(fs::read(&replacement).unwrap(), replacement_bytes);
    }
}
