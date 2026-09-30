from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import pytest

from scripts.package import verify_single_instance_macos as runtime


BUILD_ID = "a" * 32
SINGLE_INSTANCE_CASE_IDS = (
    "first",
    "focus-restored",
    "minimized-restored",
    "hidden-restored",
    "post-quit-relaunch",
)


def test_visible_main_window_title_is_harnesskit() -> None:
    assert runtime.MAIN_WINDOW_TITLE == "HarnessKit"


def _verified_network_sample(
    *, endpoint_count: int = 0, sample_sequence: int = 1
) -> dict[str, object]:
    return {
        "accepted": True,
        "pid": 410,
        "sample_sequence": sample_sequence,
        "observed_monotonic_ns": sample_sequence * 1_000_000,
        "lsof_exit_status": 0,
        "process_scope": "verified-main-and-webkit-helper-descendants",
        "main_process": {
            "pid": 410,
            "role": "main",
            "executable_path_sha256": "a" * 64,
        },
        "webkit_helpers": [
            {
                "pid": 411,
                "ppid": 410,
                "role": "webkit_web_content",
                "executable_name": "com.apple.WebKit.WebContent",
                "executable_path_sha256": "b" * 64,
                "canonical_path_class": "system-webkit-framework-xpc",
                "bundle_identifier": "com.apple.WebKit.WebContent",
                "signature_anchor": "apple",
                "parent_chain_verified": True,
            },
            {
                "pid": 412,
                "ppid": 410,
                "role": "webkit_networking",
                "executable_name": "com.apple.WebKit.Networking",
                "executable_path_sha256": "c" * 64,
                "canonical_path_class": "system-webkit-framework-xpc",
                "bundle_identifier": "com.apple.WebKit.Networking",
                "signature_anchor": "apple",
                "parent_chain_verified": True,
            },
        ],
        "endpoint_count": endpoint_count,
        "endpoint_fingerprint": hashlib.sha256(
            f"endpoint-{endpoint_count}".encode()
        ).hexdigest(),
    }


def test_workspace_network_interval_fails_closed_before_action_without_lossless_collector(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    invoked = False

    def action() -> str:
        nonlocal invoked
        invoked = True
        return "fixture-selected"

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.observe_workspace_network_interval(410, action)

    assert invoked is False
    assert captured.value.code == "process_network_connect_trace_unavailable"
    assert captured.value.operation == "process-network-connect-trace-preflight"
    assert captured.value.blocked is True
    assert captured.value.evidence == {
        "required_collector_id": "macos_process_connect_trace_v1",
        "required_semantics": "lossless-pid-tree-connect-attempt-stream",
        "observed_backend": "lsof-descriptor-snapshot",
        "event_stream": False,
        "overflow_detection": False,
        "prelaunch_ready_barrier": False,
        "audit_token_attribution": False,
        "short_lived_failed_attempt_coverage": False,
        "required_entitlement": "com.apple.developer.networking.networkextension",
    }


def test_workspace_network_sample_rejects_lsof_exit_one_and_missing_webkit_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    invalid = _verified_network_sample()
    invalid["lsof_exit_status"] = 1
    monkeypatch.setattr(port, "_helper_json", lambda *_arguments: invalid)

    with pytest.raises(runtime.RuntimeQualificationError) as lsof_error:
        port.observe_workspace_network_sample(410)
    assert lsof_error.value.code == "workspace_network_sample_invalid"

    invalid = _verified_network_sample()
    invalid["webkit_helpers"] = []
    monkeypatch.setattr(port, "_helper_json", lambda *_arguments: invalid)

    with pytest.raises(runtime.RuntimeQualificationError) as helper_error:
        port.observe_workspace_network_sample(410)
    assert helper_error.value.code == "workspace_network_sample_invalid"

    invalid = _verified_network_sample()
    invalid["webkit_helpers"][0]["canonical_path_class"] = "untrusted"
    monkeypatch.setattr(port, "_helper_json", lambda *_arguments: invalid)

    with pytest.raises(runtime.RuntimeQualificationError) as identity_error:
        port.observe_workspace_network_sample(410)
    assert identity_error.value.code == "workspace_network_sample_invalid"


def test_workspace_markdown_preview_requires_direct_ax_runtime_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    payload = {
        "accepted": True,
        "pid": 410,
        "window_id": 91,
        "preview_present": True,
        "preview_frame": {"x": 30, "y": 40, "width": 300, "height": 420},
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
            "window_id": 91,
        },
    }
    monkeypatch.setattr(port, "_helper_json", lambda *_arguments: payload)

    observed = port.observe_workspace_markdown_preview_safety(410)
    assert observed["app_web_area_identity"] == payload["app_web_area_identity"]

    payload["selected_display_name"] = "different-fixture"
    with pytest.raises(runtime.RuntimeQualificationError) as error:
        port.observe_workspace_markdown_preview_safety(410)
    assert error.value.code == "workspace_markdown_preview_safety_invalid"

    payload["selected_display_name"] = "runtime-verification"
    payload["app_web_area_identity"]["url_class"] = "external"
    with pytest.raises(runtime.RuntimeQualificationError) as web_area_error:
        port.observe_workspace_markdown_preview_safety(410)
    assert web_area_error.value.code == "workspace_markdown_preview_safety_invalid"

    payload["app_web_area_identity"]["url_class"] = "tauri-app-local"
    payload["forbidden_ax_role_counts"]["link"] = 1
    with pytest.raises(runtime.RuntimeQualificationError) as role_error:
        port.observe_workspace_markdown_preview_safety(410)
    assert role_error.value.code == "workspace_markdown_preview_safety_invalid"


def test_workspace_local_fixture_selection_requires_exact_safe_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    payload = {
        "accepted": True,
        "pid": 410,
        "window_id": 91,
        "transport": "cg-event-mouse-click",
        "requested_display_name": "runtime-verification",
        "selected_display_name": "runtime-verification",
        "selection_activation_observed": True,
    }
    monkeypatch.setattr(port, "_helper_json", lambda *_arguments: payload)

    assert port.select_workspace_local_fixture(410, "runtime-verification") == payload

    payload["selected_display_name"] = "different-item"
    with pytest.raises(runtime.RuntimeQualificationError) as error:
        port.select_workspace_local_fixture(410, "runtime-verification")
    assert error.value.code == "workspace_local_fixture_selection_invalid"


def _fixture_app(tmp_path: Path, *, name: str = "harness-desktop.app") -> Path:
    app = tmp_path / name
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


def _observation(
    pid: int,
    window_id: int,
    *,
    minimized: bool = False,
    hidden: bool = False,
    focused: bool = True,
    frontmost: bool = True,
    duplicate_main: bool = False,
) -> runtime.RuntimeObservation:
    processes = [
        runtime.ProcessObservation(pid=pid, ppid=1, role="main"),
        runtime.ProcessObservation(pid=pid + 9, ppid=pid, role="webkit_web_content"),
    ]
    main_pids = [pid]
    if duplicate_main:
        processes.append(runtime.ProcessObservation(pid=pid + 1, ppid=1, role="main"))
        main_pids.append(pid + 1)
    return runtime.RuntimeObservation(
        main_pids=tuple(main_pids),
        processes=tuple(processes),
        main_windows=(
            runtime.WindowObservation(
                window_id=window_id,
                owner_pid=pid,
                title=runtime.MAIN_WINDOW_TITLE,
                role="AXWindow",
                subrole="AXStandardWindow",
                on_screen=not minimized and not hidden,
                minimized=minimized,
                focused=focused,
                main=not minimized and not hidden,
            ),
        ),
        frontmost_pid=pid if frontmost else None,
        hidden_pids=(pid,) if hidden else (),
    )


def _absent() -> runtime.RuntimeObservation:
    return runtime.RuntimeObservation(
        main_pids=(),
        processes=(),
        main_windows=(),
        frontmost_pid=None,
        hidden_pids=(),
    )


class FakePort:
    def __init__(
        self,
        observations: list[runtime.RuntimeObservation],
        *,
        preflight: runtime.PermissionPreflight | None = None,
        applications: tuple[runtime.ApplicationObservation, ...] = (),
        primary_launch_pids: tuple[int, ...] = (101, 202),
        repeat_request_pids: tuple[int, ...] = (301, 302, 303, 304, 305),
        runtime_callback_count: int | None = None,
    ) -> None:
        self.observations = deque(observations)
        self.preflight_result = preflight or runtime.PermissionPreflight(
            accessibility=True,
            screen_capture=True,
            system_events=True,
        )
        self.applications = applications
        self.primary_launch_pids = deque(primary_launch_pids)
        self.repeat_request_pids = deque(repeat_request_pids)
        self.runtime_callback_count = runtime_callback_count
        self.runtime_log_written = False
        self.primary_launches: list[Path] = []
        self.repeat_launches: list[Path] = []
        self.waited_exit: list[int] = []
        self.waited_process_sets: list[tuple[int, ...]] = []
        self.terminated: list[int] = []
        self.terminated_requests: list[int] = []
        self.minimized: list[int] = []
        self.hidden: list[int] = []
        self.captures: list[str] = []

    def prepare_collector(self, evidence_dir: Path) -> runtime.CollectorEvidence:
        collector = evidence_dir / "collectors/macos-runtime-observer"
        collector.parent.mkdir(parents=True, exist_ok=True)
        collector.write_bytes(b"fake-collector")
        return runtime.CollectorEvidence(
            collector_id=runtime.COLLECTOR_ID,
            sha256=hashlib.sha256(collector.read_bytes()).hexdigest(),
            relative_path="collectors/macos-runtime-observer",
        )

    def preflight(self) -> runtime.PermissionPreflight:
        return self.preflight_result

    def list_applications(self, bundle_identifier: str) -> tuple[runtime.ApplicationObservation, ...]:
        assert bundle_identifier == runtime.BUNDLE_IDENTIFIER
        return self.applications

    def terminate(self, pid: int) -> bool:
        self.terminated.append(pid)
        return True

    def _record_launch_environment(self, environment: dict[str, str]) -> None:
        assert set(environment) == {"HOME", "TMPDIR", "PATH"}
        runtime_home = Path(environment["HOME"])
        assert runtime_home.name == "home"
        assert runtime_home.parent.name.startswith("harness-desktop-runtime-")
        assert runtime_home.stat().st_mode & 0o777 == 0o700
        assert Path(environment["TMPDIR"]).parent == runtime_home.parent
        assert environment["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
        if self.runtime_callback_count is not None and not self.runtime_log_written:
            log_path = (
                runtime_home
                / "Library/Logs/io.github.pureliture.harnesskit"
                / runtime.RUNTIME_LOG_FILE_NAME
            )
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                "single-instance relaunch received\n" * self.runtime_callback_count
            )
            self.runtime_log_written = True

    def launch_primary(
        self, artifact: runtime._ArtifactIdentity, environment: dict[str, str]
    ) -> int:
        self._record_launch_environment(environment)
        assert artifact.app.suffix == ".app"
        self.primary_launches.append(artifact.app)
        return self.primary_launch_pids.popleft()

    def launch_repeat(
        self, artifact: runtime._ArtifactIdentity, environment: dict[str, str]
    ) -> int:
        self._record_launch_environment(environment)
        assert artifact.app.suffix == ".app"
        self.repeat_launches.append(artifact.app)
        return self.repeat_request_pids.popleft()

    def terminate_repeat_request(self, pid: int) -> bool:
        self.terminated_requests.append(pid)
        return True

    def wait_process_exit(self, pid: int, timeout_seconds: float) -> bool:
        assert timeout_seconds > 0
        self.waited_exit.append(pid)
        return True

    def process_exit_code(self, pid: int) -> int | None:
        return 0 if pid in self.waited_exit else None

    def wait_processes_exit(self, pids: tuple[int, ...], timeout_seconds: float) -> bool:
        assert timeout_seconds > 0
        self.waited_process_sets.append(tuple(sorted(pids)))
        return True

    def observe_until(
        self,
        executable: Path,
        bundle_identifier: str,
        window_title: str,
        expectation: runtime.ObservationExpectation,
        timeout_seconds: float,
    ) -> runtime.RuntimeObservation:
        assert executable.name == "harness-desktop"
        assert bundle_identifier == runtime.BUNDLE_IDENTIFIER
        assert window_title == runtime.MAIN_WINDOW_TITLE
        assert timeout_seconds > 0
        return self.observations.popleft()

    def set_minimized(self, pid: int, minimized: bool) -> bool:
        assert minimized is True
        self.minimized.append(pid)
        return True

    def hide_application(self, pid: int) -> bool:
        self.hidden.append(pid)
        return True

    def capture_window(
        self, owner_pid: int, window_id: int, destination: Path
    ) -> runtime.ScreenshotCaptureEvidence:
        assert owner_pid > 0
        self.captures.append(destination.stem)
        destination.write_bytes(b"\x89PNG\r\n\x1a\n" + str(window_id).encode())
        return runtime.ScreenshotCaptureEvidence(
            capture_mode=runtime.SCREEN_CAPTURE_MODE,
            pixel_width=2400,
            pixel_height=1600,
            point_pixel_scale=2.0,
        )


@pytest.mark.parametrize(
    ("case_id", "expected_settle_seconds"),
    [
        ("focus-restored", 0.5),
        ("minimized-restored", 0.5),
        ("hidden-restored", 2.0),
    ],
)
def test_macos_capture_isolates_window_and_waits_for_hidden_repaint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    expected_settle_seconds: float,
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    helper_invocations: list[tuple[str, ...]] = []
    run_invocations: list[list[str]] = []
    sleeps: list[float] = []

    def helper(command: str, *_arguments: str) -> dict[str, object]:
        helper_invocations.append((command, *_arguments))
        Path(_arguments[-1]).write_bytes(b"\x89PNG\r\n\x1a\n")
        return {
            "accepted": True,
            "capture_mode": runtime.SCREEN_CAPTURE_SOURCE_MODE,
            "owner_pid": 101,
            "window_id": 42,
            "width": 2400,
            "height": 1600,
            "point_pixel_scale": 2.0,
            "content_rect": {
                "x": 120.0,
                "y": 80.0,
                "width": 1200.0,
                "height": 800.0,
            },
        }

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        run_invocations.append(argv)
        Path(argv[-1]).write_bytes(b"\x89PNG\r\n\x1a\nnormalized")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(port, "_helper_json", helper)
    monkeypatch.setattr(port, "_run", run)
    monkeypatch.setattr(runtime.time, "sleep", sleeps.append)

    destination = tmp_path / f"{case_id}.png"
    raw_destination = tmp_path / f".{case_id}.screen-capture-kit.png"
    evidence = port.capture_window(101, 42, destination)

    assert sleeps == [expected_settle_seconds]
    assert helper_invocations == [
        (
            "capture",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "42",
            runtime.MAIN_WINDOW_TITLE,
            str(raw_destination),
        )
    ]
    assert run_invocations == [
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
    ]
    assert raw_destination.exists() is False
    assert destination.read_bytes().endswith(b"normalized")
    assert evidence.content_rect == (120.0, 80.0, 1200.0, 800.0)


@pytest.mark.parametrize(
    "failure_mode", ["rejected", "missing", "empty", "normalize", "content-rect"]
)
def test_macos_capture_fails_closed_for_invalid_screen_capture_kit_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    destination = tmp_path / "first.png"

    def helper(_command: str, *_arguments: str) -> dict[str, object]:
        raw_destination = Path(_arguments[-1])
        if failure_mode == "empty":
            raw_destination.touch()
        elif failure_mode != "missing":
            raw_destination.write_bytes(b"\x89PNG\r\n\x1a\n")
        return {
            "accepted": failure_mode != "rejected",
            "capture_mode": runtime.SCREEN_CAPTURE_SOURCE_MODE,
            "owner_pid": 101,
            "window_id": 42,
            "width": 2400,
            "height": 1600,
            "point_pixel_scale": 2.0,
            "content_rect": {
                "x": 0.0,
                "y": 0.0,
                "width": 1199.0 if failure_mode == "content-rect" else 1200.0,
                "height": 800.0,
            },
        }

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if failure_mode != "normalize":
            Path(argv[-1]).write_bytes(b"\x89PNG\r\n\x1a\nnormalized")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return subprocess.CompletedProcess(argv, 1, b"", b"normalization failed")

    monkeypatch.setattr(port, "_helper_json", helper)
    monkeypatch.setattr(port, "_run", run)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    with pytest.raises(runtime.RuntimeQualificationError) as error:
        port.capture_window(101, 42, destination)

    assert error.value.code == "window_capture_failed"
    assert list(tmp_path.glob(".*.screen-capture-kit.png")) == []


def test_collector_preserves_structured_screen_capture_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    port.helper = tmp_path / "collector"
    failure = {
        "error": {
            "code": "screen_capture_api_unsupported",
            "operation": "capture",
        }
    }
    completed = subprocess.CompletedProcess(
        [str(port.helper), "capture"],
        2,
        b"",
        json.dumps(failure).encode(),
    )
    monkeypatch.setattr(port, "_run", lambda _argv: completed)

    with pytest.raises(runtime.RuntimeQualificationError) as error:
        port._helper_json("capture")

    assert error.value.code == "screen_capture_api_unsupported"
    assert error.value.operation == "capture"
    assert error.value.blocked is True


def test_helper_deadline_scope_clamps_default_subprocess_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    port.helper = tmp_path / "collector"
    timeouts: list[float] = []

    def run(
        argv: list[str], *, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        timeouts.append(timeout)
        return subprocess.CompletedProcess(argv, 0, b'{"accepted": true}', b"")

    monkeypatch.setattr(port, "_run", run)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 100.0)

    with port.helper_deadline(
        115.0,
        code="workspace_amendment_case_timeout",
        operation="amendment-case",
    ):
        assert port._helper_json("applications", runtime.BUNDLE_IDENTIFIER) == {
            "accepted": True
        }

    assert timeouts == [15.0]


def test_helper_deadline_scope_fails_before_starting_an_expired_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    port.helper = tmp_path / "collector"
    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        port,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("expired helper must not start")
        ),
    )

    with port.helper_deadline(
        115.0,
        code="workspace_amendment_case_timeout",
        operation="amendment-case",
    ):
        clock[0] = 115.0
        with pytest.raises(runtime.RuntimeQualificationError) as captured:
            port._helper_json("applications", runtime.BUNDLE_IDENTIFIER)

    assert captured.value.code == "workspace_amendment_case_timeout"
    assert captured.value.operation == "amendment-case"


def test_collector_compiles_in_ephemeral_workspace_and_publishes_only_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_dir = tmp_path / "evidence"
    port = runtime.MacOSRuntimeAutomationPort(evidence_dir)
    compile_source: Path | None = None
    compile_output: Path | None = None
    module_caches: tuple[Path, Path] | None = None

    def compile_helper(
        argv: list[str],
        *,
        timeout: float,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal compile_source, compile_output, module_caches
        assert timeout == 120
        compile_source = Path(argv[2])
        compile_output = Path(argv[argv.index("-o") + 1])
        module_caches = (
            Path(env["CLANG_MODULE_CACHE_PATH"]),
            Path(env["SWIFT_MODULECACHE_PATH"]),
        )
        assert compile_source.read_text() == runtime._SWIFT_HELPER_SOURCE
        compile_output.write_bytes(b"compiled-helper")
        compile_output.chmod(0o755)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(port, "_run", compile_helper)

    collector = port.prepare_collector(evidence_dir)

    assert compile_source is not None
    assert compile_output is not None
    assert module_caches is not None
    assert evidence_dir not in compile_source.parents
    assert evidence_dir not in compile_output.parents
    assert all(evidence_dir not in cache.parents for cache in module_caches)
    assert compile_source.exists() is False
    assert compile_output.exists() is False
    assert all(cache.exists() is False for cache in module_caches)

    published = evidence_dir / collector.relative_path
    assert collector.relative_path == "collectors/macos-runtime-observer"
    assert collector.sha256 == hashlib.sha256(b"compiled-helper").hexdigest()
    assert published.read_bytes() == b"compiled-helper"
    assert sorted(
        path.relative_to(evidence_dir).as_posix()
        for path in evidence_dir.rglob("*")
    ) == ["collectors", "collectors/macos-runtime-observer"]


@pytest.mark.skipif(
    not Path("/usr/bin/xcrun").is_file(),
    reason="macOS Swift compiler is required",
)
def test_embedded_macos_runtime_observer_compiles_with_platform_sdk(
    tmp_path: Path,
) -> None:
    evidence_dir = tmp_path / "evidence"
    port = runtime.MacOSRuntimeAutomationPort(evidence_dir)

    collector = port.prepare_collector(evidence_dir)

    published = evidence_dir / collector.relative_path
    assert published.is_file()
    assert published.stat().st_mode & 0o111
    assert collector.relative_path == "collectors/macos-runtime-observer"
    assert collector.sha256 == hashlib.sha256(published.read_bytes()).hexdigest()
    assert sorted(
        path.relative_to(evidence_dir).as_posix()
        for path in evidence_dir.rglob("*")
    ) == ["collectors", "collectors/macos-runtime-observer"]


def test_macos_primary_launch_uses_explicit_launch_services_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _fixture_app(tmp_path)
    executable = app / "Contents/MacOS/harness-desktop"
    artifact = runtime._artifact_identity(app)
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    helper_invocations: list[tuple[str, ...]] = []
    environment = {
        "HOME": str(tmp_path / "runtime-home"),
        "TMPDIR": str(tmp_path / "runtime-tmp"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }

    def helper(command: str, *arguments: str) -> dict[str, object]:
        helper_invocations.append((command, *arguments))
        return {
            "accepted": True,
            "pid": 101,
            "bundle_identifier": runtime.BUNDLE_IDENTIFIER,
            "executable_path": str(executable),
        }

    monkeypatch.setattr(port, "_helper_json", helper)

    assert port.launch_primary(artifact, environment) == 101
    assert helper_invocations == [
        (
            "launch",
            runtime.BUNDLE_IDENTIFIER,
            str(app),
            environment["HOME"],
            environment["TMPDIR"],
        )
    ]


def test_installed_path_binding_requires_exact_non_symlink_application_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _fixture_app(tmp_path, name="HarnessKit.app")
    artifact = runtime._artifact_identity(app)
    monkeypatch.setattr(runtime, "INSTALLED_APP_PATH", app)

    assert runtime._installed_app_path_verified(artifact) is True

    alias = tmp_path / "HarnessKit-alias.app"
    alias.symlink_to(app, target_is_directory=True)
    monkeypatch.setattr(runtime, "INSTALLED_APP_PATH", alias)

    assert runtime._installed_app_path_verified(artifact) is False


def test_macos_repeat_launch_uses_system_open_with_isolated_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _fixture_app(tmp_path)
    artifact = runtime._artifact_identity(app)
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    environment = {
        "HOME": str(tmp_path / "runtime-home"),
        "TMPDIR": str(tmp_path / "runtime-tmp"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }
    invocations: list[tuple[list[str], dict[str, object]]] = []

    class Child:
        pid = 303

        def poll(self) -> int | None:
            return None

    def popen(argv: list[str], **kwargs: object) -> Child:
        invocations.append((argv, kwargs))
        return Child()

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)

    assert port.launch_repeat(artifact, environment) == 303
    assert invocations == [
        (
            [
                "/usr/bin/open",
                "-na",
                str(app),
                "--env",
                f"HOME={environment['HOME']}",
                "--env",
                f"CFFIXED_USER_HOME={environment['HOME']}",
                "--env",
                f"TMPDIR={environment['TMPDIR']}",
                "--env",
                f"PATH={environment['PATH']}",
            ],
            {
                "cwd": app.parent,
                "env": environment,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "start_new_session": True,
            },
        )
    ]


def test_hung_system_open_request_is_terminated_then_killed(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    events: list[object] = []

    class HungChild:
        pid = 303
        killed = False

        def poll(self) -> int | None:
            return -9 if self.killed else None

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")
            self.killed = True

        def wait(self, *, timeout: float) -> int:
            events.append(("wait", timeout))
            if not self.killed:
                raise subprocess.TimeoutExpired("/usr/bin/open", timeout)
            return -9

    child = HungChild()
    port.repeat_requests[child.pid] = child  # type: ignore[assignment]

    assert port.terminate_repeat_request(child.pid) is True
    assert events == ["terminate", ("wait", 5), "kill", ("wait", 5)]


def test_launch_services_helper_activates_without_becoming_a_prohibited_app() -> None:
    launch_source = runtime._SWIFT_HELPER_SOURCE.split(
        '// argv: launch <bundle-id> <app-path> <home> <tmpdir>', 1
    )[1].split("guard arguments.count >= 4", 1)[0]

    assert "NSApplication.shared" not in launch_source
    assert "configuration.createsNewApplicationInstance = true" in launch_source
    assert "configuration.activates = true" in launch_source
    assert '"HOME": home' in launch_source
    assert '"CFFIXED_USER_HOME": home' in launch_source
    assert '"TMPDIR": tmpdir' in launch_source
    assert '"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"' in launch_source


def test_macos_primary_launch_rejects_changed_executable_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _fixture_app(tmp_path)
    artifact = runtime._artifact_identity(app)
    executable = app / "Contents/MacOS/harness-desktop"
    executable.write_bytes(executable.read_bytes() + b"changed-after-artifact-read")
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    environment = {
        "HOME": str(tmp_path / "runtime-home"),
        "TMPDIR": str(tmp_path / "runtime-tmp"),
    }

    helper_invocations: list[tuple[str, ...]] = []

    def helper(command: str, *arguments: str) -> dict[str, object]:
        helper_invocations.append((command, *arguments))
        return {
            "accepted": True,
            "pid": 101,
            "bundle_identifier": runtime.BUNDLE_IDENTIFIER,
            "executable_path": str(executable),
        }

    monkeypatch.setattr(port, "_helper_json", helper)

    with pytest.raises(runtime.RuntimeQualificationError) as error:
        port.launch_primary(artifact, environment)

    assert error.value.code == "launch_services_primary_invalid"
    assert len(helper_invocations) == 1


def test_collector_mutations_require_the_expected_bundle_identity() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    identity_guard = "app.bundleIdentifier == bundleIdentifier"

    assert "func verifiedApplication" in source
    assert identity_guard in source
    assert "guard let app = verifiedApplication(pid, bundleIdentifier)" in source


def _successful_observations() -> list[runtime.RuntimeObservation]:
    return [
        _absent(),
        _observation(101, 11, focused=False, frontmost=False),
        _observation(101, 11),
        _observation(101, 11),
        _observation(101, 11),
        _observation(101, 11, minimized=True, focused=False, frontmost=False),
        _observation(101, 11),
        _observation(101, 11, hidden=True, focused=False, frontmost=False),
        _observation(101, 11),
        _absent(),
        _observation(202, 22, focused=False, frontmost=False),
        _absent(),
    ]


def test_success_records_five_cases_repeated_notifiers_and_hashed_evidence(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)
    qualification_report = _fixture_qualification(tmp_path, app)
    evidence_root = tmp_path / "evidence"
    port = FakePort(_successful_observations(), runtime_callback_count=5)

    report_path = runtime.verify_single_instance_macos(
        app,
        evidence_root,
        port=port,
        qualification_report=qualification_report,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "passed"
    assert report["artifact"]["build_id"] == BUILD_ID
    assert report["artifact"]["installed_path_verified"] is False
    assert report["package_qualification"] == {
        "app_source_manifest_sha256": "b" * 64,
        "build_inputs_sha256": "d" * 64,
        "bundle_manifest_sha256": json.loads(
            qualification_report.read_text()
        )["bundle_manifest_sha256"],
        "report_sha256": hashlib.sha256(qualification_report.read_bytes()).hexdigest(),
        "source_identity_sha256": "c" * 64,
    }
    assert [case["case_id"] for case in report["cases"]] == list(
        SINGLE_INSTANCE_CASE_IDS
    )
    for case in report["cases"]:
        assert {
            "case_id",
            "started_at",
            "ended_at",
            "expected",
            "observed",
            "status",
            "screenshot",
            "observation",
            "redactions",
            "limitations",
            "error",
        } == set(case)
        assert case["status"] == "Passed"
        assert case["error"] is None
        assert case["observed"]["main_process_count"] == 1
        assert case["observed"]["main_window_count"] == 1
        assert case["redactions"] == [
            "absolute_paths",
            "raw_logs",
            "secrets",
            "source_bodies",
        ]
    assert port.primary_launches == [app, app]
    assert port.repeat_launches == [app] * 5
    assert report["launch_strategy"] == {
        "primary": "nsworkspace_explicit_new_instance_activating",
        "notifier": "system_open_new_instance_activating",
    }
    assert port.waited_exit == [301, 302, 303, 304, 305, 101, 202]
    assert port.minimized == [101]
    assert port.hidden == [101]
    assert port.terminated == [101, 202]
    assert port.captures == [
        "first",
        "focus-restored",
        "minimized-restored",
        "hidden-restored",
        "post-quit-relaunch",
    ]
    assert port.waited_process_sets == [(110,), (211,)]
    assert [
        {
            "operation": notifier["operation"],
            "launch_request_accepted": notifier["launch_request_accepted"],
            "request_process_pid": notifier["request_process_pid"],
            "request_process_exited": notifier["request_process_exited"],
            "request_process_exit_code": notifier["request_process_exit_code"],
            "notifier_pid": notifier["notifier_pid"],
            "notifier_pid_observation": notifier["notifier_pid_observation"],
        }
        for notifier in report["notifiers"]
    ] == [
        {
            "operation": operation,
            "launch_request_accepted": True,
            "request_process_pid": pid,
            "request_process_exited": True,
            "request_process_exit_code": 0,
            "notifier_pid": None,
            "notifier_pid_observation": "not_exposed_by_system_open",
        }
        for operation, pid in [
            ("focus_restored_repeat_1", 301),
            ("focus_restored_repeat_2", 302),
            ("focus_restored_repeat_3", 303),
            ("minimized_restored", 304),
            ("hidden_restored", 305),
        ]
    ]
    assert report["lifecycle"] == {
        "post_quit": {
            "helper_pids": [110],
            "helpers_exited": True,
            "main_absent": True,
        },
        "final_cleanup": {
            "helper_pids": [211],
            "helpers_exited": True,
            "main_absent": True,
        },
        "helper_observation_scope": "main_process_descendants",
    }
    assert report["runtime_log_summary"] == {
        "available": True,
        "raw_log_stored": False,
        "single_instance_callback_count": 5,
        "window_presentation_active_key_count": 0,
        "window_presentation_count": 0,
        "window_presentation_inactive_or_nonkey_count": 0,
    }

    evidence_dir = evidence_root / "runtime-single-instance"
    for case in report["cases"]:
        screenshot = evidence_dir / case["screenshot"]["path"]
        observation = evidence_dir / case["observation"]["path"]
        assert hashlib.sha256(screenshot.read_bytes()).hexdigest() == case["screenshot"]["sha256"]
        assert hashlib.sha256(observation.read_bytes()).hexdigest() == case["observation"]["sha256"]
    samples = evidence_dir / report["samples"]["path"]
    sample_rows = [json.loads(line) for line in samples.read_text().splitlines()]
    assert len(sample_rows) == 11
    assert [(row["case_id"], row["phase"]) for row in sample_rows[-3:]] == [
        ("post-quit-relaunch", "post-quit-zero"),
        ("post-quit-relaunch", "steady"),
        ("post-quit-relaunch", "final-cleanup-zero"),
    ]
    assert not (evidence_dir / "runtime-home").exists()
    serialized = report_path.read_text()
    assert str(tmp_path) not in serialized
    assert "WebKit" not in serialized


def test_system_open_repeat_never_mislabels_the_request_pid_as_an_app_pid(
    tmp_path: Path,
) -> None:
    app = _fixture_app(tmp_path)
    port = FakePort(_successful_observations(), runtime_callback_count=5)

    report_path = runtime.verify_single_instance_macos(
        app,
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "passed"
    assert port.primary_launches == [app, app]
    assert port.repeat_launches == [app] * 5
    assert port.waited_exit == [301, 302, 303, 304, 305, 101, 202]
    assert all(
        notifier["request_process_pid"] in {301, 302, 303, 304, 305}
        and notifier["request_process_exited"] is True
        and notifier["request_process_exit_code"] == 0
        and notifier["notifier_pid"] is None
        and notifier["notifier_pid_observation"]
        == "not_exposed_by_system_open"
        for notifier in report["notifiers"]
    )


def test_available_runtime_log_requires_exactly_five_plugin_callbacks(
    tmp_path: Path,
) -> None:
    port = FakePort(_successful_observations(), runtime_callback_count=4)

    report_path = runtime.verify_single_instance_macos(
        _fixture_app(tmp_path),
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"] == {
        "code": "single_instance_callback_count_mismatch",
        "operation": "LaunchServices repeat callback audit",
    }
    assert report["runtime_log_summary"]["available"] is True
    assert report["runtime_log_summary"]["single_instance_callback_count"] == 4


def test_runtime_log_summary_reads_the_current_harnesskit_product_log(
    tmp_path: Path,
) -> None:
    log_path = (
        tmp_path
        / "Library/Logs/io.github.pureliture.harnesskit/HarnessKit.log"
    )
    log_path.parent.mkdir(parents=True)
    log_path.write_text("single-instance relaunch received\n" * 5)

    summary = runtime._runtime_log_summary(tmp_path)

    assert summary["available"] is True
    assert summary["single_instance_callback_count"] == 5


def test_missing_runtime_log_never_false_passes_callback_audit(
    tmp_path: Path,
) -> None:
    port = FakePort(_successful_observations(), runtime_callback_count=None)

    report_path = runtime.verify_single_instance_macos(
        _fixture_app(tmp_path),
        tmp_path / "evidence",
        port=port,
        timeout_seconds=1,
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"] == {
        "code": "single_instance_runtime_log_missing",
        "operation": "LaunchServices repeat callback audit",
    }
    assert report["runtime_log_summary"]["available"] is False


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
def test_permission_api_denial_is_an_exact_blocker(
    tmp_path: Path,
    preflight: runtime.PermissionPreflight,
    code: str,
    operation: str,
) -> None:
    port = FakePort([], preflight=preflight)

    report_path = runtime.verify_single_instance_macos(
        _fixture_app(tmp_path), tmp_path / "evidence", port=port
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "blocked"
    assert report["error"] == {"code": code, "operation": operation}
    assert [case["case_id"] for case in report["cases"]] == list(
        SINGLE_INSTANCE_CASE_IDS
    )
    assert {case["status"] for case in report["cases"]} == {"Unavailable"}
    assert all(case["error"] == report["error"] for case in report["cases"])
    assert port.primary_launches == []


def _ready_interactive_session_payload() -> dict[str, object]:
    return {
        "accessibility": True,
        "screen_capture": True,
        "session_dictionary_available": True,
        "session_on_console": True,
        "session_user_matches": True,
        "frontmost_application_available": True,
    }


def test_macos_preflight_accepts_macos_26_without_undocumented_lock_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda command: _ready_interactive_session_payload(),
    )
    monkeypatch.setattr(
        port,
        "_run",
        lambda argv: subprocess.CompletedProcess(argv, 0, b"1\n", b""),
    )

    assert port.preflight() == runtime.PermissionPreflight(True, True, True)


@pytest.mark.parametrize(
    "payload",
    (
        {"accessibility": True, "screen_capture": True},
        {**_ready_interactive_session_payload(), "session_dictionary_available": False},
        {**_ready_interactive_session_payload(), "session_on_console": False},
        {**_ready_interactive_session_payload(), "session_user_matches": False},
        {**_ready_interactive_session_payload(), "frontmost_application_available": False},
        {**_ready_interactive_session_payload(), "session_user_matches": 501},
    ),
)
def test_macos_preflight_blocks_when_session_lock_state_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    monkeypatch.setattr(port, "_helper_json", lambda *args, **kwargs: payload)
    monkeypatch.setattr(
        port,
        "_run",
        lambda *args, **kwargs: pytest.fail(
            "unknown session state must stop before System Events"
        ),
    )

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.preflight()

    assert captured.value.code == "interactive_session_state_unavailable"
    assert captured.value.operation == "macOS GUI session preflight"
    assert captured.value.blocked is True
    assert set(captured.value.evidence or {}) == {
        "session_dictionary_available",
        "session_on_console",
        "session_user_matches",
        "frontmost_application_available",
    }


def test_embedded_collector_uses_only_documented_gui_session_signals() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    assert "CGSessionCopyCurrentDictionary" in source
    assert "kCGSessionOnConsoleKey" in source
    assert "kCGSessionUserIDKey" in source
    assert "geteuid()" in source
    assert '"session_dictionary_available"' in source
    assert '"session_on_console"' in source
    assert '"session_user_matches"' in source
    assert '"frontmost_application_available"' in source
    assert "CGSSessionScreenIsLocked" not in source


def test_different_executable_in_bundle_namespace_is_a_blocker(tmp_path: Path) -> None:
    app = _fixture_app(tmp_path)
    other = _fixture_app(tmp_path / "other", name="other.app")
    other_executable = other / "Contents/MacOS/harness-desktop"
    application = runtime.ApplicationObservation(
        pid=77,
        executable_path=other_executable,
        executable_sha256=hashlib.sha256(other_executable.read_bytes()).hexdigest(),
        build_id=BUILD_ID,
    )
    port = FakePort([], applications=(application,))

    report_path = runtime.verify_single_instance_macos(
        app, tmp_path / "evidence", port=port
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "blocked"
    assert report["error"]["code"] == "single_instance_namespace_conflict"
    assert port.terminated == []
    assert port.primary_launches == []
    assert str(other) not in report_path.read_text()


def test_duplicate_main_process_is_failed_behavior_not_a_permission_blocker(
    tmp_path: Path,
) -> None:
    observations = [_absent(), _observation(101, 11, duplicate_main=True), _absent()]
    port = FakePort(observations)

    report_path = runtime.verify_single_instance_macos(
        _fixture_app(tmp_path), tmp_path / "evidence", port=port
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"]["code"] == "first_main_process_count_mismatch"
    assert report["error"]["operation"] == "first"
    assert [case["case_id"] for case in report["cases"]] == list(
        SINGLE_INSTANCE_CASE_IDS
    )
    assert [case["status"] for case in report["cases"]] == [
        "Failed",
        "Partial",
        "Partial",
        "Partial",
        "Partial",
    ]
    assert report["cases"][0]["error"] == report["error"]
    assert port.terminated == [101, 102]


def test_cleanup_failure_never_overwrites_the_original_behavior_failure(
    tmp_path: Path,
) -> None:
    class CleanupFailurePort(FakePort):
        def terminate(self, pid: int) -> bool:
            super().terminate(pid)
            raise runtime.RuntimeQualificationError(
                "collector_operation_failed", "terminate"
            )

    observations = [_absent(), _observation(101, 11, duplicate_main=True), _absent()]
    port = CleanupFailurePort(observations)

    report_path = runtime.verify_single_instance_macos(
        _fixture_app(tmp_path), tmp_path / "evidence", port=port
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"] == {
        "code": "first_main_process_count_mismatch",
        "operation": "first",
    }


def test_macos_hide_action_uses_the_preflighted_system_events_boundary(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    calls: list[list[str]] = []

    def run(
        argv: list[str],
        *,
        timeout: float = runtime.DEFAULT_TIMEOUT_SECONDS,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert timeout > 0
        assert env is None
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    port._run = run  # type: ignore[method-assign]

    assert port.hide_application(42) is True
    assert calls == [[
        "/usr/bin/osascript",
        "-e",
        'tell application "System Events" to set visible of first process whose unix id is 42 to false',
    ]]


def test_open_request_timeout_is_reported_and_the_request_pid_is_cleaned_up(
    tmp_path: Path,
) -> None:
    class StuckNotifierPort(FakePort):
        def wait_process_exit(self, pid: int, timeout_seconds: float) -> bool:
            super().wait_process_exit(pid, timeout_seconds)
            return pid != 301

    port = StuckNotifierPort(
        [
            _absent(),
            _observation(101, 11, focused=False, frontmost=False),
            _absent(),
        ]
    )

    report_path = runtime.verify_single_instance_macos(
        _fixture_app(tmp_path), tmp_path / "evidence", port=port
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"] == {
        "code": "focus_restored_repeat_1_request_did_not_exit",
        "operation": "focus_restored_repeat_1",
    }
    assert report["notifiers"][0]["launch_request_accepted"] is None
    assert port.terminated == [101]
    assert port.terminated_requests == [301]


def test_nonzero_open_request_exit_code_fails_before_identity_observation(
    tmp_path: Path,
) -> None:
    class RejectedOpenPort(FakePort):
        def process_exit_code(self, pid: int) -> int | None:
            return 1 if pid == 301 else super().process_exit_code(pid)

    port = RejectedOpenPort(
        [
            _absent(),
            _observation(101, 11, focused=False, frontmost=False),
            _absent(),
        ]
    )

    report_path = runtime.verify_single_instance_macos(
        _fixture_app(tmp_path), tmp_path / "evidence", port=port
    )

    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error"] == {
        "code": "focus_restored_repeat_1_request_exit_code_invalid",
        "operation": "focus_restored_repeat_1",
    }
    assert report["notifiers"][0]["launch_request_accepted"] is False
    assert report["notifiers"][0]["request_process_exit_code"] == 1
    assert port.terminated == [101]
    assert port.terminated_requests == []


def test_shared_macos_collector_declares_bounded_workspace_actions_and_first_visible_stream() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    for required in [
        'command == "workspace-snapshot"',
        'command == "workspace-press"',
        'command == "workspace-click"',
        'command == "workspace-picker-observe"',
        'command == "workspace-picker-closed"',
        'command == "workspace-picker-select"',
        'harness-checkout-directory-picker-v1',
        'kAXIdentifierAttribute',
        'CFEqual(candidate.window, panel.window)',
        'command == "workspace-value-target"',
        'command == "workspace-value-equals"',
        'command == "workspace-drag"',
        'command == "workspace-key"',
        'command == "workspace-hover"',
        'command == "workspace-wheel"',
        'command == "workspace-tree-key"',
        'command == "workspace-resize"',
        'command == "workspace-first-visible"',
        "AXValueGetValue",
        "CGEvent(mouseEventSource:",
        "SCStreamOutput",
        "onScreenWindowsOnly: false",
    ]:
        assert required in source
    assert "raw_ax_tree" not in source


def test_shared_macos_collector_waits_until_picker_is_no_longer_actionable() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    assert "func checkoutDirectoryPickerPanelWithIdentifierPresent(" in source
    closure_wait = source.split("func workspacePickerClosed(", 1)[1].split(
        "func elementDescendsFrom", 1
    )[0]
    presence_check = source.split(
        "func checkoutDirectoryPickerPanelWithIdentifierPresent(", 1
    )[1].split("func workspacePickerObserve", 1)[0]

    assert "checkoutDirectoryPickerIdentifiers.contains(panelIdentifier)" in closure_wait
    assert "checkoutDirectoryPickerPanelWithIdentifierPresent(pid, panelIdentifier)" in closure_wait
    assert '"native-directory-panel-closed"' in closure_wait
    assert "nativeDirectoryPanel(pid)" in presence_check
    assert "panel.identifier == identifier" in presence_check
    assert "kAXWindowsAttribute" not in presence_check
    assert "kAXTitleAttribute" not in closure_wait
    assert "kAXValueAttribute" not in closure_wait
    assert "kAXTitleAttribute" not in presence_check
    assert "kAXValueAttribute" not in presence_check


def test_shared_macos_collector_falls_back_to_exact_open_panel_button_identifiers() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    assert "func nativePickerButton(" in source
    fallback = source.split("func nativePickerButton(", 1)[1].split(
        "func nativeDirectoryPanel", 1
    )[0]
    panel_matcher = source.split("func nativeDirectoryPanel", 1)[1].split(
        "func checkoutDirectoryPickerPanelPresent", 1
    )[0]

    assert "boundedDescendants(window, maximum: 512)" in fallback
    assert "kAXIdentifierAttribute" in fallback
    assert "matches.count == 1" in fallback
    assert 'nativePickerButton(window, kAXDefaultButtonAttribute, "OKButton")' in panel_matcher
    assert 'nativePickerButton(window, kAXCancelButtonAttribute, "CancelButton")' in panel_matcher
    assert "kAXTitleAttribute" not in fallback
    assert "kAXValueAttribute" not in fallback


def test_shared_macos_collector_preserves_sot_tree_dom_order_for_last_row() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    tree_observer = source.split("func sotTreeObservation", 1)[1].split(
        "func workspaceSnapshot", 1
    )[0]

    assert "let treeElements = boundedDescendants(tree)" in tree_observer
    assert "guard let last = components.last" in tree_observer
    assert "components.sort" not in tree_observer


def test_shared_macos_collector_reads_only_typed_semantic_graph_ax_evidence() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    snapshot_observer = source.split("func workspaceSnapshot", 1)[1].split(
        "func pointerPoint", 1
    )[0]
    matrix_observer = source.split("func matrixLayoutObservation", 1)[1].split(
        "func collapsedWorkbenchLayoutObservation", 1
    )[0]

    for prohibited in [
        "func runtimeGraphEvidenceObservation",
        "runtimeGraphEvidenceObservation(elements)",
        'token.hasPrefix("evidence:")',
        "runtime-graph-evidence",
        "runtime_graph_evidence",
        "Data(base64Encoded:",
    ]:
        assert prohibited not in source

    assert "func semanticGraphObservation" in source
    assert snapshot_observer.count("semanticGraphObservation(elements)") == 1
    assert 'payload["semantic_graph"] = semanticGraph' in snapshot_observer
    assert "semanticGraphObservation(elements)" not in matrix_observer
    assert "_ semanticGraph: [String: Any]" in matrix_observer
    assert 'semanticGraph["components"] as? [[String: Any]]' in matrix_observer
    assert 'component["selected"] as? Bool == true' in matrix_observer


def test_shared_macos_collector_resolves_semantic_tokens_exactly_and_rejects_duplicates() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    resolver = source.split("func elementForToken", 1)[1].split(
        "func pressableElementForToken", 1
    )[0]

    for prefix in [
        "semantic-relation:",
        "semantic-component:",
        "semantic-workflow-step:",
    ]:
        assert prefix in resolver
    assert "semanticIdentityFromIdentifier(token) != nil" in resolver
    assert "matches.count == 1 ? matches[0] : nil" in resolver
    assert "elementIdentifier($0) == token" in resolver
    assert '"semantic-graph": "component-map-semantic-view"' in resolver
    assert '"renderer-retry": "component-map-renderer-retry"' in resolver
    identifier_reader = source.split("func elementIdentifier", 1)[1].split(
        "func elementName", 1
    )[0]
    assert "boundedText(value, limit: 1024)" in identifier_reader


def test_shared_macos_collector_parses_percent_encoded_workflow_step_identity() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    parser = source.split("func semanticIdentityFromIdentifier", 1)[1].split(
        "func semanticAXRecord", 1
    )[0]
    workflow_parser = parser.split('let workflowStepPrefix = "semantic-workflow-step:"', 1)[1]

    for required in [
        "maxSplits: 2",
        "omittingEmptySubsequences: false",
        "parts.count == 3",
        "canonicalSemanticToken(String(parts[0]), limit: 200)",
        "let stepID = decodeURIComponent(String(parts[2]), limit: 200)",
        "decodeURIComponent",
        '"step_id": stepID',
    ]:
        assert required in parser
    assert "removingPercentEncoding" in source
    assert '"component_id"' not in workflow_parser
    assert '"source_field"' not in workflow_parser
    assert 'charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"' in source


def test_shared_macos_collector_emits_bounded_typed_semantic_graph_observation() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    observer = source.split("func semanticIdentityFromIdentifier", 1)[1].split(
        "func elementForToken", 1
    )[0]

    for required in [
        '"relations"',
        '"components"',
        '"workflow_steps"',
        '"node_id"',
        '"profile_id"',
        '"workflow_id"',
        '"component_id"',
        '"ordinal"',
        '"role"',
        '"name"',
        '"selected"',
        '"focused"',
        '"visible"',
        '"frame"',
        '"identity_overlay"',
        'identityOverlay["kind"] = kindRecord',
        'identityOverlay["count"] = countRecord',
        '"renderer"',
        '"camera"',
        '"scale"',
        '"level"',
        '"zoom_out_enabled"',
        '"fit_enabled"',
        '"zoom_in_enabled"',
        '"component-map-camera-scale"',
        '"component-map-identity-title"',
        '"component-map-identity-kind"',
        '"component-map-identity-count"',
        '"component-map-renderer-status"',
        '"component-map-renderer-retry"',
        "boundedDescendants(semanticRoot, maximum: 4096)",
        "guard matches.count == 1 else { return nil }",
        "guard seenIdentifiers.insert(identifier).inserted else { return nil }",
    ]:
        assert required in observer
    assert "raw_ax_tree" not in observer
    assert "AXChildren" not in observer
    assert '"workflow_occurrences"' not in observer
    assert '"component-map-identity-id"' not in observer
    assert '"component-map-identity-meta"' not in observer


def test_frontend_fit_ax_label_round_trips_through_embedded_swift_parser(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    rendered = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            """
import { renderSemanticGraphFallback } from './src-frontend/graph/semantic-fallback.js';
const html = renderSemanticGraphFallback({
  projection: { nodes: [], links: [] },
  rendererState: {
    availability: 'ready',
    fitReport: {
      identityCount: 10,
      relationCount: 2,
      componentCount: 8,
      envelopeCount: 10,
      camera: {
        status: 'complete',
        inFrustumEnvelopeCount: 10,
        totalEnvelopeCount: 10,
        largestDimensionOccupancy: 0.82,
      },
      sceneBounds: {
        min: { x: -100, y: -20, z: -4 },
        max: { x: 100, y: 20, z: 5.2 },
      },
    },
  },
});
const match = html.match(/id="component-map-fit-report" aria-label="([^"]+)"/);
if (!match) process.exit(2);
process.stdout.write(match[1]);
""",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    label = rendered.stdout
    assert len(label) > 240

    source = runtime._SWIFT_HELPER_SOURCE
    bounded_start = source.index("func boundedText")
    bounded_end = source.index("func elementIdentifier", bounded_start)
    parser_start = source.index("let semanticFitReportTextLimit")
    parser_end = source.index("func semanticFitReportObservation", parser_start)
    parser_source = source[parser_start:parser_end]
    observation_source = source[parser_end:].split(
        "func semanticGraphObservation", 1
    )[0]
    assert (
        "elementName(element, limit: semanticFitReportTextLimit)"
        in observation_source
    )

    runner = tmp_path / "fit-parser.swift"
    runner.write_text(
        "import Foundation\n"
        + source[bounded_start:bounded_end]
        + parser_source
        + """
guard CommandLine.arguments.count == 2 else { exit(2) }
let bounded = boundedText(
    CommandLine.arguments[1],
    limit: semanticFitReportTextLimit
)
guard let report = semanticFitReportFromText(bounded),
      JSONSerialization.isValidJSONObject(report),
      let data = try? JSONSerialization.data(withJSONObject: report) else {
    exit(3)
}
FileHandle.standardOutput.write(data)
"""
    )
    binary = tmp_path / "fit-parser"
    module_cache = tmp_path / "swift-module-cache"
    module_cache.mkdir()
    subprocess.run(
        ["xcrun", "swiftc", str(runner), "-o", str(binary)],
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "CLANG_MODULE_CACHE_PATH": str(module_cache),
            "SWIFT_MODULECACHE_PATH": str(module_cache),
        },
    )
    parsed = subprocess.run(
        [str(binary), label], check=True, capture_output=True
    )

    assert json.loads(parsed.stdout) == {
        "identity_count": 10,
        "relation_count": 2,
        "component_count": 8,
        "envelope_count": 10,
        "camera_status": "complete",
        "in_frustum_envelope_count": 10,
        "total_envelope_count": 10,
        "largest_dimension_occupancy": 0.82,
        "scene_bounds": {
            "min": {"x": -100.0, "y": -20.0, "z": -4.0},
            "max": {"x": 100.0, "y": 20.0, "z": 5.2},
        },
    }


def test_shared_macos_collector_observes_camera_scale_and_controls_fail_closed() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    observer = source.split("func cameraObservation", 1)[1].split(
        "func semanticGraphObservation", 1
    )[0]

    for required in [
        '"component-map-camera-scale"',
        '"3D · 준비"',
        '"3D · 준비됨"',
        '"3D · 목록"',
        '"^([0-9]+(?:\\\\.[0-9]+)?)× · (개요|상세|최대)$"',
        '"scale"',
        '"level"',
        '"zoom_out_enabled"',
        '"fit_enabled"',
        '"zoom_in_enabled"',
        '"ready"',
        '"AXButton"',
        '"축소"',
        '"전체 보기"',
        '"확대"',
        "guard matches.count == 1 else { return nil }",
        "scale >= 0.75 && scale <= 8.0",
        "kAXEnabledAttribute",
    ]:
        assert required in observer


def test_shared_macos_collector_joins_structured_relation_groups_fail_closed() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    observer = source.split("func relationAuthorityRecord", 1)[1].split(
        "func workflowInspectorTokenIdentity", 1
    )[0]

    for required in [
        "semantic-relation-group:",
        "semantic-relation-members:",
        "semantic-relation-member:",
        '"exact_count"',
        '"member_component_ids"',
        "guard groupMatches.count == 1",
        "membersMatches.count == 1",
        "guard relationRecords.count <= 24 else { return nil }",
        "memberIDs.insert(componentID).inserted",
        'relationKind != "workflow" && exactCount != memberIDs.count',
    ]:
        assert required in observer
    assert "raw_ax_tree" not in observer


def test_shared_macos_collector_observes_workflow_inspector_with_exact_stable_ids() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    parser = source.split("func semanticAXRecord", 1)[1].split(
        "func elementForToken", 1
    )[0]
    resolver = source.split("func elementForToken", 1)[1].split(
        "func pressableElementForToken", 1
    )[0]

    for required in [
        "workflow-inspector:",
        "workflow-inspector-step:",
        "workflow-overview:",
        "semantic-workflow-role:",
        "workflow-occurrence:",
        "workflow-warning:",
        '"workflow_id"',
        '"runtime_state"',
        '"steps"',
        '"ordinal_occurrences"',
        '"component_roles"',
        '"unresolved_warning_set"',
        '"ordinal"',
        '"selected"',
        '"focused"',
        '"visible"',
        '"frame"',
        '"dom_identifier"',
        "guard inspectorMatches.count == 1 else { return nil }",
        "guard seenStepIdentifiers.insert(identifier).inserted else { return nil }",
        'guard inspectorRecord["visible"] as? Bool == true else { return emptyObservation }',
        "workflowRoleIdentityFromIdentifier(identifier)",
        "workflowOccurrenceIdentityFromIdentifier(identifier)",
        "workflowWarningIdentityFromIdentifier(identifier)",
        "let authoredOrdinals = Set(steps.compactMap",
        "authoredOrdinals.contains(ordinal)",
        "let occurrenceTuples = Set(occurrenceIdentities.compactMap",
        "occurrenceTuples.contains(roleTuple)",
    ]:
        assert required in parser
    assert 'token.hasPrefix("workflow-inspector-step:")' in resolver
    assert 'token.hasPrefix("workflow-overview:")' in resolver
    assert "workflowInspectorTokenIdentity(token) != nil" in resolver
    assert "raw_yaml" not in parser
    assert "source_body" not in parser
    assert '"workflow_id": NSNull()' in parser
    assert '"runtime_state": NSNull()' in parser
    assert 'result["workflow_inspector"] = workflowInspector' in parser
    for prohibited in [
        '"occurrence_count"',
        '"role_count"',
        '"warning_count"',
        'identityRail["id"]',
    ]:
        assert prohibited not in parser


def test_workspace_press_resolver_requires_the_ax_press_action() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    resolver = source.split("func pressableElementForToken", 1)[1].split(
        "func safeNumericValue", 1
    )[0]
    press_action = source.split("func pressWorkspaceTarget", 1)[1].split(
        "func workspaceValueTarget", 1
    )[0]

    assert "AXUIElementCopyActionNames" in resolver
    assert "kAXPressAction" in resolver
    assert "elementForToken(token, pressableElements)" in resolver
    assert "pressableElementForToken(token, elements)" in press_action
    assert "elementForToken(token, elements)" not in press_action


def test_workspace_text_observer_only_targets_one_visible_text_control_and_reads_exact_value() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    target = source.split("func workspaceValueTarget", 1)[1].split(
        "func workspaceValueEquals", 1
    )[0]
    equality = source.split("func workspaceValueEquals", 1)[1].split(
        "final class WorkspaceDragSession", 1
    )[0]

    for helper in (target, equality):
        for required in [
            "workspaceMainWindow(pid)",
            "matches.count == 1",
            'elementMatchesIdentifier($0, identifier)',
            '["AXTextField", "AXTextArea"]',
            '!boolAttribute(target, "AXHidden")',
            "targetFrame.width > 0",
            "targetFrame.height > 0",
            "frameContains(workspaceWindow.cgFrame, targetFrame)",
        ]:
            assert required in helper
    for required in [
        '"window_id": workspaceWindow.windowID',
        '"identifier": identifier',
        '"pointer_point"',
        '"target_readback": readback',
    ]:
        assert required in target
    for required in [
        "workspaceMainWindow(pid)",
        "stringAttribute(target, kAXValueAttribute) == expectedValue",
    ]:
        assert required in equality
    assert "/usr/bin/osascript" not in source
    assert 'tell application "System Events"' not in source


def test_workspace_text_port_runs_system_events_at_the_verified_target_then_checks_exact_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    helper_calls: list[tuple[str, ...]] = []
    process_calls: list[list[str]] = []

    def helper(*arguments: str) -> dict[str, object]:
        helper_calls.append(arguments)
        if arguments[0] == "workspace-value-target":
            return {
                "accepted": True,
                "pid": 101,
                "window_id": 41,
                "identifier": "checkout-path",
                "pointer_point": {"x": 640, "y": 88},
                "target_readback": {
                    "target": "id:checkout-path",
                    "dom_identifier": "checkout-path",
                    "role": "AXTextField",
                    "visible": True,
                    "enabled": True,
                    "frame": {"x": 480, "y": 64, "width": 320, "height": 48},
                },
            }
        if arguments[0] == "workspace-value-equals":
            return {"accepted": True}
        raise AssertionError(arguments)

    def run(
        argv: list[str],
        *,
        timeout: float = runtime.DEFAULT_TIMEOUT_SECONDS,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert env is None
        assert timeout <= 3
        process_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"edited\n", b"")

    monkeypatch.setattr(port, "_helper_json", helper)
    monkeypatch.setattr(port, "_run", run)

    assert port.set_workspace_text(101, "checkout-path", "") is True
    assert helper_calls == [
        (
            "workspace-value-target",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "checkout-path",
        ),
        (
            "workspace-value-equals",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "checkout-path",
            "",
        ),
    ]
    assert len(process_calls) == 1
    assert process_calls[0][0] == "/usr/bin/osascript"
    assert process_calls[0][-4:] == ["101", "", "640", "88"]
    assert "click at {targetX, targetY}" in process_calls[0][2]
    assert 'value of attribute "AXFocusedUIElement" of targetProcess' in process_calls[0][2]
    assert "set value of focusedElement to requestedValue" in process_calls[0][2]
    assert "if value of focusedElement is not requestedValue" in process_calls[0][2]


def test_workspace_text_port_rejects_a_pointer_outside_the_verified_ax_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {
            "accepted": True,
            "pid": 101,
            "window_id": 41,
            "identifier": "checkout-path",
            "pointer_point": {"x": 900, "y": 88},
            "target_readback": {
                "target": "id:checkout-path",
                "dom_identifier": "checkout-path",
                "role": "AXTextField",
                "visible": True,
                "enabled": True,
                "frame": {"x": 480, "y": 64, "width": 320, "height": 48},
            },
        },
    )

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.set_workspace_text(101, "checkout-path", "")

    assert captured.value.code == "workspace_value_target_readback_invalid"
    assert captured.value.operation == "workspace-value-target"
    assert captured.value.blocked is False


def test_workspace_text_port_reports_only_system_events_authorization_as_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {
            "accepted": True,
            "pid": 101,
            "window_id": 41,
            "identifier": "checkout-path",
            "pointer_point": {"x": 640, "y": 88},
            "target_readback": {
                "target": "id:checkout-path",
                "dom_identifier": "checkout-path",
                "role": "AXTextField",
                "visible": True,
                "enabled": True,
                "frame": {"x": 480, "y": 64, "width": 320, "height": 48},
            },
        },
    )
    monkeypatch.setattr(
        port,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            b"",
            b"Not authorized to send Apple events to System Events. (-1743)",
        ),
    )

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.set_workspace_text(101, "checkout-path", "")

    assert captured.value.code == "system_events_automation_denied"
    assert captured.value.operation == "System Events workspace text edit"
    assert captured.value.blocked is True


def test_shared_macos_collector_exposes_divider_readback_and_physical_geometry() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    for required in [
        'record["orientation"] = "vertical"',
        'record["current_value"]',
        'record["minimum_value"]',
        'record["maximum_value"]',
        'record["focused"]',
        "kAXValueDescriptionAttribute",
        'record["active_state"]',
        'return "dragging"',
        " · 드래그 중",
        '"focused_readback"',
        '"value_readback"',
        '"physical_geometry"',
        '"ax_window"',
        '"cg_window"',
        '"workspace_shell"',
        '"window_frame_match"',
        '"tiling"',
        '"containment"',
        '"responsive"',
    ]:
        assert required in source


def test_shared_macos_collector_distinguishes_missing_graph_expanded_attribute() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    assert 'record["expanded_present"]' in source
    assert 'if token == "graph-toggle" && expandedValue == nil' in source


def test_shared_macos_collector_matches_every_frontend_graph_scale_mode() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    for mode in ("개요", "선택", "상세", "선택 불가"):
        assert f'$0.contains("{mode}")' in source
    assert '$0.contains("확대")' not in source


def test_shared_macos_collector_emits_authoritative_preferred_panel_pair() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    for required in [
        '"preferred-widths": "workspace-preferred-widths"',
        'func preferredPairFromText(_ value: String) -> [String: Int]?',
        '"preferred-widths"',
        'payload["preferred_pair"] = preferredPair',
    ]:
        assert required in source


def test_workspace_port_routes_typed_actions_and_uses_exact_text_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    calls: list[tuple[str, ...]] = []
    process_calls: list[list[str]] = []

    def helper(command: str, *arguments: str) -> dict[str, object]:
        calls.append((command, *arguments))
        if command == "workspace-snapshot":
            return {
                "accepted": True,
                "pid": 101,
                "window_id": 42,
                "targets": [],
            }
        if command == "workspace-value-target":
            return {
                "accepted": True,
                "pid": 101,
                "window_id": 42,
                "identifier": "checkout-path",
                "pointer_point": {"x": 400, "y": 100},
                "target_readback": {
                    "target": "id:checkout-path",
                    "dom_identifier": "checkout-path",
                    "role": "AXTextField",
                    "visible": True,
                    "enabled": True,
                    "frame": {"x": 300, "y": 80, "width": 300, "height": 40},
                },
            }
        if command == "workspace-click":
            target = arguments[-1]
            return {
                "accepted": True,
                "kind": "pointer-click",
                "transport": "cg-event-mouse-click",
                "pid": 101,
                "window_id": 42,
                "target": target,
                "preselected": False,
                "selection_transition_confirmed": True,
                "target_readback": {
                    "target": target,
                    "component_id": target.removeprefix("component:"),
                    "role": "AXGroup",
                    "name": "Runtime Layout Middle Anchor",
                    "visible": True,
                    "frame": {"x": 300, "y": 200, "width": 40, "height": 30},
                },
                "pointer_point": {"x": 320, "y": 215},
            }
        return {"accepted": True}

    def run(
        argv: list[str],
        *,
        timeout: float = runtime.DEFAULT_TIMEOUT_SECONDS,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert env is None
        assert timeout <= 3
        process_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"edited\n", b"")

    monkeypatch.setattr(port, "_helper_json", helper)
    monkeypatch.setattr(port, "_run", run)

    snapshot = port.observe_workspace(101, ("anchor.left", "anchor.mid", "anchor.right"))
    assert snapshot["pid"] == 101
    assert port.press_workspace_target(101, "label:HarnessKit 연결") is True
    click = port.click_workspace_target(
        101, "component:harnesskit.skill.runtime-layout-anchor-middle"
    )
    assert click["kind"] == "pointer-click"
    assert port.set_workspace_text(101, "checkout-path", "/private/runtime/fixture") is True
    assert port.drag_workspace_target(101, "left-divider", 48) is True
    assert port.key_workspace_target(101, "right-divider", "ArrowLeft") is True
    assert port.resize_workspace_window(101, 1000, 720) is True

    assert calls == [
        (
            "workspace-snapshot",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "anchor.left,anchor.mid,anchor.right",
        ),
        ("workspace-press", runtime.BUNDLE_IDENTIFIER, "101", "label:HarnessKit 연결"),
        (
            "workspace-click",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "component:harnesskit.skill.runtime-layout-anchor-middle",
        ),
        (
            "workspace-value-target",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "checkout-path",
        ),
        (
            "workspace-value-equals",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "checkout-path",
            "/private/runtime/fixture",
        ),
        ("workspace-drag", runtime.BUNDLE_IDENTIFIER, "101", "left-divider", "48"),
        ("workspace-key", runtime.BUNDLE_IDENTIFIER, "101", "right-divider", "ArrowLeft"),
        ("workspace-resize", runtime.BUNDLE_IDENTIFIER, "101", "1000", "720"),
    ]
    assert len(process_calls) == 1
    assert process_calls[0][-4:] == [
        "101",
        "/private/runtime/fixture",
        "400",
        "100",
    ]


def test_workspace_picker_selection_uses_native_panel_and_redacted_pointer_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    calls: list[tuple[str, ...]] = []
    expected = {
        "accepted": True,
        "kind": "native-directory-selection",
        "transport": "cg-event-chord-ax-value-cg-event-mouse-click",
        "pid": 101,
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

    def helper(*arguments: str) -> dict[str, object]:
        calls.append(arguments)
        return expected

    monkeypatch.setattr(port, "_helper_json", helper)

    observed = port.select_native_checkout_directory(101, checkout, 5.0)

    assert calls == [
        (
            "workspace-picker-select",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            str(checkout.resolve()),
            "5.0",
        )
    ]
    assert observed == expected
    assert str(checkout) not in json.dumps(observed)


def test_workspace_picker_observation_proves_one_app_owned_panel_without_titles_or_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    expected = {
        "accepted": True,
        "kind": "native-directory-panel-open",
        "pid": 101,
        "panel_count": 1,
        "panel_identifier": runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
        "path_redacted": True,
        "title_redacted": True,
        "panel": {
            "role": "AXWindow",
            "subrole": "AXDialog",
            "visible": True,
            "identifier": runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
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
    calls: list[tuple[str, ...]] = []

    def helper(*arguments: str) -> dict[str, object]:
        calls.append(arguments)
        return expected

    monkeypatch.setattr(port, "_helper_json", helper)

    observed = port.observe_native_checkout_picker(101, 5.0)

    assert calls == [
        (
            "workspace-picker-observe",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "5.0",
        )
    ]
    assert observed == expected
    serialized = json.dumps(observed).replace("title_redacted", "")
    assert "title" not in serialized
    assert "/" not in serialized


def test_workspace_picker_observation_accepts_appkit_open_panel_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    expected = {
        "accepted": True,
        "kind": "native-directory-panel-open",
        "pid": 101,
        "panel_count": 1,
        "panel_identifier": "open-panel",
        "path_redacted": True,
        "title_redacted": True,
        "panel": {
            "role": "AXWindow",
            "subrole": "AXStandardWindow",
            "visible": True,
            "identifier": "open-panel",
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

    monkeypatch.setattr(port, "_helper_json", lambda *_arguments: expected)

    observed = port.observe_native_checkout_picker(101, 5.0)

    assert observed == expected


def test_workspace_picker_closed_wait_proves_the_observed_app_owned_panel_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    expected = {
        "accepted": True,
        "kind": "native-directory-panel-closed",
        "pid": 101,
        "panel_identifier": runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
        "panel_closed": True,
        "path_redacted": True,
    }
    calls: list[tuple[str, ...]] = []

    def helper(*arguments: str) -> dict[str, object]:
        calls.append(arguments)
        return expected

    monkeypatch.setattr(port, "_helper_json", helper)

    observed = port.wait_native_checkout_picker_closed(
        101,
        runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
        5.0,
    )

    assert calls == [
        (
            "workspace-picker-closed",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
            "5.0",
        )
    ]
    assert observed == expected
    serialized = json.dumps(observed)
    assert "/" not in serialized
    assert "title" not in serialized


@pytest.mark.parametrize(
    ("pid", "panel_identifier", "timeout_seconds"),
    [
        (0, runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER, 5.0),
        (101, "unknown-panel", 5.0),
        (101, None, 5.0),
        (101, runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER, 0.5),
        (101, runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER, 21.0),
    ],
)
def test_workspace_picker_closed_wait_rejects_unbounded_or_unobserved_arguments(
    tmp_path: Path,
    pid: int,
    panel_identifier: str | None,
    timeout_seconds: float,
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.wait_native_checkout_picker_closed(
            pid,
            panel_identifier,
            timeout_seconds,
        )

    assert captured.value.code == "workspace_action_arguments_invalid"
    assert captured.value.operation == "workspace-picker-closed"


def test_workspace_picker_closed_wait_rejects_unredacted_or_incomplete_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    invalid = {
        "accepted": True,
        "kind": "native-directory-panel-closed",
        "pid": 101,
        "panel_identifier": runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
        "panel_closed": False,
        "path_redacted": True,
        "path": "redaction-bypass",
    }
    monkeypatch.setattr(port, "_helper_json", lambda *_arguments: invalid)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.wait_native_checkout_picker_closed(
            101,
            runtime.CHECKOUT_DIRECTORY_PICKER_AX_IDENTIFIER,
            5.0,
        )

    assert captured.value.code == "workspace_picker_closed_proof_invalid"
    assert captured.value.operation == "workspace-picker-closed"


def test_workspace_picker_selection_rejects_relative_or_missing_directory(
    tmp_path: Path,
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)

    for candidate in (Path("relative"), tmp_path / "missing"):
        with pytest.raises(runtime.RuntimeQualificationError) as captured:
            port.select_native_checkout_directory(101, candidate, 5.0)

        assert captured.value.code == "workspace_action_arguments_invalid"
        assert captured.value.operation == "workspace-picker-select"


def test_workspace_text_retries_until_cold_webview_ax_tree_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    outcomes = deque([False, False, True])
    calls: list[tuple[str, ...]] = []
    sleeps: list[float] = []

    def helper(command: str, *arguments: str) -> dict[str, object]:
        calls.append((command, *arguments))
        if command == "workspace-value-target":
            if not outcomes.popleft():
                return {"accepted": False}
            return {
                "accepted": True,
                "pid": 101,
                "window_id": 42,
                "identifier": "checkout-path",
                "pointer_point": {"x": 400, "y": 100},
                "target_readback": {
                    "target": "id:checkout-path",
                    "dom_identifier": "checkout-path",
                    "role": "AXTextField",
                    "visible": True,
                    "enabled": True,
                    "frame": {"x": 300, "y": 80, "width": 300, "height": 40},
                },
            }
        assert command == "workspace-value-equals"
        return {"accepted": True}

    monkeypatch.setattr(port, "_helper_json", helper)
    monkeypatch.setattr(
        port,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, b"edited\n", b""
        ),
    )
    monkeypatch.setattr(runtime.time, "sleep", sleeps.append)

    accepted = port.set_workspace_text(101, "checkout-path", "")

    assert accepted is True
    assert calls == [
        (
            "workspace-value-target",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "checkout-path",
        ),
        (
            "workspace-value-target",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "checkout-path",
        ),
        (
            "workspace-value-target",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "checkout-path",
        ),
        (
            "workspace-value-equals",
            runtime.BUNDLE_IDENTIFIER,
            "101",
            "checkout-path",
            "",
        ),
    ]
    assert sleeps == [0.1, 0.1]


def test_workspace_text_readiness_retry_stops_at_its_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    ticks = iter([10.0, 10.0, 10.1, 10.2])
    calls: list[tuple[str, ...]] = []
    sleeps: list[float] = []

    def helper(command: str, *arguments: str) -> dict[str, object]:
        calls.append((command, *arguments))
        return {"accepted": False}

    monkeypatch.setattr(port, "_helper_json", helper)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(runtime.time, "sleep", sleeps.append)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.set_workspace_text(
            101,
            "checkout-path",
            "",
            timeout_seconds=0.2,
        )

    assert captured.value.code == "workspace_ax_tree_readiness_timeout"
    assert captured.value.operation == "checkout-path"
    assert captured.value.blocked is False
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert all(0 < duration <= 0.1 for duration in sleeps)


@pytest.mark.parametrize(
    "timeout_seconds",
    (0.0, -1.0, float("nan"), float("inf"), runtime.DEFAULT_TIMEOUT_SECONDS + 0.1),
)
def test_workspace_text_readiness_rejects_an_unbounded_timeout(
    tmp_path: Path,
    timeout_seconds: float,
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)

    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.set_workspace_text(
            101,
            "checkout-path",
            "",
            timeout_seconds=timeout_seconds,
        )

    assert captured.value.code == "workspace_action_arguments_invalid"
    assert captured.value.operation == "workspace-set-value"


def test_workspace_keyboard_evidence_requires_focused_value_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    before = {
        "target": "left-divider",
        "role": "AXSplitter",
        "orientation": "vertical",
        "current_value": 304,
        "minimum_value": 200,
        "maximum_value": 328,
        "value_description": "304픽셀",
        "active_state": "normal",
        "focused": False,
    }
    after = {
        **before,
        "current_value": 320,
        "value_description": "320픽셀",
        "focused": True,
    }
    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {
            "accepted": True,
            "kind": "keyboard",
            "target": "left-divider",
            "key": "ArrowRight",
            "before": before,
            "after": after,
            "focused_readback": True,
            "value_readback": 320,
        },
    )

    evidence = port.key_workspace_target_evidence(
        101, "left-divider", "ArrowRight"
    )

    assert evidence["after"] == after
    assert evidence["focused_readback"] is True

    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {
            "accepted": True,
            "kind": "keyboard",
            "target": "left-divider",
            "key": "ArrowRight",
            "before": before,
            "after": {**after, "focused": False},
            "focused_readback": True,
            "value_readback": 320,
        },
    )
    with pytest.raises(runtime.RuntimeQualificationError) as error:
        port.key_workspace_target_evidence(101, "left-divider", "ArrowRight")
    assert error.value.code == "workspace_keyboard_readback_invalid"


def test_shared_macos_collector_supports_collapsed_workspace_geometry_without_hidden_ax_targets() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    snapshot = source.split("func workspaceSnapshot", 1)[1].split(
        "func pressWorkspaceTarget", 1
    )[0]

    for required in [
        '"collapsed_sides"',
        '"pane_present"',
        '"divider_present"',
        '"center_continues"',
        'left == nil && leftDivider == nil',
        'right == nil && rightDivider == nil',
        'payload["physical_geometry"]',
    ]:
        assert required in snapshot
    assert "guard let left = frameFor(\"left-pane\")" not in snapshot
    assert "guard let right = frameFor(\"right-pane\")" not in snapshot


def test_shared_macos_collector_declares_exact_amendment_selector_operations() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    swift_selector_block = source.split(
        "let workspaceAmendmentSelectors: Set<String> = [", 1
    )[1].split("\n]", 1)[0]
    identity_lookup = source.split('if token == "app-identity"', 1)[1].split(
        'if token == "appearance-mode"', 1
    )[0]

    assert runtime.WORKSPACE_AMENDMENT_SELECTORS == frozenset(
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
    for selector in runtime.WORKSPACE_AMENDMENT_SELECTORS:
        assert f'"{selector}"' in swift_selector_block
    assert '"AXHeading"' in identity_lookup
    for required in [
        'command == "workspace-selector-observe"',
        'command == "workspace-selector-press"',
        'command == "workspace-typography-select"',
        'command == "workspace-local-scroll-observe"',
        'command == "workspace-local-select"',
        "func workspaceElementForSelector",
        "func workspaceSelectorObservation",
        "func pressWorkspaceSelector",
        "func selectWorkspaceTypographyPreset",
        "func localScrollObservation",
        "func selectWorkspaceLocalResult",
        '"#register-checkout": "register-checkout"',
        '"#load-sot": "load-sot"',
        'selector == "#register-checkout"',
        '["HarnessKit 연결", "연결 중…"]',
        'selector == "#load-sot"',
        '["SoT 새로고침", "새로고침 중..."]',
        'command == "workspace-project-scope-select"',
        'command == "workspace-local-publication-observe"',
        'command == "workspace-toolbar-observe"',
        "func selectWorkspaceProjectScope",
        "func localPublicationObservation",
    ]:
        assert required in source
    assert "WKWebView" not in source
    assert "evaluateJavaScript" not in source
    assert "Web Inspector" not in source


def test_shared_macos_collector_redacts_ignore_text_and_local_paths_from_selector_records() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    selector_record = source.split("func selectorTargetRecord", 1)[1].split(
        "func workspaceSelectorObservation", 1
    )[0]
    local_scroll = source.split("func localScrollObservation", 1)[1].split(
        "func selectWorkspaceLocalResult", 1
    )[0]

    for required in [
        'selector == "#project-ignore-text"',
        'record["value_redacted"] = true',
        'record["value_length"]',
        'selector == "[data-local-instance][aria-pressed=true]"',
        'record.removeValue(forKey: "name")',
        'record.removeValue(forKey: "value_text")',
    ]:
        assert required in selector_record
    for prohibited in [
        '"source_body"',
        '"raw_body"',
        '"canonical_path"',
        '"safe_locator"',
        '"instance_id"',
    ]:
        assert prohibited not in selector_record
        assert prohibited not in local_scroll


def test_shared_macos_collector_local_scroll_evidence_uses_ax_frames_scrollbar_and_cgevent() -> None:
    source = runtime._SWIFT_HELPER_SOURCE
    observer = source.split("func localScrollObservation", 1)[1].split(
        "func selectWorkspaceLocalResult", 1
    )[0]
    selector = source.split("func selectWorkspaceLocalResult", 1)[1].split(
        "func pressWorkspaceTarget", 1
    )[0]

    for required in [
        '"scroll_area_frame"',
        '"visible_bounds"',
        '"vertical_scrollbar"',
        '"selected_row"',
        '"selected_row_index"',
        '"visible_result_count"',
        'kAXVerticalScrollBarAttribute',
        'kAXValueAttribute',
    ]:
        assert required in observer
    for required in [
        '"cg-event-mouse-click"',
        '"cg-event-keyboard-arrow-down"',
        "clickLocalResult(frame)",
        "pressLocalArrowDown()",
        '"selection_changed"',
        '"before"',
        '"after"',
    ]:
        assert required in selector
    assert "CGEvent(mouseEventSource:" in selector
    assert "CGEvent(keyboardEventSource:" in selector


def _selector_record(
    selector: str,
    *,
    role: str = "AXButton",
    expanded: bool | None = None,
    value_text: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "selector": selector,
        "role": role,
        "frame": {"x": 10, "y": 20, "width": 120, "height": 32},
        "visible": True,
        "focusable": True,
        "focused": False,
        "enabled": True,
        "selected": False,
        "expanded_present": expanded is not None,
    }
    if expanded is not None:
        record["expanded"] = expanded
    if value_text is not None:
        record["value_text"] = value_text
    return record


def test_workspace_selector_port_validates_exact_whitelist_and_stable_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    selector = "#left-pane-disclosure"
    target = _selector_record(selector, expanded=True)
    calls: list[tuple[tuple[str, ...], float | None]] = []

    def helper(
        *arguments: str, timeout_seconds: float | None = None
    ) -> dict[str, object]:
        calls.append((arguments, timeout_seconds))
        return {
            "accepted": True,
            "pid": 101,
            "window_id": 41,
            "selector": selector,
            "present": True,
            "target_count": 1,
            "target": target,
        }

    monkeypatch.setattr(port, "_helper_json", helper)

    assert port.observe_workspace_selector(
        101, selector, timeout_seconds=7.5
    )["target"] == target
    assert calls == [
        (
            (
                "workspace-selector-observe",
                runtime.BUNDLE_IDENTIFIER,
                "101",
                selector,
            ),
            7.5,
        )
    ]
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.observe_workspace_selector(101, "[data-unknown]")
    assert captured.value.code == "workspace_selector_arguments_invalid"


def test_workspace_typography_selector_port_requires_canonical_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    before = _selector_record(
        "#typography-menu-trigger", role="AXButton", value_text="Default"
    )
    after = _selector_record(
        "#typography-menu-trigger", role="AXButton", value_text="Large"
    )
    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {
            "accepted": True,
            "kind": "typography-preset",
            "transport": "ax-press-menuitemradio",
            "pid": 101,
            "requested_preset": "Large",
            "before": before,
            "after": after,
            "value_readback": "Large",
        },
    )

    evidence = port.select_workspace_typography_preset(101, "Large")
    assert evidence["after"] == after

    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {
            "accepted": True,
            "kind": "typography-preset",
            "transport": "ax-press-menuitemradio",
            "pid": 101,
            "requested_preset": "Large",
            "before": before,
            "after": {**after, "value_text": "Default"},
            "value_readback": "Default",
        },
    )
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.select_workspace_typography_preset(101, "Large")
    assert captured.value.code == "workspace_typography_readback_invalid"


def _local_scroll_observation(selected_index: int, scrollbar_value: float) -> dict[str, object]:
    return {
        "accepted": True,
        "pid": 101,
        "window_id": 41,
        "scroll_area_frame": {"x": 200, "y": 180, "width": 520, "height": 360},
        "visible_bounds": {"x": 200, "y": 180, "width": 520, "height": 360},
        "vertical_scrollbar": {
            "present": True,
            "frame": {"x": 708, "y": 180, "width": 12, "height": 360},
            "value": scrollbar_value,
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "result_count": 20,
        "visible_result_count": 8,
        "selected_row": {
            "present": True,
            "selected_row_index": selected_index,
            "frame": {"x": 210, "y": 220, "width": 480, "height": 36},
            "visible": True,
            "focused": True,
            "selected": True,
        },
    }


def _local_scroll_without_selection() -> dict[str, object]:
    observation = _local_scroll_observation(0, 0.0)
    observation["selected_row"] = {"present": False}
    return observation


def test_workspace_local_selection_port_can_initialize_an_unselected_result_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    before = _local_scroll_without_selection()
    after = _local_scroll_observation(0, 0.0)
    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {
            "accepted": True,
            "kind": "local-result-selection",
            "transport": "cg-event-mouse-click",
            "input": "mouse",
            "pid": 101,
            "selection_changed": True,
            "before": before,
            "after": after,
        },
    )

    evidence = port.select_workspace_local_result(101, "mouse")

    assert evidence["before"]["selected_row"] == {"present": False}
    assert evidence["after"]["selected_row"]["selected_row_index"] == 0


def test_workspace_local_selection_port_validates_public_ax_cgevent_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    before = _local_scroll_observation(4, 0.4)
    after = _local_scroll_observation(5, 0.4)
    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {
            "accepted": True,
            "kind": "local-result-selection",
            "transport": "cg-event-mouse-click",
            "input": "mouse",
            "pid": 101,
            "selection_changed": True,
            "before": before,
            "after": after,
        },
    )

    evidence = port.select_workspace_local_result(101, "mouse")
    assert evidence["before"] == before
    assert evidence["after"] == after

    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {
            "accepted": True,
            "kind": "local-result-selection",
            "transport": "cg-event-keyboard-arrow-down",
            "input": "keyboard",
            "pid": 101,
            "selection_changed": False,
            "before": before,
            "after": before,
        },
    )
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.select_workspace_local_result(101, "keyboard")
    assert captured.value.code == "workspace_local_selection_readback_invalid"


def test_workspace_project_scope_port_redacts_project_identity_and_requires_ignore_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    payload = {
        "accepted": True,
        "kind": "local-project-scope-selection",
        "transport": "ax-press",
        "pid": 101,
        "window_id": 41,
        "project_identity_redacted": True,
        "selected_project": {
            "role": "AXButton",
            "frame": {"x": 10, "y": 140, "width": 280, "height": 44},
            "visible": True,
            "selected": True,
        },
        "ignore_action": {
            "accepted": True,
            "pid": 101,
            "window_id": 41,
            "selector": "#project-ignore-open",
            "present": True,
            "target_count": 1,
            "target": _selector_record("#project-ignore-open"),
        },
    }
    calls: list[tuple[str, ...]] = []

    def helper(*arguments: str) -> dict[str, object]:
        calls.append(arguments)
        return payload

    monkeypatch.setattr(port, "_helper_json", helper)

    assert port.select_workspace_project_scope(101) == payload
    assert calls == [
        ("workspace-project-scope-select", runtime.BUNDLE_IDENTIFIER, "101")
    ]
    assert '"project_id":' not in json.dumps(payload)
    assert "/" not in json.dumps(payload)

    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {
            **payload,
            "project_identity_redacted": False,
        },
    )
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.select_workspace_project_scope(101)
    assert captured.value.code == "workspace_project_scope_readback_invalid"


def test_workspace_local_publication_port_accepts_only_safe_snapshot_phase_and_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    payload = {
        "accepted": True,
        "kind": "local-publication",
        "pid": 101,
        "window_id": 41,
        "snapshot_identity_prefix": "a1b2c3d4e5f6",
        "last_complete_visible": True,
        "scan_phase": "running",
        "ignore_save_outcome": "accepted",
        "result_count": 24,
    }
    monkeypatch.setattr(port, "_helper_json", lambda *args: payload)

    assert port.observe_workspace_local_publication(101) == payload

    for mutation in (
        {"snapshot_identity_prefix": "/" + "Users/me"},
        {"scan_phase": "guessed"},
        {"ignore_save_outcome": "guessed"},
        {"result_count": -1},
        {"instance_id": "private"},
    ):
        monkeypatch.setattr(
            port,
            "_helper_json",
            lambda *args, mutation=mutation: {**payload, **mutation},
        )
        with pytest.raises(runtime.RuntimeQualificationError) as captured:
            port.observe_workspace_local_publication(101)
        assert captured.value.code == "workspace_local_publication_readback_invalid"


def test_workspace_toolbar_port_returns_only_redacted_bounded_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = runtime.MacOSRuntimeAutomationPort(tmp_path)
    names = (
        "left-disclosure",
        "identity",
        "repository",
        "checkout-register",
        "load-sot",
        "appearance",
        "typography",
        "right-disclosure",
    )
    payload = {
        "accepted": True,
        "kind": "toolbar-layout",
        "pid": 101,
        "window_id": 41,
        "window_frame": {"x": 0, "y": 0, "width": 1200, "height": 800},
        "target_count": len(names),
        "tab_order": [
            "left-disclosure",
            "repository",
            "checkout-register",
            "load-sot",
            "appearance",
            "typography",
            "right-disclosure",
        ],
        "targets": {
            name: {
                "role": "AXButton",
                "frame": {"x": index * 40, "y": 10, "width": 32, "height": 32},
                "visible": True,
                "focusable": True,
            }
            for index, name in enumerate(names)
        },
    }
    monkeypatch.setattr(port, "_helper_json", lambda *args: payload)

    assert port.observe_workspace_toolbar_layout(101) == payload

    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {**payload, "checkout_path": "/" + "Users/me/repo"},
    )
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.observe_workspace_toolbar_layout(101)
    assert captured.value.code == "workspace_toolbar_readback_invalid"

    monkeypatch.setattr(
        port,
        "_helper_json",
        lambda *args: {**payload, "tab_order": ["right-disclosure"]},
    )
    with pytest.raises(runtime.RuntimeQualificationError) as captured:
        port.observe_workspace_toolbar_layout(101)
    assert captured.value.code == "workspace_toolbar_readback_invalid"


def test_workspace_toolbar_observer_measures_real_tab_focus_order() -> None:
    source = runtime._SWIFT_HELPER_SOURCE

    assert "kAXFocusedUIElementAttribute" in source
    assert "postKeyboardKey(48)" in source
    assert '"tab_order": tabOrder' in source
