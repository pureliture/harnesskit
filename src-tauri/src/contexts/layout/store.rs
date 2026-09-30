//! Owner-only workspace layout preference persistence.

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde::{Deserialize, Serialize};

const SCHEMA_VERSION: u32 = 2;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);
pub const DEFAULT_LEFT_WIDTH_PX: u32 = 304;
pub const DEFAULT_RIGHT_WIDTH_PX: u32 = 368;
pub const MIN_LEFT_WIDTH_PX: u32 = 200;
pub const MAX_LEFT_WIDTH_PX: u32 = 512;
pub const MIN_RIGHT_WIDTH_PX: u32 = 184;
pub const MAX_RIGHT_WIDTH_PX: u32 = 560;

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PreferenceFile {
    schema_version: u32,
    preferred_left_width_px: u32,
    preferred_right_width_px: u32,
    left_collapsed: bool,
    right_collapsed: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyPreferenceFile {
    schema_version: u32,
    preferred_left_width_px: u32,
    preferred_right_width_px: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceLayoutDiagnostic {
    pub code: &'static str,
    pub safe_message: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadedWorkspaceLayoutPreference {
    pub preferred_left_width_px: u32,
    pub preferred_right_width_px: u32,
    pub left_collapsed: bool,
    pub right_collapsed: bool,
    pub persisted: bool,
    pub diagnostic: Option<WorkspaceLayoutDiagnostic>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceLayoutStoreError {
    pub code: &'static str,
    pub safe_message: &'static str,
}

impl fmt::Display for WorkspaceLayoutStoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.safe_message)
    }
}

impl std::error::Error for WorkspaceLayoutStoreError {}

pub trait WorkspaceLayoutPreferencePort: Send + Sync {
    fn save(
        &self,
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
    ) -> Result<(), WorkspaceLayoutStoreError>;

    fn save_with_disclosure(
        &self,
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
        left_collapsed: bool,
        right_collapsed: bool,
    ) -> Result<(), WorkspaceLayoutStoreError> {
        if left_collapsed || right_collapsed {
            return Err(WorkspaceLayoutStoreError {
                code: "workspace_layout_preference_write_failed",
                safe_message: "레이아웃 설정 저장 실패",
            });
        }
        self.save(preferred_left_width_px, preferred_right_width_px)
    }
}

#[derive(Debug, Clone)]
pub struct WorkspaceLayoutStore {
    #[allow(dead_code)]
    path: PathBuf,
}

impl WorkspaceLayoutStore {
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }

    pub fn load(&self) -> LoadedWorkspaceLayoutPreference {
        let metadata = match fs::symlink_metadata(&self.path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return default_preference(None);
            }
            Err(_) => return unreadable_preference(),
        };
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return unreadable_preference();
        }
        let source = match fs::read_to_string(&self.path) {
            Ok(source) => source,
            Err(_) => return unreadable_preference(),
        };

        match serde_json::from_str::<PreferenceFile>(&source) {
            Ok(preference) if valid_preference(&preference) => LoadedWorkspaceLayoutPreference {
                preferred_left_width_px: preference.preferred_left_width_px,
                preferred_right_width_px: preference.preferred_right_width_px,
                left_collapsed: preference.left_collapsed,
                right_collapsed: preference.right_collapsed,
                persisted: true,
                diagnostic: None,
            },
            _ => match serde_json::from_str::<LegacyPreferenceFile>(&source) {
                Ok(preference)
                    if preference.schema_version == 1
                        && valid_widths(
                            preference.preferred_left_width_px,
                            preference.preferred_right_width_px,
                        ) =>
                {
                    LoadedWorkspaceLayoutPreference {
                        preferred_left_width_px: preference.preferred_left_width_px,
                        preferred_right_width_px: preference.preferred_right_width_px,
                        left_collapsed: false,
                        right_collapsed: false,
                        persisted: true,
                        diagnostic: None,
                    }
                }
                _ => default_preference(Some(WorkspaceLayoutDiagnostic {
                    code: "workspace_layout_preference_invalid",
                    safe_message: "레이아웃 설정이 올바르지 않아 기본값을 사용합니다.",
                })),
            },
        }
    }

    pub fn save(
        &self,
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
    ) -> Result<(), WorkspaceLayoutStoreError> {
        self.save_with_disclosure(
            preferred_left_width_px,
            preferred_right_width_px,
            false,
            false,
        )
    }

    pub fn save_with_disclosure(
        &self,
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
        left_collapsed: bool,
        right_collapsed: bool,
    ) -> Result<(), WorkspaceLayoutStoreError> {
        if !valid_widths(preferred_left_width_px, preferred_right_width_px) {
            return Err(WorkspaceLayoutStoreError {
                code: "workspace_layout_preference_invalid",
                safe_message: "레이아웃 설정 값이 올바르지 않습니다.",
            });
        }
        let parent = self.path.parent().ok_or(WorkspaceLayoutStoreError {
            code: "workspace_layout_preference_write_failed",
            safe_message: "레이아웃 설정 저장 실패",
        })?;
        fs::create_dir_all(parent).map_err(|_| write_error())?;
        let parent_metadata = fs::symlink_metadata(parent).map_err(|_| write_error())?;
        if parent_metadata.file_type().is_symlink() || !parent_metadata.is_dir() {
            return Err(write_error());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if parent_metadata.permissions().mode() & 0o200 == 0 {
                return Err(write_error());
            }
            fs::set_permissions(parent, fs::Permissions::from_mode(0o700))
                .map_err(|_| write_error())?;
        }
        match fs::symlink_metadata(&self.path) {
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_file() => {
                return Err(write_error());
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err(write_error()),
        }
        let mut bytes = serde_json::to_vec(&PreferenceFile {
            schema_version: SCHEMA_VERSION,
            preferred_left_width_px,
            preferred_right_width_px,
            left_collapsed,
            right_collapsed,
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

fn write_error() -> WorkspaceLayoutStoreError {
    WorkspaceLayoutStoreError {
        code: "workspace_layout_preference_write_failed",
        safe_message: "레이아웃 설정 저장 실패",
    }
}

fn valid_preference(preference: &PreferenceFile) -> bool {
    preference.schema_version == SCHEMA_VERSION
        && valid_widths(
            preference.preferred_left_width_px,
            preference.preferred_right_width_px,
        )
}

pub(super) fn valid_widths(preferred_left_width_px: u32, preferred_right_width_px: u32) -> bool {
    (MIN_LEFT_WIDTH_PX..=MAX_LEFT_WIDTH_PX).contains(&preferred_left_width_px)
        && (MIN_RIGHT_WIDTH_PX..=MAX_RIGHT_WIDTH_PX).contains(&preferred_right_width_px)
}

fn default_preference(
    diagnostic: Option<WorkspaceLayoutDiagnostic>,
) -> LoadedWorkspaceLayoutPreference {
    let persisted = diagnostic.is_none();
    LoadedWorkspaceLayoutPreference {
        preferred_left_width_px: DEFAULT_LEFT_WIDTH_PX,
        preferred_right_width_px: DEFAULT_RIGHT_WIDTH_PX,
        left_collapsed: false,
        right_collapsed: false,
        persisted,
        diagnostic,
    }
}

fn unreadable_preference() -> LoadedWorkspaceLayoutPreference {
    default_preference(Some(WorkspaceLayoutDiagnostic {
        code: "workspace_layout_preference_unreadable",
        safe_message: "레이아웃 설정을 읽지 못해 기본값을 사용합니다.",
    }))
}

fn sibling_temporary_path(destination: &Path) -> PathBuf {
    let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let file_name = destination
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("layout.json");
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

impl WorkspaceLayoutPreferencePort for WorkspaceLayoutStore {
    fn save(
        &self,
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
    ) -> Result<(), WorkspaceLayoutStoreError> {
        WorkspaceLayoutStore::save(self, preferred_left_width_px, preferred_right_width_px)
    }

    fn save_with_disclosure(
        &self,
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
        left_collapsed: bool,
        right_collapsed: bool,
    ) -> Result<(), WorkspaceLayoutStoreError> {
        WorkspaceLayoutStore::save_with_disclosure(
            self,
            preferred_left_width_px,
            preferred_right_width_px,
            left_collapsed,
            right_collapsed,
        )
    }
}

#[cfg(test)]
mod amendment_red_tests {
    use super::*;

    #[test]
    fn save_writes_schema_v2_with_independent_pane_disclosure_flags() {
        let temp = tempfile::tempdir().unwrap();
        let store = WorkspaceLayoutStore::new(temp.path().join("layout.json"));

        store
            .save_with_disclosure(DEFAULT_LEFT_WIDTH_PX, DEFAULT_RIGHT_WIDTH_PX, true, false)
            .unwrap();

        let document: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(temp.path().join("layout.json")).unwrap())
                .unwrap();
        assert_eq!(document["schema_version"], 2);
        assert_eq!(document["left_collapsed"], true);
        assert_eq!(document["right_collapsed"], false);
    }
}
