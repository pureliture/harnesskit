//! Revision-bound install preview/apply coordination.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::os::unix::fs::MetadataExt;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::projection::DestinationKey;

use super::plan::{InstallPlan, InstallRequest, PlanArtifact, PlanValidator, ValidatedPlan};
use super::runtime::{
    ArtifactRunRequest, FixedInstallPlanRunner, PlanMode, PlanProcessPort, PlanRunRequest,
};
use super::target_contract::{embedded_target_contract, EMBEDDED_TARGET_CONTRACT_SHA256};
use super::workspace::{InstallWorkspace, SourceManifest, SourceRevisionManifest};
use super::writer::{
    DestinationApplyState, DestinationVerifyState, InstallArtifactAuthority,
    InstallArtifactAuthorityRecord, InstallOperationStatus, InstallTargetRoots, InstallVerifier,
    InstallWriter,
};

const INSTALL_COORDINATOR_VERSION: &str = "install-coordinator-v1";
const INSTALL_WRITER_VERSION: &str = "rust-install-writer-v1";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallCoordinatorError {
    code: String,
}

impl InstallCoordinatorError {
    pub fn new(code: impl Into<String>) -> Self {
        Self { code: code.into() }
    }

    pub fn code(&self) -> &str {
        &self.code
    }
}

impl fmt::Display for InstallCoordinatorError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.code)
    }
}

impl std::error::Error for InstallCoordinatorError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CanonicalArtifactRequest {
    pub sot_snapshot_id: String,
    pub source_revision: String,
    pub component_ids: BTreeSet<String>,
    pub target_ids: BTreeSet<String>,
    pub qualified_adapter_ids: BTreeSet<String>,
    pub qualified_adapter_revision: String,
    pub target_contract_revision: String,
}

impl CanonicalArtifactRequest {
    pub fn for_snapshot(
        sot_snapshot_id: impl Into<String>,
        source_revision: impl Into<String>,
        component_ids: BTreeSet<String>,
    ) -> Result<Self, InstallCoordinatorError> {
        let contract = embedded_target_contract()
            .map_err(|_| InstallCoordinatorError::new("artifact_projection_invalid"))?;
        let target_ids = contract
            .targets
            .iter()
            .map(|target| target.id.clone())
            .collect::<BTreeSet<_>>();
        let target_adapter_pairs = target_ids
            .iter()
            .map(|target| (target.clone(), qualified_adapter_id_for_target(target)))
            .collect::<BTreeSet<_>>();
        let qualified_adapter_ids = target_adapter_pairs
            .iter()
            .map(|(_, adapter_id)| adapter_id.clone())
            .collect::<BTreeSet<_>>();
        let canonical =
            serde_json::to_vec(&(&target_adapter_pairs, EMBEDDED_TARGET_CONTRACT_SHA256))
                .map_err(|_| InstallCoordinatorError::new("artifact_projection_invalid"))?;
        let request = Self {
            sot_snapshot_id: sot_snapshot_id.into(),
            source_revision: source_revision.into(),
            component_ids,
            target_ids,
            qualified_adapter_ids,
            qualified_adapter_revision: hex_sha256(&canonical),
            target_contract_revision: EMBEDDED_TARGET_CONTRACT_SHA256.to_string(),
        };
        request.validate()?;
        Ok(request)
    }

    fn validate(&self) -> Result<(), InstallCoordinatorError> {
        let expected_adapter_ids = self
            .target_ids
            .iter()
            .map(|target| qualified_adapter_id_for_target(target))
            .collect::<BTreeSet<_>>();
        if self.sot_snapshot_id.is_empty()
            || !is_sha256(&self.source_revision)
            || self.component_ids.is_empty()
            || self.component_ids.iter().any(|value| !safe_token(value))
            || self.target_ids.is_empty()
            || self.target_ids.iter().any(|value| !safe_token(value))
            || self.qualified_adapter_ids.is_empty()
            || self
                .qualified_adapter_ids
                .iter()
                .any(|value| !safe_token(value))
            || self.qualified_adapter_ids != expected_adapter_ids
            || !is_sha256(&self.qualified_adapter_revision)
            || self.target_contract_revision != EMBEDDED_TARGET_CONTRACT_SHA256
        {
            return Err(InstallCoordinatorError::new("artifact_projection_invalid"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GeneratedArtifact {
    pub component_id: String,
    pub adapter_id: String,
    pub adapter_version: String,
    pub target: String,
    pub scope: String,
    pub destination: String,
    pub source: String,
    pub merge_strategy: Option<String>,
    pub config_entry_locator: Option<String>,
    pub content_sha256: String,
    #[serde(skip)]
    pub exact_bytes: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GeneratedArtifactIssue {
    pub code: String,
    pub safe_reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GeneratedArtifactSet {
    pub generator_version: String,
    pub source_revision: String,
    pub adapter_set_revision: String,
    pub target_contract_revision: String,
    pub records: Vec<GeneratedArtifact>,
    pub issues: Vec<GeneratedArtifactIssue>,
}

pub trait InstallPlanGenerator: Send + Sync + 'static {
    fn runtime_manifest_sha256(&self) -> &str;

    fn generate(
        &self,
        workspace: &InstallWorkspace,
        request: &PlanRunRequest,
    ) -> Result<InstallPlan, InstallCoordinatorError>;

    fn generate_artifacts_in_workspace(
        &self,
        _workspace: &InstallWorkspace,
        _request: &CanonicalArtifactRequest,
    ) -> Result<GeneratedArtifactSet, InstallCoordinatorError> {
        Err(InstallCoordinatorError::new(
            "artifact_projection_unavailable",
        ))
    }
}

pub trait ArtifactGenerationPort: Send + Sync + 'static {
    fn generate_artifacts(
        &self,
        request: &CanonicalArtifactRequest,
    ) -> Result<GeneratedArtifactSet, InstallCoordinatorError>;
}

struct ArtifactWorkspaceLease {
    workspace: InstallWorkspace,
    checkout_root: PathBuf,
}

impl ArtifactWorkspaceLease {
    fn verify_source(&self) -> Result<(), InstallCoordinatorError> {
        self.workspace
            .verify_source(&self.checkout_root)
            .map_err(|_| InstallCoordinatorError::new("artifact_projection_stale"))
    }
}

struct ArtifactWorkspaceAuthority {
    app_temp_root: PathBuf,
    source_roots: Mutex<BTreeMap<String, PathBuf>>,
}

impl ArtifactWorkspaceAuthority {
    fn new(app_temp_root: PathBuf) -> Result<Self, InstallCoordinatorError> {
        validate_app_temp_root(&app_temp_root)?;
        Ok(Self {
            app_temp_root,
            source_roots: Mutex::new(BTreeMap::new()),
        })
    }

    fn register_source(
        &self,
        source_revision: &str,
        checkout_root: &Path,
    ) -> Result<(), InstallCoordinatorError> {
        if !is_sha256(source_revision) {
            return Err(InstallCoordinatorError::new("artifact_projection_invalid"));
        }
        let checkout_root = std::fs::canonicalize(checkout_root)
            .map_err(|_| InstallCoordinatorError::new("artifact_projection_stale"))?;
        let observed = SourceRevisionManifest::observe_root(&checkout_root)
            .map_err(|_| InstallCoordinatorError::new("artifact_projection_stale"))?;
        if observed.sha256() != source_revision {
            return Err(InstallCoordinatorError::new("artifact_projection_stale"));
        }
        self.source_roots
            .lock()
            .map_err(|_| InstallCoordinatorError::new("artifact_projection_unavailable"))?
            .insert(source_revision.to_string(), checkout_root);
        Ok(())
    }

    fn capture(
        &self,
        source_revision: &str,
    ) -> Result<ArtifactWorkspaceLease, InstallCoordinatorError> {
        let checkout_root = self
            .source_roots
            .lock()
            .map_err(|_| InstallCoordinatorError::new("artifact_projection_unavailable"))?
            .get(source_revision)
            .cloned()
            .ok_or_else(|| InstallCoordinatorError::new("artifact_projection_unavailable"))?;
        let workspace = InstallWorkspace::create(&checkout_root, &self.app_temp_root)
            .map_err(|_| InstallCoordinatorError::new("artifact_projection_stale"))?;
        if workspace.source_manifest().sha256() != source_revision {
            return Err(InstallCoordinatorError::new("artifact_projection_stale"));
        }
        let lease = ArtifactWorkspaceLease {
            workspace,
            checkout_root,
        };
        lease.verify_source()?;
        Ok(lease)
    }

    fn clear(&self) {
        self.source_roots
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clear();
    }
}

struct CapturedArtifactGenerationAdapter {
    workspace_authority: Arc<ArtifactWorkspaceAuthority>,
    generator: Arc<dyn InstallPlanGenerator>,
}

impl ArtifactGenerationPort for CapturedArtifactGenerationAdapter {
    fn generate_artifacts(
        &self,
        request: &CanonicalArtifactRequest,
    ) -> Result<GeneratedArtifactSet, InstallCoordinatorError> {
        request.validate()?;
        let lease = self.workspace_authority.capture(&request.source_revision)?;
        lease
            .workspace
            .validate_captured_source()
            .map_err(|_| InstallCoordinatorError::new("artifact_projection_stale"))?;
        let generated = self
            .generator
            .generate_artifacts_in_workspace(&lease.workspace, request)?;
        lease
            .workspace
            .validate_captured_source()
            .map_err(|_| InstallCoordinatorError::new("artifact_projection_stale"))?;
        lease.verify_source()?;
        if generated.source_revision != request.source_revision {
            return Err(InstallCoordinatorError::new("artifact_projection_stale"));
        }
        if generated.target_contract_revision != request.target_contract_revision {
            return Err(InstallCoordinatorError::new("artifact_projection_invalid"));
        }
        Ok(generated)
    }
}

impl<P> InstallPlanGenerator for FixedInstallPlanRunner<P>
where
    P: PlanProcessPort + Send + Sync + 'static,
{
    fn runtime_manifest_sha256(&self) -> &str {
        self.runtime_manifest_sha256()
    }

    fn generate(
        &self,
        workspace: &InstallWorkspace,
        request: &PlanRunRequest,
    ) -> Result<InstallPlan, InstallCoordinatorError> {
        let output = self
            .run(workspace, request)
            .map_err(|error| InstallCoordinatorError::new(error.code()))?;
        serde_json::from_str(&output.json)
            .map_err(|_| InstallCoordinatorError::new("install_plan_invalid"))
    }

    fn generate_artifacts_in_workspace(
        &self,
        workspace: &InstallWorkspace,
        request: &CanonicalArtifactRequest,
    ) -> Result<GeneratedArtifactSet, InstallCoordinatorError> {
        let source_revision = workspace.source_manifest().sha256();
        let output = self
            .run_artifact_projection(
                workspace,
                &ArtifactRunRequest {
                    component_ids: request.component_ids.iter().cloned().collect(),
                },
            )
            .map_err(|error| InstallCoordinatorError::new(error.code()))?;
        let wire: GeneratedArtifactSetWire = serde_json::from_str(&output.json)
            .map_err(|_| InstallCoordinatorError::new("artifact_projection_invalid"))?;
        wire.into_domain(source_revision)
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct GeneratedArtifactSetWire {
    install_contract_id: String,
    install_contract_hash: String,
    generator_version: String,
    adapter_set_revision: String,
    records: Vec<GeneratedArtifactWire>,
    issues: Vec<GeneratedArtifactIssue>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct GeneratedArtifactWire {
    component_id: String,
    adapter_id: String,
    adapter_version: String,
    target: String,
    scope: String,
    destination: String,
    source: String,
    merge_strategy: Option<String>,
    config_entry_locator: Option<String>,
    content_sha256: String,
    exact_text: String,
}

impl GeneratedArtifactSetWire {
    fn into_domain(
        self,
        source_revision: String,
    ) -> Result<GeneratedArtifactSet, InstallCoordinatorError> {
        if self.install_contract_id != super::target_contract::EMBEDDED_TARGET_CONTRACT_ID
            || self.install_contract_hash != EMBEDDED_TARGET_CONTRACT_SHA256
            || !is_sha256(&self.adapter_set_revision)
            || self.generator_version != "canonical-artifact-generator-v1"
        {
            return Err(InstallCoordinatorError::new("artifact_projection_invalid"));
        }
        let mut records = self
            .records
            .into_iter()
            .map(GeneratedArtifactWire::into_domain)
            .collect::<Result<Vec<_>, _>>()?;
        records.sort_by(|left, right| {
            generated_artifact_identity(left).cmp(&generated_artifact_identity(right))
        });
        let mut issues = self.issues;
        issues.sort_by(|left, right| {
            left.code
                .cmp(&right.code)
                .then_with(|| left.safe_reason.cmp(&right.safe_reason))
        });
        Ok(GeneratedArtifactSet {
            generator_version: self.generator_version,
            source_revision,
            adapter_set_revision: self.adapter_set_revision,
            target_contract_revision: self.install_contract_hash,
            records,
            issues,
        })
    }
}

impl GeneratedArtifactWire {
    fn into_domain(self) -> Result<GeneratedArtifact, InstallCoordinatorError> {
        if !matches!(self.scope.as_str(), "user" | "project")
            || !is_sha256(&self.content_sha256)
            || format!("{:x}", Sha256::digest(self.exact_text.as_bytes())) != self.content_sha256
        {
            return Err(InstallCoordinatorError::new("artifact_projection_invalid"));
        }
        Ok(GeneratedArtifact {
            component_id: self.component_id,
            adapter_id: self.adapter_id,
            adapter_version: self.adapter_version,
            target: self.target,
            scope: self.scope,
            destination: self.destination,
            source: self.source,
            merge_strategy: self.merge_strategy,
            config_entry_locator: self.config_entry_locator,
            content_sha256: self.content_sha256,
            exact_bytes: self.exact_text.into_bytes(),
        })
    }
}

fn generated_artifact_identity(
    artifact: &GeneratedArtifact,
) -> (&str, &str, &str, &str, &str, Option<&str>, &str) {
    (
        artifact.component_id.as_str(),
        artifact.adapter_id.as_str(),
        artifact.target.as_str(),
        artifact.scope.as_str(),
        artifact.destination.as_str(),
        artifact.config_entry_locator.as_deref(),
        artifact.content_sha256.as_str(),
    )
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

#[cfg(test)]
mod artifact_projection_tests {
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    use tempfile::tempdir;

    use super::*;

    enum AdversarialGeneration {
        MutateCheckout(PathBuf),
        ReturnRevision(String),
    }

    struct AdversarialArtifactGenerator {
        behavior: AdversarialGeneration,
    }

    impl InstallPlanGenerator for AdversarialArtifactGenerator {
        fn runtime_manifest_sha256(&self) -> &str {
            "unused"
        }

        fn generate(
            &self,
            _workspace: &InstallWorkspace,
            _request: &PlanRunRequest,
        ) -> Result<InstallPlan, InstallCoordinatorError> {
            unreachable!("plan generation is outside this fixture")
        }

        fn generate_artifacts_in_workspace(
            &self,
            workspace: &InstallWorkspace,
            _request: &CanonicalArtifactRequest,
        ) -> Result<GeneratedArtifactSet, InstallCoordinatorError> {
            let source_revision = match &self.behavior {
                AdversarialGeneration::MutateCheckout(path) => {
                    fs::write(path, "changed renderer body\n").unwrap();
                    workspace.source_manifest().sha256()
                }
                AdversarialGeneration::ReturnRevision(revision) => revision.clone(),
            };
            Ok(GeneratedArtifactSet {
                generator_version: "canonical-artifact-generator-v1".to_string(),
                source_revision,
                adapter_set_revision: "a".repeat(64),
                target_contract_revision: EMBEDDED_TARGET_CONTRACT_SHA256.to_string(),
                records: Vec::new(),
                issues: Vec::new(),
            })
        }
    }

    fn artifact_adapter_fixture(
        behavior: AdversarialGeneration,
    ) -> (
        tempfile::TempDir,
        tempfile::TempDir,
        CapturedArtifactGenerationAdapter,
        CanonicalArtifactRequest,
    ) {
        let checkout = tempdir().unwrap();
        let body = checkout.path().join("components/skills/fixture/SKILL.md");
        fs::create_dir_all(body.parent().unwrap()).unwrap();
        fs::write(&body, "original renderer body\n").unwrap();
        let checkout_root = fs::canonicalize(checkout.path()).unwrap();
        let source_revision = SourceRevisionManifest::observe_root(&checkout_root)
            .unwrap()
            .sha256();
        let temporary = tempdir().unwrap();
        let app_temp_root = temporary.path().join("app-temp");
        fs::create_dir(&app_temp_root).unwrap();
        fs::set_permissions(&app_temp_root, fs::Permissions::from_mode(0o700)).unwrap();
        let app_temp_root = fs::canonicalize(app_temp_root).unwrap();
        let workspace_authority = Arc::new(ArtifactWorkspaceAuthority::new(app_temp_root).unwrap());
        workspace_authority
            .register_source(&source_revision, &checkout_root)
            .unwrap();
        let adapter = CapturedArtifactGenerationAdapter {
            workspace_authority,
            generator: Arc::new(AdversarialArtifactGenerator { behavior }),
        };
        let request = CanonicalArtifactRequest::for_snapshot(
            "snapshot",
            source_revision,
            BTreeSet::from(["harnesskit.skill.fixture".to_string()]),
        )
        .unwrap();
        (checkout, temporary, adapter, request)
    }

    #[test]
    fn wire_preserves_observed_revisions_and_exact_adapter_bytes() {
        let exact_text = "exact adapter output\n";
        let adapter_set_revision = "a".repeat(64);
        let wire = GeneratedArtifactSetWire {
            install_contract_id: super::super::target_contract::EMBEDDED_TARGET_CONTRACT_ID
                .to_string(),
            install_contract_hash: EMBEDDED_TARGET_CONTRACT_SHA256.to_string(),
            generator_version: "canonical-artifact-generator-v1".to_string(),
            adapter_set_revision: adapter_set_revision.clone(),
            records: vec![GeneratedArtifactWire {
                component_id: "harnesskit.skill.fixture".to_string(),
                adapter_id: "harnesskit.adapter.codex".to_string(),
                adapter_version: "1".to_string(),
                target: "codex".to_string(),
                scope: "project".to_string(),
                destination: ".agents/skills/fixture/SKILL.md".to_string(),
                source: "dist/codex/.agents/skills/fixture/SKILL.md".to_string(),
                merge_strategy: None,
                config_entry_locator: None,
                content_sha256: format!("{:x}", Sha256::digest(exact_text.as_bytes())),
                exact_text: exact_text.to_string(),
            }],
            issues: Vec::new(),
        };

        let generated = wire
            .into_domain("observed-source-revision".to_string())
            .expect("qualified artifact output");

        assert_eq!(generated.source_revision, "observed-source-revision");
        assert_eq!(generated.adapter_set_revision, adapter_set_revision);
        assert_eq!(
            generated.target_contract_revision,
            EMBEDDED_TARGET_CONTRACT_SHA256
        );
        assert_eq!(generated.records[0].exact_bytes, exact_text.as_bytes());
    }

    #[test]
    fn checkout_mutation_during_generation_is_rejected_as_stale() {
        let checkout = tempdir().unwrap();
        let body = checkout.path().join("components/skills/fixture/SKILL.md");
        fs::create_dir_all(body.parent().unwrap()).unwrap();
        fs::write(&body, "original renderer body\n").unwrap();
        let checkout_root = fs::canonicalize(checkout.path()).unwrap();
        let source_revision = SourceRevisionManifest::observe_root(&checkout_root)
            .unwrap()
            .sha256();
        let temporary = tempdir().unwrap();
        let app_temp_root = temporary.path().join("app-temp");
        fs::create_dir(&app_temp_root).unwrap();
        fs::set_permissions(&app_temp_root, fs::Permissions::from_mode(0o700)).unwrap();
        let app_temp_root = fs::canonicalize(app_temp_root).unwrap();
        let workspace_authority = Arc::new(ArtifactWorkspaceAuthority::new(app_temp_root).unwrap());
        workspace_authority
            .register_source(&source_revision, &checkout_root)
            .unwrap();
        let adapter = CapturedArtifactGenerationAdapter {
            workspace_authority,
            generator: Arc::new(AdversarialArtifactGenerator {
                behavior: AdversarialGeneration::MutateCheckout(body),
            }),
        };
        let request = CanonicalArtifactRequest::for_snapshot(
            "snapshot",
            source_revision,
            BTreeSet::from(["harnesskit.skill.fixture".to_string()]),
        )
        .unwrap();

        let error = adapter.generate_artifacts(&request).unwrap_err();

        assert_eq!(error.code(), "artifact_projection_stale");
    }

    #[test]
    fn returned_revision_mismatch_is_rejected_as_stale() {
        let (_checkout, _temporary, adapter, request) = artifact_adapter_fixture(
            AdversarialGeneration::ReturnRevision("different-revision".to_string()),
        );

        let error = adapter.generate_artifacts(&request).unwrap_err();

        assert_eq!(error.code(), "artifact_projection_stale");
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallPreviewInput {
    pub checkout_id: String,
    pub checkout_root: PathBuf,
    pub sot_snapshot_id: String,
    pub source_revision: String,
    pub profile_id: String,
    pub scope: String,
    pub target_root: PathBuf,
    pub target_ids: BTreeSet<String>,
    pub selected_component_ids: BTreeSet<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallPreviewArtifact {
    pub component_id: String,
    pub component_ids: Vec<String>,
    pub target: String,
    pub destination: String,
    pub merge_strategy: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallRuntimeGate {
    pub gate_id: String,
    pub target: String,
    pub safe_reason: String,
    pub required_before_apply: bool,
    pub required_before_runtime: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InstallRequiredApprovals {
    pub overwrite: bool,
    pub runtime_hooks: bool,
    pub management_adoption: bool,
    pub managed_replacement: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallPreview {
    pub preview_id: String,
    pub fingerprint: String,
    pub sot_snapshot_id: String,
    pub profile_id: String,
    pub scope: String,
    pub target_root: String,
    pub targets: Vec<String>,
    pub components: Vec<String>,
    pub artifacts: Vec<InstallPreviewArtifact>,
    pub skipped_writes: Vec<String>,
    pub warnings: Vec<String>,
    pub runtime_gates: Vec<InstallRuntimeGate>,
    pub non_atomic_boundary: bool,
    pub required_approvals: InstallRequiredApprovals,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallApprovals {
    pub confirmed: bool,
    pub semantic_fingerprint: String,
    pub overwrite: bool,
    pub allow_runtime_hooks: bool,
    pub adopt_management: bool,
    pub replace_managed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallDestinationResult {
    pub target: String,
    pub destination: String,
    pub apply_state: DestinationApplyState,
    pub verify_state: DestinationVerifyState,
    pub code: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallApplyOutcome {
    pub operation_id: String,
    pub status: InstallOperationStatus,
    pub destinations: Vec<InstallDestinationResult>,
    pub install_evidence_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct InstallEvidenceRecord {
    pub component_id: String,
    pub target_id: String,
    pub destination_key: DestinationKey,
    pub content_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallEvidenceSnapshot {
    pub evidence_id: String,
    pub sot_snapshot_id: String,
    pub install_semantic_fingerprint: String,
    pub verified_records: Vec<InstallEvidenceRecord>,
}

struct StoredPreview {
    input: InstallPreviewInput,
    normalized_target_root: PathBuf,
    target_identity: (u64, u64),
    source_manifest: SourceManifest,
    dry_plan: InstallPlan,
    canonical_artifact_request: CanonicalArtifactRequest,
    canonical_artifacts: GeneratedArtifactSet,
    canonical_artifact_fingerprint: String,
    fingerprint: String,
    required_approvals: InstallRequiredApprovals,
    adoption: Option<super::management::Adoption>,
}

pub struct InstallCoordinator {
    app_temp_root: PathBuf,
    generator: Arc<dyn InstallPlanGenerator>,
    artifact_generator: Arc<dyn ArtifactGenerationPort>,
    artifact_workspace_authority: Arc<ArtifactWorkspaceAuthority>,
    writer: InstallWriter,
    verifier: InstallVerifier,
    previews: Mutex<BTreeMap<String, StoredPreview>>,
    latest_evidence: Mutex<Option<InstallEvidenceSnapshot>>,
    next_preview: AtomicU64,
    next_operation: AtomicU64,
}

impl InstallCoordinator {
    pub fn new(
        app_temp_root: PathBuf,
        generator: Arc<dyn InstallPlanGenerator>,
    ) -> Result<Self, InstallCoordinatorError> {
        let artifact_workspace_authority =
            Arc::new(ArtifactWorkspaceAuthority::new(app_temp_root.clone())?);
        let artifact_generator: Arc<dyn ArtifactGenerationPort> =
            Arc::new(CapturedArtifactGenerationAdapter {
                workspace_authority: Arc::clone(&artifact_workspace_authority),
                generator: Arc::clone(&generator),
            });
        Self::new_with_artifact_services(
            app_temp_root,
            generator,
            artifact_generator,
            artifact_workspace_authority,
        )
    }

    pub fn new_with_artifact_generation_port(
        app_temp_root: PathBuf,
        generator: Arc<dyn InstallPlanGenerator>,
        artifact_generator: Arc<dyn ArtifactGenerationPort>,
    ) -> Result<Self, InstallCoordinatorError> {
        let artifact_workspace_authority =
            Arc::new(ArtifactWorkspaceAuthority::new(app_temp_root.clone())?);
        Self::new_with_artifact_services(
            app_temp_root,
            generator,
            artifact_generator,
            artifact_workspace_authority,
        )
    }

    fn new_with_artifact_services(
        app_temp_root: PathBuf,
        generator: Arc<dyn InstallPlanGenerator>,
        artifact_generator: Arc<dyn ArtifactGenerationPort>,
        artifact_workspace_authority: Arc<ArtifactWorkspaceAuthority>,
    ) -> Result<Self, InstallCoordinatorError> {
        validate_app_temp_root(&app_temp_root)?;
        Ok(Self {
            app_temp_root,
            generator,
            artifact_generator,
            artifact_workspace_authority,
            writer: InstallWriter::default(),
            verifier: InstallVerifier::default(),
            previews: Mutex::new(BTreeMap::new()),
            latest_evidence: Mutex::new(None),
            next_preview: AtomicU64::new(0),
            next_operation: AtomicU64::new(0),
        })
    }

    pub(crate) fn validate_import_candidate(
        &self,
        checkout_root: &Path,
        name: &str,
        manifest: &[u8],
        content: &[u8],
        registry: &[u8],
        documents: &[crate::contexts::sot::import::SupportDocument],
    ) -> Result<GeneratedArtifactSet, String> {
        let stage = InstallWorkspace::create(checkout_root, &self.app_temp_root)
            .map_err(|e| e.code().to_string())?;
        let value: serde_yaml::Value =
            serde_yaml::from_slice(manifest).map_err(|_| "import_manifest_invalid")?;
        let kind = value["kind"].as_str().ok_or("import_manifest_invalid")?;
        let directory = stage.root().join(format!("components/{kind}s/{name}"));
        std::fs::create_dir(&directory).map_err(|_| "import_collision".to_string())?;
        std::fs::write(directory.join("component.yml"), manifest)
            .map_err(|_| "import_stage_failed".to_string())?;
        std::fs::write(
            directory.join(if kind == "agent" {
                "prompt.md"
            } else if kind == "rule" {
                "rule.md"
            } else if kind == "hook" {
                "hook.md"
            } else {
                "SKILL.md"
            }),
            content,
        )
        .map_err(|_| "import_stage_failed".to_string())?;
        std::fs::write(stage.root().join("components/registry.yml"), registry)
            .map_err(|_| "import_stage_failed".to_string())?;
        for document in documents {
            let path = directory.join(&document.path);
            std::fs::create_dir_all(path.parent().ok_or("import_stage_failed")?)
                .map_err(|_| "import_stage_failed")?;
            std::fs::write(path, document.content.as_bytes()).map_err(|_| "import_stage_failed")?;
        }
        // Recapture after staging: the existing fixed runner requires immutable captured inputs.
        let candidate = InstallWorkspace::create(stage.root(), &self.app_temp_root)
            .map_err(|e| e.code().to_string())?;
        let component_id = format!("harnesskit.{kind}.{name}");
        let request = CanonicalArtifactRequest::for_snapshot(
            "import-candidate",
            candidate.source_manifest().sha256(),
            BTreeSet::from([component_id.clone()]),
        )
        .map_err(|e| e.code().to_string())?;
        let generated = self
            .generator
            .generate_artifacts_in_workspace(&candidate, &request)
            .map_err(|e| e.code().to_string())?;
        candidate
            .verify_source(stage.root())
            .map_err(|e| e.code().to_string())?;
        stage
            .verify_source(checkout_root)
            .map_err(|e| e.code().to_string())?;
        if !generated.issues.is_empty()
            || !generated.records.iter().any(|r| {
                r.component_id == component_id
                    && value["targets"].get(&r.target).is_some()
                    && !r.exact_bytes.is_empty()
            })
        {
            return Err("import_generation_failed".into());
        }
        Ok(generated)
    }

    pub(crate) fn validate_skill_edit(
        &self,
        root: &Path,
        name: &str,
        manifest: &[u8],
        content: &[u8],
        registry: &[u8],
        document: Option<&str>,
    ) -> Result<(), String> {
        let stage = InstallWorkspace::create(root, &self.app_temp_root)
            .map_err(|e| e.code().to_string())?;
        std::fs::remove_dir_all(stage.root().join(format!(
                "components/{}s/{name}",
                serde_yaml::from_slice::<serde_yaml::Value>(manifest)
                    .map_err(|_| "import_manifest_invalid")?["kind"]
                    .as_str()
                    .ok_or("import_manifest_invalid")?
            )))
        .map_err(|_| "import_stage_failed")?;
        let value: serde_yaml::Value =
            serde_yaml::from_slice(manifest).map_err(|_| "import_manifest_invalid")?;
        let directory = format!(
            "components/{}s/{name}",
            value["kind"].as_str().ok_or("import_manifest_invalid")?
        );
        let mut documents = vec![];
        for bundle in value["bundled_files"].as_sequence().into_iter().flatten() {
            let source = bundle["source"].as_str().ok_or("import_manifest_invalid")?;
            let path = source
                .strip_prefix(&format!("{directory}/"))
                .ok_or("import_source_unsafe")?;
            let text = if document == Some(path)
                || (document.is_none() && value["kind"] == "rule" && path == "rule.md")
            {
                std::str::from_utf8(content)
                    .map_err(|_| "import_invalid_utf8")?
                    .to_string()
            } else {
                String::from_utf8(super::writer::read_install_destination(root, source)?)
                    .map_err(|_| "import_invalid_utf8")?
            };
            documents.push(crate::contexts::sot::import::SupportDocument {
                path: path.into(),
                content: text,
                source_sha256: String::new(),
                source_revision: String::new(),
            });
        }
        let main = if document.is_some() {
            super::writer::read_install_destination(root, &format!("{directory}/SKILL.md"))?
        } else {
            content.to_vec()
        };
        self.validate_import_candidate(stage.root(), name, manifest, &main, registry, &documents)?;
        stage
            .verify_source(root)
            .map_err(|e| e.code().to_string())?;
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn with_test_writer(mut self, writer: InstallWriter) -> Self {
        self.writer = writer;
        self
    }

    pub(crate) fn artifact_generation_port(&self) -> Arc<dyn ArtifactGenerationPort> {
        Arc::clone(&self.artifact_generator)
    }

    pub(crate) fn register_artifact_source(
        &self,
        source_revision: &str,
        checkout_root: &Path,
    ) -> Result<(), InstallCoordinatorError> {
        self.artifact_workspace_authority
            .register_source(source_revision, checkout_root)
    }

    pub fn preview(
        &self,
        input: InstallPreviewInput,
    ) -> Result<InstallPreview, InstallCoordinatorError> {
        self.preview_with_management(input, None)
    }

    pub(crate) fn preview_with_management(
        &self,
        mut input: InstallPreviewInput,
        private: Option<&Path>,
    ) -> Result<InstallPreview, InstallCoordinatorError> {
        validate_input(&input)?;
        let (target_root, target_identity) = normalize_root(&input.target_root)?;
        input.target_root = target_root.clone();
        let workspace = InstallWorkspace::create(&input.checkout_root, &self.app_temp_root)
            .map_err(|error| InstallCoordinatorError::new(error.code()))?;
        let source_manifest = workspace.source_manifest().clone();
        let request = plan_request(&input, PlanMode::DryRun);
        let dry_plan = self.generator.generate(&workspace, &request)?;
        let adoption = private
            .map(|root| super::management::prepare(root, &input, &dry_plan))
            .transpose()?
            .flatten();
        let validator = PlanValidator::embedded()
            .map_err(|_| InstallCoordinatorError::new("install_contract_unsupported"))?;
        let validated = validator
            .validate_with_management(
                &dry_plan,
                &InstallRequest {
                    scope: input.scope.clone(),
                    targets: input.target_ids.clone(),
                },
                &input.selected_component_ids,
                adoption.as_ref(),
            )
            .map_err(|error| InstallCoordinatorError::new(error.code()))?;
        if dry_plan.mode != "dry-run" {
            return Err(InstallCoordinatorError::new("invalid_plan_mode"));
        }

        let canonical_artifact_request = CanonicalArtifactRequest::for_snapshot(
            &input.sot_snapshot_id,
            &input.source_revision,
            input.selected_component_ids.clone(),
        )?;
        let canonical_artifacts = self
            .artifact_generator
            .generate_artifacts(&canonical_artifact_request)?;
        validate_generated_artifacts(&canonical_artifact_request, &canonical_artifacts)?;
        let canonical_artifact_fingerprint = generated_artifact_set_sha256(&canonical_artifacts)?;
        let base_fingerprint = preview_fingerprint(
            &input,
            target_identity,
            &source_manifest,
            validated.semantic_fingerprint(),
            self.generator.runtime_manifest_sha256(),
            &canonical_artifact_fingerprint,
        )?;
        let fingerprint = management_fingerprint(&base_fingerprint, adoption.as_ref());
        let preview_sequence = self.next_preview.fetch_add(1, Ordering::Relaxed) + 1;
        let preview_id = hex_sha256(
            format!("{fingerprint}\0{}\0{preview_sequence}", std::process::id()).as_bytes(),
        );
        let runtime_gates = runtime_gates(&dry_plan);
        let required_approvals = InstallRequiredApprovals {
            managed_replacement: adoption.as_ref().is_some_and(|a| a.requires_replacement()),
            management_adoption: adoption.as_ref().is_some_and(|a| {
                a.selected
                    .iter()
                    .any(|(i, _, _, _)| a.links[*i]["managed"] == false)
            }),
            overwrite: plan_touches_existing_destination(&target_root, &dry_plan),
            runtime_hooks: runtime_gates
                .iter()
                .any(|gate| gate.required_before_apply || gate.required_before_runtime),
        };
        let mut preview = project_preview(
            &preview_id,
            &fingerprint,
            &input,
            &dry_plan,
            runtime_gates,
            required_approvals,
        );
        if let Some(adoption) = &adoption {
            for (index, tool, destination, hash) in &adoption.selected {
                let before = super::writer::read_install_destination(&target_root, destination)
                    .map_err(|_| InstallCoordinatorError::new("preview_stale"))?;
                if super::management::managed_hash(&adoption.links[*index], &before)? != *hash {
                    return Err(InstallCoordinatorError::new("preview_stale"));
                }
                let after = canonical_artifacts
                    .records
                    .iter()
                    .find(|r| {
                        &r.target == tool && &r.destination == destination && r.scope == input.scope
                    })
                    .ok_or_else(|| InstallCoordinatorError::new("artifact_projection_invalid"))?;
                preview.warnings.push(format!(
                    "{}: {destination}\n현재 원본 → 적용할 canonical\n--- 현재 원본\n+++ 적용할 canonical\n{}\n{}",
                    adoption.review_label(*index, hash),
                    String::from_utf8_lossy(&super::management::managed_bytes(&adoption.links[*index], &before)?).lines().map(|s| format!("-{s}")).collect::<Vec<_>>().join("\n"),
                    String::from_utf8_lossy(&after.exact_bytes).lines().map(|s| format!("+{s}")).collect::<Vec<_>>().join("\n")
                ));
            }
        }
        self.previews
            .lock()
            .map_err(|_| InstallCoordinatorError::new("install_preview_store_unavailable"))?
            .insert(
                preview_id,
                StoredPreview {
                    input,
                    normalized_target_root: target_root,
                    target_identity,
                    source_manifest,
                    dry_plan,
                    canonical_artifact_request,
                    canonical_artifacts,
                    canonical_artifact_fingerprint,
                    fingerprint,
                    required_approvals,
                    adoption,
                },
            );
        Ok(preview)
    }

    pub fn apply(
        &self,
        preview_id: &str,
        approvals: InstallApprovals,
    ) -> Result<InstallApplyOutcome, InstallCoordinatorError> {
        if !approvals.confirmed {
            return Err(InstallCoordinatorError::new(
                "install_confirmation_required",
            ));
        }
        let stored = {
            let mut previews = self
                .previews
                .lock()
                .map_err(|_| InstallCoordinatorError::new("install_preview_store_unavailable"))?;
            let preview = previews
                .get(preview_id)
                .ok_or_else(|| InstallCoordinatorError::new("install_preview_unavailable"))?;
            if approvals.semantic_fingerprint != preview.fingerprint {
                return Err(InstallCoordinatorError::new("preview_stale"));
            }
            if preview.required_approvals.management_adoption && !approvals.adopt_management {
                return Err(InstallCoordinatorError::new(
                    "management_adoption_approval_required",
                ));
            }
            if preview.required_approvals.managed_replacement && !approvals.replace_managed {
                return Err(InstallCoordinatorError::new(
                    "managed_replacement_approval_required",
                ));
            }
            if preview.required_approvals.overwrite && !approvals.overwrite {
                return Err(InstallCoordinatorError::new("overwrite_approval_required"));
            }
            if preview.required_approvals.runtime_hooks && !approvals.allow_runtime_hooks {
                return Err(InstallCoordinatorError::new(
                    "runtime_hook_approval_required",
                ));
            }
            previews
                .remove(preview_id)
                .expect("preview existence was checked under the same lock")
        };
        *self
            .latest_evidence
            .lock()
            .map_err(|_| InstallCoordinatorError::new("install_evidence_store_unavailable"))? =
            None;

        require_same_root(
            &stored.normalized_target_root,
            stored.target_identity,
            "preview_stale",
        )?;
        let workspace = InstallWorkspace::create(&stored.input.checkout_root, &self.app_temp_root)
            .map_err(|_| InstallCoordinatorError::new("preview_stale"))?;
        if workspace.source_manifest() != &stored.source_manifest {
            return Err(InstallCoordinatorError::new("preview_stale"));
        }
        let current_canonical_artifacts = self
            .artifact_generator
            .generate_artifacts(&stored.canonical_artifact_request)
            .map_err(|_| InstallCoordinatorError::new("preview_stale"))?;
        validate_generated_artifacts(
            &stored.canonical_artifact_request,
            &current_canonical_artifacts,
        )
        .map_err(|_| InstallCoordinatorError::new("preview_stale"))?;
        if current_canonical_artifacts != stored.canonical_artifacts
            || generated_artifact_set_sha256(&current_canonical_artifacts)
                .map_err(|_| InstallCoordinatorError::new("preview_stale"))?
                != stored.canonical_artifact_fingerprint
        {
            return Err(InstallCoordinatorError::new("preview_stale"));
        }
        let apply_plan = self
            .generator
            .generate(&workspace, &plan_request(&stored.input, PlanMode::Apply))?;
        let validator = PlanValidator::embedded()
            .map_err(|_| InstallCoordinatorError::new("install_contract_unsupported"))?;
        let validated = validator
            .validate_with_management(
                &apply_plan,
                &InstallRequest {
                    scope: stored.input.scope.clone(),
                    targets: stored.input.target_ids.clone(),
                },
                &stored.input.selected_component_ids,
                stored.adoption.as_ref(),
            )
            .map_err(|_| InstallCoordinatorError::new("preview_stale"))?;
        validator
            .validate_dry_run_apply_semantic_equality(&stored.dry_plan, &apply_plan)
            .map_err(|_| InstallCoordinatorError::new("preview_stale"))?;
        let current_fingerprint = preview_fingerprint(
            &stored.input,
            stored.target_identity,
            workspace.source_manifest(),
            validator
                .validate_with_management(
                    &stored.dry_plan,
                    &InstallRequest {
                        scope: stored.input.scope.clone(),
                        targets: stored.input.target_ids.clone(),
                    },
                    &stored.input.selected_component_ids,
                    stored.adoption.as_ref(),
                )
                .map_err(|_| InstallCoordinatorError::new("preview_stale"))?
                .semantic_fingerprint(),
            self.generator.runtime_manifest_sha256(),
            &stored.canonical_artifact_fingerprint,
        )?;
        if management_fingerprint(&current_fingerprint, stored.adoption.as_ref())
            != stored.fingerprint
        {
            return Err(InstallCoordinatorError::new("preview_stale"));
        }
        require_same_root(
            &stored.normalized_target_root,
            stored.target_identity,
            "preview_stale",
        )?;
        let artifact_authority = install_artifact_authority(&current_canonical_artifacts);
        self.writer
            .validate_artifact_authority(&validated, &workspace, &artifact_authority)
            .map_err(|error| InstallCoordinatorError::new(error.code()))?;
        let target_roots = InstallTargetRoots::new(
            stored
                .input
                .target_ids
                .iter()
                .map(|target| (target.clone(), stored.normalized_target_root.clone()))
                .collect(),
            stored
                .input
                .target_ids
                .iter()
                .map(|target| (target.clone(), stored.target_identity))
                .collect(),
        )
        .map_err(|error| InstallCoordinatorError::new(error.code()))?;
        if let Some(adoption) = &stored.adoption {
            adoption.revalidate(&stored.normalized_target_root)?;
        }
        let expected = stored
            .adoption
            .as_ref()
            .map(|a| {
                a.selected
                    .iter()
                    .map(|(index, tool, destination, hash)| {
                        let bytes = super::writer::read_install_destination(
                            &stored.normalized_target_root,
                            destination,
                        )
                        .map_err(|_| InstallCoordinatorError::new("preview_stale"))?;
                        if super::management::managed_hash(&a.links[*index], &bytes)? != *hash {
                            return Err(InstallCoordinatorError::new("preview_stale"));
                        }
                        Ok(((tool.clone(), destination.clone()), hex_sha256(&bytes)))
                    })
                    .collect::<Result<BTreeMap<_, _>, InstallCoordinatorError>>()
            })
            .transpose()?
            .unwrap_or_default();
        let applied = self
            .writer
            .apply_with_expected(&validated, &workspace, &target_roots, &expected)
            .map_err(|error| InstallCoordinatorError::new(error.code()))?;
        let verified = self
            .verifier
            .verify(&validated, &workspace, &target_roots, &applied)
            .map_err(|error| InstallCoordinatorError::new(error.code()))?;
        let destinations = combine_destinations(&applied, &verified);
        let status = combine_status(applied.status(), verified.status());
        let operation_sequence = self.next_operation.fetch_add(1, Ordering::Relaxed) + 1;
        let operation_id = format!("install-operation-{operation_sequence}");
        let evidence = build_evidence(&stored, status, &validated, &verified);
        if let Some(adoption) = stored.adoption {
            adoption.persist(&destinations, verified.destinations(), &operation_id)?;
        }
        let install_evidence_id = evidence
            .as_ref()
            .map(|evidence| evidence.evidence_id.clone());
        if let Some(evidence) = evidence {
            *self
                .latest_evidence
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(evidence);
        }
        Ok(InstallApplyOutcome {
            operation_id,
            status,
            destinations,
            install_evidence_id,
        })
    }

    pub fn latest_evidence(&self) -> Option<InstallEvidenceSnapshot> {
        self.latest_evidence
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }

    pub(crate) fn reset_revision(&self) {
        self.previews
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clear();
        *self
            .latest_evidence
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = None;
        self.artifact_workspace_authority.clear();
    }
}

fn management_fingerprint(base: &str, adoption: Option<&super::management::Adoption>) -> String {
    match adoption {
        Some(a) => hex_sha256(
            &serde_json::to_vec(&(base, &a.before, &a.selected))
                .expect("serializable approval binding"),
        ),
        None => base.into(),
    }
}

fn validate_input(input: &InstallPreviewInput) -> Result<(), InstallCoordinatorError> {
    if input.checkout_id.is_empty()
        || input.sot_snapshot_id.is_empty()
        || !is_sha256(&input.source_revision)
        || input.profile_id.is_empty()
        || !matches!(input.scope.as_str(), "user" | "project")
        || input.target_ids.is_empty()
        || input.selected_component_ids.is_empty()
        || input
            .target_ids
            .iter()
            .chain(input.selected_component_ids.iter())
            .any(|value| !safe_token(value))
    {
        return Err(InstallCoordinatorError::new("install_request_invalid"));
    }
    Ok(())
}

fn validate_app_temp_root(path: &Path) -> Result<(), InstallCoordinatorError> {
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|_| InstallCoordinatorError::new("install_workspace_unavailable"))?;
    if metadata.file_type().is_dir()
        && !metadata.file_type().is_symlink()
        && metadata.mode() & 0o077 == 0
    {
        Ok(())
    } else {
        Err(InstallCoordinatorError::new(
            "install_workspace_unavailable",
        ))
    }
}

fn validate_generated_artifacts(
    request: &CanonicalArtifactRequest,
    generated: &GeneratedArtifactSet,
) -> Result<(), InstallCoordinatorError> {
    if generated.source_revision != request.source_revision {
        return Err(InstallCoordinatorError::new("artifact_projection_stale"));
    }
    if generated.target_contract_revision != request.target_contract_revision
        || generated.generator_version != "canonical-artifact-generator-v1"
        || !is_sha256(&generated.adapter_set_revision)
    {
        return Err(InstallCoordinatorError::new("artifact_projection_invalid"));
    }
    let mut identities = BTreeSet::new();
    for record in &generated.records {
        let expected_adapter_id = qualified_adapter_id_for_target(&record.target);
        let identity = (
            record.component_id.as_str(),
            record.adapter_id.as_str(),
            record.target.as_str(),
            record.scope.as_str(),
            record.destination.as_str(),
            record.config_entry_locator.as_deref(),
        );
        if !request.component_ids.contains(&record.component_id)
            || !request.target_ids.contains(&record.target)
            || !request.qualified_adapter_ids.contains(&record.adapter_id)
            || record.adapter_id != expected_adapter_id
            || !safe_token(&record.component_id)
            || !safe_token(&record.adapter_id)
            || !safe_token(&record.adapter_version)
            || !matches!(record.scope.as_str(), "user" | "project")
            || !canonical_relative_path(&record.destination)
            || !canonical_relative_path(&record.source)
            || record
                .merge_strategy
                .as_deref()
                .is_some_and(|value| !safe_token(value))
            || record
                .config_entry_locator
                .as_deref()
                .is_some_and(|value| !safe_token(value))
            || !is_sha256(&record.content_sha256)
            || hex_sha256(&record.exact_bytes) != record.content_sha256
            || !identities.insert(identity)
        {
            return Err(InstallCoordinatorError::new("artifact_projection_invalid"));
        }
    }
    Ok(())
}

fn install_artifact_authority(generated: &GeneratedArtifactSet) -> InstallArtifactAuthority {
    InstallArtifactAuthority {
        records: generated
            .records
            .iter()
            .map(|record| InstallArtifactAuthorityRecord {
                component_id: record.component_id.clone(),
                target: record.target.clone(),
                scope: record.scope.clone(),
                destination: record.destination.clone(),
                source: record.source.clone(),
                merge_strategy: record.merge_strategy.clone(),
                config_entry_locator: record.config_entry_locator.clone(),
                content_sha256: record.content_sha256.clone(),
                exact_bytes: record.exact_bytes.clone(),
            })
            .collect(),
    }
}

fn generated_artifact_set_sha256(
    generated: &GeneratedArtifactSet,
) -> Result<String, InstallCoordinatorError> {
    let records = generated
        .records
        .iter()
        .map(|record| {
            (
                record.component_id.as_str(),
                record.adapter_id.as_str(),
                record.adapter_version.as_str(),
                record.target.as_str(),
                record.scope.as_str(),
                record.destination.as_str(),
                record.source.as_str(),
                record.merge_strategy.as_deref(),
                record.config_entry_locator.as_deref(),
                record.content_sha256.as_str(),
                record.exact_bytes.as_slice(),
            )
        })
        .collect::<Vec<_>>();
    serde_json::to_vec(&(
        generated.generator_version.as_str(),
        generated.source_revision.as_str(),
        generated.adapter_set_revision.as_str(),
        generated.target_contract_revision.as_str(),
        records,
        &generated.issues,
    ))
    .map(|bytes| hex_sha256(&bytes))
    .map_err(|_| InstallCoordinatorError::new("artifact_projection_invalid"))
}

fn safe_token(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':'))
}

fn qualified_adapter_id_for_target(target: &str) -> String {
    if target == "project" {
        "project".to_string()
    } else {
        format!("harnesskit.adapter.{target}")
    }
}

fn canonical_relative_path(value: &str) -> bool {
    !value.is_empty()
        && !value.starts_with('/')
        && !value.starts_with('~')
        && !value.contains('\\')
        && !value.contains('\0')
        && value
            .split('/')
            .all(|segment| !segment.is_empty() && segment != "." && segment != "..")
}

fn normalize_root(path: &Path) -> Result<(PathBuf, (u64, u64)), InstallCoordinatorError> {
    if !canonical_absolute_path(path) {
        return Err(InstallCoordinatorError::new("target_root_invalid"));
    }
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|_| InstallCoordinatorError::new("target_root_invalid"))?;
    let canonical = std::fs::canonicalize(path)
        .map_err(|_| InstallCoordinatorError::new("target_root_invalid"))?;
    if metadata.file_type().is_symlink() || !metadata.file_type().is_dir() || canonical != path {
        return Err(InstallCoordinatorError::new("target_root_invalid"));
    }
    Ok((canonical, (metadata.dev(), metadata.ino())))
}

fn require_same_root(
    path: &Path,
    expected: (u64, u64),
    code: &str,
) -> Result<(), InstallCoordinatorError> {
    let metadata =
        std::fs::symlink_metadata(path).map_err(|_| InstallCoordinatorError::new(code))?;
    if metadata.file_type().is_symlink()
        || !metadata.file_type().is_dir()
        || (metadata.dev(), metadata.ino()) != expected
    {
        return Err(InstallCoordinatorError::new(code));
    }
    Ok(())
}

fn canonical_absolute_path(path: &Path) -> bool {
    path.is_absolute()
        && path
            .components()
            .all(|component| matches!(component, Component::RootDir | Component::Normal(_)))
}

fn plan_request(input: &InstallPreviewInput, mode: PlanMode) -> PlanRunRequest {
    PlanRunRequest {
        mode,
        profile: input.profile_id.clone(),
        scope: input.scope.clone(),
        target_ids: input.target_ids.iter().cloned().collect(),
        component_ids: input.selected_component_ids.iter().cloned().collect(),
    }
}

#[derive(Serialize)]
struct FingerprintInput<'a> {
    coordinator_version: &'static str,
    writer_version: &'static str,
    contract_hash: &'static str,
    checkout_id: &'a str,
    sot_snapshot_id: &'a str,
    profile_id: &'a str,
    scope: &'a str,
    target_root: String,
    target_root_device: u64,
    target_root_inode: u64,
    targets: Vec<&'a str>,
    components: Vec<&'a str>,
    source_manifest_sha256: String,
    semantic_fingerprint: &'a str,
    runtime_manifest_sha256: &'a str,
    canonical_artifact_fingerprint: &'a str,
}

fn preview_fingerprint(
    input: &InstallPreviewInput,
    target_identity: (u64, u64),
    source_manifest: &SourceManifest,
    semantic_fingerprint: &str,
    runtime_manifest_sha256: &str,
    canonical_artifact_fingerprint: &str,
) -> Result<String, InstallCoordinatorError> {
    let value = FingerprintInput {
        coordinator_version: INSTALL_COORDINATOR_VERSION,
        writer_version: INSTALL_WRITER_VERSION,
        contract_hash: EMBEDDED_TARGET_CONTRACT_SHA256,
        checkout_id: &input.checkout_id,
        sot_snapshot_id: &input.sot_snapshot_id,
        profile_id: &input.profile_id,
        scope: &input.scope,
        target_root: input.target_root.to_string_lossy().into_owned(),
        target_root_device: target_identity.0,
        target_root_inode: target_identity.1,
        targets: input.target_ids.iter().map(String::as_str).collect(),
        components: input
            .selected_component_ids
            .iter()
            .map(String::as_str)
            .collect(),
        source_manifest_sha256: source_manifest.sha256(),
        semantic_fingerprint,
        runtime_manifest_sha256,
        canonical_artifact_fingerprint,
    };
    serde_json::to_vec(&value)
        .map(|bytes| hex_sha256(&bytes))
        .map_err(|_| InstallCoordinatorError::new("install_fingerprint_failed"))
}

fn project_preview(
    preview_id: &str,
    fingerprint: &str,
    input: &InstallPreviewInput,
    plan: &InstallPlan,
    runtime_gates: Vec<InstallRuntimeGate>,
    required_approvals: InstallRequiredApprovals,
) -> InstallPreview {
    InstallPreview {
        preview_id: preview_id.to_string(),
        fingerprint: fingerprint.to_string(),
        sot_snapshot_id: input.sot_snapshot_id.clone(),
        profile_id: input.profile_id.clone(),
        scope: input.scope.clone(),
        target_root: input.target_root.to_string_lossy().into_owned(),
        targets: plan.targets.clone(),
        components: plan.components.clone(),
        artifacts: plan.artifacts.iter().map(project_artifact).collect(),
        skipped_writes: Vec::new(),
        warnings: Vec::new(),
        runtime_gates,
        non_atomic_boundary: true,
        required_approvals,
    }
}

fn project_artifact(artifact: &PlanArtifact) -> InstallPreviewArtifact {
    InstallPreviewArtifact {
        component_id: artifact.component_id.clone(),
        component_ids: artifact.component_ids.clone(),
        target: artifact.target.clone(),
        destination: artifact.destination.clone(),
        merge_strategy: artifact.merge_strategy.clone(),
    }
}

fn runtime_gates(plan: &InstallPlan) -> Vec<InstallRuntimeGate> {
    plan.activation_gates
        .iter()
        .filter_map(|value| {
            Some(InstallRuntimeGate {
                gate_id: value.get("id")?.as_str()?.to_string(),
                target: value.get("target")?.as_str()?.to_string(),
                safe_reason: value.get("reason")?.as_str()?.to_string(),
                required_before_apply: value
                    .get("required_before_apply")
                    .and_then(serde_json::Value::as_bool)
                    .unwrap_or(false),
                required_before_runtime: value
                    .get("required_before_runtime")
                    .and_then(serde_json::Value::as_bool)
                    .unwrap_or(false),
            })
        })
        .collect()
}

fn plan_touches_existing_destination(target_root: &Path, plan: &InstallPlan) -> bool {
    plan.artifacts
        .iter()
        .any(|artifact| std::fs::symlink_metadata(target_root.join(&artifact.destination)).is_ok())
}

fn combine_destinations(
    applied: &super::writer::InstallApplyReport,
    verified: &super::writer::InstallVerifyReport,
) -> Vec<InstallDestinationResult> {
    let verify_by_key = verified
        .destinations()
        .iter()
        .map(|outcome| {
            (
                (outcome.target.as_str(), outcome.destination.as_str()),
                outcome,
            )
        })
        .collect::<BTreeMap<_, _>>();
    applied
        .destinations()
        .iter()
        .map(|outcome| {
            let verified =
                verify_by_key.get(&(outcome.target.as_str(), outcome.destination.as_str()));
            InstallDestinationResult {
                target: outcome.target.clone(),
                destination: outcome.destination.clone(),
                apply_state: outcome.state,
                verify_state: verified
                    .map(|outcome| outcome.state)
                    .unwrap_or(DestinationVerifyState::NotAttempted),
                code: verified
                    .and_then(|outcome| outcome.code.clone())
                    .or_else(|| outcome.code.clone()),
            }
        })
        .collect()
}

fn combine_status(
    apply: InstallOperationStatus,
    verify: InstallOperationStatus,
) -> InstallOperationStatus {
    match (apply, verify) {
        (InstallOperationStatus::Complete, InstallOperationStatus::Complete) => {
            InstallOperationStatus::Complete
        }
        (InstallOperationStatus::Failed, InstallOperationStatus::Failed) => {
            InstallOperationStatus::Failed
        }
        _ => InstallOperationStatus::Partial,
    }
}

fn build_evidence(
    stored: &StoredPreview,
    status: InstallOperationStatus,
    validated: &ValidatedPlan,
    verified: &super::writer::InstallVerifyReport,
) -> Option<InstallEvidenceSnapshot> {
    if status != InstallOperationStatus::Complete
        || verified
            .destinations()
            .iter()
            .any(|outcome| outcome.state != DestinationVerifyState::Verified)
    {
        return None;
    }
    let artifact_by_key = validated
        .artifacts()
        .iter()
        .map(|artifact| {
            (
                (artifact.target.as_str(), artifact.destination.as_str()),
                artifact,
            )
        })
        .collect::<BTreeMap<_, _>>();
    let mut verified_records = Vec::new();
    for outcome in verified.destinations() {
        let artifact =
            artifact_by_key.get(&(outcome.target.as_str(), outcome.destination.as_str()))?;
        let content_sha256 = outcome.content_sha256.as_ref()?;
        let component_ids = if artifact.component_ids.is_empty() {
            vec![artifact.component_id.as_str()]
        } else {
            artifact.component_ids.iter().map(String::as_str).collect()
        };
        for destination_key in &outcome.correlation_keys {
            verified_records.extend(component_ids.iter().map(|component_id| {
                InstallEvidenceRecord {
                    component_id: (*component_id).to_string(),
                    target_id: outcome.target.clone(),
                    destination_key: destination_key.clone(),
                    content_sha256: content_sha256.clone(),
                }
            }));
        }
    }
    verified_records.sort();
    verified_records.dedup();
    let semantic_fingerprint = validated.semantic_fingerprint().to_string();
    let canonical = serde_json::to_vec(&(
        "install-evidence-v2",
        stored.input.sot_snapshot_id.as_str(),
        semantic_fingerprint.as_str(),
        &verified_records,
    ))
    .ok()?;
    Some(InstallEvidenceSnapshot {
        evidence_id: hex_sha256(&canonical),
        sot_snapshot_id: stored.input.sot_snapshot_id.clone(),
        install_semantic_fingerprint: semantic_fingerprint,
        verified_records,
    })
}

fn hex_sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
