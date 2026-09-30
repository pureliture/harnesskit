use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde::{Deserialize, Serialize};

use super::LogicalMode;

const SCHEMA_VERSION: u32 = 1;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppearanceDiagnostic {
    pub code: &'static str,
    pub safe_message: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadedPreference {
    pub logical_mode: LogicalMode,
    pub diagnostic: Option<AppearanceDiagnostic>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppearanceStoreError {
    pub code: &'static str,
    pub safe_message: &'static str,
}

impl fmt::Display for AppearanceStoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.safe_message)
    }
}

impl std::error::Error for AppearanceStoreError {}

#[derive(Debug, Serialize, Deserialize)]
struct PreferenceFile {
    schema_version: u32,
    logical_mode: StoredLogicalMode,
}

#[derive(Debug, Serialize, Deserialize)]
enum StoredLogicalMode {
    System,
    Light,
    Dark,
}

impl From<StoredLogicalMode> for LogicalMode {
    fn from(value: StoredLogicalMode) -> Self {
        match value {
            StoredLogicalMode::System => Self::System,
            StoredLogicalMode::Light => Self::Light,
            StoredLogicalMode::Dark => Self::Dark,
        }
    }
}

impl From<LogicalMode> for StoredLogicalMode {
    fn from(value: LogicalMode) -> Self {
        match value {
            LogicalMode::System => Self::System,
            LogicalMode::Light => Self::Light,
            LogicalMode::Dark => Self::Dark,
        }
    }
}

#[derive(Debug, Clone)]
pub struct AppearanceStore {
    path: PathBuf,
}

pub trait AppearancePreferencePort: Send + Sync {
    fn save(&self, logical_mode: LogicalMode) -> Result<(), AppearanceStoreError>;
}

impl AppearancePreferencePort for AppearanceStore {
    fn save(&self, logical_mode: LogicalMode) -> Result<(), AppearanceStoreError> {
        AppearanceStore::save(self, logical_mode)
    }
}

impl AppearanceStore {
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }

    pub fn load(&self) -> LoadedPreference {
        let source = match fs::read_to_string(&self.path) {
            Ok(source) => source,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return LoadedPreference {
                    logical_mode: LogicalMode::System,
                    diagnostic: None,
                };
            }
            Err(_) => {
                return fallback_with_diagnostic(
                    "appearance_preference_unreadable",
                    "외관 설정을 읽지 못해 시스템 설정을 사용합니다.",
                );
            }
        };

        match serde_json::from_str::<PreferenceFile>(&source) {
            Ok(preference) if preference.schema_version == SCHEMA_VERSION => LoadedPreference {
                logical_mode: preference.logical_mode.into(),
                diagnostic: None,
            },
            _ => fallback_with_diagnostic(
                "appearance_preference_invalid",
                "외관 설정이 올바르지 않아 시스템 설정을 사용합니다.",
            ),
        }
    }

    pub fn save(&self, logical_mode: LogicalMode) -> Result<(), AppearanceStoreError> {
        self.save_inner(logical_mode)
            .map_err(|_| AppearanceStoreError {
                code: "appearance_preference_write_failed",
                safe_message: "설정 저장 실패",
            })
    }

    fn save_inner(&self, logical_mode: LogicalMode) -> std::io::Result<()> {
        let parent = self.path.parent().ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "missing preference parent",
            )
        })?;
        fs::create_dir_all(parent)?;

        let mut bytes = serde_json::to_vec(&PreferenceFile {
            schema_version: SCHEMA_VERSION,
            logical_mode: logical_mode.into(),
        })
        .map_err(std::io::Error::other)?;
        bytes.push(b'\n');

        let temporary = sibling_temporary_path(&self.path);
        let result = write_and_replace(&temporary, &self.path, parent, &bytes);
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result
    }
}

fn fallback_with_diagnostic(code: &'static str, safe_message: &'static str) -> LoadedPreference {
    LoadedPreference {
        logical_mode: LogicalMode::System,
        diagnostic: Some(AppearanceDiagnostic { code, safe_message }),
    }
}

fn sibling_temporary_path(destination: &Path) -> PathBuf {
    let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let file_name = destination
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("appearance.json");
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
