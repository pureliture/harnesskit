from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.install.common import (
    HERMES_EXTERNAL_CONFIG_DIR,
    HERMES_EXTERNAL_MARKER,
    artifact_count_summary,
    destination_path,
    load_plan,
    is_legacy_hermes_external_config_dir,
    retired_codex_agent_registrations,
    source_path,
)
from scripts.install.managed_artifacts import (
    marker_matches,
    merge_json_deep,
    merge_json_hooks,
    merge_managed_block,
    merge_toml_agents,
    prune_retired_codex_agent_registrations,
)

# Compatibility aliases for existing install-core consumers. The shared policy
# itself lives in managed_artifacts so apply and verify cannot diverge.
_marker_matches = marker_matches
_merge_managed_block = merge_managed_block
_merge_json_deep = merge_json_deep
_merge_toml_agents = merge_toml_agents
_merge_json_hooks = merge_json_hooks

RUNTIME_HOOK_DESTINATIONS = {
    ".claude/settings.json",
    ".codex/hooks.json",
    ".agents/hooks.json",
    ".hermes/config.yaml",
}


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
    if plan.get("hermes_external_package") is not None:
        return _apply_hermes_external_package(plan["hermes_external_package"], target_root)
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

    retired_registrations = retired_codex_agent_registrations(plan)
    _preflight_retired_codex_agent_registrations(
        target_root,
        retired_registrations,
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
    if retired_registrations:
        config_path = target_root / ".codex/config.toml"
        current = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        pruned = prune_retired_codex_agent_registrations(current, retired_registrations)
        if pruned != current:
            config_path.write_text(pruned, encoding="utf-8")
    summary = artifact_count_summary(plan["artifacts"])
    print(
        "applied "
        f"{summary['source_artifact_count']} source artifacts to "
        f"{summary['unique_final_destination_count']} unique destinations in "
        f"{target_root}; partial rollback=not-provided"
    )
    return 0


def _apply_hermes_external_package(external: dict, target_root: Path) -> int:
    """Apply only the explicit root Hermes package lifecycle.

    This deliberately never invokes Hermes and never writes its consent allowlist
    during install/update.  Uninstall removes a consent record only when both its
    event and command exactly match the marker-owned registration.
    """
    lifecycle = external["lifecycle"]
    package = _hermes_target_path(
        target_root, external["package_root"], "Hermes package root"
    )
    config = _hermes_target_path(
        target_root, external["config"], "Hermes root config"
    )
    hooks = _hermes_target_path(
        target_root, external["hook_root"], "Hermes hook root"
    )
    source = REPO_ROOT / external["source"]
    skills_source = REPO_ROOT / external["skills_source"]
    hooks_source = REPO_ROOT / external["hooks_source"]
    config_source = REPO_ROOT / external["config_source"]
    command = external["hook_command"]
    event = external["hook_event"]
    marker = package.parent / external.get("marker", HERMES_EXTERNAL_MARKER)

    if lifecycle == "cleanup":
        print("hermes external package cleanup=preserved package=config-entry=hook=consent")
        return 0
    if lifecycle in {"install", "update"}:
        # Prove source and textual merge safety before replacing an existing package.
        _validate_external_package_source(source, skills_source, hooks_source, config_source, event, external["catalog"])
        merged_config = _render_hermes_config(config, event, command)
        _replace_managed_package(source, skills_source, package, marker, external["catalog"])
        _copy_tree(hooks_source, hooks)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(merged_config, encoding="utf-8")
        print(f"hermes external package {lifecycle}=applied consent=not-created runtime=deferred")
        return 0
    if lifecycle == "uninstall":
        marker_is_owned = (
            not marker.is_symlink()
            and marker.is_file()
            and _marker_matches(marker, external["catalog"])
        )
        package_is_safe = not package.is_symlink() and (
            not package.exists() or package.is_dir()
        )
        if marker_is_owned and package_is_safe:
            if package.exists():
                shutil.rmtree(package)
            marker.unlink()
            _remove_hermes_config(config, package, event, command)
            _remove_exact_consent(target_root / ".hermes/shell-hooks-allowlist.json", event, command)
        else:
            print("hermes external package uninstall=preserved-unowned")
            return 0
        print("hermes external package uninstall=completed exact-matches-only")
        return 0
    raise ValueError("unsupported Hermes external package lifecycle")


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists() and destination.is_symlink():
        raise ValueError(f"refusing symlinked Hermes destination: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        target = destination / rel
        if item.is_symlink():
            raise ValueError(f"refusing symlinked Hermes source: {item}")
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _hermes_target_path(target_root: Path, relative_path: str, field_name: str) -> Path:
    """Return a target path only when every managed path component is real.

    The Hermes external package is a user-scoped installer surface.  A symlink
    under the managed root could otherwise turn a normal refresh into a write
    outside the selected user root.  Normal user-owned directories are created
    later by the installer; existing symlinked components are deliberately not
    adopted.
    """
    path = target_root / relative_path
    try:
        path.resolve().relative_to(target_root.resolve())
    except ValueError as exc:
        raise ValueError(f"{field_name} escapes target root") from exc
    relative = path.relative_to(target_root)
    current = target_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"refusing symlinked {field_name}: {current}")
    return path


def _validate_external_package_source(source: Path, skills_source: Path, hooks_source: Path, config_source: Path, event: str, catalog: list[str]) -> None:
    if (
        source.is_symlink()
        or skills_source.is_symlink()
        or hooks_source.is_symlink()
        or config_source.is_symlink()
        or not skills_source.is_dir()
        or not hooks_source.is_dir()
        or not config_source.is_file()
    ):
        raise FileNotFoundError("missing Hermes external package generated source")
    source_marker = source / HERMES_EXTERNAL_MARKER
    if (
        source_marker.is_symlink()
        or not source_marker.is_file()
        or not _marker_matches(source_marker, catalog)
    ):
        raise ValueError("Hermes external package source marker is missing or unowned")
    generated_config = _load_hermes_yaml(config_source)
    if generated_config.get("hooks", {}).get(event) != [{"command": "hooks/optimal-response/optimal-response-pre-llm.cjs", "timeout": 5}]:
        raise ValueError("Hermes external package config source lacks exact hook registration")
    if not (hooks_source / "optimal-response-pre-llm.cjs").is_file():
        raise FileNotFoundError("missing Hermes optimal-response hook source")
    if not (hooks_source / "stop-prompt-submit.cjs").is_file():
        raise FileNotFoundError("missing Hermes optimal-response hook asset source")
    expected_skills = {component.removeprefix("harnesskit.skill.") for component in catalog}
    skills_category = skills_source / "software-development"
    actual_skills = {path.name for path in skills_category.iterdir() if path.is_dir()}
    if actual_skills != expected_skills or any(
        not (skills_category / skill / "SKILL.md").is_file() for skill in expected_skills
    ):
        raise ValueError("Hermes external package source skills do not exactly match catalog")


def _replace_managed_package(source: Path, skills_source: Path, package: Path, marker: Path, catalog: list[str]) -> None:
    if package.exists() and (package.is_symlink() or not package.is_dir()):
        raise ValueError(f"refusing unsafe Hermes package destination: {package}")
    if marker.is_symlink():
        raise ValueError(f"refusing symlinked Hermes package marker: {marker}")
    marker_is_owned = marker.is_file() and _marker_matches(marker, catalog)
    if package.exists() and not marker_is_owned:
        raise ValueError("Hermes package exists without deterministic HarnessKit marker")
    if marker.exists() and not marker_is_owned:
        raise ValueError("Hermes package marker is not owned by this exact catalog")
    if package.exists():
        shutil.rmtree(package)
    source_marker = source / HERMES_EXTERNAL_MARKER
    if source_marker.is_symlink():
        raise ValueError("refusing symlinked Hermes package source marker")
    _copy_tree(skills_source, package)
    marker.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_marker, marker)


def _load_hermes_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Hermes root config must be a mapping")
    return data


def _write_hermes_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _render_hermes_config(path: Path, event: str, command: str) -> str:
    data = _load_hermes_yaml(path)
    skills = data.setdefault("skills", {})
    hooks = data.setdefault("hooks", {})
    if not isinstance(skills, dict) or not isinstance(hooks, dict):
        raise ValueError("unsupported unsafe Hermes config shape")
    dirs = skills.setdefault("external_dirs", [])
    if not isinstance(dirs, list) or not all(isinstance(item, str) for item in dirs):
        raise ValueError("unsupported unsafe Hermes skills.external_dirs shape")
    package_text = HERMES_EXTERNAL_CONFIG_DIR
    needs_external_dir = package_text not in dirs
    registrations = hooks.setdefault(event, [])
    if not isinstance(registrations, list) or not all(isinstance(item, dict) for item in registrations):
        raise ValueError("unsupported unsafe Hermes hook registration shape")
    exact = {"command": command, "timeout": 5}
    needs_registration = exact not in registrations
    # Do not reserialize a user config: safe_dump would discard comments and
    # reorder foreign fields.  The supported shape is deliberately narrow and
    # edited as raw list insertions after the structural validation above.
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    current = _yaml_remove_matching_list_value(
        current,
        "skills",
        "external_dirs",
        is_legacy_hermes_external_config_dir,
    )
    if needs_external_dir:
        current = _yaml_ensure_list_value(current, "skills", "external_dirs", package_text)
    if needs_registration:
        current = _yaml_ensure_mapping_list_value(current, "hooks", event, exact)
    return current


def _remove_hermes_config(path: Path, package: Path, event: str, command: str) -> None:
    if not path.exists():
        return
    _load_hermes_yaml(path)  # fail closed on malformed documents
    current = path.read_text(encoding="utf-8")
    current = _yaml_remove_list_value(current, "skills", "external_dirs", HERMES_EXTERNAL_CONFIG_DIR)
    current = _yaml_remove_matching_list_value(
        current,
        "skills",
        "external_dirs",
        is_legacy_hermes_external_config_dir,
    )
    current = _yaml_remove_mapping_list_value(current, "hooks", event, command)
    path.write_text(current, encoding="utf-8")


def _yaml_ensure_list_value(current: str, top: str, child: str, value: str) -> str:
    lines = current.splitlines(keepends=True)
    top_index = _yaml_top_index(lines, top)
    if top_index is None:
        return current.rstrip("\n") + f"\n{top}:\n  {child}:\n    - {value}\n"
    end = next((i for i in range(top_index + 1, len(lines)) if lines[i] and not lines[i].startswith((" ", "\t", "\n"))), len(lines))
    child_index = _yaml_child_index(lines, top_index, end, child)
    if child_index is None:
        lines.insert(end, f"  {child}:\n    - {value}\n")
    else:
        child_end = _yaml_child_end(lines, child_index, end)
        if any(lines[index] == f"    - {value}\n" for index in range(child_index + 1, child_end)):
            return current
        lines.insert(child_end, f"    - {value}\n")
    return "".join(lines)


def _yaml_ensure_mapping_list_value(current: str, top: str, child: str, value: dict) -> str:
    command = value["command"]
    lines = current.splitlines(keepends=True)
    top_index = _yaml_top_index(lines, top)
    block = f"  {child}:\n    - command: {command}\n      timeout: 5\n"
    if top_index is None:
        return current.rstrip("\n") + f"\n{top}:\n{block}"
    end = next((i for i in range(top_index + 1, len(lines)) if lines[i] and not lines[i].startswith((" ", "\t", "\n"))), len(lines))
    child_index = _yaml_child_index(lines, top_index, end, child)
    if child_index is None:
        lines.insert(end, block)
    else:
        child_end = _yaml_child_end(lines, child_index, end)
        if any(
            lines[index] == f"    - command: {command}\n"
            and lines[index + 1] == "      timeout: 5\n"
            for index in range(child_index + 1, child_end - 1)
        ):
            return current
        lines.insert(child_end, f"    - command: {command}\n      timeout: 5\n")
    return "".join(lines)


def _yaml_top_index(lines: list[str], top: str) -> int | None:
    prefix = f"{top}:"
    matches: list[int] = []
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        suffix = line[len(prefix):].strip()
        if suffix and not suffix.startswith("#"):
            raise ValueError("unsupported unsafe Hermes top-level YAML shape")
        matches.append(index)
    if len(matches) > 1:
        raise ValueError("duplicate Hermes top-level YAML key")
    return matches[0] if matches else None


def _yaml_key_suffix_is_safe(line: str, prefix: str) -> bool:
    suffix = line[len(prefix):].strip()
    return not suffix or suffix.startswith("#")


def _yaml_child_index(lines: list[str], start: int, end: int, child: str) -> int | None:
    matches = [i for i in range(start + 1, end) if lines[i].startswith(f"  {child}:")]
    if len(matches) > 1:
        raise ValueError("duplicate Hermes managed YAML key")
    if matches and not _yaml_key_suffix_is_safe(lines[matches[0]], f"  {child}:"):
        raise ValueError("unsupported inline Hermes managed YAML shape")
    return matches[0] if matches else None


def _yaml_child_end(lines: list[str], child_index: int, section_end: int) -> int:
    return next(
        (
            index
            for index in range(child_index + 1, section_end)
            if lines[index].startswith("  ") and not lines[index].startswith("    ")
        ),
        section_end,
    )


def _yaml_remove_list_value(current: str, top: str, child: str, value: str) -> str:
    lines = current.splitlines(keepends=True)
    top_index = _yaml_top_index(lines, top)
    if top_index is None:
        return current
    end = next((i for i in range(top_index + 1, len(lines)) if lines[i] and not lines[i].startswith((" ", "\t", "\n"))), len(lines))
    child_index = _yaml_child_index(lines, top_index, end, child)
    if child_index is None:
        return current
    child_end = next((i for i in range(child_index + 1, end) if lines[i].startswith("  ") and not lines[i].startswith("    ")), end)
    for index in range(child_index + 1, child_end):
        if _yaml_list_item_equals(lines[index], value):
            del lines[index]
            break
    return "".join(lines)


def _yaml_remove_matching_list_value(
    current: str,
    top: str,
    child: str,
    predicate: Callable[[object], bool],
) -> str:
    lines = current.splitlines(keepends=True)
    top_index = _yaml_top_index(lines, top)
    if top_index is None:
        return current
    end = next(
        (
            i
            for i in range(top_index + 1, len(lines))
            if lines[i] and not lines[i].startswith((" ", "\t", "\n"))
        ),
        len(lines),
    )
    child_index = _yaml_child_index(lines, top_index, end, child)
    if child_index is None:
        return current
    child_end = next(
        (
            i
            for i in range(child_index + 1, end)
            if lines[i].startswith("  ") and not lines[i].startswith("    ")
        ),
        end,
    )
    for index in range(child_end - 1, child_index, -1):
        if not lines[index].startswith("    - "):
            continue
        try:
            parsed = yaml.safe_load(lines[index].removeprefix("    - "))
        except yaml.YAMLError:
            continue
        if predicate(parsed):
            del lines[index]
    return "".join(lines)


def _yaml_list_item_equals(line: str, value: str) -> bool:
    if not line.startswith("    - "):
        return False
    try:
        return yaml.safe_load(line.removeprefix("    - ")) == value
    except yaml.YAMLError:
        return False


def _yaml_remove_mapping_list_value(current: str, top: str, child: str, command: str) -> str:
    lines = current.splitlines(keepends=True)
    top_index = _yaml_top_index(lines, top)
    if top_index is None:
        return current
    end = next((i for i in range(top_index + 1, len(lines)) if lines[i] and not lines[i].startswith((" ", "\t", "\n"))), len(lines))
    child_index = _yaml_child_index(lines, top_index, end, child)
    if child_index is None:
        return current
    child_end = next((i for i in range(child_index + 1, end) if lines[i].startswith("  ") and not lines[i].startswith("    ")), end)
    for index in range(child_index + 1, child_end - 1):
        if lines[index] == f"    - command: {command}\n" and lines[index + 1] == "      timeout: 5\n":
            del lines[index:index + 2]
            break
    return "".join(lines)


def _remove_exact_consent(path: Path, event: str, command: str) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Hermes shell-hook allowlist must be valid JSON") from exc
    if isinstance(data, dict):
        approvals = data.get("approvals")
        if not isinstance(approvals, list):
            raise ValueError("unsupported Hermes shell-hook approvals shape")
        kept = [item for item in approvals if not (isinstance(item, dict) and item.get("event") == event and item.get("command") == command)]
        if kept != approvals:
            data["approvals"] = kept
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    if not isinstance(data, list):
        raise ValueError("unsupported Hermes shell-hook allowlist shape")
    kept = [item for item in data if not (isinstance(item, dict) and item.get("event") == event and item.get("command") == command)]
    if kept != data:
        path.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _same_file_content(left: Path, right: Path) -> bool:
    if not right.is_file():
        return False
    return left.read_bytes() == right.read_bytes()


def _preflight_retired_codex_agent_registrations(
    target_root: Path,
    registrations: list[dict[str, str]],
) -> None:
    if not registrations:
        return
    config_path = target_root / ".codex/config.toml"
    if not config_path.exists():
        return
    prune_retired_codex_agent_registrations(
        config_path.read_text(encoding="utf-8"),
        registrations,
    )


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
