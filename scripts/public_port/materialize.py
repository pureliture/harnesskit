from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .content_policy import PublicContentError, validate_public_blob
from .git_environment import curated_git_environment
from .manifest import (
    ManifestError,
    PublicRemoteDefaultReader,
    TrackedBlob,
    read_git_blob,
    read_public_remote_default,
    repository_root,
    sha256_bytes,
    tracked_blobs,
    validate_manifest,
    validate_manifest_policy_authority,
)
from .policy import CANONICAL_PUBLIC_REPOSITORY_URL, PublicPortPolicy
from .secure_tree import SecureTree, SecureTreeError


class MaterializeError(ValueError):
    """The destination is not an exact clean public baseline or bytes drifted."""


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        env=curated_git_environment(),
    )
    if result.returncode != 0:
        raise MaterializeError(result.stderr.strip() or "public clone git preflight failed")
    return result.stdout.strip()


def _roots_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _git_common_directory(root: Path) -> Path:
    common = Path(_git(root, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = root / common
    return common.resolve(strict=True)


def validate_repository_pair(source_root: Path, public_clone_root: Path) -> tuple[Path, Path]:
    try:
        source = repository_root(Path(source_root), "private source")
        clone = repository_root(Path(public_clone_root), "public clone")
    except ManifestError as error:
        raise MaterializeError(str(error)) from error
    if _roots_overlap(source, clone):
        raise MaterializeError("private source and public clone must be disjoint roots")
    git_directory = clone / ".git"
    if git_directory.is_symlink() or not git_directory.is_dir():
        raise MaterializeError("public destination must be a full clone with a .git directory")
    if (git_directory / "objects/info/alternates").exists():
        raise MaterializeError("public destination must not use shared Git object storage")
    if _git_common_directory(source) == _git_common_directory(clone):
        raise MaterializeError("private source and public clone must not share Git object storage")
    return source, clone


def inventory_tree(root: Path, *, forbid_git: bool = False) -> dict[str, dict[str, str]]:
    try:
        with SecureTree(root) as tree:
            return tree.inventory(
                skip_root_names=() if forbid_git else (".git",),
                forbidden_root_names=(".git",) if forbid_git else (),
            )
    except SecureTreeError as error:
        raise MaterializeError(str(error)) from error


ReviewedFileTree = Mapping[str, tuple[bytes, str]]
ExpectedInventory = Mapping[str, Mapping[str, str]]


def expected_inventory(entries: Iterable[Mapping[str, str]]) -> dict[str, dict[str, str]]:
    return {
        entry["path"]: {
            "path": entry["path"],
            "mode": entry["mode"],
            "sha256": entry["sha256"],
        }
        for entry in entries
    }


def _read_reviewed_git_entries(
    root: Path,
    revision: str,
    entries: list[Mapping[str, str]],
    *,
    label: str,
    enforce_public_content: bool = False,
) -> dict[str, tuple[bytes, str]]:
    blobs = tracked_blobs(root, revision)
    result: dict[str, tuple[bytes, str]] = {}
    for entry in entries:
        blob: TrackedBlob | None = blobs.get(entry["path"])
        if blob is None or blob.mode != entry["mode"]:
            raise MaterializeError(f"{label} Git blob is unavailable: {entry['path']}")
        body = read_git_blob(root, blob.oid)
        if enforce_public_content:
            try:
                validate_public_blob(entry["path"], body)
            except PublicContentError as error:
                raise MaterializeError(str(error)) from error
        if sha256_bytes(body) != entry["sha256"]:
            raise MaterializeError(f"{label} hash mismatch: {entry['path']}")
        result[entry["path"]] = (body, blob.mode)
    return result


def _clone_exact_baseline(
    source_clone: Path,
    destination: Path,
    *,
    commit: str,
    branch: str,
) -> None:
    result = subprocess.run(
        ["git", "clone", "--no-local", "--no-checkout", "--", str(source_clone), str(destination)],
        check=False,
        capture_output=True,
        text=True,
        env=curated_git_environment(),
    )
    if result.returncode != 0:
        raise MaterializeError(result.stderr.strip() or "failed to create disposable public clone")
    _git(destination, "checkout", "-B", branch, commit)
    _git(destination, "remote", "set-url", "origin", CANONICAL_PUBLIC_REPOSITORY_URL)


def promote_reviewed_tree(
    clone_root: Path,
    *,
    baseline_commit: str,
    baseline_branch: str,
    files: ReviewedFileTree,
    expected: ExpectedInventory,
    stage_prefix: str,
    stage_error_message: str,
    promoted_error_message: str,
    validator: Callable[[Path], None] | None = None,
) -> None:
    stage_container = Path(tempfile.mkdtemp(prefix=stage_prefix, dir=clone_root.parent))
    stage = stage_container / "candidate"
    try:
        _clone_exact_baseline(
            clone_root,
            stage,
            commit=baseline_commit,
            branch=baseline_branch,
        )
        with SecureTree(stage) as stage_tree:
            stage_tree.clear(keep_root_names=(".git",))
            for relative_path, (body, mode) in sorted(files.items()):
                stage_tree.write_file(relative_path, body, mode)
        if inventory_tree(stage) != expected:
            raise MaterializeError(stage_error_message)

        def validate_promoted(root: Path) -> None:
            if inventory_tree(root) != expected:
                raise MaterializeError(promoted_error_message)
            if validator is not None:
                validator(root)

        _promote_stage(stage, clone_root, validator=validate_promoted)
    finally:
        if stage_container.exists():
            shutil.rmtree(stage_container)


def _promote_stage(
    stage: Path,
    destination: Path,
    *,
    validator: Callable[[Path], None],
) -> None:
    backup_container = Path(
        tempfile.mkdtemp(prefix=".public-port-backup-", dir=destination.parent)
    )
    backup = backup_container / "previous"
    failed_candidate = backup_container / "failed-candidate"
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    destination_parent_fd = os.open(destination.parent, directory_flags)
    stage_parent_fd = os.open(stage.parent, directory_flags)
    backup_parent_fd = os.open(backup_container, directory_flags)
    stage_identity = os.stat(stage.name, dir_fd=stage_parent_fd, follow_symlinks=False)
    backed_up = False
    try:
        os.rename(
            destination.name,
            backup.name,
            src_dir_fd=destination_parent_fd,
            dst_dir_fd=backup_parent_fd,
        )
        backed_up = True
        os.rename(
            stage.name,
            destination.name,
            src_dir_fd=stage_parent_fd,
            dst_dir_fd=destination_parent_fd,
        )
        promoted_identity = os.stat(
            destination.name,
            dir_fd=destination_parent_fd,
            follow_symlinks=False,
        )
        if (promoted_identity.st_dev, promoted_identity.st_ino) != (
            stage_identity.st_dev,
            stage_identity.st_ino,
        ):
            raise MaterializeError("promoted public clone identity drifted")
        validator(destination)
    except Exception:
        if backed_up:
            try:
                os.stat(destination.name, dir_fd=destination_parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                os.rename(
                    destination.name,
                    failed_candidate.name,
                    src_dir_fd=destination_parent_fd,
                    dst_dir_fd=backup_parent_fd,
                )
            os.rename(
                backup.name,
                destination.name,
                src_dir_fd=backup_parent_fd,
                dst_dir_fd=destination_parent_fd,
            )
            backed_up = False
        raise
    finally:
        os.close(backup_parent_fd)
        os.close(stage_parent_fd)
        os.close(destination_parent_fd)
        if not backed_up:
            if failed_candidate.exists():
                shutil.rmtree(failed_candidate, ignore_errors=True)
            try:
                backup_container.rmdir()
            except OSError:
                pass
    shutil.rmtree(backup)
    backup_container.rmdir()


def materialize(
    manifest: Mapping[str, Any],
    *,
    policy: PublicPortPolicy,
    source_root: Path,
    public_clone_root: Path,
    remote_default_reader: PublicRemoteDefaultReader = read_public_remote_default,
) -> dict[str, Any]:
    try:
        validate_manifest(manifest)
    except ManifestError as error:
        raise MaterializeError(str(error)) from error

    source_root, clone_root = validate_repository_pair(source_root, public_clone_root)
    if _git(clone_root, "rev-parse", "--is-shallow-repository") != "false":
        raise MaterializeError("public destination must be a non-shallow full clone")
    if _git(clone_root, "remote", "get-url", "origin") != CANONICAL_PUBLIC_REPOSITORY_URL:
        raise MaterializeError("public clone origin URL is not canonical")
    remote_default = remote_default_reader(CANONICAL_PUBLIC_REPOSITORY_URL)
    if (
        remote_default.commit != manifest["baseline"]["commit"]
        or remote_default.branch != manifest["baseline"]["default_branch"]
    ):
        raise MaterializeError("public remote default-branch tip drifted after manifest review")
    try:
        clone_branch = _git(clone_root, "symbolic-ref", "--short", "HEAD")
    except MaterializeError as error:
        raise MaterializeError("public clone must not be detached") from error
    if clone_branch != remote_default.branch:
        raise MaterializeError("public clone is not on the public remote default branch")
    if (
        _git(clone_root, "rev-parse", "HEAD") != manifest["baseline"]["commit"]
        or _git(clone_root, "status", "--porcelain")
    ):
        raise MaterializeError("public clone must be a clean exact baseline")

    baseline_revision = manifest["baseline"]["commit"]
    baseline_blobs = tracked_blobs(clone_root, baseline_revision)
    source_revision = _git(source_root, "rev-parse", "HEAD")
    source_blobs = tracked_blobs(source_root, source_revision)
    try:
        validate_manifest_policy_authority(
            manifest,
            policy,
            source_paths=source_blobs,
            baseline_paths=baseline_blobs,
        )
    except ManifestError as error:
        raise MaterializeError(str(error)) from error
    baseline_inventory = inventory_tree(clone_root)
    if set(baseline_inventory) != set(baseline_blobs):
        raise MaterializeError("public clone must be a clean exact baseline without ignored files")

    retained = expected_inventory(manifest["retained_baseline"])
    authored_paths = {entry["path"] for entry in manifest["ported_authored"]}
    generated_paths = {
        entry["path"] for rule in manifest["generated_rules"] for entry in rule["outputs"]
    }
    removed_paths = set(manifest["intentional_removals"])
    classified = set(retained) | authored_paths | generated_paths | removed_paths
    unclassified = sorted(set(baseline_blobs) - classified)
    if unclassified:
        raise MaterializeError(f"baseline path is not classified by manifest: {unclassified[0]}")
    invalid_baseline_only = sorted((set(retained) | removed_paths) - set(baseline_blobs))
    if invalid_baseline_only:
        raise MaterializeError(
            f"baseline-only category contains a non-baseline path: {invalid_baseline_only[0]}"
        )
    for path, expected in retained.items():
        if baseline_inventory.get(path) != expected:
            raise MaterializeError(f"retained baseline hash mismatch: {path}")

    retained_bodies = _read_reviewed_git_entries(
        clone_root,
        baseline_revision,
        list(manifest["retained_baseline"]),
        label="retained baseline",
        enforce_public_content=True,
    )
    authored_bodies = _read_reviewed_git_entries(
        source_root,
        source_revision,
        list(manifest["ported_authored"]),
        label="authored",
        enforce_public_content=True,
    )
    materialized = {**retained_bodies, **authored_bodies}
    pre_generation_entries = list(manifest["retained_baseline"]) + list(manifest["ported_authored"])
    expected_pre_tree = expected_inventory(pre_generation_entries)
    try:
        def validate_promoted(root: Path) -> None:
            if _git(root, "remote", "get-url", "origin") != CANONICAL_PUBLIC_REPOSITORY_URL:
                raise MaterializeError("promoted public clone origin URL drifted")

        promote_reviewed_tree(
            clone_root,
            baseline_commit=baseline_revision,
            baseline_branch=manifest["baseline"]["default_branch"],
            files=materialized,
            expected=expected_pre_tree,
            stage_prefix=".public-port-materialize-",
            stage_error_message="materialized pre-generation tree is not closed",
            promoted_error_message="promoted pre-generation tree drifted",
            validator=validate_promoted,
        )
    except (ManifestError, SecureTreeError, OSError, subprocess.SubprocessError) as error:
        raise MaterializeError(str(error)) from error

    return {
        "baseline_commit": manifest["baseline"]["commit"],
        "materialized_paths": sorted(expected_pre_tree),
    }
