from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

try:
    from scripts.package.build_verified_macos_package import rgba_png_pixels
    from scripts.package.graph_readability_pixels import (
        GraphPixelAnalysisError,
        analyze_graph_viewport_pixels,
    )
except ModuleNotFoundError:  # Direct execution from scripts/package.
    from build_verified_macos_package import rgba_png_pixels  # type: ignore[no-redef]
    from graph_readability_pixels import (  # type: ignore[no-redef]
        GraphPixelAnalysisError,
        analyze_graph_viewport_pixels,
    )

try:
    from scripts.package.verify_single_instance_macos import (
        BUNDLE_IDENTIFIER,
        CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
        CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIERS,
        COLLECTOR_ID as _SINGLE_INSTANCE_COLLECTOR_ID,
        DEFAULT_TIMEOUT_SECONDS,
        SCREEN_CAPTURE_MODE,
        CollectorEvidence,
        MacOSRuntimeAutomationPort,
        PermissionPreflight,
        RuntimeQualificationError,
        ScreenshotCaptureEvidence,
        _artifact_identity,
        _installed_app_path_verified,
        _package_qualification_identity,
        _permission_gate,
        _same_artifact,
    )
except ModuleNotFoundError:  # Direct execution from scripts/package.
    from verify_single_instance_macos import (  # type: ignore[no-redef]
        BUNDLE_IDENTIFIER,
        CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
        CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIERS,
        COLLECTOR_ID as _SINGLE_INSTANCE_COLLECTOR_ID,
        DEFAULT_TIMEOUT_SECONDS,
        SCREEN_CAPTURE_MODE,
        CollectorEvidence,
        MacOSRuntimeAutomationPort,
        PermissionPreflight,
        RuntimeQualificationError,
        ScreenshotCaptureEvidence,
        _artifact_identity,
        _installed_app_path_verified,
        _package_qualification_identity,
        _permission_gate,
        _same_artifact,
    )


COLLECTOR_ID = _SINGLE_INSTANCE_COLLECTOR_ID


CASE_IDS = (
    "initial-default",
    "left-pointer-drag",
    "right-pointer-drag",
    "separator-keyboard-step",
    "minimum-overshoot-clamped",
    "maximum-overshoot-clamped",
    "small-window-two-column",
    "small-window-stacked",
    "wide-window-preferred-restored",
    "graph-state-before-collapse",
    "graph-collapsed",
    "graph-expanded-state-restored",
    "graph-hover-layout-stable",
    "dashboard-shared-width",
    "restart-first-visible-persisted",
    "corrupt-preference-default-pair",
    "preference-write-denied-session-usable",
    "relation-node-identity-hover",
    "graph-toolbar-single-row",
    "sot-tree-wheel-last-row",
    "sot-tree-keyboard-last-row",
    "sot-tree-filter-offset-restore",
    "sot-tree-roundtrip-offset-restored",
    "3d-atlas-fit-light",
    "3d-atlas-fit-dark",
    "3d-component-direct-select",
    "3d-component-maximum-zoom",
    "matrix-initial-collapsed-map-first-viewport",
    "matrix-expanded-outer-scroll",
    "matrix-collapse-selection-state-preserved",
    "matrix-bounded-fluid-wrap",
    "workflow-relation-focus",
    "workflow-step-hover-no-camera",
    "workflow-step-selection-invariant",
    "workflow-camera-restore",
    "workflow-matrix-state-invariant",
    "semantic-fallback-user-transition",
    "renderer-retry-snapshot-preserved",
    "semantic-surface-workflow-parity",
    "pane-left-collapse-reopen-restart",
    "pane-right-collapse-reopen-restart",
    "typography-preset-restart",
    "toolbar-width-preset-matrix",
    "local-all-locations-alignment",
    "local-selection-mouse-scroll-preserved",
    "local-selection-keyboard-nearest-reveal",
    "local-ignore-save-rescan-retains-complete",
    "local-correlation-badge-inspector-exact",
    "local-markdown-preview-safety",
    "final-cleanup-zero",
)
AMENDMENT_CASE_IDS = CASE_IDS[-11:-1]
AMENDMENT_RUNTIME_INTEGRATION_REQUIREMENTS = (
    "MacOSWorkspaceLayoutAutomationPort must expose each amendment action through production AX/CGEvent",
    "promoted cases must use proof_level=macos-ax-cgevent-sck and append to the packaged report",
    "each promoted case must retain one ScreenCaptureKit screenshot and stay within existing target and time limits",
)

GRAPH_READABILITY_CASE_CONFIG = {
    "3d-atlas-fit-light": ("light", "fit"),
    "3d-atlas-fit-dark": ("dark", "fit"),
    "3d-component-direct-select": ("light", "direct-select"),
    "3d-component-maximum-zoom": ("light", "maximum"),
}

_LEGACY_GRAPH_READABILITY_CASE_CONFIG = {
    "graph-readability-fit-light": ("light", "fit"),
    "graph-readability-fit-dark": ("dark", "fit"),
    "graph-readability-direct-select": ("light", "direct-select"),
    "graph-readability-maximum-zoom": ("light", "maximum"),
}

FIXTURE_ANCHOR_COMPONENT_IDS = (
    "runtime-layout-anchor-left",
    "runtime-layout-anchor-middle",
    "runtime-layout-anchor-right",
)

_CANONICAL_ANCHOR_COMPONENT_IDS = tuple(
    f"harnesskit.skill.{component_id}" for component_id in FIXTURE_ANCHOR_COMPONENT_IDS
)
_PROFILE_ID = "runtime-layout-profile"
_CANONICAL_PROFILE_ID = f"harnesskit.profile.{_PROFILE_ID}"
CANONICAL_RUNTIME_GRAPH_KINDS = (
    "skill",
    "agent",
    "hook",
    "rule",
    "command",
    "composite",
)
_CANONICAL_GRAPH_AX_KINDS = CANONICAL_RUNTIME_GRAPH_KINDS
_COLLAPSED_SIDES_BY_LAYOUT_MODE = {
    "three-pane": frozenset(),
    "two-column": frozenset(),
    "stacked": frozenset(),
    "left-collapsed": frozenset({"left"}),
    "right-collapsed": frozenset({"right"}),
    "center-only": frozenset({"left", "right"}),
}

FIXED_LAYOUT_CONTRACT = {
    "default_left_px": 304,
    "default_right_px": 368,
    "left_min_px": 200,
    "left_max_px": 512,
    "right_min_px": 184,
    "right_max_px": 560,
    "center_min_px": 480,
    "divider_width_px": 12,
    "keyboard_step_px": 16,
    "css_geometry_tolerance_px": 1,
    "os_frame_tolerance_pt": 2,
    "steady_sample_interval_ms": 100,
    "steady_sample_count": 3,
    "ax_target_limit": 24,
    "case_timeout_seconds": 15,
    "screenshot_per_case_limit": 1,
    "screenshot_run_limit": 32,
    "two_column_window": [1000, 720],
    "stacked_window": [820, 720],
    "wide_window": [1200, 800],
}

_PERMISSION_OR_API_UNAVAILABLE_CODES = frozenset(
    {
        "accessibility_api_denied",
        "screen_capture_api_denied",
        "screen_capture_api_unsupported",
        "system_events_automation_denied",
        "workspace_ax_api_denied",
        "workspace_cgevent_api_denied",
    }
)

_REDACTIONS = [
    "absolute_paths",
    "raw_ax_tree",
    "raw_logs",
    "secrets",
    "source_bodies",
]

_DEFAULT_LIMITATIONS = [
    "AX actions, geometry observation, and ScreenCaptureKit capture are sequential",
    "3D canvas identity and selection are qualified through the production semantic AX surface; ScreenCaptureKit proves only the visible projection",
    "public macOS APIs do not expose a stable cursor kind; col-resize is a static bundle contract while ScreenCaptureKit proves cursor inclusion and AX proves drag-active state",
]

_CASE_EXPECTATIONS: dict[str, dict[str, object]] = {
    "initial-default": {
        "preferred_pair": {"left_px": 304, "right_px": 368},
        "layout_mode": "three-pane",
    },
    "left-pointer-drag": {
        "active_side_changes": True,
        "opposite_preferred_unchanged": True,
        "publication_delta": 1,
    },
    "right-pointer-drag": {
        "active_side_changes": True,
        "opposite_preferred_unchanged": True,
        "publication_delta": 1,
    },
    "separator-keyboard-step": {
        "sub_actions": [
            {"separator": "left", "key": "ArrowRight", "delta_px": 16},
            {"separator": "left", "key": "ArrowLeft", "delta_px": -16},
            {"separator": "right", "key": "ArrowLeft", "delta_px": 16},
            {"separator": "right", "key": "ArrowRight", "delta_px": -16},
        ],
        "publication_delta": 4,
        "net_pair_change": 0,
    },
    "minimum-overshoot-clamped": {
        "boundaries": [
            {"separator": "left", "edge": "minimum", "value_px": 200},
            {"separator": "right", "edge": "minimum", "value_px": 184},
        ],
        "overlap_count": 0,
    },
    "maximum-overshoot-clamped": {
        "boundaries": [
            {"separator": "left", "edge": "maximum", "value_px": 512},
            {"separator": "right", "edge": "maximum", "value_px": 560},
        ],
        "overlap_count": 0,
    },
    "small-window-two-column": {
        "window_size": [1000, 720],
        "layout_mode": "two-column",
        "separators_active": False,
    },
    "small-window-stacked": {
        "window_size": [820, 720],
        "layout_mode": "stacked",
        "separators_active": False,
    },
    "wide-window-preferred-restored": {
        "window_size": [1200, 800],
        "preferred_pair_preserved": True,
    },
    "graph-state-before-collapse": {
        "anchor_count": 3,
        "sot_load_count": 1,
        "local_scan_start_count": 0,
    },
    "graph-collapsed": {
        "graph_body_ax_present": False,
        "zoom_control_focusable": False,
        "matrix_height_increases": True,
    },
    "graph-expanded-state-restored": {
        "graph_fingerprint_preserved": True,
        "geometry_tolerance_px": 1,
    },
    "graph-hover-layout-stable": {
        "target_component_id": FIXTURE_ANCHOR_COMPONENT_IDS[1],
        "pointer_transport": "cg-event-mouse-move",
        "tooltip_identity_exposed": True,
        "viewport_center_matrix_stable": True,
        "graph_fingerprint_preserved": True,
        "camera_preserved": True,
    },
    "dashboard-shared-width": {"dashboard": "local", "preferred_pair_shared": True},
    "restart-first-visible-persisted": {
        "prearmed_before_launch": True,
        "sequence_gap": False,
        "pair_tolerance_px": 1,
    },
    "corrupt-preference-default-pair": {
        "subcases": ["malformed-json", "unknown-version", "out-of-range-pair"],
        "preferred_pair": {"left_px": 304, "right_px": 368},
    },
    "preference-write-denied-session-usable": {
        "persisted": False,
        "diagnostic_code": "workspace_layout_preference_write_failed",
        "dashboard_usable": True,
    },
    "relation-node-identity-hover": {
        "identity_overlay_visible": True,
        "identity_kinds": ["profile", "workflow"],
        "component_node_unique": True,
        "incidence_count_matches_relation_count": True,
        "canvas_node_ax_frame_count": 0,
    },
    "graph-toolbar-single-row": {
        "center_width_px": 480,
        "legend_camera_same_row": True,
        "camera_controls_visible_focusable": True,
        "local_overflow_only": True,
    },
    "sot-tree-wheel-last-row": {
        "inventory_minimum": 87,
        "transport": "cg-event-wheel",
        "tree_body_scroll_owner": True,
        "last_row_visible_focusable": True,
    },
    "sot-tree-keyboard-last-row": {
        "inventory_minimum": 87,
        "transport": "cg-event-key-end",
        "tree_body_scroll_owner": True,
        "last_row_visible_focusable": True,
    },
    "sot-tree-filter-offset-restore": {
        "offset_tolerance_px": 1,
        "filtered_offset_isolated": True,
        "unfiltered_offset_restored": True,
    },
    "sot-tree-roundtrip-offset-restored": {
        "offset_tolerance_px": 1,
        "selection_focus_identity_preserved": True,
        "unfiltered_offset_restored": True,
    },
    "3d-atlas-fit-light": {
        "appearance": "light",
        "zoom_state": "fit",
        "component_inline_title_count": 0,
        "profile_identity_visible": True,
        "required_kind_tokens": [
            "skill", "agent", "hook", "rule", "command", "composite",
        ],
        "shared_snapshot_fingerprint": True,
        "screenshot_count": 1,
    },
    "3d-atlas-fit-dark": {
        "appearance": "dark",
        "zoom_state": "fit",
        "component_inline_title_count": 0,
        "profile_identity_visible": True,
        "required_kind_tokens": [
            "skill", "agent", "hook", "rule", "command", "composite",
        ],
        "shared_snapshot_fingerprint": True,
        "screenshot_count": 1,
    },
    "3d-component-direct-select": {
        "zoom_state": "direct-select",
        "component_inline_title_count": 0,
        "kind_mark_center_tolerance_px": 1,
        "kind_tokens_stable": True,
        "non_color_selection_cue": True,
        "shared_snapshot_fingerprint": True,
        "screenshot_count": 1,
    },
    "3d-component-maximum-zoom": {
        "zoom_state": "maximum",
        "component_inline_title_count": 0,
        "kind_mark_center_tolerance_px": 1,
        "kind_tokens_stable": True,
        "shared_snapshot_fingerprint": True,
        "screenshot_count": 1,
    },
    "matrix-initial-collapsed-map-first-viewport": {
        "map_expanded": True,
        "matrix_expanded": False,
        "matrix_body_hidden": True,
        "matrix_body_focus_target_count": 0,
        "first_viewport_center_tolerance_px": 1,
    },
    "matrix-expanded-outer-scroll": {
        "map_viewport_preserved": True,
        "graph_fingerprint_preserved": True,
        "matrix_body_natural_flow": True,
        "outer_scroll_owner": "workbench",
        "positive_vertical_scroll_range": True,
    },
    "matrix-collapse-selection-state-preserved": {
        "state_fields_preserved": [
            "selected_component_id",
            "active_profile_id",
            "owning_profile_ids",
            "right_detail_id",
            "camera",
        ],
        "outer_scroll_preserved_or_clamped": True,
        "command_counters_preserved": True,
    },
    "matrix-bounded-fluid-wrap": {
        "profile_card_inline_range_rem": [12.5, 15.0],
        "profile_card_block_range_rem": [4.5, 4.75],
        "member_card_inline_range_rem": [13.5, 18.0],
        "profile_title_font_rem": 0.9375,
        "profile_metadata_font_rem": 0.75,
        "row_major_wrap": True,
        "overlap_count": 0,
        "hard_column_count": 0,
        "content_inset_max_rem": 0.9,
        "duplicate_spacer_band_px": 0,
    },
    "workflow-relation-focus": {
        "identity_overlay_visible": True,
        "workflow_inspector_visible": True,
        "participants_only_highlighted": True,
        "ordinal_groups_visible": True,
        "screenshot_count": 1,
    },
    "workflow-step-hover-no-camera": {
        "camera_pose_preserved": True,
        "matrix_fingerprint_preserved": True,
        "locked_step_unchanged": True,
    },
    "workflow-step-selection-invariant": {
        "locked_step_changed": True,
        "camera_pose_preserved": True,
        "anchor_projection_preserved": True,
        "ordinal_groups_visible": True,
        "screenshot_count": 1,
    },
    "workflow-camera-restore": {
        "pre_workflow_camera_restored": True,
        "selection_cleared": True,
    },
    "workflow-matrix-state-invariant": {
        "matrix_fingerprint_preserved": True,
        "projection_id_preserved": True,
    },
    "semantic-fallback-user-transition": {
        "production_control": "텍스트 보기",
        "semantic_primary_region_visible": True,
        "projection_identity_preserved": True,
        "selection_preserved": True,
        "matrix_fingerprint_preserved": True,
        "inspector_fingerprint_preserved": True,
        "load_scan_count_delta": 0,
        "screenshot_count": 1,
    },
    "renderer-retry-snapshot-preserved": {
        "production_control": "3D 다시 시도",
        "renderer_ready": True,
        "projection_identity_preserved": True,
        "selection_preserved": True,
        "matrix_fingerprint_preserved": True,
        "inspector_fingerprint_preserved": True,
        "load_scan_count_delta": 0,
    },
    "semantic-surface-workflow-parity": {
        "workflow_ids_equal": True,
        "ordinal_occurrences_equal": True,
        "roles_equal": True,
        "selected_expanded_state_equal": True,
        "unresolved_warning_set_equal": True,
    },
    "pane-left-collapse-reopen-restart": {
        "side": "left",
        "hidden_pane_and_divider_absent": True,
        "center_expands": True,
        "collapsed_after_restart": True,
        "preferred_width_restored": True,
        "screenshot_count": 1,
    },
    "pane-right-collapse-reopen-restart": {
        "side": "right",
        "hidden_pane_and_divider_absent": True,
        "center_expands": True,
        "collapsed_after_restart": True,
        "preferred_width_restored": True,
        "screenshot_count": 1,
    },
    "typography-preset-restart": {
        "presets": ["Small", "Default", "Large"],
        "token_sets_px": {
            "Small": [11, 12, 13, 15],
            "Default": [12, 13, 14, 16],
            "Large": [13, 14, 15, 17],
        },
        "persisted_after_restart": True,
        "screenshot_count": 1,
    },
    "toolbar-width-preset-matrix": {
        "widths_px": [1200, 960, 760],
        "presets": ["Small", "Default", "Large"],
        "overlap_count": 0,
        "crop_count": 0,
        "screenshot_count": 1,
    },
    "local-all-locations-alignment": {
        "label": "전체 위치",
        "visible_ax_target": True,
        "screenshot_count": 1,
    },
    "local-selection-mouse-scroll-preserved": {
        "transport": "cg-event-mouse-click",
        "scroll_value_preserved": True,
        "screenshot_count": 1,
    },
    "local-selection-keyboard-nearest-reveal": {
        "transport": "cg-event-keyboard-arrow-down",
        "selected_row_visible": True,
        "screenshot_count": 1,
    },
    "local-ignore-save-rescan-retains-complete": {
        "transport": "ax-press",
        "editor_text_redacted": True,
        "last_complete_remains_visible": True,
        "screenshot_count": 1,
    },
    "local-correlation-badge-inspector-exact": {
        "supported_states": ["verified", "drift", "ambiguous", "uncorrelated"],
        "safe_badge_and_inspector": True,
        "screenshot_count": 1,
    },
    "local-markdown-preview-safety": {
        "direct_ax_semantic_role_order": ["heading", "list", "list", "table"],
        "identity_bound_reopen": True,
        "lossless_connect_attempt_count": 0,
        "structured_evidence_negative_scan": True,
        "screenshot_count": 1,
    },
    "final-cleanup-zero": {
        "main_process_count": 0,
        "main_window_count": 0,
        "helper_process_count": 0,
    },
}

# Isolated historical validator tests keep these aliases, while CASE_IDS and
# packaged orchestration use only the approved 3D names above.
for _legacy_case_id, _approved_case_id in {
    "profile-supernode-identity-hover": "relation-node-identity-hover",
    "graph-readability-fit-light": "3d-atlas-fit-light",
    "graph-readability-fit-dark": "3d-atlas-fit-dark",
    "graph-readability-direct-select": "3d-component-direct-select",
    "graph-readability-maximum-zoom": "3d-component-maximum-zoom",
}.items():
    _CASE_EXPECTATIONS[_legacy_case_id] = dict(
        _CASE_EXPECTATIONS[_approved_case_id]
    )


_AMENDMENT_TYPOGRAPHY_TOKENS = {
    "Small": [11, 12, 13, 15],
    "Default": [12, 13, 14, 16],
    "Large": [13, 14, 15, 17],
}
_AMENDMENT_TOOLBAR_TARGETS = (
    "left-disclosure",
    "identity",
    "repository",
    "checkout-register",
    "load-sot",
    "appearance",
    "typography",
    "right-disclosure",
)
_AMENDMENT_TOOLBAR_TAB_TARGETS = (
    "left-disclosure",
    "repository",
    "checkout-register",
    "load-sot",
    "appearance",
    "typography",
    "right-disclosure",
)
_AMENDMENT_TOOLBAR_VISUAL_TARGETS = (
    "left-disclosure",
    "identity",
    "repository",
    "appearance",
    "right-disclosure",
)


def validate_amendment_case_observation(
    case_id: str, payload: dict[str, object]
) -> None:
    """Fail closed on packaged AX/CGEvent/ScreenCaptureKit amendment evidence."""

    if (
        case_id not in AMENDMENT_CASE_IDS
        or payload.get("case_id") != case_id
        or payload.get("proof_level") != "macos-ax-cgevent-sck"
    ):
        raise RuntimeQualificationError("workspace_amendment_case_invalid", case_id)
    limits = payload.get("limits")
    if not isinstance(limits, dict):
        raise RuntimeQualificationError("workspace_amendment_limits_missing", case_id)
    ax_targets = limits.get("ax_target_count")
    screenshots = limits.get("screenshot_count")
    elapsed_ms = limits.get("elapsed_ms")
    if (
        isinstance(ax_targets, bool)
        or not isinstance(ax_targets, int)
        or ax_targets < 0
        or ax_targets > int(FIXED_LAYOUT_CONTRACT["ax_target_limit"])
        or isinstance(screenshots, bool)
        or not isinstance(screenshots, int)
        or screenshots != 1
        or isinstance(elapsed_ms, bool)
        or not isinstance(elapsed_ms, int)
        or elapsed_ms < 0
        or elapsed_ms > int(FIXED_LAYOUT_CONTRACT["case_timeout_seconds"]) * 1000
    ):
        raise RuntimeQualificationError("workspace_amendment_limits_mismatch", case_id)
    action = payload.get("action")
    observation = payload.get("observation")
    if not isinstance(action, dict) or not isinstance(observation, dict):
        raise RuntimeQualificationError("workspace_amendment_observation_missing", case_id)

    if case_id in {
        "pane-left-collapse-reopen-restart",
        "pane-right-collapse-reopen-restart",
    }:
        _validate_amendment_pane(case_id, action, observation)
    elif case_id == "typography-preset-restart":
        _validate_amendment_typography(case_id, action, observation)
    elif case_id == "toolbar-width-preset-matrix":
        _validate_amendment_toolbar(case_id, action, observation)
    elif case_id == "local-all-locations-alignment":
        _validate_amendment_local_alignment(case_id, action, observation)
    elif case_id == "local-selection-mouse-scroll-preserved":
        _validate_amendment_local_mouse(case_id, action, observation)
    elif case_id == "local-selection-keyboard-nearest-reveal":
        _validate_amendment_local_keyboard(case_id, action, observation)
    elif case_id == "local-ignore-save-rescan-retains-complete":
        _validate_amendment_ignore_rescan(case_id, action, observation)
    elif case_id == "local-correlation-badge-inspector-exact":
        _validate_amendment_correlation(case_id, action, observation)
    elif case_id == "local-markdown-preview-safety":
        _validate_amendment_markdown_preview_safety(case_id, action, observation)
        identity = payload.get("identity")
        if (
            not isinstance(identity, dict)
            or identity.get("pid") is None
            or identity.get("window_id") != action.get("selection_window_id")
            or identity.get("window_id") != observation.get("preview_window_id")
        ):
            raise RuntimeQualificationError(
                "workspace_amendment_markdown_preview_identity_mismatch", case_id
            )


def _amendment_number(value: object, case_id: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(case_id)
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(case_id)
    return numeric


def _validate_amendment_pane(
    case_id: str, action: dict[str, object], observation: dict[str, object]
) -> None:
    side = "left" if "left" in case_id else "right"
    try:
        collapsed = observation["collapsed"]
        restart = observation["restart"]
        reopened = observation["reopened"]
        if not all(isinstance(item, dict) for item in (collapsed, restart, reopened)):
            raise ValueError
        if (
            action != {"kind": "pane-collapse-reopen-restart", "side": side}
            or not isinstance(observation.get("initial_preferred_width_px"), int)
            or isinstance(observation.get("initial_preferred_width_px"), bool)
            or collapsed.get("collapsed_side") != side
            or collapsed.get("pane_ax_present") is not False
            or collapsed.get("pane_geometry_present") is not False
            or collapsed.get("divider_ax_present") is not False
            or collapsed.get("divider_geometry_present") is not False
            or _amendment_number(collapsed.get("center_width_before_px"), case_id)
            >= _amendment_number(collapsed.get("center_width_after_px"), case_id)
            or restart.get("collapsed_side") != side
            or restart.get("persisted") is not True
            or reopened.get("collapsed_side") is not None
            or reopened.get("preferred_width_px")
            != observation.get("initial_preferred_width_px")
            or reopened.get("pane_ax_present") is not True
            or reopened.get("divider_ax_present") is not True
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RuntimeQualificationError("workspace_amendment_pane_contract_mismatch", case_id)


def _validate_amendment_typography(
    case_id: str, action: dict[str, object], observation: dict[str, object]
) -> None:
    try:
        presets = observation["presets"]
        if not isinstance(presets, list) or len(presets) != 3:
            raise ValueError
        received = {
            record.get("preset"): record.get("tokens_px")
            for record in presets
            if isinstance(record, dict) and record.get("persisted_after_restart") is True
        }
        if action != {"kind": "set-typography-preset-and-restart"} or received != _AMENDMENT_TYPOGRAPHY_TOKENS:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RuntimeQualificationError("workspace_amendment_typography_contract_mismatch", case_id)


def _validate_amendment_toolbar(
    case_id: str, action: dict[str, object], observation: dict[str, object]
) -> None:
    expected = {
        (width, preset, "two-row")
        for width in (1200, 960)
        for preset in _AMENDMENT_TYPOGRAPHY_TOKENS
    } | {
        (760, preset, "three-row")
        for preset in _AMENDMENT_TYPOGRAPHY_TOKENS
    }
    try:
        rows = observation["rows"]
        if not isinstance(rows, list) or len(rows) != len(expected):
            raise ValueError
        actual: set[tuple[int, str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError
            width = row.get("window_width_px")
            preset = row.get("preset")
            layout = row.get("layout")
            frames = row.get("target_frames")
            if (
                isinstance(width, bool)
                or not isinstance(width, int)
                or not isinstance(preset, str)
                or not isinstance(layout, str)
                or row.get("overlap_count") != 0
                or row.get("crop_count") != 0
                or row.get("tab_order") != list(_AMENDMENT_TOOLBAR_TAB_TARGETS)
                or not isinstance(frames, dict)
                or set(frames) != set(_AMENDMENT_TOOLBAR_TARGETS)
            ):
                raise ValueError
            for frame in frames.values():
                if not isinstance(frame, dict):
                    raise ValueError
                x = _amendment_number(frame.get("x"), case_id)
                y = _amendment_number(frame.get("y"), case_id)
                frame_width = _amendment_number(frame.get("width"), case_id)
                frame_height = _amendment_number(frame.get("height"), case_id)
                if x < 0 or y < 0 or frame_width <= 0 or frame_height <= 0:
                    raise ValueError
            visual_rows: list[tuple[float, list[tuple[str, dict[str, object]]]]] = []
            for name in _AMENDMENT_TOOLBAR_VISUAL_TARGETS:
                frame = frames[name]
                origin = _amendment_number(frame.get("y"), case_id)
                row = next(
                    (candidate for candidate in visual_rows if abs(origin - candidate[0]) <= 12),
                    None,
                )
                if row is None:
                    row = (origin, [])
                    visual_rows.append(row)
                row[1].append((name, frame))
            visual_order = [
                name
                for _, items in sorted(visual_rows, key=lambda candidate: candidate[0])
                for name, _ in sorted(
                    items,
                    key=lambda candidate: _amendment_number(
                        candidate[1].get("x"), case_id
                    ),
                )
            ]
            if visual_order != list(_AMENDMENT_TOOLBAR_VISUAL_TARGETS):
                raise ValueError
            row_origins: list[float] = []
            layout_frames = [frames[name] for name in ("identity", "repository", "appearance")]
            for frame in sorted(layout_frames, key=lambda value: float(value["y"])):
                origin = _amendment_number(frame.get("y"), case_id)
                if not row_origins or abs(origin - row_origins[-1]) > 12:
                    row_origins.append(origin)
            if layout != {1: "one-row", 2: "two-row", 3: "three-row"}.get(
                len(row_origins)
            ):
                raise ValueError
            actual.add((width, preset, layout))
        if action != {"kind": "measure-toolbar-matrix"} or actual != expected:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RuntimeQualificationError("workspace_amendment_toolbar_contract_mismatch", case_id)


def _validate_amendment_local_alignment(
    case_id: str, action: dict[str, object], observation: dict[str, object]
) -> None:
    try:
        if (
            action != {"kind": "open-all-locations"}
            or observation.get("label") != "전체 위치"
            or observation.get("visible_ax_target") is not True
            or observation.get("role") != "AXButton"
            or _amendment_number(observation.get("frame_height_px"), case_id) <= 0
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise RuntimeQualificationError("workspace_amendment_local_alignment_mismatch", case_id)


def _validate_amendment_local_mouse(
    case_id: str, action: dict[str, object], observation: dict[str, object]
) -> None:
    try:
        if (
            action != {"kind": "select-local-result", "input": "mouse"}
            or not math.isclose(
                _amendment_number(observation.get("scroll_value_before"), case_id),
                _amendment_number(observation.get("scroll_value_after"), case_id),
                abs_tol=0,
            )
            or observation.get("selection_changed") is not True
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise RuntimeQualificationError("workspace_amendment_local_mouse_scroll_mismatch", case_id)


def _validate_amendment_local_keyboard(
    case_id: str, action: dict[str, object], observation: dict[str, object]
) -> None:
    try:
        target = observation["target_frame"]
        viewport = observation["viewport_frame"]
        if not isinstance(target, dict) or not isinstance(viewport, dict):
            raise ValueError
        target_y = _amendment_number(target.get("y"), case_id)
        target_height = _amendment_number(target.get("height"), case_id)
        viewport_y = _amendment_number(viewport.get("y"), case_id)
        viewport_height = _amendment_number(viewport.get("height"), case_id)
        if (
            action != {"kind": "select-local-result", "input": "keyboard"}
            or observation.get("nearest_reveal") is not True
            or _amendment_number(observation.get("scroll_value_before"), case_id)
            >= _amendment_number(observation.get("scroll_value_after"), case_id)
            or target_y < viewport_y
            or target_y + target_height > viewport_y + viewport_height
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RuntimeQualificationError("workspace_amendment_local_keyboard_scroll_mismatch", case_id)


def _validate_amendment_ignore_rescan(
    case_id: str, action: dict[str, object], observation: dict[str, object]
) -> None:
    try:
        if (
            action != {"kind": "save-ignore-and-rescan"}
            or observation.get("outcome") not in {"accepted", "queued"}
            or observation.get("last_complete_snapshot_before")
            != observation.get("last_complete_snapshot_during")
            or observation.get("last_complete_snapshot_after")
            == observation.get("last_complete_snapshot_during")
            or observation.get("optimistic_removal_count") != 0
            or observation.get("editor_text_in_evidence") is not False
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise RuntimeQualificationError("workspace_amendment_ignore_rescan_mismatch", case_id)


def _validate_amendment_correlation(
    case_id: str, action: dict[str, object], observation: dict[str, object]
) -> None:
    expected_badges = {
        "verified": "SoT 일치",
        "drift": "SoT에서 변경됨",
        "ambiguous": "연결 확인 필요",
        "uncorrelated": None,
    }
    try:
        state = observation.get("state")
        inspector = observation.get("inspector")
        if (
            state not in expected_badges
            or action != {"kind": "inspect-correlation"}
            or observation.get("badge") != expected_badges[state]
            or not isinstance(inspector, str)
            or not inspector
            or "/" in inspector
            or (
                state == "uncorrelated"
                and "확인된 SoT 연결 없음" not in inspector
            )
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RuntimeQualificationError("workspace_amendment_correlation_mismatch", case_id)


def _validate_amendment_markdown_preview_safety(
    case_id: str, action: dict[str, object], observation: dict[str, object]
) -> None:
    """Require identity-bound, direct AX proof that preview rendering stays inert."""

    try:
        ax_roles = observation.get("forbidden_ax_role_counts")
        semantic_role_order = observation.get("semantic_role_order")
        web_area_before = observation.get("app_web_area_identity_before")
        web_area_after = observation.get("app_web_area_identity_after")
        network = observation.get("network_interval")
        negative_scan = observation.get("structured_evidence_negative_scan")
        selection_window_id = action.get("selection_window_id")
        preview_window_id = observation.get("preview_window_id")
        if (
            set(action)
            != {
                "kind",
                "transport",
                "fixture_display_name",
                "selection_window_id",
            }
            or action.get("kind") != "reopen-complex-markdown-preview"
            or action.get("transport")
            != "cg-event-mouse-click-with-bounded-observers"
            or action.get("fixture_display_name") != "runtime-verification"
            or not isinstance(selection_window_id, int)
            or isinstance(selection_window_id, bool)
            or selection_window_id <= 0
            or observation.get("preview_present") is not True
            or preview_window_id != selection_window_id
            or observation.get("selected_display_name") != "runtime-verification"
            or observation.get("selected_state_observed") is not True
            or observation.get("preview_heading") != "전체 원문"
            or observation.get("render_completion_observed") is not True
            or semantic_role_order != ["heading", "list", "list", "table"]
            or observation.get("rendered_semantic_count") != 4
            or ax_roles
            != {"link": 0, "image": 0, "embedded_web_area": 0}
            or web_area_before
            != {
                "role": "AXWebArea",
                "url_class": "tauri-app-local",
                "window_id": selection_window_id,
            }
            or web_area_after != web_area_before
            or not isinstance(network, dict)
            or set(network)
            != {
                "process_scope",
                "collector_id",
                "collector_ready_before_action",
                "event_stream",
                "lossless_connect_attempts",
                "overflow_detected",
                "sequence_gap_detected",
                "audit_token_attribution",
                "short_lived_failed_attempt_coverage",
                "observed_connect_attempt_count",
                "verified_webkit_roles",
                "duration_milliseconds",
            }
            or network.get("process_scope")
            != "verified-main-and-webkit-helper-descendants"
            or network.get("collector_id") != "macos_process_connect_trace_v1"
            or network.get("collector_ready_before_action") is not True
            or network.get("event_stream") is not True
            or network.get("lossless_connect_attempts") is not True
            or network.get("overflow_detected") is not False
            or network.get("sequence_gap_detected") is not False
            or network.get("audit_token_attribution") is not True
            or network.get("short_lived_failed_attempt_coverage") is not True
            or network.get("observed_connect_attempt_count") != 0
            or network.get("verified_webkit_roles")
            != ["webkit_networking", "webkit_web_content"]
            or not isinstance(network.get("duration_milliseconds"), int)
            or isinstance(network.get("duration_milliseconds"), bool)
            or int(network["duration_milliseconds"]) < 250
            or not isinstance(negative_scan, dict)
            or negative_scan.get("forbidden_literal_count") != 0
            or negative_scan.get("prohibited_key_count") != 0
            or negative_scan.get("absolute_path_count") != 0
            or negative_scan.get("checked_literal_count")
            != len(_LOCAL_MARKDOWN_EVIDENCE_FORBIDDEN_LITERALS)
            or negative_scan.get("prohibited_key_name_count")
            != len(_LOCAL_MARKDOWN_EVIDENCE_PROHIBITED_KEYS)
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise RuntimeQualificationError(
            "workspace_amendment_markdown_preview_safety_mismatch", case_id
        )


@dataclass(frozen=True)
class SanitizedFixtureCheckout:
    root: Path
    anchor_component_ids: tuple[str, ...]
    manifest_relative_path: str
    manifest_sha256: str


@dataclass(frozen=True)
class SanitizedLocalScanFixture:
    display_name: str


_LOCAL_MARKDOWN_EVIDENCE_FORBIDDEN_LITERALS = (
    "example.invalid",
    "window.runtimeProbe",
    "preview-link",
    "preview-image",
    "preview-frame",
    "<script",
    "<iframe",
)

_LOCAL_MARKDOWN_EVIDENCE_PROHIBITED_KEYS = frozenset(
    {
        "_".join(("source", "revision")),
        "_".join(("source", "revision", "sha256")),
        "_".join(("fixture", "sha256")),
        "_".join(("skill", "sha256")),
        "_".join(("visible", "text", "sha256")),
        "_".join(("location", "fingerprint")),
    }
)


def _structured_evidence_negative_scan(value: object) -> dict[str, object]:
    """Scan serialized structured evidence without retaining hostile source."""

    serialized = _canonical_json_bytes(value).decode("utf-8")
    literal_count = sum(
        serialized.count(literal)
        for literal in _LOCAL_MARKDOWN_EVIDENCE_FORBIDDEN_LITERALS
    )
    prohibited_key_count = 0
    absolute_path_count = 0

    def visit(candidate: object) -> None:
        nonlocal prohibited_key_count, absolute_path_count
        if isinstance(candidate, dict):
            for key, nested in candidate.items():
                if key in _LOCAL_MARKDOWN_EVIDENCE_PROHIBITED_KEYS:
                    prohibited_key_count += 1
                visit(nested)
            return
        if isinstance(candidate, (list, tuple)):
            for nested in candidate:
                visit(nested)
            return
        if isinstance(candidate, str):
            windows_absolute = (
                len(candidate) >= 3
                and candidate[0].isalpha()
                and candidate[1] == ":"
                and candidate[2] in {"/", "\\"}
            )
            if candidate.startswith("/") or windows_absolute:
                absolute_path_count += 1

    visit(value)
    return {
        "forbidden_literal_count": literal_count,
        "prohibited_key_count": prohibited_key_count,
        "absolute_path_count": absolute_path_count,
        "checked_literal_count": len(_LOCAL_MARKDOWN_EVIDENCE_FORBIDDEN_LITERALS),
        "prohibited_key_name_count": len(_LOCAL_MARKDOWN_EVIDENCE_PROHIBITED_KEYS),
    }


class WorkspaceLayoutAutomationPort(Protocol):
    """High-level product driver boundary used by the 32-case orchestrator."""

    def prepare_collector(self, evidence_dir: Path) -> CollectorEvidence: ...

    def preflight(self) -> PermissionPreflight: ...

    def launch_primary(self, artifact: object, environment: dict[str, str]) -> int: ...

    def load_fixture_after_picker_cancel(
        self,
        pid: int,
        checkout: Path,
        anchor_component_ids: tuple[str, ...],
        timeout_seconds: float,
    ) -> dict[str, object]: ...

    def exercise_case(
        self, case_id: str, pid: int, timeout_seconds: float
    ) -> dict[str, object]: ...

    def wait_for_layout_publication(
        self, pid: int, timeout_seconds: float
    ) -> dict[str, object]: ...

    def terminate(self, pid: int) -> bool: ...

    def current_primary_pid(self) -> int | None: ...

    def prepare_picker_termination(
        self, pid: int, timeout_seconds: float
    ) -> dict[str, object]: ...

    def wait_for_zero(self, timeout_seconds: float) -> dict[str, int]: ...

    def prearm_first_visible(
        self, artifact: object, timeout_seconds: float
    ) -> object: ...

    def observe_first_visible(
        self,
        arm: object,
        pid: int,
        expected_pair: dict[str, int],
        timeout_seconds: float,
    ) -> dict[str, object]: ...

    def cancel_first_visible(self, arm: object) -> None: ...

    def observe_workspace_markdown_preview_safety(
        self, pid: int
    ) -> dict[str, object]: ...

    def capture_window(
        self,
        owner_pid: int,
        window_id: int,
        destination: Path,
        *,
        include_cursor: bool,
    ) -> ScreenshotCaptureEvidence: ...


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _run_git(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeQualificationError("fixture_git_initialization_failed", "fixture")


def create_sanitized_fixture_checkout(root: Path) -> SanitizedFixtureCheckout:
    """Create a real Git checkout accepted by production canonical readers.

    The public fixture manifest uses stable extent aliases required by the
    evidence contract. `canonical_component_id` records the repo-schema identity
    actually loaded by the production reader, without embedding an absolute path.
    """

    root.mkdir(parents=True, exist_ok=False)
    components_root = root / "components"
    components_dir = components_root / "skills"
    profiles_dir = root / "profiles"
    schemas_dir = root / "schemas"
    components_dir.mkdir(parents=True)
    profiles_dir.mkdir()
    schemas_dir.mkdir()

    repository_root = Path(__file__).resolve().parents[2]
    schema_names = (
        "component.schema.json",
        "agent.schema.json",
        "workflow.schema.json",
        "composite.schema.json",
        "profile.schema.json",
    )
    for schema_name in schema_names:
        source = repository_root / "schemas" / schema_name
        if source.is_symlink() or not source.is_file():
            raise RuntimeQualificationError("fixture_schema_missing", schema_name)
        shutil.copy2(source, schemas_dir / schema_name)

    registry_lines = ['version: "1"', "components:"]
    manifest_components: list[dict[str, str]] = []

    def register_component(
        component_id: str,
        kind: str,
        relative: str,
        manifest_lines: list[str],
    ) -> None:
        registry_lines.extend(
            [
                f"  {component_id}:",
                f"    kind: {kind}",
                "    status: stable",
                f"    path: {relative}",
            ]
        )
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            "\n".join([*manifest_lines, ""]), encoding="utf-8"
        )

    for alias, canonical_id, extent in zip(
        FIXTURE_ANCHOR_COMPONENT_IDS,
        _CANONICAL_ANCHOR_COMPONENT_IDS,
        ("left", "middle", "right"),
        strict=True,
    ):
        slug = canonical_id.rsplit(".", 1)[-1]
        relative = f"components/skills/{slug}/component.yml"
        register_component(
            canonical_id,
            "skill",
            relative,
            [
                f"component_id: {canonical_id}",
                "kind: skill",
                "status: stable",
                "domain: core",
                f"title: Runtime Layout {extent.title()} Anchor",
                f"summary: Sanitized {extent} extent anchor for packaged layout qualification.",
            ],
        )
        manifest_components.append(
            {
                "component_id": alias,
                "canonical_component_id": canonical_id,
                "extent": extent,
            }
        )

    # Tree qualification needs real overflow, multiple Profile owners, and all
    # canonical visual kinds. These records are ordinary canonical source, not
    # a release-only hook: the packaged reader parses them through the same
    # schema/registry path as a user checkout.
    special_components = (
        (
            "harnesskit.agent.runtime-layout-agent",
            "agent",
            "components/agents/runtime-layout-agent/component.yml",
            [
                "component_id: harnesskit.agent.runtime-layout-agent",
                "kind: agent",
                "status: stable",
                "domain: core",
                "title: Runtime Layout Agent",
                "summary: Sanitized agent kind representative.",
            ],
        ),
        (
            "harnesskit.workflow.runtime-layout-workflow",
            "workflow",
            "components/workflows/runtime-layout-workflow/workflow.yml",
            [
                "workflow_id: harnesskit.workflow.runtime-layout-workflow",
                "kind: workflow",
                "status: stable",
                "domain: core",
                "summary: Sanitized workflow kind representative.",
                "description: Sanitized workflow kind representative.",
                "steps:",
                "  - id: step-1",
                f"    skill: {_CANONICAL_ANCHOR_COMPONENT_IDS[1]}",
                "  - id: step-2",
                f"    skill: {_CANONICAL_ANCHOR_COMPONENT_IDS[1]}",
            ],
        ),
        (
            "harnesskit.hook.runtime-layout-hook",
            "hook",
            "components/hooks/runtime-layout-hook/component.yml",
            [
                "component_id: harnesskit.hook.runtime-layout-hook",
                "kind: hook",
                "status: stable",
                "domain: core",
                "title: Runtime Layout Hook",
                "summary: Sanitized hook kind representative.",
            ],
        ),
        (
            "harnesskit.rule.runtime-layout-rule",
            "rule",
            "components/rules/runtime-layout-rule/component.yml",
            [
                "component_id: harnesskit.rule.runtime-layout-rule",
                "kind: rule",
                "status: stable",
                "domain: core",
                "title: Runtime Layout Rule",
                "summary: Sanitized rule kind representative.",
            ],
        ),
        (
            "harnesskit.command.runtime-layout-command",
            "command",
            "components/commands/runtime-layout-command/component.yml",
            [
                "component_id: harnesskit.command.runtime-layout-command",
                "kind: command",
                "status: stable",
                "domain: core",
                "title: Runtime Layout Command",
                "summary: Sanitized command kind representative.",
            ],
        ),
        (
            "harnesskit.composite.runtime-layout-last",
            "composite",
            "components/composites/runtime-layout-last/composite.yml",
            [
                "composite_id: harnesskit.composite.runtime-layout-last",
                "kind: composite",
                "status: stable",
                "title: Runtime Layout Composite",
                "summary: Sanitized composite kind representative.",
                "domain: core",
                f"members: [{_CANONICAL_ANCHOR_COMPONENT_IDS[0]}]",
            ],
        ),
    )
    for component_id, kind, relative, lines in special_components:
        register_component(component_id, kind, relative, lines)

    for index in range(78):
        slug = f"runtime-layout-bulk-{index:03d}"
        component_id = f"harnesskit.skill.{slug}"
        register_component(
            component_id,
            "skill",
            f"components/skills/{slug}/component.yml",
            [
                f"component_id: {component_id}",
                "kind: skill",
                "status: stable",
                "domain: core",
                f"title: Runtime Layout Bulk {index:03d}",
                "summary: Sanitized overflow row for packaged tree qualification.",
            ],
        )
    (root / "components/registry.yml").write_text(
        "\n".join([*registry_lines, ""]), encoding="utf-8"
    )
    (profiles_dir / "runtime-layout.yml").write_text(
        "\n".join(
            [
                f"profile_id: {_CANONICAL_PROFILE_ID}",
                "status: stable",
                "title: Runtime Layout Profile",
                "summary: Sanitized profile for packaged workspace layout qualification.",
                "components:",
                *(f"  - {component_id}" for component_id in _CANONICAL_ANCHOR_COMPONENT_IDS),
                "install_policy:",
                "  default_scope: project",
                "  allowed_scopes: [project]",
                "  activation_policy: manual",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (profiles_dir / "runtime-layout-shared.yml").write_text(
        "\n".join(
            [
                "profile_id: harnesskit.profile.runtime-layout-shared",
                "status: stable",
                "title: Runtime Layout Shared",
                "summary: Second sanitized owner for shared membership qualification.",
                "components:",
                f"  - {_CANONICAL_ANCHOR_COMPONENT_IDS[1]}",
                "install_policy:",
                "  default_scope: project",
                "  allowed_scopes: [project]",
                "  activation_policy: manual",
                "",
            ]
        ),
        encoding="utf-8",
    )
    supplemental_profiles = (
        (
            "runtime-layout-left.yml",
            "harnesskit.profile.runtime-layout-left",
            "Runtime Layout Left",
            _CANONICAL_ANCHOR_COMPONENT_IDS[0],
        ),
        (
            "runtime-layout-right.yml",
            "harnesskit.profile.runtime-layout-right",
            "Runtime Layout Right",
            _CANONICAL_ANCHOR_COMPONENT_IDS[2],
        ),
        (
            "runtime-layout-agent.yml",
            "harnesskit.profile.runtime-layout-agent",
            "Runtime Layout Agent",
            "harnesskit.agent.runtime-layout-agent",
        ),
    )
    for filename, profile_id, title, component_id in supplemental_profiles:
        (profiles_dir / filename).write_text(
            "\n".join(
                [
                    f"profile_id: {profile_id}",
                    "status: stable",
                    f"title: {title}",
                    "summary: Supplemental sanitized profile for fluid matrix wrap qualification.",
                    "components:",
                    f"  - {component_id}",
                    "install_policy:",
                    "  default_scope: project",
                    "  allowed_scopes: [project]",
                    "  activation_policy: manual",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    manifest_relative_path = "fixture-manifest.json"
    manifest_path = root / manifest_relative_path
    _write_json(
        manifest_path,
        {
            "schema_version": 1,
            "anchor_component_ids": list(FIXTURE_ANCHOR_COMPONENT_IDS),
            "profile_id": _PROFILE_ID,
            "components": manifest_components,
        },
    )

    _run_git(root, "init", "-q")
    _run_git(root, "add", ".")
    _run_git(
        root,
        "-c",
        "user.name=Harness Runtime Verifier",
        "-c",
        "user.email=runtime-verifier" "@invalid.example",
        "commit",
        "-q",
        "-m",
        "runtime fixture",
    )
    return SanitizedFixtureCheckout(
        root=root,
        anchor_component_ids=FIXTURE_ANCHOR_COMPONENT_IDS,
        manifest_relative_path=manifest_relative_path,
        manifest_sha256=_sha256_file(manifest_path),
    )


def create_sanitized_local_scan_fixture(root: Path) -> SanitizedLocalScanFixture:
    """Create the only project-scoped Local harness inside the isolated HOME."""

    skill_relative_path = ".agents/skills/runtime-verification/SKILL.md"
    skill = root / skill_relative_path
    skill.parent.mkdir(parents=True, exist_ok=False)
    body = (
        "---\n"
        "name: runtime-verification\n"
        "description: Disposable packaged runtime verification skill with inert preview probes.\n"
        "---\n"
        "# Runtime Verification\n"
        "\n"
        "An *emphasized* and **strong** paragraph with `inline-code` and "
        "[external link](https://example.invalid/preview-link).\n"
        "\n"
        "1. Ordered item\n"
        "   - Nested unordered item\n"
        "2. Ordered item two\n"
        "\n"
        "> Inert blockquote.\n"
        "\n"
        "```text\n"
        "runtime fenced code\n"
        "```\n"
        "\n"
        "| Kind | State |\n"
        "| --- | --- |\n"
        "| Skill | Ready |\n"
        "\n"
        "---\n"
        "\n"
        "<div>literal raw html</div>\n"
        "\n"
        "![external image](https://example.invalid/preview-image.png)\n"
        "<img src=\"https://example.invalid/preview-image-raw.png\">\n"
        "<script>window.runtimeProbe = 'blocked'</script>\n"
        "<iframe src=\"https://example.invalid/preview-frame\"></iframe>\n"
    ).encode()
    skill.write_bytes(body)
    (root / ".harnesskitignore").write_text(
        "# isolated packaged runtime fixture\n", encoding="utf-8"
    )
    return SanitizedLocalScanFixture(
        display_name="runtime-verification",
    )


def _empty_case(case_id: str, status: str, limitations: list[str]) -> dict[str, object]:
    record: dict[str, object] = {
        "case_id": case_id,
        "started_at": None,
        "ended_at": None,
        "expected": _CASE_EXPECTATIONS[case_id],
        "action": None,
        "before": None,
        "after": None,
        "status": status,
        "observation": None,
        "screenshot": None,
        "preference": None,
        "command_counters": None,
        "redactions": list(_REDACTIONS),
        "limitations": limitations,
    }
    return record


def _preference_evidence(payload: dict[str, object]) -> dict[str, object]:
    after = payload.get("after")
    fields: dict[str, object] = {}
    if isinstance(after, dict):
        for key in ("preferred_pair", "persisted", "diagnostic", "corruption_subcases"):
            if key in after:
                fields[key] = after[key]
    return {"fields": fields, "sha256": _sha256_bytes(_canonical_json_bytes(fields))}


def _case_identity(payload: dict[str, object]) -> tuple[int, int]:
    identity = payload.get("identity")
    if not isinstance(identity, dict):
        raise RuntimeQualificationError("workspace_case_identity_missing", "observation")
    try:
        pid = int(identity["pid"])
        window_id = int(identity["window_id"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeQualificationError("workspace_case_identity_missing", "observation")
    if pid <= 0 or window_id <= 0:
        raise RuntimeQualificationError("workspace_case_identity_missing", "observation")
    return pid, window_id


def _validate_payload_identity(
    payload: dict[str, object], artifact: object, case_id: str
) -> None:
    identity = payload.get("identity")
    if not isinstance(identity, dict):
        raise RuntimeQualificationError("workspace_case_identity_missing", "observation")
    if (
        identity.get("bundle_identifier") != BUNDLE_IDENTIFIER
        or identity.get("build_id") != getattr(artifact, "build_id")
        or identity.get("executable_sha256") != getattr(artifact, "executable_sha256")
    ):
        raise RuntimeQualificationError("workspace_case_identity_mismatch", "observation")
    if case_id == "final-cleanup-zero":
        terminated_pid = identity.get("terminated_primary_pid")
        if (
            identity.get("pid") is not None
            or identity.get("window_id") is not None
            or not isinstance(terminated_pid, int)
            or isinstance(terminated_pid, bool)
            or terminated_pid <= 0
        ):
            raise RuntimeQualificationError(
                "workspace_cleanup_identity_invalid", "final cleanup"
            )
    else:
        _case_identity(payload)


def _case_mapping(payload: dict[str, object], key: str, case_id: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RuntimeQualificationError("workspace_case_evidence_missing", f"{case_id} {key}")
    return value


def _separator_inactive(snapshot: dict[str, object]) -> bool:
    states = snapshot.get("separator_state")
    if not isinstance(states, dict):
        return False
    return all(
        states.get(side) is None
        or (
            isinstance(states.get(side), dict)
            and states[side].get("disabled") is True
            and states[side].get("focused") is not True
        )
        for side in ("left", "right")
    )


def _separator_number(state: dict[str, object], key: str, case_id: str) -> float:
    value = state.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeQualificationError("workspace_separator_value_invalid", case_id)
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RuntimeQualificationError("workspace_separator_value_invalid", case_id)
    return numeric


def _validate_separator_semantics(
    snapshot: dict[str, object], case_id: str
) -> None:
    states = snapshot.get("separator_state")
    if not isinstance(states, dict):
        raise RuntimeQualificationError("required_ax_semantic_missing", case_id)
    mode = snapshot.get("layout_mode")
    effective = _pair(snapshot, "effective_pair")
    tolerance = float(FIXED_LAYOUT_CONTRACT["css_geometry_tolerance_px"])
    for side in ("left", "right"):
        state = states.get(side)
        if not isinstance(state, dict):
            raise RuntimeQualificationError(
                "required_ax_semantic_missing", f"{case_id} {side} separator"
            )
        expected_name = (
            "좌측 탐색 패널 너비" if side == "left" else "우측 상세 패널 너비"
        )
        current = _separator_number(state, "current_value", case_id)
        minimum = _separator_number(state, "minimum_value", case_id)
        maximum = _separator_number(state, "maximum_value", case_id)
        expected_current = float(effective[f"{side}_px"])
        if (
            state.get("role") != "AXSplitter"
            or state.get("name") != expected_name
            or state.get("orientation") != "vertical"
            or not isinstance(state.get("focused"), bool)
            or not _same_within(current, expected_current, tolerance)
            or state.get("value_description") != f"{round(current)}픽셀"
            or state.get("active_state") != "normal"
        ):
            raise RuntimeQualificationError(
                "workspace_separator_semantic_mismatch", f"{case_id} {side}"
            )
        if mode == "three-pane":
            expected_minimum = float(FIXED_LAYOUT_CONTRACT[f"{side}_min_px"])
            global_maximum = float(FIXED_LAYOUT_CONTRACT[f"{side}_max_px"])
            if (
                state.get("focusable") is not True
                or state.get("disabled") is not False
                or not _same_within(minimum, expected_minimum, tolerance)
                or maximum + tolerance < current
                or maximum > global_maximum + tolerance
            ):
                raise RuntimeQualificationError(
                    "workspace_separator_range_mismatch", f"{case_id} {side}"
                )
        elif mode in {"two-column", "stacked"}:
            if (
                not isinstance(state.get("focusable"), bool)
                or state.get("disabled") is not True
                or state.get("focused") is not False
                or not _same_within(minimum, current, tolerance)
                or not _same_within(maximum, current, tolerance)
            ):
                raise RuntimeQualificationError(
                    "workspace_separator_responsive_state_mismatch",
                    f"{case_id} {side}",
                )
        else:
            raise RuntimeQualificationError("workspace_layout_mode_missing", case_id)


def _validate_geometry(snapshot: dict[str, object], case_id: str, *, strict: bool) -> None:
    geometry = snapshot.get("geometry")
    if not isinstance(geometry, dict):
        raise RuntimeQualificationError("workspace_geometry_missing", case_id)
    frames: dict[str, dict[str, float]] = {}
    for key in ("left", "center", "right"):
        frames[key] = _frame(geometry.get(key), f"{case_id} {key}")
    if any(
        _rectangles_overlap(frames[left], frames[right])
        for left, right in (("left", "center"), ("left", "right"), ("center", "right"))
    ):
        raise RuntimeQualificationError("workspace_pane_overlap", case_id)
    if any(
        frame["width"] <= 0 or frame["height"] <= 0 for frame in frames.values()
    ):
        raise RuntimeQualificationError("workspace_center_unusable", case_id)
    css_tolerance = float(FIXED_LAYOUT_CONTRACT["css_geometry_tolerance_px"])
    if frames["center"]["width"] < FIXED_LAYOUT_CONTRACT["center_min_px"]:
        raise RuntimeQualificationError("workspace_center_minimum_violated", case_id)
    if not strict:
        return
    mode = snapshot.get("layout_mode")
    window = _frame(geometry.get("window"), f"{case_id} window")
    shell = _frame(geometry.get("shell"), f"{case_id} shell")
    os_tolerance = float(FIXED_LAYOUT_CONTRACT["os_frame_tolerance_pt"])
    if (
        not _frame_contains(window, shell, os_tolerance)
        or any(
            not _frame_contains(shell, frame, os_tolerance)
            for frame in frames.values()
        )
    ):
        raise RuntimeQualificationError("workspace_pane_outside_window", case_id)
    if mode == "three-pane":
        left_separator = _frame(
            geometry.get("left_separator"), f"{case_id} left separator"
        )
        right_separator = _frame(
            geometry.get("right_separator"), f"{case_id} right separator"
        )
        if not _frame_contains(window, left_separator, os_tolerance) or not _frame_contains(
            window, right_separator, os_tolerance
        ):
            raise RuntimeQualificationError("workspace_separator_outside_window", case_id)
        if not _frame_contains(shell, left_separator, os_tolerance) or not _frame_contains(
            shell, right_separator, os_tolerance
        ):
            raise RuntimeQualificationError("workspace_separator_outside_shell", case_id)
        if any(
            not _same_within(
                separator["width"],
                float(FIXED_LAYOUT_CONTRACT["divider_width_px"]),
                css_tolerance,
            )
            for separator in (left_separator, right_separator)
        ):
            raise RuntimeQualificationError("workspace_separator_width_mismatch", case_id)
        horizontal_chain = (
            (frames["left"]["x"] + frames["left"]["width"], left_separator["x"]),
            (
                left_separator["x"] + left_separator["width"],
                frames["center"]["x"],
            ),
            (
                frames["center"]["x"] + frames["center"]["width"],
                right_separator["x"],
            ),
            (
                right_separator["x"] + right_separator["width"],
                frames["right"]["x"],
            ),
        )
        if any(
            not _same_within(left, right, css_tolerance)
            for left, right in horizontal_chain
        ):
            raise RuntimeQualificationError("workspace_three_pane_tiling_mismatch", case_id)
        if (
            not _same_within(frames["left"]["x"], shell["x"], css_tolerance)
            or not _same_within(
                frames["right"]["x"] + frames["right"]["width"],
                shell["x"] + shell["width"],
                css_tolerance,
            )
            or any(
                not _same_within(frame["y"], shell["y"], css_tolerance)
                or not _same_within(
                    frame["height"], shell["height"], css_tolerance
                )
                for frame in frames.values()
            )
        ):
            raise RuntimeQualificationError("workspace_three_pane_shell_mismatch", case_id)
        if not (
            FIXED_LAYOUT_CONTRACT["left_min_px"] - css_tolerance
            <= frames["left"]["width"]
            <= FIXED_LAYOUT_CONTRACT["left_max_px"] + css_tolerance
            and FIXED_LAYOUT_CONTRACT["right_min_px"] - css_tolerance
            <= frames["right"]["width"]
            <= FIXED_LAYOUT_CONTRACT["right_max_px"] + css_tolerance
        ):
            raise RuntimeQualificationError("workspace_side_width_out_of_bounds", case_id)
    elif mode == "two-column":
        if (
            not _same_within(frames["left"]["x"], shell["x"], css_tolerance)
            or not _same_within(frames["left"]["y"], shell["y"], css_tolerance)
            or not _same_within(
                frames["left"]["height"], shell["height"], css_tolerance
            )
            or frames["left"]["x"] >= frames["center"]["x"]
            or frames["left"]["x"] + frames["left"]["width"]
            > frames["center"]["x"] + css_tolerance
            or not _same_within(
                frames["center"]["x"], frames["right"]["x"], css_tolerance
            )
            or not _same_within(
                frames["center"]["width"],
                frames["right"]["width"],
                css_tolerance,
            )
            or frames["right"]["y"] + css_tolerance
            < frames["center"]["y"] + frames["center"]["height"]
            or not _same_within(
                frames["right"]["y"] + frames["right"]["height"],
                shell["y"] + shell["height"],
                css_tolerance,
            )
            or frames["left"]["width"] + css_tolerance
            < FIXED_LAYOUT_CONTRACT["left_min_px"]
            or frames["right"]["width"] + css_tolerance
            < FIXED_LAYOUT_CONTRACT["right_min_px"]
        ):
            raise RuntimeQualificationError("workspace_two_column_geometry_invalid", case_id)
    elif mode == "stacked":
        if (
            any(
                not _same_within(frame["x"], shell["x"], css_tolerance)
                or not _same_within(frame["width"], shell["width"], css_tolerance)
                for frame in frames.values()
            )
            or not _same_within(frames["left"]["y"], shell["y"], css_tolerance)
            or frames["center"]["y"] + css_tolerance
            < frames["left"]["y"] + frames["left"]["height"]
            or frames["right"]["y"] + css_tolerance
            < frames["center"]["y"] + frames["center"]["height"]
            or not _same_within(
                frames["right"]["y"] + frames["right"]["height"],
                shell["y"] + shell["height"],
                css_tolerance,
            )
            or frames["left"]["width"] + css_tolerance
            < FIXED_LAYOUT_CONTRACT["left_min_px"]
            or frames["right"]["width"] + css_tolerance
            < FIXED_LAYOUT_CONTRACT["right_min_px"]
        ):
            raise RuntimeQualificationError("workspace_stacked_geometry_invalid", case_id)
    else:
        raise RuntimeQualificationError("workspace_layout_mode_missing", case_id)


def _validate_graph_counter(payload: dict[str, object], case_id: str) -> None:
    counters = payload.get("command_counters")
    if counters != {"sot_load_count": 1, "local_scan_start_count": 0}:
        raise RuntimeQualificationError("workspace_command_counter_mismatch", case_id)


def _validate_graph_toggle(
    snapshot: dict[str, object], case_id: str, *, expanded: bool
) -> None:
    toggle = _required_target(
        _target_map(snapshot), "graph-toggle", f"{case_id} graph toggle"
    )
    expected_action = "접기" if expanded else "펼치기"
    if (
        toggle.get("role") != "AXButton"
        or toggle.get("enabled") is not True
        or toggle.get("focusable") is not True
        or toggle.get("expanded_present") is not True
        or toggle.get("expanded") is not expanded
        or not isinstance(toggle.get("name"), str)
        or expected_action not in str(toggle["name"])
    ):
        raise RuntimeQualificationError("workspace_graph_toggle_state_invalid", case_id)


def _camera_pose_equal(expected_pose: object, observed_pose: object) -> bool:
    if expected_pose is None and observed_pose is None:
        return False
    if not isinstance(expected_pose, dict) or not isinstance(observed_pose, dict):
        return False
    for vector in ("position", "target"):
        expected_vector = expected_pose.get(vector)
        observed_vector = observed_pose.get(vector)
        if not isinstance(expected_vector, dict) or not isinstance(
            observed_vector, dict
        ):
            return False
        for axis in ("x", "y", "z"):
            try:
                if not _same_within(
                    float(expected_vector[axis]),
                    float(observed_vector[axis]),
                    tolerance=0.001,
                ):
                    return False
            except (KeyError, TypeError, ValueError):
                return False
    return True


def _semantic_camera_pose(
    snapshot: dict[str, object], operation: str
) -> dict[str, object]:
    semantic_graph = _semantic_graph_case_observation(snapshot, operation)
    camera = _case_mapping(semantic_graph, "camera", operation)
    pose = {
        "position": camera.get("position"),
        "target": camera.get("target"),
    }
    if not _camera_pose_equal(pose, pose):
        raise RuntimeQualificationError("workspace_graph_camera_pose_invalid", operation)
    return pose


def _graph_anchor_hash(snapshot: dict[str, object], operation: str) -> str:
    fingerprint = _case_mapping(snapshot, "graph_fingerprint", operation)
    anchors = fingerprint.get("anchor_nodes")
    if not isinstance(anchors, list) or not anchors:
        raise RuntimeQualificationError("workspace_graph_anchor_observation_invalid", operation)
    return _sha256_bytes(_canonical_json_bytes(anchors))


def _validate_contextual_camera_evidence(
    evidence: object, case_id: str
) -> None:
    expected_keys = {
        "origin",
        "intent",
        "observation_mode",
        "baseline",
        "settled",
        "camera_changed",
        "anchor_hash_before",
        "anchor_hash_after",
    }
    if (
        not isinstance(evidence, dict)
        or set(evidence) != expected_keys
        or evidence.get("origin") != "graph"
        or evidence.get("intent") != "contextual"
        or evidence.get("observation_mode") != "endpoint-only-ax"
        or evidence.get("camera_changed") is not True
        or _camera_pose_equal(evidence.get("baseline"), evidence.get("settled"))
        or not isinstance(evidence.get("anchor_hash_before"), str)
        or len(str(evidence["anchor_hash_before"])) != 64
        or evidence.get("anchor_hash_before") != evidence.get("anchor_hash_after")
    ):
        raise RuntimeQualificationError(
            "workspace_graph_contextual_camera_unconfirmed", case_id
        )


def _graph_fingerprint_equal(expected: object, observed: object) -> bool:
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return False
    for key in (
        "projection_id",
        "settled_node_positions_hash",
        "selected_component_id",
        "right_detail_id",
        "active_profile_id",
        "selected_workflow_id",
        "selected_relation_node_id",
        "locked_step",
        "disclosures",
        "graph_body_identity",
        "sot_load_count",
        "local_scan_start_count",
    ):
        if expected.get(key) != observed.get(key):
            return False
    try:
        if not _same_within(
            float(expected["scale"]), float(observed["scale"]), tolerance=0.001
        ):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    if not _camera_pose_equal(
        expected.get("camera_pose"), observed.get("camera_pose")
    ):
        return False
    return _anchor_nodes_equal(
        expected.get("anchor_nodes"), observed.get("anchor_nodes")
    )


def _anchor_nodes_equal(expected_nodes: object, observed_nodes: object) -> bool:
    if (
        not isinstance(expected_nodes, list)
        or not isinstance(observed_nodes, list)
        or len(expected_nodes) != 3
        or len(observed_nodes) != 3
    ):
        return False
    for left, right in zip(expected_nodes, observed_nodes, strict=True):
        if (
            not isinstance(left, dict)
            or not isinstance(right, dict)
            or left.get("component_id") != right.get("component_id")
        ):
            return False
        left_frame = left.get("frame")
        right_frame = right.get("frame")
        if not isinstance(left_frame, dict) or not isinstance(right_frame, dict):
            return False
        for key in ("x", "y", "width", "height"):
            try:
                if not _same_within(float(left_frame[key]), float(right_frame[key])):
                    return False
            except (KeyError, TypeError, ValueError):
                return False
    return True


def _ordinary_selection_state_evidence(
    before_fingerprint: object,
    after_fingerprint: object,
) -> dict[str, object]:
    if not isinstance(before_fingerprint, dict) or not isinstance(
        after_fingerprint, dict
    ):
        raise RuntimeQualificationError(
            "workspace_graph_fingerprint_invalid", "ordinary selection"
        )
    camera_before = {
        "camera_pose": before_fingerprint.get("camera_pose"),
        "scale": before_fingerprint.get("scale"),
    }
    camera_after = {
        "camera_pose": after_fingerprint.get("camera_pose"),
        "scale": after_fingerprint.get("scale"),
    }
    anchors_before = before_fingerprint.get("anchor_nodes")
    anchors_after = after_fingerprint.get("anchor_nodes")
    evidence: dict[str, object] = {
        "camera_before": camera_before,
        "camera_after": camera_after,
        "camera_preserved": _selection_camera_equal(
            camera_before, camera_after
        ),
        "anchor_nodes_before": anchors_before,
        "anchor_nodes_after": anchors_after,
        "anchor_nodes_preserved": _anchor_nodes_equal(
            anchors_before, anchors_after
        ),
    }
    for fingerprint_key in (
        "node_position_fingerprint",
        "force_state_fingerprint",
    ):
        before_value = before_fingerprint.get(fingerprint_key)
        after_value = after_fingerprint.get(fingerprint_key)
        if before_value is not None or after_value is not None:
            evidence[f"{fingerprint_key}_before"] = before_value
            evidence[f"{fingerprint_key}_after"] = after_value
            evidence[f"{fingerprint_key}_preserved"] = (
                before_value is not None
                and after_value is not None
                and before_value == after_value
            )
    return evidence


def _selection_camera_equal(expected: object, observed: object) -> bool:
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return False
    try:
        scale_equal = _same_within(
            float(expected["scale"]),
            float(observed["scale"]),
            tolerance=0.001,
        )
    except (KeyError, TypeError, ValueError):
        return False
    return scale_equal and _camera_pose_equal(
        expected.get("camera_pose"), observed.get("camera_pose")
    )


def _ordinary_selection_state_preserved(
    evidence: object,
    before_fingerprint: object,
    after_fingerprint: object,
) -> bool:
    if not isinstance(evidence, dict):
        return False
    expected = _ordinary_selection_state_evidence(
        before_fingerprint, after_fingerprint
    )
    mandatory_keys = {
        "camera_before",
        "camera_after",
        "camera_preserved",
        "anchor_nodes_before",
        "anchor_nodes_after",
        "anchor_nodes_preserved",
    }
    if not mandatory_keys.issubset(evidence):
        return False
    for key in mandatory_keys:
        if evidence.get(key) != expected.get(key):
            return False
    if evidence.get("camera_preserved") is not True:
        return False
    if evidence.get("anchor_nodes_preserved") is not True:
        return False
    for fingerprint_key in (
        "node_position_fingerprint",
        "force_state_fingerprint",
    ):
        suffix_keys = {
            f"{fingerprint_key}_before",
            f"{fingerprint_key}_after",
            f"{fingerprint_key}_preserved",
        }
        present = suffix_keys.intersection(evidence)
        if present and present != suffix_keys:
            return False
        if suffix_keys.issubset(expected):
            if any(evidence.get(key) != expected.get(key) for key in suffix_keys):
                return False
            if evidence.get(f"{fingerprint_key}_preserved") is not True:
                return False
        elif present:
            return False
    return True


def _graph_layout_geometry_equal(expected: object, observed: object) -> bool:
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return False
    tolerance = float(FIXED_LAYOUT_CONTRACT["css_geometry_tolerance_px"])
    for region in ("viewport", "center", "matrix"):
        expected_frame = expected.get(region)
        observed_frame = observed.get(region)
        if not isinstance(expected_frame, dict) or not isinstance(observed_frame, dict):
            return False
        for key in ("x", "y", "width", "height"):
            try:
                if not _same_within(
                    float(expected_frame[key]),
                    float(observed_frame[key]),
                    tolerance,
                ):
                    return False
            except (KeyError, TypeError, ValueError):
                return False
    return True


def _semantic_graph_case_observation(
    snapshot: dict[str, object], case_id: str
) -> dict[str, object]:
    graph = snapshot.get("semantic_graph")
    if not isinstance(graph, dict):
        raise RuntimeQualificationError(
            "workspace_semantic_graph_observation_missing", case_id
        )
    for key in ("relations", "components", "workflow_steps"):
        if not isinstance(graph.get(key), list):
            raise RuntimeQualificationError(
                "workspace_semantic_graph_observation_invalid", case_id
            )
    if (
        not isinstance(graph.get("projection_id"), str)
        or re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,200}", str(graph.get("projection_id"))
        )
        is None
        or not isinstance(graph.get("settled_node_positions_hash"), str)
        or re.fullmatch(
            r"[0-9a-f]{16}", str(graph.get("settled_node_positions_hash"))
        )
        is None
        or
        not isinstance(graph.get("identity_overlay"), dict)
        or not isinstance(graph.get("renderer"), dict)
        or not isinstance(graph.get("camera"), dict)
        or not isinstance(graph.get("workflow_inspector"), dict)
    ):
        raise RuntimeQualificationError(
            "workspace_semantic_graph_observation_invalid", case_id
        )
    return graph


def _validated_scene_fit_report(
    graph: dict[str, object], case_id: str
) -> dict[str, object]:
    report = graph.get("fit_report")
    expected_keys = {
        "identity_count",
        "relation_count",
        "component_count",
        "envelope_count",
        "camera_status",
        "in_frustum_envelope_count",
        "total_envelope_count",
        "largest_dimension_occupancy",
        "scene_bounds",
    }
    if not isinstance(report, dict) or set(report) != expected_keys:
        raise RuntimeQualificationError(
            "workspace_scene_fit_report_invalid", case_id
        )

    counts: dict[str, int] = {}
    for key in (
        "identity_count",
        "relation_count",
        "component_count",
        "envelope_count",
        "in_frustum_envelope_count",
        "total_envelope_count",
    ):
        value = report.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeQualificationError(
                "workspace_scene_fit_report_invalid", case_id
            )
        counts[key] = value

    relations = graph.get("relations")
    components = graph.get("components")
    occupancy = _finite_number(
        report.get("largest_dimension_occupancy"), case_id
    )
    bounds = report.get("scene_bounds")
    if (
        not isinstance(relations, list)
        or not isinstance(components, list)
        or counts["relation_count"] != len(relations)
        or counts["component_count"] != len(components)
        or counts["identity_count"]
        != counts["relation_count"] + counts["component_count"]
        or counts["envelope_count"] != counts["identity_count"]
        or counts["total_envelope_count"] != counts["envelope_count"]
        or counts["in_frustum_envelope_count"]
        != counts["total_envelope_count"]
        or report.get("camera_status") != "complete"
        or not 0.75 <= occupancy <= 1
        or not isinstance(bounds, dict)
        or set(bounds) != {"min", "max"}
    ):
        raise RuntimeQualificationError(
            "workspace_scene_fit_report_invalid", case_id
        )

    for axis in ("x", "y", "z"):
        minimum = bounds.get("min")
        maximum = bounds.get("max")
        if (
            not isinstance(minimum, dict)
            or not isinstance(maximum, dict)
            or set(minimum) != {"x", "y", "z"}
            or set(maximum) != {"x", "y", "z"}
            or _finite_number(minimum.get(axis), case_id)
            >= _finite_number(maximum.get(axis), case_id)
        ):
            raise RuntimeQualificationError(
                "workspace_scene_fit_report_invalid", case_id
            )
    return report


def _semantic_selected_records(
    graph: dict[str, object], key: str, case_id: str
) -> list[dict[str, object]]:
    records = graph.get(key)
    if not isinstance(records, list):
        raise RuntimeQualificationError(
            "workspace_semantic_graph_observation_invalid", case_id
        )
    return [
        record
        for record in records
        if isinstance(record, dict) and record.get("selected") is True
    ]


def _semantic_matrix_fingerprint(snapshot: dict[str, object]) -> dict[str, object]:
    matrix = snapshot.get("matrix_layout")
    if not isinstance(matrix, dict):
        return {}
    return {
        key: matrix.get(key)
        for key in (
            "selected_component_id",
            "active_profile_id",
            "owning_profile_ids",
            "right_detail_id",
        )
    }


def _semantic_projection_identity(
    snapshot: dict[str, object], case_id: str
) -> dict[str, object]:
    graph = _semantic_graph_case_observation(snapshot, case_id)
    relations = graph["relations"]
    components = graph["components"]
    workflow_steps = graph["workflow_steps"]
    assert isinstance(relations, list)
    assert isinstance(components, list)
    assert isinstance(workflow_steps, list)
    relation_ids = sorted(
        str(record.get("node_id"))
        for record in relations
        if isinstance(record, dict) and isinstance(record.get("node_id"), str)
    )
    component_ids = sorted(
        str(record.get("component_id"))
        for record in components
        if isinstance(record, dict)
        and isinstance(record.get("component_id"), str)
    )
    step_ids = sorted(
        (
            str(record.get("workflow_id")),
            int(record.get("ordinal")),
            str(record.get("step_id")),
        )
        for record in workflow_steps
        if isinstance(record, dict)
        and isinstance(record.get("workflow_id"), str)
        and isinstance(record.get("ordinal"), int)
        and not isinstance(record.get("ordinal"), bool)
        and isinstance(record.get("step_id"), str)
    )
    if (
        len(relation_ids) != len(relations)
        or len(relation_ids) != len(set(relation_ids))
        or len(component_ids) != len(components)
        or len(component_ids) != len(set(component_ids))
        or len(step_ids) != len(workflow_steps)
        or len(step_ids) != len(set(step_ids))
    ):
        raise RuntimeQualificationError(
            "workspace_semantic_projection_identity_invalid", case_id
        )
    return {
        "projection_id": graph["projection_id"],
        "settled_node_positions_hash": graph[
            "settled_node_positions_hash"
        ],
        "relation_ids": relation_ids,
        "component_ids": component_ids,
        "workflow_step_ids": [list(value) for value in step_ids],
    }


def _semantic_selection_fingerprint(
    snapshot: dict[str, object], case_id: str
) -> dict[str, object]:
    graph = _semantic_graph_case_observation(snapshot, case_id)

    def selected_ids(key: str, identity_key: str) -> list[str]:
        records = graph[key]
        assert isinstance(records, list)
        return sorted(
            str(record.get(identity_key))
            for record in records
            if isinstance(record, dict)
            and record.get("selected") is True
            and isinstance(record.get(identity_key), str)
        )

    identity_overlay = graph["identity_overlay"]
    assert isinstance(identity_overlay, dict)
    overlay_names = {
        key: (
            record.get("name")
            if isinstance((record := identity_overlay.get(key)), dict)
            else None
        )
        for key in ("title", "kind", "count")
    }
    return {
        "relation_ids": selected_ids("relations", "node_id"),
        "component_ids": selected_ids("components", "component_id"),
        "workflow_step_ids": selected_ids("workflow_steps", "dom_identifier"),
        "identity_overlay": overlay_names,
    }


def _semantic_inspector_fingerprint(
    snapshot: dict[str, object], case_id: str
) -> dict[str, object]:
    graph = _semantic_graph_case_observation(snapshot, case_id)
    inspector = graph["workflow_inspector"]
    if not isinstance(inspector, dict):
        raise RuntimeQualificationError(
            "workspace_semantic_inspector_observation_invalid", case_id
        )
    steps = inspector.get("steps")
    occurrences = inspector.get("ordinal_occurrences")
    roles = inspector.get("component_roles")
    warnings = inspector.get("unresolved_warning_set")
    if not all(isinstance(value, list) for value in (steps, occurrences, roles, warnings)):
        raise RuntimeQualificationError(
            "workspace_semantic_inspector_observation_invalid", case_id
        )
    assert isinstance(steps, list)
    assert isinstance(occurrences, list)
    assert isinstance(roles, list)
    assert isinstance(warnings, list)
    return {
        "visible": inspector.get("visible"),
        "workflow_id": inspector.get("workflow_id"),
        "runtime_state": inspector.get("runtime_state"),
        "steps": sorted(
            (
                str(step.get("workflow_id")),
                int(step.get("ordinal")),
                bool(step.get("selected")),
            )
            for step in steps
            if isinstance(step, dict)
            and isinstance(step.get("workflow_id"), str)
            and isinstance(step.get("ordinal"), int)
            and not isinstance(step.get("ordinal"), bool)
        ),
        "ordinal_occurrences": sorted(
            _canonical_json_bytes(value).decode("utf-8")
            for value in occurrences
            if isinstance(value, dict)
        ),
        "component_roles": sorted(
            _canonical_json_bytes(value).decode("utf-8")
            for value in roles
            if isinstance(value, dict)
        ),
        "unresolved_warning_set": sorted(
            _canonical_json_bytes(value).decode("utf-8")
            for value in warnings
            if isinstance(value, dict)
        ),
    }


def _validate_graph_hover_case(
    payload: dict[str, object],
    action: dict[str, object],
    before: dict[str, object] | None,
    after: dict[str, object],
    *,
    strict: bool,
) -> None:
    case_id = "graph-hover-layout-stable"
    during = payload.get("during")
    if before is None or not isinstance(during, dict):
        raise RuntimeQualificationError(
            "workspace_graph_hover_observation_missing", case_id
        )

    expected_component_id = _CANONICAL_ANCHOR_COMPONENT_IDS[1]
    expected_target = f"semantic-component:{expected_component_id}"
    hover_target = action.get("hover_target")
    try:
        target_frame = _frame(
            hover_target.get("frame") if isinstance(hover_target, dict) else None,
            case_id,
        )
        pointer_point = (
            hover_target.get("pointer_point")
            if isinstance(hover_target, dict)
            else None
        )
        pointer_x = _finite_number(
            pointer_point.get("x") if isinstance(pointer_point, dict) else None,
            case_id,
        )
        pointer_y = _finite_number(
            pointer_point.get("y") if isinstance(pointer_point, dict) else None,
            case_id,
        )
    except RuntimeQualificationError:
        raise RuntimeQualificationError(
            "workspace_graph_hover_target_mismatch", case_id
        )
    if (
        action.get("kind") != "graph-node-hover"
        or action.get("transport") != "cg-event-mouse-move"
        or action.get("target") != expected_target
        or action.get("leave_target") != "graph-viewport"
        or not isinstance(hover_target, dict)
        or hover_target.get("target") != expected_target
        or hover_target.get("component_id") != expected_component_id
        or hover_target.get("role") != "AXButton"
        or hover_target.get("visible") is not True
        or target_frame["width"] <= 0
        or target_frame["height"] <= 0
        or not target_frame["x"] <= pointer_x <= target_frame["x"] + target_frame["width"]
        or not target_frame["y"] <= pointer_y <= target_frame["y"] + target_frame["height"]
        or payload.get("publication_delta") != 0
    ):
        raise RuntimeQualificationError(
            "workspace_graph_hover_target_mismatch", case_id
        )

    if strict:
        _validate_geometry(during, case_id, strict=True)
        _validate_separator_semantics(during, case_id)
    baseline_geometry = before.get("graph_layout_geometry")
    if (
        not _graph_layout_geometry_equal(
            baseline_geometry, during.get("graph_layout_geometry")
        )
        or not _graph_layout_geometry_equal(
            baseline_geometry, after.get("graph_layout_geometry")
        )
    ):
        raise RuntimeQualificationError(
            "workspace_graph_hover_layout_shifted", case_id
        )

    tooltip = during.get("graph_tooltip_identity")
    if (
        before.get("graph_tooltip_identity") is not None
        or after.get("graph_tooltip_identity") is not None
        or not isinstance(tooltip, dict)
        or tooltip.get("component_id") != expected_component_id
        or not isinstance(tooltip.get("title"), str)
        or not tooltip.get("title")
        or tooltip.get("visible") is not True
    ):
        raise RuntimeQualificationError(
            "workspace_graph_hover_tooltip_identity_mismatch", case_id
        )
    try:
        tooltip_frame = _frame(tooltip.get("frame"), case_id)
    except RuntimeQualificationError:
        raise RuntimeQualificationError(
            "workspace_graph_hover_tooltip_identity_mismatch", case_id
        )
    if tooltip_frame["width"] <= 0 or tooltip_frame["height"] <= 0:
        raise RuntimeQualificationError(
            "workspace_graph_hover_tooltip_identity_mismatch", case_id
        )

    baseline_fingerprint = before.get("graph_fingerprint")
    if (
        not _graph_fingerprint_equal(
            baseline_fingerprint, during.get("graph_fingerprint")
        )
        or not _graph_fingerprint_equal(
            baseline_fingerprint, after.get("graph_fingerprint")
        )
    ):
        raise RuntimeQualificationError(
            "workspace_graph_hover_camera_changed", case_id
        )


def _validate_case_invariants(
    case_id: str,
    payload: dict[str, object],
    graph_baseline: dict[str, object] | None = None,
) -> None:
    """Validate the approved case contract independently of the automation port.

    Production evidence sets `proof_level=macos-ax-cgevent-sck`, which enables
    physical-frame assertions. Protocol fakes remain useful for orchestration
    tests while still being checked for every case's typed product outcome.
    """

    if case_id in AMENDMENT_CASE_IDS:
        validate_amendment_case_observation(case_id, payload)
        return

    strict = payload.get("proof_level") == "macos-ax-cgevent-sck"
    action = _case_mapping(payload, "action", case_id)
    after = _case_mapping(payload, "after", case_id)
    before_value = payload.get("before")
    before = before_value if isinstance(before_value, dict) else None
    publication_delta = payload.get("publication_delta", 0)

    if case_id != "final-cleanup-zero":
        _validate_geometry(after, case_id, strict=strict)
        if strict:
            _validate_separator_semantics(after, case_id)
    if case_id in {
        "graph-state-before-collapse",
        "graph-collapsed",
        "graph-expanded-state-restored",
    }:
        _validate_graph_counter(payload, case_id)
    elif case_id != "final-cleanup-zero":
        counters = payload.get("command_counters")
        if (
            not isinstance(counters, dict)
            or counters.get("local_scan_start_count") != 0
            or not isinstance(counters.get("sot_load_count"), int)
            or int(counters["sot_load_count"]) < 1
        ):
            raise RuntimeQualificationError("workspace_command_counter_mismatch", case_id)

    if case_id == "initial-default":
        if after.get("layout_mode") != "three-pane" or _pair(after, "preferred_pair") != {
            "left_px": 304,
            "right_px": 368,
        }:
            raise RuntimeQualificationError("workspace_default_pair_mismatch", case_id)
        if _pair(after, "effective_pair") != {"left_px": 304, "right_px": 368}:
            raise RuntimeQualificationError("workspace_default_geometry_mismatch", case_id)
    elif case_id in {"left-pointer-drag", "right-pointer-drag"}:
        if before is None or publication_delta != 1 or action.get("kind") != "pointer-drag":
            raise RuntimeQualificationError("workspace_pointer_contract_mismatch", case_id)
        if strict:
            active_target = action.get("active_target")
            expected_target = (
                "left-divider" if case_id == "left-pointer-drag" else "right-divider"
            )
            active_side = "left_px" if case_id.startswith("left") else "right_px"
            try:
                active_current = _finite_number(
                    active_target.get("current_value")
                    if isinstance(active_target, dict)
                    else None,
                    case_id,
                )
                ordered_sequences = (
                    int(action["mouse_down_sequence"]),
                    int(action["drag_sequence"]),
                    int(action["capture_sequence"]),
                    int(action["mouse_up_sequence"]),
                )
                ordered_times = (
                    float(action["mouse_down_monotonic_seconds"]),
                    float(action["drag_posted_monotonic_seconds"]),
                    float(action["capture_started_monotonic_seconds"]),
                    float(action["capture_completed_monotonic_seconds"]),
                    float(action["mouse_up_monotonic_seconds"]),
                )
            except (KeyError, TypeError, ValueError):
                raise RuntimeQualificationError(
                    "workspace_drag_capture_proof_missing", case_id
                )
            if (
                action.get("target") != expected_target
                or action.get("captured_before_mouseup") is not True
                or ordered_sequences != (1, 2, 3, 4)
                or tuple(sorted(ordered_times)) != ordered_times
                or not all(math.isfinite(value) for value in ordered_times)
                or not isinstance(active_target, dict)
                or active_target.get("target") != expected_target
                or active_target.get("visible") is not True
                or active_target.get("role") != "AXSplitter"
                or active_target.get("orientation") != "vertical"
                or active_target.get("active_state") != "dragging"
                or active_target.get("value_description")
                != f"{round(active_current)}픽셀 · 드래그 중"
                or not _same_within(
                    active_current,
                    float(_pair(after, "effective_pair")[active_side]),
                    float(FIXED_LAYOUT_CONTRACT["css_geometry_tolerance_px"]),
                )
            ):
                raise RuntimeQualificationError(
                    "workspace_drag_capture_proof_invalid", case_id
                )
        side = "left_px" if case_id.startswith("left") else "right_px"
        opposite = "right_px" if side == "left_px" else "left_px"
        before_pair = _pair(before, "preferred_pair")
        after_pair = _pair(after, "preferred_pair")
        if after_pair[side] == before_pair[side] or after_pair[opposite] != before_pair[opposite]:
            raise RuntimeQualificationError("workspace_pointer_independence_mismatch", case_id)
    elif case_id == "separator-keyboard-step":
        if before is None or action.get("key") != "ArrowRight":
            raise RuntimeQualificationError("workspace_keyboard_contract_mismatch", case_id)
        before_pair = _pair(before, "preferred_pair")
        after_pair = _pair(after, "preferred_pair")
        if strict:
            sub_actions = action.get("sub_actions")
            expected = (
                ("left", "ArrowRight", 16),
                ("left", "ArrowLeft", -16),
                ("right", "ArrowLeft", 16),
                ("right", "ArrowRight", -16),
            )
            if (
                publication_delta != 4
                or after_pair != before_pair
                or not isinstance(sub_actions, list)
                or len(sub_actions) != len(expected)
            ):
                raise RuntimeQualificationError(
                    "workspace_keyboard_contract_mismatch", case_id
                )
            for entry, (side, key, delta) in zip(
                sub_actions, expected, strict=True
            ):
                if (
                    not isinstance(entry, dict)
                    or entry.get("separator") != side
                    or entry.get("key") != key
                    or entry.get("delta_px") != delta
                    or entry.get("focused") is not True
                    or entry.get("focused_readback") is not True
                    or not isinstance(entry.get("before_value"), (int, float))
                    or isinstance(entry.get("before_value"), bool)
                    or not isinstance(entry.get("after_value"), (int, float))
                    or isinstance(entry.get("after_value"), bool)
                    or entry.get("after_value") - entry.get("before_value")
                    != delta
                    or entry.get("opposite_after")
                    != entry.get("opposite_before")
                    or entry.get("ax_value_before") != entry.get("before_value")
                    or entry.get("ax_value_after") != entry.get("after_value")
                ):
                    raise RuntimeQualificationError(
                        "workspace_keyboard_step_mismatch", case_id
                    )
            before_revision = before.get("layout_revision")
            after_revision = after.get("layout_revision")
            if (
                not isinstance(before_revision, int)
                or isinstance(before_revision, bool)
                or not isinstance(after_revision, int)
                or isinstance(after_revision, bool)
                or after_revision - before_revision != 4
            ):
                raise RuntimeQualificationError(
                    "workspace_keyboard_publication_mismatch", case_id
                )
        elif (
            publication_delta != 1
            or after_pair["left_px"] - before_pair["left_px"] != 16
            or after_pair["right_px"] != before_pair["right_px"]
        ):
            raise RuntimeQualificationError("workspace_keyboard_step_mismatch", case_id)
    elif case_id in {
        "minimum-overshoot-clamped",
        "maximum-overshoot-clamped",
    }:
        minimum = case_id == "minimum-overshoot-clamped"
        expected_pair = (
            {"left_px": 200, "right_px": 184}
            if minimum
            else {"left_px": 200, "right_px": 560}
        )
        expected_publication_delta = 2 if minimum else 3
        if (
            publication_delta != expected_publication_delta
            or _pair(after, "preferred_pair") != expected_pair
            or _pair(after, "effective_pair") != expected_pair
        ):
            raise RuntimeQualificationError(
                "workspace_minimum_clamp_mismatch"
                if minimum
                else "workspace_maximum_clamp_mismatch",
                case_id,
            )
        if strict:
            before_revision = before.get("layout_revision") if before else None
            after_revision = after.get("layout_revision")
            if (
                not isinstance(before_revision, int)
                or isinstance(before_revision, bool)
                or not isinstance(after_revision, int)
                or isinstance(after_revision, bool)
                or after_revision - before_revision != expected_publication_delta
                or (
                    not minimum
                    and (
                        action.get("window_width") != 1600
                        or action.get("window_height") != 800
                    )
                )
            ):
                raise RuntimeQualificationError(
                    "workspace_keyboard_clamp_mismatch", case_id
                )
            if not minimum:
                reset = action.get("between_subcases_reset")
                if (
                    not isinstance(reset, dict)
                    or reset.get("separator") != "left"
                    or reset.get("before_value") != 512
                    or reset.get("after_value") != 200
                    or reset.get("opposite_before") != 184
                    or reset.get("opposite_after") != 184
                    or reset.get("publication_delta") != 1
                ):
                    raise RuntimeQualificationError(
                        "workspace_keyboard_clamp_mismatch", case_id
                    )
            expected_subactions = (
                (
                    ("left", "minimum", "ArrowLeft", -4096, 200),
                    ("right", "minimum", "ArrowRight", 4096, 184),
                )
                if minimum
                else (
                    ("left", "maximum", "ArrowRight", 4096, 512),
                    ("right", "maximum", "ArrowLeft", -4096, 560),
                )
            )
            sub_actions = action.get("sub_actions")
            if (
                action.get("kind") != "boundary-subcases"
                or not isinstance(sub_actions, list)
                or len(sub_actions) != len(expected_subactions)
            ):
                raise RuntimeQualificationError(
                    "workspace_keyboard_clamp_mismatch", case_id
                )
            for entry, (side, edge, key, drag_delta, value) in zip(
                sub_actions, expected_subactions, strict=True
            ):
                clamp = entry.get("keyboard_clamp") if isinstance(entry, dict) else None
                if (
                    not isinstance(entry, dict)
                    or entry.get("separator") != side
                    or entry.get("edge") != edge
                    or entry.get("drag_delta_px") != drag_delta
                    or entry.get("expected_value") != value
                    or entry.get("pointer_publication_delta") != 1
                    or entry.get("opposite_before") != entry.get("opposite_after")
                    or not isinstance(clamp, dict)
                    or clamp.get("key") != key
                    or clamp.get("before_value") != value
                    or clamp.get("after_value") != value
                    or clamp.get("publication_delta") != 0
                    or clamp.get("focused_readback") is not True
                    or clamp.get("ax_value_before") != value
                    or clamp.get("ax_value_after") != value
                ):
                    raise RuntimeQualificationError(
                        "workspace_keyboard_clamp_mismatch", case_id
                    )
    elif case_id == "small-window-two-column":
        if (
            action.get("width") != 1000
            or action.get("height") != 720
            or after.get("layout_mode") != "two-column"
            or not _separator_inactive(after)
        ):
            raise RuntimeQualificationError("workspace_two_column_contract_mismatch", case_id)
    elif case_id == "small-window-stacked":
        if (
            action.get("width") != 820
            or action.get("height") != 720
            or after.get("layout_mode") != "stacked"
            or not _separator_inactive(after)
        ):
            raise RuntimeQualificationError("workspace_stacked_contract_mismatch", case_id)
    elif case_id == "wide-window-preferred-restored":
        if action.get("width") != 1200 or action.get("height") != 800 or after.get("layout_mode") != "three-pane":
            raise RuntimeQualificationError("workspace_wide_restore_contract_mismatch", case_id)
        if strict and _pair(after, "effective_pair") != _pair(after, "preferred_pair"):
            raise RuntimeQualificationError("workspace_preferred_restore_mismatch", case_id)
    elif case_id == "graph-state-before-collapse":
        graph = after.get("graph_fingerprint")
        if not isinstance(graph, dict) or len(graph.get("anchor_nodes", [])) != 3:
            raise RuntimeQualificationError("graph_anchor_identity_mismatch", case_id)
        if (
            graph.get("selected_component_id") != FIXTURE_ANCHOR_COMPONENT_IDS[1]
            or graph.get("right_detail_id")
            != FIXTURE_ANCHOR_COMPONENT_IDS[1]
            or graph.get("active_profile_id") != _PROFILE_ID
            or graph.get("sot_load_count") != 1
            or graph.get("local_scan_start_count") != 0
        ):
            raise RuntimeQualificationError("graph_fingerprint_invalid", case_id)
        if strict:
            expected_target = (
                f"semantic-component:{_CANONICAL_ANCHOR_COMPONENT_IDS[1]}"
            )
            semantic_graph = _semantic_graph_case_observation(after, case_id)
            selected_records = _semantic_selected_records(
                semantic_graph, "components", case_id
            )
            if (
                action.get("kind") != "select-graph-state"
                or action.get("transport") != "ax-press-and-cg-event"
                or action.get("target") != expected_target
                or len(selected_records) != 1
                or selected_records[0].get("component_id")
                != _CANONICAL_ANCHOR_COMPONENT_IDS[1]
            ):
                raise RuntimeQualificationError(
                    "workspace_graph_selection_proof_invalid", case_id
                )
            _validate_graph_toggle(after, case_id, expanded=True)
    elif case_id == "graph-collapsed":
        if before is None or after.get("graph_body_ax_present") is not False or after.get("zoom_control_focusable") is not False:
            raise RuntimeQualificationError("workspace_graph_body_remained_accessible", case_id)
        setup_layout = action.get("matrix_setup_layout")
        setup_disclosures = (
            setup_layout.get("disclosures")
            if isinstance(setup_layout, dict)
            else None
        )
        setup_matrix = (
            setup_disclosures.get("profile_matrix")
            if isinstance(setup_disclosures, dict)
            else None
        )
        try:
            setup_height = _finite_number(
                action.get("matrix_setup_usable_height"), case_id
            )
            after_height = _finite_number(
                after.get("matrix_usable_height"), case_id
            )
        except RuntimeQualificationError:
            raise RuntimeQualificationError(
                "workspace_graph_matrix_did_not_expand", case_id
            ) from None
        if (
            action.get("kind") != "toggle-graph"
            or action.get("expanded") is not False
            or action.get("matrix_setup") != "expanded"
            or not isinstance(setup_matrix, dict)
            or setup_matrix.get("expanded") is not True
            or setup_matrix.get("body_present") is not True
            or setup_height < 0
            or after_height <= setup_height
        ):
            raise RuntimeQualificationError("workspace_graph_matrix_did_not_expand", case_id)
        collapsed_layout = after.get("collapsed_workbench_layout")
        try:
            if not isinstance(collapsed_layout, dict):
                raise ValueError
            stable_ids = collapsed_layout.get("stable_ids")
            center = _frame(collapsed_layout.get("center_frame"), case_id)
            first_viewport = _frame(
                collapsed_layout.get("first_viewport_frame"), case_id
            )
            matrix_header = _frame(
                collapsed_layout.get("matrix_header_frame"), case_id
            )
            matrix_body = _frame(
                collapsed_layout.get("matrix_body_frame"), case_id
            )
            first_viewport_bottom = (
                first_viewport["y"] + first_viewport["height"]
            )
            matrix_header_bottom = matrix_header["y"] + matrix_header["height"]
            if (
                stable_ids
                != {
                    "center": "workbench",
                    "first_viewport": "sot-workbench-first-viewport",
                    "matrix_header": "profile-matrix",
                    "matrix_body": "profile-matrix-body",
                }
                or collapsed_layout.get("component_map_expanded") is not False
                or collapsed_layout.get("profile_matrix_expanded") is not True
                or first_viewport["height"] <= 0
                or first_viewport["height"] >= center["height"]
                or matrix_header["height"] <= 0
                or matrix_body["height"] <= 0
                or not _same_within(first_viewport["x"], center["x"])
                or not _same_within(first_viewport["width"], center["width"])
                or not _same_within(matrix_header["x"], first_viewport["x"])
                or not _same_within(
                    matrix_header["width"], first_viewport["width"]
                )
                or matrix_header["y"] < first_viewport["y"] - 1
                or not _same_within(
                    matrix_header_bottom, first_viewport_bottom
                )
                or not _same_within(matrix_body["x"], first_viewport["x"])
                or not _same_within(matrix_body["width"], first_viewport["width"])
                or not _same_within(matrix_body["y"], first_viewport_bottom)
            ):
                raise ValueError
        except (TypeError, ValueError, RuntimeQualificationError):
            raise RuntimeQualificationError(
                "workspace_graph_collapsed_layout_mismatch", case_id
            ) from None
        if strict:
            _validate_graph_toggle(after, case_id, expanded=False)
    elif case_id == "graph-expanded-state-restored":
        if before is None or after.get("graph_body_ax_present") is not True:
            raise RuntimeQualificationError("workspace_graph_body_not_restored", case_id)
        if not _graph_fingerprint_equal(
            graph_baseline, after.get("graph_fingerprint")
        ):
            raise RuntimeQualificationError("workspace_graph_fingerprint_changed", case_id)
        if strict:
            _validate_graph_toggle(after, case_id, expanded=True)
    elif case_id == "graph-hover-layout-stable":
        _validate_graph_hover_case(
            payload, action, before, after, strict=strict
        )
    elif case_id == "dashboard-shared-width":
        if after.get("dashboard") != "local":
            raise RuntimeQualificationError("workspace_dashboard_switch_failed", case_id)
        if strict and before is not None and _pair(after, "preferred_pair") != _pair(before, "preferred_pair"):
            raise RuntimeQualificationError("workspace_dashboard_pair_mismatch", case_id)
        if strict and before is not None and _pair(after, "effective_pair") != _pair(before, "effective_pair"):
            raise RuntimeQualificationError("workspace_dashboard_effective_pair_mismatch", case_id)
    elif case_id == "relation-node-identity-hover":
        relation_snapshot = payload.get("during")
        if not isinstance(relation_snapshot, dict):
            raise RuntimeQualificationError(
                "relation_node_identity_contract_mismatch", case_id
            )
        graph = _semantic_graph_case_observation(relation_snapshot, case_id)
        identity = graph.get("identity_overlay")
        relations = graph.get("relations")
        components = graph.get("components")
        workflow_steps = graph.get("workflow_steps")
        try:
            if not isinstance(identity, dict):
                raise ValueError
            title_value = identity.get("title")
            kind_value = identity.get("kind")
            count_value = identity.get("count")
            if not all(
                isinstance(value, dict)
                for value in (title_value, kind_value, count_value)
            ):
                raise ValueError
            component_ids = [
                str(component["component_id"])
                for component in components
                if isinstance(component, dict)
            ]
            relation_kinds = {
                str(relation["relation_kind"])
                for relation in relations
                if isinstance(relation, dict)
            }
            relation_by_id = {
                str(relation["node_id"]): relation
                for relation in relations
                if isinstance(relation, dict)
            }
            target = action.get("target")
            if not isinstance(target, str) or not target.startswith(
                "semantic-relation:"
            ):
                raise ValueError
            hovered_node_id = target.removeprefix("semantic-relation:")
            hovered = relation_by_id.get(hovered_node_id)
            if not isinstance(hovered, dict):
                raise ValueError
            hovered_kind = str(hovered.get("relation_kind")).lower()
            hovered_count = hovered.get("exact_count")
            hovered_unit = "step" if hovered_kind == "workflow" else "component"
            expected_count_label = (
                f"{hovered_count} {hovered_unit}"
                if hovered_count == 1
                else f"{hovered_count} {hovered_unit}s"
            )
            all_component_ids = set(component_ids)
            for relation in relation_by_id.values():
                members = relation.get("member_component_ids")
                exact_count = relation.get("exact_count")
                if (
                    not isinstance(members, list)
                    or len(members) != len(set(members))
                    or not set(members).issubset(all_component_ids)
                    or not isinstance(exact_count, int)
                    or isinstance(exact_count, bool)
                    or exact_count < 0
                ):
                    raise ValueError
                if relation.get("relation_kind") == "profile" and exact_count != len(members):
                    raise ValueError
                if relation.get("relation_kind") == "workflow":
                    authored_steps = [
                        step
                        for step in workflow_steps
                        if isinstance(step, dict)
                        and step.get("workflow_id") == relation.get("canonical_id")
                    ]
                    if exact_count != len(authored_steps):
                        raise ValueError
            if (
                action.get("kind") != "semantic-relation-hover"
                or action.get("transport") != "cg-event-mouse-move"
                or payload.get("publication_delta") != 0
                or not isinstance(relations, list)
                or not isinstance(components, list)
                or not isinstance(workflow_steps, list)
                or not {"profile", "workflow"}.issubset(relation_kinds)
                or len(component_ids) != len(set(component_ids))
                or hovered.get("visible") is not True
                or not isinstance(title_value.get("name"), str)
                or not str(title_value.get("name"))
                or "harnesskit." in str(title_value.get("name")).lower()
                or str(kind_value.get("name")).lower()
                != hovered_kind
                or str(count_value.get("name")) != expected_count_label
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise RuntimeQualificationError(
                "relation_node_identity_contract_mismatch", case_id
            ) from None
    elif case_id == "profile-supernode-identity-hover":
        observation = after.get("profile_supernode")
        hover = action.get("hover_identity")
        try:
            if not isinstance(observation, dict) or not isinstance(hover, dict):
                raise ValueError
            profile_frames = observation.get("profile_body_frames")
            component_frames = observation.get("component_body_frames")
            if (
                action.get("kind") != "profile-node-hover"
                or action.get("transport") != "cg-event-mouse-move"
                or not isinstance(action.get("target"), str)
                or not str(action["target"]).startswith("profile:")
                or not isinstance(profile_frames, list)
                or len(profile_frames) < 2
                or not isinstance(component_frames, list)
                or len(component_frames) < 3
                or observation.get("component_node_unique") is not True
                or observation.get("membership_edge_count")
                != observation.get("membership_owner_count")
                or not isinstance(observation.get("membership_edge_count"), int)
                or int(observation["membership_edge_count"]) < 2
                or payload.get("publication_delta") != 0
            ):
                raise ValueError

            profile_ids: set[str] = set()
            profile_types: set[str] = set()
            profile_widths: list[float] = []
            profile_heights: list[float] = []
            matching_hover: dict[str, object] | None = None
            for entry in profile_frames:
                if not isinstance(entry, dict):
                    raise ValueError
                node_id = entry.get("node_id")
                node_type = entry.get("type")
                frame = _frame(entry.get("frame"), case_id)
                if (
                    not isinstance(node_id, str)
                    or not node_id
                    or node_id in profile_ids
                    or node_type not in {"Profile", "Unprofiled"}
                    or not isinstance(entry.get("name"), str)
                    or not entry.get("name")
                    or not isinstance(entry.get("member_count"), int)
                    or isinstance(entry.get("member_count"), bool)
                    or int(entry["member_count"]) < 0
                    or not isinstance(entry.get("active"), bool)
                    or frame["width"] <= 0
                    or frame["height"] <= 0
                ):
                    raise ValueError
                profile_ids.add(node_id)
                profile_types.add(str(node_type))
                profile_widths.append(float(frame["width"]))
                profile_heights.append(float(frame["height"]))
                if node_id == hover.get("node_id"):
                    matching_hover = entry

            component_ids: set[str] = set()
            component_widths: list[float] = []
            component_heights: list[float] = []
            for entry in component_frames:
                if not isinstance(entry, dict):
                    raise ValueError
                component_id = entry.get("component_id")
                frame = _frame(entry.get("frame"), case_id)
                if (
                    not isinstance(component_id, str)
                    or not component_id
                    or component_id in component_ids
                    or frame["width"] <= 0
                    or frame["height"] <= 0
                ):
                    raise ValueError
                component_ids.add(component_id)
                component_widths.append(float(frame["width"]))
                component_heights.append(float(frame["height"]))

            profile_width = statistics.median(profile_widths)
            profile_height = statistics.median(profile_heights)
            component_width = statistics.median(component_widths)
            component_height = statistics.median(component_heights)
            hover_frame = _frame(hover.get("frame"), case_id)
            matching_frame = (
                _frame(matching_hover.get("frame"), case_id)
                if isinstance(matching_hover, dict)
                else None
            )
            if (
                profile_types != {"Profile", "Unprofiled"}
                or not 3.0 <= profile_width / component_width <= 4.0
                or not 3.0 <= profile_height / component_height <= 4.0
                or matching_hover is None
                or hover.get("type") != matching_hover.get("type")
                or hover.get("name") != matching_hover.get("name")
                or hover.get("member_count") != matching_hover.get("member_count")
                or hover.get("active") != matching_hover.get("active")
                or hover.get("visible") is not True
                or matching_frame is None
                or any(
                    not _same_within(hover_frame[key], matching_frame[key])
                    for key in ("x", "y", "width", "height")
                )
                or not _graph_layout_geometry_equal(
                    action.get("before_graph_layout_geometry"),
                    action.get("after_graph_layout_geometry"),
                )
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, ZeroDivisionError, RuntimeQualificationError):
            raise RuntimeQualificationError(
                "profile_supernode_contract_mismatch", case_id
            )
    elif case_id == "graph-toolbar-single-row":
        toolbar = after.get("graph_toolbar")
        try:
            if not isinstance(toolbar, dict) or action.get("kind") != "observe-toolbar":
                raise ValueError
            legend = _frame(toolbar.get("legend_frame"), case_id)
            camera = _frame(toolbar.get("camera_frame"), case_id)
            row = _frame(toolbar.get("toolbar_frame"), case_id)
            graph_body = _frame(toolbar.get("graph_body_frame"), case_id)
            matrix = _frame(toolbar.get("matrix_frame"), case_id)
            viewport_width = _finite_number(toolbar.get("viewport_width_px"), case_id)
            scroll_width = _finite_number(toolbar.get("legend_scroll_width_px"), case_id)
            client_width = _finite_number(toolbar.get("legend_client_width_px"), case_id)
            controls = toolbar.get("camera_controls")
            if (
                not _same_within(viewport_width, 480)
                or not _same_within(legend["y"], camera["y"])
                or not _same_within(legend["height"], camera["height"])
                or row["height"] > max(legend["height"], camera["height"]) + 1
                or row["width"] > graph_body["width"] + 1
                or scroll_width < client_width
                or matrix["y"] < graph_body["y"] + graph_body["height"] - 1
                or not isinstance(controls, list)
                or len(controls) != 3
                or {entry.get("name") for entry in controls if isinstance(entry, dict)}
                != {"축소", "Fit", "확대"}
                or any(
                    not isinstance(entry, dict)
                    or entry.get("visible") is not True
                    or entry.get("focusable") is not True
                    for entry in controls
                )
            ):
                raise ValueError
        except (TypeError, ValueError, RuntimeQualificationError):
            raise RuntimeQualificationError("graph_toolbar_contract_mismatch", case_id)
    elif case_id in {"sot-tree-wheel-last-row", "sot-tree-keyboard-last-row"}:
        tree = after.get("sot_tree")
        expected_transport = (
            "cg-event-wheel"
            if case_id == "sot-tree-wheel-last-row"
            else "cg-event-key-end"
        )
        error_code = (
            "sot_tree_wheel_contract_mismatch"
            if case_id == "sot-tree-wheel-last-row"
            else "sot_tree_keyboard_contract_mismatch"
        )
        try:
            if not isinstance(tree, dict):
                raise ValueError
            outer = _frame(tree.get("outer_frame"), case_id)
            scroll = _frame(tree.get("scroll_frame"), case_id)
            last_row = tree.get("last_row")
            scroll_top = _finite_number(tree.get("scroll_top_px"), case_id)
            scroll_height = _finite_number(tree.get("scroll_height_px"), case_id)
            client_height = _finite_number(tree.get("client_height_px"), case_id)
            if not isinstance(last_row, dict):
                raise ValueError
            last_frame = _frame(last_row.get("frame"), case_id)
            if (
                action.get("kind") != "tree-scroll"
                or action.get("transport") != expected_transport
                or not isinstance(tree.get("inventory_count"), int)
                or isinstance(tree.get("inventory_count"), bool)
                or int(tree["inventory_count"]) < 87
                or scroll_height <= client_height
                or scroll_top <= 0
                or scroll_top > scroll_height - client_height + 1
                or scroll["x"] < outer["x"] - 1
                or scroll["y"] < outer["y"] - 1
                or scroll["x"] + scroll["width"] > outer["x"] + outer["width"] + 1
                or scroll["y"] + scroll["height"] > outer["y"] + outer["height"] + 1
                or last_row.get("visible") is not True
                or last_row.get("focusable") is not True
                or not isinstance(last_row.get("identity"), str)
                or not last_row.get("identity")
                or last_frame["y"] < scroll["y"] - 1
                or last_frame["y"] + last_frame["height"]
                > scroll["y"] + scroll["height"] + 1
                or (
                    case_id == "sot-tree-keyboard-last-row"
                    and (
                        last_row.get("focused") is not True
                        or tree.get("focused_identity") != last_row.get("identity")
                    )
                )
            ):
                raise ValueError
        except (TypeError, ValueError, RuntimeQualificationError):
            raise RuntimeQualificationError(error_code, case_id)
    elif case_id == "sot-tree-filter-offset-restore":
        tree = after.get("sot_tree")
        try:
            before_offset = _finite_number(action.get("before_offset_px"), case_id)
            filtered_offset = _finite_number(action.get("filtered_offset_px"), case_id)
            restored_offset = _finite_number(action.get("restored_offset_px"), case_id)
            if (
                not isinstance(tree, dict)
                or action.get("kind") != "tree-filter-offset-restore"
                or not isinstance(action.get("filter_text"), str)
                or not action.get("filter_text")
                or action.get("filter_cleared") is not True
                or _same_within(before_offset, filtered_offset)
                or not _same_within(before_offset, restored_offset)
                or not _same_within(
                    _finite_number(tree.get("scroll_top_px"), case_id),
                    restored_offset,
                )
                or tree.get("filter_value") != ""
            ):
                raise ValueError
        except (TypeError, ValueError, RuntimeQualificationError):
            raise RuntimeQualificationError("sot_tree_filter_restore_mismatch", case_id)
    elif case_id == "sot-tree-roundtrip-offset-restored":
        tree = after.get("sot_tree")
        try:
            before_offset = _finite_number(action.get("before_offset_px"), case_id)
            after_offset = _finite_number(action.get("after_offset_px"), case_id)
            before_selected = action.get("before_selected_identity")
            before_focused = action.get("before_focused_identity")
            if (
                not isinstance(tree, dict)
                or action.get("kind") != "tree-dashboard-roundtrip"
                or action.get("dashboard_sequence") != ["sot", "local", "sot"]
                or not _same_within(before_offset, after_offset)
                or not isinstance(before_selected, str)
                or not before_selected
                or action.get("after_selected_identity") != before_selected
                or not isinstance(before_focused, str)
                or not before_focused
                or action.get("after_focused_identity") != before_focused
                or tree.get("selected_identity") != before_selected
                or tree.get("focused_identity") != before_focused
                or not _same_within(
                    _finite_number(tree.get("scroll_top_px"), case_id),
                    after_offset,
                )
            ):
                raise ValueError
        except (TypeError, ValueError, RuntimeQualificationError):
            raise RuntimeQualificationError(
                "sot_tree_roundtrip_restore_mismatch", case_id
            )
    elif case_id in GRAPH_READABILITY_CASE_CONFIG or case_id in {
        "graph-readability-fit-light",
        "graph-readability-fit-dark",
        "graph-readability-direct-select",
        "graph-readability-maximum-zoom",
    }:
        readability = after.get("graph_readability")
        expected_appearance, expected_zoom = {
            **GRAPH_READABILITY_CASE_CONFIG,
            **_LEGACY_GRAPH_READABILITY_CASE_CONFIG,
        }[case_id]
        if expected_zoom == "direct-select":
            _validate_contextual_camera_evidence(
                action.get("contextual_camera_evidence"), case_id
            )
        allowed_tokens = set(_CANONICAL_GRAPH_AX_KINDS)
        try:
            if not isinstance(readability, dict):
                raise ValueError
            semantic_graph = _semantic_graph_case_observation(after, case_id)
            _validated_scene_fit_report(semantic_graph, case_id)
            expected_keys = {
                "appearance",
                "zoom_state",
                "graph_scale",
                "graph_lod",
                "snapshot_identity",
                "snapshot_identity_sha256",
                "ax_before_sha256",
                "ax_after_sha256",
                "component_inline_title_count",
                "profile_identity_visible",
                "unprofiled_identity_visible",
                "required_kind_tokens",
                "semantic_component_ids",
                "expected_kind_counts",
                "viewport_pixel_proof",
                "selected_component_id",
                "direct_selection_confirmed",
                "non_color_selection_cue",
                "maximum_reached",
                "screenshot_count",
            }
            if set(readability) != expected_keys:
                raise ValueError
            snapshot_identity = readability.get("snapshot_identity")
            identity_keys = {
                "component_ids",
                "relation_ids",
                "kind_tokens",
                "snapshot_id_sha256",
                "active_profile_id",
                "sot_load_count",
                "local_scan_start_count",
                "graph_body_identity",
            }
            if not isinstance(snapshot_identity, dict) or set(snapshot_identity) != identity_keys:
                raise ValueError
            component_ids = snapshot_identity.get("component_ids")
            relation_ids = snapshot_identity.get("relation_ids")
            identity_tokens = snapshot_identity.get("kind_tokens")
            if (
                not isinstance(component_ids, list)
                or not component_ids
                or component_ids != sorted(set(component_ids))
                or any(
                    not isinstance(component_id, str)
                    or not component_id.startswith("harnesskit.")
                    for component_id in component_ids
                )
                or not isinstance(relation_ids, list)
                or not relation_ids
                or relation_ids != sorted(set(relation_ids))
                or any(
                    not isinstance(relation_id, str)
                    or not (
                        relation_id.startswith("profile:")
                        or relation_id.startswith("workflow:")
                    )
                    for relation_id in relation_ids
                )
                or not isinstance(identity_tokens, list)
                or not identity_tokens
                or identity_tokens != sorted(set(identity_tokens))
                or any(token not in allowed_tokens for token in identity_tokens)
                or identity_tokens
                != sorted(
                    {
                        component_id.split(".", 2)[1]
                        for component_id in component_ids
                    }
                )
                or not isinstance(snapshot_identity.get("snapshot_id_sha256"), str)
                or len(str(snapshot_identity.get("snapshot_id_sha256"))) != 64
                or not isinstance(graph_baseline, dict)
                or snapshot_identity.get("active_profile_id")
                != graph_baseline.get("active_profile_id")
                or snapshot_identity.get("sot_load_count")
                != graph_baseline.get("sot_load_count")
                or snapshot_identity.get("local_scan_start_count")
                != graph_baseline.get("local_scan_start_count")
                or snapshot_identity.get("graph_body_identity")
                != graph_baseline.get("graph_body_identity")
            ):
                raise ValueError
            required_tokens = list(identity_tokens)

            def valid_hash(value: object) -> bool:
                return (
                    isinstance(value, str)
                    and len(value) == 64
                    and all(character in "0123456789abcdef" for character in value)
                )

            identity_hash = readability.get("snapshot_identity_sha256")
            ax_before_hash = readability.get("ax_before_sha256")
            ax_after_hash = readability.get("ax_after_sha256")
            scale = _finite_number(readability.get("graph_scale"), case_id)
            graph_lod = readability.get("graph_lod")
            semantic_component_ids = readability.get("semantic_component_ids")
            expected_kind_counts = readability.get("expected_kind_counts")
            pixel_proof = readability.get("viewport_pixel_proof")
            if (
                action.get("kind") != "capture-graph-readability"
                or action.get("appearance") != expected_appearance
                or action.get("zoom_state") != expected_zoom
                or readability.get("appearance") != expected_appearance
                or readability.get("zoom_state") != expected_zoom
                or readability.get("component_inline_title_count") != 0
                or readability.get("required_kind_tokens") != required_tokens
                or readability.get("screenshot_count") != 1
                or scale < 0.75
                or scale > 8
                or graph_lod not in {"overview", "select", "detail", "unavailable"}
                or not valid_hash(identity_hash)
                or identity_hash != _sha256_bytes(
                    _canonical_json_bytes(snapshot_identity)
                )
                or not valid_hash(ax_before_hash)
                or ax_before_hash != ax_after_hash
                or not isinstance(semantic_component_ids, list)
                or semantic_component_ids != sorted(set(semantic_component_ids))
                or semantic_component_ids != component_ids
                or not isinstance(expected_kind_counts, dict)
                or not isinstance(pixel_proof, dict)
            ):
                raise ValueError

            derived_kind_counts = dict(
                sorted(
                    Counter(
                        component_id.split(".", 2)[1]
                        for component_id in semantic_component_ids
                    ).items()
                )
            )
            if (
                expected_kind_counts != derived_kind_counts
                or set(expected_kind_counts) != set(required_tokens)
                or any(
                    isinstance(count, bool)
                    or not isinstance(count, int)
                    or count <= 0
                    for count in expected_kind_counts.values()
                )
            ):
                raise ValueError

            proof_keys = {
                "proof_scope",
                "node_frame_authority",
                "exact_identity_count_authority",
                "pixel_claim_scope",
                "appearance",
                "zoom_state",
                "viewport_frame",
                "viewport_pixel_frame",
                "estimated_background_rgb",
                "expected_kind_counts",
                "shared_neutral_material_evidence",
                "relation_tier_evidence",
                "central_component_volume_evidence",
                "node_extent_frame",
                "largest_dimension_occupancy",
                "node_crop_detected",
                "selected_evidence",
            }
            proof_zoom = (
                "direct" if expected_zoom == "direct-select" else expected_zoom
            )
            if (
                set(pixel_proof) != proof_keys
                or pixel_proof.get("proof_scope") != "viewport_pixels"
                or pixel_proof.get("node_frame_authority") != "none"
                or pixel_proof.get("exact_identity_count_authority")
                != "typed_projection_semantic_identity_set"
                or pixel_proof.get("appearance") != expected_appearance
                or pixel_proof.get("zoom_state") != proof_zoom
                or pixel_proof.get("expected_kind_counts")
                != expected_kind_counts
                or pixel_proof.get("node_crop_detected") is not False
            ):
                raise ValueError
            viewport_frame = _frame(pixel_proof.get("viewport_frame"), case_id)
            viewport_pixel_frame = _frame(
                pixel_proof.get("viewport_pixel_frame"), case_id
            )
            node_extent_frame = _frame(
                pixel_proof.get("node_extent_frame"), case_id
            )
            if (
                node_extent_frame["x"] < 0
                or node_extent_frame["y"] < 0
                or node_extent_frame["x"] + node_extent_frame["width"]
                > viewport_frame["width"] + 1
                or node_extent_frame["y"] + node_extent_frame["height"]
                > viewport_frame["height"] + 1
                or viewport_pixel_frame["width"] < viewport_frame["width"]
                or viewport_pixel_frame["height"] < viewport_frame["height"]
            ):
                raise ValueError

            def valid_rgb(value: object) -> bool:
                return (
                    isinstance(value, list)
                    and len(value) == 3
                    and all(
                        isinstance(channel, int)
                        and not isinstance(channel, bool)
                        and 0 <= channel <= 255
                        for channel in value
                    )
                )

            if not valid_rgb(pixel_proof.get("estimated_background_rgb")):
                raise ValueError
            shared_neutral_material_evidence = pixel_proof.get(
                "shared_neutral_material_evidence"
            )
            material_evidence_keys = {
                "body_pixel_count",
                "outline_pixel_count",
                "body_rgb",
                "outline_rgb",
            }
            if (
                not isinstance(shared_neutral_material_evidence, dict)
                or set(shared_neutral_material_evidence)
                != material_evidence_keys
                or isinstance(
                    shared_neutral_material_evidence.get("body_pixel_count"), bool
                )
                or not isinstance(
                    shared_neutral_material_evidence.get("body_pixel_count"), int
                )
                or int(shared_neutral_material_evidence["body_pixel_count"]) <= 0
                or isinstance(
                    shared_neutral_material_evidence.get("outline_pixel_count"),
                    bool,
                )
                or not isinstance(
                    shared_neutral_material_evidence.get("outline_pixel_count"),
                    int,
                )
                or int(shared_neutral_material_evidence["outline_pixel_count"])
                <= 0
                or not valid_rgb(shared_neutral_material_evidence.get("body_rgb"))
                or not valid_rgb(
                    shared_neutral_material_evidence.get("outline_rgb")
                )
            ):
                raise ValueError

            relation_tier_evidence = pixel_proof.get("relation_tier_evidence")
            central_volume_evidence = pixel_proof.get(
                "central_component_volume_evidence"
            )
            expected_pixel_claim_scope = (
                "shared_neutral_material_presence_crop_utilization_relation_tier_central_volume"
                if expected_zoom == "fit"
                else "shared_neutral_material_selected_mark_selection_halo"
            )
            if pixel_proof.get("pixel_claim_scope") != expected_pixel_claim_scope:
                raise ValueError
            if expected_zoom == "fit":
                relation_tier_keys = {
                    "tier",
                    "region_count",
                    *material_evidence_keys,
                }
                if (
                    not isinstance(relation_tier_evidence, dict)
                    or set(relation_tier_evidence) != relation_tier_keys
                    or relation_tier_evidence.get("tier")
                    != "featured_relation"
                    or relation_tier_evidence.get("region_count") != 2
                    or isinstance(
                        relation_tier_evidence.get("body_pixel_count"), bool
                    )
                    or not isinstance(
                        relation_tier_evidence.get("body_pixel_count"), int
                    )
                    or int(relation_tier_evidence["body_pixel_count"]) <= 0
                    or isinstance(
                        relation_tier_evidence.get("outline_pixel_count"), bool
                    )
                    or not isinstance(
                        relation_tier_evidence.get("outline_pixel_count"), int
                    )
                    or int(relation_tier_evidence["outline_pixel_count"]) <= 0
                    or not valid_rgb(relation_tier_evidence.get("body_rgb"))
                    or not valid_rgb(relation_tier_evidence.get("outline_rgb"))
                ):
                    raise ValueError
                if (
                    not isinstance(central_volume_evidence, dict)
                    or set(central_volume_evidence)
                    != {
                        "present",
                        "body_pixel_count",
                        "frame",
                        "central_band_frame",
                    }
                    or central_volume_evidence.get("present") is not True
                    or isinstance(
                        central_volume_evidence.get("body_pixel_count"), bool
                    )
                    or not isinstance(
                        central_volume_evidence.get("body_pixel_count"), int
                    )
                    or int(central_volume_evidence["body_pixel_count"]) <= 0
                ):
                    raise ValueError
                central_frame = _frame(
                    central_volume_evidence.get("frame"), case_id
                )
                central_band_frame = _frame(
                    central_volume_evidence.get("central_band_frame"), case_id
                )
                if (
                    not _frame_contains(
                        central_band_frame, central_frame, tolerance=1
                    )
                    or central_band_frame["x"] < 0
                    or central_band_frame["y"] < 0
                    or central_band_frame["x"]
                    + central_band_frame["width"]
                    > viewport_frame["width"] + 1
                    or central_band_frame["y"]
                    + central_band_frame["height"]
                    > viewport_frame["height"] + 1
                ):
                    raise ValueError
            elif relation_tier_evidence is not None or central_volume_evidence is not None:
                raise ValueError

            largest_dimension_occupancy = _finite_number(
                pixel_proof.get("largest_dimension_occupancy"), case_id
            )
            if not 0 < largest_dimension_occupancy <= 1:
                raise ValueError

            if expected_zoom == "fit":
                if (
                    not _same_within(scale, 1.0, tolerance=0.001)
                    or largest_dimension_occupancy < 0.75
                    or pixel_proof.get("selected_evidence") is not None
                    or readability.get("profile_identity_visible") is not True
                    or readability.get("unprofiled_identity_visible") is not True
                    or readability.get("selected_component_id") is not None
                    or readability.get("direct_selection_confirmed") is not False
                    or readability.get("non_color_selection_cue") is not False
                    or readability.get("maximum_reached") is not False
                ):
                    raise ValueError
            else:
                selected_component_id = readability.get("selected_component_id")
                selected_evidence = pixel_proof.get("selected_evidence")
                selected_kind = (
                    selected_component_id.split(".", 2)[1]
                    if isinstance(selected_component_id, str)
                    and selected_component_id.count(".") >= 2
                    else None
                )
                if (
                    not isinstance(selected_component_id, str)
                    or selected_component_id not in semantic_component_ids
                    or selected_kind not in required_tokens
                    or not isinstance(selected_evidence, dict)
                    or readability.get("direct_selection_confirmed") is not True
                    or readability.get("non_color_selection_cue") is not True
                    or graph_lod not in {"select", "detail"}
                    or readability.get("maximum_reached")
                    is not (expected_zoom == "maximum")
                    or (
                        expected_zoom == "maximum"
                        and (
                            not _same_within(scale, 8.0, tolerance=0.001)
                            or action.get("maximum_probe_stable") is not True
                        )
                    )
                ):
                    raise ValueError
                selected_keys = {
                    "component_id",
                    "kind_token",
                    "body_frame",
                    "mark_frame",
                    "mark_detected",
                    "mark_center_delta_css_px",
                    "mark_center_tolerance_css_px",
                    "selection_halo_detected",
                    "selection_halo_pixel_count",
                    "selection_halo_sides",
                    "selection_underlay_detected",
                    "selection_underlay_pixel_count",
                    "selection_underlay_sides",
                    "appearance",
                }
                body_frame = _frame(selected_evidence.get("body_frame"), case_id)
                mark_frame = _frame(selected_evidence.get("mark_frame"), case_id)
                center_delta = selected_evidence.get(
                    "mark_center_delta_css_px"
                )
                center_tolerance = _finite_number(
                    selected_evidence.get("mark_center_tolerance_css_px"),
                    case_id,
                )
                halo_sides = selected_evidence.get("selection_halo_sides")
                underlay_sides = selected_evidence.get(
                    "selection_underlay_sides"
                )
                required_sides = {
                    "bottom": True,
                    "left": True,
                    "right": True,
                    "top": True,
                }
                if (
                    set(selected_evidence) != selected_keys
                    or selected_evidence.get("component_id")
                    != selected_component_id
                    or selected_evidence.get("kind_token") != selected_kind
                    or selected_evidence.get("appearance") != expected_appearance
                    or selected_evidence.get("mark_detected") is not True
                    or not isinstance(center_delta, dict)
                    or set(center_delta) != {"x", "y"}
                    or center_tolerance <= 0
                    or _finite_number(center_delta.get("x"), case_id)
                    > center_tolerance
                    or _finite_number(center_delta.get("y"), case_id)
                    > center_tolerance
                    or mark_frame["x"] < body_frame["x"] - 1
                    or mark_frame["y"] < body_frame["y"] - 1
                    or mark_frame["x"] + mark_frame["width"]
                    > body_frame["x"] + body_frame["width"] + 1
                    or mark_frame["y"] + mark_frame["height"]
                    > body_frame["y"] + body_frame["height"] + 1
                    or selected_evidence.get("selection_halo_detected") is not True
                    or isinstance(
                        selected_evidence.get("selection_halo_pixel_count"),
                        bool,
                    )
                    or not isinstance(
                        selected_evidence.get("selection_halo_pixel_count"),
                        int,
                    )
                    or int(selected_evidence["selection_halo_pixel_count"]) <= 0
                    or halo_sides != required_sides
                    or selected_evidence.get("selection_underlay_detected")
                    is not True
                    or isinstance(
                        selected_evidence.get("selection_underlay_pixel_count"),
                        bool,
                    )
                    or not isinstance(
                        selected_evidence.get("selection_underlay_pixel_count"),
                        int,
                    )
                    or int(selected_evidence["selection_underlay_pixel_count"])
                    <= 0
                    or underlay_sides != required_sides
                ):
                    raise ValueError
        except (TypeError, ValueError, RuntimeQualificationError):
            raise RuntimeQualificationError(
                "workspace_graph_readability_contract_mismatch", case_id
            )
    elif case_id == "matrix-initial-collapsed-map-first-viewport":
        matrix = after.get("matrix_layout")
        try:
            if not isinstance(matrix, dict) or action.get("kind") != "observe":
                raise ValueError
            disclosures = matrix.get("disclosures")
            if not isinstance(disclosures, dict):
                raise ValueError
            component_map = disclosures.get("component_map")
            profile_matrix = disclosures.get("profile_matrix")
            if not isinstance(component_map, dict) or not isinstance(
                profile_matrix, dict
            ):
                raise ValueError
            first_viewport = _frame(
                matrix.get("first_viewport_frame"), case_id
            )
            center_client = _frame(matrix.get("center_client_frame"), case_id)
            outer_scroll = matrix.get("outer_scroll")
            if not isinstance(outer_scroll, dict):
                raise ValueError
            if (
                component_map.get("expanded") is not True
                or component_map.get("body_present") is not True
                or profile_matrix.get("expanded") is not False
                or profile_matrix.get("body_present") is not False
                or profile_matrix.get("focus_target_count") != 0
                or matrix.get("matrix_body_frame") is not None
                or any(
                    not _same_within(first_viewport[key], center_client[key])
                    for key in ("x", "y", "width", "height")
                )
                or outer_scroll.get("owner") != "workbench"
                or not _same_within(
                    _finite_number(outer_scroll.get("scroll_top_px"), case_id),
                    0,
                )
                or _finite_number(
                    outer_scroll.get("scroll_height_px"), case_id
                )
                > _finite_number(
                    outer_scroll.get("client_height_px"), case_id
                )
                + 1
                or outer_scroll.get("positive_nested_scroll_range_count") != 0
            ):
                raise ValueError
        except (TypeError, ValueError, RuntimeQualificationError):
            raise RuntimeQualificationError(
                "workspace_matrix_initial_contract_mismatch", case_id
            )
    elif case_id == "matrix-expanded-outer-scroll":
        matrix = after.get("matrix_layout")
        before_matrix = before.get("matrix_layout") if before else None
        try:
            if (
                not isinstance(matrix, dict)
                or not isinstance(before_matrix, dict)
                or action.get("kind") != "toggle-matrix"
                or action.get("expanded") is not True
                or action.get("transport") != "ax-press-and-cg-event-wheel"
            ):
                raise ValueError
            disclosures = matrix.get("disclosures")
            if not isinstance(disclosures, dict):
                raise ValueError
            profile_matrix = disclosures.get("profile_matrix")
            if not isinstance(profile_matrix, dict):
                raise ValueError
            first_viewport = _frame(matrix.get("first_viewport_frame"), case_id)
            before_first_viewport = _frame(
                before_matrix.get("first_viewport_frame"), case_id
            )
            graph_viewport = _frame(matrix.get("graph_viewport_frame"), case_id)
            before_graph_viewport = _frame(
                before_matrix.get("graph_viewport_frame"), case_id
            )
            matrix_body = _frame(matrix.get("matrix_body_frame"), case_id)
            outer_scroll = matrix.get("outer_scroll")
            if not isinstance(outer_scroll, dict):
                raise ValueError
            scroll_top = _finite_number(
                outer_scroll.get("scroll_top_px"), case_id
            )
            scroll_height = _finite_number(
                outer_scroll.get("scroll_height_px"), case_id
            )
            client_height = _finite_number(
                outer_scroll.get("client_height_px"), case_id
            )
            content_height = (
                matrix_body["y"]
                + matrix_body["height"]
                - first_viewport["y"]
            )
            if (
                profile_matrix.get("expanded") is not True
                or profile_matrix.get("body_present") is not True
                or int(profile_matrix.get("focus_target_count", 0)) <= 0
                or not _same_within(
                    first_viewport["height"], before_first_viewport["height"]
                )
                or any(
                    not _same_within(graph_viewport[key], before_graph_viewport[key])
                    for key in ("width", "height")
                )
                or not _graph_fingerprint_equal(
                    before.get("graph_fingerprint") if before else None,
                    after.get("graph_fingerprint"),
                )
                or not _same_within(
                    matrix_body["y"],
                    first_viewport["y"] + first_viewport["height"],
                )
                or matrix_body["height"] <= 0
                or outer_scroll.get("owner") != "workbench"
                or scroll_top <= 0
                or scroll_height <= client_height
                or scroll_top > scroll_height - client_height + 1
                or not _same_within(scroll_height, content_height)
                or outer_scroll.get("positive_nested_scroll_range_count") != 0
            ):
                raise ValueError
        except (TypeError, ValueError, RuntimeQualificationError):
            raise RuntimeQualificationError(
                "workspace_matrix_outer_scroll_contract_mismatch", case_id
            )
    elif case_id == "matrix-collapse-selection-state-preserved":
        matrix = after.get("matrix_layout")
        before_matrix = before.get("matrix_layout") if before else None
        during = payload.get("during")
        during_matrix = (
            during.get("matrix_layout") if isinstance(during, dict) else None
        )
        try:
            if (
                not isinstance(matrix, dict)
                or not isinstance(before_matrix, dict)
                or not isinstance(during_matrix, dict)
                or action.get("kind") != "matrix-collapse-expand-roundtrip"
                or action.get("transport") != "ax-press"
            ):
                raise ValueError
            during_disclosures = during_matrix.get("disclosures")
            after_disclosures = matrix.get("disclosures")
            if not isinstance(during_disclosures, dict) or not isinstance(
                after_disclosures, dict
            ):
                raise ValueError
            during_profile = during_disclosures.get("profile_matrix")
            after_profile = after_disclosures.get("profile_matrix")
            if not isinstance(during_profile, dict) or not isinstance(
                after_profile, dict
            ):
                raise ValueError
            before_scroll = before_matrix.get("outer_scroll")
            after_scroll = matrix.get("outer_scroll")
            if not isinstance(before_scroll, dict) or not isinstance(
                after_scroll, dict
            ):
                raise ValueError
            before_top = _finite_number(
                before_scroll.get("scroll_top_px"), case_id
            )
            after_top = _finite_number(after_scroll.get("scroll_top_px"), case_id)
            after_max = max(
                0,
                _finite_number(after_scroll.get("scroll_height_px"), case_id)
                - _finite_number(after_scroll.get("client_height_px"), case_id),
            )
            expected_top = min(before_top, after_max)
            state_fields = (
                "selected_component_id",
                "active_profile_id",
                "owning_profile_ids",
                "right_detail_id",
                "camera",
            )
            if (
                during_profile.get("expanded") is not False
                or during_profile.get("body_present") is not False
                or during_profile.get("focus_target_count") != 0
                or during_matrix.get("matrix_body_frame") is not None
                or after_profile.get("expanded") is not True
                or after_profile.get("body_present") is not True
                or not before_matrix.get("owning_profile_ids")
                or any(
                    matrix.get(field) != before_matrix.get(field)
                    for field in state_fields
                )
                or not _graph_fingerprint_equal(
                    before.get("graph_fingerprint") if before else None,
                    after.get("graph_fingerprint"),
                )
                or not _same_within(after_top, expected_top)
                or (
                    expected_top > 0
                    and _same_within(after_top, 0)
                )
                or payload.get("command_counters")
                != {"sot_load_count": 1, "local_scan_start_count": 0}
            ):
                raise ValueError
        except (TypeError, ValueError, RuntimeQualificationError):
            raise RuntimeQualificationError(
                "workspace_matrix_state_preservation_mismatch", case_id
            )
    elif case_id == "matrix-bounded-fluid-wrap":
        samples = after.get("matrix_wrap_samples")
        try:
            if (
                action.get("kind") != "sample-matrix-wrap"
                or not isinstance(samples, list)
                or len(samples) < 2
                or action.get("center_inline_sizes_px")
                != [sample.get("center_inline_px") for sample in samples if isinstance(sample, dict)]
            ):
                raise ValueError

            observed_widths: set[float] = set()
            for sample in samples:
                if not isinstance(sample, dict):
                    raise ValueError
                center_inline = _finite_number(
                    sample.get("center_inline_px"), case_id
                )
                root_font = _finite_number(sample.get("root_font_px"), case_id)
                if center_inline <= 0 or root_font <= 0:
                    raise ValueError
                observed_widths.add(center_inline)

                for count_key in ("profile_column_count", "member_column_count"):
                    count = sample.get(count_key)
                    if (
                        not isinstance(count, int)
                        or isinstance(count, bool)
                        or count <= 0
                    ):
                        raise ValueError

                for key, minimum_rem, maximum_rem in (
                    ("profile_cards", 12.5, 15.0),
                    ("member_cards", 13.5, 18.0),
                ):
                    cards = sample.get(key)
                    if not isinstance(cards, list) or not cards:
                        raise ValueError
                    frames: list[dict[str, float]] = []
                    identities: set[str] = set()
                    for card in cards:
                        if not isinstance(card, dict):
                            raise ValueError
                        identity = card.get("identity")
                        frame = _frame(card.get("frame"), case_id)
                        if (
                            not isinstance(identity, str)
                            or not identity
                            or identity in identities
                            or frame["width"]
                            < min(center_inline, root_font * minimum_rem) - 1
                            or frame["width"] > root_font * maximum_rem + 1
                            or frame["height"] <= 0
                            or (
                                key == "profile_cards"
                                and (
                                    frame["height"] < root_font * 4.5
                                    or frame["height"] > root_font * 4.75
                                )
                            )
                            or frame["x"] < -1
                            or frame["x"] + frame["width"] > center_inline + 1
                            or abs(
                                _finite_number(card.get("title_font_px"), case_id)
                                    - root_font * 0.9375
                            )
                            > 0.1
                            or abs(
                                _finite_number(
                                    card.get("metadata_font_px"), case_id
                                )
                                    - root_font * 0.75
                            )
                            > 0.1
                            or _finite_number(
                                card.get("content_top_inset_px"), case_id
                            )
                            > root_font * 0.9 + 0.1
                        ):
                            raise ValueError
                        identities.add(identity)
                        frames.append(frame)
                    for previous, current in zip(frames, frames[1:]):
                        if current["y"] < previous["y"] - 1 or (
                            _same_within(current["y"], previous["y"])
                            and current["x"] <= previous["x"]
                        ):
                            raise ValueError
                    if any(
                        _rectangles_overlap(frames[left], frames[right])
                        for left in range(len(frames))
                        for right in range(left + 1, len(frames))
                    ):
                        raise ValueError
            if len(observed_widths) != len(samples):
                raise ValueError
            if max(
                int(sample.get("profile_column_count", 0))
                for sample in samples
            ) < 5:
                raise ValueError
            narrow_sample = min(
                samples,
                key=lambda sample: float(sample["center_inline_px"]),
            )
            wide_sample = max(
                samples,
                key=lambda sample: float(sample["center_inline_px"]),
            )
            for key in ("profile_column_count", "member_column_count"):
                if narrow_sample.get(key) == wide_sample.get(key):
                    raise ValueError
        except (TypeError, ValueError, RuntimeQualificationError):
            raise RuntimeQualificationError(
                "workspace_matrix_bounded_fluid_contract_mismatch", case_id
            )
    elif case_id == "workflow-relation-focus":
        graph = _semantic_graph_case_observation(after, case_id)
        identity = graph.get("identity_overlay")
        inspector = graph.get("workflow_inspector")
        selected_relations = _semantic_selected_records(
            graph, "relations", case_id
        )
        workflow_id = (
            selected_relations[0].get("canonical_id")
            if len(selected_relations) == 1
            and selected_relations[0].get("relation_kind") == "workflow"
            else None
        )
        identity_title = (
            identity.get("title") if isinstance(identity, dict) else None
        )
        identity_kind = (
            identity.get("kind") if isinstance(identity, dict) else None
        )
        identity_count = (
            identity.get("count") if isinstance(identity, dict) else None
        )
        inspector_steps = (
            inspector.get("steps") if isinstance(inspector, dict) else None
        )
        if (
            action.get("kind") != "semantic-workflow-relation-press"
            or action.get("transport") != "ax-press"
            or action.get("screenshot_count") != 1
            or not isinstance(workflow_id, str)
            or not workflow_id
            or not isinstance(identity_title, dict)
            or not isinstance(identity_kind, dict)
            or not isinstance(identity_count, dict)
            or "harnesskit." in str(identity_title.get("name")).lower()
            or str(identity_kind.get("name")).lower() != "workflow"
            or str(identity_count.get("name"))
            != f"{len(inspector_steps) if isinstance(inspector_steps, list) else 0} steps"
            or not isinstance(inspector, dict)
            or inspector.get("visible") is not True
            or inspector.get("workflow_id") != workflow_id
            or inspector.get("runtime_state")
            != "authored definition · runtime 미구현"
            or not isinstance(inspector_steps, list)
            or not inspector_steps
            or not inspector.get("ordinal_occurrences")
            or not inspector.get("component_roles")
        ):
            raise RuntimeQualificationError(
                "workflow_relation_focus_contract_mismatch", case_id
            )
        try:
            _validate_contextual_camera_evidence(
                action.get("contextual_camera_evidence"), case_id
            )
        except RuntimeQualificationError as error:
            raise RuntimeQualificationError(
                "workflow_relation_focus_contract_mismatch", case_id
            ) from error
    elif case_id == "workflow-step-hover-no-camera":
        if before is None:
            raise RuntimeQualificationError("workflow_hover_baseline_missing", case_id)
        before_graph = _semantic_graph_case_observation(before, case_id)
        after_graph = _semantic_graph_case_observation(after, case_id)
        before_steps = _semantic_selected_records(
            before_graph, "workflow_steps", case_id
        )
        after_steps = _semantic_selected_records(
            after_graph, "workflow_steps", case_id
        )
        if (
            action.get("kind") != "workflow-step-hover"
            or action.get("transport") != "cg-event-mouse-move"
            or before.get("graph_fingerprint") != after.get("graph_fingerprint")
            or _semantic_matrix_fingerprint(before)
            != _semantic_matrix_fingerprint(after)
            or before_steps != after_steps
        ):
            raise RuntimeQualificationError(
                "workflow_step_hover_contract_mismatch", case_id
            )
    elif case_id == "workflow-step-selection-invariant":
        if before is None:
            raise RuntimeQualificationError(
                "workflow_selection_baseline_missing", case_id
            )
        before_graph = _semantic_graph_case_observation(before, case_id)
        after_graph = _semantic_graph_case_observation(after, case_id)
        before_steps = _semantic_selected_records(
            before_graph, "workflow_steps", case_id
        )
        after_steps = _semantic_selected_records(
            after_graph, "workflow_steps", case_id
        )
        inspector = after_graph.get("workflow_inspector")
        inspector_steps = (
            inspector.get("steps") if isinstance(inspector, dict) else None
        )
        if (
            action.get("kind") != "workflow-step-select"
            or action.get("transport") != "cg-event-mouse-click"
            or action.get("screenshot_count") != 1
            or before_steps == after_steps
            or len(after_steps) != 1
            or not isinstance(inspector_steps, list)
            or len(
                [
                    step
                    for step in inspector_steps
                    if isinstance(step, dict) and step.get("selected") is True
                ]
            )
            != 1
            or not _ordinary_selection_state_preserved(
                action.get("ordinary_selection_state_evidence"),
                before.get("graph_fingerprint"),
                after.get("graph_fingerprint"),
            )
        ):
            raise RuntimeQualificationError(
                "workflow_step_selection_contract_mismatch", case_id
            )
    elif case_id == "workflow-camera-restore":
        if before is None:
            raise RuntimeQualificationError("workflow_restore_baseline_missing", case_id)
        after_graph = _semantic_graph_case_observation(after, case_id)
        if (
            action.get("kind") != "workflow-camera-restore"
            or action.get("transport") != "cg-event-key-escape"
            or not isinstance(action.get("pre_workflow_graph_fingerprint"), dict)
            or action.get("pre_workflow_graph_fingerprint")
            != after.get("graph_fingerprint")
            or _semantic_selected_records(after_graph, "relations", case_id)
            or _semantic_selected_records(after_graph, "workflow_steps", case_id)
        ):
            raise RuntimeQualificationError(
                "workflow_camera_restore_contract_mismatch", case_id
            )
    elif case_id == "workflow-matrix-state-invariant":
        if before is None:
            raise RuntimeQualificationError("workflow_matrix_baseline_missing", case_id)
        if (
            action.get("kind") != "semantic-workflow-relation-press"
            or action.get("transport") != "ax-press"
            or _semantic_matrix_fingerprint(before)
            != _semantic_matrix_fingerprint(after)
        ):
            raise RuntimeQualificationError(
                "workflow_matrix_state_changed", case_id
            )
    elif case_id in {
        "semantic-fallback-user-transition",
        "renderer-retry-snapshot-preserved",
    }:
        if before is None:
            raise RuntimeQualificationError(
                "workspace_renderer_transition_baseline_missing", case_id
            )
        before_graph = _semantic_graph_case_observation(before, case_id)
        after_graph = _semantic_graph_case_observation(after, case_id)
        before_fit_report = _validated_scene_fit_report(before_graph, case_id)
        after_fit_report = _validated_scene_fit_report(after_graph, case_id)
        before_renderer = before_graph.get("renderer")
        after_renderer = after_graph.get("renderer")
        before_camera = before_graph.get("camera")
        after_camera = after_graph.get("camera")
        before_matrix = _semantic_matrix_fingerprint(before)
        after_matrix = _semantic_matrix_fingerprint(after)
        before_fingerprint = before.get("graph_fingerprint")
        after_fingerprint = after.get("graph_fingerprint")
        common_preserved = (
            _semantic_projection_identity(before, case_id)
            == _semantic_projection_identity(after, case_id)
            and _semantic_selection_fingerprint(before, case_id)
            == _semantic_selection_fingerprint(after, case_id)
            and bool(before_matrix)
            and before_matrix == after_matrix
            and _semantic_inspector_fingerprint(before, case_id)
            == _semantic_inspector_fingerprint(after, case_id)
            and before_fit_report == after_fit_report
            and isinstance(before_fingerprint, dict)
            and isinstance(after_fingerprint, dict)
            and {
                "sot_load_count": before_fingerprint.get("sot_load_count"),
                "local_scan_start_count": before_fingerprint.get(
                    "local_scan_start_count"
                ),
            }
            == {
                "sot_load_count": after_fingerprint.get("sot_load_count"),
                "local_scan_start_count": after_fingerprint.get(
                    "local_scan_start_count"
                ),
            }
            == {"sot_load_count": 1, "local_scan_start_count": 0}
        )
        if not common_preserved:
            raise RuntimeQualificationError(
                "workspace_renderer_transition_state_changed", case_id
            )
        if case_id == "semantic-fallback-user-transition":
            semantic_frame = (
                after_renderer.get("semantic_view_frame")
                if isinstance(after_renderer, dict)
                else None
            )
            try:
                semantic_frame = _frame(semantic_frame, case_id)
            except RuntimeQualificationError:
                semantic_frame = None
            if (
                action.get("kind") != "semantic-fallback-user-transition"
                or action.get("transport") != "ax-press"
                or action.get("target") != "label:Component Map 텍스트 보기"
                or action.get("screenshot_count") != 1
                or not isinstance(action.get("pre_setup_graph_fingerprint"), dict)
                or not isinstance(action.get("setup_actions"), list)
                or len(action["setup_actions"]) != 2
                or not isinstance(before_renderer, dict)
                or before_renderer.get("availability") != "ready"
                or not isinstance(before_camera, dict)
                or before_camera.get("ready") is not True
                or not isinstance(after_renderer, dict)
                or after_renderer.get("availability") != "manual_fallback"
                or after_renderer.get("semantic_primary_visible") is not True
                or after_renderer.get("retry_visible") is not True
                or not isinstance(after_camera, dict)
                or after_camera.get("ready") is not False
                or semantic_frame is None
                or semantic_frame["width"] <= 1
                or semantic_frame["height"] <= 1
            ):
                raise RuntimeQualificationError(
                    "workspace_semantic_fallback_transition_mismatch", case_id
                )
            setup_actions = action["setup_actions"]
            relation_setup = setup_actions[0]
            step_setup = setup_actions[1]
            before_relation_id = before_fingerprint.get(
                "selected_relation_node_id"
            )
            before_locked_step = before_fingerprint.get("locked_step")
            pre_setup_fingerprint = action["pre_setup_graph_fingerprint"]
            if (
                not isinstance(relation_setup, dict)
                or relation_setup.get("kind")
                != "semantic-workflow-relation-press"
                or relation_setup.get("transport") != "ax-press"
                or relation_setup.get("target")
                != f"semantic-relation:{before_relation_id}"
                or not isinstance(step_setup, dict)
                or step_setup.get("kind") != "workflow-step-select"
                or step_setup.get("transport") != "cg-event-mouse-click"
                or not isinstance(step_setup.get("target"), str)
                or not str(step_setup["target"]).startswith(
                    "workflow-inspector-step:"
                )
                or not isinstance(step_setup.get("click"), dict)
                or step_setup["click"].get("accepted") is not True
                or not isinstance(before_relation_id, str)
                or not before_relation_id.startswith("workflow:")
                or not isinstance(before_locked_step, dict)
                or before_locked_step.get("workflow_id")
                != before_relation_id.removeprefix("workflow:")
                or not isinstance(before_locked_step.get("ordinal"), int)
                or not _camera_pose_equal(
                    pre_setup_fingerprint.get("camera_pose"),
                    before_fingerprint.get("camera_pose"),
                )
                or not _same_within(
                    float(pre_setup_fingerprint.get("scale")),
                    float(before_fingerprint.get("scale")),
                    tolerance=0.001,
                )
            ):
                raise RuntimeQualificationError(
                    "workspace_semantic_fallback_setup_mismatch", case_id
                )
            if not _graph_fingerprint_equal(
                action.get("pre_fallback_ready_graph_fingerprint"),
                before_fingerprint,
            ):
                raise RuntimeQualificationError(
                    "workspace_semantic_fallback_fingerprint_mismatch", case_id
                )
        else:
            if (
                action.get("kind") != "renderer-retry-snapshot-preserved"
                or action.get("transport") != "ax-press"
                or action.get("target") != "renderer-retry"
                or not isinstance(before_renderer, dict)
                or before_renderer.get("availability") != "manual_fallback"
                or before_renderer.get("retry_visible") is not True
                or not isinstance(before_camera, dict)
                or before_camera.get("ready") is not False
                or not isinstance(after_renderer, dict)
                or after_renderer.get("availability") != "ready"
                or after_renderer.get("semantic_primary_visible") is not False
                or after_renderer.get("retry_visible") is not False
                or not isinstance(after_camera, dict)
                or after_camera.get("ready") is not True
            ):
                raise RuntimeQualificationError(
                    "workspace_renderer_retry_transition_mismatch", case_id
                )
            if not _graph_fingerprint_equal(
                action.get("pre_fallback_ready_graph_fingerprint"),
                after_fingerprint,
            ):
                raise RuntimeQualificationError(
                    "workspace_renderer_retry_fingerprint_changed", case_id
                )
    elif case_id == "semantic-surface-workflow-parity":
        graph = _semantic_graph_case_observation(after, case_id)
        inspector = graph.get("workflow_inspector")
        semantic_steps = graph.get("workflow_steps")
        inspector_steps = inspector.get("steps") if isinstance(inspector, dict) else None
        workflow_id = inspector.get("workflow_id") if isinstance(inspector, dict) else None
        workflow_relations = [
            relation
            for relation in graph.get("relations", [])
            if isinstance(relation, dict)
            and relation.get("relation_kind") == "workflow"
            and relation.get("canonical_id") == workflow_id
        ]
        occurrences = (
            inspector.get("ordinal_occurrences")
            if isinstance(inspector, dict)
            else None
        )
        if (
            action.get("kind") != "compare-production-semantic-surfaces"
            or action.get("transport") != "ax-observation"
            or not isinstance(inspector, dict)
            or inspector.get("visible") is not True
            or inspector.get("runtime_state")
            != "authored definition · runtime 미구현"
            or not isinstance(semantic_steps, list)
            or not isinstance(inspector_steps, list)
            or not isinstance(occurrences, list)
            or [
                {
                    "workflow_id": step.get("workflow_id"),
                    "ordinal": step.get("ordinal"),
                }
                for step in semantic_steps
                if isinstance(step, dict)
                and step.get("workflow_id") == workflow_id
            ] != [
                {
                    "workflow_id": step.get("workflow_id"),
                    "ordinal": step.get("ordinal"),
                }
                for step in inspector_steps
                if isinstance(step, dict)
            ]
            or len(workflow_relations) != 1
            or workflow_relations[0].get("exact_count")
            != len(inspector_steps)
            or set(workflow_relations[0].get("member_component_ids", []))
            != {
                occurrence.get("component_id")
                for occurrence in occurrences
                if isinstance(occurrence, dict)
            }
            or inspector.get("component_roles") != action.get("component_roles")
            or inspector.get("unresolved_warning_set")
            != action.get("unresolved_warning_set")
        ):
            raise RuntimeQualificationError(
                "semantic_workflow_parity_mismatch", case_id
            )
    elif case_id == "restart-first-visible-persisted":
        first_visible = payload.get("first_visible")
        if not isinstance(first_visible, dict):
            raise RuntimeQualificationError("first_visible_observation_missing", case_id)
        if (
            first_visible.get("prearmed_before_launch") is not True
            or first_visible.get("collector_attached_before_visible") is not True
            or first_visible.get("sequence_gap") is not False
            or first_visible.get("on_screen") is not True
            or not isinstance(first_visible.get("first_layer0_sample_sequence"), int)
            or isinstance(first_visible.get("first_layer0_sample_sequence"), bool)
            or int(first_visible["first_layer0_sample_sequence"]) < 1
            or not isinstance(first_visible.get("first_complete_frame_sequence"), int)
            or isinstance(first_visible.get("first_complete_frame_sequence"), bool)
            or int(first_visible["first_complete_frame_sequence"]) < 1
            or not isinstance(first_visible.get("alpha"), (int, float))
            or isinstance(first_visible.get("alpha"), bool)
            or not math.isfinite(float(first_visible["alpha"]))
            or float(first_visible["alpha"]) <= 0
        ):
            raise RuntimeQualificationError("workspace_first_visible_sequence_invalid", case_id)
        geometry_at = first_visible.get("first_visible_geometry_monotonic_seconds")
        frame_at = first_visible.get("first_frame_monotonic_seconds")
        if strict:
            try:
                geometry_time = float(geometry_at)
                frame_time = float(frame_at)
                if (
                    not math.isfinite(geometry_time)
                    or not math.isfinite(frame_time)
                    or geometry_time > frame_time
                ):
                    raise RuntimeQualificationError(
                        "workspace_first_visible_sequence_invalid", case_id
                    )
            except (TypeError, ValueError):
                raise RuntimeQualificationError(
                    "workspace_first_visible_sequence_invalid", case_id
                )
        delta = first_visible.get("pair_delta_px")
        if not isinstance(delta, dict) or any(abs(float(delta.get(side, 999))) > 1 for side in ("left", "right")):
            raise RuntimeQualificationError("workspace_first_visible_pair_mismatch", case_id)
    elif case_id == "corrupt-preference-default-pair":
        subcases = after.get("corruption_subcases")
        expected_kinds = ["malformed-json", "unknown-version", "out-of-range-pair"]
        if not isinstance(subcases, list) or [entry.get("kind") for entry in subcases if isinstance(entry, dict)] != expected_kinds:
            raise RuntimeQualificationError("workspace_corrupt_preference_subcases_missing", case_id)
        if any(
            not isinstance(entry, dict)
            or entry.get("preferred_pair") != {"left_px": 304, "right_px": 368}
            for entry in subcases
        ):
            raise RuntimeQualificationError("workspace_corrupt_preference_default_mismatch", case_id)
    elif case_id == "preference-write-denied-session-usable":
        diagnostic = after.get("diagnostic")
        if (
            after.get("persisted") is not False
            or not isinstance(diagnostic, dict)
            or diagnostic.get("code") != "workspace_layout_preference_write_failed"
        ):
            raise RuntimeQualificationError("workspace_write_denied_diagnostic_missing", case_id)
        if strict and before is not None and _pair(after, "effective_pair") == _pair(before, "effective_pair"):
            raise RuntimeQualificationError("write_denied_session_geometry_lost", case_id)
    elif case_id == "final-cleanup-zero":
        if any(after.get(key) != 0 for key in (
            "main_process_count", "main_window_count", "helper_process_count"
        )):
            raise RuntimeQualificationError("workspace_final_cleanup_nonzero", case_id)
    else:
        raise RuntimeQualificationError("workspace_case_invalid", case_id)


def _record_passed_case(
    *,
    case_id: str,
    started_at: str,
    payload: dict[str, object],
    artifact: object,
    automation: WorkspaceLayoutAutomationPort,
    evidence_dir: Path,
    cases_dir: Path,
    graph_baseline: dict[str, object] | None,
) -> dict[str, object]:
    _validate_payload_identity(payload, artifact, case_id)
    _validate_case_invariants(case_id, payload, graph_baseline)
    observation_path = cases_dir / f"{case_id}.json"
    _write_json(observation_path, payload)
    screenshot: dict[str, object] | None = None
    capture_ax_bracket: dict[str, object] | None = None
    if case_id != "final-cleanup-zero":
        pid, window_id = _case_identity(payload)
        screenshot_path = cases_dir / f"{case_id}.png"
        capture_ax_before: dict[str, object] | None = None
        if case_id == "local-markdown-preview-safety":
            capture_ax_before = automation.observe_workspace_markdown_preview_safety(
                pid
            )
        capture = automation.capture_window(
            pid,
            window_id,
            screenshot_path,
            include_cursor=case_id
            in {
                "left-pointer-drag",
                "right-pointer-drag",
                "graph-hover-layout-stable",
            },
        )
        if case_id == "local-markdown-preview-safety":
            capture_ax_after = automation.observe_workspace_markdown_preview_safety(
                pid
            )
            expected_ax_state = {
                "pid": pid,
                "window_id": window_id,
                "selected_display_name": "runtime-verification",
                "selected_state_observed": True,
                "preview_heading": "전체 원문",
                "render_completion_observed": True,
                "rendered_semantic_count": 4,
                "semantic_role_order": ["heading", "list", "list", "table"],
                "forbidden_ax_role_counts": {
                    "link": 0,
                    "image": 0,
                    "embedded_web_area": 0,
                },
                "app_web_area_identity": {
                    "role": "AXWebArea",
                    "url_class": "tauri-app-local",
                    "window_id": window_id,
                },
            }

            def compact_ax_state(value: object) -> dict[str, object] | None:
                if not isinstance(value, dict):
                    return None
                return {key: value.get(key) for key in expected_ax_state}

            before_state = compact_ax_state(capture_ax_before)
            after_state = compact_ax_state(capture_ax_after)
            if before_state != expected_ax_state or after_state != before_state:
                raise RuntimeQualificationError(
                    "workspace_markdown_preview_capture_bracket_changed", case_id
                )
            capture_ax_bracket = {
                "before": before_state,
                "after": after_state,
                "unchanged": True,
            }
        if (
            payload.get("proof_level") == "macos-ax-cgevent-sck"
            and case_id in {"left-pointer-drag", "right-pointer-drag"}
            and capture.capture_mode
            != f"{SCREEN_CAPTURE_MODE}_cursor_included_drag_active"
        ):
            raise RuntimeQualificationError(
                "workspace_drag_capture_mode_invalid", case_id
            )
        if (
            payload.get("proof_level") == "macos-ax-cgevent-sck"
            and case_id == "graph-hover-layout-stable"
            and capture.capture_mode
            != f"{SCREEN_CAPTURE_MODE}_cursor_included_graph_hover_active"
        ):
            raise RuntimeQualificationError(
                "workspace_graph_hover_capture_mode_invalid", case_id
            )
        screenshot = {
            "path": screenshot_path.relative_to(evidence_dir).as_posix(),
            "sha256": _sha256_file(screenshot_path),
            **asdict(capture),
        }
    limitations = payload.get("limitations")
    if not isinstance(limitations, list) or not all(
        isinstance(value, str) for value in limitations
    ):
        limitations = list(_DEFAULT_LIMITATIONS)
    record: dict[str, object] = {
        "case_id": case_id,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "expected": _CASE_EXPECTATIONS[case_id],
        "action": payload.get("action"),
        "before": payload.get("before"),
        "after": payload.get("after"),
        "status": "Passed",
        "identity": payload.get("identity"),
        "observation": {
            "path": observation_path.relative_to(evidence_dir).as_posix(),
            "sha256": _sha256_file(observation_path),
        },
        "screenshot": screenshot,
        **(
            {"capture_ax_bracket": capture_ax_bracket}
            if capture_ax_bracket is not None
            else {}
        ),
        "preference": _preference_evidence(payload),
        "command_counters": payload.get("command_counters"),
        **(
            {"runtime_observation": payload.get("observation")}
            if "observation" in payload
            else {}
        ),
        **({"limits": payload.get("limits")} if "limits" in payload else {}),
        "redactions": list(_REDACTIONS),
        "limitations": limitations,
        **(
            {"during": payload.get("during")}
            if "during" in payload
            else {}
        ),
        **(
            {"publication_delta": payload.get("publication_delta")}
            if "publication_delta" in payload
            else {}
        ),
        **(
            {"first_visible": payload.get("first_visible")}
            if "first_visible" in payload
            else {}
        ),
    }
    if case_id == "local-markdown-preview-safety":
        action = payload.get("action")
        runtime_observation = payload.get("observation")
        identity = payload.get("identity")
        if (
            not isinstance(action, dict)
            or not isinstance(runtime_observation, dict)
            or not isinstance(identity, dict)
            or not isinstance(screenshot, dict)
        ):
            raise RuntimeQualificationError(
                "workspace_markdown_preview_evidence_binding_missing", case_id
            )
        binding_source = {
            "selected_display_name": action.get("fixture_display_name"),
            "pid": identity.get("pid"),
            "window_id": identity.get("window_id"),
            "screenshot_sha256": screenshot.get("sha256"),
        }
        record["evidence_binding"] = {
            **binding_source,
            "binding_sha256": _sha256_bytes(
                _canonical_json_bytes(binding_source)
            ),
        }
        negative_scan = _structured_evidence_negative_scan(record)
        if negative_scan["forbidden_literal_count"] != 0:
            raise RuntimeQualificationError(
                "workspace_markdown_preview_structured_evidence_leak", case_id
            )
        record["structured_record_negative_scan"] = negative_scan
    return record


def _permission_unavailable(error: RuntimeQualificationError) -> bool:
    return error.blocked or error.code in _PERMISSION_OR_API_UNAVAILABLE_CODES


def _failure_limitations(error: RuntimeQualificationError) -> list[str]:
    if error.code == "required_ax_semantic_missing":
        return ["required product AX semantic was not exposed"]
    return [f"qualification stopped at {error.operation}"]


def _final_payload(artifact: object, pid: int, zero: dict[str, int]) -> dict[str, object]:
    return {
        "identity": {
            "bundle_identifier": BUNDLE_IDENTIFIER,
            "build_id": getattr(artifact, "build_id"),
            "executable_sha256": getattr(artifact, "executable_sha256"),
            "pid": None,
            "window_id": None,
            "terminated_primary_pid": pid,
        },
        "action": {"kind": "terminate-and-observe-zero"},
        "before": None,
        "after": dict(zero),
        "publication_delta": 0,
        "command_counters": {"sot_load_count": 1, "local_scan_start_count": 0},
    }


def _visible_picker_button(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    frame = value.get("frame")
    if not isinstance(frame, dict):
        return False
    coordinates = tuple(frame.get(key) for key in ("x", "y", "width", "height"))
    return (
        value.get("role") == "AXButton"
        and value.get("visible") is True
        and all(
            isinstance(item, int) and not isinstance(item, bool)
            for item in coordinates
        )
        and int(coordinates[2]) > 0
        and int(coordinates[3]) > 0
    )


def _native_picker_panel_proof_valid(
    value: object, *, expected_pid: int | None = None
) -> bool:
    if not isinstance(value, dict):
        return False
    panel = value.get("panel")
    pid = value.get("pid")
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return (
        value.get("accepted") is True
        and value.get("kind") == "native-directory-panel-open"
        and isinstance(pid, int)
        and not isinstance(pid, bool)
        and pid > 0
        and (expected_pid is None or pid == expected_pid)
        and value.get("panel_count") == 1
        and value.get("panel_identifier")
        in CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIERS
        and value.get("path_redacted") is True
        and value.get("title_redacted") is True
        and isinstance(panel, dict)
        and panel.get("role") == "AXWindow"
        and panel.get("subrole") in {"AXDialog", "AXStandardWindow"}
        and panel.get("visible") is True
        and panel.get("identifier") in CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIERS
        and _visible_picker_button(value.get("default_button"))
        and _visible_picker_button(value.get("cancel_button"))
        and "/" not in serialized
        and "title" not in serialized.replace("title_redacted", "")
    )


def _native_picker_selection_proof_valid(
    value: object, *, expected_pid: int | None = None
) -> bool:
    if not isinstance(value, dict):
        return False
    button = value.get("confirm_button")
    pointer = value.get("pointer_point")
    if not _visible_picker_button(button) or not isinstance(pointer, dict):
        return False
    assert isinstance(button, dict)
    frame = button.get("frame")
    assert isinstance(frame, dict)
    coordinates = tuple(pointer.get(key) for key in ("x", "y"))
    frame_values = tuple(frame.get(key) for key in ("x", "y", "width", "height"))
    if any(
        not isinstance(item, int) or isinstance(item, bool)
        for item in (*coordinates, *frame_values)
    ):
        return False
    pid = value.get("pid")
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return (
        value.get("accepted") is True
        and value.get("kind") == "native-directory-selection"
        and value.get("transport")
        == "cg-event-chord-ax-value-cg-event-mouse-click"
        and isinstance(pid, int)
        and not isinstance(pid, bool)
        and pid > 0
        and (expected_pid is None or pid == expected_pid)
        and value.get("panel_identifier")
        in CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIERS
        and value.get("path_redacted") is True
        and value.get("go_to_folder_value_confirmed") is True
        and value.get("panel_closed") is True
        and int(frame_values[0])
        <= int(coordinates[0])
        <= int(frame_values[0]) + int(frame_values[2])
        and int(frame_values[1])
        <= int(coordinates[1])
        <= int(frame_values[1]) + int(frame_values[3])
        and "/" not in serialized
    )


def _native_picker_closed_proof_valid(
    value: object,
    *,
    expected_pid: int | None = None,
    expected_identifier: str | None = None,
) -> bool:
    if not isinstance(value, dict):
        return False
    pid = value.get("pid")
    identifier = value.get("panel_identifier")
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return (
        set(value)
        == {
            "accepted",
            "kind",
            "pid",
            "panel_identifier",
            "panel_closed",
            "path_redacted",
        }
        and value.get("accepted") is True
        and value.get("kind") == "native-directory-panel-closed"
        and isinstance(pid, int)
        and not isinstance(pid, bool)
        and pid > 0
        and (expected_pid is None or pid == expected_pid)
        and identifier in CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIERS
        and (expected_identifier is None or identifier == expected_identifier)
        and value.get("panel_closed") is True
        and value.get("path_redacted") is True
        and "/" not in serialized
        and "title" not in serialized
    )


def _workspace_picker_evidence_valid(
    value: object, *, expected_pid: int | None = None
) -> bool:
    if not isinstance(value, dict):
        return False
    mutation_count = value.get("checkout_mutation_count")
    panel = value.get("panel")
    cancel_closure = value.get("cancel_panel_closure")
    selection_panel = value.get("selection_panel")
    selection = value.get("selection")
    panel_pid = panel.get("pid") if isinstance(panel, dict) else None
    panel_identifier = (
        panel.get("panel_identifier") if isinstance(panel, dict) else None
    )
    proof_pid = expected_pid
    if proof_pid is None and isinstance(panel_pid, int):
        proof_pid = panel_pid
    proof_identifier = (
        panel_identifier if isinstance(panel_identifier, str) else None
    )
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return (
        value.get("empty_path_triggered") is True
        and value.get("native_panel_open_confirmed") is True
        and value.get("cancel_sequence_invoked") is True
        and value.get("cancel_panel_closed_confirmed") is True
        and value.get("cancel_input_unchanged") is True
        and value.get("cancel_focus_restored") is True
        and isinstance(mutation_count, int)
        and not isinstance(mutation_count, bool)
        and mutation_count == 0
        and value.get("directory_selected") is True
        and value.get("automation_principal") == "System Events"
        and value.get("selection_surface") == "native_directory_picker"
        and _native_picker_panel_proof_valid(panel, expected_pid=expected_pid)
        and _native_picker_closed_proof_valid(
            cancel_closure,
            expected_pid=proof_pid,
            expected_identifier=proof_identifier,
        )
        and _native_picker_panel_proof_valid(
            selection_panel,
            expected_pid=proof_pid,
        )
        and isinstance(selection_panel, dict)
        and selection_panel.get("panel_identifier") == panel_identifier
        and _native_picker_selection_proof_valid(
            selection,
            expected_pid=proof_pid,
        )
        and value.get("fixture_loaded") is True
        and value.get("fixture_registration_mode") == "native_picker_after_cancel"
        and value.get("confirmation_source") == "fixture_graph_and_sot_load"
        and "/" not in serialized
    )


def verify_workspace_layout_macos(
    app: Path,
    evidence_root: Path,
    *,
    port: WorkspaceLayoutAutomationPort | None = None,
    qualification_report: Path | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    case_timeout = min(float(timeout_seconds), float(FIXED_LAYOUT_CONTRACT["case_timeout_seconds"]))
    if not math.isfinite(case_timeout) or case_timeout <= 0:
        raise ValueError("timeout_seconds must be a positive finite number")

    evidence_dir = evidence_root.resolve() / "runtime-workspace-layout"
    evidence_dir.mkdir(parents=True, exist_ok=False)
    cases_dir = evidence_dir / "cases"
    cases_dir.mkdir()
    report_path = evidence_dir / "run-report.json"
    runtime_root = Path(tempfile.mkdtemp(prefix="harness-desktop-layout-runtime-"))
    runtime_root.chmod(0o700)
    runtime_home = runtime_root / "home"
    runtime_home.mkdir(mode=0o700)
    runtime_tmp = runtime_root / "tmp"
    runtime_tmp.mkdir(mode=0o700)
    fixture: SanitizedFixtureCheckout | None = None
    local_fixture: SanitizedLocalScanFixture | None = None
    artifact: object | None = None
    automation = port
    real_runtime_requested = port is None
    active_pids: list[int] = []
    first_pid: int | None = None
    first_visible_arm: object | None = None
    current_case_index = 0
    cases: list[dict[str, object]] = []
    graph_baseline: dict[str, object] | None = None
    command_counters = {"sot_load_count": 0, "local_scan_start_count": 0}
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "failed",
        "driver_sha256": _sha256_file(Path(__file__).resolve()),
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "artifact": None,
        "package_qualification": None,
        "collector": None,
        "preflight": None,
        "fixed_layout_contract": dict(FIXED_LAYOUT_CONTRACT),
        "fixture": None,
        "cases": cases,
        "command_counters": command_counters,
        "lifecycle": {"isolated_home": True, "manual_fallback": False},
        "manual_fallback": False,
        "limitations": [
            "Web Inspector, Chrome, generated script injection, and hidden test controls are prohibited; packaged graph identity comes from the production semantic AX surface",
            "raw fixture source, raw AX trees, raw logs, and absolute paths are not retained",
            "public macOS APIs do not expose a stable cursor kind; col-resize is a static bundle contract while ScreenCaptureKit proves cursor inclusion and AX proves drag-active state",
        ],
    }
    environment = {**os.environ, "HOME": str(runtime_home), "TMPDIR": str(runtime_tmp)}

    def append_case(case_id: str, payload: dict[str, object], started_at: str) -> None:
        nonlocal graph_baseline
        assert artifact is not None and automation is not None
        cases.append(
            _record_passed_case(
                case_id=case_id,
                started_at=started_at,
                payload=payload,
                artifact=artifact,
                automation=automation,
                evidence_dir=evidence_dir,
                cases_dir=cases_dir,
                graph_baseline=graph_baseline,
            )
        )
        if case_id == "graph-state-before-collapse":
            after = payload.get("after")
            fingerprint = after.get("graph_fingerprint") if isinstance(after, dict) else None
            if not isinstance(fingerprint, dict):
                raise RuntimeQualificationError(
                    "workspace_graph_fingerprint_invalid", case_id
                )
            graph_baseline = json.loads(json.dumps(fingerprint))

    try:
        artifact = _artifact_identity(app)
        report["artifact"] = {
            "name": getattr(artifact, "app").name,
            "bundle_identifier": BUNDLE_IDENTIFIER,
            "main_executable": getattr(artifact, "executable").name,
            "executable_sha256": getattr(artifact, "executable_sha256"),
            "build_id": getattr(artifact, "build_id"),
            "version": getattr(artifact, "version"),
            "installed_path_verified": _installed_app_path_verified(artifact),
        }
        if real_runtime_requested and report["artifact"]["installed_path_verified"] is not True:
            raise RuntimeQualificationError(
                "installed_application_path_required", "artifact validation"
            )
        if qualification_report is not None:
            report["package_qualification"] = _package_qualification_identity(
                qualification_report, artifact
            )
        fixture = create_sanitized_fixture_checkout(runtime_root / "checkout")
        local_fixture = create_sanitized_local_scan_fixture(
            runtime_home / "HarnessKitRuntimeLocalFixture"
        )
        report["fixture"] = {
            "manifest_relative_path": fixture.manifest_relative_path,
            "manifest_sha256": fixture.manifest_sha256,
            "anchor_component_ids": list(fixture.anchor_component_ids),
            "schema_source": "repository canonical schemas",
            "git_checkout": True,
            "local_scan_fixture": {
                "display_name": local_fixture.display_name,
                "isolated_home_owned": True,
            },
        }
        if automation is None:
            if sys.platform != "darwin":
                raise RuntimeQualificationError("macos_runtime_required", "platform preflight")
            automation = MacOSWorkspaceLayoutAutomationPort(
                evidence_dir, readiness_timeout_seconds=case_timeout
            )
        collector = automation.prepare_collector(evidence_dir)
        report["collector"] = asdict(collector)
        preflight = automation.preflight()
        report["preflight"] = asdict(preflight)
        _permission_gate(preflight)

        first_pid = automation.launch_primary(artifact, environment)
        active_pids.append(first_pid)
        fixture_load = automation.load_fixture_after_picker_cancel(
            first_pid,
            fixture.root,
            fixture.anchor_component_ids,
            case_timeout,
        )
        command_counters.update(
            {
                key: int(fixture_load[key])
                for key in ("sot_load_count", "local_scan_start_count")
                if isinstance(fixture_load.get(key), int)
                and not isinstance(fixture_load.get(key), bool)
            }
        )
        picker_evidence = fixture_load.get("picker_evidence")
        if not _workspace_picker_evidence_valid(
            picker_evidence, expected_pid=first_pid
        ):
            raise RuntimeQualificationError(
                "workspace_picker_evidence_invalid", "fixture load"
            )
        report_fixture = report.get("fixture")
        if isinstance(report_fixture, dict):
            report_fixture["picker_evidence"] = picker_evidence
        if command_counters != {"sot_load_count": 1, "local_scan_start_count": 0}:
            raise RuntimeQualificationError("workspace_command_counter_mismatch", "fixture load")

        restart_case_index = CASE_IDS.index("restart-first-visible-persisted")
        for current_case_index, case_id in enumerate(CASE_IDS[:restart_case_index]):
            started_at = _utc_now()
            payload = automation.exercise_case(case_id, first_pid, case_timeout)
            append_case(case_id, payload, started_at)

        current_case_index = CASE_IDS.index("restart-first-visible-persisted")
        started_at = _utc_now()
        publication = automation.wait_for_layout_publication(first_pid, case_timeout)
        expected_pair = publication.get("preferred_pair")
        if (
            publication.get("persisted") is not True
            or not isinstance(publication.get("layout_revision"), int)
            or not isinstance(expected_pair, dict)
        ):
            raise RuntimeQualificationError(
                "workspace_layout_publication_unconfirmed", "restart-first-visible-persisted"
            )
        if not automation.terminate(first_pid):
            raise RuntimeQualificationError(
                "workspace_primary_termination_failed", "restart-first-visible-persisted"
            )
        zero_before_restart = automation.wait_for_zero(case_timeout)
        if any(zero_before_restart.get(key) != 0 for key in (
            "main_process_count", "main_window_count", "helper_process_count"
        )):
            raise RuntimeQualificationError(
                "workspace_restart_baseline_nonzero", "restart-first-visible-persisted"
            )
        active_pids.remove(first_pid)
        first_visible_arm = automation.prearm_first_visible(artifact, case_timeout)
        try:
            second_pid = automation.launch_primary(artifact, environment)
            active_pids.append(second_pid)
            payload = automation.observe_first_visible(
                first_visible_arm, second_pid, expected_pair, case_timeout
            )
        finally:
            automation.cancel_first_visible(first_visible_arm)
            first_visible_arm = None
        append_case("restart-first-visible-persisted", payload, started_at)

        for case_id in CASE_IDS[restart_case_index + 1 : -1]:
            current_case_index = CASE_IDS.index(case_id)
            started_at = _utc_now()
            payload = automation.exercise_case(case_id, second_pid, case_timeout)
            payload_pid, _ = _case_identity(payload)
            if payload_pid != second_pid:
                if second_pid in active_pids:
                    active_pids.remove(second_pid)
                if payload_pid not in active_pids:
                    active_pids.append(payload_pid)
                second_pid = payload_pid
            append_case(case_id, payload, started_at)

        current_case_index = CASE_IDS.index("final-cleanup-zero")
        started_at = _utc_now()
        picker_termination = automation.prepare_picker_termination(
            second_pid, case_timeout
        )
        report["lifecycle"]["picker_open_termination"] = picker_termination  # type: ignore[index]
        termination_started = time.monotonic()
        if not automation.terminate(second_pid):
            raise RuntimeQualificationError(
                "workspace_primary_termination_failed", "final-cleanup-zero"
            )
        zero = automation.wait_for_zero(case_timeout)
        if any(zero.get(key) != 0 for key in (
            "main_process_count", "main_window_count", "helper_process_count"
        )):
            raise RuntimeQualificationError("workspace_final_cleanup_nonzero", "final-cleanup-zero")
        active_pids.remove(second_pid)
        report["lifecycle"]["final_cleanup"] = zero  # type: ignore[index]
        picker_termination.update(
            {
                "normal_terminate_accepted": True,
                "main_process_absent": zero["main_process_count"] == 0,
                "main_window_absent": zero["main_window_count"] == 0,
                "observed_helpers_exited": zero["helper_process_count"] == 0,
                "termination_elapsed_ms": max(
                    0, round((time.monotonic() - termination_started) * 1000)
                ),
            }
        )
        append_case(
            "final-cleanup-zero",
            _final_payload(artifact, second_pid, zero),
            started_at,
        )
        report["status"] = "passed"
    except RuntimeQualificationError as error:
        report["error"] = {
            "code": error.code,
            "operation": error.operation,
            **(
                {"evidence": error.evidence}
                if error.evidence is not None
                else {}
            ),
        }
        unavailable = _permission_unavailable(error)
        report["status"] = "blocked" if unavailable else "failed"
        if unavailable and not cases:
            cases.extend(
                _empty_case(case_id, "Unavailable", [f"permission/API unavailable at {error.operation}"])
                for case_id in CASE_IDS
            )
        else:
            if current_case_index < len(cases):
                current_case_index = len(cases)
            if len(cases) < len(CASE_IDS):
                failure_id = CASE_IDS[current_case_index]
                cases.append(
                    _empty_case(
                        failure_id,
                        "Unavailable" if unavailable else "Failed",
                        _failure_limitations(error),
                    )
                )
            cases.extend(
                _empty_case(case_id, "Partial", ["not executed after earlier required case failure"])
                for case_id in CASE_IDS[len(cases) :]
            )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        report["status"] = "failed"
        report["error"] = {
            "code": "workspace_runtime_driver_internal_failure",
            "operation": type(error).__name__,
        }
        if current_case_index < len(cases):
            current_case_index = len(cases)
        if len(cases) < len(CASE_IDS):
            failure_id = CASE_IDS[current_case_index]
            cases.append(
                _empty_case(
                    failure_id,
                    "Failed",
                    ["internal runtime driver failure"],
                )
            )
        cases.extend(
            _empty_case(
                case_id,
                "Partial",
                ["not executed after earlier required case failure"],
            )
            for case_id in CASE_IDS[len(cases) :]
        )
    finally:
        if automation is not None:
            if report.get("status") != "passed":
                try:
                    recovered_pid = automation.current_primary_pid()
                except Exception:
                    recovered_pid = None
                if (
                    isinstance(recovered_pid, int)
                    and not isinstance(recovered_pid, bool)
                    and recovered_pid > 0
                    and recovered_pid not in active_pids
                ):
                    active_pids.append(recovered_pid)
            if first_visible_arm is not None:
                try:
                    automation.cancel_first_visible(first_visible_arm)
                except Exception:
                    pass
            for pid in tuple(reversed(active_pids)):
                try:
                    automation.terminate(pid)
                except Exception:
                    pass
            if active_pids:
                try:
                    cleanup = automation.wait_for_zero(case_timeout)
                except Exception:
                    cleanup = {"cleanup_observation_unavailable": 1}
                report["lifecycle"]["failure_cleanup"] = cleanup  # type: ignore[index]
        if len(cases) < len(CASE_IDS):
            cases.extend(
                _empty_case(case_id, "Partial", ["not executed"])
                for case_id in CASE_IDS[len(cases) :]
            )
        _write_json(report_path, report)
        shutil.rmtree(runtime_root, ignore_errors=True)
    return report_path


def _finite_number(value: object, operation: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeQualificationError("workspace_geometry_invalid", operation)
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RuntimeQualificationError("workspace_geometry_invalid", operation)
    return numeric


def _frame(value: object, operation: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise RuntimeQualificationError("required_ax_semantic_missing", operation)
    result = {
        key: _finite_number(value.get(key), operation)
        for key in ("x", "y", "width", "height")
    }
    if result["width"] < 0 or result["height"] < 0:
        raise RuntimeQualificationError("workspace_geometry_invalid", operation)
    return result


def _rectangles_overlap(left: dict[str, float], right: dict[str, float]) -> bool:
    return (
        min(left["x"] + left["width"], right["x"] + right["width"])
        - max(left["x"], right["x"])
        > 1
        and min(left["y"] + left["height"], right["y"] + right["height"])
        - max(left["y"], right["y"])
        > 1
    )


def _frame_contains(
    outer: dict[str, float], inner: dict[str, float], tolerance: float
) -> bool:
    return (
        inner["x"] >= outer["x"] - tolerance
        and inner["y"] >= outer["y"] - tolerance
        and inner["x"] + inner["width"]
        <= outer["x"] + outer["width"] + tolerance
        and inner["y"] + inner["height"]
        <= outer["y"] + outer["height"] + tolerance
    )


def _frames_match(
    left: dict[str, float], right: dict[str, float], tolerance: float
) -> bool:
    return all(
        _same_within(left[key], right[key], tolerance)
        for key in ("x", "y", "width", "height")
    )


def _safe_target(target: dict[str, object], operation: str) -> dict[str, object]:
    token = target.get("target")
    role = target.get("role")
    name = target.get("name")
    if not isinstance(token, str) or not isinstance(role, str) or not isinstance(name, str):
        raise RuntimeQualificationError("collector_output_invalid", operation)
    safe: dict[str, object] = {
        "target": token,
        "role": role,
        "name": name,
        "frame": _frame(target.get("frame"), operation),
        "visible": target.get("visible") is True,
        "focusable": target.get("focusable") is True,
        "enabled": target.get("enabled") is True,
        "selected": target.get("selected") is True,
        "expanded": (
            target.get("expanded")
            if isinstance(target.get("expanded"), bool)
            else None
        ),
        "expanded_present": target.get("expanded_present") is True,
        "focused": (
            target.get("focused")
            if isinstance(target.get("focused"), bool)
            else None
        ),
    }
    orientation = target.get("orientation")
    if isinstance(orientation, str):
        safe["orientation"] = orientation
    value_description = target.get("value_description")
    if isinstance(value_description, str):
        safe["value_description"] = value_description
    active_state = target.get("active_state")
    if isinstance(active_state, str):
        safe["active_state"] = active_state
    for key in (
        "value",
        "minimum",
        "maximum",
        "current_value",
        "minimum_value",
        "maximum_value",
    ):
        value = target.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
    return safe


def _target_map(snapshot: dict[str, object]) -> dict[str, dict[str, object]]:
    targets = snapshot.get("targets")
    if not isinstance(targets, list):
        raise RuntimeQualificationError("collector_output_invalid", "observe_workspace")
    return {
        str(target["target"]): target
        for target in targets
        if isinstance(target, dict) and isinstance(target.get("target"), str)
    }


def _required_target(
    targets: dict[str, dict[str, object]], token: str, operation: str
) -> dict[str, object]:
    target = targets.get(token)
    if target is None:
        raise RuntimeQualificationError("required_ax_semantic_missing", operation)
    return target


def _pair(snapshot: dict[str, object], key: str) -> dict[str, int]:
    value = snapshot.get(key)
    if not isinstance(value, dict):
        raise RuntimeQualificationError("workspace_preferred_pair_missing", key)
    try:
        left = round(float(value["left_px"]))
        right = round(float(value["right_px"]))
    except (KeyError, TypeError, ValueError):
        raise RuntimeQualificationError("workspace_preferred_pair_missing", key)
    return {"left_px": left, "right_px": right}


def _same_within(left: float, right: float, tolerance: float = 1.0) -> bool:
    return abs(left - right) <= tolerance


_FIRST_VISIBLE_EVENTS = frozenset(
    {
        "identity-bound-visible",
        "identity-bound-hidden",
        "first-visible",
        "visible",
        "not-visible",
        "sck-identity-confirmed",
    }
)


def _sanitize_first_visible_frame(value: object, operation: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise RuntimeQualificationError("first_visible_geometry_invalid", operation)
    frame: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        item = value.get(key)
        if not isinstance(item, int) or isinstance(item, bool):
            raise RuntimeQualificationError("first_visible_geometry_invalid", operation)
        frame[key] = item
    if frame["width"] <= 0 or frame["height"] <= 0:
        raise RuntimeQualificationError("first_visible_geometry_invalid", operation)
    return frame


def _sanitize_first_visible_evidence(
    first_visible: object,
    *,
    expected_pid: int,
    expected_window_id: int,
) -> dict[str, object]:
    operation = "restart-first-visible-persisted"
    if not isinstance(first_visible, dict):
        raise RuntimeQualificationError("first_visible_sequence_invalid", operation)
    normalized: dict[str, object] = {
        "prearmed_before_launch": first_visible.get("prearmed_before_launch"),
        "collector_attached_before_visible": first_visible.get(
            "collector_attached_before_visible"
        ),
        "sequence_gap": first_visible.get("sequence_gap"),
        "first_layer0_sample_sequence": first_visible.get(
            "first_layer0_sample_sequence"
        ),
        "first_complete_frame_sequence": first_visible.get(
            "first_complete_frame_sequence"
        ),
        "on_screen": first_visible.get("on_screen"),
        "alpha": first_visible.get("alpha"),
        "first_visible_geometry_monotonic_seconds": first_visible.get(
            "first_visible_geometry_monotonic_seconds"
        ),
        "first_frame_monotonic_seconds": first_visible.get(
            "first_frame_monotonic_seconds"
        ),
    }
    if (
        normalized["prearmed_before_launch"] is not True
        or normalized["collector_attached_before_visible"] is not True
        or normalized["sequence_gap"] is not False
        or normalized["on_screen"] is not True
    ):
        raise RuntimeQualificationError("first_visible_sequence_invalid", operation)
    try:
        layer_sequence = int(normalized["first_layer0_sample_sequence"])
        frame_sequence = int(normalized["first_complete_frame_sequence"])
        alpha = float(normalized["alpha"])
        geometry_time = float(
            normalized["first_visible_geometry_monotonic_seconds"]
        )
        frame_time = float(normalized["first_frame_monotonic_seconds"])
    except (TypeError, ValueError):
        raise RuntimeQualificationError("first_visible_sequence_invalid", operation)
    if (
        isinstance(normalized["first_layer0_sample_sequence"], bool)
        or isinstance(normalized["first_complete_frame_sequence"], bool)
        or layer_sequence < 1
        or frame_sequence < 1
        or not math.isfinite(alpha)
        or alpha <= 0
        or not math.isfinite(geometry_time)
        or not math.isfinite(frame_time)
        or geometry_time > frame_time
    ):
        raise RuntimeQualificationError("first_visible_sequence_invalid", operation)

    geometry = first_visible.get("first_visible_geometry")
    panes_raw = geometry.get("panes") if isinstance(geometry, dict) else None
    effective_raw = (
        geometry.get("effective_pair") if isinstance(geometry, dict) else None
    )
    if (
        not isinstance(geometry, dict)
        or geometry.get("layout_mode") != "three-pane"
        or not isinstance(panes_raw, dict)
        or set(panes_raw) != {"left", "center", "right"}
        or not isinstance(effective_raw, dict)
    ):
        raise RuntimeQualificationError("first_visible_geometry_invalid", operation)
    try:
        left_px = effective_raw["left_px"]
        right_px = effective_raw["right_px"]
        if (
            not isinstance(left_px, int)
            or isinstance(left_px, bool)
            or not isinstance(right_px, int)
            or isinstance(right_px, bool)
            or left_px <= 0
            or right_px <= 0
        ):
            raise TypeError
    except (KeyError, TypeError):
        raise RuntimeQualificationError("first_visible_geometry_invalid", operation)
    sanitized_geometry = {
        "layout_mode": "three-pane",
        "effective_pair": {"left_px": left_px, "right_px": right_px},
        "window": _sanitize_first_visible_frame(geometry.get("window"), operation),
        "panes": {
            side: _sanitize_first_visible_frame(panes_raw.get(side), operation)
            for side in ("left", "center", "right")
        },
    }

    visibility = first_visible.get("visibility")
    sequence_raw = (
        visibility.get("visibility_sequence")
        if isinstance(visibility, dict)
        else None
    )
    if not isinstance(sequence_raw, list) or not 1 <= len(sequence_raw) <= 64:
        raise RuntimeQualificationError("first_visible_sequence_invalid", operation)
    sequence: list[dict[str, object]] = []
    previous_sequence = -1
    previous_time = -math.inf
    for raw_sample in sequence_raw:
        if not isinstance(raw_sample, dict):
            raise RuntimeQualificationError("first_visible_sequence_invalid", operation)
        sample_sequence = raw_sample.get("sequence")
        event = raw_sample.get("event")
        sample_time = raw_sample.get("monotonic_seconds")
        on_screen = raw_sample.get("on_screen")
        alpha_positive = raw_sample.get("alpha_positive")
        sample_alpha = raw_sample.get("alpha")
        if (
            not isinstance(sample_sequence, int)
            or isinstance(sample_sequence, bool)
            or sample_sequence < 0
            or sample_sequence < previous_sequence
            or event not in _FIRST_VISIBLE_EVENTS
            or not isinstance(sample_time, (int, float))
            or isinstance(sample_time, bool)
            or not math.isfinite(float(sample_time))
            or float(sample_time) < previous_time
            or raw_sample.get("pid") != expected_pid
            or raw_sample.get("window_id") != expected_window_id
            or raw_sample.get("layer") != 0
            or not isinstance(on_screen, bool)
            or not isinstance(alpha_positive, bool)
            or not isinstance(sample_alpha, (int, float))
            or isinstance(sample_alpha, bool)
            or not math.isfinite(float(sample_alpha))
            or float(sample_alpha) < 0
            or alpha_positive is not (float(sample_alpha) > 0)
        ):
            raise RuntimeQualificationError("first_visible_sequence_invalid", operation)
        previous_sequence = sample_sequence
        previous_time = float(sample_time)
        sequence.append(
            {
                "sequence": sample_sequence,
                "event": event,
                "monotonic_seconds": float(sample_time),
                "pid": expected_pid,
                "window_id": expected_window_id,
                "layer": 0,
                "on_screen": on_screen,
                "alpha_positive": alpha_positive,
                "alpha": float(sample_alpha),
            }
        )
    if not any(
        sample["sequence"] == layer_sequence
        and sample["event"] == "first-visible"
        and sample["on_screen"] is True
        and sample["alpha_positive"] is True
        for sample in sequence
    ):
        raise RuntimeQualificationError("first_visible_sequence_invalid", operation)
    return {
        **normalized,
        "first_visible_geometry": sanitized_geometry,
        "visibility_sequence": sequence,
    }


class MacOSWorkspaceLayoutAutomationPort:
    """Real adapter over the shared single-instance macOS observer.

    The shared observer owns all AX/CGEvent/ScreenCaptureKit primitives. This
    class owns product actions, stable-sample qualification, preference fault
    setup, and conversion to the bounded evidence schema.
    """

    def __init__(
        self,
        evidence_dir: Path,
        *,
        readiness_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.evidence_dir = evidence_dir
        self.base = MacOSRuntimeAutomationPort(evidence_dir)
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.artifact: object | None = None
        self.environment: dict[str, str] | None = None
        self.runtime_home: Path | None = None
        self.current_pid: int | None = None
        self.anchor_aliases = dict(
            zip(_CANONICAL_ANCHOR_COMPONENT_IDS, FIXTURE_ANCHOR_COMPONENT_IDS, strict=True)
        )
        self.actual_anchor_ids = _CANONICAL_ANCHOR_COMPONENT_IDS
        self.preferred_pair = {
            "left_px": FIXED_LAYOUT_CONTRACT["default_left_px"],
            "right_px": FIXED_LAYOUT_CONTRACT["default_right_px"],
        }
        self.last_snapshot: dict[str, object] | None = None
        self.graph_before_collapse: dict[str, object] | None = None
        self.prearmed_capture: Path | None = None
        self.prearmed_capture_evidence: ScreenshotCaptureEvidence | None = None
        self.namespace_checked = False
        self.known_helper_pids: set[int] = set()
        self.active_drag_captures: dict[
            str, tuple[Path, ScreenshotCaptureEvidence]
        ] = {}
        self.active_hover_captures: dict[
            str, tuple[Path, ScreenshotCaptureEvidence]
        ] = {}
        self.active_graph_readability_captures: dict[
            str, tuple[Path, ScreenshotCaptureEvidence]
        ] = {}
        self.graph_readability_snapshot_sha256: str | None = None
        self.graph_readability_selected_component_id: str | None = None
        self.pre_workflow_camera: dict[str, object] | None = None
        self.renderer_recovery_fingerprint: dict[str, object] | None = None
        self.last_steady_diagnostic: dict[str, object] = {
            "attempt_count": 0,
            "snapshot_unavailable_count": 0,
            "steady_timeout_count": 0,
            "stable_snapshot_observed": False,
            "anchor_node_count": None,
            "unstable_field_counts": {},
        }

    def _method(self, name: str) -> Any:
        method = getattr(self.base, name, None)
        if not callable(method):
            raise RuntimeQualificationError(
                "workspace_observer_api_missing", name, blocked=False
            )
        return method

    def prepare_collector(self, evidence_dir: Path) -> CollectorEvidence:
        return self.base.prepare_collector(evidence_dir)

    def preflight(self) -> PermissionPreflight:
        return self.base.preflight()

    def _resolve_pid(self, requested_pid: int) -> int:
        if (
            not isinstance(requested_pid, int)
            or isinstance(requested_pid, bool)
            or requested_pid <= 0
        ):
            raise RuntimeQualificationError(
                "workspace_primary_pid_invalid", "runtime session identity"
            )
        if self.current_pid is None:
            raise RuntimeQualificationError(
                "workspace_primary_pid_unbound", "runtime session identity"
            )
        if requested_pid != self.current_pid:
            raise RuntimeQualificationError(
                "workspace_primary_pid_mismatch", "runtime session identity"
            )
        return requested_pid

    def _validated_snapshot_identity(
        self,
        requested_pid: int,
        snapshot: dict[str, object],
        operation: str,
    ) -> dict[str, int]:
        expected_pid = self._resolve_pid(requested_pid)
        identity = snapshot.get("identity")
        if not isinstance(identity, dict):
            raise RuntimeQualificationError(
                "workspace_snapshot_identity_invalid", operation
            )
        snapshot_pid = identity.get("pid")
        window_id = identity.get("window_id")
        if (
            not isinstance(snapshot_pid, int)
            or isinstance(snapshot_pid, bool)
            or not isinstance(window_id, int)
            or isinstance(window_id, bool)
            or snapshot_pid <= 0
            or window_id <= 0
        ):
            raise RuntimeQualificationError(
                "workspace_snapshot_identity_invalid", operation
            )
        if snapshot_pid != expected_pid:
            raise RuntimeQualificationError(
                "workspace_snapshot_pid_mismatch", operation
            )
        return {"pid": snapshot_pid, "window_id": window_id}

    def _namespace_baseline(self, artifact: object) -> None:
        if self.namespace_checked:
            return
        applications = self.base.list_applications(BUNDLE_IDENTIFIER)
        for application in applications:
            if not _same_artifact(application, artifact):
                raise RuntimeQualificationError(
                    "single_instance_namespace_conflict", "bundle namespace preflight"
                )
        for application in applications:
            if not self.base.terminate(application.pid) or not self.base.wait_process_exit(
                application.pid, FIXED_LAYOUT_CONTRACT["case_timeout_seconds"]
            ):
                raise RuntimeQualificationError(
                    "baseline_termination_failed", "bundle namespace preflight"
                )
        self.namespace_checked = True

    def launch_primary(self, artifact: object, environment: dict[str, str]) -> int:
        self._namespace_baseline(artifact)
        self.artifact = artifact
        self.environment = dict(environment)
        self.runtime_home = Path(environment["HOME"])
        pid = self.base.launch_primary(artifact, environment)  # type: ignore[arg-type]
        self.current_pid = pid
        return pid

    def current_primary_pid(self) -> int | None:
        return self.current_pid

    def _runtime_log_marker_counts(self) -> dict[str, int]:
        if self.runtime_home is None:
            raise RuntimeQualificationError("runtime_home_missing", "command counters")
        log_path = (
            self.runtime_home
            / "Library/Logs/io.github.pureliture.harnesskit/HarnessKit.log"
        )
        try:
            metadata = log_path.lstat()
            if log_path.is_symlink() or not log_path.is_file() or metadata.st_size > 1024 * 1024:
                raise OSError
            body = log_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            body = ""
        return {
            "sot_request_count": body.count("SoT snapshot requested"),
            "sot_completion_count": body.count("SoT snapshot loaded:"),
            "local_scan_start_count": body.count("Local scan start requested"),
        }

    def _command_counters(self) -> dict[str, int]:
        marker_counts = self._runtime_log_marker_counts()
        return {
            "sot_load_count": marker_counts["sot_request_count"],
            "local_scan_start_count": marker_counts["local_scan_start_count"],
        }

    def _wait_for_sot_snapshot_completion(
        self, deadline: float
    ) -> dict[str, object]:
        attempt_count = 0
        last_counts = {
            "sot_request_count": 0,
            "sot_completion_count": 0,
            "local_scan_start_count": 0,
        }
        while time.monotonic() < deadline:
            attempt_count += 1
            last_counts = self._runtime_log_marker_counts()
            if (
                last_counts["sot_request_count"] == 1
                and last_counts["sot_completion_count"] == 1
                and last_counts["local_scan_start_count"] == 0
            ):
                return {
                    "attempt_count": attempt_count,
                    **last_counts,
                    "sot_completion_observed": True,
                }
            time.sleep(FIXED_LAYOUT_CONTRACT["steady_sample_interval_ms"] / 1000)
        return {
            "attempt_count": attempt_count,
            **last_counts,
            "sot_completion_observed": False,
        }

    def _logical_token(self, token: str) -> str:
        if token.startswith("component:"):
            component_id = token.removeprefix("component:")
            return f"component:{self.anchor_aliases.get(component_id, component_id)}"
        if token == f"profile:{_CANONICAL_PROFILE_ID}":
            return f"profile:{_PROFILE_ID}"
        return token

    def _normalise_physical_geometry(
        self,
        raw: object,
        raw_window: object,
        target_by_id: dict[str, dict[str, object]],
        layout_mode: str,
        operation: str,
    ) -> dict[str, object]:
        if not isinstance(raw, dict):
            raise RuntimeQualificationError(
                "workspace_physical_geometry_missing", operation
            )
        panes_raw = raw.get("panes")
        pane_present = raw.get("pane_present")
        dividers_raw = raw.get("dividers")
        divider_present = raw.get("divider_present")
        tiling = raw.get("tiling")
        containment = raw.get("containment")
        side_bounds = raw.get("side_bounds")
        responsive = raw.get("responsive")
        collapsed_sides = raw.get("collapsed_sides")
        if not all(
            isinstance(value, dict)
            for value in (
                panes_raw,
                pane_present,
                dividers_raw,
                divider_present,
                tiling,
                containment,
                side_bounds,
                responsive,
            )
        ) or not isinstance(collapsed_sides, list):
            raise RuntimeQualificationError(
                "workspace_physical_geometry_invalid", operation
            )
        assert isinstance(panes_raw, dict)
        assert isinstance(pane_present, dict)
        assert isinstance(dividers_raw, dict)
        assert isinstance(divider_present, dict)
        assert isinstance(tiling, dict)
        assert isinstance(containment, dict)
        assert isinstance(side_bounds, dict)
        assert isinstance(responsive, dict)
        expected_collapsed_sides = _COLLAPSED_SIDES_BY_LAYOUT_MODE.get(layout_mode)
        if (
            expected_collapsed_sides is None
            or set(collapsed_sides) != expected_collapsed_sides
            or set(pane_present) != {"left", "center", "right"}
            or set(divider_present) != {"left", "right"}
            or pane_present.get("center") is not True
            or any(
                pane_present.get(side) is (side in expected_collapsed_sides)
                or divider_present.get(side) is (side in expected_collapsed_sides)
                for side in ("left", "right")
            )
        ):
            raise RuntimeQualificationError(
                "workspace_physical_geometry_invalid", operation
            )
        ax_window = _frame(raw.get("ax_window"), f"{operation} AX window")
        cg_window = _frame(raw.get("cg_window"), f"{operation} CG window")
        shell = _frame(raw.get("workspace_shell"), f"{operation} workspace shell")

        def optional_frame(
            value: object, label: str, present: bool
        ) -> dict[str, float] | None:
            if not present:
                if value is not None:
                    raise RuntimeQualificationError(
                        "workspace_physical_geometry_invalid", operation
                    )
                return None
            return _frame(value, label)

        panes = {
            side: optional_frame(
                panes_raw.get(side),
                f"{operation} {side} pane",
                pane_present.get(side) is True,
            )
            for side in ("left", "center", "right")
        }
        dividers = {
            side: optional_frame(
                dividers_raw.get(side),
                f"{operation} {side} divider",
                divider_present.get(side) is True,
            )
            for side in ("left", "right")
        }
        tolerance = float(FIXED_LAYOUT_CONTRACT["os_frame_tolerance_pt"])
        expected_frames = {
            "shell": _frame(
                _required_target(
                    target_by_id, "workspace-shell", f"{operation} workspace shell"
                ).get("frame"),
                f"{operation} workspace shell",
            ),
            "center": _frame(
                _required_target(
                    target_by_id, "center-pane", f"{operation} center pane"
                ).get("frame"),
                f"{operation} center pane",
            ),
        }
        for side in ("left", "right"):
            pane_target = target_by_id.get(f"{side}-pane")
            divider_target = target_by_id.get(f"{side}-divider")
            side_is_present = side not in expected_collapsed_sides
            if side_is_present:
                if pane_target is None or divider_target is None:
                    raise RuntimeQualificationError(
                        "workspace_physical_geometry_invalid", operation
                    )
                expected_frames[side] = _frame(
                    pane_target.get("frame"), f"{operation} {side} pane"
                )
                expected_frames[f"{side}_divider"] = _frame(
                    divider_target.get("frame"),
                    f"{operation} {side} divider",
                )
            elif pane_target is not None or divider_target is not None:
                raise RuntimeQualificationError(
                    "workspace_physical_geometry_invalid", operation
                )
        raw_window_frame = _frame(raw_window, f"{operation} window")
        present_panes = {
            side: frame for side, frame in panes.items() if frame is not None
        }
        present_dividers = {
            side: frame for side, frame in dividers.items() if frame is not None
        }
        if (
            raw.get("window_frame_match") is not True
            or not _frames_match(ax_window, cg_window, tolerance)
            or not _frames_match(ax_window, raw_window_frame, tolerance)
            or not _frames_match(shell, expected_frames["shell"], tolerance)
            or any(
                not _frames_match(frame, expected_frames[side], tolerance)
                for side, frame in present_panes.items()
            )
            or any(
                not _frames_match(
                    frame, expected_frames[f"{side}_divider"], tolerance
                )
                for side, frame in present_dividers.items()
            )
            or containment.get("workspace_shell_in_ax_window") is not True
            or containment.get("workspace_shell_in_cg_window") is not True
            or containment.get("all_panes") is not True
            or containment.get("all_dividers") is not True
            or tiling.get("divider_widths_match") is not True
            or responsive.get("mode") != layout_mode
            or responsive.get("usable") is not True
            or not _frame_contains(ax_window, shell, tolerance)
            or not _frame_contains(cg_window, shell, tolerance)
            or any(
                not _frame_contains(shell, frame, tolerance)
                for frame in (*present_panes.values(), *present_dividers.values())
            )
        ):
            raise RuntimeQualificationError(
                "workspace_physical_geometry_mismatch", operation
            )
        normalised_bounds: dict[str, dict[str, object]] = {}
        for side in ("left", "right"):
            bounds = side_bounds.get(side)
            if not isinstance(bounds, dict):
                raise RuntimeQualificationError(
                    "workspace_side_bounds_invalid", operation
                )
            pane = panes[side]
            if side in expected_collapsed_sides:
                preferred_width = bounds.get("preferred_width_px")
                if (
                    bounds.get("present") is not False
                    or not isinstance(preferred_width, int)
                    or isinstance(preferred_width, bool)
                    or pane is not None
                ):
                    raise RuntimeQualificationError(
                        "workspace_side_bounds_invalid", operation
                    )
                normalised_bounds[side] = {
                    "present": False,
                    "preferred_width_px": preferred_width,
                }
                continue
            if pane is None or bounds.get("present") is False:
                raise RuntimeQualificationError(
                    "workspace_side_bounds_invalid", operation
                )
            pane_width = _finite_number(bounds.get("pane_width_px"), operation)
            current = _finite_number(bounds.get("current_value"), operation)
            minimum = _finite_number(bounds.get("minimum_value"), operation)
            maximum = _finite_number(bounds.get("maximum_value"), operation)
            if (
                bounds.get("within_bounds") is not True
                or bounds.get("pane_width_matches_value") is not True
                or not minimum <= current <= maximum
                or not _same_within(pane_width, pane["width"], tolerance)
                or not _same_within(current, pane["width"], tolerance)
            ):
                raise RuntimeQualificationError(
                    "workspace_side_bounds_invalid", operation
                )
            normalised_bounds[side] = {
                "present": True,
                "pane_width_px": pane_width,
                "current_value": current,
                "minimum_value": minimum,
                "maximum_value": maximum,
                "within_bounds": True,
                "pane_width_matches_value": True,
            }

        normalised_gaps: dict[str, float | None] = {}
        for side in ("left", "right"):
            gap = tiling.get(f"{side}_gap_px")
            if side in expected_collapsed_sides:
                if gap is not None:
                    raise RuntimeQualificationError(
                        "workspace_physical_geometry_invalid", operation
                    )
                normalised_gaps[side] = None
                continue
            normalised_gaps[side] = _finite_number(gap, operation)

        normalised_responsive: dict[str, object] = {
            "mode": layout_mode,
            "usable": True,
        }
        if "center_continues" in responsive:
            if not isinstance(responsive.get("center_continues"), bool):
                raise RuntimeQualificationError(
                    "workspace_physical_geometry_invalid", operation
                )
            normalised_responsive["center_continues"] = responsive[
                "center_continues"
            ]
        return {
            "ax_window": ax_window,
            "cg_window": cg_window,
            "workspace_shell": shell,
            "window_frame_match": True,
            "panes": panes,
            "pane_present": dict(pane_present),
            "dividers": dividers,
            "divider_present": dict(divider_present),
            "tiling": {
                "left_gap_px": normalised_gaps["left"],
                "right_gap_px": normalised_gaps["right"],
                "divider_widths_match": True,
            },
            "containment": {
                "workspace_shell_in_ax_window": True,
                "workspace_shell_in_cg_window": True,
                "all_panes": True,
                "all_dividers": True,
            },
            "side_bounds": normalised_bounds,
            "responsive": normalised_responsive,
            "collapsed_sides": sorted(expected_collapsed_sides),
        }

    def _normalise_matrix_layout(
        self,
        raw: object,
        operation: str,
    ) -> dict[str, object]:
        if not isinstance(raw, dict):
            raise RuntimeQualificationError(
                "workspace_matrix_observation_invalid", operation
            )
        disclosures = raw.get("disclosures")
        if not isinstance(disclosures, dict):
            raise RuntimeQualificationError(
                "workspace_matrix_observation_invalid", operation
            )
        normalised_disclosures: dict[str, object] = {}
        for key in ("component_map", "profile_matrix"):
            disclosure = disclosures.get(key)
            if not isinstance(disclosure, dict):
                raise RuntimeQualificationError(
                    "workspace_matrix_observation_invalid", operation
                )
            expanded = disclosure.get("expanded")
            body_present = disclosure.get("body_present")
            focus_target_count = disclosure.get("focus_target_count")
            if (
                not isinstance(expanded, bool)
                or not isinstance(body_present, bool)
                or not isinstance(focus_target_count, int)
                or isinstance(focus_target_count, bool)
                or focus_target_count < 0
            ):
                raise RuntimeQualificationError(
                    "workspace_matrix_observation_invalid", operation
                )
            normalised_disclosures[key] = {
                "expanded": expanded,
                "body_present": body_present,
                "focus_target_count": focus_target_count,
            }

        outer_scroll = raw.get("outer_scroll")
        if not isinstance(outer_scroll, dict) or outer_scroll.get("owner") != "workbench":
            raise RuntimeQualificationError(
                "workspace_matrix_observation_invalid", operation
            )
        nested_count = outer_scroll.get("positive_nested_scroll_range_count")
        if (
            not isinstance(nested_count, int)
            or isinstance(nested_count, bool)
            or nested_count < 0
        ):
            raise RuntimeQualificationError(
                "workspace_matrix_observation_invalid", operation
            )
        normalised_scroll = {
            "owner": "workbench",
            "scroll_top_px": _finite_number(
                outer_scroll.get("scroll_top_px"), operation
            ),
            "scroll_height_px": _finite_number(
                outer_scroll.get("scroll_height_px"), operation
            ),
            "client_height_px": _finite_number(
                outer_scroll.get("client_height_px"), operation
            ),
            "positive_nested_scroll_range_count": nested_count,
        }
        if (
            normalised_scroll["scroll_top_px"] < 0
            or normalised_scroll["scroll_height_px"] < 0
            or normalised_scroll["client_height_px"] <= 0
        ):
            raise RuntimeQualificationError(
                "workspace_matrix_observation_invalid", operation
            )

        normalised: dict[str, object] = {
            "disclosures": normalised_disclosures,
            "center_client_frame": _frame(
                raw.get("center_client_frame"), operation
            ),
            "first_viewport_frame": _frame(
                raw.get("first_viewport_frame"), operation
            ),
            "graph_viewport_frame": _frame(
                raw.get("graph_viewport_frame"), operation
            ),
            "matrix_header_frame": _frame(
                raw.get("matrix_header_frame"), operation
            ),
            "outer_scroll": normalised_scroll,
        }
        matrix_body = raw.get("matrix_body_frame")
        normalised["matrix_body_frame"] = (
            None if matrix_body is None else _frame(matrix_body, operation)
        )
        for key in (
            "selected_component_id",
            "active_profile_id",
            "right_detail_id",
        ):
            value = raw.get(key)
            if not isinstance(value, str) or not value or len(value) > 240:
                raise RuntimeQualificationError(
                    "workspace_matrix_observation_invalid", operation
                )
            normalised[key] = value
        owning_profile_ids = raw.get("owning_profile_ids")
        if (
            not isinstance(owning_profile_ids, list)
            or len(owning_profile_ids) > 32
            or any(
                not isinstance(profile_id, str)
                or not profile_id
                or len(profile_id) > 240
                for profile_id in owning_profile_ids
            )
        ):
            raise RuntimeQualificationError(
                "workspace_matrix_observation_invalid", operation
            )
        normalised["owning_profile_ids"] = list(owning_profile_ids)
        camera = raw.get("camera")
        if not isinstance(camera, dict):
            raise RuntimeQualificationError(
                "workspace_matrix_observation_invalid", operation
            )
        camera_available = camera.get("available")
        if not isinstance(camera_available, bool):
            raise RuntimeQualificationError(
                "workspace_matrix_observation_invalid", operation
            )
        normalised_camera: dict[str, object] = {
            "available": camera_available,
        }
        for key, value in camera.items():
            if key == "anchor_nodes":
                if not isinstance(value, list) or len(value) > 8:
                    raise RuntimeQualificationError(
                        "workspace_matrix_observation_invalid", operation
                    )
                normalised_camera[key] = json.loads(
                    json.dumps(value, ensure_ascii=False)
                )
            elif key == "scale":
                normalised_camera[key] = (
                    _finite_number(value, operation)
                    if camera_available
                    else None
                )
                if not camera_available and value is not None:
                    raise RuntimeQualificationError(
                        "workspace_matrix_observation_invalid", operation
                    )
            elif key in {"translate_x", "translate_y"}:
                normalised_camera[key] = _finite_number(value, operation)
        if "scale" not in normalised_camera or "anchor_nodes" not in normalised_camera:
            raise RuntimeQualificationError(
                "workspace_matrix_observation_invalid", operation
            )
        normalised["camera"] = normalised_camera
        return normalised

    def _normalise_graph_readability_ax(
        self,
        raw: object,
        operation: str,
    ) -> dict[str, object]:
        expected_keys = {
            "snapshot_id",
            "viewport_global_frame",
            "component_nodes",
            "observed_kind_tokens",
            "visible_component_static_text_count",
            "profile_identity_visible",
            "unprofiled_identity_visible",
        }
        if not isinstance(raw, dict) or set(raw) != expected_keys:
            raise RuntimeQualificationError(
                "workspace_graph_readability_ax_invalid", operation
            )
        snapshot_id = raw.get("snapshot_id")
        identity_characters = frozenset(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
        )
        if (
            not isinstance(snapshot_id, str)
            or not 1 <= len(snapshot_id) <= 240
            or any(character not in identity_characters for character in snapshot_id)
        ):
            raise RuntimeQualificationError(
                "workspace_graph_readability_ax_invalid", operation
            )
        viewport = _frame(raw.get("viewport_global_frame"), operation)
        if viewport["width"] <= 0 or viewport["height"] <= 0:
            raise RuntimeQualificationError(
                "workspace_graph_readability_ax_invalid", operation
            )
        raw_nodes = raw.get("component_nodes")
        if (
            not isinstance(raw_nodes, list)
            or not raw_nodes
            or len(raw_nodes) > 256
        ):
            raise RuntimeQualificationError(
                "workspace_graph_readability_ax_invalid", operation
            )
        allowed_kinds = set(_CANONICAL_GRAPH_AX_KINDS)
        allowed_component_characters = frozenset(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
        )
        normalised_nodes: list[dict[str, object]] = []
        identities: set[str] = set()
        for raw_node in raw_nodes:
            node_keys = {
                "component_id",
                "kind",
                "role",
                "global_frame",
                "selected",
                "visible",
                "visible_static_text_count",
            }
            if not isinstance(raw_node, dict) or set(raw_node) != node_keys:
                raise RuntimeQualificationError(
                    "workspace_graph_readability_ax_invalid", operation
                )
            component_id = raw_node.get("component_id")
            kind = raw_node.get("kind")
            role = raw_node.get("role")
            selected = raw_node.get("selected")
            visible = raw_node.get("visible")
            static_text_count = raw_node.get("visible_static_text_count")
            component_segments = (
                component_id.split(".")
                if isinstance(component_id, str)
                else []
            )
            if (
                not isinstance(component_id, str)
                or not 1 <= len(component_id) <= 160
                or any(character not in allowed_component_characters for character in component_id)
                or component_id in identities
                or not isinstance(kind, str)
                or kind not in allowed_kinds
                or len(component_segments) < 3
                or any(not segment for segment in component_segments)
                or component_segments[:2] != ["harnesskit", kind]
                or role not in {"AXButton", "AXGroup"}
                or not isinstance(selected, bool)
                or not isinstance(visible, bool)
                or not isinstance(static_text_count, int)
                or isinstance(static_text_count, bool)
                or not 0 <= static_text_count <= 64
            ):
                raise RuntimeQualificationError(
                    "workspace_graph_readability_ax_invalid", operation
                )
            frame = _frame(raw_node.get("global_frame"), operation)
            if frame["width"] <= 0 or frame["height"] <= 0:
                raise RuntimeQualificationError(
                    "workspace_graph_readability_ax_invalid", operation
                )
            identities.add(component_id)
            normalised_nodes.append(
                {
                    "component_id": component_id,
                    "kind": kind,
                    "role": role,
                    "global_frame": frame,
                    "selected": selected,
                    "visible": visible,
                    "visible_static_text_count": static_text_count,
                }
            )
        normalised_nodes.sort(key=lambda node: str(node["component_id"]))
        observed_kinds = sorted(
            {str(node["kind"]) for node in normalised_nodes}
        )
        raw_observed_kinds = raw.get("observed_kind_tokens")
        visible_static_text_count = raw.get(
            "visible_component_static_text_count"
        )
        profile_visible = raw.get("profile_identity_visible")
        unprofiled_visible = raw.get("unprofiled_identity_visible")
        if (
            raw_observed_kinds != observed_kinds
            or not isinstance(visible_static_text_count, int)
            or isinstance(visible_static_text_count, bool)
            or visible_static_text_count
            != sum(
                int(node["visible_static_text_count"])
                for node in normalised_nodes
            )
            or not isinstance(profile_visible, bool)
            or not isinstance(unprofiled_visible, bool)
        ):
            raise RuntimeQualificationError(
                "workspace_graph_readability_ax_invalid", operation
            )
        return {
            "snapshot_id_sha256": hashlib.sha256(
                snapshot_id.encode("utf-8")
            ).hexdigest(),
            "viewport_global_frame": viewport,
            "component_nodes": normalised_nodes,
            "observed_kind_tokens": observed_kinds,
            "visible_component_static_text_count": visible_static_text_count,
            "profile_identity_visible": profile_visible,
            "unprofiled_identity_visible": unprofiled_visible,
        }

    def _normalise_matrix_wrap_sample(
        self,
        raw: object,
        operation: str,
    ) -> dict[str, object]:
        if not isinstance(raw, dict):
            raise RuntimeQualificationError(
                "workspace_matrix_wrap_observation_invalid", operation
            )
        sample: dict[str, object] = {
            "center_inline_px": _finite_number(
                raw.get("center_inline_px"), operation
            ),
            "root_font_px": _finite_number(raw.get("root_font_px"), operation),
        }
        if sample["center_inline_px"] <= 0 or sample["root_font_px"] <= 0:
            raise RuntimeQualificationError(
                "workspace_matrix_wrap_observation_invalid", operation
            )
        for count_key in ("profile_column_count", "member_column_count"):
            count = raw.get(count_key)
            if count is not None:
                if (
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or count <= 0
                ):
                    raise RuntimeQualificationError(
                        "workspace_matrix_wrap_observation_invalid", operation
                    )
                sample[count_key] = count
        for key in ("profile_cards", "member_cards"):
            cards = raw.get(key)
            if not isinstance(cards, list) or not cards or len(cards) > 256:
                raise RuntimeQualificationError(
                    "workspace_matrix_wrap_observation_invalid", operation
                )
            normalised_cards: list[dict[str, object]] = []
            identities: set[str] = set()
            for card in cards:
                if not isinstance(card, dict):
                    raise RuntimeQualificationError(
                        "workspace_matrix_wrap_observation_invalid", operation
                    )
                identity = card.get("identity")
                if (
                    not isinstance(identity, str)
                    or not identity
                    or len(identity) > 240
                    or identity in identities
                ):
                    raise RuntimeQualificationError(
                        "workspace_matrix_wrap_observation_invalid", operation
                    )
                identities.add(identity)
                normalised_cards.append(
                    {
                        "identity": identity,
                        "frame": _frame(card.get("frame"), operation),
                        "title_font_px": _finite_number(
                            card.get("title_font_px"), operation
                        ),
                        "metadata_font_px": _finite_number(
                            card.get("metadata_font_px"), operation
                        ),
                        "content_top_inset_px": _finite_number(
                            card.get("content_top_inset_px"), operation
                        ),
                    }
                )
            sample[key] = normalised_cards
        return sample

    def _normalise_collapsed_workbench_layout(
        self,
        raw: object,
        operation: str,
    ) -> dict[str, object]:
        expected_ids = {
            "center": "workbench",
            "first_viewport": "sot-workbench-first-viewport",
            "matrix_header": "profile-matrix",
            "matrix_body": "profile-matrix-body",
        }
        if not isinstance(raw, dict) or raw.get("stable_ids") != expected_ids:
            raise RuntimeQualificationError(
                "workspace_collapsed_layout_observation_invalid", operation
            )
        map_expanded = raw.get("component_map_expanded")
        matrix_expanded = raw.get("profile_matrix_expanded")
        if not isinstance(map_expanded, bool) or not isinstance(
            matrix_expanded, bool
        ):
            raise RuntimeQualificationError(
                "workspace_collapsed_layout_observation_invalid", operation
            )
        try:
            frames = {
                key: _frame(raw.get(key), operation)
                for key in (
                    "center_frame",
                    "first_viewport_frame",
                    "matrix_header_frame",
                    "matrix_body_frame",
                )
            }
        except RuntimeQualificationError:
            raise RuntimeQualificationError(
                "workspace_collapsed_layout_observation_invalid", operation
            ) from None
        return {
            "stable_ids": dict(expected_ids),
            "component_map_expanded": map_expanded,
            "profile_matrix_expanded": matrix_expanded,
            **frames,
        }

    def _normalise_semantic_graph(
        self,
        raw: object,
        operation: str,
    ) -> dict[str, object]:
        code = "workspace_semantic_graph_observation_invalid"
        if not isinstance(raw, dict):
            raise RuntimeQualificationError(code, operation)

        def bounded_string(value: object, *, limit: int = 320) -> str:
            if not isinstance(value, str) or not value or len(value) > limit:
                raise RuntimeQualificationError(code, operation)
            return value

        def ax_record(
            value: object,
            *,
            identifier_prefix: str | None = None,
            allow_empty_name: bool = False,
        ) -> dict[str, object]:
            if not isinstance(value, dict):
                raise RuntimeQualificationError(code, operation)
            identifier = bounded_string(value.get("dom_identifier"))
            role = bounded_string(value.get("role"), limit=80)
            raw_name = value.get("name")
            if (
                not isinstance(raw_name, str)
                or len(raw_name) > 512
                or (not allow_empty_name and not raw_name)
            ):
                raise RuntimeQualificationError(code, operation)
            name = raw_name
            if identifier_prefix is not None and not identifier.startswith(
                identifier_prefix
            ):
                raise RuntimeQualificationError(code, operation)
            if role not in {
                "AXButton",
                "AXCheckBox",
                "AXGroup",
                "AXStaticText",
                "AXRadioButton",
            }:
                raise RuntimeQualificationError(code, operation)
            selected = value.get("selected")
            focused = value.get("focused")
            visible = value.get("visible")
            if not all(isinstance(item, bool) for item in (selected, focused, visible)):
                raise RuntimeQualificationError(code, operation)
            return {
                "dom_identifier": identifier,
                "role": role,
                "name": name,
                "selected": selected,
                "focused": focused,
                "visible": visible,
                "frame": _frame(value.get("frame"), operation),
            }

        raw_relations = raw.get("relations")
        raw_components = raw.get("components")
        raw_steps = raw.get("workflow_steps")
        if (
            not isinstance(raw_relations, list)
            or not 1 <= len(raw_relations) <= 128
            or not isinstance(raw_components, list)
            or not 1 <= len(raw_components) <= 512
            or not isinstance(raw_steps, list)
            or len(raw_steps) > 2048
        ):
            raise RuntimeQualificationError(code, operation)

        relations: list[dict[str, object]] = []
        for value in raw_relations:
            record = ax_record(value, identifier_prefix="semantic-relation:")
            assert isinstance(value, dict)
            relation_kind = bounded_string(value.get("relation_kind"), limit=16)
            node_id = bounded_string(value.get("node_id"), limit=240)
            canonical_id = bounded_string(value.get("canonical_id"), limit=200)
            exact_count = value.get("exact_count")
            members = value.get("member_component_ids")
            if (
                relation_kind not in {"profile", "workflow", "unprofiled"}
                or (
                    relation_kind == "unprofiled"
                    and canonical_id != "projection:unprofiled"
                )
                or node_id != f"{relation_kind}:{canonical_id}"
                or record["dom_identifier"] != f"semantic-relation:{node_id}"
                or not isinstance(exact_count, int)
                or isinstance(exact_count, bool)
                or not 0 <= exact_count <= 10_000
                or not isinstance(members, list)
                or len(members) > 512
                or any(
                    not isinstance(member, str)
                    or not member
                    or len(member) > 200
                    for member in members
                )
                or len(members) != len(set(members))
            ):
                raise RuntimeQualificationError(code, operation)
            relations.append(
                {
                    **record,
                    "node_id": node_id,
                    "relation_kind": relation_kind,
                    "canonical_id": canonical_id,
                    "exact_count": exact_count,
                    "member_component_ids": sorted(members),
                }
            )

        components: list[dict[str, object]] = []
        for value in raw_components:
            record = ax_record(value, identifier_prefix="semantic-component:")
            assert isinstance(value, dict)
            component_id = bounded_string(value.get("component_id"), limit=200)
            node_id = bounded_string(value.get("node_id"), limit=240)
            segments = component_id.split(".")
            if (
                node_id != f"component:{component_id}"
                or record["dom_identifier"] != f"semantic-component:{component_id}"
                or len(segments) < 3
                or segments[0] != "harnesskit"
                or segments[1] not in _CANONICAL_GRAPH_AX_KINDS
            ):
                raise RuntimeQualificationError(code, operation)
            components.append(
                {
                    **record,
                    "node_id": node_id,
                    "component_id": component_id,
                    "kind": segments[1],
                }
            )

        workflow_steps: list[dict[str, object]] = []
        for value in raw_steps:
            record = ax_record(
                value, identifier_prefix="semantic-workflow-step:"
            )
            assert isinstance(value, dict)
            workflow_id = bounded_string(value.get("workflow_id"), limit=200)
            step_id = bounded_string(value.get("step_id"), limit=200)
            ordinal = value.get("ordinal")
            if (
                not isinstance(ordinal, int)
                or isinstance(ordinal, bool)
                or not 1 <= ordinal <= 10_000
                or value.get("node_id") != f"workflow:{workflow_id}"
                or not str(record["dom_identifier"]).startswith(
                    f"semantic-workflow-step:{workflow_id}:{ordinal}:"
                )
            ):
                raise RuntimeQualificationError(code, operation)
            workflow_steps.append(
                {
                    **record,
                    "node_id": f"workflow:{workflow_id}",
                    "workflow_id": workflow_id,
                    "ordinal": ordinal,
                    "step_id": step_id,
                }
            )

        if len({str(item["dom_identifier"]) for item in relations + components + workflow_steps}) != len(
            relations + components + workflow_steps
        ):
            raise RuntimeQualificationError(code, operation)
        component_ids = {str(item["component_id"]) for item in components}
        if any(
            not set(relation["member_component_ids"]).issubset(component_ids)
            for relation in relations
        ):
            raise RuntimeQualificationError(code, operation)

        active_profile_id = bounded_string(
            raw.get("active_profile_id"), limit=200
        )
        if (
            active_profile_id != "__unprofiled__"
            and re.fullmatch(
                r"harnesskit\.profile\.[A-Za-z0-9_.-]+", active_profile_id
            )
            is None
        ):
            raise RuntimeQualificationError(code, operation)

        projection_id = bounded_string(raw.get("projection_id"), limit=200)
        settled_node_positions_hash = bounded_string(
            raw.get("settled_node_positions_hash"), limit=16
        )
        if (
            re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", projection_id) is None
            or re.fullmatch(
                r"[0-9a-f]{16}", settled_node_positions_hash
            )
            is None
        ):
            raise RuntimeQualificationError(code, operation)

        identity = raw.get("identity_overlay")
        renderer = raw.get("renderer")
        camera = raw.get("camera")
        inspector = raw.get("workflow_inspector")
        if (
            not isinstance(identity, dict)
            or not isinstance(renderer, dict)
            or not isinstance(camera, dict)
            or not isinstance(inspector, dict)
        ):
            raise RuntimeQualificationError(code, operation)
        identity_overlay: dict[str, object] = {}
        identity_identifiers = {
            "container": "component-map-identity-overlay",
            "title": "component-map-identity-title",
            "kind": "component-map-identity-kind",
            "count": "component-map-identity-count",
        }
        for key, expected_identifier in identity_identifiers.items():
            present = identity.get(f"{key}_present")
            record_value = identity.get(key)
            if not isinstance(present, bool):
                raise RuntimeQualificationError(code, operation)
            identity_overlay[f"{key}_present"] = present
            if present:
                record = ax_record(
                    record_value,
                    allow_empty_name=key in {"container", "title"},
                )
                if record["dom_identifier"] != expected_identifier:
                    raise RuntimeQualificationError(code, operation)
                identity_overlay[key] = record
            elif record_value is not None:
                raise RuntimeQualificationError(code, operation)

        status_present = renderer.get("status_present")
        status_visible = renderer.get("status_visible")
        if not isinstance(status_present, bool) or not isinstance(
            status_visible, bool
        ):
            raise RuntimeQualificationError(code, operation)
        renderer_status: dict[str, object] | None = None
        if status_present:
            renderer_status = ax_record(
                renderer.get("status"), allow_empty_name=True
            )
            if renderer_status["dom_identifier"] != "component-map-renderer-status":
                raise RuntimeQualificationError(code, operation)
            if renderer_status["visible"] is not status_visible:
                raise RuntimeQualificationError(code, operation)
        elif renderer.get("status") is not None or status_visible:
            raise RuntimeQualificationError(code, operation)
        renderer_availability = renderer.get("availability")
        semantic_primary_visible = renderer.get("semantic_primary_visible")
        retry_present = renderer.get("retry_present")
        retry_visible = renderer.get("retry_visible")
        if (
            renderer_availability
            not in {"ready", "manual_fallback", "unavailable"}
            or not isinstance(semantic_primary_visible, bool)
            or not isinstance(retry_present, bool)
            or not isinstance(retry_visible, bool)
        ):
            raise RuntimeQualificationError(code, operation)
        normalised_renderer: dict[str, object] = {
            "availability": renderer_availability,
            "semantic_primary_visible": semantic_primary_visible,
            "semantic_view_frame": _frame(
                renderer.get("semantic_view_frame"), operation
            ),
            "status_present": status_present,
            "status_visible": status_visible,
            "retry_present": retry_present,
            "retry_visible": retry_visible,
        }
        if renderer_status is not None:
            normalised_renderer["status"] = renderer_status
        if retry_present:
            retry = ax_record(renderer.get("retry"))
            if retry["dom_identifier"] != "component-map-renderer-retry":
                raise RuntimeQualificationError(code, operation)
            normalised_renderer["retry"] = retry

        camera_ready = camera.get("ready")
        camera_scale = camera.get("scale")
        camera_level = camera.get("level")
        camera_pose: dict[str, dict[str, float]] | None = None
        raw_position = camera.get("position")
        raw_target = camera.get("target")
        if raw_position is not None or raw_target is not None:
            if not isinstance(raw_position, dict) or not isinstance(
                raw_target, dict
            ):
                raise RuntimeQualificationError(code, operation)
            if set(raw_position) != {"x", "y", "z"} or set(raw_target) != {
                "x",
                "y",
                "z",
            }:
                raise RuntimeQualificationError(code, operation)
            camera_pose = {
                "position": {
                    axis: _finite_number(raw_position.get(axis), operation)
                    for axis in ("x", "y", "z")
                },
                "target": {
                    axis: _finite_number(raw_target.get(axis), operation)
                    for axis in ("x", "y", "z")
                },
            }
        camera_flags = {
            key: camera.get(key)
            for key in ("zoom_out_enabled", "fit_enabled", "zoom_in_enabled")
        }
        if not isinstance(camera_ready, bool) or not all(
            isinstance(value, bool) for value in camera_flags.values()
        ):
            raise RuntimeQualificationError(code, operation)
        if camera_ready:
            if (
                camera_pose is None
                or
                not isinstance(camera_scale, (int, float))
                or isinstance(camera_scale, bool)
                or not math.isfinite(float(camera_scale))
                or not 0.75 <= float(camera_scale) <= 8.0
                or camera_level not in {"개요", "상세", "최대"}
                or (
                    float(camera_scale) == 8.0
                    and (
                        camera_level != "최대"
                        or camera_flags["zoom_in_enabled"] is not False
                    )
                )
                or (camera_level == "최대" and float(camera_scale) != 8.0)
                or (
                    float(camera_scale) == 0.75
                    and camera_flags["zoom_out_enabled"] is not False
                )
            ):
                raise RuntimeQualificationError(code, operation)
            normalised_camera = {
                "ready": True,
                "scale": float(camera_scale),
                "level": camera_level,
                **camera_pose,
                **camera_flags,
            }
        else:
            if (
                camera_scale is not None
                or camera_level is not None
                or any(camera_flags.values())
            ):
                raise RuntimeQualificationError(code, operation)
            normalised_camera = {
                "ready": False,
                "scale": None,
                "level": None,
                **camera_flags,
            }
            if camera_pose is not None:
                normalised_camera.update(camera_pose)

        visible = inspector.get("visible")
        steps = inspector.get("steps")
        occurrences = inspector.get("ordinal_occurrences")
        roles = inspector.get("component_roles")
        warnings = inspector.get("unresolved_warning_set")
        if (
            not isinstance(visible, bool)
            or not isinstance(steps, list)
            or len(steps) > 2048
            or not isinstance(occurrences, list)
            or len(occurrences) > 2048
            or not isinstance(roles, list)
            or len(roles) > 512
            or not isinstance(warnings, list)
            or len(warnings) > 512
        ):
            raise RuntimeQualificationError(code, operation)
        workflow_id = inspector.get("workflow_id")
        runtime_state = inspector.get("runtime_state")
        if visible and (
            not isinstance(workflow_id, str)
            or not workflow_id
            or not isinstance(runtime_state, str)
            or not runtime_state
        ):
            raise RuntimeQualificationError(code, operation)
        inspector_steps = [
            {
                **ax_record(step, identifier_prefix="workflow-inspector-step:"),
                "workflow_id": bounded_string(step.get("workflow_id"), limit=200),
                "ordinal": step.get("ordinal"),
            }
            for step in steps
            if isinstance(step, dict)
            and set(step)
            == {
                "dom_identifier",
                "role",
                "name",
                "selected",
                "focused",
                "visible",
                "frame",
                "workflow_id",
                "ordinal",
            }
        ]
        if len(inspector_steps) != len(steps) or any(
            not isinstance(step["ordinal"], int)
            or isinstance(step["ordinal"], bool)
            or int(step["ordinal"]) < 1
            for step in inspector_steps
        ):
            raise RuntimeQualificationError(code, operation)

        def occurrence_record(value: object) -> dict[str, object]:
            if not isinstance(value, dict):
                raise RuntimeQualificationError(code, operation)
            ordinal = value.get("ordinal")
            if (
                not isinstance(ordinal, int)
                or isinstance(ordinal, bool)
                or not 1 <= ordinal <= 10_000
            ):
                raise RuntimeQualificationError(code, operation)
            return {
                "workflow_id": bounded_string(value.get("workflow_id"), limit=200),
                "ordinal": ordinal,
                "component_id": bounded_string(value.get("component_id"), limit=200),
            }

        normalised_occurrences = [
            occurrence_record(value)
            for value in occurrences
            if isinstance(value, dict)
            and set(value) == {"workflow_id", "ordinal", "component_id"}
        ]
        if len(normalised_occurrences) != len(occurrences):
            raise RuntimeQualificationError(code, operation)
        normalised_roles: list[dict[str, object]] = []
        for value in roles:
            if not isinstance(value, dict) or set(value) != {"component_id", "role"}:
                raise RuntimeQualificationError(code, operation)
            normalised_roles.append(
                {
                    "component_id": bounded_string(
                        value.get("component_id"), limit=200
                    ),
                    "role": bounded_string(value.get("role"), limit=200),
                }
            )

        normalised_warnings: list[dict[str, object]] = []
        for value in warnings:
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "workflow_id",
                    "ordinal",
                    "reference_kind",
                    "source_field",
                    "reference",
                }
            ):
                raise RuntimeQualificationError(code, operation)
            ordinal = value.get("ordinal")
            if (
                not isinstance(ordinal, int)
                or isinstance(ordinal, bool)
                or not 1 <= ordinal <= 10_000
            ):
                raise RuntimeQualificationError(code, operation)
            normalised_warnings.append(
                {
                    "workflow_id": bounded_string(value.get("workflow_id"), limit=200),
                    "ordinal": ordinal,
                    "reference_kind": bounded_string(value.get("reference_kind"), limit=120),
                    "source_field": bounded_string(value.get("source_field"), limit=200),
                    "reference": bounded_string(value.get("reference"), limit=240),
                }
            )

        def unique_records(
            values: list[dict[str, object]], keys: tuple[str, ...]
        ) -> bool:
            identities = [tuple(value.get(key) for key in keys) for value in values]
            return len(identities) == len(set(identities))

        semantic_step_keys = {
            (step["workflow_id"], step["ordinal"])
            for step in workflow_steps
        }
        inspector_step_keys = {
            (step["workflow_id"], step["ordinal"])
            for step in inspector_steps
        }
        occurrence_step_keys = {
            (entry["workflow_id"], entry["ordinal"])
            for entry in normalised_occurrences
        }
        occurrence_component_ids = {
            entry["component_id"] for entry in normalised_occurrences
        }
        if (
            not unique_records(
                workflow_steps, ("workflow_id", "ordinal", "step_id")
            )
            or not unique_records(
                inspector_steps, ("workflow_id", "ordinal")
            )
            or not unique_records(
                normalised_occurrences,
                ("workflow_id", "ordinal", "component_id"),
            )
            or not unique_records(
                normalised_roles, ("component_id", "role")
            )
            or any(
                entry["component_id"] not in component_ids
                for entry in normalised_occurrences
            )
            or any(
                entry["component_id"] not in component_ids
                or entry["component_id"] not in occurrence_component_ids
                for entry in normalised_roles
            )
            or not occurrence_step_keys.issubset(semantic_step_keys)
            or any(
                (warning["workflow_id"], warning["ordinal"])
                not in {
                    (step["workflow_id"], step["ordinal"])
                    for step in workflow_steps
                }
                for warning in normalised_warnings
            )
        ):
            raise RuntimeQualificationError(code, operation)
        if visible and (
            not isinstance(workflow_id, str)
            or {
                key for key in semantic_step_keys if key[0] == workflow_id
            }
            != inspector_step_keys
        ):
            raise RuntimeQualificationError(code, operation)
        normalised_inspector = {
            "visible": visible,
            "workflow_id": workflow_id,
            "runtime_state": runtime_state,
            "steps": inspector_steps,
            "ordinal_occurrences": normalised_occurrences,
            "component_roles": normalised_roles,
            "unresolved_warning_set": normalised_warnings,
        }
        normalised_fit_report: dict[str, object] | None = None
        raw_fit_report = raw.get("fit_report")
        if raw_fit_report is not None:
            fit_keys = {
                "identity_count",
                "relation_count",
                "component_count",
                "envelope_count",
                "camera_status",
                "in_frustum_envelope_count",
                "total_envelope_count",
                "largest_dimension_occupancy",
                "scene_bounds",
            }
            if not isinstance(raw_fit_report, dict) or set(raw_fit_report) != fit_keys:
                raise RuntimeQualificationError(code, operation)

            def fit_count(key: str) -> int:
                value = raw_fit_report.get(key)
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 0 <= value <= 10_000
                ):
                    raise RuntimeQualificationError(code, operation)
                return value

            identity_count = fit_count("identity_count")
            relation_count = fit_count("relation_count")
            component_count = fit_count("component_count")
            envelope_count = fit_count("envelope_count")
            in_frustum_count = fit_count("in_frustum_envelope_count")
            total_envelope_count = fit_count("total_envelope_count")
            camera_status = raw_fit_report.get("camera_status")
            largest_dimension_occupancy = _finite_number(
                raw_fit_report.get("largest_dimension_occupancy"), operation
            )
            raw_bounds = raw_fit_report.get("scene_bounds")
            if (
                camera_status not in {"complete", "cropped"}
                or not 0 <= largest_dimension_occupancy <= 1
                or identity_count != len(relations) + len(components)
                or relation_count != len(relations)
                or component_count != len(components)
                or envelope_count != identity_count
                or total_envelope_count != envelope_count
                or in_frustum_count > total_envelope_count
                or not isinstance(raw_bounds, dict)
                or set(raw_bounds) != {"min", "max"}
            ):
                raise RuntimeQualificationError(code, operation)
            scene_bounds: dict[str, dict[str, float]] = {}
            for boundary in ("min", "max"):
                raw_vector = raw_bounds.get(boundary)
                if not isinstance(raw_vector, dict) or set(raw_vector) != {"x", "y", "z"}:
                    raise RuntimeQualificationError(code, operation)
                scene_bounds[boundary] = {
                    axis: _finite_number(raw_vector.get(axis), operation)
                    for axis in ("x", "y", "z")
                }
            if any(
                scene_bounds["min"][axis] >= scene_bounds["max"][axis]
                for axis in ("x", "y", "z")
            ):
                raise RuntimeQualificationError(code, operation)
            normalised_fit_report = {
                "identity_count": identity_count,
                "relation_count": relation_count,
                "component_count": component_count,
                "envelope_count": envelope_count,
                "camera_status": camera_status,
                "in_frustum_envelope_count": in_frustum_count,
                "total_envelope_count": total_envelope_count,
                "largest_dimension_occupancy": largest_dimension_occupancy,
                "scene_bounds": scene_bounds,
            }

        normalised_graph = {
            "projection_id": projection_id,
            "settled_node_positions_hash": settled_node_positions_hash,
            "active_profile_id": active_profile_id,
            "relations": relations,
            "components": components,
            "workflow_steps": workflow_steps,
            "identity_overlay": identity_overlay,
            "renderer": normalised_renderer,
            "camera": normalised_camera,
            "workflow_inspector": normalised_inspector,
        }
        if normalised_fit_report is not None:
            normalised_graph["fit_report"] = normalised_fit_report
        return normalised_graph

    def _normalise_observation(self, raw: object, operation: str) -> dict[str, object]:
        if not isinstance(raw, dict) or raw.get("accepted") is not True:
            raise RuntimeQualificationError("collector_output_invalid", operation)
        try:
            pid = int(raw["pid"])
            window_id = int(raw["window_id"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeQualificationError("collector_output_invalid", operation)
        if pid <= 0 or window_id <= 0:
            raise RuntimeQualificationError("collector_output_invalid", operation)
        raw_targets = raw.get("targets")
        if not isinstance(raw_targets, list) or len(raw_targets) > FIXED_LAYOUT_CONTRACT["ax_target_limit"]:
            raise RuntimeQualificationError("workspace_ax_target_limit_exceeded", operation)
        targets = []
        for raw_target in raw_targets:
            if not isinstance(raw_target, dict):
                raise RuntimeQualificationError("collector_output_invalid", operation)
            target = _safe_target(raw_target, operation)
            target["target"] = self._logical_token(str(target["target"]))
            targets.append(target)
        target_by_id = {str(target["target"]): target for target in targets}
        layout_mode = raw.get("layout_mode")
        if layout_mode not in _COLLAPSED_SIDES_BY_LAYOUT_MODE:
            raise RuntimeQualificationError("workspace_layout_mode_missing", operation)
        assert isinstance(layout_mode, str)
        collapsed_sides = _COLLAPSED_SIDES_BY_LAYOUT_MODE[layout_mode]
        required_targets = [
            "workspace-shell",
            "center-pane",
            "preferred-widths",
        ]
        for side in ("left", "right"):
            if side not in collapsed_sides:
                required_targets.extend((f"{side}-pane", f"{side}-divider"))
        for token in required_targets:
            _required_target(target_by_id, token, f"{operation} {token}")
        physical_geometry = self._normalise_physical_geometry(
            raw.get("physical_geometry"),
            raw.get("window_frame"),
            target_by_id,
            layout_mode,
            operation,
        )
        physical_panes = physical_geometry["panes"]
        physical_dividers = physical_geometry["dividers"]
        assert isinstance(physical_panes, dict)
        assert isinstance(physical_dividers, dict)
        geometry = {
            "window": physical_geometry["ax_window"],
            "shell": physical_geometry["workspace_shell"],
            "left": physical_panes["left"],
            "center": physical_panes["center"],
            "right": physical_panes["right"],
            "left_separator": physical_dividers["left"],
            "right_separator": physical_dividers["right"],
        }
        panes = [
            pane
            for pane in (geometry["left"], geometry["center"], geometry["right"])
            if isinstance(pane, dict)
        ]
        if any(
            _rectangles_overlap(panes[left], panes[right])
            for left in range(len(panes))
            for right in range(left + 1, len(panes))
        ):
            raise RuntimeQualificationError("workspace_pane_overlap", operation)
        center = geometry["center"]
        assert isinstance(center, dict)
        center_width = float(center["width"])
        if center_width + 1 < FIXED_LAYOUT_CONTRACT["center_min_px"]:
            raise RuntimeQualificationError("workspace_center_minimum_violated", operation)
        preferred = raw.get("preferred_pair")
        if not isinstance(preferred, dict):
            raise RuntimeQualificationError("workspace_preferred_pair_missing", operation)
        left_preferred = preferred.get("left_px")
        right_preferred = preferred.get("right_px")
        if (
            not isinstance(left_preferred, int)
            or isinstance(left_preferred, bool)
            or not isinstance(right_preferred, int)
            or isinstance(right_preferred, bool)
            or not FIXED_LAYOUT_CONTRACT["left_min_px"]
            <= left_preferred
            <= FIXED_LAYOUT_CONTRACT["left_max_px"]
            or not FIXED_LAYOUT_CONTRACT["right_min_px"]
            <= right_preferred
            <= FIXED_LAYOUT_CONTRACT["right_max_px"]
        ):
            raise RuntimeQualificationError("workspace_preferred_pair_missing", operation)
        preferred_pair = {
            "left_px": left_preferred,
            "right_px": right_preferred,
        }
        effective_pair = {
            f"{side}_px": (
                round(float(geometry[side]["width"]))
                if isinstance(geometry[side], dict)
                else preferred_pair[f"{side}_px"]
            )
            for side in ("left", "right")
        }
        preferred_target = _required_target(
            target_by_id, "preferred-widths", f"{operation} preferred widths"
        )
        expected_preferred_name = (
            f"선호 패널 너비 좌측 {left_preferred}픽셀, "
            f"우측 {right_preferred}픽셀"
        )
        if preferred_target.get("name") != expected_preferred_name:
            raise RuntimeQualificationError(
                "workspace_preferred_pair_semantic_mismatch", operation
            )
        self.preferred_pair = preferred_pair

        separator_state: dict[str, object] = {}
        for side in ("left", "right"):
            separator = target_by_id.get(f"{side}-divider")
            if separator is None:
                separator_state[side] = None
                continue
            separator_state[side] = {
                "role": separator["role"],
                "name": separator["name"],
                "focusable": separator["focusable"],
                "disabled": not bool(separator["enabled"]),
                "focused": separator.get("focused"),
                "orientation": separator.get("orientation"),
                "current_value": separator.get("current_value"),
                "minimum_value": separator.get("minimum_value"),
                "maximum_value": separator.get("maximum_value"),
                "value_description": separator.get("value_description"),
                "active_state": separator.get("active_state"),
            }

        graph_body = target_by_id.get("graph-body")
        graph_viewport = target_by_id.get("graph-viewport")
        graph_zoom = target_by_id.get("graph-zoom")
        matrix = target_by_id.get("matrix")
        graph_layout_geometry: dict[str, object] | None = None
        if graph_viewport is not None and matrix is not None:
            graph_layout_geometry = {
                "viewport": dict(graph_viewport["frame"]),
                "center": dict(geometry["center"]),
                "matrix": dict(matrix["frame"]),
            }

        semantic_graph: dict[str, object] | None = None
        if raw.get("semantic_graph") is not None:
            semantic_graph = self._normalise_semantic_graph(
                raw.get("semantic_graph"),
                f"{operation} semantic graph",
            )
        fingerprint_disclosures: dict[str, bool | None] | None = None
        raw_stability_token = raw.get("stability_token")
        if isinstance(raw_stability_token, dict):
            if isinstance(semantic_graph, dict) and (
                raw_stability_token.get("projection_id")
                != semantic_graph.get("projection_id")
                or raw_stability_token.get("settled_node_positions_hash")
                != semantic_graph.get("settled_node_positions_hash")
            ):
                raise RuntimeQualificationError(
                    "workspace_scene_layout_identity_mismatch", operation
                )
            raw_disclosures = raw_stability_token.get("disclosures")
            if raw_disclosures is not None:
                expected_disclosures = {
                    "component_map",
                    "profile_matrix",
                    "left_pane",
                    "right_pane",
                }
                if (
                    not isinstance(raw_disclosures, dict)
                    or set(raw_disclosures) != expected_disclosures
                    or any(
                        value is not None and not isinstance(value, bool)
                        for value in raw_disclosures.values()
                    )
                ):
                    raise RuntimeQualificationError(
                        "workspace_scene_disclosures_invalid", operation
                    )
                fingerprint_disclosures = {
                    key: raw_disclosures[key]
                    for key in sorted(expected_disclosures)
                }

        graph_tooltip_identity: dict[str, object] | None = None
        raw_tooltip = raw.get("graph_tooltip_identity")
        tooltip_target = target_by_id.get("graph-tooltip")
        if raw_tooltip is None and tooltip_target is not None:
            tooltip_name = tooltip_target.get("name")
            matching_component_ids = [
                component_id
                for component_id in _CANONICAL_ANCHOR_COMPONENT_IDS
                if isinstance(tooltip_name, str) and component_id in tooltip_name
            ]
            if len(matching_component_ids) == 1 and isinstance(tooltip_name, str):
                component_id = matching_component_ids[0]
                title = tooltip_name.split(component_id, 1)[0].strip(" ·—-")
                raw_tooltip = {
                    "component_id": component_id,
                    "title": title,
                    "visible": tooltip_target.get("visible"),
                    "frame": tooltip_target.get("frame"),
                }
        if raw_tooltip is not None:
            if not isinstance(raw_tooltip, dict) or tooltip_target is None:
                raise RuntimeQualificationError(
                    "workspace_graph_tooltip_semantic_invalid", operation
                )
            component_id = raw_tooltip.get("component_id")
            title = raw_tooltip.get("title")
            visible = raw_tooltip.get("visible")
            tooltip_frame = _frame(
                raw_tooltip.get("frame"), f"{operation} graph tooltip"
            )
            target_frame = _frame(
                tooltip_target.get("frame"), f"{operation} graph tooltip target"
            )
            tooltip_name = tooltip_target.get("name")
            if (
                component_id not in _CANONICAL_ANCHOR_COMPONENT_IDS
                or not isinstance(title, str)
                or not title
                or len(title) > 256
                or visible is not True
                or tooltip_target.get("role") not in {"AXStaticText", "AXGroup"}
                or tooltip_target.get("visible") is not True
                or not isinstance(tooltip_name, str)
                or title not in tooltip_name
                or str(component_id) not in tooltip_name
                or any(
                    not _same_within(tooltip_frame[key], target_frame[key])
                    for key in ("x", "y", "width", "height")
                )
            ):
                raise RuntimeQualificationError(
                    "workspace_graph_tooltip_semantic_invalid", operation
                )
            graph_tooltip_identity = {
                "component_id": component_id,
                "title": title,
                "visible": True,
                "frame": tooltip_frame,
            }
        semantic_components = (
            semantic_graph.get("components")
            if isinstance(semantic_graph, dict)
            and isinstance(semantic_graph.get("components"), list)
            else []
        )
        selected_components = [
            component
            for component in semantic_components
            if isinstance(component, dict) and component.get("selected") is True
        ]
        if len(selected_components) > 1:
            raise RuntimeQualificationError(
                "workspace_semantic_graph_selection_ambiguous", operation
            )
        selected = (
            selected_components[0].get("component_id")
            if selected_components
            else raw.get("selected_component_id")
        )
        if isinstance(selected, str):
            selected = self.anchor_aliases.get(selected, selected)
        raw_matrix_layout = raw.get("matrix_layout")
        right_detail = (
            raw_matrix_layout.get("right_detail_id")
            if isinstance(raw_matrix_layout, dict)
            else raw.get("right_detail_id")
        )
        if isinstance(right_detail, str):
            right_detail = self.anchor_aliases.get(right_detail, right_detail)
        else:
            right_detail = None
        active_profile = (
            raw_matrix_layout.get("active_profile_id")
            if isinstance(raw_matrix_layout, dict)
            else None
        )
        if not isinstance(active_profile, str) and isinstance(
            semantic_graph, dict
        ):
            active_profile = semantic_graph.get("active_profile_id")
        if not isinstance(active_profile, str):
            active_profile = raw.get("active_profile_id")
        if active_profile == _CANONICAL_PROFILE_ID:
            active_profile = _PROFILE_ID
        if not isinstance(active_profile, str):
            active_profile = next(
                (
                    token.removeprefix("profile:")
                    for token, target in target_by_id.items()
                    if token.startswith("profile:") and target.get("selected") is True
                ),
                None,
            )
        anchor_nodes: list[dict[str, object]] = []
        graph_origin = graph_viewport.get("frame") if graph_viewport else None
        if isinstance(graph_origin, dict):
            component_by_id = {
                str(component.get("component_id")): component
                for component in semantic_components
                if isinstance(component, dict)
            }
            for canonical_id, logical_id in zip(
                self.actual_anchor_ids, FIXTURE_ANCHOR_COMPONENT_IDS, strict=True
            ):
                node = component_by_id.get(canonical_id)
                node_frame = node.get("frame") if isinstance(node, dict) else None
                if isinstance(node_frame, dict):
                    node_frame = _frame(node_frame, operation)
                if isinstance(node_frame, dict):
                    anchor_nodes.append(
                        {
                            "component_id": logical_id,
                            "frame": {
                                "x": float(node_frame["x"]) - float(graph_origin["x"]),
                                "y": float(node_frame["y"]) - float(graph_origin["y"]),
                                "width": node_frame["width"],
                                "height": node_frame["height"],
                            },
                        }
                    )
        counters = self._command_counters()
        raw_scale = raw.get("graph_scale")
        if isinstance(raw_scale, str):
            try:
                raw_scale = float(raw_scale.split("×", 1)[0].strip())
            except ValueError:
                raw_scale = None
        graph_scale = (
            float(raw_scale)
            if isinstance(raw_scale, (int, float))
            and not isinstance(raw_scale, bool)
            and math.isfinite(float(raw_scale))
            else None
        )
        semantic_camera = (
            semantic_graph.get("camera")
            if isinstance(semantic_graph, dict)
            else None
        )
        semantic_lod: str | None = None
        semantic_camera_pose: dict[str, object] | None = None
        if isinstance(semantic_camera, dict):
            position = semantic_camera.get("position")
            target = semantic_camera.get("target")
            if isinstance(position, dict) and isinstance(target, dict):
                semantic_camera_pose = {
                    "position": dict(position),
                    "target": dict(target),
                }
            if semantic_camera.get("ready") is True:
                semantic_scale = _finite_number(
                    semantic_camera.get("scale"), operation
                )
                if graph_scale is not None and not _same_within(
                    graph_scale, semantic_scale, tolerance=0.001
                ):
                    raise RuntimeQualificationError(
                        "workspace_graph_camera_scale_mismatch", operation
                    )
                graph_scale = semantic_scale
                semantic_lod = {
                    "개요": "overview",
                    "상세": "detail",
                    "최대": "detail",
                }.get(str(semantic_camera.get("level")))
            else:
                graph_scale = None
                semantic_lod = "unavailable"
        selected_relations = (
            [
                relation
                for relation in semantic_graph.get("relations", [])
                if isinstance(relation, dict) and relation.get("selected") is True
            ]
            if isinstance(semantic_graph, dict)
            else []
        )
        selected_steps = (
            [
                step
                for step in semantic_graph.get("workflow_steps", [])
                if isinstance(step, dict) and step.get("selected") is True
            ]
            if isinstance(semantic_graph, dict)
            else []
        )
        selected_relation = (
            selected_relations[0] if len(selected_relations) == 1 else None
        )
        graph_fingerprint = {
            "projection_id": (
                semantic_graph.get("projection_id")
                if isinstance(semantic_graph, dict)
                else None
            ),
            "settled_node_positions_hash": (
                semantic_graph.get("settled_node_positions_hash")
                if isinstance(semantic_graph, dict)
                else None
            ),
            "selected_component_id": selected,
            "right_detail_id": right_detail,
            "active_profile_id": active_profile,
            "selected_workflow_id": (
                selected_relation.get("canonical_id")
                if isinstance(selected_relation, dict)
                and selected_relation.get("relation_kind") == "workflow"
                else None
            ),
            "selected_relation_node_id": (
                selected_relation.get("node_id")
                if isinstance(selected_relation, dict)
                else None
            ),
            "locked_step": (
                {
                    "workflow_id": selected_steps[0].get("workflow_id"),
                    "ordinal": selected_steps[0].get("ordinal"),
                }
                if len(selected_steps) == 1
                else None
            ),
            "camera_pose": semantic_camera_pose,
            "scale": graph_scale,
            "anchor_nodes": anchor_nodes,
            "disclosures": fingerprint_disclosures,
            **counters,
            "graph_body_identity": raw.get("graph_body_identity"),
        }
        dashboard = "local" if target_by_id.get("dashboard-local", {}).get("selected") else "sot"
        appearance_mode = raw.get("appearance_mode")
        if appearance_mode is not None and appearance_mode not in {
            "system", "light", "dark"
        }:
            raise RuntimeQualificationError(
                "workspace_appearance_semantic_invalid", operation
            )
        snapshot: dict[str, object] = {
            "identity": {"pid": pid, "window_id": window_id},
            "layout_mode": layout_mode,
            "preferred_pair": dict(self.preferred_pair),
            "effective_pair": effective_pair,
            "geometry": geometry,
            "physical_geometry": physical_geometry,
            "separator_state": separator_state,
            "dashboard": dashboard,
            "appearance_mode": appearance_mode,
            "graph_fingerprint": graph_fingerprint,
            "graph_layout_geometry": graph_layout_geometry,
            "graph_tooltip_identity": graph_tooltip_identity,
            "graph_body_ax_present": graph_body is not None,
            "zoom_control_focusable": graph_zoom is not None and graph_zoom.get("focusable") is True,
            "matrix_usable_height": (
                float(matrix["frame"]["height"])
                if matrix is not None and isinstance(matrix.get("frame"), dict)
                else 0
            ),
            "layout_revision": raw.get("layout_revision"),
            "persisted": raw.get("persisted"),
            "diagnostic": raw.get("diagnostic") if isinstance(raw.get("diagnostic"), dict) else None,
            "targets": targets,
        }
        if semantic_graph is not None:
            snapshot["semantic_graph"] = semantic_graph
        graph_lod = raw.get("graph_lod")
        if graph_lod is not None:
            if graph_lod not in {"overview", "select", "detail", "unavailable"}:
                raise RuntimeQualificationError(
                    "workspace_graph_lod_invalid", operation
                )
            if semantic_lod is not None and graph_lod not in {
                semantic_lod,
                "select" if semantic_lod == "detail" else semantic_lod,
            }:
                raise RuntimeQualificationError(
                    "workspace_graph_camera_lod_mismatch", operation
                )
        if semantic_lod is not None:
            snapshot["graph_lod"] = semantic_lod
        elif graph_lod is not None:
            snapshot["graph_lod"] = graph_lod
        if raw.get("matrix_usable_height") is not None:
            raw_matrix_usable_height = _finite_number(
                raw.get("matrix_usable_height"), operation
            )
            if raw_matrix_usable_height < 0:
                raise RuntimeQualificationError(
                    "workspace_matrix_observation_invalid", operation
                )
            snapshot["matrix_usable_height"] = raw_matrix_usable_height
        if raw.get("matrix_layout") is not None:
            snapshot["matrix_layout"] = self._normalise_matrix_layout(
                raw.get("matrix_layout"), f"{operation} matrix layout"
            )
            matrix_layout = snapshot["matrix_layout"]
            assert isinstance(matrix_layout, dict)
            matrix_body_frame = matrix_layout.get("matrix_body_frame")
            if (
                raw.get("matrix_usable_height") is None
                and isinstance(matrix_body_frame, dict)
            ):
                body = _frame(matrix_body_frame, operation)
                center = _frame(geometry["center"], operation)
                visible_top = max(body["y"], center["y"])
                visible_bottom = min(
                    body["y"] + body["height"],
                    center["y"] + center["height"],
                )
                snapshot["matrix_usable_height"] = max(
                    0.0, visible_bottom - visible_top
                )
        if raw.get("matrix_wrap_sample") is not None:
            snapshot["matrix_wrap_sample"] = self._normalise_matrix_wrap_sample(
                raw.get("matrix_wrap_sample"), f"{operation} matrix wrap"
            )
        if raw.get("collapsed_workbench_layout") is not None:
            snapshot["collapsed_workbench_layout"] = (
                self._normalise_collapsed_workbench_layout(
                    raw.get("collapsed_workbench_layout"),
                    f"{operation} collapsed workbench layout",
                )
            )
        for semantic_key in ("profile_supernode", "graph_toolbar", "sot_tree"):
            semantic_value = raw.get(semantic_key)
            if semantic_value is not None:
                if not isinstance(semantic_value, dict):
                    raise RuntimeQualificationError(
                        "collector_output_invalid", f"{operation} {semantic_key}"
                    )
                snapshot[semantic_key] = json.loads(
                    json.dumps(semantic_value, ensure_ascii=False)
                )
        stability_token = raw.get("stability_token")
        if stability_token is not None:
            if not isinstance(stability_token, dict):
                raise RuntimeQualificationError(
                    "workspace_steady_snapshot_invalid", operation
                )
            snapshot["stability_token"] = json.loads(
                json.dumps(stability_token, ensure_ascii=False)
            )
        return snapshot

    @staticmethod
    def _steady_token(
        observation: object,
        operation: str,
    ) -> tuple[dict[str, int], dict[str, object]]:
        if not isinstance(observation, dict):
            raise RuntimeQualificationError("workspace_steady_snapshot_invalid", operation)
        identity = observation.get("identity")
        token = observation.get("stability_token")
        if (
            not isinstance(identity, dict)
            or not isinstance(identity.get("pid"), int)
            or isinstance(identity.get("pid"), bool)
            or not isinstance(identity.get("window_id"), int)
            or isinstance(identity.get("window_id"), bool)
            or not isinstance(token, dict)
        ):
            raise RuntimeQualificationError("workspace_steady_snapshot_invalid", operation)
        return (
            {"pid": int(identity["pid"]), "window_id": int(identity["window_id"])},
            json.loads(json.dumps(token, ensure_ascii=False)),
        )

    def _observe_steady_token(
        self,
        pid: int,
        operation: str,
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, int], dict[str, object]]:
        raw = self._method("observe_workspace_steady")(
            pid,
            self.actual_anchor_ids,
            timeout_seconds=timeout_seconds,
        )
        identity, token = self._steady_token(raw, operation)
        self._validated_snapshot_identity(pid, {"identity": identity}, operation)
        return identity, token

    def _full_snapshot_after_steady_token(
        self,
        pid: int,
        operation: str,
        expected_token: dict[str, object],
        *,
        deadline: float,
        timeout_code: str,
    ) -> dict[str, object]:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise RuntimeQualificationError(timeout_code, operation)
        raw = self._method("observe_workspace")(
            pid,
            self.actual_anchor_ids,
            timeout_seconds=remaining_seconds,
        )
        if time.monotonic() >= deadline:
            raise RuntimeQualificationError(timeout_code, operation)
        current = self._normalise_observation(raw, operation)
        self._validated_snapshot_identity(pid, current, operation)
        _identity, full_token = self._steady_token(current, operation)
        if _canonical_json_bytes(full_token) != _canonical_json_bytes(expected_token):
            raise RuntimeQualificationError(
                "workspace_steady_full_snapshot_mismatch", operation
            )
        self.last_snapshot = current
        return current

    @staticmethod
    def _remaining_steady_seconds(
        deadline: float,
        operation: str,
        timeout_code: str,
    ) -> float:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise RuntimeQualificationError(timeout_code, operation)
        return remaining_seconds

    @staticmethod
    def _steady_diagnostic(
        *,
        attempt_count: int,
        snapshot_unavailable_count: int,
        steady_timeout_count: int,
        stable_snapshot_observed: bool,
        token: dict[str, object] | None,
        unstable_field_counts: Counter[str],
    ) -> dict[str, object]:
        anchor_nodes = token.get("anchor_nodes") if isinstance(token, dict) else None
        return {
            "attempt_count": attempt_count,
            "snapshot_unavailable_count": snapshot_unavailable_count,
            "steady_timeout_count": steady_timeout_count,
            "stable_snapshot_observed": stable_snapshot_observed,
            "anchor_node_count": (
                len(anchor_nodes) if isinstance(anchor_nodes, list) else None
            ),
            "unstable_field_counts": dict(sorted(unstable_field_counts.items())),
        }

    def _steady_renderer_ready(
        self,
        pid: int,
        operation: str,
        timeout_seconds: float,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        stable_count = 0
        signature: bytes | None = None
        last_token: dict[str, object] | None = None
        while time.monotonic() < deadline:
            try:
                _identity, current_token = self._observe_steady_token(
                    pid,
                    operation,
                    timeout_seconds=self._remaining_steady_seconds(
                        deadline,
                        operation,
                        "workspace_renderer_ready_timeout",
                    ),
                )
            except RuntimeQualificationError as error:
                if error.code not in {
                    "workspace_snapshot_unavailable",
                    "workspace_steady_snapshot_unavailable",
                    "collector_operation_timeout",
                }:
                    raise
                if error.code == "collector_operation_timeout":
                    break
                time.sleep(
                    FIXED_LAYOUT_CONTRACT["steady_sample_interval_ms"] / 1000
                )
                continue
            renderer = current_token.get("renderer")
            last_token = current_token
            if not isinstance(renderer, dict) or renderer.get("availability") != "ready":
                stable_count = 0
                signature = None
                time.sleep(
                    FIXED_LAYOUT_CONTRACT["steady_sample_interval_ms"] / 1000
                )
                continue
            current_signature = _canonical_json_bytes(current_token)
            if current_signature == signature:
                stable_count += 1
            else:
                signature = current_signature
                stable_count = 1
            if stable_count >= FIXED_LAYOUT_CONTRACT["steady_sample_count"]:
                return self._full_snapshot_after_steady_token(
                    pid,
                    operation,
                    current_token,
                    deadline=deadline,
                    timeout_code="workspace_renderer_ready_timeout",
                )
            time.sleep(FIXED_LAYOUT_CONTRACT["steady_sample_interval_ms"] / 1000)
        if last_token is None:
            raise RuntimeQualificationError("workspace_observation_timeout", operation)
        raise RuntimeQualificationError("workspace_renderer_ready_timeout", operation)

    def _steady(
        self,
        pid: int,
        operation: str,
        timeout_seconds: float,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        stable_count = 0
        signature: bytes | None = None
        last: dict[str, object] | None = None
        previous_token: dict[str, object] | None = None
        attempt_count = 0
        snapshot_unavailable_count = 0
        unstable_field_counts: Counter[str] = Counter()
        while time.monotonic() < deadline:
            attempt_count += 1
            try:
                _identity, current_token = self._observe_steady_token(
                    pid,
                    operation,
                    timeout_seconds=self._remaining_steady_seconds(
                        deadline,
                        operation,
                        "workspace_steady_state_timeout",
                    ),
                )
            except RuntimeQualificationError as error:
                if error.code not in {
                    "workspace_snapshot_unavailable",
                    "workspace_steady_snapshot_unavailable",
                    "collector_operation_timeout",
                }:
                    raise
                snapshot_unavailable_count += 1
                if error.code == "collector_operation_timeout":
                    break
                time.sleep(
                    FIXED_LAYOUT_CONTRACT["steady_sample_interval_ms"] / 1000
                )
                continue
            if previous_token is not None:
                for key in set(previous_token) | set(current_token):
                    if _canonical_json_bytes(previous_token.get(key)) != _canonical_json_bytes(
                        current_token.get(key)
                    ):
                        unstable_field_counts[key] += 1
            current_signature = _canonical_json_bytes(current_token)
            if current_signature == signature:
                stable_count += 1
            else:
                signature = current_signature
                stable_count = 1
            last = current_token
            previous_token = current_token
            if stable_count >= FIXED_LAYOUT_CONTRACT["steady_sample_count"]:
                self.last_steady_diagnostic = self._steady_diagnostic(
                    attempt_count=attempt_count,
                    snapshot_unavailable_count=snapshot_unavailable_count,
                    steady_timeout_count=0,
                    stable_snapshot_observed=True,
                    token=current_token,
                    unstable_field_counts=unstable_field_counts,
                )
                return self._full_snapshot_after_steady_token(
                    pid,
                    operation,
                    current_token,
                    deadline=deadline,
                    timeout_code="workspace_steady_state_timeout",
                )
            time.sleep(FIXED_LAYOUT_CONTRACT["steady_sample_interval_ms"] / 1000)
        self.last_steady_diagnostic = self._steady_diagnostic(
            attempt_count=attempt_count,
            snapshot_unavailable_count=snapshot_unavailable_count,
            steady_timeout_count=1 if last is not None else 0,
            stable_snapshot_observed=False,
            token=last,
            unstable_field_counts=unstable_field_counts,
        )
        if last is None:
            raise RuntimeQualificationError("workspace_observation_timeout", operation)
        raise RuntimeQualificationError("workspace_steady_state_timeout", operation)

    def _press(self, pid: int, target: str) -> None:
        result = self._method("press_workspace_target")(pid, target)
        if result is not True and (
            not isinstance(result, dict) or result.get("accepted") is not True
        ):
            raise RuntimeQualificationError("workspace_action_rejected", target)

    def _click(self, pid: int, target: str) -> dict[str, object]:
        result = self._method("click_workspace_target")(pid, target)
        if not isinstance(result, dict) or result.get("accepted") is not True:
            raise RuntimeQualificationError("workspace_action_rejected", target)
        return dict(result)

    def _clear_checkout_path_for_capture(self, pid: int, operation: str) -> None:
        cleared = self._method("set_workspace_text")(
            pid,
            "checkout-path",
            "",
            timeout_seconds=self.readiness_timeout_seconds,
        )
        if cleared is not True and (
            not isinstance(cleared, dict) or cleared.get("accepted") is not True
        ):
            raise RuntimeQualificationError(
                "workspace_screenshot_redaction_failed", operation
            )

    def _drag(
        self,
        pid: int,
        side: str,
        delta_x: int,
        *,
        active_capture_case: str | None = None,
    ) -> dict[str, object] | None:
        target = f"{side}-divider"
        if active_capture_case is not None:
            if active_capture_case not in {"left-pointer-drag", "right-pointer-drag"}:
                raise RuntimeQualificationError(
                    "workspace_drag_capture_case_invalid", active_capture_case
                )
            self._clear_checkout_path_for_capture(pid, active_capture_case)
            destination = (
                self.evidence_dir / "cases" / f"{active_capture_case}.png"
            ).resolve()
            result = self._method("drag_capture_workspace_target")(
                pid, target, delta_x, destination
            )
            action = getattr(result, "action", None)
            screenshot = getattr(result, "screenshot", None)
            captured_destination = getattr(result, "destination", None)
            if (
                not isinstance(action, dict)
                or not isinstance(screenshot, ScreenshotCaptureEvidence)
                or not isinstance(captured_destination, Path)
                or captured_destination.resolve() != destination
                or not destination.is_file()
                or action.get("kind") != "pointer-drag"
                or action.get("target") != target
                or action.get("delta_x") != delta_x
                or action.get("captured_before_mouseup") is not True
            ):
                raise RuntimeQualificationError(
                    "workspace_drag_capture_invalid", active_capture_case
                )
            self.active_drag_captures[active_capture_case] = (
                destination,
                screenshot,
            )
            return dict(action)
        result = self._method("drag_workspace_target")(pid, target, delta_x)
        if result is not True and (
            not isinstance(result, dict) or result.get("accepted") is not True
        ):
            raise RuntimeQualificationError("workspace_action_rejected", f"{side} divider drag")
        return None

    def _drag_target(self, pid: int, target: str, delta_x: int) -> None:
        result = self._method("drag_workspace_target")(pid, target, delta_x)
        if result is not True and (
            not isinstance(result, dict) or result.get("accepted") is not True
        ):
            raise RuntimeQualificationError("workspace_action_rejected", target)

    def _hover_graph_target(
        self, pid: int, target: str, operation: str
    ) -> dict[str, object]:
        result = self._method("hover_workspace_target")(pid, target)
        readback = result.get("target_readback") if isinstance(result, dict) else None
        pointer = result.get("pointer_point") if isinstance(result, dict) else None
        try:
            frame = _frame(
                readback.get("frame") if isinstance(readback, dict) else None,
                operation,
            )
            pointer_x = _finite_number(
                pointer.get("x") if isinstance(pointer, dict) else None,
                operation,
            )
            pointer_y = _finite_number(
                pointer.get("y") if isinstance(pointer, dict) else None,
                operation,
            )
        except RuntimeQualificationError:
            raise RuntimeQualificationError(
                "workspace_graph_hover_target_mismatch", operation
            )
        expected_component_id = (
            target.removeprefix("semantic-component:")
            if target.startswith("semantic-component:")
            else None
        )
        if (
            not isinstance(result, dict)
            or result.get("accepted") is not True
            or result.get("kind") != "pointer-hover"
            or result.get("transport") != "cg-event-mouse-move"
            or result.get("pid") != pid
            or result.get("target") != target
            or not isinstance(readback, dict)
            or readback.get("target") != target
            or readback.get("role") != "AXButton"
            or readback.get("visible") is not True
            or frame["width"] <= 0
            or frame["height"] <= 0
            or not frame["x"] <= pointer_x <= frame["x"] + frame["width"]
            or not frame["y"] <= pointer_y <= frame["y"] + frame["height"]
        ):
            raise RuntimeQualificationError(
                "workspace_graph_hover_target_mismatch", operation
            )
        if expected_component_id is not None and readback.get(
            "component_id"
        ) != expected_component_id:
            raise RuntimeQualificationError(
                "workspace_graph_hover_target_mismatch", operation
            )
        observation = {
            "target": target,
            "role": readback.get("role"),
            "name": readback.get("name"),
            "visible": True,
            "frame": frame,
            "pointer_point": {"x": pointer_x, "y": pointer_y},
        }
        if expected_component_id is not None:
            observation["component_id"] = expected_component_id
        return observation

    def _leave_graph_hover(self, pid: int, operation: str) -> None:
        result = self._method("hover_workspace_target")(pid, "graph-viewport")
        if (
            not isinstance(result, dict)
            or result.get("accepted") is not True
            or result.get("kind") != "pointer-hover"
            or result.get("transport") != "cg-event-mouse-move"
            or result.get("pid") != pid
            or result.get("target") != "graph-viewport"
        ):
            raise RuntimeQualificationError(
                "workspace_graph_hover_leave_failed", operation
            )

    def _hover_profile_target(
        self, pid: int, target: str, operation: str
    ) -> dict[str, object]:
        result = self._method("hover_workspace_target")(pid, target)
        readback = result.get("target_readback") if isinstance(result, dict) else None
        pointer = result.get("pointer_point") if isinstance(result, dict) else None
        identity = (
            readback.get("profile_identity")
            if isinstance(readback, dict)
            else None
        )
        try:
            frame = _frame(
                readback.get("frame") if isinstance(readback, dict) else None,
                operation,
            )
            pointer_x = _finite_number(
                pointer.get("x") if isinstance(pointer, dict) else None,
                operation,
            )
            pointer_y = _finite_number(
                pointer.get("y") if isinstance(pointer, dict) else None,
                operation,
            )
        except RuntimeQualificationError:
            raise RuntimeQualificationError(
                "profile_supernode_contract_mismatch", operation
            )
        if (
            not isinstance(result, dict)
            or result.get("accepted") is not True
            or result.get("kind") != "pointer-hover"
            or result.get("transport") != "cg-event-mouse-move"
            or result.get("pid") != pid
            or result.get("target") != target
            or not isinstance(readback, dict)
            or readback.get("target") != target
            or readback.get("role") not in {"AXButton", "AXGroup"}
            or readback.get("visible") is not True
            or not isinstance(identity, dict)
            or identity.get("node_id") != target.removeprefix("profile:")
            or identity.get("type") != "Profile"
            or not isinstance(identity.get("name"), str)
            or not identity.get("name")
            or not isinstance(identity.get("member_count"), int)
            or not isinstance(identity.get("active"), bool)
            or not frame["x"] <= pointer_x <= frame["x"] + frame["width"]
            or not frame["y"] <= pointer_y <= frame["y"] + frame["height"]
        ):
            raise RuntimeQualificationError(
                "profile_supernode_contract_mismatch", operation
            )
        return {
            **identity,
            "visible": True,
            "frame": frame,
        }

    def _tree_observation(
        self, snapshot: dict[str, object], operation: str
    ) -> dict[str, object]:
        tree = snapshot.get("sot_tree")
        if not isinstance(tree, dict):
            raise RuntimeQualificationError(
                "required_ax_semantic_missing", f"{operation} SoT tree"
            )
        return tree

    def _tree_offset(self, snapshot: dict[str, object], operation: str) -> float:
        tree = self._tree_observation(snapshot, operation)
        return _finite_number(tree.get("scroll_top_px"), operation)

    def _wheel_tree_to_last(self, pid: int, operation: str) -> dict[str, object]:
        result = self._method("wheel_workspace_target_evidence")(
            pid, "sot-tree-scroll", -4096
        )
        if (
            not isinstance(result, dict)
            or result.get("accepted") is not True
            or result.get("kind") != "tree-scroll"
            or result.get("transport") != "cg-event-wheel"
            or result.get("target") != "sot-tree-scroll"
        ):
            raise RuntimeQualificationError(
                "sot_tree_wheel_contract_mismatch", operation
            )
        return result

    def _wheel_workbench(
        self, pid: int, operation: str, delta_y: int = -480
    ) -> dict[str, object]:
        result = self._method("wheel_workspace_target_evidence")(
            pid, "center-pane", delta_y
        )
        if (
            not isinstance(result, dict)
            or result.get("accepted") is not True
            or result.get("kind") != "workbench-scroll"
            or result.get("transport") != "cg-event-wheel"
            or result.get("target") != "center-pane"
            or result.get("delta_y") != delta_y
        ):
            raise RuntimeQualificationError(
                "workspace_matrix_outer_scroll_contract_mismatch", operation
            )
        return result

    def _key_tree_to_last(self, pid: int, operation: str) -> dict[str, object]:
        result = self._method("key_workspace_tree_target_evidence")(
            pid, "sot-tree-first-component", "End"
        )
        if (
            not isinstance(result, dict)
            or result.get("accepted") is not True
            or result.get("kind") != "tree-scroll"
            or result.get("transport") != "cg-event-key-end"
            or result.get("target") != "sot-tree-first-component"
        ):
            raise RuntimeQualificationError(
                "sot_tree_keyboard_contract_mismatch", operation
            )
        return result

    def _set_sot_tree_filter(
        self, pid: int, value: str, timeout_seconds: float, operation: str
    ) -> None:
        result = self._method("set_workspace_text")(
            pid,
            "sot-tree-filter",
            value,
            timeout_seconds=timeout_seconds,
        )
        if result is not True and (
            not isinstance(result, dict) or result.get("accepted") is not True
        ):
            raise RuntimeQualificationError(
                "workspace_action_rejected", operation
            )

    def _key(self, pid: int, side: str, key: str) -> dict[str, object]:
        target = f"{side}-divider"
        result = self._method("key_workspace_target_evidence")(pid, target, key)
        if (
            not isinstance(result, dict)
            or result.get("accepted") is not True
            or result.get("kind") != "keyboard"
            or result.get("target") != target
            or result.get("key") != key
            or result.get("focused_readback") is not True
            or not isinstance(result.get("before"), dict)
            or not isinstance(result.get("after"), dict)
        ):
            raise RuntimeQualificationError("workspace_action_rejected", f"{side} divider key")
        return result

    def _perform_keyboard_step(
        self,
        pid: int,
        before: dict[str, object],
        side: str,
        key: str,
        expected_delta: int,
        deadline: float,
        operation: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeQualificationError("workspace_case_timeout", operation)
        before_pair = _pair(before, "effective_pair")
        key_evidence = self._key(pid, side, key)
        after = self._wait_publication_delta(
            pid, before.get("layout_revision"), remaining
        )
        after_pair = _pair(after, "effective_pair")
        opposite = "right" if side == "left" else "left"
        state = after.get("separator_state")
        focused = (
            state.get(side, {}).get("focused")
            if isinstance(state, dict) and isinstance(state.get(side), dict)
            else None
        )
        if (
            after_pair[f"{side}_px"] - before_pair[f"{side}_px"]
            != expected_delta
            or after_pair[f"{opposite}_px"] != before_pair[f"{opposite}_px"]
            or _pair(after, "preferred_pair") != after_pair
            or focused is not True
        ):
            raise RuntimeQualificationError(
                "workspace_keyboard_step_mismatch", operation
            )
        self.preferred_pair = dict(after_pair)
        return after, {
            "separator": side,
            "key": key,
            "delta_px": expected_delta,
            "before_value": before_pair[f"{side}_px"],
            "after_value": after_pair[f"{side}_px"],
            "opposite_before": before_pair[f"{opposite}_px"],
            "opposite_after": after_pair[f"{opposite}_px"],
            "focused": True,
            "ax_value_before": key_evidence["before"].get("current_value"),
            "ax_value_after": key_evidence["after"].get("current_value"),
            "focused_readback": key_evidence.get("focused_readback"),
        }

    def _perform_boundary_subcase(
        self,
        pid: int,
        before: dict[str, object],
        *,
        side: str,
        edge: str,
        drag_delta_px: int,
        clamp_key: str,
        expected_value: int,
        deadline: float,
        operation: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeQualificationError("workspace_case_timeout", operation)
        before_pair = _pair(before, "effective_pair")
        self._drag(pid, side, drag_delta_px)
        after = self._wait_publication_delta(
            pid, before.get("layout_revision"), remaining
        )
        after_pair = _pair(after, "effective_pair")
        opposite = "right" if side == "left" else "left"
        if (
            after_pair[f"{side}_px"] != expected_value
            or after_pair[f"{opposite}_px"] != before_pair[f"{opposite}_px"]
            or _pair(after, "preferred_pair") != after_pair
        ):
            raise RuntimeQualificationError(
                "workspace_minimum_clamp_mismatch"
                if edge == "minimum"
                else "workspace_maximum_clamp_mismatch",
                operation,
            )
        self.preferred_pair = dict(after_pair)
        clamp_revision = after.get("layout_revision")
        if not isinstance(clamp_revision, int) or isinstance(clamp_revision, bool):
            raise RuntimeQualificationError(
                "workspace_layout_revision_missing", operation
            )
        clamp_evidence = self._key(pid, side, clamp_key)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeQualificationError("workspace_case_timeout", operation)
        clamped = self._wait_layout_revision(
            pid,
            expected_revision=clamp_revision,
            timeout_seconds=remaining,
            operation=f"{operation} keyboard clamp",
        )
        if (
            _pair(clamped, "effective_pair") != after_pair
            or _pair(clamped, "preferred_pair") != after_pair
            or clamped.get("layout_revision") != clamp_revision
        ):
            raise RuntimeQualificationError(
                "workspace_keyboard_clamp_mismatch", operation
            )
        return clamped, {
            "separator": side,
            "edge": edge,
            "drag_delta_px": drag_delta_px,
            "expected_value": expected_value,
            "pointer_publication_delta": 1,
            "opposite_before": before_pair[f"{opposite}_px"],
            "opposite_after": after_pair[f"{opposite}_px"],
            "keyboard_clamp": {
                "key": clamp_key,
                "before_value": after_pair[f"{side}_px"],
                "after_value": _pair(clamped, "effective_pair")[f"{side}_px"],
                "publication_delta": 0,
                "focused_readback": clamp_evidence.get("focused_readback"),
                "ax_value_before": clamp_evidence["before"].get("current_value"),
                "ax_value_after": clamp_evidence["after"].get("current_value"),
            },
        }

    def _resize(self, pid: int, width: int, height: int) -> None:
        result = self._method("resize_workspace_window")(pid, width, height)
        if result is not True and (
            not isinstance(result, dict) or result.get("accepted") is not True
        ):
            raise RuntimeQualificationError("workspace_action_rejected", "window resize")

    def _cancel_native_checkout(
        self, pid: int, timeout_seconds: float
    ) -> dict[str, object]:
        panel = self._method("observe_native_checkout_picker")(pid, timeout_seconds)
        if (
            not _native_picker_panel_proof_valid(panel, expected_pid=pid)
        ):
            raise RuntimeQualificationError(
                "workspace_picker_observation_proof_invalid",
                "native checkout picker",
            )
        script = r'''
on run argv
    set targetPID to item 1 of argv as integer
    tell application "System Events"
        set targetProcess to first application process whose unix id is targetPID
        set frontmost of targetProcess to true
        delay 0.8
        key code 53
        return "cancel-sequence-invoked"
    end tell
end run
'''
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script, "--", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeQualificationError("workspace_picker_timeout", "native checkout picker")
        if result.returncode == 0 and result.stdout.decode(
            "utf-8", "replace"
        ).strip() == "cancel-sequence-invoked":
            panel_identifier = panel.get("panel_identifier")
            if not isinstance(panel_identifier, str):
                raise RuntimeQualificationError(
                    "workspace_picker_observation_proof_invalid",
                    "native checkout picker",
                )
            closure = self._method("wait_native_checkout_picker_closed")(
                pid,
                panel_identifier,
                timeout_seconds,
            )
            if not _native_picker_closed_proof_valid(
                closure,
                expected_pid=pid,
                expected_identifier=panel_identifier,
            ):
                raise RuntimeQualificationError(
                    "workspace_picker_closed_proof_invalid",
                    "native checkout picker",
                )
            return {
                "empty_path_triggered": True,
                "native_panel_open_confirmed": True,
                "cancel_sequence_invoked": True,
                "cancel_panel_closed_confirmed": True,
                "directory_selected": False,
                "automation_principal": "System Events",
                "selection_surface": "native_directory_picker",
                "panel": panel,
                "cancel_panel_closure": closure,
            }
        error = result.stderr.decode("utf-8", "replace")
        if "-1743" in error or "Not authorized to send Apple events" in error:
            raise RuntimeQualificationError(
                "system_events_automation_denied",
                "native checkout picker",
                blocked=True,
            )
        raise RuntimeQualificationError("workspace_picker_action_failed", "native checkout picker")

    def prepare_picker_termination(
        self, pid: int, timeout_seconds: float
    ) -> dict[str, object]:
        pid = self._resolve_pid(pid)
        result = self._method("set_workspace_text")(
            pid,
            "checkout-path",
            "",
            timeout_seconds=timeout_seconds,
        )
        if result is not True and (
            not isinstance(result, dict) or result.get("accepted") is not True
        ):
            raise RuntimeQualificationError(
                "workspace_action_rejected", "picker-open termination path"
            )
        self._press(pid, "label:HarnessKit 연결")
        panel = self._method("observe_native_checkout_picker")(pid, timeout_seconds)
        if (
            not _native_picker_panel_proof_valid(panel, expected_pid=pid)
        ):
            raise RuntimeQualificationError(
                "workspace_picker_observation_proof_invalid",
                "picker-open normal termination",
            )
        return {
            "empty_path_triggered": True,
            "native_panel_open_confirmed": True,
            "panel": panel,
        }

    def load_fixture_after_picker_cancel(
        self,
        pid: int,
        checkout: Path,
        anchor_component_ids: tuple[str, ...],
        timeout_seconds: float,
    ) -> dict[str, object]:
        pid = self._resolve_pid(pid)
        if anchor_component_ids != FIXTURE_ANCHOR_COMPONENT_IDS:
            raise RuntimeQualificationError("fixture_anchor_identity_mismatch", "fixture load")
        manifest = json.loads((checkout / "fixture-manifest.json").read_text(encoding="utf-8"))
        canonical = tuple(
            component.get("canonical_component_id")
            for component in manifest.get("components", [])
            if isinstance(component, dict)
        )
        if canonical != _CANONICAL_ANCHOR_COMPONENT_IDS:
            raise RuntimeQualificationError("fixture_anchor_identity_mismatch", "fixture load")
        self.actual_anchor_ids = canonical
        set_text = self._method("set_workspace_text")
        result = set_text(
            pid,
            "checkout-path",
            "",
            timeout_seconds=timeout_seconds,
        )
        if result is not True and (
            not isinstance(result, dict) or result.get("accepted") is not True
        ):
            raise RuntimeQualificationError("workspace_action_rejected", "checkout picker path")
        self._press(pid, "label:HarnessKit 연결")
        picker_evidence = self._cancel_native_checkout(pid, timeout_seconds)
        cancel_deadline = time.monotonic() + timeout_seconds
        cancel_confirmed = False
        cancel_diagnostic: dict[str, object] = {
            "attempt_count": 0,
            "target_accepted": False,
            "target_readback_present": False,
            "target_focused": None,
            "value_equals_accepted": False,
        }
        while time.monotonic() < cancel_deadline:
            cancel_target = self.base._helper_json(
                "workspace-value-target",
                BUNDLE_IDENTIFIER,
                str(pid),
                "checkout-path",
            )
            cancel_readback = cancel_target.get("target_readback")
            cancel_value = self.base._helper_json(
                "workspace-value-equals",
                BUNDLE_IDENTIFIER,
                str(pid),
                "checkout-path",
                "",
            )
            target_focused = (
                cancel_readback.get("focused")
                if isinstance(cancel_readback, dict)
                and isinstance(cancel_readback.get("focused"), bool)
                else None
            )
            cancel_diagnostic = {
                "attempt_count": int(cancel_diagnostic["attempt_count"]) + 1,
                "target_accepted": cancel_target.get("accepted") is True,
                "target_readback_present": isinstance(cancel_readback, dict),
                "target_focused": target_focused,
                "value_equals_accepted": cancel_value.get("accepted") is True,
            }
            if (
                cancel_target.get("accepted") is True
                and isinstance(cancel_readback, dict)
                and cancel_readback.get("focused") is True
                and cancel_value.get("accepted") is True
            ):
                cancel_confirmed = True
                break
            time.sleep(0.1)
        if not cancel_confirmed:
            raise RuntimeQualificationError(
                "workspace_picker_cancel_unconfirmed",
                "native checkout picker",
                evidence=cancel_diagnostic,
            )
        picker_evidence.update(
            {
                "cancel_input_unchanged": True,
                "cancel_focus_restored": True,
                "checkout_mutation_count": 0,
            }
        )
        self._press(pid, "label:HarnessKit 연결")
        selection_panel = self._method("observe_native_checkout_picker")(
            pid, timeout_seconds
        )
        if not _native_picker_panel_proof_valid(selection_panel, expected_pid=pid):
            raise RuntimeQualificationError(
                "workspace_picker_observation_proof_invalid",
                "native checkout picker selection",
            )
        picker_selection = self._method("select_native_checkout_directory")(
            pid,
            checkout,
            timeout_seconds,
        )
        if (
            not _native_picker_selection_proof_valid(
                picker_selection, expected_pid=pid
            )
        ):
            raise RuntimeQualificationError(
                "workspace_picker_selection_proof_invalid",
                "native checkout picker",
            )
        deadline = time.monotonic() + timeout_seconds
        completion_evidence = self._wait_for_sot_snapshot_completion(deadline)
        fixture_diagnostic: dict[str, object] = {
            "picker_selection_confirmed": True,
            "sot_completion_observed": completion_evidence[
                "sot_completion_observed"
            ],
            "sot_completion_attempt_count": completion_evidence["attempt_count"],
            "attempt_count": 0,
            "snapshot_unavailable_count": 0,
            "steady_timeout_count": 0,
            "stable_snapshot_observed": False,
            "anchor_node_count": None,
            "sot_load_count": 0,
            "local_scan_start_count": 0,
            "unstable_field_counts": {},
        }
        if completion_evidence["sot_completion_observed"] is not True:
            fixture_diagnostic.update(
                {
                    "sot_load_count": completion_evidence["sot_request_count"],
                    "local_scan_start_count": completion_evidence[
                        "local_scan_start_count"
                    ],
                }
            )
            raise RuntimeQualificationError(
                "fixture_graph_not_ready",
                "fixture load",
                evidence=fixture_diagnostic,
            )
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining < 0.7:
                break
            fixture_diagnostic["attempt_count"] = max(
                1, int(fixture_diagnostic["attempt_count"])
            )
            try:
                snapshot = self._steady(pid, "fixture load", remaining)
            except RuntimeQualificationError as error:
                if error.code == "workspace_steady_state_timeout":
                    fixture_diagnostic.update(self.last_steady_diagnostic)
                    counters = self._command_counters()
                    fixture_diagnostic.update(counters)
                    break
                raise
            anchors = snapshot["graph_fingerprint"]["anchor_nodes"]  # type: ignore[index]
            counters = self._command_counters()
            steady_diagnostic = self.last_steady_diagnostic
            if int(steady_diagnostic.get("attempt_count", 0)) > 0:
                fixture_diagnostic.update(steady_diagnostic)
            else:
                fixture_diagnostic["stable_snapshot_observed"] = True
            fixture_diagnostic.update(
                {
                    "attempt_count": max(
                        1, int(fixture_diagnostic["attempt_count"])
                    ),
                    "anchor_node_count": len(anchors),
                    **counters,
                }
            )
            if len(anchors) == 3 and counters == {
                "sot_load_count": 1,
                "local_scan_start_count": 0,
            }:
                return {
                    **counters,
                    "picker_evidence": {
                        **picker_evidence,
                        "directory_selected": True,
                        "selection_panel": selection_panel,
                        "selection": picker_selection,
                        "fixture_loaded": True,
                        "fixture_registration_mode": "native_picker_after_cancel",
                        "confirmation_source": "fixture_graph_and_sot_load",
                    },
                }
            time.sleep(0.1)
        raise RuntimeQualificationError(
            "fixture_graph_not_ready",
            "fixture load",
            evidence=fixture_diagnostic,
        )

    def _identity(self, pid: int, snapshot: dict[str, object]) -> dict[str, object]:
        if self.artifact is None:
            raise RuntimeQualificationError("artifact_identity_missing", "observation")
        identity = self._validated_snapshot_identity(pid, snapshot, "observation")
        return {
            "bundle_identifier": BUNDLE_IDENTIFIER,
            "build_id": getattr(self.artifact, "build_id"),
            "executable_sha256": getattr(self.artifact, "executable_sha256"),
            "pid": identity["pid"],
            "window_id": identity["window_id"],
        }

    def _payload(
        self,
        pid: int,
        action: dict[str, object],
        before: dict[str, object] | None,
        after: dict[str, object],
        publication_delta: int,
    ) -> dict[str, object]:
        return {
            "proof_level": "macos-ax-cgevent-sck",
            "identity": self._identity(pid, after),
            "action": action,
            "before": before,
            "after": after,
            "publication_delta": publication_delta,
            "command_counters": self._command_counters(),
        }

    def _assert_separators(self, snapshot: dict[str, object], active: bool, operation: str) -> None:
        if (snapshot.get("layout_mode") == "three-pane") is not active:
            raise RuntimeQualificationError(
                "workspace_separator_state_mismatch", operation
            )
        _validate_separator_semantics(snapshot, operation)

    def _observe_workspace_once(
        self,
        pid: int,
        expected_layout: dict[str, object],
        deadline: float,
        operation: str,
    ) -> dict[str, object]:
        observe = self._method("observe_workspace")
        expected_revision = expected_layout.get("layout_revision")
        expected_preferred = _pair(expected_layout, "preferred_pair")
        expected_effective = _pair(expected_layout, "effective_pair")
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = observe(
                    pid,
                    self.actual_anchor_ids,
                    timeout_seconds=remaining,
                )
            except RuntimeQualificationError as error:
                if error.code != "workspace_snapshot_unavailable":
                    raise
                time.sleep(
                    FIXED_LAYOUT_CONTRACT["steady_sample_interval_ms"] / 1000
                )
                continue
            snapshot = self._normalise_observation(raw, operation)
            self._validated_snapshot_identity(pid, snapshot, operation)
            if time.monotonic() >= deadline:
                break
            if (
                snapshot.get("layout_revision") == expected_revision
                and _pair(snapshot, "preferred_pair") == expected_preferred
                and _pair(snapshot, "effective_pair") == expected_effective
                and isinstance(snapshot.get("semantic_graph"), dict)
            ):
                self.last_snapshot = snapshot
                return snapshot
            time.sleep(FIXED_LAYOUT_CONTRACT["steady_sample_interval_ms"] / 1000)
        raise RuntimeQualificationError("workspace_case_timeout", operation)

    def _wait_publication_delta(
        self,
        pid: int,
        before_revision: object,
        timeout_seconds: float,
    ) -> dict[str, object]:
        before = before_revision if isinstance(before_revision, int) else None
        return self._wait_layout_revision(
            pid,
            expected_revision=None if before is None else before + 1,
            timeout_seconds=timeout_seconds,
            operation="layout publication",
        )

    def _wait_layout_revision(
        self,
        pid: int,
        *,
        expected_revision: int | None,
        timeout_seconds: float,
        operation: str,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        observe = self._method("observe_workspace_layout")
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = observe(pid, timeout_seconds=remaining)
            except RuntimeQualificationError as error:
                if error.code != "workspace_layout_snapshot_unavailable":
                    raise
                time.sleep(
                    FIXED_LAYOUT_CONTRACT["steady_sample_interval_ms"] / 1000
                )
                continue
            snapshot = self._normalise_observation(raw, operation)
            self._validated_snapshot_identity(pid, snapshot, operation)
            revision = snapshot.get("layout_revision")
            if expected_revision is None or (
                isinstance(revision, int) and revision == expected_revision
            ):
                self.last_snapshot = snapshot
                return snapshot
            time.sleep(FIXED_LAYOUT_CONTRACT["steady_sample_interval_ms"] / 1000)
        raise RuntimeQualificationError(
            "workspace_layout_publication_unconfirmed", operation
        )

    def _wait_write_denied_outcome(
        self,
        pid: int,
        before_effective: dict[str, int],
        timeout_seconds: float,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            snapshot = self._steady(
                pid, "preference write denied", max(0.3, deadline - time.monotonic())
            )
            diagnostic = snapshot.get("diagnostic")
            if (
                snapshot.get("persisted") is False
                and isinstance(diagnostic, dict)
                and diagnostic.get("code") == "workspace_layout_preference_write_failed"
                and _pair(snapshot, "effective_pair") != before_effective
            ):
                return snapshot
            time.sleep(0.1)
        raise RuntimeQualificationError(
            "workspace_write_denied_outcome_unconfirmed", "preference write denied"
        )

    def _reset_pair_for_responsive_cases(self, pid: int, timeout_seconds: float) -> None:
        before = self._steady(pid, "responsive setup", timeout_seconds)
        effective = _pair(before, "effective_pair")
        self._drag(pid, "right", effective["right_px"] - 376)
        right = self._wait_publication_delta(
            pid, before.get("layout_revision"), timeout_seconds
        )
        self.preferred_pair = _pair(right, "preferred_pair")
        if (
            self.preferred_pair["right_px"] != 376
            or self.preferred_pair != _pair(right, "effective_pair")
        ):
            raise RuntimeQualificationError(
                "workspace_responsive_setup_failed", "responsive setup"
            )
        effective = _pair(right, "effective_pair")
        self._drag(pid, "left", 320 - effective["left_px"])
        left = self._wait_publication_delta(
            pid, right.get("layout_revision"), timeout_seconds
        )
        self.preferred_pair = _pair(left, "preferred_pair")
        if (
            self.preferred_pair != {"left_px": 320, "right_px": 376}
            or self.preferred_pair != _pair(left, "effective_pair")
        ):
            raise RuntimeQualificationError(
                "workspace_responsive_setup_failed", "responsive setup"
            )

    def _graph_readability_ax(
        self, snapshot: dict[str, object], operation: str
    ) -> dict[str, object]:
        semantic = snapshot.get("semantic_graph")
        layout = snapshot.get("graph_layout_geometry")
        if not isinstance(semantic, dict) or not isinstance(layout, dict):
            raise RuntimeQualificationError(
                "workspace_graph_readability_ax_missing", operation
            )
        components = semantic.get("components")
        relations = semantic.get("relations")
        if not isinstance(components, list) or not isinstance(relations, list):
            raise RuntimeQualificationError(
                "workspace_graph_readability_ax_missing", operation
            )
        semantic_identity = {
            "component_ids": sorted(
                str(component.get("component_id"))
                for component in components
                if isinstance(component, dict)
            ),
            "relation_ids": sorted(
                str(relation.get("node_id"))
                for relation in relations
                if isinstance(relation, dict)
            ),
        }
        semantic_sha256 = _sha256_bytes(
            _canonical_json_bytes(semantic_identity)
        )
        return {
            "snapshot_id_sha256": semantic_sha256,
            "relation_ids": semantic_identity["relation_ids"],
            "viewport_global_frame": _frame(layout.get("viewport"), operation),
            "component_nodes": [
                {
                    "component_id": component.get("component_id"),
                    "kind": component.get("kind"),
                    "selected": component.get("selected"),
                    "visible": component.get("visible"),
                }
                for component in components
                if isinstance(component, dict)
            ],
            "observed_kind_tokens": sorted(
                {
                    str(component.get("kind"))
                    for component in components
                    if isinstance(component, dict)
                }
            ),
            "visible_component_static_text_count": 0,
            "profile_identity_visible": any(
                isinstance(relation, dict)
                and relation.get("relation_kind") == "profile"
                for relation in relations
            ),
            "unprofiled_identity_visible": any(
                isinstance(relation, dict)
                and relation.get("canonical_id") == "unprofiled"
                for relation in relations
            ),
        }

    def _graph_readability_snapshot_identity(
        self, snapshot: dict[str, object], operation: str
    ) -> dict[str, object]:
        observation = self._graph_readability_ax(snapshot, operation)
        nodes = observation.get("component_nodes")
        graph = snapshot.get("graph_fingerprint")
        if not isinstance(nodes, list) or not isinstance(graph, dict):
            raise RuntimeQualificationError(
                "workspace_graph_readability_identity_invalid", operation
            )
        component_ids = sorted(
            str(node["component_id"])
            for node in nodes
            if isinstance(node, dict)
            and isinstance(node.get("component_id"), str)
        )
        observed_kinds = {
            str(node["kind"])
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("kind"), str)
        }
        relation_ids = observation.get("relation_ids")
        if (
            len(component_ids) != len(nodes)
            or len(component_ids) != len(set(component_ids))
            or not isinstance(relation_ids, list)
            or not relation_ids
            or relation_ids != sorted(set(relation_ids))
            or any(
                not isinstance(relation_id, str) or not relation_id
                for relation_id in relation_ids
            )
            or not observed_kinds
            or not observed_kinds.issubset(set(_CANONICAL_GRAPH_AX_KINDS))
        ):
            raise RuntimeQualificationError(
                "workspace_graph_readability_identity_invalid", operation
            )
        identity = {
            "component_ids": component_ids,
            "relation_ids": list(relation_ids),
            "kind_tokens": sorted(observed_kinds),
            "snapshot_id_sha256": observation.get("snapshot_id_sha256"),
            "active_profile_id": graph.get("active_profile_id"),
            "sot_load_count": graph.get("sot_load_count"),
            "local_scan_start_count": graph.get("local_scan_start_count"),
            "graph_body_identity": graph.get("graph_body_identity"),
        }
        if (
            not isinstance(identity["active_profile_id"], str)
            or not isinstance(identity["snapshot_id_sha256"], str)
            or len(identity["snapshot_id_sha256"]) != 64
            or identity["sot_load_count"] != 1
            or identity["local_scan_start_count"] != 0
            or identity["graph_body_identity"] != "component-map-body"
        ):
            raise RuntimeQualificationError(
                "workspace_graph_readability_identity_invalid", operation
            )
        return identity

    def _set_graph_appearance(
        self,
        pid: int,
        appearance: str,
        timeout_seconds: float,
        operation: str,
    ) -> dict[str, object]:
        if appearance not in {"light", "dark"}:
            raise RuntimeQualificationError(
                "workspace_graph_appearance_invalid", operation
            )
        self._press(pid, f"appearance-{appearance}")
        snapshot = self._steady(pid, f"{operation} appearance", timeout_seconds)
        if snapshot.get("appearance_mode") != appearance:
            raise RuntimeQualificationError(
                "workspace_graph_appearance_unconfirmed", operation
            )
        return snapshot

    def _reset_graph_fit(
        self, pid: int, timeout_seconds: float, operation: str
    ) -> dict[str, object]:
        self._press(pid, "graph-zoom-reset")
        snapshot = self._steady(pid, f"{operation} fit", timeout_seconds)
        scale = _finite_number(
            _case_mapping(snapshot, "graph_fingerprint", operation).get("scale"),
            operation,
        )
        semantic = _case_mapping(snapshot, "semantic_graph", operation)
        camera = _case_mapping(semantic, "camera", operation)
        if (
            not _same_within(scale, 1.0, tolerance=0.001)
            or camera.get("ready") is not True
            or not _same_within(
                _finite_number(camera.get("scale"), operation),
                1.0,
                tolerance=0.001,
            )
            or camera.get("level") != "개요"
            or camera.get("fit_enabled") is not True
            or camera.get("zoom_in_enabled") is not True
        ):
            raise RuntimeQualificationError(
                "workspace_graph_fit_scale_invalid", operation
            )
        return snapshot

    def _center_graph_component(
        self, snapshot: dict[str, object], operation: str
    ) -> dict[str, object]:
        observation = self._graph_readability_ax(snapshot, operation)
        nodes = observation.get("component_nodes")
        if not isinstance(nodes, list):
            raise RuntimeQualificationError(
                "workspace_graph_select_target_missing", operation
            )
        visible_nodes = [
            node
            for node in nodes
            if isinstance(node, dict) and node.get("visible") is True
        ]
        preferred = [node for node in visible_nodes if node.get("kind") == "agent"]
        candidates = preferred or visible_nodes
        if not candidates:
            raise RuntimeQualificationError(
                "workspace_graph_select_target_missing", operation
            )
        return dict(min(candidates, key=lambda node: str(node.get("component_id"))))

    def _zoom_graph_to_direct_selection(
        self,
        pid: int,
        snapshot: dict[str, object],
        timeout_seconds: float,
        operation: str,
    ) -> tuple[dict[str, object], int]:
        current = snapshot
        press_count = 0
        for _ in range(24):
            lod = current.get("graph_lod")
            if lod in {"select", "detail"}:
                return current, press_count
            if lod == "unavailable":
                break
            self._press(pid, "graph-zoom-in")
            press_count += 1
            current = self._steady(
                pid, f"{operation} zoom {press_count}", timeout_seconds
            )
        raise RuntimeQualificationError(
            "workspace_graph_direct_selection_unavailable", operation
        )

    def _zoom_graph_to_maximum(
        self,
        pid: int,
        snapshot: dict[str, object],
        timeout_seconds: float,
        operation: str,
    ) -> tuple[dict[str, object], int]:
        current = snapshot
        press_count = 0
        saw_growth = False
        for _ in range(32):
            before_scale = _finite_number(
                _case_mapping(current, "graph_fingerprint", operation).get("scale"),
                operation,
            )
            before_camera = _case_mapping(
                _case_mapping(current, "semantic_graph", operation),
                "camera",
                operation,
            )
            if (
                _same_within(before_scale, 8.0, tolerance=0.001)
                and _same_within(
                    _finite_number(before_camera.get("scale"), operation),
                    8.0,
                    tolerance=0.001,
                )
                and before_camera.get("level") == "최대"
                and before_camera.get("zoom_in_enabled") is False
                and saw_growth
            ):
                return current, press_count
            if before_camera.get("zoom_in_enabled") is not True:
                break
            self._press(pid, "graph-zoom-in")
            press_count += 1
            after = self._steady(
                pid, f"{operation} maximum probe {press_count}", timeout_seconds
            )
            after_scale = _finite_number(
                _case_mapping(after, "graph_fingerprint", operation).get("scale"),
                operation,
            )
            after_camera = _case_mapping(
                _case_mapping(after, "semantic_graph", operation),
                "camera",
                operation,
            )
            if (
                after_scale <= before_scale
                or after_scale > 8
                or not _same_within(
                    after_scale,
                    _finite_number(after_camera.get("scale"), operation),
                    tolerance=0.001,
                )
            ):
                break
            saw_growth = True
            current = after
        raise RuntimeQualificationError(
            "workspace_graph_maximum_unconfirmed", operation
        )

    def _capture_graph_readability(
        self,
        *,
        case_id: str,
        pid: int,
        case_before: dict[str, object],
        appearance: str,
        zoom_state: str,
        zoom_press_count: int,
        maximum_reached: bool,
        selected_component_id: str | None,
        contextual_camera_evidence: dict[str, object] | None,
        timeout_seconds: float,
    ) -> dict[str, object]:
        self._clear_checkout_path_for_capture(pid, case_id)
        before_capture = self._steady(
            pid, f"{case_id} before capture", timeout_seconds
        )
        before_ax = self._graph_readability_ax(before_capture, case_id)
        before_identity = self._graph_readability_snapshot_identity(
            before_capture, case_id
        )
        identity_sha256 = _sha256_bytes(_canonical_json_bytes(before_identity))
        if self.graph_readability_snapshot_sha256 is None:
            self.graph_readability_snapshot_sha256 = identity_sha256
        elif self.graph_readability_snapshot_sha256 != identity_sha256:
            raise RuntimeQualificationError(
                "workspace_graph_readability_snapshot_changed", case_id
            )
        graph = _case_mapping(before_capture, "graph_fingerprint", case_id)
        graph_scale = _finite_number(graph.get("scale"), case_id)
        graph_lod = before_capture.get("graph_lod")
        if graph_lod not in {"overview", "select", "detail", "unavailable"}:
            raise RuntimeQualificationError(
                "workspace_graph_lod_invalid", case_id
            )
        if before_capture.get("appearance_mode") != appearance:
            raise RuntimeQualificationError(
                "workspace_graph_appearance_unconfirmed", case_id
            )
        identity = _case_mapping(before_capture, "identity", case_id)
        window_id = identity.get("window_id")
        if not isinstance(window_id, int) or isinstance(window_id, bool) or window_id <= 0:
            raise RuntimeQualificationError(
                "workspace_snapshot_identity_invalid", case_id
            )
        destination = (self.evidence_dir / "cases" / f"{case_id}.png").resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        capture = self._method("capture_workspace_window")(
            pid,
            window_id,
            destination,
            include_cursor=False,
        )
        if (
            not isinstance(capture, ScreenshotCaptureEvidence)
            or capture.capture_mode != SCREEN_CAPTURE_MODE
            or capture.content_rect is None
            or not destination.is_file()
            or destination.stat().st_size <= 0
        ):
            raise RuntimeQualificationError(
                "workspace_graph_readability_capture_invalid", case_id
            )
        after_capture = self._steady(
            pid, f"{case_id} after capture", timeout_seconds
        )
        after_ax = self._graph_readability_ax(after_capture, case_id)
        after_identity = self._graph_readability_snapshot_identity(
            after_capture, case_id
        )
        before_ax_sha256 = _sha256_bytes(_canonical_json_bytes(before_ax))
        after_ax_sha256 = _sha256_bytes(_canonical_json_bytes(after_ax))
        if (
            before_ax_sha256 != after_ax_sha256
            or before_identity != after_identity
            or before_capture.get("identity") != after_capture.get("identity")
            or before_capture.get("appearance_mode")
            != after_capture.get("appearance_mode")
            or before_capture.get("graph_lod") != after_capture.get("graph_lod")
            or not _same_within(
                graph_scale,
                _finite_number(
                    _case_mapping(
                        after_capture, "graph_fingerprint", case_id
                    ).get("scale"),
                    case_id,
                ),
                tolerance=0.001,
            )
        ):
            raise RuntimeQualificationError(
                "workspace_graph_readability_capture_drift", case_id
            )
        try:
            pixel_width, pixel_height, pixels = rgba_png_pixels(
                destination.read_bytes(),
                "workspace_graph_readability_png_invalid",
            )
        except Exception as error:
            raise RuntimeQualificationError(
                "workspace_graph_readability_png_invalid", case_id
            ) from error
        if (
            pixel_width != capture.pixel_width
            or pixel_height != capture.pixel_height
        ):
            raise RuntimeQualificationError(
                "workspace_graph_readability_capture_geometry_mismatch", case_id
            )
        nodes = before_ax.get("component_nodes")
        if not isinstance(nodes, list):
            raise RuntimeQualificationError(
                "workspace_graph_readability_ax_invalid", case_id
            )
        semantic_nodes = [node for node in nodes if isinstance(node, dict)]
        semantic_nodes.sort(key=lambda node: str(node.get("component_id")))
        matches = [
            node
            for node in semantic_nodes
            if node.get("component_id") == selected_component_id
            and node.get("selected") is True
        ]
        if zoom_state != "fit" and len(matches) != 1:
            raise RuntimeQualificationError(
                "workspace_graph_selected_node_not_visible", case_id
            )
        capture_content_frame = {
            key: value
            for key, value in zip(
                ("x", "y", "width", "height"),
                capture.content_rect,
                strict=True,
            )
        }
        expected_kind_counts = dict(
            sorted(Counter(str(node.get("kind")) for node in semantic_nodes).items())
        )
        selected_kind_token = (
            str(matches[0].get("kind"))
            if zoom_state != "fit" and len(matches) == 1
            else None
        )
        try:
            viewport_pixel_proof = analyze_graph_viewport_pixels(
                pixels=pixels,
                pixel_width=pixel_width,
                pixel_height=pixel_height,
                point_pixel_scale=capture.point_pixel_scale,
                capture_content_frame=capture_content_frame,
                viewport_frame=before_ax["viewport_global_frame"],
                appearance=appearance,
                zoom_state=("direct" if zoom_state == "direct-select" else zoom_state),
                expected_kind_counts=expected_kind_counts,
                selected_component_id=(
                    selected_component_id if zoom_state != "fit" else None
                ),
                selected_kind_token=(
                    selected_kind_token if zoom_state != "fit" else None
                ),
            )
        except GraphPixelAnalysisError as error:
            raise RuntimeQualificationError(
                "workspace_graph_pixel_proof_unavailable",
                f"{case_id} {error.code}",
            ) from error
        selected_graph_id = graph.get("selected_component_id")
        right_detail_id = graph.get("right_detail_id")
        direct_selection_confirmed = zoom_state != "fit"
        if direct_selection_confirmed and (
            selected_graph_id != selected_component_id
            or right_detail_id != selected_component_id
        ):
            raise RuntimeQualificationError(
                "workspace_graph_direct_selection_unconfirmed", case_id
            )
        semantic_component_ids = [
            str(node["component_id"]) for node in semantic_nodes
        ]
        selected_pixel_evidence = viewport_pixel_proof.get("selected_evidence")
        readability = {
            "appearance": appearance,
            "zoom_state": zoom_state,
            "graph_scale": graph_scale,
            "graph_lod": graph_lod,
            "snapshot_identity": before_identity,
            "snapshot_identity_sha256": identity_sha256,
            "ax_before_sha256": before_ax_sha256,
            "ax_after_sha256": after_ax_sha256,
            "component_inline_title_count": before_ax.get(
                "visible_component_static_text_count"
            ),
            "profile_identity_visible": before_ax.get(
                "profile_identity_visible"
            ),
            "unprofiled_identity_visible": before_ax.get(
                "unprofiled_identity_visible"
            ),
            "required_kind_tokens": list(expected_kind_counts),
            "semantic_component_ids": semantic_component_ids,
            "expected_kind_counts": expected_kind_counts,
            "viewport_pixel_proof": viewport_pixel_proof,
            "selected_component_id": (
                selected_component_id if direct_selection_confirmed else None
            ),
            "direct_selection_confirmed": direct_selection_confirmed,
            "non_color_selection_cue": (
                direct_selection_confirmed
                and isinstance(selected_pixel_evidence, dict)
                and selected_pixel_evidence.get("selection_halo_detected") is True
                and selected_pixel_evidence.get("selection_underlay_detected") is True
                and selected_pixel_evidence.get("mark_detected") is True
            ),
            "maximum_reached": maximum_reached,
            "screenshot_count": 1,
        }
        after = dict(after_capture)
        after["graph_readability"] = readability
        self.active_graph_readability_captures[case_id] = (
            destination,
            capture,
        )
        action = {
            "kind": "capture-graph-readability",
            "appearance": appearance,
            "zoom_state": zoom_state,
            "fit_reset": zoom_state != "maximum",
            "zoom_press_count": zoom_press_count,
            "selected_component_id": selected_component_id,
            "maximum_probe_stable": maximum_reached,
            "capture_transport": "ax-then-screencapturekit-then-ax",
        }
        if contextual_camera_evidence is not None:
            action["contextual_camera_evidence"] = contextual_camera_evidence
        return self._payload(pid, action, case_before, after, 0)

    def _observe_contextual_camera_focus(
        self,
        pid: int,
        before: dict[str, object],
        operation: str,
        timeout_seconds: float,
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Record the public AX endpoints of a contextual camera focus.

        The packaged collector can observe the semantic camera pose before and
        after the renderer settles. It intentionally does not claim individual
        animation frames: AX snapshots are asynchronous and expose no frame
        timestamp or reduced-motion override.
        """

        baseline = _semantic_camera_pose(before, operation)
        anchor_hash_before = _graph_anchor_hash(before, operation)
        after = self._steady(pid, f"{operation} camera settled", timeout_seconds)
        settled = _semantic_camera_pose(after, operation)
        anchor_hash_after = _graph_anchor_hash(after, operation)
        if _camera_pose_equal(baseline, settled) or anchor_hash_before != anchor_hash_after:
            raise RuntimeQualificationError(
                "workspace_graph_contextual_camera_unconfirmed", operation
            )
        return after, {
            "origin": "graph",
            "intent": "contextual",
            "observation_mode": "endpoint-only-ax",
            "baseline": baseline,
            "settled": settled,
            "camera_changed": True,
            "anchor_hash_before": anchor_hash_before,
            "anchor_hash_after": anchor_hash_after,
        }

    def _exercise_graph_readability_case(
        self,
        case_id: str,
        pid: int,
        before: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]:
        appearance, zoom_state = GRAPH_READABILITY_CASE_CONFIG[case_id]
        setup = self._set_graph_appearance(
            pid, appearance, timeout_seconds, case_id
        )
        zoom_press_count = 0
        maximum_reached = False
        selected_component_id: str | None = None
        contextual_camera_evidence: dict[str, object] | None = None
        setup = self._reset_graph_fit(pid, timeout_seconds, case_id)
        if zoom_state == "direct-select":
            target = self._center_graph_component(setup, case_id)
            selected_component_id = str(target["component_id"])
            self._press(pid, f"semantic-component:{selected_component_id}")
            setup, contextual_camera_evidence = self._observe_contextual_camera_focus(
                pid, setup, case_id, timeout_seconds
            )
            setup, zoom_press_count = self._zoom_graph_to_direct_selection(
                pid, setup, timeout_seconds, case_id
            )
            self.graph_readability_selected_component_id = selected_component_id
        elif zoom_state == "maximum":
            selected_component_id = self.graph_readability_selected_component_id
            if selected_component_id is None:
                target = self._center_graph_component(setup, case_id)
                selected_component_id = str(target["component_id"])
                self._press(pid, f"semantic-component:{selected_component_id}")
                setup = self._steady(
                    pid, f"{case_id} selected", timeout_seconds
                )
            setup, zoom_press_count = self._zoom_graph_to_maximum(
                pid, setup, timeout_seconds, case_id
            )
            maximum_reached = True
        return self._capture_graph_readability(
            case_id=case_id,
            pid=pid,
            case_before=before,
            appearance=appearance,
            zoom_state=zoom_state,
            zoom_press_count=zoom_press_count,
            maximum_reached=maximum_reached,
            selected_component_id=selected_component_id,
            contextual_camera_evidence=contextual_camera_evidence,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _amendment_deadline_remaining(
        deadline: float, operation: str
    ) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeQualificationError(
                "workspace_amendment_case_timeout", operation
            )
        return remaining

    def _amendment_raw_workspace(
        self, pid: int, operation: str, deadline: float
    ) -> dict[str, object]:
        raw = self._method("observe_workspace")(
            pid,
            self.actual_anchor_ids,
            timeout_seconds=self._amendment_deadline_remaining(
                deadline, operation
            ),
        )
        targets = raw.get("targets") if isinstance(raw, dict) else None
        if (
            not isinstance(raw, dict)
            or raw.get("accepted") is not True
            or raw.get("pid") != pid
            or not isinstance(raw.get("window_id"), int)
            or isinstance(raw.get("window_id"), bool)
            or not isinstance(targets, list)
            or len(targets) > int(FIXED_LAYOUT_CONTRACT["ax_target_limit"])
        ):
            raise RuntimeQualificationError(
                "workspace_amendment_observation_invalid", operation
            )
        return raw

    def _amendment_selector_target(
        self, pid: int, selector: str, operation: str, deadline: float
    ) -> dict[str, object]:
        observation = self._method("observe_workspace_selector")(
            pid,
            selector,
            timeout_seconds=self._amendment_deadline_remaining(
                deadline, operation
            ),
        )
        target = observation.get("target") if isinstance(observation, dict) else None
        if (
            not isinstance(observation, dict)
            or observation.get("present") is not True
            or not isinstance(target, dict)
        ):
            raise RuntimeQualificationError(
                "workspace_amendment_selector_missing", operation
            )
        return dict(target)

    @staticmethod
    def _amendment_frame(
        value: object, operation: str
    ) -> dict[str, int]:
        if not isinstance(value, dict):
            raise RuntimeQualificationError(
                "workspace_amendment_frame_invalid", operation
            )
        frame: dict[str, int] = {}
        for key in ("x", "y", "width", "height"):
            item = value.get(key)
            if not isinstance(item, int) or isinstance(item, bool):
                raise RuntimeQualificationError(
                    "workspace_amendment_frame_invalid", operation
                )
            frame[key] = item
        if frame["width"] <= 0 or frame["height"] <= 0:
            raise RuntimeQualificationError(
                "workspace_amendment_frame_invalid", operation
            )
        return frame

    def _amendment_restart(
        self, operation: str, deadline: float
    ) -> int:
        if (
            self.artifact is None
            or self.environment is None
            or self.current_pid is None
        ):
            raise RuntimeQualificationError(
                "runtime_session_missing", operation
            )
        old_pid = self.current_pid
        remaining = self._amendment_deadline_remaining(deadline, operation)
        if not self.terminate(old_pid):
            raise RuntimeQualificationError(
                "workspace_primary_termination_failed", operation
            )
        self.wait_for_zero(remaining)
        pid = self.launch_primary(self.artifact, self.environment)
        return pid

    def _amendment_final_payload(
        self,
        *,
        case_id: str,
        pid: int,
        started_monotonic: float,
        action: dict[str, object],
        before: dict[str, object] | None,
        after: dict[str, object],
        observation: dict[str, object],
        ax_target_count: int,
    ) -> dict[str, object]:
        elapsed_ms = math.ceil((time.monotonic() - started_monotonic) * 1000)
        payload = self._payload(pid, action, before, after, 0)
        payload.update(
            {
                "case_id": case_id,
                "limits": {
                    "ax_target_count": ax_target_count,
                    "screenshot_count": 1,
                    "elapsed_ms": elapsed_ms,
                },
                "observation": observation,
            }
        )
        validate_amendment_case_observation(case_id, payload)
        return payload

    def _exercise_amendment_pane(
        self,
        case_id: str,
        pid: int,
        timeout_seconds: float,
        started_monotonic: float,
    ) -> dict[str, object]:
        side = "left" if "left" in case_id else "right"
        selector = f"#{side}-pane-disclosure"
        deadline = started_monotonic + timeout_seconds
        before = self._steady(
            pid,
            f"{case_id} before",
            self._amendment_deadline_remaining(deadline, case_id),
        )
        before_target = self._amendment_selector_target(
            pid, selector, case_id, deadline
        )
        if before_target.get("expanded") is not True:
            raise RuntimeQualificationError(
                "workspace_amendment_pane_precondition_invalid", case_id
            )
        initial_preferred = _pair(before, "preferred_pair")[f"{side}_px"]
        center_before = _frame(
            _case_mapping(before, "geometry", case_id).get("center"), case_id
        )["width"]
        self._method("press_workspace_selector")(pid, selector)
        collapsed_target = self._amendment_selector_target(
            pid, selector, case_id, deadline
        )
        raw_collapsed = self._amendment_raw_workspace(
            pid, case_id, deadline
        )
        physical = raw_collapsed.get("physical_geometry")
        if not isinstance(physical, dict):
            raise RuntimeQualificationError(
                "workspace_amendment_pane_contract_mismatch", case_id
            )
        panes = physical.get("panes")
        pane_present = physical.get("pane_present")
        divider_present = physical.get("divider_present")
        dividers = physical.get("dividers")
        collapsed_sides = physical.get("collapsed_sides")
        if not all(
            isinstance(value, dict)
            for value in (panes, pane_present, divider_present, dividers)
        ) or not isinstance(collapsed_sides, list):
            raise RuntimeQualificationError(
                "workspace_amendment_pane_contract_mismatch", case_id
            )
        assert isinstance(panes, dict)
        assert isinstance(pane_present, dict)
        assert isinstance(divider_present, dict)
        assert isinstance(dividers, dict)
        center_after = self._amendment_frame(panes.get("center"), case_id)[
            "width"
        ]
        if (
            collapsed_target.get("expanded") is not False
            or side not in collapsed_sides
            or pane_present.get(side) is not False
            or panes.get(side) is not None
            or divider_present.get(side) is not False
            or dividers.get(side) is not None
        ):
            raise RuntimeQualificationError(
                "workspace_amendment_pane_contract_mismatch", case_id
            )
        pid = self._amendment_restart(case_id, deadline)
        restart_target = self._amendment_selector_target(
            pid, selector, case_id, deadline
        )
        raw_restart = self._amendment_raw_workspace(pid, case_id, deadline)
        restart_physical = raw_restart.get("physical_geometry")
        restart_sides = (
            restart_physical.get("collapsed_sides")
            if isinstance(restart_physical, dict)
            else None
        )
        if restart_target.get("expanded") is not False or (
            not isinstance(restart_sides, list) or side not in restart_sides
        ):
            raise RuntimeQualificationError(
                "workspace_amendment_pane_restart_mismatch", case_id
            )
        self._method("press_workspace_selector")(pid, selector)
        after = self._steady(
            pid,
            f"{case_id} reopened",
            self._amendment_deadline_remaining(deadline, case_id),
        )
        reopened_target = self._amendment_selector_target(
            pid, selector, case_id, deadline
        )
        observation = {
            "initial_preferred_width_px": initial_preferred,
            "collapsed": {
                "collapsed_side": side,
                "other_side": "right" if side == "left" else "left",
                "pane_ax_present": False,
                "pane_geometry_present": False,
                "divider_ax_present": False,
                "divider_geometry_present": False,
                "center_width_before_px": center_before,
                "center_width_after_px": center_after,
            },
            "restart": {"collapsed_side": side, "persisted": True},
            "reopened": {
                "collapsed_side": None,
                "preferred_width_px": _pair(after, "preferred_pair")[
                    f"{side}_px"
                ],
                "pane_ax_present": True,
                "divider_ax_present": True,
                "expanded": reopened_target.get("expanded"),
            },
        }
        return self._amendment_final_payload(
            case_id=case_id,
            pid=pid,
            started_monotonic=started_monotonic,
            action={"kind": "pane-collapse-reopen-restart", "side": side},
            before=before,
            after=after,
            observation=observation,
            ax_target_count=max(
                len(raw_collapsed.get("targets", [])),
                len(raw_restart.get("targets", [])),
            ),
        )

    def _amendment_typography_tokens(
        self, pid: int, operation: str, deadline: float
    ) -> list[int]:
        raw = self._amendment_raw_workspace(pid, operation, deadline)
        samples = raw.get("typography_samples")
        if not isinstance(samples, list) or not samples:
            raise RuntimeQualificationError(
                "workspace_amendment_typography_samples_missing", operation
            )
        tokens: set[int] = set()
        for sample in samples:
            value = sample.get("font_size_px") if isinstance(sample, dict) else None
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not math.isclose(float(value), round(float(value)), abs_tol=0.2)
            ):
                raise RuntimeQualificationError(
                    "workspace_amendment_typography_samples_invalid", operation
                )
            tokens.add(round(float(value)))
        return sorted(tokens)

    def _exercise_amendment_typography(
        self,
        case_id: str,
        pid: int,
        timeout_seconds: float,
        started_monotonic: float,
    ) -> dict[str, object]:
        deadline = started_monotonic + timeout_seconds
        before = self._steady(
            pid, f"{case_id} before", self._amendment_deadline_remaining(deadline, case_id)
        )
        records: list[dict[str, object]] = []
        for preset in ("Small", "Default", "Large"):
            self._method("select_workspace_typography_preset")(pid, preset)
            selected = self._amendment_selector_target(
                pid, "#typography-menu-trigger", case_id, deadline
            )
            if selected.get("value_text") != preset:
                raise RuntimeQualificationError(
                    "workspace_amendment_typography_contract_mismatch", case_id
                )
            pid = self._amendment_restart(case_id, deadline)
            restored = self._amendment_selector_target(
                pid, "#typography-menu-trigger", case_id, deadline
            )
            tokens = self._amendment_typography_tokens(
                pid, case_id, deadline
            )
            records.append(
                {
                    "preset": preset,
                    "tokens_px": tokens,
                    "persisted_after_restart": restored.get("value_text") == preset,
                }
            )
        after = self._steady(
            pid, f"{case_id} after", self._amendment_deadline_remaining(deadline, case_id)
        )
        return self._amendment_final_payload(
            case_id=case_id,
            pid=pid,
            started_monotonic=started_monotonic,
            action={"kind": "set-typography-preset-and-restart"},
            before=before,
            after=after,
            observation={"presets": records},
            ax_target_count=1,
        )

    @staticmethod
    def _amendment_overlap_count(frames: list[dict[str, int]]) -> int:
        count = 0
        for index, left in enumerate(frames):
            for right in frames[index + 1 :]:
                overlap_x = min(left["x"] + left["width"], right["x"] + right["width"]) > max(left["x"], right["x"])
                overlap_y = min(left["y"] + left["height"], right["y"] + right["height"]) > max(left["y"], right["y"])
                count += int(overlap_x and overlap_y)
        return count

    def _exercise_amendment_toolbar(
        self,
        case_id: str,
        pid: int,
        timeout_seconds: float,
        started_monotonic: float,
    ) -> dict[str, object]:
        deadline = started_monotonic + timeout_seconds
        before = self._steady(
            pid, f"{case_id} before", self._amendment_deadline_remaining(deadline, case_id)
        )
        rows: list[dict[str, object]] = []
        for width in (1200, 960, 760):
            self._resize(pid, width, 800)
            for preset in ("Small", "Default", "Large"):
                self._method("select_workspace_typography_preset")(pid, preset)
                toolbar = self._method("observe_workspace_toolbar_layout")(pid)
                toolbar_targets = toolbar.get("targets")
                if not isinstance(toolbar_targets, dict):
                    raise RuntimeQualificationError(
                        "workspace_amendment_toolbar_contract_mismatch", case_id
                    )
                window_frame = self._amendment_frame(
                    toolbar.get("window_frame"), case_id
                )
                frames: dict[str, dict[str, int]] = {}
                for name in _AMENDMENT_TOOLBAR_TARGETS:
                    target = toolbar_targets.get(name)
                    if not isinstance(target, dict):
                        raise RuntimeQualificationError(
                            "workspace_amendment_toolbar_contract_mismatch", case_id
                        )
                    frames[name] = self._amendment_frame(
                        target.get("frame"),
                        case_id,
                    )
                frame_values = list(frames.values())
                distinct_rows: list[int] = []
                layout_frames = [
                    frames[name]
                    for name in ("identity", "repository", "appearance")
                ]
                for frame in sorted(layout_frames, key=lambda value: value["y"]):
                    if not distinct_rows or abs(frame["y"] - distinct_rows[-1]) > 2:
                        distinct_rows.append(frame["y"])
                layout = {1: "one-row", 2: "two-row", 3: "three-row"}.get(
                    len(distinct_rows), "invalid"
                )
                crop_count = sum(
                    frame["x"] < window_frame["x"]
                    or frame["y"] < window_frame["y"]
                    or frame["x"] + frame["width"]
                    > window_frame["x"] + window_frame["width"]
                    or frame["y"] + frame["height"]
                    > window_frame["y"] + window_frame["height"]
                    for frame in frame_values
                )
                rows.append(
                    {
                        "window_width_px": window_frame["width"],
                        "preset": preset,
                        "layout": layout,
                        "overlap_count": self._amendment_overlap_count(frame_values),
                        "crop_count": crop_count,
                        "tab_order": toolbar.get("tab_order"),
                        "target_frames": frames,
                    }
                )
        self._resize(pid, 1200, 800)
        self._method("select_workspace_typography_preset")(pid, "Default")
        after = self._steady(
            pid, f"{case_id} after", self._amendment_deadline_remaining(deadline, case_id)
        )
        return self._amendment_final_payload(
            case_id=case_id,
            pid=pid,
            started_monotonic=started_monotonic,
            action={"kind": "measure-toolbar-matrix"},
            before=before,
            after=after,
            observation={"rows": rows},
            ax_target_count=3,
        )

    def _amendment_local_ready(
        self, pid: int, deadline: float, operation: str
    ) -> dict[str, object]:
        snapshot = self._steady(
            pid, f"{operation} dashboard", self._amendment_deadline_remaining(deadline, operation)
        )
        if snapshot.get("dashboard") != "local":
            self._press(pid, "dashboard-local")
        last_error: RuntimeQualificationError | None = None
        while time.monotonic() < deadline:
            try:
                observation = self._method("observe_workspace_local_scroll")(pid)
                selected = observation.get("selected_row")
                if isinstance(selected, dict) and selected.get("present") is False:
                    initialized = self._method("select_workspace_local_result")(
                        pid, "mouse"
                    )
                    after = initialized.get("after")
                    if not isinstance(after, dict):
                        raise RuntimeQualificationError(
                            "workspace_amendment_local_results_unavailable",
                            operation,
                        )
                    return after
                return observation
            except RuntimeQualificationError as error:
                last_error = error
                time.sleep(0.1)
        raise RuntimeQualificationError(
            "workspace_amendment_local_results_unavailable",
            f"{operation}: {last_error.code if last_error else 'timeout'}",
        )

    @staticmethod
    def _amendment_scroll_value(
        observation: dict[str, object], operation: str
    ) -> float:
        scrollbar = observation.get("vertical_scrollbar")
        value = scrollbar.get("value") if isinstance(scrollbar, dict) else None
        if (
            not isinstance(scrollbar, dict)
            or scrollbar.get("present") is not True
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise RuntimeQualificationError(
                "workspace_amendment_local_scrollbar_missing", operation
            )
        return float(value)

    def _exercise_amendment_local(
        self,
        case_id: str,
        pid: int,
        timeout_seconds: float,
        started_monotonic: float,
    ) -> dict[str, object]:
        deadline = started_monotonic + timeout_seconds
        before = self._steady(
            pid, f"{case_id} before", self._amendment_deadline_remaining(deadline, case_id)
        )
        initial_scroll = self._amendment_local_ready(pid, deadline, case_id)
        action: dict[str, object]
        observation: dict[str, object]
        ax_target_count = 1
        if case_id == "local-all-locations-alignment":
            self._method("press_workspace_selector")(pid, "#local-scope-all")
            target = self._amendment_selector_target(
                pid, "#local-scope-all", case_id, deadline
            )
            frame = self._amendment_frame(target.get("frame"), case_id)
            action = {"kind": "open-all-locations"}
            observation = {
                "label": target.get("name"),
                "visible_ax_target": target.get("visible") is True,
                "role": target.get("role"),
                "frame_height_px": frame["height"],
            }
        elif case_id == "local-selection-mouse-scroll-preserved":
            selected = self._method("select_workspace_local_result")(pid, "mouse")
            mouse_before = selected.get("before")
            mouse_after = selected.get("after")
            if not isinstance(mouse_before, dict) or not isinstance(mouse_after, dict):
                raise RuntimeQualificationError(
                    "workspace_amendment_local_mouse_scroll_mismatch", case_id
                )
            action = {"kind": "select-local-result", "input": "mouse"}
            observation = {
                "scroll_value_before": self._amendment_scroll_value(mouse_before, case_id),
                "scroll_value_after": self._amendment_scroll_value(mouse_after, case_id),
                "selection_changed": selected.get("selection_changed") is True,
            }
        elif case_id == "local-selection-keyboard-nearest-reveal":
            current = initial_scroll
            selected: dict[str, object] | None = None
            limit = min(32, int(initial_scroll.get("result_count", 0)))
            for _ in range(limit):
                candidate = self._method("select_workspace_local_result")(
                    pid, "keyboard"
                )
                candidate_before = candidate.get("before")
                candidate_after = candidate.get("after")
                if not isinstance(candidate_before, dict) or not isinstance(candidate_after, dict):
                    raise RuntimeQualificationError(
                        "workspace_amendment_local_keyboard_scroll_mismatch", case_id
                    )
                if self._amendment_scroll_value(candidate_after, case_id) > self._amendment_scroll_value(candidate_before, case_id):
                    selected = candidate
                    break
                current = candidate_after
            if selected is None:
                raise RuntimeQualificationError(
                    "workspace_amendment_local_keyboard_scroll_mismatch", case_id
                )
            selected_before = selected["before"]
            selected_after = selected["after"]
            assert isinstance(selected_before, dict)
            assert isinstance(selected_after, dict)
            row = selected_after.get("selected_row")
            if not isinstance(row, dict):
                raise RuntimeQualificationError(
                    "workspace_amendment_local_keyboard_scroll_mismatch", case_id
                )
            action = {"kind": "select-local-result", "input": "keyboard"}
            observation = {
                "scroll_value_before": self._amendment_scroll_value(selected_before, case_id),
                "scroll_value_after": self._amendment_scroll_value(selected_after, case_id),
                "target_frame": self._amendment_frame(row.get("frame"), case_id),
                "viewport_frame": self._amendment_frame(
                    selected_after.get("visible_bounds"), case_id
                ),
                "nearest_reveal": row.get("visible") is True,
            }
        elif case_id == "local-ignore-save-rescan-retains-complete":
            publication_before = self._method(
                "observe_workspace_local_publication"
            )(pid)
            project = self._method("select_workspace_project_scope")(pid)
            if project.get("project_identity_redacted") is not True:
                raise RuntimeQualificationError(
                    "workspace_amendment_ignore_rescan_mismatch", case_id
                )
            self._method("press_workspace_selector")(pid, "#project-ignore-open")
            dialog = self._method("observe_workspace_selector")(
                pid, "[data-project-ignore-dialog]"
            )
            editor = self._method("observe_workspace_selector")(
                pid, "#project-ignore-text"
            )
            editor_target = editor.get("target") if isinstance(editor, dict) else None
            if (
                not isinstance(dialog, dict)
                or dialog.get("present") is not True
                or not isinstance(editor_target, dict)
                or editor_target.get("value_redacted") is not True
            ):
                raise RuntimeQualificationError(
                    "workspace_amendment_ignore_rescan_mismatch", case_id
                )
            changed = self._method("set_workspace_text")(
                pid,
                "project-ignore-text",
                "# packaged runtime verification\n",
                timeout_seconds=self._amendment_deadline_remaining(deadline, case_id),
            )
            if changed is not True and (
                not isinstance(changed, dict) or changed.get("accepted") is not True
            ):
                raise RuntimeQualificationError(
                    "workspace_amendment_ignore_rescan_mismatch", case_id
                )
            self._method("press_workspace_selector")(pid, "#project-ignore-save")
            publication_during = self._method(
                "observe_workspace_local_publication"
            )(pid)
            publication_after: dict[str, object] | None = None
            while time.monotonic() < deadline:
                candidate = self._method(
                    "observe_workspace_local_publication"
                )(pid)
                if (
                    candidate.get("scan_phase") == "complete"
                    and candidate.get("snapshot_identity_prefix")
                    != publication_before.get("snapshot_identity_prefix")
                ):
                    publication_after = candidate
                    break
                time.sleep(0.1)
            if publication_after is None:
                raise RuntimeQualificationError(
                    "workspace_amendment_ignore_rescan_mismatch", case_id
                )
            action = {"kind": "save-ignore-and-rescan"}
            observation = {
                "outcome": publication_during.get("ignore_save_outcome"),
                "last_complete_snapshot_before": publication_before.get("snapshot_identity_prefix"),
                "last_complete_snapshot_during": publication_during.get("snapshot_identity_prefix"),
                "last_complete_snapshot_after": publication_after.get("snapshot_identity_prefix"),
                "optimistic_removal_count": int(
                    publication_before.get("snapshot_identity_prefix")
                    != publication_during.get("snapshot_identity_prefix")
                ),
                "editor_text_in_evidence": False,
            }
        elif case_id == "local-correlation-badge-inspector-exact":
            badge = self._method("observe_workspace_selector")(
                pid, "[data-correlation-state]"
            )
            detail = self._method("observe_workspace_selector")(
                pid, "#local-correlation-detail"
            )
            detail_target = detail.get("target") if isinstance(detail, dict) else None
            badge_target = badge.get("target") if isinstance(badge, dict) else None
            if not isinstance(detail_target, dict):
                raise RuntimeQualificationError(
                    "workspace_amendment_correlation_mismatch", case_id
                )
            state = (
                badge_target.get("correlation_state")
                if isinstance(badge_target, dict)
                else "uncorrelated"
            )
            action = {"kind": "inspect-correlation"}
            observation = {
                "state": state,
                "badge": (
                    badge_target.get("safe_text")
                    if isinstance(badge_target, dict)
                    else None
                ),
                "inspector": detail_target.get("safe_text"),
            }
            ax_target_count = 2
        elif case_id == "local-markdown-preview-safety":
            if self.runtime_home is None:
                raise RuntimeQualificationError(
                    "workspace_markdown_preview_fixture_missing", case_id
                )
            select_fixture = self._method("select_workspace_local_fixture")
            observe_preview = self._method(
                "observe_workspace_markdown_preview_safety"
            )

            def await_preview(*, expected_window_id: int) -> dict[str, object]:
                last_error: RuntimeQualificationError | None = None
                while time.monotonic() < deadline:
                    try:
                        candidate = observe_preview(pid)
                    except RuntimeQualificationError as error:
                        last_error = error
                        time.sleep(0.05)
                        continue
                    if (
                        candidate.get("pid") == pid
                        and candidate.get("window_id") == expected_window_id
                        and candidate.get("preview_present") is True
                        and candidate.get("selected_display_name")
                        == "runtime-verification"
                        and candidate.get("selected_state_observed") is True
                        and candidate.get("preview_heading") == "전체 원문"
                        and candidate.get("render_completion_observed") is True
                        and candidate.get("semantic_role_order")
                        == ["heading", "list", "list", "table"]
                    ):
                        return candidate
                    time.sleep(0.05)
                raise RuntimeQualificationError(
                    "workspace_markdown_preview_safety_unavailable",
                    f"{case_id}: {last_error.code if last_error else 'identity-timeout'}",
                )

            baseline_selection = select_fixture(pid, "runtime-verification")
            baseline_window_id = baseline_selection.get("window_id")
            if (
                not isinstance(baseline_window_id, int)
                or isinstance(baseline_window_id, bool)
                or baseline_window_id <= 0
            ):
                raise RuntimeQualificationError(
                    "workspace_markdown_preview_selection_invalid", case_id
                )
            baseline_preview = await_preview(
                expected_window_id=baseline_window_id
            )

            def reopen_current_preview() -> dict[str, object]:
                selection = select_fixture(pid, "runtime-verification")
                selection_window_id = selection.get("window_id")
                if (
                    not isinstance(selection_window_id, int)
                    or isinstance(selection_window_id, bool)
                    or selection_window_id <= 0
                ):
                    raise RuntimeQualificationError(
                        "workspace_markdown_preview_selection_invalid", case_id
                    )
                reopened = await_preview(expected_window_id=selection_window_id)
                return {"selection": selection, "preview": reopened}

            action_result, network_interval = self._method(
                "observe_workspace_network_interval"
            )(
                pid,
                reopen_current_preview,
                minimum_duration_seconds=1.0,
                sample_interval_seconds=0.05,
            )
            if not isinstance(action_result, dict):
                raise RuntimeQualificationError(
                    "workspace_markdown_preview_selection_invalid", case_id
                )
            monitored_selection = action_result.get("selection")
            monitored_preview = action_result.get("preview")
            if not isinstance(monitored_selection, dict) or not isinstance(
                monitored_preview, dict
            ):
                raise RuntimeQualificationError(
                    "workspace_markdown_preview_selection_invalid", case_id
                )
            selection_window_id = monitored_selection.get("window_id")
            if not isinstance(selection_window_id, int) or isinstance(
                selection_window_id, bool
            ):
                raise RuntimeQualificationError(
                    "workspace_markdown_preview_selection_invalid", case_id
                )
            final_preview = await_preview(expected_window_id=selection_window_id)
            role_counts = final_preview.get("forbidden_ax_role_counts")
            if not isinstance(role_counts, dict):
                raise RuntimeQualificationError(
                    "workspace_markdown_preview_safety_invalid", case_id
                )
            action = {
                "kind": "reopen-complex-markdown-preview",
                "transport": "cg-event-mouse-click-with-bounded-observers",
                "fixture_display_name": monitored_selection.get(
                    "selected_display_name"
                ),
                "selection_window_id": selection_window_id,
            }
            observation_without_scan = {
                "preview_window_id": final_preview.get("window_id"),
                "preview_present": final_preview.get("preview_present") is True,
                "selected_display_name": final_preview.get(
                    "selected_display_name"
                ),
                "selected_state_observed": final_preview.get(
                    "selected_state_observed"
                ),
                "preview_heading": final_preview.get("preview_heading"),
                "render_completion_observed": final_preview.get(
                    "render_completion_observed"
                ),
                "rendered_semantic_count": final_preview.get(
                    "rendered_semantic_count"
                ),
                "semantic_role_order": final_preview.get("semantic_role_order"),
                "forbidden_ax_role_counts": role_counts,
                "app_web_area_identity_before": baseline_preview.get(
                    "app_web_area_identity"
                ),
                "app_web_area_identity_after": final_preview.get(
                    "app_web_area_identity"
                ),
                "network_interval": network_interval,
            }
            negative_scan = _structured_evidence_negative_scan(
                {"action": action, "observation": observation_without_scan}
            )
            if negative_scan["forbidden_literal_count"] != 0:
                raise RuntimeQualificationError(
                    "workspace_markdown_preview_structured_evidence_leak", case_id
                )
            observation = {
                **observation_without_scan,
                "structured_evidence_negative_scan": negative_scan,
            }
            ax_target_count = 2
        else:
            raise RuntimeQualificationError("workspace_case_invalid", case_id)
        after = self._steady(
            pid, f"{case_id} after", self._amendment_deadline_remaining(deadline, case_id)
        )
        return self._amendment_final_payload(
            case_id=case_id,
            pid=pid,
            started_monotonic=started_monotonic,
            action=action,
            before=before,
            after=after,
            observation=observation,
            ax_target_count=ax_target_count,
        )

    def _exercise_amendment_case(
        self, case_id: str, pid: int, timeout_seconds: float
    ) -> dict[str, object]:
        started = time.monotonic()
        deadline = started + timeout_seconds
        with self.base.helper_deadline(
            deadline,
            code="workspace_amendment_case_timeout",
            operation=case_id,
        ):
            if case_id in {
                "pane-left-collapse-reopen-restart",
                "pane-right-collapse-reopen-restart",
            }:
                return self._exercise_amendment_pane(
                    case_id, pid, timeout_seconds, started
                )
            if case_id == "typography-preset-restart":
                return self._exercise_amendment_typography(
                    case_id, pid, timeout_seconds, started
                )
            if case_id == "toolbar-width-preset-matrix":
                return self._exercise_amendment_toolbar(
                    case_id, pid, timeout_seconds, started
                )
            return self._exercise_amendment_local(
                case_id, pid, timeout_seconds, started
            )

    def exercise_case(
        self, case_id: str, pid: int, timeout_seconds: float
    ) -> dict[str, object]:
        if case_id not in CASE_IDS or case_id in {
            "restart-first-visible-persisted",
            "final-cleanup-zero",
        }:
            raise RuntimeQualificationError("workspace_case_invalid", case_id)
        pid = self._resolve_pid(pid)
        if case_id in AMENDMENT_CASE_IDS:
            return self._exercise_amendment_case(case_id, pid, timeout_seconds)
        if case_id == "corrupt-preference-default-pair":
            return self._exercise_corrupt_preference(pid, timeout_seconds)
        if case_id == "preference-write-denied-session-usable":
            return self._exercise_write_denied(pid, timeout_seconds)
        if case_id == "small-window-two-column":
            self._reset_pair_for_responsive_cases(pid, timeout_seconds)
        before = self._steady(pid, f"{case_id} before", timeout_seconds)
        if case_id in GRAPH_READABILITY_CASE_CONFIG:
            return self._exercise_graph_readability_case(
                case_id, pid, before, timeout_seconds
            )
        action: dict[str, object] = {"kind": "observe"}
        publication_delta = 0
        during_snapshot: dict[str, object] | None = None

        if case_id == "initial-default":
            self._resize(pid, 1200, 800)
            after = self._steady(pid, case_id, timeout_seconds)
            if _pair(after, "preferred_pair") != {"left_px": 304, "right_px": 368}:
                raise RuntimeQualificationError("workspace_default_pair_mismatch", case_id)
            self._assert_separators(after, True, case_id)
            return self._payload(pid, action, before, after, 0)
        if case_id == "left-pointer-drag":
            before_pair = _pair(before, "preferred_pair")
            deadline = time.monotonic() + timeout_seconds
            drag_action = self._drag(
                pid, "left", 32, active_capture_case=case_id
            )
            if drag_action is None:
                raise RuntimeQualificationError(
                    "workspace_drag_capture_invalid", case_id
                )
            action = {
                **drag_action,
                "separator": "left",
                "delta_px": 32,
            }
            layout_after = self._wait_publication_delta(
                pid,
                before.get("layout_revision"),
                deadline - time.monotonic(),
            )
            after_effective = _pair(layout_after, "effective_pair")
            if (
                after_effective["right_px"]
                != _pair(before, "effective_pair")["right_px"]
                or _pair(layout_after, "preferred_pair") != after_effective
            ):
                raise RuntimeQualificationError("workspace_pointer_independence_mismatch", case_id)
            after_pair = dict(after_effective)
            if after_pair["left_px"] == before_pair["left_px"] or after_pair["right_px"] != before_pair["right_px"]:
                raise RuntimeQualificationError("workspace_pointer_independence_mismatch", case_id)
            self.preferred_pair = after_pair
            after = self._observe_workspace_once(
                pid, layout_after, deadline, case_id
            )
            publication_delta = 1
        elif case_id == "right-pointer-drag":
            before_pair = _pair(before, "preferred_pair")
            deadline = time.monotonic() + timeout_seconds
            drag_action = self._drag(
                pid, "right", 32, active_capture_case=case_id
            )
            if drag_action is None:
                raise RuntimeQualificationError(
                    "workspace_drag_capture_invalid", case_id
                )
            action = {
                **drag_action,
                "separator": "right",
                "delta_px": 32,
            }
            layout_after = self._wait_publication_delta(
                pid,
                before.get("layout_revision"),
                deadline - time.monotonic(),
            )
            after_effective = _pair(layout_after, "effective_pair")
            if (
                after_effective["left_px"]
                != _pair(before, "effective_pair")["left_px"]
                or _pair(layout_after, "preferred_pair") != after_effective
            ):
                raise RuntimeQualificationError("workspace_pointer_independence_mismatch", case_id)
            after_pair = dict(after_effective)
            if after_pair["right_px"] == before_pair["right_px"] or after_pair["left_px"] != before_pair["left_px"]:
                raise RuntimeQualificationError("workspace_pointer_independence_mismatch", case_id)
            self.preferred_pair = after_pair
            after = self._observe_workspace_once(
                pid, layout_after, deadline, case_id
            )
            publication_delta = 1
        elif case_id == "separator-keyboard-step":
            before_pair = _pair(before, "preferred_pair")
            deadline = time.monotonic() + timeout_seconds
            snapshots: list[dict[str, object]] = []
            sub_actions: list[dict[str, object]] = []
            current = before
            for side, key, delta in (
                ("left", "ArrowRight", 16),
                ("left", "ArrowLeft", -16),
                ("right", "ArrowLeft", 16),
                ("right", "ArrowRight", -16),
            ):
                current, sub_action = self._perform_keyboard_step(
                    pid,
                    current,
                    side,
                    key,
                    delta,
                    deadline,
                    case_id,
                )
                snapshots.append(current)
                sub_actions.append(sub_action)
            layout_after = snapshots[-1]
            if _pair(layout_after, "preferred_pair") != before_pair:
                raise RuntimeQualificationError(
                    "workspace_keyboard_direction_mismatch", case_id
                )
            after = self._observe_workspace_once(
                pid, layout_after, deadline, case_id
            )
            action = {
                "kind": "keyboard",
                "separator": "left",
                "key": "ArrowRight",
                "sub_actions": sub_actions,
            }
            publication_delta = 4
        elif case_id == "minimum-overshoot-clamped":
            deadline = time.monotonic() + timeout_seconds
            current = before
            sub_actions: list[dict[str, object]] = []
            for side, drag_delta, key, value in (
                ("left", -4096, "ArrowLeft", 200),
                ("right", 4096, "ArrowRight", 184),
            ):
                current, sub_action = self._perform_boundary_subcase(
                    pid,
                    current,
                    side=side,
                    edge="minimum",
                    drag_delta_px=drag_delta,
                    clamp_key=key,
                    expected_value=value,
                    deadline=deadline,
                    operation=case_id,
                )
                sub_actions.append(sub_action)
            after = self._observe_workspace_once(
                pid, current, deadline, case_id
            )
            action = {"kind": "boundary-subcases", "sub_actions": sub_actions}
            publication_delta = 2
        elif case_id == "maximum-overshoot-clamped":
            self._resize(pid, 1600, 800)
            before = self._steady(pid, f"{case_id} wide setup", timeout_seconds)
            if before.get("layout_mode") != "three-pane":
                raise RuntimeQualificationError(
                    "workspace_maximum_clamp_mismatch", case_id
                )
            deadline = time.monotonic() + timeout_seconds
            current = before
            sub_actions = []
            current, left_action = self._perform_boundary_subcase(
                pid,
                current,
                side="left",
                edge="maximum",
                drag_delta_px=4096,
                clamp_key="ArrowRight",
                expected_value=512,
                deadline=deadline,
                operation=case_id,
            )
            sub_actions.append(left_action)
            reset_before = _pair(current, "effective_pair")
            self._drag(pid, "left", -4096)
            current = self._wait_publication_delta(
                pid, current.get("layout_revision"), deadline - time.monotonic()
            )
            reset_after = _pair(current, "effective_pair")
            if (
                reset_after["left_px"] != 200
                or reset_after["right_px"] != reset_before["right_px"]
                or _pair(current, "preferred_pair") != reset_after
            ):
                raise RuntimeQualificationError(
                    "workspace_maximum_clamp_mismatch", case_id
                )
            self.preferred_pair = dict(reset_after)
            current, right_action = self._perform_boundary_subcase(
                pid,
                current,
                side="right",
                edge="maximum",
                drag_delta_px=-4096,
                clamp_key="ArrowLeft",
                expected_value=560,
                deadline=deadline,
                operation=case_id,
            )
            sub_actions.append(right_action)
            after = self._observe_workspace_once(
                pid, current, deadline, case_id
            )
            action = {
                "kind": "boundary-subcases",
                "window_width": 1600,
                "window_height": 800,
                "between_subcases_reset": {
                    "separator": "left",
                    "before_value": reset_before["left_px"],
                    "after_value": reset_after["left_px"],
                    "opposite_before": reset_before["right_px"],
                    "opposite_after": reset_after["right_px"],
                    "publication_delta": 1,
                },
                "sub_actions": sub_actions,
            }
            publication_delta = 3
        elif case_id == "small-window-two-column":
            action = {"kind": "resize-window", "width": 1000, "height": 720, "setup_publications": 2}
            self._resize(pid, 1000, 720)
            after = self._steady(pid, case_id, timeout_seconds)
            if after.get("layout_mode") != "two-column":
                raise RuntimeQualificationError("workspace_responsive_mode_mismatch", case_id)
            self._assert_separators(after, False, case_id)
        elif case_id == "small-window-stacked":
            action = {"kind": "resize-window", "width": 820, "height": 720}
            self._resize(pid, 820, 720)
            after = self._steady(pid, case_id, timeout_seconds)
            if after.get("layout_mode") != "stacked":
                raise RuntimeQualificationError("workspace_responsive_mode_mismatch", case_id)
            self._assert_separators(after, False, case_id)
        elif case_id == "wide-window-preferred-restored":
            action = {"kind": "resize-window", "width": 1200, "height": 800}
            self._resize(pid, 1200, 800)
            after = self._steady(pid, case_id, timeout_seconds)
            if after.get("layout_mode") != "three-pane" or _pair(after, "effective_pair") != self.preferred_pair:
                raise RuntimeQualificationError("workspace_preferred_restore_mismatch", case_id)
            self._assert_separators(after, True, case_id)
        elif case_id == "graph-state-before-collapse":
            component_target = (
                f"semantic-component:{_CANONICAL_ANCHOR_COMPONENT_IDS[1]}"
            )
            action = {
                "kind": "select-graph-state",
                "transport": "ax-press-and-cg-event",
                "target": component_target,
                "zoom": "in",
                "pan_delta_x": 48,
            }
            self._press(
                pid, f"semantic-relation:profile:{_CANONICAL_PROFILE_ID}"
            )
            self._press(pid, component_target)
            self._press(pid, "graph-zoom-in")
            self._drag_target(pid, "graph-viewport", 48)
            after = self._steady(pid, case_id, timeout_seconds)
            fingerprint = after.get("graph_fingerprint")
            if not isinstance(fingerprint, dict) or len(fingerprint.get("anchor_nodes", [])) != 3:
                raise RuntimeQualificationError("graph_anchor_identity_mismatch", case_id)
            if fingerprint.get("selected_component_id") != FIXTURE_ANCHOR_COMPONENT_IDS[1] or fingerprint.get("active_profile_id") != _PROFILE_ID:
                raise RuntimeQualificationError(
                    "graph_selection_mismatch",
                    case_id,
                    evidence={
                        "expected_selected_component_id": FIXTURE_ANCHOR_COMPONENT_IDS[1],
                        "observed_selected_component_id": fingerprint.get(
                            "selected_component_id"
                        ),
                        "expected_active_profile_id": _PROFILE_ID,
                        "observed_active_profile_id": fingerprint.get(
                            "active_profile_id"
                        ),
                        "selected_relation_node_id": fingerprint.get(
                            "selected_relation_node_id"
                        ),
                    },
                )
            if self._command_counters() != {"sot_load_count": 1, "local_scan_start_count": 0}:
                raise RuntimeQualificationError("workspace_command_counter_mismatch", case_id)
            before_fingerprint = before.get("graph_fingerprint")
            if not isinstance(before_fingerprint, dict):
                raise RuntimeQualificationError("graph_fingerprint_invalid", case_id)
            try:
                scale_changed = abs(
                    float(fingerprint["scale"]) - float(before_fingerprint["scale"])
                ) > 0.001
            except (KeyError, TypeError, ValueError):
                scale_changed = False
            if not scale_changed:
                raise RuntimeQualificationError("graph_camera_action_unobserved", case_id)
            self.graph_before_collapse = fingerprint
        elif case_id == "graph-collapsed":
            self._press(pid, "matrix-toggle")
            matrix_expanded = self._steady(
                pid, f"{case_id} matrix setup", timeout_seconds
            )
            matrix_setup_height = _finite_number(
                matrix_expanded.get("matrix_usable_height"), case_id
            )
            action = {
                "kind": "toggle-graph",
                "expanded": False,
                "matrix_setup": "expanded",
                "matrix_setup_usable_height": matrix_setup_height,
                "matrix_setup_layout": matrix_expanded.get("matrix_layout"),
            }
            self._press(pid, "graph-toggle")
            after = self._steady(pid, case_id, timeout_seconds)
            if after.get("graph_body_ax_present") is not False or after.get("zoom_control_focusable") is not False:
                raise RuntimeQualificationError("graph_body_remained_accessible", case_id)
            if _finite_number(
                after.get("matrix_usable_height"), case_id
            ) <= matrix_setup_height:
                raise RuntimeQualificationError("graph_matrix_did_not_expand", case_id)
        elif case_id == "graph-expanded-state-restored":
            action = {"kind": "toggle-graph", "expanded": True}
            self._press(pid, "graph-toggle")
            after = self._steady(pid, case_id, timeout_seconds)
            if not self._graph_fingerprint_matches(self.graph_before_collapse, after.get("graph_fingerprint")):
                raise RuntimeQualificationError("graph_fingerprint_changed", case_id)
        elif case_id == "graph-hover-layout-stable":
            target = f"semantic-component:{_CANONICAL_ANCHOR_COMPONENT_IDS[1]}"
            hover_active = False
            try:
                hover_target = self._hover_graph_target(pid, target, case_id)
                hover_active = True
                during_snapshot = self._steady(
                    pid, f"{case_id} during", timeout_seconds
                )
                identity = during_snapshot.get("identity")
                if not isinstance(identity, dict):
                    raise RuntimeQualificationError(
                        "workspace_snapshot_identity_invalid", case_id
                    )
                window_id = identity.get("window_id")
                if (
                    not isinstance(window_id, int)
                    or isinstance(window_id, bool)
                    or window_id <= 0
                ):
                    raise RuntimeQualificationError(
                        "workspace_snapshot_identity_invalid", case_id
                    )
                destination = (
                    self.evidence_dir / "cases" / f"{case_id}.png"
                ).resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                capture = self._method("capture_workspace_window")(
                    pid,
                    window_id,
                    destination,
                    include_cursor=True,
                )
                if (
                    not isinstance(capture, ScreenshotCaptureEvidence)
                    or capture.capture_mode
                    != f"{SCREEN_CAPTURE_MODE}_cursor_included"
                    or not destination.is_file()
                    or destination.stat().st_size <= 0
                ):
                    raise RuntimeQualificationError(
                        "workspace_graph_hover_capture_invalid", case_id
                    )
                self.active_hover_captures[case_id] = (
                    destination,
                    ScreenshotCaptureEvidence(
                        capture_mode=(
                            f"{SCREEN_CAPTURE_MODE}_cursor_included_graph_hover_active"
                        ),
                        pixel_width=capture.pixel_width,
                        pixel_height=capture.pixel_height,
                        point_pixel_scale=capture.point_pixel_scale,
                    ),
                )
                self._leave_graph_hover(pid, case_id)
                hover_active = False
                after = self._steady(pid, f"{case_id} after", timeout_seconds)
                action = {
                    "kind": "graph-node-hover",
                    "transport": "cg-event-mouse-move",
                    "target": target,
                    "hover_target": hover_target,
                    "leave_target": "graph-viewport",
                }
            except Exception:
                if hover_active:
                    try:
                        self._leave_graph_hover(pid, case_id)
                    except Exception:
                        pass
                raise
        elif case_id == "dashboard-shared-width":
            action = {"kind": "switch-dashboard", "dashboard": "local"}
            before_pair = _pair(before, "preferred_pair")
            self._press(pid, "dashboard-local")
            after = self._steady(pid, case_id, timeout_seconds)
            if after.get("dashboard") != "local" or _pair(after, "preferred_pair") != before_pair:
                raise RuntimeQualificationError("workspace_dashboard_pair_mismatch", case_id)
        elif case_id == "relation-node-identity-hover":
            target = f"semantic-relation:profile:{_CANONICAL_PROFILE_ID}"
            hover_active = False
            try:
                hover_target = self._hover_graph_target(pid, target, case_id)
                hover_active = True
                during_snapshot = self._steady(
                    pid, f"{case_id} during", timeout_seconds
                )
                self._leave_graph_hover(pid, case_id)
                hover_active = False
                after = self._steady(pid, f"{case_id} after", timeout_seconds)
                action = {
                    "kind": "semantic-relation-hover",
                    "transport": "cg-event-mouse-move",
                    "target": target,
                    "hover_target": hover_target,
                    "leave_target": "graph-viewport",
                }
            except Exception:
                if hover_active:
                    try:
                        self._leave_graph_hover(pid, case_id)
                    except Exception:
                        pass
                raise
        elif case_id == "profile-supernode-identity-hover":
            target = f"profile:{_CANONICAL_PROFILE_ID}"
            hover_active = False
            try:
                hover_identity = self._hover_profile_target(pid, target, case_id)
                hover_active = True
                during_snapshot = self._steady(
                    pid, f"{case_id} during", timeout_seconds
                )
                self._leave_graph_hover(pid, case_id)
                hover_active = False
                after = self._steady(pid, f"{case_id} after", timeout_seconds)
                action = {
                    "kind": "profile-node-hover",
                    "transport": "cg-event-mouse-move",
                    "target": target,
                    "hover_identity": hover_identity,
                    "before_graph_layout_geometry": before.get(
                        "graph_layout_geometry"
                    ),
                    "after_graph_layout_geometry": after.get(
                        "graph_layout_geometry"
                    ),
                }
            except Exception:
                if hover_active:
                    try:
                        self._leave_graph_hover(pid, case_id)
                    except Exception:
                        pass
                raise
        elif case_id == "graph-toolbar-single-row":
            geometry = before.get("geometry")
            if not isinstance(geometry, dict):
                raise RuntimeQualificationError(
                    "graph_toolbar_contract_mismatch", case_id
                )
            window_frame = _frame(geometry.get("window"), case_id)
            center_frame = _frame(geometry.get("center"), case_id)
            target_width = round(
                window_frame["width"] + 480 - center_frame["width"]
            )
            self._resize(pid, target_width, round(window_frame["height"]))
            after = self._steady(pid, case_id, timeout_seconds)
            action = {
                "kind": "observe-toolbar",
                "target_center_width_px": 480,
                "window_width_px": target_width,
            }
        elif case_id == "sot-tree-wheel-last-row":
            geometry = before.get("geometry")
            if not isinstance(geometry, dict):
                raise RuntimeQualificationError(
                    "sot_tree_wheel_contract_mismatch", case_id
                )
            window_frame = _frame(geometry.get("window"), case_id)
            self._resize(pid, round(window_frame["width"]), 560)
            before = self._steady(pid, f"{case_id} short setup", timeout_seconds)
            action = self._wheel_tree_to_last(pid, case_id)
            after = self._steady(pid, case_id, timeout_seconds)
        elif case_id == "sot-tree-keyboard-last-row":
            action = self._key_tree_to_last(pid, case_id)
            after = self._steady(pid, case_id, timeout_seconds)
        elif case_id == "sot-tree-filter-offset-restore":
            before_offset = self._tree_offset(before, f"{case_id} before")
            filter_text = "runtime-layout-anchor-middle"
            self._set_sot_tree_filter(
                pid, filter_text, timeout_seconds, f"{case_id} set filter"
            )
            filtered = self._steady(pid, f"{case_id} filtered", timeout_seconds)
            filtered_offset = self._tree_offset(filtered, f"{case_id} filtered")
            self._set_sot_tree_filter(
                pid, "", timeout_seconds, f"{case_id} clear filter"
            )
            after = self._steady(pid, f"{case_id} restored", timeout_seconds)
            action = {
                "kind": "tree-filter-offset-restore",
                "filter_text": filter_text,
                "filter_cleared": True,
                "before_offset_px": before_offset,
                "filtered_offset_px": filtered_offset,
                "restored_offset_px": self._tree_offset(
                    after, f"{case_id} restored"
                ),
            }
        elif case_id == "sot-tree-roundtrip-offset-restored":
            tree_before = self._tree_observation(before, f"{case_id} before")
            before_offset = self._tree_offset(before, f"{case_id} before")
            before_selected = tree_before.get("selected_identity")
            before_focused = tree_before.get("focused_identity")
            self._press(pid, "dashboard-local")
            local = self._steady(pid, f"{case_id} local", timeout_seconds)
            if local.get("dashboard") != "local":
                raise RuntimeQualificationError(
                    "sot_tree_roundtrip_restore_mismatch", case_id
                )
            self._press(pid, "dashboard-sot")
            after = self._steady(pid, f"{case_id} restored", timeout_seconds)
            tree_after = self._tree_observation(after, f"{case_id} restored")
            action = {
                "kind": "tree-dashboard-roundtrip",
                "dashboard_sequence": ["sot", "local", "sot"],
                "before_offset_px": before_offset,
                "after_offset_px": self._tree_offset(
                    after, f"{case_id} restored"
                ),
                "before_selected_identity": before_selected,
                "after_selected_identity": tree_after.get("selected_identity"),
                "before_focused_identity": before_focused,
                "after_focused_identity": tree_after.get("focused_identity"),
            }
        elif case_id == "matrix-initial-collapsed-map-first-viewport":
            action = {"kind": "observe"}
            after = before
        elif case_id == "matrix-expanded-outer-scroll":
            self._press(pid, "matrix-toggle")
            expanded = self._steady(
                pid, f"{case_id} expanded", timeout_seconds
            )
            wheel = self._wheel_workbench(pid, case_id)
            after = self._steady(pid, f"{case_id} scrolled", timeout_seconds)
            action = {
                "kind": "toggle-matrix",
                "expanded": True,
                "transport": "ax-press-and-cg-event-wheel",
                "wheel": wheel,
                "expanded_before_wheel": expanded.get("matrix_layout"),
            }
        elif case_id == "matrix-collapse-selection-state-preserved":
            self._press(pid, "matrix-toggle")
            during_snapshot = self._steady(
                pid, f"{case_id} collapsed", timeout_seconds
            )
            self._press(pid, "matrix-toggle")
            after = self._steady(pid, f"{case_id} restored", timeout_seconds)
            action = {
                "kind": "matrix-collapse-expand-roundtrip",
                "transport": "ax-press",
                "press_count": 2,
            }
        elif case_id == "matrix-bounded-fluid-wrap":
            samples: list[dict[str, object]] = []
            center_inline_sizes: list[float] = []
            after = before
            for window_width in (1200, 1500, 1800):
                self._resize(pid, window_width, 800)
                sample_snapshot = self._steady(
                    pid,
                    f"{case_id} width {window_width}",
                    timeout_seconds,
                )
                sample = sample_snapshot.get("matrix_wrap_sample")
                if not isinstance(sample, dict):
                    raise RuntimeQualificationError(
                        "workspace_matrix_wrap_observation_invalid", case_id
                    )
                center_inline = _finite_number(
                    sample.get("center_inline_px"), case_id
                )
                samples.append(dict(sample))
                center_inline_sizes.append(center_inline)
                after = sample_snapshot
            after = dict(after)
            after["matrix_wrap_samples"] = samples
            action = {
                "kind": "sample-matrix-wrap",
                "window_widths_px": [1200, 1500, 1800],
                "center_inline_sizes_px": center_inline_sizes,
            }
        elif case_id == "workflow-relation-focus":
            semantic = _semantic_graph_case_observation(before, case_id)
            workflow_relations = [
                relation
                for relation in semantic["relations"]
                if isinstance(relation, dict)
                and relation.get("relation_kind") == "workflow"
            ]
            if not workflow_relations:
                raise RuntimeQualificationError(
                    "workflow_relation_focus_contract_mismatch", case_id
                )
            relation = workflow_relations[0]
            target = str(relation["dom_identifier"])
            self.pre_workflow_camera = json.loads(
                json.dumps(before.get("graph_fingerprint"))
            )
            self._press(pid, target)
            after, contextual_camera_evidence = self._observe_contextual_camera_focus(
                pid, before, case_id, timeout_seconds
            )
            action = {
                "kind": "semantic-workflow-relation-press",
                "transport": "ax-press",
                "target": target,
                "screenshot_count": 1,
                "contextual_camera_evidence": contextual_camera_evidence,
            }
        elif case_id == "workflow-step-hover-no-camera":
            semantic = _semantic_graph_case_observation(before, case_id)
            inspector = semantic.get("workflow_inspector")
            steps = inspector.get("steps") if isinstance(inspector, dict) else None
            if not isinstance(steps, list) or not steps:
                raise RuntimeQualificationError(
                    "workflow_hover_baseline_missing", case_id
                )
            target = str(steps[0]["dom_identifier"])
            hover_active = False
            try:
                hover_target = self._hover_graph_target(pid, target, case_id)
                hover_active = True
                during_snapshot = self._steady(
                    pid, f"{case_id} during", timeout_seconds
                )
                self._leave_graph_hover(pid, case_id)
                hover_active = False
                after = self._steady(pid, f"{case_id} after", timeout_seconds)
                action = {
                    "kind": "workflow-step-hover",
                    "transport": "cg-event-mouse-move",
                    "target": target,
                    "hover_target": hover_target,
                }
            except Exception:
                if hover_active:
                    try:
                        self._leave_graph_hover(pid, case_id)
                    except Exception:
                        pass
                raise
        elif case_id == "workflow-step-selection-invariant":
            semantic = _semantic_graph_case_observation(before, case_id)
            inspector = semantic.get("workflow_inspector")
            steps = inspector.get("steps") if isinstance(inspector, dict) else None
            if not isinstance(steps, list) or not steps:
                raise RuntimeQualificationError(
                    "workflow_selection_baseline_missing", case_id
                )
            target = str(steps[0]["dom_identifier"])
            click = self._click(pid, target)
            after = self._steady(pid, case_id, timeout_seconds)
            action = {
                "kind": "workflow-step-select",
                "transport": "cg-event-mouse-click",
                "target": target,
                "click": click,
                "screenshot_count": 1,
                "ordinary_selection_state_evidence": (
                    _ordinary_selection_state_evidence(
                        before.get("graph_fingerprint"),
                        after.get("graph_fingerprint"),
                    )
                ),
            }
        elif case_id == "workflow-camera-restore":
            if not isinstance(self.pre_workflow_camera, dict):
                raise RuntimeQualificationError(
                    "workflow_restore_baseline_missing", case_id
                )
            semantic = _semantic_graph_case_observation(before, case_id)
            inspector = semantic.get("workflow_inspector")
            steps = inspector.get("steps") if isinstance(inspector, dict) else None
            if not isinstance(steps, list) or not steps:
                raise RuntimeQualificationError(
                    "workflow_restore_baseline_missing", case_id
                )
            target = str(steps[0]["dom_identifier"])
            key_result = self._method("key_workspace_target_evidence")(
                pid, target, "Escape"
            )
            if not isinstance(key_result, dict) or key_result.get("accepted") is not True:
                raise RuntimeQualificationError(
                    "workspace_action_rejected", case_id
                )
            after = self._steady(pid, case_id, timeout_seconds)
            action = {
                "kind": "workflow-camera-restore",
                "transport": "cg-event-key-escape",
                "target": target,
                "key": "Escape",
                "pre_workflow_graph_fingerprint": json.loads(
                    json.dumps(self.pre_workflow_camera)
                ),
            }
        elif case_id == "workflow-matrix-state-invariant":
            semantic = _semantic_graph_case_observation(before, case_id)
            workflow_relations = [
                relation
                for relation in semantic["relations"]
                if isinstance(relation, dict)
                and relation.get("relation_kind") == "workflow"
            ]
            if not workflow_relations:
                raise RuntimeQualificationError(
                    "workflow_matrix_baseline_missing", case_id
                )
            target = str(workflow_relations[0]["dom_identifier"])
            self._press(pid, target)
            after = self._steady(pid, case_id, timeout_seconds)
            action = {
                "kind": "semantic-workflow-relation-press",
                "transport": "ax-press",
                "target": target,
            }
        elif case_id == "semantic-fallback-user-transition":
            initial_fingerprint = before.get("graph_fingerprint")
            initial_graph = _semantic_graph_case_observation(before, case_id)
            workflow_relations = sorted(
                (
                    relation
                    for relation in initial_graph["relations"]
                    if isinstance(relation, dict)
                    and relation.get("relation_kind") == "workflow"
                ),
                key=lambda relation: str(relation.get("dom_identifier", "")),
            )
            if not workflow_relations:
                raise RuntimeQualificationError(
                    "workspace_renderer_recovery_workflow_missing", case_id
                )
            workflow_relation = workflow_relations[0]
            relation_target = str(workflow_relation["dom_identifier"])
            workflow_id = str(workflow_relation["canonical_id"])
            self._press(pid, relation_target)
            relation_snapshot = self._steady(
                pid, f"{case_id} workflow setup", timeout_seconds
            )
            relation_graph = _semantic_graph_case_observation(
                relation_snapshot, case_id
            )
            inspector = relation_graph.get("workflow_inspector")
            inspector_steps = (
                inspector.get("steps") if isinstance(inspector, dict) else None
            )
            if (
                not isinstance(inspector, dict)
                or inspector.get("visible") is not True
                or inspector.get("workflow_id") != workflow_id
                or not isinstance(inspector_steps, list)
                or not inspector_steps
            ):
                raise RuntimeQualificationError(
                    "workspace_renderer_recovery_workflow_missing", case_id
                )
            step = inspector_steps[0]
            if not isinstance(step, dict):
                raise RuntimeQualificationError(
                    "workspace_renderer_recovery_step_missing", case_id
                )
            step_target = str(step.get("dom_identifier", ""))
            step_ordinal = step.get("ordinal")
            if (
                not step_target.startswith(f"workflow-inspector-step:{workflow_id}:")
                or not isinstance(step_ordinal, int)
                or isinstance(step_ordinal, bool)
            ):
                raise RuntimeQualificationError(
                    "workspace_renderer_recovery_step_missing", case_id
                )
            click = self._click(pid, step_target)
            before = self._steady(pid, f"{case_id} step setup", timeout_seconds)
            ready_fingerprint = before.get("graph_fingerprint")
            if (
                not isinstance(initial_fingerprint, dict)
                or not isinstance(ready_fingerprint, dict)
                or not _graph_fingerprint_equal(
                    ready_fingerprint, ready_fingerprint
                )
                or ready_fingerprint.get("selected_relation_node_id")
                != f"workflow:{workflow_id}"
                or ready_fingerprint.get("locked_step")
                != {"workflow_id": workflow_id, "ordinal": step_ordinal}
                or _camera_pose_equal(
                    initial_fingerprint.get("camera_pose"),
                    ready_fingerprint.get("camera_pose"),
                )
            ):
                raise RuntimeQualificationError(
                    "workspace_renderer_recovery_fingerprint_missing", case_id
                )
            self.renderer_recovery_fingerprint = json.loads(
                json.dumps(ready_fingerprint)
            )
            self._press(pid, "label:Component Map 텍스트 보기")
            after = self._steady(pid, case_id, timeout_seconds)
            action = {
                "kind": "semantic-fallback-user-transition",
                "transport": "ax-press",
                "target": "label:Component Map 텍스트 보기",
                "screenshot_count": 1,
                "pre_setup_graph_fingerprint": json.loads(
                    json.dumps(initial_fingerprint)
                ),
                "setup_actions": [
                    {
                        "kind": "semantic-workflow-relation-press",
                        "transport": "ax-press",
                        "target": relation_target,
                    },
                    {
                        "kind": "workflow-step-select",
                        "transport": "cg-event-mouse-click",
                        "target": step_target,
                        "click": click,
                    },
                ],
                "pre_fallback_ready_graph_fingerprint": json.loads(
                    json.dumps(self.renderer_recovery_fingerprint)
                ),
            }
        elif case_id == "renderer-retry-snapshot-preserved":
            ready_fingerprint = self.renderer_recovery_fingerprint
            if not _graph_fingerprint_equal(ready_fingerprint, ready_fingerprint):
                raise RuntimeQualificationError(
                    "workspace_renderer_recovery_fingerprint_missing", case_id
                )
            self._press(pid, "renderer-retry")
            after = self._steady_renderer_ready(pid, case_id, timeout_seconds)
            if not _graph_fingerprint_equal(
                ready_fingerprint, after.get("graph_fingerprint")
            ):
                raise RuntimeQualificationError(
                    "workspace_renderer_retry_fingerprint_changed", case_id
                )
            action = {
                "kind": "renderer-retry-snapshot-preserved",
                "transport": "ax-press",
                "target": "renderer-retry",
                "pre_fallback_ready_graph_fingerprint": json.loads(
                    json.dumps(ready_fingerprint)
                ),
            }
        elif case_id == "semantic-surface-workflow-parity":
            semantic = _semantic_graph_case_observation(before, case_id)
            inspector = semantic.get("workflow_inspector")
            if not isinstance(inspector, dict) or inspector.get("visible") is not True:
                raise RuntimeQualificationError(
                    "semantic_workflow_parity_mismatch", case_id
                )
            after = before
            action = {
                "kind": "compare-production-semantic-surfaces",
                "transport": "ax-observation",
                "component_roles": inspector.get("component_roles"),
                "unresolved_warning_set": inspector.get(
                    "unresolved_warning_set"
                ),
            }
        else:
            raise RuntimeQualificationError("workspace_case_invalid", case_id)
        payload = self._payload(pid, action, before, after, publication_delta)
        if during_snapshot is not None:
            payload["during"] = during_snapshot
        return payload

    def _graph_fingerprint_matches(self, expected: object, observed: object) -> bool:
        return _graph_fingerprint_equal(expected, observed)

    def wait_for_layout_publication(
        self, pid: int, timeout_seconds: float
    ) -> dict[str, object]:
        pid = self._resolve_pid(pid)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            snapshot = self._steady(pid, "layout save completion", max(0.3, deadline - time.monotonic()))
            revision = snapshot.get("layout_revision")
            if isinstance(revision, int) and snapshot.get("persisted") is True:
                return {
                    "layout_revision": revision,
                    "persisted": True,
                    "preferred_pair": dict(self.preferred_pair),
                }
            time.sleep(0.1)
        raise RuntimeQualificationError("workspace_layout_publication_unconfirmed", "layout save completion")

    def _snapshot_process_tree_before_termination(self, pid: int) -> None:
        if self.artifact is None:
            raise RuntimeQualificationError(
                "artifact_identity_missing", "workspace process snapshot"
            )
        snapshot = self._method("snapshot_workspace_processes")(pid)
        processes = snapshot.get("processes") if isinstance(snapshot, dict) else None
        helper_pids = (
            snapshot.get("known_helper_pids") if isinstance(snapshot, dict) else None
        )
        try:
            main_path = Path(str(snapshot["main_executable_path"])).resolve()
            expected_path = Path(getattr(self.artifact, "executable")).resolve()
        except (KeyError, OSError, TypeError, ValueError):
            raise RuntimeQualificationError(
                "workspace_process_snapshot_invalid", "workspace process snapshot"
            )
        if (
            snapshot.get("accepted") is not True
            or snapshot.get("main_pid") != pid
            or main_path != expected_path
            or snapshot.get("main_executable_sha256")
            != getattr(self.artifact, "executable_sha256")
            or snapshot.get("main_build_id") != getattr(self.artifact, "build_id")
            or not isinstance(processes, list)
            or not isinstance(helper_pids, list)
            or snapshot.get("known_helper_count") != len(helper_pids)
            or any(
                not isinstance(helper_pid, int)
                or isinstance(helper_pid, bool)
                or helper_pid <= 0
                for helper_pid in helper_pids
            )
            or sum(
                isinstance(process, dict)
                and process.get("pid") == pid
                and process.get("role") == "main"
                for process in processes
            )
            != 1
        ):
            raise RuntimeQualificationError(
                "workspace_process_snapshot_invalid", "workspace process snapshot"
            )
        self.known_helper_pids.update(helper_pids)

    def terminate(self, pid: int) -> bool:
        pid = self._resolve_pid(pid)
        self._snapshot_process_tree_before_termination(pid)
        accepted = self.base.terminate(pid)
        if accepted and self.base.wait_process_exit(pid, FIXED_LAYOUT_CONTRACT["case_timeout_seconds"]):
            if self.current_pid == pid:
                self.current_pid = None
            return True
        return False

    def wait_for_zero(self, timeout_seconds: float) -> dict[str, int]:
        if self.artifact is None:
            raise RuntimeQualificationError("artifact_identity_missing", "final cleanup")
        cleanup = self._method("verify_workspace_process_cleanup")(
            getattr(self.artifact, "executable"),
            tuple(sorted(self.known_helper_pids)),
            timeout_seconds,
        )
        if (
            not isinstance(cleanup, dict)
            or cleanup.get("accepted") is not True
            or cleanup.get("known_helpers_exited") is not True
            or not all(
                isinstance(cleanup.get(key), int)
                and not isinstance(cleanup.get(key), bool)
                and cleanup.get(key) >= 0
                for key in (
                    "main_process_count",
                    "main_window_count",
                    "live_known_helper_count",
                )
            )
        ):
            raise RuntimeQualificationError(
                "workspace_process_cleanup_invalid", "workspace process cleanup"
            )
        zero = {
            "main_process_count": int(cleanup["main_process_count"]),
            "main_window_count": int(cleanup["main_window_count"]),
            "helper_process_count": int(cleanup["live_known_helper_count"]),
        }
        if any(zero.values()):
            raise RuntimeQualificationError(
                "workspace_process_cleanup_nonzero", "workspace process cleanup"
            )
        self.known_helper_pids.clear()
        return zero

    def prearm_first_visible(self, artifact: object, timeout_seconds: float) -> object:
        if self.runtime_home is None:
            raise RuntimeQualificationError("runtime_home_missing", "workspace first-visible")
        self.prearmed_capture = self.evidence_dir / "cases/.restart-first-visible-prearmed.png"
        return self._method("prearm_workspace_first_visible")(
            artifact,
            self.actual_anchor_ids,
            self.prearmed_capture,
            timeout_seconds=timeout_seconds,
        )

    def observe_first_visible(
        self,
        arm: object,
        pid: int,
        expected_pair: dict[str, int],
        timeout_seconds: float,
    ) -> dict[str, object]:
        pid = self._resolve_pid(pid)
        raw = self._method("wait_workspace_first_visible")(
            arm, pid, timeout_seconds=timeout_seconds
        )
        if not isinstance(raw, dict) or raw.get("accepted") is not True:
            raise RuntimeQualificationError("first_visible_observation_failed", "restart-first-visible-persisted")
        if self.artifact is None:
            raise RuntimeQualificationError("artifact_identity_missing", "restart-first-visible-persisted")
        applications = self.base.list_applications(BUNDLE_IDENTIFIER)
        if (
            len(applications) != 1
            or applications[0].pid != pid
            or not _same_artifact(applications[0], self.artifact)  # type: ignore[arg-type]
        ):
            raise RuntimeQualificationError(
                "workspace_first_visible_artifact_identity_mismatch",
                "restart-first-visible-persisted",
            )
        first_visible = raw.get("first_visible") if isinstance(raw.get("first_visible"), dict) else raw
        normalized_first_visible = _sanitize_first_visible_evidence(
            first_visible,
            expected_pid=pid,
            expected_window_id=int(raw["window_id"]),
        )
        observation_raw = raw.get("observation")
        if not isinstance(observation_raw, dict):
            observation_raw = raw
        after = self._normalise_observation(observation_raw, "restart-first-visible-persisted")
        if _pair(after, "preferred_pair") != expected_pair:
            raise RuntimeQualificationError("first_visible_pair_mismatch", "restart-first-visible-persisted")
        first_geometry = normalized_first_visible.get("first_visible_geometry")
        if not isinstance(first_geometry, dict):
            raise RuntimeQualificationError(
                "first_visible_geometry_missing", "restart-first-visible-persisted"
            )
        first_effective = first_geometry.get("effective_pair")
        if not isinstance(first_effective, dict):
            raise RuntimeQualificationError(
                "first_visible_geometry_missing", "restart-first-visible-persisted"
            )
        try:
            effective_pair = {
                "left_px": int(first_effective["left_px"]),
                "right_px": int(first_effective["right_px"]),
            }
        except (KeyError, TypeError, ValueError):
            raise RuntimeQualificationError(
                "first_visible_geometry_missing", "restart-first-visible-persisted"
            )
        if first_geometry.get("layout_mode") != "three-pane":
            raise RuntimeQualificationError(
                "first_visible_geometry_invalid", "restart-first-visible-persisted"
            )
        first_panes_raw = first_geometry.get("panes")
        if not isinstance(first_panes_raw, dict):
            raise RuntimeQualificationError(
                "first_visible_geometry_invalid", "restart-first-visible-persisted"
            )
        first_window = _frame(
            first_geometry.get("window"), "restart-first-visible-persisted window"
        )
        first_panes = {
            side: _frame(
                first_panes_raw.get(side),
                f"restart-first-visible-persisted {side}",
            )
            for side in ("left", "center", "right")
        }
        if (
            any(
                not _frame_contains(
                    first_window,
                    frame,
                    float(FIXED_LAYOUT_CONTRACT["os_frame_tolerance_pt"]),
                )
                for frame in first_panes.values()
            )
            or any(
                _rectangles_overlap(first_panes[left], first_panes[right])
                for left, right in (
                    ("left", "center"),
                    ("left", "right"),
                    ("center", "right"),
                )
            )
            or not _same_within(
                first_panes["left"]["width"], effective_pair["left_px"]
            )
            or not _same_within(
                first_panes["right"]["width"], effective_pair["right_px"]
            )
            or first_panes["center"]["width"]
            < FIXED_LAYOUT_CONTRACT["center_min_px"]
        ):
            raise RuntimeQualificationError(
                "first_visible_geometry_invalid", "restart-first-visible-persisted"
            )
        pair_delta = {
            "left": effective_pair["left_px"] - int(expected_pair["left_px"]),
            "right": effective_pair["right_px"] - int(expected_pair["right_px"]),
        }
        if any(abs(value) > 1 for value in pair_delta.values()):
            raise RuntimeQualificationError("first_visible_pair_mismatch", "restart-first-visible-persisted")
        capture = raw.get("capture") if isinstance(raw.get("capture"), dict) else first_visible
        if isinstance(capture, dict):
            try:
                self.prearmed_capture_evidence = ScreenshotCaptureEvidence(
                    capture_mode=str(capture["capture_mode"]),
                    pixel_width=int(capture["pixel_width"]),
                    pixel_height=int(capture["pixel_height"]),
                    point_pixel_scale=float(capture["point_pixel_scale"]),
                )
            except (KeyError, TypeError, ValueError):
                raise RuntimeQualificationError("first_visible_capture_invalid", "restart-first-visible-persisted")
        return {
            **self._payload(
                pid,
                {"kind": "restart-first-visible"},
                None,
                after,
                0,
            ),
            "first_visible": normalized_first_visible | {"pair_delta_px": pair_delta},
        }

    def cancel_first_visible(self, arm: object) -> None:
        self._method("cancel_workspace_first_visible")(arm)

    def _layout_path(self) -> Path:
        if self.runtime_home is None:
            raise RuntimeQualificationError("runtime_home_missing", "layout preference")
        candidates = [
            path
            for path in self.runtime_home.rglob("layout.json")
            if not path.is_symlink() and path.is_file()
        ]
        if len(candidates) != 1:
            raise RuntimeQualificationError("layout_preference_path_unavailable", "layout preference")
        return candidates[0]

    def _relaunch_after_preference(self, body: bytes, timeout_seconds: float) -> tuple[int, dict[str, object]]:
        if self.artifact is None or self.environment is None or self.current_pid is None:
            raise RuntimeQualificationError("runtime_session_missing", "corrupt preference")
        old_pid = self.current_pid
        if not self.terminate(old_pid):
            raise RuntimeQualificationError("workspace_primary_termination_failed", "corrupt preference")
        self.wait_for_zero(timeout_seconds)
        preference = self._layout_path()
        preference.write_bytes(body)
        preference.chmod(0o600)
        pid = self.launch_primary(self.artifact, self.environment)
        snapshot = self._steady(pid, "corrupt preference", timeout_seconds)
        return pid, snapshot

    def _exercise_corrupt_preference(self, pid: int, timeout_seconds: float) -> dict[str, object]:
        before = self._steady(pid, "corrupt preference before", timeout_seconds)
        variants = (
            ("malformed-json", b"{not-json\n"),
            (
                "unknown-version",
                _canonical_json_bytes(
                    {
                        "schema_version": 999,
                        "preferred_left_width_px": 320,
                        "preferred_right_width_px": 376,
                    }
                ),
            ),
            (
                "out-of-range-pair",
                _canonical_json_bytes(
                    {
                        "schema_version": 1,
                        "preferred_left_width_px": 1,
                        "preferred_right_width_px": 9999,
                    }
                ),
            ),
        )
        subcases: list[dict[str, object]] = []
        current = pid
        after = before
        for kind, body in variants:
            current, after = self._relaunch_after_preference(body, timeout_seconds)
            observed_default = _pair(after, "effective_pair")
            if (
                observed_default != {"left_px": 304, "right_px": 368}
                or _pair(after, "preferred_pair") != observed_default
            ):
                raise RuntimeQualificationError("corrupt_preference_default_mismatch", kind)
            self.preferred_pair = observed_default
            subcases.append({"kind": kind, "preferred_pair": dict(self.preferred_pair)})
        after["corruption_subcases"] = subcases
        return self._payload(
            current,
            {"kind": "restart-with-corrupt-preference", "subcases": [kind for kind, _ in variants]},
            before,
            after,
            0,
        )

    def _exercise_write_denied(self, pid: int, timeout_seconds: float) -> dict[str, object]:
        pid = self._resolve_pid(pid)
        before = self._steady(pid, "write denied before", timeout_seconds)
        preference = self._layout_path()
        parent = preference.parent
        original_mode = parent.stat().st_mode & 0o777
        try:
            parent.chmod(0o500)
            before_effective = _pair(before, "effective_pair")
            self._drag(pid, "left", 32)
            after = self._wait_write_denied_outcome(
                pid, before_effective, timeout_seconds
            )
            if after.get("persisted") is not False:
                raise RuntimeQualificationError("write_denied_persisted_state_mismatch", "preference write denied")
            diagnostic = after.get("diagnostic")
            if not isinstance(diagnostic, dict) or diagnostic.get("code") != "workspace_layout_preference_write_failed":
                raise RuntimeQualificationError("write_denied_diagnostic_missing", "preference write denied")
            if _pair(after, "effective_pair")["left_px"] == before_effective["left_px"]:
                raise RuntimeQualificationError("write_denied_session_geometry_lost", "preference write denied")
            self._press(pid, "dashboard-local")
            local = self._steady(pid, "write denied dashboard", timeout_seconds)
            self._press(pid, "dashboard-sot")
            restored = self._steady(pid, "write denied dashboard restore", timeout_seconds)
            if local.get("dashboard") != "local" or restored.get("dashboard") != "sot":
                raise RuntimeQualificationError("write_denied_dashboard_unusable", "preference write denied")
            after = restored
            self.preferred_pair = _pair(after, "preferred_pair")
            if self.preferred_pair != _pair(after, "effective_pair"):
                raise RuntimeQualificationError(
                    "write_denied_session_geometry_lost", "preference write denied"
                )
            after["persisted"] = False
            after["diagnostic"] = diagnostic
            return self._payload(
                pid,
                {"kind": "deny-preference-write-and-drag", "separator": "left"},
                before,
                after,
                1,
            )
        finally:
            parent.chmod(original_mode)

    def capture_window(
        self,
        owner_pid: int,
        window_id: int,
        destination: Path,
        *,
        include_cursor: bool,
    ) -> ScreenshotCaptureEvidence:
        active_pid = self._resolve_pid(owner_pid)
        graph_readability_capture = self.active_graph_readability_captures.pop(
            destination.stem, None
        )
        if graph_readability_capture is not None:
            captured_path, evidence = graph_readability_capture
            if (
                include_cursor is not False
                or captured_path.resolve() != destination.resolve()
                or not destination.is_file()
                or evidence.capture_mode != SCREEN_CAPTURE_MODE
            ):
                raise RuntimeQualificationError(
                    "workspace_graph_readability_capture_invalid",
                    destination.stem,
                )
            return evidence
        active_drag_capture = self.active_drag_captures.pop(destination.stem, None)
        if active_drag_capture is not None:
            captured_path, evidence = active_drag_capture
            if (
                include_cursor is not True
                or captured_path.resolve() != destination.resolve()
                or not destination.is_file()
                or evidence.capture_mode
                != f"{SCREEN_CAPTURE_MODE}_cursor_included_drag_active"
            ):
                raise RuntimeQualificationError(
                    "workspace_drag_capture_invalid", destination.stem
                )
            return evidence
        active_hover_capture = self.active_hover_captures.pop(
            destination.stem, None
        )
        if active_hover_capture is not None:
            captured_path, evidence = active_hover_capture
            if (
                include_cursor is not True
                or captured_path.resolve() != destination.resolve()
                or not destination.is_file()
                or evidence.capture_mode
                != f"{SCREEN_CAPTURE_MODE}_cursor_included_graph_hover_active"
            ):
                raise RuntimeQualificationError(
                    "workspace_graph_hover_capture_invalid", destination.stem
                )
            return evidence
        if destination.stem == "restart-first-visible-persisted":
            if self.prearmed_capture is None or not self.prearmed_capture.is_file():
                raise RuntimeQualificationError("first_visible_capture_missing", destination.stem)
            shutil.copy2(self.prearmed_capture, destination)
            self.prearmed_capture.unlink(missing_ok=True)
            if self.prearmed_capture_evidence is None:
                raise RuntimeQualificationError("first_visible_capture_invalid", destination.stem)
            return self.prearmed_capture_evidence
        self._clear_checkout_path_for_capture(active_pid, destination.stem)
        workspace_capture = getattr(self.base, "capture_workspace_window", None)
        if callable(workspace_capture):
            return workspace_capture(
                active_pid, window_id, destination, include_cursor=include_cursor
            )
        if include_cursor:
            raise RuntimeQualificationError("cursor_capture_api_missing", destination.stem)
        return self.base.capture_window(active_pid, window_id, destination)


def _read_regular_json(path: Path, code: str) -> tuple[dict[str, object], bytes]:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not path.is_file() or metadata.st_size > 16 * 1024 * 1024:
            raise OSError
        body = path.read_bytes()
        payload = json.loads(body)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        raise RuntimeQualificationError(code, "runtime suite index")
    if not isinstance(payload, dict):
        raise RuntimeQualificationError(code, "runtime suite index")
    return payload, body


def _qualification_for_suite(
    qualification_report: Path,
) -> tuple[dict[str, object], dict[str, str], bytes]:
    report, body = _read_regular_json(
        qualification_report, "runtime_suite_package_report_invalid"
    )
    build = report.get("build_identity")
    if not isinstance(build, dict) or report.get("status") != "passed":
        raise RuntimeQualificationError(
            "runtime_suite_package_report_invalid", "runtime suite index"
        )
    identity: dict[str, str] = {
        "app_source_manifest_sha256": str(build.get("app_source_manifest_sha256", "")),
        "build_inputs_sha256": str(report.get("build_inputs_sha256", "")),
        "bundle_manifest_sha256": str(report.get("bundle_manifest_sha256", "")),
        "report_sha256": _sha256_bytes(body),
        "source_identity_sha256": str(build.get("source_identity_sha256", "")),
    }
    if any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in identity.values()
    ):
        raise RuntimeQualificationError(
            "runtime_suite_package_identity_invalid", "runtime suite index"
        )
    build_id = build.get("build_id")
    bundle_identifier = build.get("bundle_identifier")
    if (
        not isinstance(build_id, str)
        or len(build_id) != 32
        or any(character not in "0123456789abcdef" for character in build_id)
        or bundle_identifier != BUNDLE_IDENTIFIER
    ):
        raise RuntimeQualificationError(
            "runtime_suite_package_identity_invalid", "runtime suite index"
        )
    return report, identity, body


def _installed_package_for_suite(
    package_report: Path,
    qualification_report: Path,
    qualification: dict[str, object],
) -> tuple[dict[str, str], bytes]:
    try:
        resolved_package = package_report.resolve(strict=True)
        resolved_qualification = qualification_report.resolve(strict=True)
    except OSError:
        raise RuntimeQualificationError(
            "runtime_suite_installed_package_report_invalid", "runtime suite index"
        )
    if (
        package_report.is_symlink()
        or resolved_package.name != "package-report.json"
        or resolved_package.parent != resolved_qualification.parent
    ):
        raise RuntimeQualificationError(
            "runtime_suite_installed_package_report_invalid", "runtime suite index"
        )
    package, package_body = _read_regular_json(
        resolved_package, "runtime_suite_installed_package_report_invalid"
    )
    build = qualification.get("build_identity")
    chain = package.get("install_evidence_chain")
    if (
        not isinstance(build, dict)
        or package.get("schema_version") != 1
        or package.get("status") != "passed"
        or package.get("artifact_strategy") != "apple-silicon-arm64-only"
        or package.get("target") != "aarch64-apple-darwin"
        or package.get("product_name") != "HarnessKit"
        or package.get("bundle_identifier") != BUNDLE_IDENTIFIER
        or package.get("build_id") != build.get("build_id")
        or package.get("standalone_app_manifest_sha256")
        != qualification.get("bundle_manifest_sha256")
        or package.get("installed_runtime_state") != "Passed"
        or package.get("installed_icon_evidence") != "Passed"
        or not isinstance(chain, dict)
        or set(chain) != {"run_report", "run_report_sha256"}
    ):
        raise RuntimeQualificationError(
            "runtime_suite_installed_package_report_invalid", "runtime suite index"
        )
    run_report_value = chain.get("run_report")
    expected_run_hash = chain.get("run_report_sha256")
    if (
        not isinstance(run_report_value, str)
        or not 1 <= len(run_report_value) <= 4096
        or not Path(run_report_value).is_absolute()
        or not isinstance(expected_run_hash, str)
        or len(expected_run_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_run_hash)
    ):
        raise RuntimeQualificationError(
            "runtime_suite_install_evidence_chain_invalid", "runtime suite index"
        )
    installed_report_path = Path(run_report_value)
    installed, installed_body = _read_regular_json(
        installed_report_path, "runtime_suite_install_evidence_chain_invalid"
    )
    installed_identity = installed.get("package_identity")
    installed_executable = "/Applications/HarnessKit.app/Contents/MacOS/harness-desktop"
    if (
        _sha256_bytes(installed_body) != expected_run_hash
        or installed.get("schema_version") != 1
        or installed.get("status") != "passed"
        or installed.get("final_installed_app_retained") is not True
        or installed.get("installed_executable") != installed_executable
        or installed.get("catalog_surface") not in {"launchpad", "spotlight_apps"}
        or not isinstance(installed_identity, dict)
        or installed_identity.get("bundle_identifier") != BUNDLE_IDENTIFIER
        or installed_identity.get("package_build_id") != build.get("build_id")
        or installed_identity.get("bundle_manifest_sha256")
        != qualification.get("bundle_manifest_sha256")
    ):
        raise RuntimeQualificationError(
            "runtime_suite_install_evidence_chain_invalid", "runtime suite index"
        )
    return (
        {
            "catalog_surface": str(installed["catalog_surface"]),
            "installed_executable": installed_executable,
            "run_report_sha256": expected_run_hash,
            "status": "passed",
        },
        package_body,
    )


def write_runtime_suite_index(
    evidence_root: Path,
    qualification_report: Path,
    installed_package_report: Path,
    single_instance_report: Path,
    workspace_report: Path,
) -> Path:
    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    index_path = evidence_root / "runtime-suite-index.json"
    package_report_sha256 = ""
    build_id: str | None = None
    package_identity: dict[str, str] | None = None
    installed_runtime: dict[str, str] | None = None
    installed_package_report_sha256 = ""
    bundle_identifier: str | None = None
    executable_sha256: str | None = None
    reports: dict[str, object] = {}
    error: RuntimeQualificationError | None = None

    try:
        qualification, package_identity, package_body = _qualification_for_suite(
            qualification_report
        )
        package_report_sha256 = _sha256_bytes(package_body)
        package_build = qualification["build_identity"]
        assert isinstance(package_build, dict)
        build_id = str(package_build["build_id"])
        bundle_identifier = str(package_build["bundle_identifier"])
        installed_runtime, installed_package_body = _installed_package_for_suite(
            installed_package_report,
            qualification_report,
            qualification,
        )
        installed_package_report_sha256 = _sha256_bytes(installed_package_body)

        for label, report_path, expected_relative in (
            (
                "single_instance",
                single_instance_report,
                "runtime-single-instance/run-report.json",
            ),
            (
                "workspace_layout",
                workspace_report,
                "runtime-workspace-layout/run-report.json",
            ),
        ):
            try:
                resolved = report_path.resolve(strict=True)
                relative = resolved.relative_to(evidence_root).as_posix()
            except (OSError, ValueError):
                raise RuntimeQualificationError(
                    f"runtime_suite_{label}_report_missing", "runtime suite index"
                )
            if relative != expected_relative or report_path.is_symlink():
                raise RuntimeQualificationError(
                    f"runtime_suite_{label}_report_location_invalid",
                    "runtime suite index",
                )
            runtime_report, runtime_body = _read_regular_json(
                resolved, f"runtime_suite_{label}_report_invalid"
            )
            artifact = runtime_report.get("artifact")
            runtime_package = runtime_report.get("package_qualification")
            if not isinstance(artifact, dict):
                raise RuntimeQualificationError(
                    f"runtime_suite_{label}_artifact_missing", "runtime suite index"
                )
            if runtime_report.get("status") != "passed":
                raise RuntimeQualificationError(
                    f"runtime_suite_{label}_not_passed", "runtime suite index"
                )
            runtime_bundle_identifier = artifact.get("bundle_identifier")
            if runtime_bundle_identifier is None:
                runtime_bundle_identifier = runtime_report.get("bundle_identifier")
            if (
                artifact.get("build_id") != build_id
                or runtime_bundle_identifier != bundle_identifier
                or artifact.get("installed_path_verified") is not True
            ):
                raise RuntimeQualificationError(
                    f"runtime_suite_{label}_build_identity_mismatch",
                    "runtime suite index",
                )
            runtime_executable_sha256 = artifact.get("executable_sha256")
            if (
                not isinstance(runtime_executable_sha256, str)
                or len(runtime_executable_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in runtime_executable_sha256
                )
            ):
                raise RuntimeQualificationError(
                    f"runtime_suite_{label}_executable_identity_invalid",
                    "runtime suite index",
                )
            if executable_sha256 is None:
                executable_sha256 = runtime_executable_sha256
            elif executable_sha256 != runtime_executable_sha256:
                raise RuntimeQualificationError(
                    "runtime_suite_executable_identity_mismatch", "runtime suite index"
                )
            if runtime_package != package_identity:
                raise RuntimeQualificationError(
                    f"runtime_suite_{label}_package_identity_mismatch",
                    "runtime suite index",
                )
            case_count: int | None = None
            if label == "workspace_layout":
                runtime_cases = runtime_report.get("cases")
                runtime_fixture = runtime_report.get("fixture")
                picker_evidence = (
                    runtime_fixture.get("picker_evidence")
                    if isinstance(runtime_fixture, dict)
                    else None
                )
                if not _workspace_picker_evidence_valid(picker_evidence):
                    raise RuntimeQualificationError(
                        "runtime_suite_workspace_picker_evidence_invalid",
                        "runtime suite index",
                    )
                lifecycle = runtime_report.get("lifecycle")
                picker_termination = (
                    lifecycle.get("picker_open_termination")
                    if isinstance(lifecycle, dict)
                    else None
                )
                picker_panel = (
                    picker_termination.get("panel")
                    if isinstance(picker_termination, dict)
                    else None
                )
                termination_elapsed_ms = (
                    picker_termination.get("termination_elapsed_ms")
                    if isinstance(picker_termination, dict)
                    else None
                )
                if (
                    not isinstance(picker_termination, dict)
                    or picker_termination.get("empty_path_triggered") is not True
                    or picker_termination.get("native_panel_open_confirmed") is not True
                    or picker_termination.get("normal_terminate_accepted") is not True
                    or picker_termination.get("main_process_absent") is not True
                    or picker_termination.get("main_window_absent") is not True
                    or picker_termination.get("observed_helpers_exited") is not True
                    or not isinstance(termination_elapsed_ms, int)
                    or isinstance(termination_elapsed_ms, bool)
                    or termination_elapsed_ms < 0
                    or not isinstance(picker_panel, dict)
                    or not _native_picker_panel_proof_valid(picker_panel)
                    or picker_panel.get("accepted") is not True
                    or picker_panel.get("kind") != "native-directory-panel-open"
                    or picker_panel.get("panel_count") != 1
                    or picker_panel.get("path_redacted") is not True
                    or picker_panel.get("title_redacted") is not True
                    or "/" in json.dumps(picker_panel)
                    or "title" in json.dumps(picker_panel).replace("title_redacted", "")
                ):
                    raise RuntimeQualificationError(
                        "runtime_suite_workspace_picker_termination_invalid",
                        "runtime suite index",
                    )
                expected_cases = [
                    {"case_id": case_id, "status": "Passed"}
                    for case_id in CASE_IDS
                ]
                if (
                    not isinstance(runtime_cases, list)
                    or [
                        {
                            "case_id": case.get("case_id"),
                            "status": case.get("status"),
                        }
                        for case in runtime_cases
                        if isinstance(case, dict)
                    ]
                    != expected_cases
                    or len(runtime_cases) != len(CASE_IDS)
                ):
                    raise RuntimeQualificationError(
                        "runtime_suite_workspace_layout_cases_invalid",
                        "runtime suite index",
                    )
                initial_identity = runtime_cases[0].get("identity")
                final_identity = runtime_cases[-1].get("identity")
                initial_pid = (
                    initial_identity.get("pid")
                    if isinstance(initial_identity, dict)
                    else None
                )
                final_pid = (
                    final_identity.get("terminated_primary_pid")
                    if isinstance(final_identity, dict)
                    else None
                )
                fixture_panel = (
                    picker_evidence.get("panel")
                    if isinstance(picker_evidence, dict)
                    else None
                )
                fixture_selection = (
                    picker_evidence.get("selection")
                    if isinstance(picker_evidence, dict)
                    else None
                )
                if (
                    not isinstance(initial_pid, int)
                    or isinstance(initial_pid, bool)
                    or initial_pid <= 0
                    or not isinstance(final_pid, int)
                    or isinstance(final_pid, bool)
                    or final_pid <= 0
                    or not isinstance(fixture_panel, dict)
                    or fixture_panel.get("pid") != initial_pid
                    or not isinstance(fixture_selection, dict)
                    or fixture_selection.get("pid") != initial_pid
                    or not isinstance(picker_panel, dict)
                    or picker_panel.get("pid") != final_pid
                ):
                    raise RuntimeQualificationError(
                        "runtime_suite_workspace_picker_pid_mismatch",
                        "runtime suite index",
                    )
                case_count = len(runtime_cases)
            reports[label] = {
                "path": relative,
                "sha256": _sha256_bytes(runtime_body),
                "status": "passed",
                **({"case_count": case_count} if case_count is not None else {}),
            }
    except RuntimeQualificationError as failure:
        error = failure
        if not package_report_sha256:
            try:
                package_body = qualification_report.read_bytes()
                package_report_sha256 = _sha256_bytes(package_body)
                package_payload = json.loads(package_body)
                package_build = package_payload.get("build_identity", {})
                if isinstance(package_build, dict):
                    candidate = package_build.get("build_id")
                    build_id = candidate if isinstance(candidate, str) else None
            except (OSError, json.JSONDecodeError, TypeError):
                pass

    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "failed" if error is not None else "passed",
        "build_id": build_id,
        "bundle_identifier": bundle_identifier,
        "executable_sha256": executable_sha256,
        "package_report_sha256": package_report_sha256,
        "installed_package_report_sha256": installed_package_report_sha256,
        "installed_runtime": installed_runtime,
        "package_qualification": package_identity,
        "reports": reports,
        "claim_boundaries": {
            "divider_cursor": "static bundle contract proves col-resize; packaged ScreenCaptureKit proves cursor inclusion and AX proves drag-active state because public macOS APIs expose no stable cursor kind",
        },
        "redactions": ["absolute_paths", "raw_logs", "secrets", "source_bodies"],
    }
    if error is not None:
        payload["error"] = {"code": error.code, "operation": error.operation}
    _write_json(index_path, payload)
    return index_path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify packaged harness-desktop workspace layout and graph runtime behavior."
    )
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--qualification-report", type=Path, required=True)
    parser.add_argument("--package-report", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--single-instance-report", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    report_path = verify_workspace_layout_macos(
        arguments.app,
        arguments.evidence_root,
        qualification_report=arguments.qualification_report,
        timeout_seconds=arguments.timeout_seconds,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    suite_path = write_runtime_suite_index(
        arguments.evidence_root,
        arguments.qualification_report,
        arguments.package_report,
        arguments.single_instance_report,
        report_path,
    )
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    print(json.dumps({"report": str(report_path), "status": report["status"]}, sort_keys=True))
    if report["status"] == "passed" and suite.get("status") == "passed":
        return 0
    if report["status"] == "blocked":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
