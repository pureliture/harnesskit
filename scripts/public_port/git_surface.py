from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .git_environment import curated_git_environment
from .sanitize import PublicSurfaceSnapshot, SurfaceValue
from .secure_tree import SecureTree, SecureTreeError


_OID_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_IDENTITY_RE = re.compile(rb"^(?P<name>.*) <(?P<email>[^<>]+)> [0-9]+ [+-][0-9]{4}$")
_LFS_VERSION = b"version https://git-lfs.github.com/spec/v1"
_LFS_OID_RE = re.compile(rb"^oid sha256:([0-9a-f]{64})$")
_LFS_SIZE_RE = re.compile(rb"^size ([0-9]+)$")
_MAX_LFS_OBJECT_BYTES = 128 * 1024 * 1024
_GIT_COMMAND_TIMEOUT_SECONDS = 120


class GitSurfaceError(ValueError):
    """Git object/ref/LFS coverage is incomplete or cannot be proved."""


@dataclass(frozen=True)
class CandidateGitObjects:
    commit_oid: str
    tag_oid: str | None = None

    def __post_init__(self) -> None:
        if not _OID_RE.fullmatch(self.commit_oid):
            raise ValueError("candidate commit OID is invalid")
        if self.tag_oid is not None and not _OID_RE.fullmatch(self.tag_oid):
            raise ValueError("candidate tag OID is invalid")


@dataclass(frozen=True)
class GitRef:
    name: str
    oid: str


@dataclass(frozen=True)
class GitCoverage:
    complete: bool
    advertised_ref_count: int
    object_count: int
    commit_count: int
    tag_count: int
    tree_count: int
    blob_count: int
    lfs_pointer_count: int
    lfs_object_count: int


@dataclass(frozen=True)
class GitSurfaceSnapshot:
    refs: tuple[GitRef, ...]
    candidate: CandidateGitObjects
    values: tuple[SurfaceValue, ...]
    coverage: GitCoverage
    snapshot_sha256: str

    def as_public_surface_snapshot(self, *, name: str) -> PublicSurfaceSnapshot:
        return PublicSurfaceSnapshot.create(
            name=name,
            values=self.values,
            complete=self.coverage.complete,
        )


def _run(root: Path, *args: str, input_body: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_body,
            check=False,
            capture_output=True,
            env=curated_git_environment(),
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise GitSurfaceError("Git surface command timed out") from error
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitSurfaceError(message or f"git command failed: {' '.join(args)}")
    return result.stdout


def _text(root: Path, *args: str) -> str:
    return _run(root, *args).decode("utf-8", errors="strict").strip()


def _optional_config(root: Path, key: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "config", "--get", key],
            check=False,
            capture_output=True,
            text=True,
            env=curated_git_environment(),
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise GitSurfaceError("Git surface config read timed out") from error
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        raise GitSurfaceError(result.stderr.strip() or f"failed to read Git config {key}")
    return result.stdout.strip()


def _repository_root(root: Path) -> tuple[Path, Path]:
    candidate = Path(root)
    if candidate.is_symlink():
        raise GitSurfaceError("Git surface root must not be a symlink")
    try:
        physical = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise GitSurfaceError("Git surface root does not exist") from error
    reported = Path(_text(physical, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if reported != physical:
        raise GitSurfaceError("Git surface root must be the repository top level")
    common_dir = Path(_text(physical, "rev-parse", "--git-common-dir"))
    if not common_dir.is_absolute():
        common_dir = physical / common_dir
    return physical, common_dir.resolve(strict=True)


def _reject_incomplete_repository(root: Path, common_dir: Path) -> None:
    if _text(root, "rev-parse", "--is-shallow-repository") != "false":
        raise GitSurfaceError("shallow or partial Git repository is not a complete surface")
    if _optional_config(root, "extensions.partialClone") is not None:
        raise GitSurfaceError("shallow or partial Git repository is not a complete surface")
    promisor = _optional_config(root, "remote.origin.promisor")
    if promisor is not None and promisor.lower() in {"true", "1", "yes", "on"}:
        raise GitSurfaceError("shallow or partial Git repository is not a complete surface")
    if (common_dir / "objects" / "info" / "alternates").exists():
        raise GitSurfaceError("Git object alternates are not allowed for public sanitation")


def _advertised_refs(root: Path) -> tuple[GitRef, ...]:
    output = _run(root, "ls-remote", "--refs", "origin")
    refs: list[GitRef] = []
    names: set[str] = set()
    for line in output.decode("utf-8", errors="strict").splitlines():
        try:
            oid, name = line.split("\t", 1)
        except ValueError as error:
            raise GitSurfaceError("advertised Git ref record is malformed") from error
        if not _OID_RE.fullmatch(oid) or not name.startswith("refs/") or name.endswith("^{}"):
            raise GitSurfaceError("advertised Git ref record is invalid")
        if name in names:
            raise GitSurfaceError("advertised Git ref is duplicated")
        names.add(name)
        refs.append(GitRef(name=name, oid=oid))
    if not refs:
        raise GitSurfaceError("advertised Git ref namespace is empty")
    return tuple(sorted(refs, key=lambda ref: ref.name))


def _fetch_and_verify_namespace(
    root: Path,
    refs: tuple[GitRef, ...],
    remote_url: str,
) -> None:
    _run(
        root,
        "fetch",
        "--force",
        "--prune",
        "--no-write-fetch-head",
        remote_url,
        "+refs/*:refs/public-port/remote/*",
    )
    expected = {
        f"refs/public-port/remote/{ref.name.removeprefix('refs/')}": ref.oid for ref in refs
    }
    output = _run(
        root,
        "for-each-ref",
        "--format=%(objectname)%09%(refname)",
        "refs/public-port/remote/",
    )
    observed: dict[str, str] = {}
    for line in output.decode("utf-8", errors="strict").splitlines():
        oid, name = line.split("\t", 1)
        observed[name] = oid
    if observed != expected:
        raise GitSurfaceError("fetched Git ref namespace does not match advertised refs")


def _copy_candidate_objects(source: Path, mirror: Path, oids: list[str]) -> None:
    producer: subprocess.Popen[bytes] | None = None
    consumer: subprocess.Popen[bytes] | None = None
    producer_status: int | None = None
    consumer_error = b""
    producer_error = b""
    try:
        producer = subprocess.Popen(
            ["git", "-C", str(source), "pack-objects", "--stdout", "--revs"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=curated_git_environment(),
        )
        assert producer.stdin is not None
        assert producer.stdout is not None
        assert producer.stderr is not None
        consumer = subprocess.Popen(
            ["git", "-C", str(mirror), "index-pack", "--stdin"],
            stdin=producer.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=curated_git_environment(),
        )
        producer.stdout.close()
        producer.stdin.write(("\n".join(oids) + "\n").encode("ascii"))
        producer.stdin.close()
        _consumer_output, consumer_error = consumer.communicate(
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS
        )
        producer_status = producer.wait(timeout=_GIT_COMMAND_TIMEOUT_SECONDS)
        producer_error = producer.stderr.read()
    except subprocess.TimeoutExpired as error:
        raise GitSurfaceError("candidate Git object isolation timed out") from error
    except OSError as error:
        raise GitSurfaceError("candidate Git objects could not be isolated") from error
    finally:
        for process in (producer, consumer):
            if process is None:
                continue
            if process.poll() is None:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
    assert consumer is not None
    if producer_status != 0 or consumer.returncode != 0:
        message = (producer_error or consumer_error).decode(
            "utf-8", errors="replace"
        ).strip()
        raise GitSurfaceError(message or "candidate Git objects could not be isolated")


def _object_type(root: Path, oid: str) -> str:
    return _text(root, "cat-file", "-t", oid)


def _object_body(root: Path, oid: str) -> bytes:
    return _run(root, "cat-file", "-p", oid)


def _parse_headers(body: bytes) -> tuple[dict[bytes, list[bytes]], bytes]:
    header_body, separator, message = body.partition(b"\n\n")
    if not separator:
        raise GitSurfaceError("Git commit or tag object has no message boundary")
    headers: dict[bytes, list[bytes]] = {}
    current: bytes | None = None
    for line in header_body.splitlines():
        if line.startswith(b" ") and current is not None:
            headers[current][-1] += b"\n" + line
            continue
        key, separator, value = line.partition(b" ")
        if not separator:
            raise GitSurfaceError("Git commit or tag header is malformed")
        headers.setdefault(key, []).append(value)
        current = key
    return headers, message


def _identity_values(surface_prefix: str, locator: str, raw: bytes) -> tuple[SurfaceValue, ...]:
    match = _IDENTITY_RE.fullmatch(raw)
    if match is None:
        raise GitSurfaceError(f"{surface_prefix} identity is malformed")
    return (
        SurfaceValue(f"{surface_prefix}_name", f"{locator}:name", match.group("name")),
        SurfaceValue(f"{surface_prefix}_email", f"{locator}:email", match.group("email")),
    )


def _lfs_pointer(body: bytes) -> tuple[str, int] | None:
    if not body.startswith(_LFS_VERSION):
        return None
    lines = body.rstrip(b"\n").splitlines()
    if len(lines) != 3 or lines[0] != _LFS_VERSION:
        raise GitSurfaceError("LFS pointer is malformed")
    oid_match = _LFS_OID_RE.fullmatch(lines[1])
    size_match = _LFS_SIZE_RE.fullmatch(lines[2])
    if oid_match is None or size_match is None:
        raise GitSurfaceError("LFS pointer is malformed")
    return oid_match.group(1).decode("ascii"), int(size_match.group(1))


def _read_lfs_object(common_dir: Path, oid: str, declared_size: int) -> bytes:
    relative_path = f"lfs/objects/{oid[:2]}/{oid[2:4]}/{oid}"
    try:
        with SecureTree(common_dir) as tree:
            return tree.read_file(relative_path, max_bytes=declared_size)
    except SecureTreeError as error:
        if isinstance(error.__cause__, FileNotFoundError):
            raise GitSurfaceError(f"LFS object is missing: {oid}") from error
        raise GitSurfaceError(
            f"LFS object violates the no-follow bounded-read contract: {oid}"
        ) from error


def _canonical_digest(value: object) -> str:
    body = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _read_isolated_surface(
    repository: Path,
    *,
    common_dir: Path,
    refs: tuple[GitRef, ...],
    candidate: CandidateGitObjects,
) -> GitSurfaceSnapshot:
    candidate_oids = [candidate.commit_oid]
    if candidate.tag_oid is not None:
        candidate_oids.append(candidate.tag_oid)
    for oid in candidate_oids:
        _object_type(repository, oid)
    if _object_type(repository, candidate.commit_oid) != "commit":
        raise GitSurfaceError("candidate commit OID does not name a commit")
    if candidate.tag_oid is not None and _object_type(repository, candidate.tag_oid) != "tag":
        raise GitSurfaceError("candidate tag OID does not name an annotated tag")

    roots = sorted({ref.oid for ref in refs} | set(candidate_oids))
    output = _run(repository, "rev-list", "--objects", "--no-object-names", *roots)
    object_oids = set(output.decode("ascii", errors="strict").splitlines()) | set(roots)
    if not object_oids or any(not _OID_RE.fullmatch(oid) for oid in object_oids):
        raise GitSurfaceError("reachable Git object traversal was incomplete")

    values: dict[tuple[str, str], SurfaceValue] = {}
    for ref in refs:
        value = SurfaceValue("git.ref.name", f"ref:{ref.oid}:{ref.name}", ref.name.encode("utf-8"))
        values[(value.surface, value.locator)] = value
    counts = {"commit": 0, "tag": 0, "tree": 0, "blob": 0}
    lfs_objects: dict[str, bytes] = {}
    lfs_pointer_count = 0
    for oid in sorted(object_oids):
        object_type = _object_type(repository, oid)
        if object_type not in counts:
            raise GitSurfaceError(f"reachable Git object type is unsupported: {object_type}")
        counts[object_type] += 1
        body = _object_body(repository, oid)
        if oid == candidate.commit_oid:
            locator = "candidate:commit"
        elif candidate.tag_oid is not None and oid == candidate.tag_oid:
            locator = "candidate:tag"
        else:
            locator = f"object:{oid}"
        if object_type == "commit":
            headers, message = _parse_headers(body)
            if len(headers.get(b"author", [])) != 1 or len(headers.get(b"committer", [])) != 1:
                raise GitSurfaceError("Git commit identity headers are incomplete")
            for value in _identity_values("git.commit.author", f"{locator}:author", headers[b"author"][0]):
                values[(value.surface, value.locator)] = value
            for value in _identity_values(
                "git.commit.committer", f"{locator}:committer", headers[b"committer"][0]
            ):
                values[(value.surface, value.locator)] = value
            value = SurfaceValue("git.commit.message", f"{locator}:message", message)
            values[(value.surface, value.locator)] = value
            tree_output = _run(repository, "ls-tree", "-rz", "-r", "--full-tree", oid)
            for record in tree_output.split(b"\0"):
                if not record:
                    continue
                _metadata, separator, path = record.partition(b"\t")
                if not separator:
                    raise GitSurfaceError("Git tree path record is malformed")
                path_value = SurfaceValue(
                    "git.tree.path",
                    f"{locator}:path:{hashlib.sha256(path).hexdigest()}",
                    path,
                )
                values[(path_value.surface, path_value.locator)] = path_value
        elif object_type == "tag":
            headers, message = _parse_headers(body)
            if len(headers.get(b"tagger", [])) != 1 or len(headers.get(b"tag", [])) != 1:
                raise GitSurfaceError("annotated tag identity headers are incomplete")
            for value in _identity_values("git.tag.tagger", f"{locator}:tagger", headers[b"tagger"][0]):
                values[(value.surface, value.locator)] = value
            for value in (
                SurfaceValue("git.tag.name", f"{locator}:name", headers[b"tag"][0]),
                SurfaceValue("git.tag.message", f"{locator}:message", message),
            ):
                values[(value.surface, value.locator)] = value
        elif object_type == "blob":
            value = SurfaceValue("git.blob", locator, body)
            values[(value.surface, value.locator)] = value
            pointer = _lfs_pointer(body)
            if pointer is not None:
                lfs_pointer_count += 1
                lfs_oid, declared_size = pointer
                if declared_size > _MAX_LFS_OBJECT_BYTES:
                    raise GitSurfaceError(
                        f"LFS object exceeds the reviewed maximum: {lfs_oid}"
                    )
                lfs_body = _read_lfs_object(common_dir, lfs_oid, declared_size)
                if len(lfs_body) != declared_size:
                    raise GitSurfaceError(f"LFS object size mismatch: {lfs_oid}")
                if hashlib.sha256(lfs_body).hexdigest() != lfs_oid:
                    raise GitSurfaceError(f"LFS object digest mismatch: {lfs_oid}")
                lfs_objects[lfs_oid] = lfs_body

    for oid, body in sorted(lfs_objects.items()):
        value = SurfaceValue("git.lfs.object", f"lfs:{oid}", body)
        values[(value.surface, value.locator)] = value

    normalized_values = tuple(
        sorted(values.values(), key=lambda value: (value.surface, value.locator))
    )
    coverage = GitCoverage(
        complete=True,
        advertised_ref_count=len(refs),
        object_count=len(object_oids),
        commit_count=counts["commit"],
        tag_count=counts["tag"],
        tree_count=counts["tree"],
        blob_count=counts["blob"],
        lfs_pointer_count=lfs_pointer_count,
        lfs_object_count=len(lfs_objects),
    )
    public_snapshot = PublicSurfaceSnapshot.create(
        name="git",
        values=normalized_values,
        complete=True,
    )
    snapshot_sha256 = _canonical_digest(
        {
            "refs": [[ref.name, ref.oid] for ref in refs],
            "candidate": [candidate.commit_oid, candidate.tag_oid],
            "surface": public_snapshot.snapshot_sha256,
            "coverage": [
                coverage.advertised_ref_count,
                coverage.object_count,
                coverage.commit_count,
                coverage.tag_count,
                coverage.tree_count,
                coverage.blob_count,
                coverage.lfs_pointer_count,
                coverage.lfs_object_count,
            ],
        }
    )
    return GitSurfaceSnapshot(
        refs=refs,
        candidate=candidate,
        values=normalized_values,
        coverage=coverage,
        snapshot_sha256=snapshot_sha256,
    )


def read_git_surface(
    root: Path,
    *,
    candidate: CandidateGitObjects,
) -> GitSurfaceSnapshot:
    """Read remote refs and candidate objects from a disposable isolated mirror."""

    source, common_dir = _repository_root(Path(root))
    _reject_incomplete_repository(source, common_dir)
    refs = _advertised_refs(source)
    remote_url = _text(source, "remote", "get-url", "origin")
    candidate_oids = [candidate.commit_oid]
    if candidate.tag_oid is not None:
        candidate_oids.append(candidate.tag_oid)
    for oid in candidate_oids:
        _object_type(source, oid)

    with tempfile.TemporaryDirectory(prefix="harnesskit-git-surface-") as temporary:
        temporary_root = Path(temporary)
        mirror = temporary_root / "inspection.git"
        _run(temporary_root, "init", "--bare", str(mirror))
        _fetch_and_verify_namespace(mirror, refs, remote_url)
        _copy_candidate_objects(source, mirror, candidate_oids)
        if _advertised_refs(source) != refs:
            raise GitSurfaceError("advertised Git ref namespace changed during observation")
        return _read_isolated_surface(
            mirror,
            common_dir=common_dir,
            refs=refs,
            candidate=candidate,
        )
