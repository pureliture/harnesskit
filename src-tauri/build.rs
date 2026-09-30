use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

#[path = "build_support/release_manifest.rs"]
mod release_manifest;

use release_manifest::{
    load_verified_package_request, load_verified_runtime_manifest, sha256_bytes,
    verify_release_marker,
};

const FRONTEND_SOURCE_CONTRACT_FILES: &[&str] = &[
    "src-frontend/package.json",
    "src-frontend/package-lock.json",
    "src-frontend/build.mjs",
];
const FRONTEND_BUNDLE_MANIFEST: &str = "src-tauri/target/frontend-dist/bundle-manifest.json";
const TOOL_IDENTITY_ASSET_FILES: &[&str] = &[
    "src-frontend/assets/tool-identities/codex.png",
    "src-frontend/assets/tool-identities/claude.png",
    "src-frontend/assets/tool-identities/antigravity.png",
    "src-frontend/assets/tool-identities/hermesagent.png",
];
const TOOL_IDENTITY_MANIFEST: &str = "assets/tool-identities/manifest.json";
const BRAND_PACKAGING_FILES: &[&str] = &[
    "assets/branding/harness-desktop/BRAND_ASSET_POLICY.md",
    "assets/branding/harness-desktop/source/harness-desktop-cabinet.png",
    "assets/branding/harness-desktop/derivation-manifest.json",
    "assets/packaging/macos/dmg-background.png",
    "assets/packaging/macos/dmg-background.svg",
    "assets/packaging/macos/dmg-layout-manifest.json",
];
const GENERATED_TOOL_IDENTITY_CATALOG: &str = "tool-identities.catalog.js";
struct ExpectedToolIdentity {
    asset_id: &'static str,
    canonical_tool_id: &'static str,
    aliases: &'static [&'static str],
    display_name: &'static str,
    source_url: &'static str,
    repository_path: &'static str,
    bundled_relative_url: &'static str,
    source_sha256: &'static str,
    repository_sha256: &'static str,
    derivation: &'static str,
    dimensions: (u32, u32),
}
const EXPECTED_TOOL_IDENTITIES: &[ExpectedToolIdentity] = &[
    ExpectedToolIdentity {
        asset_id: "tool.codex",
        canonical_tool_id: "codex",
        aliases: &["codex"],
        display_name: "Codex",
        source_url: "https://openai.com/brand/",
        repository_path: "src-frontend/assets/tool-identities/codex.png",
        bundled_relative_url: "./assets/tool-identities/codex.png",
        source_sha256: "01485e70cea6df8422f5abc643fbbd3c153442cc41da0e7d8e7451801ebf26e2",
        repository_sha256: "033bcda93ec990cb4a465d27b5d4f2d53af263eac703d1e73b2048d7b7e694af",
        derivation: "aspect_ratio_containment_rasterized_png",
        dimensions: (640, 640),
    },
    ExpectedToolIdentity {
        asset_id: "tool.claude-code",
        canonical_tool_id: "claude_code",
        aliases: &["claude_code", "claude-code", "claude"],
        display_name: "Claude Code",
        source_url: "https://anthropic.com/press-kit",
        repository_path: "src-frontend/assets/tool-identities/claude.png",
        bundled_relative_url: "./assets/tool-identities/claude.png",
        source_sha256: "f252cddcf91362ce4e01655044c7d8308c32b4972b1c16b459839ad8c61a0a68",
        repository_sha256: "f252cddcf91362ce4e01655044c7d8308c32b4972b1c16b459839ad8c61a0a68",
        derivation: "exact_bytes",
        dimensions: (1280, 1280),
    },
    ExpectedToolIdentity {
        asset_id: "tool.antigravity",
        canonical_tool_id: "antigravity",
        aliases: &[
            "antigravity",
            "antigravity_ide",
            "antigravity-cli",
            "antigravity_cli",
        ],
        display_name: "Antigravity",
        source_url: "https://antigravity.google/press?app=antigravity",
        repository_path: "src-frontend/assets/tool-identities/antigravity.png",
        bundled_relative_url: "./assets/tool-identities/antigravity.png",
        source_sha256: "e0cd08ccd10cd8d08ccf0ba449823ee88495825c0841619618100d3ab089f51e",
        repository_sha256: "e0cd08ccd10cd8d08ccf0ba449823ee88495825c0841619618100d3ab089f51e",
        derivation: "exact_bytes",
        dimensions: (540, 540),
    },
    ExpectedToolIdentity {
        asset_id: "tool.hermes",
        canonical_tool_id: "hermes",
        aliases: &["hermes", "hermes_agent", "hermesagent"],
        display_name: "Hermes",
        source_url: "https://hermes-agent.nousresearch.com/icon.png",
        repository_path: "src-frontend/assets/tool-identities/hermesagent.png",
        bundled_relative_url: "./assets/tool-identities/hermesagent.png",
        source_sha256: "5fbced606189c9bfc2925a16f8903333041cabbf1f8699fe9fe9b3e24504048f",
        repository_sha256: "5fbced606189c9bfc2925a16f8903333041cabbf1f8699fe9fe9b3e24504048f",
        derivation: "exact_bytes",
        dimensions: (48, 48),
    },
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ToolIdentityMode {
    Qualified,
    TextOnly(&'static str),
}

#[derive(Debug, Clone)]
pub(crate) struct QualifiedToolIdentity {
    pub(crate) canonical_tool_id: String,
    aliases: Vec<String>,
    display_name: String,
    repository_path: String,
    bundled_relative_url: String,
    expected_sha256: String,
    pub(crate) mode: ToolIdentityMode,
}

pub(crate) struct ToolIdentityQualification {
    manifest_sha256: String,
    pub(crate) qualified_assets: Vec<QualifiedToolIdentity>,
}
const ICON_FILES: &[&str] = &[
    "32x32.png",
    "128x128.png",
    concat!("128x128", "@2x.png"),
    "icon.icns",
    "icon.ico",
];

#[cfg(not(test))]
fn main() {
    println!("cargo:rerun-if-env-changed=HARNESS_VERIFIED_PACKAGE_BUILD");
    println!("cargo:rerun-if-env-changed=HARNESS_PACKAGE_BUILD_ID");
    println!("cargo:rerun-if-env-changed=HARNESS_PACKAGE_BUILD_INPUTS_SHA256");
    println!("cargo:rerun-if-changed=target/package-build-request.json");
    println!(
        "cargo:rerun-if-changed=target/install-runtime/aarch64-apple-darwin/runtime-manifest.json"
    );
    println!("cargo:rerun-if-changed=install-runtime.manifest.sha256");
    println!("cargo:rerun-if-changed=../schemas/install-target-contract-v1.json");
    let manifest_dir = PathBuf::from(
        std::env::var_os("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR is required"),
    );
    let repo_root = manifest_dir
        .parent()
        .expect("src-tauri must be below repository root");
    let release = std::env::var("PROFILE").as_deref() == Ok("release");
    let marker = std::env::var("HARNESS_VERIFIED_PACKAGE_BUILD").ok();
    let reuse_prebuilt_frontend = release && marker.as_deref() == Some("1");
    let tool_identity_qualification = qualify_tool_identity_assets(&repo_root);
    let frontend_bundle =
        prepare_frontend_bundle(&tool_identity_qualification, reuse_prebuilt_frontend);
    let build_id = std::env::var("HARNESS_PACKAGE_BUILD_ID").ok();
    let build_inputs_sha256 = std::env::var("HARNESS_PACKAGE_BUILD_INPUTS_SHA256").ok();
    let verified_release = verify_release_marker(
        release,
        marker.as_deref(),
        build_id.as_deref(),
        build_inputs_sha256.as_deref(),
    )
    .unwrap_or_else(|code| panic!("{code}"));
    let manifest_path =
        manifest_dir.join("target/install-runtime/aarch64-apple-darwin/runtime-manifest.json");
    let pinned_hash_path = manifest_dir.join("install-runtime.manifest.sha256");
    let manifest = load_verified_runtime_manifest(&manifest_path, &pinned_hash_path, release)
        .unwrap_or_else(|code| panic!("{code}"));
    let target = std::env::var("TARGET").expect("TARGET is required");
    if release && target != "aarch64-apple-darwin" {
        panic!("verified_package_target_invalid");
    }
    let target_contract = fs::read(
        manifest_dir
            .parent()
            .expect("src-tauri must be below repository root")
            .join("schemas/install-target-contract-v1.json"),
    )
    .expect("target_contract_unreadable");
    let tauri_config =
        fs::read(manifest_dir.join("tauri.conf.json")).expect("package_tauri_config_unreadable");
    let cargo_toml =
        fs::read(manifest_dir.join("Cargo.toml")).expect("package_cargo_toml_unreadable");
    let cargo_lock =
        fs::read(manifest_dir.join("Cargo.lock")).expect("package_cargo_lock_unreadable");
    let runtime_manifest_sha256 = sha256_bytes(manifest.as_bytes());
    let target_contract_sha256 = sha256_bytes(&target_contract);
    let tauri_config_sha256 = sha256_bytes(&tauri_config);
    let cargo_toml_sha256 = sha256_bytes(&cargo_toml);
    let cargo_lock_sha256 = sha256_bytes(&cargo_lock);
    let app_source_manifest_sha256 = app_source_manifest_sha256(&manifest_dir);
    let source_identity_sha256 = source_identity_sha256(
        &app_source_manifest_sha256,
        &cargo_lock_sha256,
        &cargo_toml_sha256,
        &frontend_bundle,
        &runtime_manifest_sha256,
        &target_contract_sha256,
        &tauri_config_sha256,
    );
    let profile = std::env::var("PROFILE").unwrap_or_else(|_| "unknown".to_string());
    let version = std::env::var("CARGO_PKG_VERSION").unwrap_or_else(|_| "unknown".to_string());
    let package_inputs = if let Some(verified) = verified_release {
        let request = load_verified_package_request(
            &manifest_dir.join("target/package-build-request.json"),
            verified.build_inputs_sha256,
        )
        .unwrap_or_else(|code| panic!("{code}"));
        let expected = package_build_inputs_body(PackageBuildInputs {
            build_id: verified.build_id,
            app_source_manifest_sha256: &app_source_manifest_sha256,
            version: &version,
            target: &target,
            profile: &profile,
            runtime_manifest_sha256: &runtime_manifest_sha256,
            target_contract_sha256: &target_contract_sha256,
            tauri_config_sha256: &tauri_config_sha256,
            cargo_toml_sha256: &cargo_toml_sha256,
            cargo_lock_sha256: &cargo_lock_sha256,
            frontend_bundle: &frontend_bundle,
            source_identity_sha256: &source_identity_sha256,
        });
        if request != expected {
            panic!("verified_package_build_inputs_mismatch");
        }
        request
    } else {
        package_build_inputs_body(PackageBuildInputs {
            build_id: "debug-unverified",
            app_source_manifest_sha256: &app_source_manifest_sha256,
            version: &version,
            target: &target,
            profile: &profile,
            runtime_manifest_sha256: &runtime_manifest_sha256,
            target_contract_sha256: &target_contract_sha256,
            tauri_config_sha256: &tauri_config_sha256,
            cargo_toml_sha256: &cargo_toml_sha256,
            cargo_lock_sha256: &cargo_lock_sha256,
            frontend_bundle: &frontend_bundle,
            source_identity_sha256: &source_identity_sha256,
        })
    };
    let package_inputs_hash = sha256_bytes(&package_inputs);
    write_package_build_inputs(&manifest_dir, &package_inputs);
    println!(
        "cargo:rustc-env=HARNESS_PACKAGE_BUILD_ID_EMBEDDED={}",
        build_id.as_deref().unwrap_or("debug-unverified")
    );
    println!("cargo:rustc-env=HARNESS_PACKAGE_BUILD_INPUTS_SHA256_EMBEDDED={package_inputs_hash}");
    let generated = format!(
        "pub const INSTALL_RUNTIME_MANIFEST_JSON: &str = {:?};\n",
        manifest
    );
    let out_dir = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("OUT_DIR"));
    std::fs::write(out_dir.join("install_runtime_manifest.rs"), generated)
        .expect("write install runtime manifest embed");
    tauri_build::build()
}

#[cfg(not(test))]
struct FrontendBundleIdentity {
    package_json_sha256: String,
    package_lock_sha256: String,
    build_script_sha256: String,
    bundle_manifest_sha256: String,
    bundle_output_sha256: String,
    license_inventory_sha256: String,
}

#[cfg(not(test))]
struct PackageBuildInputs<'a> {
    build_id: &'a str,
    app_source_manifest_sha256: &'a str,
    version: &'a str,
    target: &'a str,
    profile: &'a str,
    runtime_manifest_sha256: &'a str,
    target_contract_sha256: &'a str,
    tauri_config_sha256: &'a str,
    cargo_toml_sha256: &'a str,
    cargo_lock_sha256: &'a str,
    frontend_bundle: &'a FrontendBundleIdentity,
    source_identity_sha256: &'a str,
}

#[cfg(not(test))]
fn package_build_inputs_body(inputs: PackageBuildInputs<'_>) -> Vec<u8> {
    let value = serde_json::json!({
        "app_source_manifest_sha256": inputs.app_source_manifest_sha256,
        "build_id": inputs.build_id,
        "bundle_identifier": "io.github.pureliture.harnesskit",
        "cargo_lock_sha256": inputs.cargo_lock_sha256,
        "cargo_toml_sha256": inputs.cargo_toml_sha256,
        "frontend_build_script_sha256": inputs.frontend_bundle.build_script_sha256,
        "frontend_bundle_manifest_sha256": inputs.frontend_bundle.bundle_manifest_sha256,
        "frontend_bundle_output_sha256": inputs.frontend_bundle.bundle_output_sha256,
        "frontend_license_inventory_sha256": inputs.frontend_bundle.license_inventory_sha256,
        "frontend_package_json_sha256": inputs.frontend_bundle.package_json_sha256,
        "frontend_package_lock_sha256": inputs.frontend_bundle.package_lock_sha256,
        "product_name": "HarnessKit",
        "profile": inputs.profile,
        "runtime_manifest_sha256": inputs.runtime_manifest_sha256,
        "schema_version": 1,
        "source_identity_sha256": inputs.source_identity_sha256,
        "target": inputs.target,
        "target_contract_sha256": inputs.target_contract_sha256,
        "tauri_config_sha256": inputs.tauri_config_sha256,
        "version": inputs.version,
    });
    let mut body = serde_json::to_vec(&value).expect("serialize package build inputs");
    body.push(b'\n');
    body
}

#[cfg(not(test))]
fn source_identity_sha256(
    app_source_manifest_sha256: &str,
    cargo_lock_sha256: &str,
    cargo_toml_sha256: &str,
    frontend_bundle: &FrontendBundleIdentity,
    runtime_manifest_sha256: &str,
    target_contract_sha256: &str,
    tauri_config_sha256: &str,
) -> String {
    sha256_bytes(
        format!(
            concat!(
                "app_source_manifest_sha256={}\n",
                "cargo_lock_sha256={}\n",
                "cargo_toml_sha256={}\n",
                "frontend_build_script_sha256={}\n",
                "frontend_bundle_manifest_sha256={}\n",
                "frontend_bundle_output_sha256={}\n",
                "frontend_license_inventory_sha256={}\n",
                "frontend_package_json_sha256={}\n",
                "frontend_package_lock_sha256={}\n",
                "runtime_manifest_sha256={}\n",
                "target_contract_sha256={}\n",
                "tauri_config_sha256={}\n"
            ),
            app_source_manifest_sha256,
            cargo_lock_sha256,
            cargo_toml_sha256,
            frontend_bundle.build_script_sha256,
            frontend_bundle.bundle_manifest_sha256,
            frontend_bundle.bundle_output_sha256,
            frontend_bundle.license_inventory_sha256,
            frontend_bundle.package_json_sha256,
            frontend_bundle.package_lock_sha256,
            runtime_manifest_sha256,
            target_contract_sha256,
            tauri_config_sha256,
        )
        .as_bytes(),
    )
}

#[cfg(not(test))]
fn app_source_manifest_sha256(manifest_dir: &Path) -> String {
    let repo_root = manifest_dir
        .parent()
        .expect("src-tauri must be below repository root");
    let mut entries = Vec::new();
    add_source_file(repo_root, &manifest_dir.join("build.rs"), &mut entries);
    add_source_file(
        repo_root,
        &repo_root.join(TOOL_IDENTITY_MANIFEST),
        &mut entries,
    );
    for relative in BRAND_PACKAGING_FILES {
        add_source_file(repo_root, &repo_root.join(relative), &mut entries);
    }
    for relative in ["src", "build_support", "capabilities"] {
        collect_source_tree(repo_root, &manifest_dir.join(relative), &mut entries);
    }
    for relative in FRONTEND_SOURCE_CONTRACT_FILES {
        add_source_file(repo_root, &repo_root.join(relative), &mut entries);
    }
    let frontend_bundle_root = repo_root
        .join(FRONTEND_BUNDLE_MANIFEST)
        .parent()
        .expect("frontend bundle manifest parent")
        .to_path_buf();
    collect_source_tree(repo_root, &frontend_bundle_root, &mut entries);
    for relative in TOOL_IDENTITY_ASSET_FILES {
        match optional_verified_repository_file(repo_root, relative) {
            Ok(Some(path)) => add_source_file(repo_root, &path, &mut entries),
            Ok(None) => {}
            Err(code) => panic!("{code}"),
        }
    }
    for relative in ICON_FILES {
        add_source_file(
            repo_root,
            &manifest_dir.join("icons").join(relative),
            &mut entries,
        );
    }
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    let body = entries
        .into_iter()
        .map(|(relative, size, hash)| format!("{relative}\0{size}\0{hash}\n"))
        .collect::<String>();
    sha256_bytes(body.as_bytes())
}

#[cfg(not(test))]
fn collect_source_tree(
    repo_root: &Path,
    directory: &Path,
    entries: &mut Vec<(String, usize, String)>,
) {
    let metadata = fs::symlink_metadata(directory).expect("package_source_identity_invalid");
    assert!(
        metadata.is_dir() && !metadata.file_type().is_symlink(),
        "package_source_identity_invalid"
    );
    let mut children = fs::read_dir(directory)
        .expect("package_source_identity_invalid")
        .map(|entry| entry.expect("package_source_identity_invalid").path())
        .collect::<Vec<_>>();
    children.sort();
    for path in children {
        let metadata = fs::symlink_metadata(&path).expect("package_source_identity_invalid");
        assert!(
            !metadata.file_type().is_symlink(),
            "package_critical_path_rejected"
        );
        if metadata.is_dir() {
            collect_source_tree(repo_root, &path, entries);
        } else if metadata.is_file() {
            add_source_file(repo_root, &path, entries);
        } else {
            panic!("package_source_identity_invalid");
        }
    }
}

#[cfg(not(test))]
fn add_source_file(repo_root: &Path, path: &Path, entries: &mut Vec<(String, usize, String)>) {
    let metadata = fs::symlink_metadata(path).expect("package_source_identity_invalid");
    assert!(
        metadata.is_file() && !metadata.file_type().is_symlink(),
        "package_source_identity_invalid"
    );
    assert!(
        metadata.len() <= 4 * 1024 * 1024,
        "package_source_identity_invalid"
    );
    let body = fs::read(path).expect("package_source_identity_invalid");
    let relative = path
        .strip_prefix(repo_root)
        .expect("package_critical_path_rejected")
        .to_string_lossy()
        .replace('\\', "/");
    entries.push((relative, body.len(), sha256_bytes(&body)));
}

#[cfg(not(test))]
fn write_package_build_inputs(manifest_dir: &Path, body: &[u8]) {
    let output = manifest_dir.join("target/package-build-inputs.json");
    fs::create_dir_all(output.parent().expect("package build inputs parent"))
        .expect("create package build inputs parent");
    let staging = output.with_extension("json.tmp");
    fs::write(&staging, body).expect("write package build inputs staging");
    fs::rename(staging, output).expect("publish package build inputs");
}

fn verified_repository_file(repo_root: &Path, relative: &str) -> PathBuf {
    match optional_verified_repository_file(repo_root, relative) {
        Ok(Some(path)) => path,
        Ok(None) => panic!("asset_unreadable"),
        Err(code) => panic!("{code}"),
    }
}

fn optional_verified_repository_file(
    repo_root: &Path,
    relative: &str,
) -> Result<Option<PathBuf>, &'static str> {
    let relative_path = Path::new(relative);
    if relative_path.is_absolute()
        || relative_path
            .components()
            .any(|component| !matches!(component, std::path::Component::Normal(_)))
    {
        return Err("asset_path_invalid");
    }
    let mut current = repo_root.to_path_buf();
    let components = relative_path.components().collect::<Vec<_>>();
    if components.is_empty() {
        return Err("asset_path_invalid");
    }
    for (index, component) in components.iter().enumerate() {
        current.push(component.as_os_str());
        let metadata = match fs::symlink_metadata(&current) {
            Ok(metadata) => metadata,
            Err(_) => return Ok(None),
        };
        if metadata.file_type().is_symlink() {
            return Err("asset_symlink_rejected");
        }
        if index < components.len() - 1 && !metadata.is_dir() {
            return Ok(None);
        }
    }
    let metadata = match fs::symlink_metadata(&current) {
        Ok(metadata) => metadata,
        Err(_) => return Ok(None),
    };
    if !metadata.is_file() || metadata.len() > 4 * 1024 * 1024 {
        return Ok(None);
    }
    Ok(Some(current))
}

fn validate_object_keys(
    object: &serde_json::Map<String, serde_json::Value>,
    allowed: &[&str],
    required: &[&str],
) {
    if object.keys().any(|key| !allowed.contains(&key.as_str()))
        || required.iter().any(|key| !object.contains_key(*key))
    {
        panic!("asset_manifest_invalid");
    }
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

pub(crate) fn qualify_tool_identity_assets(repo_root: &Path) -> ToolIdentityQualification {
    use std::collections::HashSet;

    const MANIFEST_KEYS: &[&str] = &["schema_version", "assets"];
    const ASSET_KEYS: &[&str] = &[
        "asset_id",
        "canonical_tool_id",
        "aliases",
        "display_name",
        "sourceUrl",
        "repository_path",
        "bundled_relative_url",
        "source",
        "repository_sha256",
        "media_type",
        "dimensions",
        "derivation",
        "copyright_status",
        "license_status",
        "trademark_review_status",
        "allowed_usage_scope",
        "product_use_approval",
        "attribution_notice",
        "modification_constraint",
    ];
    const IDENTITY_KEYS: &[&str] = &[
        "asset_id",
        "canonical_tool_id",
        "aliases",
        "display_name",
        "repository_path",
        "bundled_relative_url",
        "attribution_notice",
    ];
    const CONTEXTS: &[&str] = &[
        "install_preview",
        "install_result",
        "install_selected_target",
        "local_result_badge",
        "local_selected_inspector",
        "local_tool_filter",
        "sot_target",
    ];

    let manifest_path = verified_repository_file(repo_root, TOOL_IDENTITY_MANIFEST);
    let manifest_bytes =
        fs::read(&manifest_path).unwrap_or_else(|_| panic!("asset_manifest_invalid"));
    let manifest_sha256 = sha256_bytes(&manifest_bytes);
    let manifest_text =
        String::from_utf8(manifest_bytes).unwrap_or_else(|_| panic!("asset_manifest_invalid"));
    let lowered = manifest_text.to_ascii_lowercase();
    if lowered.contains("/users/")
        || lowered.contains("file://")
        || lowered.contains("official")
        || lowered.contains("endorsement")
    {
        panic!("asset_vendor_claim_rejected");
    }
    let manifest: serde_json::Value =
        serde_json::from_str(&manifest_text).unwrap_or_else(|_| panic!("asset_manifest_invalid"));
    let manifest_object = manifest
        .as_object()
        .unwrap_or_else(|| panic!("asset_manifest_invalid"));
    validate_object_keys(manifest_object, MANIFEST_KEYS, MANIFEST_KEYS);
    if manifest
        .get("schema_version")
        .and_then(|value| value.as_u64())
        != Some(1)
    {
        panic!("asset_manifest_invalid");
    }
    let assets = manifest
        .get("assets")
        .and_then(|value| value.as_array())
        .filter(|assets| assets.len() == EXPECTED_TOOL_IDENTITIES.len())
        .unwrap_or_else(|| panic!("asset_manifest_invalid"));
    let expected_contexts = CONTEXTS.iter().copied().collect::<HashSet<_>>();
    let mut canonical_ids = HashSet::new();
    let mut aliases = HashSet::new();
    let mut qualified_assets = Vec::new();

    for asset in assets {
        let object = asset
            .as_object()
            .unwrap_or_else(|| panic!("asset_manifest_invalid"));
        validate_object_keys(object, ASSET_KEYS, IDENTITY_KEYS);
        let canonical_tool_id = asset
            .get("canonical_tool_id")
            .and_then(|value| value.as_str())
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| panic!("asset_manifest_invalid"));
        let expected = EXPECTED_TOOL_IDENTITIES
            .iter()
            .find(|candidate| candidate.canonical_tool_id == canonical_tool_id)
            .unwrap_or_else(|| panic!("asset_catalog_drift"));
        if !canonical_ids.insert(canonical_tool_id) {
            panic!("asset_alias_invalid");
        }
        let raw_aliases = asset
            .get("aliases")
            .and_then(|value| value.as_array())
            .unwrap_or_else(|| panic!("asset_alias_invalid"));
        let normalized_aliases = raw_aliases
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .filter(|alias| !alias.trim().is_empty())
                    .unwrap_or_else(|| panic!("asset_alias_invalid"))
                    .to_string()
            })
            .collect::<Vec<_>>();
        if normalized_aliases
            != expected
                .aliases
                .iter()
                .map(|alias| alias.to_string())
                .collect::<Vec<_>>()
            || normalized_aliases
                .iter()
                .any(|alias| !aliases.insert(alias.clone()))
        {
            panic!("asset_alias_invalid");
        }
        let relative = asset
            .get("repository_path")
            .and_then(|value| value.as_str())
            .unwrap_or_else(|| panic!("asset_path_invalid"));
        let bundled_url = asset
            .get("bundled_relative_url")
            .and_then(|value| value.as_str())
            .unwrap_or_else(|| panic!("asset_url_invalid"));
        if relative != expected.repository_path
            || bundled_url != expected.bundled_relative_url
            || bundled_url.contains("://")
            || bundled_url.starts_with("data:")
            || bundled_url.starts_with("file:")
        {
            panic!("asset_url_invalid");
        }
        if asset.get("asset_id").and_then(|value| value.as_str()) != Some(expected.asset_id)
            || asset.get("display_name").and_then(|value| value.as_str())
                != Some(expected.display_name)
            || asset.get("sourceUrl").and_then(|value| value.as_str()) != Some(expected.source_url)
        {
            panic!("asset_catalog_drift");
        }
        if !asset
            .get("attribution_notice")
            .is_some_and(serde_json::Value::is_null)
        {
            panic!("asset_vendor_claim_rejected");
        }

        let source = asset.get("source").and_then(|value| value.as_object());
        if let Some(source) = source {
            validate_object_keys(source, &["kind", "source_sha256"], &[]);
            if source
                .get("kind")
                .and_then(|value| value.as_str())
                .is_some_and(|kind| kind != "vendor_downloaded")
            {
                panic!("asset_source_invalid");
            }
        }
        if let Some(dimensions) = asset.get("dimensions").and_then(|value| value.as_object()) {
            validate_object_keys(dimensions, &["width", "height"], &[]);
        }
        if let Some(approval) = asset
            .get("product_use_approval")
            .and_then(|value| value.as_object())
        {
            validate_object_keys(approval, &["status", "authority", "date"], &[]);
        }
        let path = optional_verified_repository_file(repo_root, relative)
            .unwrap_or_else(|code| panic!("{code}"));

        let qualification = (|| -> Result<(), &'static str> {
            let path = path.as_ref().ok_or_else(|| {
                if [
                    "copyright_status",
                    "license_status",
                    "trademark_review_status",
                ]
                .iter()
                .any(|field| asset.get(field).and_then(|value| value.as_str()) == Some("not_asserted"))
                {
                    "asset_rights_not_asserted"
                } else {
                    "asset_unreadable"
                }
            })?;
            let source = source.ok_or("asset_source_invalid")?;
            if source.len() != 2
                || source.get("kind").and_then(|value| value.as_str()) != Some("vendor_downloaded")
            {
                return Err("asset_source_invalid");
            }
            let source_hash = source
                .get("source_sha256")
                .and_then(|value| value.as_str())
                .ok_or("asset_hash_invalid")?;
            let repository_hash = asset
                .get("repository_sha256")
                .and_then(|value| value.as_str())
                .ok_or("asset_hash_invalid")?;
            if !valid_sha256(source_hash) || !valid_sha256(repository_hash) {
                return Err("asset_hash_invalid");
            }
            let bytes = fs::read(path).map_err(|_| "asset_unreadable")?;
            if source_hash != expected.source_sha256
                || repository_hash != expected.repository_sha256
                || sha256_bytes(&bytes) != expected.repository_sha256
            {
                return Err("asset_hash_mismatch");
            }
            if asset.get("media_type").and_then(|value| value.as_str()) != Some("image/png")
                || asset.get("derivation").and_then(|value| value.as_str())
                    != Some(expected.derivation)
                || bytes.len() < 24
                || &bytes[..8] != b"\x89PNG\r\n\x1a\n"
                || &bytes[12..16] != b"IHDR"
            {
                return Err("asset_media_invalid");
            }
            let width = u32::from_be_bytes(bytes[16..20].try_into().expect("png width"));
            let height = u32::from_be_bytes(bytes[20..24].try_into().expect("png height"));
            let dimensions = asset
                .get("dimensions")
                .and_then(|value| value.as_object())
                .ok_or("asset_media_invalid")?;
            if dimensions.len() != 2
                || (width, height) != expected.dimensions
                || dimensions.get("width").and_then(|value| value.as_u64())
                    != Some(expected.dimensions.0.into())
                || dimensions.get("height").and_then(|value| value.as_u64())
                    != Some(expected.dimensions.1.into())
            {
                return Err("asset_media_invalid");
            }
            let approval = asset
                .get("product_use_approval")
                .and_then(|value| value.as_object())
                .ok_or("asset_approval_missing")?;
            if approval.len() != 3
                || approval.get("status").and_then(|value| value.as_str()) != Some("approved")
                || approval.get("authority").and_then(|value| value.as_str()) != Some("user")
                || approval
                    .get("date")
                    .and_then(|value| value.as_str())
                    .is_none_or(|date| date.trim().is_empty())
            {
                return Err("asset_approval_missing");
            }
            let mut rights_not_asserted = false;
            for field in [
                "copyright_status",
                "license_status",
                "trademark_review_status",
            ] {
                match asset.get(field).and_then(|value| value.as_str()) {
                    Some("not_asserted") => rights_not_asserted = true,
                    Some("denied" | "restricted" | "rejected") => {
                        return Err("asset_rights_restricted")
                    }
                    _ => return Err("asset_rights_state_invalid"),
                }
            }
            if rights_not_asserted {
                return Err("asset_rights_not_asserted");
            }
            let contexts = asset
                .get("allowed_usage_scope")
                .and_then(|value| value.as_array())
                .ok_or("asset_usage_scope_invalid")?;
            let actual_contexts = contexts
                .iter()
                .map(|value| value.as_str().ok_or("asset_usage_scope_invalid"))
                .collect::<Result<HashSet<_>, _>>()?;
            if actual_contexts != expected_contexts || contexts.len() != expected_contexts.len() {
                return Err("asset_usage_scope_invalid");
            }
            if asset
                .get("modification_constraint")
                .and_then(|value| value.as_str())
                != Some("aspect_ratio_containment_only")
            {
                return Err("asset_modification_invalid");
            }
            Ok(())
        })();
        let mode = match qualification {
            Ok(()) => ToolIdentityMode::Qualified,
            Err(code) => ToolIdentityMode::TextOnly(code),
        };
        qualified_assets.push(QualifiedToolIdentity {
            canonical_tool_id: canonical_tool_id.to_string(),
            aliases: normalized_aliases,
            display_name: expected.display_name.to_string(),
            repository_path: expected.repository_path.to_string(),
            bundled_relative_url: expected.bundled_relative_url.to_string(),
            expected_sha256: expected.repository_sha256.to_string(),
            mode,
        });
    }
    if canonical_ids.len() != EXPECTED_TOOL_IDENTITIES.len()
        || EXPECTED_TOOL_IDENTITIES
            .iter()
            .any(|expected| !canonical_ids.contains(expected.canonical_tool_id))
    {
        panic!("asset_catalog_drift");
    }
    ToolIdentityQualification {
        manifest_sha256,
        qualified_assets,
    }
}

pub(crate) fn generated_tool_identity_catalog_body(
    qualification: &ToolIdentityQualification,
) -> Vec<u8> {
    let entries = qualification
        .qualified_assets
        .iter()
        .map(|asset| {
            let (mode, src, reason) = match asset.mode {
                ToolIdentityMode::Qualified => {
                    ("qualified", Some(asset.bundled_relative_url.as_str()), None)
                }
                ToolIdentityMode::TextOnly(reason) => ("text", None, Some(reason)),
            };
            serde_json::json!({
                "aliases": asset.aliases,
                "canonicalToolId": asset.canonical_tool_id,
                "displayName": asset.display_name,
                "mode": mode,
                "reason": reason,
                "src": src,
            })
        })
        .collect::<Vec<_>>();
    format!(
        concat!(
            "// Generated from assets/tool-identities/manifest.json qualification.\n",
            "// Do not edit this catalog by hand.\n",
            "export const TOOL_IDENTITY_CATALOG = Object.freeze({});\n"
        ),
        serde_json::to_string_pretty(&entries).expect("serialize tool identity runtime catalog")
    )
    .into_bytes()
}

fn verify_tool_identity_manifest_unchanged(
    repo_root: &Path,
    qualification: &ToolIdentityQualification,
) {
    let path = verified_repository_file(repo_root, TOOL_IDENTITY_MANIFEST);
    let bytes = fs::read(path).unwrap_or_else(|_| panic!("asset_manifest_invalid"));
    if sha256_bytes(&bytes) != qualification.manifest_sha256 {
        panic!("asset_manifest_changed_during_build");
    }
}

fn write_generated_tool_identity_catalog(
    staging: &Path,
    qualification: &ToolIdentityQualification,
) {
    let path = staging.join(GENERATED_TOOL_IDENTITY_CATALOG);
    fs::write(&path, generated_tool_identity_catalog_body(qualification))
        .expect("write generated tool identity catalog");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644))
            .expect("set generated tool identity catalog permissions");
    }
}

pub(crate) fn stage_qualified_tool_identity_assets(
    repo_root: &Path,
    staging: &Path,
    qualification: &ToolIdentityQualification,
) {
    for asset in &qualification.qualified_assets {
        println!("cargo:rerun-if-changed=../{}", asset.repository_path);
        if asset.mode == ToolIdentityMode::Qualified {
            let destination = asset
                .bundled_relative_url
                .strip_prefix("./")
                .unwrap_or_else(|| panic!("asset_url_invalid"));
            copy_regular_file_to(repo_root, &asset.repository_path, destination, staging);
        }
    }
    write_generated_tool_identity_catalog(staging, qualification);
    verify_staged_tool_identity_assets(staging, qualification);
}

#[cfg(not(test))]
fn prepare_frontend_bundle(
    qualification: &ToolIdentityQualification,
    reuse_prebuilt: bool,
) -> FrontendBundleIdentity {
    let manifest_dir = PathBuf::from(
        std::env::var_os("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR is required"),
    );
    let repo_root = manifest_dir
        .parent()
        .expect("src-tauri must be below the repository root");
    let source_root = repo_root.join("src-frontend");
    let output_parent = manifest_dir.join("target");
    fs::create_dir_all(&output_parent).expect("create frontend target parent");
    let final_dir = output_parent.join("frontend-dist");
    if reuse_prebuilt {
        verify_tool_identity_manifest_unchanged(repo_root, qualification);
        return verify_frontend_bundle_dist(repo_root, &final_dir, qualification);
    }
    let staging = output_parent.join(format!(".frontend-bundle-{}", std::process::id()));
    if staging.exists() {
        fs::remove_dir_all(&staging).expect("remove stale frontend staging");
    }
    verify_tool_identity_manifest_unchanged(repo_root, qualification);
    let source_catalog = fs::read(source_root.join(GENERATED_TOOL_IDENTITY_CATALOG))
        .expect("frontend_tool_identity_catalog_missing");
    if source_catalog != generated_tool_identity_catalog_body(qualification) {
        panic!("frontend_tool_identity_catalog_drift");
    }

    println!("cargo:rerun-if-changed=../src-frontend");
    println!("cargo:rerun-if-changed=../{TOOL_IDENTITY_MANIFEST}");
    let status = Command::new("npm")
        .args(["run", "--silent", "build"])
        .current_dir(&source_root)
        .env("HARNESS_FRONTEND_DIST", &staging)
        .status()
        .unwrap_or_else(|_| panic!("frontend_build_tool_unavailable_run_npm_ci"));
    if !status.success() {
        panic!("frontend_bundle_build_failed");
    }

    let identity = verify_frontend_bundle_dist(repo_root, &staging, qualification);
    verify_tool_identity_manifest_unchanged(repo_root, qualification);

    if final_dir.exists() {
        fs::remove_dir_all(&final_dir).expect("replace generated frontend dist");
    }
    fs::rename(&staging, &final_dir).expect("publish generated frontend dist");
    verify_frontend_bundle_dist(repo_root, &final_dir, qualification);
    identity
}

#[cfg(not(test))]
fn verify_frontend_bundle_dist(
    repo_root: &Path,
    dist_root: &Path,
    qualification: &ToolIdentityQualification,
) -> FrontendBundleIdentity {
    let manifest_path = dist_root.join("bundle-manifest.json");
    let manifest_bytes = read_bundle_regular_file(&manifest_path);
    let manifest: serde_json::Value = serde_json::from_slice(&manifest_bytes)
        .unwrap_or_else(|_| panic!("frontend_bundle_manifest_invalid"));
    let manifest = manifest
        .as_object()
        .unwrap_or_else(|| panic!("frontend_bundle_manifest_invalid"));
    if manifest
        .get("schema_version")
        .and_then(|value| value.as_u64())
        != Some(1)
        || manifest.get("entrypoint").and_then(|value| value.as_str())
            != Some("appearance/bootstrap.js")
        || manifest.get("source_map").and_then(|value| value.as_bool()) != Some(false)
    {
        panic!("frontend_bundle_manifest_invalid");
    }

    let source_root = repo_root.join("src-frontend");
    let package_json = read_bundle_regular_file(&source_root.join("package.json"));
    let package_lock = read_bundle_regular_file(&source_root.join("package-lock.json"));
    let build_script = read_bundle_regular_file(&source_root.join("build.mjs"));
    let lockfile = verified_bundle_reference(manifest, "lockfile", "package-lock.json");
    if lockfile.1 != sha256_bytes(&package_lock) {
        panic!("frontend_bundle_lockfile_hash_mismatch");
    }
    let output = verified_bundle_reference(manifest, "output", "assets/app.bundle.js");
    let license_inventory =
        verified_bundle_reference(manifest, "license_inventory", "license-inventory.json");
    let output_bytes = read_bundle_regular_file(&dist_root.join(&output.0));
    let license_bytes = read_bundle_regular_file(&dist_root.join(&license_inventory.0));
    if sha256_bytes(&output_bytes) != output.1 {
        panic!("frontend_bundle_output_hash_mismatch");
    }
    if sha256_bytes(&license_bytes) != license_inventory.1 {
        panic!("frontend_bundle_license_hash_mismatch");
    }

    let files = manifest
        .get("files")
        .and_then(|value| value.as_array())
        .unwrap_or_else(|| panic!("frontend_bundle_inventory_invalid"));
    let mut expected = BTreeSet::new();
    for value in files {
        let entry = value
            .as_object()
            .unwrap_or_else(|| panic!("frontend_bundle_inventory_invalid"));
        let path = safe_bundle_relative(
            entry
                .get("path")
                .and_then(|value| value.as_str())
                .unwrap_or_else(|| panic!("frontend_bundle_inventory_invalid")),
        );
        let expected_hash = entry
            .get("sha256")
            .and_then(|value| value.as_str())
            .filter(|value| valid_sha256(value))
            .unwrap_or_else(|| panic!("frontend_bundle_inventory_invalid"));
        if !expected.insert(path.clone()) {
            panic!("frontend_bundle_inventory_invalid");
        }
        if sha256_bytes(&read_bundle_regular_file(&dist_root.join(&path))) != expected_hash {
            panic!("frontend_bundle_inventory_hash_mismatch");
        }
    }
    let mut actual = BTreeSet::new();
    collect_bundle_files(dist_root, dist_root, &mut actual);
    actual.remove(Path::new("bundle-manifest.json"));
    if actual != expected {
        panic!("frontend_bundle_inventory_mismatch");
    }

    for asset in &qualification.qualified_assets {
        let relative = safe_bundle_relative(
            asset
                .bundled_relative_url
                .strip_prefix("./")
                .unwrap_or_else(|| panic!("asset_url_invalid")),
        );
        match asset.mode {
            ToolIdentityMode::Qualified => {
                if sha256_bytes(&read_bundle_regular_file(&dist_root.join(relative)))
                    != asset.expected_sha256
                {
                    panic!("asset_staging_hash_mismatch");
                }
            }
            ToolIdentityMode::TextOnly(_) => {
                if dist_root.join(relative).exists() {
                    panic!("asset_staging_hash_mismatch");
                }
            }
        }
    }

    FrontendBundleIdentity {
        package_json_sha256: sha256_bytes(&package_json),
        package_lock_sha256: sha256_bytes(&package_lock),
        build_script_sha256: sha256_bytes(&build_script),
        bundle_manifest_sha256: sha256_bytes(&manifest_bytes),
        bundle_output_sha256: output.1,
        license_inventory_sha256: license_inventory.1,
    }
}

#[cfg(not(test))]
fn verified_bundle_reference(
    manifest: &serde_json::Map<String, serde_json::Value>,
    field: &str,
    expected_path: &str,
) -> (PathBuf, String) {
    let value = manifest
        .get(field)
        .and_then(|value| value.as_object())
        .unwrap_or_else(|| panic!("frontend_bundle_manifest_invalid"));
    let path = safe_bundle_relative(
        value
            .get("path")
            .and_then(|value| value.as_str())
            .unwrap_or_else(|| panic!("frontend_bundle_manifest_invalid")),
    );
    if path != Path::new(expected_path) {
        panic!("frontend_bundle_manifest_invalid");
    }
    let hash = value
        .get("sha256")
        .and_then(|value| value.as_str())
        .filter(|value| valid_sha256(value))
        .unwrap_or_else(|| panic!("frontend_bundle_manifest_invalid"));
    (path, hash.to_string())
}

#[cfg(not(test))]
fn safe_bundle_relative(value: &str) -> PathBuf {
    let path = Path::new(value);
    if value.is_empty()
        || value.contains('\\')
        || value.contains(':')
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, std::path::Component::Normal(_)))
    {
        panic!("frontend_bundle_path_invalid");
    }
    path.to_path_buf()
}

#[cfg(not(test))]
fn read_bundle_regular_file(path: &Path) -> Vec<u8> {
    let metadata =
        fs::symlink_metadata(path).unwrap_or_else(|_| panic!("frontend_bundle_member_unreadable"));
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > 16 * 1024 * 1024
    {
        panic!("frontend_bundle_member_invalid");
    }
    fs::read(path).unwrap_or_else(|_| panic!("frontend_bundle_member_unreadable"))
}

#[cfg(not(test))]
fn collect_bundle_files(root: &Path, current: &Path, output: &mut BTreeSet<PathBuf>) {
    let metadata = fs::symlink_metadata(current)
        .unwrap_or_else(|_| panic!("frontend_bundle_inventory_invalid"));
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        panic!("frontend_bundle_inventory_invalid");
    }
    let mut entries = fs::read_dir(current)
        .unwrap_or_else(|_| panic!("frontend_bundle_inventory_invalid"))
        .map(|entry| {
            entry
                .unwrap_or_else(|_| panic!("frontend_bundle_inventory_invalid"))
                .path()
        })
        .collect::<Vec<_>>();
    entries.sort();
    for path in entries {
        let metadata = fs::symlink_metadata(&path)
            .unwrap_or_else(|_| panic!("frontend_bundle_inventory_invalid"));
        if metadata.file_type().is_symlink() {
            panic!("frontend_bundle_inventory_invalid");
        }
        if metadata.is_dir() {
            collect_bundle_files(root, &path, output);
        } else if metadata.is_file() {
            let relative = path
                .strip_prefix(root)
                .unwrap_or_else(|_| panic!("frontend_bundle_inventory_invalid"))
                .to_path_buf();
            if !output.insert(relative) {
                panic!("frontend_bundle_inventory_invalid");
            }
        } else {
            panic!("frontend_bundle_inventory_invalid");
        }
    }
}

fn verify_staged_tool_identity_assets(staging: &Path, qualification: &ToolIdentityQualification) {
    let catalog_path = staging.join(GENERATED_TOOL_IDENTITY_CATALOG);
    let catalog_metadata = fs::symlink_metadata(&catalog_path)
        .unwrap_or_else(|_| panic!("asset_staging_hash_mismatch"));
    if catalog_metadata.file_type().is_symlink() || !catalog_metadata.is_file() {
        panic!("asset_staging_hash_mismatch");
    }
    let catalog_bytes =
        fs::read(&catalog_path).unwrap_or_else(|_| panic!("asset_staging_hash_mismatch"));
    if catalog_bytes != generated_tool_identity_catalog_body(qualification) {
        panic!("asset_staging_hash_mismatch");
    }
    for asset in &qualification.qualified_assets {
        let bundled_path = asset
            .bundled_relative_url
            .strip_prefix("./")
            .unwrap_or_else(|| panic!("asset_staging_hash_mismatch"));
        let path = staging.join(bundled_path);
        match asset.mode {
            ToolIdentityMode::Qualified => {
                let metadata = fs::symlink_metadata(&path)
                    .unwrap_or_else(|_| panic!("asset_staging_hash_mismatch"));
                if metadata.file_type().is_symlink() || !metadata.is_file() {
                    panic!("asset_staging_hash_mismatch");
                }
                let bytes =
                    fs::read(path).unwrap_or_else(|_| panic!("asset_staging_hash_mismatch"));
                if sha256_bytes(&bytes) != asset.expected_sha256 {
                    panic!("asset_staging_hash_mismatch");
                }
            }
            ToolIdentityMode::TextOnly(_) if path.exists() => {
                panic!("asset_staging_hash_mismatch");
            }
            ToolIdentityMode::TextOnly(_) => {}
        }
    }
}

fn copy_regular_file(source_root: &Path, relative: &str, staging: &Path) {
    copy_regular_file_to(source_root, relative, relative, staging);
}

fn copy_regular_file_to(
    source_root: &Path,
    source_relative: &str,
    destination_relative: &str,
    staging: &Path,
) {
    let relative_path = Path::new(source_relative);
    let destination_path = Path::new(destination_relative);
    assert!(
        !relative_path.is_absolute()
            && relative_path
                .components()
                .all(|component| matches!(component, std::path::Component::Normal(_)))
            && !destination_path.is_absolute()
            && destination_path
                .components()
                .all(|component| matches!(component, std::path::Component::Normal(_))),
        "frontend allowlist path must be a safe relative path"
    );
    let mut source = source_root.to_path_buf();
    for component in relative_path.components() {
        source.push(component.as_os_str());
        let metadata = fs::symlink_metadata(&source).expect("frontend allowlist member must exist");
        assert!(
            !metadata.file_type().is_symlink(),
            "frontend symlink rejected"
        );
    }
    let metadata = fs::symlink_metadata(&source).expect("frontend allowlist file metadata");
    assert!(
        metadata.is_file(),
        "frontend allowlist member must be a file"
    );
    assert!(metadata.len() <= 4 * 1024 * 1024, "frontend file too large");

    let destination = staging.join(destination_path);
    fs::create_dir_all(destination.parent().expect("frontend destination parent"))
        .expect("create frontend destination parent");
    fs::copy(&source, &destination).expect("copy frontend allowlist member");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&destination, fs::Permissions::from_mode(0o644))
            .expect("set frontend file permissions");
    }
}
