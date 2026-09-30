use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use crate::checkout::RegisteredCheckout;
use crate::contexts::correlation::{QualifiedArtifactProjection, QualifiedArtifactProjector};
use crate::contexts::install::{
    ArtifactGenerationPort, CanonicalArtifactRequest, GeneratedArtifactIssue, GeneratedArtifactSet,
    InstallApplyOutcome, InstallApprovals, InstallCoordinator, InstallEvidenceSnapshot,
    InstallPreview, InstallPreviewInput, EMBEDDED_TARGET_CONTRACT_SHA256,
};
use crate::controller::{AppController as CheckoutController, RegisterCheckoutResponse};
use crate::repo_status::RepoInspector;

use super::domain::SotSnapshot;
use super::snapshot::{finalize_snapshot_id, SotSnapshotService};

#[derive(Default)]
struct SotSession {
    active_checkout_id: Option<String>,
    current_snapshot: Option<SotSnapshot>,
    load_generation: u64,
    latest_install_evidence: Option<InstallEvidenceSnapshot>,
}

pub struct SotContext {
    session: Mutex<SotSession>,
    revision_gate: Mutex<()>,
    checkout: Option<CheckoutController>,
    install: Option<InstallCoordinator>,
    artifact_generator: Option<Arc<dyn ArtifactGenerationPort>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SotSessionHeaders {
    pub checkout_id: Option<String>,
    pub snapshot_id: Option<String>,
    pub install_evidence_id: Option<String>,
}

impl Default for SotContext {
    fn default() -> Self {
        Self {
            session: Mutex::new(SotSession::default()),
            revision_gate: Mutex::new(()),
            checkout: None,
            install: None,
            artifact_generator: None,
        }
    }
}

impl SotContext {
    pub fn with_active_checkout(active_checkout_id: Option<String>) -> Self {
        Self {
            session: Mutex::new(SotSession {
                active_checkout_id,
                current_snapshot: None,
                load_generation: 0,
                latest_install_evidence: None,
            }),
            revision_gate: Mutex::new(()),
            checkout: None,
            install: None,
            artifact_generator: None,
        }
    }

    pub fn with_services(
        checkout: CheckoutController,
        install: Option<InstallCoordinator>,
    ) -> Result<Self, String> {
        let active_checkout_id = checkout.saved_checkout_id()?;
        let artifact_generator = install
            .as_ref()
            .map(InstallCoordinator::artifact_generation_port);
        Ok(Self {
            session: Mutex::new(SotSession {
                active_checkout_id,
                current_snapshot: None,
                load_generation: 0,
                latest_install_evidence: None,
            }),
            revision_gate: Mutex::new(()),
            checkout: Some(checkout),
            install,
            artifact_generator,
        })
    }

    pub fn activate_checkout(&self, checkout_id: String) -> Result<(), String> {
        let _revision = self.lock_revision()?;
        self.activate_checkout_inner(checkout_id)
    }

    fn activate_checkout_inner(&self, checkout_id: String) -> Result<(), String> {
        let mut session = self.lock_session()?;
        session.load_generation = next_generation(session.load_generation)?;
        session.active_checkout_id = Some(checkout_id);
        session.current_snapshot = None;
        session.latest_install_evidence = None;
        drop(session);
        if let Some(install) = &self.install {
            install.reset_revision();
        }
        Ok(())
    }

    pub fn load(
        &self,
        checkout_id: &str,
        checkout: &RegisteredCheckout,
    ) -> Result<SotSnapshot, String> {
        let _revision = self.lock_revision()?;
        self.load_registered(checkout_id, checkout)
    }

    pub fn load_active(&self, checkout_id: &str) -> Result<SotSnapshot, String> {
        let _revision = self.lock_revision()?;
        let checkout = self.checkout_controller()?.resolve_checkout(checkout_id)?;
        self.load_registered(checkout_id, &checkout)
    }

    fn load_registered(
        &self,
        checkout_id: &str,
        checkout: &RegisteredCheckout,
    ) -> Result<SotSnapshot, String> {
        let generation = self.begin_load(checkout_id)?;

        let mut snapshot = SotSnapshotService::load(checkout.root())?;
        let status = RepoInspector::status(checkout)?;
        snapshot.checkout_summary.canonical_path =
            status.canonical_path.to_string_lossy().to_string();
        snapshot.checkout_summary.branch = status.current_branch;
        snapshot.checkout_summary.detached = Some(status.detached);
        snapshot.checkout_summary.dirty = Some(status.dirty);
        snapshot.checkout_summary.recent_commits = status.recent_commit_summaries;
        finalize_snapshot_id(&mut snapshot)?;

        if let Some(install) = &self.install {
            install
                .register_artifact_source(
                    &snapshot.checkout_summary.source_revision,
                    checkout.root(),
                )
                .map_err(|error| error.code().to_string())?;
        }

        self.publish_snapshot(checkout_id, generation, snapshot.clone())?;
        Ok(snapshot)
    }

    pub fn clone_default_checkout(
        &self,
        app_data_dir: &Path,
    ) -> Result<RegisterCheckoutResponse, String> {
        let _revision = self.lock_revision()?;
        let registration = self
            .checkout_controller()?
            .clone_default_checkout(app_data_dir)?;
        self.activate_checkout_inner(registration.checkout_id.clone())?;
        Ok(registration)
    }

    pub fn register_checkout(
        &self,
        path: impl AsRef<Path>,
    ) -> Result<RegisterCheckoutResponse, String> {
        let _revision = self.lock_revision()?;
        let registration = self.checkout_controller()?.register_checkout(path)?;
        self.activate_checkout_inner(registration.checkout_id.clone())?;
        Ok(registration)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn preview_install(
        &self,
        checkout_id: &str,
        sot_snapshot_id: &str,
        profile_id: &str,
        scope: &str,
        target_root: PathBuf,
        target_ids: BTreeSet<String>,
    ) -> Result<InstallPreview, String> {
        let _revision = self.lock_revision()?;
        let (active_checkout_id, snapshot) = {
            let session = self.lock_session()?;
            (
                session.active_checkout_id.clone(),
                session.current_snapshot.clone(),
            )
        };
        if active_checkout_id.as_deref() != Some(checkout_id) {
            return Err("sot_checkout_not_active".to_string());
        }
        let snapshot = snapshot
            .filter(|snapshot| snapshot.snapshot_id == sot_snapshot_id)
            .ok_or_else(|| "snapshot_expired".to_string())?;
        let selected_component_ids =
            selected_profile_component_closure(&snapshot, profile_id, scope)?;
        let checkout = self.checkout_controller()?.resolve_checkout(checkout_id)?;
        let install = self
            .install
            .as_ref()
            .ok_or_else(|| "install_runtime_unavailable".to_string())?;
        install
            .preview(InstallPreviewInput {
                checkout_id: checkout_id.to_string(),
                checkout_root: checkout.root().to_path_buf(),
                sot_snapshot_id: sot_snapshot_id.to_string(),
                source_revision: snapshot.checkout_summary.source_revision.clone(),
                profile_id: profile_id.to_string(),
                scope: scope.to_string(),
                target_root,
                target_ids,
                selected_component_ids,
            })
            .map_err(|error| error.code().to_string())
    }

    pub fn apply_install(
        &self,
        preview_id: &str,
        approvals: InstallApprovals,
    ) -> Result<InstallApplyOutcome, String> {
        let _revision = self.lock_revision()?;
        let install = self
            .install
            .as_ref()
            .ok_or_else(|| "install_runtime_unavailable".to_string())?;
        self.lock_session()?.latest_install_evidence = None;
        let outcome = install
            .apply(preview_id, approvals)
            .map_err(|error| error.code().to_string())?;
        if let Some(evidence) = install.latest_evidence() {
            let mut session = self.lock_session()?;
            if session
                .current_snapshot
                .as_ref()
                .is_some_and(|snapshot| snapshot.snapshot_id == evidence.sot_snapshot_id)
            {
                session.latest_install_evidence = Some(evidence);
            }
        }
        Ok(outcome)
    }

    pub fn session_state(&self) -> Result<(Option<String>, Option<String>), String> {
        let session = self.lock_session()?;
        Ok((
            session.active_checkout_id.clone(),
            session
                .current_snapshot
                .as_ref()
                .map(|snapshot| snapshot.snapshot_id.clone()),
        ))
    }

    pub fn session_headers(&self) -> Result<SotSessionHeaders, String> {
        let session = self.lock_session()?;
        Ok(SotSessionHeaders {
            checkout_id: session.active_checkout_id.clone(),
            snapshot_id: session
                .current_snapshot
                .as_ref()
                .map(|snapshot| snapshot.snapshot_id.clone()),
            install_evidence_id: session
                .latest_install_evidence
                .as_ref()
                .map(|evidence| evidence.evidence_id.clone()),
        })
    }

    pub fn require_snapshot(&self, snapshot_id: &str) -> Result<SotSnapshot, String> {
        let session = self.lock_session()?;
        match session.current_snapshot.as_ref() {
            Some(snapshot) if snapshot.snapshot_id == snapshot_id => Ok(snapshot.clone()),
            _ => Err("snapshot_expired".to_string()),
        }
    }

    pub fn require_install_evidence(
        &self,
        snapshot_id: &str,
        evidence_id: &str,
    ) -> Result<InstallEvidenceSnapshot, String> {
        let session = self.lock_session()?;
        if session
            .current_snapshot
            .as_ref()
            .is_none_or(|snapshot| snapshot.snapshot_id != snapshot_id)
        {
            return Err("snapshot_expired".to_string());
        }
        let evidence = session
            .latest_install_evidence
            .as_ref()
            .filter(|evidence| evidence.evidence_id == evidence_id)
            .ok_or_else(|| "evidence_expired".to_string())?;
        if evidence.sot_snapshot_id != snapshot_id {
            return Err("projection_stale".to_string());
        }
        Ok(evidence.clone())
    }

    pub fn qualified_artifact_projection(
        &self,
        snapshot_id: &str,
    ) -> Result<QualifiedArtifactProjection, String> {
        let _revision = self.lock_revision()?;
        let (active_checkout_id, snapshot) = {
            let session = self.lock_session()?;
            (
                session.active_checkout_id.clone(),
                session.current_snapshot.clone(),
            )
        };
        active_checkout_id.ok_or_else(|| "snapshot_expired".to_string())?;
        let snapshot = snapshot
            .filter(|snapshot| snapshot.snapshot_id == snapshot_id)
            .ok_or_else(|| "snapshot_expired".to_string())?;
        let component_ids = snapshot
            .components
            .iter()
            .map(|component| component.component_id.clone())
            .collect::<BTreeSet<_>>();
        let request = CanonicalArtifactRequest::for_snapshot(
            snapshot_id,
            &snapshot.checkout_summary.source_revision,
            component_ids,
        )
        .map_err(|error| error.code().to_string())?;
        let generated = match self.artifact_generator.as_ref() {
            Some(artifact_generator) => artifact_generator
                .generate_artifacts(&request)
                .map(|generated| {
                    bind_artifacts_to_snapshot_revision(
                        &snapshot.checkout_summary.source_revision,
                        generated,
                    )
                })
                .unwrap_or_else(|error| GeneratedArtifactSet {
                    generator_version: "canonical-artifact-generator-v1".to_string(),
                    source_revision: snapshot.checkout_summary.source_revision.clone(),
                    adapter_set_revision: "0".repeat(64),
                    target_contract_revision: "0".repeat(64),
                    records: Vec::new(),
                    issues: vec![GeneratedArtifactIssue {
                        code: error.code().to_string(),
                        safe_reason: "Qualified artifact projection was unavailable".to_string(),
                    }],
                }),
            None => GeneratedArtifactSet {
                generator_version: "canonical-artifact-generator-v1".to_string(),
                source_revision: snapshot.checkout_summary.source_revision.clone(),
                adapter_set_revision: "0".repeat(64),
                target_contract_revision: "0".repeat(64),
                records: Vec::new(),
                issues: vec![GeneratedArtifactIssue {
                    code: "install_runtime_unavailable".to_string(),
                    safe_reason: "Qualified artifact projection was unavailable".to_string(),
                }],
            },
        };
        Ok(QualifiedArtifactProjector.project(snapshot_id, generated))
    }

    pub fn current_snapshot_id(&self) -> Result<Option<String>, String> {
        self.session_state().map(|(_, snapshot_id)| snapshot_id)
    }

    fn lock_session(&self) -> Result<std::sync::MutexGuard<'_, SotSession>, String> {
        self.session
            .lock()
            .map_err(|_| "SoT snapshot state is unavailable".to_string())
    }

    fn lock_revision(&self) -> Result<std::sync::MutexGuard<'_, ()>, String> {
        self.revision_gate
            .lock()
            .map_err(|_| "SoT revision gate is unavailable".to_string())
    }

    fn checkout_controller(&self) -> Result<&CheckoutController, String> {
        self.checkout
            .as_ref()
            .ok_or_else(|| "Checkout registry is unavailable".to_string())
    }

    fn begin_load(&self, checkout_id: &str) -> Result<u64, String> {
        let mut session = self.lock_session()?;
        if session.active_checkout_id.as_deref() != Some(checkout_id) {
            return Err("sot_checkout_not_active".to_string());
        }
        let generation = next_generation(session.load_generation)?;
        session.load_generation = generation;
        session.current_snapshot = None;
        session.latest_install_evidence = None;
        drop(session);
        if let Some(install) = &self.install {
            install.reset_revision();
        }
        Ok(generation)
    }

    fn publish_snapshot(
        &self,
        checkout_id: &str,
        generation: u64,
        snapshot: SotSnapshot,
    ) -> Result<(), String> {
        let mut session = self.lock_session()?;
        if session.active_checkout_id.as_deref() != Some(checkout_id)
            || session.load_generation != generation
        {
            return Err("sot_snapshot_load_stale".to_string());
        }
        session.current_snapshot = Some(snapshot);
        Ok(())
    }
}

fn bind_artifacts_to_snapshot_revision(
    expected_source_revision: &str,
    generated: GeneratedArtifactSet,
) -> GeneratedArtifactSet {
    if generated.source_revision == expected_source_revision
        && generated.target_contract_revision == EMBEDDED_TARGET_CONTRACT_SHA256
    {
        return generated;
    }
    let code = if generated.source_revision != expected_source_revision {
        "artifact_projection_stale"
    } else {
        "artifact_projection_invalid"
    };
    GeneratedArtifactSet {
        generator_version: generated.generator_version,
        source_revision: generated.source_revision,
        adapter_set_revision: generated.adapter_set_revision,
        target_contract_revision: generated.target_contract_revision,
        records: Vec::new(),
        issues: vec![GeneratedArtifactIssue {
            code: code.to_string(),
            safe_reason: "Qualified artifact projection revision did not match the active snapshot"
                .to_string(),
        }],
    }
}

fn next_generation(generation: u64) -> Result<u64, String> {
    generation
        .checked_add(1)
        .ok_or_else(|| "SoT snapshot generation is exhausted".to_string())
}

pub fn selected_profile_component_closure(
    snapshot: &SotSnapshot,
    profile_id: &str,
    scope: &str,
) -> Result<BTreeSet<String>, String> {
    if !matches!(scope, "user" | "project") {
        return Err("install_request_invalid".to_string());
    }
    let profile = snapshot
        .profiles
        .iter()
        .find(|profile| profile.profile_id == profile_id)
        .ok_or_else(|| "install_profile_unavailable".to_string())?;
    let base = if profile.base_component_ids.is_empty() {
        &profile.component_ids
    } else {
        &profile.base_component_ids
    };
    let mut selected = base
        .iter()
        .filter(|component_id| component_supports_scope(snapshot, component_id, scope))
        .cloned()
        .collect::<BTreeSet<_>>();
    if let Some(scoped) = profile.scope_component_ids.get(scope) {
        for component_id in scoped {
            if !component_supports_scope(snapshot, component_id, scope) {
                return Err("install_component_scope_mismatch".to_string());
            }
            selected.insert(component_id.clone());
        }
    }

    let mut closure = BTreeSet::new();
    for component_id in selected {
        let component = snapshot
            .components
            .iter()
            .find(|component| component.component_id == component_id)
            .ok_or_else(|| "install_component_closure_invalid".to_string())?;
        if component.kind == "composite" {
            let members = snapshot
                .relations
                .iter()
                .filter(|relation| {
                    relation.source == component_id && relation.relation_type == "composite_member"
                })
                .map(|relation| relation.target.clone())
                .collect::<BTreeSet<_>>();
            if members.is_empty()
                || members
                    .iter()
                    .any(|member| !component_supports_scope(snapshot, member, scope))
            {
                return Err("install_component_closure_invalid".to_string());
            }
            closure.extend(members);
        } else {
            closure.insert(component_id);
        }
    }
    if closure.is_empty() {
        return Err("install_component_closure_empty".to_string());
    }
    Ok(closure)
}

fn component_supports_scope(snapshot: &SotSnapshot, component_id: &str, scope: &str) -> bool {
    snapshot
        .components
        .iter()
        .find(|component| component.component_id == component_id)
        .is_some_and(|component| component.install_scopes.iter().any(|value| value == scope))
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    use super::*;
    use crate::contexts::correlation::CorrelationAvailability;
    use crate::contexts::install::{
        GeneratedArtifact, InstallCoordinatorError, InstallPlan, InstallPlanGenerator,
        InstallWorkspace, PlanMode, PlanRunRequest,
    };
    use crate::contexts::sot::domain::{
        SotCheckoutSummary, SotComponent, SotGraphProjection, SotNavigationProjection,
        SotProvenance, SotTarget,
    };
    use sha2::Digest;
    use tempfile::tempdir;

    struct SharedArtifactSpy {
        calls: Mutex<Vec<CanonicalArtifactRequest>>,
        exact_bytes: Vec<u8>,
    }

    impl ArtifactGenerationPort for SharedArtifactSpy {
        fn generate_artifacts(
            &self,
            request: &CanonicalArtifactRequest,
        ) -> Result<GeneratedArtifactSet, InstallCoordinatorError> {
            self.calls.lock().unwrap().push(request.clone());
            Ok(GeneratedArtifactSet {
                generator_version: "canonical-artifact-generator-v1".to_string(),
                source_revision: request.source_revision.clone(),
                adapter_set_revision: "a".repeat(64),
                target_contract_revision: request.target_contract_revision.clone(),
                records: vec![GeneratedArtifact {
                    component_id: "harnesskit.skill.optimal-response".to_string(),
                    adapter_id: "harnesskit.adapter.codex".to_string(),
                    adapter_version: "1".to_string(),
                    target: "codex".to_string(),
                    scope: "project".to_string(),
                    destination: ".agents/skills/optimal-response/SKILL.md".to_string(),
                    source: "dist/codex/.agents/skills/optimal-response/SKILL.md".to_string(),
                    merge_strategy: None,
                    config_entry_locator: None,
                    content_sha256: format!("{:x}", sha2::Sha256::digest(&self.exact_bytes)),
                    exact_bytes: self.exact_bytes.clone(),
                }],
                issues: Vec::new(),
            })
        }
    }

    struct SharedPortPlanGenerator;

    impl InstallPlanGenerator for SharedPortPlanGenerator {
        fn runtime_manifest_sha256(&self) -> &str {
            "runtime-manifest-fixture"
        }

        fn generate(
            &self,
            _workspace: &InstallWorkspace,
            request: &PlanRunRequest,
        ) -> Result<InstallPlan, InstallCoordinatorError> {
            let mut value: serde_json::Value = serde_json::from_str(include_str!(
                "../../../tests/fixtures/m5_install_plan_positive.json"
            ))
            .unwrap();
            value["install_contract_hash"] = serde_json::json!(EMBEDDED_TARGET_CONTRACT_SHA256);
            let mut plan: InstallPlan = serde_json::from_value(value).unwrap();
            plan.mode = match request.mode {
                PlanMode::DryRun => "dry-run",
                PlanMode::Apply => "apply",
            }
            .to_string();
            plan.targets = vec!["codex".to_string()];
            plan.components = vec!["harnesskit.skill.optimal-response".to_string()];
            plan.artifacts.retain(|artifact| {
                artifact.destination == ".agents/skills/optimal-response/SKILL.md"
            });
            plan.runtime_surfaces
                .retain(|surface| surface.target == "codex" && surface.path == ".agents/skills");
            Ok(plan)
        }
    }

    fn write(path: &Path, body: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, body).unwrap();
    }

    fn renderer_fixture() -> tempfile::TempDir {
        let fixture = tempdir().unwrap();
        write(
            &fixture.path().join("components/registry.yml"),
            r#"version: "1"
components:
  harnesskit.skill.fixture:
    kind: skill
    status: draft
    path: components/skills/fixture/component.yml
"#,
        );
        write(
            &fixture
                .path()
                .join("components/skills/fixture/component.yml"),
            r#"component_id: harnesskit.skill.fixture
kind: skill
status: draft
title: Fixture
owned_files:
  - components/skills/fixture/SKILL.md
targets:
  codex: { output_path: skills/fixture }
"#,
        );
        write(
            &fixture.path().join("components/skills/fixture/SKILL.md"),
            "# Fixture\nold body\n",
        );
        for (relative, schema) in [
            (
                "schemas/component.schema.json",
                include_str!("../../../../schemas/component.schema.json"),
            ),
            (
                "schemas/agent.schema.json",
                include_str!("../../../../schemas/agent.schema.json"),
            ),
            (
                "schemas/workflow.schema.json",
                include_str!("../../../../schemas/workflow.schema.json"),
            ),
            (
                "schemas/composite.schema.json",
                include_str!("../../../../schemas/composite.schema.json"),
            ),
            (
                "schemas/profile.schema.json",
                include_str!("../../../../schemas/profile.schema.json"),
            ),
        ] {
            write(&fixture.path().join(relative), schema);
        }
        fixture
    }

    fn snapshot(snapshot_id: &str) -> SotSnapshot {
        SotSnapshot {
            snapshot_id: snapshot_id.to_string(),
            checkout_summary: SotCheckoutSummary {
                source_revision: "source".to_string(),
                canonical_path: "/fixture".to_string(),
                branch: Some("main".to_string()),
                detached: Some(false),
                dirty: Some(false),
                recent_commits: vec!["fixture".to_string()],
            },
            components: Vec::new(),
            profiles: Vec::new(),
            workflows: Vec::new(),
            unprofiled_component_ids: Vec::new(),
            relations: Vec::new(),
            graph_projection: SotGraphProjection {
                schema_version: 2,
                snapshot_id: snapshot_id.to_string(),
                layout_seed: "fixture".to_string(),
                nodes: Vec::new(),
                links: Vec::new(),
            },
            navigation_projection: SotNavigationProjection {
                all_component_ids: Vec::new(),
                groups: Vec::new(),
            },
            issues: Vec::new(),
        }
    }

    fn generated(source_revision: &str, target_contract_revision: &str) -> GeneratedArtifactSet {
        let exact_bytes = b"exact adapter output\n".to_vec();
        GeneratedArtifactSet {
            generator_version: "canonical-artifact-generator-v1".to_string(),
            source_revision: source_revision.to_string(),
            adapter_set_revision: "a".repeat(64),
            target_contract_revision: target_contract_revision.to_string(),
            records: vec![GeneratedArtifact {
                component_id: "harnesskit.skill.fixture".to_string(),
                adapter_id: "harnesskit.adapter.codex".to_string(),
                adapter_version: "1".to_string(),
                target: "codex".to_string(),
                scope: "project".to_string(),
                destination: ".agents/skills/fixture/SKILL.md".to_string(),
                source: "dist/codex/.agents/skills/fixture/SKILL.md".to_string(),
                merge_strategy: None,
                config_entry_locator: None,
                content_sha256: format!("{:x}", sha2::Sha256::digest(&exact_bytes)),
                exact_bytes,
            }],
            issues: Vec::new(),
        }
    }

    #[test]
    fn older_load_completion_cannot_replace_the_latest_generation() {
        let context = SotContext::with_active_checkout(Some("checkout-a".to_string()));
        let older = context.begin_load("checkout-a").unwrap();
        let latest = context.begin_load("checkout-a").unwrap();
        context
            .publish_snapshot("checkout-a", latest, snapshot("latest"))
            .unwrap();

        let error = context
            .publish_snapshot("checkout-a", older, snapshot("older"))
            .unwrap_err();

        assert_eq!(error, "sot_snapshot_load_stale");
        assert_eq!(
            context.current_snapshot_id().unwrap().as_deref(),
            Some("latest")
        );
    }

    #[test]
    fn matching_artifact_observation_preserves_exact_projection_bytes() {
        let generated = generated("source", EMBEDDED_TARGET_CONTRACT_SHA256);

        let bound = bind_artifacts_to_snapshot_revision("source", generated.clone());

        assert_eq!(bound, generated);
        assert_eq!(bound.records[0].exact_bytes, b"exact adapter output\n");
    }

    #[test]
    fn checkout_or_contract_revision_change_makes_projection_unavailable() {
        for generated in [
            generated("changed", EMBEDDED_TARGET_CONTRACT_SHA256),
            generated("source", &"0".repeat(64)),
        ] {
            let bound = bind_artifacts_to_snapshot_revision("source", generated);

            assert!(bound.records.is_empty());
            assert_eq!(bound.issues.len(), 1);
            assert!(matches!(
                bound.issues[0].code.as_str(),
                "artifact_projection_stale" | "artifact_projection_invalid"
            ));
        }
    }

    #[test]
    fn snapshot_preserves_canonical_english_title_and_korean_summary() {
        let fixture = renderer_fixture();
        let manifest_path = fixture
            .path()
            .join("components/skills/fixture/component.yml");
        let manifest = fs::read_to_string(&manifest_path).unwrap();
        write(
            &manifest_path,
            &manifest.replace(
                "title: Fixture\n",
                "title: Adapter Author\nsummary: 한국어 요약\n",
            ),
        );

        let snapshot = SotSnapshotService::load(fixture.path()).unwrap();
        let component = snapshot
            .components
            .iter()
            .find(|component| component.component_id == "harnesskit.skill.fixture")
            .unwrap();
        assert_eq!(component.title, "Adapter Author");
        assert_eq!(component.summary.as_deref(), Some("한국어 요약"));
    }

    #[test]
    fn renderer_body_only_mutation_changes_revision_and_publishes_no_stale_records() {
        let fixture = renderer_fixture();
        let before = SotSnapshotService::load(fixture.path()).unwrap();
        write(
            &fixture.path().join("components/skills/fixture/SKILL.md"),
            "# Fixture\nnew body\n",
        );
        let after = SotSnapshotService::load(fixture.path()).unwrap();

        assert_ne!(
            before.checkout_summary.source_revision, after.checkout_summary.source_revision,
            "renderer-owned body bytes must participate in the snapshot revision"
        );
        let bound = bind_artifacts_to_snapshot_revision(
            &before.checkout_summary.source_revision,
            generated(
                &after.checkout_summary.source_revision,
                EMBEDDED_TARGET_CONTRACT_SHA256,
            ),
        );
        assert!(bound.records.is_empty());
        assert_eq!(bound.issues[0].code, "artifact_projection_stale");
        let projection = QualifiedArtifactProjector.project("snapshot", bound);
        assert!(projection.records.is_empty());
        assert!(matches!(
            projection.availability,
            CorrelationAvailability::Unavailable { .. }
        ));
    }

    #[test]
    fn install_preview_and_projection_share_one_path_free_artifact_port_instance() {
        let fixture = tempdir().unwrap();
        let root = fs::canonicalize(fixture.path()).unwrap();
        let checkout_root = root.join("checkout");
        let app_temp_root = root.join("app-temp");
        let target_root = root.join("target");
        write(
            &checkout_root.join("components/registry.yml"),
            "components: []\n",
        );
        fs::create_dir(&app_temp_root).unwrap();
        fs::set_permissions(&app_temp_root, fs::Permissions::from_mode(0o700)).unwrap();
        fs::create_dir(&target_root).unwrap();
        let source_revision = InstallWorkspace::create(&checkout_root, &app_temp_root)
            .unwrap()
            .source_manifest()
            .sha256();
        let spy = Arc::new(SharedArtifactSpy {
            calls: Mutex::new(Vec::new()),
            exact_bytes: b"shared exact bytes\n".to_vec(),
        });
        let artifact_generator: Arc<dyn ArtifactGenerationPort> = spy.clone();
        let coordinator = InstallCoordinator::new_with_artifact_generation_port(
            app_temp_root,
            Arc::new(SharedPortPlanGenerator),
            Arc::clone(&artifact_generator),
        )
        .unwrap();

        let preview = coordinator
            .preview(InstallPreviewInput {
                checkout_id: "checkout".to_string(),
                checkout_root,
                sot_snapshot_id: "snapshot".to_string(),
                source_revision: source_revision.clone(),
                profile_id: "harnesskit.profile.engineering".to_string(),
                scope: "project".to_string(),
                target_root,
                target_ids: BTreeSet::from(["codex".to_string()]),
                selected_component_ids: BTreeSet::from([
                    "harnesskit.skill.optimal-response".to_string()
                ]),
            })
            .unwrap();
        let mut active_snapshot = snapshot("snapshot");
        active_snapshot.checkout_summary.source_revision = source_revision.clone();
        active_snapshot.components.push(SotComponent {
            component_id: "harnesskit.skill.optimal-response".to_string(),
            kind: "skill".to_string(),
            status: "stable".to_string(),
            title: "Optimal response".to_string(),
            summary: None,
            domain: None,
            targets: vec![SotTarget {
                target_id: "codex".to_string(),
                support_status: None,
            }],
            provenance: SotProvenance::default(),
            owned_files: Vec::new(),
            profile_ids: Vec::new(),
            install_scopes: vec!["project".to_string()],
        });
        let context = SotContext {
            session: Mutex::new(SotSession {
                active_checkout_id: Some("checkout".to_string()),
                current_snapshot: Some(active_snapshot),
                load_generation: 1,
                latest_install_evidence: None,
            }),
            revision_gate: Mutex::new(()),
            checkout: None,
            install: Some(coordinator),
            artifact_generator: Some(artifact_generator),
        };

        let projection = context.qualified_artifact_projection("snapshot").unwrap();
        let calls = spy.calls.lock().unwrap();
        assert_eq!(calls.len(), 2);
        assert_eq!(calls[0], calls[1]);
        assert_eq!(calls[0].source_revision, source_revision);
        assert!(!preview.fingerprint.is_empty());
        assert_eq!(projection.source_revision, calls[0].source_revision);
        assert_eq!(projection.records.len(), 1);
        assert_eq!(
            projection.records[0].content_sha256,
            format!("{:x}", sha2::Sha256::digest(b"shared exact bytes\n"))
        );
    }
}
