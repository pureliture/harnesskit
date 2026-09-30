from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from scripts.install import plan as install_plan
from scripts.install.common import validate_plan_contract


ROOT = Path(__file__).resolve().parents[2]


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


def test_plan_cli_generates_engineering_install_plan_json():
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

    plan = json.loads(result.stdout)
    profile = _load_yaml("profiles/engineering.yml")

    assert plan["plan_id"] == "harnesskit.install-plan.engineering.project"
    assert plan["profile_id"] == profile["profile_id"]
    assert plan["scope"] == "project"
    assert plan["mode"] == "dry-run"
    assert plan["targets"] == profile["targets"]
    project_scoped = profile["install_policy"]["scope_components"].get("project", [])
    assert plan["components"] == profile["components"] + project_scoped
    destinations = {artifact["destination"] for artifact in plan["artifacts"]}
    assert ".claude/skills/doc-curator/SKILL.md" in destinations
    assert ".codex/hooks.json" not in destinations
    assert ".agents/hooks.json" not in destinations
    assert ".harnesskit/scripts/human_doc_turn_scan.py" not in destinations


def test_plan_user_scope_includes_codex_user_hook_registration_only_in_user_scope():
    user_result = _run_plan(
        "--profile",
        "engineering",
        "--scope",
        "user",
        "--mode",
        "dry-run",
        "--format",
        "json",
    )
    project_result = _run_plan(
        "--profile",
        "engineering",
        "--scope",
        "project",
        "--mode",
        "dry-run",
        "--format",
        "json",
    )

    assert user_result.returncode == 0, user_result.stdout + user_result.stderr
    assert project_result.returncode == 0, project_result.stdout + project_result.stderr

    user_plan = json.loads(user_result.stdout)
    project_plan = json.loads(project_result.stdout)
    user_hook_artifacts = [
        artifact
        for artifact in user_plan["artifacts"]
        if artifact["target"] == "codex"
        and artifact["destination"] == ".codex/hooks.json"
        and "harnesskit.hook.optimal-response-prompt-submit"
        in artifact.get("component_ids", [artifact["component_id"]])
    ]
    project_hook_artifacts = [
        artifact
        for artifact in project_plan["artifacts"]
        if artifact["target"] == "codex"
        and artifact["destination"] == ".codex/hooks.json"
    ]

    assert user_hook_artifacts == [
        {
            "component_id": "harnesskit.hook.optimal-response-prompt-submit",
            "component_ids": ["harnesskit.hook.optimal-response-prompt-submit"],
            "target": "codex",
            "source": "dist/codex/.codex/hooks.user.json",
            "destination": ".codex/hooks.json",
            "merge_strategy": "json-deep-merge",
            "json_merge_key": "codex-hooks",
            "scope": "user",
            "ownership": {
                "type": "managed-hook-group",
                "managed_group_id": "optimal-response.codex-user-prompt-submit",
                "merge_policy": "json-deep-merge",
                "preserve_foreign_entries": True,
            },
            "runtime_contract": {
                "surface": "codex-user-hooks",
                "event": "UserPromptSubmit",
                "path": "${CODEX_HOME:-$HOME/.codex}/hooks.json",
            },
            "evidence_status": {
                "status": "build_only",
                "reason": "OR-02 statically builds the compact marker contract; the installed Codex hook has not observed this changed semantic revision.",
            },
            "historical_predecessor_evidence": {
                "status_at_observation": "runtime_supported",
                "latest_evidence_dir": "docs/runtime-evidence/codex-optimal-response/20260606T040248Z",
                "boundary": "The predecessor probe proved registration, hook execution, and STOP-off state, not the OR-02 compact marker or on-demand reference.",
            },
        }
    ]
    assert project_hook_artifacts == []


def test_plan_cli_matches_checked_in_static_contract():
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


def test_plan_cli_generates_scm_issue_template_project_surface():
    result = _run_plan(
        "--profile",
        "scm",
        "--scope",
        "project",
        "--mode",
        "dry-run",
        "--format",
        "json",
    )

    assert result.returncode == 0, result.stdout + result.stderr

    plan = json.loads(result.stdout)
    destinations = {artifact["destination"] for artifact in plan["artifacts"]}
    surfaces = {(surface["target"], surface["path"]) for surface in plan["runtime_surfaces"]}

    assert ".github/ISSUE_TEMPLATE/collaboration_idea.yml" in destinations
    assert ".github/ISSUE_TEMPLATE/config.yml" in destinations
    assert ("project", ".github/ISSUE_TEMPLATE") in surfaces


def test_work_profile_is_project_only_atlassian_plan():
    project_plan = install_plan.build_plan("work", scope="project", mode="dry-run")

    assert project_plan["components"] == [
        "harnesskit.skill.atlassian-work",
        "harnesskit.skill.acli-gateway",
        "harnesskit.skill.acli-read",
        "harnesskit.skill.jira-write",
        "harnesskit.skill.confluence-write",
        "harnesskit.skill.jira-ticket-management",
        "harnesskit.skill.confluence-page-authoring",
        "harnesskit.skill.atlassian-account-diagnostics",
        "harnesskit.agent.atlassian-work-worker",
    ]

    with pytest.raises(ValueError, match="scope is not allowed: user"):
        install_plan.build_plan("work", scope="user", mode="dry-run")


def test_scm_profile_uses_github_workflow_policy_for_project_and_user_scope():
    profile = _load_yaml("profiles/scm.yml")

    assert "harnesskit.skill.github-workflow-policy" in profile["components"]
    assert "harnesskit.skill.github-kanban-policy" not in profile["components"]
    assert profile["install_policy"]["allowed_scopes"] == ["project", "user"]

    project_plan = install_plan.build_plan("scm", scope="project", mode="dry-run")
    user_plan = install_plan.build_plan("scm", scope="user", mode="dry-run")
    dumped = json.dumps({"project": project_plan, "user": user_plan})

    assert project_plan["profile_id"] == "harnesskit.profile.scm"
    assert user_plan["profile_id"] == "harnesskit.profile.scm"
    assert "harnesskit.skill.github-workflow-policy" in project_plan["components"]
    assert "harnesskit.skill.github-workflow-policy" in user_plan["components"]
    assert "harnesskit.skill.capture-collaboration-idea" in project_plan["components"]
    assert "harnesskit.skill.capture-collaboration-idea" not in user_plan["components"]
    assert "github-kanban-policy" not in dumped

    destinations = {artifact["destination"] for artifact in user_plan["artifacts"]}
    surfaces = {
        (surface["target"], surface["path"])
        for surface in user_plan["runtime_surfaces"]
    }

    for slug in [
        "github-workflow-policy",
        "github-issue",
        "github-start",
        "github-pr",
        "github-pr-followup",
    ]:
        assert f".claude/skills/{slug}/SKILL.md" in destinations
        assert f".codex/skills/{slug}/SKILL.md" in destinations
        assert f".agents/skills/{slug}/SKILL.md" not in destinations

    assert ".claude/agents/github-manager.md" in destinations
    assert ".codex/agents/github_manager.toml" in destinations
    assert ".codex/config.toml" in destinations
    assert not any(destination.startswith(".github/") for destination in destinations)
    assert not any(destination.startswith(".agents/") for destination in destinations)
    assert ("claude", ".claude/skills") in surfaces
    assert ("claude", ".claude/agents") in surfaces
    assert ("codex", ".codex/skills") in surfaces
    assert ("codex", ".codex/agents") in surfaces
    assert ("codex", ".codex/config.toml") in surfaces
    assert ("project", ".github/ISSUE_TEMPLATE") not in surfaces


def test_user_scope_profiles_are_explicitly_allowlisted():
    allowed_user_scope_profiles = {
        "harnesskit.profile.development",
        "harnesskit.profile.engineering",
        "harnesskit.profile.scm",
    }

    profiles = [
        _load_yaml(str(path.relative_to(ROOT)))
        for path in sorted((ROOT / "profiles").glob("*.yml"))
    ]
    actual_user_scope_profiles = {
        profile["profile_id"]
        for profile in profiles
        if "user" in (profile.get("install_policy") or {}).get("allowed_scopes", [])
    }

    assert actual_user_scope_profiles == allowed_user_scope_profiles

    for profile in profiles:
        profile_id = profile["profile_id"]
        if profile_id in allowed_user_scope_profiles:
            continue
        with pytest.raises(ValueError, match="scope is not allowed: user"):
            install_plan.build_plan(profile_id, scope="user", mode="dry-run")


def test_hermes_external_package_plan_is_explicit_root_only_and_consent_pending():
    plan = install_plan.build_hermes_external_package_plan(
        lifecycle="install", mode="apply"
    )
    external = plan["hermes_external_package"]
    assert plan["targets"] == ["hermes"]
    assert plan["artifacts"] == []
    assert external["skills_source"] == "dist/hermes/external-package/skills"
    assert external["package_root"] == ".local/share/harnesskit/hermes/skills"
    assert external["config"] == ".hermes/config.yaml"
    assert external["hook_event"] == "pre_llm_call"
    assert "accept" not in json.dumps(plan).lower()


@pytest.mark.parametrize("field,value", [
    ("hook_command", "~/.hermes/harnesskit/hooks/optimal-response/other.cjs"),
    ("hook_event", "post_llm_call"),
    ("marker", "other-marker.json"),
    ("package_root", ".local/share/harnesskit/hermes/other"),
])
def test_hermes_external_plan_rejects_modified_contract_fields(field, value):
    plan = install_plan.build_hermes_external_package_plan(lifecycle="install", mode="apply")
    plan["hermes_external_package"][field] = value

    with pytest.raises(ValueError, match="invalid Hermes external package"):
        validate_plan_contract(plan)


def test_development_user_plan_declares_intent_guide_and_selected_destinations():
    plan = install_plan.build_plan("development", scope="user", mode="dry-run")

    assert plan["profile_id"] == "harnesskit.profile.development"
    assert plan["components"] == [
        "harnesskit.skill.intent-guide",
        "harnesskit.skill.grill",
        "harnesskit.skill.grill-to-spec",
        "harnesskit.skill.wayfinder",
        "harnesskit.skill.to-questionnaire",
    ]
    assert plan["targets"] == ["codex"]
    assert {artifact["destination"] for artifact in plan["artifacts"]} == {
        ".codex/skills/intent-guide/SKILL.md",
        ".codex/skills/grill/SKILL.md",
        ".codex/skills/grill-to-spec/SKILL.md",
        ".codex/skills/wayfinder/SKILL.md",
        ".codex/skills/to-questionnaire/SKILL.md",
    }
    assert {(surface["target"], surface["path"]) for surface in plan["runtime_surfaces"]} == {
        ("codex", ".codex/skills")
    }


def test_plan_cli_rejects_path_like_profile_names():
    result = _run_plan("--profile", "../engineering", "--format", "json")

    assert result.returncode != 0
    assert "Invalid profile name" in result.stderr


def test_plan_cli_rejects_scope_disallowed_by_profile_policy():
    result = _run_plan(
        "--profile",
        "minimal",
        "--scope",
        "user",
        "--format",
        "json",
    )

    assert result.returncode != 0
    assert "scope is not allowed: user" in result.stderr


@pytest.mark.parametrize(
    "component_id",
    [
        "harnesskit.skill.optimal-response",
        "harnesskit.hook.optimal-response-prompt-submit",
    ],
)
def test_plan_rejects_user_only_component_in_project_scope(component_id: str):
    with pytest.raises(
        ValueError,
        match=f"component is not allowed for scope project: {component_id}",
    ):
        install_plan.build_plan(
            "engineering",
            scope="project",
            mode="dry-run",
            extra_components=[component_id],
        )


def test_plan_cli_rejects_workspace_scope_option():
    result = _run_plan(
        "--profile",
        "engineering",
        "--scope",
        "workspace",
        "--mode",
        "dry-run",
        "--format",
        "json",
    )

    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_plan_rejects_duplicate_non_append_artifact_destinations(monkeypatch):
    def fake_entries(component_ids: list[str]) -> dict[str, dict]:
        return {component_id: {} for component_id in component_ids}

    def fake_render_component(component_id: str, _entry: dict):
        return [
            (
                ROOT / "dist/claude/.claude/skills/shared/SKILL.md",
                f"{component_id}\n",
                False,
            )
        ]

    monkeypatch.setattr(install_plan, "selected_registry_entries", fake_entries)
    monkeypatch.setattr(install_plan, "render_component", fake_render_component)

    with pytest.raises(ValueError, match="Duplicate adapter output|duplicate non-append artifact destination"):
        install_plan._artifacts(["harnesskit.skill.alpha", "harnesskit.skill.beta"])


def test_plan_allows_shared_final_destination_only_when_sources_match(monkeypatch):
    def fake_entries(component_ids: list[str]) -> dict[str, dict]:
        return {component_id: {} for component_id in component_ids}

    def fake_render_component(component_id: str, _entry: dict):
        target = "codex" if component_id.endswith("codex") else "antigravity"
        return [
            (
                ROOT / f"dist/{target}/.agents/skills/shared/SKILL.md",
                "shared byte-identical content\n",
                False,
            )
        ]

    monkeypatch.setattr(install_plan, "selected_registry_entries", fake_entries)
    monkeypatch.setattr(install_plan, "render_component", fake_render_component)
    monkeypatch.setattr(
        install_plan,
        "_component_scopes",
        lambda _component_id, _entry: ["project"],
    )

    plan = install_plan._selection_plan(
        ["harnesskit.skill.shared-codex", "harnesskit.skill.shared-antigravity"],
        plan_id="test.shared.project",
        scope="project",
        mode="dry-run",
    )

    assert [artifact["destination"] for artifact in plan["artifacts"]] == [
        ".agents/skills/shared/SKILL.md",
        ".agents/skills/shared/SKILL.md",
    ]


def test_plan_rejects_shared_final_destination_when_sources_diverge(monkeypatch):
    def fake_entries(component_ids: list[str]) -> dict[str, dict]:
        return {component_id: {} for component_id in component_ids}

    def fake_render_component(component_id: str, _entry: dict):
        target = "codex" if component_id.endswith("codex") else "antigravity"
        return [
            (
                ROOT / f"dist/{target}/.agents/skills/shared/SKILL.md",
                f"{component_id} specific content\n",
                False,
            )
        ]

    monkeypatch.setattr(install_plan, "selected_registry_entries", fake_entries)
    monkeypatch.setattr(install_plan, "render_component", fake_render_component)
    monkeypatch.setattr(
        install_plan,
        "_component_scopes",
        lambda _component_id, _entry: ["project"],
    )

    with pytest.raises(
        ValueError,
        match="final destination has divergent source content: .agents/skills/shared/SKILL.md",
    ):
        install_plan._selection_plan(
            ["harnesskit.skill.shared-codex", "harnesskit.skill.shared-antigravity"],
            plan_id="test.shared.project",
            scope="project",
            mode="dry-run",
        )


def test_apply_mode_plan_reports_adapter_output_materialization():
    plan = install_plan.build_plan("harness-maintenance", scope="project", mode="apply")

    assert plan["materialization"] == {
        "adapter_outputs": "materialized",
        "generated_source_root": "dist",
        "read_only": False,
    }


def test_plan_normalizes_project_artifact_sources_under_dist_project():
    source = install_plan._artifact_source_for_output(
        ROOT / ".acli/acli-gateway.sh",
        "project",
        ".acli/acli-gateway.sh",
    )

    assert source == ROOT / "dist/project/.acli/acli-gateway.sh"


def test_plan_project_runtime_routing_follows_config(monkeypatch):
    # plan.py must derive the project-runtime root from the shared config
    # (adapters/project/adapter.yml), not a hardcoded literal.
    assert ".acli" in install_plan.project_runtime_roots()
    monkeypatch.setattr(install_plan, "project_runtime_roots", lambda: (".acli", "customroot"))
    assert install_plan._artifact_target_and_destination(
        ROOT / "customroot/foo.sh"
    ) == ("project", "customroot/foo.sh")


def test_plan_rejects_components_whose_explicit_scopes_do_not_include_scope(monkeypatch):
    component_id = "harnesskit.skill.project-only"

    monkeypatch.setattr(
        install_plan,
        "selected_registry_entries",
        lambda component_ids: {component_id: {} for component_id in component_ids},
    )
    monkeypatch.setattr(
        install_plan,
        "_component_scopes",
        lambda _component_id, _entry: ["project"],
    )

    with pytest.raises(
        ValueError,
        match=f"component is not allowed for scope user: {component_id}",
    ):
        install_plan._validate_components_allowed_for_scope([component_id], "user")


def test_plan_allows_duplicate_append_artifact_destinations(monkeypatch):
    def fake_entries(component_ids: list[str]) -> dict[str, dict]:
        return {component_id: {} for component_id in component_ids}

    def fake_render_component(component_id: str, _entry: dict):
        return [
            (
                ROOT / "dist/codex/.codex/config.toml",
                f"[agents.{component_id}]\n",
                True,
            )
        ]

    monkeypatch.setattr(install_plan, "selected_registry_entries", fake_entries)
    monkeypatch.setattr(install_plan, "render_component", fake_render_component)

    artifacts = install_plan._artifacts(["harnesskit.agent.alpha", "harnesskit.agent.beta"])

    # The aggregated `.codex/config.toml` registration artifact carries the
    # preserving toml-agents-merge contract (strategy + key) so a real user
    # config is never clobbered; ownership stays on the aggregated
    # component_ids rather than a per-agent merge_artifacts block.
    assert artifacts == [
        {
            "component_id": "harnesskit.agent.alpha",
            "component_ids": ["harnesskit.agent.alpha", "harnesskit.agent.beta"],
            "target": "codex",
            "source": "dist/codex/.codex/config.toml",
            "destination": ".codex/config.toml",
            "merge_strategy": "toml-agents-merge",
            "toml_merge_key": "codex-agents",
        }
    ]


def test_plan_codex_config_references_only_selected_profile_agent_files():
    result = _run_plan(
        "--profile",
        "harness-maintenance",
        "--scope",
        "project",
        "--mode",
        "dry-run",
        "--format",
        "json",
    )

    assert result.returncode == 0, result.stdout + result.stderr

    plan = json.loads(result.stdout)
    destinations = {
        artifact["destination"]
        for artifact in plan["artifacts"]
        if artifact["target"] == "codex"
    }
    adapter_outputs = install_plan._collect_adapter_outputs(plan["components"])
    codex_config_output = adapter_outputs[ROOT / "dist/codex/.codex/config.toml"]
    codex_config = tomllib.loads(codex_config_output["content"])
    agents = codex_config["agents"]

    assert "human_doc_curator" not in agents
    for registration in agents.values():
        destination = f".codex/{registration['config_file']}"
        assert destination in destinations


def test_artifact_projection_is_profile_free_sorted_and_read_only(monkeypatch):
    monkeypatch.setattr(
        install_plan,
        "_materialize_adapter_outputs",
        lambda _component_ids: pytest.fail("projection must not materialize adapter output"),
    )

    first = install_plan.build_artifact_projection(
        ["harnesskit.agent.adapter-author"]
    )
    second = install_plan.build_artifact_projection(
        ["harnesskit.agent.adapter-author"]
    )

    assert first == second
    assert first["generator_version"] == "canonical-artifact-generator-v1"
    assert len(first["adapter_set_revision"]) == 64
    assert first["issues"] == []
    identities = [
        (
            record["component_id"],
            record["adapter_id"],
            record["target"],
            record["scope"],
            record["destination"],
            record.get("config_entry_locator") or "",
            record["content_sha256"],
        )
        for record in first["records"]
    ]
    assert identities == sorted(identities)

    codex_agent = next(
        record
        for record in first["records"]
        if record["target"] == "codex"
        and record["scope"] == "project"
        and record["destination"] == ".codex/agents/adapter_author.toml"
    )
    assert codex_agent["content_sha256"] == (
        "a72079a857edf4a816fed92f62b8b8b8ce557a39debecbe6465947867cda98d0"
    )
    assert codex_agent["exact_text"].startswith("name = \"adapter_author\"")
    assert "Adapter Author" in codex_agent["exact_text"]
    assert "어댑터 작성자" not in codex_agent["exact_text"]


def test_install_plan_and_projection_share_exact_artifact_bytes_and_hash():
    component_id = "harnesskit.agent.adapter-author"
    canonical = install_plan._artifacts(
        [component_id],
        materialize_adapter_outputs=False,
        include_internal_hashes=True,
        include_internal_content=True,
    )
    canonical = install_plan._scope_filtered_artifacts(
        canonical,
        scope="project",
        materialize_adapter_outputs=False,
    )
    install_record = next(
        artifact
        for artifact in canonical
        if artifact["target"] == "codex"
        and artifact["destination"] == ".codex/agents/adapter_author.toml"
    )
    projection_record = next(
        record
        for record in install_plan.build_artifact_projection([component_id])["records"]
        if record["target"] == "codex"
        and record["scope"] == "project"
        and record["destination"] == ".codex/agents/adapter_author.toml"
    )

    assert projection_record["exact_text"] == install_record["_exact_text"]
    assert projection_record["content_sha256"] == install_record["_content_sha256"]
    assert projection_record["source"] == install_record["source"]


def test_artifact_projection_expands_composites_without_emitting_group_identity():
    projection = install_plan.build_artifact_projection(
        ["harnesskit.composite.atlassian-acli"]
    )
    component_ids = {record["component_id"] for record in projection["records"]}

    assert "harnesskit.composite.atlassian-acli" not in component_ids
    assert {
        "harnesskit.skill.acli-gateway",
        "harnesskit.skill.acli-read",
        "harnesskit.skill.jira-write",
    }.issubset(component_ids)


def test_artifact_projection_includes_non_correlation_install_authority_destinations():
    projection = install_plan.build_artifact_projection(
        [
            "harnesskit.agent.adapter-author",
            "harnesskit.hook.optimal-response-prompt-submit",
            "harnesskit.rule.harnesskit-project-context",
        ]
    )
    destinations = {
        (record["target"], record["scope"], record["destination"])
        for record in projection["records"]
    }

    assert ("project", "project", "AGENTS.md") in destinations
    assert ("codex", "project", ".codex/config.toml") in destinations
    assert ("codex", "user", ".codex/hooks.json") in destinations
