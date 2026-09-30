from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / "docs/runtime-evidence/reference-curator-github"
PROFILE = "harness-maintenance"
DEFAULT_MODEL = "gpt-5.3-codex-spark"
REASONING_EFFORT = "xhigh"
MARKER = "REFERENCE_CURATOR_GITHUB_LIVE: ok"
PACKET_REL_PATH = Path("docs/live-reference-curator/reference-packet.yml")
LOCAL_SOURCE_PREFIXES = (
    "/",
    ".",
    "file:",
    ".codex",
    ".agents",
    "components/",
    "docs/",
    "dist/",
)
LOCAL_SOURCE_MARKERS = (
    "/tmp/",
    "/Users/",
)


def _contract() -> dict[str, Any]:
    return {
        "profile": PROFILE,
        "evidence_root": "docs/runtime-evidence/reference-curator-github",
        "default_model": DEFAULT_MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "marker": MARKER,
        "packet_rel_path": PACKET_REL_PATH.as_posix(),
        "source_mode": "github_open_source_research",
        "source_scope_enforced": True,
        "persisted_outputs": [
            "result.json",
            "summary.md",
            "latest.json",
            "codex-events.jsonl",
            "codex-stderr.txt",
            "last-message.txt",
            "reference-packet.yml",
        ],
        "non_goals": [
            "license approval",
            "requirements writing",
            "blueprint writing",
            "adapter authoring",
            "runtime hooks",
        ],
    }


def _run(
    argv: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        timeout_message = f"command timed out after {timeout_seconds} seconds"
        stderr = (stderr.rstrip() + "\n" + timeout_message).lstrip()
        return subprocess.CompletedProcess(argv, 124, stdout, stderr)


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _prepare_output_dir(output_dir: Path | None, *, overwrite: bool) -> Path:
    path = output_dir or (EVIDENCE_ROOT / _timestamp())
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"output directory already exists: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _materialize_workspace(output_dir: Path) -> tuple[Path, Path]:
    workspace = Path(tempfile.mkdtemp(prefix="rh-reference-curator-github."))
    _run(["git", "init", "-q"], cwd=workspace)

    plan_path = output_dir / "install-plan.apply.json"
    plan = _run(
        [
            sys.executable,
            "scripts/install/plan.py",
            "--profile",
            PROFILE,
            "--mode",
            "apply",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
    )
    (output_dir / "install-plan-stderr.txt").write_text(plan.stderr, encoding="utf-8")
    if plan.returncode != 0:
        raise RuntimeError(plan.stderr)
    plan_path.write_text(plan.stdout, encoding="utf-8")

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
    (output_dir / "install-apply-stdout.txt").write_text(
        apply_result.stdout,
        encoding="utf-8",
    )
    (output_dir / "install-apply-stderr.txt").write_text(
        apply_result.stderr,
        encoding="utf-8",
    )
    if apply_result.returncode != 0:
        raise RuntimeError(apply_result.stderr)
    return workspace, plan_path


def _codex_home(workspace: Path, output_dir: Path) -> tuple[Path, dict[str, str]]:
    codex_home = output_dir / "codex-home"
    codex_home.mkdir()
    real_codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    real_auth = real_codex_home / "auth.json"
    if real_auth.exists():
        (codex_home / "auth.json").symlink_to(real_auth)
    (codex_home / "config.toml").write_text(
        f'[projects."{workspace}"]\ntrust_level = "trusted"\n\n[features]\nhooks = true\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    return codex_home, env


def _prompt() -> str:
    return (
        "Runtime live probe only. Use model gpt-5.3-codex-spark for this run. "
        'Spawn a subagent with agent_type="reference_curator". Ask that subagent '
        "to run in github_open_source_research mode and create "
        "docs/live-reference-curator/reference-packet.yml.\n\n"
        "Research public GitHub/open-source references for coding-agent reference "
        "curation patterns. Candidate external sources should include public "
        "GitHub repositories such as https://github.com/Aider-AI/aider, "
        "https://github.com/All-Hands-AI/OpenHands, and "
        "https://github.com/continuedev/continue when available. Do not use local "
        "repository files, installed local skills, generated outputs, workspace "
        "docs, .codex files, .agents files, components files, or docs files as "
        "candidate reference sources. It is acceptable for the runtime to load this "
        "agent configuration, but local files must not appear under sources.\n\n"
        "The packet must include source_mode: github_open_source_research, "
        "source_scope_enforced: true, sources, exclusions, "
        "context_inspected_not_sources, and open_questions. Every "
        "sources[].url_or_path must be a public https URL, preferably a github.com "
        "URL. Local path values are forbidden: no source may start with /, ., "
        "file:, .codex, .agents, components/, docs/, or dist/, or contain /tmp/ "
        "or /Users/. Public HTTPS GitHub URLs may point to repository subpaths.\n\n"
        "After the subagent finishes, inspect "
        "docs/live-reference-curator/reference-packet.yml. Final-answer exactly "
        f"{MARKER} if the file exists, source_mode is github_open_source_research, "
        "source_scope_enforced is true, and every source url_or_path is a public "
        "https URL. Otherwise final-answer REFERENCE_CURATOR_GITHUB_LIVE: "
        "unavailable and include the failing condition only."
    )


def validate_packet(packet_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    if not packet_path.is_file():
        return {
            "packet_path": str(packet_path),
            "source_count": 0,
            "source_urls": [],
            "errors": ["missing_packet"],
        }

    data = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {
            "packet_path": str(packet_path),
            "source_count": 0,
            "source_urls": [],
            "errors": ["packet_not_mapping"],
        }

    if data.get("source_mode") != "github_open_source_research":
        errors.append("source_mode")
    if data.get("source_scope_enforced") is not True:
        errors.append("source_scope_enforced")

    sources = data.get("sources") or []
    if not isinstance(sources, list) or not sources:
        errors.append("sources_empty")
        sources = []

    source_urls: list[str] = []
    for index, source in enumerate(sources):
        url = source.get("url_or_path") if isinstance(source, dict) else None
        is_public_https = isinstance(url, str) and url.startswith("https://")
        if not is_public_https:
            errors.append(f"source_{index}_not_https")
        if not isinstance(url, str):
            continue
        if is_public_https:
            source_urls.append(url)
        has_local_prefix = not is_public_https and url.startswith(LOCAL_SOURCE_PREFIXES)
        has_local_marker = any(part in url for part in LOCAL_SOURCE_MARKERS)
        if has_local_prefix or has_local_marker:
            errors.append(f"source_{index}_local_or_blocked")

    return {
        "packet_path": str(packet_path),
        "source_count": len(sources),
        "source_urls": source_urls,
        "errors": errors,
    }


def _write_summary(output_dir: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Reference Curator GitHub Runtime Evidence",
        "",
        f"- passed: `{result['passed']}`",
        f"- model: `{result['model']}`",
        f"- reasoning_effort: `{result['reasoning_effort']}`",
        f"- marker: `{result['marker']}`",
        f"- source_count: `{result['validation']['source_count']}`",
        f"- validation_errors: `{', '.join(result['validation']['errors']) or 'none'}`",
        "",
        "## Source URLs",
    ]
    lines.extend(f"- {url}" for url in result["validation"]["source_urls"])
    lines.extend(["", "## Files"])
    for key in ["events", "stderr", "last_message", "packet_copy", "install_plan"]:
        lines.append(f"- {key}: `{result[key]}`")
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_latest(output_dir: Path, result: dict[str, Any]) -> None:
    try:
        rel_output_dir = output_dir.resolve().relative_to(REPO_ROOT).as_posix()
        latest_dir = EVIDENCE_ROOT
    except ValueError:
        rel_output_dir = str(output_dir.resolve())
        latest_dir = output_dir
    latest_dir.mkdir(parents=True, exist_ok=True)
    latest = {
        "evidence_dir": rel_output_dir,
        "passed": result["passed"],
        "model": result["model"],
        "reasoning_effort": result["reasoning_effort"],
        "source_count": result["validation"]["source_count"],
        "validation_errors": result["validation"]["errors"],
        "result_json": str((output_dir / "result.json").resolve()),
        "summary_md": str((output_dir / "summary.md").resolve()),
    }
    (latest_dir / "latest.json").write_text(
        json.dumps(latest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> int:
    output_dir = _prepare_output_dir(args.output_dir, overwrite=args.overwrite)
    workspace: Path | None = None
    try:
        workspace, plan_path = _materialize_workspace(output_dir)
        _codex_home(workspace, output_dir)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(output_dir / "codex-home")

        events_path = output_dir / "codex-events.jsonl"
        stderr_path = output_dir / "codex-stderr.txt"
        last_message_path = output_dir / "last-message.txt"
        command = [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(workspace),
            "--enable",
            "multi_agent",
            "--model",
            args.codex_model,
            "-c",
            f'model_reasoning_effort="{REASONING_EFFORT}"',
            "--output-last-message",
            str(last_message_path),
            _prompt(),
        ]
        probe_result = _run(command, cwd=REPO_ROOT, env=env)
        events_path.write_text(probe_result.stdout, encoding="utf-8")
        stderr_path.write_text(probe_result.stderr, encoding="utf-8")

        packet_path = workspace / PACKET_REL_PATH
        validation = validate_packet(packet_path)
        packet_copy = output_dir / "reference-packet.yml"
        if packet_path.is_file():
            shutil.copy2(packet_path, packet_copy)

        last_message = (
            last_message_path.read_text(encoding="utf-8")
            if last_message_path.is_file()
            else ""
        )
        result = {
            "passed": (
                probe_result.returncode == 0
                and MARKER in last_message
                and validation["errors"] == []
            ),
            "returncode": probe_result.returncode,
            "marker": MARKER,
            "model": args.codex_model,
            "reasoning_effort": REASONING_EFFORT,
            "workspace": str(workspace),
            "install_plan": str(plan_path),
            "events": str(events_path),
            "stderr": str(stderr_path),
            "last_message": str(last_message_path),
            "packet": str(packet_path),
            "packet_copy": str(packet_copy) if packet_copy.is_file() else "",
            "validation": validation,
            "contract": _contract(),
        }
        (output_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_summary(output_dir, result)
        _write_latest(output_dir, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1
    finally:
        if workspace is not None and not args.keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a live Codex probe for reference_curator GitHub source scope."
    )
    parser.add_argument("--list-probes", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--codex-model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)

    if args.list_probes:
        print(json.dumps(_contract(), indent=2, sort_keys=True))
        return 0
    if args.codex_model != DEFAULT_MODEL:
        parser.error(f"--codex-model must be {DEFAULT_MODEL}")
    try:
        return run(args)
    except (FileExistsError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
