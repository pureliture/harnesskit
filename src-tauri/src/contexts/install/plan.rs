use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use super::target_contract::{
    embedded_target_contract, ContractError, InstallTargetContract, MergeDestinationContract,
    EMBEDDED_TARGET_CONTRACT_ID, EMBEDDED_TARGET_CONTRACT_SHA256,
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstallRequest {
    pub scope: String,
    pub targets: BTreeSet<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstallPlan {
    pub install_contract_id: String,
    pub install_contract_hash: String,
    pub plan_id: String,
    #[serde(default)]
    pub profile_id: Option<String>,
    pub scope: String,
    pub mode: String,
    pub targets: Vec<String>,
    pub components: Vec<String>,
    pub runtime_surfaces: Vec<PlanRuntimeSurface>,
    pub artifacts: Vec<PlanArtifact>,
    #[serde(default)]
    pub component_contracts: Vec<Value>,
    #[serde(default)]
    pub activation_gates: Vec<Value>,
    #[serde(default)]
    pub materialization: Option<Value>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlanRuntimeSurface {
    pub target: String,
    pub path: String,
    pub source: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlanArtifact {
    pub component_id: String,
    #[serde(default)]
    pub component_ids: Vec<String>,
    pub target: String,
    pub source: String,
    pub destination: String,
    #[serde(default)]
    pub merge_strategy: Option<String>,
    #[serde(default)]
    pub begin_marker: Option<String>,
    #[serde(default)]
    pub end_marker: Option<String>,
    #[serde(default)]
    pub json_merge_key: Option<String>,
    #[serde(default)]
    pub toml_merge_key: Option<String>,
    #[serde(default)]
    pub scope: Option<String>,
    #[serde(default)]
    pub mode: Option<u32>,
    #[serde(default)]
    pub source_sha256: Option<String>,
    #[serde(default)]
    pub skip_write: bool,
    #[serde(default)]
    pub ownership: Option<Value>,
    #[serde(default)]
    pub runtime_contract: Option<Value>,
    #[serde(default)]
    pub evidence_status: Option<Value>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ValidatedPlan {
    contract_hash: String,
    semantic_fingerprint: String,
    request_scope: String,
    request_targets: BTreeSet<String>,
    canonical_artifacts: Vec<PlanArtifact>,
    component_count: usize,
    mode: String,
}

impl ValidatedPlan {
    pub fn contract_hash(&self) -> &str {
        &self.contract_hash
    }

    pub fn semantic_fingerprint(&self) -> &str {
        &self.semantic_fingerprint
    }

    pub fn component_count(&self) -> usize {
        self.component_count
    }

    pub fn artifact_count(&self) -> usize {
        self.canonical_artifacts.len()
    }

    pub fn request_targets(&self) -> &BTreeSet<String> {
        &self.request_targets
    }

    pub(crate) fn request_scope(&self) -> &str {
        &self.request_scope
    }

    pub(crate) fn artifacts(&self) -> &[PlanArtifact] {
        &self.canonical_artifacts
    }

    pub(crate) fn mode(&self) -> &str {
        &self.mode
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanValidationError {
    code: &'static str,
    detail: String,
}

impl PlanValidationError {
    fn new(code: &'static str, detail: impl Into<String>) -> Self {
        Self {
            code,
            detail: detail.into(),
        }
    }

    pub fn code(&self) -> &'static str {
        self.code
    }
}

impl fmt::Display for PlanValidationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.detail)
    }
}

impl std::error::Error for PlanValidationError {}

#[derive(Debug, Clone, Copy)]
pub struct PlanValidator {
    contract: &'static InstallTargetContract,
}

impl PlanValidator {
    pub fn embedded() -> Result<Self, ContractError> {
        Ok(Self {
            contract: embedded_target_contract()?,
        })
    }

    pub fn validate(
        &self,
        plan: &InstallPlan,
        request: &InstallRequest,
        selected_component_closure: &BTreeSet<String>,
    ) -> Result<ValidatedPlan, PlanValidationError> {
        self.validate_contract_identity(plan)?;
        self.validate_plan_mode(&plan.mode)?;
        if plan.scope != request.scope {
            return Err(PlanValidationError::new(
                "scope_mismatch",
                "plan scope differs from normalized request scope",
            ));
        }

        let target_set = unique_set(&plan.targets, "duplicate_target")?;
        for target_id in &target_set {
            let target = self.contract.target(target_id).ok_or_else(|| {
                PlanValidationError::new("unknown_target", format!("unknown target: {target_id}"))
            })?;
            if !target.scopes.iter().any(|scope| scope == &plan.scope) {
                return Err(PlanValidationError::new(
                    "scope_mismatch",
                    format!("target {target_id} does not support scope {}", plan.scope),
                ));
            }
        }
        if target_set != request.targets {
            return Err(PlanValidationError::new(
                "target_set_mismatch",
                "plan target set differs from normalized request",
            ));
        }

        self.validate_selected_component_closure(plan, selected_component_closure)?;

        let provided_surfaces = self.validate_provided_surfaces(plan, &target_set)?;
        let mut derived_surfaces = BTreeSet::new();
        let mut destination_owners: BTreeMap<&str, &str> = BTreeMap::new();
        let mut exact_destination_keys = BTreeSet::new();

        for artifact in &plan.artifacts {
            if !target_set.contains(&artifact.target) {
                let code = if self.contract.target(&artifact.target).is_some() {
                    "target_set_mismatch"
                } else {
                    "unknown_target"
                };
                return Err(PlanValidationError::new(
                    code,
                    format!("artifact target is not selected: {}", artifact.target),
                ));
            }
            validate_relative_path(&artifact.source)?;
            validate_relative_path(&artifact.destination)?;
            self.validate_artifact_components(artifact, selected_component_closure)?;
            if artifact
                .scope
                .as_ref()
                .is_some_and(|scope| scope != &plan.scope)
            {
                return Err(PlanValidationError::new(
                    "scope_mismatch",
                    format!("artifact scope differs: {}", artifact.destination),
                ));
            }
            if artifact.skip_write {
                return Err(PlanValidationError::new(
                    "skipped_write",
                    format!("artifact requests skipped write: {}", artifact.destination),
                ));
            }
            self.validate_source(artifact)?;
            let surface = self
                .contract
                .runtime_surface(&artifact.target, &artifact.destination)
                .ok_or_else(|| {
                    PlanValidationError::new(
                        "runtime_surface_mismatch",
                        format!(
                            "artifact is outside canonical runtime surfaces: {}:{}",
                            artifact.target, artifact.destination
                        ),
                    )
                })?;
            self.validate_merge_contract(artifact)?;
            self.validate_file_mode(artifact, plan.mode == "apply")?;
            self.validate_source_hash(artifact, plan.mode == "apply")?;

            let destination_key = (artifact.target.as_str(), artifact.destination.as_str());
            if !exact_destination_keys.insert(destination_key) {
                return Err(PlanValidationError::new(
                    "duplicate_destination",
                    format!("duplicate artifact destination: {}", artifact.destination),
                ));
            }
            if let Some(previous_target) =
                destination_owners.insert(artifact.destination.as_str(), artifact.target.as_str())
            {
                if previous_target != artifact.target {
                    return Err(PlanValidationError::new(
                        "destination_fan_in",
                        format!("cross-target destination fan-in: {}", artifact.destination),
                    ));
                }
            }

            let source = if artifact.destination == surface.prefix {
                artifact.source.clone()
            } else {
                surface.source.clone()
            };
            derived_surfaces.insert(PlanRuntimeSurface {
                target: artifact.target.clone(),
                path: surface.prefix.clone(),
                source,
            });
        }

        if provided_surfaces != derived_surfaces {
            return Err(PlanValidationError::new(
                "runtime_surface_mismatch",
                "plan runtime surfaces are not the exact artifact-derived set",
            ));
        }

        let semantic_fingerprint = semantic_fingerprint(plan)?;
        let mut canonical_artifacts = plan.artifacts.clone();
        canonical_artifacts.sort_by(|left, right| {
            (left.target.as_str(), left.destination.as_str())
                .cmp(&(right.target.as_str(), right.destination.as_str()))
        });
        Ok(ValidatedPlan {
            contract_hash: EMBEDDED_TARGET_CONTRACT_SHA256.to_string(),
            semantic_fingerprint,
            request_scope: request.scope.clone(),
            request_targets: request.targets.clone(),
            canonical_artifacts,
            component_count: plan.components.len(),
            mode: plan.mode.clone(),
        })
    }

    pub fn validate_selected_component_closure(
        &self,
        plan: &InstallPlan,
        selected_component_closure: &BTreeSet<String>,
    ) -> Result<(), PlanValidationError> {
        let components = unique_set(&plan.components, "duplicate_component")?;
        if &components != selected_component_closure {
            return Err(PlanValidationError::new(
                "component_closure_mismatch",
                "plan components differ from independently selected closure",
            ));
        }
        Ok(())
    }

    pub fn validate_dry_run_apply_semantic_equality(
        &self,
        dry_run: &InstallPlan,
        apply: &InstallPlan,
    ) -> Result<String, PlanValidationError> {
        if dry_run.mode != "dry-run" || apply.mode != "apply" {
            return Err(PlanValidationError::new(
                "invalid_plan_mode",
                "semantic comparison requires dry-run followed by apply",
            ));
        }
        let dry_projection = semantic_projection(dry_run)?;
        let apply_projection = semantic_projection(apply)?;
        if dry_projection != apply_projection {
            return Err(PlanValidationError::new(
                "semantic_plan_mismatch",
                "apply plan semantics differ from preview",
            ));
        }
        hash_serializable(&dry_projection)
    }

    fn validate_contract_identity(&self, plan: &InstallPlan) -> Result<(), PlanValidationError> {
        if plan.install_contract_id != EMBEDDED_TARGET_CONTRACT_ID
            || plan.install_contract_hash != EMBEDDED_TARGET_CONTRACT_SHA256
        {
            return Err(PlanValidationError::new(
                "install_contract_unsupported",
                "plan contract identity differs from embedded authority",
            ));
        }
        Ok(())
    }

    fn validate_plan_mode(&self, mode: &str) -> Result<(), PlanValidationError> {
        if !self
            .contract
            .plan_modes
            .iter()
            .any(|allowed| allowed == mode)
        {
            return Err(PlanValidationError::new(
                "invalid_plan_mode",
                format!("unsupported plan mode: {mode}"),
            ));
        }
        Ok(())
    }

    fn validate_provided_surfaces(
        &self,
        plan: &InstallPlan,
        targets: &BTreeSet<String>,
    ) -> Result<BTreeSet<PlanRuntimeSurface>, PlanValidationError> {
        let mut surfaces = BTreeSet::new();
        for surface in &plan.runtime_surfaces {
            validate_relative_path(&surface.path)?;
            validate_relative_path(&surface.source)?;
            if !targets.contains(&surface.target) {
                return Err(PlanValidationError::new(
                    "runtime_surface_mismatch",
                    format!("surface target is not selected: {}", surface.target),
                ));
            }
            if !surfaces.insert(surface.clone()) {
                return Err(PlanValidationError::new(
                    "runtime_surface_mismatch",
                    format!(
                        "duplicate runtime surface: {}:{}",
                        surface.target, surface.path
                    ),
                ));
            }
        }
        Ok(surfaces)
    }

    fn validate_artifact_components(
        &self,
        artifact: &PlanArtifact,
        selected: &BTreeSet<String>,
    ) -> Result<(), PlanValidationError> {
        if !selected.contains(&artifact.component_id) {
            return Err(PlanValidationError::new(
                "component_closure_mismatch",
                format!(
                    "artifact component is not selected: {}",
                    artifact.component_id
                ),
            ));
        }
        if artifact.component_ids.is_empty() {
            return Ok(());
        }
        let ids = unique_set(&artifact.component_ids, "duplicate_component")?;
        if !ids.contains(&artifact.component_id) || !ids.is_subset(selected) {
            return Err(PlanValidationError::new(
                "component_closure_mismatch",
                format!(
                    "artifact component membership is invalid: {}",
                    artifact.destination
                ),
            ));
        }
        Ok(())
    }

    fn validate_source(&self, artifact: &PlanArtifact) -> Result<(), PlanValidationError> {
        let expected = format!("dist/{}/{}", artifact.target, artifact.destination);
        if artifact.source != expected
            && !self.contract.source_is_equivalent(
                &artifact.target,
                &artifact.destination,
                &artifact.source,
            )
        {
            return Err(PlanValidationError::new(
                "source_mismatch",
                format!(
                    "artifact source does not equal canonical destination source: {}",
                    artifact.source
                ),
            ));
        }
        Ok(())
    }

    fn validate_merge_contract(&self, artifact: &PlanArtifact) -> Result<(), PlanValidationError> {
        match self
            .contract
            .merge_destination(&artifact.target, &artifact.destination)
        {
            Some(expected) if merge_fields_match(artifact, expected) => Ok(()),
            None if artifact.merge_strategy.is_none()
                && artifact.begin_marker.is_none()
                && artifact.end_marker.is_none()
                && artifact.json_merge_key.is_none()
                && artifact.toml_merge_key.is_none() =>
            {
                Ok(())
            }
            _ => Err(PlanValidationError::new(
                "merge_contract_mismatch",
                format!("merge contract differs for {}", artifact.destination),
            )),
        }
    }

    fn validate_file_mode(
        &self,
        artifact: &PlanArtifact,
        required: bool,
    ) -> Result<(), PlanValidationError> {
        let Some(mode) = artifact.mode else {
            if required {
                return Err(PlanValidationError::new(
                    "source_mode_missing",
                    format!("apply artifact mode is missing: {}", artifact.destination),
                ));
            }
            return Ok(());
        };
        let policy = &self.contract.file_mode_policy;
        if mode < policy.minimum || mode > policy.maximum {
            return Err(PlanValidationError::new(
                "invalid_file_mode",
                format!("file mode is outside 0000..0777: {mode:o}"),
            ));
        }
        if mode & policy.required_group_or_other_read_mask == 0 {
            return Err(PlanValidationError::new(
                "secret_like_file_mode",
                format!("owner-only generated file mode is rejected: {mode:o}"),
            ));
        }
        Ok(())
    }

    fn validate_source_hash(
        &self,
        artifact: &PlanArtifact,
        required: bool,
    ) -> Result<(), PlanValidationError> {
        if required && artifact.source_sha256.is_none() {
            return Err(PlanValidationError::new(
                "source_hash_missing",
                format!(
                    "apply artifact source hash is missing: {}",
                    artifact.destination
                ),
            ));
        }
        if artifact.source_sha256.as_ref().is_some_and(|hash| {
            hash.len() != 64
                || !hash
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        }) {
            return Err(PlanValidationError::new(
                "invalid_source_hash",
                format!("invalid source hash for {}", artifact.destination),
            ));
        }
        Ok(())
    }
}

fn unique_set(
    values: &[String],
    duplicate_code: &'static str,
) -> Result<BTreeSet<String>, PlanValidationError> {
    let set: BTreeSet<_> = values.iter().cloned().collect();
    if set.len() != values.len() {
        return Err(PlanValidationError::new(
            duplicate_code,
            "duplicate values are not allowed",
        ));
    }
    Ok(set)
}

fn validate_relative_path(path: &str) -> Result<(), PlanValidationError> {
    let drive_like = path.as_bytes().get(1).is_some_and(|byte| *byte == b':');
    if path.is_empty()
        || path.starts_with('/')
        || path.starts_with('~')
        || drive_like
        || path.contains('\\')
        || path.contains('\0')
        || path
            .split('/')
            .any(|segment| segment.is_empty() || segment == "." || segment == "..")
    {
        return Err(PlanValidationError::new(
            "invalid_path",
            format!("path is not canonical root-relative POSIX: {path}"),
        ));
    }
    Ok(())
}

fn merge_fields_match(artifact: &PlanArtifact, expected: &MergeDestinationContract) -> bool {
    if artifact.merge_strategy.as_deref() != Some(expected.strategy.as_str()) {
        return false;
    }
    match expected.strategy.as_str() {
        "managed-block" => {
            artifact.begin_marker == expected.begin_marker
                && artifact.end_marker == expected.end_marker
                && artifact.json_merge_key.is_none()
                && artifact.toml_merge_key.is_none()
        }
        "json-deep-merge" => {
            artifact.json_merge_key == expected.merge_key
                && artifact.toml_merge_key.is_none()
                && artifact.begin_marker.is_none()
                && artifact.end_marker.is_none()
        }
        "toml-agents-merge" => {
            artifact.toml_merge_key == expected.merge_key
                && artifact.json_merge_key.is_none()
                && artifact.begin_marker.is_none()
                && artifact.end_marker.is_none()
        }
        _ => false,
    }
}

fn semantic_fingerprint(plan: &InstallPlan) -> Result<String, PlanValidationError> {
    let projection = semantic_projection(plan)?;
    hash_serializable(&projection)
}

fn semantic_projection(plan: &InstallPlan) -> Result<Value, PlanValidationError> {
    let mut normalized = plan.clone();
    normalized.mode.clear();
    normalized.materialization = None;
    normalized.targets.sort();
    normalized.components.sort();
    normalized.runtime_surfaces.sort();
    for artifact in &mut normalized.artifacts {
        artifact.component_ids.sort();
        artifact.mode = None;
        artifact.source_sha256 = None;
    }
    normalized.artifacts.sort_by(|left, right| {
        (
            left.target.as_str(),
            left.destination.as_str(),
            left.component_id.as_str(),
        )
            .cmp(&(
                right.target.as_str(),
                right.destination.as_str(),
                right.component_id.as_str(),
            ))
    });
    serde_json::to_value(normalized).map_err(|error| {
        PlanValidationError::new("semantic_serialization_failed", error.to_string())
    })
}

fn hash_serializable(value: &Value) -> Result<String, PlanValidationError> {
    let bytes = serde_json::to_vec(value).map_err(|error| {
        PlanValidationError::new("semantic_serialization_failed", error.to_string())
    })?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}
