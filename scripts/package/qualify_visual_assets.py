#!/usr/bin/env python3
"""Fail-closed qualification for HarnessKit brand assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any

try:
    from .derive_brand_assets import (
        DerivationError,
        MANIFEST_RELATIVE,
        SOURCE_RELATIVE,
        SOURCE_SHA256,
        derive_brand_assets,
        png_dimensions,
    )
except ImportError:
    from derive_brand_assets import (  # type: ignore[no-redef]
        DerivationError,
        MANIFEST_RELATIVE,
        SOURCE_RELATIVE,
        SOURCE_SHA256,
        derive_brand_assets,
        png_dimensions,
    )


SHA256 = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER_ICON_HASHES = {
    "1535d355994f8ee82da91384d2a29e04d5b4440f3e6051b5c8b06e7a62283693",
    "2998b41d0c9278b08e8b3874e094920034fe983a46053c9d62764225f58ab7b0",
    "6413bfe9d546e5225fc9256a0ecec48df3a1272ea4bdc91c15b8e000222a95d3",
    "aaa96d0c53b756b77c7f46aeb72290f42256c786c69be68f6bcdeb9c7492a48e",
    "e38ca88e1d5490f3dcbc3c3fa525f7fcb7b80fff3cb2f3a4eb1b2d018c0915c1",
    "eeebf55d8ffe07348786b9057af253e3ca08c56b6a8f8893826381430e3c0b7b",
}


class VisualQualificationError(RuntimeError):
    """Safe-code visual asset qualification failure."""


def fail(code: str) -> None:
    raise VisualQualificationError(code)


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def regular_bytes(path: Path, code: str) -> bytes:
    try:
        metadata = path.lstat()
        body = path.read_bytes()
    except OSError:
        fail(code)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        fail(code)
    return body


def safe_path(repo_root: Path, value: Any, code: str) -> Path:
    if not isinstance(value, str) or not value:
        fail(code)
    pure = PurePosixPath(value)
    if pure.is_absolute() or str(pure) != value or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        fail(code)
    current = repo_root
    for part in pure.parts:
        current = current / part
        try:
            if current.is_symlink():
                fail(code)
        except OSError:
            fail(code)
    return current


def load_manifest(repo_root: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(
            regular_bytes(
                repo_root / MANIFEST_RELATIVE, "brand_derivation_manifest_missing"
            ).decode("utf-8", "strict")
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("brand_derivation_manifest_invalid")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        fail("brand_derivation_manifest_invalid")
    return manifest


def validate_source(repo_root: Path, manifest: dict[str, Any]) -> None:
    body = regular_bytes(repo_root / SOURCE_RELATIVE, "brand_source_missing")
    if sha256(body) != SOURCE_SHA256:
        fail("brand_source_hash_mismatch")
    if png_dimensions(body, "brand_source_invalid") != (1254, 1254, 8, 6, 0):
        fail("brand_source_mode_mismatch")
    if manifest.get("source") != {
        "color_mode": "RGBA",
        "dimensions": {"height": 1254, "width": 1254},
        "media_type": "image/png",
        "path": SOURCE_RELATIVE.as_posix(),
        "sha256": SOURCE_SHA256,
    }:
        fail("brand_derivation_source_mismatch")


def validate_outputs(repo_root: Path, manifest: dict[str, Any]) -> dict[str, str]:
    raw_outputs = manifest.get("outputs")
    if not isinstance(raw_outputs, list) or not raw_outputs:
        fail("brand_output_inventory_invalid")
    hashes: dict[str, str] = {}
    observed_order: list[str] = []
    for record in raw_outputs:
        if not isinstance(record, dict):
            fail("brand_output_inventory_invalid")
        path_value = record.get("path")
        path = safe_path(repo_root, path_value, "brand_output_path_invalid")
        if not isinstance(path_value, str) or path_value in hashes:
            fail("brand_output_inventory_invalid")
        body = regular_bytes(path, "brand_output_missing")
        digest = sha256(body)
        if record.get("sha256") != digest or record.get("size") != len(body):
            fail("brand_output_hash_mismatch")
        if digest in PLACEHOLDER_ICON_HASHES:
            fail("brand_placeholder_detected")
        if path_value.endswith(".png"):
            width, height, _depth, _mode, _interlace = png_dimensions(
                body, "brand_output_media_invalid"
            )
            if record.get("media_type") != "image/png" or record.get(
                "dimensions"
            ) != {"height": height, "width": width}:
                fail("brand_output_media_invalid")
        elif record.get("media_type") not in {
            "image/icns",
            "image/vnd.microsoft.icon",
        }:
            fail("brand_output_media_invalid")
        hashes[path_value] = digest
        observed_order.append(path_value)
    if observed_order != sorted(observed_order):
        fail("brand_output_inventory_invalid")
    return hashes


def validate_placeholder_boundary(repo_root: Path) -> None:
    try:
        shell = regular_bytes(
            repo_root / "src-frontend/app-shell.js", "brand_header_missing"
        ).decode("utf-8", "strict")
    except UnicodeDecodeError:
        fail("brand_header_invalid")
    if re.search(r'class=["\']app-mark["\'][^>]*>\s*H\s*<', shell):
        fail("brand_placeholder_detected")
    brand_image = re.search(
        r'<img(?=[^>]*src=["\']\./assets/branding/harness-desktop-64\.png["\'])'
        r'(?=[^>]*alt=["\']["\'])(?=[^>]*aria-hidden=["\']true["\'])[^>]*>',
        shell,
    )
    if brand_image is None or not re.search(r"<h1>\s*HarnessKit\s*</h1>", shell):
        fail("brand_header_invalid")


def qualify_visual_assets(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    manifest = load_manifest(repo_root)
    validate_source(repo_root, manifest)
    committed_hashes = validate_outputs(repo_root, manifest)
    validate_placeholder_boundary(repo_root)
    with tempfile.TemporaryDirectory(prefix="harness-brand-qualification-") as temporary:
        destination = Path(temporary)
        try:
            fresh_manifest = derive_brand_assets(repo_root, destination)
        except DerivationError as error:
            fail(str(error))
        fresh_hashes = validate_outputs(destination, fresh_manifest)
    if fresh_manifest != manifest or fresh_hashes != committed_hashes:
        fail("brand_fresh_derivation_mismatch")
    return {
        "canonical_source_sha256": SOURCE_SHA256,
        "fresh_derivation_match": True,
        "output_count": len(committed_hashes),
        "placeholder_scan": "passed",
        "status": "qualified",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = qualify_visual_assets(args.repo_root)
    except VisualQualificationError as error:
        report = {"code": str(error), "status": "rejected"}
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print(str(error))
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"qualified {report['output_count']} brand assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
