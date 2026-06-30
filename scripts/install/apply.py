from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.install.common import destination_path, load_plan, source_path

RUNTIME_HOOK_DESTINATIONS = {
    ".claude/settings.json",
    ".codex/hooks.json",
    ".agents/hooks.json",
}
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
# Matches a TOML `[agents."<name>"]` header line (allowing surrounding whitespace
# and an optional trailing comment), capturing the quoted agent name. Only the
# agents table family is ownable by the harness; every other table is foreign.
_TOML_AGENTS_TABLE_HEADER = re.compile(
    r'^[ \t]*\[agents\.("(?:[^"\\]|\\.)*"|\'[^\']*\')\][ \t]*(?:#.*)?$'
)
# Matches any top-level TOML table or array-of-tables header line, used to bound
# an agents table region (it ends at the next top-level header or EOF).
_TOML_TABLE_HEADER = re.compile(r"^[ \t]*\[\[?[^\]]")
# Prune only previously generated HarnessKit scanner hook groups during JSON merge.
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


def apply_plan(
    plan_path: Path,
    *,
    target_root: Path,
    overwrite: bool = False,
    allow_runtime_hooks: bool = False,
) -> int:
    plan = load_plan(plan_path)
    if plan.get("mode") != "apply":
        raise ValueError("install plan mode must be apply")
    blocked_gates = [
        gate["id"]
        for gate in plan["activation_gates"]
        if gate.get("required_before_apply") is True
    ]
    if blocked_gates:
        raise ValueError(
            "activation gates require approval before apply: "
            + ", ".join(blocked_gates)
        )
    if _requires_runtime_hook_approval(plan) and not allow_runtime_hooks:
        raise ValueError(
            "runtime hook surfaces require --allow-runtime-hooks after review"
        )

    copy_pairs: list[tuple[Path, Path]] = []
    merge_pairs: list[tuple[Path, Path, dict]] = []
    json_merge_pairs: list[tuple[Path, Path, dict]] = []
    toml_merge_pairs: list[tuple[Path, Path, dict]] = []
    for artifact in plan["artifacts"]:
        src = source_path(artifact)
        dest = destination_path(target_root, artifact)
        if artifact.get("merge_strategy") == "managed-block":
            merge_pairs.append((src, dest, artifact))
            continue
        if artifact.get("merge_strategy") == "json-deep-merge":
            json_merge_pairs.append((src, dest, artifact))
            continue
        if artifact.get("merge_strategy") == "toml-agents-merge":
            toml_merge_pairs.append((src, dest, artifact))
            continue
        if dest.exists() and not overwrite and not _same_file_content(src, dest):
            # Check for exact Routine-Harness generated marker for safe auto-update
            existing_content = dest.read_text(encoding="utf-8")
            if "<!-- BEGIN ROUTINE-HARNESS GENERATED -->" not in existing_content:
                raise ValueError(
                    "destination already exists with different content; "
                    f"rerun with --overwrite after review: {dest.relative_to(target_root)}"
                )
        copy_pairs.append((src, dest))

    count = 0
    for src, dest in copy_pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        count += 1
    for src, dest, artifact in merge_pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = src.read_text(encoding="utf-8").rstrip()
        current = dest.read_text(encoding="utf-8") if dest.exists() else ""
        dest.write_text(_merge_managed_block(current, body, artifact), encoding="utf-8")
        count += 1
    for src, dest, artifact in json_merge_pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = src.read_text(encoding="utf-8")
        current = dest.read_text(encoding="utf-8") if dest.exists() else ""
        dest.write_text(_merge_json_deep(current, body, artifact), encoding="utf-8")
        count += 1
    for src, dest, artifact in toml_merge_pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = src.read_text(encoding="utf-8")
        current = dest.read_text(encoding="utf-8") if dest.exists() else ""
        dest.write_text(_merge_toml_agents(current, body, artifact), encoding="utf-8")
        count += 1
    print(f"applied {count} artifacts to {target_root}")
    return 0


def _same_file_content(left: Path, right: Path) -> bool:
    if not right.is_file():
        return False
    return left.read_bytes() == right.read_bytes()


def _merge_managed_block(current: str, body: str, artifact: dict) -> str:
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
    else:
        before = current[:begin_index]
        after = current[end_index + len(existing_end):]
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


def _merge_json_deep(current: str, body: str, artifact: dict) -> str:
    json_merge_key = artifact.get("json_merge_key")
    if json_merge_key not in {CLAUDE_SETTINGS_JSON_MERGE_KEY, CODEX_HOOKS_JSON_MERGE_KEY}:
        raise ValueError("unsupported json merge key")

    source = json.loads(body)
    if not isinstance(source, dict):
        raise ValueError("json-deep-merge source must be a JSON object")
    if not current.strip():
        return json.dumps(source, indent=2, ensure_ascii=False) + "\n"

    existing = json.loads(current)
    if not isinstance(existing, dict):
        raise ValueError("json-deep-merge destination must be a JSON object")
    merged = _deep_merge_settings(existing, source, json_merge_key=json_merge_key)
    return json.dumps(merged, indent=2, ensure_ascii=False) + "\n"


def _merge_toml_agents(current: str, body: str, artifact: dict) -> str:
    """Preserving TOML merge for codex `.codex/config.toml`.

    The harness owns ONLY the `[agents."<name>"]` registration tables present in
    the source ``body`` (build.py emits the source as exactly the owned tables).
    Every other byte of ``current`` is preserved verbatim: this is a raw-text
    region splice, NOT a parse-and-reserialize (``tomllib`` has no writer and we
    must not lose comments, ordering, or foreign tables).

    Algorithm:
      1. Derive the owned table-name set from the source ``body``.
      2. Remove every owned `[agents."<name>"]` region from ``current``
         (a region runs from its header line to the next top-level table header
         or EOF). Foreign `[agents."<x>"]` tables (names not in the owned set)
         and all non-agents content are left untouched.
      3. Append the source owned tables verbatim.
      4. ``tomllib.loads`` both source and merged text as a validity gate.

    Idempotent: re-applying yields a stable fixed point.
    """
    toml_merge_key = artifact.get("toml_merge_key")
    if toml_merge_key != CODEX_AGENTS_TOML_MERGE_KEY:
        raise ValueError("unsupported toml merge key")

    # Validity gate: the source must be parseable TOML.
    try:
        tomllib.loads(body)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"toml-agents-merge source is not valid TOML: {exc}") from exc

    owned_names = _toml_owned_agent_names(body)
    source_block = body.strip("\n")

    if not current.strip():
        return source_block + "\n" if source_block else ""

    # Validity gate: the destination must be parseable TOML before we splice it.
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

    # Validity gate: the merged result must remain parseable TOML.
    try:
        tomllib.loads(merged)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"toml-agents-merge result is not valid TOML: {exc}"
        ) from exc
    return merged


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
            # Drop this owned region: from the header up to (not including) the
            # next top-level table header or EOF.
            index += 1
            while index < total and not _TOML_TABLE_HEADER.match(
                lines[index].rstrip("\n")
            ):
                index += 1
            continue
        kept.append(line)
        index += 1
    return "".join(kept)


# Public aliases for the install-core merge primitives. verify.py consumes these
# stable names so it no longer reaches into apply.py's private surface. The
# private implementations (and their dependency cluster) intentionally stay in
# place; these are thin name aliases with identical behavior.
merge_managed_block = _merge_managed_block
merge_json_deep = _merge_json_deep
merge_toml_agents = _merge_toml_agents


def _deep_merge_settings(existing: dict, source: dict, *, json_merge_key: str) -> dict:
    merged = copy.deepcopy(existing)
    for key, value in source.items():
        if key == "hooks":
            merged[key] = _merge_json_hooks(
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


def _merge_json_hooks(
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


def _requires_runtime_hook_approval(plan: dict) -> bool:
    gated_targets = {
        gate["target"]
        for gate in plan["activation_gates"]
        if gate.get("required_before_runtime") is True
    }
    return any(
        artifact.get("target") in gated_targets
        and artifact.get("destination") in RUNTIME_HOOK_DESTINATIONS
        for artifact in plan["artifacts"]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a HarnessKit install plan.")
    parser.add_argument("plan", help="Path to install plan JSON/YAML")
    parser.add_argument("--target-root", required=True, help="Target project root")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing destination files after reviewing the install plan.",
    )
    parser.add_argument(
        "--allow-runtime-hooks",
        action="store_true",
        help="Materialize hook runtime surfaces after reviewing activation gates.",
    )
    args = parser.parse_args(argv)

    try:
        return apply_plan(
            Path(args.plan),
            target_root=Path(args.target_root),
            overwrite=args.overwrite,
            allow_runtime_hooks=args.allow_runtime_hooks,
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
