from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = "scm"
EVIDENCE_ROOT = REPO_ROOT / "docs" / "runtime-evidence" / "github-workflow"

SKILL_PROBES = [
    {
        "name": "github-workflow-policy",
        "marker": "GITHUB_WORKFLOW_POLICY_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the github-workflow-policy skill from this workspace. "
            "Do not perform any GitHub mutation. Final-answer exactly "
            "GITHUB_WORKFLOW_POLICY_SKILL_RUNTIME: ok if the skill body includes "
            "Policy Scope and Install Boundary Policy; otherwise final-answer "
            "GITHUB_WORKFLOW_POLICY_SKILL_RUNTIME: unavailable."
        ),
    },
    {
        "name": "github-issue",
        "marker": "GITHUB_ISSUE_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the github-issue skill from this workspace. Do not create an issue. "
            "Final-answer exactly GITHUB_ISSUE_SKILL_RUNTIME: ok if the skill body "
            "has Consent Gate and Project Registration sections; otherwise final-answer "
            "GITHUB_ISSUE_SKILL_RUNTIME: unavailable."
        ),
    },
    {
        "name": "github-start",
        "marker": "GITHUB_START_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the github-start skill from this workspace. Do not mutate GitHub or git. "
            "Final-answer exactly GITHUB_START_SKILL_RUNTIME: ok if the skill body "
            "delegates lifecycle work to worktree-lifecycle; otherwise final-answer "
            "GITHUB_START_SKILL_RUNTIME: unavailable."
        ),
    },
    {
        "name": "github-pr",
        "marker": "GITHUB_PR_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the github-pr skill from this workspace. Do not create a PR. "
            "Final-answer exactly GITHUB_PR_SKILL_RUNTIME: ok if the skill body "
            "requires a linked issue closing reference and actual git diff; otherwise "
            "final-answer GITHUB_PR_SKILL_RUNTIME: unavailable."
        ),
    },
    {
        "name": "github-pr-followup",
        "marker": "GITHUB_PR_FOLLOWUP_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the github-pr-followup skill from this workspace. Do not mutate GitHub or git. "
            "Final-answer exactly GITHUB_PR_FOLLOWUP_SKILL_RUNTIME: ok if the skill body "
            "classifies review threads into immediate fix, deferred issue, and user decision; "
            "otherwise final-answer GITHUB_PR_FOLLOWUP_SKILL_RUNTIME: unavailable."
        ),
    },
]

CODEX_AGENT_MARKER = "GITHUB_MANAGER_CODEX_AGENT_RUNTIME: ok"
CLAUDE_AGENT_MARKER = "GITHUB_MANAGER_CLAUDE_AGENT_RUNTIME: ok"
CODEX_PROBE_MODEL = os.environ.get("CODEX_GITHUB_WORKFLOW_PROBE_MODEL", "gpt-5.5")
CODEX_PROBE_REASONING_EFFORT = os.environ.get(
    "CODEX_GITHUB_WORKFLOW_PROBE_REASONING_EFFORT",
    "medium",
)


def _coerce_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_s: int = 600,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            argv,
            124,
            _coerce_timeout_output(exc.stdout),
            _coerce_timeout_output(exc.stderr) + f"\nTIMEOUT after {timeout_s}s\n",
        )


def _build_outputs(output_dir: Path) -> dict[str, Any]:
    build = _run([sys.executable, "scripts/adapters/build.py", "--profile", PROFILE], cwd=REPO_ROOT)
    check = _run(
        [sys.executable, "scripts/adapters/build.py", "--profile", PROFILE, "--check"],
        cwd=REPO_ROOT,
    )
    (output_dir / "adapter-build-stdout.txt").write_text(build.stdout, encoding="utf-8")
    (output_dir / "adapter-build-stderr.txt").write_text(build.stderr, encoding="utf-8")
    (output_dir / "adapter-check-stdout.txt").write_text(check.stdout, encoding="utf-8")
    (output_dir / "adapter-check-stderr.txt").write_text(check.stderr, encoding="utf-8")
    return {
        "build_returncode": build.returncode,
        "check_returncode": check.returncode,
        "passed": build.returncode == 0 and check.returncode == 0,
    }


def _prepare_workspace(output_dir: Path) -> tuple[Path, Path]:
    workspace = Path(tempfile.mkdtemp(prefix="harnesskit-github-workflow."))
    (workspace / "README.md").write_text("# GitHub Workflow Probe\n", encoding="utf-8")
    git_init = _run(["git", "init", "-q"], cwd=workspace)
    if git_init.returncode != 0:
        raise RuntimeError(git_init.stderr)

    plan_path = output_dir / "install-plan.apply.json"
    plan = _run(
        [
            sys.executable,
            "scripts/install/plan.py",
            "--profile",
            PROFILE,
            "--scope",
            "project",
            "--mode",
            "apply",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
    )
    plan_path.write_text(plan.stdout, encoding="utf-8")
    (output_dir / "install-plan-stderr.txt").write_text(plan.stderr, encoding="utf-8")
    if plan.returncode != 0:
        raise RuntimeError(plan.stderr)

    apply_result = _run(
        [
            sys.executable,
            "scripts/install/apply.py",
            str(plan_path),
            "--target-root",
            str(workspace),
            "--overwrite",
        ],
        cwd=REPO_ROOT,
    )
    (output_dir / "install-apply-stdout.txt").write_text(apply_result.stdout, encoding="utf-8")
    (output_dir / "install-apply-stderr.txt").write_text(apply_result.stderr, encoding="utf-8")
    if apply_result.returncode != 0:
        raise RuntimeError(apply_result.stderr)
    return workspace, plan_path


def _user_scope_rejection(output_dir: Path) -> dict[str, Any]:
    stdout_path = output_dir / "install-plan-user-scope-stdout.txt"
    stderr_path = output_dir / "install-plan-user-scope-stderr.txt"
    result = _run(
        [
            sys.executable,
            "scripts/install/plan.py",
            "--profile",
            PROFILE,
            "--scope",
            "user",
            "--mode",
            "dry-run",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    return {
        "returncode": result.returncode,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "expected_error": "scope is not allowed: user",
        "passed": result.returncode != 0 and "scope is not allowed: user" in result.stderr,
    }


def _project_local_install_contract(plan_path: Path) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    destinations = [artifact["destination"] for artifact in plan["artifacts"]]
    non_project_local = [
        destination
        for destination in destinations
        if destination.startswith("/") or destination.startswith("~") or ".." in Path(destination).parts
    ]
    return {
        "scope": plan.get("scope"),
        "mode": plan.get("mode"),
        "artifact_count": len(destinations),
        "non_project_local_destinations": non_project_local,
        "passed": plan.get("scope") == "project"
        and plan.get("mode") == "apply"
        and not non_project_local,
    }


def _probe_codex_home(workspace: Path) -> Path:
    codex_home = Path(tempfile.mkdtemp(prefix="harnesskit-github-workflow-codex-home."))
    real_codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    real_auth = real_codex_home / "auth.json"
    if real_auth.exists():
        (codex_home / "auth.json").symlink_to(real_auth)
    (codex_home / "config.toml").write_text(
        f'[projects."{workspace}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    return codex_home


def _codex_skill(workspace: Path, output_dir: Path, env: dict[str, str], probe: dict[str, str]) -> dict[str, Any]:
    name = probe["name"]
    last_message = output_dir / f"codex-skill-{name}-last-message.txt"
    stdout_path = output_dir / f"codex-skill-{name}.jsonl"
    stderr_path = output_dir / f"codex-skill-{name}-stderr.txt"
    result = _run(
        [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--cd",
            str(workspace),
            "--model",
            CODEX_PROBE_MODEL,
            "-c",
            f'model_reasoning_effort="{CODEX_PROBE_REASONING_EFFORT}"',
            "--output-last-message",
            str(last_message),
            probe["prompt"],
        ],
        env=env,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    message = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    return {
        "name": name,
        "returncode": result.returncode,
        "marker": probe["marker"],
        "last_message": message.strip(),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "passed": result.returncode == 0 and probe["marker"] in message,
    }


def _codex_agent(workspace: Path, output_dir: Path, env: dict[str, str]) -> dict[str, Any]:
    last_message = output_dir / "codex-agent-github-manager-last-message.txt"
    stdout_path = output_dir / "codex-agent-github-manager.jsonl"
    stderr_path = output_dir / "codex-agent-github-manager-stderr.txt"
    prompt = (
        "Runtime probe only. Spawn a subagent with agent_type='github_manager' "
        "and ask it to final-answer exactly GITHUB_MANAGER_CODEX_AGENT_RUNTIME: ok "
        "if it can see GitHub Manager routing instructions for github-issue, "
        "github-start, github-pr, and github-pr-followup. Parent final-answer exactly "
        "GITHUB_MANAGER_CODEX_AGENT_RUNTIME: ok if the subagent succeeded; otherwise "
        "final-answer GITHUB_MANAGER_CODEX_AGENT_RUNTIME: unavailable."
    )
    result = _run(
        [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--cd",
            str(workspace),
            "--model",
            CODEX_PROBE_MODEL,
            "-c",
            f'model_reasoning_effort="{CODEX_PROBE_REASONING_EFFORT}"',
            "--enable",
            "multi_agent",
            "--output-last-message",
            str(last_message),
            prompt,
        ],
        env=env,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    message = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    return {
        "name": "github_manager",
        "returncode": result.returncode,
        "marker": CODEX_AGENT_MARKER,
        "last_message": message.strip(),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "passed": result.returncode == 0 and CODEX_AGENT_MARKER in message,
    }


def _claude_skill(workspace: Path, output_dir: Path, probe: dict[str, str]) -> dict[str, Any]:
    name = probe["name"]
    stdout_path = output_dir / f"claude-skill-{name}.jsonl"
    stderr_path = output_dir / f"claude-skill-{name}-stderr.txt"
    result = _run(
        [
            "claude",
            "-p",
            "--verbose",
            "--setting-sources",
            "project",
            "--output-format",
            "stream-json",
            "--include-hook-events",
            probe["prompt"],
        ],
        cwd=workspace,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    return {
        "name": name,
        "returncode": result.returncode,
        "marker": probe["marker"],
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "passed": result.returncode == 0 and probe["marker"] in result.stdout,
    }


def _claude_agent(workspace: Path, output_dir: Path) -> dict[str, Any]:
    stdout_path = output_dir / "claude-agent-github-manager.jsonl"
    stderr_path = output_dir / "claude-agent-github-manager-stderr.txt"
    prompt = (
        "Runtime probe only. Final-answer exactly GITHUB_MANAGER_CLAUDE_AGENT_RUNTIME: ok "
        "if your agent instructions identify you as GitHub Manager and route issue, "
        "start-work, PR, and PR follow-up intents. Otherwise final-answer exactly "
        "GITHUB_MANAGER_CLAUDE_AGENT_RUNTIME: unavailable."
    )
    result = _run(
        [
            "claude",
            "-p",
            "--verbose",
            "--setting-sources",
            "project",
            "--agent",
            "github-manager",
            "--output-format",
            "stream-json",
            "--include-hook-events",
            prompt,
        ],
        cwd=workspace,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    return {
        "name": "github-manager",
        "returncode": result.returncode,
        "marker": CLAUDE_AGENT_MARKER,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "passed": result.returncode == 0 and CLAUDE_AGENT_MARKER in result.stdout,
    }


def _installed_files(workspace: Path) -> dict[str, bool]:
    paths = [
        ".claude/skills/github-workflow-policy/SKILL.md",
        ".claude/skills/github-issue/SKILL.md",
        ".claude/skills/github-start/SKILL.md",
        ".claude/skills/github-pr/SKILL.md",
        ".claude/skills/github-pr-followup/SKILL.md",
        ".claude/agents/github-manager.md",
        ".agents/skills/github-workflow-policy/SKILL.md",
        ".agents/skills/github-issue/SKILL.md",
        ".agents/skills/github-start/SKILL.md",
        ".agents/skills/github-pr/SKILL.md",
        ".agents/skills/github-pr-followup/SKILL.md",
        ".codex/agents/github_manager.toml",
        ".codex/config.toml",
    ]
    return {path: (workspace / path).is_file() for path in paths}


def _target_passed(result: dict[str, Any], target: str) -> bool:
    target_result = result.get(target)
    if not isinstance(target_result, dict):
        return False
    skill_results = target_result.get("skills") or []
    agent_result = target_result.get("agent") or {}
    return all(item.get("passed") for item in skill_results) and bool(agent_result.get("passed"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe GitHub Workflow HarnessKit runtime surfaces.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--target", action="append", choices=["codex", "claude"], default=[])
    parser.add_argument("--keep-workspace", action="store_true")
    args = parser.parse_args(argv)

    targets = args.target or ["codex", "claude"]
    output_dir = args.output_dir or EVIDENCE_ROOT / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace: Path | None = None
    codex_home: Path | None = None

    try:
        build = _build_outputs(output_dir)
        workspace, plan_path = _prepare_workspace(output_dir)
        result: dict[str, Any] = {
            "profile": f"harnesskit.profile.{PROFILE}",
            "targets": targets,
            "output_dir": str(output_dir),
            "workspace": str(workspace),
            "install_plan": str(plan_path),
            "adapter_static": build,
            "project_local_install_contract": _project_local_install_contract(plan_path),
            "user_scope_rejection": _user_scope_rejection(output_dir),
            "installed_files": _installed_files(workspace),
        }

        if "codex" in targets:
            version = _run(["codex", "--version"])
            codex_home = _probe_codex_home(workspace)
            env = os.environ.copy()
            env["CODEX_HOME"] = str(codex_home)
            result["codex"] = {
                "version": version.stdout.strip(),
                "model": CODEX_PROBE_MODEL,
                "reasoning_effort": CODEX_PROBE_REASONING_EFFORT,
                "codex_home": str(codex_home),
                "skills": [_codex_skill(workspace, output_dir, env, probe) for probe in SKILL_PROBES],
                "agent": _codex_agent(workspace, output_dir, env),
            }

        if "claude" in targets:
            version = _run(["claude", "--version"])
            result["claude"] = {
                "version": version.stdout.strip(),
                "skills": [_claude_skill(workspace, output_dir, probe) for probe in SKILL_PROBES],
                "agent": _claude_agent(workspace, output_dir),
            }

        result["passed"] = (
            build["passed"]
            and result["project_local_install_contract"]["passed"]
            and result["user_scope_rejection"]["passed"]
            and all(result["installed_files"].values())
            and all(_target_passed(result, target) for target in targets)
        )
        (output_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1
    finally:
        if workspace is not None and not args.keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)
        if codex_home is not None:
            shutil.rmtree(codex_home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
