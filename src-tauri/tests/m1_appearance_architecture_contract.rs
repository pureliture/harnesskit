use std::path::{Path, PathBuf};

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn assert_file(path: &Path) {
    assert!(
        path.is_file(),
        "missing approved appearance module: {}",
        path.display()
    );
}

#[test]
fn approved_appearance_module_seams_are_present() {
    let root = manifest_dir();

    for relative in [
        "src/contexts/appearance/context.rs",
        "src/contexts/appearance/resolver.rs",
        "src/contexts/appearance/store.rs",
        "src/contexts/appearance/window.rs",
    ] {
        assert_file(&root.join(relative));
    }

    let module = std::fs::read_to_string(root.join("src/contexts/appearance/mod.rs")).unwrap();
    for declaration in ["mod context;", "mod resolver;", "mod store;", "mod window;"] {
        assert!(
            module.contains(declaration),
            "appearance module must declare {declaration}"
        );
    }
}

#[test]
fn main_window_starts_hidden_under_the_exact_csp() {
    let config_path = manifest_dir().join("tauri.conf.json");
    let config: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(config_path).unwrap()).unwrap();
    let window = &config["app"]["windows"][0];

    assert_eq!(config["identifier"], "io.github.pureliture.harnesskit");
    assert_eq!(window["label"], "main");
    assert_eq!(window["visible"], false);
    assert_eq!(window["minWidth"], 720);
    assert_eq!(window["minHeight"], 600);
    assert_eq!(
        config["app"]["security"]["csp"],
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self' ipc: http://ipc.localhost; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; media-src 'none'; worker-src 'none'"
    );
}

#[test]
fn appearance_commands_are_wired_through_the_app_controller() {
    let root = manifest_dir();
    let controller = std::fs::read_to_string(root.join("src/app_controller.rs")).unwrap();
    let commands = std::fs::read_to_string(root.join("src/api/commands.rs")).unwrap();
    let lib = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();

    assert!(controller.contains("appearance: AppearanceContext"));
    assert!(lib.contains("use app_controller::AppController;"));
    assert!(!lib.contains("controller::{\n    AppController"));

    for command in [
        "get_bootstrap_state",
        "set_appearance_mode",
        "complete_bootstrap",
    ] {
        assert!(
            commands.contains(&format!("fn {command}")),
            "missing appearance command {command}"
        );
        assert!(
            lib.contains(&format!("api::commands::{command}")),
            "appearance command {command} is not registered"
        );
    }
    assert!(lib.contains("WindowEvent::ThemeChanged"));
}

#[test]
fn hidden_bootstrap_watchdog_is_scheduled_for_five_seconds() {
    let root = manifest_dir();
    let controller = std::fs::read_to_string(root.join("src/app_controller.rs")).unwrap();
    let lib = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();

    assert!(controller.contains("fn handle_bootstrap_timeout"));
    assert!(controller.contains("fn bootstrap_watchdog_generation"));
    assert!(lib.contains("const BOOTSTRAP_TIMEOUT: Duration = Duration::from_secs(5);"));
    assert!(lib.contains("schedule_bootstrap_watchdog"));
    assert!(lib.contains("BootstrapRecoveryAction::Retry"));
}

#[test]
fn appearance_transitions_use_one_mailbox_and_never_block_the_window_callback() {
    let root = manifest_dir();
    let context = std::fs::read_to_string(root.join("src/contexts/appearance/context.rs")).unwrap();
    let commands = std::fs::read_to_string(root.join("src/api/commands.rs")).unwrap();
    let lib = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();

    assert!(context.contains("enum AppearanceMessage"));
    assert!(context.contains("fn enqueue_system_theme_changed"));
    assert!(lib.contains("enqueue_system_theme_changed(resolved_mode)"));
    assert!(!lib.contains("controller.system_theme_changed(resolved_mode)"));
    assert!(commands.contains("spawn_blocking"));
}

#[test]
fn domain_window_port_has_no_api_dependency_and_events_target_main_only() {
    let root = manifest_dir();
    let window = std::fs::read_to_string(root.join("src/contexts/appearance/window.rs")).unwrap();
    let commands = std::fs::read_to_string(root.join("src/api/commands.rs")).unwrap();

    assert!(!window.contains("crate::api"));
    assert!(commands.contains("impl AppearanceEventPort"));
    assert!(commands.contains("EventTarget::webview_window(\"main\")"));
    assert!(commands.contains("emit_to"));
}

#[test]
fn setup_reconciles_system_mode_after_managed_state_is_available() {
    let lib = std::fs::read_to_string(manifest_dir().join("src/lib.rs")).unwrap();
    let managed_tail = lib
        .split_once("app.manage(Arc::clone(&controller));")
        .map(|(_, tail)| tail)
        .expect("setup must manage the controller before final system reconciliation");

    assert!(managed_tail.contains("window_port.current_system_mode()?"));
    assert!(managed_tail.contains("controller.enqueue_system_theme_changed(latest_system_mode)?"));
}

#[test]
fn hidden_bootstrap_show_and_repeat_restore_use_the_approved_activation_policy() {
    let root = manifest_dir();
    let window = std::fs::read_to_string(root.join("src/contexts/appearance/window.rs")).unwrap();
    let lib = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();

    assert!(window.contains("native_window.makeKeyAndOrderFront(None);"));
    assert!(window.contains("activate_current_application(marker)"));
    assert!(window.contains("uses_modern_application_activation"));
    assert!(window.contains("application.activate();"));
    assert!(window.contains("application.activateIgnoringOtherApps(true);"));
    assert!(window.contains("NSProcessInfo::processInfo()"));
    assert!(window.contains("appearance first-show activation deferred"));
    assert!(window.contains("REACTIVATION_SETTLE_DELAY: Duration = Duration::from_millis(350)"));
    assert!(window.matches("with_activation_settle_retry(").count() >= 3);
    assert!(!window.contains("ACTIVATION_CONFIRM"));

    assert!(lib.contains("WindowEvent::Focused(is_focused)"));
    assert!(lib.contains("appearance main window focus changed: focused={is_focused}"));
}
