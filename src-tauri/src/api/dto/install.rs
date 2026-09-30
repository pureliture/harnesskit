//! Typed Install preview/apply DTOs without backend path handles or source bodies.

use serde::{Deserialize, Serialize};

use crate::contexts::install::{
    DestinationApplyState, DestinationVerifyState, InstallApplyOutcome, InstallOperationStatus,
    InstallPreview,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InstallScopeDto {
    User,
    Project,
}

impl InstallScopeDto {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Project => "project",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct InstallPreviewRequestDto {
    pub sot_snapshot_id: String,
    pub profile_id: String,
    pub scope: InstallScopeDto,
    pub target_root: String,
    pub target_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallArtifactPreviewDto {
    pub component_id: String,
    pub component_ids: Vec<String>,
    pub target: String,
    pub destination: String,
    pub merge_strategy: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallRuntimeGateDto {
    pub gate_id: String,
    pub target: String,
    pub safe_reason: String,
    pub required_before_apply: bool,
    pub required_before_runtime: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallRequiredApprovalsDto {
    pub overwrite: bool,
    pub runtime_hooks: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PreviewInstallResponseDto {
    pub preview_id: String,
    pub fingerprint: String,
    pub sot_snapshot_id: String,
    pub profile_id: String,
    pub scope: InstallScopeDto,
    pub target_root: String,
    pub targets: Vec<String>,
    pub components: Vec<String>,
    pub artifacts: Vec<InstallArtifactPreviewDto>,
    pub skipped_writes: Vec<String>,
    pub warnings: Vec<String>,
    pub runtime_gates: Vec<InstallRuntimeGateDto>,
    pub non_atomic_boundary: bool,
    pub required_approvals: InstallRequiredApprovalsDto,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ApplyInstallApprovalsDto {
    pub confirmed: bool,
    pub semantic_fingerprint: String,
    pub overwrite: bool,
    pub allow_runtime_hooks: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InstallOperationStatusDto {
    Complete,
    Partial,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InstallApplyStateDto {
    Changed,
    Unchanged,
    Skipped,
    Failed,
    NotAttempted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InstallVerifyStateDto {
    Verified,
    Failed,
    NotApplied,
    NotAttempted,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallDestinationOutcomeDto {
    pub target: String,
    pub destination: String,
    pub apply_state: InstallApplyStateDto,
    pub verify_state: InstallVerifyStateDto,
    pub code: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApplyInstallResponseDto {
    pub operation_id: String,
    pub status: InstallOperationStatusDto,
    pub destinations: Vec<InstallDestinationOutcomeDto>,
    pub install_evidence_id: Option<String>,
}

impl From<InstallPreview> for PreviewInstallResponseDto {
    fn from(preview: InstallPreview) -> Self {
        let scope = match preview.scope.as_str() {
            "user" => InstallScopeDto::User,
            "project" => InstallScopeDto::Project,
            _ => unreachable!("validated install preview scope"),
        };
        Self {
            preview_id: preview.preview_id,
            fingerprint: preview.fingerprint,
            sot_snapshot_id: preview.sot_snapshot_id,
            profile_id: preview.profile_id,
            scope,
            target_root: preview.target_root,
            targets: preview.targets,
            components: preview.components,
            artifacts: preview
                .artifacts
                .into_iter()
                .map(|artifact| InstallArtifactPreviewDto {
                    component_id: artifact.component_id,
                    component_ids: artifact.component_ids,
                    target: artifact.target,
                    destination: artifact.destination,
                    merge_strategy: artifact.merge_strategy,
                })
                .collect(),
            skipped_writes: preview.skipped_writes,
            warnings: preview.warnings,
            runtime_gates: preview
                .runtime_gates
                .into_iter()
                .map(|gate| InstallRuntimeGateDto {
                    gate_id: gate.gate_id,
                    target: gate.target,
                    safe_reason: gate.safe_reason,
                    required_before_apply: gate.required_before_apply,
                    required_before_runtime: gate.required_before_runtime,
                })
                .collect(),
            non_atomic_boundary: preview.non_atomic_boundary,
            required_approvals: InstallRequiredApprovalsDto {
                overwrite: preview.required_approvals.overwrite,
                runtime_hooks: preview.required_approvals.runtime_hooks,
            },
        }
    }
}

impl From<InstallApplyOutcome> for ApplyInstallResponseDto {
    fn from(outcome: InstallApplyOutcome) -> Self {
        Self {
            operation_id: outcome.operation_id,
            status: match outcome.status {
                InstallOperationStatus::Complete => InstallOperationStatusDto::Complete,
                InstallOperationStatus::Partial => InstallOperationStatusDto::Partial,
                InstallOperationStatus::Failed => InstallOperationStatusDto::Failed,
            },
            destinations: outcome
                .destinations
                .into_iter()
                .map(|destination| InstallDestinationOutcomeDto {
                    target: destination.target,
                    destination: destination.destination,
                    apply_state: match destination.apply_state {
                        DestinationApplyState::Applied
                        | DestinationApplyState::AppliedWithWarning => {
                            InstallApplyStateDto::Changed
                        }
                        DestinationApplyState::Unchanged => InstallApplyStateDto::Unchanged,
                        DestinationApplyState::Failed => InstallApplyStateDto::Failed,
                        DestinationApplyState::NotAttempted => InstallApplyStateDto::NotAttempted,
                    },
                    verify_state: match destination.verify_state {
                        DestinationVerifyState::Verified => InstallVerifyStateDto::Verified,
                        DestinationVerifyState::Failed => InstallVerifyStateDto::Failed,
                        DestinationVerifyState::NotApplied => InstallVerifyStateDto::NotApplied,
                        DestinationVerifyState::NotAttempted => InstallVerifyStateDto::NotAttempted,
                    },
                    code: destination.code,
                })
                .collect(),
            install_evidence_id: outcome.install_evidence_id,
        }
    }
}
