use std::fmt;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const READY_SESSION_IDLE_TTL_MILLIS: u64 = 5 * 60 * 1_000;
const REAPER_INTERVAL: Duration = Duration::from_secs(30);

pub(crate) trait SessionClock: Send + Sync + 'static {
    fn now_millis(&self) -> u64;
}

struct MonotonicSessionClock {
    started_at: Instant,
}

impl Default for MonotonicSessionClock {
    fn default() -> Self {
        Self {
            started_at: Instant::now(),
        }
    }
}

impl SessionClock for MonotonicSessionClock {
    fn now_millis(&self) -> u64 {
        u64::try_from(self.started_at.elapsed().as_millis()).unwrap_or(u64::MAX)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct OpenSessionIntent {
    pub(crate) view_generation: u64,
    pub(crate) snapshot_id: String,
    pub(crate) instance_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ReadySessionKey {
    pub(crate) preview_session_id: String,
    pub(crate) view_generation: u64,
    pub(crate) snapshot_id: String,
    pub(crate) instance_id: String,
    pub(crate) source_revision: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SourceSessionError {
    pub(crate) code: &'static str,
    pub(crate) active_chunk_index: Option<u64>,
}

impl SourceSessionError {
    fn new(code: &'static str, active_chunk_index: Option<u64>) -> Self {
        Self {
            code,
            active_chunk_index,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct SessionCloseOutcome {
    pub(crate) closed: bool,
    pub(crate) cancelled: bool,
}

#[derive(Clone)]
struct ReadyEntry<T> {
    key: ReadySessionKey,
    payload: Arc<T>,
    last_used_millis: u64,
}

enum WorkerKind {
    Opening(OpenSessionIntent),
    Reading {
        key: ReadySessionKey,
        chunk_index: u64,
    },
}

struct ActiveWorker {
    token: u64,
    cancel: Arc<AtomicBool>,
    kind: WorkerKind,
    last_activity_millis: u64,
}

struct SessionState<T> {
    latest_generation: u64,
    ready: Option<ReadyEntry<T>>,
    worker: Option<ActiveWorker>,
}

impl<T> Default for SessionState<T> {
    fn default() -> Self {
        Self {
            latest_generation: 0,
            ready: None,
            worker: None,
        }
    }
}

pub(crate) struct SourceSessionStore<T> {
    state: Mutex<SessionState<T>>,
    next_worker: AtomicU64,
    clock: Arc<dyn SessionClock>,
    reaper_started: AtomicBool,
}

impl<T> Default for SourceSessionStore<T> {
    fn default() -> Self {
        Self {
            state: Mutex::new(SessionState::default()),
            next_worker: AtomicU64::new(0),
            clock: Arc::new(MonotonicSessionClock::default()),
            reaper_started: AtomicBool::new(false),
        }
    }
}

pub(crate) struct OpenWorkerPermit<T> {
    store: Arc<SourceSessionStore<T>>,
    token: u64,
    intent: OpenSessionIntent,
    cancel: Arc<AtomicBool>,
    finished: bool,
}

pub(crate) struct ReadWorkerPermit<T> {
    store: Arc<SourceSessionStore<T>>,
    token: u64,
    pub(crate) key: ReadySessionKey,
    pub(crate) chunk_index: u64,
    pub(crate) payload: Arc<T>,
    cancel: Arc<AtomicBool>,
    finished: bool,
}

impl<T> fmt::Debug for OpenWorkerPermit<T> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("OpenWorkerPermit")
            .field("token", &self.token)
            .field("view_generation", &self.intent.view_generation)
            .field("cancelled", &self.is_cancelled())
            .finish()
    }
}

impl<T> fmt::Debug for ReadWorkerPermit<T> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ReadWorkerPermit")
            .field("token", &self.token)
            .field("view_generation", &self.key.view_generation)
            .field("chunk_index", &self.chunk_index)
            .field("cancelled", &self.is_cancelled())
            .finish()
    }
}

impl<T> SourceSessionStore<T> {
    #[cfg(test)]
    fn with_clock(clock: Arc<dyn SessionClock>) -> Self {
        Self {
            state: Mutex::new(SessionState::default()),
            next_worker: AtomicU64::new(0),
            clock,
            reaper_started: AtomicBool::new(false),
        }
    }

    pub(crate) fn begin_open(
        self: &Arc<Self>,
        intent: OpenSessionIntent,
    ) -> Result<OpenWorkerPermit<T>, SourceSessionError> {
        let mut state = self.lock_state();
        self.expire_idle_locked(&mut state);
        if intent.view_generation < state.latest_generation {
            return Err(SourceSessionError::new("preview_stale", None));
        }
        if intent.view_generation > state.latest_generation {
            state.latest_generation = intent.view_generation;
            state.ready = None;
            if let Some(worker) = &state.worker {
                worker.cancel.store(true, Ordering::Release);
                return Err(SourceSessionError::new(
                    "preview_busy",
                    active_chunk_index(worker),
                ));
            }
        }
        if let Some(worker) = &state.worker {
            let duplicate = matches!(
                &worker.kind,
                WorkerKind::Opening(active) if active == &intent
            );
            return Err(SourceSessionError::new(
                if duplicate {
                    "already_reading"
                } else {
                    "preview_busy"
                },
                active_chunk_index(worker),
            ));
        }

        state.latest_generation = intent.view_generation;
        state.ready = None;
        let token = self.next_worker.fetch_add(1, Ordering::Relaxed) + 1;
        let cancel = Arc::new(AtomicBool::new(false));
        state.worker = Some(ActiveWorker {
            token,
            cancel: Arc::clone(&cancel),
            kind: WorkerKind::Opening(intent.clone()),
            last_activity_millis: self.clock.now_millis(),
        });
        Ok(OpenWorkerPermit {
            store: Arc::clone(self),
            token,
            intent,
            cancel,
            finished: false,
        })
    }

    pub(crate) fn begin_read(
        self: &Arc<Self>,
        key: &ReadySessionKey,
        chunk_index: u64,
    ) -> Result<ReadWorkerPermit<T>, SourceSessionError> {
        let mut state = self.lock_state();
        self.expire_idle_locked(&mut state);
        let Some(ready) = &state.ready else {
            return Err(SourceSessionError::new("preview_session_stale", None));
        };
        if &ready.key != key {
            return Err(SourceSessionError::new("preview_session_stale", None));
        }
        let payload = Arc::clone(&ready.payload);
        if let Some(worker) = &state.worker {
            let duplicate = matches!(
                &worker.kind,
                WorkerKind::Reading {
                    key: active_key,
                    chunk_index: active_index,
                } if active_key == key && *active_index == chunk_index
            );
            return Err(SourceSessionError::new(
                if duplicate {
                    "already_reading"
                } else {
                    "preview_busy"
                },
                active_chunk_index(worker),
            ));
        }

        let token = self.next_worker.fetch_add(1, Ordering::Relaxed) + 1;
        let cancel = Arc::new(AtomicBool::new(false));
        state.worker = Some(ActiveWorker {
            token,
            cancel: Arc::clone(&cancel),
            kind: WorkerKind::Reading {
                key: key.clone(),
                chunk_index,
            },
            last_activity_millis: self.clock.now_millis(),
        });
        Ok(ReadWorkerPermit {
            store: Arc::clone(self),
            token,
            key: key.clone(),
            chunk_index,
            payload,
            cancel,
            finished: false,
        })
    }

    pub(crate) fn close(
        &self,
        view_generation: u64,
        preview_session_id: Option<&str>,
    ) -> SessionCloseOutcome {
        let mut state = self.lock_state();
        self.expire_idle_locked(&mut state);
        if state.latest_generation != view_generation {
            return SessionCloseOutcome {
                closed: false,
                cancelled: false,
            };
        }

        let ready_matches = state.ready.as_ref().is_some_and(|ready| {
            preview_session_id
                .map(|session_id| session_id == ready.key.preview_session_id)
                .unwrap_or(true)
        });
        let worker_matches = state
            .worker
            .as_ref()
            .is_some_and(|worker| match &worker.kind {
                WorkerKind::Opening(intent) => {
                    intent.view_generation == view_generation && preview_session_id.is_none()
                }
                WorkerKind::Reading { key, .. } => {
                    key.view_generation == view_generation
                        && preview_session_id
                            .map(|session_id| session_id == key.preview_session_id)
                            .unwrap_or(true)
                }
            });
        if !ready_matches && !worker_matches {
            return SessionCloseOutcome {
                closed: false,
                cancelled: false,
            };
        }

        if worker_matches {
            if let Some(worker) = &state.worker {
                worker.cancel.store(true, Ordering::Release);
            }
        }
        if ready_matches {
            state.ready = None;
        }
        SessionCloseOutcome {
            closed: ready_matches,
            cancelled: worker_matches,
        }
    }

    fn commit_open(
        &self,
        token: u64,
        intent: &OpenSessionIntent,
        cancel: &AtomicBool,
        key: ReadySessionKey,
        payload: Arc<T>,
    ) -> Result<(), SourceSessionError> {
        let mut state = self.lock_state();
        let active = state.worker.as_ref().is_some_and(|worker| {
            worker.token == token
                && matches!(&worker.kind, WorkerKind::Opening(active) if active == intent)
        });
        state.worker = state.worker.take().filter(|worker| worker.token != token);
        if !active
            || cancel.load(Ordering::Acquire)
            || state.latest_generation != intent.view_generation
            || key.view_generation != intent.view_generation
            || key.snapshot_id != intent.snapshot_id
            || key.instance_id != intent.instance_id
        {
            return Err(SourceSessionError::new("preview_cancelled", None));
        }
        state.ready = Some(ReadyEntry {
            key,
            payload,
            last_used_millis: self.clock.now_millis(),
        });
        Ok(())
    }

    fn finish_worker(&self, token: u64) {
        let mut state = self.lock_state();
        self.expire_idle_locked(&mut state);
        if state
            .worker
            .as_ref()
            .is_some_and(|worker| worker.token == token)
        {
            state.worker = None;
        }
    }

    fn touch_worker(&self, token: u64) {
        let mut state = self.lock_state();
        if let Some(worker) = &mut state.worker {
            if worker.token == token && !worker.cancel.load(Ordering::Acquire) {
                worker.last_activity_millis = self.clock.now_millis();
            }
        }
    }

    fn commit_read_response<R>(
        &self,
        token: u64,
        key: &ReadySessionKey,
        cancel: &AtomicBool,
        value: R,
    ) -> Result<R, SourceSessionError> {
        let mut state = self.lock_state();
        self.expire_idle_locked(&mut state);
        let active = state.worker.as_ref().is_some_and(|worker| {
            worker.token == token
                && matches!(
                    &worker.kind,
                    WorkerKind::Reading {
                        key: active_key,
                        ..
                    } if active_key == key
                )
        });
        let ready_matches = state.ready.as_ref().is_some_and(|ready| &ready.key == key);
        let current = active
            && ready_matches
            && state.latest_generation == key.view_generation
            && !cancel.load(Ordering::Acquire);
        if active {
            state.worker = None;
        }
        if current {
            if let Some(ready) = &mut state.ready {
                ready.last_used_millis = self.clock.now_millis();
            }
            Ok(value)
        } else {
            Err(SourceSessionError::new("preview_cancelled", None))
        }
    }

    fn lock_state(&self) -> std::sync::MutexGuard<'_, SessionState<T>> {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub(crate) fn ready_key_for(
        &self,
        preview_session_id: &str,
        source_revision: &str,
    ) -> Result<ReadySessionKey, SourceSessionError> {
        let mut state = self.lock_state();
        self.expire_idle_locked(&mut state);
        state
            .ready
            .as_ref()
            .filter(|ready| {
                ready.key.preview_session_id == preview_session_id
                    && ready.key.source_revision == source_revision
            })
            .map(|ready| ready.key.clone())
            .ok_or_else(|| SourceSessionError::new("preview_session_stale", None))
    }

    #[cfg(test)]
    pub(crate) fn ready_payload_for(&self, preview_session_id: &str) -> Option<Arc<T>> {
        let mut state = self.lock_state();
        self.expire_idle_locked(&mut state);
        state
            .ready
            .as_ref()
            .filter(|ready| ready.key.preview_session_id == preview_session_id)
            .map(|ready| Arc::clone(&ready.payload))
    }

    pub(crate) fn active_worker_count(&self) -> usize {
        usize::from(self.lock_state().worker.is_some())
    }

    pub(crate) fn ready_session_count(&self) -> usize {
        let mut state = self.lock_state();
        self.expire_idle_locked(&mut state);
        usize::from(state.ready.is_some())
    }

    pub(crate) fn expire_idle(&self) -> bool {
        let mut state = self.lock_state();
        self.expire_idle_locked(&mut state)
    }

    fn expire_idle_locked(&self, state: &mut SessionState<T>) -> bool {
        let now = self.clock.now_millis();
        let mut expired = false;
        if let Some(worker) = &state.worker {
            if now.saturating_sub(worker.last_activity_millis) >= READY_SESSION_IDLE_TTL_MILLIS {
                worker.cancel.store(true, Ordering::Release);
                expired = true;
            }
        }
        let expired_key = state
            .ready
            .as_ref()
            .filter(|ready| {
                now.saturating_sub(ready.last_used_millis) >= READY_SESSION_IDLE_TTL_MILLIS
            })
            .map(|ready| ready.key.clone());
        let Some(expired_key) = expired_key else {
            return expired;
        };
        if let Some(worker) = &state.worker {
            if matches!(
                &worker.kind,
                WorkerKind::Reading { key, .. } if key == &expired_key
            ) {
                worker.cancel.store(true, Ordering::Release);
            }
        }
        state.ready = None;
        true
    }

    #[cfg(test)]
    fn queued_worker_count(&self) -> usize {
        0
    }

    #[cfg(test)]
    fn ready_key(&self) -> Option<ReadySessionKey> {
        self.lock_state()
            .ready
            .as_ref()
            .map(|ready| ready.key.clone())
    }
}

impl<T: Send + Sync + 'static> SourceSessionStore<T> {
    pub(crate) fn start_idle_reaper(self: &Arc<Self>) {
        if self.reaper_started.swap(true, Ordering::AcqRel) {
            return;
        }
        let sessions = Arc::downgrade(self);
        std::thread::spawn(move || loop {
            std::thread::sleep(REAPER_INTERVAL);
            let Some(sessions) = sessions.upgrade() else {
                break;
            };
            sessions.expire_idle();
        });
    }
}

impl<T> OpenWorkerPermit<T> {
    pub(crate) fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::Acquire)
    }

    pub(crate) fn touch(&self) {
        self.store.touch_worker(self.token);
    }

    pub(crate) fn complete(
        mut self,
        key: ReadySessionKey,
        payload: Arc<T>,
    ) -> Result<(), SourceSessionError> {
        let result = self
            .store
            .commit_open(self.token, &self.intent, &self.cancel, key, payload);
        self.finished = true;
        result
    }
}

impl<T> Drop for OpenWorkerPermit<T> {
    fn drop(&mut self) {
        if !self.finished {
            self.store.finish_worker(self.token);
        }
    }
}

impl<T> ReadWorkerPermit<T> {
    pub(crate) fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::Acquire)
    }

    pub(crate) fn touch(&self) {
        self.store.touch_worker(self.token);
    }

    pub(crate) fn finish_with<R>(mut self, value: R) -> Result<R, SourceSessionError> {
        let result =
            self.store
                .commit_read_response(self.token, &self.key, self.cancel.as_ref(), value);
        self.finished = true;
        result
    }
}

impl<T> Drop for ReadWorkerPermit<T> {
    fn drop(&mut self) {
        if !self.finished {
            self.store.finish_worker(self.token);
        }
    }
}

fn active_chunk_index(worker: &ActiveWorker) -> Option<u64> {
    match &worker.kind {
        WorkerKind::Opening(_) => None,
        WorkerKind::Reading { chunk_index, .. } => Some(*chunk_index),
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::Arc;

    use super::*;

    #[derive(Default)]
    struct FakeClock {
        now_millis: AtomicU64,
    }

    impl FakeClock {
        fn advance(&self, millis: u64) {
            self.now_millis.fetch_add(millis, Ordering::AcqRel);
        }
    }

    impl SessionClock for FakeClock {
        fn now_millis(&self) -> u64 {
            self.now_millis.load(Ordering::Acquire)
        }
    }

    fn open_intent(generation: u64, instance_id: &str) -> OpenSessionIntent {
        OpenSessionIntent {
            view_generation: generation,
            snapshot_id: "snapshot-1".to_string(),
            instance_id: instance_id.to_string(),
        }
    }

    fn ready_key(generation: u64, instance_id: &str) -> ReadySessionKey {
        ReadySessionKey {
            preview_session_id: format!("session-{generation}-{instance_id}"),
            view_generation: generation,
            snapshot_id: "snapshot-1".to_string(),
            instance_id: instance_id.to_string(),
            source_revision: format!("revision-{generation}-{instance_id}"),
        }
    }

    #[test]
    fn newer_open_cancels_the_active_worker_without_queueing_another_worker() {
        let sessions = Arc::new(SourceSessionStore::<String>::default());
        let first = sessions.begin_open(open_intent(1, "instance-a")).unwrap();

        let busy = sessions
            .begin_open(open_intent(2, "instance-b"))
            .unwrap_err();
        assert_eq!(busy.code, "preview_busy");
        assert!(first.is_cancelled());
        assert_eq!(sessions.active_worker_count(), 1);
        assert_eq!(sessions.queued_worker_count(), 0);

        drop(first);
        let second = sessions.begin_open(open_intent(2, "instance-b")).unwrap();
        let key = ready_key(2, "instance-b");
        second
            .complete(key.clone(), Arc::new("ready-b".to_string()))
            .unwrap();
        assert_eq!(sessions.ready_key(), Some(key));
        assert_eq!(sessions.active_worker_count(), 0);
    }

    #[test]
    fn duplicate_and_concurrent_reads_are_typed_and_never_queued() {
        let sessions = Arc::new(SourceSessionStore::<String>::default());
        let open = sessions.begin_open(open_intent(3, "instance-a")).unwrap();
        let key = ready_key(3, "instance-a");
        open.complete(key.clone(), Arc::new("ready".to_string()))
            .unwrap();

        let first = sessions.begin_read(&key, 2).unwrap();
        let duplicate = sessions.begin_read(&key, 2).unwrap_err();
        assert_eq!(duplicate.code, "already_reading");
        assert_eq!(duplicate.active_chunk_index, Some(2));
        let busy = sessions.begin_read(&key, 4).unwrap_err();
        assert_eq!(busy.code, "preview_busy");
        assert_eq!(busy.active_chunk_index, Some(2));
        assert_eq!(sessions.active_worker_count(), 1);
        assert_eq!(sessions.queued_worker_count(), 0);

        drop(first);
        assert!(sessions.begin_read(&key, 4).is_ok());
    }

    #[test]
    fn close_only_cancels_the_matching_generation_and_session() {
        let sessions = Arc::new(SourceSessionStore::<String>::default());
        let open = sessions.begin_open(open_intent(5, "instance-a")).unwrap();
        let key = ready_key(5, "instance-a");
        open.complete(key.clone(), Arc::new("ready".to_string()))
            .unwrap();
        let read = sessions.begin_read(&key, 1).unwrap();

        assert_eq!(
            sessions.close(4, Some(&key.preview_session_id)),
            SessionCloseOutcome {
                closed: false,
                cancelled: false,
            }
        );
        assert_eq!(
            sessions.close(5, Some("another-session")),
            SessionCloseOutcome {
                closed: false,
                cancelled: false,
            }
        );
        assert!(!read.is_cancelled());

        assert_eq!(
            sessions.close(5, Some(&key.preview_session_id)),
            SessionCloseOutcome {
                closed: true,
                cancelled: true,
            }
        );
        assert!(read.is_cancelled());
        assert_eq!(sessions.ready_key(), None);
        drop(read);
        assert_eq!(sessions.active_worker_count(), 0);
    }

    #[test]
    fn read_response_commit_rejects_a_close_that_won_the_session_transaction() {
        let sessions = Arc::new(SourceSessionStore::<String>::default());
        let open = sessions.begin_open(open_intent(7, "instance-a")).unwrap();
        let key = ready_key(7, "instance-a");
        open.complete(key.clone(), Arc::new("ready".to_string()))
            .unwrap();
        let read = sessions.begin_read(&key, 0).unwrap();

        assert_eq!(
            sessions.close(7, Some(&key.preview_session_id)),
            SessionCloseOutcome {
                closed: true,
                cancelled: true,
            }
        );
        let result = sessions.commit_read_response(
            read.token,
            &read.key,
            read.cancel.as_ref(),
            "must-not-escape",
        );

        assert_eq!(
            result.unwrap_err(),
            SourceSessionError::new("preview_cancelled", None)
        );
    }

    #[test]
    fn ready_session_expires_after_five_idle_minutes_and_successful_read_refreshes_it() {
        let clock = Arc::new(FakeClock::default());
        let sessions = Arc::new(SourceSessionStore::<String>::with_clock(clock.clone()));
        let open = sessions.begin_open(open_intent(8, "instance-a")).unwrap();
        let key = ready_key(8, "instance-a");
        open.complete(key.clone(), Arc::new("ready".to_string()))
            .unwrap();

        clock.advance(299_999);
        assert_eq!(
            sessions.ready_key_for(&key.preview_session_id, &key.source_revision),
            Ok(key.clone())
        );
        let read = sessions.begin_read(&key, 0).unwrap();
        read.finish_with(()).unwrap();

        clock.advance(299_999);
        assert!(!sessions.expire_idle());
        assert_eq!(sessions.ready_session_count(), 1);
        clock.advance(1);
        assert!(sessions.expire_idle());
        assert_eq!(sessions.ready_session_count(), 0);
    }

    #[test]
    fn idle_expiry_cancels_an_in_flight_chunk_before_response_commit() {
        let clock = Arc::new(FakeClock::default());
        let sessions = Arc::new(SourceSessionStore::<String>::with_clock(clock.clone()));
        let open = sessions.begin_open(open_intent(9, "instance-a")).unwrap();
        let key = ready_key(9, "instance-a");
        open.complete(key.clone(), Arc::new("ready".to_string()))
            .unwrap();
        let read = sessions.begin_read(&key, 0).unwrap();

        clock.advance(300_000);
        assert!(sessions.expire_idle());
        assert!(read.is_cancelled());
        assert_eq!(
            read.finish_with("must-not-escape"),
            Err(SourceSessionError::new("preview_cancelled", None))
        );
        assert_eq!(sessions.active_worker_count(), 0);
        assert_eq!(sessions.ready_session_count(), 0);
    }

    #[test]
    fn idle_expiry_cancels_an_opening_worker_and_worker_activity_refreshes_the_deadline() {
        let clock = Arc::new(FakeClock::default());
        let sessions = Arc::new(SourceSessionStore::<String>::with_clock(clock.clone()));
        let opening = sessions.begin_open(open_intent(10, "instance-a")).unwrap();

        clock.advance(299_999);
        opening.touch();
        clock.advance(299_999);
        assert!(!sessions.expire_idle());
        assert!(!opening.is_cancelled());
        clock.advance(1);
        assert!(sessions.expire_idle());
        assert!(opening.is_cancelled());
        assert_eq!(sessions.active_worker_count(), 1);
        drop(opening);
        assert_eq!(sessions.active_worker_count(), 0);
    }
}
