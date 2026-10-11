from __future__ import annotations

import argparse
import copy
import hashlib
from pathlib import Path

import pytest
import yaml

from scripts.projection.manifest import build_manifest, registry_projection_bytes
from scripts.publish.materialize import materialize_tree
from scripts.publish.publish import _projected_files

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "publish/harnesskit.yml"


def test_public_targets_have_required_adapter_support_files():
    manifest = build_manifest(POLICY, REPO_ROOT)
    included = set(manifest["included_paths"])
    missing = set()
    for group in manifest["components"].values():
        for entry in group:
            component = yaml.safe_load((REPO_ROOT / entry["manifest_path"]).read_text())
            if component["kind"] not in {"agent", "skill", "hook"}:
                continue
            for target in component.get("targets") or {}:
                descriptor = f"adapters/{target}/adapter.yml"
                adapter = yaml.safe_load((REPO_ROOT / descriptor).read_text())
                template = adapter["structures"][component["kind"] + "s"]["template"]
                missing.update({descriptor, f"adapters/{target}/{template}"} - included)
    assert not missing, f"required public adapter support missing: {sorted(missing)}"


def test_real_public_registry_matches_closure_and_preserves_source(tmp_path: Path):
    source = REPO_ROOT / "components/registry.yml"
    original = source.read_bytes()
    manifest = build_manifest(POLICY, REPO_ROOT)
    selected = {c["id"] for group in manifest["components"].values() for c in group}
    assert len(selected) == 19
    data = registry_projection_bytes(manifest["registry_projection"])
    registry = yaml.safe_load(data)
    private = yaml.safe_load(original)
    assert registry["version"] == private["version"]
    assert registry["components"] == {key: private["components"][key] for key in selected}
    assert len(private["components"]) > len(selected)
    assert "harnesskit.skill.public-publisher" not in selected

    dst = tmp_path / "public"
    dst.mkdir()
    materialize_tree(
        REPO_ROOT,
        dst,
        manifest["included_paths"],
        registry_projection=manifest["registry_projection"],
        public_metadata_projections=manifest["public_metadata_projections"],
    )
    assert (dst / "components/registry.yml").read_bytes() == data
    assert all((dst / entry["path"]).is_file() for entry in registry["components"].values())
    assert len(manifest["public_metadata_projections"]) == 12
    assert all((dst / item["path"]).read_text().find("/Users/") < 0 for item in manifest["public_metadata_projections"])
    recomputed = build_manifest(dst / "publish/harnesskit.yml", dst)
    assert recomputed["registry_projection"] == manifest["registry_projection"]
    assert recomputed["public_metadata_projections"] == manifest["public_metadata_projections"]
    assert recomputed["included_paths"] == manifest["included_paths"]
    report = {f["path"]: f["digest"] for f in _projected_files(manifest, REPO_ROOT)}
    assert report["components/registry.yml"] == "sha256:" + hashlib.sha256(data).hexdigest()
    assert all(report[rel] == "sha256:" + hashlib.sha256((dst / rel).read_bytes()).hexdigest()
               for rel in manifest["included_paths"] if rel != "components/registry.yml"
               and rel not in {item["path"] for item in manifest["public_metadata_projections"]})
    for item in manifest["public_metadata_projections"]:
        assert report[item["path"]] == "sha256:" + item["sha256"]
    assert source.read_bytes() == original


@pytest.mark.parametrize("exception", [None, [], "all", {"README.md": "selected_component_closure"},
                                     {"components/registry.yml": "all"}])
def test_unknown_registry_write_exceptions_rejected(tmp_path: Path, exception):
    policy = yaml.safe_load(POLICY.read_text())
    policy["policy"]["content_write_exceptions"] = exception
    path = tmp_path / "policy.yml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="unsupported content_write_exceptions"):
        build_manifest(path, REPO_ROOT)


def test_policy_without_exception_keeps_copy_behavior(tmp_path: Path):
    policy = yaml.safe_load(POLICY.read_text())
    del policy["policy"]["content_write_exceptions"]
    path = tmp_path / "policy.yml"
    path.write_text(yaml.safe_dump(policy))
    manifest = build_manifest(path, REPO_ROOT)
    assert "registry_projection" not in manifest
    dst = tmp_path / "public"
    dst.mkdir()
    materialize_tree(REPO_ROOT, dst, ["components/registry.yml"])
    assert (dst / "components/registry.yml").read_bytes() == (REPO_ROOT / "components/registry.yml").read_bytes()


def test_registry_exception_rejects_alternate_registry_path(tmp_path: Path):
    policy = yaml.safe_load(POLICY.read_text())
    policy["registry_path"] = "profiles/harness-maintenance.yml"
    path = tmp_path / "policy.yml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="registry exception requires"):
        build_manifest(path, REPO_ROOT)


def test_report_rejects_tampered_registry_projection():
    manifest = build_manifest(POLICY, REPO_ROOT)
    manifest["registry_projection"]["content"] += "tampered"
    with pytest.raises(ValueError, match="sha256 mismatch"):
        _projected_files(manifest, REPO_ROOT)


def test_missing_source_rejected_before_stale_cleanup(tmp_path: Path):
    dst = tmp_path / "public"
    dst.mkdir()
    sentinel = dst / "stale.txt"
    sentinel.write_text("keep")
    with pytest.raises(FileNotFoundError):
        materialize_tree(REPO_ROOT, dst, ["missing-component.yml"])
    assert sentinel.read_text() == "keep"


@pytest.mark.parametrize("change", ["path", "content", "hash", "extra", "type", "excluded"])
def test_invalid_projection_rejected_before_stale_cleanup(tmp_path: Path, change: str):
    manifest = build_manifest(POLICY, REPO_ROOT)
    projection = copy.deepcopy(manifest["registry_projection"])
    paths = ["components/registry.yml"]
    if change == "path":
        projection["path"] = "README.md"
    elif change == "content":
        projection["content"] += "tampered"
    elif change == "hash":
        projection["sha256"] = "0" * 64
    elif change == "extra":
        projection["other"] = "README.md"
    elif change == "type":
        projection["content"] = b"invalid"
    else:
        paths = ["README.md"]
    dst = tmp_path / "public"
    dst.mkdir()
    sentinel = dst / "stale.txt"
    sentinel.write_text("keep")
    with pytest.raises(ValueError):
        materialize_tree(REPO_ROOT, dst, paths, registry_projection=projection)
    assert sentinel.read_text() == "keep"


def test_selected_registry_private_reference_rejected(tmp_path: Path):
    manifest = build_manifest(POLICY, REPO_ROOT)
    dst = tmp_path / "public"
    dst.mkdir()
    materialize_tree(REPO_ROOT, dst, manifest["included_paths"],
                     registry_projection=manifest["registry_projection"])
    path = dst / "components/registry.yml"
    registry = yaml.safe_load(path.read_text())
    registry["components"]["harnesskit.skill.component-authoring"]["replacement"] = "harnesskit.skill.private"
    registry["components"]["harnesskit.skill.private"] = {"kind": "skill", "path": "private/component.yml"}
    path.write_text(yaml.safe_dump(registry))
    with pytest.raises(ValueError, match="private/out-of-closure dependency"):
        build_manifest(dst / "publish/harnesskit.yml", dst)


def test_prepare_passes_public_tree_to_package_gate(tmp_path: Path, monkeypatch):
    import scripts.publish.publish as publisher

    dst = tmp_path / "public"
    dst.mkdir()
    monkeypatch.setattr(publisher, "resolve_branch_name", lambda *args: "test")
    monkeypatch.setattr(publisher, "create_publish_worktree", lambda *args, **kwargs: dst)
    seen = []

    def package_gate(root):
        seen.append(root)
        registry = yaml.safe_load((root / "components/registry.yml").read_text())
        assert len(registry["components"]) == 19
        return {"id": "package-smoke", "status": "pass", "detail": "stub"}

    monkeypatch.setattr(publisher, "run_package_gate", package_gate)
    args = argparse.Namespace(repo="pureliture/harnesskit", checkout=tmp_path, policy=str(POLICY),
                              date="20261005", skip_package_gate=False)
    report = publisher.run_prepare(args)
    assert seen == [dst]
    assert report["push"] is None
    assert report["approval"]["decision"] == "not-requested"
