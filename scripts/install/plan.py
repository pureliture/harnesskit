from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.adapters.api import (  # noqa: E402
    CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT,
    CODEX_USER_HOOK_OUTPUT,
    build_adapter_outputs,
    combined_append_content,
    project_runtime_roots,
    render_component,
    selected_registry_entries,
)
from scripts.install.common import (  # noqa: E402
    HERMES_EXTERNAL_CONFIG,
    HERMES_EXTERNAL_HOOK_COMMAND,
    HERMES_EXTERNAL_HOOK_EVENT,
    HERMES_EXTERNAL_CONFIG_SOURCE,
    HERMES_EXTERNAL_HOOK_ROOT,
    HERMES_EXTERNAL_HOOKS_SOURCE,
    HERMES_EXTERNAL_PACKAGE_ROOT,
    HERMES_EXTERNAL_PACKAGE_SOURCE,
    HERMES_EXTERNAL_SKILLS_SOURCE,
    TOML_AGENTS_MERGE_DESTINATION_KEYS,
    retired_codex_agent_registrations,
    validate_final_destination_fan_in,
)
from scripts.install.migrations import (  # noqa: E402
    retired_codex_agent_registrations_for_profile,
)
from scripts.profiles.selection import validate_profile_selection  # noqa: E402

PROFILES_DIR = REPO_ROOT / "profiles"

PROFILE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

COMPOSITE_KIND = "composite"
COMPOSITE_ID_PREFIX = "harnesskit.composite."


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


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
        },
        {
            "target": "project",
            "path": ".acli",
            "source": "dist/project/.acli",
        },
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
    "antigravity-cli": [
        {
            "target": "antigravity-cli",
            "path": ".agents/skills",
            "source": "dist/antigravity-cli/.agents/skills",
        },
        {
            "target": "antigravity-cli",
            "path": ".gemini/config/skills",
            "source": "dist/antigravity-cli/.gemini/config/skills",
        },
    ],
    "hermes": [
        {
            "target": "hermes",
            "path": ".hermes/config.yaml",
            "source": "dist/hermes/config.yaml",
        },
        {
            "target": "hermes",
            "path": ".hermes/harnesskit/hooks",
            "source": "dist/hermes/hooks",
        },
    ],
}

ACTIVATION_GATES = {
    "claude": [
        {
            "id": "claude-prompt-submit-hook-review",
            "target": "claude",
            "reason": (
                "Claude prompt-submit hook changes user runtime behavior and should be "
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
                "Codex user-level hooks require trust state before runtime "
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
    "hermes": [
        {
            "id": "hermes-shell-hook-consent",
            "target": "hermes",
            "reason": (
                "Hermes shell hooks execute local commands and require exact "
                "event/command allowlist consent before runtime use."
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
        for root in project_runtime_roots():
            if destination.startswith(f"{root}/"):
                return ("project", root)
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
    if target == "antigravity-cli":
        if destination.startswith(".agents/skills/"):
            return ("antigravity-cli", ".agents/skills")
        if destination.startswith(".gemini/config/skills/"):
            return ("antigravity-cli", ".gemini/config/skills")
    if target == "hermes":
        if destination == ".hermes/config.yaml":
            return ("hermes", ".hermes/config.yaml")
        if destination.startswith(".hermes/harnesskit/hooks/"):
            return ("hermes", ".hermes/harnesskit/hooks")
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
        if artifact.get("source") == CODEX_USER_HOOK_OUTPUT:
            surface_by_key[key] = {**surface_by_key[key], "source": artifact["source"]}
        seen.add(key)
    return [
        surface_by_key[(surface["target"], surface["path"])]
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
    if ".hermes/config.yaml" in destinations_by_target.get("hermes", set()):
        gates.extend(ACTIVATION_GATES["hermes"])
    return gates


def _artifact_target_and_destination(source_path: Path) -> tuple[str, str]:
    rel_source = source_path.relative_to(REPO_ROOT)
    parts = rel_source.parts
    if len(parts) >= 2 and parts[0] in project_runtime_roots():
        return "project", rel_source.as_posix()
    if len(parts) < 3 or parts[0] != "dist":
        raise ValueError(f"Adapter output must live under dist/<target>: {rel_source}")
    target = parts[1]
    destination = Path(*parts[2:]).as_posix()
    if target == "hermes":
        if destination == "config.yaml":
            return target, ".hermes/config.yaml"
        if destination.startswith("hooks/"):
            return target, f".hermes/harnesskit/{destination}"
    return target, destination


def _artifact_source_for_output(output_path: Path, target: str, destination: str) -> Path:
    rel_output = output_path.relative_to(REPO_ROOT)
    if rel_output.parts[:2] == ("dist", target):
        return output_path
    if target == "project":
        return REPO_ROOT / "dist" / target / destination
    return output_path


def _unpack_rendered_output(item: tuple[Any, ...]) -> tuple[Path, str, bool, int | None]:
    if len(item) == 3:
        output_path, content, append = item
        return output_path, content, append, None
    output_path, content, append, mode = item
    return output_path, content, append, mode


def _toml_agents_merge_key_for(target: str, destination: str) -> str | None:
    """Return the toml-agents-merge key owned by ``(target, destination)``.

    The codex `.codex/config.toml` registration artifact installs via the
    preserving toml-agents-merge so a real user config is never clobbered.
    Ownership of which `(target, destination)` carries the strategy (and which
    merge key) lives in the contract gate's
    ``TOML_AGENTS_MERGE_DESTINATION_KEYS`` map, reused here so the generated
    plan and ``validate_plan_contract`` can never drift apart.
    """
    return TOML_AGENTS_MERGE_DESTINATION_KEYS.get(
        (target, PurePosixPath(destination))
    )


def _artifacts(
    component_ids: list[str],
    *,
    materialize_adapter_outputs: bool = True,
    include_internal_hashes: bool = False,
    include_internal_content: bool = False,
) -> list[dict[str, Any]]:
    if materialize_adapter_outputs:
        _materialize_adapter_outputs(component_ids)
    entries = selected_registry_entries(component_ids)
    artifacts: list[dict[str, Any]] = []
    seen_destinations: dict[tuple[str, str], bool] = {}
    artifact_by_destination: dict[tuple[str, str], dict[str, Any]] = {}
    for component_id, entry in entries.items():
        manifest: dict[str, Any] = {}
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
        for item in render_component(component_id, entry or {}):
            output_path, content, append, _mode = _unpack_rendered_output(item)
            target, destination = _artifact_target_and_destination(output_path)
            source_path = _artifact_source_for_output(output_path, target, destination)
            source = source_path.relative_to(REPO_ROOT).as_posix()
            destination_key = (target, destination)
            if destination_key in seen_destinations:
                if append and seen_destinations[destination_key]:
                    artifact = artifact_by_destination[destination_key]
                    artifact.setdefault("component_ids", [artifact["component_id"]])
                    if component_id not in artifact["component_ids"]:
                        artifact["component_ids"].append(component_id)
                    if include_internal_hashes or include_internal_content:
                        chunks = artifact.setdefault("_content_chunks", [])
                        chunks.append(content)
                        exact_text = combined_append_content(output_path, chunks)
                        if include_internal_content:
                            artifact["_exact_text"] = exact_text
                        if include_internal_hashes:
                            artifact["_content_sha256"] = hashlib.sha256(
                                exact_text.encode("utf-8")
                            ).hexdigest()
                    continue
                artifact = artifact_by_destination[destination_key]
                if artifact.get("source") == source:
                    continue
                raise ValueError(f"duplicate non-append artifact destination: {destination}")
            seen_destinations[destination_key] = append
            artifact = {
                "component_id": component_id,
                "component_ids": [component_id],
                "target": target,
                "source": source,
                "destination": destination,
            }
            if include_internal_hashes or include_internal_content:
                artifact["_content_chunks"] = [content]
            if include_internal_content:
                artifact["_exact_text"] = content
            if include_internal_hashes:
                artifact["_content_sha256"] = hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest()
            if destination in merge_by_destination:
                merge_config = merge_by_destination[destination]
                artifact["merge_strategy"] = merge_config.get("strategy")
                if merge_config.get("ownership"):
                    artifact["ownership"] = merge_config["ownership"]
                if artifact["merge_strategy"] == "managed-block":
                    artifact["begin_marker"] = merge_config.get("begin_marker")
                    artifact["end_marker"] = merge_config.get("end_marker")
                if artifact["merge_strategy"] == "json-deep-merge":
                    artifact["json_merge_key"] = merge_config.get("json_merge_key")
                if artifact["merge_strategy"] == "toml-agents-merge":
                    artifact["toml_merge_key"] = merge_config.get("toml_merge_key")
            else:
                toml_merge_key = _toml_agents_merge_key_for(target, destination)
                if toml_merge_key is not None:
                    artifact["merge_strategy"] = "toml-agents-merge"
                    artifact["toml_merge_key"] = toml_merge_key
            if (target == "project" and destination == "AGENTS.md"
                    and manifest.get("ownership") == {"type": "path", "merge_policy": "full-path-overwrite"}):
                artifact["ownership"] = manifest["ownership"]
            artifacts.append(artifact)
            artifact_by_destination[destination_key] = artifact
    return artifacts


def _qualified_adapter_descriptor(target: str) -> dict[str, str]:
    descriptor_path = REPO_ROOT / "adapters" / target / "adapter.yml"
    descriptor = _load_yaml(descriptor_path)
    adapter_id = descriptor.get("adapter_id")
    adapter_version = descriptor.get("version")
    descriptor_target = descriptor.get("target")
    if (
        not isinstance(adapter_id, str)
        or not adapter_id
        or not isinstance(adapter_version, str)
        or not adapter_version
        or descriptor_target != target
    ):
        raise ValueError(f"invalid qualified adapter descriptor: {target}")
    return {
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "target": target,
        "descriptor_sha256": hashlib.sha256(descriptor_path.read_bytes()).hexdigest(),
    }


def _artifact_generator_descriptor(target: str) -> dict[str, str]:
    try:
        return _qualified_adapter_descriptor(target)
    except ValueError:
        if target != "project":
            raise
        descriptor_path = REPO_ROOT / "adapters" / target / "adapter.yml"
        descriptor_sha256 = hashlib.sha256(descriptor_path.read_bytes()).hexdigest()
        return {
            "adapter_id": "project",
            "adapter_version": descriptor_sha256,
            "target": target,
            "descriptor_sha256": descriptor_sha256,
        }


def build_artifact_projection(component_ids: list[str]) -> dict[str, Any]:
    """Generate exact canonical adapter artifacts without profile or target writes."""
    requested_components = sorted(_dedupe_preserving_order(component_ids))
    selected_registry_entries(requested_components)
    components = sorted(_expand_composite_ids(requested_components))
    selected_registry_entries(components)
    records: list[dict[str, Any]] = []
    descriptors: dict[str, dict[str, str]] = {}
    issues: list[dict[str, str]] = []
    for scope in ("project", "user"):
        scoped_components = _profile_components_for_scope(components, scope)
        artifacts = _artifacts(
            scoped_components,
            materialize_adapter_outputs=False,
            include_internal_hashes=True,
            include_internal_content=True,
        )
        raw_codex_hook = next(
            (
                artifact
                for artifact in artifacts
                if artifact.get("source") == CODEX_USER_HOOK_OUTPUT
            ),
            None,
        )
        artifacts = _scope_filtered_artifacts(
            artifacts,
            scope=scope,
            materialize_adapter_outputs=False,
        )
        codex_user_hook = (
            _codex_user_hook_registration_artifact(scoped_components)
            if scope == "user"
            else None
        )
        if codex_user_hook is not None and raw_codex_hook is not None:
            codex_user_hook["_exact_text"] = raw_codex_hook.get("_exact_text")
            codex_user_hook["_content_sha256"] = raw_codex_hook.get(
                "_content_sha256"
            )
            artifacts.append(codex_user_hook)

        validate_final_destination_fan_in(artifacts)
        for artifact in artifacts:
            exact_text = artifact.get("_exact_text")
            content_sha256 = artifact.get("_content_sha256")
            target = artifact.get("target")
            if (
                not isinstance(exact_text, str)
                or not isinstance(content_sha256, str)
                or not isinstance(target, str)
            ):
                issues.append(
                    {
                        "code": "artifact_content_unavailable",
                        "safe_reason": "Canonical adapter output was unavailable",
                    }
                )
                continue
            try:
                descriptor = descriptors.setdefault(
                    target, _artifact_generator_descriptor(target)
                )
            except (FileNotFoundError, ValueError, KeyError):
                issues.append(
                    {
                        "code": "adapter_descriptor_unavailable",
                        "safe_reason": f"Qualified adapter descriptor was unavailable: {target}",
                    }
                )
                continue
            component_set = sorted(
                set(artifact.get("component_ids") or [artifact["component_id"]])
            )
            for component_id in component_set:
                record = {
                    "component_id": component_id,
                    "adapter_id": descriptor["adapter_id"],
                    "adapter_version": descriptor["adapter_version"],
                    "target": target,
                    "scope": scope,
                    "destination": artifact["destination"],
                    "source": artifact["source"],
                    "merge_strategy": artifact.get("merge_strategy"),
                    "config_entry_locator": artifact.get("json_merge_key")
                    or artifact.get("toml_merge_key"),
                    "content_sha256": content_sha256,
                    "exact_text": exact_text,
                }
                records.append(record)

    records.sort(
        key=lambda record: (
            record["component_id"],
            record["adapter_id"],
            record["target"],
            record["scope"],
            record["destination"],
            record.get("config_entry_locator") or "",
            record["content_sha256"],
        )
    )
    descriptor_rows = sorted(
        descriptors.values(), key=lambda descriptor: descriptor["adapter_id"]
    )
    adapter_set_revision = _canonical_json_sha256(descriptor_rows)
    return {
        "generator_version": "canonical-artifact-generator-v1",
        "adapter_set_revision": adapter_set_revision,
        "records": records,
        "issues": [
            {"code": code, "safe_reason": safe_reason}
            for code, safe_reason in sorted(
                {(issue["code"], issue["safe_reason"]) for issue in issues}
            )
        ],
    }


def _strip_internal_artifact_fields(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in artifact.items() if not key.startswith("_")}
        for artifact in artifacts
    ]


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _materialization_status(mode: str) -> dict[str, Any] | None:
    if mode == "dry-run":
        return None
    return {
        "adapter_outputs": "materialized",
        "generated_source_root": "dist",
        "read_only": False,
    }


def _component_scopes(component_id: str, entry: dict[str, Any]) -> list[str]:
    rel_manifest = entry.get("path")
    if not isinstance(rel_manifest, str) or not rel_manifest:
        return ["project"]

    manifest_path = REPO_ROOT / rel_manifest
    if not manifest_path.is_file():
        if entry.get("planned") is True:
            return ["project"]
        raise FileNotFoundError(f"Missing manifest for {component_id}: {rel_manifest}")

    manifest = _load_yaml(manifest_path)
    scopes = manifest.get("scopes") or ["project", "user"]
    if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
        raise ValueError(f"{rel_manifest}: scopes must be a list of strings")
    return scopes


def _validate_components_allowed_for_scope(component_ids: list[str], scope: str) -> None:
    entries = selected_registry_entries(component_ids)
    for component_id, entry in entries.items():
        scopes = _component_scopes(component_id, entry or {})
        if scope not in scopes:
            raise ValueError(f"component is not allowed for scope {scope}: {component_id}")


def _is_composite_id(candidate: str) -> bool:
    return candidate.startswith(COMPOSITE_ID_PREFIX)


def _is_composite_entry(entry: dict[str, Any] | None) -> bool:
    return isinstance(entry, dict) and entry.get("kind") == COMPOSITE_KIND


def _composite_members(composite_id: str) -> list[str]:
    """Resolve a composite id to its ordered member component ids.

    A composite is registered in components/registry.yml like any other entry,
    but with kind: composite and a composite.yml manifest carrying `members`.
    This expands the composite to that member list (members are plain
    component ids, never nested composites).
    """
    entry = selected_registry_entries([composite_id])[composite_id]
    if not _is_composite_entry(entry):
        raise ValueError(f"not a composite: {composite_id}")

    rel_manifest = entry.get("path")
    if not isinstance(rel_manifest, str) or not rel_manifest:
        raise ValueError(f"{composite_id}: registry path is required")

    manifest_path = REPO_ROOT / rel_manifest
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing composite manifest for {composite_id}: {rel_manifest}")

    manifest = _load_yaml(manifest_path)
    members = manifest.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError(f"{rel_manifest}: members must be a non-empty list")
    resolved: list[str] = []
    for member_id in members:
        if not isinstance(member_id, str) or not member_id:
            raise ValueError(f"{rel_manifest}: members must be non-empty strings")
        if _is_composite_id(member_id):
            # Multi-combination / nested-composite resolution is deferred (M9);
            # composite members are plain component ids only.
            raise ValueError(
                f"{rel_manifest}: member must be a component, not a composite: {member_id}"
            )
        resolved.append(member_id)
    return _dedupe_preserving_order(resolved)


def _expand_composite_ids(component_ids: list[str]) -> list[str]:
    """Expand any composite ids in a flat id list to their member ids in place.

    Used on the --profile path: a profile MAY list composite ids alongside
    plain component ids; those are expanded to members before scope handling.
    Plain component ids pass through unchanged. Order is preserved.
    """
    expanded: list[str] = []
    for candidate in component_ids:
        if _is_composite_id(candidate):
            expanded.extend(_composite_members(candidate))
        else:
            expanded.append(candidate)
    return _dedupe_preserving_order(expanded)


def _composite_shared_scope(member_ids: list[str]) -> list[str]:
    """Shared install scope for a composite = INTERSECTION of member scopes (M9).

    Multi-combination resolution is deferred; only the plain intersection is
    computed here. Order follows the first member's scope ordering.
    """
    entries = selected_registry_entries(member_ids)
    scope_sets = [
        set(_component_scopes(member_id, entry or {}))
        for member_id, entry in entries.items()
    ]
    if not scope_sets:
        return []
    shared = set.intersection(*scope_sets)
    first_member = next(iter(entries))
    first_scopes = _component_scopes(first_member, entries[first_member] or {})
    return [scope for scope in first_scopes if scope in shared]


def _standalone_installable(component_id: str, entry: dict[str, Any]) -> bool:
    """Read the standalone_installable atom from a component manifest.

    Absent => True (the schema default); only an explicit False blocks
    standalone install. Composites and manifest-less entries are not subject
    to this atom and resolve to True here.
    """
    rel_manifest = entry.get("path")
    if not isinstance(rel_manifest, str) or not rel_manifest:
        return True
    manifest_path = REPO_ROOT / rel_manifest
    if not manifest_path.is_file():
        return True
    manifest = _load_yaml(manifest_path)
    value = manifest.get("standalone_installable")
    if value is None:
        return True
    if not isinstance(value, bool):
        raise ValueError(f"{rel_manifest}: standalone_installable must be a boolean")
    return value


def _validate_member_block_for_standalone(component_ids: list[str]) -> None:
    """Refuse standalone --component install of a member-blocked atom.

    ISOLATED, one-way safety constraint (M8): a component manifest with
    standalone_installable: false may only be installed via a composite that
    includes it (or a profile). This validator runs ONLY on the standalone
    --component path and is deliberately NOT grafted into
    _validate_components_allowed_for_scope, which would break profile install.
    """
    entries = selected_registry_entries(component_ids)
    for component_id, entry in entries.items():
        if _is_composite_entry(entry):
            continue
        if not _standalone_installable(component_id, entry or {}):
            raise ValueError(
                "component is not standalone-installable; "
                f"install it via a composite or profile: {component_id}"
            )


def _profile_components_for_scope(component_ids: list[str], scope: str) -> list[str]:
    entries = selected_registry_entries(component_ids)
    return [
        component_id
        for component_id, entry in entries.items()
        if scope in _component_scopes(component_id, entry or {})
    ]


def _codex_user_hook_registration_artifact(component_ids: list[str]) -> dict[str, Any] | None:
    if CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT not in component_ids:
        return None
    return {
        "component_id": CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT,
        "component_ids": [CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT],
        "target": "codex",
        "source": CODEX_USER_HOOK_OUTPUT,
        "destination": ".codex/hooks.json",
        "merge_strategy": "json-deep-merge",
        "json_merge_key": "codex-hooks",
        "scope": "user",
        "ownership": {
            "type": "managed-hook-group",
            "managed_group_id": "optimal-response.codex-user-prompt-submit",
            "merge_policy": "json-deep-merge",
            "preserve_foreign_entries": True,
        },
        "runtime_contract": {
            "surface": "codex-user-hooks",
            "event": "UserPromptSubmit",
            "path": "${CODEX_HOME:-$HOME/.codex}/hooks.json",
        },
        "evidence_status": {
            "status": "build_only",
            "reason": "OR-02 statically builds the compact marker contract; the installed Codex hook has not observed this changed semantic revision.",
        },
        "historical_predecessor_evidence": {
            "status_at_observation": "runtime_supported",
            "latest_evidence_dir": "docs/runtime-evidence/codex-optimal-response/20260606T040248Z",
            "boundary": "The predecessor probe proved registration, hook execution, and STOP-off state, not the OR-02 compact marker or on-demand reference.",
        },
    }


def _materialize_adapter_outputs(component_ids: list[str]) -> None:
    expected_outputs = _collect_adapter_outputs(component_ids)
    for output_path, expected in expected_outputs.items():
        content = (
            combined_append_content(output_path, expected.get("chunks", [expected["content"]]))
            if expected["append"]
            else expected["content"]
        )
        mode = expected.get("mode")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        if mode is not None:
            output_path.chmod(mode)


def _collect_adapter_outputs(component_ids: list[str]) -> dict[Path, dict[str, Any]]:
    entries = selected_registry_entries(component_ids)
    expected_outputs: dict[Path, dict[str, Any]] = {}

    for component_id, entry in entries.items():
        for item in render_component(component_id, entry or {}):
            output_path, content, append, mode = _unpack_rendered_output(item)
            if append:
                existing = expected_outputs.get(output_path)
                if existing is None:
                    expected_outputs[output_path] = {
                        "content": content,
                        "append": True,
                        "chunks": [content],
                        "mode": None,
                    }
                else:
                    if not existing["append"]:
                        raise ValueError(f"Duplicate adapter output: {output_path}")
                    existing.setdefault("chunks", []).append(content)
                    existing["content"] = combined_append_content(
                        output_path,
                        existing["chunks"],
                    )
                    existing["append"] = existing["append"] and append
                continue
            if output_path in expected_outputs:
                raise ValueError(f"Duplicate adapter output: {output_path}")
            expected_outputs[output_path] = {"content": content, "append": False, "mode": mode}
    return expected_outputs


def _remap_artifact_source(
    artifact: dict[str, Any],
    destination: str,
    *,
    materialize_adapter_outputs: bool = False,
) -> dict[str, Any]:
    target = artifact["target"]
    source = REPO_ROOT / artifact["source"]
    remapped_source = REPO_ROOT / "dist" / target / destination
    if materialize_adapter_outputs and source != remapped_source:
        remapped_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, remapped_source)
    new_artifact = artifact.copy()
    new_artifact["source"] = remapped_source.relative_to(REPO_ROOT).as_posix()
    new_artifact["destination"] = destination
    return new_artifact


def _scope_filtered_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    scope: str,
    materialize_adapter_outputs: bool,
) -> list[dict[str, Any]]:
    """Filter and remap artifacts by install scope.

    Shared by every selection mode (profile, composite, component) so the
    project/user scope routing stays identical across them.
    """
    filtered_artifacts: list[dict[str, Any]] = []
    for artifact in artifacts:
        dest = artifact["destination"]
        target = artifact["target"]

        if artifact.get("source") == CODEX_USER_HOOK_OUTPUT:
            continue

        # The agy CLI setup intentionally exposes the skill surface only. Its
        # historical static adapter may still render hook/agent files for
        # compatibility, but install plans must never materialize them.
        if target == "antigravity-cli" and (
            dest.startswith(".agents/agents/") or dest == ".agents/hooks.json"
        ):
            continue

        if scope == "user":
            if target == "codex" and dest == ".codex/hooks.json":
                continue
            # Remap workspace paths to global paths for Codex and Antigravity
            if target == "codex" and dest.startswith(".agents/skills/"):
                filtered_artifacts.append(
                    _remap_artifact_source(
                        artifact,
                        dest.replace(".agents/skills/", ".codex/skills/"),
                        materialize_adapter_outputs=materialize_adapter_outputs,
                    )
                )
                continue
            if target == "antigravity" and dest.startswith(".agents/skills/"):
                filtered_artifacts.append(
                    _remap_artifact_source(
                        artifact,
                        dest.replace(".agents/skills/", ".gemini/config/skills/"),
                        materialize_adapter_outputs=materialize_adapter_outputs,
                    )
                )
                continue
            if target == "antigravity-cli" and dest.startswith(".agents/skills/"):
                filtered_artifacts.append(
                    _remap_artifact_source(
                        artifact,
                        dest.replace(".agents/skills/", ".gemini/config/skills/"),
                        materialize_adapter_outputs=materialize_adapter_outputs,
                    )
                )
                continue
            # Drop purely workspace paths (codex/antigravity .agents/skills/ already
            # remapped above). Claude uses the same relative path at user and project
            # scope (~/.claude mirrors project .claude), so its .claude/ destinations
            # need no remap and fall through to be kept at user scope.
            if dest.startswith(".agents/"):
                continue
            filtered_artifacts.append(artifact)
        elif scope == "project":
            if dest.startswith(".codex/skills/") or dest.startswith(".gemini/"):
                continue
            filtered_artifacts.append(artifact)
        else:
            filtered_artifacts.append(artifact)

    return filtered_artifacts


def _selection_plan(
    component_ids: list[str],
    *,
    plan_id: str,
    scope: str,
    mode: str,
    targets: list[str] | None = None,
) -> dict[str, Any]:
    """Build a non-profile install plan (component or composite selection).

    Shares artifact rendering, scope filtering, and the codex user-hook
    registration tail with the profile path, but emits a selection-shaped
    plan (no profile_id) keyed by the supplied plan_id.
    """
    components = _dedupe_preserving_order(component_ids)
    _validate_components_allowed_for_scope(components, scope)
    materialize_adapter_outputs = mode != "dry-run"
    artifacts = _artifacts(
        components,
        materialize_adapter_outputs=materialize_adapter_outputs,
        include_internal_hashes=True,
    )
    artifacts = _scope_filtered_artifacts(
        artifacts,
        scope=scope,
        materialize_adapter_outputs=materialize_adapter_outputs,
    )

    codex_user_hook_artifact = (
        _codex_user_hook_registration_artifact(components) if scope == "user" else None
    )
    if codex_user_hook_artifact is not None and not any(
        artifact["target"] == "codex"
        and artifact["destination"] == ".codex/hooks.json"
        and CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT
        in artifact.get("component_ids", [artifact["component_id"]])
        for artifact in artifacts
    ):
        artifacts.append(codex_user_hook_artifact)

    validate_final_destination_fan_in(artifacts)
    artifacts = _strip_internal_artifact_fields(artifacts)
    selected_targets = list(targets or _artifact_targets(artifacts))

    plan = {
        "plan_id": plan_id,
        "scope": scope,
        "mode": mode,
        "targets": selected_targets,
        "components": components,
        "artifacts": artifacts,
        "runtime_surfaces": _runtime_surfaces(selected_targets, artifacts),
        "activation_gates": _activation_gates(artifacts),
    }
    materialization = _materialization_status(mode)
    if materialization is not None:
        plan["materialization"] = materialization
    return plan


def _artifact_targets(artifacts: list[dict[str, Any]]) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    for artifact in artifacts:
        target = artifact["target"]
        if target in seen:
            continue
        seen.add(target)
        targets.append(target)
    return targets


def build_component_plan(
    component_id: str,
    *,
    scope: str,
    mode: str,
    targets: list[str] | None = None,
) -> dict[str, Any]:
    """Plan a single standalone --component install.

    This is the ONLY path that runs the isolated member-block validator: a
    component marked standalone_installable: false may not be installed here
    and must go through --composite or --profile instead.
    """
    _validate_member_block_for_standalone([component_id])
    return _selection_plan(
        [component_id],
        plan_id=f"harnesskit.install-plan.component.{scope}",
        scope=scope,
        mode=mode,
        targets=targets,
    )


def build_composite_plan(
    composite: str,
    *,
    scope: str,
    mode: str,
    targets: list[str] | None = None,
) -> dict[str, Any]:
    """Plan a --composite install: expand to members, install the group.

    Shared scope is the INTERSECTION of member scopes (M9); the requested
    scope must lie within that intersection. The member-block validator is
    NOT invoked here — installing a standalone_installable: false member via
    its composite is exactly the permitted path.
    """
    name = composite.removeprefix(COMPOSITE_ID_PREFIX)
    members = _composite_members(composite)
    shared_scope = _composite_shared_scope(members)
    if scope not in shared_scope:
        raise ValueError(
            f"composite {composite}: scope is not in member-shared scope: {scope}"
        )
    return _selection_plan(
        members,
        plan_id=f"harnesskit.install-plan.composite.{name}.{scope}",
        scope=scope,
        mode=mode,
        targets=targets,
    )


def build_plan(
    profile: str,
    *,
    scope: str,
    mode: str,
    extra_components: list[str] | None = None,
) -> dict[str, Any]:
    name, profile_data = _load_profile(profile)
    targets = profile_data.get("targets") or []
    profile_components = profile_data.get("components") or []
    install_policy = profile_data.get("install_policy") or {}
    allowed_scopes = install_policy.get("allowed_scopes") or []
    scope_components = install_policy.get("scope_components") or {}
    if not isinstance(targets, list):
        raise ValueError(f"profiles/{name}.yml: targets must be a list")
    if not isinstance(profile_components, list):
        raise ValueError(f"profiles/{name}.yml: components must be a list")
    if not isinstance(allowed_scopes, list) or not all(
        isinstance(allowed_scope, str) for allowed_scope in allowed_scopes
    ):
        raise ValueError(f"profiles/{name}.yml: install_policy.allowed_scopes must be a list")
    if not isinstance(scope_components, dict):
        raise ValueError(
            f"profiles/{name}.yml: install_policy.scope_components must be a mapping"
        )
    if scope not in allowed_scopes:
        raise ValueError(f"profiles/{name}.yml: scope is not allowed: {scope}")

    registry = _load_yaml(REPO_ROOT / "components" / "registry.yml")
    registry_components = registry.get("components")
    if not isinstance(registry_components, dict):
        raise ValueError("components/registry.yml: components must be a mapping")
    validate_profile_selection(
        profile_data,
        registry_components,
        load_manifest=lambda rel_path: _load_yaml(REPO_ROOT / rel_path),
    )

    # Scope-conditional component selection: a profile can declare components that
    # are selected only at a specific scope (e.g. user-only optimal-response). These
    # are resolved here, before _artifacts, so the emitted plan stays schema-clean.
    selected_scope_components = scope_components.get(scope) or []
    if not isinstance(selected_scope_components, list) or not all(
        isinstance(item, str) for item in selected_scope_components
    ):
        raise ValueError(
            f"profiles/{name}.yml: install_policy.scope_components.{scope} must be a list"
        )
    selected_retired_registrations = retired_codex_agent_registrations_for_profile(
        REPO_ROOT,
        profile_data["profile_id"],
        scope,
    )

    # A profile MAY list composite ids alongside plain component ids (M7);
    # expand them to their member component ids before scope selection so the
    # rest of the profile path is unchanged for non-composite profiles.
    expanded_profile_components = _expand_composite_ids(profile_components)
    expanded_scope_components = _expand_composite_ids(selected_scope_components)

    scoped_profile_components = _profile_components_for_scope(
        expanded_profile_components, scope
    )
    components = _dedupe_preserving_order(
        [*scoped_profile_components, *expanded_scope_components, *(extra_components or [])]
    )
    _validate_components_allowed_for_scope(components, scope)
    materialize_adapter_outputs = mode != "dry-run"
    artifacts = _artifacts(
        components,
        materialize_adapter_outputs=materialize_adapter_outputs,
        include_internal_hashes=True,
    )
    artifacts = [artifact for artifact in artifacts if artifact["target"] in targets]

    artifacts = _scope_filtered_artifacts(
        artifacts,
        scope=scope,
        materialize_adapter_outputs=materialize_adapter_outputs,
    )
    codex_user_hook_artifact = (
        _codex_user_hook_registration_artifact(components) if scope == "user" else None
    )
    if codex_user_hook_artifact is not None and not any(
        artifact["target"] == "codex"
        and artifact["destination"] == ".codex/hooks.json"
        and CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT in artifact.get(
            "component_ids",
            [artifact["component_id"]],
        )
        for artifact in artifacts
    ):
        artifacts.append(codex_user_hook_artifact)

    validate_final_destination_fan_in(artifacts)
    artifacts = _strip_internal_artifact_fields(artifacts)

    plan = {
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
    if selected_retired_registrations:
        plan["retired_codex_agent_registrations"] = retired_codex_agent_registrations(
            {"retired_codex_agent_registrations": selected_retired_registrations}
        )
    materialization = _materialization_status(mode)
    if materialization is not None:
        plan["materialization"] = materialization
    return plan


def build_hermes_external_package_plan(*, lifecycle: str, mode: str) -> dict[str, Any]:
    """Plan the explicit, root-only Hermes external package lifecycle."""
    if mode != "apply":
        raise ValueError("Hermes external package lifecycle requires apply mode")
    adapter = _load_yaml(REPO_ROOT / "adapters/hermes/adapter.yml")
    hermes = adapter.get("hermes")
    package = hermes.get("external_package") if isinstance(hermes, dict) else None
    catalog = package.get("components") if isinstance(package, dict) else None
    if not isinstance(catalog, list) or len(catalog) != 25 or not all(isinstance(item, str) for item in catalog):
        raise ValueError("adapters/hermes/adapter.yml external_package catalog is invalid")
    if lifecycle in {"install", "update"}:
        build_adapter_outputs([], hermes_external_package=True, check=False)
    return {
        "plan_id": f"harnesskit.install-plan.hermes-external-package.{lifecycle}",
        "scope": "user",
        "mode": mode,
        "targets": ["hermes"],
        "components": catalog,
        "artifacts": [],
        "runtime_surfaces": [],
        "activation_gates": [],
        "hermes_external_package": {
            "lifecycle": lifecycle,
            "source": str(HERMES_EXTERNAL_PACKAGE_SOURCE),
            "skills_source": str(HERMES_EXTERNAL_SKILLS_SOURCE),
            "hooks_source": str(HERMES_EXTERNAL_HOOKS_SOURCE),
            "config_source": str(HERMES_EXTERNAL_CONFIG_SOURCE),
            "package_root": str(HERMES_EXTERNAL_PACKAGE_ROOT),
            "config": str(HERMES_EXTERNAL_CONFIG),
            "hook_root": str(HERMES_EXTERNAL_HOOK_ROOT),
            "hook_command": HERMES_EXTERNAL_HOOK_COMMAND,
            "hook_event": HERMES_EXTERNAL_HOOK_EVENT,
            "marker": ".harnesskit-hermes-external-package.json",
            "catalog": catalog,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a HarnessKit install plan.")
    # Three selection modes: --profile (existing), --composite (expand a
    # composite to its members), or standalone --component. --profile is no
    # longer required; --profile and --composite are mutually exclusive.
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--profile", help="Profile name or id")
    selection.add_argument(
        "--hermes-external-package",
        choices=["install", "update", "cleanup", "uninstall"],
        help="Explicit root-only Hermes external package lifecycle.",
    )
    selection.add_argument(
        "--composite",
        help="Composite id to expand to its members and install as a group.",
    )
    parser.add_argument(
        "--scope",
        choices=["project", "user"],
        default="project",
        help="Install scope",
    )
    parser.add_argument(
        "--mode",
        choices=["dry-run", "apply", "verify"],
        default="dry-run",
        help="Install plan mode",
    )
    parser.add_argument(
        "--component",
        action="append",
        default=[],
        help=(
            "Component id. With --profile it is an additional component; with no "
            "--profile/--composite it selects a single standalone component."
        ),
    )
    parser.add_argument("--format", choices=["json", "yaml"], default="yaml")
    args = parser.parse_args(argv)

    if args.hermes_external_package is not None and args.component:
        parser.error("--component cannot be combined with --hermes-external-package")
    if args.composite is not None and args.component:
        parser.error("--component cannot be combined with --composite")
    if args.profile is None and args.composite is None and args.hermes_external_package is None and not args.component:
        parser.error("one of --profile, --composite, or --component is required")
    if args.profile is None and args.composite is None and len(args.component) > 1:
        parser.error(
            "standalone --component install accepts exactly one component; "
            "use --profile or --composite to install a group"
        )

    try:
        if args.hermes_external_package is not None:
            plan = build_hermes_external_package_plan(
                lifecycle=args.hermes_external_package, mode=args.mode
            )
        elif args.composite is not None:
            plan = build_composite_plan(
                args.composite,
                scope=args.scope,
                mode=args.mode,
            )
        elif args.profile is None:
            plan = build_component_plan(
                args.component[0],
                scope=args.scope,
                mode=args.mode,
            )
        else:
            plan = build_plan(
                args.profile,
                scope=args.scope,
                mode=args.mode,
                extra_components=args.component,
            )
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
