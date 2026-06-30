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
    'OPTIMAL_RESPONSE_FLAG="${CODEX_HOME:-$HOME/.codex}/.optimal-response-disabled" '
    'OPTIMAL_RESPONSE_MODE="${CODEX_HOME:-$HOME/.codex}/.optimal-response-mode" '
    'OPTIMAL_RESPONSE_STATE_DIR="${CODEX_HOME:-$HOME/.codex}/optimal-response-out/state" '
    'OPTIMAL_RESPONSE_SURFACE=codex '
    'node "${CODEX_HOME:-$HOME/.codex}/skills/optimal-response/hooks/stop-prompt-submit.cjs"'
)


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML mapping expected: {path}")
    return data


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


def _skills_yaml(target_options: dict[str, Any]) -> str:
    skills = target_options.get("skills") or []
    return yaml.safe_dump(skills, default_flow_style=True, sort_keys=False).strip()


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

    return {
        "target_name": _target_name(manifest["kind"], output_path),
        "component_id": _target_name(manifest["kind"], output_path),
        "canonical_component_id": manifest["component_id"],
        "description": _description(manifest, target_options),
        "description_toml": json.dumps(_description(manifest, target_options)),
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
    component_ids: list[str] = []
    for profile_id in profile_ids:
        profile_path = _profile_path(profile_id)
        if not profile_path.is_file():
            raise FileNotFoundError(f"Missing profile: {profile_id}")
        profile = _load_yaml(profile_path)
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


def _render_component(component_id: str, registry_entry: dict[str, Any], *, explicit: bool = True) -> list[tuple[Path, str, bool, int | None]]:
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
                content = _hook_registration_content(manifest, target, target_options)
            else:
                template = template_path.read_text(encoding="utf-8")
                content = _render_template(template, context)
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
                source_path.read_text(encoding="utf-8").rstrip() + "\n",
                False,
                mode,
            )
        )
    if component_id == CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT:
        rendered.append(
            (
                _safe_bundle_output(CODEX_USER_HOOK_OUTPUT, rel_manifest=rel_manifest),
                _codex_user_prompt_submit_registration_content(),
                False,
                None,
            )
        )
    return rendered


def _collect_outputs(
    component_ids: list[str], *, explicit_ids: set[str] | None = None
) -> dict[Path, dict[str, Any]]:
    entries = _selected_registry_entries(component_ids)
    expected_outputs: dict[Path, dict[str, Any]] = {}

    for component_id, entry in entries.items():
        explicit = explicit_ids is None or component_id in explicit_ids
        for item in _render_component(component_id, entry or {}, explicit=explicit):
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
    return expected_outputs


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


def _cleanup_unexpected_shared_hook_outputs(expected_outputs: dict[Path, dict[str, Any]]) -> None:
    for output_path in sorted(_shared_hook_output_paths()):
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


def _cleanup_stale_project_context_outputs(expected_outputs: dict[Path, dict[str, Any]]) -> None:
    for rel_output in STALE_PROJECT_CONTEXT_OUTPUTS:
        output_path = REPO_ROOT / rel_output
        if output_path in expected_outputs or not output_path.is_file():
            continue
        output_path.unlink()


def build(component_ids: list[str], *, profile_ids: list[str] | None = None, check: bool) -> int:
    selected_component_ids = _dedupe_preserving_order(
        [*component_ids, *_component_ids_from_profiles(profile_ids or [])]
    )
    mismatches: list[str] = []
    expected_outputs = _collect_outputs(selected_component_ids, explicit_ids=set(component_ids))

    if not check:
        _cleanup_unexpected_shared_hook_outputs(expected_outputs)
        _cleanup_stale_project_context_outputs(expected_outputs)
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
            if not output_path.exists():
                mismatches.append(f"missing: {output_path.relative_to(REPO_ROOT)}")
                continue
            current = output_path.read_text(encoding="utf-8")
            if current != content:
                mismatches.append(f"stale: {output_path.relative_to(REPO_ROOT)}")
            if mode is not None and (output_path.stat().st_mode & 0o777) != mode:
                mismatches.append(f"stale-mode: {output_path.relative_to(REPO_ROOT)}")
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            if mode is not None:
                output_path.chmod(mode)

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
    args = parser.parse_args(argv)

    if not args.component and not args.profile:
        parser.error("pass --component or --profile at least once")

    return build(args.component, profile_ids=args.profile, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
