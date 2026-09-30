use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// harness surface marker — scan이 탐지하는 디렉터리/파일 표시자
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub enum SurfaceMarker {
    Claude,     // .claude/
    Codex,      // .codex/
    Agents,     // .agents/
    Acli,       // .acli/
    HarnessKit, // .harnesskit/
    Gemini,     // .gemini/
    Hermes,     // .hermes/
    AgentsMd,   // AGENTS.md
    ClaudeMd,   // CLAUDE.md
}

/// scan scope — user-level vs project-level
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub enum Scope {
    User,
    Project,
}

/// scan 결과 개별 파일/디렉터리 항목
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScanItem {
    pub path: String,
    pub surface: SurfaceMarker,
    pub scope: Scope,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScanIssue {
    pub category: String,
    pub path: String,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InventoryIssue {
    pub category: String,
    pub path: String,
    pub message: String,
}

/// scanner 전체 결과
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScanResult {
    pub items: Vec<ScanItem>,
    pub user_level_surfaces: Vec<SurfaceMarker>,
    pub skipped_paths: Vec<String>,
    #[serde(default)]
    pub issues: Vec<ScanIssue>,
}

/// registry.yml의 개별 컴포넌트 항목
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegistryEntry {
    pub component_id: String,
    pub kind: String,
    pub status: String,
    pub path: String,
}

/// component.yml manifest data
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ComponentManifest {
    pub component_id: String,
    pub kind: Option<String>,
    pub status: Option<String>,
    pub domain: Option<String>,
    pub title: Option<String>,
    pub summary: Option<String>,
    pub targets: std::collections::BTreeMap<String, TargetConfig>,
    pub adapter: Option<serde_yaml::Mapping>,
    pub owned_files: Vec<String>,
    pub provenance_mode: Option<String>,
    pub scopes: Vec<String>,
    pub entrypoints: Option<serde_yaml::Mapping>,
}

/// component.yml의 targets 맵 개별 항목
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TargetConfig {
    pub output_path: Option<String>,
}

/// profile 매핑
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProfileMembership {
    pub profile_id: String,
    pub component_ids: Vec<String>,
}

/// install 상태
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum InstallStatus {
    Match,
    Drift,
    Missing,
}

/// foreign 분류
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ForeignClassification {
    HarnessKitOwned,
    Foreign,
    RegistryUnregistered,
    NonHarness,
}

/// canonical component output이 발견된 실제 설치 위치
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InventoryLocation {
    pub scope: Scope,
    pub path: String,
    pub install_status: InstallStatus,
}

/// registry identity에 연결되지 않는 harness-like 발견 항목
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OrphanDiscovery {
    pub scope: Scope,
    pub path: String,
    pub classification: ForeignClassification,
}

/// normalized inventory item
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InventoryItem {
    pub component_id: String,
    pub target: String,
    pub kind: String,
    pub status: String,
    pub title: Option<String>,
    pub summary: Option<String>,
    pub domain: Option<String>,
    pub scope: Scope,
    pub source_path: String,
    pub install_status: InstallStatus,
    pub profile_memberships: Vec<String>,
    pub provenance_mode: Option<String>,
    pub owned_files: Vec<String>,
    pub foreign_classification: ForeignClassification,
    #[serde(default)]
    pub locations: Vec<InventoryLocation>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct TargetInstallCounts {
    pub matched: usize,
    pub drifted: usize,
    pub missing: usize,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct DashboardAggregates {
    pub kind_counts: BTreeMap<String, usize>,
    pub domain_counts: BTreeMap<String, usize>,
    pub status_counts: BTreeMap<String, usize>,
    pub target_installable_counts: BTreeMap<String, usize>,
    pub target_install_counts: BTreeMap<String, TargetInstallCounts>,
    pub profile_component_counts: BTreeMap<String, usize>,
    pub unprofiled_component_count: usize,
    pub provenance_mode_counts: BTreeMap<String, usize>,
}

/// 전체 inventory
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Inventory {
    pub items: Vec<InventoryItem>,
    #[serde(default)]
    pub dashboard: DashboardAggregates,
    #[serde(default)]
    pub orphan_discoveries: Vec<OrphanDiscovery>,
    #[serde(default)]
    pub issues: Vec<InventoryIssue>,
    pub scan_metadata: ScanMetadata,
}

impl Inventory {
    /// volatile scan timestamp를 제외한 deterministic snapshot JSON
    pub fn canonical_json(&self) -> Result<Vec<u8>, String> {
        let mut value = serde_json::to_value(self).map_err(|error| error.to_string())?;
        value
            .get_mut("scan_metadata")
            .and_then(serde_json::Value::as_object_mut)
            .ok_or_else(|| "scan_metadata must serialize as an object".to_string())?
            .remove("scan_timestamp");
        serde_json::to_vec(&value).map_err(|error| error.to_string())
    }
}

/// scan 메타데이터
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScanMetadata {
    pub scan_timestamp: String,
    pub app_version: String,
    pub user_level_surfaces: Vec<String>,
    pub skipped_path_count: usize,
}
