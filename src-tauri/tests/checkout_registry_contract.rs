use harness_desktop_lib::checkout::{
    CheckoutRegistry, RegisteredCheckoutInfo, DEFAULT_HARNESSKIT_REPO_URL,
};
use harness_desktop_lib::repo_status::{RepoInspector, RepoStatus};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tempfile::TempDir;

const PUBLIC_HARNESSKIT_REPO_URL: &str = "https://github.com/pureliture/harnesskit.git";

fn write_file(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().expect("fixture file must have a parent")).unwrap();
    fs::write(path, contents).unwrap();
}

fn run_git(root: &Path, args: &[&str]) {
    let output = Command::new("git")
        .current_dir(root)
        .args(args)
        .env("GIT_AUTHOR_NAME", "HarnessKit Contract")
        .env("GIT_AUTHOR_EMAIL", "contract@example.invalid")
        .env("GIT_COMMITTER_NAME", "HarnessKit Contract")
        .env("GIT_COMMITTER_EMAIL", "contract@example.invalid")
        .output()
        .expect("git must be available for repo status contract tests");

    assert!(
        output.status.success(),
        "git fixture command failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn git_checkout_fixture() -> TempDir {
    let fixture = TempDir::new().unwrap();
    let root = fixture.path();

    run_git(root, &["init", "--quiet", "--initial-branch=main"]);
    write_file(
        &root.join("components/registry.yml"),
        "version: '1'\ncomponents: {}\n",
    );
    write_file(
        &root.join("scripts/adapters/build.py"),
        "raise SystemExit('fixture only')\n",
    );
    run_git(root, &["add", "."]);
    run_git(
        root,
        &["commit", "--quiet", "-m", "initial harnesskit checkout"],
    );

    fixture
}

#[test]
fn registry_rejects_a_non_git_directory_even_when_registry_exists() {
    let fixture = TempDir::new().unwrap();
    write_file(
        &fixture.path().join("components/registry.yml"),
        "version: '1'\ncomponents: {}\n",
    );
    let mut registry = CheckoutRegistry::default();

    let error = registry
        .register(fixture.path())
        .expect_err("checkout registration must require a Git worktree identity");

    assert_eq!(error, "Registered checkout is not a local Git repository");
}

#[test]
fn registry_returns_a_stable_opaque_id_and_resolves_without_the_raw_path() {
    assert_eq!(DEFAULT_HARNESSKIT_REPO_URL, PUBLIC_HARNESSKIT_REPO_URL);

    let fixture = git_checkout_fixture();
    let canonical_root = fs::canonicalize(fixture.path()).unwrap();
    let alternate_spelling = fixture.path().join(".");
    let mut registry = CheckoutRegistry::default();

    let first: RegisteredCheckoutInfo = registry.register(fixture.path()).unwrap();
    let duplicate: RegisteredCheckoutInfo = registry.register(&alternate_spelling).unwrap();

    assert_eq!(first.checkout_id, duplicate.checkout_id);
    assert_eq!(first.canonical_path, canonical_root);
    assert_eq!(duplicate.canonical_path, canonical_root);
    assert!(!first.checkout_id.is_empty());
    assert_ne!(first.checkout_id, canonical_root.to_string_lossy());

    let resolved = registry.resolve(&first.checkout_id).unwrap();
    assert_eq!(resolved.root(), canonical_root);
    assert!(registry.resolve("unknown-checkout-id").is_err());

    let mut restarted_registry = CheckoutRegistry::default();
    let restarted = restarted_registry.register(&alternate_spelling).unwrap();
    assert_eq!(restarted.checkout_id, first.checkout_id);
}

#[test]
fn repo_status_reports_canonical_local_git_truth_with_stable_commit_summaries() {
    let fixture = git_checkout_fixture();
    let canonical_root: PathBuf = fs::canonicalize(fixture.path()).unwrap();
    let mut registry = CheckoutRegistry::default();
    let registered = registry.register(fixture.path()).unwrap();
    let checkout = registry.resolve(&registered.checkout_id).unwrap();

    write_file(&fixture.path().join("untracked-change.txt"), "dirty\n");

    let first: RepoStatus = RepoInspector::status(checkout).unwrap();
    let second: RepoStatus = RepoInspector::status(checkout).unwrap();
    let first_json: Value = serde_json::to_value(&first).unwrap();
    let second_json: Value = serde_json::to_value(&second).unwrap();

    assert_eq!(
        first_json["canonical_path"],
        canonical_root.to_string_lossy().as_ref()
    );
    assert_eq!(first_json["current_branch"], "main");
    assert_eq!(first_json["dirty"], true);
    assert_eq!(
        first_json["recent_commit_summaries"],
        serde_json::json!(["initial harnesskit checkout"])
    );
    assert_eq!(
        first_json["recent_commit_summaries"], second_json["recent_commit_summaries"],
        "unchanged local git state must produce stable recent commit summaries"
    );
}
