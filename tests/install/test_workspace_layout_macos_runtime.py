from __future__ import annotations

import hashlib
import json
import plistlib
import subprocess
from collections import deque
from pathlib import Path
from typing import Callable

import pytest
import yaml
import jsonschema

from scripts.package import verify_workspace_layout_macos as runtime
from scripts.package import verify_single_instance_macos as shared_runtime
from scripts.profiles.selection import (
    PROFILE_WORKFLOW_MEMBERSHIP_FORBIDDEN,
    ProfileSelectionError,
    validate_profile_selection,
)


BUILD_ID = "a" * 32
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
EXTENDED_CASE_IDS = CASE_IDS[17:39]
FIXTURE_ANCHOR_COMPONENT_IDS = (
    "runtime-layout-anchor-left",
    "runtime-layout-anchor-middle",
    "runtime-layout-anchor-right",
)
HOVER_COMPONENT_ID = FIXTURE_ANCHOR_COMPONENT_IDS[1]
CANONICAL_HOVER_COMPONENT_ID = f"harnesskit.skill.{HOVER_COMPONENT_ID}"
CANONICAL_PROFILE_ID = "harnesskit.profile.runtime-layout-profile"
SCENE_PROJECTION_ID = "graph:runtime-layout-snapshot:runtime-layout-seed"
SETTLED_NODE_POSITIONS_HASH = "0123456789abcdef"
SEMANTIC_CASE_IDS = CASE_IDS[17:23]
GRAPH_READABILITY_CASE_IDS = CASE_IDS[23:27]
MATRIX_CASE_IDS = CASE_IDS[27:31]
WORKFLOW_CASE_IDS = CASE_IDS[31:36]
RENDERER_RESILIENCE_CASE_IDS = CASE_IDS[36:39]
CANONICAL_FIXTURE_KINDS = {
    "skill",
    "agent",
    "workflow",
    "hook",
    "rule",
    "command",
    "composite",
}
GRAPH_READABILITY_KIND_TOKENS = (
    "skill",
    "agent",
    "hook",
    "rule",
    "command",
    "composite",
)
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


def _amendment_payload(case_id: str) -> dict[str, object]:
    """Independent expected truth for promoted packaged amendment cases."""

    base = {
        "case_id": case_id,
        "proof_level": "macos-ax-cgevent-sck",
        "limits": {"ax_target_count": 18, "screenshot_count": 1, "elapsed_ms": 1200},
    }
    if case_id in {
        "pane-left-collapse-reopen-restart",
        "pane-right-collapse-reopen-restart",
    }:
        side = "left" if "left" in case_id else "right"
        other = "right" if side == "left" else "left"
        baseline = 304 if side == "left" else 368
        return {
            **base,
            "action": {"kind": "pane-collapse-reopen-restart", "side": side},
            "observation": {
                "initial_preferred_width_px": baseline,
                "collapsed": {
                    "collapsed_side": side,
                    "other_side": other,
                    "pane_ax_present": False,
                    "pane_geometry_present": False,
                    "divider_ax_present": False,
                    "divider_geometry_present": False,
                    "center_width_before_px": 508,
                    "center_width_after_px": 824 if side == "left" else 888,
                },
                "restart": {"collapsed_side": side, "persisted": True},
                "reopened": {
                    "collapsed_side": None,
                    "preferred_width_px": baseline,
                    "pane_ax_present": True,
                    "divider_ax_present": True,
                },
            },
        }
    if case_id == "typography-preset-restart":
        return {
            **base,
            "action": {"kind": "set-typography-preset-and-restart"},
            "observation": {
                "presets": [
                    {"preset": "Small", "tokens_px": [11, 12, 13, 15], "persisted_after_restart": True},
                    {"preset": "Default", "tokens_px": [12, 13, 14, 16], "persisted_after_restart": True},
                    {"preset": "Large", "tokens_px": [13, 14, 15, 17], "persisted_after_restart": True},
                ]
            },
        }
    if case_id == "toolbar-width-preset-matrix":
        rows = []
        for width, layout in ((1200, "two-row"), (960, "two-row"), (760, "three-row")):
            for preset in ("Small", "Default", "Large"):
                rows.append(
                    {
                        "window_width_px": width,
                        "preset": preset,
                        "layout": layout,
                        "overlap_count": 0,
                        "crop_count": 0,
                        "tab_order": [
                            "left-disclosure",
                            "repository",
                            "checkout-register",
                            "load-sot",
                            "appearance",
                            "typography",
                            "right-disclosure",
                        ],
                        "target_frames": {
                            "left-disclosure": {"x": 8, "y": 8, "width": 32, "height": 32},
                            "identity": {"x": 48, "y": 8, "width": 88, "height": 20},
                            "repository": {"x": 144, "y": 48 if layout == "three-row" else 8, "width": 180, "height": 32},
                            "checkout-register": {"x": 332, "y": 48 if layout == "three-row" else 8, "width": 96, "height": 32},
                            "load-sot": {"x": 436, "y": 48 if layout == "three-row" else 8, "width": 96, "height": 32},
                            "appearance": {"x": 332, "y": 88 if layout == "three-row" else 48, "width": 64, "height": 32},
                            "typography": {"x": 404, "y": 88 if layout == "three-row" else 48, "width": 96, "height": 32},
                            "right-disclosure": {"x": 508, "y": 88 if layout == "three-row" else 48, "width": 32, "height": 32},
                        },
                    }
                )
        return {**base, "action": {"kind": "measure-toolbar-matrix"}, "observation": {"rows": rows}}
    if case_id == "local-all-locations-alignment":
        return {
            **base,
            "action": {"kind": "open-all-locations"},
            "observation": {
                "label": "전체 위치",
                "visible_ax_target": True,
                "role": "AXButton",
                "frame_height_px": 36,
            },
        }
    if case_id == "local-selection-mouse-scroll-preserved":
        return {
            **base,
            "action": {"kind": "select-local-result", "input": "mouse"},
            "observation": {"scroll_value_before": 0.42, "scroll_value_after": 0.42, "selection_changed": True},
        }
    if case_id == "local-selection-keyboard-nearest-reveal":
        return {
            **base,
            "action": {"kind": "select-local-result", "input": "keyboard"},
            "observation": {
                "scroll_value_before": 0.42,
                "scroll_value_after": 0.53,
                "target_frame": {"y": 400, "height": 36},
                "viewport_frame": {"y": 240, "height": 240},
                "nearest_reveal": True,
            },
        }
    if case_id == "local-ignore-save-rescan-retains-complete":
        return {
            **base,
            "action": {"kind": "save-ignore-and-rescan"},
            "observation": {
                "outcome": "accepted",
                "last_complete_snapshot_before": "snapshot-a",
                "last_complete_snapshot_during": "snapshot-a",
                "last_complete_snapshot_after": "snapshot-b",
                "optimistic_removal_count": 0,
                "editor_text_in_evidence": False,
            },
        }
    if case_id == "local-correlation-badge-inspector-exact":
        return {
            **base,
            "action": {"kind": "inspect-correlation"},
            "observation": {
                "state": "uncorrelated",
                "badge": None,
                "inspector": "Correlation 확인된 SoT 연결 없음",
            },
        }
    if case_id == "local-markdown-preview-safety":
        return {
            **base,
            "identity": {
                "pid": 101,
                "window_id": 42,
                "bundle_identifier": runtime.BUNDLE_IDENTIFIER,
                "build_id": BUILD_ID,
                "executable_sha256": "e" * 64,
            },
            "action": {
                "kind": "reopen-complex-markdown-preview",
                "transport": "cg-event-mouse-click-with-bounded-observers",
                "fixture_display_name": "runtime-verification",
                "selection_window_id": 42,
            },
            "observation": {
                "preview_window_id": 42,
                "preview_present": True,
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
                "app_web_area_identity_before": {
                    "role": "AXWebArea",
                    "url_class": "tauri-app-local",
                    "window_id": 42,
                },
                "app_web_area_identity_after": {
                    "role": "AXWebArea",
                    "url_class": "tauri-app-local",
                    "window_id": 42,
                },
                "network_interval": {
                    "process_scope": "verified-main-and-webkit-helper-descendants",
                    "collector_id": "macos_process_connect_trace_v1",
                    "collector_ready_before_action": True,
                    "event_stream": True,
                    "lossless_connect_attempts": True,
                    "overflow_detected": False,
                    "sequence_gap_detected": False,
                    "audit_token_attribution": True,
                    "short_lived_failed_attempt_coverage": True,
                    "observed_connect_attempt_count": 0,
                    "verified_webkit_roles": [
                        "webkit_networking",
                        "webkit_web_content",
                    ],
                    "duration_milliseconds": 1000,
                },
                "structured_evidence_negative_scan": {
                    "forbidden_literal_count": 0,
                    "prohibited_key_count": 0,
                    "absolute_path_count": 0,
                    "checked_literal_count": 7,
                    "prohibited_key_name_count": 6,
                },
            },
        }
    raise AssertionError(case_id)


def _fixture_app(tmp_path: Path) -> Path:
    app = tmp_path / "harness-desktop.app"
    executable = app / "Contents/MacOS/harness-desktop"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(
        b"fixture-main\0HARNESS_PACKAGE_BUILD_ID=" + BUILD_ID.encode() + b"\n"
    )
    executable.chmod(0o755)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": runtime.BUNDLE_IDENTIFIER,
                "CFBundleExecutable": "harness-desktop",
                "CFBundleShortVersionString": "0.1.0",
            },
            sort_keys=True,
        )
    )
    return app


def _fixture_qualification(tmp_path: Path, app: Path) -> Path:
    evidence = tmp_path / "package-evidence" / BUILD_ID
    evidence.mkdir(parents=True)
    executable = app / "Contents/MacOS/harness-desktop"
    manifest = [
        {
            "path": "Contents/MacOS/harness-desktop",
            "type": "file",
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "size": executable.stat().st_size,
            "mode": 0o755,
        }
    ]
    manifest_body = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (evidence / "bundle-manifest.json").write_bytes(manifest_body)
    report = {
        "status": "passed",
        "build_identity": {
            "app_source_manifest_sha256": "b" * 64,
            "source_identity_sha256": "c" * 64,
            "build_id": BUILD_ID,
            "bundle_identifier": runtime.BUNDLE_IDENTIFIER,
            "version": "0.1.0",
        },
        "build_inputs_sha256": "d" * 64,
        "bundle_manifest_sha256": hashlib.sha256(manifest_body).hexdigest(),
    }
    report_path = evidence / "qualification-report.json"
    report_path.write_text(json.dumps(report, sort_keys=True) + "\n")
    return report_path


def _fixture_installed_package_report(
    tmp_path: Path, qualification_report: Path
) -> Path:
    qualification = json.loads(qualification_report.read_text())
    build = qualification["build_identity"]
    installed_report = tmp_path / "installed-runtime" / BUILD_ID / "run-report.json"
    installed_report.parent.mkdir(parents=True, exist_ok=True)
    installed_payload = {
        "catalog_surface": "spotlight_apps",
        "final_installed_app_retained": True,
        "installed_executable": "/Applications/HarnessKit.app/Contents/MacOS/harness-desktop",
        "package_identity": {
            "bundle_identifier": runtime.BUNDLE_IDENTIFIER,
            "bundle_manifest_sha256": qualification["bundle_manifest_sha256"],
            "package_build_id": build["build_id"],
        },
        "schema_version": 1,
        "status": "passed",
    }
    installed_report.write_text(json.dumps(installed_payload, sort_keys=True) + "\n")
    package_report = qualification_report.with_name("package-report.json")
    package_report.write_text(
        json.dumps(
            {
                "artifact_strategy": "apple-silicon-arm64-only",
                "build_id": build["build_id"],
                "bundle_identifier": runtime.BUNDLE_IDENTIFIER,
                "install_evidence_chain": {
                    "run_report": str(installed_report.resolve()),
                    "run_report_sha256": hashlib.sha256(
                        installed_report.read_bytes()
                    ).hexdigest(),
                },
                "installed_icon_evidence": "Passed",
                "installed_runtime_state": "Passed",
                "product_name": "HarnessKit",
                "schema_version": 1,
                "standalone_app_manifest_sha256": qualification[
                    "bundle_manifest_sha256"
                ],
                "status": "passed",
                "target": "aarch64-apple-darwin",
            },
            sort_keys=True,
        )
        + "\n"
    )
    return package_report


def _three_pane_geometry(
    left: int, right: int, *, window_width: int = 1200
) -> dict[str, object]:
    center = window_width - left - right - (2 * 12)
    return {
        "window": {"x": 0, "y": 0, "width": window_width, "height": 800},
        "shell": {"x": 0, "y": 0, "width": window_width, "height": 720},
        "left": {"x": 0, "y": 0, "width": left, "height": 720},
        "left_separator": {"x": left, "y": 0, "width": 12, "height": 720},
        "center": {"x": left + 12, "y": 0, "width": center, "height": 720},
        "right_separator": {
            "x": left + 12 + center,
            "y": 0,
            "width": 12,
            "height": 720,
        },
        "right": {
            "x": left + 24 + center,
            "y": 0,
            "width": right,
            "height": 720,
        },
    }


def _two_column_geometry() -> dict[str, object]:
    return {
        "window": {"x": 0, "y": 0, "width": 1000, "height": 720},
        "shell": {"x": 0, "y": 0, "width": 1000, "height": 720},
        "left": {"x": 0, "y": 0, "width": 304, "height": 720},
        "left_separator": {"x": 304, "y": 0, "width": 0, "height": 0},
        "center": {"x": 304, "y": 0, "width": 696, "height": 440},
        "right_separator": {"x": 1000, "y": 0, "width": 0, "height": 0},
        "right": {"x": 304, "y": 440, "width": 696, "height": 280},
    }


def _stacked_geometry() -> dict[str, object]:
    return {
        "window": {"x": 0, "y": 0, "width": 820, "height": 720},
        "shell": {"x": 0, "y": 0, "width": 820, "height": 720},
        "left": {"x": 0, "y": 0, "width": 820, "height": 200},
        "left_separator": {"x": 0, "y": 200, "width": 0, "height": 0},
        "center": {"x": 0, "y": 200, "width": 820, "height": 300},
        "right_separator": {"x": 0, "y": 500, "width": 0, "height": 0},
        "right": {"x": 0, "y": 500, "width": 820, "height": 220},
    }


def _graph_fingerprint() -> dict[str, object]:
    return {
        "projection_id": SCENE_PROJECTION_ID,
        "settled_node_positions_hash": SETTLED_NODE_POSITIONS_HASH,
        "selected_component_id": FIXTURE_ANCHOR_COMPONENT_IDS[1],
        "right_detail_id": FIXTURE_ANCHOR_COMPONENT_IDS[1],
        "active_profile_id": "runtime-layout-profile",
        "selected_workflow_id": None,
        "camera_pose": {
            "position": {"x": 0.0, "y": 0.0, "z": 500.0},
            "target": {"x": 0.0, "y": 0.0, "z": 0.0},
        },
        "scale": 1.25,
        "anchor_nodes": [
            {
                "component_id": FIXTURE_ANCHOR_COMPONENT_IDS[0],
                "frame": {"x": 30, "y": 90, "width": 24, "height": 24},
            },
            {
                "component_id": FIXTURE_ANCHOR_COMPONENT_IDS[1],
                "frame": {"x": 280, "y": 180, "width": 32, "height": 32},
            },
            {
                "component_id": FIXTURE_ANCHOR_COMPONENT_IDS[2],
                "frame": {"x": 525, "y": 275, "width": 24, "height": 24},
            },
        ],
        "sot_load_count": 1,
        "local_scan_start_count": 0,
        "disclosures": {
            "component_map": True,
            "profile_matrix": False,
            "left_pane": False,
            "right_pane": False,
        },
        "graph_body_identity": "component-map-body",
    }


def _ordinary_selection_state_evidence(
    fingerprint: dict[str, object],
) -> dict[str, object]:
    camera = {
        "camera_pose": fingerprint["camera_pose"],
        "scale": fingerprint["scale"],
    }
    anchors = fingerprint["anchor_nodes"]
    return {
        "camera_before": json.loads(json.dumps(camera)),
        "camera_after": json.loads(json.dumps(camera)),
        "camera_preserved": True,
        "anchor_nodes_before": json.loads(json.dumps(anchors)),
        "anchor_nodes_after": json.loads(json.dumps(anchors)),
        "anchor_nodes_preserved": True,
    }


def _fit_report_observation(
    *, relation_count: int = 2, component_count: int = 8
) -> dict[str, object]:
    identity_count = relation_count + component_count
    return {
        "identity_count": identity_count,
        "relation_count": relation_count,
        "component_count": component_count,
        "envelope_count": identity_count,
        "camera_status": "complete",
        "in_frustum_envelope_count": identity_count,
        "total_envelope_count": identity_count,
        "largest_dimension_occupancy": 0.88,
        "scene_bounds": {
            "min": {"x": -100.0, "y": -20.0, "z": -4.0},
            "max": {"x": 100.0, "y": 20.0, "z": 5.2},
        },
    }


def _semantic_graph_observation(
    *,
    active_profile_id: str = CANONICAL_PROFILE_ID,
    selected_workflow_id: str | None = None,
    selected_component_id: str | None = CANONICAL_HOVER_COMPONENT_ID,
    locked_ordinal: int | None = None,
    focused_relation_id: str | None = None,
    selected_profile: bool = True,
    camera_scale: float = 1.0,
    camera_position: tuple[float, float, float] = (0.0, 0.0, 500.0),
    camera_target: tuple[float, float, float] = (0.0, 0.0, 0.0),
    renderer_availability: str = "ready",
) -> dict[str, object]:
    workflow_id = "harnesskit.workflow.runtime-layout-workflow"
    component_values = [
        (f"harnesskit.skill.{FIXTURE_ANCHOR_COMPONENT_IDS[0]}", "skill"),
        (CANONICAL_HOVER_COMPONENT_ID, "skill"),
        (f"harnesskit.skill.{FIXTURE_ANCHOR_COMPONENT_IDS[2]}", "skill"),
        *[
            (f"harnesskit.{kind}.runtime-layout-{kind}", kind)
            for kind in ("agent", "hook", "rule", "command", "composite")
        ],
    ]

    def ax_record(
        identifier: str,
        name: str,
        *,
        selected: bool = False,
        focused: bool = False,
        role: str = "AXButton",
        index: int = 0,
    ) -> dict[str, object]:
        return {
            "dom_identifier": identifier,
            "role": role,
            "name": name,
            "selected": selected,
            "focused": focused,
            "visible": True,
            "frame": {
                "x": 360 + (index % 6) * 56,
                "y": 120 + (index // 6) * 48,
                "width": 44,
                "height": 36,
            },
        }

    profile_node_id = f"profile:{CANONICAL_PROFILE_ID}"
    workflow_node_id = f"workflow:{workflow_id}"
    relations = [
        {
            **ax_record(
                f"semantic-relation:{profile_node_id}",
                f"Relation {profile_node_id}",
                selected=selected_workflow_id is None and selected_profile,
                focused=focused_relation_id == profile_node_id,
            ),
            "node_id": profile_node_id,
            "canonical_id": CANONICAL_PROFILE_ID,
            "relation_kind": "profile",
            "exact_count": 3,
            "member_component_ids": [value[0] for value in component_values[:3]],
        },
        {
            **ax_record(
                f"semantic-relation:{workflow_node_id}",
                f"Relation {workflow_node_id}",
                selected=selected_workflow_id == workflow_id,
                focused=focused_relation_id == workflow_node_id,
                index=1,
            ),
            "node_id": workflow_node_id,
            "canonical_id": workflow_id,
            "relation_kind": "workflow",
            "exact_count": 2,
            "member_component_ids": [CANONICAL_HOVER_COMPONENT_ID],
        },
    ]
    components = [
        {
            **ax_record(
                f"semantic-component:{component_id}",
                f"Component {component_id}",
                selected=component_id == selected_component_id,
                index=index + 2,
            ),
            "node_id": f"component:{component_id}",
            "component_id": component_id,
            "kind": kind,
        }
        for index, (component_id, kind) in enumerate(component_values)
    ]
    workflow_steps = [
        {
            **ax_record(
                f"semantic-workflow-step:{workflow_id}:{ordinal}:step-{ordinal}",
                f"Workflow step {workflow_id} {ordinal}",
                selected=locked_ordinal == ordinal,
                index=ordinal + 12,
            ),
            "node_id": workflow_node_id,
            "workflow_id": workflow_id,
            "ordinal": ordinal,
            "step_id": f"step-{ordinal}",
        }
        for ordinal in (1, 2)
    ]
    occurrence_values = [
        {
            "workflow_id": workflow_id,
            "ordinal": ordinal,
            "component_id": CANONICAL_HOVER_COMPONENT_ID,
        }
        for ordinal in (1, 2)
    ]
    component_roles = [
        {"component_id": CANONICAL_HOVER_COMPONENT_ID, "role": "skill"}
    ]
    renderer_ready = renderer_availability == "ready"
    retry_record = ax_record(
        "component-map-renderer-retry",
        "Component Map renderer retry",
    )
    renderer = {
        "availability": renderer_availability,
        "semantic_primary_visible": not renderer_ready,
        "semantic_view_frame": {
            "x": 340,
            "y": 104,
            "width": 456 if not renderer_ready else 1,
            "height": 248 if not renderer_ready else 1,
        },
        "status": ax_record(
            "component-map-renderer-status",
            (
                "사용자가 텍스트 보기를 선택했습니다."
                if not renderer_ready
                else "3D renderer ready"
            ),
            role="AXStaticText",
        ),
        "status_present": True,
        "status_visible": True,
        "retry_present": not renderer_ready,
        "retry_visible": not renderer_ready,
    }
    if not renderer_ready:
        renderer["retry"] = retry_record

    return {
        "projection_id": SCENE_PROJECTION_ID,
        "settled_node_positions_hash": SETTLED_NODE_POSITIONS_HASH,
        "active_profile_id": active_profile_id,
        "relations": relations,
        "components": components,
        "workflow_steps": workflow_steps,
        "fit_report": _fit_report_observation(
            relation_count=len(relations), component_count=len(components)
        ),
        "identity_overlay": {
            "container_present": True,
            "title_present": True,
            "kind_present": True,
            "count_present": True,
            "container": ax_record(
                "component-map-identity-overlay",
                "Identity overlay",
                role="AXGroup",
            ),
            "title": ax_record(
                "component-map-identity-title",
                "Runtime Layout Workflow" if selected_workflow_id else "Runtime Layout Profile",
                role="AXStaticText",
            ),
            "kind": ax_record(
                "component-map-identity-kind",
                "WORKFLOW" if selected_workflow_id else "PROFILE",
                role="AXStaticText",
            ),
            "count": ax_record(
                "component-map-identity-count",
                "2 steps" if selected_workflow_id else "3 components",
                role="AXStaticText",
            ),
        },
        "renderer": renderer,
        "camera": {
            "ready": renderer_ready,
            "position": dict(zip(("x", "y", "z"), camera_position, strict=True)),
            "target": dict(zip(("x", "y", "z"), camera_target, strict=True)),
            "scale": camera_scale if renderer_ready else None,
            "level": (
                "최대"
                if camera_scale == 8.0
                else "개요"
                if camera_scale == 1.0
                else "상세"
            ) if renderer_ready else None,
            "zoom_out_enabled": renderer_ready and camera_scale > 0.75,
            "fit_enabled": renderer_ready,
            "zoom_in_enabled": renderer_ready and camera_scale < 8.0,
        },
        "workflow_inspector": {
            "visible": selected_workflow_id is not None,
            "workflow_id": selected_workflow_id,
            "runtime_state": (
                "authored definition · runtime 미구현"
                if selected_workflow_id
                else None
            ),
            "steps": [
                {
                    **ax_record(
                        f"workflow-inspector-step:{workflow_id}:{ordinal}:step-{ordinal}",
                        f"Workflow step {workflow_id} {ordinal}",
                        selected=locked_ordinal == ordinal,
                        index=ordinal + 20,
                    ),
                    "workflow_id": workflow_id,
                    "ordinal": ordinal,
                }
                for ordinal in (1, 2)
            ] if selected_workflow_id else [],
            "ordinal_occurrences": occurrence_values if selected_workflow_id else [],
            "component_roles": component_roles if selected_workflow_id else [],
            "unresolved_warning_set": [] if selected_workflow_id else [],
        },
    }


def _graph_viewport_pixel_proof(
    *,
    appearance: str,
    zoom_state: str,
    expected_kind_counts: dict[str, int],
    selected_component_id: str | None,
) -> dict[str, object]:
    selected_kind = (
        selected_component_id.split(".", 2)[1]
        if selected_component_id is not None
        else None
    )
    selected_evidence = (
        None
        if selected_component_id is None
        else {
            "component_id": selected_component_id,
            "kind_token": selected_kind,
            "body_frame": {"x": 200, "y": 90, "width": 40, "height": 40},
            "mark_frame": {"x": 215, "y": 105, "width": 10, "height": 10},
            "mark_detected": True,
            "mark_center_delta_css_px": {"x": 0.0, "y": 0.0},
            "mark_center_tolerance_css_px": 4.0,
            "selection_halo_detected": True,
            "selection_halo_pixel_count": 32,
            "selection_halo_sides": {
                "bottom": True,
                "left": True,
                "right": True,
                "top": True,
            },
            "selection_underlay_detected": True,
            "selection_underlay_pixel_count": 48,
            "selection_underlay_sides": {
                "bottom": True,
                "left": True,
                "right": True,
                "top": True,
            },
            "appearance": appearance,
        }
    )
    relation_tier_evidence = (
        {
            "tier": "featured_relation",
            "region_count": 2,
            "body_pixel_count": 240,
            "outline_pixel_count": 96,
            "body_rgb": [71, 85, 105],
            "outline_rgb": [203, 213, 225],
        }
        if zoom_state == "fit"
        else None
    )
    central_component_volume_evidence = (
        {
            "present": True,
            "body_pixel_count": 480,
            "frame": {"x": 118, "y": 32, "width": 220, "height": 180},
            "central_band_frame": {
                "x": 114,
                "y": 0,
                "width": 228,
                "height": 248,
            },
        }
        if zoom_state == "fit"
        else None
    )
    return {
        "proof_scope": "viewport_pixels",
        "node_frame_authority": "none",
        "exact_identity_count_authority": (
            "typed_projection_semantic_identity_set"
        ),
        "pixel_claim_scope": (
            "shared_neutral_material_presence_crop_utilization_relation_tier_central_volume"
            if zoom_state == "fit"
            else "shared_neutral_material_selected_mark_selection_halo"
        ),
        "appearance": appearance,
        "zoom_state": (
            "direct" if zoom_state == "direct-select" else zoom_state
        ),
        "viewport_frame": {"x": 340, "y": 104, "width": 456, "height": 248},
        "viewport_pixel_frame": {
            "x": 680,
            "y": 208,
            "width": 912,
            "height": 496,
        },
        "estimated_background_rgb": (
            [240, 244, 252] if appearance == "light" else [15, 23, 42]
        ),
        "expected_kind_counts": expected_kind_counts,
        "shared_neutral_material_evidence": {
            "body_pixel_count": 240,
            "outline_pixel_count": 96,
            "body_rgb": [71, 85, 105],
            "outline_rgb": [203, 213, 225],
        },
        "relation_tier_evidence": relation_tier_evidence,
        "central_component_volume_evidence": central_component_volume_evidence,
        "node_extent_frame": {"x": 20, "y": 12, "width": 410, "height": 220},
        "largest_dimension_occupancy": 0.9,
        "node_crop_detected": False,
        "selected_evidence": selected_evidence,
    }


def _graph_readability_observation(case_id: str) -> dict[str, object]:
    appearance, zoom_state = {
        "3d-atlas-fit-light": ("light", "fit"),
        "3d-atlas-fit-dark": ("dark", "fit"),
        "3d-component-direct-select": ("light", "direct-select"),
        "3d-component-maximum-zoom": ("light", "maximum"),
        "graph-readability-fit-light": ("light", "fit"),
        "graph-readability-fit-dark": ("dark", "fit"),
        "graph-readability-direct-select": ("light", "direct-select"),
        "graph-readability-maximum-zoom": ("light", "maximum"),
    }[case_id]
    semantic_component_ids = sorted(
        f"harnesskit.{kind}.readability-{kind}-{ordinal}"
        for kind in GRAPH_READABILITY_KIND_TOKENS
        for ordinal in range(2)
    )
    selected_component_id = (
        "harnesskit.agent.readability-agent-0"
        if zoom_state != "fit"
        else None
    )
    expected_kind_counts = {
        kind: 2 for kind in GRAPH_READABILITY_KIND_TOKENS
    }
    relation_ids = sorted(
        (
            f"profile:{CANONICAL_PROFILE_ID}",
            "workflow:harnesskit.workflow.runtime-layout-workflow",
        )
    )
    snapshot_identity = {
        "component_ids": semantic_component_ids,
        "relation_ids": relation_ids,
        "kind_tokens": sorted(GRAPH_READABILITY_KIND_TOKENS),
        "active_profile_id": "runtime-layout-profile",
        "sot_load_count": 1,
        "local_scan_start_count": 0,
        "graph_body_identity": "component-map-body",
        "snapshot_id_sha256": hashlib.sha256(
            b"snapshot-runtime-layout"
        ).hexdigest(),
    }
    return {
        "appearance": appearance,
        "zoom_state": zoom_state,
        "graph_scale": {
            "fit": 1.0,
            "direct-select": 2.0,
            "maximum": 8.0,
        }[zoom_state],
        "graph_lod": {
            "fit": "overview",
            "direct-select": "select",
            "maximum": "detail",
        }[zoom_state],
        "snapshot_identity": snapshot_identity,
        "snapshot_identity_sha256": hashlib.sha256(
            runtime._canonical_json_bytes(snapshot_identity)
        ).hexdigest(),
        "ax_before_sha256": "a" * 64,
        "ax_after_sha256": "a" * 64,
        "component_inline_title_count": 0,
        "profile_identity_visible": zoom_state == "fit",
        "unprofiled_identity_visible": zoom_state == "fit",
        "required_kind_tokens": sorted(GRAPH_READABILITY_KIND_TOKENS),
        "semantic_component_ids": semantic_component_ids,
        "expected_kind_counts": expected_kind_counts,
        "viewport_pixel_proof": _graph_viewport_pixel_proof(
            appearance=appearance,
            zoom_state=zoom_state,
            expected_kind_counts=expected_kind_counts,
            selected_component_id=selected_component_id,
        ),
        "selected_component_id": selected_component_id,
        "direct_selection_confirmed": zoom_state != "fit",
        "non_color_selection_cue": zoom_state != "fit",
        "maximum_reached": zoom_state == "maximum",
        "screenshot_count": 1,
    }


def _graph_layout_geometry() -> dict[str, object]:
    return {
        "viewport": {"x": 340, "y": 104, "width": 456, "height": 248},
        "center": {"x": 316, "y": 0, "width": 504, "height": 720},
        "matrix": {"x": 340, "y": 384, "width": 456, "height": 280},
    }


def _matrix_layout_observation(
    *,
    expanded: bool,
    scroll_top_px: int = 0,
    center_width_px: int = 504,
) -> dict[str, object]:
    center_height_px = 720
    first_viewport_y = -scroll_top_px
    body_height_px = 640 if expanded else 0
    body_y = first_viewport_y + center_height_px
    scroll_height_px = center_height_px + body_height_px
    return {
        "disclosures": {
            "component_map": {
                "expanded": True,
                "body_present": True,
                "focus_target_count": 4,
            },
            "profile_matrix": {
                "expanded": expanded,
                "body_present": expanded,
                "focus_target_count": 8 if expanded else 0,
            },
        },
        "center_client_frame": {
            "x": 316,
            "y": 0,
            "width": center_width_px,
            "height": center_height_px,
        },
        "first_viewport_frame": {
            "x": 316,
            "y": first_viewport_y,
            "width": center_width_px,
            "height": center_height_px,
        },
        "graph_viewport_frame": {
            "x": 340,
            "y": 104 + first_viewport_y,
            "width": center_width_px - 48,
            "height": 520,
        },
        "matrix_header_frame": {
            "x": 316,
            "y": 640 + first_viewport_y,
            "width": center_width_px,
            "height": 80,
        },
        "matrix_body_frame": (
            {
                "x": 316,
                "y": body_y,
                "width": center_width_px,
                "height": body_height_px,
            }
            if expanded
            else None
        ),
        "outer_scroll": {
            "owner": "workbench",
            "scroll_top_px": scroll_top_px,
            "scroll_height_px": scroll_height_px,
            "client_height_px": center_height_px,
            "positive_nested_scroll_range_count": 0,
        },
        "selected_component_id": CANONICAL_HOVER_COMPONENT_ID,
        "active_profile_id": CANONICAL_PROFILE_ID,
        "owning_profile_ids": [CANONICAL_PROFILE_ID],
        "right_detail_id": CANONICAL_HOVER_COMPONENT_ID,
        "camera": {
            "available": True,
            "scale": 1.25,
            "translate_x": 48,
            "translate_y": 0,
            "anchor_nodes": [],
        },
    }


def _collapsed_workbench_layout_observation() -> dict[str, object]:
    return {
        "stable_ids": {
            "center": "workbench",
            "first_viewport": "sot-workbench-first-viewport",
            "matrix_header": "profile-matrix",
            "matrix_body": "profile-matrix-body",
        },
        "component_map_expanded": False,
        "profile_matrix_expanded": True,
        "center_frame": {
            "x": 316,
            "y": 0,
            "width": 504,
            "height": 720,
        },
        "first_viewport_frame": {
            "x": 316,
            "y": 0,
            "width": 504,
            "height": 144,
        },
        "matrix_header_frame": {
            "x": 316,
            "y": 64,
            "width": 504,
            "height": 80,
        },
        "matrix_body_frame": {
            "x": 316,
            "y": 144,
            "width": 504,
            "height": 640,
        },
    }


def _matrix_wrap_samples() -> list[dict[str, object]]:
    def cards(
        widths: list[int],
        *,
        columns: int,
        row_height: int,
        gap: int,
        title_font_px: float,
        metadata_font_px: float,
    ) -> list[dict[str, object]]:
        frames: list[dict[str, object]] = []
        for index, width in enumerate(widths):
            row, column = divmod(index, columns)
            x = column * (width + gap)
            y = row * (row_height + gap)
            frames.append(
                {
                    "identity": f"card-{index}",
                    "frame": {
                        "x": x,
                        "y": y,
                        "width": width,
                        "height": row_height,
                    },
                    "title_font_px": title_font_px,
                    "metadata_font_px": metadata_font_px,
                    "content_top_inset_px": 11.52,
                }
            )
        return frames

    return [
        {
            "center_inline_px": 504,
            "root_font_px": 16,
            "profile_column_count": 2,
            "member_column_count": 2,
            "profile_cards": cards(
                [230] * 5,
                columns=2,
                row_height=72,
                gap=10,
                title_font_px=15.0,
                metadata_font_px=12.0,
            ),
            "member_cards": cards(
                [230] * 6,
                columns=2,
                row_height=64,
                gap=10,
                title_font_px=15.0,
                metadata_font_px=12.0,
            ),
        },
        {
            "center_inline_px": 804,
            "root_font_px": 16,
            "profile_column_count": 3,
            "member_column_count": 3,
            "profile_cards": cards(
                [240] * 5,
                columns=3,
                row_height=72,
                gap=10,
                title_font_px=15.0,
                metadata_font_px=12.0,
            ),
            "member_cards": cards(
                [250] * 6,
                columns=3,
                row_height=64,
                gap=10,
                title_font_px=15.0,
                metadata_font_px=12.0,
            ),
        },
        {
            "center_inline_px": 1104,
            "root_font_px": 16,
            "profile_column_count": 5,
            "member_column_count": 4,
            "profile_cards": cards(
                [210] * 5,
                columns=5,
                row_height=72,
                gap=10,
                title_font_px=15.0,
                metadata_font_px=12.0,
            ),
            "member_cards": cards(
                [216] * 6,
                columns=4,
                row_height=64,
                gap=10,
                title_font_px=15.0,
                metadata_font_px=12.0,
            ),
        },
    ]


def _profile_supernode_observation() -> dict[str, object]:
    return {
        "profile_body_frames": [
            {
                "node_id": CANONICAL_PROFILE_ID,
                "type": "Profile",
                "name": "Runtime Layout Profile",
                "member_count": 3,
                "active": True,
                "frame": {"x": 360, "y": 120, "width": 112, "height": 112},
            },
            {
                "node_id": "unprofiled",
                "type": "Unprofiled",
                "name": "Unprofiled",
                "member_count": 1,
                "active": False,
                "frame": {"x": 640, "y": 120, "width": 112, "height": 112},
            },
        ],
        "component_body_frames": [
            {
                "component_id": f"harnesskit.skill.{component_id}",
                "frame": {
                    "x": 420 + (index * 72),
                    "y": 268,
                    "width": 32,
                    "height": 32,
                },
            }
            for index, component_id in enumerate(FIXTURE_ANCHOR_COMPONENT_IDS)
        ],
        "component_node_unique": True,
        "membership_edge_count": 4,
        "membership_owner_count": 4,
    }


def _graph_toolbar_observation() -> dict[str, object]:
    return {
        "viewport_width_px": 480,
        "legend_frame": {"x": 340, "y": 84, "width": 300, "height": 36},
        "camera_frame": {"x": 640, "y": 84, "width": 180, "height": 36},
        "toolbar_frame": {"x": 340, "y": 84, "width": 480, "height": 36},
        "graph_body_frame": {"x": 340, "y": 84, "width": 480, "height": 300},
        "matrix_frame": {"x": 340, "y": 384, "width": 480, "height": 280},
        "camera_controls": [
            {"name": "축소", "visible": True, "focusable": True},
            {"name": "Fit", "visible": True, "focusable": True},
            {"name": "확대", "visible": True, "focusable": True},
        ],
        "legend_scroll_width_px": 360,
        "legend_client_width_px": 300,
    }


def _sot_tree_observation(*, focused: bool = True) -> dict[str, object]:
    last_identity = "harnesskit.composite.runtime-layout-last"
    return {
        "inventory_count": 87,
        "outer_frame": {"x": 0, "y": 120, "width": 304, "height": 480},
        "scroll_frame": {"x": 0, "y": 200, "width": 304, "height": 400},
        "scroll_top_px": 1200,
        "scroll_height_px": 1600,
        "client_height_px": 400,
        "last_row": {
            "identity": last_identity,
            "visible": True,
            "focusable": True,
            "focused": focused,
            "frame": {"x": 12, "y": 568, "width": 280, "height": 28},
        },
        "filter_value": "",
        "selected_identity": last_identity,
        "focused_identity": last_identity if focused else None,
    }


def _separator_state(*, enabled: bool = True) -> dict[str, object]:
    return {
        "left": {
            "role": "AXSplitter",
            "name": "왼쪽 탐색 패널 너비 조절",
            "focusable": enabled,
            "disabled": not enabled,
        },
        "right": {
            "role": "AXSplitter",
            "name": "오른쪽 상세 패널 너비 조절",
            "focusable": enabled,
            "disabled": not enabled,
        },
    }


def _case_payload(
    case_id: str,
    *,
    pid: int,
    executable_sha256: str,
) -> dict[str, object]:
    before_left = 304
    before_right = 368
    after_left = before_left
    after_right = before_right
    effective_left = after_left
    effective_right = after_right
    window_width = 1200
    mode = "three-pane"
    publication_delta = 0
    action: dict[str, object] = {"kind": "observe"}
    separator_state = _separator_state()
    graph = _graph_fingerprint()
    matrix_height = 320
    graph_body_present = True
    zoom_focusable = True
    dashboard = "sot"
    persisted = True
    diagnostic: dict[str, str] | None = None
    graph_readability: dict[str, object] | None = None
    workflow_id = "harnesskit.workflow.runtime-layout-workflow"

    if case_id == "left-pointer-drag":
        after_left = 328
        effective_left = 328
        publication_delta = 1
        action = {"kind": "pointer-drag", "separator": "left", "delta_px": 32}
    elif case_id == "right-pointer-drag":
        after_right = 336
        effective_right = 336
        publication_delta = 1
        action = {"kind": "pointer-drag", "separator": "right", "delta_px": 32}
    elif case_id == "separator-keyboard-step":
        after_left = 320
        effective_left = 320
        publication_delta = 1
        action = {"kind": "keyboard", "separator": "left", "key": "ArrowRight"}
    elif case_id == "minimum-overshoot-clamped":
        after_left = 200
        after_right = 184
        effective_left = 200
        effective_right = 184
        publication_delta = 2
        action = {"kind": "boundary-subcases"}
    elif case_id == "maximum-overshoot-clamped":
        after_left = 200
        after_right = 560
        effective_left = 200
        effective_right = 560
        window_width = 1600
        publication_delta = 3
        action = {
            "kind": "boundary-subcases",
            "window_width": 1600,
            "window_height": 800,
        }
    elif case_id == "small-window-two-column":
        mode = "two-column"
        effective_left = 304
        effective_right = 696
        separator_state = _separator_state(enabled=False)
        action = {"kind": "resize-window", "width": 1000, "height": 720}
    elif case_id == "small-window-stacked":
        mode = "stacked"
        effective_left = 820
        effective_right = 820
        separator_state = _separator_state(enabled=False)
        action = {"kind": "resize-window", "width": 820, "height": 720}
    elif case_id == "wide-window-preferred-restored":
        after_left = 320
        after_right = 376
        effective_left = 320
        effective_right = 376
        action = {"kind": "resize-window", "width": 1200, "height": 800}
    elif case_id == "graph-state-before-collapse":
        target = f"semantic-component:{CANONICAL_HOVER_COMPONENT_ID}"
        action = {
            "kind": "select-graph-state",
            "transport": "ax-press-and-cg-event",
            "target": target,
        }
    elif case_id == "graph-collapsed":
        action = {
            "kind": "toggle-graph",
            "expanded": False,
            "matrix_setup": "expanded",
            "matrix_setup_usable_height": 0,
            "matrix_setup_layout": _matrix_layout_observation(expanded=True),
        }
        matrix_height = 610
        graph_body_present = False
        zoom_focusable = False
    elif case_id == "graph-expanded-state-restored":
        action = {"kind": "toggle-graph", "expanded": True}
    elif case_id == "graph-hover-layout-stable":
        action = {
            "kind": "graph-node-hover",
            "transport": "cg-event-mouse-move",
            "target": f"semantic-component:{CANONICAL_HOVER_COMPONENT_ID}",
            "hover_target": {
                "target": f"semantic-component:{CANONICAL_HOVER_COMPONENT_ID}",
                "component_id": CANONICAL_HOVER_COMPONENT_ID,
                "role": "AXButton",
                "visible": True,
                "frame": {"x": 620, "y": 284, "width": 32, "height": 32},
                "pointer_point": {"x": 636, "y": 300},
            },
            "leave_target": "graph-viewport",
        }
    elif case_id == "dashboard-shared-width":
        after_left = 320
        after_right = 376
        effective_left = 320
        effective_right = 376
        dashboard = "local"
        action = {"kind": "switch-dashboard", "dashboard": "local"}
    elif case_id == "corrupt-preference-default-pair":
        action = {
            "kind": "restart-with-corrupt-preference",
            "subcases": ["malformed-json", "unknown-version", "out-of-range-pair"],
        }
    elif case_id == "preference-write-denied-session-usable":
        after_left = 320
        effective_left = 320
        persisted = False
        diagnostic = {
            "code": "workspace_layout_preference_write_failed",
            "message": "레이아웃 설정 저장 실패",
        }
        action = {"kind": "deny-preference-write-and-drag", "separator": "left"}
    elif case_id == "relation-node-identity-hover":
        target = f"semantic-relation:profile:{CANONICAL_PROFILE_ID}"
        action = {
            "kind": "semantic-relation-hover",
            "transport": "cg-event-mouse-move",
            "target": target,
        }
    elif case_id == "profile-supernode-identity-hover":
        profile_frame = {"x": 360, "y": 120, "width": 112, "height": 112}
        action = {
            "kind": "profile-node-hover",
            "transport": "cg-event-mouse-move",
            "target": f"profile:{CANONICAL_PROFILE_ID}",
            "hover_identity": {
                "node_id": CANONICAL_PROFILE_ID,
                "type": "Profile",
                "name": "Runtime Layout Profile",
                "member_count": 3,
                "active": True,
                "visible": True,
                "frame": profile_frame,
            },
            "before_graph_layout_geometry": _graph_layout_geometry(),
            "after_graph_layout_geometry": _graph_layout_geometry(),
        }
    elif case_id == "graph-toolbar-single-row":
        action = {"kind": "observe-toolbar"}
    elif case_id == "sot-tree-wheel-last-row":
        action = {"kind": "tree-scroll", "transport": "cg-event-wheel"}
    elif case_id == "sot-tree-keyboard-last-row":
        action = {"kind": "tree-scroll", "transport": "cg-event-key-end"}
    elif case_id == "sot-tree-filter-offset-restore":
        action = {
            "kind": "tree-filter-offset-restore",
            "filter_text": "runtime-layout",
            "filter_cleared": True,
            "before_offset_px": 920,
            "filtered_offset_px": 0,
            "restored_offset_px": 920,
        }
    elif case_id == "sot-tree-roundtrip-offset-restored":
        selected_identity = CANONICAL_HOVER_COMPONENT_ID
        action = {
            "kind": "tree-dashboard-roundtrip",
            "before_offset_px": 920,
            "after_offset_px": 920,
            "before_selected_identity": selected_identity,
            "after_selected_identity": selected_identity,
            "before_focused_identity": selected_identity,
            "after_focused_identity": selected_identity,
            "dashboard_sequence": ["sot", "local", "sot"],
        }
    elif case_id in GRAPH_READABILITY_CASE_IDS or case_id in {
        "graph-readability-fit-light",
        "graph-readability-fit-dark",
        "graph-readability-direct-select",
        "graph-readability-maximum-zoom",
    }:
        graph_readability = _graph_readability_observation(case_id)
        action = {
            "kind": "capture-graph-readability",
            "appearance": graph_readability["appearance"],
            "zoom_state": graph_readability["zoom_state"],
            "maximum_probe_stable": (
                graph_readability["zoom_state"] == "maximum"
            ),
        }
        if graph_readability["zoom_state"] == "direct-select":
            action["contextual_camera_evidence"] = {
                "origin": "graph",
                "intent": "contextual",
                "observation_mode": "endpoint-only-ax",
                "baseline": {
                    "position": {"x": 0.0, "y": 0.0, "z": 500.0},
                    "target": {"x": 0.0, "y": 0.0, "z": 0.0},
                },
                "settled": {
                    "position": {"x": 42.0, "y": -18.0, "z": 240.0},
                    "target": {"x": 6.0, "y": 4.0, "z": 0.0},
                },
                "camera_changed": True,
                "anchor_hash_before": "a" * 64,
                "anchor_hash_after": "a" * 64,
            }
    elif case_id == "matrix-expanded-outer-scroll":
        action = {
            "kind": "toggle-matrix",
            "expanded": True,
            "transport": "ax-press-and-cg-event-wheel",
        }
    elif case_id == "matrix-collapse-selection-state-preserved":
        action = {
            "kind": "matrix-collapse-expand-roundtrip",
            "transport": "ax-press",
        }
    elif case_id == "matrix-bounded-fluid-wrap":
        action = {
            "kind": "sample-matrix-wrap",
            "center_inline_sizes_px": [504, 804, 1104],
        }
    elif case_id == "workflow-relation-focus":
        action = {
            "kind": "semantic-workflow-relation-press",
            "transport": "ax-press",
            "target": f"semantic-relation:workflow:{workflow_id}",
            "screenshot_count": 1,
            "contextual_camera_evidence": {
                "origin": "graph",
                "intent": "contextual",
                "observation_mode": "endpoint-only-ax",
                "baseline": {
                    "position": {"x": 0.0, "y": 0.0, "z": 500.0},
                    "target": {"x": 0.0, "y": 0.0, "z": 0.0},
                },
                "settled": {
                    "position": {"x": 32.0, "y": -12.0, "z": 260.0},
                    "target": {"x": 4.0, "y": 3.0, "z": 0.0},
                },
                "camera_changed": True,
                "anchor_hash_before": "b" * 64,
                "anchor_hash_after": "b" * 64,
            },
        }
    elif case_id == "workflow-step-hover-no-camera":
        action = {
            "kind": "workflow-step-hover",
            "transport": "cg-event-mouse-move",
        }
    elif case_id == "workflow-step-selection-invariant":
        action = {
            "kind": "workflow-step-select",
            "transport": "cg-event-mouse-click",
            "screenshot_count": 1,
            "ordinary_selection_state_evidence": _ordinary_selection_state_evidence(graph),
        }
    elif case_id == "workflow-camera-restore":
        action = {
            "kind": "workflow-camera-restore",
            "transport": "cg-event-key-escape",
            "pre_workflow_graph_fingerprint": json.loads(json.dumps(graph)),
        }
    elif case_id == "workflow-matrix-state-invariant":
        action = {
            "kind": "semantic-workflow-relation-press",
            "transport": "ax-press",
        }
    elif case_id == "semantic-fallback-user-transition":
        relation_target = f"semantic-relation:workflow:{workflow_id}"
        step_target = f"workflow-inspector-step:{workflow_id}:1:step-1"
        action = {
            "kind": "semantic-fallback-user-transition",
            "transport": "ax-press",
            "target": "label:Component Map 텍스트 보기",
            "screenshot_count": 1,
            "pre_setup_graph_fingerprint": json.loads(json.dumps(graph)),
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
                    "click": {
                        "accepted": True,
                        "point": {"x": 420, "y": 220},
                    },
                },
            ],
        }
    elif case_id == "renderer-retry-snapshot-preserved":
        action = {
            "kind": "renderer-retry-snapshot-preserved",
            "transport": "ax-press",
            "target": "renderer-retry",
        }
    elif case_id == "semantic-surface-workflow-parity":
        action = {
            "kind": "compare-production-semantic-surfaces",
            "transport": "ax-observation",
            "component_roles": [
                {"component_id": CANONICAL_HOVER_COMPONENT_ID, "role": "skill"}
            ],
            "unresolved_warning_set": [],
        }
    elif case_id == "final-cleanup-zero":
        action = {"kind": "terminate-and-observe-zero"}

    before = {
        "layout_mode": "three-pane",
        "preferred_pair": {"left_px": before_left, "right_px": before_right},
        "effective_pair": {"left_px": before_left, "right_px": before_right},
        "geometry": _three_pane_geometry(
            before_left, before_right, window_width=window_width
        ),
        "separator_state": _separator_state(),
        "dashboard": "sot",
        "graph_fingerprint": graph,
        "graph_body_ax_present": True,
        "zoom_control_focusable": True,
        "matrix_usable_height": 320,
        "semantic_graph": _semantic_graph_observation(),
    }
    after = {
        "layout_mode": mode,
        "preferred_pair": {"left_px": after_left, "right_px": after_right},
        "effective_pair": {"left_px": effective_left, "right_px": effective_right},
        "geometry": (
            _two_column_geometry()
            if mode == "two-column"
            else _stacked_geometry()
            if mode == "stacked"
            else _three_pane_geometry(
                effective_left,
                effective_right,
                window_width=window_width,
            )
        ),
        "separator_state": separator_state,
        "dashboard": dashboard,
        "graph_fingerprint": graph,
        "graph_body_ax_present": graph_body_present,
        "zoom_control_focusable": zoom_focusable,
        "matrix_usable_height": matrix_height,
        "persisted": persisted,
        "diagnostic": diagnostic,
        "semantic_graph": _semantic_graph_observation(),
    }
    if case_id in GRAPH_READABILITY_CASE_IDS:
        after["appearance_mode"] = action["appearance"]
        after["semantic_graph"] = _semantic_graph_observation(
            selected_component_id=(
                CANONICAL_HOVER_COMPONENT_ID
                if graph_readability is not None
                and graph_readability["zoom_state"] != "fit"
                else None
            ),
            camera_scale=(
                float(graph_readability["graph_scale"])
                if graph_readability is not None
                else 1.0
            ),
        )
    if case_id == "corrupt-preference-default-pair":
        after["corruption_subcases"] = [
            {"kind": kind, "preferred_pair": {"left_px": 304, "right_px": 368}}
            for kind in ("malformed-json", "unknown-version", "out-of-range-pair")
        ]
    if case_id == "profile-supernode-identity-hover":
        after["profile_supernode"] = _profile_supernode_observation()
    if case_id == "relation-node-identity-hover":
        relation_graph = _semantic_graph_observation(
            focused_relation_id=f"profile:{CANONICAL_PROFILE_ID}",
        )
        after["semantic_graph"] = relation_graph
    if case_id in WORKFLOW_CASE_IDS or case_id in RENDERER_RESILIENCE_CASE_IDS:
        before_graph = _semantic_graph_observation(
            selected_workflow_id=workflow_id,
        )
        after_graph = _semantic_graph_observation(
            selected_workflow_id=workflow_id,
        )
        if case_id == "workflow-relation-focus":
            before_graph = _semantic_graph_observation()
        elif case_id == "workflow-step-selection-invariant":
            after_graph = _semantic_graph_observation(
                selected_workflow_id=workflow_id,
                locked_ordinal=1,
            )
            after["graph_fingerprint"] = json.loads(json.dumps(graph))
        elif case_id == "workflow-camera-restore":
            before_graph = _semantic_graph_observation(
                selected_workflow_id=workflow_id,
                locked_ordinal=1,
            )
            before["graph_fingerprint"] = {
                **json.loads(json.dumps(graph)),
                "scale": 2.0,
            }
            after_graph = _semantic_graph_observation(
                selected_component_id=None,
                selected_profile=False,
            )
        before["semantic_graph"] = before_graph
        after["semantic_graph"] = after_graph
        before["matrix_layout"] = _matrix_layout_observation(expanded=True)
        after["matrix_layout"] = _matrix_layout_observation(expanded=True)
    if case_id == "semantic-fallback-user-transition":
        recovery_graph = {
            **json.loads(json.dumps(graph)),
            "selected_relation_node_id": f"workflow:{workflow_id}",
            "locked_step": {"workflow_id": workflow_id, "ordinal": 1},
            "scale": 2.0,
            "camera_pose": {
                "position": {"x": 52.0, "y": -18.0, "z": 240.0},
                "target": {"x": 4.0, "y": 5.0, "z": 6.0},
            },
        }
        before["graph_fingerprint"] = json.loads(json.dumps(recovery_graph))
        after["graph_fingerprint"] = json.loads(json.dumps(recovery_graph))
        action["pre_setup_graph_fingerprint"] = json.loads(
            json.dumps(recovery_graph)
        )
        before["semantic_graph"] = _semantic_graph_observation(
            selected_component_id=CANONICAL_HOVER_COMPONENT_ID,
            selected_workflow_id=workflow_id,
            locked_ordinal=1,
            camera_scale=2.0,
            camera_position=(52.0, -18.0, 240.0),
            camera_target=(4.0, 5.0, 6.0),
        )
        after["semantic_graph"] = _semantic_graph_observation(
            selected_component_id=CANONICAL_HOVER_COMPONENT_ID,
            selected_workflow_id=workflow_id,
            locked_ordinal=1,
            camera_scale=2.0,
            camera_position=(52.0, -18.0, 240.0),
            camera_target=(4.0, 5.0, 6.0),
            renderer_availability="manual_fallback",
        )
        action["pre_fallback_ready_graph_fingerprint"] = json.loads(
            json.dumps(before["graph_fingerprint"])
        )
    if case_id == "renderer-retry-snapshot-preserved":
        recovery_graph = {
            **json.loads(json.dumps(graph)),
            "selected_relation_node_id": f"workflow:{workflow_id}",
            "locked_step": {"workflow_id": workflow_id, "ordinal": 1},
            "scale": 2.0,
            "camera_pose": {
                "position": {"x": 52.0, "y": -18.0, "z": 240.0},
                "target": {"x": 4.0, "y": 5.0, "z": 6.0},
            },
        }
        before["graph_fingerprint"] = json.loads(json.dumps(recovery_graph))
        after["graph_fingerprint"] = json.loads(json.dumps(recovery_graph))
        before["semantic_graph"] = _semantic_graph_observation(
            selected_component_id=CANONICAL_HOVER_COMPONENT_ID,
            selected_workflow_id=workflow_id,
            locked_ordinal=1,
            camera_scale=2.0,
            camera_position=(52.0, -18.0, 240.0),
            camera_target=(4.0, 5.0, 6.0),
            renderer_availability="manual_fallback",
        )
        after["semantic_graph"] = _semantic_graph_observation(
            selected_component_id=CANONICAL_HOVER_COMPONENT_ID,
            selected_workflow_id=workflow_id,
            locked_ordinal=1,
            camera_scale=2.0,
            camera_position=(52.0, -18.0, 240.0),
            camera_target=(4.0, 5.0, 6.0),
        )
        action["pre_fallback_ready_graph_fingerprint"] = json.loads(
            json.dumps(after["graph_fingerprint"])
        )
    if case_id == "graph-toolbar-single-row":
        after["graph_toolbar"] = _graph_toolbar_observation()
    if case_id in {"sot-tree-wheel-last-row", "sot-tree-keyboard-last-row"}:
        after["sot_tree"] = _sot_tree_observation()
    if case_id in {
        "sot-tree-filter-offset-restore",
        "sot-tree-roundtrip-offset-restored",
    }:
        tree = _sot_tree_observation()
        tree["scroll_top_px"] = 920
        tree["selected_identity"] = CANONICAL_HOVER_COMPONENT_ID
        tree["focused_identity"] = CANONICAL_HOVER_COMPONENT_ID
        after["sot_tree"] = tree
    if graph_readability is not None:
        after["graph_readability"] = graph_readability
    if case_id == "matrix-initial-collapsed-map-first-viewport":
        after["matrix_layout"] = _matrix_layout_observation(expanded=False)
    if case_id == "matrix-expanded-outer-scroll":
        before["matrix_layout"] = _matrix_layout_observation(expanded=False)
        after["matrix_layout"] = _matrix_layout_observation(
            expanded=True,
            scroll_top_px=180,
        )
    if case_id == "matrix-collapse-selection-state-preserved":
        before["matrix_layout"] = _matrix_layout_observation(
            expanded=True,
            scroll_top_px=180,
        )
        after["matrix_layout"] = _matrix_layout_observation(
            expanded=True,
            scroll_top_px=180,
        )
    if case_id == "matrix-bounded-fluid-wrap":
        after["matrix_wrap_samples"] = _matrix_wrap_samples()
    if case_id == "graph-collapsed":
        after["collapsed_workbench_layout"] = (
            _collapsed_workbench_layout_observation()
        )
    if case_id == "final-cleanup-zero":
        after.update(
            {
                "main_process_count": 0,
                "main_window_count": 0,
                "helper_process_count": 0,
            }
        )

    payload = {
        "identity": {
            "bundle_identifier": runtime.BUNDLE_IDENTIFIER,
            "build_id": BUILD_ID,
            "executable_sha256": executable_sha256,
            "pid": pid,
            "window_id": 41 if pid == 101 else 42,
        },
        "action": action,
        "before": before,
        "after": after,
        "publication_delta": publication_delta,
        "command_counters": {"sot_load_count": 1, "local_scan_start_count": 0},
    }
    if case_id in AMENDMENT_CASE_IDS:
        amendment = _amendment_payload(case_id)
        payload.update(
            {
                "case_id": case_id,
                "proof_level": amendment["proof_level"],
                "limits": amendment["limits"],
                "action": amendment["action"],
                "observation": amendment["observation"],
            }
        )
    if case_id == "relation-node-identity-hover":
        payload["during"] = {
            **json.loads(json.dumps(after)),
            "semantic_graph": json.loads(json.dumps(after["semantic_graph"])),
        }
    if case_id == "graph-hover-layout-stable":
        before["graph_layout_geometry"] = _graph_layout_geometry()
        before["graph_tooltip_identity"] = None
        during = json.loads(json.dumps(before))
        during["graph_tooltip_identity"] = {
            "component_id": CANONICAL_HOVER_COMPONENT_ID,
            "title": "Runtime Layout Middle Anchor",
            "visible": True,
            "frame": {"x": 340, "y": 58, "width": 456, "height": 42},
        }
        after["graph_layout_geometry"] = _graph_layout_geometry()
        after["graph_tooltip_identity"] = None
        payload["during"] = during
    if case_id == "matrix-collapse-selection-state-preserved":
        payload["during"] = {
            **json.loads(json.dumps(after)),
            "matrix_layout": _matrix_layout_observation(expanded=False),
        }
    return payload


def _strict_separator_state(snapshot: dict[str, object]) -> dict[str, object]:
    mode = str(snapshot["layout_mode"])
    effective = snapshot["effective_pair"]
    geometry = snapshot["geometry"]
    assert isinstance(effective, dict)
    assert isinstance(geometry, dict)
    shell = geometry["shell"]
    assert isinstance(shell, dict)
    left = int(effective["left_px"])
    right = int(effective["right_px"])
    if mode == "three-pane":
        budget = int(shell["width"]) - 24 - 480
        ranges = {
            "left": (200, min(512, budget - right)),
            "right": (184, min(560, budget - left)),
        }
    else:
        ranges = {"left": (left, left), "right": (right, right)}
    return {
        side: {
            "role": "AXSplitter",
            "name": "좌측 탐색 패널 너비" if side == "left" else "우측 상세 패널 너비",
            "orientation": "vertical",
            "focused": False,
            "focusable": True,
            "disabled": mode != "three-pane",
            "current_value": value,
            "minimum_value": ranges[side][0],
            "maximum_value": ranges[side][1],
            "value_description": f"{value}픽셀",
            "active_state": "normal",
        }
        for side, value in (("left", left), ("right", right))
    }


def _strict_case_payload(case_id: str, executable_sha256: str) -> dict[str, object]:
    payload = _case_payload(case_id, pid=101, executable_sha256=executable_sha256)
    payload["proof_level"] = "macos-ax-cgevent-sck"
    after = payload["after"]
    before = payload["before"]
    action = payload["action"]
    assert isinstance(after, dict)
    assert isinstance(before, dict)
    assert isinstance(action, dict)

    if case_id == "separator-keyboard-step":
        after["preferred_pair"] = {"left_px": 304, "right_px": 368}
        after["effective_pair"] = {"left_px": 304, "right_px": 368}
        after["geometry"] = _three_pane_geometry(304, 368)
        before["layout_revision"] = 10
        after["layout_revision"] = 14
        payload["publication_delta"] = 4
        action["sub_actions"] = [
            {
                "separator": side,
                "key": key,
                "delta_px": delta,
                "focused": True,
                "focused_readback": True,
                "before_value": before_value,
                "after_value": after_value,
                "opposite_before": opposite,
                "opposite_after": opposite,
                "ax_value_before": before_value,
                "ax_value_after": after_value,
            }
            for side, key, delta, before_value, after_value, opposite in (
                ("left", "ArrowRight", 16, 304, 320, 368),
                ("left", "ArrowLeft", -16, 320, 304, 368),
                ("right", "ArrowLeft", 16, 368, 384, 304),
                ("right", "ArrowRight", -16, 384, 368, 304),
            )
        ]
    elif case_id in {"left-pointer-drag", "right-pointer-drag"}:
        side = "left" if case_id.startswith("left") else "right"
        current = int(after["effective_pair"][f"{side}_px"])
        action.update(
            {
                "target": f"{side}-divider",
                "captured_before_mouseup": True,
                "mouse_down_sequence": 1,
                "drag_sequence": 2,
                "capture_sequence": 3,
                "mouse_up_sequence": 4,
                "mouse_down_monotonic_seconds": 1.0,
                "drag_posted_monotonic_seconds": 2.0,
                "capture_started_monotonic_seconds": 3.0,
                "capture_completed_monotonic_seconds": 4.0,
                "mouse_up_monotonic_seconds": 5.0,
                "active_target": {
                    "target": f"{side}-divider",
                    "visible": True,
                    "role": "AXSplitter",
                    "orientation": "vertical",
                    "current_value": current,
                    "value_description": f"{current}픽셀 · 드래그 중",
                    "active_state": "dragging",
                },
            }
        )
    elif case_id == "minimum-overshoot-clamped":
        before["layout_revision"] = 10
        after["layout_revision"] = 12
        action["sub_actions"] = [
            {
                "separator": side,
                "edge": "minimum",
                "drag_delta_px": drag_delta,
                "expected_value": value,
                "pointer_publication_delta": 1,
                "opposite_before": opposite,
                "opposite_after": opposite,
                "keyboard_clamp": {
                    "key": key,
                    "before_value": value,
                    "after_value": value,
                    "publication_delta": 0,
                    "focused_readback": True,
                    "ax_value_before": value,
                    "ax_value_after": value,
                },
            }
            for side, key, drag_delta, value, opposite in (
                ("left", "ArrowLeft", -4096, 200, 368),
                ("right", "ArrowRight", 4096, 184, 200),
            )
        ]
    elif case_id == "maximum-overshoot-clamped":
        before["preferred_pair"] = {"left_px": 200, "right_px": 184}
        before["effective_pair"] = {"left_px": 200, "right_px": 184}
        before["geometry"] = _three_pane_geometry(200, 184, window_width=1600)
        before["layout_revision"] = 10
        after["layout_revision"] = 13
        action["between_subcases_reset"] = {
            "separator": "left",
            "before_value": 512,
            "after_value": 200,
            "opposite_before": 184,
            "opposite_after": 184,
            "publication_delta": 1,
        }
        action["sub_actions"] = [
            {
                "separator": side,
                "edge": "maximum",
                "drag_delta_px": drag_delta,
                "expected_value": value,
                "pointer_publication_delta": 1,
                "opposite_before": opposite,
                "opposite_after": opposite,
                "keyboard_clamp": {
                    "key": key,
                    "before_value": value,
                    "after_value": value,
                    "publication_delta": 0,
                    "focused_readback": True,
                    "ax_value_before": value,
                    "ax_value_after": value,
                },
            }
            for side, key, drag_delta, value, opposite in (
                ("left", "ArrowRight", 4096, 512, 184),
                ("right", "ArrowLeft", -4096, 560, 200),
            )
        ]
    elif case_id == "graph-state-before-collapse":
        after["targets"] = [
            {
                "target": "graph-toggle",
                "role": "AXButton",
                "name": "⌃ 그래프 접기",
                "enabled": True,
                "focusable": True,
                "expanded_present": True,
                "expanded": True,
            }
        ]
    elif case_id == "graph-collapsed":
        after["targets"] = [
            {
                "target": "graph-toggle",
                "role": "AXButton",
                "name": "⌄ 그래프 펼치기",
                "enabled": True,
                "focusable": True,
                "expanded_present": True,
                "expanded": False,
            }
        ]
    elif case_id == "restart-first-visible-persisted":
        payload["first_visible"] = {
            "prearmed_before_launch": True,
            "collector_attached_before_visible": True,
            "sequence_gap": False,
            "first_layer0_sample_sequence": 18,
            "first_complete_frame_sequence": 19,
            "on_screen": True,
            "alpha": 1.0,
            "first_visible_geometry_monotonic_seconds": 10.0,
            "first_frame_monotonic_seconds": 10.1,
            "pair_delta_px": {"left": 0, "right": 0},
        }

    after["separator_state"] = _strict_separator_state(after)
    during = payload.get("during")
    if isinstance(during, dict):
        during["separator_state"] = _strict_separator_state(during)
    return payload


def _raw_three_pane_physical_geometry() -> tuple[
    dict[str, object], dict[str, dict[str, object]], dict[str, int]
]:
    geometry = _three_pane_geometry(304, 368)
    window = geometry["window"]
    shell = geometry["shell"]
    assert isinstance(window, dict)
    assert isinstance(shell, dict)
    targets = {
        token: {"frame": geometry[key]}
        for token, key in (
            ("workspace-shell", "shell"),
            ("left-pane", "left"),
            ("center-pane", "center"),
            ("right-pane", "right"),
            ("left-divider", "left_separator"),
            ("right-divider", "right_separator"),
        )
    }
    raw = {
        "ax_window": window,
        "cg_window": window,
        "workspace_shell": shell,
        "window_frame_match": True,
        "panes": {
            side: geometry[side] for side in ("left", "center", "right")
        },
        "pane_present": {"left": True, "center": True, "right": True},
        "dividers": {
            "left": geometry["left_separator"],
            "right": geometry["right_separator"],
        },
        "divider_present": {"left": True, "right": True},
        "tiling": {
            "left_gap_px": 0,
            "right_gap_px": 0,
            "divider_widths_match": True,
        },
        "containment": {
            "workspace_shell_in_ax_window": True,
            "workspace_shell_in_cg_window": True,
            "all_panes": True,
            "all_dividers": True,
        },
        "side_bounds": {
            "left": {
                "pane_width_px": 304,
                "current_value": 304,
                "minimum_value": 200,
                "maximum_value": 328,
                "within_bounds": True,
                "pane_width_matches_value": True,
            },
            "right": {
                "pane_width_px": 368,
                "current_value": 368,
                "minimum_value": 184,
                "maximum_value": 392,
                "within_bounds": True,
                "pane_width_matches_value": True,
            },
        },
        "responsive": {"mode": "three-pane", "usable": True},
        "collapsed_sides": [],
    }
    return raw, targets, window


def _raw_workspace_observation(
    *, preferred_left: int = 304, preferred_right: int = 368
) -> dict[str, object]:
    physical, frames, window = _raw_three_pane_physical_geometry()
    semantic = (
        f"선호 패널 너비 좌측 {preferred_left}픽셀, "
        f"우측 {preferred_right}픽셀"
    )
    names = {
        "workspace-shell": "Harness dashboard workspace",
        "left-pane": "Component inventory",
        "center-pane": "중앙 작업 영역",
        "right-pane": "Component detail",
        "left-divider": "좌측 탐색 패널 너비",
        "right-divider": "우측 상세 패널 너비",
    }
    targets: list[dict[str, object]] = []
    for token, frame_record in frames.items():
        side = token.removesuffix("-divider")
        target: dict[str, object] = {
            "target": token,
            "role": "AXSplitter" if token.endswith("-divider") else "AXGroup",
            "name": names[token],
            "frame": frame_record["frame"],
            "visible": True,
            "focusable": token.endswith("-divider"),
            "focused": False,
            "enabled": True,
            "selected": False,
            "expanded_present": False,
        }
        if token.endswith("-divider"):
            bounds = physical["side_bounds"][side]
            current = bounds["current_value"]
            target.update(
                {
                    "orientation": "vertical",
                    "current_value": current,
                    "minimum_value": bounds["minimum_value"],
                    "maximum_value": bounds["maximum_value"],
                    "value_description": f"{current}픽셀",
                    "active_state": "normal",
                }
            )
        targets.append(target)
    targets.append(
        {
            "target": "preferred-widths",
            "role": "AXStaticText",
            "name": semantic,
            "frame": {"x": 0, "y": 0, "width": 1, "height": 1},
            "visible": True,
            "focusable": False,
            "focused": False,
            "enabled": True,
            "selected": False,
            "expanded_present": False,
        }
    )
    return {
        "accepted": True,
        "pid": 202,
        "window_id": 42,
        "window_frame": window,
        "layout_mode": "three-pane",
        "targets": targets,
        "physical_geometry": physical,
        "preferred_pair": {
            "left_px": preferred_left,
            "right_px": preferred_right,
        },
    }


def _raw_collapsed_workspace_observation(mode: str) -> dict[str, object]:
    raw = _raw_workspace_observation()
    collapsed_sides = {
        "left-collapsed": {"left"},
        "right-collapsed": {"right"},
        "center-only": {"left", "right"},
    }[mode]
    frames: dict[str, dict[str, int] | None] = {
        "left": None,
        "left-divider": None,
        "center": {"x": 0, "y": 0, "width": 1200, "height": 720},
        "right-divider": None,
        "right": None,
    }
    if "left" not in collapsed_sides:
        frames["left"] = {"x": 0, "y": 0, "width": 304, "height": 720}
        frames["left-divider"] = {
            "x": 304,
            "y": 0,
            "width": 12,
            "height": 720,
        }
        frames["center"] = {"x": 316, "y": 0, "width": 884, "height": 720}
    if "right" not in collapsed_sides:
        center = frames["center"]
        assert isinstance(center, dict)
        center["width"] -= 380
        divider_x = center["x"] + center["width"]
        frames["right-divider"] = {
            "x": divider_x,
            "y": 0,
            "width": 12,
            "height": 720,
        }
        frames["right"] = {
            "x": divider_x + 12,
            "y": 0,
            "width": 368,
            "height": 720,
        }

    targets = raw["targets"]
    assert isinstance(targets, list)
    filtered_targets: list[dict[str, object]] = []
    for target in targets:
        assert isinstance(target, dict)
        token = str(target.get("target"))
        frame_key = {
            "left-pane": "left",
            "center-pane": "center",
            "right-pane": "right",
            "left-divider": "left-divider",
            "right-divider": "right-divider",
        }.get(token)
        if frame_key is not None:
            frame = frames[frame_key]
            if frame is None:
                continue
            target = {**target, "frame": frame}
        filtered_targets.append(target)
    raw["targets"] = filtered_targets
    raw["layout_mode"] = mode
    physical = raw["physical_geometry"]
    assert isinstance(physical, dict)
    physical.update(
        {
            "panes": {
                "left": frames["left"],
                "center": frames["center"],
                "right": frames["right"],
            },
            "pane_present": {
                "left": frames["left"] is not None,
                "center": True,
                "right": frames["right"] is not None,
            },
            "dividers": {
                "left": frames["left-divider"],
                "right": frames["right-divider"],
            },
            "divider_present": {
                "left": frames["left-divider"] is not None,
                "right": frames["right-divider"] is not None,
            },
            "tiling": {
                "left_gap_px": 0 if frames["left"] is not None else None,
                "right_gap_px": 0 if frames["right"] is not None else None,
                "divider_widths_match": True,
            },
            "side_bounds": {
                side: (
                    {
                        "present": False,
                        "preferred_width_px": 304 if side == "left" else 368,
                    }
                    if side in collapsed_sides
                    else physical["side_bounds"][side]
                )
                for side in ("left", "right")
            },
            "responsive": {
                "mode": mode,
                "usable": True,
                "center_continues": True,
            },
            "collapsed_sides": sorted(collapsed_sides),
        }
    )
    return raw


def _raw_hover_workspace_observation() -> dict[str, object]:
    raw = _raw_workspace_observation()
    targets = raw["targets"]
    assert isinstance(targets, list)

    def graph_target(
        token: str,
        role: str,
        name: str,
        frame: dict[str, int],
        *,
        selected: bool = False,
        focusable: bool = False,
    ) -> dict[str, object]:
        return {
            "target": token,
            "role": role,
            "name": name,
            "frame": frame,
            "visible": True,
            "focusable": focusable,
            "focused": False,
            "enabled": True,
            "selected": selected,
            "expanded_present": False,
        }

    tooltip = {
        "component_id": CANONICAL_HOVER_COMPONENT_ID,
        "title": "Runtime Layout Middle Anchor",
        "visible": True,
        "frame": {"x": 340, "y": 58, "width": 456, "height": 42},
    }
    targets.extend(
        [
            graph_target(
                "graph-body",
                "AXGroup",
                "Component Map",
                {"x": 340, "y": 84, "width": 456, "height": 280},
            ),
            graph_target(
                "graph-viewport",
                "AXGroup",
                "Component graph viewport",
                _graph_layout_geometry()["viewport"],
            ),
            graph_target(
                "graph-zoom",
                "AXButton",
                "그래프 확대",
                {"x": 748, "y": 88, "width": 32, "height": 32},
                focusable=True,
            ),
            graph_target(
                "matrix",
                "AXGroup",
                "Profile Matrix",
                _graph_layout_geometry()["matrix"],
            ),
            graph_target(
                f"component:{CANONICAL_HOVER_COMPONENT_ID}",
                "AXGroup",
                "Runtime Layout Middle Anchor",
                {"x": 620, "y": 284, "width": 32, "height": 32},
                selected=True,
                focusable=True,
            ),
            graph_target(
                "graph-tooltip",
                "AXStaticText",
                (
                    "Runtime Layout Middle Anchor · "
                    f"{CANONICAL_HOVER_COMPONENT_ID}"
                ),
                tooltip["frame"],
            ),
        ]
    )
    raw.update(
        {
            "selected_component_id": CANONICAL_HOVER_COMPONENT_ID,
            "right_detail_id": CANONICAL_HOVER_COMPONENT_ID,
            "active_profile_id": "runtime-layout-profile",
            "graph_scale": "1.25×",
            "graph_body_identity": "component-map-body",
            "graph_tooltip_identity": tooltip,
        }
    )
    return raw


def _raw_first_visible() -> dict[str, object]:
    return {
        "prearmed_before_launch": True,
        "collector_attached_before_visible": True,
        "sequence_gap": False,
        "first_layer0_sample_sequence": 7,
        "first_complete_frame_sequence": 8,
        "on_screen": True,
        "alpha": 1.0,
        "first_visible_geometry_monotonic_seconds": 10.1,
        "first_frame_monotonic_seconds": 10.2,
        "first_visible_geometry": {
            "layout_mode": "three-pane",
            "effective_pair": {"left_px": 304, "right_px": 368},
            "window": {"x": 0, "y": 0, "width": 1200, "height": 800},
            "panes": {
                "left": {"x": 0, "y": 0, "width": 304, "height": 720},
                "center": {"x": 316, "y": 0, "width": 504, "height": 720},
                "right": {"x": 832, "y": 0, "width": 368, "height": 720},
            },
            "checkout_path": "/private/secret",
        },
        "visibility": {
            "visibility_sequence": [
                {
                    "sequence": 6,
                    "event": "identity-bound-hidden",
                    "monotonic_seconds": 10.0,
                    "pid": 202,
                    "window_id": 42,
                    "layer": 0,
                    "on_screen": False,
                    "alpha_positive": False,
                    "alpha": 0.0,
                    "path": "/private/secret",
                },
                {
                    "sequence": 7,
                    "event": "first-visible",
                    "monotonic_seconds": 10.1,
                    "pid": 202,
                    "window_id": 42,
                    "layer": 0,
                    "on_screen": True,
                    "alpha_positive": True,
                    "alpha": 1.0,
                },
            ]
        },
    }


def _picker_panel_evidence(pid: int) -> dict[str, object]:
    identifier = runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER
    return {
        "accepted": True,
        "kind": "native-directory-panel-open",
        "pid": pid,
        "panel_count": 1,
        "panel_identifier": identifier,
        "path_redacted": True,
        "title_redacted": True,
        "panel": {
            "role": "AXWindow",
            "subrole": "AXDialog",
            "visible": True,
            "identifier": identifier,
        },
        "default_button": {
            "role": "AXButton",
            "visible": True,
            "frame": {"x": 700, "y": 620, "width": 80, "height": 32},
        },
        "cancel_button": {
            "role": "AXButton",
            "visible": True,
            "frame": {"x": 600, "y": 620, "width": 80, "height": 32},
        },
    }


def _picker_selection_evidence(pid: int) -> dict[str, object]:
    return {
        "accepted": True,
        "kind": "native-directory-selection",
        "transport": "cg-event-chord-ax-value-cg-event-mouse-click",
        "pid": pid,
        "panel_identifier": runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
        "path_redacted": True,
        "go_to_folder_value_confirmed": True,
        "confirm_button": {
            "role": "AXButton",
            "visible": True,
            "frame": {"x": 700, "y": 620, "width": 80, "height": 32},
        },
        "pointer_point": {"x": 740, "y": 636},
        "panel_closed": True,
    }


def _picker_closed_evidence(pid: int) -> dict[str, object]:
    return {
        "accepted": True,
        "kind": "native-directory-panel-closed",
        "pid": pid,
        "panel_identifier": runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
        "panel_closed": True,
        "path_redacted": True,
    }


def _picker_fixture_evidence(pid: int) -> dict[str, object]:
    return {
        "empty_path_triggered": True,
        "native_panel_open_confirmed": True,
        "cancel_sequence_invoked": True,
        "cancel_panel_closed_confirmed": True,
        "directory_selected": True,
        "automation_principal": "System Events",
        "selection_surface": "native_directory_picker",
        "panel": _picker_panel_evidence(pid),
        "cancel_panel_closure": _picker_closed_evidence(pid),
        "cancel_input_unchanged": True,
        "cancel_focus_restored": True,
        "checkout_mutation_count": 0,
        "selection_panel": _picker_panel_evidence(pid),
        "selection": _picker_selection_evidence(pid),
        "fixture_loaded": True,
        "fixture_registration_mode": "native_picker_after_cancel",
        "confirmation_source": "fixture_graph_and_sot_load",
    }


def _direct_markdown_ax_evidence(
    pid: int, window_id: int = 42
) -> dict[str, object]:
    return {
        "accepted": True,
        "pid": pid,
        "window_id": window_id,
        "preview_present": True,
        "preview_frame": {"x": 20, "y": 40, "width": 320, "height": 440},
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


def test_native_picker_proof_accepts_the_approved_appkit_open_panel_identifier() -> None:
    panel = _picker_panel_evidence(101)
    panel["panel_identifier"] = "open-panel"
    assert isinstance(panel["panel"], dict)
    panel["panel"]["identifier"] = "open-panel"

    selection = _picker_selection_evidence(101)
    selection["panel_identifier"] = "open-panel"

    assert runtime._native_picker_panel_proof_valid(panel, expected_pid=101)
    assert runtime._native_picker_selection_proof_valid(selection, expected_pid=101)


class FakePort:
    def __init__(
        self,
        app: Path,
        *,
        preflight: runtime.PermissionPreflight | None = None,
        fail_case: str | None = None,
        fail_error: runtime.RuntimeQualificationError | None = None,
    ) -> None:
        self.app = app
        self.executable = app / "Contents/MacOS/harness-desktop"
        self.executable_sha256 = hashlib.sha256(self.executable.read_bytes()).hexdigest()
        self.preflight_result = preflight or runtime.PermissionPreflight(
            accessibility=True,
            screen_capture=True,
            system_events=True,
        )
        self.fail_case = fail_case
        self.fail_error = fail_error
        self.launch_pids = deque([101, 202])
        self.events: list[tuple[object, ...]] = []
        self.exercised: list[str] = []
        self.captures: list[str] = []
        self.terminated: list[int] = []
        self.current_pid: int | None = None

    def prepare_collector(self, evidence_dir: Path) -> runtime.CollectorEvidence:
        collector = evidence_dir / "collectors/macos-layout-observer"
        collector.parent.mkdir(parents=True, exist_ok=True)
        collector.write_bytes(b"fake-layout-collector")
        return runtime.CollectorEvidence(
            collector_id=runtime.COLLECTOR_ID,
            sha256=hashlib.sha256(collector.read_bytes()).hexdigest(),
            relative_path="collectors/macos-layout-observer",
        )

    def preflight(self) -> runtime.PermissionPreflight:
        self.events.append(("preflight",))
        return self.preflight_result

    def launch_primary(
        self, artifact: object, environment: dict[str, str]
    ) -> int:
        assert getattr(artifact, "app") == self.app
        assert getattr(artifact, "build_id") == BUILD_ID
        assert getattr(artifact, "executable_sha256") == self.executable_sha256
        home = Path(environment["HOME"])
        assert home.name == "home"
        assert home.parent.name.startswith("harness-desktop-layout-runtime-")
        assert home.stat().st_mode & 0o777 == 0o700
        pid = self.launch_pids.popleft()
        self.current_pid = pid
        self.events.append(("launch", pid, BUILD_ID, self.executable_sha256))
        return pid

    def current_primary_pid(self) -> int | None:
        return self.current_pid

    def load_fixture_after_picker_cancel(
        self,
        pid: int,
        checkout: Path,
        anchor_component_ids: tuple[str, ...],
        timeout_seconds: float,
    ) -> dict[str, object]:
        assert pid == 101
        assert checkout.is_dir()
        assert anchor_component_ids == FIXTURE_ANCHOR_COMPONENT_IDS
        assert timeout_seconds <= 15
        self.events.append(("load-fixture-after-picker-cancel", pid, anchor_component_ids))
        return {
            "sot_load_count": 1,
            "local_scan_start_count": 0,
            "picker_evidence": _picker_fixture_evidence(pid),
        }

    def exercise_case(
        self,
        case_id: str,
        pid: int,
        timeout_seconds: float,
    ) -> dict[str, object]:
        assert case_id in CASE_IDS
        assert timeout_seconds <= 15
        self.exercised.append(case_id)
        self.events.append(("exercise", case_id, pid))
        if case_id == self.fail_case:
            assert self.fail_error is not None
            raise self.fail_error
        return _case_payload(
            case_id,
            pid=pid,
            executable_sha256=self.executable_sha256,
        )

    def wait_for_layout_publication(
        self, pid: int, timeout_seconds: float
    ) -> dict[str, object]:
        assert pid == 101
        assert timeout_seconds <= 15
        self.events.append(("wait-layout-publication", pid))
        return {
            "layout_revision": 5,
            "persisted": True,
            "preferred_pair": {"left_px": 320, "right_px": 376},
        }

    def terminate(self, pid: int) -> bool:
        self.terminated.append(pid)
        self.events.append(("terminate", pid))
        return True

    def prepare_picker_termination(
        self, pid: int, timeout_seconds: float
    ) -> dict[str, object]:
        assert pid in {202, 303}
        assert timeout_seconds <= 15
        self.events.append(("prepare-picker-termination", pid))
        return {
            "empty_path_triggered": True,
            "native_panel_open_confirmed": True,
            "panel": _picker_panel_evidence(pid),
        }

    def wait_for_zero(self, timeout_seconds: float) -> dict[str, int]:
        assert timeout_seconds <= 15
        self.events.append(("wait-for-zero",))
        return {
            "main_process_count": 0,
            "main_window_count": 0,
            "helper_process_count": 0,
        }

    def prearm_first_visible(
        self,
        artifact: object,
        timeout_seconds: float,
    ) -> dict[str, object]:
        assert getattr(artifact, "build_id") == BUILD_ID
        assert timeout_seconds <= 15
        arm = {
            "collector_attached": True,
            "sequence_start": 17,
            "expected_build_id": BUILD_ID,
        }
        self.events.append(("prearm-first-visible", 17))
        return arm

    def observe_first_visible(
        self,
        arm: dict[str, object],
        pid: int,
        expected_pair: dict[str, int],
        timeout_seconds: float,
    ) -> dict[str, object]:
        assert arm["collector_attached"] is True
        assert arm["expected_build_id"] == BUILD_ID
        assert pid == 202
        assert expected_pair == {"left_px": 320, "right_px": 376}
        assert timeout_seconds <= 15
        self.events.append(("observe-first-visible", pid, arm["sequence_start"]))
        payload = _case_payload(
            "restart-first-visible-persisted",
            pid=pid,
            executable_sha256=self.executable_sha256,
        )
        payload["before"] = None
        payload["after"]["preferred_pair"] = expected_pair
        payload["after"]["effective_pair"] = expected_pair
        payload["after"]["geometry"] = _three_pane_geometry(320, 376)
        payload["first_visible"] = {
            "prearmed_before_launch": True,
            "collector_attached_before_visible": True,
            "sequence_gap": False,
            "first_layer0_sample_sequence": 18,
            "first_complete_frame_sequence": 18,
            "on_screen": True,
            "alpha": 1.0,
            "pair_delta_px": {"left": 0, "right": 0},
        }
        return payload

    def cancel_first_visible(self, arm: dict[str, object]) -> None:
        sequence_start = arm["sequence_start"]
        assert isinstance(sequence_start, int)
        if arm.get("cancelled") is True:
            return
        arm["cancelled"] = True
        self.events.append(("cancel-first-visible", sequence_start))

    def capture_window(
        self,
        owner_pid: int,
        window_id: int,
        destination: Path,
        *,
        include_cursor: bool,
    ) -> runtime.ScreenshotCaptureEvidence:
        case_id = destination.stem
        assert owner_pid in {101, 202}
        assert window_id in {41, 42}
        assert include_cursor is (
            case_id
            in {
                "left-pointer-drag",
                "right-pointer-drag",
                "graph-hover-layout-stable",
            }
        )
        self.captures.append(case_id)
        destination.write_bytes(b"\x89PNG\r\n\x1a\n" + case_id.encode())
        return runtime.ScreenshotCaptureEvidence(
            capture_mode=(
                f"{runtime.SCREEN_CAPTURE_MODE}_cursor_included_graph_hover_active"
                if case_id == "graph-hover-layout-stable"
                else runtime.SCREEN_CAPTURE_MODE
            ),
            pixel_width=2400,
            pixel_height=1600,
            point_pixel_scale=2.0,
        )

    def observe_workspace_markdown_preview_safety(
        self, pid: int
    ) -> dict[str, object]:
        assert pid in {101, 202, 303}
        self.events.append(("observe-markdown-preview", pid))
        return _direct_markdown_ax_evidence(pid)


def test_case_ids_and_runtime_limits_are_the_approved_fixed_contract() -> None:
    assert runtime.CASE_IDS == CASE_IDS
    assert runtime.CANONICAL_RUNTIME_GRAPH_KINDS == GRAPH_READABILITY_KIND_TOKENS
    assert runtime.FIXTURE_ANCHOR_COMPONENT_IDS == FIXTURE_ANCHOR_COMPONENT_IDS
    assert runtime.FIXED_LAYOUT_CONTRACT == FIXED_LAYOUT_CONTRACT
    assert len(runtime.CASE_IDS) == 50
    assert tuple(
        case_id
        for case_id in runtime.CASE_IDS
        if case_id in runtime._CASE_EXPECTATIONS
    ) == runtime.CASE_IDS
    assert tuple(
        case_id for case_id in EXTENDED_CASE_IDS if case_id in runtime._CASE_EXPECTATIONS
    ) == EXTENDED_CASE_IDS
    assert runtime._CASE_EXPECTATIONS["graph-hover-layout-stable"] == {
        "target_component_id": HOVER_COMPONENT_ID,
        "pointer_transport": "cg-event-mouse-move",
        "tooltip_identity_exposed": True,
        "viewport_center_matrix_stable": True,
        "graph_fingerprint_preserved": True,
        "camera_preserved": True,
    }
    assert runtime._CASE_EXPECTATIONS["separator-keyboard-step"] == {
        "sub_actions": [
            {"separator": "left", "key": "ArrowRight", "delta_px": 16},
            {"separator": "left", "key": "ArrowLeft", "delta_px": -16},
            {"separator": "right", "key": "ArrowLeft", "delta_px": 16},
            {"separator": "right", "key": "ArrowRight", "delta_px": -16},
        ],
        "publication_delta": 4,
        "net_pair_change": 0,
    }
    assert runtime._CASE_EXPECTATIONS["minimum-overshoot-clamped"] == {
        "boundaries": [
            {"separator": "left", "edge": "minimum", "value_px": 200},
            {"separator": "right", "edge": "minimum", "value_px": 184},
        ],
        "overlap_count": 0,
    }
    assert runtime._CASE_EXPECTATIONS["maximum-overshoot-clamped"] == {
        "boundaries": [
            {"separator": "left", "edge": "maximum", "value_px": 512},
            {"separator": "right", "edge": "maximum", "value_px": 560},
        ],
        "overlap_count": 0,
    }
    assert runtime._CASE_EXPECTATIONS["matrix-initial-collapsed-map-first-viewport"] == {
        "map_expanded": True,
        "matrix_expanded": False,
        "matrix_body_hidden": True,
        "matrix_body_focus_target_count": 0,
        "first_viewport_center_tolerance_px": 1,
    }
    assert runtime._CASE_EXPECTATIONS["matrix-expanded-outer-scroll"] == {
        "map_viewport_preserved": True,
        "graph_fingerprint_preserved": True,
        "matrix_body_natural_flow": True,
        "outer_scroll_owner": "workbench",
        "positive_vertical_scroll_range": True,
    }
    assert runtime._CASE_EXPECTATIONS[
        "matrix-collapse-selection-state-preserved"
    ] == {
        "state_fields_preserved": [
            "selected_component_id",
            "active_profile_id",
            "owning_profile_ids",
            "right_detail_id",
            "camera",
        ],
        "outer_scroll_preserved_or_clamped": True,
        "command_counters_preserved": True,
    }
    assert runtime._CASE_EXPECTATIONS["matrix-bounded-fluid-wrap"] == {
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
    }
    assert runtime._CASE_EXPECTATIONS["workflow-relation-focus"] == {
        "identity_overlay_visible": True,
        "workflow_inspector_visible": True,
        "participants_only_highlighted": True,
        "ordinal_groups_visible": True,
        "screenshot_count": 1,
    }
    assert runtime._CASE_EXPECTATIONS["workflow-step-hover-no-camera"] == {
        "camera_pose_preserved": True,
        "matrix_fingerprint_preserved": True,
        "locked_step_unchanged": True,
    }
    assert runtime._CASE_EXPECTATIONS["workflow-step-selection-invariant"] == {
        "locked_step_changed": True,
        "camera_pose_preserved": True,
        "anchor_projection_preserved": True,
        "ordinal_groups_visible": True,
        "screenshot_count": 1,
    }
    assert runtime._CASE_EXPECTATIONS["workflow-camera-restore"] == {
        "pre_workflow_camera_restored": True,
        "selection_cleared": True,
    }
    assert runtime._CASE_EXPECTATIONS["workflow-matrix-state-invariant"] == {
        "matrix_fingerprint_preserved": True,
        "projection_id_preserved": True,
    }
    assert runtime._CASE_EXPECTATIONS[
        "semantic-fallback-user-transition"
    ] == {
        "production_control": "텍스트 보기",
        "semantic_primary_region_visible": True,
        "projection_identity_preserved": True,
        "selection_preserved": True,
        "matrix_fingerprint_preserved": True,
        "inspector_fingerprint_preserved": True,
        "load_scan_count_delta": 0,
        "screenshot_count": 1,
    }
    assert runtime._CASE_EXPECTATIONS["renderer-retry-snapshot-preserved"] == {
        "production_control": "3D 다시 시도",
        "renderer_ready": True,
        "projection_identity_preserved": True,
        "selection_preserved": True,
        "matrix_fingerprint_preserved": True,
        "inspector_fingerprint_preserved": True,
        "load_scan_count_delta": 0,
    }
    assert runtime._CASE_EXPECTATIONS["semantic-surface-workflow-parity"] == {
        "workflow_ids_equal": True,
        "ordinal_occurrences_equal": True,
        "roles_equal": True,
        "selected_expanded_state_equal": True,
        "unresolved_warning_set_equal": True,
    }


def test_20260718_amendment_cases_are_promoted_into_packaged_suite() -> None:
    assert runtime.CASE_IDS == CASE_IDS
    assert runtime.AMENDMENT_CASE_IDS == AMENDMENT_CASE_IDS
    assert tuple(runtime.CASE_IDS[-11:-1]) == runtime.AMENDMENT_CASE_IDS
    assert set(runtime.AMENDMENT_CASE_IDS).issubset(runtime.CASE_IDS)
    assert len(runtime.CASE_IDS) == 50
    assert len(runtime.AMENDMENT_CASE_IDS) == 10
    assert runtime.AMENDMENT_RUNTIME_INTEGRATION_REQUIREMENTS == (
        "MacOSWorkspaceLayoutAutomationPort must expose each amendment action through production AX/CGEvent",
        "promoted cases must use proof_level=macos-ax-cgevent-sck and append to the packaged report",
        "each promoted case must retain one ScreenCaptureKit screenshot and stay within existing target and time limits",
    )


def test_sanitized_local_scan_fixture_is_project_scoped_and_isolated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "home" / "HarnessKitRuntimeLocalFixture"

    fixture = runtime.create_sanitized_local_scan_fixture(root)

    skill = root / ".agents/skills/runtime-verification/SKILL.md"
    assert fixture.display_name == "runtime-verification"
    source = skill.read_text(encoding="utf-8")
    assert "runtime-verification" in source
    for structure in (
        "*emphasized*",
        "**strong**",
        "`inline-code`",
        "1. Ordered item",
        "   - Nested unordered item",
        "> Inert blockquote.",
        "```text",
        "| Kind | State |",
        "---",
        "<div>literal raw html</div>",
        "<script>",
        "<img ",
        "<iframe ",
    ):
        assert structure in source
    assert (root / ".harnesskitignore").read_text(encoding="utf-8").startswith("#")


def test_20260718_packaged_driver_uses_only_public_macos_amendment_ports() -> None:
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    driver = source.split("class MacOSWorkspaceLayoutAutomationPort:", 1)[1]

    for api in (
        "observe_workspace_selector",
        "press_workspace_selector",
        "select_workspace_typography_preset",
        "observe_workspace_local_scroll",
        "select_workspace_local_result",
        "select_workspace_project_scope",
        "observe_workspace_local_publication",
        "observe_workspace_toolbar_layout",
        "observe_workspace_markdown_preview_safety",
        "observe_workspace_network_interval",
        "select_workspace_local_fixture",
    ):
        assert f'"{api}"' in driver
    assert "_exercise_amendment_case(case_id, pid, timeout_seconds)" in driver
    assert '"proof_level": "macos-ax-cgevent-sck"' in source
    assert "validate_amendment_case_observation(case_id, payload)" in driver


def test_local_markdown_preview_runtime_driver_uses_live_interval_observation() -> None:
    workspace_source = Path(runtime.__file__).read_text(encoding="utf-8")
    shared_source = Path(shared_runtime.__file__).read_text(encoding="utf-8")

    assert '"local-markdown-preview-safety"' in workspace_source
    assert '"workspace-markdown-preview-safety"' in shared_source
    assert '"workspace-network-sample"' in shared_source
    assert '"workspace-local-select-fixture"' in shared_source
    assert "sourcePreviewSafetyObservation" in shared_source
    assert "workspaceNetworkSample" in shared_source
    assert "AXLink" in shared_source
    assert "AXImage" in shared_source
    assert '"observe_workspace_network_interval"' in workspace_source
    assert '"select_workspace_local_fixture"' in workspace_source
    assert '"source_body_redacted": True' not in workspace_source
    assert '"workspace-network-snapshot"' not in shared_source
    assert "Web Inspector" not in shared_source.split(
        'if command == "workspace-markdown-preview-safety"', 1
    )[1].split('if command == "workspace-local-select-fixture"', 1)[0]


def test_local_markdown_preview_driver_reselects_exact_fixture_inside_monitor(
    tmp_path: Path,
) -> None:
    runtime_home = tmp_path / "home"
    fixture = runtime.create_sanitized_local_scan_fixture(
        runtime_home / "HarnessKitRuntimeLocalFixture"
    )
    events: list[str] = []

    def preview(sequence: int) -> dict[str, object]:
        return {
            "accepted": True,
            "pid": 101,
            "window_id": 42,
            "preview_present": True,
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
                "window_id": 42,
            },
        }

    class Base:
        select_count = 0

        def select_workspace_local_fixture(
            self, pid: int, display_name: str
        ) -> dict[str, object]:
            assert pid == 101
            assert display_name == "runtime-verification"
            self.select_count += 1
            events.append(f"select-{self.select_count}")
            return {
                "accepted": True,
                "pid": pid,
                "window_id": 42,
                "transport": "cg-event-mouse-click",
                "requested_display_name": display_name,
                "selected_display_name": display_name,
                "selection_activation_observed": True,
            }

        def observe_workspace_markdown_preview_safety(
            self, pid: int
        ) -> dict[str, object]:
            assert pid == 101
            events.append("preview")
            return preview(6 if self.select_count < 2 else 7)

        def observe_workspace_network_interval(
            self,
            pid: int,
            action: Callable[[], object],
            **_: object,
        ) -> tuple[object, dict[str, object]]:
            assert pid == 101
            events.append("monitor-start")
            result = action()
            events.append("monitor-end")
            return result, {
                "process_scope": "verified-main-and-webkit-helper-descendants",
                "collector_id": "macos_process_connect_trace_v1",
                "collector_ready_before_action": True,
                "event_stream": True,
                "lossless_connect_attempts": True,
                "overflow_detected": False,
                "sequence_gap_detected": False,
                "audit_token_attribution": True,
                "short_lived_failed_attempt_coverage": True,
                "observed_connect_attempt_count": 0,
                "verified_webkit_roles": [
                    "webkit_networking",
                    "webkit_web_content",
                ],
                "duration_milliseconds": 1000,
            }

    class Port(runtime.MacOSWorkspaceLayoutAutomationPort):
        def __init__(self) -> None:
            self.base = Base()
            self.runtime_home = runtime_home

        def _steady(
            self, pid: int, operation: str, timeout_seconds: float
        ) -> dict[str, object]:
            assert pid == 101
            assert operation
            assert timeout_seconds > 0
            return {"window_id": 42}

        def _amendment_local_ready(
            self, pid: int, deadline: float, operation: str
        ) -> dict[str, object]:
            assert pid == 101
            assert deadline > 0
            assert operation == "local-markdown-preview-safety"
            return {}

        def _amendment_final_payload(self, **values: object) -> dict[str, object]:
            return values

    payload = Port()._exercise_amendment_local(
        "local-markdown-preview-safety",
        101,
        5,
        runtime.time.monotonic(),
    )

    action = payload["action"]
    observation = payload["observation"]
    assert isinstance(action, dict)
    assert isinstance(observation, dict)
    assert action == {
        "kind": "reopen-complex-markdown-preview",
        "transport": "cg-event-mouse-click-with-bounded-observers",
        "fixture_display_name": "runtime-verification",
        "selection_window_id": 42,
    }
    assert fixture.display_name == "runtime-verification"
    assert observation["selected_display_name"] == "runtime-verification"
    assert observation["render_completion_observed"] is True
    assert observation["app_web_area_identity_before"] == (
        observation["app_web_area_identity_after"]
    )
    assert observation["network_interval"]["observed_connect_attempt_count"] == 0
    assert observation["structured_evidence_negative_scan"] == {
        "forbidden_literal_count": 0,
        "prohibited_key_count": 0,
        "absolute_path_count": 0,
        "checked_literal_count": 7,
        "prohibited_key_name_count": 6,
    }
    assert events.index("monitor-start") < events.index("select-2")
    assert events.index("select-2") < events.index("monitor-end")


def test_local_markdown_case_record_binds_fixture_window_and_screenshot(
    tmp_path: Path,
) -> None:
    payload = _amendment_payload("local-markdown-preview-safety")
    artifact = type(
        "Artifact",
        (),
        {"build_id": BUILD_ID, "executable_sha256": "e" * 64},
    )()

    class Capture:
        def __init__(self) -> None:
            self.observation_count = 0

        def observe_workspace_markdown_preview_safety(
            self, pid: int
        ) -> dict[str, object]:
            assert pid == 101
            self.observation_count += 1
            return _direct_markdown_ax_evidence(pid)

        def capture_window(
            self,
            owner_pid: int,
            window_id: int,
            destination: Path,
            *,
            include_cursor: bool,
        ) -> runtime.ScreenshotCaptureEvidence:
            assert (owner_pid, window_id, include_cursor) == (101, 42, False)
            destination.write_bytes(b"\x89PNG\r\n\x1a\nidentity-bound")
            return runtime.ScreenshotCaptureEvidence(
                capture_mode=runtime.SCREEN_CAPTURE_MODE,
                pixel_width=1200,
                pixel_height=800,
                point_pixel_scale=2.0,
            )

    evidence_dir = tmp_path / "evidence"
    cases_dir = evidence_dir / "cases"
    cases_dir.mkdir(parents=True)
    record = runtime._record_passed_case(
        case_id="local-markdown-preview-safety",
        started_at="2026-07-18T00:00:00Z",
        payload=payload,
        artifact=artifact,
        automation=Capture(),
        evidence_dir=evidence_dir,
        cases_dir=cases_dir,
        graph_baseline=None,
    )

    binding = record["evidence_binding"]
    assert binding["selected_display_name"] == "runtime-verification"
    assert binding["pid"] == 101
    assert binding["window_id"] == 42
    assert binding["screenshot_sha256"] == record["screenshot"]["sha256"]
    assert len(binding["binding_sha256"]) == 64
    assert record["capture_ax_bracket"]["unchanged"] is True
    assert record["capture_ax_bracket"]["before"] == (
        record["capture_ax_bracket"]["after"]
    )
    assert record["structured_record_negative_scan"][
        "forbidden_literal_count"
    ] == 0
    assert "example.invalid" not in json.dumps(record, sort_keys=True)


def test_local_markdown_capture_fails_when_live_ax_state_changes_during_capture(
    tmp_path: Path,
) -> None:
    payload = _amendment_payload("local-markdown-preview-safety")
    artifact = type(
        "Artifact",
        (),
        {"build_id": BUILD_ID, "executable_sha256": "e" * 64},
    )()

    class DriftCapture:
        observation_count = 0

        def observe_workspace_markdown_preview_safety(
            self, pid: int
        ) -> dict[str, object]:
            self.observation_count += 1
            observation = _direct_markdown_ax_evidence(pid)
            if self.observation_count == 2:
                observation["render_completion_observed"] = False
            return observation

        def capture_window(
            self,
            owner_pid: int,
            window_id: int,
            destination: Path,
            *,
            include_cursor: bool,
        ) -> runtime.ScreenshotCaptureEvidence:
            destination.write_bytes(b"\x89PNG\r\n\x1a\nbracket-drift")
            return runtime.ScreenshotCaptureEvidence(
                capture_mode=runtime.SCREEN_CAPTURE_MODE,
                pixel_width=1200,
                pixel_height=800,
                point_pixel_scale=2.0,
            )

    cases_dir = tmp_path / "evidence/cases"
    cases_dir.mkdir(parents=True)
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._record_passed_case(
            case_id="local-markdown-preview-safety",
            started_at="2026-07-18T00:00:00Z",
            payload=payload,
            artifact=artifact,
            automation=DriftCapture(),
            evidence_dir=tmp_path / "evidence",
            cases_dir=cases_dir,
            graph_baseline=None,
        )

    assert captured.value.code == (
        "workspace_markdown_preview_capture_bracket_changed"
    )


def test_structured_evidence_negative_scan_finds_nested_hostile_source() -> None:
    result = runtime._structured_evidence_negative_scan(
        {"nested": [{"source": "https://example.invalid/preview-link"}]}
    )

    assert result == {
        "forbidden_literal_count": 2,
        "prohibited_key_count": 0,
        "absolute_path_count": 0,
        "checked_literal_count": 7,
        "prohibited_key_name_count": 6,
    }


def test_structured_evidence_negative_scan_rejects_sensitive_keys_and_paths() -> None:
    result = runtime._structured_evidence_negative_scan(
        {
            "_".join(("source", "revision")): "not-retained",
            "nested": {
                "_".join(("visible", "text", "sha256")): "not-retained"
            },
            "path": str(
                Path("/").joinpath(
                    "private", "tmp", "runtime-fixture", "source.md"
                )
            ),
        }
    )

    assert result["prohibited_key_count"] == 2
    assert result["absolute_path_count"] == 1


def test_m2k_packaged_toolbar_uses_compact_menu_and_current_checkout_copy() -> None:
    shared_source = Path(shared_runtime.__file__).read_text(encoding="utf-8")

    assert "#typography-menu-trigger" in shared_source
    assert "workspaceTypographyMenuItem" in shared_source
    assert "HarnessKit 연결" in shared_source
    assert '"Checkout 등록"' not in shared_source


def test_m2k_direct_graph_selection_requires_contextual_camera_evidence() -> None:
    payload = _case_payload(
        "3d-component-direct-select", pid=101, executable_sha256="e" * 64
    )
    action = payload["action"]
    assert isinstance(action, dict)
    evidence = action["contextual_camera_evidence"]
    assert isinstance(evidence, dict)
    assert evidence["origin"] == "graph"
    assert evidence["intent"] == "contextual"
    assert evidence["observation_mode"] == "endpoint-only-ax"
    assert evidence["camera_changed"] is True
    assert evidence["baseline"] != evidence["settled"]
    assert evidence["anchor_hash_before"] == evidence["anchor_hash_after"]


def test_m2k_contextual_camera_evidence_rejects_a_static_pose() -> None:
    payload = _case_payload(
        "3d-component-direct-select", pid=101, executable_sha256="e" * 64
    )
    action = payload["action"]
    assert isinstance(action, dict)
    evidence = action["contextual_camera_evidence"]
    assert isinstance(evidence, dict)
    evidence["settled"] = json.loads(json.dumps(evidence["baseline"]))

    with pytest.raises(runtime.RuntimeQualificationError) as raised:
        runtime._validate_case_invariants("3d-component-direct-select", payload)

    assert raised.value.code == "workspace_graph_contextual_camera_unconfirmed"


@pytest.mark.parametrize("case_id", AMENDMENT_CASE_IDS)
def test_20260718_amendment_packaged_observation_contract_is_accepted(
    case_id: str,
) -> None:
    runtime.validate_amendment_case_observation(case_id, _amendment_payload(case_id))


@pytest.mark.parametrize(
    ("case_id", "mutate", "expected_code"),
    (
        (
            "pane-left-collapse-reopen-restart",
            lambda payload: payload["observation"]["collapsed"].update({"pane_ax_present": True}),
            "workspace_amendment_pane_contract_mismatch",
        ),
        (
            "pane-right-collapse-reopen-restart",
            lambda payload: payload["observation"]["reopened"].update({"preferred_width_px": 999}),
            "workspace_amendment_pane_contract_mismatch",
        ),
        (
            "typography-preset-restart",
            lambda payload: payload["observation"]["presets"][1].update({"tokens_px": [12, 14, 14, 16]}),
            "workspace_amendment_typography_contract_mismatch",
        ),
        (
            "toolbar-width-preset-matrix",
            lambda payload: payload["observation"]["rows"][0].update({"crop_count": 1}),
            "workspace_amendment_toolbar_contract_mismatch",
        ),
        (
            "toolbar-width-preset-matrix",
            lambda payload: payload["observation"]["rows"][0].update(
                {"tab_order": ["right-disclosure"]}
            ),
            "workspace_amendment_toolbar_contract_mismatch",
        ),
        (
            "local-all-locations-alignment",
            lambda payload: payload["observation"].update({"visible_ax_target": False}),
            "workspace_amendment_local_alignment_mismatch",
        ),
        (
            "local-selection-mouse-scroll-preserved",
            lambda payload: payload["observation"].update({"scroll_value_after": 0.43}),
            "workspace_amendment_local_mouse_scroll_mismatch",
        ),
        (
            "local-selection-keyboard-nearest-reveal",
            lambda payload: payload["observation"].update({"nearest_reveal": False}),
            "workspace_amendment_local_keyboard_scroll_mismatch",
        ),
        (
            "local-ignore-save-rescan-retains-complete",
            lambda payload: payload["observation"].update({"last_complete_snapshot_during": "snapshot-b"}),
            "workspace_amendment_ignore_rescan_mismatch",
        ),
        (
            "local-correlation-badge-inspector-exact",
            lambda payload: payload["observation"].update({"badge": "연결 확인 필요"}),
            "workspace_amendment_correlation_mismatch",
        ),
        (
            "local-markdown-preview-safety",
            lambda payload: payload["observation"]["forbidden_ax_role_counts"].update({"embedded_web_area": 1}),
            "workspace_amendment_markdown_preview_safety_mismatch",
        ),
        (
            "local-markdown-preview-safety",
            lambda payload: payload["observation"]["network_interval"].update({"observed_connect_attempt_count": 1}),
            "workspace_amendment_markdown_preview_safety_mismatch",
        ),
        (
            "local-markdown-preview-safety",
            lambda payload: payload["observation"]["app_web_area_identity_after"].update({"url_class": "external"}),
            "workspace_amendment_markdown_preview_safety_mismatch",
        ),
        (
            "local-markdown-preview-safety",
            lambda payload: payload["observation"]["structured_evidence_negative_scan"].update({"forbidden_literal_count": 1}),
            "workspace_amendment_markdown_preview_safety_mismatch",
        ),
        (
            "local-markdown-preview-safety",
            lambda payload: payload["identity"].update({"window_id": 43}),
            "workspace_amendment_markdown_preview_identity_mismatch",
        ),
    ),
)
def test_20260718_amendment_packaged_observation_fails_closed(
    case_id: str,
    mutate: Callable[[dict[str, object]], None],
    expected_code: str,
) -> None:
    payload = _amendment_payload(case_id)
    mutate(payload)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime.validate_amendment_case_observation(case_id, payload)

    assert captured.value.code == expected_code


def test_packaged_graph_driver_excludes_production_runtime_injection() -> None:
    repo_root = Path(runtime.__file__).resolve().parents[2]
    workspace_source = Path(runtime.__file__).read_text(encoding="utf-8")
    shared_observer_source = (
        repo_root / "scripts/package/verify_single_instance_macos.py"
    ).read_text(encoding="utf-8")
    tauri_source = (repo_root / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    frontend_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (repo_root / "src-frontend").rglob("*.js")
        if path.is_file()
        and "vendor" not in path.parts
        and "node_modules" not in path.parts
        and "tests" not in path.parts
    )

    assert not (repo_root / "src-tauri/src/runtime_evidence.rs").exists()
    for source in (workspace_source, shared_observer_source, tauri_source, frontend_sources):
        assert "runtime_graph_evidence" not in source
        assert "__HARNESS_RUNTIME_EVIDENCE__" not in source
        assert "runtime-graph-evidence" not in source
        assert "temp-confined-webview-evidence" not in source
    assert "HARNESS_DESKTOP_E2E_" not in workspace_source
    assert ".eval(" not in tauri_source
    assert "semanticGraphObservation" in shared_observer_source


def test_real_packaged_case_driver_routes_graph_cases_without_runtime_evidence() -> None:
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    exercise = source.split(
        "    def exercise_case(\n",
    )[-1].split("    def _graph_fingerprint_matches", 1)[0]

    assert "_exercise_3d_runtime_case" not in exercise
    for case_id in (*GRAPH_READABILITY_CASE_IDS, *WORKFLOW_CASE_IDS, *RENDERER_RESILIENCE_CASE_IDS):
        assert case_id in source
    assert "runtime_graph_cases" not in exercise
    assert "runtime-graph-command" not in exercise
    assert "temp-confined-webview-evidence" not in exercise


def test_matrix_initial_collapsed_case_requires_map_first_viewport_contract() -> None:
    case_id = "matrix-initial-collapsed-map-first-viewport"
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    after = payload["after"]
    assert isinstance(after, dict)
    after["matrix_layout"] = _matrix_layout_observation(expanded=False)

    runtime._validate_case_invariants(case_id, payload)

    matrix = after["matrix_layout"]
    assert isinstance(matrix, dict)
    matrix["disclosures"]["profile_matrix"]["focus_target_count"] = 1
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == "workspace_matrix_initial_contract_mismatch"


def test_matrix_expanded_case_requires_natural_flow_and_outer_scroll() -> None:
    case_id = "matrix-expanded-outer-scroll"
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    before = payload["before"]
    after = payload["after"]
    assert isinstance(before, dict)
    assert isinstance(after, dict)
    payload["action"] = {
        "kind": "toggle-matrix",
        "expanded": True,
        "transport": "ax-press-and-cg-event-wheel",
    }
    before["matrix_layout"] = _matrix_layout_observation(expanded=False)
    after["matrix_layout"] = _matrix_layout_observation(
        expanded=True,
        scroll_top_px=180,
    )

    runtime._validate_case_invariants(case_id, payload)

    matrix = after["matrix_layout"]
    assert isinstance(matrix, dict)
    matrix["outer_scroll"]["positive_nested_scroll_range_count"] = 1
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == "workspace_matrix_outer_scroll_contract_mismatch"


def test_matrix_collapse_roundtrip_preserves_selection_detail_camera_and_scroll() -> None:
    case_id = "matrix-collapse-selection-state-preserved"
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    before = payload["before"]
    after = payload["after"]
    assert isinstance(before, dict)
    assert isinstance(after, dict)
    payload["action"] = {
        "kind": "matrix-collapse-expand-roundtrip",
        "transport": "ax-press",
    }
    before["matrix_layout"] = _matrix_layout_observation(
        expanded=True,
        scroll_top_px=180,
    )
    payload["during"] = {
        **json.loads(json.dumps(after)),
        "matrix_layout": _matrix_layout_observation(expanded=False),
    }
    after["matrix_layout"] = _matrix_layout_observation(
        expanded=True,
        scroll_top_px=180,
    )

    runtime._validate_case_invariants(case_id, payload)

    matrix = after["matrix_layout"]
    assert isinstance(matrix, dict)
    matrix["right_detail_id"] = "harnesskit.skill.different-component"
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == "workspace_matrix_state_preservation_mismatch"


def test_matrix_bounded_fluid_case_validates_wrap_geometry_and_typography() -> None:
    case_id = "matrix-bounded-fluid-wrap"
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    after = payload["after"]
    assert isinstance(after, dict)
    payload["action"] = {
        "kind": "sample-matrix-wrap",
        "center_inline_sizes_px": [504, 804, 1104],
    }
    after["matrix_wrap_samples"] = _matrix_wrap_samples()

    runtime._validate_case_invariants(case_id, payload)

    samples = after["matrix_wrap_samples"]
    assert isinstance(samples, list)
    profile_cards = samples[0]["profile_cards"]
    assert isinstance(profile_cards, list)
    profile_cards[1]["frame"]["x"] = 100
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == "workspace_matrix_bounded_fluid_contract_mismatch"


def test_matrix_bounded_fluid_case_requires_a_five_column_profile_sample() -> None:
    case_id = "matrix-bounded-fluid-wrap"
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    after = payload["after"]
    assert isinstance(after, dict)
    samples = _matrix_wrap_samples()
    samples[-1]["profile_column_count"] = 4
    after["matrix_wrap_samples"] = samples

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == "workspace_matrix_bounded_fluid_contract_mismatch"


@pytest.mark.parametrize(
    ("column_count_key", "fixed_count"),
    (("profile_column_count", 5), ("member_column_count", 2)),
)
def test_matrix_bounded_fluid_case_requires_each_grid_column_count_to_change(
    column_count_key: str,
    fixed_count: int,
) -> None:
    case_id = "matrix-bounded-fluid-wrap"
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    after = payload["after"]
    assert isinstance(after, dict)
    samples = _matrix_wrap_samples()
    for sample in samples:
        sample[column_count_key] = fixed_count
    after["matrix_wrap_samples"] = samples

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == "workspace_matrix_bounded_fluid_contract_mismatch"


@pytest.mark.parametrize("invalid_height_px", (71, 77))
def test_matrix_bounded_fluid_case_rejects_profile_card_height_outside_rem_range(
    invalid_height_px: int,
) -> None:
    case_id = "matrix-bounded-fluid-wrap"
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    after = payload["after"]
    assert isinstance(after, dict)
    samples = _matrix_wrap_samples()
    profile_cards = samples[0]["profile_cards"]
    assert isinstance(profile_cards, list)
    profile_cards[0]["frame"]["height"] = invalid_height_px
    after["matrix_wrap_samples"] = samples

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == "workspace_matrix_bounded_fluid_contract_mismatch"


def test_graph_collapse_compares_matrix_height_with_expanded_setup() -> None:
    case_id = "graph-collapsed"
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    action = payload["action"]
    after = payload["after"]
    assert isinstance(action, dict)
    assert isinstance(after, dict)
    action.update(
        {
            "matrix_setup": "expanded",
            "matrix_setup_usable_height": 0,
            "matrix_setup_layout": _matrix_layout_observation(expanded=True),
        }
    )
    after["matrix_usable_height"] = 10

    runtime._validate_case_invariants(case_id, payload)

    action["matrix_setup_usable_height"] = 10
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == "workspace_graph_matrix_did_not_expand"


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("component_map_expanded",), True),
        (("profile_matrix_expanded",), False),
        (("first_viewport_frame", "height"), 720),
        (("matrix_body_frame", "y"), 147),
        (("matrix_header_frame", "y"), 61),
        (("stable_ids", "matrix_body"), "renamed-matrix-body"),
    ),
)
def test_graph_collapsed_requires_contiguous_auto_height_workbench_observation(
    path: tuple[str, ...],
    value: object,
) -> None:
    case_id = "graph-collapsed"
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    _patch_payload(
        payload,
        ((('after', 'collapsed_workbench_layout', *path), value),),
    )

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == "workspace_graph_collapsed_layout_mismatch"


@pytest.mark.parametrize("case_id", SEMANTIC_CASE_IDS)
def test_semantic_case_success_fake_payload_satisfies_invariants(
    case_id: str,
) -> None:
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)

    runtime._validate_case_invariants(case_id, payload)


@pytest.mark.parametrize(
    ("case_id", "path", "value", "expected_code"),
    (
        (
            "profile-supernode-identity-hover",
            ("after", "profile_supernode", "component_node_unique"),
            False,
            "profile_supernode_contract_mismatch",
        ),
        (
            "graph-toolbar-single-row",
            ("after", "graph_toolbar", "camera_frame", "y"),
            128,
            "graph_toolbar_contract_mismatch",
        ),
        (
            "sot-tree-wheel-last-row",
            ("after", "sot_tree", "last_row", "visible"),
            False,
            "sot_tree_wheel_contract_mismatch",
        ),
        (
            "sot-tree-keyboard-last-row",
            ("after", "sot_tree", "last_row", "focusable"),
            False,
            "sot_tree_keyboard_contract_mismatch",
        ),
        (
            "sot-tree-filter-offset-restore",
            ("action", "restored_offset_px"),
            924,
            "sot_tree_filter_restore_mismatch",
        ),
        (
            "sot-tree-roundtrip-offset-restored",
            ("action", "after_selected_identity"),
            "harnesskit.skill.different-component",
            "sot_tree_roundtrip_restore_mismatch",
        ),
    ),
)
def test_semantic_case_core_observation_mismatch_fails_closed(
    case_id: str,
    path: tuple[str, ...],
    value: object,
    expected_code: str,
) -> None:
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    _patch_payload(payload, ((path, value),))

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == expected_code


def test_runtime_graph_readability_kinds_follow_canonical_schema_without_removing_static_fallback() -> None:
    canonical_runtime_kinds = list(GRAPH_READABILITY_KIND_TOKENS)

    for case_id in (
        "graph-readability-fit-light",
        "graph-readability-fit-dark",
    ):
        assert runtime._CASE_EXPECTATIONS[case_id]["required_kind_tokens"] == (
            canonical_runtime_kinds
        )

    frontend_root = Path(runtime.__file__).resolve().parents[2] / "src-frontend"
    node_object_source = (frontend_root / "graph/node-object.js").read_text(
        encoding="utf-8"
    )
    for kind in canonical_runtime_kinds:
        assert f'{kind}: "' in node_object_source
    assert 'KIND_MARKS[kind] ?? "?"' in node_object_source

    observer_source = shared_runtime._SWIFT_HELPER_SOURCE
    graph_identity_parser = observer_source.split(
        "func canonicalGraphComponentIdentityFromName", 1
    )[1].split("func legacySelectedCanonicalGraphComponentID", 1)[0]
    catalog_identity_parser = observer_source.split(
        "func canonicalComponentIDFromName", 1
    )[1].split("func canonicalGraphComponentIdentityFromName", 1)[0]
    for forbidden in ('"workflow"', '"mode"', '"other"'):
        assert forbidden not in graph_identity_parser
        assert forbidden not in catalog_identity_parser

    frontend_source = (frontend_root / "sot-view.js").read_text(encoding="utf-8")
    semantic_source = (frontend_root / "graph/semantic-fallback.js").read_text(
        encoding="utf-8"
    )
    assert "renderGraphSemanticFallback" not in frontend_source
    assert "data-component-map-semantic-host" in frontend_source
    assert "renderSemanticGraphFallback" in semantic_source
    assert 'id="component-map-semantic-view"' in semantic_source


@pytest.mark.parametrize("case_id", GRAPH_READABILITY_CASE_IDS)
def test_graph_readability_case_success_fake_payload_satisfies_invariants(
    case_id: str,
) -> None:
    graph_baseline = _graph_fingerprint()
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    after = payload["after"]
    assert isinstance(after, dict)
    observation = after["graph_readability"]
    assert isinstance(observation, dict)
    assert set(observation) == {
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
    assert observation["snapshot_identity"]["active_profile_id"] == (
        graph_baseline["active_profile_id"]
    )
    assert observation["ax_before_sha256"] == observation["ax_after_sha256"]
    assert observation["component_inline_title_count"] == 0
    assert observation["profile_identity_visible"] is ("fit" in case_id)
    assert observation["unprofiled_identity_visible"] is ("fit" in case_id)
    assert observation["required_kind_tokens"] == sorted(
        GRAPH_READABILITY_KIND_TOKENS
    )
    expected_component_count = len(GRAPH_READABILITY_KIND_TOKENS) * 2
    assert len(observation["semantic_component_ids"]) == expected_component_count
    assert observation["expected_kind_counts"] == {
        kind: 2 for kind in GRAPH_READABILITY_KIND_TOKENS
    }
    pixel_proof = observation["viewport_pixel_proof"]
    assert isinstance(pixel_proof, dict)
    assert pixel_proof["proof_scope"] == "viewport_pixels"
    assert pixel_proof["node_frame_authority"] == "none"
    assert pixel_proof["node_crop_detected"] is False
    assert observation["screenshot_count"] == 1
    assert observation["appearance"] == {
        "3d-atlas-fit-light": "light",
        "3d-atlas-fit-dark": "dark",
        "3d-component-direct-select": "light",
        "3d-component-maximum-zoom": "light",
        "graph-readability-fit-light": "light",
        "graph-readability-fit-dark": "dark",
        "graph-readability-direct-select": "light",
        "graph-readability-maximum-zoom": "light",
    }[case_id]
    assert observation["zoom_state"] == {
        "3d-atlas-fit-light": "fit",
        "3d-atlas-fit-dark": "fit",
        "3d-component-direct-select": "direct-select",
        "3d-component-maximum-zoom": "maximum",
        "graph-readability-fit-light": "fit",
        "graph-readability-fit-dark": "fit",
        "graph-readability-direct-select": "direct-select",
        "graph-readability-maximum-zoom": "maximum",
    }[case_id]
    assert observation["non_color_selection_cue"] is ("fit" not in case_id)
    if "fit" in case_id:
        assert pixel_proof["selected_evidence"] is None
    else:
        assert isinstance(pixel_proof["selected_evidence"], dict)

    runtime._validate_case_invariants(case_id, payload, graph_baseline)


@pytest.mark.parametrize(
    ("case_id", "mismatch"),
    (
        ("graph-readability-fit-light", "snapshot-fingerprint"),
        ("graph-readability-fit-dark", "snapshot-fingerprint"),
        ("graph-readability-direct-select", "snapshot-fingerprint"),
        ("graph-readability-maximum-zoom", "snapshot-fingerprint"),
        ("graph-readability-fit-light", "appearance"),
        ("graph-readability-fit-dark", "appearance"),
        ("graph-readability-fit-light", "required-kind-token"),
        ("graph-readability-fit-dark", "shared-neutral-evidence-missing"),
        ("graph-readability-fit-light", "screenshot-count"),
        ("graph-readability-fit-dark", "inline-title"),
        ("graph-readability-fit-dark", "profile-identity"),
        ("graph-readability-fit-light", "semantic-component-missing"),
        ("graph-readability-fit-dark", "proof-count-mismatch"),
        ("graph-readability-direct-select", "zoom-state"),
        ("graph-readability-direct-select", "mark-center"),
        ("graph-readability-direct-select", "body-rgb-invalid"),
        ("graph-readability-direct-select", "non-color-selection-cue"),
        ("graph-readability-direct-select", "selection-halo-incomplete"),
        ("graph-readability-maximum-zoom", "zoom-state"),
        ("graph-readability-maximum-zoom", "mark-contained"),
        ("graph-readability-maximum-zoom", "outline-rgb-invalid"),
    ),
)
def test_graph_readability_core_mismatch_fails_closed(
    case_id: str,
    mismatch: str,
) -> None:
    graph_baseline = _graph_fingerprint()
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    after = payload["after"]
    assert isinstance(after, dict)
    observation = after["graph_readability"]
    assert isinstance(observation, dict)

    if mismatch == "snapshot-fingerprint":
        observation["snapshot_identity_sha256"] = "f" * 64
    elif mismatch == "appearance":
        observation["appearance"] = (
            "dark" if case_id.endswith("fit-light") else "light"
        )
    elif mismatch == "required-kind-token":
        observation["required_kind_tokens"] = list(
            GRAPH_READABILITY_KIND_TOKENS[:-1]
        )
    elif mismatch == "shared-neutral-evidence-missing":
        proof = observation["viewport_pixel_proof"]
        assert isinstance(proof, dict)
        proof.pop("shared_neutral_material_evidence")
    elif mismatch == "screenshot-count":
        observation["screenshot_count"] = 0
    elif mismatch == "inline-title":
        observation["component_inline_title_count"] = 1
    elif mismatch == "profile-identity":
        observation["profile_identity_visible"] = False
    elif mismatch == "semantic-component-missing":
        semantic_ids = observation["semantic_component_ids"]
        assert isinstance(semantic_ids, list)
        semantic_ids.pop()
    elif mismatch == "proof-count-mismatch":
        proof = observation["viewport_pixel_proof"]
        assert isinstance(proof, dict)
        proof_counts = proof["expected_kind_counts"]
        assert isinstance(proof_counts, dict)
        proof_counts["agent"] = int(proof_counts["agent"]) + 1
    elif mismatch == "zoom-state":
        observation["zoom_state"] = (
            "maximum"
            if case_id == "graph-readability-direct-select"
            else "direct-select"
        )
    elif mismatch in {
        "mark-center",
        "body-rgb-invalid",
        "mark-contained",
        "outline-rgb-invalid",
    }:
        proof = observation["viewport_pixel_proof"]
        assert isinstance(proof, dict)
        selected = proof["selected_evidence"]
        assert isinstance(selected, dict)
        if mismatch == "mark-center":
            center_delta = selected["mark_center_delta_css_px"]
            assert isinstance(center_delta, dict)
            center_delta["x"] = 5.0
        elif mismatch == "body-rgb-invalid":
            evidence = proof["shared_neutral_material_evidence"]
            assert isinstance(evidence, dict)
            evidence["body_rgb"] = [256, 0, 0]
        elif mismatch == "mark-contained":
            body_frame = selected["body_frame"]
            mark_frame = selected["mark_frame"]
            assert isinstance(body_frame, dict)
            assert isinstance(mark_frame, dict)
            mark_frame["x"] = int(body_frame["x"]) - 2
        else:
            evidence = proof["shared_neutral_material_evidence"]
            assert isinstance(evidence, dict)
            evidence["outline_rgb"] = [-1, 0, 0]
    elif mismatch == "non-color-selection-cue":
        observation["non_color_selection_cue"] = False
    elif mismatch == "selection-halo-incomplete":
        proof = observation["viewport_pixel_proof"]
        assert isinstance(proof, dict)
        selected = proof["selected_evidence"]
        assert isinstance(selected, dict)
        halo_sides = selected["selection_halo_sides"]
        assert isinstance(halo_sides, dict)
        halo_sides["left"] = False
    else:  # pragma: no cover - parameter exhaustiveness guard
        raise AssertionError(mismatch)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload, graph_baseline)

    assert captured.value.code == "workspace_graph_readability_contract_mismatch"


@pytest.mark.parametrize(
    "case_id",
    (
        "initial-default",
        "left-pointer-drag",
        "right-pointer-drag",
        "separator-keyboard-step",
        "minimum-overshoot-clamped",
        "maximum-overshoot-clamped",
        "small-window-two-column",
        "graph-state-before-collapse",
        "graph-collapsed",
        "graph-hover-layout-stable",
        "restart-first-visible-persisted",
    ),
)
def test_strict_production_proof_payload_exercises_runtime_only_invariants(
    tmp_path: Path, case_id: str
) -> None:
    app = _fixture_app(tmp_path)
    executable = app / "Contents/MacOS/harness-desktop"
    payload = _strict_case_payload(
        case_id, hashlib.sha256(executable.read_bytes()).hexdigest()
    )

    runtime._validate_case_invariants(case_id, payload)


@pytest.mark.parametrize(
    ("case_id", "path", "value", "expected_code"),
    (
        (
            "initial-default",
            ("after", "separator_state", "left", "orientation"),
            "horizontal",
            "workspace_separator_semantic_mismatch",
        ),
        (
            "initial-default",
            ("after", "geometry", "left_separator", "width"),
            8,
            "workspace_separator_width_mismatch",
        ),
        (
            "small-window-two-column",
            ("after", "geometry", "right", "x"),
            1005,
            "workspace_pane_outside_window",
        ),
        (
            "small-window-two-column",
            ("after", "separator_state", "right", "focused"),
            True,
            "workspace_separator_responsive_state_mismatch",
        ),
        (
            "left-pointer-drag",
            ("action", "active_target", "active_state"),
            "normal",
            "workspace_drag_capture_proof_invalid",
        ),
        (
            "minimum-overshoot-clamped",
            ("action", "kind"),
            "pointer-drag",
            "workspace_keyboard_clamp_mismatch",
        ),
        (
            "maximum-overshoot-clamped",
            ("action", "between_subcases_reset", "opposite_after"),
            289,
            "workspace_keyboard_clamp_mismatch",
        ),
        (
            "graph-state-before-collapse",
            ("action", "target"),
            "semantic-component:harnesskit.skill.wrong-component",
            "workspace_graph_selection_proof_invalid",
        ),
        (
            "graph-collapsed",
            ("after", "targets"),
            [
                {
                    "target": "graph-toggle",
                    "role": "AXButton",
                    "name": "⌄ 그래프 펼치기",
                    "enabled": True,
                    "focusable": True,
                    "expanded": False,
                }
            ],
            "workspace_graph_toggle_state_invalid",
        ),
        (
            "graph-hover-layout-stable",
            ("during", "graph_layout_geometry", "viewport", "y"),
            120,
            "workspace_graph_hover_layout_shifted",
        ),
        (
            "graph-hover-layout-stable",
            ("during", "graph_tooltip_identity", "component_id"),
            "harnesskit.skill.wrong-component",
            "workspace_graph_hover_tooltip_identity_mismatch",
        ),
        (
            "graph-hover-layout-stable",
            ("after", "graph_fingerprint", "scale"),
            2.0,
            "workspace_graph_hover_camera_changed",
        ),
        (
            "graph-hover-layout-stable",
            ("action", "hover_target", "visible"),
            False,
            "workspace_graph_hover_target_mismatch",
        ),
        (
            "restart-first-visible-persisted",
            ("first_visible", "first_visible_geometry_monotonic_seconds"),
            None,
            "workspace_first_visible_sequence_invalid",
        ),
    ),
)
def test_strict_production_proof_mutations_fail_closed(
    tmp_path: Path,
    case_id: str,
    path: tuple[str, ...],
    value: object,
    expected_code: str,
) -> None:
    app = _fixture_app(tmp_path)
    executable = app / "Contents/MacOS/harness-desktop"
    payload = _strict_case_payload(
        case_id, hashlib.sha256(executable.read_bytes()).hexdigest()
    )
    _patch_payload(payload, ((path, value),))

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == expected_code


def test_strict_keyboard_proof_requires_each_focused_ax_readback(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)
    executable = app / "Contents/MacOS/harness-desktop"
    payload = _strict_case_payload(
        "separator-keyboard-step",
        hashlib.sha256(executable.read_bytes()).hexdigest(),
    )
    action = payload["action"]
    assert isinstance(action, dict)
    sub_actions = action["sub_actions"]
    assert isinstance(sub_actions, list)
    assert isinstance(sub_actions[2], dict)
    sub_actions[2]["focused_readback"] = False

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants("separator-keyboard-step", payload)

    assert captured.value.code == "workspace_keyboard_step_mismatch"


@pytest.mark.parametrize(
    ("case_id", "expected"),
    (
        (
            "minimum-overshoot-clamped",
            (("left", "ArrowLeft", 200), ("right", "ArrowRight", 184)),
        ),
        (
            "maximum-overshoot-clamped",
            (("left", "ArrowRight", 512), ("right", "ArrowLeft", 560)),
        ),
    ),
)
def test_strict_boundary_cases_prove_both_divider_edges(
    tmp_path: Path,
    case_id: str,
    expected: tuple[tuple[str, str, int], ...],
) -> None:
    app = _fixture_app(tmp_path)
    executable = app / "Contents/MacOS/harness-desktop"
    payload = _strict_case_payload(
        case_id, hashlib.sha256(executable.read_bytes()).hexdigest()
    )
    action = payload["action"]
    assert isinstance(action, dict)
    sub_actions = action["sub_actions"]
    assert isinstance(sub_actions, list)
    assert [
        (
            entry["separator"],
            entry["keyboard_clamp"]["key"],
            entry["keyboard_clamp"]["after_value"],
        )
        for entry in sub_actions
        if isinstance(entry, dict)
    ] == list(expected)

    runtime._validate_case_invariants(case_id, payload)


def test_raw_physical_geometry_is_recomputed_against_ax_targets(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    raw, targets, window = _raw_three_pane_physical_geometry()

    normalized = port._normalise_physical_geometry(
        raw, window, targets, "three-pane", "physical geometry test"
    )

    assert normalized["window_frame_match"] is True
    assert normalized["side_bounds"]["left"]["pane_width_px"] == 304


@pytest.mark.parametrize(
    ("mode", "collapsed_sides"),
    (
        ("left-collapsed", {"left"}),
        ("right-collapsed", {"right"}),
        ("center-only", {"left", "right"}),
    ),
)
def test_full_workspace_normaliser_accepts_collapsed_pane_modes(
    tmp_path: Path, mode: str, collapsed_sides: set[str]
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    raw = _raw_collapsed_workspace_observation(mode)

    snapshot = port._normalise_observation(raw, f"{mode} full snapshot")

    assert snapshot["layout_mode"] == mode
    geometry = snapshot["geometry"]
    assert isinstance(geometry, dict)
    for side in ("left", "right"):
        expected_present = side not in collapsed_sides
        assert (geometry[side] is not None) is expected_present
        assert (geometry[f"{side}_separator"] is not None) is expected_present
    physical = snapshot["physical_geometry"]
    assert isinstance(physical, dict)
    assert set(physical["collapsed_sides"]) == collapsed_sides


def test_raw_preferred_pair_stays_distinct_from_effective_geometry(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"

    normalized = port._normalise_observation(
        _raw_workspace_observation(preferred_left=500, preferred_right=550),
        "preferred pair test",
    )

    assert normalized["preferred_pair"] == {"left_px": 500, "right_px": 550}
    assert normalized["effective_pair"] == {"left_px": 304, "right_px": 368}


def test_raw_preferred_pair_is_required_even_in_three_pane_mode(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    raw = _raw_workspace_observation()
    raw.pop("preferred_pair")

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._normalise_observation(raw, "preferred pair test")

    assert captured.value.code == "workspace_preferred_pair_missing"


def test_raw_preferred_pair_must_match_its_accessible_semantic(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    raw = _raw_workspace_observation()
    targets = raw["targets"]
    assert isinstance(targets, list)
    preferred = next(
        target
        for target in targets
        if isinstance(target, dict) and target.get("target") == "preferred-widths"
    )
    preferred["name"] = "선호 패널 너비 좌측 305픽셀, 우측 368픽셀"

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._normalise_observation(raw, "preferred pair test")

    assert captured.value.code == "workspace_preferred_pair_semantic_mismatch"


def test_raw_hover_snapshot_preserves_graph_geometry_and_tooltip_identity(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"

    snapshot = port._normalise_observation(
        _raw_hover_workspace_observation(),
        "graph hover observation",
    )

    assert snapshot["graph_layout_geometry"] == _graph_layout_geometry()
    assert snapshot["graph_tooltip_identity"] == {
        "component_id": CANONICAL_HOVER_COMPONENT_ID,
        "title": "Runtime Layout Middle Anchor",
        "visible": True,
        "frame": {"x": 340, "y": 58, "width": 456, "height": 42},
    }


def test_shared_observer_hover_uses_cgevent_and_returns_target_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = shared_runtime.MacOSRuntimeAutomationPort(tmp_path)
    target = f"component:{CANONICAL_HOVER_COMPONENT_ID}"
    expected = {
        "accepted": True,
        "kind": "pointer-hover",
        "transport": "cg-event-mouse-move",
        "pid": 101,
        "target": target,
        "target_readback": {
            "target": target,
            "component_id": CANONICAL_HOVER_COMPONENT_ID,
            "role": "AXButton",
            "name": "Runtime Layout Middle Anchor",
            "visible": True,
            "frame": {"x": 620, "y": 284, "width": 32, "height": 32},
        },
        "pointer_point": {"x": 636, "y": 300},
    }
    calls: list[tuple[str, ...]] = []

    def helper_json(*arguments: str) -> dict[str, object]:
        calls.append(arguments)
        return expected

    monkeypatch.setattr(port, "_helper_json", helper_json)

    observed = port.hover_workspace_target(101, target)

    assert calls == [
        ("workspace-hover", runtime.BUNDLE_IDENTIFIER, "101", target)
    ]
    assert observed == expected
    source = shared_runtime._SWIFT_HELPER_SOURCE
    assert 'if command == "workspace-hover" {' in source
    assert "mouseType: .mouseMoved" in source
    assert '"target_readback"' in source
    assert '"pointer_point"' in source


def test_shared_observer_hover_rejects_unbounded_component_token(
    tmp_path: Path,
) -> None:
    port = shared_runtime.MacOSRuntimeAutomationPort(tmp_path)

    with pytest.raises(shared_runtime.RuntimeQualificationError) as captured:
        port.hover_workspace_target(101, "component:../../outside")

    assert captured.value.code == "workspace_action_arguments_invalid"
    assert captured.value.operation == "workspace-hover"


def test_shared_observer_exposes_bounded_matrix_semantics_and_workbench_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = shared_runtime._SWIFT_HELPER_SOURCE
    matrix_observer = source.split("func matrixLayoutObservation", 1)[1].split(
        "func workspaceSnapshot", 1
    )[0]

    assert '"matrix-toggle"' in source
    assert '"profile-matrix-body"' in source
    assert '"matrix_layout"' in source
    assert '"matrix_wrap_sample"' in source
    assert "positiveNestedScrollRangeCount" in matrix_observer
    assert "boundedDescendants" in matrix_observer
    assert "raw_ax_tree" not in matrix_observer

    port = shared_runtime.MacOSRuntimeAutomationPort(tmp_path)
    expected = {
        "accepted": True,
        "kind": "workbench-scroll",
        "transport": "cg-event-wheel",
        "target": "center-pane",
        "delta_y": -480,
    }
    calls: list[tuple[str, ...]] = []

    def helper_json(*arguments: str) -> dict[str, object]:
        calls.append(arguments)
        return expected

    monkeypatch.setattr(port, "_helper_json", helper_json)

    observed = port.wheel_workspace_target_evidence(101, "center-pane", -480)

    assert calls == [
        (
            "workspace-wheel",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "center-pane",
            "-480",
        )
    ]
    assert observed == expected


def test_shared_observer_exposes_bounded_collapsed_workbench_layout() -> None:
    source = shared_runtime._SWIFT_HELPER_SOURCE
    observer = source.split(
        "func collapsedWorkbenchLayoutObservation", 1
    )[1].split("let workspaceAmendmentSelectors", 1)[0]

    assert 'elementForToken("center-pane", elements)' in observer
    assert 'elementForToken("first-viewport", elements)' in observer
    assert 'elementForToken("matrix", elements)' in observer
    assert 'elementForToken("matrix-body", elements)' in observer
    assert 'elementIdentifier(center) == "workbench"' in observer
    assert (
        'elementIdentifier(firstViewport) == "sot-workbench-first-viewport"'
        in observer
    )
    assert 'elementIdentifier(matrixHeader) == "profile-matrix"' in observer
    assert 'elementIdentifier(matrixBody) == "profile-matrix-body"' in observer
    assert 'payload["collapsed_workbench_layout"]' in source
    assert "boundedDescendants" not in observer
    assert "raw_ax_tree" not in observer


def test_shared_observer_reads_bounded_semantic_graph_without_webview_bridge() -> None:
    source = shared_runtime._SWIFT_HELPER_SOURCE
    element_lookup = source.split("func elementForToken", 1)[1].split(
        "func integerFrame", 1
    )[0]
    observer = source.split("func semanticGraphObservation", 1)[1].split(
        "func elementForToken", 1
    )[0]

    assert 'elementMatchesIdentifier($0, "component-map-semantic-view")' in observer
    assert "boundedDescendants(semanticRoot, maximum: 4096)" in observer
    assert "relationAuthorityRecords" in observer
    assert "semanticAXRecord" in observer
    assert "seenIdentifiers.insert(identifier).inserted" in observer
    assert 'token.hasPrefix("semantic-relation:")' in element_lookup
    assert 'token.hasPrefix("semantic-component:")' in element_lookup
    assert 'token.hasPrefix("semantic-workflow-step:")' in element_lookup
    assert "Data(base64Encoded:" not in observer
    assert "raw_ax_tree" not in observer


def test_shared_observer_keeps_semantic_selection_and_matrix_detail_independent() -> None:
    source = shared_runtime._SWIFT_HELPER_SOURCE
    snapshot = source.split("func workspaceSnapshot", 1)[1].split(
        "func pressWorkspaceTarget", 1
    )[0]
    matrix = source.split("func matrixLayoutObservation", 1)[1].split(
        "func collapsedWorkbenchLayoutObservation", 1
    )[0]

    assert 'payload["semantic_graph"] = semanticGraph' in snapshot
    assert '"selected_component_id": selectedIdentity' in matrix
    assert '"right_detail_id": rightDetailIdentity' in matrix
    assert matrix.index('"selected_component_id"') != matrix.index(
        '"right_detail_id"'
    )


def test_shared_observer_reuses_one_semantic_graph_per_workspace_snapshot() -> None:
    """Avoid recomputing the bounded semantic graph for matrix readback."""

    source = shared_runtime._SWIFT_HELPER_SOURCE
    snapshot = source.split("func workspaceSnapshot", 1)[1].split(
        "func pressWorkspaceTarget", 1
    )[0]
    matrix = source.split("func matrixLayoutObservation", 1)[1].split(
        "func collapsedWorkbenchLayoutObservation", 1
    )[0]

    assert (
        snapshot.count("semanticGraphObservation(elements)")
        + matrix.count("semanticGraphObservation(elements)")
        == 1
    )
    assert "semanticGraph: [String: Any]" in matrix
    assert "semanticGraphObservation(elements)" not in matrix
    assert "matrixLayoutObservation(elements, anchorComponentIDs, semanticGraph)" in snapshot


def test_semantic_observer_preserves_explicit_active_profile_identity_when_matrix_is_closed(
    tmp_path: Path,
) -> None:
    source = shared_runtime._SWIFT_HELPER_SOURCE
    observer = source.split("func semanticGraphObservation", 1)[1].split(
        "func elementForToken", 1
    )[0]

    assert 'hasPrefix("component-map-active-profile:")' in observer
    assert 'result["active_profile_id"]' in observer

    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    normalised = port._normalise_semantic_graph(
        _semantic_graph_observation(
            active_profile_id="harnesskit.profile.runtime-layout-shared"
        ),
        "active profile semantic observation",
    )

    assert normalised["active_profile_id"] == (
        "harnesskit.profile.runtime-layout-shared"
    )


def test_semantic_observer_publishes_one_canonical_scene_layout_identity() -> None:
    source = shared_runtime._SWIFT_HELPER_SOURCE
    observer = source.split("func semanticGraphObservation", 1)[1].split(
        "func elementForToken", 1
    )[0]

    assert "semanticSceneLayoutIdentityObservation" in source
    assert 'result["projection_id"]' in observer
    assert 'result["settled_node_positions_hash"]' in observer


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("projection_id", None),
        ("projection_id", "projection with spaces"),
        ("settled_node_positions_hash", None),
        ("settled_node_positions_hash", "ABCDEF0123456789"),
        ("settled_node_positions_hash", "abc123"),
    ),
)
def test_semantic_graph_rejects_missing_or_invalid_scene_layout_identity(
    tmp_path: Path, field: str, invalid_value: object
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    observation = _semantic_graph_observation()
    if invalid_value is None:
        observation.pop(field)
    else:
        observation[field] = invalid_value

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._normalise_semantic_graph(observation, "scene layout identity")

    assert captured.value.code == "workspace_semantic_graph_observation_invalid"


def test_shared_observer_exposes_bounded_layout_snapshot_without_graph_walk() -> None:
    """Divider publication polling must not rescan the semantic graph."""

    source = shared_runtime._SWIFT_HELPER_SOURCE
    assert "func workspaceLayoutSnapshot" in source
    observer = source.split("func workspaceLayoutSnapshot", 1)[1].split(
        "\nfunc ", 1
    )[0]

    assert (
        "boundedDescendants(window.element, maximum: 1024)" in observer
        or "boundedDescendants(window.element, maximum: 2048)" in observer
    )
    for forbidden in (
        "semanticGraphObservation",
        "matrixLayoutObservation",
        "sotTreeObservation",
    ):
        assert forbidden not in observer
    for field in (
        '"layout_mode"',
        '"preferred_pair"',
        '"effective_pair"',
        '"separator_state"',
        '"layout_revision"',
    ):
        assert field in observer


def test_shared_observer_routes_layout_snapshot_without_anchor_arguments() -> None:
    source = shared_runtime._SWIFT_HELPER_SOURCE
    assert 'if command == "workspace-layout-snapshot"' in source
    command = source.split('if command == "workspace-layout-snapshot"', 1)[1].split(
        "\nif command ==", 1
    )[0]

    assert "workspaceLayoutSnapshot(pid)" in command
    assert "anchorComponentIDs" not in command
    assert "workspace-layout-snapshot" in command


def test_layout_snapshot_port_uses_narrow_helper_and_rejects_malformed_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = shared_runtime.MacOSRuntimeAutomationPort(tmp_path)
    calls: list[tuple[tuple[str, ...], float | None]] = []
    valid = {
        "accepted": True,
        "pid": 101,
        "window_id": 7,
        "layout_mode": "three-pane",
        "preferred_pair": {"left_px": 304, "right_px": 368},
        "effective_pair": {"left_px": 304, "right_px": 368},
        "separator_state": {"left": {"focused": False}, "right": {"focused": False}},
        "layout_revision": 4,
        "persisted": True,
    }

    def helper(
        *arguments: str, timeout_seconds: float | None = None
    ) -> dict[str, object]:
        calls.append((arguments, timeout_seconds))
        return valid

    monkeypatch.setattr(port, "_helper_json", helper)

    assert port.observe_workspace_layout(101, timeout_seconds=15.0) == valid
    assert calls == [
        (
            (
                "workspace-layout-snapshot",
                shared_runtime.BUNDLE_IDENTIFIER,
                "101",
            ),
            15.0,
        )
    ]

    malformed = dict(valid)
    malformed.pop("effective_pair")
    monkeypatch.setattr(port, "_helper_json", lambda *_arguments: malformed)
    with pytest.raises(shared_runtime.RuntimeQualificationError):
        port.observe_workspace_layout(101)


def test_full_workspace_poll_passes_remaining_case_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    snapshot = {
        "layout_revision": 4,
        "preferred_pair": {"left_px": 304, "right_px": 368},
        "effective_pair": {"left_px": 304, "right_px": 368},
        "semantic_graph": {},
    }
    received_timeouts: list[float] = []

    def observe(
        _pid: int, _anchors: tuple[str, ...], *, timeout_seconds: float
    ) -> dict[str, object]:
        received_timeouts.append(timeout_seconds)
        return snapshot

    monkeypatch.setattr(port, "_method", lambda _name: observe)
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 100.0)

    assert port._observe_workspace_once(101, snapshot, 115.0, "full poll") == snapshot
    assert received_timeouts == [15.0]


def test_layout_revision_poll_passes_remaining_case_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    snapshot = {"layout_revision": 4}
    received_timeouts: list[float] = []

    def observe(
        _pid: int, *, timeout_seconds: float
    ) -> dict[str, object]:
        received_timeouts.append(timeout_seconds)
        return snapshot

    monkeypatch.setattr(port, "_method", lambda _name: observe)
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 100.0)

    assert (
        port._wait_layout_revision(
            101,
            expected_revision=4,
            timeout_seconds=15.0,
            operation="layout poll",
        )
        == snapshot
    )
    assert received_timeouts == [15.0]


def test_amendment_case_scopes_every_helper_to_one_case_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    events: list[tuple[str, object]] = []

    class Scope:
        def __enter__(self) -> None:
            events.append(("enter", None))

        def __exit__(self, *_args: object) -> None:
            events.append(("exit", None))

    def helper_deadline(
        deadline: float, *, code: str, operation: str
    ) -> Scope:
        events.append(("scope", (deadline, code, operation)))
        return Scope()

    monkeypatch.setattr(port.base, "helper_deadline", helper_deadline, raising=False)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        port,
        "_exercise_amendment_pane",
        lambda case_id, pid, timeout_seconds, started: {
            "case_id": case_id,
            "pid": pid,
            "timeout_seconds": timeout_seconds,
            "started": started,
        },
    )

    result = port._exercise_amendment_case(
        "pane-left-collapse-reopen-restart", 101, 15.0
    )

    assert result["started"] == 100.0
    assert events == [
        (
            "scope",
            (
                115.0,
                "workspace_amendment_case_timeout",
                "pane-left-collapse-reopen-restart",
            ),
        ),
        ("enter", None),
        ("exit", None),
    ]


def test_amendment_full_snapshot_passes_remaining_case_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    received_timeouts: list[float] = []
    raw = {
        "accepted": True,
        "pid": 101,
        "window_id": 42,
        "targets": [],
    }

    def observe(
        _pid: int,
        _anchors: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        received_timeouts.append(timeout_seconds)
        return raw

    monkeypatch.setattr(port, "_method", lambda _name: observe)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 100.0)

    assert port._amendment_raw_workspace(101, "amendment", 115.0) == raw
    assert received_timeouts == [15.0]


def test_amendment_selector_read_passes_remaining_case_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    received_timeouts: list[float] = []
    target = {"selector": "#left-pane-disclosure"}

    def observe(
        _pid: int,
        _selector: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        received_timeouts.append(timeout_seconds)
        return {"present": True, "target": target}

    monkeypatch.setattr(port, "_method", lambda _name: observe)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 100.0)

    assert port._amendment_selector_target(
        101, "#left-pane-disclosure", "amendment", 115.0
    ) == target
    assert received_timeouts == [15.0]


def test_shared_observer_exposes_compact_steady_snapshot_without_full_walk() -> None:
    """Three 100ms stability samples must not repeat the full AX observer."""

    source = shared_runtime._SWIFT_HELPER_SOURCE
    observer = source.split("func workspaceSteadyToken", 1)[1].split(
        "func workspaceSnapshot", 1
    )[0]
    command = source.split('if command == "workspace-steady-snapshot"', 1)[1].split(
        "\nif command ==", 1
    )[0]

    assert "boundedDescendants(workspaceWindow.element, maximum: 2048)" in observer
    for forbidden in (
        "semanticGraphObservation",
        "matrixLayoutObservation",
        "sotTreeObservation",
        "typographySamples",
    ):
        assert forbidden not in observer
    for required in (
        '"layout_revision"',
        '"renderer"',
        '"camera"',
        '"active_profile_id"',
        '"selected_component_id"',
        '"disclosures"',
        '"graph_frames"',
        '"anchor_nodes"',
    ):
        assert required in observer
    assert 'workspaceSteadySnapshot(pid, anchorComponentIDs)' in command


def test_compact_steady_snapshot_reuses_full_responsive_layout_vocabulary() -> None:
    """The quick stability probe must classify every layout the full snapshot can emit."""

    source = shared_runtime._SWIFT_HELPER_SOURCE
    layout_helper = source.split("func workspaceLayoutMode", 1)[1].split(
        "func workspaceSteadyToken", 1
    )[0]
    steady = source.split("func workspaceSteadyToken", 1)[1].split(
        "func workspaceSnapshot", 1
    )[0]
    full = source.split("func workspaceSnapshot", 1)[1].split(
        "func workspacePickerObserve", 1
    )[0]

    for mode in (
        '"three-pane"',
        '"two-column"',
        '"stacked"',
        '"left-collapsed"',
        '"right-collapsed"',
        '"center-only"',
    ):
        assert mode in layout_helper
    assert "workspaceLayoutMode(" in steady
    assert "workspaceLayoutMode(" in full


def test_steady_snapshot_port_uses_compact_helper_and_rejects_invalid_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = shared_runtime.MacOSRuntimeAutomationPort(tmp_path)
    calls: list[tuple[str, ...]] = []
    valid = {
        "accepted": True,
        "pid": 101,
        "window_id": 7,
        "anchor_target_count": 3,
        "stability_token": {
            "layout_revision": 4,
            "layout": {
                "mode": "three-pane",
                "window": {"x": 0, "y": 0, "width": 1200, "height": 800},
                "shell": {"x": 0, "y": 0, "width": 1200, "height": 760},
                "left": {"x": 0, "y": 0, "width": 240, "height": 760},
                "center": {"x": 242, "y": 0, "width": 700, "height": 760},
                "right": {"x": 944, "y": 0, "width": 256, "height": 760},
            },
            "graph_body_identity": "component-map-body",
            "projection_id": SCENE_PROJECTION_ID,
            "settled_node_positions_hash": SETTLED_NODE_POSITIONS_HASH,
            "renderer": {
                "availability": "ready",
                "semantic_view_visible": True,
                "status": "3D renderer ready",
                "retry_visible": False,
            },
            "camera": {
                "ready": True,
                "scale": 1.0,
                "level": "개요",
                "zoom_out_enabled": True,
                "fit_enabled": True,
                "zoom_in_enabled": True,
            },
            "active_profile_id": "harnesskit.profile.engineering",
            "selected_component_id": "harnesskit.skill.example",
            "selected_relation_node_id": None,
            "locked_workflow_step": None,
            "right_detail_id": "harnesskit.skill.example",
            "disclosures": {
                "component_map": True,
                "profile_matrix": False,
                "left_pane": False,
                "right_pane": False,
            },
            "graph_frames": {
                "body": {"x": 0, "y": 0, "width": 800, "height": 400},
                "viewport": {"x": 0, "y": 0, "width": 800, "height": 360},
                "matrix": {"x": 0, "y": 400, "width": 800, "height": 120},
                "matrix_body": None,
            },
            "anchor_nodes": [
                    {
                        "component_id": component_id,
                        "frame": {"x": index * 20, "y": 10, "width": 12, "height": 12},
                        "visible": True,
                        "selected": False,
                }
                for index, component_id in enumerate(
                    ("harnesskit.skill.one", "harnesskit.skill.two", "harnesskit.skill.three")
                )
            ],
        },
    }

    def helper(*arguments: str) -> dict[str, object]:
        calls.append(arguments)
        return valid

    monkeypatch.setattr(port, "_helper_json", helper)

    assert port.observe_workspace_steady(
        101,
        ("harnesskit.skill.one", "harnesskit.skill.two", "harnesskit.skill.three"),
    ) == valid
    assert calls == [
        (
            "workspace-steady-snapshot",
            shared_runtime.BUNDLE_IDENTIFIER,
            "101",
            "harnesskit.skill.one,harnesskit.skill.two,harnesskit.skill.three",
        )
    ]

    malformed = dict(valid)
    malformed["stability_token"] = {"layout_revision": 4}
    monkeypatch.setattr(port, "_helper_json", lambda *_arguments: malformed)
    with pytest.raises(shared_runtime.RuntimeQualificationError):
        port.observe_workspace_steady(
            101,
            ("harnesskit.skill.one", "harnesskit.skill.two", "harnesskit.skill.three"),
        )

    for field, invalid_value in (
        ("projection_id", "projection with spaces"),
        ("settled_node_positions_hash", "ABCDEF0123456789"),
        ("settled_node_positions_hash", "abc123"),
    ):
        malformed = json.loads(json.dumps(valid))
        malformed["stability_token"][field] = invalid_value
        monkeypatch.setattr(port, "_helper_json", lambda *_arguments: malformed)
        with pytest.raises(shared_runtime.RuntimeQualificationError):
            port.observe_workspace_steady(
                101,
                (
                    "harnesskit.skill.one",
                    "harnesskit.skill.two",
                    "harnesskit.skill.three",
                ),
            )


def test_steady_snapshot_port_forwards_an_explicit_subprocess_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compact public observer carries its caller's remaining case budget to the helper."""

    port = shared_runtime.MacOSRuntimeAutomationPort(tmp_path)
    payload = {
        "accepted": True,
        "pid": 101,
        "window_id": 7,
        "anchor_target_count": 3,
        "stability_token": {
            "layout_revision": 4,
            "layout": {
                "mode": "three-pane",
                "window": {"x": 0, "y": 0, "width": 1200, "height": 800},
                "shell": {"x": 0, "y": 0, "width": 1200, "height": 760},
                "left": {"x": 0, "y": 0, "width": 240, "height": 760},
                "center": {"x": 242, "y": 0, "width": 700, "height": 760},
                "right": {"x": 944, "y": 0, "width": 256, "height": 760},
            },
            "graph_body_identity": "component-map-body",
            "projection_id": SCENE_PROJECTION_ID,
            "settled_node_positions_hash": SETTLED_NODE_POSITIONS_HASH,
            "renderer": {
                "availability": "ready",
                "semantic_view_visible": True,
                "status": "3D renderer ready",
                "retry_visible": False,
            },
            "camera": {
                "ready": True,
                "scale": 1.0,
                "level": "개요",
                "zoom_out_enabled": True,
                "fit_enabled": True,
                "zoom_in_enabled": True,
            },
            "active_profile_id": None,
            "selected_component_id": None,
            "selected_relation_node_id": None,
            "locked_workflow_step": None,
            "right_detail_id": None,
            "disclosures": {
                "component_map": True,
                "profile_matrix": False,
                "left_pane": False,
                "right_pane": False,
            },
            "graph_frames": {
                "body": {"x": 0, "y": 0, "width": 800, "height": 400},
                "viewport": {"x": 0, "y": 0, "width": 800, "height": 360},
                "matrix": {"x": 0, "y": 400, "width": 800, "height": 120},
                "matrix_body": None,
            },
            "anchor_nodes": [
                {
                    "component_id": component_id,
                    "frame": {"x": index * 20, "y": 10, "width": 12, "height": 12},
                    "visible": True,
                    "selected": False,
                }
                for index, component_id in enumerate(
                    (
                        "harnesskit.skill.one",
                        "harnesskit.skill.two",
                        "harnesskit.skill.three",
                    )
                )
            ],
        },
    }
    helper_timeouts: list[float | None] = []

    def helper(
        *_arguments: str, timeout_seconds: float | None = None
    ) -> dict[str, object]:
        helper_timeouts.append(timeout_seconds)
        return payload

    monkeypatch.setattr(port, "_helper_json", helper)

    assert port.observe_workspace_steady(
        101,
        ("harnesskit.skill.one", "harnesskit.skill.two", "harnesskit.skill.three"),
        timeout_seconds=0.25,
    ) == payload
    assert helper_timeouts == [0.25]


@pytest.mark.parametrize("mode", ("two-column", "stacked"))
def test_steady_snapshot_port_accepts_full_responsive_layout_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """The narrow port accepts the same non-collapsed responsive modes as full evidence."""

    port = shared_runtime.MacOSRuntimeAutomationPort(tmp_path)
    payload = {
        "accepted": True,
        "pid": 101,
        "window_id": 7,
        "anchor_target_count": 3,
        "stability_token": {
            "layout_revision": 4,
            "layout": {
                "mode": mode,
                "window": {"x": 0, "y": 0, "width": 1200, "height": 800},
                "shell": {"x": 0, "y": 0, "width": 1200, "height": 760},
                "left": {"x": 0, "y": 0, "width": 240, "height": 760},
                "center": {"x": 242, "y": 0, "width": 700, "height": 760},
                "right": {"x": 944, "y": 0, "width": 256, "height": 760},
            },
            "graph_body_identity": "component-map-body",
            "projection_id": SCENE_PROJECTION_ID,
            "settled_node_positions_hash": SETTLED_NODE_POSITIONS_HASH,
            "renderer": {
                "availability": "ready",
                "semantic_view_visible": True,
                "status": "3D renderer ready",
                "retry_visible": False,
            },
            "camera": {
                "ready": True,
                "scale": 1.0,
                "level": "개요",
                "zoom_out_enabled": True,
                "fit_enabled": True,
                "zoom_in_enabled": True,
            },
            "active_profile_id": None,
            "selected_component_id": None,
            "selected_relation_node_id": None,
            "locked_workflow_step": None,
            "right_detail_id": None,
            "disclosures": {
                "component_map": True,
                "profile_matrix": False,
                "left_pane": False,
                "right_pane": False,
            },
            "graph_frames": {
                "body": {"x": 0, "y": 0, "width": 800, "height": 400},
                "viewport": {"x": 0, "y": 0, "width": 800, "height": 360},
                "matrix": {"x": 0, "y": 400, "width": 800, "height": 120},
                "matrix_body": None,
            },
            "anchor_nodes": [
                {
                    "component_id": component_id,
                    "frame": {"x": index * 20, "y": 10, "width": 12, "height": 12},
                    "visible": True,
                    "selected": False,
                }
                for index, component_id in enumerate(
                    (
                        "harnesskit.skill.one",
                        "harnesskit.skill.two",
                        "harnesskit.skill.three",
                    )
                )
            ],
        },
    }
    monkeypatch.setattr(port, "_helper_json", lambda *_arguments, **_kwargs: payload)

    assert port.observe_workspace_steady(
        101,
        ("harnesskit.skill.one", "harnesskit.skill.two", "harnesskit.skill.three"),
    ) == payload


def test_steady_uses_three_narrow_samples_then_one_matching_full_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    token = {"layout_revision": 7, "renderer": {"availability": "ready"}}
    narrow_samples = deque(
        [
            {"identity": {"pid": 101, "window_id": 7}, "stability_token": token}
            for _ in range(3)
        ]
    )
    full = {
        "identity": {"pid": 101, "window_id": 7},
        "stability_token": token,
        "full_evidence": True,
    }
    calls: list[str] = []

    def method(name: str) -> Callable[..., dict[str, object]]:
        calls.append(name)
        if name == "observe_workspace_steady":
            return lambda _pid, _anchors, *, timeout_seconds: narrow_samples.popleft()
        if name == "observe_workspace":
            return lambda _pid, _anchors, *, timeout_seconds: full
        raise AssertionError(name)

    monkeypatch.setattr(port, "_method", method)
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    assert port._steady(101, "fixture load", 1.0) == full
    assert calls == ["observe_workspace_steady"] * 3 + ["observe_workspace"]
    assert narrow_samples == deque()


def test_renderer_ready_steady_uses_matching_full_snapshot_after_narrow_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    token = {"layout_revision": 8, "renderer": {"availability": "ready"}}
    narrow_samples = deque(
        [
            {"identity": {"pid": 101, "window_id": 7}, "stability_token": token}
            for _ in range(3)
        ]
    )
    full = {
        "identity": {"pid": 101, "window_id": 7},
        "stability_token": token,
        "full_evidence": True,
    }

    def method(name: str) -> Callable[..., dict[str, object]]:
        if name == "observe_workspace_steady":
            return lambda _pid, _anchors, *, timeout_seconds: narrow_samples.popleft()
        if name == "observe_workspace":
            return lambda _pid, _anchors, *, timeout_seconds: full
        raise AssertionError(name)

    monkeypatch.setattr(port, "_method", method)
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    assert port._steady_renderer_ready(101, "renderer retry", 1.0) == full
    assert narrow_samples == deque()


@pytest.mark.parametrize(
    ("method_name", "operation"),
    (
        ("_steady", "fixture load"),
        ("_steady_renderer_ready", "renderer retry"),
    ),
)
def test_steady_observers_pass_the_case_remaining_budget_to_every_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    operation: str,
) -> None:
    """A stable case cannot let its narrow/full subprocess calls outlive its deadline."""

    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    token = {"layout_revision": 8, "renderer": {"availability": "ready"}}
    narrow_samples = deque(
        [
            {"identity": {"pid": 101, "window_id": 7}, "stability_token": token}
            for _ in range(3)
        ]
    )
    full = {
        "identity": {"pid": 101, "window_id": 7},
        "stability_token": token,
        "full_evidence": True,
    }
    observed_timeouts: list[float] = []

    def method(name: str) -> Callable[..., dict[str, object]]:
        if name == "observe_workspace_steady":
            def narrow(
                _pid: int, _anchors: tuple[str, ...], *, timeout_seconds: float
            ) -> dict[str, object]:
                observed_timeouts.append(timeout_seconds)
                return narrow_samples.popleft()

            return narrow
        if name == "observe_workspace":
            def full_snapshot(
                _pid: int, _anchors: tuple[str, ...], *, timeout_seconds: float
            ) -> dict[str, object]:
                observed_timeouts.append(timeout_seconds)
                return full

            return full_snapshot
        raise AssertionError(name)

    monkeypatch.setattr(port, "_method", method)
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    assert getattr(port, method_name)(101, operation, 2.0) == full
    assert len(observed_timeouts) == 4
    assert all(0 < timeout <= 2.0 for timeout in observed_timeouts)


def test_steady_rejects_full_snapshot_when_the_case_deadline_is_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final full read cannot turn an expired stable sample into a success."""

    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    monkeypatch.setattr(
        port,
        "_method",
        lambda _name: (_ for _ in ()).throw(AssertionError("helper must not run")),
    )
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 5.1)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._full_snapshot_after_steady_token(
            101,
            "expired full read",
            {"layout_revision": 8},
            deadline=5.0,
            timeout_code="workspace_steady_state_timeout",
        )

    assert captured.value.code == "workspace_steady_state_timeout"


def test_steady_resets_narrow_sample_count_when_compact_token_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    before = {"layout_revision": 7, "renderer": {"availability": "ready"}}
    after = {"layout_revision": 8, "renderer": {"availability": "ready"}}
    narrow_samples = deque(
        [
            {"identity": {"pid": 101, "window_id": 7}, "stability_token": before},
            {"identity": {"pid": 101, "window_id": 7}, "stability_token": before},
            {"identity": {"pid": 101, "window_id": 7}, "stability_token": after},
            {"identity": {"pid": 101, "window_id": 7}, "stability_token": after},
            {"identity": {"pid": 101, "window_id": 7}, "stability_token": after},
        ]
    )
    full = {
        "identity": {"pid": 101, "window_id": 7},
        "stability_token": after,
        "full_evidence": True,
    }
    narrow_call_count = 0

    def narrow(
        _pid: int, _anchors: tuple[str, ...], *, timeout_seconds: float
    ) -> dict[str, object]:
        nonlocal narrow_call_count
        narrow_call_count += 1
        return narrow_samples.popleft()

    def method(name: str) -> Callable[..., dict[str, object]]:
        if name == "observe_workspace_steady":
            return narrow
        if name == "observe_workspace":
            return lambda _pid, _anchors, *, timeout_seconds: full
        raise AssertionError(name)

    monkeypatch.setattr(port, "_method", method)
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    assert port._steady(101, "revision changed", 1.0) == full
    assert narrow_call_count == 5


def test_steady_rejects_full_snapshot_when_compact_token_no_longer_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    narrow_token = {"layout_revision": 7, "renderer": {"availability": "ready"}}
    full_token = {"layout_revision": 8, "renderer": {"availability": "ready"}}
    narrow_samples = deque(
        [
            {"identity": {"pid": 101, "window_id": 7}, "stability_token": narrow_token}
            for _ in range(3)
        ]
    )
    full = {
        "identity": {"pid": 101, "window_id": 7},
        "stability_token": full_token,
    }

    def method(name: str) -> Callable[..., dict[str, object]]:
        if name == "observe_workspace_steady":
            return lambda _pid, _anchors, *, timeout_seconds: narrow_samples.popleft()
        if name == "observe_workspace":
            return lambda _pid, _anchors, *, timeout_seconds: full
        raise AssertionError(name)

    monkeypatch.setattr(port, "_method", method)
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._steady(101, "full mismatch", 1.0)
    assert captured.value.code == "workspace_steady_full_snapshot_mismatch"


def test_layout_revision_wait_uses_layout_snapshot_while_cases_keep_full_final_evidence() -> None:
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    wait = source.split("    def _wait_layout_revision(\n", 1)[1].split(
        "    def _wait_write_denied_outcome", 1
    )[0]
    exercise = source.split("    def exercise_case(\n", 1)[1].split(
        "    def _graph_fingerprint_matches", 1
    )[0]

    assert 'self._method("observe_workspace_layout")' in wait
    assert 'self._method("observe_workspace")' not in wait
    for case_id in (
        'elif case_id == "separator-keyboard-step":',
        'elif case_id == "minimum-overshoot-clamped":',
        'elif case_id == "maximum-overshoot-clamped":',
    ):
        branch = exercise.split(case_id, 1)[1]
        assert "_observe_workspace_once(" in branch


def test_shared_observer_exposes_typed_bounded_3d_semantic_readback() -> None:
    source = shared_runtime._SWIFT_HELPER_SOURCE
    observer = source.split("func semanticGraphObservation", 1)[1].split(
        "func elementForToken", 1
    )[0]

    assert '"relations": relations' in observer
    assert '"components": components' in observer
    assert '"workflow_steps": workflowSteps' in observer
    assert '"identity_overlay": identityOverlay' in observer
    assert 'stableElementMatches("component-map-identity-overlay")' in observer
    assert 'stableElementMatches("component-map-identity-kind")' in observer
    assert 'stableElementMatches("component-map-identity-count")' in observer
    assert 'component-map-identity-id' not in observer
    assert 'component-map-identity-meta' not in observer
    assert '"renderer": renderer' in observer
    assert 'stableElementMatches("component-map-fit-report")' in observer
    assert 'stableElementMatches("component-map-camera-pose")' in observer
    assert 'result["fit_report"] = fitReport' in observer
    assert 'camera["position"] = position' in observer
    assert 'camera["target"] = target' in observer
    assert 'result["workflow_inspector"] = workflowInspector' in observer
    assert "JSONSerialization.jsonObject" not in observer
    assert "raw_ax_tree" not in observer

    fit_parser = source.split("func semanticFitReportFromText", 1)[1].split(
        "func semanticFitReportObservation", 1
    )[0]
    for field in (
        '"identity_count"',
        '"relation_count"',
        '"component_count"',
        '"envelope_count"',
        '"camera_status"',
        '"in_frustum_envelope_count"',
        '"total_envelope_count"',
        '"largest_dimension_occupancy"',
        '"scene_bounds"',
    ):
        assert field in fit_parser
    camera_parser = source.split("func semanticCameraPoseFromText", 1)[1].split(
        "func semanticCameraPoseObservation", 1
    )[0]
    assert '"position"' in camera_parser
    assert '"target"' in camera_parser


def test_graph_fingerprint_requires_right_detail_identity() -> None:
    expected = _graph_fingerprint()
    observed = json.loads(json.dumps(expected))

    assert runtime._graph_fingerprint_equal(expected, observed)

    observed["right_detail_id"] = FIXTURE_ANCHOR_COMPONENT_IDS[0]
    assert not runtime._graph_fingerprint_equal(expected, observed)


@pytest.mark.parametrize(
    ("field", "changed_value"),
    (
        ("projection_id", "graph:other-snapshot:runtime-layout-seed"),
        ("settled_node_positions_hash", "fedcba9876543210"),
        ("selected_workflow_id", "harnesskit.workflow.other"),
        (
            "disclosures",
            {
                "component_map": False,
                "profile_matrix": True,
                "left_pane": False,
                "right_pane": False,
            },
        ),
    ),
)
def test_graph_fingerprint_rejects_canonical_scene_or_disclosure_drift(
    field: str, changed_value: object
) -> None:
    expected = _graph_fingerprint()
    observed = json.loads(json.dumps(expected))
    observed[field] = changed_value

    assert not runtime._graph_fingerprint_equal(expected, observed)


def test_normalised_graph_fingerprint_binds_scene_workflow_and_disclosures(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    workflow_id = "harnesskit.workflow.runtime-layout-workflow"
    raw = _raw_hover_workspace_observation()
    raw["semantic_graph"] = _semantic_graph_observation(
        selected_workflow_id=workflow_id,
        selected_profile=False,
        camera_scale=1.25,
    )
    raw["stability_token"] = {
        "projection_id": SCENE_PROJECTION_ID,
        "settled_node_positions_hash": SETTLED_NODE_POSITIONS_HASH,
        "disclosures": {
            "component_map": True,
            "profile_matrix": False,
            "left_pane": False,
            "right_pane": True,
        },
    }

    snapshot = port._normalise_observation(raw, "canonical graph fingerprint")

    fingerprint = snapshot["graph_fingerprint"]
    assert isinstance(fingerprint, dict)
    assert fingerprint["projection_id"] == SCENE_PROJECTION_ID
    assert fingerprint["settled_node_positions_hash"] == SETTLED_NODE_POSITIONS_HASH
    assert fingerprint["selected_workflow_id"] == workflow_id
    assert fingerprint["disclosures"] == {
        "component_map": True,
        "profile_matrix": False,
        "left_pane": False,
        "right_pane": True,
    }


def test_full_snapshot_rejects_scene_identity_that_disagrees_with_semantic_graph(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    raw = _raw_hover_workspace_observation()
    raw["semantic_graph"] = _semantic_graph_observation(camera_scale=1.25)
    raw["stability_token"] = {
        "projection_id": "graph:other-snapshot:runtime-layout-seed",
        "settled_node_positions_hash": SETTLED_NODE_POSITIONS_HASH,
        "disclosures": {
            "component_map": True,
            "profile_matrix": False,
            "left_pane": False,
            "right_pane": False,
        },
    }

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._normalise_observation(raw, "scene identity mismatch")

    assert captured.value.code == "workspace_scene_layout_identity_mismatch"


@pytest.mark.parametrize(
    ("field", "expected_value", "observed_value"),
    (
        (
            "selected_relation_node_id",
            f"profile:{CANONICAL_PROFILE_ID}",
            "workflow:harnesskit.workflow.runtime-layout-workflow",
        ),
        (
            "locked_step",
            {
                "workflow_id": "harnesskit.workflow.runtime-layout-workflow",
                "ordinal": 1,
            },
            {
                "workflow_id": "harnesskit.workflow.runtime-layout-workflow",
                "ordinal": 2,
            },
        ),
    ),
)
def test_graph_fingerprint_rejects_relation_or_locked_step_drift(
    field: str, expected_value: object, observed_value: object
) -> None:
    expected = {**_graph_fingerprint(), field: expected_value}
    observed = json.loads(json.dumps(expected))
    observed[field] = observed_value

    assert not runtime._graph_fingerprint_equal(expected, observed)


@pytest.mark.parametrize(
    ("vector", "axis"),
    (("position", "x"), ("target", "z")),
)
def test_graph_fingerprint_rejects_camera_pose_drift(
    vector: str, axis: str
) -> None:
    expected = {
        **_graph_fingerprint(),
        "camera_pose": {
            "position": {"x": 52.0, "y": -18.0, "z": 240.0},
            "target": {"x": 4.0, "y": 5.0, "z": 6.0},
        },
    }
    observed = json.loads(json.dumps(expected))
    observed_pose = observed["camera_pose"]
    assert isinstance(observed_pose, dict)
    observed_vector = observed_pose[vector]
    assert isinstance(observed_vector, dict)
    observed_vector[axis] = float(observed_vector[axis]) + 1.0

    assert not runtime._graph_fingerprint_equal(expected, observed)


def test_semantic_camera_pose_is_typed_and_preserved(tmp_path: Path) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    raw = _raw_hover_workspace_observation()
    semantic = _semantic_graph_observation(camera_scale=1.25)
    camera = semantic["camera"]
    assert isinstance(camera, dict)
    camera.update(
        {
            "position": {"x": 52.0, "y": -18.0, "z": 240.0},
            "target": {"x": 4.0, "y": 5.0, "z": 6.0},
        }
    )
    raw["semantic_graph"] = semantic

    snapshot = port._normalise_observation(raw, "typed camera pose")

    graph = snapshot["graph_fingerprint"]
    assert isinstance(graph, dict)
    assert graph["camera_pose"] == {
        "position": {"x": 52.0, "y": -18.0, "z": 240.0},
        "target": {"x": 4.0, "y": 5.0, "z": 6.0},
    }


def test_semantic_graph_accepts_webkit_checkboxes_and_unprofiled_relation(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    raw = _raw_hover_workspace_observation()
    semantic = _semantic_graph_observation(camera_scale=1.25)

    relations = semantic["relations"]
    components = semantic["components"]
    assert isinstance(relations, list)
    assert isinstance(components, list)
    for record in (*relations, *components):
        assert isinstance(record, dict)
        record["role"] = "AXCheckBox"

    component_ids = [
        str(component["component_id"])
        for component in components
        if isinstance(component, dict)
    ]
    relations.append(
        {
            "dom_identifier": "semantic-relation:unprofiled:projection:unprofiled",
            "role": "AXCheckBox",
            "name": "Relation unprofiled:projection:unprofiled",
            "selected": False,
            "focused": False,
            "visible": True,
            "frame": {"x": 472, "y": 120, "width": 44, "height": 36},
            "node_id": "unprofiled:projection:unprofiled",
            "canonical_id": "projection:unprofiled",
            "relation_kind": "unprofiled",
            "exact_count": len(component_ids),
            "member_component_ids": component_ids,
        }
    )
    semantic["fit_report"] = _fit_report_observation(
        relation_count=len(relations), component_count=len(components)
    )
    raw["semantic_graph"] = semantic

    snapshot = port._normalise_observation(
        raw, "WebKit checkbox and Unprofiled relation"
    )

    graph = snapshot["semantic_graph"]
    assert isinstance(graph, dict)
    normalised_relations = graph["relations"]
    assert isinstance(normalised_relations, list)
    assert any(
        relation["relation_kind"] == "unprofiled"
        and relation["canonical_id"] == "projection:unprofiled"
        and relation["role"] == "AXCheckBox"
        for relation in normalised_relations
        if isinstance(relation, dict)
    )
    assert all(
        component["role"] == "AXCheckBox"
        for component in graph["components"]
        if isinstance(component, dict)
    )


def test_semantic_graph_accepts_explicit_empty_overlay_and_renderer_presence(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    raw = _raw_hover_workspace_observation()
    semantic = _semantic_graph_observation(camera_scale=1.25)

    semantic["identity_overlay"] = {
        "container_present": True,
        "title_present": True,
        "kind_present": False,
        "count_present": False,
        "container": {
            "dom_identifier": "component-map-identity-overlay",
            "role": "AXGroup",
            "name": "",
            "selected": False,
            "focused": False,
            "visible": True,
            "frame": {"x": 360, "y": 120, "width": 290, "height": 40},
        },
        "title": {
            "dom_identifier": "component-map-identity-title",
            "role": "AXGroup",
            "name": "",
            "selected": False,
            "focused": False,
            "visible": True,
            "frame": {"x": 371, "y": 130, "width": 74, "height": 21},
        },
    }
    renderer = semantic["renderer"]
    assert isinstance(renderer, dict)
    renderer.pop("status")
    renderer.update({"status_present": False, "status_visible": False})
    raw["semantic_graph"] = semantic

    snapshot = port._normalise_observation(
        raw, "explicit empty semantic presence"
    )

    graph = snapshot["semantic_graph"]
    assert isinstance(graph, dict)
    identity = graph["identity_overlay"]
    assert isinstance(identity, dict)
    assert identity["container_present"] is True
    assert identity["title_present"] is True
    assert identity["kind_present"] is False
    assert identity["count_present"] is False
    assert "kind" not in identity
    assert "count" not in identity
    normalised_renderer = graph["renderer"]
    assert isinstance(normalised_renderer, dict)
    assert normalised_renderer["status_present"] is False
    assert normalised_renderer["status_visible"] is False
    assert "status" not in normalised_renderer


def test_renderer_retry_rejects_restored_camera_fingerprint_drift() -> None:
    payload = _strict_case_payload(
        "renderer-retry-snapshot-preserved", "a" * 64
    )
    action = payload["action"]
    after = payload["after"]
    assert isinstance(action, dict)
    assert isinstance(after, dict)
    baseline = json.loads(json.dumps(after["graph_fingerprint"]))
    action["pre_fallback_ready_graph_fingerprint"] = baseline
    after_fingerprint = after["graph_fingerprint"]
    assert isinstance(after_fingerprint, dict)
    after_fingerprint["scale"] = float(baseline["scale"]) + 0.5

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(
            "renderer-retry-snapshot-preserved", payload
        )

    assert captured.value.code == "workspace_renderer_retry_fingerprint_changed"


def test_semantic_fallback_rejects_mismatched_ready_fingerprint_evidence() -> None:
    payload = _strict_case_payload(
        "semantic-fallback-user-transition", "a" * 64
    )
    action = payload["action"]
    before = payload["before"]
    assert isinstance(action, dict)
    assert isinstance(before, dict)
    baseline = json.loads(json.dumps(before["graph_fingerprint"]))
    baseline["scale"] = float(baseline["scale"]) + 0.5
    action["pre_fallback_ready_graph_fingerprint"] = baseline

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(
            "semantic-fallback-user-transition", payload
        )

    assert captured.value.code == "workspace_semantic_fallback_fingerprint_mismatch"


def test_shared_observer_reports_matrix_visible_height_in_center_viewport() -> None:
    source = shared_runtime._SWIFT_HELPER_SOURCE
    snapshot = source.split("func workspaceSnapshot", 1)[1].split(
        "func pressWorkspaceTarget", 1
    )[0]

    assert "frame.intersection(centerFrame)" in snapshot
    assert 'payload["matrix_usable_height"]' in snapshot


def test_matrix_observer_payload_is_typed_and_preserved_fail_closed(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    raw = _raw_hover_workspace_observation()
    expected_layout = _matrix_layout_observation(expanded=True, scroll_top_px=216)
    expected_wrap = _matrix_wrap_samples()[0]
    raw["matrix_layout"] = expected_layout
    raw["matrix_wrap_sample"] = expected_wrap
    raw["matrix_usable_height"] = 0

    snapshot = port._normalise_observation(raw, "matrix observer payload")

    assert snapshot["matrix_layout"] == expected_layout
    assert snapshot["matrix_wrap_sample"] == expected_wrap
    assert snapshot["matrix_usable_height"] == 0

    malformed = _raw_hover_workspace_observation()
    malformed["matrix_layout"] = {"disclosures": "not-a-record"}
    malformed["matrix_wrap_sample"] = expected_wrap

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._normalise_observation(malformed, "matrix observer malformed")

    assert captured.value.code == "workspace_matrix_observation_invalid"


def test_semantic_graph_payload_is_typed_and_preserved_fail_closed(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    expected = _semantic_graph_observation(camera_scale=1.25)
    raw = _raw_hover_workspace_observation()
    raw["semantic_graph"] = expected

    snapshot = port._normalise_observation(raw, "semantic graph payload")

    observed = snapshot["semantic_graph"]
    assert {component["kind"] for component in observed["components"]} == set(
        GRAPH_READABILITY_KIND_TOKENS
    )
    assert len(observed["components"]) == len(
        {component["component_id"] for component in observed["components"]}
    )
    assert snapshot["graph_fingerprint"]["selected_component_id"] == (
        HOVER_COMPONENT_ID
    )

    malformed_payloads = []
    for mutation in (
        lambda payload: payload["components"].append(
            json.loads(json.dumps(payload["components"][0]))
        ),
        lambda payload: payload["components"][0].update(
            {"component_id": "harnesskit.mode.runtime-layout-mode"}
        ),
        lambda payload: payload["relations"][0].update(
            {"member_component_ids": ["harnesskit.skill.missing-component"]}
        ),
        lambda payload: payload["components"][0].update(
            {"selected": True}
        ),
    ):
        malformed = json.loads(json.dumps(expected))
        mutation(malformed)
        malformed_payloads.append(malformed)

    for malformed in malformed_payloads:
        invalid_raw = _raw_hover_workspace_observation()
        invalid_raw["semantic_graph"] = malformed
        with pytest.raises(runtime.RuntimeQualificationError) as captured:
            port._normalise_observation(
                invalid_raw, "semantic graph malformed"
            )
        assert captured.value.code in {
            "workspace_semantic_graph_observation_invalid",
            "workspace_semantic_graph_selection_ambiguous",
        }


def test_semantic_fit_report_is_typed_and_preserved(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    expected = _semantic_graph_observation(camera_scale=1.25)
    fit_report = _fit_report_observation()
    expected["fit_report"] = fit_report
    raw = _raw_hover_workspace_observation()
    raw["semantic_graph"] = expected

    snapshot = port._normalise_observation(raw, "semantic Fit report")

    semantic = snapshot["semantic_graph"]
    assert isinstance(semantic, dict)
    assert semantic["fit_report"] == fit_report


def test_packaged_fit_case_rejects_cropped_scene_camera_report() -> None:
    payload = _strict_case_payload("3d-atlas-fit-light", "a" * 64)
    graph_baseline = _graph_fingerprint()
    runtime._validate_case_invariants(
        "3d-atlas-fit-light",
        json.loads(json.dumps(payload)),
        graph_baseline,
    )
    after = payload["after"]
    assert isinstance(after, dict)
    semantic = after["semantic_graph"]
    assert isinstance(semantic, dict)
    fit_report = semantic["fit_report"]
    assert isinstance(fit_report, dict)
    fit_report["camera_status"] = "cropped"
    fit_report["in_frustum_envelope_count"] = int(
        fit_report["total_envelope_count"]
    ) - 1

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(
            "3d-atlas-fit-light", payload, graph_baseline
        )

    assert captured.value.code == "workspace_graph_readability_contract_mismatch"


def test_direct_component_selection_rejects_a_static_contextual_camera() -> None:
    payload = _strict_case_payload("3d-component-direct-select", "a" * 64)
    graph_baseline = _graph_fingerprint()
    action = payload["action"]
    assert isinstance(action, dict)
    action["contextual_camera_evidence"] = {
        "origin": "graph",
        "intent": "contextual",
        "observation_mode": "endpoint-only-ax",
        "baseline": {
            "position": {"x": 0.0, "y": 0.0, "z": 240.0},
            "target": {"x": 0.0, "y": 0.0, "z": 0.0},
        },
        "settled": {
            "position": {"x": 0.0, "y": 0.0, "z": 240.0},
            "target": {"x": 0.0, "y": 0.0, "z": 0.0},
        },
        "camera_changed": True,
        "anchor_hash_before": "a" * 64,
        "anchor_hash_after": "a" * 64,
    }

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(
            "3d-component-direct-select", payload, graph_baseline
        )

    assert captured.value.code == "workspace_graph_contextual_camera_unconfirmed"


@pytest.mark.parametrize("mutation", ("camera", "anchor"))
def test_workflow_step_selection_requires_runtime_state_invariance(
    mutation: str,
) -> None:
    case_id = "workflow-step-selection-invariant"
    payload = _case_payload(case_id, pid=101, executable_sha256="e" * 64)
    evidence = payload["action"]["ordinary_selection_state_evidence"]
    assert isinstance(evidence, dict)
    if mutation == "camera":
        evidence["camera_after"]["camera_pose"]["position"]["x"] = 12.0
    else:
        evidence["anchor_nodes_after"][0]["frame"]["x"] += 12

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._validate_case_invariants(case_id, payload)

    assert captured.value.code == "workflow_step_selection_contract_mismatch"


@pytest.mark.parametrize("graph_lod", ("overview", "select", "detail", "unavailable"))
def test_graph_lod_is_typed_and_preserved_fail_closed(
    tmp_path: Path,
    graph_lod: str,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    raw = _raw_hover_workspace_observation()
    raw["graph_lod"] = graph_lod

    snapshot = port._normalise_observation(raw, "graph LOD payload")

    assert snapshot["graph_lod"] == graph_lod

    raw["graph_lod"] = "invented"
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._normalise_observation(raw, "graph LOD malformed")
    assert captured.value.code == "workspace_graph_lod_invalid"


def test_collapsed_workbench_observer_payload_is_typed_and_preserved_fail_closed(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.runtime_home = tmp_path / "home"
    expected = _collapsed_workbench_layout_observation()
    raw = _raw_hover_workspace_observation()
    raw["collapsed_workbench_layout"] = expected

    snapshot = port._normalise_observation(raw, "collapsed workbench payload")

    assert snapshot["collapsed_workbench_layout"] == expected

    malformed = _raw_hover_workspace_observation()
    malformed["collapsed_workbench_layout"] = {
        **expected,
        "stable_ids": {"center": "workbench"},
    }

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._normalise_observation(malformed, "collapsed workbench malformed")

    assert captured.value.code == "workspace_collapsed_layout_observation_invalid"


def test_graph_state_uses_semantic_ax_selection_and_cg_event_pan() -> None:
    source = Path(runtime.__file__).read_text(encoding="utf-8")

    assert 'f"semantic-component:{_CANONICAL_ANCHOR_COMPONENT_IDS[1]}"' in source
    assert "self._press(pid, component_target)" in source
    assert 'self._drag_target(pid, "graph-viewport", 48)' in source
    assert 'self._click(pid, component_target)' not in source


@pytest.mark.parametrize(
    ("case_id", "starting_pair", "published_pairs", "expected_drags", "expected_keys"),
    (
        (
            "minimum-overshoot-clamped",
            {"left_px": 328, "right_px": 336},
            (
                {"left_px": 200, "right_px": 336},
                {"left_px": 200, "right_px": 184},
            ),
            (("left", -4096), ("right", 4096)),
            (("left", "ArrowLeft"), ("right", "ArrowRight")),
        ),
        (
            "maximum-overshoot-clamped",
            {"left_px": 200, "right_px": 184},
            (
                {"left_px": 512, "right_px": 184},
                {"left_px": 200, "right_px": 184},
                {"left_px": 200, "right_px": 560},
            ),
            (("left", 4096), ("left", -4096), ("right", -4096)),
            (("left", "ArrowRight"), ("right", "ArrowLeft")),
        ),
    ),
)
def test_real_port_boundary_cases_drive_both_dividers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    starting_pair: dict[str, int],
    published_pairs: tuple[dict[str, int], ...],
    expected_drags: tuple[tuple[str, int], ...],
    expected_keys: tuple[tuple[str, str], ...],
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    drags: list[tuple[str, int]] = []
    keys: list[tuple[str, str]] = []
    resizes: list[tuple[int, int]] = []
    revisions = iter(range(11, 11 + len(published_pairs)))
    publication_queue = [
        {
            "layout_mode": "three-pane",
            "preferred_pair": dict(pair),
            "effective_pair": dict(pair),
            "layout_revision": revision,
        }
        for pair, revision in zip(published_pairs, revisions, strict=True)
    ]
    steady_queue = []
    if case_id == "maximum-overshoot-clamped":
        steady_queue.append(
            {
                "layout_mode": "three-pane",
                "preferred_pair": dict(starting_pair),
                "effective_pair": dict(starting_pair),
                "layout_revision": 10,
            }
        )
    for index, snapshot in enumerate(publication_queue):
        if case_id == "maximum-overshoot-clamped" and index == 1:
            continue
        steady_queue.append(json.loads(json.dumps(snapshot)))
    initial = {
        "layout_mode": "three-pane",
        "preferred_pair": dict(starting_pair),
        "effective_pair": dict(starting_pair),
        "layout_revision": 10,
    }
    steady_results = iter([initial, *steady_queue])

    monkeypatch.setattr(port, "_steady", lambda *_args, **_kwargs: next(steady_results))
    monkeypatch.setattr(
        port,
        "_wait_layout_revision",
        lambda *_args, **_kwargs: next(steady_results),
    )
    monkeypatch.setattr(
        port,
        "_wait_publication_delta",
        lambda *_args, **_kwargs: publication_queue.pop(0),
    )
    monkeypatch.setattr(
        port,
        "_drag",
        lambda _pid, side, delta, **_kwargs: drags.append((side, delta)),
    )

    def key(_pid: int, side: str, name: str) -> dict[str, object]:
        keys.append((side, name))
        expected_values = (
            {"left": 200, "right": 184}
            if case_id.startswith("minimum")
            else {"left": 512, "right": 560}
        )
        value = expected_values[side]
        return {
            "accepted": True,
            "kind": "keyboard",
            "target": f"{side}-divider",
            "key": name,
            "focused_readback": True,
            "before": {"current_value": value},
            "after": {"current_value": value},
        }

    monkeypatch.setattr(port, "_key", key)
    monkeypatch.setattr(
        port,
        "_resize",
        lambda _pid, width, height: resizes.append((width, height)),
    )
    monkeypatch.setattr(
        port,
        "_payload",
        lambda _pid, action, before, after, publication_delta: {
            "action": action,
            "before": before,
            "after": after,
            "publication_delta": publication_delta,
        },
    )
    monkeypatch.setattr(
        port,
        "_observe_workspace_once",
        lambda _pid, expected, _deadline, _operation: expected,
    )

    payload = port.exercise_case(case_id, 101, 15.0)

    assert drags == list(expected_drags)
    assert keys == list(expected_keys)
    assert payload["publication_delta"] == len(published_pairs)
    assert len(payload["action"]["sub_actions"]) == 2
    assert resizes == ([(1600, 800)] if case_id.startswith("maximum") else [])
    if case_id == "maximum-overshoot-clamped":
        strict_payload = _strict_case_payload(case_id, "a" * 64)
        strict_payload["action"] = payload["action"]
        strict_payload["publication_delta"] = payload["publication_delta"]
        for phase in ("before", "after"):
            observed = payload[phase]
            strict_snapshot = strict_payload[phase]
            assert isinstance(observed, dict)
            assert isinstance(strict_snapshot, dict)
            strict_snapshot["preferred_pair"] = observed["preferred_pair"]
            strict_snapshot["effective_pair"] = observed["effective_pair"]
            strict_snapshot["layout_revision"] = observed["layout_revision"]
        runtime._validate_case_invariants(case_id, strict_payload)


def test_responsive_setup_releases_right_constraint_before_growing_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    snapshots = iter(
        (
            {
                "preferred_pair": {"left_px": 200, "right_px": 560},
                "effective_pair": {"left_px": 200, "right_px": 560},
                "layout_revision": 10,
            },
            {
                "preferred_pair": {"left_px": 200, "right_px": 376},
                "effective_pair": {"left_px": 200, "right_px": 376},
                "layout_revision": 11,
            },
            {
                "preferred_pair": {"left_px": 320, "right_px": 376},
                "effective_pair": {"left_px": 320, "right_px": 376},
                "layout_revision": 12,
            },
        )
    )
    drags: list[tuple[str, int]] = []
    monkeypatch.setattr(port, "_steady", lambda *_args, **_kwargs: next(snapshots))
    monkeypatch.setattr(
        port,
        "_wait_publication_delta",
        lambda *_args, **_kwargs: next(snapshots),
    )
    monkeypatch.setattr(
        port,
        "_drag",
        lambda _pid, side, delta, **_kwargs: drags.append((side, delta)),
    )

    port._reset_pair_for_responsive_cases(101, 15.0)

    assert drags == [("right", 184), ("left", 120)]
    assert port.preferred_pair == {"left_px": 320, "right_px": 376}


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("cg-window-mismatch", "workspace_physical_geometry_mismatch"),
        ("target-frame-mismatch", "workspace_physical_geometry_mismatch"),
        ("derived-containment-lie", "workspace_physical_geometry_mismatch"),
        ("derived-side-bound-lie", "workspace_side_bounds_invalid"),
    ),
)
def test_raw_physical_geometry_mutations_fail_closed(
    tmp_path: Path, mutation: str, expected_code: str
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    raw, targets, window = _raw_three_pane_physical_geometry()
    raw = json.loads(json.dumps(raw))
    targets = json.loads(json.dumps(targets))
    if mutation == "cg-window-mismatch":
        raw["cg_window"]["width"] = 1190
    elif mutation == "target-frame-mismatch":
        targets["center-pane"]["frame"]["x"] = 320
    elif mutation == "derived-containment-lie":
        raw["panes"]["right"]["x"] = 1201
        targets["right-pane"]["frame"]["x"] = 1201
    elif mutation == "derived-side-bound-lie":
        raw["side_bounds"]["left"]["current_value"] = 300
    else:
        raise AssertionError(mutation)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._normalise_physical_geometry(
            raw, window, targets, "three-pane", "physical geometry test"
        )

    assert captured.value.code == expected_code


def test_first_visible_sanitizer_preserves_bounded_frames_and_sequence_only() -> None:
    evidence = runtime._sanitize_first_visible_evidence(
        _raw_first_visible(), expected_pid=202, expected_window_id=42
    )

    assert evidence["first_visible_geometry"]["panes"]["left"]["width"] == 304
    assert [sample["event"] for sample in evidence["visibility_sequence"]] == [
        "identity-bound-hidden",
        "first-visible",
    ]
    assert "/private/secret" not in json.dumps(evidence)


def test_first_visible_sanitizer_rejects_misattributed_sequence() -> None:
    raw = _raw_first_visible()
    raw["visibility"]["visibility_sequence"][1]["pid"] = 999

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        runtime._sanitize_first_visible_evidence(
            raw, expected_pid=202, expected_window_id=42
        )

    assert captured.value.code == "first_visible_sequence_invalid"


def test_real_port_rejects_requested_pid_that_differs_from_current_session(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 202

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._resolve_pid(101)

    assert captured.value.code == "workspace_primary_pid_mismatch"


def test_real_port_rejects_snapshot_pid_that_differs_from_validated_session(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.artifact = runtime._artifact_identity(app)
    port.current_pid = 202

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._identity(
            202,
            {"identity": {"pid": 999, "window_id": 42}},
        )

    assert captured.value.code == "workspace_snapshot_pid_mismatch"


def test_fixture_load_passes_its_case_budget_to_checkout_ax_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = runtime.create_sanitized_fixture_checkout(tmp_path / "checkout")
    readiness_calls: list[tuple[int, str, str, float | None]] = []

    class ReadinessBase:
        def set_workspace_text(
            self,
            pid: int,
            identifier: str,
            value: str,
            *,
            timeout_seconds: float | None = None,
        ) -> bool:
            readiness_calls.append((pid, identifier, value, timeout_seconds))
            return True

    class PickerBoundaryReached(Exception):
        pass

    def stop_at_picker(_pid: int, _target: str) -> None:
        raise PickerBoundaryReached

    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.base = ReadinessBase()  # type: ignore[assignment]
    port.current_pid = 101
    monkeypatch.setattr(port, "_press", stop_at_picker)

    with pytest.raises(PickerBoundaryReached):
        port.load_fixture_after_picker_cancel(
            101,
            fixture.root,
            fixture.anchor_component_ids,
            15.0,
        )

    assert readiness_calls == [(101, "checkout-path", "", 15.0)]


def test_screenshot_redaction_uses_the_suite_ax_readiness_budget(tmp_path: Path) -> None:
    readiness_calls: list[tuple[int, str, str, float | None]] = []

    class ReadinessBase:
        def set_workspace_text(
            self,
            pid: int,
            identifier: str,
            value: str,
            *,
            timeout_seconds: float | None = None,
        ) -> bool:
            readiness_calls.append((pid, identifier, value, timeout_seconds))
            return True

    port = runtime.MacOSWorkspaceLayoutAutomationPort(
        tmp_path, readiness_timeout_seconds=15.0
    )
    port.base = ReadinessBase()  # type: ignore[assignment]

    port._clear_checkout_path_for_capture(101, "initial-default")

    assert readiness_calls == [(101, "checkout-path", "", 15.0)]


def test_native_checkout_picker_cancel_observes_app_owned_panel_then_uses_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv,
            0,
            b"cancel-sequence-invoked\n",
            b"",
        )

    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    panel = _picker_panel_evidence(101)

    class PickerObserver:
        def observe_native_checkout_picker(
            self, pid: int, timeout_seconds: float
        ) -> dict[str, object]:
            assert (pid, timeout_seconds) == (101, 1.0)
            return panel

        def wait_native_checkout_picker_closed(
            self,
            pid: int,
            panel_identifier: str,
            timeout_seconds: float,
        ) -> dict[str, object]:
            assert (pid, panel_identifier, timeout_seconds) == (
                101,
                runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
                1.0,
            )
            return _picker_closed_evidence(pid)

    port.base = PickerObserver()  # type: ignore[assignment]
    monkeypatch.setattr(runtime.subprocess, "run", run)

    evidence = port._cancel_native_checkout(101, 1.0)

    script = captured["argv"][2]  # type: ignore[index]
    assert "key code 53" in script
    for forbidden in ["AXFocusedUIElement", "windows", "sheets", "AXDialog"]:
        assert forbidden not in script
    assert evidence == {
        "empty_path_triggered": True,
        "native_panel_open_confirmed": True,
        "cancel_sequence_invoked": True,
        "cancel_panel_closed_confirmed": True,
        "directory_selected": False,
        "automation_principal": "System Events",
        "selection_surface": "native_directory_picker",
        "panel": panel,
        "cancel_panel_closure": _picker_closed_evidence(101),
    }


def test_native_checkout_picker_rejects_incomplete_cancel_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv,
            0,
            b"incomplete\n",
            b"",
        ),
    )
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)

    class PickerObserver:
        def observe_native_checkout_picker(
            self, pid: int, timeout_seconds: float
        ) -> dict[str, object]:
            return _picker_panel_evidence(pid)

    port.base = PickerObserver()  # type: ignore[assignment]

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port._cancel_native_checkout(101, 1.0)

    assert captured.value.code == "workspace_picker_action_failed"
    assert captured.value.blocked is False


def test_fixture_load_uses_native_picker_selection_after_cancel_without_path_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = runtime.create_sanitized_fixture_checkout(tmp_path / "checkout")
    set_values: list[str] = []
    presses: list[str] = []
    selections: list[tuple[int, Path, float]] = []
    selection_panel_observations: list[tuple[int, float]] = []
    selection_proof = _picker_selection_evidence(101)

    class NativePickerBase:
        def set_workspace_text(
            self,
            _pid: int,
            _identifier: str,
            value: str,
            *,
            timeout_seconds: float,
        ) -> bool:
            assert timeout_seconds == 5.0
            set_values.append(value)
            return True

        def _helper_json(self, command: str, *_arguments: str) -> dict[str, object]:
            if command == "workspace-value-target":
                return {
                    "accepted": True,
                    "target_readback": {"focused": True},
                }
            assert command == "workspace-value-equals"
            return {"accepted": True}

        def select_native_checkout_directory(
            self, pid: int, checkout: Path, timeout_seconds: float
        ) -> dict[str, object]:
            selections.append((pid, checkout, timeout_seconds))
            return selection_proof

        def observe_native_checkout_picker(
            self, pid: int, timeout_seconds: float
        ) -> dict[str, object]:
            selection_panel_observations.append((pid, timeout_seconds))
            return _picker_panel_evidence(pid)

    runtime_home = tmp_path / "home"
    log = runtime_home / "Library/Logs/io.github.pureliture.harnesskit/HarnessKit.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "SoT snapshot requested\n"
        "SoT snapshot loaded: 3 components, 1 relations, 1 profiles, 0 issues\n",
        encoding="utf-8",
    )
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.base = NativePickerBase()  # type: ignore[assignment]
    port.current_pid = 101
    port.runtime_home = runtime_home
    monkeypatch.setattr(port, "_press", lambda _pid, target: presses.append(target))
    monkeypatch.setattr(
        port,
        "_cancel_native_checkout",
        lambda _pid, _timeout: {
            "empty_path_triggered": True,
            "native_panel_open_confirmed": True,
            "cancel_sequence_invoked": True,
            "directory_selected": False,
            "automation_principal": "System Events",
            "selection_surface": "native_directory_picker",
            "panel": _picker_panel_evidence(101),
        },
    )
    monkeypatch.setattr(
        port,
        "_steady",
        lambda _pid, _operation, _timeout: {
            "graph_fingerprint": {"anchor_nodes": [{}, {}, {}]},
        },
    )

    observed = port.load_fixture_after_picker_cancel(
        101,
        fixture.root,
        fixture.anchor_component_ids,
        5.0,
    )

    assert set_values == [""]
    assert presses == ["label:HarnessKit 연결", "label:HarnessKit 연결"]
    assert selection_panel_observations == [(101, 5.0)]
    assert selections == [(101, fixture.root, 5.0)]
    picker = observed["picker_evidence"]
    assert isinstance(picker, dict)
    assert picker["fixture_registration_mode"] == "native_picker_after_cancel"
    assert picker["selection_panel"] == _picker_panel_evidence(101)
    assert picker["selection"] == selection_proof


def test_picker_cancel_unconfirmed_reports_bounded_focus_and_value_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = runtime.create_sanitized_fixture_checkout(tmp_path / "checkout")

    class CancelReadbackBase:
        def set_workspace_text(
            self,
            _pid: int,
            _identifier: str,
            _value: str,
            *,
            timeout_seconds: float,
        ) -> bool:
            assert timeout_seconds == 0.1
            return True

        def _helper_json(self, command: str, *_arguments: str) -> dict[str, object]:
            if command == "workspace-value-target":
                return {
                    "accepted": True,
                    "target_readback": {"focused": False},
                }
            assert command == "workspace-value-equals"
            return {"accepted": True}

    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.base = CancelReadbackBase()  # type: ignore[assignment]
    port.current_pid = 101
    monkeypatch.setattr(port, "_press", lambda _pid, _target: None)
    monkeypatch.setattr(
        port,
        "_cancel_native_checkout",
        lambda _pid, _timeout: {
            "empty_path_triggered": True,
            "native_panel_open_confirmed": True,
            "cancel_sequence_invoked": True,
            "directory_selected": False,
        },
    )
    monotonic_values = iter((0.0, 0.0, 0.2))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.load_fixture_after_picker_cancel(
            101,
            fixture.root,
            fixture.anchor_component_ids,
            0.1,
        )

    assert captured.value.code == "workspace_picker_cancel_unconfirmed"
    assert captured.value.evidence == {
        "attempt_count": 1,
        "target_accepted": True,
        "target_readback_present": True,
        "target_focused": False,
        "value_equals_accepted": True,
    }
    assert str(fixture.root) not in json.dumps(captured.value.evidence)


def test_sot_snapshot_completion_wait_uses_bounded_marker_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    marker_counts = iter(
        (
            {
                "sot_request_count": 1,
                "sot_completion_count": 0,
                "local_scan_start_count": 0,
            },
            {
                "sot_request_count": 1,
                "sot_completion_count": 1,
                "local_scan_start_count": 0,
            },
        )
    )
    monkeypatch.setattr(port, "_runtime_log_marker_counts", lambda: next(marker_counts))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    evidence = port._wait_for_sot_snapshot_completion(1.0)

    assert evidence == {
        "attempt_count": 2,
        "sot_request_count": 1,
        "sot_completion_count": 1,
        "local_scan_start_count": 0,
        "sot_completion_observed": True,
    }
    assert str(tmp_path) not in json.dumps(evidence)


def test_fixture_graph_not_ready_reports_bounded_observation_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = runtime.create_sanitized_fixture_checkout(tmp_path / "checkout")

    class NativePickerBase:
        def set_workspace_text(
            self,
            _pid: int,
            _identifier: str,
            _value: str,
            *,
            timeout_seconds: float,
        ) -> bool:
            assert timeout_seconds == 1.0
            return True

        def _helper_json(self, command: str, *_arguments: str) -> dict[str, object]:
            if command == "workspace-value-target":
                return {
                    "accepted": True,
                    "target_readback": {"focused": True},
                }
            assert command == "workspace-value-equals"
            return {"accepted": True}

        def select_native_checkout_directory(
            self, pid: int, _checkout: Path, _timeout_seconds: float
        ) -> dict[str, object]:
            return _picker_selection_evidence(pid)

    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.base = NativePickerBase()  # type: ignore[assignment]
    port.current_pid = 101
    original_method = port._method
    monkeypatch.setattr(
        port,
        "_method",
        lambda name: (
            (lambda _pid, _timeout: _picker_panel_evidence(101))
            if name == "observe_native_checkout_picker"
            else original_method(name)
        ),
    )
    monkeypatch.setattr(port, "_press", lambda *_args: None)
    monkeypatch.setattr(
        port,
        "_cancel_native_checkout",
        lambda _pid, _timeout: {
            "empty_path_triggered": True,
            "native_panel_open_confirmed": True,
            "cancel_sequence_invoked": True,
            "directory_selected": False,
            "automation_principal": "System Events",
            "selection_surface": "native_directory_picker",
            "panel": _picker_panel_evidence(101),
        },
    )
    monkeypatch.setattr(
        port,
        "_steady",
        lambda *_args: {
            "graph_fingerprint": {"anchor_nodes": [{}, {}]},
        },
    )
    monkeypatch.setattr(
        port,
        "_wait_for_sot_snapshot_completion",
        lambda _deadline: {
            "attempt_count": 1,
            "sot_request_count": 1,
            "sot_completion_count": 1,
            "local_scan_start_count": 0,
            "sot_completion_observed": True,
        },
    )
    monkeypatch.setattr(
        port,
        "_command_counters",
        lambda: {"sot_load_count": 1, "local_scan_start_count": 0},
    )
    monotonic_values = iter((0.0, 0.0, 0.0, 0.0, 0.0, 2.0))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.load_fixture_after_picker_cancel(
            101,
            fixture.root,
            fixture.anchor_component_ids,
            1.0,
        )

    assert captured.value.code == "fixture_graph_not_ready"
    assert captured.value.operation == "fixture load"
    assert captured.value.evidence == {
        "picker_selection_confirmed": True,
        "sot_completion_observed": True,
        "sot_completion_attempt_count": 1,
        "attempt_count": 1,
        "snapshot_unavailable_count": 0,
        "steady_timeout_count": 0,
        "stable_snapshot_observed": True,
        "anchor_node_count": 2,
        "sot_load_count": 1,
        "local_scan_start_count": 0,
        "unstable_field_counts": {},
    }
    assert str(fixture.root) not in json.dumps(captured.value.evidence)


def test_workspace_steady_observer_retries_transient_snapshot_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    token = {"layout_revision": 7, "renderer": {"availability": "ready"}}
    observations: deque[object] = deque(
        [
            runtime.RuntimeQualificationError(
                "workspace_steady_snapshot_unavailable", "workspace-observe"
            ),
            *[
                {"identity": {"pid": 101, "window_id": 7}, "stability_token": token}
                for _ in range(3)
            ],
        ]
    )
    full = {
        "identity": {"pid": 101, "window_id": 7},
        "stability_token": token,
        "revision": 7,
    }

    def observe_steady(
        _pid: int, _anchors: tuple[str, ...], *, timeout_seconds: float
    ) -> dict[str, object]:
        next_observation = observations.popleft()
        if isinstance(next_observation, Exception):
            raise next_observation
        return next_observation  # type: ignore[return-value]

    def observe_workspace(
        _pid: int, _anchors: tuple[str, ...], *, timeout_seconds: float
    ) -> dict[str, object]:
        return full

    def method(name: str) -> Callable[..., dict[str, object]]:
        if name == "observe_workspace_steady":
            return observe_steady
        return observe_workspace

    monkeypatch.setattr(port, "_method", method)
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    assert port._steady(101, "fixture load", 1.0) == full


def test_layout_publication_wait_accepts_atomic_revision_despite_graph_motion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    snapshots: deque[dict[str, object]] = deque(
        [
            {
                "layout_revision": 11,
                "layout_mode": "three-pane",
                "preferred_pair": {"left_px": 320, "right_px": 336},
                "effective_pair": {"left_px": 320, "right_px": 336},
                "separator_state": {"left": {"focused": True}},
                "semantic_graph": {"camera": {"position": {"x": x}}},
            }
            for x in (1.0, 2.0, 3.0)
        ]
    )

    monkeypatch.setattr(
        port,
        "_steady",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("layout publication must not wait for graph motion to stop")
        ),
    )
    monkeypatch.setattr(
        port,
        "_method",
        lambda _name: lambda _pid, *, timeout_seconds: snapshots.popleft(),
    )
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    observed = port._wait_publication_delta(101, 10, 1.0)

    assert observed["layout_revision"] == 11
    assert observed["semantic_graph"] == {
        "camera": {"position": {"x": 1.0}}
    }
    assert len(snapshots) == 2


def test_layout_publication_without_revision_baseline_defers_pair_check_to_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    snapshot = {
        "layout_revision": None,
        "preferred_pair": {"left_px": 336, "right_px": 368},
        "effective_pair": {"left_px": 336, "right_px": 368},
    }
    monkeypatch.setattr(
        port,
        "_method",
        lambda _name: lambda _pid, *, timeout_seconds: snapshot,
    )
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)

    observed = port._wait_publication_delta(101, None, 1.0)

    assert observed == snapshot


def test_layout_revision_confirmation_does_not_wait_for_graph_motion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    snapshots: deque[dict[str, object]] = deque(
        [
            {
                "layout_revision": 11,
                "preferred_pair": {"left_px": 200, "right_px": 336},
                "effective_pair": {"left_px": 200, "right_px": 336},
                "semantic_graph": {"camera": {"position": {"x": x}}},
            }
            for x in (1.0, 2.0, 3.0)
        ]
    )
    monkeypatch.setattr(
        port,
        "_method",
        lambda _name: lambda _pid, *, timeout_seconds: snapshots.popleft(),
    )
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    observed = port._wait_layout_revision(
        101,
        expected_revision=11,
        timeout_seconds=1.0,
        operation="keyboard clamp",
    )

    assert observed["layout_revision"] == 11
    assert len(snapshots) == 2


def test_renderer_ready_steady_wait_does_not_accept_stable_old_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    fallback = {"layout_revision": 7, "renderer": {"availability": "manual_fallback"}}
    ready = {"layout_revision": 8, "renderer": {"availability": "ready"}}
    observations: deque[dict[str, object]] = deque(
        [
            *[
                {"identity": {"pid": 101, "window_id": 7}, "stability_token": fallback}
                for _ in range(3)
            ],
            *[
                {"identity": {"pid": 101, "window_id": 7}, "stability_token": ready}
                for _ in range(3)
            ],
        ]
    )
    full = {
        "identity": {"pid": 101, "window_id": 7},
        "stability_token": ready,
        "revision": 8,
    }

    def observe_steady(
        _pid: int, _anchors: tuple[str, ...], *, timeout_seconds: float
    ) -> dict[str, object]:
        return observations.popleft()

    def observe_workspace(
        _pid: int, _anchors: tuple[str, ...], *, timeout_seconds: float
    ) -> dict[str, object]:
        return full

    def method(name: str) -> Callable[..., dict[str, object]]:
        if name == "observe_workspace_steady":
            return observe_steady
        return observe_workspace

    monkeypatch.setattr(port, "_method", method)
    monkeypatch.setattr(port, "_normalise_observation", lambda raw, _operation: raw)
    monkeypatch.setattr(port, "_validated_snapshot_identity", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    observed = port._steady_renderer_ready(101, "renderer retry", 1.0)

    assert observed == full
    assert observations == deque()


def test_renderer_retry_case_uses_ready_specific_steady_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    case = _strict_case_payload("renderer-retry-snapshot-preserved", "a" * 64)
    fallback = case["before"]
    ready = case["after"]
    port.renderer_recovery_fingerprint = json.loads(
        json.dumps(ready["graph_fingerprint"])
    )
    ready_waits: list[tuple[int, str, float]] = []
    presses: list[tuple[int, str]] = []

    monkeypatch.setattr(port, "_steady", lambda *_args, **_kwargs: fallback)

    def ready_steady(pid: int, operation: str, timeout: float) -> dict[str, object]:
        ready_waits.append((pid, operation, timeout))
        return ready

    monkeypatch.setattr(port, "_steady_renderer_ready", ready_steady)
    monkeypatch.setattr(port, "_press", lambda pid, target: presses.append((pid, target)))
    monkeypatch.setattr(
        port,
        "_payload",
        lambda _pid, action, before, after, publication_delta: {
            "action": action,
            "before": before,
            "after": after,
            "publication_delta": publication_delta,
        },
    )

    observed = port.exercise_case(
        "renderer-retry-snapshot-preserved", 101, 15.0
    )

    assert ready_waits == [(101, "renderer-retry-snapshot-preserved", 15.0)]
    assert presses == [(101, "renderer-retry")]
    assert observed["after"] == ready
    assert observed["action"]["pre_fallback_ready_graph_fingerprint"] == (
        ready["graph_fingerprint"]
    )


def test_capture_rejects_stale_owner_pid_before_redaction_or_capture_calls(
    tmp_path: Path,
) -> None:
    class CaptureBase:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def set_workspace_text(
            self, pid: int, dom_identifier: str, value: str
        ) -> bool:
            self.calls.append(("set-workspace-text", pid, dom_identifier, value))
            return True

        def capture_workspace_window(
            self,
            pid: int,
            window_id: int,
            destination: Path,
            *,
            include_cursor: bool,
        ) -> runtime.ScreenshotCaptureEvidence:
            self.calls.append(
                ("capture-workspace-window", pid, window_id, include_cursor)
            )
            return runtime.ScreenshotCaptureEvidence(
                capture_mode=runtime.SCREEN_CAPTURE_MODE,
                pixel_width=2400,
                pixel_height=1600,
                point_pixel_scale=2.0,
            )

    base = CaptureBase()
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.base = base  # type: ignore[assignment]
    port.current_pid = 202

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.capture_window(
            101,
            42,
            tmp_path / "stale-owner.png",
            include_cursor=False,
        )

    assert captured.value.code == "workspace_primary_pid_mismatch"
    assert base.calls == []


@pytest.mark.parametrize(
    ("zoom_state", "selected_kind", "expected_component_count"),
    (("fit", None, 8), ("direct-select", "agent", 8)),
)
def test_real_graph_readability_capture_uses_one_sck_png_and_reuses_it_for_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zoom_state: str,
    selected_kind: str | None,
    expected_component_count: int,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    semantic_graph = _semantic_graph_observation(
        selected_component_id=None,
    )
    nodes = semantic_graph["components"]
    assert isinstance(nodes, list)
    selected_component_id = None
    for node in nodes:
        assert isinstance(node, dict)
        node["selected"] = (
            selected_component_id is None and node["kind"] == selected_kind
        )
        if node["selected"]:
            selected_component_id = str(node["component_id"])
    snapshot = {
        "identity": {"pid": 101, "window_id": 42},
        "appearance_mode": "light",
        "graph_lod": "overview" if zoom_state == "fit" else "select",
        "semantic_graph": semantic_graph,
        "graph_layout_geometry": _graph_layout_geometry(),
        "graph_fingerprint": {
            "selected_component_id": selected_component_id,
            "right_detail_id": selected_component_id,
            "active_profile_id": "runtime-layout-profile",
            "scale": 1.0 if zoom_state == "fit" else 2.0,
            "sot_load_count": 1,
            "local_scan_start_count": 0,
            "graph_body_identity": "component-map-body",
        },
        "physical_geometry": {
            "cg_window": {"x": 0, "y": 0, "width": 600, "height": 400}
        },
    }
    monkeypatch.setattr(port, "_clear_checkout_path_for_capture", lambda *_args: None)
    monkeypatch.setattr(port, "_steady", lambda *_args: json.loads(json.dumps(snapshot)))
    monkeypatch.setattr(
        port,
        "_payload",
        lambda _pid, action, before, after, publication_delta: {
            "action": action,
            "before": before,
            "after": after,
            "publication_delta": publication_delta,
        },
    )
    capture_calls: list[tuple[int, int, bool]] = []

    def capture(
        pid: int,
        window_id: int,
        destination: Path,
        *,
        include_cursor: bool,
    ) -> runtime.ScreenshotCaptureEvidence:
        capture_calls.append((pid, window_id, include_cursor))
        destination.write_bytes(b"single-sck-png")
        return runtime.ScreenshotCaptureEvidence(
            capture_mode=runtime.SCREEN_CAPTURE_MODE,
            pixel_width=1200,
            pixel_height=800,
            point_pixel_scale=2.0,
            content_rect=(0.0, 0.0, 600.0, 400.0),
        )

    monkeypatch.setattr(port, "_method", lambda name: capture if name == "capture_workspace_window" else None)
    monkeypatch.setattr(runtime, "rgba_png_pixels", lambda _body, _code: (1200, 800, b"\0" * (1200 * 800 * 4)))
    analysis_calls: list[dict[str, object]] = []

    def analyze(**kwargs: object) -> dict[str, object]:
        analysis_calls.append(kwargs)
        expected_counts = kwargs["expected_kind_counts"]
        assert isinstance(expected_counts, dict)
        return _graph_viewport_pixel_proof(
            appearance=str(kwargs["appearance"]),
            zoom_state=(
                "direct-select"
                if kwargs["zoom_state"] == "direct"
                else str(kwargs["zoom_state"])
            ),
            expected_kind_counts=expected_counts,
            selected_component_id=(
                str(kwargs["selected_component_id"])
                if kwargs["selected_component_id"] is not None
                else None
            ),
        )

    monkeypatch.setattr(runtime, "analyze_graph_viewport_pixels", analyze)

    contextual_camera_evidence = None
    if zoom_state == "direct-select":
        camera = semantic_graph["camera"]
        assert isinstance(camera, dict)
        contextual_camera_evidence = {
            "origin": "graph",
            "intent": "contextual",
            "observation_mode": "endpoint-only-ax",
            "baseline": {
                "position": json.loads(json.dumps(camera["position"])),
                "target": json.loads(json.dumps(camera["target"])),
            },
            "settled": {
                "position": {"x": 12.0, "y": 4.0, "z": 260.0},
                "target": {"x": 2.0, "y": 1.0, "z": 0.0},
            },
            "camera_changed": True,
            "anchor_hash_before": "c" * 64,
            "anchor_hash_after": "c" * 64,
        }

    payload = port._capture_graph_readability(
        case_id=f"graph-readability-{zoom_state}",
        pid=101,
        case_before=snapshot,
        appearance="light",
        zoom_state=zoom_state,
        zoom_press_count=1,
        maximum_reached=False,
        selected_component_id=selected_component_id,
        contextual_camera_evidence=contextual_camera_evidence,
        timeout_seconds=1,
    )

    assert capture_calls == [(101, 42, False)]
    assert len(analysis_calls) == 1
    analysis_call = analysis_calls[0]
    assert analysis_call["capture_content_frame"] == {
        "x": 0.0,
        "y": 0.0,
        "width": 600.0,
        "height": 400.0,
    }
    assert analysis_call["viewport_frame"] == {
        "x": 340,
        "y": 104,
        "width": 456,
        "height": 248,
    }
    assert sum(analysis_call["expected_kind_counts"].values()) == (
        expected_component_count
    )
    assert "node_frame" not in analysis_call
    assert "window_frame" not in analysis_call
    after = payload["after"]
    assert isinstance(after, dict)
    assert after["graph_readability"]["screenshot_count"] == 1
    assert after["graph_readability"]["snapshot_identity"]["relation_ids"] == [
        f"profile:{CANONICAL_PROFILE_ID}",
        "workflow:harnesskit.workflow.runtime-layout-workflow",
    ]
    destination = tmp_path / "cases" / f"graph-readability-{zoom_state}.png"
    evidence = port.capture_window(101, 42, destination, include_cursor=False)
    assert evidence.capture_mode == runtime.SCREEN_CAPTURE_MODE
    assert capture_calls == [(101, 42, False)]


def test_real_graph_maximum_requires_exact_8x_and_disabled_zoom_in_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)

    def snapshot(scale: float) -> dict[str, object]:
        return {
            "graph_fingerprint": {"scale": scale},
            "semantic_graph": {
                "camera": {
                    "ready": True,
                    "scale": scale,
                    "level": "최대" if scale == 8.0 else "상세",
                    "zoom_in_enabled": scale < 8.0,
                }
            },
        }

    snapshots = deque(
        [
            snapshot(4.0),
            snapshot(6.0),
            snapshot(8.0),
        ]
    )
    presses: list[str] = []
    monkeypatch.setattr(port, "_press", lambda _pid, token: presses.append(token))
    monkeypatch.setattr(port, "_steady", lambda *_args: snapshots.popleft())

    observed, press_count = port._zoom_graph_to_maximum(
        101,
        snapshot(3.0),
        1,
        "maximum probe",
    )

    assert observed["graph_fingerprint"]["scale"] == 8.0
    assert observed["semantic_graph"]["camera"]["zoom_in_enabled"] is False
    assert press_count == 3
    assert presses == ["graph-zoom-in"] * 3


@pytest.mark.parametrize(
    "case_id", ("left-pointer-drag", "restart-first-visible-persisted")
)
def test_prepared_capture_paths_still_reject_a_stale_owner_pid(
    tmp_path: Path,
    case_id: str,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 202
    destination = tmp_path / f"{case_id}.png"
    evidence = runtime.ScreenshotCaptureEvidence(
        capture_mode=(
            f"{runtime.SCREEN_CAPTURE_MODE}_cursor_included_drag_active"
            if case_id == "left-pointer-drag"
            else runtime.SCREEN_CAPTURE_MODE
        ),
        pixel_width=2400,
        pixel_height=1600,
        point_pixel_scale=2.0,
    )
    if case_id == "left-pointer-drag":
        destination.write_bytes(b"\x89PNG\r\n\x1a\nprepared")
        port.active_drag_captures[case_id] = (destination, evidence)
    else:
        prearmed = tmp_path / ".restart-first-visible-prearmed.png"
        prearmed.write_bytes(b"\x89PNG\r\n\x1a\nprepared")
        port.prearmed_capture = prearmed
        port.prearmed_capture_evidence = evidence

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.capture_window(
            101,
            42,
            destination,
            include_cursor=case_id == "left-pointer-drag",
        )

    assert captured.value.code == "workspace_primary_pid_mismatch"


def test_sanitized_fixture_has_exactly_three_stable_extent_anchors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime-fixture"

    fixture = runtime.create_sanitized_fixture_checkout(root)

    assert fixture.anchor_component_ids == FIXTURE_ANCHOR_COMPONENT_IDS
    assert fixture.manifest_relative_path == "fixture-manifest.json"
    manifest_path = root / fixture.manifest_relative_path
    manifest = json.loads(manifest_path.read_text())
    assert manifest["anchor_component_ids"] == list(FIXTURE_ANCHOR_COMPONENT_IDS)
    assert [component["component_id"] for component in manifest["components"]] == list(
        FIXTURE_ANCHOR_COMPONENT_IDS
    )
    assert [component["extent"] for component in manifest["components"]] == [
        "left",
        "middle",
        "right",
    ]

    registry = yaml.safe_load((root / "components/registry.yml").read_text())
    registry_components = registry["components"]
    assert isinstance(registry_components, dict)
    assert len(registry_components) >= 87
    assert {
        component["kind"]
        for component in registry_components.values()
        if isinstance(component, dict)
    } == CANONICAL_FIXTURE_KINDS

    profiles = [
        yaml.safe_load(path.read_text())
        for path in sorted((root / "profiles").glob("*.yml"))
    ]
    assert len(profiles) >= 5
    middle_anchor_owners = [
        profile["profile_id"]
        for profile in profiles
        if isinstance(profile, dict)
        and CANONICAL_HOVER_COMPONENT_ID in profile.get("components", [])
    ]
    assert len(middle_anchor_owners) == 2
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == fixture.manifest_sha256
    assert str(tmp_path) not in manifest_path.read_text()


def test_sanitized_fixture_matches_canonical_source_contracts(
    tmp_path: Path,
) -> None:
    """Keep the packaged-runtime fixture valid under the real canonical rules.

    The runtime fixture is read by production registry/profile readers, so it
    must not rely on a looser hand-written format than a normal checkout. This
    test deliberately validates the generated files independently of those
    readers to catch fixture-only schema or cross-domain policy drift.
    """

    root = tmp_path / "runtime-fixture"
    runtime.create_sanitized_fixture_checkout(root)

    registry = yaml.safe_load((root / "components/registry.yml").read_text())
    registry_components = registry["components"]
    assert isinstance(registry_components, dict)

    schemas = {
        "agent": "agent.schema.json",
        "workflow": "workflow.schema.json",
        "composite": "composite.schema.json",
    }
    violations: list[str] = []

    def validate_schema(
        *,
        label: str,
        value: object,
        schema_name: str,
    ) -> None:
        schema = json.loads((root / "schemas" / schema_name).read_text())
        errors = jsonschema.Draft7Validator(schema).iter_errors(value)
        for error in sorted(errors, key=lambda candidate: list(candidate.path)):
            path = ".".join(str(part) for part in error.absolute_path) or "<root>"
            violations.append(f"{label} {path}: {error.message}")

    for component_id, entry in registry_components.items():
        assert isinstance(component_id, str)
        assert isinstance(entry, dict)
        kind = entry["kind"]
        relative_path = entry["path"]
        assert isinstance(kind, str)
        assert isinstance(relative_path, str)
        manifest = yaml.safe_load((root / relative_path).read_text())
        validate_schema(
            label=f"component {component_id}",
            value=manifest,
            schema_name=schemas.get(kind, "component.schema.json"),
        )

    def load_fixture_manifest(relative_path: str) -> dict[str, object]:
        manifest = yaml.safe_load((root / relative_path).read_text())
        assert isinstance(manifest, dict)
        return manifest

    for profile_path in sorted((root / "profiles").glob("*.yml")):
        profile = yaml.safe_load(profile_path.read_text())
        assert isinstance(profile, dict)
        validate_schema(
            label=f"profile {profile_path.name}",
            value=profile,
            schema_name="profile.schema.json",
        )
        try:
            validate_profile_selection(
                profile,
                registry_components,
                load_manifest=load_fixture_manifest,
            )
        except ProfileSelectionError as error:
            violations.append(
                f"profile {profile_path.name}: {error.code} at {error.field_path}"
            )

    workflow_path = (
        root / "components/workflows/runtime-layout-workflow/workflow.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text())
    assert isinstance(workflow, dict)
    workflow_steps = workflow.get("steps")
    if not isinstance(workflow_steps, list):
        violations.append("workflow steps: expected an ordered list")
    else:
        observed_steps = [
            (ordinal, step.get("skill") if isinstance(step, dict) else None)
            for ordinal, step in enumerate(workflow_steps, start=1)
        ]
        expected_steps = [
            (1, CANONICAL_HOVER_COMPONENT_ID),
            (2, CANONICAL_HOVER_COMPONENT_ID),
        ]
        if observed_steps != expected_steps:
            violations.append(
                "workflow steps: expected ordinal 1/2 to reference the middle anchor "
                f"but received {observed_steps!r}"
            )

    assert not violations, "\n".join(violations)


def test_success_runs_feasible_cases_prearms_restart_and_hashes_sanitized_evidence(
    tmp_path: Path,
) -> None:
    feasible_case_ids = CASE_IDS
    app = _fixture_app(tmp_path)
    qualification = _fixture_qualification(tmp_path, app)
    evidence_root = tmp_path / "evidence"
    port = FakePort(app)

    report_path = runtime.verify_workspace_layout_macos(
        app,
        evidence_root,
        port=port,
        qualification_report=qualification,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["schema_version"] == 1
    assert report["status"] == "passed"
    assert report["artifact"]["build_id"] == BUILD_ID
    assert report["artifact"]["bundle_identifier"] == runtime.BUNDLE_IDENTIFIER
    assert report["artifact"]["executable_sha256"] == port.executable_sha256
    assert report["artifact"]["installed_path_verified"] is False
    assert report["package_qualification"] == {
        "app_source_manifest_sha256": "b" * 64,
        "build_inputs_sha256": "d" * 64,
        "bundle_manifest_sha256": json.loads(qualification.read_text())[
            "bundle_manifest_sha256"
        ],
        "report_sha256": hashlib.sha256(qualification.read_bytes()).hexdigest(),
        "source_identity_sha256": "c" * 64,
    }
    assert report["fixed_layout_contract"] == FIXED_LAYOUT_CONTRACT
    assert report["fixture"]["anchor_component_ids"] == list(
        FIXTURE_ANCHOR_COMPONENT_IDS
    )
    assert report["fixture"]["manifest_sha256"]
    assert [case["case_id"] for case in report["cases"]] == list(
        feasible_case_ids
    )
    assert [case["status"] for case in report["cases"]] == ["Passed"] * len(
        feasible_case_ids
    )

    cases = {case["case_id"]: case for case in report["cases"]}
    for case_id, case in cases.items():
        assert {
            "case_id",
            "started_at",
            "ended_at",
            "expected",
            "action",
            "before",
            "after",
            "status",
            "observation",
            "screenshot",
            "preference",
            "command_counters",
            "redactions",
            "limitations",
        } <= set(case)
        observation_path = evidence_root / "runtime-workspace-layout" / case[
            "observation"
        ]["path"]
        assert hashlib.sha256(observation_path.read_bytes()).hexdigest() == case[
            "observation"
        ]["sha256"]
        if case_id == "final-cleanup-zero":
            assert case["screenshot"] is None
        else:
            screenshot_path = evidence_root / "runtime-workspace-layout" / case[
                "screenshot"
            ]["path"]
            assert hashlib.sha256(screenshot_path.read_bytes()).hexdigest() == case[
                "screenshot"
            ]["sha256"]
        assert case["redactions"] == [
            "absolute_paths",
            "raw_ax_tree",
            "raw_logs",
            "secrets",
            "source_bodies",
        ]
        if case_id == "graph-hover-layout-stable":
            assert "during" in case

    assert cases["left-pointer-drag"]["after"]["preferred_pair"] == {
        "left_px": 328,
        "right_px": 368,
    }
    assert cases["right-pointer-drag"]["after"]["preferred_pair"] == {
        "left_px": 304,
        "right_px": 336,
    }
    assert cases["left-pointer-drag"]["publication_delta"] == 1
    assert cases["right-pointer-drag"]["publication_delta"] == 1
    assert cases["separator-keyboard-step"]["publication_delta"] == 1
    assert cases["separator-keyboard-step"]["action"]["key"] == "ArrowRight"
    assert (
        cases["separator-keyboard-step"]["after"]["preferred_pair"]["left_px"]
        - cases["separator-keyboard-step"]["before"]["preferred_pair"]["left_px"]
        == 16
    )

    graph_before = cases["graph-state-before-collapse"]["after"]
    graph_collapsed = cases["graph-collapsed"]["after"]
    graph_restored = cases["graph-expanded-state-restored"]["after"]
    graph_hover = cases["graph-hover-layout-stable"]
    assert graph_collapsed["graph_body_ax_present"] is False
    assert graph_collapsed["zoom_control_focusable"] is False
    assert graph_collapsed["matrix_usable_height"] > graph_before["matrix_usable_height"]
    assert graph_restored["graph_fingerprint"] == graph_before["graph_fingerprint"]
    assert graph_hover["action"]["transport"] == "cg-event-mouse-move"
    assert graph_hover["action"]["hover_target"]["component_id"] == (
        CANONICAL_HOVER_COMPONENT_ID
    )
    assert graph_hover["during"]["graph_tooltip_identity"]["component_id"] == (
        CANONICAL_HOVER_COMPONENT_ID
    )
    assert (
        graph_hover["before"]["graph_layout_geometry"]
        == graph_hover["during"]["graph_layout_geometry"]
        == graph_hover["after"]["graph_layout_geometry"]
    )
    assert (
        graph_hover["before"]["graph_fingerprint"]
        == graph_hover["during"]["graph_fingerprint"]
        == graph_hover["after"]["graph_fingerprint"]
    )
    assert graph_hover["screenshot"]["capture_mode"] == (
        f"{runtime.SCREEN_CAPTURE_MODE}_cursor_included_graph_hover_active"
    )
    assert report["command_counters"] == {
        "sot_load_count": 1,
        "local_scan_start_count": 0,
    }

    first_visible = cases["restart-first-visible-persisted"]["first_visible"]
    assert first_visible == {
        "prearmed_before_launch": True,
        "collector_attached_before_visible": True,
        "sequence_gap": False,
        "first_layer0_sample_sequence": 18,
        "first_complete_frame_sequence": 18,
        "on_screen": True,
        "alpha": 1.0,
        "pair_delta_px": {"left": 0, "right": 0},
    }
    prearm_index = port.events.index(("prearm-first-visible", 17))
    relaunch_index = port.events.index(
        ("launch", 202, BUILD_ID, port.executable_sha256)
    )
    observe_index = port.events.index(("observe-first-visible", 202, 17))
    cancel_index = port.events.index(("cancel-first-visible", 17))
    assert prearm_index < relaunch_index < observe_index < cancel_index
    assert port.events.index(("wait-layout-publication", 101)) < port.events.index(
        ("terminate", 101)
    )

    assert port.exercised == [
        case_id
        for case_id in feasible_case_ids
        if case_id
        not in {"restart-first-visible-persisted", "final-cleanup-zero"}
    ]
    assert port.captures == list(feasible_case_ids[:-1])
    assert port.terminated == [101, 202]
    picker_termination = report["lifecycle"]["picker_open_termination"]
    assert picker_termination["empty_path_triggered"] is True
    assert picker_termination["native_panel_open_confirmed"] is True
    assert picker_termination["panel"]["kind"] == "native-directory-panel-open"
    assert picker_termination["normal_terminate_accepted"] is True
    assert picker_termination["main_process_absent"] is True
    assert picker_termination["main_window_absent"] is True
    assert picker_termination["observed_helpers_exited"] is True
    assert isinstance(picker_termination["termination_elapsed_ms"], int)
    assert port.events.index(("prepare-picker-termination", 202)) < port.events.index(
        ("terminate", 202)
    )
    assert report["lifecycle"]["final_cleanup"] == {
        "main_process_count": 0,
        "main_window_count": 0,
        "helper_process_count": 0,
    }

    evidence_dir = evidence_root / "runtime-workspace-layout"
    json_evidence = [report_path, *evidence_dir.glob("**/*.json")]
    serialized = "\n".join(path.read_text() for path in json_evidence)
    assert str(tmp_path) not in serialized
    assert str(app) not in serialized
    assert str(evidence_root) not in serialized


def test_full_case_report_records_visible_fallback_journey_without_context_loss_claim(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)
    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=FakePort(app),
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "passed"
    assert [case["case_id"] for case in report["cases"]] == list(CASE_IDS)
    cases = {case["case_id"]: case for case in report["cases"]}
    expected_fallback = _case_payload(
        "semantic-fallback-user-transition",
        pid=101,
        executable_sha256="f" * 64,
    )
    expected_retry = _case_payload(
        "renderer-retry-snapshot-preserved",
        pid=101,
        executable_sha256="f" * 64,
    )
    assert cases["semantic-fallback-user-transition"]["action"] == (
        expected_fallback["action"]
    )
    assert cases["renderer-retry-snapshot-preserved"]["action"] == (
        expected_retry["action"]
    )
    serialized = report_path.read_text()
    assert "WEBGL_lose_context" not in serialized
    assert "context_loss" not in serialized


@pytest.mark.parametrize(
    ("case_id", "expected_presses", "expected_kind"),
    (
        (
            "semantic-fallback-user-transition",
            (
                "semantic-relation:workflow:"
                "harnesskit.workflow.runtime-layout-workflow",
                "label:Component Map 텍스트 보기",
            ),
            "semantic-fallback-user-transition",
        ),
        (
            "renderer-retry-snapshot-preserved",
            ("renderer-retry",),
            "renderer-retry-snapshot-preserved",
        ),
    ),
)
def test_renderer_resilience_cases_use_visible_production_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    expected_presses: tuple[str, ...],
    expected_kind: str,
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    monkeypatch.setattr(port, "_resolve_pid", lambda pid: pid)
    ready = _case_payload(
        "initial-default",
        pid=101,
        executable_sha256="f" * 64,
    )["after"]
    assert isinstance(ready, dict)
    ready = json.loads(json.dumps(ready))
    ready["semantic_graph"] = _semantic_graph_observation()
    ready["matrix_layout"] = _matrix_layout_observation(expanded=True)
    workflow_id = "harnesskit.workflow.runtime-layout-workflow"
    relation = json.loads(json.dumps(ready))
    relation["semantic_graph"] = _semantic_graph_observation(
        selected_workflow_id=workflow_id
    )
    relation["graph_fingerprint"].update(
        {
            "selected_relation_node_id": f"workflow:{workflow_id}",
            "camera_pose": {
                "position": {"x": 36.0, "y": -10.0, "z": 300.0},
                "target": {"x": 2.0, "y": 3.0, "z": 4.0},
            },
        }
    )
    locked = json.loads(json.dumps(relation))
    locked["semantic_graph"] = _semantic_graph_observation(
        selected_workflow_id=workflow_id,
        locked_ordinal=1,
        camera_scale=2.0,
        camera_position=(52.0, -18.0, 240.0),
        camera_target=(4.0, 5.0, 6.0),
    )
    locked["graph_fingerprint"].update(
        {
            "locked_step": {"workflow_id": workflow_id, "ordinal": 1},
            "scale": 2.0,
            "camera_pose": {
                "position": {"x": 52.0, "y": -18.0, "z": 240.0},
                "target": {"x": 4.0, "y": 5.0, "z": 6.0},
            },
        }
    )
    fallback = json.loads(json.dumps(locked if case_id == "semantic-fallback-user-transition" else ready))
    fallback["semantic_graph"] = _semantic_graph_observation(
        selected_workflow_id=(
            workflow_id if case_id == "semantic-fallback-user-transition" else None
        ),
        locked_ordinal=(1 if case_id == "semantic-fallback-user-transition" else None),
        camera_scale=(2.0 if case_id == "semantic-fallback-user-transition" else 1.0),
        camera_position=(
            (52.0, -18.0, 240.0)
            if case_id == "semantic-fallback-user-transition"
            else (0.0, 0.0, 500.0)
        ),
        camera_target=(
            (4.0, 5.0, 6.0)
            if case_id == "semantic-fallback-user-transition"
            else (0.0, 0.0, 0.0)
        ),
        renderer_availability="manual_fallback",
    )
    snapshots = deque(
        (
                (ready, relation, locked, fallback)
            if case_id == "semantic-fallback-user-transition"
            else (fallback, ready)
        )
    )
    presses: list[str] = []
    clicks: list[str] = []
    monkeypatch.setattr(port, "_steady", lambda *_args, **_kwargs: snapshots.popleft())
    monkeypatch.setattr(
        port,
        "_steady_renderer_ready",
        lambda *_args, **_kwargs: snapshots.popleft(),
    )
    if case_id == "renderer-retry-snapshot-preserved":
        port.renderer_recovery_fingerprint = json.loads(
            json.dumps(ready["graph_fingerprint"])
        )
    monkeypatch.setattr(port, "_press", lambda _pid, target: presses.append(target))
    monkeypatch.setattr(
        port,
        "_click",
        lambda _pid, target: (
            clicks.append(target)
            or {"accepted": True, "point": {"x": 420, "y": 220}}
        ),
    )
    monkeypatch.setattr(
        port,
        "_payload",
        lambda _pid, action, before, after, publication_delta: {
            "action": action,
            "before": before,
            "after": after,
            "publication_delta": publication_delta,
            "command_counters": {
                "sot_load_count": 1,
                "local_scan_start_count": 0,
            },
        },
    )
    monkeypatch.setattr(
        port,
        "_command_counters",
        lambda: {"sot_load_count": 1, "local_scan_start_count": 0},
    )

    payload = port.exercise_case(case_id, 101, 1)

    assert tuple(presses) == expected_presses
    if case_id == "semantic-fallback-user-transition":
        assert clicks == [f"workflow-inspector-step:{workflow_id}:1:step-1"]
    assert payload["action"]["kind"] == expected_kind
    assert not snapshots


def test_semantic_fallback_case_records_ready_fingerprint_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    initial = _case_payload(
        "initial-default", pid=101, executable_sha256="a" * 64
    )["after"]
    ready = _strict_case_payload(
        "semantic-fallback-user-transition", "a" * 64
    )["before"]
    fallback = _strict_case_payload(
        "semantic-fallback-user-transition", "a" * 64
    )["after"]
    assert isinstance(initial, dict)
    workflow_id = "harnesskit.workflow.runtime-layout-workflow"
    relation = json.loads(json.dumps(initial))
    relation["semantic_graph"] = _semantic_graph_observation(
        selected_workflow_id=workflow_id
    )
    relation["graph_fingerprint"].update(
        {
            "selected_relation_node_id": f"workflow:{workflow_id}",
            "camera_pose": {
                "position": {"x": 36.0, "y": -10.0, "z": 300.0},
                "target": {"x": 2.0, "y": 3.0, "z": 4.0},
            },
        }
    )
    snapshots = deque(
        [
            json.loads(json.dumps(initial)),
            relation,
            json.loads(json.dumps(ready)),
            json.loads(json.dumps(fallback)),
        ]
    )
    monkeypatch.setattr(port, "_steady", lambda *_args, **_kwargs: snapshots.popleft())
    monkeypatch.setattr(port, "_press", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        port,
        "_click",
        lambda *_args, **_kwargs: {
            "accepted": True,
            "point": {"x": 420, "y": 220},
        },
    )
    monkeypatch.setattr(
        port,
        "_payload",
        lambda _pid, action, before, after, publication_delta: {
            "action": action,
            "before": before,
            "after": after,
            "publication_delta": publication_delta,
        },
    )

    observed = port.exercise_case(
        "semantic-fallback-user-transition", 101, 15.0
    )

    expected = ready["graph_fingerprint"]
    assert observed["action"]["pre_fallback_ready_graph_fingerprint"] == expected
    assert port.renderer_recovery_fingerprint == expected
    assert not snapshots


def test_semantic_fallback_setup_locks_workflow_step_and_non_default_camera(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSWorkspaceLayoutAutomationPort(tmp_path)
    port.current_pid = 101
    workflow_id = "harnesskit.workflow.runtime-layout-workflow"
    relation_target = f"semantic-relation:workflow:{workflow_id}"
    step_target = f"workflow-inspector-step:{workflow_id}:1:step-1"
    initial = _case_payload(
        "initial-default", pid=101, executable_sha256="a" * 64
    )["after"]
    assert isinstance(initial, dict)
    initial = json.loads(json.dumps(initial))
    initial_semantic = initial["semantic_graph"]
    assert isinstance(initial_semantic, dict)
    initial_relations = initial_semantic["relations"]
    assert isinstance(initial_relations, list)
    secondary_workflow = json.loads(
        json.dumps(
            next(
                relation
                for relation in initial_relations
                if relation["relation_kind"] == "workflow"
            )
        )
    )
    secondary_workflow.update(
        {
            "canonical_id": "harnesskit.workflow.zzz-runtime-layout-workflow",
            "node_id": "workflow:harnesskit.workflow.zzz-runtime-layout-workflow",
            "dom_identifier": (
                "semantic-relation:workflow:"
                "harnesskit.workflow.zzz-runtime-layout-workflow"
            ),
            "selected": False,
        }
    )
    initial_relations.append(secondary_workflow)
    initial_fingerprint = initial["graph_fingerprint"]
    assert isinstance(initial_fingerprint, dict)
    initial_fingerprint["camera_pose"] = {
        "position": {"x": 0.0, "y": 0.0, "z": 500.0},
        "target": {"x": 0.0, "y": 0.0, "z": 0.0},
    }

    relation = json.loads(json.dumps(initial))
    relation["semantic_graph"] = _semantic_graph_observation(
        selected_workflow_id=workflow_id,
    )
    relation_fingerprint = relation["graph_fingerprint"]
    assert isinstance(relation_fingerprint, dict)
    relation_fingerprint.update(
        {
            "selected_relation_node_id": f"workflow:{workflow_id}",
            "locked_step": None,
            "camera_pose": {
                "position": {"x": 36.0, "y": -10.0, "z": 300.0},
                "target": {"x": 2.0, "y": 3.0, "z": 4.0},
            },
        }
    )

    locked = json.loads(json.dumps(relation))
    locked["semantic_graph"] = _semantic_graph_observation(
        selected_workflow_id=workflow_id,
        locked_ordinal=1,
        camera_scale=2.0,
    )
    locked_fingerprint = locked["graph_fingerprint"]
    assert isinstance(locked_fingerprint, dict)
    locked_fingerprint.update(
        {
            "scale": 2.0,
            "locked_step": {"workflow_id": workflow_id, "ordinal": 1},
            "camera_pose": {
                "position": {"x": 52.0, "y": -18.0, "z": 240.0},
                "target": {"x": 4.0, "y": 5.0, "z": 6.0},
            },
        }
    )
    fallback = json.loads(json.dumps(locked))
    fallback["semantic_graph"] = _semantic_graph_observation(
        selected_workflow_id=workflow_id,
        locked_ordinal=1,
        camera_scale=2.0,
        renderer_availability="manual_fallback",
    )
    snapshots = deque([initial, relation, locked, fallback])
    presses: list[str] = []
    clicks: list[str] = []
    click_evidence = {"accepted": True, "point": {"x": 420, "y": 220}}
    monkeypatch.setattr(port, "_steady", lambda *_args, **_kwargs: snapshots.popleft())
    monkeypatch.setattr(port, "_press", lambda _pid, target: presses.append(target))

    def click(_pid: int, target: str) -> dict[str, object]:
        clicks.append(target)
        return click_evidence

    monkeypatch.setattr(port, "_click", click)
    monkeypatch.setattr(
        port,
        "_payload",
        lambda _pid, action, before, after, publication_delta: {
            "action": action,
            "before": before,
            "after": after,
            "publication_delta": publication_delta,
        },
    )

    observed = port.exercise_case(
        "semantic-fallback-user-transition", 101, 15.0
    )

    assert presses == [relation_target, "label:Component Map 텍스트 보기"]
    assert clicks == [step_target]
    assert observed["before"] == locked
    assert observed["action"]["setup_actions"] == [
        {
            "kind": "semantic-workflow-relation-press",
            "transport": "ax-press",
            "target": relation_target,
        },
        {
            "kind": "workflow-step-select",
            "transport": "cg-event-mouse-click",
            "target": step_target,
            "click": click_evidence,
        },
    ]
    assert observed["action"]["pre_fallback_ready_graph_fingerprint"] == (
        locked_fingerprint
    )
    assert port.renderer_recovery_fingerprint == locked_fingerprint
    assert not snapshots


def test_first_visible_handle_is_cancelled_when_observation_fails(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)

    class FailingObservationPort(FakePort):
        def observe_first_visible(
            self,
            arm: dict[str, object],
            pid: int,
            expected_pair: dict[str, int],
            timeout_seconds: float,
        ) -> dict[str, object]:
            self.events.append(("observe-first-visible", pid, arm["sequence_start"]))
            raise runtime.RuntimeQualificationError(
                "workspace_first_visible_wait_timeout",
                "workspace-first-visible",
            )

    port = FailingObservationPort(app)

    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"] == {
        "code": "workspace_first_visible_wait_timeout",
        "operation": "workspace-first-visible",
    }
    assert port.events.count(("cancel-first-visible", 17)) == 1
    assert [case["case_id"] for case in report["cases"]] == list(CASE_IDS)
    restart_index = CASE_IDS.index("restart-first-visible-persisted")
    assert [case["status"] for case in report["cases"][:restart_index]] == [
        "Passed"
    ] * restart_index
    assert report["cases"][restart_index]["status"] == "Failed"
    assert [case["status"] for case in report["cases"][restart_index + 1 :]] == [
        "Partial"
    ] * (len(CASE_IDS) - restart_index - 1)


def test_fixture_load_without_native_picker_evidence_fails_before_layout_cases(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)

    class MissingPickerEvidencePort(FakePort):
        def load_fixture_after_picker_cancel(
            self,
            pid: int,
            checkout: Path,
            anchor_component_ids: tuple[str, ...],
            timeout_seconds: float,
        ) -> dict[str, object]:
            result = super().load_fixture_after_picker_cancel(
                pid, checkout, anchor_component_ids, timeout_seconds
            )
            result.pop("picker_evidence")
            return result

    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=MissingPickerEvidencePort(app),
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"] == {
        "code": "workspace_picker_evidence_invalid",
        "operation": "fixture load",
    }
    assert report["cases"][0]["status"] == "Failed"


@pytest.mark.parametrize(
    ("failed_zero_observation", "expected_pid", "expected_code"),
    [
        (1, 101, "workspace_restart_baseline_nonzero"),
        (2, 202, "workspace_final_cleanup_nonzero"),
    ],
)
def test_process_remains_tracked_for_failure_cleanup_until_zero_is_proven(
    tmp_path: Path,
    failed_zero_observation: int,
    expected_pid: int,
    expected_code: str,
) -> None:
    app = _fixture_app(tmp_path)

    class NonzeroAfterAcceptedTerminationPort(FakePort):
        def __init__(self, app: Path) -> None:
            super().__init__(app)
            self.zero_observation_count = 0

        def wait_for_zero(self, timeout_seconds: float) -> dict[str, int]:
            zero = super().wait_for_zero(timeout_seconds)
            self.zero_observation_count += 1
            if self.zero_observation_count == failed_zero_observation:
                zero["main_process_count"] = 1
            return zero

    port = NonzeroAfterAcceptedTerminationPort(app)

    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"]["code"] == expected_code
    assert port.terminated.count(expected_pid) == 2
    assert report["lifecycle"]["failure_cleanup"] == {
        "main_process_count": 0,
        "main_window_count": 0,
        "helper_process_count": 0,
    }


def test_hidden_preference_relaunch_pid_is_recovered_for_failure_cleanup(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)

    class FailingHiddenRelaunchPort(FakePort):
        def exercise_case(
            self, case_id: str, pid: int, timeout_seconds: float
        ) -> dict[str, object]:
            if case_id == "corrupt-preference-default-pair":
                assert pid == 202
                self.current_pid = 303
                self.events.append(("hidden-relaunch", 303))
                raise runtime.RuntimeQualificationError(
                    "workspace_corrupt_preference_probe_failed",
                    "corrupt preference",
                )
            return super().exercise_case(case_id, pid, timeout_seconds)

        def terminate(self, pid: int) -> bool:
            self.terminated.append(pid)
            self.events.append(("terminate", pid))
            return pid == self.current_pid

    port = FailingHiddenRelaunchPort(app)
    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"]["code"] == "workspace_corrupt_preference_probe_failed"
    assert 303 in port.terminated
    assert report["lifecycle"]["failure_cleanup"] == {
        "main_process_count": 0,
        "main_window_count": 0,
        "helper_process_count": 0,
    }


def test_internal_launch_failure_after_prearm_keeps_all_required_case_slots(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)

    class LaunchFailurePort(FakePort):
        def launch_primary(
            self, artifact: object, environment: dict[str, str]
        ) -> int:
            if any(event[0] == "prearm-first-visible" for event in self.events):
                raise OSError("redacted launch failure")
            return super().launch_primary(artifact, environment)

    port = LaunchFailurePort(app)

    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"] == {
        "code": "workspace_runtime_driver_internal_failure",
        "operation": "OSError",
    }
    assert port.events.count(("cancel-first-visible", 17)) == 1
    assert [case["case_id"] for case in report["cases"]] == list(CASE_IDS)
    restart_index = CASE_IDS.index("restart-first-visible-persisted")
    assert [case["status"] for case in report["cases"][:restart_index]] == [
        "Passed"
    ] * restart_index
    assert report["cases"][restart_index]["status"] == "Failed"
    assert [case["status"] for case in report["cases"][restart_index + 1 :]] == [
        "Partial"
    ] * (len(CASE_IDS) - restart_index - 1)


def test_internal_observer_timeout_keeps_all_required_case_slots(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)

    class ObserverTimeoutPort(FakePort):
        def observe_first_visible(
            self,
            arm: dict[str, object],
            pid: int,
            expected_pair: dict[str, int],
            timeout_seconds: float,
        ) -> dict[str, object]:
            self.events.append(("observe-first-visible", pid, arm["sequence_start"]))
            raise subprocess.TimeoutExpired("workspace-first-visible", timeout_seconds)

    port = ObserverTimeoutPort(app)

    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"] == {
        "code": "workspace_runtime_driver_internal_failure",
        "operation": "TimeoutExpired",
    }
    assert port.events.count(("cancel-first-visible", 17)) == 1
    assert [case["case_id"] for case in report["cases"]] == list(CASE_IDS)
    restart_index = CASE_IDS.index("restart-first-visible-persisted")
    assert [case["status"] for case in report["cases"][:restart_index]] == [
        "Passed"
    ] * restart_index
    assert report["cases"][restart_index]["status"] == "Failed"
    assert [case["status"] for case in report["cases"][restart_index + 1 :]] == [
        "Partial"
    ] * (len(CASE_IDS) - restart_index - 1)


def test_corrupt_preference_relaunch_updates_the_orchestrator_primary_pid(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)

    class RelaunchingCorruptionPort(FakePort):
        def exercise_case(
            self, case_id: str, pid: int, timeout_seconds: float
        ) -> dict[str, object]:
            if case_id == "corrupt-preference-default-pair":
                assert pid == 202
                payload = super().exercise_case(case_id, pid, timeout_seconds)
                identity = payload["identity"]
                assert isinstance(identity, dict)
                identity["pid"] = 303
                identity["window_id"] = 43
                return payload
            if case_id == "preference-write-denied-session-usable":
                assert pid == 303
            return super().exercise_case(case_id, pid, timeout_seconds)

        def capture_window(
            self,
            owner_pid: int,
            window_id: int,
            destination: Path,
            *,
            include_cursor: bool,
        ) -> runtime.ScreenshotCaptureEvidence:
            if owner_pid == 303:
                assert window_id in {42, 43}
                assert include_cursor is False
                self.captures.append(destination.stem)
                destination.write_bytes(
                    b"\x89PNG\r\n\x1a\n" + destination.stem.encode()
                )
                return runtime.ScreenshotCaptureEvidence(
                    capture_mode=runtime.SCREEN_CAPTURE_MODE,
                    pixel_width=2400,
                    pixel_height=1600,
                    point_pixel_scale=2.0,
                )
            return super().capture_window(
                owner_pid,
                window_id,
                destination,
                include_cursor=include_cursor,
            )

    port = RelaunchingCorruptionPort(app)
    evidence_root = tmp_path / "evidence"

    report_path = runtime.verify_workspace_layout_macos(
        app,
        evidence_root,
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "passed"
    assert port.terminated[-1] == 303
    assert (
        "exercise",
        "preference-write-denied-session-usable",
        303,
    ) in port.events
    final_case = next(
        case for case in report["cases"] if case["case_id"] == "final-cleanup-zero"
    )
    observation_path = (
        evidence_root
        / "runtime-workspace-layout"
        / final_case["observation"]["path"]
    )
    final_payload = json.loads(observation_path.read_text())
    assert final_payload["identity"]["terminated_primary_pid"] == 303


@pytest.mark.parametrize(
    ("preflight", "code", "operation"),
    [
        (
            runtime.PermissionPreflight(False, True, True),
            "accessibility_api_denied",
            "AXIsProcessTrusted",
        ),
        (
            runtime.PermissionPreflight(True, False, True),
            "screen_capture_api_denied",
            "CGPreflightScreenCaptureAccess",
        ),
        (
            runtime.PermissionPreflight(True, True, False),
            "system_events_automation_denied",
            "System Events process enumeration",
        ),
    ],
)
def test_permission_denial_blocks_run_and_marks_every_required_case_unavailable(
    tmp_path: Path,
    preflight: runtime.PermissionPreflight,
    code: str,
    operation: str,
) -> None:
    app = _fixture_app(tmp_path)
    port = FakePort(app, preflight=preflight)

    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=port,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "blocked"
    assert report["error"] == {"code": code, "operation": operation}
    assert [case["case_id"] for case in report["cases"]] == list(CASE_IDS)
    assert [case["status"] for case in report["cases"]] == [
        "Unavailable"
    ] * len(CASE_IDS)
    assert report["manual_fallback"] is False
    assert not any(event[0] == "launch" for event in port.events)


def test_blocked_session_preflight_marks_every_workspace_case_unavailable(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)

    class LockedSessionPort(FakePort):
        def preflight(self) -> runtime.PermissionPreflight:
            raise runtime.RuntimeQualificationError(
                "interactive_session_locked",
                "macOS GUI session preflight",
                blocked=True,
                evidence={"screen_locked": True},
            )

    port = LockedSessionPort(app)
    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=port,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "blocked"
    assert report["error"] == {
        "code": "interactive_session_locked",
        "operation": "macOS GUI session preflight",
        "evidence": {"screen_locked": True},
    }
    assert [case["status"] for case in report["cases"]] == [
        "Unavailable"
    ] * len(CASE_IDS)
    assert not any(event[0] == "launch" for event in port.events)


def test_missing_product_ax_semantic_is_failed_not_permission_unavailable(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)
    port = FakePort(
        app,
        fail_case="left-pointer-drag",
        fail_error=runtime.RuntimeQualificationError(
            "required_ax_semantic_missing",
            "left workspace separator",
            blocked=False,
        ),
    )

    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"] == {
        "code": "required_ax_semantic_missing",
        "operation": "left workspace separator",
    }
    assert [case["case_id"] for case in report["cases"]] == list(CASE_IDS)
    statuses = [case["status"] for case in report["cases"]]
    assert statuses[:2] == ["Passed", "Failed"]
    assert statuses[2:] == ["Partial"] * (len(CASE_IDS) - 2)
    assert report["cases"][1]["limitations"] == [
        "required product AX semantic was not exposed"
    ]
    assert report["manual_fallback"] is False


def test_failure_report_keeps_all_case_slots_and_hashes_completed_observations(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)
    port = FakePort(
        app,
        fail_case="graph-collapsed",
        fail_error=runtime.RuntimeQualificationError(
            "graph_body_remained_accessible",
            "graph-collapsed",
            blocked=False,
        ),
    )

    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert [case["case_id"] for case in report["cases"]] == list(CASE_IDS)
    failure_index = CASE_IDS.index("graph-collapsed")
    assert [case["status"] for case in report["cases"][:failure_index]] == [
        "Passed"
    ] * failure_index
    assert report["cases"][failure_index]["status"] == "Failed"
    assert [case["status"] for case in report["cases"][failure_index + 1 :]] == [
        "Partial"
    ] * (len(CASE_IDS) - failure_index - 1)

    evidence_dir = report_path.parent
    for case in report["cases"][:failure_index]:
        observation = evidence_dir / case["observation"]["path"]
        assert hashlib.sha256(observation.read_bytes()).hexdigest() == case[
            "observation"
        ]["sha256"]
    assert report["manual_fallback"] is False


def _patch_payload(
    payload: dict[str, object], patches: tuple[tuple[tuple[str, ...], object], ...]
) -> None:
    for path, value in patches:
        cursor: dict[str, object] = payload
        for key in path[:-1]:
            child = cursor[key]
            assert isinstance(child, dict)
            cursor = child
        cursor[path[-1]] = value


class PayloadMutatingFakePort(FakePort):
    def __init__(
        self,
        app: Path,
        *,
        mutation_case: str,
        patches: tuple[tuple[tuple[str, ...], object], ...],
    ) -> None:
        super().__init__(app)
        self.mutation_case = mutation_case
        self.patches = patches
        self.zero_observation_count = 0

    def exercise_case(
        self,
        case_id: str,
        pid: int,
        timeout_seconds: float,
    ) -> dict[str, object]:
        payload = super().exercise_case(case_id, pid, timeout_seconds)
        if case_id == self.mutation_case:
            _patch_payload(payload, self.patches)
        return payload

    def observe_first_visible(
        self,
        arm: dict[str, object],
        pid: int,
        expected_pair: dict[str, int],
        timeout_seconds: float,
    ) -> dict[str, object]:
        payload = super().observe_first_visible(
            arm, pid, expected_pair, timeout_seconds
        )
        if self.mutation_case == "restart-first-visible-persisted":
            _patch_payload(payload, self.patches)
        return payload

    def wait_for_zero(self, timeout_seconds: float) -> dict[str, int]:
        zero = super().wait_for_zero(timeout_seconds)
        self.zero_observation_count += 1
        if (
            self.mutation_case == "final-cleanup-zero"
            and self.zero_observation_count == 2
        ):
            for path, value in self.patches:
                assert len(path) == 1
                zero[path[0]] = int(value)
        return zero


def test_real_macos_port_routes_every_post_restart_ax_case() -> None:
    assert tuple(runtime.GRAPH_READABILITY_CASE_CONFIG) == GRAPH_READABILITY_CASE_IDS
    for case_id in GRAPH_READABILITY_CASE_IDS:
        assert runtime.GRAPH_READABILITY_CASE_CONFIG[case_id][0] in {"light", "dark"}
        assert runtime.GRAPH_READABILITY_CASE_CONFIG[case_id][1] in {
            "fit", "direct-select", "maximum"
        }


def test_real_graph_collapse_expands_matrix_before_hiding_the_map() -> None:
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    exercise = source.split("    def exercise_case(\n", 2)[-1]
    branch = exercise.split('elif case_id == "graph-collapsed":', 1)[1].split(
        'elif case_id == "graph-expanded-state-restored":', 1
    )[0]

    matrix_press = branch.index('self._press(pid, "matrix-toggle")')
    graph_press = branch.index('self._press(pid, "graph-toggle")')

    assert matrix_press < graph_press
    assert '"matrix_setup": "expanded"' in branch


_SEMANTIC_PAYLOAD_MUTATIONS = [
    pytest.param(
        "initial-default",
        ((("after", "preferred_pair", "left_px"), 305),),
        id="wrong-default-pair",
    ),
    pytest.param(
        "left-pointer-drag",
        (
            (("after", "preferred_pair", "left_px"), 304),
            (("after", "effective_pair", "left_px"), 304),
            (("after", "geometry"), _three_pane_geometry(304, 368)),
        ),
        id="left-active-side-unchanged",
    ),
    pytest.param(
        "left-pointer-drag",
        (
            (("after", "preferred_pair", "right_px"), 369),
            (("after", "effective_pair", "right_px"), 369),
            (("after", "geometry"), _three_pane_geometry(328, 369)),
        ),
        id="left-drag-mutates-opposite-side",
    ),
    pytest.param(
        "left-pointer-drag",
        ((("publication_delta",), 0),),
        id="left-publication-not-exactly-one",
    ),
    pytest.param(
        "right-pointer-drag",
        (
            (("after", "preferred_pair", "right_px"), 368),
            (("after", "effective_pair", "right_px"), 368),
            (("after", "geometry"), _three_pane_geometry(304, 368)),
        ),
        id="right-active-side-unchanged",
    ),
    pytest.param(
        "right-pointer-drag",
        (
            (("after", "preferred_pair", "left_px"), 305),
            (("after", "effective_pair", "left_px"), 305),
            (("after", "geometry"), _three_pane_geometry(305, 392)),
        ),
        id="right-drag-mutates-opposite-side",
    ),
    pytest.param(
        "right-pointer-drag",
        ((("publication_delta",), 2),),
        id="right-publication-not-exactly-one",
    ),
    pytest.param(
        "separator-keyboard-step",
        (
            (("after", "preferred_pair", "left_px"), 319),
            (("after", "effective_pair", "left_px"), 319),
            (("after", "geometry"), _three_pane_geometry(319, 368)),
        ),
        id="keyboard-step-not-sixteen",
    ),
    pytest.param(
        "separator-keyboard-step",
        ((("publication_delta",), 0),),
        id="keyboard-publication-not-exactly-one",
    ),
    pytest.param(
        "minimum-overshoot-clamped",
        (
            (("after", "preferred_pair", "left_px"), 257),
            (("after", "effective_pair", "left_px"), 257),
            (("after", "geometry"), _three_pane_geometry(257, 368)),
        ),
        id="minimum-not-clamped",
    ),
    pytest.param(
        "maximum-overshoot-clamped",
        (
            (("after", "preferred_pair", "right_px"), 559),
            (("after", "effective_pair", "right_px"), 559),
            (
                ("after", "geometry"),
                _three_pane_geometry(304, 559, window_width=1440),
            ),
        ),
        id="maximum-not-clamped",
    ),
    pytest.param(
        "minimum-overshoot-clamped",
        ((("after", "geometry", "right", "x"), 100),),
        id="pane-overlap",
    ),
    pytest.param(
        "maximum-overshoot-clamped",
        ((("after", "geometry", "center", "width"), 479),),
        id="center-below-minimum",
    ),
    pytest.param(
        "small-window-two-column",
        ((("after", "layout_mode"), "three-pane"),),
        id="wrong-two-column-mode",
    ),
    pytest.param(
        "small-window-two-column",
        (
            (("after", "separator_state", "left", "focusable"), True),
            (("after", "separator_state", "left", "disabled"), False),
        ),
        id="two-column-separator-active",
    ),
    pytest.param(
        "small-window-stacked",
        ((("after", "layout_mode"), "two-column"),),
        id="wrong-stacked-mode",
    ),
    pytest.param(
        "small-window-stacked",
        (
            (("after", "separator_state", "right", "focusable"), True),
            (("after", "separator_state", "right", "disabled"), False),
        ),
        id="stacked-separator-active",
    ),
    pytest.param(
        "graph-collapsed",
        ((("after", "graph_body_ax_present"), True),),
        id="collapsed-body-remains-in-ax-tree",
    ),
    pytest.param(
        "graph-collapsed",
        ((("after", "matrix_usable_height"), 0),),
        id="collapsed-matrix-does-not-expand",
    ),
    pytest.param(
        "graph-expanded-state-restored",
        ((("after", "graph_fingerprint", "scale"), 1.5),),
        id="expanded-fingerprint-changed",
    ),
    pytest.param(
        "graph-state-before-collapse",
        ((("command_counters", "sot_load_count"), 2),),
        id="graph-command-counter-changed",
    ),
    pytest.param(
        "restart-first-visible-persisted",
        ((("first_visible", "collector_attached_before_visible"), False),),
        id="first-visible-late-attach",
    ),
    pytest.param(
        "restart-first-visible-persisted",
        ((("first_visible", "sequence_gap"), True),),
        id="first-visible-sequence-gap",
    ),
    pytest.param(
        "restart-first-visible-persisted",
        ((("first_visible", "pair_delta_px", "left"), 2),),
        id="first-visible-pair-mismatch",
    ),
    pytest.param(
        "corrupt-preference-default-pair",
        (
            (
                ("after", "corruption_subcases"),
                [
                    {
                        "kind": "malformed-json",
                        "preferred_pair": {"left_px": 304, "right_px": 368},
                    },
                    {
                        "kind": "unknown-version",
                        "preferred_pair": {"left_px": 304, "right_px": 368},
                    },
                ],
            ),
        ),
        id="corrupt-subcase-missing",
    ),
    pytest.param(
        "corrupt-preference-default-pair",
        (
            (
                ("after", "corruption_subcases"),
                [
                    {
                        "kind": "malformed-json",
                        "preferred_pair": {"left_px": 305, "right_px": 368},
                    },
                    {
                        "kind": "unknown-version",
                        "preferred_pair": {"left_px": 304, "right_px": 368},
                    },
                    {
                        "kind": "out-of-range-pair",
                        "preferred_pair": {"left_px": 304, "right_px": 368},
                    },
                ],
            ),
        ),
        id="corrupt-subcase-wrong-default",
    ),
    pytest.param(
        "preference-write-denied-session-usable",
        ((("after", "persisted"), True),),
        id="write-denied-reported-persisted",
    ),
    pytest.param(
        "preference-write-denied-session-usable",
        ((("after", "diagnostic"), None),),
        id="write-denied-diagnostic-missing",
    ),
    pytest.param(
        "3d-atlas-fit-light",
        ((("after", "graph_readability", "appearance"), "dark"),),
        id="3d-atlas-fit-light-appearance-mismatch",
    ),
    pytest.param(
        "3d-atlas-fit-dark",
        ((("after", "graph_readability", "screenshot_count"), 0),),
        id="3d-atlas-fit-dark-screenshot-missing",
    ),
    pytest.param(
        "3d-component-direct-select",
        ((("after", "graph_readability", "selected_component_id"), None),),
        id="3d-component-direct-select-selection-missing",
    ),
    pytest.param(
        "3d-component-maximum-zoom",
        ((("after", "graph_readability", "maximum_reached"), False),),
        id="3d-component-maximum-zoom-not-reached",
    ),
    pytest.param(
        "semantic-fallback-user-transition",
        (
            (
                (
                    "after",
                    "semantic_graph",
                    "renderer",
                    "semantic_primary_visible",
                ),
                False,
            ),
        ),
        id="semantic-fallback-primary-region-not-visible",
    ),
    pytest.param(
        "semantic-fallback-user-transition",
        ((("after", "graph_fingerprint", "sot_load_count"), 2),),
        id="semantic-fallback-command-counter-changed",
    ),
    pytest.param(
        "renderer-retry-snapshot-preserved",
        (
            (
                ("after", "semantic_graph", "renderer", "availability"),
                "manual_fallback",
            ),
        ),
        id="renderer-retry-remains-in-fallback",
    ),
    pytest.param(
        "final-cleanup-zero",
        ((("main_process_count",), 1),),
        id="cleanup-main-process-nonzero",
    ),
]


@pytest.mark.parametrize(
    ("case_id", "patches"),
    _SEMANTIC_PAYLOAD_MUTATIONS,
)
def test_identity_valid_semantic_mismatch_fails_exact_case_and_partials_later(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    patches: tuple[tuple[tuple[str, ...], object], ...],
) -> None:
    expected_case_ids = CASE_IDS
    app = _fixture_app(tmp_path)
    port = PayloadMutatingFakePort(
        app,
        mutation_case=case_id,
        patches=patches,
    )

    report_path = runtime.verify_workspace_layout_macos(
        app,
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    failure_index = expected_case_ids.index(case_id)
    assert report["status"] == "failed"
    assert report["error"]["operation"] == case_id
    if case_id in GRAPH_READABILITY_CASE_IDS:
        assert report["error"]["code"] == (
            "workspace_graph_readability_contract_mismatch"
        )
    else:
        assert report["error"]["code"].startswith("workspace_")
    assert [case["case_id"] for case in report["cases"]] == list(
        expected_case_ids
    )
    assert [case["status"] for case in report["cases"][:failure_index]] == [
        "Passed"
    ] * failure_index
    assert report["cases"][failure_index]["status"] == "Failed"
    assert [case["status"] for case in report["cases"][failure_index + 1 :]] == [
        "Partial"
    ] * (len(expected_case_ids) - failure_index - 1)
    assert report["manual_fallback"] is False


def _qualification_identity(report_path: Path) -> dict[str, str]:
    report = json.loads(report_path.read_text())
    build_identity = report["build_identity"]
    return {
        "app_source_manifest_sha256": build_identity[
            "app_source_manifest_sha256"
        ],
        "build_inputs_sha256": report["build_inputs_sha256"],
        "bundle_manifest_sha256": report["bundle_manifest_sha256"],
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "source_identity_sha256": build_identity["source_identity_sha256"],
    }


def _fixture_runtime_gate_report(
    evidence_root: Path,
    report_kind: str,
    *,
    qualification_report: Path,
    executable_sha256: str,
    status: str = "passed",
) -> Path:
    directory_name = {
        "single_instance": "runtime-single-instance",
        "workspace_layout": "runtime-workspace-layout",
    }[report_kind]
    report_path = evidence_root / directory_name / "run-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": status,
        "artifact": {
            "build_id": BUILD_ID,
            "bundle_identifier": runtime.BUNDLE_IDENTIFIER,
            "executable_sha256": executable_sha256,
            "installed_path_verified": True,
        },
        "package_qualification": _qualification_identity(qualification_report),
        "redactions": [
            "absolute_paths",
            "raw_logs",
            "secrets",
            "source_bodies",
        ],
    }
    if report_kind == "workspace_layout":
        payload["fixture"] = {
            "picker_evidence": _picker_fixture_evidence(101),
        }
        restart_index = CASE_IDS.index("restart-first-visible-persisted")
        payload["cases"] = [
            {
                "case_id": case_id,
                "status": "Passed",
                "identity": (
                    {"terminated_primary_pid": 202}
                    if case_id == "final-cleanup-zero"
                    else {
                        "pid": 101 if index < restart_index else 202,
                        "window_id": 42,
                    }
                ),
            }
            for index, case_id in enumerate(CASE_IDS)
        ]
        payload["lifecycle"] = {
            "picker_open_termination": {
                "empty_path_triggered": True,
                "native_panel_open_confirmed": True,
                "panel": _picker_panel_evidence(202),
                "normal_terminate_accepted": True,
                "main_process_absent": True,
                "main_window_absent": True,
                "observed_helpers_exited": True,
                "termination_elapsed_ms": 325,
            }
        }
    report_path.write_text(json.dumps(payload, sort_keys=True) + "\n")
    return report_path


def _rewrite_json(
    path: Path, mutate: Callable[[dict[str, object]], None]
) -> None:
    payload = json.loads(path.read_text())
    mutate(payload)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")


def test_runtime_suite_index_passes_only_for_same_qualified_packaged_build(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)
    evidence_root = tmp_path / "evidence"
    qualification = _fixture_qualification(evidence_root, app)
    installed_package = _fixture_installed_package_report(evidence_root, qualification)
    executable_sha256 = hashlib.sha256(
        (app / "Contents/MacOS/harness-desktop").read_bytes()
    ).hexdigest()
    single = _fixture_runtime_gate_report(
        evidence_root,
        "single_instance",
        qualification_report=qualification,
        executable_sha256=executable_sha256,
    )
    workspace = _fixture_runtime_gate_report(
        evidence_root,
        "workspace_layout",
        qualification_report=qualification,
        executable_sha256=executable_sha256,
    )

    index_path = runtime.write_runtime_suite_index(
        evidence_root,
        qualification,
        installed_package,
        single,
        workspace,
    )

    index = json.loads(index_path.read_text())
    assert index_path == evidence_root / "runtime-suite-index.json"
    assert index["schema_version"] == 1
    assert index["status"] == "passed"
    assert index["build_id"] == BUILD_ID
    assert index["bundle_identifier"] == runtime.BUNDLE_IDENTIFIER
    assert index["executable_sha256"] == executable_sha256
    assert index["package_report_sha256"] == hashlib.sha256(
        qualification.read_bytes()
    ).hexdigest()
    assert index["package_qualification"] == _qualification_identity(qualification)
    assert index["installed_package_report_sha256"] == hashlib.sha256(
        installed_package.read_bytes()
    ).hexdigest()
    assert index["installed_runtime"] == {
        "catalog_surface": "spotlight_apps",
        "installed_executable": "/Applications/HarnessKit.app/Contents/MacOS/harness-desktop",
        "run_report_sha256": json.loads(installed_package.read_text())[
            "install_evidence_chain"
        ]["run_report_sha256"],
        "status": "passed",
    }
    assert index["reports"] == {
        "single_instance": {
            "path": "runtime-single-instance/run-report.json",
            "sha256": hashlib.sha256(single.read_bytes()).hexdigest(),
            "status": "passed",
        },
        "workspace_layout": {
            "path": "runtime-workspace-layout/run-report.json",
            "sha256": hashlib.sha256(workspace.read_bytes()).hexdigest(),
            "status": "passed",
            "case_count": len(CASE_IDS),
        },
    }
    assert index["redactions"] == [
        "absolute_paths",
        "raw_logs",
        "secrets",
        "source_bodies",
    ]
    serialized = index_path.read_text()
    assert str(tmp_path) not in serialized
    assert "source_body" not in serialized
    assert "api_key" not in serialized


_SUITE_INDEX_FAILURES = [
    "installed-state-not-run",
    "installed-chain-missing",
    "installed-run-report-hash-mismatch",
    "installed-executable-not-applications",
    "single-runtime-not-installed",
    "workspace-runtime-installed-binding-missing",
    "single-build-id-mismatch",
    "workspace-bundle-mismatch",
    "workspace-executable-mismatch",
    "single-package-report-hash-mismatch",
    "both-reports-wrong-package-identity",
    "cross-report-package-identity-mismatch",
    "missing-single-report",
    "missing-workspace-report",
    "single-status-failed",
    "workspace-status-blocked",
    "workspace-hover-case-missing",
    "workspace-hover-case-failed",
    "workspace-case-order-mismatch",
    "workspace-picker-termination-missing",
    "workspace-picker-panel-unconfirmed",
    "workspace-picker-termination-identifier-mismatch",
    "workspace-picker-evidence-missing",
    "workspace-picker-identifier-mismatch",
    "workspace-picker-cancel-unconfirmed",
    "workspace-picker-cancel-panel-not-closed",
    "workspace-picker-mutation-nonzero",
    "workspace-picker-selection-unconfirmed",
    "workspace-picker-panel-not-closed",
    "workspace-picker-fixture-not-loaded",
    "workspace-picker-mode-mismatch",
    "workspace-picker-raw-path",
    "workspace-picker-initial-pid-mismatch",
    "workspace-picker-termination-pid-mismatch",
]


@pytest.mark.parametrize("failure_kind", _SUITE_INDEX_FAILURES)
def test_runtime_suite_index_mismatch_missing_or_nonpassed_never_false_passes(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    app = _fixture_app(tmp_path)
    evidence_root = tmp_path / "evidence"
    qualification = _fixture_qualification(evidence_root, app)
    installed_package = _fixture_installed_package_report(evidence_root, qualification)
    executable_sha256 = hashlib.sha256(
        (app / "Contents/MacOS/harness-desktop").read_bytes()
    ).hexdigest()
    single = _fixture_runtime_gate_report(
        evidence_root,
        "single_instance",
        qualification_report=qualification,
        executable_sha256=executable_sha256,
    )
    workspace = _fixture_runtime_gate_report(
        evidence_root,
        "workspace_layout",
        qualification_report=qualification,
        executable_sha256=executable_sha256,
    )

    if failure_kind == "installed-state-not-run":
        _rewrite_json(
            installed_package,
            lambda row: row.update(installed_runtime_state="NotRun"),
        )
    elif failure_kind == "installed-chain-missing":
        _rewrite_json(installed_package, lambda row: row.update(install_evidence_chain="NotRun"))
    elif failure_kind == "installed-run-report-hash-mismatch":
        _rewrite_json(
            installed_package,
            lambda row: row["install_evidence_chain"].update(
                run_report_sha256="e" * 64
            ),
        )
    elif failure_kind == "installed-executable-not-applications":
        installed = Path(
            json.loads(installed_package.read_text())["install_evidence_chain"][
                "run_report"
            ]
        )
        _rewrite_json(
            installed,
            lambda row: row.update(
                installed_executable=str(app / "Contents/MacOS/harness-desktop")
            ),
        )
        _rewrite_json(
            installed_package,
            lambda row: row["install_evidence_chain"].update(
                run_report_sha256=hashlib.sha256(installed.read_bytes()).hexdigest()
            ),
        )
    elif failure_kind == "single-runtime-not-installed":
        _rewrite_json(
            single,
            lambda row: row["artifact"].update(installed_path_verified=False),
        )
    elif failure_kind == "workspace-runtime-installed-binding-missing":
        def remove_installed_binding(row: dict[str, object]) -> None:
            artifact = row["artifact"]
            assert isinstance(artifact, dict)
            artifact.pop("installed_path_verified")

        _rewrite_json(workspace, remove_installed_binding)
    elif failure_kind == "single-build-id-mismatch":
        _rewrite_json(single, lambda row: row["artifact"].update(build_id="x" * 32))
    elif failure_kind == "workspace-bundle-mismatch":
        _rewrite_json(
            workspace,
            lambda row: row["artifact"].update(bundle_identifier="invalid.bundle"),
        )
    elif failure_kind == "workspace-executable-mismatch":
        _rewrite_json(
            workspace,
            lambda row: row["artifact"].update(executable_sha256="e" * 64),
        )
    elif failure_kind == "single-package-report-hash-mismatch":
        _rewrite_json(
            single,
            lambda row: row["package_qualification"].update(report_sha256="e" * 64),
        )
    elif failure_kind == "both-reports-wrong-package-identity":
        for report_path in (single, workspace):
            _rewrite_json(
                report_path,
                lambda row: row["package_qualification"].update(
                    app_source_manifest_sha256="e" * 64
                ),
            )
    elif failure_kind == "cross-report-package-identity-mismatch":
        _rewrite_json(
            workspace,
            lambda row: row["package_qualification"].update(
                bundle_manifest_sha256="e" * 64
            ),
        )
    elif failure_kind == "missing-single-report":
        single.unlink()
    elif failure_kind == "missing-workspace-report":
        workspace.unlink()
    elif failure_kind == "single-status-failed":
        _rewrite_json(single, lambda row: row.update(status="failed"))
    elif failure_kind == "workspace-status-blocked":
        _rewrite_json(workspace, lambda row: row.update(status="blocked"))
    elif failure_kind == "workspace-hover-case-missing":
        _rewrite_json(
            workspace,
            lambda row: row.update(
                cases=[
                    case
                    for case in row["cases"]
                    if case["case_id"] != "graph-hover-layout-stable"
                ]
            ),
        )
    elif failure_kind == "workspace-hover-case-failed":
        def fail_hover(row: dict[str, object]) -> None:
            cases = row["cases"]
            assert isinstance(cases, list)
            hover = next(
                case
                for case in cases
                if isinstance(case, dict)
                and case.get("case_id") == "graph-hover-layout-stable"
            )
            hover["status"] = "Failed"

        _rewrite_json(workspace, fail_hover)
    elif failure_kind == "workspace-case-order-mismatch":
        def reorder_hover(row: dict[str, object]) -> None:
            cases = row["cases"]
            assert isinstance(cases, list)
            hover_index = next(
                index
                for index, case in enumerate(cases)
                if isinstance(case, dict)
                and case.get("case_id") == "graph-hover-layout-stable"
            )
            cases[hover_index - 1], cases[hover_index] = (
                cases[hover_index],
                cases[hover_index - 1],
            )

        _rewrite_json(workspace, reorder_hover)
    elif failure_kind == "workspace-picker-termination-missing":
        _rewrite_json(workspace, lambda row: row.update(lifecycle={}))
    elif failure_kind == "workspace-picker-panel-unconfirmed":
        def unconfirm_picker(row: dict[str, object]) -> None:
            lifecycle = row["lifecycle"]
            assert isinstance(lifecycle, dict)
            termination = lifecycle["picker_open_termination"]
            assert isinstance(termination, dict)
            termination["native_panel_open_confirmed"] = False

        _rewrite_json(workspace, unconfirm_picker)
    elif failure_kind == "workspace-picker-termination-identifier-mismatch":
        def mismatch_termination_identifier(row: dict[str, object]) -> None:
            lifecycle = row["lifecycle"]
            assert isinstance(lifecycle, dict)
            termination = lifecycle["picker_open_termination"]
            assert isinstance(termination, dict)
            panel = termination["panel"]
            assert isinstance(panel, dict)
            panel["panel_identifier"] = "generic-dialog-v1"

        _rewrite_json(workspace, mismatch_termination_identifier)
    elif failure_kind == "workspace-picker-evidence-missing":
        _rewrite_json(workspace, lambda row: row.update(fixture={}))
    elif failure_kind == "workspace-picker-identifier-mismatch":
        def mismatch_fixture_identifier(row: dict[str, object]) -> None:
            fixture = row["fixture"]
            assert isinstance(fixture, dict)
            picker = fixture["picker_evidence"]
            assert isinstance(picker, dict)
            panel = picker["panel"]
            assert isinstance(panel, dict)
            panel["panel_identifier"] = "generic-dialog-v1"

        _rewrite_json(workspace, mismatch_fixture_identifier)
    elif failure_kind == "workspace-picker-cancel-unconfirmed":
        def unconfirm_cancel(row: dict[str, object]) -> None:
            picker = row["fixture"]["picker_evidence"]
            assert isinstance(picker, dict)
            picker["cancel_focus_restored"] = False

        _rewrite_json(workspace, unconfirm_cancel)
    elif failure_kind == "workspace-picker-cancel-panel-not-closed":
        def leave_cancel_panel_open(row: dict[str, object]) -> None:
            picker = row["fixture"]["picker_evidence"]
            assert isinstance(picker, dict)
            closure = picker["cancel_panel_closure"]
            assert isinstance(closure, dict)
            closure["panel_closed"] = False

        _rewrite_json(workspace, leave_cancel_panel_open)
    elif failure_kind == "workspace-picker-mutation-nonzero":
        def mutate_checkout(row: dict[str, object]) -> None:
            picker = row["fixture"]["picker_evidence"]
            assert isinstance(picker, dict)
            picker["checkout_mutation_count"] = 1

        _rewrite_json(workspace, mutate_checkout)
    elif failure_kind == "workspace-picker-selection-unconfirmed":
        def unconfirm_selection(row: dict[str, object]) -> None:
            picker = row["fixture"]["picker_evidence"]
            assert isinstance(picker, dict)
            selection = picker["selection"]
            assert isinstance(selection, dict)
            selection["accepted"] = False

        _rewrite_json(workspace, unconfirm_selection)
    elif failure_kind == "workspace-picker-panel-not-closed":
        def leave_panel_open(row: dict[str, object]) -> None:
            picker = row["fixture"]["picker_evidence"]
            assert isinstance(picker, dict)
            selection = picker["selection"]
            assert isinstance(selection, dict)
            selection["panel_closed"] = False

        _rewrite_json(workspace, leave_panel_open)
    elif failure_kind == "workspace-picker-fixture-not-loaded":
        def unload_fixture(row: dict[str, object]) -> None:
            picker = row["fixture"]["picker_evidence"]
            assert isinstance(picker, dict)
            picker["fixture_loaded"] = False

        _rewrite_json(workspace, unload_fixture)
    elif failure_kind == "workspace-picker-mode-mismatch":
        def mismatch_picker_mode(row: dict[str, object]) -> None:
            picker = row["fixture"]["picker_evidence"]
            assert isinstance(picker, dict)
            picker["fixture_registration_mode"] = "direct_path_injection"

        _rewrite_json(workspace, mismatch_picker_mode)
    elif failure_kind == "workspace-picker-raw-path":
        def inject_raw_path(row: dict[str, object]) -> None:
            picker = row["fixture"]["picker_evidence"]
            assert isinstance(picker, dict)
            picker["raw_path"] = "/private/secret-checkout"

        _rewrite_json(workspace, inject_raw_path)
    elif failure_kind == "workspace-picker-initial-pid-mismatch":
        def mismatch_initial_picker_pid(row: dict[str, object]) -> None:
            fixture = row["fixture"]
            assert isinstance(fixture, dict)
            picker = fixture["picker_evidence"]
            assert isinstance(picker, dict)
            panel = picker["panel"]
            selection = picker["selection"]
            assert isinstance(panel, dict)
            assert isinstance(selection, dict)
            panel["pid"] = 999
            selection["pid"] = 999

        _rewrite_json(workspace, mismatch_initial_picker_pid)
    elif failure_kind == "workspace-picker-termination-pid-mismatch":
        def mismatch_termination_picker_pid(row: dict[str, object]) -> None:
            lifecycle = row["lifecycle"]
            assert isinstance(lifecycle, dict)
            termination = lifecycle["picker_open_termination"]
            assert isinstance(termination, dict)
            panel = termination["panel"]
            assert isinstance(panel, dict)
            panel["pid"] = 999

        _rewrite_json(workspace, mismatch_termination_picker_pid)
    else:  # pragma: no cover - parameter exhaustiveness guard
        raise AssertionError(failure_kind)

    index_path = runtime.write_runtime_suite_index(
        evidence_root,
        qualification,
        installed_package,
        single,
        workspace,
    )

    index = json.loads(index_path.read_text())
    assert index["status"] == "failed"
    assert index["status"] != "passed"
    assert index["error"]["code"].startswith("runtime_suite_")
    assert index["build_id"] == BUILD_ID
    assert index["package_report_sha256"] == hashlib.sha256(
        qualification.read_bytes()
    ).hexdigest()
    assert str(tmp_path) not in index_path.read_text()


def test_cli_single_instance_report_option_writes_integrated_suite_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _fixture_app(tmp_path)
    evidence_root = tmp_path / "evidence"
    qualification = _fixture_qualification(evidence_root, app)
    installed_package = _fixture_installed_package_report(evidence_root, qualification)
    executable_sha256 = hashlib.sha256(
        (app / "Contents/MacOS/harness-desktop").read_bytes()
    ).hexdigest()
    single = _fixture_runtime_gate_report(
        evidence_root,
        "single_instance",
        qualification_report=qualification,
        executable_sha256=executable_sha256,
    )
    workspace = _fixture_runtime_gate_report(
        evidence_root,
        "workspace_layout",
        qualification_report=qualification,
        executable_sha256=executable_sha256,
    )
    calls: list[tuple[Path, Path, Path, Path, Path]] = []

    monkeypatch.setattr(
        runtime,
        "verify_workspace_layout_macos",
        lambda *_args, **_kwargs: workspace,
    )
    real_writer = runtime.write_runtime_suite_index

    def write_index(
        root: Path,
        qualification_report: Path,
        package_report: Path,
        single_report: Path,
        workspace_report: Path,
    ) -> Path:
        calls.append(
            (root, qualification_report, package_report, single_report, workspace_report)
        )
        return real_writer(
            root,
            qualification_report,
            package_report,
            single_report,
            workspace_report,
        )

    monkeypatch.setattr(runtime, "write_runtime_suite_index", write_index)

    exit_code = runtime.main(
        [
            "--app",
            str(app),
            "--qualification-report",
            str(qualification),
            "--package-report",
            str(installed_package),
            "--evidence-root",
            str(evidence_root),
            "--single-instance-report",
            str(single),
            "--timeout-seconds",
            "1",
        ]
    )

    assert exit_code == 0
    assert calls == [
        (evidence_root, qualification, installed_package, single, workspace)
    ]
    index = json.loads((evidence_root / "runtime-suite-index.json").read_text())
    assert index["status"] == "passed"
