#!/usr/bin/env python3
"""Safely install and qualify one verified HarnessKit macOS package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from typing import Iterable, Protocol

try:
    from . import build_verified_macos_app as app_package
except ImportError:
    import build_verified_macos_app as app_package  # type: ignore[no-redef]


BUNDLE_IDENTIFIER = "io.github.pureliture.harnesskit"
PRODUCT_NAME = "HarnessKit"
DRIVER_IDENTITY = "verify-installed-macos-package-v1"
OWNER_SCHEMA_VERSION = 1
CATALOG_TIMEOUT_SECONDS = 60.0
POLL_INITIAL_SECONDS = 0.5
POLL_MAX_SECONDS = 2.0
CAPTURE_RETRY_ATTEMPTS = 8
CAPTURE_RETRY_INITIAL_SECONDS = 0.1
CAPTURE_RETRY_MAX_SECONDS = 1.0
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
_BUILD_ID_PATTERN = re.compile(rb"HARNESS_PACKAGE_BUILD_ID=([0-9a-f]{32})")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OWNER_KEYS = {
    "bundle_identifier",
    "bundle_manifest_sha256",
    "destination_device",
    "destination_inode",
    "driver_identity",
    "package_build_id",
    "schema_version",
}
MACOS_26_CATALOG_AUTHORITY = {
    "bundle_identifier": "com.apple.Spotlight",
    "layer": 23,
    "owner_name": "Spotlight",
    "window_name": "Spotlight",
}
LEGACY_LAUNCHPAD_CATALOG_AUTHORITY = {
    "bundle_identifier": "com.apple.dock",
    "application_path": "/System/Applications/Launchpad.app",
}
_OBSERVER_SOURCE = Path(__file__).with_name(
    "macos_installed_surface_observer.swift"
)


class InstalledRuntimeError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        blocked: bool = False,
        evidence: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.blocked = blocked
        self.evidence = evidence


class InstalledRuntimePort(Protocol):
    def preflight(self) -> dict[str, bool]: ...

    def host_os_version(self) -> dict[str, int]: ...

    def mount_dmg(self, dmg: Path, evidence_dir: Path) -> dict[str, object]: ...

    def unmount_dmg(self, mountpoint: Path) -> None: ...

    def stop_owned_destination(self, destination: Path) -> None: ...

    def remove_owned_destination(self, destination: Path) -> None: ...

    def finder_pointer_drag(
        self, source_app: Path, applications_alias: Path, destination: Path
    ) -> bool: ...

    def observe_catalog(
        self, surface: str, bundle_identifier: str, destination: Path
    ) -> dict[str, object] | None: ...

    def observe_spotlight_name(
        self, query: str, bundle_identifier: str, destination: Path
    ) -> dict[str, object] | None: ...

    def launch_result(
        self, source: str, observation: dict[str, object], destination: Path
    ) -> dict[str, object]: ...

    def capture_evidence(self, case_id: str, evidence_dir: Path) -> Path: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


def _fail(
    code: str,
    *,
    blocked: bool = False,
    evidence: dict[str, object] | None = None,
) -> None:
    raise InstalledRuntimeError(code, blocked=blocked, evidence=evidence)


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            _fail("package_build_identity_mismatch")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        _fail("package_build_identity_mismatch")
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _load_json(path: Path, code: str) -> dict[str, object]:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            _fail(code)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, dict):
        _fail(code)
    return value


def _valid_hash(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _artifact_identity(app: Path) -> dict[str, object]:
    try:
        metadata = app.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            _fail("package_build_identity_mismatch")
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        _fail("package_build_identity_mismatch")
    if not isinstance(info, dict):
        _fail("package_build_identity_mismatch")
    executable_name = info.get("CFBundleExecutable")
    if (
        info.get("CFBundleIdentifier") != BUNDLE_IDENTIFIER
        or info.get("CFBundleDisplayName", PRODUCT_NAME) != PRODUCT_NAME
        or info.get("CFBundleName", PRODUCT_NAME) != PRODUCT_NAME
        or not isinstance(executable_name, str)
        or not executable_name
    ):
        _fail("package_build_identity_mismatch")
    executable = app / "Contents/MacOS" / executable_name
    try:
        executable_body = executable.read_bytes()
        executable_metadata = executable.lstat()
    except OSError:
        _fail("package_build_identity_mismatch")
    match = _BUILD_ID_PATTERN.search(executable_body)
    if (
        stat.S_ISLNK(executable_metadata.st_mode)
        or not stat.S_ISREG(executable_metadata.st_mode)
        or executable_metadata.st_mode & 0o111 == 0
        or match is None
    ):
        _fail("package_build_identity_mismatch")
    manifest = app_package._canonical_bundle_manifest(app)
    return {
        "app": app,
        "build_id": match.group(1).decode("ascii"),
        "bundle_manifest_sha256": app_package._sha256_bytes(
            app_package._json_bytes(manifest)
        ),
        "executable": executable,
    }


def _package_identity(
    app: Path, dmg: Path, package_report: Path
) -> tuple[dict[str, object], dict[str, object]]:
    artifact = _artifact_identity(app)
    report = _load_json(package_report, "package_build_identity_mismatch")
    manifest_sha256 = artifact["bundle_manifest_sha256"]
    checksum = dmg.with_name(f"{dmg.name}.sha256")
    try:
        checksum_metadata = checksum.lstat()
        if not stat.S_ISREG(checksum_metadata.st_mode) or checksum_metadata.st_size > 256:
            _fail("package_build_identity_mismatch")
        checksum_body = checksum.read_bytes()
    except OSError:
        _fail("package_build_identity_mismatch")
    expected_checksum = f"{_sha256_file(dmg)}  {dmg.name}\n".encode("ascii")
    if any(
        [
            report.get("schema_version") != 1,
            report.get("status") != "passed",
            report.get("artifact_strategy") != "apple-silicon-arm64-only",
            report.get("target") != "aarch64-apple-darwin",
            report.get("product_name") != PRODUCT_NAME,
            report.get("bundle_identifier") != BUNDLE_IDENTIFIER,
            report.get("build_id") != artifact["build_id"],
            report.get("standalone_app_manifest_sha256") != manifest_sha256,
            report.get("dmg_contained_app_manifest_sha256") != manifest_sha256,
            report.get("dmg_sha256") != _sha256_file(dmg),
            report.get("dmg_checksum_sha256") != _sha256_bytes(checksum_body),
            checksum_body != expected_checksum,
            report.get("distribution_scope") != "local_internal",
            report.get("signing_state") != "AdHoc",
            report.get("notarization_state") != "NotConfigured",
        ]
    ):
        _fail("package_build_identity_mismatch")
    if not _valid_hash(manifest_sha256):
        _fail("package_build_identity_mismatch")
    package_identity = {
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "bundle_manifest_sha256": manifest_sha256,
        "package_build_id": artifact["build_id"],
    }
    return artifact, package_identity


def _owner_record(
    destination: Path, package_identity: dict[str, object]
) -> dict[str, object]:
    try:
        metadata = destination.lstat()
    except OSError:
        _fail("installed_application_conflict")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _fail("installed_application_conflict")
    return {
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "bundle_manifest_sha256": package_identity["bundle_manifest_sha256"],
        "destination_device": metadata.st_dev,
        "destination_inode": metadata.st_ino,
        "driver_identity": DRIVER_IDENTITY,
        "package_build_id": package_identity["package_build_id"],
        "schema_version": OWNER_SCHEMA_VERSION,
    }


def _valid_owner_record(value: dict[str, object]) -> bool:
    return (
        set(value) == _OWNER_KEYS
        and value.get("schema_version") == OWNER_SCHEMA_VERSION
        and value.get("bundle_identifier") == BUNDLE_IDENTIFIER
        and value.get("driver_identity") == DRIVER_IDENTITY
        and isinstance(value.get("destination_device"), int)
        and not isinstance(value.get("destination_device"), bool)
        and int(value["destination_device"]) >= 0
        and isinstance(value.get("destination_inode"), int)
        and not isinstance(value.get("destination_inode"), bool)
        and int(value["destination_inode"]) >= 0
        and isinstance(value.get("package_build_id"), str)
        and re.fullmatch(r"[0-9a-f]{32}", str(value["package_build_id"])) is not None
        and _valid_hash(value.get("bundle_manifest_sha256"))
    )


def write_install_owner_record(path: Path, record: dict[str, object]) -> None:
    if not _valid_owner_record(record):
        _fail("install_owner_record_invalid")
    parent = path.parent
    temporary = parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            _fail("install_owner_record_write_failed")
        parent.chmod(0o700)
        body = _json_bytes(record)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except InstalledRuntimeError:
        raise
    except OSError:
        _fail("install_owner_record_write_failed")
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _owned_destination_matches(
    destination: Path,
    owner_record_path: Path,
) -> bool:
    if not destination.exists() and not destination.is_symlink():
        return False
    try:
        owner = _load_json(owner_record_path, "installed_application_conflict")
        metadata = owner_record_path.lstat()
    except InstalledRuntimeError:
        _fail("installed_application_conflict")
    try:
        destination_metadata = destination.lstat()
    except OSError:
        _fail("installed_application_conflict")
    if (
        stat.S_IMODE(metadata.st_mode) != 0o600
        or not _valid_owner_record(owner)
        or stat.S_ISLNK(destination_metadata.st_mode)
        or not stat.S_ISDIR(destination_metadata.st_mode)
        or owner.get("destination_device") != destination_metadata.st_dev
        or owner.get("destination_inode") != destination_metadata.st_ino
    ):
        _fail("installed_application_conflict")
    actual = _artifact_identity(destination)
    if (
        actual["build_id"] != owner["package_build_id"]
        or actual["bundle_manifest_sha256"]
        != owner["bundle_manifest_sha256"]
    ):
        _fail("installed_application_conflict")
    return True


class MacOSInstalledRuntimePort:
    """Native macOS GUI adapter for installed package qualification."""

    def __init__(self, evidence_root: Path) -> None:
        self.evidence_root = evidence_root
        self.helper: Path | None = None
        self._owned_mountpoints: set[Path] = set()
        self._cached_evidence: dict[str, Path] = {}
        self._catalog_observation: dict[str, object] | None = None
        self._catalog_surface: str | None = None
        self._spotlight_observation: dict[str, object] | None = None
        self._launch_observation: dict[str, object] | None = None
        self._destination: Path | None = None
        self._original_dark_mode: bool | None = None
        self._appearance_changed = False
        self._pending_search_dir = (
            self.evidence_root / ".pending-search-captures"
        )

    def _run(
        self,
        argv: list[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            argv,
            check=False,
            env=env,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            timeout=timeout,
        )

    def prepare_observer(self, evidence_dir: Path) -> Path:
        if not _OBSERVER_SOURCE.is_file():
            _fail("installed_observer_source_missing")
        collector_dir = evidence_dir / "collectors"
        helper = collector_dir / "macos-installed-surface-observer"
        module_cache = collector_dir / "module-cache"
        try:
            collector_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            module_cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            collector_dir.chmod(0o700)
            module_cache.chmod(0o700)
        except OSError:
            _fail("installed_observer_compile_failed")
        compile_environment = {
            **os.environ,
            "CLANG_MODULE_CACHE_PATH": str(module_cache),
            "SWIFT_MODULECACHE_PATH": str(module_cache),
        }
        result = self._run(
            [
                "/usr/bin/xcrun",
                "swiftc",
                str(_OBSERVER_SOURCE),
                "-framework",
                "AppKit",
                "-framework",
                "ApplicationServices",
                "-framework",
                "CoreGraphics",
                "-framework",
                "ImageIO",
                "-framework",
                "ScreenCaptureKit",
                "-framework",
                "UniformTypeIdentifiers",
                "-o",
                str(helper),
            ],
            timeout=120.0,
            env=compile_environment,
        )
        if result.returncode != 0 or not helper.is_file():
            _fail("installed_observer_compile_failed")
        try:
            helper.chmod(0o700)
        except OSError:
            _fail("installed_observer_compile_failed")
        self.helper = helper
        return helper

    def _ensure_observer(self) -> Path:
        if self.helper is None:
            return self.prepare_observer(self.evidence_root)
        return self.helper

    def _observer_json(self, *arguments: str, timeout: float = 60.0) -> dict[str, object]:
        helper = self._ensure_observer()
        result = self._run([str(helper), *arguments], timeout=timeout)
        if result.returncode != 0:
            try:
                failure = json.loads(result.stderr.decode("utf-8"))
                error = failure["error"]
                code = error["code"]
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
                _fail("installed_observer_operation_failed")
            if not isinstance(code, str) or not code:
                _fail("installed_observer_operation_failed")
            operation = error.get("operation")
            if not isinstance(operation, str) or not operation:
                operation = arguments[0] if arguments else "observer"
            raw_evidence = error.get("evidence")
            evidence = (
                dict(raw_evidence)
                if isinstance(raw_evidence, dict)
                else {}
            )
            evidence["operation"] = operation
            if code in {
                "interactive_session_locked",
                "interactive_session_state_unavailable",
            }:
                _fail(
                    code,
                    blocked=True,
                    evidence=evidence,
                )
            _fail(code, evidence=evidence)
        try:
            payload = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail("installed_observer_output_invalid")
        if not isinstance(payload, dict):
            _fail("installed_observer_output_invalid")
        return payload

    def preflight(self) -> dict[str, bool]:
        payload = self._observer_json("preflight")
        session_fields = (
            "session_dictionary_available",
            "session_on_console",
            "session_user_matches",
            "frontmost_application_available",
            "session_unlocked",
        )
        session_evidence = {
            field: value if isinstance(value := payload.get(field), bool) else None
            for field in session_fields
        }
        if session_evidence["session_unlocked"] is False:
            _fail(
                "interactive_session_locked",
                blocked=True,
                evidence={"session_unlocked": False},
            )
        if any(session_evidence[field] is not True for field in session_fields):
            _fail(
                "interactive_session_state_unavailable",
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
                _fail("system_events_preflight_failed")
        appearance = self._run(
            ["/usr/bin/defaults", "read", "-g", "AppleInterfaceStyle"]
        )
        self._original_dark_mode = (
            appearance.returncode == 0
            and appearance.stdout.decode("utf-8", "replace").strip() == "Dark"
        )
        return {
            "accessibility": payload.get("accessibility") is True,
            "screen_capture": payload.get("screen_capture") is True,
            "system_events": system_events_allowed,
        }

    def host_os_version(self) -> dict[str, int]:
        parts = platform.mac_ver()[0].split(".")
        try:
            values = [int(value) for value in parts[:3]]
        except ValueError:
            _fail("host_os_version_invalid")
        while len(values) < 3:
            values.append(0)
        return {"major": values[0], "minor": values[1], "patch": values[2]}

    def mount_dmg(self, dmg: Path, evidence_dir: Path) -> dict[str, object]:
        self._ensure_observer()
        try:
            evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            evidence_dir.chmod(0o700)
        except OSError:
            _fail("dmg_mount_failed")
        mountpoint = (
            evidence_dir / f"mounted-dmg-{secrets.token_hex(6)}"
        ).resolve()
        result = self._run(
            [
                "/usr/bin/hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-plist",
                "-mountpoint",
                str(mountpoint),
                str(dmg),
            ]
        )
        if result.returncode != 0:
            _fail("dmg_mount_failed")
        try:
            response = plistlib.loads(result.stdout)
            entities = response["system-entities"]
            mounted_paths = {
                Path(str(entity["mount-point"])).resolve()
                for entity in entities
                if isinstance(entity, dict) and "mount-point" in entity
            }
        except (plistlib.InvalidFileException, KeyError, TypeError, ValueError):
            mounted_paths = set()
        if mounted_paths != {mountpoint}:
            if mountpoint.exists():
                self._owned_mountpoints.add(mountpoint)
                self.unmount_dmg(mountpoint)
            _fail("dmg_mount_identity_mismatch")
        self._owned_mountpoints.add(mountpoint)
        try:
            opened = self._run(["/usr/bin/open", str(mountpoint)])
            if opened.returncode != 0:
                _fail("finder_window_open_failed")
            payload = self._observer_json("finder-layout", str(mountpoint))
            app = Path(str(payload.get("app", "")))
            applications_alias = Path(
                str(payload.get("applications_alias", ""))
            )
            if (
                payload.get("accepted") is not True
                or app != mountpoint / "HarnessKit.app"
                or applications_alias != mountpoint / "Applications"
                or not app.is_dir()
                or not applications_alias.is_symlink()
                or applications_alias.resolve() != Path("/Applications")
            ):
                _fail("dmg_layout_invalid")
            screenshot = payload.get("screenshot")
            if isinstance(screenshot, str) and Path(screenshot).is_file():
                self._cached_evidence["dmg-layout"] = Path(screenshot)
            app_center = payload.get("app_center")
            applications_center = payload.get("applications_center")
            return {
                "mountpoint": mountpoint,
                "app": app,
                "applications_alias": applications_alias,
                "instruction_visible": payload.get("instruction_visible") is True,
                "app_center": tuple(app_center) if isinstance(app_center, list) else None,
                "applications_center": (
                    tuple(applications_center)
                    if isinstance(applications_center, list)
                    else None
                ),
            }
        except BaseException:
            if mountpoint in self._owned_mountpoints:
                self.unmount_dmg(mountpoint)
            raise

    def unmount_dmg(self, mountpoint: Path) -> None:
        exact = mountpoint.resolve()
        if exact not in self._owned_mountpoints:
            _fail("dmg_mount_not_owned")
        interval = 0.25
        for attempt in range(5):
            result = self._run(["/usr/bin/hdiutil", "detach", str(exact)])
            if result.returncode == 0:
                self._owned_mountpoints.remove(exact)
                return
            if attempt < 4:
                self.sleep(interval)
                interval = min(interval * 2, 1.0)
        _fail("dmg_unmount_failed")

    def remove_owned_destination(self, destination: Path) -> None:
        try:
            metadata = destination.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                _fail("installed_application_conflict")
            shutil.rmtree(destination)
        except InstalledRuntimeError:
            raise
        except OSError:
            _fail("owned_destination_remove_failed")

    def stop_owned_destination(self, destination: Path) -> None:
        payload = self._observer_json(
            "stop-owned-application",
            BUNDLE_IDENTIFIER,
            str(destination),
        )
        if payload.get("accepted") is not True:
            _fail("owned_destination_stop_failed")

    def finder_pointer_drag(
        self, source_app: Path, applications_alias: Path, destination: Path
    ) -> bool:
        payload = self._observer_json(
            "finder-drag",
            str(source_app),
            str(applications_alias),
            str(destination),
            timeout=90.0,
        )
        screenshot = payload.get("screenshot")
        if isinstance(screenshot, str) and Path(screenshot).is_file():
            self._cached_evidence["finder-drag-installed"] = Path(screenshot)
        self._destination = destination
        return payload.get("accepted") is True

    def observe_catalog(
        self, surface: str, bundle_identifier: str, destination: Path
    ) -> dict[str, object] | None:
        if surface not in {"launchpad", "spotlight_apps"}:
            _fail("catalog_surface_unsupported")
        capture_path = self._new_search_capture_path(surface)
        payload = self._observer_json(
            "observe-catalog",
            surface,
            bundle_identifier,
            str(destination),
            PRODUCT_NAME,
            str(capture_path),
        )
        if payload.get("ready") is not True:
            self._discard_pending_search_capture(capture_path)
            return None
        if payload.get("surface") != surface:
            self._discard_pending_search_capture(capture_path)
            _fail("installed_evidence_observation_invalid")
        self._catalog_observation = payload
        self._catalog_surface = surface
        self._destination = destination
        self._cache_search_observation(
            payload,
            ("catalog-discovered", "catalog-icon"),
            capture_path,
        )
        return payload

    def observe_spotlight_name(
        self, query: str, bundle_identifier: str, destination: Path
    ) -> dict[str, object] | None:
        capture_path = self._new_search_capture_path("spotlight-name")
        payload = self._observer_json(
            "observe-spotlight-name",
            bundle_identifier,
            str(destination),
            query,
            str(capture_path),
        )
        if payload.get("ready") is not True:
            self._discard_pending_search_capture(capture_path)
            return None
        if payload.get("surface") != "spotlight_name":
            self._discard_pending_search_capture(capture_path)
            _fail("installed_evidence_observation_invalid")
        self._spotlight_observation = payload
        self._destination = destination
        self._cache_search_observation(
            payload,
            ("spotlight-name-result", "spotlight-icon"),
            capture_path,
        )
        return payload

    def _new_search_capture_path(self, surface: str) -> Path:
        try:
            self._pending_search_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if (
                self._pending_search_dir.is_symlink()
                or not self._pending_search_dir.is_dir()
            ):
                _fail("installed_evidence_write_failed")
            self._pending_search_dir.chmod(0o700)
        except OSError:
            _fail("installed_evidence_write_failed")
        return self._pending_search_dir / (
            f"{surface}-{secrets.token_hex(8)}.png"
        )

    def _discard_pending_search_capture(self, capture_path: Path) -> None:
        try:
            if capture_path.parent != self._pending_search_dir:
                return
            if capture_path.is_file() and not capture_path.is_symlink():
                capture_path.unlink()
        except OSError:
            pass

    def _cache_search_observation(
        self,
        observation: dict[str, object],
        case_ids: tuple[str, str],
        capture_path: Path,
    ) -> None:
        try:
            if (
                observation.get("accepted") is not True
                or observation.get("screenshot") != str(capture_path)
                or capture_path.parent != self._pending_search_dir
            ):
                _fail("installed_evidence_observation_invalid")
            metadata = capture_path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                _fail("installed_evidence_capture_failed")
            uncached = [
                case_id
                for case_id in case_ids
                if case_id not in self._cached_evidence
            ]
            if not uncached:
                capture_path.unlink()
                return
            self._cached_evidence[uncached[0]] = capture_path
            for case_id in uncached[1:]:
                duplicate = self._pending_search_dir / (
                    f"{case_id}-{secrets.token_hex(8)}.png"
                )
                shutil.copyfile(capture_path, duplicate, follow_symlinks=False)
                duplicate.chmod(0o600)
                self._cached_evidence[case_id] = duplicate
        except InstalledRuntimeError:
            self._discard_pending_search_capture(capture_path)
            raise
        except OSError:
            self._discard_pending_search_capture(capture_path)
            _fail("installed_evidence_write_failed")

    def launch_result(
        self, source: str, observation: dict[str, object], destination: Path
    ) -> dict[str, object]:
        surface = observation.get("surface")
        if source == "catalog" and surface not in {"launchpad", "spotlight_apps"}:
            _fail("installed_evidence_observation_invalid")
        if source == "spotlight_name" and surface != "spotlight_name":
            _fail("installed_evidence_observation_invalid")
        payload = self._observer_json(
            "launch-result",
            source,
            str(surface),
            BUNDLE_IDENTIFIER,
            str(destination),
            PRODUCT_NAME,
        )
        self._launch_observation = payload
        self._destination = destination
        return payload

    def _set_dark_mode(self, dark: bool) -> None:
        script = (
            'tell application "System Events" to tell appearance preferences '
            f"to set dark mode to {'true' if dark else 'false'}"
        )
        result = self._run(["/usr/bin/osascript", "-e", script])
        if result.returncode != 0:
            error = result.stderr.decode("utf-8", "replace")
            if "-1743" in error or "Not authorized to send Apple events" in error:
                _fail("system_events_automation_denied")
            _fail("appearance_change_failed")
        self._appearance_changed = self._original_dark_mode != dark
        time.sleep(1.0)

    def _restore_appearance(self) -> None:
        if self._appearance_changed and self._original_dark_mode is not None:
            original = self._original_dark_mode
            self._appearance_changed = False
            self._set_dark_mode(original)
            self._appearance_changed = False

    def restore_environment(self) -> None:
        self._restore_appearance()
        for path in tuple(self._cached_evidence.values()):
            if path.parent != self._pending_search_dir:
                continue
            try:
                if path.is_file() and not path.is_symlink():
                    path.unlink()
            except OSError:
                pass
        try:
            self._pending_search_dir.rmdir()
        except OSError:
            pass

    def _capture_window_observation(
        self, observation: dict[str, object], destination: Path, bundle: str
    ) -> None:
        try:
            owner_pid = int(observation["owner_pid"])
            window_id = int(observation["window_id"])
            title = str(observation["window_title"])
            layer = int(observation["window_layer"])
        except (KeyError, TypeError, ValueError):
            _fail("installed_evidence_observation_invalid")
        arguments = (
            "capture-evidence",
            "window",
            bundle,
            str(owner_pid),
            str(window_id),
            title,
            str(layer),
            str(destination),
            "false",
        )
        interval = CAPTURE_RETRY_INITIAL_SECONDS
        for attempt in range(CAPTURE_RETRY_ATTEMPTS):
            try:
                payload = self._observer_json(*arguments)
                break
            except InstalledRuntimeError as error:
                if (
                    error.code != "capture_window_unavailable"
                    or attempt == CAPTURE_RETRY_ATTEMPTS - 1
                ):
                    raise
                self.sleep(interval)
                interval = min(interval * 2, CAPTURE_RETRY_MAX_SECONDS)
        if payload.get("accepted") is not True:
            _fail("installed_evidence_capture_failed")

    def _capture_finder_reveal(self, destination: Path) -> None:
        if self._destination is None:
            _fail("installed_evidence_observation_invalid")
        opened = self._run(["/usr/bin/open", "-R", str(self._destination)])
        if opened.returncode != 0:
            _fail("finder_reveal_failed")
        time.sleep(1.0)
        payload = self._observer_json(
            "capture-evidence", "finder-reveal", str(destination)
        )
        if payload.get("accepted") is not True:
            _fail("installed_evidence_capture_failed")

    def capture_evidence(self, case_id: str, evidence_dir: Path) -> Path:
        destination = evidence_dir / "screenshots" / f"{case_id}.png"
        try:
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.parent.chmod(0o700)
        except OSError:
            _fail("installed_evidence_write_failed")
        cached = self._cached_evidence.get(case_id)
        if cached is not None and cached.is_file():
            try:
                os.replace(cached, destination)
            except OSError:
                _fail("installed_evidence_write_failed")
            return destination
        if case_id == "applications-identity":
            self._capture_finder_reveal(destination)
        elif case_id == "finder-icon-light":
            self._set_dark_mode(False)
            try:
                self._capture_finder_reveal(destination)
            finally:
                self._restore_appearance()
        elif case_id == "finder-icon-dark":
            self._set_dark_mode(True)
            try:
                self._capture_finder_reveal(destination)
            finally:
                self._restore_appearance()
        elif case_id in {"catalog-discovered", "catalog-icon"}:
            if self._destination is None or self._catalog_surface is None:
                _fail("installed_evidence_observation_invalid")
            observation = self.observe_catalog(
                self._catalog_surface, BUNDLE_IDENTIFIER, self._destination
            )
            if observation is None:
                _fail("installed_evidence_observation_invalid")
            cached = self._cached_evidence.get(case_id)
            if cached is None or cached.is_symlink() or not cached.is_file():
                _fail("installed_evidence_capture_failed")
            try:
                os.replace(cached, destination)
            except OSError:
                _fail("installed_evidence_write_failed")
            return destination
        elif case_id in {"spotlight-name-result", "spotlight-icon"}:
            if self._destination is None:
                _fail("installed_evidence_observation_invalid")
            observation = self.observe_spotlight_name(
                PRODUCT_NAME, BUNDLE_IDENTIFIER, self._destination
            )
            if observation is None:
                _fail("installed_evidence_observation_invalid")
            cached = self._cached_evidence.get(case_id)
            if cached is None or cached.is_symlink() or not cached.is_file():
                _fail("installed_evidence_capture_failed")
            try:
                os.replace(cached, destination)
            except OSError:
                _fail("installed_evidence_write_failed")
            return destination
        elif case_id in {"catalog-launch", "header-icon"}:
            if self._launch_observation is None:
                _fail("installed_evidence_observation_invalid")
            observation = dict(self._launch_observation)
            observation["owner_pid"] = observation.get("pid")
            self._capture_window_observation(
                observation, destination, BUNDLE_IDENTIFIER
            )
        elif case_id == "dock-icon":
            payload = self._observer_json(
                "capture-evidence",
                "dock",
                PRODUCT_NAME,
                str(destination),
            )
            if payload.get("accepted") is not True:
                _fail("installed_evidence_capture_failed")
        elif case_id == "app-switcher-icon":
            payload = self._observer_json(
                "capture-evidence",
                "app-switcher",
                PRODUCT_NAME,
                str(destination),
            )
            if payload.get("accepted") is not True:
                _fail("installed_evidence_capture_failed")
        else:
            _fail("installed_evidence_case_unsupported")
        if destination.is_symlink() or not destination.is_file():
            _fail("installed_evidence_missing")
        return destination

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _require_preflight(port: InstalledRuntimePort) -> None:
    preflight = port.preflight()
    if preflight.get("accessibility") is not True:
        _fail("accessibility_api_denied")
    if preflight.get("screen_capture") is not True:
        _fail("screen_capture_api_denied")
    if preflight.get("system_events") is not True:
        _fail("system_events_automation_denied")


def _poll_discovery(
    port: InstalledRuntimePort,
    *,
    surface: str,
    destination: Path,
    timeout_seconds: float,
) -> tuple[dict[str, object], dict[str, object]]:
    started = port.monotonic()
    interval = POLL_INITIAL_SECONDS
    last_catalog: dict[str, object] | None = None
    while last_catalog is None:
        last_catalog = port.observe_catalog(
            surface, BUNDLE_IDENTIFIER, destination
        )
        if last_catalog is not None:
            break
        elapsed = port.monotonic() - started
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            _fail("application_catalog_index_timeout")
        sleep_seconds = min(interval, POLL_MAX_SECONDS, remaining)
        if sleep_seconds <= 0:
            _fail("application_catalog_index_timeout")
        port.sleep(sleep_seconds)
        interval = min(interval * 2, POLL_MAX_SECONDS)

    interval = POLL_INITIAL_SECONDS
    last_spotlight: dict[str, object] | None = None
    while last_spotlight is None:
        last_spotlight = port.observe_spotlight_name(
            PRODUCT_NAME, BUNDLE_IDENTIFIER, destination
        )
        if last_spotlight is not None:
            break
        elapsed = port.monotonic() - started
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            _fail("application_catalog_index_timeout")
        sleep_seconds = min(interval, POLL_MAX_SECONDS, remaining)
        if sleep_seconds <= 0:
            _fail("application_catalog_index_timeout")
        port.sleep(sleep_seconds)
        interval = min(interval * 2, POLL_MAX_SECONDS)

    return last_catalog, last_spotlight


def _write_report(path: Path, value: dict[str, object]) -> None:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.parent.is_symlink() or not path.parent.is_dir() or path.exists():
            _fail("installed_evidence_write_failed")
        path.parent.chmod(0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        _fail("installed_evidence_write_failed")


def _update_package_report(package_report: Path, run_report: Path) -> None:
    temporary = package_report.parent / (
        f".{package_report.name}.{secrets.token_hex(8)}.tmp"
    )
    try:
        metadata = package_report.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            _fail("package_report_update_failed")
        report = _load_json(package_report, "package_report_update_failed")
        report["installed_runtime_state"] = "Passed"
        report["installed_icon_evidence"] = "Passed"
        report["install_evidence_chain"] = {
            "run_report": str(run_report.resolve()),
            "run_report_sha256": _sha256_file(run_report),
        }
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IMODE(metadata.st_mode),
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(_json_bytes(report))
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
        os.replace(temporary, package_report)
        descriptor = os.open(package_report.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except InstalledRuntimeError:
        raise
    except OSError:
        _fail("package_report_update_failed")
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def verify_installed_macos_package(
    *,
    app: Path,
    dmg: Path,
    package_report: Path,
    evidence_root: Path,
    destination: Path = Path("/Applications/HarnessKit.app"),
    owner_record_path: Path = Path.home()
    / "Library/Application Support/io.github.pureliture.harnesskit-verifier/install-owner.json",
    port: InstalledRuntimePort,
    timeout_seconds: float = CATALOG_TIMEOUT_SECONDS,
) -> Path:
    if timeout_seconds <= 0 or timeout_seconds > CATALOG_TIMEOUT_SECONDS:
        _fail("installed_runtime_timeout_invalid")
    artifact, package_identity = _package_identity(app, dmg, package_report)
    prior_owned = _owned_destination_matches(
        destination, owner_record_path
    )
    _require_preflight(port)
    evidence_root = evidence_root.resolve()
    mounted: Path | None = None
    try:
        mounted_record = port.mount_dmg(dmg, evidence_root)
        mounted = mounted_record.get("mountpoint")  # type: ignore[assignment]
        source_app = mounted_record.get("app")
        applications_alias = mounted_record.get("applications_alias")
        if (
            not isinstance(mounted, Path)
            or not isinstance(source_app, Path)
            or not isinstance(applications_alias, Path)
            or mounted_record.get("instruction_visible") is not True
            or mounted_record.get("app_center") != (180, 190)
            or mounted_record.get("applications_center") != (480, 190)
        ):
            _fail("dmg_layout_invalid")
        mounted_identity = _artifact_identity(source_app)
        if (
            mounted_identity["build_id"] != package_identity["package_build_id"]
            or mounted_identity["bundle_manifest_sha256"]
            != package_identity["bundle_manifest_sha256"]
        ):
            _fail("package_build_identity_mismatch")
        if prior_owned:
            port.stop_owned_destination(destination)
            port.remove_owned_destination(destination)
        if destination.exists() or destination.is_symlink():
            _fail("installed_application_conflict")
        if not port.finder_pointer_drag(source_app, applications_alias, destination):
            _fail("finder_drag_failed")
        installed_identity = _artifact_identity(destination)
        if (
            installed_identity["build_id"] != package_identity["package_build_id"]
            or installed_identity["bundle_manifest_sha256"]
            != package_identity["bundle_manifest_sha256"]
            or installed_identity["executable"]
            != destination / "Contents/MacOS/harness-desktop"
        ):
            _fail("installed_application_identity_mismatch")
        write_install_owner_record(
            owner_record_path, _owner_record(destination, package_identity)
        )
    finally:
        if mounted is not None:
            port.unmount_dmg(mounted)

    version = port.host_os_version()
    surface = "spotlight_apps" if int(version.get("major", 0)) >= 26 else "launchpad"
    catalog, spotlight = _poll_discovery(
        port,
        surface=surface,
        destination=destination,
        timeout_seconds=timeout_seconds,
    )
    expected_executable = str(destination / "Contents/MacOS/harness-desktop")
    spotlight_launch = port.launch_result(
        "spotlight_name", spotlight, destination
    )
    if (
        spotlight_launch.get("executable") != expected_executable
        or spotlight_launch.get("main_window_count") != 1
        or spotlight_launch.get("usable") is not True
    ):
        _fail("spotlight_launch_identity_mismatch")
    catalog_launch = port.launch_result("catalog", catalog, destination)
    if (
        catalog_launch.get("executable") != expected_executable
        or catalog_launch.get("main_window_count") != 1
        or catalog_launch.get("usable") is not True
    ):
        _fail("catalog_launch_identity_mismatch")
    if any(
        observation.get("bundle_identifier") != BUNDLE_IDENTIFIER
        or observation.get("name") != PRODUCT_NAME
        or observation.get("path") != str(destination)
        or observation.get("icon_visible") is not True
        for observation in (catalog, spotlight)
    ):
        _fail("application_catalog_identity_mismatch")

    run_dir = evidence_root / str(package_identity["package_build_id"])
    cases: list[dict[str, object]] = []
    for case_id in REQUIRED_CASE_IDS:
        evidence_path = port.capture_evidence(case_id, run_dir)
        try:
            relative = evidence_path.relative_to(run_dir).as_posix()
        except ValueError:
            _fail("installed_evidence_path_invalid")
        if evidence_path.is_symlink() or not evidence_path.is_file():
            _fail("installed_evidence_missing")
        cases.append(
            {
                "case_id": case_id,
                "status": "passed",
                "evidence": [relative],
                "sha256": _sha256_file(evidence_path),
            }
        )
    report = {
        "catalog_surface": surface,
        "cases": cases,
        "claim_boundary": {
            "distribution_scope": "local_internal",
            "gatekeeper_warning_free": False,
            "notarization_state": "NotConfigured",
            "production_distribution_ready": False,
            "public_download_ready": False,
            "signing_state": "AdHoc",
        },
        "driver_identity": DRIVER_IDENTITY,
        "final_installed_app_retained": True,
        "installed_executable": str(destination / "Contents/MacOS/harness-desktop"),
        "package_identity": package_identity,
        "schema_version": 1,
        "search_path_bindings": {
            "catalog": {
                "executable": expected_executable,
                "method": "result_launch_executable",
            },
            "spotlight_name": {
                "executable": expected_executable,
                "method": "result_launch_executable",
            },
        },
        "status": "passed",
    }
    report_path = run_dir / "run-report.json"
    _write_report(report_path, report)
    _update_package_report(package_report, report_path)
    return report_path


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--dmg", type=Path, required=True)
    parser.add_argument("--package-report", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("/Applications/HarnessKit.app"),
    )
    parser.add_argument(
        "--owner-record",
        type=Path,
        default=Path.home()
        / "Library/Application Support/io.github.pureliture.harnesskit-verifier/install-owner.json",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=CATALOG_TIMEOUT_SECONDS,
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parse_args(argv)
    port = MacOSInstalledRuntimePort(arguments.evidence_root)
    try:
        report = verify_installed_macos_package(
            app=arguments.app,
            destination=arguments.destination,
            dmg=arguments.dmg,
            evidence_root=arguments.evidence_root,
            owner_record_path=arguments.owner_record,
            package_report=arguments.package_report,
            port=port,
            timeout_seconds=arguments.timeout_seconds,
        )
    except InstalledRuntimeError as error:
        print(
            json.dumps(
                {
                    "error": {
                        "code": error.code,
                        **(
                            {"evidence": error.evidence}
                            if error.evidence is not None
                            else {}
                        ),
                    },
                    "status": "blocked" if error.blocked else "failed",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        restore = getattr(port, "restore_environment", None)
        if callable(restore):
            restore()
    print(
        json.dumps(
            {"report": str(report), "status": "passed"},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
