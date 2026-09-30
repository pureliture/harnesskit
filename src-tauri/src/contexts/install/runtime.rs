//! Fixed bundled runtime invocation for install_plan_sandbox_v1.

use super::workspace::{
    directory_authority_from_path, DirectoryAuthority, InstallWorkspace, SourceManifest,
    SourceManifestEntry, StageWritePolicy, TreeManifest,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
#[cfg(test)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

pub const SANDBOX_EXECUTABLE: &str = "/usr/bin/sandbox-exec";
const RUNTIME_ID: &str = "harness-desktop-install-runtime-v1";
const RUNTIME_MANIFEST: &str = "runtime-manifest.json";
const PYTHON_EXECUTABLE: &str = "python/bin/python3";
const INSTALL_ENTRYPOINT: &str = "install_entry.py";
const VENDOR_ROOT: &str = "vendor";

#[derive(Debug)]
pub struct BundledInstallRuntime {
    authority: DirectoryAuthority,
    python_executable: PathBuf,
    entrypoint: PathBuf,
    vendor_root: PathBuf,
    tree_manifest: TreeManifest,
    manifest_sha256: String,
    target_triple: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlanMode {
    DryRun,
    Apply,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanRunRequest {
    pub mode: PlanMode,
    pub profile: String,
    pub scope: String,
    pub target_ids: Vec<String>,
    pub component_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArtifactRunRequest {
    pub component_ids: Vec<String>,
}

#[derive(Debug)]
pub struct PlanProcessSpec {
    pub executable: PathBuf,
    pub args: Vec<String>,
    pub environment: BTreeMap<String, String>,
    pub clear_environment: bool,
    pub timeout: Duration,
    pub stdout_limit: usize,
    pub stderr_limit: usize,
    spawn_authority: Option<SpawnAuthority>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanProcessCapture {
    pub exit_code: i32,
    pub timed_out: bool,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlanProcessPortError {
    LaunchFailed,
    AuthorityChanged,
    CaptureFailed,
}

pub trait PlanProcessPort {
    fn run(&self, spec: &PlanProcessSpec) -> Result<PlanProcessCapture, PlanProcessPortError>;
}

#[derive(Debug, Default, Clone, Copy)]
pub struct BoundedPlanProcess;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanRunOutput {
    pub json: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InstallRuntimeError {
    RuntimeUnavailable,
    InvalidRequest,
    PreviewStale,
    LaunchFailed,
    Timeout,
    StdoutOverflow,
    StderrOverflow,
    NonUtf8Output,
    NonZeroExit,
}

pub struct FixedInstallPlanRunner<P> {
    runtime: BundledInstallRuntime,
    process: P,
}

#[derive(Debug)]
struct SpawnAuthority {
    runtime: BundledInstallRuntime,
    workspace: DirectoryAuthority,
    runtime_tmp: DirectoryAuthority,
    dist: Option<DirectoryAuthority>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct EmbeddedRuntimeManifest {
    schema_version: u32,
    runtime_id: String,
    target: String,
    lock_sha256: String,
    entries: Vec<RuntimeManifestEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeManifestEntry {
    path: String,
    sha256: String,
    size: u64,
    mode: u32,
}

impl BundledInstallRuntime {
    /// Opens the packaged runtime against manifest bytes pinned into the application binary.
    /// Callers must not source either manifest argument from the mutable resource directory.
    pub(crate) fn from_embedded_manifest(
        resource_root: PathBuf,
        manifest_json: &str,
        embedded_manifest_sha256: &str,
    ) -> Result<Self, InstallRuntimeError> {
        validate_bundle_root_shape(&resource_root)?;
        let actual_manifest_sha256 = hex_sha256(manifest_json.as_bytes());
        if !is_sha256(embedded_manifest_sha256)
            || actual_manifest_sha256 != embedded_manifest_sha256
        {
            return Err(InstallRuntimeError::RuntimeUnavailable);
        }
        let manifest: EmbeddedRuntimeManifest = serde_json::from_str(manifest_json)
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        if manifest.schema_version != 1
            || manifest.runtime_id != RUNTIME_ID
            || manifest.target != current_target_triple()
            || !is_sha256(&manifest.lock_sha256)
            || !resource_root.ends_with(Path::new("install-runtime").join(&manifest.target))
        {
            return Err(InstallRuntimeError::RuntimeUnavailable);
        }

        let mut expected_entries = Vec::with_capacity(manifest.entries.len());
        let mut previous_path: Option<&str> = None;
        for entry in &manifest.entries {
            validate_relative(&entry.path)?;
            if entry.path == RUNTIME_MANIFEST
                || !is_sha256(&entry.sha256)
                || entry.mode > 0o777
                || previous_path.is_some_and(|previous| previous >= entry.path.as_str())
            {
                return Err(InstallRuntimeError::RuntimeUnavailable);
            }
            previous_path = Some(&entry.path);
            expected_entries.push(SourceManifestEntry {
                relative_path: entry.path.clone(),
                sha256: entry.sha256.clone(),
                mode: entry.mode,
                size: entry.size,
            });
        }

        let authority = directory_authority_from_path(&resource_root)
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        let actual_tree = authority
            .tree_manifest(None)
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        let runtime_manifest_entry = actual_tree
            .files
            .entries
            .iter()
            .find(|entry| entry.relative_path == RUNTIME_MANIFEST)
            .ok_or(InstallRuntimeError::RuntimeUnavailable)?;
        if runtime_manifest_entry.sha256 != actual_manifest_sha256
            || runtime_manifest_entry.size != manifest_json.len() as u64
            || runtime_manifest_entry.mode != 0o644
        {
            return Err(InstallRuntimeError::RuntimeUnavailable);
        }
        let actual_package_files = SourceManifest {
            entries: actual_tree
                .files
                .entries
                .iter()
                .filter(|entry| entry.relative_path != RUNTIME_MANIFEST)
                .cloned()
                .collect(),
        };
        if actual_package_files.entries != expected_entries {
            return Err(InstallRuntimeError::RuntimeUnavailable);
        }
        let python_entry = actual_package_files
            .entries
            .iter()
            .find(|entry| entry.relative_path == PYTHON_EXECUTABLE)
            .ok_or(InstallRuntimeError::RuntimeUnavailable)?;
        if python_entry.mode & 0o111 == 0
            || !actual_package_files
                .entries
                .iter()
                .any(|entry| entry.relative_path == INSTALL_ENTRYPOINT)
            || !actual_tree.directories.contains(VENDOR_ROOT)
            || !actual_tree.directories.contains("python/bin")
        {
            return Err(InstallRuntimeError::RuntimeUnavailable);
        }
        let runtime = Self {
            python_executable: resource_root.join(PYTHON_EXECUTABLE),
            entrypoint: resource_root.join(INSTALL_ENTRYPOINT),
            vendor_root: resource_root.join(VENDOR_ROOT),
            authority,
            tree_manifest: actual_tree,
            manifest_sha256: actual_manifest_sha256,
            target_triple: manifest.target,
        };
        runtime.revalidate()?;
        Ok(runtime)
    }

    pub fn resource_root(&self) -> &Path {
        &self.authority.path
    }

    pub fn python_executable(&self) -> &Path {
        &self.python_executable
    }

    pub fn entrypoint(&self) -> &Path {
        &self.entrypoint
    }

    pub fn vendor_root(&self) -> &Path {
        &self.vendor_root
    }

    pub fn manifest_sha256(&self) -> &str {
        &self.manifest_sha256
    }

    pub fn target_triple(&self) -> &str {
        &self.target_triple
    }

    fn try_clone(&self) -> Result<Self, InstallRuntimeError> {
        Ok(Self {
            authority: self
                .authority
                .try_clone()
                .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?,
            python_executable: self.python_executable.clone(),
            entrypoint: self.entrypoint.clone(),
            vendor_root: self.vendor_root.clone(),
            tree_manifest: self.tree_manifest.clone(),
            manifest_sha256: self.manifest_sha256.clone(),
            target_triple: self.target_triple.clone(),
        })
    }

    fn revalidate(&self) -> Result<(), InstallRuntimeError> {
        self.authority
            .revalidate()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        if self
            .authority
            .tree_manifest(None)
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?
            != self.tree_manifest
        {
            return Err(InstallRuntimeError::RuntimeUnavailable);
        }
        Ok(())
    }

    #[cfg(test)]
    #[allow(dead_code)]
    pub(crate) fn build_test_manifest(
        resource_root: &Path,
    ) -> Result<(String, String), InstallRuntimeError> {
        let authority = directory_authority_from_path(resource_root)
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        let tree = authority
            .tree_manifest(None)
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        let manifest = EmbeddedRuntimeManifest {
            schema_version: 1,
            runtime_id: RUNTIME_ID.to_string(),
            target: current_target_triple(),
            lock_sha256: "1".repeat(64),
            entries: tree
                .files
                .entries
                .into_iter()
                .filter(|entry| entry.relative_path != RUNTIME_MANIFEST)
                .map(|entry| RuntimeManifestEntry {
                    path: entry.relative_path,
                    sha256: entry.sha256,
                    size: entry.size,
                    mode: entry.mode,
                })
                .collect(),
        };
        let json = serde_json::to_string(&manifest)
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        let hash = hex_sha256(json.as_bytes());
        Ok((json, hash))
    }

    #[cfg(test)]
    #[allow(dead_code)]
    pub(crate) fn from_test_bundle(resource_root: PathBuf) -> Result<Self, InstallRuntimeError> {
        let (manifest, hash) = Self::build_test_manifest(&resource_root)?;
        let manifest_path = resource_root.join(RUNTIME_MANIFEST);
        std::fs::write(&manifest_path, manifest.as_bytes())
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        std::fs::set_permissions(&manifest_path, std::fs::Permissions::from_mode(0o644))
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        Self::from_embedded_manifest(resource_root, &manifest, &hash)
    }
}

impl SpawnAuthority {
    fn revalidate_spawn_authority(&self) -> Result<(), InstallRuntimeError> {
        self.runtime.revalidate()?;
        self.workspace
            .revalidate()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        self.runtime_tmp
            .revalidate()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        if let Some(dist) = &self.dist {
            dist.revalidate()
                .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        }
        Ok(())
    }
}

impl PlanProcessSpec {
    pub(crate) fn revalidate_spawn_authority(&self) -> Result<(), PlanProcessPortError> {
        match &self.spawn_authority {
            Some(authority) => authority
                .revalidate_spawn_authority()
                .map_err(|_| PlanProcessPortError::AuthorityChanged),
            None if cfg!(test) => Ok(()),
            None => Err(PlanProcessPortError::AuthorityChanged),
        }
    }

    #[cfg(test)]
    #[allow(dead_code)]
    pub(crate) fn test_only(
        executable: PathBuf,
        args: Vec<String>,
        environment: BTreeMap<String, String>,
        timeout: Duration,
        stdout_limit: usize,
        stderr_limit: usize,
    ) -> Self {
        Self {
            executable,
            args,
            environment,
            clear_environment: true,
            timeout,
            stdout_limit,
            stderr_limit,
            spawn_authority: None,
        }
    }
}

impl PlanProcessPort for BoundedPlanProcess {
    fn run(&self, spec: &PlanProcessSpec) -> Result<PlanProcessCapture, PlanProcessPortError> {
        if !spec.clear_environment {
            return Err(PlanProcessPortError::AuthorityChanged);
        }
        spec.revalidate_spawn_authority()?;

        let mut command = Command::new(&spec.executable);
        command
            .args(&spec.args)
            .env_clear()
            .envs(&spec.environment)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command
            .spawn()
            .map_err(|_| PlanProcessPortError::LaunchFailed)?;
        let stdout = child
            .stdout
            .take()
            .ok_or(PlanProcessPortError::CaptureFailed)?;
        let stderr = child
            .stderr
            .take()
            .ok_or(PlanProcessPortError::CaptureFailed)?;
        let stdout_limit = spec.stdout_limit;
        let stderr_limit = spec.stderr_limit;
        let stdout_reader = thread::spawn(move || read_capped(stdout, stdout_limit));
        let stderr_reader = thread::spawn(move || read_capped(stderr, stderr_limit));

        let deadline = Instant::now() + spec.timeout;
        let (status, timed_out) = loop {
            match child
                .try_wait()
                .map_err(|_| PlanProcessPortError::CaptureFailed)?
            {
                Some(status) => break (status, false),
                None if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let status = child
                        .wait()
                        .map_err(|_| PlanProcessPortError::CaptureFailed)?;
                    break (status, true);
                }
                None => thread::sleep(Duration::from_millis(5)),
            }
        };
        let stdout = stdout_reader
            .join()
            .map_err(|_| PlanProcessPortError::CaptureFailed)??;
        let stderr = stderr_reader
            .join()
            .map_err(|_| PlanProcessPortError::CaptureFailed)??;
        Ok(PlanProcessCapture {
            exit_code: status.code().unwrap_or(-1),
            timed_out,
            stdout,
            stderr,
        })
    }
}

impl<P: PlanProcessPort> FixedInstallPlanRunner<P> {
    pub fn new(runtime: BundledInstallRuntime, process: P) -> Result<Self, InstallRuntimeError> {
        runtime.revalidate()?;
        Ok(Self { runtime, process })
    }

    pub fn run(
        &self,
        workspace: &InstallWorkspace,
        request: &PlanRunRequest,
    ) -> Result<PlanRunOutput, InstallRuntimeError> {
        self.runtime.revalidate()?;
        validate_request(request)?;
        workspace
            .validate_root_identity()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        workspace
            .validate_captured_source()
            .map_err(|_| InstallRuntimeError::PreviewStale)?;
        let runtime_tmp = workspace
            .ensure_runtime_tmp_authority()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        let dist = if request.mode == PlanMode::Apply {
            Some(
                workspace
                    .ensure_dist_authority()
                    .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?,
            )
        } else {
            None
        };
        let before = workspace
            .snapshot_stage_manifest()
            .map_err(|_| InstallRuntimeError::PreviewStale)?;
        let profile = sandbox_profile(
            &self.runtime,
            workspace.root(),
            &runtime_tmp.path,
            dist.as_ref().map(|authority| authority.path.as_path()),
        )?;
        let mut args = vec![
            "-p".to_string(),
            profile,
            path_text(&self.runtime.python_executable)?.to_string(),
            "-I".to_string(),
            "-B".to_string(),
            "-S".to_string(),
            path_text(&self.runtime.entrypoint)?.to_string(),
            "--script-id".to_string(),
            "install-plan".to_string(),
            "--workspace".to_string(),
            path_text(workspace.root())?.to_string(),
            "--mode".to_string(),
            match request.mode {
                PlanMode::DryRun => "dry-run",
                PlanMode::Apply => "apply",
            }
            .to_string(),
            "--profile".to_string(),
            request.profile.clone(),
            "--scope".to_string(),
            request.scope.clone(),
        ];
        let mut targets = request.target_ids.clone();
        targets.sort();
        for target in targets {
            args.push("--target-id".to_string());
            args.push(target);
        }
        let mut components = request.component_ids.clone();
        components.sort();
        for component in components {
            args.push("--component-id".to_string());
            args.push(component);
        }
        let environment = BTreeMap::from([
            ("LANG".to_string(), "en_US.UTF-8".to_string()),
            ("LC_ALL".to_string(), "en_US.UTF-8".to_string()),
            ("PYTHONDONTWRITEBYTECODE".to_string(), "1".to_string()),
            ("PYTHONNOUSERSITE".to_string(), "1".to_string()),
            (
                "TMPDIR".to_string(),
                path_text(&runtime_tmp.path)?.to_string(),
            ),
        ]);
        let spec = PlanProcessSpec {
            executable: PathBuf::from(SANDBOX_EXECUTABLE),
            args,
            environment,
            clear_environment: true,
            timeout: Duration::from_secs(60),
            stdout_limit: 8 * 1024 * 1024,
            stderr_limit: 1024 * 1024,
            spawn_authority: Some(SpawnAuthority {
                runtime: self.runtime.try_clone()?,
                workspace: workspace
                    .directory_authority()
                    .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?,
                runtime_tmp,
                dist,
            }),
        };
        spec.revalidate_spawn_authority()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        let capture = self.process.run(&spec);
        spec.revalidate_spawn_authority()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        workspace
            .validate_stage_transition(
                &before,
                match request.mode {
                    PlanMode::DryRun => StageWritePolicy::ReadOnly,
                    PlanMode::Apply => StageWritePolicy::DistOnly,
                },
            )
            .map_err(|_| InstallRuntimeError::PreviewStale)?;
        workspace
            .validate_captured_source()
            .map_err(|_| InstallRuntimeError::PreviewStale)?;
        let capture = capture.map_err(|error| match error {
            PlanProcessPortError::AuthorityChanged => InstallRuntimeError::RuntimeUnavailable,
            PlanProcessPortError::LaunchFailed | PlanProcessPortError::CaptureFailed => {
                InstallRuntimeError::LaunchFailed
            }
        })?;
        validate_capture(capture, &spec)
    }

    pub fn run_artifact_projection(
        &self,
        workspace: &InstallWorkspace,
        request: &ArtifactRunRequest,
    ) -> Result<PlanRunOutput, InstallRuntimeError> {
        self.runtime.revalidate()?;
        validate_artifact_request(request)?;
        workspace
            .validate_root_identity()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        workspace
            .validate_captured_source()
            .map_err(|_| InstallRuntimeError::PreviewStale)?;
        let runtime_tmp = workspace
            .ensure_runtime_tmp_authority()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        let before = workspace
            .snapshot_stage_manifest()
            .map_err(|_| InstallRuntimeError::PreviewStale)?;
        let profile = sandbox_profile(&self.runtime, workspace.root(), &runtime_tmp.path, None)?;
        let mut args = vec![
            "-p".to_string(),
            profile,
            path_text(&self.runtime.python_executable)?.to_string(),
            "-I".to_string(),
            "-B".to_string(),
            "-S".to_string(),
            path_text(&self.runtime.entrypoint)?.to_string(),
            "--script-id".to_string(),
            "artifact-projection".to_string(),
            "--workspace".to_string(),
            path_text(workspace.root())?.to_string(),
        ];
        let mut components = request.component_ids.clone();
        components.sort();
        for component in components {
            args.push("--component-id".to_string());
            args.push(component);
        }
        let environment = BTreeMap::from([
            ("LANG".to_string(), "en_US.UTF-8".to_string()),
            ("LC_ALL".to_string(), "en_US.UTF-8".to_string()),
            ("PYTHONDONTWRITEBYTECODE".to_string(), "1".to_string()),
            ("PYTHONNOUSERSITE".to_string(), "1".to_string()),
            (
                "TMPDIR".to_string(),
                path_text(&runtime_tmp.path)?.to_string(),
            ),
        ]);
        let spec = PlanProcessSpec {
            executable: PathBuf::from(SANDBOX_EXECUTABLE),
            args,
            environment,
            clear_environment: true,
            timeout: Duration::from_secs(60),
            stdout_limit: 8 * 1024 * 1024,
            stderr_limit: 1024 * 1024,
            spawn_authority: Some(SpawnAuthority {
                runtime: self.runtime.try_clone()?,
                workspace: workspace
                    .directory_authority()
                    .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?,
                runtime_tmp,
                dist: None,
            }),
        };
        spec.revalidate_spawn_authority()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        let capture = self.process.run(&spec);
        spec.revalidate_spawn_authority()
            .map_err(|_| InstallRuntimeError::RuntimeUnavailable)?;
        workspace
            .validate_stage_transition(&before, StageWritePolicy::ReadOnly)
            .map_err(|_| InstallRuntimeError::PreviewStale)?;
        workspace
            .validate_captured_source()
            .map_err(|_| InstallRuntimeError::PreviewStale)?;
        let capture = capture.map_err(|error| match error {
            PlanProcessPortError::AuthorityChanged => InstallRuntimeError::RuntimeUnavailable,
            PlanProcessPortError::LaunchFailed | PlanProcessPortError::CaptureFailed => {
                InstallRuntimeError::LaunchFailed
            }
        })?;
        validate_capture(capture, &spec)
    }

    pub fn runtime_manifest_sha256(&self) -> &str {
        self.runtime.manifest_sha256()
    }

    #[cfg(test)]
    pub fn process(&self) -> &P {
        &self.process
    }
}

impl InstallRuntimeError {
    pub const fn code(self) -> &'static str {
        match self {
            Self::RuntimeUnavailable => "install_runtime_unavailable",
            Self::InvalidRequest => "install_request_invalid",
            Self::PreviewStale => "preview_stale",
            Self::LaunchFailed => "install_plan_launch_failed",
            Self::Timeout => "install_plan_timeout",
            Self::StdoutOverflow => "install_plan_stdout_overflow",
            Self::StderrOverflow => "install_plan_stderr_overflow",
            Self::NonUtf8Output => "install_plan_non_utf8_output",
            Self::NonZeroExit => "install_plan_failed",
        }
    }
}

fn validate_request(request: &PlanRunRequest) -> Result<(), InstallRuntimeError> {
    if !safe_token(&request.profile)
        || !matches!(request.scope.as_str(), "user" | "project")
        || request.target_ids.is_empty()
        || request.target_ids.len() > 64
        || request.component_ids.is_empty()
        || request.component_ids.len() > 4096
        || request.target_ids.iter().any(|value| !safe_token(value))
        || request.component_ids.iter().any(|value| !safe_token(value))
        || request.target_ids.iter().collect::<BTreeSet<_>>().len() != request.target_ids.len()
        || request.component_ids.iter().collect::<BTreeSet<_>>().len()
            != request.component_ids.len()
    {
        return Err(InstallRuntimeError::InvalidRequest);
    }
    Ok(())
}

fn validate_artifact_request(request: &ArtifactRunRequest) -> Result<(), InstallRuntimeError> {
    if request.component_ids.is_empty()
        || request.component_ids.len() > 4096
        || request.component_ids.iter().any(|value| !safe_token(value))
        || request.component_ids.iter().collect::<BTreeSet<_>>().len()
            != request.component_ids.len()
    {
        return Err(InstallRuntimeError::InvalidRequest);
    }
    Ok(())
}

fn safe_token(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':'))
}

fn sandbox_profile(
    runtime: &BundledInstallRuntime,
    workspace: &Path,
    runtime_tmp: &Path,
    dist: Option<&Path>,
) -> Result<String, InstallRuntimeError> {
    let resource = seatbelt_path(runtime.resource_root())?;
    let python = seatbelt_path(runtime.python_executable())?;
    let workspace = seatbelt_path(workspace)?;
    let runtime_tmp = seatbelt_path(runtime_tmp)?;
    let mut writes = format!("(subpath \"{runtime_tmp}\")");
    if let Some(dist) = dist {
        writes.push_str(&format!(" (subpath \"{}\")", seatbelt_path(dist)?));
    }
    Ok(format!(
        "(version 1)\n(deny default)\n(deny network*)\n(deny process-fork)\n(allow process-exec (literal \"{python}\"))\n(allow file-read* (literal \"/\") (subpath \"{resource}\") (subpath \"{workspace}\") (subpath \"/usr/lib\") (subpath \"/System/Library\") (subpath \"/usr/share/locale\") (literal \"/private/etc/localtime\"))\n(allow file-write* {writes})"
    ))
}

fn seatbelt_path(path: &Path) -> Result<String, InstallRuntimeError> {
    let value = path_text(path)?;
    if value.chars().any(char::is_control) {
        return Err(InstallRuntimeError::RuntimeUnavailable);
    }
    Ok(value.replace('\\', "\\\\").replace('"', "\\\""))
}

fn path_text(path: &Path) -> Result<&str, InstallRuntimeError> {
    path.to_str().ok_or(InstallRuntimeError::RuntimeUnavailable)
}

fn validate_capture(
    capture: PlanProcessCapture,
    spec: &PlanProcessSpec,
) -> Result<PlanRunOutput, InstallRuntimeError> {
    if capture.timed_out {
        return Err(InstallRuntimeError::Timeout);
    }
    if capture.stdout.len() > spec.stdout_limit {
        return Err(InstallRuntimeError::StdoutOverflow);
    }
    if capture.stderr.len() > spec.stderr_limit {
        return Err(InstallRuntimeError::StderrOverflow);
    }
    let stdout =
        String::from_utf8(capture.stdout).map_err(|_| InstallRuntimeError::NonUtf8Output)?;
    String::from_utf8(capture.stderr).map_err(|_| InstallRuntimeError::NonUtf8Output)?;
    if capture.exit_code != 0 {
        return Err(InstallRuntimeError::NonZeroExit);
    }
    Ok(PlanRunOutput { json: stdout })
}

fn validate_bundle_root_shape(path: &Path) -> Result<(), InstallRuntimeError> {
    if !path.is_absolute() || path == Path::new("/") {
        return Err(InstallRuntimeError::RuntimeUnavailable);
    }
    let components = path
        .components()
        .filter_map(|component| match component {
            Component::Normal(value) => value.to_str(),
            _ => None,
        })
        .collect::<Vec<_>>();
    if components.len() < 5
        || !components[components.len() - 5].ends_with(".app")
        || components[components.len() - 4] != "Contents"
        || components[components.len() - 3] != "Resources"
        || components[components.len() - 2] != "install-runtime"
    {
        return Err(InstallRuntimeError::RuntimeUnavailable);
    }
    Ok(())
}

fn validate_relative(value: &str) -> Result<(), InstallRuntimeError> {
    let path = Path::new(value);
    if value.is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(InstallRuntimeError::RuntimeUnavailable);
    }
    Ok(())
}

fn current_target_triple() -> String {
    format!("{}-apple-darwin", std::env::consts::ARCH)
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn hex_sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn read_capped<R: Read>(reader: R, limit: usize) -> Result<Vec<u8>, PlanProcessPortError> {
    let mut bytes = Vec::with_capacity(limit.min(64 * 1024).saturating_add(1));
    reader
        .take(limit.saturating_add(1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| PlanProcessPortError::CaptureFailed)?;
    Ok(bytes)
}
