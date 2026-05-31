from __future__ import annotations

import re
import sys
from pathlib import Path


WORK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def is_safe_work_id(work_id: str) -> bool:
    if not work_id:
        return False
    return bool(WORK_ID_PATTERN.match(work_id))


def validate_and_resolve_work_dir(base_dir: Path, work_id: str) -> Path:
    if not is_safe_work_id(work_id):
        print(
            f"error: invalid work-id or slug '{work_id}'. Only alphanumeric characters, dashes, and underscores are allowed.",
            file=sys.stderr,
        )
        sys.exit(1)

    base_resolved = base_dir.resolve()
    target_path = base_resolved / work_id
    resolved_path = target_path.resolve()

    try:
        # Check if resolved path is strictly inside base directory to prevent Path Traversal
        resolved_path.relative_to(base_resolved)
    except ValueError:
        print(
            f"error: path traversal detected! '{work_id}' escapes the base work directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    return resolved_path


def prepare_work_dir(base_dir: Path, work_id: str) -> Path:
    work_dir = validate_and_resolve_work_dir(base_dir, work_id)

    # Overwrite Protection: Check if directory exists and contains state.yml or other files
    if work_dir.exists() and any(work_dir.iterdir()):
        # If it contains files, refuse to overwrite to protect developer data
        print(
            f"error: work item '{work_id}' already exists at {work_dir} and is not empty. Overwrite rejected.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Create the directory structure safely
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "local-component").mkdir(parents=True, exist_ok=True)
    (work_dir / "local-adapter-output").mkdir(parents=True, exist_ok=True)

    return work_dir
