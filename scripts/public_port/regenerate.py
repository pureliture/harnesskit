from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from .content_policy import PublicContentError, validate_public_blob
from .manifest import (
    ManifestError,
    canonical_json,
    sha256_bytes,
    tracked_blobs,
    validate_manifest,
    validate_manifest_policy_authority,
)
from .materialize import (
    MaterializeError,
    expected_inventory,
    inventory_tree,
    promote_reviewed_tree,
    validate_repository_pair,
)
from .policy import GeneratedRulePolicy, PublicPortPolicy
from .secure_tree import SecureTree, SecureTreeError


class RegenerateError(ValueError):
    """A reviewed generator did not reproduce its exact declared outputs."""


GeneratorRunner = Callable[[GeneratedRulePolicy, Path], None]


def _tree_digest(inventory: Mapping[str, Mapping[str, str]]) -> str:
    entries = [inventory[path] for path in sorted(inventory)]
    return sha256_bytes(canonical_json(entries))


def _inventory_record(entry: Mapping[str, str]) -> dict[str, str]:
    return {
        "path": entry["path"],
        "mode": entry["mode"],
        "sha256": entry["sha256"],
    }


def _workspace_inventory(root: Path) -> dict[str, dict[str, str]]:
    try:
        return inventory_tree(root, forbid_git=True)
    except MaterializeError as error:
        if "forbidden root entry exists: .git" in str(error):
            raise RegenerateError("generator created or changed unlisted output: .git") from error
        raise RegenerateError(str(error)) from error


def regenerate(
    manifest: Mapping[str, Any],
    *,
    policy: PublicPortPolicy,
    source_root: Path,
    public_clone_root: Path,
    generator_runner: GeneratorRunner | None = None,
) -> dict[str, Any]:
    """Regenerate in a Git-free workspace using an explicitly trusted execution capability.

    There is intentionally no ambient subprocess fallback. A production caller must provide a
    reviewed sandbox runner with its own executable pinning, minimal environment, network policy,
    timeout and process-tree cleanup. An untrusted manifest can never acquire execution authority.
    """

    try:
        validate_manifest(manifest)
    except ManifestError as error:
        raise RegenerateError(str(error)) from error
    try:
        source, root = validate_repository_pair(source_root, public_clone_root)
        source_blobs = tracked_blobs(source, "HEAD")
        baseline_blobs = tracked_blobs(root, manifest["baseline"]["commit"])
        validate_manifest_policy_authority(
            manifest,
            policy,
            source_paths=source_blobs,
            baseline_paths=baseline_blobs,
        )
    except (ManifestError, MaterializeError) as error:
        raise RegenerateError(str(error)) from error
    policy_rules = list(policy.generated_rules)
    if policy_rules and generator_runner is None:
        raise RegenerateError("a trusted generator runner is required for reviewed generated rules")

    expected_pre_tree = expected_inventory(
        list(manifest["retained_baseline"]) + list(manifest["ported_authored"])
    )
    if inventory_tree(root) != expected_pre_tree:
        raise RegenerateError("generator input tree is not the closed materialized tree")

    try:
        with SecureTree(root) as source_tree:
            pre_bodies = {
                path: (source_tree.read_file(path), entry["mode"])
                for path, entry in expected_pre_tree.items()
            }
    except SecureTreeError as error:
        raise RegenerateError(str(error)) from error

    workspace_container = Path(
        tempfile.mkdtemp(prefix=".public-port-generator-", dir=root.parent)
    )
    workspace = workspace_container / "workspace"
    workspace.mkdir(mode=0o700)
    completed: list[str] = []
    try:
        with SecureTree(workspace) as workspace_tree:
            for relative_path, (body, mode) in sorted(pre_bodies.items()):
                workspace_tree.write_file(relative_path, body, mode)
        if _workspace_inventory(workspace) != expected_pre_tree:
            raise RegenerateError("isolated generator input tree drifted")

        for manifest_rule, policy_rule in zip(manifest["generated_rules"], policy_rules):
            before = _workspace_inventory(workspace)
            for entry in manifest_rule["inputs"]:
                observed = before.get(entry["path"])
                if observed != _inventory_record(entry):
                    raise RegenerateError(f"generated input hash mismatch: {entry['path']}")

            assert generator_runner is not None
            generator_runner(policy_rule, workspace)

            after = _workspace_inventory(workspace)
            allowed_outputs = {entry["path"] for entry in manifest_rule["outputs"]}
            changed = {
                path for path in set(before) | set(after) if before.get(path) != after.get(path)
            }
            unlisted = sorted(changed - allowed_outputs)
            if unlisted:
                raise RegenerateError(
                    f"generator created or changed unlisted output: {unlisted[0]}"
                )
            for entry in manifest_rule["outputs"]:
                observed = after.get(entry["path"])
                if observed is None:
                    raise RegenerateError(f"missing generated output: {entry['path']}")
                if observed != _inventory_record(entry):
                    raise RegenerateError(f"generated output hash mismatch: {entry['path']}")
                try:
                    with SecureTree(workspace) as workspace_tree:
                        validate_public_blob(entry["path"], workspace_tree.read_file(entry["path"]))
                except (PublicContentError, SecureTreeError) as error:
                    raise RegenerateError(str(error)) from error
            completed.append(policy_rule.rule_id)

        final_inventory = _workspace_inventory(workspace)
        expected_final = expected_inventory(manifest["expected_tree"])
        if final_inventory != expected_final:
            unexpected = sorted(set(final_inventory) - set(expected_final))
            missing = sorted(set(expected_final) - set(final_inventory))
            detail = unexpected[0] if unexpected else missing[0] if missing else "content drift"
            raise RegenerateError(f"final generated tree is not closed: {detail}")
        final_digest = _tree_digest(final_inventory)
        if final_digest != manifest["expected_tree_sha256"]:
            raise RegenerateError("final tree digest mismatch")

        try:
            with SecureTree(workspace) as workspace_tree:
                final_bodies = {
                    path: (workspace_tree.read_file(path), entry["mode"])
                    for path, entry in expected_final.items()
                }
        except SecureTreeError as error:
            raise RegenerateError(str(error)) from error

        promote_reviewed_tree(
            root,
            baseline_commit=manifest["baseline"]["commit"],
            baseline_branch=manifest["baseline"]["default_branch"],
            files=final_bodies,
            expected=expected_final,
            stage_prefix=".public-port-regenerate-",
            stage_error_message="generated promotion stage is not closed",
            promoted_error_message="promoted generated tree drifted",
        )
    except (MaterializeError, SecureTreeError, OSError) as error:
        raise RegenerateError(str(error)) from error
    finally:
        if workspace_container.exists():
            shutil.rmtree(workspace_container)

    return {"generated_rule_ids": completed, "final_tree_sha256": final_digest}
