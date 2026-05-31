from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from scripts.authoring.engine import prepare_work_dir
from scripts.authoring.models import AuthoringState
from scripts.authoring.templates import (
    get_blueprint_template,
    get_decision_log_template,
    get_intent_template,
    get_requirements_template,
)


def run_add(
    base_dir: Path,
    kind: str,
    slug: str,
    idea_path: Path | None,
    targets: list[str],
) -> dict[str, Any]:
    work_id = f"{kind}-{slug}"

    # Prepare work directory safely with Overwrite Protection and Path Traversal Defense
    work_dir = prepare_work_dir(base_dir, work_id)

    idea_content = ""
    if idea_path is not None:
        if not idea_path.is_file():
            print(f"error: idea file not found: {idea_path}", file=sys.stderr)
            sys.exit(1)
        try:
            idea_content = idea_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"error: failed to read idea file: {exc}", file=sys.stderr)
            sys.exit(1)

    # Compile intent.md combining standard template and the provided idea
    intent_template = get_intent_template(work_id, kind)
    if idea_content:
        intent_template += f"\n## Idea Reference\n\n{idea_content}\n"

    try:
        (work_dir / "intent.md").write_text(intent_template, encoding="utf-8")
        (work_dir / "requirements.md").write_text(get_requirements_template(work_id, kind), encoding="utf-8")
        (work_dir / "blueprint.md").write_text(get_blueprint_template(work_id, kind), encoding="utf-8")
        (work_dir / "decision-log.md").write_text(get_decision_log_template(work_id), encoding="utf-8")
    except Exception as exc:
        print(f"error: failed to write template markdown files: {exc}", file=sys.stderr)
        sys.exit(1)

    # Save state.yml
    state = AuthoringState(
        work_id=work_id,
        kind=kind,
        status="added",
        targets=targets,
        source_idea=str(idea_path) if idea_path else "",
        public_projection=False,
    )
    state.save(work_dir / "state.yml")

    return state.to_dict()
