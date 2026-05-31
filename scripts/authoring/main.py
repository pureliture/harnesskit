from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.authoring.commands.add import run_add
from scripts.authoring.commands.preview import run_preview


VALID_TARGETS = {"claude", "codex"}
VALID_TARGETS_DISPLAY = "codex, claude"


def _parse_targets(raw_targets: str) -> list[str]:
    targets = [target.strip() for target in raw_targets.split(",") if target.strip()]
    if not targets:
        raise ValueError(
            f"--targets must include at least one target: {VALID_TARGETS_DISPLAY}"
        )

    invalid_targets = [target for target in targets if target not in VALID_TARGETS]
    if invalid_targets:
        invalid_display = ", ".join(invalid_targets)
        raise ValueError(
            f"invalid target for --targets: {invalid_display}; allowed: {VALID_TARGETS_DISPLAY}"
        )

    return targets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="harnesskit harness local private authoring CLI."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. 'add' subcommand
    add_parser = subparsers.add_parser("add", help="Add a new private work item")
    add_parser.add_argument(
        "kind",
        choices=["skill", "agent", "profile"],
        help="The kind of the work item",
    )
    add_parser.add_argument(
        "--slug",
        required=True,
        help="Slug for the work item (e.g. release-summary)",
    )
    add_parser.add_argument(
        "--idea",
        type=Path,
        help="Path to an idea file",
    )
    add_parser.add_argument(
        "--targets",
        default="codex,claude",
        help="Comma-separated target list (default: codex,claude)",
    )
    add_parser.add_argument(
        "--target-root",
        type=Path,
        default=Path("."),
        help="Target root folder (defaults to current directory)",
    )
    add_parser.add_argument(
        "--json",
        action="store_true",
        help="Format output as JSON for automation",
    )

    # 2. 'preview' subcommand
    preview_parser = subparsers.add_parser(
        "preview", help="Preview a private work item state"
    )
    preview_parser.add_argument(
        "work_id",
        help="ID of the work item (e.g. skill-release-summary)",
    )
    preview_parser.add_argument(
        "--target-root",
        type=Path,
        default=Path("."),
        help="Target root folder (defaults to current directory)",
    )
    preview_parser.add_argument(
        "--json",
        action="store_true",
        help="Format output as JSON for automation",
    )

    args = parser.parse_args()

    target_root = args.target_root.resolve()
    base_dir = target_root / ".harnesskit" / "work"

    try:
        if args.command == "add":
            targets_list = _parse_targets(args.targets)
            result = run_add(
                base_dir=base_dir,
                kind=args.kind,
                slug=args.slug,
                idea_path=args.idea,
                targets=targets_list,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
            else:
                print(
                    f"Successfully added private work item '{result['id']}' under {base_dir / result['id']}/"
                )

        elif args.command == "preview":
            result = run_preview(base_dir=base_dir, work_id=args.work_id)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
            else:
                print(f"Previewing work item: {result['id']}")
                print(f"  Status:            {result['status']}")
                print(f"  Public Projection: {result['public_projection']}")
                print("  Files:")
                for f in result["files"]:
                    print(f"    - {f}")

    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
