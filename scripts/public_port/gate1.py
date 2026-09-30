from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .candidate import CandidateIdentity, build_candidate
from .git_surface import CandidateGitObjects, GitSurfaceSnapshot, read_git_surface
from .github_surface import (
    GitHubRestClient,
    GitHubSurfaceError,
    GitHubSurfaceSnapshot,
    TraversalEvidence,
    read_github_surface,
)
from .git_environment import curated_git_environment
from .manifest import PublicRemoteDefaultReader, build_manifest, read_public_remote_default
from .materialize import materialize
from .policy import PublicPortPolicy
from .policy import load_policy
from .regenerate import GeneratorRunner, regenerate
from .sanitize import (
    ReviewedSanitationAllowlist,
    SanitationError,
    SanitationReport,
    audit_public_surface,
    load_reviewed_allowlist,
)
from .secure_tree import (
    SecureTreeError,
    atomic_write_file_at,
    open_absolute_directory_no_follow,
)


class Gate1Error(ValueError):
    """Gate 1 did not qualify. The attached outcome contains digest-only evidence."""

    def __init__(
        self,
        message: str,
        *,
        outcome: Gate1Outcome | Gate1FailureOutcome | None = None,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome


class Gate1PreflightError(ValueError):
    """Gate 1 cannot start safely with the current local authentication state."""

    def __init__(self, failure_code: str) -> None:
        super().__init__(failure_code)
        self.failure_code = failure_code


@dataclass(frozen=True)
class Gate1Outcome:
    schema_version: int
    qualified: bool
    manifest_sha256: str
    expected_tree_sha256: str
    candidate_commit_oid: str
    candidate_tag_oid: str | None
    baseline_git_snapshot_sha256: str
    candidate_git_snapshot_sha256: str
    github_snapshot_sha256: str
    baseline_findings_sha256: str
    candidate_findings_sha256: str
    github_findings_sha256: str
    allowlist_sha256: str
    finding_count: int
    github_traversals: tuple[TraversalEvidence, ...]
    github_minimum_rate_limit_remaining: int
    remote_mutation_count: int = 0


@dataclass(frozen=True)
class Gate1FailureOutcome:
    schema_version: int
    qualified: bool
    failure_code: str
    manifest_sha256: str | None
    expected_tree_sha256: str | None
    candidate_commit_oid: str | None
    candidate_tag_oid: str | None
    baseline_git_snapshot_sha256: str | None
    candidate_git_snapshot_sha256: str | None
    github_snapshot_sha256: str | None
    github_traversals: tuple[TraversalEvidence, ...]
    github_minimum_rate_limit_remaining: int | None
    remote_mutation_count: int = 0


GitSurfaceReader = Callable[..., GitSurfaceSnapshot]
GitHubSurfaceReader = Callable[[], GitHubSurfaceSnapshot]
StageReporter = Callable[[str], None]

REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_TIMESTAMP_TIMEOUT_SECONDS = 30


def _source_timestamp(source_root: Path) -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(source_root),
                "show",
                "-s",
                "--format=%cI",
                "HEAD",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=curated_git_environment(),
            timeout=_SOURCE_TIMESTAMP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise Gate1PreflightError("source-timestamp-timeout") from error
    timestamp = result.stdout.strip()
    if result.returncode != 0 or not timestamp:
        raise Gate1PreflightError("source-timestamp-unavailable")
    return timestamp


def _github_surface_reader_from_environment() -> GitHubSurfaceReader:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise Gate1PreflightError("github-token-missing")
    client = GitHubRestClient(
        owner="pureliture",
        repository="harnesskit",
        token=token,
    )
    try:
        client.read_repository_identity()
        remaining = client.read_core_rate_limit_remaining()
    except GitHubSurfaceError as error:
        raise Gate1PreflightError("github-preflight-failed") from error
    if remaining == 0:
        raise Gate1PreflightError("github-rate-limit-exhausted")
    return lambda: read_github_surface(client)


def _is_nested(candidate: Path, root: Path) -> bool:
    return candidate == root or candidate.is_relative_to(root)


def _write_report(
    path: Path,
    outcome: Gate1Outcome | Gate1FailureOutcome,
    *,
    forbidden_roots: tuple[Path, ...],
) -> None:
    destination = Path(path)
    if not destination.is_absolute():
        raise Gate1Error("Gate 1 report path must be absolute", outcome=outcome)
    canonical = Path(os.path.abspath(destination))
    if canonical != destination:
        raise Gate1Error("Gate 1 report path must be canonical", outcome=outcome)
    if any(_is_nested(canonical, root.resolve(strict=True)) for root in forbidden_roots):
        raise Gate1Error("Gate 1 private report must be outside source repositories", outcome=outcome)
    body = (
        json.dumps(asdict(outcome), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    parent_descriptor = -1
    try:
        parent_descriptor = open_absolute_directory_no_follow(canonical.parent)
        atomic_write_file_at(parent_descriptor, canonical.name, body, mode=0o600)
    except SecureTreeError as error:
        raise Gate1Error(
            "Gate 1 report path violates the no-follow contract",
            outcome=outcome,
        ) from error
    finally:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _outcome(
    *,
    manifest,
    candidate: CandidateGitObjects,
    baseline_git: GitSurfaceSnapshot,
    candidate_git: GitSurfaceSnapshot,
    github: GitHubSurfaceSnapshot,
    sanitation: SanitationReport,
) -> Gate1Outcome:
    return Gate1Outcome(
        schema_version=1,
        qualified=sanitation.qualified,
        manifest_sha256=manifest["manifest_sha256"],
        expected_tree_sha256=manifest["expected_tree_sha256"],
        candidate_commit_oid=candidate.commit_oid,
        candidate_tag_oid=candidate.tag_oid,
        baseline_git_snapshot_sha256=baseline_git.snapshot_sha256,
        candidate_git_snapshot_sha256=candidate_git.snapshot_sha256,
        github_snapshot_sha256=github.snapshot_sha256,
        baseline_findings_sha256=sanitation.baseline_findings_sha256,
        candidate_findings_sha256=sanitation.candidate_findings_sha256,
        github_findings_sha256=sanitation.github_findings_sha256,
        allowlist_sha256=sanitation.allowlist_sha256,
        finding_count=len(sanitation.findings),
        github_traversals=github.coverage.traversals,
        github_minimum_rate_limit_remaining=(
            github.coverage.minimum_rate_limit_remaining
        ),
    )


def _failure_outcome(
    *,
    failure_code: str,
    manifest,
    candidate: CandidateGitObjects | None,
    baseline_git: GitSurfaceSnapshot | None,
    candidate_git: GitSurfaceSnapshot | None,
    github: GitHubSurfaceSnapshot | None,
) -> Gate1FailureOutcome:
    return Gate1FailureOutcome(
        schema_version=1,
        qualified=False,
        failure_code=failure_code,
        manifest_sha256=(manifest["manifest_sha256"] if manifest is not None else None),
        expected_tree_sha256=(
            manifest["expected_tree_sha256"] if manifest is not None else None
        ),
        candidate_commit_oid=(candidate.commit_oid if candidate is not None else None),
        candidate_tag_oid=(candidate.tag_oid if candidate is not None else None),
        baseline_git_snapshot_sha256=(
            baseline_git.snapshot_sha256 if baseline_git is not None else None
        ),
        candidate_git_snapshot_sha256=(
            candidate_git.snapshot_sha256 if candidate_git is not None else None
        ),
        github_snapshot_sha256=(github.snapshot_sha256 if github is not None else None),
        github_traversals=(github.coverage.traversals if github is not None else ()),
        github_minimum_rate_limit_remaining=(
            github.coverage.minimum_rate_limit_remaining
            if github is not None
            else None
        ),
    )


def run_gate1(
    *,
    source_root: Path,
    public_clone_root: Path,
    policy: PublicPortPolicy,
    allowlist: ReviewedSanitationAllowlist,
    identity: CandidateIdentity,
    commit_message: str,
    report_path: Path,
    remote_default_reader: PublicRemoteDefaultReader = read_public_remote_default,
    generator_runner: GeneratorRunner | None = None,
    git_surface_reader: GitSurfaceReader = read_git_surface,
    github_surface_reader: GitHubSurfaceReader | None = None,
    stage_reporter: StageReporter | None = None,
) -> Gate1Outcome:
    """Close Gate 1 locally. It creates no remote refs, pushes, releases, or assets."""

    source = Path(source_root).resolve(strict=True)
    public_clone = Path(public_clone_root).resolve(strict=True)
    stage = "not-started"
    manifest = None
    baseline_git = None
    candidate = None
    candidate_git = None
    github = None

    def enter(next_stage: str) -> None:
        nonlocal stage
        stage = next_stage
        if stage_reporter is not None:
            stage_reporter(stage)

    try:
        enter("remote-default")
        remote_default = remote_default_reader(policy.target_repository_url)
        enter("manifest")
        manifest = build_manifest(
            policy,
            baseline_root=public_clone,
            baseline_commit=remote_default.commit,
            source_root=source,
            remote_default_reader=remote_default_reader,
        )

        enter("baseline-git-coverage")
        baseline_git = git_surface_reader(
            public_clone,
            candidate=CandidateGitObjects(commit_oid=remote_default.commit),
        )
        enter("materialization")
        materialize(
            manifest,
            policy=policy,
            source_root=source,
            public_clone_root=public_clone,
            remote_default_reader=remote_default_reader,
        )
        enter("regeneration")
        regenerate(
            manifest,
            policy=policy,
            source_root=source,
            public_clone_root=public_clone,
            generator_runner=generator_runner,
        )
        enter("candidate-build")
        candidate = build_candidate(
            public_clone,
            manifest=manifest,
            identity=identity,
            message=commit_message,
        )
        enter("candidate-git-coverage")
        candidate_git = git_surface_reader(public_clone, candidate=candidate)
        enter("git-remote-drift")
        if candidate_git.refs != baseline_git.refs:
            raise Gate1Error("public Git ref namespace drifted during Gate 1")

        enter("github-coverage")
        if github_surface_reader is None:
            client = GitHubRestClient(
                owner="pureliture",
                repository="harnesskit",
                token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
            )
            github = read_github_surface(client)
        else:
            github = github_surface_reader()

        enter("sanitation-coverage")
        sanitation = audit_public_surface(
            baseline=baseline_git.as_public_surface_snapshot(name="baseline"),
            candidate=candidate_git.as_public_surface_snapshot(name="candidate"),
            github=github.as_public_surface_snapshot(),
            allowlist=allowlist,
        )
        outcome = _outcome(
            manifest=manifest,
            candidate=candidate,
            baseline_git=baseline_git,
            candidate_git=candidate_git,
            github=github,
            sanitation=sanitation,
        )
        _write_report(report_path, outcome, forbidden_roots=(source, public_clone))
        return outcome
    except SanitationError as error:
        if error.report is not None:
            assert manifest is not None
            assert candidate is not None
            assert baseline_git is not None
            assert candidate_git is not None
            assert github is not None
            blocked = _outcome(
                manifest=manifest,
                candidate=candidate,
                baseline_git=baseline_git,
                candidate_git=candidate_git,
                github=github,
                sanitation=error.report,
            )
            _write_report(report_path, blocked, forbidden_roots=(source, public_clone))
            raise Gate1Error(
                "Gate 1 sanitation found unreviewed residue",
                outcome=blocked,
            ) from error
        failure = _failure_outcome(
            failure_code=stage,
            manifest=manifest,
            candidate=candidate,
            baseline_git=baseline_git,
            candidate_git=candidate_git,
            github=github,
        )
        _write_report(report_path, failure, forbidden_roots=(source, public_clone))
        raise Gate1Error(f"Gate 1 stage failed: {stage}", outcome=failure) from error
    except Gate1Error as error:
        if error.outcome is not None:
            raise
        failure = _failure_outcome(
            failure_code=stage,
            manifest=manifest,
            candidate=candidate,
            baseline_git=baseline_git,
            candidate_git=candidate_git,
            github=github,
        )
        _write_report(report_path, failure, forbidden_roots=(source, public_clone))
        raise Gate1Error(f"Gate 1 stage failed: {stage}", outcome=failure) from error
    except Exception as error:
        failure = _failure_outcome(
            failure_code=stage,
            manifest=manifest,
            candidate=candidate,
            baseline_git=baseline_git,
            candidate_git=candidate_git,
            github=github,
        )
        _write_report(report_path, failure, forbidden_roots=(source, public_clone))
        raise Gate1Error(f"Gate 1 stage failed: {stage}", outcome=failure) from error


def _main_failure_code(error: BaseException) -> str:
    if (
        isinstance(error, Gate1Error)
        and isinstance(error.outcome, Gate1FailureOutcome)
    ):
        return error.outcome.failure_code
    if isinstance(error, Gate1PreflightError):
        return error.failure_code
    if isinstance(error, Gate1Error):
        return "gate1-failed"
    return "input-validation-failed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Qualify HarnessKit public-port Gate 1 locally")
    parser.add_argument("--source-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--public-clone-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--allowlist", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--timestamp")
    parser.add_argument(
        "--message",
        default="feat: publish HarnessKit desktop application",
    )
    arguments = parser.parse_args()

    def report_stage(stage: str) -> None:
        print(
            json.dumps(
                {"gate": "public-port", "stage": stage, "status": "running"},
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )

    try:
        source_root = arguments.source_root.resolve(strict=True)
        public_clone_root = arguments.public_clone_root.resolve(strict=True)
        policy_path = arguments.policy or source_root / "publish/public-port.yml"
        allowlist_path = (
            arguments.allowlist
            or source_root / "publish/public-sanitation-allowlist.yml"
        )
        report_path = arguments.report or (
            public_clone_root.parent
            / f"{public_clone_root.name}-public-port-gate1.json"
        )
        report_stage("source-preflight")
        timestamp = arguments.timestamp or _source_timestamp(source_root)
        report_stage("github-preflight")
        github_surface_reader = _github_surface_reader_from_environment()
        outcome = run_gate1(
            source_root=source_root,
            public_clone_root=public_clone_root,
            policy=load_policy(policy_path),
            allowlist=load_reviewed_allowlist(allowlist_path),
            identity=CandidateIdentity(
                name="HarnessKit Contributors",
                email="contributors@users.noreply.github.com",
                timestamp=timestamp,
            ),
            commit_message=arguments.message,
            report_path=report_path,
            github_surface_reader=github_surface_reader,
            stage_reporter=report_stage,
        )
    except (Gate1Error, Gate1PreflightError, ValueError, OSError) as error:
        failure_code = _main_failure_code(error)
        print(
            json.dumps(
                {"qualified": False, "failure_code": failure_code},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "qualified": outcome.qualified,
                "candidate_commit_oid": outcome.candidate_commit_oid,
                "manifest_sha256": outcome.manifest_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
