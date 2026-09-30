from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.public_port.github_surface import (
    GitHubSurfaceError,
    Page,
    PullRequestMetadata,
    ReleaseAssetMetadata,
    ReleaseMetadata,
    RepositoryIdentity,
    read_github_surface,
)


class FakeGitHubClient:
    def __init__(self) -> None:
        self.mutate_second_read = False
        self.release_reads = 0

    def read_repository_identity(self) -> RepositoryIdentity:
        return RepositoryIdentity(
            owner="pureliture",
            name="harnesskit",
            permission="read",
        )

    def list_releases(self, cursor: str | None) -> Page[ReleaseMetadata]:
        if cursor is None:
            self.release_reads += 1
            name = "v1" if not self.mutate_second_read or self.release_reads == 1 else "changed"
            return Page(
                items=(ReleaseMetadata("release-1", "v1", name, "public note", "release-author"),),
                next_cursor="release-page-2",
                reported_total=2,
                permission_verified=True,
                rate_limit_remaining=100,
            )
        return Page(
            items=(ReleaseMetadata("release-2", "v0", "v0", "older note"),),
            next_cursor=None,
            reported_total=2,
            permission_verified=True,
            rate_limit_remaining=99,
        )

    def list_release_assets(
        self, release_id: str, cursor: str | None
    ) -> Page[ReleaseAssetMetadata]:
        assets = {
            "release-1": (ReleaseAssetMetadata("asset-1", "HarnessKit.dmg", 3),),
            "release-2": (),
        }[release_id]
        return Page(
            items=assets,
            next_cursor=None,
            reported_total=len(assets),
            permission_verified=True,
            rate_limit_remaining=98,
        )

    def read_release_asset(self, asset_id: str) -> bytes:
        assert asset_id == "asset-1"
        return b"dmg"

    def list_pull_requests(
        self, *, state: str, cursor: str | None
    ) -> Page[PullRequestMetadata]:
        assert cursor is None
        number = 1 if state == "open" else 2
        return Page(
            items=(
                PullRequestMetadata(
                    number=number,
                    state=state,
                    title=f"{state} title",
                    body=f"{state} body",
                    author="contributor",
                    head_ref="feature",
                    base_ref="main",
                    head_repository_url="https://github.com/example/fork",
                ),
            ),
            next_cursor=None,
            reported_total=1,
            permission_verified=True,
            rate_limit_remaining=97,
        )

    def minimum_rate_limit_remaining(self) -> int:
        return 96


def test_reads_releases_nested_assets_and_open_closed_prs_to_exhaustion() -> None:
    snapshot = read_github_surface(FakeGitHubClient())

    assert snapshot.coverage.complete is True
    assert snapshot.coverage.release_count == 2
    assert snapshot.coverage.release_asset_count == 1
    assert snapshot.coverage.open_pull_request_count == 1
    assert snapshot.coverage.closed_pull_request_count == 1
    assert snapshot.coverage.minimum_rate_limit_remaining == 96
    assert any(value.surface == "github.release.asset" and value.value == b"dmg" for value in snapshot.values)
    assert any(
        value.surface == "github.release.author_name" and value.value == b"release-author"
        for value in snapshot.values
    )
    assert tuple(evidence.label for evidence in snapshot.coverage.traversals) == (
        "releases",
        "release assets for release-1",
        "release assets for release-2",
        "open pull requests",
        "closed pull requests",
    )
    release_evidence = snapshot.coverage.traversals[0]
    assert release_evidence.observed_count == 2
    assert release_evidence.page_count == 2
    assert release_evidence.exhausted is True
    assert release_evidence.final_cursor_sha256
    assert release_evidence.permission_verified is True
    assert release_evidence.minimum_rate_limit_remaining == 99
    assert release_evidence.reported_total == 2


def test_preserves_terminal_pagination_evidence_when_total_is_unreported() -> None:
    client = FakeGitHubClient()
    original = client.list_pull_requests

    def unreported(*, state, cursor):
        return replace(original(state=state, cursor=cursor), reported_total=None)

    client.list_pull_requests = unreported  # type: ignore[method-assign]
    snapshot = read_github_surface(client)

    evidence = next(
        item for item in snapshot.coverage.traversals if item.label == "open pull requests"
    )
    assert evidence.reported_total is None
    assert evidence.observed_count == 1
    assert evidence.page_count == 1
    assert evidence.exhausted is True
    assert evidence.final_cursor_sha256


def test_rejects_permission_ambiguity() -> None:
    client = FakeGitHubClient()
    original = client.list_releases

    def denied(cursor):
        return replace(original(cursor), permission_verified=False)

    client.list_releases = denied  # type: ignore[method-assign]
    with pytest.raises(GitHubSurfaceError, match="permission"):
        read_github_surface(client)


def test_rejects_reported_count_mismatch() -> None:
    client = FakeGitHubClient()
    original = client.list_releases

    def truncated(cursor):
        return replace(original(cursor), reported_total=3)

    client.list_releases = truncated  # type: ignore[method-assign]
    with pytest.raises(GitHubSurfaceError, match="count"):
        read_github_surface(client)


def test_rejects_repeated_cursor() -> None:
    client = FakeGitHubClient()

    def repeated(_cursor):
        return Page(
            items=(),
            next_cursor="same",
            reported_total=0,
            permission_verified=True,
            rate_limit_remaining=10,
        )

    client.list_releases = repeated  # type: ignore[method-assign]
    with pytest.raises(GitHubSurfaceError, match="cursor"):
        read_github_surface(client)


def test_rejects_duplicate_release_identity_across_pages() -> None:
    client = FakeGitHubClient()

    def duplicate(cursor):
        return Page(
            items=(ReleaseMetadata("release-1", "v1", "v1", "note"),),
            next_cursor="page-2" if cursor is None else None,
            reported_total=2,
            permission_verified=True,
            rate_limit_remaining=10,
        )

    client.list_releases = duplicate  # type: ignore[method-assign]

    with pytest.raises(GitHubSurfaceError, match="duplicate"):
        read_github_surface(client)


def test_rejects_concurrent_mutation_between_complete_reads() -> None:
    client = FakeGitHubClient()
    client.mutate_second_read = True

    with pytest.raises(GitHubSurfaceError, match="changed during observation"):
        read_github_surface(client)
