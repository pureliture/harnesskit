from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml


KIND_PLURALS = {
    "agent": "agents",
    "skill": "skills",
    "workflow": "workflows",
}
COMPONENT_REFERENCE_RE = re.compile(
    r"\bharnesskit\.(?:skill|agent|hook|rule|command|workflow)\.[A-Za-z0-9][A-Za-z0-9._-]*\b"
)


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML mapping expected: {path}")
    return data


def _repo_relative(repo_root: Path, path: Path) -> str:
    resolved = path.resolve()
    root = repo_root.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"path escapes repo root: {path}") from exc


def _normalize_repo_file(repo_root: Path, raw_path: object) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"repo-relative file path expected: {raw_path!r}")

    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"repo-relative file path expected: {raw_path}")

    full_path = repo_root / path
    if not full_path.is_file():
        raise ValueError(f"included file does not exist: {raw_path}")

    return _repo_relative(repo_root, full_path)


def _validate_repo_glob(raw_pattern: object) -> str:
    if not isinstance(raw_pattern, str) or not raw_pattern.strip():
        raise ValueError(f"repo-relative glob expected: {raw_pattern!r}")

    pattern_path = Path(raw_pattern)
    if pattern_path.is_absolute() or ".." in pattern_path.parts:
        raise ValueError(f"repo-relative glob expected: {raw_pattern}")
    return raw_pattern


def _is_excluded(rel_path: str, patterns: list[str]) -> bool:
    return any(fnmatch(rel_path, pattern) for pattern in patterns)


def _matching_exclusion(rel_path: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        if fnmatch(rel_path, pattern):
            return pattern
    return None


def _ensure_not_excluded(rel_path: str, excluded_patterns: list[str]) -> None:
    blocked_pattern = _matching_exclusion(rel_path, excluded_patterns)
    if blocked_pattern is not None:
        raise ValueError(
            "private blocklist matched required path: "
            f"{rel_path} (pattern: {blocked_pattern})"
        )


def _add_required_path(paths: set[str], rel_path: str, excluded_patterns: list[str]) -> None:
    _ensure_not_excluded(rel_path, excluded_patterns)
    paths.add(rel_path)


def _component_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, str):
        references.update(match.group(0) for match in COMPONENT_REFERENCE_RE.finditer(value))
    elif isinstance(value, dict):
        for child in value.values():
            references.update(_component_references(child))
    elif isinstance(value, list):
        for child in value:
            references.update(_component_references(child))
    return references


def _declared_component_identity(component_id: str, manifest: dict[str, Any]) -> set[str]:
    identity = {component_id}
    for key in ("component_id", "workflow_id"):
        value = manifest.get(key)
        if isinstance(value, str):
            identity.add(value)
    return identity


def _validate_component_dependencies(
    component_id: str,
    manifest_path: str,
    manifest: dict[str, Any],
    registry_components: dict[str, Any],
    public_component_ids: set[str],
) -> None:
    references = _component_references(manifest)
    references.difference_update(_declared_component_identity(component_id, manifest))

    for reference in sorted(references):
        if reference not in registry_components:
            raise ValueError(
                f"unregistered dependency: {component_id} references {reference} "
                f"in {manifest_path}"
            )
        if reference not in public_component_ids:
            raise ValueError(
                f"private/out-of-closure dependency: {component_id} references {reference} "
                f"in {manifest_path}"
            )


def _source_revision(repo_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    revision = result.stdout.strip()
    return revision or None


def _support_paths(repo_root: Path, patterns: list[str]) -> list[str]:
    paths: set[str] = set()
    for raw_pattern in patterns:
        pattern = _validate_repo_glob(raw_pattern)
        for path in repo_root.glob(pattern):
            if not path.is_file():
                continue
            rel_path = _normalize_repo_file(repo_root, _repo_relative(repo_root, path))
            paths.add(rel_path)
    return sorted(paths)


def _optional_sibling(repo_root: Path, manifest_path: str, filename: str) -> str | None:
    raw_path = (Path(manifest_path).parent / filename).as_posix()
    if not (repo_root / raw_path).is_file():
        return None
    return _normalize_repo_file(repo_root, raw_path)


def build_manifest(policy_path: Path, repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    policy = _load_yaml(policy_path)
    excluded_patterns = list(policy.get("excluded_patterns") or [])

    source_profile_path = _normalize_repo_file(repo_root, policy["source_profile_path"])
    registry_path = _normalize_repo_file(
        repo_root,
        policy.get("registry_path", "components/registry.yml"),
    )
    _ensure_not_excluded(source_profile_path, excluded_patterns)
    _ensure_not_excluded(registry_path, excluded_patterns)
    profile = _load_yaml(repo_root / source_profile_path)
    registry = _load_yaml(repo_root / registry_path)
    registry_components = registry.get("components") or {}
    if not isinstance(registry_components, dict):
        raise ValueError("components/registry.yml components mapping expected")

    if profile.get("profile_id") != policy["source_profile"]:
        raise ValueError(
            f"policy source_profile does not match profile_id: {policy['source_profile']}"
        )

    grouped_components: dict[str, list[dict[str, Any]]] = {
        "agents": [],
        "skills": [],
        "workflows": [],
    }
    public_component_ids = set(profile.get("components") or [])
    included_paths: set[str] = set()
    _add_required_path(included_paths, source_profile_path, excluded_patterns)
    _add_required_path(included_paths, registry_path, excluded_patterns)

    for component_id in public_component_ids:
        registry_entry = registry_components.get(component_id)
        if not isinstance(registry_entry, dict):
            raise ValueError(f"profile component is not registered: {component_id}")

        manifest_path = _normalize_repo_file(repo_root, registry_entry["path"])
        _ensure_not_excluded(manifest_path, excluded_patterns)
        manifest = _load_yaml(repo_root / manifest_path)
        _validate_component_dependencies(
            component_id,
            manifest_path,
            manifest,
            registry_components,
            public_component_ids,
        )
        kind = str(manifest.get("kind") or registry_entry.get("kind"))
        group = KIND_PLURALS.get(kind)
        if group is None:
            raise ValueError(f"unsupported public projection component kind: {kind}")

        owned_files = sorted(
            _normalize_repo_file(repo_root, path)
            for path in manifest.get("owned_files") or []
        )
        component_paths = [manifest_path, *owned_files]
        provenance_path = _optional_sibling(repo_root, manifest_path, "provenance.map.yml")
        if provenance_path is not None:
            component_paths.append(provenance_path)
        if kind == "workflow":
            card_path = _optional_sibling(repo_root, manifest_path, "card.md")
            if card_path is not None:
                component_paths.append(card_path)

        for rel_path in component_paths:
            _add_required_path(included_paths, rel_path, excluded_patterns)

        grouped_components[group].append(
            {
                "id": component_id,
                "kind": kind,
                "manifest_path": manifest_path,
                "owned_files": owned_files,
            }
        )

    for group in grouped_components.values():
        group.sort(key=lambda component: component["id"])

    support_paths = _support_paths(
        repo_root,
        list(policy.get("support_paths") or []),
    )
    for rel_path in support_paths:
        _add_required_path(included_paths, rel_path, excluded_patterns)

    return {
        "projection_id": policy["projection_id"],
        "source_profile": {
            "id": profile["profile_id"],
            "path": source_profile_path,
        },
        "source_revision": _source_revision(repo_root),
        "registry_path": registry_path,
        "components": grouped_components,
        "closure_summary": {
            "agents": len(grouped_components["agents"]),
            "skills": len(grouped_components["skills"]),
            "workflows": len(grouped_components["workflows"]),
        },
        "included_paths": sorted(included_paths),
        "excluded_patterns": excluded_patterns,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Emit a deterministic HarnessKit public projection manifest."
    )
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    policy_path = args.policy
    if not policy_path.is_absolute():
        policy_path = repo_root / policy_path

    try:
        manifest = build_manifest(policy_path.resolve(), repo_root)
    except KeyError as exc:
        print(f"error: missing required field: {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
