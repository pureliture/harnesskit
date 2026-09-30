use std::fs;
use std::fs::{FileTimes, OpenOptions};
use std::io::Write;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

use harness_desktop_lib::contexts::local::scanner::LocalScanner;
use harness_desktop_lib::contexts::local::verified_path::{
    VerificationPolicy, VerifiedLocalPathResolver,
};
use harness_desktop_lib::contexts::local::{AdapterCatalog, ParserId, ToolId};
use sha2::{Digest, Sha256};
use tempfile::tempdir;

fn write(path: &Path, body: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, body).unwrap();
}

fn scan_handle(home: &Path) -> harness_desktop_lib::contexts::local::domain::InstanceHandle {
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
    let output = LocalScanner::scan_with_handles(home, &[adapter]).unwrap();
    let instance = output
        .result
        .instances
        .iter()
        .find(|instance| instance.name.as_deref() == Some("release"))
        .unwrap();
    output.instance_handles[&instance.instance_id].clone()
}

#[test]
fn strict_policy_rejects_changed_source_while_current_policy_opens_the_same_locator() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let source = home.join(".codex/skills/release/SKILL.md");
    write(
        &source,
        "---\nname: release\ndescription: before\n---\nbefore\n",
    );
    let handle = scan_handle(&home);
    write(
        &source,
        "---\nname: release\ndescription: after\n---\nafter changed\n",
    );

    let resolver = VerifiedLocalPathResolver;
    let strict = resolver
        .resolve(&handle, VerificationPolicy::StrictSnapshotIdentity)
        .unwrap_err();
    assert_eq!(strict.code, "stale_path_handle");

    let current = resolver
        .resolve(&handle, VerificationPolicy::CurrentSourceAtLocator)
        .unwrap();
    assert!(current.metadata_changed_since_snapshot());
    assert_eq!(current.canonical_path(), fs::canonicalize(&source).unwrap());
    assert_eq!(
        current.current_identity().size,
        fs::metadata(&source).unwrap().len()
    );

    let before = fs::metadata(&source).unwrap();
    let modified = before.modified().unwrap();
    let mut stream = OpenOptions::new()
        .write(true)
        .truncate(true)
        .open(&source)
        .unwrap();
    let replacement = vec![b'x'; usize::try_from(before.len()).unwrap()];
    stream.write_all(&replacement).unwrap();
    stream
        .set_times(FileTimes::new().set_modified(modified))
        .unwrap();
    drop(stream);
    let after = fs::metadata(&source).unwrap();
    assert_eq!(before.ino(), after.ino());
    assert_eq!(before.len(), after.len());
    assert_eq!(before.modified().unwrap(), after.modified().unwrap());
    let error = current.verify_unchanged().unwrap_err();
    assert_eq!(error.code, "stale_path_handle");
}

#[test]
fn current_policy_rejects_symlink_and_non_regular_replacements() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let release = home.join(".codex/skills/release");
    let source = release.join("SKILL.md");
    write(
        &source,
        "---\nname: release\ndescription: before\n---\nbody\n",
    );
    let handle = scan_handle(&home);
    let resolver = VerifiedLocalPathResolver;
    let current = resolver
        .resolve(&handle, VerificationPolicy::CurrentSourceAtLocator)
        .unwrap();
    let moved = release.with_file_name("release-real");
    fs::rename(&release, &moved).unwrap();
    std::os::unix::fs::symlink(&moved, &release).unwrap();

    let error = current.verify_unchanged().unwrap_err();
    assert_eq!(error.code, "stale_path_handle");
    let error = resolver
        .resolve(&handle, VerificationPolicy::CurrentSourceAtLocator)
        .unwrap_err();
    assert_eq!(error.code, "stale_path_handle");

    fs::remove_file(&release).unwrap();
    fs::create_dir_all(&release).unwrap();
    fs::create_dir(source).unwrap();
    let error = resolver
        .resolve(&handle, VerificationPolicy::CurrentSourceAtLocator)
        .unwrap_err();
    assert_eq!(error.code, "stale_path_handle");
}

#[test]
fn scan_handle_retains_parser_and_content_digest_without_serializing_them() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let source = b"---\nname: release\ndescription: fixture\n---\nbody\n";
    let path = home.join(".codex/skills/release/SKILL.md");
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(&path, source).unwrap();

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
    let output = LocalScanner::scan_with_handles(&home, &[adapter]).unwrap();
    let instance = output
        .result
        .instances
        .iter()
        .find(|instance| instance.name.as_deref() == Some("release"))
        .unwrap();
    let handle = &output.instance_handles[&instance.instance_id];

    assert_eq!(handle.source_parser_id, ParserId::SkillFrontmatterV1);
    assert_eq!(
        handle.scan_content_sha256,
        Some(Sha256::digest(source).into())
    );
    let serialized = serde_json::to_string(&output.result).unwrap();
    assert!(!serialized.contains("source_parser_id"));
    assert!(!serialized.contains("scan_content_sha256"));
}
