use harness_desktop_lib::app_service::AppService;
use harness_desktop_lib::checkout::RegisteredCheckout;
use harness_desktop_lib::subprocess_runner::SubprocessRunner;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tempfile::TempDir;

struct CheckoutFixture {
    root: TempDir,
    marker: PathBuf,
}

fn write_file(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().expect("fixture file must have a parent")).unwrap();
    fs::write(path, contents).unwrap();
}

fn initialize_git(root: &Path) {
    for args in [
        &["init", "--quiet", "--initial-branch=main"][..],
        &["add", "."][..],
        &["commit", "--quiet", "-m", "trust fixture"][..],
    ] {
        let output = Command::new("git")
            .current_dir(root)
            .args(args)
            .env("GIT_AUTHOR_NAME", "Trust Contract")
            .env("GIT_AUTHOR_EMAIL", "contract@example.invalid")
            .env("GIT_COMMITTER_NAME", "Trust Contract")
            .env("GIT_COMMITTER_EMAIL", "contract@example.invalid")
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "git fixture command failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

fn write_registry_fixture(root: &Path) {
    write_file(
        &root.join("components/registry.yml"),
        r#"version: "1"
components:
  harnesskit.skill.zeta:
    kind: skill
    status: draft
    path: components/skills/zeta/component.yml
  harnesskit.skill.alpha:
    kind: skill
    status: draft
    path: components/skills/alpha/component.yml
"#,
    );
    write_file(
        &root.join("components/skills/alpha/component.yml"),
        r#"component_id: harnesskit.skill.alpha
kind: skill
status: draft
title: Alpha
summary: Alpha fixture component
owned_files:
  - components/skills/alpha/SKILL.md
targets:
  codex:
    output_path: .agents/skills/alpha/SKILL.md
provenance_mode: original
"#,
    );
    write_file(
        &root.join("components/skills/zeta/component.yml"),
        r#"component_id: harnesskit.skill.zeta
kind: skill
status: draft
title: Zeta
summary: Zeta fixture component
owned_files:
  - components/skills/zeta/SKILL.md
targets:
  codex:
    output_path: .agents/skills/zeta/SKILL.md
provenance_mode: original
"#,
    );
}

fn write_marker_build_script(root: &Path) -> PathBuf {
    let marker = root.join("build-marker.json");
    write_file(
        &root.join("scripts/adapters/build.py"),
        r##"from pathlib import Path
import json
import sys

checkout = Path(__file__).resolve().parents[2]
(checkout / "build-marker.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
args = sys.argv[1:]
for index, argument in enumerate(args):
    if argument != "--component":
        continue
    slug = args[index + 1].rsplit(".", 1)[-1]
    output = checkout / "dist" / "codex" / ".agents" / "skills" / slug / "SKILL.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"# {slug}\n", encoding="utf-8")
"##,
    );
    marker
}

fn valid_checkout_fixture() -> CheckoutFixture {
    let root = TempDir::new().unwrap();
    write_registry_fixture(root.path());
    let marker = write_marker_build_script(root.path());
    initialize_git(root.path());
    CheckoutFixture { root, marker }
}

fn read_marker_arguments(marker: &Path) -> Vec<String> {
    serde_json::from_str(&fs::read_to_string(marker).expect("build marker must exist")).unwrap()
}

#[test]
fn app_service_scan_builds_registered_components_in_deterministic_order() {
    let fixture = valid_checkout_fixture();
    let home = TempDir::new().unwrap();
    let checkout = RegisteredCheckout::open(fixture.root.path())
        .expect("a minimal HarnessKit checkout must register");

    let inventory = AppService::scan(&checkout, home.path(), "9.9.9-test", "2026-07-11T00:00:00Z")
        .expect("a registered checkout must be scannable");

    assert_eq!(
        read_marker_arguments(&fixture.marker),
        [
            "--component",
            "harnesskit.skill.alpha",
            "--component",
            "harnesskit.skill.zeta",
        ]
        .map(str::to_owned),
        "build.py must receive one repeated --component argument per sorted registry id"
    );
    assert_eq!(inventory.scan_metadata.app_version, "9.9.9-test");
    assert_eq!(
        inventory.scan_metadata.scan_timestamp,
        "2026-07-11T00:00:00Z"
    );
    assert_eq!(
        inventory
            .items
            .iter()
            .map(|item| item.component_id.as_str())
            .collect::<Vec<_>>(),
        vec!["harnesskit.skill.alpha", "harnesskit.skill.zeta"]
    );
}

#[test]
fn build_rejects_empty_component_selection_without_executing_script() {
    let fixture = valid_checkout_fixture();
    let checkout = RegisteredCheckout::open(fixture.root.path()).unwrap();

    let result = SubprocessRunner::run_build_components(&checkout, &[]);

    assert!(result.is_err(), "an empty build selection must fail closed");
    assert!(
        !fixture.marker.exists(),
        "build.py must not execute for an empty selection"
    );
}

#[test]
fn missing_registry_is_rejected_before_checkout_script_can_execute() {
    let root = TempDir::new().unwrap();
    let marker = write_marker_build_script(root.path());
    initialize_git(root.path());

    let result = RegisteredCheckout::open(root.path());

    assert!(
        result.is_err(),
        "components/registry.yml is a trust prerequisite"
    );
    assert!(
        !marker.exists(),
        "registration must validate registry presence without executing build.py"
    );
}

#[cfg(unix)]
#[test]
fn symlinked_build_script_escaping_checkout_is_rejected_without_execution() {
    use std::os::unix::fs::symlink;

    let checkout_root = TempDir::new().unwrap();
    write_registry_fixture(checkout_root.path());
    let outside = TempDir::new().unwrap();
    let escaped_marker = outside.path().join("escaped-marker");
    let outside_script = outside.path().join("build.py");
    write_file(
        &outside_script,
        r#"from pathlib import Path

Path(__file__).resolve().with_name("escaped-marker").write_text("executed", encoding="utf-8")
"#,
    );
    let checkout_script = checkout_root.path().join("scripts/adapters/build.py");
    fs::create_dir_all(checkout_script.parent().unwrap()).unwrap();
    symlink(&outside_script, &checkout_script).unwrap();
    initialize_git(checkout_root.path());

    if let Ok(checkout) = RegisteredCheckout::open(checkout_root.path()) {
        let components = vec!["harnesskit.skill.alpha".to_string()];
        assert!(
            SubprocessRunner::run_build_components(&checkout, &components).is_err(),
            "execution must reject a checkout-owned path that resolves outside the checkout"
        );
    }

    assert!(
        !escaped_marker.exists(),
        "an escaping symlink target must never execute"
    );
}

#[test]
fn subprocess_runner_does_not_publish_the_current_generic_python_escape_hatch() {
    let source = include_str!("../src/subprocess_runner.rs");

    assert!(
        !source.contains("pub fn execute_python"),
        "public callers must use checkout-bound operations instead of an arbitrary script API"
    );
}

#[test]
fn successful_build_without_declared_output_blocks_scan() {
    let fixture = valid_checkout_fixture();
    write_file(
        &fixture.root.path().join("scripts/adapters/build.py"),
        "# succeeds without materializing declared adapter outputs\n",
    );
    let home = TempDir::new().unwrap();
    let checkout = RegisteredCheckout::open(fixture.root.path()).unwrap();

    let result = AppService::scan(&checkout, home.path(), "9.9.9-test", "2026-07-11T00:00:00Z");

    let error = result.expect_err("missing declared adapter output must block scan");
    assert!(
        error.contains("adapter output"),
        "the error must identify the deterministic build contract"
    );
}

#[test]
fn failed_build_does_not_expose_raw_stderr() {
    let fixture = valid_checkout_fixture();
    write_file(
        &fixture.root.path().join("scripts/adapters/build.py"),
        "import sys\nprint('API_TOKEN=secret-value', file=sys.stderr)\nraise SystemExit(7)\n",
    );
    let home = TempDir::new().unwrap();
    let checkout = RegisteredCheckout::open(fixture.root.path()).unwrap();

    let error = AppService::scan(&checkout, home.path(), "9.9.9-test", "2026-07-11T00:00:00Z")
        .expect_err("failed build must block scan");

    assert_eq!(error, "HarnessKit component build failed");
    assert!(!error.contains("secret-value"));
}

#[test]
fn absolute_adapter_output_outside_checkout_is_rejected() {
    let fixture = valid_checkout_fixture();
    let outside = TempDir::new().unwrap();
    let escaped_output = outside.path().join("SKILL.md");
    write_file(&escaped_output, "outside content must not be trusted\n");
    write_file(
        &fixture
            .root
            .path()
            .join("components/skills/alpha/component.yml"),
        &format!(
            r#"component_id: harnesskit.skill.alpha
kind: skill
status: draft
title: Alpha
summary: Alpha fixture component
targets:
  codex:
    output_path: "{}"
"#,
            escaped_output.display()
        ),
    );
    let home = TempDir::new().unwrap();
    let checkout = RegisteredCheckout::open(fixture.root.path()).unwrap();

    let result = AppService::scan(&checkout, home.path(), "test", "2026-07-11T00:00:00Z");

    assert!(result.is_err());
}
