use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::checkout::RegisteredCheckout;
use crate::subprocess_runner::{InstallPlanMode, InstallScope, SubprocessResult, SubprocessRunner};

const SEMANTIC_PLAN_KEYS: [&str; 6] = [
    "scope",
    "targets",
    "components",
    "artifacts",
    "runtime_surfaces",
    "activation_gates",
];
const TEMP_PLAN_ATTEMPTS: u64 = 128;
static TEMP_PLAN_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone)]
pub struct InstallRequest {
    pub profile: String,
    pub scope: String,
    pub target_root: PathBuf,
}

#[derive(Debug, Clone, Copy)]
pub struct ApplyApprovals {
    pub confirmed: bool,
    pub overwrite: bool,
    pub allow_runtime_hooks: bool,
}

#[derive(Debug)]
pub enum InstallFlowError {
    ConfirmationRequired,
    PlanChanged,
    InvalidRequest,
    CheckoutUnavailable,
    PlanGenerationFailed,
    InvalidPlan,
    TemporaryPlanUnavailable,
    ApplyFailed,
    VerifyFailed,
}

impl std::fmt::Display for InstallFlowError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let message = match self {
            Self::ConfirmationRequired => "Install confirmation is required",
            Self::PlanChanged => "Install plan changed after preview",
            Self::InvalidRequest => "Install request is invalid",
            Self::CheckoutUnavailable => "HarnessKit checkout is unavailable",
            Self::PlanGenerationFailed => "HarnessKit install plan generation failed",
            Self::InvalidPlan => "HarnessKit install plan is invalid",
            Self::TemporaryPlanUnavailable => "App-owned temporary install plan is unavailable",
            Self::ApplyFailed => "HarnessKit install apply failed",
            Self::VerifyFailed => "HarnessKit install verification failed",
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for InstallFlowError {}

#[derive(Debug, Clone)]
pub struct InstallPreview {
    checkout_root: PathBuf,
    request: InstallRequest,
    plan: Value,
    semantic_plan: Value,
    semantic_fingerprint: String,
}

impl InstallPreview {
    pub fn plan(&self) -> &Value {
        &self.plan
    }

    pub fn request(&self) -> &InstallRequest {
        &self.request
    }

    pub fn semantic_fingerprint(&self) -> &str {
        &self.semantic_fingerprint
    }
}

#[derive(Debug)]
pub struct InstallAttempt {
    pub apply: SubprocessResult,
    pub verify: Option<SubprocessResult>,
    pub destinations: Vec<DestinationOutcome>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DestinationOutcome {
    pub destination: String,
    pub status: DestinationStatus,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DestinationStatus {
    Changed,
    Unchanged,
    Skipped,
    Failed,
}

impl DestinationStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Changed => "changed",
            Self::Unchanged => "unchanged",
            Self::Skipped => "skipped",
            Self::Failed => "failed",
        }
    }
}

pub struct InstallFlow<'checkout> {
    checkout: &'checkout RegisteredCheckout,
}

impl<'checkout> InstallFlow<'checkout> {
    pub fn new(checkout: &'checkout RegisteredCheckout) -> Self {
        Self { checkout }
    }

    pub fn preview(&self, request: &InstallRequest) -> Result<InstallPreview, InstallFlowError> {
        let request = normalize_request(request)?;
        let scope = parse_scope(&request)?;
        let result = SubprocessRunner::run_install_plan(
            self.checkout,
            &request.profile,
            scope,
            InstallPlanMode::DryRun,
        )
        .map_err(|_| InstallFlowError::CheckoutUnavailable)?;
        if result.exit_code != 0 {
            return Err(InstallFlowError::PlanGenerationFailed);
        }

        let plan = parse_plan(&result.stdout, InstallPlanMode::DryRun, scope)?;
        let semantic_plan = semantic_plan(&plan)?;
        let semantic_fingerprint = fingerprint(&semantic_plan)?;

        Ok(InstallPreview {
            checkout_root: self.checkout.root().to_path_buf(),
            request,
            plan,
            semantic_plan,
            semantic_fingerprint,
        })
    }

    pub fn execute_confirmed(
        &self,
        preview: &InstallPreview,
        approvals: &ApplyApprovals,
    ) -> Result<InstallAttempt, InstallFlowError> {
        if !approvals.confirmed {
            return Err(InstallFlowError::ConfirmationRequired);
        }
        if preview.checkout_root != self.checkout.root() {
            return Err(InstallFlowError::InvalidRequest);
        }

        let scope = parse_scope(&preview.request)?;
        let apply_plan_result = SubprocessRunner::run_install_plan(
            self.checkout,
            &preview.request.profile,
            scope,
            InstallPlanMode::Apply,
        )
        .map_err(|_| InstallFlowError::CheckoutUnavailable)?;
        if apply_plan_result.exit_code != 0 {
            return Err(InstallFlowError::PlanGenerationFailed);
        }

        let apply_plan = parse_plan(&apply_plan_result.stdout, InstallPlanMode::Apply, scope)?;
        if semantic_plan(&apply_plan)? != preview.semantic_plan {
            return Err(InstallFlowError::PlanChanged);
        }
        let (tracked_destinations, skipped_destinations) = plan_destinations(&apply_plan)?;
        let before = capture_destinations(&preview.request.target_root, &tracked_destinations)?;

        let temporary_plan = TemporaryPlan::create(
            &apply_plan,
            self.checkout.root(),
            &preview.request.target_root,
        )?;
        let apply = SubprocessRunner::run_install_apply(
            self.checkout,
            temporary_plan.path(),
            &preview.request.target_root,
            approvals.overwrite,
            approvals.allow_runtime_hooks,
        )
        .map_err(|_| InstallFlowError::CheckoutUnavailable)?;
        if apply.exit_code != 0 {
            let destinations = classify_destinations(
                &preview.request.target_root,
                &tracked_destinations,
                &skipped_destinations,
                &before,
                false,
            );
            return Ok(InstallAttempt {
                apply,
                verify: None,
                destinations,
            });
        }

        let verify = SubprocessRunner::run_install_verify(
            self.checkout,
            temporary_plan.path(),
            &preview.request.target_root,
        )
        .ok();

        let destinations = classify_destinations(
            &preview.request.target_root,
            &tracked_destinations,
            &skipped_destinations,
            &before,
            true,
        );

        Ok(InstallAttempt {
            apply,
            verify,
            destinations,
        })
    }
}

fn normalize_request(request: &InstallRequest) -> Result<InstallRequest, InstallFlowError> {
    if request.profile.trim().is_empty()
        || !request.target_root.is_absolute()
        || !request.target_root.is_dir()
    {
        return Err(InstallFlowError::InvalidRequest);
    }
    let target_root =
        fs::canonicalize(&request.target_root).map_err(|_| InstallFlowError::InvalidRequest)?;
    Ok(InstallRequest {
        profile: request.profile.clone(),
        scope: request.scope.clone(),
        target_root,
    })
}

fn parse_scope(request: &InstallRequest) -> Result<InstallScope, InstallFlowError> {
    if request.profile.is_empty() || request.target_root.as_os_str().is_empty() {
        return Err(InstallFlowError::InvalidRequest);
    }
    InstallScope::try_from(request.scope.as_str()).map_err(|_| InstallFlowError::InvalidRequest)
}

fn parse_plan(
    stdout: &str,
    expected_mode: InstallPlanMode,
    expected_scope: InstallScope,
) -> Result<Value, InstallFlowError> {
    let plan: Value = serde_json::from_str(stdout).map_err(|_| InstallFlowError::InvalidPlan)?;
    let object = plan.as_object().ok_or(InstallFlowError::InvalidPlan)?;
    let expected_mode = match expected_mode {
        InstallPlanMode::DryRun => "dry-run",
        InstallPlanMode::Apply => "apply",
    };
    let expected_scope = match expected_scope {
        InstallScope::User => "user",
        InstallScope::Project => "project",
    };
    if object.get("mode").and_then(Value::as_str) != Some(expected_mode)
        || object.get("scope").and_then(Value::as_str) != Some(expected_scope)
    {
        return Err(InstallFlowError::InvalidPlan);
    }
    Ok(plan)
}

fn semantic_plan(plan: &Value) -> Result<Value, InstallFlowError> {
    let object = plan.as_object().ok_or(InstallFlowError::InvalidPlan)?;
    let mut semantic = Map::new();
    for key in SEMANTIC_PLAN_KEYS {
        let value = object.get(key).ok_or(InstallFlowError::InvalidPlan)?;
        semantic.insert(key.to_string(), value.clone());
    }
    semantic.insert(
        "warnings".to_string(),
        object
            .get("warnings")
            .cloned()
            .unwrap_or_else(|| Value::Array(Vec::new())),
    );
    semantic.insert(
        "skipped_writes".to_string(),
        object
            .get("skipped_writes")
            .cloned()
            .unwrap_or_else(|| Value::Array(Vec::new())),
    );
    Ok(Value::Object(semantic))
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum DestinationSnapshot {
    Missing,
    File(String),
}

fn plan_destinations(plan: &Value) -> Result<(Vec<String>, Vec<String>), InstallFlowError> {
    let object = plan.as_object().ok_or(InstallFlowError::InvalidPlan)?;
    let artifacts = object
        .get("artifacts")
        .and_then(Value::as_array)
        .ok_or(InstallFlowError::InvalidPlan)?;
    let mut tracked = std::collections::BTreeSet::new();
    for artifact in artifacts {
        let destination = artifact
            .get("destination")
            .and_then(Value::as_str)
            .ok_or(InstallFlowError::InvalidPlan)?;
        validate_relative_destination(destination)?;
        tracked.insert(destination.to_string());
    }

    let mut skipped = std::collections::BTreeSet::new();
    if let Some(entries) = object.get("skipped_writes").and_then(Value::as_array) {
        for entry in entries {
            let destination = entry
                .as_str()
                .or_else(|| entry.get("destination").and_then(Value::as_str))
                .ok_or(InstallFlowError::InvalidPlan)?;
            validate_relative_destination(destination)?;
            skipped.insert(destination.to_string());
        }
    }

    Ok((tracked.into_iter().collect(), skipped.into_iter().collect()))
}

fn validate_relative_destination(destination: &str) -> Result<(), InstallFlowError> {
    let path = Path::new(destination);
    if destination.is_empty()
        || path.is_absolute()
        || !path
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
    {
        return Err(InstallFlowError::InvalidPlan);
    }
    Ok(())
}

fn capture_destinations(
    target_root: &Path,
    destinations: &[String],
) -> Result<std::collections::BTreeMap<String, DestinationSnapshot>, InstallFlowError> {
    destinations
        .iter()
        .map(|destination| {
            capture_destination(target_root, destination)
                .map(|snapshot| (destination.clone(), snapshot))
        })
        .collect()
}

fn capture_destination(
    target_root: &Path,
    destination: &str,
) -> Result<DestinationSnapshot, InstallFlowError> {
    let path = target_root.join(destination);
    match fs::symlink_metadata(&path) {
        Ok(_) => {
            let canonical =
                fs::canonicalize(&path).map_err(|_| InstallFlowError::InvalidRequest)?;
            if !canonical.starts_with(target_root) || !canonical.is_file() {
                return Err(InstallFlowError::InvalidRequest);
            }
            let bytes = fs::read(canonical).map_err(|_| InstallFlowError::InvalidRequest)?;
            let digest = Sha256::digest(bytes);
            Ok(DestinationSnapshot::File(
                digest.iter().map(|byte| format!("{byte:02x}")).collect(),
            ))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            validate_existing_ancestor(target_root, &path)?;
            Ok(DestinationSnapshot::Missing)
        }
        Err(_) => Err(InstallFlowError::InvalidRequest),
    }
}

fn validate_existing_ancestor(target_root: &Path, path: &Path) -> Result<(), InstallFlowError> {
    let mut ancestor = path.parent().ok_or(InstallFlowError::InvalidRequest)?;
    while !ancestor.exists() {
        ancestor = ancestor.parent().ok_or(InstallFlowError::InvalidRequest)?;
    }
    let canonical = fs::canonicalize(ancestor).map_err(|_| InstallFlowError::InvalidRequest)?;
    if !canonical.starts_with(target_root) {
        return Err(InstallFlowError::InvalidRequest);
    }
    Ok(())
}

fn classify_destinations(
    target_root: &Path,
    tracked: &[String],
    skipped: &[String],
    before: &std::collections::BTreeMap<String, DestinationSnapshot>,
    apply_succeeded: bool,
) -> Vec<DestinationOutcome> {
    let mut outcomes = std::collections::BTreeMap::new();
    for destination in tracked {
        let after = capture_destination(target_root, destination);
        let status = match (before.get(destination), after) {
            (Some(DestinationSnapshot::File(before)), Ok(DestinationSnapshot::File(after)))
                if apply_succeeded && before == &after =>
            {
                DestinationStatus::Unchanged
            }
            (Some(_), Ok(DestinationSnapshot::File(_))) => DestinationStatus::Changed,
            _ => DestinationStatus::Failed,
        };
        outcomes.insert(destination.clone(), status);
    }
    for destination in skipped {
        outcomes.insert(destination.clone(), DestinationStatus::Skipped);
    }
    outcomes
        .into_iter()
        .map(|(destination, status)| DestinationOutcome {
            destination,
            status,
        })
        .collect()
}

fn fingerprint(semantic_plan: &Value) -> Result<String, InstallFlowError> {
    let canonical = serde_json::to_vec(semantic_plan).map_err(|_| InstallFlowError::InvalidPlan)?;
    let digest = Sha256::digest(canonical);
    Ok(digest.iter().map(|byte| format!("{byte:02x}")).collect())
}

struct TemporaryPlan {
    path: PathBuf,
}

impl TemporaryPlan {
    fn create(
        plan: &Value,
        checkout_root: &Path,
        target_root: &Path,
    ) -> Result<Self, InstallFlowError> {
        let temp_root = fs::canonicalize(std::env::temp_dir())
            .map_err(|_| InstallFlowError::TemporaryPlanUnavailable)?;
        let checkout_root = absolute_path(checkout_root)?;
        let target_root = absolute_path(target_root)?;
        if temp_root.starts_with(&checkout_root) || temp_root.starts_with(&target_root) {
            return Err(InstallFlowError::TemporaryPlanUnavailable);
        }

        let serialized = serde_json::to_vec(plan).map_err(|_| InstallFlowError::InvalidPlan)?;
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| InstallFlowError::TemporaryPlanUnavailable)?
            .as_nanos();

        for _ in 0..TEMP_PLAN_ATTEMPTS {
            let counter = TEMP_PLAN_COUNTER.fetch_add(1, Ordering::Relaxed);
            let path = temp_root.join(format!(
                "harness-desktop-install-plan-{}-{nonce}-{counter}.json",
                std::process::id()
            ));
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            set_owner_only_permissions(&mut options);
            match options.open(&path) {
                Ok(mut file) => {
                    if write_plan(&mut file, &serialized).is_err() {
                        let _ = fs::remove_file(&path);
                        return Err(InstallFlowError::TemporaryPlanUnavailable);
                    }
                    return Ok(Self { path });
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(_) => return Err(InstallFlowError::TemporaryPlanUnavailable),
            }
        }

        Err(InstallFlowError::TemporaryPlanUnavailable)
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TemporaryPlan {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

fn absolute_path(path: &Path) -> Result<PathBuf, InstallFlowError> {
    if let Ok(canonical) = fs::canonicalize(path) {
        return Ok(canonical);
    }
    if path.is_absolute() {
        return Ok(path.to_path_buf());
    }
    std::env::current_dir()
        .map(|current| current.join(path))
        .map_err(|_| InstallFlowError::TemporaryPlanUnavailable)
}

fn write_plan(file: &mut File, serialized: &[u8]) -> std::io::Result<()> {
    file.write_all(serialized)?;
    file.sync_all()
}

#[cfg(unix)]
fn set_owner_only_permissions(options: &mut OpenOptions) {
    use std::os::unix::fs::OpenOptionsExt;
    options.mode(0o600);
}

#[cfg(not(unix))]
fn set_owner_only_permissions(_options: &mut OpenOptions) {}
