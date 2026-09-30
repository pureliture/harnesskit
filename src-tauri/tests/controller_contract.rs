use harness_desktop_lib::controller::AppController;
use harness_desktop_lib::install_flow::{ApplyApprovals, InstallRequest};
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
        .env("GIT_AUTHOR_NAME", "HarnessKit Contract")
        .env("GIT_AUTHOR_EMAIL", "contract@example.invalid")
        .env("GIT_COMMITTER_NAME", "HarnessKit Contract")
        .env("GIT_COMMITTER_EMAIL", "contract@example.invalid")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git fixture command failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn checkout_fixture() -> TempDir {
    let fixture = TempDir::new().unwrap();
    let root = fixture.path();
    write_file(
        &root.join("components/registry.yml"),
        "version: '1'\ncomponents: {}\n",
    );
    write_file(
        &root.join("scripts/adapters/build.py"),
        "raise SystemExit(0)\n",
    );
    write_file(
        &root.join("scripts/install/plan.py"),
        r#"import json
import sys

args = sys.argv[1:]
mode = args[args.index("--mode") + 1]
scope = args[args.index("--scope") + 1]
print(json.dumps({
    "mode": mode,
    "scope": scope,
    "targets": ["codex"],
    "components": ["harnesskit.skill.fixture"],
    "artifacts": [{
        "component_id": "harnesskit.skill.fixture",
        "target": "codex",
        "source": "dist/codex/.agents/skills/fixture/SKILL.md",
        "destination": ".agents/skills/fixture/SKILL.md",
    }],
    "runtime_surfaces": [],
    "activation_gates": [],
}, sort_keys=True))
"#,
    );
    write_file(
        &root.join("scripts/install/apply.py"),
        r#"from pathlib import Path
import json
import sys

args = sys.argv[1:]
plan = json.loads(Path(args[0]).read_text(encoding="utf-8"))
target_root = Path(args[args.index("--target-root") + 1])
destination = target_root / plan["artifacts"][0]["destination"]
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text("installed\n", encoding="utf-8")
"#,
    );
    write_file(
        &root.join("scripts/install/verify.py"),
        r#"from pathlib import Path
import json
import sys

args = sys.argv[1:]
plan = json.loads(Path(args[0]).read_text(encoding="utf-8"))
target_root = Path(args[args.index("--target-root") + 1])
destination = target_root / plan["artifacts"][0]["destination"]
if destination.read_text(encoding="utf-8") != "installed\n":
    raise SystemExit(31)
"#,
    );

    run_git(root, &["init", "--quiet", "--initial-branch=main"]);
    run_git(root, &["add", "."]);
    run_git(root, &["commit", "--quiet", "-m", "fixture checkout"]);
    fixture
}

#[test]
fn controller_owns_checkout_and_single_use_preview_state() {
    let checkout = checkout_fixture();
    let target = TempDir::new().unwrap();
    let controller = AppController::default();

    let registration = controller.register_checkout(checkout.path()).unwrap();
    assert_eq!(registration.repo_status.branch.as_deref(), Some("main"));
    assert!(!registration.checkout_id.is_empty());

    let preview = controller
        .preview_install(
            &registration.checkout_id,
            InstallRequest {
                profile: "harnesskit.profile.engineering".to_string(),
                scope: "user".to_string(),
                target_root: target.path().to_path_buf(),
            },
        )
        .unwrap();
    assert!(!preview.preview_id.is_empty());
    assert!(!preview.semantic_fingerprint.is_empty());
    assert_eq!(preview.request.profile, "harnesskit.profile.engineering");
    assert_eq!(preview.request.scope, "user");
    assert_eq!(preview.plan["mode"], "dry-run");
    assert_eq!(
        preview.plan["target_root"],
        fs::canonicalize(target.path())
            .unwrap()
            .to_string_lossy()
            .as_ref()
    );

    let unconfirmed = controller.apply_install(
        &preview.preview_id,
        ApplyApprovals {
            confirmed: false,
            overwrite: true,
            allow_runtime_hooks: true,
        },
    );
    assert!(unconfirmed.is_err(), "confirmation must be server-enforced");

    let execution = controller
        .apply_install(
            &preview.preview_id,
            ApplyApprovals {
                confirmed: true,
                overwrite: true,
                allow_runtime_hooks: true,
            },
        )
        .unwrap();
    assert_eq!(execution.apply.exit_code, 0);
    assert_eq!(execution.verify.unwrap().exit_code, 0);
    assert_eq!(execution.destinations.len(), 1);
    assert_eq!(
        execution.destinations[0].destination,
        ".agents/skills/fixture/SKILL.md"
    );
    assert_eq!(execution.destinations[0].status, "changed");
    assert_eq!(
        fs::read_to_string(target.path().join(".agents/skills/fixture/SKILL.md")).unwrap(),
        "installed\n"
    );

    assert!(
        controller
            .apply_install(
                &preview.preview_id,
                ApplyApprovals {
                    confirmed: true,
                    overwrite: true,
                    allow_runtime_hooks: true,
                },
            )
            .is_err(),
        "a consumed preview must not be replayable"
    );

    let unchanged_preview = controller
        .preview_install(
            &registration.checkout_id,
            InstallRequest {
                profile: "harnesskit.profile.engineering".to_string(),
                scope: "user".to_string(),
                target_root: target.path().to_path_buf(),
            },
        )
        .unwrap();
    let unchanged = controller
        .apply_install(
            &unchanged_preview.preview_id,
            ApplyApprovals {
                confirmed: true,
                overwrite: true,
                allow_runtime_hooks: true,
            },
        )
        .unwrap();
    assert_eq!(unchanged.destinations[0].status, "unchanged");
}

#[test]
fn approval_fingerprint_binds_the_canonical_target_root() {
    let checkout = checkout_fixture();
    let first_target = TempDir::new().unwrap();
    let second_target = TempDir::new().unwrap();
    let controller = AppController::default();
    let registration = controller.register_checkout(checkout.path()).unwrap();

    let preview_for = |target: &TempDir| {
        controller
            .preview_install(
                &registration.checkout_id,
                InstallRequest {
                    profile: "harnesskit.profile.engineering".to_string(),
                    scope: "user".to_string(),
                    target_root: target.path().to_path_buf(),
                },
            )
            .unwrap()
    };

    let first = preview_for(&first_target);
    let second = preview_for(&second_target);

    assert_ne!(first.semantic_fingerprint, second.semantic_fingerprint);
}

#[test]
fn controller_rejects_unknown_checkout_ids_before_plan_execution() {
    let target = TempDir::new().unwrap();
    let controller = AppController::default();

    let result = controller.preview_install(
        "unknown-checkout-id",
        InstallRequest {
            profile: "harnesskit.profile.engineering".to_string(),
            scope: "user".to_string(),
            target_root: target.path().to_path_buf(),
        },
    );

    assert!(result.is_err());
}
