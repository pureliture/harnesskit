from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Protocol


BUNDLE_IDENTIFIER = "io.github.pureliture.harnesskit"
MAIN_WINDOW_TITLE = "HarnessKit"
RUNTIME_LOG_FILE_NAME = f"{MAIN_WINDOW_TITLE}.log"
INSTALLED_APP_PATH = Path("/Applications/HarnessKit.app")
COLLECTOR_ID = "macos_ax_cg_sck_single_instance_v2"
SCREEN_CAPTURE_SOURCE_MODE = "screen_capture_kit_desktop_independent_window"
SCREEN_CAPTURE_MODE = f"{SCREEN_CAPTURE_SOURCE_MODE}_sips_png_normalized"
DEFAULT_TIMEOUT_SECONDS = 20.0
WORKSPACE_AX_READINESS_TIMEOUT_SECONDS = 3.0
WORKSPACE_AX_READINESS_POLL_SECONDS = 0.1
EXPECTED_SINGLE_INSTANCE_CALLBACK_COUNT = 5
CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER = "harness-checkout-directory-picker-v1"
CHECKOUT_DIRECTORY_PICKER_APPKIT_AX_IDENTIFIER = "open-panel"
CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIERS = frozenset(
    {
        CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
        CHECKOUT_DIRECTORY_PICKER_APPKIT_AX_IDENTIFIER,
    }
)
WORKSPACE_AMENDMENT_SELECTORS = frozenset(
    {
        "#left-pane-disclosure",
        "#right-pane-disclosure",
        "#typography-menu-trigger",
        "#register-checkout",
        "#load-sot",
        "#local-scope-all",
        "[data-local-instance][aria-pressed=true]",
        "#project-ignore-open",
        "[data-project-ignore-dialog]",
        "#project-ignore-text",
        "#project-ignore-save",
        "[data-project-ignore-dialog] [role=alert]",
        "[data-correlation-state]",
        "#local-correlation-detail",
        "#local-runtime-status",
        "#local-runtime-snapshot",
        "#typography-source-preview-body",
    }
)
WORKSPACE_TOOLBAR_TAB_ORDER = (
    "left-disclosure",
    "repository",
    "checkout-register",
    "load-sot",
    "appearance",
    "typography",
    "right-disclosure",
)
_BUILD_ID_PATTERN = re.compile(rb"HARNESS_PACKAGE_BUILD_ID=([0-9a-f]{32})")
_CASE_EXPECTATIONS: dict[str, dict[str, object]] = {
    "first": {"main_process_count": 1, "main_window_count": 1, "visible": True},
    "focus-restored": {
        "notifier_count": 3,
        "same_primary_pid": True,
        "same_main_window": True,
        "focused": True,
        "frontmost": True,
    },
    "minimized-restored": {
        "precondition": "minimized",
        "restored": "visible_focused_frontmost",
        "same_primary_pid": True,
        "same_main_window": True,
    },
    "hidden-restored": {
        "precondition": "hidden",
        "restored": "visible_focused_frontmost",
        "same_primary_pid": True,
        "same_main_window": True,
    },
    "post-quit-relaunch": {
        "post_quit_main_process_count": 0,
        "post_quit_main_window_count": 0,
        "relaunched_main_process_count": 1,
        "relaunched_main_window_count": 1,
        "new_primary_pid": True,
    },
}
SINGLE_INSTANCE_CASE_IDS = tuple(_CASE_EXPECTATIONS)


class RuntimeQualificationError(RuntimeError):
    def __init__(
        self,
        code: str,
        operation: str,
        *,
        blocked: bool = False,
        evidence: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.operation = operation
        self.blocked = blocked
        self.evidence = evidence


@dataclass(frozen=True)
class PermissionPreflight:
    accessibility: bool
    screen_capture: bool
    system_events: bool


@dataclass(frozen=True)
class CollectorEvidence:
    collector_id: str
    sha256: str
    relative_path: str


@dataclass(frozen=True)
class ScreenshotCaptureEvidence:
    capture_mode: str
    pixel_width: int
    pixel_height: int
    point_pixel_scale: float
    content_rect: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class WorkspaceFirstVisibleHandle:
    process: subprocess.Popen[bytes]
    ready_path: Path
    destination: Path
    anchor_component_ids: tuple[str, str, str]
    started_monotonic: float


@dataclass(frozen=True)
class WorkspaceDragCaptureEvidence:
    action: dict[str, object]
    screenshot: ScreenshotCaptureEvidence
    destination: Path


@dataclass(frozen=True)
class ApplicationObservation:
    pid: int
    executable_path: Path
    executable_sha256: str
    build_id: str | None


@dataclass(frozen=True)
class ProcessObservation:
    pid: int
    ppid: int
    role: str


@dataclass(frozen=True)
class WindowObservation:
    window_id: int
    owner_pid: int
    title: str
    role: str
    subrole: str
    on_screen: bool
    minimized: bool
    focused: bool
    main: bool


@dataclass(frozen=True)
class RuntimeObservation:
    main_pids: tuple[int, ...]
    processes: tuple[ProcessObservation, ...]
    main_windows: tuple[WindowObservation, ...]
    frontmost_pid: int | None
    hidden_pids: tuple[int, ...]


@dataclass(frozen=True)
class ObservationExpectation:
    primary_pid: int | None = None
    window_id: int | None = None
    absent: bool = False
    minimized: bool | None = None
    hidden: bool | None = None
    on_screen: bool | None = None
    focused: bool | None = None
    frontmost: bool | None = None
    main: bool | None = None


@dataclass(frozen=True)
class _ArtifactIdentity:
    app: Path
    executable: Path
    executable_sha256: str
    build_id: str
    version: str


class RuntimeAutomationPort(Protocol):
    def prepare_collector(self, evidence_dir: Path) -> CollectorEvidence: ...

    def preflight(self) -> PermissionPreflight: ...

    def list_applications(
        self, bundle_identifier: str
    ) -> tuple[ApplicationObservation, ...]: ...

    def terminate(self, pid: int) -> bool: ...

    def launch_primary(
        self, artifact: _ArtifactIdentity, environment: dict[str, str]
    ) -> int: ...

    def launch_repeat(
        self, artifact: _ArtifactIdentity, environment: dict[str, str]
    ) -> int: ...

    def terminate_repeat_request(self, pid: int) -> bool: ...

    def wait_process_exit(self, pid: int, timeout_seconds: float) -> bool: ...

    def process_exit_code(self, pid: int) -> int | None: ...

    def wait_processes_exit(
        self, pids: tuple[int, ...], timeout_seconds: float
    ) -> bool: ...

    def observe_until(
        self,
        executable: Path,
        bundle_identifier: str,
        window_title: str,
        expectation: ObservationExpectation,
        timeout_seconds: float,
    ) -> RuntimeObservation: ...

    def set_minimized(self, pid: int, minimized: bool) -> bool: ...

    def hide_application(self, pid: int) -> bool: ...

    def capture_window(
        self, owner_pid: int, window_id: int, destination: Path
    ) -> ScreenshotCaptureEvidence: ...


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _runtime_log_summary(runtime_home: Path) -> dict[str, object]:
    log_path = (
        runtime_home
        / "Library/Logs/io.github.pureliture.harnesskit"
        / RUNTIME_LOG_FILE_NAME
    )
    try:
        if log_path.is_symlink() or not log_path.is_file() or log_path.stat().st_size > 1024 * 1024:
            raise OSError
        body = log_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        body = ""
    return {
        "available": bool(body),
        "raw_log_stored": False,
        "single_instance_callback_count": body.count(
            "single-instance relaunch received"
        ),
        "window_presentation_count": body.count("appearance window presented:"),
        "window_presentation_active_key_count": body.count(
            "appearance window presented: foreground_requested=true, active=true, key=true"
        ),
        "window_presentation_inactive_or_nonkey_count": sum(
            body.count(
                "appearance window presented: foreground_requested=true, "
                f"active={active}, key={key}"
            )
            for active, key in [("false", "false"), ("false", "true"), ("true", "false")]
        ),
    }


def _artifact_identity(app: Path) -> _ArtifactIdentity:
    app = app.resolve(strict=True)
    if not app.is_dir() or app.suffix != ".app":
        raise RuntimeQualificationError("packaged_app_invalid", "artifact validation")
    info_path = app / "Contents/Info.plist"
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        raise RuntimeQualificationError("packaged_info_plist_invalid", "artifact validation")
    if info.get("CFBundleIdentifier") != BUNDLE_IDENTIFIER:
        raise RuntimeQualificationError("packaged_bundle_identifier_mismatch", "artifact validation")
    executable_name = info.get("CFBundleExecutable")
    version = info.get("CFBundleShortVersionString")
    if not isinstance(executable_name, str) or not isinstance(version, str):
        raise RuntimeQualificationError("packaged_info_plist_invalid", "artifact validation")
    executable = (app / "Contents/MacOS" / executable_name).resolve(strict=True)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeQualificationError("packaged_main_executable_invalid", "artifact validation")
    body = executable.read_bytes()
    build_match = _BUILD_ID_PATTERN.search(body)
    if build_match is None:
        raise RuntimeQualificationError("package_build_identity_missing", "artifact validation")
    return _ArtifactIdentity(
        app=app,
        executable=executable,
        executable_sha256=_sha256_bytes(body),
        build_id=build_match.group(1).decode("ascii"),
        version=version,
    )


def _installed_app_path_verified(artifact: _ArtifactIdentity) -> bool:
    try:
        installed_app = INSTALLED_APP_PATH.resolve(strict=True)
    except OSError:
        return False
    return not INSTALLED_APP_PATH.is_symlink() and artifact.app == installed_app


def _package_qualification_identity(
    qualification_report: Path,
    artifact: _ArtifactIdentity,
) -> dict[str, str]:
    try:
        report_path = qualification_report.resolve(strict=True)
        if report_path.is_symlink() or not report_path.is_file():
            raise OSError
        report_body = report_path.read_bytes()
        report = json.loads(report_body)
        manifest_path = report_path.parent / "bundle-manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise OSError
        manifest_body = manifest_path.read_bytes()
        manifest = json.loads(manifest_body)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        raise RuntimeQualificationError(
            "package_qualification_invalid", "artifact validation"
        )
    if not isinstance(report, dict) or not isinstance(manifest, list):
        raise RuntimeQualificationError(
            "package_qualification_invalid", "artifact validation"
        )
    build_identity = report.get("build_identity")
    if not isinstance(build_identity, dict) or any(
        [
            report.get("status") != "passed",
            build_identity.get("build_id") != artifact.build_id,
            build_identity.get("bundle_identifier") != BUNDLE_IDENTIFIER,
            build_identity.get("version") != artifact.version,
            report.get("bundle_manifest_sha256") != _sha256_bytes(manifest_body),
        ]
    ):
        raise RuntimeQualificationError(
            "package_qualification_identity_mismatch", "artifact validation"
        )
    executable_entries = [
        entry
        for entry in manifest
        if isinstance(entry, dict)
        and entry.get("path") == f"Contents/MacOS/{artifact.executable.name}"
        and entry.get("type") == "file"
    ]
    if len(executable_entries) != 1 or executable_entries[0].get(
        "sha256"
    ) != artifact.executable_sha256:
        raise RuntimeQualificationError(
            "package_qualification_executable_mismatch", "artifact validation"
        )
    required_hashes = {
        "app_source_manifest_sha256": build_identity.get(
            "app_source_manifest_sha256"
        ),
        "source_identity_sha256": build_identity.get("source_identity_sha256"),
        "build_inputs_sha256": report.get("build_inputs_sha256"),
        "bundle_manifest_sha256": report.get("bundle_manifest_sha256"),
    }
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in required_hashes.values()
    ):
        raise RuntimeQualificationError(
            "package_qualification_identity_mismatch", "artifact validation"
        )
    return {
        "report_sha256": _sha256_bytes(report_body),
        **required_hashes,
    }


def _observation_payload(observation: RuntimeObservation) -> dict[str, object]:
    return {
        "main_pids": list(observation.main_pids),
        "processes": [asdict(process) for process in observation.processes],
        "main_windows": [asdict(window) for window in observation.main_windows],
        "frontmost_pid": observation.frontmost_pid,
        "hidden_pids": list(observation.hidden_pids),
    }


def _observation_matches(
    observation: RuntimeObservation, expectation: ObservationExpectation
) -> bool:
    if expectation.absent:
        return not observation.main_pids and not observation.main_windows
    pid = expectation.primary_pid
    if pid is None or observation.main_pids != (pid,) or len(observation.main_windows) != 1:
        return False
    window = observation.main_windows[0]
    if window.owner_pid != pid:
        return False
    if expectation.window_id is not None and window.window_id != expectation.window_id:
        return False
    if expectation.minimized is not None and window.minimized != expectation.minimized:
        return False
    hidden = pid in observation.hidden_pids
    if expectation.hidden is not None and hidden != expectation.hidden:
        return False
    if expectation.on_screen is not None and window.on_screen != expectation.on_screen:
        return False
    if expectation.focused is not None and window.focused != expectation.focused:
        return False
    if expectation.main is not None and window.main != expectation.main:
        return False
    frontmost = observation.frontmost_pid == pid
    if expectation.frontmost is not None and frontmost != expectation.frontmost:
        return False
    return True


def _assert_observation(
    observation: RuntimeObservation,
    expectation: ObservationExpectation,
    operation: str,
) -> None:
    if expectation.absent:
        if observation.main_pids:
            raise RuntimeQualificationError(
                f"{operation}_main_process_count_mismatch", operation
            )
        if observation.main_windows:
            raise RuntimeQualificationError(f"{operation}_main_window_count_mismatch", operation)
        return
    if len(observation.main_pids) != 1:
        raise RuntimeQualificationError(f"{operation}_main_process_count_mismatch", operation)
    if observation.main_pids != (expectation.primary_pid,):
        raise RuntimeQualificationError(f"{operation}_primary_identity_changed", operation)
    if len(observation.main_windows) != 1:
        raise RuntimeQualificationError(f"{operation}_main_window_count_mismatch", operation)
    window = observation.main_windows[0]
    if window.owner_pid != expectation.primary_pid:
        raise RuntimeQualificationError(f"{operation}_window_owner_mismatch", operation)
    if expectation.window_id is not None and window.window_id != expectation.window_id:
        raise RuntimeQualificationError(f"{operation}_window_identity_changed", operation)
    if window.role != "AXWindow" or window.subrole != "AXStandardWindow":
        raise RuntimeQualificationError(f"{operation}_main_window_semantics_mismatch", operation)
    checks = (
        (expectation.minimized, window.minimized, "minimized_state_mismatch"),
        (
            expectation.hidden,
            expectation.primary_pid in observation.hidden_pids,
            "hidden_state_mismatch",
        ),
        (expectation.on_screen, window.on_screen, "on_screen_state_mismatch"),
        (expectation.focused, window.focused, "focus_state_mismatch"),
        (expectation.main, window.main, "main_state_mismatch"),
        (
            expectation.frontmost,
            observation.frontmost_pid == expectation.primary_pid,
            "frontmost_state_mismatch",
        ),
    )
    for expected, observed, suffix in checks:
        if expected is not None and expected != observed:
            raise RuntimeQualificationError(f"{operation}_{suffix}", operation)


def _helper_pids(observation: RuntimeObservation) -> tuple[int, ...]:
    mains = set(observation.main_pids)
    return tuple(sorted(process.pid for process in observation.processes if process.pid not in mains))


def _permission_gate(preflight: PermissionPreflight) -> None:
    if not preflight.accessibility:
        raise RuntimeQualificationError(
            "accessibility_api_denied", "AXIsProcessTrusted", blocked=True
        )
    if not preflight.screen_capture:
        raise RuntimeQualificationError(
            "screen_capture_api_denied",
            "CGPreflightScreenCaptureAccess",
            blocked=True,
        )
    if not preflight.system_events:
        raise RuntimeQualificationError(
            "system_events_automation_denied",
            "System Events process enumeration",
            blocked=True,
        )


def _same_artifact(application: ApplicationObservation, artifact: _ArtifactIdentity) -> bool:
    try:
        same_path = application.executable_path.resolve(strict=True) == artifact.executable
    except OSError:
        return False
    return (
        same_path
        and application.executable_sha256 == artifact.executable_sha256
        and application.build_id == artifact.build_id
    )


def _expect_visible(pid: int, window_id: int | None = None) -> ObservationExpectation:
    return ObservationExpectation(
        primary_pid=pid,
        window_id=window_id,
        minimized=False,
        hidden=False,
        on_screen=True,
        focused=True,
        frontmost=True,
        main=True,
    )


def _expect_first_visible(pid: int) -> ObservationExpectation:
    return ObservationExpectation(
        primary_pid=pid,
        minimized=False,
        hidden=False,
        on_screen=True,
        main=True,
    )


def verify_single_instance_macos(
    app: Path,
    evidence_root: Path,
    *,
    port: RuntimeAutomationPort | None = None,
    qualification_report: Path | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    evidence_dir = evidence_root.resolve() / "runtime-single-instance"
    evidence_dir.mkdir(parents=True, exist_ok=False)
    cases_dir = evidence_dir / "cases"
    cases_dir.mkdir()
    runtime_root = Path(tempfile.mkdtemp(prefix="harness-desktop-runtime-"))
    runtime_root.chmod(0o700)
    runtime_home = runtime_root / "home"
    runtime_home.mkdir(mode=0o700)
    runtime_tmp = runtime_root / "tmp"
    runtime_tmp.mkdir(mode=0o700)
    report_path = evidence_dir / "run-report.json"
    samples_path = evidence_dir / "process-window-samples.jsonl"
    cases: list[dict[str, object]] = []
    samples: list[dict[str, object]] = []
    notifiers: list[dict[str, object]] = []
    case_started_at: dict[str, str] = {}
    lifecycle: dict[str, object] = {
        "helper_observation_scope": "main_process_descendants",
    }
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "failed",
        "driver_sha256": _sha256_file(Path(__file__).resolve()),
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "launch_strategy": {
            "primary": "nsworkspace_explicit_new_instance_activating",
            "notifier": "system_open_new_instance_activating",
        },
        "collector": None,
        "preflight": None,
        "artifact": None,
        "package_qualification": None,
        "cases": cases,
        "notifiers": notifiers,
        "lifecycle": lifecycle,
        "runtime_log_summary": None,
    }
    automation = port
    real_runtime_requested = port is None
    artifact: _ArtifactIdentity | None = None
    known_main_pids: set[int] = set()
    known_repeat_request_pids: set[int] = set()
    known_helper_pids: set[int] = set()
    active_case_id: str | None = None

    def observe(
        expectation: ObservationExpectation, operation: str
    ) -> RuntimeObservation:
        assert artifact is not None and automation is not None
        result = automation.observe_until(
            artifact.executable,
            BUNDLE_IDENTIFIER,
            MAIN_WINDOW_TITLE,
            expectation,
            timeout_seconds,
        )
        known_main_pids.update(result.main_pids)
        known_helper_pids.update(_helper_pids(result))
        report["last_observation"] = {
            "operation": operation,
            "observation": _observation_payload(result),
        }
        _assert_observation(result, expectation, operation)
        return result

    def sample(case_id: str, phase: str, observation: RuntimeObservation) -> None:
        samples.append(
            {
                "case_id": case_id,
                "phase": phase,
                "observation": _observation_payload(observation),
            }
        )

    def record_case(
        case_id: str, observations: list[RuntimeObservation]
    ) -> None:
        assert automation is not None
        final = observations[-1]
        if len(final.main_windows) != 1 or final.main_windows[0].window_id <= 0:
            raise RuntimeQualificationError(
                f"{case_id}_capture_window_unavailable", case_id
            )
        screenshot_path = cases_dir / f"{case_id}.png"
        observation_path = cases_dir / f"{case_id}.json"
        capture = automation.capture_window(
            final.main_windows[0].owner_pid,
            final.main_windows[0].window_id,
            screenshot_path,
        )
        _write_json(
            observation_path,
            [_observation_payload(observation) for observation in observations],
        )
        cases.append(
            {
                "case_id": case_id,
                "started_at": case_started_at[case_id],
                "ended_at": _utc_now(),
                "expected": _CASE_EXPECTATIONS[case_id],
                "observed": {
                    "main_process_count": len(final.main_pids),
                    "main_window_count": len(final.main_windows),
                    "frontmost": final.frontmost_pid in final.main_pids,
                    "focused": final.main_windows[0].focused,
                    "visible": final.main_windows[0].on_screen,
                },
                "status": "Passed",
                "screenshot": {
                    "path": screenshot_path.relative_to(evidence_dir).as_posix(),
                    "sha256": _sha256_file(screenshot_path),
                    **asdict(capture),
                },
                "observation": {
                    "path": observation_path.relative_to(evidence_dir).as_posix(),
                    "sha256": _sha256_file(observation_path),
                },
                "redactions": [
                    "absolute_paths",
                    "raw_logs",
                    "secrets",
                    "source_bodies",
                ],
                "limitations": [
                    "AX/CG observation and screenshot capture are sequential",
                    "helper inventory is limited to observed main-process descendants",
                ],
                "error": None,
            }
        )

    def finalize_case_slots() -> None:
        nonlocal active_case_id
        report_status = str(report.get("status"))
        error = report.get("error")
        if report_status == "passed" and len(cases) != len(SINGLE_INSTANCE_CASE_IDS):
            report_status = "failed"
            report["status"] = report_status
            error = {
                "code": "single_instance_case_schema_incomplete",
                "operation": "runtime report finalization",
            }
            report["error"] = error
            active_case_id = next(
                case_id
                for case_id in SINGLE_INSTANCE_CASE_IDS
                if not any(case.get("case_id") == case_id for case in cases)
            )

        existing = {
            str(case.get("case_id")): case
            for case in cases
            if isinstance(case, dict)
            and case.get("case_id") in SINGLE_INSTANCE_CASE_IDS
        }
        active_index = (
            SINGLE_INSTANCE_CASE_IDS.index(active_case_id)
            if active_case_id in SINGLE_INSTANCE_CASE_IDS
            else None
        )
        finalized: list[dict[str, object]] = []
        for index, case_id in enumerate(SINGLE_INSTANCE_CASE_IDS):
            case = existing.get(case_id)
            if case is not None:
                if report_status != "passed" and active_index == index:
                    case["status"] = (
                        "Unavailable" if report_status == "blocked" else "Failed"
                    )
                    case["error"] = error
                finalized.append(case)
                continue

            if report_status == "blocked" and active_index is None:
                status = "Unavailable"
            elif active_index is not None and index == active_index:
                status = "Unavailable" if report_status == "blocked" else "Failed"
            elif active_index is None and index == 0:
                status = "Failed"
            else:
                status = "Partial"
            finalized.append(
                {
                    "case_id": case_id,
                    "started_at": case_started_at.get(case_id),
                    "ended_at": _utc_now(),
                    "expected": _CASE_EXPECTATIONS[case_id],
                    "observed": None,
                    "status": status,
                    "screenshot": None,
                    "observation": None,
                    "redactions": [
                        "absolute_paths",
                        "raw_logs",
                        "secrets",
                        "source_bodies",
                    ],
                    "limitations": [
                        "case was not completed after the recorded runtime gate failure"
                    ],
                    "error": error,
                }
            )
        cases[:] = finalized

    def launch_notifier(operation: str) -> int:
        assert artifact is not None and automation is not None
        started = time.monotonic()
        request_pid = automation.launch_repeat(artifact, environment)
        known_repeat_request_pids.add(request_pid)
        request_exited = automation.wait_process_exit(request_pid, timeout_seconds)
        request_exit_code = (
            automation.process_exit_code(request_pid) if request_exited else None
        )
        launch_request_accepted: bool | None
        if not request_exited:
            launch_request_accepted = None
        else:
            launch_request_accepted = request_exit_code == 0
        notifiers.append(
            {
                "operation": operation,
                "launch_request_accepted": launch_request_accepted,
                "request_process_pid": request_pid,
                "request_process_exited": request_exited,
                "request_process_exit_code": request_exit_code,
                "notifier_pid": None,
                "notifier_pid_observation": "not_exposed_by_system_open",
                "elapsed_ms": max(0, round((time.monotonic() - started) * 1000)),
            }
        )
        if not request_exited:
            raise RuntimeQualificationError(
                f"{operation}_request_did_not_exit", operation
            )
        known_repeat_request_pids.discard(request_pid)
        if request_exit_code != 0:
            raise RuntimeQualificationError(
                f"{operation}_request_exit_code_invalid", operation
            )
        return request_pid

    environment = {
        "HOME": str(runtime_home),
        "TMPDIR": str(runtime_tmp),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }

    try:
        artifact = _artifact_identity(app)
        report["artifact"] = {
            "name": artifact.app.name,
            "main_executable": artifact.executable.name,
            "executable_sha256": artifact.executable_sha256,
            "build_id": artifact.build_id,
            "version": artifact.version,
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
        if automation is None:
            if sys.platform != "darwin":
                raise RuntimeQualificationError(
                    "macos_runtime_required", "platform preflight"
                )
            automation = MacOSRuntimeAutomationPort(evidence_dir)
        collector = automation.prepare_collector(evidence_dir)
        report["collector"] = asdict(collector)
        preflight = automation.preflight()
        report["preflight"] = asdict(preflight)
        _permission_gate(preflight)

        applications = automation.list_applications(BUNDLE_IDENTIFIER)
        for application in applications:
            if not _same_artifact(application, artifact):
                raise RuntimeQualificationError(
                    "single_instance_namespace_conflict",
                    "bundle namespace preflight",
                    blocked=True,
                )
        for application in applications:
            if not automation.terminate(application.pid):
                raise RuntimeQualificationError(
                    "baseline_termination_failed", "bundle namespace preflight"
                )
            if not automation.wait_process_exit(application.pid, timeout_seconds):
                raise RuntimeQualificationError(
                    "baseline_process_did_not_exit", "bundle namespace preflight"
                )
        observe(ObservationExpectation(absent=True), "baseline")

        active_case_id = "first"
        case_started_at["first"] = _utc_now()
        primary_pid = automation.launch_primary(artifact, environment)
        known_main_pids.add(primary_pid)
        first = observe(_expect_first_visible(primary_pid), "first")
        primary_window_id = first.main_windows[0].window_id
        sample("first", "steady", first)
        record_case("first", [first])

        active_case_id = "focus-restored"
        case_started_at["focus-restored"] = _utc_now()
        repeated: list[RuntimeObservation] = []
        for repeat_index in range(1, 4):
            operation = f"focus_restored_repeat_{repeat_index}"
            launch_notifier(operation)
            restored = observe(_expect_visible(primary_pid, primary_window_id), operation)
            repeated.append(restored)
            sample("focus-restored", f"repeat-{repeat_index}", restored)
        record_case("focus-restored", repeated)

        active_case_id = "minimized-restored"
        case_started_at["minimized-restored"] = _utc_now()
        if not automation.set_minimized(primary_pid, True):
            raise RuntimeQualificationError("minimize_action_failed", "minimized-restored")
        minimized = observe(
            ObservationExpectation(
                primary_pid=primary_pid,
                window_id=primary_window_id,
                minimized=True,
                hidden=False,
                on_screen=False,
            ),
            "minimize_precondition",
        )
        sample("minimized-restored", "precondition", minimized)
        launch_notifier("minimized_restored")
        minimized_restored = observe(
            _expect_visible(primary_pid, primary_window_id), "minimized_restored"
        )
        sample("minimized-restored", "restored", minimized_restored)
        record_case("minimized-restored", [minimized, minimized_restored])

        active_case_id = "hidden-restored"
        case_started_at["hidden-restored"] = _utc_now()
        if not automation.hide_application(primary_pid):
            raise RuntimeQualificationError("hide_action_failed", "hidden-restored")
        hidden = observe(
            ObservationExpectation(
                primary_pid=primary_pid,
                window_id=primary_window_id,
                minimized=False,
                hidden=True,
                on_screen=False,
            ),
            "hide_precondition",
        )
        sample("hidden-restored", "precondition", hidden)
        launch_notifier("hidden_restored")
        hidden_restored = observe(
            _expect_visible(primary_pid, primary_window_id), "hidden_restored"
        )
        sample("hidden-restored", "restored", hidden_restored)
        record_case("hidden-restored", [hidden, hidden_restored])

        active_case_id = "post-quit-relaunch"
        case_started_at["post-quit-relaunch"] = _utc_now()
        first_helpers = tuple(sorted(known_helper_pids))
        if not automation.terminate(primary_pid):
            raise RuntimeQualificationError("quit_action_failed", "post-quit-relaunch")
        if not automation.wait_process_exit(primary_pid, timeout_seconds):
            raise RuntimeQualificationError(
                "post_quit_main_process_remained", "post-quit-relaunch"
            )
        known_main_pids.discard(primary_pid)
        post_quit_zero = observe(ObservationExpectation(absent=True), "post_quit")
        sample("post-quit-relaunch", "post-quit-zero", post_quit_zero)
        first_helpers_exited = not first_helpers or automation.wait_processes_exit(
            first_helpers, timeout_seconds
        )
        lifecycle["post_quit"] = {
            "main_absent": True,
            "helper_pids": list(first_helpers),
            "helpers_exited": first_helpers_exited,
        }
        if not first_helpers_exited:
            raise RuntimeQualificationError(
                "post_quit_helper_processes_remained", "post-quit-relaunch"
            )
        known_helper_pids.clear()

        relaunched_pid = automation.launch_primary(artifact, environment)
        known_main_pids.add(relaunched_pid)
        if relaunched_pid == primary_pid:
            raise RuntimeQualificationError(
                "post_quit_primary_pid_not_replaced", "post-quit-relaunch"
            )
        relaunched = observe(_expect_first_visible(relaunched_pid), "post_quit_relaunch")
        sample("post-quit-relaunch", "steady", relaunched)
        record_case("post-quit-relaunch", [relaunched])

        relaunch_helpers = _helper_pids(relaunched)
        if not automation.terminate(relaunched_pid):
            raise RuntimeQualificationError(
                "cleanup_termination_failed", "post-quit-relaunch cleanup"
            )
        if not automation.wait_process_exit(relaunched_pid, timeout_seconds):
            raise RuntimeQualificationError(
                "cleanup_main_process_remained", "post-quit-relaunch cleanup"
            )
        known_main_pids.discard(relaunched_pid)
        final_cleanup_zero = observe(ObservationExpectation(absent=True), "cleanup")
        sample("post-quit-relaunch", "final-cleanup-zero", final_cleanup_zero)
        relaunch_helpers_exited = not relaunch_helpers or automation.wait_processes_exit(
            relaunch_helpers, timeout_seconds
        )
        lifecycle["final_cleanup"] = {
            "main_absent": True,
            "helper_pids": list(relaunch_helpers),
            "helpers_exited": relaunch_helpers_exited,
        }
        if not relaunch_helpers_exited:
            raise RuntimeQualificationError(
                "cleanup_helper_processes_remained", "post-quit-relaunch cleanup"
            )
        known_helper_pids.clear()
        report["status"] = "passed"
    except RuntimeQualificationError as error:
        report["status"] = "blocked" if error.blocked else "failed"
        report["error"] = {
            "code": error.code,
            "operation": error.operation,
            **({"evidence": error.evidence} if error.evidence is not None else {}),
        }
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        report["status"] = "failed"
        report["error"] = {
            "code": "runtime_driver_internal_failure",
            "operation": type(error).__name__,
        }
    finally:
        cleanup_required = bool(known_main_pids or known_repeat_request_pids)
        if automation is not None and cleanup_required:
            for pid in sorted(known_repeat_request_pids):
                try:
                    automation.terminate_repeat_request(pid)
                    automation.wait_process_exit(pid, timeout_seconds)
                except (
                    OSError,
                    subprocess.SubprocessError,
                    RuntimeQualificationError,
                ):
                    pass
            for pid in sorted(known_main_pids):
                try:
                    automation.terminate(pid)
                    automation.wait_process_exit(pid, timeout_seconds)
                except (
                    OSError,
                    subprocess.SubprocessError,
                    RuntimeQualificationError,
                ):
                    pass
            if artifact is not None:
                try:
                    automation.observe_until(
                        artifact.executable,
                        BUNDLE_IDENTIFIER,
                        MAIN_WINDOW_TITLE,
                        ObservationExpectation(absent=True),
                        timeout_seconds,
                    )
                except (OSError, subprocess.SubprocessError, RuntimeQualificationError):
                    pass
            if known_helper_pids:
                try:
                    automation.wait_processes_exit(
                        tuple(sorted(known_helper_pids)), timeout_seconds
                    )
                except (
                    OSError,
                    subprocess.SubprocessError,
                    RuntimeQualificationError,
                ):
                    pass
        runtime_log_summary = _runtime_log_summary(runtime_home)
        report["runtime_log_summary"] = runtime_log_summary
        if report.get("status") == "passed":
            callback_error = (
                "single_instance_runtime_log_missing"
                if runtime_log_summary["available"] is not True
                else (
                    "single_instance_callback_count_mismatch"
                    if runtime_log_summary["single_instance_callback_count"]
                    != EXPECTED_SINGLE_INSTANCE_CALLBACK_COUNT
                    else None
                )
            )
            if callback_error is not None:
                report["status"] = "failed"
                report["error"] = {
                    "code": callback_error,
                    "operation": "LaunchServices repeat callback audit",
                }
                active_case_id = "focus-restored"
        finalize_case_slots()
        shutil.rmtree(runtime_root, ignore_errors=True)

    samples_path.write_text(
        "".join(
            json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
            for entry in samples
        )
    )
    report["samples"] = {
        "path": samples_path.relative_to(evidence_dir).as_posix(),
        "sha256": _sha256_file(samples_path),
    }
    _write_json(report_path, report)
    return report_path


_SWIFT_HELPER_SOURCE = r'''
import AppKit
import ApplicationServices
import CoreImage
import CoreGraphics
import CoreMedia
import CoreVideo
import CryptoKit
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

let checkoutDirectoryPickerIdentifiers: Set<String> = [
    "harness-checkout-directory-picker-v1",
    "open-panel",
]

func emit(_ value: Any) -> Never {
    let body = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    FileHandle.standardOutput.write(body)
    FileHandle.standardOutput.write(Data([0x0a]))
    exit(0)
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(2)
}

func failJSON(_ code: String, _ operation: String) -> Never {
    let payload: [String: Any] = [
        "error": ["code": code, "operation": operation]
    ]
    let body = try! JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    FileHandle.standardError.write(body)
    FileHandle.standardError.write(Data([0x0a]))
    exit(2)
}

func pngData(_ image: CGImage) -> Data? {
    let buffer = NSMutableData()
    guard let encoder = CGImageDestinationCreateWithData(
        buffer,
        UTType.png.identifier as CFString,
        1,
        nil
    ) else { return nil }
    CGImageDestinationAddImage(encoder, image, nil)
    guard CGImageDestinationFinalize(encoder) else { return nil }
    return buffer as Data
}

func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value
}

func boolAttribute(_ element: AXUIElement, _ name: String) -> Bool {
    return (attribute(element, name) as? NSNumber)?.boolValue ?? false
}

func stringAttribute(_ element: AXUIElement, _ name: String) -> String {
    return attribute(element, name) as? String ?? ""
}

func urlAttributeString(_ element: AXUIElement) -> String {
    if let value = attribute(element, kAXURLAttribute as String) as? URL {
        return value.absoluteString
    }
    return stringAttribute(element, kAXURLAttribute as String)
}

func workspaceWebAreaURLClass(_ value: String) -> String? {
    guard let components = URLComponents(string: value),
          let scheme = components.scheme?.lowercased() else { return nil }
    if scheme == "tauri" && components.host?.lowercased() == "localhost" {
        return "tauri-app-local"
    }
    if (scheme == "http" || scheme == "https") &&
       ["localhost", "tauri.localhost"].contains(components.host?.lowercased() ?? "") {
        return "tauri-app-local"
    }
    return nil
}

func runningApplications(_ bundleIdentifier: String) -> [NSRunningApplication] {
    return NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier)
        .filter { !$0.isTerminated }
        .sorted { $0.processIdentifier < $1.processIdentifier }
}

func verifiedApplication(
    _ pid: pid_t,
    _ bundleIdentifier: String
) -> NSRunningApplication? {
    guard let app = NSRunningApplication(processIdentifier: pid),
          !app.isTerminated,
          app.bundleIdentifier == bundleIdentifier else {
        return nil
    }
    return app
}

func applicationRecords(_ bundleIdentifier: String) -> [[String: Any]] {
    return runningApplications(bundleIdentifier).map { app in
        [
            "pid": Int(app.processIdentifier),
            "executable_path": app.executableURL?.path ?? "",
            "hidden": app.isHidden,
            "active": app.isActive,
        ]
    }
}

func cgWindows(_ pid: pid_t, _ expectedTitle: String) -> [[String: Any]] {
    guard let raw = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID)
        as? [[String: Any]] else { return [] }
    return raw.filter { item in
        let owner = item[kCGWindowOwnerPID as String] as? Int ?? -1
        let layer = item[kCGWindowLayer as String] as? Int ?? -1
        let title = item[kCGWindowName as String] as? String ?? ""
        return owner == Int(pid) && layer == 0 && title == expectedTitle
    }
}

func windowRecords(_ pid: pid_t, _ expectedTitle: String) -> [[String: Any]] {
    let application = AXUIElementCreateApplication(pid)
    let focusedWindow = attribute(application, kAXFocusedWindowAttribute)
    let windows = attribute(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
    let cg = cgWindows(pid, expectedTitle)
    return windows.compactMap { window in
        let title = stringAttribute(window, kAXTitleAttribute)
        let role = stringAttribute(window, kAXRoleAttribute)
        let subrole = stringAttribute(window, kAXSubroleAttribute)
        guard title == expectedTitle && role == "AXWindow" && subrole == "AXStandardWindow" else {
            return nil
        }
        let cgWindow = cg.first
        let windowID = cgWindow?[kCGWindowNumber as String] as? Int ?? 0
        let onScreen = cgWindow?[kCGWindowIsOnscreen as String] as? Bool ?? false
        let focused = focusedWindow.map { CFEqual($0, window) } ?? false
        return [
            "window_id": windowID,
            "owner_pid": Int(pid),
            "title": title,
            "role": role,
            "subrole": subrole,
            "on_screen": onScreen,
            "minimized": boolAttribute(window, kAXMinimizedAttribute),
            "focused": focused,
            "main": boolAttribute(window, kAXMainAttribute),
        ]
    }
}

func numberAttribute(_ element: AXUIElement, _ name: String) -> Double? {
    return (attribute(element, name) as? NSNumber)?.doubleValue
}

func axFrame(_ element: AXUIElement) -> CGRect? {
    guard let rawPosition = attribute(element, kAXPositionAttribute),
          let rawSize = attribute(element, kAXSizeAttribute),
          CFGetTypeID(rawPosition) == AXValueGetTypeID(),
          CFGetTypeID(rawSize) == AXValueGetTypeID() else {
        return nil
    }
    var position = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(rawPosition as! AXValue, .cgPoint, &position),
          AXValueGetValue(rawSize as! AXValue, .cgSize, &size) else {
        return nil
    }
    return CGRect(origin: position, size: size)
}

func integerFrame(_ frame: CGRect) -> [String: Int] {
    return [
        "x": Int(frame.origin.x.rounded()),
        "y": Int(frame.origin.y.rounded()),
        "width": max(0, Int(frame.width.rounded())),
        "height": max(0, Int(frame.height.rounded())),
    ]
}

func cgWindowFrame(_ record: [String: Any]) -> CGRect? {
    guard let rawBounds = record[kCGWindowBounds as String] as? NSDictionary,
          let frame = CGRect(
            dictionaryRepresentation: rawBounds as CFDictionary
          ),
          frame.width > 0,
          frame.height > 0 else { return nil }
    return frame
}

func framesMatch(_ left: CGRect, _ right: CGRect, tolerance: CGFloat = 2) -> Bool {
    return abs(left.minX - right.minX) <= tolerance &&
        abs(left.minY - right.minY) <= tolerance &&
        abs(left.width - right.width) <= tolerance &&
        abs(left.height - right.height) <= tolerance
}

func frameContains(_ outer: CGRect, _ inner: CGRect, tolerance: CGFloat = 2) -> Bool {
    return inner.width >= 0 && inner.height >= 0 &&
        inner.minX >= outer.minX - tolerance &&
        inner.minY >= outer.minY - tolerance &&
        inner.maxX <= outer.maxX + tolerance &&
        inner.maxY <= outer.maxY + tolerance
}

func boundedText(_ value: String, limit: Int = 240) -> String {
    let printable = value.unicodeScalars.filter {
        !CharacterSet.controlCharacters.contains($0)
    }
    return String(String.UnicodeScalarView(printable).prefix(limit))
}

func elementIdentifier(_ element: AXUIElement) -> String {
    for name in ["AXDOMIdentifier", "AXIdentifier"] {
        let value = stringAttribute(element, name)
        if !value.isEmpty { return boundedText(value, limit: 1024) }
    }
    return ""
}

func elementName(_ element: AXUIElement) -> String {
    return elementName(element, limit: 240)
}

func elementName(_ element: AXUIElement, limit: Int) -> String {
    for name in [kAXTitleAttribute as String, kAXDescriptionAttribute as String, "AXLabel", kAXHelpAttribute as String] {
        let value = stringAttribute(element, name)
        if !value.isEmpty { return boundedText(value, limit: limit) }
    }
    let role = stringAttribute(element, kAXRoleAttribute)
    if role == "AXStaticText" || role == "AXHeading" {
        return boundedText(stringAttribute(element, kAXValueAttribute), limit: limit)
    }
    return ""
}

func boundedDescendants(_ root: AXUIElement, maximum: Int = 8192) -> [AXUIElement] {
    var pending: [AXUIElement] = [root]
    var result: [AXUIElement] = []
    var cursor = 0
    while cursor < pending.count && result.count < maximum {
        let element = pending[cursor]
        cursor += 1
        result.append(element)
        if let children = attribute(element, kAXChildrenAttribute) as? [AXUIElement] {
            let remaining = maximum - pending.count
            if remaining > 0 { pending.append(contentsOf: children.prefix(remaining)) }
        }
    }
    return result
}

func workspaceMainWindow(
    _ pid: pid_t
) -> (element: AXUIElement, windowID: Int, cgFrame: CGRect)? {
    let application = AXUIElementCreateApplication(pid)
    let windows = attribute(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
    let matches = windows.filter {
        stringAttribute($0, kAXTitleAttribute) == "HarnessKit" &&
        stringAttribute($0, kAXRoleAttribute) == "AXWindow" &&
        stringAttribute($0, kAXSubroleAttribute) == "AXStandardWindow"
    }
    guard matches.count == 1 else { return nil }
    let cg = cgWindows(pid, "HarnessKit")
    guard cg.count == 1,
          let windowID = cg[0][kCGWindowNumber as String] as? Int,
          windowID > 0,
          let frame = cgWindowFrame(cg[0]) else { return nil }
    return (matches[0], windowID, frame)
}

func elementIsFocusable(_ element: AXUIElement) -> Bool {
    var settable = DarwinBoolean(false)
    guard AXUIElementIsAttributeSettable(
        element,
        kAXFocusedAttribute as CFString,
        &settable
    ) == .success else { return false }
    return settable.boolValue
}

func elementIsSelected(_ element: AXUIElement) -> Bool {
    if boolAttribute(element, kAXSelectedAttribute) { return true }
    let role = stringAttribute(element, kAXRoleAttribute)
    if role == "AXButton" || role == "AXRadioButton" || role == "AXCheckBox" {
        if let number = attribute(element, kAXValueAttribute) as? NSNumber {
            return number.boolValue
        }
        return ["1", "true", "selected", "on"].contains(
            stringAttribute(element, kAXValueAttribute).lowercased()
        )
    }
    return false
}

func elementMatchesIdentifier(_ element: AXUIElement, _ identifier: String) -> Bool {
    return elementIdentifier(element) == identifier
}

func elementMatchesLabel(_ element: AXUIElement, _ label: String) -> Bool {
    let name = elementName(element)
    return name == label || name.contains(label)
}

func componentIDFromToken(_ token: String) -> String? {
    let prefix = "component:"
    guard token.hasPrefix(prefix) else { return nil }
    let componentID = String(token.dropFirst(prefix.count))
    let allowed = CharacterSet(
        charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
    )
    guard (1...160).contains(componentID.count),
          componentID.unicodeScalars.allSatisfy({ allowed.contains($0) }) else {
        return nil
    }
    return componentID
}

func graphViewportElements(_ elements: [AXUIElement]) -> [AXUIElement] {
    guard let viewport = elements.first(where: {
        elementMatchesIdentifier($0, "component-map-viewport")
    }) else { return [] }
    return [viewport] + boundedDescendants(viewport, maximum: 2048)
}

func canonicalSemanticToken(
    _ value: String,
    limit: Int = 240
) -> Bool {
    let allowed = CharacterSet(
        charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    )
    return (1...limit).contains(value.count) &&
        value.unicodeScalars.allSatisfy { allowed.contains($0) }
}

func boundedOpaqueSemanticToken(
    _ value: String,
    limit: Int = 240
) -> Bool {
    return (1...limit).contains(value.count) &&
        value.unicodeScalars.allSatisfy {
            !CharacterSet.controlCharacters.contains($0)
        }
}

func decodeURIComponent(
    _ value: String,
    limit: Int = 240
) -> String? {
    guard !value.isEmpty,
          value.count <= 2048,
          let decoded = value.removingPercentEncoding,
          boundedOpaqueSemanticToken(decoded, limit: limit) else { return nil }
    let allowed = CharacterSet(
        charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.!~*'()"
    )
    guard decoded.addingPercentEncoding(withAllowedCharacters: allowed) == value else {
        return nil
    }
    return decoded
}

func semanticIdentityFromIdentifier(
    _ identifier: String
) -> [String: Any]? {
    let relationPrefix = "semantic-relation:"
    if identifier.hasPrefix(relationPrefix) {
        let nodeID = String(identifier.dropFirst(relationPrefix.count))
        let parts = nodeID.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
        guard parts.count == 2 else { return nil }
        let namespace = String(parts[0])
        let canonicalID = String(parts[1])
        let relationKind: String
        if ["profile", "workflow"].contains(namespace) &&
           canonicalSemanticToken(canonicalID, limit: 200) {
            relationKind = namespace
        } else if namespace == "unprofiled" &&
                  ["projection:unprofiled", "__unprofiled__"].contains(canonicalID) {
            relationKind = "unprofiled"
        } else {
            return nil
        }
        var identity: [String: Any] = [
            "semantic_type": "relation",
            "node_id": nodeID,
            "relation_kind": relationKind,
            "canonical_id": canonicalID,
        ]
        if relationKind == "workflow" {
            identity["workflow_id"] = canonicalID
        } else if relationKind == "profile" {
            identity["profile_id"] = canonicalID
        }
        return identity
    }

    let componentPrefix = "semantic-component:"
    if identifier.hasPrefix(componentPrefix) {
        let componentID = String(identifier.dropFirst(componentPrefix.count))
        guard canonicalSemanticToken(componentID, limit: 200) else { return nil }
        return [
            "semantic_type": "component",
            "node_id": "component:\(componentID)",
            "component_id": componentID,
        ]
    }

    let workflowStepPrefix = "semantic-workflow-step:"
    if identifier.hasPrefix(workflowStepPrefix) {
        let value = String(identifier.dropFirst(workflowStepPrefix.count))
        let parts = value.split(separator: ":", maxSplits: 2, omittingEmptySubsequences: false)
        guard parts.count == 3,
              canonicalSemanticToken(String(parts[0]), limit: 200),
              let ordinal = Int(parts[1]),
              (1...10_000).contains(ordinal),
              let stepID = decodeURIComponent(String(parts[2]), limit: 200) else {
            return nil
        }
        let workflowID = String(parts[0])
        return [
            "semantic_type": "workflow_step",
            "node_id": "workflow:\(workflowID)",
            "workflow_id": workflowID,
            "ordinal": ordinal,
            "step_id": stepID,
        ]
    }
    return nil
}

func semanticAXRecord(
    _ element: AXUIElement,
    identifier: String
) -> [String: Any]? {
    guard let frame = axFrame(element) else { return nil }
    return [
        "dom_identifier": identifier,
        "role": boundedText(stringAttribute(element, kAXRoleAttribute), limit: 80),
        "name": elementName(element),
        "selected": elementIsSelected(element),
        "focused": boolAttribute(element, kAXFocusedAttribute),
        "visible": frame.width > 0 && frame.height > 0 && !boolAttribute(element, "AXHidden"),
        "frame": integerFrame(frame),
    ]
}

func relationExactCount(
    _ groupName: String,
    relationKind: String
) -> Int? {
    let labelKind: String
    let unit: String
    if relationKind == "workflow" {
        labelKind = "Workflow"
        unit = "authored steps?"
    } else if relationKind == "profile" {
        labelKind = "Profile"
        unit = "components?"
    } else if relationKind == "unprofiled" {
        labelKind = "Unprofiled"
        unit = "components?"
    } else {
        return nil
    }
    let pattern = "^\(labelKind) relation .+, ([0-9]+) \(unit)$"
    guard let expression = try? NSRegularExpression(
            pattern: pattern,
            options: [.caseInsensitive]
          ),
          let match = expression.firstMatch(
            in: groupName,
            range: NSRange(groupName.startIndex..<groupName.endIndex, in: groupName)
          ),
          match.numberOfRanges == 2,
          let countRange = Range(match.range(at: 1), in: groupName),
          let count = Int(groupName[countRange]),
          (0...10_000).contains(count) else { return nil }
    return count
}

func relationAuthorityRecord(
    _ relation: [String: Any],
    _ semanticElements: [AXUIElement]
) -> [String: Any]? {
    guard let nodeID = relation["node_id"] as? String,
          let relationKind = relation["relation_kind"] as? String,
          let buttonIdentifier = relation["dom_identifier"] as? String else {
        return nil
    }
    let groupIdentifier = "semantic-relation-group:\(nodeID)"
    let membersIdentifier = "semantic-relation-members:\(nodeID)"
    let groupMatches = semanticElements.filter {
        elementIdentifier($0) == groupIdentifier
    }
    let membersMatches = semanticElements.filter {
        elementIdentifier($0) == membersIdentifier
    }
    guard groupMatches.count == 1,
          membersMatches.count == 1 else { return nil }
    let groupElements = boundedDescendants(groupMatches[0], maximum: 2048)
    guard groupElements.contains(where: {
        elementIdentifier($0) == buttonIdentifier
    }), groupElements.contains(where: {
        elementIdentifier($0) == membersIdentifier
    }), let exactCount = relationExactCount(
        elementName(groupMatches[0]),
        relationKind: relationKind
    ) else { return nil }

    let memberPrefix = "semantic-relation-member:\(nodeID):"
    let memberElements = boundedDescendants(membersMatches[0], maximum: 1024)
        .filter { elementIdentifier($0).hasPrefix(memberPrefix) }
    guard memberElements.count <= 512 else { return nil }
    var memberIDs = Set<String>()
    for element in memberElements {
        let identifier = elementIdentifier(element)
        let componentID = String(identifier.dropFirst(memberPrefix.count))
        guard canonicalSemanticToken(componentID, limit: 200),
              memberIDs.insert(componentID).inserted else { return nil }
    }
    if relationKind != "workflow" && exactCount != memberIDs.count {
        return nil
    }
    var result = relation
    result["exact_count"] = exactCount
    result["member_component_ids"] = memberIDs.sorted()
    return result
}

func relationAuthorityRecords(
    _ relationRecords: [[String: Any]],
    _ semanticElements: [AXUIElement]
) -> [[String: Any]]? {
    var relations: [[String: Any]] = []
    guard relationRecords.count <= 24 else { return nil }
    for relation in relationRecords {
        guard let record = relationAuthorityRecord(relation, semanticElements) else {
            return nil
        }
        relations.append(record)
    }
    return relations
}

func workflowInspectorTokenIdentity(
    _ identifier: String
) -> [String: Any]? {
    let inspectorPrefix = "workflow-inspector:"
    if identifier.hasPrefix(inspectorPrefix) {
        let workflowID = String(identifier.dropFirst(inspectorPrefix.count))
        guard workflowID == "none" || canonicalSemanticToken(workflowID, limit: 200) else {
            return nil
        }
        return [
            "inspector_type": "root",
            "workflow_id": workflowID,
        ]
    }
    let overviewPrefix = "workflow-overview:"
    if identifier.hasPrefix(overviewPrefix) {
        let workflowID = String(identifier.dropFirst(overviewPrefix.count))
        guard canonicalSemanticToken(workflowID, limit: 200) else { return nil }
        return [
            "inspector_type": "overview",
            "workflow_id": workflowID,
        ]
    }
    let stepPrefix = "workflow-inspector-step:"
    if identifier.hasPrefix(stepPrefix) {
        let value = String(identifier.dropFirst(stepPrefix.count))
        let parts = value.split(separator: ":", maxSplits: 2, omittingEmptySubsequences: false)
        guard parts.count == 3,
              canonicalSemanticToken(String(parts[0]), limit: 200),
              let ordinal = Int(parts[1]),
              (1...10_000).contains(ordinal),
              let stepID = decodeURIComponent(String(parts[2]), limit: 200) else { return nil }
        return [
            "inspector_type": "step",
            "workflow_id": String(parts[0]),
            "ordinal": ordinal,
            "step_id": stepID,
        ]
    }
    return nil
}

func workflowRoleIdentityFromIdentifier(
    _ identifier: String
) -> [String: Any]? {
    let prefix = "semantic-workflow-role:"
    guard identifier.hasPrefix(prefix) else { return nil }
    let value = String(identifier.dropFirst(prefix.count))
    let parts = value.split(separator: ":", maxSplits: 2, omittingEmptySubsequences: false)
    guard parts.count == 3,
          canonicalSemanticToken(String(parts[0]), limit: 200),
          let ordinal = Int(parts[1]),
          (1...10_000).contains(ordinal) else { return nil }
    let roleParts = String(parts[2]).split(
        separator: "|",
        maxSplits: 3,
        omittingEmptySubsequences: false
    )
    guard roleParts.count == 4,
          canonicalSemanticToken(String(roleParts[0]), limit: 200),
          let stepID = decodeURIComponent(String(roleParts[1]), limit: 200),
          let sourceField = decodeURIComponent(String(roleParts[2]), limit: 200),
          let role = decodeURIComponent(String(roleParts[3]), limit: 200) else { return nil }
    return [
        "workflow_id": String(parts[0]),
        "ordinal": ordinal,
        "component_id": String(roleParts[0]),
        "step_id": stepID,
        "source_field": sourceField,
        "role": role,
    ]
}

func workflowOccurrenceIdentityFromIdentifier(
    _ identifier: String
) -> [String: Any]? {
    let prefix = "workflow-occurrence:"
    guard identifier.hasPrefix(prefix) else { return nil }
    let value = String(identifier.dropFirst(prefix.count))
    let parts = value.split(separator: ":", maxSplits: 2, omittingEmptySubsequences: false)
    guard parts.count == 3,
          canonicalSemanticToken(String(parts[0]), limit: 200),
          let ordinal = Int(parts[1]),
          (1...10_000).contains(ordinal) else { return nil }
    let occurrenceParts = String(parts[2]).split(
        separator: "|",
        maxSplits: 2,
        omittingEmptySubsequences: false
    )
    guard occurrenceParts.count == 3,
          canonicalSemanticToken(String(occurrenceParts[0]), limit: 200),
          let stepID = decodeURIComponent(String(occurrenceParts[1]), limit: 200),
          let sourceField = decodeURIComponent(String(occurrenceParts[2]), limit: 200) else {
        return nil
    }
    return [
        "workflow_id": String(parts[0]),
        "ordinal": ordinal,
        "component_id": String(occurrenceParts[0]),
        "step_id": stepID,
        "source_field": sourceField,
    ]
}

func workflowWarningIdentityFromIdentifier(
    _ identifier: String
) -> [String: Any]? {
    let prefix = "workflow-warning:"
    guard identifier.hasPrefix(prefix) else { return nil }
    let value = String(identifier.dropFirst(prefix.count))
    let parts = value.split(separator: ":", maxSplits: 2, omittingEmptySubsequences: false)
    guard parts.count == 3,
          canonicalSemanticToken(String(parts[0]), limit: 200),
          let ordinal = Int(parts[1]),
          (1...10_000).contains(ordinal) else { return nil }
    let warningParts = String(parts[2]).split(
        separator: "|",
        maxSplits: 2,
        omittingEmptySubsequences: false
    )
    guard warningParts.count == 3,
          let referenceKind = decodeURIComponent(String(warningParts[0]), limit: 120),
          let sourceField = decodeURIComponent(String(warningParts[1]), limit: 200),
          let reference = decodeURIComponent(String(warningParts[2]), limit: 240) else {
        return nil
    }
    return [
        "workflow_id": String(parts[0]),
        "ordinal": ordinal,
        "reference_kind": referenceKind,
        "source_field": sourceField,
        "reference": reference,
    ]
}

func workflowInspectorObservation(
    _ elements: [AXUIElement]
) -> [String: Any]? {
    let emptyObservation: [String: Any] = [
        "visible": false,
        "workflow_id": NSNull(),
        "runtime_state": NSNull(),
        "steps": [],
        "ordinal_occurrences": [],
        "component_roles": [],
        "unresolved_warning_set": [],
    ]
    let inspectorMatches = elements.filter { element in
        guard let identity = workflowInspectorTokenIdentity(elementIdentifier(element)) else {
            return false
        }
        return identity["inspector_type"] as? String == "root"
    }
    if inspectorMatches.isEmpty { return emptyObservation }
    guard inspectorMatches.count == 1 else { return nil }
    let inspector = inspectorMatches[0]
    let inspectorIdentifier = elementIdentifier(inspector)
    guard let inspectorIdentity = workflowInspectorTokenIdentity(inspectorIdentifier),
          let workflowID = inspectorIdentity["workflow_id"] as? String else { return nil }
    if workflowID == "none" { return emptyObservation }
    guard let inspectorRecord = semanticAXRecord(
            inspector,
            identifier: inspectorIdentifier
          ),
          inspectorRecord["frame"] != nil else { return nil }
    guard inspectorRecord["visible"] as? Bool == true else { return emptyObservation }
    let inspectorElements = boundedDescendants(inspector, maximum: 2048)
    let overviewIdentifier = "workflow-overview:\(workflowID)"
    let overviewMatches = inspectorElements.filter {
        elementIdentifier($0) == overviewIdentifier
    }
    guard overviewMatches.count == 1,
          let overviewRecord = semanticAXRecord(
            overviewMatches[0],
            identifier: overviewIdentifier
          ) else { return nil }

    var seenStepIdentifiers = Set<String>()
    var seenOrdinals = Set<Int>()
    var steps: [[String: Any]] = []
    for element in inspectorElements {
        let identifier = elementIdentifier(element)
        guard identifier.hasPrefix("workflow-inspector-step:") else { continue }
        guard seenStepIdentifiers.insert(identifier).inserted else { return nil }
        guard let identity = workflowInspectorTokenIdentity(identifier),
              identity["inspector_type"] as? String == "step",
              identity["workflow_id"] as? String == workflowID,
              let ordinal = identity["ordinal"] as? Int,
              seenOrdinals.insert(ordinal).inserted,
              let record = semanticAXRecord(element, identifier: identifier),
              let frame = record["frame"] else {
            return nil
        }
        steps.append([
            "dom_identifier": identifier,
            "role": record["role"] as? String ?? "",
            "name": record["name"] as? String ?? "",
            "selected": record["selected"] as? Bool ?? false,
            "focused": record["focused"] as? Bool ?? false,
            "visible": record["visible"] as? Bool ?? false,
            "frame": frame,
            "workflow_id": workflowID,
            "ordinal": ordinal,
        ])
    }
    guard !steps.isEmpty, steps.count <= 512 else { return nil }
    steps.sort {
        ($0["ordinal"] as? Int ?? Int.max) < ($1["ordinal"] as? Int ?? Int.max)
    }
    let authoredOrdinals = Set(steps.compactMap { $0["ordinal"] as? Int })
    guard authoredOrdinals.count == steps.count else { return nil }

    let runtimeStateMatches = inspectorElements.filter { element in
        let role = stringAttribute(element, kAXRoleAttribute)
        let name = elementName(element)
        return role == "AXStaticText" &&
            name.contains("authored definition") && name.contains("runtime")
    }
    guard runtimeStateMatches.count == 1 else { return nil }
    let runtimeState = elementName(runtimeStateMatches[0])

    var seenOccurrenceIdentifiers = Set<String>()
    var occurrenceIdentities: [[String: Any]] = []
    for element in inspectorElements {
        let identifier = elementIdentifier(element)
        guard identifier.hasPrefix("workflow-occurrence:") else { continue }
        guard seenOccurrenceIdentifiers.insert(identifier).inserted,
              let identity = workflowOccurrenceIdentityFromIdentifier(identifier),
              identity["workflow_id"] as? String == workflowID,
              let ordinal = identity["ordinal"] as? Int,
              authoredOrdinals.contains(ordinal),
              let record = semanticAXRecord(element, identifier: identifier),
              record["visible"] as? Bool == true else { return nil }
        occurrenceIdentities.append(identity)
    }
    guard occurrenceIdentities.count <= 1024 else { return nil }
    let occurrenceTuples = Set(occurrenceIdentities.compactMap { occurrence -> String? in
        guard let ordinal = occurrence["ordinal"] as? Int,
              let componentID = occurrence["component_id"] as? String else { return nil }
        return "\(workflowID)|\(ordinal)|\(componentID)"
    })
    var occurrenceKeys = Set<String>()
    var ordinalOccurrences: [[String: Any]] = []
    for occurrence in occurrenceIdentities {
        guard let ordinal = occurrence["ordinal"] as? Int,
              let componentID = occurrence["component_id"] as? String else {
            return nil
        }
        let key = "\(workflowID)|\(ordinal)|\(componentID)"
        if occurrenceKeys.insert(key).inserted {
            ordinalOccurrences.append([
                "workflow_id": workflowID,
                "ordinal": ordinal,
                "component_id": componentID,
            ])
        }
    }
    ordinalOccurrences.sort {
        let leftOrdinal = $0["ordinal"] as? Int ?? Int.max
        let rightOrdinal = $1["ordinal"] as? Int ?? Int.max
        if leftOrdinal != rightOrdinal { return leftOrdinal < rightOrdinal }
        return String(describing: $0["component_id"] ?? "") <
            String(describing: $1["component_id"] ?? "")
    }

    var seenRoleIdentifiers = Set<String>()
    var componentRoleKeys = Set<String>()
    var componentRoles: [[String: Any]] = []
    for element in inspectorElements {
        let identifier = elementIdentifier(element)
        guard identifier.hasPrefix("semantic-workflow-role:") else { continue }
        guard seenRoleIdentifiers.insert(identifier).inserted,
              let identity = workflowRoleIdentityFromIdentifier(identifier),
              identity["workflow_id"] as? String == workflowID,
              let ordinal = identity["ordinal"] as? Int,
              let componentID = identity["component_id"] as? String,
              let role = identity["role"] as? String,
              let record = semanticAXRecord(element, identifier: identifier),
              record["visible"] as? Bool == true else { return nil }
        let roleTuple = "\(workflowID)|\(ordinal)|\(componentID)"
        guard occurrenceTuples.contains(roleTuple) else { return nil }
        let key = "\(componentID)|\(role)"
        if componentRoleKeys.insert(key).inserted {
            componentRoles.append([
                "component_id": componentID,
                "role": role,
            ])
        }
    }
    guard componentRoles.count <= 512 else { return nil }
    componentRoles.sort {
        let left = "\(String(describing: $0["component_id"] ?? ""))|\(String(describing: $0["role"] ?? ""))"
        let right = "\(String(describing: $1["component_id"] ?? ""))|\(String(describing: $1["role"] ?? ""))"
        return left < right
    }

    var seenWarningIdentifiers = Set<String>()
    var warningKeys = Set<String>()
    var unresolvedWarningSet: [[String: Any]] = []
    for element in inspectorElements {
        let identifier = elementIdentifier(element)
        guard identifier.hasPrefix("workflow-warning:") else { continue }
        guard seenWarningIdentifiers.insert(identifier).inserted,
              let identity = workflowWarningIdentityFromIdentifier(identifier),
              identity["workflow_id"] as? String == workflowID,
              let ordinal = identity["ordinal"] as? Int,
              authoredOrdinals.contains(ordinal),
              let referenceKind = identity["reference_kind"] as? String,
              let sourceField = identity["source_field"] as? String,
              let reference = identity["reference"] as? String,
              let record = semanticAXRecord(element, identifier: identifier),
              record["visible"] as? Bool == true else { return nil }
        let key = "\(ordinal)|\(referenceKind)|\(sourceField)|\(reference)"
        if warningKeys.insert(key).inserted {
            unresolvedWarningSet.append([
                "workflow_id": workflowID,
                "ordinal": ordinal,
                "reference_kind": referenceKind,
                "source_field": sourceField,
                "reference": reference,
            ])
        }
    }
    guard unresolvedWarningSet.count <= 512 else { return nil }
    unresolvedWarningSet.sort {
        let left = "\(String(describing: $0["ordinal"] ?? ""))|\(String(describing: $0["reference_kind"] ?? ""))|\(String(describing: $0["source_field"] ?? ""))|\(String(describing: $0["reference"] ?? ""))"
        let right = "\(String(describing: $1["ordinal"] ?? ""))|\(String(describing: $1["reference_kind"] ?? ""))|\(String(describing: $1["source_field"] ?? ""))|\(String(describing: $1["reference"] ?? ""))"
        return left < right
    }

    return [
        "workflow_id": workflowID,
        "visible": inspectorRecord["visible"] as? Bool ?? false,
        "runtime_state": boundedText(runtimeState, limit: 160),
        "steps": steps,
        "ordinal_occurrences": ordinalOccurrences,
        "component_roles": componentRoles,
        "unresolved_warning_set": unresolvedWarningSet,
    ]
}

func cameraObservation(
    _ elements: [AXUIElement]
) -> [String: Any]? {
    let cameraRoots = elements.filter {
        elementIdentifier($0) == "component-map-camera-controls"
    }
    guard cameraRoots.count == 1 else { return nil }
    let cameraElements = boundedDescendants(cameraRoots[0], maximum: 64)

    func exactButton(_ name: String) -> AXUIElement? {
        let matches = cameraElements.filter {
            stringAttribute($0, kAXRoleAttribute) == "AXButton" &&
                elementName($0) == name
        }
        guard matches.count == 1 else { return nil }
        return matches[0]
    }
    guard let zoomOut = exactButton("축소"),
          let fit = exactButton("전체 보기"),
          let zoomIn = exactButton("확대") else { return nil }

    var observation: [String: Any] = [
        "ready": false,
        "scale": NSNull(),
        "level": NSNull(),
        "zoom_out_enabled": boolAttribute(zoomOut, kAXEnabledAttribute),
        "fit_enabled": boolAttribute(fit, kAXEnabledAttribute),
        "zoom_in_enabled": boolAttribute(zoomIn, kAXEnabledAttribute),
    ]
    let scaleMatches = cameraElements.filter {
        elementIdentifier($0) == "component-map-camera-scale"
    }
    guard scaleMatches.count <= 1 else { return nil }
    guard let scaleElement = scaleMatches.first else { return observation }
    let displayCandidates = [
        stringAttribute(scaleElement, kAXValueAttribute),
        elementName(scaleElement),
        stringAttribute(scaleElement, kAXDescriptionAttribute),
    ]
    let display = displayCandidates.first(where: { !$0.isEmpty }) ?? ""
    let pendingDisplays = Set(["3D · 준비", "3D · 준비됨", "3D · 목록"])
    if display.isEmpty || pendingDisplays.contains(display) { return observation }

    let pattern = "^([0-9]+(?:\\.[0-9]+)?)× · (개요|상세|최대)$"
    guard let expression = try? NSRegularExpression(pattern: pattern),
          let match = expression.firstMatch(
            in: display,
            range: NSRange(display.startIndex..<display.endIndex, in: display)
          ),
          match.numberOfRanges == 3,
          match.range == NSRange(display.startIndex..<display.endIndex, in: display),
          let scaleRange = Range(match.range(at: 1), in: display),
          let levelRange = Range(match.range(at: 2), in: display),
          let scale = Double(display[scaleRange]),
          scale >= 0.75 && scale <= 8.0 else { return nil }
    observation["ready"] = true
    observation["scale"] = scale
    observation["level"] = String(display[levelRange])
    return observation
}

let semanticFitReportTextLimit = 1024

func semanticFitReportFromText(_ text: String) -> [String: Any]? {
    let segments = text.components(separatedBy: "; ")
    guard segments.count == 11,
          segments[0] == "Component Map Fit report" else { return nil }

    func value(_ index: Int, prefix: String) -> String? {
        let segment = segments[index]
        guard segment.hasPrefix(prefix) else { return nil }
        return String(segment.dropFirst(prefix.count))
    }

    func count(_ index: Int, prefix: String) -> Int? {
        guard let text = value(index, prefix: prefix),
              let parsed = Int(text),
              (0...10_000).contains(parsed) else { return nil }
        return parsed
    }

    func vector(_ index: Int, prefix: String) -> [String: Double]? {
        guard let text = value(index, prefix: prefix) else { return nil }
        let values = text.split(
            separator: ",",
            maxSplits: 2,
            omittingEmptySubsequences: false
        ).compactMap { Double($0) }
        guard values.count == 3, values.allSatisfy({ $0.isFinite }) else {
            return nil
        }
        return ["x": values[0], "y": values[1], "z": values[2]]
    }

    guard let identityCount = count(1, prefix: "identity count="),
          let relationCount = count(2, prefix: "relation count="),
          let componentCount = count(3, prefix: "component count="),
          let envelopeCount = count(4, prefix: "envelope count="),
          let cameraStatus = value(5, prefix: "camera status="),
          ["complete", "cropped"].contains(cameraStatus),
          let inFrustumCount = count(6, prefix: "in-frustum="),
          let totalEnvelopeCount = count(7, prefix: "total envelopes="),
          let occupancyText = value(8, prefix: "largest dimension occupancy="),
          let largestDimensionOccupancy = Double(occupancyText),
          largestDimensionOccupancy.isFinite,
          (0...1).contains(largestDimensionOccupancy),
          let minimum = vector(9, prefix: "bounds min="),
          let maximum = vector(10, prefix: "bounds max=") else { return nil }
    return [
        "identity_count": identityCount,
        "relation_count": relationCount,
        "component_count": componentCount,
        "envelope_count": envelopeCount,
        "camera_status": cameraStatus,
        "in_frustum_envelope_count": inFrustumCount,
        "total_envelope_count": totalEnvelopeCount,
        "largest_dimension_occupancy": largestDimensionOccupancy,
        "scene_bounds": ["min": minimum, "max": maximum],
    ]
}

func semanticFitReportObservation(
    _ element: AXUIElement
) -> [String: Any]? {
    let identifier = "component-map-fit-report"
    guard let record = semanticAXRecord(element, identifier: identifier),
          record["dom_identifier"] as? String == identifier else { return nil }
    return semanticFitReportFromText(
        elementName(element, limit: semanticFitReportTextLimit)
    )
}

func semanticCameraPoseFromText(_ text: String) -> [String: Any]? {
    let segments = text.components(separatedBy: "; ")
    guard segments.count == 3, segments[0] == "Camera pose" else { return nil }

    func vector(_ index: Int, prefix: String) -> [String: Double]? {
        guard segments[index].hasPrefix(prefix) else { return nil }
        let values = segments[index].dropFirst(prefix.count).split(
            separator: ",",
            maxSplits: 2,
            omittingEmptySubsequences: false
        ).compactMap { Double($0) }
        guard values.count == 3, values.allSatisfy({ $0.isFinite }) else {
            return nil
        }
        return ["x": values[0], "y": values[1], "z": values[2]]
    }

    guard let position = vector(1, prefix: "position="),
          let target = vector(2, prefix: "target=") else { return nil }
    return ["position": position, "target": target]
}

func semanticCameraPoseObservation(
    _ element: AXUIElement
) -> [String: Any]? {
    let identifier = "component-map-camera-pose"
    guard let record = semanticAXRecord(element, identifier: identifier),
          record["dom_identifier"] as? String == identifier else { return nil }
    return semanticCameraPoseFromText(elementName(element))
}

func semanticActiveProfileFromIdentifier(_ identifier: String) -> String? {
    let prefix = "component-map-active-profile:"
    guard identifier.hasPrefix(prefix),
          let activeProfileID = decodeURIComponent(
              String(identifier.dropFirst(prefix.count)),
              limit: 200
          ) else { return nil }
    guard activeProfileID == "__unprofiled__" || (
        activeProfileID.hasPrefix("harnesskit.profile.") &&
        canonicalSemanticToken(activeProfileID, limit: 200)
    ) else { return nil }
    return activeProfileID
}

func semanticActiveProfileObservation(_ element: AXUIElement) -> String? {
    let identifier = elementIdentifier(element)
    guard identifier.hasPrefix("component-map-active-profile:"),
          let record = semanticAXRecord(element, identifier: identifier),
          record["dom_identifier"] as? String == identifier else { return nil }
    return semanticActiveProfileFromIdentifier(identifier)
}

func semanticSceneLayoutIdentityObservation(
    _ elements: [AXUIElement]
) -> [String: String]? {
    let projectionPrefix = "component-map-projection:"
    let settledHashPrefix = "component-map-settled-hash:"
    let projectionMatches = elements.filter {
        elementIdentifier($0).hasPrefix(projectionPrefix)
    }
    let settledHashMatches = elements.filter {
        elementIdentifier($0).hasPrefix(settledHashPrefix)
    }
    guard projectionMatches.count == 1,
          settledHashMatches.count == 1 else { return nil }

    let projectionElement = projectionMatches[0]
    let settledHashElement = settledHashMatches[0]
    let projectionIdentifier = elementIdentifier(projectionElement)
    let settledHashIdentifier = elementIdentifier(settledHashElement)
    guard semanticAXRecord(
        projectionElement,
        identifier: projectionIdentifier
    )?["dom_identifier"] as? String == projectionIdentifier,
    semanticAXRecord(
        settledHashElement,
        identifier: settledHashIdentifier
    )?["dom_identifier"] as? String == settledHashIdentifier,
    let projectionID = decodeURIComponent(
        String(projectionIdentifier.dropFirst(projectionPrefix.count)),
        limit: 200
    ) else { return nil }

    let projectionCharacters = CharacterSet(
        charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
    )
    let settledHash = String(
        settledHashIdentifier.dropFirst(settledHashPrefix.count)
    )
    let settledHashCharacters = CharacterSet(charactersIn: "0123456789abcdef")
    guard (1...200).contains(projectionID.count),
          projectionID.unicodeScalars.allSatisfy({
            projectionCharacters.contains($0)
          }),
          settledHash.count == 16,
          settledHash.unicodeScalars.allSatisfy({
            settledHashCharacters.contains($0)
          }) else { return nil }
    return [
        "projection_id": projectionID,
        "settled_node_positions_hash": settledHash,
    ]
}

func semanticGraphObservation(
    _ elements: [AXUIElement]
) -> [String: Any]? {
    let rootMatches = elements.filter {
        elementMatchesIdentifier($0, "component-map-semantic-view")
    }
    guard rootMatches.count == 1 else { return nil }
    let semanticRoot = rootMatches[0]
    let semanticElements = boundedDescendants(semanticRoot, maximum: 4096)
    var seenIdentifiers = Set<String>()
    var relations: [[String: Any]] = []
    var components: [[String: Any]] = []
    var workflowSteps: [[String: Any]] = []
    let prefixes = [
        "semantic-relation:",
        "semantic-component:",
        "semantic-workflow-step:",
    ]
    for element in semanticElements {
        let identifier = elementIdentifier(element)
        guard prefixes.contains(where: { identifier.hasPrefix($0) }) else { continue }
        guard seenIdentifiers.insert(identifier).inserted else { return nil }
        guard let identity = semanticIdentityFromIdentifier(identifier),
              var record = semanticAXRecord(element, identifier: identifier),
              let semanticType = identity["semantic_type"] as? String else { return nil }
        for (key, value) in identity where key != "semantic_type" {
            record[key] = value
        }
        if semanticType == "relation" {
            relations.append(record)
        } else if semanticType == "component" {
            components.append(record)
        } else if semanticType == "workflow_step" {
            workflowSteps.append(record)
        } else {
            return nil
        }
    }
    guard !relations.isEmpty, !components.isEmpty,
          let authorityRelations = relationAuthorityRecords(
            relations,
            semanticElements
          ) else { return nil }
    relations = authorityRelations
    func stableOrder(_ left: [String: Any], _ right: [String: Any]) -> Bool {
        return String(describing: left["dom_identifier"] ?? "") <
            String(describing: right["dom_identifier"] ?? "")
    }
    relations.sort(by: stableOrder)
    components.sort(by: stableOrder)
    workflowSteps.sort(by: stableOrder)

    func stableElementMatches(_ identifier: String) -> [AXUIElement] {
        return elements.filter { elementIdentifier($0) == identifier }
    }
    let overlayMatches = stableElementMatches("component-map-identity-overlay")
    let titleMatches = stableElementMatches("component-map-identity-title")
    let kindMatches = stableElementMatches("component-map-identity-kind")
    let countMatches = stableElementMatches("component-map-identity-count")
    let rendererStatusMatches = stableElementMatches("component-map-renderer-status")
    let retryMatches = stableElementMatches("component-map-renderer-retry")
    let fitReportMatches = stableElementMatches("component-map-fit-report")
    let cameraPoseMatches = stableElementMatches("component-map-camera-pose")
    let activeProfileMatches = elements.filter {
        elementIdentifier($0).hasPrefix("component-map-active-profile:")
    }
    guard overlayMatches.count <= 1,
          titleMatches.count <= 1,
          kindMatches.count <= 1,
          countMatches.count <= 1,
          rendererStatusMatches.count <= 1,
          retryMatches.count <= 1,
          fitReportMatches.count <= 1,
          cameraPoseMatches.count <= 1,
          activeProfileMatches.count == 1 else { return nil }
    let overlayRecord = overlayMatches.first.flatMap {
        semanticAXRecord($0, identifier: "component-map-identity-overlay")
    }
    let titleRecord = titleMatches.first.flatMap {
        semanticAXRecord($0, identifier: "component-map-identity-title")
    }
    let kindRecord = kindMatches.first.flatMap {
        semanticAXRecord($0, identifier: "component-map-identity-kind")
    }
    let countRecord = countMatches.first.flatMap {
        semanticAXRecord($0, identifier: "component-map-identity-count")
    }
    let rendererStatusRecord = rendererStatusMatches.first.flatMap {
        semanticAXRecord($0, identifier: "component-map-renderer-status")
    }
    let retryRecord = retryMatches.first.flatMap {
        semanticAXRecord($0, identifier: "component-map-renderer-retry")
    }
    let fitReport = fitReportMatches.first.flatMap(semanticFitReportObservation)
    if !fitReportMatches.isEmpty && fitReport == nil { return nil }
    let cameraPose = cameraPoseMatches.first.flatMap(
        semanticCameraPoseObservation
    )
    if !cameraPoseMatches.isEmpty && cameraPose == nil { return nil }
    guard let activeProfile = activeProfileMatches.first.flatMap(
        semanticActiveProfileObservation
    ), let sceneLayoutIdentity = semanticSceneLayoutIdentityObservation(
        semanticElements
    ) else { return nil }
    let semanticRootFrame = axFrame(semanticRoot) ?? .zero
    guard var camera = cameraObservation(elements) else { return nil }
    if let cameraPose,
       let position = cameraPose["position"] as? [String: Double],
       let target = cameraPose["target"] as? [String: Double] {
        camera["position"] = position
        camera["target"] = target
    }
    let cameraReady = camera["ready"] as? Bool ?? false
    let semanticPrimaryVisible = !cameraReady &&
        !boolAttribute(semanticRoot, "AXHidden") &&
        semanticRootFrame.width > 1 && semanticRootFrame.height > 1
    let rendererStatusName = rendererStatusRecord?["name"] as? String ?? ""
    let retryVisible = retryRecord?["visible"] as? Bool ?? false
    let availability: String
    if cameraReady {
        availability = "ready"
    } else if retryVisible &&
        rendererStatusName == "사용자가 텍스트 보기를 선택했습니다." {
        availability = "manual_fallback"
    } else {
        availability = "unavailable"
    }
    var renderer: [String: Any] = [
        "availability": availability,
        "semantic_primary_visible": semanticPrimaryVisible,
        "semantic_view_frame": integerFrame(semanticRootFrame),
        "status_present": rendererStatusRecord != nil,
        "status_visible": rendererStatusRecord?["visible"] as? Bool ?? false,
        "retry_present": retryRecord != nil,
        "retry_visible": retryVisible,
    ]
    if let rendererStatusRecord { renderer["status"] = rendererStatusRecord }
    if let retryRecord { renderer["retry"] = retryRecord }
    var identityOverlay: [String: Any] = [
        "container_present": overlayRecord != nil,
        "title_present": titleRecord != nil,
        "kind_present": kindRecord != nil,
        "count_present": countRecord != nil,
    ]
    if let overlayRecord { identityOverlay["container"] = overlayRecord }
    if let titleRecord { identityOverlay["title"] = titleRecord }
    if let kindRecord { identityOverlay["kind"] = kindRecord }
    if let countRecord { identityOverlay["count"] = countRecord }
    var result: [String: Any] = [
        "relations": relations,
        "components": components,
        "workflow_steps": workflowSteps,
        "identity_overlay": identityOverlay,
        "renderer": renderer,
    ]
    result["active_profile_id"] = activeProfile
    result["projection_id"] = sceneLayoutIdentity["projection_id"]
    result["settled_node_positions_hash"] = sceneLayoutIdentity[
        "settled_node_positions_hash"
    ]
    result["camera"] = camera
    if let fitReport { result["fit_report"] = fitReport }
    guard let workflowInspector = workflowInspectorObservation(elements) else {
        return nil
    }
    result["workflow_inspector"] = workflowInspector
    return result
}

func elementForToken(
    _ token: String,
    _ elements: [AXUIElement]
) -> AXUIElement? {
    let identifiers: [String: String] = [
        "left-pane": "workspace-left-pane",
        "center-pane": "workbench",
        "first-viewport": "sot-workbench-first-viewport",
        "right-pane": "workspace-right-pane",
        "preferred-widths": "workspace-preferred-widths",
        "selected-component": "selected-component-id",
        "graph-body": "component-map-body",
        "graph-viewport": "component-map-viewport",
        "graph-tooltip": "component-map-node-tooltip",
        "graph-toolbar": "component-map-toolbar",
        "graph-legend": "component-map-legend",
        "graph-camera": "component-map-camera-controls",
        "semantic-graph": "component-map-semantic-view",
        "renderer-retry": "component-map-renderer-retry",
        "matrix": "profile-matrix",
        "matrix-body": "profile-matrix-body",
        "profile-grid": "profile-grid",
        "profile-member-region": "profile-member-region",
        "profile-member-grid": "profile-member-grid",
        "sot-tree-scroll": "sot-tree-scroll",
        "sot-tree-filter": "sot-tree-filter",
        "local-results": "local-results",
        "checkout-path": "checkout-path",
    ]
    if let identifier = identifiers[token],
       let match = elements.first(where: { elementMatchesIdentifier($0, identifier) }) {
        return match
    }
    if token == "workspace-shell" {
        return elements.first(where: {
            elementMatchesLabel($0, "Harness dashboard workspace")
        })
    }
    if token == "app-identity" {
        let headings = elements.filter {
            stringAttribute($0, kAXRoleAttribute) == "AXHeading" &&
                elementName($0) == "HarnessKit"
        }
        if headings.count == 1 { return headings[0] }
        let textMatches = elements.filter {
            stringAttribute($0, kAXRoleAttribute) == "AXStaticText" &&
                elementName($0) == "HarnessKit"
        }
        return textMatches.count == 1 ? textMatches[0] : nil
    }
    if token == "appearance-mode" {
        let matches = elements.filter {
            stringAttribute($0, kAXRoleAttribute) == "AXRadioButton" &&
                elementName($0) == "System"
        }
        return matches.count == 1 ? matches[0] : nil
    }
    if token == "left-divider" {
        return elements.first(where: { elementMatchesLabel($0, "좌측 탐색 패널 너비") })
    }
    if token == "right-divider" {
        return elements.first(where: { elementMatchesLabel($0, "우측 상세 패널 너비") })
    }
    if token == "graph-toggle" {
        return elements.first(where: {
            let name = elementName($0)
            return name.contains("그래프 접기") || name.contains("그래프 펼치기")
        })
    }
    if token == "matrix-toggle" {
        return elements.first(where: {
            let name = elementName($0)
            return name.contains("매트릭스 접기") || name.contains("매트릭스 펼치기")
        })
    }
    if token == "graph-zoom" {
        return elements.first(where: { elementMatchesLabel($0, "전체 보기") })
            ?? elements.first(where: { elementMatchesLabel($0, "확대") })
    }
    if token == "graph-zoom-in" {
        return elements.first(where: {
            stringAttribute($0, kAXRoleAttribute) == "AXButton" && elementName($0) == "확대"
        })
    }
    if token == "graph-zoom-out" {
        return elements.first(where: {
            stringAttribute($0, kAXRoleAttribute) == "AXButton" && elementName($0) == "축소"
        })
    }
    if token == "graph-zoom-reset" {
        return elements.first(where: {
            stringAttribute($0, kAXRoleAttribute) == "AXButton" && elementName($0) == "전체 보기"
        })
    }
    if token == "dashboard-sot" {
        return elements.first(where: { elementMatchesLabel($0, "SoT 명세 대시보드") })
    }
    if token == "dashboard-local" {
        return elements.first(where: { elementMatchesLabel($0, "로컬 PC 설치 대시보드") })
    }
    if token.hasPrefix("appearance-") {
        let requested = String(token.dropFirst("appearance-".count)).lowercased()
        return elements.first(where: {
            stringAttribute($0, kAXRoleAttribute) == "AXRadioButton" &&
                elementName($0).lowercased() == requested
        })
    }
    if token == "layout-status" {
        return elements.first(where: { elementName($0).contains("레이아웃 설정") })
    }
    if token.hasPrefix("semantic-relation:") ||
       token.hasPrefix("semantic-component:") ||
       token.hasPrefix("semantic-workflow-step:") {
        guard semanticIdentityFromIdentifier(token) != nil else { return nil }
        let matches = elements.filter { elementIdentifier($0) == token }
        return matches.count == 1 ? matches[0] : nil
    }
    if token.hasPrefix("workflow-inspector:") ||
       token.hasPrefix("workflow-inspector-step:") ||
       token.hasPrefix("workflow-overview:") {
        guard workflowInspectorTokenIdentity(token) != nil else { return nil }
        let matches = elements.filter { elementIdentifier($0) == token }
        return matches.count == 1 ? matches[0] : nil
    }
    if let componentID = componentIDFromToken(token) {
        return graphViewportElements(elements).first(where: {
            let name = elementName($0)
            return name == componentID || name.contains(" · \(componentID) ·")
        })
    }
    if token.hasPrefix("profile:") {
        let profileID = String(token.dropFirst("profile:".count))
        return graphViewportElements(elements).first(where: {
            let name = elementName($0)
            return name == profileID ||
                name.contains(" · profile:\(profileID) ·") ||
                name.contains(" · \(profileID) ·")
        })
    }
    if token == "unprofiled-node" {
        return elements.first(where: {
            elementName($0).contains(" · projection:unprofiled · Unprofiled · ")
        })
    }
    if token == "sot-tree-first-component" || token == "sot-tree-last-component" {
        guard let tree = elements.first(where: {
            elementMatchesIdentifier($0, "sot-tree-scroll")
        }) else { return nil }
        let matches = boundedDescendants(tree).filter {
            let role = stringAttribute($0, kAXRoleAttribute)
            let name = elementName($0)
            return ["AXButton", "AXRow", "AXGroup"].contains(role) &&
                name.components(separatedBy: " · ").contains(where: {
                    componentIDFromToken("component:\($0)") != nil
                })
        }
        let sorted = matches.sorted {
            guard let left = axFrame($0), let right = axFrame($1) else {
                return elementName($0) < elementName($1)
            }
            if abs(left.minY - right.minY) > 1 { return left.minY < right.minY }
            return left.minX < right.minX
        }
        return token == "sot-tree-first-component" ? sorted.first : sorted.last
    }
    if token.hasPrefix("label:") {
        return elements.first(where: {
            elementMatchesLabel($0, String(token.dropFirst("label:".count)))
        })
    }
    if token.hasPrefix("id:") {
        return elements.first(where: {
            elementMatchesIdentifier($0, String(token.dropFirst("id:".count)))
        })
    }
    return elements.first(where: { elementMatchesIdentifier($0, token) })
}

func pressableElementForToken(
    _ token: String,
    _ elements: [AXUIElement]
) -> AXUIElement? {
    let pressableElements = elements.filter { element in
        var actionNames: CFArray?
        guard AXUIElementCopyActionNames(element, &actionNames) == .success,
              let names = actionNames as? [String] else { return false }
        return names.contains(kAXPressAction as String)
    }
    return elementForToken(token, pressableElements)
}

func safeNumericValue(_ element: AXUIElement, _ attributeName: String) -> Any? {
    guard let number = attribute(element, attributeName) as? NSNumber else { return nil }
    let value = number.doubleValue
    if value.rounded() == value { return Int(value) }
    return value
}

func dividerActiveState(_ valueDescription: String, _ currentValue: Double) -> String? {
    guard currentValue.isFinite, currentValue.rounded() == currentValue else { return nil }
    let normal = "\(Int(currentValue))픽셀"
    if valueDescription == normal { return "normal" }
    if valueDescription == "\(normal) · 드래그 중" { return "dragging" }
    return nil
}

func targetRecord(_ token: String, _ element: AXUIElement) -> [String: Any]? {
    guard let frame = axFrame(element) else { return nil }
    let name = elementName(element)
    let enabled = (attribute(element, kAXEnabledAttribute) as? NSNumber)?.boolValue ?? true
    var record: [String: Any] = [
        "target": token,
        "role": boundedText(stringAttribute(element, kAXRoleAttribute), limit: 80),
        "name": name,
        "frame": integerFrame(frame),
        "visible": frame.width > 0 && frame.height > 0 && !boolAttribute(element, "AXHidden"),
        "focusable": elementIsFocusable(element),
        "focused": boolAttribute(element, kAXFocusedAttribute),
        "enabled": enabled,
        "selected": elementIsSelected(element),
    ]
    let expandedValue = (attribute(element, kAXExpandedAttribute) as? NSNumber)?.boolValue
    record["expanded_present"] = expandedValue != nil
    if let expandedValue { record["expanded"] = expandedValue }
    if token == "graph-toggle" && expandedValue == nil { return nil }
    let identifier = elementIdentifier(element)
    if !identifier.isEmpty { record["dom_identifier"] = identifier }
    if let profileIdentity = profileNodeIdentityFromName(name) {
        record["profile_identity"] = profileIdentity
    }
    let valueDescription = boundedText(
        stringAttribute(element, kAXValueDescriptionAttribute),
        limit: 80
    )
    if !valueDescription.isEmpty {
        record["value_description"] = valueDescription
    }
    if let value = safeNumericValue(element, kAXValueAttribute) { record["value"] = value }
    if let minimum = safeNumericValue(element, kAXMinValueAttribute) { record["minimum"] = minimum }
    if let maximum = safeNumericValue(element, kAXMaxValueAttribute) { record["maximum"] = maximum }
    if token == "left-divider" || token == "right-divider" {
        let rawOrientation = stringAttribute(element, kAXOrientationAttribute).lowercased()
        guard rawOrientation.contains("vertical"),
              let current = numberAttribute(element, kAXValueAttribute),
              let minimum = numberAttribute(element, kAXMinValueAttribute),
              let maximum = numberAttribute(element, kAXMaxValueAttribute),
              current.isFinite,
              minimum.isFinite,
              maximum.isFinite,
              minimum <= current,
              current <= maximum,
              let activeState = dividerActiveState(valueDescription, current) else { return nil }
        record["orientation"] = "vertical"
        record["current_value"] = safeNumericValue(element, kAXValueAttribute)
        record["minimum_value"] = safeNumericValue(element, kAXMinValueAttribute)
        record["maximum_value"] = safeNumericValue(element, kAXMaxValueAttribute)
        record["active_state"] = activeState
    }
    return record
}

func graphTooltipIdentity(_ elements: [AXUIElement]) -> [String: Any]? {
    guard let tooltip = elementForToken("graph-tooltip", elements),
          !boolAttribute(tooltip, "AXHidden"),
          let frame = axFrame(tooltip),
          frame.width > 0,
          frame.height > 0 else { return nil }
    let tooltipElements = boundedDescendants(tooltip)
    func uniqueElement(_ identifier: String) -> AXUIElement? {
        let matches = tooltipElements.filter {
            elementMatchesIdentifier($0, identifier)
        }
        return matches.count == 1 ? matches[0] : nil
    }
    guard let titleElement = uniqueElement("component-map-node-tooltip-title"),
          let idElement = uniqueElement("component-map-node-tooltip-id"),
          let metaElement = uniqueElement("component-map-node-tooltip-meta") else {
        return nil
    }
    let title = elementName(titleElement)
    let componentID = elementName(idElement)
    let meta = elementName(metaElement)
    guard !title.isEmpty,
          title.count <= 256,
          componentIDFromToken("component:\(componentID)") != nil,
          !meta.isEmpty else { return nil }
    return [
        "component_id": componentID,
        "title": boundedText(title, limit: 256),
        "meta": boundedText(meta, limit: 480),
        "visible": true,
        "frame": integerFrame(frame),
    ]
}

func profileNodeIdentityFromName(_ name: String) -> [String: Any]? {
    let parts = name.components(separatedBy: " · ")
    guard parts.count >= 4 else { return nil }
    let nodeID = parts[1]
    let type = parts[2]
    let profileID: String?
    if nodeID.hasPrefix("profile:") && type == "Profile" {
        profileID = String(nodeID.dropFirst("profile:".count))
    } else if nodeID == "projection:unprofiled" && type == "Unprofiled" {
        profileID = nil
    } else {
        return nil
    }
    guard nodeID.count <= 180,
          let memberCount = integerFromText(parts[3]) else { return nil }
    let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    guard nodeID.unicodeScalars.allSatisfy({ allowed.contains($0) }) else { return nil }
    var identity: [String: Any] = [
        "node_id": profileID ?? "unprofiled",
        "graph_node_id": nodeID,
        "type": type,
        "name": boundedText(parts[0], limit: 160),
        "member_count": memberCount,
        "active": name.contains(" · 현재 profile"),
    ]
    if let profileID { identity["profile_id"] = profileID }
    return identity
}

func profileIDFromName(_ name: String) -> String? {
    guard let identity = profileNodeIdentityFromName(name),
          identity["type"] as? String == "Profile" else { return nil }
    return identity["profile_id"] as? String
}

func integerFromText(_ value: String) -> Int? {
    let expression = try? NSRegularExpression(pattern: "[0-9]+")
    let range = NSRange(value.startIndex..<value.endIndex, in: value)
    guard let match = expression?.firstMatch(in: value, range: range),
          let swiftRange = Range(match.range, in: value) else { return nil }
    return Int(value[swiftRange])
}

func preferredPairFromText(_ value: String) -> [String: Int]? {
    let pattern = "^선호 패널 너비 좌측 ([0-9]+)픽셀, 우측 ([0-9]+)픽셀$"
    guard let expression = try? NSRegularExpression(pattern: pattern),
          let match = expression.firstMatch(
            in: value,
            range: NSRange(value.startIndex..<value.endIndex, in: value)
          ),
          match.numberOfRanges == 3,
          let leftRange = Range(match.range(at: 1), in: value),
          let rightRange = Range(match.range(at: 2), in: value),
          let left = Int(value[leftRange]),
          let right = Int(value[rightRange]),
          (200...512).contains(left),
          (184...560).contains(right) else { return nil }
    return ["left_px": left, "right_px": right]
}

func canonicalComponentIDFromName(_ name: String) -> String? {
    let allowedKinds = Set([
        "skill", "agent", "hook", "rule", "command", "composite",
    ])
    for part in name.components(separatedBy: " · ") {
        let segments = part.split(separator: ".").map(String.init)
        guard segments.count >= 3,
              segments[0] == "harnesskit",
              allowedKinds.contains(segments[1]),
              componentIDFromToken("component:\(part)") != nil else { continue }
        return part
    }
    return nil
}

func canonicalGraphComponentIdentityFromName(
    _ name: String
) -> (componentID: String, kind: String)? {
    let allowedKinds = Set([
        "skill", "agent", "hook", "rule", "command", "composite",
    ])
    let parts = name.components(separatedBy: " · ")
    for (index, part) in parts.enumerated() {
        let segments = part.split(separator: ".").map(String.init)
        if segments.count < 3 || segments[0] != "harnesskit" { continue }
        let canonicalKind = segments[1].lowercased()
        if !allowedKinds.contains(canonicalKind) ||
            componentIDFromToken("component:\(part)") == nil ||
            index + 1 >= parts.count {
            continue
        }
        let actualKind = parts[index + 1]
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        if actualKind == canonicalKind {
            return (part, actualKind)
        }
    }
    return nil
}

func legacySelectedCanonicalGraphComponentID(_ elements: [AXUIElement]) -> String? {
    let selectedIDs = Set(graphViewportElements(elements)
        .compactMap { element -> String? in
        guard elementIsSelected(element),
              let identity = canonicalGraphComponentIdentityFromName(
                elementName(element)
              ) else { return nil }
        return identity.componentID
        })
    guard selectedIDs.count == 1 else { return nil }
    return selectedIDs.first
}

func graphSnapshotIDFromDescription(_ viewport: AXUIElement) -> String? {
    let description = stringAttribute(viewport, kAXDescriptionAttribute)
    let separator = " · source "
    guard description.hasPrefix("snapshot "),
          let separatorRange = description.range(of: separator) else { return nil }
    let snapshotStart = description.index(
        description.startIndex,
        offsetBy: "snapshot ".count
    )
    let snapshotID = String(description[snapshotStart..<separatorRange.lowerBound])
    let allowed = CharacterSet(
        charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
    )
    guard (1...240).contains(snapshotID.count),
          snapshotID.unicodeScalars.allSatisfy({ allowed.contains($0) }) else {
        return nil
    }
    return snapshotID
}

func componentOwnerCountFromName(_ name: String) -> Int {
    guard let profilePart = name.components(separatedBy: " · ").first(where: {
        $0.hasPrefix("profile ")
    }) else { return 1 }
    let value = profilePart.dropFirst("profile ".count)
        .trimmingCharacters(in: .whitespacesAndNewlines)
    if value.isEmpty || value == "없음" { return 1 }
    return max(1, value.split(separator: ",").count)
}

func unionFrame(_ frames: [CGRect]) -> CGRect? {
    guard var result = frames.first else { return nil }
    for frame in frames.dropFirst() { result = result.union(frame) }
    return result
}

func legacyProfileSupernodeAXObservation(
    _ elements: [AXUIElement],
    _ anchorComponentIDs: [String]
) -> [String: Any]? {
    guard let viewport = elementForToken("graph-viewport", elements) else { return nil }
    let graphElements = boundedDescendants(viewport)
    var profiles: [[String: Any]] = []
    var membershipEdgeCount = 0
    for element in graphElements {
        guard var identity = profileNodeIdentityFromName(elementName(element)),
              let frame = axFrame(element) else { continue }
        identity["frame"] = integerFrame(frame)
        profiles.append(identity)
        membershipEdgeCount += identity["member_count"] as? Int ?? 0
    }
    profiles.sort {
        String(describing: $0["graph_node_id"] ?? "") <
            String(describing: $1["graph_node_id"] ?? "")
    }

    var componentRecords: [(id: String, element: AXUIElement, frame: CGRect, owners: Int)] = []
    for element in graphElements {
        let name = elementName(element)
        guard let componentID = canonicalComponentIDFromName(name),
              let frame = axFrame(element), frame.width > 0, frame.height > 0 else { continue }
        componentRecords.append((
            id: componentID,
            element: element,
            frame: frame,
            owners: componentOwnerCountFromName(name)
        ))
    }
    let uniqueIDs = Set(componentRecords.map(\.id))
    let componentNodeUnique = uniqueIDs.count == componentRecords.count
    let membershipOwnerCount = componentRecords.reduce(0) { $0 + $1.owners }
    let anchors = componentRecords.filter { anchorComponentIDs.contains($0.id) }
    guard profiles.contains(where: { $0["type"] as? String == "Profile" }),
          profiles.contains(where: { $0["type"] as? String == "Unprofiled" }),
          anchors.count == anchorComponentIDs.count else { return nil }
    return [
        "profile_body_frames": profiles,
        "component_body_frames": anchors.map {
            ["component_id": $0.id, "frame": integerFrame($0.frame)]
        },
        "component_node_unique": componentNodeUnique,
        "membership_edge_count": membershipEdgeCount,
        "membership_owner_count": membershipOwnerCount,
    ]
}

func legacyGraphReadabilityObservation(
    _ elements: [AXUIElement]
) -> [String: Any]? {
    guard let viewport = elementForToken("graph-viewport", elements),
          let snapshotID = graphSnapshotIDFromDescription(viewport),
          let viewportFrame = axFrame(viewport),
          viewportFrame.width > 0,
          viewportFrame.height > 0,
          !boolAttribute(viewport, "AXHidden") else { return nil }
    let graphElements = boundedDescendants(viewport, maximum: 2048)

    func isVisible(_ element: AXUIElement, _ frame: CGRect) -> Bool {
        guard frame.width > 0,
              frame.height > 0,
              !boolAttribute(element, "AXHidden") else { return false }
        let intersection = frame.intersection(viewportFrame)
        return !intersection.isNull && intersection.width > 0 && intersection.height > 0
    }

    func visibleStaticTextCount(_ element: AXUIElement) -> Int {
        boundedDescendants(element, maximum: 64).filter { descendant in
            guard stringAttribute(descendant, kAXRoleAttribute) == "AXStaticText",
                  let frame = axFrame(descendant) else { return false }
            return isVisible(descendant, frame)
        }.count
    }

    var componentNodes: [[String: Any]] = []
    var componentIDs = Set<String>()
    for element in graphElements {
        let role = stringAttribute(element, kAXRoleAttribute)
        guard role == "AXButton" || role == "AXGroup",
              let identity = canonicalGraphComponentIdentityFromName(
                elementName(element)
              ),
              let frame = axFrame(element),
              !componentIDs.contains(identity.componentID) else { continue }
        componentIDs.insert(identity.componentID)
        componentNodes.append([
            "component_id": identity.componentID,
            "kind": identity.kind,
            "role": role,
            "global_frame": integerFrame(frame),
            "selected": elementIsSelected(element),
            "visible": isVisible(element, frame),
            "visible_static_text_count": visibleStaticTextCount(element),
        ])
        if componentNodes.count > 256 { return nil }
    }
    guard !componentNodes.isEmpty else { return nil }
    componentNodes.sort {
        String(describing: $0["component_id"] ?? "") <
            String(describing: $1["component_id"] ?? "")
    }

    var profileIdentityVisible = false
    var unprofiledIdentityVisible = false
    for element in graphElements {
        guard let identity = profileNodeIdentityFromName(elementName(element)),
              let type = identity["type"] as? String,
              let frame = axFrame(element),
              isVisible(element, frame) else { continue }
        if type == "Profile" { profileIdentityVisible = true }
        if type == "Unprofiled" { unprofiledIdentityVisible = true }
    }
    let visibleStaticTextTotal = componentNodes.reduce(0) { partial, node in
        partial + (node["visible_static_text_count"] as? Int ?? 0)
    }
    let observedKinds = Array(Set(componentNodes.compactMap {
        $0["kind"] as? String
    })).sorted()
    return [
        "snapshot_id": snapshotID,
        "viewport_global_frame": integerFrame(viewportFrame),
        "component_nodes": componentNodes,
        "observed_kind_tokens": observedKinds,
        "visible_component_static_text_count": visibleStaticTextTotal,
        "profile_identity_visible": profileIdentityVisible,
        "unprofiled_identity_visible": unprofiledIdentityVisible,
    ]
}

func graphToolbarObservation(_ elements: [AXUIElement]) -> [String: Any]? {
    guard let center = elementForToken("center-pane", elements).flatMap(axFrame),
          let toolbar = elementForToken("graph-toolbar", elements).flatMap(axFrame),
          let legend = elementForToken("graph-legend", elements),
          let legendFrame = axFrame(legend),
          let camera = elementForToken("graph-camera", elements),
          let cameraFrame = axFrame(camera),
          let graphBody = elementForToken("graph-body", elements).flatMap(axFrame),
          let matrix = elementForToken("matrix", elements).flatMap(axFrame) else { return nil }
    let legendContent = unionFrame(boundedDescendants(legend).compactMap(axFrame))
    let controls = ["축소", "전체 보기", "확대"].compactMap { expected -> [String: Any]? in
        guard let element = boundedDescendants(camera).first(where: {
            elementName($0) == expected
        }), let frame = axFrame(element) else { return nil }
        return [
            "name": expected == "전체 보기" ? "Fit" : expected,
            "visible": frame.width > 0 && frame.height > 0 && !boolAttribute(element, "AXHidden"),
            "focusable": elementIsFocusable(element),
        ]
    }
    guard controls.count == 3 else { return nil }
    let scrollWidth = max(
        legendFrame.width,
        legendContent.map { max(0, $0.maxX - legendFrame.minX) } ?? legendFrame.width
    )
    return [
        "viewport_width_px": Int(center.width.rounded()),
        "legend_frame": integerFrame(legendFrame),
        "camera_frame": integerFrame(cameraFrame),
        "toolbar_frame": integerFrame(toolbar),
        "graph_body_frame": integerFrame(graphBody),
        "matrix_frame": integerFrame(matrix),
        "camera_controls": controls,
        "legend_scroll_width_px": Int(scrollWidth.rounded()),
        "legend_client_width_px": Int(legendFrame.width.rounded()),
    ]
}

func sotTreeObservation(_ elements: [AXUIElement]) -> [String: Any]? {
    guard let outer = elementForToken("left-pane", elements).flatMap(axFrame),
          let tree = elementForToken("sot-tree-scroll", elements),
          let scrollFrame = axFrame(tree) else { return nil }
    let treeElements = boundedDescendants(tree)
    var components: [(id: String, element: AXUIElement, frame: CGRect)] = []
    for element in treeElements {
        guard let componentID = canonicalComponentIDFromName(elementName(element)),
              let frame = axFrame(element), frame.width > 0, frame.height > 0 else { continue }
        components.append((componentID, element, frame))
    }
    guard let last = components.last else { return nil }
    let contentFrame = unionFrame(components.map(\.frame)) ?? scrollFrame
    let scrollHeight = max(scrollFrame.height, contentFrame.height)
    let scrollTop = max(0, scrollFrame.minY - contentFrame.minY)
    let lastVisible = frameContains(scrollFrame, last.frame)
    let selected = components.first(where: { elementIsSelected($0.element) })?.id
    let focused = components.first(where: {
        boolAttribute($0.element, kAXFocusedAttribute)
    })?.id
    let filterValue = elementForToken("sot-tree-filter", elements)
        .map { stringAttribute($0, kAXValueAttribute) } ?? ""
    var payload: [String: Any] = [
        "inventory_count": components.count,
        "outer_frame": integerFrame(outer),
        "scroll_frame": integerFrame(scrollFrame),
        "scroll_top_px": Int(scrollTop.rounded()),
        "scroll_height_px": Int(scrollHeight.rounded()),
        "client_height_px": Int(scrollFrame.height.rounded()),
        "last_row": [
            "identity": last.id,
            "visible": lastVisible,
            "focusable": elementIsFocusable(last.element),
            "focused": boolAttribute(last.element, kAXFocusedAttribute),
            "frame": integerFrame(last.frame),
        ],
        "filter_value": boundedText(filterValue, limit: 160),
    ]
    if let selected { payload["selected_identity"] = selected }
    if let focusIdentity = focused ?? selected {
        payload["focused_identity"] = focusIdentity
    }
    return payload
}

func positiveNestedScrollRangeCount(_ root: AXUIElement?) -> Int {
    guard let root else { return 0 }
    return boundedDescendants(root, maximum: 2048).filter { element in
        guard stringAttribute(element, kAXRoleAttribute) == "AXScrollBar" else {
            return false
        }
        let orientation = stringAttribute(element, kAXOrientationAttribute).lowercased()
        guard orientation.contains("vertical"),
              let minimum = numberAttribute(element, kAXMinValueAttribute),
              let maximum = numberAttribute(element, kAXMaxValueAttribute) else {
            return false
        }
        return maximum > minimum
    }.count
}

func visibleFocusableCount(_ root: AXUIElement?) -> Int {
    guard let root else { return 0 }
    return boundedDescendants(root, maximum: 2048).filter { element in
        guard elementIsFocusable(element),
              !boolAttribute(element, "AXHidden"),
              let frame = axFrame(element) else { return false }
        return frame.width > 0 && frame.height > 0
    }.count
}

func matrixProfileIDFromName(_ name: String) -> String? {
    return name.components(separatedBy: " · ").first(where: {
        $0.hasPrefix("harnesskit.profile.") || $0 == "__unprofiled__"
    })
}

func observedFontSize(_ element: AXUIElement) -> Double? {
    if let direct = numberAttribute(element, "AXFontSize"), direct > 0 {
        return direct
    }
    if let font = attribute(element, "AXFont") as? NSDictionary {
        for key in ["AXFontSize", "NSSize", "size"] {
            if let value = font[key] as? NSNumber, value.doubleValue > 0 {
                return value.doubleValue
            }
        }
    }
    let text = stringAttribute(element, kAXValueAttribute)
    if !text.isEmpty {
        var range = CFRange(location: 0, length: max(1, text.utf16.count))
        if let rangeValue = AXValueCreate(.cfRange, &range) {
            var value: CFTypeRef?
            if AXUIElementCopyParameterizedAttributeValue(
                element,
                kAXAttributedStringForRangeParameterizedAttribute as CFString,
                rangeValue,
                &value
            ) == .success,
               let attributed = value as? NSAttributedString,
               attributed.length > 0,
               let font = attributed.attribute(
                    .font,
                    at: 0,
                    effectiveRange: nil
               ) as? NSFont,
               font.pointSize > 0 {
                return Double(font.pointSize)
            }
        }
    }
    return nil
}

func typographySamples(_ elements: [AXUIElement]) -> [[String: Any]]? {
    let specifications: [(surface: String, identifier: String, prefix: Bool)] = [
        ("toolbar_control", "typography-toolbar-control", false),
        ("tree_row", "typography-tree-row", false),
        ("inspector_metadata", "typography-inspector-meta", false),
        ("inspector_section", "typography-inspector-section", false),
        ("workflow_card_title", "typography-workflow-card-title:", true),
        ("workflow_card_body", "typography-workflow-card-body:", true),
        ("source_preview_body", "typography-source-preview-body", false),
    ]
    var samples: [[String: Any]] = []
    for specification in specifications {
        let matches = elements.filter {
            let identifier = elementIdentifier($0)
            return specification.prefix
                ? identifier.hasPrefix(specification.identifier)
                : identifier == specification.identifier
        }.sorted {
            elementIdentifier($0) < elementIdentifier($1)
        }
        guard let root = matches.first else { continue }
        let candidates = [root] + boundedDescendants(root, maximum: 128)
        let sizes = candidates.compactMap(observedFontSize).filter {
            $0.isFinite && $0 > 0
        }.sorted()
        guard !sizes.isEmpty else { continue }
        samples.append([
            "surface": specification.surface,
            "dom_identifier": boundedText(elementIdentifier(root), limit: 320),
            "font_size_px": sizes[sizes.count / 2],
        ])
    }
    return samples.isEmpty ? nil : samples
}

func matrixCardRecord(
    _ element: AXUIElement,
    identity: String,
    origin: CGPoint
) -> [String: Any]? {
    guard let frame = axFrame(element), frame.width > 0, frame.height > 0 else {
        return nil
    }
    let typography = boundedDescendants(element, maximum: 64).compactMap {
        descendant -> (name: String, frame: CGRect, size: Double)? in
        let name = elementName(descendant)
        guard !name.isEmpty,
              let textFrame = axFrame(descendant),
              textFrame.width > 0,
              textFrame.height > 0,
              let size = observedFontSize(descendant),
              size > 0 else { return nil }
        return (name, textFrame, size)
    }
    guard let title = typography.max(by: { $0.size < $1.size }) else {
        return nil
    }
    let metadataCandidates = typography.filter {
        $0.size < title.size - 0.01 || $0.name.contains("components")
    }
    guard let metadata = metadataCandidates.max(by: { $0.size < $1.size }) else {
        return nil
    }
    let rootFont = title.size / 0.9375
    guard rootFont.isFinite, rootFont > 0 else { return nil }
    let contentTopInset = max(0, title.frame.minY - frame.minY)
    return [
        "identity": boundedText(identity, limit: 240),
        "frame": integerFrame(CGRect(
            x: frame.minX - origin.x,
            y: frame.minY - origin.y,
            width: frame.width,
            height: frame.height
        )),
        "title_font_px": title.size,
        "metadata_font_px": metadata.size,
        "content_top_inset_px": Double(contentTopInset),
        "root_font_px": rootFont,
    ]
}

func firstRowColumnCount(_ cards: [[String: Any]]) -> Int? {
    let frames = cards.compactMap { card -> CGRect? in
        guard let frame = card["frame"] as? [String: Int],
              let x = frame["x"], let y = frame["y"],
              let width = frame["width"], let height = frame["height"] else {
            return nil
        }
        return CGRect(x: x, y: y, width: width, height: height)
    }
    guard frames.count == cards.count, let firstY = frames.map(\.minY).min() else {
        return nil
    }
    return frames.filter { abs($0.minY - firstY) <= 1 }.count
}

func matrixWrapObservation(_ elements: [AXUIElement]) -> [String: Any]? {
    guard let center = elementForToken("center-pane", elements).flatMap(axFrame),
          let body = elementForToken("matrix-body", elements),
          let bodyFrame = axFrame(body),
          let profileGrid = elementForToken("profile-grid", elements),
          let memberGrid = elementForToken("profile-member-grid", elements) else {
        return nil
    }
    func buttons(_ root: AXUIElement) -> [AXUIElement] {
        return boundedDescendants(root, maximum: 512).filter {
            stringAttribute($0, kAXRoleAttribute) == "AXButton" &&
                !boolAttribute($0, "AXHidden")
        }
    }
    var profileCards = buttons(profileGrid).compactMap { element -> [String: Any]? in
        let name = elementName(element)
        guard let identity = matrixProfileIDFromName(name) else { return nil }
        return matrixCardRecord(
            element,
            identity: identity,
            origin: CGPoint(x: center.minX, y: bodyFrame.minY)
        )
    }
    var memberCards = buttons(memberGrid).compactMap { element -> [String: Any]? in
        let name = elementName(element)
        let identity = canonicalComponentIDFromName(name) ?? name
        guard !identity.isEmpty else { return nil }
        return matrixCardRecord(
            element,
            identity: identity,
            origin: CGPoint(x: center.minX, y: bodyFrame.minY)
        )
    }
    func rowMajor(_ left: [String: Any], _ right: [String: Any]) -> Bool {
        guard let leftFrame = left["frame"] as? [String: Int],
              let rightFrame = right["frame"] as? [String: Int],
              let leftX = leftFrame["x"], let leftY = leftFrame["y"],
              let rightX = rightFrame["x"], let rightY = rightFrame["y"] else {
            return false
        }
        if abs(leftY - rightY) > 1 { return leftY < rightY }
        return leftX < rightX
    }
    profileCards.sort(by: rowMajor)
    memberCards.sort(by: rowMajor)
    guard !profileCards.isEmpty,
          !memberCards.isEmpty,
          let profileColumns = firstRowColumnCount(profileCards),
          let memberColumns = firstRowColumnCount(memberCards),
          let rootFont = (profileCards.first?["root_font_px"] as? NSNumber)?.doubleValue,
          rootFont > 0 else { return nil }
    func boundedCards(_ cards: [[String: Any]]) -> [[String: Any]] {
        return cards.map { card in
            var bounded = card
            bounded.removeValue(forKey: "root_font_px")
            return bounded
        }
    }
    return [
        "center_inline_px": Int(center.width.rounded()),
        "root_font_px": rootFont,
        "profile_column_count": profileColumns,
        "member_column_count": memberColumns,
        "profile_cards": boundedCards(profileCards),
        "member_cards": boundedCards(memberCards),
    ]
}

func matrixLayoutObservation(
    _ elements: [AXUIElement],
    _ anchorComponentIDs: [String],
    _ semanticGraph: [String: Any]
) -> [String: Any]? {
    guard let center = elementForToken("center-pane", elements).flatMap(axFrame),
          let firstViewport = elementForToken("first-viewport", elements).flatMap(axFrame),
          let graphBody = elementForToken("graph-body", elements),
          let graphBodyFrame = axFrame(graphBody),
          let graphViewport = elementForToken("graph-viewport", elements).flatMap(axFrame),
          let graphToggle = elementForToken("graph-toggle", elements),
          let mapExpanded = attribute(graphToggle, kAXExpandedAttribute) as? NSNumber,
          let matrixHeader = elementForToken("matrix", elements).flatMap(axFrame),
          let matrixToggle = elementForToken("matrix-toggle", elements),
          let matrixExpanded = attribute(matrixToggle, kAXExpandedAttribute) as? NSNumber else {
        return nil
    }
    let matrixBody = elementForToken("matrix-body", elements)
    let matrixBodyFrame = matrixBody.flatMap { element -> CGRect? in
        guard !boolAttribute(element, "AXHidden"),
              let frame = axFrame(element),
              frame.width > 0,
              frame.height > 0 else { return nil }
        return frame
    }
    let contentFrame = unionFrame([firstViewport] + (matrixBodyFrame.map { [$0] } ?? []))
        ?? firstViewport
    let scrollHeight = max(center.height, contentFrame.maxY - firstViewport.minY)
    let scrollTop = max(0, center.minY - firstViewport.minY)
    guard let semanticComponents = semanticGraph["components"] as? [[String: Any]] else {
        return nil
    }
    let rightDetailIdentity = elementForToken("selected-component", elements)
        .flatMap { canonicalComponentIDFromName(elementName($0)) }
    let selectedComponents = semanticComponents.filter { component in
        component["selected"] as? Bool == true
    }
    guard selectedComponents.count == 1,
          let selectedIdentity = selectedComponents[0]["component_id"] as? String,
          let rightDetailIdentity,
          selectedIdentity == rightDetailIdentity else { return nil }
    let activeProfile = elements.compactMap { element -> String? in
            guard elementIsSelected(element) else { return nil }
            return profileIDFromName(elementName(element))
                ?? matrixProfileIDFromName(elementName(element))
        }.first
    guard let activeProfile else { return nil }
    let owningProfiles = elementForToken("profile-grid", elements).map {
        boundedDescendants($0, maximum: 256).compactMap { element -> String? in
            let name = elementName(element)
            guard name.contains("선택 component 포함") else { return nil }
            return matrixProfileIDFromName(name)
        }
    } ?? []
    let zoomText = elements.map(elementName).first(where: {
        $0.contains("×") && ($0.contains("개요") || $0.contains("선택") || $0.contains("상세"))
    }) ?? ""
    let scaleText = zoomText.components(separatedBy: "×").first ?? ""
    let scale = Double(scaleText.trimmingCharacters(in: .whitespacesAndNewlines))
    let cameraAnchors = anchorComponentIDs.compactMap { componentID -> [String: Any]? in
        guard let frame = elementForToken(
            "semantic-component:\(componentID)",
            elements
        ).flatMap(axFrame) else {
            return nil
        }
        return [
            "component_id": componentID,
            "frame": integerFrame(CGRect(
                x: frame.minX - graphViewport.minX,
                y: frame.minY - graphViewport.minY,
                width: frame.width,
                height: frame.height
            )),
        ]
    }
    guard cameraAnchors.count == anchorComponentIDs.count else { return nil }
    var payload: [String: Any] = [
        "disclosures": [
            "component_map": [
                "expanded": mapExpanded.boolValue,
                "body_present": graphBodyFrame.width > 0 && graphBodyFrame.height > 0,
                "focus_target_count": visibleFocusableCount(graphBody),
            ],
            "profile_matrix": [
                "expanded": matrixExpanded.boolValue,
                "body_present": matrixBodyFrame != nil,
                "focus_target_count": visibleFocusableCount(matrixBody),
            ],
        ],
        "center_client_frame": integerFrame(center),
        "first_viewport_frame": integerFrame(firstViewport),
        "graph_viewport_frame": integerFrame(graphViewport),
        "matrix_header_frame": integerFrame(matrixHeader),
        "outer_scroll": [
            "owner": "workbench",
            "scroll_top_px": Int(scrollTop.rounded()),
            "scroll_height_px": Int(scrollHeight.rounded()),
            "client_height_px": Int(center.height.rounded()),
            "positive_nested_scroll_range_count": positiveNestedScrollRangeCount(matrixBody),
        ],
        "selected_component_id": selectedIdentity,
        "active_profile_id": activeProfile,
        "owning_profile_ids": Array(Set(owningProfiles)).sorted(),
        "right_detail_id": rightDetailIdentity,
        "camera": [
            "available": scale != nil,
            "scale": scale.map { $0 as Any } ?? NSNull(),
            "anchor_nodes": cameraAnchors,
        ],
    ]
    payload["matrix_body_frame"] = matrixBodyFrame.map(integerFrame) ?? NSNull()
    return payload
}

func workspaceAmendmentDescendants(
    _ root: AXUIElement,
    maximum: Int
) -> [AXUIElement] {
    boundedDescendants(root, maximum: maximum)
}

func collapsedWorkbenchLayoutObservation(
    _ elements: [AXUIElement]
) -> [String: Any]? {
    guard let center = elementForToken("center-pane", elements),
          let firstViewport = elementForToken("first-viewport", elements),
          let matrixHeader = elementForToken("matrix", elements),
          let matrixBody = elementForToken("matrix-body", elements),
          let graphToggle = elementForToken("graph-toggle", elements),
          let matrixToggle = elementForToken("matrix-toggle", elements),
          let mapExpanded = attribute(graphToggle, kAXExpandedAttribute) as? NSNumber,
          let matrixExpanded = attribute(matrixToggle, kAXExpandedAttribute) as? NSNumber,
          !mapExpanded.boolValue,
          matrixExpanded.boolValue,
          !boolAttribute(matrixBody, "AXHidden"),
          elementIdentifier(center) == "workbench",
          elementIdentifier(firstViewport) == "sot-workbench-first-viewport",
          elementIdentifier(matrixHeader) == "profile-matrix",
          elementIdentifier(matrixBody) == "profile-matrix-body",
          let centerFrame = axFrame(center),
          let firstViewportFrame = axFrame(firstViewport),
          let matrixHeaderFrame = axFrame(matrixHeader),
          let matrixBodyFrame = axFrame(matrixBody),
          centerFrame.width > 0,
          centerFrame.height > 0,
          firstViewportFrame.width > 0,
          firstViewportFrame.height > 0,
          matrixHeaderFrame.width > 0,
          matrixHeaderFrame.height > 0,
          matrixBodyFrame.width > 0,
          matrixBodyFrame.height > 0 else { return nil }
    return [
        "stable_ids": [
            "center": "workbench",
            "first_viewport": "sot-workbench-first-viewport",
            "matrix_header": "profile-matrix",
            "matrix_body": "profile-matrix-body",
        ],
        "component_map_expanded": false,
        "profile_matrix_expanded": true,
        "center_frame": integerFrame(centerFrame),
        "first_viewport_frame": integerFrame(firstViewportFrame),
        "matrix_header_frame": integerFrame(matrixHeaderFrame),
        "matrix_body_frame": integerFrame(matrixBodyFrame),
    ]
}

let workspaceAmendmentSelectors: Set<String> = [
    "#left-pane-disclosure",
    "#right-pane-disclosure",
    "#typography-menu-trigger",
    "#register-checkout",
    "#load-sot",
    "#local-scope-all",
    "[data-local-instance][aria-pressed=true]",
    "#project-ignore-open",
    "[data-project-ignore-dialog]",
    "#project-ignore-text",
    "#project-ignore-save",
    "[data-project-ignore-dialog] [role=alert]",
    "[data-correlation-state]",
    "#local-correlation-detail",
    "#local-runtime-status",
    "#local-runtime-snapshot",
    "#typography-source-preview-body",
]

func canonicalTypographyPreset(_ value: String) -> String? {
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    let candidate = normalized
        .split(whereSeparator: { $0 == ":" || $0 == "·" })
        .last
        .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
        ?? normalized
    switch candidate.lowercased() {
    case "small", "작게": return "Small"
    case "default", "기본": return "Default"
    case "large", "크게": return "Large"
    default: return nil
    }
}

func workspaceTypographyMenuItem(
    _ elements: [AXUIElement],
    _ preset: String
) -> AXUIElement? {
    let label = ["Small": "작게", "Default": "기본", "Large": "크게"][preset]
    guard let label else { return nil }
    let matches = elements.filter {
        ["AXButton", "AXMenuItem"].contains(stringAttribute($0, kAXRoleAttribute)) &&
            elementName($0) == label &&
            !boolAttribute($0, "AXHidden")
    }
    return matches.count == 1 ? matches[0] : nil
}

func selectedLocalResult(_ elements: [AXUIElement]) -> AXUIElement? {
    guard let results = elementForToken("local-results", elements) else { return nil }
    let matches = workspaceAmendmentDescendants(results, maximum: 512).filter {
        stringAttribute($0, kAXRoleAttribute) == "AXButton" && elementIsSelected($0)
    }
    return matches.count == 1 ? matches[0] : nil
}

func workspaceElementForSelector(
    _ selector: String,
    _ elements: [AXUIElement]
) -> AXUIElement? {
    guard workspaceAmendmentSelectors.contains(selector) else { return nil }
    let identifiers: [String: String] = [
        "#left-pane-disclosure": "left-pane-disclosure",
        "#right-pane-disclosure": "right-pane-disclosure",
        "#typography-menu-trigger": "typography-menu-trigger",
        "#register-checkout": "register-checkout",
        "#load-sot": "load-sot",
        "#local-scope-all": "local-scope-all",
        "#project-ignore-open": "project-ignore-open",
        "#project-ignore-text": "project-ignore-text",
        "#project-ignore-save": "project-ignore-save",
        "#local-correlation-detail": "local-correlation-detail",
        "#local-runtime-status": "local-runtime-status",
        "#local-runtime-snapshot": "local-runtime-snapshot",
    ]
    if let identifier = identifiers[selector] {
        let matches = elements.filter { elementIdentifier($0) == identifier }
        if matches.count == 1 { return matches[0] }
    }
    if selector == "#register-checkout" {
        let names = ["HarnessKit 연결", "연결 중…"]
        let matches = elements.filter {
            let name = elementName($0)
            return stringAttribute($0, kAXRoleAttribute) == "AXButton" &&
                names.contains(where: { name.contains($0) })
        }
        return matches.count == 1 ? matches[0] : nil
    }
    if selector == "#load-sot" {
        let names = ["SoT 새로고침", "새로고침 중..."]
        let matches = elements.filter {
            let name = elementName($0)
            return stringAttribute($0, kAXRoleAttribute) == "AXButton" &&
                names.contains(where: { name.contains($0) })
        }
        return matches.count == 1 ? matches[0] : nil
    }
    if selector == "[data-local-instance][aria-pressed=true]" {
        return selectedLocalResult(elements)
    }
    if selector == "[data-project-ignore-dialog]" {
        let matches = elements.filter {
            ["AXDialog", "AXGroup"].contains(stringAttribute($0, kAXRoleAttribute)) &&
                elementName($0).contains("스캔 제외 규칙 편집")
        }
        return matches.count == 1 ? matches[0] : nil
    }
    if selector == "[data-project-ignore-dialog] [role=alert]" {
        guard let dialog = workspaceElementForSelector(
            "[data-project-ignore-dialog]",
            elements
        ) else { return nil }
        let matches = workspaceAmendmentDescendants(dialog, maximum: 256).filter {
            let role = stringAttribute($0, kAXRoleAttribute)
            let subrole = stringAttribute($0, kAXSubroleAttribute)
            return (role == "AXGroup" || role == "AXStaticText") &&
                (subrole == "AXApplicationAlert" || elementIdentifier($0).contains("alert")) &&
                !elementName($0).isEmpty
        }
        return matches.count == 1 ? matches[0] : nil
    }
    if selector == "[data-correlation-state]" {
        guard let selected = selectedLocalResult(elements) else { return nil }
        let labels = Set(["SoT 일치", "SoT에서 변경됨", "연결 확인 필요"])
        let matches = workspaceAmendmentDescendants(selected, maximum: 128).filter {
            labels.contains(elementName($0))
        }
        return matches.count == 1 ? matches[0] : nil
    }
    return nil
}

func safeCorrelationState(_ text: String) -> String? {
    switch text {
    case "SoT 일치": return "verified"
    case "SoT에서 변경됨": return "drift"
    case "연결 확인 필요": return "ambiguous"
    default: return nil
    }
}

func selectorTargetRecord(
    _ selector: String,
    _ element: AXUIElement
) -> [String: Any]? {
    guard var record = targetRecord(selector, element) else { return nil }
    record["selector"] = selector
    let rawValue = stringAttribute(element, kAXValueAttribute)
    if selector == "#project-ignore-text" {
        record.removeValue(forKey: "name")
        record.removeValue(forKey: "value_text")
        record["value_redacted"] = true
        record["value_length"] = rawValue.count
    } else if selector == "[data-local-instance][aria-pressed=true]" {
        record.removeValue(forKey: "name")
        record.removeValue(forKey: "value_text")
        record["pressed"] = elementIsSelected(element)
    } else if selector == "#typography-menu-trigger" {
        guard let preset = canonicalTypographyPreset(elementName(element)) else { return nil }
        record["value_text"] = preset
    } else if selector == "[data-correlation-state]" {
        let label = elementName(element)
        guard let state = safeCorrelationState(label) else { return nil }
        record["safe_text"] = label
        record["correlation_state"] = state
    } else if selector == "#local-correlation-detail" {
        let safeText = elementName(element, limit: 320)
        guard !safeText.contains("/") else { return nil }
        record["safe_text"] = safeText
    } else if !rawValue.isEmpty && rawValue.count <= 80 {
        record["value_text"] = boundedText(rawValue, limit: 80)
    }
    return record
}

func workspaceSelectorObservation(
    _ pid: pid_t,
    _ selector: String
) -> [String: Any]? {
    guard workspaceAmendmentSelectors.contains(selector),
          let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let element = workspaceElementForSelector(selector, elements) else {
        return [
            "accepted": true,
            "pid": Int(pid),
            "window_id": workspaceWindow.windowID,
            "selector": selector,
            "present": false,
            "target_count": 0,
        ]
    }
    guard let target = selectorTargetRecord(selector, element) else { return nil }
    return [
        "accepted": true,
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "selector": selector,
        "present": true,
        "target_count": 1,
        "target": target,
    ]
}

func postMouseClick(_ frame: CGRect) -> Bool {
    guard frame.width > 0,
          frame.height > 0,
          let source = CGEventSource(stateID: .hidSystemState) else { return false }
    let point = CGPoint(x: frame.midX, y: frame.midY)
    let types: [CGEventType] = [.mouseMoved, .leftMouseDown, .leftMouseUp]
    for type in types {
        guard let event = CGEvent(
            mouseEventSource: source,
            mouseType: type,
            mouseCursorPosition: point,
            mouseButton: .left
        ) else { return false }
        event.post(tap: .cghidEventTap)
        Thread.sleep(forTimeInterval: 0.03)
    }
    return true
}

func pressWorkspaceSelector(
    _ pid: pid_t,
    _ selector: String
) -> [String: Any]? {
    let pressable = Set([
        "#left-pane-disclosure",
        "#right-pane-disclosure",
        "#local-scope-all",
        "#project-ignore-open",
        "#project-ignore-save",
    ])
    guard pressable.contains(selector),
          let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let element = workspaceElementForSelector(selector, elements),
          let before = selectorTargetRecord(selector, element),
          AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else {
        return nil
    }
    let deadline = ProcessInfo.processInfo.systemUptime + 1.5
    var afterObservation: [String: Any]?
    repeat {
        guard let candidate = workspaceSelectorObservation(pid, selector) else { return nil }
        let present = candidate["present"] as? Bool == true
        let target = candidate["target"] as? [String: Any]
        let effectObserved: Bool
        if selector == "#left-pane-disclosure" || selector == "#right-pane-disclosure" {
            effectObserved = target?["expanded"] as? Bool != before["expanded"] as? Bool
        } else if selector == "#local-scope-all" {
            effectObserved = target?["selected"] as? Bool == true
        } else if selector == "#project-ignore-open" {
            effectObserved = workspaceSelectorObservation(
                pid,
                "[data-project-ignore-dialog]"
            )?["present"] as? Bool == true
        } else {
            effectObserved = !present && workspaceSelectorObservation(
                pid,
                "[data-project-ignore-dialog]"
            )?["present"] as? Bool == false
        }
        if effectObserved {
            afterObservation = candidate
            break
        }
        Thread.sleep(forTimeInterval: 0.03)
    } while ProcessInfo.processInfo.systemUptime < deadline
    guard let afterObservation else { return nil }
    return [
        "accepted": true,
        "kind": "selector-press",
        "transport": "ax-press",
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "selector": selector,
        "before": before,
        "after": afterObservation,
    ]
}

func selectWorkspaceTypographyPreset(
    _ pid: pid_t,
    _ preset: String
) -> [String: Any]? {
    guard ["Small", "Default", "Large"].contains(preset),
          let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let element = workspaceElementForSelector("#typography-menu-trigger", elements),
          let before = selectorTargetRecord("#typography-menu-trigger", element),
          let frame = axFrame(element),
          frameContains(workspaceWindow.cgFrame, frame),
          AXUIElementSetAttributeValue(
              element,
              kAXFocusedAttribute as CFString,
              kCFBooleanTrue
          ) == .success,
          AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else { return nil }
    let deadline = ProcessInfo.processInfo.systemUptime + 1.5
    var after: [String: Any]?
    repeat {
        let currentElements = boundedDescendants(workspaceWindow.element)
        if let item = workspaceTypographyMenuItem(currentElements, preset),
           AXUIElementPerformAction(item, kAXPressAction as CFString) == .success {
            let selectionDeadline = ProcessInfo.processInfo.systemUptime + 1.0
            repeat {
                if let observation = workspaceSelectorObservation(pid, "#typography-menu-trigger"),
                   let target = observation["target"] as? [String: Any],
                   target["value_text"] as? String == preset {
                    after = target
                    break
                }
                Thread.sleep(forTimeInterval: 0.03)
            } while ProcessInfo.processInfo.systemUptime < selectionDeadline
            break
        }
        Thread.sleep(forTimeInterval: 0.03)
    } while ProcessInfo.processInfo.systemUptime < deadline
    guard let after else { return nil }
    return [
        "accepted": true,
        "kind": "typography-preset",
        "transport": "ax-press-menuitemradio",
        "pid": Int(pid),
        "requested_preset": preset,
        "before": before,
        "after": after,
        "value_readback": preset,
    ]
}

func localResultRows(_ elements: [AXUIElement]) -> [AXUIElement]? {
    guard let results = elementForToken("local-results", elements) else { return nil }
    let rows = workspaceAmendmentDescendants(results, maximum: 512).filter {
        stringAttribute($0, kAXRoleAttribute) == "AXButton" &&
            axFrame($0).map { $0.width > 0 && $0.height > 0 } == true
    }
    guard !rows.isEmpty, rows.count <= 128 else { return nil }
    return rows.sorted {
        guard let left = axFrame($0), let right = axFrame($1) else { return false }
        if abs(left.minY - right.minY) > 1 { return left.minY < right.minY }
        return left.minX < right.minX
    }
}

func localScrollObservation(_ pid: pid_t) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let scrollArea = elementForToken("local-results", elements),
          let scrollFrame = axFrame(scrollArea),
          let rows = localResultRows(elements) else { return nil }
    let visibleBounds = scrollFrame.intersection(workspaceWindow.cgFrame)
    guard !visibleBounds.isNull,
          visibleBounds.width > 0,
          visibleBounds.height > 0 else { return nil }
    let selected = rows.enumerated().filter { elementIsSelected($0.element) }
    guard selected.count <= 1 else { return nil }
    var scrollbarRecord: [String: Any] = ["present": false]
    let vertical = elementAttribute(scrollArea, kAXVerticalScrollBarAttribute as String)
        ?? boundedDescendants(scrollArea, maximum: 256).first(where: {
            stringAttribute($0, kAXRoleAttribute) == "AXScrollBar" &&
                stringAttribute($0, kAXOrientationAttribute).lowercased().contains("vertical")
        })
    if let vertical,
       let frame = axFrame(vertical),
       let value = numberAttribute(vertical, kAXValueAttribute),
       let minimum = numberAttribute(vertical, kAXMinValueAttribute),
       let maximum = numberAttribute(vertical, kAXMaxValueAttribute),
       value.isFinite,
       minimum.isFinite,
       maximum.isFinite,
       minimum <= value,
       value <= maximum {
        scrollbarRecord = [
            "present": true,
            "frame": integerFrame(frame),
            "value": value,
            "minimum": minimum,
            "maximum": maximum,
        ]
    }
    let visibleCount = rows.compactMap(axFrame).filter {
        let intersection = $0.intersection(visibleBounds)
        return !intersection.isNull && intersection.width > 0 && intersection.height > 0
    }.count
    var selectedRecord: [String: Any] = ["present": false]
    if let selectedEntry = selected.first,
       let selectedFrame = axFrame(selectedEntry.element) {
        let selectedIntersection = selectedFrame.intersection(visibleBounds)
        let selectedVisible = !selectedIntersection.isNull &&
            selectedIntersection.width > 0 && selectedIntersection.height > 0
        selectedRecord = [
            "present": true,
            "selected_row_index": selectedEntry.offset,
            "frame": integerFrame(selectedFrame),
            "visible": selectedVisible,
            "focused": boolAttribute(selectedEntry.element, kAXFocusedAttribute),
            "selected": true,
        ]
    }
    return [
        "accepted": true,
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "scroll_area_frame": integerFrame(scrollFrame),
        "visible_bounds": integerFrame(visibleBounds),
        "vertical_scrollbar": scrollbarRecord,
        "result_count": rows.count,
        "visible_result_count": visibleCount,
        "selected_row": selectedRecord,
    ]
}

struct WorkspaceProcessRecord {
    let pid: pid_t
    let ppid: pid_t
    let executablePath: String
}

func sha256Text(_ value: String) -> String {
    return SHA256.hash(data: Data(value.utf8)).map {
        String(format: "%02x", $0)
    }.joined()
}

func workspaceProcessTree(_ rootPID: pid_t) -> [WorkspaceProcessRecord]? {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/ps")
    process.arguments = ["-axo", "pid=,ppid=,comm="]
    let output = Pipe()
    process.standardOutput = output
    process.standardError = Pipe()
    do {
        try process.run()
        process.waitUntilExit()
    } catch {
        return nil
    }
    guard process.terminationStatus == 0,
          let text = String(data: output.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) else {
        return nil
    }
    var records: [pid_t: WorkspaceProcessRecord] = [:]
    var children: [pid_t: [pid_t]] = [:]
    for line in text.split(whereSeparator: \.isNewline) {
        let columns = line.split(
            maxSplits: 2,
            omittingEmptySubsequences: true,
            whereSeparator: \.isWhitespace
        )
        guard columns.count == 3,
              let pid = Int32(columns[0]),
              let parent = Int32(columns[1]),
              pid > 0,
              parent >= 0 else { continue }
        records[pid] = WorkspaceProcessRecord(
            pid: pid,
            ppid: parent,
            executablePath: String(columns[2])
        )
        children[parent, default: []].append(pid)
    }
    guard records[rootPID] != nil else { return nil }
    var processIDs: [pid_t] = [rootPID]
    var index = 0
    while index < processIDs.count && processIDs.count <= 64 {
        let current = processIDs[index]
        index += 1
        processIDs.append(contentsOf: children[current] ?? [])
    }
    guard processIDs.count <= 64 else { return nil }
    return processIDs.compactMap { records[$0] }.sorted { $0.pid < $1.pid }
}

func workspaceWebKitRole(_ executableName: String) -> String? {
    switch executableName {
    case "com.apple.WebKit.WebContent": return "webkit_web_content"
    case "com.apple.WebKit.Networking": return "webkit_networking"
    case "com.apple.WebKit.GPU": return "webkit_gpu"
    default: return nil
    }
}

func workspaceWebKitCanonicalPathClass(
    _ executablePath: String,
    executableName: String
) -> String? {
    let resolved = URL(fileURLWithPath: executablePath)
        .resolvingSymlinksInPath().path
    let frameworkMarker = "/System/Library/Frameworks/WebKit.framework/"
    let executableSuffix = "/XPCServices/\(executableName).xpc/Contents/MacOS/\(executableName)"
    let systemPrefix = resolved.hasPrefix("/System/Library/") ||
        resolved.hasPrefix(
            "/System/Volumes/Preboot/Cryptexes/OS/System/Library/"
        )
    guard systemPrefix,
          resolved.contains(frameworkMarker),
          resolved.hasSuffix(executableSuffix) else { return nil }
    return "system-webkit-framework-xpc"
}

func workspaceAppleCodeSignatureIdentifier(
    _ executablePath: String,
    expectedIdentifier: String
) -> String? {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/codesign")
    process.arguments = ["-dr", "-", executablePath]
    let output = Pipe()
    process.standardOutput = output
    process.standardError = output
    do {
        try process.run()
        process.waitUntilExit()
    } catch {
        return nil
    }
    guard process.terminationStatus == 0,
          let text = String(
              data: output.fileHandleForReading.readDataToEndOfFile(),
              encoding: .utf8
          ),
          text.contains("identifier \"\(expectedIdentifier)\""),
          text.contains("anchor apple") else { return nil }
    return expectedIdentifier
}

func workspaceParentChainReachesRoot(
    _ record: WorkspaceProcessRecord,
    recordsByPID: [pid_t: WorkspaceProcessRecord],
    rootPID: pid_t
) -> Bool {
    var current = record
    var visited = Set<pid_t>()
    while current.pid != rootPID && visited.count <= 64 {
        guard visited.insert(current.pid).inserted,
              let parent = recordsByPID[current.ppid] else { return false }
        current = parent
    }
    return current.pid == rootPID
}

func workspaceNetworkSample(
    _ rootPID: pid_t,
    expectedMainExecutablePath: String
) -> [String: Any]? {
    guard let tree = workspaceProcessTree(rootPID),
          let main = tree.first(where: { $0.pid == rootPID }),
          main.executablePath == expectedMainExecutablePath else { return nil }
    let recordsByPID = Dictionary(uniqueKeysWithValues: tree.map { ($0.pid, $0) })
    let helpers = tree.compactMap { record -> [String: Any]? in
        guard record.pid != rootPID else { return nil }
        let name = URL(fileURLWithPath: record.executablePath).lastPathComponent
        guard let role = workspaceWebKitRole(name),
              let pathClass = workspaceWebKitCanonicalPathClass(
                  record.executablePath,
                  executableName: name
              ),
              let bundleIdentifier = workspaceAppleCodeSignatureIdentifier(
                  record.executablePath,
                  expectedIdentifier: name
              ),
              workspaceParentChainReachesRoot(
                  record,
                  recordsByPID: recordsByPID,
                  rootPID: rootPID
              ) else { return nil }
        return [
            "pid": Int(record.pid),
            "ppid": Int(record.ppid),
            "role": role,
            "executable_name": name,
            "executable_path_sha256": sha256Text(record.executablePath),
            "canonical_path_class": pathClass,
            "bundle_identifier": bundleIdentifier,
            "signature_anchor": "apple",
            "parent_chain_verified": true,
        ]
    }
    let helperRoles = Set(helpers.compactMap { $0["role"] as? String })
    guard helperRoles.contains("webkit_web_content"),
          helperRoles.contains("webkit_networking") else { return nil }
    let scopedPIDs = ([rootPID] + helpers.compactMap { item in
        (item["pid"] as? Int).flatMap(pid_t.init)
    }).sorted()
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
    process.arguments = [
        "-nP",
        "-a",
        "-p",
        scopedPIDs.map(String.init).joined(separator: ","),
        "-FpcftPn",
    ]
    let output = Pipe()
    process.standardOutput = output
    process.standardError = Pipe()
    do {
        try process.run()
        process.waitUntilExit()
    } catch {
        return nil
    }
    // This command lists all descriptors for verified processes, so zero network
    // endpoints still has exit status 0. Any non-zero status is incomplete proof.
    guard process.terminationStatus == 0,
          let text = String(data: output.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) else {
        return nil
    }
    var currentPID: String?
    var currentType = ""
    var currentProtocol = ""
    var currentName = ""
    var endpoints: [String] = []
    func appendCurrentEndpoint() {
        guard let currentPID,
              currentType == "IPv4" || currentType == "IPv6" || !currentProtocol.isEmpty else {
            return
        }
        endpoints.append(
            [currentPID, currentType, currentProtocol, currentName].joined(separator: "|")
        )
    }
    for rawLine in text.split(whereSeparator: \.isNewline) {
        guard let prefix = rawLine.first else { continue }
        let value = String(rawLine.dropFirst())
        switch prefix {
        case "p":
            appendCurrentEndpoint()
            currentPID = value
            currentType = ""
            currentProtocol = ""
            currentName = ""
        case "f":
            appendCurrentEndpoint()
            currentType = ""
            currentProtocol = ""
            currentName = ""
        case "t": currentType = value
        case "P": currentProtocol = value
        case "n": currentName = value
        default: break
        }
    }
    appendCurrentEndpoint()
    let normalized = Array(Set(endpoints)).sorted()
    let observed = DispatchTime.now().uptimeNanoseconds
    return [
        "accepted": true,
        "pid": Int(rootPID),
        "sample_sequence": Int(min(observed, UInt64(Int.max))),
        "observed_monotonic_ns": Int(min(observed, UInt64(Int.max))),
        "lsof_exit_status": Int(process.terminationStatus),
        "process_scope": "verified-main-and-webkit-helper-descendants",
        "main_process": [
            "pid": Int(main.pid),
            "role": "main",
            "executable_path_sha256": sha256Text(main.executablePath),
        ],
        "webkit_helpers": helpers,
        "endpoint_count": normalized.count,
        "endpoint_fingerprint": sha256Text(normalized.joined(separator: "\n")),
    ]
}

func sourcePreviewSafetyObservation(_ pid: pid_t) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let preview = elementForToken("typography-source-preview-body", elements),
          let frame = axFrame(preview), frame.width > 0, frame.height > 0 else { return nil }
    let descendants = boundedDescendants(preview, maximum: 1024)
    let forbiddenRoleCounts = [
        "link": descendants.filter { stringAttribute($0, kAXRoleAttribute) == "AXLink" }.count,
        "image": descendants.filter { stringAttribute($0, kAXRoleAttribute) == "AXImage" }.count,
        "embedded_web_area": descendants.filter {
            stringAttribute($0, kAXRoleAttribute) == "AXWebArea"
        }.count,
    ]
    let semanticRoleOrder = descendants.compactMap { element -> String? in
        switch stringAttribute(element, kAXRoleAttribute) {
        case "AXHeading": return "heading"
        case "AXList": return "list"
        case "AXTable": return "table"
        default: return nil
        }
    }
    let expectedSemanticRoleOrder = ["heading", "list", "list", "table"]
    let selected = (localResultRows(elements) ?? []).filter {
        elementName($0) == "runtime-verification" && elementIsSelected($0)
    }
    let headings = elements.filter {
        stringAttribute($0, kAXRoleAttribute) == "AXHeading" &&
            elementName($0) == "전체 원문"
    }
    let webAreas = elements.filter {
        stringAttribute($0, kAXRoleAttribute) == "AXWebArea"
    }
    guard selected.count == 1,
          headings.count == 1,
          webAreas.count == 1,
          let urlClass = workspaceWebAreaURLClass(urlAttributeString(webAreas[0])) else {
        return nil
    }
    return [
        "accepted": true,
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "preview_present": true,
        "preview_frame": integerFrame(frame),
        "selected_display_name": "runtime-verification",
        "selected_state_observed": true,
        "preview_heading": "전체 원문",
        "render_completion_observed": semanticRoleOrder == expectedSemanticRoleOrder,
        "rendered_semantic_count": semanticRoleOrder.count,
        "semantic_role_order": semanticRoleOrder,
        "forbidden_ax_role_counts": forbiddenRoleCounts,
        "app_web_area_identity": [
            "role": "AXWebArea",
            "url_class": urlClass,
            "window_id": workspaceWindow.windowID,
        ],
    ]
}

func selectWorkspaceLocalFixture(
    _ pid: pid_t,
    displayName: String
) -> [String: Any]? {
    guard displayName == "runtime-verification",
          let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let rows = localResultRows(elements) else { return nil }
    let matches = rows.filter { elementName($0) == displayName }
    guard matches.count == 1,
          let frame = axFrame(matches[0]),
          frameContains(workspaceWindow.cgFrame, frame),
          let source = CGEventSource(stateID: .hidSystemState),
          let down = CGEvent(
              mouseEventSource: source,
              mouseType: .leftMouseDown,
              mouseCursorPosition: CGPoint(x: frame.midX, y: frame.midY),
              mouseButton: .left
          ),
          let up = CGEvent(
              mouseEventSource: source,
              mouseType: .leftMouseUp,
              mouseCursorPosition: CGPoint(x: frame.midX, y: frame.midY),
              mouseButton: .left
          ) else { return nil }
    down.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.03)
    up.post(tap: .cghidEventTap)
    let deadline = ProcessInfo.processInfo.systemUptime + 1.5
    repeat {
        guard let activeWindow = workspaceMainWindow(pid),
              activeWindow.windowID == workspaceWindow.windowID,
              let activeRows = localResultRows(
                  boundedDescendants(activeWindow.element)
              ) else { return nil }
        let selected = activeRows.filter {
            elementName($0) == displayName && elementIsSelected($0)
        }
        if selected.count == 1 {
            return [
                "accepted": true,
                "pid": Int(pid),
                "window_id": activeWindow.windowID,
                "transport": "cg-event-mouse-click",
                "requested_display_name": displayName,
                "selected_display_name": displayName,
                "selection_activation_observed": true,
            ]
        }
        Thread.sleep(forTimeInterval: 0.03)
    } while ProcessInfo.processInfo.systemUptime < deadline
    return nil
}

func selectWorkspaceLocalResult(
    _ pid: pid_t,
    _ input: String
) -> [String: Any]? {
    func clickLocalResult(_ frame: CGRect) -> Bool {
        guard let source = CGEventSource(stateID: .hidSystemState) else { return false }
        let point = CGPoint(x: frame.midX, y: frame.midY)
        guard let down = CGEvent(mouseEventSource: source,
                  mouseType: .leftMouseDown,
                  mouseCursorPosition: point,
                  mouseButton: .left
              ),
              let up = CGEvent(mouseEventSource: source,
                  mouseType: .leftMouseUp,
                  mouseCursorPosition: point,
                  mouseButton: .left
              ) else { return false }
        down.post(tap: .cghidEventTap)
        Thread.sleep(forTimeInterval: 0.03)
        up.post(tap: .cghidEventTap)
        return true
    }
    func pressLocalArrowDown() -> Bool {
        guard let source = CGEventSource(stateID: .hidSystemState),
              let down = CGEvent(keyboardEventSource: source,
                  virtualKey: 125,
                  keyDown: true
              ),
              let up = CGEvent(keyboardEventSource: source,
                  virtualKey: 125,
                  keyDown: false
              ) else { return false }
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
        return true
    }
    guard ["mouse", "keyboard"].contains(input),
          let before = localScrollObservation(pid),
          let beforeRow = before["selected_row"] as? [String: Any],
          let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let beforeIndex = beforeRow["selected_row_index"] as? Int
    let elements = boundedDescendants(workspaceWindow.element)
    guard let rows = localResultRows(elements),
          beforeIndex == nil || rows.indices.contains(beforeIndex!) else { return nil }
    let transport: String
    if input == "mouse" {
        let visibleBoundsRecord = before["visible_bounds"] as? [String: Int]
        guard let x = visibleBoundsRecord?["x"],
              let y = visibleBoundsRecord?["y"],
              let width = visibleBoundsRecord?["width"],
              let height = visibleBoundsRecord?["height"] else { return nil }
        let visibleBounds = CGRect(x: x, y: y, width: width, height: height)
        let candidates = rows.enumerated().filter { index, row in
            guard index != beforeIndex, let frame = axFrame(row) else { return false }
            let intersection = frame.intersection(visibleBounds)
            return !intersection.isNull && intersection.width > 0 && intersection.height > 0
        }
        guard let candidate = candidates.first,
              let frame = axFrame(candidate.element),
              clickLocalResult(frame) else { return nil }
        transport = "cg-event-mouse-click"
    } else {
        guard let beforeIndex,
              AXUIElementSetAttributeValue(
              rows[beforeIndex],
                  kAXFocusedAttribute as CFString,
                  kCFBooleanTrue
              ) == .success,
              pressLocalArrowDown() else { return nil }
        transport = "cg-event-keyboard-arrow-down"
    }
    let deadline = ProcessInfo.processInfo.systemUptime + 1.5
    var after: [String: Any]?
    repeat {
        if let candidate = localScrollObservation(pid),
           let row = candidate["selected_row"] as? [String: Any],
           let index = row["selected_row_index"] as? Int,
           beforeIndex == nil || index != beforeIndex {
            after = candidate
            break
        }
        Thread.sleep(forTimeInterval: 0.03)
    } while ProcessInfo.processInfo.systemUptime < deadline
    guard let after else { return nil }
    return [
        "accepted": true,
        "kind": "local-result-selection",
        "transport": transport,
        "input": input,
        "pid": Int(pid),
        "selection_changed": true,
        "before": before,
        "after": after,
    ]
}

func redactedProjectScopeRecord(_ element: AXUIElement) -> [String: Any]? {
    guard let frame = axFrame(element),
          frame.width > 0,
          frame.height > 0 else { return nil }
    let role = stringAttribute(element, kAXRoleAttribute)
    guard ["AXButton", "AXRow"].contains(role) else { return nil }
    return [
        "role": role,
        "frame": integerFrame(frame),
        "visible": !boolAttribute(element, "AXHidden"),
        "selected": elementIsSelected(element),
    ]
}

func selectWorkspaceProjectScope(_ pid: pid_t) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    let candidates = elements.filter {
        elementIdentifier($0).hasPrefix("local-project-scope-") &&
        ["AXButton", "AXRow"].contains(
            stringAttribute($0, kAXRoleAttribute)
        ) &&
            axFrame($0).map { $0.width > 0 && $0.height > 0 } == true &&
            !elementName($0).isEmpty
    }.sorted {
        let left = elementIdentifier($0).dropFirst("local-project-scope-".count)
        let right = elementIdentifier($1).dropFirst("local-project-scope-".count)
        return (Int(left) ?? Int.max) < (Int(right) ?? Int.max)
    }
    guard let project = candidates.first,
          AXUIElementPerformAction(project, kAXPressAction as CFString) == .success
    else { return nil }
    let deadline = ProcessInfo.processInfo.systemUptime + 1.5
    var selectedProject: [String: Any]?
    var ignoreAction: [String: Any]?
    repeat {
        selectedProject = redactedProjectScopeRecord(project)
        ignoreAction = workspaceSelectorObservation(pid, "#project-ignore-open")
        if selectedProject?["selected"] as? Bool == true,
           ignoreAction?["present"] as? Bool == true {
            break
        }
        Thread.sleep(forTimeInterval: 0.03)
    } while ProcessInfo.processInfo.systemUptime < deadline
    guard let selectedProject,
          selectedProject["selected"] as? Bool == true,
          let ignoreAction,
          ignoreAction["present"] as? Bool == true else { return nil }
    return [
        "accepted": true,
        "kind": "local-project-scope-selection",
        "transport": "ax-press",
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "project_identity_redacted": true,
        "selected_project": selectedProject,
        "ignore_action": ignoreAction,
    ]
}

func localPublicationObservation(_ pid: pid_t) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let statusElement = workspaceElementForSelector(
              "#local-runtime-status", elements
          ),
          let snapshotElement = workspaceElementForSelector(
              "#local-runtime-snapshot", elements
          ) else { return nil }
    let status = elementName(statusElement, limit: 320)
    let snapshot = elementName(snapshotElement, limit: 96)
    guard snapshot.hasPrefix("snapshot ") else { return nil }
    let prefix = String(snapshot.dropFirst("snapshot ".count))
    guard (6...64).contains(prefix.count),
          prefix.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber) })
    else { return nil }
    let phase: String
    if status.contains("완료 후 제외 규칙 반영 스캔") ||
       status.contains("Filesystem 작업 대기") ||
       status.contains("이미 로컬 하네스 스캔 중") {
        phase = "queued"
    } else if status.contains("스캔 중") || status.contains("스캔을 시작") {
        phase = "running"
    } else if status.contains("마지막 유효 snapshot") {
        phase = "failed"
    } else if status.contains("일부 coverage issue") {
        phase = "partial"
    } else {
        phase = "complete"
    }
    let ignoreSaveOutcome: String
    if status.contains("저장하고 새 스캔을 시작") {
        ignoreSaveOutcome = "accepted"
    } else if status.contains("완료 후 제외 규칙 반영 스캔") {
        ignoreSaveOutcome = "queued"
    } else if status.contains("제외 규칙은 저장했지만 다른 작업 중") {
        ignoreSaveOutcome = "busy"
    } else {
        ignoreSaveOutcome = "none"
    }
    let resultCount = localResultRows(elements)?.count ?? 0
    return [
        "accepted": true,
        "kind": "local-publication",
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "snapshot_identity_prefix": prefix,
        "last_complete_visible": true,
        "scan_phase": phase,
        "ignore_save_outcome": ignoreSaveOutcome,
        "result_count": resultCount,
    ]
}

func toolbarLayoutObservation(_ pid: pid_t) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    let specifications: [(name: String, element: AXUIElement?)] = [
        ("left-disclosure", workspaceElementForSelector("#left-pane-disclosure", elements)),
        ("identity", elementForToken("app-identity", elements)),
        ("repository", elementForToken("checkout-path", elements)),
        ("checkout-register", workspaceElementForSelector("#register-checkout", elements)),
        ("load-sot", workspaceElementForSelector("#load-sot", elements)),
        ("appearance", elementForToken("appearance-mode", elements)),
        ("typography", workspaceElementForSelector("#typography-menu-trigger", elements)),
        ("right-disclosure", workspaceElementForSelector("#right-pane-disclosure", elements)),
    ]
    var targets: [String: [String: Any]] = [:]
    for specification in specifications {
        guard let element = specification.element,
              let frame = axFrame(element),
              frame.width > 0,
              frame.height > 0 else { return nil }
        targets[specification.name] = [
            "role": stringAttribute(element, kAXRoleAttribute),
            "frame": integerFrame(frame),
            "visible": !boolAttribute(element, "AXHidden"),
            "focusable": elementIsFocusable(element),
        ]
    }
    guard targets.count == specifications.count else { return nil }
    guard let tabOrder = toolbarTabOrder(pid, elements: elements) else { return nil }
    return [
        "accepted": true,
        "kind": "toolbar-layout",
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "window_frame": integerFrame(workspaceWindow.cgFrame),
        "target_count": targets.count,
        "tab_order": tabOrder,
        "targets": targets,
    ]
}

func toolbarTabOrder(
    _ pid: pid_t,
    elements: [AXUIElement]
) -> [String]? {
    let appearance = elements.filter {
        stringAttribute($0, kAXRoleAttribute) == "AXRadioButton" &&
            ["System", "Light", "Dark"].contains(elementName($0))
    }
    let groups: [(name: String, elements: [AXUIElement])] = [
        ("left-disclosure", [workspaceElementForSelector("#left-pane-disclosure", elements)].compactMap { $0 }),
        ("repository", [elementForToken("checkout-path", elements)].compactMap { $0 }),
        ("checkout-register", [workspaceElementForSelector("#register-checkout", elements)].compactMap { $0 }),
        ("load-sot", [workspaceElementForSelector("#load-sot", elements)].compactMap { $0 }),
        ("appearance", appearance),
        ("typography", [workspaceElementForSelector("#typography-menu-trigger", elements)].compactMap { $0 }),
        ("right-disclosure", [workspaceElementForSelector("#right-pane-disclosure", elements)].compactMap { $0 }),
    ]
    guard groups.allSatisfy({ !$0.elements.isEmpty }),
          let first = groups.first?.elements.first else { return nil }
    let application = AXUIElementCreateApplication(pid)
    guard AXUIElementSetAttributeValue(
        first,
        kAXFocusedAttribute as CFString,
        kCFBooleanTrue
    ) == .success else { return nil }
    Thread.sleep(forTimeInterval: 0.08)

    func focusedGroup() -> String? {
        guard let focused = attribute(
            application,
            kAXFocusedUIElementAttribute
        ) else { return nil }
        return groups.first(where: { group in
            group.elements.contains(where: { CFEqual(focused, $0) })
        })?.name
    }

    var observed: [String] = []
    for index in groups.indices {
        guard let focused = focusedGroup(), focused == groups[index].name else {
            return nil
        }
        observed.append(focused)
        if index < groups.index(before: groups.endIndex) {
            guard postKeyboardKey(48) else { return nil }
            Thread.sleep(forTimeInterval: 0.08)
        }
    }
    return observed
}

func workspaceLayoutSnapshot(_ pid: pid_t) -> [String: Any]? {
    guard let window = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(window.element, maximum: 1024)
    let targetTokens = [
        "workspace-shell", "left-pane", "center-pane", "right-pane",
        "left-divider", "right-divider", "preferred-widths",
    ]
    var targets: [[String: Any]] = []
    for token in targetTokens {
        guard let element = elementForToken(token, elements),
              let record = targetRecord(token, element) else { return nil }
        targets.append(record)
    }

    func frameFor(_ token: String) -> CGRect? {
        elementForToken(token, elements).flatMap(axFrame)
    }
    func numericTargetValue(_ token: String, _ attributeName: String) -> Double? {
        guard let element = elementForToken(token, elements) else { return nil }
        return numberAttribute(element, attributeName)
    }
    guard let axWindowFrame = axFrame(window.element),
          let shell = frameFor("workspace-shell"),
          let left = frameFor("left-pane"),
          let center = frameFor("center-pane"),
          let right = frameFor("right-pane"),
          let leftDivider = frameFor("left-divider"),
          let rightDivider = frameFor("right-divider"),
          let preferredElement = elementForToken("preferred-widths", elements),
          let preferredPair = preferredPairFromText(elementName(preferredElement)),
          let leftCurrent = numericTargetValue("left-divider", kAXValueAttribute),
          let leftMinimum = numericTargetValue("left-divider", kAXMinValueAttribute),
          let leftMaximum = numericTargetValue("left-divider", kAXMaxValueAttribute),
          let rightCurrent = numericTargetValue("right-divider", kAXValueAttribute),
          let rightMinimum = numericTargetValue("right-divider", kAXMinValueAttribute),
          let rightMaximum = numericTargetValue("right-divider", kAXMaxValueAttribute),
          let status = elementForToken("layout-status", elements).map(elementName),
          let layoutRevision = integerFromText(status) else { return nil }

    let tolerance: CGFloat = 2
    let cgWindowFrame = window.cgFrame
    let leftGap = center.minX - left.maxX
    let rightGap = right.minX - center.maxX
    let windowFrameMatch = framesMatch(axWindowFrame, cgWindowFrame)
    let allPanesContained = [left, center, right].allSatisfy {
        frameContains(shell, $0)
    }
    let allDividersContained = [leftDivider, rightDivider].allSatisfy {
        frameContains(shell, $0)
    }
    let dividerTiling =
        abs(leftGap - leftDivider.width) <= tolerance &&
        abs(rightGap - rightDivider.width) <= tolerance &&
        abs(leftDivider.minX - left.maxX) <= tolerance &&
        abs(leftDivider.maxX - center.minX) <= tolerance &&
        abs(rightDivider.minX - center.maxX) <= tolerance &&
        abs(rightDivider.maxX - right.minX) <= tolerance
    let sideBoundsUsable =
        leftMinimum <= leftCurrent && leftCurrent <= leftMaximum &&
        rightMinimum <= rightCurrent && rightCurrent <= rightMaximum &&
        abs(left.width - leftCurrent) <= tolerance &&
        abs(right.width - rightCurrent) <= tolerance
    let responsiveUsable =
        windowFrameMatch &&
        frameContains(axWindowFrame, shell) &&
        frameContains(cgWindowFrame, shell) &&
        allPanesContained &&
        allDividersContained &&
        dividerTiling &&
        sideBoundsUsable &&
        left.maxX <= center.minX + tolerance &&
        center.maxX <= right.minX + tolerance &&
        center.width >= 480 - tolerance
    guard responsiveUsable else { return nil }

    let effectivePair: [String: Int] = [
        "left_px": Int(left.width.rounded()),
        "right_px": Int(right.width.rounded()),
    ]
    let separatorState: [String: Any] = [
        "left": [
            "focused": elementForToken("left-divider", elements)
                .map { boolAttribute($0, kAXFocusedAttribute) } ?? false,
            "current_value": leftCurrent,
            "minimum_value": leftMinimum,
            "maximum_value": leftMaximum,
        ],
        "right": [
            "focused": elementForToken("right-divider", elements)
                .map { boolAttribute($0, kAXFocusedAttribute) } ?? false,
            "current_value": rightCurrent,
            "minimum_value": rightMinimum,
            "maximum_value": rightMaximum,
        ],
    ]
    let physicalGeometry: [String: Any] = [
        "ax_window": integerFrame(axWindowFrame),
        "cg_window": integerFrame(cgWindowFrame),
        "workspace_shell": integerFrame(shell),
        "window_frame_match": true,
        "panes": [
            "left": integerFrame(left),
            "center": integerFrame(center),
            "right": integerFrame(right),
        ],
        "dividers": [
            "left": integerFrame(leftDivider),
            "right": integerFrame(rightDivider),
        ],
        "tiling": [
            "left_gap_px": Int(leftGap.rounded()),
            "right_gap_px": Int(rightGap.rounded()),
            "divider_widths_match": true,
        ],
        "containment": [
            "workspace_shell_in_ax_window": true,
            "workspace_shell_in_cg_window": true,
            "all_panes": true,
            "all_dividers": true,
        ],
        "side_bounds": [
            "left": [
                "pane_width_px": Int(left.width.rounded()),
                "current_value": leftCurrent,
                "minimum_value": leftMinimum,
                "maximum_value": leftMaximum,
                "within_bounds": true,
                "pane_width_matches_value": true,
            ],
            "right": [
                "pane_width_px": Int(right.width.rounded()),
                "current_value": rightCurrent,
                "minimum_value": rightMinimum,
                "maximum_value": rightMaximum,
                "within_bounds": true,
                "pane_width_matches_value": true,
            ],
        ],
        "responsive": ["mode": "three-pane", "usable": true],
    ]
    var payload: [String: Any] = [
        "accepted": true,
        "pid": Int(pid),
        "window_id": window.windowID,
        "window_frame": integerFrame(axWindowFrame),
        "layout_mode": "three-pane",
        "preferred_pair": preferredPair,
        "effective_pair": effectivePair,
        "separator_state": separatorState,
        "layout_revision": layoutRevision,
        "persisted": status.contains("저장 완료"),
        "targets": targets,
        "physical_geometry": physicalGeometry,
    ]
    if status == "레이아웃 설정 저장 실패" {
        payload["persisted"] = false
        payload["diagnostic"] = [
            "code": "workspace_layout_preference_write_failed",
            "message": status,
        ]
    }
    return payload
}

func workspaceLayoutMode(
    left: CGRect?,
    center: CGRect,
    right: CGRect?,
    leftDivider: CGRect?,
    rightDivider: CGRect?,
    tolerance: CGFloat = 2
) -> String? {
    let leftCollapsed = left == nil && leftDivider == nil
    let rightCollapsed = right == nil && rightDivider == nil
    guard leftCollapsed || (left != nil && leftDivider != nil),
          rightCollapsed || (right != nil && rightDivider != nil) else {
        return nil
    }
    if leftCollapsed {
        return rightCollapsed ? "center-only" : "left-collapsed"
    }
    if rightCollapsed {
        return "right-collapsed"
    }
    guard let left, let right else { return nil }
    let sameColumn = abs(center.minX - right.minX) <= tolerance &&
        abs(center.width - right.width) <= tolerance
    let sameStack = abs(left.minX - center.minX) <= tolerance &&
        abs(center.minX - right.minX) <= tolerance &&
        abs(left.width - center.width) <= tolerance &&
        abs(center.width - right.width) <= tolerance
    if left.maxX <= center.minX + tolerance &&
       center.maxX <= right.minX + tolerance {
        return "three-pane"
    }
    if left.maxX <= center.minX + tolerance &&
       sameColumn &&
       center.maxY <= right.minY + tolerance {
        return "two-column"
    }
    if sameStack &&
       left.maxY <= center.minY + tolerance &&
       center.maxY <= right.minY + tolerance {
        return "stacked"
    }
    return nil
}

func workspaceSteadyToken(
    _ workspaceWindow: (element: AXUIElement, windowID: Int, cgFrame: CGRect),
    _ elements: [AXUIElement],
    _ anchorComponentIDs: [String]
) -> [String: Any]? {
    guard anchorComponentIDs.isEmpty || anchorComponentIDs.count == 3,
          let windowFrame = axFrame(workspaceWindow.element),
          let shell = elementForToken("workspace-shell", elements).flatMap(axFrame),
          let center = elementForToken("center-pane", elements).flatMap(axFrame),
          let status = elementForToken("layout-status", elements).map(elementName),
          let layoutRevision = integerFromText(status) else { return nil }

    func optionalFrame(_ token: String) -> Any {
        elementForToken(token, elements).flatMap(axFrame).map(integerFrame) ?? NSNull()
    }
    func expanded(_ token: String) -> Any {
        guard let element = elementForToken(token, elements),
              let value = attribute(element, kAXExpandedAttribute) as? NSNumber else {
            return NSNull()
        }
        return value.boolValue
    }
    func selectedRelationNodeID() -> (valid: Bool, value: Any) {
        let matches = elements.filter {
            elementIdentifier($0).hasPrefix("semantic-relation:") && elementIsSelected($0)
        }
        guard matches.count <= 1 else { return (false, NSNull()) }
        guard let match = matches.first,
              let identity = semanticIdentityFromIdentifier(elementIdentifier(match)) else {
            return matches.isEmpty ? (true, NSNull()) : (false, NSNull())
        }
        guard let nodeID = identity["node_id"] as? String else {
            return (false, NSNull())
        }
        return (true, nodeID)
    }
    func selectedLockedWorkflowStep() -> (valid: Bool, value: Any) {
        let matches = elements.filter {
            elementIdentifier($0).hasPrefix("semantic-workflow-step:") && elementIsSelected($0)
        }
        guard matches.count <= 1 else { return (false, NSNull()) }
        guard let match = matches.first,
              let identity = semanticIdentityFromIdentifier(elementIdentifier(match)) else {
            return matches.isEmpty ? (true, NSNull()) : (false, NSNull())
        }
        guard let workflowID = identity["workflow_id"] as? String,
              let ordinal = identity["ordinal"] as? Int,
              let stepID = identity["step_id"] as? String else {
            return (false, NSNull())
        }
        return (true, [
            "workflow_id": workflowID,
            "ordinal": ordinal,
            "step_id": stepID,
        ])
    }

    let semanticRoots = elements.filter {
        elementMatchesIdentifier($0, "component-map-semantic-view")
    }
    guard semanticRoots.count <= 1 else { return nil }
    let semanticRoot = semanticRoots.first
    let sceneLayoutIdentity = semanticSceneLayoutIdentityObservation(elements)
    if semanticRoot != nil && sceneLayoutIdentity == nil { return nil }
    let rendererStatuses = elements.filter {
        elementMatchesIdentifier($0, "component-map-renderer-status")
    }
    let retryButtons = elements.filter {
        elementMatchesIdentifier($0, "component-map-renderer-retry")
    }
    let cameraPoses = elements.filter {
        elementMatchesIdentifier($0, "component-map-camera-pose")
    }
    let activeProfiles = elements.filter {
        elementIdentifier($0).hasPrefix("component-map-active-profile:")
    }
    guard rendererStatuses.count <= 1,
          retryButtons.count <= 1,
          cameraPoses.count <= 1,
          activeProfiles.count <= 1 else { return nil }
    let rendererStatus = rendererStatuses.first.map(elementName) ?? ""
    let retryVisible = retryButtons.first.map {
        !boolAttribute($0, "AXHidden") && (axFrame($0)?.width ?? 0) > 0
    } ?? false
    let cameraPose = cameraPoses.first.flatMap(semanticCameraPoseObservation)
    if !cameraPoses.isEmpty && cameraPose == nil { return nil }
    let observedCamera = cameraObservation(elements)
    guard semanticRoot == nil || observedCamera != nil else { return nil }
    var camera = observedCamera ?? [
        "ready": false,
        "scale": NSNull(),
        "level": NSNull(),
        "zoom_out_enabled": false,
        "fit_enabled": false,
        "zoom_in_enabled": false,
    ]
    if let cameraPose,
       let position = cameraPose["position"] as? [String: Double],
       let target = cameraPose["target"] as? [String: Double] {
        camera["position"] = position
        camera["target"] = target
    }
    let cameraReady = camera["ready"] as? Bool ?? false
    let activeProfile = activeProfiles.first.flatMap(semanticActiveProfileObservation)
    if !activeProfiles.isEmpty && activeProfile == nil { return nil }
    let selectedRelation = selectedRelationNodeID()
    let lockedWorkflowStep = selectedLockedWorkflowStep()
    guard selectedRelation.valid, lockedWorkflowStep.valid else { return nil }
    let selectedComponent = elementForToken("selected-component", elements)
        .flatMap { canonicalComponentIDFromName(elementName($0)) }
    let anchorNodes: [[String: Any]] = anchorComponentIDs.map { componentID in
        let element = elementForToken("semantic-component:\(componentID)", elements)
        return [
            "component_id": componentID,
            "frame": element.flatMap(axFrame).map(integerFrame) ?? NSNull(),
            "visible": element.map {
                !boolAttribute($0, "AXHidden") && (axFrame($0)?.width ?? 0) > 0
            } ?? false,
            "selected": element.map(elementIsSelected) ?? false,
        ]
    }
    let left = elementForToken("left-pane", elements).flatMap(axFrame)
    let right = elementForToken("right-pane", elements).flatMap(axFrame)
    let leftDivider = elementForToken("left-divider", elements).flatMap(axFrame)
    let rightDivider = elementForToken("right-divider", elements).flatMap(axFrame)
    guard let layoutMode = workspaceLayoutMode(
        left: left,
        center: center,
        right: right,
        leftDivider: leftDivider,
        rightDivider: rightDivider
    ) else { return nil }
    let rendererAvailability: String
    if semanticRoot == nil {
        rendererAvailability = "unavailable"
    } else if cameraReady {
        rendererAvailability = "ready"
    } else if retryVisible && rendererStatus == "사용자가 텍스트 보기를 선택했습니다." {
        rendererAvailability = "manual_fallback"
    } else {
        rendererAvailability = "unavailable"
    }
    return [
        "layout_revision": layoutRevision,
        "layout": [
            "mode": layoutMode,
            "window": integerFrame(windowFrame),
            "shell": integerFrame(shell),
            "left": left.map(integerFrame) ?? NSNull(),
            "center": integerFrame(center),
            "right": right.map(integerFrame) ?? NSNull(),
        ],
        "graph_body_identity": elementForToken("graph-body", elements).map {
            elementIdentifier($0)
        } ?? NSNull(),
        "projection_id": sceneLayoutIdentity?["projection_id"] ?? NSNull(),
        "settled_node_positions_hash": sceneLayoutIdentity?[
            "settled_node_positions_hash"
        ] ?? NSNull(),
        "renderer": [
            "availability": rendererAvailability,
            "semantic_view_visible": semanticRoot.map {
                !boolAttribute($0, "AXHidden") && (axFrame($0)?.width ?? 0) > 0
            } ?? false,
            "status": rendererStatus,
            "retry_visible": retryVisible,
        ],
        "camera": camera,
        "active_profile_id": activeProfile ?? NSNull(),
        "selected_component_id": selectedComponent ?? NSNull(),
        "selected_relation_node_id": selectedRelation.value,
        "locked_workflow_step": lockedWorkflowStep.value,
        "right_detail_id": selectedComponent ?? NSNull(),
        "disclosures": [
            "component_map": expanded("graph-toggle"),
            "profile_matrix": expanded("matrix-toggle"),
            "left_pane": workspaceElementForSelector(
                "#left-pane-disclosure", elements
            ).map(elementIsSelected) ?? false,
            "right_pane": workspaceElementForSelector(
                "#right-pane-disclosure", elements
            ).map(elementIsSelected) ?? false,
        ],
        "graph_frames": [
            "body": optionalFrame("graph-body"),
            "viewport": optionalFrame("graph-viewport"),
            "matrix": optionalFrame("matrix"),
            "matrix_body": optionalFrame("matrix-body"),
        ],
        "anchor_nodes": anchorNodes,
    ]
}

func workspaceSteadySnapshot(
    _ pid: pid_t,
    _ anchorComponentIDs: [String]
) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element, maximum: 2048)
    guard let stabilityToken = workspaceSteadyToken(
        workspaceWindow,
        elements,
        anchorComponentIDs
    ) else { return nil }
    return [
        "accepted": true,
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "stability_token": stabilityToken,
        "anchor_target_count": anchorComponentIDs.count,
    ]
}

func workspaceSnapshot(
    _ pid: pid_t,
    _ anchorComponentIDs: [String]
) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    let semanticGraph = semanticGraphObservation(elements)
    let fixedTokens = [
        "workspace-shell", "left-pane", "center-pane", "right-pane",
        "left-divider", "right-divider", "preferred-widths",
        "graph-body", "graph-viewport", "graph-toggle", "graph-zoom", "matrix",
        "graph-tooltip", "graph-legend", "graph-camera", "sot-tree-scroll",
        "dashboard-sot", "dashboard-local", "layout-status",
    ]
    let tooltipIdentity = graphTooltipIdentity(elements)
    var targets: [[String: Any]] = []
    for token in fixedTokens {
        if let element = elementForToken(token, elements),
           var record = targetRecord(token, element) {
            if token == "graph-tooltip",
               let tooltipIdentity,
               let title = tooltipIdentity["title"] as? String,
               let componentID = tooltipIdentity["component_id"] as? String,
               let meta = tooltipIdentity["meta"] as? String {
                record["name"] = "\(title) · \(componentID) · \(meta)"
            }
            targets.append(record)
        }
    }
    if elementForToken("graph-tooltip", elements) == nil,
       let lastComponent = elementForToken("sot-tree-last-component", elements),
       let record = targetRecord("sot-tree-last-component", lastComponent) {
        targets.append(record)
    }
    for selector in [
        "#left-pane-disclosure",
        "#right-pane-disclosure",
        "#typography-menu-trigger",
    ] {
        if let element = workspaceElementForSelector(selector, elements),
           let record = selectorTargetRecord(selector, element) {
            targets.append(record)
        }
    }
    // Canvas-internal graph nodes are deliberately absent from AX targets.
    // The last tree row is omitted while a fixed identity surface is present.
    guard targets.count <= 24 else { return nil }

    func frameFor(_ token: String) -> CGRect? {
        guard let element = elementForToken(token, elements) else { return nil }
        return axFrame(element)
    }
    guard let axWindowFrame = axFrame(workspaceWindow.element),
          let shell = frameFor("workspace-shell"),
          let center = frameFor("center-pane") else { return nil }
    let left = frameFor("left-pane")
    let right = frameFor("right-pane")
    let leftDivider = frameFor("left-divider")
    let rightDivider = frameFor("right-divider")
    let leftCollapsed = left == nil && leftDivider == nil
    let rightCollapsed = right == nil && rightDivider == nil
    guard leftCollapsed || (left != nil && leftDivider != nil),
          rightCollapsed || (right != nil && rightDivider != nil) else { return nil }
    var collapsedSides: [String] = []
    if leftCollapsed { collapsedSides.append("left") }
    if rightCollapsed { collapsedSides.append("right") }
    guard let preferredElement = elementForToken("preferred-widths", elements),
          let preferredPair = preferredPairFromText(elementName(preferredElement)),
          let preferredLeft = preferredPair["left_px"],
          let preferredRight = preferredPair["right_px"] else {
        return nil
    }
    let cgWindowFrame = workspaceWindow.cgFrame
    let tolerance: CGFloat = 2
    guard let layoutMode = workspaceLayoutMode(
        left: left,
        center: center,
        right: right,
        leftDivider: leftDivider,
        rightDivider: rightDivider,
        tolerance: tolerance
    ) else { return nil }
    let sameColumn = left.flatMap { left in
        right.map {
            abs(center.minX - $0.minX) <= tolerance &&
            abs(center.width - $0.width) <= tolerance
        }
    } ?? false
    let sameStack = left.flatMap { left in
        right.map {
            abs(left.minX - center.minX) <= tolerance &&
            abs(center.minX - $0.minX) <= tolerance &&
            abs(left.width - center.width) <= tolerance &&
            abs(center.width - $0.width) <= tolerance
        }
    } ?? false

    func numericTargetValue(_ token: String, _ attributeName: String) -> Double? {
        guard let element = elementForToken(token, elements) else { return nil }
        return numberAttribute(element, attributeName)
    }
    let leftCurrent = numericTargetValue("left-divider", kAXValueAttribute)
    let leftMinimum = numericTargetValue("left-divider", kAXMinValueAttribute)
    let leftMaximum = numericTargetValue("left-divider", kAXMaxValueAttribute)
    let rightCurrent = numericTargetValue("right-divider", kAXValueAttribute)
    let rightMinimum = numericTargetValue("right-divider", kAXMinValueAttribute)
    let rightMaximum = numericTargetValue("right-divider", kAXMaxValueAttribute)
    if !leftCollapsed {
        guard let leftCurrent, let leftMinimum, let leftMaximum,
              [leftCurrent, leftMinimum, leftMaximum].allSatisfy({ $0.isFinite })
        else { return nil }
    }
    if !rightCollapsed {
        guard let rightCurrent, let rightMinimum, let rightMaximum,
              [rightCurrent, rightMinimum, rightMaximum].allSatisfy({ $0.isFinite })
        else { return nil }
    }
    let windowFrameMatch = framesMatch(axWindowFrame, cgWindowFrame)
    let shellInAXWindow = frameContains(axWindowFrame, shell)
    let shellInCGWindow = frameContains(cgWindowFrame, shell)
    let allPanesContained = [left, Optional(center), right].compactMap { $0 }.allSatisfy {
        frameContains(shell, $0)
    }
    let allDividersContained = [leftDivider, rightDivider].compactMap { $0 }.allSatisfy {
        frameContains(shell, $0)
    }
    let leftGap = left.map { center.minX - $0.maxX }
    let rightGap = right.map { $0.minX - center.maxX }
    let threePaneDividerTiling: Bool
    if let left, let right, let leftDivider, let rightDivider,
       let leftGap, let rightGap {
        threePaneDividerTiling =
            abs(leftGap - leftDivider.width) <= tolerance &&
            abs(rightGap - rightDivider.width) <= tolerance &&
            abs(leftDivider.minX - left.maxX) <= tolerance &&
            abs(leftDivider.maxX - center.minX) <= tolerance &&
            abs(rightDivider.minX - center.maxX) <= tolerance &&
            abs(rightDivider.maxX - right.minX) <= tolerance
    } else {
        threePaneDividerTiling = false
    }
    let responsiveDividerTiling = [leftDivider, rightDivider].compactMap { $0 }
        .allSatisfy { $0.width <= 2 && $0.height <= 2 }
    let leftBoundsUsable = leftCollapsed || (
        left != nil && leftCurrent != nil && leftMinimum != nil && leftMaximum != nil &&
        leftMinimum! <= leftCurrent! && leftCurrent! <= leftMaximum! &&
        abs(left!.width - leftCurrent!) <= tolerance
    )
    let rightBoundsUsable = rightCollapsed || (
        right != nil && rightCurrent != nil && rightMinimum != nil && rightMaximum != nil &&
        rightMinimum! <= rightCurrent! && rightCurrent! <= rightMaximum! &&
        abs(right!.width - rightCurrent!) <= tolerance
    )
    let sideBoundsUsable = leftBoundsUsable && rightBoundsUsable
    let threePaneUsable =
        layoutMode == "three-pane" &&
        [left, Optional(center), right].compactMap { $0 }.allSatisfy({
            abs($0.minY - shell.minY) <= tolerance &&
            abs($0.height - shell.height) <= tolerance
        }) &&
        center.width >= 480 - tolerance &&
        threePaneDividerTiling
    var twoColumnUsable = false
    var stackedUsable = false
    if let left, let right {
        twoColumnUsable =
            layoutMode == "two-column" &&
            abs(left.minX - shell.minX) <= tolerance &&
            abs(left.minY - shell.minY) <= tolerance &&
            abs(left.height - shell.height) <= tolerance &&
            abs(center.minX - left.maxX) <= tolerance &&
            sameColumn &&
            abs(center.minY - shell.minY) <= tolerance &&
            center.maxY <= right.minY + tolerance &&
            abs(right.maxY - shell.maxY) <= tolerance &&
            center.width >= 480 - tolerance &&
            responsiveDividerTiling
        stackedUsable =
            layoutMode == "stacked" &&
            [left, center, right].allSatisfy({
                abs($0.minX - shell.minX) <= tolerance &&
                abs($0.width - shell.width) <= tolerance
            }) &&
            abs(left.minY - shell.minY) <= tolerance &&
            left.maxY <= center.minY + tolerance &&
            center.maxY <= right.minY + tolerance &&
            abs(right.maxY - shell.maxY) <= tolerance &&
            responsiveDividerTiling
    }
    let centerContinues = frameContains(shell, center) &&
        center.width >= 480 - tolerance && center.height > 0
    let collapsedUsable = !collapsedSides.isEmpty && centerContinues
    let responsiveUsable =
        windowFrameMatch && shellInAXWindow && shellInCGWindow &&
        allPanesContained && allDividersContained && sideBoundsUsable &&
        (threePaneUsable || twoColumnUsable || stackedUsable || collapsedUsable)

    func sideBoundsRecord(
        frame: CGRect?,
        current: Double?,
        minimum: Double?,
        maximum: Double?,
        preferred: Int
    ) -> [String: Any] {
        guard let frame, let current, let minimum, let maximum else {
            return [
                "present": false,
                "preferred_width_px": preferred,
            ]
        }
        return [
            "present": true,
            "pane_width_px": Int(frame.width.rounded()),
            "current_value": current,
            "minimum_value": minimum,
            "maximum_value": maximum,
            "within_bounds": minimum <= current && current <= maximum,
            "pane_width_matches_value": abs(frame.width - current) <= tolerance,
        ]
    }
    func nullableFrame(_ frame: CGRect?) -> Any {
        guard let frame else { return NSNull() }
        return integerFrame(frame)
    }
    func nullableGap(_ gap: CGFloat?) -> Any {
        guard let gap else { return NSNull() }
        return Int(gap.rounded())
    }
    let physicalGeometry: [String: Any] = [
            "ax_window": integerFrame(axWindowFrame),
            "cg_window": integerFrame(cgWindowFrame),
            "workspace_shell": integerFrame(shell),
            "window_frame_match": windowFrameMatch,
            "panes": [
                "left": nullableFrame(left),
                "center": integerFrame(center),
                "right": nullableFrame(right),
            ],
            "pane_present": [
                "left": left != nil,
                "center": true,
                "right": right != nil,
            ],
            "dividers": [
                "left": nullableFrame(leftDivider),
                "right": nullableFrame(rightDivider),
            ],
            "divider_present": [
                "left": leftDivider != nil,
                "right": rightDivider != nil,
            ],
            "tiling": [
                "left_gap_px": nullableGap(leftGap),
                "right_gap_px": nullableGap(rightGap),
                "divider_widths_match": layoutMode == "three-pane"
                    ? threePaneDividerTiling
                    : responsiveDividerTiling,
            ],
            "containment": [
                "workspace_shell_in_ax_window": shellInAXWindow,
                "workspace_shell_in_cg_window": shellInCGWindow,
                "all_panes": allPanesContained,
                "all_dividers": allDividersContained,
            ],
            "side_bounds": [
                "left": sideBoundsRecord(
                    frame: left,
                    current: leftCurrent,
                    minimum: leftMinimum,
                    maximum: leftMaximum,
                    preferred: preferredLeft
                ),
                "right": sideBoundsRecord(
                    frame: right,
                    current: rightCurrent,
                    minimum: rightMinimum,
                    maximum: rightMaximum,
                    preferred: preferredRight
                ),
            ],
            "responsive": [
                "mode": layoutMode,
                "usable": responsiveUsable,
                "center_continues": centerContinues,
            ],
            "collapsed_sides": collapsedSides,
        ]
    var payload: [String: Any] = [
        "accepted": true,
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "window_frame": integerFrame(axWindowFrame),
        "layout_mode": layoutMode,
        "targets": targets,
    ]
    guard let stabilityToken = workspaceSteadyToken(
        workspaceWindow,
        elements,
        anchorComponentIDs
    ) else { return nil }
    payload["stability_token"] = stabilityToken
    payload["physical_geometry"] = physicalGeometry
    payload["preferred_pair"] = preferredPair
    let selectedAppearanceModes = ["system", "light", "dark"].filter { mode in
        elementForToken("appearance-\(mode)", elements).map(elementIsSelected) == true
    }
    if selectedAppearanceModes.count == 1 {
        payload["appearance_mode"] = selectedAppearanceModes[0]
    }
    if elementForToken("graph-body", elements) != nil {
        payload["graph_body_identity"] = "component-map-body"
    }
    if let tooltipIdentity {
        payload["graph_tooltip_identity"] = tooltipIdentity
    }
    if let zoomValue = elements.map(elementName).first(where: {
        $0.contains("×") && (
            $0.contains("개요") || $0.contains("선택") ||
            $0.contains("상세") || $0.contains("선택 불가")
        )
    }) {
        let scaleText = zoomValue.components(separatedBy: "×").first ?? ""
        if let scale = Double(scaleText.trimmingCharacters(in: .whitespacesAndNewlines)) {
            payload["graph_scale"] = scale
        }
        payload["graph_lod"] = zoomValue.contains("선택 불가")
            ? "unavailable"
            : zoomValue.contains("상세")
                ? "detail"
                : zoomValue.contains("선택")
                    ? "select"
                    : "overview"
    }
    if let status = elementForToken("layout-status", elements).map(elementName) {
        if let revision = integerFromText(status) { payload["layout_revision"] = revision }
        if status.contains("저장 완료") { payload["persisted"] = true }
        if status == "레이아웃 설정 저장 실패" {
            payload["persisted"] = false
            payload["diagnostic"] = [
                "code": "workspace_layout_preference_write_failed",
                "message": status,
            ]
        }
        if status == "레이아웃 설정이 올바르지 않아 기본값을 사용합니다." {
            payload["persisted"] = false
            payload["diagnostic"] = [
                "code": "workspace_layout_preference_invalid",
                "message": status,
            ]
        }
        if status == "레이아웃 설정을 읽지 못해 기본값을 사용합니다." {
            payload["persisted"] = false
            payload["diagnostic"] = [
                "code": "workspace_layout_preference_unreadable",
                "message": status,
            ]
        }
    }
    if let semanticGraph {
        payload["semantic_graph"] = semanticGraph
    }
    if let graphToolbar = graphToolbarObservation(elements) {
        payload["graph_toolbar"] = graphToolbar
    }
    if let sotTree = sotTreeObservation(elements) {
        payload["sot_tree"] = sotTree
    }
    if let semanticGraph,
       let matrixLayout = matrixLayoutObservation(elements, anchorComponentIDs, semanticGraph) {
        payload["matrix_layout"] = matrixLayout
    }
    if let collapsedLayout = collapsedWorkbenchLayoutObservation(elements) {
        payload["collapsed_workbench_layout"] = collapsedLayout
    }
    if let matrixWrap = matrixWrapObservation(elements) {
        payload["matrix_wrap_sample"] = matrixWrap
    }
    if let typography = typographySamples(elements) {
        payload["typography_samples"] = typography
    }
    if let matrixBody = elementForToken("matrix-body", elements),
       !boolAttribute(matrixBody, "AXHidden"),
       let frame = axFrame(matrixBody),
       let centerFrame = elementForToken("center-pane", elements).flatMap(axFrame),
       frame.width > 0,
       frame.height > 0 {
        let visibleFrame = frame.intersection(centerFrame)
        let visibleHeight = visibleFrame.isNull ? 0 : max(0, visibleFrame.height)
        payload["matrix_usable_height"] = Int(visibleHeight.rounded())
    }
    return payload
}

func pressWorkspaceTarget(_ pid: pid_t, _ token: String) -> Bool {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return false }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let target = pressableElementForToken(token, elements) else { return false }
    return AXUIElementPerformAction(target, kAXPressAction as CFString) == .success
}

func uniqueHoverElement(
    _ token: String,
    _ elements: [AXUIElement]
) -> (element: AXUIElement, componentID: String?)? {
    if token == "graph-viewport" {
        let matches = elements.filter {
            elementMatchesIdentifier($0, "component-map-viewport")
        }
        guard matches.count == 1 else { return nil }
        return (matches[0], nil)
    }
    if token.hasPrefix("profile:") {
        let matches = graphViewportElements(elements).filter {
            guard let identity = profileNodeIdentityFromName(elementName($0)),
                  identity["type"] as? String == "Profile",
                  let profileID = identity["profile_id"] as? String else { return false }
            return token == "profile:\(profileID)"
        }
        guard matches.count == 1 else { return nil }
        return (matches[0], nil)
    }
    guard let componentID = componentIDFromToken(token) else { return nil }
    let matches = graphViewportElements(elements).filter {
        let name = elementName($0)
        return ["AXButton", "AXGroup"].contains(
            stringAttribute($0, kAXRoleAttribute)
        ) &&
            (name == componentID || name.contains(" · \(componentID) ·"))
    }
    guard matches.count == 1 else { return nil }
    return (matches[0], componentID)
}

func workspaceHoverTarget(
    _ pid: pid_t,
    _ token: String
) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let resolved = uniqueHoverElement(token, elements),
          let frame = axFrame(resolved.element),
          frame.width > 0,
          frame.height > 0,
          !boolAttribute(resolved.element, "AXHidden"),
          frameContains(workspaceWindow.cgFrame, frame),
          let source = CGEventSource(stateID: .hidSystemState) else { return nil }
    let point = token == "graph-viewport"
        ? CGPoint(x: frame.maxX - 3, y: frame.maxY - 3)
        : CGPoint(x: frame.midX, y: frame.midY)
    guard let move = CGEvent(
        mouseEventSource: source,
        mouseType: .mouseMoved,
        mouseCursorPosition: point,
        mouseButton: .left
    ) else { return nil }
    move.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.05)

    guard let activeWindow = workspaceMainWindow(pid),
          activeWindow.windowID == workspaceWindow.windowID else { return nil }
    let activeElements = boundedDescendants(activeWindow.element)
    guard let activeResolved = uniqueHoverElement(token, activeElements),
          activeResolved.componentID == resolved.componentID,
          let activeFrame = axFrame(activeResolved.element),
          activeFrame.width > 0,
          activeFrame.height > 0,
          !boolAttribute(activeResolved.element, "AXHidden"),
          frameContains(activeWindow.cgFrame, activeFrame),
          framesMatch(frame, activeFrame),
          var readback = targetRecord(token, activeResolved.element) else {
        return nil
    }
    if let componentID = activeResolved.componentID {
        readback["component_id"] = componentID
    }
    return [
        "accepted": true,
        "kind": "pointer-hover",
        "transport": "cg-event-mouse-move",
        "pid": Int(pid),
        "window_id": activeWindow.windowID,
        "target": token,
        "target_readback": readback,
        "pointer_point": [
            "x": Int(point.x.rounded()),
            "y": Int(point.y.rounded()),
        ],
    ]
}

func workspaceClickTarget(
    _ pid: pid_t,
    _ token: String
) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let resolved = uniqueHoverElement(token, elements),
          let componentID = resolved.componentID,
          elementForToken("selected-component", elements).map(elementName)
              != componentID,
          let frame = axFrame(resolved.element),
          frame.width > 0,
          frame.height > 0,
          !boolAttribute(resolved.element, "AXHidden"),
          frameContains(workspaceWindow.cgFrame, frame),
          let source = CGEventSource(stateID: .hidSystemState),
          var readback = targetRecord(token, resolved.element) else { return nil }
    let point = CGPoint(x: frame.midX, y: frame.midY)
    guard let move = CGEvent(
        mouseEventSource: source,
        mouseType: .mouseMoved,
        mouseCursorPosition: point,
        mouseButton: .left
    ), let down = CGEvent(
        mouseEventSource: source,
        mouseType: .leftMouseDown,
        mouseCursorPosition: point,
        mouseButton: .left
    ), let up = CGEvent(
        mouseEventSource: source,
        mouseType: .leftMouseUp,
        mouseCursorPosition: point,
        mouseButton: .left
    ) else { return nil }
    move.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.03)
    down.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.03)
    up.post(tap: .cghidEventTap)
    let confirmationDeadline = ProcessInfo.processInfo.systemUptime + 0.75
    var selectedComponentConfirmed = false
    repeat {
        guard let activeWindow = workspaceMainWindow(pid),
              activeWindow.windowID == workspaceWindow.windowID else { return nil }
        let activeElements = boundedDescendants(activeWindow.element)
        if let selectedIdentity = elementForToken("selected-component", activeElements)
            .map(elementName),
           selectedIdentity == componentID {
            selectedComponentConfirmed = true
            break
        }
        Thread.sleep(forTimeInterval: 0.02)
    } while ProcessInfo.processInfo.systemUptime < confirmationDeadline
    guard selectedComponentConfirmed else { return nil }
    readback["component_id"] = componentID
    return [
        "accepted": true,
        "kind": "pointer-click",
        "transport": "cg-event-mouse-click",
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "target": token,
        "preselected": false,
        "selection_transition_confirmed": true,
        "target_readback": readback,
        "pointer_point": [
            "x": Int(point.x.rounded()),
            "y": Int(point.y.rounded()),
        ],
    ]
}

func elementAttribute(_ element: AXUIElement, _ name: String) -> AXUIElement? {
    guard let raw = attribute(element, name),
          CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
    return unsafeBitCast(raw, to: AXUIElement.self)
}

func postKeyboardKey(_ code: CGKeyCode, flags: CGEventFlags = []) -> Bool {
    guard let source = CGEventSource(stateID: .hidSystemState),
          let down = CGEvent(
              keyboardEventSource: source,
              virtualKey: code,
              keyDown: true
          ),
          let up = CGEvent(
              keyboardEventSource: source,
              virtualKey: code,
              keyDown: false
          ) else { return false }
    down.flags = flags
    up.flags = flags
    down.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.03)
    up.post(tap: .cghidEventTap)
    return true
}

func nativePickerButton(
    _ window: AXUIElement,
    _ attributeName: String,
    _ fallbackIdentifier: String
) -> AXUIElement? {
    if let button = elementAttribute(window, attributeName),
       stringAttribute(button, kAXRoleAttribute) == "AXButton",
       (attribute(button, kAXEnabledAttribute) as? NSNumber)?.boolValue != false,
       !boolAttribute(button, "AXHidden"),
       let frame = axFrame(button),
       frame.width > 0,
       frame.height > 0 {
        return button
    }
    let matches = boundedDescendants(window, maximum: 512).filter {
        stringAttribute($0, kAXRoleAttribute) == "AXButton"
            && stringAttribute($0, kAXIdentifierAttribute) == fallbackIdentifier
            && (attribute($0, kAXEnabledAttribute) as? NSNumber)?.boolValue != false
            && !boolAttribute($0, "AXHidden")
            && axFrame($0).map { $0.width > 0 && $0.height > 0 } == true
    }
    guard matches.count == 1 else { return nil }
    return matches[0]
}

func nativeDirectoryPanel(
    _ pid: pid_t
) -> (
    window: AXUIElement,
    button: AXUIElement,
    frame: CGRect,
    cancelFrame: CGRect,
    subrole: String,
    identifier: String
)? {
    let application = AXUIElementCreateApplication(pid)
    let windows = attribute(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
    let candidates = windows.compactMap {
        window -> (AXUIElement, AXUIElement, CGRect, CGRect, String, String)? in
        let identifier = stringAttribute(window, kAXIdentifierAttribute)
        guard stringAttribute(window, kAXRoleAttribute) == "AXWindow",
              checkoutDirectoryPickerIdentifiers.contains(identifier),
              let cancelButton = nativePickerButton(window, kAXCancelButtonAttribute, "CancelButton"),
              let cancelFrame = axFrame(cancelButton),
              cancelFrame.width > 0,
              cancelFrame.height > 0,
              let button = nativePickerButton(window, kAXDefaultButtonAttribute, "OKButton"),
              let frame = axFrame(button),
              frame.width > 0,
              frame.height > 0 else { return nil }
        return (
            window,
            button,
            frame,
            cancelFrame,
            stringAttribute(window, kAXSubroleAttribute),
            identifier
        )
    }
    guard candidates.count == 1, let candidate = candidates.first else { return nil }
    return (
        window: candidate.0,
        button: candidate.1,
        frame: candidate.2,
        cancelFrame: candidate.3,
        subrole: candidate.4,
        identifier: candidate.5
    )
}

func checkoutDirectoryPickerPanelPresent(
    _ pid: pid_t,
    _ panel: AXUIElement
) -> Bool {
    let application = AXUIElementCreateApplication(pid)
    let windows = attribute(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
    return windows.contains { candidate in
        CFEqual(candidate, panel)
            && checkoutDirectoryPickerIdentifiers.contains(
                stringAttribute(candidate, kAXIdentifierAttribute)
            )
    }
}

func checkoutDirectoryPickerPanelWithIdentifierPresent(
    _ pid: pid_t,
    _ identifier: String
) -> Bool {
    guard checkoutDirectoryPickerIdentifiers.contains(identifier) else {
        return false
    }
    guard let panel = nativeDirectoryPanel(pid) else { return false }
    return panel.identifier == identifier
}

func workspacePickerObserve(
    _ pid: pid_t,
    _ bundleIdentifier: String,
    _ timeoutSeconds: Double
) -> [String: Any]? {
    guard let application = verifiedApplication(pid, bundleIdentifier) else {
        return nil
    }
    _ = application.activate(options: [.activateAllWindows])
    let deadline = ProcessInfo.processInfo.systemUptime + timeoutSeconds
    repeat {
        if let panel = nativeDirectoryPanel(pid) {
            return [
                "accepted": true,
                "kind": "native-directory-panel-open",
                "pid": Int(pid),
                "panel_count": 1,
                "panel_identifier": panel.identifier,
                "path_redacted": true,
                "title_redacted": true,
                "panel": [
                    "role": "AXWindow",
                    "subrole": panel.subrole,
                    "visible": true,
                    "identifier": panel.identifier,
                ],
                "default_button": [
                    "role": "AXButton",
                    "visible": true,
                    "frame": integerFrame(panel.frame),
                ],
                "cancel_button": [
                    "role": "AXButton",
                    "visible": true,
                    "frame": integerFrame(panel.cancelFrame),
                ],
            ]
        }
        Thread.sleep(forTimeInterval: 0.05)
    } while ProcessInfo.processInfo.systemUptime < deadline
    return nil
}

func workspacePickerClosed(
    _ pid: pid_t,
    _ bundleIdentifier: String,
    _ panelIdentifier: String,
    _ timeoutSeconds: Double
) -> [String: Any]? {
    guard checkoutDirectoryPickerIdentifiers.contains(panelIdentifier),
          let application = verifiedApplication(pid, bundleIdentifier) else {
        return nil
    }
    _ = application.activate(options: [.activateAllWindows])
    let deadline = ProcessInfo.processInfo.systemUptime + timeoutSeconds
    repeat {
        if !checkoutDirectoryPickerPanelWithIdentifierPresent(pid, panelIdentifier) {
            return [
                "accepted": true,
                "kind": "native-directory-panel-closed",
                "pid": Int(pid),
                "panel_identifier": panelIdentifier,
                "panel_closed": true,
                "path_redacted": true,
            ]
        }
        Thread.sleep(forTimeInterval: 0.05)
    } while ProcessInfo.processInfo.systemUptime < deadline
    return nil
}

func elementDescendsFrom(_ element: AXUIElement, _ ancestor: AXUIElement) -> Bool {
    var current: AXUIElement? = element
    for _ in 0..<16 {
        guard let candidate = current else { return false }
        if CFEqual(candidate, ancestor) { return true }
        current = elementAttribute(candidate, kAXParentAttribute)
    }
    return false
}

func focusedPickerTextField(
    _ pid: pid_t,
    _ panel: AXUIElement
) -> AXUIElement? {
    let system = AXUIElementCreateSystemWide()
    guard let focused = elementAttribute(system, kAXFocusedUIElementAttribute) else {
        return nil
    }
    var ownerPID: pid_t = 0
    guard AXUIElementGetPid(focused, &ownerPID) == .success,
          ownerPID == pid,
          ["AXTextField", "AXTextArea"].contains(
              stringAttribute(focused, kAXRoleAttribute)
          ),
          elementDescendsFrom(focused, panel) else { return nil }
    return focused
}

func workspacePickerSelect(
    _ pid: pid_t,
    _ bundleIdentifier: String,
    _ directory: String,
    _ timeoutSeconds: Double
) -> [String: Any]? {
    var isDirectory: ObjCBool = false
    guard directory.hasPrefix("/"),
          FileManager.default.fileExists(
              atPath: directory,
              isDirectory: &isDirectory
          ),
          isDirectory.boolValue,
          let application = verifiedApplication(pid, bundleIdentifier) else {
        return nil
    }
    _ = application.activate(options: [.activateAllWindows])
    let deadline = ProcessInfo.processInfo.systemUptime + timeoutSeconds
    var initialPanel: (
        window: AXUIElement,
        button: AXUIElement,
        frame: CGRect,
        cancelFrame: CGRect,
        subrole: String,
        identifier: String
    )?
    repeat {
        if let panel = nativeDirectoryPanel(pid) {
            initialPanel = panel
            break
        }
        Thread.sleep(forTimeInterval: 0.05)
    } while ProcessInfo.processInfo.systemUptime < deadline
    guard let panel = initialPanel,
          postKeyboardKey(5, flags: [.maskCommand, .maskShift]) else { return nil }

    var pathField: AXUIElement?
    repeat {
        if let field = focusedPickerTextField(pid, panel.window) {
            pathField = field
            break
        }
        Thread.sleep(forTimeInterval: 0.05)
    } while ProcessInfo.processInfo.systemUptime < deadline
    guard let field = pathField,
          AXUIElementSetAttributeValue(
              field,
              kAXValueAttribute as CFString,
              directory as CFString
          ) == .success,
          stringAttribute(field, kAXValueAttribute) == directory,
          postKeyboardKey(36) else { return nil }

    var confirm: (
        window: AXUIElement,
        button: AXUIElement,
        frame: CGRect,
        cancelFrame: CGRect,
        subrole: String,
        identifier: String
    )?
    repeat {
        let fieldClosed = stringAttribute(field, kAXRoleAttribute).isEmpty
            || boolAttribute(field, "AXHidden")
            || axFrame(field) == nil
        if fieldClosed,
           let candidate = nativeDirectoryPanel(pid),
           CFEqual(candidate.window, panel.window) {
            confirm = candidate
            break
        }
        Thread.sleep(forTimeInterval: 0.05)
    } while ProcessInfo.processInfo.systemUptime < deadline
    guard let selectedPanel = confirm,
          let source = CGEventSource(stateID: .hidSystemState) else { return nil }
    let point = CGPoint(
        x: selectedPanel.frame.midX,
        y: selectedPanel.frame.midY
    )
    guard let move = CGEvent(
        mouseEventSource: source,
        mouseType: .mouseMoved,
        mouseCursorPosition: point,
        mouseButton: .left
    ), let down = CGEvent(
        mouseEventSource: source,
        mouseType: .leftMouseDown,
        mouseCursorPosition: point,
        mouseButton: .left
    ), let up = CGEvent(
        mouseEventSource: source,
        mouseType: .leftMouseUp,
        mouseCursorPosition: point,
        mouseButton: .left
    ) else { return nil }
    move.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.03)
    down.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.03)
    up.post(tap: .cghidEventTap)

    var panelClosed = false
    repeat {
        if !checkoutDirectoryPickerPanelPresent(pid, panel.window) {
            panelClosed = true
            break
        }
        Thread.sleep(forTimeInterval: 0.05)
    } while ProcessInfo.processInfo.systemUptime < deadline
    guard panelClosed else { return nil }
    return [
        "accepted": true,
        "kind": "native-directory-selection",
        "transport": "cg-event-chord-ax-value-cg-event-mouse-click",
        "pid": Int(pid),
        "panel_identifier": selectedPanel.identifier,
        "path_redacted": true,
        "go_to_folder_value_confirmed": true,
        "confirm_button": [
            "role": "AXButton",
            "visible": true,
            "frame": integerFrame(selectedPanel.frame),
        ],
        "pointer_point": [
            "x": Int(point.x.rounded()),
            "y": Int(point.y.rounded()),
        ],
        "panel_closed": true,
    ]
}

func workspaceValueTarget(
    _ pid: pid_t,
    _ identifier: String
) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    let matches = elements.filter { elementMatchesIdentifier($0, identifier) }
    guard matches.count == 1,
          let target = matches.first,
          ["AXTextField", "AXTextArea"].contains(
            stringAttribute(target, kAXRoleAttribute)
          ),
          !boolAttribute(target, "AXHidden"),
          (attribute(target, kAXEnabledAttribute) as? NSNumber)?.boolValue != false,
          let targetFrame = axFrame(target),
          targetFrame.width > 0,
          targetFrame.height > 0,
          frameContains(workspaceWindow.cgFrame, targetFrame),
          let readback = targetRecord("id:\(identifier)", target) else { return nil }
    let clickPoint = CGPoint(x: targetFrame.midX, y: targetFrame.midY)
    return [
        "accepted": true,
        "pid": Int(pid),
        "window_id": workspaceWindow.windowID,
        "identifier": identifier,
        "pointer_point": [
            "x": Int(clickPoint.x.rounded()),
            "y": Int(clickPoint.y.rounded()),
        ],
        "target_readback": readback,
    ]
}

func workspaceValueEquals(
    _ pid: pid_t,
    _ identifier: String,
    _ expectedValue: String
) -> Bool {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return false }
    let elements = boundedDescendants(workspaceWindow.element)
    let matches = elements.filter { elementMatchesIdentifier($0, identifier) }
    guard matches.count == 1,
          let target = matches.first,
          ["AXTextField", "AXTextArea"].contains(
            stringAttribute(target, kAXRoleAttribute)
          ),
          !boolAttribute(target, "AXHidden"),
          (attribute(target, kAXEnabledAttribute) as? NSNumber)?.boolValue != false,
          let targetFrame = axFrame(target),
          targetFrame.width > 0,
          targetFrame.height > 0,
          frameContains(workspaceWindow.cgFrame, targetFrame) else {
        return false
    }
    return stringAttribute(target, kAXValueAttribute) == expectedValue
}

final class WorkspaceDragSession: @unchecked Sendable {
    let source: CGEventSource
    let start: CGPoint
    let end: CGPoint
    let mouseDownAt: TimeInterval
    let dragPostedAt: TimeInterval
    let targetRecord: [String: Any]
    private let mouseUp: CGEvent
    private let lock = NSLock()
    private var releasedAt: TimeInterval?

    init(
        source: CGEventSource,
        start: CGPoint,
        end: CGPoint,
        mouseDownAt: TimeInterval,
        dragPostedAt: TimeInterval,
        targetRecord: [String: Any],
        mouseUp: CGEvent
    ) {
        self.source = source
        self.start = start
        self.end = end
        self.mouseDownAt = mouseDownAt
        self.dragPostedAt = dragPostedAt
        self.targetRecord = targetRecord
        self.mouseUp = mouseUp
    }

    func isReleased() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return releasedAt != nil
    }

    func release() -> TimeInterval {
        lock.lock()
        defer { lock.unlock() }
        if let releasedAt { return releasedAt }
        mouseUp.post(tap: .cghidEventTap)
        let now = ProcessInfo.processInfo.systemUptime
        releasedAt = now
        return now
    }
}

func beginWorkspaceDrag(
    _ pid: pid_t,
    _ token: String,
    _ deltaX: CGFloat
) -> WorkspaceDragSession? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let target = elementForToken(token, elements),
          let frame = axFrame(target),
          let record = targetRecord(token, target),
          let source = CGEventSource(stateID: .hidSystemState) else { return nil }
    let start = CGPoint(x: frame.midX, y: frame.midY)
    let end = CGPoint(x: start.x + deltaX, y: start.y)
    guard let move = CGEvent(mouseEventSource: source, mouseType: .mouseMoved, mouseCursorPosition: start, mouseButton: .left),
          let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown, mouseCursorPosition: start, mouseButton: .left),
          let drag = CGEvent(mouseEventSource: source, mouseType: .leftMouseDragged, mouseCursorPosition: end, mouseButton: .left),
          let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp, mouseCursorPosition: end, mouseButton: .left) else {
        return nil
    }
    move.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.03)
    down.post(tap: .cghidEventTap)
    let mouseDownAt = ProcessInfo.processInfo.systemUptime
    Thread.sleep(forTimeInterval: 0.03)
    drag.post(tap: .cghidEventTap)
    let dragPostedAt = ProcessInfo.processInfo.systemUptime
    Thread.sleep(forTimeInterval: 0.03)
    return WorkspaceDragSession(
        source: source,
        start: start,
        end: end,
        mouseDownAt: mouseDownAt,
        dragPostedAt: dragPostedAt,
        targetRecord: record,
        mouseUp: up
    )
}

func dragWorkspaceTarget(_ pid: pid_t, _ token: String, _ deltaX: CGFloat) -> Bool {
    guard let session = beginWorkspaceDrag(pid, token, deltaX) else { return false }
    _ = session.release()
    return true
}

func keyWorkspaceTarget(
    _ pid: pid_t,
    _ token: String,
    _ key: String
) -> [String: Any]? {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let target = elementForToken(token, elements),
          let before = targetRecord(token, target),
          AXUIElementSetAttributeValue(
            target,
            kAXFocusedAttribute as CFString,
            kCFBooleanTrue
          ) == .success,
          let source = CGEventSource(stateID: .hidSystemState) else { return nil }
    let code: CGKeyCode = key == "ArrowLeft" ? 123 : 124
    guard let down = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: true),
          let up = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: false) else {
        return nil
    }
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
    let deadline = ProcessInfo.processInfo.systemUptime + 0.5
    var after: [String: Any]?
    while ProcessInfo.processInfo.systemUptime < deadline {
        if let activeWindow = workspaceMainWindow(pid) {
            let activeElements = boundedDescendants(activeWindow.element)
            if let activeTarget = elementForToken(token, activeElements),
               let record = targetRecord(token, activeTarget),
               record["focused"] as? Bool == true {
                after = record
                break
            }
        }
        Thread.sleep(forTimeInterval: 0.01)
    }
    guard let after,
          let valueReadback = after["current_value"] else { return nil }
    return [
        "accepted": true,
        "kind": "keyboard",
        "target": token,
        "key": key,
        "before": before,
        "after": after,
        "focused_readback": true,
        "value_readback": valueReadback,
    ]
}

func wheelWorkspaceTarget(
    _ pid: pid_t,
    _ token: String,
    _ deltaY: Int32
) -> [String: Any]? {
    guard ["sot-tree-scroll", "center-pane", "local-results"].contains(token),
          let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let target = elementForToken(token, elements),
          let frame = axFrame(target),
          frame.width > 0,
          frame.height > 0,
          let source = CGEventSource(stateID: .hidSystemState),
          let move = CGEvent(
            mouseEventSource: source,
            mouseType: .mouseMoved,
            mouseCursorPosition: CGPoint(x: frame.midX, y: frame.midY),
            mouseButton: .left
          ),
          let wheel = CGEvent(
            scrollWheelEvent2Source: source,
            units: .pixel,
            wheelCount: 1,
            wheel1: deltaY,
            wheel2: 0,
            wheel3: 0
          ) else { return nil }
    move.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.03)
    wheel.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.08)
    return [
        "accepted": true,
        "kind": token == "sot-tree-scroll"
            ? "tree-scroll"
            : token == "local-results"
                ? "local-result-scroll"
                : "workbench-scroll",
        "transport": "cg-event-wheel",
        "target": token,
        "delta_y": Int(deltaY),
    ]
}

func keyWorkspaceTreeTarget(
    _ pid: pid_t,
    _ token: String,
    _ key: String
) -> [String: Any]? {
    guard token == "sot-tree-first-component",
          key == "End",
          let workspaceWindow = workspaceMainWindow(pid) else { return nil }
    let elements = boundedDescendants(workspaceWindow.element)
    guard let target = elementForToken(token, elements),
          AXUIElementSetAttributeValue(
            target,
            kAXFocusedAttribute as CFString,
            kCFBooleanTrue
          ) == .success,
          let source = CGEventSource(stateID: .hidSystemState),
          let down = CGEvent(
            keyboardEventSource: source,
            virtualKey: 119,
            keyDown: true
          ),
          let up = CGEvent(
            keyboardEventSource: source,
            virtualKey: 119,
            keyDown: false
          ) else { return nil }
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.08)
    guard let activeWindow = workspaceMainWindow(pid),
          let last = elementForToken(
            "sot-tree-last-component",
            boundedDescendants(activeWindow.element)
          ),
          let componentID = canonicalComponentIDFromName(elementName(last)),
          boolAttribute(last, kAXFocusedAttribute) else { return nil }
    return [
        "accepted": true,
        "kind": "tree-scroll",
        "transport": "cg-event-key-end",
        "target": token,
        "key": key,
        "focused_identity": componentID,
    ]
}

func resizeWorkspaceWindow(_ pid: pid_t, _ width: CGFloat, _ height: CGFloat) -> Bool {
    guard let workspaceWindow = workspaceMainWindow(pid) else { return false }
    var size = CGSize(width: width, height: height)
    guard let rawSize = AXValueCreate(.cgSize, &size) else { return false }
    return AXUIElementSetAttributeValue(
        workspaceWindow.element,
        kAXSizeAttribute as CFString,
        rawSize
    ) == .success
}

enum WorkspaceDragCaptureError: Error {
    case failed
}

@available(macOS 14.0, *)
func runWorkspaceDragCapture(
    bundleIdentifier: String,
    pid: pid_t,
    token: String,
    deltaX: CGFloat,
    destinationURL: URL
) -> Never {
    guard let workspaceWindow = workspaceMainWindow(pid),
          let session = beginWorkspaceDrag(pid, token, deltaX) else {
        failJSON("workspace_drag_capture_start_failed", "workspace-drag-capture")
    }
    let safetyRelease = DispatchWorkItem { _ = session.release() }
    DispatchQueue.global(qos: .userInitiated).asyncAfter(
        deadline: .now() + .seconds(10),
        execute: safetyRelease
    )
    _ = NSApplication.shared.setActivationPolicy(.prohibited)
    Task { @MainActor in
        var captured: [String: Any]?
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                true,
                onScreenWindowsOnly: true
            )
            guard let window = content.windows.first(where: {
                $0.windowID == CGWindowID(workspaceWindow.windowID) &&
                $0.windowLayer == 0 &&
                $0.title == "HarnessKit" &&
                $0.owningApplication?.processID == pid &&
                $0.owningApplication?.bundleIdentifier == bundleIdentifier
            }) else { throw WorkspaceDragCaptureError.failed }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let configuration = SCStreamConfiguration()
            let scale = CGFloat(filter.pointPixelScale)
            configuration.width = max(
                1,
                Int((filter.contentRect.width * scale).rounded(.up))
            )
            configuration.height = max(
                1,
                Int((filter.contentRect.height * scale).rounded(.up))
            )
            configuration.showsCursor = true
            configuration.shouldBeOpaque = true
            let captureStartedAt = ProcessInfo.processInfo.systemUptime
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            )
            let captureCompletedAt = ProcessInfo.processInfo.systemUptime
            guard !session.isReleased(),
                  let body = pngData(image) else {
                throw WorkspaceDragCaptureError.failed
            }
            try body.write(to: destinationURL, options: .atomic)
            let activeWindow = workspaceMainWindow(pid)
            let activeElements = activeWindow.map { boundedDescendants($0.element) } ?? []
            guard let activeTarget = elementForToken(token, activeElements)
                .flatMap({ targetRecord(token, $0) }) else {
                throw WorkspaceDragCaptureError.failed
            }
            guard activeTarget["active_state"] as? String == "dragging",
                  let activeValue = activeTarget["current_value"] as? NSNumber,
                  let valueDescription = activeTarget["value_description"] as? String,
                  dividerActiveState(valueDescription, activeValue.doubleValue)
                    == "dragging" else {
                throw WorkspaceDragCaptureError.failed
            }
            captured = [
                "capture_started_monotonic_seconds": captureStartedAt,
                "capture_completed_monotonic_seconds": captureCompletedAt,
                "capture_mode": "screen_capture_kit_desktop_independent_window",
                "cursor_included": true,
                "owner_pid": Int(pid),
                "window_id": workspaceWindow.windowID,
                "width": image.width,
                "height": image.height,
                "point_pixel_scale": filter.pointPixelScale,
                "pointer_down_during_capture": !session.isReleased(),
                "active_target": activeTarget,
            ]
        } catch {
            captured = nil
        }
        let mouseUpAt = session.release()
        safetyRelease.cancel()
        guard var capture = captured,
              let captureCompletedAt = capture["capture_completed_monotonic_seconds"] as? Double,
              captureCompletedAt <= mouseUpAt else {
            try? FileManager.default.removeItem(at: destinationURL)
            failJSON("workspace_drag_capture_failed", "workspace-drag-capture")
        }
        capture["mouse_up_monotonic_seconds"] = mouseUpAt
        capture["captured_before_mouseup"] = captureCompletedAt <= mouseUpAt
        emit([
            "accepted": true,
            "action": [
                "kind": "pointer-drag",
                "target": token,
                "delta_x": deltaX,
                "mouse_down_monotonic_seconds": session.mouseDownAt,
                "drag_posted_monotonic_seconds": session.dragPostedAt,
                "mouse_up_monotonic_seconds": mouseUpAt,
                "mouse_down_sequence": 1,
                "drag_sequence": 2,
                "capture_sequence": 3,
                "mouse_up_sequence": 4,
            ],
            "capture": capture,
        ])
    }
    RunLoop.main.run()
    _ = session.release()
    safetyRelease.cancel()
    failJSON("workspace_drag_capture_runloop_stopped", "workspace-drag-capture")
}

func releaseWorkspacePointer() -> Bool {
    guard let source = CGEventSource(stateID: .hidSystemState) else { return false }
    let location = CGEvent(source: nil)?.location ?? .zero
    guard let up = CGEvent(
        mouseEventSource: source,
        mouseType: .leftMouseUp,
        mouseCursorPosition: location,
        mouseButton: .left
    ) else { return false }
    up.post(tap: .cghidEventTap)
    return true
}

func cgWorkspaceWindowState(
    _ pid: pid_t,
    _ expectedTitle: String,
    _ expectedWindowID: Int? = nil
) -> [String: Any]? {
    guard let raw = CGWindowListCopyWindowInfo(
        [.optionAll, .excludeDesktopElements],
        kCGNullWindowID
    ) as? [[String: Any]] else { return nil }
    let matches = raw.filter { item in
        let owner = item[kCGWindowOwnerPID as String] as? Int ?? -1
        let layer = item[kCGWindowLayer as String] as? Int ?? -1
        let title = item[kCGWindowName as String] as? String ?? ""
        let windowID = item[kCGWindowNumber as String] as? Int ?? 0
        return owner == Int(pid) && layer == 0 && title == expectedTitle &&
            (expectedWindowID == nil || expectedWindowID == windowID)
    }
    guard matches.count == 1,
          let windowID = matches[0][kCGWindowNumber as String] as? Int,
          windowID > 0 else { return nil }
    let alpha = (matches[0][kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 0
    var frame: [String: Int] = ["x": 0, "y": 0, "width": 0, "height": 0]
    if let rawBounds = matches[0][kCGWindowBounds as String] as? NSDictionary,
       let bounds = CGRect(dictionaryRepresentation: rawBounds as CFDictionary) {
        frame = integerFrame(bounds)
    }
    return [
        "window_id": windowID,
        "layer": 0,
        "on_screen": matches[0][kCGWindowIsOnscreen as String] as? Bool ?? false,
        "alpha_positive": alpha > 0,
        "alpha": alpha,
        "window_frame": frame,
    ]
}

func firstVisibleWorkspaceGeometry(_ pid: pid_t) -> [String: Any]? {
    guard let snapshot = workspaceSnapshot(pid, []),
          let targets = snapshot["targets"] as? [[String: Any]] else { return nil }
    var frames: [String: Any] = [:]
    for token in ["left-pane", "center-pane", "right-pane"] {
        guard let target = targets.first(where: { $0["target"] as? String == token }),
              let frame = target["frame"] as? [String: Int] else { return nil }
        frames[String(token.dropLast("-pane".count))] = frame
    }
    guard let left = frames["left"] as? [String: Int],
          let right = frames["right"] as? [String: Int],
          let leftWidth = left["width"],
          let rightWidth = right["width"],
          let windowFrame = snapshot["window_frame"] as? [String: Int],
          let layoutMode = snapshot["layout_mode"] as? String else { return nil }
    return [
        "window": windowFrame,
        "panes": frames,
        "layout_mode": layoutMode,
        "effective_pair": ["left_px": leftWidth, "right_px": rightWidth],
    ]
}

@available(macOS 14.0, *)
final class WorkspaceVisibilityMonitor: @unchecked Sendable {
    private let lock = NSLock()
    private let pid: pid_t
    private let expectedTitle: String
    private let streamStartedAt: TimeInterval
    private let applicationDetectedAt: TimeInterval
    private let queue = DispatchQueue(label: "io.pureliture.harness.workspace-first-visible.visibility")
    private var timer: DispatchSourceTimer?
    private var expectedWindowID: Int?
    private var sampleCounter = 0
    private var lastSampleAt: TimeInterval?
    private var maxSequenceGap = 0.0
    private var identityMismatch = false
    private var identityBoundAt: TimeInterval?
    private var identityBindingSampleSequence: Int?
    private var sckIdentityConfirmed = false
    private var sckIdentityConfirmedAt: TimeInterval?
    private var visibleBeforeIdentityBinding = false
    private var currentlyVisible = false
    private var firstVisibleAt: TimeInterval?
    private var firstVisibleSampleSequence: Int?
    private var firstVisibleGeometry: [String: Any]?
    private var firstVisibleGeometryCapturedAt: TimeInterval?
    private var firstVisibleGeometryUnavailable = false
    private var currentWindowFrame: [String: Int]?
    private var currentAlpha = 0.0
    private var sequence: [[String: Any]] = []

    init(
        pid: pid_t,
        expectedTitle: String,
        streamStartedAt: TimeInterval,
        applicationDetectedAt: TimeInterval
    ) {
        self.pid = pid
        self.expectedTitle = expectedTitle
        self.streamStartedAt = streamStartedAt
        self.applicationDetectedAt = applicationDetectedAt
    }

    func start() {
        let source = DispatchSource.makeTimerSource(queue: queue)
        source.schedule(deadline: .now(), repeating: .milliseconds(2), leeway: .milliseconds(1))
        source.setEventHandler { [weak self] in self?.poll() }
        timer = source
        source.resume()
    }

    func stop() {
        lock.lock()
        let activeTimer = timer
        timer = nil
        lock.unlock()
        activeTimer?.cancel()
    }

    func confirmSCKIdentity(windowID: Int) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard let discovered = expectedWindowID else { return false }
        if discovered != windowID {
            identityMismatch = true
            return false
        }
        if !sckIdentityConfirmed {
            sckIdentityConfirmed = true
            sckIdentityConfirmedAt = ProcessInfo.processInfo.systemUptime
            appendSequenceEvent(
                timestamp: sckIdentityConfirmedAt!,
                windowID: windowID,
                onScreen: currentlyVisible,
                alpha: currentAlpha,
                event: "sck-identity-confirmed"
            )
        }
        return true
    }

    private func appendSequenceEvent(
        timestamp: TimeInterval,
        windowID: Int,
        onScreen: Bool,
        alpha: Double,
        event: String
    ) {
        guard sequence.count < 64 else { return }
        sequence.append([
            "sequence": sampleCounter,
            "event": event,
            "monotonic_seconds": timestamp,
            "pid": Int(pid),
            "window_id": windowID,
            "layer": 0,
            "on_screen": onScreen,
            "alpha_positive": alpha > 0,
            "alpha": alpha,
        ])
    }

    private func poll() {
        let now = ProcessInfo.processInfo.systemUptime
        lock.lock()
        let boundWindowID = expectedWindowID
        lock.unlock()
        let state = cgWorkspaceWindowState(pid, expectedTitle, boundWindowID)
        let visibleState = state.map {
            ($0["on_screen"] as? Bool ?? false) &&
            ($0["alpha"] as? Double ?? 0) > 0 &&
            $0["layer"] as? Int == 0
        } ?? false
        lock.lock()
        let needsFirstVisibleGeometry = visibleState && firstVisibleAt == nil
        lock.unlock()
        let geometry = needsFirstVisibleGeometry ? firstVisibleWorkspaceGeometry(pid) : nil
        let geometryCapturedAt = needsFirstVisibleGeometry
            ? ProcessInfo.processInfo.systemUptime
            : nil
        lock.lock()
        defer { lock.unlock() }
        sampleCounter += 1
        if let previous = lastSampleAt {
            maxSequenceGap = max(maxSequenceGap, now - previous)
        }
        lastSampleAt = now
        guard let state,
              let observedWindowID = state["window_id"] as? Int else { return }
        let onScreen = state["on_screen"] as? Bool ?? false
        let alpha = state["alpha"] as? Double ?? 0
        let visible = onScreen && alpha > 0 && state["layer"] as? Int == 0
        if let bound = expectedWindowID, bound != observedWindowID {
            identityMismatch = true
            return
        }
        if expectedWindowID == nil {
            expectedWindowID = observedWindowID
            identityBoundAt = now
            identityBindingSampleSequence = sampleCounter
            if visible { visibleBeforeIdentityBinding = true }
            appendSequenceEvent(
                timestamp: now,
                windowID: observedWindowID,
                onScreen: onScreen,
                alpha: alpha,
                event: visible ? "identity-bound-visible" : "identity-bound-hidden"
            )
        }
        currentWindowFrame = state["window_frame"] as? [String: Int]
        currentAlpha = alpha
        if visible && firstVisibleAt == nil {
            firstVisibleAt = now
            firstVisibleSampleSequence = sampleCounter
            firstVisibleGeometry = geometry
            firstVisibleGeometryCapturedAt = geometryCapturedAt
            firstVisibleGeometryUnavailable = geometry == nil
            appendSequenceEvent(
                timestamp: now,
                windowID: observedWindowID,
                onScreen: onScreen,
                alpha: alpha,
                event: "first-visible"
            )
        }
        if firstVisibleAt != nil && visible != currentlyVisible {
            appendSequenceEvent(
                timestamp: now,
                windowID: observedWindowID,
                onScreen: onScreen,
                alpha: alpha,
                event: visible ? "visible" : "not-visible"
            )
        }
        currentlyVisible = visible
    }

    func state() -> [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        let lateAttach = firstVisibleAt.map { streamStartedAt >= $0 } ?? false
        var value: [String: Any] = [
            "pid": Int(pid),
            "stream_started": true,
            "stream_started_monotonic_seconds": streamStartedAt,
            "application_detected_monotonic_seconds": applicationDetectedAt,
            "late_attach": lateAttach,
            "identity_mismatch": identityMismatch,
            "identity_bound": identityBoundAt != nil,
            "sck_identity_confirmed": sckIdentityConfirmed,
            "visible_before_identity_binding": visibleBeforeIdentityBinding,
            "first_visible_geometry_unavailable": firstVisibleGeometryUnavailable,
            "visible": currentlyVisible,
            "max_sequence_gap_seconds": maxSequenceGap,
            "sequence_gap": maxSequenceGap > 0.1,
            "visibility_sequence": sequence,
        ]
        if let windowID = expectedWindowID { value["window_id"] = windowID }
        if let identityBoundAt { value["identity_bound_monotonic_seconds"] = identityBoundAt }
        if let identityBindingSampleSequence {
            value["identity_binding_sample_sequence"] = identityBindingSampleSequence
        }
        if let sckIdentityConfirmedAt {
            value["sck_identity_confirmed_monotonic_seconds"] = sckIdentityConfirmedAt
        }
        if let firstVisibleAt { value["first_visible_monotonic_seconds"] = firstVisibleAt }
        if let firstVisibleSampleSequence {
            value["first_visible_sample_sequence"] = firstVisibleSampleSequence
        }
        if let firstVisibleGeometry { value["first_visible_geometry"] = firstVisibleGeometry }
        if let firstVisibleGeometryCapturedAt {
            value["first_visible_geometry_monotonic_seconds"] = firstVisibleGeometryCapturedAt
        }
        if let currentWindowFrame { value["window_frame"] = currentWindowFrame }
        value["alpha"] = currentAlpha
        return value
    }
}

@available(macOS 14.0, *)
final class WorkspaceFirstVisibleFrameCollector: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private let destinationURL: URL
    private let displayFrame: CGRect
    private let context = CIContext()
    private var monitor: WorkspaceVisibilityMonitor?
    private var completeFrameSequence = 0
    private var completed: [String: Any]?
    private var failureCode: String?

    init(destinationURL: URL, displayFrame: CGRect) {
        self.destinationURL = destinationURL
        self.displayFrame = displayFrame
    }

    func bind(monitor: WorkspaceVisibilityMonitor) {
        lock.lock()
        self.monitor = monitor
        lock.unlock()
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard outputType == .screen,
              sampleBuffer.isValid,
              let attachments = CMSampleBufferGetSampleAttachmentsArray(
                sampleBuffer,
                createIfNecessary: false
              ) as? [[SCStreamFrameInfo: Any]],
              let attachment = attachments.first,
              let rawStatus = attachment[.status] as? Int,
              SCFrameStatus(rawValue: rawStatus) == .complete else { return }

        lock.lock()
        completeFrameSequence += 1
        let frameSequence = completeFrameSequence
        let alreadyCompleted = completed != nil || failureCode != nil
        lock.unlock()
        if alreadyCompleted { return }

        lock.lock()
        let activeMonitor = monitor
        lock.unlock()
        guard let activeMonitor else { return }
        let visibility = activeMonitor.state()
        guard visibility["visible"] as? Bool == true,
              visibility["late_attach"] as? Bool == false,
              visibility["identity_mismatch"] as? Bool == false,
              visibility["identity_bound"] as? Bool == true,
              visibility["sck_identity_confirmed"] as? Bool == true,
              visibility["visible_before_identity_binding"] as? Bool == false,
              visibility["first_visible_geometry_unavailable"] as? Bool == false,
              visibility["sequence_gap"] as? Bool == false,
              let firstVisibleAt = visibility["first_visible_monotonic_seconds"] as? Double,
              let geometryCapturedAt = visibility["first_visible_geometry_monotonic_seconds"] as? Double,
              let firstVisibleGeometry = visibility["first_visible_geometry"] as? [String: Any],
              let rawFrame = firstVisibleGeometry["window"] as? [String: Int],
              let windowX = rawFrame["x"],
              let windowY = rawFrame["y"],
              let windowWidth = rawFrame["width"],
              let windowHeight = rawFrame["height"],
              windowWidth > 0,
              windowHeight > 0,
              let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let pixelWidth = CGFloat(CVPixelBufferGetWidth(pixelBuffer))
        let pixelHeight = CGFloat(CVPixelBufferGetHeight(pixelBuffer))
        let scaleX = pixelWidth / max(1, displayFrame.width)
        let scaleY = pixelHeight / max(1, displayFrame.height)
        let pointFrame = CGRect(
            x: CGFloat(windowX),
            y: CGFloat(windowY),
            width: CGFloat(windowWidth),
            height: CGFloat(windowHeight)
        )
        let crop = CGRect(
            x: (pointFrame.minX - displayFrame.minX) * scaleX,
            y: pixelHeight - ((pointFrame.maxY - displayFrame.minY) * scaleY),
            width: pointFrame.width * scaleX,
            height: pointFrame.height * scaleY
        ).integral
        guard crop.minX >= 0,
              crop.minY >= 0,
              crop.maxX <= pixelWidth,
              crop.maxY <= pixelHeight else {
            lock.lock()
            failureCode = "workspace_first_visible_window_outside_prearmed_display"
            lock.unlock()
            return
        }
        let image = CIImage(cvPixelBuffer: pixelBuffer)
        guard let cgImage = context.createCGImage(image, from: crop),
              let body = pngData(cgImage) else {
            lock.lock()
            failureCode = "workspace_first_visible_png_encode_failed"
            lock.unlock()
            return
        }
        do {
            try body.write(to: destinationURL, options: .atomic)
        } catch {
            lock.lock()
            failureCode = "workspace_first_visible_png_write_failed"
            lock.unlock()
            return
        }
        let frameObservedAt = ProcessInfo.processInfo.systemUptime
        guard frameObservedAt >= firstVisibleAt,
              frameObservedAt >= geometryCapturedAt else { return }
        lock.lock()
        if completed == nil {
            completed = [
                "complete_frame_sequence": frameSequence,
                "first_frame_monotonic_seconds": frameObservedAt,
                "pixel_width": cgImage.width,
                "pixel_height": cgImage.height,
                "point_pixel_scale": scaleX,
            ]
        }
        lock.unlock()
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        lock.lock()
        if completed == nil { failureCode = "workspace_first_visible_stream_stopped" }
        lock.unlock()
    }

    func outcome() -> (payload: [String: Any]?, failure: String?) {
        lock.lock()
        defer { lock.unlock() }
        return (completed, failureCode)
    }
}

@available(macOS 14.0, *)
func runWorkspaceFirstVisible(
    bundleIdentifier: String,
    expectedTitle: String,
    anchorComponentIDs: [String],
    readyURL: URL,
    destinationURL: URL,
    timeoutSeconds: Double
) -> Never {
    _ = NSApplication.shared.setActivationPolicy(.prohibited)
    guard runningApplications(bundleIdentifier).isEmpty else {
        failJSON("workspace_first_visible_existing_application", "workspace-first-visible")
    }

    Task { @MainActor in
        let deadline = ProcessInfo.processInfo.systemUptime + timeoutSeconds
        var observedPID: pid_t?
        var monitor: WorkspaceVisibilityMonitor?
        var identityBound = false
        let outputQueue = DispatchQueue(label: "io.pureliture.harness.workspace-first-visible.frames")
        let initialContent: SCShareableContent
        do {
            initialContent = try await SCShareableContent.excludingDesktopWindows(
                true,
                onScreenWindowsOnly: false
            )
        } catch {
            failJSON("workspace_first_visible_content_unavailable", "workspace-first-visible")
        }
        let mainDisplayID = (NSScreen.main?.deviceDescription[
            NSDeviceDescriptionKey("NSScreenNumber")
        ] as? NSNumber)?.uint32Value
        guard let display = initialContent.displays.first(where: {
            mainDisplayID != nil && $0.displayID == CGDirectDisplayID(mainDisplayID!)
        }) ?? initialContent.displays.first else {
            failJSON("workspace_first_visible_display_unavailable", "workspace-first-visible")
        }
        let displayFilter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.width = max(1, display.width)
        configuration.height = max(1, display.height)
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 60)
        configuration.queueDepth = 6
        configuration.showsCursor = false
        configuration.shouldBeOpaque = true
        let frameCollector = WorkspaceFirstVisibleFrameCollector(
            destinationURL: destinationURL,
            displayFrame: display.frame
        )
        let stream = SCStream(
            filter: displayFilter,
            configuration: configuration,
            delegate: frameCollector
        )
        do {
            try stream.addStreamOutput(
                frameCollector,
                type: .screen,
                sampleHandlerQueue: outputQueue
            )
            try await stream.startCapture()
        } catch {
            failJSON("workspace_first_visible_stream_prearm_failed", "workspace-first-visible")
        }
        let streamStartedAt = ProcessInfo.processInfo.systemUptime
        do {
            try Data("ready\n".utf8).write(to: readyURL, options: .atomic)
        } catch {
            try? await stream.stopCapture()
            failJSON("workspace_first_visible_ready_write_failed", "workspace-first-visible")
        }
        let readyWrittenAt = ProcessInfo.processInfo.systemUptime

        while ProcessInfo.processInfo.systemUptime < deadline {
            let applications = runningApplications(bundleIdentifier)
            if applications.count > 1 {
                try? await stream.stopCapture()
                monitor?.stop()
                failJSON("workspace_first_visible_application_count_mismatch", "workspace-first-visible")
            }
            if observedPID == nil, let application = applications.first {
                let applicationDetectedAt = ProcessInfo.processInfo.systemUptime
                observedPID = application.processIdentifier
                let activeMonitor = WorkspaceVisibilityMonitor(
                    pid: application.processIdentifier,
                    expectedTitle: expectedTitle,
                    streamStartedAt: streamStartedAt,
                    applicationDetectedAt: applicationDetectedAt
                )
                activeMonitor.start()
                monitor = activeMonitor
                frameCollector.bind(monitor: activeMonitor)
            }

            if !identityBound, let pid = observedPID, let activeMonitor = monitor {
                do {
                    let content = try await SCShareableContent.excludingDesktopWindows(
                        true,
                        onScreenWindowsOnly: false
                    )
                    let candidates = content.windows.filter {
                        $0.windowLayer == 0 &&
                        $0.title == expectedTitle &&
                        $0.owningApplication?.processID == pid &&
                        $0.owningApplication?.bundleIdentifier == bundleIdentifier
                    }
                    if candidates.count > 1 {
                        try? await stream.stopCapture()
                        activeMonitor.stop()
                        failJSON("workspace_first_visible_window_count_mismatch", "workspace-first-visible")
                    }
                    if let window = candidates.first {
                        guard activeMonitor.confirmSCKIdentity(windowID: Int(window.windowID)) else {
                            try? await Task.sleep(nanoseconds: 2_000_000)
                            continue
                        }
                        guard activeMonitor.state()["identity_mismatch"] as? Bool == false else {
                            try? await stream.stopCapture()
                            activeMonitor.stop()
                            failJSON("workspace_first_visible_window_identity_mismatch", "workspace-first-visible")
                        }
                        guard display.frame.intersects(window.frame) else {
                            try? await stream.stopCapture()
                            activeMonitor.stop()
                            failJSON("workspace_first_visible_window_outside_prearmed_display", "workspace-first-visible")
                        }
                        identityBound = true
                    }
                } catch {
                    try? await stream.stopCapture()
                    activeMonitor.stop()
                    failJSON("workspace_first_visible_identity_discovery_failed", "workspace-first-visible")
                }
            }

            if let activeMonitor = monitor {
                let visibility = activeMonitor.state()
                if visibility["late_attach"] as? Bool == true {
                    try? await stream.stopCapture()
                    activeMonitor.stop()
                    failJSON("workspace_first_visible_late_attach", "workspace-first-visible")
                }
                if visibility["identity_mismatch"] as? Bool == true {
                    try? await stream.stopCapture()
                    activeMonitor.stop()
                    failJSON("workspace_first_visible_window_identity_mismatch", "workspace-first-visible")
                }
                if visibility["visible_before_identity_binding"] as? Bool == true {
                    try? await stream.stopCapture()
                    activeMonitor.stop()
                    failJSON("workspace_first_visible_visible_before_identity", "workspace-first-visible")
                }
                if visibility["first_visible_geometry_unavailable"] as? Bool == true {
                    try? await stream.stopCapture()
                    activeMonitor.stop()
                    failJSON("workspace_first_visible_geometry_unavailable", "workspace-first-visible")
                }
                if visibility["sequence_gap"] as? Bool == true {
                    try? await stream.stopCapture()
                    activeMonitor.stop()
                    failJSON("workspace_first_visible_sequence_gap", "workspace-first-visible")
                }
            }

            let outcome = frameCollector.outcome()
            if let failure = outcome.failure {
                try? await stream.stopCapture()
                monitor?.stop()
                failJSON(failure, "workspace-first-visible")
            }
            if identityBound,
               var frame = outcome.payload,
               let pid = observedPID,
               let activeMonitor = monitor,
               var snapshot = workspaceSnapshot(pid, anchorComponentIDs) {
                try? await stream.stopCapture()
                activeMonitor.stop()
                let visibility = activeMonitor.state()
                guard visibility["late_attach"] as? Bool == false,
                      visibility["identity_mismatch"] as? Bool == false,
                      visibility["visible_before_identity_binding"] as? Bool == false,
                      visibility["identity_bound"] as? Bool == true,
                      visibility["sck_identity_confirmed"] as? Bool == true,
                      visibility["first_visible_geometry_unavailable"] as? Bool == false,
                      visibility["sequence_gap"] as? Bool == false,
                      visibility["window_id"] as? Int == snapshot["window_id"] as? Int,
                      let firstVisibleAt = visibility["first_visible_monotonic_seconds"] as? Double,
                      let identityBoundAt = visibility["identity_bound_monotonic_seconds"] as? Double,
                      let applicationDetectedAt = visibility["application_detected_monotonic_seconds"] as? Double,
                      let frameAt = frame["first_frame_monotonic_seconds"] as? Double,
                      let firstVisibleSequence = visibility["first_visible_sample_sequence"] as? Int,
                      let firstVisibleGeometry = visibility["first_visible_geometry"] as? [String: Any],
                      let geometryCapturedAt = visibility["first_visible_geometry_monotonic_seconds"] as? Double,
                      streamStartedAt < readyWrittenAt,
                      readyWrittenAt <= applicationDetectedAt,
                      identityBoundAt < firstVisibleAt,
                      geometryCapturedAt >= firstVisibleAt,
                      frameAt >= geometryCapturedAt else {
                    failJSON("workspace_first_visible_identity_unconfirmed", "workspace-first-visible")
                }
                frame["capture_mode"] = "screen_capture_kit_prearmed_display_first_complete_window_crop"
                frame["collector_prearmed"] =
                    streamStartedAt < readyWrittenAt && readyWrittenAt <= applicationDetectedAt
                frame["stream_started_before_ready"] = streamStartedAt < readyWrittenAt
                frame["prearmed_stream_started_monotonic_seconds"] = streamStartedAt
                frame["ready_written_monotonic_seconds"] = readyWrittenAt
                frame["visibility"] = visibility
                frame["prearmed_before_launch"] = readyWrittenAt <= applicationDetectedAt
                frame["collector_attached_before_visible"] =
                    streamStartedAt < firstVisibleAt && identityBoundAt < firstVisibleAt
                frame["sequence_gap"] = visibility["sequence_gap"] as? Bool ?? true
                frame["on_screen"] = visibility["visible"] as? Bool ?? false
                frame["alpha"] = visibility["alpha"] as? Double ?? 0
                frame["first_visible_geometry"] = firstVisibleGeometry
                frame["first_visible_geometry_monotonic_seconds"] = geometryCapturedAt
                frame["first_layer0_sample_sequence"] = firstVisibleSequence
                frame["first_complete_frame_sequence"] = frame["complete_frame_sequence"]
                snapshot["first_visible"] = frame
                snapshot["observation"] = snapshot
                snapshot["capture"] = [
                    "capture_mode": frame["capture_mode"] ?? "",
                    "pixel_width": frame["pixel_width"] ?? 0,
                    "pixel_height": frame["pixel_height"] ?? 0,
                    "point_pixel_scale": frame["point_pixel_scale"] ?? 0,
                ]
                emit(snapshot)
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        try? await stream.stopCapture()
        monitor?.stop()
        failJSON("workspace_first_visible_timeout", "workspace-first-visible")
    }
    RunLoop.main.run()
    failJSON("workspace_first_visible_runloop_stopped", "workspace-first-visible")
}

let arguments = CommandLine.arguments
guard arguments.count >= 2 else { fail("missing command") }
let command = arguments[1]

if command == "preflight" {
    let frontmost = NSWorkspace.shared.frontmostApplication
    let session = CGSessionCopyCurrentDictionary() as? [String: Any]
    let onConsole = (session?[kCGSessionOnConsoleKey as String] as? NSNumber)?.boolValue == true
    let sessionUserID = (session?[kCGSessionUserIDKey as String] as? NSNumber)?.uint32Value
    emit([
        "accessibility": AXIsProcessTrusted(),
        "screen_capture": CGPreflightScreenCaptureAccess(),
        "session_dictionary_available": session != nil,
        "session_on_console": onConsole,
        "session_user_matches": sessionUserID == geteuid(),
        "frontmost_application_available": frontmost != nil,
    ])
}

guard arguments.count >= 3 else { fail("missing bundle identifier") }
let bundleIdentifier = arguments[2]

if command == "applications" {
    emit(["applications": applicationRecords(bundleIdentifier)])
}

if command == "observe" {
    guard arguments.count >= 4 else { fail("missing window title") }
    let expectedTitle = arguments[3]
    let apps = runningApplications(bundleIdentifier)
    let frontmostPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
    let windows = apps.flatMap { windowRecords($0.processIdentifier, expectedTitle) }
    emit([
        "applications": applicationRecords(bundleIdentifier),
        "frontmost_pid": frontmostPID.map { Int($0) } as Any,
        "windows": windows,
    ])
}

// argv: workspace-first-visible <bundle-id> <title> <anchor-ids> <ready> <destination> <timeout>
if command == "workspace-first-visible" {
    guard #available(macOS 14.0, *) else {
        failJSON("screen_capture_api_unsupported", "workspace-first-visible")
    }
    guard arguments.count >= 8,
          let timeoutSeconds = Double(arguments[7]),
          timeoutSeconds >= 1,
          timeoutSeconds <= 60 else {
        failJSON("workspace_first_visible_arguments_invalid", "workspace-first-visible")
    }
    let expectedTitle = arguments[3]
    let anchorComponentIDs = arguments[4].split(separator: ",").map(String.init)
    let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    guard anchorComponentIDs.count == 3,
          anchorComponentIDs.allSatisfy({
              !$0.isEmpty && $0.count <= 160 &&
              $0.unicodeScalars.allSatisfy({ allowed.contains($0) })
          }) else {
        failJSON("workspace_first_visible_arguments_invalid", "workspace-first-visible")
    }
    runWorkspaceFirstVisible(
        bundleIdentifier: bundleIdentifier,
        expectedTitle: expectedTitle,
        anchorComponentIDs: anchorComponentIDs,
        readyURL: URL(fileURLWithPath: arguments[5]),
        destinationURL: URL(fileURLWithPath: arguments[6]),
        timeoutSeconds: timeoutSeconds
    )
}

// argv: capture <bundle-id> <expected-pid> <window-id> <title> <destination> [include-cursor]
if command == "capture" {
    guard #available(macOS 14.0, *) else {
        failJSON("screen_capture_api_unsupported", "capture")
    }
    guard arguments.count >= 7,
          let expectedPID = pid_t(arguments[3]), expectedPID > 0,
          let rawWindowID = UInt32(arguments[4]) else {
        failJSON("window_capture_arguments_invalid", "capture")
    }
    let expectedTitle = arguments[5]
    let destinationURL = URL(fileURLWithPath: arguments[6])
    let includeCursor = arguments.count >= 8 && arguments[7] == "true"

    _ = NSApplication.shared.setActivationPolicy(.prohibited)

    Task { @MainActor in
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                true,
                onScreenWindowsOnly: true
            )
            guard let window = content.windows.first(where: {
                $0.windowID == CGWindowID(rawWindowID) &&
                $0.windowLayer == 0 &&
                $0.title == expectedTitle &&
                $0.owningApplication?.processID == expectedPID &&
                $0.owningApplication?.bundleIdentifier == bundleIdentifier
            }) else {
                failJSON("capture_window_unavailable", "capture")
            }

            let filter = SCContentFilter(desktopIndependentWindow: window)
            let configuration = SCStreamConfiguration()
            let scale = CGFloat(filter.pointPixelScale)
            configuration.width = max(
                1,
                Int((filter.contentRect.width * scale).rounded(.up))
            )
            configuration.height = max(
                1,
                Int((filter.contentRect.height * scale).rounded(.up))
            )
            configuration.showsCursor = includeCursor
            configuration.shouldBeOpaque = true

            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            )
            guard let body = pngData(image) else {
                failJSON("window_capture_png_encode_failed", "capture")
            }
            do {
                try body.write(to: destinationURL, options: .atomic)
            } catch {
                failJSON("window_capture_png_write_failed", "capture")
            }
            emit([
                "accepted": true,
                "capture_mode": "screen_capture_kit_desktop_independent_window",
                "window_id": Int(rawWindowID),
                "owner_pid": Int(expectedPID),
                "width": image.width,
                "height": image.height,
                "point_pixel_scale": filter.pointPixelScale,
                "content_rect": [
                    "x": filter.contentRect.minX,
                    "y": filter.contentRect.minY,
                    "width": filter.contentRect.width,
                    "height": filter.contentRect.height,
                ],
                "cursor_included": includeCursor,
            ])
        } catch {
            failJSON("window_capture_failed", "capture")
        }
    }
    RunLoop.main.run()
    failJSON("window_capture_runloop_stopped", "capture")
}

// argv: launch <bundle-id> <app-path> <home> <tmpdir>
if command == "launch" {
    guard arguments.count >= 6 else {
        failJSON("launch_services_arguments_invalid", "launch")
    }
    let appURL = URL(fileURLWithPath: arguments[3])
    let home = arguments[4]
    let tmpdir = arguments[5]

    let configuration = NSWorkspace.OpenConfiguration()
    configuration.createsNewApplicationInstance = true
    configuration.activates = true
    configuration.environment = [
        "HOME": home,
        "CFFIXED_USER_HOME": home,
        "TMPDIR": tmpdir,
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    ]

    Task { @MainActor in
        do {
            let app = try await NSWorkspace.shared.openApplication(
                at: appURL,
                configuration: configuration
            )
            guard app.bundleIdentifier == bundleIdentifier else {
                failJSON("launch_services_bundle_identifier_mismatch", "launch")
            }
            emit([
                "accepted": true,
                "pid": Int(app.processIdentifier),
                "bundle_identifier": app.bundleIdentifier ?? "",
                "executable_path": app.executableURL?.path ?? "",
            ])
        } catch {
            failJSON("launch_services_primary_failed", "launch")
        }
    }
    RunLoop.main.run()
    failJSON("launch_services_runloop_stopped", "launch")
}

guard arguments.count >= 4, let pid = pid_t(arguments[3]) else { fail("missing pid") }
guard let app = verifiedApplication(pid, bundleIdentifier) else {
    failJSON("application_identity_mismatch", command)
}

if command == "workspace-drag-capture" {
    guard #available(macOS 14.0, *) else {
        failJSON("screen_capture_api_unsupported", "workspace-drag-capture")
    }
    guard arguments.count >= 7,
          !arguments[4].isEmpty,
          arguments[4].count <= 240,
          let deltaX = Double(arguments[5]),
          abs(deltaX) <= 4096 else {
        failJSON("workspace_action_arguments_invalid", "workspace-drag-capture")
    }
    runWorkspaceDragCapture(
        bundleIdentifier: bundleIdentifier,
        pid: pid,
        token: arguments[4],
        deltaX: CGFloat(deltaX),
        destinationURL: URL(fileURLWithPath: arguments[6])
    )
}

if command == "workspace-release-pointer" {
    emit(["accepted": releaseWorkspacePointer()])
}

if command == "workspace-selector-observe" {
    guard arguments.count == 5,
          workspaceAmendmentSelectors.contains(arguments[4]),
          let payload = workspaceSelectorObservation(pid, arguments[4]) else {
        failJSON("workspace_selector_observation_failed", "workspace-selector-observe")
    }
    emit(payload)
}

if command == "workspace-selector-press" {
    guard arguments.count == 5,
          workspaceAmendmentSelectors.contains(arguments[4]),
          let payload = pressWorkspaceSelector(pid, arguments[4]) else {
        failJSON("workspace_selector_press_failed", "workspace-selector-press")
    }
    emit(payload)
}

if command == "workspace-typography-select" {
    guard arguments.count == 5,
          ["Small", "Default", "Large"].contains(arguments[4]),
          let payload = selectWorkspaceTypographyPreset(pid, arguments[4]) else {
        failJSON("workspace_typography_selection_failed", "workspace-typography-select")
    }
    emit(payload)
}

if command == "workspace-local-scroll-observe" {
    guard arguments.count == 4,
          let payload = localScrollObservation(pid) else {
        failJSON("workspace_local_scroll_observation_failed", "workspace-local-scroll-observe")
    }
    emit(payload)
}

if command == "workspace-local-select" {
    guard arguments.count == 5,
          ["mouse", "keyboard"].contains(arguments[4]),
          let payload = selectWorkspaceLocalResult(pid, arguments[4]) else {
        failJSON("workspace_local_selection_failed", "workspace-local-select")
    }
    emit(payload)
}

if command == "workspace-project-scope-select" {
    guard arguments.count == 4,
          let payload = selectWorkspaceProjectScope(pid) else {
        failJSON("workspace_project_scope_selection_failed", "workspace-project-scope-select")
    }
    emit(payload)
}

if command == "workspace-local-publication-observe" {
    guard arguments.count == 4,
          let payload = localPublicationObservation(pid) else {
        failJSON("workspace_local_publication_observation_failed", "workspace-local-publication-observe")
    }
    emit(payload)
}

if command == "workspace-network-sample" {
    guard arguments.count == 4,
          let executablePath = app.executableURL?.path,
          let payload = workspaceNetworkSample(pid, expectedMainExecutablePath: executablePath) else {
        failJSON("workspace_network_sample_failed", "workspace-network-sample")
    }
    emit(payload)
}

if command == "workspace-markdown-preview-safety" {
    guard arguments.count == 4,
          let payload = sourcePreviewSafetyObservation(pid) else {
        failJSON("workspace_markdown_preview_safety_observation_failed", "workspace-markdown-preview-safety")
    }
    emit(payload)
}

if command == "workspace-local-select-fixture" {
    guard arguments.count == 5,
          let payload = selectWorkspaceLocalFixture(
              pid,
              displayName: arguments[4]
          ) else {
        failJSON(
            "workspace_local_fixture_selection_failed",
            "workspace-local-select-fixture"
        )
    }
    emit(payload)
}

if command == "workspace-toolbar-observe" {
    guard arguments.count == 4,
          let payload = toolbarLayoutObservation(pid) else {
        failJSON("workspace_toolbar_observation_failed", "workspace-toolbar-observe")
    }
    emit(payload)
}

if command == "workspace-layout-snapshot" {
    guard arguments.count == 4,
          let payload = workspaceLayoutSnapshot(pid) else {
        failJSON(
            "workspace_layout_snapshot_unavailable",
            "workspace-layout-snapshot"
        )
    }
    emit(payload)
}

if command == "workspace-steady-snapshot" {
    guard arguments.count >= 5 else {
        failJSON("workspace_steady_snapshot_arguments_invalid", "workspace-steady-snapshot")
    }
    let anchorComponentIDs = arguments[4].split(separator: ",").map(String.init)
    guard anchorComponentIDs.count == 3,
          let payload = workspaceSteadySnapshot(pid, anchorComponentIDs) else {
        failJSON("workspace_steady_snapshot_unavailable", "workspace-steady-snapshot")
    }
    emit(payload)
}

if command == "workspace-snapshot" {
    guard arguments.count >= 5 else {
        failJSON("workspace_snapshot_arguments_invalid", "workspace-snapshot")
    }
    let anchorComponentIDs = arguments[4].split(separator: ",").map(String.init)
    guard anchorComponentIDs.count == 3,
          let payload = workspaceSnapshot(pid, anchorComponentIDs) else {
        failJSON("workspace_snapshot_unavailable", "workspace-snapshot")
    }
    emit(payload)
}

if command == "workspace-press" {
    guard arguments.count >= 5,
          !arguments[4].isEmpty,
          arguments[4].count <= 240 else {
        failJSON("workspace_action_arguments_invalid", "workspace-press")
    }
    emit(["accepted": pressWorkspaceTarget(pid, arguments[4])])
}

if command == "workspace-click" {
    guard arguments.count >= 5,
          componentIDFromToken(arguments[4]) != nil else {
        failJSON("workspace_action_arguments_invalid", "workspace-click")
    }
    emit(
        workspaceClickTarget(pid, arguments[4]) ??
        ["accepted": false]
    )
}

if command == "workspace-picker-observe" {
    guard arguments.count >= 5,
          let timeoutSeconds = Double(arguments[4]),
          timeoutSeconds >= 1,
          timeoutSeconds <= 20 else {
        failJSON("workspace_action_arguments_invalid", "workspace-picker-observe")
    }
    guard let payload = workspacePickerObserve(
        pid,
        bundleIdentifier,
        timeoutSeconds
    ) else {
        failJSON("workspace_picker_observation_failed", "workspace-picker-observe")
    }
    emit(payload)
}

if command == "workspace-picker-closed" {
    guard arguments.count >= 6,
          checkoutDirectoryPickerIdentifiers.contains(arguments[4]),
          let timeoutSeconds = Double(arguments[5]),
          timeoutSeconds >= 1,
          timeoutSeconds <= 20 else {
        failJSON("workspace_action_arguments_invalid", "workspace-picker-closed")
    }
    guard let payload = workspacePickerClosed(
        pid,
        bundleIdentifier,
        arguments[4],
        timeoutSeconds
    ) else {
        failJSON("workspace_picker_closure_unconfirmed", "workspace-picker-closed")
    }
    emit(payload)
}

if command == "workspace-picker-select" {
    guard arguments.count >= 6,
          let timeoutSeconds = Double(arguments[5]),
          timeoutSeconds >= 1,
          timeoutSeconds <= 20 else {
        failJSON("workspace_action_arguments_invalid", "workspace-picker-select")
    }
    guard let payload = workspacePickerSelect(
        pid,
        bundleIdentifier,
        arguments[4],
        timeoutSeconds
    ) else {
        failJSON("workspace_picker_selection_failed", "workspace-picker-select")
    }
    emit(payload)
}

if command == "workspace-hover" {
    guard arguments.count >= 5,
          arguments[4] == "graph-viewport" ||
            arguments[4].hasPrefix("profile:") ||
            componentIDFromToken(arguments[4]) != nil else {
        failJSON("workspace_action_arguments_invalid", "workspace-hover")
    }
    emit(
        workspaceHoverTarget(pid, arguments[4]) ??
        ["accepted": false]
    )
}

if command == "workspace-value-target" {
    guard arguments.count >= 5,
          !arguments[4].isEmpty,
          arguments[4].count <= 80 else {
        failJSON("workspace_action_arguments_invalid", "workspace-value-target")
    }
    emit(
        workspaceValueTarget(pid, arguments[4]) ??
        ["accepted": false]
    )
}

if command == "workspace-value-equals" {
    guard arguments.count >= 6,
          !arguments[4].isEmpty,
          arguments[4].count <= 80,
          arguments[5].count <= 4096 else {
        failJSON("workspace_action_arguments_invalid", "workspace-value-equals")
    }
    emit(["accepted": workspaceValueEquals(pid, arguments[4], arguments[5])])
}

if command == "workspace-drag" {
    guard arguments.count >= 6,
          !arguments[4].isEmpty,
          arguments[4].count <= 240,
          let deltaX = Double(arguments[5]),
          abs(deltaX) <= 4096 else {
        failJSON("workspace_action_arguments_invalid", "workspace-drag")
    }
    emit(["accepted": dragWorkspaceTarget(pid, arguments[4], CGFloat(deltaX))])
}

if command == "workspace-key" {
    guard arguments.count >= 6,
          ["ArrowLeft", "ArrowRight"].contains(arguments[5]) else {
        failJSON("workspace_action_arguments_invalid", "workspace-key")
    }
    emit(keyWorkspaceTarget(pid, arguments[4], arguments[5]) ?? ["accepted": false])
}

if command == "workspace-wheel" {
    guard arguments.count >= 6,
          ["sot-tree-scroll", "center-pane", "local-results"].contains(arguments[4]),
          let deltaY = Int32(arguments[5]),
          abs(Int64(deltaY)) <= 4096 else {
        failJSON("workspace_action_arguments_invalid", "workspace-wheel")
    }
    emit(
        wheelWorkspaceTarget(pid, arguments[4], deltaY) ??
        ["accepted": false]
    )
}

if command == "workspace-tree-key" {
    guard arguments.count >= 6,
          arguments[4] == "sot-tree-first-component",
          arguments[5] == "End" else {
        failJSON("workspace_action_arguments_invalid", "workspace-tree-key")
    }
    emit(
        keyWorkspaceTreeTarget(pid, arguments[4], arguments[5]) ??
        ["accepted": false]
    )
}

if command == "workspace-resize" {
    guard arguments.count >= 6,
          let width = Double(arguments[4]),
          let height = Double(arguments[5]),
          width >= 720,
          width <= 4096,
          height >= 600,
          height <= 4096 else {
        failJSON("workspace_action_arguments_invalid", "workspace-resize")
    }
    emit(["accepted": resizeWorkspaceWindow(pid, CGFloat(width), CGFloat(height))])
}

if command == "terminate" {
    emit(["accepted": app.terminate()])
}

if command == "hide" {
    emit(["accepted": app.hide()])
}

if command == "minimize" {
    guard arguments.count >= 5 else { fail("missing minimized value") }
    let expectedTitle = arguments[4]
    let application = AXUIElementCreateApplication(pid)
    let windows = attribute(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
    guard let window = windows.first(where: {
        stringAttribute($0, kAXTitleAttribute) == expectedTitle &&
        stringAttribute($0, kAXSubroleAttribute) == "AXStandardWindow"
    }) else { fail("main window not found") }
    let result = AXUIElementSetAttributeValue(window, kAXMinimizedAttribute as CFString, kCFBooleanTrue)
    emit(["accepted": result == .success])
}

fail("unsupported command")
'''


class MacOSRuntimeAutomationPort:
    def __init__(self, evidence_dir: Path) -> None:
        self.evidence_dir = evidence_dir
        self.helper: Path | None = None
        self.repeat_requests: dict[int, subprocess.Popen[bytes]] = {}
        self._active_helper_deadline: tuple[float, str, str] | None = None

    @contextmanager
    def helper_deadline(
        self,
        deadline: float,
        *,
        code: str,
        operation: str,
    ) -> Iterator[None]:
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(float(deadline))
            or not isinstance(code, str)
            or not code
            or not isinstance(operation, str)
            or not operation
        ):
            raise RuntimeQualificationError(
                "collector_timeout_arguments_invalid", operation
            )
        previous = self._active_helper_deadline
        self._active_helper_deadline = (float(deadline), code, operation)
        try:
            yield
        finally:
            self._active_helper_deadline = previous

    def _run(
        self,
        argv: list[str],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )

    def _helper_json(
        self,
        *arguments: str,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        if self.helper is None:
            raise RuntimeQualificationError("collector_not_prepared", "collector")
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or float(timeout_seconds) <= 0
        ):
            raise RuntimeQualificationError(
                "collector_timeout_arguments_invalid", arguments[0]
            )
        effective_timeout = (
            float(timeout_seconds) if timeout_seconds is not None else None
        )
        deadline_limited = False
        deadline_context = self._active_helper_deadline
        if deadline_context is not None:
            deadline, deadline_code, deadline_operation = deadline_context
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeQualificationError(
                    deadline_code, deadline_operation
                )
            requested_timeout = (
                effective_timeout
                if effective_timeout is not None
                else DEFAULT_TIMEOUT_SECONDS
            )
            deadline_limited = remaining <= requested_timeout
            effective_timeout = min(requested_timeout, remaining)
        helper_arguments = [str(self.helper), *arguments]
        try:
            if effective_timeout is None:
                result = self._run(helper_arguments)
            else:
                result = self._run(
                    helper_arguments,
                    timeout=effective_timeout,
                )
        except subprocess.TimeoutExpired:
            if deadline_limited and deadline_context is not None:
                raise RuntimeQualificationError(
                    deadline_context[1], deadline_context[2]
                )
            raise RuntimeQualificationError(
                "collector_operation_timeout", arguments[0]
            )
        if result.returncode != 0:
            try:
                failure = json.loads(result.stderr)
                error = failure["error"]
                code = error["code"]
                operation = error["operation"]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
            else:
                if isinstance(code, str) and isinstance(operation, str):
                    raise RuntimeQualificationError(
                        code,
                        operation,
                        blocked=code == "screen_capture_api_unsupported",
                    )
            raise RuntimeQualificationError("collector_operation_failed", arguments[0])
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeQualificationError("collector_output_invalid", arguments[0])
        if not isinstance(payload, dict):
            raise RuntimeQualificationError("collector_output_invalid", arguments[0])
        return payload

    def prepare_collector(self, evidence_dir: Path) -> CollectorEvidence:
        collector_dir = evidence_dir / "collectors"
        collector_dir.mkdir(parents=True, exist_ok=True)
        helper = collector_dir / "macos-runtime-observer"
        with tempfile.TemporaryDirectory(prefix="harness-macos-runtime-observer-") as work:
            work_dir = Path(work)
            source = work_dir / "macos-runtime-observer.swift"
            compiled_helper = work_dir / "macos-runtime-observer"
            module_cache = work_dir / "module-cache"
            module_cache.mkdir()
            source.write_text(_SWIFT_HELPER_SOURCE, encoding="utf-8")
            compile_environment = {
                **os.environ,
                "CLANG_MODULE_CACHE_PATH": str(module_cache),
                "SWIFT_MODULECACHE_PATH": str(module_cache),
            }
            result = self._run(
                [
                    "/usr/bin/xcrun",
                    "swiftc",
                    str(source),
                    "-framework",
                    "AppKit",
                    "-framework",
                    "ApplicationServices",
                    "-framework",
                    "CoreImage",
                    "-framework",
                    "CoreGraphics",
                    "-framework",
                    "CoreMedia",
                    "-framework",
                    "CoreVideo",
                    "-framework",
                    "ImageIO",
                    "-framework",
                    "ScreenCaptureKit",
                    "-framework",
                    "UniformTypeIdentifiers",
                    "-o",
                    str(compiled_helper),
                ],
                timeout=120,
                env=compile_environment,
            )
            if result.returncode != 0 or not compiled_helper.is_file():
                raise RuntimeQualificationError("collector_compile_failed", "swiftc")
            shutil.copy2(compiled_helper, helper)
        if not helper.is_file():
            raise RuntimeQualificationError("collector_compile_failed", "publish")
        self.helper = helper
        return CollectorEvidence(
            collector_id=COLLECTOR_ID,
            sha256=_sha256_file(helper),
            relative_path=helper.relative_to(evidence_dir).as_posix(),
        )

    def preflight(self) -> PermissionPreflight:
        payload = self._helper_json("preflight")
        session_fields = (
            "session_dictionary_available",
            "session_on_console",
            "session_user_matches",
            "frontmost_application_available",
        )
        session_evidence = {
            field: value if isinstance(value := payload.get(field), bool) else None
            for field in session_fields
        }
        if any(session_evidence[field] is not True for field in session_fields):
            raise RuntimeQualificationError(
                "interactive_session_state_unavailable",
                "macOS GUI session preflight",
                blocked=True,
                evidence=session_evidence,
            )
        system_events = self._run(
            [
                "/usr/bin/osascript",
                "-e",
                'tell application "System Events" to count application processes',
            ]
        )
        if system_events.returncode == 0:
            system_events_allowed = True
        else:
            error = system_events.stderr.decode("utf-8", "replace")
            if "-1743" in error or "Not authorized to send Apple events" in error:
                system_events_allowed = False
            else:
                raise RuntimeQualificationError(
                    "system_events_preflight_failed", "System Events process enumeration"
                )
        return PermissionPreflight(
            accessibility=payload.get("accessibility") is True,
            screen_capture=payload.get("screen_capture") is True,
            system_events=system_events_allowed,
        )

    def list_applications(
        self, bundle_identifier: str
    ) -> tuple[ApplicationObservation, ...]:
        payload = self._helper_json("applications", bundle_identifier)
        raw_applications = payload.get("applications")
        if not isinstance(raw_applications, list):
            raise RuntimeQualificationError("collector_output_invalid", "applications")
        applications: list[ApplicationObservation] = []
        for raw in raw_applications:
            if not isinstance(raw, dict):
                raise RuntimeQualificationError("collector_output_invalid", "applications")
            path = Path(str(raw.get("executable_path", "")))
            try:
                body = path.read_bytes()
                executable_sha256 = _sha256_bytes(body)
                build_match = _BUILD_ID_PATTERN.search(body)
                build_id = build_match.group(1).decode("ascii") if build_match else None
            except OSError:
                executable_sha256 = ""
                build_id = None
            applications.append(
                ApplicationObservation(
                    pid=int(raw["pid"]),
                    executable_path=path,
                    executable_sha256=executable_sha256,
                    build_id=build_id,
                )
            )
        return tuple(sorted(applications, key=lambda application: application.pid))

    def terminate(self, pid: int) -> bool:
        return self._helper_json("terminate", BUNDLE_IDENTIFIER, str(pid)).get("accepted") is True

    def launch_primary(
        self, artifact: _ArtifactIdentity, environment: dict[str, str]
    ) -> int:
        payload = self._helper_json(
            "launch",
            BUNDLE_IDENTIFIER,
            str(artifact.app),
            environment["HOME"],
            environment["TMPDIR"],
        )
        try:
            pid = int(payload["pid"])
            executable_path = Path(str(payload["executable_path"])).resolve(strict=True)
            executable_body = executable_path.read_bytes()
            build_match = _BUILD_ID_PATTERN.search(executable_body)
            build_id = build_match.group(1).decode("ascii") if build_match else None
        except (KeyError, OSError, TypeError, ValueError):
            raise RuntimeQualificationError(
                "launch_services_primary_invalid", "launch"
            )
        if (
            payload.get("accepted") is not True
            or pid <= 0
            or payload.get("bundle_identifier") != BUNDLE_IDENTIFIER
            or executable_path != artifact.executable
            or _sha256_bytes(executable_body) != artifact.executable_sha256
            or build_id != artifact.build_id
        ):
            raise RuntimeQualificationError(
                "launch_services_primary_invalid", "launch"
            )
        return pid

    def launch_repeat(
        self, artifact: _ArtifactIdentity, environment: dict[str, str]
    ) -> int:
        request = subprocess.Popen(
            [
                "/usr/bin/open",
                "-na",
                str(artifact.app),
                "--env",
                f"HOME={environment['HOME']}",
                "--env",
                f"CFFIXED_USER_HOME={environment['HOME']}",
                "--env",
                f"TMPDIR={environment['TMPDIR']}",
                "--env",
                f"PATH={environment['PATH']}",
            ],
            cwd=artifact.app.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.repeat_requests[request.pid] = request
        return request.pid

    def terminate_repeat_request(self, pid: int) -> bool:
        request = self.repeat_requests.get(pid)
        if request is None:
            return False
        if request.poll() is not None:
            return True
        request.terminate()
        try:
            request.wait(timeout=5)
        except subprocess.TimeoutExpired:
            request.kill()
            request.wait(timeout=5)
        return request.poll() is not None

    def _pid_alive(self, pid: int) -> bool:
        request = self.repeat_requests.get(pid)
        if request is not None:
            return request.poll() is None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def wait_process_exit(self, pid: int, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                request = self.repeat_requests.get(pid)
                if request is not None:
                    request.wait(timeout=0)
                return True
            time.sleep(0.1)
        return False

    def process_exit_code(self, pid: int) -> int | None:
        request = self.repeat_requests.get(pid)
        return request.poll() if request is not None else None

    def wait_processes_exit(
        self, pids: tuple[int, ...], timeout_seconds: float
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if all(not self._pid_alive(pid) for pid in pids):
                return True
            time.sleep(0.1)
        return False

    def _processes(self, main_pids: tuple[int, ...]) -> tuple[ProcessObservation, ...]:
        result = self._run(["/bin/ps", "-axo", "pid=,ppid=,command="])
        if result.returncode != 0:
            raise RuntimeQualificationError("process_enumeration_failed", "ps")
        rows: dict[int, tuple[int, str]] = {}
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) != 3:
                continue
            try:
                rows[int(fields[0])] = (int(fields[1]), fields[2])
            except ValueError:
                continue
        descendants = set(main_pids)
        changed = True
        while changed:
            changed = False
            for pid, (ppid, _) in rows.items():
                if ppid in descendants and pid not in descendants:
                    descendants.add(pid)
                    changed = True
        observations: list[ProcessObservation] = []
        for pid in sorted(descendants):
            ppid, command = rows.get(pid, (0, ""))
            lowered = command.lower()
            if pid in main_pids:
                role = "main"
            elif "webkit" in lowered and "network" in lowered:
                role = "webkit_network"
            elif "webkit" in lowered and "gpu" in lowered:
                role = "webkit_gpu"
            elif "webkit" in lowered:
                role = "webkit_web_content"
            else:
                role = "helper"
            observations.append(ProcessObservation(pid=pid, ppid=ppid, role=role))
        return tuple(observations)

    def snapshot_workspace_processes(self, pid: int) -> dict[str, object]:
        if pid <= 0:
            raise RuntimeQualificationError(
                "workspace_process_snapshot_arguments_invalid",
                "workspace-process-snapshot",
            )
        applications = self.list_applications(BUNDLE_IDENTIFIER)
        application = next(
            (candidate for candidate in applications if candidate.pid == pid), None
        )
        if application is None:
            raise RuntimeQualificationError(
                "workspace_process_identity_mismatch", "workspace-process-snapshot"
            )
        processes = self._processes((pid,))
        if (
            not processes
            or len(processes) > 64
            or sum(process.role == "main" for process in processes) != 1
            or not any(
                process.pid == pid and process.role == "main"
                for process in processes
            )
        ):
            raise RuntimeQualificationError(
                "workspace_process_snapshot_invalid", "workspace-process-snapshot"
            )
        helper_pids = tuple(
            process.pid for process in processes if process.role != "main"
        )
        return {
            "accepted": True,
            "main_pid": pid,
            "main_executable_path": str(application.executable_path),
            "main_executable_sha256": application.executable_sha256,
            "main_build_id": application.build_id,
            "processes": [asdict(process) for process in processes],
            "known_helper_pids": list(helper_pids),
            "known_helper_count": len(helper_pids),
            "scope": "exact_main_process_and_descendants",
        }

    def verify_workspace_process_cleanup(
        self,
        executable: Path,
        known_helper_pids: tuple[int, ...],
        timeout_seconds: float,
    ) -> dict[str, object]:
        if (
            not 0.1 <= timeout_seconds <= 60.0
            or len(known_helper_pids) > 64
            or any(
                not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                for pid in known_helper_pids
            )
        ):
            raise RuntimeQualificationError(
                "workspace_process_cleanup_arguments_invalid",
                "workspace-process-cleanup",
            )
        try:
            exact_executable = executable.resolve(strict=True)
        except OSError:
            raise RuntimeQualificationError(
                "workspace_process_cleanup_executable_invalid",
                "workspace-process-cleanup",
            )
        unique_helpers = tuple(sorted(set(known_helper_pids)))
        helpers_exited = not unique_helpers or self.wait_processes_exit(
            unique_helpers, timeout_seconds
        )
        observation = self.observe_until(
            exact_executable,
            BUNDLE_IDENTIFIER,
            MAIN_WINDOW_TITLE,
            ObservationExpectation(absent=True),
            timeout_seconds,
        )
        exact_applications = tuple(
            application
            for application in self.list_applications(BUNDLE_IDENTIFIER)
            if application.executable_path.resolve() == exact_executable
        )
        live_helpers = tuple(
            pid for pid in unique_helpers if self._pid_alive(pid)
        )
        evidence: dict[str, object] = {
            "accepted": True,
            "main_process_count": len(observation.main_pids),
            "main_window_count": len(observation.main_windows),
            "application_count": len(exact_applications),
            "known_helper_pids": list(unique_helpers),
            "known_helper_count": len(unique_helpers),
            "live_known_helper_pids": list(live_helpers),
            "live_known_helper_count": len(live_helpers),
            "known_helpers_exited": helpers_exited and not live_helpers,
            "scope": "exact_packaged_artifact_and_previously_observed_descendants",
        }
        if observation.main_pids or observation.main_windows or exact_applications:
            raise RuntimeQualificationError(
                "workspace_process_cleanup_main_remained",
                "workspace-process-cleanup",
            )
        if not helpers_exited or live_helpers:
            raise RuntimeQualificationError(
                "workspace_process_cleanup_helper_remained",
                "workspace-process-cleanup",
            )
        return evidence

    def _observe(
        self, executable: Path, bundle_identifier: str, window_title: str
    ) -> RuntimeObservation:
        payload = self._helper_json("observe", bundle_identifier, window_title)
        raw_apps = payload.get("applications")
        raw_windows = payload.get("windows")
        if not isinstance(raw_apps, list) or not isinstance(raw_windows, list):
            raise RuntimeQualificationError("collector_output_invalid", "observe")
        exact = executable.resolve()
        main_pids = tuple(
            sorted(
                int(raw["pid"])
                for raw in raw_apps
                if isinstance(raw, dict)
                and Path(str(raw.get("executable_path", ""))).resolve() == exact
            )
        )
        hidden_pids = tuple(
            sorted(
                int(raw["pid"])
                for raw in raw_apps
                if isinstance(raw, dict)
                and int(raw["pid"]) in main_pids
                and raw.get("hidden") is True
            )
        )
        windows = tuple(
            WindowObservation(
                window_id=int(raw["window_id"]),
                owner_pid=int(raw["owner_pid"]),
                title=str(raw["title"]),
                role=str(raw["role"]),
                subrole=str(raw["subrole"]),
                on_screen=raw.get("on_screen") is True,
                minimized=raw.get("minimized") is True,
                focused=raw.get("focused") is True,
                main=raw.get("main") is True,
            )
            for raw in raw_windows
            if isinstance(raw, dict) and int(raw.get("owner_pid", -1)) in main_pids
        )
        raw_frontmost = payload.get("frontmost_pid")
        frontmost = int(raw_frontmost) if isinstance(raw_frontmost, int) else None
        return RuntimeObservation(
            main_pids=main_pids,
            processes=self._processes(main_pids),
            main_windows=windows,
            frontmost_pid=frontmost,
            hidden_pids=hidden_pids,
        )

    def observe_until(
        self,
        executable: Path,
        bundle_identifier: str,
        window_title: str,
        expectation: ObservationExpectation,
        timeout_seconds: float,
    ) -> RuntimeObservation:
        deadline = time.monotonic() + timeout_seconds
        last = RuntimeObservation((), (), (), None, ())
        while time.monotonic() < deadline:
            last = self._observe(executable, bundle_identifier, window_title)
            if _observation_matches(last, expectation):
                return last
            time.sleep(0.1)
        return last

    def observe_workspace(
        self,
        pid: int,
        anchor_component_ids: tuple[str, str, str],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        if pid <= 0 or len(anchor_component_ids) != 3 or any(
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", component_id)
            for component_id in anchor_component_ids
        ):
            raise RuntimeQualificationError(
                "workspace_observation_arguments_invalid", "workspace-snapshot"
            )
        arguments = (
            "workspace-snapshot",
            BUNDLE_IDENTIFIER,
            str(pid),
            ",".join(anchor_component_ids),
        )
        payload = (
            self._helper_json(*arguments, timeout_seconds=timeout_seconds)
            if timeout_seconds is not None
            else self._helper_json(*arguments)
        )
        targets = payload.get("targets")
        if (
            payload.get("accepted") is not True
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or not isinstance(targets, list)
            or len(targets) > 24
            or any(not isinstance(target, dict) for target in targets)
        ):
            raise RuntimeQualificationError(
                "workspace_observation_invalid", "workspace-snapshot"
            )
        return payload

    def observe_workspace_steady(
        self,
        pid: int,
        anchor_component_ids: tuple[str, str, str],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        if pid <= 0 or len(anchor_component_ids) != 3 or any(
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", component_id)
            for component_id in anchor_component_ids
        ):
            raise RuntimeQualificationError(
                "workspace_steady_snapshot_arguments_invalid",
                "workspace-steady-snapshot",
            )
        arguments = (
            "workspace-steady-snapshot",
            BUNDLE_IDENTIFIER,
            str(pid),
            ",".join(anchor_component_ids),
        )
        payload = (
            self._helper_json(*arguments, timeout_seconds=timeout_seconds)
            if timeout_seconds is not None
            else self._helper_json(*arguments)
        )
        token = payload.get("stability_token")

        def valid_frame(value: object, *, nullable: bool = False) -> bool:
            if nullable and value is None:
                return True
            return (
                isinstance(value, dict)
                and set(value) == {"x", "y", "width", "height"}
                and all(
                    isinstance(value.get(key), int)
                    and not isinstance(value.get(key), bool)
                    for key in ("x", "y", "width", "height")
                )
                and int(value["width"]) >= 0
                and int(value["height"]) >= 0
            )

        def bounded_identifier(value: object, *, nullable: bool = False) -> bool:
            if nullable and value is None:
                return True
            return (
                isinstance(value, str)
                and bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value))
            )

        def valid_camera(value: object) -> bool:
            if not isinstance(value, dict):
                return False
            base_keys = {
                "ready",
                "scale",
                "level",
                "zoom_out_enabled",
                "fit_enabled",
                "zoom_in_enabled",
            }
            if (
                set(value) != base_keys
                and set(value) != base_keys | {"position", "target"}
            ) or not isinstance(value.get("ready"), bool):
                return False
            if value.get("scale") is not None and not isinstance(
                value.get("scale"), (int, float)
            ):
                return False
            if value.get("level") is not None and value.get("level") not in {
                "개요", "상세", "최대"
            }:
                return False
            if any(
                not isinstance(value.get(key), bool)
                for key in ("zoom_out_enabled", "fit_enabled", "zoom_in_enabled")
            ):
                return False
            for key in ("position", "target"):
                vector = value.get(key)
                if vector is None:
                    continue
                if (
                    not isinstance(vector, dict)
                    or set(vector) != {"x", "y", "z"}
                    or any(
                        not isinstance(vector.get(axis), (int, float))
                        for axis in ("x", "y", "z")
                    )
                ):
                    return False
            return True

        def valid_locked_step(value: object) -> bool:
            return value is None or (
                isinstance(value, dict)
                and set(value) == {"workflow_id", "ordinal", "step_id"}
                and bounded_identifier(value.get("workflow_id"))
                and isinstance(value.get("ordinal"), int)
                and not isinstance(value.get("ordinal"), bool)
                and 1 <= int(value["ordinal"]) <= 10_000
                and isinstance(value.get("step_id"), str)
                and 1 <= len(str(value["step_id"])) <= 200
            )

        def valid_scene_layout_identity(
            projection_id: object,
            settled_hash: object,
        ) -> bool:
            if projection_id is None or settled_hash is None:
                return projection_id is None and settled_hash is None
            return (
                bounded_identifier(projection_id)
                and isinstance(settled_hash, str)
                and re.fullmatch(r"[0-9a-f]{16}", settled_hash) is not None
            )

        if (
            payload.get("accepted") is not True
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or isinstance(payload.get("window_id"), bool)
            or int(payload["window_id"]) <= 0
            or payload.get("anchor_target_count") != 3
            or not isinstance(token, dict)
            or set(token) != {
                "layout_revision",
                "layout",
                "graph_body_identity",
                "projection_id",
                "settled_node_positions_hash",
                "renderer",
                "camera",
                "active_profile_id",
                "selected_component_id",
                "selected_relation_node_id",
                "locked_workflow_step",
                "right_detail_id",
                "disclosures",
                "graph_frames",
                "anchor_nodes",
            }
            or not isinstance(token.get("layout_revision"), int)
            or isinstance(token.get("layout_revision"), bool)
            or not isinstance(token.get("layout"), dict)
            or set(token["layout"])
            != {"mode", "window", "shell", "left", "center", "right"}
            or token["layout"].get("mode")
            not in {
                "three-pane",
                "two-column",
                "stacked",
                "left-collapsed",
                "right-collapsed",
                "center-only",
            }
            or any(
                not valid_frame(token["layout"].get(key), nullable=key in {"left", "right"})
                for key in ("window", "shell", "left", "center", "right")
            )
            or not bounded_identifier(token.get("graph_body_identity"), nullable=True)
            or not valid_scene_layout_identity(
                token.get("projection_id"),
                token.get("settled_node_positions_hash"),
            )
            or (
                token.get("graph_body_identity") is not None
                and token.get("projection_id") is None
            )
            or not isinstance(token.get("renderer"), dict)
            or set(token["renderer"])
            != {"availability", "semantic_view_visible", "status", "retry_visible"}
            or token["renderer"].get("availability")
            not in {"ready", "manual_fallback", "unavailable"}
            or not isinstance(token["renderer"].get("semantic_view_visible"), bool)
            or not isinstance(token["renderer"].get("status"), str)
            or len(str(token["renderer"]["status"])) > 240
            or not isinstance(token["renderer"].get("retry_visible"), bool)
            or not valid_camera(token.get("camera"))
            or not bounded_identifier(token.get("active_profile_id"), nullable=True)
            or not bounded_identifier(token.get("selected_component_id"), nullable=True)
            or not bounded_identifier(token.get("selected_relation_node_id"), nullable=True)
            or not valid_locked_step(token.get("locked_workflow_step"))
            or not bounded_identifier(token.get("right_detail_id"), nullable=True)
            or not isinstance(token.get("disclosures"), dict)
            or set(token["disclosures"])
            != {"component_map", "profile_matrix", "left_pane", "right_pane"}
            or any(
                value is not None and not isinstance(value, bool)
                for value in token["disclosures"].values()
            )
            or not isinstance(token.get("graph_frames"), dict)
            or set(token["graph_frames"])
            != {"body", "viewport", "matrix", "matrix_body"}
            or any(
                not valid_frame(value, nullable=True)
                for value in token["graph_frames"].values()
            )
            or not isinstance(token.get("anchor_nodes"), list)
            or len(token["anchor_nodes"]) != 3
            or [anchor.get("component_id") if isinstance(anchor, dict) else None for anchor in token["anchor_nodes"]]
            != list(anchor_component_ids)
            or any(
                not isinstance(anchor, dict)
                or set(anchor) != {"component_id", "frame", "visible", "selected"}
                or not valid_frame(anchor.get("frame"), nullable=True)
                or not isinstance(anchor.get("visible"), bool)
                or not isinstance(anchor.get("selected"), bool)
                for anchor in token["anchor_nodes"]
            )
        ):
            raise RuntimeQualificationError(
                "workspace_steady_snapshot_invalid", "workspace-steady-snapshot"
            )
        return payload

    def observe_workspace_layout(
        self,
        pid: int,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise RuntimeQualificationError(
                "workspace_layout_observation_arguments_invalid",
                "workspace-layout-snapshot",
            )
        arguments = (
            "workspace-layout-snapshot",
            BUNDLE_IDENTIFIER,
            str(pid),
        )
        payload = (
            self._helper_json(*arguments, timeout_seconds=timeout_seconds)
            if timeout_seconds is not None
            else self._helper_json(*arguments)
        )

        def valid_pair(value: object) -> bool:
            return (
                isinstance(value, dict)
                and set(value) == {"left_px", "right_px"}
                and all(
                    isinstance(value.get(key), int)
                    and not isinstance(value.get(key), bool)
                    and int(value[key]) > 0
                    for key in ("left_px", "right_px")
                )
            )

        separator_state = payload.get("separator_state")
        layout_revision = payload.get("layout_revision")
        if (
            payload.get("accepted") is not True
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or isinstance(payload.get("window_id"), bool)
            or int(payload["window_id"]) <= 0
            or payload.get("layout_mode") != "three-pane"
            or not valid_pair(payload.get("preferred_pair"))
            or not valid_pair(payload.get("effective_pair"))
            or not isinstance(separator_state, dict)
            or set(separator_state) != {"left", "right"}
            or any(
                not isinstance(separator_state.get(side), dict)
                or not isinstance(separator_state[side].get("focused"), bool)
                for side in ("left", "right")
            )
            or not isinstance(layout_revision, int)
            or isinstance(layout_revision, bool)
            or layout_revision < 0
            or not isinstance(payload.get("persisted"), bool)
        ):
            raise RuntimeQualificationError(
                "workspace_layout_observation_invalid",
                "workspace-layout-snapshot",
            )
        return payload

    @staticmethod
    def _validate_workspace_frame(
        frame: object, operation: str
    ) -> dict[str, object]:
        if not isinstance(frame, dict):
            raise RuntimeQualificationError(
                "workspace_amendment_frame_invalid", operation
            )
        if set(frame) != {"x", "y", "width", "height"} or any(
            not isinstance(frame.get(key), int)
            or isinstance(frame.get(key), bool)
            for key in ("x", "y", "width", "height")
        ):
            raise RuntimeQualificationError(
                "workspace_amendment_frame_invalid", operation
            )
        if int(frame["width"]) <= 0 or int(frame["height"]) <= 0:
            raise RuntimeQualificationError(
                "workspace_amendment_frame_invalid", operation
            )
        return frame

    @classmethod
    def _validate_workspace_selector_target(
        cls,
        target: object,
        selector: str,
        operation: str,
    ) -> dict[str, object]:
        if not isinstance(target, dict):
            raise RuntimeQualificationError(
                "workspace_selector_readback_invalid", operation
            )
        cls._validate_workspace_frame(target.get("frame"), operation)
        if (
            target.get("selector") != selector
            or not isinstance(target.get("role"), str)
            or not target.get("role")
            or any(
                not isinstance(target.get(key), bool)
                for key in (
                    "visible",
                    "focusable",
                    "focused",
                    "enabled",
                    "selected",
                    "expanded_present",
                )
            )
            or (
                target.get("expanded_present") is True
                and not isinstance(target.get("expanded"), bool)
            )
        ):
            raise RuntimeQualificationError(
                "workspace_selector_readback_invalid", operation
            )
        if selector == "#project-ignore-text":
            if (
                target.get("value_redacted") is not True
                or not isinstance(target.get("value_length"), int)
                or isinstance(target.get("value_length"), bool)
                or int(target["value_length"]) < 0
                or "name" in target
                or "value_text" in target
            ):
                raise RuntimeQualificationError(
                    "workspace_selector_readback_invalid", operation
                )
        elif selector == "[data-local-instance][aria-pressed=true]":
            if (
                target.get("pressed") is not True
                or "name" in target
                or "value_text" in target
            ):
                raise RuntimeQualificationError(
                    "workspace_selector_readback_invalid", operation
                )
        elif selector == "#typography-menu-trigger" and target.get(
            "value_text"
        ) not in {"Small", "Default", "Large"}:
            raise RuntimeQualificationError(
                "workspace_selector_readback_invalid", operation
            )
        elif selector == "[data-correlation-state]" and (
            target.get("correlation_state")
            not in {"verified", "drift", "ambiguous"}
            or not isinstance(target.get("safe_text"), str)
        ):
            raise RuntimeQualificationError(
                "workspace_selector_readback_invalid", operation
            )
        elif selector == "#local-correlation-detail":
            safe_text = target.get("safe_text")
            if (
                not isinstance(safe_text, str)
                or not safe_text
                or "/" in safe_text
            ):
                raise RuntimeQualificationError(
                    "workspace_selector_readback_invalid", operation
                )
        prohibited = {
            "source_body",
            "raw_body",
            "canonical_path",
            "safe_locator",
            "instance_id",
        }
        if prohibited.intersection(target):
            raise RuntimeQualificationError(
                "workspace_selector_readback_invalid", operation
            )
        return target

    @classmethod
    def _validate_workspace_selector_observation(
        cls,
        payload: object,
        pid: int,
        selector: str,
        operation: str,
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise RuntimeQualificationError(
                "workspace_selector_readback_invalid", operation
            )
        present = payload.get("present")
        target_count = payload.get("target_count")
        if (
            payload.get("accepted") is not True
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or isinstance(payload.get("window_id"), bool)
            or int(payload["window_id"]) <= 0
            or payload.get("selector") != selector
            or not isinstance(present, bool)
            or not isinstance(target_count, int)
            or isinstance(target_count, bool)
            or target_count != (1 if present else 0)
            or (present is False and "target" in payload)
        ):
            raise RuntimeQualificationError(
                "workspace_selector_readback_invalid", operation
            )
        if present:
            cls._validate_workspace_selector_target(
                payload.get("target"), selector, operation
            )
        return payload

    def observe_workspace_selector(
        self,
        pid: int,
        selector: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        if pid <= 0 or selector not in WORKSPACE_AMENDMENT_SELECTORS:
            raise RuntimeQualificationError(
                "workspace_selector_arguments_invalid", "workspace-selector-observe"
            )
        arguments = (
            "workspace-selector-observe",
            BUNDLE_IDENTIFIER,
            str(pid),
            selector,
        )
        payload = (
            self._helper_json(*arguments, timeout_seconds=timeout_seconds)
            if timeout_seconds is not None
            else self._helper_json(*arguments)
        )
        return self._validate_workspace_selector_observation(
            payload, pid, selector, "workspace-selector-observe"
        )

    def press_workspace_selector(
        self, pid: int, selector: str
    ) -> dict[str, object]:
        pressable = {
            "#left-pane-disclosure",
            "#right-pane-disclosure",
            "#local-scope-all",
            "#project-ignore-open",
            "#project-ignore-save",
        }
        if pid <= 0 or selector not in pressable:
            raise RuntimeQualificationError(
                "workspace_selector_arguments_invalid", "workspace-selector-press"
            )
        payload = self._helper_json(
            "workspace-selector-press", BUNDLE_IDENTIFIER, str(pid), selector
        )
        before = self._validate_workspace_selector_target(
            payload.get("before"), selector, "workspace-selector-press"
        )
        after = self._validate_workspace_selector_observation(
            payload.get("after"), pid, selector, "workspace-selector-press"
        )
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "selector-press"
            or payload.get("transport") != "ax-press"
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or payload.get("selector") != selector
        ):
            raise RuntimeQualificationError(
                "workspace_selector_press_readback_invalid",
                "workspace-selector-press",
            )
        if selector in {"#left-pane-disclosure", "#right-pane-disclosure"}:
            after_target = after.get("target")
            if (
                not isinstance(after_target, dict)
                or after_target.get("expanded") == before.get("expanded")
            ):
                raise RuntimeQualificationError(
                    "workspace_selector_press_readback_invalid",
                    "workspace-selector-press",
                )
        return payload

    def select_workspace_typography_preset(
        self, pid: int, preset: str
    ) -> dict[str, object]:
        if pid <= 0 or preset not in {"Small", "Default", "Large"}:
            raise RuntimeQualificationError(
                "workspace_typography_arguments_invalid",
                "workspace-typography-select",
            )
        payload = self._helper_json(
            "workspace-typography-select", BUNDLE_IDENTIFIER, str(pid), preset
        )
        self._validate_workspace_selector_target(
            payload.get("before"),
            "#typography-menu-trigger",
            "workspace-typography-select",
        )
        after = self._validate_workspace_selector_target(
            payload.get("after"),
            "#typography-menu-trigger",
            "workspace-typography-select",
        )
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "typography-preset"
            or payload.get("transport") != "ax-press-menuitemradio"
            or payload.get("pid") != pid
            or payload.get("requested_preset") != preset
            or payload.get("value_readback") != preset
            or after.get("value_text") != preset
        ):
            raise RuntimeQualificationError(
                "workspace_typography_readback_invalid",
                "workspace-typography-select",
            )
        return payload

    @classmethod
    def _validate_workspace_local_scroll_observation(
        cls, payload: object, pid: int, operation: str
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise RuntimeQualificationError(
                "workspace_local_scroll_readback_invalid", operation
            )
        cls._validate_workspace_frame(payload.get("scroll_area_frame"), operation)
        cls._validate_workspace_frame(payload.get("visible_bounds"), operation)
        scrollbar = payload.get("vertical_scrollbar")
        selected_row = payload.get("selected_row")
        if not isinstance(scrollbar, dict) or not isinstance(selected_row, dict):
            raise RuntimeQualificationError(
                "workspace_local_scroll_readback_invalid", operation
            )
        result_count = payload.get("result_count")
        visible_result_count = payload.get("visible_result_count")
        selected_index = selected_row.get("selected_row_index")
        selected_present = selected_row.get("present")
        if (
            payload.get("accepted") is not True
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or not isinstance(result_count, int)
            or isinstance(result_count, bool)
            or result_count <= 0
            or not isinstance(visible_result_count, int)
            or isinstance(visible_result_count, bool)
            or not 0 < visible_result_count <= result_count
            or not isinstance(selected_present, bool)
            or not isinstance(scrollbar.get("present"), bool)
        ):
            raise RuntimeQualificationError(
                "workspace_local_scroll_readback_invalid", operation
            )
        if selected_present:
            cls._validate_workspace_frame(selected_row.get("frame"), operation)
            if (
                not isinstance(selected_index, int)
                or isinstance(selected_index, bool)
                or not 0 <= selected_index < result_count
                or selected_row.get("visible") is not True
                or selected_row.get("selected") is not True
                or not isinstance(selected_row.get("focused"), bool)
            ):
                raise RuntimeQualificationError(
                    "workspace_local_scroll_readback_invalid", operation
                )
        elif set(selected_row) != {"present"}:
            raise RuntimeQualificationError(
                "workspace_local_scroll_readback_invalid", operation
            )
        if scrollbar.get("present") is True:
            cls._validate_workspace_frame(scrollbar.get("frame"), operation)
            try:
                value = float(scrollbar["value"])
                minimum = float(scrollbar["minimum"])
                maximum = float(scrollbar["maximum"])
            except (KeyError, TypeError, ValueError):
                raise RuntimeQualificationError(
                    "workspace_local_scroll_readback_invalid", operation
                )
            if (
                not all(math.isfinite(item) for item in (value, minimum, maximum))
                or not minimum <= value <= maximum
            ):
                raise RuntimeQualificationError(
                    "workspace_local_scroll_readback_invalid", operation
                )
        prohibited = {
            "source_body",
            "raw_body",
            "canonical_path",
            "safe_locator",
            "instance_id",
        }
        if prohibited.intersection(payload) or prohibited.intersection(selected_row):
            raise RuntimeQualificationError(
                "workspace_local_scroll_readback_invalid", operation
            )
        return payload

    def observe_workspace_local_scroll(self, pid: int) -> dict[str, object]:
        if pid <= 0:
            raise RuntimeQualificationError(
                "workspace_local_scroll_arguments_invalid",
                "workspace-local-scroll-observe",
            )
        payload = self._helper_json(
            "workspace-local-scroll-observe", BUNDLE_IDENTIFIER, str(pid)
        )
        return self._validate_workspace_local_scroll_observation(
            payload, pid, "workspace-local-scroll-observe"
        )

    @staticmethod
    def _workspace_sha256(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    def observe_workspace_network_sample(self, pid: int) -> dict[str, object]:
        operation = "workspace-network-sample"
        if pid <= 0:
            raise RuntimeQualificationError(
                "workspace_network_sample_arguments_invalid", operation
            )
        payload = self._helper_json(operation, BUNDLE_IDENTIFIER, str(pid))
        main = payload.get("main_process") if isinstance(payload, dict) else None
        helpers = payload.get("webkit_helpers") if isinstance(payload, dict) else None
        allowed_helpers = {
            "webkit_web_content": "com.apple.WebKit.WebContent",
            "webkit_networking": "com.apple.WebKit.Networking",
            "webkit_gpu": "com.apple.WebKit.GPU",
        }
        helper_roles: set[str] = set()
        helpers_valid = isinstance(helpers, list) and bool(helpers)
        if isinstance(helpers, list):
            for helper in helpers:
                if not isinstance(helper, dict):
                    helpers_valid = False
                    continue
                role = helper.get("role")
                name = helper.get("executable_name")
                helper_pid = helper.get("pid")
                helper_ppid = helper.get("ppid")
                if (
                    role not in allowed_helpers
                    or name != allowed_helpers.get(str(role))
                    or not isinstance(helper_pid, int)
                    or isinstance(helper_pid, bool)
                    or helper_pid <= 0
                    or not isinstance(helper_ppid, int)
                    or isinstance(helper_ppid, bool)
                    or helper_ppid <= 0
                    or helper.get("canonical_path_class")
                    != "system-webkit-framework-xpc"
                    or helper.get("bundle_identifier") != name
                    or helper.get("signature_anchor") != "apple"
                    or helper.get("parent_chain_verified") is not True
                    or not self._workspace_sha256(
                        helper.get("executable_path_sha256")
                    )
                ):
                    helpers_valid = False
                    continue
                helper_roles.add(str(role))
        if (
            not isinstance(payload, dict)
            or payload.get("accepted") is not True
            or payload.get("pid") != pid
            or payload.get("lsof_exit_status") != 0
            or payload.get("process_scope")
            != "verified-main-and-webkit-helper-descendants"
            or not isinstance(payload.get("sample_sequence"), int)
            or isinstance(payload.get("sample_sequence"), bool)
            or int(payload["sample_sequence"]) <= 0
            or not isinstance(payload.get("observed_monotonic_ns"), int)
            or isinstance(payload.get("observed_monotonic_ns"), bool)
            or int(payload["observed_monotonic_ns"]) <= 0
            or not isinstance(main, dict)
            or main.get("pid") != pid
            or main.get("role") != "main"
            or not self._workspace_sha256(main.get("executable_path_sha256"))
            or not helpers_valid
            or not {"webkit_web_content", "webkit_networking"}.issubset(
                helper_roles
            )
            or not isinstance(payload.get("endpoint_count"), int)
            or isinstance(payload.get("endpoint_count"), bool)
            or int(payload["endpoint_count"]) < 0
            or not self._workspace_sha256(payload.get("endpoint_fingerprint"))
        ):
            raise RuntimeQualificationError(
                "workspace_network_sample_invalid", operation
            )
        return payload

    def observe_workspace_network_interval(
        self,
        pid: int,
        action: Callable[[], object],
        *,
        minimum_duration_seconds: float = 1.0,
        sample_interval_seconds: float = 0.05,
    ) -> tuple[object, dict[str, object]]:
        operation = "workspace-network-interval"
        if (
            pid <= 0
            or not callable(action)
            or not math.isfinite(minimum_duration_seconds)
            or minimum_duration_seconds <= 0
            or not math.isfinite(sample_interval_seconds)
            or sample_interval_seconds <= 0
            or sample_interval_seconds > minimum_duration_seconds
        ):
            raise RuntimeQualificationError(
                "workspace_network_interval_arguments_invalid", operation
            )
        raise RuntimeQualificationError(
            "process_network_connect_trace_unavailable",
            "process-network-connect-trace-preflight",
            blocked=True,
            evidence={
                "required_collector_id": "macos_process_connect_trace_v1",
                "required_semantics": "lossless-pid-tree-connect-attempt-stream",
                "observed_backend": "lsof-descriptor-snapshot",
                "event_stream": False,
                "overflow_detection": False,
                "prelaunch_ready_barrier": False,
                "audit_token_attribution": False,
                "short_lived_failed_attempt_coverage": False,
                "required_entitlement": (
                    "com.apple.developer.networking.networkextension"
                ),
            },
        )

    def observe_workspace_markdown_preview_safety(
        self, pid: int
    ) -> dict[str, object]:
        operation = "workspace-markdown-preview-safety"
        if pid <= 0:
            raise RuntimeQualificationError(
                "workspace_markdown_preview_safety_arguments_invalid", operation
            )
        payload = self._helper_json(operation, BUNDLE_IDENTIFIER, str(pid))
        role_counts = (
            payload.get("forbidden_ax_role_counts")
            if isinstance(payload, dict)
            else None
        )
        web_area_identity = (
            payload.get("app_web_area_identity")
            if isinstance(payload, dict)
            else None
        )
        semantic_role_order = (
            payload.get("semantic_role_order")
            if isinstance(payload, dict)
            else None
        )
        expected_semantic_role_order = ["heading", "list", "list", "table"]
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {
                "accepted",
                "pid",
                "window_id",
                "preview_present",
                "preview_frame",
                "selected_display_name",
                "selected_state_observed",
                "preview_heading",
                "render_completion_observed",
                "rendered_semantic_count",
                "semantic_role_order",
                "forbidden_ax_role_counts",
                "app_web_area_identity",
            }
            or payload.get("accepted") is not True
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or isinstance(payload.get("window_id"), bool)
            or payload.get("preview_present") is not True
            or not isinstance(payload.get("preview_frame"), dict)
            or payload.get("selected_display_name") != "runtime-verification"
            or payload.get("selected_state_observed") is not True
            or payload.get("preview_heading") != "전체 원문"
            or payload.get("render_completion_observed") is not True
            or semantic_role_order != expected_semantic_role_order
            or payload.get("rendered_semantic_count")
            != len(expected_semantic_role_order)
            or not isinstance(role_counts, dict)
            or role_counts
            != {
                "link": 0,
                "image": 0,
                "embedded_web_area": 0,
            }
            or web_area_identity
            != {
                "role": "AXWebArea",
                "url_class": "tauri-app-local",
                "window_id": payload.get("window_id"),
            }
        ):
            raise RuntimeQualificationError(
                "workspace_markdown_preview_safety_invalid", operation
            )
        return payload

    def select_workspace_local_result(
        self, pid: int, input_method: str
    ) -> dict[str, object]:
        if pid <= 0 or input_method not in {"mouse", "keyboard"}:
            raise RuntimeQualificationError(
                "workspace_local_selection_arguments_invalid",
                "workspace-local-select",
            )
        payload = self._helper_json(
            "workspace-local-select",
            BUNDLE_IDENTIFIER,
            str(pid),
            input_method,
        )
        before = self._validate_workspace_local_scroll_observation(
            payload.get("before"), pid, "workspace-local-select"
        )
        after = self._validate_workspace_local_scroll_observation(
            payload.get("after"), pid, "workspace-local-select"
        )
        before_row = before["selected_row"]
        after_row = after["selected_row"]
        expected_transport = (
            "cg-event-mouse-click"
            if input_method == "mouse"
            else "cg-event-keyboard-arrow-down"
        )
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "local-result-selection"
            or payload.get("transport") != expected_transport
            or payload.get("input") != input_method
            or payload.get("pid") != pid
            or payload.get("selection_changed") is not True
            or not isinstance(before_row, dict)
            or not isinstance(after_row, dict)
            or after_row.get("present") is not True
            or (
                before_row.get("present") is True
                and before_row.get("selected_row_index")
                == after_row.get("selected_row_index")
            )
        ):
            raise RuntimeQualificationError(
                "workspace_local_selection_readback_invalid",
                "workspace-local-select",
            )
        return payload

    def select_workspace_local_fixture(
        self, pid: int, display_name: str
    ) -> dict[str, object]:
        operation = "workspace-local-select-fixture"
        if (
            pid <= 0
            or display_name != "runtime-verification"
        ):
            raise RuntimeQualificationError(
                "workspace_local_fixture_selection_arguments_invalid", operation
            )
        payload = self._helper_json(
            operation,
            BUNDLE_IDENTIFIER,
            str(pid),
            display_name,
        )
        if (
            not isinstance(payload, dict)
            or payload.get("accepted") is not True
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or isinstance(payload.get("window_id"), bool)
            or payload.get("transport") != "cg-event-mouse-click"
            or payload.get("requested_display_name") != display_name
            or payload.get("selected_display_name") != display_name
            or payload.get("selection_activation_observed") is not True
        ):
            raise RuntimeQualificationError(
                "workspace_local_fixture_selection_invalid", operation
            )
        return payload

    def select_workspace_project_scope(self, pid: int) -> dict[str, object]:
        operation = "workspace-project-scope-select"
        if pid <= 0:
            raise RuntimeQualificationError(
                "workspace_project_scope_arguments_invalid", operation
            )
        payload = self._helper_json(
            operation, BUNDLE_IDENTIFIER, str(pid)
        )
        selected = payload.get("selected_project")
        ignore_action = payload.get("ignore_action")
        if not isinstance(selected, dict) or not isinstance(ignore_action, dict):
            raise RuntimeQualificationError(
                "workspace_project_scope_readback_invalid", operation
            )
        self._validate_workspace_frame(selected.get("frame"), operation)
        self._validate_workspace_selector_observation(
            ignore_action, pid, "#project-ignore-open", operation
        )
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "local-project-scope-selection"
            or payload.get("transport") != "ax-press"
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or isinstance(payload.get("window_id"), bool)
            or int(payload["window_id"]) <= 0
            or payload.get("project_identity_redacted") is not True
            or selected.get("role") not in {"AXButton", "AXRow"}
            or selected.get("visible") is not True
            or selected.get("selected") is not True
            or any(
                f'"{key}"' in serialized
                for key in (
                    "project_id",
                    "canonical_path",
                    "safe_locator",
                    "instance_id",
                )
            )
            or "/Users/" in serialized
        ):
            raise RuntimeQualificationError(
                "workspace_project_scope_readback_invalid", operation
            )
        return payload

    def observe_workspace_local_publication(
        self, pid: int
    ) -> dict[str, object]:
        operation = "workspace-local-publication-observe"
        if pid <= 0:
            raise RuntimeQualificationError(
                "workspace_local_publication_arguments_invalid", operation
            )
        payload = self._helper_json(
            operation, BUNDLE_IDENTIFIER, str(pid)
        )
        prefix = payload.get("snapshot_identity_prefix")
        result_count = payload.get("result_count")
        ignore_save_outcome = payload.get("ignore_save_outcome")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "local-publication"
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or isinstance(payload.get("window_id"), bool)
            or int(payload["window_id"]) <= 0
            or not isinstance(prefix, str)
            or re.fullmatch(r"[A-Za-z0-9]{6,64}", prefix) is None
            or payload.get("last_complete_visible") is not True
            or payload.get("scan_phase")
            not in {"complete", "running", "queued", "partial", "failed"}
            or ignore_save_outcome not in {"accepted", "queued", "busy", "none"}
            or not isinstance(result_count, int)
            or isinstance(result_count, bool)
            or result_count < 0
            or any(
                f'"{key}"' in serialized
                for key in (
                    "instance_id",
                    "project_id",
                    "canonical_path",
                    "safe_locator",
                    "source_body",
                    "raw_body",
                )
            )
            or "/Users/" in serialized
        ):
            raise RuntimeQualificationError(
                "workspace_local_publication_readback_invalid", operation
            )
        return payload

    def observe_workspace_toolbar_layout(
        self, pid: int
    ) -> dict[str, object]:
        operation = "workspace-toolbar-observe"
        if pid <= 0:
            raise RuntimeQualificationError(
                "workspace_toolbar_arguments_invalid", operation
            )
        payload = self._helper_json(
            operation, BUNDLE_IDENTIFIER, str(pid)
        )
        targets = payload.get("targets")
        tab_order = payload.get("tab_order")
        expected = {
            "left-disclosure",
            "identity",
            "repository",
            "checkout-register",
            "load-sot",
            "appearance",
            "typography",
            "right-disclosure",
        }
        if not isinstance(targets, dict) or set(targets) != expected:
            raise RuntimeQualificationError(
                "workspace_toolbar_readback_invalid", operation
            )
        self._validate_workspace_frame(payload.get("window_frame"), operation)
        for target in targets.values():
            if (
                not isinstance(target, dict)
                or not isinstance(target.get("role"), str)
                or not target.get("role")
                or target.get("visible") is not True
                or not isinstance(target.get("focusable"), bool)
            ):
                raise RuntimeQualificationError(
                    "workspace_toolbar_readback_invalid", operation
                )
            self._validate_workspace_frame(target.get("frame"), operation)
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "toolbar-layout"
            or payload.get("pid") != pid
            or not isinstance(payload.get("window_id"), int)
            or isinstance(payload.get("window_id"), bool)
            or int(payload["window_id"]) <= 0
            or payload.get("target_count") != len(expected)
            or tab_order != list(WORKSPACE_TOOLBAR_TAB_ORDER)
            or any(
                f'"{key}"' in serialized
                for key in (
                    "name",
                    "value_text",
                    "canonical_path",
                    "safe_locator",
                    "instance_id",
                    "project_id",
                )
            )
            or "/Users/" in serialized
        ):
            raise RuntimeQualificationError(
                "workspace_toolbar_readback_invalid", operation
            )
        return payload

    def prearm_workspace_first_visible(
        self,
        artifact: _ArtifactIdentity,
        anchor_component_ids: tuple[str, str, str],
        destination: Path,
        timeout_seconds: float = 15.0,
    ) -> WorkspaceFirstVisibleHandle:
        if self.helper is None:
            raise RuntimeQualificationError("collector_not_prepared", "workspace-first-visible")
        if (
            artifact.app.suffix != ".app"
            or artifact.build_id == ""
            or len(anchor_component_ids) != 3
            or any(
                not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", component_id)
                for component_id in anchor_component_ids
            )
            or not 1.0 <= timeout_seconds <= 60.0
        ):
            raise RuntimeQualificationError(
                "workspace_first_visible_arguments_invalid", "workspace-first-visible"
            )
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        ready_path = destination.with_name(f".{destination.name}.collector-ready")
        if destination.exists() or ready_path.exists():
            raise RuntimeQualificationError(
                "workspace_first_visible_destination_exists", "workspace-first-visible"
            )
        process = subprocess.Popen(
            [
                str(self.helper),
                "workspace-first-visible",
                BUNDLE_IDENTIFIER,
                MAIN_WINDOW_TITLE,
                ",".join(anchor_component_ids),
                str(ready_path),
                str(destination),
                str(timeout_seconds),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        started = time.monotonic()
        deadline = started + min(timeout_seconds, 5.0)
        while time.monotonic() < deadline:
            if ready_path.is_file():
                try:
                    ready_body = ready_path.read_bytes()
                except OSError:
                    ready_body = b""
                if ready_body != b"ready\n":
                    process.terminate()
                    process.wait(timeout=2)
                    raise RuntimeQualificationError(
                        "workspace_first_visible_ready_invalid", "workspace-first-visible"
                    )
                return WorkspaceFirstVisibleHandle(
                    process=process,
                    ready_path=ready_path,
                    destination=destination,
                    anchor_component_ids=anchor_component_ids,
                    started_monotonic=started,
                )
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=1)
                self._raise_workspace_collector_failure(
                    stderr,
                    "workspace_first_visible_prearm_failed",
                )
            time.sleep(0.01)
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        ready_path.unlink(missing_ok=True)
        raise RuntimeQualificationError(
            "workspace_first_visible_ready_timeout", "workspace-first-visible"
        )

    def wait_workspace_first_visible(
        self,
        handle: WorkspaceFirstVisibleHandle,
        expected_pid: int,
        timeout_seconds: float = 15.0,
    ) -> dict[str, object]:
        if expected_pid <= 0 or not 1.0 <= timeout_seconds <= 60.0:
            self.cancel_workspace_first_visible(handle)
            raise RuntimeQualificationError(
                "workspace_first_visible_arguments_invalid", "workspace-first-visible"
            )
        try:
            stdout, stderr = handle.process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self.cancel_workspace_first_visible(handle)
            raise RuntimeQualificationError(
                "workspace_first_visible_wait_timeout", "workspace-first-visible"
            )
        finally:
            handle.ready_path.unlink(missing_ok=True)
        if handle.process.returncode != 0:
            self._raise_workspace_collector_failure(
                stderr,
                "workspace_first_visible_collector_failed",
            )
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            raise RuntimeQualificationError(
                "workspace_first_visible_output_invalid", "workspace-first-visible"
            )
        if not isinstance(payload, dict):
            raise RuntimeQualificationError(
                "workspace_first_visible_output_invalid", "workspace-first-visible"
            )
        first_visible = payload.get("first_visible")
        targets = payload.get("targets")
        if (
            payload.get("accepted") is not True
            or payload.get("pid") != expected_pid
            or not isinstance(payload.get("window_id"), int)
            or not isinstance(targets, list)
            or len(targets) > 24
            or any(not isinstance(target, dict) for target in targets)
            or not isinstance(first_visible, dict)
            or first_visible.get("collector_prearmed") is not True
            or first_visible.get("capture_mode")
            != "screen_capture_kit_prearmed_display_first_complete_window_crop"
            or first_visible.get("stream_started_before_ready") is not True
            or first_visible.get("prearmed_before_launch") is not True
            or first_visible.get("collector_attached_before_visible") is not True
            or first_visible.get("on_screen") is not True
            or not isinstance(first_visible.get("complete_frame_sequence"), int)
            or int(first_visible.get("complete_frame_sequence", 0)) < 1
        ):
            raise RuntimeQualificationError(
                "workspace_first_visible_output_invalid", "workspace-first-visible"
            )
        visibility = first_visible.get("visibility")
        if not isinstance(visibility, dict):
            raise RuntimeQualificationError(
                "workspace_first_visible_sequence_invalid", "workspace-first-visible"
            )
        sequence = visibility.get("visibility_sequence")
        window_id = payload["window_id"]
        try:
            stream_started_at = float(
                first_visible["prearmed_stream_started_monotonic_seconds"]
            )
            ready_written_at = float(first_visible["ready_written_monotonic_seconds"])
            application_detected_at = float(
                visibility["application_detected_monotonic_seconds"]
            )
            identity_bound_at = float(
                visibility["identity_bound_monotonic_seconds"]
            )
            first_visible_at = float(
                visibility["first_visible_monotonic_seconds"]
            )
            geometry_at = float(
                first_visible["first_visible_geometry_monotonic_seconds"]
            )
            first_frame_at = float(first_visible["first_frame_monotonic_seconds"])
            first_visible_sequence = int(
                first_visible["first_layer0_sample_sequence"]
            )
            first_complete_sequence = int(
                first_visible["first_complete_frame_sequence"]
            )
            alpha = float(first_visible["alpha"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeQualificationError(
                "workspace_first_visible_sequence_invalid", "workspace-first-visible"
            )
        geometry = first_visible.get("first_visible_geometry")
        panes = geometry.get("panes") if isinstance(geometry, dict) else None
        if (
            visibility.get("pid") != expected_pid
            or visibility.get("window_id") != window_id
            or visibility.get("late_attach") is not False
            or visibility.get("identity_mismatch") is not False
            or visibility.get("identity_bound") is not True
            or visibility.get("sck_identity_confirmed") is not True
            or visibility.get("visible_before_identity_binding") is not False
            or visibility.get("first_visible_geometry_unavailable") is not False
            or visibility.get("sequence_gap") is not False
            or visibility.get("stream_started") is not True
            or not (
                stream_started_at
                < ready_written_at
                <= application_detected_at
                <= identity_bound_at
                < first_visible_at
                <= geometry_at
                <= first_frame_at
            )
            or first_visible_sequence < 1
            or first_complete_sequence < 1
            or alpha <= 0
            or not isinstance(panes, dict)
            or set(panes) != {"left", "center", "right"}
            or any(
                not isinstance(panes.get(side), dict)
                or any(
                    not isinstance(panes[side].get(key), int)
                    for key in ("x", "y", "width", "height")
                )
                for side in ("left", "center", "right")
            )
            or not isinstance(sequence, list)
            or not 1 <= len(sequence) <= 64
            or not any(
                isinstance(sample, dict)
                and sample.get("pid") == expected_pid
                and sample.get("window_id") == window_id
                and sample.get("layer") == 0
                and sample.get("on_screen") is True
                and sample.get("alpha_positive") is True
                and sample.get("sequence") == first_visible_sequence
                and isinstance(sample.get("alpha"), (int, float))
                and float(sample["alpha"]) > 0
                for sample in sequence
            )
        ):
            raise RuntimeQualificationError(
                "workspace_first_visible_sequence_invalid", "workspace-first-visible"
            )
        try:
            screenshot = handle.destination.read_bytes()
        except OSError:
            screenshot = b""
        if not screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeQualificationError(
                "workspace_first_visible_screenshot_invalid", "workspace-first-visible"
            )
        first_visible["screenshot_sha256"] = _sha256_bytes(screenshot)
        first_visible["screenshot_size"] = len(screenshot)
        return payload

    def cancel_workspace_first_visible(
        self, handle: WorkspaceFirstVisibleHandle
    ) -> None:
        if handle.process.poll() is None:
            handle.process.terminate()
            try:
                handle.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                handle.process.kill()
                handle.process.wait(timeout=2)
        handle.ready_path.unlink(missing_ok=True)

    @staticmethod
    def _raise_workspace_collector_failure(
        stderr: bytes, fallback_code: str
    ) -> None:
        try:
            failure = json.loads(stderr)
            error = failure["error"]
            code = error["code"]
            operation = error["operation"]
        except (json.JSONDecodeError, KeyError, TypeError):
            code = fallback_code
            operation = "workspace-first-visible"
        if not isinstance(code, str) or not isinstance(operation, str):
            code = fallback_code
            operation = "workspace-first-visible"
        raise RuntimeQualificationError(
            code,
            operation,
            blocked=code == "screen_capture_api_unsupported",
        )

    def press_workspace_target(self, pid: int, target: str) -> bool:
        return self._workspace_action("workspace-press", pid, target)

    def click_workspace_target(
        self, pid: int, target: str
    ) -> dict[str, object]:
        if (
            pid <= 0
            or not isinstance(target, str)
            or re.fullmatch(r"component:[A-Za-z0-9_.:-]{1,160}", target) is None
        ):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-click"
            )
        payload = self._helper_json(
            "workspace-click", BUNDLE_IDENTIFIER, str(pid), target
        )
        readback = payload.get("target_readback")
        point = payload.get("pointer_point")
        frame = readback.get("frame") if isinstance(readback, dict) else None
        coordinates = (
            point.get("x") if isinstance(point, dict) else None,
            point.get("y") if isinstance(point, dict) else None,
        )
        frame_values = (
            frame.get("x") if isinstance(frame, dict) else None,
            frame.get("y") if isinstance(frame, dict) else None,
            frame.get("width") if isinstance(frame, dict) else None,
            frame.get("height") if isinstance(frame, dict) else None,
        )
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "pointer-click"
            or payload.get("transport") != "cg-event-mouse-click"
            or payload.get("pid") != pid
            or payload.get("target") != target
            or payload.get("preselected") is not False
            or payload.get("selection_transition_confirmed") is not True
            or not isinstance(readback, dict)
            or readback.get("target") != target
            or readback.get("component_id") != target.removeprefix("component:")
            or readback.get("role") not in {"AXButton", "AXGroup"}
            or readback.get("visible") is not True
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in (*coordinates, *frame_values)
            )
            or int(frame_values[2]) <= 0
            or int(frame_values[3]) <= 0
            or not int(frame_values[0])
            <= int(coordinates[0])
            <= int(frame_values[0]) + int(frame_values[2])
            or not int(frame_values[1])
            <= int(coordinates[1])
            <= int(frame_values[1]) + int(frame_values[3])
        ):
            raise RuntimeQualificationError(
                "workspace_click_readback_invalid", "workspace-click"
            )
        return payload

    @staticmethod
    def _valid_visible_button(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        frame = value.get("frame")
        if not isinstance(frame, dict):
            return False
        dimensions = tuple(frame.get(key) for key in ("x", "y", "width", "height"))
        return (
            value.get("role") == "AXButton"
            and value.get("visible") is True
            and all(isinstance(item, int) and not isinstance(item, bool) for item in dimensions)
            and int(dimensions[2]) > 0
            and int(dimensions[3]) > 0
        )

    def select_native_checkout_directory(
        self,
        pid: int,
        checkout: Path,
        timeout_seconds: float,
    ) -> dict[str, object]:
        try:
            resolved_checkout = checkout.resolve(strict=True)
        except OSError:
            resolved_checkout = checkout
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or not isinstance(checkout, Path)
            or not checkout.is_absolute()
            or not resolved_checkout.is_dir()
            or not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 1 <= timeout_seconds <= DEFAULT_TIMEOUT_SECONDS
        ):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-picker-select"
            )
        payload = self._helper_json(
            "workspace-picker-select",
            BUNDLE_IDENTIFIER,
            str(pid),
            str(resolved_checkout),
            str(float(timeout_seconds)),
        )
        confirm = payload.get("confirm_button")
        pointer = payload.get("pointer_point")
        frame = confirm.get("frame") if isinstance(confirm, dict) else None
        coordinates = (
            pointer.get("x") if isinstance(pointer, dict) else None,
            pointer.get("y") if isinstance(pointer, dict) else None,
        )
        frame_values = tuple(
            frame.get(key) if isinstance(frame, dict) else None
            for key in ("x", "y", "width", "height")
        )
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "native-directory-selection"
            or payload.get("transport")
            != "cg-event-chord-ax-value-cg-event-mouse-click"
            or payload.get("pid") != pid
            or payload.get("panel_identifier")
            not in CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIERS
            or payload.get("path_redacted") is not True
            or payload.get("go_to_folder_value_confirmed") is not True
            or payload.get("panel_closed") is not True
            or not isinstance(confirm, dict)
            or confirm.get("role") != "AXButton"
            or confirm.get("visible") is not True
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in (*coordinates, *frame_values)
            )
            or int(frame_values[2]) <= 0
            or int(frame_values[3]) <= 0
            or not int(frame_values[0])
            <= int(coordinates[0])
            <= int(frame_values[0]) + int(frame_values[2])
            or not int(frame_values[1])
            <= int(coordinates[1])
            <= int(frame_values[1]) + int(frame_values[3])
            or str(resolved_checkout) in json.dumps(payload)
        ):
            raise RuntimeQualificationError(
                "workspace_picker_selection_proof_invalid",
                "workspace-picker-select",
            )
        return payload

    def observe_native_checkout_picker(
        self, pid: int, timeout_seconds: float
    ) -> dict[str, object]:
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 1 <= timeout_seconds <= DEFAULT_TIMEOUT_SECONDS
        ):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-picker-observe"
            )
        payload = self._helper_json(
            "workspace-picker-observe",
            BUNDLE_IDENTIFIER,
            str(pid),
            str(float(timeout_seconds)),
        )
        panel = payload.get("panel")
        default_button = payload.get("default_button")
        cancel_button = payload.get("cancel_button")
        panel_identifier = payload.get("panel_identifier")
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "native-directory-panel-open"
            or payload.get("pid") != pid
            or payload.get("panel_count") != 1
            or panel_identifier not in CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIERS
            or payload.get("path_redacted") is not True
            or payload.get("title_redacted") is not True
            or not isinstance(panel, dict)
            or panel.get("role") != "AXWindow"
            or panel.get("subrole") not in {"AXDialog", "AXStandardWindow"}
            or panel.get("visible") is not True
            or panel.get("identifier") != panel_identifier
            or not self._valid_visible_button(default_button)
            or not self._valid_visible_button(cancel_button)
            or "title" in json.dumps(payload).replace("title_redacted", "")
            or "/" in json.dumps(payload)
        ):
            raise RuntimeQualificationError(
                "workspace_picker_observation_proof_invalid",
                "workspace-picker-observe",
            )
        return payload

    def wait_native_checkout_picker_closed(
        self,
        pid: int,
        panel_identifier: str,
        timeout_seconds: float,
    ) -> dict[str, object]:
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or not isinstance(panel_identifier, str)
            or panel_identifier not in CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIERS
            or not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 1 <= timeout_seconds <= DEFAULT_TIMEOUT_SECONDS
        ):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-picker-closed"
            )
        payload = self._helper_json(
            "workspace-picker-closed",
            BUNDLE_IDENTIFIER,
            str(pid),
            panel_identifier,
            str(float(timeout_seconds)),
        )
        if (
            set(payload)
            != {
                "accepted",
                "kind",
                "pid",
                "panel_identifier",
                "panel_closed",
                "path_redacted",
            }
            or payload.get("accepted") is not True
            or payload.get("kind") != "native-directory-panel-closed"
            or payload.get("pid") != pid
            or payload.get("panel_identifier") != panel_identifier
            or payload.get("panel_closed") is not True
            or payload.get("path_redacted") is not True
            or "title" in json.dumps(payload)
            or "/" in json.dumps(payload)
        ):
            raise RuntimeQualificationError(
                "workspace_picker_closed_proof_invalid", "workspace-picker-closed"
            )
        return payload

    def set_workspace_text(
        self,
        pid: int,
        dom_identifier: str,
        value: str,
        *,
        timeout_seconds: float = WORKSPACE_AX_READINESS_TIMEOUT_SECONDS,
    ) -> bool:
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", dom_identifier)
            or not isinstance(value, str)
            or len(value) > 4096
            or not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= DEFAULT_TIMEOUT_SECONDS
        ):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-set-value"
            )
        editor_script = r'''
on run argv
    if (count of argv) is not 4 then error "invalid arguments"
    set targetPID to item 1 of argv as integer
    set requestedValue to item 2 of argv
    set targetX to item 3 of argv as integer
    set targetY to item 4 of argv as integer
    with timeout of 2 seconds
        tell application "System Events"
            set targetProcesses to every application process whose unix id is targetPID
            if (count of targetProcesses) is not 1 then error "target process unavailable"
            set targetProcess to item 1 of targetProcesses
            set frontmost of targetProcess to true
            repeat with activationAttempt from 1 to 20
                if frontmost of targetProcess then exit repeat
                delay 0.05
            end repeat
            if frontmost of targetProcess is false then error "target activation failed"
            delay 0.25
            click at {targetX, targetY}
            delay 0.1
            set focusedElement to value of attribute "AXFocusedUIElement" of targetProcess
            if role of focusedElement is not in {"AXTextField", "AXTextArea"} then error "target focus mismatch"
            set value of focusedElement to requestedValue
            delay 0.05
            if value of focusedElement is not requestedValue then error "target value mismatch"
        end tell
    end timeout
    return "edited"
end run
'''
        deadline = time.monotonic() + timeout_seconds
        while True:
            target = self._helper_json(
                "workspace-value-target",
                BUNDLE_IDENTIFIER,
                str(pid),
                dom_identifier,
            )
            if target.get("accepted") is True:
                point = target.get("pointer_point")
                readback = target.get("target_readback")
                x = point.get("x") if isinstance(point, dict) else None
                y = point.get("y") if isinstance(point, dict) else None
                frame = readback.get("frame") if isinstance(readback, dict) else None
                frame_x = frame.get("x") if isinstance(frame, dict) else None
                frame_y = frame.get("y") if isinstance(frame, dict) else None
                frame_width = frame.get("width") if isinstance(frame, dict) else None
                frame_height = frame.get("height") if isinstance(frame, dict) else None
                if (
                    target.get("pid") != pid
                    or not isinstance(target.get("window_id"), int)
                    or isinstance(target.get("window_id"), bool)
                    or target["window_id"] <= 0
                    or target.get("identifier") != dom_identifier
                    or not isinstance(x, int)
                    or isinstance(x, bool)
                    or not isinstance(y, int)
                    or isinstance(y, bool)
                    or abs(x) > 32768
                    or abs(y) > 32768
                    or not isinstance(readback, dict)
                    or readback.get("target") != f"id:{dom_identifier}"
                    or readback.get("dom_identifier") != dom_identifier
                    or readback.get("role") not in {"AXTextField", "AXTextArea"}
                    or readback.get("visible") is not True
                    or readback.get("enabled") is not True
                    or not all(
                        isinstance(candidate, int) and not isinstance(candidate, bool)
                        for candidate in (frame_x, frame_y, frame_width, frame_height)
                    )
                    or frame_width <= 0
                    or frame_height <= 0
                    or not frame_x <= x <= frame_x + frame_width
                    or not frame_y <= y <= frame_y + frame_height
                ):
                    raise RuntimeQualificationError(
                        "workspace_value_target_readback_invalid",
                        "workspace-value-target",
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeQualificationError(
                        "workspace_ax_tree_readiness_timeout",
                        dom_identifier,
                    )
                try:
                    result = self._run(
                        [
                            "/usr/bin/osascript",
                            "-e",
                            editor_script,
                            "--",
                            str(pid),
                            value,
                            str(x),
                            str(y),
                        ],
                        timeout=min(3.0, remaining),
                    )
                except subprocess.TimeoutExpired:
                    result = None
                if result is not None and result.returncode != 0:
                    error = result.stderr.decode("utf-8", "replace")
                    if (
                        "-1743" in error
                        or "Not authorized to send Apple events" in error
                        or "not authorized to send Apple events" in error
                    ):
                        raise RuntimeQualificationError(
                            "system_events_automation_denied",
                            "System Events workspace text edit",
                            blocked=True,
                        )
                elif (
                    result is not None
                    and result.stdout.decode("utf-8", "replace").strip() == "edited"
                ):
                    equality = self._helper_json(
                        "workspace-value-equals",
                        BUNDLE_IDENTIFIER,
                        str(pid),
                        dom_identifier,
                        value,
                    )
                    if equality.get("accepted") is True:
                        return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeQualificationError(
                    "workspace_ax_tree_readiness_timeout",
                    dom_identifier,
                )
            time.sleep(min(WORKSPACE_AX_READINESS_POLL_SECONDS, remaining))

    def hover_workspace_target(
        self, pid: int, target: str
    ) -> dict[str, object]:
        component_match = (
            re.fullmatch(r"component:([A-Za-z0-9_.:-]{1,160})", target)
            if isinstance(target, str)
            else None
        )
        profile_match = (
            re.fullmatch(r"profile:([A-Za-z0-9_.:-]{1,160})", target)
            if isinstance(target, str)
            else None
        )
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or (
                component_match is None
                and profile_match is None
                and target != "graph-viewport"
            )
        ):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-hover"
            )
        payload = self._helper_json(
            "workspace-hover", BUNDLE_IDENTIFIER, str(pid), target
        )
        readback = payload.get("target_readback")
        point = payload.get("pointer_point")
        frame = readback.get("frame") if isinstance(readback, dict) else None
        coordinates = (
            point.get("x") if isinstance(point, dict) else None,
            point.get("y") if isinstance(point, dict) else None,
        )
        frame_values = tuple(
            frame.get(key) if isinstance(frame, dict) else None
            for key in ("x", "y", "width", "height")
        )
        integer_values = (*coordinates, *frame_values)
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "pointer-hover"
            or payload.get("transport") != "cg-event-mouse-move"
            or payload.get("pid") != pid
            or payload.get("target") != target
            or not isinstance(readback, dict)
            or readback.get("target") != target
            or not isinstance(readback.get("role"), str)
            or not readback.get("role")
            or not isinstance(readback.get("name"), str)
            or readback.get("visible") is not True
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in integer_values
            )
            or int(frame_values[2]) <= 0
            or int(frame_values[3]) <= 0
            or not int(frame_values[0])
            <= int(coordinates[0])
            <= int(frame_values[0]) + int(frame_values[2])
            or not int(frame_values[1])
            <= int(coordinates[1])
            <= int(frame_values[1]) + int(frame_values[3])
            or (
                component_match is not None
                and (
                    readback.get("role") not in {"AXButton", "AXGroup"}
                    or readback.get("component_id") != component_match.group(1)
                )
            )
            or (
                profile_match is not None
                and (
                    readback.get("role") not in {"AXButton", "AXGroup"}
                    or not isinstance(readback.get("profile_identity"), dict)
                    or readback["profile_identity"].get("profile_id")
                    != profile_match.group(1)
                )
            )
        ):
            raise RuntimeQualificationError(
                "workspace_hover_readback_invalid", "workspace-hover"
            )
        return payload

    def drag_workspace_target(self, pid: int, target: str, delta_x: int) -> bool:
        if not isinstance(delta_x, int) or abs(delta_x) > 4096:
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-drag"
            )
        return self._workspace_action("workspace-drag", pid, target, str(delta_x))

    def drag_capture_workspace_target(
        self,
        pid: int,
        target: str,
        delta_x: int,
        destination: Path,
    ) -> WorkspaceDragCaptureEvidence:
        if (
            pid <= 0
            or not target
            or len(target) > 240
            or not isinstance(delta_x, int)
            or abs(delta_x) > 4096
        ):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-drag-capture"
            )
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        raw_destination = destination.with_name(
            f".{destination.stem}.screen-capture-kit-drag-active.png"
        )
        if destination.exists() or raw_destination.exists():
            raise RuntimeQualificationError(
                "workspace_drag_capture_destination_exists", "workspace-drag-capture"
            )
        normalized = False
        try:
            try:
                payload = self._helper_json(
                    "workspace-drag-capture",
                    BUNDLE_IDENTIFIER,
                    str(pid),
                    target,
                    str(delta_x),
                    str(raw_destination),
                )
            except subprocess.TimeoutExpired:
                self._release_workspace_pointer_best_effort(pid)
                raise RuntimeQualificationError(
                    "workspace_drag_capture_timeout", "workspace-drag-capture"
                )
            action = payload.get("action")
            capture = payload.get("capture")
            if not isinstance(action, dict) or not isinstance(capture, dict):
                raise RuntimeQualificationError(
                    "workspace_drag_capture_output_invalid", "workspace-drag-capture"
                )
            active_target = capture.get("active_target")
            try:
                mouse_down_at = float(action["mouse_down_monotonic_seconds"])
                drag_posted_at = float(action["drag_posted_monotonic_seconds"])
                capture_started_at = float(
                    capture["capture_started_monotonic_seconds"]
                )
                capture_completed_at = float(
                    capture["capture_completed_monotonic_seconds"]
                )
                mouse_up_at = float(action["mouse_up_monotonic_seconds"])
                captured_owner_pid = int(capture["owner_pid"])
                captured_window_id = int(capture["window_id"])
                pixel_width = int(capture["width"])
                pixel_height = int(capture["height"])
                point_pixel_scale = float(capture["point_pixel_scale"])
                sequences = (
                    int(action["mouse_down_sequence"]),
                    int(action["drag_sequence"]),
                    int(action["capture_sequence"]),
                    int(action["mouse_up_sequence"]),
                )
            except (KeyError, TypeError, ValueError):
                raise RuntimeQualificationError(
                    "workspace_drag_capture_output_invalid", "workspace-drag-capture"
                )
            target_frame = (
                active_target.get("frame") if isinstance(active_target, dict) else None
            )
            try:
                active_current = float(active_target["current_value"])
                active_value_description = str(active_target["value_description"])
            except (KeyError, TypeError, ValueError):
                active_current = math.nan
                active_value_description = ""
            expected_active_description = (
                f"{int(active_current)}픽셀 · 드래그 중"
                if math.isfinite(active_current) and active_current.is_integer()
                else ""
            )
            if (
                payload.get("accepted") is not True
                or action.get("kind") != "pointer-drag"
                or action.get("target") != target
                or action.get("delta_x") != delta_x
                or capture.get("capture_mode") != SCREEN_CAPTURE_SOURCE_MODE
                or capture.get("cursor_included") is not True
                or capture.get("pointer_down_during_capture") is not True
                or capture.get("captured_before_mouseup") is not True
                or captured_owner_pid != pid
                or captured_window_id <= 0
                or pixel_width <= 0
                or pixel_height <= 0
                or point_pixel_scale <= 0
                or sequences != (1, 2, 3, 4)
                or not (
                    mouse_down_at
                    <= drag_posted_at
                    <= capture_started_at
                    <= capture_completed_at
                    <= mouse_up_at
                )
                or not isinstance(active_target, dict)
                or active_target.get("target") != target
                or not isinstance(active_target.get("role"), str)
                or not active_target.get("role")
                or active_target.get("visible") is not True
                or active_target.get("orientation") != "vertical"
                or active_target.get("active_state") != "dragging"
                or not math.isfinite(active_current)
                or not active_current.is_integer()
                or active_value_description != expected_active_description
                or not isinstance(target_frame, dict)
                or any(
                    not isinstance(target_frame.get(key), int)
                    for key in ("x", "y", "width", "height")
                )
                or int(target_frame.get("width", 0)) <= 0
                or int(target_frame.get("height", 0)) <= 0
                or not raw_destination.is_file()
                or raw_destination.stat().st_size == 0
            ):
                raise RuntimeQualificationError(
                    "workspace_drag_capture_output_invalid", "workspace-drag-capture"
                )
            result = self._run(
                [
                    "/usr/bin/sips",
                    "--deleteColorManagementProperties",
                    "-s",
                    "format",
                    "png",
                    str(raw_destination),
                    "--out",
                    str(destination),
                ]
            )
            if (
                result.returncode != 0
                or not destination.is_file()
                or destination.stat().st_size == 0
            ):
                raise RuntimeQualificationError(
                    "workspace_drag_capture_normalization_failed",
                    "workspace-drag-capture",
                )
            normalized = True
            return WorkspaceDragCaptureEvidence(
                action={
                    **action,
                    "active_target": active_target,
                    "capture_started_monotonic_seconds": capture_started_at,
                    "capture_completed_monotonic_seconds": capture_completed_at,
                    "captured_before_mouseup": True,
                },
                screenshot=ScreenshotCaptureEvidence(
                    capture_mode=(
                        f"{SCREEN_CAPTURE_MODE}_cursor_included_drag_active"
                    ),
                    pixel_width=pixel_width,
                    pixel_height=pixel_height,
                    point_pixel_scale=point_pixel_scale,
                ),
                destination=destination,
            )
        except Exception:
            self._release_workspace_pointer_best_effort(pid)
            raise
        finally:
            raw_destination.unlink(missing_ok=True)
            if not normalized:
                destination.unlink(missing_ok=True)

    def _release_workspace_pointer_best_effort(self, pid: int) -> None:
        try:
            self._helper_json(
                "workspace-release-pointer", BUNDLE_IDENTIFIER, str(pid)
            )
        except (RuntimeQualificationError, subprocess.TimeoutExpired):
            pass

    def key_workspace_target(self, pid: int, target: str, key: str) -> bool:
        if key not in {"ArrowLeft", "ArrowRight"}:
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-key"
            )
        return self._workspace_action("workspace-key", pid, target, key)

    def key_workspace_target_evidence(
        self, pid: int, target: str, key: str
    ) -> dict[str, object]:
        if (
            pid <= 0
            or target not in {"left-divider", "right-divider"}
            or key not in {"ArrowLeft", "ArrowRight"}
        ):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-key"
            )
        payload = self._helper_json(
            "workspace-key", BUNDLE_IDENTIFIER, str(pid), target, key
        )
        before = payload.get("before")
        after = payload.get("after")
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise RuntimeQualificationError(
                "workspace_keyboard_readback_invalid", "workspace-key"
            )
        self._validate_workspace_divider_record(
            before, target, "workspace-key before", expected_state="normal"
        )
        after_value = self._validate_workspace_divider_record(
            after, target, "workspace-key after", expected_state="normal"
        )
        try:
            value_readback = float(payload["value_readback"])
        except (KeyError, TypeError, ValueError):
            value_readback = math.nan
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "keyboard"
            or payload.get("target") != target
            or payload.get("key") != key
            or payload.get("focused_readback") is not True
            or after.get("focused") is not True
            or not math.isfinite(value_readback)
            or value_readback != after_value
        ):
            raise RuntimeQualificationError(
                "workspace_keyboard_readback_invalid", "workspace-key"
            )
        return payload

    def wheel_workspace_target_evidence(
        self, pid: int, target: str, delta_y: int
    ) -> dict[str, object]:
        if (
            pid <= 0
            or target not in {"sot-tree-scroll", "center-pane", "local-results"}
            or not isinstance(delta_y, int)
            or isinstance(delta_y, bool)
            or abs(delta_y) > 4096
        ):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-wheel"
            )
        payload = self._helper_json(
            "workspace-wheel",
            BUNDLE_IDENTIFIER,
            str(pid),
            target,
            str(delta_y),
        )
        if (
            payload.get("accepted") is not True
            or payload.get("kind")
            != (
                "tree-scroll"
                if target == "sot-tree-scroll"
                else (
                    "local-result-scroll"
                    if target == "local-results"
                    else "workbench-scroll"
                )
            )
            or payload.get("transport") != "cg-event-wheel"
            or payload.get("target") != target
            or payload.get("delta_y") != delta_y
        ):
            raise RuntimeQualificationError(
                "workspace_wheel_readback_invalid", "workspace-wheel"
            )
        return payload

    def key_workspace_tree_target_evidence(
        self, pid: int, target: str, key: str
    ) -> dict[str, object]:
        if (
            pid <= 0
            or target != "sot-tree-first-component"
            or key != "End"
        ):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-tree-key"
            )
        payload = self._helper_json(
            "workspace-tree-key",
            BUNDLE_IDENTIFIER,
            str(pid),
            target,
            key,
        )
        if (
            payload.get("accepted") is not True
            or payload.get("kind") != "tree-scroll"
            or payload.get("transport") != "cg-event-key-end"
            or payload.get("target") != target
            or payload.get("key") != key
            or not isinstance(payload.get("focused_identity"), str)
            or not payload.get("focused_identity")
        ):
            raise RuntimeQualificationError(
                "workspace_tree_keyboard_readback_invalid", "workspace-tree-key"
            )
        return payload

    @staticmethod
    def _validate_workspace_divider_record(
        record: dict[str, object],
        target: str,
        operation: str,
        *,
        expected_state: str,
    ) -> float:
        try:
            current = float(record["current_value"])
            minimum = float(record["minimum_value"])
            maximum = float(record["maximum_value"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeQualificationError(
                "workspace_divider_readback_invalid", operation
            )
        suffix = "" if expected_state == "normal" else " · 드래그 중"
        expected_description = (
            f"{int(current)}픽셀{suffix}"
            if math.isfinite(current) and current.is_integer()
            else ""
        )
        if (
            record.get("target") != target
            or record.get("role") != "AXSplitter"
            or record.get("orientation") != "vertical"
            or not isinstance(record.get("focused"), bool)
            or not all(math.isfinite(value) for value in (current, minimum, maximum))
            or not minimum <= current <= maximum
            or record.get("active_state") != expected_state
            or record.get("value_description") != expected_description
        ):
            raise RuntimeQualificationError(
                "workspace_divider_readback_invalid", operation
            )
        return current

    def resize_workspace_window(self, pid: int, width: int, height: int) -> bool:
        if not (720 <= width <= 4096 and 600 <= height <= 4096):
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", "workspace-resize"
            )
        return self._workspace_action(
            "workspace-resize", pid, str(width), str(height)
        )

    def _workspace_action(self, command: str, pid: int, *arguments: str) -> bool:
        if pid <= 0:
            raise RuntimeQualificationError(
                "workspace_action_arguments_invalid", command
            )
        payload = self._helper_json(
            command, BUNDLE_IDENTIFIER, str(pid), *arguments
        )
        return payload.get("accepted") is True

    def set_minimized(self, pid: int, minimized: bool) -> bool:
        if not minimized:
            raise RuntimeQualificationError("unsupported_window_action", "unminimize")
        return (
            self._helper_json("minimize", BUNDLE_IDENTIFIER, str(pid), MAIN_WINDOW_TITLE).get(
                "accepted"
            )
            is True
        )

    def hide_application(self, pid: int) -> bool:
        script = (
            'tell application "System Events" to set visible of first process '
            f"whose unix id is {pid} to false"
        )
        result = self._run(["/usr/bin/osascript", "-e", script])
        if result.returncode == 0:
            return True
        error = result.stderr.decode("utf-8", "replace")
        if "-1743" in error or "Not authorized to send Apple events" in error:
            raise RuntimeQualificationError(
                "system_events_automation_denied",
                "System Events hide application",
                blocked=True,
            )
        return False

    def capture_window(
        self, owner_pid: int, window_id: int, destination: Path
    ) -> ScreenshotCaptureEvidence:
        settle_seconds = 2.0 if destination.stem == "hidden-restored" else 0.5
        return self._capture_exact_window(
            owner_pid,
            window_id,
            destination,
            settle_seconds=settle_seconds,
            include_cursor=None,
        )

    def capture_workspace_window(
        self,
        owner_pid: int,
        window_id: int,
        destination: Path,
        include_cursor: bool,
    ) -> ScreenshotCaptureEvidence:
        return self._capture_exact_window(
            owner_pid,
            window_id,
            destination,
            settle_seconds=0.1,
            include_cursor=include_cursor,
        )

    def _capture_exact_window(
        self,
        owner_pid: int,
        window_id: int,
        destination: Path,
        *,
        settle_seconds: float,
        include_cursor: bool | None,
    ) -> ScreenshotCaptureEvidence:
        time.sleep(settle_seconds)
        raw_destination = destination.with_name(
            f".{destination.stem}.screen-capture-kit.png"
        )
        normalized = False
        try:
            arguments = [
                "capture",
                BUNDLE_IDENTIFIER,
                str(owner_pid),
                str(window_id),
                MAIN_WINDOW_TITLE,
                str(raw_destination),
            ]
            if include_cursor is not None:
                arguments.append("true" if include_cursor else "false")
            payload = self._helper_json(*arguments)
            try:
                captured_owner_pid = int(payload["owner_pid"])
                captured_window_id = int(payload["window_id"])
                pixel_width = int(payload["width"])
                pixel_height = int(payload["height"])
                point_pixel_scale = float(payload["point_pixel_scale"])
                raw_content_rect = payload["content_rect"]
                if not isinstance(raw_content_rect, dict):
                    raise TypeError
                content_rect = tuple(
                    float(raw_content_rect[key])
                    for key in ("x", "y", "width", "height")
                )
            except (KeyError, TypeError, ValueError):
                raise RuntimeQualificationError(
                    "window_capture_failed", destination.stem
                )
            if (
                payload.get("accepted") is not True
                or payload.get("capture_mode") != SCREEN_CAPTURE_SOURCE_MODE
                or captured_owner_pid != owner_pid
                or captured_window_id != window_id
                or pixel_width <= 0
                or pixel_height <= 0
                or point_pixel_scale <= 0
                or any(not math.isfinite(value) for value in content_rect)
                or content_rect[2] <= 0
                or content_rect[3] <= 0
                or abs(pixel_width - content_rect[2] * point_pixel_scale) > 1
                or abs(pixel_height - content_rect[3] * point_pixel_scale) > 1
                or (
                    include_cursor is not None
                    and payload.get("cursor_included") is not include_cursor
                )
                or not raw_destination.is_file()
                or raw_destination.stat().st_size == 0
            ):
                raise RuntimeQualificationError(
                    "window_capture_failed", destination.stem
                )
            result = self._run(
                [
                    "/usr/bin/sips",
                    "--deleteColorManagementProperties",
                    "-s",
                    "format",
                    "png",
                    str(raw_destination),
                    "--out",
                    str(destination),
                ]
            )
            if (
                result.returncode != 0
                or not destination.is_file()
                or destination.stat().st_size == 0
            ):
                raise RuntimeQualificationError(
                    "window_capture_failed", destination.stem
                )
            normalized = True
            return ScreenshotCaptureEvidence(
                capture_mode=(
                    f"{SCREEN_CAPTURE_MODE}_cursor_included"
                    if include_cursor is True
                    else SCREEN_CAPTURE_MODE
                ),
                pixel_width=pixel_width,
                pixel_height=pixel_height,
                point_pixel_scale=point_pixel_scale,
                content_rect=content_rect,
            )
        finally:
            raw_destination.unlink(missing_ok=True)
            if not normalized:
                destination.unlink(missing_ok=True)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify packaged harness-desktop single-instance runtime behavior."
    )
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--qualification-report", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    report_path = verify_single_instance_macos(
        arguments.app,
        arguments.evidence_root,
        qualification_report=arguments.qualification_report,
        timeout_seconds=arguments.timeout_seconds,
    )
    report = json.loads(report_path.read_text())
    print(
        json.dumps(
            {"report": str(report_path), "status": report["status"]},
            sort_keys=True,
        )
    )
    if report["status"] == "passed":
        return 0
    if report["status"] == "blocked":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
