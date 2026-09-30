use std::fmt;
use std::time::Duration;

#[cfg(target_os = "macos")]
use std::ptr::NonNull;
#[cfg(target_os = "macos")]
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc, Arc,
};

use super::{AppearanceChanged, ResolvedMode};
#[cfg(not(target_os = "macos"))]
use tauri::Theme;
use tauri::{Manager, WebviewWindow};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WindowThemeOverride {
    FollowSystem,
    Light,
    Dark,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BootstrapRecoveryAction {
    Retry,
    Quit,
}

pub fn bootstrap_recovery_action(first_button: bool) -> BootstrapRecoveryAction {
    if first_button {
        BootstrapRecoveryAction::Retry
    } else {
        BootstrapRecoveryAction::Quit
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppearanceWindowError {
    pub code: &'static str,
    pub safe_message: &'static str,
}

impl fmt::Display for AppearanceWindowError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.safe_message)
    }
}

impl std::error::Error for AppearanceWindowError {}

pub trait AppearanceWindowPort: Send + Sync {
    fn current_system_mode(&self) -> Result<ResolvedMode, AppearanceWindowError>;

    fn apply_theme(
        &self,
        theme_override: WindowThemeOverride,
        resolved_mode: ResolvedMode,
    ) -> Result<(), AppearanceWindowError>;

    fn show(&self) -> Result<(), AppearanceWindowError>;

    fn restore_and_focus(&self) -> Result<(), AppearanceWindowError>;

    fn prompt_bootstrap_recovery(&self) -> Result<BootstrapRecoveryAction, AppearanceWindowError>;

    fn reload(&self) -> Result<(), AppearanceWindowError>;

    fn quit(&self);
}

pub trait AppearanceEventPort: Send + Sync {
    fn emit_changed(&self, event: &AppearanceChanged) -> Result<(), AppearanceWindowError>;
}

trait WindowRestorationSteps {
    fn unhide(&self) -> Result<(), AppearanceWindowError>;
    fn unminimize(&self) -> Result<(), AppearanceWindowError>;
    fn show(&self) -> Result<(), AppearanceWindowError>;
    fn activate(&self) -> Result<(), AppearanceWindowError>;
    fn focus(&self) -> Result<(), AppearanceWindowError>;
}

fn restore_with_steps(steps: &dyn WindowRestorationSteps) -> Result<(), AppearanceWindowError> {
    let mut first_error = None;
    for result in [
        steps.unhide(),
        steps.unminimize(),
        steps.show(),
        steps.focus(),
        steps.activate(),
    ] {
        if let Err(error) = result {
            first_error.get_or_insert(error);
        }
    }
    first_error.map_or(Ok(()), Err)
}

const REACTIVATION_SETTLE_DELAY: Duration = Duration::from_millis(350);

fn with_activation_settle_retry<Attempt, Wait>(
    mut attempt: Attempt,
    wait: Wait,
) -> Result<(), AppearanceWindowError>
where
    Attempt: FnMut() -> Result<(), AppearanceWindowError>,
    Wait: FnOnce(Duration),
{
    let immediate = attempt();
    wait(REACTIVATION_SETTLE_DELAY);
    let settled = attempt();
    immediate.and(settled)
}

#[derive(Clone)]
pub(crate) struct TauriAppearanceWindowPort {
    window: WebviewWindow,
}

#[cfg(target_os = "macos")]
struct MacWindowRestorationSteps<'a> {
    window: &'a WebviewWindow,
    marker: objc2::MainThreadMarker,
}

#[cfg(target_os = "macos")]
impl WindowRestorationSteps for MacWindowRestorationSteps<'_> {
    fn unhide(&self) -> Result<(), AppearanceWindowError> {
        use objc2_app_kit::NSApplication;

        NSApplication::sharedApplication(self.marker).unhide(None);
        Ok(())
    }

    fn unminimize(&self) -> Result<(), AppearanceWindowError> {
        self.window
            .unminimize()
            .map_err(|_| window_error("appearance_window_restore_failed"))
    }

    fn show(&self) -> Result<(), AppearanceWindowError> {
        self.window
            .show()
            .map_err(|_| window_error("appearance_window_show_failed"))
    }

    fn activate(&self) -> Result<(), AppearanceWindowError> {
        activate_current_application(self.marker)
    }

    fn focus(&self) -> Result<(), AppearanceWindowError> {
        use objc2_app_kit::NSWindow;

        let mut first_error = None;
        match self.window.ns_window() {
            Ok(raw) => match NonNull::new(raw.cast::<NSWindow>()) {
                Some(pointer) => {
                    let native_window = unsafe { pointer.as_ref() };
                    if native_window.isMiniaturized() {
                        native_window.deminiaturize(None);
                    }
                    native_window.makeKeyAndOrderFront(None);
                }
                None => {
                    first_error = Some(window_error("appearance_window_handle_unavailable"));
                }
            },
            Err(_) => {
                first_error = Some(window_error("appearance_window_handle_unavailable"));
            }
        }
        if self.window.set_focus().is_err() && first_error.is_none() {
            first_error = Some(window_error("appearance_window_focus_failed"));
        }
        first_error.map_or(Ok(()), Err)
    }
}

#[cfg(not(target_os = "macos"))]
struct PortableWindowRestorationSteps<'a> {
    window: &'a WebviewWindow,
}

#[cfg(not(target_os = "macos"))]
impl WindowRestorationSteps for PortableWindowRestorationSteps<'_> {
    fn unhide(&self) -> Result<(), AppearanceWindowError> {
        Ok(())
    }

    fn unminimize(&self) -> Result<(), AppearanceWindowError> {
        self.window
            .unminimize()
            .map_err(|_| window_error("appearance_window_restore_failed"))
    }

    fn show(&self) -> Result<(), AppearanceWindowError> {
        self.window
            .show()
            .map_err(|_| window_error("appearance_window_show_failed"))
    }

    fn activate(&self) -> Result<(), AppearanceWindowError> {
        Ok(())
    }

    fn focus(&self) -> Result<(), AppearanceWindowError> {
        self.window
            .set_focus()
            .map_err(|_| window_error("appearance_window_focus_failed"))
    }
}

const MAIN_THREAD_REPLY_TIMEOUT: Duration = Duration::from_secs(15);
const ALERT_REPLY_TIMEOUT: Duration = Duration::from_secs(300);

#[cfg(target_os = "macos")]
fn uses_modern_application_activation(macos_major: isize) -> bool {
    macos_major >= 14
}

#[cfg(target_os = "macos")]
fn activate_current_application(
    marker: objc2::MainThreadMarker,
) -> Result<(), AppearanceWindowError> {
    use objc2_app_kit::NSApplication;
    use objc2_foundation::NSProcessInfo;

    let application = NSApplication::sharedApplication(marker);
    let macos_major = NSProcessInfo::processInfo()
        .operatingSystemVersion()
        .majorVersion;
    if uses_modern_application_activation(macos_major) {
        application.activate();
    } else {
        #[allow(deprecated)]
        application.activateIgnoringOtherApps(true);
    }
    Ok(())
}

impl TauriAppearanceWindowPort {
    pub(crate) fn new(window: WebviewWindow) -> Self {
        Self { window }
    }

    #[cfg(target_os = "macos")]
    fn run_on_main_thread<T, F>(
        &self,
        timeout: Duration,
        task: F,
    ) -> Result<T, AppearanceWindowError>
    where
        T: Send + 'static,
        F: FnOnce(&WebviewWindow, objc2::MainThreadMarker) -> Result<T, AppearanceWindowError>
            + Send
            + 'static,
    {
        if let Some(marker) = objc2::MainThreadMarker::new() {
            return task(&self.window, marker);
        }

        let window = self.window.clone();
        let (sender, receiver) = mpsc::sync_channel(1);
        let pending = Arc::new(AtomicBool::new(true));
        let task_pending = Arc::clone(&pending);
        self.window
            .run_on_main_thread(move || {
                if !task_pending.swap(false, Ordering::AcqRel) {
                    let _ = sender.send(Err(window_error("appearance_main_thread_cancelled")));
                    return;
                }
                let result = objc2::MainThreadMarker::new()
                    .ok_or_else(|| window_error("appearance_main_thread_unavailable"))
                    .and_then(|marker| task(&window, marker));
                let _ = sender.send(result);
            })
            .map_err(|_| window_error("appearance_main_thread_unavailable"))?;
        match receiver.recv_timeout(timeout) {
            Ok(result) => result,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                pending.store(false, Ordering::Release);
                Err(window_error("appearance_main_thread_timeout"))
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                Err(window_error("appearance_main_thread_unavailable"))
            }
        }
    }
}

impl AppearanceWindowPort for TauriAppearanceWindowPort {
    fn current_system_mode(&self) -> Result<ResolvedMode, AppearanceWindowError> {
        #[cfg(target_os = "macos")]
        {
            self.run_on_main_thread(MAIN_THREAD_REPLY_TIMEOUT, |_window, marker| {
                use objc2_app_kit::NSApplication;

                let appearance = NSApplication::sharedApplication(marker).effectiveAppearance();
                let name = appearance.name().to_string();
                Ok(if name.contains("Dark") {
                    ResolvedMode::Dark
                } else {
                    ResolvedMode::Light
                })
            })
        }

        #[cfg(not(target_os = "macos"))]
        {
            self.window
                .theme()
                .map(theme_to_resolved)
                .map_err(|_| window_error("appearance_system_mode_unavailable"))
        }
    }

    fn apply_theme(
        &self,
        theme_override: WindowThemeOverride,
        _resolved_mode: ResolvedMode,
    ) -> Result<(), AppearanceWindowError> {
        #[cfg(target_os = "macos")]
        {
            self.run_on_main_thread(MAIN_THREAD_REPLY_TIMEOUT, move |window, _marker| {
                use objc2_app_kit::{NSAppearance, NSAppearanceCustomization, NSWindow};
                use objc2_foundation::NSString;

                let raw = window
                    .ns_window()
                    .map_err(|_| window_error("appearance_window_handle_unavailable"))?;
                let pointer = NonNull::new(raw.cast::<NSWindow>())
                    .ok_or_else(|| window_error("appearance_window_handle_unavailable"))?;
                let window = unsafe { pointer.as_ref() };
                let appearance = match theme_override {
                    WindowThemeOverride::FollowSystem => None,
                    WindowThemeOverride::Light => {
                        NSAppearance::appearanceNamed(&NSString::from_str("NSAppearanceNameAqua"))
                    }
                    WindowThemeOverride::Dark => NSAppearance::appearanceNamed(
                        &NSString::from_str("NSAppearanceNameDarkAqua"),
                    ),
                };
                if theme_override != WindowThemeOverride::FollowSystem && appearance.is_none() {
                    return Err(window_error("appearance_native_theme_unavailable"));
                }
                window.setAppearance(appearance.as_deref());
                Ok(())
            })
        }

        #[cfg(not(target_os = "macos"))]
        {
            let theme = match theme_override {
                WindowThemeOverride::FollowSystem => None,
                WindowThemeOverride::Light => Some(Theme::Light),
                WindowThemeOverride::Dark => Some(Theme::Dark),
            };
            self.window
                .set_theme(theme)
                .map_err(|_| window_error("appearance_native_theme_unavailable"))
        }
    }

    fn show(&self) -> Result<(), AppearanceWindowError> {
        #[cfg(target_os = "macos")]
        {
            with_activation_settle_retry(
                || {
                    self.run_on_main_thread(MAIN_THREAD_REPLY_TIMEOUT, |window, marker| {
                        use objc2_app_kit::{
                            NSApplication, NSRunningApplication, NSWindow,
                        };

                        let raw = window.ns_window().map_err(|_| {
                            window_error("appearance_window_handle_unavailable")
                        })?;
                        let pointer = NonNull::new(raw.cast::<NSWindow>()).ok_or_else(|| {
                            window_error("appearance_window_handle_unavailable")
                        })?;
                        let native_window = unsafe { pointer.as_ref() };
                        let application = NSApplication::sharedApplication(marker);
                        application.unhide(None);
                        window
                            .show()
                            .map_err(|_| window_error("appearance_window_show_failed"))?;
                        native_window.makeKeyAndOrderFront(None);
                        if let Err(error) = activate_current_application(marker) {
                            log::warn!(
                                "appearance first-show activation deferred: {}",
                                error.code
                            );
                        }
                        let running_application = NSRunningApplication::currentApplication();
                        log::info!(
                            "appearance window presented: foreground_requested=true, active={}, key={}",
                            running_application.isActive(),
                            native_window.isKeyWindow()
                        );
                        Ok(())
                    })
                },
                std::thread::sleep,
            )
        }

        #[cfg(not(target_os = "macos"))]
        {
            self.window
                .show()
                .map_err(|_| window_error("appearance_window_show_failed"))
        }
    }

    fn restore_and_focus(&self) -> Result<(), AppearanceWindowError> {
        #[cfg(target_os = "macos")]
        {
            with_activation_settle_retry(
                || {
                    self.run_on_main_thread(MAIN_THREAD_REPLY_TIMEOUT, |window, marker| {
                        restore_with_steps(&MacWindowRestorationSteps { window, marker })
                    })
                },
                std::thread::sleep,
            )
        }

        #[cfg(not(target_os = "macos"))]
        {
            with_activation_settle_retry(
                || {
                    restore_with_steps(&PortableWindowRestorationSteps {
                        window: &self.window,
                    })
                },
                std::thread::sleep,
            )
        }
    }

    fn prompt_bootstrap_recovery(&self) -> Result<BootstrapRecoveryAction, AppearanceWindowError> {
        #[cfg(target_os = "macos")]
        {
            self.run_on_main_thread(ALERT_REPLY_TIMEOUT, |_window, marker| {
                use objc2_app_kit::{
                    NSAlert, NSAlertFirstButtonReturn, NSAlertStyle, NSApplication,
                };
                use objc2_foundation::NSString;

                let application = NSApplication::sharedApplication(marker);
                #[allow(deprecated)]
                application.activateIgnoringOtherApps(true);
                let alert = NSAlert::new(marker);
                alert.setAlertStyle(NSAlertStyle::Informational);
                alert.setMessageText(&NSString::from_str("HarnessKit을 표시하지 못했습니다."));
                alert.setInformativeText(&NSString::from_str(
                    "다시 시도하거나 앱을 종료할 수 있습니다.",
                ));
                let _retry = alert.addButtonWithTitle(&NSString::from_str("다시 시도"));
                let _quit = alert.addButtonWithTitle(&NSString::from_str("종료"));
                Ok(bootstrap_recovery_action(
                    alert.runModal() == NSAlertFirstButtonReturn,
                ))
            })
        }

        #[cfg(not(target_os = "macos"))]
        {
            Ok(BootstrapRecoveryAction::Quit)
        }
    }

    fn reload(&self) -> Result<(), AppearanceWindowError> {
        self.window
            .reload()
            .map_err(|_| window_error("appearance_bootstrap_reload_failed"))
    }

    fn quit(&self) {
        self.window.app_handle().exit(0);
    }
}

fn window_error(code: &'static str) -> AppearanceWindowError {
    AppearanceWindowError {
        code,
        safe_message: "외관 상태를 적용할 수 없습니다.",
    }
}

#[cfg(not(target_os = "macos"))]
fn theme_to_resolved(theme: Theme) -> ResolvedMode {
    match theme {
        Theme::Dark => ResolvedMode::Dark,
        _ => ResolvedMode::Light,
    }
}

#[cfg(test)]
mod restore_tests {
    use std::sync::Mutex;
    use std::time::Duration;

    use super::{
        restore_with_steps, with_activation_settle_retry, AppearanceWindowError,
        WindowRestorationSteps,
    };

    struct FakeSteps {
        calls: Mutex<Vec<&'static str>>,
        failures: Vec<&'static str>,
    }

    impl FakeSteps {
        fn step(&self, name: &'static str) -> Result<(), AppearanceWindowError> {
            self.calls.lock().unwrap().push(name);
            if self.failures.contains(&name) {
                Err(AppearanceWindowError {
                    code: name,
                    safe_message: "복원 실패",
                })
            } else {
                Ok(())
            }
        }
    }

    impl WindowRestorationSteps for FakeSteps {
        fn unhide(&self) -> Result<(), AppearanceWindowError> {
            self.step("unhide")
        }
        fn unminimize(&self) -> Result<(), AppearanceWindowError> {
            self.step("unminimize")
        }
        fn show(&self) -> Result<(), AppearanceWindowError> {
            self.step("show")
        }
        fn activate(&self) -> Result<(), AppearanceWindowError> {
            self.step("activate")
        }
        fn focus(&self) -> Result<(), AppearanceWindowError> {
            self.step("focus")
        }
    }

    #[test]
    fn restoration_runs_every_step_and_returns_the_first_failure() {
        let steps = FakeSteps {
            calls: Mutex::new(Vec::new()),
            failures: vec!["unminimize", "activate"],
        };

        let error = restore_with_steps(&steps).unwrap_err();

        assert_eq!(error.code, "unminimize");
        assert_eq!(
            *steps.calls.lock().unwrap(),
            ["unhide", "unminimize", "show", "focus", "activate"]
        );
    }

    #[test]
    fn restoration_settle_retry_runs_twice_and_preserves_immediate_failure() {
        let mut attempts = 0;
        let mut waits = Vec::new();

        let error = with_activation_settle_retry(
            || {
                attempts += 1;
                if attempts == 1 {
                    Err(AppearanceWindowError {
                        code: "immediate",
                        safe_message: "복원 실패",
                    })
                } else {
                    Ok(())
                }
            },
            |duration| waits.push(duration),
        )
        .unwrap_err();

        assert_eq!(attempts, 2);
        assert_eq!(waits, [Duration::from_millis(350)]);
        assert_eq!(error.code, "immediate");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn application_activation_api_matches_the_supported_macos_version() {
        assert!(!super::uses_modern_application_activation(13));
        assert!(super::uses_modern_application_activation(14));
        assert!(super::uses_modern_application_activation(26));
    }
}
