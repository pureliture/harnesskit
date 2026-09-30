from __future__ import annotations

import hashlib
import json
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import jsonschema

from .content_policy import PublicContentError, validate_public_blob
from .git_environment import curated_git_environment
from .policy import (
    CANONICAL_PUBLIC_REPOSITORY_URL,
    PolicyError,
    PublicPortPolicy,
    path_matches_any,
    policy_sha256,
    reject_forbidden_authored_paths,
    validate_relative_path,
)
from .secure_tree import SecureTree, SecureTreeError


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas/public-port-manifest-v1.json"
_REGULAR_MODES = {"100644", "100755"}
_GIT_COMMAND_TIMEOUT_SECONDS = 120


class ManifestError(ValueError):
    """A public-port manifest cannot be proved from the reviewed inputs."""


@dataclass(frozen=True)
class PublicRemoteDefault:
    branch: str
    commit: str


@dataclass(frozen=True)
class TrackedBlob:
    mode: str
    oid: str


PublicRemoteDefaultReader = Callable[[str], PublicRemoteDefault]


def read_public_remote_default(repository_url: str) -> PublicRemoteDefault:
    if repository_url != CANONICAL_PUBLIC_REPOSITORY_URL:
        raise ManifestError("remote default reader only accepts the canonical public repository")
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--symref", repository_url, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            env=curated_git_environment(),
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise ManifestError("public remote default-branch lookup timed out") from error
    if result.returncode != 0:
        raise ManifestError(result.stderr.strip() or "public remote default-branch lookup failed")
    branch: str | None = None
    commit: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            branch = line.split("\t", 1)[0].removeprefix("ref: refs/heads/")
        elif line.endswith("\tHEAD"):
            commit = line.split("\t", 1)[0]
    if not branch or not commit or len(commit) not in {40, 64}:
        raise ManifestError("public remote default-branch lookup was incomplete")
    return PublicRemoteDefault(branch=branch, commit=commit)


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            env=curated_git_environment(),
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise ManifestError("public manifest Git command timed out") from error
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ManifestError(message or f"git command failed in {root}")
    return result.stdout


def _git(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8", errors="strict").strip()


def repository_root(root: Path, label: str) -> Path:
    candidate = Path(root)
    if candidate.is_symlink():
        raise ManifestError(f"{label} root must not be a symlink")
    try:
        physical = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise ManifestError(f"{label} root does not exist") from error
    reported = Path(_git(physical, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if reported != physical:
        raise ManifestError(f"{label} root must be the Git repository top level")
    return physical


def _require_clean_repository(root: Path, label: str) -> None:
    if _git(root, "status", "--porcelain"):
        raise ManifestError(f"{label} repository must be clean")


def tracked_blobs(root: Path, revision: str) -> dict[str, TrackedBlob]:
    output = _git_bytes(root, "ls-tree", "-rz", "--full-tree", revision)
    entries: dict[str, TrackedBlob] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, oid = metadata.decode("ascii").split(" ", 2)
        path = raw_path.decode("utf-8")
        try:
            validate_relative_path(path, "tracked path")
        except PolicyError as error:
            raise ManifestError(str(error)) from error
        if object_type != "blob" or mode not in _REGULAR_MODES:
            raise ManifestError(f"tracked path is not a regular file blob: {path}")
        entries[path] = TrackedBlob(mode=mode, oid=oid)
    reject_path_identity_collisions(entries)
    return entries


def _tracked_entries(root: Path, revision: str) -> dict[str, str]:
    return {path: blob.mode for path, blob in tracked_blobs(root, revision).items()}


def read_git_blob(root: Path, oid: str) -> bytes:
    if len(oid) not in {40, 64} or any(character not in "0123456789abcdef" for character in oid):
        raise ManifestError("Git blob OID is invalid")
    return _git_bytes(root, "cat-file", "blob", oid)


def read_regular_no_follow(root: Path, relative_path: str) -> bytes:
    try:
        with SecureTree(root) as tree:
            return tree.read_file(relative_path)
    except SecureTreeError as error:
        raise ManifestError(str(error)) from error


def _entry_from_blob(
    root: Path,
    path: str,
    blob: TrackedBlob,
    *,
    origin: str | None = None,
    enforce_public_content: bool = False,
) -> dict[str, str]:
    body = read_git_blob(root, blob.oid)
    if enforce_public_content:
        try:
            validate_public_blob(path, body)
        except PublicContentError as error:
            raise ManifestError(str(error)) from error
    result = {
        "path": path,
        "mode": blob.mode,
        "sha256": sha256_bytes(body),
    }
    if origin is not None:
        result["origin"] = origin
    return result


def _tree_digest(entries: Iterable[Mapping[str, str]]) -> str:
    normalized = [
        {"path": entry["path"], "mode": entry["mode"], "sha256": entry["sha256"]}
        for entry in sorted(entries, key=lambda item: item["path"])
    ]
    return sha256_bytes(canonical_json(normalized))


def _entries_with_origin(entries: Iterable[Mapping[str, str]], origin: str) -> list[dict[str, str]]:
    return [{**entry, "origin": origin} for entry in entries]


def _manifest_categories(
    manifest: Mapping[str, Any],
) -> tuple[tuple[str, list[str], set[str]], ...]:
    retained = [entry["path"] for entry in manifest["retained_baseline"]]
    authored = [entry["path"] for entry in manifest["ported_authored"]]
    generated = [
        entry["path"]
        for rule in manifest["generated_rules"]
        for entry in rule["outputs"]
    ]
    removed = list(manifest["intentional_removals"])
    return (
        ("retained_baseline", retained, set(retained)),
        ("ported_authored", authored, set(authored)),
        ("generated", generated, set(generated)),
        ("intentional_removals", removed, set(removed)),
    )


def reject_path_identity_collisions(paths: Iterable[str]) -> None:
    identities: dict[str, str] = {}
    for path in paths:
        identity = unicodedata.normalize("NFC", path).casefold()
        previous = identities.get(identity)
        if previous is not None and previous != path:
            raise ManifestError(f"public path normalization collision: {previous} <> {path}")
        identities[identity] = path


def _roots_are_nested(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _select_authored_paths(
    policy: PublicPortPolicy,
    source_paths: Iterable[str],
) -> set[str]:
    tracked_paths = set(source_paths)
    included: set[str] = set()
    for pattern in policy.ported_includes:
        matched = {path for path in tracked_paths if path_matches_any(path, (pattern,))}
        if not matched:
            raise ManifestError(f"ported authored include matched no tracked files: {pattern}")
        included.update(matched)
    try:
        reject_forbidden_authored_paths(included)
    except PolicyError as error:
        raise ManifestError(str(error)) from error
    return {
        path for path in included if not path_matches_any(path, policy.ported_excludes)
    }


def build_manifest(
    policy: PublicPortPolicy,
    *,
    baseline_root: Path,
    baseline_commit: str,
    source_root: Path,
    require_clean_source: bool = True,
    remote_default_reader: PublicRemoteDefaultReader = read_public_remote_default,
) -> dict[str, Any]:
    baseline_root = repository_root(Path(baseline_root), "baseline")
    source_root = repository_root(Path(source_root), "source")
    if _roots_are_nested(baseline_root, source_root):
        raise ManifestError("baseline and private source must be physically disjoint roots")
    if policy.target_repository_url != CANONICAL_PUBLIC_REPOSITORY_URL:
        raise ManifestError("policy target is not the canonical public repository")
    if _git(baseline_root, "remote", "get-url", "origin") != CANONICAL_PUBLIC_REPOSITORY_URL:
        raise ManifestError("baseline origin is not the canonical public repository")
    remote_default = remote_default_reader(CANONICAL_PUBLIC_REPOSITORY_URL)
    if remote_default.commit != baseline_commit:
        raise ManifestError("baseline commit is not the public remote default-branch tip")
    try:
        baseline_branch = _git(baseline_root, "symbolic-ref", "--short", "HEAD")
    except ManifestError as error:
        raise ManifestError("baseline checkout must not be detached") from error
    if baseline_branch != remote_default.branch:
        raise ManifestError("baseline checkout is not on the public remote default branch")
    if _git(baseline_root, "rev-parse", "HEAD") != baseline_commit:
        raise ManifestError("baseline checkout HEAD does not match baseline commit")
    _require_clean_repository(baseline_root, "baseline")
    if require_clean_source:
        _require_clean_repository(source_root, "source")

    baseline_blobs = tracked_blobs(baseline_root, baseline_commit)
    source_revision = _git(source_root, "rev-parse", "HEAD")
    source_blobs = tracked_blobs(source_root, source_revision)

    included = _select_authored_paths(policy, source_blobs)

    generated_paths = {output for rule in policy.generated_rules for output in rule.outputs}
    authored_generated_overlap = included.intersection(generated_paths)
    if authored_generated_overlap:
        raise ManifestError(
            f"path cannot be both authored and generated: {sorted(authored_generated_overlap)[0]}"
        )
    removals = set(policy.intentional_removals)
    authored_removed_overlap = included.intersection(removals)
    if authored_removed_overlap:
        raise ManifestError(
            f"path cannot be both authored and removed: {sorted(authored_removed_overlap)[0]}"
        )
    missing_removals = sorted(removals - set(baseline_blobs))
    if missing_removals:
        raise ManifestError(f"intentional removal is absent from baseline: {missing_removals[0]}")

    retained_paths = sorted(set(baseline_blobs) - included - generated_paths - removals)
    retained = [
        _entry_from_blob(
            baseline_root,
            path,
            baseline_blobs[path],
            enforce_public_content=True,
        )
        for path in retained_paths
    ]
    authored = [
        _entry_from_blob(
            source_root,
            path,
            source_blobs[path],
            enforce_public_content=True,
        )
        for path in sorted(included)
    ]

    generated_rules: list[dict[str, Any]] = []
    generated_entries: list[dict[str, str]] = []
    available_inputs = set(retained_paths) | included
    generated_so_far: set[str] = set()
    for rule in policy.generated_rules:
        input_entries: list[dict[str, str]] = []
        for path in rule.inputs:
            if path not in available_inputs:
                raise ManifestError(f"generated input is not materialized before its rule: {path}")
            if path in included or path in generated_so_far:
                root = source_root
                blobs = source_blobs
            else:
                root = baseline_root
                blobs = baseline_blobs
            input_entries.append(_entry_from_blob(root, path, blobs[path]))
        output_entries: list[dict[str, str]] = []
        for path in rule.outputs:
            if path not in source_blobs:
                raise ManifestError(f"reviewed generated output is not tracked in source: {path}")
            output = _entry_from_blob(
                source_root,
                path,
                source_blobs[path],
                enforce_public_content=True,
            )
            output_entries.append(output)
            generated_entries.append(output)
            available_inputs.add(path)
            generated_so_far.add(path)
        generated_rules.append(
            {
                "id": rule.rule_id,
                "inputs": input_entries,
                "outputs": output_entries,
            }
        )

    expected_tree = (
        _entries_with_origin(retained, "retained_baseline")
        + _entries_with_origin(authored, "ported_authored")
        + _entries_with_origin(generated_entries, "generated")
    )
    expected_tree.sort(key=lambda item: item["path"])
    reject_path_identity_collisions(entry["path"] for entry in expected_tree)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "target_repository_url": CANONICAL_PUBLIC_REPOSITORY_URL,
        "policy_sha256": policy_sha256(policy),
        "baseline": {"commit": baseline_commit, "default_branch": remote_default.branch},
        "retained_baseline": retained,
        "ported_authored": authored,
        "generated_rules": generated_rules,
        "intentional_removals": sorted(removals),
        "expected_tree": expected_tree,
        "expected_tree_sha256": _tree_digest(expected_tree),
    }
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest))
    validate_manifest_policy_authority(
        manifest,
        policy,
        source_paths=source_blobs,
        baseline_paths=baseline_blobs,
    )
    return manifest


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=manifest, schema=schema)
    except jsonschema.ValidationError as error:
        raise ManifestError(f"public-port manifest schema validation failed: {error.message}") from error
    if manifest["target_repository_url"] != CANONICAL_PUBLIC_REPOSITORY_URL:
        raise ManifestError("manifest target is not canonical")

    category_data = _manifest_categories(manifest)
    categories = [paths for _label, _raw_paths, paths in category_data]
    for label, raw_paths, unique_paths in category_data:
        if len(raw_paths) != len(unique_paths):
            raise ManifestError(f"manifest category contains duplicate paths: {label}")
    reject_path_identity_collisions(path for category in categories for path in category)
    for index, left in enumerate(categories):
        for right in categories[index + 1 :]:
            overlap = left.intersection(right)
            if overlap:
                raise ManifestError(f"manifest categories overlap at {sorted(overlap)[0]}")

    available = {
        entry["path"]: entry
        for entry in list(manifest["retained_baseline"]) + list(manifest["ported_authored"])
    }
    rule_ids: set[str] = set()
    for rule in manifest["generated_rules"]:
        if rule["id"] in rule_ids:
            raise ManifestError(f"generated rule id is duplicated: {rule['id']}")
        rule_ids.add(rule["id"])
        for entry in rule["inputs"]:
            predecessor = available.get(entry["path"])
            if predecessor is None:
                raise ManifestError(f"generated input is unavailable before rule {rule['id']}: {entry['path']}")
            if entry["mode"] != predecessor["mode"] or entry["sha256"] != predecessor["sha256"]:
                raise ManifestError(f"generated input digest mismatch: {entry['path']}")
        for entry in rule["outputs"]:
            available[entry["path"]] = entry

    expected_paths = {entry["path"] for entry in manifest["expected_tree"]}
    if len(expected_paths) != len(manifest["expected_tree"]):
        raise ManifestError("expected_tree contains duplicate paths")
    if expected_paths != categories[0] | categories[1] | categories[2]:
        raise ManifestError("expected_tree does not close the retained/authored/generated union")
    expected_origins = {
        **{path: "retained_baseline" for path in categories[0]},
        **{path: "ported_authored" for path in categories[1]},
        **{path: "generated" for path in categories[2]},
    }
    for entry in manifest["expected_tree"]:
        if entry["origin"] != expected_origins[entry["path"]]:
            raise ManifestError(f"expected_tree origin mismatch: {entry['path']}")
        authoritative = available[entry["path"]]
        if (
            entry["mode"] != authoritative["mode"]
            or entry["sha256"] != authoritative["sha256"]
        ):
            raise ManifestError(f"expected_tree entry mismatch: {entry['path']}")
    if manifest["expected_tree_sha256"] != _tree_digest(manifest["expected_tree"]):
        raise ManifestError("expected_tree_sha256 mismatch")
    without_digest = dict(manifest)
    actual_digest = without_digest.pop("manifest_sha256")
    if actual_digest != sha256_bytes(canonical_json(without_digest)):
        raise ManifestError("manifest_sha256 mismatch")


def validate_manifest_policy_authority(
    manifest: Mapping[str, Any],
    policy: PublicPortPolicy,
    *,
    source_paths: Iterable[str],
    baseline_paths: Iterable[str],
) -> None:
    """Bind a self-consistent manifest to one reviewed policy and current Git path closure."""

    validate_manifest(manifest)
    if manifest["policy_sha256"] != policy_sha256(policy):
        raise ManifestError("manifest does not match the reviewed policy digest")
    if manifest["target_repository_url"] != policy.target_repository_url:
        raise ManifestError("manifest target does not match the reviewed policy")

    source_path_set = set(source_paths)
    baseline_path_set = set(baseline_paths)
    expected_authored = _select_authored_paths(policy, source_path_set)
    observed_authored = {entry["path"] for entry in manifest["ported_authored"]}
    if observed_authored != expected_authored:
        raise ManifestError("manifest authored paths do not match the reviewed policy")

    expected_removals = set(policy.intentional_removals)
    observed_removals = set(manifest["intentional_removals"])
    if observed_removals != expected_removals:
        raise ManifestError("manifest removals do not match the reviewed policy")
    missing_removals = expected_removals - baseline_path_set
    if missing_removals:
        raise ManifestError(
            f"reviewed removal is absent from baseline: {sorted(missing_removals)[0]}"
        )

    policy_rules = list(policy.generated_rules)
    manifest_rules = list(manifest["generated_rules"])
    if [rule["id"] for rule in manifest_rules] != [rule.rule_id for rule in policy_rules]:
        raise ManifestError("manifest generated rules do not match the reviewed policy")
    for manifest_rule, policy_rule in zip(manifest_rules, policy_rules):
        if [entry["path"] for entry in manifest_rule["inputs"]] != list(policy_rule.inputs):
            raise ManifestError(
                f"generated inputs do not match the reviewed policy: {policy_rule.rule_id}"
            )
        if [entry["path"] for entry in manifest_rule["outputs"]] != list(policy_rule.outputs):
            raise ManifestError(
                f"generated outputs do not match the reviewed policy: {policy_rule.rule_id}"
            )

    generated_paths = {output for rule in policy_rules for output in rule.outputs}
    expected_retained = (
        baseline_path_set - expected_authored - generated_paths - expected_removals
    )
    observed_retained = {entry["path"] for entry in manifest["retained_baseline"]}
    if observed_retained != expected_retained:
        raise ManifestError("manifest retained paths do not match the reviewed policy")
