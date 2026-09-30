#!/usr/bin/env python3
"""Compile a fail-closed manifest into per-tool qualified or TextOnly runtime entries."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
from typing import Any


ALLOWED_CONTEXTS = {
    "install_preview",
    "install_result",
    "install_selected_target",
    "local_result_badge",
    "local_selected_inspector",
    "local_tool_filter",
    "sot_target",
}
MANIFEST_KEYS = {"schema_version", "assets"}
ASSET_KEYS = {
    "asset_id",
    "canonical_tool_id",
    "aliases",
    "display_name",
    "sourceUrl",
    "repository_path",
    "bundled_relative_url",
    "source",
    "repository_sha256",
    "media_type",
    "dimensions",
    "derivation",
    "copyright_status",
    "license_status",
    "trademark_review_status",
    "allowed_usage_scope",
    "product_use_approval",
    "attribution_notice",
    "modification_constraint",
}
SOURCE_KEYS = {"kind", "source_sha256"}
DIMENSION_KEYS = {"width", "height"}
APPROVAL_KEYS = {"status", "authority", "date"}
IDENTITY_KEYS = {
    "asset_id",
    "canonical_tool_id",
    "aliases",
    "display_name",
    "repository_path",
    "bundled_relative_url",
    "attribution_notice",
}
EXPECTED_ASSETS = {
    "codex": {
        "asset_id": "tool.codex",
        "aliases": ["codex"],
        "display_name": "Codex",
        "source_url": "https://openai.com/brand/",
        "repository_path": "src-frontend/assets/tool-identities/codex.png",
        "bundled_relative_url": "./assets/tool-identities/codex.png",
        "source_sha256": "01485e70cea6df8422f5abc643fbbd3c153442cc41da0e7d8e7451801ebf26e2",
        "repository_sha256": "033bcda93ec990cb4a465d27b5d4f2d53af263eac703d1e73b2048d7b7e694af",
        "derivation": "aspect_ratio_containment_rasterized_png",
        "dimensions": (640, 640),
    },
    "claude_code": {
        "asset_id": "tool.claude-code",
        "aliases": ["claude_code", "claude-code", "claude"],
        "display_name": "Claude Code",
        "source_url": "https://anthropic.com/press-kit",
        "repository_path": "src-frontend/assets/tool-identities/claude.png",
        "bundled_relative_url": "./assets/tool-identities/claude.png",
        "source_sha256": "f252cddcf91362ce4e01655044c7d8308c32b4972b1c16b459839ad8c61a0a68",
        "repository_sha256": "f252cddcf91362ce4e01655044c7d8308c32b4972b1c16b459839ad8c61a0a68",
        "derivation": "exact_bytes",
        "dimensions": (1280, 1280),
    },
    "antigravity": {
        "asset_id": "tool.antigravity",
        "aliases": ["antigravity", "antigravity_ide", "antigravity-cli", "antigravity_cli"],
        "display_name": "Antigravity",
        "source_url": "https://antigravity.google/press?app=antigravity",
        "repository_path": "src-frontend/assets/tool-identities/antigravity.png",
        "bundled_relative_url": "./assets/tool-identities/antigravity.png",
        "source_sha256": "e0cd08ccd10cd8d08ccf0ba449823ee88495825c0841619618100d3ab089f51e",
        "repository_sha256": "e0cd08ccd10cd8d08ccf0ba449823ee88495825c0841619618100d3ab089f51e",
        "derivation": "exact_bytes",
        "dimensions": (540, 540),
    },
    "hermes": {
        "asset_id": "tool.hermes",
        "aliases": ["hermes", "hermes_agent", "hermesagent"],
        "display_name": "Hermes",
        "source_url": "https://hermes-agent.nousresearch.com/icon.png",
        "repository_path": "src-frontend/assets/tool-identities/hermesagent.png",
        "bundled_relative_url": "./assets/tool-identities/hermesagent.png",
        "source_sha256": "5fbced606189c9bfc2925a16f8903333041cabbf1f8699fe9fe9b3e24504048f",
        "repository_sha256": "5fbced606189c9bfc2925a16f8903333041cabbf1f8699fe9fe9b3e24504048f",
        "derivation": "exact_bytes",
        "dimensions": (48, 48),
    },
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class QualificationError(RuntimeError):
    """Safe-code qualification failure."""


def fail(code: str) -> None:
    raise QualificationError(code)


def text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        fail("asset_media_invalid")
    return struct.unpack(">II", header[16:24])


def validate_repository_path(value: Any) -> str:
    relative = text(value)
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or str(pure) != relative:
        fail("asset_path_invalid")
    if any(part in {"", ".", ".."} for part in pure.parts):
        fail("asset_path_invalid")
    return relative


def safe_repository_asset(repo_root: Path, value: Any) -> Path | None:
    relative = validate_repository_path(value)
    pure = PurePosixPath(relative)
    current = repo_root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            fail("asset_symlink_rejected")
    try:
        metadata = current.stat()
    except FileNotFoundError:
        return None
    except OSError:
        fail("asset_unreadable")
    if not current.is_file() or metadata.st_size > 4 * 1024 * 1024:
        fail("asset_unreadable")
    return current


def validate_runtime_url(value: Any) -> str:
    url = text(value)
    if not url.startswith("./assets/tool-identities/"):
        fail("asset_url_invalid")
    lowered = url.lower()
    if "://" in lowered or lowered.startswith(("data:", "file:")) or ".." in PurePosixPath(url).parts:
        fail("asset_url_invalid")
    return url


def validate_claim_boundary(manifest: dict[str, Any]) -> None:
    serialized = json.dumps(manifest, ensure_ascii=False).lower()
    if "/users/" in serialized or "file://" in serialized:
        fail("asset_local_path_rejected")
    if "official" in serialized or "endorsement" in serialized:
        fail("asset_vendor_claim_rejected")


def validate_asset(repo_root: Path, asset: Any) -> tuple[str, list[str], dict[str, Any]]:
    if not isinstance(asset, dict):
        fail("asset_manifest_invalid")
    if not set(asset).issubset(ASSET_KEYS) or not IDENTITY_KEYS.issubset(asset):
        fail("asset_manifest_invalid")
    canonical_tool_id = text(asset.get("canonical_tool_id"))
    asset_id = text(asset.get("asset_id"))
    display_name = text(asset.get("display_name"))
    source_url = text(asset.get("sourceUrl"))
    aliases = asset.get("aliases")
    if not canonical_tool_id or not asset_id or not display_name or not isinstance(aliases, list):
        fail("asset_manifest_invalid")
    expected = EXPECTED_ASSETS.get(canonical_tool_id)
    if expected is None:
        fail("asset_manifest_invalid")
    normalized_aliases = [text(alias) for alias in aliases]
    if any(not alias for alias in normalized_aliases) or len(set(normalized_aliases)) != len(normalized_aliases):
        fail("asset_alias_invalid")
    if canonical_tool_id not in normalized_aliases:
        fail("asset_alias_invalid")
    validate_repository_path(asset.get("repository_path"))
    validate_runtime_url(asset.get("bundled_relative_url"))
    if (
        asset_id != expected["asset_id"]
        or normalized_aliases != expected["aliases"]
        or display_name != expected["display_name"]
        or source_url != expected["source_url"]
        or asset.get("repository_path") != expected["repository_path"]
        or asset.get("bundled_relative_url") != expected["bundled_relative_url"]
    ):
        fail("asset_catalog_drift")
    if asset.get("attribution_notice") is not None:
        fail("asset_vendor_claim_rejected")

    source = asset.get("source")
    if isinstance(source, dict) and not set(source).issubset(SOURCE_KEYS):
        fail("asset_manifest_invalid")
    if isinstance(source, dict) and source.get("kind") is not None and source.get("kind") != "vendor_downloaded":
        fail("asset_source_invalid")
    dimensions = asset.get("dimensions")
    approval = asset.get("product_use_approval")
    if isinstance(dimensions, dict) and not set(dimensions).issubset(DIMENSION_KEYS):
        fail("asset_manifest_invalid")
    if isinstance(approval, dict) and not set(approval).issubset(APPROVAL_KEYS):
        fail("asset_manifest_invalid")

    reason: str | None = None
    try:
        path = safe_repository_asset(repo_root, asset.get("repository_path"))
        if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
            fail("asset_source_invalid")
        source_hash = text(source.get("source_sha256")).lower()
        repository_hash = text(asset.get("repository_sha256")).lower()
        if not SHA256.fullmatch(source_hash) or not SHA256.fullmatch(repository_hash):
            fail("asset_hash_invalid")
        try:
            actual_hash = sha256_file(path) if path is not None else None
        except OSError:
            fail("asset_unreadable")
        if (
            source_hash != expected["source_sha256"]
            or repository_hash != expected["repository_sha256"]
            or (path is not None and repository_hash != actual_hash)
        ):
            fail("asset_hash_mismatch")
        if (
            asset.get("media_type") != "image/png"
            or asset.get("derivation") != expected["derivation"]
        ):
            fail("asset_media_invalid")
        if not isinstance(dimensions, dict) or set(dimensions) != DIMENSION_KEYS:
            fail("asset_media_invalid")
        try:
            actual_dimensions = png_dimensions(path) if path is not None else None
        except OSError:
            fail("asset_unreadable")
        if (
            (path is not None and actual_dimensions != expected["dimensions"])
            or (dimensions.get("width"), dimensions.get("height")) != expected["dimensions"]
        ):
            fail("asset_media_invalid")

        if (
            not isinstance(approval, dict)
            or set(approval) != APPROVAL_KEYS
            or approval.get("status") != "approved"
            or approval.get("authority") != "user"
            or not text(approval.get("date"))
        ):
            fail("asset_approval_missing")
        statuses = [
            asset.get("copyright_status"),
            asset.get("license_status"),
            asset.get("trademark_review_status"),
        ]
        if any(isinstance(status, str) and status in {"denied", "restricted", "rejected"} for status in statuses):
            fail("asset_rights_restricted")
        if statuses != ["not_asserted", "not_asserted", "not_asserted"]:
            fail("asset_rights_state_invalid")
        contexts = asset.get("allowed_usage_scope")
        if (
            not isinstance(contexts, list)
            or any(not isinstance(context, str) for context in contexts)
            or set(contexts) != ALLOWED_CONTEXTS
            or len(contexts) != len(ALLOWED_CONTEXTS)
        ):
            fail("asset_usage_scope_invalid")
        if asset.get("modification_constraint") != "aspect_ratio_containment_only":
            fail("asset_modification_invalid")
        fail("asset_rights_not_asserted")
    except QualificationError as error:
        if str(error) in {"asset_path_invalid", "asset_symlink_rejected", "asset_manifest_invalid"}:
            raise
        reason = str(error)

    return canonical_tool_id, normalized_aliases, {
        "aliases": normalized_aliases,
        "bundled_relative_url": asset["bundled_relative_url"] if reason is None else None,
        "canonical_tool_id": canonical_tool_id,
        "display_name": display_name,
        "mode": "qualified" if reason is None else "text",
        "reason": reason,
    }


def qualify_tool_identity_manifest(repo_root: Path, manifest_path: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("asset_manifest_invalid")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        fail("asset_manifest_invalid")
    if set(manifest) != MANIFEST_KEYS:
        fail("asset_manifest_invalid")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        fail("asset_manifest_invalid")
    validate_claim_boundary(manifest)
    canonical_ids: list[str] = []
    aliases: list[str] = []
    catalog: list[dict[str, Any]] = []
    for asset in assets:
        canonical_tool_id, asset_aliases, catalog_entry = validate_asset(repo_root, asset)
        canonical_ids.append(canonical_tool_id)
        aliases.extend(asset_aliases)
        catalog.append(catalog_entry)
    if len(set(canonical_ids)) != len(canonical_ids) or len(set(aliases)) != len(aliases):
        fail("asset_alias_invalid")
    if set(canonical_ids) != set(EXPECTED_ASSETS) or len(canonical_ids) != len(EXPECTED_ASSETS):
        fail("asset_catalog_drift")
    qualified_tool_ids = sorted(
        entry["canonical_tool_id"] for entry in catalog if entry["mode"] == "qualified"
    )
    text_only_tool_ids = sorted(
        entry["canonical_tool_id"] for entry in catalog if entry["mode"] == "text"
    )
    status = "qualified" if not text_only_tool_ids else "text_only"
    if qualified_tool_ids and text_only_tool_ids:
        status = "partial"
    return {
        "asset_count": len(canonical_ids),
        "catalog": catalog,
        "canonical_tool_ids": sorted(canonical_ids),
        "qualified_asset_count": len(qualified_tool_ids),
        "qualified_tool_ids": qualified_tool_ids,
        "status": status,
        "text_only_tool_ids": text_only_tool_ids,
    }


def render_runtime_catalog_module(catalog: list[dict[str, Any]]) -> str:
    runtime_catalog = [
        {
            "canonicalToolId": entry["canonical_tool_id"],
            "aliases": entry["aliases"],
            "displayName": entry["display_name"],
            "mode": entry["mode"],
            "src": entry["bundled_relative_url"],
            "reason": entry["reason"],
        }
        for entry in catalog
    ]
    return (
        "// Generated from assets/tool-identities/manifest.json qualification.\n"
        "// Do not edit this catalog by hand.\n"
        "export const TOOL_IDENTITY_CATALOG = Object.freeze("
        + json.dumps(runtime_catalog, ensure_ascii=True, indent=2, sort_keys=True)
        + ");\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = qualify_tool_identity_manifest(args.repo_root, args.manifest)
    except QualificationError as error:
        if args.json:
            print(json.dumps({"code": str(error), "status": "rejected"}, sort_keys=True))
        else:
            print(str(error))
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"{report['status']} {report['asset_count']} tool identity assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
