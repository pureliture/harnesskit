from __future__ import annotations

from pathlib import Path

import pytest

from scripts.public_port.content_policy import PublicContentError, validate_public_blob
from scripts.public_port.sanitize import (
    PublicSurfaceSnapshot,
    ReviewedSanitationAllowlist,
    SurfaceValue,
    audit_public_surface,
)


def test_content_policy_allows_canonical_public_repository_url():
    validate_public_blob(
        "app/checkout.rs",
        b'const URL: &str = "https://github.com/pureliture/harnesskit.git";\n',
    )


@pytest.mark.parametrize(
    "url",
    [
        b"git+https://github.com/pureliture/graph-workbench.git#8f24852701282828c90eb20c54a298a3f8db4bbb",
        b"git@" + b"github.com:pureliture/graph-workbench.git#8f24852701282828c90eb20c54a298a3f8db4bbb",
    ],
)
def test_content_policy_allows_exact_reviewed_public_graph_dependency(url: bytes):
    validate_public_blob("src-frontend/package.json", url)


def test_content_policy_allows_codex_tool_paths_without_branch_context():
    validate_public_blob(
        "app/adapter.py",
        b'destination = ".codex/agents/router.toml"\n',
    )


def test_content_policy_allows_generic_users_directory_guidance():
    validate_public_blob(
        "app/path-policy.md",
        b"Local path values are forbidden, including /" + b"Users/.\n",
    )


@pytest.mark.parametrize(
    "body",
    [
        b"checkout = /" + b"Users/private-user\n",
        b"home = /" + b"home/private-user\n",
    ],
)
def test_content_policy_rejects_personal_home_roots_even_without_a_descendant(body: bytes):
    with pytest.raises(PublicContentError, match="personal absolute home path"):
        validate_public_blob("app/config.txt", body)


@pytest.mark.parametrize(
    "body",
    [
        b"ref = refs/heads/" + b"codex/private-task\n",
        b"release_branch = " + b"codex/private-task\n",
    ],
)
def test_content_policy_rejects_codex_branch_identity_in_git_or_branch_context(body: bytes):
    with pytest.raises(PublicContentError, match="private Codex branch identity"):
        validate_public_blob("app/release.txt", body)


@pytest.mark.parametrize(
    "private_url",
    [
        b"https://github.com/pureliture/" + b"private-project.git",
        b"git@" + b"github.com:pureliture/" + b"graph-workbench-private.git",
    ],
)
def test_content_policy_rejects_non_public_pureliture_repository_url(private_url: bytes):

    with pytest.raises(PublicContentError, match="non-public pureliture repository URL"):
        validate_public_blob("app/checkout.rs", private_url)


def test_content_policy_allows_secret_named_runtime_expression_without_literal_value():
    runtime_expression = b"let api_" + b"key = read_revision_file(&revision)?;\n"

    validate_public_blob("app/provider_store.rs", runtime_expression)


def test_content_policy_allows_secret_named_object_property_with_identifier_value():
    object_property = b"api_" + b"key: leakedSecret,\n"

    validate_public_blob("app/provider_state.js", object_property)


@pytest.mark.parametrize(
    "body",
    [
        b'api_' + b'key = "fixture-secret-value"\n',
        b"access_" + b"token: fixture-secret-value\n",
        b"path=client_" + b"secret=fixture-secret-value.txt\n",
    ],
)
def test_content_policy_rejects_secret_like_literal_assignments(body: bytes):
    with pytest.raises(PublicContentError, match="secret-like assignment"):
        validate_public_blob("app/fixture.txt", body)


@pytest.mark.parametrize(
    "path",
    [
        "src-frontend/tests/ai-explanation.test.js",
        "src-frontend/tests/app-interactions.test.js",
    ],
)
def test_frontend_warning_fixtures_are_safe_for_public_port(path: str) -> None:
    body = (Path(__file__).resolve().parents[2] / path).read_bytes()
    validate_public_blob(path, body)
    baseline = PublicSurfaceSnapshot.create(name="baseline", complete=True)
    candidate = PublicSurfaceSnapshot.create(
        name="candidate", values=(SurfaceValue("git.blob", f"blob:{path}", body),), complete=True
    )
    github = PublicSurfaceSnapshot.create(name="github", complete=True)

    report = audit_public_surface(
        baseline=baseline,
        candidate=candidate,
        github=github,
        allowlist=ReviewedSanitationAllowlist.empty(),
    )
    assert report.qualified is True
