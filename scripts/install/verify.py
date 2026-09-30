from __future__ import annotations

import argparse
import filecmp
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.install.common import artifact_count_summary, destination_path, load_plan, source_path
from scripts.install.managed_artifacts import (
    marker_matches,
    merge_json_deep,
    merge_managed_block,
    merge_toml_agents,
    prune_retired_codex_agent_registrations,
)
from scripts.install.common import (
    HERMES_EXTERNAL_CONFIG_DIR,
    HERMES_EXTERNAL_MARKER,
    is_legacy_hermes_external_config_dir,
)


def verify_plan(plan_path: Path, *, target_root: Path) -> int:
    plan = load_plan(plan_path)
    if plan.get("mode") not in {"apply", "verify"}:
        raise ValueError("install plan mode must be apply or verify")
    issues: list[str] = []

    if plan.get("hermes_external_package") is not None:
        return _verify_hermes_external_package(plan["hermes_external_package"], target_root)

    for artifact in plan["artifacts"]:
        try:
            src = source_path(artifact)
            dest = destination_path(target_root, artifact)
        except (FileNotFoundError, ValueError) as exc:
            issues.append(str(exc))
            continue

        if not dest.is_file():
            issues.append(f"missing destination: {dest.relative_to(target_root)}")
            continue
        if artifact.get("merge_strategy") == "managed-block":
            body = src.read_text(encoding="utf-8").rstrip()
            current = dest.read_text(encoding="utf-8")
            if merge_managed_block(current, body, artifact) != current:
                issues.append(f"managed block mismatch: {dest.relative_to(target_root)}")
            continue
        if artifact.get("merge_strategy") == "json-deep-merge":
            body = src.read_text(encoding="utf-8")
            current = dest.read_text(encoding="utf-8")
            if merge_json_deep(current, body, artifact) != current:
                issues.append(f"json merge mismatch: {dest.relative_to(target_root)}")
            continue
        if artifact.get("merge_strategy") == "toml-agents-merge":
            body = src.read_text(encoding="utf-8")
            current = dest.read_text(encoding="utf-8")
            try:
                expected = merge_toml_agents(current, body, artifact)
                expected = prune_retired_codex_agent_registrations(
                    expected,
                    plan.get("retired_codex_agent_registrations", []),
                )
            except ValueError as exc:
                issues.append(str(exc))
                continue
            if expected != current:
                issues.append(f"toml merge mismatch: {dest.relative_to(target_root)}")
            continue
        if not filecmp.cmp(src, dest, shallow=False):
            existing_content = dest.read_text(encoding="utf-8")
            if "<!-- BEGIN ROUTINE-HARNESS GENERATED -->" in existing_content:
                issues.append(f"updatable (needs apply): {dest.relative_to(target_root)}")
            else:
                issues.append(f"mismatch: {dest.relative_to(target_root)}")

    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1

    summary = artifact_count_summary(plan["artifacts"])
    print(
        "verified "
        f"{summary['source_artifact_count']} source artifacts across "
        f"{summary['unique_final_destination_count']} unique destinations in "
        f"{target_root}; scope=content+merge-fixed-point; "
        "unchecked=file-mode,mtime,xattr,platform-metadata,runtime"
    )
    return 0


def _verify_hermes_external_package(external: dict, target_root: Path) -> int:
    lifecycle = external["lifecycle"]
    package = target_root / external["package_root"]
    config = target_root / external["config"]
    hook_root = target_root / external["hook_root"]
    hook = hook_root / "optimal-response-pre-llm.cjs"
    hook_asset = hook_root / "stop-prompt-submit.cjs"
    command = external["hook_command"]
    event = external["hook_event"]
    marker = package.parent / external.get("marker", HERMES_EXTERNAL_MARKER)
    if lifecycle == "cleanup":
        print("verified hermes external package cleanup=preserves-package-and-config")
        return 0
    if lifecycle == "uninstall":
        if package.exists() or marker.exists():
            if not marker_matches(marker, external["catalog"]):
                print("verified hermes external package uninstall=preserved-unowned; runtime=unchecked")
                return 0
            print("managed Hermes package remains after uninstall", file=sys.stderr)
            return 1
        issues: list[str] = []
        if config.exists():
            try:
                config_data = yaml.safe_load(config.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                issues.append(f"invalid Hermes root config: {exc}")
                config_data = {}
            if isinstance(config_data, dict):
                external_dirs = _hermes_external_dirs(config_data, issues)
                if HERMES_EXTERNAL_CONFIG_DIR in external_dirs:
                    issues.append("exact Hermes external_dirs entry remains after uninstall")
                if any(is_legacy_hermes_external_config_dir(item) for item in external_dirs):
                    issues.append("legacy Hermes external_dirs entry remains after uninstall")
                registrations = _hermes_hook_registrations(config_data, event, issues)
                if {"command": command, "timeout": 5} in registrations:
                    issues.append("exact Hermes hook registration remains after uninstall")
            else:
                issues.append("Hermes root config is not a mapping")
        allowlist = target_root / ".hermes/shell-hooks-allowlist.json"
        if allowlist.exists():
            try:
                allowlist_data = json.loads(allowlist.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                issues.append(f"invalid Hermes shell-hook allowlist: {exc}")
                allowlist_data = []
            approvals = allowlist_data.get("approvals", []) if isinstance(allowlist_data, dict) else allowlist_data
            if isinstance(approvals, list) and any(
                isinstance(item, dict) and item.get("event") == event and item.get("command") == command
                for item in approvals
            ):
                issues.append("exact Hermes consent approval remains after uninstall")
        if issues:
            print("\n".join(issues), file=sys.stderr)
            return 1
        print("verified hermes external package uninstall=package-absent; runtime=unchecked")
        return 0
    issues: list[str] = []
    if not marker.is_file() or not marker_matches(marker, external["catalog"]):
        issues.append("missing deterministic Hermes package marker")
    else:
        expected_skills = {component.removeprefix("harnesskit.skill.") for component in external["catalog"]}
        skills_category = package / "software-development"
        actual_skills = (
            {path.name for path in skills_category.iterdir() if path.is_dir()}
            if skills_category.is_dir()
            else set()
        )
        if actual_skills != expected_skills or any(
            not (skills_category / skill / "SKILL.md").is_file() for skill in expected_skills
        ):
            issues.append("Hermes installed skills do not exactly match marker catalog")
    source_hook_root = REPO_ROOT / external["hooks_source"]
    for source_name, installed_path in (
        ("optimal-response-pre-llm.cjs", hook),
        ("stop-prompt-submit.cjs", hook_asset),
    ):
        source_path = source_hook_root / source_name
        if not source_path.is_file():
            issues.append(f"missing Hermes generated hook source: {source_name}")
        elif not installed_path.is_file():
            issues.append(f"missing Hermes installed hook asset: {source_name}")
        elif not filecmp.cmp(source_path, installed_path, shallow=False):
            issues.append(f"Hermes installed hook asset mismatch: {source_name}")
    try:
        config_data = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        issues.append(f"invalid Hermes root config: {exc}")
        config_data = {}
    if not isinstance(config_data, dict):
        issues.append("Hermes root config is not a mapping")
    else:
        external_dirs = _hermes_external_dirs(config_data, issues)
        if HERMES_EXTERNAL_CONFIG_DIR not in external_dirs:
            issues.append("missing exact Hermes external_dirs entry")
        if any(is_legacy_hermes_external_config_dir(item) for item in external_dirs):
            issues.append("legacy Hermes external_dirs entry remains")
        registrations = _hermes_hook_registrations(config_data, event, issues)
        if {"command": command, "timeout": 5} not in registrations:
            issues.append("missing exact Hermes hook registration")
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    print("verified hermes external package package=config-entry=hook-registration; consent=unverified; runtime=unchecked")
    return 0


def _hermes_external_dirs(config_data: dict, issues: list[str]) -> list[str]:
    skills = config_data.get("skills", {})
    if not isinstance(skills, dict):
        issues.append("Hermes skills config is not a mapping")
        return []
    external_dirs = skills.get("external_dirs", [])
    if external_dirs is None:
        return []
    if not isinstance(external_dirs, list) or not all(
        isinstance(item, str) for item in external_dirs
    ):
        issues.append("Hermes skills.external_dirs is not a list of strings")
        return []
    return external_dirs


def _hermes_hook_registrations(
    config_data: dict, event: str, issues: list[str]
) -> list[dict]:
    hooks = config_data.get("hooks", {})
    if not isinstance(hooks, dict):
        issues.append("Hermes hooks config is not a mapping")
        return []
    registrations = hooks.get(event, [])
    if registrations is None:
        return []
    if not isinstance(registrations, list) or not all(
        isinstance(item, dict) for item in registrations
    ):
        issues.append("Hermes hook registrations are not a list of mappings")
        return []
    return registrations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a HarnessKit install plan.")
    parser.add_argument("plan", help="Path to install plan JSON/YAML")
    parser.add_argument("--target-root", required=True, help="Target project root")
    args = parser.parse_args(argv)

    try:
        return verify_plan(Path(args.plan), target_root=Path(args.target_root))
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
