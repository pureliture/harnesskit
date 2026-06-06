from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
JSON_DEEP_MERGE_KEYS = {"claude-settings-hooks", "codex-hooks"}
JSON_DEEP_MERGE_DESTINATION_KEYS = {
    ("claude", PurePosixPath(".claude/settings.json")): "claude-settings-hooks",
    ("codex", PurePosixPath(".codex/hooks.json")): "codex-hooks",
}
SOURCE_DESTINATION_EQUIVALENTS = {
    (
        "codex",
        PurePosixPath(".codex/hooks.json"),
    ): {PurePosixPath("dist/codex/.codex/hooks.user.json")},
}


def load_plan(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Install plan must be a mapping: {path}")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError(f"Install plan must contain artifacts list: {path}")
    validate_plan_contract(data)
    return data


def validate_plan_contract(plan: dict[str, Any]) -> None:
    targets = plan.get("targets")
    components = plan.get("components")
    surfaces = plan.get("runtime_surfaces")
    if not isinstance(targets, list) or not all(
        isinstance(target, str) for target in targets
    ):
        raise ValueError("Install plan targets must be a list of strings")
    if not isinstance(components, list) or not all(
        isinstance(component_id, str) for component_id in components
    ):
        raise ValueError("Install plan components must be a list of strings")
    if not isinstance(surfaces, list):
        raise ValueError("Install plan runtime_surfaces must be a list")

    surface_paths_by_target: dict[str, list[PurePosixPath]] = {}
    for surface in surfaces:
        if not isinstance(surface, dict):
            raise ValueError(f"Runtime surface must be a mapping: {surface}")
        target = surface.get("target")
        if not isinstance(target, str) or target not in targets:
            raise ValueError(f"Runtime surface target is not selected: {target}")
        surface_path = _relative_posix_path(surface.get("path"), "Runtime surface path")
        _relative_posix_path(surface.get("source"), "Runtime surface source")
        surface_paths_by_target.setdefault(target, []).append(surface_path)

    for artifact in plan["artifacts"]:
        if not isinstance(artifact, dict):
            raise ValueError(f"Artifact must be a mapping: {artifact}")
        target = artifact.get("target")
        component_id = artifact.get("component_id")
        if not isinstance(target, str) or target not in targets:
            raise ValueError(f"Artifact target is not selected: {target}")
        if not isinstance(component_id, str) or component_id not in components:
            raise ValueError(f"Artifact component is not selected: {component_id}")
        component_ids = artifact.get("component_ids")
        if component_ids is not None:
            if not isinstance(component_ids, list) or not all(
                isinstance(item, str) and item in components for item in component_ids
            ):
                raise ValueError(f"Artifact component_ids must be selected components: {component_ids}")
            if component_id not in component_ids:
                raise ValueError("Artifact component_id must be included in component_ids")

        source = _relative_posix_path(artifact.get("source"), "Artifact source")
        destination = _relative_posix_path(
            artifact.get("destination"),
            "Artifact destination",
        )
        merge_strategy = artifact.get("merge_strategy")
        if merge_strategy is not None:
            if merge_strategy not in {"managed-block", "json-deep-merge"}:
                raise ValueError(f"unsupported merge strategy: {merge_strategy}")
            if merge_strategy == "managed-block":
                _non_empty_string(artifact.get("begin_marker"), "Artifact begin_marker")
                _non_empty_string(artifact.get("end_marker"), "Artifact end_marker")
            if merge_strategy == "json-deep-merge":
                if destination.suffix != ".json":
                    raise ValueError(
                        "json-deep-merge artifacts must target JSON destinations"
                    )
                json_merge_key = _non_empty_string(
                    artifact.get("json_merge_key"),
                    "Artifact json_merge_key",
                )
                if json_merge_key not in JSON_DEEP_MERGE_KEYS:
                    raise ValueError(f"unsupported json merge key: {json_merge_key}")
                expected_json_merge_key = JSON_DEEP_MERGE_DESTINATION_KEYS.get(
                    (target, destination)
                )
                if expected_json_merge_key is None:
                    raise ValueError(
                        "json-deep-merge destination is not supported: "
                        f"{target}:{destination}"
                    )
                if json_merge_key != expected_json_merge_key:
                    raise ValueError(
                        "json merge key does not match destination: "
                        f"{json_merge_key} != {expected_json_merge_key} "
                        f"for {target}:{destination}"
                    )
        expected_source = PurePosixPath("dist") / target / destination
        allowed_sources = SOURCE_DESTINATION_EQUIVALENTS.get((target, destination), set())
        if source != expected_source and source not in allowed_sources:
            raise ValueError(
                "artifact source must match target and destination: "
                f"{source} != {expected_source}"
            )
        if not any(
            _same_or_under(destination, surface_path)
            for surface_path in surface_paths_by_target.get(target, [])
        ):
            raise ValueError(
                "artifact destination is outside target runtime surfaces: "
                f"{target}:{destination}"
            )


def _relative_posix_path(raw: Any, field_name: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{field_name} must be a non-empty string")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_name} must stay inside its root: {raw}")
    return path


def _non_empty_string(raw: Any, field_name: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{field_name} must be a non-empty string")
    return raw


def _same_or_under(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


def source_path(artifact: dict[str, Any]) -> Path:
    raw = artifact.get("source")
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"Artifact source must be a non-empty string: {artifact}")
    path = (REPO_ROOT / raw).resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"source escapes repository root: {raw}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"missing source: {raw}")
    return path


def destination_path(target_root: Path, artifact: dict[str, Any]) -> Path:
    raw = artifact.get("destination")
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"Artifact destination must be a non-empty string: {artifact}")
    path = target_root / raw
    try:
        path.resolve().relative_to(target_root.resolve())
    except ValueError as exc:
        raise ValueError(f"destination escapes target root: {raw}") from exc
    return path
