from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.install import apply as install_apply
from scripts.install import verify as install_verify
from scripts.install.apply import merge_toml_agents
from scripts.install import common as install_common
from scripts.install.common import validate_plan_contract
from scripts.install.plan import build_hermes_external_package_plan

ROOT = Path(__file__).resolve().parents[2]

# SYNTHETIC representative codex config.toml destination. This is NOT the real
# user config; it is a hand-authored fixture exercising foreign top-level keys
# and tables plus a user-authored agents table the harness must NOT touch.
SYNTHETIC_RICH_CONFIG_TOML = """\
model = "gpt-5"
model_reasoning_effort = "high"
approval_policy = "on-request"
sandbox_mode = "workspace-write"
notify = ["say", "done"]

[projects."/fixture-home/repo"]
trust_level = "trusted"

[plugins.example]
enabled = true

[mcp_servers.local]
command = "node"
args = ["server.js"]

[mcp_servers.local.env]
TOKEN = "user-secret-placeholder"

[hooks.state]
last_run = "2026-01-01"

[agents."foo"]
description = "user authored agent"
config_file = "agents/foo.toml"
"""

# SYNTHETIC harness source: only owned `[agents."<name>"]` tables.
SYNTHETIC_HARNESS_SOURCE_TOML = """\
[agents."doc_curator"]
description = "harness doc curator"
config_file = "agents/doc_curator.toml"
nickname_candidates = ["doc_curator"]
[agents."designer"]
description = "harness designer"
config_file = "agents/designer.toml"
nickname_candidates = ["designer"]
"""

SYNTHETIC_RETIRED_SYSTEM_ARCHITECTURE_MANAGER_TOML = """\
[agents."system_architecture_manager"]
description = "retired HarnessKit architecture manager"
config_file = "agents/system_architecture_manager.toml"
nickname_candidates = ["system_architecture_manager"]
"""

_TOML_MERGE_ARTIFACT = {"toml_merge_key": "codex-agents"}

# Path to the on-disk SYNTHETIC rich config.toml fixture. It is intentionally
# richer than SYNTHETIC_RICH_CONFIG_TOML (comments, spacing, extra tables) so the
# end-to-end regression proves byte-level preservation, including content a
# parse-and-reserialize merge would drop. It is NOT the real user config.
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CODEX_CONFIG_RICH_FIXTURE = FIXTURES_DIR / "codex_config_rich.toml"


def _synthetic_legacy_hermes_directory(user: str = "fixture-user") -> str:
    return str(Path("/") / "Users" / user / ".local/share/harnesskit/hermes/skills")


def _read_rich_fixture() -> str:
    return CODEX_CONFIG_RICH_FIXTURE.read_text(encoding="utf-8")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_generated_plan(
    tmp_path: Path,
    *,
    mode: str = "apply",
    scope: str = "project",
    extra_components: list[str] | None = None,
) -> Path:
    args = [
        "scripts/install/plan.py",
        "--profile",
        "engineering",
        "--scope",
        scope,
        "--mode",
        mode,
        "--format",
        "json",
    ]
    for component in extra_components or []:
        args.extend(["--component", component])
    result = _run(*args)
    assert result.returncode == 0, result.stdout + result.stderr

    plan_path = tmp_path / "install-plan.json"
    plan_path.write_text(result.stdout, encoding="utf-8")
    return plan_path


def _write_mutated_plan(tmp_path: Path, mutate) -> Path:
    plan_path = _write_generated_plan(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    mutate(plan)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan_path


def _hermes_external_source(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "dist/hermes/external-package"
    catalog = yaml.safe_load((ROOT / "adapters/hermes/adapter.yml").read_text(encoding="utf-8"))["hermes"]["external_package"]["components"]
    for component in catalog:
        skill = component.removeprefix("harnesskit.skill.")
        skill_path = source / "skills/software-development" / skill / "SKILL.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    (source / ".harnesskit-hermes-external-package.json").write_text(json.dumps({"format": "harnesskit.hermes.external-package", "version": 1, "ownership": "harnesskit-managed-external-package", "adapter_id": "harnesskit.adapter.hermes", "catalog": catalog}), encoding="utf-8")
    hooks = source / "hooks/optimal-response"
    hooks.mkdir(parents=True)
    (hooks / "optimal-response-pre-llm.cjs").write_text("// hook\n", encoding="utf-8")
    (hooks / "stop-prompt-submit.cjs").write_text("// asset\n", encoding="utf-8")
    (source / "config.yaml").write_text("hooks:\n  pre_llm_call:\n    - command: hooks/optimal-response/optimal-response-pre-llm.cjs\n      timeout: 5\n", encoding="utf-8")
    monkeypatch.setattr(install_apply, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(install_verify, "REPO_ROOT", tmp_path)


def test_hermes_external_install_update_cleanup_and_exact_uninstall(tmp_path: Path, monkeypatch):
    _hermes_external_source(tmp_path, monkeypatch)
    target = tmp_path / "home"
    foreign_skills = target / ".hermes/skills/wayfinder/SKILL.md"
    foreign_skills.parent.mkdir(parents=True)
    foreign_skills.write_text("foreign skill\n", encoding="utf-8")
    named_profile = target / ".hermes/profiles/custom/config.yaml"
    named_profile.parent.mkdir(parents=True)
    named_profile.write_text("model: foreign\n", encoding="utf-8")
    config = target / ".hermes/config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("# foreign comment\nmodel: local\nforeign:\n  keep: [one, two]\n  command: ~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs\n  path: ~/.local/share/harnesskit/hermes/skills\nskills: # root skills\n  external_dirs:\n    - /foreign\nhooks: # root hooks\n  pre_llm_call:\n    - command: echo foreign\n      timeout: 5\n", encoding="utf-8")
    allowlist = target / ".hermes/shell-hooks-allowlist.json"
    allowlist.write_text(json.dumps({"approvals": [
        {"event": "pre_llm_call", "command": "~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs"},
        {"event": "pre_llm_call", "command": "~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs --near"},
    ]}), encoding="utf-8")
    install = build_hermes_external_package_plan(lifecycle="install", mode="apply")
    plan_path = tmp_path / "hermes.json"
    plan_path.write_text(json.dumps(install), encoding="utf-8")
    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    update = build_hermes_external_package_plan(lifecycle="update", mode="apply")
    plan_path.write_text(json.dumps(update), encoding="utf-8")
    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    assert yaml.safe_load(config.read_text())["skills"]["external_dirs"].count(install_common.HERMES_EXTERNAL_CONFIG_DIR) == 1
    assert "# foreign comment\nmodel: local\nforeign:\n  keep: [one, two]\n" in config.read_text(encoding="utf-8")
    assert config.read_text(encoding="utf-8").count("skills: # root skills") == 1
    assert json.loads(allowlist.read_text())["approvals"][0]["command"].endswith("optimal-response-pre-llm.cjs")
    assert install_verify.verify_plan(plan_path, target_root=target) == 0
    cleanup = build_hermes_external_package_plan(lifecycle="cleanup", mode="apply")
    plan_path.write_text(json.dumps(cleanup), encoding="utf-8")
    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    assert (target / ".local/share/harnesskit/hermes/skills").is_dir()
    assert (target / ".hermes/harnesskit/hooks/optimal-response/stop-prompt-submit.cjs").is_file()
    assert foreign_skills.read_text(encoding="utf-8") == "foreign skill\n"
    assert named_profile.read_text(encoding="utf-8") == "model: foreign\n"
    uninstall = build_hermes_external_package_plan(lifecycle="uninstall", mode="apply")
    plan_path.write_text(json.dumps(uninstall), encoding="utf-8")
    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    assert not (target / ".local/share/harnesskit/hermes/skills").exists()
    assert yaml.safe_load(config.read_text())["skills"]["external_dirs"] == ["/foreign"]
    assert "  command: ~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs\n  path: ~/.local/share/harnesskit/hermes/skills\n" in config.read_text(encoding="utf-8")
    assert json.loads(allowlist.read_text()) == {"approvals": [{"event": "pre_llm_call", "command": "~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs --near"}]}
    assert foreign_skills.read_text(encoding="utf-8") == "foreign skill\n"
    assert named_profile.read_text(encoding="utf-8") == "model: foreign\n"
    assert install_verify.verify_plan(plan_path, target_root=target) == 0


@pytest.mark.parametrize("config_text", [
    "skills:\n  external_dirs: []\nhooks: {}\n",
    "skills: {external_dirs: []}\nhooks: {}\n",
])
def test_hermes_update_fails_closed_before_replacing_package_for_unsupported_yaml(tmp_path: Path, monkeypatch, config_text: str):
    _hermes_external_source(tmp_path, monkeypatch)
    target = tmp_path / "home"
    package = target / ".local/share/harnesskit/hermes/skills"
    package.mkdir(parents=True)
    (package / "sentinel").write_text("keep", encoding="utf-8")
    marker = package.parent / ".harnesskit-hermes-external-package.json"
    marker.write_text((tmp_path / "dist/hermes/external-package/.harnesskit-hermes-external-package.json").read_text(encoding="utf-8"), encoding="utf-8")
    config = target / ".hermes/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(config_text, encoding="utf-8")
    plan = build_hermes_external_package_plan(lifecycle="update", mode="apply")
    plan_path = tmp_path / "hermes.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported.*Hermes"):
        install_apply.apply_plan(plan_path, target_root=target)

    assert (package / "sentinel").read_text(encoding="utf-8") == "keep"
    assert config.read_text(encoding="utf-8") == config_text


def test_hermes_uninstall_preserves_all_unowned_surfaces(tmp_path: Path, monkeypatch):
    _hermes_external_source(tmp_path, monkeypatch)
    target = tmp_path / "home"
    package = target / ".local/share/harnesskit/hermes/skills"
    package.mkdir(parents=True)
    (package / "foreign").write_text("keep", encoding="utf-8")
    config = target / ".hermes/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(f"skills:\n  external_dirs:\n    - {_synthetic_legacy_hermes_directory()}\nhooks:\n  pre_llm_call:\n    - command: ~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs\n      timeout: 5\n", encoding="utf-8")
    allowlist = target / ".hermes/shell-hooks-allowlist.json"
    allowlist.write_text(json.dumps({"approvals": [{"event": "pre_llm_call", "command": "~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs"}]}), encoding="utf-8")
    plan = build_hermes_external_package_plan(lifecycle="uninstall", mode="apply")
    plan_path = tmp_path / "hermes.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    assert (package / "foreign").read_text(encoding="utf-8") == "keep"
    assert "external_dirs" in config.read_text(encoding="utf-8")
    assert json.loads(allowlist.read_text(encoding="utf-8"))["approvals"]


def test_hermes_update_rejects_foreign_marker_without_replacing_package(tmp_path: Path, monkeypatch):
    _hermes_external_source(tmp_path, monkeypatch)
    target = tmp_path / "home"
    package = target / ".local/share/harnesskit/hermes/skills"
    package.mkdir(parents=True)
    (package / "foreign.txt").write_text("keep", encoding="utf-8")
    marker = package.parent / ".harnesskit-hermes-external-package.json"
    marker.write_text("{}\n", encoding="utf-8")
    config = target / ".hermes/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "skills:\n  external_dirs:\n    - /foreign\nhooks:\n  pre_llm_call:\n    - command: echo foreign\n      timeout: 5\n",
        encoding="utf-8",
    )
    plan = build_hermes_external_package_plan(lifecycle="update", mode="apply")
    plan_path = tmp_path / "hermes.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(ValueError, match="marker"):
        install_apply.apply_plan(plan_path, target_root=target)

    assert (package / "foreign.txt").read_text(encoding="utf-8") == "keep"


def test_hermes_uninstall_preserves_package_when_marker_is_symlink(tmp_path: Path, monkeypatch):
    _hermes_external_source(tmp_path, monkeypatch)
    target = tmp_path / "home"
    package = target / ".local/share/harnesskit/hermes/skills"
    package.mkdir(parents=True)
    (package / "foreign.txt").write_text("keep", encoding="utf-8")
    foreign_marker = tmp_path / "foreign-marker.json"
    foreign_marker.write_text(
        (tmp_path / "dist/hermes/external-package/.harnesskit-hermes-external-package.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    marker = package.parent / ".harnesskit-hermes-external-package.json"
    marker.symlink_to(foreign_marker)
    plan = build_hermes_external_package_plan(lifecycle="uninstall", mode="apply")
    plan_path = tmp_path / "hermes.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    assert (package / "foreign.txt").read_text(encoding="utf-8") == "keep"
    assert marker.is_symlink()


def test_hermes_install_ignores_foreign_matching_lines_outside_owned_sections(tmp_path: Path, monkeypatch):
    _hermes_external_source(tmp_path, monkeypatch)
    target = tmp_path / "home"
    config = target / ".hermes/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "foreign:\n"
        "  paths:\n"
        f"    - {_synthetic_legacy_hermes_directory()}\n"
        "  hooks:\n"
        "    - command: ~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs\n"
        "      timeout: 5\n"
        "skills:\n"
        "  external_dirs:\n"
        "    - /foreign\n"
        "hooks:\n"
        "  pre_llm_call:\n"
        "    - command: echo foreign\n"
        "      timeout: 5\n",
        encoding="utf-8",
    )
    plan = build_hermes_external_package_plan(lifecycle="install", mode="apply")
    plan_path = tmp_path / "hermes.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    merged = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert merged["skills"]["external_dirs"] == [
        "/foreign",
        install_common.HERMES_EXTERNAL_CONFIG_DIR,
    ]
    assert {entry["command"] for entry in merged["hooks"]["pre_llm_call"]} == {
        "echo foreign",
        "~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs",
    }


def test_hermes_update_recognizes_quoted_and_reordered_owned_yaml_entries(tmp_path: Path, monkeypatch):
    _hermes_external_source(tmp_path, monkeypatch)
    target = tmp_path / "home"
    config = target / ".hermes/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "skills:\n"
        "  external_dirs:\n"
        f'    - "{_synthetic_legacy_hermes_directory()}"\n'
        "hooks:\n"
        "  pre_llm_call:\n"
        "    - timeout: 5\n"
        '      command: "~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs"\n',
        encoding="utf-8",
    )
    plan = build_hermes_external_package_plan(lifecycle="update", mode="apply")
    plan_path = tmp_path / "hermes.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    merged = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert merged["skills"]["external_dirs"] == [
        install_common.HERMES_EXTERNAL_CONFIG_DIR
    ]
    assert _synthetic_legacy_hermes_directory() not in config.read_text(encoding="utf-8")
    assert install_verify.verify_plan(plan_path, target_root=target) == 0
    assert merged["hooks"]["pre_llm_call"] == [
        {
            "timeout": 5,
            "command": "~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs",
        }
    ]


def test_hermes_update_removes_all_legacy_external_dirs(tmp_path: Path, monkeypatch):
    _hermes_external_source(tmp_path, monkeypatch)
    target = tmp_path / "home"
    config = target / ".hermes/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "skills:\n"
        "  external_dirs:\n"
        f'    - "{_synthetic_legacy_hermes_directory("alice")}"\n'
        f'    - "{_synthetic_legacy_hermes_directory("bob")}"\n'
        "    - /foreign\n"
        "hooks:\n"
        "  pre_llm_call:\n"
        "    - command: echo foreign\n"
        "      timeout: 5\n",
        encoding="utf-8",
    )
    plan = build_hermes_external_package_plan(lifecycle="update", mode="apply")
    plan_path = tmp_path / "hermes.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    merged = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert merged["skills"]["external_dirs"] == [
        "/foreign",
        install_common.HERMES_EXTERNAL_CONFIG_DIR,
    ]
    assert install_verify.verify_plan(plan_path, target_root=target) == 0


def test_hermes_uninstall_removes_legacy_managed_external_dir(tmp_path: Path, monkeypatch):
    _hermes_external_source(tmp_path, monkeypatch)
    target = tmp_path / "home"
    plan_path = tmp_path / "hermes.json"
    install = build_hermes_external_package_plan(lifecycle="install", mode="apply")
    plan_path.write_text(json.dumps(install), encoding="utf-8")
    assert install_apply.apply_plan(plan_path, target_root=target) == 0

    config = target / ".hermes/config.yaml"
    config.write_text(
        "skills:\n"
        "  external_dirs:\n"
        f'    - "{_synthetic_legacy_hermes_directory()}"\n'
        "hooks:\n"
        "  pre_llm_call:\n"
        "    - command: ~/.hermes/harnesskit/hooks/optimal-response/optimal-response-pre-llm.cjs\n"
        "      timeout: 5\n",
        encoding="utf-8",
    )
    uninstall = build_hermes_external_package_plan(lifecycle="uninstall", mode="apply")
    plan_path.write_text(json.dumps(uninstall), encoding="utf-8")

    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    assert not (target / ".local/share/harnesskit/hermes/skills").exists()
    assert install_common.HERMES_EXTERNAL_CONFIG_DIR not in config.read_text(encoding="utf-8")
    assert _synthetic_legacy_hermes_directory() not in config.read_text(encoding="utf-8")
    assert install_verify.verify_plan(plan_path, target_root=target) == 0


def test_hermes_install_never_creates_shell_hook_consent(tmp_path: Path, monkeypatch):
    _hermes_external_source(tmp_path, monkeypatch)
    target = tmp_path / "home"
    plan = build_hermes_external_package_plan(lifecycle="install", mode="apply")
    plan_path = tmp_path / "hermes.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    assert install_apply.apply_plan(plan_path, target_root=target) == 0
    assert not (target / ".hermes/shell-hooks-allowlist.json").exists()


def test_apply_materializes_profile_artifacts_only(tmp_path: Path):
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (target_root / ".claude/skills/doc-curator/SKILL.md").is_file()
    assert (target_root / ".claude/agents/doc-curator.md").is_file()
    assert (target_root / ".claude/agents/system-designer.md").is_file()
    assert (target_root / ".claude/agents/codebase-architecture-manager.md").is_file()
    assert (target_root / ".codex/agents/doc_curator.toml").is_file()
    assert (target_root / ".codex/agents/system_designer.toml").is_file()
    assert (target_root / ".codex/agents/codebase_architecture_manager.toml").is_file()
    assert not (target_root / ".codex/hooks.json").exists()
    assert not (target_root / ".agents/hooks.json").exists()
    assert not (target_root / ".harnesskit/scripts/human_doc_turn_scan.py").exists()
    assert not (target_root / ".agents/templates/human_doc.html").exists()
    assert "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->" in (
        target_root / "AGENTS.md"
    ).read_text(encoding="utf-8")
    assert "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->" in (
        target_root / "CLAUDE.md"
    ).read_text(encoding="utf-8")
    assert not (target_root / "GEMINI.md").exists()

    agents_text = (target_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "`worktree-lifecycle` skill" in agents_text
    # M1 cleanup removed the using-git-worktrees "deprecated reference" policy text
    # from the managed block; the worktree-lifecycle-manager guidance replaces it.
    assert "deprecated reference" not in agents_text
    assert "`worktree-lifecycle-manager`" in agents_text

    assert not (target_root / ".claude/skills/harnesskit-tdd/SKILL.md").exists()
    assert not (target_root / ".claude/agents/backend-codebase-reviewer.md").exists()
    assert not (target_root / ".claude/agents/system-architecture-reviewer.md").exists()
    assert not (target_root / ".claude/skills/optimal-response/SKILL.md").exists()
    assert not (target_root / ".agents/skills/optimal-response/SKILL.md").exists()
    assert not (target_root / ".claude/settings.json").exists()


def test_removed_human_doc_turn_scan_component_cannot_be_selected(tmp_path: Path):
    result = _run(
        "scripts/install/plan.py",
        "--profile",
        "engineering",
        "--scope",
        "project",
        "--mode",
        "apply",
        "--format",
        "json",
        "--component",
        "harnesskit.hook.human-doc-turn-scan",
    )

    assert result.returncode != 0
    assert "Component is not registered: harnesskit.hook.human-doc-turn-scan" in result.stderr


def test_apply_merges_managed_instruction_blocks_without_overwriting_manual_text(
    tmp_path: Path,
):
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"
    agents = target_root / "AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text(
        "# Manual top\n\nkeep this\n\n<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->\nold\n<!-- END HARNESSKIT GENERATED CONTEXT -->\n\nmanual bottom\n",
        encoding="utf-8",
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    text = agents.read_text(encoding="utf-8")
    assert text.startswith("# Manual top")
    assert "keep this" in text
    assert "manual bottom" in text
    assert "old" not in text
    assert text.count("<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->") == 1
    assert ".agents/skills/doc-{curator,bootstrap,sync,audit}" in text
    assert "`worktree-lifecycle` skill" in text


def test_apply_deep_merges_claude_settings_json_without_losing_user_settings(
    tmp_path: Path,
):
    plan_path = _write_generated_plan(
        tmp_path,
        mode="apply",
        scope="user",
    )
    target_root = tmp_path / "target"
    settings = target_root / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(git status:*)"]},
                "enabledPlugins": ["local-plugin"],
                "statusLine": {"type": "command", "command": "echo ready"},
                "mcpServers": {"local": {"command": "node", "args": ["server.js"]}},
                "env": {"USER_FLAG": "preserved"},
                "model": "sonnet",
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "manual",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo user-stop",
                                }
                            ],
                        },
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        'OPTIMAL_RESPONSE_FLAG="$HOME/.claude/old" '
                                        'node "$HOME/.claude/old.cjs"'
                                    ),
                                }
                            ],
                        },
                    ],
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "echo user-pre"}
                            ],
                        }
                    ],
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    merged = json.loads(settings.read_text(encoding="utf-8"))
    assert merged["permissions"] == {"allow": ["Bash(git status:*)"]}
    assert merged["enabledPlugins"] == ["local-plugin"]
    assert merged["statusLine"]["command"] == "echo ready"
    assert merged["mcpServers"]["local"]["command"] == "node"
    assert merged["env"]["USER_FLAG"] == "preserved"
    assert merged["model"] == "sonnet"

    stop_commands = [
        hook["command"]
        for group in merged["hooks"]["Stop"]
        for hook in group.get("hooks", [])
    ]
    assert "echo user-stop" in stop_commands
    user_prompt_submit_commands = [
        hook["command"]
        for group in merged["hooks"]["UserPromptSubmit"]
        for hook in group.get("hooks", [])
    ]
    assert any("stop-prompt-submit.cjs" in item for item in user_prompt_submit_commands)
    assert not any("old.cjs" in item for item in user_prompt_submit_commands)
    assert merged["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "echo user-pre"


def test_json_deep_merge_uses_canonical_key_order():
    current = json.dumps(
        {
            "zeta": True,
            "hooks": {"Stop": []},
            "alpha": True,
        },
        indent=2,
    )
    body = json.dumps(
        {
            "hooks": {"UserPromptSubmit": [{"hooks": []}]},
            "beta": True,
        },
        indent=2,
    )

    merged = install_apply.merge_json_deep(
        current,
        body,
        {"json_merge_key": "codex-hooks"},
    )

    assert list(json.loads(merged).keys()) == ["alpha", "beta", "hooks", "zeta"]
    assert merged.endswith("\n")


def test_apply_deep_merges_codex_user_hooks_without_losing_user_hooks(
    tmp_path: Path,
):
    plan_path = _write_generated_plan(tmp_path, mode="apply", scope="user")
    target_root = tmp_path / "home"
    hooks_json = target_root / ".codex/hooks.json"
    hooks_json.parent.mkdir(parents=True)
    hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo user-stop",
                                }
                            ],
                        }
                    ],
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        'node "${CODEX_HOME:-$HOME/.codex}/skills/'
                                        'optimal-response/hooks/stop-prompt-submit.cjs"'
                                    ),
                                }
                            ],
                        },
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo user-prompt",
                                }
                            ],
                        },
                    ],
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    merged = json.loads(hooks_json.read_text(encoding="utf-8"))
    stop_commands = [
        hook["command"]
        for group in merged["hooks"]["Stop"]
        for hook in group.get("hooks", [])
    ]
    prompt_commands = [
        hook["command"]
        for group in merged["hooks"]["UserPromptSubmit"]
        for hook in group.get("hooks", [])
    ]

    assert stop_commands == ["echo user-stop"]
    assert "echo user-prompt" in prompt_commands
    assert sum("optimal-response/hooks/stop-prompt-submit.cjs" in item for item in prompt_commands) == 1
    assert any("OPTIMAL_RESPONSE_SURFACE=codex" in item for item in prompt_commands)


def test_apply_prunes_legacy_human_doc_turn_scan_hook_only(
    tmp_path: Path,
):
    plan_path = _write_generated_plan(tmp_path, mode="apply", scope="user")
    target_root = tmp_path / "home"
    hooks_json = target_root / ".codex/hooks.json"
    hooks_json.parent.mkdir(parents=True)
    hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo user-stop",
                                }
                            ],
                        },
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "python3 .harnesskit/scripts/"
                                        "human_doc_turn_scan.py --runtime codex-stop"
                                    ),
                                }
                            ],
                        },
                    ]
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    merged = json.loads(hooks_json.read_text(encoding="utf-8"))
    stop_commands = [
        hook["command"]
        for group in merged["hooks"].get("Stop", [])
        for hook in group.get("hooks", [])
    ]
    prompt_commands = [
        hook["command"]
        for group in merged["hooks"]["UserPromptSubmit"]
        for hook in group.get("hooks", [])
    ]

    assert stop_commands == ["echo user-stop"]
    assert not any("human_doc_turn_scan.py" in item for item in stop_commands)
    assert any("optimal-response/hooks/stop-prompt-submit.cjs" in item for item in prompt_commands)


def test_apply_rejects_malformed_existing_hooks_mapping():
    with pytest.raises(ValueError, match="destination hooks must be a JSON object"):
        install_apply._merge_json_hooks(
            [],
            {"Stop": []},
            managed_command_tokens=(),
        )


def test_verify_accepts_json_deep_merged_claude_settings_with_user_keys(
    tmp_path: Path,
):
    plan_path = _write_generated_plan(tmp_path, mode="apply")
    target_root = tmp_path / "target"
    settings = target_root / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "permissions": {"deny": ["Bash(rm -rf:*)"]},
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "manual",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo user-stop",
                                }
                            ],
                        }
                    ]
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    apply_result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )
    assert apply_result.returncode == 0, apply_result.stdout + apply_result.stderr

    verify_result = _run(
        "scripts/install/verify.py",
        str(plan_path),
        "--target-root",
        str(target_root),
    )

    assert verify_result.returncode == 0, verify_result.stdout + verify_result.stderr


def test_apply_replaces_legacy_managed_instruction_block_markers(tmp_path: Path):
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"
    agents = target_root / "AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text(
        "# Manual top\n\n"
        "<!-- BEGIN ROUTINE-HARNESS GENERATED CONTEXT -->\nold\n"
        "<!-- END ROUTINE-HARNESS GENERATED CONTEXT -->\n\n"
        "manual bottom\n",
        encoding="utf-8",
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    text = agents.read_text(encoding="utf-8")
    assert "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->" in text
    assert "<!-- END HARNESSKIT GENERATED CONTEXT -->" in text
    assert "ROUTINE-HARNESS" not in text
    assert "manual bottom" in text
    assert "old" not in text


def test_apply_rejects_duplicate_managed_block_markers(tmp_path: Path):
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"
    agents = target_root / "AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text(
        "# Manual top\n\n"
        "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->\nold\n"
        "<!-- END HARNESSKIT GENERATED CONTEXT -->\n\n"
        "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->\nolder\n"
        "<!-- END HARNESSKIT GENERATED CONTEXT -->\n",
        encoding="utf-8",
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    assert result.returncode != 0
    assert "managed block markers must be absent or appear exactly once" in result.stderr


def test_apply_overwrite_guard_refuses_existing_full_file_artifact(tmp_path: Path):
    # The overwrite guard still protects plain (non-merge) full-file artifacts:
    # a per-agent `.codex/agents/<name>.toml` config layer is full-file copy, so
    # pre-existing different content blocks apply without --overwrite. (The
    # registration layer `.codex/config.toml` is now a preserving merge and is
    # covered separately.)
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"
    existing_agent = target_root / ".codex/agents/doc_curator.toml"
    existing_agent.parent.mkdir(parents=True)
    existing_agent.write_text("# manual edit\ncustom = true\n", encoding="utf-8")

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    assert result.returncode != 0
    assert "destination already exists with different content" in result.stderr
    assert existing_agent.read_text(encoding="utf-8") == "# manual edit\ncustom = true\n"


def test_apply_overwrites_existing_full_file_artifact_only_when_explicit(tmp_path: Path):
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"
    existing_agent = target_root / ".codex/agents/doc_curator.toml"
    existing_agent.parent.mkdir(parents=True)
    existing_agent.write_text("# manual edit\ncustom = true\n", encoding="utf-8")

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--overwrite",
        "--allow-runtime-hooks",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "custom = true" not in existing_agent.read_text(encoding="utf-8")


def test_apply_merges_codex_config_without_overwrite_and_preserves_user_content(
    tmp_path: Path,
):
    # S2: the `.codex/config.toml` registration artifact now installs via the
    # preserving toml-agents-merge. Applying it onto an EXISTING rich user config
    # must SUCCEED without --overwrite, add the harness owned `[agents."<name>"]`
    # tables, and byte-preserve every non-owned key/table. NOT the real user config.
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"
    existing_config = target_root / ".codex/config.toml"
    existing_config.parent.mkdir(parents=True)
    existing_config.write_text(SYNTHETIC_RICH_CONFIG_TOML, encoding="utf-8")

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    # A managed merge is not an overwrite: success WITHOUT --overwrite (R4).
    assert result.returncode == 0, result.stdout + result.stderr

    merged = existing_config.read_text(encoding="utf-8")
    # Non-owned content is byte-preserved (R1).
    assert _foreign_region(SYNTHETIC_RICH_CONFIG_TOML) in merged
    # The user-authored foreign agents table survives (R2).
    assert (
        '[agents."foo"]\n'
        'description = "user authored agent"\n'
        'config_file = "agents/foo.toml"\n'
    ) in merged

    import tomllib

    parsed = tomllib.loads(merged)
    assert parsed["model"] == "gpt-5"
    assert parsed["approval_policy"] == "on-request"
    assert parsed["sandbox_mode"] == "workspace-write"
    assert parsed["mcp_servers"]["local"]["env"]["TOKEN"] == "user-secret-placeholder"
    assert parsed["hooks"]["state"]["last_run"] == "2026-01-01"
    assert parsed["agents"]["foo"]["description"] == "user authored agent"
    # Harness owned registration tables are present.
    assert "doc_curator" in parsed["agents"]

    # Idempotent: re-apply is a stable no-op fixed point (R4).
    again = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert existing_config.read_text(encoding="utf-8") == merged


def test_user_install_removes_only_exact_retired_codex_registration(tmp_path: Path):
    # This is a synthetic user root, never the real Codex configuration. The
    # profile-declared migration must remove the exact retired registration,
    # preserve the unrelated user agent, and become a verify fixed point.
    plan_path = _write_generated_plan(tmp_path, scope="user")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["retired_codex_agent_registrations"] == [
        {
            "name": "system_architecture_manager",
            "config_file": "agents/system_architecture_manager.toml",
        }
    ]

    target_root = tmp_path / "target"
    existing_config = target_root / ".codex/config.toml"
    existing_config.parent.mkdir(parents=True)
    existing_config.write_text(
        SYNTHETIC_RICH_CONFIG_TOML
        + "\n"
        + SYNTHETIC_RETIRED_SYSTEM_ARCHITECTURE_MANAGER_TOML,
        encoding="utf-8",
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    merged = existing_config.read_text(encoding="utf-8")
    assert "system_architecture_manager" not in merged
    assert '[agents."foo"]' in merged
    assert "user authored agent" in merged

    import tomllib

    agents = tomllib.loads(merged)["agents"]
    assert "system_architecture_manager" not in agents
    assert agents["foo"]["config_file"] == "agents/foo.toml"

    verify_result = _run(
        "scripts/install/verify.py",
        str(plan_path),
        "--target-root",
        str(target_root),
    )
    assert verify_result.returncode == 0, verify_result.stdout + verify_result.stderr

    again = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert existing_config.read_text(encoding="utf-8") == merged


def test_user_install_preserves_conflicting_retired_agent_name_before_writes(
    tmp_path: Path,
):
    # Same name but a different config-file signature is user-owned. The install
    # must fail closed before copying any generated files.
    plan_path = _write_generated_plan(tmp_path, scope="user")
    target_root = tmp_path / "target"
    existing_config = target_root / ".codex/config.toml"
    existing_config.parent.mkdir(parents=True)
    original = (
        '[agents."system_architecture_manager"]\n'
        'description = "user-defined agent with a retired name"\n'
        'config_file = "agents/user-owned-manager.toml"\n'
    )
    existing_config.write_text(original, encoding="utf-8")

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    assert result.returncode != 0
    assert "conflicts with user configuration" in result.stderr
    assert existing_config.read_text(encoding="utf-8") == original
    assert not (target_root / ".codex/skills").exists()


def test_apply_codex_config_preserves_rich_fixture_byte_for_byte(tmp_path: Path):
    # R6: the canonical regression. Start from the on-disk SYNTHETIC rich
    # config.toml fixture (top-level keys, [projects.*], [plugins.*],
    # [mcp_servers.*] with nested env, [hooks.state], user [agents."foo"], plus
    # comments and spacing), apply the codex config artifact via the preserving
    # toml-agents-merge, and prove: (a) every non-owned key/table is byte-
    # preserved verbatim; (b) the user [agents."foo"] survives; (c) the harness
    # agent tables are present; (d) re-apply is an idempotent fixed point; and
    # (e) it all succeeds WITHOUT --overwrite. Uses NO real user config.
    original = _read_rich_fixture()

    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"
    existing_config = target_root / ".codex/config.toml"
    existing_config.parent.mkdir(parents=True)
    existing_config.write_text(original, encoding="utf-8")

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )

    # (e) A managed merge is not an overwrite: success WITHOUT --overwrite (R4).
    assert result.returncode == 0, result.stdout + result.stderr

    merged = existing_config.read_text(encoding="utf-8")

    # (a) Every non-owned byte region is preserved verbatim. The fixture's owned
    # region begins at the first `[agents.` header; everything before it (all
    # top-level keys, [projects.*], [plugins.*], [mcp_servers.*] + nested env,
    # [hooks.state], AND the inline comments / blank-line spacing) must appear
    # byte-for-byte in the merged output.
    non_owned_prefix = _foreign_region(original)
    assert non_owned_prefix in merged
    # Comments survive (a parse-and-reserialize merge would have dropped them).
    assert "# user-chosen model; must survive untouched" in merged
    assert "# An MCP server with secrets in a nested env table" in merged

    # (b) The user-authored foreign agents table survives byte-for-byte.
    assert (
        '[agents."foo"]\n'
        'description = "user authored agent"\n'
        'config_file = "agents/foo.toml"\n'
        'nickname_candidates = ["foo", "fooey"]\n'
    ) in merged

    import tomllib

    parsed = tomllib.loads(merged)
    # Top-level keys preserved with exact values.
    assert parsed["model"] == "gpt-5-codex"
    assert parsed["model_reasoning_effort"] == "high"
    assert parsed["approval_policy"] == "on-request"
    assert parsed["approvals_reviewer"] == "user"
    assert parsed["sandbox_mode"] == "workspace-write"
    assert parsed["notify"] == ["say", "Codex is done"]
    # Foreign tables preserved.
    assert parsed["projects"]["/fixture-home/work/repo"]["trust_level"] == "trusted"
    assert parsed["plugins"]["example-plugin"]["enabled"] is True
    assert parsed["mcp_servers"]["local"]["command"] == "node"
    assert parsed["mcp_servers"]["local"]["env"]["API_TOKEN"] == (
        "synthetic-not-a-real-secret"
    )
    assert parsed["mcp_servers"]["local"]["env"]["WORKDIR"] == "/fixture-home/work"
    assert parsed["hooks"]["state"]["last_run"] == "2026-01-01T00:00:00Z"
    # (b) parse-level: user agent survives.
    assert parsed["agents"]["foo"]["description"] == "user authored agent"
    assert parsed["agents"]["foo"]["nickname_candidates"] == ["foo", "fooey"]
    # (c) Harness owned registration tables are present.
    assert "doc_curator" in parsed["agents"]
    assert "designer" in parsed["agents"]
    # The fixture is unmodified on disk (we only ever wrote the target copy).
    assert _read_rich_fixture() == original

    # (d) Idempotent: re-apply yields a byte-stable fixed point (R4).
    again = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert existing_config.read_text(encoding="utf-8") == merged

    # And verify reports the merged artifact as a clean fixed point (no drift).
    verify_result = _run(
        "scripts/install/verify.py",
        str(plan_path),
        "--target-root",
        str(target_root),
    )
    assert verify_result.returncode == 0, verify_result.stdout + verify_result.stderr


def test_apply_rejects_source_root_escape(tmp_path: Path):
    plan_path = _write_mutated_plan(
        tmp_path,
        lambda plan: plan["artifacts"][0].update({"source": "../secrets.txt"}),
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(tmp_path / "target"),
    )

    assert result.returncode != 0
    assert "Artifact source must stay inside its root" in result.stderr


def test_apply_rejects_absolute_source(tmp_path: Path):
    plan_path = _write_mutated_plan(
        tmp_path,
        lambda plan: plan["artifacts"][0].update({"source": "/tmp/secrets.txt"}),
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(tmp_path / "target"),
    )

    assert result.returncode != 0
    assert "Artifact source must stay inside its root" in result.stderr


def test_apply_rejects_cross_target_source_destination_mismatch(tmp_path: Path):
    def mutate(plan: dict) -> None:
        plan["artifacts"][0].update(
            {
                "target": "claude",
                "source": "dist/codex/.codex/hooks.json",
                "destination": ".claude/agents/doc-curator.md",
            }
        )

    plan_path = _write_mutated_plan(tmp_path, mutate)

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(tmp_path / "target"),
    )

    assert result.returncode != 0
    assert "artifact source must match target and destination" in result.stderr


def test_apply_rejects_artifact_component_outside_profile_selection(tmp_path: Path):
    plan_path = _write_mutated_plan(
        tmp_path,
        lambda plan: plan["artifacts"][0].update({"component_id": "harnesskit.skill.not-selected"}),
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(tmp_path / "target"),
    )

    assert result.returncode != 0
    assert "Artifact component is not selected" in result.stderr


def test_apply_rejects_required_before_apply_activation_gate(tmp_path: Path):
    def mutate(plan: dict) -> None:
        plan["activation_gates"].append(
            {
                "id": "manual-runtime-gate",
                "target": "codex",
                "reason": "test gate",
                "required_before_apply": True,
                "required_before_runtime": True,
            }
        )

    plan_path = _write_generated_plan(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    mutate(plan)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(tmp_path / "target"),
    )

    assert result.returncode != 0
    assert "activation gates require approval before apply" in result.stderr


def test_apply_rejects_dry_run_plan(tmp_path: Path):
    plan_path = _write_generated_plan(tmp_path, mode="dry-run")

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(tmp_path / "target"),
        "--allow-runtime-hooks",
    )

    assert result.returncode != 0
    assert "install plan mode must be apply" in result.stderr


def test_apply_rejects_runtime_hook_surfaces_without_explicit_flag(tmp_path: Path):
    plan_path = _write_generated_plan(
        tmp_path,
        scope="user",
    )

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(tmp_path / "target"),
    )

    assert result.returncode != 0
    assert "runtime hook surfaces require --allow-runtime-hooks" in result.stderr


def test_apply_preflight_prevents_partial_materialization(tmp_path: Path):
    def mutate(plan: dict) -> None:
        plan["artifacts"][1]["source"] = "dist/codex/missing-file.toml"

    plan_path = _write_mutated_plan(tmp_path, mutate)
    target_root = tmp_path / "target"

    result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
    )

    assert result.returncode != 0
    assert not (target_root / ".claude/agents/doc-curator.md").exists()


def test_verify_confirms_applied_artifacts_match_sources(tmp_path: Path):
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"

    apply_result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )
    assert apply_result.returncode == 0, apply_result.stdout + apply_result.stderr

    verify_result = _run(
        "scripts/install/verify.py",
        str(plan_path),
        "--target-root",
        str(target_root),
    )

    assert verify_result.returncode == 0, verify_result.stdout + verify_result.stderr
    assert "source artifacts" in verify_result.stdout
    assert "unique destinations" in verify_result.stdout
    assert "scope=content+merge-fixed-point" in verify_result.stdout
    assert "unchecked=file-mode,mtime,xattr,platform-metadata,runtime" in verify_result.stdout


def test_verify_fails_when_artifact_is_missing(tmp_path: Path):
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"

    verify_result = _run(
        "scripts/install/verify.py",
        str(plan_path),
        "--target-root",
        str(target_root),
    )

    assert verify_result.returncode != 0
    assert "missing" in verify_result.stderr


def test_verify_rejects_dry_run_plan(tmp_path: Path):
    plan_path = _write_generated_plan(tmp_path, mode="dry-run")

    verify_result = _run(
        "scripts/install/verify.py",
        str(plan_path),
        "--target-root",
        str(tmp_path / "target"),
    )

    assert verify_result.returncode != 0
    assert "install plan mode must be apply or verify" in verify_result.stderr


def test_validate_plan_contract_rejects_divergent_shared_final_destination(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(install_common, "REPO_ROOT", tmp_path)
    codex_source = tmp_path / "dist/codex/.agents/skills/shared/SKILL.md"
    antigravity_source = tmp_path / "dist/antigravity/.agents/skills/shared/SKILL.md"
    codex_source.parent.mkdir(parents=True)
    antigravity_source.parent.mkdir(parents=True)
    codex_source.write_text("codex content\n", encoding="utf-8")
    antigravity_source.write_text("antigravity content\n", encoding="utf-8")

    plan = {
        "targets": ["codex", "antigravity"],
        "components": ["harnesskit.skill.shared"],
        "runtime_surfaces": [
            {
                "target": "codex",
                "path": ".agents/skills",
                "source": "dist/codex/.agents/skills",
            },
            {
                "target": "antigravity",
                "path": ".agents/skills",
                "source": "dist/antigravity/.agents/skills",
            },
        ],
        "artifacts": [
            {
                "component_id": "harnesskit.skill.shared",
                "target": "codex",
                "source": "dist/codex/.agents/skills/shared/SKILL.md",
                "destination": ".agents/skills/shared/SKILL.md",
            },
            {
                "component_id": "harnesskit.skill.shared",
                "target": "antigravity",
                "source": "dist/antigravity/.agents/skills/shared/SKILL.md",
                "destination": ".agents/skills/shared/SKILL.md",
            },
        ],
    }

    with pytest.raises(
        ValueError,
        match="final destination has divergent source content: .agents/skills/shared/SKILL.md",
    ):
        validate_plan_contract(plan)


def test_verify_fails_when_codex_config_owned_table_drifts(tmp_path: Path):
    # Migrated from the old full-file-overwrite contract: `.codex/config.toml`
    # is now a preserving toml-agents-merge, so drift is detected via the merge
    # fixed point, NOT filecmp. An owned `[agents."doc_curator"]` table edited
    # away from the harness source is no longer the merge fixed point, so verify
    # reports the strategy-specific `toml merge mismatch`, not a generic
    # `mismatch`. (Non-owned drift, e.g. a user editing their own model key,
    # would NOT trip verify because the merge preserves it — that is the point.)
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"

    apply_result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )
    assert apply_result.returncode == 0, apply_result.stdout + apply_result.stderr

    drifted_artifact = target_root / ".codex/config.toml"
    current = drifted_artifact.read_text(encoding="utf-8")
    # Tamper with an owned table's body so the file is no longer the merge's
    # fixed point. Keep the assertion independent of the current localized
    # component copy; the behavior under test is the owned-table drift itself.
    description_line = next(
        (line for line in current.splitlines() if line.startswith("description = ")),
        None,
    )
    assert description_line is not None
    drifted = current.replace(
        description_line,
        'description = "MANUALLY DRIFTED owned table for routing"',
        1,
    )
    assert drifted != current
    drifted_artifact.write_text(drifted, encoding="utf-8")

    verify_result = _run(
        "scripts/install/verify.py",
        str(plan_path),
        "--target-root",
        str(target_root),
    )

    assert verify_result.returncode != 0
    # The preserving-merge contract reports a strategy-specific drift message.
    assert "toml merge mismatch: .codex/config.toml" in verify_result.stderr


def test_verify_fails_when_managed_instruction_block_drifts(tmp_path: Path):
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"

    apply_result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )
    assert apply_result.returncode == 0, apply_result.stdout + apply_result.stderr

    agents = target_root / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace(
            ".agents/skills/doc-{curator,bootstrap,sync,audit}",
            ".agents/skills/manual-edit",
        ),
        encoding="utf-8",
    )

    verify_result = _run(
        "scripts/install/verify.py",
        str(plan_path),
        "--target-root",
        str(target_root),
    )

    assert verify_result.returncode != 0
    assert "managed block mismatch: AGENTS.md" in verify_result.stderr


def test_verify_ignores_unowned_legacy_gemini_file(tmp_path: Path):
    plan_path = _write_generated_plan(tmp_path)
    target_root = tmp_path / "target"

    apply_result = _run(
        "scripts/install/apply.py",
        str(plan_path),
        "--target-root",
        str(target_root),
        "--allow-runtime-hooks",
    )
    assert apply_result.returncode == 0, apply_result.stdout + apply_result.stderr

    gemini = target_root / "GEMINI.md"
    gemini.write_text("legacy local Gemini notes\n", encoding="utf-8")

    verify_result = _run(
        "scripts/install/verify.py",
        str(plan_path),
        "--target-root",
        str(target_root),
    )

    assert verify_result.returncode == 0, verify_result.stdout + verify_result.stderr


# --- toml-agents-merge engine (S1) -----------------------------------------


def _foreign_region(text: str) -> str:
    """Return the destination text up to the first owned-or-foreign agents table.

    The synthetic source owns only doc_curator/designer, so the user `[agents."foo"]`
    region is foreign and is asserted separately. Everything before the first
    `[agents.` header is non-agents content that must be byte-preserved.
    """
    marker = '[agents."'
    idx = text.index(marker)
    return text[:idx]


def test_merge_toml_agents_preserves_non_owned_bytes_and_appends_owned():
    merged = merge_toml_agents(
        SYNTHETIC_RICH_CONFIG_TOML,
        SYNTHETIC_HARNESS_SOURCE_TOML,
        _TOML_MERGE_ARTIFACT,
    )

    # Every non-agents byte region is preserved verbatim (top-level keys plus
    # [projects.*], [plugins.*], [mcp_servers.*] with nested env, [hooks.state]).
    assert _foreign_region(SYNTHETIC_RICH_CONFIG_TOML) in merged
    # The user-authored foreign agents table survives byte-for-byte.
    assert (
        '[agents."foo"]\n'
        'description = "user authored agent"\n'
        'config_file = "agents/foo.toml"\n'
    ) in merged
    # The harness owned tables are present.
    assert '[agents."doc_curator"]' in merged
    assert '[agents."designer"]' in merged

    # Parse-level: foreign content keeps exact values; owned tables added.
    import tomllib

    parsed = tomllib.loads(merged)
    assert parsed["model"] == "gpt-5"
    assert parsed["model_reasoning_effort"] == "high"
    assert parsed["approval_policy"] == "on-request"
    assert parsed["sandbox_mode"] == "workspace-write"
    assert parsed["notify"] == ["say", "done"]
    assert parsed["projects"]["/fixture-home/repo"]["trust_level"] == "trusted"
    assert parsed["plugins"]["example"]["enabled"] is True
    assert parsed["mcp_servers"]["local"]["command"] == "node"
    assert parsed["mcp_servers"]["local"]["env"]["TOKEN"] == "user-secret-placeholder"
    assert parsed["hooks"]["state"]["last_run"] == "2026-01-01"
    assert parsed["agents"]["foo"]["description"] == "user authored agent"
    assert parsed["agents"]["doc_curator"]["description"] == "harness doc curator"
    assert parsed["agents"]["designer"]["config_file"] == "agents/designer.toml"


def test_merge_toml_agents_is_idempotent_fixed_point():
    once = merge_toml_agents(
        SYNTHETIC_RICH_CONFIG_TOML,
        SYNTHETIC_HARNESS_SOURCE_TOML,
        _TOML_MERGE_ARTIFACT,
    )
    twice = merge_toml_agents(
        once,
        SYNTHETIC_HARNESS_SOURCE_TOML,
        _TOML_MERGE_ARTIFACT,
    )
    assert twice == once


def test_merge_toml_agents_replaces_only_owned_existing_table():
    # Destination already carries a stale harness `doc_curator` table plus the
    # foreign `foo` table. The owned table is replaced; foreign is preserved.
    stale = (
        '[agents."doc_curator"]\n'
        'description = "STALE"\n'
        'config_file = "agents/doc_curator.toml"\n'
        '[agents."foo"]\n'
        'description = "user authored agent"\n'
    )
    merged = merge_toml_agents(stale, SYNTHETIC_HARNESS_SOURCE_TOML, _TOML_MERGE_ARTIFACT)
    assert "STALE" not in merged
    assert "harness doc curator" in merged
    assert '[agents."foo"]' in merged
    assert "user authored agent" in merged


def test_merge_toml_agents_writes_owned_tables_into_empty_destination():
    merged = merge_toml_agents("", SYNTHETIC_HARNESS_SOURCE_TOML, _TOML_MERGE_ARTIFACT)
    assert '[agents."doc_curator"]' in merged
    assert '[agents."designer"]' in merged

    import tomllib

    parsed = tomllib.loads(merged)
    assert set(parsed.get("agents", {})) == {"doc_curator", "designer"}


def test_merge_toml_agents_does_not_remove_foreign_agents_when_source_disjoint():
    # Source owns only doc_curator/designer; a destination foreign agent that is
    # not in the owned set must never be dropped.
    merged = merge_toml_agents(
        SYNTHETIC_RICH_CONFIG_TOML,
        SYNTHETIC_HARNESS_SOURCE_TOML,
        _TOML_MERGE_ARTIFACT,
    )

    import tomllib

    assert "foo" in tomllib.loads(merged)["agents"]


def test_merge_toml_agents_rejects_unsupported_key():
    with pytest.raises(ValueError, match="unsupported toml merge key"):
        merge_toml_agents("", SYNTHETIC_HARNESS_SOURCE_TOML, {"toml_merge_key": "nope"})


def test_merge_toml_agents_rejects_invalid_destination_toml():
    with pytest.raises(ValueError, match="destination is not valid TOML"):
        merge_toml_agents(
            'model = "x"\n[agents."foo"\nbroken',
            SYNTHETIC_HARNESS_SOURCE_TOML,
            _TOML_MERGE_ARTIFACT,
        )


def _toml_plan_artifact() -> dict:
    return {
        "target": "codex",
        "component_id": "harnesskit.agent.doc-curator",
        "source": "dist/codex/.codex/config.toml",
        "destination": ".codex/config.toml",
        "merge_strategy": "toml-agents-merge",
        "toml_merge_key": "codex-agents",
    }


def _toml_contract_plan(artifact: dict) -> dict:
    return {
        "targets": ["codex"],
        "components": ["harnesskit.agent.doc-curator"],
        "runtime_surfaces": [
            {
                "target": "codex",
                "path": ".codex/config.toml",
                "source": "dist/codex/.codex/config.toml",
            }
        ],
        "artifacts": [artifact],
    }


def test_validate_plan_contract_accepts_toml_agents_merge():
    validate_plan_contract(_toml_contract_plan(_toml_plan_artifact()))


def test_validate_plan_contract_rejects_toml_merge_wrong_key():
    artifact = _toml_plan_artifact()
    artifact["toml_merge_key"] = "claude-settings-hooks"
    with pytest.raises(ValueError, match="unsupported toml merge key"):
        validate_plan_contract(_toml_contract_plan(artifact))


def test_validate_plan_contract_rejects_toml_merge_wrong_destination():
    artifact = _toml_plan_artifact()
    artifact["destination"] = ".codex/agents/doc_curator.toml"
    artifact["source"] = "dist/codex/.codex/agents/doc_curator.toml"
    with pytest.raises(ValueError, match="toml-agents-merge destination is not supported"):
        validate_plan_contract(_toml_contract_plan(artifact))


def test_validate_plan_contract_requires_toml_merge_key():
    artifact = _toml_plan_artifact()
    del artifact["toml_merge_key"]
    with pytest.raises(ValueError, match="Artifact toml_merge_key"):
        validate_plan_contract(_toml_contract_plan(artifact))
