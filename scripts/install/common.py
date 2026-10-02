from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
# Fixed module contract, independent of a caller's generated-output root.
_MANAGED_BLOCK_CONTRACTS = {
    (entry["target"], entry["destination"]): entry
    for entry in json.loads((REPO_ROOT / "schemas/install-target-contract-v1.json").read_text(encoding="utf-8"))["merge_destinations"]
    if entry["strategy"] == "managed-block"
}
JSON_DEEP_MERGE_KEYS = {"claude-settings-hooks", "codex-hooks"}
JSON_DEEP_MERGE_DESTINATION_KEYS = {
    ("claude", PurePosixPath(".claude/settings.json")): "claude-settings-hooks",
    ("codex", PurePosixPath(".codex/hooks.json")): "codex-hooks",
}
# TOML preserving-merge contract for codex `.codex/config.toml`. The harness owns
# ONLY the `[agents."<name>"]` registration tables it materializes; every other
# byte of the destination config is preserved. This mirrors the json-deep-merge
# key/destination guard so a mis-pointed toml merge can never be applied.
TOML_AGENTS_MERGE_KEYS = {"codex-agents"}
TOML_AGENTS_MERGE_DESTINATION_KEYS = {
    ("codex", PurePosixPath(".codex/config.toml")): "codex-agents",
}
# Retired registrations are an explicit, narrowly scoped migration surface.
# Do not turn this into a broad name-based user-agent cleanup list.
RETIRED_CODEX_AGENT_REGISTRATION_SIGNATURES = {
    ("system_architecture_manager", "agents/system_architecture_manager.toml"),
}
HERMES_EXTERNAL_PACKAGE_SOURCE = PurePosixPath("dist/hermes/external-package")
HERMES_EXTERNAL_SKILLS_SOURCE = HERMES_EXTERNAL_PACKAGE_SOURCE / "skills"
HERMES_EXTERNAL_HOOKS_SOURCE = HERMES_EXTERNAL_PACKAGE_SOURCE / "hooks/optimal-response"
HERMES_EXTERNAL_CONFIG_SOURCE = HERMES_EXTERNAL_PACKAGE_SOURCE / "config.yaml"
HERMES_EXTERNAL_PACKAGE_ROOT = PurePosixPath(".local/share/harnesskit/hermes/skills")
HERMES_EXTERNAL_HOOK_ROOT = PurePosixPath(".hermes/harnesskit/hooks/optimal-response")
HERMES_EXTERNAL_CONFIG = PurePosixPath(".hermes/config.yaml")
HERMES_EXTERNAL_MARKER = ".harnesskit-hermes-external-package.json"
HERMES_EXTERNAL_CONFIG_DIR = "~/.local/share/harnesskit/hermes/skills"
_HERMES_LEGACY_EXTERNAL_CONFIG_DIR_RE = re.compile(
    r"^/Users/[^/]+/\.local/share/harnesskit/hermes/skills$"
)
HERMES_EXTERNAL_HOOK_COMMAND = "~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs"
HERMES_EXTERNAL_HOOK_EVENT = "pre_llm_call"
SOURCE_DESTINATION_EQUIVALENTS = {
    (
        "codex",
        PurePosixPath(".codex/hooks.json"),
    ): {PurePosixPath("dist/codex/.codex/hooks.user.json")},
    (
        "hermes",
        PurePosixPath(".hermes/config.yaml"),
    ): {PurePosixPath("dist/hermes/config.yaml")},
    (
        "hermes",
        PurePosixPath(".hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs"),
    ): {
        PurePosixPath(
            "dist/hermes/hooks/optimal-response/optimal-response-pre-llm.cjs"
        )
    },
    (
        "hermes",
        PurePosixPath(".hermes/harnesskit/hooks/optimal-response/stop-prompt-submit.cjs"),
    ): {PurePosixPath("dist/hermes/hooks/optimal-response/stop-prompt-submit.cjs")},
}


def is_legacy_hermes_external_config_dir(value: object) -> bool:
    return isinstance(value, str) and bool(
        _HERMES_LEGACY_EXTERNAL_CONFIG_DIR_RE.fullmatch(value)
    )


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

    retired_registrations = retired_codex_agent_registrations(plan)
    if retired_registrations:
        if plan.get("scope") != "user":
            raise ValueError("retired Codex agent registrations require user scope")
        if "codex" not in targets:
            raise ValueError("retired Codex agent registrations require the codex target")

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

    normalized_artifacts: list[dict[str, Any]] = []
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
        # CLI plans never carry backend private-source authority. Ownership is
        # descriptive only; the embedded project merge contract remains required.
        expected_merge = _MANAGED_BLOCK_CONTRACTS.get((target, str(destination)))
        if expected_merge is not None:
            fields = {"strategy": "merge_strategy", "begin_marker": "begin_marker", "end_marker": "end_marker"}
            if any(artifact.get(plan_field) != expected_merge.get(contract_field)
                   for contract_field, plan_field in fields.items()):
                raise ValueError(f"merge contract mismatch: {target}:{destination}")
        if merge_strategy is not None:
            if merge_strategy not in {
                "managed-block",
                "json-deep-merge",
                "toml-agents-merge",
            }:
                raise ValueError(f"unsupported merge strategy: {merge_strategy}")
            if merge_strategy == "managed-block":
                _non_empty_string(artifact.get("begin_marker"), "Artifact begin_marker")
                _non_empty_string(artifact.get("end_marker"), "Artifact end_marker")
            if merge_strategy == "toml-agents-merge":
                toml_merge_key = _non_empty_string(
                    artifact.get("toml_merge_key"),
                    "Artifact toml_merge_key",
                )
                if toml_merge_key not in TOML_AGENTS_MERGE_KEYS:
                    raise ValueError(f"unsupported toml merge key: {toml_merge_key}")
                expected_toml_merge_key = TOML_AGENTS_MERGE_DESTINATION_KEYS.get(
                    (target, destination)
                )
                if expected_toml_merge_key is None:
                    raise ValueError(
                        "toml-agents-merge destination is not supported: "
                        f"{target}:{destination}"
                    )
                if toml_merge_key != expected_toml_merge_key:
                    raise ValueError(
                        "toml merge key does not match destination: "
                        f"{toml_merge_key} != {expected_toml_merge_key} "
                        f"for {target}:{destination}"
                    )
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
        normalized_artifacts.append(
            {
                "target": target,
                "component_id": component_id,
                "source": source,
                "destination": destination,
            }
        )

    validate_final_destination_fan_in(normalized_artifacts)
    if retired_registrations and not any(
        artifact["target"] == "codex"
        and artifact["destination"] == PurePosixPath(".codex/config.toml")
        for artifact in normalized_artifacts
    ):
        raise ValueError(
            "retired Codex agent registrations require the Codex config artifact"
        )
    external = plan.get("hermes_external_package")
    if external is not None:
        _validate_hermes_external_package(external, targets, components)


def retired_codex_agent_registrations(plan: dict[str, Any]) -> list[dict[str, str]]:
    raw = plan.get("retired_codex_agent_registrations")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("retired Codex agent registrations must be a list")

    registrations: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for registration in raw:
        if not isinstance(registration, dict) or set(registration) != {
            "name",
            "config_file",
        }:
            raise ValueError(
                "retired Codex agent registration must contain only name and config_file"
            )
        name = _non_empty_string(registration.get("name"), "retired Codex agent name")
        config_file = _non_empty_string(
            registration.get("config_file"),
            "retired Codex agent config_file",
        )
        signature = (name, config_file)
        if signature not in RETIRED_CODEX_AGENT_REGISTRATION_SIGNATURES:
            raise ValueError(f"unsupported retired Codex agent registration: {name}")
        if signature in seen:
            raise ValueError(f"duplicate retired Codex agent registration: {name}")
        seen.add(signature)
        registrations.append({"name": name, "config_file": config_file})
    return registrations


def _validate_hermes_external_package(external: Any, targets: list[str], components: list[str]) -> None:
    if not isinstance(external, dict) or targets != ["hermes"]:
        raise ValueError("Hermes external package requires only the hermes target")
    if external.get("lifecycle") not in {"install", "update", "cleanup", "uninstall"}:
        raise ValueError("unsupported Hermes external package lifecycle")
    expected = {
        "source": str(HERMES_EXTERNAL_PACKAGE_SOURCE),
        "skills_source": str(HERMES_EXTERNAL_SKILLS_SOURCE),
        "hooks_source": str(HERMES_EXTERNAL_HOOKS_SOURCE),
        "config_source": str(HERMES_EXTERNAL_CONFIG_SOURCE),
        "package_root": str(HERMES_EXTERNAL_PACKAGE_ROOT),
        "config": str(HERMES_EXTERNAL_CONFIG),
        "hook_root": str(HERMES_EXTERNAL_HOOK_ROOT),
        "marker": HERMES_EXTERNAL_MARKER,
        "hook_command": HERMES_EXTERNAL_HOOK_COMMAND,
        "hook_event": HERMES_EXTERNAL_HOOK_EVENT,
    }
    for key, value in expected.items():
        if external.get(key) != value:
            raise ValueError(f"invalid Hermes external package {key}")
    catalog = external.get("catalog")
    if not isinstance(catalog, list) or catalog != components or len(catalog) != 25:
        raise ValueError("Hermes external package catalog must exactly match plan components")


def artifact_count_summary(artifacts: list[dict[str, Any]]) -> dict[str, int]:
    destinations = [
        str(_relative_posix_path(artifact.get("destination"), "Artifact destination"))
        for artifact in artifacts
    ]
    unique_destinations = set(destinations)
    return {
        "source_artifact_count": len(artifacts),
        "unique_final_destination_count": len(unique_destinations),
        "shared_final_destination_count": sum(
            1 for destination in unique_destinations if destinations.count(destination) > 1
        ),
    }


def validate_final_destination_fan_in(artifacts: list[dict[str, Any]]) -> None:
    by_destination: dict[PurePosixPath, list[dict[str, Any]]] = {}
    for artifact in artifacts:
        destination = _relative_posix_path(
            artifact.get("destination"),
            "Artifact destination",
        )
        by_destination.setdefault(destination, []).append(artifact)

    for destination, destination_artifacts in by_destination.items():
        if len(destination_artifacts) < 2:
            continue

        fingerprints: dict[str, list[dict[str, Any]]] = {}
        for artifact in destination_artifacts:
            fingerprint = artifact.get("_content_sha256")
            if not isinstance(fingerprint, str):
                source = _relative_posix_path(artifact.get("source"), "Artifact source")
                source_file = (REPO_ROOT / source).resolve()
                try:
                    source_file.relative_to(REPO_ROOT.resolve())
                except ValueError as exc:
                    raise ValueError(f"source escapes repository root: {source}") from exc
                if not source_file.is_file():
                    continue
                fingerprint = hashlib.sha256(source_file.read_bytes()).hexdigest()
            fingerprints.setdefault(fingerprint, []).append(artifact)

        if len(fingerprints) <= 1:
            continue

        sources = ", ".join(
            f"{artifact.get('source')} ({artifact.get('target')}:{artifact.get('component_id')})"
            for artifact in destination_artifacts
        )
        raise ValueError(
            "final destination has divergent source content: "
            f"{destination}; sources: {sources}"
        )


def _relative_posix_path(raw: Any, field_name: str) -> PurePosixPath:
    if isinstance(raw, PurePosixPath):
        path = raw
    elif isinstance(raw, str) and raw:
        path = PurePosixPath(raw)
    else:
        raise ValueError(f"{field_name} must be a non-empty string")
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
