use harness_desktop_lib::checkout::CheckoutCloner;
use std::fs;
use std::path::Path;
use std::process::Command;
use tempfile::TempDir;

fn write_file(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().expect("fixture file must have a parent")).unwrap();
    fs::write(path, contents).unwrap();
}

fn run_git(root: &Path, args: &[&str]) {
    let output = Command::new("git")
        .current_dir(root)
        .args(args)
        .env("GIT_AUTHOR_NAME", "Harness Clone Contract")
        .env("GIT_AUTHOR_EMAIL", "contract@example.invalid")
        .env("GIT_COMMITTER_NAME", "Harness Clone Contract")
        .env("GIT_COMMITTER_EMAIL", "contract@example.invalid")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git fixture command failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn source_fixture() -> TempDir {
    let fixture = TempDir::new().unwrap();
    write_file(
        &fixture.path().join("components/registry.yml"),
        "version: '1'\ncomponents: {}\n",
    );
    write_file(
        &fixture.path().join("scripts/adapters/build.py"),
        "raise SystemExit(0)\n",
    );
    run_git(
        fixture.path(),
        &["init", "--quiet", "--initial-branch=main"],
    );
    run_git(fixture.path(), &["add", "."]);
    run_git(fixture.path(), &["commit", "--quiet", "-m", "clone source"]);
    fixture
}

#[test]
fn clone_is_confined_to_the_app_owned_destination_and_is_idempotent() {
    let source = source_fixture();
    let app_data = TempDir::new().unwrap();

    let first =
        CheckoutCloner::clone_from(source.path().to_string_lossy().as_ref(), app_data.path())
            .unwrap();
    let second =
        CheckoutCloner::clone_from(source.path().to_string_lossy().as_ref(), app_data.path())
            .unwrap();

    let app_data = fs::canonicalize(app_data.path()).unwrap();
    assert_eq!(first.root(), app_data.join("harnesskit"));
    assert_eq!(second.root(), first.root());
    assert!(first.root().starts_with(app_data));
}

#[test]
fn existing_foreign_destination_is_never_removed_or_rewritten() {
    let source = source_fixture();
    let app_data = TempDir::new().unwrap();
    let destination = app_data.path().join("harnesskit");
    write_file(&destination.join("foreign.txt"), "preserve me\n");

    let result =
        CheckoutCloner::clone_from(source.path().to_string_lossy().as_ref(), app_data.path());

    assert!(result.is_err());
    assert_eq!(
        fs::read_to_string(destination.join("foreign.txt")).unwrap(),
        "preserve me\n"
    );
}

#[cfg(unix)]
#[test]
fn app_owned_destination_symlink_is_rejected_even_for_the_same_origin() {
    use std::os::unix::fs::symlink;

    let source = source_fixture();
    let external_parent = TempDir::new().unwrap();
    let external = CheckoutCloner::clone_from(
        source.path().to_string_lossy().as_ref(),
        external_parent.path(),
    )
    .unwrap();
    let app_data = TempDir::new().unwrap();
    symlink(external.root(), app_data.path().join("harnesskit")).unwrap();

    let result =
        CheckoutCloner::clone_from(source.path().to_string_lossy().as_ref(), app_data.path());

    assert!(result.is_err());
    assert!(
        external.root().is_dir(),
        "external checkout must remain untouched"
    );
}
