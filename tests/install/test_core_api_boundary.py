"""Structural contract: install CLI/core decoupling boundaries.

These tests pin the public API surfaces that decouple verify.py from
apply.py's privates (FIX A) and plan.py from build.py's privates (FIX B).
They assert both the import-ability of the new public names and the
absence of the old private import lines in the consumer source files.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_apply_exposes_public_merge_aliases() -> None:
    from scripts.install.apply import (
        _merge_json_deep,
        _merge_managed_block,
        merge_json_deep,
        merge_managed_block,
    )
    from scripts.install.managed_artifacts import (
        merge_json_deep as shared_merge_json_deep,
        merge_managed_block as shared_merge_managed_block,
    )

    # Thin aliases: the public names must be the SAME objects as the private
    # implementations, not wrappers/copies (the "no new logic" contract).
    assert merge_managed_block is _merge_managed_block
    assert merge_json_deep is _merge_json_deep
    assert merge_managed_block is shared_merge_managed_block
    assert merge_json_deep is shared_merge_json_deep


def test_managed_artifacts_exposes_shared_install_policy() -> None:
    from scripts.install.managed_artifacts import (  # noqa: F401
        marker_matches,
        merge_json_deep,
        merge_managed_block,
        merge_toml_agents,
        prune_retired_codex_agent_registrations,
    )


def test_verify_does_not_import_apply() -> None:
    verify_src = (REPO_ROOT / "scripts" / "install" / "verify.py").read_text(
        encoding="utf-8"
    )
    assert "from scripts.install.apply import" not in verify_src
    assert "from scripts.install.managed_artifacts import" in verify_src


def test_adapters_api_exposes_public_names() -> None:
    from scripts.adapters.api import (  # noqa: F401
        CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT,
        CODEX_USER_HOOK_OUTPUT,
        build_adapter_outputs,
        combined_append_content,
        project_runtime_roots,
        render_component,
        selected_registry_entries,
    )

    from scripts.adapters.build import (
        _combined_append_content,
        _project_runtime_roots,
        _render_component,
        _selected_registry_entries,
        build,
    )

    # Thin re-export shim: public names must be the SAME objects as build's
    # privates, so the boundary is a pure rename and never diverges in behavior.
    assert combined_append_content is _combined_append_content
    assert project_runtime_roots is _project_runtime_roots
    assert render_component is _render_component
    assert selected_registry_entries is _selected_registry_entries
    assert build_adapter_outputs is build
    assert isinstance(CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT, str)
    assert isinstance(CODEX_USER_HOOK_OUTPUT, str)


def test_plan_does_not_import_build_directly() -> None:
    plan_src = (REPO_ROOT / "scripts" / "install" / "plan.py").read_text(
        encoding="utf-8"
    )
    assert "from scripts.adapters.build import" not in plan_src
