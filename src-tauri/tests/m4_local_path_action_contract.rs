use std::fs;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use harness_desktop_lib::contexts::local::domain::FileIdentityType;
use harness_desktop_lib::contexts::local::path_action::{
    LocalPathAction, LocalPathActionOutcome, NativePathActionError, NativePathActionPort,
    VerifiedNativePath,
};
use harness_desktop_lib::contexts::local::scanner::LocalScanner;
use harness_desktop_lib::contexts::local::{
    AdapterCatalog, LocalContext, LocalScanExecutor, LocalScanExecutorResult,
    LocalScanProgressPort, LocalScanPublication, LocalSnapshot, LocalSnapshotStatus, ToolId,
};
use tempfile::tempdir;

fn write(path: &Path, body: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, body).unwrap();
}

#[test]
fn publication_owns_backend_only_instance_handle_map() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/contexts/local");
    let domain = fs::read_to_string(root.join("domain.rs")).unwrap();
    let context = fs::read_to_string(root.join("context.rs")).unwrap();
    let engine = fs::read_to_string(root.join("engine.rs")).unwrap();
    let snapshot = fs::read_to_string(root.join("snapshot.rs")).unwrap();

    assert!(domain.contains("pub struct InstanceHandle"));
    assert!(domain.contains("pub struct FileIdentity"));
    assert!(domain.contains("pub raw_relative_components: Vec<Vec<u8>>"));
    assert!(domain.contains("pub config_entry_locator: Option<String>"));
    assert!(context.contains("pub instance_handles: BTreeMap<String, InstanceHandle>"));
    assert!(engine.contains("scan_with_handles"));

    for forbidden in [
        "InstanceHandle",
        "canonical_root",
        "raw_relative_components",
    ] {
        assert!(
            !snapshot.contains(forbidden),
            "backend handle leaked into snapshot module: {forbidden}"
        );
    }
}

#[test]
fn scan_builds_file_identity_handle_without_serializing_it() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    write(
        &home.join(".codex/skills/release/SKILL.md"),
        "---\nname: release\ndescription: fixture\n---\nprivate body\n",
    );
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();

    let output = LocalScanner::scan_with_handles(&home, &[adapter]).unwrap();
    let instance = output
        .result
        .instances
        .iter()
        .find(|instance| instance.name.as_deref() == Some("release"))
        .unwrap();
    let handle = output.instance_handles.get(&instance.instance_id).unwrap();

    assert_eq!(
        handle.raw_relative_components,
        [
            b"skills".to_vec(),
            b"release".to_vec(),
            b"SKILL.md".to_vec()
        ]
    );
    assert_eq!(handle.config_entry_locator, None);
    assert_eq!(
        handle.scan_file_identity.file_type,
        FileIdentityType::Regular
    );
    assert_eq!(handle.scan_file_identity.size, instance.size.unwrap());
    assert!(handle.root_device > 0);
    assert!(handle.root_inode > 0);

    let serialized = serde_json::to_string(&output.result).unwrap();
    assert!(!serialized.contains("canonical_root"));
    assert!(!serialized.contains("raw_relative_components"));
    assert!(!serialized.contains(&handle.root_inode.to_string()));
}

#[derive(Clone)]
struct FixedExecutor(LocalScanPublication);

impl LocalScanExecutor for FixedExecutor {
    fn execute(
        &self,
        _attempt_id: &str,
        _progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult {
        LocalScanExecutorResult::Complete(self.0.clone())
    }
}

#[derive(Default)]
struct RecordingNativePort(Mutex<Vec<(String, String)>>);

impl NativePathActionPort for RecordingNativePort {
    fn reveal(&self, target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError> {
        self.0.lock().unwrap().push((
            "reveal".to_string(),
            target.canonical_path().to_string_lossy().into_owned(),
        ));
        Ok(())
    }

    fn copy_path(&self, target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError> {
        self.0.lock().unwrap().push((
            "copy_path".to_string(),
            target.canonical_path().to_string_lossy().into_owned(),
        ));
        Ok(())
    }
}

fn scan_publication(home: &Path) -> (LocalScanPublication, String) {
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
    let output = LocalScanner::scan_with_handles(home, &[adapter]).unwrap();
    let instance_id = output
        .result
        .instances
        .iter()
        .find(|instance| instance.name.as_deref() == Some("release"))
        .or_else(|| output.result.instances.first())
        .unwrap()
        .instance_id
        .clone();
    let snapshot_id = "m4-snapshot".to_string();
    let snapshot = LocalSnapshot {
        snapshot_id: snapshot_id.clone(),
        attempt_id: "fixture-attempt".to_string(),
        status: LocalSnapshotStatus::Complete,
        scan_timestamp: "2026-07-12T00:00:00Z".to_string(),
        app_version: "0.1.0-test".to_string(),
        adapter_set_fingerprint: "a".repeat(64),
        content_fingerprint: "b".repeat(64),
        qualified_tools: output.result.qualified_tools,
        instances: output.result.instances,
        coverage: output.result.coverage,
        skipped_paths: output.result.skipped_paths,
        issues: output.result.issues,
        project_ignore_summaries: output.result.project_ignore_summaries,
    };
    (
        LocalScanPublication {
            instance_handle_ids: vec![instance_id.clone()],
            instance_handles: output.instance_handles,
            project_locations: output.project_locations,
            snapshot,
        },
        instance_id,
    )
}

fn published_context(
    publication: LocalScanPublication,
    port: Arc<dyn NativePathActionPort>,
) -> LocalContext {
    let context =
        LocalContext::with_executor_and_path_actions(Arc::new(FixedExecutor(publication)), port);
    context.start_scan().unwrap();
    let deadline = Instant::now() + Duration::from_secs(1);
    while context.state().latest_complete.is_none() {
        assert!(Instant::now() < deadline, "publication timeout");
        std::thread::yield_now();
    }
    context
}

#[test]
fn stale_and_missing_handles_fail_closed_without_native_action() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let path = home.join(".codex/skills/release/SKILL.md");
    write(
        &path,
        "---\nname: release\ndescription: fixture\n---\noriginal\n",
    );
    let (publication, instance_id) = scan_publication(&home);
    let snapshot_id = publication.snapshot.snapshot_id.clone();
    let port = Arc::new(RecordingNativePort::default());
    let context = published_context(publication, port.clone());
    fs::write(&path, b"changed identity").unwrap();

    let error = context
        .act_on_instance(&snapshot_id, &instance_id, LocalPathAction::Reveal)
        .unwrap_err();
    assert_eq!(error.code, "stale_path_handle");
    assert!(port.0.lock().unwrap().is_empty());

    write(
        &path,
        "---\nname: release\ndescription: fixture\n---\nrestored\n",
    );
    let (mut missing, missing_id) = scan_publication(&home);
    missing.instance_handles.clear();
    let missing_snapshot = missing.snapshot.snapshot_id.clone();
    let context = published_context(missing, port.clone());
    let error = context
        .act_on_instance(&missing_snapshot, &missing_id, LocalPathAction::CopyPath)
        .unwrap_err();
    assert_eq!(error.code, "instance_handle_missing");
    assert!(port.0.lock().unwrap().is_empty());
}

#[test]
fn reveal_and_copy_use_only_the_verified_native_port() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    write(
        &home.join(".codex/skills/release/SKILL.md"),
        "---\nname: release\ndescription: fixture\n---\nbody\n",
    );
    let (publication, instance_id) = scan_publication(&home);
    let snapshot_id = publication.snapshot.snapshot_id.clone();
    let port = Arc::new(RecordingNativePort::default());
    let context = published_context(publication, port.clone());

    assert_eq!(
        context
            .act_on_instance(&snapshot_id, &instance_id, LocalPathAction::Reveal)
            .unwrap(),
        LocalPathActionOutcome::Revealed
    );
    assert_eq!(
        context
            .act_on_instance(&snapshot_id, &instance_id, LocalPathAction::CopyPath)
            .unwrap(),
        LocalPathActionOutcome::PathCopied
    );
    assert_eq!(
        port.0
            .lock()
            .unwrap()
            .iter()
            .map(|entry| entry.0.as_str())
            .collect::<Vec<_>>(),
        ["reveal", "copy_path"]
    );

    let source = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("src/contexts/local/path_action.rs"),
    )
    .unwrap();
    for forbidden in [
        "std::process::Command",
        "/usr/bin/open",
        "generic_uri",
        "shell",
    ] {
        assert!(
            !source.contains(forbidden),
            "forbidden native fallback: {forbidden}"
        );
    }
}

#[test]
fn intermediate_directory_symlink_swap_is_rejected() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let release = home.join(".codex/skills/release");
    write(
        &release.join("SKILL.md"),
        "---\nname: release\ndescription: fixture\n---\nbody\n",
    );
    let (publication, instance_id) = scan_publication(&home);
    let snapshot_id = publication.snapshot.snapshot_id.clone();
    let port = Arc::new(RecordingNativePort::default());
    let context = published_context(publication, port.clone());

    let moved = release.with_file_name("release-real");
    fs::rename(&release, &moved).unwrap();
    std::os::unix::fs::symlink(&moved, &release).unwrap();

    let error = context
        .act_on_instance(&snapshot_id, &instance_id, LocalPathAction::Reveal)
        .unwrap_err();
    assert_eq!(error.code, "stale_path_handle");
    assert!(port.0.lock().unwrap().is_empty());
}

#[test]
fn leaf_symlink_and_type_swaps_are_rejected() {
    for replacement in ["symlink", "directory"] {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        write(
            &path,
            "---\nname: release\ndescription: fixture\n---\nbody\n",
        );
        let (publication, instance_id) = scan_publication(&home);
        let snapshot_id = publication.snapshot.snapshot_id.clone();
        let port = Arc::new(RecordingNativePort::default());
        let context = published_context(publication, port.clone());
        fs::remove_file(&path).unwrap();
        if replacement == "symlink" {
            let alternate = path.with_file_name("alternate.md");
            write(&alternate, "alternate");
            std::os::unix::fs::symlink(alternate, &path).unwrap();
        } else {
            fs::create_dir(&path).unwrap();
        }

        let error = context
            .act_on_instance(&snapshot_id, &instance_id, LocalPathAction::CopyPath)
            .unwrap_err();
        assert_eq!(error.code, "stale_path_handle", "replacement={replacement}");
        assert!(port.0.lock().unwrap().is_empty());
    }
}

struct ErrorNativePort(NativePathActionError);

impl NativePathActionPort for ErrorNativePort {
    fn reveal(&self, _target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError> {
        Err(self.0)
    }

    fn copy_path(&self, _target: &VerifiedNativePath<'_>) -> Result<(), NativePathActionError> {
        Err(self.0)
    }
}

#[test]
fn native_port_unavailable_and_failure_are_typed_action_errors() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    write(
        &home.join(".codex/skills/release/SKILL.md"),
        "---\nname: release\ndescription: fixture\n---\nbody\n",
    );
    let (publication, instance_id) = scan_publication(&home);
    let snapshot_id = publication.snapshot.snapshot_id.clone();

    for (native_error, action, expected) in [
        (
            NativePathActionError::Unavailable,
            LocalPathAction::Reveal,
            "reveal_unavailable",
        ),
        (
            NativePathActionError::Failed,
            LocalPathAction::CopyPath,
            "copy_path_failed",
        ),
    ] {
        let context =
            published_context(publication.clone(), Arc::new(ErrorNativePort(native_error)));
        let error = context
            .act_on_instance(&snapshot_id, &instance_id, action)
            .unwrap_err();
        assert_eq!(error.code, expected);
    }
}

#[test]
fn expired_snapshot_fails_before_path_resolution() {
    let error = LocalContext::default()
        .act_on_instance("expired", "opaque-instance", LocalPathAction::Reveal)
        .unwrap_err();
    assert_eq!(error.code, "snapshot_expired");
}
