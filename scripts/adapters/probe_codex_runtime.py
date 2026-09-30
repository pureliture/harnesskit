from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_CODEX = REPO_ROOT / "dist" / "codex"
DEFAULT_PROBES = ["skill", "agent"]
OPTIMAL_RESPONSE_RUNTIME_PROMPT = (
    "이 테스트 worktree의 현재 위치와 git branch를 읽기 전용으로 확인한 뒤, "
    "사용자가 이해하기 쉽게 알려줘."
)
RUNTIME_SELECTION_CONFIG_KEYS = frozenset(
    {
        "model",
        "model_reasoning_effort",
        "model_verbosity",
        "service_tier",
        "personality",
        "model_context_window",
        "model_auto_compact_token_limit",
    }
)
TOP_LEVEL_TOML_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
ARCHITECTURE_AGENT_PROBES = [
    {
        "name": "system-designer",
        "agent_type": "system_designer",
        "sub_marker": "SYSTEM_DESIGNER_AGENT_OK",
        "marker": "SYSTEM_DESIGNER_AGENT_RUNTIME: ok",
        "prompt": (
            "Runtime probe only. Spawn a subagent with "
            "agent_type='system_designer'. Ask it to answer exactly "
            "SYSTEM_DESIGNER_AGENT_OK if it can see the System Design Packet "
            "instructions and the boundary Do not claim runtime verified without "
            "observed proof. Final-answer exactly SYSTEM_DESIGNER_AGENT_RUNTIME: ok if the subagent "
            "succeeded; otherwise final-answer exactly "
            "SYSTEM_DESIGNER_AGENT_RUNTIME: unavailable with the error."
        ),
    },
    {
        "name": "codebase-architecture-manager",
        "agent_type": "codebase_architecture_manager",
        "sub_marker": "CODEBASE_ARCHITECTURE_MANAGER_AGENT_OK",
        "marker": "CODEBASE_ARCHITECTURE_MANAGER_AGENT_RUNTIME: ok",
        "prompt": (
            "Runtime probe only. Spawn a subagent with "
            "agent_type='codebase_architecture_manager'. Ask it to answer exactly "
            "CODEBASE_ARCHITECTURE_MANAGER_AGENT_OK if it can see Codebase "
            "Architecture Manager instructions, the read-only initial review "
            "boundary, and the temp HTML report contract. Final-answer exactly "
            "CODEBASE_ARCHITECTURE_MANAGER_AGENT_RUNTIME: ok if the subagent "
            "succeeded; otherwise final-answer exactly "
            "CODEBASE_ARCHITECTURE_MANAGER_AGENT_RUNTIME: unavailable with the error."
        ),
    },
]


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_sec: int | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        stderr = stderr + f"\ntimeout after {timeout_sec} seconds\n"
        return subprocess.CompletedProcess(argv, 124, stdout, stderr)


def _copy_dist(workspace: Path) -> None:
    if not DIST_CODEX.is_dir():
        raise FileNotFoundError(f"Missing Codex dist: {DIST_CODEX}")
    shutil.copytree(DIST_CODEX, workspace, dirs_exist_ok=True)
    (workspace / "AGENTS.md").write_text(
        "Use generated HarnessKit Codex adapter outputs in this workspace.\n",
        encoding="utf-8",
    )
    (workspace / "docs").mkdir(exist_ok=True)
    (workspace / "docs" / "source.md").write_text("# Source\n\nHuman docs probe.\n", encoding="utf-8")
    git_init = _run(["git", "init", "-q"], cwd=workspace)
    if git_init.returncode != 0:
        raise RuntimeError(git_init.stderr)


def _prepare_probe_workspace(workspace: Path) -> None:
    (workspace / "AGENTS.md").write_text(
        "Use generated HarnessKit Codex adapter outputs in this workspace.\n",
        encoding="utf-8",
    )
    (workspace / "docs").mkdir(exist_ok=True)
    (workspace / "docs" / "source.md").write_text("# Source\n\nHuman docs probe.\n", encoding="utf-8")
    git_init = _run(["git", "init", "-q"], cwd=workspace)
    if git_init.returncode != 0:
        raise RuntimeError(git_init.stderr)


def _apply_install_plan(
    target_root: Path,
    output_dir: Path,
    *,
    scope: str = "project",
    extra_components: list[str] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / f"install-plan.{scope}.apply.json"
    plan_argv = [
        sys.executable,
        "scripts/install/plan.py",
        "--profile",
        "engineering",
        "--scope",
        scope,
        "--mode",
        "apply",
        "--format",
        "json",
    ]
    for component in extra_components or []:
        plan_argv.extend(["--component", component])
    plan = _run(plan_argv, cwd=REPO_ROOT)
    if plan.returncode != 0:
        raise RuntimeError(plan.stderr)
    plan_path.write_text(plan.stdout, encoding="utf-8")

    apply_result = _run(
        [
            sys.executable,
            "scripts/install/apply.py",
            str(plan_path),
            "--target-root",
            str(target_root),
            "--overwrite",
            "--allow-runtime-hooks",
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
    return plan_path


def _top_level_runtime_selection_lines(config: str) -> list[tuple[str, str]]:
    """Return only non-sensitive global model-selection assignments.

    The temporary runtime home must not inherit user hooks, plugins, MCP
    servers, project trust, or agent registrations.  Model selection is the
    small exception: it lets an isolated probe use the same available model
    and tier as the invoking Codex session.
    """
    selected: list[tuple[str, str]] = []
    for line in config.splitlines():
        if line.lstrip().startswith("["):
            break
        match = TOP_LEVEL_TOML_ASSIGNMENT.match(line)
        if match and match.group(1) in RUNTIME_SELECTION_CONFIG_KEYS:
            selected.append((match.group(1), line))
    return selected


def _prepend_runtime_selection_config(config_path: Path, real_config_path: Path) -> list[str]:
    if not real_config_path.is_file():
        return []

    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    existing_keys = {
        key for key, _line in _top_level_runtime_selection_lines(existing)
    }
    selected = [
        (key, line)
        for key, line in _top_level_runtime_selection_lines(
            real_config_path.read_text(encoding="utf-8")
        )
        if key not in existing_keys
    ]
    if not selected:
        return []

    prefix = "\n".join(line for _key, line in selected)
    config_path.write_text(
        f"{prefix}\n\n{existing.lstrip()}",
        encoding="utf-8",
    )
    return [key for key, _line in selected]


def _probe_home(
    workspace: Path,
    *,
    home_root: Path | None = None,
    preserve_runtime_settings: bool = False,
) -> Path:
    codex_home = home_root / ".codex" if home_root is not None else Path(tempfile.mkdtemp(prefix="harnesskit-codex-home."))
    codex_home.mkdir(parents=True, exist_ok=True)
    real_codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    real_auth = real_codex_home / "auth.json"
    if real_auth.exists() and not (codex_home / "auth.json").exists():
        (codex_home / "auth.json").symlink_to(real_auth)
    config_path = codex_home / "config.toml"
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    additions: list[str] = []
    project_header = f'[projects."{workspace}"]'
    if project_header not in existing:
        additions.append(f'{project_header}\ntrust_level = "trusted"\n')
    if "[features]" not in existing:
        additions.append("[features]\nhooks = true\n")
    elif "hooks = true" not in existing:
        additions.append("[features]\nhooks = true\n")
    config_path.write_text(
        (existing.rstrip() + "\n\n" + "\n".join(additions)).strip() + "\n",
        encoding="utf-8",
    )
    if preserve_runtime_settings:
        _prepend_runtime_selection_config(config_path, real_codex_home / "config.toml")
    return codex_home


def _project_codex_hooks_path(workspace: Path) -> Path:
    return workspace / ".codex" / "hooks.json"


def _user_codex_hooks_path(env: dict[str, str]) -> Path:
    return Path(env["CODEX_HOME"]) / "hooks.json"


def _load_hook_config(path: Path) -> dict:
    if not path.is_file():
        return {"hooks": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _optimal_response_hook_path(workspace: Path, env: dict[str, str]) -> Path:
    user_hook_path = _user_codex_hooks_path(env)
    if user_hook_path.is_file():
        hook_config = json.loads(user_hook_path.read_text(encoding="utf-8"))
        hooks = hook_config.get("hooks", {})
        if isinstance(hooks, dict) and "UserPromptSubmit" in hooks:
            return user_hook_path
    return _project_codex_hooks_path(workspace)


def _nested_value(data: object, keys: tuple[str, ...]) -> object | None:
    value = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _thread_id_from_spawn(item: dict) -> str | None:
    receiver_thread_ids = item.get("receiver_thread_ids")
    if isinstance(receiver_thread_ids, list):
        for thread_id in receiver_thread_ids:
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    candidates = [
        item.get("receiver_thread_id"),
        item.get("thread_id"),
        _nested_value(item, ("result", "receiver_thread_id")),
        _nested_value(item, ("result", "thread_id")),
        _nested_value(item, ("output", "receiver_thread_id")),
        _nested_value(item, ("output", "thread_id")),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _subagent_marker_evidence(stdout: str, marker: str) -> dict:
    spawned_thread_ids: set[str] = set()
    completed_thread_ids: set[str] = set()
    completed_wait_thread_ids: set[str] = set()
    marker_contract_thread_ids: set[str] = set()
    marker_thread_ids: set[str] = set()
    wait_started_thread_ids_by_item_id: dict[str, set[str]] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "collab_tool_call":
            continue
        name = item.get("name") or item.get("tool")
        if name == "spawn_agent":
            thread_id = _thread_id_from_spawn(item)
            if thread_id:
                spawned_thread_ids.add(thread_id)
                prompt = item.get("prompt")
                if isinstance(prompt, str) and marker in prompt:
                    marker_contract_thread_ids.add(thread_id)
            continue
        if name != "wait":
            continue
        receiver_thread_ids = {
            thread_id
            for thread_id in (item.get("receiver_thread_ids") or [])
            if isinstance(thread_id, str) and thread_id
        }
        item_id = item.get("id")
        if event.get("type") == "item.started":
            if isinstance(item_id, str) and receiver_thread_ids:
                wait_started_thread_ids_by_item_id[item_id] = receiver_thread_ids
            continue
        if not receiver_thread_ids and isinstance(item_id, str):
            receiver_thread_ids = wait_started_thread_ids_by_item_id.get(item_id, set())
        if item.get("status") == "completed":
            completed_wait_thread_ids.update(receiver_thread_ids)
        agents_states = (
            _nested_value(item, ("result", "agents_states"))
            or _nested_value(item, ("output", "agents_states"))
            or item.get("agents_states")
        )
        if not isinstance(agents_states, dict):
            continue
        for thread_id, state in agents_states.items():
            if not isinstance(thread_id, str) or not isinstance(state, dict):
                continue
            if state.get("status") != "completed":
                continue
            completed_thread_ids.add(thread_id)
            message = state.get("message")
            if isinstance(message, str) and marker in message:
                marker_thread_ids.add(thread_id)
    completed_spawned_thread_ids = spawned_thread_ids & (
        completed_thread_ids | completed_wait_thread_ids
    )
    marker_contract_spawned_thread_ids = spawned_thread_ids & marker_contract_thread_ids
    marker_spawned_thread_ids = spawned_thread_ids & marker_thread_ids
    contract_completed_thread_ids = (
        spawned_thread_ids
        & marker_contract_thread_ids
        & (completed_thread_ids | completed_wait_thread_ids)
    )
    return {
        "completed": bool(completed_spawned_thread_ids),
        "marker_contract_sent": bool(marker_contract_spawned_thread_ids),
        "contract_completed": bool(contract_completed_thread_ids),
        "marker_found": bool(marker_spawned_thread_ids),
        "marker": marker,
        "source": (
            "codex-jsonl-collab-tool-wait"
            if marker_spawned_thread_ids
            else "codex-jsonl-spawn-wait-status"
        ),
    }


def _codex_exec_argv(
    *,
    workspace: Path,
    last_message: Path,
    prompt: str,
    json_output: bool = False,
    multi_agent: bool = False,
    model: str | None = None,
) -> list[str]:
    argv = ["codex", "exec"]
    if json_output:
        argv.append("--json")
    if model:
        argv.extend(["--model", model])
    argv.extend(
        [
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--cd",
            str(workspace),
        ]
    )
    if multi_agent:
        argv.extend(["--enable", "multi_agent"])
    argv.extend(["--output-last-message", str(last_message), prompt])
    return argv


def _run_agent_probe(
    *,
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    name: str,
    prompt: str,
    marker: str,
    sub_marker: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    last_message = output_dir / f"agent-{name}-last-message.txt"
    jsonl = output_dir / f"agent-{name}-events.jsonl"
    stderr_path = output_dir / f"agent-{name}-stderr.txt"
    result = _run(
        _codex_exec_argv(
            workspace=workspace,
            last_message=last_message,
            prompt=prompt,
            json_output=True,
            multi_agent=True,
            model=model,
        ),
        env=env,
        timeout_sec=timeout_seconds,
    )
    jsonl.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    message = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    subagent_evidence = (
        _subagent_marker_evidence(result.stdout, sub_marker) if sub_marker else None
    )
    subagent_passed = (
        True
        if subagent_evidence is None
        else (
            subagent_evidence["completed"]
            and subagent_evidence["marker_contract_sent"]
            and subagent_evidence["contract_completed"]
            and subagent_evidence["marker_found"]
        )
    )
    return {
        "name": name,
        "returncode": result.returncode,
        "last_message": message.strip(),
        "jsonl": str(jsonl),
        "stderr": str(stderr_path),
        "marker": marker,
        "subagent_evidence": subagent_evidence,
        "passed": result.returncode == 0 and marker in message and subagent_passed,
    }


def _probe_agent(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    *,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    return _run_agent_probe(
        workspace=workspace,
        output_dir=output_dir,
        env=env,
        name="human-doc-curator",
        prompt=(
            "Runtime probe only. Spawn a subagent with agent_type='human_doc_curator' "
            "and ask it to answer exactly HUMAN_DOC_AGENT_OK if it can see its "
            "human-doc-curator instructions. Then final-answer exactly "
            "AGENT_RUNTIME: ok if the subagent succeeded; otherwise final-answer "
            "AGENT_RUNTIME: unavailable with the error."
        ),
        marker="AGENT_RUNTIME: ok",
        sub_marker="HUMAN_DOC_AGENT_OK",
        model=model,
        timeout_seconds=timeout_seconds,
    )


def _probe_architecture_agents(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    *,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    agents = [
        _run_agent_probe(
            workspace=workspace,
            output_dir=output_dir,
            env=env,
            name=probe["name"],
            prompt=probe["prompt"],
            marker=probe["marker"],
            sub_marker=probe["sub_marker"],
            model=model,
            timeout_seconds=timeout_seconds,
        )
        for probe in ARCHITECTURE_AGENT_PROBES
    ]
    return {
        "passed": all(agent["passed"] for agent in agents),
        "agents": agents,
    }


def _probe_skill(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    *,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    last_message = output_dir / "skill-last-message.txt"
    prompt = (
        "Use the human-doc-curator skill from this workspace. Final-answer exactly "
        "SKILL_RUNTIME: ok if the skill body includes bootstrap, sync, and audit "
        "routing; otherwise final-answer SKILL_RUNTIME: unavailable."
    )
    result = _run(
        _codex_exec_argv(
            workspace=workspace,
            last_message=last_message,
            prompt=prompt,
            model=model,
        ),
        env=env,
        timeout_sec=timeout_seconds,
    )
    (output_dir / "skill-stdout.txt").write_text(result.stdout, encoding="utf-8")
    (output_dir / "skill-stderr.txt").write_text(result.stderr, encoding="utf-8")
    message = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    return {
        "returncode": result.returncode,
        "last_message": message.strip(),
        "stdout": str(output_dir / "skill-stdout.txt"),
        "stderr": str(output_dir / "skill-stderr.txt"),
        "passed": result.returncode == 0 and "SKILL_RUNTIME: ok" in message,
    }


def _event_counts(jsonl: str) -> dict[str, int]:
    counts = {"hook_started": 0, "hook_completed": 0}
    for line in jsonl.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type in counts:
            counts[event_type] += 1
    return counts


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _start_app_server(
    workspace: Path,
    env: dict[str, str],
    *,
    model: str | None = None,
) -> subprocess.Popen[str]:
    argv = ["codex", "app-server"]
    if model:
        argv.extend(["-c", f'model="{model}"'])
    argv.extend(["--listen", "stdio://"])
    return subprocess.Popen(
        argv,
        cwd=workspace,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )


def _send_jsonrpc(process: subprocess.Popen[str], msg_id: int, method: str, params: dict) -> None:
    if process.stdin is None:
        raise RuntimeError("app-server stdin is unavailable")
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}) + "\n")
    process.stdin.flush()


def _read_app_server(
    process: subprocess.Popen[str],
    *,
    until,
    timeout_sec: float,
) -> tuple[list[dict], list[str], dict | None]:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("app-server stdout/stderr is unavailable")

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout_sec
    messages: list[dict] = []
    stderr_lines: list[str] = []
    matched: dict | None = None

    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        for key, _ in selector.select(timeout=0.2):
            line = key.fileobj.readline()
            if line == "":
                continue
            if key.data == "stderr":
                stderr_lines.append(line)
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                message = {"parse_error": line.rstrip("\n")}
            messages.append(message)
            if until(message):
                matched = message
                selector.close()
                return messages, stderr_lines, matched
    selector.close()
    return messages, stderr_lines, matched


def _terminate_app_server(process: subprocess.Popen[str]) -> int | None:
    if process.poll() is not None:
        return process.returncode
    process.terminate()
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait(timeout=5)


def _write_jsonl(path: Path, messages: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(message, sort_keys=True) + "\n" for message in messages),
        encoding="utf-8",
    )


def _app_hook_counts(messages: list[dict]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for message in messages:
        method = message.get("method")
        if method not in {"hook/started", "hook/completed"}:
            continue
        event_name = (
            message.get("params", {})
            .get("run", {})
            .get("eventName")
        )
        if not isinstance(event_name, str):
            continue
        event_counts = counts.setdefault(event_name, {"started": 0, "completed": 0})
        if method == "hook/started":
            event_counts["started"] += 1
        else:
            event_counts["completed"] += 1
    return counts


def _request_app_server(
    process: subprocess.Popen[str],
    *,
    msg_id: int,
    method: str,
    params: dict,
    timeout_sec: float = 15,
) -> tuple[list[dict], list[str], dict]:
    _send_jsonrpc(process, msg_id, method, params)
    messages, stderr_lines, matched = _read_app_server(
        process,
        until=lambda message: message.get("id") == msg_id,
        timeout_sec=timeout_sec,
    )
    if matched is None:
        raise RuntimeError(f"Timed out waiting for app-server response id={msg_id} method={method}")
    return messages, stderr_lines, matched


def _discover_app_server_hooks(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    *,
    model: str | None = None,
) -> tuple[list[dict], list[dict]]:
    process = _start_app_server(workspace, env, model=model)
    messages: list[dict] = []
    stderr_lines: list[str] = []
    try:
        for msg_id, method, params in [
            (1, "initialize", {"clientInfo": {"name": "harnesskit-codex-hook-probe", "version": "0"}}),
            (2, "hooks/list", {"cwds": [str(workspace)]}),
        ]:
            step_messages, step_stderr, _ = _request_app_server(
                process,
                msg_id=msg_id,
                method=method,
                params=params,
            )
            messages.extend(step_messages)
            stderr_lines.extend(step_stderr)
    finally:
        exit_code = _terminate_app_server(process)
        messages.append({"process_exit": exit_code})

    _write_jsonl(output_dir / "hook-appserver-discovery.jsonl", messages)
    (output_dir / "hook-appserver-discovery-stderr.txt").write_text(
        "".join(stderr_lines),
        encoding="utf-8",
    )

    hooks: list[dict] = []
    for message in messages:
        if message.get("id") != 2:
            continue
        for item in message.get("result", {}).get("data", []):
            hooks.extend(item.get("hooks", []))
    trusted_hooks = [
        hook
        for hook in hooks
        if hook.get("source") in {"project", "user"}
        and hook.get("enabled")
        and hook.get("currentHash")
    ]
    return hooks, trusted_hooks


def _append_hook_trust(codex_home: Path, hooks: list[dict]) -> None:
    config_path = codex_home / "config.toml"
    existing = config_path.read_text(encoding="utf-8")
    lines = [existing.rstrip(), ""]
    for hook in hooks:
        key = hook["key"]
        current_hash = hook["currentHash"]
        lines.extend(
            [
                f"[hooks.state.{_toml_string(key)}]",
                f"trusted_hash = {_toml_string(current_hash)}",
                "",
            ]
        )
    config_path.write_text("\n".join(lines), encoding="utf-8")


def _completed_agent_message(message: dict, *, phase: str) -> bool:
    item = message.get("params", {}).get("item", {})
    return (
        message.get("method") == "item/completed"
        and isinstance(item, dict)
        and item.get("type") == "agentMessage"
        and item.get("phase") == phase
    )


def _app_server_turn_evidence(messages: list[dict]) -> dict[str, object]:
    completed_turn_message = next(
        (
            message
            for message in reversed(messages)
            if message.get("method") == "turn/completed"
        ),
        None,
    )
    turn = (
        completed_turn_message.get("params", {}).get("turn", {})
        if completed_turn_message
        else {}
    )
    status = turn.get("status") if isinstance(turn, dict) else None
    error = turn.get("error") if isinstance(turn, dict) else None
    if error is None:
        error_message = next(
            (
                message.get("params", {}).get("error")
                for message in reversed(messages)
                if message.get("method") == "error"
            ),
            None,
        )
        if error_message is not None:
            error = error_message

    commentary_observed = any(
        _completed_agent_message(message, phase="commentary")
        for message in messages
    )
    final_answer_observed = any(
        _completed_agent_message(message, phase="final_answer")
        for message in messages
    )
    turn_completed_event_observed = completed_turn_message is not None
    lifecycle_succeeded = (
        turn_completed_event_observed
        and status == "completed"
        and error is None
    )
    response_succeeded = (
        final_answer_observed
        and error is None
        and (
            not turn_completed_event_observed
            or status == "completed"
        )
    )
    return {
        "response_received": final_answer_observed or turn_completed_event_observed,
        "commentary_observed": commentary_observed,
        "final_answer_observed": final_answer_observed,
        "turn_completed_event_observed": turn_completed_event_observed,
        "status": status,
        "error": error,
        "lifecycle_succeeded": lifecycle_succeeded,
        "response_succeeded": response_succeeded,
    }


def _run_app_server_turn(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    *,
    model: str | None = None,
    timeout_seconds: int = 60,
) -> tuple[list[dict], list[str], dict[str, object], int | None]:

    process = _start_app_server(workspace, env, model=model)
    messages: list[dict] = []
    stderr_lines: list[str] = []
    exit_code: int | None = None
    try:
        for msg_id, method, params in [
            (1, "initialize", {"clientInfo": {"name": "harnesskit-codex-hook-probe", "version": "0"}}),
            (2, "hooks/list", {"cwds": [str(workspace)]}),
        ]:
            step_messages, step_stderr, _ = _request_app_server(
                process,
                msg_id=msg_id,
                method=method,
                params=params,
            )
            messages.extend(step_messages)
            stderr_lines.extend(step_stderr)

        step_messages, step_stderr, thread_response = _request_app_server(
            process,
            msg_id=3,
            method="thread/start",
            params={
                "cwd": str(workspace),
                "ephemeral": True,
                "sandbox": "workspace-write",
                "approvalPolicy": "never",
            },
            timeout_sec=20,
        )
        messages.extend(step_messages)
        stderr_lines.extend(step_stderr)
        thread_id = thread_response.get("result", {}).get("thread", {}).get("id")
        if not isinstance(thread_id, str):
            raise RuntimeError("app-server thread/start did not return a thread id")

        step_messages, step_stderr, _ = _request_app_server(
            process,
            msg_id=4,
            method="turn/start",
            params={
                "threadId": thread_id,
                "cwd": str(workspace),
                "input": [
                    {
                        "type": "text",
                        "text": OPTIMAL_RESPONSE_RUNTIME_PROMPT,
                    }
                ],
            },
            timeout_sec=20,
        )
        messages.extend(step_messages)
        stderr_lines.extend(step_stderr)

        step_messages, step_stderr, terminal_response = _read_app_server(
            process,
            until=lambda message: (
                message.get("method") in {"turn/completed", "error"}
                or _completed_agent_message(message, phase="final_answer")
            ),
            timeout_sec=timeout_seconds,
        )
        messages.extend(step_messages)
        stderr_lines.extend(step_stderr)
        if (
            terminal_response is not None
            and _completed_agent_message(terminal_response, phase="final_answer")
        ):
            step_messages, step_stderr, _ = _read_app_server(
                process,
                until=lambda message: message.get("method") in {"turn/completed", "error"},
                timeout_sec=5,
            )
            messages.extend(step_messages)
            stderr_lines.extend(step_stderr)
    finally:
        exit_code = _terminate_app_server(process)
        messages.append({"process_exit": exit_code})

    _write_jsonl(output_dir / "hook-appserver-runtime.jsonl", messages)
    (output_dir / "hook-appserver-runtime-stderr.txt").write_text(
        "".join(stderr_lines),
        encoding="utf-8",
    )
    turn_result = _app_server_turn_evidence(messages)
    return messages, stderr_lines, turn_result, exit_code


def _run_hook_prompt(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    *,
    name: str,
    prompt: str,
) -> dict:
    last_message = output_dir / f"hook-{name}-last-message.txt"
    stdout_path = output_dir / f"hook-{name}-events.jsonl"
    stderr_path = output_dir / f"hook-{name}-stderr.txt"
    result = _run(
        [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(workspace),
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
        "returncode": result.returncode,
        "last_message": message.strip(),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "event_counts": _event_counts(result.stdout),
    }


def _probe_optimal_response_hook_exec(workspace: Path, output_dir: Path, env: dict[str, str]) -> dict:
    hook_path = _optimal_response_hook_path(workspace, env)
    hook_config = json.loads(hook_path.read_text(encoding="utf-8"))
    hooks = hook_config.get("hooks", {})
    user_prompt_submit_registered = "UserPromptSubmit" in hooks
    session_start_registered = "SessionStart" in hooks

    normal_request = _run_hook_prompt(
        workspace,
        output_dir,
        env,
        name="normal-request",
        prompt=OPTIMAL_RESPONSE_RUNTIME_PROMPT,
    )

    passed = (
        user_prompt_submit_registered
        and not session_start_registered
        and normal_request["returncode"] == 0
    )
    failure_reason = None
    if not passed:
        if not user_prompt_submit_registered:
            failure_reason = "UserPromptSubmit hook is not registered in .codex/hooks.json"
        elif session_start_registered:
            failure_reason = "Codex SessionStart hook should not be registered without runtime evidence"
        elif normal_request["returncode"] != 0:
            failure_reason = "normal request did not complete through codex exec"
    return {
        "runtime": "exec",
        "hook_config": str(hook_path),
        "user_prompt_submit_registered": user_prompt_submit_registered,
        "session_start_registered": session_start_registered,
        "normal_request": normal_request,
        "event_observation_boundary": "codex exec does not prove UserPromptSubmit hook notifications; use app-server for hook execution evidence",
        "passed": passed,
        "failure_reason": failure_reason,
    }


def _probe_optimal_response_hook_app_server(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    *,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    hook_path = _optimal_response_hook_path(workspace, env)
    hook_config = json.loads(hook_path.read_text(encoding="utf-8"))
    registered_hooks = hook_config.get("hooks", {})
    user_prompt_submit_registered = "UserPromptSubmit" in registered_hooks
    session_start_registered = "SessionStart" in registered_hooks

    discovered_hooks, trusted_hooks = _discover_app_server_hooks(
        workspace,
        output_dir,
        env,
        model=model,
    )
    _append_hook_trust(Path(env["CODEX_HOME"]), trusted_hooks)
    (output_dir / "hook-trust-state.json").write_text(
        json.dumps(
            {
                "trusted_hooks": [
                    {
                        "key": hook.get("key"),
                        "eventName": hook.get("eventName"),
                        "currentHash": hook.get("currentHash"),
                        "sourcePath": hook.get("sourcePath"),
                        "trustStatus_before_append": hook.get("trustStatus"),
                    }
                    for hook in trusted_hooks
                ]
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    runtime_messages, _, turn, process_exit = _run_app_server_turn(
        workspace,
        output_dir,
        env,
        model=model,
        timeout_seconds=timeout_seconds or 60,
    )
    hook_counts = _app_hook_counts(runtime_messages)
    trusted_statuses_after = []
    for message in runtime_messages:
        if message.get("id") != 2:
            continue
        for item in message.get("result", {}).get("data", []):
            for hook in item.get("hooks", []):
                trusted_statuses_after.append(
                    {
                        "eventName": hook.get("eventName"),
                        "key": hook.get("key"),
                        "trustStatus": hook.get("trustStatus"),
                        "currentHash": hook.get("currentHash"),
                    }
                )

    user_prompt_counts = hook_counts.get("userPromptSubmit", {"started": 0, "completed": 0})
    user_prompt_trusted = any(
        hook.get("eventName") == "userPromptSubmit" and hook.get("trustStatus") == "trusted"
        for hook in trusted_statuses_after
    )
    passed = (
        user_prompt_submit_registered
        and not session_start_registered
        and user_prompt_trusted
        and user_prompt_counts["started"] >= 1
        and user_prompt_counts["completed"] >= 1
        and turn["response_succeeded"]
        and process_exit in (0, -15, 143)
    )

    failure_reason = None
    if not passed:
        if not user_prompt_submit_registered:
            failure_reason = "UserPromptSubmit hook is not registered in .codex/hooks.json"
        elif session_start_registered:
            failure_reason = "Codex SessionStart hook should not be registered without runtime evidence"
        elif not user_prompt_trusted:
            failure_reason = "app-server did not report UserPromptSubmit hook as trusted after hash registration"
        elif user_prompt_counts["started"] == 0 or user_prompt_counts["completed"] == 0:
            failure_reason = "app-server turn did not emit UserPromptSubmit hook/started and hook/completed notifications"
        elif not turn["response_received"]:
            failure_reason = "app-server did not return a completed final answer or turn/completed event"
        elif not turn["response_succeeded"]:
            failure_reason = (
                "app-server did not complete a successful final answer for the normal request: "
                f"status={turn['status']!r} error={turn['error']!r}"
            )

    return {
        "runtime": "app-server",
        "hook_config": str(hook_path),
        "user_prompt_submit_registered": user_prompt_submit_registered,
        "session_start_registered": session_start_registered,
        "discovered_hooks": [
            {
                "eventName": hook.get("eventName"),
                "key": hook.get("key"),
                "source": hook.get("source"),
                "enabled": hook.get("enabled"),
                "currentHash": hook.get("currentHash"),
                "trustStatus": hook.get("trustStatus"),
            }
            for hook in discovered_hooks
        ],
        "trusted_statuses_after_append": trusted_statuses_after,
        "event_counts": hook_counts,
        "scenario_prompt": OPTIMAL_RESPONSE_RUNTIME_PROMPT,
        "turn": turn,
        "runtime_jsonl": str(output_dir / "hook-appserver-runtime.jsonl"),
        "discovery_jsonl": str(output_dir / "hook-appserver-discovery.jsonl"),
        "passed": passed,
        "failure_reason": failure_reason,
    }


def _probe_no_project_hook_default(workspace: Path, env: dict[str, str]) -> dict:
    project_hook_path = _project_codex_hooks_path(workspace)
    user_hook_path = _user_codex_hooks_path(env)
    project_hooks = _load_hook_config(project_hook_path).get("hooks", {})
    user_hooks = _load_hook_config(user_hook_path).get("hooks", {})
    project_stop_registered = isinstance(project_hooks, dict) and "Stop" in project_hooks
    project_user_prompt_submit_registered = (
        isinstance(project_hooks, dict) and "UserPromptSubmit" in project_hooks
    )
    user_prompt_submit_registered = isinstance(user_hooks, dict) and "UserPromptSubmit" in user_hooks
    passed = (
        not project_stop_registered
        and not project_user_prompt_submit_registered
        and not user_prompt_submit_registered
    )
    failure_reason = None
    if not passed:
        if project_stop_registered:
            failure_reason = "Default project install unexpectedly registered a Stop hook"
        elif project_user_prompt_submit_registered:
            failure_reason = "Default project install unexpectedly registered UserPromptSubmit"
        elif user_prompt_submit_registered:
            failure_reason = "Project-scope no-hook default should not install user hooks"
    return {
        "runtime": "static-no-hook-default",
        "project_hook_config": str(project_hook_path),
        "user_hook_config": str(user_hook_path),
        "project_hook_config_exists": project_hook_path.is_file(),
        "user_hook_config_exists": user_hook_path.is_file(),
        "project_stop_registered": project_stop_registered,
        "project_user_prompt_submit_registered": project_user_prompt_submit_registered,
        "user_prompt_submit_registered": user_prompt_submit_registered,
        "passed": passed,
        "failure_reason": failure_reason,
        "runtime_execution": "not_run",
    }


def _probe_missing_user_prompt_submit(workspace: Path, env: dict[str, str]) -> dict:
    project_hook_path = _project_codex_hooks_path(workspace)
    user_hook_path = _user_codex_hooks_path(env)
    project_hooks = _load_hook_config(project_hook_path).get("hooks", {})
    user_hooks = _load_hook_config(user_hook_path).get("hooks", {})
    return {
        "runtime": "static-user-hook-missing",
        "project_hook_config": str(project_hook_path),
        "user_hook_config": str(user_hook_path),
        "project_hook_config_exists": project_hook_path.is_file(),
        "user_hook_config_exists": user_hook_path.is_file(),
        "project_stop_registered": isinstance(project_hooks, dict) and "Stop" in project_hooks,
        "project_user_prompt_submit_registered": (
            isinstance(project_hooks, dict) and "UserPromptSubmit" in project_hooks
        ),
        "user_prompt_submit_registered": (
            isinstance(user_hooks, dict) and "UserPromptSubmit" in user_hooks
        ),
        "passed": False,
        "failure_reason": "User-scope Codex hook probe requires UserPromptSubmit in ${CODEX_HOME:-$HOME/.codex}/hooks.json",
        "runtime_execution": "not_run",
    }


def _probe_optimal_response_hook(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    *,
    hook_runtime: str,
    model: str | None = None,
    timeout_seconds: int | None = None,
    install_scope: str = "project",
) -> dict:
    hook_path = (
        _user_codex_hooks_path(env)
        if install_scope == "user"
        else _optimal_response_hook_path(workspace, env)
    )
    hook_config = _load_hook_config(hook_path)
    hooks = hook_config.get("hooks", {})
    if "UserPromptSubmit" not in hooks:
        if install_scope == "project":
            return _probe_no_project_hook_default(workspace, env)
        return _probe_missing_user_prompt_submit(workspace, env)
    if hook_runtime == "app-server":
        return _probe_optimal_response_hook_app_server(
            workspace,
            output_dir,
            env,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    return _probe_optimal_response_hook_exec(workspace, output_dir, env)


def _live_calls_for_probes(probes: list[str]) -> int:
    calls = 0
    if "skill" in probes:
        calls += 1
    if "agent" in probes:
        calls += 1
    if "architecture-agents" in probes:
        calls += len(ARCHITECTURE_AGENT_PROBES)
    if "hooks" in probes:
        calls += 1
    return calls


def _cleanup_runtime_paths(
    *,
    workspace: Path,
    codex_home: Path | None,
    keep_workspace: bool,
) -> dict:
    workspace_existed_before = workspace.exists()
    codex_home_existed_before = bool(codex_home and codex_home.exists())
    if not keep_workspace:
        shutil.rmtree(workspace, ignore_errors=True)
    if codex_home is not None:
        shutil.rmtree(codex_home, ignore_errors=True)
    return {
        "workspace_existed_before_cleanup": workspace_existed_before,
        "workspace_removed": (not workspace.exists()) if not keep_workspace else False,
        "workspace_kept": keep_workspace,
        "codex_home_existed_before_cleanup": codex_home_existed_before,
        "codex_home_removed": (not codex_home.exists()) if codex_home is not None else True,
    }


def _runtime_contract(
    *,
    output_dir: Path,
    workspace: Path,
    probes: list[str],
    model: str | None,
    timeout_seconds: int | None,
    max_live_calls: int | None,
    max_budget_usd: float | None,
    cleanup_result: dict | None,
    passed: bool,
    install_scope: str = "project",
) -> dict:
    live_calls_used = _live_calls_for_probes(probes)
    user_prompt_submit_status = (
        "hook_transport_observed_pending_behavior_review"
        if install_scope == "user" and "hooks" in probes and passed
        else "pending_probe_for_user_prompt_submit"
    )
    return {
        "fixture_id": output_dir.name,
        "runtime_target": "codex",
        "command_summary": "codex exec isolated apply-plan probe",
        "hook_config_split": {
            "project_no_human_doc_default": str(_project_codex_hooks_path(workspace)),
            "user_prompt_submit": "${CODEX_HOME:-$HOME/.codex}/hooks.json",
            "install_scope": install_scope,
            "runtime_status": user_prompt_submit_status,
        },
        "sanitized_event_log_paths": [
            str(path)
            for path in sorted(output_dir.glob("agent-*-events.jsonl"))
        ],
        "timeout_seconds": timeout_seconds,
        "timeout_result": {
            "configured": timeout_seconds is not None,
            "passed": True,
        },
        "budget_result": {
            "native_budget_flag": False,
            "max_budget_usd": max_budget_usd,
            "model_guard": model,
            "passed": max_budget_usd is None or max_budget_usd > 0,
        },
        "process_cleanup_result": cleanup_result,
        "provider": "codex",
        "selected_model": model,
        "allowed_lightest_model_class": "gpt-5.4-mini",
        "effort": "default",
        "live_call_ceiling": max_live_calls,
        "live_calls_used": live_calls_used,
        "workspace": str(workspace),
        "verdict": "PASS" if passed else "FAIL",
        "deferred_items": [],
        "required_fixes": [] if passed else ["inspect failed probe records"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe generated Codex adapter outputs in Codex CLI.")
    parser.add_argument("--component", action="append", default=[])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument(
        "--model",
        help="Optional Codex model override for exec probes, for example gpt-5.4-mini.",
    )
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--max-live-calls", type=int)
    parser.add_argument("--max-budget-usd", type=float)
    parser.add_argument(
        "--probe",
        action="append",
        choices=["skill", "agent", "architecture-agents", "hooks", "all"],
        default=[],
        help="Probe group to run. Defaults to skill and agent for backward compatibility.",
    )
    parser.add_argument(
        "--materialize",
        choices=["copy-dist", "apply-plan"],
        default="copy-dist",
        help="How to materialize generated Codex outputs into the probe workspace.",
    )
    parser.add_argument(
        "--install-scope",
        choices=["project", "user"],
        default="project",
        help="Install scope used with --materialize apply-plan.",
    )
    parser.add_argument(
        "--hook-runtime",
        choices=["exec", "app-server"],
        default="exec",
        help="Runtime path for hook probes. exec preserves the original probe; app-server observes interactive hook notifications.",
    )
    args = parser.parse_args(argv)
    probes = args.probe or DEFAULT_PROBES
    if "all" in probes:
        probes = ["skill", "agent", "architecture-agents", "hooks"]
    live_calls_used = _live_calls_for_probes(probes)
    if args.max_live_calls is not None and live_calls_used > args.max_live_calls:
        parser.error("--max-live-calls is lower than the selected probe count")
    if args.max_budget_usd is not None and args.max_budget_usd <= 0:
        parser.error("--max-budget-usd must be positive when provided")
    if args.timeout_seconds is not None and args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive when provided")

    version = _run(["codex", "--version"])
    if version.returncode != 0:
        print(version.stderr, file=sys.stderr)
        return version.returncode

    workspace = Path(tempfile.mkdtemp(prefix="harnesskit-codex-runtime."))
    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="harnesskit-codex-runtime-evidence."))
    output_dir.mkdir(parents=True, exist_ok=True)
    codex_home: Path | None = None
    home_root: Path | None = None
    cleanup_done = False

    try:
        plan_path = None
        if args.materialize == "apply-plan":
            if args.install_scope == "user":
                home_root = Path(tempfile.mkdtemp(prefix="harnesskit-codex-user-home."))
                plan_path = _apply_install_plan(
                    home_root,
                    output_dir,
                    scope="user",
                    extra_components=args.component,
                )
                _prepare_probe_workspace(workspace)
            else:
                plan_path = _apply_install_plan(
                    workspace,
                    output_dir,
                    scope="project",
                    extra_components=args.component,
                )
                _prepare_probe_workspace(workspace)
        else:
            _copy_dist(workspace)
        codex_home = _probe_home(workspace, home_root=home_root)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        if home_root is not None:
            env["HOME"] = str(home_root)
        result = {
            "codex_version": version.stdout.strip(),
            "workspace": str(workspace),
            "output_dir": str(output_dir),
            "codex_home": str(codex_home),
            "materialize": args.materialize,
            "install_scope": args.install_scope,
            "install_plan": str(plan_path) if plan_path is not None else None,
            "components": args.component,
            "model": args.model,
            "timeout_seconds": args.timeout_seconds,
            "max_live_calls": args.max_live_calls,
            "max_budget_usd": args.max_budget_usd,
            "probes": probes,
        }
        if "skill" in probes:
            result["skill"] = _probe_skill(
                workspace,
                output_dir,
                env,
                model=args.model,
                timeout_seconds=args.timeout_seconds,
            )
        if "agent" in probes:
            result["agent"] = _probe_agent(
                workspace,
                output_dir,
                env,
                model=args.model,
                timeout_seconds=args.timeout_seconds,
            )
        if "architecture-agents" in probes:
            result["architecture-agents"] = _probe_architecture_agents(
                workspace,
                output_dir,
                env,
                model=args.model,
                timeout_seconds=args.timeout_seconds,
            )
        if "hooks" in probes:
            result["hooks"] = _probe_optimal_response_hook(
                workspace,
                output_dir,
                env,
                hook_runtime=args.hook_runtime,
                model=args.model,
                timeout_seconds=args.timeout_seconds,
                install_scope=args.install_scope,
            )
        passed = all(result[probe]["passed"] for probe in probes)
        cleanup_result = _cleanup_runtime_paths(
            workspace=workspace,
            codex_home=codex_home,
            keep_workspace=args.keep_workspace,
        )
        if home_root is not None and not args.keep_workspace:
            shutil.rmtree(home_root, ignore_errors=True)
        cleanup_done = True
        result["runtime_contract"] = _runtime_contract(
            output_dir=output_dir,
            workspace=workspace,
            probes=probes,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
            max_live_calls=args.max_live_calls,
            max_budget_usd=args.max_budget_usd,
            cleanup_result=cleanup_result,
            passed=passed,
            install_scope=args.install_scope,
        )
        result["passed"] = passed
        (output_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if passed else 1
    except Exception as exc:
        failure = {
            "error_type": type(exc).__name__,
            "error": str(exc),
            "materialize": args.materialize,
            "install_scope": args.install_scope,
            "probes": probes,
        }
        (output_dir / "probe-error.json").write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(failure, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        if not cleanup_done:
            if not args.keep_workspace:
                shutil.rmtree(workspace, ignore_errors=True)
            if codex_home is not None:
                shutil.rmtree(codex_home, ignore_errors=True)
            if home_root is not None and not args.keep_workspace:
                shutil.rmtree(home_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
