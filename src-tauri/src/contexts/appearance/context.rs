use std::fmt;
use std::sync::{mpsc, Arc};

use super::resolver::resolve_mode;
use super::store::{AppearanceDiagnostic, AppearancePreferencePort};
use super::window::{
    AppearanceEventPort, AppearanceWindowError, AppearanceWindowPort, BootstrapRecoveryAction,
    WindowThemeOverride,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogicalMode {
    System,
    Light,
    Dark,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResolvedMode {
    Light,
    Dark,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AppearanceState {
    pub logical_mode: LogicalMode,
    pub resolved_mode: ResolvedMode,
    pub revision: u64,
    pub persisted: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AppearanceChangeSource {
    User,
    System,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppearanceChanged {
    pub state: AppearanceState,
    pub source: AppearanceChangeSource,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppearanceUpdate {
    pub state: AppearanceState,
    pub diagnostic: Option<AppearanceDiagnostic>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BootstrapInstruction {
    Show,
    Refresh,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BootstrapCompletion {
    pub instruction: BootstrapInstruction,
    pub state: AppearanceState,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BootstrapRecovery {
    pub action: BootstrapRecoveryAction,
    pub generation: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppearanceContextError {
    pub code: &'static str,
    pub safe_message: &'static str,
}

impl fmt::Display for AppearanceContextError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.safe_message)
    }
}

impl std::error::Error for AppearanceContextError {}

impl From<AppearanceWindowError> for AppearanceContextError {
    fn from(error: AppearanceWindowError) -> Self {
        Self {
            code: error.code,
            safe_message: error.safe_message,
        }
    }
}

#[derive(Debug, Clone, Copy)]
struct AppearanceSession {
    state: AppearanceState,
    shown: bool,
    foreground_pending: bool,
    bootstrap_generation: u64,
    completion_armed: bool,
    prompt_in_flight: bool,
    quitting: bool,
}

enum AppearanceMessage {
    GetState {
        arm_bootstrap: bool,
        reply: mpsc::SyncSender<AppearanceState>,
    },
    SetMode {
        logical_mode: LogicalMode,
        reply: mpsc::SyncSender<Result<AppearanceUpdate, AppearanceContextError>>,
    },
    SystemThemeChanged {
        system_mode: ResolvedMode,
        reply: Option<mpsc::SyncSender<Result<Option<AppearanceChanged>, AppearanceContextError>>>,
    },
    ForegroundRequested,
    CompleteBootstrap {
        revision: u64,
        resolved_mode: ResolvedMode,
        reply: mpsc::SyncSender<Result<BootstrapCompletion, AppearanceContextError>>,
    },
    GetWatchdogGeneration {
        reply: mpsc::SyncSender<u64>,
    },
    BootstrapTimedOut {
        generation: u64,
        reply: mpsc::SyncSender<Result<Option<BootstrapRecovery>, AppearanceContextError>>,
    },
}

pub struct AppearanceContext {
    sender: mpsc::Sender<AppearanceMessage>,
}

impl AppearanceContext {
    pub fn new(
        logical_mode: LogicalMode,
        system_mode: ResolvedMode,
        store: Arc<dyn AppearancePreferencePort>,
        window: Arc<dyn AppearanceWindowPort>,
        events: Arc<dyn AppearanceEventPort>,
    ) -> Self {
        let (sender, receiver) = mpsc::channel();
        let session = AppearanceSession {
            state: AppearanceState {
                logical_mode,
                resolved_mode: resolve_mode(logical_mode, system_mode),
                revision: 0,
                persisted: true,
            },
            shown: false,
            foreground_pending: false,
            bootstrap_generation: 1,
            completion_armed: true,
            prompt_in_flight: false,
            quitting: false,
        };
        std::thread::Builder::new()
            .name("appearance-context".to_owned())
            .spawn(move || run_actor(receiver, session, store, window, events))
            .expect("appearance actor thread must start");
        Self { sender }
    }

    pub fn state(&self) -> AppearanceState {
        self.request_state(false)
    }

    pub(crate) fn bootstrap_state(&self) -> AppearanceState {
        self.request_state(true)
    }

    pub fn set_mode(
        &self,
        logical_mode: LogicalMode,
    ) -> Result<AppearanceUpdate, AppearanceContextError> {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.sender
            .send(AppearanceMessage::SetMode {
                logical_mode,
                reply,
            })
            .map_err(|_| actor_unavailable())?;
        receive_reply(receiver)?
    }

    pub fn system_theme_changed(
        &self,
        system_mode: ResolvedMode,
    ) -> Result<Option<AppearanceChanged>, AppearanceContextError> {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.sender
            .send(AppearanceMessage::SystemThemeChanged {
                system_mode,
                reply: Some(reply),
            })
            .map_err(|_| actor_unavailable())?;
        receive_reply(receiver)?
    }

    pub fn enqueue_system_theme_changed(
        &self,
        system_mode: ResolvedMode,
    ) -> Result<(), AppearanceContextError> {
        self.sender
            .send(AppearanceMessage::SystemThemeChanged {
                system_mode,
                reply: None,
            })
            .map_err(|_| actor_unavailable())
    }

    pub fn enqueue_main_window_reactivation(&self) -> Result<(), AppearanceContextError> {
        self.sender
            .send(AppearanceMessage::ForegroundRequested)
            .map_err(|_| actor_unavailable())
    }

    pub fn complete_bootstrap(
        &self,
        revision: u64,
        resolved_mode: ResolvedMode,
    ) -> Result<BootstrapCompletion, AppearanceContextError> {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.sender
            .send(AppearanceMessage::CompleteBootstrap {
                revision,
                resolved_mode,
                reply,
            })
            .map_err(|_| actor_unavailable())?;
        receive_reply(receiver)?
    }

    pub fn bootstrap_watchdog_generation(&self) -> u64 {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.sender
            .send(AppearanceMessage::GetWatchdogGeneration { reply })
            .expect("appearance actor must remain available");
        receiver
            .recv()
            .expect("appearance actor must answer generation requests")
    }

    pub fn handle_bootstrap_timeout(
        &self,
        generation: u64,
    ) -> Result<Option<BootstrapRecovery>, AppearanceContextError> {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.sender
            .send(AppearanceMessage::BootstrapTimedOut { generation, reply })
            .map_err(|_| actor_unavailable())?;
        receive_reply(receiver)?
    }

    fn request_state(&self, arm_bootstrap: bool) -> AppearanceState {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.sender
            .send(AppearanceMessage::GetState {
                arm_bootstrap,
                reply,
            })
            .expect("appearance actor must remain available");
        receiver
            .recv()
            .expect("appearance actor must answer state requests")
    }
}

fn run_actor(
    receiver: mpsc::Receiver<AppearanceMessage>,
    mut session: AppearanceSession,
    store: Arc<dyn AppearancePreferencePort>,
    window: Arc<dyn AppearanceWindowPort>,
    events: Arc<dyn AppearanceEventPort>,
) {
    while let Ok(message) = receiver.recv() {
        match message {
            AppearanceMessage::GetState {
                arm_bootstrap,
                reply,
            } => {
                if arm_bootstrap && !session.shown && !session.quitting {
                    session.completion_armed = true;
                }
                let _ = reply.send(session.state);
            }
            AppearanceMessage::SetMode {
                logical_mode,
                reply,
            } => {
                let _ = reply.send(set_mode(
                    &mut session,
                    store.as_ref(),
                    window.as_ref(),
                    events.as_ref(),
                    logical_mode,
                ));
            }
            AppearanceMessage::SystemThemeChanged { system_mode, reply } => {
                let result = system_theme_changed(
                    &mut session,
                    window.as_ref(),
                    events.as_ref(),
                    system_mode,
                );
                if let Some(reply) = reply {
                    let _ = reply.send(result);
                }
            }
            AppearanceMessage::ForegroundRequested => {
                if session.quitting {
                    continue;
                }
                if session.shown {
                    if let Err(error) = window.restore_and_focus() {
                        log::error!("main window reactivation failed: {}", error.code);
                    }
                } else {
                    session.foreground_pending = true;
                }
            }
            AppearanceMessage::CompleteBootstrap {
                revision,
                resolved_mode,
                reply,
            } => {
                let _ = reply.send(complete_bootstrap(
                    &mut session,
                    window.as_ref(),
                    revision,
                    resolved_mode,
                ));
            }
            AppearanceMessage::GetWatchdogGeneration { reply } => {
                let _ = reply.send(session.bootstrap_generation);
            }
            AppearanceMessage::BootstrapTimedOut { generation, reply } => {
                let _ = reply.send(handle_bootstrap_timeout(
                    &mut session,
                    window.as_ref(),
                    generation,
                ));
            }
        }
    }
}

fn set_mode(
    session: &mut AppearanceSession,
    store: &dyn AppearancePreferencePort,
    window: &dyn AppearanceWindowPort,
    events: &dyn AppearanceEventPort,
    logical_mode: LogicalMode,
) -> Result<AppearanceUpdate, AppearanceContextError> {
    let previous = session.state;
    let revision = next_revision(previous.revision)?;
    let resolved_mode = match logical_mode {
        LogicalMode::System => window.current_system_mode()?,
        LogicalMode::Light => ResolvedMode::Light,
        LogicalMode::Dark => ResolvedMode::Dark,
    };
    session.state = AppearanceState {
        logical_mode,
        resolved_mode,
        revision,
        persisted: true,
    };

    if let Err(error) = window.apply_theme(theme_override(logical_mode), resolved_mode) {
        session.state = AppearanceState {
            revision,
            ..previous
        };
        return Err(error.into());
    }

    let mut diagnostic = store.save(logical_mode).err().map(|error| {
        session.state.persisted = false;
        AppearanceDiagnostic {
            code: error.code,
            safe_message: error.safe_message,
        }
    });
    let event = AppearanceChanged {
        state: session.state,
        source: AppearanceChangeSource::User,
    };
    if let Err(error) = events.emit_changed(&event) {
        diagnostic.get_or_insert(AppearanceDiagnostic {
            code: error.code,
            safe_message: "외관 변경 알림을 전달하지 못했습니다.",
        });
    }

    Ok(AppearanceUpdate {
        state: session.state,
        diagnostic,
    })
}

fn system_theme_changed(
    session: &mut AppearanceSession,
    window: &dyn AppearanceWindowPort,
    events: &dyn AppearanceEventPort,
    system_mode: ResolvedMode,
) -> Result<Option<AppearanceChanged>, AppearanceContextError> {
    if session.state.logical_mode != LogicalMode::System
        || session.state.resolved_mode == system_mode
    {
        return Ok(None);
    }

    let previous = session.state;
    let revision = next_revision(previous.revision)?;
    session.state = AppearanceState {
        resolved_mode: system_mode,
        revision,
        ..previous
    };
    if let Err(error) = window.apply_theme(WindowThemeOverride::FollowSystem, system_mode) {
        session.state = AppearanceState {
            revision,
            ..previous
        };
        return Err(error.into());
    }

    let event = AppearanceChanged {
        state: session.state,
        source: AppearanceChangeSource::System,
    };
    if events.emit_changed(&event).is_err() {
        let _ = events.emit_changed(&event);
    }
    Ok(Some(event))
}

fn complete_bootstrap(
    session: &mut AppearanceSession,
    window: &dyn AppearanceWindowPort,
    revision: u64,
    resolved_mode: ResolvedMode,
) -> Result<BootstrapCompletion, AppearanceContextError> {
    if session.shown
        && revision == session.state.revision
        && resolved_mode == session.state.resolved_mode
        && !session.quitting
    {
        return Ok(BootstrapCompletion {
            instruction: BootstrapInstruction::Show,
            state: session.state,
        });
    }
    if revision != session.state.revision
        || resolved_mode != session.state.resolved_mode
        || !session.completion_armed
        || session.prompt_in_flight
        || session.quitting
    {
        return Ok(BootstrapCompletion {
            instruction: BootstrapInstruction::Refresh,
            state: session.state,
        });
    }

    if !session.shown {
        window.show()?;
        session.shown = true;
        session.completion_armed = false;
        session.bootstrap_generation = next_generation(session.bootstrap_generation)?;
        session.prompt_in_flight = false;
        if session.foreground_pending {
            session.foreground_pending = false;
            if let Err(error) = window.restore_and_focus() {
                log::error!("pending main window reactivation failed: {}", error.code);
            }
        }
    }
    Ok(BootstrapCompletion {
        instruction: BootstrapInstruction::Show,
        state: session.state,
    })
}

fn handle_bootstrap_timeout(
    session: &mut AppearanceSession,
    window: &dyn AppearanceWindowPort,
    generation: u64,
) -> Result<Option<BootstrapRecovery>, AppearanceContextError> {
    if session.shown
        || session.quitting
        || session.prompt_in_flight
        || session.bootstrap_generation != generation
    {
        return Ok(None);
    }
    session.prompt_in_flight = true;

    let action = window
        .prompt_bootstrap_recovery()
        .unwrap_or(BootstrapRecoveryAction::Quit);
    match action {
        BootstrapRecoveryAction::Retry => {
            let next_generation = next_generation(session.bootstrap_generation)?;
            session.state.revision = next_revision(session.state.revision)?;
            session.bootstrap_generation = next_generation;
            session.completion_armed = false;
            session.prompt_in_flight = false;
            if window.reload().is_err() {
                session.quitting = true;
                window.quit();
                return Ok(Some(BootstrapRecovery {
                    action: BootstrapRecoveryAction::Quit,
                    generation: next_generation,
                }));
            }
            Ok(Some(BootstrapRecovery {
                action,
                generation: next_generation,
            }))
        }
        BootstrapRecoveryAction::Quit => {
            session.prompt_in_flight = false;
            session.quitting = true;
            window.quit();
            Ok(Some(BootstrapRecovery { action, generation }))
        }
    }
}

fn receive_reply<T>(receiver: mpsc::Receiver<T>) -> Result<T, AppearanceContextError> {
    receiver.recv().map_err(|_| actor_unavailable())
}

fn actor_unavailable() -> AppearanceContextError {
    AppearanceContextError {
        code: "appearance_context_unavailable",
        safe_message: "외관 상태를 처리할 수 없습니다.",
    }
}

fn theme_override(logical_mode: LogicalMode) -> WindowThemeOverride {
    match logical_mode {
        LogicalMode::System => WindowThemeOverride::FollowSystem,
        LogicalMode::Light => WindowThemeOverride::Light,
        LogicalMode::Dark => WindowThemeOverride::Dark,
    }
}

fn next_revision(revision: u64) -> Result<u64, AppearanceContextError> {
    revision.checked_add(1).ok_or(AppearanceContextError {
        code: "appearance_revision_exhausted",
        safe_message: "외관 상태를 갱신할 수 없습니다.",
    })
}

fn next_generation(generation: u64) -> Result<u64, AppearanceContextError> {
    generation.checked_add(1).ok_or(AppearanceContextError {
        code: "appearance_bootstrap_generation_exhausted",
        safe_message: "앱 표시를 다시 시도할 수 없습니다.",
    })
}
