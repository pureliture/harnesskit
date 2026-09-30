from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Generic, Hashable, Mapping, Protocol, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .sanitize import PublicSurfaceSnapshot, SurfaceValue


class GitHubSurfaceError(ValueError):
    """GitHub metadata coverage is incomplete, unstable, or unauthorized."""


@dataclass(frozen=True)
class RepositoryIdentity:
    owner: str
    name: str
    permission: str


@dataclass(frozen=True)
class ReleaseMetadata:
    release_id: str
    tag_name: str
    name: str
    body: str
    author: str = ""


@dataclass(frozen=True)
class ReleaseAssetMetadata:
    asset_id: str
    name: str
    size: int
    uploader: str = ""


@dataclass(frozen=True)
class PullRequestMetadata:
    number: int
    state: str
    title: str
    body: str
    author: str
    head_ref: str
    base_ref: str
    head_repository_url: str


T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None
    reported_total: int | None
    permission_verified: bool
    rate_limit_remaining: int


class GitHubMetadataClient(Protocol):
    def read_repository_identity(self) -> RepositoryIdentity: ...

    def list_releases(self, cursor: str | None) -> Page[ReleaseMetadata]: ...

    def list_release_assets(
        self, release_id: str, cursor: str | None
    ) -> Page[ReleaseAssetMetadata]: ...

    def read_release_asset(self, asset_id: str) -> bytes: ...

    def list_pull_requests(
        self, *, state: str, cursor: str | None
    ) -> Page[PullRequestMetadata]: ...

    def minimum_rate_limit_remaining(self) -> int: ...


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str


class HttpTransport(Protocol):
    def get(self, url: str, *, headers: dict[str, str]) -> HttpResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class UrllibHttpTransport:
    def __init__(self, *, max_response_bytes: int = 512 * 1024 * 1024) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._max_response_bytes = max_response_bytes
        self._opener = build_opener(_NoRedirectHandler())

    def _read_response(self, response) -> bytes:
        body = response.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            raise GitHubSurfaceError("GitHub response exceeded the reviewed byte limit")
        return body

    def get(self, url: str, *, headers: dict[str, str]) -> HttpResponse:
        request = Request(url, headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=30) as response:
                return HttpResponse(
                    status=response.status,
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=self._read_response(response),
                    final_url=response.geturl(),
                )
        except HTTPError as error:
            if 300 <= error.code < 400:
                return HttpResponse(
                    status=error.code,
                    headers={key.lower(): value for key, value in error.headers.items()},
                    body=self._read_response(error),
                    final_url=error.geturl(),
                )
            raise GitHubSurfaceError(f"GitHub HTTP request failed with status {error.code}") from error
        except URLError as error:
            raise GitHubSurfaceError("GitHub HTTP request failed") from error


class GitHubRestClient:
    """Minimal read-only GitHub REST adapter with explicit pagination and rate-limit proof."""

    _API_ORIGIN = "https://api.github.com"
    _ASSET_HOSTS = {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }

    def __init__(
        self,
        *,
        owner: str,
        repository: str,
        transport: HttpTransport | None = None,
        token: str | None = None,
    ) -> None:
        if not owner or not repository or any("/" in value for value in (owner, repository)):
            raise ValueError("GitHub repository identity is invalid")
        self._owner = owner
        self._repository = repository
        self._transport = transport or UrllibHttpTransport()
        self._token = token
        self._permission_verified = False
        self._assets: dict[str, tuple[str, int]] = {}
        self._observed_api_rate_limits: list[int] = []

    @property
    def _repository_url(self) -> str:
        return f"{self._API_ORIGIN}/repos/{self._owner}/{self._repository}"

    def _headers(
        self,
        *,
        accept: str = "application/vnd.github+json",
        include_token: bool = True,
    ) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "HarnessKit-public-port-auditor",
        }
        if include_token and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _normalized_headers(response: HttpResponse) -> dict[str, str]:
        return {key.lower(): value for key, value in response.headers.items()}

    def _rate_limit(self, response: HttpResponse) -> int:
        raw = self._normalized_headers(response).get("x-ratelimit-remaining")
        if raw is None:
            raise GitHubSurfaceError("GitHub rate-limit evidence is missing")
        try:
            remaining = int(raw)
        except ValueError as error:
            raise GitHubSurfaceError("GitHub rate-limit evidence is invalid") from error
        if remaining < 0:
            raise GitHubSurfaceError("GitHub rate-limit evidence is invalid")
        self._observed_api_rate_limits.append(remaining)
        return remaining

    def minimum_rate_limit_remaining(self) -> int:
        if not self._observed_api_rate_limits:
            raise GitHubSurfaceError("GitHub rate-limit evidence is missing")
        return min(self._observed_api_rate_limits)

    def read_core_rate_limit_remaining(self) -> int:
        payload, _response = self._get_json(f"{self._API_ORIGIN}/rate_limit")
        resources = payload.get("resources") if isinstance(payload, dict) else None
        core = resources.get("core") if isinstance(resources, dict) else None
        remaining = core.get("remaining") if isinstance(core, dict) else None
        if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining < 0:
            raise GitHubSurfaceError("GitHub core rate-limit state is invalid")
        return remaining

    @staticmethod
    def _decode_json(response: HttpResponse):
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GitHubSurfaceError("GitHub JSON response is malformed") from error

    def _get_json(self, url: str) -> tuple[object, HttpResponse]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "api.github.com":
            raise GitHubSurfaceError("GitHub API URL is not canonical")
        response = self._transport.get(url, headers=self._headers())
        if response.status != 200:
            raise GitHubSurfaceError(f"GitHub API returned status {response.status}")
        if response.final_url != url:
            raise GitHubSurfaceError("GitHub API request redirected unexpectedly")
        self._rate_limit(response)
        return self._decode_json(response), response

    def _validate_api_collection_url(
        self,
        url: str,
        *,
        expected_path: str,
        required_query: Mapping[str, str],
    ) -> None:
        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError as error:
            raise GitHubSurfaceError("GitHub pagination URL is not canonical") from error
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.github.com"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.path != expected_path
            or parsed.fragment
        ):
            raise GitHubSurfaceError("GitHub pagination URL is outside the expected collection")
        query = parse_qs(parsed.query, keep_blank_values=True)
        allowed = set(required_query) | {"page"}
        if set(query) - allowed:
            raise GitHubSurfaceError("GitHub pagination URL is outside the expected collection")
        for key, expected in required_query.items():
            if query.get(key) != [expected]:
                raise GitHubSurfaceError("GitHub pagination URL is outside the expected collection")
        if "page" in query and (
            len(query["page"]) != 1 or not query["page"][0].isdigit()
        ):
            raise GitHubSurfaceError("GitHub pagination URL is outside the expected collection")

    def _next_cursor(
        self,
        response: HttpResponse,
        *,
        expected_path: str,
        required_query: Mapping[str, str],
    ) -> str | None:
        link = self._normalized_headers(response).get("link")
        if not link:
            return None
        next_urls: list[str] = []
        for part in link.split(","):
            segments = [segment.strip() for segment in part.split(";")]
            if len(segments) < 2 or not any(segment == 'rel="next"' for segment in segments[1:]):
                continue
            if not segments[0].startswith("<") or not segments[0].endswith(">"):
                raise GitHubSurfaceError("GitHub pagination link is malformed")
            next_urls.append(segments[0][1:-1])
        if not next_urls:
            return None
        if len(next_urls) != 1:
            raise GitHubSurfaceError("GitHub pagination has multiple next cursors")
        self._validate_api_collection_url(
            next_urls[0],
            expected_path=expected_path,
            required_query=required_query,
        )
        return next_urls[0]

    def _reported_total(self, response: HttpResponse) -> int | None:
        raw = self._normalized_headers(response).get("x-total-count")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError as error:
            raise GitHubSurfaceError("GitHub reported count is invalid") from error

    def read_repository_identity(self) -> RepositoryIdentity:
        payload, _response = self._get_json(self._repository_url)
        if not isinstance(payload, dict):
            raise GitHubSurfaceError("GitHub repository identity response is malformed")
        owner = payload.get("owner")
        observed_owner = owner.get("login") if isinstance(owner, dict) else None
        observed_name = payload.get("name")
        if observed_owner != self._owner or observed_name != self._repository:
            raise GitHubSurfaceError("GitHub repository identity does not match the requested target")
        permissions = payload.get("permissions")
        authenticated_push = isinstance(permissions, dict) and permissions.get("push") is True
        if self._token and authenticated_push:
            self._permission_verified = True
            return RepositoryIdentity(owner=self._owner, name=self._repository, permission="write")
        raise GitHubSurfaceError(
            "GitHub authenticated draft release visibility was not verified"
        )

    def list_releases(self, cursor: str | None) -> Page[ReleaseMetadata]:
        path = f"/repos/{self._owner}/{self._repository}/releases"
        required_query = {"per_page": "100"}
        url = cursor or f"{self._repository_url}/releases?per_page=100"
        self._validate_api_collection_url(
            url, expected_path=path, required_query=required_query
        )
        payload, response = self._get_json(url)
        if not isinstance(payload, list):
            raise GitHubSurfaceError("GitHub release page is malformed")
        releases: list[ReleaseMetadata] = []
        for raw in payload:
            if not isinstance(raw, dict):
                raise GitHubSurfaceError("GitHub release record is malformed")
            release_id = raw.get("id")
            tag_name = raw.get("tag_name")
            name = raw.get("name")
            body = raw.get("body")
            author = raw.get("author")
            author_login = author.get("login") if isinstance(author, dict) else ""
            if not isinstance(release_id, int) or not isinstance(tag_name, str):
                raise GitHubSurfaceError("GitHub release identity is incomplete")
            releases.append(
                ReleaseMetadata(
                    release_id=str(release_id),
                    tag_name=tag_name,
                    name=name if isinstance(name, str) else "",
                    body=body if isinstance(body, str) else "",
                    author=author_login if isinstance(author_login, str) else "",
                )
            )
        return Page(
            items=tuple(releases),
            next_cursor=self._next_cursor(
                response, expected_path=path, required_query=required_query
            ),
            reported_total=self._reported_total(response),
            permission_verified=self._permission_verified,
            rate_limit_remaining=self._rate_limit(response),
        )

    def list_release_assets(
        self, release_id: str, cursor: str | None
    ) -> Page[ReleaseAssetMetadata]:
        if not release_id.isdigit():
            raise GitHubSurfaceError("GitHub release id is invalid")
        path = f"/repos/{self._owner}/{self._repository}/releases/{release_id}/assets"
        required_query = {"per_page": "100"}
        url = cursor or f"{self._repository_url}/releases/{release_id}/assets?per_page=100"
        self._validate_api_collection_url(
            url, expected_path=path, required_query=required_query
        )
        payload, response = self._get_json(url)
        if not isinstance(payload, list):
            raise GitHubSurfaceError("GitHub release asset page is malformed")
        assets: list[ReleaseAssetMetadata] = []
        for raw in payload:
            if not isinstance(raw, dict):
                raise GitHubSurfaceError("GitHub release asset record is malformed")
            asset_id = raw.get("id")
            name = raw.get("name")
            size = raw.get("size")
            api_url = raw.get("url")
            uploader = raw.get("uploader")
            uploader_login = uploader.get("login") if isinstance(uploader, dict) else ""
            if (
                not isinstance(asset_id, int)
                or not isinstance(name, str)
                or not isinstance(size, int)
                or size < 0
                or not isinstance(api_url, str)
            ):
                raise GitHubSurfaceError("GitHub release asset identity is incomplete")
            expected_api_url = f"{self._repository_url}/releases/assets/{asset_id}"
            parsed = urlparse(api_url)
            if (
                api_url != expected_api_url
                or parsed.scheme != "https"
                or parsed.hostname != "api.github.com"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
            ):
                raise GitHubSurfaceError("GitHub release asset URL is not canonical")
            key = str(asset_id)
            self._assets[key] = (api_url, size)
            assets.append(
                ReleaseAssetMetadata(
                    asset_id=key,
                    name=name,
                    size=size,
                    uploader=uploader_login if isinstance(uploader_login, str) else "",
                )
            )
        return Page(
            items=tuple(assets),
            next_cursor=self._next_cursor(
                response, expected_path=path, required_query=required_query
            ),
            reported_total=self._reported_total(response),
            permission_verified=self._permission_verified,
            rate_limit_remaining=self._rate_limit(response),
        )

    def read_release_asset(self, asset_id: str) -> bytes:
        registered = self._assets.get(asset_id)
        if registered is None:
            raise GitHubSurfaceError("GitHub release asset was not enumerated before download")
        url, expected_size = registered
        response = self._transport.get(
            url,
            headers=self._headers(accept="application/octet-stream"),
        )
        if response.final_url != url:
            raise GitHubSurfaceError("GitHub release asset API request redirected unexpectedly")
        self._rate_limit(response)
        if response.status in {301, 302, 303, 307, 308}:
            location = self._normalized_headers(response).get("location")
            if not location:
                raise GitHubSurfaceError("GitHub release asset redirect is missing a target")
            parsed = urlparse(location)
            try:
                port = parsed.port
            except ValueError as error:
                raise GitHubSurfaceError(
                    "GitHub release asset redirected to an untrusted host"
                ) from error
            if (
                parsed.scheme != "https"
                or parsed.hostname not in self._ASSET_HOSTS
                or parsed.username is not None
                or parsed.password is not None
                or port is not None
                or parsed.fragment
            ):
                raise GitHubSurfaceError("GitHub release asset redirected to an untrusted host")
            response = self._transport.get(
                location,
                headers=self._headers(
                    accept="application/octet-stream", include_token=False
                ),
            )
            if response.final_url != location:
                raise GitHubSurfaceError("GitHub release asset redirected more than once")
        if response.status != 200:
            raise GitHubSurfaceError(f"GitHub release asset returned status {response.status}")
        if len(response.body) != expected_size:
            raise GitHubSurfaceError("GitHub release asset size does not match enumeration")
        return response.body

    def list_pull_requests(
        self, *, state: str, cursor: str | None
    ) -> Page[PullRequestMetadata]:
        if state not in {"open", "closed"}:
            raise GitHubSurfaceError("GitHub pull request state is invalid")
        path = f"/repos/{self._owner}/{self._repository}/pulls"
        required_query = {"state": state, "per_page": "100"}
        url = cursor or f"{self._repository_url}/pulls?state={state}&per_page=100"
        self._validate_api_collection_url(
            url, expected_path=path, required_query=required_query
        )
        payload, response = self._get_json(url)
        if not isinstance(payload, list):
            raise GitHubSurfaceError("GitHub pull request page is malformed")
        pull_requests: list[PullRequestMetadata] = []
        for raw in payload:
            if not isinstance(raw, dict):
                raise GitHubSurfaceError("GitHub pull request record is malformed")
            user = raw.get("user")
            head = raw.get("head")
            base = raw.get("base")
            head_repository = head.get("repo") if isinstance(head, dict) else None
            values = (
                raw.get("number"),
                raw.get("state"),
                raw.get("title"),
                user.get("login") if isinstance(user, dict) else None,
                head.get("ref") if isinstance(head, dict) else None,
                base.get("ref") if isinstance(base, dict) else None,
            )
            if (
                not isinstance(values[0], int)
                or values[1] != state
                or any(not isinstance(value, str) for value in values[2:])
            ):
                raise GitHubSurfaceError("GitHub pull request identity is incomplete")
            head_repository_url = (
                head_repository.get("clone_url") if isinstance(head_repository, dict) else ""
            )
            if not isinstance(head_repository_url, str):
                raise GitHubSurfaceError("GitHub pull request repository metadata is malformed")
            body = raw.get("body")
            pull_requests.append(
                PullRequestMetadata(
                    number=values[0],
                    state=values[1],
                    title=values[2],
                    body=body if isinstance(body, str) else "",
                    author=values[3],
                    head_ref=values[4],
                    base_ref=values[5],
                    head_repository_url=head_repository_url,
                )
            )
        return Page(
            items=tuple(pull_requests),
            next_cursor=self._next_cursor(
                response, expected_path=path, required_query=required_query
            ),
            reported_total=self._reported_total(response),
            permission_verified=self._permission_verified,
            rate_limit_remaining=self._rate_limit(response),
        )


@dataclass(frozen=True)
class GitHubCoverage:
    complete: bool
    release_count: int
    release_asset_count: int
    open_pull_request_count: int
    closed_pull_request_count: int
    minimum_rate_limit_remaining: int
    stabilized_read_count: int
    traversals: tuple[TraversalEvidence, ...] = ()


@dataclass(frozen=True)
class TraversalEvidence:
    label: str
    observed_count: int
    page_count: int
    exhausted: bool
    final_cursor_sha256: str
    permission_verified: bool
    minimum_rate_limit_remaining: int
    reported_total: int | None


@dataclass(frozen=True)
class GitHubSurfaceSnapshot:
    repository: RepositoryIdentity
    releases: tuple[ReleaseMetadata, ...]
    release_assets: tuple[ReleaseAssetMetadata, ...]
    open_pull_requests: tuple[PullRequestMetadata, ...]
    closed_pull_requests: tuple[PullRequestMetadata, ...]
    values: tuple[SurfaceValue, ...]
    coverage: GitHubCoverage
    snapshot_sha256: str

    def as_public_surface_snapshot(self) -> PublicSurfaceSnapshot:
        return PublicSurfaceSnapshot.create(
            name="github",
            values=self.values,
            complete=self.coverage.complete,
        )


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _paginate(
    fetch: Callable[[str | None], Page[T]],
    *,
    label: str,
    identity: Callable[[T], Hashable],
) -> tuple[tuple[T, ...], TraversalEvidence]:
    cursor: str | None = None
    seen: set[str] = set()
    seen_identities: set[Hashable] = set()
    items: list[T] = []
    reported_total: int | None = None
    minimum_rate_limit: int | None = None
    page_count = 0
    while True:
        try:
            page = fetch(cursor)
        except Exception as error:
            raise GitHubSurfaceError(f"{label} page read failed") from error
        if not page.permission_verified:
            raise GitHubSurfaceError(f"{label} permission was not verified")
        page_count += 1
        if page.rate_limit_remaining < 0:
            raise GitHubSurfaceError(f"{label} rate-limit state is invalid")
        minimum_rate_limit = (
            page.rate_limit_remaining
            if minimum_rate_limit is None
            else min(minimum_rate_limit, page.rate_limit_remaining)
        )
        if page.reported_total is not None and page.reported_total < 0:
            raise GitHubSurfaceError(f"{label} reported count is invalid")
        if reported_total is None and page.reported_total is not None:
            reported_total = page.reported_total
        elif page.reported_total is not None and reported_total != page.reported_total:
            raise GitHubSurfaceError(f"{label} reported count changed during pagination")
        for item in page.items:
            item_identity = identity(item)
            if item_identity in seen_identities:
                raise GitHubSurfaceError(f"{label} contains a duplicate identity")
            seen_identities.add(item_identity)
            items.append(item)
        next_cursor = page.next_cursor
        if next_cursor is None:
            break
        if not next_cursor or next_cursor == cursor or next_cursor in seen:
            raise GitHubSurfaceError(f"{label} pagination cursor repeated")
        seen.add(next_cursor)
        cursor = next_cursor
    if reported_total is not None and len(items) != reported_total:
        raise GitHubSurfaceError(f"{label} observed count does not match reported count")
    final_cursor = "<first-page>" if cursor is None else cursor
    return tuple(items), TraversalEvidence(
        label=label,
        observed_count=len(items),
        page_count=page_count,
        exhausted=True,
        final_cursor_sha256=_sha256(
            f"{final_cursor}|terminal:none".encode("utf-8")
        ),
        permission_verified=True,
        minimum_rate_limit_remaining=(
            minimum_rate_limit if minimum_rate_limit is not None else 0
        ),
        reported_total=reported_total,
    )


def _value(surface: str, locator: str, value: str | bytes) -> SurfaceValue:
    body = value.encode("utf-8") if isinstance(value, str) else value
    return SurfaceValue(surface=surface, locator=locator, value=body)


def _read_once(client: GitHubMetadataClient) -> GitHubSurfaceSnapshot:
    try:
        repository = client.read_repository_identity()
    except Exception as error:
        raise GitHubSurfaceError("repository identity read failed") from error
    if not repository.owner or not repository.name or repository.permission not in {
        "read",
        "triage",
        "write",
        "maintain",
        "admin",
    }:
        raise GitHubSurfaceError("repository identity or permission is incomplete")

    releases, release_evidence = _paginate(
        client.list_releases,
        label="releases",
        identity=lambda release: release.release_id,
    )
    assets: list[ReleaseAssetMetadata] = []
    values: list[SurfaceValue] = [
        _value("github.repository.owner", "repository:owner", repository.owner),
        _value("github.repository.name", "repository:name", repository.name),
    ]
    traversals = [release_evidence]
    for release in releases:
        locator = f"release:{release.release_id}"
        values.extend(
            (
                _value("github.release.tag", f"{locator}:tag", release.tag_name),
                _value("github.release.name", f"{locator}:name", release.name),
                _value("github.release.body", f"{locator}:body", release.body),
            )
        )
        if release.author:
            values.append(
                _value("github.release.author_name", f"{locator}:author", release.author)
            )
        release_assets, asset_evidence = _paginate(
            lambda cursor, release_id=release.release_id: client.list_release_assets(
                release_id, cursor
            ),
            label=f"release assets for {release.release_id}",
            identity=lambda asset: asset.asset_id,
        )
        traversals.append(asset_evidence)
        for asset in release_assets:
            if asset.size < 0:
                raise GitHubSurfaceError("release asset size is invalid")
            try:
                body = client.read_release_asset(asset.asset_id)
            except Exception as error:
                raise GitHubSurfaceError("release asset read failed") from error
            if len(body) != asset.size:
                raise GitHubSurfaceError("release asset observed count does not match declared size")
            assets.append(asset)
            values.extend(
                (
                    _value(
                        "github.release.asset_name",
                        f"{locator}:asset:{asset.asset_id}:name",
                        asset.name,
                    ),
                    _value(
                        "github.release.asset",
                        f"{locator}:asset:{asset.asset_id}:body",
                        body,
                    ),
                )
            )
            if asset.uploader:
                values.append(
                    _value(
                        "github.release.asset_uploader_name",
                        f"{locator}:asset:{asset.asset_id}:uploader",
                        asset.uploader,
                    )
                )

    pull_requests: dict[str, tuple[PullRequestMetadata, ...]] = {}
    for state in ("open", "closed"):
        prs, pull_request_evidence = _paginate(
            lambda cursor, state=state: client.list_pull_requests(state=state, cursor=cursor),
            label=f"{state} pull requests",
            identity=lambda pull_request: pull_request.number,
        )
        traversals.append(pull_request_evidence)
        pull_requests[state] = prs
        for pull_request in prs:
            if pull_request.state != state:
                raise GitHubSurfaceError(f"{state} pull request state mismatch")
            locator = f"pull-request:{pull_request.number}"
            values.extend(
                (
                    _value("github.pull_request.title", f"{locator}:title", pull_request.title),
                    _value("github.pull_request.body", f"{locator}:body", pull_request.body),
                    _value("github.pull_request.author_name", f"{locator}:author", pull_request.author),
                    _value("github.pull_request.head_ref", f"{locator}:head-ref", pull_request.head_ref),
                    _value("github.pull_request.base_ref", f"{locator}:base-ref", pull_request.base_ref),
                    _value(
                        "github.pull_request.head_repository_url",
                        f"{locator}:head-repository",
                        pull_request.head_repository_url,
                    ),
                )
            )

    surface = PublicSurfaceSnapshot.create(name="github", values=values, complete=True)
    coverage = GitHubCoverage(
        complete=True,
        release_count=len(releases),
        release_asset_count=len(assets),
        open_pull_request_count=len(pull_requests["open"]),
        closed_pull_request_count=len(pull_requests["closed"]),
        minimum_rate_limit_remaining=min(
            client.minimum_rate_limit_remaining(),
            *(evidence.minimum_rate_limit_remaining for evidence in traversals),
        ),
        stabilized_read_count=1,
        traversals=tuple(traversals),
    )
    digest = _sha256(
        _canonical_json(
            {
                "repository": [repository.owner, repository.name, repository.permission],
                "surface": surface.snapshot_sha256,
                "counts": [
                    coverage.release_count,
                    coverage.release_asset_count,
                    coverage.open_pull_request_count,
                    coverage.closed_pull_request_count,
                ],
                "traversals": [
                    {
                        "label": evidence.label,
                        "observed_count": evidence.observed_count,
                        "page_count": evidence.page_count,
                        "exhausted": evidence.exhausted,
                        "final_cursor_sha256": evidence.final_cursor_sha256,
                        "permission_verified": evidence.permission_verified,
                        "reported_total": evidence.reported_total,
                    }
                    for evidence in coverage.traversals
                ],
            }
        )
    )
    return GitHubSurfaceSnapshot(
        repository=repository,
        releases=releases,
        release_assets=tuple(assets),
        open_pull_requests=pull_requests["open"],
        closed_pull_requests=pull_requests["closed"],
        values=tuple(values),
        coverage=coverage,
        snapshot_sha256=digest,
    )


def read_github_surface(client: GitHubMetadataClient) -> GitHubSurfaceSnapshot:
    """Require two equal, exhaustive reads so concurrent GitHub changes fail closed."""

    first = _read_once(client)
    second = _read_once(client)
    if first.snapshot_sha256 != second.snapshot_sha256:
        raise GitHubSurfaceError("GitHub surface changed during observation")
    if len(first.coverage.traversals) != len(second.coverage.traversals):
        raise GitHubSurfaceError("GitHub traversal coverage changed during observation")
    traversals = tuple(
        TraversalEvidence(
            label=second_evidence.label,
            observed_count=second_evidence.observed_count,
            page_count=second_evidence.page_count,
            exhausted=second_evidence.exhausted,
            final_cursor_sha256=second_evidence.final_cursor_sha256,
            permission_verified=second_evidence.permission_verified,
            minimum_rate_limit_remaining=min(
                first_evidence.minimum_rate_limit_remaining,
                second_evidence.minimum_rate_limit_remaining,
            ),
            reported_total=second_evidence.reported_total,
        )
        for first_evidence, second_evidence in zip(
            first.coverage.traversals,
            second.coverage.traversals,
            strict=True,
        )
    )
    coverage = GitHubCoverage(
        complete=True,
        release_count=second.coverage.release_count,
        release_asset_count=second.coverage.release_asset_count,
        open_pull_request_count=second.coverage.open_pull_request_count,
        closed_pull_request_count=second.coverage.closed_pull_request_count,
        minimum_rate_limit_remaining=min(
            first.coverage.minimum_rate_limit_remaining,
            second.coverage.minimum_rate_limit_remaining,
        ),
        stabilized_read_count=2,
        traversals=traversals,
    )
    final_digest = _sha256(
        _canonical_json(
            {
                "stable_snapshot_sha256": second.snapshot_sha256,
                "stabilized_read_count": coverage.stabilized_read_count,
                "minimum_rate_limit_remaining": (
                    coverage.minimum_rate_limit_remaining
                ),
                "traversals": [
                    {
                        "label": evidence.label,
                        "observed_count": evidence.observed_count,
                        "page_count": evidence.page_count,
                        "exhausted": evidence.exhausted,
                        "final_cursor_sha256": evidence.final_cursor_sha256,
                        "permission_verified": evidence.permission_verified,
                        "minimum_rate_limit_remaining": (
                            evidence.minimum_rate_limit_remaining
                        ),
                        "reported_total": evidence.reported_total,
                    }
                    for evidence in traversals
                ],
            }
        )
    )
    return GitHubSurfaceSnapshot(
        repository=second.repository,
        releases=second.releases,
        release_assets=second.release_assets,
        open_pull_requests=second.open_pull_requests,
        closed_pull_requests=second.closed_pull_requests,
        values=second.values,
        coverage=coverage,
        snapshot_sha256=final_digest,
    )
