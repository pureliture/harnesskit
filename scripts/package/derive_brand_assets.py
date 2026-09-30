#!/usr/bin/env python3
"""Derive deterministic HarnessKit brand assets from the approved PNG."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from typing import Any, Protocol


SOURCE_RELATIVE = Path(
    "assets/branding/harness-desktop/source/harness-desktop-cabinet.png"
)
MANIFEST_RELATIVE = Path("assets/branding/harness-desktop/derivation-manifest.json")
SOURCE_SHA256 = "c74519996e2ddefd56a77cb2c17bf6cc38a8ed954966c7a17c63a74bc93b9c5f"
TAURI_CLI_VERSION = "2.11.2"
TAURI_CLI_PACKAGE = f"@tauri-apps/cli@{TAURI_CLI_VERSION}"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_ASSET_BYTES = 8 * 1024 * 1024
ICNS_LAYERS = (
    (b"icp4", "icon_16x16.png"),
    (b"icp5", "icon_32x32.png"),
    (b"icp6", "icon_32x32" "@2x.png"),
    (b"ic07", "icon_128x128.png"),
    (b"ic08", "icon_128x128" "@2x.png"),
    (b"ic09", "icon_256x256" "@2x.png"),
    (b"ic10", "icon_512x512" "@2x.png"),
)


class DerivationError(RuntimeError):
    """Typed brand derivation failure."""


class CommandRunner(Protocol):
    def run(self, argv: list[str], **kwargs: object) -> object: ...


class SubprocessRunner:
    def run(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(argv, check=False, **kwargs)


def fail(code: str) -> None:
    raise DerivationError(code)


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def read_regular(path: Path, code: str) -> bytes:
    try:
        metadata = path.lstat()
        body = path.read_bytes()
    except OSError:
        fail(code)
    if path.is_symlink() or not path.is_file() or len(body) > MAX_ASSET_BYTES:
        fail(code)
    if metadata.st_size != len(body):
        fail(code)
    return body


def png_dimensions(body: bytes, code: str) -> tuple[int, int, int, int, int]:
    if len(body) < 29 or body[:8] != PNG_SIGNATURE or body[12:16] != b"IHDR":
        fail(code)
    width, height, depth, color_type, _compression, _filter, interlace = struct.unpack(
        ">IIBBBBB", body[16:29]
    )
    return width, height, depth, color_type, interlace


def write_regular(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with staging.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        staging.chmod(0o644)
        staging.replace(path)
    finally:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass


def deterministic_icns(iconset: Path) -> bytes:
    chunks: list[bytes] = []
    for kind, filename in ICNS_LAYERS:
        body = read_regular(iconset / filename, "icon_derivation_icns_layer_missing")
        png_dimensions(body, "icon_derivation_icns_layer_invalid")
        chunks.append(kind + struct.pack(">I", len(body) + 8) + body)
    size = 8 + sum(len(chunk) for chunk in chunks)
    return b"icns" + struct.pack(">I", size) + b"".join(chunks)


def command(
    runner: CommandRunner,
    argv: list[str],
    *,
    cwd: Path,
    code: str,
    env: dict[str, str] | None = None,
) -> None:
    try:
        result = runner.run(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10 * 60,
        )
    except (OSError, subprocess.SubprocessError):
        fail(code)
    if getattr(result, "returncode", 1) != 0:
        fail(code)


def output_record(relative: str, body: bytes) -> dict[str, Any]:
    record: dict[str, Any] = {
        "media_type": "image/png" if relative.endswith(".png") else (
            "image/icns" if relative.endswith(".icns") else "image/vnd.microsoft.icon"
        ),
        "path": relative,
        "sha256": sha256_bytes(body),
        "size": len(body),
    }
    if relative.endswith(".png"):
        width, height, _depth, _color_type, _interlace = png_dimensions(
            body, "icon_derivation_output_invalid"
        )
        record["dimensions"] = {"height": height, "width": width}
    return record


def derive_brand_assets(
    repo_root: Path,
    destination_root: Path,
    *,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    destination_root = destination_root.resolve()
    source = repo_root / SOURCE_RELATIVE
    source_bytes = read_regular(source, "brand_source_missing")
    if sha256_bytes(source_bytes) != SOURCE_SHA256:
        fail("brand_source_hash_mismatch")
    if png_dimensions(source_bytes, "brand_source_invalid") != (1254, 1254, 8, 6, 0):
        fail("brand_source_mode_mismatch")

    active_runner = runner or SubprocessRunner()
    with tempfile.TemporaryDirectory(prefix="harness-brand-derivation-") as temporary:
        temporary_root = Path(temporary)
        generated = temporary_root / "tauri-icons"
        npm_cache = temporary_root / "npm-cache"
        npm_cache.mkdir(mode=0o700)
        command(
            active_runner,
            [
                "npx",
                "--yes",
                TAURI_CLI_PACKAGE,
                "icon",
                str(source),
                "--output",
                str(generated),
            ],
            cwd=repo_root,
            code="icon_derivation_tauri_cli_failed",
            env={**os.environ, "npm_config_cache": str(npm_cache)},
        )
        iconset = temporary_root / "normalized.iconset"
        command(
            active_runner,
            [
                "/usr/bin/iconutil",
                "--convert",
                "iconset",
                str(generated / "icon.icns"),
                "--output",
                str(iconset),
            ],
            cwd=repo_root,
            code="icon_derivation_icns_unpack_failed",
        )

        outputs: dict[str, bytes] = {}
        for generated_file in sorted(generated.iterdir(), key=lambda path: path.name):
            if not generated_file.is_file() or generated_file.name == "icon.icns":
                continue
            outputs[f"src-tauri/icons/{generated_file.name}"] = read_regular(
                generated_file, "icon_derivation_output_missing"
            )
        outputs["src-tauri/icons/16x16.png"] = read_regular(
            iconset / "icon_16x16.png", "icon_derivation_output_missing"
        )
        outputs["src-tauri/icons/icon.icns"] = deterministic_icns(iconset)
        outputs["src-frontend/assets/branding/harness-desktop-64.png"] = outputs[
            "src-tauri/icons/64x64.png"
        ]

        records = [output_record(path, body) for path, body in sorted(outputs.items())]
        manifest = {
            "generator": {
                "container_normalizer": "icns-png-chunk-v1",
                "name": "tauri-cli",
                "package": TAURI_CLI_PACKAGE,
                "version": TAURI_CLI_VERSION,
            },
            "operation": "resize_container_only",
            "outputs": records,
            "schema_version": 1,
            "small_size_evidence": [
                {"path": "src-tauri/icons/16x16.png", "size": 16},
                {"path": "src-tauri/icons/32x32.png", "size": 32},
                {"path": "src-tauri/icons/64x64.png", "size": 64},
            ],
            "source": {
                "color_mode": "RGBA",
                "dimensions": {"height": 1254, "width": 1254},
                "media_type": "image/png",
                "path": SOURCE_RELATIVE.as_posix(),
                "sha256": SOURCE_SHA256,
            },
        }
        for relative, body in sorted(outputs.items()):
            write_regular(destination_root / relative, body)
        write_regular(
            destination_root / MANIFEST_RELATIVE,
            (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        )
        return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--destination-root", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    destination = args.destination_root or args.repo_root
    try:
        manifest = derive_brand_assets(args.repo_root, destination)
    except DerivationError as error:
        if args.json:
            print(json.dumps({"code": str(error), "status": "rejected"}, sort_keys=True))
        else:
            print(str(error))
        return 1
    report = {
        "canonical_source_sha256": SOURCE_SHA256,
        "output_count": len(manifest["outputs"]),
        "status": "derived",
    }
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"derived {report['output_count']} brand assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
