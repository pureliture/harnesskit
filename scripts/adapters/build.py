from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.adapters.hermes import (  # noqa: E402
    HERMES_PROHIBITED_DESTINATION_STRINGS,
    HERMES_SKILL_NAME_RE,
    optimal_response_wrapper_content,
    prepare_skill,
    render_external_package_manifest,
    render_hook_manifest,
    render_skill,
    skill_slug as _hermes_slug,
)
from scripts.profiles.selection import validate_profile_selection  # noqa: E402

REGISTRY_PATH = REPO_ROOT / "components" / "registry.yml"
PROFILES_DIR = REPO_ROOT / "profiles"
ADAPTERS_DIR = REPO_ROOT / "adapters"

TOKEN_RE = re.compile(r"{{\s*([a-zA-Z0-9_.-]+)\s*}}")
PROFILE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

INSTALLABLE_KINDS = {"skill", "agent", "hook", "rule"}
NON_INSTALLABLE_KINDS = {"workflow", "command"}

# Named adapter output families. A bundled_files output must resolve to exactly
# one family; anything else is rejected, replacing per-path output exceptions.
# `project-runtime` roots are tracked live-runtime paths the build is allowed to
# own. They are declared in adapters/project/adapter.yml (data, not a code
# literal); extend that allowlist only with explicit approval.
PROJECT_TARGET_ADAPTER = ADAPTERS_DIR / "project" / "adapter.yml"
ALLOWED_BUNDLE_KINDS = ("runtime-script", "config", "asset", "doc")
ALLOWED_BUNDLE_SURFACES = (
    "dist",
    "project-runtime",
    "project",
    "claude",
    "codex",
    "gemini",
    "antigravity",
    "antigravity-cli",
)
# Tracked generated outputs must stay group/other readable; owner-only modes
# (no group/other read bits) look like leaked secrets and are rejected.
SECRET_LIKE_MODE_MASK = 0o044

CODEX_USER_HOOK_OUTPUT = "dist/codex/.codex/hooks.user.json"
STALE_PROJECT_CONTEXT_OUTPUTS = (
    "dist/project/GEMINI.md",
)
CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT = "harnesskit.hook.optimal-response-prompt-submit"
CODEX_OPTIMAL_RESPONSE_COMMAND = (
    'OPTIMAL_RESPONSE_SURFACE=codex '
    'node "${CODEX_HOME:-$HOME/.codex}/skills/optimal-response/hooks/stop-prompt-submit.cjs"'
)
HERMES_TARGET = "hermes"
HERMES_ADAPTER_PATH = ADAPTERS_DIR / HERMES_TARGET / "adapter.yml"
HERMES_OPTIMAL_RESPONSE_WRAPPER_OUTPUT = (
    "dist/hermes/hooks/optimal-response/optimal-response-pre-llm.cjs"
)
HERMES_OPTIMAL_RESPONSE_HOOK_OUTPUT = (
    "dist/hermes/hooks/optimal-response/stop-prompt-submit.cjs"
)
HERMES_OPTIMAL_RESPONSE_INSTALL_COMMAND = (
    "~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs"
)
HERMES_ALLOWED_SUPPORT_DIRS = {"references", "templates", "scripts", "assets"}
HERMES_PROFILE_PLAN_VERSION = 1
HERMES_PROFILE_OUTPUT_SUFFIX = ".hermes-profile-out"
HERMES_PROFILE_OUTPUT_MARKER = ".harnesskit-hermes-profile-output"
HERMES_PROFILE_OUTPUT_MARKER_CONTENT = "HarnessKit managed temporary Hermes profile output.\n"
HERMES_EXTERNAL_PACKAGE_ALLOWED_EXCLUSIONS = {
    "harnesskit.skill.optimal-response",
    "harnesskit.skill.wayfinder",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML mapping expected: {path}")
    return data


def _display_path(path: Path, *, relative_to: Path | None = None) -> str:
    display_root = relative_to or REPO_ROOT
    try:
        return path.relative_to(display_root).as_posix()
    except ValueError:
        return path.as_posix()


def _lookup(context: dict[str, Any], dotted_key: str) -> str:
    value: Any = context
    for part in dotted_key.split("."):
        if not isinstance(value, dict) or part not in value:
            return ""
        value = value[part]
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _render_template(template: str, context: dict[str, Any]) -> str:
    return TOKEN_RE.sub(lambda match: _lookup(context, match.group(1)), template)


def _target_name(kind: str, output_path: str) -> str:
    path = Path(output_path)
    if kind == "skill":
        return path.parent.name
    return path.stem


def _component_dir(manifest_path: Path) -> Path:
    return manifest_path.parent


def _description(manifest: dict[str, Any], target_options: dict[str, Any]) -> str:
    raw = target_options.get("description") or manifest.get("description") or manifest.get("summary")
    if raw is None:
        return ""
    return " ".join(str(raw).split())


def _yaml_scalar(value: str) -> str:
    """Return one JSON-compatible quoted scalar that YAML can parse exactly."""
    return json.dumps(value, ensure_ascii=False)


def _skills_yaml(target_options: dict[str, Any]) -> str:
    skills = target_options.get("skills") or []
    return yaml.safe_dump(skills, default_flow_style=True, sort_keys=False).strip()


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    lines = text.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1 :]).lstrip("\n")
    return text


def _codex_agent_config_file(output_path: str) -> str:
    parts = Path(output_path).parts
    if ".codex" not in parts:
        return ""
    codex_index = parts.index(".codex")
    return str(Path(*parts[codex_index + 1 :]))


def _hook_name(manifest: dict[str, Any], target_options: dict[str, Any]) -> str:
    raw = target_options.get("hook_name") or target_options.get("name")
    if raw:
        return str(raw)
    component_id = str(manifest.get("component_id", ""))
    return component_id.split(".")[-1] if component_id else "harnesskit-hook"


def _antigravity_hook_registration_content(
    manifest: dict[str, Any],
    target_options: dict[str, Any],
) -> str:
    hook = manifest.get("hook") or {}
    if not isinstance(hook, dict):
        hook = {}

    event = (
        target_options.get("event")
        or target_options.get("hook_event")
        or hook.get("source_event")
        or "Stop"
    )
    command = target_options.get("command") or hook.get("source_command") or ""
    timeout = target_options.get("timeout") or hook.get("source_timeout_seconds") or 10
    matcher = target_options.get("matcher")
    if matcher is None:
        matcher = hook.get("source_matcher")

    handler = {
        "type": target_options.get("type", "command"),
        "command": command,
        "timeout": timeout,
    }
    if str(event) in {"PreToolUse", "PostToolUse"}:
        event_config: list[dict[str, Any]] = [
            {
                "matcher": matcher or "*",
                "hooks": [handler],
            }
        ]
    else:
        event_config = [handler]

    return (
        json.dumps(
            {_hook_name(manifest, target_options): {str(event): event_config}},
            indent=2,
        )
        + "\n"
    )


def _hook_registration_content(
    manifest: dict[str, Any],
    target: str,
    target_options: dict[str, Any],
) -> str:
    if target in {"antigravity", "antigravity-cli"}:
        return _antigravity_hook_registration_content(manifest, target_options)
    if target == HERMES_TARGET:
        return _hermes_hook_registration_content(manifest, target_options)

    hook = manifest.get("hook") or {}
    if not isinstance(hook, dict):
        hook = {}

    event = (
        target_options.get("event")
        or target_options.get("hook_event")
        or hook.get("source_event")
        or "Stop"
    )
    command = target_options.get("command") or hook.get("source_command") or ""
    timeout = target_options.get("timeout") or hook.get("source_timeout_seconds") or 10
    matcher = target_options.get("matcher")
    if matcher is None:
        matcher = hook.get("source_matcher")

    hook_group: dict[str, Any] = {
        "hooks": [
            {
                "type": target_options.get("type", "command"),
                "command": command,
                "timeout": timeout,
            }
        ]
    }
    if matcher:
        hook_group["matcher"] = matcher

    return json.dumps({"hooks": {str(event): [hook_group]}}, indent=2) + "\n"


def _hermes_hook_registration_content(
    manifest: dict[str, Any],
    target_options: dict[str, Any],
) -> str:
    hook = manifest.get("hook") or {}
    if not isinstance(hook, dict):
        hook = {}
    event = str(
        target_options.get("event")
        or target_options.get("hook_event")
        or hook.get("source_event")
        or "pre_llm_call"
    )
    if manifest.get("component_id") == CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT:
        command = _hermes_optimal_response_command()
    else:
        command = str(target_options.get("command") or "")
    timeout = int(target_options.get("timeout") or hook.get("source_timeout_seconds") or 10)
    return render_hook_manifest(
        event=event,
        command=command,
        timeout=timeout,
    )


def _hermes_optimal_response_command() -> str:
    return HERMES_OPTIMAL_RESPONSE_INSTALL_COMMAND


def _hermes_optimal_response_hook_files(
    *, rel_manifest: str
) -> list[tuple[Path, str, bool, int | None]]:
    hook_source = _safe_bundle_source(
        "components/hooks/optimal-response-prompt-submit/stop-prompt-submit.cjs",
        component_id=CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT,
        rel_manifest=rel_manifest,
    )
    return [
        (
            _safe_bundle_output(HERMES_OPTIMAL_RESPONSE_WRAPPER_OUTPUT, rel_manifest=rel_manifest),
            optimal_response_wrapper_content().rstrip() + "\n",
            False,
            0o755,
        ),
        (
            _safe_bundle_output(HERMES_OPTIMAL_RESPONSE_HOOK_OUTPUT, rel_manifest=rel_manifest),
            hook_source.read_text(encoding="utf-8").rstrip() + "\n",
            False,
            None,
        ),
    ]


def _is_antigravity_hooks_output(output_path: Path) -> bool:
    try:
        rel_path = output_path.relative_to(REPO_ROOT).as_posix()
        is_antigravity_dist = rel_path.startswith("dist/antigravity/") or rel_path.startswith(
            "dist/antigravity-cli/"
        )
        return is_antigravity_dist and rel_path.endswith("hooks.json")
    except ValueError:
        return False


def _merge_antigravity_hook_chunks(chunks: list[str]) -> str:
    merged: dict[str, Any] = {}
    for chunk in chunks:
        data = json.loads(chunk)
        if not isinstance(data, dict) or "hooks" in data:
            raise ValueError("Antigravity hook chunk must be a hook-name mapping")
        for hook_name, event_map in data.items():
            if not isinstance(event_map, dict):
                raise ValueError(f"Antigravity hook events must be mappings: {hook_name}")
            merged_events = merged.setdefault(hook_name, {})
            for event, handlers in event_map.items():
                if not isinstance(handlers, list):
                    raise ValueError(f"Antigravity hook event entries must be lists: {event}")
                merged_handlers = merged_events.setdefault(event, [])
                for handler in handlers:
                    if handler not in merged_handlers:
                        merged_handlers.append(handler)
    return json.dumps(merged, indent=2) + "\n"


def _merge_json_hook_chunks(chunks: list[str], output_path: Path) -> str:
    if _is_antigravity_hooks_output(output_path):
        return _merge_antigravity_hook_chunks(chunks)

    merged: dict[str, Any] = {"hooks": {}}
    for chunk in chunks:
        data = json.loads(chunk)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            raise ValueError("append JSON hook chunk must contain a hooks mapping")
        for event, groups in hooks.items():
            if not isinstance(groups, list):
                raise ValueError(f"hook event entries must be lists: {event}")
            merged_groups = merged["hooks"].setdefault(event, [])
            for group in groups:
                if group not in merged_groups:
                    merged_groups.append(group)
    return json.dumps(merged, indent=2) + "\n"


def _combined_append_content(output_path: Path, chunks: list[str]) -> str:
    if output_path.suffix == ".json":
        return _merge_json_hook_chunks(chunks, output_path)
    return "".join(chunks)


def _safe_relative_path(raw: Any, *, field_name: str, rel_manifest: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{rel_manifest}: {field_name} must be a non-empty string")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{rel_manifest}: {field_name} must stay inside the repository: {raw}")
    return path


def _safe_output_path(raw: Any, *, target: str, rel_manifest: str) -> Path:
    path = _safe_relative_path(raw, field_name="target output_path", rel_manifest=rel_manifest)
    expected_prefix = ("dist", target)
    if path.parts[:2] != expected_prefix:
        raise ValueError(
            f"{rel_manifest}: target output_path must live under dist/{target}/: {raw}"
        )
    return REPO_ROOT / Path(*path.parts)


def _safe_bundle_source(raw: Any, *, component_id: str, rel_manifest: str) -> Path:
    path = _safe_relative_path(raw, field_name="bundled_files.source", rel_manifest=rel_manifest)
    source_path = REPO_ROOT / Path(*path.parts)
    if not source_path.is_file():
        raise FileNotFoundError(f"Missing bundled file for {component_id}: {raw}")
    return source_path


def _project_runtime_roots() -> tuple[str, ...]:
    """Allowed project-runtime roots, declared in adapters/project/adapter.yml.

    These are the tracked live-runtime directories (outside dist/) the build may
    own. They live in config so build.py holds no path literal for them.
    """
    data = _load_yaml(PROJECT_TARGET_ADAPTER)
    roots = (data.get("project_runtime") or {}).get("allowed_roots") or []
    if not isinstance(roots, list) or not all(isinstance(r, str) and r for r in roots):
        raise ValueError(
            f"{PROJECT_TARGET_ADAPTER}: project_runtime.allowed_roots must be a list of non-empty strings"
        )
    return tuple(roots)


def _resolve_bundle_family(
    path: PurePosixPath, *, raw: Any, rel_manifest: str
) -> tuple[str, str | None]:
    """Classify a bundled output path into a named output family.

    Returns ``(family, dist_target)`` where ``dist_target`` is the dist target
    segment for ``dist`` outputs and ``None`` for ``project-runtime`` outputs.
    Raises if the path belongs to no allowed family.
    """
    parts = path.parts
    if parts[:1] == ("dist",) and len(parts) >= 3:
        return "dist", parts[1]
    project_runtime_roots = _project_runtime_roots()
    if parts[:1] and parts[0] in project_runtime_roots:
        return "project-runtime", None
    raise ValueError(
        f"{rel_manifest}: bundled_files.output_path is outside an allowed output family "
        f"(dist/<target>/... or project-runtime roots {project_runtime_roots}): {raw}"
    )


def _validate_bundle_surface(
    surface: Any, *, family: str, dist_target: str | None, rel_manifest: str
) -> None:
    if surface is None:
        return
    if not isinstance(surface, str) or surface not in ALLOWED_BUNDLE_SURFACES:
        raise ValueError(
            f"{rel_manifest}: bundled_files.surface must be one of {ALLOWED_BUNDLE_SURFACES}: {surface!r}"
        )
    if family == "project-runtime" and surface != "project-runtime":
        raise ValueError(
            f"{rel_manifest}: bundled_files.surface must be 'project-runtime' for project-runtime outputs: {surface!r}"
        )
    if family == "dist" and surface not in ("dist", dist_target):
        raise ValueError(
            f"{rel_manifest}: bundled_files.surface {surface!r} does not match dist target {dist_target!r}"
        )


def _validate_bundle_kind(kind: Any, *, rel_manifest: str) -> None:
    if kind is None:
        return
    if not isinstance(kind, str) or kind not in ALLOWED_BUNDLE_KINDS:
        raise ValueError(
            f"{rel_manifest}: bundled_files.kind must be one of {ALLOWED_BUNDLE_KINDS}: {kind!r}"
        )


def _reject_secret_like_mode(mode: int | None, *, raw: Any, rel_manifest: str) -> None:
    if mode is None:
        return
    if (mode & SECRET_LIKE_MODE_MASK) == 0:
        raise ValueError(
            f"{rel_manifest}: bundled_files.mode {oct(mode)} is secret-like (owner-only) for a "
            f"tracked generated output; tracked outputs must stay group/other readable: {raw}"
        )


def _safe_bundle_output(raw: Any, *, rel_manifest: str) -> Path:
    path = _safe_relative_path(raw, field_name="bundled_files.output_path", rel_manifest=rel_manifest)
    _resolve_bundle_family(path, raw=raw, rel_manifest=rel_manifest)
    return REPO_ROOT / Path(*path.parts)


def _safe_bundle_mode(raw: Any, *, rel_manifest: str) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"{rel_manifest}: bundled_files.mode must be a string or integer")
    if isinstance(raw, int):
        mode = raw
    elif isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized.startswith("0o"):
            digits = normalized[2:]
        elif len(normalized) == 4 and normalized.startswith("0"):
            digits = normalized[1:]
        else:
            digits = normalized
        if not digits or any(char not in "01234567" for char in digits):
            raise ValueError(f"{rel_manifest}: bundled_files.mode must be an octal mode")
        mode = int(digits, 8)
    else:
        raise ValueError(f"{rel_manifest}: bundled_files.mode must be a string or integer")
    if mode < 0 or mode > 0o777:
        raise ValueError(f"{rel_manifest}: bundled_files.mode must not exceed 0777")
    return mode


def _explicitly_internal_source_only(manifest: dict[str, Any]) -> bool:
    generic_internal = manifest.get("installable") is False or manifest.get("adapter_output") in {
        "internal",
        "source-only",
    }
    kind = manifest.get("kind")
    if kind == "workflow":
        return generic_internal or manifest.get("runtime_implemented") is False or (
            (manifest.get("routine_harness") or {}).get("runner_deferred") is True
        )
    return generic_internal


def _validate_installable_kind(component_id: str, manifest: dict[str, Any], rel_manifest: str) -> bool:
    kind = manifest.get("kind")
    if kind in INSTALLABLE_KINDS:
        return True
    if kind in NON_INSTALLABLE_KINDS:
        if _explicitly_internal_source_only(manifest):
            return False
        raise ValueError(
            f"{rel_manifest}: {kind} is not an installable adapter kind for {component_id}; "
            "model executable workflows as skill.kind=workflow_trigger or mark the record "
            "internal/source-only before selecting it for adapter output"
        )
    raise ValueError(f"{rel_manifest}: unsupported component kind for {component_id}: {kind}")


def _codex_user_prompt_submit_registration_content() -> str:
    return (
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": CODEX_OPTIMAL_RESPONSE_COMMAND,
                                    "timeout": 5,
                                }
                            ]
                        }
                    ]
                }
            },
            indent=2,
        )
        + "\n"
    )


def _context(
    manifest: dict[str, Any],
    target: str,
    target_options: dict[str, Any],
    output_path: str,
    body: str,
) -> dict[str, Any]:
    model_policy = manifest.get("model_policy") or {}
    target_model = target_options.get("model") or model_policy.get("normal") or "inherit"
    model_toml_line = "" if target_model == "inherit" else f'model = "{target_model}"\n'
    model_yaml_line = "" if target_model == "inherit" else f"model: {target_model}\n"

    description = _description(manifest, target_options)
    if target == "antigravity" and manifest["kind"] == "agent" and "description" in target_options:
        description = target_options["description"]
    return {
        "target_name": _target_name(manifest["kind"], output_path),
        "component_id": _target_name(manifest["kind"], output_path),
        "canonical_component_id": manifest["component_id"],
        "description": description,
        "description_yaml": _yaml_scalar(description),
        "description_toml": json.dumps(description),
        "body": body.rstrip(),
        "model": target_model,
        "model_toml_line": model_toml_line,
        "model_yaml_line": model_yaml_line,
        "model_policy": {"normal": target_model},
        "reasoning_effort": target_options.get("reasoning_effort", "medium"),
        "tools": target_options.get("tools", ""),
        "skills_yaml": _skills_yaml(target_options),
        "color": target_options.get("color", ""),
        "command": target_options.get("command", ""),
        "command_json": json.dumps(target_options.get("command", "")),
        "timeout": str(target_options.get("timeout", 10)),
        "agent_config_file": _codex_agent_config_file(output_path),
    }


def _selected_registry_entries(component_ids: list[str]) -> dict[str, dict[str, Any]]:
    registry = _load_yaml(REGISTRY_PATH)
    entries = registry.get("components")
    if not isinstance(entries, dict):
        raise ValueError("components/registry.yml must contain a components mapping")

    selected: dict[str, dict[str, Any]] = {}
    for component_id in component_ids:
        if component_id not in entries:
            raise KeyError(f"Component is not registered: {component_id}")
        selected[component_id] = entries[component_id]
    return selected


def _profile_path(profile: str) -> Path:
    profile_name = profile.removeprefix("harnesskit.profile.")
    if not PROFILE_NAME_RE.fullmatch(profile_name):
        raise ValueError(f"Invalid profile name: {profile}")
    return PROFILES_DIR / f"{profile_name}.yml"


def _component_ids_from_profiles(profile_ids: list[str]) -> list[str]:
    registry = _load_yaml(REGISTRY_PATH)
    registry_components = registry.get("components")
    if not isinstance(registry_components, dict):
        raise ValueError("components/registry.yml must contain a components mapping")
    component_ids: list[str] = []
    for profile_id in profile_ids:
        profile_path = _profile_path(profile_id)
        if not profile_path.is_file():
            raise FileNotFoundError(f"Missing profile: {profile_id}")
        profile = _load_yaml(profile_path)
        validate_profile_selection(
            profile,
            registry_components,
            load_manifest=lambda rel_path: _load_yaml(REPO_ROOT / rel_path),
        )
        components = profile.get("components")
        if not isinstance(components, list):
            raise ValueError(f"{profile_path.relative_to(REPO_ROOT)}: components must be a list")
        for component_id in components:
            if not isinstance(component_id, str):
                raise ValueError(
                    f"{profile_path.relative_to(REPO_ROOT)}: component ids must be strings"
                )
            component_ids.append(component_id)
        install_policy = profile.get("install_policy") or {}
        scope_components = install_policy.get("scope_components") or {}
        if not isinstance(scope_components, dict):
            raise ValueError(
                f"{profile_path.relative_to(REPO_ROOT)}: install_policy.scope_components must be a mapping"
            )
        for scope, scoped_components in scope_components.items():
            if scope not in {"project", "user"}:
                raise ValueError(
                    f"{profile_path.relative_to(REPO_ROOT)}: unsupported scope_components scope: {scope}"
                )
            if not isinstance(scoped_components, list):
                raise ValueError(
                    f"{profile_path.relative_to(REPO_ROOT)}: install_policy.scope_components.{scope} must be a list"
                )
            for component_id in scoped_components:
                if not isinstance(component_id, str):
                    raise ValueError(
                        f"{profile_path.relative_to(REPO_ROOT)}: scoped component ids must be strings"
                    )
                component_ids.append(component_id)
    return component_ids


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _unpack_rendered_output(item: tuple[Any, ...]) -> tuple[Path, str, bool, int | None]:
    if len(item) == 3:
        output_path, content, append = item
        return output_path, content, append, None
    output_path, content, append, mode = item
    return output_path, content, append, mode


def _render_component(
    component_id: str,
    registry_entry: dict[str, Any],
    *,
    explicit: bool = True,
    target_ids: set[str] | None = None,
) -> list[tuple[Path, str, bool, int | None]]:
    rel_manifest = registry_entry.get("path")
    if not rel_manifest:
        raise ValueError(f"Missing path for {component_id}")

    manifest_path = REPO_ROOT / rel_manifest
    if not manifest_path.is_file():
        if registry_entry.get("planned") is True:
            return []
        raise FileNotFoundError(f"Missing manifest for {component_id}: {rel_manifest}")

    manifest = _load_yaml(manifest_path)
    registry_kind = registry_entry.get("kind")
    if isinstance(registry_kind, str) and manifest.get("kind") is None:
        manifest = {**manifest, "kind": registry_kind}
    if not _validate_installable_kind(component_id, manifest, rel_manifest):
        return []
    kind = manifest.get("kind")

    component_scopes = manifest.get("scopes")
    user_scope_only = (
        isinstance(component_scopes, list)
        and "user" in component_scopes
        and "project" not in component_scopes
    )

    targets = manifest.get("targets") or {}
    if not isinstance(targets, dict):
        raise ValueError(f"{rel_manifest}: targets must be a mapping")

    rendered: list[tuple[Path, str, bool, int | None]] = []
    if kind in {"skill", "agent", "hook"}:
        for target, target_config in targets.items():
            if target_ids is not None and target not in target_ids:
                continue
            if kind == "hook" and user_scope_only and not explicit:
                # User-scope-only hooks are materialized only by the install
                # layer (scripts/install/plan.py, per-scope) or by an explicit
                # --component selection — never by an unscoped/profile adapter
                # build writing into project-shared hook files.
                continue
            if not isinstance(target_config, dict):
                raise ValueError(f"{rel_manifest}: target config must be a mapping: {target}")
            adapter_path = ADAPTERS_DIR / target / "adapter.yml"
            adapter = _load_yaml(adapter_path)
            structure = adapter["structures"][f"{kind}s"]
            template_path = ADAPTERS_DIR / target / structure["template"]
            body_path = _component_dir(manifest_path) / structure["content_source"]
            output_path = str(target_config.get("output_path", ""))
            output_abs_path = _safe_output_path(output_path, target=target, rel_manifest=rel_manifest)

            target_options = (manifest.get("adapter") or {}).get(target) or {}
            body = body_path.read_text(encoding="utf-8")
            context = _context(manifest, target, target_options, output_path, body)
            if kind == "hook":
                if target_options.get("imported_hook"):
                    handler = json.loads(body)
                    content = json.dumps({"hooks": {target_options["event"]: [{"hooks": [handler]}]}}, indent=2) + "\n"
                else:
                    content = _hook_registration_content(manifest, target, target_options)
            else:
                template = template_path.read_text(encoding="utf-8")
                content = _render_template(template, context)
                if target == "codex" and kind == "agent" and "imported_agent" in target_options:
                    fields = {k: v for k, v in target_options["imported_agent"].items() if k != "registration"}
                    fields["developer_instructions"] = body
                    content = "\n".join(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items()) + "\n"
                if kind == "skill" and "skill_frontmatter" in target_options:
                    extras = target_options["skill_frontmatter"]
                    if not isinstance(extras, dict) or set(extras) & {"name", "description"}:
                        raise ValueError(f"{rel_manifest}: invalid skill_frontmatter options")
                    # Existing template owns name/description; serialize typed extras
                    # before its closing delimiter, never interpolate raw YAML.
                    lines = content.splitlines(keepends=True)
                    if not lines or lines[0].strip() != "---":
                        raise ValueError(f"{rel_manifest}: skill template requires frontmatter")
                    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
                    fields = yaml.safe_load("".join(lines[1:end]))
                    body = "\n" + "".join(lines[end + 1:])
                    fields.update(extras)
                    content = "---\n" + yaml.safe_dump(fields, allow_unicode=True, sort_keys=False) + "---" + body
                rendered.append((output_abs_path, content.rstrip() + "\n", False, None))

            registration = structure.get("registration")
            if kind == "hook":
                registration = {"path": output_path, "template": structure["template"]}
            if isinstance(registration, dict):
                if kind == "hook":
                    registration_path = output_abs_path
                else:
                    registration_path = REPO_ROOT / adapter["output_root"] / registration["path"]
                if kind == "hook":
                    registration_content = content
                else:
                    registration_template = (
                        ADAPTERS_DIR / target / registration["template"]
                    ).read_text(encoding="utf-8")
                    registration_content = _render_template(registration_template, context)
                    if target == "codex" and kind == "agent" and "imported_agent" in target_options:
                        fields = target_options["imported_agent"]["registration"]
                        registration_content = f'[agents.{json.dumps(context["target_name"])}]\n' + "\n".join(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items()) + "\n"
                rendered.append((registration_path, registration_content.rstrip() + "\n", True, None))
    for bundle in manifest.get("bundled_files") or []:
        if not isinstance(bundle, dict):
            raise ValueError(f"{rel_manifest}: bundled_files entries must be mappings")
        source = bundle.get("source")
        output_path = bundle.get("output_path")
        if not isinstance(source, str) or not isinstance(output_path, str):
            raise ValueError(
                f"{rel_manifest}: bundled_files entries require source and output_path"
            )
        rel_output = _safe_relative_path(
            output_path, field_name="bundled_files.output_path", rel_manifest=rel_manifest
        )
        family, dist_target = _resolve_bundle_family(
            rel_output, raw=output_path, rel_manifest=rel_manifest
        )
        if target_ids is not None:
            if family == "dist" and dist_target not in target_ids:
                continue
            if family == "project-runtime" and "project" not in target_ids:
                continue
        _validate_bundle_surface(
            bundle.get("surface"), family=family, dist_target=dist_target, rel_manifest=rel_manifest
        )
        _validate_bundle_kind(bundle.get("kind"), rel_manifest=rel_manifest)
        source_path = _safe_bundle_source(source, component_id=component_id, rel_manifest=rel_manifest)
        output_abs_path = REPO_ROOT / Path(*rel_output.parts)
        mode = _safe_bundle_mode(bundle.get("mode"), rel_manifest=rel_manifest)
        _reject_secret_like_mode(mode, raw=output_path, rel_manifest=rel_manifest)
        rendered.append(
            (
                output_abs_path,
                source_path.read_bytes().decode("utf-8") if bundle.get("kind") == "doc" else source_path.read_text(encoding="utf-8").rstrip() + "\n",
                False,
                mode,
            )
        )
    if component_id == CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT and (
        target_ids is None or "codex" in target_ids
    ):
        rendered.append(
            (
                _safe_bundle_output(CODEX_USER_HOOK_OUTPUT, rel_manifest=rel_manifest),
                _codex_user_prompt_submit_registration_content(),
                False,
                None,
            )
        )
    if (
        component_id == CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT
        and explicit
        and (target_ids is None or HERMES_TARGET in target_ids)
    ):
        rendered.extend(_hermes_optimal_response_hook_files(rel_manifest=rel_manifest))
    return rendered


def _hermes_adapter() -> dict[str, Any]:
    return _load_yaml(HERMES_ADAPTER_PATH)


def _hermes_policy(adapter: dict[str, Any]) -> dict[str, Any]:
    policy = adapter.get("hermes")
    if not isinstance(policy, dict):
        raise ValueError(f"{HERMES_ADAPTER_PATH.relative_to(REPO_ROOT)}: hermes policy required")
    return policy


def _hermes_excluded_components(policy: dict[str, Any]) -> set[str]:
    excluded = policy.get("excluded_components") or []
    if not isinstance(excluded, list) or not all(isinstance(item, str) for item in excluded):
        raise ValueError("adapters/hermes/adapter.yml: hermes.excluded_components must be a string list")
    return set(excluded)


def _hermes_profile_output_root(plan_path: Path) -> Path:
    return plan_path.parent / f"{plan_path.stem}{HERMES_PROFILE_OUTPUT_SUFFIX}"


def _load_hermes_profile_plan(
    raw_path: Path,
    *,
    selected_component_ids: list[str],
    policy: dict[str, Any],
) -> tuple[dict[str, list[str]], Path]:
    expanded_path = raw_path.expanduser()
    if expanded_path.is_symlink():
        raise ValueError("Hermes profile plan must not be a symlink")
    plan_path = expanded_path.resolve(strict=True)
    if not plan_path.is_file():
        raise ValueError(f"Hermes profile plan must be a file: {plan_path}")

    repo_root = REPO_ROOT.resolve()
    live_hermes_root = (Path.home() / ".hermes").resolve()
    if plan_path.is_relative_to(repo_root):
        raise ValueError("Hermes profile plan must live outside the repository")
    if plan_path.is_relative_to(live_hermes_root):
        raise ValueError("Hermes profile plan must live outside live ~/.hermes")

    plan = _load_yaml(plan_path)
    unknown_top_level = sorted(set(plan) - {"version", "profiles"})
    if unknown_top_level:
        raise ValueError(f"Hermes profile plan has unknown fields: {unknown_top_level}")
    plan_version = plan.get("version")
    if type(plan_version) is not int or plan_version != HERMES_PROFILE_PLAN_VERSION:
        raise ValueError(
            f"Hermes profile plan version must be {HERMES_PROFILE_PLAN_VERSION}"
        )
    profiles = plan.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("Hermes profile plan profiles must be a non-empty mapping")

    selected_set = set(selected_component_ids)
    selected_entries = _selected_registry_entries(selected_component_ids)
    normalized: dict[str, list[str]] = {}
    for profile, config in profiles.items():
        if not isinstance(profile, str) or not PROFILE_NAME_RE.fullmatch(profile):
            raise ValueError(f"Invalid Hermes profile plan name: {profile!r}")
        if not isinstance(config, dict):
            raise ValueError(f"Hermes profile plan entry must be a mapping: {profile}")
        unknown_profile_fields = sorted(set(config) - {"components"})
        if unknown_profile_fields:
            raise ValueError(
                f"Hermes profile plan entry has unknown fields for {profile}: "
                f"{unknown_profile_fields}"
            )
        components = config.get("components")
        if not isinstance(components, list) or not components or not all(
            isinstance(component_id, str) for component_id in components
        ):
            raise ValueError(
                f"Hermes profile plan components must be a non-empty string list: {profile}"
            )
        if len(set(components)) != len(components):
            raise ValueError(f"Hermes profile plan components must be unique: {profile}")
        for component_id in components:
            if component_id not in selected_set:
                raise ValueError(
                    f"{component_id}: Hermes profile plan must stay within "
                    "selected component closure"
                )
            if _hermes_renderable_manifest(
                component_id,
                selected_entries[component_id],
                policy,
            ) is None:
                raise ValueError(
                    f"{component_id}: selected component closure is not Hermes-renderable"
                )
        normalized[profile] = components

    output_root = _hermes_profile_output_root(plan_path)
    if output_root.is_relative_to(repo_root) or output_root.is_relative_to(live_hermes_root):
        raise ValueError("Hermes profile output must stay outside repository and live ~/.hermes")
    if output_root.is_symlink():
        raise ValueError("Hermes profile output root must not be a symlink")
    if (output_root / HERMES_PROFILE_OUTPUT_MARKER).is_symlink():
        raise ValueError("Hermes profile output ownership marker must not be a symlink")
    return normalized, output_root


def _hermes_output_path(
    adapter: dict[str, Any],
    policy: dict[str, Any],
    skill_name: str,
    *,
    profile: str | None = None,
    output_root: Path | None = None,
) -> Path:
    structure = adapter["structures"]["skills"]
    category = str(policy.get("category") or "software-development")
    if profile is None:
        path_template = str(structure["path_template"])
        rel_path = path_template.format(category=category, skill_id=skill_name)
    else:
        path_template = str(structure.get("profile_path_template") or "")
        if not path_template:
            raise ValueError("Hermes profile_path_template is required for profile exports")
        rel_path = path_template.format(profile=profile, category=category, skill_id=skill_name)
    resolved_output_root = output_root or (REPO_ROOT / str(adapter["output_root"]))
    return resolved_output_root / rel_path


def _hermes_renderable_manifest(
    component_id: str,
    registry_entry: dict[str, Any],
    policy: dict[str, Any],
    *,
    allowed_excluded_components: set[str] | None = None,
) -> tuple[Path, dict[str, Any]] | None:
    allowed_excluded_components = allowed_excluded_components or set()
    if (
        component_id in _hermes_excluded_components(policy)
        and component_id not in allowed_excluded_components
    ):
        return None
    rel_manifest = registry_entry.get("path")
    if not rel_manifest:
        return None
    manifest_path = REPO_ROOT / rel_manifest
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing manifest for {component_id}: {rel_manifest}")
    manifest = _load_yaml(manifest_path)
    if manifest.get("kind") != "skill":
        return None
    return manifest_path, manifest


def _render_hermes_skill(
    *,
    component_id: str,
    registry_entry: dict[str, Any],
    adapter: dict[str, Any],
    policy: dict[str, Any],
    profile: str | None = None,
    output_root: Path | None = None,
    allowed_excluded_components: set[str] | None = None,
) -> tuple[Path, str, bool, int | None]:
    manifest_info = _hermes_renderable_manifest(
        component_id,
        registry_entry,
        policy,
        allowed_excluded_components=allowed_excluded_components,
    )
    if manifest_info is None:
        raise ValueError(f"{component_id}: Hermes can render skill components only")
    manifest_path, manifest = manifest_info
    structure = adapter["structures"]["skills"]
    body_path = _component_dir(manifest_path) / structure["content_source"]
    prepared = prepare_skill(
        component_id=component_id,
        manifest=manifest,
        raw_body=body_path.read_text(encoding="utf-8"),
        adapter_version=str(adapter.get("version") or "0.1.0"),
        policy=policy,
    )
    projection = render_skill(
        prepared=prepared,
        template=(ADAPTERS_DIR / HERMES_TARGET / structure["template"]).read_text(
            encoding="utf-8"
        ),
    )
    return (
        _hermes_output_path(
            adapter,
            policy,
            projection.skill_name,
            profile=profile,
            output_root=output_root,
        ),
        projection.content,
        False,
        None,
    )


def _render_hermes_support_files(
    component_skill_roots: list[tuple[str, Path]], adapter: dict[str, Any], policy: dict[str, Any]
) -> list[tuple[Path, str, bool, int | None]]:
    rendered: list[tuple[Path, str, bool, int | None]] = []
    support_files = policy.get("support_files") or {}
    if not isinstance(support_files, dict):
        raise ValueError("adapters/hermes/adapter.yml: hermes.support_files must be a mapping")
    for component_id, skill_root in component_skill_roots:
        if component_id not in support_files:
            continue
        entries = support_files[component_id]
        if not isinstance(entries, list):
            raise ValueError(f"Hermes support_files must be a list: {component_id}")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"Hermes support_files entries must be mappings: {component_id}")
            source = _safe_bundle_source(
                entry.get("source"),
                component_id=component_id,
                rel_manifest="adapters/hermes/adapter.yml",
            )
            output_name = entry.get("output_name")
            if not isinstance(output_name, str) or "/" in output_name or not output_name:
                raise ValueError(f"Hermes support output_name must be a file name: {component_id}")
            output_path = skill_root / "references" / output_name
            rendered.append(
                (output_path, source.read_text(encoding="utf-8").rstrip() + "\n", False, None)
            )
    return rendered


def _render_hermes_install_note(adapter: dict[str, Any], policy: dict[str, Any]) -> tuple[Path, str, bool, int | None]:
    output_root = REPO_ROOT / str(adapter["output_root"])
    skills_external_dir = (output_root / str(policy.get("skills_root") or "skills")).relative_to(
        REPO_ROOT
    )
    template = (ADAPTERS_DIR / HERMES_TARGET / "templates/install-note.md").read_text(
        encoding="utf-8"
    )
    content = _render_template(
        template,
        {
            "skills_external_dir": skills_external_dir.as_posix(),
        },
    ).rstrip() + "\n"
    return output_root / "README.md", content, False, None


def _render_hermes_outputs(
    component_ids: list[str],
    *,
    profile_exports: dict[str, list[str]] | None = None,
    profile_output_root: Path | None = None,
) -> list[tuple[Path, str, bool, int | None]]:
    adapter = _hermes_adapter()
    policy = _hermes_policy(adapter)
    entries = _selected_registry_entries(component_ids)
    component_set = set(component_ids)
    profile_exports = profile_exports or {}
    if bool(profile_exports) != (profile_output_root is not None):
        raise ValueError("Hermes profile exports require one temporary output root")
    selected = [
        component_id
        for component_id in component_ids
        if _hermes_renderable_manifest(component_id, entries[component_id], policy) is not None
    ]
    if not selected:
        return []
    rendered: list[tuple[Path, str, bool, int | None]] = []
    component_skill_roots: list[tuple[str, Path]] = []

    for component_id in selected:
        item = _render_hermes_skill(
            component_id=component_id,
            registry_entry=entries[component_id],
            adapter=adapter,
            policy=policy,
        )
        rendered.append(item)
        component_skill_roots.append((component_id, item[0].parent))

    for profile, profile_components in profile_exports.items():
        if not set(profile_components) <= component_set:
            raise ValueError(
                f"Hermes profile plan must stay within selected component closure: {profile}"
            )
        for component_id in profile_components:
            registry_entry = entries.get(component_id)
            if registry_entry is None:
                raise ValueError(
                    f"Missing registry entry for Hermes profile component: {component_id}"
                )
            if _hermes_renderable_manifest(component_id, registry_entry, policy) is None:
                raise ValueError(f"Hermes profile component is not renderable: {component_id}")
            item = _render_hermes_skill(
                component_id=component_id,
                registry_entry=registry_entry,
                adapter=adapter,
                policy=policy,
                profile=profile,
                output_root=profile_output_root,
            )
            rendered.append(item)
            component_skill_roots.append((component_id, item[0].parent))

    rendered.extend(_render_hermes_support_files(component_skill_roots, adapter, policy))
    if profile_output_root is not None:
        rendered.append(
            (
                profile_output_root / HERMES_PROFILE_OUTPUT_MARKER,
                HERMES_PROFILE_OUTPUT_MARKER_CONTENT,
                False,
                None,
            )
        )
    rendered.append(_render_hermes_install_note(adapter, policy))
    return rendered


def _hermes_external_package_policy(policy: dict[str, Any]) -> dict[str, Any]:
    package = policy.get("external_package")
    if not isinstance(package, dict):
        raise ValueError("adapters/hermes/adapter.yml: hermes.external_package must be a mapping")
    components = package.get("components")
    if not isinstance(components, list) or not components or not all(
        isinstance(component_id, str) for component_id in components
    ):
        raise ValueError("Hermes external package components must be a non-empty string list")
    if len(components) != len(set(components)):
        raise ValueError("Hermes external package components must be unique")
    if set(HERMES_EXTERNAL_PACKAGE_ALLOWED_EXCLUSIONS) - set(components):
        raise ValueError("Hermes external package must explicitly include its allowed exclusions")
    if not isinstance(package.get("output_root"), str) or not package["output_root"]:
        raise ValueError("Hermes external package output_root is required")
    if not isinstance(package.get("marker_file"), str) or "/" in package["marker_file"]:
        raise ValueError("Hermes external package marker_file must be a file name")
    return package


def _hermes_external_package_root(package: dict[str, Any]) -> Path:
    rel_root = _safe_relative_path(
        package["output_root"],
        field_name="hermes.external_package.output_root",
        rel_manifest="adapters/hermes/adapter.yml",
    )
    if rel_root.parts[:2] != ("dist", HERMES_TARGET):
        raise ValueError("Hermes external package output must stay under dist/hermes")
    return REPO_ROOT / Path(*rel_root.parts)


def _hermes_external_package_path(
    output_root: Path, raw_path: Any, *, field_name: str
) -> Path:
    rel_path = _safe_relative_path(
        raw_path,
        field_name=field_name,
        rel_manifest="adapters/hermes/adapter.yml",
    )
    return output_root / Path(*rel_path.parts)


def _render_hermes_external_package_outputs() -> list[tuple[Path, str, bool, int | None]]:
    adapter = _hermes_adapter()
    policy = _hermes_policy(adapter)
    package = _hermes_external_package_policy(policy)
    component_ids = list(package["components"])
    entries = _selected_registry_entries(component_ids)
    output_root = _hermes_external_package_root(package)
    rendered: list[tuple[Path, str, bool, int | None]] = []
    skill_roots: list[tuple[str, Path]] = []
    for component_id in component_ids:
        item = _render_hermes_skill(
            component_id=component_id,
            registry_entry=entries[component_id],
            adapter=adapter,
            policy=policy,
            output_root=output_root,
            allowed_excluded_components=HERMES_EXTERNAL_PACKAGE_ALLOWED_EXCLUSIONS,
        )
        rendered.append(item)
        skill_roots.append((component_id, item[0].parent))
    rendered.extend(_render_hermes_support_files(skill_roots, adapter, policy))

    hooks = package.get("hook_outputs")
    if not isinstance(hooks, dict):
        raise ValueError("Hermes external package hook_outputs must be a mapping")
    required_hook_fields = (
        "config_output",
        "wrapper_output",
        "hook_asset_output",
        "event",
        "command",
        "timeout",
    )
    if any(not hooks.get(field) for field in required_hook_fields):
        raise ValueError("Hermes external package hook_outputs is incomplete")
    config_path = _hermes_external_package_path(
        output_root, hooks["config_output"], field_name="hermes.external_package.hook_outputs.config_output"
    )
    wrapper_path = _hermes_external_package_path(
        output_root, hooks["wrapper_output"], field_name="hermes.external_package.hook_outputs.wrapper_output"
    )
    asset_path = _hermes_external_package_path(
        output_root, hooks["hook_asset_output"], field_name="hermes.external_package.hook_outputs.hook_asset_output"
    )
    hook_source = _safe_bundle_source(
        "components/hooks/optimal-response-prompt-submit/stop-prompt-submit.cjs",
        component_id=CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT,
        rel_manifest="adapters/hermes/adapter.yml",
    )
    rendered.extend(
        [
            (
                config_path,
                render_hook_manifest(
                    event=str(hooks["event"]),
                    command=str(hooks["command"]),
                    timeout=int(hooks["timeout"]),
                ),
                False,
                None,
            ),
            (wrapper_path, optimal_response_wrapper_content(), False, 0o755),
            (asset_path, hook_source.read_text(encoding="utf-8").rstrip() + "\n", False, None),
            (
                _hermes_external_package_path(
                    output_root,
                    package["marker_file"],
                    field_name="hermes.external_package.marker_file",
                ),
                render_external_package_manifest(
                    adapter=adapter,
                    package=package,
                    component_ids=component_ids,
                ),
                False,
                None,
            ),
        ]
    )
    return rendered


def _collect_outputs(
    component_ids: list[str],
    *,
    explicit_ids: set[str] | None = None,
    target_ids: set[str] | None = None,
    include_hermes: bool = False,
    hermes_profile_exports: dict[str, list[str]] | None = None,
    hermes_profile_output_root: Path | None = None,
) -> dict[Path, dict[str, Any]]:
    entries = _selected_registry_entries(component_ids)
    expected_outputs: dict[Path, dict[str, Any]] = {}

    for component_id, entry in entries.items():
        explicit = explicit_ids is None or component_id in explicit_ids
        for item in _render_component(
            component_id,
            entry or {},
            explicit=explicit,
            target_ids=target_ids,
        ):
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
                    existing["content"] = _combined_append_content(
                        output_path,
                        existing["chunks"],
                    )
                    existing["append"] = existing["append"] and append
                continue
            if output_path in expected_outputs:
                raise ValueError(f"Duplicate adapter output: {output_path}")
            expected_outputs[output_path] = {"content": content, "append": False, "mode": mode}
    if include_hermes:
        for item in _render_hermes_outputs(
            component_ids,
            profile_exports=hermes_profile_exports,
            profile_output_root=hermes_profile_output_root,
        ):
            output_path, content, append, mode = _unpack_rendered_output(item)
            if output_path in expected_outputs:
                raise ValueError(f"Duplicate adapter output: {output_path}")
            expected_outputs[output_path] = {"content": content, "append": append, "mode": mode}
    return expected_outputs


def _dist_target_for_output_path(output_path: Path) -> str | None:
    try:
        rel_parts = output_path.relative_to(REPO_ROOT).parts
    except ValueError:
        return None
    if len(rel_parts) >= 2 and rel_parts[0] == "dist":
        return rel_parts[1]
    return None


def _shared_hook_output_paths() -> set[Path]:
    output_paths: set[Path] = set()
    for adapter_path in ADAPTERS_DIR.glob("*/adapter.yml"):
        adapter = _load_yaml(adapter_path)
        hooks = (adapter.get("structures") or {}).get("hooks")
        output_root = adapter.get("output_root")
        if not isinstance(hooks, dict) or not isinstance(output_root, str):
            continue
        path_template = hooks.get("path_template")
        if not isinstance(path_template, str) or "{" in path_template:
            continue
        output_paths.add(REPO_ROOT / output_root / path_template)
    return output_paths


def _cleanup_unexpected_shared_hook_outputs(
    expected_outputs: dict[Path, dict[str, Any]], *, target_ids: set[str] | None = None
) -> None:
    for output_path in sorted(_shared_hook_output_paths()):
        output_target = _dist_target_for_output_path(output_path)
        if target_ids is not None and output_target not in target_ids:
            continue
        if output_path in expected_outputs or not output_path.is_file():
            continue
        output_path.unlink()
        parent = output_path.parent
        while parent != REPO_ROOT and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def _cleanup_stale_project_context_outputs(
    expected_outputs: dict[Path, dict[str, Any]], *, target_ids: set[str] | None = None
) -> None:
    if target_ids is not None and "project" not in target_ids:
        return
    for rel_output in STALE_PROJECT_CONTEXT_OUTPUTS:
        output_path = REPO_ROOT / rel_output
        if output_path in expected_outputs or not output_path.is_file():
            continue
        output_path.unlink()


def _profile_target_ids(profile_ids: list[str]) -> list[str]:
    target_ids: list[str] = []
    for profile_id in profile_ids:
        profile_path = _profile_path(profile_id)
        if not profile_path.is_file():
            raise FileNotFoundError(f"Missing profile: {profile_id}")
        profile = _load_yaml(profile_path)
        targets = profile.get("targets") or []
        if not isinstance(targets, list):
            raise ValueError(f"{profile_path.relative_to(REPO_ROOT)}: targets must be a list")
        for target in targets:
            if not isinstance(target, str):
                raise ValueError(
                    f"{profile_path.relative_to(REPO_ROOT)}: target ids must be strings"
                )
            target_ids.append(target)
    return _dedupe_preserving_order(target_ids)


def _hermes_enabled(
    *,
    profile_target_ids: list[str],
    target_ids: set[str] | None,
) -> bool:
    if target_ids is not None:
        return HERMES_TARGET in target_ids
    return HERMES_TARGET in profile_target_ids


def _validate_hermes_skill_output(
    path: Path,
    content: str,
    *,
    max_length: int,
    output_root: Path,
    allowed_excluded_components: set[str] | None = None,
) -> None:
    rel_path = _display_path(path, relative_to=output_root)
    if len(content) > max_length:
        raise ValueError(f"{rel_path}: Hermes skill exceeds max length {max_length}")
    if not content.startswith("---\n"):
        raise ValueError(f"{rel_path}: Hermes skill must start with YAML frontmatter")
    lines = content.splitlines()
    try:
        closing_index = next(index for index in range(1, len(lines)) if lines[index] == "---")
    except StopIteration as exc:
        raise ValueError(f"{rel_path}: Hermes skill frontmatter is not closed") from exc
    frontmatter = yaml.safe_load("\n".join(lines[1:closing_index]))
    if not isinstance(frontmatter, dict):
        raise ValueError(f"{rel_path}: Hermes frontmatter must parse as a mapping")
    required = {"name", "description", "version", "author", "license", "metadata"}
    missing = sorted(required - set(frontmatter))
    if missing:
        raise ValueError(f"{rel_path}: Hermes frontmatter missing fields: {missing}")
    name = frontmatter["name"]
    if not isinstance(name, str) or not HERMES_SKILL_NAME_RE.fullmatch(name):
        raise ValueError(f"{rel_path}: invalid Hermes skill name: {name!r}")
    description = frontmatter["description"]
    if not isinstance(description, str) or not description or len(description) > 60:
        raise ValueError(f"{rel_path}: Hermes description must be 1-60 chars")
    if "\n" in description or description.count(".") + description.count("!") + description.count("?") > 1:
        raise ValueError(f"{rel_path}: Hermes description must be one sentence")
    if "platforms" in frontmatter:
        raise ValueError(f"{rel_path}: portable Hermes MVP skills must omit platforms")
    metadata = frontmatter["metadata"]
    if not isinstance(metadata, dict):
        raise ValueError(f"{rel_path}: metadata mapping required")
    source_component_id = metadata.get("source_component_id")
    if not isinstance(source_component_id, str) or not source_component_id.startswith(
        "harnesskit.skill."
    ):
        raise ValueError(f"{rel_path}: metadata.source_component_id skill id required")
    if (
        source_component_id in _hermes_excluded_components(_hermes_policy(_hermes_adapter()))
        and source_component_id not in (allowed_excluded_components or set())
    ):
        raise ValueError(f"{rel_path}: excluded Hermes component rendered: {source_component_id}")
    hermes = metadata.get("hermes") if isinstance(metadata, dict) else None
    if not isinstance(hermes, dict):
        raise ValueError(f"{rel_path}: metadata.hermes mapping required")
    if not isinstance(hermes.get("tags"), list):
        raise ValueError(f"{rel_path}: metadata.hermes.tags list required")
    if not isinstance(hermes.get("related_skills"), list):
        raise ValueError(f"{rel_path}: metadata.hermes.related_skills list required")
    body = "\n".join(lines[closing_index + 1 :]).strip()
    if not body:
        raise ValueError(f"{rel_path}: Hermes skill body must be non-empty")
    ownership_notice = _hermes_policy(_hermes_adapter())["ownership_notice"]
    if str(ownership_notice) not in body:
        raise ValueError(f"{rel_path}: Hermes ownership notice missing")


def _hermes_skill_path_info(
    path: Path,
    policy: dict[str, Any],
    *,
    output_root: Path,
    profile_scoped: bool,
) -> tuple[str | None, str, str]:
    rel_parts = path.relative_to(output_root).parts
    category = str(policy.get("category") or "software-development")
    if not profile_scoped and len(rel_parts) == 4 and rel_parts[:2] == ("skills", category):
        return None, rel_parts[1], rel_parts[2]
    if profile_scoped and len(rel_parts) == 6 and rel_parts[:1] == ("profiles",):
        if rel_parts[2:4] == ("skills", category):
            return rel_parts[1], rel_parts[3], rel_parts[4]
    raise ValueError(
        f"{_display_path(path, relative_to=output_root)}: Hermes SKILL.md path is invalid"
    )


def _hermes_support_dir_for_path(
    path: Path,
    policy: dict[str, Any],
    *,
    output_root: Path,
    profile_scoped: bool,
) -> str | None:
    rel_parts = path.relative_to(output_root).parts
    category = str(policy.get("category") or "software-development")
    if not profile_scoped and len(rel_parts) > 4 and rel_parts[:2] == ("skills", category):
        return rel_parts[3]
    if profile_scoped and len(rel_parts) > 6 and rel_parts[:1] == ("profiles",):
        if rel_parts[2:4] == ("skills", category):
            return rel_parts[5]
    return None


def _validate_hermes_output_family(
    outputs: dict[Path, dict[str, Any]],
    *,
    output_root: Path,
    profile_scoped: bool,
) -> None:
    if not outputs:
        return
    adapter = _hermes_adapter()
    policy = _hermes_policy(adapter)
    max_length = int(policy.get("max_skill_content_length") or 50000)
    skill_names: set[str] = set()
    marker_path = output_root / HERMES_PROFILE_OUTPUT_MARKER
    if profile_scoped:
        marker = outputs.get(marker_path)
        if marker is None or marker.get("content") != HERMES_PROFILE_OUTPUT_MARKER_CONTENT:
            raise ValueError("Hermes temporary profile output ownership marker is required")

    for path, data in outputs.items():
        try:
            rel_path = path.relative_to(output_root)
        except ValueError as exc:
            raise ValueError(f"Hermes output outside allowed root: {path}") from exc
        if profile_scoped and path == marker_path:
            continue
        if profile_scoped and rel_path.parts[:1] != ("profiles",):
            raise ValueError(f"Hermes temporary profile output path is invalid: {rel_path}")
        if not profile_scoped and rel_path.parts[:1] == ("profiles",):
            raise ValueError("Repository-owned Hermes output must not contain profiles")
        content = str(data["content"])
        for prohibited in HERMES_PROHIBITED_DESTINATION_STRINGS:
            if prohibited in content or prohibited in rel_path.as_posix():
                raise ValueError(
                    f"{rel_path.as_posix()}: prohibited Hermes destination string: {prohibited}"
                )
        if path.name != "SKILL.md":
            continue
        _validate_hermes_skill_output(
            path,
            content,
            max_length=max_length,
            output_root=output_root,
        )
        _profile, _category, skill_name = _hermes_skill_path_info(
            path,
            policy,
            output_root=output_root,
            profile_scoped=profile_scoped,
        )
        skill_names.add(skill_name)
    for path in outputs:
        if profile_scoped and path == marker_path:
            continue
        support_dir = _hermes_support_dir_for_path(
            path,
            policy,
            output_root=output_root,
            profile_scoped=profile_scoped,
        )
        if support_dir is not None and path.name != "SKILL.md":
            if support_dir not in HERMES_ALLOWED_SUPPORT_DIRS:
                raise ValueError(
                    f"{_display_path(path, relative_to=output_root)}: "
                    "unsupported Hermes support dir"
                )
    excluded_names = {
        _hermes_slug(component_id)
        for component_id in _hermes_excluded_components(policy)
    }
    leaked = skill_names & excluded_names
    if leaked:
        raise ValueError(f"Hermes output contains excluded skills: {sorted(leaked)}")


def _validate_hermes_outputs(expected_outputs: dict[Path, dict[str, Any]]) -> None:
    output_root = REPO_ROOT / str(_hermes_adapter()["output_root"])
    external_root = _hermes_external_package_root(
        _hermes_external_package_policy(_hermes_policy(_hermes_adapter()))
    )
    hermes_outputs = {
        path: data
        for path, data in expected_outputs.items()
        if path.is_relative_to(output_root) and not path.is_relative_to(external_root)
    }
    _validate_hermes_output_family(
        hermes_outputs,
        output_root=output_root,
        profile_scoped=False,
    )


def _validate_hermes_profile_outputs(
    expected_outputs: dict[Path, dict[str, Any]],
    *,
    output_root: Path,
) -> None:
    profile_outputs = {
        path: data for path, data in expected_outputs.items() if path.is_relative_to(output_root)
    }
    _validate_hermes_output_family(
        profile_outputs,
        output_root=output_root,
        profile_scoped=True,
    )


def _validate_hermes_external_package_outputs(
    expected_outputs: dict[Path, dict[str, Any]],
) -> None:
    adapter = _hermes_adapter()
    policy = _hermes_policy(adapter)
    package = _hermes_external_package_policy(policy)
    output_root = _hermes_external_package_root(package)
    outputs = {
        path: data for path, data in expected_outputs.items() if path.is_relative_to(output_root)
    }
    marker_path = _hermes_external_package_path(
        output_root,
        package["marker_file"],
        field_name="hermes.external_package.marker_file",
    )
    marker = outputs.get(marker_path)
    expected_ids = list(package["components"])
    if marker is None or marker["content"] != render_external_package_manifest(
        adapter=adapter, package=package, component_ids=expected_ids
    ):
        raise ValueError("Hermes external package ownership marker is required")

    actual_ids: list[str] = []
    for path, data in outputs.items():
        rel_path = path.relative_to(output_root)
        if path == marker_path:
            continue
        if path.name == "SKILL.md":
            _validate_hermes_skill_output(
                path,
                str(data["content"]),
                max_length=int(policy.get("max_skill_content_length") or 50000),
                output_root=output_root,
                allowed_excluded_components=HERMES_EXTERNAL_PACKAGE_ALLOWED_EXCLUSIONS,
            )
            frontmatter = yaml.safe_load(str(data["content"]).split("---", 2)[1])
            actual_ids.append(frontmatter["metadata"]["source_component_id"])
            continue
        allowed = {
            "config.yaml",
            "hooks/optimal-response/optimal-response-pre-llm.cjs",
            "hooks/optimal-response/stop-prompt-submit.cjs",
            "skills/software-development/optimal-response/references/rich-workflow.md",
            "skills/software-development/worktree-lifecycle/references/worktree-lifecycle-policy.md",
            "skills/software-development/worktree-lifecycle/references/worktree-ledger.schema.json",
        }
        if rel_path.as_posix() not in allowed:
            raise ValueError(f"Hermes external package output is not allowed: {rel_path}")
    if actual_ids != expected_ids:
        raise ValueError("Hermes external package skill catalog must match exactly")
    if len(actual_ids) != 25:
        raise ValueError("Hermes external package must contain exactly 25 skills")


def _prepare_hermes_profile_output_root(output_root: Path) -> None:
    if output_root.is_symlink():
        raise ValueError("Hermes profile output root must not be a symlink")
    if not output_root.exists():
        return
    marker_path = output_root / HERMES_PROFILE_OUTPUT_MARKER
    if (
        not output_root.is_dir()
        or marker_path.is_symlink()
        or not marker_path.is_file()
        or marker_path.read_text(encoding="utf-8") != HERMES_PROFILE_OUTPUT_MARKER_CONTENT
    ):
        raise ValueError(
            "Hermes profile output ownership marker is missing; refusing to delete"
        )
    shutil.rmtree(output_root)


def _initialize_hermes_profile_output_root(output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=False)
    marker_path = output_root / HERMES_PROFILE_OUTPUT_MARKER
    try:
        marker_path.write_text(HERMES_PROFILE_OUTPUT_MARKER_CONTENT, encoding="utf-8")
    except BaseException:
        try:
            output_root.rmdir()
        except OSError:
            pass
        raise


def _unexpected_output_paths(output_root: Path, expected_paths: set[Path]) -> list[Path]:
    if not output_root.is_dir():
        return []
    return sorted(
        path
        for path in output_root.rglob("*")
        if (path.is_file() or path.is_symlink()) and path not in expected_paths
    )


def _has_symlink_at_or_below(path: Path, *, output_root: Path) -> bool:
    current = output_root
    for part in path.relative_to(output_root).parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _owned_hermes_external_package_marker(
    marker_path: Path, *, adapter: dict[str, Any], package: dict[str, Any]
) -> bool:
    if marker_path.is_symlink() or not marker_path.is_file():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(marker, dict):
        return False
    catalog = marker.get("catalog")
    return (
        marker.get("format") == package["marker_format"]
        and marker.get("version") == package["marker_version"]
        and marker.get("ownership") == "harnesskit-managed-external-package"
        and marker.get("adapter_id") == adapter["adapter_id"]
        and isinstance(catalog, list)
        and bool(catalog)
        and all(isinstance(component_id, str) for component_id in catalog)
        and len(catalog) == len(set(catalog))
    )


def _prepare_hermes_external_package_root(
    output_root: Path, *, adapter: dict[str, Any], package: dict[str, Any]
) -> None:
    if output_root.is_symlink():
        raise ValueError("Hermes external package root must not be a symlink")
    if not output_root.exists():
        return
    marker_path = _hermes_external_package_path(
        output_root,
        _hermes_external_package_policy(_hermes_policy(_hermes_adapter()))["marker_file"],
        field_name="hermes.external_package.marker_file",
    )
    if not output_root.is_dir() or not _owned_hermes_external_package_marker(
        marker_path, adapter=adapter, package=package
    ):
        raise ValueError(
            "Hermes external package ownership marker is missing; refusing to delete"
        )
    shutil.rmtree(output_root)


def _clean_generic_hermes_output_root(output_root: Path) -> None:
    """Remove only generic Hermes output, preserving the dedicated package family."""
    if output_root.is_symlink():
        raise ValueError("Hermes generic output root must not be a symlink")
    external_root = _hermes_external_package_root(
        _hermes_external_package_policy(_hermes_policy(_hermes_adapter()))
    )
    if not output_root.is_dir():
        return
    for child in output_root.iterdir():
        if child == external_root:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def build(
    component_ids: list[str],
    *,
    profile_ids: list[str] | None = None,
    target_ids: list[str] | None = None,
    hermes_profile_plan: Path | None = None,
    hermes_external_package: bool = False,
    check: bool,
) -> int:
    profile_ids = profile_ids or []
    if hermes_external_package and (component_ids or profile_ids or target_ids or hermes_profile_plan):
        raise ValueError("--hermes-external-package is independent and accepts no component, profile, target, or plan")
    profile_target_ids = _profile_target_ids(profile_ids)
    target_filter = set(target_ids) if target_ids else None
    selected_component_ids = _dedupe_preserving_order(
        [*component_ids, *_component_ids_from_profiles(profile_ids)]
    )
    mismatches: list[str] = []
    include_hermes = _hermes_enabled(
        profile_target_ids=profile_target_ids,
        target_ids=target_filter,
    )
    hermes_profile_exports: dict[str, list[str]] | None = None
    hermes_profile_output_root: Path | None = None
    if hermes_profile_plan is not None:
        if not include_hermes:
            raise ValueError("--hermes-profile-plan requires the Hermes target")
        hermes_profile_exports, hermes_profile_output_root = _load_hermes_profile_plan(
            hermes_profile_plan,
            selected_component_ids=selected_component_ids,
            policy=_hermes_policy(_hermes_adapter()),
        )
    expected_outputs = _collect_outputs(
        selected_component_ids,
        explicit_ids=set(component_ids),
        target_ids=target_filter,
        include_hermes=include_hermes,
        hermes_profile_exports=hermes_profile_exports,
        hermes_profile_output_root=hermes_profile_output_root,
    )
    hermes_external_output_root: Path | None = None
    if hermes_external_package:
        package = _hermes_external_package_policy(_hermes_policy(_hermes_adapter()))
        hermes_external_output_root = _hermes_external_package_root(package)
        for item in _render_hermes_external_package_outputs():
            output_path, content, append, mode = _unpack_rendered_output(item)
            if append or output_path in expected_outputs:
                raise ValueError(f"Duplicate external Hermes adapter output: {output_path}")
            expected_outputs[output_path] = {"content": content, "append": False, "mode": mode}
    _validate_hermes_outputs(expected_outputs)
    if hermes_external_package:
        _validate_hermes_external_package_outputs(expected_outputs)
    if hermes_profile_output_root is not None:
        _validate_hermes_profile_outputs(
            expected_outputs,
            output_root=hermes_profile_output_root,
        )

    if not check:
        if hermes_external_output_root is not None:
            adapter = _hermes_adapter()
            package = _hermes_external_package_policy(_hermes_policy(adapter))
            _prepare_hermes_external_package_root(
                hermes_external_output_root,
                adapter=adapter,
                package=package,
            )
        if hermes_profile_output_root is not None:
            _prepare_hermes_profile_output_root(hermes_profile_output_root)
            _initialize_hermes_profile_output_root(hermes_profile_output_root)
        if include_hermes:
            hermes_output_root = REPO_ROOT / str(_hermes_adapter()["output_root"])
            _clean_generic_hermes_output_root(hermes_output_root)
        if not hermes_external_package:
            _cleanup_unexpected_shared_hook_outputs(expected_outputs, target_ids=target_filter)
            _cleanup_stale_project_context_outputs(expected_outputs, target_ids=target_filter)
        cleanup_dirs = {
            output_path.parent
            for output_path in expected_outputs
            if output_path.name == "SKILL.md" and "skills" in output_path.parts
        }
        for cleanup_dir in sorted(cleanup_dirs):
            if cleanup_dir.is_dir():
                shutil.rmtree(cleanup_dir)

    for output_path, expected in expected_outputs.items():
        append = expected["append"]
        content = (
            _combined_append_content(output_path, expected.get("chunks", [expected["content"]]))
            if append
            else expected["content"]
        )
        mode = expected.get("mode")
        if check:
            if (
                hermes_profile_output_root is not None
                and output_path.is_relative_to(hermes_profile_output_root)
                and _has_symlink_at_or_below(
                    output_path,
                    output_root=hermes_profile_output_root,
                )
            ):
                mismatches.append(
                    "symlink: "
                    f"{_display_path(output_path, relative_to=hermes_profile_output_root)}"
                )
                continue
            if not output_path.exists():
                mismatches.append(f"missing: {_display_path(output_path)}")
                continue
            current = output_path.read_text(encoding="utf-8")
            if current != content:
                mismatches.append(f"stale: {_display_path(output_path)}")
            if mode is not None and (output_path.stat().st_mode & 0o777) != mode:
                mismatches.append(f"stale-mode: {_display_path(output_path)}")
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            if mode is not None:
                output_path.chmod(mode)

    if check and include_hermes:
        hermes_output_root = REPO_ROOT / str(_hermes_adapter()["output_root"])
        legacy_profiles_root = hermes_output_root / "profiles"
        if legacy_profiles_root.exists() or legacy_profiles_root.is_symlink():
            mismatches.append(f"unexpected: {_display_path(legacy_profiles_root)}")
    if check and hermes_profile_output_root is not None:
        expected_profile_paths = {
            path for path in expected_outputs if path.is_relative_to(hermes_profile_output_root)
        }
        for unexpected in _unexpected_output_paths(
            hermes_profile_output_root,
            expected_profile_paths,
        ):
            mismatches.append(f"unexpected: {_display_path(unexpected)}")
    if check and hermes_external_output_root is not None:
        expected_external_paths = {
            path for path in expected_outputs if path.is_relative_to(hermes_external_output_root)
        }
        for unexpected in _unexpected_output_paths(
            hermes_external_output_root,
            expected_external_paths,
        ):
            mismatches.append(f"unexpected: {_display_path(unexpected)}")

    if mismatches:
        print("Adapter outputs are not current:", file=sys.stderr)
        for mismatch in mismatches:
            print(f"- {mismatch}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build HarnessKit adapter outputs.")
    parser.add_argument("--component", action="append", default=[], help="Component id to build")
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        help="Profile name or id whose components should be built",
    )
    parser.add_argument("--check", action="store_true", help="Check outputs without writing files")
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Limit generated outputs to a target adapter id (repeatable)",
    )
    parser.add_argument(
        "--hermes-profile-plan",
        type=Path,
        help="Repository-external temporary Hermes profile export plan",
    )
    parser.add_argument(
        "--hermes-external-package",
        action="store_true",
        help="Build the adapter-owned Hermes external package without profile selection",
    )
    args = parser.parse_args(argv)

    if not args.component and not args.profile and not args.hermes_external_package:
        parser.error("pass --component, --profile, or --hermes-external-package")

    return build(
        args.component,
        profile_ids=args.profile,
        target_ids=args.target,
        hermes_profile_plan=args.hermes_profile_plan,
        hermes_external_package=args.hermes_external_package,
        check=args.check,
    )


if __name__ == "__main__":
    raise SystemExit(main())
