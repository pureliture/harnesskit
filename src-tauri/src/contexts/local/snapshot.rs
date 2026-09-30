use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::domain::{
    CoverageRecord, LocalInstance, LocalScanResult, ProjectIgnoreSummary, QualifiedTool, SafeIssue,
    SkippedPath,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalSnapshotStatus {
    Complete,
    Partial,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalSnapshot {
    pub snapshot_id: String,
    pub attempt_id: String,
    pub status: LocalSnapshotStatus,
    pub scan_timestamp: String,
    pub app_version: String,
    pub adapter_set_fingerprint: String,
    pub content_fingerprint: String,
    pub qualified_tools: Vec<QualifiedTool>,
    pub instances: Vec<LocalInstance>,
    pub coverage: Vec<CoverageRecord>,
    pub skipped_paths: Vec<SkippedPath>,
    pub issues: Vec<SafeIssue>,
    #[serde(default)]
    pub project_ignore_summaries: Vec<ProjectIgnoreSummary>,
}

impl LocalSnapshot {
    pub(crate) fn from_result(
        attempt_id: &str,
        status: LocalSnapshotStatus,
        scan_timestamp: String,
        app_version: String,
        adapter_set_fingerprint: String,
        result: LocalScanResult,
    ) -> Result<Self, String> {
        let content_fingerprint = canonical_sha256(&result)?;
        let status_name = match status {
            LocalSnapshotStatus::Complete => "complete",
            LocalSnapshotStatus::Partial => "partial",
        };
        let snapshot_id = tuple_sha256(&[
            b"local-snapshot-v1",
            attempt_id.as_bytes(),
            status_name.as_bytes(),
            content_fingerprint.as_bytes(),
            adapter_set_fingerprint.as_bytes(),
        ]);
        Ok(Self {
            snapshot_id,
            attempt_id: attempt_id.to_string(),
            status,
            scan_timestamp,
            app_version,
            adapter_set_fingerprint,
            content_fingerprint,
            qualified_tools: result.qualified_tools,
            instances: result.instances,
            coverage: result.coverage,
            skipped_paths: result.skipped_paths,
            issues: result.issues,
            project_ignore_summaries: result.project_ignore_summaries,
        })
    }
}

pub(crate) fn canonical_sha256<T: Serialize>(value: &T) -> Result<String, String> {
    serde_json::to_vec(value)
        .map(|bytes| sha256(&bytes))
        .map_err(|_| "local_snapshot_canonicalization_failed".to_string())
}

fn tuple_sha256(parts: &[&[u8]]) -> String {
    let mut digest = Sha256::new();
    for part in parts {
        digest.update(part.len().to_be_bytes());
        digest.update(part);
    }
    digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contexts::local::domain::ProjectIgnoreSummary;

    fn snapshot_with_excluded_count(excluded_path_count: usize) -> LocalSnapshot {
        LocalSnapshot::from_result(
            "attempt",
            LocalSnapshotStatus::Complete,
            "2026-07-18T00:00:00Z".to_string(),
            "test".to_string(),
            "adapter-set".to_string(),
            LocalScanResult {
                project_ignore_summaries: vec![ProjectIgnoreSummary {
                    project_id: "project".to_string(),
                    source_revision: "source-revision".to_string(),
                    rule_count: 1,
                    excluded_path_count,
                }],
                ..LocalScanResult::default()
            },
        )
        .expect("snapshot")
    }

    #[test]
    fn project_ignore_summary_is_snapshot_authority_and_binds_content_fingerprint() {
        let first = snapshot_with_excluded_count(1);
        let second = snapshot_with_excluded_count(2);

        assert_eq!(first.project_ignore_summaries[0].excluded_path_count, 1);
        assert_ne!(first.content_fingerprint, second.content_fingerprint);
    }
}
