use std::path::PathBuf;
#[cfg(target_os = "macos")]
use std::sync::atomic::AtomicU64;
use std::sync::atomic::{AtomicBool, Ordering};
#[cfg(target_os = "macos")]
use std::sync::mpsc;
use std::sync::Arc;
#[cfg(target_os = "macos")]
use std::sync::Mutex;

use tauri::AppHandle;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PickerSafeReason {
    PanelUnavailable,
    SelectionUnavailable,
    AlreadyActive,
}

impl PickerSafeReason {
    pub fn safe_message(self) -> &'static str {
        match self {
            Self::PanelUnavailable => "폴더 선택기를 열지 못했습니다.",
            Self::SelectionUnavailable => "선택한 폴더를 읽지 못했습니다.",
            Self::AlreadyActive => "폴더 선택기가 이미 열려 있습니다.",
        }
    }
}

#[derive(Clone, PartialEq, Eq)]
pub enum PickerOutcome {
    Selected(PathBuf),
    Cancelled,
    Failed(PickerSafeReason),
}

impl std::fmt::Debug for PickerOutcome {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Selected(_) => formatter.write_str("Selected(<redacted-path>)"),
            Self::Cancelled => formatter.write_str("Cancelled"),
            Self::Failed(reason) => formatter.debug_tuple("Failed").field(reason).finish(),
        }
    }
}

pub trait CheckoutDirectoryPickerPort: Send + Sync {
    fn pick_directory(&self) -> PickerOutcome;
}

pub struct SingleFlightCheckoutDirectoryPickerPort {
    inner: Arc<dyn CheckoutDirectoryPickerPort>,
    active: AtomicBool,
}

impl SingleFlightCheckoutDirectoryPickerPort {
    pub fn new(inner: Arc<dyn CheckoutDirectoryPickerPort>) -> Self {
        Self {
            inner,
            active: AtomicBool::new(false),
        }
    }
}

struct PickerActivationGuard<'a>(&'a AtomicBool);

impl Drop for PickerActivationGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

impl CheckoutDirectoryPickerPort for SingleFlightCheckoutDirectoryPickerPort {
    fn pick_directory(&self) -> PickerOutcome {
        if self
            .active
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return PickerOutcome::Failed(PickerSafeReason::AlreadyActive);
        }
        let _activation = PickerActivationGuard(&self.active);
        self.inner.pick_directory()
    }
}

pub struct TauriCheckoutDirectoryPickerPort {
    app: AppHandle,
}

#[cfg(target_os = "macos")]
struct PickerCompletion {
    sender: Mutex<Option<mpsc::SyncSender<PickerOutcome>>>,
}

#[cfg(target_os = "macos")]
impl PickerCompletion {
    fn new(sender: mpsc::SyncSender<PickerOutcome>) -> Self {
        Self {
            sender: Mutex::new(Some(sender)),
        }
    }

    fn resolve(&self, outcome: PickerOutcome) -> bool {
        let Some(sender) = self
            .sender
            .lock()
            .expect("picker completion poisoned")
            .take()
        else {
            return false;
        };
        sender.try_send(outcome).is_ok()
    }
}

#[cfg(target_os = "macos")]
struct PendingPickerSession {
    id: u64,
    completion: Arc<PickerCompletion>,
}

#[cfg(target_os = "macos")]
struct PendingPickerCompletionSlot {
    session: Mutex<Option<PendingPickerSession>>,
}

#[cfg(target_os = "macos")]
impl PendingPickerCompletionSlot {
    const fn new() -> Self {
        Self {
            session: Mutex::new(None),
        }
    }

    fn register(
        &self,
        id: u64,
        completion: Arc<PickerCompletion>,
        shutting_down: &AtomicBool,
    ) -> bool {
        let mut session = self.session.lock().expect("pending picker slot poisoned");
        if shutting_down.load(Ordering::Acquire) || session.is_some() {
            return false;
        }
        *session = Some(PendingPickerSession { id, completion });
        true
    }

    fn contains(&self, id: u64, completion: &Arc<PickerCompletion>) -> bool {
        self.session
            .lock()
            .expect("pending picker slot poisoned")
            .as_ref()
            .is_some_and(|session| session.id == id && Arc::ptr_eq(&session.completion, completion))
    }

    fn clear(&self, id: u64) {
        let mut session = self.session.lock().expect("pending picker slot poisoned");
        if session.as_ref().is_some_and(|session| session.id == id) {
            session.take();
        }
    }

    fn resolve(&self, outcome: PickerOutcome) -> bool {
        let completion = self
            .session
            .lock()
            .expect("pending picker slot poisoned")
            .take()
            .map(|session| session.completion);
        completion.is_some_and(|completion| completion.resolve(outcome))
    }
}

#[cfg(target_os = "macos")]
struct ActivePickerSession {
    id: u64,
    panel: objc2::rc::Retained<objc2_app_kit::NSOpenPanel>,
    completion: Arc<PickerCompletion>,
}

#[cfg(target_os = "macos")]
thread_local! {
    static ACTIVE_PICKER_SESSION: std::cell::RefCell<Option<ActivePickerSession>> = const {
        std::cell::RefCell::new(None)
    };
}

#[cfg(target_os = "macos")]
static NEXT_PICKER_SESSION_ID: AtomicU64 = AtomicU64::new(1);
#[cfg(target_os = "macos")]
static PICKER_SHUTTING_DOWN: AtomicBool = AtomicBool::new(false);
#[cfg(target_os = "macos")]
const HARNESS_CHECKOUT_DIRECTORY_PICKER_V1: &str = "harness-checkout-directory-picker-v1";
#[cfg(target_os = "macos")]
static PENDING_PICKER_COMPLETION: PendingPickerCompletionSlot = PendingPickerCompletionSlot::new();

#[cfg(target_os = "macos")]
fn register_pending_picker_completion(id: u64, completion: Arc<PickerCompletion>) -> bool {
    PENDING_PICKER_COMPLETION.register(id, completion, &PICKER_SHUTTING_DOWN)
}

#[cfg(target_os = "macos")]
fn clear_pending_picker_completion(id: u64) {
    PENDING_PICKER_COMPLETION.clear(id);
}

#[cfg(target_os = "macos")]
fn resolve_pending_picker_completion(outcome: PickerOutcome) -> bool {
    PENDING_PICKER_COMPLETION.resolve(outcome)
}

#[cfg(target_os = "macos")]
fn clear_active_picker_session(id: u64) {
    ACTIVE_PICKER_SESSION.with(|active| {
        let matches = active
            .borrow()
            .as_ref()
            .is_some_and(|session| session.id == id);
        if matches {
            active.borrow_mut().take();
        }
    });
}

#[cfg(target_os = "macos")]
pub fn cancel_active_checkout_picker() {
    PICKER_SHUTTING_DOWN.store(true, Ordering::Release);
    resolve_pending_picker_completion(PickerOutcome::Cancelled);
    if objc2::MainThreadMarker::new().is_none() {
        return;
    }
    let active = ACTIVE_PICKER_SESSION.with(|session| session.borrow_mut().take());
    if let Some(session) = active {
        unsafe { session.panel.cancel(None) };
        session.completion.resolve(PickerOutcome::Cancelled);
    }
}

#[cfg(not(target_os = "macos"))]
pub fn cancel_active_checkout_picker() {}

impl TauriCheckoutDirectoryPickerPort {
    pub fn new(app: AppHandle) -> Self {
        Self { app }
    }

    #[cfg(target_os = "macos")]
    fn begin_on_main_thread(
        marker: objc2::MainThreadMarker,
        session_id: u64,
        completion: Arc<PickerCompletion>,
    ) {
        use block2::RcBlock;
        use objc2_app_kit::{
            NSAccessibility, NSModalResponseCancel, NSModalResponseOK, NSOpenPanel,
        };
        use objc2_foundation::NSString;

        if PICKER_SHUTTING_DOWN.load(Ordering::Acquire)
            || !PENDING_PICKER_COMPLETION.contains(session_id, &completion)
        {
            clear_pending_picker_completion(session_id);
            completion.resolve(PickerOutcome::Cancelled);
            return;
        }

        let panel = NSOpenPanel::openPanel(marker);
        let accessibility_identifier = NSString::from_str(HARNESS_CHECKOUT_DIRECTORY_PICKER_V1);
        panel.setAccessibilityIdentifier(Some(&accessibility_identifier));
        panel.setCanChooseDirectories(true);
        panel.setCanChooseFiles(false);
        panel.setAllowsMultipleSelection(false);
        panel.setResolvesAliases(false);

        let completion_panel = panel.clone();
        let completion_resolver = Arc::clone(&completion);
        let handler = RcBlock::new(move |response| {
            let outcome = if response == NSModalResponseCancel {
                PickerOutcome::Cancelled
            } else if response != NSModalResponseOK {
                PickerOutcome::Failed(PickerSafeReason::PanelUnavailable)
            } else {
                completion_panel
                    .URL()
                    .and_then(|url| url.to_file_path())
                    .map(PickerOutcome::Selected)
                    .unwrap_or(PickerOutcome::Failed(
                        PickerSafeReason::SelectionUnavailable,
                    ))
            };
            clear_active_picker_session(session_id);
            completion_resolver.resolve(outcome);
        });

        let registered = ACTIVE_PICKER_SESSION.with(|active| {
            let mut active = active.borrow_mut();
            if active.is_some() {
                return false;
            }
            *active = Some(ActivePickerSession {
                id: session_id,
                panel: panel.clone(),
                completion: Arc::clone(&completion),
            });
            true
        });
        if !registered {
            clear_pending_picker_completion(session_id);
            completion.resolve(PickerOutcome::Failed(PickerSafeReason::AlreadyActive));
            return;
        }

        clear_pending_picker_completion(session_id);
        panel.beginWithCompletionHandler(&handler);
    }
}

impl CheckoutDirectoryPickerPort for TauriCheckoutDirectoryPickerPort {
    fn pick_directory(&self) -> PickerOutcome {
        #[cfg(target_os = "macos")]
        {
            if objc2::MainThreadMarker::new().is_some() {
                return PickerOutcome::Failed(PickerSafeReason::PanelUnavailable);
            }

            let (sender, receiver) = mpsc::sync_channel(1);
            let completion = Arc::new(PickerCompletion::new(sender));
            let session_id = NEXT_PICKER_SESSION_ID.fetch_add(1, Ordering::Relaxed);
            if !register_pending_picker_completion(session_id, Arc::clone(&completion)) {
                let outcome = if PICKER_SHUTTING_DOWN.load(Ordering::Acquire) {
                    PickerOutcome::Cancelled
                } else {
                    PickerOutcome::Failed(PickerSafeReason::AlreadyActive)
                };
                completion.resolve(outcome);
                return receiver
                    .recv()
                    .unwrap_or(PickerOutcome::Failed(PickerSafeReason::PanelUnavailable));
            }
            let main_thread_completion = Arc::clone(&completion);
            if self
                .app
                .run_on_main_thread(move || {
                    if let Some(marker) = objc2::MainThreadMarker::new() {
                        Self::begin_on_main_thread(marker, session_id, main_thread_completion);
                    } else {
                        clear_pending_picker_completion(session_id);
                        main_thread_completion
                            .resolve(PickerOutcome::Failed(PickerSafeReason::PanelUnavailable));
                    }
                })
                .is_err()
            {
                clear_pending_picker_completion(session_id);
                completion.resolve(PickerOutcome::Failed(PickerSafeReason::PanelUnavailable));
            }
            receiver
                .recv()
                .unwrap_or(PickerOutcome::Failed(PickerSafeReason::PanelUnavailable))
        }

        #[cfg(not(target_os = "macos"))]
        {
            let _ = &self.app;
            PickerOutcome::Failed(PickerSafeReason::PanelUnavailable)
        }
    }
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::*;

    #[test]
    fn picker_completion_delivers_only_the_first_outcome() {
        let (sender, receiver) = mpsc::sync_channel(1);
        let completion = PickerCompletion::new(sender);

        assert!(completion.resolve(PickerOutcome::Cancelled));
        assert!(!completion.resolve(PickerOutcome::Failed(PickerSafeReason::PanelUnavailable)));
        assert_eq!(receiver.recv().unwrap(), PickerOutcome::Cancelled);
        assert!(receiver.try_recv().is_err());
    }

    #[test]
    fn pending_picker_completion_resolves_on_shutdown_and_rejects_late_registration() {
        let slot = PendingPickerCompletionSlot::new();
        let shutting_down = AtomicBool::new(false);
        let (sender, receiver) = mpsc::sync_channel(1);
        let completion = Arc::new(PickerCompletion::new(sender));

        assert!(slot.register(41, Arc::clone(&completion), &shutting_down));
        assert!(slot.contains(41, &completion));
        shutting_down.store(true, Ordering::Release);
        assert!(slot.resolve(PickerOutcome::Cancelled));
        assert!(!slot.resolve(PickerOutcome::Cancelled));
        assert_eq!(receiver.recv().unwrap(), PickerOutcome::Cancelled);

        let (late_sender, _late_receiver) = mpsc::sync_channel(1);
        assert!(!slot.register(
            42,
            Arc::new(PickerCompletion::new(late_sender)),
            &shutting_down,
        ));
    }

    #[test]
    fn clearing_a_failed_enqueue_releases_the_pending_slot_for_the_next_picker() {
        let slot = PendingPickerCompletionSlot::new();
        let shutting_down = AtomicBool::new(false);
        let (first_sender, _first_receiver) = mpsc::sync_channel(1);
        let first = Arc::new(PickerCompletion::new(first_sender));

        assert!(slot.register(51, first, &shutting_down));
        slot.clear(51);

        let (next_sender, _next_receiver) = mpsc::sync_channel(1);
        let next = Arc::new(PickerCompletion::new(next_sender));
        assert!(slot.register(52, Arc::clone(&next), &shutting_down));
        assert!(slot.contains(52, &next));
    }
}
