//! Verified macOS native actions for Local instance handles.

use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::time::Duration;

use tauri::AppHandle;

use super::path_action::{NativePathActionError, NativePathActionPort, VerifiedNativePath};

const MAIN_THREAD_ACTION_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone)]
struct PendingMainThreadAction {
    pending: Arc<AtomicBool>,
}

impl PendingMainThreadAction {
    fn new() -> Self {
        Self {
            pending: Arc::new(AtomicBool::new(true)),
        }
    }

    fn try_start(&self) -> bool {
        self.pending.swap(false, Ordering::AcqRel)
    }

    fn cancel(&self) -> bool {
        self.pending.swap(false, Ordering::AcqRel)
    }
}

pub struct TauriNativePathActionPort {
    app: AppHandle,
}

impl TauriNativePathActionPort {
    pub fn new(app: AppHandle) -> Self {
        Self { app }
    }

    #[cfg(target_os = "macos")]
    fn run_on_main_thread(
        &self,
        action: impl FnOnce() -> bool + Send + 'static,
    ) -> Result<(), NativePathActionError> {
        if objc2::MainThreadMarker::new().is_some() {
            return action().then_some(()).ok_or(NativePathActionError::Failed);
        }

        let (sender, receiver) = mpsc::sync_channel(1);
        let pending = PendingMainThreadAction::new();
        let queued = pending.clone();
        self.app
            .run_on_main_thread(move || {
                if !queued.try_start() {
                    return;
                }
                let _ = sender.send(action());
            })
            .map_err(|_| NativePathActionError::Unavailable)?;
        let succeeded = match receiver.recv_timeout(MAIN_THREAD_ACTION_TIMEOUT) {
            Ok(succeeded) => succeeded,
            Err(mpsc::RecvTimeoutError::Timeout) if pending.cancel() => {
                return Err(NativePathActionError::Failed);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                receiver.recv().map_err(|_| NativePathActionError::Failed)?
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                return Err(NativePathActionError::Failed);
            }
        };
        succeeded.then_some(()).ok_or(NativePathActionError::Failed)
    }

    #[cfg(not(target_os = "macos"))]
    fn run_on_main_thread(
        &self,
        _action: impl FnOnce() -> bool + Send + 'static,
    ) -> Result<(), NativePathActionError> {
        let _ = &self.app;
        Err(NativePathActionError::Unavailable)
    }

    fn verified_utf8_path(
        target: &VerifiedNativePath<'_>,
    ) -> Result<String, NativePathActionError> {
        target
            .canonical_path()
            .to_str()
            .filter(|path| Path::new(path).is_absolute())
            .map(str::to_owned)
            .ok_or(NativePathActionError::Unavailable)
    }
}

impl NativePathActionPort for TauriNativePathActionPort {
    fn reveal(&self, target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError> {
        let path = Self::verified_utf8_path(target)?;
        self.run_on_main_thread(move || {
            #[cfg(target_os = "macos")]
            {
                use objc2_app_kit::NSWorkspace;
                use objc2_foundation::NSString;

                let workspace = NSWorkspace::sharedWorkspace();
                let path = NSString::from_str(&path);
                let root = NSString::from_str("");
                workspace.selectFile_inFileViewerRootedAtPath(Some(&path), &root)
            }
            #[cfg(not(target_os = "macos"))]
            {
                let _ = path;
                false
            }
        })
    }

    fn copy_path(&self, target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError> {
        let path = Self::verified_utf8_path(target)?;
        self.run_on_main_thread(move || {
            #[cfg(target_os = "macos")]
            {
                use objc2_app_kit::{NSPasteboard, NSPasteboardTypeString};
                use objc2_foundation::NSString;

                let pasteboard = NSPasteboard::generalPasteboard();
                let _ = pasteboard.clearContents();
                // SAFETY: AppKit exports this immutable process-wide pasteboard type constant.
                let string_type = unsafe { NSPasteboardTypeString };
                pasteboard.setString_forType(&NSString::from_str(&path), string_type)
            }
            #[cfg(not(target_os = "macos"))]
            {
                let _ = path;
                false
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::PendingMainThreadAction;
    use std::sync::atomic::{AtomicBool, Ordering};

    #[test]
    fn starved_main_thread_action_cancelled_by_timeout_never_executes_late() {
        let pending = PendingMainThreadAction::new();
        let queued = pending.clone();
        let executed = AtomicBool::new(false);

        assert!(pending.cancel());
        if queued.try_start() {
            executed.store(true, Ordering::SeqCst);
        }

        assert!(!executed.load(Ordering::SeqCst));
    }

    #[test]
    fn timeout_cannot_report_failure_after_main_thread_action_started() {
        let pending = PendingMainThreadAction::new();
        let queued = pending.clone();

        assert!(queued.try_start());
        assert!(!pending.cancel());
    }
}
