use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde::{Deserialize, Serialize};

use super::TypographyPreset;

const SCHEMA_VERSION: u32 = 1;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypographyDiagnostic {
    pub code: &'static str,
    pub safe_message: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadedTypographyPreference {
    pub preset: TypographyPreset,
    pub persisted: bool,
    pub diagnostic: Option<TypographyDiagnostic>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypographyStoreError {
    pub code: &'static str,
    pub safe_message: &'static str,
}

impl fmt::Display for TypographyStoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.safe_message)
    }
}

impl std::error::Error for TypographyStoreError {}

pub trait TypographyPreferencePort: Send + Sync {
    fn save(&self, preset: TypographyPreset) -> Result<(), TypographyStoreError>;
}

#[derive(Debug, Clone)]
pub struct TypographyStore {
    path: PathBuf,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PreferenceFile {
    schema_version: u32,
    preset: TypographyPreset,
}

impl TypographyStore {
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }

    pub fn load(&self) -> LoadedTypographyPreference {
        let metadata = match fs::symlink_metadata(&self.path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return default_loaded(None)
            }
            Err(_) => return unreadable(),
        };
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return unreadable();
        }
        let bytes = match fs::read(&self.path) {
            Ok(bytes) => bytes,
            Err(_) => return unreadable(),
        };
        match serde_json::from_slice::<PreferenceFile>(&bytes) {
            Ok(value) if value.schema_version == SCHEMA_VERSION => LoadedTypographyPreference {
                preset: value.preset,
                persisted: true,
                diagnostic: None,
            },
            _ => default_loaded(Some(TypographyDiagnostic {
                code: "typography_preference_invalid",
                safe_message: "글자 크기 설정이 올바르지 않아 기본값을 사용합니다.",
            })),
        }
    }

    pub fn save(&self, preset: TypographyPreset) -> Result<(), TypographyStoreError> {
        let parent = self.path.parent().ok_or_else(write_error)?;
        fs::create_dir_all(parent).map_err(|_| write_error())?;
        let parent_metadata = fs::symlink_metadata(parent).map_err(|_| write_error())?;
        if parent_metadata.file_type().is_symlink() || !parent_metadata.is_dir() {
            return Err(write_error());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(parent, fs::Permissions::from_mode(0o700))
                .map_err(|_| write_error())?;
        }
        match fs::symlink_metadata(&self.path) {
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_file() => {
                return Err(write_error())
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err(write_error()),
        }
        let mut bytes = serde_json::to_vec(&PreferenceFile {
            schema_version: SCHEMA_VERSION,
            preset,
        })
        .map_err(|_| write_error())?;
        bytes.push(b'\n');
        let temporary = sibling_temporary_path(&self.path);
        let result = write_and_replace(&temporary, &self.path, parent, &bytes);
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result.map_err(|_| write_error())
    }
}

impl TypographyPreferencePort for TypographyStore {
    fn save(&self, preset: TypographyPreset) -> Result<(), TypographyStoreError> {
        Self::save(self, preset)
    }
}

fn default_loaded(diagnostic: Option<TypographyDiagnostic>) -> LoadedTypographyPreference {
    LoadedTypographyPreference {
        preset: TypographyPreset::Default,
        persisted: diagnostic.is_none(),
        diagnostic,
    }
}

fn unreadable() -> LoadedTypographyPreference {
    default_loaded(Some(TypographyDiagnostic {
        code: "typography_preference_unreadable",
        safe_message: "글자 크기 설정을 읽지 못해 기본값을 사용합니다.",
    }))
}

fn write_error() -> TypographyStoreError {
    TypographyStoreError {
        code: "typography_preference_write_failed",
        safe_message: "글자 크기 설정 저장 실패",
    }
}

fn sibling_temporary_path(destination: &Path) -> PathBuf {
    let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let file_name = destination
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("typography.json");
    destination.with_file_name(format!(
        ".{file_name}.{}.{}.tmp",
        std::process::id(),
        sequence
    ))
}

fn write_and_replace(
    temporary: &Path,
    destination: &Path,
    parent: &Path,
    bytes: &[u8],
) -> std::io::Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(temporary)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    fs::rename(temporary, destination)?;
    File::open(parent)?.sync_all()
}
