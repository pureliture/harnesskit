from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.adapters.probe_codex_runtime import (  # noqa: E402
    _subagent_marker_evidence as _codex_component_author_subagent_evidence,
)


PROFILE = "harness-maintenance"
EVIDENCE_ROOT = REPO_ROOT / "docs" / "runtime-evidence" / "harness-creation"
MODES = ["loading-dispatch", "functional-e2e"]
FUNCTIONAL_E2E_REQUIRED_CAPS = [
    "selected target model",
    "max_effort<=medium",
    "max_budget_usd",
    "timeout_seconds<=300",
]
E2E_TOY_COMPONENT_ID = "harnesskit.skill.e2e-toy-checklist"
FUNCTIONAL_CODEX_BUDGET_GUARD = "luna-policy + max-medium-effort + live-call-ceiling"
FUNCTIONAL_CODEX_MODEL_POLICY = "luna"
FUNCTIONAL_CODEX_MAX_LIVE_CALLS = 5
FUNCTIONAL_CLAUDE_MAX_LIVE_CALLS = 6
FUNCTIONAL_E2E_MAX_BUDGET_USD = 0.25
FUNCTIONAL_E2E_MAX_TIMEOUT_SECONDS = 300
E2E_COMPONENT_AUTHOR_SUBAGENT_MARKER = "E2E_TOY_COMPONENT_AUTHOR_SUBAGENT: ok"
E2E_TOY_REQUIRED_FILES = {
    "docs/e2e-toy/requirements.md": [
        "# E2E Toy Requirements",
        "approved-for-blueprint",
        "E2E_TOY_REQUIREMENTS: ok",
    ],
    "docs/e2e-toy/blueprint.md": ["# E2E Toy Blueprint", "E2E_TOY_BLUEPRINT: ok"],
    "components/harness/skills/e2e-toy-checklist/component.yml": [
        "component_id: harnesskit.skill.e2e-toy-checklist",
        "kind: skill",
        "dist/codex/.agents/skills/e2e-toy-checklist/SKILL.md",
    ],
    "components/harness/skills/e2e-toy-checklist/SKILL.md": [
        "# E2E Toy Checklist",
        "E2E_TOY_SKILL_RUNTIME: ok",
    ],
    "components/harness/skills/e2e-toy-checklist/provenance.map.yml": [
        "component_id: harnesskit.skill.e2e-toy-checklist",
        "runtime_dependency_on_sources: false",
    ],
    "docs/e2e-toy/evaluation.md": ["# E2E Toy Evaluation", "E2E_TOY_EVALUATION: ok"],
}
E2E_TOY_GENERATED_SKILL = "dist/codex/.agents/skills/e2e-toy-checklist/SKILL.md"
E2E_TOY_LIVE_SKILL = ".agents/skills/e2e-toy-checklist/SKILL.md"
E2E_REQUIREMENTS_PROMPT = """Use the harness-requirements skill from this workspace. Create only docs/e2e-toy/requirements.md. Do not write blueprint, component, provenance, registry, dist, or evaluation files.

Write docs/e2e-toy/requirements.md exactly:
# E2E Toy Requirements

- component_id: harnesskit.skill.e2e-toy-checklist
- goal: prove staged Codex functional E2E harness creation with cheap model controls
- must_have:
  - deterministic toy checklist skill
  - Codex generated adapter output
  - runtime marker E2E_TOY_SKILL_RUNTIME: ok
- non_goals:
  - Claude functional E2E
  - production component authoring
- E2E_TOY_REQUIREMENTS: ok

## Approval Status
approved-for-blueprint

Final-answer exactly E2E_TOY_REQUIREMENTS_STAGE: ok."""
E2E_BLUEPRINT_PROMPT = """Use the harness-blueprint skill from this workspace. Read docs/e2e-toy/requirements.md and create only docs/e2e-toy/blueprint.md. Do not write component, provenance, registry, dist, or evaluation files.

Write docs/e2e-toy/blueprint.md exactly:
# E2E Toy Blueprint

- kind: skill
- component_id: harnesskit.skill.e2e-toy-checklist
- canonical_path: components/harness/skills/e2e-toy-checklist
- codex_output: dist/codex/.agents/skills/e2e-toy-checklist/SKILL.md
- E2E_TOY_BLUEPRINT: ok

Final-answer exactly E2E_TOY_BLUEPRINT_STAGE: ok."""
E2E_COMPONENT_AUTHOR_PROMPT = """Runtime probe only. Spawn or use a `component_author` or `component-author` agent. Instruct that subagent: Author only the canonical toy skill files under components/harness/skills/e2e-toy-checklist/ and add only the matching components/registry.yml entry. Do not write docs/e2e-toy/requirements.md, docs/e2e-toy/blueprint.md, docs/e2e-toy/evaluation.md, or dist files.

The component-author subagent must final-answer exactly:
E2E_TOY_COMPONENT_AUTHOR_SUBAGENT: ok

Ensure components/registry.yml contains this entry under components:
  harnesskit.skill.e2e-toy-checklist:
    kind: skill
    status: draft
    path: components/harness/skills/e2e-toy-checklist/component.yml

Write components/harness/skills/e2e-toy-checklist/component.yml exactly:
component_id: harnesskit.skill.e2e-toy-checklist
kind: skill
status: draft
title: E2E Toy Checklist
summary: Deterministic toy checklist for Codex functional E2E probe.
source_refs: []
owned_files:
  - components/harness/skills/e2e-toy-checklist/component.yml
  - components/harness/skills/e2e-toy-checklist/SKILL.md
  - components/harness/skills/e2e-toy-checklist/provenance.map.yml
targets:
  codex:
    output_path: dist/codex/.agents/skills/e2e-toy-checklist/SKILL.md
provenance_mode: manual
routine_harness:
  category: harness-maintenance
  phase: functional-e2e-toy
  runtime_probe_in_scope: true

Write components/harness/skills/e2e-toy-checklist/SKILL.md exactly:
# E2E Toy Checklist

Use this skill only for the Codex functional E2E runtime probe.

When asked for the runtime marker, final-answer exactly:
E2E_TOY_SKILL_RUNTIME: ok

Write components/harness/skills/e2e-toy-checklist/provenance.map.yml exactly:
component_id: harnesskit.skill.e2e-toy-checklist
provenance:
  mode: original
  runtime_dependency_on_sources: false
  sources: []
  attribution_note: Original deterministic toy component for Codex functional E2E probe.
  source_mode: none
  copied_sections: []
  rewritten_sections: []
  new_sections:
    - deterministic runtime marker
  license_notes: No third-party content.
  human_review_required: false
  review_triggers: []

Parent final-answer must be exactly E2E_TOY_COMPONENT_AUTHOR_STAGE: ok only after the subagent succeeded."""
E2E_EVALUATION_PROMPT = """Use the skill-evaluation skill from this workspace. Read docs/e2e-toy/requirements.md, docs/e2e-toy/blueprint.md, components/harness/skills/e2e-toy-checklist/component.yml, and the controller validate/build returncodes included below. Create only docs/e2e-toy/evaluation.md.

Write docs/e2e-toy/evaluation.md exactly:
# E2E Toy Evaluation

- validate: pass
- build: pass
- runtime_marker: E2E_TOY_SKILL_RUNTIME: ok
- E2E_TOY_EVALUATION: ok

Final-answer exactly E2E_TOY_EVALUATION_STAGE: ok."""
E2E_TOY_RUNTIME_PROMPT = (
    "Use the e2e-toy-checklist skill from this workspace. "
    "Read its body and final-answer exactly the runtime marker declared there. "
    "Do not infer or invent the marker."
)
E2E_CLAUDE_REQUIRED_FILES = {
    "docs/e2e-claude-toy/requirements.md": [
        "# E2E Claude Toy Requirements",
        "CLAUDE_TOY_REQUIREMENTS: ok",
    ],
    "docs/e2e-claude-toy/blueprint.md": [
        "# E2E Claude Toy Blueprint",
        "CLAUDE_TOY_BLUEPRINT: ok",
    ],
    "docs/e2e-claude-toy/evaluation.md": [
        "# E2E Claude Toy Evaluation",
        "CLAUDE_TOY_EVALUATION: ok",
    ],
    "docs/e2e-claude-toy/reference-curator.md": [
        "# E2E Claude Toy Reference Curator",
        "CLAUDE_TOY_REFERENCE_CURATOR: ok",
    ],
    "docs/e2e-claude-toy/requirements-analysis.md": [
        "# E2E Claude Toy Requirements Analysis",
        "CLAUDE_TOY_REQUIREMENTS_ANALYSIS: ok",
    ],
    "docs/e2e-claude-toy/blueprint-author.md": [
        "# E2E Claude Toy Blueprint Author",
        "CLAUDE_TOY_BLUEPRINT_AUTHOR: ok",
    ],
}
E2E_CLAUDE_FORBIDDEN_FILES = [
    "components/harness/skills/e2e-claude-toy/component.yml",
    "components/harness/skills/e2e-claude-toy/SKILL.md",
    "components/harness/skills/e2e-claude-toy/provenance.map.yml",
    "dist/codex/.agents/skills/e2e-claude-toy/SKILL.md",
    "dist/claude/.claude/skills/e2e-claude-toy/SKILL.md",
]
E2E_CLAUDE_FORBIDDEN_UNCHANGED_FILES = [
    "components/registry.yml",
]
E2E_CLAUDE_REQUIREMENTS_PROMPT = """Runtime probe only. Use the harness-requirements skill from this workspace. All intake fields are already approved for this deterministic probe. Do not ask clarifying questions. Create only docs/e2e-claude-toy/requirements.md. Do not write blueprint, evaluation, agent phase, components, registry, or dist files.

Write docs/e2e-claude-toy/requirements.md exactly:
# E2E Claude Toy Requirements

- slug: e2e-claude-toy
- goal: prove Claude functional E2E probe can write a minimal requirements artifact
- output_scope: docs/e2e-claude-toy only
- CLAUDE_TOY_REQUIREMENTS: ok

After writing the file, your final response must be exactly CLAUDE_TOY_REQUIREMENTS_STAGE: ok and nothing else. Return no markdown, no explanation, and no extra text."""
E2E_CLAUDE_BLUEPRINT_PROMPT = """Runtime probe only. Use the harness-blueprint skill from this workspace. The requirements are already approved for blueprinting. Do not ask clarifying questions. Read the existing e2e-claude-toy requirements file. Create only docs/e2e-claude-toy/blueprint.md. Do not write evaluation, agent phase, components, registry, or dist files.

Write docs/e2e-claude-toy/blueprint.md exactly:
# E2E Claude Toy Blueprint

- slug: e2e-claude-toy
- kind: documentation-only-probe
- canonical_component: none
- CLAUDE_TOY_BLUEPRINT: ok

After writing the file, your final response must be exactly CLAUDE_TOY_BLUEPRINT_STAGE: ok and nothing else. Return no markdown, no explanation, and no extra text."""
E2E_CLAUDE_EVALUATION_PROMPT = """Runtime probe only. Use the skill-evaluation skill from this workspace. Do not ask clarifying questions. Read the existing e2e-claude-toy requirements and blueprint files. Create only docs/e2e-claude-toy/evaluation.md. Do not write components, registry, or dist files.

Write docs/e2e-claude-toy/evaluation.md exactly:
# E2E Claude Toy Evaluation

- requirements: pass
- blueprint: pass
- CLAUDE_TOY_EVALUATION: ok

After writing the file, your final response must be exactly CLAUDE_TOY_EVALUATION_STAGE: ok and nothing else. Return no markdown, no explanation, and no extra text."""
E2E_CLAUDE_REFERENCE_CURATOR_PROMPT = """Constrained runtime probe. As reference-curator, do not ask clarifying questions. Create only docs/e2e-claude-toy/reference-curator.md. Do not write requirements, blueprint, evaluation, components, registry, or dist files.

Write docs/e2e-claude-toy/reference-curator.md exactly:
# E2E Claude Toy Reference Curator

- source_refs: []
- external_fetch: false
- CLAUDE_TOY_REFERENCE_CURATOR: ok

After writing the file, your final response must be exactly CLAUDE_TOY_REFERENCE_CURATOR_STAGE: ok and nothing else. Return no markdown, no explanation, and no extra text."""
E2E_CLAUDE_REQUIREMENTS_ANALYSIS_PROMPT = """Constrained runtime probe. As harness-requirements-analyst, all intake fields are already approved. Do not ask clarifying questions. Create only docs/e2e-claude-toy/requirements-analysis.md. Do not write blueprint, components, registry, or dist files.

Write docs/e2e-claude-toy/requirements-analysis.md exactly:
# E2E Claude Toy Requirements Analysis

- approved_for_blueprint: true
- missing_inputs: []
- CLAUDE_TOY_REQUIREMENTS_ANALYSIS: ok

After writing the file, your final response must be exactly CLAUDE_TOY_REQUIREMENTS_ANALYSIS_STAGE: ok and nothing else. Return no markdown, no explanation, and no extra text."""
E2E_CLAUDE_BLUEPRINT_AUTHOR_PROMPT = """Constrained runtime probe. As harness-blueprint-author, do not ask clarifying questions. Create only docs/e2e-claude-toy/blueprint-author.md. Do not create canonical component files, registry entries, or dist files.

Write docs/e2e-claude-toy/blueprint-author.md exactly:
# E2E Claude Toy Blueprint Author

- phase_output: constrained-blueprint-author
- canonical_component_created: false
- CLAUDE_TOY_BLUEPRINT_AUTHOR: ok

After writing the file, your final response must be exactly CLAUDE_TOY_BLUEPRINT_AUTHOR_STAGE: ok and nothing else. Return no markdown, no explanation, and no extra text."""
CLAUDE_FUNCTIONAL_STAGES = [
    {
        "name": "claude-harness-requirements",
        "prompt": E2E_CLAUDE_REQUIREMENTS_PROMPT,
        "marker": "CLAUDE_TOY_REQUIREMENTS_STAGE: ok",
        "expected": {
            "docs/e2e-claude-toy/requirements.md": E2E_CLAUDE_REQUIRED_FILES[
                "docs/e2e-claude-toy/requirements.md"
            ]
        },
    },
    {
        "name": "claude-harness-blueprint",
        "prompt": E2E_CLAUDE_BLUEPRINT_PROMPT,
        "marker": "CLAUDE_TOY_BLUEPRINT_STAGE: ok",
        "expected": {
            "docs/e2e-claude-toy/blueprint.md": E2E_CLAUDE_REQUIRED_FILES[
                "docs/e2e-claude-toy/blueprint.md"
            ]
        },
    },
    {
        "name": "claude-skill-evaluation",
        "prompt": E2E_CLAUDE_EVALUATION_PROMPT,
        "marker": "CLAUDE_TOY_EVALUATION_STAGE: ok",
        "expected": {
            "docs/e2e-claude-toy/evaluation.md": E2E_CLAUDE_REQUIRED_FILES[
                "docs/e2e-claude-toy/evaluation.md"
            ]
        },
    },
    {
        "name": "claude-reference-curator",
        "agent": "reference-curator",
        "prompt": E2E_CLAUDE_REFERENCE_CURATOR_PROMPT,
        "marker": "CLAUDE_TOY_REFERENCE_CURATOR_STAGE: ok",
        "expected": {
            "docs/e2e-claude-toy/reference-curator.md": E2E_CLAUDE_REQUIRED_FILES[
                "docs/e2e-claude-toy/reference-curator.md"
            ]
        },
    },
    {
        "name": "claude-harness-requirements-analyst",
        "agent": "harness-requirements-analyst",
        "prompt": E2E_CLAUDE_REQUIREMENTS_ANALYSIS_PROMPT,
        "marker": "CLAUDE_TOY_REQUIREMENTS_ANALYSIS_STAGE: ok",
        "expected": {
            "docs/e2e-claude-toy/requirements-analysis.md": E2E_CLAUDE_REQUIRED_FILES[
                "docs/e2e-claude-toy/requirements-analysis.md"
            ]
        },
    },
    {
        "name": "claude-harness-blueprint-author",
        "agent": "harness-blueprint-author",
        "prompt": E2E_CLAUDE_BLUEPRINT_AUTHOR_PROMPT,
        "marker": "CLAUDE_TOY_BLUEPRINT_AUTHOR_STAGE: ok",
        "expected": {
            "docs/e2e-claude-toy/blueprint-author.md": E2E_CLAUDE_REQUIRED_FILES[
                "docs/e2e-claude-toy/blueprint-author.md"
            ]
        },
    },
]

CODEX_SKILL_PROBES = [
    {
        "name": "harness-requirements",
        "marker": "HARNESS_REQUIREMENTS_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the harness-requirements skill from this workspace. "
            "Final-answer exactly HARNESS_REQUIREMENTS_SKILL_RUNTIME: ok if the "
            "loaded skill body includes no_external, user_supplied_refs, "
            "research_requested, local_migration, requirements.md, and "
            "approved-for-blueprint. Otherwise final-answer exactly "
            "HARNESS_REQUIREMENTS_SKILL_RUNTIME: unavailable."
        ),
    },
    {
        "name": "harness-blueprint",
        "marker": "HARNESS_BLUEPRINT_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the harness-blueprint skill from this workspace. "
            "Final-answer exactly HARNESS_BLUEPRINT_SKILL_RUNTIME: ok if the "
            "loaded skill body includes Component Kind Decision Matrix and the "
            "phrase skill, agent, hook, workflow, rule, or command. Otherwise "
            "final-answer exactly HARNESS_BLUEPRINT_SKILL_RUNTIME: unavailable."
        ),
    },
    {
        "name": "skill-evaluation",
        "marker": "SKILL_EVALUATION_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the skill-evaluation skill from this workspace. "
            "Final-answer exactly SKILL_EVALUATION_SKILL_RUNTIME: ok if the "
            "loaded skill body includes PASS_WITH_DEFERRED, Do not fix files "
            "unless the user asks, and Static adapter output is not runtime "
            "proof. Otherwise final-answer exactly "
            "SKILL_EVALUATION_SKILL_RUNTIME: unavailable."
        ),
    },
]

CODEX_AGENT_PROBES = [
    {
        "name": "reference-curator",
        "agent_type": "reference_curator",
        "sub_marker": "REFERENCE_CURATOR_AGENT_OK",
        "marker": "REFERENCE_CURATOR_AGENT_RUNTIME: ok",
        "prompt": (
            "Runtime probe only. Spawn a subagent with "
            "agent_type='reference_curator'. Ask it to answer exactly "
            "REFERENCE_CURATOR_AGENT_OK if it can see Reference Curator "
            "instructions and the boundary Do not write `requirements.md`. "
            "Final-answer exactly REFERENCE_CURATOR_AGENT_RUNTIME: ok if the "
            "subagent succeeded; otherwise final-answer exactly "
            "REFERENCE_CURATOR_AGENT_RUNTIME: unavailable with the error."
        ),
    },
    {
        "name": "harness-requirements-analyst",
        "agent_type": "harness_requirements_analyst",
        "sub_marker": "HARNESS_REQUIREMENTS_ANALYST_AGENT_OK",
        "marker": "HARNESS_REQUIREMENTS_ANALYST_AGENT_RUNTIME: ok",
        "prompt": (
            "Runtime probe only. Spawn a subagent with "
            "agent_type='harness_requirements_analyst'. Ask it to answer exactly "
            "HARNESS_REQUIREMENTS_ANALYST_AGENT_OK if it can see Harness "
            "Requirements Analyst instructions and the boundary Do not write "
            "blueprint.md. Final-answer exactly "
            "HARNESS_REQUIREMENTS_ANALYST_AGENT_RUNTIME: ok if the subagent "
            "succeeded; otherwise final-answer exactly "
            "HARNESS_REQUIREMENTS_ANALYST_AGENT_RUNTIME: unavailable with the error."
        ),
    },
    {
        "name": "harness-blueprint-author",
        "agent_type": "harness_blueprint_author",
        "sub_marker": "HARNESS_BLUEPRINT_AUTHOR_AGENT_OK",
        "marker": "HARNESS_BLUEPRINT_AUTHOR_AGENT_RUNTIME: ok",
        "prompt": (
            "Runtime probe only. Spawn a subagent with "
            "agent_type='harness_blueprint_author'. Ask it to answer exactly "
            "HARNESS_BLUEPRINT_AUTHOR_AGENT_OK if it can see Harness Blueprint "
            "Author instructions and the boundary Do not create canonical "
            "component files. Final-answer exactly "
            "HARNESS_BLUEPRINT_AUTHOR_AGENT_RUNTIME: ok if the subagent "
            "succeeded; otherwise final-answer exactly "
            "HARNESS_BLUEPRINT_AUTHOR_AGENT_RUNTIME: unavailable with the error."
        ),
    },
]

CLAUDE_SKILL_PROBES = [
    {
        "name": "harness-requirements",
        "marker": "CLAUDE_HARNESS_REQUIREMENTS_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the harness-requirements skill from this workspace. Return exactly "
            "CLAUDE_HARNESS_REQUIREMENTS_SKILL_RUNTIME: ok if the skill body includes "
            "no_external, user_supplied_refs, research_requested, and approved-for-blueprint."
        ),
    },
    {
        "name": "harness-blueprint",
        "marker": "CLAUDE_HARNESS_BLUEPRINT_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the harness-blueprint skill from this workspace. Return exactly "
            "CLAUDE_HARNESS_BLUEPRINT_SKILL_RUNTIME: ok if the skill body includes "
            "Component Kind Decision Matrix and skill, agent, hook, workflow, rule, or command."
        ),
    },
    {
        "name": "skill-evaluation",
        "marker": "CLAUDE_SKILL_EVALUATION_SKILL_RUNTIME: ok",
        "prompt": (
            "Use the skill-evaluation skill from this workspace. Return exactly "
            "CLAUDE_SKILL_EVALUATION_SKILL_RUNTIME: ok if the skill body includes "
            "PASS_WITH_DEFERRED and Static adapter output is not runtime proof."
        ),
    },
]

CLAUDE_AGENT_PROBES = [
    {
        "name": "reference-curator",
        "marker": "CLAUDE_REFERENCE_CURATOR_AGENT_RUNTIME: ok",
        "prompt": (
            "Return exactly CLAUDE_REFERENCE_CURATOR_AGENT_RUNTIME: ok if your "
            "agent instructions identify you as Reference Curator and include "
            "Do not write `requirements.md`."
        ),
    },
    {
        "name": "harness-requirements-analyst",
        "marker": "CLAUDE_HARNESS_REQUIREMENTS_ANALYST_AGENT_RUNTIME: ok",
        "prompt": (
            "Return exactly CLAUDE_HARNESS_REQUIREMENTS_ANALYST_AGENT_RUNTIME: ok "
            "if your agent instructions identify you as Harness Requirements "
            "Analyst and include Do not write `blueprint.md`."
        ),
    },
    {
        "name": "harness-blueprint-author",
        "marker": "CLAUDE_HARNESS_BLUEPRINT_AUTHOR_AGENT_RUNTIME: ok",
        "prompt": (
            "Return exactly CLAUDE_HARNESS_BLUEPRINT_AUTHOR_AGENT_RUNTIME: ok if "
            "your agent instructions identify you as Harness Blueprint Author "
            "and include Do not create canonical component files."
        ),
    },
]


THREAD_ID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        timeout_message = f"command timed out after {timeout_seconds} seconds"
        stderr = (stderr.rstrip() + "\n" + timeout_message).lstrip()
        return subprocess.CompletedProcess(argv, 124, stdout, stderr)


def _now_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%S%z")


def _contract() -> dict[str, Any]:
    return {
        "profile": PROFILE,
        "evidence_root": str(EVIDENCE_ROOT.relative_to(REPO_ROOT)),
        "persisted_outputs": [
            "result.json",
            "summary.md",
            "latest.json",
            "install-plan.apply.json",
            "functional-workspace.tar.gz",
            "codex/*.jsonl",
            "codex/*-stderr.txt",
            "codex/*-last-message.txt",
            "claude/*.jsonl",
            "claude/*-stderr.txt",
        ],
        "codex": {
            "skills": [probe["name"] for probe in CODEX_SKILL_PROBES],
            "agents": [probe["name"] for probe in CODEX_AGENT_PROBES],
            "agent_types": [probe["agent_type"] for probe in CODEX_AGENT_PROBES],
            "thread_links": True,
        },
        "claude": {
            "skills": [probe["name"] for probe in CLAUDE_SKILL_PROBES],
            "agents": [probe["name"] for probe in CLAUDE_AGENT_PROBES],
        },
        "non_goals": [
            "workflow runner runtime",
            "hook interception",
            "rule runtime",
            "command runtime",
        ],
        "modes": MODES,
        "functional_e2e": {
            "requires": FUNCTIONAL_E2E_REQUIRED_CAPS,
            "target_requires": {
                "codex": [
                    "codex_model",
                    "max_effort<=medium",
                    "max_budget_usd",
                    "timeout_seconds<=300",
                ],
                "claude": [
                    "claude_model",
                    "max_effort<=medium",
                    "max_budget_usd",
                    "timeout_seconds<=300",
                ],
            },
            "caps": {
                "codex_model": "luna policy label; Codex CLI inherits the parent model",
                "claude_model": "haiku or concrete names containing haiku",
                "max_budget_usd": "finite positive <= 0.25, validated and recorded as guard metadata; Codex CLI native USD cap is unavailable",
                "max_effort": "medium",
                "max_timeout_seconds": FUNCTIONAL_E2E_MAX_TIMEOUT_SECONDS,
                "native_budget_flag": False,
                "codex_max_live_calls": FUNCTIONAL_CODEX_MAX_LIVE_CALLS,
                "claude_max_live_calls": FUNCTIONAL_CLAUDE_MAX_LIVE_CALLS,
            },
        },
        "proof_types": {
            "loading_dispatch": "Runtime proof that installed skills and agents load and dispatch in Codex and Claude.",
            "functional_e2e": "Runtime proof that the harness-creation workflow can produce functional end-to-end evidence under capped policy, effort, timeout, and live-call settings.",
        },
    }


def _prepare_output_dir(raw_output_dir: Path | None, *, overwrite: bool) -> Path:
    output_dir = raw_output_dir or EVIDENCE_ROOT / _now_stamp()
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    output_dir = output_dir.resolve()
    evidence_root = EVIDENCE_ROOT.resolve()
    try:
        output_dir.relative_to(evidence_root)
    except ValueError as exc:
        raise ValueError(f"--output-dir must stay under {evidence_root}") from exc
    if output_dir == evidence_root:
        raise ValueError("--output-dir must be a run directory below the evidence root")
    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Evidence directory already exists: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    return output_dir


def _materialize_workspace(output_dir: Path) -> tuple[Path, Path]:
    workspace = output_dir / "workspace"
    workspace.mkdir()

    plan_path = output_dir / "install-plan.apply.json"
    plan = _run(
        [
            sys.executable,
            "scripts/install/plan.py",
            "--profile",
            PROFILE,
            "--scope",
            "project",
            "--mode",
            "apply",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
    )
    (output_dir / "install-plan-stderr.txt").write_text(plan.stderr, encoding="utf-8")
    if plan.returncode != 0:
        raise RuntimeError(plan.stderr)
    plan_path.write_text(plan.stdout, encoding="utf-8")

    apply_result = _run(
        [
            sys.executable,
            "scripts/install/apply.py",
            str(plan_path),
            "--target-root",
            str(workspace),
            "--overwrite",
        ],
        cwd=REPO_ROOT,
    )
    (output_dir / "install-apply-stdout.txt").write_text(apply_result.stdout, encoding="utf-8")
    (output_dir / "install-apply-stderr.txt").write_text(apply_result.stderr, encoding="utf-8")
    if apply_result.returncode != 0:
        raise RuntimeError(apply_result.stderr)

    (workspace / "AGENTS.md").write_text(
        "Use generated HarnessKit harness-maintenance runtime surfaces in this workspace.\n",
        encoding="utf-8",
    )
    return workspace, plan_path


def _git_tracked_files() -> list[Path]:
    result = _run(["git", "ls-files", "-z"], cwd=REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return [Path(path) for path in result.stdout.split("\0") if path]


def _include_in_functional_workspace(rel_path: Path) -> bool:
    return not rel_path.parts[:2] == ("docs", "runtime-evidence")


def _copy_git_tracked_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True)
    for rel_path in _git_tracked_files():
        if not _include_in_functional_workspace(rel_path):
            continue
        source = REPO_ROOT / rel_path
        destination = workspace / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            destination.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, destination)


def _materialize_functional_workspace(output_dir: Path) -> tuple[Path, Path]:
    workspace = output_dir / "functional-workspace"
    _copy_git_tracked_workspace(workspace)

    plan_path = output_dir / "install-plan.apply.json"
    plan = _run(
        [
            sys.executable,
            "scripts/install/plan.py",
            "--profile",
            PROFILE,
            "--scope",
            "project",
            "--mode",
            "apply",
            "--format",
            "json",
        ],
        cwd=workspace,
    )
    (output_dir / "install-plan-stderr.txt").write_text(plan.stderr, encoding="utf-8")
    if plan.returncode != 0:
        raise RuntimeError(plan.stderr)
    plan_path.write_text(plan.stdout, encoding="utf-8")

    apply_result = _run(
        [
            sys.executable,
            "scripts/install/apply.py",
            str(plan_path),
            "--target-root",
            str(workspace),
            "--overwrite",
        ],
        cwd=workspace,
    )
    (output_dir / "install-apply-stdout.txt").write_text(apply_result.stdout, encoding="utf-8")
    (output_dir / "install-apply-stderr.txt").write_text(apply_result.stderr, encoding="utf-8")
    if apply_result.returncode != 0:
        raise RuntimeError(apply_result.stderr)

    (workspace / "AGENTS.md").write_text(
        "Use generated HarnessKit harness-maintenance runtime surfaces in this functional E2E workspace.\n",
        encoding="utf-8",
    )
    return workspace, plan_path


def _codex_home(workspace: Path) -> tuple[Path, dict[str, str]]:
    codex_home = Path(tempfile.mkdtemp(prefix="harnesskit-codex-home."))
    real_codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    real_auth = real_codex_home / "auth.json"
    if real_auth.exists():
        (codex_home / "auth.json").symlink_to(real_auth)
    (codex_home / "config.toml").write_text(
        f'[projects."{workspace}"]\ntrust_level = "trusted"\n\n[features]\nhooks = true\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    return codex_home, env


def _codex_thread_links(stdout: str) -> list[str]:
    thread_ids: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw = json.dumps(event)
        for thread_id in THREAD_ID_RE.findall(raw):
            if thread_id not in thread_ids:
                thread_ids.append(thread_id)
    return [f"codex://threads/{thread_id}" for thread_id in thread_ids]


def _capped_effort(max_effort: str) -> str:
    return "low" if max_effort == "low" else "medium"


def _codex_functional_exec_argv(
    *,
    workspace: Path,
    codex_model: str,
    max_effort: str,
    last_message: Path,
    prompt: str,
    enable_multi_agent: bool = False,
) -> list[str]:
    if codex_model != FUNCTIONAL_CODEX_MODEL_POLICY:
        raise ValueError(f"functional Codex model policy must be {FUNCTIONAL_CODEX_MODEL_POLICY}")
    effort = _capped_effort(max_effort)
    argv = [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(workspace),
        "-c",
        f'model_reasoning_effort="{effort}"',
    ]
    if enable_multi_agent:
        argv.extend(["--enable", "multi_agent"])
    argv.extend(
        [
            "--output-last-message",
            str(last_message),
            prompt,
        ]
    )
    return argv


def _record_command(
    *,
    name: str,
    argv: list[str],
    cwd: Path,
    env: dict[str, str] | None,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
) -> tuple[dict[str, Any], subprocess.CompletedProcess[str]]:
    result = _run(argv, cwd=cwd, env=env, timeout_seconds=timeout_seconds)
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    record = {
        "name": name,
        "argv": argv,
        "cwd": str(cwd),
        "returncode": result.returncode,
        "timeout_seconds": timeout_seconds,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    return record, result


def _format_budget_usd(max_budget_usd: float) -> str:
    return f"{max_budget_usd:g}"


def _override_functional_codex_agent_models(
    workspace: Path,
    *,
    codex_model: str,
    max_effort: str,
) -> list[str]:
    if codex_model != FUNCTIONAL_CODEX_MODEL_POLICY:
        raise ValueError(f"functional Codex model policy must be {FUNCTIONAL_CODEX_MODEL_POLICY}")
    agent_dir = workspace / ".codex" / "agents"
    if not agent_dir.is_dir():
        return []
    changed: list[str] = []
    effort = _capped_effort(max_effort)
    for path in sorted(agent_dir.glob("*.toml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        new_lines: list[str] = []
        name_seen = False
        description_seen = False
        effort_seen = False
        for line in lines:
            if line.startswith("name = "):
                name_seen = True
            if line.startswith("description = "):
                description_seen = True
            if line.startswith("model = "):
                continue
            if line.startswith("model_reasoning_effort = "):
                new_lines.append(f'model_reasoning_effort = "{effort}"')
                effort_seen = True
                continue
            new_lines.append(line)
        if not name_seen:
            new_lines.insert(0, f'name = "{path.stem}"')
        if not description_seen:
            insert_at = 1 if new_lines and new_lines[0].startswith("name = ") else 0
            new_lines.insert(insert_at, f'description = "Runtime probe agent role {path.stem}"')
        if not effort_seen:
            insert_at = 0
            if new_lines and new_lines[0].startswith("name = "):
                insert_at = 1
            if len(new_lines) > insert_at and new_lines[insert_at].startswith("description = "):
                insert_at += 1
            new_lines.insert(insert_at, f'model_reasoning_effort = "{effort}"')
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        changed.append(str(path))
    return changed


def _override_functional_claude_agent_models(
    workspace: Path,
    *,
    claude_model: str,
) -> list[str]:
    agent_dir = workspace / ".claude" / "agents"
    if not agent_dir.is_dir():
        return []
    changed: list[str] = []
    for path in sorted(agent_dir.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        if lines and lines[0].strip() == "---":
            end_index = next(
                (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
                None,
            )
            if end_index is not None:
                model_seen = False
                new_lines: list[str] = []
                for index, line in enumerate(lines):
                    if 0 < index < end_index and line.startswith("model:"):
                        new_lines.append(f"model: {claude_model}")
                        model_seen = True
                        continue
                    new_lines.append(line)
                if not model_seen:
                    new_lines.insert(end_index, f"model: {claude_model}")
                path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                changed.append(str(path))
                continue
        path.write_text(
            f"---\nmodel: {claude_model}\n---\n\n" + path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        changed.append(str(path))
    return changed


def _has_component_author_agent_override(paths: list[str]) -> bool:
    return any(Path(path).stem in {"component_author", "component-author"} for path in paths)


def _nested_value(data: Any, keys: tuple[str, ...]) -> Any:
    value = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _thread_id_from_spawn(item: dict[str, Any]) -> str | None:
    receiver_thread_ids = item.get("receiver_thread_ids")
    if isinstance(receiver_thread_ids, list):
        for thread_id in receiver_thread_ids:
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    candidates = [
        item.get("receiver_thread_id"),
        item.get("thread_id"),
        _nested_value(item, ("result", "receiver_thread_id")),
        _nested_value(item, ("result", "thread_id")),
        _nested_value(item, ("output", "receiver_thread_id")),
        _nested_value(item, ("output", "thread_id")),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _run_codex_functional_stage(
    *,
    workspace: Path,
    target_dir: Path,
    env: dict[str, str],
    codex_model: str,
    max_effort: str,
    name: str,
    prompt: str,
    marker: str,
    expected: dict[str, list[str]],
    timeout_seconds: int,
    enable_multi_agent: bool = False,
    required_subagent_marker: str | None = None,
) -> tuple[dict[str, Any], subprocess.CompletedProcess[str], list[str]]:
    last_message = target_dir / f"{name}-last-message.txt"
    argv = _codex_functional_exec_argv(
        workspace=workspace,
        codex_model=codex_model,
        max_effort=max_effort,
        last_message=last_message,
        prompt=prompt,
        enable_multi_agent=enable_multi_agent,
    )
    record, result = _record_command(
        name=name,
        argv=argv,
        cwd=workspace,
        env=env,
        stdout_path=target_dir / f"{name}.jsonl",
        stderr_path=target_dir / f"{name}-stderr.txt",
        timeout_seconds=timeout_seconds,
    )
    failures: list[str] = []
    message = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    if result.returncode != 0:
        failures.append(f"{name} command failed")
    if marker not in message:
        failures.append(f"{name} marker missing")
    if required_subagent_marker:
        subagent_evidence = _codex_component_author_subagent_evidence(
            result.stdout,
            required_subagent_marker,
        )
        record["subagent_evidence"] = {
            "completed": subagent_evidence["completed"],
            "marker_contract_sent": subagent_evidence["marker_contract_sent"],
            "contract_completed": subagent_evidence["contract_completed"],
            "marker_found": subagent_evidence["marker_found"],
            "marker": required_subagent_marker,
            "source": subagent_evidence["source"],
        }
        if not subagent_evidence["contract_completed"]:
            failures.append(f"{name} subagent evidence missing: {required_subagent_marker}")
    failures.extend(_verify_snippets(workspace, expected))
    return record, result, failures


def _relative_existing_files(workspace: Path, paths: list[str]) -> list[str]:
    return [path for path in paths if (workspace / path).is_file()]


def _verify_snippets(workspace: Path, expected: dict[str, list[str]]) -> list[str]:
    failures: list[str] = []
    for rel_path, snippets in expected.items():
        path = workspace / rel_path
        if not path.is_file():
            failures.append(f"missing: {rel_path}")
            continue
        text = path.read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                failures.append(f"{rel_path}: missing snippet {snippet!r}")
    if any(rel_path.startswith("components/") for rel_path in expected):
        registry_path = workspace / "components" / "registry.yml"
        if not registry_path.is_file():
            failures.append("missing: components/registry.yml")
        else:
            registry_text = registry_path.read_text(encoding="utf-8")
            registry_snippets = [
                "harnesskit.skill.e2e-toy-checklist:",
                "kind: skill",
                "components/harness/skills/e2e-toy-checklist/component.yml",
            ]
            for snippet in registry_snippets:
                if snippet not in registry_text:
                    failures.append(f"components/registry.yml: missing snippet {snippet!r}")
    return failures


def _deploy_generated_toy_skill(workspace: Path) -> str | None:
    generated = workspace / E2E_TOY_GENERATED_SKILL
    if not generated.is_file():
        return f"missing: {E2E_TOY_GENERATED_SKILL}"
    live = workspace / E2E_TOY_LIVE_SKILL
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(generated.read_text(encoding="utf-8"), encoding="utf-8")
    return None


def _run_functional_codex(
    *,
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    codex_model: str,
    max_effort: str,
    max_budget_usd: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    target_dir = output_dir / "codex"
    target_dir.mkdir(exist_ok=True)
    commands: list[dict[str, Any]] = []
    failures: list[str] = []
    thread_links: list[str] = []
    live_calls_used = 0
    runtime_skipped_reason: str | None = None
    agent_overrides = _override_functional_codex_agent_models(
        workspace,
        codex_model=codex_model,
        max_effort=max_effort,
    )

    def run_stage(
        *,
        name: str,
        prompt: str,
        marker: str,
        expected: dict[str, list[str]],
        enable_multi_agent: bool = False,
        required_subagent_marker: str | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        nonlocal live_calls_used, runtime_skipped_reason
        if failures:
            if runtime_skipped_reason is None:
                runtime_skipped_reason = f"prior failures before {name}"
            return None
        if live_calls_used >= FUNCTIONAL_CODEX_MAX_LIVE_CALLS:
            failures.append(f"live call ceiling reached before {name}")
            if runtime_skipped_reason is None:
                runtime_skipped_reason = f"live call ceiling before {name}"
            return None
        record, result, stage_failures = _run_codex_functional_stage(
            workspace=workspace,
            target_dir=target_dir,
            env=env,
            codex_model=codex_model,
            max_effort=max_effort,
            name=name,
            prompt=prompt,
            marker=marker,
            expected=expected,
            timeout_seconds=timeout_seconds,
            enable_multi_agent=enable_multi_agent,
            required_subagent_marker=required_subagent_marker,
        )
        commands.append(record)
        live_calls_used += 1
        thread_links.extend(_codex_thread_links(result.stdout))
        failures.extend(stage_failures)
        return result

    run_stage(
        name="codex-harness-requirements",
        prompt=E2E_REQUIREMENTS_PROMPT,
        marker="E2E_TOY_REQUIREMENTS_STAGE: ok",
        expected={"docs/e2e-toy/requirements.md": E2E_TOY_REQUIRED_FILES["docs/e2e-toy/requirements.md"]},
    )
    run_stage(
        name="codex-harness-blueprint",
        prompt=E2E_BLUEPRINT_PROMPT,
        marker="E2E_TOY_BLUEPRINT_STAGE: ok",
        expected={"docs/e2e-toy/blueprint.md": E2E_TOY_REQUIRED_FILES["docs/e2e-toy/blueprint.md"]},
    )
    if not failures and not _has_component_author_agent_override(agent_overrides):
        failures.append("component_author agent config override missing")
    run_stage(
        name="codex-component-author",
        prompt=E2E_COMPONENT_AUTHOR_PROMPT,
        marker="E2E_TOY_COMPONENT_AUTHOR_STAGE: ok",
        expected={
            "components/harness/skills/e2e-toy-checklist/component.yml": E2E_TOY_REQUIRED_FILES[
                "components/harness/skills/e2e-toy-checklist/component.yml"
            ],
            "components/harness/skills/e2e-toy-checklist/SKILL.md": E2E_TOY_REQUIRED_FILES[
                "components/harness/skills/e2e-toy-checklist/SKILL.md"
            ],
            "components/harness/skills/e2e-toy-checklist/provenance.map.yml": E2E_TOY_REQUIRED_FILES[
                "components/harness/skills/e2e-toy-checklist/provenance.map.yml"
            ],
        },
        enable_multi_agent=True,
        required_subagent_marker=E2E_COMPONENT_AUTHOR_SUBAGENT_MARKER,
    )

    validate_argv = [
        "uv",
        "run",
        "python",
        "scripts/components/validate.py",
        "--component",
        E2E_TOY_COMPONENT_ID,
    ]
    validate_record, validate = _record_command(
        name="validate-toy-component",
        argv=validate_argv,
        cwd=workspace,
        env=None,
        stdout_path=target_dir / "toy-validate-stdout.txt",
        stderr_path=target_dir / "toy-validate-stderr.txt",
        timeout_seconds=timeout_seconds,
    )
    commands.append(validate_record)
    if validate.returncode != 0:
        failures.append("toy component validation failed")

    build_argv = [
        "uv",
        "run",
        "python",
        "scripts/adapters/build.py",
        "--component",
        E2E_TOY_COMPONENT_ID,
    ]
    build_record, build = _record_command(
        name="build-toy-codex-output",
        argv=build_argv,
        cwd=workspace,
        env=None,
        stdout_path=target_dir / "toy-build-stdout.txt",
        stderr_path=target_dir / "toy-build-stderr.txt",
        timeout_seconds=timeout_seconds,
    )
    commands.append(build_record)
    if build.returncode != 0:
        failures.append("toy adapter build failed")

    generated_failures = _verify_snippets(
        workspace,
        {E2E_TOY_GENERATED_SKILL: ["name: e2e-toy-checklist", "E2E_TOY_SKILL_RUNTIME: ok"]},
    )
    failures.extend(generated_failures)
    deploy_failure = _deploy_generated_toy_skill(workspace)
    if deploy_failure:
        failures.append(deploy_failure)

    run_stage(
        name="codex-skill-evaluation",
        prompt=(
            f"{E2E_EVALUATION_PROMPT}\n\n"
            f"Controller validate_returncode={validate.returncode}. "
            f"Controller build_returncode={build.returncode}."
        ),
        marker="E2E_TOY_EVALUATION_STAGE: ok",
        expected={"docs/e2e-toy/evaluation.md": E2E_TOY_REQUIRED_FILES["docs/e2e-toy/evaluation.md"]},
    )
    run_stage(
        name="codex-load-generated-toy-skill",
        prompt=E2E_TOY_RUNTIME_PROMPT,
        marker="E2E_TOY_SKILL_RUNTIME: ok",
        expected={E2E_TOY_LIVE_SKILL: ["name: e2e-toy-checklist", "E2E_TOY_SKILL_RUNTIME: ok"]},
    )

    file_paths = _relative_existing_files(
        workspace,
        [
            *E2E_TOY_REQUIRED_FILES.keys(),
            E2E_TOY_GENERATED_SKILL,
            E2E_TOY_LIVE_SKILL,
            "components/registry.yml",
        ],
    )
    deduped_thread_links = list(dict.fromkeys(thread_links))
    result = {
        "target": "codex",
        "skipped": False,
        "passed": not failures,
        "model": codex_model,
        "model_policy": FUNCTIONAL_CODEX_MODEL_POLICY,
        "effort": _capped_effort(max_effort),
        "timeout_seconds": timeout_seconds,
        "max_budget_usd": max_budget_usd,
        "native_budget_flag": False,
        "budget_guard": FUNCTIONAL_CODEX_BUDGET_GUARD,
        "max_live_calls": FUNCTIONAL_CODEX_MAX_LIVE_CALLS,
        "live_calls_used": live_calls_used,
        "agent_model_overrides": agent_overrides,
        "component_author_agent_override_present": _has_component_author_agent_override(agent_overrides),
        "component_author_subagent_marker": E2E_COMPONENT_AUTHOR_SUBAGENT_MARKER,
        "commands": commands,
        "file_paths": file_paths,
        "failures": failures,
        "thread_links": deduped_thread_links,
    }
    if runtime_skipped_reason:
        result["runtime_skipped_reason"] = runtime_skipped_reason
    return result


def _claude_functional_argv(
    *,
    workspace: Path,
    claude_model: str,
    max_effort: str,
    max_budget_usd: float,
    prompt: str,
    agent: str | None = None,
) -> list[str]:
    del workspace
    argv = [
        "claude",
        "-p",
        "--verbose",
        "--model",
        claude_model,
        "--effort",
        _capped_effort(max_effort),
        "--max-budget-usd",
        _format_budget_usd(max_budget_usd),
        "--permission-mode",
        "bypassPermissions",
        "--setting-sources",
        "project",
        "--output-format",
        "stream-json",
    ]
    if agent:
        argv.extend(["--agent", agent])
    argv.append(prompt)
    return argv


def _verify_absent(workspace: Path, paths: list[str]) -> list[str]:
    return [path for path in paths if (workspace / path).exists()]


def _snapshot_files(workspace: Path, paths: list[str]) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    for rel_path in paths:
        path = workspace / rel_path
        snapshot[rel_path] = path.read_text(encoding="utf-8") if path.is_file() else None
    return snapshot


def _verify_unchanged(workspace: Path, snapshot: dict[str, str | None]) -> list[str]:
    changed: list[str] = []
    for rel_path, original in snapshot.items():
        path = workspace / rel_path
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != original:
            changed.append(rel_path)
    return changed


def _claude_stream_result_text(stdout: str) -> str | None:
    result_text: str | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            event.get("type") != "result"
            or event.get("subtype") != "success"
            or event.get("is_error") is True
        ):
            continue
        raw_result = event.get("result")
        if isinstance(raw_result, str):
            result_text = raw_result.strip()
    return result_text


def _git_status_lines() -> set[str]:
    result = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return {line for line in result.stdout.splitlines() if line}


def _git_diff_snapshot() -> str:
    result = _run(["git", "diff", "--no-ext-diff", "--binary"], cwd=REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def _status_line_path(line: str) -> Path:
    raw_path = line[3:]
    if " -> " in raw_path:
        raw_path = raw_path.rsplit(" -> ", 1)[1]
    return Path(raw_path)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _outside_workspace_status_changes(before: set[str], after: set[str], workspace: Path) -> list[str]:
    try:
        workspace_rel = workspace.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return sorted(after - before)
    outside: list[str] = []
    for line in sorted(after - before):
        if not _is_relative_to(_status_line_path(line), workspace_rel):
            outside.append(line)
    return outside


def _prune_functional_workspace_runtime_noise(workspace: Path) -> list[str]:
    removed: list[str] = []
    venv = workspace / ".venv"
    if venv.exists():
        shutil.rmtree(venv)
        removed.append(str(venv))
    for pycache in workspace.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache)
            removed.append(str(pycache))
    return removed


def _archive_functional_workspace(output_dir: Path, workspace: Path) -> Path:
    archive = output_dir / "functional-workspace.tar.gz"
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(workspace, arcname="functional-workspace")
    shutil.rmtree(workspace)
    return archive


def _claude_workspace_scoped_prompt(*, workspace: Path, prompt: str) -> str:
    workspace_root = str(workspace.resolve())
    return f"""Runtime filesystem boundary:
- Writable root for this probe: {workspace_root}
- Resolve every relative output path under that root.
- For any requested `docs/e2e-claude-toy/...` path, write exactly under `{workspace_root}/docs/e2e-claude-toy/...`.
- Do not create, edit, or delete files outside `{workspace_root}`.

{prompt}"""


def _run_claude_functional_stage(
    *,
    workspace: Path,
    target_dir: Path,
    claude_model: str,
    max_effort: str,
    max_budget_usd: float,
    name: str,
    prompt: str,
    marker: str,
    expected: dict[str, list[str]],
    forbidden: list[str],
    unchanged_snapshot: dict[str, str | None],
    timeout_seconds: int,
    agent: str | None = None,
) -> tuple[dict[str, Any], list[str], list[str], list[str]]:
    argv = _claude_functional_argv(
        workspace=workspace,
        claude_model=claude_model,
        max_effort=max_effort,
        max_budget_usd=max_budget_usd,
        prompt=_claude_workspace_scoped_prompt(workspace=workspace, prompt=prompt),
        agent=agent,
    )
    status_before = _git_status_lines()
    diff_before = _git_diff_snapshot()
    result = _run(argv, cwd=workspace, env=None, timeout_seconds=timeout_seconds)
    diff_after = _git_diff_snapshot()
    status_after = _git_status_lines()
    stdout_path = target_dir / f"{name}.jsonl"
    stderr_path = target_dir / f"{name}-stderr.txt"
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    record = {
        "name": name,
        "argv": argv,
        "cwd": str(workspace),
        "returncode": result.returncode,
        "timeout_seconds": timeout_seconds,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    outside_workspace_changes = _outside_workspace_status_changes(
        status_before,
        status_after,
        workspace,
    )
    failures: list[str] = []
    if result.returncode != 0:
        failures.append(f"{name} command failed")
    if diff_after != diff_before:
        failures.append(f"{name} modified tracked files outside functional workspace")
    for line in outside_workspace_changes:
        failures.append(f"{name} changed path outside functional workspace: {line}")
    final_result = _claude_stream_result_text(result.stdout)
    marker_found = isinstance(final_result, str) and marker in final_result
    marker_exact = final_result == marker
    if not marker_found:
        failures.append(f"{name} marker missing")
    failures.extend(_verify_snippets(workspace, expected))
    forbidden_present = _verify_absent(workspace, forbidden)
    for rel_path in forbidden_present:
        failures.append(f"{name} wrote forbidden file: {rel_path}")
    forbidden_changed = _verify_unchanged(workspace, unchanged_snapshot)
    for rel_path in forbidden_changed:
        failures.append(f"{name} modified forbidden file: {rel_path}")
    record["marker"] = marker
    record["final_result"] = final_result
    record["marker_found"] = marker_found
    record["marker_exact"] = marker_exact
    record["agent"] = agent
    record["outside_workspace_changes"] = outside_workspace_changes
    record["tracked_diff_changed_outside_workspace"] = diff_after != diff_before
    record["forbidden_files_checked"] = forbidden
    record["forbidden_files_present"] = forbidden_present
    record["forbidden_files_unchanged_checked"] = list(unchanged_snapshot)
    record["forbidden_files_changed"] = forbidden_changed
    return record, failures, forbidden_present, forbidden_changed


def _run_functional_claude(
    *,
    workspace: Path,
    output_dir: Path,
    claude_model: str,
    max_effort: str,
    max_budget_usd: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    target_dir = output_dir / "claude"
    target_dir.mkdir(exist_ok=True)
    commands: list[dict[str, Any]] = []
    failures: list[str] = []
    forbidden_files_present: list[str] = []
    forbidden_files_changed: list[str] = []
    runtime_skipped_reason: str | None = None
    unchanged_snapshot = _snapshot_files(workspace, E2E_CLAUDE_FORBIDDEN_UNCHANGED_FILES)
    agent_overrides = _override_functional_claude_agent_models(
        workspace,
        claude_model=claude_model,
    )

    for stage in CLAUDE_FUNCTIONAL_STAGES:
        name = str(stage["name"])
        if failures:
            if runtime_skipped_reason is None:
                runtime_skipped_reason = f"prior failures before {name}"
            break
        (
            record,
            stage_failures,
            stage_forbidden_present,
            stage_forbidden_changed,
        ) = _run_claude_functional_stage(
            workspace=workspace,
            target_dir=target_dir,
            claude_model=claude_model,
            max_effort=max_effort,
            max_budget_usd=max_budget_usd,
            name=name,
            prompt=str(stage["prompt"]),
            marker=str(stage["marker"]),
            expected=stage["expected"],  # type: ignore[arg-type]
            forbidden=E2E_CLAUDE_FORBIDDEN_FILES,
            unchanged_snapshot=unchanged_snapshot,
            timeout_seconds=timeout_seconds,
            agent=stage.get("agent"),  # type: ignore[arg-type]
        )
        commands.append(record)
        failures.extend(stage_failures)
        forbidden_files_present.extend(stage_forbidden_present)
        forbidden_files_changed.extend(stage_forbidden_changed)

    file_paths = _relative_existing_files(
        workspace,
        list(E2E_CLAUDE_REQUIRED_FILES.keys()),
    )
    result = {
        "target": "claude",
        "skipped": False,
        "passed": not failures,
        "model": claude_model,
        "effort": _capped_effort(max_effort),
        "timeout_seconds": timeout_seconds,
        "max_budget_usd": max_budget_usd,
        "max_live_calls": FUNCTIONAL_CLAUDE_MAX_LIVE_CALLS,
        "live_calls_used": len(commands),
        "agent_model_overrides": agent_overrides,
        "commands": commands,
        "file_paths": file_paths,
        "forbidden_files_checked": E2E_CLAUDE_FORBIDDEN_FILES,
        "forbidden_files_unchanged_checked": E2E_CLAUDE_FORBIDDEN_UNCHANGED_FILES,
        "forbidden_files_present": list(dict.fromkeys(forbidden_files_present)),
        "forbidden_files_changed": list(dict.fromkeys(forbidden_files_changed)),
        "failures": failures,
    }
    if runtime_skipped_reason:
        result["runtime_skipped_reason"] = runtime_skipped_reason
    return result


def _run_codex_skill(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    probe: dict[str, str],
) -> dict[str, Any]:
    target_dir = output_dir / "codex"
    target_dir.mkdir(exist_ok=True)
    name = probe["name"]
    last_message = target_dir / f"skill-{name}-last-message.txt"
    stdout_path = target_dir / f"skill-{name}.jsonl"
    stderr_path = target_dir / f"skill-{name}-stderr.txt"
    result = _run(
        [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--cd",
            str(workspace),
            "--output-last-message",
            str(last_message),
            probe["prompt"],
        ],
        env=env,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    message = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    return {
        "name": name,
        "returncode": result.returncode,
        "passed": result.returncode == 0 and probe["marker"] in message,
        "marker": probe["marker"],
        "last_message": message.strip(),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "thread_links": _codex_thread_links(result.stdout),
    }


def _run_codex_agent(
    workspace: Path,
    output_dir: Path,
    env: dict[str, str],
    probe: dict[str, str],
) -> dict[str, Any]:
    target_dir = output_dir / "codex"
    target_dir.mkdir(exist_ok=True)
    name = probe["name"]
    last_message = target_dir / f"agent-{name}-last-message.txt"
    stdout_path = target_dir / f"agent-{name}.jsonl"
    stderr_path = target_dir / f"agent-{name}-stderr.txt"
    result = _run(
        [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--cd",
            str(workspace),
            "--enable",
            "multi_agent",
            "--output-last-message",
            str(last_message),
            probe["prompt"],
        ],
        env=env,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    message = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    return {
        "name": name,
        "returncode": result.returncode,
        "passed": result.returncode == 0 and probe["marker"] in message,
        "marker": probe["marker"],
        "last_message": message.strip(),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "thread_links": _codex_thread_links(result.stdout),
    }


def _run_claude_skill(workspace: Path, output_dir: Path, probe: dict[str, str]) -> dict[str, Any]:
    target_dir = output_dir / "claude"
    target_dir.mkdir(exist_ok=True)
    name = probe["name"]
    stdout_path = target_dir / f"skill-{name}.jsonl"
    stderr_path = target_dir / f"skill-{name}-stderr.txt"
    result = _run(
        [
            "claude",
            "-p",
            "--verbose",
            "--setting-sources",
            "project",
            "--output-format",
            "stream-json",
            "--include-hook-events",
            probe["prompt"],
        ],
        cwd=workspace,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    return {
        "name": name,
        "returncode": result.returncode,
        "passed": result.returncode == 0 and probe["marker"] in result.stdout,
        "marker": probe["marker"],
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }


def _run_claude_agent(workspace: Path, output_dir: Path, probe: dict[str, str]) -> dict[str, Any]:
    target_dir = output_dir / "claude"
    target_dir.mkdir(exist_ok=True)
    name = probe["name"]
    stdout_path = target_dir / f"agent-{name}.jsonl"
    stderr_path = target_dir / f"agent-{name}-stderr.txt"
    result = _run(
        [
            "claude",
            "-p",
            "--verbose",
            "--setting-sources",
            "project",
            "--agent",
            name,
            "--output-format",
            "stream-json",
            "--include-hook-events",
            probe["prompt"],
        ],
        cwd=workspace,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    return {
        "name": name,
        "returncode": result.returncode,
        "passed": result.returncode == 0 and probe["marker"] in result.stdout,
        "marker": probe["marker"],
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }


def _versions() -> dict[str, Any]:
    versions: dict[str, Any] = {}
    for binary in ["codex", "claude"]:
        result = _run([binary, "--version"])
        versions[binary] = {
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    return versions


def _write_summary(output_dir: Path, result: dict[str, Any]) -> None:
    rel_output_dir = output_dir.resolve().relative_to(REPO_ROOT)
    lines = [
        "# Harness Creation Runtime Evidence",
        "",
        f"- profile: `{PROFILE}`",
        f"- evidence_dir: `{rel_output_dir.as_posix()}`",
        f"- codex_version: `{result['versions']['codex']['stdout']}`",
        f"- claude_version: `{result['versions']['claude']['stdout']}`",
        f"- passed: `{result['passed']}`",
        "",
        "## Codex",
    ]
    for group in ["skills", "agents"]:
        for item in result.get("codex", {}).get(group, []):
            links = ", ".join(item.get("thread_links", [])) or "none"
            lines.append(
                f"- {group[:-1]} `{item['name']}`: passed=`{item['passed']}`, "
                f"marker=`{item['marker']}`, threads={links}"
            )
    lines.append("")
    lines.append("## Claude")
    for group in ["skills", "agents"]:
        for item in result.get("claude", {}).get(group, []):
            lines.append(
                f"- {group[:-1]} `{item['name']}`: passed=`{item['passed']}`, "
                f"marker=`{item['marker']}`"
            )
    lines.append("")
    lines.append("## Non-Goals")
    for item in _contract()["non_goals"]:
        lines.append(f"- {item}")
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_latest(output_dir: Path, result: dict[str, Any]) -> None:
    rel_output_dir = output_dir.resolve().relative_to(REPO_ROOT)
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    latest = {
        "evidence_dir": rel_output_dir.as_posix(),
        "result_json": (rel_output_dir / "result.json").as_posix(),
        "summary_md": (rel_output_dir / "summary.md").as_posix(),
        "passed": result["passed"],
        "codex_thread_links": [
            link
            for group in result.get("codex", {}).values()
            for item in group
            for link in item.get("thread_links", [])
        ],
    }
    (EVIDENCE_ROOT / "latest.json").write_text(
        json.dumps(latest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _write_functional_summary(output_dir: Path, result: dict[str, Any]) -> None:
    functional_codex = result["functional_codex"]
    functional_claude = result["functional_claude"]
    lines = [
        "# Harness Creation Functional E2E Evidence",
        "",
        f"- profile: `{PROFILE}`",
        f"- proof_type: `{result.get('proof_type', 'functional_e2e')}`",
        f"- mode: `functional-e2e`",
        "- proof_boundary: `loading-dispatch` only proves installed runtime surface loading; `functional_e2e` proves staged artifact creation and runtime checks.",
        f"- evidence_dir: `{_display_path(output_dir)}`",
        f"- workspace: `{_display_path(Path(result['workspace']))}`",
        f"- workspace_archive: `{_display_path(Path(result['workspace_archive']))}`",
        f"- workspace_persisted_as: `{result.get('workspace_persisted_as', 'directory')}`",
        f"- passed: `{result['passed']}`",
        f"- codex_model: `{functional_codex.get('model', 'skipped')}`",
        f"- claude_model: `{functional_claude.get('model', 'skipped')}`",
        f"- codex_effort: `{functional_codex.get('effort', 'skipped')}`",
        f"- claude_effort: `{functional_claude.get('effort', 'skipped')}`",
        f"- max_budget_usd: `{result.get('max_budget_usd', 'skipped')}`",
        f"- timeout_seconds: `{result.get('timeout_seconds', 'skipped')}`",
        f"- native_budget_flag: `{functional_codex.get('native_budget_flag', False)}`",
        f"- budget_guard: `{functional_codex.get('budget_guard', '')}`",
        f"- live_calls_used: `{functional_codex.get('live_calls_used', 0)}`",
        f"- max_live_calls: `{functional_codex.get('max_live_calls', FUNCTIONAL_CODEX_MAX_LIVE_CALLS)}`",
        "",
        "## Functional Codex",
        f"- skipped: `{functional_codex['skipped']}`",
        f"- passed: `{functional_codex['passed']}`",
    ]
    for command in functional_codex.get("commands", []):
        lines.append(
            f"- command `{command['name']}`: returncode=`{command['returncode']}`"
        )
    lines.append("")
    lines.append("## Functional Claude")
    lines.append(f"- skipped: `{functional_claude['skipped']}`")
    lines.append(f"- passed: `{functional_claude['passed']}`")
    for command in functional_claude.get("commands", []):
        lines.append(
            f"- command `{command['name']}`: returncode=`{command['returncode']}`"
        )
    lines.append("")
    lines.append("## Files")
    for path in functional_codex.get("file_paths", []):
        lines.append(f"- `{path}`")
    for path in functional_claude.get("file_paths", []):
        lines.append(f"- `{path}`")
    lines.append("")
    lines.append("## Threads")
    for link in functional_codex.get("thread_links", []):
        lines.append(f"- {link}")
    failures = (functional_codex.get("failures") or []) + (functional_claude.get("failures") or [])
    if failures:
        lines.append("")
        lines.append("## Failures")
        for failure in failures:
            lines.append(f"- {failure}")
    lines.append("")
    lines.append("## Non-Goals")
    for item in _contract()["non_goals"]:
        lines.append(f"- {item}")
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_functional_latest(output_dir: Path, result: dict[str, Any]) -> None:
    try:
        output_dir.resolve().relative_to(EVIDENCE_ROOT.resolve())
    except ValueError:
        return
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    latest = {
        "proof_type": "functional_e2e",
        "mode": "functional-e2e",
        "evidence_dir": _display_path(output_dir),
        "result_json": _display_path(output_dir / "result.json"),
        "summary_md": _display_path(output_dir / "summary.md"),
        "workspace_archive": _display_path(Path(result["workspace_archive"])),
        "passed": result["passed"],
        "targets": result.get("targets", []),
        "models": result.get("models", {}),
        "max_effort": result.get("max_effort"),
        "max_budget_usd": result.get("max_budget_usd"),
        "timeout_seconds": result.get("timeout_seconds"),
        "functional_codex": {
            "skipped": result["functional_codex"].get("skipped"),
            "passed": result["functional_codex"].get("passed"),
            "failures": result["functional_codex"].get("failures", []),
            "model": result["functional_codex"].get("model"),
            "effort": result["functional_codex"].get("effort"),
            "timeout_seconds": result["functional_codex"].get("timeout_seconds"),
            "max_budget_usd": result["functional_codex"].get("max_budget_usd"),
            "budget_guard": result["functional_codex"].get("budget_guard"),
        },
        "functional_claude": {
            "skipped": result["functional_claude"].get("skipped"),
            "passed": result["functional_claude"].get("passed"),
            "failures": result["functional_claude"].get("failures", []),
            "model": result["functional_claude"].get("model"),
            "effort": result["functional_claude"].get("effort"),
            "timeout_seconds": result["functional_claude"].get("timeout_seconds"),
            "max_budget_usd": result["functional_claude"].get("max_budget_usd"),
            "max_live_calls": result["functional_claude"].get("max_live_calls"),
            "live_calls_used": result["functional_claude"].get("live_calls_used"),
        },
        "codex_thread_links": result["functional_codex"].get("thread_links", []),
    }
    (EVIDENCE_ROOT / "latest.json").write_text(
        json.dumps(latest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_functional_e2e(args: argparse.Namespace) -> int:
    targets = set(args.target or ["codex"])
    output_dir = _prepare_output_dir(args.output_dir, overwrite=args.overwrite)
    workspace, plan_path = _materialize_functional_workspace(output_dir)
    versions = _versions()
    codex_home: Path | None = None

    result: dict[str, Any] = {
        "profile": PROFILE,
        "mode": "functional-e2e",
        "proof_type": "functional_e2e",
        "evidence_dir": str(output_dir),
        "workspace": str(workspace),
        "install_plan": str(plan_path),
        "versions": versions,
        "contract": _contract(),
        "targets": sorted(targets),
        "models": {
            "codex": args.codex_model if "codex" in targets else None,
            "claude": args.claude_model if "claude" in targets else None,
        },
        "max_effort": args.max_effort,
        "max_budget_usd": args.max_budget_usd,
        "timeout_seconds": args.timeout_seconds,
        "functional_codex": {"target": "codex", "skipped": True, "passed": True},
        "functional_claude": {"target": "claude", "skipped": True, "passed": True},
    }

    try:
        if "codex" in targets:
            codex_home, codex_env = _codex_home(workspace)
            result["codex_home_sanitized"] = {
                "path": str(codex_home),
                "auth": "symlinked-if-present-not-copied",
                "config": "trusted isolated functional E2E workspace",
            }
            result["functional_codex"] = _run_functional_codex(
                workspace=workspace,
                output_dir=output_dir,
                env=codex_env,
                codex_model=args.codex_model,
                max_effort=args.max_effort,
                max_budget_usd=args.max_budget_usd,
                timeout_seconds=args.timeout_seconds,
            )
        if "claude" in targets:
            result["functional_claude"] = _run_functional_claude(
                workspace=workspace,
                output_dir=output_dir,
                claude_model=args.claude_model,
                max_effort=args.max_effort,
                max_budget_usd=args.max_budget_usd,
                timeout_seconds=args.timeout_seconds,
            )

        requested_results = [
            result["functional_codex"]
            for target in ["codex"]
            if target in targets
        ] + [
            result["functional_claude"]
            for target in ["claude"]
            if target in targets
        ]
        result["passed"] = bool(requested_results) and all(item["passed"] for item in requested_results)
        result["pruned_runtime_noise"] = _prune_functional_workspace_runtime_noise(workspace)
        workspace_archive = _archive_functional_workspace(output_dir, workspace)
        result["workspace_archive"] = str(workspace_archive)
        result["workspace_persisted_as"] = "tar.gz"
        (output_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_functional_summary(output_dir, result)
        _write_functional_latest(output_dir, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1
    finally:
        if codex_home is not None:
            shutil.rmtree(codex_home, ignore_errors=True)


def run(args: argparse.Namespace) -> int:
    output_dir = _prepare_output_dir(args.output_dir, overwrite=args.overwrite)
    workspace, plan_path = _materialize_workspace(output_dir)
    versions = _versions()
    codex_home: Path | None = None

    result: dict[str, Any] = {
        "profile": PROFILE,
        "evidence_dir": str(output_dir),
        "workspace": str(workspace),
        "install_plan": str(plan_path),
        "versions": versions,
        "contract": _contract(),
        "codex": {"skills": [], "agents": []},
        "claude": {"skills": [], "agents": []},
    }

    try:
        targets = set(args.target)
        if "codex" in targets:
            codex_home, codex_env = _codex_home(workspace)
            result["codex_home_sanitized"] = {
                "path": str(codex_home),
                "auth": "symlinked-if-present-not-copied",
                "config": "trusted isolated probe workspace",
            }
            for probe in CODEX_SKILL_PROBES:
                result["codex"]["skills"].append(_run_codex_skill(workspace, output_dir, codex_env, probe))
            for probe in CODEX_AGENT_PROBES:
                result["codex"]["agents"].append(_run_codex_agent(workspace, output_dir, codex_env, probe))

        if "claude" in targets:
            for probe in CLAUDE_SKILL_PROBES:
                result["claude"]["skills"].append(_run_claude_skill(workspace, output_dir, probe))
            for probe in CLAUDE_AGENT_PROBES:
                result["claude"]["agents"].append(_run_claude_agent(workspace, output_dir, probe))

        all_results = [
            item
            for target_result in [result["codex"], result["claude"]]
            for group in target_result.values()
            for item in group
        ]
        result["passed"] = bool(all_results) and all(item["passed"] for item in all_results)
        (output_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_summary(output_dir, result)
        _write_latest(output_dir, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1
    finally:
        if codex_home is not None:
            shutil.rmtree(codex_home, ignore_errors=True)


def _validate_functional_e2e(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    targets = set(args.target or ["codex"])
    if "codex" in targets and not args.codex_model:
        parser.error("--mode functional-e2e requires explicit --codex-model")
    if "claude" in targets and not args.claude_model:
        parser.error("--mode functional-e2e requires explicit --claude-model")
    if args.max_effort not in {"low", "medium"}:
        parser.error("--mode functional-e2e requires --max-effort low|medium")
    if args.max_budget_usd is None:
        parser.error("--mode functional-e2e requires --max-budget-usd")
    if (
        not math.isfinite(args.max_budget_usd)
        or args.max_budget_usd <= 0
        or args.max_budget_usd > FUNCTIONAL_E2E_MAX_BUDGET_USD
    ):
        parser.error(
            f"--mode functional-e2e requires finite positive --max-budget-usd <= {FUNCTIONAL_E2E_MAX_BUDGET_USD:g}"
        )
    if args.timeout_seconds is None:
        parser.error("--mode functional-e2e requires --timeout-seconds")
    if (
        not math.isfinite(args.timeout_seconds)
        or args.timeout_seconds <= 0
        or not float(args.timeout_seconds).is_integer()
        or args.timeout_seconds > FUNCTIONAL_E2E_MAX_TIMEOUT_SECONDS
    ):
        parser.error(
            f"--mode functional-e2e requires finite positive integer --timeout-seconds <= {FUNCTIONAL_E2E_MAX_TIMEOUT_SECONDS}"
        )
    if "codex" in targets:
        codex_model = args.codex_model.lower()
        if codex_model != FUNCTIONAL_CODEX_MODEL_POLICY:
            parser.error("--mode functional-e2e requires --codex-model luna")
    if "claude" in targets:
        claude_model = args.claude_model.lower()
        if any(token in claude_model for token in ("opus", "sonnet")):
            parser.error("--mode functional-e2e requires cheap --claude-model haiku or containing haiku")
        if "haiku" not in claude_model:
            parser.error("--mode functional-e2e requires cheap --claude-model haiku or containing haiku")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe harness-creation runtime loading in isolated Codex and Claude workspaces."
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--list-probes", action="store_true")
    parser.add_argument("--mode", choices=MODES, default="loading-dispatch")
    parser.add_argument("--codex-model")
    parser.add_argument("--claude-model")
    parser.add_argument("--max-effort", default="medium")
    parser.add_argument("--max-budget-usd", type=float)
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument(
        "--target",
        action="append",
        choices=["codex", "claude"],
        default=[],
        help="Runtime target to probe. Defaults to both Codex and Claude.",
    )
    args = parser.parse_args(argv)
    if args.list_probes:
        print(json.dumps(_contract(), indent=2, sort_keys=True))
        return 0
    if not args.target:
        args.target = ["codex"] if args.mode == "functional-e2e" else ["codex", "claude"]
    if args.mode == "functional-e2e":
        _validate_functional_e2e(parser, args)
        args.timeout_seconds = int(args.timeout_seconds)
        return _run_functional_e2e(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
