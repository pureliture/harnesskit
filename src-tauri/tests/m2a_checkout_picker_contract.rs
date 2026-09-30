use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Barrier, Condvar, Mutex};

use harness_desktop_lib::api::dto::sot::CheckoutDirectoryPickerOutcomeDto;
use harness_desktop_lib::contexts::sot::checkout_picker::{
    CheckoutDirectoryPickerPort, PickerOutcome, PickerSafeReason,
    SingleFlightCheckoutDirectoryPickerPort,
};

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

#[test]
fn single_flight_rejects_a_duplicate_without_queueing_and_releases_after_cancel() {
    struct BlockingPicker {
        calls: AtomicUsize,
        entered: Barrier,
        released: (Mutex<bool>, Condvar),
    }

    impl CheckoutDirectoryPickerPort for BlockingPicker {
        fn pick_directory(&self) -> PickerOutcome {
            let call = self.calls.fetch_add(1, Ordering::SeqCst);
            if call == 0 {
                self.entered.wait();
                let (lock, wake) = &self.released;
                let released = lock.lock().unwrap();
                drop(wake.wait_while(released, |released| !*released).unwrap());
            }
            PickerOutcome::Cancelled
        }
    }

    let inner = Arc::new(BlockingPicker {
        calls: AtomicUsize::new(0),
        entered: Barrier::new(2),
        released: (Mutex::new(false), Condvar::new()),
    });
    let picker = Arc::new(SingleFlightCheckoutDirectoryPickerPort::new(inner.clone()));
    let worker = {
        let picker = picker.clone();
        std::thread::spawn(move || picker.pick_directory())
    };
    inner.entered.wait();

    assert_eq!(
        picker.pick_directory(),
        PickerOutcome::Failed(PickerSafeReason::AlreadyActive)
    );
    assert_eq!(inner.calls.load(Ordering::SeqCst), 1);

    *inner.released.0.lock().unwrap() = true;
    inner.released.1.notify_one();
    assert_eq!(worker.join().unwrap(), PickerOutcome::Cancelled);
    assert_eq!(picker.pick_directory(), PickerOutcome::Cancelled);
    assert_eq!(inner.calls.load(Ordering::SeqCst), 2);
}

#[test]
fn single_flight_gate_releases_when_the_platform_picker_panics() {
    struct PanicOncePicker(AtomicUsize);

    impl CheckoutDirectoryPickerPort for PanicOncePicker {
        fn pick_directory(&self) -> PickerOutcome {
            if self.0.fetch_add(1, Ordering::SeqCst) == 0 {
                panic!("platform picker panic fixture");
            }
            PickerOutcome::Cancelled
        }
    }

    let picker = SingleFlightCheckoutDirectoryPickerPort::new(Arc::new(PanicOncePicker(
        AtomicUsize::new(0),
    )));
    let panic = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| picker.pick_directory()));

    assert!(panic.is_err());
    assert_eq!(picker.pick_directory(), PickerOutcome::Cancelled);
}

#[test]
fn picker_outcomes_map_to_safe_transport_without_raw_debug_paths() {
    let selected = CheckoutDirectoryPickerOutcomeDto::from(PickerOutcome::Selected(PathBuf::from(
        "/private/tmp/private-harnesskit",
    )));
    assert_eq!(
        serde_json::to_value(&selected).unwrap(),
        serde_json::json!({
            "outcome": "selected",
            "path": "/private/tmp/private-harnesskit"
        })
    );
    assert!(!format!("{selected:?}").contains("private-harnesskit"));

    assert_eq!(
        serde_json::to_value(CheckoutDirectoryPickerOutcomeDto::from(
            PickerOutcome::Cancelled
        ))
        .unwrap(),
        serde_json::json!({ "outcome": "cancelled" })
    );
    assert_eq!(
        serde_json::to_value(CheckoutDirectoryPickerOutcomeDto::from(
            PickerOutcome::Failed(PickerSafeReason::PanelUnavailable)
        ))
        .unwrap(),
        serde_json::json!({
            "outcome": "failed",
            "reason": "폴더 선택기를 열지 못했습니다."
        })
    );
    assert_eq!(
        serde_json::to_value(CheckoutDirectoryPickerOutcomeDto::from(
            PickerOutcome::Failed(PickerSafeReason::AlreadyActive)
        ))
        .unwrap(),
        serde_json::json!({
            "outcome": "failed",
            "reason": "폴더 선택기가 이미 열려 있습니다."
        })
    );

    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStringExt;

        let invalid_utf8 = PathBuf::from(std::ffi::OsString::from_vec(vec![b'/', 0xff]));
        assert_eq!(
            serde_json::to_value(CheckoutDirectoryPickerOutcomeDto::from(
                PickerOutcome::Selected(invalid_utf8)
            ))
            .unwrap(),
            serde_json::json!({
                "outcome": "failed",
                "reason": "선택한 폴더를 읽지 못했습니다."
            })
        );
    }

    assert_eq!(
        serde_json::to_value(CheckoutDirectoryPickerOutcomeDto::from(
            PickerOutcome::Selected(PathBuf::from("/private/tmp/line\nbreak"))
        ))
        .unwrap(),
        serde_json::json!({
            "outcome": "failed",
            "reason": "선택한 폴더를 읽지 못했습니다."
        })
    );
    assert_eq!(
        serde_json::to_value(CheckoutDirectoryPickerOutcomeDto::from(
            PickerOutcome::Selected(PathBuf::from("/private/tmp/carriage\rreturn"))
        ))
        .unwrap(),
        serde_json::json!({
            "outcome": "failed",
            "reason": "선택한 폴더를 읽지 못했습니다."
        })
    );
}

#[test]
fn app_controller_owns_the_injected_picker_and_command_only_delegates_selection() {
    let root = manifest_dir();
    let controller = std::fs::read_to_string(root.join("src/app_controller.rs")).unwrap();
    let commands = std::fs::read_to_string(root.join("src/api/commands.rs")).unwrap();
    let runtime = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();

    assert!(controller.contains("checkout_picker: Arc<dyn CheckoutDirectoryPickerPort>"));
    assert!(controller.contains("fn pick_checkout_directory(&self) -> PickerOutcome"));

    let command = commands
        .split_once("fn pick_checkout_directory")
        .and_then(|(_, tail)| tail.split_once("#[tauri::command]"))
        .map(|(body, _)| body)
        .unwrap();
    assert!(command.contains("controller.pick_checkout_directory()"));
    assert!(command.contains("spawn_blocking"));
    for forbidden in ["canonicalize", ".git", "register_checkout", "RepoStatus"] {
        assert!(!command.contains(forbidden));
    }

    assert!(runtime.contains("TauriCheckoutDirectoryPickerPort::new(app.handle().clone())"));
    assert!(runtime.contains("SingleFlightCheckoutDirectoryPickerPort::new("));
    assert!(runtime.contains("checkout_picker,"));
}

#[test]
fn macos_adapter_is_nonblocking_directory_only_single_selection_without_dialog_plugin() {
    let root = manifest_dir();
    let picker = std::fs::read_to_string(root.join("src/contexts/sot/checkout_picker.rs")).unwrap();
    let cargo = std::fs::read_to_string(root.join("Cargo.toml")).unwrap();

    for required in [
        "TauriCheckoutDirectoryPickerPort",
        "NSOpenPanel::openPanel",
        "setCanChooseDirectories(true)",
        "setCanChooseFiles(false)",
        "setAllowsMultipleSelection(false)",
        "NSModalResponseOK",
        "NSModalResponseCancel",
        "to_file_path()",
        "run_on_main_thread",
        "beginWithCompletionHandler",
        "HARNESS_CHECKOUT_DIRECTORY_PICKER_V1",
        "setAccessibilityIdentifier",
        "RcBlock::new",
        ".recv()",
    ] {
        assert!(
            picker.contains(required),
            "missing native picker contract: {required}"
        );
    }
    for forbidden in [".runModal()", "runModalForWindow"] {
        assert!(
            !picker.contains(forbidden),
            "blocking native picker API can deadlock the Tauri/Tao main event loop: {forbidden}"
        );
    }
    assert!(!picker.contains("recv_timeout"));
    for feature in [
        "\"NSOpenPanel\"",
        "\"NSAccessibilityProtocols\"",
        "\"NSPanel\"",
        "\"NSSavePanel\"",
        "\"NSURL\"",
        "\"block2\"",
    ] {
        assert!(
            cargo.contains(feature),
            "missing native picker feature {feature}"
        );
    }
    assert!(cargo.contains("block2 ="));
    assert!(!cargo.contains("tauri-plugin-dialog"));
}

#[test]
fn app_exit_cancels_the_active_picker_and_resolves_the_waiter_exactly_once() {
    let root = manifest_dir();
    let picker = std::fs::read_to_string(root.join("src/contexts/sot/checkout_picker.rs")).unwrap();
    let runtime = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();

    for required in [
        "PickerCompletion",
        "try_send",
        "ACTIVE_PICKER_SESSION",
        "PENDING_PICKER_COMPLETION",
        "PICKER_SHUTTING_DOWN",
        "cancel_active_checkout_picker",
        "resolve_pending_picker_completion(PickerOutcome::Cancelled)",
        ".cancel(None)",
        "PickerOutcome::Cancelled",
    ] {
        assert!(
            picker.contains(required),
            "missing active picker lifecycle contract: {required}"
        );
    }

    assert!(runtime.contains(".build(tauri::generate_context!())"));
    assert!(runtime.contains("app.run(|_app_handle, event|"));
    assert!(runtime.contains("tauri::RunEvent::ExitRequested"));
    assert!(runtime.contains("tauri::RunEvent::Exit"));
    assert!(runtime.contains("cancel_active_checkout_picker()"));

    let platform_pick = picker
        .rfind("fn pick_directory(&self) -> PickerOutcome")
        .map(|index| &picker[index..])
        .expect("missing platform picker implementation");
    let pending_registration = platform_pick
        .find("register_pending_picker_completion")
        .expect("waiter completion must be registered before main-thread enqueue");
    let main_thread_enqueue = platform_pick
        .find(".run_on_main_thread")
        .expect("missing main-thread picker enqueue");
    assert!(pending_registration < main_thread_enqueue);
}
