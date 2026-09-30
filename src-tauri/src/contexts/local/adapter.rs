//! Typed, data-only discovery adapter contracts.

use serde::{Deserialize, Serialize};

use super::domain::Scope;
use super::probe::VersionProbeId;
use super::roots::RootEnumeratorId;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolId {
    Codex,
    ClaudeCode,
    Antigravity,
    AntigravityCli,
    Hermes,
}

impl ToolId {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Codex => "codex",
            Self::ClaudeCode => "claude_code",
            Self::Antigravity => "antigravity",
            Self::AntigravityCli => "antigravity_cli",
            Self::Hermes => "hermes",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub enum SurfaceKind {
    Skill,
    Agent,
    Hook,
    Rule,
    Command,
    Workflow,
    Unclassified,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ParserId {
    SkillFrontmatterV1,
    CodexTomlV1,
    HookJsonV1,
    MarkdownRuleV1,
    ClaudeSettingsV1,
    ClaudeWorkflowJsMetadataV1,
    AntigravityManifestV1,
    AntigravityCliJsonV1,
    HermesYamlV1,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IgnorePolicyId {
    LocalDiscoveryIgnoreV1,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RedactionPolicyId {
    LocalSecretRedactionV1,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SupportedOs {
    Macos,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VersionSupportKind {
    Exact,
    SemverRange,
    SchemaFingerprint,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VersionSupport {
    pub kind: VersionSupportKind,
    pub value: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdapterSurfaceDescriptor {
    pub surface_id: String,
    pub scope: Scope,
    pub root_enumerator_id: RootEnumeratorId,
    pub parser_ids: Vec<ParserId>,
    pub supported_kinds: Vec<SurfaceKind>,
    pub ignore_policy_id: IgnorePolicyId,
    pub redaction_policy_id: RedactionPolicyId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdapterDescriptor {
    pub schema_version: u32,
    pub adapter_id: String,
    pub adapter_version: String,
    pub implementation_version: u32,
    pub identity_namespace: String,
    pub locator_version: u32,
    pub tool_id: ToolId,
    pub supported_os: Vec<SupportedOs>,
    pub qualification_id: String,
    pub version_probe_id: VersionProbeId,
    pub supported_tool_versions: VersionSupport,
    pub surfaces: Vec<AdapterSurfaceDescriptor>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceSourceKind {
    OfficialDocumentation,
    LocalVersionMetadata,
    DistributionManifest,
    ReadOnlyLiveProbe,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvidenceSource {
    pub source_id: String,
    pub source_kind: EvidenceSourceKind,
    pub source_revision: String,
    pub captured_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservedRuntime {
    pub tool_version: String,
    pub macos_version: String,
    pub architecture: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ProbeStatus {
    Passed,
    Failed,
    Unavailable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EvidenceDigestAlgorithm {
    Sha256,
    Sha512,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QualificationProbeResult {
    pub probe_id: VersionProbeId,
    pub surface_id: Option<String>,
    pub observed_tool_version: String,
    pub status: ProbeStatus,
    pub observed_schema_digest: Option<String>,
    pub evidence_algorithm: Option<EvidenceDigestAlgorithm>,
    pub evidence_digest: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artifact_digest_algorithm: Option<EvidenceDigestAlgorithm>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artifact_digest: Option<String>,
    pub safe_failure_code: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QualificationRecord {
    pub qualification_id: String,
    pub adapter_id: String,
    pub adapter_version: String,
    pub descriptor_canonical_sha256: String,
    pub fixture_manifest_sha256: String,
    pub evidence_sources: Vec<EvidenceSource>,
    pub observed_runtime: ObservedRuntime,
    pub probe_results: Vec<QualificationProbeResult>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CompiledSurfaceBinding {
    pub surface_id: &'static str,
    pub scope: Scope,
    pub root_enumerator_id: RootEnumeratorId,
    pub parser_ids: &'static [ParserId],
    pub supported_kinds: &'static [SurfaceKind],
    pub ignore_policy_id: IgnorePolicyId,
    pub redaction_policy_id: RedactionPolicyId,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AdapterImplementation {
    pub adapter_id: &'static str,
    pub implementation_version: u32,
    pub tool_id: ToolId,
    pub version_probe_id: VersionProbeId,
    pub surfaces: &'static [CompiledSurfaceBinding],
}
