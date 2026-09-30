mod context;
mod projector;

pub use crate::projection::{normalized_relative_locator, DestinationKey, DestinationScope};
pub use context::CorrelationContext;
pub use projector::{project, QualifiedArtifactProjector};

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct ArtifactJoinKey {
    pub adapter_id: String,
    pub tool_id: String,
    pub surface_id: String,
    pub scope: DestinationScope,
    pub normalized_target_locator: String,
    pub config_entry_locator: Option<String>,
}

impl ArtifactJoinKey {
    pub fn from_local(adapter_id: &str, destination: &DestinationKey) -> Self {
        Self {
            adapter_id: adapter_id.to_string(),
            tool_id: destination.tool_id.clone(),
            surface_id: destination.surface_id.clone(),
            scope: destination.scope,
            normalized_target_locator: destination.normalized_relative_locator.clone(),
            config_entry_locator: destination.config_entry_locator.clone(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct QualifiedArtifactIssue {
    pub code: String,
    pub safe_reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct QualifiedArtifactRecord {
    pub artifact_record_ref: String,
    pub component_id: String,
    pub adapter_id: String,
    pub adapter_version: String,
    pub tool_id: String,
    pub surface_id: String,
    pub scope: DestinationScope,
    pub normalized_target_locator: String,
    pub config_entry_locator: Option<String>,
    pub content_sha256: String,
}

impl QualifiedArtifactRecord {
    pub fn join_key(&self) -> ArtifactJoinKey {
        ArtifactJoinKey {
            adapter_id: self.adapter_id.clone(),
            tool_id: self.tool_id.clone(),
            surface_id: self.surface_id.clone(),
            scope: self.scope,
            normalized_target_locator: self.normalized_target_locator.clone(),
            config_entry_locator: self.config_entry_locator.clone(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum CorrelationAvailability {
    Available,
    Unavailable { safe_reason: String },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct QualifiedArtifactProjection {
    pub artifact_projection_id: String,
    pub projector_version: String,
    pub sot_snapshot_id: String,
    pub source_revision: String,
    pub adapter_set_revision: String,
    pub availability: CorrelationAvailability,
    pub records: Vec<QualifiedArtifactRecord>,
    pub issues: Vec<QualifiedArtifactIssue>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum CorrelationState {
    Uncorrelated,
    Verified {
        component_id: String,
        method: String,
        artifact_record_ref: String,
        install_evidence_ref: Option<String>,
    },
    Drift {
        component_id: String,
        artifact_record_ref: String,
        expected_sha256: String,
        current_sha256: String,
    },
    Ambiguous {
        candidate_component_ids: Vec<String>,
        safe_reason: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InstanceCorrelation {
    pub instance_id: String,
    pub correlation: CorrelationState,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CorrelationProjection {
    pub projection_id: String,
    pub projector_version: String,
    pub local_snapshot_id: String,
    pub sot_snapshot_id: Option<String>,
    pub artifact_projection_id: Option<String>,
    pub install_evidence_id: Option<String>,
    pub availability: CorrelationAvailability,
    pub correlations: Vec<InstanceCorrelation>,
}

impl CorrelationProjection {
    pub fn correlation_for(&self, instance_id: &str) -> CorrelationState {
        self.correlations
            .iter()
            .find(|record| record.instance_id == instance_id)
            .map(|record| record.correlation.clone())
            .unwrap_or(CorrelationState::Uncorrelated)
    }

    pub fn verified_instance_ids(&self, component_id: &str) -> BTreeSet<String> {
        self.correlations
            .iter()
            .filter_map(|record| match &record.correlation {
                CorrelationState::Verified {
                    component_id: verified_component_id,
                    ..
                } if verified_component_id == component_id => Some(record.instance_id.clone()),
                _ => None,
            })
            .collect()
    }
}
