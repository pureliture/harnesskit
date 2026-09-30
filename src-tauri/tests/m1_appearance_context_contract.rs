use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

use harness_desktop_lib::contexts::appearance::{
    AppearanceChangeSource, AppearanceChanged, AppearanceContext, AppearanceEventPort,
    AppearancePreferencePort, AppearanceState, AppearanceStoreError, AppearanceWindowError,
    AppearanceWindowPort, BootstrapInstruction, BootstrapRecoveryAction, LogicalMode, ResolvedMode,
    WindowThemeOverride,
};

#[derive(Default)]
struct FakeStore {
    trace: Arc<Mutex<Vec<&'static str>>>,
    fail: bool,
}

impl AppearancePreferencePort for FakeStore {
    fn save(&self, _logical_mode: LogicalMode) -> Result<(), AppearanceStoreError> {
        self.trace.lock().unwrap().push("store");
        if self.fail {
            Err(AppearanceStoreError {
                code: "appearance_preference_write_failed",
                safe_message: "설정 저장 실패",
            })
        } else {
            Ok(())
        }
    }
}

struct FakeWindow {
    trace: Arc<Mutex<Vec<&'static str>>>,
    events: Mutex<Vec<AppearanceChanged>>,
    overrides: Mutex<Vec<WindowThemeOverride>>,
    show_count: Mutex<usize>,
    restore_count: Mutex<usize>,
    system_mode: Mutex<ResolvedMode>,
    emit_fail: bool,
    restore_fail: bool,
}

impl Default for FakeWindow {
    fn default() -> Self {
        Self {
            trace: Arc::new(Mutex::new(Vec::new())),
            events: Mutex::new(Vec::new()),
            overrides: Mutex::new(Vec::new()),
            show_count: Mutex::new(0),
            restore_count: Mutex::new(0),
            system_mode: Mutex::new(ResolvedMode::Light),
            emit_fail: false,
            restore_fail: false,
        }
    }
}

impl AppearanceWindowPort for FakeWindow {
    fn current_system_mode(&self) -> Result<ResolvedMode, AppearanceWindowError> {
        Ok(*self.system_mode.lock().unwrap())
    }

    fn apply_theme(
        &self,
        theme_override: WindowThemeOverride,
        _resolved_mode: ResolvedMode,
    ) -> Result<(), AppearanceWindowError> {
        self.trace.lock().unwrap().push("window");
        self.overrides.lock().unwrap().push(theme_override);
        Ok(())
    }

    fn show(&self) -> Result<(), AppearanceWindowError> {
        *self.show_count.lock().unwrap() += 1;
        Ok(())
    }

    fn restore_and_focus(&self) -> Result<(), AppearanceWindowError> {
        *self.restore_count.lock().unwrap() += 1;
        if self.restore_fail {
            Err(AppearanceWindowError {
                code: "appearance_window_restore_failed",
                safe_message: "창 복원 실패",
            })
        } else {
            Ok(())
        }
    }

    fn prompt_bootstrap_recovery(&self) -> Result<BootstrapRecoveryAction, AppearanceWindowError> {
        Ok(BootstrapRecoveryAction::Retry)
    }

    fn reload(&self) -> Result<(), AppearanceWindowError> {
        Ok(())
    }

    fn quit(&self) {}
}

impl AppearanceEventPort for FakeWindow {
    fn emit_changed(&self, event: &AppearanceChanged) -> Result<(), AppearanceWindowError> {
        self.trace.lock().unwrap().push("emit");
        if self.emit_fail {
            return Err(AppearanceWindowError {
                code: "appearance_event_emit_failed",
                safe_message: "외관 변경 알림을 전달하지 못했습니다.",
            });
        }
        self.events.lock().unwrap().push(event.clone());
        Ok(())
    }
}

#[test]
fn emit_failure_returns_updated_state_with_a_safe_diagnostic() {
    let store = Arc::new(FakeStore::default());
    let window = Arc::new(FakeWindow {
        emit_fail: true,
        ..FakeWindow::default()
    });
    let appearance = context(LogicalMode::System, ResolvedMode::Light, store, window);

    let update = appearance.set_mode(LogicalMode::Dark).unwrap();

    assert_eq!(update.state.logical_mode, LogicalMode::Dark);
    assert_eq!(update.state.resolved_mode, ResolvedMode::Dark);
    assert_eq!(
        update.diagnostic.unwrap().code,
        "appearance_event_emit_failed"
    );
}

fn context(
    initial: LogicalMode,
    system: ResolvedMode,
    store: Arc<FakeStore>,
    window: Arc<FakeWindow>,
) -> AppearanceContext {
    let window_port: Arc<dyn AppearanceWindowPort> = window.clone();
    let event_port: Arc<dyn AppearanceEventPort> = window;
    AppearanceContext::new(initial, system, store, window_port, event_port)
}

#[test]
fn user_transition_is_serialized_window_store_emit() {
    let trace = Arc::new(Mutex::new(Vec::new()));
    let store = Arc::new(FakeStore {
        trace: Arc::clone(&trace),
        fail: false,
    });
    let window = Arc::new(FakeWindow {
        trace: Arc::clone(&trace),
        ..FakeWindow::default()
    });
    let appearance = context(
        LogicalMode::System,
        ResolvedMode::Light,
        store,
        Arc::clone(&window),
    );

    let update = appearance.set_mode(LogicalMode::Dark).unwrap();

    assert_eq!(*trace.lock().unwrap(), ["window", "store", "emit"]);
    assert_eq!(
        update.state,
        AppearanceState {
            logical_mode: LogicalMode::Dark,
            resolved_mode: ResolvedMode::Dark,
            revision: 1,
            persisted: true,
        }
    );
    assert_eq!(update.diagnostic, None);
    assert_eq!(
        *window.overrides.lock().unwrap(),
        [WindowThemeOverride::Dark]
    );
    assert_eq!(
        window.events.lock().unwrap()[0].source,
        AppearanceChangeSource::User
    );
}

#[test]
fn write_failure_keeps_session_mode_and_emits_unpersisted_state() {
    let trace = Arc::new(Mutex::new(Vec::new()));
    let store = Arc::new(FakeStore {
        trace: Arc::clone(&trace),
        fail: true,
    });
    let window = Arc::new(FakeWindow {
        trace: Arc::clone(&trace),
        ..FakeWindow::default()
    });
    let appearance = context(
        LogicalMode::System,
        ResolvedMode::Light,
        store,
        Arc::clone(&window),
    );

    let update = appearance.set_mode(LogicalMode::Dark).unwrap();

    assert_eq!(*trace.lock().unwrap(), ["window", "store", "emit"]);
    assert_eq!(update.state.logical_mode, LogicalMode::Dark);
    assert_eq!(update.state.resolved_mode, ResolvedMode::Dark);
    assert_eq!(update.state.revision, 1);
    assert!(!update.state.persisted);
    assert_eq!(update.diagnostic.unwrap().safe_message, "설정 저장 실패");
    assert!(!window.events.lock().unwrap()[0].state.persisted);
    assert_eq!(appearance.state(), update.state);
}

#[test]
fn system_event_only_changes_a_system_session_and_never_persists() {
    let trace = Arc::new(Mutex::new(Vec::new()));
    let store = Arc::new(FakeStore {
        trace: Arc::clone(&trace),
        fail: false,
    });
    let window = Arc::new(FakeWindow {
        trace: Arc::clone(&trace),
        ..FakeWindow::default()
    });
    let appearance = context(
        LogicalMode::System,
        ResolvedMode::Light,
        store,
        Arc::clone(&window),
    );

    let changed = appearance
        .system_theme_changed(ResolvedMode::Dark)
        .unwrap()
        .unwrap();
    assert_eq!(*trace.lock().unwrap(), ["window", "emit"]);
    assert_eq!(changed.state.revision, 1);
    assert_eq!(changed.state.resolved_mode, ResolvedMode::Dark);
    assert_eq!(changed.source, AppearanceChangeSource::System);
    assert_eq!(
        *window.overrides.lock().unwrap(),
        [WindowThemeOverride::FollowSystem]
    );

    trace.lock().unwrap().clear();
    assert_eq!(
        appearance.system_theme_changed(ResolvedMode::Dark).unwrap(),
        None
    );
    assert!(trace.lock().unwrap().is_empty());

    let explicit_store = Arc::new(FakeStore::default());
    let explicit_window = Arc::new(FakeWindow::default());
    let explicit = context(
        LogicalMode::Light,
        ResolvedMode::Light,
        explicit_store,
        Arc::clone(&explicit_window),
    );
    assert_eq!(
        explicit.system_theme_changed(ResolvedMode::Dark).unwrap(),
        None
    );
    assert_eq!(explicit.state().revision, 0);
    assert!(explicit_window.events.lock().unwrap().is_empty());
}

#[test]
fn concurrent_transitions_emit_monotonic_revisions() {
    let store = Arc::new(FakeStore::default());
    let window = Arc::new(FakeWindow::default());
    let appearance = Arc::new(context(
        LogicalMode::System,
        ResolvedMode::Light,
        store,
        Arc::clone(&window),
    ));

    let first = {
        let appearance = Arc::clone(&appearance);
        std::thread::spawn(move || appearance.set_mode(LogicalMode::Light))
    };
    let second = {
        let appearance = Arc::clone(&appearance);
        std::thread::spawn(move || appearance.set_mode(LogicalMode::Dark))
    };
    first.join().unwrap().unwrap();
    second.join().unwrap().unwrap();

    let revisions = window
        .events
        .lock()
        .unwrap()
        .iter()
        .map(|event| event.state.revision)
        .collect::<Vec<_>>();
    assert_eq!(revisions, [1, 2]);
}

#[test]
fn only_the_current_bootstrap_revision_can_show_the_window() {
    let store = Arc::new(FakeStore::default());
    let window = Arc::new(FakeWindow::default());
    let appearance = context(
        LogicalMode::System,
        ResolvedMode::Light,
        store,
        Arc::clone(&window),
    );

    let stale = appearance
        .complete_bootstrap(7, ResolvedMode::Light)
        .unwrap();
    assert_eq!(stale.instruction, BootstrapInstruction::Refresh);
    assert_eq!(*window.show_count.lock().unwrap(), 0);

    let current = appearance
        .complete_bootstrap(0, ResolvedMode::Light)
        .unwrap();
    assert_eq!(current.instruction, BootstrapInstruction::Show);
    assert_eq!(*window.show_count.lock().unwrap(), 1);

    let repeated = appearance
        .complete_bootstrap(0, ResolvedMode::Light)
        .unwrap();
    assert_eq!(repeated.instruction, BootstrapInstruction::Show);
    assert_eq!(*window.show_count.lock().unwrap(), 1);
}

#[test]
fn current_revision_with_wrong_resolved_mode_never_shows_the_window() {
    let store = Arc::new(FakeStore::default());
    let window = Arc::new(FakeWindow::default());
    let appearance = context(
        LogicalMode::System,
        ResolvedMode::Light,
        store,
        Arc::clone(&window),
    );

    let mismatch = appearance
        .complete_bootstrap(0, ResolvedMode::Dark)
        .unwrap();
    assert_eq!(mismatch.instruction, BootstrapInstruction::Refresh);
    assert_eq!(*window.show_count.lock().unwrap(), 0);

    let matching = appearance
        .complete_bootstrap(0, ResolvedMode::Light)
        .unwrap();
    assert_eq!(matching.instruction, BootstrapInstruction::Show);
    assert_eq!(*window.show_count.lock().unwrap(), 1);
}

#[test]
fn foreground_requests_before_first_paint_are_coalesced_until_the_window_is_shown() {
    let store = Arc::new(FakeStore::default());
    let window = Arc::new(FakeWindow::default());
    let appearance = context(
        LogicalMode::System,
        ResolvedMode::Light,
        store,
        Arc::clone(&window),
    );

    appearance.enqueue_main_window_reactivation().unwrap();
    appearance.enqueue_main_window_reactivation().unwrap();
    let before = appearance.state();
    assert_eq!(*window.restore_count.lock().unwrap(), 0);

    appearance
        .complete_bootstrap(before.revision, before.resolved_mode)
        .unwrap();

    assert_eq!(*window.show_count.lock().unwrap(), 1);
    assert_eq!(*window.restore_count.lock().unwrap(), 1);
    assert_eq!(appearance.state(), before);
}

#[test]
fn every_foreground_request_after_first_paint_restores_the_existing_window() {
    let store = Arc::new(FakeStore::default());
    let window = Arc::new(FakeWindow::default());
    let appearance = context(
        LogicalMode::System,
        ResolvedMode::Light,
        store,
        Arc::clone(&window),
    );
    let initial = appearance.state();
    appearance
        .complete_bootstrap(initial.revision, initial.resolved_mode)
        .unwrap();

    appearance.enqueue_main_window_reactivation().unwrap();
    assert_eq!(appearance.state(), initial);
    appearance.enqueue_main_window_reactivation().unwrap();
    assert_eq!(appearance.state(), initial);

    assert_eq!(*window.show_count.lock().unwrap(), 1);
    assert_eq!(*window.restore_count.lock().unwrap(), 2);
}

#[test]
fn foreground_restore_failure_does_not_kill_the_appearance_actor() {
    let store = Arc::new(FakeStore::default());
    let window = Arc::new(FakeWindow {
        restore_fail: true,
        ..FakeWindow::default()
    });
    let appearance = context(
        LogicalMode::System,
        ResolvedMode::Light,
        store,
        Arc::clone(&window),
    );
    let initial = appearance.state();
    appearance
        .complete_bootstrap(initial.revision, initial.resolved_mode)
        .unwrap();

    appearance.enqueue_main_window_reactivation().unwrap();
    assert_eq!(appearance.state(), initial);
    appearance.enqueue_main_window_reactivation().unwrap();
    assert_eq!(appearance.state(), initial);
    assert_eq!(*window.restore_count.lock().unwrap(), 2);
}

struct BlockingWindow {
    entered: mpsc::SyncSender<()>,
    release: Mutex<mpsc::Receiver<()>>,
}

impl AppearanceWindowPort for BlockingWindow {
    fn current_system_mode(&self) -> Result<ResolvedMode, AppearanceWindowError> {
        Ok(ResolvedMode::Light)
    }

    fn apply_theme(
        &self,
        _theme_override: WindowThemeOverride,
        _resolved_mode: ResolvedMode,
    ) -> Result<(), AppearanceWindowError> {
        self.entered.send(()).unwrap();
        self.release.lock().unwrap().recv().unwrap();
        Ok(())
    }

    fn show(&self) -> Result<(), AppearanceWindowError> {
        Ok(())
    }
    fn restore_and_focus(&self) -> Result<(), AppearanceWindowError> {
        Ok(())
    }
    fn prompt_bootstrap_recovery(&self) -> Result<BootstrapRecoveryAction, AppearanceWindowError> {
        Ok(BootstrapRecoveryAction::Quit)
    }
    fn reload(&self) -> Result<(), AppearanceWindowError> {
        Ok(())
    }
    fn quit(&self) {}
}

impl AppearanceEventPort for BlockingWindow {
    fn emit_changed(&self, _event: &AppearanceChanged) -> Result<(), AppearanceWindowError> {
        Ok(())
    }
}

#[test]
fn window_callback_enqueue_returns_before_the_actor_finishes_native_work() {
    let (entered_sender, entered_receiver) = mpsc::sync_channel(1);
    let (release_sender, release_receiver) = mpsc::sync_channel(1);
    let window = Arc::new(BlockingWindow {
        entered: entered_sender,
        release: Mutex::new(release_receiver),
    });
    let window_port: Arc<dyn AppearanceWindowPort> = window.clone();
    let event_port: Arc<dyn AppearanceEventPort> = window;
    let appearance = AppearanceContext::new(
        LogicalMode::System,
        ResolvedMode::Light,
        Arc::new(FakeStore::default()),
        window_port,
        event_port,
    );

    appearance
        .enqueue_system_theme_changed(ResolvedMode::Dark)
        .unwrap();
    entered_receiver
        .recv_timeout(Duration::from_secs(1))
        .expect("actor must begin native work after the callback already returned");
    release_sender.send(()).unwrap();

    assert_eq!(appearance.state().resolved_mode, ResolvedMode::Dark);
}
