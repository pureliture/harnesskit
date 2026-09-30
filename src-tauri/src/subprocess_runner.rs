use std::ffi::OsString;
use std::path::Path;
use std::process::Command;

use crate::checkout::RegisteredCheckout;

pub struct SubprocessRunner;

#[derive(Debug)]
pub struct SubprocessResult {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum InstallPlanMode {
    DryRun,
    Apply,
}

impl InstallPlanMode {
    fn as_str(self) -> &'static str {
        match self {
            Self::DryRun => "dry-run",
            Self::Apply => "apply",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum InstallScope {
    User,
    Project,
}

impl InstallScope {
    fn as_str(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Project => "project",
        }
    }
}

impl TryFrom<&str> for InstallScope {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "user" => Ok(Self::User),
            "project" => Ok(Self::Project),
            _ => Err("Install scope must be user or project".to_string()),
        }
    }
}

impl SubprocessRunner {
    fn execute_python(
        script: &Path,
        args: &[OsString],
        cwd: &Path,
    ) -> Result<SubprocessResult, String> {
        let output = Command::new("python3")
            .arg(script)
            .args(args)
            .current_dir(cwd)
            .output()
            .map_err(|e| format!("Failed to execute python3: {e}"))?;

        Ok(SubprocessResult {
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
            exit_code: output.status.code().unwrap_or(-1),
        })
    }

    pub fn run_build_components(
        checkout: &RegisteredCheckout,
        component_ids: &[String],
    ) -> Result<SubprocessResult, String> {
        if component_ids.is_empty() {
            return Err("At least one registered component is required".to_string());
        }

        let mut sorted_ids = component_ids.to_vec();
        sorted_ids.sort();
        sorted_ids.dedup();

        let mut args = Vec::with_capacity(sorted_ids.len() * 2);
        for component_id in sorted_ids {
            if component_id.is_empty() {
                return Err("Component ids must not be empty".to_string());
            }
            args.push(OsString::from("--component"));
            args.push(OsString::from(component_id));
        }

        let build_script = checkout.build_script()?;
        Self::execute_python(&build_script, &args, checkout.root())
    }

    pub(crate) fn run_install_plan(
        checkout: &RegisteredCheckout,
        profile: &str,
        scope: InstallScope,
        mode: InstallPlanMode,
    ) -> Result<SubprocessResult, String> {
        if profile.is_empty() {
            return Err("Install profile must not be empty".to_string());
        }

        let args = vec![
            OsString::from("--profile"),
            OsString::from(profile),
            OsString::from("--scope"),
            OsString::from(scope.as_str()),
            OsString::from("--mode"),
            OsString::from(mode.as_str()),
            OsString::from("--format"),
            OsString::from("json"),
        ];
        let plan_script = checkout.install_plan_script()?;
        Self::execute_python(&plan_script, &args, checkout.root())
    }

    pub(crate) fn run_install_apply(
        checkout: &RegisteredCheckout,
        plan_file: &Path,
        target_root: &Path,
        overwrite: bool,
        allow_runtime_hooks: bool,
    ) -> Result<SubprocessResult, String> {
        let args = vec![
            plan_file.as_os_str().to_owned(),
            OsString::from("--target-root"),
            target_root.as_os_str().to_owned(),
        ];
        let mut args = args;
        if overwrite {
            args.push(OsString::from("--overwrite"));
        }
        if allow_runtime_hooks {
            args.push(OsString::from("--allow-runtime-hooks"));
        }

        let apply_script = checkout.install_apply_script()?;
        Self::execute_python(&apply_script, &args, checkout.root())
    }

    pub(crate) fn run_install_verify(
        checkout: &RegisteredCheckout,
        plan_file: &Path,
        target_root: &Path,
    ) -> Result<SubprocessResult, String> {
        let args = vec![
            plan_file.as_os_str().to_owned(),
            OsString::from("--target-root"),
            target_root.as_os_str().to_owned(),
        ];
        let verify_script = checkout.install_verify_script()?;
        Self::execute_python(&verify_script, &args, checkout.root())
    }
}
