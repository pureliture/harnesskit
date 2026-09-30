from __future__ import annotations

import os

import pytest

from scripts.public_port.git_environment import (
    GitEnvironmentError,
    curated_git_environment,
)


def test_curated_git_environment_removes_ambient_repository_and_config_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poisoned = {
        "GIT_DIR": "/private/attacker.git",
        "GIT_WORK_TREE": "/private/attacker-tree",
        "GIT_OBJECT_DIRECTORY": "/private/objects",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/private/alternates",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "url.https://attacker.invalid/.insteadOf",
        "GIT_CONFIG_VALUE_0": "https://github.com/",
        "GIT_SSH_COMMAND": "attacker-helper",
        "GIT_ASKPASS": "attacker-helper",
        "GCM_INTERACTIVE": "Always",
    }
    for key, value in poisoned.items():
        monkeypatch.setenv(key, value)

    environment = curated_git_environment(
        {"GIT_AUTHOR_NAME": "HarnessKit Contributors"}
    )

    assert all(
        key not in environment for key in poisoned if key != "GCM_INTERACTIVE"
    )
    assert environment["GIT_AUTHOR_NAME"] == "HarnessKit Contributors"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GCM_INTERACTIVE"] == "Never"


def test_curated_git_environment_rejects_unreviewed_git_override() -> None:
    with pytest.raises(GitEnvironmentError, match="override"):
        curated_git_environment({"GIT_OBJECT_DIRECTORY": "/private/objects"})
