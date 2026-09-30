//! Safe Local inventory domain values.

use std::ffi::OsString;
use std::fmt;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::projection::DestinationKey;

use super::adapter::{ParserId, SurfaceKind, ToolId};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Scope {
    User,
    Project,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ParseState {
    Parsed,
    Malformed,
    Unreadable,
    Unclassified,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StableIdentity {
    pub identity_namespace: String,
    pub tool_id: ToolId,
    pub surface_id: String,
    pub scope: Scope,
    pub project_id: Option<String>,
    pub locator_version: u32,
    pub stable_source_locator: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileIdentityType {
    Regular,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct FileIdentity {
    pub device: u64,
    pub inode: u64,
    pub file_type: FileIdentityType,
    pub mode: u32,
    pub owner_uid: u32,
    pub owner_gid: u32,
    pub link_count: u64,
    pub size: u64,
    pub mtime_ns: i128,
    pub ctime_ns: i128,
}

impl fmt::Debug for FileIdentity {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("FileIdentity")
            .field("captured", &true)
            .finish_non_exhaustive()
    }
}

#[derive(Clone, PartialEq, Eq)]
pub struct InstanceHandle {
    pub canonical_root: PathBuf,
    pub root_device: u64,
    pub root_inode: u64,
    pub raw_relative_components: Vec<Vec<u8>>,
    pub config_entry_locator: Option<String>,
    pub source_parser_id: ParserId,
    pub scan_content_sha256: Option<[u8; 32]>,
    pub scan_file_identity: FileIdentity,
}

impl fmt::Debug for InstanceHandle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("InstanceHandle")
            .field("canonical_root", &"[local-only]")
            .field("root_device", &self.root_device)
            .field("root_inode", &self.root_inode)
            .field(
                "raw_relative_component_count",
                &self.raw_relative_components.len(),
            )
            .field("config_entry_locator", &self.config_entry_locator)
            .field("source_parser_id", &self.source_parser_id)
            .field(
                "scan_content_sha256_present",
                &self.scan_content_sha256.is_some(),
            )
            .field("scan_file_identity", &self.scan_file_identity)
            .finish()
    }
}

#[derive(Clone, PartialEq, Eq)]
pub struct ProjectLocationRecord {
    pub project_id: String,
    pub display_name: OsString,
    pub root_identity: String,
    pub root_device: u64,
    pub root_inode: u64,
    pub owner_uid: u32,
    pub canonical_path: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectIgnoreSummary {
    pub project_id: String,
    pub source_revision: String,
    pub rule_count: usize,
    pub excluded_path_count: usize,
}

impl fmt::Debug for ProjectLocationRecord {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ProjectLocationRecord")
            .field("project_id", &self.project_id)
            .field("display_name", &"[local-only]")
            .field("root_identity", &self.root_identity)
            .field("root_device", &self.root_device)
            .field("root_inode", &self.root_inode)
            .field("owner_uid", &self.owner_uid)
            .field("canonical_path", &"[local-only]")
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SafeSetting {
    pub key: String,
    pub present: bool,
    pub redacted: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ParsedItem {
    pub stable_source_locator: String,
    pub kind: SurfaceKind,
    pub name: Option<String>,
    pub description: Option<String>,
    pub description_source: Option<String>,
    pub settings: Vec<SafeSetting>,
    pub parse_state: ParseState,
    pub issue_codes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SafeIssue {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project_id: Option<String>,
    pub code: String,
    pub safe_message: String,
    pub safe_relative_locator: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ParseBatch {
    pub items: Vec<ParsedItem>,
    pub issues: Vec<SafeIssue>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CoverageStatus {
    Complete,
    Partial,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SurfacePresence {
    Present,
    NotPresent,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CoverageRecord {
    pub adapter_id: String,
    pub adapter_version: String,
    pub surface_id: String,
    pub root_id: Option<String>,
    pub scope: Scope,
    pub project_id: Option<String>,
    pub status: CoverageStatus,
    pub presence: SurfacePresence,
    pub item_count: usize,
    pub skipped_count: usize,
    pub issue_codes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct QualifiedTool {
    pub tool_id: ToolId,
    pub adapter_id: String,
    pub adapter_version: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SkippedPath {
    pub root_id: Option<String>,
    pub safe_relative_locator: String,
    pub reason_code: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalInstance {
    pub instance_id: String,
    pub root_id: String,
    pub adapter_id: String,
    pub adapter_version: String,
    pub tool_id: ToolId,
    pub surface_id: String,
    pub scope: Scope,
    pub project_id: Option<String>,
    pub stable_source_locator: String,
    pub correlation_key: Option<DestinationKey>,
    pub kind: SurfaceKind,
    pub name: Option<String>,
    pub description: Option<String>,
    pub description_source: Option<String>,
    pub settings: Vec<SafeSetting>,
    pub content_hash: Option<String>,
    pub size: Option<u64>,
    pub modified_unix_millis: Option<u64>,
    pub parse_state: ParseState,
    pub issue_codes: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalScanResult {
    pub qualified_tools: Vec<QualifiedTool>,
    pub instances: Vec<LocalInstance>,
    pub coverage: Vec<CoverageRecord>,
    pub skipped_paths: Vec<SkippedPath>,
    pub issues: Vec<SafeIssue>,
    #[serde(default)]
    pub project_ignore_summaries: Vec<ProjectIgnoreSummary>,
}
