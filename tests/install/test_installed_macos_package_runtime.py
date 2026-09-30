from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
from typing import Any, Callable

import pytest

from scripts.package import build_verified_macos_app as app_package


BUILD_ID = "d" * 32
BUNDLE_IDENTIFIER = "io.github.pureliture.harnesskit"
BUNDLE_MANIFEST_SHA256 = "e" * 64
DRIVER_IDENTITY = "verify-installed-macos-package-v1"
REQUIRED_CASE_IDS = (
    "dmg-layout",
    "finder-drag-installed",
    "applications-identity",
    "catalog-discovered",
    "spotlight-name-result",
    "catalog-launch",
    "finder-icon-light",
    "finder-icon-dark",
    "catalog-icon",
    "spotlight-icon",
    "dock-icon",
    "app-switcher-icon",
    "header-icon",
)
RUNTIME_MODULE = "scripts.package.verify_installed_macos_package"
RUNTIME_AVAILABLE = importlib.util.find_spec(RUNTIME_MODULE) is not None


def test_installed_runtime_driver_module_exists() -> None:
    assert RUNTIME_AVAILABLE, (
        "M6B installed-runtime driver module is missing: "
        "scripts/package/verify_installed_macos_package.py"
    )


@pytest.fixture(scope="module")
def runtime() -> Any:
    if not RUNTIME_AVAILABLE:
        pytest.skip("M6B installed-runtime driver is not implemented yet")
    return importlib.import_module(RUNTIME_MODULE)


def test_visible_product_name_is_harnesskit(runtime: Any) -> None:
    assert runtime.PRODUCT_NAME == "HarnessKit"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _tree_snapshot(root: Path) -> list[tuple[str, str, int]]:
    if not root.exists():
        return []
    result: list[tuple[str, str, int]] = []
    for path in sorted((root, *root.rglob("*"))):
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            result.append((relative, "directory", metadata.st_mode & 0o777))
        elif stat.S_ISLNK(metadata.st_mode):
            result.append((relative, f"symlink:{os.readlink(path)}", 0))
        else:
            result.append((relative, _sha256_file(path), metadata.st_mode & 0o777))
    return result


def _error_code(runtime: Any, operation: Callable[[], object]) -> str:
    with pytest.raises(runtime.InstalledRuntimeError) as captured:
        operation()
    return captured.value.code


@dataclass(frozen=True)
class InstalledFixture:
    app: Path
    dmg: Path
    package_report: Path
    evidence: Path
    destination: Path
    owner_record: Path
    bundle_manifest_sha256: str


@pytest.fixture
def installed_fixture(tmp_path: Path) -> InstalledFixture:
    app = tmp_path / "artifacts/HarnessKit.app"
    executable = app / "Contents/MacOS/harness-desktop"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(
        b"fixture-main\0HARNESS_PACKAGE_BUILD_ID=" + BUILD_ID.encode() + b"\n"
    )
    executable.chmod(0o755)
    (app / "Contents/Resources").mkdir(parents=True)
    (app / "Contents/Resources/icon.icns").write_bytes(b"qualified-cabinet-icon")
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleDisplayName": "HarnessKit",
                "CFBundleExecutable": "harness-desktop",
                "CFBundleIdentifier": BUNDLE_IDENTIFIER,
                "CFBundleName": "HarnessKit",
                "CFBundleShortVersionString": "0.1.0",
            },
            sort_keys=True,
        )
    )
    manifest = app_package._canonical_bundle_manifest(app)
    manifest_sha256 = app_package._sha256_bytes(
        app_package._json_bytes(manifest)
    )
    dmg = tmp_path / "artifacts/HarnessKit_0.1.0_aarch64.dmg"
    dmg.write_bytes(b"qualified-dmg")
    checksum = dmg.with_name(f"{dmg.name}.sha256")
    checksum.write_text(f"{_sha256_file(dmg)}  {dmg.name}\n", encoding="ascii")
    package_report = tmp_path / "package-evidence" / BUILD_ID / "package-report.json"
    package_report.parent.mkdir(parents=True)
    package_report.write_bytes(
        _json_bytes(
            {
                "artifact_strategy": "apple-silicon-arm64-only",
                "build_id": BUILD_ID,
                "bundle_identifier": BUNDLE_IDENTIFIER,
                "distribution_scope": "local_internal",
                "dmg_contained_app_manifest_sha256": manifest_sha256,
                "dmg_sha256": _sha256_file(dmg),
                "dmg_checksum_sha256": _sha256_file(checksum),
                "install_evidence_chain": "NotRun",
                "installed_icon_evidence": "NotRun",
                "installed_runtime_state": "NotRun",
                "notarization_state": "NotConfigured",
                "product_name": "HarnessKit",
                "schema_version": 1,
                "signing_state": "AdHoc",
                "standalone_app_manifest_sha256": manifest_sha256,
                "status": "passed",
                "target": "aarch64-apple-darwin",
            }
        )
    )
    return InstalledFixture(
        app=app,
        dmg=dmg,
        package_report=package_report,
        evidence=tmp_path / "installed-evidence",
        destination=tmp_path / "Applications/HarnessKit.app",
        owner_record=(
            tmp_path
            / "Library/Application Support/io.github.pureliture.harnesskit-verifier"
            / "install-owner.json"
        ),
        bundle_manifest_sha256=manifest_sha256,
    )


class FakeInstalledRuntimePort:
    """Semantic fake: only the Finder-drag operation may create the install."""

    def __init__(
        self,
        fixture: InstalledFixture,
        *,
        host_major: int = 26,
        catalog_ready: bool = True,
        spotlight_ready: bool = True,
        drag_succeeds: bool = True,
    ) -> None:
        self.fixture = fixture
        self.host_major = host_major
        self.catalog_ready = catalog_ready
        self.spotlight_ready = spotlight_ready
        self.drag_succeeds = drag_succeeds
        self.now = 0.0
        self.actions: list[tuple[object, ...]] = []
        self.sleeps: list[float] = []
        self.stopped_owned: list[Path] = []
        self.removed: list[Path] = []
        self.pointer_drags: list[tuple[Path, Path, Path]] = []
        self.direct_copies: list[tuple[Path, Path]] = []
        self.catalog_surfaces: list[str] = []
        self.launch_sources: list[str] = []
        self.global_mutations: list[str] = []

    def preflight(self) -> dict[str, bool]:
        self.actions.append(("preflight",))
        return {
            "accessibility": True,
            "screen_capture": True,
            "system_events": True,
        }

    def host_os_version(self) -> dict[str, int]:
        return {"major": self.host_major, "minor": 5, "patch": 1}

    def mount_dmg(self, dmg: Path, evidence_dir: Path) -> dict[str, object]:
        self.actions.append(("mount_dmg", dmg))
        mountpoint = evidence_dir / "mounted-dmg"
        mountpoint.mkdir(parents=True, exist_ok=True)
        applications = mountpoint / "Applications"
        if not applications.exists():
            applications.symlink_to("/Applications", target_is_directory=True)
        return {
            "mountpoint": mountpoint,
            "app": self.fixture.app,
            "applications_alias": applications,
            "instruction_visible": True,
            "app_center": (180, 190),
            "applications_center": (480, 190),
        }

    def unmount_dmg(self, mountpoint: Path) -> None:
        self.actions.append(("unmount_dmg", mountpoint))

    def stop_owned_destination(self, destination: Path) -> None:
        self.actions.append(("stop_owned_destination", destination))
        self.stopped_owned.append(destination)

    def remove_owned_destination(self, destination: Path) -> None:
        self.actions.append(("remove_owned_destination", destination))
        self.removed.append(destination)
        shutil.rmtree(destination)

    def finder_pointer_drag(
        self,
        source_app: Path,
        applications_alias: Path,
        destination: Path,
    ) -> bool:
        self.actions.append(
            ("finder_pointer_drag", source_app, applications_alias, destination)
        )
        self.pointer_drags.append((source_app, applications_alias, destination))
        if not self.drag_succeeds:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_app, destination)
        return True

    def copy_application(self, source_app: Path, destination: Path) -> None:
        self.direct_copies.append((source_app, destination))
        raise AssertionError("direct filesystem copy is not an acceptance action")

    def observe_catalog(
        self,
        surface: str,
        bundle_identifier: str,
        destination: Path,
    ) -> dict[str, object] | None:
        self.actions.append(("observe_catalog", surface, bundle_identifier, destination))
        self.catalog_surfaces.append(surface)
        if not self.catalog_ready:
            return None
        return {
            "bundle_identifier": bundle_identifier,
            "name": "HarnessKit",
            "path": str(destination),
            "icon_visible": True,
        }

    def observe_spotlight_name(
        self,
        query: str,
        bundle_identifier: str,
        destination: Path,
    ) -> dict[str, object] | None:
        self.actions.append(
            ("observe_spotlight_name", query, bundle_identifier, destination)
        )
        if not self.spotlight_ready:
            return None
        return {
            "bundle_identifier": bundle_identifier,
            "name": query,
            "path": str(destination),
            "icon_visible": True,
        }

    def launch_result(
        self,
        source: str,
        observation: dict[str, object],
        destination: Path,
    ) -> dict[str, object]:
        self.actions.append(("launch_result", source, observation, destination))
        self.launch_sources.append(source)
        return {
            "executable": str(destination / "Contents/MacOS/harness-desktop"),
            "main_window_count": 1,
            "pid": 4123,
            "usable": True,
        }

    def capture_evidence(self, case_id: str, evidence_dir: Path) -> Path:
        self.actions.append(("capture_evidence", case_id))
        destination = evidence_dir / "screenshots" / f"{case_id}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"\x89PNG\r\n\x1a\n" + case_id.encode())
        return destination

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert 0 < seconds <= 2.0
        self.sleeps.append(seconds)
        self.now += seconds

    def reset_global_spotlight_index(self) -> None:
        self.global_mutations.append("spotlight-index-reset")
        raise AssertionError("global Spotlight reset is forbidden")

    def reset_launch_services(self) -> None:
        self.global_mutations.append("launch-services-reset")
        raise AssertionError("global LaunchServices reset is forbidden")

    def reset_dock_or_icon_cache(self) -> None:
        self.global_mutations.append("dock-icon-cache-reset")
        raise AssertionError("global Dock/icon reset is forbidden")


def _run(
    runtime: Any,
    fixture: InstalledFixture,
    port: FakeInstalledRuntimePort,
    *,
    timeout_seconds: float = 60.0,
) -> Path:
    return runtime.verify_installed_macos_package(
        app=fixture.app,
        dmg=fixture.dmg,
        package_report=fixture.package_report,
        evidence_root=fixture.evidence,
        destination=fixture.destination,
        owner_record_path=fixture.owner_record,
        port=port,
        timeout_seconds=timeout_seconds,
    )


def _expected_owner_record(fixture: InstalledFixture, runtime: Any) -> dict[str, object]:
    metadata = fixture.destination.lstat()
    return {
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "bundle_manifest_sha256": fixture.bundle_manifest_sha256,
        "destination_device": metadata.st_dev,
        "destination_inode": metadata.st_ino,
        "driver_identity": runtime.DRIVER_IDENTITY,
        "package_build_id": BUILD_ID,
        "schema_version": 1,
    }


def test_contract_constants_fix_owner_driver_polling_and_required_cases(
    runtime: Any,
) -> None:
    assert runtime.DRIVER_IDENTITY == DRIVER_IDENTITY
    assert runtime.OWNER_SCHEMA_VERSION == 1
    assert runtime.CATALOG_TIMEOUT_SECONDS == 60.0
    assert runtime.POLL_INITIAL_SECONDS == 0.5
    assert runtime.POLL_MAX_SECONDS == 2.0
    assert tuple(runtime.REQUIRED_CASE_IDS) == REQUIRED_CASE_IDS


def test_native_port_and_repository_owned_swift_observer_are_available(
    runtime: Any,
) -> None:
    observer_source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    )

    assert hasattr(runtime, "MacOSInstalledRuntimePort")
    assert observer_source.is_file()
    assert observer_source.stat().st_size > 0


def _ready_interactive_session_payload() -> dict[str, object]:
    return {
        "accessibility": True,
        "screen_capture": True,
        "session_dictionary_available": True,
        "session_on_console": True,
        "session_user_matches": True,
        "frontmost_application_available": True,
        "session_unlocked": True,
    }


def test_installed_observer_preflight_uses_documented_gui_session_signals(
    runtime: Any,
) -> None:
    observer_source = Path(runtime.__file__).resolve().with_name(
        "macos_installed_surface_observer.swift"
    )
    source = observer_source.read_text(encoding="utf-8")

    assert "CGSessionCopyCurrentDictionary" in source
    assert "kCGSessionOnConsoleKey" in source
    assert "kCGSessionUserIDKey" in source
    assert "geteuid()" in source
    assert '"session_dictionary_available"' in source
    assert '"session_on_console"' in source
    assert '"session_user_matches"' in source
    assert '"frontmost_application_available"' in source
    assert '"session_unlocked"' in source
    assert '"com.apple.loginwindow"' in source
    assert "CGSSessionScreenIsLocked" not in source


def test_native_preflight_accepts_macos_26_without_undocumented_lock_key(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    monkeypatch.setattr(port, "_observer_json", lambda *args, **kwargs: _ready_interactive_session_payload())

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        if argv[0] == "/usr/bin/osascript":
            return subprocess.CompletedProcess(argv, 0, b"1\n", b"")
        if argv[:3] == ["/usr/bin/defaults", "read", "-g"]:
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        raise AssertionError(f"unexpected argv: {argv!r}")

    monkeypatch.setattr(port, "_run", fake_run)

    assert port.preflight() == {
        "accessibility": True,
        "screen_capture": True,
        "system_events": True,
    }


def test_swift_observer_rechecks_interactive_session_before_gui_commands(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")
    stop_segment = source.split('if command == "stop-owned-application"', 1)[1]
    gui_segment = stop_segment.split('if command == "finder-layout"', 1)[0]

    assert "func interactiveSessionAvailable() -> Bool" in source
    assert "func interactiveSessionLocked() -> Bool" in source
    assert "CGSessionCopyCurrentDictionary" in source
    assert "kCGSessionOnConsoleKey" in source
    assert "kCGSessionUserIDKey" in source
    assert "geteuid()" in source
    assert "guard interactiveSessionAvailable() else" in gui_segment
    assert "guard !interactiveSessionLocked() else" in gui_segment
    assert 'failJSON("interactive_session_locked", command)' in gui_segment
    assert 'failJSON("interactive_session_state_unavailable", command)' in gui_segment
    assert '"com.apple.loginwindow"' in source
    assert "CGSSessionScreenIsLocked" not in source


@pytest.mark.parametrize(
    "payload",
    (
        {"accessibility": True, "screen_capture": True},
        {**_ready_interactive_session_payload(), "session_dictionary_available": False},
        {**_ready_interactive_session_payload(), "session_on_console": False},
        {**_ready_interactive_session_payload(), "session_user_matches": False},
        {**_ready_interactive_session_payload(), "frontmost_application_available": False},
        {**_ready_interactive_session_payload(), "session_on_console": 1},
    ),
)
def test_native_preflight_blocks_when_interactive_session_state_is_unavailable(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    monkeypatch.setattr(port, "_observer_json", lambda *args, **kwargs: payload)
    monkeypatch.setattr(
        port,
        "_run",
        lambda *args, **kwargs: pytest.fail(
            "unknown session state must stop before System Events"
        ),
    )

    with pytest.raises(runtime.InstalledRuntimeError) as captured:
        port.preflight()

    assert captured.value.code == "interactive_session_state_unavailable"
    assert captured.value.blocked is True
    assert set(captured.value.evidence or {}) == {
        "session_dictionary_available",
        "session_on_console",
        "session_user_matches",
        "frontmost_application_available",
        "session_unlocked",
    }


def test_native_preflight_reports_locked_session_exactly(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    payload = {**_ready_interactive_session_payload(), "session_unlocked": False}
    monkeypatch.setattr(port, "_observer_json", lambda *args, **kwargs: payload)
    monkeypatch.setattr(
        port,
        "_run",
        lambda *args, **kwargs: pytest.fail(
            "locked session must stop before System Events"
        ),
    )

    with pytest.raises(runtime.InstalledRuntimeError) as captured:
        port.preflight()

    assert captured.value.code == "interactive_session_locked"
    assert captured.value.blocked is True
    assert captured.value.evidence == {"session_unlocked": False}


def test_native_preflight_accepts_authoritative_interactive_session(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    monkeypatch.setattr(
        port,
        "_observer_json",
        lambda *args, **kwargs: _ready_interactive_session_payload(),
    )

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        if argv[0] == "/usr/bin/osascript":
            return subprocess.CompletedProcess(argv, 0, b"1\n", b"")
        if argv[:3] == ["/usr/bin/defaults", "read", "-g"]:
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        raise AssertionError(f"unexpected argv: {argv!r}")

    monkeypatch.setattr(port, "_run", fake_run)

    assert port.preflight() == {
        "accessibility": True,
        "screen_capture": True,
        "system_events": True,
    }


def test_native_port_compiles_repository_observer_into_evidence_collectors(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "evidence"
    port = runtime.MacOSInstalledRuntimePort(evidence)
    calls: list[dict[str, object]] = []

    def fake_run(
        argv: list[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append({"argv": argv, "timeout": timeout, "env": env})
        assert argv[:2] == ["/usr/bin/xcrun", "swiftc"]
        output = Path(argv[argv.index("-o") + 1])
        output.write_bytes(b"compiled-observer")
        output.chmod(0o755)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(port, "_run", fake_run)

    helper = port.prepare_observer(evidence)

    assert helper == evidence / "collectors/macos-installed-surface-observer"
    assert helper.read_bytes() == b"compiled-observer"
    assert helper.stat().st_mode & 0o111
    assert port.helper == helper
    assert len(calls) == 1
    compile_call = calls[0]
    argv = compile_call["argv"]
    assert isinstance(argv, list)
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    )
    assert argv[:3] == ["/usr/bin/xcrun", "swiftc", str(source)]
    required_frameworks = {
        "AppKit",
        "ApplicationServices",
        "CoreGraphics",
        "ScreenCaptureKit",
    }
    assert required_frameworks <= {
        argv[index + 1]
        for index, item in enumerate(argv[:-1])
        if item == "-framework"
    }
    assert compile_call["timeout"] == 120.0
    compile_env = compile_call["env"]
    assert isinstance(compile_env, dict)
    assert compile_env["CLANG_MODULE_CACHE_PATH"]
    assert compile_env["SWIFT_MODULECACHE_PATH"]


def test_native_runner_uses_argv_without_shell_and_bounded_stdio(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    observed: list[tuple[list[str], dict[str, object]]] = []

    def fake_subprocess_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        observed.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"ok", b"")

    monkeypatch.setattr(runtime.subprocess, "run", fake_subprocess_run)

    result = port._run(
        ["/usr/bin/example", "literal argument"],
        timeout=7.5,
        env={"PATH": "/usr/bin"},
    )

    assert result.stdout == b"ok"
    assert observed == [
        (
            ["/usr/bin/example", "literal argument"],
            {
                "check": False,
                "env": {"PATH": "/usr/bin"},
                "stderr": subprocess.PIPE,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "timeout": 7.5,
            },
        )
    ]


@pytest.mark.parametrize(
    "code",
    ("interactive_session_locked", "interactive_session_state_unavailable"),
)
def test_native_observer_preserves_mid_run_interactive_session_blockers(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    port.helper = tmp_path / "observer"
    port.helper.write_bytes(b"observer")

    monkeypatch.setattr(
        port,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [str(port.helper), "finder-layout"],
            2,
            b"",
            _json_bytes(
                {"error": {"code": code, "operation": "finder-layout"}}
            ),
        ),
    )

    with pytest.raises(runtime.InstalledRuntimeError) as captured:
        port._observer_json("finder-layout", "/private/tmp/mounted")

    assert captured.value.code == code
    assert captured.value.blocked is True
    assert captured.value.evidence == {"operation": "finder-layout"}


def test_native_port_mounts_read_only_and_detaches_only_the_recorded_mountpoint(
    runtime: Any,
    installed_fixture: InstalledFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "native-evidence"
    helper = tmp_path / "macos-installed-surface-observer"
    helper.write_bytes(b"observer")
    helper.chmod(0o755)
    port = runtime.MacOSInstalledRuntimePort(evidence)
    port.helper = helper
    calls: list[list[str]] = []

    def fake_run(
        argv: list[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del timeout, env
        calls.append(argv)
        if argv[:2] == ["/usr/bin/hdiutil", "attach"]:
            mountpoint = Path(argv[argv.index("-mountpoint") + 1])
            mountpoint.mkdir(parents=True)
            shutil.copytree(
                installed_fixture.app,
                mountpoint / "HarnessKit.app",
            )
            (mountpoint / "Applications").symlink_to(
                "/Applications", target_is_directory=True
            )
            return subprocess.CompletedProcess(
                argv,
                0,
                plistlib.dumps(
                    {"system-entities": [{"mount-point": str(mountpoint)}]},
                    sort_keys=True,
                ),
                b"",
            )
        if argv[:1] == ["/usr/bin/open"] and len(argv) == 2:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[:2] == [str(helper), "finder-layout"]:
            mountpoint = Path(argv[2])
            return subprocess.CompletedProcess(
                argv,
                0,
                _json_bytes(
                    {
                        "accepted": True,
                        "app": str(mountpoint / "HarnessKit.app"),
                        "app_center": [180, 190],
                        "applications_alias": str(mountpoint / "Applications"),
                        "applications_center": [480, 190],
                        "instruction_visible": True,
                        "mountpoint": str(mountpoint),
                    }
                ),
                b"",
            )
        if argv[:2] == ["/usr/bin/hdiutil", "detach"]:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        raise AssertionError(f"unexpected argv: {argv!r}")

    monkeypatch.setattr(port, "_run", fake_run)

    mounted = port.mount_dmg(installed_fixture.dmg, evidence)
    mountpoint = mounted["mountpoint"]

    assert isinstance(mountpoint, Path)
    assert mountpoint.resolve().is_relative_to(evidence.resolve())
    attach = calls[0]
    assert attach[:4] == [
        "/usr/bin/hdiutil",
        "attach",
        "-readonly",
        "-nobrowse",
    ]
    assert attach.count("-plist") == 1
    assert attach.count("-mountpoint") == 1
    assert attach[-1] == str(installed_fixture.dmg)
    assert "-force" not in attach
    assert "-noverify" not in attach
    assert any(
        call[:3] == [str(helper), "finder-layout", str(mountpoint)]
        for call in calls
    )
    assert ["/usr/bin/open", str(mountpoint)] in calls
    assert not any(call[:3] == ["/usr/bin/open", "-a", "Finder"] for call in calls)

    port.unmount_dmg(mountpoint)

    assert calls[-1] == ["/usr/bin/hdiutil", "detach", str(mountpoint)]
    calls_before_unknown_detach = list(calls)
    assert (
        _error_code(
            runtime,
            lambda: port.unmount_dmg(tmp_path / "unrelated-volume"),
        )
        == "dmg_mount_not_owned"
    )
    assert calls == calls_before_unknown_detach


def test_native_mount_failure_after_attach_detaches_the_exact_volume(
    runtime: Any,
    installed_fixture: InstalledFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "native-evidence"
    helper = tmp_path / "macos-installed-surface-observer"
    helper.write_bytes(b"observer")
    helper.chmod(0o755)
    port = runtime.MacOSInstalledRuntimePort(evidence)
    port.helper = helper
    calls: list[list[str]] = []
    attached_mountpoint: Path | None = None

    def fake_run(
        argv: list[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal attached_mountpoint
        del timeout, env
        calls.append(argv)
        if argv[:2] == ["/usr/bin/hdiutil", "attach"]:
            attached_mountpoint = Path(argv[argv.index("-mountpoint") + 1])
            attached_mountpoint.mkdir(parents=True)
            return subprocess.CompletedProcess(
                argv,
                0,
                plistlib.dumps(
                    {
                        "system-entities": [
                            {"mount-point": str(attached_mountpoint)}
                        ]
                    },
                    sort_keys=True,
                ),
                b"",
            )
        if argv[:1] == ["/usr/bin/open"] and len(argv) == 2:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[:2] == [str(helper), "finder-layout"]:
            return subprocess.CompletedProcess(
                argv,
                1,
                b"",
                _json_bytes(
                    {
                        "error": {
                            "code": "finder_layout_not_found",
                            "operation": "finder-layout",
                        }
                    }
                ),
            )
        if argv[:2] == ["/usr/bin/hdiutil", "detach"]:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        raise AssertionError(f"unexpected argv: {argv!r}")

    monkeypatch.setattr(port, "_run", fake_run)

    with pytest.raises(runtime.InstalledRuntimeError):
        port.mount_dmg(installed_fixture.dmg, evidence)

    assert attached_mountpoint is not None
    assert calls[-1] == [
        "/usr/bin/hdiutil",
        "detach",
        str(attached_mountpoint),
    ]


def test_native_port_retries_only_the_exact_nonforce_detach_when_temporarily_busy(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    mountpoint = (tmp_path / "mounted-dmg").resolve()
    port._owned_mountpoints.add(mountpoint)
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_run(
        argv: list[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del timeout, env
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0 if len(calls) == 3 else 16,
            b"",
            b"resource busy",
        )

    monkeypatch.setattr(port, "_run", fake_run)
    monkeypatch.setattr(port, "sleep", sleeps.append)

    port.unmount_dmg(mountpoint)

    assert calls == [
        ["/usr/bin/hdiutil", "detach", str(mountpoint)],
        ["/usr/bin/hdiutil", "detach", str(mountpoint)],
        ["/usr/bin/hdiutil", "detach", str(mountpoint)],
    ]
    assert all("-force" not in call for call in calls)
    assert sleeps == [0.25, 0.5]
    assert mountpoint not in port._owned_mountpoints


def test_native_port_and_swift_observer_share_the_approved_command_map(
    runtime: Any,
) -> None:
    command_map = {
        "preflight": "preflight",
        "mount_dmg": "finder-layout",
        "stop_owned_destination": "stop-owned-application",
        "finder_pointer_drag": "finder-drag",
        "observe_catalog": "observe-catalog",
        "observe_spotlight_name": "observe-spotlight-name",
        "launch_result": "launch-result",
        "capture_evidence": "capture-evidence",
    }
    port_type = runtime.MacOSInstalledRuntimePort
    observer_source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")

    for method_name, command in command_map.items():
        method_source = inspect.getsource(getattr(port_type, method_name))
        assert f'"{command}"' in method_source
        assert f'"{command}"' in observer_source


def test_macos_26_catalog_authority_is_the_observed_spotlight_grid(
    runtime: Any,
) -> None:
    assert runtime.MACOS_26_CATALOG_AUTHORITY == {
        "bundle_identifier": "com.apple.Spotlight",
        "layer": 23,
        "owner_name": "Spotlight",
        "window_name": "Spotlight",
    }


def test_macos_15_catalog_authority_is_the_dock_owned_launchpad_surface(
    runtime: Any,
) -> None:
    assert runtime.LEGACY_LAUNCHPAD_CATALOG_AUTHORITY == {
        "application_path": "/System/Applications/Launchpad.app",
        "bundle_identifier": "com.apple.dock",
    }


def test_macos_26_catalog_scene_accepts_the_observed_grid_cell_identity(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")
    segment = source.split("func sceneIsCatalog", 1)[1].split(
        "func closeSpotlightSurface", 1
    )[0]

    assert '== "QueryFilterBar"' in segment
    assert 'hasPrefix("Identifier:GridCell")' in segment
    assert '== "AXCell"' in segment


def test_search_observation_does_not_trust_an_ambiguous_launchservices_default(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")
    search_segment = source.split("func searchResult(", 1)[1].split(
        "func searchPayload", 1
    )[0]
    activation_segment = source.split("func activateSearchResult(", 1)[1].split(
        "func searchPayload", 1
    )[0]
    launch_segment = source.split('if command == "launch-result"', 1)[1].split(
        'if command == "capture-evidence"', 1
    )[0]

    assert "NSWorkspace.shared.urlForApplication" not in search_segment
    assert "setExactSearchQuery(field, query: query)" in search_segment
    assert "resultPollCount: Int = 40" in search_segment
    assert "for _ in 0..<resultPollCount" in search_segment
    assert "expectedExecutable" in launch_segment
    assert "executableURL?.standardizedFileURL.path" in source
    assert "failurePrefix" in launch_segment
    assert "_launch_not_observed" in launch_segment
    assert 'sourceName == "catalog" || sourceName == "spotlight_name"' in (
        launch_segment
    )
    assert 'sourceName == "catalog" ? "catalog" : "spotlight"' in launch_segment
    assert "resultPollCount: 120" in launch_segment
    assert 'sourceName == "spotlight_name"' in activation_segment
    assert "postKey(36)" in activation_segment
    assert "click(result.cell)" in activation_segment
    assert "activateSearchResult(result, sourceName: sourceName)" in launch_segment


def test_search_query_is_replaced_atomically_without_simulated_typing(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")
    replacement_segment = source.split("func setExactSearchQuery(", 1)[1].split(
        "struct SearchResult", 1
    )[0]
    search_segment = source.split("func searchResult(", 1)[1].split(
        "func activateSearchResult", 1
    )[0]

    assert "AXUIElementSetAttributeValue" in replacement_segment
    assert "kAXValueAttribute" in replacement_segment
    assert "text(field, kAXValueAttribute as CFString) == query" in (
        replacement_segment
    )
    assert "setExactSearchQuery(field, query: query)" in search_segment
    assert "postKey(" not in search_segment
    assert "postText(" not in search_segment


def test_search_query_reapplies_an_identical_value_to_refresh_a_reopened_surface(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")
    segment = source.split("func setExactSearchQuery(", 1)[1].split(
        "struct SearchResult", 1
    )[0]

    assert (
        "if text(field, kAXValueAttribute as CFString) == query { return true }\n"
        "    guard AXUIElementSetAttributeValue"
    ) not in segment
    assert segment.count("AXUIElementSetAttributeValue") == 2


def test_standard_spotlight_search_reopens_a_fresh_surface(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")
    segment = source.split("func searchResult(", 1)[1].split(
        "func activateSearchResult", 1
    )[0]

    assert 'if surface == "spotlight_name" {' in segment
    assert "closeSpotlightSurface()" in segment
    assert segment.index("closeSpotlightSurface()") < segment.index(
        "openStandardSpotlightSurface()"
    )


def test_search_observer_captures_exact_window_before_emitting_observation(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")
    segment = source.split(
        'if command == "observe-catalog" || command == "observe-spotlight-name"',
        1,
    )[1].split('if command == "launch-result"', 1)[0]

    assert "captureDestination" in segment
    assert "await captureWindow(" in segment
    assert "result.window" in segment
    assert '"search_observation_capture_failed"' in segment
    assert segment.index("await captureWindow(") < segment.index("emit(captured)")


def test_exact_window_capture_rebinds_only_one_same_identity_replacement(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")
    segment = source.split("func captureWindow(", 1)[1].split(
        "func captureMainDisplay", 1
    )[0]

    assert "let identityMatches = content.windows.filter" in segment
    assert "$0.windowID == expected.windowID" in segment
    assert "identityMatches.count == 1" in segment
    assert "identityMatches[0]" in segment
    assert '"window_id": Int(window.windowID)' in segment
    assert '"window_id": Int(expected.windowID)' not in segment


def test_appearance_restore_preserves_pending_search_evidence_until_final_cleanup(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    pending = port._pending_search_dir / "spotlight-icon.png"
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"pending-search-evidence")
    port._cached_evidence["spotlight-icon"] = pending
    port._original_dark_mode = False
    port._appearance_changed = True
    restored: list[bool] = []
    monkeypatch.setattr(port, "_set_dark_mode", restored.append)

    port._restore_appearance()

    assert restored == [False]
    assert pending.is_file()
    assert port._cached_evidence["spotlight-icon"] == pending

    port.restore_environment()

    assert not pending.exists()


def test_native_port_has_no_direct_application_copy_fallback(runtime: Any) -> None:
    port_type = runtime.MacOSInstalledRuntimePort
    source = "\n".join(
        inspect.getsource(getattr(port_type, method_name))
        for method_name in (
            "mount_dmg",
            "finder_pointer_drag",
            "remove_owned_destination",
        )
    )

    assert not hasattr(port_type, "copy_application")
    for forbidden in (
        "copy_application",
        "copytree(",
        "copy2(",
        '"/bin/cp"',
        '"/usr/bin/ditto"',
    ):
        assert forbidden not in source


def test_native_finder_drag_passes_exact_source_alias_and_destination_to_observer(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = tmp_path / "macos-installed-surface-observer"
    helper.write_bytes(b"observer")
    helper.chmod(0o755)
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    port.helper = helper
    source = tmp_path / "mounted/HarnessKit.app"
    alias = tmp_path / "mounted/Applications"
    destination = tmp_path / "Applications/HarnessKit.app"
    calls: list[list[str]] = []

    def fake_run(
        argv: list[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del timeout, env
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            _json_bytes({"accepted": True}),
            b"",
        )

    monkeypatch.setattr(port, "_run", fake_run)

    accepted = port.finder_pointer_drag(source, alias, destination)

    assert accepted is True
    assert calls == [
        [
            str(helper),
            "finder-drag",
            str(source),
            str(alias),
            str(destination),
        ]
    ]


@pytest.mark.parametrize(
    ("method_name", "arguments", "case_ids"),
    [
        (
            "observe_catalog",
            ("spotlight_apps", BUNDLE_IDENTIFIER),
            ("catalog-discovered", "catalog-icon"),
        ),
        (
            "observe_catalog",
            ("launchpad", BUNDLE_IDENTIFIER),
            ("catalog-discovered", "catalog-icon"),
        ),
        (
            "observe_spotlight_name",
            ("HarnessKit", BUNDLE_IDENTIFIER),
            ("spotlight-name-result", "spotlight-icon"),
        ),
    ],
)
def test_successful_search_observation_caches_atomic_capture_from_same_helper_call(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    arguments: tuple[str, str],
    case_ids: tuple[str, str],
) -> None:
    evidence_root = tmp_path / "evidence"
    destination = tmp_path / "Applications/HarnessKit.app"
    port = runtime.MacOSInstalledRuntimePort(evidence_root)
    calls: list[tuple[str, ...]] = []

    def fake_observer(*helper_arguments: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        calls.append(helper_arguments)
        capture_path = Path(helper_arguments[-1])
        assert capture_path.parent == port._pending_search_dir
        capture_path.write_bytes(b"\x89PNG\r\n\x1a\nsearch-window")
        return {
            "accepted": True,
            "ready": True,
            "surface": (
                arguments[0]
                if method_name == "observe_catalog"
                else "spotlight_name"
            ),
            "owner_pid": 2049,
            "window_id": 118,
            "window_title": "Spotlight",
            "window_layer": 23,
            "screenshot": str(capture_path),
        }

    monkeypatch.setattr(port, "_observer_json", fake_observer)
    monkeypatch.setattr(
        port,
        "_capture_window_observation",
        lambda *args, **kwargs: pytest.fail(
            "search evidence must be captured by the observation helper call"
        ),
    )

    observation = getattr(port, method_name)(*arguments, destination)

    assert observation is not None
    assert set(port._cached_evidence) == set(case_ids)
    assert len(calls) == 1
    assert calls[0][-1].endswith(".png")
    for case_id in case_ids:
        captured = port._cached_evidence[case_id]
        assert captured.is_file()
        published = port.capture_evidence(case_id, tmp_path / "run")
        assert published.is_file()
        assert not captured.exists()


def test_atomic_search_capture_rejects_mismatched_helper_screenshot_path(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    destination = tmp_path / "Applications/HarnessKit.app"

    def fake_observer(*arguments: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        expected_capture = Path(arguments[-1])
        expected_capture.write_bytes(b"\x89PNG\r\n\x1a\nsearch-window")
        return {
            "accepted": True,
            "ready": True,
            "surface": "spotlight_name",
            "screenshot": str(tmp_path / "untrusted.png"),
        }

    monkeypatch.setattr(port, "_observer_json", fake_observer)

    with pytest.raises(runtime.InstalledRuntimeError) as captured:
        port.observe_spotlight_name(
            "HarnessKit", BUNDLE_IDENTIFIER, destination
        )

    assert captured.value.code == "installed_evidence_observation_invalid"
    assert not any(port._pending_search_dir.glob("*.png"))


def test_observer_failure_preserves_typed_capture_context(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    port.helper = tmp_path / "observer"
    port.helper.write_bytes(b"observer")
    failure = {
        "error": {
            "code": "search_observation_capture_failed",
            "operation": "observe-spotlight-name",
            "evidence": {
                "surface": "spotlight_name",
                "window_id": 118,
                "screenshot": "/private/tmp/search.png",
            },
        }
    }
    monkeypatch.setattr(
        port,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [str(port.helper), "observe-spotlight-name"],
            2,
            b"",
            _json_bytes(failure),
        ),
    )

    with pytest.raises(runtime.InstalledRuntimeError) as captured:
        port._observer_json("observe-spotlight-name")

    assert captured.value.code == "search_observation_capture_failed"
    assert captured.value.evidence == {
        "operation": "observe-spotlight-name",
        "surface": "spotlight_name",
        "window_id": 118,
        "screenshot": "/private/tmp/search.png",
    }


def test_exact_window_capture_retries_only_transient_shareable_content_lag(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    observation = {
        "owner_pid": 2049,
        "window_id": 118,
        "window_title": "Spotlight",
        "window_layer": 23,
    }
    calls: list[tuple[str, ...]] = []
    sleeps: list[float] = []

    def fake_observer(*arguments: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        calls.append(arguments)
        if len(calls) == 1:
            raise runtime.InstalledRuntimeError("capture_window_unavailable")
        return {"accepted": True}

    monkeypatch.setattr(port, "_observer_json", fake_observer)
    monkeypatch.setattr(port, "sleep", sleeps.append)

    port._capture_window_observation(
        observation,
        tmp_path / "spotlight.png",
        "com.apple.Spotlight",
    )

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert sleeps == [runtime.CAPTURE_RETRY_INITIAL_SECONDS]


def test_exact_window_capture_allows_screen_capture_kit_to_settle_after_four_transient_misses(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    calls = 0
    sleeps: list[float] = []

    def fake_observer(*arguments: str, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        del arguments, kwargs
        calls += 1
        if calls <= 4:
            raise runtime.InstalledRuntimeError("capture_window_unavailable")
        return {"accepted": True}

    monkeypatch.setattr(port, "_observer_json", fake_observer)
    monkeypatch.setattr(port, "sleep", sleeps.append)

    port._capture_window_observation(
        {
            "owner_pid": 2049,
            "window_id": 118,
            "window_title": "Spotlight",
            "window_layer": 23,
        },
        tmp_path / "spotlight.png",
        "com.apple.Spotlight",
    )

    assert calls == 5
    assert sleeps == [0.1, 0.2, 0.4, 0.8]


def test_exact_window_capture_does_not_retry_non_transient_observer_failure(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    calls = 0

    def fake_observer(*arguments: str, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        del arguments, kwargs
        calls += 1
        raise runtime.InstalledRuntimeError("screen_capture_api_denied")

    monkeypatch.setattr(port, "_observer_json", fake_observer)

    with pytest.raises(runtime.InstalledRuntimeError) as captured:
        port._capture_window_observation(
            {
                "owner_pid": 2049,
                "window_id": 118,
                "window_title": "Spotlight",
                "window_layer": 23,
            },
            tmp_path / "spotlight.png",
            "com.apple.Spotlight",
        )

    assert captured.value.code == "screen_capture_api_denied"
    assert calls == 1


def test_exact_window_capture_exhausts_bounded_retries_fail_closed(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    calls = 0
    sleeps: list[float] = []

    def fake_observer(*arguments: str, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        del arguments, kwargs
        calls += 1
        raise runtime.InstalledRuntimeError("capture_window_unavailable")

    monkeypatch.setattr(port, "_observer_json", fake_observer)
    monkeypatch.setattr(port, "sleep", sleeps.append)

    with pytest.raises(runtime.InstalledRuntimeError) as captured:
        port._capture_window_observation(
            {
                "owner_pid": 2049,
                "window_id": 118,
                "window_title": "Spotlight",
                "window_layer": 23,
            },
            tmp_path / "spotlight.png",
            "com.apple.Spotlight",
        )

    assert captured.value.code == "capture_window_unavailable"
    assert calls == runtime.CAPTURE_RETRY_ATTEMPTS
    expected_sleeps: list[float] = []
    interval = runtime.CAPTURE_RETRY_INITIAL_SECONDS
    for _ in range(runtime.CAPTURE_RETRY_ATTEMPTS - 1):
        expected_sleeps.append(interval)
        interval = min(interval * 2, runtime.CAPTURE_RETRY_MAX_SECONDS)
    assert sleeps == expected_sleeps
    assert sum(sleeps) <= 5.0


@pytest.mark.parametrize("surface", ("launchpad", "spotlight_apps"))
def test_native_catalog_observation_routes_the_selected_os_surface_to_observer(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    destination = tmp_path / "Applications/HarnessKit.app"
    calls: list[tuple[str, ...]] = []

    def fake_observer(*arguments: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        calls.append(arguments)
        return {
            "ready": True,
            "surface": surface,
            "owner_pid": 42,
            "window_id": 7,
            "window_title": "Launchpad" if surface == "launchpad" else "Spotlight",
            "window_layer": 0 if surface == "launchpad" else 23,
        }

    monkeypatch.setattr(port, "_observer_json", fake_observer)
    monkeypatch.setattr(port, "_cache_search_observation", lambda *args: None)

    observation = port.observe_catalog(surface, BUNDLE_IDENTIFIER, destination)

    assert observation is not None
    assert observation["surface"] == surface
    assert len(calls) == 1
    assert calls[0][:-1] == (
        "observe-catalog",
        surface,
        BUNDLE_IDENTIFIER,
        str(destination),
        "HarnessKit",
    )
    assert Path(calls[0][-1]).parent == port._pending_search_dir


@pytest.mark.parametrize(
    ("source_name", "surface"),
    (("catalog", "launchpad"), ("catalog", "spotlight_apps"), ("spotlight_name", "spotlight_name")),
)
def test_native_result_launch_reopens_the_same_observed_surface(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_name: str,
    surface: str,
) -> None:
    port = runtime.MacOSInstalledRuntimePort(tmp_path / "evidence")
    destination = tmp_path / "Applications/HarnessKit.app"
    calls: list[tuple[str, ...]] = []

    def fake_observer(*arguments: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        calls.append(arguments)
        return {"accepted": True}

    monkeypatch.setattr(port, "_observer_json", fake_observer)

    result = port.launch_result(source_name, {"surface": surface}, destination)

    assert result == {"accepted": True}
    assert calls == [
        (
            "launch-result",
            source_name,
            surface,
            BUNDLE_IDENTIFIER,
            str(destination),
            "HarnessKit",
        )
    ]


def test_native_result_launch_reports_search_and_activation_failures_separately(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")
    segment = source.split('if command == "launch-result"', 1)[1].split(
        'if command == "capture-evidence"', 1
    )[0]

    assert '"\\(failurePrefix)_result_not_found"' in segment
    assert '"\\(failurePrefix)_result_activation_failed"' in segment
    assert '"\\(failurePrefix)_result_click_failed"' not in segment




def test_legacy_launchpad_adapter_opens_and_observes_the_real_dock_surface(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")

    assert 'let dockBundleIdentifier = "com.apple.dock"' in source
    assert 'let legacyLaunchpadPath = "/System/Applications/Launchpad.app"' in source
    assert "func openLegacyLaunchpadSurface" in source
    assert "func legacyLaunchpadResult" in source
    assert 'surface == "launchpad"' in source
    assert 'surface == "spotlight_apps"' in source
    assert 'failJSON("catalog_surface_unsupported", command)' in source
    assert "NSWorkspace.shared.openApplication" in source
    assert "launchpadCGWindow" in source
    assert "(13...15).contains(hostMajor)" in source


def test_swift_finder_drag_uses_pointer_events_not_filesystem_copy(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")

    assert "CGEvent" in source
    assert "leftMouseDragged" in source
    for forbidden in (
        "copyItem",
        "replaceItemAt",
        "moveItem",
        "FileManager.default.copy",
    ):
        assert forbidden not in source


def test_swift_finder_drag_targets_unique_exact_path_items_and_balances_mouse_up(
    runtime: Any,
) -> None:
    source = (
        Path(runtime.__file__).resolve().with_name(
            "macos_installed_surface_observer.swift"
        )
    ).read_text(encoding="utf-8")
    layout_segment = source.split("func finderLayout(", 1)[1].split(
        "func temporaryScreenshot", 1
    )[0]
    drag_segment = source.split('if command == "finder-drag"', 1)[1].split(
        'if command == "observe-catalog"', 1
    )[0]
    finder_layout_command = source.split('if command == "finder-layout"', 1)[
        1
    ].split('if command == "finder-drag"', 1)[0]

    assert "func elementPID(" in source
    assert "func parentElement(" in source
    assert "func exactFinderItem(" in source
    assert "finderPID:" in source
    assert "elementURLPath" in source
    assert layout_segment.count("exactFinderItem") == 2
    assert finder_layout_command.count("for _ in 0..<50") == 2
    assert "Thread.sleep(forTimeInterval: 0.1)" in finder_layout_command
    assert "NSWorkspace.shared.selectFile(" in finder_layout_command
    assert "sourcePath" in finder_layout_command
    assert "inFileViewerRootedAtPath: mountpoint" in finder_layout_command
    assert "sourcePath" in layout_segment
    assert "applicationsPath" in layout_segment
    assert "AXUIElementPerformAction" in drag_segment
    assert "kAXRaiseAction" in drag_segment
    assert "AXUIElementCopyElementAtPosition" in source
    assert "exactFinderItem" in drag_segment
    assert "parentElement" in source
    assert "elementPID(element) == finderPID" in source
    assert "sourceHitMatched" in drag_segment
    assert "applicationsHitMatched" in drag_segment
    assert "frontmostMatched" in drag_segment
    assert '"source_hit_matched": sourceHitMatched' in drag_segment
    assert '"applications_hit_matched": applicationsHitMatched' in drag_segment
    assert '"frontmost_matched": frontmostMatched' in drag_segment
    assert '"source_frame": frameRecord(layout.sourceFrame)' in drag_segment
    assert (
        '"applications_frame": frameRecord(layout.applicationsFrame)'
        in drag_segment
    )
    assert "exactFinderImage" not in source
    assert "bundleInventory" in source
    assert "lstat(" in source
    assert "sourceInventory" in drag_segment
    assert "destinationInventory == sourceInventory" in drag_segment
    assert drag_segment.index("Task { @MainActor") < drag_segment.index(
        ".leftMouseDown"
    )
    assert drag_segment.index("defer") < drag_segment.index(".leftMouseDown")
    assert drag_segment.index(".leftMouseUp") < drag_segment.index(
        "destinationInventory == sourceInventory"
    )


def test_owner_record_writer_is_atomic_owner_only_and_exact_schema(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "owner/install-owner.json"
    record = {
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "bundle_manifest_sha256": BUNDLE_MANIFEST_SHA256,
        "destination_device": 17,
        "destination_inode": 29,
        "driver_identity": DRIVER_IDENTITY,
        "package_build_id": BUILD_ID,
        "schema_version": 1,
    }
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def spy_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(runtime.os, "replace", spy_replace)

    runtime.write_install_owner_record(path, record)

    assert json.loads(path.read_text(encoding="utf-8")) == record
    assert path.stat().st_mode & 0o777 == 0o600
    assert len(replacements) == 1
    temporary, published = replacements[0]
    assert published == path
    assert temporary.parent == path.parent
    assert temporary != published
    assert not temporary.exists()
    assert list(path.parent.glob("*.tmp")) == []


def test_owner_record_atomic_failure_preserves_previous_record(
    runtime: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "owner/install-owner.json"
    path.parent.mkdir(parents=True)
    previous = b'{"previous":true}\n'
    path.write_bytes(previous)
    path.chmod(0o600)
    record = {
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "bundle_manifest_sha256": BUNDLE_MANIFEST_SHA256,
        "destination_device": 17,
        "destination_inode": 29,
        "driver_identity": DRIVER_IDENTITY,
        "package_build_id": BUILD_ID,
        "schema_version": 1,
    }

    def reject_replace(*_args: object) -> None:
        raise OSError("fixture replace failure")

    monkeypatch.setattr(runtime.os, "replace", reject_replace)

    assert (
        _error_code(runtime, lambda: runtime.write_install_owner_record(path, record))
        == "install_owner_record_write_failed"
    )
    assert path.read_bytes() == previous
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.glob("*.tmp")) == []


def test_absent_destination_allows_finder_drag_and_publishes_exact_owner(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    port = FakeInstalledRuntimePort(installed_fixture)

    report_path = _run(runtime, installed_fixture, port)

    assert report_path.is_file()
    assert installed_fixture.destination.is_dir()
    assert len(port.pointer_drags) == 1
    assert port.direct_copies == []
    assert port.removed == []
    assert json.loads(installed_fixture.owner_record.read_text(encoding="utf-8")) == (
        _expected_owner_record(installed_fixture, runtime)
    )
    assert installed_fixture.owner_record.stat().st_mode & 0o777 == 0o600


def test_unowned_existing_destination_is_rejected_without_mutation(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    marker = installed_fixture.destination / "Contents/unowned.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("do-not-touch", encoding="utf-8")
    before = _tree_snapshot(installed_fixture.destination)
    port = FakeInstalledRuntimePort(installed_fixture)

    code = _error_code(runtime, lambda: _run(runtime, installed_fixture, port))

    assert code == "installed_application_conflict"
    assert _tree_snapshot(installed_fixture.destination) == before
    assert not installed_fixture.owner_record.exists()
    assert port.actions == []


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version", 2),
        ("destination_device", -1),
        ("destination_inode", -1),
        ("bundle_identifier", "io.example.other"),
        ("package_build_id", "f" * 32),
        ("bundle_manifest_sha256", "0" * 64),
        ("driver_identity", "other-driver-v1"),
    ],
)
def test_conflicting_owner_field_rejects_without_destination_mutation(
    runtime: Any,
    installed_fixture: InstalledFixture,
    field: str,
    replacement: object,
) -> None:
    shutil.copytree(installed_fixture.app, installed_fixture.destination)
    record = _expected_owner_record(installed_fixture, runtime)
    record[field] = replacement
    installed_fixture.owner_record.parent.mkdir(parents=True)
    installed_fixture.owner_record.write_bytes(_json_bytes(record))
    installed_fixture.owner_record.chmod(0o600)
    before = _tree_snapshot(installed_fixture.destination)
    owner_before = installed_fixture.owner_record.read_bytes()
    port = FakeInstalledRuntimePort(installed_fixture)

    code = _error_code(runtime, lambda: _run(runtime, installed_fixture, port))

    assert code == "installed_application_conflict"
    assert _tree_snapshot(installed_fixture.destination) == before
    assert installed_fixture.owner_record.read_bytes() == owner_before
    assert port.actions == []


def test_exact_owned_install_can_be_replaced_and_successful_final_app_is_retained(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    shutil.copytree(installed_fixture.app, installed_fixture.destination)
    runtime.write_install_owner_record(
        installed_fixture.owner_record,
        _expected_owner_record(installed_fixture, runtime),
    )
    prior_inode = installed_fixture.destination.lstat().st_ino
    port = FakeInstalledRuntimePort(installed_fixture)

    report_path = _run(runtime, installed_fixture, port)

    assert report_path.is_file()
    assert port.stopped_owned == [installed_fixture.destination]
    assert port.removed == [installed_fixture.destination]
    stop_index = port.actions.index(
        ("stop_owned_destination", installed_fixture.destination)
    )
    remove_index = port.actions.index(
        ("remove_owned_destination", installed_fixture.destination)
    )
    drag_index = next(
        index
        for index, action in enumerate(port.actions)
        if action[0] == "finder_pointer_drag"
    )
    assert stop_index < remove_index < drag_index
    assert len(port.pointer_drags) == 1
    assert installed_fixture.destination.is_dir()
    assert installed_fixture.destination.lstat().st_ino != prior_inode
    assert json.loads(installed_fixture.owner_record.read_text(encoding="utf-8")) == (
        _expected_owner_record(installed_fixture, runtime)
    )
    assert not any(action[0] == "cleanup_final_install" for action in port.actions)


def test_exact_prior_owned_build_can_be_replaced_by_a_new_verified_build(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    prior_build_id = "c" * 32
    shutil.copytree(installed_fixture.app, installed_fixture.destination)
    prior_executable = (
        installed_fixture.destination / "Contents/MacOS/harness-desktop"
    )
    prior_executable.write_bytes(
        prior_executable.read_bytes().replace(
            BUILD_ID.encode("ascii"), prior_build_id.encode("ascii")
        )
    )
    prior_manifest = app_package._canonical_bundle_manifest(
        installed_fixture.destination
    )
    prior_manifest_sha256 = app_package._sha256_bytes(
        app_package._json_bytes(prior_manifest)
    )
    prior_metadata = installed_fixture.destination.lstat()
    runtime.write_install_owner_record(
        installed_fixture.owner_record,
        {
            "bundle_identifier": BUNDLE_IDENTIFIER,
            "bundle_manifest_sha256": prior_manifest_sha256,
            "destination_device": prior_metadata.st_dev,
            "destination_inode": prior_metadata.st_ino,
            "driver_identity": runtime.DRIVER_IDENTITY,
            "package_build_id": prior_build_id,
            "schema_version": 1,
        },
    )
    port = FakeInstalledRuntimePort(installed_fixture)

    report_path = _run(runtime, installed_fixture, port)

    assert report_path.is_file()
    assert port.stopped_owned == [installed_fixture.destination]
    assert port.removed == [installed_fixture.destination]
    assert runtime._artifact_identity(installed_fixture.destination)["build_id"] == (
        BUILD_ID
    )
    assert json.loads(installed_fixture.owner_record.read_text(encoding="utf-8")) == (
        _expected_owner_record(installed_fixture, runtime)
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("bundle_identifier", "io.example.other"),
        ("build_id", "0" * 32),
        ("standalone_app_manifest_sha256", "1" * 64),
        ("dmg_contained_app_manifest_sha256", "2" * 64),
    ],
)
def test_package_report_must_bind_the_exact_app_and_dmg_identity_before_mutation(
    runtime: Any,
    installed_fixture: InstalledFixture,
    field: str,
    replacement: str,
) -> None:
    report = json.loads(installed_fixture.package_report.read_text(encoding="utf-8"))
    report[field] = replacement
    installed_fixture.package_report.write_bytes(_json_bytes(report))
    port = FakeInstalledRuntimePort(installed_fixture)

    code = _error_code(runtime, lambda: _run(runtime, installed_fixture, port))

    assert code == "package_build_identity_mismatch"
    assert not installed_fixture.destination.exists()
    assert not installed_fixture.owner_record.exists()
    assert port.actions == []


@pytest.mark.parametrize("corruption", ["missing", "wrong_digest", "wrong_name", "wrong_report_hash"])
def test_install_rejects_a_missing_or_mismatched_download_checksum_before_mutation(
    runtime: Any,
    installed_fixture: InstalledFixture,
    corruption: str,
) -> None:
    checksum = installed_fixture.dmg.with_name(f"{installed_fixture.dmg.name}.sha256")
    if corruption == "missing":
        checksum.unlink()
    elif corruption == "wrong_digest":
        checksum.write_text(f"{'0' * 64}  {installed_fixture.dmg.name}\n", encoding="ascii")
    elif corruption == "wrong_name":
        checksum.write_text(
            f"{_sha256_file(installed_fixture.dmg)}  other.dmg\n", encoding="ascii"
        )
    else:
        report = json.loads(installed_fixture.package_report.read_text(encoding="utf-8"))
        report["dmg_checksum_sha256"] = "0" * 64
        installed_fixture.package_report.write_bytes(_json_bytes(report))
    port = FakeInstalledRuntimePort(installed_fixture)

    code = _error_code(runtime, lambda: _run(runtime, installed_fixture, port))

    assert code == "package_build_identity_mismatch"
    assert not installed_fixture.destination.exists()
    assert not installed_fixture.owner_record.exists()
    assert port.actions == []


def test_finder_pointer_drag_failure_is_not_replaced_by_direct_copy(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    port = FakeInstalledRuntimePort(installed_fixture, drag_succeeds=False)

    code = _error_code(runtime, lambda: _run(runtime, installed_fixture, port))

    assert code == "finder_drag_failed"
    assert len(port.pointer_drags) == 1
    assert port.direct_copies == []
    assert not installed_fixture.destination.exists()
    assert not installed_fixture.owner_record.exists()


def test_mounted_volume_is_detached_exactly_once_on_layout_failure(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    class InvalidLayoutPort(FakeInstalledRuntimePort):
        def mount_dmg(
            self, dmg: Path, evidence_dir: Path
        ) -> dict[str, object]:
            record = super().mount_dmg(dmg, evidence_dir)
            record["app_center"] = (181, 190)
            return record

    port = InvalidLayoutPort(installed_fixture)

    code = _error_code(runtime, lambda: _run(runtime, installed_fixture, port))

    assert code == "dmg_layout_invalid"
    mount_actions = [action for action in port.actions if action[0] == "mount_dmg"]
    detach_actions = [action for action in port.actions if action[0] == "unmount_dmg"]
    assert len(mount_actions) == 1
    assert detach_actions == [
        ("unmount_dmg", installed_fixture.evidence / "mounted-dmg")
    ]
    assert port.direct_copies == []


def test_macos_26_uses_spotlight_apps_and_bounded_spotlight_name_query(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    port = FakeInstalledRuntimePort(installed_fixture, host_major=26)

    report_path = _run(runtime, installed_fixture, port)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(port.catalog_surfaces) == {"spotlight_apps"}
    assert "launchpad" not in port.catalog_surfaces
    assert report["catalog_surface"] == "spotlight_apps"
    assert any(
        action[0] == "observe_spotlight_name" and action[1] == "HarnessKit"
        for action in port.actions
    )
    assert port.now <= runtime.CATALOG_TIMEOUT_SECONDS
    assert port.global_mutations == []


def test_macos_15_uses_launchpad_and_keeps_spotlight_name_as_a_separate_surface(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    port = FakeInstalledRuntimePort(installed_fixture, host_major=15)

    report_path = _run(runtime, installed_fixture, port)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(port.catalog_surfaces) == {"launchpad"}
    assert "spotlight_apps" not in port.catalog_surfaces
    assert report["catalog_surface"] == "launchpad"
    assert any(
        action[0] == "observe_spotlight_name" and action[1] == "HarnessKit"
        for action in port.actions
    )
    assert port.launch_sources == ["spotlight_name", "catalog"]
    assert port.now <= runtime.CATALOG_TIMEOUT_SECONDS
    assert port.global_mutations == []


def test_spotlight_and_catalog_results_each_bind_to_the_installed_executable(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    port = FakeInstalledRuntimePort(installed_fixture, host_major=26)

    report_path = _run(runtime, installed_fixture, port)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    executable = str(
        installed_fixture.destination / "Contents/MacOS/harness-desktop"
    )
    assert port.launch_sources == ["spotlight_name", "catalog"]
    assert report["search_path_bindings"] == {
        "catalog": {
            "executable": executable,
            "method": "result_launch_executable",
        },
        "spotlight_name": {
            "executable": executable,
            "method": "result_launch_executable",
        },
    }


def test_discovery_settles_each_search_surface_without_alternating_them(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    class SettlingSurfacePort(FakeInstalledRuntimePort):
        def __init__(self, fixture: InstalledFixture) -> None:
            super().__init__(fixture, catalog_ready=False, spotlight_ready=False)
            self.catalog_attempts = 0
            self.spotlight_attempts = 0

        def observe_catalog(
            self,
            surface: str,
            bundle_identifier: str,
            destination: Path,
        ) -> dict[str, object] | None:
            self.catalog_attempts += 1
            self.catalog_ready = self.catalog_attempts >= 3
            return super().observe_catalog(surface, bundle_identifier, destination)

        def observe_spotlight_name(
            self,
            query: str,
            bundle_identifier: str,
            destination: Path,
        ) -> dict[str, object] | None:
            self.spotlight_attempts += 1
            self.spotlight_ready = self.spotlight_attempts >= 3
            return super().observe_spotlight_name(
                query, bundle_identifier, destination
            )

    port = SettlingSurfacePort(installed_fixture)

    catalog, spotlight = runtime._poll_discovery(
        port,
        surface="spotlight_apps",
        destination=installed_fixture.destination,
        timeout_seconds=20.0,
    )

    assert catalog["path"] == str(installed_fixture.destination)
    assert spotlight["path"] == str(installed_fixture.destination)
    search_actions = [
        action[0]
        for action in port.actions
        if action[0] in {"observe_catalog", "observe_spotlight_name"}
    ]
    assert search_actions == [
        "observe_catalog",
        "observe_catalog",
        "observe_catalog",
        "observe_spotlight_name",
        "observe_spotlight_name",
        "observe_spotlight_name",
    ]


@pytest.mark.parametrize(
    ("catalog_ready", "spotlight_ready"),
    [(False, True), (True, False)],
)
def test_catalog_or_spotlight_timeout_is_bounded_and_never_resets_global_state(
    runtime: Any,
    installed_fixture: InstalledFixture,
    catalog_ready: bool,
    spotlight_ready: bool,
) -> None:
    timeout = 2.0
    port = FakeInstalledRuntimePort(
        installed_fixture,
        catalog_ready=catalog_ready,
        spotlight_ready=spotlight_ready,
    )

    code = _error_code(
        runtime,
        lambda: _run(runtime, installed_fixture, port, timeout_seconds=timeout),
    )

    assert code == "application_catalog_index_timeout"
    assert 0 < port.now <= timeout
    assert port.sleeps
    assert max(port.sleeps) <= runtime.POLL_MAX_SECONDS
    assert port.global_mutations == []


def test_run_report_binds_package_identity_required_cases_and_claim_boundary(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    port = FakeInstalledRuntimePort(installed_fixture)

    report_path = _run(runtime, installed_fixture, port)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert report["status"] == "passed"
    assert report["driver_identity"] == DRIVER_IDENTITY
    assert report["package_identity"] == {
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "bundle_manifest_sha256": installed_fixture.bundle_manifest_sha256,
        "package_build_id": BUILD_ID,
    }
    assert "package_report_sha256" not in report["package_identity"]
    assert report["installed_executable"] == str(
        installed_fixture.destination / "Contents/MacOS/harness-desktop"
    )
    assert report["final_installed_app_retained"] is True
    assert installed_fixture.destination.is_dir()
    assert report["claim_boundary"] == {
        "distribution_scope": "local_internal",
        "gatekeeper_warning_free": False,
        "notarization_state": "NotConfigured",
        "production_distribution_ready": False,
        "public_download_ready": False,
        "signing_state": "AdHoc",
    }
    cases = report["cases"]
    assert [case["case_id"] for case in cases] == list(REQUIRED_CASE_IDS)
    assert all(case["status"] == "passed" for case in cases)
    assert all(case["evidence"] for case in cases)
    assert all(
        not Path(relative).is_absolute()
        for case in cases
        for relative in case["evidence"]
    )
    assert set(port.catalog_surfaces) == {"spotlight_apps"}
    assert port.direct_copies == []
    assert port.global_mutations == []


def test_success_atomically_updates_only_install_fields_in_package_report(
    runtime: Any,
    installed_fixture: InstalledFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = json.loads(
        installed_fixture.package_report.read_text(encoding="utf-8")
    )
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def spy_replace(
        source: str | os.PathLike[str], target: str | os.PathLike[str]
    ) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(runtime.os, "replace", spy_replace)

    report_path = _run(
        runtime,
        installed_fixture,
        FakeInstalledRuntimePort(installed_fixture),
    )

    updated = json.loads(
        installed_fixture.package_report.read_text(encoding="utf-8")
    )
    mutable_fields = {
        "install_evidence_chain",
        "installed_icon_evidence",
        "installed_runtime_state",
    }
    assert {key: value for key, value in updated.items() if key not in mutable_fields} == {
        key: value for key, value in before.items() if key not in mutable_fields
    }
    assert updated["installed_runtime_state"] == "Passed"
    assert updated["installed_icon_evidence"] == "Passed"
    assert updated["install_evidence_chain"] == {
        "run_report": str(report_path.resolve()),
        "run_report_sha256": _sha256_file(report_path),
    }
    package_publications = [
        (source, target)
        for source, target in replacements
        if target == installed_fixture.package_report
    ]
    assert len(package_publications) == 1
    temporary, published = package_publications[0]
    assert temporary.parent == published.parent
    assert temporary != published
    assert not temporary.exists()


def test_failed_install_does_not_publish_package_report_acceptance(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    before = installed_fixture.package_report.read_bytes()

    code = _error_code(
        runtime,
        lambda: _run(
            runtime,
            installed_fixture,
            FakeInstalledRuntimePort(installed_fixture, drag_succeeds=False),
        ),
    )

    assert code == "finder_drag_failed"
    assert installed_fixture.package_report.read_bytes() == before


def test_package_report_publish_failure_preserves_original_bytes(
    runtime: Any,
    installed_fixture: InstalledFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = installed_fixture.package_report.read_bytes()
    real_replace = os.replace

    def reject_package_report_replace(
        source: str | os.PathLike[str], target: str | os.PathLike[str]
    ) -> None:
        if Path(target) == installed_fixture.package_report:
            raise OSError("fixture package report publication failure")
        real_replace(source, target)

    monkeypatch.setattr(runtime.os, "replace", reject_package_report_replace)

    code = _error_code(
        runtime,
        lambda: _run(
            runtime,
            installed_fixture,
            FakeInstalledRuntimePort(installed_fixture),
        ),
    )

    assert code == "package_report_update_failed"
    assert installed_fixture.package_report.read_bytes() == before
    assert list(installed_fixture.package_report.parent.glob("*.tmp")) == []


def test_cli_defaults_are_safe_and_expose_only_bounded_overrides(
    runtime: Any,
    installed_fixture: InstalledFixture,
) -> None:
    arguments = runtime._parse_args(
        [
            "--app",
            str(installed_fixture.app),
            "--dmg",
            str(installed_fixture.dmg),
            "--package-report",
            str(installed_fixture.package_report),
            "--evidence-root",
            str(installed_fixture.evidence),
        ]
    )

    assert arguments.destination == Path("/Applications/HarnessKit.app")
    assert arguments.owner_record == (
        Path.home()
        / "Library/Application Support/io.github.pureliture.harnesskit-verifier"
        / "install-owner.json"
    )
    assert arguments.timeout_seconds == runtime.CATALOG_TIMEOUT_SECONDS


def test_cli_parses_explicit_destination_owner_and_timeout_without_rewriting_paths(
    runtime: Any,
    installed_fixture: InstalledFixture,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "Applications/HarnessKit.app"
    owner_record = tmp_path / "state/install-owner.json"

    arguments = runtime._parse_args(
        [
            "--app",
            str(installed_fixture.app),
            "--dmg",
            str(installed_fixture.dmg),
            "--package-report",
            str(installed_fixture.package_report),
            "--evidence-root",
            str(installed_fixture.evidence),
            "--destination",
            str(destination),
            "--owner-record",
            str(owner_record),
            "--timeout-seconds",
            "12.5",
        ]
    )

    assert arguments.app == installed_fixture.app
    assert arguments.dmg == installed_fixture.dmg
    assert arguments.package_report == installed_fixture.package_report
    assert arguments.evidence_root == installed_fixture.evidence
    assert arguments.destination == destination
    assert arguments.owner_record == owner_record
    assert arguments.timeout_seconds == 12.5


def test_cli_constructs_native_port_and_prints_machine_readable_success(
    runtime: Any,
    installed_fixture: InstalledFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected_report = installed_fixture.evidence / BUILD_ID / "run-report.json"
    native_port = object()
    constructed: list[Path] = []
    calls: list[dict[str, object]] = []

    def fake_port(evidence_root: Path) -> object:
        constructed.append(evidence_root)
        return native_port

    def fake_verify(**kwargs: object) -> Path:
        calls.append(kwargs)
        expected_report.parent.mkdir(parents=True)
        expected_report.write_bytes(_json_bytes({"status": "passed"}))
        return expected_report

    monkeypatch.setattr(runtime, "MacOSInstalledRuntimePort", fake_port)
    monkeypatch.setattr(runtime, "verify_installed_macos_package", fake_verify)

    status = runtime.main(
        [
            "--app",
            str(installed_fixture.app),
            "--dmg",
            str(installed_fixture.dmg),
            "--package-report",
            str(installed_fixture.package_report),
            "--evidence-root",
            str(installed_fixture.evidence),
        ]
    )

    assert status == 0
    assert constructed == [installed_fixture.evidence]
    assert calls == [
        {
            "app": installed_fixture.app,
            "destination": Path("/Applications/HarnessKit.app"),
            "dmg": installed_fixture.dmg,
            "evidence_root": installed_fixture.evidence,
            "owner_record_path": (
                Path.home()
                / "Library/Application Support/io.github.pureliture.harnesskit-verifier"
                / "install-owner.json"
            ),
            "package_report": installed_fixture.package_report,
            "port": native_port,
            "timeout_seconds": runtime.CATALOG_TIMEOUT_SECONDS,
        }
    ]
    assert json.loads(capsys.readouterr().out) == {
        "report": str(expected_report),
        "status": "passed",
    }


def test_cli_preserves_blocked_session_status_and_bounded_evidence(
    runtime: Any,
    installed_fixture: InstalledFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        runtime,
        "MacOSInstalledRuntimePort",
        lambda evidence_root: object(),
    )

    def blocked_verify(**kwargs: object) -> Path:
        del kwargs
        raise runtime.InstalledRuntimeError(
            "interactive_session_locked",
            blocked=True,
            evidence={"screen_locked": True},
        )

    monkeypatch.setattr(runtime, "verify_installed_macos_package", blocked_verify)

    status = runtime.main(
        [
            "--app",
            str(installed_fixture.app),
            "--dmg",
            str(installed_fixture.dmg),
            "--package-report",
            str(installed_fixture.package_report),
            "--evidence-root",
            str(installed_fixture.evidence),
        ]
    )

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": {
            "code": "interactive_session_locked",
            "evidence": {"screen_locked": True},
        },
        "status": "blocked",
    }
