use harness_desktop_lib::install_flow::InstallFlowError;
use harness_desktop_lib::inventory_builder::InventoryBuilder;
use harness_desktop_lib::models::{ScanIssue, ScanResult};
use harness_desktop_lib::registry_reader::{RegistryData, RegistryReadError, RegistryReader};
use harness_desktop_lib::safety::redact_message;
use harness_desktop_lib::scanner::Scanner;
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use tempfile::TempDir;

const MALFORMED_SECRET: &str = "fixture-client-secret-never-expose";
const MALFORMED_ENDPOINT: &str = "https://private.internal.example.invalid/api";

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn write_file(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().expect("fixture file must have a parent")).unwrap();
    fs::write(path, contents).unwrap();
}

fn registry_fixture() -> TempDir {
    let fixture = TempDir::new().expect("create registry fixture");
    write_file(
        &fixture.path().join("components/registry.yml"),
        r#"version: "1"
components:
  harnesskit.skill.valid:
    kind: skill
    status: stable
    path: components/skills/valid/component.yml
  harnesskit.skill.malformed:
    kind: skill
    status: draft
    path: components/skills/malformed/component.yml
"#,
    );
    write_file(
        &fixture.path().join("components/skills/valid/component.yml"),
        r#"component_id: harnesskit.skill.valid
kind: skill
status: stable
title: Valid fixture
summary: A valid component remains available
targets:
  codex:
    output_path: .agents/skills/valid/SKILL.md
"#,
    );
    write_file(
        &fixture
            .path()
            .join("components/skills/malformed/component.yml"),
        &format!(
            "CLIENT_SECRET={MALFORMED_SECRET}\nPRIVATE_ENDPOINT={MALFORMED_ENDPOINT}\nsummary: [unterminated\n"
        ),
    );
    fixture
}

fn error_projection(data: &RegistryData) -> Vec<(String, String, String)> {
    data.errors
        .iter()
        .map(|error| {
            (
                error.category.clone(),
                error.path.clone(),
                error.message.clone(),
            )
        })
        .collect()
}

fn read_json(path: PathBuf) -> Value {
    let text = fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("failed to read {}: {error}", path.display()));
    serde_json::from_str(&text)
        .unwrap_or_else(|error| panic!("failed to parse {}: {error}", path.display()))
}

fn csp_directive_values<'a>(csp: &'a str, directive: &str) -> Vec<&'a str> {
    csp.split(';')
        .map(str::trim)
        .filter(|entry| !entry.is_empty())
        .find_map(|entry| {
            let mut tokens = entry.split_whitespace();
            (tokens.next() == Some(directive)).then(|| tokens.collect())
        })
        .unwrap_or_default()
}

#[test]
fn malformed_manifest_is_excluded_with_deterministic_redacted_error() {
    let first_fixture = registry_fixture();
    let second_fixture = registry_fixture();

    let first = RegistryReader::load_all(first_fixture.path())
        .expect("one malformed manifest must not hide valid registry data");
    let second = RegistryReader::load_all(second_fixture.path())
        .expect("the same fixture shape must remain loadable");

    assert_eq!(
        first
            .entries
            .iter()
            .map(|entry| entry.component_id.as_str())
            .collect::<Vec<_>>(),
        vec!["harnesskit.skill.valid"],
        "a malformed component must not remain in inventory input"
    );
    assert_eq!(
        first
            .manifests
            .keys()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        vec!["harnesskit.skill.valid"]
    );
    assert_eq!(error_projection(&first), error_projection(&second));
    assert_eq!(first.errors.len(), 1);

    let error = &first.errors[0];
    assert_eq!(error.category, "malformed_manifest");
    assert_eq!(
        error.path, "components/skills/malformed/component.yml",
        "error paths must be checkout-relative and fixture-root independent"
    );
    assert!(!error.message.trim().is_empty());
    assert!(!error.message.contains(MALFORMED_SECRET));
    assert!(!error.message.contains(MALFORMED_ENDPOINT));
    assert!(!error.message.contains("CLIENT_SECRET="));
    assert!(!error.message.contains("PRIVATE_ENDPOINT="));
}

#[test]
fn redaction_removes_secret_like_values_without_erasing_the_error_category() {
    let message = concat!(
        "category=build_failed ",
        "API_TOKEN=token-value ",
        "DB_PASSWORD=password-value ",
        "SERVICE_CREDENTIAL=credential-value ",
        "CLIENT_",
        "SECRET=secret-value ",
        "PRIVATE_ENDPOINT=https://private.example.invalid/api ",
        "operation aborted"
    );

    let redacted = redact_message(message);

    for sensitive_value in [
        "token-value",
        "password-value",
        "credential-value",
        "secret-value",
        "https://private.example.invalid/api",
    ] {
        assert!(
            !redacted.contains(sensitive_value),
            "redacted output exposed {sensitive_value}"
        );
    }
    assert!(redacted.contains("category=build_failed"));
    assert!(redacted.contains("operation aborted"));
}

#[test]
fn tauri_content_security_policy_and_capabilities_deny_remote_or_direct_access() {
    let config = read_json(manifest_dir().join("tauri.conf.json"));
    let csp = config["app"]["security"]["csp"]
        .as_str()
        .expect("CSP must be a non-null policy string");

    assert_eq!(csp_directive_values(csp, "script-src"), vec!["'self'"]);
    assert_eq!(
        csp_directive_values(csp, "connect-src"),
        vec!["'self'", "ipc:", "http://ipc.localhost"]
    );

    let capability = read_json(manifest_dir().join("capabilities/default.json"));
    let permissions = capability["permissions"]
        .as_array()
        .expect("permissions must be an array");
    assert!(permissions.iter().all(|permission| {
        permission.as_str().is_some_and(|name| {
            !name.starts_with("fs:")
                && !name.starts_with("shell:")
                && !name.starts_with("plugin:fs")
                && !name.starts_with("plugin:shell")
        })
    }));

    let cargo = fs::read_to_string(manifest_dir().join("Cargo.toml")).unwrap();
    let lib = fs::read_to_string(manifest_dir().join("src/lib.rs")).unwrap();
    assert!(!cargo.contains("tauri-plugin-fs"));
    assert!(!cargo.contains("tauri-plugin-shell"));
    assert!(!lib.contains("tauri_plugin_fs"));
    assert!(!lib.contains("tauri_plugin_shell"));
}

#[test]
fn install_failures_are_distinct_and_never_report_success() {
    let apply_failure = InstallFlowError::ApplyFailed.to_string();
    let verify_failure = InstallFlowError::VerifyFailed.to_string();

    assert_ne!(apply_failure, verify_failure);
    for failure in [apply_failure, verify_failure] {
        assert!(failure.to_ascii_lowercase().contains("failed"));
        assert!(!failure.to_ascii_lowercase().contains("success"));
    }
}

#[test]
fn unavailable_scan_root_is_reported_without_raw_content_or_panic() {
    let fixture = TempDir::new().expect("create scan fixture");
    let missing_root = fixture.path().join("missing-home");

    let result = Scanner::scan(&missing_root);

    assert!(result.items.is_empty());
    assert_eq!(result.issues.len(), 1);
    let issue = &result.issues[0];
    assert_eq!(issue.category, "scan_root_unavailable");
    assert_eq!(issue.path, missing_root.to_string_lossy());
    assert!(!issue.message.trim().is_empty());
    assert!(!issue.message.contains("TOKEN="));
}

#[test]
fn inventory_preserves_sorted_registry_and_scan_issues() {
    let checkout = TempDir::new().unwrap();
    let scan = ScanResult {
        items: vec![],
        user_level_surfaces: vec![],
        skipped_paths: vec![],
        issues: vec![ScanIssue {
            category: "permission_denied".to_string(),
            path: "/restricted/z".to_string(),
            message: "경로를 읽지 못했습니다.".to_string(),
        }],
    };
    let registry = RegistryData {
        entries: vec![],
        manifests: Default::default(),
        profile_memberships: vec![],
        errors: vec![RegistryReadError {
            category: "malformed_manifest".to_string(),
            path: "components/a/component.yml".to_string(),
            message: "component manifest could not be parsed".to_string(),
        }],
    };

    let inventory = InventoryBuilder::build(&scan, &registry, &[], &[], checkout.path(), "test");

    assert_eq!(inventory.issues.len(), 2);
    assert_eq!(inventory.issues[0].category, "malformed_manifest");
    assert_eq!(inventory.issues[1].category, "permission_denied");
}
