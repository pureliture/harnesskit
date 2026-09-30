from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


TEMP_PREFIX = "rh-agy-"
DEFAULT_REF_PREFIX = "/tmp/rh-agy-"
DEFAULT_RESIDUE_PREFIXES = (
    "/tmp/rh-agy-",
    "/private/tmp/rh-agy-",
    "/tmp/claude-",
    "/private/tmp/claude-",
)
DEFAULT_RESIDUE_MARKERS = (
    "/.claude/jobs/",
    "/.claude/worktrees/",
    "/.aco-worktrees/",
)
DEFAULT_CACHE_FILES = ("projects.json", "last_conversations.json")
DEFAULT_PROMPT = "optimal-response RICH 동작 대신 기본 STOP hook context만 확인해줘."


@dataclass(frozen=True)
class ProbeLayout:
    temp_root: Path
    workspace: Path
    home: Path
    config_dir: Path
    app_data_dir: Path
    cache_dir: Path
    state_dir: Path
    sqlite_path: Path

    @classmethod
    def from_temp_root(cls, temp_root: Path) -> "ProbeLayout":
        root = temp_root.expanduser().resolve()
        return cls(
            temp_root=root,
            workspace=root / "workspace",
            home=root / "home",
            config_dir=root / "config",
            app_data_dir=root / "appData",
            cache_dir=root / "cache",
            state_dir=root / "state",
            sqlite_path=root / "appData" / "optimal-response" / "mode-events.sqlite",
        )


@dataclass(frozen=True)
class RegistryCleanup:
    removed: list[Path]
    failed: list[Path]


@dataclass(frozen=True)
class CacheCleanup:
    removed: dict[Path, list[str]]
    failed: dict[Path, str]
    dry_run: bool


def _default_temp_root() -> Path:
    parent = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=str(parent)))


def isolated_project_registry_dir(layout: ProbeLayout) -> Path:
    return layout.home / ".gemini" / "config" / "projects"


def isolated_agy_env(base_env: Mapping[str, str], layout: ProbeLayout) -> dict[str, str]:
    env = dict(base_env)
    env.update(
        {
            "HOME": str(layout.home),
            "XDG_CONFIG_HOME": str(layout.config_dir),
            "XDG_DATA_HOME": str(layout.app_data_dir),
            "XDG_CACHE_HOME": str(layout.cache_dir),
            "APPDATA": str(layout.app_data_dir),
            "ANTIGRAVITY_CONFIG_HOME": str(layout.config_dir),
            "ANTIGRAVITY_APP_DATA_DIR": str(layout.app_data_dir),
            "GEMINI_CONFIG_HOME": str(layout.config_dir),
            "GEMINI_HOME": str(layout.home / ".gemini"),
            "OPTIMAL_RESPONSE_SURFACE": "antigravity",
            "OPTIMAL_RESPONSE_STATE_DIR": str(layout.state_dir),
            "OPTIMAL_RESPONSE_SQLITE_PATH": str(layout.sqlite_path),
        }
    )
    return env


def _ensure_layout_dirs(layout: ProbeLayout) -> None:
    for directory in [
        layout.workspace,
        layout.home,
        layout.config_dir,
        layout.app_data_dir,
        layout.cache_dir,
        layout.state_dir,
        isolated_project_registry_dir(layout),
        layout.sqlite_path.parent,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def _ref_prefixes(ref_prefix: str, extra_ref_prefixes: Sequence[str] | None = None) -> list[str]:
    values = [ref_prefix]
    if extra_ref_prefixes:
        values.extend(extra_ref_prefixes)
    return [value for value in values if value]


def _is_probe_or_stale_worker_path(
    value: str,
    *,
    prefixes: Sequence[str] = DEFAULT_RESIDUE_PREFIXES,
    markers: Sequence[str] = DEFAULT_RESIDUE_MARKERS,
) -> bool:
    if any(value.startswith(prefix) for prefix in prefixes):
        return True
    if any(marker in value for marker in markers):
        return not Path(value).exists()
    return False


def scan_project_registry_refs(
    projects_dir: Path,
    *,
    ref_prefix: str = DEFAULT_REF_PREFIX,
    extra_ref_prefixes: Sequence[str] | None = None,
) -> list[Path]:
    if not projects_dir.exists():
        return []

    prefixes = _ref_prefixes(ref_prefix, extra_ref_prefixes)
    matches: list[Path] = []
    for path in sorted(projects_dir.glob("*.json")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(prefix in text for prefix in prefixes):
            matches.append(path)
    return matches


def cleanup_project_registry_refs(
    projects_dir: Path,
    *,
    ref_prefix: str = DEFAULT_REF_PREFIX,
    extra_ref_prefixes: Sequence[str] | None = None,
) -> RegistryCleanup:
    removed: list[Path] = []
    failed: list[Path] = []
    for path in scan_project_registry_refs(
        projects_dir,
        ref_prefix=ref_prefix,
        extra_ref_prefixes=extra_ref_prefixes,
    ):
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            failed.append(path)
    return RegistryCleanup(removed=removed, failed=failed)


def scan_cli_cache_refs(
    cache_dir: Path,
    *,
    cache_files: Sequence[str] = DEFAULT_CACHE_FILES,
    prefixes: Sequence[str] = DEFAULT_RESIDUE_PREFIXES,
    markers: Sequence[str] = DEFAULT_RESIDUE_MARKERS,
) -> dict[Path, list[str]]:
    matches: dict[Path, list[str]] = {}
    for name in cache_files:
        path = cache_dir / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        keys = [
            key
            for key in sorted(data)
            if isinstance(key, str)
            and _is_probe_or_stale_worker_path(key, prefixes=prefixes, markers=markers)
        ]
        if keys:
            matches[path] = keys
    return matches


def cleanup_cli_cache_refs(
    cache_dir: Path,
    *,
    cache_files: Sequence[str] = DEFAULT_CACHE_FILES,
    prefixes: Sequence[str] = DEFAULT_RESIDUE_PREFIXES,
    markers: Sequence[str] = DEFAULT_RESIDUE_MARKERS,
    dry_run: bool = True,
) -> CacheCleanup:
    removed: dict[Path, list[str]] = {}
    failed: dict[Path, str] = {}
    for path, keys in scan_cli_cache_refs(
        cache_dir,
        cache_files=cache_files,
        prefixes=prefixes,
        markers=markers,
    ).items():
        removed[path] = keys
        if dry_run:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            for key in keys:
                data.pop(key, None)
            _write_json(path, data)
        except (OSError, json.JSONDecodeError) as exc:
            failed[path] = str(exc)
    return CacheCleanup(removed=removed, failed=failed, dry_run=dry_run)


def _default_global_projects_dir() -> Path:
    return Path.home() / ".gemini" / "config" / "projects"


def _default_global_cache_dir() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "cache"


def _resolve_agents_bundle(bundle_root: Path) -> Path:
    root = bundle_root.expanduser().resolve()
    if root.name == ".agents" and root.is_dir():
        return root
    nested = root / ".agents"
    if nested.is_dir():
        return nested
    raise FileNotFoundError(f"missing .agents bundle under {bundle_root}")


def _copy_agents_bundle(bundle_root: Path, workspace: Path) -> Path:
    src = _resolve_agents_bundle(bundle_root)
    dest = workspace / ".agents"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_agy_print(
    *,
    agy_bin: str,
    layout: ProbeLayout,
    output_dir: Path,
    env: Mapping[str, str],
    prompt: str,
    print_timeout: str,
    subprocess_timeout: int,
) -> dict:
    log_path = output_dir / "agy.log"
    command = [
        agy_bin,
        "--print",
        "--dangerously-skip-permissions",
        "--log-file",
        str(log_path),
        "--print-timeout",
        print_timeout,
        prompt,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=layout.workspace,
            env=dict(env),
            text=True,
            capture_output=True,
            check=False,
            timeout=subprocess_timeout,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode: int | None = completed.returncode
        error = None
    except (OSError, subprocess.TimeoutExpired) as exc:
        stdout = _coerce_text(getattr(exc, "stdout", ""))
        stderr = _coerce_text(getattr(exc, "stderr", ""))
        returncode = None
        error = str(exc)

    (output_dir / "agy-stdout.txt").write_text(stdout, encoding="utf-8")
    (output_dir / "agy-stderr.txt").write_text(stderr, encoding="utf-8")
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
    loaded_hooks = "Loaded hooks.json" in log_text and ".agents/hooks.json" in log_text
    executed_hook = "executing command" in log_text and "optimal-response-prompt-submit" in log_text
    return {
        "returncode": returncode,
        "error": error,
        "log_path": str(log_path),
        "stdout_path": str(output_dir / "agy-stdout.txt"),
        "stderr_path": str(output_dir / "agy-stderr.txt"),
        "loaded_workspace_hooks": loaded_hooks,
        "executed_optimal_response_hook": executed_hook,
        "passed": returncode == 0 and loaded_hooks and executed_hook,
    }


def _write_transcript(layout: ProbeLayout) -> Path:
    transcript = layout.app_data_dir / "transcript.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps(
            {
                "step_index": 1,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "created_at": "2026-06-06T00:00:00.000Z",
                "content": DEFAULT_PROMPT,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return transcript


def _run_payload_capture(layout: ProbeLayout, output_dir: Path, env: Mapping[str, str]) -> dict:
    hook = layout.workspace / ".agents" / "skills" / "optimal-response" / "hooks" / "stop-prompt-submit.cjs"
    if not hook.is_file():
        return {"passed": False, "error": f"missing hook asset: {hook}"}

    transcript = _write_transcript(layout)
    payload = {
        "artifactDirectoryPath": str(layout.app_data_dir / "artifacts"),
        "conversationId": "rh-agy-probe",
        "initialNumSteps": 1,
        "invocationNum": 1,
        "transcriptPath": str(transcript),
        "workspacePaths": [str(layout.workspace)],
    }
    stdout_path = output_dir / "payload-hook-stdout.json"
    stderr_path = output_dir / "payload-hook-stderr.txt"
    payload_path = output_dir / "payload.json"
    try:
        result = subprocess.run(
            ["node", "skills/optimal-response/hooks/stop-prompt-submit.cjs"],
            cwd=layout.workspace / ".agents",
            env=dict(env),
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        stdout = result.stdout
        stderr = result.stderr
        returncode: int | None = result.returncode
        error = None
    except (OSError, subprocess.TimeoutExpired) as exc:
        stdout = _coerce_text(getattr(exc, "stdout", ""))
        stderr = _coerce_text(getattr(exc, "stderr", ""))
        returncode = None
        error = str(exc)

    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    _write_json(payload_path, payload)

    try:
        parsed = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        parsed = {}
    inject_steps = parsed.get("injectSteps")
    mode_events = layout.state_dir / "mode-events.jsonl"
    return {
        "returncode": returncode,
        "error": error,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "payload_path": str(payload_path),
        "has_inject_steps": isinstance(inject_steps, list) and bool(inject_steps),
        "has_mode_events": mode_events.is_file(),
        "sqlite_path": str(layout.sqlite_path),
        "sqlite_exists": layout.sqlite_path.is_file(),
        "passed": returncode == 0
        and isinstance(inject_steps, list)
        and bool(inject_steps)
        and mode_events.is_file(),
    }


def run_probe(
    *,
    agy_bin: str = "agy",
    bundle_root: Path = Path("dist/antigravity-cli/.agents"),
    output_dir: Path | None = None,
    global_projects_dir: Path | None = None,
    global_cache_dir: Path | None = None,
    temp_root: Path | None = None,
    prompt: str = DEFAULT_PROMPT,
    print_timeout: str = "90s",
    subprocess_timeout: int = 120,
    run_payload_capture: bool = True,
    keep_workspace: bool = False,
) -> dict:
    evidence_dir = output_dir or Path(tempfile.mkdtemp(prefix="harnesskit-agy-evidence."))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    root = temp_root.expanduser().resolve() if temp_root is not None else _default_temp_root()
    layout = ProbeLayout.from_temp_root(root)
    projects_dir = (global_projects_dir or _default_global_projects_dir()).expanduser().resolve()
    cache_dir = (global_cache_dir or _default_global_cache_dir()).expanduser().resolve()
    ref_prefixes = [str(layout.temp_root), DEFAULT_REF_PREFIX]
    result: dict = {}
    try:
        _ensure_layout_dirs(layout)
        _copy_agents_bundle(bundle_root, layout.workspace)
        env = isolated_agy_env(os.environ, layout)
        agy_result = _run_agy_print(
            agy_bin=agy_bin,
            layout=layout,
            output_dir=evidence_dir,
            env=env,
            prompt=prompt,
            print_timeout=print_timeout,
            subprocess_timeout=subprocess_timeout,
        )
        payload_result = (
            _run_payload_capture(layout, evidence_dir, env)
            if run_payload_capture
            else {"passed": True, "skipped": True}
        )
        registry_cleanup = cleanup_project_registry_refs(
            projects_dir,
            ref_prefix=DEFAULT_REF_PREFIX,
            extra_ref_prefixes=[str(layout.temp_root)],
        )
        registry_refs = scan_project_registry_refs(
            projects_dir,
            ref_prefix=DEFAULT_REF_PREFIX,
            extra_ref_prefixes=[str(layout.temp_root)],
        )
        cache_cleanup = cleanup_cli_cache_refs(
            cache_dir,
            prefixes=tuple(ref_prefixes),
            markers=(),
            dry_run=False,
        )
        cache_refs = scan_cli_cache_refs(
            cache_dir,
            prefixes=tuple(ref_prefixes),
            markers=(),
        )
        result = {
            "passed": bool(
                agy_result["passed"]
                and payload_result["passed"]
                and not registry_cleanup.failed
                and not registry_refs
                and not cache_cleanup.failed
                and not cache_refs
            ),
            "temp_root": str(layout.temp_root),
            "workspace": str(layout.workspace),
            "output_dir": str(evidence_dir),
            "bundle_root": str(_resolve_agents_bundle(bundle_root)),
            "isolated_home": str(layout.home),
            "isolated_config_dir": str(layout.config_dir),
            "isolated_app_data_dir": str(layout.app_data_dir),
            "isolated_project_registry_dir": str(isolated_project_registry_dir(layout)),
            "global_projects_dir": str(projects_dir),
            "global_cache_dir": str(cache_dir),
            "ref_prefixes": ref_prefixes,
            "agy_print": agy_result,
            "payload_capture": payload_result,
            "global_project_registry_cleanup": {
                "removed": [str(path) for path in registry_cleanup.removed],
                "failed": [str(path) for path in registry_cleanup.failed],
            },
            "global_project_registry_postcheck": {
                "passed": not registry_refs,
                "ref_count": len(registry_refs),
                "refs": [str(path) for path in registry_refs],
            },
            "global_cli_cache_cleanup": {
                "removed": {
                    str(path): keys for path, keys in cache_cleanup.removed.items()
                },
                "failed": {
                    str(path): error for path, error in cache_cleanup.failed.items()
                },
            },
            "global_cli_cache_postcheck": {
                "passed": not cache_refs,
                "ref_count": sum(len(keys) for keys in cache_refs.values()),
                "refs": {str(path): keys for path, keys in cache_refs.items()},
            },
        }
    finally:
        if not keep_workspace:
            shutil.rmtree(layout.temp_root, ignore_errors=True)
            temp_cleanup = {
                "removed": not layout.temp_root.exists(),
                "kept": False,
                "path": str(layout.temp_root),
            }
        else:
            temp_cleanup = {
                "removed": False,
                "kept": True,
                "path": str(layout.temp_root),
            }
        if result:
            result["temp_cleanup"] = temp_cleanup
            if not keep_workspace and not temp_cleanup["removed"]:
                result["passed"] = False
            _write_json(evidence_dir / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run an isolated Antigravity CLI runtime probe for HarnessKit outputs."
    )
    parser.add_argument("--agy-bin", default="agy")
    parser.add_argument("--bundle-root", type=Path, default=Path("dist/antigravity-cli/.agents"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--global-projects-dir", type=Path)
    parser.add_argument("--global-cache-dir", type=Path)
    parser.add_argument("--temp-root", type=Path)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--print-timeout", default="90s")
    parser.add_argument("--subprocess-timeout", type=int, default=120)
    parser.add_argument("--skip-payload-capture", action="store_true")
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--residue-only", action="store_true")
    parser.add_argument("--apply-residue-cleanup", action="store_true")
    args = parser.parse_args()

    if args.residue_only:
        projects_dir = (
            args.global_projects_dir or _default_global_projects_dir()
        ).expanduser().resolve()
        cache_dir = (args.global_cache_dir or _default_global_cache_dir()).expanduser().resolve()
        registry_refs = scan_project_registry_refs(
            projects_dir,
            ref_prefix=DEFAULT_REF_PREFIX,
            extra_ref_prefixes=list(DEFAULT_RESIDUE_PREFIXES[1:]),
        )
        cache_cleanup = cleanup_cli_cache_refs(
            cache_dir,
            dry_run=not args.apply_residue_cleanup,
        )
        result = {
            "passed": not cache_cleanup.failed,
            "mode": "apply" if args.apply_residue_cleanup else "dry-run",
            "global_projects_dir": str(projects_dir),
            "global_cache_dir": str(cache_dir),
            "project_registry_refs": [str(path) for path in registry_refs],
            "cli_cache_refs": {
                str(path): keys for path, keys in cache_cleanup.removed.items()
            },
            "cli_cache_failed": {
                str(path): error for path, error in cache_cleanup.failed.items()
            },
        }
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            _write_json(args.output_dir / "antigravity-residue-cleanup.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1

    result = run_probe(
        agy_bin=args.agy_bin,
        bundle_root=args.bundle_root,
        output_dir=args.output_dir,
        global_projects_dir=args.global_projects_dir,
        global_cache_dir=args.global_cache_dir,
        temp_root=args.temp_root,
        prompt=args.prompt,
        print_timeout=args.print_timeout,
        subprocess_timeout=args.subprocess_timeout,
        run_payload_capture=not args.skip_payload_capture,
        keep_workspace=args.keep_workspace,
    )
    if "temp_cleanup" not in result:
        result["temp_cleanup"] = {
            "removed": False,
            "kept": args.keep_workspace,
            "path": result["temp_root"],
        }
    _write_json(Path(result["output_dir"]) / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
