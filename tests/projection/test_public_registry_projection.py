from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.projection.manifest import (
    build_manifest,
    public_metadata_projection_bytes,
    registry_projection_bytes,
)

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
    assert "harnesskit.skill.public-publisher" not in selected

    metadata = public_metadata_projection_bytes(manifest["public_metadata_projections"])
    assert len(manifest["public_metadata_projections"]) == 12
    assert set(metadata) == {
        f"components/skills/{skill}/{name}"
        for skill in ("intent-guide", "wayfinder", "grill", "grill-to-spec", "agentic-execution", "worktree-lifecycle")
        for name in ("component.yml", "provenance.map.yml")
    }
    assert all(b"/Users/" not in body for body in metadata.values())
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


def test_registry_exception_rejects_alternate_registry_path(tmp_path: Path):
    policy = yaml.safe_load(POLICY.read_text())
    policy["registry_path"] = "profiles/harness-maintenance.yml"
    path = tmp_path / "policy.yml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="registry exception requires"):
        build_manifest(path, REPO_ROOT)
