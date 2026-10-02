use std::collections::{BTreeSet, VecDeque};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use harness_desktop_lib::contexts::correlation::{
    self, normalized_relative_locator, CorrelationAvailability, CorrelationState, DestinationScope,
    QualifiedArtifactProjection, QualifiedArtifactRecord,
};
use harness_desktop_lib::contexts::install::{
    ArtifactGenerationPort, CanonicalArtifactRequest, DestinationApplyState,
    DestinationVerifyState, GeneratedArtifact, GeneratedArtifactSet, InstallApprovals,
    InstallCoordinator, InstallCoordinatorError, InstallOperationStatus, InstallPlan,
    InstallPlanGenerator, InstallPreviewInput, InstallWorkspace, PlanMode, PlanRunRequest,
    EMBEDDED_TARGET_CONTRACT_SHA256,
};
use harness_desktop_lib::contexts::local::scanner::LocalScanner;
use harness_desktop_lib::contexts::local::{
    AdapterCatalog, LocalSnapshot, LocalSnapshotStatus, ToolId,
};
use harness_desktop_lib::contexts::sot::SotSnapshot;
use serde_json::json;
use sha2::{Digest, Sha256};
use tempfile::TempDir;

fn source(relative: &str) -> String {
    fs::read_to_string(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(relative)).unwrap()
}

fn write_mode(path: &Path, bytes: &[u8], mode: u32) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, bytes).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
}

fn exact_plan(mode: &str) -> InstallPlan {
    let mut value: serde_json::Value =
        serde_json::from_str(&source("tests/fixtures/m5_install_plan_positive.json")).unwrap();
    value["install_contract_hash"] = json!(EMBEDDED_TARGET_CONTRACT_SHA256);
    let mut plan: InstallPlan = serde_json::from_value(value).unwrap();
    plan.mode = mode.to_string();
    plan.targets = vec!["codex".to_string()];
    plan.components = vec!["harnesskit.skill.optimal-response".to_string()];
    plan.artifacts
        .retain(|artifact| artifact.destination == ".agents/skills/optimal-response/SKILL.md");
    plan.runtime_surfaces
        .retain(|surface| surface.target == "codex" && surface.path == ".agents/skills");
    plan
}

#[derive(Default)]
struct FakePlanGenerator {
    plans: Mutex<VecDeque<InstallPlan>>,
    calls: Mutex<Vec<PlanRunRequest>>,
    apply_body: Vec<u8>,
}

impl FakePlanGenerator {
    fn with_plans(plans: impl IntoIterator<Item = InstallPlan>) -> Self {
        Self {
            plans: Mutex::new(plans.into_iter().collect()),
            calls: Mutex::new(Vec::new()),
            apply_body: b"# canonical skill\n".to_vec(),
        }
    }

    fn with_plans_and_body(
        plans: impl IntoIterator<Item = InstallPlan>,
        apply_body: impl Into<Vec<u8>>,
    ) -> Self {
        Self {
            plans: Mutex::new(plans.into_iter().collect()),
            calls: Mutex::new(Vec::new()),
            apply_body: apply_body.into(),
        }
    }
}

impl InstallPlanGenerator for FakePlanGenerator {
    fn runtime_manifest_sha256(&self) -> &str {
        "runtime-manifest-fixture"
    }

    fn generate(
        &self,
        workspace: &InstallWorkspace,
        request: &PlanRunRequest,
    ) -> Result<InstallPlan, InstallCoordinatorError> {
        self.calls.lock().unwrap().push(request.clone());
        let mut plan = self
            .plans
            .lock()
            .unwrap()
            .pop_front()
            .ok_or_else(|| InstallCoordinatorError::new("fixture_plan_exhausted"))?;
        if request.mode == PlanMode::Apply {
            for artifact in &mut plan.artifacts {
                let body = &self.apply_body;
                write_mode(&workspace.root().join(&artifact.source), body, 0o644);
                artifact.mode = Some(0o644);
                artifact.source_sha256 = Some(format!("{:x}", Sha256::digest(body)));
            }
        }
        Ok(plan)
    }
}

struct FakeArtifactGenerationPort {
    target: String,
    adapter_id: String,
    exact_bytes: Vec<u8>,
}

impl Default for FakeArtifactGenerationPort {
    fn default() -> Self {
        Self {
            target: "codex".to_string(),
            adapter_id: "harnesskit.adapter.codex".to_string(),
            exact_bytes: b"# canonical skill\n".to_vec(),
        }
    }
}

impl ArtifactGenerationPort for FakeArtifactGenerationPort {
    fn generate_artifacts(
        &self,
        request: &CanonicalArtifactRequest,
    ) -> Result<GeneratedArtifactSet, InstallCoordinatorError> {
        let exact_bytes = self.exact_bytes.clone();
        Ok(GeneratedArtifactSet {
            generator_version: "canonical-artifact-generator-v1".to_string(),
            source_revision: request.source_revision.clone(),
            adapter_set_revision: "a".repeat(64),
            target_contract_revision: request.target_contract_revision.clone(),
            records: vec![GeneratedArtifact {
                component_id: "harnesskit.skill.optimal-response".to_string(),
                adapter_id: self.adapter_id.clone(),
                adapter_version: "1".to_string(),
                target: self.target.clone(),
                scope: "project".to_string(),
                destination: ".agents/skills/optimal-response/SKILL.md".to_string(),
                source: "dist/codex/.agents/skills/optimal-response/SKILL.md".to_string(),
                merge_strategy: None,
                config_entry_locator: None,
                content_sha256: format!("{:x}", Sha256::digest(&exact_bytes)),
                exact_bytes,
            }],
            issues: Vec::new(),
        })
    }
}

struct Fixture {
    _root: TempDir,
    checkout: PathBuf,
    app_temp: PathBuf,
    target: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        let canonical = fs::canonicalize(root.path()).unwrap();
        let checkout = canonical.join("checkout");
        let app_temp = canonical.join("app-temp");
        let target = canonical.join("workspace");
        write_mode(
            &checkout.join("components/registry.yml"),
            b"components: []\n",
            0o644,
        );
        fs::create_dir_all(&app_temp).unwrap();
        fs::set_permissions(&app_temp, fs::Permissions::from_mode(0o700)).unwrap();
        fs::create_dir_all(&target).unwrap();
        Self {
            _root: root,
            checkout,
            app_temp,
            target,
        }
    }

    fn input(&self) -> InstallPreviewInput {
        let source_revision = InstallWorkspace::create(&self.checkout, &self.app_temp)
            .unwrap()
            .source_manifest()
            .sha256();
        InstallPreviewInput {
            checkout_id: "checkout-1".to_string(),
            checkout_root: self.checkout.clone(),
            sot_snapshot_id: "sot-1".to_string(),
            source_revision,
            profile_id: "harnesskit.profile.engineering".to_string(),
            scope: "project".to_string(),
            target_root: self.target.clone(),
            target_ids: BTreeSet::from(["codex".to_string()]),
            selected_component_ids: BTreeSet::from([
                "harnesskit.skill.optimal-response".to_string()
            ]),
        }
    }
}

fn coordinator(fixture: &Fixture, generator: Arc<FakePlanGenerator>) -> InstallCoordinator {
    InstallCoordinator::new_with_artifact_generation_port(
        fixture.app_temp.clone(),
        generator,
        Arc::new(FakeArtifactGenerationPort::default()),
    )
    .unwrap()
}

fn coordinator_with_artifact_port(
    fixture: &Fixture,
    generator: Arc<FakePlanGenerator>,
    artifact_port: FakeArtifactGenerationPort,
) -> InstallCoordinator {
    InstallCoordinator::new_with_artifact_generation_port(
        fixture.app_temp.clone(),
        generator,
        Arc::new(artifact_port),
    )
    .unwrap()
}

fn sot_snapshot(snapshot_id: &str) -> SotSnapshot {
    serde_json::from_value(serde_json::json!({
        "snapshot_id": snapshot_id,
        "checkout_summary": {
            "source_revision": "source-revision",
            "canonical_path": "/backend-only",
            "branch": "main",
            "detached": false,
            "dirty": false,
            "recent_commits": []
        },
        "components": [{
            "component_id": "harnesskit.skill.optimal-response",
            "kind": "skill",
            "status": "stable",
            "title": "Optimal response",
            "summary": "Response mode",
            "domain": null,
            "targets": [],
            "provenance": { "mode": null, "source_classification": null },
            "owned_files": ["components/skills/optimal-response/SKILL.md"],
            "profile_ids": []
        }],
        "profiles": [],
        "unprofiled_component_ids": [],
        "relations": [],
        "graph_projection": {
            "logical_width": 1200,
            "logical_height": 460,
            "view_box": [0, 0, 1200, 460],
            "content_extent": { "min_x": 0, "min_y": 0, "max_x": 1200, "max_y": 460 },
            "nodes": [],
            "edges": []
        },
        "navigation_projection": { "all_component_ids": [], "groups": [] },
        "issues": []
    }))
    .unwrap()
}

#[test]
fn preview_is_dry_run_only_and_persists_a_revision_bound_single_use_fingerprint() {
    let fixture = Fixture::new();
    let generator = Arc::new(FakePlanGenerator::with_plans([exact_plan("dry-run")]));
    let coordinator = coordinator(&fixture, generator.clone());
    let before_checkout = fs::read(fixture.checkout.join("components/registry.yml")).unwrap();

    let preview = coordinator.preview(fixture.input()).unwrap();

    assert_eq!(preview.sot_snapshot_id, "sot-1");
    assert_eq!(preview.profile_id, "harnesskit.profile.engineering");
    assert_eq!(preview.fingerprint.len(), 64);
    assert_eq!(preview.artifacts.len(), 1);
    assert_eq!(
        preview.artifacts[0].destination,
        ".agents/skills/optimal-response/SKILL.md"
    );
    assert!(preview.non_atomic_boundary);
    assert_eq!(generator.calls.lock().unwrap()[0].mode, PlanMode::DryRun);
    assert_eq!(
        fs::read(fixture.checkout.join("components/registry.yml")).unwrap(),
        before_checkout
    );
    assert!(fs::read_dir(&fixture.target).unwrap().next().is_none());
}

#[test]
fn confirmation_is_required_without_consuming_the_preview() {
    let fixture = Fixture::new();
    let generator = Arc::new(FakePlanGenerator::with_plans([
        exact_plan("dry-run"),
        exact_plan("apply"),
    ]));
    let coordinator = coordinator(&fixture, generator);
    let preview = coordinator.preview(fixture.input()).unwrap();

    let error = coordinator
        .apply(
            &preview.preview_id,
            InstallApprovals {
                confirmed: false,
                semantic_fingerprint: preview.fingerprint.clone(),
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap_err();
    assert_eq!(error.code(), "install_confirmation_required");

    let applied = coordinator
        .apply(
            &preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: preview.fingerprint.clone(),
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap();
    assert_eq!(applied.status, InstallOperationStatus::Complete);
}

#[test]
fn confirmed_apply_replans_writes_verifies_emits_evidence_and_is_single_use() {
    let fixture = Fixture::new();
    let generator = Arc::new(FakePlanGenerator::with_plans([
        exact_plan("dry-run"),
        exact_plan("apply"),
    ]));
    let coordinator = coordinator(&fixture, generator.clone());
    let preview = coordinator.preview(fixture.input()).unwrap();

    let applied = coordinator
        .apply(
            &preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: preview.fingerprint.clone(),
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap();

    assert_eq!(applied.status, InstallOperationStatus::Complete);
    assert_eq!(applied.destinations.len(), 1);
    assert_eq!(
        applied.destinations[0].apply_state,
        DestinationApplyState::Applied
    );
    assert_eq!(
        applied.destinations[0].verify_state,
        DestinationVerifyState::Verified
    );
    assert!(applied.install_evidence_id.is_some());
    let evidence = coordinator.latest_evidence().unwrap();
    assert_eq!(
        applied.install_evidence_id.as_deref(),
        Some(evidence.evidence_id.as_str())
    );
    assert_eq!(evidence.sot_snapshot_id, "sot-1");
    assert_eq!(evidence.verified_records.len(), 1);
    assert_eq!(
        evidence.verified_records[0].component_id,
        "harnesskit.skill.optimal-response"
    );
    assert_eq!(evidence.verified_records[0].target_id, "codex");
    assert_eq!(
        evidence.verified_records[0].destination_key.tool_id,
        "codex"
    );
    assert_eq!(
        evidence.verified_records[0].destination_key.surface_id,
        "codex_project"
    );
    assert_eq!(
        evidence.verified_records[0].destination_key.scope,
        DestinationScope::Project
    );
    assert_eq!(
        evidence.verified_records[0]
            .destination_key
            .normalized_relative_locator,
        normalized_relative_locator(Path::new(".agents/skills/optimal-response/SKILL.md")).unwrap()
    );
    assert_eq!(
        evidence.verified_records[0].content_sha256,
        format!("{:x}", Sha256::digest(b"# canonical skill\n"))
    );
    assert_eq!(
        fs::read(
            fixture
                .target
                .join(".agents/skills/optimal-response/SKILL.md")
        )
        .unwrap(),
        b"# canonical skill\n"
    );
    assert_eq!(generator.calls.lock().unwrap().len(), 2);
    assert_eq!(generator.calls.lock().unwrap()[1].mode, PlanMode::Apply);
    assert_eq!(
        coordinator
            .apply(
                &preview.preview_id,
                InstallApprovals {
                    confirmed: true,
                    semantic_fingerprint: preview.fingerprint.clone(),
                    overwrite: true,
                    allow_runtime_hooks: true,
                    replace_managed: false,
                    adopt_management: false,
                },
            )
            .unwrap_err()
            .code(),
        "install_preview_unavailable"
    );
}

#[test]
fn stable_generated_artifact_byte_mismatch_fails_before_target_mutation() {
    let fixture = Fixture::new();
    let generator = Arc::new(FakePlanGenerator::with_plans([
        exact_plan("dry-run"),
        exact_plan("apply"),
    ]));
    let coordinator = coordinator_with_artifact_port(
        &fixture,
        generator,
        FakeArtifactGenerationPort {
            exact_bytes: b"# generated authority differs\n".to_vec(),
            ..FakeArtifactGenerationPort::default()
        },
    );
    let preview = coordinator.preview(fixture.input()).unwrap();

    let error = coordinator
        .apply(
            &preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: preview.fingerprint,
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap_err();

    assert_eq!(error.code(), "artifact_authority_mismatch");
    assert!(fs::read_dir(&fixture.target).unwrap().next().is_none());
    assert!(coordinator.latest_evidence().is_none());
}

#[test]
fn installed_exact_bytes_are_authorized_by_generated_artifact_set() {
    let fixture = Fixture::new();
    let exact_bytes = b"# generated artifact authority\n".to_vec();
    let generator = Arc::new(FakePlanGenerator::with_plans_and_body(
        [exact_plan("dry-run"), exact_plan("apply")],
        exact_bytes.clone(),
    ));
    let coordinator = coordinator_with_artifact_port(
        &fixture,
        generator,
        FakeArtifactGenerationPort {
            exact_bytes: exact_bytes.clone(),
            ..FakeArtifactGenerationPort::default()
        },
    );
    let preview = coordinator.preview(fixture.input()).unwrap();

    let applied = coordinator
        .apply(
            &preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: preview.fingerprint,
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap();

    assert_eq!(applied.status, InstallOperationStatus::Complete);
    assert_eq!(
        fs::read(
            fixture
                .target
                .join(".agents/skills/optimal-response/SKILL.md")
        )
        .unwrap(),
        exact_bytes
    );
}

#[test]
fn generated_artifact_target_or_adapter_outside_request_is_rejected() {
    for artifact_port in [
        FakeArtifactGenerationPort {
            target: "unsupported-target".to_string(),
            ..FakeArtifactGenerationPort::default()
        },
        FakeArtifactGenerationPort {
            adapter_id: "harnesskit.adapter.claude".to_string(),
            ..FakeArtifactGenerationPort::default()
        },
    ] {
        let fixture = Fixture::new();
        let generator = Arc::new(FakePlanGenerator::with_plans([exact_plan("dry-run")]));
        let coordinator = coordinator_with_artifact_port(&fixture, generator, artifact_port);

        let error = coordinator.preview(fixture.input()).unwrap_err();

        assert_eq!(error.code(), "artifact_projection_invalid");
        assert!(fs::read_dir(&fixture.target).unwrap().next().is_none());
    }
}

#[test]
fn real_writer_evidence_and_local_scanner_join_to_verified_without_path_inference() {
    let fixture = Fixture::new();
    let generator = Arc::new(FakePlanGenerator::with_plans([
        exact_plan("dry-run"),
        exact_plan("apply"),
    ]));
    let coordinator = coordinator(&fixture, generator);
    let preview = coordinator.preview(fixture.input()).unwrap();
    coordinator
        .apply(
            &preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: preview.fingerprint,
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap();
    let evidence = coordinator.latest_evidence().unwrap();
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let result = LocalScanner::scan(
        fixture.target.parent().unwrap(),
        &[catalog.for_tool(ToolId::Codex).unwrap().clone()],
    )
    .unwrap();
    let local = LocalSnapshot {
        snapshot_id: "local-after-install".to_string(),
        attempt_id: "attempt-after-install".to_string(),
        status: LocalSnapshotStatus::Complete,
        scan_timestamp: "2026-07-12T00:00:00Z".to_string(),
        app_version: "0.1.0-test".to_string(),
        adapter_set_fingerprint: "adapter-fingerprint".to_string(),
        content_fingerprint: "content-fingerprint".to_string(),
        qualified_tools: result.qualified_tools,
        instances: result.instances,
        coverage: result.coverage,
        skipped_paths: result.skipped_paths,
        issues: result.issues,
        project_ignore_summaries: result.project_ignore_summaries,
    };
    let artifact_records = evidence
        .verified_records
        .iter()
        .map(|record| QualifiedArtifactRecord {
            artifact_record_ref: format!("artifact-{}", record.component_id),
            component_id: record.component_id.clone(),
            adapter_id: "codex".to_string(),
            adapter_version: "test".to_string(),
            tool_id: record.destination_key.tool_id.clone(),
            surface_id: record.destination_key.surface_id.clone(),
            scope: record.destination_key.scope,
            normalized_target_locator: record.destination_key.normalized_relative_locator.clone(),
            config_entry_locator: record.destination_key.config_entry_locator.clone(),
            content_sha256: record.content_sha256.clone(),
        })
        .collect();
    let artifacts = QualifiedArtifactProjection {
        artifact_projection_id: "artifact-projection-install-fixture".to_string(),
        projector_version: "fixture".to_string(),
        sot_snapshot_id: "sot-1".to_string(),
        source_revision: "source-revision".to_string(),
        adapter_set_revision: "adapter-set".to_string(),
        availability: CorrelationAvailability::Available,
        records: artifact_records,
        issues: Vec::new(),
    };

    let projection = correlation::project(&local, &artifacts, Some(&evidence)).unwrap();
    assert_eq!(projection.correlations.len(), 1);
    assert_eq!(
        projection.correlations[0].correlation,
        CorrelationState::Verified {
            component_id: "harnesskit.skill.optimal-response".to_string(),
            method: "exact_artifact".to_string(),
            artifact_record_ref: "artifact-harnesskit.skill.optimal-response".to_string(),
            install_evidence_ref: Some(evidence.evidence_id),
        }
    );
}

#[test]
fn source_or_apply_semantic_drift_fails_before_target_write_and_never_emits_evidence() {
    let fixture = Fixture::new();
    let mut changed_apply = exact_plan("apply");
    changed_apply.artifacts[0].destination = ".agents/skills/changed/SKILL.md".to_string();
    let generator = Arc::new(FakePlanGenerator::with_plans([
        exact_plan("dry-run"),
        changed_apply,
    ]));
    let coordinator = coordinator(&fixture, generator);
    let preview = coordinator.preview(fixture.input()).unwrap();

    let error = coordinator
        .apply(
            &preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: preview.fingerprint.clone(),
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap_err();

    assert_eq!(error.code(), "preview_stale");
    assert!(fs::read_dir(&fixture.target).unwrap().next().is_none());
    assert!(coordinator.latest_evidence().is_none());
}

#[test]
fn a_new_apply_attempt_clears_prior_verified_evidence_before_any_failure() {
    let fixture = Fixture::new();
    let mut changed_apply = exact_plan("apply");
    changed_apply.artifacts[0].destination = ".agents/skills/changed/SKILL.md".to_string();
    let generator = Arc::new(FakePlanGenerator::with_plans([
        exact_plan("dry-run"),
        exact_plan("apply"),
        exact_plan("dry-run"),
        changed_apply,
    ]));
    let coordinator = coordinator(&fixture, generator);
    let first = coordinator.preview(fixture.input()).unwrap();
    coordinator
        .apply(
            &first.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: first.fingerprint,
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap();
    assert!(coordinator.latest_evidence().is_some());

    let second = coordinator.preview(fixture.input()).unwrap();
    assert_eq!(
        coordinator
            .apply(
                &second.preview_id,
                InstallApprovals {
                    confirmed: true,
                    semantic_fingerprint: second.fingerprint,
                    overwrite: true,
                    allow_runtime_hooks: true,
                    replace_managed: false,
                    adopt_management: false,
                },
            )
            .unwrap_err()
            .code(),
        "preview_stale"
    );
    assert!(coordinator.latest_evidence().is_none());
}

#[test]
fn checkout_manifest_drift_invalidates_preview_before_apply_process_or_target_write() {
    let fixture = Fixture::new();
    let generator = Arc::new(FakePlanGenerator::with_plans([
        exact_plan("dry-run"),
        exact_plan("apply"),
    ]));
    let coordinator = coordinator(&fixture, generator.clone());
    let preview = coordinator.preview(fixture.input()).unwrap();
    write_mode(
        &fixture.checkout.join("components/registry.yml"),
        b"components: [changed]\n",
        0o644,
    );

    let error = coordinator
        .apply(
            &preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: preview.fingerprint.clone(),
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap_err();

    assert_eq!(error.code(), "preview_stale");
    assert_eq!(generator.calls.lock().unwrap().len(), 1);
    assert!(fs::read_dir(&fixture.target).unwrap().next().is_none());
}

#[test]
fn fingerprint_mismatch_rejects_without_consuming_the_preview() {
    let fixture = Fixture::new();
    let generator = Arc::new(FakePlanGenerator::with_plans([
        exact_plan("dry-run"),
        exact_plan("apply"),
    ]));
    let coordinator = coordinator(&fixture, generator);
    let preview = coordinator.preview(fixture.input()).unwrap();

    let error = coordinator
        .apply(
            &preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: "0".repeat(64),
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap_err();
    assert_eq!(error.code(), "preview_stale");

    let outcome = coordinator
        .apply(
            &preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: preview.fingerprint,
                overwrite: true,
                allow_runtime_hooks: true,
                replace_managed: false,
                adopt_management: false,
            },
        )
        .unwrap();
    assert_eq!(outcome.status, InstallOperationStatus::Complete);
}
