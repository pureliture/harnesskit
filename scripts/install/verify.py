from __future__ import annotations

import argparse
import filecmp
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.install.common import destination_path, load_plan, source_path
from scripts.install.apply import (
    merge_json_deep,
    merge_managed_block,
    merge_toml_agents,
)


def verify_plan(plan_path: Path, *, target_root: Path) -> int:
    plan = load_plan(plan_path)
    if plan.get("mode") not in {"apply", "verify"}:
        raise ValueError("install plan mode must be apply or verify")
    issues: list[str] = []

    for artifact in plan["artifacts"]:
        try:
            src = source_path(artifact)
            dest = destination_path(target_root, artifact)
        except (FileNotFoundError, ValueError) as exc:
            issues.append(str(exc))
            continue

        if not dest.is_file():
            issues.append(f"missing destination: {dest.relative_to(target_root)}")
            continue
        if artifact.get("merge_strategy") == "managed-block":
            body = src.read_text(encoding="utf-8").rstrip()
            current = dest.read_text(encoding="utf-8")
            if merge_managed_block(current, body, artifact) != current:
                issues.append(f"managed block mismatch: {dest.relative_to(target_root)}")
            continue
        if artifact.get("merge_strategy") == "json-deep-merge":
            body = src.read_text(encoding="utf-8")
            current = dest.read_text(encoding="utf-8")
            if merge_json_deep(current, body, artifact) != current:
                issues.append(f"json merge mismatch: {dest.relative_to(target_root)}")
            continue
        if artifact.get("merge_strategy") == "toml-agents-merge":
            body = src.read_text(encoding="utf-8")
            current = dest.read_text(encoding="utf-8")
            if merge_toml_agents(current, body, artifact) != current:
                issues.append(f"toml merge mismatch: {dest.relative_to(target_root)}")
            continue
        if not filecmp.cmp(src, dest, shallow=False):
            existing_content = dest.read_text(encoding="utf-8")
            if "<!-- BEGIN ROUTINE-HARNESS GENERATED -->" in existing_content:
                issues.append(f"updatable (needs apply): {dest.relative_to(target_root)}")
            else:
                issues.append(f"mismatch: {dest.relative_to(target_root)}")

    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1

    print(f"verified {len(plan['artifacts'])} artifacts in {target_root}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a HarnessKit install plan.")
    parser.add_argument("plan", help="Path to install plan JSON/YAML")
    parser.add_argument("--target-root", required=True, help="Target project root")
    args = parser.parse_args(argv)

    try:
        return verify_plan(Path(args.plan), target_root=Path(args.target_root))
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
