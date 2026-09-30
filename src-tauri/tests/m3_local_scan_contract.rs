use std::fs;
use std::path::Path;

use harness_desktop_lib::contexts::local::domain::Scope;
use harness_desktop_lib::contexts::local::domain::{ParseState, StableIdentity};
use harness_desktop_lib::contexts::local::parser::parse_document;
use harness_desktop_lib::contexts::local::roots::{
    AuthorizedRootRegistry, RootEnumeratorId, RootSpec,
};
use harness_desktop_lib::contexts::local::scanner::{derive_instance_id, LocalScanner};
use harness_desktop_lib::contexts::local::{AdapterCatalog, ParserId, SurfaceKind, ToolId};
use tempfile::tempdir;

fn write(path: &Path, body: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, body).unwrap();
}

#[test]
fn local_context_exposes_only_the_bounded_scan_modules() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let module = fs::read_to_string(root.join("src/contexts/local/mod.rs")).unwrap();

    for expected in [
        "pub mod domain;",
        "pub mod roots;",
        "pub mod parser;",
        "pub mod scanner;",
        "ProjectLocationRecord",
    ] {
        assert!(
            module.contains(expected),
            "missing local scan seam: {expected}"
        );
    }
    for forbidden in ["checkout", "registry", "install", "subprocess", "dist"] {
        assert!(
            !module.to_ascii_lowercase().contains(forbidden),
            "Local scan module imports forbidden authority: {forbidden}"
        );
    }
}

#[test]
fn authorized_root_api_is_compiled_enum_only() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let source = fs::read_to_string(root.join("src/contexts/local/roots.rs")).unwrap();

    assert!(source.contains("pub enum RootEnumeratorId"));
    assert!(source.contains("pub struct AuthorizedRootRegistry"));
    assert!(source.contains("pub fn discover"));
    for forbidden in ["scan_root", "arbitrary_root", "user_supplied_root"] {
        assert!(
            !source.contains(forbidden),
            "forbidden root API: {forbidden}"
        );
    }
}

#[test]
fn root_discovery_is_marker_bounded_symlink_safe_and_deterministic() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    write(&home.join(".codex/skills/user/SKILL.md"), "# User\n");
    write(&home.join("workspace/zeta/.codex/agents/a.md"), "# Agent\n");
    write(&home.join("workspace/alpha/AGENTS.md"), "# Rules\n");
    write(
        &home.join("workspace/node_modules/dependency/.codex/agents/ignored.md"),
        "# Ignored\n",
    );
    let outside = fixture.path().join("outside");
    fs::create_dir_all(&outside).unwrap();
    fs::create_dir_all(home.join("workspace/link")).unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, home.join("workspace/link/.codex")).unwrap();

    let specs = [
        RootSpec {
            adapter_id: "codex".to_string(),
            adapter_version: "1.0.0".to_string(),
            tool_id: "codex".to_string(),
            surface_id: "codex-user".to_string(),
            scope: Scope::User,
            enumerator_id: RootEnumeratorId::CodexHomeMarkersV1,
        },
        RootSpec {
            adapter_id: "codex".to_string(),
            adapter_version: "1.0.0".to_string(),
            tool_id: "codex".to_string(),
            surface_id: "codex-project".to_string(),
            scope: Scope::Project,
            enumerator_id: RootEnumeratorId::CodexHomeMarkersV1,
        },
    ];

    let first = AuthorizedRootRegistry::discover(&home, &specs).unwrap();
    let second = AuthorizedRootRegistry::discover(&home, &specs).unwrap();
    let canonical_home = fs::canonicalize(&home).unwrap();
    let roots = first
        .roots
        .iter()
        .map(|root| {
            (
                root.scope,
                root.canonical_root
                    .strip_prefix(&canonical_home)
                    .unwrap()
                    .to_string_lossy()
                    .to_string(),
            )
        })
        .collect::<Vec<_>>();

    assert_eq!(
        roots,
        [
            (Scope::Project, "workspace/alpha".to_string()),
            (Scope::Project, "workspace/zeta".to_string()),
            (Scope::User, ".codex".to_string()),
        ]
    );
    assert_eq!(first, second);
    assert!(first
        .issues
        .iter()
        .any(|issue| issue.code == "symlink_marker_rejected"));
    assert!(first.roots.iter().all(|root| {
        !root
            .canonical_root
            .to_string_lossy()
            .contains("node_modules")
    }));
}

#[test]
fn one_project_root_has_one_identity_across_ai_tools() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    write(
        &home.join("workspace/routine-harness/AGENTS.md"),
        "# Rules\n",
    );
    let specs = [
        RootSpec {
            adapter_id: "codex".to_string(),
            adapter_version: "1.0.0".to_string(),
            tool_id: "codex".to_string(),
            surface_id: "codex-project".to_string(),
            scope: Scope::Project,
            enumerator_id: RootEnumeratorId::CodexHomeMarkersV1,
        },
        RootSpec {
            adapter_id: "claude-code".to_string(),
            adapter_version: "1.0.0".to_string(),
            tool_id: "claude_code".to_string(),
            surface_id: "claude-project".to_string(),
            scope: Scope::Project,
            enumerator_id: RootEnumeratorId::CodexHomeMarkersV1,
        },
    ];

    let registry = AuthorizedRootRegistry::discover(&home, &specs).unwrap();
    assert_eq!(registry.roots.len(), 2);
    assert_eq!(
        registry.roots[0].canonical_root,
        registry.roots[1].canonical_root
    );
    assert_eq!(registry.roots[0].project_id, registry.roots[1].project_id);
    assert_ne!(registry.roots[0].root_id, registry.roots[1].root_id);
}

#[test]
fn parser_preserves_safe_metadata_entries_and_redacts_bodies() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/discovery/scan/home");
    let skill = fs::read(root.join(".codex/skills/release/SKILL.md")).unwrap();
    let parsed = parse_document(
        ParserId::SkillFrontmatterV1,
        "skills/release/SKILL.md",
        SurfaceKind::Skill,
        &skill,
    );
    assert_eq!(parsed.items.len(), 1);
    assert_eq!(parsed.items[0].name.as_deref(), Some("release-safety"));
    assert_eq!(parsed.items[0].parse_state, ParseState::Parsed);

    let hooks = fs::read(root.join(".codex/hooks.json")).unwrap();
    let parsed = parse_document(
        ParserId::HookJsonV1,
        "hooks.json",
        SurfaceKind::Hook,
        &hooks,
    );
    assert_eq!(parsed.items.len(), 1);
    assert_eq!(
        parsed.items[0].stable_source_locator,
        "hooks.json#/hooks/PreToolUse/0"
    );
    assert!(parsed.items[0]
        .settings
        .iter()
        .any(|setting| setting.key == "api_key" && setting.redacted));

    let serialized = serde_json::to_string(&parsed).unwrap();
    for forbidden in [
        "PRIVATE_PROMPT_BODY_MUST_NOT_BE_INDEXED",
        "RAW_HOOK_COMMAND_MUST_NOT_BE_INDEXED",
        "SUPER_SECRET_VALUE",
    ] {
        assert!(
            !serialized.contains(forbidden),
            "leaked source value: {forbidden}"
        );
    }

    let malformed = fs::read(root.join(".claude/settings.local.json")).unwrap();
    let parsed = parse_document(
        ParserId::ClaudeSettingsV1,
        "settings.local.json",
        SurfaceKind::Hook,
        &malformed,
    );
    assert_eq!(parsed.items.len(), 1);
    assert_eq!(parsed.items[0].parse_state, ParseState::Malformed);
    assert_eq!(parsed.issues[0].code, "malformed_document");
    assert!(!serde_json::to_string(&parsed).unwrap().contains("expected"));
}

#[test]
fn nested_hook_groups_emit_one_item_per_handler_pointer() {
    let body = br#"{
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Bash",
            "hooks": [
              {"type": "command", "command": "PRIVATE_COMMAND_ONE"},
              {"type": "prompt", "prompt": "PRIVATE_PROMPT_TWO"}
            ]
          }
        ]
      }
    }"#;

    for parser_id in [ParserId::ClaudeSettingsV1, ParserId::HookJsonV1] {
        let parsed = parse_document(parser_id, "hooks.json", SurfaceKind::Hook, body);
        assert_eq!(parsed.items.len(), 2);
        assert_eq!(
            parsed
                .items
                .iter()
                .map(|item| item.stable_source_locator.as_str())
                .collect::<Vec<_>>(),
            [
                "hooks.json#/hooks/PreToolUse/0/hooks/0",
                "hooks.json#/hooks/PreToolUse/0/hooks/1",
            ]
        );
        let serialized = serde_json::to_string(&parsed).unwrap();
        assert!(!serialized.contains("PRIVATE_COMMAND_ONE"));
        assert!(!serialized.contains("PRIVATE_PROMPT_TWO"));
    }
}

#[test]
fn plugin_manifest_emits_one_manifest_and_only_explicit_typed_children() {
    let body = br#"{
      "$schema": "https://example.invalid/plugin.schema.json",
      "name": "review-pack",
      "description": "Safe manifest description",
      "version": "1.0.0",
      "metadata": {"author": "fixture"},
      "skills": {"review": {"path": "skills/review/SKILL.md"}},
      "commands": {"release": {"path": "commands/release.md"}}
    }"#;

    let parsed = parse_document(
        ParserId::AntigravityManifestV1,
        "plugin.json",
        SurfaceKind::Unclassified,
        body,
    );

    assert_eq!(parsed.items.len(), 3);
    assert_eq!(parsed.items[0].stable_source_locator, "plugin.json");
    assert_eq!(parsed.items[0].kind, SurfaceKind::Unclassified);
    assert_eq!(parsed.items[0].name.as_deref(), Some("review-pack"));
    assert_eq!(
        parsed.items[0].description.as_deref(),
        Some("Safe manifest description")
    );
    assert!(parsed.items.iter().any(|item| {
        item.stable_source_locator == "plugin.json#/skills/review"
            && item.kind == SurfaceKind::Skill
    }));
    assert!(parsed.items.iter().any(|item| {
        item.stable_source_locator == "plugin.json#/commands/release"
            && item.kind == SurfaceKind::Command
    }));
    for scalar in ["$schema", "name", "description", "version", "author"] {
        assert!(
            parsed
                .items
                .iter()
                .all(|item| item.name.as_deref() != Some(scalar)),
            "scalar key became an item: {scalar}"
        );
    }
}

#[test]
fn codex_toml_is_one_conservative_manifest_instead_of_section_items() {
    let body = br#"
model = "gpt-fixture"
[projects.fixture]
trust_level = "trusted"
[agents.reviewer]
description = "review agent"
[skills.docs]
enabled = true
"#;

    let config = parse_document(
        ParserId::CodexTomlV1,
        "config.toml",
        SurfaceKind::Unclassified,
        body,
    );
    assert_eq!(config.items.len(), 1);
    assert_eq!(config.items[0].stable_source_locator, "config.toml");
    assert_eq!(config.items[0].kind, SurfaceKind::Unclassified);

    let agent = parse_document(
        ParserId::CodexTomlV1,
        "agents/reviewer.toml",
        SurfaceKind::Agent,
        body,
    );
    assert_eq!(agent.items.len(), 1);
    assert_eq!(agent.items[0].stable_source_locator, "agents/reviewer.toml");
    assert_eq!(agent.items[0].kind, SurfaceKind::Agent);
}

#[test]
fn hermes_yaml_emits_hook_key_items_but_not_external_root_metadata() {
    let body = br#"
name: hermes-config
hooks:
  PreToolUse:
    command: PRIVATE_HERMES_COMMAND
  Stop:
    enabled: true
skills:
  external_dirs:
    - /private/external/skills
"#;

    let parsed = parse_document(
        ParserId::HermesYamlV1,
        "config.yaml",
        SurfaceKind::Unclassified,
        body,
    );

    assert_eq!(parsed.items.len(), 3);
    assert!(parsed.items.iter().any(|item| {
        item.stable_source_locator == "config.yaml#/hooks/PreToolUse"
            && item.kind == SurfaceKind::Hook
    }));
    assert!(parsed.items.iter().any(|item| {
        item.stable_source_locator == "config.yaml#/hooks/Stop" && item.kind == SurfaceKind::Hook
    }));
    assert!(parsed.items.iter().all(|item| {
        !item.stable_source_locator.contains("external_dirs")
            && item.name.as_deref() != Some("external_dirs")
    }));
    assert!(!serde_json::to_string(&parsed)
        .unwrap()
        .contains("PRIVATE_HERMES_COMMAND"));
}

#[test]
fn instance_identity_uses_only_the_approved_stable_tuple() {
    let identity = StableIdentity {
        identity_namespace: "local-discovery-v1".to_string(),
        tool_id: ToolId::Codex,
        surface_id: "codex-user".to_string(),
        scope: Scope::User,
        project_id: None,
        locator_version: 1,
        stable_source_locator: "skills/release/SKILL.md".to_string(),
    };
    let first = derive_instance_id(&identity);
    let second = derive_instance_id(&identity);
    assert_eq!(first, second);
    assert_eq!(first.len(), 64);

    let mut changed = identity.clone();
    changed.tool_id = ToolId::ClaudeCode;
    assert_ne!(first, derive_instance_id(&changed));

    let scanner = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("src/contexts/local/scanner.rs"),
    )
    .unwrap();
    for forbidden in [
        "kind",
        "name",
        "mtime",
        "modified",
        "content_hash",
        "adapter_version",
    ] {
        assert!(
            !scanner.contains(&format!("identity.{forbidden}")),
            "volatile identity input: {forbidden}"
        );
    }
}

fn tree_snapshot(root: &Path) -> Vec<(String, Vec<u8>)> {
    fn visit(root: &Path, directory: &Path, files: &mut Vec<(String, Vec<u8>)>) {
        let mut entries = fs::read_dir(directory)
            .unwrap()
            .map(Result::unwrap)
            .collect::<Vec<_>>();
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path).unwrap();
            if metadata.is_dir() {
                visit(root, &path, files);
            } else if metadata.is_file() {
                files.push((
                    path.strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .to_string(),
                    fs::read(path).unwrap(),
                ));
            }
        }
    }
    let mut files = Vec::new();
    visit(root, root, &mut files);
    files
}

#[test]
fn scanner_is_read_only_deterministic_and_preserves_all_tool_bindings() {
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/discovery/scan");
    let home = fixture.join("home");
    let before = tree_snapshot(&fixture);
    let catalog = AdapterCatalog::load_embedded().unwrap();

    let first = LocalScanner::scan(&home, catalog.adapters()).unwrap();
    let second = LocalScanner::scan(&home, catalog.adapters()).unwrap();
    assert_eq!(first, second);
    assert_eq!(
        before,
        tree_snapshot(&fixture),
        "scanner changed source/config files"
    );

    let tools = first
        .instances
        .iter()
        .map(|instance| instance.tool_id)
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(
        tools,
        [
            ToolId::Codex,
            ToolId::ClaudeCode,
            ToolId::Antigravity,
            ToolId::AntigravityCli,
            ToolId::Hermes,
        ]
        .into_iter()
        .collect()
    );
    let kinds = first
        .instances
        .iter()
        .map(|instance| instance.kind)
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(
        kinds,
        [
            SurfaceKind::Skill,
            SurfaceKind::Agent,
            SurfaceKind::Hook,
            SurfaceKind::Rule,
            SurfaceKind::Command,
            SurfaceKind::Workflow,
            SurfaceKind::Unclassified,
        ]
        .into_iter()
        .collect()
    );

    for locator in [
        ".gemini/antigravity-cli/skills/direct.md",
        ".gemini/antigravity-cli/skills/plugin/SKILL.md",
    ] {
        assert!(first.instances.iter().any(|instance| {
            instance.tool_id == ToolId::AntigravityCli
                && instance.stable_source_locator == locator
                && instance.kind == SurfaceKind::Skill
        }));
    }
    assert!(first.instances.iter().any(|instance| {
        instance.tool_id == ToolId::ClaudeCode
            && instance
                .stable_source_locator
                .ends_with("output-styles/concise.md")
            && instance.kind == SurfaceKind::Rule
    }));
    assert!(first.instances.iter().any(|instance| {
        instance.tool_id == ToolId::Hermes
            && instance.stable_source_locator == "external.md"
            && instance.scope == Scope::Project
    }));

    let shared = first
        .instances
        .iter()
        .filter(|instance| instance.stable_source_locator == ".agents/agents/shared.md")
        .map(|instance| instance.tool_id)
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(
        shared,
        [ToolId::Antigravity, ToolId::AntigravityCli]
            .into_iter()
            .collect(),
        "shared candidates: {:?}",
        first
            .instances
            .iter()
            .filter(|instance| instance.name.as_deref() == Some("shared-project-agent"))
            .map(|instance| (
                &instance.stable_source_locator,
                instance.tool_id,
                instance.scope
            ))
            .collect::<Vec<_>>()
    );
    assert!(first
        .instances
        .iter()
        .any(|instance| instance.parse_state == ParseState::Malformed));

    let serialized = serde_json::to_string(&first).unwrap();
    for forbidden in [
        "PRIVATE_PROMPT_BODY_MUST_NOT_BE_INDEXED",
        "RAW_HOOK_COMMAND_MUST_NOT_BE_INDEXED",
        "SUPER_SECRET_VALUE",
        "WORKFLOW_BODY_MUST_NOT_BE_INDEXED",
    ] {
        assert!(
            !serialized.contains(forbidden),
            "scanner leaked source value: {forbidden}"
        );
    }
}
