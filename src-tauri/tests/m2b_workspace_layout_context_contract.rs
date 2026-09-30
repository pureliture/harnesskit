use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Barrier, Mutex};

use harness_desktop_lib::contexts::layout::{
    WorkspaceLayoutContext, WorkspaceLayoutPreferencePort, WorkspaceLayoutStoreError,
};

#[derive(Default)]
struct FakeStore {
    calls: Mutex<Vec<(u32, u32)>>,
    fail: AtomicBool,
}

impl WorkspaceLayoutPreferencePort for FakeStore {
    fn save(
        &self,
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
    ) -> Result<(), WorkspaceLayoutStoreError> {
        self.calls
            .lock()
            .unwrap()
            .push((preferred_left_width_px, preferred_right_width_px));
        if self.fail.load(Ordering::SeqCst) {
            Err(WorkspaceLayoutStoreError {
                code: "workspace_layout_preference_write_failed",
                safe_message: "레이아웃 설정 저장 실패",
            })
        } else {
            Ok(())
        }
    }
}

#[test]
fn initial_state_uses_the_loaded_pair_at_revision_zero() {
    let context = WorkspaceLayoutContext::new(420, 500, Arc::new(FakeStore::default()));

    let state = context.state();

    assert_eq!(state.preferred_left_width_px, 420);
    assert_eq!(state.preferred_right_width_px, 500);
    assert_eq!(state.revision, 0);
    assert!(state.persisted);
}

#[test]
fn valid_whole_pair_commit_persists_once_and_increments_revision() {
    let store = Arc::new(FakeStore::default());
    let context = WorkspaceLayoutContext::new(420, 500, store.clone());

    let update = context.set_preference(0, 430, 510).unwrap();

    assert_eq!(update.state.preferred_left_width_px, 430);
    assert_eq!(update.state.preferred_right_width_px, 510);
    assert_eq!(update.state.revision, 1);
    assert!(update.state.persisted);
    assert_eq!(update.diagnostic, None);
    assert_eq!(*store.calls.lock().unwrap(), [(430, 510)]);
    assert_eq!(context.state(), update.state);
}

#[test]
fn identical_persisted_pair_is_a_revision_and_write_noop() {
    let store = Arc::new(FakeStore::default());
    let context = WorkspaceLayoutContext::new(420, 500, store.clone());

    let update = context.set_preference(0, 420, 500).unwrap();

    assert_eq!(update.state.revision, 0);
    assert!(update.state.persisted);
    assert_eq!(update.diagnostic, None);
    assert!(store.calls.lock().unwrap().is_empty());
}

#[test]
fn write_failure_keeps_the_new_session_pair_as_unpersisted() {
    let store = Arc::new(FakeStore {
        fail: AtomicBool::new(true),
        ..FakeStore::default()
    });
    let context = WorkspaceLayoutContext::new(420, 500, store.clone());

    let update = context.set_preference(0, 430, 510).unwrap();

    assert_eq!(update.state.preferred_left_width_px, 430);
    assert_eq!(update.state.preferred_right_width_px, 510);
    assert_eq!(update.state.revision, 1);
    assert!(!update.state.persisted);
    assert_eq!(
        update.diagnostic.unwrap().code,
        "workspace_layout_preference_write_failed"
    );
    assert_eq!(context.state(), update.state);
    assert_eq!(*store.calls.lock().unwrap(), [(430, 510)]);
}

#[test]
fn stale_revision_rejects_without_write_or_session_mutation() {
    let store = Arc::new(FakeStore::default());
    let context = WorkspaceLayoutContext::new(420, 500, store.clone());

    let error = context.set_preference(7, 430, 510).unwrap_err();

    assert_eq!(error.code, "workspace_layout_stale");
    assert_eq!(context.state().revision, 0);
    assert!(store.calls.lock().unwrap().is_empty());
}

#[test]
fn invalid_pair_never_reaches_the_store() {
    let store = Arc::new(FakeStore::default());
    let context = WorkspaceLayoutContext::new(420, 500, store.clone());

    let error = context.set_preference(0, 199, 510).unwrap_err();

    assert_eq!(error.code, "workspace_layout_preference_invalid");
    assert_eq!(context.state().revision, 0);
    assert!(store.calls.lock().unwrap().is_empty());
}

#[test]
fn identical_unpersisted_pair_retries_save_and_publishes_a_new_revision() {
    let store = Arc::new(FakeStore {
        fail: AtomicBool::new(true),
        ..FakeStore::default()
    });
    let context = WorkspaceLayoutContext::new(420, 500, store.clone());
    let failed = context.set_preference(0, 430, 510).unwrap();
    assert!(!failed.state.persisted);
    store.fail.store(false, Ordering::SeqCst);

    let retried = context.set_preference(1, 430, 510).unwrap();

    assert_eq!(retried.state.revision, 2);
    assert!(retried.state.persisted);
    assert_eq!(*store.calls.lock().unwrap(), [(430, 510), (430, 510)]);
}

#[test]
fn invalid_load_fallback_retries_the_identical_default_pair_before_claiming_persistence() {
    let store = Arc::new(FakeStore::default());
    let context = WorkspaceLayoutContext::from_loaded(304, 368, false, store.clone());

    assert!(!context.state().persisted);
    let repaired = context.set_preference(0, 304, 368).unwrap();

    assert_eq!(repaired.state.revision, 1);
    assert!(repaired.state.persisted);
    assert_eq!(repaired.diagnostic, None);
    assert_eq!(*store.calls.lock().unwrap(), [(304, 368)]);
}

#[test]
fn concurrent_same_revision_allows_exactly_one_commit() {
    let store = Arc::new(FakeStore::default());
    let context = Arc::new(WorkspaceLayoutContext::new(420, 500, store.clone()));
    let barrier = Arc::new(Barrier::new(3));
    let mut workers = Vec::new();
    for pair in [(430, 510), (440, 520)] {
        let context = context.clone();
        let barrier = barrier.clone();
        workers.push(std::thread::spawn(move || {
            barrier.wait();
            context.set_preference(0, pair.0, pair.1)
        }));
    }
    barrier.wait();

    let results = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .collect::<Vec<_>>();

    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(
        results
            .iter()
            .filter_map(|result| result.as_ref().err())
            .filter(|error| error.code == "workspace_layout_stale")
            .count(),
        1
    );
    assert_eq!(context.state().revision, 1);
    assert_eq!(store.calls.lock().unwrap().len(), 1);
}
