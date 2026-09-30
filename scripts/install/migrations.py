from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


MIGRATION_MANIFEST_RELATIVE_PATH = Path("migrations/codex.yml")
PROFILE_ID_RE = re.compile(r"^harnesskit\.profile\.[a-z][a-z0-9-]+$")
SCOPES = {"project", "user"}


def retired_codex_agent_registrations_for_profile(
    repo_root: Path,
    profile_id: str,
    scope: str,
) -> list[dict[str, str]]:
    manifest = load_codex_migration_manifest(repo_root)
    by_profile = manifest["retired_codex_agent_registrations"].get(profile_id, {})
    return list(by_profile.get(scope, []))


def load_codex_migration_manifest(repo_root: Path) -> dict[str, Any]:
    path = repo_root / MIGRATION_MANIFEST_RELATIVE_PATH
    if not path.is_file():
        return {"schema_version": 1, "retired_codex_agent_registrations": {}}

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Codex migration manifest must be a mapping: {path}")
    if set(data) != {"schema_version", "retired_codex_agent_registrations"}:
        raise ValueError(f"Codex migration manifest has unsupported keys: {path}")
    if data.get("schema_version") != 1:
        raise ValueError(f"Unsupported Codex migration schema version: {path}")

    raw_profiles = data["retired_codex_agent_registrations"]
    if not isinstance(raw_profiles, dict):
        raise ValueError(
            "Codex migration retired_codex_agent_registrations must be a mapping"
        )

    normalized_profiles: dict[str, dict[str, list[dict[str, str]]]] = {}
    for profile_id, raw_scopes in raw_profiles.items():
        if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
            raise ValueError(f"Invalid Codex migration profile id: {profile_id}")
        if not isinstance(raw_scopes, dict) or not set(raw_scopes).issubset(SCOPES):
            raise ValueError(f"Invalid Codex migration scopes for {profile_id}")

        normalized_scopes: dict[str, list[dict[str, str]]] = {}
        for scope, raw_registrations in raw_scopes.items():
            if not isinstance(raw_registrations, list):
                raise ValueError(
                    f"Codex migration registrations for {profile_id}/{scope} must be a list"
                )
            registrations: list[dict[str, str]] = []
            for registration in raw_registrations:
                if not isinstance(registration, dict) or set(registration) != {
                    "name",
                    "config_file",
                }:
                    raise ValueError(
                        "Codex migration registration must contain only name and config_file"
                    )
                name = registration.get("name")
                config_file = registration.get("config_file")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Codex migration registration name must be non-empty")
                if not isinstance(config_file, str) or not config_file.strip():
                    raise ValueError(
                        "Codex migration registration config_file must be non-empty"
                    )
                registrations.append({"name": name, "config_file": config_file})
            normalized_scopes[scope] = registrations
        normalized_profiles[profile_id] = normalized_scopes

    return {
        "schema_version": 1,
        "retired_codex_agent_registrations": normalized_profiles,
    }
