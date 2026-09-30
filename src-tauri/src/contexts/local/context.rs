use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::operation_coordinator::{
    BeginLocalReadError, BeginLocalRemovalError, BeginLocalScanError, OperationCoordinator,
};

use super::domain::{
    CoverageRecord, InstanceHandle, LocalInstance, ParseState, ProjectLocationRecord, SafeIssue,
    SkippedPath,
};
use super::path_action::{
    execute_path_action, LocalPathAction, LocalPathActionError, LocalPathActionOutcome,
    NativePathActionPort, UnavailableNativePathActionPort,
};
use super::project_ignore::{
    load_project_ignore, save_project_ignore, ProjectIgnoreError, ProjectIgnoreSource,
};
use super::removal::{
    apply_json_removal, validate_json_removal, verify_json_removal_semantics, ExpectedJsonValue,
    JsonRemovalSelection, RemovalMember, RemovalPlan, RemovalStrategy,
};
use super::snapshot::{LocalSnapshot, LocalSnapshotStatus};
#[cfg(test)]
use super::source_inspection::SourceReadPort;
use super::source_inspection::{
    CapturedLocalSource, LocalSourceInspectionService, OpenSourcePreviewJob, OpenSourceRequest,
    ReadSourceChunkRequest, ReadSourcePreviewJob, SourceInspectionError, SourcePreviewClose,
};
use super::store::{LocalAuthority, LocalSessionState};
use super::verified_path::{VerificationPolicy, VerifiedLocalPathResolver};

pub trait LocalScanExecutor: Send + Sync + 'static {
    fn execute(
        &self,
        attempt_id: &str,
        progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult;
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalScanProgress {
    pub adapter_id: String,
    pub surface_id: String,
    pub coverage_id: String,
    pub item_count: u64,
}

pub trait LocalScanProgressPort: Send + Sync {
    fn report(&self, progress: &LocalScanProgress);
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalScanPublication {
    pub snapshot: LocalSnapshot,
    pub instance_handle_ids: Vec<String>,
    pub instance_handles: BTreeMap<String, InstanceHandle>,
    pub project_locations: BTreeMap<String, ProjectLocationRecord>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalScanDiagnostics {
    pub app_version: Option<String>,
    pub adapter_set_fingerprint: Option<String>,
    pub coverage: Vec<CoverageRecord>,
    pub skipped_paths: Vec<SkippedPath>,
    pub issues: Vec<SafeIssue>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LocalScanExecutorResult {
    Complete(LocalScanPublication),
    Partial {
        publication: LocalScanPublication,
        issue_codes: Vec<String>,
    },
    Failed {
        code: String,
        diagnostics: Option<LocalScanDiagnostics>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StartLocalScanOutcome {
    Accepted { attempt_id: String },
    AlreadyRunning { attempt_id: String },
    OperationBusy,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IgnoreSaveRescanOutcome {
    Accepted { attempt_id: String },
    QueuedAfterCurrent { current_attempt_id: String },
    OperationBusy,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalContextError {
    pub code: String,
    pub line: Option<usize>,
    pub column: Option<usize>,
}

impl LocalContextError {
    fn new(code: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            line: None,
            column: None,
        }
    }

    fn from_project_ignore(error: ProjectIgnoreError) -> Self {
        Self {
            code: error.code.to_owned(),
            line: error.line,
            column: error.column,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalScanEventEnvelope {
    pub attempt_id: String,
    pub sequence: u64,
    pub state_revision: u64,
    pub payload: LocalScanEventPayload,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum LocalScanEventPayload {
    Running,
    Progress {
        adapter_id: String,
        surface_id: String,
        coverage_id: String,
        item_count: u64,
    },
    Complete {
        snapshot_id: String,
    },
    Partial {
        snapshot_id: String,
    },
    Failed {
        code: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LocalScanEventError {
    pub code: &'static str,
}

pub trait LocalScanEventPort: Send + Sync + 'static {
    fn emit_changed(&self, event: &LocalScanEventEnvelope) -> Result<(), LocalScanEventError>;
}

struct NoopLocalScanEventPort;

impl LocalScanEventPort for NoopLocalScanEventPort {
    fn emit_changed(&self, _event: &LocalScanEventEnvelope) -> Result<(), LocalScanEventError> {
        Ok(())
    }
}

trait LocalWorkerSpawner: Send + Sync + 'static {
    fn spawn(&self, worker: Box<dyn FnOnce() + Send + 'static>) -> Result<(), ()>;
}

struct ThreadLocalWorkerSpawner;

impl LocalWorkerSpawner for ThreadLocalWorkerSpawner {
    fn spawn(&self, worker: Box<dyn FnOnce() + Send + 'static>) -> Result<(), ()> {
        std::thread::Builder::new()
            .name("local-scan-worker".to_string())
            .spawn(worker)
            .map(|_| ())
            .map_err(|_| ())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LocalRemovalEffectKind {
    SharedConfigEntry,
    DedicatedFile,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalRemovalMemberSummary {
    pub instance_id: String,
    pub display_name: String,
    pub effect: Option<LocalRemovalEffectKind>,
    pub code: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalRemovalPlanPreview {
    pub plan_id: String,
    pub plan_digest: String,
    pub eligible_members: Vec<LocalRemovalMemberSummary>,
    pub blocked_members: Vec<LocalRemovalMemberSummary>,
    pub shared_config_entry_removal_count: usize,
    pub dedicated_file_deletion_count: usize,
    pub parent_folder_deletion_count: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LocalRemovalSourceGroupState {
    Success,
    FailedUnchanged,
    Indeterminate,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalRemovalSourceGroupOutcome {
    pub state: LocalRemovalSourceGroupState,
    pub member_ids: Vec<String>,
    pub code: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LocalRemovalRescanState {
    NotStarted,
    Accepted,
    Queued,
    Unavailable,
    FailedToStart,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalRemovalApplyOutcome {
    pub source_group_outcomes: Vec<LocalRemovalSourceGroupOutcome>,
    pub rescan_state: LocalRemovalRescanState,
    pub rescan_attempt_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LocalRemovalReconciliationState {
    Confirmed,
    CoverageIncomplete,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalRemovalReconciliation {
    pub snapshot_id: String,
    pub state: LocalRemovalReconciliationState,
    pub confirmed_absent_instance_ids: Vec<String>,
    pub unresolved_instance_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct LocalRemovalRescanHandoff {
    state: LocalRemovalRescanState,
    attempt_id: Option<String>,
}

#[derive(Clone)]
struct PreparedRemovalMember {
    summary: LocalRemovalMemberSummary,
    handle: InstanceHandle,
    member: RemovalMember,
}

#[derive(Clone)]
struct PreparedRemovalGroup {
    strategy: RemovalStrategy,
    members: Vec<PreparedRemovalMember>,
    prepared_content_sha256: [u8; 32],
    json_selections: Vec<JsonRemovalSelection>,
}

#[derive(Clone)]
struct PreparedRemovalPlan {
    plan: RemovalPlan,
    groups: Vec<PreparedRemovalGroup>,
    snapshot_generation: u64,
    expires_at: Instant,
}

#[derive(Clone)]
struct RemovalCandidate {
    instance: LocalInstance,
    handle: InstanceHandle,
    display_name: String,
}

const REMOVAL_PLAN_TTL: Duration = Duration::from_secs(300);

pub struct LocalContext {
    executor: Arc<dyn LocalScanExecutor>,
    operations: Arc<OperationCoordinator>,
    authority: Arc<LocalAuthority>,
    event_port: Arc<dyn LocalScanEventPort>,
    native_path_actions: Arc<dyn NativePathActionPort>,
    source_inspection: Arc<LocalSourceInspectionService>,
    worker_spawner: Arc<dyn LocalWorkerSpawner>,
    next_attempt: Arc<AtomicU64>,
    snapshot_generation: Arc<AtomicU64>,
    scheduler: Arc<Mutex<LocalScanSchedulerState>>,
    removal_plans: Arc<Mutex<BTreeMap<String, PreparedRemovalPlan>>>,
    next_removal_plan: Arc<AtomicU64>,
}

#[derive(Debug, Default)]
struct LocalScanSchedulerState {
    running_attempt_id: Option<String>,
    follow_up_after_terminal: bool,
}

struct AttemptGuard {
    operations: Arc<OperationCoordinator>,
    authority: Arc<LocalAuthority>,
    event_port: Arc<dyn LocalScanEventPort>,
    attempt_id: String,
    sequence: Arc<AtomicU64>,
    scheduler: Arc<Mutex<LocalScanSchedulerState>>,
    next_attempt: Arc<AtomicU64>,
    snapshot_generation: Arc<AtomicU64>,
    executor: Arc<dyn LocalScanExecutor>,
    worker_spawner: Arc<dyn LocalWorkerSpawner>,
    finalized: bool,
}

struct AttemptProgressEventPort {
    operations: Arc<OperationCoordinator>,
    authority: Arc<LocalAuthority>,
    event_port: Arc<dyn LocalScanEventPort>,
    attempt_id: String,
    sequence: Arc<AtomicU64>,
}

struct TerminalCommit {
    state: LocalSessionState,
    payload: LocalScanEventPayload,
}

impl LocalScanProgressPort for AttemptProgressEventPort {
    fn report(&self, progress: &LocalScanProgress) {
        let Some(state) = self
            .operations
            .record_local_scan_progress(&self.attempt_id, || {
                self.authority
                    .record_progress(&self.attempt_id, progress.clone())
            })
            .ok()
            .flatten()
        else {
            return;
        };
        let sequence = self.sequence.fetch_add(1, Ordering::Relaxed) + 1;
        emit_changed_safely(
            self.event_port.as_ref(),
            &LocalScanEventEnvelope {
                attempt_id: self.attempt_id.clone(),
                sequence,
                state_revision: state.state_revision,
                payload: LocalScanEventPayload::Progress {
                    adapter_id: progress.adapter_id.clone(),
                    surface_id: progress.surface_id.clone(),
                    coverage_id: progress.coverage_id.clone(),
                    item_count: progress.item_count,
                },
            },
        );
    }
}

impl AttemptGuard {
    fn new(
        operations: Arc<OperationCoordinator>,
        authority: Arc<LocalAuthority>,
        event_port: Arc<dyn LocalScanEventPort>,
        attempt_id: String,
        sequence: Arc<AtomicU64>,
        scheduler: Arc<Mutex<LocalScanSchedulerState>>,
        next_attempt: Arc<AtomicU64>,
        snapshot_generation: Arc<AtomicU64>,
        executor: Arc<dyn LocalScanExecutor>,
        worker_spawner: Arc<dyn LocalWorkerSpawner>,
    ) -> Self {
        Self {
            operations,
            authority,
            event_port,
            attempt_id,
            sequence,
            scheduler,
            next_attempt,
            snapshot_generation,
            executor,
            worker_spawner,
            finalized: false,
        }
    }

    fn finish(mut self, result: LocalScanExecutorResult) {
        self.finalize(result);
        self.finalized = true;
    }

    fn finalize(&mut self, result: LocalScanExecutorResult) {
        let (commit, successor_id) = {
            let mut scheduler = self
                .scheduler
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if scheduler.running_attempt_id.as_deref() != Some(self.attempt_id.as_str()) {
                return;
            }
            let successor_id = scheduler.follow_up_after_terminal.then(|| {
                let sequence = self.next_attempt.fetch_add(1, Ordering::Relaxed) + 1;
                format!("local-scan-{sequence}")
            });
            let completion = self.operations.finish_local_scan_with_handoff(
                &self.attempt_id,
                successor_id.as_deref(),
                || {
                    store_terminal_with_fallback(
                        &self.authority,
                        &self.attempt_id,
                        result,
                        self.snapshot_generation.as_ref(),
                    )
                },
                || match successor_id.as_deref() {
                    Some(next) => self.authority.begin_attempt(next),
                    None => Ok(()),
                },
            );
            let Ok(Some(commit)) = completion else {
                scheduler.running_attempt_id = None;
                scheduler.follow_up_after_terminal = false;
                return;
            };
            scheduler.follow_up_after_terminal = false;
            scheduler.running_attempt_id = successor_id.clone();
            (commit, successor_id)
        };
        let successor_running = successor_id.and_then(|attempt_id| {
            let guard = AttemptGuard::new(
                Arc::clone(&self.operations),
                Arc::clone(&self.authority),
                Arc::clone(&self.event_port),
                attempt_id,
                Arc::new(AtomicU64::new(1)),
                Arc::clone(&self.scheduler),
                Arc::clone(&self.next_attempt),
                Arc::clone(&self.snapshot_generation),
                Arc::clone(&self.executor),
                Arc::clone(&self.worker_spawner),
            );
            let running = LocalScanEventEnvelope {
                attempt_id: guard.attempt_id.clone(),
                sequence: 1,
                state_revision: self.authority.state().state_revision,
                payload: LocalScanEventPayload::Running,
            };
            spawn_attempt_guard(guard).ok().map(|()| running)
        });
        emit_changed_safely(
            self.event_port.as_ref(),
            &LocalScanEventEnvelope {
                attempt_id: self.attempt_id.clone(),
                sequence: self.sequence.fetch_add(1, Ordering::Relaxed) + 1,
                state_revision: commit.state.state_revision,
                payload: commit.payload,
            },
        );
        if let Some(running) = successor_running {
            emit_changed_safely(self.event_port.as_ref(), &running);
        }
    }
}

impl Drop for AttemptGuard {
    fn drop(&mut self) {
        if self.finalized {
            return;
        }
        self.finalize(LocalScanExecutorResult::Failed {
            code: "worker_aborted".to_string(),
            diagnostics: None,
        });
        self.finalized = true;
    }
}

fn spawn_attempt_guard(guard: AttemptGuard) -> Result<(), ()> {
    let executor = Arc::clone(&guard.executor);
    let operations = Arc::clone(&guard.operations);
    let authority = Arc::clone(&guard.authority);
    let event_port = Arc::clone(&guard.event_port);
    let worker_attempt_id = guard.attempt_id.clone();
    let worker_sequence = Arc::clone(&guard.sequence);
    let worker_spawner = Arc::clone(&guard.worker_spawner);
    let worker = Box::new(move || {
        let progress = AttemptProgressEventPort {
            operations,
            authority,
            event_port,
            attempt_id: worker_attempt_id.clone(),
            sequence: worker_sequence,
        };
        if let Ok(result) = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            executor.execute(&worker_attempt_id, &progress)
        })) {
            guard.finish(result);
        }
    });
    worker_spawner.spawn(worker)
}

fn store_terminal_with_fallback(
    authority: &LocalAuthority,
    attempt_id: &str,
    result: LocalScanExecutorResult,
    snapshot_generation: &AtomicU64,
) -> Result<TerminalCommit, String> {
    let payload = terminal_payload(&result);
    let publishes_snapshot = result_publishes_snapshot(&result);
    match authority.finish_attempt(attempt_id, result) {
        Ok(state) => {
            if publishes_snapshot {
                snapshot_generation.fetch_add(1, Ordering::AcqRel);
            }
            Ok(TerminalCommit { state, payload })
        }
        Err(_) => {
            let fallback = LocalScanExecutorResult::Failed {
                code: "worker_aborted".to_string(),
                diagnostics: None,
            };
            authority
                .finish_attempt(attempt_id, fallback)
                .map(|state| TerminalCommit {
                    state,
                    payload: LocalScanEventPayload::Failed {
                        code: "worker_aborted".to_string(),
                    },
                })
        }
    }
}

fn result_publishes_snapshot(result: &LocalScanExecutorResult) -> bool {
    match result {
        LocalScanExecutorResult::Complete(_) => true,
        LocalScanExecutorResult::Partial { issue_codes, .. } => !issue_codes.is_empty(),
        LocalScanExecutorResult::Failed { .. } => false,
    }
}

fn emit_changed_safely(event_port: &dyn LocalScanEventPort, event: &LocalScanEventEnvelope) {
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _ = event_port.emit_changed(event);
    }));
}

struct UnavailableExecutor;

impl LocalScanExecutor for UnavailableExecutor {
    fn execute(
        &self,
        _attempt_id: &str,
        _progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult {
        LocalScanExecutorResult::Failed {
            code: "local_scan_unavailable".to_string(),
            diagnostics: None,
        }
    }
}

impl Default for LocalContext {
    fn default() -> Self {
        Self::with_executor_operations_events_and_path_actions(
            Arc::new(UnavailableExecutor),
            Arc::new(OperationCoordinator::default()),
            Arc::new(NoopLocalScanEventPort),
            Arc::new(UnavailableNativePathActionPort),
        )
    }
}

impl LocalContext {
    pub fn with_executor(executor: Arc<dyn LocalScanExecutor>) -> Self {
        Self::with_executor_and_events(executor, Arc::new(NoopLocalScanEventPort))
    }

    pub fn with_executor_and_events(
        executor: Arc<dyn LocalScanExecutor>,
        event_port: Arc<dyn LocalScanEventPort>,
    ) -> Self {
        Self::with_executor_operations_and_events(
            executor,
            Arc::new(OperationCoordinator::default()),
            event_port,
        )
    }

    pub fn with_executor_and_path_actions(
        executor: Arc<dyn LocalScanExecutor>,
        native_path_actions: Arc<dyn NativePathActionPort>,
    ) -> Self {
        Self::with_executor_operations_events_and_path_actions(
            executor,
            Arc::new(OperationCoordinator::default()),
            Arc::new(NoopLocalScanEventPort),
            native_path_actions,
        )
    }

    pub(crate) fn with_executor_operations_and_events(
        executor: Arc<dyn LocalScanExecutor>,
        operations: Arc<OperationCoordinator>,
        event_port: Arc<dyn LocalScanEventPort>,
    ) -> Self {
        Self::with_executor_operations_events_and_path_actions(
            executor,
            operations,
            event_port,
            Arc::new(UnavailableNativePathActionPort),
        )
    }

    pub(crate) fn with_executor_operations_events_and_path_actions(
        executor: Arc<dyn LocalScanExecutor>,
        operations: Arc<OperationCoordinator>,
        event_port: Arc<dyn LocalScanEventPort>,
        native_path_actions: Arc<dyn NativePathActionPort>,
    ) -> Self {
        let authority = Arc::new(LocalAuthority::default());
        let source_inspection = Arc::new(LocalSourceInspectionService::new(
            Arc::clone(&operations),
            Arc::clone(&authority),
        ));
        Self {
            executor,
            operations,
            authority,
            event_port,
            native_path_actions,
            source_inspection,
            worker_spawner: Arc::new(ThreadLocalWorkerSpawner),
            next_attempt: Arc::new(AtomicU64::new(0)),
            snapshot_generation: Arc::new(AtomicU64::new(0)),
            scheduler: Arc::new(Mutex::new(LocalScanSchedulerState::default())),
            removal_plans: Arc::new(Mutex::new(BTreeMap::new())),
            next_removal_plan: Arc::new(AtomicU64::new(0)),
        }
    }

    #[cfg(test)]
    pub(crate) fn with_executor_operations_and_source_reader(
        executor: Arc<dyn LocalScanExecutor>,
        operations: Arc<OperationCoordinator>,
        source_reader: Arc<dyn SourceReadPort>,
    ) -> Self {
        let authority = Arc::new(LocalAuthority::default());
        let source_inspection = Arc::new(LocalSourceInspectionService::with_reader(
            Arc::clone(&operations),
            Arc::clone(&authority),
            source_reader,
        ));
        Self {
            executor,
            operations,
            authority,
            event_port: Arc::new(NoopLocalScanEventPort),
            native_path_actions: Arc::new(UnavailableNativePathActionPort),
            source_inspection,
            worker_spawner: Arc::new(ThreadLocalWorkerSpawner),
            next_attempt: Arc::new(AtomicU64::new(0)),
            snapshot_generation: Arc::new(AtomicU64::new(0)),
            scheduler: Arc::new(Mutex::new(LocalScanSchedulerState::default())),
            removal_plans: Arc::new(Mutex::new(BTreeMap::new())),
            next_removal_plan: Arc::new(AtomicU64::new(0)),
        }
    }

    pub fn start_scan(&self) -> Result<StartLocalScanOutcome, LocalContextError> {
        self.start_scan_inner(false).map(|outcome| match outcome {
            IgnoreSaveRescanOutcome::Accepted { attempt_id } => {
                StartLocalScanOutcome::Accepted { attempt_id }
            }
            IgnoreSaveRescanOutcome::QueuedAfterCurrent { current_attempt_id } => {
                StartLocalScanOutcome::AlreadyRunning {
                    attempt_id: current_attempt_id,
                }
            }
            IgnoreSaveRescanOutcome::OperationBusy => StartLocalScanOutcome::OperationBusy,
        })
    }

    pub fn request_ignore_save_rescan(&self) -> Result<IgnoreSaveRescanOutcome, LocalContextError> {
        self.request_follow_up_rescan()
    }

    fn request_follow_up_rescan(&self) -> Result<IgnoreSaveRescanOutcome, LocalContextError> {
        self.start_scan_inner(true)
    }

    fn start_scan_inner(
        &self,
        ignore_save: bool,
    ) -> Result<IgnoreSaveRescanOutcome, LocalContextError> {
        let mut scheduler = self
            .scheduler
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(attempt_id) = scheduler.running_attempt_id.clone() {
            if ignore_save {
                scheduler.follow_up_after_terminal = true;
            }
            return Ok(IgnoreSaveRescanOutcome::QueuedAfterCurrent {
                current_attempt_id: attempt_id,
            });
        }
        let sequence = self.next_attempt.fetch_add(1, Ordering::Relaxed) + 1;
        let attempt_id = format!("local-scan-{sequence}");
        let guard_attempt_id = attempt_id.clone();
        let guard_sequence = Arc::new(AtomicU64::new(1));
        let guard_operations = Arc::clone(&self.operations);
        let guard_authority = Arc::clone(&self.authority);
        let guard_event_port = Arc::clone(&self.event_port);
        match self
            .operations
            .begin_local_scan(&attempt_id, || self.authority.begin_attempt(&attempt_id))
        {
            Ok(()) => {}
            Err(BeginLocalScanError::AlreadyRunning(attempt_id)) => {
                scheduler.running_attempt_id = Some(attempt_id.clone());
                return Ok(IgnoreSaveRescanOutcome::QueuedAfterCurrent {
                    current_attempt_id: attempt_id,
                });
            }
            Err(BeginLocalScanError::OperationBusy) => {
                return Ok(IgnoreSaveRescanOutcome::OperationBusy);
            }
            Err(BeginLocalScanError::Invariant(code)) => {
                return Err(LocalContextError::new(code));
            }
        }
        let guard = AttemptGuard::new(
            guard_operations,
            guard_authority,
            guard_event_port,
            guard_attempt_id,
            guard_sequence,
            Arc::clone(&self.scheduler),
            Arc::clone(&self.next_attempt),
            Arc::clone(&self.snapshot_generation),
            Arc::clone(&self.executor),
            Arc::clone(&self.worker_spawner),
        );
        scheduler.running_attempt_id = Some(attempt_id.clone());
        drop(scheduler);
        let running = LocalScanEventEnvelope {
            attempt_id: attempt_id.clone(),
            sequence: 1,
            state_revision: self.authority.state().state_revision,
            payload: LocalScanEventPayload::Running,
        };
        spawn_attempt_guard(guard).map_err(|()| LocalContextError::new("worker_aborted"))?;
        emit_changed_safely(self.event_port.as_ref(), &running);
        Ok(IgnoreSaveRescanOutcome::Accepted { attempt_id })
    }

    pub fn state(&self) -> LocalSessionState {
        self.authority.state()
    }

    pub fn publication(&self, snapshot_id: &str) -> Result<LocalScanPublication, String> {
        self.authority.publication(snapshot_id)
    }

    pub fn get_project_ignore(
        &self,
        snapshot_id: &str,
        project_id: &str,
    ) -> Result<ProjectIgnoreSource, LocalContextError> {
        let publication = self
            .publication(snapshot_id)
            .map_err(LocalContextError::new)?;
        let project = publication
            .project_locations
            .get(project_id)
            .ok_or_else(|| LocalContextError::new("project_unavailable"))?;
        load_project_ignore(project)
            .map(|(source, _)| source)
            .map_err(LocalContextError::from_project_ignore)
    }

    pub fn save_project_ignore_and_rescan(
        &self,
        snapshot_id: &str,
        project_id: &str,
        expected_source_revision: &str,
        exact_text: &str,
    ) -> Result<(ProjectIgnoreSource, IgnoreSaveRescanOutcome), LocalContextError> {
        let publication = self
            .publication(snapshot_id)
            .map_err(LocalContextError::new)?;
        let project = publication
            .project_locations
            .get(project_id)
            .ok_or_else(|| LocalContextError::new("project_unavailable"))?;
        let saved = save_project_ignore(project, expected_source_revision, exact_text)
            .map_err(LocalContextError::from_project_ignore)?;
        let outcome = self.request_ignore_save_rescan()?;
        Ok((saved.source, outcome))
    }

    pub fn act_on_instance(
        &self,
        snapshot_id: &str,
        instance_id: &str,
        action: LocalPathAction,
    ) -> Result<LocalPathActionOutcome, LocalPathActionError> {
        let lease = self
            .operations
            .begin_local_read(|| {
                self.authority
                    .instance_handle(snapshot_id, instance_id)
                    .map_err(|code| BeginLocalReadError::from_lookup_code(&code))
            })
            .map_err(|error| LocalPathActionError::new(error.code()))?;
        execute_path_action(lease.value(), action, self.native_path_actions.as_ref())
    }

    pub fn prepare_local_removal(
        &self,
        snapshot_id: &str,
        instance_ids: &[String],
    ) -> Result<LocalRemovalPlanPreview, LocalContextError> {
        let requested_ids = unique_removal_instance_ids(instance_ids);
        if requested_ids.is_empty() {
            return Err(LocalContextError::new("removal_selection_empty"));
        }
        if requested_ids.len() > 256 {
            return Err(LocalContextError::new("removal_selection_too_large"));
        }

        let snapshot_generation = self.snapshot_generation.load(Ordering::Acquire);
        let publication = self
            .operations
            .begin_local_read(|| {
                self.publication(snapshot_id)
                    .map_err(|code| BeginLocalReadError::from_lookup_code(&code))
            })
            .map_err(|error| LocalContextError::new(error.code()))?;
        let publication = publication.value().clone();
        let selected_ids = requested_ids.iter().cloned().collect::<BTreeSet<_>>();
        let mut blocked_members = Vec::new();
        let mut source_groups = Vec::<Vec<RemovalCandidate>>::new();

        for instance_id in requested_ids {
            let Some(instance) = publication
                .snapshot
                .instances
                .iter()
                .find(|instance| instance.instance_id == instance_id)
            else {
                blocked_members.push(blocked_removal_member(
                    instance_id,
                    "이름 정보 없음".to_string(),
                    "instance_handle_missing",
                ));
                continue;
            };
            let display_name = removal_display_name(instance);
            let Some(handle) = publication.instance_handles.get(&instance_id).cloned() else {
                blocked_members.push(blocked_removal_member(
                    instance_id,
                    display_name,
                    "instance_handle_missing",
                ));
                continue;
            };
            let candidate = RemovalCandidate {
                instance: instance.clone(),
                handle,
                display_name,
            };
            if let Some(group) = source_groups.iter_mut().find(|group| {
                group.first().is_some_and(|first| {
                    same_physical_removal_source(&first.handle, &candidate.handle)
                })
            }) {
                group.push(candidate);
            } else {
                source_groups.push(vec![candidate]);
            }
        }

        let mut prepared_groups = Vec::new();
        for source_group in source_groups {
            let mut group =
                match prepare_removal_source_group(&publication, &selected_ids, source_group) {
                    Ok(group) => group,
                    Err((members, code)) => {
                        blocked_members.extend(members.into_iter().map(|member| {
                            blocked_removal_member(
                                member.instance.instance_id,
                                member.display_name,
                                code,
                            )
                        }));
                        continue;
                    }
                };
            match prepare_removal_group(&mut group) {
                Ok(()) => prepared_groups.push(group),
                Err(code) => blocked_members.extend(group.members.into_iter().map(|member| {
                    blocked_removal_member(
                        member.summary.instance_id,
                        member.summary.display_name,
                        &code,
                    )
                })),
            }
        }
        let eligible_members = prepared_groups
            .iter()
            .flat_map(|group| group.members.iter().map(|member| member.summary.clone()))
            .collect::<Vec<_>>();
        let plan_id = new_removal_plan_id(&self.next_removal_plan)?;
        let plan_digest = removal_plan_digest(&plan_id, snapshot_id, &eligible_members);
        let members = prepared_groups
            .iter()
            .flat_map(|group| group.members.iter().map(|member| member.member.clone()))
            .collect::<Vec<_>>();
        let plan = RemovalPlan::new(&plan_id, &plan_digest, snapshot_id, members);
        let effect = plan.effect();
        let preview = LocalRemovalPlanPreview {
            plan_id: plan_id.clone(),
            plan_digest,
            eligible_members,
            blocked_members,
            shared_config_entry_removal_count: effect.shared_json_entry_removals(),
            dedicated_file_deletion_count: effect.dedicated_file_deletions(),
            parent_folder_deletion_count: 0,
        };
        let mut plans = self
            .removal_plans
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if plans.len() >= 32 {
            let expired = plans.keys().next().cloned();
            if let Some(expired) = expired {
                plans.remove(&expired);
            }
        }
        plans.insert(
            plan_id,
            PreparedRemovalPlan {
                plan,
                groups: prepared_groups,
                snapshot_generation,
                expires_at: removal_plan_deadline()?,
            },
        );
        Ok(preview)
    }

    pub fn apply_local_removal(
        &self,
        plan_id: &str,
        plan_digest: &str,
        confirmed: bool,
    ) -> Result<LocalRemovalApplyOutcome, LocalContextError> {
        if !confirmed {
            self.removal_plans
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .remove(plan_id);
            return Err(LocalContextError::new("removal_confirmation_required"));
        }
        let removal_guard = self
            .operations
            .begin_local_removal(plan_id)
            .map_err(|error| match error {
                BeginLocalRemovalError::OperationBusy => LocalContextError::new("operation_busy"),
            })?;
        let prepared = self
            .removal_plans
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(plan_id)
            .ok_or_else(|| LocalContextError::new("removal_plan_expired"))?;
        if prepared.snapshot_generation != self.snapshot_generation.load(Ordering::Acquire)
            || prepared.expires_at <= Instant::now()
        {
            return Err(LocalContextError::new("removal_plan_expired"));
        }
        if prepared.plan.digest() != plan_digest {
            return Err(LocalContextError::new("removal_plan_digest_mismatch"));
        }
        if prepared.plan.members().is_empty() {
            return Err(LocalContextError::new("removal_plan_empty"));
        }
        self.publication(prepared.plan.snapshot_id())
            .map_err(|_| LocalContextError::new("removal_plan_snapshot_expired"))?;

        let source_group_outcomes = prepared
            .groups
            .iter()
            .map(apply_removal_group)
            .collect::<Vec<_>>();
        let requires_reconciliation =
            removal_outcomes_require_reconciliation(&source_group_outcomes);

        let rescan = if requires_reconciliation {
            self.handoff_successful_removal_to_rescan(plan_id)
        } else {
            LocalRemovalRescanHandoff {
                state: LocalRemovalRescanState::NotStarted,
                attempt_id: None,
            }
        };
        drop(removal_guard);
        Ok(LocalRemovalApplyOutcome {
            source_group_outcomes,
            rescan_state: rescan.state,
            rescan_attempt_id: rescan.attempt_id,
        })
    }

    pub fn reconcile_local_removal(
        &self,
        snapshot_id: &str,
        expected_attempt_id: &str,
        instance_ids: &[String],
    ) -> Result<LocalRemovalReconciliation, LocalContextError> {
        let requested_ids = unique_removal_instance_ids(instance_ids);
        if requested_ids.is_empty() {
            return Err(LocalContextError::new("removal_reconciliation_empty"));
        }
        if requested_ids.len() != instance_ids.len() {
            return Err(LocalContextError::new(
                "removal_reconciliation_duplicate_id",
            ));
        }
        if requested_ids.len() > 256 {
            return Err(LocalContextError::new("removal_reconciliation_too_large"));
        }
        let expected_attempt_id = expected_attempt_id.trim();
        if expected_attempt_id.is_empty() {
            return Err(LocalContextError::new(
                "removal_reconciliation_attempt_required",
            ));
        }
        let lease = self
            .operations
            .begin_local_read(|| {
                self.authority
                    .latest_published_publication_for_attempt(snapshot_id, expected_attempt_id)
                    .map_err(|code| BeginLocalReadError::from_lookup_code(&code))
            })
            .map_err(|error| LocalContextError::new(error.code()))?;
        let publication = lease.value();
        if publication.snapshot.status != LocalSnapshotStatus::Complete {
            return Ok(LocalRemovalReconciliation {
                snapshot_id: snapshot_id.to_string(),
                state: LocalRemovalReconciliationState::CoverageIncomplete,
                confirmed_absent_instance_ids: Vec::new(),
                unresolved_instance_ids: requested_ids,
            });
        }
        let present_ids = publication
            .snapshot
            .instances
            .iter()
            .map(|instance| instance.instance_id.as_str())
            .collect::<BTreeSet<_>>();
        let (confirmed_absent_instance_ids, unresolved_instance_ids) = requested_ids
            .iter()
            .cloned()
            .partition(|instance_id| !present_ids.contains(instance_id.as_str()));
        Ok(LocalRemovalReconciliation {
            snapshot_id: snapshot_id.to_string(),
            state: LocalRemovalReconciliationState::Confirmed,
            confirmed_absent_instance_ids,
            unresolved_instance_ids,
        })
    }

    fn handoff_successful_removal_to_rescan(&self, plan_id: &str) -> LocalRemovalRescanHandoff {
        let mut scheduler = self
            .scheduler
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if scheduler.running_attempt_id.is_some() {
            return LocalRemovalRescanHandoff {
                state: LocalRemovalRescanState::Unavailable,
                attempt_id: None,
            };
        }

        let sequence = self.next_attempt.fetch_add(1, Ordering::Relaxed) + 1;
        let attempt_id = format!("local-scan-{sequence}");
        if self
            .operations
            .handoff_local_removal_to_scan(plan_id, &attempt_id, || {
                self.authority.begin_attempt(&attempt_id)
            })
            .is_err()
        {
            return LocalRemovalRescanHandoff {
                state: LocalRemovalRescanState::Unavailable,
                attempt_id: None,
            };
        }
        let guard = AttemptGuard::new(
            Arc::clone(&self.operations),
            Arc::clone(&self.authority),
            Arc::clone(&self.event_port),
            attempt_id.clone(),
            Arc::new(AtomicU64::new(1)),
            Arc::clone(&self.scheduler),
            Arc::clone(&self.next_attempt),
            Arc::clone(&self.snapshot_generation),
            Arc::clone(&self.executor),
            Arc::clone(&self.worker_spawner),
        );
        scheduler.running_attempt_id = Some(attempt_id.clone());
        drop(scheduler);

        let running = LocalScanEventEnvelope {
            attempt_id: attempt_id.clone(),
            sequence: 1,
            state_revision: self.authority.state().state_revision,
            payload: LocalScanEventPayload::Running,
        };
        if spawn_attempt_guard(guard).is_err() {
            return LocalRemovalRescanHandoff {
                state: LocalRemovalRescanState::FailedToStart,
                attempt_id: Some(attempt_id),
            };
        }
        emit_changed_safely(self.event_port.as_ref(), &running);
        LocalRemovalRescanHandoff {
            state: LocalRemovalRescanState::Accepted,
            attempt_id: Some(attempt_id),
        }
    }

    pub(crate) fn prepare_open_source_preview(
        &self,
        request: OpenSourceRequest,
    ) -> Result<OpenSourcePreviewJob, SourceInspectionError> {
        self.source_inspection.prepare_open(request)
    }

    pub(crate) fn prepare_read_source_preview(
        &self,
        request: ReadSourceChunkRequest,
    ) -> Result<ReadSourcePreviewJob, SourceInspectionError> {
        self.source_inspection.prepare_read(request)
    }

    pub(crate) fn close_source_preview(
        &self,
        view_generation: u64,
        preview_session_id: Option<&str>,
    ) -> SourcePreviewClose {
        self.source_inspection
            .close(view_generation, preview_session_id)
    }

    pub(crate) fn capture_revision_bound_source(
        &self,
        snapshot_id: &str,
        instance_id: &str,
        expected_source_revision: &str,
        max_bytes: usize,
    ) -> Result<CapturedLocalSource, SourceInspectionError> {
        self.source_inspection.capture_revision_bound_source(
            snapshot_id,
            instance_id,
            expected_source_revision,
            max_bytes,
        )
    }

    #[cfg(test)]
    pub(crate) fn publish_scan_result_for_test(
        &self,
        attempt_id: &str,
        result: LocalScanExecutorResult,
    ) -> Result<(), String> {
        let publishes_snapshot = result_publishes_snapshot(&result);
        self.operations
            .begin_local_scan(attempt_id, || self.authority.begin_attempt(attempt_id))
            .map_err(|error| format!("{error:?}"))?;
        self.operations
            .finish_local_scan(attempt_id, || {
                self.authority.finish_attempt(attempt_id, result)
            })?
            .ok_or_else(|| "local_attempt_invariant".to_string())?;
        if publishes_snapshot {
            self.snapshot_generation.fetch_add(1, Ordering::AcqRel);
        }
        Ok(())
    }
}

fn unique_removal_instance_ids(instance_ids: &[String]) -> Vec<String> {
    let mut unique = Vec::new();
    for instance_id in instance_ids {
        let normalized = instance_id.trim();
        if !normalized.is_empty() && !unique.iter().any(|existing| existing == normalized) {
            unique.push(normalized.to_string());
        }
    }
    unique
}

fn removal_effect_kind(strategy: RemovalStrategy) -> LocalRemovalEffectKind {
    match strategy {
        RemovalStrategy::JsonPointerRemovalV1 => LocalRemovalEffectKind::SharedConfigEntry,
        RemovalStrategy::DedicatedFileRemovalV1 => LocalRemovalEffectKind::DedicatedFile,
    }
}

fn blocked_removal_member(
    instance_id: String,
    display_name: String,
    code: impl AsRef<str>,
) -> LocalRemovalMemberSummary {
    LocalRemovalMemberSummary {
        instance_id,
        display_name,
        effect: None,
        code: Some(code.as_ref().to_string()),
    }
}

fn removal_display_name(instance: &LocalInstance) -> String {
    instance
        .name
        .as_deref()
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| "이름 정보 없음".to_string())
}

fn same_physical_removal_source(left: &InstanceHandle, right: &InstanceHandle) -> bool {
    left.canonical_root == right.canonical_root
        && left.root_device == right.root_device
        && left.root_inode == right.root_inode
        && left.raw_relative_components == right.raw_relative_components
}

fn source_owner_ids(publication: &LocalScanPublication, source: &InstanceHandle) -> Vec<String> {
    publication
        .snapshot
        .instances
        .iter()
        .filter(|instance| {
            publication
                .instance_handles
                .get(&instance.instance_id)
                .is_some_and(|handle| same_physical_removal_source(handle, source))
        })
        .map(|instance| instance.instance_id.clone())
        .collect()
}

fn block_removal_source_group(
    members: Vec<RemovalCandidate>,
    code: &'static str,
) -> Result<PreparedRemovalGroup, (Vec<RemovalCandidate>, &'static str)> {
    Err((members, code))
}

fn prepare_removal_source_group(
    publication: &LocalScanPublication,
    selected_ids: &BTreeSet<String>,
    source_group: Vec<RemovalCandidate>,
) -> Result<PreparedRemovalGroup, (Vec<RemovalCandidate>, &'static str)> {
    let Some(first) = source_group.first() else {
        return block_removal_source_group(source_group, "removal_plan_empty");
    };
    let owner_ids = source_owner_ids(publication, &first.handle);
    if owner_ids
        .iter()
        .any(|owner_id| !selected_ids.contains(owner_id))
    {
        return block_removal_source_group(source_group, "removal_source_unselected_owner");
    }
    if source_group
        .iter()
        .any(|candidate| candidate.instance.parse_state != ParseState::Parsed)
    {
        return block_removal_source_group(source_group, "removal_requires_parsed_source");
    }
    if source_group
        .iter()
        .any(|candidate| candidate.handle.scan_content_sha256.is_none())
    {
        return block_removal_source_group(source_group, "removal_requires_source_digest");
    }

    let mut members = Vec::with_capacity(source_group.len());
    for candidate in &source_group {
        let member = match RemovalMember::qualify(
            candidate.instance.instance_id.clone(),
            candidate.instance.kind,
            &candidate.handle,
        ) {
            Ok(member) => member,
            Err(error) => return block_removal_source_group(source_group, error.code()),
        };
        members.push(PreparedRemovalMember {
            summary: LocalRemovalMemberSummary {
                instance_id: candidate.instance.instance_id.clone(),
                display_name: candidate.display_name.clone(),
                effect: Some(removal_effect_kind(member.strategy())),
                code: None,
            },
            handle: candidate.handle.clone(),
            member,
        });
    }

    let Some(strategy) = members.first().map(|member| member.member.strategy()) else {
        return block_removal_source_group(source_group, "removal_plan_empty");
    };
    if members
        .iter()
        .any(|member| member.member.strategy() != strategy)
    {
        return block_removal_source_group(source_group, "unsupported_removal_shape");
    }
    if strategy == RemovalStrategy::DedicatedFileRemovalV1 && owner_ids.len() != 1 {
        return block_removal_source_group(source_group, "removal_dedicated_source_not_standalone");
    }

    Ok(PreparedRemovalGroup {
        strategy,
        members,
        prepared_content_sha256: [0; 32],
        json_selections: Vec::new(),
    })
}

fn removal_plan_deadline() -> Result<Instant, LocalContextError> {
    Instant::now()
        .checked_add(REMOVAL_PLAN_TTL)
        .ok_or_else(|| LocalContextError::new("removal_plan_unavailable"))
}

fn prepare_removal_group(group: &mut PreparedRemovalGroup) -> Result<(), String> {
    let first = group
        .members
        .first()
        .ok_or_else(|| "removal_plan_empty".to_string())?;
    let file = VerifiedLocalPathResolver::default()
        .resolve(&first.handle, VerificationPolicy::StrictSnapshotIdentity)
        .map_err(|error| error.code)?;
    let bytes = file.read_all_for_removal().map_err(|error| error.code)?;
    if group.members.iter().any(|member| {
        member
            .member
            .snapshot_identity()
            .content_sha256()
            .is_some_and(|expected| expected != sha256_bytes(&bytes))
    }) {
        return Err("changed_since_scan".to_string());
    }
    group.prepared_content_sha256 = sha256_bytes(&bytes);
    match group.strategy {
        RemovalStrategy::DedicatedFileRemovalV1 => Ok(()),
        RemovalStrategy::JsonPointerRemovalV1 => {
            let source: Value = serde_json::from_slice(&bytes)
                .map_err(|_| "malformed_removal_source".to_string())?;
            let mut selections = Vec::with_capacity(group.members.len());
            for member in &group.members {
                let locator = member
                    .handle
                    .config_entry_locator
                    .as_deref()
                    .ok_or_else(|| "unsupported_removal_shape".to_string())?;
                let pointer = locator
                    .strip_prefix('#')
                    .ok_or_else(|| "invalid_json_pointer".to_string())?;
                let expected = source
                    .pointer(pointer)
                    .cloned()
                    .ok_or_else(|| "json_pointer_not_found".to_string())?;
                let selection = member
                    .member
                    .json_selection(ExpectedJsonValue::new(expected))
                    .ok_or_else(|| "unsupported_removal_shape".to_string())?;
                selections.push(selection);
            }
            validate_json_removal(&source, &selections)
                .map_err(|error| error.code().to_string())?;
            group.json_selections = selections;
            Ok(())
        }
    }
}

fn apply_removal_group(group: &PreparedRemovalGroup) -> LocalRemovalSourceGroupOutcome {
    let member_ids = group
        .members
        .iter()
        .map(|member| member.summary.instance_id.clone())
        .collect::<Vec<_>>();
    let result = (|| -> Result<(), String> {
        let first = group
            .members
            .first()
            .ok_or_else(|| "removal_plan_empty".to_string())?;
        let file = VerifiedLocalPathResolver::default()
            .resolve(&first.handle, VerificationPolicy::StrictSnapshotIdentity)
            .map_err(|error| error.code)?;
        let bytes = file.read_all_for_removal().map_err(|error| error.code)?;
        if sha256_bytes(&bytes) != group.prepared_content_sha256 {
            return Err("changed_since_prepare".to_string());
        }
        match group.strategy {
            RemovalStrategy::DedicatedFileRemovalV1 => {
                file.unlink_for_removal().map_err(|error| error.code)
            }
            RemovalStrategy::JsonPointerRemovalV1 => {
                let original: Value = serde_json::from_slice(&bytes)
                    .map_err(|_| "malformed_removal_source".to_string())?;
                validate_json_removal(&original, &group.json_selections)
                    .map_err(|error| error.code().to_string())?;
                let mut rewritten = original.clone();
                apply_json_removal(&mut rewritten, &group.json_selections)
                    .map_err(|error| error.code().to_string())?;
                let serialized = serde_json::to_vec(&rewritten)
                    .map_err(|_| "removal_serialization_failed".to_string())?;
                let reparsed: Value = serde_json::from_slice(&serialized)
                    .map_err(|_| "removal_serialization_failed".to_string())?;
                verify_json_removal_semantics(&original, &reparsed, &group.json_selections)
                    .map_err(|error| error.code().to_string())?;
                file.replace_atomically_for_removal(&serialized)
                    .map_err(|error| error.code)
            }
        }
    })();
    removal_group_outcome(member_ids, result)
}

fn removal_group_outcome(
    member_ids: Vec<String>,
    result: Result<(), String>,
) -> LocalRemovalSourceGroupOutcome {
    match result {
        Ok(()) => LocalRemovalSourceGroupOutcome {
            state: LocalRemovalSourceGroupState::Success,
            member_ids,
            code: None,
        },
        Err(code) if code == "removal_state_indeterminate" => LocalRemovalSourceGroupOutcome {
            state: LocalRemovalSourceGroupState::Indeterminate,
            member_ids,
            code: Some(code),
        },
        Err(code) => LocalRemovalSourceGroupOutcome {
            state: LocalRemovalSourceGroupState::FailedUnchanged,
            member_ids,
            code: Some(code),
        },
    }
}

fn removal_outcomes_require_reconciliation(outcomes: &[LocalRemovalSourceGroupOutcome]) -> bool {
    outcomes.iter().any(|outcome| {
        matches!(
            outcome.state,
            LocalRemovalSourceGroupState::Success | LocalRemovalSourceGroupState::Indeterminate
        )
    })
}

fn sha256_bytes(bytes: &[u8]) -> [u8; 32] {
    Sha256::digest(bytes).into()
}

fn new_removal_plan_id(counter: &AtomicU64) -> Result<String, LocalContextError> {
    let sequence = counter.fetch_add(1, Ordering::Relaxed) + 1;
    let mut random = [0_u8; 16];
    getrandom::getrandom(&mut random)
        .map_err(|_| LocalContextError::new("removal_plan_unavailable"))?;
    let token = random
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    Ok(format!("local-removal-{sequence}-{token}"))
}

fn removal_plan_digest(
    plan_id: &str,
    snapshot_id: &str,
    members: &[LocalRemovalMemberSummary],
) -> String {
    let mut digest = Sha256::new();
    digest.update(b"harnesskit-local-removal-plan-v1\0");
    digest.update(plan_id.as_bytes());
    digest.update([0]);
    digest.update(snapshot_id.as_bytes());
    digest.update([0]);
    for member in members {
        digest.update(member.instance_id.as_bytes());
        digest.update([0]);
    }
    format!("{:x}", digest.finalize())
}

fn terminal_payload(result: &LocalScanExecutorResult) -> LocalScanEventPayload {
    match result {
        LocalScanExecutorResult::Complete(publication) => LocalScanEventPayload::Complete {
            snapshot_id: publication.snapshot.snapshot_id.clone(),
        },
        LocalScanExecutorResult::Partial { publication, .. } => LocalScanEventPayload::Partial {
            snapshot_id: publication.snapshot.snapshot_id.clone(),
        },
        LocalScanExecutorResult::Failed { code, .. } => {
            LocalScanEventPayload::Failed { code: code.clone() }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;
    use std::fs;
    use std::os::unix::fs::MetadataExt;
    use std::path::Path;
    use std::sync::atomic::{AtomicBool, AtomicUsize};
    use std::sync::{Condvar, Mutex};
    use std::time::{Duration, Instant};

    use tempfile::tempdir;

    use super::*;
    use crate::contexts::local::path_action::{NativePathActionError, VerifiedNativePath};
    use crate::contexts::local::scanner::LocalScanner;
    use crate::contexts::local::{AdapterCatalog, LocalAttemptState, LocalSnapshotStatus, ToolId};
    use crate::operation_coordinator::BeginInstallError;
    use crate::projection::root_identity;

    #[derive(Clone)]
    struct FixedExecutor(LocalScanPublication);

    impl LocalScanExecutor for FixedExecutor {
        fn execute(
            &self,
            _attempt_id: &str,
            _progress: &dyn LocalScanProgressPort,
        ) -> LocalScanExecutorResult {
            LocalScanExecutorResult::Complete(self.0.clone())
        }
    }

    struct RejectingWorkerSpawner;

    impl LocalWorkerSpawner for RejectingWorkerSpawner {
        fn spawn(&self, _worker: Box<dyn FnOnce() + Send + 'static>) -> Result<(), ()> {
            Err(())
        }
    }

    struct InlineWorkerSpawner;

    impl LocalWorkerSpawner for InlineWorkerSpawner {
        fn spawn(&self, worker: Box<dyn FnOnce() + Send + 'static>) -> Result<(), ()> {
            worker();
            Ok(())
        }
    }

    type QueuedWorker = Box<dyn FnOnce() + Send + 'static>;

    struct GateProbeQueuedWorkerSpawner {
        operations: Arc<OperationCoordinator>,
        scheduler: Mutex<Option<Arc<Mutex<LocalScanSchedulerState>>>>,
        workers: Mutex<VecDeque<QueuedWorker>>,
        spawn_count: AtomicUsize,
        saw_locked_scheduler: AtomicBool,
        primary_busy_at_spawn: Mutex<Vec<bool>>,
    }

    impl GateProbeQueuedWorkerSpawner {
        fn new(operations: Arc<OperationCoordinator>) -> Self {
            Self {
                operations,
                scheduler: Mutex::new(None),
                workers: Mutex::new(VecDeque::new()),
                spawn_count: AtomicUsize::new(0),
                saw_locked_scheduler: AtomicBool::new(false),
                primary_busy_at_spawn: Mutex::new(Vec::new()),
            }
        }

        fn attach_scheduler(&self, scheduler: Arc<Mutex<LocalScanSchedulerState>>) {
            *self.scheduler.lock().unwrap() = Some(scheduler);
        }

        fn pending(&self) -> usize {
            self.workers.lock().unwrap().len()
        }

        fn run_next(&self) {
            let worker = self.workers.lock().unwrap().pop_front().unwrap();
            worker();
        }
    }

    impl LocalWorkerSpawner for GateProbeQueuedWorkerSpawner {
        fn spawn(&self, worker: QueuedWorker) -> Result<(), ()> {
            let ordinal = self.spawn_count.fetch_add(1, Ordering::SeqCst) + 1;
            let scheduler = self.scheduler.lock().unwrap().clone().unwrap();
            if scheduler.try_lock().is_err() {
                self.saw_locked_scheduler.store(true, Ordering::SeqCst);
            }
            let install_probe = self.operations.begin_install(
                &format!("local-spawn-probe-{ordinal}"),
                "probe",
                None,
            );
            let primary_busy = matches!(install_probe, Err(BeginInstallError::OperationBusy));
            self.primary_busy_at_spawn
                .lock()
                .unwrap()
                .push(primary_busy);
            self.workers.lock().unwrap().push_back(worker);
            Ok(())
        }
    }

    struct RejectSuccessorWorkerSpawner {
        workers: Mutex<VecDeque<QueuedWorker>>,
        spawn_count: AtomicUsize,
    }

    impl RejectSuccessorWorkerSpawner {
        fn new() -> Self {
            Self {
                workers: Mutex::new(VecDeque::new()),
                spawn_count: AtomicUsize::new(0),
            }
        }

        fn run_next(&self) {
            let worker = self.workers.lock().unwrap().pop_front().unwrap();
            worker();
        }
    }

    impl LocalWorkerSpawner for RejectSuccessorWorkerSpawner {
        fn spawn(&self, worker: QueuedWorker) -> Result<(), ()> {
            let ordinal = self.spawn_count.fetch_add(1, Ordering::SeqCst) + 1;
            if ordinal == 2 {
                return Err(());
            }
            self.workers.lock().unwrap().push_back(worker);
            Ok(())
        }
    }

    struct PanickingRunningEventPort;

    impl LocalScanEventPort for PanickingRunningEventPort {
        fn emit_changed(&self, event: &LocalScanEventEnvelope) -> Result<(), LocalScanEventError> {
            if event.payload == LocalScanEventPayload::Running {
                panic!("running event panic fixture");
            }
            Ok(())
        }
    }

    struct SchedulerProbeEventPort {
        operations: Arc<OperationCoordinator>,
        scheduler: Mutex<Option<Arc<Mutex<LocalScanSchedulerState>>>>,
        event_count: AtomicUsize,
        saw_locked_scheduler: AtomicBool,
        primary_busy_on_terminal: Mutex<Vec<bool>>,
    }

    #[derive(Default)]
    struct BlockingFirstTerminalEventPort {
        blocked_once: AtomicBool,
        entered: (Mutex<bool>, Condvar),
        released: (Mutex<bool>, Condvar),
    }

    #[derive(Default)]
    struct BlockingFirstRunningEventPort {
        entered: (Mutex<bool>, Condvar),
        released: (Mutex<bool>, Condvar),
    }

    impl BlockingFirstRunningEventPort {
        fn wait_until_entered(&self) {
            let (lock, wake) = &self.entered;
            let entered = lock.lock().unwrap();
            let (entered, timeout) = wake
                .wait_timeout_while(entered, Duration::from_secs(1), |entered| !*entered)
                .unwrap();
            assert!(
                *entered && !timeout.timed_out(),
                "Running event did not enter the blocking fixture"
            );
        }

        fn release(&self) {
            let (lock, wake) = &self.released;
            *lock.lock().unwrap() = true;
            wake.notify_all();
        }
    }

    impl LocalScanEventPort for BlockingFirstRunningEventPort {
        fn emit_changed(&self, event: &LocalScanEventEnvelope) -> Result<(), LocalScanEventError> {
            if event.payload != LocalScanEventPayload::Running {
                return Ok(());
            }
            let (entered_lock, entered_wake) = &self.entered;
            *entered_lock.lock().unwrap() = true;
            entered_wake.notify_all();

            let (release_lock, release_wake) = &self.released;
            let released = release_lock.lock().unwrap();
            let released = release_wake
                .wait_while(released, |released| !*released)
                .unwrap();
            assert!(*released);
            Ok(())
        }
    }

    impl BlockingFirstTerminalEventPort {
        fn wait_until_entered(&self) {
            let (lock, wake) = &self.entered;
            let entered = lock.lock().unwrap();
            let (entered, timeout) = wake
                .wait_timeout_while(entered, Duration::from_secs(1), |entered| !*entered)
                .unwrap();
            assert!(
                *entered && !timeout.timed_out(),
                "terminal event did not enter the blocking fixture"
            );
        }

        fn release(&self) {
            let (lock, wake) = &self.released;
            *lock.lock().unwrap() = true;
            wake.notify_all();
        }
    }

    impl LocalScanEventPort for BlockingFirstTerminalEventPort {
        fn emit_changed(&self, event: &LocalScanEventEnvelope) -> Result<(), LocalScanEventError> {
            let terminal = matches!(
                event.payload,
                LocalScanEventPayload::Complete { .. }
                    | LocalScanEventPayload::Partial { .. }
                    | LocalScanEventPayload::Failed { .. }
            );
            if terminal && !self.blocked_once.swap(true, Ordering::SeqCst) {
                let (entered_lock, entered_wake) = &self.entered;
                *entered_lock.lock().unwrap() = true;
                entered_wake.notify_all();

                let (release_lock, release_wake) = &self.released;
                let released = release_lock.lock().unwrap();
                let released = release_wake
                    .wait_while(released, |released| !*released)
                    .unwrap();
                assert!(*released);
            }
            Ok(())
        }
    }

    impl SchedulerProbeEventPort {
        fn new(operations: Arc<OperationCoordinator>) -> Self {
            Self {
                operations,
                scheduler: Mutex::new(None),
                event_count: AtomicUsize::new(0),
                saw_locked_scheduler: AtomicBool::new(false),
                primary_busy_on_terminal: Mutex::new(Vec::new()),
            }
        }

        fn attach_scheduler(&self, scheduler: Arc<Mutex<LocalScanSchedulerState>>) {
            *self.scheduler.lock().unwrap() = Some(scheduler);
        }
    }

    impl LocalScanEventPort for SchedulerProbeEventPort {
        fn emit_changed(&self, event: &LocalScanEventEnvelope) -> Result<(), LocalScanEventError> {
            let scheduler = self.scheduler.lock().unwrap().clone().unwrap();
            if scheduler.try_lock().is_err() {
                self.saw_locked_scheduler.store(true, Ordering::SeqCst);
            }
            let _ = self.operations.probe_active_local_reads();
            let ordinal = self.event_count.fetch_add(1, Ordering::SeqCst) + 1;
            if matches!(
                &event.payload,
                LocalScanEventPayload::Complete { .. }
                    | LocalScanEventPayload::Partial { .. }
                    | LocalScanEventPayload::Failed { .. }
            ) {
                let install_probe = self.operations.begin_install(
                    &format!("local-terminal-event-probe-{ordinal}"),
                    "probe",
                    None,
                );
                self.primary_busy_on_terminal.lock().unwrap().push(matches!(
                    install_probe,
                    Err(BeginInstallError::OperationBusy)
                ));
            }
            Ok(())
        }
    }

    #[derive(Default)]
    struct BlockingNativePort {
        entered: (Mutex<bool>, Condvar),
        released: (Mutex<bool>, Condvar),
    }

    impl BlockingNativePort {
        fn wait_until_entered(&self) {
            let (lock, wake) = &self.entered;
            let entered = lock.lock().unwrap();
            let (entered, timeout) = wake
                .wait_timeout_while(entered, Duration::from_secs(1), |entered| !*entered)
                .unwrap();
            assert!(
                *entered && !timeout.timed_out(),
                "native action did not start"
            );
        }

        fn release(&self) {
            let (lock, wake) = &self.released;
            *lock.lock().unwrap() = true;
            wake.notify_all();
        }

        fn block(&self) {
            let (entered_lock, entered_wake) = &self.entered;
            *entered_lock.lock().unwrap() = true;
            entered_wake.notify_all();

            let (release_lock, release_wake) = &self.released;
            let released = release_lock.lock().unwrap();
            let released = release_wake
                .wait_while(released, |released| !*released)
                .unwrap();
            assert!(*released);
        }
    }

    impl NativePathActionPort for BlockingNativePort {
        fn reveal(&self, _target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError> {
            self.block();
            Ok(())
        }

        fn copy_path(&self, _target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError> {
            self.block();
            Ok(())
        }
    }

    fn write(path: &Path, body: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, body).unwrap();
    }

    fn publication(home: &Path) -> (LocalScanPublication, String) {
        let catalog = AdapterCatalog::load_embedded().unwrap();
        let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
        let output = LocalScanner::scan_with_handles(home, &[adapter]).unwrap();
        let instance_id = output
            .result
            .instances
            .iter()
            .find(|instance| instance.name.as_deref() == Some("release"))
            .unwrap()
            .instance_id
            .clone();
        (
            LocalScanPublication {
                snapshot: super::super::LocalSnapshot {
                    snapshot_id: "shared-gate-snapshot".to_string(),
                    attempt_id: "shared-gate-attempt".to_string(),
                    status: LocalSnapshotStatus::Complete,
                    scan_timestamp: "2026-07-13T00:00:00Z".to_string(),
                    app_version: "test".to_string(),
                    adapter_set_fingerprint: "a".repeat(64),
                    content_fingerprint: "b".repeat(64),
                    qualified_tools: output.result.qualified_tools,
                    instances: output.result.instances,
                    coverage: output.result.coverage,
                    skipped_paths: output.result.skipped_paths,
                    issues: output.result.issues,
                    project_ignore_summaries: output.result.project_ignore_summaries,
                },
                instance_handle_ids: vec![instance_id.clone()],
                instance_handles: output.instance_handles,
                project_locations: output.project_locations,
            },
            instance_id,
        )
    }

    #[test]
    fn local_context_path_action_and_install_share_the_same_runtime_gate() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        write(
            &home.join(".codex/skills/release/SKILL.md"),
            "---\nname: release\ndescription: fixture\n---\nbody\n",
        );
        let (publication, instance_id) = publication(&home);
        let snapshot_id = publication.snapshot.snapshot_id.clone();
        let operations = Arc::new(OperationCoordinator::default());
        let port = Arc::new(BlockingNativePort::default());
        let context = Arc::new(
            LocalContext::with_executor_operations_events_and_path_actions(
                Arc::new(FixedExecutor(publication)),
                Arc::clone(&operations),
                Arc::new(NoopLocalScanEventPort),
                port.clone(),
            ),
        );
        context.start_scan().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while context.state().latest_complete.is_none() {
            assert!(Instant::now() < deadline, "publication timeout");
            std::thread::yield_now();
        }

        let action = {
            let context = Arc::clone(&context);
            std::thread::spawn(move || {
                context.act_on_instance(&snapshot_id, &instance_id, LocalPathAction::Reveal)
            })
        };
        port.wait_until_entered();
        assert!(matches!(
            operations.begin_install("blocked", "preview", None),
            Err(BeginInstallError::OperationBusy)
        ));
        port.release();
        assert_eq!(
            action.join().unwrap().unwrap(),
            LocalPathActionOutcome::Revealed
        );
        assert!(operations.begin_install("allowed", "preview", None).is_ok());
    }

    #[test]
    fn local_contexts_share_scheduling_but_not_snapshot_or_handle_authority() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        write(
            &home.join(".codex/skills/release/SKILL.md"),
            "---\nname: release\ndescription: fixture\n---\nbody\n",
        );
        let (publication, _) = publication(&home);
        let operations = Arc::new(OperationCoordinator::default());
        let first = LocalContext::with_executor_operations_and_events(
            Arc::new(FixedExecutor(publication)),
            Arc::clone(&operations),
            Arc::new(NoopLocalScanEventPort),
        );
        let second = LocalContext::with_executor_operations_and_events(
            Arc::new(UnavailableExecutor),
            operations,
            Arc::new(NoopLocalScanEventPort),
        );

        first.start_scan().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while first.state().latest_complete.is_none() {
            assert!(Instant::now() < deadline, "publication timeout");
            std::thread::yield_now();
        }

        assert_eq!(second.state(), LocalSessionState::default());
        assert_eq!(
            second.publication("shared-gate-snapshot"),
            Err("snapshot_expired".to_string())
        );
    }

    #[test]
    fn worker_spawn_failure_publishes_terminal_failure_and_releases_the_gate() {
        let operations = Arc::new(OperationCoordinator::default());
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(UnavailableExecutor),
            Arc::clone(&operations),
            Arc::new(NoopLocalScanEventPort),
        );
        context.worker_spawner = Arc::new(RejectingWorkerSpawner);

        assert_eq!(
            context.start_scan(),
            Err(LocalContextError {
                code: "worker_aborted".to_string(),
                line: None,
                column: None,
            })
        );
        let state = context.state();
        assert!(state.current_attempt.is_none());
        let terminal = state.latest_terminal_report.expect("terminal failure");
        assert_eq!(
            terminal.state,
            super::super::store::LocalAttemptState::Failed
        );
        assert_eq!(terminal.error_code.as_deref(), Some("worker_aborted"));
        assert!(operations
            .begin_install("after-spawn-failure", "preview", None)
            .is_ok());
    }

    #[test]
    fn initial_worker_spawn_and_gate_release_do_not_wait_for_a_blocking_running_event() {
        let operations = Arc::new(OperationCoordinator::default());
        let spawner = Arc::new(GateProbeQueuedWorkerSpawner::new(Arc::clone(&operations)));
        let events = Arc::new(BlockingFirstRunningEventPort::default());
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(UnavailableExecutor),
            Arc::clone(&operations),
            events.clone(),
        );
        context.worker_spawner = spawner.clone();
        spawner.attach_scheduler(Arc::clone(&context.scheduler));
        let context = Arc::new(context);

        let start = {
            let context = Arc::clone(&context);
            std::thread::spawn(move || context.start_scan())
        };
        events.wait_until_entered();
        let worker_pending_while_blocked = spawner.pending();
        let gate_released_while_blocked = if worker_pending_while_blocked == 1 {
            spawner.run_next();
            operations
                .begin_install("after-blocked-running-event", "preview", None)
                .is_ok()
        } else {
            false
        };
        events.release();
        let outcome = start.join().unwrap().unwrap();

        assert!(matches!(outcome, StartLocalScanOutcome::Accepted { .. }));
        assert_eq!(
            worker_pending_while_blocked, 1,
            "initial worker must be spawned before Running event delivery"
        );
        assert!(gate_released_while_blocked);
        assert_eq!(
            spawner.primary_busy_at_spawn.lock().unwrap().as_slice(),
            [true],
            "initial worker dispatch must observe the active scan gate"
        );
    }

    #[test]
    fn terminal_store_error_falls_back_to_failed_and_releases_the_gate() {
        let operations = Arc::new(OperationCoordinator::default());
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(UnavailableExecutor),
            Arc::clone(&operations),
            Arc::new(NoopLocalScanEventPort),
        );
        context.worker_spawner = Arc::new(InlineWorkerSpawner);
        context.authority.fail_next_finish();

        assert!(matches!(
            context.start_scan().unwrap(),
            StartLocalScanOutcome::Accepted { .. }
        ));
        let state = context.state();
        assert!(state.current_attempt.is_none());
        let terminal = state.latest_terminal_report.expect("fallback terminal");
        assert_eq!(
            terminal.state,
            super::super::store::LocalAttemptState::Failed
        );
        assert_eq!(terminal.error_code.as_deref(), Some("worker_aborted"));
        assert!(operations
            .begin_install("after-terminal-error", "preview", None)
            .is_ok());
    }

    #[test]
    fn failed_handoff_drops_no_successor_guard_under_the_scheduler_lock() {
        let operations = Arc::new(OperationCoordinator::default());
        let spawner = Arc::new(GateProbeQueuedWorkerSpawner::new(Arc::clone(&operations)));
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(UnavailableExecutor),
            Arc::clone(&operations),
            Arc::new(NoopLocalScanEventPort),
        );
        context.worker_spawner = spawner.clone();
        spawner.attach_scheduler(Arc::clone(&context.scheduler));

        let StartLocalScanOutcome::Accepted { attempt_id } = context.start_scan().unwrap() else {
            panic!("initial scan must be accepted");
        };
        assert!(matches!(
            context.request_ignore_save_rescan().unwrap(),
            IgnoreSaveRescanOutcome::QueuedAfterCurrent { .. }
        ));
        context
            .authority
            .finish_attempt(
                &attempt_id,
                LocalScanExecutorResult::Failed {
                    code: "preterminalized".to_string(),
                    diagnostics: None,
                },
            )
            .unwrap();

        spawner.run_next();

        assert_eq!(spawner.spawn_count.load(Ordering::SeqCst), 1);
        assert!(!spawner.saw_locked_scheduler.load(Ordering::SeqCst));
        let scheduler = context.scheduler.lock().unwrap();
        assert!(scheduler.running_attempt_id.is_none());
        assert!(!scheduler.follow_up_after_terminal);
        drop(scheduler);
        assert_eq!(
            context
                .state()
                .latest_terminal_report
                .and_then(|terminal| terminal.error_code),
            Some("preterminalized".to_string())
        );
        assert!(operations
            .begin_install("after-handoff-failure", "preview", None)
            .is_ok());
    }

    #[test]
    fn running_event_panic_cannot_leak_the_attempt_or_primary_gate() {
        let operations = Arc::new(OperationCoordinator::default());
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(UnavailableExecutor),
            Arc::clone(&operations),
            Arc::new(PanickingRunningEventPort),
        );
        context.worker_spawner = Arc::new(InlineWorkerSpawner);

        let outcome =
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| context.start_scan()));
        assert!(outcome.is_ok(), "event adapter panic escaped start_scan");
        assert!(context.state().current_attempt.is_none());
        assert!(operations
            .begin_install("after-event-panic", "preview", None)
            .is_ok());
    }

    #[test]
    fn start_and_finalize_emit_events_only_after_releasing_scheduler() {
        let operations = Arc::new(OperationCoordinator::default());
        let events = Arc::new(SchedulerProbeEventPort::new(Arc::clone(&operations)));
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(UnavailableExecutor),
            operations,
            events.clone(),
        );
        context.worker_spawner = Arc::new(InlineWorkerSpawner);
        events.attach_scheduler(Arc::clone(&context.scheduler));

        assert!(matches!(
            context.start_scan().unwrap(),
            StartLocalScanOutcome::Accepted { .. }
        ));
        assert_eq!(events.event_count.load(Ordering::SeqCst), 2);
        assert!(
            !events.saw_locked_scheduler.load(Ordering::SeqCst),
            "event port observed the scheduler mutex held"
        );
    }

    #[test]
    fn repeated_ignore_rescan_requests_coalesce_to_one_successor_without_idle_handoff() {
        let operations = Arc::new(OperationCoordinator::default());
        let spawner = Arc::new(GateProbeQueuedWorkerSpawner::new(Arc::clone(&operations)));
        let events = Arc::new(SchedulerProbeEventPort::new(Arc::clone(&operations)));
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(UnavailableExecutor),
            operations,
            events.clone(),
        );
        context.worker_spawner = spawner.clone();
        spawner.attach_scheduler(Arc::clone(&context.scheduler));
        events.attach_scheduler(Arc::clone(&context.scheduler));

        let StartLocalScanOutcome::Accepted { attempt_id } = context.start_scan().unwrap() else {
            panic!("initial scan must be accepted");
        };
        for _ in 0..8 {
            assert_eq!(
                context.request_ignore_save_rescan().unwrap(),
                IgnoreSaveRescanOutcome::QueuedAfterCurrent {
                    current_attempt_id: attempt_id.clone(),
                }
            );
        }
        assert_eq!(spawner.pending(), 1);

        spawner.run_next();

        assert_eq!(spawner.spawn_count.load(Ordering::SeqCst), 2);
        assert_eq!(spawner.pending(), 1, "exactly one successor is prepared");
        assert_eq!(
            spawner.primary_busy_at_spawn.lock().unwrap().as_slice(),
            [true, true],
            "the coordinator must remain non-idle through successor handoff"
        );
        assert_eq!(
            events.primary_busy_on_terminal.lock().unwrap().as_slice(),
            [true],
            "the first terminal event must observe the prepared successor, not Idle"
        );
        assert!(!spawner.saw_locked_scheduler.load(Ordering::SeqCst));
        assert!(!events.saw_locked_scheduler.load(Ordering::SeqCst));
        assert_eq!(
            context
                .state()
                .current_attempt
                .as_ref()
                .map(|attempt| attempt.attempt_id.as_str()),
            Some("local-scan-2")
        );

        spawner.run_next();

        assert_eq!(spawner.spawn_count.load(Ordering::SeqCst), 2);
        assert_eq!(spawner.pending(), 0);
        assert!(context.state().current_attempt.is_none());
        assert_eq!(
            events.primary_busy_on_terminal.lock().unwrap().as_slice(),
            [true, false]
        );
    }

    #[test]
    fn successful_removal_rescan_handoff_is_accepted_while_install_competes_at_dispatch() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let skill = home.join(".codex/skills/release/SKILL.md");
        write(
            &skill,
            "---\nname: release\ndescription: fixture\n---\nbody\n",
        );
        let (publication, instance_id) = publication(&home);
        let snapshot_id = publication.snapshot.snapshot_id.clone();
        let operations = Arc::new(OperationCoordinator::default());
        let spawner = Arc::new(GateProbeQueuedWorkerSpawner::new(Arc::clone(&operations)));
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(FixedExecutor(publication)),
            Arc::clone(&operations),
            Arc::new(NoopLocalScanEventPort),
        );
        context.worker_spawner = spawner.clone();
        spawner.attach_scheduler(Arc::clone(&context.scheduler));

        assert!(matches!(
            context.start_scan().unwrap(),
            StartLocalScanOutcome::Accepted { .. }
        ));
        spawner.run_next();

        let plan = context
            .prepare_local_removal(&snapshot_id, std::slice::from_ref(&instance_id))
            .unwrap();
        let outcome = context
            .apply_local_removal(&plan.plan_id, &plan.plan_digest, true)
            .unwrap();

        assert_eq!(outcome.rescan_state, LocalRemovalRescanState::Accepted);
        assert_eq!(spawner.pending(), 1);
        assert_eq!(
            spawner.primary_busy_at_spawn.lock().unwrap().as_slice(),
            [true, true],
            "the dispatch-time install probe must not observe Idle after mutation"
        );

        spawner.run_next();
        assert!(operations
            .begin_install("after-removal-rescan", "preview", None)
            .is_ok());
    }

    #[test]
    fn rejected_removal_rescan_spawn_preserves_group_outcomes_and_never_claims_acceptance() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let skill = home.join(".codex/skills/release/SKILL.md");
        write(
            &skill,
            "---\nname: release\ndescription: fixture\n---\nbody\n",
        );
        let (publication, instance_id) = publication(&home);
        let snapshot_id = publication.snapshot.snapshot_id.clone();
        let operations = Arc::new(OperationCoordinator::default());
        let spawner = Arc::new(RejectSuccessorWorkerSpawner::new());
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(FixedExecutor(publication)),
            Arc::clone(&operations),
            Arc::new(NoopLocalScanEventPort),
        );
        context.worker_spawner = spawner.clone();

        assert!(matches!(
            context.start_scan().unwrap(),
            StartLocalScanOutcome::Accepted { .. }
        ));
        spawner.run_next();

        let plan = context
            .prepare_local_removal(&snapshot_id, std::slice::from_ref(&instance_id))
            .unwrap();
        let outcome = context
            .apply_local_removal(&plan.plan_id, &plan.plan_digest, true)
            .unwrap();

        assert_eq!(outcome.rescan_state, LocalRemovalRescanState::FailedToStart);
        assert_eq!(outcome.rescan_attempt_id.as_deref(), Some("local-scan-2"));
        assert_eq!(outcome.source_group_outcomes.len(), 1);
        assert_eq!(
            outcome.source_group_outcomes[0].state,
            LocalRemovalSourceGroupState::Success
        );
        assert!(!skill.exists());
        let terminal = context.state().latest_terminal_report.unwrap();
        assert_eq!(terminal.attempt_id, "local-scan-2");
        assert_eq!(terminal.state, LocalAttemptState::Failed);
        assert_eq!(terminal.error_code.as_deref(), Some("worker_aborted"));
        assert!(operations
            .begin_install("after-removal-rescan-spawn-failure", "preview", None)
            .is_ok());
    }

    #[test]
    fn successor_spawn_and_gate_release_do_not_wait_for_a_blocking_terminal_event() {
        let operations = Arc::new(OperationCoordinator::default());
        let spawner = Arc::new(GateProbeQueuedWorkerSpawner::new(Arc::clone(&operations)));
        let events = Arc::new(BlockingFirstTerminalEventPort::default());
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(UnavailableExecutor),
            Arc::clone(&operations),
            events.clone(),
        );
        context.worker_spawner = spawner.clone();
        spawner.attach_scheduler(Arc::clone(&context.scheduler));

        assert!(matches!(
            context.start_scan().unwrap(),
            StartLocalScanOutcome::Accepted { .. }
        ));
        assert!(matches!(
            context.request_ignore_save_rescan().unwrap(),
            IgnoreSaveRescanOutcome::QueuedAfterCurrent { .. }
        ));

        let first_worker = {
            let spawner = spawner.clone();
            std::thread::spawn(move || spawner.run_next())
        };
        events.wait_until_entered();
        let successor_pending_while_blocked = spawner.pending();
        let gate_released_while_blocked = if successor_pending_while_blocked == 1 {
            spawner.run_next();
            operations
                .begin_install("after-blocked-terminal-event", "preview", None)
                .is_ok()
        } else {
            false
        };
        events.release();
        first_worker.join().unwrap();

        assert_eq!(
            successor_pending_while_blocked, 1,
            "the prepared successor must be spawned before terminal event delivery"
        );
        assert!(gate_released_while_blocked);
        assert_eq!(
            spawner.primary_busy_at_spawn.lock().unwrap().as_slice(),
            [true, true],
            "atomic handoff must not expose Idle before successor execution"
        );
    }

    #[test]
    fn rejected_prepared_successor_terminalizes_and_releases_primary_gate() {
        let operations = Arc::new(OperationCoordinator::default());
        let spawner = Arc::new(RejectSuccessorWorkerSpawner::new());
        let mut context = LocalContext::with_executor_operations_and_events(
            Arc::new(UnavailableExecutor),
            Arc::clone(&operations),
            Arc::new(NoopLocalScanEventPort),
        );
        context.worker_spawner = spawner.clone();

        assert!(matches!(
            context.start_scan().unwrap(),
            StartLocalScanOutcome::Accepted { .. }
        ));
        assert!(matches!(
            context.request_ignore_save_rescan().unwrap(),
            IgnoreSaveRescanOutcome::QueuedAfterCurrent { .. }
        ));

        spawner.run_next();

        assert_eq!(spawner.spawn_count.load(Ordering::SeqCst), 2);
        let state = context.state();
        assert!(state.current_attempt.is_none());
        let terminal = state.latest_terminal_report.expect("successor terminal");
        assert_eq!(terminal.attempt_id, "local-scan-2");
        assert_eq!(
            terminal.state,
            super::super::store::LocalAttemptState::Failed
        );
        assert_eq!(terminal.error_code.as_deref(), Some("worker_aborted"));
        assert!(operations
            .begin_install("after-successor-spawn-failure", "preview", None)
            .is_ok());
    }

    #[test]
    fn project_ignore_validation_location_is_preserved_by_get_and_save_errors() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let project_root = fixture.path().join("project");
        write(
            &home.join(".codex/skills/release/SKILL.md"),
            "---\nname: release\ndescription: fixture\n---\nbody\n",
        );
        write(
            &project_root.join(".harnesskitignore"),
            "valid/\n../outside\n",
        );
        let project_root = fs::canonicalize(project_root).unwrap();
        let project_metadata = fs::metadata(&project_root).unwrap();
        let (mut publication, _) = publication(&home);
        let snapshot_id = publication.snapshot.snapshot_id.clone();
        publication.project_locations.insert(
            "project".to_owned(),
            ProjectLocationRecord {
                project_id: "project".to_owned(),
                display_name: "project".into(),
                canonical_path: project_root.clone(),
                root_identity: root_identity(
                    project_metadata.dev(),
                    project_metadata.ino(),
                    &project_root,
                ),
                root_device: project_metadata.dev(),
                root_inode: project_metadata.ino(),
                owner_uid: project_metadata.uid(),
            },
        );
        let context = LocalContext::default();
        context
            .publish_scan_result_for_test(
                "seed-attempt",
                LocalScanExecutorResult::Complete(publication),
            )
            .unwrap();

        let expected = LocalContextError {
            code: "project_ignore_invalid".to_owned(),
            line: Some(2),
            column: Some(1),
        };
        assert_eq!(
            context
                .get_project_ignore(&snapshot_id, "project")
                .expect_err("invalid stored ignore rule"),
            expected
        );
        let save_error = context
            .save_project_ignore_and_rescan(
                &snapshot_id,
                "project",
                "fixture-revision",
                "valid/\n../outside\n",
            )
            .expect_err("invalid ignore rule");

        assert_eq!(save_error, expected);
    }

    #[test]
    fn removal_plans_expire_on_deadline_and_snapshot_replacement_generation() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let skill = home.join(".codex/skills/release/SKILL.md");
        write(
            &skill,
            "---\nname: release\ndescription: fixture\n---\nbody\n",
        );
        let (publication, instance_id) = publication(&home);
        let snapshot_id = publication.snapshot.snapshot_id.clone();
        let context = LocalContext::default();
        context
            .publish_scan_result_for_test(
                "initial-publication",
                LocalScanExecutorResult::Complete(publication.clone()),
            )
            .unwrap();

        let expired_plan = context
            .prepare_local_removal(&snapshot_id, std::slice::from_ref(&instance_id))
            .unwrap();
        context
            .removal_plans
            .lock()
            .unwrap()
            .get_mut(&expired_plan.plan_id)
            .unwrap()
            .expires_at = Instant::now();
        assert_eq!(
            context
                .apply_local_removal(&expired_plan.plan_id, &expired_plan.plan_digest, true)
                .unwrap_err()
                .code,
            "removal_plan_expired"
        );
        assert!(skill.exists());

        let replaced_plan = context
            .prepare_local_removal(&snapshot_id, std::slice::from_ref(&instance_id))
            .unwrap();
        context
            .publish_scan_result_for_test(
                "replacement-publication",
                LocalScanExecutorResult::Complete(publication),
            )
            .unwrap();
        assert_eq!(
            context
                .apply_local_removal(&replaced_plan.plan_id, &replaced_plan.plan_digest, true,)
                .unwrap_err()
                .code,
            "removal_plan_expired"
        );
        assert!(skill.exists());
    }

    #[test]
    fn indeterminate_removal_group_preserves_member_selection_and_requires_reconciliation() {
        let member_ids = vec!["instance-a".to_string(), "instance-b".to_string()];
        let indeterminate = removal_group_outcome(
            member_ids.clone(),
            Err("removal_state_indeterminate".to_string()),
        );

        assert_eq!(
            indeterminate.state,
            LocalRemovalSourceGroupState::Indeterminate
        );
        assert_eq!(indeterminate.member_ids, member_ids);
        assert_eq!(
            indeterminate.code.as_deref(),
            Some("removal_state_indeterminate")
        );
        assert!(removal_outcomes_require_reconciliation(&[indeterminate]));

        let unchanged = removal_group_outcome(
            vec!["instance-c".to_string()],
            Err("changed_since_prepare".to_string()),
        );
        assert_eq!(
            unchanged.state,
            LocalRemovalSourceGroupState::FailedUnchanged
        );
        assert!(!removal_outcomes_require_reconciliation(&[unchanged]));
    }
}
