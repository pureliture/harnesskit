use super::context::{
    LocalScanDiagnostics, LocalScanExecutorResult, LocalScanProgress, LocalScanPublication,
};
use super::domain::{CoverageRecord, InstanceHandle, SafeIssue, SkippedPath};
use super::snapshot::LocalSnapshotStatus;
use std::collections::BTreeMap;
use std::sync::Mutex;

#[cfg(test)]
use std::sync::atomic::{AtomicBool, Ordering};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LocalAttemptState {
    Running,
    Complete,
    Partial,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalAttemptReport {
    pub attempt_id: String,
    pub state: LocalAttemptState,
    pub started_at: String,
    pub ended_at: Option<String>,
    pub app_version: Option<String>,
    pub adapter_set_fingerprint: Option<String>,
    pub coverage: Vec<CoverageRecord>,
    pub skipped_paths: Vec<SkippedPath>,
    pub issues: Vec<SafeIssue>,
    pub error_code: Option<String>,
    pub progress: Option<LocalScanProgress>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalSnapshotHeader {
    pub snapshot_id: String,
    pub attempt_id: String,
    pub status: LocalSnapshotStatus,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LocalSessionState {
    pub state_revision: u64,
    pub current_attempt: Option<LocalAttemptReport>,
    pub latest_terminal_report: Option<LocalAttemptReport>,
    pub latest_complete: Option<LocalSnapshotHeader>,
    pub latest_partial: Option<LocalSnapshotHeader>,
}

#[derive(Debug, Clone, Default)]
pub(crate) struct LocalStore {
    state: LocalSessionState,
    publications: BTreeMap<String, LocalScanPublication>,
    progress_counts: BTreeMap<String, u64>,
}

#[derive(Default)]
pub(crate) struct LocalAuthority {
    store: Mutex<LocalStore>,
    #[cfg(test)]
    fail_next_finish: AtomicBool,
}

impl LocalAuthority {
    pub(crate) fn begin_attempt(&self, attempt_id: &str) -> Result<(), String> {
        self.lock_store().begin_attempt(attempt_id)
    }

    pub(crate) fn record_progress(
        &self,
        attempt_id: &str,
        progress: LocalScanProgress,
    ) -> Result<Option<LocalSessionState>, String> {
        let mut store = self.lock_store();
        if store.record_progress(attempt_id, progress)? {
            Ok(Some(store.state()))
        } else {
            Ok(None)
        }
    }

    pub(crate) fn finish_attempt(
        &self,
        attempt_id: &str,
        result: LocalScanExecutorResult,
    ) -> Result<LocalSessionState, String> {
        #[cfg(test)]
        if self.fail_next_finish.swap(false, Ordering::SeqCst) {
            return Err("local_terminal_store_failed".to_string());
        }
        let mut store = self.lock_store();
        if !store.finish_attempt(attempt_id, result)? {
            return Err("local_attempt_invariant".to_string());
        }
        Ok(store.state())
    }

    #[cfg(test)]
    pub(crate) fn fail_next_finish(&self) {
        self.fail_next_finish.store(true, Ordering::SeqCst);
    }

    pub(crate) fn state(&self) -> LocalSessionState {
        self.lock_store().state()
    }

    pub(crate) fn publication(&self, snapshot_id: &str) -> Result<LocalScanPublication, String> {
        self.lock_store().publication(snapshot_id)
    }

    pub(crate) fn latest_published_publication_for_attempt(
        &self,
        snapshot_id: &str,
        expected_attempt_id: &str,
    ) -> Result<LocalScanPublication, String> {
        self.lock_store()
            .latest_published_publication_for_attempt(snapshot_id, expected_attempt_id)
    }

    pub(crate) fn instance_handle(
        &self,
        snapshot_id: &str,
        instance_id: &str,
    ) -> Result<InstanceHandle, String> {
        self.lock_store().instance_handle(snapshot_id, instance_id)
    }

    fn lock_store(&self) -> std::sync::MutexGuard<'_, LocalStore> {
        self.store
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

impl LocalStore {
    pub(crate) fn begin_attempt(&mut self, attempt_id: &str) -> Result<(), String> {
        if self.state.current_attempt.is_some() {
            return Err("local_attempt_already_running".to_string());
        }
        self.progress_counts.clear();
        self.state.state_revision = self
            .state
            .state_revision
            .checked_add(1)
            .ok_or_else(|| "local_state_revision_exhausted".to_string())?;
        self.state.current_attempt = Some(LocalAttemptReport {
            attempt_id: attempt_id.to_string(),
            state: LocalAttemptState::Running,
            started_at: chrono::Utc::now().to_rfc3339(),
            ended_at: None,
            app_version: None,
            adapter_set_fingerprint: None,
            coverage: Vec::new(),
            skipped_paths: Vec::new(),
            issues: Vec::new(),
            error_code: None,
            progress: None,
        });
        Ok(())
    }

    pub(crate) fn record_progress(
        &mut self,
        attempt_id: &str,
        progress: LocalScanProgress,
    ) -> Result<bool, String> {
        let Some(attempt) = self.state.current_attempt.as_mut() else {
            return Ok(false);
        };
        if attempt.attempt_id != attempt_id {
            return Ok(false);
        }
        if self
            .progress_counts
            .get(&progress.coverage_id)
            .is_some_and(|last_count| *last_count >= progress.item_count)
        {
            return Ok(false);
        }
        self.state.state_revision = self
            .state
            .state_revision
            .checked_add(1)
            .ok_or_else(|| "local_state_revision_exhausted".to_string())?;
        self.progress_counts
            .insert(progress.coverage_id.clone(), progress.item_count);
        attempt.progress = Some(progress);
        Ok(true)
    }

    pub(crate) fn state(&self) -> LocalSessionState {
        self.state.clone()
    }

    pub(crate) fn finish_attempt(
        &mut self,
        attempt_id: &str,
        result: LocalScanExecutorResult,
    ) -> Result<bool, String> {
        if self
            .state
            .current_attempt
            .as_ref()
            .map(|attempt| attempt.attempt_id.as_str())
            != Some(attempt_id)
        {
            return Ok(false);
        }
        self.state.state_revision = self
            .state
            .state_revision
            .checked_add(1)
            .ok_or_else(|| "local_state_revision_exhausted".to_string())?;
        let started_at = self
            .state
            .current_attempt
            .as_ref()
            .map(|attempt| attempt.started_at.clone())
            .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
        let last_progress = self
            .state
            .current_attempt
            .as_ref()
            .and_then(|attempt| attempt.progress.clone());
        self.state.current_attempt = None;
        let (state, error_code, diagnostics) = match result {
            LocalScanExecutorResult::Failed { code, diagnostics } => {
                (LocalAttemptState::Failed, Some(code), diagnostics)
            }
            LocalScanExecutorResult::Complete(publication) => {
                let diagnostics = snapshot_diagnostics(&publication);
                let header = LocalSnapshotHeader {
                    snapshot_id: publication.snapshot.snapshot_id.clone(),
                    attempt_id: attempt_id.to_string(),
                    status: LocalSnapshotStatus::Complete,
                };
                if let Some(replaced) = self.state.latest_complete.replace(header) {
                    self.publications.remove(&replaced.snapshot_id);
                }
                self.publications
                    .insert(publication.snapshot.snapshot_id.clone(), publication);
                (LocalAttemptState::Complete, None, Some(diagnostics))
            }
            LocalScanExecutorResult::Partial {
                publication,
                issue_codes,
            } if !issue_codes.is_empty() => {
                let diagnostics = snapshot_diagnostics(&publication);
                let header = LocalSnapshotHeader {
                    snapshot_id: publication.snapshot.snapshot_id.clone(),
                    attempt_id: attempt_id.to_string(),
                    status: LocalSnapshotStatus::Partial,
                };
                if let Some(replaced) = self.state.latest_partial.replace(header) {
                    self.publications.remove(&replaced.snapshot_id);
                }
                self.publications
                    .insert(publication.snapshot.snapshot_id.clone(), publication);
                (LocalAttemptState::Partial, None, Some(diagnostics))
            }
            LocalScanExecutorResult::Partial { .. } => (
                LocalAttemptState::Failed,
                Some("scan_result_invariant".to_string()),
                None,
            ),
        };
        let diagnostics = diagnostics.unwrap_or_default();
        self.state.latest_terminal_report = Some(LocalAttemptReport {
            attempt_id: attempt_id.to_string(),
            state,
            started_at,
            ended_at: Some(chrono::Utc::now().to_rfc3339()),
            app_version: diagnostics.app_version,
            adapter_set_fingerprint: diagnostics.adapter_set_fingerprint,
            coverage: diagnostics.coverage,
            skipped_paths: diagnostics.skipped_paths,
            issues: diagnostics.issues,
            error_code,
            progress: last_progress,
        });
        self.progress_counts.clear();
        Ok(true)
    }

    pub(crate) fn publication(&self, snapshot_id: &str) -> Result<LocalScanPublication, String> {
        self.publications
            .get(snapshot_id)
            .cloned()
            .ok_or_else(|| "snapshot_expired".to_string())
    }

    pub(crate) fn latest_published_publication_for_attempt(
        &self,
        snapshot_id: &str,
        expected_attempt_id: &str,
    ) -> Result<LocalScanPublication, String> {
        if self.state.current_attempt.is_some() {
            return Err("removal_reconciliation_not_authoritative".to_string());
        }
        let terminal = self
            .state
            .latest_terminal_report
            .as_ref()
            .ok_or_else(|| "removal_reconciliation_not_authoritative".to_string())?;
        if terminal.attempt_id != expected_attempt_id {
            return Err("removal_reconciliation_not_authoritative".to_string());
        }
        let header = match terminal.state {
            LocalAttemptState::Complete => self.state.latest_complete.as_ref(),
            LocalAttemptState::Partial => self.state.latest_partial.as_ref(),
            LocalAttemptState::Running | LocalAttemptState::Failed => None,
        }
        .ok_or_else(|| "removal_reconciliation_not_authoritative".to_string())?;
        if header.snapshot_id != snapshot_id || header.attempt_id != expected_attempt_id {
            return Err("removal_reconciliation_not_authoritative".to_string());
        }
        let publication = self.publication(snapshot_id)?;
        if publication.snapshot.status != header.status
            || publication.snapshot.attempt_id != expected_attempt_id
        {
            return Err("removal_reconciliation_not_authoritative".to_string());
        }
        Ok(publication)
    }

    pub(crate) fn instance_handle(
        &self,
        snapshot_id: &str,
        instance_id: &str,
    ) -> Result<InstanceHandle, String> {
        let publication = self
            .publications
            .get(snapshot_id)
            .ok_or_else(|| "snapshot_expired".to_string())?;
        publication
            .instance_handles
            .get(instance_id)
            .cloned()
            .ok_or_else(|| "instance_handle_missing".to_string())
    }
}

fn snapshot_diagnostics(publication: &LocalScanPublication) -> LocalScanDiagnostics {
    LocalScanDiagnostics {
        app_version: Some(publication.snapshot.app_version.clone()),
        adapter_set_fingerprint: Some(publication.snapshot.adapter_set_fingerprint.clone()),
        coverage: publication.snapshot.coverage.clone(),
        skipped_paths: publication.snapshot.skipped_paths.clone(),
        issues: publication.snapshot.issues.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn progress_for(coverage_id: &str, item_count: u64) -> LocalScanProgress {
        LocalScanProgress {
            adapter_id: "codex-builtin".to_string(),
            surface_id: "project-skills".to_string(),
            coverage_id: coverage_id.to_string(),
            item_count,
        }
    }

    #[test]
    fn progress_is_active_attempt_only_idempotent_and_monotonic_per_coverage() {
        let mut store = LocalStore::default();
        store.begin_attempt("attempt-progress").unwrap();
        assert!(store
            .record_progress("attempt-progress", progress_for("coverage-a", 0))
            .unwrap());
        assert!(!store
            .record_progress("attempt-progress", progress_for("coverage-a", 0))
            .unwrap());
        assert!(store
            .record_progress("attempt-progress", progress_for("coverage-a", 3))
            .unwrap());
        assert!(store
            .record_progress("attempt-progress", progress_for("coverage-b", 0))
            .unwrap());
        assert!(!store
            .record_progress("attempt-progress", progress_for("coverage-a", 2))
            .unwrap());
        assert_eq!(store.state().state_revision, 4);

        store
            .finish_attempt(
                "attempt-progress",
                LocalScanExecutorResult::Failed {
                    code: "fixture_finished".to_string(),
                    diagnostics: None,
                },
            )
            .unwrap();
        assert!(!store
            .record_progress("attempt-progress", progress_for("coverage-a", 4))
            .unwrap());
        let state = store.state();
        assert_eq!(state.state_revision, 5);
        assert_eq!(
            state.latest_terminal_report.unwrap().progress,
            Some(progress_for("coverage-b", 0))
        );
    }
}
