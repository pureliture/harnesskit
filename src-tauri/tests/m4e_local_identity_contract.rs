use std::fs;
use std::path::Path;

use harness_desktop_lib::api::dto::local::LocalSourcePreviewContentDto;
use harness_desktop_lib::contexts::local::domain::{
    LocalInstance, ParseState, ProjectIgnoreSummary, Scope,
};
use harness_desktop_lib::contexts::local::parser::resolve_local_display_name;
use harness_desktop_lib::contexts::local::query::{
    query_local_instances, LocalQueryRequest, LocationFilter, LocationScope,
};
use harness_desktop_lib::contexts::local::{
    LocalSnapshot, LocalSnapshotStatus, SurfaceKind, ToolId,
};

fn snapshot_with(instance: LocalInstance) -> LocalSnapshot {
    LocalSnapshot {
        snapshot_id: "m4e-snapshot".to_string(),
        attempt_id: "m4e-attempt".to_string(),
        status: LocalSnapshotStatus::Complete,
        scan_timestamp: "2026-07-18T00:00:00Z".to_string(),
        app_version: "0.1.0-test".to_string(),
        adapter_set_fingerprint: "adapter-fingerprint".to_string(),
        content_fingerprint: "content-fingerprint".to_string(),
        qualified_tools: Vec::new(),
        instances: vec![instance],
        coverage: Vec::new(),
        skipped_paths: Vec::new(),
        issues: Vec::new(),
        project_ignore_summaries: vec![ProjectIgnoreSummary {
            project_id: "project-one".to_string(),
            source_revision: "ignore-revision".to_string(),
            rule_count: 0,
            excluded_path_count: 0,
        }],
    }
}

fn request() -> LocalQueryRequest {
    LocalQueryRequest {
        snapshot_id: "m4e-snapshot".to_string(),
        location_filter: LocationFilter {
            scope: LocationScope::All,
            project_id: None,
        },
        tool_id: None,
        kind: None,
        query: String::new(),
        correlation_projection_id: None,
        verified_component_id: None,
    }
}

fn nameless_instance() -> LocalInstance {
    LocalInstance {
        instance_id: "instance-m4e".to_string(),
        root_id: "root-m4e".to_string(),
        adapter_id: "codex".to_string(),
        adapter_version: "1.0.0".to_string(),
        tool_id: ToolId::Codex,
        surface_id: "codex-user".to_string(),
        scope: Scope::User,
        project_id: None,
        stable_source_locator: ".codex/skills/release/SKILL.md".to_string(),
        correlation_key: None,
        kind: SurfaceKind::Skill,
        name: None,
        description: None,
        description_source: None,
        settings: Vec::new(),
        content_hash: None,
        size: None,
        modified_unix_millis: None,
        parse_state: ParseState::Parsed,
        issue_codes: Vec::new(),
    }
}

#[test]
fn query_derives_a_short_component_display_name_without_promoting_locator() {
    let result = query_local_instances(&snapshot_with(nameless_instance()), &request(), None)
        .expect("current snapshot must be queryable");

    assert_eq!(result.items.len(), 1);
    assert_eq!(result.items[0].display_name, "release");
    assert_eq!(result.items[0].name, None);
    assert_eq!(
        result.items[0].safe_locator,
        ".codex/skills/release/SKILL.md"
    );
}

#[test]
fn generic_parser_file_name_yields_the_enclosing_component_name() {
    assert_eq!(
        resolve_local_display_name(Some("SKILL"), ".codex/skills/release/SKILL.md",),
        "release",
    );
}

#[test]
fn markdown_projection_has_a_dedicated_pure_owner() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/contexts/local");
    let module = fs::read_to_string(root.join("mod.rs")).expect("local module must exist");
    let inspection = fs::read_to_string(root.join("source_inspection.rs"))
        .expect("source inspection must exist");
    let projection = fs::read_to_string(root.join("source_markdown.rs"))
        .expect("Markdown projection must have a dedicated module");

    assert!(module.contains("mod source_markdown;"));
    assert!(inspection.contains("MarkdownProjectionService::project("));
    assert!(projection.contains("pub(crate) struct MarkdownProjectionService"));
    assert!(projection.contains("pub(crate) struct MarkdownCheckpoint"));
    assert!(projection.contains("pub(crate) struct MarkdownBlockFragment"));
    assert!(projection.contains("pub(crate) struct MarkdownScanState"));
    assert!(
        !projection.contains("source_inspection"),
        "pure Markdown projection must not depend on verified I/O/session ownership"
    );
    for legacy_owner in [
        "struct MarkdownCheckpoint",
        "struct MarkdownBlockFragment",
        "struct MarkdownScanState",
        "fn project_markdown_chunk",
    ] {
        assert!(
            !inspection.contains(legacy_owner),
            "source inspection must delegate {legacy_owner} to the Markdown projection boundary"
        );
    }
    for forbidden in ["http", "Command", "Navigation", "raw_html", "image_url"] {
        assert!(
            !projection.contains(forbidden),
            "Markdown semantic DTO must not expose capability: {forbidden}"
        );
    }
}

#[test]
fn markdown_semantic_fixture_round_trips_through_the_public_dto_without_capabilities() {
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/m4e_markdown_semantic_contract.json"))
            .expect("semantic Markdown fixture must be valid JSON");
    let expected = fixture["content"].clone();

    let dto: LocalSourcePreviewContentDto = serde_json::from_value(expected.clone())
        .expect("public Markdown DTO must accept the shared semantic tree fixture");
    let serialized = serde_json::to_value(dto).expect("public Markdown DTO must serialize");

    assert_eq!(serialized, expected);
    let serialized_text = serialized.to_string();
    for key in fixture["forbidden_schema_keys"]
        .as_array()
        .expect("forbidden keys must be an array")
    {
        let key = key.as_str().expect("forbidden key must be text");
        assert!(
            !serialized_text.contains(&format!("\"{key}\":")),
            "semantic DTO must not expose capability key {key}"
        );
    }
}
