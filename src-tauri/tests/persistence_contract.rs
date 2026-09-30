use harness_desktop_lib::controller::AppController;
use std::fs;
use std::path::Path;
use std::process::Command;
use tempfile::TempDir;

fn write_file(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().expect("fixture file must have a parent")).unwrap();
    fs::write(path, contents).unwrap();
}

fn checkout_fixture() -> TempDir {
    let fixture = TempDir::new().unwrap();
    write_file(
        &fixture.path().join("components/registry.yml"),
        "version: '1'\ncomponents: {}\n",
    );
    write_file(
        &fixture.path().join("scripts/adapters/build.py"),
        "raise SystemExit(0)\n",
    );
    let git = |args: &[&str]| {
        let output = Command::new("git")
            .current_dir(fixture.path())
            .args(args)
            .env("GIT_AUTHOR_NAME", "Persistence Contract")
            .env("GIT_AUTHOR_EMAIL", "contract@example.invalid")
            .env("GIT_COMMITTER_NAME", "Persistence Contract")
            .env("GIT_COMMITTER_EMAIL", "contract@example.invalid")
            .output()
            .unwrap();
        assert!(output.status.success());
    };
    git(&["init", "--quiet", "--initial-branch=main"]);
    git(&["add", "."]);
    git(&["commit", "--quiet", "-m", "persistence fixture"]);
    fixture
}

#[test]
fn registered_checkout_is_restored_from_app_owned_state() {
    let checkout = checkout_fixture();
    let state_root = TempDir::new().unwrap();
    let state_file = state_root.path().join("state.json");

    let first = AppController::with_state_file(state_file.clone());
    let registered = first.register_checkout(checkout.path()).unwrap();
    assert!(state_file.is_file());

    let restarted = AppController::with_state_file(state_file);
    let restored = restarted.saved_checkout().unwrap().unwrap();

    assert_eq!(restored.checkout_id, registered.checkout_id);
    assert_eq!(restored.canonical_path, registered.canonical_path);
}

#[test]
fn malformed_persisted_state_is_ignored_without_panicking() {
    let state_root = TempDir::new().unwrap();
    let state_file = state_root.path().join("state.json");
    fs::write(&state_file, "{malformed secret=never-display").unwrap();

    let controller = AppController::with_state_file(state_file.clone());

    assert!(controller.saved_checkout().unwrap().is_none());
    assert_eq!(
        fs::read_to_string(state_file).unwrap(),
        "{malformed secret=never-display"
    );
}
