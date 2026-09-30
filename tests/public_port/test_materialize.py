from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts.public_port.manifest import canonical_json, sha256_bytes
from scripts.public_port.materialize import (
    MaterializeError,
    _promote_stage,
    materialize as _materialize,
)
from tests.public_port.support import git, manifest_fixture, policy, remote_reader_for


def materialize(manifest, **kwargs):
    return _materialize(manifest, policy=policy(), **kwargs)


def _resign_manifest(manifest: dict) -> None:
    tree_digest_entries = [
        {"path": entry["path"], "mode": entry["mode"], "sha256": entry["sha256"]}
        for entry in manifest["expected_tree"]
    ]
    manifest["expected_tree_sha256"] = sha256_bytes(canonical_json(tree_digest_entries))
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256")
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(unsigned))


def test_materialize_writes_only_retained_and_authored_files(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)

    materialize(
        manifest,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )

    assert (baseline / "README.md").read_text(encoding="utf-8") == "public baseline\n"
    assert (baseline / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert (baseline / "app/main.txt").read_text(encoding="utf-8") == "new app\n"
    assert (baseline / "generator/input.txt").read_text(encoding="utf-8") == "input-v1\n"
    assert not (baseline / "obsolete.txt").exists()
    assert not (baseline / "generated/catalog.json").exists()
    assert not (baseline / "private/operator.txt").exists()


def test_materialize_rejects_wrong_origin_without_mutation(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    git(baseline, "remote", "set-url", "origin", "https://github.com/example/fork.git")
    before = (baseline / "app/main.txt").read_bytes()

    with pytest.raises(MaterializeError, match="origin URL"):
        materialize(
            manifest,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(manifest),
        )

    assert (baseline / "app/main.txt").read_bytes() == before


def test_materialize_rejects_baseline_drift_without_mutation(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    (baseline / "keep.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(MaterializeError, match="clean exact baseline"):
        materialize(
            manifest,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(manifest),
        )

    assert (baseline / "keep.txt").read_text(encoding="utf-8") == "dirty\n"


def test_materialize_rechecks_authored_hash_before_copy(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    (source / "app/main.txt").write_text("changed after review\n", encoding="utf-8")
    git(source, "add", "app/main.txt")
    git(source, "commit", "-m", "change reviewed source")

    with pytest.raises(MaterializeError, match="authored hash mismatch"):
        materialize(
            manifest,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(manifest),
        )

    assert (baseline / "app/main.txt").read_text(encoding="utf-8") == "old app\n"


def test_materialize_rejects_expected_tree_digest_that_differs_from_its_category(
    tmp_path: Path,
):
    baseline, source, manifest = manifest_fixture(tmp_path)
    poisoned = copy.deepcopy(manifest)
    expected_entry = next(
        entry for entry in poisoned["expected_tree"] if entry["path"] == "keep.txt"
    )
    expected_entry["sha256"] = "0" * 64
    _resign_manifest(poisoned)

    with pytest.raises(MaterializeError, match="expected_tree entry mismatch"):
        materialize(
            poisoned,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(poisoned),
        )

    assert (baseline / "app/main.txt").read_text(encoding="utf-8") == "old app\n"


def test_materialize_rejects_self_resigned_authored_path_outside_reviewed_policy(
    tmp_path: Path,
):
    baseline, source, manifest = manifest_fixture(tmp_path)
    poisoned = copy.deepcopy(manifest)
    body = (source / "private/operator.txt").read_bytes()
    authored_entry = {
        "path": "private/operator.txt",
        "mode": "100644",
        "sha256": sha256_bytes(body),
    }
    poisoned["ported_authored"].append(authored_entry)
    poisoned["ported_authored"].sort(key=lambda entry: entry["path"])
    poisoned["expected_tree"].append({**authored_entry, "origin": "ported_authored"})
    poisoned["expected_tree"].sort(key=lambda entry: entry["path"])
    _resign_manifest(poisoned)

    with pytest.raises(MaterializeError, match="reviewed policy"):
        materialize(
            poisoned,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(poisoned),
        )

    assert not (baseline / "private/operator.txt").exists()
    assert (baseline / "app/main.txt").read_text(encoding="utf-8") == "old app\n"


def test_materialize_rejects_private_content_even_when_manifest_hashes_match(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    private_body = b"checkout = /" + b"Users/private-user/Projects/internal\n"
    (source / "app/main.txt").write_bytes(private_body)
    git(source, "add", "app/main.txt")
    git(source, "commit", "-m", "replace reviewed source")

    poisoned = copy.deepcopy(manifest)
    authored_entry = next(
        entry for entry in poisoned["ported_authored"] if entry["path"] == "app/main.txt"
    )
    authored_entry["sha256"] = sha256_bytes(private_body)
    expected_entry = next(
        entry for entry in poisoned["expected_tree"] if entry["path"] == "app/main.txt"
    )
    expected_entry["sha256"] = sha256_bytes(private_body)
    _resign_manifest(poisoned)

    with pytest.raises(MaterializeError, match="public content policy"):
        materialize(
            poisoned,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(poisoned),
        )

    assert (baseline / "app/main.txt").read_text(encoding="utf-8") == "old app\n"


def test_materialize_rejects_private_retained_content_before_promotion(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    private_body = b"checkout = /" + b"Users/private-user\n"
    (baseline / "keep.txt").write_bytes(private_body)
    git(baseline, "add", "keep.txt")
    git(baseline, "commit", "-m", "replace retained baseline")
    baseline_commit = git(baseline, "rev-parse", "HEAD")

    poisoned = copy.deepcopy(manifest)
    poisoned["baseline"]["commit"] = baseline_commit
    retained_entry = next(
        entry for entry in poisoned["retained_baseline"] if entry["path"] == "keep.txt"
    )
    retained_entry["sha256"] = sha256_bytes(private_body)
    expected_entry = next(
        entry for entry in poisoned["expected_tree"] if entry["path"] == "keep.txt"
    )
    expected_entry["sha256"] = sha256_bytes(private_body)
    _resign_manifest(poisoned)

    with pytest.raises(MaterializeError, match="public content policy"):
        materialize(
            poisoned,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(poisoned),
        )

    assert (baseline / "keep.txt").read_bytes() == private_body
    assert (baseline / "app/main.txt").read_text(encoding="utf-8") == "old app\n"


def test_materialize_reads_authored_bytes_from_git_object_not_symlinked_worktree(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    external = tmp_path / "external-secret.txt"
    external.write_text("private\n", encoding="utf-8")
    target = source / "app/main.txt"
    target.unlink()
    target.symlink_to(external)

    materialize(
        manifest,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )

    assert (baseline / "app/main.txt").read_text(encoding="utf-8") == "new app\n"
    assert external.read_text(encoding="utf-8") == "private\n"


def test_materialize_stage_failure_leaves_original_baseline_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    baseline, source, manifest = manifest_fixture(tmp_path)
    from scripts.public_port import materialize as materialize_module

    original_write = materialize_module.SecureTree.write_file

    def fail_on_authored(self, relative_path, body, mode):
        if relative_path == "app/main.txt":
            raise OSError("simulated stage disk failure")
        return original_write(self, relative_path, body, mode)

    monkeypatch.setattr(materialize_module.SecureTree, "write_file", fail_on_authored)

    with pytest.raises(MaterializeError, match="simulated stage disk failure"):
        materialize(
            manifest,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(manifest),
        )

    assert (baseline / "app/main.txt").read_text(encoding="utf-8") == "old app\n"
    assert (baseline / "obsolete.txt").read_text(encoding="utf-8") == "remove me\n"


def test_promotion_rolls_back_when_stage_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destination = tmp_path / "destination"
    stage_container = tmp_path / "stage-container"
    stage = stage_container / "candidate"
    destination.mkdir()
    stage.mkdir(parents=True)
    (destination / "state.txt").write_text("baseline\n", encoding="utf-8")
    (stage / "state.txt").write_text("candidate\n", encoding="utf-8")
    original_rename = __import__("os").rename

    def fail_candidate_rename(source, target, **kwargs):
        if source == "candidate" and target == "destination":
            raise OSError("simulated promotion failure")
        return original_rename(source, target, **kwargs)

    monkeypatch.setattr("scripts.public_port.materialize.os.rename", fail_candidate_rename)

    with pytest.raises(OSError, match="simulated promotion failure"):
        _promote_stage(stage, destination, validator=lambda _root: None)

    assert (destination / "state.txt").read_text(encoding="utf-8") == "baseline\n"


def test_promotion_preserves_destination_when_initial_backup_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destination = tmp_path / "destination"
    stage = tmp_path / "stage-container" / "candidate"
    destination.mkdir()
    stage.mkdir(parents=True)
    (destination / "state.txt").write_text("baseline\n", encoding="utf-8")
    (stage / "state.txt").write_text("candidate\n", encoding="utf-8")
    original_rename = __import__("os").rename

    def fail_initial_backup(source, target, **kwargs):
        if source == "destination" and target == "previous":
            raise OSError("simulated backup failure")
        return original_rename(source, target, **kwargs)

    monkeypatch.setattr("scripts.public_port.materialize.os.rename", fail_initial_backup)

    with pytest.raises(OSError, match="simulated backup failure"):
        _promote_stage(stage, destination, validator=lambda _root: None)

    assert (destination / "state.txt").read_text(encoding="utf-8") == "baseline\n"
    assert (stage / "state.txt").read_text(encoding="utf-8") == "candidate\n"


def test_promotion_validator_failure_rolls_back_without_backup_residue(tmp_path: Path):
    destination = tmp_path / "destination"
    stage = tmp_path / "stage-container" / "candidate"
    destination.mkdir()
    stage.mkdir(parents=True)
    (destination / "state.txt").write_text("baseline\n", encoding="utf-8")
    (stage / "state.txt").write_text("candidate\n", encoding="utf-8")

    def fail_validation(_root: Path) -> None:
        raise MaterializeError("simulated validation failure")

    with pytest.raises(MaterializeError, match="simulated validation failure"):
        _promote_stage(stage, destination, validator=fail_validation)

    assert (destination / "state.txt").read_text(encoding="utf-8") == "baseline\n"
    assert not list(tmp_path.glob(".public-port-backup-*"))


def test_materialize_rejects_public_clone_with_shared_object_alternates(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    alternates = baseline / ".git/objects/info/alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(str(source / ".git/objects") + "\n", encoding="utf-8")

    with pytest.raises(MaterializeError, match="shared Git object storage"):
        materialize(
            manifest,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(manifest),
        )


def test_materialize_rejects_physical_root_alias_overlap(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)

    with pytest.raises(MaterializeError, match="symlink"):
        materialize(
            manifest,
            source_root=alias,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(manifest),
        )


def test_materialize_rejects_a_linked_worktree_git_file(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    shutil_target = baseline / ".git-real"
    (baseline / ".git").rename(shutil_target)
    (baseline / ".git").write_text(f"gitdir: {shutil_target}\n", encoding="utf-8")

    with pytest.raises(MaterializeError, match="full clone"):
        materialize(
            manifest,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(manifest),
        )


def test_materialize_rejects_clone_nested_inside_private_source(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    nested_clone = source / "staging"
    baseline.rename(nested_clone)

    with pytest.raises(MaterializeError, match="disjoint roots"):
        materialize(
            manifest,
            source_root=source,
            public_clone_root=nested_clone,
            remote_default_reader=remote_reader_for(manifest),
        )


def test_materialize_rejects_remote_tip_drift_without_mutation(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)

    with pytest.raises(MaterializeError, match="tip drifted"):
        materialize(
            manifest,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=lambda _url: type("Remote", (), {"branch": "main", "commit": "0" * 40})(),
        )

    assert (baseline / "app/main.txt").read_text(encoding="utf-8") == "old app\n"


def test_materialize_rejects_unclassified_baseline_path_without_deleting_it(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    poisoned = copy.deepcopy(manifest)
    poisoned["retained_baseline"] = [
        entry for entry in poisoned["retained_baseline"] if entry["path"] != "keep.txt"
    ]
    poisoned["expected_tree"] = [
        entry for entry in poisoned["expected_tree"] if entry["path"] != "keep.txt"
    ]
    _resign_manifest(poisoned)

    with pytest.raises(MaterializeError, match="reviewed policy"):
        materialize(
            poisoned,
            source_root=source,
            public_clone_root=baseline,
            remote_default_reader=remote_reader_for(poisoned),
        )

    assert (baseline / "keep.txt").read_text(encoding="utf-8") == "keep\n"
