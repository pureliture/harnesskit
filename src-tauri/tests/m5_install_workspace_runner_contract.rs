#![allow(dead_code)]

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::symlink;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::PathBuf;
use std::time::Duration;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use sha2::{Digest, Sha256};

#[path = "../src/contexts/install/workspace.rs"]
mod workspace;

#[path = "../src/contexts/install/runtime.rs"]
mod runtime;

use runtime::{
    ArtifactRunRequest, BoundedPlanProcess, BundledInstallRuntime, FixedInstallPlanRunner,
    PlanMode, PlanProcessCapture, PlanProcessPort, PlanProcessPortError, PlanProcessSpec,
    PlanRunRequest, SANDBOX_EXECUTABLE,
};
use workspace::InstallWorkspace;

fn source(relative: &str) -> String {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    fs::read_to_string(root.join(relative)).unwrap_or_default()
}

fn unique_temp(label: &str) -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let base = fs::canonicalize(std::env::temp_dir()).expect("canonical temp root");
    let path = base.join(format!("harness-m5-{label}-{}-{nonce}", std::process::id()));
    fs::create_dir_all(&path).expect("create temp fixture");
    fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("temp fixture mode");
    path
}

fn unique_short_temp() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .subsec_nanos();
    let path = PathBuf::from("/private/tmp").join(format!("hm5-{}-{nonce}", std::process::id()));
    fs::create_dir_all(&path).expect("short temp fixture");
    fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("short temp mode");
    path
}

fn write_fixture(root: &std::path::Path, relative: &str, body: &[u8], mode: u32) {
    let path = root.join(relative);
    fs::create_dir_all(path.parent().expect("fixture parent")).expect("create fixture parent");
    fs::write(&path, body).expect("write fixture");
    fs::set_permissions(path, fs::Permissions::from_mode(mode)).expect("fixture mode");
}

fn bundled_runtime_fixture(label: &str) -> (PathBuf, PathBuf, BundledInstallRuntime) {
    let container = unique_temp(label);
    let target_triple = format!("{}-apple-darwin", std::env::consts::ARCH);
    let root = container
        .join("Harness.app/Contents/Resources/install-runtime")
        .join(target_triple);
    write_fixture(&root, "python/bin/python3", b"runtime", 0o700);
    write_fixture(&root, "install_entry.py", b"pass\n", 0o600);
    fs::create_dir_all(root.join("vendor")).expect("vendor");
    let runtime =
        BundledInstallRuntime::from_test_bundle(root.clone()).expect("validated test bundle");
    (container, root, runtime)
}

#[test]
fn m5_declares_isolated_workspace_and_fixed_runner_seams() {
    let workspace = source("src/contexts/install/workspace.rs");
    let runtime = source("src/contexts/install/runtime.rs");

    for declaration in [
        "pub struct InstallWorkspace",
        "pub struct SourceManifest",
        "pub struct SourceManifestEntry",
        "pub enum InstallWorkspaceError",
        "install_workspace_inputs_v1",
    ] {
        assert!(
            workspace.contains(declaration),
            "missing workspace seam: {}",
            declaration
        );
    }
    assert!(
        !workspace.contains("unsafe extern \"C\""),
        "declared Rust 1.77.2 cannot compile unsafe extern blocks"
    );
    assert!(workspace.contains("parent_fd"));
    assert!(workspace.contains("root_name"));
    assert!(
        !workspace.contains("remove_dir_all(&self.root)"),
        "cleanup must stay anchored to the retained parent/root descriptors"
    );
    for declaration in [
        "pub struct FixedInstallPlanRunner",
        "pub trait PlanProcessPort",
        "pub struct PlanProcessSpec",
        "pub enum InstallRuntimeError",
        "/usr/bin/sandbox-exec",
        "install_plan_sandbox_v1",
    ] {
        assert!(
            runtime.contains(declaration),
            "missing runtime seam: {}",
            declaration
        );
    }
    let fixture_root =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/install-workspace");
    assert!(fixture_root
        .join("allowed/components/registry.yml")
        .is_file());
    assert!(fixture_root
        .join("allowed/scripts/install/plan.py")
        .is_file());
    assert!(fixture_root.join("excluded/docs/private.md").is_file());
}

#[test]
fn workspace_copies_only_allowlisted_regular_files_with_lexical_manifest_and_raii_cleanup() {
    let source = unique_temp("source-valid");
    let app_temp = unique_temp("app-temp");
    for (path, body, mode) in [
        ("components/registry.yml", b"components".as_slice(), 0o644),
        ("profiles/engineering.yml", b"profile".as_slice(), 0o640),
        ("adapters/codex/adapter.yml", b"adapter".as_slice(), 0o600),
        ("schemas/install.json", b"{}".as_slice(), 0o644),
        ("scripts/adapters/build.py", b"pass\n".as_slice(), 0o755),
        ("scripts/install/plan.py", b"pass\n".as_slice(), 0o700),
        ("scripts/profiles/selection.py", b"pass\n".as_slice(), 0o644),
    ] {
        write_fixture(&source, path, body, mode);
    }
    write_fixture(&source, "docs/private.md", b"not staged", 0o600);
    write_fixture(&source, ".git/config", b"not staged", 0o600);

    let workspace = InstallWorkspace::create(&source, &app_temp).expect("workspace");
    let workspace_root = workspace.root().to_path_buf();
    let paths = workspace
        .source_manifest()
        .entries
        .iter()
        .map(|entry| entry.relative_path.as_str())
        .collect::<Vec<_>>();

    assert_eq!(
        paths,
        [
            "adapters/codex/adapter.yml",
            "components/registry.yml",
            "profiles/engineering.yml",
            "schemas/install.json",
            "scripts/adapters/build.py",
            "scripts/install/plan.py",
            "scripts/profiles/selection.py",
        ]
    );
    assert!(workspace
        .source_manifest()
        .entries
        .iter()
        .all(|entry| entry.sha256.len() == 64));
    assert_eq!(workspace.source_manifest().sha256().len(), 64);
    assert!(workspace
        .source_manifest()
        .canonical_bytes()
        .starts_with(b"install_workspace_inputs_v1\n"));
    assert_eq!(
        fs::metadata(&workspace_root)
            .expect("workspace metadata")
            .permissions()
            .mode()
            & 0o777,
        0o700
    );
    assert_eq!(
        fs::read(workspace_root.join("scripts/install/plan.py")).expect("staged plan"),
        b"pass\n"
    );
    assert!(!workspace_root.join("docs/private.md").exists());
    assert!(!workspace_root.join(".git/config").exists());
    assert_eq!(
        fs::metadata(workspace_root.join("scripts/install/plan.py"))
            .expect("staged mode")
            .permissions()
            .mode()
            & 0o777,
        0o700
    );

    drop(workspace);
    assert!(!workspace_root.exists());
    let _ = fs::remove_dir_all(source);
    let _ = fs::remove_dir_all(app_temp);
}

#[derive(Default)]
struct RecordingProcess {
    specs: RefCell<Vec<RecordedSpec>>,
}

#[derive(Clone)]
struct RecordedSpec {
    executable: PathBuf,
    args: Vec<String>,
    environment: BTreeMap<String, String>,
    clear_environment: bool,
    timeout: Duration,
    stdout_limit: usize,
    stderr_limit: usize,
}

impl PlanProcessPort for RecordingProcess {
    fn run(&self, spec: &PlanProcessSpec) -> Result<PlanProcessCapture, PlanProcessPortError> {
        self.specs.borrow_mut().push(RecordedSpec {
            executable: spec.executable.clone(),
            args: spec.args.clone(),
            environment: spec.environment.clone(),
            clear_environment: spec.clear_environment,
            timeout: spec.timeout,
            stdout_limit: spec.stdout_limit,
            stderr_limit: spec.stderr_limit,
        });
        Ok(PlanProcessCapture {
            exit_code: 0,
            timed_out: false,
            stdout: b"{\"plan\":true}".to_vec(),
            stderr: Vec::new(),
        })
    }
}

#[test]
fn fixed_runner_builds_one_sandboxed_bundled_python_invocation_without_host_authority() {
    let source = unique_temp("source-runner");
    let app_temp = unique_temp("app-temp-runner");
    write_fixture(&source, "components/registry.yml", b"components", 0o644);
    write_fixture(&source, "scripts/install/plan.py", b"pass\n", 0o700);
    let workspace = InstallWorkspace::create(&source, &app_temp).expect("workspace");

    let (resources_container, resources, runtime) = bundled_runtime_fixture("resources");
    let process = RecordingProcess::default();
    let runner = FixedInstallPlanRunner::new(runtime, process).expect("fixed runner");

    let output = runner
        .run(
            &workspace,
            &PlanRunRequest {
                mode: PlanMode::DryRun,
                profile: "engineering".to_string(),
                scope: "user".to_string(),
                target_ids: vec!["codex".to_string()],
                component_ids: vec!["harnesskit.skill.release".to_string()],
            },
        )
        .expect("plan output");
    let specs = runner.process().specs.borrow();
    let spec = specs.first().expect("one invocation");
    let joined = format!(
        "{}\n{}\n{:?}",
        spec.executable.display(),
        spec.args.join("\n"),
        spec.environment
    );
    let sandbox_profile = spec.args.get(1).expect("seatbelt profile");
    let forbidden_target = "/private/tmp/operator-target";
    let forbidden_home = concat!("/", "Users/operator");

    assert_eq!(specs.len(), 1);
    assert_eq!(spec.executable, PathBuf::from(SANDBOX_EXECUTABLE));
    assert_eq!(spec.args.first().map(String::as_str), Some("-p"));
    assert!(sandbox_profile.contains("(deny default)"));
    assert!(sandbox_profile.contains("(deny network*)"));
    assert!(sandbox_profile.contains("(deny process-fork)"));
    assert!(sandbox_profile.contains("(literal \"/\")"));
    assert!(!sandbox_profile.contains("(subpath \"/\")"));
    assert!(sandbox_profile.contains(&resources.display().to_string()));
    assert!(sandbox_profile.contains(&workspace.root().display().to_string()));
    let write_rule = sandbox_profile
        .lines()
        .find(|line| line.starts_with("(allow file-write*"))
        .expect("write rule");
    assert!(!write_rule.contains(&format!("(subpath \"{}\")", workspace.root().display())));
    assert!(!joined.contains(&source.display().to_string()));
    assert!(!joined.contains(forbidden_target));
    assert!(!joined.contains(forbidden_home));
    assert!(!joined.contains("/usr/bin/python"));
    assert!(!joined.contains("/usr/bin/uv"));
    assert!(!joined.contains(".venv"));
    assert_eq!(
        spec.environment
            .keys()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        [
            "LANG",
            "LC_ALL",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONNOUSERSITE",
            "TMPDIR"
        ]
    );
    assert_eq!(
        spec.environment.get("PYTHONNOUSERSITE").map(String::as_str),
        Some("1")
    );
    assert!(spec.clear_environment);
    assert_eq!(spec.timeout.as_secs(), 60);
    assert_eq!(spec.stdout_limit, 8 * 1024 * 1024);
    assert_eq!(spec.stderr_limit, 1024 * 1024);
    assert!(spec.args.windows(3).any(|args| args == ["-I", "-B", "-S"]));
    assert_eq!(output.json, "{\"plan\":true}");

    drop(specs);
    drop(runner);
    drop(workspace);
    let _ = fs::remove_dir_all(source);
    let _ = fs::remove_dir_all(app_temp);
    let _ = fs::remove_dir_all(resources_container);
}

#[test]
fn artifact_projection_runner_is_profile_free_and_read_only() {
    let source = unique_temp("source-artifact-projection");
    let app_temp = unique_temp("app-temp-artifact-projection");
    write_fixture(&source, "components/registry.yml", b"components", 0o644);
    write_fixture(&source, "scripts/install/plan.py", b"pass\n", 0o700);
    let workspace = InstallWorkspace::create(&source, &app_temp).expect("workspace");
    let (resources_container, _resources, runtime) =
        bundled_runtime_fixture("resources-artifact-projection");
    let runner =
        FixedInstallPlanRunner::new(runtime, RecordingProcess::default()).expect("fixed runner");

    let output = runner
        .run_artifact_projection(
            &workspace,
            &ArtifactRunRequest {
                component_ids: vec!["harnesskit.skill.release".to_string()],
            },
        )
        .expect("projection output");
    let specs = runner.process().specs.borrow();
    let spec = specs.first().expect("one invocation");
    let joined = spec.args.join("\n");
    let sandbox_profile = spec.args.get(1).expect("seatbelt profile");

    assert_eq!(specs.len(), 1);
    assert!(joined.contains("artifact-projection"));
    assert!(!joined.contains("--profile"));
    assert!(!joined.contains("--scope"));
    assert!(!joined.contains("--target-id"));
    assert!(!joined.contains("--mode"));
    let write_rule = sandbox_profile
        .lines()
        .find(|line| line.starts_with("(allow file-write*"))
        .expect("write rule");
    assert!(!write_rule.contains("/dist"));
    assert_eq!(output.json, "{\"plan\":true}");

    drop(specs);
    drop(runner);
    drop(workspace);
    let _ = fs::remove_dir_all(source);
    let _ = fs::remove_dir_all(app_temp);
    let _ = fs::remove_dir_all(resources_container);
}

#[test]
fn fixed_runner_revalidates_bundled_resource_lineage_before_each_process_call() {
    let source = unique_temp("source-runtime-swap");
    let app_temp = unique_temp("app-temp-runtime-swap");
    write_fixture(&source, "components/registry.yml", b"components", 0o644);
    write_fixture(&source, "scripts/install/plan.py", b"pass\n", 0o700);
    let workspace = InstallWorkspace::create(&source, &app_temp).expect("workspace");
    let (resources_container, resources, runtime) =
        bundled_runtime_fixture("resources-runtime-swap");
    let python = resources.join("python/bin/python3");
    let runner =
        FixedInstallPlanRunner::new(runtime, RecordingProcess::default()).expect("fixed runner");
    fs::remove_file(&python).expect("remove bundled python");
    symlink("/usr/bin/python3", &python).expect("swap bundled python");

    let error = runner
        .run(
            &workspace,
            &PlanRunRequest {
                mode: PlanMode::DryRun,
                profile: "engineering".to_string(),
                scope: "user".to_string(),
                target_ids: vec!["codex".to_string()],
                component_ids: vec!["harnesskit.skill.release".to_string()],
            },
        )
        .expect_err("resource swap must fail closed");

    assert_eq!(error.code(), "install_runtime_unavailable");
    assert!(runner.process().specs.borrow().is_empty());

    drop(runner);
    drop(workspace);
    let _ = fs::remove_dir_all(source);
    let _ = fs::remove_dir_all(app_temp);
    let _ = fs::remove_dir_all(resources_container);
}

#[test]
fn workspace_rejects_symlink_and_special_inputs_and_detects_manifest_drift() {
    let app_temp = unique_temp("app-temp-malicious");
    let outside = unique_temp("outside-malicious");
    write_fixture(&outside, "secret.txt", b"secret", 0o600);

    let symlink_source = unique_temp("source-symlink");
    write_fixture(
        &symlink_source,
        "components/registry.yml",
        b"components",
        0o644,
    );
    fs::create_dir_all(symlink_source.join("scripts/install")).expect("symlink parent");
    symlink(
        outside.join("secret.txt"),
        symlink_source.join("scripts/install/plan.py"),
    )
    .expect("malicious symlink");
    let error =
        InstallWorkspace::create(&symlink_source, &app_temp).expect_err("symlink must fail closed");
    assert_eq!(error.code(), "preview_stale");

    let special_source = unique_short_temp();
    write_fixture(
        &special_source,
        "components/registry.yml",
        b"components",
        0o644,
    );
    fs::create_dir_all(special_source.join("profiles")).expect("special parent");
    let listener =
        UnixListener::bind(special_source.join("profiles/control.sock")).expect("special socket");
    let error = InstallWorkspace::create(&special_source, &app_temp)
        .expect_err("special file must fail closed");
    assert_eq!(error.code(), "preview_stale");
    drop(listener);

    let drift_source = unique_temp("source-drift");
    write_fixture(
        &drift_source,
        "components/registry.yml",
        b"components",
        0o644,
    );
    write_fixture(&drift_source, "scripts/install/plan.py", b"pass\n", 0o700);
    let workspace = InstallWorkspace::create(&drift_source, &app_temp).expect("workspace");
    fs::write(drift_source.join("scripts/install/plan.py"), b"changed\n").expect("change source");
    assert_eq!(
        workspace
            .verify_source(&drift_source)
            .expect_err("changed source")
            .code(),
        "preview_stale"
    );
    fs::write(drift_source.join("scripts/install/plan.py"), b"pass\n").expect("restore source");
    fs::set_permissions(
        drift_source.join("scripts/install/plan.py"),
        fs::Permissions::from_mode(0o700),
    )
    .expect("restore mode");
    write_fixture(&drift_source, "profiles/extra.yml", b"extra", 0o600);
    assert_eq!(
        workspace
            .verify_source(&drift_source)
            .expect_err("extra source")
            .code(),
        "preview_stale"
    );
    fs::remove_file(drift_source.join("profiles/extra.yml")).expect("remove extra");
    fs::remove_file(drift_source.join("components/registry.yml")).expect("remove source");
    assert_eq!(
        workspace
            .verify_source(&drift_source)
            .expect_err("missing source")
            .code(),
        "preview_stale"
    );

    drop(workspace);
    for path in [
        app_temp,
        outside,
        symlink_source,
        special_source,
        drift_source,
    ] {
        let _ = fs::remove_dir_all(path);
    }
}

#[test]
fn workspace_rejects_symlinked_intermediate_roots_and_non_owner_app_temp() {
    let fixture_parent = unique_temp("lineage-parent");
    let actual_source = fixture_parent.join("actual-source");
    fs::create_dir(&actual_source).expect("actual source");
    write_fixture(
        &actual_source,
        "components/registry.yml",
        b"components",
        0o644,
    );
    let linked_source = fixture_parent.join("linked-source");
    symlink(&actual_source, &linked_source).expect("source root symlink");
    let app_temp = unique_temp("lineage-app-temp");
    assert_eq!(
        InstallWorkspace::create(&linked_source, &app_temp)
            .expect_err("source lineage symlink")
            .code(),
        "preview_stale"
    );

    let allowed_symlink_source = unique_temp("allowed-root-symlink");
    let outside = unique_temp("allowed-root-outside");
    write_fixture(&outside, "registry.yml", b"outside", 0o600);
    symlink(&outside, allowed_symlink_source.join("components")).expect("allowed root symlink");
    assert_eq!(
        InstallWorkspace::create(&allowed_symlink_source, &app_temp)
            .expect_err("allowlist root symlink")
            .code(),
        "preview_stale"
    );

    let source = unique_temp("owner-source");
    write_fixture(&source, "components/registry.yml", b"components", 0o644);
    let open_app_temp = unique_temp("open-app-temp");
    fs::set_permissions(&open_app_temp, fs::Permissions::from_mode(0o755)).expect("open temp mode");
    assert_eq!(
        InstallWorkspace::create(&source, &open_app_temp)
            .expect_err("non-owner-only temp")
            .code(),
        "install_workspace_unavailable"
    );
    let linked_app_temp = fixture_parent.join("linked-app-temp");
    symlink(&app_temp, &linked_app_temp).expect("app temp symlink");
    assert_eq!(
        InstallWorkspace::create(&source, &linked_app_temp)
            .expect_err("app temp lineage symlink")
            .code(),
        "install_workspace_unavailable"
    );

    for path in [
        fixture_parent,
        app_temp,
        allowed_symlink_source,
        outside,
        source,
        open_app_temp,
    ] {
        let _ = fs::remove_dir_all(path);
    }
}

struct ScriptedProcess {
    capture: RefCell<Option<Result<PlanProcessCapture, PlanProcessPortError>>>,
}

impl PlanProcessPort for ScriptedProcess {
    fn run(&self, _spec: &PlanProcessSpec) -> Result<PlanProcessCapture, PlanProcessPortError> {
        self.capture
            .borrow_mut()
            .take()
            .expect("one scripted capture")
    }
}

#[test]
fn fixed_runner_maps_timeout_limits_non_utf8_launch_and_exit_without_raw_output() {
    let source = unique_temp("source-failures");
    let app_temp = unique_temp("app-temp-failures");
    write_fixture(&source, "components/registry.yml", b"components", 0o644);
    write_fixture(&source, "scripts/install/plan.py", b"pass\n", 0o700);
    let workspace = InstallWorkspace::create(&source, &app_temp).expect("workspace");
    let (resources_container, resources, _runtime) = bundled_runtime_fixture("resources-failures");
    let request = PlanRunRequest {
        mode: PlanMode::Apply,
        profile: "engineering".to_string(),
        scope: "user".to_string(),
        target_ids: vec!["codex".to_string()],
        component_ids: vec!["harnesskit.skill.release".to_string()],
    };
    let cases = [
        (
            Ok(PlanProcessCapture {
                exit_code: 0,
                timed_out: true,
                stdout: Vec::new(),
                stderr: Vec::new(),
            }),
            "install_plan_timeout",
        ),
        (
            Ok(PlanProcessCapture {
                exit_code: 0,
                timed_out: false,
                stdout: vec![b'x'; 8 * 1024 * 1024 + 1],
                stderr: Vec::new(),
            }),
            "install_plan_stdout_overflow",
        ),
        (
            Ok(PlanProcessCapture {
                exit_code: 0,
                timed_out: false,
                stdout: Vec::new(),
                stderr: vec![b'x'; 1024 * 1024 + 1],
            }),
            "install_plan_stderr_overflow",
        ),
        (
            Ok(PlanProcessCapture {
                exit_code: 0,
                timed_out: false,
                stdout: vec![0xff],
                stderr: Vec::new(),
            }),
            "install_plan_non_utf8_output",
        ),
        (
            Ok(PlanProcessCapture {
                exit_code: 9,
                timed_out: false,
                stdout: Vec::new(),
                stderr: b"SECRET_RAW_OUTPUT".to_vec(),
            }),
            "install_plan_failed",
        ),
        (
            Err(PlanProcessPortError::LaunchFailed),
            "install_plan_launch_failed",
        ),
    ];

    for (capture, expected_code) in cases {
        let runtime = BundledInstallRuntime::from_test_bundle(resources.clone())
            .expect("fresh validated runtime");
        let runner = FixedInstallPlanRunner::new(
            runtime,
            ScriptedProcess {
                capture: RefCell::new(Some(capture)),
            },
        )
        .expect("runner");
        let error = runner.run(&workspace, &request).expect_err("typed failure");
        assert_eq!(error.code(), expected_code);
        assert!(!format!("{error:?}").contains("SECRET_RAW_OUTPUT"));
    }

    drop(workspace);
    for path in [source, app_temp, resources_container] {
        let _ = fs::remove_dir_all(path);
    }
}

#[test]
fn bundled_runtime_requires_app_resource_shape_manifest_hash_target_and_exact_tree() {
    let (container, root, runtime) = bundled_runtime_fixture("runtime-authority");
    let (manifest, hash) =
        BundledInstallRuntime::build_test_manifest(&root).expect("test manifest");
    let manifest_value: serde_json::Value =
        serde_json::from_str(&manifest).expect("package manifest JSON");
    assert_eq!(manifest_value["schema_version"], 1);
    assert_eq!(
        manifest_value["runtime_id"],
        "harness-desktop-install-runtime-v1"
    );
    assert!(manifest_value["target"].is_string());
    assert!(manifest_value["lock_sha256"].is_string());
    assert!(manifest_value["entries"].is_array());
    assert!(manifest_value.get("target_triple").is_none());
    assert_eq!(runtime.manifest_sha256(), hash);
    assert_eq!(
        runtime.target_triple(),
        format!("{}-apple-darwin", std::env::consts::ARCH)
    );

    assert_eq!(
        BundledInstallRuntime::from_embedded_manifest(PathBuf::from("/"), &manifest, &hash)
            .expect_err("filesystem root is never a runtime bundle")
            .code(),
        "install_runtime_unavailable"
    );
    assert_eq!(
        BundledInstallRuntime::from_embedded_manifest(
            container.join("operator-selected-runtime"),
            &manifest,
            &hash,
        )
        .expect_err("arbitrary operator root is never a runtime bundle")
        .code(),
        "install_runtime_unavailable"
    );
    assert_eq!(
        BundledInstallRuntime::from_embedded_manifest(root.clone(), &manifest, &"0".repeat(64))
            .expect_err("manifest hash mismatch")
            .code(),
        "install_runtime_unavailable"
    );

    let mut wrong_target: serde_json::Value =
        serde_json::from_str(&manifest).expect("manifest JSON");
    wrong_target["target"] = serde_json::Value::String("wrong-apple-darwin".to_string());
    let wrong_target = serde_json::to_string(&wrong_target).expect("wrong target JSON");
    let wrong_target_hash = format!("{:x}", Sha256::digest(wrong_target.as_bytes()));
    assert_eq!(
        BundledInstallRuntime::from_embedded_manifest(
            root.clone(),
            &wrong_target,
            &wrong_target_hash
        )
        .expect_err("target mismatch")
        .code(),
        "install_runtime_unavailable"
    );

    fs::write(root.join("install_entry.py"), b"changed\n").expect("replace runtime member");
    let error = match FixedInstallPlanRunner::new(runtime, RecordingProcess::default()) {
        Ok(_) => panic!("regular-file runtime drift must fail closed"),
        Err(error) => error,
    };
    assert_eq!(error.code(), "install_runtime_unavailable");
    let _ = fs::remove_dir_all(container);
}

#[test]
fn bundled_runtime_rejects_replacement_of_any_absolute_bundle_ancestor() {
    let (container, root, runtime) = bundled_runtime_fixture("runtime-ancestor-swap");
    let original_app = container.join("Harness.app");
    let moved_app = container.join("Retained.app");
    fs::rename(&original_app, &moved_app).expect("move retained app bundle");
    write_fixture(&root, "python/bin/python3", b"malicious", 0o700);
    write_fixture(&root, "install_entry.py", b"pass\n", 0o600);
    fs::create_dir_all(root.join("vendor")).expect("replacement vendor");

    let error = match FixedInstallPlanRunner::new(runtime, RecordingProcess::default()) {
        Ok(_) => panic!("absolute bundle ancestor replacement must fail closed"),
        Err(error) => error,
    };
    assert_eq!(error.code(), "install_runtime_unavailable");
    let _ = fs::remove_dir_all(container);
}

struct WorkspaceMutatingProcess {
    relative_path: &'static str,
}

impl PlanProcessPort for WorkspaceMutatingProcess {
    fn run(&self, spec: &PlanProcessSpec) -> Result<PlanProcessCapture, PlanProcessPortError> {
        let workspace_index = spec
            .args
            .iter()
            .position(|argument| argument == "--workspace")
            .expect("workspace argument");
        let workspace = PathBuf::from(
            spec.args
                .get(workspace_index + 1)
                .expect("workspace argument value"),
        );
        write_fixture(&workspace, self.relative_path, b"mutation", 0o600);
        Ok(PlanProcessCapture {
            exit_code: 0,
            timed_out: false,
            stdout: b"{\"plan\":true}".to_vec(),
            stderr: Vec::new(),
        })
    }
}

#[test]
fn runner_enforces_zero_dry_run_diff_and_apply_changes_only_under_dist() {
    for (mode, relative_path, expected) in [
        (
            PlanMode::DryRun,
            "dist/forbidden.json",
            Err("preview_stale"),
        ),
        (
            PlanMode::Apply,
            "profiles/forbidden.yml",
            Err("preview_stale"),
        ),
        (PlanMode::Apply, "dist/allowed.json", Ok("{\"plan\":true}")),
    ] {
        let source = unique_temp("source-stage-gate");
        let app_temp = unique_temp("app-temp-stage-gate");
        write_fixture(&source, "components/registry.yml", b"components", 0o644);
        write_fixture(&source, "scripts/install/plan.py", b"pass\n", 0o700);
        let workspace = InstallWorkspace::create(&source, &app_temp).expect("workspace");
        let (container, _root, runtime) = bundled_runtime_fixture("stage-gate-runtime");
        let runner =
            FixedInstallPlanRunner::new(runtime, WorkspaceMutatingProcess { relative_path })
                .expect("runner");
        let result = runner.run(
            &workspace,
            &PlanRunRequest {
                mode,
                profile: "engineering".to_string(),
                scope: "user".to_string(),
                target_ids: vec!["codex".to_string()],
                component_ids: vec!["harnesskit.skill.release".to_string()],
            },
        );
        match expected {
            Ok(json) => assert_eq!(result.expect("allowed dist mutation").json, json),
            Err(code) => assert_eq!(result.expect_err("forbidden stage mutation").code(), code),
        }
        drop(runner);
        drop(workspace);
        for path in [source, app_temp, container] {
            let _ = fs::remove_dir_all(path);
        }
    }
}

#[test]
fn cleanup_removes_only_the_retained_workspace_and_preserves_path_replacement() {
    let source = unique_temp("source-cleanup-replacement");
    let app_temp = unique_temp("app-temp-cleanup-replacement");
    write_fixture(&source, "components/registry.yml", b"components", 0o644);
    let workspace = InstallWorkspace::create(&source, &app_temp).expect("workspace");
    let original = workspace.root().to_path_buf();
    let orphan = app_temp.join("retained-workspace-orphan");
    fs::rename(&original, &orphan).expect("move retained workspace");
    fs::create_dir(&original).expect("replacement workspace path");
    fs::set_permissions(&original, fs::Permissions::from_mode(0o700)).expect("replacement mode");
    fs::write(original.join("sentinel"), b"preserve").expect("replacement sentinel");

    drop(workspace);

    assert_eq!(
        fs::read(original.join("sentinel")).expect("sentinel"),
        b"preserve"
    );
    assert!(
        orphan.is_dir(),
        "mismatched retained root is left as an empty orphan"
    );
    assert!(
        fs::read_dir(&orphan)
            .expect("orphan directory")
            .next()
            .is_none(),
        "retained descriptor contents are cleaned"
    );
    for path in [source, app_temp] {
        let _ = fs::remove_dir_all(path);
    }
}

struct SwapThenBoundedProcess {
    delegated_error: RefCell<Option<PlanProcessPortError>>,
}

impl PlanProcessPort for SwapThenBoundedProcess {
    fn run(&self, spec: &PlanProcessSpec) -> Result<PlanProcessCapture, PlanProcessPortError> {
        let python = PathBuf::from(spec.args.get(2).expect("bundled python argument"));
        fs::remove_file(&python).expect("remove bundled python before spawn");
        fs::write(&python, b"replacement").expect("replace bundled python before spawn");
        let result = BoundedPlanProcess.run(spec);
        if let Err(error) = result {
            *self.delegated_error.borrow_mut() = Some(error);
        }
        result
    }
}

#[test]
fn bounded_process_revalidates_runtime_identity_immediately_before_spawn() {
    let source = unique_temp("source-pre-spawn");
    let app_temp = unique_temp("app-temp-pre-spawn");
    write_fixture(&source, "components/registry.yml", b"components", 0o644);
    write_fixture(&source, "scripts/install/plan.py", b"pass\n", 0o700);
    let workspace = InstallWorkspace::create(&source, &app_temp).expect("workspace");
    let (container, _root, runtime) = bundled_runtime_fixture("pre-spawn-runtime");
    let runner = FixedInstallPlanRunner::new(
        runtime,
        SwapThenBoundedProcess {
            delegated_error: RefCell::new(None),
        },
    )
    .expect("runner");
    let error = runner
        .run(
            &workspace,
            &PlanRunRequest {
                mode: PlanMode::DryRun,
                profile: "engineering".to_string(),
                scope: "user".to_string(),
                target_ids: vec!["codex".to_string()],
                component_ids: vec!["harnesskit.skill.release".to_string()],
            },
        )
        .expect_err("pre-spawn runtime replacement");
    assert_eq!(error.code(), "install_runtime_unavailable");
    assert_eq!(
        *runner.process().delegated_error.borrow(),
        Some(PlanProcessPortError::AuthorityChanged)
    );

    drop(runner);
    drop(workspace);
    for path in [source, app_temp, container] {
        let _ = fs::remove_dir_all(path);
    }
}

#[derive(Clone, Copy)]
enum AuthorityMutation {
    WorkspaceMode,
    RuntimeTmpMapping,
}

struct MutateThenRestoreBoundedProcess {
    mutation: AuthorityMutation,
    delegated_error: RefCell<Option<PlanProcessPortError>>,
}

impl PlanProcessPort for MutateThenRestoreBoundedProcess {
    fn run(&self, spec: &PlanProcessSpec) -> Result<PlanProcessCapture, PlanProcessPortError> {
        let workspace_index = spec
            .args
            .iter()
            .position(|argument| argument == "--workspace")
            .expect("workspace argument");
        let workspace = PathBuf::from(
            spec.args
                .get(workspace_index + 1)
                .expect("workspace argument value"),
        );
        let runtime_tmp = PathBuf::from(spec.environment.get("TMPDIR").expect("TMPDIR"));
        let moved_runtime_tmp = workspace.join(".install-runtime-tmp-retained");
        match self.mutation {
            AuthorityMutation::WorkspaceMode => {
                fs::set_permissions(&workspace, fs::Permissions::from_mode(0o755))
                    .expect("weaken workspace mode");
            }
            AuthorityMutation::RuntimeTmpMapping => {
                fs::rename(&runtime_tmp, &moved_runtime_tmp).expect("move retained runtime tmp");
                fs::create_dir(&runtime_tmp).expect("replacement runtime tmp");
                fs::set_permissions(&runtime_tmp, fs::Permissions::from_mode(0o700))
                    .expect("replacement runtime tmp mode");
            }
        }
        let result = BoundedPlanProcess.run(spec);
        match self.mutation {
            AuthorityMutation::WorkspaceMode => {
                fs::set_permissions(&workspace, fs::Permissions::from_mode(0o700))
                    .expect("restore workspace mode");
            }
            AuthorityMutation::RuntimeTmpMapping => {
                fs::remove_dir(&runtime_tmp).expect("remove replacement runtime tmp");
                fs::rename(&moved_runtime_tmp, &runtime_tmp).expect("restore retained runtime tmp");
            }
        }
        if let Err(error) = result {
            *self.delegated_error.borrow_mut() = Some(error);
        }
        result
    }
}

#[test]
fn bounded_process_revalidates_workspace_and_runtime_tmp_immediately_before_spawn() {
    for mutation in [
        AuthorityMutation::WorkspaceMode,
        AuthorityMutation::RuntimeTmpMapping,
    ] {
        let source = unique_temp("source-pre-spawn-authority");
        let app_temp = unique_temp("app-temp-pre-spawn-authority");
        write_fixture(&source, "components/registry.yml", b"components", 0o644);
        write_fixture(&source, "scripts/install/plan.py", b"pass\n", 0o700);
        let workspace = InstallWorkspace::create(&source, &app_temp).expect("workspace");
        let (container, _root, runtime) = bundled_runtime_fixture("pre-spawn-authority-runtime");
        let runner = FixedInstallPlanRunner::new(
            runtime,
            MutateThenRestoreBoundedProcess {
                mutation,
                delegated_error: RefCell::new(None),
            },
        )
        .expect("runner");
        let error = runner
            .run(
                &workspace,
                &PlanRunRequest {
                    mode: PlanMode::DryRun,
                    profile: "engineering".to_string(),
                    scope: "user".to_string(),
                    target_ids: vec!["codex".to_string()],
                    component_ids: vec!["harnesskit.skill.release".to_string()],
                },
            )
            .expect_err("pre-spawn authority mutation");
        assert_eq!(error.code(), "install_runtime_unavailable");
        assert_eq!(
            *runner.process().delegated_error.borrow(),
            Some(PlanProcessPortError::AuthorityChanged)
        );

        drop(runner);
        drop(workspace);
        for path in [source, app_temp, container] {
            let _ = fs::remove_dir_all(path);
        }
    }
}

#[test]
fn bounded_process_clears_environment_caps_output_and_kills_then_reaps_on_deadline() {
    let environment = BTreeMap::from([("HARNESS_MARKER".to_string(), "visible".to_string())]);
    let env_spec = PlanProcessSpec::test_only(
        PathBuf::from("/usr/bin/env"),
        Vec::new(),
        environment,
        Duration::from_secs(1),
        16 * 1024,
        1024,
    );
    let env_capture = BoundedPlanProcess
        .run(&env_spec)
        .expect("bounded env process");
    let env_output = String::from_utf8(env_capture.stdout).expect("env UTF-8");
    assert!(env_output
        .lines()
        .any(|line| line == "HARNESS_MARKER=visible"));
    assert!(!env_output.lines().any(|line| line.starts_with("HOME=")));
    assert!(!env_output.lines().any(|line| line.starts_with("PATH=")));

    let output_limit = 4096;
    let output_spec = PlanProcessSpec::test_only(
        PathBuf::from("/usr/bin/yes"),
        Vec::new(),
        BTreeMap::new(),
        Duration::from_secs(5),
        output_limit,
        1024,
    );
    let output_capture = BoundedPlanProcess
        .run(&output_spec)
        .expect("bounded output process");
    assert_eq!(
        output_capture.stdout.len(),
        output_limit + 1,
        "bounded capture: {:?}",
        output_capture
    );
    assert!(!output_capture.timed_out);

    let timeout_spec = PlanProcessSpec::test_only(
        PathBuf::from("/bin/sleep"),
        vec!["1".to_string()],
        BTreeMap::new(),
        Duration::from_millis(40),
        1024,
        1024,
    );
    let started = Instant::now();
    let timeout_capture = BoundedPlanProcess
        .run(&timeout_spec)
        .expect("timeout capture");
    assert!(timeout_capture.timed_out);
    assert!(started.elapsed() < Duration::from_secs(1));
}
