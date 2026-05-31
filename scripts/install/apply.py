from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.install.common import destination_path, load_plan, source_path

RUNTIME_HOOK_DESTINATIONS = {
    ".claude/settings.json",
    ".codex/hooks.json",
    ".agents/hooks.json",
}
HARNESSKIT_MANAGED_BLOCK = (
    "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->",
    "<!-- END HARNESSKIT GENERATED CONTEXT -->",
)
LEGACY_ROUTINE_HARNESS_MANAGED_BLOCK = (
    "<!-- BEGIN ROUTINE-HARNESS GENERATED CONTEXT -->",
    "<!-- END ROUTINE-HARNESS GENERATED CONTEXT -->",
)


def apply_plan(
    plan_path: Path,
    *,
    target_root: Path,
    overwrite: bool = False,
    allow_runtime_hooks: bool = False,
) -> int:
    plan = load_plan(plan_path)
    if plan.get("mode") != "apply":
        raise ValueError("install plan mode must be apply")
    blocked_gates = [
        gate["id"]
        for gate in plan["activation_gates"]
        if gate.get("required_before_apply") is True
    ]
    if blocked_gates:
        raise ValueError(
            "activation gates require approval before apply: "
            + ", ".join(blocked_gates)
        )
    if _requires_runtime_hook_approval(plan) and not allow_runtime_hooks:
        raise ValueError(
            "runtime hook surfaces require --allow-runtime-hooks after review"
        )

    copy_pairs: list[tuple[Path, Path]] = []
    merge_pairs: list[tuple[Path, Path, dict]] = []
    for artifact in plan["artifacts"]:
        src = source_path(artifact)
        dest = destination_path(target_root, artifact)
        if artifact.get("merge_strategy") == "managed-block":
            merge_pairs.append((src, dest, artifact))
            continue
        if dest.exists() and not overwrite and not _same_file_content(src, dest):
            # Check for exact Routine-Harness generated marker for safe auto-update
            existing_content = dest.read_text(encoding="utf-8")
            if "<!-- BEGIN ROUTINE-HARNESS GENERATED -->" not in existing_content:
                raise ValueError(
                    "destination already exists with different content; "
                    f"rerun with --overwrite after review: {dest.relative_to(target_root)}"
                )
        copy_pairs.append((src, dest))

    count = 0
    for src, dest in copy_pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        count += 1
    for src, dest, artifact in merge_pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = src.read_text(encoding="utf-8").rstrip()
        current = dest.read_text(encoding="utf-8") if dest.exists() else ""
        dest.write_text(_merge_managed_block(current, body, artifact), encoding="utf-8")
        count += 1
    print(f"applied {count} artifacts to {target_root}")
    return 0


def _same_file_content(left: Path, right: Path) -> bool:
    if not right.is_file():
        return False
    return left.read_bytes() == right.read_bytes()


def _merge_managed_block(current: str, body: str, artifact: dict) -> str:
    begin = artifact["begin_marker"]
    end = artifact["end_marker"]
    if begin == end:
        raise ValueError("managed block begin_marker and end_marker must differ")
    block = f"{begin}\n{body}\n{end}"
    marker_pair = _find_existing_managed_block_markers(current, begin, end)
    if marker_pair is None:
        return f"{current.rstrip()}\n\n{block}\n"

    existing_begin, existing_end = marker_pair
    begin_index = current.index(existing_begin)
    end_index = current.index(existing_end)
    if end_index <= begin_index:
        raise ValueError("managed block end_marker must appear after begin_marker")
    else:
        before = current[:begin_index]
        after = current[end_index + len(existing_end):]
        return f"{before}{block}{after}"


def _find_existing_managed_block_markers(
    current: str,
    begin: str,
    end: str,
) -> tuple[str, str] | None:
    marker_pairs = [(begin, end)]
    if (begin, end) == HARNESSKIT_MANAGED_BLOCK:
        marker_pairs.append(LEGACY_ROUTINE_HARNESS_MANAGED_BLOCK)

    found: list[tuple[str, str]] = []
    for candidate_begin, candidate_end in marker_pairs:
        begin_count = current.count(candidate_begin)
        end_count = current.count(candidate_end)
        if begin_count == 0 and end_count == 0:
            continue
        if begin_count != 1 or end_count != 1:
            raise ValueError(
                "managed block markers must be absent or appear exactly once"
            )
        found.append((candidate_begin, candidate_end))

    if len(found) > 1:
        raise ValueError("managed block markers must be absent or appear exactly once")
    if not found:
        return None
    return found[0]


def _requires_runtime_hook_approval(plan: dict) -> bool:
    gated_targets = {
        gate["target"]
        for gate in plan["activation_gates"]
        if gate.get("required_before_runtime") is True
    }
    return any(
        artifact.get("target") in gated_targets
        and artifact.get("destination") in RUNTIME_HOOK_DESTINATIONS
        for artifact in plan["artifacts"]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a HarnessKit install plan.")
    parser.add_argument("plan", help="Path to install plan JSON/YAML")
    parser.add_argument("--target-root", required=True, help="Target project root")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing destination files after reviewing the install plan.",
    )
    parser.add_argument(
        "--allow-runtime-hooks",
        action="store_true",
        help="Materialize hook runtime surfaces after reviewing activation gates.",
    )
    args = parser.parse_args(argv)

    try:
        return apply_plan(
            Path(args.plan),
            target_root=Path(args.target_root),
            overwrite=args.overwrite,
            allow_runtime_hooks=args.allow_runtime_hooks,
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
