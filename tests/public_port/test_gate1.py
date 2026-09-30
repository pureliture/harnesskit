from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import scripts.public_port.gate1 as gate1_module

from scripts.public_port.candidate import CandidateIdentity
from scripts.public_port.gate1 import Gate1Error, run_gate1
from scripts.public_port.git_surface import (
    CandidateGitObjects,
    GitCoverage,
    GitRef,
    GitSurfaceSnapshot,
)
from scripts.public_port.github_surface import (
    GitHubCoverage,
    GitHubSurfaceSnapshot,
    RepositoryIdentity,
)
from scripts.public_port.manifest import PublicRemoteDefault
from scripts.public_port.sanitize import ReviewedSanitationAllowlist
from tests.public_port.support import policy, repositories


def _write_catalog(_rule, root: Path) -> None:
    output = root / "generated/catalog.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('{"ok":true}\n', encoding="utf-8")


def _git_snapshot(candidate: CandidateGitObjects, baseline_commit: str) -> GitSurfaceSnapshot:
    return GitSurfaceSnapshot(
        refs=(GitRef("refs/heads/main", baseline_commit),),
        candidate=candidate,
        values=(),
        coverage=GitCoverage(
            complete=True,
            advertised_ref_count=1,
            object_count=1,
            commit_count=1,
            tag_count=0,
            tree_count=0,
            blob_count=0,
            lfs_pointer_count=0,
            lfs_object_count=0,
        ),
        snapshot_sha256=f"git-{candidate.commit_oid}",
    )


def _github_snapshot() -> GitHubSurfaceSnapshot:
    return GitHubSurfaceSnapshot(
        repository=RepositoryIdentity("pureliture", "harnesskit", "read"),
        releases=(),
        release_assets=(),
        open_pull_requests=(),
        closed_pull_requests=(),
        values=(),
        coverage=GitHubCoverage(
            complete=True,
            release_count=0,
            release_asset_count=0,
            open_pull_request_count=0,
            closed_pull_request_count=0,
            minimum_rate_limit_remaining=50,
            stabilized_read_count=2,
        ),
        snapshot_sha256="github-snapshot",
    )


def test_gate1_composes_manifest_candidate_and_sanitized_digest_report(tmp_path: Path) -> None:
    baseline, source, baseline_commit = repositories(tmp_path)
    selected_policy = policy()
    report_path = tmp_path / "private-report" / "gate1.json"
    report_path.parent.mkdir()
    stages: list[str] = []

    outcome = run_gate1(
        source_root=source,
        public_clone_root=baseline,
        policy=selected_policy,
        allowlist=ReviewedSanitationAllowlist.empty(),
        identity=CandidateIdentity(
            name="HarnessKit Contributors",
            email="contributors@users.noreply.github.com",
            timestamp="2026-07-20T00:00:00+00:00",
        ),
        commit_message="feat: publish HarnessKit desktop application",
        report_path=report_path,
        remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
        generator_runner=_write_catalog,
        git_surface_reader=lambda _root, *, candidate: _git_snapshot(
            candidate, baseline_commit
        ),
        github_surface_reader=_github_snapshot,
        stage_reporter=stages.append,
    )

    assert outcome.qualified is True
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["qualified"] is True
    assert report["candidate_commit_oid"] == outcome.candidate_commit_oid
    assert report["github_minimum_rate_limit_remaining"] == 50
    serialized = report_path.read_text(encoding="utf-8")
    assert str(source) not in serialized
    assert str(baseline) not in serialized
    assert stages == [
        "remote-default",
        "manifest",
        "baseline-git-coverage",
        "materialization",
        "regeneration",
        "candidate-build",
        "candidate-git-coverage",
        "git-remote-drift",
        "github-coverage",
        "sanitation-coverage",
    ]


def test_gate1_writes_digest_only_failure_report_before_sanitizer(tmp_path: Path) -> None:
    baseline, source, baseline_commit = repositories(tmp_path)
    report_path = tmp_path / "private-report" / "gate1-failure.json"
    report_path.parent.mkdir()
    private_marker = "person-name-private-marker"

    def fail_git_coverage(_root, *, candidate):
        raise RuntimeError(f"{private_marker}:{source}:{candidate.commit_oid}")

    with pytest.raises(Gate1Error) as raised:
        run_gate1(
            source_root=source,
            public_clone_root=baseline,
            policy=policy(),
            allowlist=ReviewedSanitationAllowlist.empty(),
            identity=CandidateIdentity(
                name="HarnessKit Contributors",
                email="contributors@users.noreply.github.com",
                timestamp="2026-07-20T00:00:00+00:00",
            ),
            commit_message="feat: publish HarnessKit desktop application",
            report_path=report_path,
            remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
            generator_runner=_write_catalog,
            git_surface_reader=fail_git_coverage,
            github_surface_reader=_github_snapshot,
        )

    assert raised.value.outcome is not None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["qualified"] is False
    assert report["failure_code"] == "baseline-git-coverage"
    assert report["remote_mutation_count"] == 0
    serialized = report_path.read_text(encoding="utf-8")
    assert private_marker not in serialized
    assert str(source) not in serialized
    assert str(baseline) not in serialized


def test_gate1_rejects_report_path_inside_source_without_creating_parent(tmp_path: Path) -> None:
    baseline, source, baseline_commit = repositories(tmp_path)
    report_path = source / "new-private-report" / "gate1.json"

    with pytest.raises(Gate1Error, match="outside source repositories"):
        run_gate1(
            source_root=source,
            public_clone_root=baseline,
            policy=policy(),
            allowlist=ReviewedSanitationAllowlist.empty(),
            identity=CandidateIdentity(
                name="HarnessKit Contributors",
                email="contributors@users.noreply.github.com",
                timestamp="2026-07-20T00:00:00+00:00",
            ),
            commit_message="feat: publish HarnessKit desktop application",
            report_path=report_path,
            remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
            generator_runner=_write_catalog,
            git_surface_reader=lambda _root, *, candidate: _git_snapshot(
                candidate, baseline_commit
            ),
            github_surface_reader=_github_snapshot,
        )

    assert report_path.parent.exists() is False


def test_gate1_rejects_report_path_with_symlinked_ancestor(tmp_path: Path) -> None:
    baseline, source, baseline_commit = repositories(tmp_path)
    external = tmp_path / "external-report-root"
    (external / "nested").mkdir(parents=True)
    link = tmp_path / "report-link"
    link.symlink_to(external, target_is_directory=True)
    report_path = link / "nested" / "gate1.json"

    with pytest.raises(Gate1Error, match="no-follow"):
        run_gate1(
            source_root=source,
            public_clone_root=baseline,
            policy=policy(),
            allowlist=ReviewedSanitationAllowlist.empty(),
            identity=CandidateIdentity(
                name="HarnessKit Contributors",
                email="contributors@users.noreply.github.com",
                timestamp="2026-07-20T00:00:00+00:00",
            ),
            commit_message="feat: publish HarnessKit desktop application",
            report_path=report_path,
            remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
            generator_runner=_write_catalog,
            git_surface_reader=lambda _root, *, candidate: _git_snapshot(
                candidate, baseline_commit
            ),
            github_surface_reader=_github_snapshot,
        )

    assert (external / "nested" / "gate1.json").exists() is False


def test_gate1_cli_emits_only_sanitized_failure_without_exception_chain(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    private_marker = "/" + "Users/private-person/internal-checkout"
    source = tmp_path / "source"
    public = tmp_path / "public"
    source.mkdir()
    public.mkdir()

    def fail_gate1(**_arguments):
        try:
            raise RuntimeError(private_marker)
        except RuntimeError as cause:
            raise Gate1Error("Gate 1 stage failed: github-coverage") from cause

    monkeypatch.setattr(gate1_module, "run_gate1", fail_gate1)
    monkeypatch.setattr(gate1_module, "REPO_ROOT", source)
    monkeypatch.setattr(
        gate1_module,
        "_source_timestamp",
        lambda _root: "2026-07-20T00:00:00+00:00",
    )
    monkeypatch.setattr(
        gate1_module,
        "_github_surface_reader_from_environment",
        lambda: _github_snapshot,
    )
    monkeypatch.setattr(gate1_module, "load_policy", lambda _path: policy())
    monkeypatch.setattr(
        gate1_module,
        "load_reviewed_allowlist",
        lambda _path: ReviewedSanitationAllowlist.empty(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "public-port-gate1",
            "--public-clone-root",
            str(public),
        ],
    )

    assert gate1_module.main() == 1
    captured = capsys.readouterr()
    assert private_marker not in captured.out
    assert private_marker not in captured.err
    assert "Traceback" not in captured.err
    assert '"qualified": false' in captured.err


def test_gate1_preflight_accepts_standard_gh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, str | None] = {}

    class FakeClient:
        def __init__(self, *, owner, repository, token):
            observed.update(owner=owner, repository=repository, token=token)

        def read_repository_identity(self):
            return RepositoryIdentity("pureliture", "harnesskit", "write")

        def read_core_rate_limit_remaining(self):
            return 50

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr(gate1_module, "GitHubRestClient", FakeClient)
    monkeypatch.setattr(
        gate1_module,
        "read_github_surface",
        lambda _client: _github_snapshot(),
    )

    reader = gate1_module._github_surface_reader_from_environment()

    assert observed == {
        "owner": "pureliture",
        "repository": "harnesskit",
        "token": "test-token",
    }
    assert reader().snapshot_sha256 == "github-snapshot"


def test_gate1_preflight_fails_before_projection_when_rate_limit_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExhaustedClient:
        def __init__(self, **_arguments):
            pass

        def read_repository_identity(self):
            return RepositoryIdentity("pureliture", "harnesskit", "write")

        def read_core_rate_limit_remaining(self):
            return 0

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(gate1_module, "GitHubRestClient", ExhaustedClient)

    with pytest.raises(
        gate1_module.Gate1PreflightError,
        match="github-rate-limit-exhausted",
    ):
        gate1_module._github_surface_reader_from_environment()
