"""Part A — install selection modes: composite expansion, member-block,
profile-with-composite-ref, and selection-mode regression.

These exercise scripts/install/plan.py's three selection modes against the real
registered composite (harnesskit.composite.atlassian-acli) and its members:

  - acli-gateway: standalone_installable absent -> True
  - acli-read:    standalone_installable: false
  - jira-write: standalone_installable: false

None of the three members declare an explicit `scopes`, so each defaults to
["project", "user"]; the composite's shared scope is their INTERSECTION,
["project", "user"].
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.install import plan as install_plan
from scripts.install.common import validate_plan_contract


ROOT = Path(__file__).resolve().parents[2]

COMPOSITE_ID = "harnesskit.composite.atlassian-acli"
COMPOSITE_MEMBERS = [
    "harnesskit.skill.acli-gateway",
    "harnesskit.skill.acli-read",
    "harnesskit.skill.jira-write",
]
BLOCKED_MEMBERS = [
    "harnesskit.skill.acli-read",
    "harnesskit.skill.jira-write",
]
STANDALONE_OK_MEMBER = "harnesskit.skill.acli-gateway"


def _load_yaml(rel_path: str) -> dict:
    data = yaml.safe_load((ROOT / rel_path).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _run_plan(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/install/plan.py", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


# ── 1. --composite expansion (with intersection scope) ──────────────────────


class TestCompositeExpansion:
    def test_composite_members_resolve_in_order(self):
        assert install_plan._composite_members(COMPOSITE_ID) == COMPOSITE_MEMBERS

    def test_composite_shared_scope_is_member_intersection(self):
        # Every member defaults to ["project", "user"]; intersection is the same.
        assert install_plan._composite_shared_scope(COMPOSITE_MEMBERS) == [
            "project",
            "user",
        ]

    def test_build_composite_plan_installs_member_group(self):
        plan = install_plan.build_composite_plan(
            COMPOSITE_ID, scope="project", mode="dry-run"
        )

        assert plan["plan_id"] == (
            "harnesskit.install-plan.composite.atlassian-acli.project"
        )
        assert "profile_id" not in plan
        assert plan["scope"] == "project"
        assert plan["components"] == COMPOSITE_MEMBERS

    def test_composite_plan_infers_targets_and_runtime_surfaces(self):
        plan = install_plan.build_composite_plan(
            COMPOSITE_ID, scope="project", mode="apply"
        )

        validate_plan_contract(plan)
        assert plan["targets"]
        assert plan["runtime_surfaces"]

    def test_composite_plan_available_at_each_shared_scope(self):
        for scope in ("project", "user"):
            plan = install_plan.build_composite_plan(
                COMPOSITE_ID, scope=scope, mode="dry-run"
            )
            assert plan["scope"] == scope
            assert plan["components"] == COMPOSITE_MEMBERS

    def test_composite_plan_rejects_scope_outside_intersection(self, monkeypatch):
        # Force a disjoint-ish intersection so the requested scope falls outside it.
        def fake_scopes(component_id: str, _entry: dict) -> list[str]:
            if component_id == "harnesskit.skill.acli-read":
                return ["user"]
            return ["project"]

        monkeypatch.setattr(install_plan, "_component_scopes", fake_scopes)

        with pytest.raises(ValueError, match="scope is not in member-shared scope"):
            install_plan.build_composite_plan(
                COMPOSITE_ID, scope="project", mode="dry-run"
            )

    def test_composite_cli_expands_to_members(self):
        result = _run_plan(
            "--composite",
            COMPOSITE_ID,
            "--scope",
            "project",
            "--mode",
            "dry-run",
            "--format",
            "json",
        )

        assert result.returncode == 0, result.stdout + result.stderr
        plan = json.loads(result.stdout)
        assert plan["components"] == COMPOSITE_MEMBERS


# ── 2. standalone_installable blocking ──────────────────────────────────────


class TestMemberBlock:
    @pytest.mark.parametrize("component_id", BLOCKED_MEMBERS)
    def test_blocked_member_cannot_install_standalone(self, component_id: str):
        with pytest.raises(
            ValueError,
            match=f"not standalone-installable.*{component_id}",
        ):
            install_plan.build_component_plan(
                component_id, scope="project", mode="dry-run"
            )

    def test_block_lives_in_isolated_validator(self):
        # The isolated validator raises directly, independent of scope-allow logic.
        with pytest.raises(ValueError, match="not standalone-installable"):
            install_plan._validate_member_block_for_standalone(BLOCKED_MEMBERS[:1])

        # And the scope-allow validator must NOT carry member-block logic, so a
        # blocked member passes it cleanly (project is a valid scope for it).
        install_plan._validate_components_allowed_for_scope(
            BLOCKED_MEMBERS[:1], "project"
        )

    def test_standalone_ok_member_installs_standalone(self):
        plan = install_plan.build_component_plan(
            STANDALONE_OK_MEMBER, scope="project", mode="dry-run"
        )
        assert plan["components"] == [STANDALONE_OK_MEMBER]

    def test_blocked_member_installs_via_composite(self):
        # The exact member that is refused standalone is installable via composite.
        plan = install_plan.build_composite_plan(
            COMPOSITE_ID, scope="project", mode="dry-run"
        )
        for blocked in BLOCKED_MEMBERS:
            assert blocked in plan["components"]

    @pytest.mark.parametrize("component_id", BLOCKED_MEMBERS)
    def test_blocked_member_cli_returns_nonzero(self, component_id: str):
        result = _run_plan(
            "--component",
            component_id,
            "--scope",
            "project",
            "--mode",
            "dry-run",
            "--format",
            "json",
        )
        assert result.returncode != 0
        assert "not standalone-installable" in result.stderr


# ── 3. profile-with-composite-ref planning ──────────────────────────────────


class TestProfileWithCompositeRef:
    """A profile MAY list a composite id; planning expands it to members and the
    member-block must NOT fire on the profile path (composite/profile install is
    the permitted route for blocked members).

    Uses a temp fixture profile (monkeypatched PROFILES_DIR) so the existing
    profiles/ contract tests stay untouched — no real profile is rewired.
    """

    def _write_fixture_profile(self, tmp_path: Path, components: list[str]) -> Path:
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        profile = {
            "profile_id": "harnesskit.profile.composite-fixture",
            "status": "draft",
            "title": "Composite Fixture",
            "summary": "Test fixture profile that selects a composite id.",
            "components": components,
            "targets": ["project", "claude", "codex"],
            "install_policy": {
                "default_scope": "project",
                "allowed_scopes": ["project", "user"],
                "activation_policy": "manual",
            },
        }
        (profiles_dir / "composite-fixture.yml").write_text(
            yaml.safe_dump(profile, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return profiles_dir

    def test_profile_composite_ref_expands_to_members(self, tmp_path, monkeypatch):
        profiles_dir = self._write_fixture_profile(tmp_path, [COMPOSITE_ID])
        monkeypatch.setattr(install_plan, "PROFILES_DIR", profiles_dir)

        plan = install_plan.build_plan(
            "composite-fixture", scope="project", mode="dry-run"
        )

        assert plan["profile_id"] == "harnesskit.profile.composite-fixture"
        for member in COMPOSITE_MEMBERS:
            assert member in plan["components"]
        # The composite id itself never leaks into the resolved component list.
        assert COMPOSITE_ID not in plan["components"]

    def test_profile_path_does_not_trigger_member_block(self, tmp_path, monkeypatch):
        # Blocked members (acli-read/write) come in via the composite; the
        # profile path must plan successfully without member-block refusal.
        profiles_dir = self._write_fixture_profile(tmp_path, [COMPOSITE_ID])
        monkeypatch.setattr(install_plan, "PROFILES_DIR", profiles_dir)

        plan = install_plan.build_plan(
            "composite-fixture", scope="project", mode="dry-run"
        )

        for blocked in BLOCKED_MEMBERS:
            assert blocked in plan["components"]

    def test_profile_composite_dedupes_against_plain_component(
        self, tmp_path, monkeypatch
    ):
        # A composite ref plus a plain ref to one of its members must not
        # duplicate that member in the resolved list.
        profiles_dir = self._write_fixture_profile(
            tmp_path, [COMPOSITE_ID, STANDALONE_OK_MEMBER]
        )
        monkeypatch.setattr(install_plan, "PROFILES_DIR", profiles_dir)

        plan = install_plan.build_plan(
            "composite-fixture", scope="project", mode="dry-run"
        )

        assert plan["components"].count(STANDALONE_OK_MEMBER) == 1


# ── 4. selection-mode regression: plain --profile / --component unchanged ────


class TestSelectionModeRegression:
    def test_plain_profile_matches_checked_in_contract(self):
        # The flagship profile plan must remain byte-equivalent to the checked-in
        # static contract, proving Part A did not alter the --profile path.
        result = _run_plan(
            "--profile",
            "engineering",
            "--scope",
            "project",
            "--mode",
            "dry-run",
            "--format",
            "json",
        )
        assert result.returncode == 0, result.stdout + result.stderr

        generated = json.loads(result.stdout)
        checked_in = _load_yaml("install-plans/engineering.project.yml")
        assert generated == checked_in

    def test_plain_profile_function_path_unchanged(self):
        plan = install_plan.build_plan("scm", scope="project", mode="dry-run")
        assert plan["profile_id"] == "harnesskit.profile.scm"
        assert "harnesskit.skill.github-workflow-policy" in plan["components"]

    def test_plain_component_install_unchanged(self):
        # A standalone, non-blocked component still plans as a single-component
        # selection with no profile_id and the member-block does not interfere.
        plan = install_plan.build_component_plan(
            STANDALONE_OK_MEMBER, scope="project", mode="dry-run"
        )
        assert plan["plan_id"] == "harnesskit.install-plan.component.project"
        assert "profile_id" not in plan
        assert plan["components"] == [STANDALONE_OK_MEMBER]

    def test_plain_component_plan_infers_targets_and_runtime_surfaces(self):
        plan = install_plan.build_component_plan(
            STANDALONE_OK_MEMBER, scope="project", mode="apply"
        )

        validate_plan_contract(plan)
        assert plan["targets"]
        assert plan["runtime_surfaces"]

    def test_plain_component_cli_unchanged(self):
        result = _run_plan(
            "--component",
            STANDALONE_OK_MEMBER,
            "--scope",
            "project",
            "--mode",
            "dry-run",
            "--format",
            "json",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        plan = json.loads(result.stdout)
        validate_plan_contract(plan)
        assert plan["components"] == [STANDALONE_OK_MEMBER]

    def test_profile_extra_component_path_still_works(self):
        # --profile + --component (extra) is the pre-A additive behavior; it must
        # still resolve and is NOT subject to the standalone member-block.
        plan = install_plan.build_plan(
            "minimal",
            scope="project",
            mode="dry-run",
            extra_components=[STANDALONE_OK_MEMBER],
        )
        assert plan["profile_id"] == "harnesskit.profile.minimal"
        assert STANDALONE_OK_MEMBER in plan["components"]
