use std::collections::BTreeSet;
use std::path::PathBuf;

use serde_json::{json, Value};
use sha2::Digest;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri must live below repo root")
        .to_path_buf()
}

fn lock_integrity(resolution: &Value) -> Value {
    if let Some(integrity) = resolution["integrity"].as_str() {
        assert!(integrity.starts_with("sha512-"));
        return Value::String(integrity.to_string());
    }

    let resolved = resolution["resolved"]
        .as_str()
        .expect("git dependency must have a resolved URL");
    let (repository, commit) = resolved
        .rsplit_once('#')
        .expect("git dependency must pin a commit");
    assert!(
        repository.starts_with("git+https://github.com/"),
        "git dependency must use GitHub HTTPS"
    );
    assert!(repository.ends_with(".git"));
    assert_eq!(commit.len(), 40, "git dependency must use a full commit");
    assert!(commit.bytes().all(|byte| byte.is_ascii_hexdigit()));
    Value::String(format!("git-commit:{}", commit.to_ascii_lowercase()))
}

#[test]
fn runtime_lock_pins_python_wheels_entrypoint_and_target_architecture() {
    let root = repo_root();
    let lock: Value = serde_json::from_slice(
        &std::fs::read(root.join("src-tauri/install-runtime.lock.json")).unwrap(),
    )
    .unwrap();

    assert_eq!(lock["schema_version"], 1);
    assert_eq!(lock["target"], "aarch64-apple-darwin");
    assert_eq!(lock["python"]["version"], "3.11.15");
    assert_eq!(lock["python"]["sha256"].as_str().unwrap().len(), 64);
    assert!(lock["python"]["url"].as_str().unwrap().starts_with(
        "https://github.com/astral-sh/python-build-standalone/releases/download/20260510/"
    ));

    let wheels = lock["wheels"].as_array().unwrap();
    for required in [
        "PyYAML",
        "jsonschema",
        "attrs",
        "jsonschema-specifications",
        "referencing",
        "rpds-py",
        "typing-extensions",
    ] {
        assert!(wheels.iter().any(|wheel| wheel["name"] == required));
    }
    assert!(wheels.iter().all(|wheel| {
        wheel["url"]
            .as_str()
            .is_some_and(|url| url.starts_with("https://files.pythonhosted.org/"))
            && wheel["sha256"]
                .as_str()
                .is_some_and(|hash| hash.len() == 64)
    }));

    let entrypoint = root.join(lock["entrypoint"]["path"].as_str().unwrap());
    let bytes = std::fs::read(entrypoint).unwrap();
    assert_eq!(
        format!("{:x}", sha2::Sha256::digest(bytes)),
        lock["entrypoint"]["sha256"]
    );
}

#[test]
fn package_prepare_is_lock_only_and_bundle_owns_the_generated_runtime() {
    let root = repo_root();
    let prepare =
        std::fs::read_to_string(root.join("scripts/package/prepare_install_runtime.py")).unwrap();
    let config = std::fs::read_to_string(root.join("src-tauri/tauri.conf.json")).unwrap();
    let ignore = std::fs::read_to_string(root.join(".gitignore")).unwrap();
    let pinned_manifest_hash =
        std::fs::read_to_string(root.join("src-tauri/install-runtime.manifest.sha256")).unwrap();

    for required in [
        "install-runtime.lock.json",
        "runtime-manifest.json",
        "install-runtime.manifest.sha256",
        "hashlib.sha256",
        "urllib.request",
        "tarfile",
        "zipfile",
        "/usr/bin/codesign",
        "runtime_architecture_probe_failed",
    ] {
        assert!(
            prepare.contains(required),
            "missing prepare gate: {required}"
        );
    }
    assert!(prepare.contains("_validated_https_url"));
    assert!(prepare.contains("parsed.scheme != \"https\""));
    assert!(!prepare.contains("pip install"));
    assert!(!prepare.contains("uv pip"));
    assert!(config.contains("target/install-runtime/aarch64-apple-darwin"));
    assert!(config.contains("scripts/package/prepare_install_runtime.py"));
    assert!(!config.contains("\"beforeBuildCommand\": \"echo build\""));
    assert!(ignore
        .lines()
        .any(|line| line == "src-tauri/target/install-runtime/"));
    assert_eq!(pinned_manifest_hash.trim().len(), 64);
    assert!(pinned_manifest_hash
        .trim()
        .bytes()
        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)));
}

#[test]
fn generated_frontend_dist_is_one_verified_manifest_owned_bundle() {
    let root = repo_root();
    let staged = root.join("src-tauri/target/frontend-dist");
    let build_authority = std::fs::read_to_string(root.join("src-tauri/build.rs")).unwrap();
    let package_authority =
        std::fs::read_to_string(root.join("scripts/package/build_verified_macos_app.py")).unwrap();
    let config: Value =
        serde_json::from_slice(&std::fs::read(root.join("src-tauri/tauri.conf.json")).unwrap())
            .unwrap();
    for (name, authority) in [
        ("src-tauri/build.rs", build_authority.as_str()),
        (
            "scripts/package/build_verified_macos_app.py",
            package_authority.as_str(),
        ),
    ] {
        assert!(
            !authority.contains("FRONTEND_FILES"),
            "{name} must not restore the raw JavaScript copy allowlist"
        );
        for required in [
            "src-frontend/package.json",
            "src-frontend/package-lock.json",
            "src-frontend/build.mjs",
            "bundle-manifest.json",
        ] {
            assert!(
                authority.contains(required),
                "{name} must bind {required} into the verified package identity"
            );
        }
    }

    assert_eq!(config["build"]["frontendDist"], "target/frontend-dist");
    let manifest_path = staged.join("bundle-manifest.json");
    let manifest: Value = serde_json::from_slice(&std::fs::read(&manifest_path).unwrap()).unwrap();
    assert_eq!(manifest["schema_version"], 1);
    assert_eq!(manifest["entrypoint"], "appearance/bootstrap.js");
    assert_eq!(manifest["source_map"], false);
    assert_eq!(manifest["lockfile"]["path"], "package-lock.json");
    assert_eq!(
        manifest["lockfile"]["sha256"],
        sha256(&root.join("src-frontend/package-lock.json"))
    );
    assert_eq!(manifest["output"]["path"], "assets/app.bundle.js");
    assert_eq!(
        manifest["license_inventory"]["path"],
        "license-inventory.json"
    );
    let frontend_lock: Value = serde_json::from_slice(
        &std::fs::read(root.join("src-frontend/package-lock.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(frontend_lock["lockfileVersion"], 3);
    let mut expected_licenses = frontend_lock["packages"]
        .as_object()
        .expect("lock packages")
        .iter()
        .filter(|(path, _)| !path.is_empty())
        .map(|(path, resolution)| {
            let name = path
                .rsplit("node_modules/")
                .next()
                .filter(|name| !name.is_empty())
                .expect("lock package name");
            json!({
                "integrity": lock_integrity(resolution),
                "license": resolution["license"],
                "name": name,
                "path": path,
                "usage": if resolution["dev"] == true { "build" } else { "runtime" },
                "version": resolution["version"],
            })
        })
        .collect::<Vec<_>>();
    expected_licenses.sort_by(|left, right| {
        (
            left["name"].as_str(),
            left["version"].as_str(),
            left["path"].as_str(),
        )
            .cmp(&(
                right["name"].as_str(),
                right["version"].as_str(),
                right["path"].as_str(),
            ))
    });
    assert_eq!(
        manifest["licenses"],
        Value::Array(expected_licenses),
        "bundle license inventory must equal the full lockfile closure"
    );
    let license_inventory: Value =
        serde_json::from_slice(&std::fs::read(staged.join("license-inventory.json")).unwrap())
            .unwrap();
    assert_eq!(license_inventory["schema_version"], 1);
    assert_eq!(license_inventory["packages"], manifest["licenses"]);

    let files = manifest["files"]
        .as_array()
        .expect("bundle manifest files must be an array");
    let mut expected = BTreeSet::new();
    for entry in files {
        let relative = entry["path"]
            .as_str()
            .expect("bundle inventory path must be text");
        assert_safe_bundle_relative(relative);
        assert!(expected.insert(relative.to_string()));
        let expected_hash = entry["sha256"]
            .as_str()
            .expect("bundle inventory hash must be text");
        assert_valid_sha256(expected_hash);
        assert_eq!(
            sha256(&staged.join(relative)),
            expected_hash,
            "generated bundle member diverged from manifest: {relative}"
        );
    }

    let mut actual = regular_files(&staged);
    assert!(actual.remove("bundle-manifest.json"));
    assert_eq!(actual, expected);
    assert!(actual.iter().all(|path| !path.ends_with(".map")));
    assert!(actual.iter().all(|path| !path.contains("node_modules")));
    assert!(actual
        .iter()
        .all(|path| !path.to_ascii_lowercase().contains("esbuild")));
    assert_eq!(
        actual
            .iter()
            .filter(|path| path.ends_with(".js"))
            .map(String::as_str)
            .collect::<Vec<_>>(),
        vec!["assets/app.bundle.js"]
    );
    assert_eq!(
        manifest["output"]["sha256"],
        sha256(&staged.join("assets/app.bundle.js"))
    );
    assert_eq!(
        manifest["license_inventory"]["sha256"],
        sha256(&staged.join("license-inventory.json"))
    );

    let index = std::fs::read_to_string(staged.join("index.html")).unwrap();
    assert!(index.contains("<script type=\"module\" src=\"./assets/app.bundle.js\"></script>"));
    assert!(!index.contains("src=\"http://"));
    assert!(!index.contains("src=\"https://"));
    assert!(!index.contains("href=\"http://"));
    assert!(!index.contains("href=\"https://"));
}

fn assert_safe_bundle_relative(relative: &str) {
    assert!(!relative.is_empty());
    assert!(!relative.starts_with('/'));
    assert!(!relative.contains("../"));
    assert!(!relative.contains("://"));
}

fn assert_valid_sha256(value: &str) {
    assert_eq!(value.len(), 64);
    assert!(value
        .bytes()
        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)));
}

fn regular_files(root: &std::path::Path) -> BTreeSet<String> {
    fn collect(root: &std::path::Path, current: &std::path::Path, output: &mut Vec<String>) {
        for entry in std::fs::read_dir(current).unwrap() {
            let path = entry.unwrap().path();
            let metadata = std::fs::symlink_metadata(&path).unwrap();
            assert!(!metadata.file_type().is_symlink());
            if metadata.is_dir() {
                collect(root, &path, output);
            } else {
                assert!(metadata.is_file());
                output.push(
                    path.strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .to_string(),
                );
            }
        }
    }

    let mut paths = Vec::new();
    collect(root, root, &mut paths);
    paths.into_iter().collect()
}

fn sha256(path: &std::path::Path) -> String {
    format!("{:x}", sha2::Sha256::digest(std::fs::read(path).unwrap()))
}
