from __future__ import annotations

from pathlib import Path

import pytest

from scripts.projection import manifest as projection_manifest


ROOT = Path(__file__).resolve().parents[2]
# The projection test basename is intentionally unique across test directories.
COMPONENT_ID = "harnesskit.rule.harness-maintenance-project-context"


def test_public_closure_is_order_preserving_union_with_project_components() -> None:
    profile = {
        "components": ["harnesskit.skill.one", "harnesskit.agent.two"],
        "install_policy": {
            "allowed_scopes": ["project"],
            "scope_components": {
                "project": ["harnesskit.agent.two", "harnesskit.rule.three"]
            },
        },
    }

    assert projection_manifest._public_component_ids(profile) == [
        "harnesskit.skill.one",
        "harnesskit.agent.two",
        "harnesskit.rule.three",
    ]


@pytest.mark.parametrize(
    "install_policy",
    [
        {"allowed_scopes": ["project", "user"], "scope_components": {}},
        {
            "allowed_scopes": ["project"],
            "scope_components": {"user": ["harnesskit.skill.private"]},
        },
        {
            "allowed_scopes": ["project"],
            "default_scope": "user",
            "scope_components": {},
        },
    ],
)
def test_public_closure_rejects_user_scope(install_policy: dict) -> None:
    with pytest.raises(ValueError, match="project-scope-only"):
        projection_manifest._public_component_ids(
            {"components": [], "install_policy": install_policy}
        )


def test_projected_maintenance_rule_is_public_safe() -> None:
    manifest = projection_manifest.build_manifest(
        ROOT / "publish/harnesskit.yml",
        ROOT,
    )
    rules = manifest["components"]["rules"]
    assert [rule["id"] for rule in rules] == [COMPONENT_ID]
    assert manifest["closure_summary"] == {
        "agents": 6,
        "rules": 1,
        "skills": 5,
        "workflows": 1,
    }

    included = set(manifest["included_paths"])
    assert "components/rules/harness-maintenance-project-context/PROJECT_CONTEXT.md" in included
    assert "sources/harness-maintenance-project-context.yml" in included
    assert "components/rules/harnesskit-project-context/component.yml" not in included
    assert "profiles/engineering.yml" not in included

    body = (ROOT / "components/rules/harness-maintenance-project-context/PROJECT_CONTEXT.md").read_text(
        encoding="utf-8"
    )
    source_registry = (ROOT / "sources/registry.yml").read_text(encoding="utf-8")
    assert "harness-maintenance-project-context:" in source_registry
    assert "HarnessKit `harness-maintenance` profile" in body
    for forbidden in (
        "engineering profile",
        "doc-curator",
        "human-doc",
        "user-level",
        "/Users/",
        "runtime_supported",
    ):
        assert forbidden not in body
