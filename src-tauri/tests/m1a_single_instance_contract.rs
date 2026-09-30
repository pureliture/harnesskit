use std::path::PathBuf;

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

#[test]
fn official_single_instance_plugin_is_the_first_desktop_plugin() {
    let root = manifest_dir();
    let cargo = std::fs::read_to_string(root.join("Cargo.toml")).unwrap();
    let runtime = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();

    assert!(
        cargo.contains("tauri-plugin-single-instance"),
        "official single-instance dependency is required"
    );
    assert!(
        cargo.contains("cfg(any(target_os = \"macos\", windows, target_os = \"linux\"))"),
        "single-instance dependency must stay on the portable desktop target boundary"
    );

    let single = runtime
        .find("tauri_plugin_single_instance::init")
        .expect("single-instance plugin must be registered");
    let logging = runtime
        .find("tauri_plugin_log::Builder")
        .expect("log plugin must remain registered");
    assert!(single < logging, "single-instance must be the first plugin");
}

#[test]
fn secondary_launch_only_enqueues_existing_main_window_reactivation() {
    let root = manifest_dir();
    let runtime = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();
    let controller = std::fs::read_to_string(root.join("src/app_controller.rs")).unwrap();
    let inbox = std::fs::read_to_string(root.join("src/activation_inbox.rs")).unwrap();

    assert!(runtime.contains("ActivationInbox::default()"));
    assert!(runtime.contains("activation_inbox.request()"));
    assert!(runtime.contains(".bind_controller"));
    assert!(runtime.contains("move |_app, _args, _cwd|"));
    assert!(!runtime.contains("args.len()"));
    assert!(!runtime.contains("cwd_present"));
    assert!(controller.contains("fn enqueue_main_window_reactivation"));
    assert!(inbox.contains("pending"));
    assert!(!runtime.contains("WebviewWindowBuilder"));
    assert!(!runtime.contains("WindowBuilder"));
}

#[test]
fn main_thread_restore_timeout_never_waits_without_a_bound() {
    let source =
        std::fs::read_to_string(manifest_dir().join("src/contexts/appearance/window.rs")).unwrap();
    let timeout_start = source
        .find("Err(mpsc::RecvTimeoutError::Timeout) =>")
        .expect("main-thread timeout branch must exist");
    let timeout_end = source[timeout_start..]
        .find("Err(mpsc::RecvTimeoutError::Disconnected) =>")
        .map(|offset| timeout_start + offset)
        .expect("disconnected branch must follow timeout branch");
    let timeout_branch = &source[timeout_start..timeout_end];

    assert!(timeout_branch.contains("pending.store(false"));
    assert!(!timeout_branch.contains("receiver.recv()"));
}

#[test]
fn macos_restore_uses_version_safe_nsapplication_activation_after_window_focus() {
    let source =
        std::fs::read_to_string(manifest_dir().join("src/contexts/appearance/window.rs")).unwrap();
    let restoration = source
        .split("fn restore_with_steps")
        .nth(1)
        .and_then(|body| body.split("#[derive(Clone)]").next())
        .expect("window restoration sequence must exist");

    let focus = restoration.find("steps.focus()").unwrap();
    let activate = restoration.find("steps.activate()").unwrap();
    assert!(focus < activate, "window must be key before app activation");
    assert!(source.contains("uses_modern_application_activation"));
    assert!(source.contains("application.activate();"));
    assert!(source.contains("application.activateIgnoringOtherApps(true);"));
    assert!(source.contains("NSProcessInfo::processInfo()"));
    assert!(source.matches("with_activation_settle_retry(").count() >= 3);
    assert!(source.contains("Duration::from_millis(350)"));
    assert!(source.contains("appearance first-show activation deferred"));
}

#[test]
fn single_instance_does_not_expand_windows_commands_or_capabilities() {
    let root = manifest_dir();
    let config: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(root.join("tauri.conf.json")).unwrap())
            .unwrap();
    let capability: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(root.join("capabilities/default.json")).unwrap(),
    )
    .unwrap();

    let windows = config["app"]["windows"].as_array().unwrap();
    assert_eq!(windows.len(), 1);
    assert_eq!(windows[0]["label"], "main");
    assert_eq!(
        capability["permissions"].as_array().unwrap(),
        &[serde_json::Value::String("core:default".to_owned())]
    );
}
