from __future__ import annotations

import pytest
from pathlib import Path

from scripts.public_port.policy import (
    PolicyError,
    load_policy,
    parse_policy,
    reject_forbidden_authored_paths,
)
from tests.public_port.support import PUBLIC_REPOSITORY_URL, policy


def test_policy_accepts_the_canonical_public_target():
    assert policy().target_repository_url == PUBLIC_REPOSITORY_URL


def test_repository_public_port_policy_is_valid():
    root = Path(__file__).resolve().parents[2]
    loaded = load_policy(root / "publish/public-port.yml")

    assert loaded.target_repository_url == PUBLIC_REPOSITORY_URL
    assert loaded.generated_rules == ()
    assert "publish/public-sanitation-allowlist.yml" in loaded.ported_includes


@pytest.mark.parametrize(
    "repository_url",
    [
        "https://github.com/pureliture/" + "routine-harness.git",
        "git@github.com:pureliture/harnesskit.git",
        "file:///tmp/harnesskit.git",
    ],
)
def test_policy_rejects_any_noncanonical_target(repository_url: str):
    with pytest.raises(PolicyError, match="canonical public target"):
        parse_policy(
            {
                "schema_version": 1,
                "target_repository_url": repository_url,
                "retain_public_baseline": True,
                "ported_authored": {"include": ["src-tauri/**"], "exclude": []},
                "intentional_removals": [],
                "generated_rules": [],
            }
        )


@pytest.mark.parametrize(
    "include",
    [
        ["**/*"],
        ["docs/**"],
        ["docs/runtime-evidence/**"],
        ["docs/harness-requirements/**"],
        [".harnesskit/**"],
        ["../outside/**"],
        ["/absolute/**"],
    ],
)
def test_policy_rejects_broad_private_or_escaping_authored_patterns(include: list[str]):
    with pytest.raises(PolicyError):
        parse_policy(
            {
                "schema_version": 1,
                "target_repository_url": PUBLIC_REPOSITORY_URL,
                "retain_public_baseline": True,
                "ported_authored": {"include": include, "exclude": []},
                "intentional_removals": [],
                "generated_rules": [],
            }
        )


def test_selected_authored_paths_reject_forbidden_path_matched_by_glob_character_class():
    selected_path = "docs/runtime-evidence/secret-scan.json"
    parsed = parse_policy(
        {
            "schema_version": 1,
            "target_repository_url": PUBLIC_REPOSITORY_URL,
            "retain_public_baseline": True,
            "ported_authored": {"include": ["docs/[r]untime-evidence/*"], "exclude": []},
            "intentional_removals": [],
            "generated_rules": [],
        }
    )

    assert parsed.ported_includes == ("docs/[r]untime-evidence/*",)
    with pytest.raises(PolicyError, match="private or generated-only path"):
        reject_forbidden_authored_paths({selected_path})


def test_policy_rejects_unknown_private_provenance_fields():
    with pytest.raises(PolicyError, match="unknown keys"):
        parse_policy(
            {
                "schema_version": 1,
                "target_repository_url": PUBLIC_REPOSITORY_URL,
                "retain_public_baseline": True,
                "source_revision": "deadbeef",
                "ported_authored": {"include": ["src-tauri/**"], "exclude": []},
                "intentional_removals": [],
                "generated_rules": [],
            }
        )


def test_policy_rejects_overlapping_generated_outputs_and_removals():
    with pytest.raises(PolicyError, match="both generated and removed"):
        parse_policy(
            {
                "schema_version": 1,
                "target_repository_url": PUBLIC_REPOSITORY_URL,
                "retain_public_baseline": True,
                "ported_authored": {
                    "include": ["src-tauri/**"],
                    "exclude": ["src-tauri/icons/icon.png"],
                },
                "intentional_removals": ["src-tauri/icons/icon.png"],
                "generated_rules": [
                    {
                        "id": "icons",
                        "command": ["python3", "scripts/package/derive_brand_assets.py"],
                        "working_directory": ".",
                        "inputs": ["assets/branding/source.png"],
                        "outputs": ["src-tauri/icons/icon.png"],
                    }
                ],
            }
        )
