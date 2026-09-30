//! Scan, path-action, and install operation coordination shell.

use std::sync::Arc;
use std::sync::Mutex;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum PrimaryOperation {
    Idle,
    LocalScan(String),
    LocalRemoval(String),
    Install {
        operation_id: String,
        phase: String,
        preview_id: Option<String>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum BeginLocalScanError {
    AlreadyRunning(String),
    OperationBusy,
    Invariant(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum BeginInstallError {
    OperationBusy,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum BeginLocalRemovalError {
    OperationBusy,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum BeginLocalReadError {
    OperationBusy,
    SnapshotExpired,
    InstanceHandleMissing,
    ReconciliationNotAuthoritative,
    CapacityExhausted,
}

impl BeginLocalReadError {
    pub(crate) const fn code(&self) -> &'static str {
        match self {
            Self::OperationBusy => "operation_busy",
            Self::SnapshotExpired => "snapshot_expired",
            Self::InstanceHandleMissing => "instance_handle_missing",
            Self::ReconciliationNotAuthoritative => "removal_reconciliation_not_authoritative",
            Self::CapacityExhausted => "local_read_capacity_exhausted",
        }
    }

    pub(crate) fn from_lookup_code(code: &str) -> Self {
        match code {
            "snapshot_expired" => Self::SnapshotExpired,
            "removal_reconciliation_not_authoritative" => Self::ReconciliationNotAuthoritative,
            _ => Self::InstanceHandleMissing,
        }
    }
}

pub(crate) struct InstallOperationGuard {
    coordinator: Arc<OperationCoordinator>,
    operation_id: String,
}

pub(crate) struct LocalRemovalGuard {
    coordinator: Arc<OperationCoordinator>,
    plan_id: String,
}

struct LocalReadGuard {
    coordinator: Arc<OperationCoordinator>,
}

pub(crate) struct LocalReadLease<T> {
    _guard: LocalReadGuard,
    value: T,
}

impl<T> LocalReadLease<T> {
    pub(crate) fn value(&self) -> &T {
        &self.value
    }
}

impl Drop for LocalReadGuard {
    fn drop(&mut self) {
        self.coordinator.finish_local_read();
    }
}

impl Drop for InstallOperationGuard {
    fn drop(&mut self) {
        self.coordinator.finish_install(&self.operation_id);
    }
}

impl Drop for LocalRemovalGuard {
    fn drop(&mut self) {
        self.coordinator.finish_local_removal(&self.plan_id);
    }
}

#[derive(Debug, Clone)]
pub(crate) struct OperationState {
    pub(crate) primary: PrimaryOperation,
    pub(crate) active_local_reads: usize,
}

impl Default for OperationState {
    fn default() -> Self {
        Self {
            primary: PrimaryOperation::Idle,
            active_local_reads: 0,
        }
    }
}

#[derive(Default)]
pub(crate) struct OperationCoordinator {
    state: Mutex<OperationState>,
}

impl OperationCoordinator {
    pub(crate) fn begin_install(
        self: &Arc<Self>,
        operation_id: &str,
        phase: &str,
        preview_id: Option<String>,
    ) -> Result<InstallOperationGuard, BeginInstallError> {
        let mut state = self.lock_state();
        if state.primary != PrimaryOperation::Idle || state.active_local_reads != 0 {
            return Err(BeginInstallError::OperationBusy);
        }
        state.primary = PrimaryOperation::Install {
            operation_id: operation_id.to_string(),
            phase: phase.to_string(),
            preview_id,
        };
        Ok(InstallOperationGuard {
            coordinator: Arc::clone(self),
            operation_id: operation_id.to_string(),
        })
    }

    pub(crate) fn begin_local_removal(
        self: &Arc<Self>,
        plan_id: &str,
    ) -> Result<LocalRemovalGuard, BeginLocalRemovalError> {
        let mut state = self.lock_state();
        if state.primary != PrimaryOperation::Idle || state.active_local_reads != 0 {
            return Err(BeginLocalRemovalError::OperationBusy);
        }
        state.primary = PrimaryOperation::LocalRemoval(plan_id.to_string());
        Ok(LocalRemovalGuard {
            coordinator: Arc::clone(self),
            plan_id: plan_id.to_string(),
        })
    }

    pub(crate) fn begin_local_scan(
        &self,
        attempt_id: &str,
        begin: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), BeginLocalScanError> {
        let mut state = self.lock_state();
        match &state.primary {
            PrimaryOperation::Idle => {
                begin().map_err(BeginLocalScanError::Invariant)?;
                state.primary = PrimaryOperation::LocalScan(attempt_id.to_string());
                Ok(())
            }
            PrimaryOperation::LocalScan(active_attempt_id) => Err(
                BeginLocalScanError::AlreadyRunning(active_attempt_id.clone()),
            ),
            PrimaryOperation::Install { .. } | PrimaryOperation::LocalRemoval(_) => {
                Err(BeginLocalScanError::OperationBusy)
            }
        }
    }

    /// Transfers a successful Local removal directly into its authoritative rescan.
    ///
    /// The transition deliberately retains the primary-operation gate while the caller
    /// publishes the scan attempt. This prevents an install from claiming `Idle` in the
    /// interval between a filesystem mutation and its required rescan.
    pub(crate) fn handoff_local_removal_to_scan(
        &self,
        plan_id: &str,
        attempt_id: &str,
        begin: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), String> {
        let mut state = self.lock_state();
        if !matches!(
            &state.primary,
            PrimaryOperation::LocalRemoval(active_plan_id) if active_plan_id == plan_id
        ) {
            return Err("operation_busy".to_string());
        }
        begin()?;
        state.primary = PrimaryOperation::LocalScan(attempt_id.to_string());
        Ok(())
    }

    pub(crate) fn begin_local_read<T>(
        self: &Arc<Self>,
        lookup: impl FnOnce() -> Result<T, BeginLocalReadError>,
    ) -> Result<LocalReadLease<T>, BeginLocalReadError> {
        let mut state = self.lock_state();
        if matches!(
            state.primary,
            PrimaryOperation::Install { .. } | PrimaryOperation::LocalRemoval(_)
        ) {
            return Err(BeginLocalReadError::OperationBusy);
        }
        let value = lookup()?;
        state.active_local_reads = state
            .active_local_reads
            .checked_add(1)
            .ok_or(BeginLocalReadError::CapacityExhausted)?;
        Ok(LocalReadLease {
            _guard: LocalReadGuard {
                coordinator: Arc::clone(self),
            },
            value,
        })
    }

    pub(crate) fn finish_local_scan<T>(
        &self,
        attempt_id: &str,
        finish: impl FnOnce() -> Result<T, String>,
    ) -> Result<Option<T>, String> {
        let mut state = self.lock_state();
        if matches!(
            &state.primary,
            PrimaryOperation::LocalScan(active_attempt_id) if active_attempt_id == attempt_id
        ) {
            let terminal = finish();
            state.primary = PrimaryOperation::Idle;
            return terminal.map(Some);
        }
        Ok(None)
    }

    /// Finalizes an attempt while optionally publishing a prepared successor without exposing
    /// `Idle` between the two states. The scheduler owns the decision and holds its mutex before
    /// calling this method; this coordinator never owns a Local follow-up queue.
    pub(crate) fn finish_local_scan_with_handoff<T>(
        &self,
        attempt_id: &str,
        successor_attempt_id: Option<&str>,
        finish: impl FnOnce() -> Result<T, String>,
        prepare_successor: impl FnOnce() -> Result<(), String>,
    ) -> Result<Option<T>, String> {
        let mut state = self.lock_state();
        if !matches!(
            &state.primary,
            PrimaryOperation::LocalScan(active_attempt_id) if active_attempt_id == attempt_id
        ) {
            return Ok(None);
        }
        let terminal = match finish() {
            Ok(terminal) => terminal,
            Err(error) => {
                state.primary = PrimaryOperation::Idle;
                return Err(error);
            }
        };
        if let Some(successor_attempt_id) = successor_attempt_id {
            if let Err(error) = prepare_successor() {
                state.primary = PrimaryOperation::Idle;
                return Err(error);
            }
            state.primary = PrimaryOperation::LocalScan(successor_attempt_id.to_owned());
        } else {
            state.primary = PrimaryOperation::Idle;
        }
        Ok(Some(terminal))
    }

    pub(crate) fn record_local_scan_progress<T>(
        &self,
        attempt_id: &str,
        record: impl FnOnce() -> Result<Option<T>, String>,
    ) -> Result<Option<T>, String> {
        let state = self.lock_state();
        if !matches!(
            &state.primary,
            PrimaryOperation::LocalScan(active_attempt_id) if active_attempt_id == attempt_id
        ) {
            return Ok(None);
        }
        record()
    }

    #[cfg(test)]
    pub(crate) fn probe_active_local_reads(&self) -> usize {
        self.lock_state().active_local_reads
    }

    fn lock_state(&self) -> std::sync::MutexGuard<'_, OperationState> {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn finish_install(&self, operation_id: &str) {
        let mut state = self.lock_state();
        if matches!(
            &state.primary,
            PrimaryOperation::Install {
                operation_id: active_operation_id,
                ..
            } if active_operation_id == operation_id
        ) {
            state.primary = PrimaryOperation::Idle;
        }
    }

    fn finish_local_removal(&self, plan_id: &str) {
        let mut state = self.lock_state();
        if matches!(
            &state.primary,
            PrimaryOperation::LocalRemoval(active_plan_id) if active_plan_id == plan_id
        ) {
            state.primary = PrimaryOperation::Idle;
        }
    }

    fn finish_local_read(&self) {
        let mut state = self.lock_state();
        debug_assert!(state.active_local_reads > 0);
        if state.active_local_reads > 0 {
            state.active_local_reads -= 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::path::PathBuf;
    use std::sync::Barrier;

    use super::*;
    use crate::contexts::local::domain::{FileIdentity, FileIdentityType, InstanceHandle};
    use crate::contexts::local::store::LocalAuthority;
    use crate::contexts::local::{
        LocalScanExecutorResult, LocalScanPublication, LocalSnapshot, LocalSnapshotStatus, ParserId,
    };

    fn publish_handle(coordinator: &Arc<OperationCoordinator>) -> Arc<LocalAuthority> {
        let authority = Arc::new(LocalAuthority::default());
        let handle = InstanceHandle {
            canonical_root: PathBuf::from("/fixture"),
            root_device: 1,
            root_inode: 2,
            raw_relative_components: vec![b"fixture.md".to_vec()],
            config_entry_locator: None,
            source_parser_id: ParserId::MarkdownRuleV1,
            scan_content_sha256: None,
            scan_file_identity: FileIdentity {
                device: 1,
                inode: 3,
                file_type: FileIdentityType::Regular,
                mode: 0o600,
                owner_uid: 1,
                owner_gid: 1,
                link_count: 1,
                size: 0,
                mtime_ns: 0,
                ctime_ns: 0,
            },
        };
        coordinator
            .begin_local_scan("scan-fixture", || authority.begin_attempt("scan-fixture"))
            .unwrap();
        coordinator
            .finish_local_scan("scan-fixture", || {
                authority.finish_attempt(
                    "scan-fixture",
                    LocalScanExecutorResult::Complete(LocalScanPublication {
                        snapshot: LocalSnapshot {
                            snapshot_id: "snapshot-fixture".to_string(),
                            attempt_id: "scan-fixture".to_string(),
                            status: LocalSnapshotStatus::Complete,
                            scan_timestamp: "2026-07-12T00:00:00Z".to_string(),
                            app_version: "test".to_string(),
                            adapter_set_fingerprint: "a".repeat(64),
                            content_fingerprint: "b".repeat(64),
                            qualified_tools: Vec::new(),
                            instances: Vec::new(),
                            coverage: Vec::new(),
                            skipped_paths: Vec::new(),
                            issues: Vec::new(),
                            project_ignore_summaries: Vec::new(),
                        },
                        instance_handle_ids: vec!["instance-fixture".to_string()],
                        instance_handles: BTreeMap::from([(
                            "instance-fixture".to_string(),
                            handle,
                        )]),
                        project_locations: BTreeMap::new(),
                    }),
                )
            })
            .unwrap();
        authority
    }

    fn begin_fixture_read(
        coordinator: &Arc<OperationCoordinator>,
        authority: &Arc<LocalAuthority>,
        snapshot_id: &str,
        instance_id: &str,
    ) -> Result<LocalReadLease<InstanceHandle>, BeginLocalReadError> {
        coordinator.begin_local_read(|| {
            authority
                .instance_handle(snapshot_id, instance_id)
                .map_err(|code| BeginLocalReadError::from_lookup_code(&code))
        })
    }

    #[test]
    fn install_and_scan_are_mutually_exclusive_and_raii_releases_the_gate() {
        let coordinator = Arc::new(OperationCoordinator::default());
        let install = coordinator
            .begin_install("install-1", "preview", None)
            .unwrap();
        assert_eq!(
            coordinator.begin_local_scan("scan-blocked", || Ok(())),
            Err(BeginLocalScanError::OperationBusy)
        );
        drop(install);

        coordinator.begin_local_scan("scan-1", || Ok(())).unwrap();
        assert!(matches!(
            coordinator.begin_install("install-blocked", "apply", None),
            Err(BeginInstallError::OperationBusy)
        ));
        coordinator.finish_local_scan("scan-1", || Ok(())).unwrap();

        assert!(coordinator
            .begin_install("install-2", "apply", Some("preview-1".to_string()))
            .is_ok());
    }

    #[test]
    fn failed_terminal_handoff_releases_the_primary_gate() {
        let coordinator = Arc::new(OperationCoordinator::default());
        coordinator.begin_local_scan("scan-1", || Ok(())).unwrap();

        assert_eq!(
            coordinator.finish_local_scan_with_handoff(
                "scan-1",
                Some("scan-2"),
                || Err::<(), _>("terminal-store-failed".to_string()),
                || Ok(()),
            ),
            Err("terminal-store-failed".to_string())
        );
        assert!(coordinator
            .begin_install("after-terminal-failure", "preview", None)
            .is_ok());
    }

    #[test]
    fn successful_removal_handoff_to_rescan_never_exposes_idle_to_install() {
        let coordinator = Arc::new(OperationCoordinator::default());
        let removal = coordinator.begin_local_removal("removal-1").unwrap();

        coordinator
            .handoff_local_removal_to_scan("removal-1", "rescan-1", || Ok(()))
            .unwrap();

        assert!(matches!(
            coordinator.begin_install("racing-install", "apply", None),
            Err(BeginInstallError::OperationBusy)
        ));
        assert_eq!(
            coordinator.begin_local_scan("racing-scan", || Ok(())),
            Err(BeginLocalScanError::AlreadyRunning("rescan-1".to_string()))
        );

        coordinator
            .finish_local_scan("rescan-1", || Ok(()))
            .unwrap();
        drop(removal);
        assert!(coordinator
            .begin_install("after-rescan", "apply", None)
            .is_ok());
    }

    #[test]
    fn local_read_lease_blocks_install_and_drop_releases_exactly_once() {
        let coordinator = Arc::new(OperationCoordinator::default());
        let authority = publish_handle(&coordinator);

        let lease = begin_fixture_read(
            &coordinator,
            &authority,
            "snapshot-fixture",
            "instance-fixture",
        )
        .unwrap_or_else(|error| panic!("local read lease failed: {error:?}"));
        assert_eq!(coordinator.lock_state().active_local_reads, 1);
        assert!(matches!(
            coordinator.begin_install("blocked", "preview", None),
            Err(BeginInstallError::OperationBusy)
        ));

        drop(lease);
        assert_eq!(coordinator.lock_state().active_local_reads, 0);
        assert!(coordinator
            .begin_install("allowed", "preview", None)
            .is_ok());
    }

    #[test]
    fn install_gate_rejects_path_action_before_handle_lookup() {
        let coordinator = Arc::new(OperationCoordinator::default());
        let authority = publish_handle(&coordinator);
        let install = coordinator.begin_install("install", "apply", None).unwrap();

        match begin_fixture_read(&coordinator, &authority, "missing", "missing") {
            Err(error) => assert_eq!(error, BeginLocalReadError::OperationBusy),
            Ok(_) => panic!("install must reject a new local read"),
        }
        assert_eq!(coordinator.lock_state().active_local_reads, 0);
        drop(install);
    }

    #[test]
    fn failed_lookup_and_unwind_leave_the_local_read_counter_at_zero() {
        let coordinator = Arc::new(OperationCoordinator::default());
        let authority = publish_handle(&coordinator);

        match begin_fixture_read(&coordinator, &authority, "snapshot-fixture", "missing") {
            Err(error) => assert_eq!(error, BeginLocalReadError::InstanceHandleMissing),
            Ok(_) => panic!("missing handle must fail"),
        }
        assert_eq!(coordinator.lock_state().active_local_reads, 0);

        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe({
            let coordinator = Arc::clone(&coordinator);
            let authority = Arc::clone(&authority);
            move || {
                let _lease = begin_fixture_read(
                    &coordinator,
                    &authority,
                    "snapshot-fixture",
                    "instance-fixture",
                )
                .unwrap();
                panic!("fixture panic");
            }
        }));
        assert!(result.is_err());
        assert_eq!(coordinator.lock_state().active_local_reads, 0);
        assert!(coordinator
            .begin_install("after-panic", "preview", None)
            .is_ok());
    }

    #[test]
    fn every_local_read_lease_must_drop_before_install_can_begin() {
        let coordinator = Arc::new(OperationCoordinator::default());
        let authority = publish_handle(&coordinator);
        let first = begin_fixture_read(
            &coordinator,
            &authority,
            "snapshot-fixture",
            "instance-fixture",
        )
        .unwrap();
        let second = begin_fixture_read(
            &coordinator,
            &authority,
            "snapshot-fixture",
            "instance-fixture",
        )
        .unwrap();
        assert_eq!(coordinator.lock_state().active_local_reads, 2);

        drop(first);
        assert_eq!(coordinator.lock_state().active_local_reads, 1);
        assert!(matches!(
            coordinator.begin_install("still-blocked", "preview", None),
            Err(BeginInstallError::OperationBusy)
        ));
        drop(second);
        assert_eq!(coordinator.lock_state().active_local_reads, 0);
        assert!(coordinator
            .begin_install("now-allowed", "preview", None)
            .is_ok());
    }

    #[test]
    fn simultaneous_install_and_local_read_have_exactly_one_winner() {
        let coordinator = Arc::new(OperationCoordinator::default());
        let authority = publish_handle(&coordinator);
        let start = Arc::new(Barrier::new(3));
        let finish = Arc::new(Barrier::new(3));

        let read_thread = {
            let coordinator = Arc::clone(&coordinator);
            let authority = Arc::clone(&authority);
            let start = Arc::clone(&start);
            let finish = Arc::clone(&finish);
            std::thread::spawn(move || {
                start.wait();
                let result = begin_fixture_read(
                    &coordinator,
                    &authority,
                    "snapshot-fixture",
                    "instance-fixture",
                );
                let won = result.is_ok();
                finish.wait();
                won
            })
        };
        let install_thread = {
            let coordinator = Arc::clone(&coordinator);
            let start = Arc::clone(&start);
            let finish = Arc::clone(&finish);
            std::thread::spawn(move || {
                start.wait();
                let result = coordinator.begin_install("racing-install", "preview", None);
                let won = result.is_ok();
                finish.wait();
                won
            })
        };

        start.wait();
        finish.wait();
        let read_won = read_thread.join().unwrap();
        let install_won = install_thread.join().unwrap();
        assert_ne!(read_won, install_won);
        assert_eq!(coordinator.lock_state().active_local_reads, 0);
        assert_eq!(coordinator.lock_state().primary, PrimaryOperation::Idle);
    }
}
