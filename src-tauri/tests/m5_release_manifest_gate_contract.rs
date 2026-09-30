#[path = "../build_support/release_manifest.rs"]
mod release_manifest;

use std::fs;

use release_manifest::{
    load_verified_package_request, load_verified_runtime_manifest, verify_release_marker,
};
use sha2::{Digest, Sha256};

#[test]
fn release_manifest_is_required_and_must_match_the_pinned_hash() {
    let fixture = tempfile::tempdir().unwrap();
    let manifest = fixture.path().join("runtime-manifest.json");
    let pin = fixture.path().join("install-runtime.manifest.sha256");

    assert_eq!(
        load_verified_runtime_manifest(&manifest, &pin, true).unwrap_err(),
        "install_runtime_manifest_missing"
    );
    assert_eq!(
        load_verified_runtime_manifest(&manifest, &pin, false).unwrap(),
        "{}"
    );

    fs::write(&manifest, b"{\"schema_version\":1}\n").unwrap();
    fs::write(&pin, "0".repeat(64)).unwrap();
    assert_eq!(
        load_verified_runtime_manifest(&manifest, &pin, true).unwrap_err(),
        "install_runtime_manifest_hash_mismatch"
    );

    let expected = format!("{:x}", Sha256::digest(fs::read(&manifest).unwrap()));
    fs::write(&pin, format!("{expected}\n")).unwrap();
    assert_eq!(
        load_verified_runtime_manifest(&manifest, &pin, true).unwrap(),
        "{\"schema_version\":1}\n"
    );
}

#[test]
fn release_build_requires_the_verified_wrapper_marker_and_build_id() {
    assert_eq!(
        verify_release_marker(false, None, None, None).unwrap(),
        None
    );
    assert_eq!(
        verify_release_marker(true, None, None, None).unwrap_err(),
        "verified_package_wrapper_required"
    );
    assert_eq!(
        verify_release_marker(true, Some("1"), Some("not-hex"), None).unwrap_err(),
        "verified_package_build_id_invalid"
    );
    let build_id = "a".repeat(32);
    let inputs_hash = "b".repeat(64);
    assert_eq!(
        verify_release_marker(true, Some("1"), Some(&build_id), None).unwrap_err(),
        "verified_package_build_inputs_hash_invalid"
    );
    let verified = verify_release_marker(true, Some("1"), Some(&build_id), Some(&inputs_hash))
        .unwrap()
        .unwrap();
    assert_eq!(verified.build_id, build_id);
    assert_eq!(verified.build_inputs_sha256, inputs_hash);
}

#[test]
fn release_package_request_bytes_must_match_the_wrapper_hash() {
    let fixture = tempfile::tempdir().unwrap();
    let request = fixture.path().join("package-build-request.json");
    let body = b"{\"schema_version\":1}\n";
    fs::write(&request, body).unwrap();

    assert_eq!(
        load_verified_package_request(&request, &"0".repeat(64)).unwrap_err(),
        "verified_package_build_inputs_hash_mismatch"
    );
    let expected = format!("{:x}", Sha256::digest(body));
    assert_eq!(
        load_verified_package_request(&request, &expected).unwrap(),
        body
    );
}

#[test]
fn tauri_reverifies_before_bundle_and_the_wrapper_owns_the_release_entry() {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repo_root = manifest_dir.parent().unwrap();
    let config: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(manifest_dir.join("tauri.conf.json")).unwrap())
            .unwrap();
    assert_eq!(
        config["build"]["beforeBundleCommand"],
        "uv run --no-project --no-cache python scripts/package/prepare_install_runtime.py --repo-root . --verify-only"
    );
    let wrapper =
        fs::read_to_string(repo_root.join("scripts/package/build_verified_macos_app.py")).unwrap();
    assert!(wrapper.contains("[\"cargo\", \"tauri\", \"build\", \"--bundles\", \"app\"]"));
    assert!(wrapper.contains("HARNESS_VERIFIED_PACKAGE_BUILD"));
    assert!(wrapper.contains("HARNESS_PACKAGE_BUILD_ID"));
    assert!(wrapper.contains("HARNESS_PACKAGE_BUILD_INPUTS_SHA256"));
    let lib = fs::read_to_string(manifest_dir.join("src/lib.rs")).unwrap();
    assert!(lib.contains("HARNESS_PACKAGE_BUILD_INPUTS_SHA256="));
}
