from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.public_port.candidate as candidate_module
from scripts.public_port.candidate import (
    CandidateError,
    CandidateIdentity,
    build_candidate,
)
from scripts.public_port.materialize import materialize
from scripts.public_port.regenerate import regenerate
from tests.public_port.support import git, manifest_fixture, policy, remote_reader_for


def test_candidate_git_command_timeout_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def time_out(*_arguments, **_keywords):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=120)

    monkeypatch.setattr(candidate_module.subprocess, "run", time_out)

    with pytest.raises(CandidateError, match="Git candidate command timed out"):
        candidate_module._run(tmp_path, "status")


def _write_catalog(_rule, root: Path) -> None:
    output = root / "generated/catalog.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('{"ok":true}\n', encoding="utf-8")


def _qualified_tree(tmp_path: Path):
    baseline, source, manifest = manifest_fixture(tmp_path)
    selected = policy()
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )
    regenerate(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        generator_runner=_write_catalog,
    )
    return baseline, manifest


def _identity() -> CandidateIdentity:
    return CandidateIdentity(
        name="HarnessKit Contributors",
        email="contributors@users.noreply.github.com",
        timestamp="2026-07-20T00:00:00+00:00",
    )


def test_builds_exact_candidate_without_using_ambient_git_identity_or_creating_refs(
    tmp_path: Path,
) -> None:
    root, manifest = _qualified_tree(tmp_path)
    git(root, "config", "user.name", "Private Person")
    git(root, "config", "user.email", "private" + "@example.com")
    refs_before = git(root, "for-each-ref", "--format=%(refname) %(objectname)")
    index_before = git(root, "status", "--porcelain")

    candidate = build_candidate(
        root,
        manifest=manifest,
        identity=_identity(),
        message="feat: publish HarnessKit desktop application",
    )

    assert git(root, "show", "-s", "--format=%an <%ae>", candidate.commit_oid) == (
        "HarnessKit Contributors <contributors@users.noreply.github.com>"
    )
    assert git(root, "show", "-s", "--format=%cn <%ce>", candidate.commit_oid) == (
        "HarnessKit Contributors <contributors@users.noreply.github.com>"
    )
    assert git(root, "show", "-s", "--format=%P", candidate.commit_oid) == manifest["baseline"]["commit"]
    assert git(root, "for-each-ref", "--format=%(refname) %(objectname)") == refs_before
    assert git(root, "status", "--porcelain") == index_before


def test_same_inputs_produce_same_commit_oid(tmp_path: Path) -> None:
    root, manifest = _qualified_tree(tmp_path)
    kwargs = {
        "manifest": manifest,
        "identity": _identity(),
        "message": "feat: publish HarnessKit desktop application",
    }

    first = build_candidate(root, **kwargs)
    second = build_candidate(root, **kwargs)

    assert first == second


def test_builds_annotated_tag_object_without_creating_tag_ref(tmp_path: Path) -> None:
    root, manifest = _qualified_tree(tmp_path)

    candidate = build_candidate(
        root,
        manifest=manifest,
        identity=_identity(),
        message="feat: publish HarnessKit desktop application",
        tag_name="v0.1.0",
        tag_message="HarnessKit v0.1.0",
    )

    assert candidate.tag_oid is not None
    assert git(root, "cat-file", "-t", candidate.tag_oid) == "tag"
    assert git(root, "tag", "--list") == ""


def test_rejects_tree_drift_before_writing_candidate(tmp_path: Path) -> None:
    root, manifest = _qualified_tree(tmp_path)
    (root / "app/main.txt").write_text("drift\n", encoding="utf-8")

    with pytest.raises(CandidateError, match="tree does not match"):
        build_candidate(
            root,
            manifest=manifest,
            identity=_identity(),
            message="feat: publish HarnessKit desktop application",
        )


def test_rejects_wrong_candidate_parent(tmp_path: Path) -> None:
    root, manifest = _qualified_tree(tmp_path)
    manifest["baseline"]["commit"] = "0" * 40

    with pytest.raises(CandidateError):
        build_candidate(
            root,
            manifest=manifest,
            identity=_identity(),
            message="feat: publish HarnessKit desktop application",
        )
