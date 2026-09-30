use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const DEFAULT_HARNESSKIT_REPO_URL: &str = "https://github.com/pureliture/harnesskit.git";
const APP_OWNED_CHECKOUT_DIR: &str = "harnesskit";
static CLONE_COUNTER: AtomicU64 = AtomicU64::new(0);

const REGISTRY_PATH: &str = "components/registry.yml";
const BUILD_SCRIPT_PATH: &str = "scripts/adapters/build.py";
const INSTALL_PLAN_SCRIPT_PATH: &str = "scripts/install/plan.py";
const INSTALL_APPLY_SCRIPT_PATH: &str = "scripts/install/apply.py";
const INSTALL_VERIFY_SCRIPT_PATH: &str = "scripts/install/verify.py";

#[derive(Debug, Clone)]
pub struct RegisteredCheckout {
    root: PathBuf,
}

pub struct CheckoutCloner;

impl CheckoutCloner {
    pub fn clone_default(app_data_dir: &Path) -> Result<RegisteredCheckout, String> {
        Self::clone_from(DEFAULT_HARNESSKIT_REPO_URL, app_data_dir)
    }

    pub fn clone_from(source: &str, app_data_dir: &Path) -> Result<RegisteredCheckout, String> {
        if source.trim().is_empty() {
            return Err("HarnessKit clone source is invalid".to_string());
        }
        fs::create_dir_all(app_data_dir)
            .map_err(|_| "App-owned checkout directory is unavailable".to_string())?;
        let app_data_dir = fs::canonicalize(app_data_dir)
            .map_err(|_| "App-owned checkout directory is unavailable".to_string())?;
        let destination = app_data_dir.join(APP_OWNED_CHECKOUT_DIR);

        match fs::symlink_metadata(&destination) {
            Ok(_) => return existing_app_owned_checkout(&destination, source),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err("App-owned clone destination is unavailable".to_string()),
        }

        let temporary = unique_clone_path(&app_data_dir)?;
        let output = Command::new("git")
            .args(["clone", "--quiet", "--", source])
            .arg(&temporary)
            .output()
            .map_err(|_| "Public HarnessKit clone could not start".to_string())?;
        if !output.status.success() {
            cleanup_clone_temp(&temporary);
            return Err("Public HarnessKit clone failed".to_string());
        }

        let temporary_checkout = RegisteredCheckout::open(&temporary)
            .map_err(|_| "Cloned HarnessKit checkout is invalid".to_string());
        let temporary_checkout = match temporary_checkout {
            Ok(checkout)
                if checkout.root().starts_with(&app_data_dir)
                    && origin_url(&checkout).is_ok_and(|origin| origin == source) =>
            {
                checkout
            }
            _ => {
                cleanup_clone_temp(&temporary);
                return Err("Cloned HarnessKit checkout is invalid".to_string());
            }
        };
        drop(temporary_checkout);

        match fs::rename(&temporary, &destination) {
            Ok(()) => Ok(RegisteredCheckout { root: destination }),
            Err(_) => {
                cleanup_clone_temp(&temporary);
                existing_app_owned_checkout(&destination, source)
                    .map_err(|_| "Public HarnessKit clone could not be published".to_string())
            }
        }
    }
}

fn existing_app_owned_checkout(
    destination: &Path,
    expected_origin: &str,
) -> Result<RegisteredCheckout, String> {
    let metadata = fs::symlink_metadata(destination)
        .map_err(|_| "App-owned clone destination already exists".to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err("App-owned clone destination already exists".to_string());
    }
    let checkout = RegisteredCheckout::open(destination)
        .map_err(|_| "App-owned clone destination already exists".to_string())?;
    if checkout.root() != destination || origin_url(&checkout)? != expected_origin {
        return Err("App-owned clone destination already exists".to_string());
    }
    Ok(checkout)
}

fn unique_clone_path(app_data_dir: &Path) -> Result<PathBuf, String> {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "App-owned clone staging is unavailable".to_string())?
        .as_nanos();
    for _ in 0..128 {
        let counter = CLONE_COUNTER.fetch_add(1, Ordering::Relaxed);
        let candidate = app_data_dir.join(format!(
            ".harnesskit-clone-{}-{nonce}-{counter}",
            std::process::id()
        ));
        if fs::symlink_metadata(&candidate).is_err() {
            return Ok(candidate);
        }
    }
    Err("App-owned clone staging is unavailable".to_string())
}

fn cleanup_clone_temp(path: &Path) {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return;
    };
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        let _ = fs::remove_file(path);
    } else {
        let _ = fs::remove_dir_all(path);
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegisteredCheckoutInfo {
    pub checkout_id: String,
    pub canonical_path: PathBuf,
}

#[derive(Debug, Default)]
pub struct CheckoutRegistry {
    checkouts: BTreeMap<String, RegisteredCheckout>,
}

impl CheckoutRegistry {
    pub fn register(&mut self, path: impl AsRef<Path>) -> Result<RegisteredCheckoutInfo, String> {
        let checkout = RegisteredCheckout::open(path)?;
        let canonical_path = checkout.root().to_path_buf();
        let checkout_id = checkout_id(&canonical_path);

        self.checkouts
            .entry(checkout_id.clone())
            .or_insert(checkout);

        Ok(RegisteredCheckoutInfo {
            checkout_id,
            canonical_path,
        })
    }

    pub fn resolve(&self, checkout_id: &str) -> Result<&RegisteredCheckout, String> {
        self.checkouts
            .get(checkout_id)
            .ok_or_else(|| "Registered HarnessKit checkout is unavailable".to_string())
    }

    pub fn resolve_cloned(&self, checkout_id: &str) -> Result<RegisteredCheckout, String> {
        self.resolve(checkout_id).cloned()
    }
}

impl RegisteredCheckout {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, String> {
        let root = fs::canonicalize(path.as_ref())
            .map_err(|_| "HarnessKit checkout root is missing or inaccessible".to_string())?;
        if !root.is_dir() {
            return Err("HarnessKit checkout root is not a directory".to_string());
        }

        verify_git_worktree_root(&root)?;

        let checkout = Self { root };
        checkout.resolve_required_file(REGISTRY_PATH, "component registry")?;
        Ok(checkout)
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub(crate) fn build_script(&self) -> Result<PathBuf, String> {
        self.resolve_required_file(BUILD_SCRIPT_PATH, "adapter build script")
    }

    pub(crate) fn install_plan_script(&self) -> Result<PathBuf, String> {
        self.resolve_required_file(INSTALL_PLAN_SCRIPT_PATH, "install plan script")
    }

    pub(crate) fn install_apply_script(&self) -> Result<PathBuf, String> {
        self.resolve_required_file(INSTALL_APPLY_SCRIPT_PATH, "install apply script")
    }

    pub(crate) fn install_verify_script(&self) -> Result<PathBuf, String> {
        self.resolve_required_file(INSTALL_VERIFY_SCRIPT_PATH, "install verify script")
    }

    fn resolve_required_file(&self, relative_path: &str, label: &str) -> Result<PathBuf, String> {
        let resolved = fs::canonicalize(self.root.join(relative_path))
            .map_err(|_| format!("HarnessKit {label} is missing or inaccessible"))?;

        if !resolved.starts_with(&self.root) {
            return Err(format!("HarnessKit {label} resolves outside checkout"));
        }
        if !resolved.is_file() {
            return Err(format!("HarnessKit {label} is not a file"));
        }

        Ok(resolved)
    }
}

fn verify_git_worktree_root(root: &Path) -> Result<(), String> {
    let output = Command::new("git")
        .current_dir(root)
        .args(["rev-parse", "--show-toplevel"])
        .output()
        .map_err(|_| "Registered checkout is not a local Git repository".to_string())?;
    if !output.status.success() {
        return Err("Registered checkout is not a local Git repository".to_string());
    }
    let top_level = String::from_utf8(output.stdout)
        .map_err(|_| "Registered checkout is not a local Git repository".to_string())?;
    let top_level = fs::canonicalize(top_level.trim())
        .map_err(|_| "Registered checkout is not a local Git repository".to_string())?;
    if top_level != root {
        return Err("Registered checkout is not a local Git repository".to_string());
    }
    Ok(())
}

fn checkout_id(canonical_path: &Path) -> String {
    let digest = Sha256::digest(canonical_path.to_string_lossy().as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn origin_url(checkout: &RegisteredCheckout) -> Result<String, String> {
    let output = Command::new("git")
        .current_dir(checkout.root())
        .args(["config", "--get", "remote.origin.url"])
        .output()
        .map_err(|_| "App-owned checkout origin is unavailable".to_string())?;
    if !output.status.success() {
        return Err("App-owned checkout origin is unavailable".to_string());
    }
    String::from_utf8(output.stdout)
        .map(|value| value.trim().to_string())
        .map_err(|_| "App-owned checkout origin is invalid".to_string())
}
