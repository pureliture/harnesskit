from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.adapters.build import (  # noqa: E402
    _combined_append_content,
    _render_component,
    _selected_registry_entries,
)

PROFILES_DIR = REPO_ROOT / "profiles"

PROFILE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

RUNTIME_SURFACES = {
    "project": [
        {
            "target": "project",
            "path": ".harnesskit",
            "source": "dist/project/.harnesskit",
        },
        {
            "target": "project",
            "path": "AGENTS.md",
            "source": "dist/project/AGENTS.md",
        },
        {
            "target": "project",
            "path": "CLAUDE.md",
            "source": "dist/project/CLAUDE.md",
        },
        {
            "target": "project",
            "path": ".github/ISSUE_TEMPLATE",
            "source": "dist/project/.github/ISSUE_TEMPLATE",
        }
    ],
    "claude": [
        {
            "target": "claude",
            "path": ".claude/skills",
            "source": "dist/claude/.claude/skills",
        },
        {
            "target": "claude",
            "path": ".claude/agents",
            "source": "dist/claude/.claude/agents",
        },
        {
            "target": "claude",
            "path": ".claude/settings.json",
            "source": "dist/claude/.claude/settings.json",
        },
    ],
    "codex": [
        {
            "target": "codex",
            "path": ".agents/skills",
            "source": "dist/codex/.agents/skills",
        },
        {
            "target": "codex",
            "path": ".codex/skills",
            "source": "dist/codex/.codex/skills",
        },
        {
            "target": "codex",
            "path": ".agents/templates",
            "source": "dist/codex/.agents/templates",
        },
        {
            "target": "codex",
            "path": ".codex/agents",
            "source": "dist/codex/.codex/agents",
        },
        {
            "target": "codex",
            "path": ".codex/config.toml",
            "source": "dist/codex/.codex/config.toml",
        },
        {
            "target": "codex",
            "path": ".codex/hooks.json",
            "source": "dist/codex/.codex/hooks.json",
        },
    ],
    "antigravity": [
        {
            "target": "antigravity",
            "path": ".agents/skills",
            "source": "dist/antigravity/.agents/skills",
        },
        {
            "target": "antigravity",
            "path": ".gemini/config/skills",
            "source": "dist/antigravity/.gemini/config/skills",
        },
        {
            "target": "antigravity",
            "path": ".agents/agents",
            "source": "dist/antigravity/.agents/agents",
        },
        {
            "target": "antigravity",
            "path": ".agents/hooks.json",
            "source": "dist/antigravity/.agents/hooks.json",
        },
    ],
}

ACTIVATION_GATES = {
    "claude": [
        {
            "id": "claude-stop-hook-review",
            "target": "claude",
            "reason": (
                "Claude Stop hook changes project runtime behavior and should be "
                "reviewed separately from file materialization."
            ),
            "required_before_apply": False,
            "required_before_runtime": True,
        }
    ],
    "codex": [
        {
            "id": "codex-hook-trust",
            "target": "codex",
            "reason": (
                "Codex project hooks require user-level trust state before runtime "
                "execution."
            ),
            "required_before_apply": False,
            "required_before_runtime": True,
        }
    ],
    "antigravity": [
        {
            "id": "antigravity-hook-runtime-review",
            "target": "antigravity",
            "reason": (
                "Antigravity project hooks execute local commands and need runtime "
                "review before use."
            ),
            "required_before_apply": False,
            "required_before_runtime": True,
        }
    ],
}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML mapping expected: {path}")
    return data


def _profile_name(profile: str) -> str:
    name = profile.removeprefix("harnesskit.profile.")
    if not PROFILE_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid profile name: {profile}")
    return name


def _load_profile(profile: str) -> tuple[str, dict[str, Any]]:
    name = _profile_name(profile)
    path = PROFILES_DIR / f"{name}.yml"
    if not path.is_file():
        raise FileNotFoundError(f"Missing profile: {profile}")
    return name, _load_yaml(path)


def _surface_key_for_artifact(artifact: dict[str, Any]) -> tuple[str, str] | None:
    target = artifact["target"]
    destination = artifact["destination"]

    if target == "claude":
        if destination.startswith(".claude/skills/"):
            return ("claude", ".claude/skills")
        if destination.startswith(".claude/agents/"):
            return ("claude", ".claude/agents")
        if destination == ".claude/settings.json":
            return ("claude", ".claude/settings.json")
    if target == "codex":
        if destination.startswith(".agents/skills/"):
            return ("codex", ".agents/skills")
        if destination.startswith(".codex/skills/"):
            return ("codex", ".codex/skills")
        if destination.startswith(".agents/templates/"):
            return ("codex", ".agents/templates")
        if destination.startswith(".codex/agents/"):
            return ("codex", ".codex/agents")
        if destination == ".codex/config.toml":
            return ("codex", ".codex/config.toml")
        if destination == ".codex/hooks.json":
            return ("codex", ".codex/hooks.json")
    if target == "project":
        if destination.startswith(".harnesskit/"):
            return ("project", ".harnesskit")
        if destination == "AGENTS.md":
            return ("project", "AGENTS.md")
        if destination == "CLAUDE.md":
            return ("project", "CLAUDE.md")
        if destination.startswith(".github/ISSUE_TEMPLATE/"):
            return ("project", ".github/ISSUE_TEMPLATE")
    if target == "antigravity":
        if destination.startswith(".agents/skills/"):
            return ("antigravity", ".agents/skills")
        if destination.startswith(".gemini/config/skills/"):
            return ("antigravity", ".gemini/config/skills")
        if destination.startswith(".agents/agents/"):
            return ("antigravity", ".agents/agents")
        if destination == ".agents/hooks.json":
            return ("antigravity", ".agents/hooks.json")
    return None


def _runtime_surfaces(targets: list[str], artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    surface_by_key = {
        (surface["target"], surface["path"]): surface
        for target in targets
        for surface in RUNTIME_SURFACES.get(target, [])
    }
    seen: set[tuple[str, str]] = set()
    for artifact in artifacts:
        key = _surface_key_for_artifact(artifact)
        if key is None or key in seen or key not in surface_by_key:
            continue
        seen.add(key)
    return [
        surface
        for target in targets
        for surface in RUNTIME_SURFACES.get(target, [])
        if (surface["target"], surface["path"]) in seen
    ]


def _activation_gates(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    destinations_by_target: dict[str, set[str]] = {}
    for artifact in artifacts:
        destinations_by_target.setdefault(artifact["target"], set()).add(artifact["destination"])

    gates: list[dict[str, Any]] = []
    if ".claude/settings.json" in destinations_by_target.get("claude", set()):
        gates.extend(ACTIVATION_GATES["claude"])
    if ".codex/hooks.json" in destinations_by_target.get("codex", set()):
        gates.extend(ACTIVATION_GATES["codex"])
    if ".agents/hooks.json" in destinations_by_target.get("antigravity", set()):
        gates.extend(ACTIVATION_GATES["antigravity"])
    return gates


def _artifact_target_and_destination(source_path: Path) -> tuple[str, str]:
    rel_source = source_path.relative_to(REPO_ROOT)
    parts = rel_source.parts
    if len(parts) < 3 or parts[0] != "dist":
        raise ValueError(f"Adapter output must live under dist/<target>: {rel_source}")
    target = parts[1]
    destination = Path(*parts[2:]).as_posix()
    return target, destination


def _artifacts(component_ids: list[str]) -> list[dict[str, Any]]:
    _materialize_adapter_outputs(component_ids)
    entries = _selected_registry_entries(component_ids)
    artifacts: list[dict[str, Any]] = []
    seen_destinations: dict[tuple[str, str], bool] = {}
    artifact_by_destination: dict[tuple[str, str], dict[str, Any]] = {}
    for component_id, entry in entries.items():
        merge_by_destination: dict[str, dict[str, Any]] = {}
        if entry and entry.get("path"):
            manifest_path = REPO_ROOT / entry["path"]
            if manifest_path.is_file():
                manifest = _load_yaml(manifest_path)
                merge_by_destination = {
                    item["destination"]: item
                    for item in manifest.get("merge_artifacts") or []
                    if isinstance(item, dict) and isinstance(item.get("destination"), str)
                }
        for output_path, _content, append in _render_component(component_id, entry or {}):
            target, destination = _artifact_target_and_destination(output_path)
            destination_key = (target, destination)
            if destination_key in seen_destinations:
                if append and seen_destinations[destination_key]:
                    artifact = artifact_by_destination[destination_key]
                    artifact.setdefault("component_ids", [artifact["component_id"]])
                    if component_id not in artifact["component_ids"]:
                        artifact["component_ids"].append(component_id)
                    continue
                raise ValueError(f"duplicate non-append artifact destination: {destination}")
            seen_destinations[destination_key] = append
            artifact = {
                "component_id": component_id,
                "component_ids": [component_id],
                "target": target,
                "source": output_path.relative_to(REPO_ROOT).as_posix(),
                "destination": destination,
            }
            if destination in merge_by_destination:
                merge_config = merge_by_destination[destination]
                artifact["merge_strategy"] = merge_config.get("strategy")
                artifact["begin_marker"] = merge_config.get("begin_marker")
                artifact["end_marker"] = merge_config.get("end_marker")
            artifacts.append(artifact)
            artifact_by_destination[destination_key] = artifact
    return artifacts


def _materialize_adapter_outputs(component_ids: list[str]) -> None:
    expected_outputs = _collect_adapter_outputs(component_ids)
    for output_path, expected in expected_outputs.items():
        content = (
            _combined_append_content(output_path, expected.get("chunks", [expected["content"]]))
            if expected["append"]
            else expected["content"]
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")


def _collect_adapter_outputs(component_ids: list[str]) -> dict[Path, dict[str, Any]]:
    entries = _selected_registry_entries(component_ids)
    expected_outputs: dict[Path, dict[str, Any]] = {}

    for component_id, entry in entries.items():
        for output_path, content, append in _render_component(component_id, entry or {}):
            if append:
                existing = expected_outputs.get(output_path)
                if existing is None:
                    expected_outputs[output_path] = {
                        "content": content,
                        "append": True,
                        "chunks": [content],
                    }
                else:
                    if not existing["append"]:
                        raise ValueError(f"Duplicate adapter output: {output_path}")
                    existing.setdefault("chunks", []).append(content)
                    existing["content"] = _combined_append_content(
                        output_path,
                        existing["chunks"],
                    )
                    existing["append"] = existing["append"] and append
                continue
            if output_path in expected_outputs:
                raise ValueError(f"Duplicate adapter output: {output_path}")
            expected_outputs[output_path] = {"content": content, "append": False}
    return expected_outputs


def _remap_artifact_source(artifact: dict[str, Any], destination: str) -> dict[str, Any]:
    target = artifact["target"]
    source = REPO_ROOT / artifact["source"]
    remapped_source = REPO_ROOT / "dist" / target / destination
    if source != remapped_source:
        remapped_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, remapped_source)
    new_artifact = artifact.copy()
    new_artifact["source"] = remapped_source.relative_to(REPO_ROOT).as_posix()
    new_artifact["destination"] = destination
    return new_artifact


def build_plan(profile: str, *, scope: str, mode: str) -> dict[str, Any]:
    name, profile_data = _load_profile(profile)
    targets = profile_data.get("targets") or []
    components = profile_data.get("components") or []
    install_policy = profile_data.get("install_policy") or {}
    allowed_scopes = install_policy.get("allowed_scopes") or []
    if not isinstance(targets, list):
        raise ValueError(f"profiles/{name}.yml: targets must be a list")
    if not isinstance(components, list):
        raise ValueError(f"profiles/{name}.yml: components must be a list")
    if not isinstance(allowed_scopes, list) or not all(
        isinstance(allowed_scope, str) for allowed_scope in allowed_scopes
    ):
        raise ValueError(f"profiles/{name}.yml: install_policy.allowed_scopes must be a list")
    if scope not in allowed_scopes:
        raise ValueError(f"profiles/{name}.yml: scope is not allowed: {scope}")

    artifacts = _artifacts(components)

    # Filter and remap artifacts by scope
    filtered_artifacts = []
    for artifact in artifacts:
        dest = artifact["destination"]
        target = artifact["target"]

        if scope == "user":
            # Remap workspace paths to global paths for Codex and Antigravity
            if target == "codex" and dest.startswith(".agents/skills/"):
                filtered_artifacts.append(
                    _remap_artifact_source(
                        artifact,
                        dest.replace(".agents/skills/", ".codex/skills/"),
                    )
                )
                continue
            if target == "antigravity" and dest.startswith(".agents/skills/"):
                filtered_artifacts.append(
                    _remap_artifact_source(
                        artifact,
                        dest.replace(".agents/skills/", ".gemini/config/skills/"),
                    )
                )
                continue
            # Drop purely workspace paths
            if dest.startswith(".agents/") or dest.startswith(".claude/"):
                continue
            filtered_artifacts.append(artifact)
        elif scope in ("workspace", "project"):
            if dest.startswith(".codex/skills/") or dest.startswith(".gemini/"):
                continue
            filtered_artifacts.append(artifact)
        else:
            filtered_artifacts.append(artifact)
            
    artifacts = filtered_artifacts

    return {
        "plan_id": f"harnesskit.install-plan.{name}.{scope}",
        "profile_id": profile_data["profile_id"],
        "scope": scope,
        "mode": mode,
        "targets": targets,
        "components": components,
        "artifacts": artifacts,
        "runtime_surfaces": _runtime_surfaces(targets, artifacts),
        "activation_gates": _activation_gates(artifacts),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a HarnessKit install plan.")
    parser.add_argument("--profile", required=True, help="Profile name or id")
    parser.add_argument(
        "--scope",
        choices=["project", "workspace", "user"],
        default="project",
        help="Install scope",
    )
    parser.add_argument(
        "--mode",
        choices=["dry-run", "apply", "verify"],
        default="dry-run",
        help="Install plan mode",
    )
    parser.add_argument("--format", choices=["json", "yaml"], default="yaml")
    args = parser.parse_args(argv)

    try:
        plan = build_plan(args.profile, scope=args.scope, mode=args.mode)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
