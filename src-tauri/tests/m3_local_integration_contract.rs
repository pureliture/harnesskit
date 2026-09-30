use std::collections::BTreeMap;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use harness_desktop_lib::contexts::local::{
    AdapterCatalog, DiscoveryScanExecutor, LocalAttemptState, LocalContext, LocalScanExecutor,
    LocalScanExecutorResult, LocalScanProgress, LocalScanProgressPort, LocalSnapshotStatus,
    MetadataProbeObservation, ScanEnvironment, StartLocalScanOutcome, ToolId,
};
use tempfile::tempdir;

fn exact_observations(
    catalog: &AdapterCatalog,
    surface_present: bool,
) -> BTreeMap<ToolId, MetadataProbeObservation> {
    catalog
        .adapters()
        .iter()
        .map(|adapter| {
            (
                adapter.descriptor.tool_id,
                MetadataProbeObservation {
                    install_metadata_present: true,
                    declared_surface_present: surface_present,
                    observed_tool_version: Some(
                        adapter.descriptor.supported_tool_versions.value.clone(),
                    ),
                    observed_schema_digest: None,
                },
            )
        })
        .collect()
}

fn wait_for_terminal(
    context: &LocalContext,
) -> harness_desktop_lib::contexts::local::LocalSessionState {
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let state = context.state();
        if state.latest_terminal_report.is_some() {
            return state;
        }
        assert!(Instant::now() < deadline, "Local scan terminal timeout");
        std::thread::yield_now();
    }
}

#[derive(Default)]
struct RecordingProgressPort(Mutex<Vec<LocalScanProgress>>);

impl LocalScanProgressPort for RecordingProgressPort {
    fn report(&self, progress: &LocalScanProgress) {
        self.0.lock().unwrap().push(progress.clone());
    }
}

#[test]
fn all_absent_tools_publish_a_complete_empty_snapshot_with_truthful_coverage() {
    let home = tempdir().unwrap();
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let observations = catalog
        .adapters()
        .iter()
        .map(|adapter| {
            (
                adapter.descriptor.tool_id,
                MetadataProbeObservation {
                    install_metadata_present: false,
                    declared_surface_present: false,
                    observed_tool_version: None,
                    observed_schema_digest: None,
                },
            )
        })
        .collect();
    let executor = DiscoveryScanExecutor::new(
        catalog,
        ScanEnvironment::new(
            home.path().to_path_buf(),
            "0.1.0-test",
            "2026-07-12T00:00:00Z",
            observations,
        ),
    );

    let progress = RecordingProgressPort::default();
    let LocalScanExecutorResult::Complete(publication) =
        executor.execute("attempt-absent", &progress)
    else {
        panic!("all ToolAbsent adapters must produce Complete/NotPresent coverage");
    };
    let snapshot = publication.snapshot;
    let progress = progress.0.into_inner().unwrap();

    assert_eq!(snapshot.status, LocalSnapshotStatus::Complete);
    assert!(snapshot.instances.is_empty());
    assert_eq!(snapshot.qualified_tools.len(), 5);
    assert_eq!(
        snapshot
            .qualified_tools
            .iter()
            .map(|tool| tool.tool_id)
            .collect::<std::collections::BTreeSet<_>>(),
        [
            ToolId::Codex,
            ToolId::ClaudeCode,
            ToolId::Antigravity,
            ToolId::AntigravityCli,
            ToolId::Hermes,
        ]
        .into_iter()
        .collect()
    );
    assert_eq!(snapshot.coverage.len(), 10);
    assert_eq!(progress.len(), snapshot.coverage.len());
    assert_eq!(
        progress
            .iter()
            .map(|record| record.coverage_id.as_str())
            .collect::<std::collections::BTreeSet<_>>()
            .len(),
        progress.len()
    );
    assert!(progress.iter().all(|record| record.item_count == 0));
    assert!(snapshot.coverage.iter().all(|coverage| {
        coverage.status == harness_desktop_lib::contexts::local::CoverageStatus::Complete
            && coverage.presence
                == harness_desktop_lib::contexts::local::SurfacePresence::NotPresent
    }));
    assert_eq!(snapshot.adapter_set_fingerprint.len(), 64);
    assert_eq!(snapshot.content_fingerprint.len(), 64);
    assert_eq!(snapshot.snapshot_id.len(), 64);
}

#[test]
fn malformed_fixture_publishes_partial_snapshot_without_leaking_source_bodies() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let home = root.join("tests/fixtures/discovery/scan/home");
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let observations = exact_observations(&catalog, true);
    let executor = DiscoveryScanExecutor::new(
        catalog,
        ScanEnvironment::new(home, "0.1.0-test", "2026-07-12T00:00:00Z", observations),
    );

    let LocalScanExecutorResult::Partial {
        publication,
        issue_codes,
    } = executor.execute_once("attempt-partial")
    else {
        panic!("malformed publishable inventory must be Partial");
    };
    assert!(!issue_codes.is_empty());
    assert_eq!(publication.snapshot.status, LocalSnapshotStatus::Partial);
    assert!(!publication.snapshot.instances.is_empty());
    assert!(publication.snapshot.coverage.iter().any(|coverage| {
        coverage.status == harness_desktop_lib::contexts::local::CoverageStatus::Partial
            && coverage
                .issue_codes
                .iter()
                .any(|code| code == "malformed_document")
    }));
    let serialized = serde_json::to_string(&publication.snapshot).unwrap();
    for forbidden in [
        "PRIVATE_PROMPT_BODY_MUST_NOT_BE_INDEXED",
        "RAW_HOOK_COMMAND_MUST_NOT_BE_INDEXED",
        "SUPER_SECRET_VALUE",
        "WORKFLOW_BODY_MUST_NOT_BE_INDEXED",
    ] {
        assert!(!serialized.contains(forbidden));
    }
}

#[test]
fn project_ignore_summary_survives_discovery_engine_snapshot_publication() {
    let home = tempdir().unwrap();
    let project = home.path().join("project");
    let keep = project.join(".agents/skills/keep/SKILL.md");
    let ignored = project.join(".agents/skills/ignored/SKILL.md");
    std::fs::create_dir_all(keep.parent().unwrap()).unwrap();
    std::fs::create_dir_all(ignored.parent().unwrap()).unwrap();
    std::fs::write(&keep, "---\nname: Keep\ndescription: Keep\n---\n").unwrap();
    std::fs::write(&ignored, "---\nname: Ignored\ndescription: Ignored\n---\n").unwrap();
    std::fs::write(
        project.join(".harnesskitignore"),
        ".agents/skills/ignored/\n",
    )
    .unwrap();
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let observations = exact_observations(&catalog, true);
    let executor = DiscoveryScanExecutor::new(
        catalog,
        ScanEnvironment::new(
            home.path().to_path_buf(),
            "0.1.0-test",
            "2026-07-18T00:00:00Z",
            observations,
        ),
    );

    let publication = match executor.execute_once("attempt-project-ignore") {
        LocalScanExecutorResult::Complete(publication) => publication,
        LocalScanExecutorResult::Partial { publication, .. } => publication,
        LocalScanExecutorResult::Failed { code, .. } => panic!("unexpected failure: {code}"),
    };
    let summary = publication
        .snapshot
        .project_ignore_summaries
        .first()
        .expect("published project ignore summary");

    assert_eq!(summary.rule_count, 1);
    assert_eq!(summary.excluded_path_count, 1);
    assert_eq!(summary.source_revision.len(), 64);
    assert!(!publication
        .snapshot
        .instances
        .iter()
        .any(|instance| instance.name.as_deref() == Some("Ignored")));
    let serialized = serde_json::to_string(&publication.snapshot).unwrap();
    assert!(!serialized.contains(".agents/skills/ignored"));
    assert!(!serialized.contains(".harnesskitignore"));
}

#[test]
fn all_runtime_mismatches_fail_without_publishing_a_snapshot() {
    let home = tempdir().unwrap();
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let observations = catalog
        .adapters()
        .iter()
        .map(|adapter| {
            (
                adapter.descriptor.tool_id,
                MetadataProbeObservation {
                    install_metadata_present: true,
                    declared_surface_present: true,
                    observed_tool_version: Some("unsupported-version".to_string()),
                    observed_schema_digest: None,
                },
            )
        })
        .collect();
    let executor = DiscoveryScanExecutor::new(
        catalog,
        ScanEnvironment::new(
            home.path().to_path_buf(),
            "0.1.0-test",
            "2026-07-12T00:00:00Z",
            observations,
        ),
    );

    let LocalScanExecutorResult::Failed {
        code,
        diagnostics: Some(diagnostics),
    } = executor.execute_once("attempt-failed")
    else {
        panic!("all mismatches must retain a failed report without a snapshot");
    };
    assert_eq!(code, "no_publishable_coverage");
    assert_eq!(diagnostics.coverage.len(), 10);
    assert!(diagnostics.coverage.iter().all(|coverage| {
        coverage.status == harness_desktop_lib::contexts::local::CoverageStatus::Failed
            && coverage.presence == harness_desktop_lib::contexts::local::SurfacePresence::Unknown
    }));
}

#[test]
fn real_executor_is_retained_by_the_session_store_and_old_ids_fail_closed() {
    let home = tempdir().unwrap();
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let observations = catalog
        .adapters()
        .iter()
        .map(|adapter| {
            (
                adapter.descriptor.tool_id,
                MetadataProbeObservation {
                    install_metadata_present: false,
                    declared_surface_present: false,
                    observed_tool_version: None,
                    observed_schema_digest: None,
                },
            )
        })
        .collect();
    let executor = DiscoveryScanExecutor::new(
        catalog,
        ScanEnvironment::new(
            home.path().to_path_buf(),
            "0.1.0-test",
            "2026-07-12T00:00:00Z",
            observations,
        ),
    );
    let context = LocalContext::with_executor(Arc::new(executor));

    let StartLocalScanOutcome::Accepted { attempt_id } = context.start_scan().unwrap() else {
        panic!("first Local scan must be accepted");
    };
    let state = wait_for_terminal(&context);
    assert_eq!(
        state.latest_terminal_report.as_ref().unwrap().state,
        LocalAttemptState::Complete
    );
    let header = state.latest_complete.unwrap();
    assert_eq!(header.attempt_id, attempt_id);
    let publication = context.publication(&header.snapshot_id).unwrap();
    assert_eq!(publication.snapshot.snapshot_id, header.snapshot_id);
    assert_eq!(publication.snapshot.attempt_id, attempt_id);
}
