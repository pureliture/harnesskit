from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

import pytest

import scripts.public_port.manifest as manifest_module

from scripts.public_port.manifest import (
    ManifestError,
    PublicRemoteDefault,
    build_manifest,
    reject_path_identity_collisions,
    validate_manifest,
)
from tests.public_port.support import manifest_fixture, policy, repositories


def test_remote_default_lookup_timeout_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def time_out(*_arguments, **_keywords):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=120)

    monkeypatch.setattr(manifest_module.subprocess, "run", time_out)

    with pytest.raises(ManifestError, match="default-branch lookup timed out"):
        manifest_module.read_public_remote_default(
            "https://github.com/pureliture/harnesskit.git"
        )


def test_manifest_git_blob_reads_are_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def time_out(*_arguments, **_keywords):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=120)

    monkeypatch.setattr(manifest_module.subprocess, "run", time_out)

    with pytest.raises(ManifestError, match="public manifest Git command timed out"):
        manifest_module.tracked_blobs(tmp_path, "HEAD")


def test_manifest_is_deterministic_closed_world_and_schema_valid(tmp_path: Path):
    baseline, source, baseline_commit = repositories(tmp_path)
    first = build_manifest(
        policy(),
        baseline_root=baseline,
        baseline_commit=baseline_commit,
        source_root=source,
        remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
    )
    second = build_manifest(
        policy(),
        baseline_root=baseline,
        baseline_commit=baseline_commit,
        source_root=source,
        remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
    )

    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )
    validate_manifest(first)
    assert first["target_repository_url"] == "https://github.com/pureliture/harnesskit.git"
    assert first["baseline"]["commit"] == baseline_commit
    assert first["manifest_sha256"] == second["manifest_sha256"]


def test_manifest_categories_are_disjoint_and_cover_the_expected_tree(tmp_path: Path):
    _baseline, _source, manifest = manifest_fixture(tmp_path)

    retained = {entry["path"] for entry in manifest["retained_baseline"]}
    authored = {entry["path"] for entry in manifest["ported_authored"]}
    generated = {
        entry["path"]
        for rule in manifest["generated_rules"]
        for entry in rule["outputs"]
    }
    removed = set(manifest["intentional_removals"])
    expected = {entry["path"] for entry in manifest["expected_tree"]}

    categories = [retained, authored, generated, removed]
    for index, left in enumerate(categories):
        for right in categories[index + 1 :]:
            assert left.isdisjoint(right)
    assert expected == retained | authored | generated
    assert "private/operator.txt" not in expected
    assert "obsolete.txt" in removed


def test_manifest_never_serializes_private_source_revision_or_root(tmp_path: Path):
    _baseline, source, manifest = manifest_fixture(tmp_path)
    serialized = json.dumps(manifest, sort_keys=True)

    assert "source_revision" not in serialized
    assert "source_root" not in serialized
    assert str(source) not in serialized


def test_manifest_hashes_committed_git_blob_not_mutable_worktree_bytes(tmp_path: Path):
    baseline, source, baseline_commit = repositories(tmp_path)
    (source / "app/main.txt").write_text("uncommitted replacement\n", encoding="utf-8")

    manifest = build_manifest(
        policy(),
        baseline_root=baseline,
        baseline_commit=baseline_commit,
        source_root=source,
        require_clean_source=False,
        remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
    )

    authored = {entry["path"]: entry for entry in manifest["ported_authored"]}
    assert authored["app/main.txt"]["sha256"] == hashlib.sha256(b"new app\n").hexdigest()


def test_manifest_generated_rules_store_only_reviewed_rule_identity_and_file_closure(tmp_path: Path):
    _baseline, _source, manifest = manifest_fixture(tmp_path)

    assert len(manifest["policy_sha256"]) == 64
    assert manifest["generated_rules"] == [
        {
            "id": "catalog",
            "inputs": manifest["generated_rules"][0]["inputs"],
            "outputs": manifest["generated_rules"][0]["outputs"],
        }
    ]
    assert "command" not in manifest["generated_rules"][0]
    assert "working_directory" not in manifest["generated_rules"][0]


def test_manifest_rejects_a_dirty_private_source(tmp_path: Path):
    baseline, source, baseline_commit = repositories(tmp_path)
    (source / "app/main.txt").write_text("uncommitted bytes\n", encoding="utf-8")

    with pytest.raises(ManifestError, match="source repository must be clean"):
        build_manifest(
            policy(),
            baseline_root=baseline,
            baseline_commit=baseline_commit,
            source_root=source,
            remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
        )


def test_manifest_ignores_symlinked_worktree_file_and_uses_committed_blob(tmp_path: Path):
    baseline, source, baseline_commit = repositories(tmp_path)
    target = source / "app/main.txt"
    target.unlink()
    target.symlink_to(source / "generator/input.txt")

    manifest = build_manifest(
        policy(),
        baseline_root=baseline,
        baseline_commit=baseline_commit,
        source_root=source,
        require_clean_source=False,
        remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
    )

    authored = {entry["path"]: entry for entry in manifest["ported_authored"]}
    assert authored["app/main.txt"]["sha256"] == hashlib.sha256(b"new app\n").hexdigest()


def test_manifest_rejects_casefold_colliding_public_paths(tmp_path: Path):
    with pytest.raises(ManifestError, match="normalization collision"):
        reject_path_identity_collisions(["app/main.txt", "app/MAIN.txt"])


@pytest.mark.parametrize(
    ("relative_path", "body"),
    [
        ("app/main.txt", "checkout = /" + "Users/alice-private/Projects/internal\n"),
        (
            "app/main.txt",
            "origin = https://github.com/pureliture/" + "routine-harness.git\n",
        ),
        ("app/main.txt", "release_branch = " + "codex/tauri-harness-app-impl\n"),
        ("app/operator-runbook.md", "private deployment runbook\n"),
        ("app/cache/module-cache.txt", "raw collector output\n"),
    ],
)
def test_manifest_rejects_private_residue_inside_allowlisted_authored_blob(
    tmp_path: Path, relative_path: str, body: str
):
    baseline, source, baseline_commit = repositories(tmp_path)
    target = source / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    from tests.public_port.support import git

    git(source, "add", relative_path)
    git(source, "commit", "-m", "add private residue fixture")

    with pytest.raises(ManifestError, match="public content policy"):
        build_manifest(
            policy(),
            baseline_root=baseline,
            baseline_commit=baseline_commit,
            source_root=source,
            remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
        )

    assert (baseline / "app/main.txt").read_text(encoding="utf-8") == "old app\n"


def test_manifest_rejects_private_residue_inside_retained_baseline_blob(tmp_path: Path):
    baseline, source, _baseline_commit = repositories(tmp_path)
    private_body = b"checkout = /" + b"Users/private-user\n"
    (baseline / "keep.txt").write_bytes(private_body)
    from tests.public_port.support import git

    git(baseline, "add", "keep.txt")
    git(baseline, "commit", "-m", "add retained private residue fixture")
    baseline_commit = git(baseline, "rev-parse", "HEAD")

    with pytest.raises(ManifestError, match="public content policy"):
        build_manifest(
            policy(),
            baseline_root=baseline,
            baseline_commit=baseline_commit,
            source_root=source,
            remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
        )


def test_manifest_rejects_stale_public_default_branch_tip(tmp_path: Path):
    baseline, source, baseline_commit = repositories(tmp_path)

    with pytest.raises(ManifestError, match="default-branch tip"):
        build_manifest(
            policy(),
            baseline_root=baseline,
            baseline_commit=baseline_commit,
            source_root=source,
            remote_default_reader=lambda _url: PublicRemoteDefault("main", "0" * 40),
        )


def test_manifest_rejects_detached_public_baseline(tmp_path: Path):
    baseline, source, baseline_commit = repositories(tmp_path)
    from tests.public_port.support import git

    git(baseline, "checkout", "--detach", baseline_commit)

    with pytest.raises(ManifestError, match="must not be detached"):
        build_manifest(
            policy(),
            baseline_root=baseline,
            baseline_commit=baseline_commit,
            source_root=source,
            remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
        )
