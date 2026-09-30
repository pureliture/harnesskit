from __future__ import annotations

import hashlib
import io
import subprocess
from pathlib import Path

import pytest

import scripts.public_port.git_surface as git_surface_module

from scripts.public_port.git_surface import (
    CandidateGitObjects,
    GitSurfaceError,
    read_git_surface,
)
from scripts.public_port.sanitize import (
    PublicSurfaceSnapshot,
    ReviewedAllowance,
    ReviewedSanitationAllowlist,
    SanitationError,
    audit_public_surface,
)
from tests.public_port.support import commit_repo, git


def test_git_surface_command_timeout_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def time_out(*_arguments, **_keywords):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=120)

    monkeypatch.setattr(git_surface_module.subprocess, "run", time_out)

    with pytest.raises(GitSurfaceError, match="Git surface command timed out"):
        git_surface_module._run(tmp_path, "status")


def test_candidate_object_producer_is_stopped_when_consumer_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeProducer:
        def __init__(self) -> None:
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.killed = False
            self.waited = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.waited = True
            return -9

    producer = FakeProducer()
    calls = 0

    def popen(*_arguments, **_keywords):
        nonlocal calls
        calls += 1
        if calls == 1:
            return producer
        raise OSError("consumer startup failed")

    monkeypatch.setattr(git_surface_module.subprocess, "Popen", popen)

    with pytest.raises(
        GitSurfaceError,
        match="candidate Git objects could not be isolated",
    ):
        git_surface_module._copy_candidate_objects(tmp_path, tmp_path, ["0" * 40])

    assert producer.killed is True
    assert producer.waited is True
    assert producer.stdin.closed is True
    assert producer.stdout.closed is True
    assert producer.stderr.closed is True


def _remote_clone(tmp_path: Path) -> tuple[Path, Path, str]:
    seed = tmp_path / "seed"
    main_commit = commit_repo(seed, {"README.md": "public\n"}, "public baseline")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "origin", "main")

    git(seed, "switch", "-c", "history-surface")
    (seed / "history.txt").write_text("private-history-marker\n", encoding="utf-8")
    git(seed, "add", "history.txt")
    git(seed, "commit", "-m", "history fixture")
    git(seed, "push", "origin", "history-surface")
    git(seed, "tag", "-a", "v0.1.0", "-m", "release fixture", main_commit)
    git(seed, "push", "origin", "v0.1.0")

    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(remote), str(clone))
    git(clone, "switch", "main")
    return remote, clone, main_commit


def test_reads_all_advertised_refs_tags_commit_metadata_and_blobs(tmp_path: Path) -> None:
    _remote, clone, main_commit = _remote_clone(tmp_path)
    refs_before = git(clone, "for-each-ref", "--format=%(refname) %(objectname)")

    snapshot = read_git_surface(
        clone,
        candidate=CandidateGitObjects(commit_oid=main_commit),
    )

    assert snapshot.coverage.complete is True
    assert {ref.name for ref in snapshot.refs} >= {
        "refs/heads/main",
        "refs/heads/history-surface",
        "refs/tags/v0.1.0",
    }
    assert any(value.surface == "git.commit.author_email" for value in snapshot.values)
    assert any(
        value.surface == "git.ref.name" and value.value == b"refs/heads/history-surface"
        for value in snapshot.values
    )
    assert any(
        value.surface == "git.blob"
        and value.value == b"private-history-marker\n"
        for value in snapshot.values
    )
    assert snapshot.coverage.advertised_ref_count == len(snapshot.refs)
    assert git(clone, "for-each-ref", "--format=%(refname) %(objectname)") == refs_before


def test_candidate_commit_not_on_remote_is_still_scanned(tmp_path: Path) -> None:
    _remote, clone, _main_commit = _remote_clone(tmp_path)
    (clone / "candidate.txt").write_text("candidate-only-marker\n", encoding="utf-8")
    git(clone, "add", "candidate.txt")
    git(clone, "commit", "-m", "candidate fixture")
    candidate = git(clone, "rev-parse", "HEAD")

    snapshot = read_git_surface(
        clone,
        candidate=CandidateGitObjects(commit_oid=candidate),
    )

    assert snapshot.candidate.commit_oid == candidate
    assert any(value.value == b"candidate-only-marker\n" for value in snapshot.values)
    assert any(
        value.surface == "git.commit.author_email"
        and value.locator == "candidate:commit:author:email"
        for value in snapshot.values
    )
    assert any(
        value.surface == "git.commit.committer_email"
        and value.locator == "candidate:commit:committer:email"
        for value in snapshot.values
    )


def test_actual_git_reader_and_exact_allowlist_qualify_candidate(tmp_path: Path) -> None:
    _remote, clone, main_commit = _remote_clone(tmp_path)
    baseline = read_git_surface(
        clone,
        candidate=CandidateGitObjects(commit_oid=main_commit),
    )
    (clone / "icon.bin").write_bytes(b"\x00\x01\x02")
    git(clone, "add", "icon.bin")
    git(clone, "commit", "-m", "candidate binary fixture")
    candidate_commit = git(clone, "rev-parse", "HEAD")
    candidate = read_git_surface(
        clone,
        candidate=CandidateGitObjects(commit_oid=candidate_commit),
    )
    github = PublicSurfaceSnapshot.create(name="github", values=(), complete=True)

    with pytest.raises(SanitationError) as blocked:
        audit_public_surface(
            baseline=baseline.as_public_surface_snapshot(name="baseline"),
            candidate=candidate.as_public_surface_snapshot(name="candidate"),
            github=github,
            allowlist=ReviewedSanitationAllowlist.empty(),
        )

    allowances = ReviewedSanitationAllowlist(
        schema_version=1,
        entries=tuple(
            ReviewedAllowance(
                rule_id=finding.rule_id,
                surface=finding.surface,
                locator_sha256=finding.locator_sha256,
                value_sha256=finding.value_sha256,
                justification_sha256=hashlib.sha256(
                    b"reviewed integration fixture"
                ).hexdigest(),
                approved_by="public-port-integration-test",
            )
            for finding in blocked.value.report.findings
        ),
    )

    report = audit_public_surface(
        baseline=baseline.as_public_surface_snapshot(name="baseline"),
        candidate=candidate.as_public_surface_snapshot(name="candidate"),
        github=github,
        allowlist=allowances,
    )

    assert report.qualified is True


def test_rejects_missing_lfs_object(tmp_path: Path) -> None:
    _remote, clone, _main_commit = _remote_clone(tmp_path)
    object_body = b"large private payload"
    object_oid = hashlib.sha256(object_body).hexdigest()
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{object_oid}\n"
        f"size {len(object_body)}\n"
    )
    (clone / "large.bin").write_text(pointer, encoding="utf-8")
    git(clone, "add", "large.bin")
    git(clone, "commit", "-m", "lfs pointer fixture")
    candidate = git(clone, "rev-parse", "HEAD")

    with pytest.raises(GitSurfaceError, match="LFS object is missing"):
        read_git_surface(clone, candidate=CandidateGitObjects(commit_oid=candidate))


def test_verifies_and_scans_reachable_lfs_object(tmp_path: Path) -> None:
    _remote, clone, _main_commit = _remote_clone(tmp_path)
    object_body = b"large private payload"
    object_oid = hashlib.sha256(object_body).hexdigest()
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{object_oid}\n"
        f"size {len(object_body)}\n"
    )
    (clone / "large.bin").write_text(pointer, encoding="utf-8")
    git(clone, "add", "large.bin")
    git(clone, "commit", "-m", "lfs pointer fixture")
    candidate = git(clone, "rev-parse", "HEAD")
    object_path = clone / ".git" / "lfs" / "objects" / object_oid[:2] / object_oid[2:4] / object_oid
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(object_body)

    snapshot = read_git_surface(
        clone,
        candidate=CandidateGitObjects(commit_oid=candidate),
    )

    assert snapshot.coverage.lfs_pointer_count == 1
    assert snapshot.coverage.lfs_object_count == 1
    assert any(value.surface == "git.lfs.object" and value.value == object_body for value in snapshot.values)


def test_rejects_lfs_object_through_symlinked_ancestor(tmp_path: Path) -> None:
    _remote, clone, _main_commit = _remote_clone(tmp_path)
    object_body = b"large public payload"
    object_oid = hashlib.sha256(object_body).hexdigest()
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{object_oid}\n"
        f"size {len(object_body)}\n"
    )
    (clone / "large.bin").write_text(pointer, encoding="utf-8")
    git(clone, "add", "large.bin")
    git(clone, "commit", "-m", "lfs pointer fixture")
    candidate = git(clone, "rev-parse", "HEAD")
    outside = tmp_path / "outside-lfs"
    object_path = outside / "objects" / object_oid[:2] / object_oid[2:4] / object_oid
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(object_body)
    (clone / ".git" / "lfs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(GitSurfaceError, match="no-follow"):
        read_git_surface(clone, candidate=CandidateGitObjects(commit_oid=candidate))


def test_rejects_lfs_object_larger_than_absolute_reviewed_limit(tmp_path: Path) -> None:
    _remote, clone, _main_commit = _remote_clone(tmp_path)
    object_oid = "a" * 64
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{object_oid}\n"
        f"size {128 * 1024 * 1024 + 1}\n"
    )
    (clone / "large.bin").write_text(pointer, encoding="utf-8")
    git(clone, "add", "large.bin")
    git(clone, "commit", "-m", "oversized lfs pointer fixture")
    candidate = git(clone, "rev-parse", "HEAD")

    with pytest.raises(GitSurfaceError, match="reviewed maximum"):
        read_git_surface(clone, candidate=CandidateGitObjects(commit_oid=candidate))


def test_rejects_shallow_or_partial_clone(tmp_path: Path) -> None:
    _remote, clone, main_commit = _remote_clone(tmp_path)
    (clone / ".git" / "shallow").write_text(f"{main_commit}\n", encoding="ascii")

    with pytest.raises(GitSurfaceError, match="shallow or partial"):
        read_git_surface(clone, candidate=CandidateGitObjects(commit_oid=main_commit))
