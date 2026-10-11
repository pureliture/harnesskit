from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.profiles.selection import validate_profile_selection  # noqa: E402


KIND_PLURALS = {
    "agent": "agents",
    "rule": "rules",
    "skill": "skills",
    "workflow": "workflows",
}
COMPONENT_REFERENCE_RE = re.compile(
    r"\bharnesskit\.(?:skill|agent|hook|rule|command|workflow)\.[A-Za-z0-9][A-Za-z0-9._-]*\b"
)


def registry_projection_bytes(projection: dict[str, Any] | None) -> bytes | None:
    """Validate the single approved generated file before reporting or writing it."""
    if projection is None:
        return None
    if not isinstance(projection, dict) or set(projection) != {"path", "content", "sha256"}:
        raise ValueError("invalid registry_projection fields")
    if projection["path"] != "components/registry.yml":
        raise ValueError("registry_projection requires components/registry.yml")
    if not isinstance(projection["content"], str):
        raise ValueError("registry_projection content must be text")
    data = projection["content"].encode("utf-8")
    if hashlib.sha256(data).hexdigest() != projection["sha256"]:
        raise ValueError("registry_projection sha256 mismatch")
    return data


PUBLIC_COMPONENT_PROVENANCE_SOURCE_ID = "harnesskit-public-skill-provenance"
PUBLIC_COMPONENT_PROVENANCE_SOURCE_PATH = "sources/public-component-provenance.md"


def public_metadata_projection_bytes(
    projections: list[dict[str, Any]] | None,
) -> dict[str, bytes]:
    """Validate deterministic safe-metadata overlays for explicitly published skills."""
    if projections is None:
        return {}
    if not isinstance(projections, list):
        raise ValueError("public_metadata_projections must be a list")
    result: dict[str, bytes] = {}
    for projection in projections:
        if not isinstance(projection, dict) or set(projection) != {"path", "content", "sha256"}:
            raise ValueError("invalid public metadata projection fields")
        path = projection["path"]
        if (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or "\\" in path
        ):
            raise ValueError("public metadata projection path must be repo-relative")
        if path in result:
            raise ValueError(f"duplicate public metadata projection path: {path}")
        if not isinstance(projection["content"], str):
            raise ValueError("public metadata projection content must be text")
        data = projection["content"].encode("utf-8")
        if hashlib.sha256(data).hexdigest() != projection["sha256"]:
            raise ValueError(f"public metadata projection sha256 mismatch: {path}")
        result[path] = data
    return result


def _public_source_refs(
    refs: object,
    source_registry: dict[str, Any],
    included_paths: set[str],
) -> list[dict[str, Any]]:
    if refs is None:
        refs = []
    if not isinstance(refs, list) or not all(isinstance(ref, dict) for ref in refs):
        raise ValueError("public skill source references must be a list of mappings")

    public_refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        source_id = ref.get("source_id")
        source = source_registry.get(source_id) if isinstance(source_id, str) else None
        if not isinstance(source, dict):
            continue
        if source.get("kind") == "repository_local":
            source_path = source.get("path")
            if not isinstance(source_path, str) or source_path not in included_paths:
                continue
        if source_id not in seen:
            public_refs.append(ref)
            seen.add(source_id)

    note = {
        "source_id": PUBLIC_COMPONENT_PROVENANCE_SOURCE_ID,
        "path": PUBLIC_COMPONENT_PROVENANCE_SOURCE_PATH,
        "used_for": ["public_standalone_component_provenance"],
    }
    if PUBLIC_COMPONENT_PROVENANCE_SOURCE_ID not in seen:
        public_refs.append(note)
    return public_refs


def _public_skill_metadata_projections(
    component_ids: list[str],
    registry_components: dict[str, Any],
    source_registry: dict[str, Any],
    repo_root: Path,
    included_paths: set[str],
) -> list[dict[str, Any]]:
    note = source_registry.get(PUBLIC_COMPONENT_PROVENANCE_SOURCE_ID)
    if (
        not isinstance(note, dict)
        or note.get("kind") != "repository_local"
        or note.get("path") != PUBLIC_COMPONENT_PROVENANCE_SOURCE_PATH
        or PUBLIC_COMPONENT_PROVENANCE_SOURCE_PATH not in included_paths
    ):
        raise ValueError("public standalone skill provenance source is not registered and included")

    projections: list[dict[str, Any]] = []
    for component_id in component_ids:
        entry = registry_components[component_id]
        manifest_path = _normalize_repo_file(repo_root, entry.get("path"))
        provenance_path = _optional_sibling(repo_root, manifest_path, "provenance.map.yml")
        for rel_path, field in ((manifest_path, "source_refs"), (provenance_path, "sources")):
            if rel_path is None:
                continue
            if rel_path not in included_paths:
                raise ValueError(f"public skill metadata path is outside the projection: {rel_path}")
            value = _load_yaml(repo_root / rel_path)
            if field == "source_refs":
                value[field] = _public_source_refs(value.get(field), source_registry, included_paths)
            else:
                provenance = value.get("provenance")
                if not isinstance(provenance, dict):
                    raise ValueError(f"public provenance map is missing its provenance mapping: {rel_path}")
                provenance[field] = _public_source_refs(
                    provenance.get(field), source_registry, included_paths
                )
            content = yaml.safe_dump(value, sort_keys=False, allow_unicode=True)
            body = content.encode("utf-8")
            projections.append({
                "path": rel_path,
                "content": content,
                "sha256": hashlib.sha256(body).hexdigest(),
            })
    return sorted(projections, key=lambda item: item["path"])


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


def _support_paths(
    repo_root: Path,
    patterns: list[str],
    excluded_patterns: list[str],
) -> list[str]:
    paths: set[str] = set()
    for raw_pattern in patterns:
        pattern = _validate_repo_glob(raw_pattern)
        for path in repo_root.glob(pattern):
            if not path.is_file():
                continue
            rel_path = _normalize_repo_file(repo_root, _repo_relative(repo_root, path))
            if _matching_exclusion(rel_path, excluded_patterns) is not None:
                continue
            paths.add(rel_path)
    return sorted(paths)


def _optional_sibling(repo_root: Path, manifest_path: str, filename: str) -> str | None:
    raw_path = (Path(manifest_path).parent / filename).as_posix()
    if not (repo_root / raw_path).is_file():
        return None
    return _normalize_repo_file(repo_root, raw_path)


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _explicit_public_components(
    policy: dict[str, Any],
    repo_root: Path,
    registry_components: dict[str, Any],
    excluded_patterns: list[str],
) -> list[str]:
    raw_components = policy.get("public_components") or []
    if not isinstance(raw_components, list) or not all(
        isinstance(component_id, str) and component_id
        for component_id in raw_components
    ):
        raise ValueError("public_components must be a list of non-empty component ids")
    if len(raw_components) != len(set(raw_components)):
        raise ValueError("duplicate public component identity")

    for component_id in raw_components:
        registry_entry = registry_components.get(component_id)
        if not isinstance(registry_entry, dict):
            raise ValueError(f"public component is not registered: {component_id}")
        if registry_entry.get("kind") != "skill":
            raise ValueError(
                f"public component registry kind must be skill: {component_id}"
            )
        manifest_path = _normalize_repo_file(repo_root, registry_entry.get("path"))
        _ensure_not_excluded(manifest_path, excluded_patterns)
        manifest = _load_yaml(repo_root / manifest_path)
        if manifest.get("component_id") != component_id:
            raise ValueError(
                f"public skill manifest identity does not match registry: {component_id}"
            )
        if manifest.get("kind") != "skill":
            raise ValueError(f"public skill manifest kind must be skill: {component_id}")
    return list(raw_components)


def _public_component_ids(profile: dict[str, Any]) -> list[str]:
    components = profile.get("components") or []
    install_policy = profile.get("install_policy") or {}
    allowed_scopes = install_policy.get("allowed_scopes") or []
    default_scope = install_policy.get("default_scope")
    scope_components = install_policy.get("scope_components") or {}

    if not isinstance(components, list) or not all(
        isinstance(component_id, str) for component_id in components
    ):
        raise ValueError("profile components must be a list of strings")
    if not isinstance(allowed_scopes, list) or not all(
        isinstance(scope, str) for scope in allowed_scopes
    ):
        raise ValueError("install_policy.allowed_scopes must be a list of strings")
    if not isinstance(scope_components, dict):
        raise ValueError("install_policy.scope_components must be a mapping")

    user_components = scope_components.get("user") or []
    if allowed_scopes != ["project"] or default_scope == "user" or user_components:
        raise ValueError("public projection source profile must be project-scope-only")

    project_components = scope_components.get("project") or []
    if not isinstance(project_components, list) or not all(
        isinstance(component_id, str) for component_id in project_components
    ):
        raise ValueError(
            "install_policy.scope_components.project must be a list of strings"
        )
    return _dedupe_preserving_order([*components, *project_components])


def build_manifest(policy_path: Path, repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    policy = _load_yaml(policy_path)
    exceptions = (policy.get("policy") or {}).get("content_write_exceptions", {})
    if exceptions not in ({}, {"components/registry.yml": "selected_component_closure"}):
        raise ValueError("unsupported content_write_exceptions")
    excluded_patterns = list(policy.get("excluded_patterns") or [])

    source_profile_path = _normalize_repo_file(repo_root, policy["source_profile_path"])
    registry_path = _normalize_repo_file(
        repo_root,
        policy.get("registry_path", "components/registry.yml"),
    )
    if exceptions and registry_path != "components/registry.yml":
        raise ValueError("registry exception requires components/registry.yml")
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

    validate_profile_selection(
        profile,
        registry_components,
        load_manifest=lambda raw_path: _load_yaml(
            repo_root / _normalize_repo_file(repo_root, raw_path)
        ),
    )

    explicit_public_components = _explicit_public_components(
        policy,
        repo_root,
        registry_components,
        excluded_patterns,
    )
    profile_component_ids = set(_public_component_ids(profile))
    overlap = sorted(profile_component_ids.intersection(explicit_public_components))
    if overlap:
        raise ValueError(
            "public components must not duplicate profile members: " + overlap[0]
        )

    raw_public_workflows = policy.get("public_workflows") or []
    if not isinstance(raw_public_workflows, list) or not all(
        isinstance(component_id, str) and component_id
        for component_id in raw_public_workflows
    ):
        raise ValueError("public_workflows must be a list of non-empty component ids")
    if len(raw_public_workflows) != len(set(raw_public_workflows)):
        raise ValueError("duplicate public workflow identity")
    explicit_public_workflows = sorted(raw_public_workflows)
    for component_id in explicit_public_workflows:
        registry_entry = registry_components.get(component_id)
        if not isinstance(registry_entry, dict):
            raise ValueError(f"public workflow is not registered: {component_id}")
        if registry_entry.get("kind") != "workflow":
            raise ValueError(
                f"public workflow registry kind must be workflow: {component_id}"
            )
        manifest_path = _normalize_repo_file(repo_root, registry_entry.get("path"))
        _ensure_not_excluded(manifest_path, excluded_patterns)
        manifest = _load_yaml(repo_root / manifest_path)
        if manifest.get("kind") != "workflow":
            raise ValueError(
                f"public workflow manifest kind must be workflow: {component_id}"
            )

    grouped_components: dict[str, list[dict[str, Any]]] = {
        "agents": [],
        "rules": [],
        "skills": [],
        "workflows": [],
    }
    ordered_component_ids = _dedupe_preserving_order(
        [
            *_public_component_ids(profile),
            *explicit_public_components,
            *explicit_public_workflows,
        ]
    )
    public_component_ids = set(ordered_component_ids)
    included_paths: set[str] = set()
    _add_required_path(included_paths, source_profile_path, excluded_patterns)
    _add_required_path(included_paths, registry_path, excluded_patterns)

    for component_id in ordered_component_ids:
        registry_entry = registry_components.get(component_id)
        if not isinstance(registry_entry, dict):
            raise ValueError(f"profile component is not registered: {component_id}")

        if exceptions:
            _validate_component_dependencies(
                component_id, registry_path, registry_entry,
                registry_components, public_component_ids,
            )

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

    support_paths = _support_paths(
        repo_root,
        list(policy.get("support_paths") or []),
        excluded_patterns,
    )
    for rel_path in support_paths:
        _add_required_path(included_paths, rel_path, excluded_patterns)

    public_metadata_projections: list[dict[str, Any]] = []
    if explicit_public_components:
        source_registry_path = "sources/registry.yml"
        _ensure_not_excluded(source_registry_path, excluded_patterns)
        source_registry_data = _load_yaml(repo_root / source_registry_path)
        source_registry = source_registry_data.get("sources") or {}
        if not isinstance(source_registry, dict):
            raise ValueError("sources/registry.yml must contain a sources mapping")
        public_metadata_projections = _public_skill_metadata_projections(
            explicit_public_components,
            registry_components,
            source_registry,
            repo_root,
            included_paths,
        )

    result = {
        "projection_id": policy["projection_id"],
        "source_profile": {
            "id": profile["profile_id"],
            "path": source_profile_path,
        },
        "explicit_public_components": explicit_public_components,
        "explicit_public_workflows": explicit_public_workflows,
        "source_revision": _source_revision(repo_root),
        "registry_path": registry_path,
        "public_metadata_projections": public_metadata_projections,
        "components": grouped_components,
        "closure_summary": {
            "agents": len(grouped_components["agents"]),
            "rules": len(grouped_components["rules"]),
            "skills": len(grouped_components["skills"]),
            "workflows": len(grouped_components["workflows"]),
        },
        "included_paths": sorted(included_paths),
        "excluded_patterns": excluded_patterns,
    }
    if exceptions:
        public_registry = {
            **({"version": registry["version"]} if "version" in registry else {}),
            "components": {
                component_id: registry_components[component_id]
                for component_id in ordered_component_ids
            },
        }
        content = yaml.safe_dump(public_registry, sort_keys=True, allow_unicode=True)
        result["registry_projection"] = {
            "path": registry_path,
            "content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
    return result


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
