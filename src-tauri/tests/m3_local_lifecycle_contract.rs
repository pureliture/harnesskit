use std::collections::{BTreeMap, VecDeque};
use std::fs;
use std::path::PathBuf;
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

use harness_desktop_lib::api::dto::local::LocalScanStateDto;
use harness_desktop_lib::contexts::local::{
    LocalAttemptState, LocalContext, LocalScanEventEnvelope, LocalScanEventError,
    LocalScanEventPayload, LocalScanEventPort, LocalScanExecutor, LocalScanExecutorResult,
    LocalScanProgress, LocalScanProgressPort, LocalScanPublication, LocalSnapshot,
    LocalSnapshotStatus, StartLocalScanOutcome,
};

fn publication(
    snapshot_id: &str,
    status: LocalSnapshotStatus,
    instance_handle_ids: &[&str],
) -> LocalScanPublication {
    LocalScanPublication {
        snapshot: LocalSnapshot {
            snapshot_id: snapshot_id.to_string(),
            attempt_id: "fixture-attempt".to_string(),
            status,
            scan_timestamp: "2026-07-12T00:00:00Z".to_string(),
            app_version: "0.1.0-test".to_string(),
            adapter_set_fingerprint: "a".repeat(64),
            content_fingerprint: "b".repeat(64),
            qualified_tools: Vec::new(),
            instances: Vec::new(),
            coverage: Vec::new(),
            skipped_paths: Vec::new(),
            issues: Vec::new(),
            project_ignore_summaries: Vec::new(),
        },
        instance_handle_ids: instance_handle_ids
            .iter()
            .map(|value| (*value).to_string())
            .collect(),
        instance_handles: BTreeMap::new(),
        project_locations: BTreeMap::new(),
    }
}

fn source(relative: &str) -> String {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    fs::read_to_string(root.join(relative)).unwrap_or_default()
}

#[test]
fn local_lifecycle_declares_a_narrow_executor_and_session_store_boundary() {
    let context = source("src/contexts/local/context.rs");
    let store = source("src/contexts/local/store.rs");
    let coordinator = source("src/operation_coordinator.rs");

    assert!(context.contains("pub trait LocalScanExecutor"));
    assert!(context.contains("pub enum StartLocalScanOutcome"));
    assert!(context.contains("pub fn start_scan"));
    assert!(store.contains("pub struct LocalSessionState"));
    assert!(coordinator.contains("LocalScan"));

    for forbidden in [
        "checkout",
        "SotContext",
        "InstallFlow",
        "dist/",
        "SubprocessRunner",
    ] {
        assert!(!context.contains(forbidden));
        assert!(!store.contains(forbidden));
    }
}

struct BlockingExecutor {
    entered: mpsc::SyncSender<String>,
    release: Mutex<mpsc::Receiver<()>>,
}

impl LocalScanExecutor for BlockingExecutor {
    fn execute(
        &self,
        attempt_id: &str,
        _progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult {
        self.entered.send(attempt_id.to_string()).unwrap();
        self.release.lock().unwrap().recv().unwrap();
        LocalScanExecutorResult::Failed {
            code: "fixture_failed".to_string(),
            diagnostics: None,
        }
    }
}

#[test]
fn start_is_immediate_and_duplicate_returns_the_running_attempt_id() {
    let (entered_tx, entered_rx) = mpsc::sync_channel(1);
    let (release_tx, release_rx) = mpsc::sync_channel(1);
    let context = LocalContext::with_executor(Arc::new(BlockingExecutor {
        entered: entered_tx,
        release: Mutex::new(release_rx),
    }));

    let StartLocalScanOutcome::Accepted { attempt_id } = context.start_scan().unwrap() else {
        panic!("first start must be accepted");
    };
    assert_eq!(
        entered_rx.recv_timeout(Duration::from_secs(1)).unwrap(),
        attempt_id
    );

    assert_eq!(
        context.start_scan().unwrap(),
        StartLocalScanOutcome::AlreadyRunning {
            attempt_id: attempt_id.clone()
        }
    );

    release_tx.send(()).unwrap();
}

#[test]
fn local_state_surface_declares_current_terminal_and_retained_snapshot_slots() {
    let context = source("src/contexts/local/context.rs");
    let store = source("src/contexts/local/store.rs");

    assert!(context.contains("pub fn state"));
    assert!(context.contains("pub fn publication"));
    assert!(store.contains("current_attempt"));
    assert!(store.contains("latest_terminal_report"));
    assert!(store.contains("latest_complete"));
    assert!(store.contains("latest_partial"));
}

#[test]
fn running_attempt_is_stored_before_the_worker_can_observe_it() {
    let (entered_tx, entered_rx) = mpsc::sync_channel(1);
    let (release_tx, release_rx) = mpsc::sync_channel(1);
    let context = LocalContext::with_executor(Arc::new(BlockingExecutor {
        entered: entered_tx,
        release: Mutex::new(release_rx),
    }));

    let StartLocalScanOutcome::Accepted { attempt_id } = context.start_scan().unwrap() else {
        panic!("first start must be accepted");
    };
    entered_rx.recv_timeout(Duration::from_secs(1)).unwrap();

    let state = context.state();
    let running = state.current_attempt.expect("Running must be stored first");
    assert_eq!(state.state_revision, 1);
    assert_eq!(running.attempt_id, attempt_id);
    assert_eq!(running.state, LocalAttemptState::Running);
    assert!(state.latest_terminal_report.is_none());

    release_tx.send(()).unwrap();
}

struct FixedExecutor(LocalScanExecutorResult);

impl LocalScanExecutor for FixedExecutor {
    fn execute(
        &self,
        _attempt_id: &str,
        _progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult {
        self.0.clone()
    }
}

struct ProgressingExecutor(LocalScanExecutorResult);

impl LocalScanExecutor for ProgressingExecutor {
    fn execute(
        &self,
        _attempt_id: &str,
        progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult {
        progress.report(&LocalScanProgress {
            adapter_id: "codex-builtin".to_string(),
            surface_id: "project-skills".to_string(),
            coverage_id: "coverage-project-skills".to_string(),
            item_count: 7,
        });
        self.0.clone()
    }
}

#[derive(Default)]
struct RecordingEventPort(Mutex<Vec<LocalScanEventEnvelope>>);

impl LocalScanEventPort for RecordingEventPort {
    fn emit_changed(&self, event: &LocalScanEventEnvelope) -> Result<(), LocalScanEventError> {
        self.0.lock().unwrap().push(event.clone());
        Ok(())
    }
}

struct FailingEventPort;

impl LocalScanEventPort for FailingEventPort {
    fn emit_changed(&self, _event: &LocalScanEventEnvelope) -> Result<(), LocalScanEventError> {
        Err(LocalScanEventError {
            code: "fixture_emit_failed",
        })
    }
}

struct BlockingProgressExecutor {
    progress_stored: mpsc::SyncSender<()>,
    release: Mutex<mpsc::Receiver<()>>,
}

impl LocalScanExecutor for BlockingProgressExecutor {
    fn execute(
        &self,
        _attempt_id: &str,
        progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult {
        progress.report(&LocalScanProgress {
            adapter_id: "claude-settings".to_string(),
            surface_id: "user-hooks".to_string(),
            coverage_id: "coverage-user-hooks".to_string(),
            item_count: 3,
        });
        self.progress_stored.send(()).unwrap();
        self.release.lock().unwrap().recv().unwrap();
        LocalScanExecutorResult::Failed {
            code: "fixture_finished".to_string(),
            diagnostics: None,
        }
    }
}

fn wait_for_terminal(
    context: &LocalContext,
) -> harness_desktop_lib::contexts::local::LocalSessionState {
    let deadline = std::time::Instant::now() + Duration::from_secs(1);
    loop {
        let state = context.state();
        if state.latest_terminal_report.is_some() {
            return state;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "terminal report timeout"
        );
        std::thread::yield_now();
    }
}

fn wait_for_terminal_attempt(
    context: &LocalContext,
    attempt_id: &str,
) -> harness_desktop_lib::contexts::local::LocalSessionState {
    let deadline = std::time::Instant::now() + Duration::from_secs(1);
    loop {
        let state = context.state();
        if state
            .latest_terminal_report
            .as_ref()
            .is_some_and(|report| report.attempt_id == attempt_id)
        {
            return state;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "terminal report timeout"
        );
        std::thread::yield_now();
    }
}

#[test]
fn failed_scan_publishes_report_only_and_clears_the_running_attempt() {
    let context =
        LocalContext::with_executor(Arc::new(FixedExecutor(LocalScanExecutorResult::Failed {
            code: "catalog_failed".to_string(),
            diagnostics: None,
        })));

    let StartLocalScanOutcome::Accepted { attempt_id } = context.start_scan().unwrap() else {
        panic!("first start must be accepted");
    };
    let state = wait_for_terminal(&context);

    assert!(state.current_attempt.is_none());
    assert!(state.latest_complete.is_none());
    assert!(state.latest_partial.is_none());
    assert_eq!(state.state_revision, 2);
    let report = state.latest_terminal_report.unwrap();
    assert_eq!(report.attempt_id, attempt_id);
    assert_eq!(report.state, LocalAttemptState::Failed);
    assert_eq!(report.error_code.as_deref(), Some("catalog_failed"));
    assert_eq!(
        context.publication("not-published").unwrap_err(),
        "snapshot_expired"
    );
}

#[test]
fn complete_scan_replaces_the_complete_header_and_retains_its_publication() {
    let publication = publication(
        "snapshot-complete-1",
        LocalSnapshotStatus::Complete,
        &["instance-a"],
    );
    let context = LocalContext::with_executor(Arc::new(FixedExecutor(
        LocalScanExecutorResult::Complete(publication.clone()),
    )));

    let StartLocalScanOutcome::Accepted { attempt_id } = context.start_scan().unwrap() else {
        panic!("first start must be accepted");
    };
    let state = wait_for_terminal(&context);

    assert_eq!(
        state.latest_terminal_report.as_ref().unwrap().state,
        LocalAttemptState::Complete
    );
    let header = state.latest_complete.expect("complete header must publish");
    assert_eq!(header.snapshot_id, publication.snapshot.snapshot_id);
    assert_eq!(header.attempt_id, attempt_id);
    assert_eq!(header.status, LocalSnapshotStatus::Complete);
    assert!(state.latest_partial.is_none());
    assert_eq!(
        context
            .publication(&publication.snapshot.snapshot_id)
            .unwrap(),
        publication
    );
}

#[test]
fn scan_events_are_revisioned_and_strictly_sequenced_after_store_transitions() {
    let events = Arc::new(RecordingEventPort::default());
    let publication = publication(
        "snapshot-event",
        LocalSnapshotStatus::Complete,
        &["instance-event"],
    );
    let context = LocalContext::with_executor_and_events(
        Arc::new(ProgressingExecutor(LocalScanExecutorResult::Complete(
            publication,
        ))),
        events.clone(),
    );

    let StartLocalScanOutcome::Accepted { attempt_id } = context.start_scan().unwrap() else {
        panic!("event fixture must be accepted");
    };
    wait_for_terminal_attempt(&context, &attempt_id);
    let events = events.0.lock().unwrap().clone();

    assert_eq!(events.len(), 3);
    assert_eq!(events[0].attempt_id, attempt_id);
    assert_eq!(events[0].sequence, 1);
    assert_eq!(events[0].state_revision, 1);
    assert_eq!(events[0].payload, LocalScanEventPayload::Running);
    assert_eq!(events[1].attempt_id, attempt_id);
    assert_eq!(events[1].sequence, 2);
    assert_eq!(events[1].state_revision, 2);
    assert_eq!(
        events[1].payload,
        LocalScanEventPayload::Progress {
            adapter_id: "codex-builtin".to_string(),
            surface_id: "project-skills".to_string(),
            coverage_id: "coverage-project-skills".to_string(),
            item_count: 7,
        }
    );
    assert_eq!(events[2].attempt_id, attempt_id);
    assert_eq!(events[2].sequence, 3);
    assert_eq!(events[2].state_revision, 3);
    assert_eq!(
        events[2].payload,
        LocalScanEventPayload::Complete {
            snapshot_id: "snapshot-event".to_string()
        }
    );
}

#[test]
fn progress_is_reconcilable_from_store_when_every_event_emit_fails() {
    let (progress_tx, progress_rx) = mpsc::sync_channel(1);
    let (release_tx, release_rx) = mpsc::sync_channel(1);
    let context = LocalContext::with_executor_and_events(
        Arc::new(BlockingProgressExecutor {
            progress_stored: progress_tx,
            release: Mutex::new(release_rx),
        }),
        Arc::new(FailingEventPort),
    );

    let StartLocalScanOutcome::Accepted { attempt_id } = context.start_scan().unwrap() else {
        panic!("progress fixture must be accepted");
    };
    progress_rx.recv_timeout(Duration::from_secs(1)).unwrap();

    let state = context.state();
    assert_eq!(state.state_revision, 2);
    let running = state
        .current_attempt
        .expect("progress must remain in store");
    assert_eq!(running.attempt_id, attempt_id);
    assert_eq!(
        running.progress,
        Some(LocalScanProgress {
            adapter_id: "claude-settings".to_string(),
            surface_id: "user-hooks".to_string(),
            coverage_id: "coverage-user-hooks".to_string(),
            item_count: 3,
        })
    );
    let json = serde_json::to_string(&LocalScanStateDto::from(context.state())).unwrap();
    assert!(json.contains("claude-settings"));
    assert!(json.contains("user-hooks"));
    assert!(!json.contains("/Users/"));

    release_tx.send(()).unwrap();
}

struct QueueExecutor(Mutex<VecDeque<LocalScanExecutorResult>>);

impl LocalScanExecutor for QueueExecutor {
    fn execute(
        &self,
        _attempt_id: &str,
        _progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult {
        self.0.lock().unwrap().pop_front().unwrap()
    }
}

#[test]
fn partial_scan_uses_a_separate_slot_without_replacing_latest_complete() {
    let complete = publication(
        "snapshot-complete",
        LocalSnapshotStatus::Complete,
        &["complete-instance"],
    );
    let partial = publication(
        "snapshot-partial",
        LocalSnapshotStatus::Partial,
        &["partial-instance"],
    );
    let context =
        LocalContext::with_executor(Arc::new(QueueExecutor(Mutex::new(VecDeque::from([
            LocalScanExecutorResult::Complete(complete.clone()),
            LocalScanExecutorResult::Partial {
                publication: partial.clone(),
                issue_codes: vec!["permission_denied".to_string()],
            },
        ])))));

    let StartLocalScanOutcome::Accepted {
        attempt_id: complete_attempt,
    } = context.start_scan().unwrap()
    else {
        panic!("complete start must be accepted");
    };
    wait_for_terminal_attempt(&context, &complete_attempt);
    let StartLocalScanOutcome::Accepted {
        attempt_id: partial_attempt,
    } = context.start_scan().unwrap()
    else {
        panic!("partial start must be accepted");
    };
    let state = wait_for_terminal_attempt(&context, &partial_attempt);

    assert_eq!(
        state.latest_terminal_report.as_ref().unwrap().state,
        LocalAttemptState::Partial
    );
    assert_eq!(
        state.latest_complete.as_ref().unwrap().snapshot_id,
        complete.snapshot.snapshot_id
    );
    assert_eq!(
        state.latest_partial.as_ref().unwrap().snapshot_id,
        partial.snapshot.snapshot_id
    );
    assert_eq!(
        context.publication(&complete.snapshot.snapshot_id).unwrap(),
        complete
    );
    assert_eq!(
        context.publication(&partial.snapshot.snapshot_id).unwrap(),
        partial
    );
}

struct PanicExecutor;

impl LocalScanExecutor for PanicExecutor {
    fn execute(
        &self,
        _attempt_id: &str,
        _progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult {
        panic!("fixture worker panic")
    }
}

#[test]
fn worker_panic_finalizes_once_as_worker_aborted_and_releases_the_gate() {
    let context = LocalContext::with_executor(Arc::new(PanicExecutor));
    let StartLocalScanOutcome::Accepted { attempt_id } = context.start_scan().unwrap() else {
        panic!("panic fixture must be accepted");
    };

    let state = wait_for_terminal_attempt(&context, &attempt_id);
    let report = state.latest_terminal_report.unwrap();
    assert_eq!(report.state, LocalAttemptState::Failed);
    assert_eq!(report.error_code.as_deref(), Some("worker_aborted"));
    assert!(state.current_attempt.is_none());

    assert!(matches!(
        context.start_scan().unwrap(),
        StartLocalScanOutcome::Accepted { .. }
    ));
}

#[test]
fn replacing_a_complete_slot_expires_the_old_snapshot_and_handles() {
    let first = publication(
        "snapshot-complete-old",
        LocalSnapshotStatus::Complete,
        &["old-handle"],
    );
    let replacement = publication(
        "snapshot-complete-new",
        LocalSnapshotStatus::Complete,
        &["new-handle"],
    );
    let context =
        LocalContext::with_executor(Arc::new(QueueExecutor(Mutex::new(VecDeque::from([
            LocalScanExecutorResult::Complete(first.clone()),
            LocalScanExecutorResult::Complete(replacement.clone()),
        ])))));

    let StartLocalScanOutcome::Accepted {
        attempt_id: first_attempt,
    } = context.start_scan().unwrap()
    else {
        panic!("first complete must be accepted");
    };
    wait_for_terminal_attempt(&context, &first_attempt);
    let StartLocalScanOutcome::Accepted {
        attempt_id: replacement_attempt,
    } = context.start_scan().unwrap()
    else {
        panic!("replacement complete must be accepted");
    };
    let state = wait_for_terminal_attempt(&context, &replacement_attempt);

    assert_eq!(
        context
            .publication(&first.snapshot.snapshot_id)
            .unwrap_err(),
        "snapshot_expired"
    );
    assert_eq!(
        context
            .publication(&replacement.snapshot.snapshot_id)
            .unwrap(),
        replacement
    );
    assert_eq!(
        state.latest_complete.as_ref().unwrap().snapshot_id,
        "snapshot-complete-new"
    );
    assert_eq!(
        state.latest_terminal_report.as_ref().unwrap().attempt_id,
        replacement_attempt
    );
    assert_eq!(state.state_revision, 4);
    assert_eq!(
        context.state(),
        state,
        "read-only state calls must keep revision stable"
    );
}
