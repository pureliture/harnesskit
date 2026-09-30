"""Stable public adapter API for install-plan consumers.

This is a thin re-export shim over scripts.adapters.build. It gives plan.py
(and any other install-core consumer) a public surface to depend on instead
of importing build.py's private, underscore-prefixed names directly. No new
logic lives here.
"""

from __future__ import annotations

from scripts.adapters.build import (
    CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT,
    CODEX_USER_HOOK_OUTPUT,
    _combined_append_content as combined_append_content,
    _project_runtime_roots as project_runtime_roots,
    _render_component as render_component,
    _selected_registry_entries as selected_registry_entries,
    build as build_adapter_outputs,
)

__all__ = [
    "CODEX_OPTIMAL_RESPONSE_PROMPT_SUBMIT",
    "CODEX_USER_HOOK_OUTPUT",
    "build_adapter_outputs",
    "combined_append_content",
    "project_runtime_roots",
    "render_component",
    "selected_registry_entries",
]
