from __future__ import annotations

import json

import pytest

from scripts.public_port.github_surface import (
    GitHubRestClient,
    GitHubSurfaceError,
    HttpResponse,
)


class FakeTransport:
    def __init__(self, responses: dict[str, HttpResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, headers: dict[str, str]) -> HttpResponse:
        self.requests.append((url, headers))
        return self.responses[url]


def _response(url: str, body, *, link: str | None = None) -> HttpResponse:
    headers = {
        "content-type": "application/json",
        "x-ratelimit-remaining": "50",
    }
    if link is not None:
        headers["link"] = link
    return HttpResponse(
        status=200,
        headers=headers,
        body=json.dumps(body).encode("utf-8"),
        final_url=url,
    )


def _authenticated_repository() -> dict[str, object]:
    return {
        "owner": {"login": "pureliture"},
        "name": "harnesskit",
        "private": False,
        "permissions": {"pull": True, "push": True},
    }


def test_authenticated_repository_identity_qualifies_read_permission() -> None:
    url = "https://api.github.com/repos/pureliture/harnesskit"
    client = GitHubRestClient(
        owner="pureliture",
        repository="harnesskit",
        transport=FakeTransport(
            {url: _response(url, _authenticated_repository())}
        ),
        token="review-token",
    )

    identity = client.read_repository_identity()

    assert identity.owner == "pureliture"
    assert identity.name == "harnesskit"
    assert identity.permission == "write"


def test_reads_authoritative_core_rate_limit_state() -> None:
    url = "https://api.github.com/rate_limit"
    client = GitHubRestClient(
        owner="pureliture",
        repository="harnesskit",
        transport=FakeTransport(
            {
                url: _response(
                    url,
                    {"resources": {"core": {"remaining": 0}}},
                )
            }
        ),
        token="review-token",
    )

    assert client.read_core_rate_limit_remaining() == 0


def test_public_repository_without_authenticated_permission_fails_closed() -> None:
    url = "https://api.github.com/repos/pureliture/harnesskit"
    client = GitHubRestClient(
        owner="pureliture",
        repository="harnesskit",
        transport=FakeTransport(
            {
                url: _response(
                    url,
                    {
                        "owner": {"login": "pureliture"},
                        "name": "harnesskit",
                        "private": False,
                    },
                )
            }
        ),
    )

    with pytest.raises(GitHubSurfaceError, match="authenticated"):
        client.read_repository_identity()


def test_pull_only_permission_cannot_claim_draft_release_visibility() -> None:
    url = "https://api.github.com/repos/pureliture/harnesskit"
    client = GitHubRestClient(
        owner="pureliture",
        repository="harnesskit",
        token="review-token",
        transport=FakeTransport(
            {
                url: _response(
                    url,
                    {
                        "owner": {"login": "pureliture"},
                        "name": "harnesskit",
                        "private": False,
                        "permissions": {"pull": True, "push": False},
                    },
                )
            }
        ),
    )

    with pytest.raises(GitHubSurfaceError, match="draft release"):
        client.read_repository_identity()


def test_parses_release_and_nested_asset_pages_with_valid_next_cursor() -> None:
    release_url = "https://api.github.com/repos/pureliture/harnesskit/releases?per_page=100"
    next_url = f"{release_url}&page=2"
    asset_url = "https://api.github.com/repos/pureliture/harnesskit/releases/7/assets?per_page=100"
    transport = FakeTransport(
        {
            "https://api.github.com/repos/pureliture/harnesskit": _response(
                "https://api.github.com/repos/pureliture/harnesskit",
                _authenticated_repository(),
            ),
            release_url: _response(
                release_url,
                [{"id": 7, "tag_name": "v1", "name": "v1", "body": "note"}],
                link=f'<{next_url}>; rel="next"',
            ),
            asset_url: _response(
                asset_url,
                [
                    {
                        "id": 9,
                        "name": "HarnessKit.dmg",
                        "size": 3,
                        "url": "https://api.github.com/repos/pureliture/harnesskit/releases/assets/9",
                    }
                ],
            ),
        }
    )
    client = GitHubRestClient(
        owner="pureliture", repository="harnesskit", transport=transport, token="review-token"
    )
    client.read_repository_identity()

    release_page = client.list_releases(None)
    asset_page = client.list_release_assets("7", None)

    assert release_page.next_cursor == next_url
    assert release_page.reported_total is None
    assert release_page.permission_verified is True
    assert asset_page.items[0].name == "HarnessKit.dmg"


def test_rejects_next_cursor_outside_official_github_api() -> None:
    release_url = "https://api.github.com/repos/pureliture/harnesskit/releases?per_page=100"
    transport = FakeTransport(
        {
            "https://api.github.com/repos/pureliture/harnesskit": _response(
                "https://api.github.com/repos/pureliture/harnesskit",
                _authenticated_repository(),
            ),
            release_url: _response(
                release_url,
                [],
                link='<https://example.com/steal?page=2>; rel="next"',
            ),
        }
    )
    client = GitHubRestClient(
        owner="pureliture", repository="harnesskit", transport=transport, token="review-token"
    )
    client.read_repository_identity()

    with pytest.raises(GitHubSurfaceError, match="pagination URL"):
        client.list_releases(None)


def test_rejects_missing_rate_limit_evidence() -> None:
    url = "https://api.github.com/repos/pureliture/harnesskit"
    response = _response(
        url,
        _authenticated_repository(),
    )
    response = HttpResponse(
        status=response.status,
        headers={"content-type": "application/json"},
        body=response.body,
        final_url=response.final_url,
    )
    client = GitHubRestClient(
        owner="pureliture",
        repository="harnesskit",
        transport=FakeTransport({url: response}),
        token="review-token",
    )

    with pytest.raises(GitHubSurfaceError, match="rate-limit"):
        client.read_repository_identity()


def test_release_asset_download_uses_octet_stream_and_exact_registered_url() -> None:
    repo_url = "https://api.github.com/repos/pureliture/harnesskit"
    assets_url = "https://api.github.com/repos/pureliture/harnesskit/releases/7/assets?per_page=100"
    asset_url = "https://api.github.com/repos/pureliture/harnesskit/releases/assets/9"
    download_url = "https://release-assets.githubusercontent.com/download/9"
    transport = FakeTransport(
        {
            repo_url: _response(
                repo_url,
                _authenticated_repository(),
            ),
            assets_url: _response(
                assets_url,
                [{"id": 9, "name": "HarnessKit.dmg", "size": 3, "url": asset_url}],
            ),
            asset_url: HttpResponse(
                status=302,
                headers={"location": download_url, "x-ratelimit-remaining": "48"},
                body=b"",
                final_url=asset_url,
            ),
            download_url: HttpResponse(
                status=200,
                headers={"content-type": "application/octet-stream"},
                body=b"dmg",
                final_url=download_url,
            ),
        }
    )
    client = GitHubRestClient(
        owner="pureliture", repository="harnesskit", transport=transport, token="review-token"
    )
    client.read_repository_identity()
    client.list_release_assets("7", None)

    assert client.read_release_asset("9") == b"dmg"
    assert transport.requests[-1][1]["Accept"] == "application/octet-stream"
    assert "Authorization" not in transport.requests[-1][1]


def test_rejects_asset_redirect_before_sending_token_to_untrusted_host() -> None:
    repo_url = "https://api.github.com/repos/pureliture/harnesskit"
    assets_url = "https://api.github.com/repos/pureliture/harnesskit/releases/7/assets?per_page=100"
    asset_url = "https://api.github.com/repos/pureliture/harnesskit/releases/assets/9"
    transport = FakeTransport(
        {
            repo_url: _response(repo_url, _authenticated_repository()),
            assets_url: _response(
                assets_url,
                [{"id": 9, "name": "HarnessKit.dmg", "size": 3, "url": asset_url}],
            ),
            asset_url: HttpResponse(
                status=302,
                headers={
                    "location": "https://example.com/download/9",
                    "x-ratelimit-remaining": "48",
                },
                body=b"",
                final_url=asset_url,
            ),
        }
    )
    client = GitHubRestClient(
        owner="pureliture", repository="harnesskit", transport=transport, token="review-token"
    )
    client.read_repository_identity()
    client.list_release_assets("7", None)

    with pytest.raises(GitHubSurfaceError, match="untrusted host"):
        client.read_release_asset("9")

    assert [request[0] for request in transport.requests] == [repo_url, assets_url, asset_url]


def test_release_asset_api_redirect_requires_rate_limit_evidence() -> None:
    repo_url = "https://api.github.com/repos/pureliture/harnesskit"
    assets_url = "https://api.github.com/repos/pureliture/harnesskit/releases/7/assets?per_page=100"
    asset_url = "https://api.github.com/repos/pureliture/harnesskit/releases/assets/9"
    download_url = "https://release-assets.githubusercontent.com/download/9"
    transport = FakeTransport(
        {
            repo_url: _response(repo_url, _authenticated_repository()),
            assets_url: _response(
                assets_url,
                [{"id": 9, "name": "HarnessKit.dmg", "size": 3, "url": asset_url}],
            ),
            asset_url: HttpResponse(
                status=302,
                headers={"location": download_url},
                body=b"",
                final_url=asset_url,
            ),
        }
    )
    client = GitHubRestClient(
        owner="pureliture", repository="harnesskit", transport=transport, token="review-token"
    )
    client.read_repository_identity()
    client.list_release_assets("7", None)

    with pytest.raises(GitHubSurfaceError, match="rate-limit"):
        client.read_release_asset("9")


def test_rejects_pagination_cursor_for_different_repository_collection() -> None:
    release_url = "https://api.github.com/repos/pureliture/harnesskit/releases?per_page=100"
    client = GitHubRestClient(
        owner="pureliture",
        repository="harnesskit",
        token="review-token",
        transport=FakeTransport(
            {
                "https://api.github.com/repos/pureliture/harnesskit": _response(
                    "https://api.github.com/repos/pureliture/harnesskit",
                    _authenticated_repository(),
                ),
                release_url: _response(
                    release_url,
                    [],
                    link=(
                        '<https://api.github.com/repos/pureliture/other/releases?per_page=100&page=2>; '
                        'rel="next"'
                    ),
                ),
            }
        ),
    )
    client.read_repository_identity()

    with pytest.raises(GitHubSurfaceError, match="collection"):
        client.list_releases(None)
