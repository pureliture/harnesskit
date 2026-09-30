"""Read-only inspection and validation for the HarnessKit project install surface.

This module deliberately has no apply, delete, or prune operation. It derives
removal candidates from fresh profile plans plus exact renderer/hash evidence,
then validates a separately reviewed manifest without mutating the target tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

import jsonschema
import yaml


SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from scripts.adapters import build as adapter_build  # noqa: E402
from scripts.install import plan as install_plan  # noqa: E402


TARGET_PROFILE = "harness-maintenance"
TARGET_PROFILE_ID = "harnesskit.profile.harness-maintenance"
OWNERSHIP_PROFILES = ("engineering", "scm")
ALLOWED_INSTALL_SURFACE_ROOTS = (
    ".agents/skills/",
    ".claude/skills/",
    ".claude/agents/",
    ".codex/agents/",
    ".codex/config.toml",
    "AGENTS.md",
    "CLAUDE.md",
)
PROTECTED_PREFIXES = (
    ".codex/specs/",
    ".claude/specs/",
    "components/",
    "sources/",
    "docs/",
    "history/",
    "docs/runtime-evidence/",
    ".worktrees/",
)
CODEX_CONFIG_PATH = ".codex/config.toml"
BEGIN_MARKER = "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->"
END_MARKER = "<!-- END HARNESSKIT GENERATED CONTEXT -->"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_value(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout


def _repository_identity(repo_root: Path) -> str:
    git_root = Path(_run_git(repo_root, "rev-parse", "--show-toplevel").strip()).resolve()
    if git_root != repo_root.resolve():
        raise ValueError("repo-root must be the git worktree root")
    revision = _run_git(repo_root, "rev-parse", "HEAD").strip()
    return f"{repo_root.resolve().name}@{revision}"


def _is_allowed_surface_path(path: str) -> bool:
    return any(
        path.startswith(root) if root.endswith("/") else path == root
        for root in ALLOWED_INSTALL_SURFACE_ROOTS
    )


def _safe_relative_path(path: str) -> PurePosixPath:
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty repository-relative string")
    if "\\" in path or "\0" in path:
        raise ValueError(f"unsafe repository-relative path: {path!r}")
    if any(character in path for character in "*?[]{}"):
        raise ValueError(f"glob syntax is prohibited: {path}")
    normalized = PurePosixPath(path)
    if normalized.is_absolute() or ".." in normalized.parts or str(normalized) in {"", "."}:
        raise ValueError(f"unsafe repository-relative path: {path}")
    return normalized


def _validate_removal_path(path: str) -> PurePosixPath:
    normalized = _safe_relative_path(path)
    rendered = normalized.as_posix()
    if not _is_allowed_surface_path(rendered):
        raise ValueError(f"path is outside approved install surfaces: {path}")
    if any(rendered == prefix.rstrip("/") or rendered.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        raise ValueError(f"path overlaps a protected prefix: {path}")
    if rendered in {"AGENTS.md", "CLAUDE.md", CODEX_CONFIG_PATH}:
        raise ValueError(f"protected expected destination cannot be removed: {path}")
    return normalized


def _has_symlink_component(repo_root: Path, path: str) -> bool:
    current = repo_root
    for part in PurePosixPath(path).parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _tracked_surface(repo_root: Path) -> tuple[list[str], list[str]]:
    tracked = _run_git(repo_root, "ls-files", "-z").split("\0")
    present: list[str] = []
    symlinks: list[str] = []
    for path in sorted(item for item in tracked if item and _is_allowed_surface_path(item)):
        target = repo_root / path
        if _has_symlink_component(repo_root, path):
            symlinks.append(path)
        elif target.is_file():
            present.append(path)
    if symlinks:
        raise ValueError(f"tracked install surface contains symlink: {symlinks[0]}")
    return present, symlinks


def _rendered_sources(plan: dict[str, Any]) -> dict[str, str]:
    entries = adapter_build._selected_registry_entries(plan["components"])
    chunks: dict[Path, list[str]] = {}
    append_paths: set[Path] = set()
    targets = set(plan["targets"])
    for component_id, entry in entries.items():
        for rendered in adapter_build._render_component(
            component_id,
            entry,
            explicit=False,
            target_ids=targets,
        ):
            output_path, content, append, _mode = adapter_build._unpack_rendered_output(rendered)
            chunks.setdefault(output_path, []).append(content)
            if append:
                append_paths.add(output_path)

    result: dict[str, str] = {}
    for output_path, values in chunks.items():
        rel_path = output_path.relative_to(SOURCE_ROOT).as_posix()
        if output_path in append_paths:
            result[rel_path] = adapter_build._combined_append_content(output_path, values)
        else:
            if len(set(values)) != 1:
                raise ValueError(f"renderer produced divergent content for {rel_path}")
            result[rel_path] = values[0]
    return result


def _profile_evidence(profile: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    plan = install_plan.build_plan(profile, scope="project", mode="dry-run")
    rendered = _rendered_sources(plan)
    files: dict[str, dict[str, Any]] = {}
    config_tables: dict[str, dict[str, Any]] = {}

    config_artifact: dict[str, Any] | None = None
    for artifact in plan["artifacts"]:
        source = artifact["source"]
        if source not in rendered:
            raise ValueError(f"renderer source is missing for plan artifact: {source}")
        evidence = {
            "path": artifact["destination"],
            "owner_profile_id": plan["profile_id"],
            "component_id": artifact["component_id"],
            "target": artifact["target"],
            "source": source,
            "content_sha256": _sha256_bytes(rendered[source].encode("utf-8")),
            "merge_strategy": artifact.get("merge_strategy", "full-path-overwrite"),
            "expected_content": rendered[source],
        }
        if artifact["destination"] == CODEX_CONFIG_PATH:
            config_artifact = artifact
            continue
        destination = artifact["destination"]
        existing = files.get(destination)
        if existing is not None and existing["content_sha256"] != evidence["content_sha256"]:
            raise ValueError(
                f"profile renderer produced divergent target content: {destination}"
            )
        priority = {"codex": 0, "claude": 1, "project": 2}
        if existing is None or priority.get(evidence["target"], 99) < priority.get(
            existing["target"], 99
        ):
            files[destination] = evidence

    if config_artifact is not None:
        config_source = config_artifact["source"]
        config = tomllib.loads(rendered[config_source])
        agents = config.get("agents") or {}
        component_by_name: dict[str, str] = {}
        for destination, evidence in files.items():
            if destination.startswith(".codex/agents/") and destination.endswith(".toml"):
                component_by_name[PurePosixPath(destination).stem] = evidence["component_id"]
        for name, table in agents.items():
            if not isinstance(table, dict):
                raise ValueError(f"rendered Codex agent table is not a mapping: {name}")
            component_id = component_by_name.get(name)
            if component_id is None:
                raise ValueError(f"rendered Codex agent table has no component file: {name}")
            config_tables[name] = {
                "config_path": CODEX_CONFIG_PATH,
                "table": name,
                "owner_profile_id": plan["profile_id"],
                "component_id": component_id,
                "content_sha256": _sha256_value(table),
                "expected_table": table,
            }
    return plan, files, config_tables


def _managed_block_body(content: str, begin_marker: str, end_marker: str) -> str | None:
    begin = content.find(begin_marker)
    end = content.find(end_marker)
    if begin < 0 or end < 0 or end < begin:
        return None
    body_start = begin + len(begin_marker)
    return content[body_start:end].strip("\n") + "\n"


def _file_matches(repo_root: Path, evidence: dict[str, Any]) -> bool:
    target = repo_root / evidence["path"]
    if target.is_symlink() or not target.is_file():
        return False
    current = target.read_text(encoding="utf-8")
    if evidence["merge_strategy"] == "managed-block":
        extracted = _managed_block_body(current, BEGIN_MARKER, END_MARKER)
        expected = evidence["expected_content"].strip("\n") + "\n"
        return extracted == expected
    return _sha256_bytes(current.encode("utf-8")) == evidence["content_sha256"]


def _public_file_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        key: evidence[key]
        for key in (
            "path",
            "owner_profile_id",
            "component_id",
            "target",
            "source",
            "content_sha256",
        )
    }


def _public_table_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        key: evidence[key]
        for key in (
            "config_path",
            "table",
            "owner_profile_id",
            "component_id",
            "content_sha256",
        )
    }


def inspect_project_surface(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    repository_identity = _repository_identity(repo_root)
    target_plan, target_files, target_tables = _profile_evidence(TARGET_PROFILE)
    plan_hash = _sha256_value(target_plan)
    # Multiple adapters can intentionally converge on one project path (for
    # example Codex and agy both use `.agents/skills`). The surface inventory is
    # about final paths, not source-artifact fan-in, so keep it unique.
    expected = sorted({artifact["destination"] for artifact in target_plan["artifacts"]})
    tracked_present, _symlinks = _tracked_surface(repo_root)
    present_set = set(tracked_present)
    expected_set = set(expected)

    ownership_files: dict[str, dict[str, Any]] = {}
    ownership_tables: dict[str, dict[str, Any]] = {}
    ownership_profiles_used: list[str] = []
    for profile in OWNERSHIP_PROFILES:
        if not (SOURCE_ROOT / "profiles" / f"{profile}.yml").is_file():
            continue
        _plan, files, tables = _profile_evidence(profile)
        ownership_profiles_used.append(_plan["profile_id"])
        for path, evidence in files.items():
            ownership_files.setdefault(path, evidence)
        for name, evidence in tables.items():
            ownership_tables.setdefault(name, evidence)

    missing = sorted(expected_set - present_set)
    extra = sorted(present_set - expected_set)
    content_mismatches = sorted(
        path
        for path in expected_set & present_set
        if path != CODEX_CONFIG_PATH and not _file_matches(repo_root, target_files[path])
    )

    owned_extra_files: list[dict[str, Any]] = []
    foreign_extra_files: list[str] = []
    for path in extra:
        evidence = ownership_files.get(path)
        if evidence is not None and _file_matches(repo_root, evidence):
            owned_extra_files.append(_public_file_evidence(evidence))
        else:
            foreign_extra_files.append(path)

    current_agents: dict[str, Any] = {}
    config_path = repo_root / CODEX_CONFIG_PATH
    if CODEX_CONFIG_PATH in present_set:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        raw_agents = config.get("agents") or {}
        if not isinstance(raw_agents, dict):
            raise ValueError(".codex/config.toml agents must be a mapping")
        current_agents = raw_agents

    expected_agent_names = set(target_tables)
    current_agent_names = set(current_agents)
    missing_tables = sorted(expected_agent_names - current_agent_names)
    mismatched_tables = sorted(
        name
        for name in expected_agent_names & current_agent_names
        if current_agents[name] != target_tables[name]["expected_table"]
    )
    owned_extra_tables: list[dict[str, Any]] = []
    foreign_extra_tables: list[str] = []
    for name in sorted(current_agent_names - expected_agent_names):
        evidence = ownership_tables.get(name)
        if evidence is not None and current_agents[name] == evidence["expected_table"]:
            owned_extra_tables.append(_public_table_evidence(evidence))
        else:
            foreign_extra_tables.append(name)

    counts = {
        "expected": len(expected),
        "observed": len(tracked_present),
        "missing": len(missing),
        "extra": len(extra),
        "content_mismatch": len(content_mismatches),
        "registration_mismatch": (
            len(missing_tables) + len(mismatched_tables) + len(owned_extra_tables)
        ),
    }
    return {
        "version": 1,
        "repository_root_identity": repository_identity,
        "profile_id": TARGET_PROFILE_ID,
        "scope": "project",
        "source_plan": {
            "plan_id": target_plan["plan_id"],
            "sha256": plan_hash,
        },
        "allowed_install_surface_roots": list(ALLOWED_INSTALL_SURFACE_ROOTS),
        "protected_prefixes": list(PROTECTED_PREFIXES),
        "ownership_profiles": ownership_profiles_used,
        "expected_destinations": expected,
        "tracked_present": tracked_present,
        "missing": missing,
        "extra": extra,
        "content_mismatches": content_mismatches,
        "owned_extra_files": owned_extra_files,
        "foreign_extra_files": foreign_extra_files,
        "codex_agent_tables": {
            "expected": sorted(expected_agent_names),
            "present": sorted(current_agent_names),
            "missing": missing_tables,
            "mismatched": mismatched_tables,
            "owned_extra": owned_extra_tables,
            "foreign_extra": foreign_extra_tables,
        },
        "counts": counts,
    }


def _load_data(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"mapping expected: {path}")
    return data


def validate_removal_manifest(
    manifest: dict[str, Any],
    *,
    repo_root: Path,
    schema_path: Path,
) -> dict[str, Any]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(manifest)
    report = inspect_project_surface(repo_root)

    for field in (
        "version",
        "repository_root_identity",
        "profile_id",
        "scope",
        "source_plan",
        "allowed_install_surface_roots",
        "protected_prefixes",
        "expected_destinations",
        "counts",
    ):
        if manifest[field] != report[field]:
            raise ValueError(f"manifest field is stale or inconsistent: {field}")

    file_entries = manifest["remove_files"]
    file_paths = [entry["path"] for entry in file_entries]
    if file_paths != sorted(file_paths) or len(file_paths) != len(set(file_paths)):
        raise ValueError("remove_files must be unique and lexicographically sorted")
    for path in file_paths:
        _validate_removal_path(path)
        target = repo_root / path
        if _has_symlink_component(repo_root, path) or not target.is_file():
            raise ValueError(f"remove file is not a tracked-present regular file: {path}")
    expected_files = report["owned_extra_files"]
    if file_entries != expected_files:
        raise ValueError("remove_files do not exactly match fresh owned-extra evidence")

    table_entries = manifest["remove_codex_agent_tables"]
    table_names = [entry["table"] for entry in table_entries]
    if table_names != sorted(table_names) or len(table_names) != len(set(table_names)):
        raise ValueError("remove_codex_agent_tables must be unique and sorted")
    expected_tables = report["codex_agent_tables"]["owned_extra"]
    if table_entries != expected_tables:
        raise ValueError(
            "remove_codex_agent_tables do not exactly match fresh owned-extra evidence"
        )

    return {
        "status": "pass",
        "profile_id": report["profile_id"],
        "scope": report["scope"],
        "source_plan": report["source_plan"],
        "remove_file_count": len(file_entries),
        "remove_codex_agent_table_count": len(table_entries),
    }


def _emit(value: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(yaml.safe_dump(value, allow_unicode=True, sort_keys=True).rstrip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only HarnessKit project install-surface inspection and validation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="emit a fresh read-only report")
    inspect_parser.add_argument("--repo-root", required=True, type=Path)
    inspect_parser.add_argument("--format", choices=["json", "yaml"], default="json")

    validate_parser = subparsers.add_parser(
        "validate", help="validate a reviewed manifest without mutation"
    )
    validate_parser.add_argument("--repo-root", required=True, type=Path)
    validate_parser.add_argument("--manifest", required=True, type=Path)
    validate_parser.add_argument(
        "--schema",
        type=Path,
        default=SOURCE_ROOT / "schemas/project-surface-removal.schema.json",
    )
    validate_parser.add_argument("--format", choices=["json", "yaml"], default="json")
    args = parser.parse_args(argv)

    try:
        if args.command == "inspect":
            result = inspect_project_surface(args.repo_root)
        else:
            result = validate_removal_manifest(
                _load_data(args.manifest),
                repo_root=args.repo_root.resolve(),
                schema_path=args.schema.resolve(),
            )
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError, json.JSONDecodeError, yaml.YAMLError, jsonschema.ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _emit(result, args.format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
