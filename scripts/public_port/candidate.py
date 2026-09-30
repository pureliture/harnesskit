from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .git_environment import curated_git_environment
from .git_surface import CandidateGitObjects
from .manifest import ManifestError, validate_manifest
from .materialize import MaterializeError, expected_inventory, inventory_tree
from .secure_tree import SecureTree, SecureTreeError


_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+$")
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_GIT_COMMAND_TIMEOUT_SECONDS = 120


class CandidateError(ValueError):
    """The exact reviewed public tree could not be turned into an immutable candidate."""


@dataclass(frozen=True)
class CandidateIdentity:
    name: str
    email: str
    timestamp: str

    def __post_init__(self) -> None:
        if not self.name or any(character in self.name for character in "<>\n\x00"):
            raise ValueError("candidate identity name is invalid")
        if not _EMAIL_RE.fullmatch(self.email) or any(
            character in self.email for character in "<>\n\x00"
        ):
            raise ValueError("candidate identity email is invalid")
        try:
            parsed = datetime.fromisoformat(self.timestamp)
        except ValueError as error:
            raise ValueError("candidate identity timestamp must be ISO-8601") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("candidate identity timestamp must include a timezone")


def _run(
    root: Path,
    *args: str,
    input_body: bytes | None = None,
    environment: Mapping[str, str] | None = None,
) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_body,
            check=False,
            capture_output=True,
            env=curated_git_environment(environment),
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise CandidateError("Git candidate command timed out") from error
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise CandidateError(message or f"Git candidate command failed: {' '.join(args)}")
    return result.stdout


def _text(root: Path, *args: str, environment: Mapping[str, str] | None = None) -> str:
    return _run(root, *args, environment=environment).decode("utf-8", errors="strict").strip()


def _repository_root(root: Path) -> Path:
    candidate = Path(root)
    if candidate.is_symlink():
        raise CandidateError("candidate repository root must not be a symlink")
    try:
        physical = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise CandidateError("candidate repository root does not exist") from error
    if Path(_text(physical, "rev-parse", "--show-toplevel")).resolve(strict=True) != physical:
        raise CandidateError("candidate repository root must be the Git top level")
    return physical


def _timestamp_parts(timestamp: str) -> tuple[int, str]:
    parsed = datetime.fromisoformat(timestamp)
    offset = parsed.strftime("%z")
    return int(parsed.timestamp()), offset


def _candidate_environment(identity: CandidateIdentity, *, index_path: Path | None = None) -> dict[str, str]:
    environment = {
        "GIT_AUTHOR_NAME": identity.name,
        "GIT_AUTHOR_EMAIL": identity.email,
        "GIT_AUTHOR_DATE": identity.timestamp,
        "GIT_COMMITTER_NAME": identity.name,
        "GIT_COMMITTER_EMAIL": identity.email,
        "GIT_COMMITTER_DATE": identity.timestamp,
    }
    if index_path is not None:
        environment["GIT_INDEX_FILE"] = str(index_path)
    return environment


def _build_tree(
    root: Path,
    manifest: Mapping[str, Any],
    *,
    index_path: Path,
) -> str:
    expected = expected_inventory(manifest["expected_tree"])
    try:
        observed = inventory_tree(root)
    except MaterializeError as error:
        raise CandidateError(str(error)) from error
    if observed != expected:
        raise CandidateError("candidate tree does not match the reviewed manifest")

    environment = {"GIT_INDEX_FILE": str(index_path)}
    _run(root, "read-tree", "--empty", environment=environment)
    index_records: list[bytes] = []
    try:
        with SecureTree(root) as tree:
            for entry in sorted(manifest["expected_tree"], key=lambda item: item["path"]):
                body = tree.read_file(entry["path"])
                if hashlib.sha256(body).hexdigest() != entry["sha256"]:
                    raise CandidateError(
                        f"candidate tree does not match the reviewed manifest: {entry['path']}"
                    )
                oid = _run(root, "hash-object", "-w", "--stdin", input_body=body).decode(
                    "ascii"
                ).strip()
                index_records.append(
                    f"{entry['mode']} blob {oid}\t{entry['path']}".encode("utf-8") + b"\x00"
                )
    except SecureTreeError as error:
        raise CandidateError(str(error)) from error
    _run(
        root,
        "update-index",
        "-z",
        "--index-info",
        input_body=b"".join(index_records),
        environment=environment,
    )
    tree_oid = _text(root, "write-tree", environment=environment)
    tree_output = _run(root, "ls-tree", "-rz", "--full-tree", tree_oid)
    tree_paths: set[str] = set()
    for record in tree_output.split(b"\x00"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator:
            raise CandidateError("candidate Git tree record is malformed")
        mode, object_type, _oid = metadata.decode("ascii").split(" ", 2)
        path = raw_path.decode("utf-8")
        if object_type != "blob" or expected.get(path, {}).get("mode") != mode:
            raise CandidateError("candidate Git tree differs from the reviewed tree")
        tree_paths.add(path)
    if tree_paths != set(expected):
        raise CandidateError("candidate Git tree path closure is incomplete")
    return tree_oid


def build_candidate(
    root: Path,
    *,
    manifest: Mapping[str, Any],
    identity: CandidateIdentity,
    message: str,
    tag_name: str | None = None,
    tag_message: str | None = None,
) -> CandidateGitObjects:
    """Create commit/tag objects only; refs, working tree and the real index remain untouched."""

    try:
        validate_manifest(manifest)
    except ManifestError as error:
        raise CandidateError(str(error)) from error
    if not message or "\x00" in message:
        raise CandidateError("candidate commit message is invalid")
    if tag_name is None and tag_message is not None:
        raise CandidateError("candidate tag message requires a tag name")
    if tag_name is not None and (not _TAG_RE.fullmatch(tag_name) or not tag_message):
        raise CandidateError("candidate annotated tag definition is invalid")

    repository = _repository_root(Path(root))
    parent_oid = manifest["baseline"]["commit"]
    if _text(repository, "rev-parse", "HEAD") != parent_oid:
        raise CandidateError("candidate parent does not match the reviewed baseline")

    descriptor, raw_index_path = tempfile.mkstemp(
        prefix=".public-port-index-",
        dir=repository.parent,
    )
    os.close(descriptor)
    index_path = Path(raw_index_path)
    index_path.unlink()
    try:
        tree_oid = _build_tree(repository, manifest, index_path=index_path)
        environment = _candidate_environment(identity)
        commit_body = message.rstrip("\n").encode("utf-8") + b"\n"
        commit_oid = _run(
            repository,
            "commit-tree",
            tree_oid,
            "-p",
            parent_oid,
            input_body=commit_body,
            environment=environment,
        ).decode("ascii").strip()

        tag_oid: str | None = None
        if tag_name is not None:
            assert tag_message is not None
            seconds, offset = _timestamp_parts(identity.timestamp)
            tag_body = (
                f"object {commit_oid}\n"
                "type commit\n"
                f"tag {tag_name}\n"
                f"tagger {identity.name} <{identity.email}> {seconds} {offset}\n\n"
                f"{tag_message.rstrip()}\n"
            ).encode("utf-8")
            tag_oid = _run(repository, "mktag", input_body=tag_body).decode("ascii").strip()
        return CandidateGitObjects(commit_oid=commit_oid, tag_oid=tag_oid)
    finally:
        index_path.unlink(missing_ok=True)
