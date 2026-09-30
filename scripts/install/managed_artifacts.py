"""Shared, preserving merge policy for managed install artifacts.

This module intentionally has no install-CLI dependencies so both apply and
verify can use the identical policy without importing one another.
"""

from __future__ import annotations

import copy
import json
import re
import tomllib
from pathlib import Path


HARNESSKIT_MANAGED_BLOCK = (
    "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->",
    "<!-- END HARNESSKIT GENERATED CONTEXT -->",
)
LEGACY_ROUTINE_HARNESS_MANAGED_BLOCK = (
    "<!-- BEGIN ROUTINE-HARNESS GENERATED CONTEXT -->",
    "<!-- END ROUTINE-HARNESS GENERATED CONTEXT -->",
)
CLAUDE_SETTINGS_JSON_MERGE_KEY = "claude-settings-hooks"
CODEX_HOOKS_JSON_MERGE_KEY = "codex-hooks"
CODEX_AGENTS_TOML_MERGE_KEY = "codex-agents"

_TOML_AGENTS_TABLE_HEADER = re.compile(
    r'^[ \t]*\[agents\.("(?:[^"\\]|\\.)*"|\'[^\']*\')\][ \t]*(?:#.*)?$'
)
_TOML_TABLE_HEADER = re.compile(r"^[ \t]*\[\[?[^\]]")

LEGACY_HUMAN_DOC_TURN_SCAN_COMMAND_TOKENS = (
    "human_doc_turn_scan.py",
    ".harnesskit/scripts/human_doc_turn_scan.py",
)
HARNESSKIT_CLAUDE_HOOK_COMMAND_TOKENS = (
    *LEGACY_HUMAN_DOC_TURN_SCAN_COMMAND_TOKENS,
    ".claude/skills/optimal-response/hooks/stop-prompt-submit.cjs",
    ".claude/skills/optimal-response/hooks/stop-session-start.cjs",
    "/.claude/skills/optimal-response/hooks/stop-prompt-submit.cjs",
    "/.claude/skills/optimal-response/hooks/stop-session-start.cjs",
)
HARNESSKIT_CODEX_HOOK_COMMAND_TOKENS = (
    *LEGACY_HUMAN_DOC_TURN_SCAN_COMMAND_TOKENS,
    ".codex/skills/optimal-response/hooks/stop-prompt-submit.cjs",
    "/.codex/skills/optimal-response/hooks/stop-prompt-submit.cjs",
    "${CODEX_HOME:-$HOME/.codex}/skills/optimal-response/hooks/stop-prompt-submit.cjs",
)


def marker_matches(marker: Path, external_catalog: list[str] | None) -> bool:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        data.get("format") == "harnesskit.hermes.external-package"
        and data.get("version") == 1
        and data.get("ownership") == "harnesskit-managed-external-package"
        and data.get("adapter_id") == "harnesskit.adapter.hermes"
        and isinstance(data.get("catalog"), list)
        and len(data["catalog"]) == 25
        and all(isinstance(component_id, str) for component_id in data["catalog"])
        and (external_catalog is None or data.get("catalog") == external_catalog)
    )


def merge_managed_block(current: str, body: str, artifact: dict) -> str:
    begin = artifact["begin_marker"]
    end = artifact["end_marker"]
    if begin == end:
        raise ValueError("managed block begin_marker and end_marker must differ")
    block = f"{begin}\n{body}\n{end}"
    marker_pair = _find_existing_managed_block_markers(current, begin, end)
    if marker_pair is None:
        return f"{current.rstrip()}\n\n{block}\n"

    existing_begin, existing_end = marker_pair
    begin_index = current.index(existing_begin)
    end_index = current.index(existing_end)
    if end_index <= begin_index:
        raise ValueError("managed block end_marker must appear after begin_marker")
    before = current[:begin_index]
    after = current[end_index + len(existing_end) :]
    return f"{before}{block}{after}"


def _find_existing_managed_block_markers(
    current: str,
    begin: str,
    end: str,
) -> tuple[str, str] | None:
    marker_pairs = [(begin, end)]
    if (begin, end) == HARNESSKIT_MANAGED_BLOCK:
        marker_pairs.append(LEGACY_ROUTINE_HARNESS_MANAGED_BLOCK)

    found: list[tuple[str, str]] = []
    for candidate_begin, candidate_end in marker_pairs:
        begin_count = current.count(candidate_begin)
        end_count = current.count(candidate_end)
        if begin_count == 0 and end_count == 0:
            continue
        if begin_count != 1 or end_count != 1:
            raise ValueError(
                "managed block markers must be absent or appear exactly once"
            )
        found.append((candidate_begin, candidate_end))

    if len(found) > 1:
        raise ValueError("managed block markers must be absent or appear exactly once")
    if not found:
        return None
    return found[0]


def merge_json_deep(current: str, body: str, artifact: dict) -> str:
    json_merge_key = artifact.get("json_merge_key")
    if json_merge_key not in {
        CLAUDE_SETTINGS_JSON_MERGE_KEY,
        CODEX_HOOKS_JSON_MERGE_KEY,
    }:
        raise ValueError("unsupported json merge key")

    source = json.loads(body)
    if not isinstance(source, dict):
        raise ValueError("json-deep-merge source must be a JSON object")
    if not current.strip():
        return json.dumps(source, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    existing = json.loads(current)
    if not isinstance(existing, dict):
        raise ValueError("json-deep-merge destination must be a JSON object")
    merged = _deep_merge_settings(existing, source, json_merge_key=json_merge_key)
    return json.dumps(merged, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _deep_merge_settings(existing: dict, source: dict, *, json_merge_key: str) -> dict:
    merged = copy.deepcopy(existing)
    for key, value in source.items():
        if key == "hooks":
            merged[key] = merge_json_hooks(
                merged.get(key),
                value,
                managed_command_tokens=(
                    HARNESSKIT_CODEX_HOOK_COMMAND_TOKENS
                    if json_merge_key == CODEX_HOOKS_JSON_MERGE_KEY
                    else HARNESSKIT_CLAUDE_HOOK_COMMAND_TOKENS
                ),
            )
            continue
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_settings(
                current,
                value,
                json_merge_key=json_merge_key,
            )
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def merge_json_hooks(
    existing_raw,
    source_raw,
    *,
    managed_command_tokens: tuple[str, ...],
) -> dict:
    if not isinstance(source_raw, dict):
        raise ValueError("json-deep-merge source hooks must be a JSON object")
    if existing_raw is None:
        existing = {}
    elif isinstance(existing_raw, dict):
        existing = copy.deepcopy(existing_raw)
    else:
        raise ValueError("json-deep-merge destination hooks must be a JSON object")
    source_commands = set(_iter_hook_commands(source_raw))

    merged: dict = {}
    for event, groups in existing.items():
        if not isinstance(groups, list):
            raise ValueError(f"settings hooks event must be a list: {event}")
        retained_groups = [
            group
            for group in groups
            if not _hook_group_is_harnesskit_managed(
                group,
                source_commands,
                managed_command_tokens=managed_command_tokens,
            )
        ]
        if retained_groups:
            merged[event] = retained_groups

    for event, groups in source_raw.items():
        if not isinstance(groups, list):
            raise ValueError(f"settings hooks event must be a list: {event}")
        target_groups = merged.setdefault(event, [])
        for group in groups:
            copied_group = copy.deepcopy(group)
            if copied_group not in target_groups:
                target_groups.append(copied_group)
    return merged


def _hook_group_is_harnesskit_managed(
    group,
    source_commands: set[str],
    *,
    managed_command_tokens: tuple[str, ...],
) -> bool:
    for command in _iter_hook_commands_from_group(group):
        if command in source_commands:
            return True
        if any(token in command for token in managed_command_tokens):
            return True
    return False


def _iter_hook_commands(hooks_map: dict):
    for groups in hooks_map.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            yield from _iter_hook_commands_from_group(group)


def _iter_hook_commands_from_group(group):
    if not isinstance(group, dict):
        return
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command")
        if isinstance(command, str):
            yield command


def merge_toml_agents(current: str, body: str, artifact: dict) -> str:
    """Preserving TOML merge for Codex ``.codex/config.toml``."""
    toml_merge_key = artifact.get("toml_merge_key")
    if toml_merge_key != CODEX_AGENTS_TOML_MERGE_KEY:
        raise ValueError("unsupported toml merge key")

    try:
        tomllib.loads(body)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"toml-agents-merge source is not valid TOML: {exc}") from exc

    owned_names = _toml_owned_agent_names(body)
    source_block = body.strip("\n")

    if not current.strip():
        return source_block + "\n" if source_block else ""

    try:
        tomllib.loads(current)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"toml-agents-merge destination is not valid TOML: {exc}"
        ) from exc

    preserved = _toml_strip_owned_agent_regions(current, owned_names)
    preserved_body = preserved.rstrip("\n")

    if not source_block:
        merged = (preserved_body + "\n") if preserved_body else ""
    elif not preserved_body:
        merged = source_block + "\n"
    else:
        merged = f"{preserved_body}\n{source_block}\n"

    try:
        tomllib.loads(merged)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"toml-agents-merge result is not valid TOML: {exc}"
        ) from exc
    return merged


def prune_retired_codex_agent_registrations(
    current: str,
    registrations: list[dict[str, str]],
) -> str:
    """Remove only an exactly signed retired user registration from Codex TOML."""
    if not registrations or not current.strip():
        return current
    try:
        parsed = tomllib.loads(current)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"retired Codex agent registration destination is not valid TOML: {exc}"
        ) from exc

    agents = parsed.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError(
            "retired Codex agent registration destination has invalid agents table"
        )

    removable_names: set[str] = set()
    for registration in registrations:
        name = registration["name"]
        configured = agents.get(name)
        if configured is None:
            continue
        if (
            not isinstance(configured, dict)
            or configured.get("config_file") != registration["config_file"]
        ):
            raise ValueError(
                "retired Codex agent registration conflicts with user configuration; "
                f"preserved: {name}"
            )
        removable_names.add(name)

    if not removable_names:
        return current
    pruned = _toml_strip_agent_regions_by_name(current, removable_names)
    try:
        tomllib.loads(pruned)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"retired Codex agent registration result is not valid TOML: {exc}"
        ) from exc
    return pruned


def _toml_owned_agent_names(body: str) -> set[str]:
    owned: set[str] = set()
    for line in body.splitlines():
        match = _TOML_AGENTS_TABLE_HEADER.match(line)
        if match is not None:
            owned.add(match.group(1))
    return owned


def _toml_strip_owned_agent_regions(current: str, owned_names: set[str]) -> str:
    lines = current.splitlines(keepends=True)
    kept: list[str] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        match = _TOML_AGENTS_TABLE_HEADER.match(line.rstrip("\n"))
        if match is not None and match.group(1) in owned_names:
            index += 1
            while index < total and not _TOML_TABLE_HEADER.match(
                lines[index].rstrip("\n")
            ):
                index += 1
            continue
        kept.append(line)
        index += 1
    return "".join(kept)


def _toml_strip_agent_regions_by_name(current: str, names: set[str]) -> str:
    lines = current.splitlines(keepends=True)
    kept: list[str] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        match = _TOML_AGENTS_TABLE_HEADER.match(line.rstrip("\n"))
        if match is not None and _toml_agent_header_name(match.group(1)) in names:
            if kept and not kept[-1].strip():
                kept.pop()
            index += 1
            while index < total and not _TOML_TABLE_HEADER.match(
                lines[index].rstrip("\n")
            ):
                index += 1
            continue
        kept.append(line)
        index += 1
    return "".join(kept)


def _toml_agent_header_name(raw_name: str) -> str:
    try:
        parsed = tomllib.loads(f"name = {raw_name}\n")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid Codex agent table name: {raw_name}") from exc
    name = parsed.get("name")
    if not isinstance(name, str):
        raise ValueError(f"invalid Codex agent table name: {raw_name}")
    return name
