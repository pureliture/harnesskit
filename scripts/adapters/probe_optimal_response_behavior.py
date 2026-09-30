from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.adapters import probe_codex_runtime as runtime  # noqa: E402


OPTIMAL_RESPONSE_COMPONENTS = [
    "harnesskit.skill.optimal-response",
    "harnesskit.hook.optimal-response-prompt-submit",
]
STOP_PROMPT = """안전한 `pwd` 한 번으로 현재 workspace를 확인해줘.

도구 실행 전 commentary에 지금 무엇을 확인하는지와 이유를 짧게 알려줘. 명령 결과 뒤에는 commentary로
현재 상태와 다음 단계를 짧게 알린 뒤, 중간 메시지를 읽지 않아도 이해되는 final answer를 작성해줘.
final answer에는 요청, 수행한 확인, 결과, 그리고 확인하지 않은 범위만 남겨줘."""
COMPLETION_PROMPT = """아래 smoke 결과를 사용자가 바로 이해할 수 있는 완료 보고로 답해줘.

무엇을 확인했고, 왜 차단됐는지, 원문을 노출하지 않았다는 점, 그리고 결과만 짧게 설명해줘.
raw field label, exact command, exit code, 가짜 secret 원문은 쓰지 마. 중간 메시지를 읽지 않아도 이해되어야 해.

결과:
scan: 통과, finding 1개 생성
finding.sourceTool: gitleaks
finding.ruleId: synthetic-fake-secret
gitleaks.secret: FAKE_SECRET_12345678 보존 확인
gitleaks.match: FAKE_SECRET_12345678 포함 확인
report: 가짜 secret 원문 출력 안 함
gate --max 0: exit 1, finding 때문에 blocking 처리됨
"""
RICH_PROMPT = (
    "optimal-response runtime gate를 HTML 대시보드로 시각화해서 보여줘. "
    "현재 workspace의 optimal-response skill RICH workflow를 사용하고, "
    "artifact 생성 후 전체 보기 URL을 포함해."
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_project_trust_aliases(codex_home: Path, workspace: Path) -> None:
    config_path = codex_home / "config.toml"
    existing = config_path.read_text(encoding="utf-8")
    aliases = {str(workspace), str(workspace.resolve())}
    additions: list[str] = []
    for alias in sorted(aliases):
        header = f'[projects."{alias}"]'
        if header not in existing:
            additions.append(f'{header}\ntrust_level = "trusted"\n')
    if additions:
        config_path.write_text(existing.rstrip() + "\n\n" + "\n".join(additions), encoding="utf-8")


def _optimal_response_env(base_env: dict[str, str], codex_home: Path, home_root: Path | None) -> dict[str, str]:
    env = base_env.copy()
    env["CODEX_HOME"] = str(codex_home)
    if home_root is not None:
        env["HOME"] = str(home_root)
        env["XDG_DATA_HOME"] = str(home_root / ".local" / "share")
    else:
        env["XDG_DATA_HOME"] = str(codex_home / "xdg-data")
    env["OPTIMAL_RESPONSE_FLAG"] = str(codex_home / ".optimal-response-disabled")
    env["OPTIMAL_RESPONSE_MODE"] = str(codex_home / ".optimal-response-mode")
    env["OPTIMAL_RESPONSE_DISPLAY_MODE"] = str(codex_home / ".optimal-response-display-mode")
    env["OPTIMAL_RESPONSE_STATE_DIR"] = str(codex_home / "optimal-response-out" / "state")
    env.pop("OPTIMAL_RESPONSE_SQLITE_PATH", None)
    env["OPTIMAL_RESPONSE_SURFACE"] = "codex"
    return env


def _materialize_workspace(
    output_dir: Path,
    materialize: str,
    workspace: Path | None = None,
) -> tuple[Path, Path | None, Path | None]:
    if workspace is not None:
        workspace = workspace.expanduser().resolve(strict=True)
        check = runtime._run(["git", "rev-parse", "--is-inside-work-tree"], cwd=workspace)
        if check.returncode != 0 or check.stdout.strip() != "true":
            raise RuntimeError("Runtime workspace must be an existing git worktree")
    else:
        workspace = Path(tempfile.mkdtemp(prefix="harnesskit-or-behavior-workspace."))
    if materialize == "apply-plan":
        home_root = Path(tempfile.mkdtemp(prefix="harnesskit-or-behavior-home."))
        if not (workspace / ".git").exists():
            runtime._prepare_probe_workspace(workspace)
        plan_path = runtime._apply_install_plan(
            home_root,
            output_dir,
            scope="user",
            extra_components=OPTIMAL_RESPONSE_COMPONENTS,
        )
    else:
        runtime._copy_dist(workspace)
        plan_path = None
        home_root = None
    return workspace, plan_path, home_root


def _optimal_response_hook_path(workspace: Path, codex_home: Path) -> Path:
    candidates = [
        codex_home / "skills" / "optimal-response" / "hooks" / "stop-prompt-submit.cjs",
        workspace / ".agents" / "skills" / "optimal-response" / "hooks" / "stop-prompt-submit.cjs",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "optimal-response hook was not materialized in CODEX_HOME or workspace .agents"
    )


def _run_hook(
    workspace: Path,
    output_dir: Path,
    name: str,
    prompt: str,
    *,
    hook_path: Path,
    env: dict[str, str],
) -> dict:
    completed = subprocess.run(
        ["node", str(hook_path)],
        cwd=workspace,
        env=env,
        input=json.dumps({"prompt": prompt}),
        text=True,
        capture_output=True,
        check=False,
    )
    stdout_path = output_dir / f"hook-{name}.json"
    stderr_path = output_dir / f"hook-{name}-stderr.txt"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    try:
        parsed = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        parsed = {}
    context = parsed.get("hookSpecificOutput", {}).get("additionalContext", "")
    return {
        "returncode": completed.returncode,
        "hook_path": str(hook_path),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "additional_context": context,
        "contains_rich": "🖥️ RICH:" in context,
        "contains_rich_best_effort": "best-effort" in context and "preview" in context,
        "contains_stop": "STOP/" in context,
        "contains_stop_ultra": "STOP/ultra" in context,
        "contains_progress_visible": "progress updates remain visible" in context,
        "contains_final_only": "FINAL_ONLY" in context,
    }


def _run_state_canary(
    workspace: Path,
    output_dir: Path,
    *,
    state_root: Path,
    hook_path: Path,
    env: dict[str, str],
) -> dict:
    flag_path = state_root / ".optimal-response-disabled"
    mode_path = state_root / ".optimal-response-mode"
    display_mode_path = state_root / ".optimal-response-display-mode"
    _reset_mode_state(state_root)

    ultra = _run_hook(
        workspace,
        output_dir,
        "state-ultra",
        "/caveman ultra",
        hook_path=hook_path,
        env=env,
    )
    mode_after_ultra = mode_path.read_text(encoding="utf-8").strip() if mode_path.exists() else None
    final_only = _run_hook(
        workspace,
        output_dir,
        "state-final-only",
        "/optimal-response final-only",
        hook_path=hook_path,
        env=env,
    )
    display_mode_after_final_only = (
        display_mode_path.read_text(encoding="utf-8").strip()
        if display_mode_path.exists()
        else None
    )
    normal = _run_hook(
        workspace,
        output_dir,
        "state-normal",
        "normal mode",
        hook_path=hook_path,
        env=env,
    )
    return {
        "ultra": ultra,
        "normal": normal,
        "mode_after_ultra": mode_after_ultra,
        "final_only": final_only,
        "display_mode_after_final_only": display_mode_after_final_only,
        "flag_after_normal": flag_path.exists(),
        "mode_after_normal": mode_path.exists(),
        "display_mode_after_normal": display_mode_path.exists(),
        "passed": (
            mode_after_ultra == "ultra"
            and ultra["contains_stop_ultra"]
            and final_only["contains_final_only"]
            and display_mode_after_final_only == "final-only"
            and flag_path.exists()
            and not mode_path.exists()
            and not display_mode_path.exists()
        ),
    }


def _reset_mode_state(state_root: Path) -> None:
    (state_root / ".optimal-response-disabled").unlink(missing_ok=True)
    (state_root / ".optimal-response-mode").unlink(missing_ok=True)
    (state_root / ".optimal-response-display-mode").unlink(missing_ok=True)


def _read_mode_events(workspace: Path, output_dir: Path, codex_home: Path) -> dict:
    candidates = [
        codex_home / "optimal-response-out" / "state" / "mode-events.jsonl",
        workspace / ".harnesskit" / "optimal-response-out" / "state" / "mode-events.jsonl",
    ]
    events_path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    copied_path = output_dir / "mode-events.jsonl"
    parsed: list[dict] = []
    raw = ""
    if events_path.exists():
        raw = events_path.read_text(encoding="utf-8")
        copied_path.write_text(raw, encoding="utf-8")
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                parsed.append(item)
    mode_hints = sorted({str(item.get("mode_hint")) for item in parsed if item.get("mode_hint")})
    triggers = sorted({str(item.get("trigger")) for item in parsed if item.get("trigger")})
    forbidden_fragments = ["prompt", "response", "FAKE_SECRET_12345678", "SECRET_SHOULD_NOT_BE_STORED"]
    return {
        "path": str(events_path),
        "candidate_paths": [str(candidate) for candidate in candidates],
        "copied_path": str(copied_path),
        "count": len(parsed),
        "mode_hints": mode_hints,
        "triggers": triggers,
        "has_rich": "RICH" in mode_hints,
        "has_stop": "STOP" in mode_hints,
        "has_neutral": "NEUTRAL" in mode_hints,
        "redacts_prompt_and_response": not any(fragment in raw for fragment in forbidden_fragments),
    }


def _completion_summary_contract(final: str) -> dict:
    forbidden = ["FAKE_SECRET_12345678"]
    has_forbidden = [fragment for fragment in forbidden if fragment in final]
    lower = final.lower()
    mentions_secret = (
        "secret" in lower
        or "시크릿" in final
        or "비밀" in final
        or "민감" in final
    )
    speaks_human = (
        mentions_secret
        and any(word in final for word in ["차단", "막", "blocking", "blocked"])
        and any(word in final for word in ["노출", "숨", "원문", "redact"])
        and any(word in final for word in ["검사", "스캔", "scan", "finding", "발견"])
    )
    return {
        "passed": bool(final.strip()) and not has_forbidden and speaks_human and len(final) <= 900,
        "has_forbidden_fragments": has_forbidden,
        "speaks_human": speaks_human,
        "final_len": len(final),
    }


def _rich_presentation_contract(final: str) -> dict:
    forbidden = [
        "start_timeout",
        "healthcheck_failed",
        "background_process_exited",
        "Retry in a persistent terminal",
        "재시도",
        "원인 로그",
        "port 탐색",
    ]
    has_forbidden = [fragment for fragment in forbidden if fragment in final]
    has_artifact_view = "file://" in final or "http://localhost:" in final
    return {
        "passed": has_artifact_view and not has_forbidden,
        "has_forbidden_fragments": has_forbidden,
        "has_artifact_view": has_artifact_view,
        "final_len": len(final),
    }


def _rich_server_contract(skill_root: Path) -> dict:
    script = skill_root / "scripts" / "start-server.sh"
    text = script.read_text(encoding="utf-8")
    return {
        "script": str(script),
        "has_degraded_type": "server-degraded" in text,
        "has_file_fallback": "file-fallback" in text,
        "has_content_dir": "content_dir" in text,
        "has_retry_instruction": "Retry in a persistent terminal" in text,
        "passed": (
            "server-degraded" in text
            and "file-fallback" in text
            and "content_dir" in text
            and "Retry in a persistent terminal" not in text
        ),
    }


def _start_app_server_with_model(
    workspace: Path,
    env: dict[str, str],
    model: str | None,
) -> subprocess.Popen[str]:
    command = ["codex", "app-server"]
    if model:
        command.extend(["-c", f'model="{model}"'])
    command.extend(["--listen", "stdio://"])
    return subprocess.Popen(
        command,
        cwd=workspace,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )


def _extract_final(messages: list[dict]) -> str:
    texts = _agent_message_texts(messages)
    return texts[-1] if texts else ""


def _agent_message_texts(messages: list[dict]) -> list[str]:
    texts: list[str] = []
    for message in messages:
        item = message.get("params", {}).get("item")
        if message.get("method") != "item/completed" or not isinstance(item, dict):
            continue
        if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
            texts.append(item["text"])
    return texts


def _progress_contract(messages: list[dict]) -> dict:
    command_indexes: list[int] = []
    agent_messages: list[tuple[int, str]] = []
    for index, message in enumerate(messages):
        item = message.get("params", {}).get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") == "commandExecution":
            command_indexes.append(index)
        if message.get("method") == "item/completed" and item.get("type") == "agentMessage":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                agent_messages.append((index, text))

    first_command = command_indexes[0] if command_indexes else None
    final_index = agent_messages[-1][0] if agent_messages else None
    before_command = [text for index, text in agent_messages if first_command is not None and index < first_command]
    after_command = [
        text
        for index, text in agent_messages
        if first_command is not None and final_index is not None and first_command < index < final_index
    ]
    before_text = " ".join(before_command).lower()
    after_text = " ".join(after_command).lower()
    has_work_and_purpose = bool(before_text) and (
        "확인" in before_text or "check" in before_text or "inspect" in before_text
    ) and (
        "이유" in before_text
        or "목적" in before_text
        or "위해" in before_text
        or "하려고" in before_text
        or "하려는" in before_text
        or " to " in before_text
    )
    has_material_state_and_next = bool(after_text) and (
        "상태" in after_text or "확인" in after_text or "완료" in after_text or "result" in after_text
    ) and (
        "다음" in after_text
        or "이어서" in after_text
        or "계속" in after_text
        or "next" in after_text
    )
    return {
        "command_count": len(command_indexes),
        "before_command_messages": before_command,
        "after_command_messages": after_command,
        "has_work_and_purpose": has_work_and_purpose,
        "has_material_state_and_next": has_material_state_and_next,
        "passed": len(command_indexes) >= 1 and has_work_and_purpose and has_material_state_and_next,
    }


def _run_app_server_turn(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    *,
    model: str | None,
    name: str,
    prompt: str,
    timeout_sec: int,
) -> dict:
    output_root = workspace / ".harnesskit" / "optimal-response-out"
    content_dir = output_root / "content"
    before = {str(path) for path in output_root.glob("**/optimal-response-*.html")} if output_root.exists() else set()
    process = _start_app_server_with_model(workspace, env, model)
    messages: list[dict] = []
    stderr_lines: list[str] = []
    exit_code: int | None = None
    try:
        for msg_id, method, params in [
            (1, "initialize", {"clientInfo": {"name": "harnesskit-optimal-response-behavior", "version": "0"}}),
            (2, "hooks/list", {"cwds": [str(workspace)]}),
        ]:
            step_messages, step_stderr, _ = runtime._request_app_server(
                process,
                msg_id=msg_id,
                method=method,
                params=params,
                timeout_sec=20,
            )
            messages.extend(step_messages)
            stderr_lines.extend(step_stderr)

        step_messages, step_stderr, thread_response = runtime._request_app_server(
            process,
            msg_id=3,
            method="thread/start",
            params={
                "cwd": str(workspace),
                "ephemeral": True,
                "sandbox": "workspace-write",
                "approvalPolicy": "never",
            },
            timeout_sec=30,
        )
        messages.extend(step_messages)
        stderr_lines.extend(step_stderr)
        thread_id = thread_response.get("result", {}).get("thread", {}).get("id")
        if not isinstance(thread_id, str):
            raise RuntimeError("app-server thread/start did not return a thread id")

        step_messages, step_stderr, _ = runtime._request_app_server(
            process,
            msg_id=4,
            method="turn/start",
            params={
                "threadId": thread_id,
                "cwd": str(workspace),
                "input": [{"type": "text", "text": prompt}],
            },
            timeout_sec=30,
        )
        messages.extend(step_messages)
        stderr_lines.extend(step_stderr)
        step_messages, step_stderr, _ = runtime._read_app_server(
            process,
            until=lambda message: message.get("method") == "turn/completed",
            timeout_sec=timeout_sec,
        )
        messages.extend(step_messages)
        stderr_lines.extend(step_stderr)
    finally:
        exit_code = runtime._terminate_app_server(process)
        messages.append({"process_exit": exit_code})

    runtime._write_jsonl(output_dir / f"{name}.jsonl", messages)
    (output_dir / f"{name}-stderr.txt").write_text("".join(stderr_lines), encoding="utf-8")
    after = {str(path) for path in output_root.glob("**/optimal-response-*.html")} if output_root.exists() else set()
    final = _extract_final(messages)
    progress = _progress_contract(messages)
    created_html = sorted(after - before)
    created_html_in_content_dir = [path for path in created_html if str(content_dir) in path]
    summary = {
        "model": model,
        "hook_counts": runtime._app_hook_counts(messages),
        "final": final,
        "final_len": len(final),
        "progress": progress,
        "created_html": created_html,
        "created_html_in_content_dir": created_html_in_content_dir,
        "has_full_view": "전체 보기" in final or "http://localhost:" in final or "file://" in final,
        "output_root": str(output_root),
        "content_dir": str(content_dir),
        "process_exit": exit_code,
    }
    _write_json(output_dir / f"{name}-summary.json", summary)
    return summary


def _run_codex_exec_expected_gap(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    model: str | None,
) -> dict:
    last_message = output_dir / "codex-exec-stop-last.txt"
    events = output_dir / "codex-exec-stop-events.jsonl"
    stderr = output_dir / "codex-exec-stop-stderr.txt"
    command = [
        "codex",
        "exec",
        "--enable",
        "hooks",
        "--json",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(workspace),
    ]
    if model:
        command.extend(["--model", model])
    command.extend(["--output-last-message", str(last_message), STOP_PROMPT])
    result = runtime._run(command, env=env)
    events.write_text(result.stdout, encoding="utf-8")
    stderr.write_text(result.stderr, encoding="utf-8")
    final = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    counts = runtime._event_counts(result.stdout)
    summary = {
        "model": model,
        "returncode": result.returncode,
        "event_counts": counts,
        "final": final.strip(),
        "final_len": len(final.strip()),
        "expected_gap_observed": counts["hook_started"] == 0 and counts["hook_completed"] == 0,
    }
    _write_json(output_dir / "codex-exec-stop-summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run optimal-response behavior canaries against Codex runtime surfaces.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--materialize", choices=["apply-plan", "copy-dist"], default="apply-plan")
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--skip-exec-gap", action="store_true")
    parser.add_argument("--skip-rich", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=300)
    args = parser.parse_args()

    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="harnesskit-or-behavior-evidence."))
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace: Path | None = None
    codex_home: Path | None = None
    home_root: Path | None = None
    try:
        workspace, plan_path, home_root = _materialize_workspace(
            output_dir,
            args.materialize,
            args.workspace,
        )
        codex_home = runtime._probe_home(
            workspace,
            home_root=home_root,
            preserve_runtime_settings=True,
        )
        preserved_runtime_settings = [
            key
            for key, _line in runtime._top_level_runtime_selection_lines(
                (codex_home / "config.toml").read_text(encoding="utf-8")
            )
        ]
        _append_project_trust_aliases(codex_home, workspace)
        env = _optimal_response_env(os.environ, codex_home, home_root)
        discovered_hooks, trusted_hooks = runtime._discover_app_server_hooks(workspace, output_dir, env)
        runtime._append_hook_trust(codex_home, trusted_hooks)
        hook_path = _optimal_response_hook_path(workspace, codex_home)
        skill_root = hook_path.parents[1]

        direct_rich = _run_hook(
            workspace,
            output_dir,
            "rich",
            "optimal-response 구조를 HTML로 시각화해서 보여줘",
            hook_path=hook_path,
            env=env,
        )
        state = _run_state_canary(
            workspace,
            output_dir,
            state_root=codex_home,
            hook_path=hook_path,
            env=env,
        )
        _reset_mode_state(codex_home)
        stop = _run_app_server_turn(
            workspace,
            output_dir,
            env,
            model=args.model,
            name="appserver-stop",
            prompt=STOP_PROMPT,
            timeout_sec=args.timeout_sec,
        )
        completion = _run_app_server_turn(
            workspace,
            output_dir,
            env,
            model=args.model,
            name="appserver-completion-summary",
            prompt=COMPLETION_PROMPT,
            timeout_sec=args.timeout_sec,
        )
        _reset_mode_state(codex_home)
        rich = None if args.skip_rich else _run_app_server_turn(
            workspace,
            output_dir,
            env,
            model=args.model,
            name="appserver-rich",
            prompt=RICH_PROMPT,
            timeout_sec=args.timeout_sec,
        )
        mode_events = _read_mode_events(workspace, output_dir, codex_home)
        completion_contract = _completion_summary_contract(completion["final"])
        progress_contract = stop["progress"]
        rich_presentation_contract = None if rich is None else _rich_presentation_contract(rich["final"])
        rich_server_contract = _rich_server_contract(skill_root)
        exec_gap = None if args.skip_exec_gap else _run_codex_exec_expected_gap(workspace, output_dir, env, args.model)

        passed = (
            direct_rich["contains_rich"]
            and direct_rich["contains_rich_best_effort"]
            and state["passed"]
            and state["ultra"]["contains_stop_ultra"]
            and mode_events["has_rich"]
            and mode_events["has_stop"]
            and mode_events["has_neutral"]
            and mode_events["redacts_prompt_and_response"]
            and stop["hook_counts"].get("userPromptSubmit", {}).get("started", 0) >= 1
            and stop["hook_counts"].get("userPromptSubmit", {}).get("completed", 0) >= 1
            and progress_contract["passed"]
            and len(stop["final"]) <= 900
            and completion["hook_counts"].get("userPromptSubmit", {}).get("started", 0) >= 1
            and completion["hook_counts"].get("userPromptSubmit", {}).get("completed", 0) >= 1
            and completion_contract["passed"]
            and (
                args.skip_rich
                or (
                    rich["hook_counts"].get("userPromptSubmit", {}).get("started", 0) >= 1
                    and rich["created_html"]
                    and rich["has_full_view"]
                    and rich_presentation_contract["passed"]
                )
            )
            and rich_server_contract["passed"]
            and (exec_gap is None or exec_gap["expected_gap_observed"])
        )
        result = {
            "passed": bool(passed),
            "model": args.model,
            "workspace": str(workspace),
            "output_dir": str(output_dir),
            "codex_home": str(codex_home),
            "home_root": str(home_root) if home_root is not None else None,
            "preserved_runtime_settings": preserved_runtime_settings,
            "materialize": args.materialize,
            "install_plan": str(plan_path) if plan_path is not None else None,
            "discovered_hook_count": len(discovered_hooks),
            "trusted_hook_count": len(trusted_hooks),
            "direct_rich": direct_rich,
            "state": state,
            "mode_events": mode_events,
            "appserver_stop": stop,
            "progress_contract": progress_contract,
            "appserver_completion_summary": completion,
            "completion_summary_contract_passed": completion_contract,
            "appserver_rich": rich,
            "rich_presentation_contract_passed": rich_presentation_contract,
            "rich_server_fallback_contract": rich_server_contract,
            "codex_exec_expected_gap": exec_gap,
        }
        _write_json(output_dir / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if passed else 1
    except Exception as error:
        failure = {
            "passed": False,
            "failure_type": type(error).__name__,
            "failure": str(error),
            "model": args.model,
            "materialize": args.materialize,
        }
        _write_json(output_dir / "result.json", failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    finally:
        if workspace is not None and args.workspace is None and not args.keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)
        if home_root is not None and not args.keep_workspace:
            shutil.rmtree(home_root, ignore_errors=True)
        elif codex_home is not None and home_root is None:
            shutil.rmtree(codex_home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
