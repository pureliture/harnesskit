use std::sync::Mutex;

use crate::contexts::install::InstallEvidenceSnapshot;
use crate::contexts::local::LocalSnapshot;

use super::{CorrelationProjection, QualifiedArtifactProjection};

#[derive(Default)]
pub struct CorrelationContext {
    active: Mutex<Option<CorrelationProjection>>,
}

impl CorrelationContext {
    pub fn project(
        &self,
        local: &LocalSnapshot,
        artifacts: &QualifiedArtifactProjection,
        evidence: Option<&InstallEvidenceSnapshot>,
    ) -> Result<CorrelationProjection, String> {
        let projection = super::projector::project(local, artifacts, evidence)?;
        let mut active = self
            .active
            .lock()
            .map_err(|_| "correlation_state_unavailable".to_string())?;
        if let Some(current) = active.as_ref() {
            if current.projection_id == projection.projection_id {
                return Ok(current.clone());
            }
        }
        *active = Some(projection.clone());
        Ok(projection)
    }

    pub fn require(
        &self,
        projection_id: &str,
        local_snapshot_id: &str,
    ) -> Result<CorrelationProjection, String> {
        let active = self
            .active
            .lock()
            .map_err(|_| "correlation_state_unavailable".to_string())?;
        match active.as_ref() {
            Some(projection)
                if projection.projection_id == projection_id
                    && projection.local_snapshot_id == local_snapshot_id =>
            {
                Ok(projection.clone())
            }
            _ => Err("projection_stale".to_string()),
        }
    }

    pub fn invalidate(&self) -> Result<(), String> {
        *self
            .active
            .lock()
            .map_err(|_| "correlation_state_unavailable".to_string())? = None;
        Ok(())
    }
}
