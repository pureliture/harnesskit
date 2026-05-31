from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from scripts.authoring.engine import validate_and_resolve_work_dir
from scripts.authoring.models import AuthoringState


def run_preview(base_dir: Path, work_id: str) -> dict[str, Any]:
    # Resolve and validate work dir safely (Traversal check)
    work_dir = validate_and_resolve_work_dir(base_dir, work_id)
    state_file = work_dir / "state.yml"

    if not state_file.is_file():
        print(f"error: state.yml not found at {state_file}", file=sys.stderr)
        sys.exit(1)

    # Load state.yml with full schema validation and type checking
    state = AuthoringState.load(state_file)

    # Gather all file paths relative to base_dir
    files: list[str] = []
    if work_dir.exists():
        for path in work_dir.rglob("*"):
            if path.is_file():
                try:
                    rel_path = path.relative_to(base_dir.parent.parent).as_posix()
                except ValueError:
                    rel_path = path.as_posix()
                files.append(rel_path)

    return {
        "id": state.work_id,
        "status": state.status,
        "files": sorted(files),
        "public_projection": state.public_projection,
    }
