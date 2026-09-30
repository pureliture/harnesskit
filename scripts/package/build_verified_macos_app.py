from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import plistlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Iterable, Protocol

try:
    from .prepare_install_runtime import _safe_relative, _sha256_bytes, _sha256_file
except ImportError:  # Direct script execution.
    from prepare_install_runtime import _safe_relative, _sha256_bytes, _sha256_file


PRODUCT_NAME = "HarnessKit"
MAIN_BINARY_NAME = "harness-desktop"
CARGO_PACKAGE_NAME = "harness-desktop"
BUNDLE_IDENTIFIER = "io.github.pureliture.harnesskit"
TARGET = "aarch64-apple-darwin"
RUNTIME_RELATIVE = Path("install-runtime") / TARGET
PACKAGE_INPUTS = Path("package-inputs")
FRONTEND_SOURCE_CONTRACT_FILES = (
    "src-frontend/package.json",
    "src-frontend/package-lock.json",
    "src-frontend/build.mjs",
)
FRONTEND_BUNDLE_MANIFEST = Path(
    "src-tauri/target/frontend-dist/bundle-manifest.json"
)
FRONTEND_ENTRYPOINT = "appearance/bootstrap.js"
_IMMUTABLE_GITHUB_COMMIT_SPEC = re.compile(
    r"git\+(?:https://github\.com/|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repository>[A-Za-z0-9_.-]+)"
    r"\.git#(?P<commit>[0-9a-f]{40})\Z",
    re.IGNORECASE,
)
TOOL_IDENTITY_ASSET_FILES = (
    "src-frontend/assets/tool-identities/codex.png",
    "src-frontend/assets/tool-identities/claude.png",
    "src-frontend/assets/tool-identities/antigravity.png",
    "src-frontend/assets/tool-identities/hermesagent.png",
)
TOOL_IDENTITY_MANIFEST = "assets/tool-identities/manifest.json"
BRAND_PACKAGING_FILES = (
    "assets/branding/harness-desktop/BRAND_ASSET_POLICY.md",
    "assets/branding/harness-desktop/source/harness-desktop-cabinet.png",
    "assets/branding/harness-desktop/derivation-manifest.json",
    "assets/packaging/macos/dmg-background.png",
    "assets/packaging/macos/dmg-background.svg",
    "assets/packaging/macos/dmg-layout-manifest.json",
)
ICON_FILES = (
    "32x32.png",
    "128x128.png",
    "128x128" "@2x.png",
    "icon.icns",
    "icon.ico",
)


class PackageVerificationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CommandRunner(Protocol):
    def run(self, argv: list[str], **kwargs: object) -> object: ...


class SubprocessRunner:
    def run(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(argv, check=False, **kwargs)


def _fail(code: str) -> None:
    raise PackageVerificationError(code)


def _require_directory(path: Path, code: str) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        _fail(code)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _fail(code)


def _require_regular(path: Path, code: str, *, executable: bool = False) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        _fail(code)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        _fail(code)
    if executable and metadata.st_mode & 0o111 == 0:
        _fail(code)


def _lexical_absolute(path: Path) -> Path:
    if ".." in path.parts:
        _fail("package_critical_path_rejected")
    return Path(os.path.abspath(path))


def _critical_path(
    root: Path,
    relative: Path,
    *,
    leaf: str,
    leaf_code: str,
    executable: bool = False,
) -> Path:
    if relative.is_absolute() or not relative.parts or any(
        component in {"", ".", ".."} for component in relative.parts
    ):
        _fail("package_critical_path_rejected")
    _require_directory(root, "package_critical_path_rejected")
    current = root
    for index, component in enumerate(relative.parts):
        current = current / component
        last = index == len(relative.parts) - 1
        try:
            metadata = current.lstat()
        except OSError:
            _fail(leaf_code if last else "package_critical_path_rejected")
        if stat.S_ISLNK(metadata.st_mode):
            _fail("package_critical_path_rejected")
        if not last:
            if not stat.S_ISDIR(metadata.st_mode):
                _fail("package_critical_path_rejected")
        elif leaf == "directory":
            if not stat.S_ISDIR(metadata.st_mode):
                _fail(leaf_code)
        elif leaf == "regular":
            if not stat.S_ISREG(metadata.st_mode):
                _fail(leaf_code)
            if executable and metadata.st_mode & 0o111 == 0:
                _fail(leaf_code)
        else:
            raise AssertionError(f"unsupported critical leaf kind: {leaf}")
    return current


def _read_regular(path: Path, code: str, limit: int = 4 * 1024 * 1024) -> bytes:
    _require_regular(path, code)
    try:
        body = path.read_bytes()
    except OSError:
        _fail(code)
    if len(body) > limit:
        _fail(code)
    return body


def _validate_build_id(build_id: str) -> None:
    if len(build_id) != 32 or any(character not in "0123456789abcdef" for character in build_id):
        _fail("package_build_id_invalid")


def _command_result(
    runner: CommandRunner,
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> object:
    return runner.run(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60 * 60,
    )


def _verify_command(result: object, code: str) -> bytes:
    if getattr(result, "returncode", 1) != 0:
        _fail(code)
    stdout = getattr(result, "stdout", b"")
    return stdout if isinstance(stdout, bytes) else str(stdout).encode()


def _verify_tauri_cli(
    repo_root: Path,
    runner: CommandRunner,
    environment: dict[str, str],
) -> None:
    try:
        result = _command_result(
            runner,
            ["cargo", "tauri", "--version"],
            cwd=repo_root,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        _fail("package_tauri_cli_missing")
    _verify_command(result, "package_tauri_cli_missing")


def _build_frontend_bundle(
    repo_root: Path,
    runner: CommandRunner,
    environment: dict[str, str],
) -> None:
    frontend_root = _critical_path(
        repo_root,
        Path("src-frontend"),
        leaf="directory",
        leaf_code="package_frontend_build_failed",
    )
    build_environment = dict(environment)
    build_environment.pop("HARNESS_FRONTEND_DIST", None)
    build_environment.pop("NODE_OPTIONS", None)
    build_environment.update(
        {
            "HARNESS_FRONTEND_DIST": str(
                (repo_root / "src-tauri/target/frontend-dist").resolve()
            ),
            "npm_config_audit": "false",
            "npm_config_cache": str(
                (repo_root / "src-tauri/target/npm-cache").resolve()
            ),
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
        }
    )
    with tempfile.TemporaryDirectory(prefix="harnesskit-frontend-build-") as temporary:
        isolated_frontend = Path(temporary) / "src-frontend"
        try:
            shutil.copytree(
                frontend_root,
                isolated_frontend,
                ignore=shutil.ignore_patterns("node_modules"),
            )
            install_result = _command_result(
                runner,
                ["npm", "ci", "--ignore-scripts", "--prefer-offline"],
                cwd=isolated_frontend,
                env=build_environment,
            )
            _verify_command(install_result, "package_frontend_build_failed")
            build_result = _command_result(
                runner,
                ["npm", "run", "build"],
                cwd=isolated_frontend,
                env=build_environment,
            )
        except (OSError, shutil.Error, subprocess.SubprocessError):
            _fail("package_frontend_build_failed")
        _verify_command(build_result, "package_frontend_build_failed")


def _verify_codesign(app: Path, runner: CommandRunner) -> None:
    result = _command_result(
        runner,
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    _verify_command(result, "package_codesign_failed")


def _verify_arm64(path: Path, runner: CommandRunner, code: str) -> None:
    result = _command_result(
        runner,
        ["/usr/bin/lipo", "-archs", str(path)],
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    output = _verify_command(result, code)
    try:
        architectures = output.decode("ascii", "strict").split()
    except UnicodeDecodeError:
        _fail(code)
    if architectures != ["arm64"]:
        _fail(code)


def _manifest_entry_path(value: object) -> str:
    if not isinstance(value, str):
        _fail("package_runtime_manifest_invalid")
    try:
        return _safe_relative(value).as_posix()
    except RuntimeError:
        _fail("package_runtime_manifest_invalid")


def _verify_runtime_tree(root: Path, manifest: dict[str, object]) -> None:
    _require_directory(root, "package_runtime_tree_mismatch")
    raw_entries = manifest.get("entries")
    if manifest.get("schema_version") != 1 or not isinstance(raw_entries, list):
        _fail("package_runtime_manifest_invalid")
    expected: dict[str, dict[str, object]] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            _fail("package_runtime_manifest_invalid")
        relative = _manifest_entry_path(raw_entry.get("path"))
        if relative in expected:
            _fail("package_runtime_manifest_invalid")
        expected[relative] = raw_entry

    actual: dict[str, Path] = {}
    for path in root.rglob("*"):
        try:
            metadata = path.lstat()
        except OSError:
            _fail("package_runtime_tree_mismatch")
        if stat.S_ISLNK(metadata.st_mode):
            _fail("package_runtime_tree_mismatch")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            _fail("package_runtime_tree_mismatch")
        relative = path.relative_to(root).as_posix()
        if relative != "runtime-manifest.json":
            actual[relative] = path
    if set(actual) != set(expected):
        _fail("package_runtime_tree_mismatch")
    for relative, entry in expected.items():
        path = actual[relative]
        metadata = path.stat()
        if (
            entry.get("sha256") != _sha256_file(path)
            or entry.get("size") != metadata.st_size
            or entry.get("mode") != stat.S_IMODE(metadata.st_mode)
        ):
            _fail("package_runtime_tree_mismatch")


def _canonical_bundle_manifest(root: Path) -> list[dict[str, object]]:
    _require_directory(root, "package_app_missing")
    entries: list[dict[str, object]] = []

    def visit(directory: Path) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            _fail("package_bundle_manifest_failed")
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError:
                _fail("package_bundle_manifest_failed")
            if stat.S_ISLNK(metadata.st_mode):
                try:
                    target = os.readlink(path)
                except OSError:
                    _fail("package_bundle_manifest_failed")
                target_path = PurePosixPath(target)
                if target_path.is_absolute():
                    _fail("package_bundle_symlink_target_rejected")
                stack = list(PurePosixPath(relative).parent.parts)
                for component in target_path.parts:
                    if component in {"", "."}:
                        continue
                    if component == "..":
                        if not stack:
                            _fail("package_bundle_symlink_target_rejected")
                        stack.pop()
                    else:
                        stack.append(component)
                entries.append({"path": relative, "type": "symlink", "target": target})
            elif stat.S_ISDIR(metadata.st_mode):
                entries.append({"path": relative, "type": "directory"})
                visit(path)
            elif stat.S_ISREG(metadata.st_mode):
                # The read-only DMG's HFS copy removes group-write while preserving
                # bytes and executable state. Canonicalize only that filesystem
                # transport difference; every other permission bit remains bound.
                canonical_mode = stat.S_IMODE(metadata.st_mode) & ~stat.S_IWGRP
                entries.append(
                    {
                        "path": relative,
                        "type": "file",
                        "sha256": _sha256_file(path),
                        "size": metadata.st_size,
                        "mode": canonical_mode,
                    }
                )
            else:
                _fail("package_bundle_special_file_rejected")

    visit(root)
    entries.sort(key=lambda entry: str(entry["path"]))
    return entries


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _source_identity_sha256(hashes: dict[str, str]) -> str:
    body = "".join(f"{key}={hashes[key]}\n" for key in sorted(hashes)).encode()
    return _sha256_bytes(body)


def _frontend_bundle_member(
    dist_root: Path,
    value: object,
) -> tuple[Path, str]:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        _fail("package_frontend_bundle_invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(
        component in {"", ".", ".."} for component in relative.parts
    ):
        _fail("package_frontend_bundle_invalid")
    path = _critical_path(
        dist_root,
        Path(*relative.parts),
        leaf="regular",
        leaf_code="package_frontend_bundle_invalid",
    )
    return path, relative.as_posix()


def _frontend_lock_inventory(packages: dict[object, object]) -> list[dict[str, object]]:
    inventory: list[dict[str, object]] = []
    for package_path, resolution in packages.items():
        if package_path == "":
            continue
        if (
            not isinstance(package_path, str)
            or not isinstance(resolution, dict)
            or "\\" in package_path
            or ":" in package_path
        ):
            _fail("package_frontend_bundle_invalid")
        relative = PurePosixPath(package_path)
        parts = relative.parts
        if (
            relative.is_absolute()
            or not parts
            or parts[0] != "node_modules"
            or any(part in {"", ".", ".."} for part in parts)
            or "node_modules" not in parts
        ):
            _fail("package_frontend_bundle_invalid")
        last_node_modules = max(
            index for index, part in enumerate(parts) if part == "node_modules"
        )
        leaf = parts[last_node_modules + 1 :]
        if len(leaf) == 1 and not leaf[0].startswith("@"):
            name = leaf[0]
        elif len(leaf) == 2 and leaf[0].startswith("@") and len(leaf[0]) > 1:
            name = "/".join(leaf)
        else:
            _fail("package_frontend_bundle_invalid")

        version = resolution.get("version")
        integrity = resolution.get("integrity")
        license_name = resolution.get("license")
        if (
            not isinstance(version, str)
            or not version
            or not isinstance(integrity, str)
            or not integrity.startswith("sha512-")
            or not isinstance(license_name, str)
            or not license_name
        ):
            _fail("package_frontend_bundle_invalid")
        try:
            digest = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
        except (ValueError, binascii.Error):
            _fail("package_frontend_bundle_invalid")
        if len(digest) != 64:
            _fail("package_frontend_bundle_invalid")
        inventory.append(
            {
                "integrity": integrity,
                "license": license_name,
                "name": name,
                "path": package_path,
                "usage": "build" if resolution.get("dev") is True else "runtime",
                "version": version,
            }
        )
    if not inventory:
        _fail("package_frontend_bundle_invalid")
    return sorted(
        inventory,
        key=lambda item: (str(item["name"]), str(item["version"]), str(item["path"])),
    )


def _frontend_dependency_matches_lock(
    name: object,
    declared: object,
    packages: dict[object, object],
) -> bool:
    if not isinstance(name, str) or not isinstance(declared, str):
        return False
    resolution = packages.get(f"node_modules/{name}")
    if not isinstance(resolution, dict):
        return False
    declared_match = _IMMUTABLE_GITHUB_COMMIT_SPEC.fullmatch(declared)
    if declared_match:
        resolved = resolution.get("resolved")
        resolved_match = (
            _IMMUTABLE_GITHUB_COMMIT_SPEC.fullmatch(resolved)
            if isinstance(resolved, str)
            else None
        )
        return (
            resolved_match is not None
            and resolved_match.group("owner").lower()
            == declared_match.group("owner").lower()
            and resolved_match.group("repository").lower()
            == declared_match.group("repository").lower()
            and resolved_match.group("commit").lower()
            == declared_match.group("commit").lower()
        )
    return resolution.get("version") == declared


def _frontend_contract_members(repo_root: Path) -> dict[str, tuple[Path, bytes]]:
    package_path = _critical_path(
        repo_root,
        Path("src-frontend/package.json"),
        leaf="regular",
        leaf_code="package_frontend_bundle_invalid",
    )
    lock_path = _critical_path(
        repo_root,
        Path("src-frontend/package-lock.json"),
        leaf="regular",
        leaf_code="package_frontend_bundle_invalid",
    )
    build_path = _critical_path(
        repo_root,
        Path("src-frontend/build.mjs"),
        leaf="regular",
        leaf_code="package_frontend_bundle_invalid",
    )
    dist_root = _critical_path(
        repo_root,
        Path("src-tauri/target/frontend-dist"),
        leaf="directory",
        leaf_code="package_frontend_bundle_invalid",
    )
    manifest_path = _critical_path(
        repo_root,
        FRONTEND_BUNDLE_MANIFEST,
        leaf="regular",
        leaf_code="package_frontend_bundle_invalid",
    )
    package_body = _read_regular(package_path, "package_frontend_bundle_invalid")
    lock_body = _read_regular(lock_path, "package_frontend_bundle_invalid")
    build_body = _read_regular(build_path, "package_frontend_bundle_invalid")
    manifest_body = _read_regular(manifest_path, "package_frontend_bundle_invalid")
    try:
        package = json.loads(package_body)
        lock = json.loads(lock_body)
        manifest = json.loads(manifest_body)
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        _fail("package_frontend_bundle_invalid")
    if not all(isinstance(value, dict) for value in (package, lock, manifest)):
        _fail("package_frontend_bundle_invalid")

    scripts = package.get("scripts")
    dependencies = package.get("dependencies")
    dev_dependencies = package.get("devDependencies")
    packages = lock.get("packages")
    lock_root = packages.get("") if isinstance(packages, dict) else None
    if any(
        [
            package.get("private") is not True,
            package.get("type") != "module",
            not isinstance(scripts, dict),
            scripts.get("build") != "node build.mjs"
            if isinstance(scripts, dict)
            else True,
            not isinstance(dependencies, dict),
            not isinstance(dev_dependencies, dict),
            lock.get("lockfileVersion") != 3,
            not isinstance(lock_root, dict),
        ]
    ):
        _fail("package_frontend_bundle_invalid")
    assert isinstance(dependencies, dict)
    assert isinstance(dev_dependencies, dict)
    assert isinstance(packages, dict)
    assert isinstance(lock_root, dict)
    if (
        lock_root.get("dependencies") != dependencies
        or lock_root.get("devDependencies") != dev_dependencies
        or any(
            not _frontend_dependency_matches_lock(name, declared, packages)
            for name, declared in {**dependencies, **dev_dependencies}.items()
        )
    ):
        _fail("package_frontend_bundle_invalid")

    if (
        manifest.get("schema_version") != 1
        or manifest.get("entrypoint") != FRONTEND_ENTRYPOINT
        or manifest.get("source_map") is not False
    ):
        _fail("package_frontend_bundle_invalid")
    lockfile = manifest.get("lockfile")
    output = manifest.get("output")
    license_inventory = manifest.get("license_inventory")
    licenses = manifest.get("licenses")
    files = manifest.get("files")
    if not all(
        isinstance(value, dict) for value in (lockfile, output, license_inventory)
    ) or not isinstance(licenses, list) or not isinstance(files, list):
        _fail("package_frontend_bundle_invalid")
    assert isinstance(lockfile, dict)
    assert isinstance(output, dict)
    assert isinstance(license_inventory, dict)
    if (
        lockfile.get("path") != "package-lock.json"
        or lockfile.get("sha256") != _sha256_bytes(lock_body)
    ):
        _fail("package_frontend_bundle_invalid")

    output_path, output_relative = _frontend_bundle_member(
        dist_root, output.get("path")
    )
    output_body = _read_regular(
        output_path, "package_frontend_bundle_invalid", 16 * 1024 * 1024
    )
    if (
        output_relative != "assets/app.bundle.js"
        or output.get("sha256") != _sha256_bytes(output_body)
    ):
        _fail("package_frontend_bundle_invalid")
    license_path, _license_relative = _frontend_bundle_member(
        dist_root, license_inventory.get("path")
    )
    license_body = _read_regular(license_path, "package_frontend_bundle_invalid")
    if (
        _license_relative != "license-inventory.json"
        or license_inventory.get("sha256") != _sha256_bytes(license_body)
    ):
        _fail("package_frontend_bundle_invalid")
    try:
        inventory = json.loads(license_body)
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        _fail("package_frontend_bundle_invalid")
    if (
        not isinstance(inventory, dict)
        or inventory.get("schema_version") != 1
        or inventory.get("packages") != licenses
    ):
        _fail("package_frontend_bundle_invalid")

    if licenses != _frontend_lock_inventory(packages):
        _fail("package_frontend_bundle_invalid")

    expected_files: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict):
            _fail("package_frontend_bundle_invalid")
        member_path, relative = _frontend_bundle_member(dist_root, item.get("path"))
        expected_hash = item.get("sha256")
        if (
            relative in expected_files
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(character not in "0123456789abcdef" for character in expected_hash)
            or _sha256_bytes(
                _read_regular(
                    member_path,
                    "package_frontend_bundle_invalid",
                    16 * 1024 * 1024,
                )
            )
            != expected_hash
        ):
            _fail("package_frontend_bundle_invalid")
        expected_files[relative] = expected_hash
    if (
        expected_files.get(output_relative) != output.get("sha256")
        or expected_files.get(_license_relative)
        != license_inventory.get("sha256")
    ):
        _fail("package_frontend_bundle_invalid")

    javascript_files: list[str] = []
    actual_files: set[str] = set()
    try:
        candidates = sorted(dist_root.rglob("*"))
    except OSError:
        _fail("package_frontend_bundle_invalid")
    for candidate in candidates:
        try:
            metadata = candidate.lstat()
        except OSError:
            _fail("package_frontend_bundle_invalid")
        if stat.S_ISLNK(metadata.st_mode):
            _fail("package_critical_path_rejected")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            _fail("package_frontend_bundle_invalid")
        relative = candidate.relative_to(dist_root).as_posix()
        actual_files.add(relative)
        if relative.endswith(".js"):
            javascript_files.append(relative)
        if relative.endswith(".map") or "node_modules" in PurePosixPath(relative).parts:
            _fail("package_frontend_bundle_invalid")
    if actual_files - {"bundle-manifest.json"} != set(expected_files) or (
        javascript_files != [output_relative]
    ):
        _fail("package_frontend_bundle_invalid")

    return {
        "frontend_package_json_sha256": (package_path, package_body),
        "frontend_package_lock_sha256": (lock_path, lock_body),
        "frontend_build_script_sha256": (build_path, build_body),
        "frontend_bundle_manifest_sha256": (manifest_path, manifest_body),
        "frontend_bundle_output_sha256": (output_path, output_body),
        "frontend_license_inventory_sha256": (license_path, license_body),
    }


def _app_source_manifest_sha256(
    repo_root: Path,
    frontend_members: dict[str, tuple[Path, bytes]],
) -> str:
    entries: dict[str, tuple[int, str]] = {}

    def add(path: Path) -> None:
        relative = path.relative_to(repo_root).as_posix()
        body = _read_regular(path, "package_source_identity_invalid")
        entries[relative] = (len(body), _sha256_bytes(body))

    def add_verified(path: Path, body: bytes) -> None:
        relative = path.relative_to(repo_root).as_posix()
        entries[relative] = (len(body), _sha256_bytes(body))

    def visit(directory: Path) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda child: child.name)
        except OSError:
            _fail("package_source_identity_invalid")
        for child in children:
            path = Path(child.path)
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError:
                _fail("package_source_identity_invalid")
            if stat.S_ISLNK(metadata.st_mode):
                _fail("package_critical_path_rejected")
            if stat.S_ISDIR(metadata.st_mode):
                visit(path)
            elif stat.S_ISREG(metadata.st_mode):
                add(path)
            else:
                _fail("package_source_identity_invalid")

    def add_optional(relative: str) -> None:
        path = repo_root
        parts = Path(relative).parts
        if not parts or Path(relative).is_absolute() or any(part in {"", ".", ".."} for part in parts):
            _fail("package_critical_path_rejected")
        for index, part in enumerate(parts):
            path = path / part
            try:
                metadata = path.lstat()
            except OSError:
                return
            if stat.S_ISLNK(metadata.st_mode):
                _fail("package_critical_path_rejected")
            if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
                _fail("package_critical_path_rejected")
        if not stat.S_ISREG(metadata.st_mode):
            return
        add(path)

    add(
        _critical_path(
            repo_root,
            Path("src-tauri/build.rs"),
            leaf="regular",
            leaf_code="package_source_identity_invalid",
        )
    )
    for relative in BRAND_PACKAGING_FILES:
        add(
            _critical_path(
                repo_root,
                Path(relative),
                leaf="regular",
                leaf_code="package_source_identity_invalid",
            )
        )
    add(
        _critical_path(
            repo_root,
            Path(TOOL_IDENTITY_MANIFEST),
            leaf="regular",
            leaf_code="package_source_identity_invalid",
        )
    )
    for relative in ("src-tauri/src", "src-tauri/build_support", "src-tauri/capabilities"):
        visit(
            _critical_path(
                repo_root,
                Path(relative),
                leaf="directory",
                leaf_code="package_source_identity_invalid",
            )
        )
    for path, body in frontend_members.values():
        add_verified(path, body)
    visit(
        _critical_path(
            repo_root,
            Path("src-tauri/target/frontend-dist"),
            leaf="directory",
            leaf_code="package_source_identity_invalid",
        )
    )
    for relative in TOOL_IDENTITY_ASSET_FILES:
        add_optional(relative)
    for relative in ICON_FILES:
        add(
            _critical_path(
                repo_root,
                Path("src-tauri/icons") / relative,
                leaf="regular",
                leaf_code="package_source_identity_invalid",
            )
        )
    body = "".join(
        f"{relative}\0{entries[relative][0]}\0{entries[relative][1]}\n"
        for relative in sorted(entries)
    ).encode("utf-8")
    return _sha256_bytes(body)


def _prepare_build_input_contract(repo_root: Path, build_id: str) -> bytes:
    _validate_build_id(build_id)
    repo_root = _lexical_absolute(repo_root)
    _require_directory(repo_root, "package_critical_path_rejected")
    frontend_members = _frontend_contract_members(repo_root)
    source_paths = {
        "cargo_lock_sha256": Path("src-tauri/Cargo.lock"),
        "cargo_toml_sha256": Path("src-tauri/Cargo.toml"),
        "runtime_manifest_sha256": Path(
            "src-tauri/target/install-runtime"
        )
        / TARGET
        / "runtime-manifest.json",
        "target_contract_sha256": Path("schemas/install-target-contract-v1.json"),
        "tauri_config_sha256": Path("src-tauri/tauri.conf.json"),
    }
    source_bodies: dict[str, bytes] = {}
    for key, relative in source_paths.items():
        path = _critical_path(
            repo_root,
            relative,
            leaf="regular",
            leaf_code="package_source_identity_invalid",
        )
        source_bodies[key] = _read_regular(path, "package_source_identity_invalid")
    hashes = {key: _sha256_bytes(body) for key, body in source_bodies.items()}
    hashes.update(
        {
            key: _sha256_bytes(body)
            for key, (_path, body) in frontend_members.items()
        }
    )
    hashes["app_source_manifest_sha256"] = _app_source_manifest_sha256(
        repo_root, frontend_members
    )

    try:
        tauri_config = json.loads(source_bodies["tauri_config_sha256"])
        cargo_toml = tomllib.loads(source_bodies["cargo_toml_sha256"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError, tomllib.TOMLDecodeError):
        _fail("package_source_identity_invalid")
    if not isinstance(tauri_config, dict) or not isinstance(cargo_toml, dict):
        _fail("package_source_identity_invalid")
    cargo_package = cargo_toml.get("package")
    if not isinstance(cargo_package, dict):
        _fail("package_source_identity_invalid")
    version = cargo_package.get("version")
    app_config = tauri_config.get("app")
    windows = app_config.get("windows") if isinstance(app_config, dict) else None
    main_windows = (
        [window for window in windows if isinstance(window, dict) and window.get("label") == "main"]
        if isinstance(windows, list)
        else []
    )
    main_window_title_valid = (
        len(main_windows) == 1 and main_windows[0].get("title") == PRODUCT_NAME
    )
    if any(
        [
            tauri_config.get("productName") != PRODUCT_NAME,
            tauri_config.get("mainBinaryName") != MAIN_BINARY_NAME,
            tauri_config.get("identifier") != BUNDLE_IDENTIFIER,
            not main_window_title_valid,
            not isinstance(version, str),
            tauri_config.get("version") != version,
            cargo_package.get("name") != CARGO_PACKAGE_NAME,
        ]
    ):
        _fail("package_source_identity_invalid")
    try:
        pin = _critical_path(
            repo_root,
            Path("src-tauri/install-runtime.manifest.sha256"),
            leaf="regular",
            leaf_code="package_runtime_manifest_mismatch",
        ).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        _fail("package_runtime_manifest_mismatch")
    if pin != hashes["runtime_manifest_sha256"]:
        _fail("package_runtime_manifest_mismatch")

    return _json_bytes(
        {
            "build_id": build_id,
            "app_source_manifest_sha256": hashes["app_source_manifest_sha256"],
            "bundle_identifier": BUNDLE_IDENTIFIER,
            "cargo_lock_sha256": hashes["cargo_lock_sha256"],
            "cargo_toml_sha256": hashes["cargo_toml_sha256"],
            "frontend_build_script_sha256": hashes[
                "frontend_build_script_sha256"
            ],
            "frontend_bundle_manifest_sha256": hashes[
                "frontend_bundle_manifest_sha256"
            ],
            "frontend_bundle_output_sha256": hashes[
                "frontend_bundle_output_sha256"
            ],
            "frontend_license_inventory_sha256": hashes[
                "frontend_license_inventory_sha256"
            ],
            "frontend_package_json_sha256": hashes[
                "frontend_package_json_sha256"
            ],
            "frontend_package_lock_sha256": hashes[
                "frontend_package_lock_sha256"
            ],
            "product_name": PRODUCT_NAME,
            "profile": "release",
            "runtime_manifest_sha256": hashes["runtime_manifest_sha256"],
            "schema_version": 1,
            "source_identity_sha256": _source_identity_sha256(hashes),
            "target": TARGET,
            "target_contract_sha256": hashes["target_contract_sha256"],
            "tauri_config_sha256": hashes["tauri_config_sha256"],
            "version": version,
        }
    )


def _publish_build_request(repo_root: Path, body: bytes) -> Path:
    target = _critical_path(
        repo_root,
        Path("src-tauri/target"),
        leaf="directory",
        leaf_code="package_critical_path_rejected",
    )
    request = target / "package-build-request.json"
    staging = target / f".package-build-request-{secrets.token_hex(8)}.tmp"
    try:
        with staging.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        staging.replace(request)
    except OSError:
        if staging.exists():
            staging.unlink()
        _fail("package_build_request_write_failed")
    return request


def _binary_build_marker(build_id: str, build_inputs_sha256: str) -> bytes:
    return (
        f"HARNESS_PACKAGE_BUILD_ID={build_id}\n"
        f"HARNESS_PACKAGE_BUILD_INPUTS_SHA256={build_inputs_sha256}\n"
    ).encode("ascii")


def _require_file_contains(path: Path, marker: bytes, code: str) -> None:
    _require_regular(path, code, executable=True)
    overlap = b""
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                window = overlap + chunk
                if marker in window:
                    return
                overlap = window[-max(len(marker) - 1, 0) :]
    except OSError:
        _fail(code)
    _fail(code)


def _write_evidence(path: Path, body: bytes) -> None:
    staging = path.with_name(f".{path.name}.tmp")
    try:
        with staging.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        staging.replace(path)
    except OSError:
        if staging.exists():
            staging.unlink()
        _fail("package_evidence_write_failed")


def _evidence_directory(repo_root: Path, evidence_root: Path, build_id: str) -> Path:
    repo = repo_root.resolve()
    root = evidence_root.resolve()
    if root == repo or root.is_relative_to(repo):
        _fail("package_evidence_must_be_external")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        run = root / build_id
        run.mkdir(mode=0o700)
    except OSError:
        _fail("package_evidence_directory_failed")
    return run.resolve()


def verify_built_macos_app(
    repo_root: Path,
    app: Path,
    evidence_root: Path,
    *,
    runner: CommandRunner,
    build_id: str,
    expected_build_inputs: bytes,
    expected_app_relative: Path | None = None,
) -> Path:
    repo_root = _lexical_absolute(repo_root)
    _require_directory(repo_root, "package_critical_path_rejected")
    expected_app_relative = expected_app_relative or (
        Path("src-tauri/target/release/bundle/macos") / f"{PRODUCT_NAME}.app"
    )
    if expected_app_relative.is_absolute() or any(
        component in {"", ".", ".."} for component in expected_app_relative.parts
    ):
        _fail("package_app_path_mismatch")
    expected_app = repo_root / expected_app_relative
    app = _lexical_absolute(app)
    if app != expected_app:
        _fail("package_app_path_mismatch")
    app = _critical_path(
        repo_root,
        expected_app_relative,
        leaf="directory",
        leaf_code="package_app_missing",
    )
    _validate_build_id(build_id)
    current_build_inputs = _prepare_build_input_contract(repo_root, build_id)
    if current_build_inputs != expected_build_inputs:
        _fail("package_source_identity_changed")
    build_inputs_sha256 = _sha256_bytes(expected_build_inputs)

    main = _critical_path(
        app,
        Path("Contents/MacOS") / MAIN_BINARY_NAME,
        leaf="regular",
        leaf_code="package_main_executable_missing",
        executable=True,
    )
    info_plist = _critical_path(
        app,
        Path("Contents/Info.plist"),
        leaf="regular",
        leaf_code="package_bundle_identity_mismatch",
    )
    _critical_path(
        app,
        Path("Contents/Resources"),
        leaf="directory",
        leaf_code="package_critical_path_rejected",
    )
    bundled_runtime = _critical_path(
        app,
        Path("Contents/Resources") / RUNTIME_RELATIVE,
        leaf="directory",
        leaf_code="package_runtime_tree_mismatch",
    )
    bundled_python = _critical_path(
        app,
        Path("Contents/Resources") / RUNTIME_RELATIVE / "python/bin/python3",
        leaf="regular",
        leaf_code="package_python_missing",
        executable=True,
    )

    prepared_runtime = _critical_path(
        repo_root,
        Path("src-tauri/target/install-runtime") / TARGET,
        leaf="directory",
        leaf_code="package_runtime_tree_mismatch",
    )
    prepared_manifest_path = _critical_path(
        repo_root,
        Path("src-tauri/target/install-runtime") / TARGET / "runtime-manifest.json",
        leaf="regular",
        leaf_code="package_runtime_manifest_mismatch",
    )
    bundled_manifest_path = _critical_path(
        app,
        Path("Contents/Resources") / RUNTIME_RELATIVE / "runtime-manifest.json",
        leaf="regular",
        leaf_code="package_runtime_manifest_mismatch",
    )
    prepared_manifest_bytes = _read_regular(
        prepared_manifest_path, "package_runtime_manifest_mismatch"
    )
    bundled_manifest_bytes = _read_regular(
        bundled_manifest_path, "package_runtime_manifest_mismatch"
    )
    try:
        pin = _read_regular(
            _critical_path(
                repo_root,
                Path("src-tauri/install-runtime.manifest.sha256"),
                leaf="regular",
                leaf_code="package_runtime_manifest_mismatch",
            ),
            "package_runtime_manifest_mismatch",
            1024,
        ).decode("ascii", "strict").strip()
    except UnicodeDecodeError:
        _fail("package_runtime_manifest_mismatch")
    manifest_hash = _sha256_bytes(prepared_manifest_bytes)
    if bundled_manifest_bytes != prepared_manifest_bytes or pin != manifest_hash:
        _fail("package_runtime_manifest_mismatch")
    try:
        runtime_manifest = json.loads(prepared_manifest_bytes)
    except (TypeError, ValueError, json.JSONDecodeError):
        _fail("package_runtime_manifest_invalid")
    if not isinstance(runtime_manifest, dict) or runtime_manifest.get("target") != TARGET:
        _fail("package_runtime_manifest_invalid")
    _verify_runtime_tree(prepared_runtime, runtime_manifest)
    _verify_runtime_tree(bundled_runtime, runtime_manifest)

    source_contract = _read_regular(
        _critical_path(
            repo_root,
            Path("schemas/install-target-contract-v1.json"),
            leaf="regular",
            leaf_code="package_target_contract_mismatch",
        ),
        "package_target_contract_mismatch",
    )
    bundled_contract = _read_regular(
        _critical_path(
            app,
            Path("Contents/Resources")
            / PACKAGE_INPUTS
            / "install-target-contract-v1.json",
            leaf="regular",
            leaf_code="package_target_contract_mismatch",
        ),
        "package_target_contract_mismatch",
    )
    if source_contract != bundled_contract:
        _fail("package_target_contract_mismatch")

    bundled_build_inputs = _read_regular(
        _critical_path(
            app,
            Path("Contents/Resources") / PACKAGE_INPUTS / "package-build-inputs.json",
            leaf="regular",
            leaf_code="package_build_inputs_invalid",
        ),
        "package_build_inputs_invalid",
    )
    if bundled_build_inputs != expected_build_inputs:
        _fail("package_build_inputs_mismatch")
    try:
        build_inputs = json.loads(expected_build_inputs)
    except (TypeError, ValueError, json.JSONDecodeError):
        _fail("package_build_inputs_invalid")
    if not isinstance(build_inputs, dict):
        _fail("package_build_inputs_invalid")
    expected_keys = {
        "app_source_manifest_sha256",
        "build_id",
        "bundle_identifier",
        "cargo_lock_sha256",
        "cargo_toml_sha256",
        "frontend_build_script_sha256",
        "frontend_bundle_manifest_sha256",
        "frontend_bundle_output_sha256",
        "frontend_license_inventory_sha256",
        "frontend_package_json_sha256",
        "frontend_package_lock_sha256",
        "product_name",
        "profile",
        "runtime_manifest_sha256",
        "schema_version",
        "source_identity_sha256",
        "target",
        "target_contract_sha256",
        "tauri_config_sha256",
        "version",
    }
    if set(build_inputs) != expected_keys or any(
        [
            build_inputs.get("schema_version") != 1,
            build_inputs.get("build_id") != build_id,
            build_inputs.get("product_name") != PRODUCT_NAME,
            build_inputs.get("bundle_identifier") != BUNDLE_IDENTIFIER,
            build_inputs.get("target") != TARGET,
            build_inputs.get("profile") != "release",
            build_inputs.get("runtime_manifest_sha256") != manifest_hash,
            build_inputs.get("target_contract_sha256") != _sha256_bytes(source_contract),
            not isinstance(build_inputs.get("version"), str),
        ]
    ):
        _fail("package_build_inputs_mismatch")

    try:
        info = plistlib.loads(_read_regular(info_plist, "package_bundle_identity_mismatch"))
    except (plistlib.InvalidFileException, ValueError, TypeError):
        _fail("package_bundle_identity_mismatch")
    if not isinstance(info, dict) or any(
        [
            info.get("CFBundleIdentifier") != BUNDLE_IDENTIFIER,
            info.get("CFBundleExecutable") != MAIN_BINARY_NAME,
            info.get("CFBundleDisplayName") != PRODUCT_NAME,
            info.get("CFBundleName") != PRODUCT_NAME,
            info.get("CFBundleShortVersionString") != build_inputs.get("version"),
        ]
    ):
        _fail("package_bundle_identity_mismatch")
    _require_file_contains(
        main,
        _binary_build_marker(build_id, build_inputs_sha256),
        "package_binary_build_identity_mismatch",
    )
    _verify_arm64(main, runner, "package_main_architecture_mismatch")
    _verify_arm64(bundled_python, runner, "package_python_architecture_mismatch")
    _verify_codesign(app, runner)

    manifest = _canonical_bundle_manifest(app)
    manifest_bytes = _json_bytes(manifest)
    report = {
        "schema_version": 1,
        "status": "passed",
        "claim_scope": "official_wrapper_pipeline",
        "build_identity": build_inputs,
        "build_inputs_sha256": build_inputs_sha256,
        "qualification": {
            "binary_build_identity": "verified",
            "build_input_contract": "verified",
            "bundle_identity": "verified",
            "codesign": "verified",
            "official_wrapper_pipeline": "verified",
            "main_executable_architecture": "arm64",
            "python_architecture": "arm64",
            "runtime_tree": "verified",
            "embedded_inputs": "verified",
        },
        "bundle_manifest_sha256": _sha256_bytes(manifest_bytes),
    }
    evidence = _evidence_directory(repo_root, evidence_root, build_id)
    _write_evidence(evidence / "bundle-manifest.json", manifest_bytes)
    report_path = evidence / "qualification-report.json"
    _write_evidence(report_path, _json_bytes(report))
    return report_path.resolve()


def build_verified_macos_app(
    repo_root: Path,
    evidence_root: Path,
    *,
    runner: CommandRunner | None = None,
    build_id: str | None = None,
) -> tuple[Path, Path]:
    repo_root = _lexical_absolute(repo_root)
    _require_directory(repo_root, "package_critical_path_rejected")
    runner = runner or SubprocessRunner()
    environment = dict(os.environ)
    environment.pop("CARGO_TARGET_DIR", None)
    _verify_tauri_cli(repo_root, runner, environment)
    build_id = build_id or secrets.token_hex(16)
    _validate_build_id(build_id)
    _build_frontend_bundle(repo_root, runner, environment)
    expected_build_inputs = _prepare_build_input_contract(repo_root, build_id)
    build_inputs_sha256 = _sha256_bytes(expected_build_inputs)
    request = _publish_build_request(repo_root, expected_build_inputs)
    environment["HARNESS_VERIFIED_PACKAGE_BUILD"] = "1"
    environment["HARNESS_PACKAGE_BUILD_ID"] = build_id
    environment["HARNESS_PACKAGE_BUILD_INPUTS_SHA256"] = build_inputs_sha256
    try:
        result = _command_result(
            runner,
            ["cargo", "tauri", "build", "--bundles", "app"],
            cwd=repo_root,
            env=environment,
        )
        _verify_command(result, "package_build_failed")
        app = (
            repo_root
            / "src-tauri/target/release/bundle/macos"
            / f"{PRODUCT_NAME}.app"
        )
        report = verify_built_macos_app(
            repo_root,
            app,
            evidence_root,
            runner=runner,
            build_id=build_id,
            expected_build_inputs=expected_build_inputs,
        )
        return app, report
    finally:
        try:
            request.unlink(missing_ok=True)
        except OSError:
            _fail("package_build_request_cleanup_failed")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path(tempfile.gettempdir()) / "harness-desktop-package-evidence",
    )
    args = parser.parse_args(argv)
    try:
        app, _report = build_verified_macos_app(args.repo_root, args.evidence_root)
    except PackageVerificationError as error:
        print(error.code, file=sys.stderr)
        return 1
    print(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
