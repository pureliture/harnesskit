use sha2::{Digest, Sha256};
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

#[path = "../build.rs"]
mod build_script;

const BUILD: &str = include_str!("../build.rs");
const MANIFEST: &str = include_str!("../../assets/tool-identities/manifest.json");
const APP: &str = include_str!("../../src-frontend/app.js");
const RESOLVER: &str = include_str!("../../src-frontend/tool-identities.js");

#[test]
fn unverified_tool_identity_assets_keep_hashes_but_need_not_be_published() {
    let repo_root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repository root");
    let manifest: serde_json::Value = serde_json::from_str(MANIFEST).expect("manifest json");
    assert_eq!(manifest["schema_version"], 1);
    let assets = manifest["assets"].as_array().expect("assets");
    assert_eq!(assets.len(), 4);
    for asset in assets {
        assert_eq!(asset["source"]["kind"], "vendor_downloaded");
        assert!(asset["sourceUrl"]
            .as_str()
            .is_some_and(|url| url.starts_with("https://")));
        assert_eq!(asset["product_use_approval"]["status"], "approved");
        assert_eq!(asset["copyright_status"], "not_asserted");
        assert_eq!(asset["license_status"], "not_asserted");
        assert_eq!(asset["trademark_review_status"], "not_asserted");
        let relative = asset["repository_path"].as_str().expect("repository path");
        assert!(!relative.starts_with('/') && !relative.contains(".."));
        let path = repo_root.join(relative);
        if path.exists() {
            let metadata = fs::symlink_metadata(&path).expect("asset metadata");
            assert!(metadata.is_file() && !metadata.file_type().is_symlink());
            let actual = format!("{:x}", Sha256::digest(fs::read(path).expect("asset bytes")));
            assert_eq!(asset["repository_sha256"], actual);
        }
        assert_eq!(
            asset["source"]["source_sha256"].as_str().map(str::len),
            Some(64)
        );
    }
    let serialized = MANIFEST.to_ascii_lowercase();
    assert!(!serialized.contains("/users/"));
    assert!(!serialized.contains("official"));
    assert!(!serialized.contains("endorsement"));
}

#[test]
fn build_guard_bundles_the_manifest_resolver_and_catalog_module_graph() {
    assert!(APP.contains("\"./tool-identities.js\""));
    assert!(RESOLVER.contains("\"./tool-identities.catalog.js\""));
    assert!(BUILD.contains("GENERATED_TOOL_IDENTITY_CATALOG"));
    assert!(BUILD.contains("verify_frontend_bundle_dist"));
    for contract in [
        "qualify_tool_identity_assets(&repo_root)",
        "assets/tool-identities/manifest.json",
        "asset_hash_mismatch",
        "asset_symlink_rejected",
        "asset_approval_missing",
        "asset_rights_restricted",
        "asset_rights_not_asserted",
        "write_generated_tool_identity_catalog",
    ] {
        assert!(
            BUILD.contains(contract),
            "build contract is missing {contract}"
        );
    }
    assert!(!RESOLVER.contains("/Users/"));
    assert!(!RESOLVER.contains("http://") && !RESOLVER.contains("https://"));
    assert!(!RESOLVER.contains("data:image"));
}

#[test]
fn build_guard_keeps_catalog_structure_fail_closed_but_allows_entry_local_text_fallback() {
    assert!(!BUILD.contains("TOOL_IDENTITY_MANIFEST_SHA256"));
    assert!(BUILD.contains("EXPECTED_TOOL_IDENTITIES"));
    assert!(BUILD.contains("TextOnly"));
    assert!(BUILD.contains("qualified_assets"));
    assert!(BUILD.contains("TOOL_IDENTITY_ASSET_FILES"));
}

#[test]
fn tracked_catalog_is_the_exact_qualified_build_input_for_every_packager() {
    let repo_root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repository root");
    let qualification = build_script::qualify_tool_identity_assets(repo_root);
    let tracked = fs::read(repo_root.join("src-frontend/tool-identities.catalog.js"))
        .expect("tracked tool identity catalog");

    assert_eq!(
        tracked,
        build_script::generated_tool_identity_catalog_body(&qualification),
        "Rust and Python package builds must bundle the same qualified catalog bytes",
    );
}

#[test]
fn frontend_staging_is_reverified_after_copy_before_publish() {
    let generated_catalog = BUILD
        .find("write_generated_tool_identity_catalog")
        .expect("generated runtime catalog");
    let staged_verification = BUILD
        .find("verify_staged_tool_identity_assets(staging, qualification)")
        .expect("post-copy staged tool identity verification");
    let publish = BUILD
        .find("fs::rename(&staging, &final_dir)")
        .expect("frontend staging publish");

    assert!(generated_catalog < staged_verification && staged_verification < publish);
    assert!(BUILD.contains("asset_staging_hash_mismatch"));
}

#[test]
fn rust_qualifier_keeps_unverified_assets_text_only_with_or_without_image_bytes() {
    let source_root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repository root");
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let repo_root = std::env::temp_dir().join(format!(
        "harness-m4c-rust-qualifier-{}-{unique}",
        std::process::id()
    ));
    let manifest: serde_json::Value = serde_json::from_str(MANIFEST).expect("manifest json");
    for asset in manifest["assets"].as_array().expect("assets") {
        let relative = asset["repository_path"].as_str().expect("repository path");
        if !source_root.join(relative).is_file() {
            continue;
        }
        let destination = repo_root.join(relative);
        fs::create_dir_all(destination.parent().expect("asset parent")).expect("asset directory");
        fs::copy(source_root.join(relative), destination).expect("copy fixture asset");
    }
    let manifest_path = repo_root.join("assets/tool-identities/manifest.json");
    fs::create_dir_all(manifest_path.parent().expect("manifest parent"))
        .expect("manifest directory");
    fs::write(&manifest_path, MANIFEST).expect("manifest fixture");

    let report = build_script::qualify_tool_identity_assets(&repo_root);
    assert_eq!(report.qualified_assets.len(), 4);
    assert!(report.qualified_assets.iter().all(|asset| asset.mode
        == build_script::ToolIdentityMode::TextOnly("asset_rights_not_asserted")));

    let staging = repo_root.join("staging");
    fs::create_dir(&staging).expect("staging directory");
    build_script::stage_qualified_tool_identity_assets(&repo_root, &staging, &report);
    for asset in manifest["assets"].as_array().expect("assets") {
        let relative = asset["bundled_relative_url"]
            .as_str()
            .expect("bundled relative url")
            .trim_start_matches("./");
        assert!(!staging.join(relative).exists());
    }
    let catalog = fs::read_to_string(staging.join("tool-identities.catalog.js"))
        .expect("generated runtime catalog");
    let catalog_json: serde_json::Value = serde_json::from_str(
        catalog
            .split("Object.freeze(")
            .nth(1)
            .expect("catalog prefix")
            .trim_end_matches(");\n"),
    )
    .expect("generated catalog json");
    assert!(catalog_json.as_array().expect("catalog entries").iter().all(|entry| {
        entry["mode"] == "text"
            && entry["src"].is_null()
            && entry["reason"] == "asset_rights_not_asserted"
    }));
    assert!(!catalog.contains("/Users/") && !catalog.contains("http://"));

    let public_root = repo_root.join("public");
    let public_manifest = public_root.join("assets/tool-identities/manifest.json");
    fs::create_dir_all(public_manifest.parent().expect("public manifest parent"))
        .expect("public manifest directory");
    fs::write(&public_manifest, MANIFEST).expect("public manifest fixture");
    let public_report = build_script::qualify_tool_identity_assets(&public_root);
    assert_eq!(
        build_script::generated_tool_identity_catalog_body(&report),
        build_script::generated_tool_identity_catalog_body(&public_report),
        "private and public trees must generate the same text-only catalog"
    );

    let mut invalid = manifest;
    invalid["assets"][0]["vendor_authority"] = serde_json::json!("vendor");
    fs::write(
        &manifest_path,
        serde_json::to_vec(&invalid).expect("invalid manifest json"),
    )
    .expect("invalid manifest fixture");
    assert!(std::panic::catch_unwind(|| {
        build_script::qualify_tool_identity_assets(&repo_root)
    })
    .is_err());

    fs::remove_dir_all(repo_root).expect("remove qualifier fixture");
}
