from __future__ import annotations

import os
from typing import Mapping


class GitEnvironmentError(ValueError):
    """A Git subprocess requested an ambient or unreviewed control variable."""


_ALLOWED_OVERRIDES = {
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_COMMITTER_DATE",
    "GIT_INDEX_FILE",
}


def curated_git_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key != "GCM_INTERACTIVE"
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GCM_INTERACTIVE": "Never",
            "LC_ALL": "C",
        }
    )
    if overrides:
        unexpected = set(overrides) - _ALLOWED_OVERRIDES
        if unexpected:
            raise GitEnvironmentError("Git environment override is not reviewed")
        environment.update(overrides)
    return environment
