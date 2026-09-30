from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "assets/tool-identities/manifest.json"
QUALIFIER_PATH = REPO_ROOT / "scripts/package/qualify_tool_identity_assets.py"
CATALOG_MODULE_PATH = REPO_ROOT / "src-frontend/tool-identities.catalog.js"
EXPECTED = {
    "codex": ("codex.png", 640, 640, "01485e70cea6df8422f5abc643fbbd3c153442cc41da0e7d8e7451801ebf26e2", "033bcda93ec990cb4a465d27b5d4f2d53af263eac703d1e73b2048d7b7e694af", "aspect_ratio_containment_rasterized_png"),
    "claude_code": ("claude.png", 1280, 1280, "f252cddcf91362ce4e01655044c7d8308c32b4972b1c16b459839ad8c61a0a68", "f252cddcf91362ce4e01655044c7d8308c32b4972b1c16b459839ad8c61a0a68", "exact_bytes"),
    "antigravity": ("antigravity.png", 540, 540, "e0cd08ccd10cd8d08ccf0ba449823ee88495825c0841619618100d3ab089f51e", "e0cd08ccd10cd8d08ccf0ba449823ee88495825c0841619618100d3ab089f51e", "exact_bytes"),
    "hermes": ("hermesagent.png", 48, 48, "5fbced606189c9bfc2925a16f8903333041cabbf1f8699fe9fe9b3e24504048f", "5fbced606189c9bfc2925a16f8903333041cabbf1f8699fe9fe9b3e24504048f", "exact_bytes"),
}


def load_qualifier():
    spec = importlib.util.spec_from_file_location("qualify_tool_identity_assets", QUALIFIER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_candidate(tmp_path: Path, manifest: dict[str, object]) -> Path:
    candidate = tmp_path / "manifest.json"
    candidate.write_text(json.dumps(manifest), encoding="utf-8")
    return candidate


def copy_manifest_assets(repo_root: Path, manifest: dict[str, object]) -> None:
    for asset in manifest["assets"]:
        relative = Path(asset["repository_path"])
        destination = repo_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, destination)


def catalog_by_tool(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        entry["canonical_tool_id"]: entry
        for entry in report["catalog"]
    }


def test_unasserted_rights_produce_the_same_text_only_catalog_without_public_image_bytes(
    tmp_path: Path,
) -> None:
    qualifier = load_qualifier()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    candidate = write_candidate(tmp_path, manifest)
    private_repo = tmp_path / "private"
    private_repo.mkdir()
    copy_manifest_assets(private_repo, manifest)
    public_repo = tmp_path / "public"
    public_repo.mkdir()

    private_report = qualifier.qualify_tool_identity_manifest(private_repo, candidate)
    public_report = qualifier.qualify_tool_identity_manifest(public_repo, candidate)

    assert private_report == public_report
    assert private_report["asset_count"] == 4
    assert private_report["qualified_asset_count"] == 0
    assert private_report["qualified_tool_ids"] == []
    assert private_report["text_only_tool_ids"] == ["antigravity", "claude_code", "codex", "hermes"]
    assert private_report["status"] == "text_only"
    assert {entry["mode"] for entry in private_report["catalog"]} == {"text"}
    assert {entry["reason"] for entry in private_report["catalog"]} == {"asset_rights_not_asserted"}
    assert all(entry["bundled_relative_url"] is None for entry in private_report["catalog"])
    assert qualifier.render_runtime_catalog_module(private_report["catalog"]) == (
        qualifier.render_runtime_catalog_module(public_report["catalog"])
    )


def test_user_approved_vendor_sources_remain_unqualified_without_rights_evidence() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(QUALIFIER_PATH),
            "--repo-root",
            str(REPO_ROOT),
            "--manifest",
            str(MANIFEST_PATH),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["asset_count"] == 4
    assert report["canonical_tool_ids"] == ["antigravity", "claude_code", "codex", "hermes"]
    assert report["qualified_asset_count"] == 0
    assert report["qualified_tool_ids"] == []
    assert report["text_only_tool_ids"] == ["antigravity", "claude_code", "codex", "hermes"]
    assert report["status"] == "text_only"
    assert {entry["mode"] for entry in report["catalog"]} == {"text"}
    assert {entry["reason"] for entry in report["catalog"]} == {"asset_rights_not_asserted"}
    assert all(entry["bundled_relative_url"] is None for entry in report["catalog"])
    qualifier = load_qualifier()
    assert CATALOG_MODULE_PATH.read_text(encoding="utf-8") == qualifier.render_runtime_catalog_module(
        report["catalog"]
    )

    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert "/Users/" not in manifest_text
    assert "official" not in manifest_text.lower()
    assert "endorsement" not in manifest_text.lower()
    assert len(manifest["assets"]) == 4
    for asset in manifest["assets"]:
        filename, width, height, source_sha256, repository_sha256, derivation = EXPECTED[asset["canonical_tool_id"]]
        assert Path(asset["repository_path"]).name == filename
        assert asset["source"] == {"kind": "vendor_downloaded", "source_sha256": source_sha256}
        assert asset["repository_sha256"] == repository_sha256
        assert asset["dimensions"] == {"width": width, "height": height}
        assert asset["derivation"] == derivation
        assert asset["sourceUrl"].startswith("https://")
        assert asset["product_use_approval"]["status"] == "approved"
        assert asset["copyright_status"] == "not_asserted"
        assert asset["license_status"] == "not_asserted"
        assert asset["trademark_review_status"] == "not_asserted"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda asset: asset.update(repository_path="/tmp/codex.png"), "asset_path_invalid"),
        (lambda asset: asset["source"].update(kind={}), "asset_source_invalid"),
        (lambda asset: asset.update(bundled_relative_url="https://example.invalid/codex.png"), "asset_url_invalid"),
        (lambda asset: asset.update(bundled_relative_url="data:image/png;base64,AA=="), "asset_url_invalid"),
        (lambda asset: asset.update(bundled_relative_url="./assets/tool-identities/../codex.png"), "asset_url_invalid"),
    ],
)
@pytest.mark.parametrize("with_images", [True, False], ids=["private", "public"])
def test_qualification_fails_closed_for_structurally_unsafe_entries(
    tmp_path: Path,
    mutation,
    code: str,
    with_images: bool,
) -> None:
    qualifier = load_qualifier()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    mutation(manifest["assets"][0])
    candidate = tmp_path / "manifest.json"
    candidate.write_text(json.dumps(manifest), encoding="utf-8")
    repo_root = REPO_ROOT if with_images else tmp_path / "public"
    if not with_images:
        repo_root.mkdir()

    with pytest.raises(qualifier.QualificationError, match=code):
        qualifier.qualify_tool_identity_manifest(repo_root, candidate)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda asset: asset["source"].update(source_sha256="0" * 64), "asset_hash_mismatch"),
        (lambda asset: asset.update(media_type="image/svg+xml"), "asset_media_invalid"),
        (lambda asset: asset["product_use_approval"].update(status="pending"), "asset_approval_missing"),
        (lambda asset: asset.update(allowed_usage_scope=[]), "asset_usage_scope_invalid"),
        (lambda asset: asset.update(modification_constraint="recolor"), "asset_modification_invalid"),
        (lambda asset: asset.update(copyright_status="restricted"), "asset_rights_restricted"),
        (lambda asset: asset.update(license_status="approved"), "asset_rights_state_invalid"),
        (lambda asset: asset.update(copyright_status={}), "asset_rights_state_invalid"),
        (lambda asset: asset.update(allowed_usage_scope=[{}]), "asset_usage_scope_invalid"),
    ],
)
def test_missing_public_images_do_not_hide_other_validation_failures(
    tmp_path: Path,
    mutation,
    code: str,
) -> None:
    qualifier = load_qualifier()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    mutation(manifest["assets"][0])
    public_repo = tmp_path / "public"
    public_repo.mkdir()

    report = qualifier.qualify_tool_identity_manifest(
        public_repo,
        write_candidate(tmp_path, manifest),
    )

    assert report["qualified_asset_count"] == 0
    assert catalog_by_tool(report)["codex"]["reason"] == code
    assert all(entry["bundled_relative_url"] is None for entry in report["catalog"])


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("missing_file", "asset_rights_not_asserted"),
        ("non_file", "asset_unreadable"),
        ("changed_bytes", "asset_hash_mismatch"),
        ("changed_manifest_hash", "asset_hash_mismatch"),
        ("approval_missing", "asset_approval_missing"),
        ("rights_restricted", "asset_rights_restricted"),
    ],
)
def test_entry_local_validation_keeps_specific_reasons_without_qualifying_unverified_tools(
    tmp_path: Path,
    failure: str,
    code: str,
) -> None:
    qualifier = load_qualifier()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    repo_root = tmp_path / "repo"
    copy_manifest_assets(repo_root, manifest)
    changed_asset = manifest["assets"][0]
    changed_path = repo_root / changed_asset["repository_path"]

    if failure == "missing_file":
        changed_path.unlink()
    elif failure == "non_file":
        changed_path.unlink()
        changed_path.mkdir()
    elif failure == "changed_bytes":
        changed_path.write_bytes(changed_path.read_bytes() + b"changed")
    elif failure == "changed_manifest_hash":
        changed_asset["source"]["source_sha256"] = "0" * 64
    elif failure == "approval_missing":
        changed_asset["product_use_approval"]["status"] = "pending"
    else:
        changed_asset["copyright_status"] = "restricted"

    report = qualifier.qualify_tool_identity_manifest(
        repo_root,
        write_candidate(tmp_path, manifest),
    )
    catalog = catalog_by_tool(report)

    assert report["asset_count"] == 4
    assert report["qualified_asset_count"] == 0
    assert report["text_only_tool_ids"] == ["antigravity", "claude_code", "codex", "hermes"]
    assert catalog["codex"]["mode"] == "text"
    assert catalog["codex"]["reason"] == code
    assert catalog["codex"]["bundled_relative_url"] is None
    assert catalog["codex"]["aliases"] == ["codex"]
    for tool_id in {"claude_code", "antigravity", "hermes"}:
        assert catalog[tool_id]["mode"] == "text"
        assert catalog[tool_id]["reason"] == "asset_rights_not_asserted"
        assert catalog[tool_id]["bundled_relative_url"] is None


@pytest.mark.parametrize("target_exists", [True, False], ids=["matching_bytes", "missing_target"])
def test_qualification_rejects_symlinked_asset_with_or_without_target_bytes(
    tmp_path: Path,
    target_exists: bool,
) -> None:
    qualifier = load_qualifier()
    source = REPO_ROOT / "src-frontend/assets/tool-identities/codex.png"
    repo_root = tmp_path / "repo"
    asset_dir = repo_root / "src-frontend/assets/tool-identities"
    asset_dir.mkdir(parents=True)
    linked = asset_dir / "codex.png"
    linked.symlink_to(source if target_exists else tmp_path / "missing.png")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["assets"] = [manifest["assets"][0]]
    candidate = tmp_path / "manifest.json"
    candidate.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(qualifier.QualificationError, match="asset_symlink_rejected"):
        qualifier.qualify_tool_identity_manifest(repo_root, candidate)


@pytest.mark.parametrize("drift", ["removed_asset", "added_alias"])
def test_qualification_rejects_approved_catalog_identity_or_alias_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    qualifier = load_qualifier()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if drift == "removed_asset":
        manifest["assets"].pop()
    else:
        manifest["assets"][0]["aliases"].append("codex-cli")

    with pytest.raises(qualifier.QualificationError):
        qualifier.qualify_tool_identity_manifest(REPO_ROOT, write_candidate(tmp_path, manifest))


def test_qualification_rejects_self_rehashed_bytes_outside_the_approved_hash_matrix(
    tmp_path: Path,
) -> None:
    qualifier = load_qualifier()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    repo_root = tmp_path / "repo"
    copy_manifest_assets(repo_root, manifest)

    changed_asset = manifest["assets"][0]
    changed_path = repo_root / changed_asset["repository_path"]
    changed_path.write_bytes(changed_path.read_bytes() + b"unapproved-self-rehash")
    changed_hash = hashlib.sha256(changed_path.read_bytes()).hexdigest()
    changed_asset["source"]["source_sha256"] = changed_hash
    changed_asset["repository_sha256"] = changed_hash

    report = qualifier.qualify_tool_identity_manifest(
        repo_root,
        write_candidate(tmp_path, manifest),
    )
    catalog = catalog_by_tool(report)

    assert report["qualified_asset_count"] == 0
    assert report["text_only_tool_ids"] == ["antigravity", "claude_code", "codex", "hermes"]
    assert catalog["codex"]["mode"] == "text"
    assert catalog["codex"]["reason"] == "asset_hash_mismatch"


def test_qualification_rejects_unknown_vendor_authority_claims(tmp_path: Path) -> None:
    qualifier = load_qualifier()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["assets"][0]["vendor_authority"] = "endorsed_by_vendor"

    with pytest.raises(qualifier.QualificationError):
        qualifier.qualify_tool_identity_manifest(REPO_ROOT, write_candidate(tmp_path, manifest))
