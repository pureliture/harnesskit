use std::sync::{Arc, Mutex};

use harness_desktop_lib::contexts::appearance::{
    bootstrap_recovery_action, AppearanceChanged, AppearanceContext, AppearanceEventPort,
    AppearancePreferencePort, AppearanceStoreError, AppearanceWindowError, AppearanceWindowPort,
    BootstrapRecoveryAction, LogicalMode, ResolvedMode, WindowThemeOverride,
};

struct FakeStore;

impl AppearancePreferencePort for FakeStore {
    fn save(&self, _logical_mode: LogicalMode) -> Result<(), AppearanceStoreError> {
        Ok(())
    }
}

struct RecoveryWindow {
    action: Mutex<BootstrapRecoveryAction>,
    prompts: Mutex<usize>,
    reloads: Mutex<usize>,
    quits: Mutex<usize>,
    shows: Mutex<usize>,
    reload_fails: bool,
}

impl RecoveryWindow {
    fn new(action: BootstrapRecoveryAction) -> Self {
        Self {
            action: Mutex::new(action),
            prompts: Mutex::new(0),
            reloads: Mutex::new(0),
            quits: Mutex::new(0),
            shows: Mutex::new(0),
            reload_fails: false,
        }
    }
}

impl AppearanceWindowPort for RecoveryWindow {
    fn current_system_mode(&self) -> Result<ResolvedMode, AppearanceWindowError> {
        Ok(ResolvedMode::Light)
    }

    fn apply_theme(
        &self,
        _theme_override: WindowThemeOverride,
        _resolved_mode: ResolvedMode,
    ) -> Result<(), AppearanceWindowError> {
        Ok(())
    }

    fn show(&self) -> Result<(), AppearanceWindowError> {
        *self.shows.lock().unwrap() += 1;
        Ok(())
    }

    fn restore_and_focus(&self) -> Result<(), AppearanceWindowError> {
        Ok(())
    }

    fn prompt_bootstrap_recovery(&self) -> Result<BootstrapRecoveryAction, AppearanceWindowError> {
        *self.prompts.lock().unwrap() += 1;
        Ok(*self.action.lock().unwrap())
    }

    fn reload(&self) -> Result<(), AppearanceWindowError> {
        *self.reloads.lock().unwrap() += 1;
        if self.reload_fails {
            Err(AppearanceWindowError {
                code: "appearance_bootstrap_reload_failed",
                safe_message: "앱 표시를 다시 시도할 수 없습니다.",
            })
        } else {
            Ok(())
        }
    }

    fn quit(&self) {
        *self.quits.lock().unwrap() += 1;
    }
}

#[test]
fn reload_failure_enters_quitting_state_before_exit() {
    let window = Arc::new(RecoveryWindow {
        reload_fails: true,
        ..RecoveryWindow::new(BootstrapRecoveryAction::Retry)
    });
    let appearance = context(Arc::clone(&window));
    let generation = appearance.bootstrap_watchdog_generation();

    let recovery = appearance
        .handle_bootstrap_timeout(generation)
        .unwrap()
        .unwrap();
    assert_eq!(recovery.action, BootstrapRecoveryAction::Quit);
    assert_eq!(*window.quits.lock().unwrap(), 1);
    assert_eq!(
        appearance
            .handle_bootstrap_timeout(recovery.generation)
            .unwrap(),
        None
    );
    assert_eq!(*window.prompts.lock().unwrap(), 1);
}

impl AppearanceEventPort for RecoveryWindow {
    fn emit_changed(&self, _event: &AppearanceChanged) -> Result<(), AppearanceWindowError> {
        Ok(())
    }
}

fn context(window: Arc<RecoveryWindow>) -> AppearanceContext {
    let window_port: Arc<dyn AppearanceWindowPort> = window.clone();
    let event_port: Arc<dyn AppearanceEventPort> = window;
    AppearanceContext::new(
        LogicalMode::System,
        ResolvedMode::Light,
        Arc::new(FakeStore),
        window_port,
        event_port,
    )
}

#[test]
fn stale_generation_never_prompts_and_retry_rotates_before_reload() {
    let window = Arc::new(RecoveryWindow::new(BootstrapRecoveryAction::Retry));
    let appearance = context(Arc::clone(&window));
    let generation = appearance.bootstrap_watchdog_generation();

    assert_eq!(
        appearance.handle_bootstrap_timeout(generation + 1).unwrap(),
        None
    );
    assert_eq!(*window.prompts.lock().unwrap(), 0);

    let recovery = appearance
        .handle_bootstrap_timeout(generation)
        .unwrap()
        .unwrap();
    assert_eq!(recovery.action, BootstrapRecoveryAction::Retry);
    assert!(recovery.generation > generation);
    assert_eq!(
        appearance.bootstrap_watchdog_generation(),
        recovery.generation
    );
    assert_eq!(appearance.state().revision, 1);
    assert_eq!(
        appearance
            .complete_bootstrap(0, ResolvedMode::Light)
            .unwrap()
            .instruction,
        harness_desktop_lib::contexts::appearance::BootstrapInstruction::Refresh
    );
    assert_eq!(*window.prompts.lock().unwrap(), 1);
    assert_eq!(*window.reloads.lock().unwrap(), 1);
    assert_eq!(*window.quits.lock().unwrap(), 0);

    assert_eq!(
        appearance.handle_bootstrap_timeout(generation).unwrap(),
        None
    );
    assert_eq!(*window.prompts.lock().unwrap(), 1);
}

#[test]
fn completed_bootstrap_cancels_timeout_and_quit_never_shows() {
    let completed_window = Arc::new(RecoveryWindow::new(BootstrapRecoveryAction::Quit));
    let completed = context(Arc::clone(&completed_window));
    let generation = completed.bootstrap_watchdog_generation();
    completed
        .complete_bootstrap(0, ResolvedMode::Light)
        .unwrap();
    assert_eq!(
        completed.handle_bootstrap_timeout(generation).unwrap(),
        None
    );
    assert_eq!(*completed_window.prompts.lock().unwrap(), 0);
    assert_eq!(*completed_window.shows.lock().unwrap(), 1);

    let quit_window = Arc::new(RecoveryWindow::new(BootstrapRecoveryAction::Quit));
    let quit = context(Arc::clone(&quit_window));
    let recovery = quit
        .handle_bootstrap_timeout(quit.bootstrap_watchdog_generation())
        .unwrap()
        .unwrap();
    assert_eq!(recovery.action, BootstrapRecoveryAction::Quit);
    assert_eq!(*quit_window.quits.lock().unwrap(), 1);
    assert_eq!(*quit_window.reloads.lock().unwrap(), 0);
    assert_eq!(*quit_window.shows.lock().unwrap(), 0);
}

#[test]
fn any_non_retry_native_response_fails_closed_to_quit() {
    assert_eq!(
        bootstrap_recovery_action(true),
        BootstrapRecoveryAction::Retry
    );
    assert_eq!(
        bootstrap_recovery_action(false),
        BootstrapRecoveryAction::Quit
    );
}
