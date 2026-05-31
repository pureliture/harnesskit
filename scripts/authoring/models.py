from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


VALID_KINDS = {"skill", "agent", "profile"}
VALID_STATUSES = {"added", "in-progress", "completed"}


class AuthoringState:
    def __init__(
        self,
        work_id: str,
        kind: str,
        status: str = "added",
        targets: list[str] | None = None,
        source_idea: str | None = None,
        public_projection: bool = False,
    ) -> None:
        self.work_id = work_id
        self.kind = kind
        self.status = status
        self.targets = targets or []
        self.source_idea = source_idea or ""
        self.public_projection = public_projection

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.work_id,
            "kind": self.kind,
            "status": self.status,
            "targets": self.targets,
            "source_idea": self.source_idea,
            "public_projection": self.public_projection,
        }

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)
        except Exception as exc:
            print(f"error: failed to save state: {exc}", file=sys.stderr)
            sys.exit(1)

    @classmethod
    def load(cls, path: Path) -> AuthoringState:
        if not path.is_file():
            print(f"error: state file not found: {path}", file=sys.stderr)
            sys.exit(1)
        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as exc:
            print(f"error: invalid state YAML format: {exc}", file=sys.stderr)
            sys.exit(1)

        if not isinstance(data, dict):
            print(f"error: state YAML must be a mapping: {path}", file=sys.stderr)
            sys.exit(1)

        work_id = data.get("id")
        kind = data.get("kind")
        status = data.get("status", "added")
        targets = data.get("targets")
        source_idea = data.get("source_idea")
        public_projection = data.get("public_projection", False)

        # Strict schema validation
        if not work_id or not isinstance(work_id, str):
            print(f"error: missing or invalid field 'id': {path}", file=sys.stderr)
            sys.exit(1)
        if not kind or kind not in VALID_KINDS:
            print(f"error: missing or invalid field 'kind' (must be skill, agent, or profile): {path}", file=sys.stderr)
            sys.exit(1)
        if status not in VALID_STATUSES:
            print(f"error: invalid status '{status}': {path}", file=sys.stderr)
            sys.exit(1)
        if targets is not None and not isinstance(targets, list):
            print(f"error: 'targets' must be a list: {path}", file=sys.stderr)
            sys.exit(1)
        if source_idea is not None and not isinstance(source_idea, str):
            print(f"error: 'source_idea' must be a string: {path}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(public_projection, bool):
            print(f"error: 'public_projection' must be a boolean: {path}", file=sys.stderr)
            sys.exit(1)

        targets_list = [str(t) for t in targets] if targets is not None else []

        return cls(
            work_id=work_id,
            kind=kind,
            status=status,
            targets=targets_list,
            source_idea=source_idea,
            public_projection=public_projection,
        )
