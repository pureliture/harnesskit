//! Compiled bindings for the five approved discovery adapters.

use super::adapter::{
    AdapterImplementation, CompiledSurfaceBinding, IgnorePolicyId, ParserId, RedactionPolicyId,
    SurfaceKind, ToolId,
};
use super::domain::Scope;
use super::probe::VersionProbeId;
use super::roots::RootEnumeratorId;

#[derive(Debug, Clone, Copy)]
pub(crate) struct EmbeddedAdapterSource {
    pub(crate) implementation: &'static AdapterImplementation,
    pub(crate) descriptor_yaml: &'static str,
    pub(crate) qualification_json: &'static str,
}

const CODEX_PARSERS: &[ParserId] = &[
    ParserId::SkillFrontmatterV1,
    ParserId::CodexTomlV1,
    ParserId::HookJsonV1,
    ParserId::MarkdownRuleV1,
];
const CODEX_KINDS: &[SurfaceKind] = &[
    SurfaceKind::Skill,
    SurfaceKind::Agent,
    SurfaceKind::Hook,
    SurfaceKind::Rule,
    SurfaceKind::Command,
    SurfaceKind::Unclassified,
];
const CLAUDE_PARSERS: &[ParserId] = &[
    ParserId::SkillFrontmatterV1,
    ParserId::ClaudeSettingsV1,
    ParserId::ClaudeWorkflowJsMetadataV1,
    ParserId::MarkdownRuleV1,
];
const CLAUDE_KINDS: &[SurfaceKind] = &[
    SurfaceKind::Skill,
    SurfaceKind::Agent,
    SurfaceKind::Hook,
    SurfaceKind::Rule,
    SurfaceKind::Command,
    SurfaceKind::Workflow,
    SurfaceKind::Unclassified,
];
const ANTIGRAVITY_PARSERS: &[ParserId] = &[
    ParserId::SkillFrontmatterV1,
    ParserId::AntigravityManifestV1,
    ParserId::HookJsonV1,
    ParserId::MarkdownRuleV1,
];
const ANTIGRAVITY_KINDS: &[SurfaceKind] = &[
    SurfaceKind::Skill,
    SurfaceKind::Agent,
    SurfaceKind::Hook,
    SurfaceKind::Rule,
    SurfaceKind::Workflow,
    SurfaceKind::Unclassified,
];
const ANTIGRAVITY_CLI_PARSERS: &[ParserId] = &[
    ParserId::AntigravityCliJsonV1,
    ParserId::SkillFrontmatterV1,
    ParserId::HookJsonV1,
    ParserId::MarkdownRuleV1,
];
const ANTIGRAVITY_CLI_KINDS: &[SurfaceKind] = &[
    SurfaceKind::Skill,
    SurfaceKind::Agent,
    SurfaceKind::Hook,
    SurfaceKind::Rule,
    SurfaceKind::Command,
    SurfaceKind::Workflow,
    SurfaceKind::Unclassified,
];
const HERMES_PARSERS: &[ParserId] = &[
    ParserId::SkillFrontmatterV1,
    ParserId::HermesYamlV1,
    ParserId::MarkdownRuleV1,
];
const HERMES_KINDS: &[SurfaceKind] = &[
    SurfaceKind::Skill,
    SurfaceKind::Hook,
    SurfaceKind::Rule,
    SurfaceKind::Unclassified,
];

macro_rules! surfaces {
    ($name:ident, $prefix:literal, $enumerator:expr, $parsers:expr, $kinds:expr) => {
        const $name: &[CompiledSurfaceBinding] = &[
            CompiledSurfaceBinding {
                surface_id: concat!($prefix, "_project"),
                scope: Scope::Project,
                root_enumerator_id: $enumerator,
                parser_ids: $parsers,
                supported_kinds: $kinds,
                ignore_policy_id: IgnorePolicyId::LocalDiscoveryIgnoreV1,
                redaction_policy_id: RedactionPolicyId::LocalSecretRedactionV1,
            },
            CompiledSurfaceBinding {
                surface_id: concat!($prefix, "_user"),
                scope: Scope::User,
                root_enumerator_id: $enumerator,
                parser_ids: $parsers,
                supported_kinds: $kinds,
                ignore_policy_id: IgnorePolicyId::LocalDiscoveryIgnoreV1,
                redaction_policy_id: RedactionPolicyId::LocalSecretRedactionV1,
            },
        ];
    };
}

surfaces!(
    CODEX_SURFACES,
    "codex",
    RootEnumeratorId::CodexHomeMarkersV1,
    CODEX_PARSERS,
    CODEX_KINDS
);
surfaces!(
    CLAUDE_SURFACES,
    "claude_code",
    RootEnumeratorId::ClaudeHomeMarkersV2,
    CLAUDE_PARSERS,
    CLAUDE_KINDS
);
surfaces!(
    ANTIGRAVITY_SURFACES,
    "antigravity",
    RootEnumeratorId::AntigravityHomeMarkersV2,
    ANTIGRAVITY_PARSERS,
    ANTIGRAVITY_KINDS
);
surfaces!(
    ANTIGRAVITY_CLI_SURFACES,
    "antigravity_cli",
    RootEnumeratorId::AntigravityCliHomeMarkersV2,
    ANTIGRAVITY_CLI_PARSERS,
    ANTIGRAVITY_CLI_KINDS
);
surfaces!(
    HERMES_SURFACES,
    "hermes",
    RootEnumeratorId::HermesExternalDirsV1,
    HERMES_PARSERS,
    HERMES_KINDS
);

const CODEX: AdapterImplementation = AdapterImplementation {
    adapter_id: "codex",
    implementation_version: 1,
    tool_id: ToolId::Codex,
    version_probe_id: VersionProbeId::CodexPackageJsonV1,
    surfaces: CODEX_SURFACES,
};
const CLAUDE_CODE: AdapterImplementation = AdapterImplementation {
    adapter_id: "claude-code",
    implementation_version: 1,
    tool_id: ToolId::ClaudeCode,
    version_probe_id: VersionProbeId::ClaudeCliMetadataV1,
    surfaces: CLAUDE_SURFACES,
};
const ANTIGRAVITY: AdapterImplementation = AdapterImplementation {
    adapter_id: "antigravity",
    implementation_version: 1,
    tool_id: ToolId::Antigravity,
    version_probe_id: VersionProbeId::AntigravityInfoPlistV1,
    surfaces: ANTIGRAVITY_SURFACES,
};
const ANTIGRAVITY_CLI: AdapterImplementation = AdapterImplementation {
    adapter_id: "antigravity-cli",
    implementation_version: 1,
    tool_id: ToolId::AntigravityCli,
    version_probe_id: VersionProbeId::AntigravityCliManifestV1,
    surfaces: ANTIGRAVITY_CLI_SURFACES,
};
const HERMES: AdapterImplementation = AdapterImplementation {
    adapter_id: "hermes",
    implementation_version: 1,
    tool_id: ToolId::Hermes,
    version_probe_id: VersionProbeId::HermesDistInfoV1,
    surfaces: HERMES_SURFACES,
};

pub(crate) const EMBEDDED_ADAPTERS: &[EmbeddedAdapterSource] = &[
    EmbeddedAdapterSource {
        implementation: &ANTIGRAVITY,
        descriptor_yaml: include_str!("antigravity.yml"),
        qualification_json: include_str!("antigravity.qualification.json"),
    },
    EmbeddedAdapterSource {
        implementation: &ANTIGRAVITY_CLI,
        descriptor_yaml: include_str!("antigravity-cli.yml"),
        qualification_json: include_str!("antigravity-cli.qualification.json"),
    },
    EmbeddedAdapterSource {
        implementation: &CLAUDE_CODE,
        descriptor_yaml: include_str!("claude-code.yml"),
        qualification_json: include_str!("claude-code.qualification.json"),
    },
    EmbeddedAdapterSource {
        implementation: &CODEX,
        descriptor_yaml: include_str!("codex.yml"),
        qualification_json: include_str!("codex.qualification.json"),
    },
    EmbeddedAdapterSource {
        implementation: &HERMES,
        descriptor_yaml: include_str!("hermes.yml"),
        qualification_json: include_str!("hermes.qualification.json"),
    },
];
