from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

from scripts.install.plan import build_plan


ROOT = Path(__file__).resolve().parents[2]


def test_codex_migration_manifest_is_schema_valid_and_profile_scoped():
    manifest = yaml.safe_load(
        (ROOT / "migrations/codex.yml").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (ROOT / "schemas/codex-migration.schema.json").read_text(encoding="utf-8")
    )

    jsonschema.validate(instance=manifest, schema=schema)
    assert manifest["retired_codex_agent_registrations"] == {
        "harnesskit.profile.engineering": {
            "user": [
                {
                    "name": "system_architecture_manager",
                    "config_file": "agents/system_architecture_manager.toml",
                }
            ]
        }
    }


def test_install_plan_reads_codex_migration_without_polluting_profile():
    project_plan = build_plan("engineering", scope="project", mode="dry-run")
    user_plan = build_plan("engineering", scope="user", mode="dry-run")
    development_plan = build_plan("development", scope="user", mode="dry-run")

    assert "retired_codex_agent_registrations" not in project_plan
    assert user_plan["retired_codex_agent_registrations"] == [
        {
            "name": "system_architecture_manager",
            "config_file": "agents/system_architecture_manager.toml",
        }
    ]
    assert "retired_codex_agent_registrations" not in development_plan
