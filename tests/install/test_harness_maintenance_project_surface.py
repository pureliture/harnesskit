from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml

from scripts.install import plan as install_plan
from scripts.install import project_surface
from scripts.install.apply import apply_plan


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas/project-surface-removal.schema.json"


def _manifest_from_report(report: dict) -> dict:
    return {
        key: copy.deepcopy(report[key])
        for key in (
            "version",
            "repository_root_identity",
            "profile_id",
            "scope",
            "source_plan",
            "allowed_install_surface_roots",
            "protected_prefixes",
            "expected_destinations",
            "counts",
        )
    } | {
        "remove_files": copy.deepcopy(report["owned_extra_files"]),
        "remove_codex_agent_tables": copy.deepcopy(
            report["codex_agent_tables"]["owned_extra"]
        ),
    }


def _synthetic_file_evidence(**overrides: str) -> dict:
    evidence = {
        "path": ".agents/skills/adapter-authoring/SKILL.md",
        "owner_profile_id": "harnesskit.profile.engineering",
        "component_id": "harnesskit.skill.unknown",
        "target": "codex",
        "source": "dist/codex/.agents/skills/adapter-authoring/SKILL.md",
        "content_sha256": "0" * 64,
    }
    evidence.update(overrides)
    return evidence


def _synthetic_table_evidence(**overrides: str) -> dict:
    evidence = {
        "config_path": ".codex/config.toml",
        "table": "unknown_agent",
        "owner_profile_id": "harnesskit.profile.engineering",
        "component_id": "harnesskit.agent.unknown",
        "content_sha256": "0" * 64,
    }
    evidence.update(overrides)
    return evidence


def test_checked_in_maintenance_plan_is_exact_25_destination_contract() -> None:
    generated = install_plan.build_plan(
        "harness-maintenance", scope="project", mode="dry-run"
    )
    checked_in = yaml.safe_load(
        (ROOT / "install-plans/harness-maintenance.project.yml").read_text(encoding="utf-8")
    )

    assert generated == checked_in
    assert len({artifact["destination"] for artifact in generated["artifacts"]}) == 25
    assert generated["targets"] == ["project", "claude", "codex", "antigravity-cli"]
    config = next(
        artifact
        for artifact in generated["artifacts"]
        if artifact["destination"] == ".codex/config.toml"
    )
    assert len(config["component_ids"]) == 6
    assert config["merge_strategy"] == "toml-agents-merge"


def test_inspect_reports_current_owned_state_and_is_deterministic() -> None:
    first = project_surface.inspect_project_surface(ROOT)
    second = project_surface.inspect_project_surface(ROOT)

    assert first == second
    assert first["counts"]["expected"] == 25
    assert first["counts"]["extra"] == len(first["owned_extra_files"])
    assert first["counts"]["registration_mismatch"] == (
        len(first["codex_agent_tables"]["missing"])
        + len(first["codex_agent_tables"]["mismatched"])
        + len(first["codex_agent_tables"]["owned_extra"])
    )
    assert first["foreign_extra_files"] == []
    assert first["codex_agent_tables"]["foreign_extra"] == []


def test_current_removal_evidence_validates_without_mutation() -> None:
    report = project_surface.inspect_project_surface(ROOT)
    manifest = _manifest_from_report(report)
    before = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout

    result = project_surface.validate_removal_manifest(
        manifest, repo_root=ROOT, schema_path=SCHEMA
    )

    after = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout
    assert result["status"] == "pass"
    assert result["remove_file_count"] == len(manifest["remove_files"])
    assert result["remove_codex_agent_table_count"] == len(
        manifest["remove_codex_agent_tables"]
    )
    assert after == before


def test_isolated_applied_surface_is_a_fixed_point(tmp_path: Path) -> None:
    plan = install_plan.build_plan(
        "harness-maintenance", scope="project", mode="apply"
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    repo = tmp_path / "fixed-project"
    repo.mkdir()

    assert apply_plan(plan_path, target_root=repo) == 0
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=HarnessKit Test",
            "-c",
            "user.email=harnesskit@" + "example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )

    first = project_surface.inspect_project_surface(repo)
    second = project_surface.inspect_project_surface(repo)

    assert first == second
    assert first["counts"] == {
        "expected": 25,
        "observed": 25,
        "missing": 0,
        "extra": 0,
        "content_mismatch": 0,
        "registration_mismatch": 0,
    }
    assert first["owned_extra_files"] == []
    assert first["codex_agent_tables"]["owned_extra"] == []


@pytest.mark.parametrize(
    "path, message",
    [
        ("/tmp/absolute", "unsafe repository-relative"),
        (".agents/skills/../escape", "unsafe repository-relative"),
        (".agents/skills/..\\escape", "unsafe repository-relative"),
        (".agents/skills/*/SKILL.md", "glob syntax"),
        ("components/rules/old/component.yml", "outside approved install surfaces"),
        (".codex/specs/old.md", "outside approved install surfaces"),
        ("AGENTS.md", "cannot be removed"),
    ],
)
def test_removal_path_safety_fails_closed(path: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        project_surface._validate_removal_path(path)


@pytest.mark.parametrize(
    "path",
    ["AGENTS.md.bak", "CLAUDE.md.old", ".codex/config.toml.backup"],
)
def test_exact_file_surfaces_reject_near_prefix_paths(path: str) -> None:
    assert project_surface._is_allowed_surface_path(path) is False
    with pytest.raises(ValueError, match="outside approved install surfaces"):
        project_surface._validate_removal_path(path)


def test_schema_rejects_absolute_path() -> None:
    report = project_surface.inspect_project_surface(ROOT)
    manifest = _manifest_from_report(report)
    manifest["remove_files"].append(
        _synthetic_file_evidence(path="/tmp/absolute")
    )
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(manifest)


def test_validate_rejects_stale_plan_hash() -> None:
    report = project_surface.inspect_project_surface(ROOT)
    manifest = _manifest_from_report(report)
    manifest["source_plan"]["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="source_plan"):
        project_surface.validate_removal_manifest(
            manifest, repo_root=ROOT, schema_path=SCHEMA
        )


def test_validate_rejects_unknown_file_ownership() -> None:
    report = project_surface.inspect_project_surface(ROOT)
    manifest = _manifest_from_report(report)
    manifest["remove_files"].append(_synthetic_file_evidence())
    manifest["remove_files"].sort(key=lambda entry: entry["path"])

    with pytest.raises(ValueError, match="owned-extra evidence"):
        project_surface.validate_removal_manifest(
            manifest, repo_root=ROOT, schema_path=SCHEMA
        )


def test_validate_rejects_unknown_codex_table_ownership() -> None:
    report = project_surface.inspect_project_surface(ROOT)
    manifest = _manifest_from_report(report)
    manifest["remove_codex_agent_tables"].append(_synthetic_table_evidence())
    manifest["remove_codex_agent_tables"].sort(key=lambda entry: entry["table"])

    with pytest.raises(ValueError, match="owned-extra evidence"):
        project_surface.validate_removal_manifest(
            manifest, repo_root=ROOT, schema_path=SCHEMA
        )


def test_tracked_surface_rejects_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    target = repo / "outside.md"
    target.write_text("outside\n", encoding="utf-8")
    link = repo / ".agents/skills/example/SKILL.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    subprocess.run(["git", "-C", str(repo), "add", ".agents/skills/example/SKILL.md"], check=True)

    with pytest.raises(ValueError, match="contains symlink"):
        project_surface._tracked_surface(repo)


def test_cli_exposes_only_inspect_and_validate() -> None:
    result = subprocess.run(
        [
            "python",
            "scripts/install/project_surface.py",
            "--help",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "inspect" in result.stdout
    assert "validate" in result.stdout
    assert "apply" not in result.stdout
    assert "delete" not in result.stdout
    assert "prune" not in result.stdout
