from __future__ import annotations

from pathlib import Path

import pytest

from scripts.profiles.selection import (
    PROFILE_WORKFLOW_MEMBERSHIP_FORBIDDEN,
    ProfileSelectionError,
)
from scripts.projection import manifest as projection_manifest


build_manifest = projection_manifest.build_manifest


SKILL_ID = "harnesskit.skill.public"
WORKFLOW_A = "harnesskit.workflow.alpha"
WORKFLOW_B = "harnesskit.workflow.beta"


def _write_fixture(
    root: Path,
    *,
    public_workflows: list[str],
    workflow_manifest_kind: str = "workflow",
) -> Path:
    (root / "profiles").mkdir(parents=True)
    (root / "components" / "skill").mkdir(parents=True)
    (root / "components" / "workflow-a").mkdir(parents=True)
    (root / "components" / "workflow-b").mkdir(parents=True)

    (root / "profiles" / "public.yml").write_text(
        "\n".join(
            [
                "profile_id: harnesskit.profile.public",
                "components:",
                f"  - {SKILL_ID}",
                "install_policy:",
                "  default_scope: project",
                "  allowed_scopes:",
                "    - project",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "components" / "registry.yml").write_text(
        "\n".join(
            [
                'version: "1"',
                "components:",
                f"  {SKILL_ID}:",
                "    kind: skill",
                "    path: components/skill/component.yml",
                f"  {WORKFLOW_A}:",
                "    kind: workflow",
                "    path: components/workflow-a/workflow.yml",
                f"  {WORKFLOW_B}:",
                "    kind: workflow",
                "    path: components/workflow-b/workflow.yml",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "components" / "skill" / "component.yml").write_text(
        "\n".join([f"component_id: {SKILL_ID}", "kind: skill"]) + "\n",
        encoding="utf-8",
    )
    for directory, workflow_id in (
        ("workflow-a", WORKFLOW_A),
        ("workflow-b", WORKFLOW_B),
    ):
        (root / "components" / directory / "workflow.yml").write_text(
            "\n".join(
                [
                    f"workflow_id: {workflow_id}",
                    f"kind: {workflow_manifest_kind}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    policy_path = root / "policy.yml"
    policy_lines = [
        "projection_id: harnesskit.projection.public",
        "source_profile: harnesskit.profile.public",
        "source_profile_path: profiles/public.yml",
        "registry_path: components/registry.yml",
        "public_workflows:",
        *[f"  - {workflow_id}" for workflow_id in public_workflows],
        "support_paths: []",
        "excluded_patterns: []",
    ]
    policy_path.write_text("\n".join(policy_lines) + "\n", encoding="utf-8")
    return policy_path


def test_explicit_public_workflows_are_profile_independent_and_stably_ordered(tmp_path: Path):
    policy_path = _write_fixture(
        tmp_path,
        public_workflows=[WORKFLOW_B, WORKFLOW_A],
    )

    manifest = build_manifest(policy_path, tmp_path)

    assert manifest["explicit_public_workflows"] == [WORKFLOW_A, WORKFLOW_B]
    assert manifest["closure_summary"] == {
        "agents": 0,
        "rules": 0,
        "skills": 1,
        "workflows": 2,
    }
    assert [item["id"] for item in manifest["components"]["workflows"]] == [
        WORKFLOW_A,
        WORKFLOW_B,
    ]


def test_duplicate_explicit_public_workflow_is_rejected(tmp_path: Path):
    policy_path = _write_fixture(
        tmp_path,
        public_workflows=[WORKFLOW_A, WORKFLOW_A],
    )

    with pytest.raises(ValueError, match="duplicate public workflow"):
        build_manifest(policy_path, tmp_path)


def test_unknown_explicit_public_workflow_is_rejected(tmp_path: Path):
    policy_path = _write_fixture(
        tmp_path,
        public_workflows=["harnesskit.workflow.unknown"],
    )

    with pytest.raises(ValueError, match="public workflow is not registered"):
        build_manifest(policy_path, tmp_path)


def test_non_workflow_explicit_root_is_rejected(tmp_path: Path):
    policy_path = _write_fixture(tmp_path, public_workflows=[SKILL_ID])

    with pytest.raises(ValueError, match="public workflow registry kind must be workflow"):
        build_manifest(policy_path, tmp_path)


def test_registry_and_manifest_workflow_kind_must_match(tmp_path: Path):
    policy_path = _write_fixture(
        tmp_path,
        public_workflows=[WORKFLOW_A],
        workflow_manifest_kind="skill",
    )

    with pytest.raises(ValueError, match="public workflow manifest kind must be workflow"):
        build_manifest(policy_path, tmp_path)


def test_source_profile_workflow_membership_is_rejected_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    policy_path = _write_fixture(tmp_path, public_workflows=[])
    profile_path = tmp_path / "profiles" / "public.yml"
    profile_path.write_text(
        "\n".join(
            [
                "profile_id: harnesskit.profile.public",
                "components:",
                f"  - {SKILL_ID}",
                f"  - {WORKFLOW_A}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    publish_assembly_started = False

    def fail_if_publish_assembly_starts(*_args, **_kwargs):
        nonlocal publish_assembly_started
        publish_assembly_started = True
        raise AssertionError("publish assembly started before Profile selection validation")

    monkeypatch.setattr(
        projection_manifest,
        "_add_required_path",
        fail_if_publish_assembly_starts,
    )

    with pytest.raises(ProfileSelectionError) as caught:
        build_manifest(policy_path, tmp_path)

    assert caught.value.code == PROFILE_WORKFLOW_MEMBERSHIP_FORBIDDEN
    assert caught.value.profile_id == "harnesskit.profile.public"
    assert caught.value.field_path == "components[1]"
    assert caught.value.component_id == WORKFLOW_A
    assert publish_assembly_started is False
