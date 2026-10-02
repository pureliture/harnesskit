use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::app_service::AppService;
use crate::checkout::{CheckoutCloner, CheckoutRegistry, RegisteredCheckout};
use crate::install_flow::{
    ApplyApprovals, InstallAttempt, InstallFlow, InstallPreview, InstallRequest,
};
use crate::models::Inventory;
use crate::repo_status::{RepoInspector, RepoStatus};

static STATE_FILE_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Serialize)]
pub struct RepoStatusView {
    pub branch: Option<String>,
    pub detached: bool,
    pub dirty: bool,
    pub recent_commits: Vec<String>,
}

impl From<RepoStatus> for RepoStatusView {
    fn from(status: RepoStatus) -> Self {
        Self {
            branch: status.current_branch,
            detached: status.detached,
            dirty: status.dirty,
            recent_commits: status.recent_commit_summaries,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct RegisterCheckoutResponse {
    pub checkout_id: String,
    pub canonical_path: PathBuf,
    pub repo_status: RepoStatusView,
}

#[derive(Debug, Clone, Serialize)]
pub struct PreviewInstallResponse {
    pub preview_id: String,
    pub semantic_fingerprint: String,
    pub plan: Value,
    pub request: PreviewRequestView,
}

#[derive(Debug, Clone, Serialize)]
pub struct PreviewRequestView {
    pub profile: String,
    pub scope: String,
    pub target_root: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ProcessExitView {
    pub exit_code: i32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ApplyInstallResponse {
    pub apply: ProcessExitView,
    pub verify: Option<ProcessExitView>,
    pub destinations: Vec<DestinationOutcomeView>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DestinationOutcomeView {
    pub destination: String,
    pub status: String,
}

struct StoredPreview {
    checkout_id: String,
    preview: InstallPreview,
}

#[derive(Debug, Serialize, Deserialize)]
struct PersistedState {
    checkout_path: PathBuf,
}

pub struct AppController {
    checkouts: Mutex<CheckoutRegistry>,
    previews: Mutex<BTreeMap<String, StoredPreview>>,
    operation_gate: Mutex<()>,
    registration_gate: Mutex<()>,
    preview_counter: AtomicU64,
    state_file: Option<PathBuf>,
    last_checkout_id: Mutex<Option<String>>,
}

impl Default for AppController {
    fn default() -> Self {
        Self::new(None)
    }
}

impl AppController {
    fn new(state_file: Option<PathBuf>) -> Self {
        Self {
            checkouts: Mutex::new(CheckoutRegistry::default()),
            previews: Mutex::new(BTreeMap::new()),
            operation_gate: Mutex::new(()),
            registration_gate: Mutex::new(()),
            preview_counter: AtomicU64::new(0),
            state_file,
            last_checkout_id: Mutex::new(None),
        }
    }

    pub(crate) fn private_state_root(&self) -> Result<PathBuf, String> {
        let parent = self
            .state_file
            .as_ref()
            .and_then(|path| path.parent())
            .ok_or_else(|| "import_private_state_unavailable".to_string())?;
        let parent =
            std::fs::canonicalize(parent).map_err(|_| "import_private_state_unsafe".to_string())?;
        crate::contexts::install::writer::private_registration_directory(
            &parent,
            "component-import-state",
        )
    }

    pub fn with_state_file(state_file: PathBuf) -> Self {
        let controller = Self::new(Some(state_file));
        controller.restore_persisted_state();
        controller
    }

    pub fn clone_default_checkout(
        &self,
        app_data_dir: &Path,
    ) -> Result<RegisterCheckoutResponse, String> {
        let _operation = self
            .operation_gate
            .lock()
            .map_err(|_| "Harness operation gate is unavailable".to_string())?;
        let checkout = CheckoutCloner::clone_default(app_data_dir)?;
        self.register_checkout(checkout.root())
    }

    pub fn register_checkout(
        &self,
        path: impl AsRef<Path>,
    ) -> Result<RegisterCheckoutResponse, String> {
        let _registration = self
            .registration_gate
            .lock()
            .map_err(|_| "Checkout registration gate is unavailable".to_string())?;
        let (registered, checkout) = {
            let mut registry = self
                .checkouts
                .lock()
                .map_err(|_| "Checkout registry is unavailable".to_string())?;
            let registered = registry.register(path)?;
            let checkout = registry.resolve_cloned(&registered.checkout_id)?;
            (registered, checkout)
        };
        let repo_status = RepoInspector::status(&checkout)?.into();
        self.persist_checkout(&registered.canonical_path)?;
        *self
            .last_checkout_id
            .lock()
            .map_err(|_| "Saved checkout state is unavailable".to_string())? =
            Some(registered.checkout_id.clone());

        Ok(RegisterCheckoutResponse {
            checkout_id: registered.checkout_id,
            canonical_path: registered.canonical_path,
            repo_status,
        })
    }

    pub fn saved_checkout(&self) -> Result<Option<RegisterCheckoutResponse>, String> {
        let checkout_id = self.saved_checkout_id()?;
        let Some(checkout_id) = checkout_id else {
            return Ok(None);
        };
        let checkout = self.resolve_checkout(&checkout_id)?;
        let repo_status = RepoInspector::status(&checkout)?.into();
        Ok(Some(RegisterCheckoutResponse {
            checkout_id,
            canonical_path: checkout.root().to_path_buf(),
            repo_status,
        }))
    }

    pub(crate) fn saved_checkout_id(&self) -> Result<Option<String>, String> {
        self.last_checkout_id
            .lock()
            .map(|checkout_id| checkout_id.clone())
            .map_err(|_| "Saved checkout state is unavailable".to_string())
    }

    pub fn scan_harness(
        &self,
        checkout_id: &str,
        home: &Path,
        app_version: &str,
        scan_timestamp: &str,
    ) -> Result<Inventory, String> {
        let checkout = self.resolve_checkout(checkout_id)?;
        let _operation = self
            .operation_gate
            .lock()
            .map_err(|_| "Harness operation gate is unavailable".to_string())?;
        AppService::scan(&checkout, home, app_version, scan_timestamp)
    }

    pub fn preview_install(
        &self,
        checkout_id: &str,
        request: InstallRequest,
    ) -> Result<PreviewInstallResponse, String> {
        let checkout = self.resolve_checkout(checkout_id)?;
        let _operation = self
            .operation_gate
            .lock()
            .map_err(|_| "Harness operation gate is unavailable".to_string())?;
        let preview = InstallFlow::new(&checkout)
            .preview(&request)
            .map_err(|error| error.to_string())?;
        let semantic_fingerprint = approval_fingerprint(checkout_id, &preview);
        let preview_id = self.preview_id(&semantic_fingerprint);
        let request = PreviewRequestView {
            profile: preview.request().profile.clone(),
            scope: preview.request().scope.clone(),
            target_root: preview.request().target_root.clone(),
        };
        let mut plan = preview.plan().clone();
        if let Some(object) = plan.as_object_mut() {
            object.insert(
                "target_root".to_string(),
                Value::String(preview.request().target_root.to_string_lossy().to_string()),
            );
        }

        let mut previews = self
            .previews
            .lock()
            .map_err(|_| "Install preview registry is unavailable".to_string())?;
        previews.retain(|_, stored| stored.checkout_id != checkout_id);
        previews.insert(
            preview_id.clone(),
            StoredPreview {
                checkout_id: checkout_id.to_string(),
                preview,
            },
        );

        Ok(PreviewInstallResponse {
            preview_id,
            semantic_fingerprint,
            plan,
            request,
        })
    }

    pub fn apply_install(
        &self,
        preview_id: &str,
        approvals: ApplyApprovals,
    ) -> Result<ApplyInstallResponse, String> {
        if !approvals.confirmed {
            return Err("Install confirmation is required".to_string());
        }
        let stored = self
            .previews
            .lock()
            .map_err(|_| "Install preview registry is unavailable".to_string())?
            .remove(preview_id)
            .ok_or_else(|| "Install preview is unavailable or already consumed".to_string())?;
        let checkout = self.resolve_checkout(&stored.checkout_id)?;
        let _operation = self
            .operation_gate
            .lock()
            .map_err(|_| "Harness operation gate is unavailable".to_string())?;
        let attempt = InstallFlow::new(&checkout)
            .execute_confirmed(&stored.preview, &approvals)
            .map_err(|error| error.to_string())?;

        Ok(project_attempt(attempt))
    }

    pub(crate) fn resolve_checkout(&self, checkout_id: &str) -> Result<RegisteredCheckout, String> {
        self.checkouts
            .lock()
            .map_err(|_| "Checkout registry is unavailable".to_string())?
            .resolve_cloned(checkout_id)
    }

    fn preview_id(&self, approval_fingerprint: &str) -> String {
        let counter = self.preview_counter.fetch_add(1, Ordering::Relaxed);
        let identity = format!(
            "{}\0{}\0{}",
            approval_fingerprint,
            std::process::id(),
            counter
        );
        digest(identity.as_bytes())
    }

    fn restore_persisted_state(&self) {
        let Some(state_file) = &self.state_file else {
            return;
        };
        let Ok(contents) = fs::read(state_file) else {
            return;
        };
        let Ok(state) = serde_json::from_slice::<PersistedState>(&contents) else {
            return;
        };
        let registered = {
            let Ok(mut registry) = self.checkouts.lock() else {
                return;
            };
            let Ok(registered) = registry.register(&state.checkout_path) else {
                return;
            };
            let Ok(checkout) = registry.resolve_cloned(&registered.checkout_id) else {
                return;
            };
            if RepoInspector::status(&checkout).is_err() {
                return;
            }
            registered
        };
        if let Ok(mut last_checkout_id) = self.last_checkout_id.lock() {
            *last_checkout_id = Some(registered.checkout_id);
        }
    }

    fn persist_checkout(&self, checkout_path: &Path) -> Result<(), String> {
        let Some(state_file) = &self.state_file else {
            return Ok(());
        };
        let parent = state_file
            .parent()
            .ok_or_else(|| "App state directory is unavailable".to_string())?;
        fs::create_dir_all(parent).map_err(|_| "App state directory is unavailable".to_string())?;
        let serialized = serde_json::to_vec(&PersistedState {
            checkout_path: checkout_path.to_path_buf(),
        })
        .map_err(|_| "App state could not be serialized".to_string())?;
        let counter = STATE_FILE_COUNTER.fetch_add(1, Ordering::Relaxed);
        let temporary = parent.join(format!(
            ".harness-desktop-state-{}-{counter}.tmp",
            std::process::id()
        ));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        set_owner_only_permissions(&mut options);
        let mut file = options
            .open(&temporary)
            .map_err(|_| "App state could not be persisted".to_string())?;
        if file
            .write_all(&serialized)
            .and_then(|_| file.sync_all())
            .is_err()
        {
            let _ = fs::remove_file(&temporary);
            return Err("App state could not be persisted".to_string());
        }
        if fs::rename(&temporary, state_file).is_err() {
            let _ = fs::remove_file(&temporary);
            return Err("App state could not be persisted".to_string());
        }
        Ok(())
    }
}

#[cfg(unix)]
fn set_owner_only_permissions(options: &mut OpenOptions) {
    use std::os::unix::fs::OpenOptionsExt;
    options.mode(0o600);
}

#[cfg(not(unix))]
fn set_owner_only_permissions(_options: &mut OpenOptions) {}

fn approval_fingerprint(checkout_id: &str, preview: &InstallPreview) -> String {
    let request = preview.request();
    let identity = format!(
        "{}\0{}\0{}\0{}\0{}",
        checkout_id,
        request.profile,
        request.scope,
        request.target_root.to_string_lossy(),
        preview.semantic_fingerprint()
    );
    digest(identity.as_bytes())
}

fn digest(input: &[u8]) -> String {
    let digest = Sha256::digest(input);
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn project_attempt(attempt: InstallAttempt) -> ApplyInstallResponse {
    ApplyInstallResponse {
        apply: ProcessExitView {
            exit_code: attempt.apply.exit_code,
        },
        verify: attempt.verify.map(|verify| ProcessExitView {
            exit_code: verify.exit_code,
        }),
        destinations: attempt
            .destinations
            .into_iter()
            .map(|outcome| DestinationOutcomeView {
                destination: outcome.destination,
                status: outcome.status.as_str().to_string(),
            })
            .collect(),
    }
}
