use std::fs;
use std::path::Path;
use std::process::Command;

use harness_desktop_lib::api::dto::sot::{
    CheckoutRegistrationDto, CheckoutRepoStatusDto, SotSnapshotDto,
};
use harness_desktop_lib::checkout::RegisteredCheckout;
use harness_desktop_lib::contexts::sot::SotContext;
use tempfile::TempDir;

fn run_git(root: &Path, args: &[&str]) {
    let output = Command::new("git")
        .current_dir(root)
        .args(args)
        .env("GIT_AUTHOR_NAME", "HarnessKit Contract")
        .env("GIT_AUTHOR_EMAIL", "contract@example.invalid")
        .env("GIT_COMMITTER_NAME", "HarnessKit Contract")
        .env("GIT_COMMITTER_EMAIL", "contract@example.invalid")
        .output()
        .expect("git must be available for SoT ownership tests");
    assert!(
        output.status.success(),
        "git fixture command failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn git_checkout_fixture() -> TempDir {
    let fixture = TempDir::new().unwrap();
    run_git(
        fixture.path(),
        &["init", "--quiet", "--initial-branch=main"],
    );
    fs::create_dir_all(fixture.path().join("schemas")).unwrap();
    for schema in [
        "component.schema.json",
        "agent.schema.json",
        "workflow.schema.json",
        "composite.schema.json",
        "profile.schema.json",
    ] {
        fs::write(fixture.path().join("schemas").join(schema), "{}").unwrap();
    }
    fs::create_dir_all(fixture.path().join("components")).unwrap();
    fs::write(
        fixture.path().join("components/registry.yml"),
        "version: '1'\ncomponents: {}\n",
    )
    .unwrap();
    run_git(fixture.path(), &["add", "."]);
    run_git(fixture.path(), &["commit", "--quiet", "-m", "fixture"]);
    fixture
}

#[test]
fn snapshot_load_fails_closed_when_registered_git_identity_disappears() {
    let fixture = git_checkout_fixture();
    let checkout = RegisteredCheckout::open(fixture.path()).unwrap();
    fs::remove_dir_all(fixture.path().join(".git")).unwrap();
    let context = SotContext::with_active_checkout(Some("checkout-a".to_string()));

    let error = context
        .load("checkout-a", &checkout)
        .expect_err("repository inspection failure must reject the snapshot");

    assert_eq!(error, "Registered checkout is not a local Git repository");
    assert_eq!(context.current_snapshot_id().unwrap(), None);
}

#[test]
fn snapshot_id_covers_the_final_repo_summary_payload() {
    let fixture = git_checkout_fixture();
    let checkout = RegisteredCheckout::open(fixture.path()).unwrap();
    let context = SotContext::with_active_checkout(Some("checkout-a".to_string()));
    let first = context.load("checkout-a", &checkout).unwrap();
    run_git(
        fixture.path(),
        &[
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "summary changed",
        ],
    );

    let second = context.load("checkout-a", &checkout).unwrap();

    assert_eq!(
        first.checkout_summary.source_revision,
        second.checkout_summary.source_revision
    );
    assert_ne!(
        first.checkout_summary.recent_commits,
        second.checkout_summary.recent_commits
    );
    assert_ne!(first.snapshot_id, second.snapshot_id);
}

#[test]
fn sot_context_owns_active_checkout_and_load_generation_with_the_snapshot() {
    let source = include_str!("../src/contexts/sot/context.rs");

    assert!(source.contains("active_checkout_id: Option<String>"));
    assert!(source.contains("load_generation: u64"));
    assert!(source.contains("fn activate_checkout"));
    assert!(source.contains("fn session_state"));
}

#[test]
fn activating_a_replacement_checkout_invalidates_the_previous_snapshot_pair() {
    let fixture = git_checkout_fixture();
    let checkout = RegisteredCheckout::open(fixture.path()).unwrap();
    let context = SotContext::with_active_checkout(Some("checkout-a".to_string()));
    let first = context.load("checkout-a", &checkout).unwrap();
    assert_eq!(
        context.session_state().unwrap(),
        (
            Some("checkout-a".to_string()),
            Some(first.snapshot_id.clone())
        )
    );

    context.activate_checkout("checkout-b".to_string()).unwrap();

    assert_eq!(
        context.session_state().unwrap(),
        (Some("checkout-b".to_string()), None)
    );
    assert_eq!(
        context.require_snapshot(&first.snapshot_id).unwrap_err(),
        "snapshot_expired"
    );
    assert_eq!(
        context.load("checkout-a", &checkout).unwrap_err(),
        "sot_checkout_not_active"
    );
}

#[test]
fn public_sot_snapshot_dto_never_serializes_the_checkout_absolute_path() {
    let fixture = git_checkout_fixture();
    let checkout = RegisteredCheckout::open(fixture.path()).unwrap();
    let context = SotContext::with_active_checkout(Some("checkout-a".to_string()));
    let snapshot = context.load("checkout-a", &checkout).unwrap();
    let canonical_path = snapshot.checkout_summary.canonical_path.clone();

    let serialized = serde_json::to_value(SotSnapshotDto::from(snapshot)).unwrap();
    let summary = serialized
        .get("checkout_summary")
        .and_then(serde_json::Value::as_object)
        .expect("public SoT snapshot must retain a typed checkout summary");

    assert!(summary.get("canonical_path").is_none());
    assert!(!serialized.to_string().contains(&canonical_path));

    let dto_source = include_str!("../src/api/dto/sot.rs");
    let summary_contract = dto_source
        .split_once("pub struct SotCheckoutSummaryDto")
        .and_then(|(_, tail)| tail.split_once("pub struct SotComponentDto"))
        .map(|(summary, _)| summary)
        .expect("SotCheckoutSummaryDto contract");
    assert!(!summary_contract.contains("canonical_path"));
}

#[test]
fn tauri_handler_exposes_no_legacy_restore_or_checkout_bound_scan_commands() {
    let runtime = include_str!("../src/lib.rs");
    let handler = runtime
        .split(".invoke_handler")
        .nth(1)
        .expect("Tauri runtime must declare one invoke handler");

    assert!(!runtime.contains("async fn restore_checkout"));
    assert!(!runtime.contains("async fn scan_harness"));
    assert!(!handler.contains("restore_checkout"));
    assert!(!handler.contains("scan_harness"));
}

#[test]
fn clone_and_register_use_typed_api_without_serializing_the_canonical_path() {
    let dto = CheckoutRegistrationDto {
        checkout_id: "checkout-a".to_string(),
        repo_status: CheckoutRepoStatusDto {
            branch: Some("main".to_string()),
            detached: false,
            dirty: false,
            recent_commits: vec!["fixture".to_string()],
        },
    };
    let serialized = serde_json::to_value(dto).unwrap();
    assert!(serialized.get("canonical_path").is_none());

    let runtime = include_str!("../src/lib.rs");
    let commands = include_str!("../src/api/commands.rs");
    let handler = runtime
        .split(".invoke_handler")
        .nth(1)
        .expect("Tauri runtime must declare one invoke handler");

    assert!(!runtime.contains("async fn clone_checkout"));
    assert!(!runtime.contains("async fn register_checkout"));
    assert!(handler.contains("api::commands::clone_checkout"));
    assert!(handler.contains("api::commands::register_checkout"));
    assert!(commands.contains("Result<CheckoutRegistrationDto, ApiErrorDto>"));
}
