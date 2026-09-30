use sha2::{Digest, Sha256};
use std::fmt;
use std::path::{Path, PathBuf};

use crate::models::InstallStatus;

pub struct HashEngine;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HashComparisonError {
    CanonicalMissing { path: PathBuf },
    CanonicalUnreadable { path: PathBuf, message: String },
    DestinationUnreadable { path: PathBuf, message: String },
}

impl fmt::Display for HashComparisonError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::CanonicalMissing { path } => {
                write!(formatter, "canonical output is missing: {}", path.display())
            }
            Self::CanonicalUnreadable { path, message } => write!(
                formatter,
                "canonical output is unreadable: {} ({message})",
                path.display()
            ),
            Self::DestinationUnreadable { path, message } => write!(
                formatter,
                "installed destination is unreadable: {} ({message})",
                path.display()
            ),
        }
    }
}

impl std::error::Error for HashComparisonError {}

impl HashEngine {
    /// 파일의 sha256 hash 계산
    pub fn file_hash(path: &Path) -> Result<String, String> {
        let data =
            std::fs::read(path).map_err(|e| format!("Failed to read {}: {}", path.display(), e))?;
        let mut hasher = Sha256::new();
        hasher.update(&data);
        let hash = hasher.finalize();
        Ok(format!("{:x}", hash))
    }

    /// dist canonical vs 설치된 파일 비교
    pub fn compare(dist_path: &Path, installed_path: &Path) -> InstallStatus {
        Self::compare_checked(dist_path, installed_path).unwrap_or(InstallStatus::Missing)
    }

    pub fn compare_checked(
        canonical_path: &Path,
        destination_path: &Path,
    ) -> Result<InstallStatus, HashComparisonError> {
        if !canonical_path.exists() {
            return Err(HashComparisonError::CanonicalMissing {
                path: canonical_path.to_path_buf(),
            });
        }

        let canonical = std::fs::read(canonical_path).map_err(|error| {
            HashComparisonError::CanonicalUnreadable {
                path: canonical_path.to_path_buf(),
                message: error.to_string(),
            }
        })?;
        if !destination_path.exists() {
            return Ok(InstallStatus::Missing);
        }
        let destination = std::fs::read(destination_path).map_err(|error| {
            HashComparisonError::DestinationUnreadable {
                path: destination_path.to_path_buf(),
                message: error.to_string(),
            }
        })?;

        let canonical_hash = Sha256::digest(canonical);
        let destination_hash = Sha256::digest(destination);
        Ok(if canonical_hash == destination_hash {
            InstallStatus::Match
        } else {
            InstallStatus::Drift
        })
    }
}
