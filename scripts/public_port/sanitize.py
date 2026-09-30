from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import jsonschema
import yaml

from .policy import REVIEWED_PUBLIC_REPOSITORIES


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PERSONAL_HOME_RE = re.compile(
    rb"(?:^|[^A-Za-z0-9_])/(?:Users|home)/[A-Za-z0-9_][A-Za-z0-9._-]*"
    rb"(?=[/\s\"'`;,)\]}]|$)"
)
_PRIVATE_BRANCH_RE = re.compile(
    rb"(?:refs/heads/|(?:branch|head)[\"']?\s*[:=]\s*[\"']?)codex/[A-Za-z0-9._/-]+",
    re.IGNORECASE,
)
_CREDENTIAL_RE = re.compile(
    rb"(?:api[_-]?key|access[_-]?token|client[_-]?secret|authorization)\s*[:=]\s*"
    rb"(?:bearer\s+)?(?:[\"'][A-Za-z0-9+/=_-]{12,}[\"']|"
    rb"[A-Za-z0-9+/=_-]{12,}(?=\s*(?:[\"'#.]|$)))",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(
    rb"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)
_URL_RE = re.compile(rb"https?://[^\s\"'`<>]+", re.IGNORECASE)
_TAURI_ICON_FILENAME_RE = re.compile(rb"[0-9]+x[0-9]+@[0-9]+x\.png", re.IGNORECASE)
_JSON_SCHEMA_PROPERTY_RE = re.compile(rb'"\$schema"\s*:\s*"$')
_JSON_SCHEMA_DRAFT_07_URI = b"http:" b"//json-schema.org/draft-07/schema#"
_SVG_XMLNS_URI = b"http:" b"//www.w3.org/2000/svg"
_PURELITURE_REPOSITORY_RE = re.compile(
    rb"(?:https?://github\.com/pureliture/|(?:ssh://)?git@github\.com[:/]pureliture/)"
    rb"(?P<repository>[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_INTERNAL_URL_RE = re.compile(
    rb"https?://(?:"
    rb"(?:10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})|"
    rb"(?:192\.168\.[0-9]{1,3}\.[0-9]{1,3})|"
    rb"(?:172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})|"
    rb"(?:[A-Za-z0-9.-]+\.(?:internal|corp|lan|local))"
    rb")(?::[0-9]+)?(?:[/\s]|$)",
    re.IGNORECASE,
)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALLOWLIST_SCHEMA = _REPO_ROOT / "schemas/public-sanitation-allowlist-v1.json"


class SanitationError(ValueError):
    """A complete public-surface snapshot did not qualify for publication."""

    def __init__(self, message: str, *, report: SanitationReport | None = None) -> None:
        super().__init__(message)
        self.report = report


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


@dataclass(frozen=True)
class SurfaceValue:
    """In-memory scanner input. It must never be serialized into the public report."""

    surface: str
    locator: str
    value: bytes

    def __post_init__(self) -> None:
        if not self.surface or not self.locator:
            raise ValueError("surface and locator must be non-empty")
        if not isinstance(self.value, bytes):
            raise TypeError("surface value must be bytes")


@dataclass(frozen=True)
class PublicSurfaceSnapshot:
    name: str
    values: tuple[SurfaceValue, ...]
    complete: bool
    snapshot_sha256: str

    @classmethod
    def create(
        cls,
        *,
        name: str,
        values: Iterable[SurfaceValue] = (),
        complete: bool,
    ) -> PublicSurfaceSnapshot:
        normalized = tuple(values)
        digest_input = [
            {
                "surface": item.surface,
                "locator_sha256": _sha256(item.locator.encode("utf-8")),
                "value_sha256": _sha256(item.value),
            }
            for item in sorted(normalized, key=lambda item: (item.surface, item.locator, item.value))
        ]
        return cls(
            name=name,
            values=normalized,
            complete=complete,
            snapshot_sha256=_sha256(_canonical_json(digest_input)),
        )


@dataclass(frozen=True)
class ReviewedAllowance:
    rule_id: str
    surface: str
    locator_sha256: str
    value_sha256: str
    justification_sha256: str
    approved_by: str

    def __post_init__(self) -> None:
        if not self.rule_id or not self.surface or not self.approved_by:
            raise ValueError("reviewed allowance identity fields must be non-empty")
        for label, value in (
            ("locator_sha256", self.locator_sha256),
            ("value_sha256", self.value_sha256),
            ("justification_sha256", self.justification_sha256),
        ):
            if not _SHA256_RE.fullmatch(value):
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class ReviewedSanitationAllowlist:
    schema_version: int
    entries: tuple[ReviewedAllowance, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("sanitation allowlist schema_version must be 1")
        identities = {
            (entry.rule_id, entry.surface, entry.locator_sha256, entry.value_sha256)
            for entry in self.entries
        }
        if len(identities) != len(self.entries):
            raise ValueError("sanitation allowlist contains duplicate exact entries")

    @classmethod
    def empty(cls) -> ReviewedSanitationAllowlist:
        return cls(schema_version=1, entries=())

    @property
    def sha256(self) -> str:
        normalized = [
            {
                "rule_id": entry.rule_id,
                "surface": entry.surface,
                "locator_sha256": entry.locator_sha256,
                "value_sha256": entry.value_sha256,
                "justification_sha256": entry.justification_sha256,
                "approved_by": entry.approved_by,
            }
            for entry in sorted(
                self.entries,
                key=lambda entry: (
                    entry.rule_id,
                    entry.surface,
                    entry.locator_sha256,
                    entry.value_sha256,
                ),
            )
        ]
        return _sha256(_canonical_json({"schema_version": 1, "entries": normalized}))


def load_reviewed_allowlist(path: Path) -> ReviewedSanitationAllowlist:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        schema = json.loads(_ALLOWLIST_SCHEMA.read_text(encoding="utf-8"))
        jsonschema.validate(instance=raw, schema=schema)
    except (OSError, UnicodeError, yaml.YAMLError, json.JSONDecodeError) as error:
        raise SanitationError("sanitation allowlist could not be read") from error
    except jsonschema.ValidationError as error:
        raise SanitationError(
            f"sanitation allowlist schema validation failed: {error.message}"
        ) from error
    assert isinstance(raw, dict)
    entries = tuple(
        ReviewedAllowance(
            rule_id=entry["rule_id"],
            surface=entry["surface"],
            locator_sha256=entry["locator_sha256"],
            value_sha256=entry["value_sha256"],
            justification_sha256=entry["justification_sha256"],
            approved_by=entry["approved_by"],
        )
        for entry in raw["entries"]
    )
    return ReviewedSanitationAllowlist(schema_version=raw["schema_version"], entries=entries)


@dataclass(frozen=True)
class SanitationFinding:
    rule_id: str
    surface: str
    locator_sha256: str
    value_sha256: str


@dataclass(frozen=True)
class SanitationReport:
    qualified: bool
    baseline_snapshot_sha256: str
    candidate_snapshot_sha256: str
    github_snapshot_sha256: str
    baseline_findings_sha256: str
    candidate_findings_sha256: str
    github_findings_sha256: str
    allowlist_sha256: str
    findings: tuple[SanitationFinding, ...]


def _is_binary(body: bytes) -> bool:
    if b"\x00" in body:
        return True
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _is_tauri_icon_filename(value: SurfaceValue, match: re.Match[bytes]) -> bool:
    if value.surface != "git.blob" or not _TAURI_ICON_FILENAME_RE.fullmatch(match.group(0)):
        return False
    prefix = value.value[: match.start()]
    return prefix.endswith(b"/icons/") or prefix.endswith(b'"icons/') or prefix == b"icons/"


def _is_json_schema_meta_identifier(value: SurfaceValue, match: re.Match[bytes]) -> bool:
    return (
        value.surface == "git.blob"
        and match.group(0) == _JSON_SCHEMA_DRAFT_07_URI
        and value.value[match.end() : match.end() + 1] == b'"'
        and _JSON_SCHEMA_PROPERTY_RE.search(value.value[: match.start()]) is not None
    )


def _is_svg_xmlns_identifier(value: SurfaceValue, match: re.Match[bytes]) -> bool:
    return (
        value.surface == "git.blob"
        and match.group(0) == _SVG_XMLNS_URI
        and value.value[match.end() : match.end() + 1] == b'"'
        and value.value[: match.start()].endswith(b'xmlns="')
        and value.value.lstrip().startswith(b"<svg ")
    )


def _rule_matches(value: SurfaceValue) -> set[str]:
    matches: set[str] = set()
    if _is_binary(value.value):
        matches.add("binary-content-review-required")
        return matches
    if value.surface.endswith(
        (
            ".author_name",
            ".author_email",
            ".committer_name",
            ".committer_email",
            ".tagger_name",
            ".tagger_email",
        )
    ):
        matches.add("public-identity-review-required")
    if _PERSONAL_HOME_RE.search(value.value):
        matches.add("personal-home-path")
    if _PRIVATE_BRANCH_RE.search(value.value):
        matches.add("private-branch")
    if (
        value.surface == "github.pull_request.head_ref"
        and value.value.lower().startswith(b"codex/")
    ):
        matches.add("private-branch")
    if _CREDENTIAL_RE.search(value.value):
        matches.add("credential-like-value")
    repositories = tuple(_PURELITURE_REPOSITORY_RE.finditer(value.value))
    for repository_match in repositories:
        repository = repository_match.group("repository").decode("ascii").lower().removesuffix(".git")
        if repository not in REVIEWED_PUBLIC_REPOSITORIES:
            matches.add("private-repository-url")
    if _INTERNAL_URL_RE.search(value.value):
        matches.add("internal-url")
    if "public-identity-review-required" not in matches and any(
        not _is_tauri_icon_filename(value, email_match)
        and not any(
            repository_match.group(0).lower().startswith((b"git@", b"ssh://git@"))
            and repository_match.start() <= email_match.start()
            and email_match.end() <= repository_match.end()
            for repository_match in repositories
        )
        for email_match in _EMAIL_RE.finditer(value.value)
    ):
        matches.add("email-review-required")
    for url_match in _URL_RE.finditer(value.value):
        lowered = url_match.group(0).lower().rstrip(b".,;:)")
        if lowered.startswith(b"http://") and not lowered.startswith(
            (b"http://localhost", b"http://127.0.0.1", b"http://[::1]")
        ) and not _is_json_schema_meta_identifier(value, url_match) and not _is_svg_xmlns_identifier(value, url_match):
            matches.add("non-loopback-plaintext-url")
    return matches


def _finding(rule_id: str, value: SurfaceValue) -> SanitationFinding:
    return SanitationFinding(
        rule_id=rule_id,
        surface=value.surface,
        locator_sha256=_sha256(value.locator.encode("utf-8")),
        value_sha256=_sha256(value.value),
    )


def _finding_sha256(findings: Iterable[SanitationFinding]) -> str:
    normalized = [
        {
            "rule_id": finding.rule_id,
            "surface": finding.surface,
            "locator_sha256": finding.locator_sha256,
            "value_sha256": finding.value_sha256,
        }
        for finding in sorted(
            findings,
            key=lambda finding: (
                finding.rule_id,
                finding.surface,
                finding.locator_sha256,
                finding.value_sha256,
            ),
        )
    ]
    return _sha256(_canonical_json(normalized))


def _scan(
    snapshot: PublicSurfaceSnapshot,
    allowlist: ReviewedSanitationAllowlist,
) -> tuple[SanitationFinding, ...]:
    allowed = {
        (entry.rule_id, entry.surface, entry.locator_sha256, entry.value_sha256)
        for entry in allowlist.entries
    }
    findings: list[SanitationFinding] = []
    for value in snapshot.values:
        for rule_id in sorted(_rule_matches(value)):
            finding = _finding(rule_id, value)
            identity = (
                finding.rule_id,
                finding.surface,
                finding.locator_sha256,
                finding.value_sha256,
            )
            if identity not in allowed:
                findings.append(finding)
    return tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.rule_id,
                finding.surface,
                finding.locator_sha256,
                finding.value_sha256,
            ),
        )
    )


def audit_public_surface(
    *,
    baseline: PublicSurfaceSnapshot,
    candidate: PublicSurfaceSnapshot,
    github: PublicSurfaceSnapshot,
    allowlist: ReviewedSanitationAllowlist,
) -> SanitationReport:
    """Qualify complete snapshots without copying raw scanner inputs into the report."""

    for snapshot in (baseline, candidate, github):
        if not snapshot.complete:
            raise SanitationError(f"{snapshot.name} coverage is incomplete")

    baseline_findings = _scan(baseline, allowlist)
    baseline_identities = {
        (value.surface, value.locator, _sha256(value.value)) for value in baseline.values
    }
    candidate_delta = PublicSurfaceSnapshot.create(
        name="candidate-delta",
        values=(
            value
            for value in candidate.values
            if (value.surface, value.locator, _sha256(value.value)) not in baseline_identities
        ),
        complete=candidate.complete,
    )
    candidate_findings = _scan(candidate_delta, allowlist)
    github_findings = _scan(github, allowlist)
    findings = tuple(sorted(
        baseline_findings + candidate_findings + github_findings,
        key=lambda finding: (
            finding.rule_id,
            finding.surface,
            finding.locator_sha256,
            finding.value_sha256,
        ),
    ))
    report = SanitationReport(
        qualified=not findings,
        baseline_snapshot_sha256=baseline.snapshot_sha256,
        candidate_snapshot_sha256=candidate.snapshot_sha256,
        github_snapshot_sha256=github.snapshot_sha256,
        baseline_findings_sha256=_finding_sha256(baseline_findings),
        candidate_findings_sha256=_finding_sha256(candidate_findings),
        github_findings_sha256=_finding_sha256(github_findings),
        allowlist_sha256=allowlist.sha256,
        findings=findings,
    )
    if findings:
        raise SanitationError("public surface contains unreviewed residue", report=report)
    return report
