use std::fs;
use std::path::PathBuf;

use harness_desktop_lib::contexts::correlation::{
    normalized_relative_locator, project, ArtifactJoinKey, CorrelationAvailability,
    CorrelationState, DestinationKey, DestinationScope, QualifiedArtifactProjection,
    QualifiedArtifactProjector, QualifiedArtifactRecord,
};
use harness_desktop_lib::contexts::install::{
    GeneratedArtifact, GeneratedArtifactSet, InstallEvidenceRecord, InstallEvidenceSnapshot,
};
use harness_desktop_lib::contexts::local::domain::{LocalInstance, ParseState, Scope};
use harness_desktop_lib::contexts::local::{
    LocalSnapshot, LocalSnapshotStatus, SurfaceKind, ToolId,
};
use sha2::Digest;

fn source(relative: &str) -> String {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    fs::read_to_string(root.join(relative)).unwrap_or_default()
}

#[test]
fn shared_artifact_generation_seam_is_declared_for_install_and_projection_consumers() {
    let coordinator = source("src/contexts/install/coordinator.rs");
    let sot = source("src/contexts/sot/context.rs");
    let runtime = source("src/contexts/install/runtime.rs");
    let entrypoint = source("resources/install-runtime-entry/install_entry.py");
    let plan = source("../scripts/install/plan.py");

    assert!(coordinator.contains("pub trait ArtifactGenerationPort"));
    assert!(coordinator.contains("artifact_generator: Arc<dyn ArtifactGenerationPort>"));
    assert!(sot.contains("artifact_generator: Option<Arc<dyn ArtifactGenerationPort>>"));
    assert!(coordinator.contains(".generate_artifacts(&canonical_artifact_request)"));
    assert!(sot.contains(".generate_artifacts(&request)"));
    assert!(coordinator.contains("GeneratedArtifactSet"));
    assert!(runtime.contains("ArtifactRunRequest"));
    assert!(entrypoint.contains("artifact-projection"));
    assert!(plan.contains("def build_artifact_projection("));
}

#[test]
fn artifact_generation_reports_observed_revisions_and_sot_rejects_stale_projection() {
    let coordinator = source("src/contexts/install/coordinator.rs");
    let sot = source("src/contexts/sot/context.rs");

    assert!(coordinator.contains("target_contract_revision"));
    assert!(!coordinator.contains("wire.into_domain(&request.source_revision)"));
    assert!(!coordinator
        .contains("generate_artifacts(\n        &self,\n        workspace: &InstallWorkspace"));
    assert!(!coordinator.contains("_checkout_root: &Path"));
    assert!(!coordinator.contains("_app_temp_root: &Path"));
    assert!(!coordinator.contains("checkout_root: PathBuf,\n    app_temp_root: PathBuf,"));
    assert!(!coordinator
        .contains("generate_canonical_artifacts(\n        &self,\n        checkout_root"));
    assert!(!coordinator.contains("SotContext::observe_source_revision"));
    assert!(!sot.contains("pub(crate) fn observe_source_revision"));
    assert!(!sot.contains("generate_canonical_artifacts(checkout.root()"));
    assert!(sot.contains("artifact_projection_stale"));
    assert!(sot.contains("generated.source_revision"));
    assert!(sot.contains("snapshot.checkout_summary.source_revision"));
}

#[test]
fn canonical_artifact_request_is_path_free_and_carries_complete_input_identity() {
    let coordinator = source("src/contexts/install/coordinator.rs");
    let request = coordinator
        .split("pub struct CanonicalArtifactRequest {")
        .nth(1)
        .and_then(|tail| tail.split("}\n\n").next())
        .expect("canonical artifact request declaration");

    for identity in [
        "sot_snapshot_id",
        "source_revision",
        "component_ids",
        "target_ids",
        "qualified_adapter_ids",
        "qualified_adapter_revision",
        "target_contract_revision",
    ] {
        assert!(
            request.contains(identity),
            "missing request identity: {identity}"
        );
    }
    for forbidden in [
        "Path",
        "PathBuf",
        "checkout",
        "RegisteredCheckout",
        "InstallWorkspace",
    ] {
        assert!(
            !request.contains(forbidden),
            "path or checkout authority leaked into canonical request: {forbidden}"
        );
    }
}

#[test]
fn correlation_declares_qualified_artifact_projection_and_all_exact_join_states() {
    let module = source("src/contexts/correlation/mod.rs");
    let projector = source("src/contexts/correlation/projector.rs");

    for declaration in [
        "pub struct QualifiedArtifactProjection",
        "pub struct QualifiedArtifactRecord",
        "pub struct ArtifactJoinKey",
        "pub enum CorrelationAvailability",
    ] {
        assert!(
            module.contains(declaration),
            "missing declaration: {declaration}"
        );
    }
    for state in [
        "Verified",
        "Drift",
        "Ambiguous",
        "Uncorrelated",
        "Unavailable",
    ] {
        assert!(
            projector.contains(state),
            "missing exact join state: {state}"
        );
    }
}

#[test]
fn production_projection_uses_active_sot_artifacts_not_install_evidence_as_authority() {
    let controller = source("src/app_controller.rs");
    let sot = source("src/contexts/sot/context.rs");

    assert!(sot.contains("qualified_artifact_projection"));
    assert!(controller.contains("qualified_artifact_projection"));
    assert!(controller.contains("&qualified_artifact_projection"));
    assert!(!controller.contains("project(&local.snapshot, sot.as_ref(), evidence.as_ref())"));
}

fn destination(root_identity: &str, locator: &str) -> DestinationKey {
    DestinationKey {
        tool_id: "codex".to_string(),
        surface_id: "codex_project".to_string(),
        scope: DestinationScope::Project,
        root_identity: root_identity.to_string(),
        normalized_relative_locator: normalized_relative_locator(PathBuf::from(locator).as_path())
            .expect("fixture locator"),
        config_entry_locator: None,
    }
}

fn local(content_hash: &str, root_identity: &str, locator: &str) -> LocalSnapshot {
    LocalSnapshot {
        snapshot_id: "local-snapshot-a".to_string(),
        attempt_id: "attempt-a".to_string(),
        status: LocalSnapshotStatus::Complete,
        scan_timestamp: "2026-07-18T00:00:00Z".to_string(),
        app_version: "0.1.0-test".to_string(),
        adapter_set_fingerprint: "local-adapter-set".to_string(),
        content_fingerprint: "local-content".to_string(),
        qualified_tools: Vec::new(),
        instances: vec![LocalInstance {
            instance_id: "instance-a".to_string(),
            root_id: "root-a".to_string(),
            adapter_id: "codex".to_string(),
            adapter_version: "0.1.0".to_string(),
            tool_id: ToolId::Codex,
            surface_id: "codex_project".to_string(),
            scope: Scope::Project,
            project_id: Some("project-a".to_string()),
            stable_source_locator: locator.to_string(),
            correlation_key: Some(destination(root_identity, locator)),
            kind: SurfaceKind::Skill,
            name: Some("Fixture".to_string()),
            description: None,
            description_source: None,
            settings: Vec::new(),
            content_hash: Some(content_hash.to_string()),
            size: Some(12),
            modified_unix_millis: Some(1),
            parse_state: ParseState::Parsed,
            issue_codes: Vec::new(),
        }],
        coverage: Vec::new(),
        skipped_paths: Vec::new(),
        issues: Vec::new(),
        project_ignore_summaries: Vec::new(),
    }
}

fn artifact(component_id: &str, hash: &str, locator: &str) -> QualifiedArtifactRecord {
    QualifiedArtifactRecord {
        artifact_record_ref: format!("artifact-{component_id}"),
        component_id: component_id.to_string(),
        adapter_id: "codex".to_string(),
        adapter_version: "0.1.0".to_string(),
        tool_id: "codex".to_string(),
        surface_id: "codex_project".to_string(),
        scope: DestinationScope::Project,
        normalized_target_locator: normalized_relative_locator(PathBuf::from(locator).as_path())
            .expect("fixture locator"),
        config_entry_locator: None,
        content_sha256: hash.to_string(),
    }
}

fn artifact_projection(records: Vec<QualifiedArtifactRecord>) -> QualifiedArtifactProjection {
    QualifiedArtifactProjection {
        artifact_projection_id: "artifact-projection-a".to_string(),
        projector_version: "qualified-artifact-projector-v1".to_string(),
        sot_snapshot_id: "sot-snapshot-a".to_string(),
        source_revision: "source-revision-a".to_string(),
        adapter_set_revision: "a".repeat(64),
        availability: CorrelationAvailability::Available,
        records,
        issues: Vec::new(),
    }
}

#[test]
fn pure_exact_join_covers_verified_drift_ambiguous_uncorrelated_and_restart_identity() {
    let local = local(
        "a".repeat(64).as_str(),
        "physical-root-a",
        ".codex/fixture.md",
    );
    let exact = artifact_projection(vec![artifact(
        "harnesskit.skill.fixture",
        "a".repeat(64).as_str(),
        ".codex/fixture.md",
    )]);

    let verified = project(&local, &exact, None).expect("verified projection");
    assert_eq!(verified, project(&local, &exact, None).unwrap());
    assert_eq!(
        verified.correlations[0].correlation,
        CorrelationState::Verified {
            component_id: "harnesskit.skill.fixture".to_string(),
            method: "exact_artifact".to_string(),
            artifact_record_ref: "artifact-harnesskit.skill.fixture".to_string(),
            install_evidence_ref: None,
        }
    );

    let drift = artifact_projection(vec![artifact(
        "harnesskit.skill.fixture",
        "b".repeat(64).as_str(),
        ".codex/fixture.md",
    )]);
    assert!(matches!(
        project(&local, &drift, None).unwrap().correlations[0].correlation,
        CorrelationState::Drift { .. }
    ));

    let ambiguous = artifact_projection(vec![
        artifact(
            "harnesskit.skill.fixture",
            "a".repeat(64).as_str(),
            ".codex/fixture.md",
        ),
        artifact(
            "harnesskit.skill.other",
            "a".repeat(64).as_str(),
            ".codex/fixture.md",
        ),
    ]);
    assert!(matches!(
        project(&local, &ambiguous, None).unwrap().correlations[0].correlation,
        CorrelationState::Ambiguous { .. }
    ));

    let no_candidate = artifact_projection(vec![artifact(
        "harnesskit.skill.fixture",
        "a".repeat(64).as_str(),
        ".codex/other.md",
    )]);
    assert_eq!(
        project(&local, &no_candidate, None).unwrap().correlations[0].correlation,
        CorrelationState::Uncorrelated
    );
}

#[test]
fn pure_join_ignores_physical_root_identity_and_install_evidence_is_supplemental() {
    let local = local(
        "a".repeat(64).as_str(),
        "physical-root-b",
        ".codex/fixture.md",
    );
    let artifacts = artifact_projection(vec![artifact(
        "harnesskit.skill.fixture",
        "a".repeat(64).as_str(),
        ".codex/fixture.md",
    )]);
    let evidence = InstallEvidenceSnapshot {
        evidence_id: "evidence-a".to_string(),
        sot_snapshot_id: "sot-snapshot-a".to_string(),
        install_semantic_fingerprint: "semantic-a".to_string(),
        verified_records: vec![InstallEvidenceRecord {
            component_id: "harnesskit.skill.fixture".to_string(),
            target_id: "codex".to_string(),
            destination_key: destination("different-physical-root", ".codex/fixture.md"),
            content_sha256: "a".repeat(64),
        }],
    };

    let without = project(&local, &artifacts, None).unwrap();
    let with = project(&local, &artifacts, Some(&evidence)).unwrap();
    assert_eq!(without.projection_id, with.projection_id);
    assert!(matches!(
        with.correlations[0].correlation,
        CorrelationState::Verified {
            install_evidence_ref: Some(ref evidence_ref),
            ..
        } if evidence_ref == "evidence-a"
    ));
}

#[test]
fn unavailable_artifact_projection_never_guesses_item_state() {
    let local = local("a".repeat(64).as_str(), "root-a", ".codex/fixture.md");
    let mut unavailable = artifact_projection(Vec::new());
    unavailable.availability = CorrelationAvailability::Unavailable {
        safe_reason: "qualified adapter unavailable".to_string(),
    };
    let projection = project(&local, &unavailable, None).unwrap();
    assert!(matches!(
        projection.availability,
        CorrelationAvailability::Unavailable { .. }
    ));
    assert_eq!(
        projection.correlations[0].correlation,
        CorrelationState::Uncorrelated
    );
}

#[test]
fn qualified_projector_normalizes_target_locator_without_target_write_authority() {
    let exact = b"fixture bytes".to_vec();
    let generated = GeneratedArtifactSet {
        generator_version: "canonical-artifact-generator-v1".to_string(),
        source_revision: "source-revision-a".to_string(),
        adapter_set_revision: "a".repeat(64),
        target_contract_revision: "b".repeat(64),
        records: vec![GeneratedArtifact {
            component_id: "harnesskit.skill.fixture".to_string(),
            adapter_id: "harnesskit.adapter.codex".to_string(),
            adapter_version: "0.1.0".to_string(),
            target: "codex".to_string(),
            scope: "project".to_string(),
            destination: ".agents/skills/fixture/SKILL.md".to_string(),
            source: "dist/codex/.agents/skills/fixture/SKILL.md".to_string(),
            merge_strategy: None,
            config_entry_locator: None,
            content_sha256: format!("{:x}", sha2::Sha256::digest(&exact)),
            exact_bytes: exact,
        }],
        issues: Vec::new(),
    };

    let projection = QualifiedArtifactProjector.project("sot-snapshot-a", generated);
    assert!(matches!(
        projection.availability,
        CorrelationAvailability::Available
    ));
    assert_eq!(projection.records.len(), 1);
    assert_eq!(
        projection.records[0].join_key(),
        ArtifactJoinKey {
            adapter_id: "codex".to_string(),
            tool_id: "codex".to_string(),
            surface_id: "codex_project".to_string(),
            scope: DestinationScope::Project,
            normalized_target_locator: normalized_relative_locator(
                PathBuf::from(".agents/skills/fixture/SKILL.md").as_path()
            )
            .unwrap(),
            config_entry_locator: None,
        }
    );
}

#[test]
fn qualified_projector_ignores_install_authority_records_without_correlation_bindings() {
    let exact = b"[agents]\n".to_vec();
    let generated = GeneratedArtifactSet {
        generator_version: "canonical-artifact-generator-v1".to_string(),
        source_revision: "source-revision-a".to_string(),
        adapter_set_revision: "a".repeat(64),
        target_contract_revision: "b".repeat(64),
        records: vec![GeneratedArtifact {
            component_id: "harnesskit.agent.fixture".to_string(),
            adapter_id: "harnesskit.adapter.codex".to_string(),
            adapter_version: "0.1.0".to_string(),
            target: "codex".to_string(),
            scope: "project".to_string(),
            destination: ".codex/config.toml".to_string(),
            source: "dist/codex/.codex/config.toml".to_string(),
            merge_strategy: Some("toml-agents-merge".to_string()),
            config_entry_locator: Some("codex-agents".to_string()),
            content_sha256: format!("{:x}", sha2::Sha256::digest(&exact)),
            exact_bytes: exact,
        }],
        issues: Vec::new(),
    };

    let projection = QualifiedArtifactProjector.project("sot-snapshot-a", generated);

    assert!(matches!(
        projection.availability,
        CorrelationAvailability::Unavailable { .. }
    ));
    assert!(projection.records.is_empty());
    assert!(projection.issues.is_empty());
}

#[test]
fn generic_project_artifact_resolves_through_each_qualified_discovery_binding() {
    let exact = b"# Shared project rule\n".to_vec();
    let generated = GeneratedArtifactSet {
        generator_version: "canonical-artifact-generator-v1".to_string(),
        source_revision: "source-revision-a".to_string(),
        adapter_set_revision: "a".repeat(64),
        target_contract_revision: "b".repeat(64),
        records: vec![GeneratedArtifact {
            component_id: "harnesskit.rule.project-context".to_string(),
            adapter_id: "project".to_string(),
            adapter_version: "generator-revision".to_string(),
            target: "project".to_string(),
            scope: "project".to_string(),
            destination: "AGENTS.md".to_string(),
            source: "dist/project/AGENTS.md".to_string(),
            merge_strategy: Some("managed-block".to_string()),
            config_entry_locator: None,
            content_sha256: format!("{:x}", sha2::Sha256::digest(&exact)),
            exact_bytes: exact,
        }],
        issues: Vec::new(),
    };

    let projection = QualifiedArtifactProjector.project("sot-snapshot-a", generated);
    assert!(matches!(
        projection.availability,
        CorrelationAvailability::Available
    ));
    assert_eq!(projection.adapter_set_revision.len(), 64);
    assert_eq!(
        projection
            .records
            .iter()
            .map(|record| (
                record.adapter_id.as_str(),
                record.tool_id.as_str(),
                record.surface_id.as_str(),
            ))
            .collect::<Vec<_>>(),
        [
            (
                "antigravity-cli",
                "antigravity_cli",
                "antigravity_cli_project",
            ),
            ("codex", "codex", "codex_project"),
        ]
    );
}
