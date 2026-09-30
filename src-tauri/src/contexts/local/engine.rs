use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

use serde::Serialize;

use super::adapter::ToolId;
use super::catalog::{AdapterCatalog, CatalogAdapter};
use super::context::{
    LocalScanDiagnostics, LocalScanExecutor, LocalScanExecutorResult, LocalScanProgress,
    LocalScanProgressPort, LocalScanPublication,
};
use super::domain::{
    CoverageRecord, CoverageStatus, LocalScanResult, QualifiedTool, SafeIssue, SurfacePresence,
};
use super::probe::{
    collect_metadata_observations, evaluate_metadata_probe, MetadataProbeFilesystem,
    MetadataProbeObservation, MetadataProbeOutcome,
};
use super::scanner::{surface_progress_coverage_id, LocalScanner};
use super::snapshot::{canonical_sha256, LocalSnapshot, LocalSnapshotStatus};

#[derive(Debug, Clone)]
pub struct ScanEnvironment {
    home: PathBuf,
    app_version: String,
    fixed_scan_timestamp: Option<String>,
    observations: BTreeMap<ToolId, MetadataProbeObservation>,
}

impl ScanEnvironment {
    pub fn new(
        home: PathBuf,
        app_version: impl Into<String>,
        scan_timestamp: impl Into<String>,
        observations: BTreeMap<ToolId, MetadataProbeObservation>,
    ) -> Self {
        Self {
            home,
            app_version: app_version.into(),
            fixed_scan_timestamp: Some(scan_timestamp.into()),
            observations,
        }
    }

    pub fn for_current_macos(
        catalog: &AdapterCatalog,
        home: PathBuf,
        app_version: impl Into<String>,
    ) -> Self {
        let observations = collect_metadata_observations(
            catalog,
            &MetadataProbeFilesystem::for_macos(home.clone()),
        );
        Self {
            home,
            app_version: app_version.into(),
            fixed_scan_timestamp: None,
            observations,
        }
    }

    fn scan_timestamp(&self) -> String {
        self.fixed_scan_timestamp
            .clone()
            .unwrap_or_else(|| chrono::Utc::now().to_rfc3339())
    }
}

pub struct DiscoveryScanExecutor {
    catalog: AdapterCatalog,
    environment: ScanEnvironment,
}

impl DiscoveryScanExecutor {
    pub fn new(catalog: AdapterCatalog, environment: ScanEnvironment) -> Self {
        Self {
            catalog,
            environment,
        }
    }

    pub fn execute_once(&self, attempt_id: &str) -> LocalScanExecutorResult {
        match self.scan(attempt_id, &NoopProgressPort) {
            Ok(result) => result,
            Err(code) => LocalScanExecutorResult::Failed {
                code,
                diagnostics: None,
            },
        }
    }

    fn scan(
        &self,
        attempt_id: &str,
        progress: &dyn LocalScanProgressPort,
    ) -> Result<LocalScanExecutorResult, String> {
        let mut runtime_adapters = Vec::new();
        let mut result = LocalScanResult::default();
        let mut instance_handles = BTreeMap::new();
        let mut project_locations = BTreeMap::new();
        let mut fingerprint_entries = Vec::new();

        for adapter in self.catalog.adapters() {
            let observation = self
                .environment
                .observations
                .get(&adapter.descriptor.tool_id)
                .cloned()
                .unwrap_or(MetadataProbeObservation {
                    install_metadata_present: false,
                    declared_surface_present: true,
                    observed_tool_version: None,
                    observed_schema_digest: None,
                });
            match evaluate_metadata_probe(adapter, &observation) {
                MetadataProbeOutcome::ToolPresent {
                    current_tool_version_or_schema_digest,
                } => {
                    result.qualified_tools.push(qualified_tool(adapter));
                    fingerprint_entries.push(fingerprint_entry(
                        adapter,
                        current_tool_version_or_schema_digest,
                    ));
                    runtime_adapters.push(adapter.clone());
                }
                MetadataProbeOutcome::ToolAbsent => {
                    report_static_surface_progress(adapter, progress);
                    result.qualified_tools.push(qualified_tool(adapter));
                    fingerprint_entries.push(fingerprint_entry(adapter, "absent".to_string()));
                    add_absent_coverage(adapter, &mut result);
                }
                MetadataProbeOutcome::Unavailable { reason } => {
                    report_static_surface_progress(adapter, progress);
                    add_unavailable_coverage(adapter, reason, &mut result);
                }
            }
        }

        if !runtime_adapters.is_empty() {
            let mut report = |scan_progress: &LocalScanProgress| progress.report(scan_progress);
            let scanned = LocalScanner::scan_with_handles_and_progress(
                &self.environment.home,
                &runtime_adapters,
                &mut report,
            )?;
            result.instances.extend(scanned.result.instances);
            result.coverage.extend(scanned.result.coverage);
            result.skipped_paths.extend(scanned.result.skipped_paths);
            result.issues.extend(scanned.result.issues);
            result
                .project_ignore_summaries
                .extend(scanned.result.project_ignore_summaries);
            instance_handles.extend(scanned.instance_handles);
            for (project_id, location) in scanned.project_locations {
                if let Some(existing) = project_locations.get(&project_id) {
                    if existing != &location {
                        return Err("project_identity_collision".to_string());
                    }
                } else {
                    project_locations.insert(project_id, location);
                }
            }
        }
        normalize(&mut result);
        validate_normalized_result(&result)?;
        fingerprint_entries.sort_by(|left, right| left.adapter_id.cmp(&right.adapter_id));
        let adapter_set_fingerprint = canonical_sha256(&fingerprint_entries)?;

        let publishable = result
            .coverage
            .iter()
            .any(|coverage| coverage.status != CoverageStatus::Failed);
        if !publishable {
            return Ok(LocalScanExecutorResult::Failed {
                code: "no_publishable_coverage".to_string(),
                diagnostics: Some(LocalScanDiagnostics {
                    app_version: Some(self.environment.app_version.clone()),
                    adapter_set_fingerprint: Some(adapter_set_fingerprint),
                    coverage: result.coverage,
                    skipped_paths: result.skipped_paths,
                    issues: result.issues,
                }),
            });
        }

        let partial = !result.issues.is_empty()
            || result
                .coverage
                .iter()
                .any(|coverage| coverage.status != CoverageStatus::Complete);
        let status = if partial {
            LocalSnapshotStatus::Partial
        } else {
            LocalSnapshotStatus::Complete
        };
        let issue_codes = result
            .issues
            .iter()
            .map(|issue| issue.code.clone())
            .chain(
                result
                    .coverage
                    .iter()
                    .flat_map(|coverage| coverage.issue_codes.iter().cloned()),
            )
            .collect::<Vec<_>>();
        let snapshot = LocalSnapshot::from_result(
            attempt_id,
            status,
            self.environment.scan_timestamp(),
            self.environment.app_version.clone(),
            adapter_set_fingerprint,
            result,
        )?;
        let publication = LocalScanPublication {
            instance_handle_ids: snapshot
                .instances
                .iter()
                .map(|instance| instance.instance_id.clone())
                .collect(),
            instance_handles,
            project_locations,
            snapshot,
        };
        Ok(match status {
            LocalSnapshotStatus::Complete => LocalScanExecutorResult::Complete(publication),
            LocalSnapshotStatus::Partial => LocalScanExecutorResult::Partial {
                publication,
                issue_codes,
            },
        })
    }
}

impl LocalScanExecutor for DiscoveryScanExecutor {
    fn execute(
        &self,
        attempt_id: &str,
        progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult {
        match self.scan(attempt_id, progress) {
            Ok(result) => result,
            Err(code) => LocalScanExecutorResult::Failed {
                code,
                diagnostics: None,
            },
        }
    }
}

struct NoopProgressPort;

impl LocalScanProgressPort for NoopProgressPort {
    fn report(&self, _progress: &LocalScanProgress) {}
}

fn report_static_surface_progress(adapter: &CatalogAdapter, progress: &dyn LocalScanProgressPort) {
    for surface in &adapter.descriptor.surfaces {
        progress.report(&LocalScanProgress {
            adapter_id: adapter.adapter_id.clone(),
            surface_id: surface.surface_id.clone(),
            coverage_id: surface_progress_coverage_id(
                &adapter.adapter_id,
                &adapter.descriptor.adapter_version,
                &surface.surface_id,
            ),
            item_count: 0,
        });
    }
}

#[derive(Debug, Serialize)]
struct AdapterFingerprintEntry {
    adapter_id: String,
    adapter_version: String,
    implementation_version: u32,
    locator_version: u32,
    descriptor_canonical_sha256: String,
    qualification_record_sha256: String,
    current_tool_version_or_schema_digest: String,
}

fn fingerprint_entry(
    adapter: &CatalogAdapter,
    current_tool_version_or_schema_digest: String,
) -> AdapterFingerprintEntry {
    AdapterFingerprintEntry {
        adapter_id: adapter.adapter_id.clone(),
        adapter_version: adapter.descriptor.adapter_version.clone(),
        implementation_version: adapter.descriptor.implementation_version,
        locator_version: adapter.descriptor.locator_version,
        descriptor_canonical_sha256: adapter.descriptor_canonical_sha256.clone(),
        qualification_record_sha256: adapter.qualification_record_sha256.clone(),
        current_tool_version_or_schema_digest,
    }
}

fn add_absent_coverage(adapter: &CatalogAdapter, result: &mut LocalScanResult) {
    for surface in &adapter.descriptor.surfaces {
        result.coverage.push(CoverageRecord {
            adapter_id: adapter.adapter_id.clone(),
            adapter_version: adapter.descriptor.adapter_version.clone(),
            surface_id: surface.surface_id.clone(),
            root_id: None,
            scope: surface.scope,
            project_id: None,
            status: CoverageStatus::Complete,
            presence: SurfacePresence::NotPresent,
            item_count: 0,
            skipped_count: 0,
            issue_codes: Vec::new(),
        });
    }
}

fn qualified_tool(adapter: &CatalogAdapter) -> QualifiedTool {
    QualifiedTool {
        tool_id: adapter.descriptor.tool_id,
        adapter_id: adapter.descriptor.adapter_id.clone(),
        adapter_version: adapter.descriptor.adapter_version.clone(),
    }
}

fn add_unavailable_coverage(
    adapter: &CatalogAdapter,
    reason: &'static str,
    result: &mut LocalScanResult,
) {
    let code = format!("adapter_{reason}");
    result.issues.push(SafeIssue {
        project_id: None,
        code: code.clone(),
        safe_message: "A discovery adapter could not qualify the installed tool version"
            .to_string(),
        safe_relative_locator: None,
    });
    for surface in &adapter.descriptor.surfaces {
        result.coverage.push(CoverageRecord {
            adapter_id: adapter.adapter_id.clone(),
            adapter_version: adapter.descriptor.adapter_version.clone(),
            surface_id: surface.surface_id.clone(),
            root_id: None,
            scope: surface.scope,
            project_id: None,
            status: CoverageStatus::Failed,
            presence: SurfacePresence::Unknown,
            item_count: 0,
            skipped_count: 0,
            issue_codes: vec![code.clone()],
        });
    }
}

fn normalize(result: &mut LocalScanResult) {
    result.qualified_tools.sort();
    result.qualified_tools.dedup();
    result.project_ignore_summaries.sort_by(|left, right| {
        left.project_id
            .cmp(&right.project_id)
            .then_with(|| left.source_revision.cmp(&right.source_revision))
    });
    result.project_ignore_summaries.dedup();
    result
        .instances
        .sort_by(|left, right| left.instance_id.cmp(&right.instance_id));
    result.coverage.sort_by(|left, right| {
        left.adapter_id
            .cmp(&right.adapter_id)
            .then_with(|| left.surface_id.cmp(&right.surface_id))
            .then_with(|| left.root_id.cmp(&right.root_id))
    });
    result.skipped_paths.sort_by(|left, right| {
        left.root_id
            .cmp(&right.root_id)
            .then_with(|| left.safe_relative_locator.cmp(&right.safe_relative_locator))
    });
    result.issues.sort_by(|left, right| {
        left.code
            .cmp(&right.code)
            .then_with(|| left.safe_relative_locator.cmp(&right.safe_relative_locator))
    });
}

fn validate_normalized_result(result: &LocalScanResult) -> Result<(), String> {
    let mut identities = BTreeSet::new();
    for instance in &result.instances {
        if !identities.insert(&instance.instance_id) {
            return Err("identity_collision".to_string());
        }
    }
    if result.coverage.iter().any(|coverage| {
        coverage.status == CoverageStatus::Complete && coverage.presence == SurfacePresence::Unknown
    }) {
        return Err("coverage_presence_invariant".to_string());
    }
    let mut project_ids = BTreeSet::new();
    if result
        .project_ignore_summaries
        .iter()
        .any(|summary| !project_ids.insert(&summary.project_id))
    {
        return Err("project_ignore_summary_collision".to_string());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contexts::local::domain::{ParseState, Scope};
    use crate::contexts::local::SurfaceKind;

    fn instance(instance_id: &str, locator: &str) -> super::super::domain::LocalInstance {
        super::super::domain::LocalInstance {
            instance_id: instance_id.to_string(),
            root_id: "root".to_string(),
            adapter_id: "adapter".to_string(),
            adapter_version: "1".to_string(),
            tool_id: ToolId::Codex,
            surface_id: "surface".to_string(),
            scope: Scope::User,
            project_id: None,
            stable_source_locator: locator.to_string(),
            correlation_key: None,
            kind: SurfaceKind::Skill,
            name: None,
            description: None,
            description_source: None,
            settings: Vec::new(),
            content_hash: None,
            size: None,
            modified_unix_millis: None,
            parse_state: ParseState::Parsed,
            issue_codes: Vec::new(),
        }
    }

    #[test]
    fn duplicate_instance_identity_is_a_failed_invariant_not_a_silent_dedupe() {
        let result = LocalScanResult {
            instances: vec![instance("same", "first"), instance("same", "second")],
            ..LocalScanResult::default()
        };

        assert_eq!(
            validate_normalized_result(&result),
            Err("identity_collision".to_string())
        );
    }

    #[test]
    fn conflicting_project_ignore_summaries_fail_instead_of_selecting_a_revision() {
        let result = LocalScanResult {
            project_ignore_summaries: vec![
                super::super::domain::ProjectIgnoreSummary {
                    project_id: "project".to_string(),
                    source_revision: "first".to_string(),
                    rule_count: 1,
                    excluded_path_count: 1,
                },
                super::super::domain::ProjectIgnoreSummary {
                    project_id: "project".to_string(),
                    source_revision: "second".to_string(),
                    rule_count: 2,
                    excluded_path_count: 2,
                },
            ],
            ..LocalScanResult::default()
        };

        assert_eq!(
            validate_normalized_result(&result),
            Err("project_ignore_summary_collision".to_string())
        );
    }
}
