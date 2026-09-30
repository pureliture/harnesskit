use std::collections::BTreeSet;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde_json::json;
use sha2::{Digest, Sha256};

use super::workspace::SourceRevisionManifest;
use super::{
    BoundedPlanProcess, BundledInstallRuntime, DestinationApplyState, DestinationVerifyState,
    FixedInstallPlanRunner, InstallApprovals, InstallCoordinator, InstallOperationStatus,
    InstallPreviewInput,
};

const EMBEDDED_RUNTIME_MANIFEST: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/target/install-runtime/aarch64-apple-darwin/runtime-manifest.json"
));
const CHECKOUT_INPUTS: &[&str] = &[
    "components",
    "profiles",
    "adapters",
    "schemas",
    "scripts/adapters",
    "scripts/install",
];
const SCM_COMPONENTS: &[&str] = &[
    "harnesskit.skill.capture-collaboration-idea",
    "harnesskit.skill.github-workflow-policy",
    "harnesskit.skill.github-issue",
    "harnesskit.skill.github-start",
    "harnesskit.skill.github-pr",
    "harnesskit.skill.github-pr-followup",
    "harnesskit.agent.github-manager",
];

#[test]
fn packaged_app_path_requires_explicit_environment_configuration() {
    let previous = std::env::var_os("HARNESS_PACKAGED_APP");
    std::env::remove_var("HARNESS_PACKAGED_APP");

    let result = std::panic::catch_unwind(packaged_app_path);

    if let Some(previous) = previous {
        std::env::set_var("HARNESS_PACKAGED_APP", previous);
    }

    let panic = result.expect_err("missing HARNESS_PACKAGED_APP must fail closed");
    let message = panic
        .downcast_ref::<String>()
        .map(String::as_str)
        .or_else(|| panic.downcast_ref::<&str>().copied())
        .unwrap_or("non-string panic");
    assert!(
        message.contains("HARNESS_PACKAGED_APP is required"),
        "unexpected missing-configuration failure: {message}"
    );
}

#[test]
#[ignore = "requires the exact packaged release app"]
fn packaged_release_app_runs_stage_only_install_and_idempotent_rust_apply_verify() {
    assert_eq!(std::env::consts::ARCH, "aarch64");
    let app = packaged_app_path();
    let app_executable = app.join("Contents/MacOS/harness-desktop");
    let runtime_root = app
        .join("Contents/Resources/install-runtime")
        .join("aarch64-apple-darwin");
    assert!(
        app_executable.is_file(),
        "exact release executable is required"
    );
    assert!(runtime_root.is_dir(), "bundled install runtime is required");

    let embedded_manifest_sha256 = sha256(EMBEDDED_RUNTIME_MANIFEST.as_bytes());
    let runtime = BundledInstallRuntime::from_embedded_manifest(
        runtime_root,
        EMBEDDED_RUNTIME_MANIFEST,
        &embedded_manifest_sha256,
    )
    .expect("exact release runtime manifest, architecture, and tree");
    let runner = FixedInstallPlanRunner::new(runtime, BoundedPlanProcess)
        .expect("packaged runtime preflight");
    assert_eq!(runner.runtime_manifest_sha256(), embedded_manifest_sha256);

    let run = tempfile::tempdir_in("/private/tmp").expect("run-specific temp root");
    let run_root = fs::canonicalize(run.path()).expect("canonical run root");
    let checkout = run_root.join("checkout");
    let app_temp = run_root.join("app-temp");
    let target = run_root.join("target");
    fs::create_dir_all(&checkout).expect("checkout fixture root");
    copy_checkout_inputs(&repo_root(), &checkout);
    create_directory(&app_temp, 0o700);
    create_directory(&target, 0o700);

    let checkout_before = tree_sha256(&checkout);
    let target_before = tree_sha256(&target);
    let coordinator = InstallCoordinator::new(app_temp.clone(), Arc::new(runner))
        .expect("production install coordinator");
    let source_revision = SourceRevisionManifest::observe_root(&checkout)
        .expect("packaged source observation")
        .sha256();
    coordinator
        .register_artifact_source(&source_revision, &checkout)
        .expect("packaged artifact source registration");

    let first_preview = coordinator
        .preview(probe_input(&checkout, &target, &source_revision))
        .expect("packaged dry-run preview");
    assert!(!first_preview.artifacts.is_empty());
    assert!(first_preview
        .artifacts
        .iter()
        .all(|artifact| artifact.target == "codex"));
    assert_eq!(tree_sha256(&checkout), checkout_before);
    assert_eq!(tree_sha256(&target), target_before);

    let first = coordinator
        .apply(
            &first_preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: first_preview.fingerprint.clone(),
                overwrite: true,
                allow_runtime_hooks: true,
            },
        )
        .expect("packaged apply and Rust verify");
    assert_eq!(first.status, InstallOperationStatus::Complete);
    assert!(first.install_evidence_id.is_some());
    assert!(first.destinations.iter().all(|outcome| {
        outcome.apply_state == DestinationApplyState::Applied
            && outcome.verify_state == DestinationVerifyState::Verified
            && outcome.code.is_none()
    }));
    assert_eq!(tree_sha256(&checkout), checkout_before);
    assert_eq!(
        regular_file_paths(&target),
        destination_paths(&first.destinations)
    );
    let target_after_first = tree_sha256(&target);

    let second_preview = coordinator
        .preview(probe_input(&checkout, &target, &source_revision))
        .expect("second packaged dry-run preview");
    assert_eq!(second_preview.fingerprint, first_preview.fingerprint);
    assert!(second_preview.required_approvals.overwrite);
    let second = coordinator
        .apply(
            &second_preview.preview_id,
            InstallApprovals {
                confirmed: true,
                semantic_fingerprint: second_preview.fingerprint.clone(),
                overwrite: true,
                allow_runtime_hooks: true,
            },
        )
        .expect("second packaged apply and Rust verify");
    assert_eq!(second.status, InstallOperationStatus::Complete);
    assert!(second.install_evidence_id.is_some());
    assert!(second.destinations.iter().all(|outcome| {
        outcome.apply_state == DestinationApplyState::Unchanged
            && outcome.verify_state == DestinationVerifyState::Verified
            && outcome.code.is_none()
    }));
    assert_eq!(
        destination_paths(&second.destinations),
        destination_paths(&first.destinations)
    );
    assert_eq!(tree_sha256(&target), target_after_first);
    assert_eq!(tree_sha256(&checkout), checkout_before);
    assert!(fs::read_dir(&app_temp).expect("app temp").next().is_none());

    let report = serde_json::to_string_pretty(&json!({
        "case_id": "packaged_install_runtime_scm_codex_project_v1",
        "app": "harness-desktop.app",
        "app_executable_sha256": sha256_file(&app_executable),
        "bundled_runtime_manifest_sha256": embedded_manifest_sha256,
        "profile_id": "harnesskit.profile.scm",
        "target_id": "codex",
        "scope": "project",
        "preview_artifact_count": first_preview.artifacts.len(),
        "first_destinations": outcome_evidence(&first.destinations),
        "second_destinations": outcome_evidence(&second.destinations),
        "checkout_diff_count": 0,
        "target_before_sha256": target_before,
        "target_after_first_sha256": target_after_first,
        "target_after_second_sha256": tree_sha256(&target),
        "second_run_unchanged": true,
    }))
    .expect("probe report JSON");
    println!("{report}");
    if let Some(report_path) = std::env::var_os("HARNESS_PACKAGED_INSTALL_REPORT") {
        write_atomic_report(&PathBuf::from(report_path), report.as_bytes());
    }
}

fn packaged_app_path() -> PathBuf {
    let configured = std::env::var_os("HARNESS_PACKAGED_APP")
        .map(PathBuf::from)
        .expect("HARNESS_PACKAGED_APP is required");
    assert!(
        configured.is_absolute(),
        "HARNESS_PACKAGED_APP must be absolute"
    );
    fs::canonicalize(&configured).expect("canonical packaged app path")
}

fn write_atomic_report(path: &Path, bytes: &[u8]) {
    assert!(
        path.is_absolute(),
        "HARNESS_PACKAGED_INSTALL_REPORT must be absolute"
    );
    let parent = path.parent().expect("report parent");
    fs::create_dir_all(parent).expect("create report parent");
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .expect("report file name");
    let temporary = parent.join(format!(".{name}.{}.tmp", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)
        .expect("create report temp file");
    file.write_all(bytes).expect("write report temp file");
    file.write_all(b"\n").expect("terminate report JSON");
    file.sync_all().expect("sync report temp file");
    fs::rename(&temporary, path).expect("atomically publish report");
    fs::File::open(parent)
        .expect("open report parent")
        .sync_all()
        .expect("sync report parent");
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri below repo root")
        .to_path_buf()
}

fn create_directory(path: &Path, mode: u32) {
    fs::create_dir_all(path).expect("create directory");
    fs::set_permissions(path, fs::Permissions::from_mode(mode)).expect("directory mode");
}

fn copy_checkout_inputs(source_root: &Path, checkout: &Path) {
    for relative in CHECKOUT_INPUTS {
        copy_tree(&source_root.join(relative), &checkout.join(relative));
    }
}

fn copy_tree(source: &Path, destination: &Path) {
    let metadata = fs::symlink_metadata(source).expect("source metadata");
    assert!(
        !metadata.file_type().is_symlink(),
        "checkout fixture rejects symlinks"
    );
    if metadata.is_dir() {
        fs::create_dir_all(destination).expect("destination directory");
        let mut entries = fs::read_dir(source)
            .expect("source directory")
            .map(|entry| entry.expect("source entry"))
            .collect::<Vec<_>>();
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            copy_tree(&entry.path(), &destination.join(entry.file_name()));
        }
        fs::set_permissions(
            destination,
            fs::Permissions::from_mode(metadata.permissions().mode() & 0o777),
        )
        .expect("destination directory mode");
    } else {
        assert!(
            metadata.is_file(),
            "checkout fixture accepts regular files only"
        );
        fs::create_dir_all(destination.parent().expect("destination parent"))
            .expect("destination parent");
        fs::copy(source, destination).expect("copy checkout input");
        fs::set_permissions(
            destination,
            fs::Permissions::from_mode(metadata.permissions().mode() & 0o777),
        )
        .expect("destination file mode");
    }
}

fn probe_input(checkout: &Path, target: &Path, source_revision: &str) -> InstallPreviewInput {
    InstallPreviewInput {
        checkout_id: "packaged-probe-checkout".to_string(),
        checkout_root: checkout.to_path_buf(),
        sot_snapshot_id: "packaged-probe-sot".to_string(),
        source_revision: source_revision.to_string(),
        profile_id: "harnesskit.profile.scm".to_string(),
        scope: "project".to_string(),
        target_root: target.to_path_buf(),
        target_ids: BTreeSet::from(["codex".to_string()]),
        selected_component_ids: SCM_COMPONENTS
            .iter()
            .map(|component| (*component).to_string())
            .collect(),
    }
}

fn tree_sha256(root: &Path) -> String {
    let mut entries = Vec::new();
    collect_tree_evidence(root, root, &mut entries);
    entries.sort();
    sha256(entries.join("\n").as_bytes())
}

fn collect_tree_evidence(root: &Path, current: &Path, entries: &mut Vec<String>) {
    let mut children = fs::read_dir(current)
        .expect("tree directory")
        .map(|entry| entry.expect("tree entry"))
        .collect::<Vec<_>>();
    children.sort_by_key(|entry| entry.file_name());
    for child in children {
        let path = child.path();
        let relative = path
            .strip_prefix(root)
            .expect("relative evidence path")
            .to_string_lossy()
            .replace(std::path::MAIN_SEPARATOR, "/");
        let metadata = fs::symlink_metadata(&path).expect("tree metadata");
        assert!(
            !metadata.file_type().is_symlink(),
            "tree evidence rejects symlinks"
        );
        if metadata.is_dir() {
            entries.push(format!(
                "d\0{relative}\0{:03o}",
                metadata.permissions().mode() & 0o777
            ));
            collect_tree_evidence(root, &path, entries);
        } else {
            assert!(
                metadata.is_file(),
                "tree evidence accepts regular files only"
            );
            entries.push(format!(
                "f\0{relative}\0{:03o}\0{}\0{}",
                metadata.permissions().mode() & 0o777,
                metadata.len(),
                sha256_file(&path)
            ));
        }
    }
}

fn regular_file_paths(root: &Path) -> Vec<String> {
    let mut paths = Vec::new();
    collect_regular_file_paths(root, root, &mut paths);
    paths.sort();
    paths
}

fn collect_regular_file_paths(root: &Path, current: &Path, paths: &mut Vec<String>) {
    for entry in fs::read_dir(current).expect("target directory") {
        let path = entry.expect("target entry").path();
        let metadata = fs::symlink_metadata(&path).expect("target metadata");
        assert!(
            !metadata.file_type().is_symlink(),
            "target rejects symlinks"
        );
        if metadata.is_dir() {
            collect_regular_file_paths(root, &path, paths);
        } else {
            assert!(metadata.is_file(), "target accepts regular files only");
            paths.push(
                path.strip_prefix(root)
                    .expect("relative target path")
                    .to_string_lossy()
                    .replace(std::path::MAIN_SEPARATOR, "/"),
            );
        }
    }
}

fn destination_paths(destinations: &[super::InstallDestinationResult]) -> Vec<String> {
    let mut paths = destinations
        .iter()
        .map(|outcome| outcome.destination.clone())
        .collect::<Vec<_>>();
    paths.sort();
    paths
}

fn outcome_evidence(destinations: &[super::InstallDestinationResult]) -> Vec<serde_json::Value> {
    destinations
        .iter()
        .map(|outcome| {
            json!({
                "target": outcome.target,
                "destination": outcome.destination,
                "apply_state": format!("{:?}", outcome.apply_state),
                "verify_state": format!("{:?}", outcome.verify_state),
                "code": outcome.code,
            })
        })
        .collect()
}

fn sha256_file(path: &Path) -> String {
    sha256(&fs::read(path).expect("hash file"))
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
