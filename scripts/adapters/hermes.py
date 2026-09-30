from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import yaml

HERMES_PROHIBITED_DESTINATION_STRINGS = (
    ".codex/",
    ".claude/",
    ".gemini/",
    ".agents/hooks.json",
    ".codex/hooks.json",
    ".codex/config.toml",
)
HERMES_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class HermesSkillProjection:
    skill_name: str
    content: str


@dataclass(frozen=True)
class PreparedHermesSkill:
    skill_name: str
    frontmatter_yaml: str
    ownership_notice: str
    body: str


def skill_slug(component_id: str) -> str:
    raw = component_id.split(".")[-1].strip().lower()
    return re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")


def prepare_skill(
    *,
    component_id: str,
    manifest: dict[str, Any],
    raw_body: str,
    adapter_version: str,
    policy: dict[str, Any],
) -> PreparedHermesSkill:
    body = _sanitize_body(raw_body)
    overrides = _allowlist(policy).get(component_id) or {}
    skill_name = str(overrides.get("name") or skill_slug(component_id)).strip()
    if not HERMES_SKILL_NAME_RE.fullmatch(skill_name):
        raise ValueError(
            f"Invalid derived Hermes skill name for {component_id}: {skill_name!r}"
        )

    skill_config = {
        "description": _description(
            component_id=component_id,
            manifest=manifest,
            skill_name=skill_name,
            skill_config=overrides,
        ),
        "related_skills": overrides.get("related_skills") or [],
    }
    frontmatter = _frontmatter(
        component_id=component_id,
        skill_name=skill_name,
        skill_config=skill_config,
        adapter_version=adapter_version,
        policy=policy,
    )
    return PreparedHermesSkill(
        skill_name=skill_name,
        frontmatter_yaml=yaml.safe_dump(
            frontmatter,
            sort_keys=False,
            allow_unicode=True,
        ),
        ownership_notice=str(policy["ownership_notice"]),
        body=body,
    )


def render_skill(
    *,
    prepared: PreparedHermesSkill,
    template: str,
) -> HermesSkillProjection:
    context = {
        "frontmatter_yaml": prepared.frontmatter_yaml,
        "ownership_notice": prepared.ownership_notice,
        "body": prepared.body,
    }
    content = re.sub(
        r"{{\s*([a-zA-Z0-9_.-]+)\s*}}",
        lambda match: context.get(match.group(1), ""),
        template,
    )
    return HermesSkillProjection(
        skill_name=prepared.skill_name,
        content=content.rstrip() + "\n",
    )


def render_hook_manifest(*, event: str, command: str, timeout: int) -> str:
    return yaml.safe_dump(
        {
            "hooks": {
                event: [
                    {
                        "command": command,
                        "timeout": timeout,
                    }
                ]
            }
        },
        sort_keys=False,
        allow_unicode=True,
    )


def optimal_response_wrapper_content() -> str:
    return """#!/usr/bin/env node
const path = require('path');
process.env.OPTIMAL_RESPONSE_SURFACE = 'hermes';

require(path.join(__dirname, 'stop-prompt-submit.cjs'));
"""


def render_external_package_manifest(
    *,
    adapter: dict[str, Any],
    package: dict[str, Any],
    component_ids: list[str],
) -> str:
    return (
        json.dumps(
            {
                "format": package["marker_format"],
                "version": package["marker_version"],
                "ownership": "harnesskit-managed-external-package",
                "adapter_id": adapter["adapter_id"],
                "adapter_version": adapter["version"],
                "catalog": component_ids,
            },
            indent=2,
        )
        + "\n"
    )


def _allowlist(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    allowlist = policy.get("mvp_allowlist")
    if not isinstance(allowlist, dict):
        raise ValueError("adapters/hermes/adapter.yml: hermes.mvp_allowlist must be a mapping")
    normalized: dict[str, dict[str, Any]] = {}
    for component_id, item in allowlist.items():
        if not isinstance(component_id, str) or not isinstance(item, dict):
            raise ValueError("adapters/hermes/adapter.yml: allowlist entries must be mappings")
        normalized[component_id] = item
    return normalized


def _description(
    *,
    component_id: str,
    manifest: dict[str, Any],
    skill_name: str,
    skill_config: dict[str, Any],
) -> str:
    override = str(skill_config.get("description") or "").strip()
    if override:
        return override
    title = " ".join(str(manifest.get("title") or skill_name).split())
    candidates = [
        f"Use the {title} HarnessKit skill.",
        f"Use the {skill_name} HarnessKit skill.",
        "Use this HarnessKit skill.",
    ]
    for candidate in candidates:
        if len(candidate) <= 60 and "\n" not in candidate:
            return candidate
    raise ValueError(f"Unable to derive Hermes description for {component_id}")


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    lines = text.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1 :]).lstrip("\n")
    return text


def _sanitize_body(body: str) -> str:
    body = _strip_frontmatter(body).strip()
    replacements = {
        "policy/worktree-lifecycle-policy.md": "references/worktree-lifecycle-policy.md",
        "schemas/worktree-ledger.schema.json": "references/worktree-ledger.schema.json",
        "`policy/worktree-lifecycle-policy.md`": "`references/worktree-lifecycle-policy.md`",
        "`schemas/worktree-ledger.schema.json`": "`references/worktree-ledger.schema.json`",
        "`.claude/specs/<timestamp>-<topic>/`": "`target-specific preview workspace`",
        "Claude adapter는 `.claude/specs/<timestamp>-<topic>/`\n같은 preview workspace를 사용할 수 있고, Codex adapter는 대응되는 runtime-safe preview\nroot를 사용할 수 있다.": "Target adapters may choose a runtime-safe preview workspace for temporary specs.",
        "Claude adapter는 `.claude/specs/<timestamp>-<topic>/`\r\n같은 preview workspace를 사용할 수 있고, Codex adapter는 대응되는 runtime-safe preview\r\nroot를 사용할 수 있다.": "Target adapters may choose a runtime-safe preview workspace for temporary specs.",
    }
    for old, new in replacements.items():
        body = body.replace(old, new)
    for prohibited in HERMES_PROHIBITED_DESTINATION_STRINGS:
        body = body.replace(prohibited, "target-specific/")
    return body


def _frontmatter(
    *,
    component_id: str,
    skill_name: str,
    skill_config: dict[str, Any],
    adapter_version: str,
    policy: dict[str, Any],
) -> dict[str, Any]:
    description = str(skill_config.get("description") or "").strip()
    related_skills = skill_config.get("related_skills") or []
    if not isinstance(related_skills, list) or not all(
        isinstance(item, str) for item in related_skills
    ):
        raise ValueError(f"Hermes related_skills must be a string list: {component_id}")
    tags = policy.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
        raise ValueError("Hermes tags must be a string list")
    return {
        "name": skill_name,
        "description": description,
        "version": str(adapter_version),
        "author": str(policy.get("author") or "harnesskit"),
        "license": str(policy.get("license") or "MIT"),
        "metadata": {
            "source_component_id": component_id,
            "generated_from": str(policy.get("generated_from") or "harnesskit"),
            "hermes": {
                "tags": tags,
                "related_skills": related_skills,
            },
        },
    }
