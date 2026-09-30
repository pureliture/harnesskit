//! Identity-bound Local instance path actions.

use std::path::Path;

use rustix::fd::BorrowedFd;
use serde::{Deserialize, Serialize};

use super::domain::InstanceHandle;
use super::verified_path::{
    VerificationPolicy, VerifiedLocalFile, VerifiedLocalPathResolver, VerifiedPathError,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalPathAction {
    Reveal,
    CopyPath,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum LocalPathActionOutcome {
    Revealed,
    PathCopied,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalPathActionError {
    pub code: String,
}

impl LocalPathActionError {
    pub(crate) fn new(code: &str) -> Self {
        Self {
            code: code.to_string(),
        }
    }
}

impl From<VerifiedPathError> for LocalPathActionError {
    fn from(value: VerifiedPathError) -> Self {
        Self { code: value.code }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NativePathActionError {
    Unavailable,
    Failed,
}

pub struct VerifiedNativePath<'a> {
    canonical_path: &'a Path,
    _open_file: BorrowedFd<'a>,
}

impl VerifiedNativePath<'_> {
    pub fn canonical_path(&self) -> &Path {
        self.canonical_path
    }
}

pub trait NativePathActionPort: Send + Sync + 'static {
    fn reveal(&self, target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError>;

    fn copy_path(&self, target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError>;
}

pub(crate) struct UnavailableNativePathActionPort;

impl NativePathActionPort for UnavailableNativePathActionPort {
    fn reveal(&self, _target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError> {
        Err(NativePathActionError::Unavailable)
    }

    fn copy_path(&self, _target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError> {
        Err(NativePathActionError::Unavailable)
    }
}

pub(crate) fn execute_path_action(
    handle: &InstanceHandle,
    action: LocalPathAction,
    native_port: &dyn NativePathActionPort,
) -> Result<LocalPathActionOutcome, LocalPathActionError> {
    let target =
        VerifiedLocalPathResolver.resolve(handle, VerificationPolicy::StrictSnapshotIdentity)?;
    match action {
        LocalPathAction::Reveal => {
            run_native(&target, native_port, NativeAction::Reveal)?;
            Ok(LocalPathActionOutcome::Revealed)
        }
        LocalPathAction::CopyPath => {
            run_native(&target, native_port, NativeAction::CopyPath)?;
            Ok(LocalPathActionOutcome::PathCopied)
        }
    }
}

enum NativeAction {
    Reveal,
    CopyPath,
}

fn run_native(
    verified: &VerifiedLocalFile,
    port: &dyn NativePathActionPort,
    action: NativeAction,
) -> Result<(), LocalPathActionError> {
    verified.verify_unchanged()?;
    let target = VerifiedNativePath {
        canonical_path: verified.canonical_path(),
        _open_file: verified.borrowed_fd(),
    };
    let result = match action {
        NativeAction::Reveal => port.reveal(&target),
        NativeAction::CopyPath => port.copy_path(&target),
    };
    verified.verify_unchanged()?;
    match (action, result) {
        (_, Ok(())) => Ok(()),
        (NativeAction::Reveal, Err(NativePathActionError::Unavailable)) => {
            Err(LocalPathActionError::new("reveal_unavailable"))
        }
        (NativeAction::CopyPath, Err(NativePathActionError::Unavailable)) => {
            Err(LocalPathActionError::new("copy_path_unavailable"))
        }
        (NativeAction::Reveal, Err(NativePathActionError::Failed)) => {
            Err(LocalPathActionError::new("reveal_failed"))
        }
        (NativeAction::CopyPath, Err(NativePathActionError::Failed)) => {
            Err(LocalPathActionError::new("copy_path_failed"))
        }
    }
}
