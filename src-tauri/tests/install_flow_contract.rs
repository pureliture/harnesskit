use harness_desktop_lib::checkout::RegisteredCheckout;
use harness_desktop_lib::install_flow::{
    ApplyApprovals, DestinationStatus, InstallFlow, InstallFlowError, InstallRequest,
};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tempfile::TempDir;

struct InstallFixture {
    checkout_root: TempDir,
    target_root: TempDir,
    behavior: PathBuf,
    plan_invocations: PathBuf,
    apply_invocation: PathBuf,
    verify_invocation: PathBuf,
    destination: PathBuf,
}

fn write_file(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().expect("fixture file must have a parent")).unwrap();
    fs::write(path, contents).unwrap();
}

fn run_git(root: &Path, args: &[&str]) {
    let output = Command::new("git")
        .current_dir(root)
        .args(args)
        .env("GIT_AUTHOR_NAME", "Install Flow Contract")
        .env("GIT_AUTHOR_EMAIL", "contract@example.invalid")
        .env("GIT_COMMITTER_NAME", "Install Flow Contract")
        .env("GIT_COMMITTER_EMAIL", "contract@example.invalid")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git fixture command failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn install_fixture() -> InstallFixture {
    let checkout_root = TempDir::new().unwrap();
    let target_root = TempDir::new().unwrap();
    let root = checkout_root.path();
    let behavior = root.join("test-plan-behavior");
    let plan_invocations = root.join("test-plan-invocations");
    let apply_invocation = root.join("test-apply-invocation.json");
    let verify_invocation = root.join("test-verify-invocation.json");
    let destination = target_root.path().join(".codex/hooks.json");

    write_file(
        &root.join("components/registry.yml"),
        "version: '1'\ncomponents: {}\n",
    );
    write_file(
        &root.join("scripts/adapters/build.py"),
        "raise SystemExit('build.py must not run during install flow tests')\n",
    );
    write_file(&behavior, "stable\n");
    write_file(&destination, "foreign-content\n");

    write_file(
        &root.join("scripts/install/plan.py"),
        r#"from pathlib import Path
import json
import sys

root = Path(__file__).resolve().parents[2]
args = sys.argv[1:]
mode = args[args.index("--mode") + 1]
scope = args[args.index("--scope") + 1]
profile = args[args.index("--profile") + 1]
with (root / "test-plan-invocations").open("a", encoding="utf-8") as stream:
    stream.write(mode + "\n")

behavior = (root / "test-plan-behavior").read_text(encoding="utf-8").strip()
destination = ".codex/hooks.json"
if behavior == "changed" and mode == "apply":
    destination = ".codex/changed-hooks.json"

plan = {
    "plan_id": "harnesskit.install-plan.engineering.user",
    "profile_id": profile,
    "scope": scope,
    "mode": mode,
    "targets": ["codex"],
    "components": ["harnesskit.hook.test"],
    "artifacts": [{
        "component_id": "harnesskit.hook.test",
        "target": "codex",
        "source": "dist/codex/.codex/hooks.json",
        "destination": destination,
    }],
    "runtime_surfaces": [{
        "target": "codex",
        "path": ".codex/hooks.json",
        "source": "dist/codex/.codex/hooks.json",
    }],
    "activation_gates": [{
        "id": "codex-hook-trust",
        "target": "codex",
        "reason": "fixture runtime hook approval",
        "required_before_apply": False,
        "required_before_runtime": True,
    }],
}
print(json.dumps(plan, sort_keys=True))
"#,
    );

    write_file(
        &root.join("scripts/install/apply.py"),
        r#"from pathlib import Path
import json
import sys

root = Path(__file__).resolve().parents[2]
args = sys.argv[1:]
plan_path = Path(args[0]).resolve()
target_root = Path(args[args.index("--target-root") + 1]).resolve()
(root / "test-apply-invocation.json").write_text(json.dumps({
    "plan_path": str(plan_path),
    "overwrite": "--overwrite" in args,
    "allow_runtime_hooks": "--allow-runtime-hooks" in args,
}), encoding="utf-8")

if "--overwrite" not in args or "--allow-runtime-hooks" not in args:
    raise SystemExit(23)
plan = json.loads(plan_path.read_text(encoding="utf-8"))
if plan["mode"] != "apply":
    raise SystemExit(24)
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

root = Path(__file__).resolve().parents[2]
args = sys.argv[1:]
plan_path = Path(args[0]).resolve()
target_root = Path(args[args.index("--target-root") + 1]).resolve()
(root / "test-verify-invocation.json").write_text(json.dumps({
    "plan_path": str(plan_path),
}), encoding="utf-8")
plan = json.loads(plan_path.read_text(encoding="utf-8"))
destination = target_root / plan["artifacts"][0]["destination"]
if plan["mode"] != "apply" or destination.read_text(encoding="utf-8") != "installed\n":
    raise SystemExit(31)
"#,
    );

    run_git(root, &["init", "--quiet", "--initial-branch=main"]);
    run_git(root, &["add", "."]);
    run_git(root, &["commit", "--quiet", "-m", "install flow fixture"]);

    InstallFixture {
        checkout_root,
        target_root,
        behavior,
        plan_invocations,
        apply_invocation,
        verify_invocation,
        destination,
    }
}

fn request(fixture: &InstallFixture) -> InstallRequest {
    InstallRequest {
        profile: "harnesskit.profile.engineering".to_string(),
        scope: "user".to_string(),
        target_root: fixture.target_root.path().to_path_buf(),
    }
}

fn approvals(confirmed: bool) -> ApplyApprovals {
    ApplyApprovals {
        confirmed,
        overwrite: true,
        allow_runtime_hooks: true,
    }
}

#[test]
fn unconfirmed_apply_is_rejected_without_regenerating_or_writing() {
    let fixture = install_fixture();
    let checkout = RegisteredCheckout::open(fixture.checkout_root.path()).unwrap();
    let flow = InstallFlow::new(&checkout);
    let preview = flow.preview(&request(&fixture)).unwrap();

    let result = flow.execute_confirmed(&preview, &approvals(false));

    assert!(matches!(
        result,
        Err(InstallFlowError::ConfirmationRequired)
    ));
    assert_eq!(
        fs::read_to_string(&fixture.plan_invocations).unwrap(),
        "dry-run\n",
        "confirmation must be checked before regenerating an apply plan"
    );
    assert_eq!(
        fs::read_to_string(&fixture.destination).unwrap(),
        "foreign-content\n"
    );
    assert!(!fixture.apply_invocation.exists());
    assert!(!fixture.verify_invocation.exists());
}

#[test]
fn stable_confirmed_apply_uses_explicit_approvals_and_verifies() {
    let fixture = install_fixture();
    let checkout = RegisteredCheckout::open(fixture.checkout_root.path()).unwrap();
    let flow = InstallFlow::new(&checkout);
    let preview = flow.preview(&request(&fixture)).unwrap();
    assert!(!preview.semantic_fingerprint().is_empty());

    let execution = flow
        .execute_confirmed(&preview, &approvals(true))
        .expect("an unchanged confirmed plan must apply and verify");

    assert_eq!(execution.apply.exit_code, 0);
    assert_eq!(execution.verify.as_ref().unwrap().exit_code, 0);
    assert_eq!(
        fs::read_to_string(&fixture.plan_invocations).unwrap(),
        "dry-run\napply\n"
    );
    assert_eq!(
        fs::read_to_string(&fixture.destination).unwrap(),
        "installed\n"
    );

    let apply_marker: Value =
        serde_json::from_str(&fs::read_to_string(&fixture.apply_invocation).unwrap()).unwrap();
    let verify_marker: Value =
        serde_json::from_str(&fs::read_to_string(&fixture.verify_invocation).unwrap()).unwrap();
    assert_eq!(apply_marker["overwrite"], true);
    assert_eq!(apply_marker["allow_runtime_hooks"], true);
    assert_eq!(apply_marker["plan_path"], verify_marker["plan_path"]);

    let temporary_plan = PathBuf::from(apply_marker["plan_path"].as_str().unwrap());
    assert!(!temporary_plan.starts_with(checkout.root()));
    assert!(!temporary_plan.starts_with(fixture.target_root.path()));
    assert!(
        !temporary_plan.exists(),
        "the app-owned plan must be temporary and removed after verification"
    );
}

#[test]
fn changed_apply_plan_is_rejected_before_apply_or_target_write() {
    let fixture = install_fixture();
    let checkout = RegisteredCheckout::open(fixture.checkout_root.path()).unwrap();
    let flow = InstallFlow::new(&checkout);
    let preview = flow.preview(&request(&fixture)).unwrap();
    fs::write(&fixture.behavior, "changed\n").unwrap();

    let result = flow.execute_confirmed(&preview, &approvals(true));

    assert!(matches!(result, Err(InstallFlowError::PlanChanged)));
    assert_eq!(
        fs::read_to_string(&fixture.plan_invocations).unwrap(),
        "dry-run\napply\n"
    );
    assert_eq!(
        fs::read_to_string(&fixture.destination).unwrap(),
        "foreign-content\n"
    );
    assert!(!fixture.apply_invocation.exists());
    assert!(!fixture.verify_invocation.exists());
    assert!(!fixture
        .target_root
        .path()
        .join(".codex/changed-hooks.json")
        .exists());
}

#[test]
fn successful_processes_that_leave_a_planned_destination_missing_report_failed() {
    let fixture = install_fixture();
    fs::remove_file(&fixture.destination).unwrap();
    write_file(
        &fixture
            .checkout_root
            .path()
            .join("scripts/install/apply.py"),
        "raise SystemExit(0)\n",
    );
    write_file(
        &fixture
            .checkout_root
            .path()
            .join("scripts/install/verify.py"),
        "raise SystemExit(0)\n",
    );
    let checkout = RegisteredCheckout::open(fixture.checkout_root.path()).unwrap();
    let flow = InstallFlow::new(&checkout);
    let preview = flow.preview(&request(&fixture)).unwrap();

    let attempt = flow.execute_confirmed(&preview, &approvals(true)).unwrap();

    assert_eq!(attempt.apply.exit_code, 0);
    assert_eq!(attempt.verify.unwrap().exit_code, 0);
    assert_eq!(attempt.destinations.len(), 1);
    assert_eq!(attempt.destinations[0].status, DestinationStatus::Failed);
}
