from __future__ import annotations

import re
from pathlib import PurePosixPath

from .policy import REVIEWED_PUBLIC_REPOSITORIES


class PublicContentError(ValueError):
    """A reviewed path or blob still contains private-only publication residue."""


_FORBIDDEN_PATH_COMPONENTS = {
    "module-cache",
    "operator-docs",
    "operator-runbook.md",
    "private-operator",
    "runtime-evidence",
}

_PURELITURE_REPOSITORY_URL = re.compile(
    rb"(?:https?://github\.com/pureliture/|(?:ssh://)?git@github\.com[:/]pureliture/)"
    rb"(?P<repository>[A-Za-z0-9_.-]+)(?=[/?#\s\"'`;,)\]]|$)",
    re.IGNORECASE,
)

_FORBIDDEN_BODY_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "personal absolute home path",
        re.compile(
            rb"(?:^|[^A-Za-z0-9_])/(?:Users|home)/[A-Za-z0-9_]"
            rb"[A-Za-z0-9._-]*"
            rb"(?=[/\s\"'`;,\)\]}]|$)"
        ),
    ),
    (
        "private Codex branch identity",
        re.compile(
            rb"(?:refs/heads/|(?:[A-Za-z0-9_]*branch|head)[\"']?\s*[:=]\s*[\"']?)"
            rb"codex/[A-Za-z0-9._/-]+",
            re.IGNORECASE,
        ),
    ),
    (
        "secret-like assignment",
        re.compile(
            rb"(?:api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*"
            rb"(?:[\"'][A-Za-z0-9+/=_-]{12,}[\"']|"
            rb"[A-Za-z0-9+/=_-]{12,}(?=\s*(?:[\"'#.]|$)))",
            re.IGNORECASE,
        ),
    ),
)


def validate_public_blob(path: str, body: bytes) -> None:
    """Fail closed on private path classes and high-confidence private text residue.

    This is the narrow M7A materialization gate. M7B still owns exhaustive tree/history/ref/LFS
    and GitHub metadata sanitation with versioned allowlists and completeness evidence.
    """

    components = {component.casefold() for component in PurePosixPath(path).parts}
    forbidden_components = {
        component
        for component in components
        if component in _FORBIDDEN_PATH_COMPONENTS or component.startswith("module-cache.")
    }
    if forbidden_components:
        raise PublicContentError(f"public content policy rejected private path class: {path}")
    for match in _PURELITURE_REPOSITORY_URL.finditer(body):
        repository = match.group("repository").decode("ascii").lower().removesuffix(".git")
        if repository not in REVIEWED_PUBLIC_REPOSITORIES:
            raise PublicContentError(
                f"public content policy rejected non-public pureliture repository URL: {path}"
            )
    for label, pattern in _FORBIDDEN_BODY_PATTERNS:
        if pattern.search(body):
            raise PublicContentError(f"public content policy rejected {label}: {path}")
