from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any


EXPECTED_INSTALL_CONTRACT_ID = "harnesskit.install-target-contract.v1"
EXPECTED_INSTALL_CONTRACT_SHA256 = (
    "de887f021dd5322f99016490be574c0f7ab08cb7c77e5e7ec5e78cbb54ded7a4"
)


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("install_entry_invalid_relative_path")
    return path


def _regular_file_without_symlink(path: Path) -> os.stat_result:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("install_entry_source_not_regular")
    return metadata


def _source_evidence(workspace: Path, plan: dict[str, Any]) -> None:
    for artifact in plan.get("artifacts", []):
        relative = _safe_relative(str(artifact.get("source", "")))
        if not relative.parts or relative.parts[0] != "dist":
            raise ValueError("install_entry_source_outside_dist")
        source = workspace.joinpath(*relative.parts)
        metadata = _regular_file_without_symlink(source)
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = _regular_file_without_symlink(source)
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("install_entry_source_changed")
        artifact["source_sha256"] = digest.hexdigest()
        artifact["mode"] = stat.S_IMODE(metadata.st_mode)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--script-id", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--mode", choices=["dry-run", "apply"])
    parser.add_argument("--profile")
    parser.add_argument("--scope", choices=["user", "project"])
    parser.add_argument("--target-id", action="append", default=[])
    parser.add_argument("--component-id", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.script_id not in {"install-plan", "artifact-projection"}:
            raise ValueError("install_entry_script_rejected")
        workspace = Path(args.workspace)
        workspace_metadata = workspace.lstat()
        if (
            not workspace.is_absolute()
            or stat.S_ISLNK(workspace_metadata.st_mode)
            or not stat.S_ISDIR(workspace_metadata.st_mode)
        ):
            raise ValueError("install_entry_workspace_rejected")
        components = set(args.component_id)
        if not components or len(components) != len(args.component_id):
            raise ValueError("install_entry_component_set_rejected")

        resource_root = Path(__file__).resolve().parent
        vendor = resource_root / "vendor"
        sys.path.insert(0, str(vendor))
        sys.path.insert(1, str(workspace))
        os.chdir(workspace)

        contract_path = workspace / "schemas/install-target-contract-v1.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        if (
            contract.get("contract_id") != EXPECTED_INSTALL_CONTRACT_ID
            or _canonical_json_sha256(contract) != EXPECTED_INSTALL_CONTRACT_SHA256
        ):
            raise ValueError("install_entry_contract_rejected")

        if args.script_id == "artifact-projection":
            if (
                args.mode is not None
                or args.profile is not None
                or args.scope is not None
                or args.target_id
            ):
                raise ValueError("artifact_projection_input_rejected")
            from scripts.install.plan import build_artifact_projection

            projection = build_artifact_projection(sorted(components))
            projection["install_contract_id"] = EXPECTED_INSTALL_CONTRACT_ID
            projection["install_contract_hash"] = EXPECTED_INSTALL_CONTRACT_SHA256
            print(
                json.dumps(
                    projection,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0

        if args.mode is None or args.profile is None or args.scope is None:
            raise ValueError("install_entry_install_input_rejected")
        targets = set(args.target_id)
        if not targets or len(targets) != len(args.target_id):
            raise ValueError("install_entry_target_set_rejected")

        from scripts.install.plan import build_plan

        plan = build_plan(args.profile, scope=args.scope, mode=args.mode)
        full_targets = list(plan.get("targets", []))
        if not targets.issubset(full_targets):
            raise ValueError("install_entry_target_set_mismatch")
        if set(plan.get("components", [])) != components:
            raise ValueError("install_entry_component_set_mismatch")
        plan["targets"] = [target for target in full_targets if target in targets]
        plan["artifacts"] = [
            artifact
            for artifact in plan.get("artifacts", [])
            if artifact.get("target") in targets
        ]
        plan["runtime_surfaces"] = [
            surface
            for surface in plan.get("runtime_surfaces", [])
            if surface.get("target") in targets
        ]
        plan["activation_gates"] = [
            gate
            for gate in plan.get("activation_gates", [])
            if gate.get("target") in targets
        ]
        plan["install_contract_id"] = EXPECTED_INSTALL_CONTRACT_ID
        plan["install_contract_hash"] = EXPECTED_INSTALL_CONTRACT_SHA256
        if args.mode == "apply":
            _source_evidence(workspace, plan)
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        print("install_entry_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
