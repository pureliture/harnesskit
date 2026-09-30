from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest

from scripts.public_port.sanitize import (
    PublicSurfaceSnapshot,
    ReviewedAllowance,
    ReviewedSanitationAllowlist,
    SanitationError,
    SurfaceValue,
    audit_public_surface,
    load_reviewed_allowlist,
)


def _digest(value: str | bytes) -> str:
    body = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(body).hexdigest()


def _snapshot(name: str, *values: SurfaceValue, complete: bool = True) -> PublicSurfaceSnapshot:
    return PublicSurfaceSnapshot.create(name=name, values=values, complete=complete)


def test_blocks_private_residue_across_baseline_candidate_and_github_surfaces() -> None:
    personal_email = b"person" + b"@example.com"
    personal_home = b"/" + b"Users/person/private/repo"
    private_branch = b"refs/heads/" + b"codex/private-work"
    credential = b"api_" + b"key=" + b"abcdefghijklmnop"
    baseline = _snapshot(
        "baseline",
        SurfaceValue("git.commit.author_email", "commit:1:author", personal_email),
        SurfaceValue("git.blob", "blob:1", personal_home),
    )
    candidate = _snapshot(
        "candidate",
        SurfaceValue("git.commit.message", "commit:2:message", private_branch),
    )
    github = _snapshot(
        "github",
        SurfaceValue("github.pull_request.body", "pr:1:body", credential),
    )

    with pytest.raises(SanitationError) as raised:
        audit_public_surface(
            baseline=baseline,
            candidate=candidate,
            github=github,
            allowlist=ReviewedSanitationAllowlist.empty(),
        )

    report = raised.value.report
    assert {finding.rule_id for finding in report.findings} >= {
        "public-identity-review-required",
        "personal-home-path",
        "private-branch",
        "credential-like-value",
    }
    serialized = repr(report)
    assert personal_email.decode("utf-8") not in serialized
    assert personal_home.decode("utf-8") not in serialized
    assert credential.decode("utf-8") not in serialized


def test_exact_allowlist_matches_rule_surface_locator_and_value_only() -> None:
    value = SurfaceValue(
        "git.commit.author_email",
        "commit:1:author",
        b"contributors@" + b"users.noreply.github.com",
    )
    snapshot = _snapshot("baseline", value)
    allowance = ReviewedAllowance(
        rule_id="public-identity-review-required",
        surface=value.surface,
        locator_sha256=_digest(value.locator),
        value_sha256=_digest(value.value),
        justification_sha256=_digest("approved public identity"),
        approved_by="release-security-review",
    )

    report = audit_public_surface(
        baseline=snapshot,
        candidate=_snapshot("candidate"),
        github=_snapshot("github"),
        allowlist=ReviewedSanitationAllowlist(schema_version=1, entries=(allowance,)),
    )
    assert report.qualified is True

    changed_locator = _snapshot(
        "baseline",
        SurfaceValue(value.surface, "commit:2:author", value.value),
    )
    with pytest.raises(SanitationError):
        audit_public_surface(
            baseline=changed_locator,
            candidate=_snapshot("candidate"),
            github=_snapshot("github"),
            allowlist=ReviewedSanitationAllowlist(schema_version=1, entries=(allowance,)),
        )


def test_rejects_incomplete_snapshot_even_when_it_has_no_findings() -> None:
    with pytest.raises(SanitationError, match="coverage is incomplete"):
        audit_public_surface(
            baseline=_snapshot("baseline", complete=False),
            candidate=_snapshot("candidate"),
            github=_snapshot("github"),
            allowlist=ReviewedSanitationAllowlist.empty(),
        )


def test_baseline_and_candidate_finding_digests_are_independent() -> None:
    baseline = _snapshot(
        "baseline",
        SurfaceValue("git.blob", "blob:1", b"/" + b"Users/one/private"),
    )
    candidate_one = _snapshot(
        "candidate",
        SurfaceValue("git.blob", "blob:2", b"/" + b"Users/two/private"),
    )
    candidate_two = _snapshot(
        "candidate",
        SurfaceValue("git.blob", "blob:3", b"/" + b"Users/three/private"),
    )

    def blocked(candidate):
        with pytest.raises(SanitationError) as raised:
            audit_public_surface(
                baseline=baseline,
                candidate=candidate,
                github=_snapshot("github"),
                allowlist=ReviewedSanitationAllowlist.empty(),
            )
        return raised.value.report

    first = blocked(candidate_one)
    second = blocked(candidate_two)
    assert first.baseline_findings_sha256 == second.baseline_findings_sha256
    assert first.candidate_findings_sha256 != second.candidate_findings_sha256


def test_candidate_finding_digest_contains_only_delta_from_baseline() -> None:
    shared = SurfaceValue("git.blob", "blob:shared", b"/" + b"Users/shared/private")
    baseline = _snapshot("baseline", shared)
    candidate = _snapshot("candidate", shared)

    with pytest.raises(SanitationError) as raised:
        audit_public_surface(
            baseline=baseline,
            candidate=candidate,
            github=_snapshot("github"),
            allowlist=ReviewedSanitationAllowlist.empty(),
        )

    assert raised.value.report.baseline_findings_sha256 != _digest(b"[]\n")
    assert raised.value.report.candidate_findings_sha256 == _digest(b"[]\n")


def test_binary_blob_requires_exact_review() -> None:
    snapshot = _snapshot(
        "candidate",
        SurfaceValue("git.blob", "blob:binary", b"\x00\x01\x02"),
    )
    with pytest.raises(SanitationError) as raised:
        audit_public_surface(
            baseline=_snapshot("baseline"),
            candidate=snapshot,
            github=_snapshot("github"),
            allowlist=ReviewedSanitationAllowlist.empty(),
        )
    assert any(
        finding.rule_id == "binary-content-review-required"
        for finding in raised.value.report.findings
    )


@pytest.mark.parametrize(
    ("body", "rule_id"),
    [
        (
            b"https://github.com/" + b"pureliture/private-repository",
            "private-repository-url",
        ),
        (b"https://scanner." + b"internal/api", "internal-url"),
    ],
)
def test_blocks_private_repository_and_internal_urls(body: bytes, rule_id: str) -> None:
    with pytest.raises(SanitationError) as raised:
        audit_public_surface(
            baseline=_snapshot("baseline", SurfaceValue("git.blob", "blob:url", body)),
            candidate=_snapshot("candidate"),
            github=_snapshot("github"),
            allowlist=ReviewedSanitationAllowlist.empty(),
        )

    assert any(finding.rule_id == rule_id for finding in raised.value.report.findings)


@pytest.mark.parametrize(
    "url",
    [
        b"git+https://github.com/pureliture/graph-workbench.git#8f24852701282828c90eb20c54a298a3f8db4bbb",
        b"git+ssh://git@" + b"github.com/pureliture/graph-workbench.git#8f24852701282828c90eb20c54a298a3f8db4bbb",
    ],
)
def test_allows_reviewed_public_graph_dependency_url(url: bytes) -> None:
    report = audit_public_surface(
        baseline=_snapshot("baseline"),
        candidate=_snapshot(
            "candidate",
            SurfaceValue(
                "git.blob",
                "blob:dependency",
                url,
            ),
        ),
        github=_snapshot("github"),
        allowlist=ReviewedSanitationAllowlist.empty(),
    )
    assert report.qualified is True
    assert report.findings == ()


def test_public_git_transport_does_not_hide_an_unrelated_email() -> None:
    with pytest.raises(SanitationError) as raised:
        audit_public_surface(
            baseline=_snapshot("baseline"),
            candidate=_snapshot(
                "candidate",
                SurfaceValue(
                    "git.blob",
                    "blob:dependency-and-email",
                    b"git+ssh://git@" + b"github.com/pureliture/graph-workbench.git#8f24852701282828c90eb20c54a298a3f8db4bbb "
                    + b"author@" + b"example.com",
                ),
            ),
            github=_snapshot("github"),
            allowlist=ReviewedSanitationAllowlist.empty(),
        )
    assert any(finding.rule_id == "email-review-required" for finding in raised.value.report.findings)


def test_tauri_multires_icon_filename_is_not_email() -> None:
    report = audit_public_surface(
        baseline=_snapshot("baseline"),
        candidate=_snapshot(
            "candidate",
            SurfaceValue("git.blob", "blob:icon-path", b'"path": "src-tauri/icons/128x128' b'@2x.png"'),
        ),
        github=_snapshot("github"),
        allowlist=ReviewedSanitationAllowlist.empty(),
    )
    assert report.qualified is True
    assert report.findings == ()


@pytest.mark.parametrize(
    "body",
    [
        b'"email": "128x128' b'@2x.png"',
        b'"path": "src-tauri/icons/128x128' b'@2x.png", "email": "author' b'@example.com"',
    ],
)
def test_tauri_multires_icon_exception_does_not_hide_email(body: bytes) -> None:
    with pytest.raises(SanitationError) as raised:
        audit_public_surface(
            baseline=_snapshot("baseline"),
            candidate=_snapshot("candidate", SurfaceValue("git.blob", "blob:icon-and-email", body)),
            github=_snapshot("github"),
            allowlist=ReviewedSanitationAllowlist.empty(),
        )
    assert any(finding.rule_id == "email-review-required" for finding in raised.value.report.findings)


def test_json_schema_draft_07_meta_uri_is_not_a_plaintext_endpoint() -> None:
    report = audit_public_surface(
        baseline=_snapshot("baseline"),
        candidate=_snapshot(
            "candidate",
            SurfaceValue("git.blob", "blob:schema", b'{"$schema": "http:' b'//json-schema.org/draft-07/schema#"}'),
        ),
        github=_snapshot("github"),
        allowlist=ReviewedSanitationAllowlist.empty(),
    )
    assert report.qualified is True
    assert report.findings == ()


@pytest.mark.parametrize(
    "body",
    [
        b'{"$schema": "http:' b'//json-schema.org/draft-07/schema#-other"}',
        b'{"$schema": "http:' b'//json-schema.org/DRAFT-07/schema#"}',
        b'{"endpoint": "http:' b'//json-schema.org/draft-07/schema#"}',
        b'{"$schema": "http:' b'//example.org/draft-07/schema#"}',
        b'{"$schema": "http:' b'//json-schema.org/draft-07/schema#", "endpoint": "http:' b'//example.org/api"}',
    ],
)
def test_json_schema_meta_uri_exception_does_not_hide_http_endpoint(body: bytes) -> None:
    with pytest.raises(SanitationError) as raised:
        audit_public_surface(
            baseline=_snapshot("baseline"),
            candidate=_snapshot("candidate", SurfaceValue("git.blob", "blob:schema-and-endpoint", body)),
            github=_snapshot("github"),
            allowlist=ReviewedSanitationAllowlist.empty(),
        )
    assert any(finding.rule_id == "non-loopback-plaintext-url" for finding in raised.value.report.findings)


def test_blocks_private_github_ssh_repository_url() -> None:
    with pytest.raises(SanitationError) as raised:
        audit_public_surface(
            baseline=_snapshot("baseline"),
            candidate=_snapshot(
                "candidate",
                SurfaceValue(
                    "git.blob",
                    "blob:ssh",
                    b"git@" + b"github.com:pureliture/" + b"private-repository.git",
                ),
            ),
            github=_snapshot("github"),
            allowlist=ReviewedSanitationAllowlist.empty(),
        )
    assert any(finding.rule_id == "private-repository-url" for finding in raised.value.report.findings)


def test_blocks_bare_private_github_pull_request_head_ref() -> None:
    with pytest.raises(SanitationError) as raised:
        audit_public_surface(
            baseline=_snapshot("baseline"),
            candidate=_snapshot("candidate"),
            github=_snapshot(
                "github",
                SurfaceValue(
                    "github.pull_request.head_ref",
                    "pull-request:1:head-ref",
                    b"codex/private-work",
                ),
            ),
            allowlist=ReviewedSanitationAllowlist.empty(),
        )

    assert any(
        finding.rule_id == "private-branch" for finding in raised.value.report.findings
    )


def test_loads_versioned_exact_allowlist(tmp_path) -> None:
    path = tmp_path / "allowlist.yml"
    path.write_text(
        """schema_version: 1
entries:
  - rule_id: public-identity-review-required
    surface: git.commit.author_email
    locator_sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    value_sha256: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
    justification_sha256: cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
    approved_by: release-security-review
""",
        encoding="utf-8",
    )

    loaded = load_reviewed_allowlist(path)

    assert loaded.entries[0].surface == "git.commit.author_email"


def test_rejects_generic_pattern_allowlist(tmp_path) -> None:
    path = tmp_path / "allowlist.yml"
    path.write_text(
        """schema_version: 1
entries:
  - rule_id: personal-home-path
    surface: "git.*"
    locator_sha256: "*"
    value_sha256: "*"
    justification_sha256: cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
    approved_by: release-security-review
""",
        encoding="utf-8",
    )

    with pytest.raises((ValueError, SanitationError)):
        load_reviewed_allowlist(path)


def test_svg_xmlns_is_not_treated_as_external_http_endpoint() -> None:
    from scripts.public_port.sanitize import _rule_matches, SurfaceValue

    exact = SurfaceValue("git.blob", "object:svg", b'<svg xmlns="http:' b'//www.w3.org/2000/svg"></svg>')
    remote = SurfaceValue("git.blob", "object:remote", b'<svg xmlns="http:' b'//example.test/svg"></svg>')
    assert "non-loopback-plaintext-url" not in _rule_matches(exact)
    assert "non-loopback-plaintext-url" in _rule_matches(remote)


def test_only_approved_brand_source_and_exact_derived_bytes_have_binary_review() -> None:
    root = Path(__file__).resolve().parents[2]
    brand = root / "assets/branding/harness-desktop"
    policy = (brand / "BRAND_ASSET_POLICY.md").read_bytes()
    source_approval = re.search(rb"Approved source SHA-256: `([0-9a-f]{64})`", policy)
    assert source_approval is not None
    manifest = json.loads((brand / "derivation-manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"]["sha256"].encode("ascii") == source_approval.group(1)
    assert len(manifest["outputs"]) == 19
    justification_sha256 = hashlib.sha256(policy).hexdigest()

    reviewed = load_reviewed_allowlist(root / "publish/public-sanitation-allowlist.yml")
    allowed = {
        (
            entry.rule_id,
            entry.surface,
            entry.locator_sha256,
            entry.value_sha256,
            entry.justification_sha256,
            entry.approved_by,
        )
        for entry in reviewed.entries
    }
    reviewed_identities = set()
    for item in [manifest["source"], *manifest["outputs"]]:
        relative = Path(item["path"])
        assert not relative.is_absolute() and ".." not in relative.parts
        asset = root / relative
        assert asset.is_file() and not asset.is_symlink()
        body = asset.read_bytes()
        assert _digest(body) == item["sha256"]
        if "size" in item:
            assert len(body) == item["size"]
        oid = subprocess.run(
            ["git", "hash-object", "--", str(relative)],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        locator = f"object:{oid}"
        identity = (
            "binary-content-review-required",
            "git.blob",
            _digest(locator),
            _digest(body),
            justification_sha256,
            "pureliture",
        )
        assert identity in allowed, item["path"]
        reviewed_identities.add(identity)
        if item == manifest["source"]:
            tampered = SurfaceValue("git.blob", locator, body + b"\x00")

    assert len(reviewed_identities) == 19
    with pytest.raises(SanitationError) as raised:
        audit_public_surface(
            baseline=_snapshot("baseline"),
            candidate=_snapshot("candidate", tampered),
            github=_snapshot("github"),
            allowlist=reviewed,
        )
    assert any(f.rule_id == "binary-content-review-required" for f in raised.value.report.findings)
