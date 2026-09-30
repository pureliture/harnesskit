use std::fs;
use std::io::ErrorKind;
use std::path::Path;

use sha2::{Digest, Sha256};

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct VerifiedRelease<'a> {
    pub(crate) build_id: &'a str,
    pub(crate) build_inputs_sha256: &'a str,
}

pub(crate) fn load_verified_runtime_manifest(
    manifest_path: &Path,
    pinned_hash_path: &Path,
    release: bool,
) -> Result<String, &'static str> {
    let manifest = match fs::read(manifest_path) {
        Ok(manifest) => manifest,
        Err(error) if error.kind() == ErrorKind::NotFound && !release => {
            return Ok("{}".to_string());
        }
        Err(error) if error.kind() == ErrorKind::NotFound => {
            return Err("install_runtime_manifest_missing");
        }
        Err(_) => return Err("install_runtime_manifest_unreadable"),
    };
    let pinned =
        fs::read_to_string(pinned_hash_path).map_err(|_| "install_runtime_manifest_pin_missing")?;
    let pinned = pinned.trim();
    if pinned.len() != 64
        || !pinned
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("install_runtime_manifest_pin_invalid");
    }
    if sha256_bytes(&manifest) != pinned {
        return Err("install_runtime_manifest_hash_mismatch");
    }
    String::from_utf8(manifest).map_err(|_| "install_runtime_manifest_invalid_utf8")
}

pub(crate) fn verify_release_marker<'a>(
    release: bool,
    marker: Option<&str>,
    build_id: Option<&'a str>,
    build_inputs_sha256: Option<&'a str>,
) -> Result<Option<VerifiedRelease<'a>>, &'static str> {
    if !release {
        return Ok(None);
    }
    if marker != Some("1") {
        return Err("verified_package_wrapper_required");
    }
    let build_id = build_id.ok_or("verified_package_build_id_invalid")?;
    if build_id.len() != 32
        || !build_id
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("verified_package_build_id_invalid");
    }
    let build_inputs_sha256 = build_inputs_sha256
        .filter(|value| is_lower_sha256(value))
        .ok_or("verified_package_build_inputs_hash_invalid")?;
    Ok(Some(VerifiedRelease {
        build_id,
        build_inputs_sha256,
    }))
}

pub(crate) fn load_verified_package_request(
    path: &Path,
    expected_sha256: &str,
) -> Result<Vec<u8>, &'static str> {
    if !is_lower_sha256(expected_sha256) {
        return Err("verified_package_build_inputs_hash_invalid");
    }
    let body = fs::read(path).map_err(|_| "verified_package_build_request_missing")?;
    if sha256_bytes(&body) != expected_sha256 {
        return Err("verified_package_build_inputs_hash_mismatch");
    }
    Ok(body)
}

fn is_lower_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub(crate) fn sha256_bytes(body: &[u8]) -> String {
    format!("{:x}", Sha256::digest(body))
}
