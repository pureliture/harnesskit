use std::path::PathBuf;
use std::process::{Command, Output};

use serde::{Deserialize, Serialize};

use crate::checkout::RegisteredCheckout;

const RECENT_COMMIT_LIMIT: &str = "5";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RepoStatus {
    pub canonical_path: PathBuf,
    pub current_branch: Option<String>,
    pub detached: bool,
    pub dirty: bool,
    pub recent_commit_summaries: Vec<String>,
}

pub struct RepoInspector;

impl RepoInspector {
    pub fn status(checkout: &RegisteredCheckout) -> Result<RepoStatus, String> {
        let repository = run_git(checkout, &["rev-parse", "--is-inside-work-tree"])?;
        if !repository.status.success() || repository.stdout != b"true\n" {
            return Err("Registered checkout is not a local Git repository".to_string());
        }

        let branch = run_git(checkout, &["symbolic-ref", "--quiet", "--short", "HEAD"])?;
        let (current_branch, detached) = if branch.status.success() {
            let branch = decode_trimmed(branch.stdout)?;
            if branch.is_empty() {
                return Err("Local Git branch state is unavailable".to_string());
            }
            (Some(branch), false)
        } else if branch.status.code() == Some(1) {
            (None, true)
        } else {
            return Err("Local Git branch state is unavailable".to_string());
        };

        let status = run_git(
            checkout,
            &["status", "--porcelain=v1", "--untracked-files=normal"],
        )?;
        if !status.status.success() {
            return Err("Local Git status is unavailable".to_string());
        }

        let log = run_git(
            checkout,
            &[
                "log",
                "--max-count",
                RECENT_COMMIT_LIMIT,
                "--pretty=format:%s",
            ],
        )?;
        if !log.status.success() {
            return Err("Local Git history is unavailable".to_string());
        }
        let recent_commit_summaries = decode_lines(log.stdout)?;

        Ok(RepoStatus {
            canonical_path: checkout.root().to_path_buf(),
            current_branch,
            detached,
            dirty: !status.stdout.is_empty(),
            recent_commit_summaries,
        })
    }
}

fn run_git(checkout: &RegisteredCheckout, args: &[&str]) -> Result<Output, String> {
    Command::new("git")
        .current_dir(checkout.root())
        .args(args)
        .output()
        .map_err(|_| "Local Git inspection is unavailable".to_string())
}

fn decode_trimmed(output: Vec<u8>) -> Result<String, String> {
    String::from_utf8(output)
        .map(|value| value.trim_end_matches(['\r', '\n']).to_string())
        .map_err(|_| "Local Git output is invalid".to_string())
}

fn decode_lines(output: Vec<u8>) -> Result<Vec<String>, String> {
    let output =
        String::from_utf8(output).map_err(|_| "Local Git output is invalid".to_string())?;
    Ok(output.lines().map(str::to_string).collect())
}
