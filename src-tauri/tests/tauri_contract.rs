use serde_json::Value;
use std::path::PathBuf;

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn read_json(path: PathBuf) -> Value {
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("failed to read {}: {error}", path.display()));
    serde_json::from_str(&text)
        .unwrap_or_else(|error| panic!("failed to parse {}: {error}", path.display()))
}

#[test]
fn tauri_config_exposes_the_narrow_invoke_bridge_and_existing_frontend() {
    let config = read_json(manifest_dir().join("tauri.conf.json"));
    let frontend_dist = config["build"]["frontendDist"]
        .as_str()
        .expect("frontendDist must be configured");

    assert_eq!(config["app"]["withGlobalTauri"], Value::Bool(true));
    assert!(
        manifest_dir().join(frontend_dist).is_dir(),
        "frontendDist must resolve to an existing directory"
    );
}

#[test]
fn macos_local_bundle_uses_explicit_official_ad_hoc_signing_identity() {
    let config = read_json(manifest_dir().join("tauri.conf.json"));

    assert_eq!(
        config["bundle"]["macOS"]["signingIdentity"],
        Value::String("-".to_string()),
        "local macOS bundles must explicitly select the ad-hoc signing identity"
    );
}

#[test]
fn frontend_has_no_direct_filesystem_or_shell_permission() {
    let capability = read_json(manifest_dir().join("capabilities/default.json"));
    let permissions = capability["permissions"]
        .as_array()
        .expect("permissions must be an array");

    assert_eq!(permissions, &[Value::String("core:default".to_string())]);
}

#[test]
fn tauri_bridge_exposes_typed_checkout_and_server_preview_commands() {
    let runtime = std::fs::read_to_string(manifest_dir().join("src/lib.rs")).unwrap();
    let commands = std::fs::read_to_string(manifest_dir().join("src/api/commands.rs")).unwrap();

    for command in [
        "api::commands::clone_checkout",
        "api::commands::register_checkout",
        "preview_install",
        "apply_install",
    ] {
        assert!(runtime.contains(command), "missing Tauri command {command}");
    }
    assert!(commands.contains("Result<CheckoutRegistrationDto, ApiErrorDto>"));
    assert!(!runtime.contains("restore_checkout"));
    assert!(!runtime.contains("scan_harness"));
    assert!(!runtime.contains("get_registry_data"));
    assert!(!runtime.contains("run_plan_dry_run"));
    assert!(!runtime.contains("clone_url"));
}

#[test]
fn production_runtime_excludes_webview_eval_and_packaged_evidence_driver() {
    let lib = std::fs::read_to_string(manifest_dir().join("src/lib.rs")).unwrap();
    let driver = manifest_dir().join("src/runtime_evidence.rs");

    assert!(!lib.contains("mod runtime_evidence;"));
    assert!(!lib.contains("runtime_evidence::script"));
    assert!(!lib.contains(".on_page_load("));
    assert!(!lib.contains(".eval("));
    assert!(
        !driver.exists(),
        "production source must not package a hidden runtime evidence driver"
    );
}
