use std::fs;
use std::path::PathBuf;

use harness_desktop_lib::api::dto::local::{LocalInstanceDetailDto, LocalQueryResultDto};
use harness_desktop_lib::contexts::correlation::{
    CorrelationAvailability, CorrelationProjection, CorrelationState, InstanceCorrelation,
};
use harness_desktop_lib::contexts::local::domain::{
    LocalInstance, ParseState, ProjectIgnoreSummary, ProjectLocationRecord, SafeSetting, Scope,
};
use harness_desktop_lib::contexts::local::{
    get_local_instance_detail, query_local_instances, LocalQueryRequest, LocalSnapshot,
    LocalSnapshotStatus, LocationFilter, LocationScope, SurfaceKind, ToolId,
};

fn source(relative: &str) -> String {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    fs::read_to_string(root.join(relative)).unwrap_or_default()
}

#[test]
fn local_query_declares_the_exact_revision_and_location_authority() {
    let query = source("src/contexts/local/query.rs");

    for declaration in [
        "pub struct LocalQueryRequest",
        "pub struct LocationFilter",
        "pub enum LocationScope",
        "pub fn query_local_instances",
        "correlation_projection_id",
        "verified_component_id",
    ] {
        assert!(
            query.contains(declaration),
            "missing query seam: {declaration}"
        );
    }
}

#[test]
fn query_dto_requires_project_locations_at_every_conversion_boundary() {
    let dto = source("src/api/dto/local.rs");

    assert!(!dto.contains("impl From<LocalQueryResult> for LocalQueryResultDto"));
    assert!(!dto.contains("pub fn from_domain(\n        value: LocalQueryResult"));
    assert!(dto.contains("pub fn from_domain_with_projects("));
}

struct InstanceSpec<'a> {
    instance_id: &'a str,
    tool_id: ToolId,
    kind: SurfaceKind,
    scope: Scope,
    project_id: Option<&'a str>,
    locator: &'a str,
    name: Option<&'a str>,
    description: Option<&'a str>,
}

fn instance(spec: InstanceSpec<'_>) -> LocalInstance {
    LocalInstance {
        instance_id: spec.instance_id.to_string(),
        root_id: format!("root-{}", spec.instance_id),
        adapter_id: format!("adapter-{}", spec.tool_id.as_str()),
        adapter_version: "1.0.0".to_string(),
        tool_id: spec.tool_id,
        surface_id: format!("surface-{}", spec.instance_id),
        scope: spec.scope,
        project_id: spec.project_id.map(str::to_owned),
        stable_source_locator: spec.locator.to_string(),
        correlation_key: None,
        kind: spec.kind,
        name: spec.name.map(str::to_owned),
        description: spec.description.map(str::to_owned),
        description_source: spec.description.map(|_| "structured_field".to_string()),
        settings: vec![SafeSetting {
            key: "model".to_string(),
            present: true,
            redacted: false,
            value: Some("safe-model".to_string()),
        }],
        content_hash: Some("HASH_ONLY_NEEDLE".to_string()),
        size: Some(64),
        modified_unix_millis: Some(7),
        parse_state: ParseState::Parsed,
        issue_codes: Vec::new(),
    }
}

fn snapshot(instances: Vec<LocalInstance>) -> LocalSnapshot {
    LocalSnapshot {
        snapshot_id: "snapshot-query".to_string(),
        attempt_id: "attempt-query".to_string(),
        status: LocalSnapshotStatus::Complete,
        scan_timestamp: "2026-07-12T00:00:00Z".to_string(),
        app_version: "0.1.0-test".to_string(),
        adapter_set_fingerprint: "adapter-fingerprint".to_string(),
        content_fingerprint: "content-fingerprint".to_string(),
        qualified_tools: Vec::new(),
        instances,
        coverage: Vec::new(),
        skipped_paths: Vec::new(),
        issues: Vec::new(),
        project_ignore_summaries: vec![ProjectIgnoreSummary {
            project_id: "project-one".to_string(),
            source_revision: "ignore-source-revision".to_string(),
            rule_count: 2,
            excluded_path_count: 3,
        }],
    }
}

fn request(query: &str) -> LocalQueryRequest {
    LocalQueryRequest {
        snapshot_id: "snapshot-query".to_string(),
        location_filter: LocationFilter {
            scope: LocationScope::All,
            project_id: None,
        },
        tool_id: None,
        kind: None,
        query: query.to_string(),
        correlation_projection_id: None,
        verified_component_id: None,
    }
}

#[test]
fn query_searches_only_safe_text_case_insensitively_with_stable_ordering() {
    let snapshot = snapshot(vec![
        instance(InstanceSpec {
            instance_id: "instance-release",
            tool_id: ToolId::Codex,
            kind: SurfaceKind::Skill,
            scope: Scope::User,
            project_id: None,
            locator: ".codex/skills/release/SKILL.md",
            name: Some("Release Safety"),
            description: Some("Ship documentation"),
        }),
        instance(InstanceSpec {
            instance_id: "instance-guard",
            tool_id: ToolId::ClaudeCode,
            kind: SurfaceKind::Hook,
            scope: Scope::Project,
            project_id: Some("project-a"),
            locator: ".claude/settings.json#/hooks/guard",
            name: Some("Deploy Guard"),
            description: Some("release safety checks"),
        }),
        instance(InstanceSpec {
            instance_id: "instance-docs",
            tool_id: ToolId::Codex,
            kind: SurfaceKind::Rule,
            scope: Scope::Project,
            project_id: Some("project-a"),
            locator: "workspace/DOCS/AGENTS.md",
            name: None,
            description: None,
        }),
    ]);

    let result = query_local_instances(&snapshot, &request("RELEASE"), None).unwrap();
    assert_eq!(
        result
            .items
            .iter()
            .map(|item| item.instance_id.as_str())
            .collect::<Vec<_>>(),
        ["instance-guard", "instance-release"]
    );
    assert_eq!(result.counts.total_instances, 3);
    assert_eq!(result.counts.matched_instances, 2);
    assert_eq!(
        result
            .kind_counts
            .iter()
            .map(|count| (count.kind, count.count))
            .collect::<Vec<_>>(),
        [(SurfaceKind::Skill, 1), (SurfaceKind::Hook, 1)]
    );

    let forbidden = query_local_instances(&snapshot, &request("hash_only_needle"), None).unwrap();
    assert!(forbidden.items.is_empty());
}

#[test]
fn correlation_declares_a_pure_projector_and_one_active_projection_context() {
    let module = source("src/contexts/correlation/mod.rs");
    let context = source("src/contexts/correlation/context.rs");
    let projector = source("src/contexts/correlation/projector.rs");

    assert!(module.contains("pub struct CorrelationProjection"));
    assert!(context.contains("pub struct CorrelationContext"));
    assert!(context.contains("pub fn project"));
    assert!(context.contains("pub fn require"));
    assert!(projector.contains("pub fn project"));
}

#[test]
fn production_correlation_path_is_bound_to_active_sot_qualified_artifacts() {
    let module = source("src/contexts/correlation/mod.rs");
    let context = source("src/contexts/correlation/context.rs");
    let projector = source("src/contexts/correlation/projector.rs");
    let controller = source("src/app_controller.rs");
    let commands = source("src/api/commands.rs");
    let dto = source("src/api/dto/local.rs");

    assert!(module.contains("pub artifact_projection_id: Option<String>"));
    assert!(context.contains("artifacts: &QualifiedArtifactProjection"));
    assert!(projector.contains("artifacts: &QualifiedArtifactProjection"));
    assert!(projector.contains("ArtifactJoinKey"));
    assert!(controller.contains("qualified_artifact_projection"));
    assert!(commands.contains("install_evidence_id: Option<String>"));
    assert!(dto.contains("pub install_evidence_id: Option<String>"));
}

#[test]
fn query_declares_safe_snapshot_summary_and_structured_detail_surfaces() {
    let query = source("src/contexts/local/query.rs");

    for declaration in [
        "pub struct LocalSnapshotSummary",
        "pub struct LocalInstanceDetail",
        "pub fn get_local_instance_detail",
        "pub snapshot_summary:",
    ] {
        assert!(
            query.contains(declaration),
            "missing safe detail seam: {declaration}"
        );
    }
}

#[test]
fn selected_detail_is_revision_bound_and_contains_only_structured_metadata() {
    let local = snapshot(vec![instance(InstanceSpec {
        instance_id: "instance-release",
        tool_id: ToolId::Codex,
        kind: SurfaceKind::Skill,
        scope: Scope::User,
        project_id: None,
        locator: ".codex/skills/release/SKILL.md",
        name: Some("Release Safety"),
        description: Some("Ship documentation"),
    })]);

    let detail = get_local_instance_detail(&local, "snapshot-query", "instance-release").unwrap();
    assert_eq!(detail.instance_id, "instance-release");
    assert_eq!(detail.safe_locator, ".codex/skills/release/SKILL.md");
    assert_eq!(detail.settings[0].key, "model");
    assert_eq!(detail.size, Some(64));
    assert_eq!(
        get_local_instance_detail(&local, "replaced-snapshot", "instance-release").unwrap_err(),
        "snapshot_expired"
    );

    let query_source = source("src/contexts/local/query.rs");
    let detail_shape = query_source
        .split("pub struct LocalInstanceDetail")
        .nth(1)
        .unwrap()
        .split('}')
        .next()
        .unwrap();
    for forbidden in ["content_hash", "raw_path", "body"] {
        assert!(!detail_shape.contains(forbidden));
    }
}

#[test]
fn public_api_declares_safe_query_correlation_and_detail_dtos() {
    let dto = source("src/api/dto/local.rs");
    let commands = source("src/api/commands.rs");
    let controller = source("src/app_controller.rs");

    for declaration in [
        "pub struct LocalQueryRequestDto",
        "pub struct LocalQueryResultDto",
        "pub struct LocalProjectLocationDto",
        "pub struct LocalInstanceDetailDto",
        "pub struct CorrelationProjectionDto",
    ] {
        assert!(
            dto.contains(declaration),
            "missing public DTO: {declaration}"
        );
    }
    assert!(dto.contains("pub projects: Vec<LocalProjectLocationDto>"));
    assert!(dto.contains("pub qualified_tools: Vec<QualifiedToolDto>"));
    for command in [
        "async fn query_local_instances",
        "async fn get_correlation_projection",
        "async fn get_local_instance_detail",
    ] {
        assert!(commands.contains(command), "missing command: {command}");
    }
    for method in [
        "fn query_local_instances",
        "fn get_correlation_projection",
        "fn get_local_instance_detail",
    ] {
        assert!(
            controller.contains(method),
            "missing controller method: {method}"
        );
    }

    for forbidden in ["raw_path", "content_hash", "source_body", "prompt_body"] {
        assert!(!dto.contains(forbidden), "unsafe DTO field: {forbidden}");
    }
}

#[test]
fn sot_input_replacement_explicitly_invalidates_the_active_correlation() {
    let controller = source("src/app_controller.rs");

    for method in [
        "load_sot_snapshot",
        "clone_default_checkout",
        "register_checkout",
    ] {
        let body = controller
            .split(&format!("fn {method}"))
            .nth(1)
            .unwrap_or_else(|| panic!("missing controller method {method}"))
            .split("pub(crate) fn")
            .next()
            .unwrap();
        assert!(
            body.contains("correlation.invalidate"),
            "{method} must invalidate the previous correlation projection"
        );
    }
}

#[test]
fn revision_gate_serializes_projection_publish_use_and_evidence_invalidation() {
    let controller = source("src/app_controller.rs");
    assert!(controller.contains("correlation_revision_gate: Mutex<()>"));

    for method in [
        "load_sot_snapshot",
        "get_correlation_projection",
        "query_local_instances",
        "get_local_instance_detail",
        "clone_default_checkout",
        "register_checkout",
        "apply_install",
    ] {
        let body = controller
            .split(&format!("fn {method}"))
            .nth(1)
            .unwrap_or_else(|| panic!("missing controller method {method}"))
            .split("pub(crate) fn")
            .next()
            .unwrap();
        assert!(
            body.contains("lock_correlation_revision"),
            "{method} must participate in the correlation revision barrier"
        );
    }

    let apply = controller
        .split("fn apply_install")
        .nth(1)
        .unwrap()
        .split("fn next_install_operation_id")
        .next()
        .unwrap();
    let lock = apply.find("lock_correlation_revision").unwrap();
    let invalidate = apply.find("correlation.invalidate").unwrap();
    let mutation = apply.find("sot.apply_install").unwrap();
    assert!(lock < invalidate && invalidate < mutation);

    let load = controller
        .split("fn load_sot_snapshot")
        .nth(1)
        .unwrap()
        .split("pub(crate) fn local_scan_state")
        .next()
        .unwrap();
    assert!(
        load.find("correlation.invalidate").unwrap() < load.find("sot.load_active").unwrap(),
        "a failed SoT reload must not retain a previous Verified projection"
    );

    for (method, mutation) in [
        ("clone_default_checkout", "sot.clone_default_checkout"),
        ("register_checkout", "sot.register_checkout"),
    ] {
        let body = controller
            .split(&format!("fn {method}"))
            .nth(1)
            .unwrap()
            .split("pub(crate) fn")
            .next()
            .unwrap();
        assert!(
            body.find(mutation).unwrap() < body.find("correlation.invalidate").unwrap(),
            "{method} must preserve the current projection when checkout activation fails"
        );
    }

    let projection = controller
        .split("fn get_correlation_projection")
        .nth(1)
        .unwrap()
        .split("pub(crate) fn query_local_instances")
        .next()
        .unwrap();
    assert!(
        projection.find("lock_correlation_revision").unwrap()
            < projection.find("self.correlation").unwrap()
    );
}

#[test]
fn location_authority_and_snapshot_revision_fail_closed_without_reinterpretation() {
    let local = snapshot(vec![
        instance(InstanceSpec {
            instance_id: "project-a-skill",
            tool_id: ToolId::Codex,
            kind: SurfaceKind::Skill,
            scope: Scope::Project,
            project_id: Some("project-a"),
            locator: ".agents/skills/a/SKILL.md",
            name: Some("Alpha"),
            description: None,
        }),
        instance(InstanceSpec {
            instance_id: "project-b-rule",
            tool_id: ToolId::Codex,
            kind: SurfaceKind::Rule,
            scope: Scope::Project,
            project_id: Some("project-b"),
            locator: "AGENTS.md",
            name: Some("Beta"),
            description: None,
        }),
        instance(InstanceSpec {
            instance_id: "user-skill",
            tool_id: ToolId::Codex,
            kind: SurfaceKind::Skill,
            scope: Scope::User,
            project_id: None,
            locator: ".codex/skills/user/SKILL.md",
            name: Some("User"),
            description: None,
        }),
    ]);
    let mut project_request = request("");
    project_request.location_filter = LocationFilter {
        scope: LocationScope::Project,
        project_id: None,
    };
    project_request.kind = Some(SurfaceKind::Skill);

    let result = query_local_instances(&local, &project_request, None).unwrap();
    assert_eq!(
        result
            .items
            .iter()
            .map(|item| item.instance_id.as_str())
            .collect::<Vec<_>>(),
        ["project-a-skill"]
    );
    assert_eq!(
        result
            .kind_counts
            .iter()
            .map(|count| (count.kind, count.count))
            .collect::<Vec<_>>(),
        [(SurfaceKind::Skill, 1), (SurfaceKind::Rule, 1)]
    );

    let mut stale = project_request.clone();
    stale.snapshot_id = "expired".to_string();
    assert_eq!(
        query_local_instances(&local, &stale, None).unwrap_err(),
        "snapshot_expired"
    );
    let mut invalid = project_request;
    invalid.location_filter = LocationFilter {
        scope: LocationScope::All,
        project_id: Some("project-a".to_string()),
    };
    assert_eq!(
        query_local_instances(&local, &invalid, None).unwrap_err(),
        "invalid_location_filter"
    );
}

#[test]
fn serialized_query_and_detail_dtos_exclude_backend_hash_and_raw_path_fields() {
    let local = snapshot(vec![instance(InstanceSpec {
        instance_id: "instance-release",
        tool_id: ToolId::Codex,
        kind: SurfaceKind::Skill,
        scope: Scope::User,
        project_id: None,
        locator: ".codex/skills/release/SKILL.md",
        name: Some("Release Safety"),
        description: Some("Ship documentation"),
    })]);
    let query_result = query_local_instances(&local, &request(""), None).unwrap();
    let query_json = serde_json::to_string(&LocalQueryResultDto::from_domain_with_projects(
        query_result,
        None,
        &[],
    ))
    .unwrap();
    assert!(query_json.contains("snapshotSummary"));
    for forbidden in [
        "contentHash",
        "contentFingerprint",
        "adapterSetFingerprint",
        "canonicalPath",
        "HASH_ONLY_NEEDLE",
    ] {
        assert!(!query_json.contains(forbidden));
    }

    let detail = get_local_instance_detail(&local, "snapshot-query", "instance-release").unwrap();
    let detail_json = serde_json::to_string(&LocalInstanceDetailDto::from_domain(
        detail,
        CorrelationState::Uncorrelated,
    ))
    .unwrap();
    assert!(detail_json.contains("safeLocator"));
    for forbidden in ["contentHash", "canonicalPath", "HASH_ONLY_NEEDLE"] {
        assert!(!detail_json.contains(forbidden));
    }
}

#[test]
fn query_dto_projects_compact_project_ignore_summary_without_exact_text_or_paths() {
    let local = snapshot(Vec::new());
    let query_result = query_local_instances(&local, &request(""), None).unwrap();
    let value = serde_json::to_value(LocalQueryResultDto::from_domain_with_projects(
        query_result,
        None,
        &[],
    ))
    .unwrap();
    let summary = &value["snapshotSummary"]["projectIgnoreSummaries"][0];

    assert_eq!(summary["projectId"], "project-one");
    assert_eq!(summary["sourceRevision"], "ignore-source-revision");
    assert_eq!(summary["ruleCount"], 2);
    assert_eq!(summary["excludedPathCount"], 3);
    let serialized = serde_json::to_string(&value).unwrap();
    for forbidden in ["exactText", "excludedPaths", ".harnesskitignore"] {
        assert!(!serialized.contains(forbidden));
    }
}

#[test]
fn query_dto_allows_full_canonical_path_only_in_project_location_records() {
    let local = snapshot(vec![instance(InstanceSpec {
        instance_id: "instance-project",
        tool_id: ToolId::Codex,
        kind: SurfaceKind::Skill,
        scope: Scope::Project,
        project_id: Some("project-one"),
        locator: ".agents/skills/release/SKILL.md",
        name: Some("Release Safety"),
        description: None,
    })]);
    let result = query_local_instances(&local, &request(""), None).unwrap();
    let projects = vec![ProjectLocationRecord {
        project_id: "project-one".to_string(),
        display_name: "routine-harness".into(),
        root_identity: "backend-only-root-identity".to_string(),
        root_device: 1,
        root_inode: 2,
        owner_uid: 501,
        canonical_path: PathBuf::from("/fixture-home/Projects/routine-harness"),
    }];
    let value = serde_json::to_value(LocalQueryResultDto::from_domain_with_projects(
        result, None, &projects,
    ))
    .unwrap();

    assert_eq!(value["projects"][0]["displayName"], "routine-harness");
    assert_eq!(
        value["projects"][0]["canonicalPath"],
        "/fixture-home/Projects/routine-harness"
    );
    assert!(value["items"][0].get("canonicalPath").is_none());
    assert!(value["snapshotSummary"].get("canonicalPath").is_none());
}

#[test]
fn query_dto_projects_the_revision_bound_correlation_state_for_each_item() {
    let local = snapshot(vec![instance(InstanceSpec {
        instance_id: "instance-release",
        tool_id: ToolId::Codex,
        kind: SurfaceKind::Skill,
        scope: Scope::User,
        project_id: None,
        locator: ".codex/skills/release/SKILL.md",
        name: Some("Release Safety"),
        description: Some("Ship documentation"),
    })]);
    let result = query_local_instances(&local, &request(""), None).unwrap();
    let projection = CorrelationProjection {
        projection_id: "projection-query".to_string(),
        projector_version: "fixture".to_string(),
        local_snapshot_id: local.snapshot_id.clone(),
        sot_snapshot_id: Some("sot-query".to_string()),
        artifact_projection_id: Some("artifact-projection-query".to_string()),
        install_evidence_id: Some("evidence-query".to_string()),
        availability: CorrelationAvailability::Available,
        correlations: vec![InstanceCorrelation {
            instance_id: "instance-release".to_string(),
            correlation: CorrelationState::Verified {
                component_id: "harnesskit.skill.release".to_string(),
                method: "exact_artifact".to_string(),
                artifact_record_ref: "artifact-record-query".to_string(),
                install_evidence_ref: Some("evidence-query".to_string()),
            },
        }],
    };

    let value = serde_json::to_value(LocalQueryResultDto::from_domain_with_projects(
        result,
        Some(&projection),
        &[],
    ))
    .unwrap();
    assert_eq!(value["items"][0]["correlation"]["state"], "verified");
    assert_eq!(
        value["items"][0]["correlation"]["component_id"],
        "harnesskit.skill.release"
    );
}
