#!/usr/bin/env python3
"""Build and qualify one Apple Silicon HarnessKit app+DMG artifact set."""

from __future__ import annotations

import argparse
import binascii
import json
import os
from pathlib import Path
import plistlib
import secrets
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Iterable, Protocol
import zlib

try:
    from . import build_verified_macos_app as app_package
    from .qualify_visual_assets import qualify_visual_assets
except ImportError:
    import build_verified_macos_app as app_package  # type: ignore[no-redef]
    from qualify_visual_assets import qualify_visual_assets  # type: ignore[no-redef]


PRODUCT_NAME = "HarnessKit"
MAIN_BINARY_NAME = "harness-desktop"
BUNDLE_IDENTIFIER = "io.github.pureliture.harnesskit"
TARGET = "aarch64-apple-darwin"
DEPLOYMENT_TARGET = "13.0"
ARTIFACT_STRATEGY = "apple-silicon-arm64-only"
UNSUPPORTED_ARCHITECTURE = "x86_64"
APP_RELATIVE = Path(
    "src-tauri/target/aarch64-apple-darwin/release/bundle/macos/HarnessKit.app"
)
DMG_DIRECTORY_RELATIVE = Path(
    "src-tauri/target/aarch64-apple-darwin/release/bundle/dmg"
)
ICON_SOURCE_RELATIVE = Path(
    "assets/branding/harness-desktop/source/harness-desktop-cabinet.png"
)
ICON_MANIFEST_RELATIVE = Path(
    "assets/branding/harness-desktop/derivation-manifest.json"
)
DMG_LAYOUT_RELATIVE = Path("assets/packaging/macos/dmg-layout-manifest.json")
DMG_BACKGROUND_RELATIVE = Path("assets/packaging/macos/dmg-background.png")
DMG_BACKGROUND_SVG_RELATIVE = Path("assets/packaging/macos/dmg-background.svg")
MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}
FAT_MAGICS = {
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


class CommandRunner(Protocol):
    def run(self, argv: list[str], **kwargs: object) -> object: ...


class SubprocessRunner:
    def run(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(argv, check=False, **kwargs)


def fail(code: str) -> None:
    raise app_package.PackageVerificationError(code)


def run_command(
    runner: CommandRunner,
    argv: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
    code: str,
    timeout: int = 60 * 60,
) -> bytes:
    try:
        result = runner.run(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        fail(code)
    if getattr(result, "returncode", 1) != 0:
        fail(code)
    stdout = getattr(result, "stdout", b"")
    return stdout if isinstance(stdout, bytes) else str(stdout).encode()


def regular_bytes(path: Path, code: str, limit: int = 32 * 1024 * 1024) -> bytes:
    try:
        metadata = path.lstat()
        body = path.read_bytes()
    except OSError:
        fail(code)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or len(body) > limit
    ):
        fail(code)
    return body


def load_json(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(regular_bytes(path, code).decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail(code)
    if not isinstance(value, dict):
        fail(code)
    return value


def sha256_file(path: Path, code: str) -> str:
    return app_package._sha256_bytes(regular_bytes(path, code, 4 * 1024 * 1024 * 1024))


def retina_png_contract(body: bytes) -> dict[str, object]:
    if (
        len(body) < 33
        or body[:8] != b"\x89PNG\r\n\x1a\n"
        or body[12:16] != b"IHDR"
    ):
        fail("package_dmg_background_invalid")
    width, height = struct.unpack(">II", body[16:24])
    density: tuple[int, int, int] | None = None
    offset = 8
    while offset + 12 <= len(body):
        length = struct.unpack(">I", body[offset : offset + 4])[0]
        chunk_type = body[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end + 4 > len(body):
            fail("package_dmg_background_invalid")
        if chunk_type == b"pHYs":
            if length != 9 or density is not None:
                fail("package_dmg_background_invalid")
            density = struct.unpack(">IIB", body[data_start:data_end])
        offset = data_end + 4
    if (width, height) != (1320, 800) or density != (5669, 5669, 1):
        fail("package_dmg_background_invalid")
    return {
        "dpi": {"x": 144, "y": 144},
        "logical_dimensions": {"height": 400, "width": 660},
        "pixel_dimensions": {"height": 800, "width": 1320},
    }


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def rgba_png_pixels(body: bytes, code: str) -> tuple[int, int, bytes]:
    if len(body) < 33 or body[:8] != b"\x89PNG\r\n\x1a\n":
        fail(code)
    offset = 8
    header: tuple[int, int] | None = None
    compressed: list[bytes] = []
    saw_end = False
    while offset + 12 <= len(body):
        length = struct.unpack(">I", body[offset : offset + 4])[0]
        kind = body[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(body):
            fail(code)
        payload = body[data_start:data_end]
        expected_crc = struct.unpack(">I", body[data_end:crc_end])[0]
        if binascii.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            fail(code)
        if kind == b"IHDR":
            if header is not None or length != 13:
                fail(code)
            width, height, depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if (
                width == 0
                or height == 0
                or width > 4096
                or height > 4096
                or (depth, color_type, compression, filtering, interlace)
                != (8, 6, 0, 0, 0)
            ):
                fail(code)
            header = (width, height)
        elif kind == b"IDAT":
            if header is None or saw_end:
                fail(code)
            compressed.append(payload)
        elif kind == b"IEND":
            if length != 0 or header is None or not compressed:
                fail(code)
            saw_end = True
            offset = crc_end
            break
        offset = crc_end
    if not saw_end or offset != len(body) or header is None:
        fail(code)
    width, height = header
    stride = width * 4
    expected_size = height * (stride + 1)
    try:
        decompressor = zlib.decompressobj()
        filtered = decompressor.decompress(b"".join(compressed), expected_size + 1)
        if len(filtered) > expected_size or decompressor.unconsumed_tail:
            fail(code)
        filtered += decompressor.flush(expected_size - len(filtered) + 1)
    except zlib.error:
        fail(code)
    if (
        len(filtered) != expected_size
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        fail(code)

    pixels = bytearray(height * stride)
    source_offset = 0
    previous = bytearray(stride)
    for row_index in range(height):
        filter_type = filtered[source_offset]
        source_offset += 1
        encoded = filtered[source_offset : source_offset + stride]
        source_offset += stride
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - 4] if index >= 4 else 0
            above = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                fail(code)
            row[index] = (value + predictor) & 0xFF
        start = row_index * stride
        pixels[start : start + stride] = row
        previous = row
    return width, height, bytes(pixels)


def fresh_dmg_background_contract(
    repo_root: Path,
    committed_background: bytes,
    runner: CommandRunner,
) -> dict[str, str]:
    source = repo_root / DMG_BACKGROUND_SVG_RELATIVE
    source_body = regular_bytes(source, "package_dmg_background_source_missing")
    with tempfile.TemporaryDirectory(prefix="harness-dmg-background-") as temporary:
        destination = Path(temporary)
        run_command(
            runner,
            [
                "/usr/bin/qlmanage",
                "-t",
                "-s",
                "1320",
                "-o",
                str(destination),
                str(source),
            ],
            cwd=repo_root,
            code="package_dmg_background_renderer_failed",
            timeout=60,
        )
        preview = regular_bytes(
            destination / f"{source.name}.png",
            "package_dmg_background_renderer_failed",
            64 * 1024 * 1024,
        )
    preview_width, preview_height, preview_pixels = rgba_png_pixels(
        preview, "package_dmg_background_renderer_invalid"
    )
    committed_width, committed_height, committed_pixels = rgba_png_pixels(
        committed_background, "package_dmg_background_invalid"
    )
    if (preview_width, preview_height) != (1320, 1320) or (
        committed_width,
        committed_height,
    ) != (1320, 800):
        fail("package_dmg_background_derivation_mismatch")
    cropped_preview = preview_pixels[: 1320 * 800 * 4]
    if cropped_preview != committed_pixels:
        fail("package_dmg_background_derivation_mismatch")
    return {
        "pixel_sha256": app_package._sha256_bytes(committed_pixels),
        "renderer": "macos-quicklook-1320-top-crop-v1",
        "source_sha256": app_package._sha256_bytes(source_body),
    }


def validate_package_inputs(
    repo_root: Path,
    *,
    runner: CommandRunner | None = None,
) -> dict[str, object]:
    visual = qualify_visual_assets(repo_root)
    config = load_json(repo_root / "src-tauri/tauri.conf.json", "package_tauri_config_invalid")
    bundle = config.get("bundle")
    macos = bundle.get("macOS") if isinstance(bundle, dict) else None
    dmg = macos.get("dmg") if isinstance(macos, dict) else None
    if any(
        [
            config.get("productName") != PRODUCT_NAME,
            config.get("mainBinaryName") != MAIN_BINARY_NAME,
            config.get("identifier") != BUNDLE_IDENTIFIER,
            not isinstance(bundle, dict),
            bundle.get("targets") != ["app", "dmg"] if isinstance(bundle, dict) else True,
            not isinstance(macos, dict),
            macos.get("minimumSystemVersion") != DEPLOYMENT_TARGET
            if isinstance(macos, dict)
            else True,
            macos.get("signingIdentity") != "-" if isinstance(macos, dict) else True,
            not isinstance(dmg, dict),
        ]
    ):
        fail("package_tauri_config_invalid")
    layout = load_json(repo_root / DMG_LAYOUT_RELATIVE, "package_dmg_layout_invalid")
    background = regular_bytes(
        repo_root / DMG_BACKGROUND_RELATIVE, "package_dmg_background_missing"
    )
    background_contract = retina_png_contract(background)
    expected_dmg = {
        "background": f"../{DMG_BACKGROUND_RELATIVE.as_posix()}",
        "windowSize": {"height": 400, "width": 660},
        "appPosition": {"x": 180, "y": 190},
        "applicationFolderPosition": {"x": 480, "y": 190},
    }
    if (
        dmg != expected_dmg
        or
        layout.get("schema_version") != 2
        or layout.get("window") != {"height": 400, "width": 660}
        or layout.get("items")
        != {
            "app": {"name": "HarnessKit.app", "x": 180, "y": 190},
            "applications_alias": {
                "name": "Applications",
                "target": "/Applications",
                "x": 480,
                "y": 190,
            },
        }
        or layout.get("instruction")
        != {"text": "HarnessKit을 Applications로 드래그"}
        or layout.get("background")
        != {
            **background_contract,
            "path": DMG_BACKGROUND_RELATIVE.as_posix(),
            "sha256": app_package._sha256_bytes(background),
        }
    ):
        fail("package_dmg_layout_invalid")
    derivation = fresh_dmg_background_contract(
        repo_root,
        background,
        runner or SubprocessRunner(),
    )
    return {**visual, "dmg_background_derivation": derivation}


def clean_dmg_outputs(repo_root: Path) -> None:
    directory = repo_root / DMG_DIRECTORY_RELATIVE
    if not directory.exists():
        return
    if directory.is_symlink() or not directory.is_dir():
        fail("package_critical_path_rejected")
    for child in directory.iterdir():
        if child.name.startswith("HarnessKit") and (
            child.suffix == ".dmg" or child.name.endswith(".dmg.sha256")
        ):
            if child.is_symlink() or not child.is_file():
                fail("package_critical_path_rejected")
            child.unlink()


def find_dmg(repo_root: Path) -> Path:
    directory = repo_root / DMG_DIRECTORY_RELATIVE
    try:
        candidates = sorted(
            path
            for path in directory.iterdir()
            if path.name.startswith("HarnessKit") and path.suffix == ".dmg"
        )
    except OSError:
        fail("package_dmg_missing")
    if len(candidates) != 1:
        fail("package_dmg_missing")
    regular_bytes(candidates[0], "package_dmg_missing", 8 * 1024 * 1024 * 1024)
    return candidates[0]


def recursive_architecture_audit(
    app: Path, runner: CommandRunner, repo_root: Path
) -> list[dict[str, str]]:
    roots = [
        app / "Contents/MacOS",
        app / "Contents/Frameworks",
        app / "Contents/Resources/install-runtime/aarch64-apple-darwin",
    ]
    inspected: list[dict[str, str]] = []
    for root in roots:
        if not root.exists():
            if root.name == "Frameworks":
                continue
            fail("package_architecture_mismatch")
        for current, directories, filenames in os.walk(root, followlinks=False):
            directories.sort()
            filenames.sort()
            current_path = Path(current)
            for name in filenames:
                path = current_path / name
                try:
                    metadata = path.lstat()
                except OSError:
                    fail("package_architecture_mismatch")
                if stat.S_ISLNK(metadata.st_mode):
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    fail("package_architecture_mismatch")
                with path.open("rb") as stream:
                    magic = stream.read(4)
                native_suffix = path.suffix.lower() in {".dylib", ".so"}
                if magic not in MACHO_MAGICS:
                    if native_suffix:
                        fail("package_architecture_mismatch")
                    continue
                if magic in FAT_MAGICS:
                    fail("package_architecture_mismatch")
                stdout = run_command(
                    runner,
                    ["/usr/bin/lipo", "-archs", str(path)],
                    cwd=repo_root,
                    code="package_architecture_mismatch",
                )
                if stdout.decode("utf-8", "strict").strip() != "arm64":
                    fail("package_architecture_mismatch")
                inspected.append(
                    {
                        "architecture": "arm64",
                        "path": path.relative_to(app).as_posix(),
                    }
                )
    required = {
        f"Contents/MacOS/{MAIN_BINARY_NAME}",
        "Contents/Resources/install-runtime/aarch64-apple-darwin/python/bin/python3",
    }
    if not required <= {entry["path"] for entry in inspected}:
        fail("package_architecture_mismatch")
    return inspected


def mounted_dmg_identity(
    repo_root: Path,
    dmg_path: Path,
    standalone_app_manifest_sha256: str,
    runner: CommandRunner,
) -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="harness-dmg-audit-") as temporary:
        mountpoint = Path(temporary) / "mounted"
        mountpoint.mkdir()
        run_command(
            runner,
            [
                "/usr/bin/hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-plist",
                "-mountpoint",
                str(mountpoint),
                str(dmg_path),
            ],
            cwd=repo_root,
            code="package_dmg_mount_failed",
        )
        try:
            contained_app = mountpoint / "HarnessKit.app"
            if contained_app.is_symlink() or not contained_app.is_dir():
                fail("package_app_missing")
            applications = mountpoint / "Applications"
            if not applications.is_symlink() or os.readlink(applications) != "/Applications":
                fail("package_dmg_layout_invalid")
            manifest = app_package._canonical_bundle_manifest(contained_app)
            manifest_bytes = app_package._json_bytes(manifest)
            dmg_contained_app_manifest_sha256 = app_package._sha256_bytes(manifest_bytes)
            if standalone_app_manifest_sha256 != dmg_contained_app_manifest_sha256:
                fail("package_build_identity_mismatch")
            runtime_root = contained_app / "Contents/Resources" / app_package.RUNTIME_RELATIVE
            runtime_manifest_bytes = regular_bytes(
                runtime_root / "runtime-manifest.json", "package_runtime_manifest_mismatch"
            )
            try:
                runtime_manifest = json.loads(runtime_manifest_bytes)
            except (UnicodeError, json.JSONDecodeError):
                fail("package_runtime_manifest_invalid")
            if not isinstance(runtime_manifest, dict) or runtime_manifest.get("target") != TARGET:
                fail("package_runtime_manifest_invalid")
            app_package._verify_runtime_tree(runtime_root, runtime_manifest)
            info = plistlib.loads(
                regular_bytes(
                    contained_app / "Contents/Info.plist",
                    "package_bundle_identity_mismatch",
                )
            )
            if (
                not isinstance(info, dict)
                or info.get("CFBundleIdentifier") != BUNDLE_IDENTIFIER
                or info.get("CFBundleExecutable") != MAIN_BINARY_NAME
            ):
                fail("package_build_identity_mismatch")
            icon_hash = sha256_file(
                contained_app / "Contents/Resources/icon.icns",
                "package_icon_resource_missing",
            )
            return dmg_contained_app_manifest_sha256, icon_hash
        finally:
            run_command(
                runner,
                ["/usr/bin/hdiutil", "detach", str(mountpoint)],
                cwd=repo_root,
                code="package_dmg_detach_failed",
                timeout=60,
            )


def build_verified_macos_package(
    repo_root: Path,
    evidence_root: Path,
    *,
    runner: CommandRunner | None = None,
    build_id: str | None = None,
) -> tuple[Path, Path, Path]:
    repo_root = app_package._lexical_absolute(repo_root)
    app_package._require_directory(repo_root, "package_critical_path_rejected")
    active_runner = runner or SubprocessRunner()
    visual_report = validate_package_inputs(repo_root, runner=active_runner)
    environment = dict(os.environ)
    environment.pop("CARGO_TARGET_DIR", None)
    if environment.get("CI") in {"0", "1"}:
        environment["CI"] = "true" if environment["CI"] == "1" else "false"
    app_package._verify_tauri_cli(repo_root, active_runner, environment)
    build_id = build_id or secrets.token_hex(16)
    app_package._validate_build_id(build_id)
    app_package._build_frontend_bundle(repo_root, active_runner, environment)
    expected_build_inputs = app_package._prepare_build_input_contract(repo_root, build_id)
    build_inputs_sha256 = app_package._sha256_bytes(expected_build_inputs)
    request = app_package._publish_build_request(repo_root, expected_build_inputs)
    environment.update(
        {
            "CARGO_BUILD_TARGET": TARGET,
            "HARNESS_PACKAGE_BUILD_ID": build_id,
            "HARNESS_PACKAGE_BUILD_INPUTS_SHA256": build_inputs_sha256,
            "HARNESS_VERIFIED_PACKAGE_BUILD": "1",
            "MACOSX_DEPLOYMENT_TARGET": DEPLOYMENT_TARGET,
        }
    )
    try:
        clean_dmg_outputs(repo_root)
        run_command(
            active_runner,
            [
                "cargo",
                "tauri",
                "build",
                "--target",
                "aarch64-apple-darwin",
                "--bundles",
                "app,dmg",
            ],
            cwd=repo_root,
            environment=environment,
            code="package_build_failed",
        )
        app = repo_root / APP_RELATIVE
        if app.is_symlink() or not app.is_dir():
            fail("package_app_missing")
        dmg = find_dmg(repo_root)
        app_report_path = app_package.verify_built_macos_app(
            repo_root,
            app,
            evidence_root,
            runner=active_runner,
            build_id=build_id,
            expected_build_inputs=expected_build_inputs,
            expected_app_relative=APP_RELATIVE,
        )
        app_report = load_json(app_report_path, "package_app_report_invalid")
        standalone_manifest_sha256 = str(app_report.get("bundle_manifest_sha256", ""))
        if len(standalone_manifest_sha256) != 64:
            fail("package_app_report_invalid")
        architecture_entries = recursive_architecture_audit(app, active_runner, repo_root)
        packaged_icon_resource_sha256 = sha256_file(
            app / "Contents/Resources/icon.icns", "package_icon_resource_missing"
        )
        (
            dmg_contained_app_manifest_sha256,
            contained_icon_resource_sha256,
        ) = mounted_dmg_identity(
            repo_root,
            dmg,
            standalone_manifest_sha256,
            active_runner,
        )
        if packaged_icon_resource_sha256 != contained_icon_resource_sha256:
            fail("package_build_identity_mismatch")
        report = {
            "artifact_strategy": ARTIFACT_STRATEGY,
            "build_id": build_id,
            "bundle_identifier": BUNDLE_IDENTIFIER,
            "canonical_app_icon_sha256": visual_report[
                "canonical_source_sha256"
            ],
            "derived_icon_manifest_sha256": sha256_file(
                repo_root / ICON_MANIFEST_RELATIVE,
                "package_icon_manifest_missing",
            ),
            "dmg_background_sha256": sha256_file(
                repo_root / DMG_BACKGROUND_RELATIVE,
                "package_dmg_background_missing",
            ),
            "dmg_contained_app_manifest_sha256": dmg_contained_app_manifest_sha256,
            "dmg_layout_manifest_sha256": sha256_file(
                repo_root / DMG_LAYOUT_RELATIVE,
                "package_dmg_layout_invalid",
            ),
            "dmg_sha256": sha256_file(dmg, "package_dmg_missing"),
            "distribution_scope": "local_internal",
            "install_evidence_chain": "NotRun",
            "installed_icon_evidence": "NotRun",
            "installed_runtime_state": "NotRun",
            "native_entries": architecture_entries,
            "notarization_state": "NotConfigured",
            "packaged_icon_resource_sha256": packaged_icon_resource_sha256,
            "product_name": PRODUCT_NAME,
            "schema_version": 1,
            "signing_state": "AdHoc",
            "standalone_app_manifest_sha256": standalone_manifest_sha256,
            "status": "passed",
            "target": TARGET,
        }
        checksum_path = dmg.with_name(f"{dmg.name}.sha256")
        if checksum_path.exists() or checksum_path.is_symlink():
            fail("package_critical_path_rejected")
        app_package._write_evidence(
            checksum_path, f"{report['dmg_sha256']}  {dmg.name}\n".encode("ascii")
        )
        report["dmg_checksum_sha256"] = sha256_file(
            checksum_path, "package_checksum_write_failed"
        )
        evidence = app_package._lexical_absolute(evidence_root) / build_id
        expected_app_report = evidence / "qualification-report.json"
        if app_report_path != expected_app_report.resolve():
            fail("package_app_report_invalid")
        app_package._require_directory(evidence, "package_evidence_directory_failed")
        report_path = evidence / "package-report.json"
        app_package._write_evidence(report_path, app_package._json_bytes(report))
        return app.resolve(), dmg.resolve(), report_path.resolve()
    finally:
        try:
            request.unlink(missing_ok=True)
        except OSError:
            fail("package_build_request_cleanup_failed")


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
        app, dmg, report = build_verified_macos_package(
            args.repo_root, args.evidence_root
        )
    except app_package.PackageVerificationError as error:
        print(error.code, file=sys.stderr)
        return 1
    print(json.dumps({"app": str(app), "dmg": str(dmg), "report": str(report)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
