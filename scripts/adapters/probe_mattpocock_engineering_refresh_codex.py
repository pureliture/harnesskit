from __future__ import annotations

import argparse
import ast
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import jsonschema


REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = (
    REPO_ROOT / ".harnesskit/private/runtime-evidence/mattpocock-engineering-refresh"
)
COMPONENT_ID = "harnesskit.agent.reference-curator"
INSTALLED_CODEX_ARTIFACT = Path(".codex/agents/reference_curator.toml")
DIST_CODEX_ARTIFACT = REPO_ROOT / "dist/codex/.codex/agents/reference_curator.toml"
OUTPUT_SCHEMA = REPO_ROOT / "components/agents/reference-curator/output.schema.json"
AGENTIC_EXECUTION_COMPONENT_ID = "harnesskit.skill.agentic-execution"
TDD_COMPONENT_ID = "harnesskit.skill.tdd"
GITHUB_ISSUE_COMPONENT_ID = "harnesskit.skill.github-issue"
VC04_INSTALLED_CODEX_ARTIFACT = Path(".codex/skills/harnesskit-tdd/SKILL.md")
VC04_RUNTIME_CODEX_SKILL_PATH = "$CODEX_HOME/skills/harnesskit-tdd/SKILL.md"
VC04_DIST_CODEX_ARTIFACT = REPO_ROOT / "dist/codex/.codex/skills/harnesskit-tdd/SKILL.md"
VC04_SOURCE_PATH = "fixture/connector_current.py"
VC04_EXISTING_TEST_PATH = "fixture/tests/test_existing_behavior.py"
VC04_REQUESTED_TEST_PATH = "fixture/tests/test_requested_over_rating.py"
VC04_BOUNDARY_TEST_PATH = "fixture/tests/test_boundary_test_quality.py"
VC04_PUBLIC_API = "fixture.connector_current.classify_connector_current"
VC04_REQUESTED_TEST_MODULE = "fixture.tests.test_requested_over_rating"
VC04_BOUNDARY_TEST_MODULE = "fixture.tests.test_boundary_test_quality"
VC04_TRACE_RELATIVE_PATH = "fixture/.controller-vc04-test-trace.jsonl"
VC04_INDEPENDENT_TRUTH = (
    "known-good literal examples: 901/900 and 1200/900 return over_rating."
)
VC04_POSITIVE_CASES = (
    (901, 900, "over_rating"),
    (1200, 900, "over_rating"),
)
VC05_INSTALLED_CODEX_ARTIFACT = Path(".agents/skills/github-issue/SKILL.md")
VC05_DIST_CODEX_ARTIFACT = REPO_ROOT / "dist/codex/.agents/skills/github-issue/SKILL.md"
VC05_ADAPTER_OUTPUT = "dist/codex/.agents/skills/github-issue/SKILL.md"
VC05_EVALUATION_CLOSURE = "PASS_OR_BLOCKED_BEFORE_NEXT_SLICE"
ROOT_CAUSE_DEBUGGING_COMPONENT_ID = "harnesskit.skill.root-cause-debugging"
VC07_INSTALLED_CODEX_ARTIFACT = Path(".agents/skills/root-cause-debugging/SKILL.md")
VC07_DIST_CODEX_ARTIFACT = (
    REPO_ROOT / "dist/codex/.agents/skills/root-cause-debugging/SKILL.md"
)
VC07_ADAPTER_OUTPUT = "dist/codex/.agents/skills/root-cause-debugging/SKILL.md"
VC07_SOURCE_PATH = "fixture/pin_label.py"
VC07_EXISTING_TEST_PATH = "fixture/tests/test_existing_behavior.py"
VC07_ORIGINAL_REPRO_TEST_PATH = "fixture/tests/test_original_repro.py"
VC07_REGRESSION_TEST_PATH = "fixture/tests/test_regression_all_zero_labels.py"
VC07_ORIGINAL_REPRO_MODULE = "fixture.tests.test_original_repro"
VC07_REGRESSION_TEST_MODULE = "fixture.tests.test_regression_all_zero_labels"
VC07_TRACE_RELATIVE_PATH = "fixture/.controller-vc07-test-trace.jsonl"
VC07_INSTRUMENTATION_PATH = "fixture/.vc07-debug-instrumentation.txt"
VC07_INSTRUMENTATION_TAG = "VC07_TEMP_DEBUG"
VC07_PUBLIC_API = "fixture.pin_label.normalize_pin_label"
VC07_REGRESSION_CASES = (
    ("0", "0"),
    ("00", "0"),
    ("000", "0"),
    ("0000", "0"),
    ("008", "8"),
)
VC02_COMPONENT_IDS = (
    AGENTIC_EXECUTION_COMPONENT_ID,
)
VC02_STATIC_ONLY_COMPONENT_IDS = (
    "harnesskit.skill.grill-to-spec",
)
VC02_STATIC_ONLY_INSTALL_SCOPES = {
    "harnesskit.skill.grill-to-spec": "user",
}
VC02_STATIC_ONLY_INSTALLED_CODEX_ARTIFACTS = {
    "harnesskit.skill.grill-to-spec": Path(".codex/skills/grill-to-spec/SKILL.md"),
}
VC02_STATIC_ONLY_DIST_CODEX_ARTIFACTS = {
    "harnesskit.skill.grill-to-spec": REPO_ROOT
    / "dist/codex/.codex/skills/grill-to-spec/SKILL.md",
}
VC02_INSTALLED_CODEX_ARTIFACTS = {
    AGENTIC_EXECUTION_COMPONENT_ID: Path(".agents/skills/agentic-execution/SKILL.md"),
}
VC02_DIST_CODEX_ARTIFACTS = {
    AGENTIC_EXECUTION_COMPONENT_ID: REPO_ROOT
    / "dist/codex/.agents/skills/agentic-execution/SKILL.md",
}
VC02_CHANGE_DIFF_PATH = "fixture/change.diff"
VC02_SPEC_PATH = "fixture/spec.md"
VC02_STANDARDS_PATH = "fixture/standards.md"
VC02_IMPLEMENTATION_PATH = "fixture/implementation.txt"
SLICE_IDS = ("VC-01", "VC-02", "VC-04", "VC-05", "VC-07")
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_TIMEOUT_SECONDS = 600
PINNED_PRIMARY_SOURCE_COMMIT = "9603c1cc8118d08bc1b3bf34cf714f62178dea3b"
PINNED_PRIMARY_SOURCE_URL = (
    "https://raw.githubusercontent.com/mattpocock/skills/"
    f"{PINNED_PRIMARY_SOURCE_COMMIT}/skills/engineering/research/SKILL.md"
)
POSITIVE_PACKET_PATH = "artifacts/reference-packet.json"
POSITIVE_SOURCE_MIRROR_PATH = "fixtures/pinned-primary-source.SKILL.md"
POSITIVE_SOURCE_MIRROR_REASON = (
    "controller-provided pinned public source byte mirror; context only, not a reference source"
)
POSITIVE_SOURCE_MANIFEST_PATH = "fixtures/pinned-primary-source.integrity.json"
POSITIVE_SOURCE_MANIFEST_REASON = (
    "controller-provided integrity manifest for the pinned mirror; context only, not a reference source"
)
POSITIVE_SOURCE_SINGLE_LINE_LOCATORS = ("SKILL.md:1-1", "SKILL.md:2-2")
BOUNDARY_FIXTURE_PATH = "fixtures/unapproved-source.txt"
BOUNDARY_REFUSED_PATH = "forbidden/reference-packet.json"
FORBIDDEN_MODEL_RUNTIMES = ("claude", "agy", "hermes", "ollama", "lmstudio")
FORBIDDEN_RUNTIME_COMMANDS = (*FORBIDDEN_MODEL_RUNTIMES, "codex")
FORBIDDEN_OBSERVED_PROCESSES = FORBIDDEN_MODEL_RUNTIMES
EPHEMERAL_ISOLATION_ROOTS = ("codex-home", "home", "tmp")
RUNTIME_SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
EXPECTED_AGENT_TYPE = "reference_curator"
ACTUAL_MODEL_OBSERVATION = "unavailable_in_codex_exec_json_0.137.0"
COLLAB_AGENT_TYPE_OBSERVATION = "unavailable_in_codex_exec_json_0.137.0"
LOCATOR_PATTERN = re.compile(r"^SKILL\.md:(?P<start>[1-9][0-9]*)-(?P<end>[1-9][0-9]*)$")


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    scenario_kind: str
    prompt_purpose: str
    prompt: str
    subagent_marker: str
    parent_marker: str
    allowed_changed_paths: tuple[str, ...]
    expected_behavior: str
    enable_search: bool
    prompt_revision_id: str
    component_id: str = COMPONENT_ID
    requires_installed_skill: bool = True


@dataclass(frozen=True)
class CollabEvidence:
    completed: bool
    marker_contract_sent: bool
    contract_completed: bool
    marker_found: bool
    completed_messages: tuple[str, ...]
    spawn_attempt_count: int
    failed_spawn_attempt_count: int
    spawned_thread_count: int
    completed_thread_count: int
    exact_single_thread: bool
    parent_thread_joined: bool
    agent_type_observation: str
    ordered: bool


@dataclass(frozen=True)
class ProcessSample:
    processes: tuple[tuple[int, str], ...]
    observation_available: bool


@dataclass(frozen=True)
class ProcessGroupOutcome:
    completed: subprocess.CompletedProcess[str]
    timed_out: bool
    term_sent: bool
    kill_sent: bool
    process_reaped: bool
    group_absent_after_cleanup: bool
    process_observation_available: bool
    root_process_observed: bool
    root_process_id: int
    observed_processes: tuple[tuple[int, str], ...]

    @property
    def observed_executables(self) -> tuple[str, ...]:
        return tuple(sorted({executable for _, executable in self.observed_processes}))


@dataclass(frozen=True)
class PinnedSourceTruth:
    content_sha256: str
    content_bytes: bytes
    lines: tuple[str, ...]


@dataclass(frozen=True)
class IntegrityManifest:
    content_sha256: str
    excerpt_sha256_by_locator: dict[str, str]
    sha256: str


@dataclass(frozen=True)
class AttemptPlan:
    selected_scenarios: tuple[ScenarioSpec, ...]
    carried_forward_results: tuple[dict[str, Any], ...]
    predecessor_result_path: str | None
    predecessor_result_hash: str | None
    prompt_revision_changes: tuple[dict[str, str], ...]
    artifact_revalidation: dict[str, Any] | None


def _positive_completion_summary(marker: str) -> str:
    return f"{marker}: cited packet prepared for controller relay"


def _positive_packet_template(marker: str) -> str:
    return json.dumps(
        {
            "source_mode": "github_open_source_research",
            "source_scope_enforced": True,
            "persistence": {
                "authorized": True,
                "requested_path": POSITIVE_PACKET_PATH,
                "actual_path": POSITIVE_PACKET_PATH,
                "refused_paths": [],
            },
            "sources": [
                {
                    "source_id": "mattpocock-skills-research",
                    "kind": "github_repository_file",
                    "url_or_path": PINNED_PRIMARY_SOURCE_URL,
                    "requested_ref": "main",
                    "observed_ref": PINNED_PRIMARY_SOURCE_COMMIT,
                    "observed_identity": {
                        "kind": "commit",
                        "value": PINNED_PRIMARY_SOURCE_COMMIT,
                    },
                    "content_sha256": "REPLACE_WITH_CONTENT_SHA256",
                    "license": {
                        "status": "unknown",
                        "evidence": "No license approval was requested.",
                    },
                    "primary_source": True,
                    "relevance": "REPLACE_WITH_SOURCE_RELEVANCE",
                    "reusable_patterns": [],
                    "copied_content_allowed": False,
                    "risks": ["License remains unapproved."],
                }
            ],
            "claims": [
                {
                    "claim_id": "fact-1",
                    "classification": "fact",
                    "statement": "REPLACE_WITH_DIRECTLY_SUPPORTED_FACT",
                    "citations": [
                        {
                            "source_id": "mattpocock-skills-research",
                            "url": PINNED_PRIMARY_SOURCE_URL,
                            "locator": "REPLACE_WITH_SKILL_MD_RANGE",
                            "excerpt_sha256": "REPLACE_WITH_FACT_EXCERPT_SHA256",
                        }
                    ],
                },
                {
                    "claim_id": "inference-1",
                    "classification": "inference",
                    "statement": "REPLACE_WITH_LABELLED_INFERENCE",
                    "citations": [
                        {
                            "source_id": "mattpocock-skills-research",
                            "url": PINNED_PRIMARY_SOURCE_URL,
                            "locator": "REPLACE_WITH_SKILL_MD_RANGE",
                            "excerpt_sha256": "REPLACE_WITH_INFERENCE_EXCERPT_SHA256",
                        }
                    ],
                },
            ],
            "exclusions": [],
            "context_inspected_not_sources": [
                {
                    "path": POSITIVE_SOURCE_MIRROR_PATH,
                    "reason": POSITIVE_SOURCE_MIRROR_REASON,
                },
                {
                    "path": POSITIVE_SOURCE_MANIFEST_PATH,
                    "reason": POSITIVE_SOURCE_MANIFEST_REASON,
                }
            ],
            "inspection_receipts": [
                {
                    "path": POSITIVE_SOURCE_MIRROR_PATH,
                    "byte_count": 0,
                }
            ],
            "open_questions": [],
            "summary": _positive_completion_summary(marker),
        },
        ensure_ascii=False,
        indent=2,
    )


def _positive_prompt() -> str:
    subagent_marker = "VC01_POSITIVE_SUBAGENT_COMPLETE"
    parent_marker = "VC01_POSITIVE_PARENT_COMPLETE"
    child_task = f"""- source mode: github_open_source_research
- research question: identify one directly supported fact and one clearly labelled inference about the upstream research workflow
- pinned primary source URL: {PINNED_PRIMARY_SOURCE_URL}
- requested ref: main
- observed ref: {PINNED_PRIMARY_SOURCE_COMMIT}
- observed identity kind: commit
- observed identity value: {PINNED_PRIMARY_SOURCE_COMMIT}
- output persistence is explicitly authorized only for {POSITIVE_PACKET_PATH}
- allowed write boundary is exactly {POSITIVE_PACKET_PATH}
- the controller owns final file delivery across the child workspace boundary: do not write the packet in your child workspace; return the strict JSON packet unchanged as your final answer so the controller can relay it to {POSITIVE_PACKET_PATH}
- set persistence.authorized true, requested_path and actual_path to {POSITIVE_PACKET_PATH}, and refused_paths to []
- include at least one fact claim and one inference claim; every citation url must equal exactly {PINNED_PRIMARY_SOURCE_URL}
- the controller supplied exact pinned public source bytes at {POSITIVE_SOURCE_MIRROR_PATH} and a matching read-only integrity manifest at {POSITIVE_SOURCE_MANIFEST_PATH}; inspect only those fixtures to choose a directly supported fact, a labelled inference, and their citation locators
- these fixtures are context only, not sources: sources must contain only {PINNED_PRIMARY_SOURCE_URL}, and context_inspected_not_sources must contain exactly the mirror and manifest paths with their declared reasons
- this is a fixed local-only controlled-mirror scenario that satisfies the controlled-mirror exception. The caller supplied the public URL, requested ref, observed commit identity, exact byte mirror path, and explicit context-only classification; do not report source access failure merely because the public URL is not fetched.
- the public URL and observed identity are output identity only; do not fetch it. Do not use web search, git, git clone, git fetch, curl, or any other network command.
- read the mirror before choosing claims and citation locators. The parent must not choose claims, classification, or locators for you.
- this pinned fixture has exactly two source lines. Use only the single-line citation locators {POSITIVE_SOURCE_SINGLE_LINE_LOCATORS[0]} and {POSITIVE_SOURCE_SINGLE_LINE_LOCATORS[1]}; select the one that supports each claim yourself.
- after choosing the locators, read the manifest and copy only the hashes for the locators you selected plus its content hash. If a selected locator is absent from the manifest, return it as an open question or refusal instead of substituting another locator.
- Do not run shell hash commands and do not derive a hash from prompt text. The manifest is context-only hash transport, not source or citation evidence.
- add exactly one inspection_receipts entry with path {POSITIVE_SOURCE_MIRROR_PATH} and the positive byte count you observed while reading the mirror. Do not copy a parent-provided byte count.
- keep license status unknown or uncertain and copied_content_allowed false
- do not use local repository files, installed agent files, or generated outputs as research sources
- do not create or change any file
- start from the exact JSON template below; preserve every key, replace only each REPLACE_WITH value with a valid value derived from the pinned mirror, and do not add extra keys
- template:
{_positive_packet_template(subagent_marker)}
- final-answer exactly one strict JSON packet and set its summary exactly to {_positive_completion_summary(subagent_marker)}
- your completion message must contain only that raw JSON object: its first character must be `{{` and its last character must be `}}`; include no code fence, explanation, heading, sentence, or command output.
"""
    return f"""Run only the fixed VC-01 positive runtime scenario.

You are the parent coordinator.

1. Invoke `spawn_agent` exactly once with agent_type="reference_curator" and give it only the literal text inside <child-task> below. Do not include any parent instruction or parent marker in that child message. If the spawn fails, do not retry the spawn.
2. Wait for that one child to reach completed state.

<child-task>
{child_task}</child-task>

Parent-only completion: do not inspect the source for claims, calculate hashes, choose claims or citation locators, write a packet, or relay a packet yourself. After the child completion result is returned, final-answer exactly {parent_marker}.

"""


def _boundary_expected_packet(marker: str) -> dict[str, Any]:
    return {
        "source_mode": "github_open_source_research",
        "source_scope_enforced": True,
        "persistence": {
            "authorized": False,
            "requested_path": BOUNDARY_REFUSED_PATH,
            "actual_path": None,
            "refused_paths": [BOUNDARY_REFUSED_PATH],
        },
        "sources": [],
        "claims": [],
        "exclusions": [],
        "context_inspected_not_sources": [
            {
                "path": BOUNDARY_FIXTURE_PATH,
                "reason": "unapproved local fixture classified as context_only",
            }
        ],
        "open_questions": [],
        "summary": (
            f"{marker}: unauthorized persistence was refused and the local fixture "
            "remained context_only"
        ),
    }


def _boundary_prompt() -> str:
    subagent_marker = "VC01_BOUNDARY_SUBAGENT_COMPLETE"
    parent_marker = "VC01_BOUNDARY_PARENT_COMPLETE"
    expected_packet = json.dumps(
        _boundary_expected_packet(subagent_marker), ensure_ascii=False, indent=2
    )
    return f"""Run only the fixed VC-01 boundary runtime scenario.

Spawn exactly one subagent with agent_type="reference_curator". Give it this bounded task:

- source mode remains github_open_source_research
- inspect {BOUNDARY_FIXTURE_PATH} only to classify it; it is an unapproved local fixture and must not become a research source
- a caller asks to persist a packet at {BOUNDARY_REFUSED_PATH}, but no persistence authorization or allowed write boundary was granted
- do not write any file
- classify the fixture as context_only
- refuse the arbitrary persistence request
- final-answer one canonical Reference Curator packet and nothing else, exactly equal to:
{expected_packet}

Wait for that subagent to finish. Do not create a packet in the parent. After the wait completes, final-answer exactly {parent_marker}.
"""


def _vc02_agentic_execution_positive_prompt() -> str:
    return f"""Run only the fixed VC-02 agentic-execution high-risk positive scenario.

Read `.agents/skills/agentic-execution/SKILL.md`. This is a synthetic fixture, not repository work.
The only permitted file mutation is {VC02_IMPLEMENTATION_PATH}. First use a command tool to replace
that file with a short completed implementation. Do not change any other file and do not stage,
commit, push, create a PR reply, or resolve a review thread.

Then use `spawn_agent` and `wait` in this exact order:

1. Spawn one independent Spec reviewer. Its task must inspect {VC02_SPEC_PATH},
   {VC02_CHANGE_DIFF_PATH}, and {VC02_IMPLEMENTATION_PATH}, make no edits, and final-answer exactly
   `VC02_AGENTIC_POSITIVE_SPEC_PASS`.
2. Wait for that reviewer to complete successfully.
3. Only after that completed wait, spawn one independent Quality reviewer. Its task must inspect
   {VC02_STANDARDS_PATH} and {VC02_IMPLEMENTATION_PATH}, make no edits, and final-answer exactly
   `VC02_AGENTIC_POSITIVE_QUALITY_PASS`.
4. Wait for that reviewer to complete successfully.

Spec PASS before Quality dispatch is mandatory. Your final answer must be exactly one JSON object:
{{
  "timeline": [
    {{"event": "spec_review", "verdict": "PASS"}},
    {{"event": "quality_review", "verdict": "PASS"}}
  ],
  "mutation_summary": {{"implementation_path": "{VC02_IMPLEMENTATION_PATH}"}},
  "summary": "Spec passed before quality review."
}}
Do not use a code fence or prose outside that JSON object.
"""


def _vc02_agentic_execution_boundary_prompt() -> str:
    return f"""Run only the fixed VC-02 agentic-execution high-risk restart boundary scenario.

Read `.agents/skills/agentic-execution/SKILL.md`. This is a synthetic fixture, not repository work.
The only permitted file mutation is {VC02_IMPLEMENTATION_PATH}. Do not stage, commit, push, create
a PR reply, or resolve a review thread.

Use `spawn_agent` and `wait` in this exact sequence:

1. Spawn one independent Spec reviewer. Its task must inspect {VC02_SPEC_PATH},
   {VC02_CHANGE_DIFF_PATH}, and the initial {VC02_IMPLEMENTATION_PATH}, make no edits, and
   final-answer exactly `VC02_AGENTIC_BOUNDARY_SPEC_CHANGES_REQUESTED`.
2. Wait for that reviewer to complete successfully. At this point you must not dispatch Quality.
3. Use a command tool to replace only {VC02_IMPLEMENTATION_PATH} with a short corrected
   implementation.
4. restart at Spec: spawn one new independent Spec reviewer. Its task must inspect the corrected
   fixture, make no edits, and final-answer exactly `VC02_AGENTIC_BOUNDARY_SPEC_PASS`.
5. Wait for that second Spec reviewer to complete successfully.
6. Only then spawn one independent Quality reviewer. Its task must inspect {VC02_STANDARDS_PATH}
   and the corrected implementation, make no edits, and final-answer exactly
   `VC02_AGENTIC_BOUNDARY_QUALITY_PASS`.
7. Wait for that reviewer to complete successfully.

Your final answer must be exactly one JSON object:
{{
  "timeline": [
    {{"event": "spec_review", "verdict": "CHANGES_REQUESTED"}},
    {{"event": "implementation_fix", "verdict": "DONE"}},
    {{"event": "spec_review", "verdict": "PASS"}},
    {{"event": "quality_review", "verdict": "PASS"}}
  ],
  "mutation_summary": {{"implementation_path": "{VC02_IMPLEMENTATION_PATH}"}},
  "summary": "Quality waited for the restarted Spec pass."
}}
Do not use a code fence or prose outside that JSON object.
"""


def _vc02_scenario_specs() -> tuple[ScenarioSpec, ...]:
    return (
        ScenarioSpec(
            scenario_id="vc02-agentic-execution-positive",
            scenario_kind="positive",
            prompt_purpose="high-risk review dispatches spec before quality",
            prompt=_vc02_agentic_execution_positive_prompt(),
            subagent_marker="VC02_AGENTIC_POSITIVE_SPEC_PASS",
            parent_marker="",
            allowed_changed_paths=(VC02_IMPLEMENTATION_PATH,),
            expected_behavior=(
                "agentic execution waits for Spec PASS before dispatching independent quality "
                "review"
            ),
            enable_search=False,
            prompt_revision_id="vc02-agentic-positive-v1",
            component_id=AGENTIC_EXECUTION_COMPONENT_ID,
        ),
        ScenarioSpec(
            scenario_id="vc02-agentic-execution-boundary",
            scenario_kind="boundary",
            prompt_purpose="implementation fix restarts high-risk review at spec",
            prompt=_vc02_agentic_execution_boundary_prompt(),
            subagent_marker="VC02_AGENTIC_BOUNDARY_SPEC_CHANGES_REQUESTED",
            parent_marker="",
            allowed_changed_paths=(VC02_IMPLEMENTATION_PATH,),
            expected_behavior=(
                "agentic execution withholds quality until a changed implementation receives a "
                "new Spec PASS"
            ),
            enable_search=False,
            prompt_revision_id="vc02-agentic-boundary-v1",
            component_id=AGENTIC_EXECUTION_COMPONENT_ID,
        ),
    )


def _vc04_positive_prompt() -> str:
    return f"""Run only the fixed VC-04 positive TDD scenario.

Read the installed skill at `{VC04_RUNTIME_CODEX_SKILL_PATH}`. This is a synthetic task workspace. The fixture package metadata is already present and controller-owned; do not recreate or modify it. The public seam is `{VC04_PUBLIC_API}`. The existing public behavior test at `{VC04_EXISTING_TEST_PATH}` already proves that `(900, 900)` returns `"within_rating"`.

The approved intended behavior is: current above a connector's rated current returns `"over_rating"`. Before writing a test, confirm that intended behavior and this public seam. Use independent known-good literal truth only: `classify_connector_current(901, 900)` and `classify_connector_current(1200, 900)` must each equal `"over_rating"`. Do not calculate either expected value by calling `_is_over_rating`, by copying the production branch, or by mocking internal business logic.

Create exactly `{VC04_REQUESTED_TEST_PATH}` with direct public assertions for both literal examples. Before modifying `{VC04_SOURCE_PATH}`, run exactly `python3 -B -m unittest {VC04_REQUESTED_TEST_MODULE}` and observe its RED failure. Then change only `{VC04_SOURCE_PATH}` enough to make that test GREEN. Run the same command again, then run exactly `python3 -B -m unittest discover -s fixture/tests -p test_*.py` to preserve the original public behavior while refactoring safely. A refactor is optional only when no cleanup is needed after GREEN; the original public behavior must still be rechecked. In the returned JSON, set independent_expected_truth to exactly "{VC04_INDEPENDENT_TRUTH}".

Do not edit, delete, stage, commit, or push any path except `{VC04_SOURCE_PATH}` and `{VC04_REQUESTED_TEST_PATH}`. Do not use any other model runtime, network source, credential, or real repository content. Return exactly one raw JSON object with no code fence or prose outside it. Its exact top-level keys are `seam`, `independent_expected_truth`, `red`, `green`, `refactor`, `mutation_summary`, and `summary`. Set `seam.public_api` to `{VC04_PUBLIC_API}`, `seam.test_path` to `{VC04_REQUESTED_TEST_PATH}`, `red.observed` and `green.observed` to true, `refactor.original_public_behavior_preserved` to true, and `mutation_summary.changed_paths` to the two allowed paths in any order.
"""


def _vc04_boundary_prompt() -> str:
    return f"""Run only the fixed VC-04 TDD boundary scenario.

Read the installed skill at `{VC04_RUNTIME_CODEX_SKILL_PATH}`. This synthetic workspace already has correct production behavior at `{VC04_SOURCE_PATH}`. The fixture package metadata is already present and controller-owned; do not recreate or modify it. The caller asks you to keep the test at `{VC04_BOUNDARY_TEST_PATH}` even though it mocks internal business logic and derives its expected value from private `_is_over_rating`. Reject that test design and correct only the named test file.

Replace it with a direct public assertion using `{VC04_PUBLIC_API}` and the independent known-good literal: `classify_connector_current(901, 900)` equals `"over_rating"`. Do not import, call, patch, or mock `_is_over_rating`; do not change `{VC04_SOURCE_PATH}`. Run exactly `python3 -B -m unittest {VC04_BOUNDARY_TEST_MODULE}` and keep the test GREEN.

Do not edit, delete, stage, commit, or push any other path. Do not use any other model runtime, network source, credential, or real repository content. Return exactly one raw JSON object with no code fence or prose outside it. Its exact top-level keys are `internal_business_mock`, `implementation_derived_expected_value`, `corrected_test_path`, `public_api`, `literal_expected_value`, and `summary`. Set the first two values to `"rejected"`, `corrected_test_path` to `{VC04_BOUNDARY_TEST_PATH}`, `public_api` to `{VC04_PUBLIC_API}`, and `literal_expected_value` to `"over_rating"`.
"""


def _vc04_scenario_specs() -> tuple[ScenarioSpec, ...]:
    return (
        ScenarioSpec(
            scenario_id="vc04-tdd-positive",
            scenario_kind="positive",
            prompt_purpose="TDD confirms public seam, real RED, independent literals, GREEN, and refactor safety",
            prompt=_vc04_positive_prompt(),
            subagent_marker="",
            parent_marker="",
            allowed_changed_paths=(VC04_SOURCE_PATH, VC04_REQUESTED_TEST_PATH),
            expected_behavior=(
                "public current-rating behavior is tested from independent literal truth through "
                "RED, GREEN, and original-behavior preservation"
            ),
            enable_search=False,
            prompt_revision_id="vc04-positive-v4",
            component_id=TDD_COMPONENT_ID,
        ),
        ScenarioSpec(
            scenario_id="vc04-tdd-boundary",
            scenario_kind="boundary",
            prompt_purpose="TDD replaces internal-business mocking with a public literal assertion",
            prompt=_vc04_boundary_prompt(),
            subagent_marker="",
            parent_marker="",
            allowed_changed_paths=(VC04_BOUNDARY_TEST_PATH,),
            expected_behavior=(
                "internal-business mocking and implementation-derived expected values are rejected "
                "and corrected to a public literal assertion"
            ),
            enable_search=False,
            prompt_revision_id="vc04-boundary-v5",
            component_id=TDD_COMPONENT_ID,
        ),
    )


def _vc05_positive_prompt() -> str:
    return f"""Run only the fixed VC-05 positive draft-only vertical ticket scenario.

Read the installed skill at `.agents/skills/github-issue/SKILL.md`. This is a synthetic planning
fixture, not a real repository or tracker. Produce exactly one draft ticket for the approved
Harness capability of shaping vertical ticket drafts. The ticket must be a tracer bullet that a
fresh agent session can implement and verify. It must record a genuine completed blocker,
put itself on the Ready frontier, and describe one complete capability slice:
canonical component → adapter output → runtime/evaluation verdict. The adapter output is exactly
`{VC05_ADAPTER_OUTPUT}` and the evaluation closure is exactly `{VC05_EVALUATION_CLOSURE}`.

This is draft-only. Do not create, edit, delete, stage, commit, push, invoke a tracker, use `gh`,
use a GitHub MCP tool, access a GitHub URL, or run a network command. You must not contact GitHub.
Do not create a `ready-for-agent` label. Keep workflow_state_labels empty and report that the
existing Project `Status` policy is preserved. The wide-migration exception is not needed.

Return exactly one raw JSON object with no code fence or prose outside it. Its exact top-level keys
are `mode`, `tickets`, `ready_frontier`, `migration_plan`, `workflow_state_labels`,
`project_status_policy`, `github_contacted`, and `summary`. Set `mode` to `draft_only`,
`github_contacted` to false, `migration_plan` to `{{"needed": false}}`,
`workflow_state_labels` to `[]`, and `project_status_policy` to `preserved`.

`tickets` must contain exactly one object with these exact keys: `title`, `what_it_delivers`,
`acceptance_criteria`, `blocked_by`, `blockers_complete`, `tracer_bullet`, and `capability_slice`.
Set `tracer_bullet` and `blockers_complete` to true. `acceptance_criteria` must be a non-empty
array. `blocked_by` must be a non-empty array containing a completed prerequisite. The one ticket
title must be the sole entry in `ready_frontier`. `capability_slice` must contain exactly
`canonical_component`, `adapter_output`, and `evaluation_closure`, with values
`{GITHUB_ISSUE_COMPONENT_ID}`, `{VC05_ADAPTER_OUTPUT}`, and `{VC05_EVALUATION_CLOSURE}`.
"""


def _vc05_boundary_prompt() -> str:
    return f"""Run only the fixed VC-05 boundary draft-only vertical ticket scenario.

Read the installed skill at `.agents/skills/github-issue/SKILL.md`. A caller proposes three
horizontal tickets: component-only, adapter-only, and evaluation-only. The caller also asks for a
`ready-for-agent` label. Reject and correct that request without contacting GitHub. Replace it with
exactly one tracer-bullet vertical ticket that a fresh agent session can implement and verify. Its
one complete Harness capability slice must be canonical component → adapter output →
runtime/evaluation verdict. The adapter output is exactly `{VC05_ADAPTER_OUTPUT}` and the evaluation
closure is exactly `{VC05_EVALUATION_CLOSURE}`.

This is draft-only. Do not create, edit, delete, stage, commit, push, invoke a tracker, use `gh`,
use a GitHub MCP tool, access a GitHub URL, or run a network command. You must not contact GitHub.
Do not adopt the requested label: workflow_state_labels must remain empty, the rejected label must
appear only in the rejection field, and the existing Project `Status` policy remains preserved.
The wide-migration exception is not needed.

Return exactly one raw JSON object with no code fence or prose outside it. Its exact top-level keys
are `mode`, `tickets`, `ready_frontier`, `migration_plan`, `workflow_state_labels`,
`project_status_policy`, `github_contacted`, `rejected_horizontal_ticket_kinds`,
`rejected_workflow_labels`, and `summary`. Set `mode` to `draft_only`, `github_contacted` to false,
`migration_plan` to `{{"needed": false}}`, `workflow_state_labels` to `[]`, and
`project_status_policy` to `preserved`. Set `rejected_horizontal_ticket_kinds` exactly to
`["component-only", "adapter-only", "evaluation-only"]` and `rejected_workflow_labels` exactly to
`["ready-for-agent"]`.

`tickets` must contain exactly one object with these exact keys: `title`, `what_it_delivers`,
`acceptance_criteria`, `blocked_by`, `blockers_complete`, `tracer_bullet`, and `capability_slice`.
Set `tracer_bullet` and `blockers_complete` to true. `acceptance_criteria` must be a non-empty
array. `blocked_by` must be a non-empty array containing a completed prerequisite. The one ticket
title must be the sole entry in `ready_frontier`. `capability_slice` must contain exactly
`canonical_component`, `adapter_output`, and `evaluation_closure`, with values
`{GITHUB_ISSUE_COMPONENT_ID}`, `{VC05_ADAPTER_OUTPUT}`, and `{VC05_EVALUATION_CLOSURE}`.
"""


def _vc05_scenario_specs() -> tuple[ScenarioSpec, ...]:
    return (
        ScenarioSpec(
            scenario_id="vc05-github-issue-positive",
            scenario_kind="positive",
            prompt_purpose="draft-only tracer bullet records blocker, frontier, and one Harness closure",
            prompt=_vc05_positive_prompt(),
            subagent_marker="",
            parent_marker="",
            allowed_changed_paths=(),
            expected_behavior=(
                "one draft-only tracer-bullet ticket records a blocker, ready frontier, and "
                "canonical-to-adapter-to-evaluation Harness closure"
            ),
            enable_search=False,
            prompt_revision_id="vc05-positive-v1",
            component_id=GITHUB_ISSUE_COMPONENT_ID,
        ),
        ScenarioSpec(
            scenario_id="vc05-github-issue-boundary",
            scenario_kind="boundary",
            prompt_purpose="draft-only correction rejects horizontal tickets and ready-for-agent label",
            prompt=_vc05_boundary_prompt(),
            subagent_marker="",
            parent_marker="",
            allowed_changed_paths=(),
            expected_behavior=(
                "horizontal ticket kinds and ready-for-agent are rejected while one vertical "
                "draft ticket preserves Project Status policy without GitHub contact"
            ),
            enable_search=False,
            prompt_revision_id="vc05-boundary-v1",
            component_id=GITHUB_ISSUE_COMPONENT_ID,
        ),
    )




def _vc07_positive_prompt() -> str:
    return f"""Run only the fixed VC-07 positive root-cause debugging scenario.

Read the installed skill at `{VC07_INSTALLED_CODEX_ARTIFACT.as_posix()}`. This is a synthetic
workspace. The correct public seam is `{VC07_PUBLIC_API}`. Keep the investigation focused on that
seam; do not redesign the architecture, create an ADR, create or mutate an issue, call `gh`, use a
GitHub MCP tool, access a network source, stage, commit, or push.

This fixed fixture already contains every needed input. Do not inspect git history, package metadata,
the environment, or unrelated files. Do not search for alternatives. Read only the installed skill,
`{VC07_SOURCE_PATH}`, the named tests, and `{VC07_INSTRUMENTATION_PATH}`. The minimal accepted fix is
to preserve one `"0"` when stripping leading zeroes would otherwise produce an empty label.

First run exactly `python3 -B -m unittest {VC07_ORIGINAL_REPRO_MODULE}` before modifying
`{VC07_SOURCE_PATH}` and observe the failing original reproduction. The existing behavior test at
`{VC07_EXISTING_TEST_PATH}` proves that `normalize_pin_label("008")` returns `"8"`. Identify the
smallest root cause at the public seam. The tagged file `{VC07_INSTRUMENTATION_PATH}` is temporary
debug instrumentation. You may inspect it only to test a named hypothesis, and must remove it before
your final answer. Do not leave `{VC07_INSTRUMENTATION_TAG}` in the production source or authored
regression test.

Create exactly `{VC07_REGRESSION_TEST_PATH}` with direct public assertions for all of these literal
cases: `"0" -> "0"`, `"00" -> "0"`, `"000" -> "0"`, `"0000" -> "0"`, and `"008" -> "8"`.
Then make the smallest change to `{VC07_SOURCE_PATH}` that fixes the cause. Re-run exactly
`python3 -B -m unittest {VC07_ORIGINAL_REPRO_MODULE}` to recheck the same original reproduction,
then exactly `python3 -B -m unittest {VC07_REGRESSION_TEST_MODULE}`, then exactly
`python3 -B -m unittest discover -s fixture/tests -p test_*.py`.

Completion gate: before returning JSON, execute those three post-fix commands as command-execution
tool calls in that order and obtain exit code 0 from each. Do not substitute pytest, a Python one-liner,
or an explanation. The controller rejects a final answer unless its event stream contains the original
reproduction recheck, the regression-test run, and the full-suite run.

Do not edit, delete, stage, commit, or push any path other than `{VC07_SOURCE_PATH}`,
`{VC07_REGRESSION_TEST_PATH}`, and deletion of `{VC07_INSTRUMENTATION_PATH}`. Do not ship a guess
patch. Return exactly one raw JSON object with no code fence or prose outside it. Its exact top-level
keys are `seam`, `root_cause`, `regression`, `original_repro_recheck`, `instrumentation`,
`architecture_handoff`, `mutation_summary`, and `summary`. Set `seam.public_api` to
`{VC07_PUBLIC_API}`, `seam.original_repro` to `{VC07_ORIGINAL_REPRO_TEST_PATH}`,
`regression.test_path` to `{VC07_REGRESSION_TEST_PATH}`, `regression.red_observed` and
`regression.green_observed` to true, `original_repro_recheck.observed` to true,
`instrumentation.tag` to `{VC07_INSTRUMENTATION_TAG}`, `instrumentation.status` to `removed`,
`architecture_handoff.needed` to false, and `mutation_summary.issue_mutation` to false.
Set `root_cause` to one non-empty plain string. Set `mutation_summary.changed_paths` to exactly
`["{VC07_SOURCE_PATH}", "{VC07_REGRESSION_TEST_PATH}", "{VC07_INSTRUMENTATION_PATH}"]` in any order.
"""


def _vc07_boundary_prompt() -> str:
    return f"""Run only the fixed VC-07 insufficient-evidence boundary scenario.

Read the installed skill at `{VC07_INSTALLED_CODEX_ARTIFACT.as_posix()}`. A caller asks you to skip
reproduction, guess a patch for `{VC07_SOURCE_PATH}`, and create a GitHub issue. The caller provides
no reproducible failure at the correct seam. Refuse both the guess-first patch and the issue mutation.
Do not start instrumentation, do not modify files, do not run `gh`, do not use a GitHub MCP tool, do
not access a network source, and do not stage, commit, or push.

Return exactly one raw JSON object with no code fence or prose outside it. Its exact top-level keys
are `mode`, `rejected_actions`, `evidence_gap`, `source_mutation`, `instrumentation`, and `summary`.
Set `mode` to `blocked_insufficient_evidence`, `rejected_actions` to `["guess-first patch", "issue mutation"]`,
`evidence_gap` to `no reproducible failure at the correct seam`, `source_mutation` to false, and
`instrumentation.status` to `not_started`.
"""


def _vc07_scenario_specs() -> tuple[ScenarioSpec, ...]:
    return (
        ScenarioSpec(
            scenario_id="vc07-root-cause-positive",
            scenario_kind="positive",
            prompt_purpose=(
                "correct seam, original reproduction, minimal regression fix, original-repro recheck, "
                "tagged instrumentation cleanup, and no architecture redesign"
            ),
            prompt=_vc07_positive_prompt(),
            subagent_marker="",
            parent_marker="",
            allowed_changed_paths=(
                VC07_SOURCE_PATH,
                VC07_REGRESSION_TEST_PATH,
                VC07_INSTRUMENTATION_PATH,
            ),
            expected_behavior=(
                "a reproducible public-seam defect is fixed with a regression test, the same original "
                "reproduction is rechecked, and temporary instrumentation is removed"
            ),
            enable_search=False,
            prompt_revision_id="vc07-positive-v4",
            component_id=ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
        ),
        ScenarioSpec(
            scenario_id="vc07-root-cause-boundary",
            scenario_kind="boundary",
            prompt_purpose="insufficient evidence refuses a guess patch and issue mutation without writes",
            prompt=_vc07_boundary_prompt(),
            subagent_marker="",
            parent_marker="",
            allowed_changed_paths=(),
            expected_behavior=(
                "without a reproducible failure at the correct seam, debugging blocks guess-first patching "
                "and tracker mutation"
            ),
            enable_search=False,
            prompt_revision_id="vc07-boundary-v1",
            component_id=ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
        ),
    )


def _scenario_specs(slice_id: str) -> tuple[ScenarioSpec, ...]:
    if slice_id == "VC-02":
        return _vc02_scenario_specs()
    if slice_id == "VC-04":
        return _vc04_scenario_specs()
    if slice_id == "VC-05":
        return _vc05_scenario_specs()
    if slice_id == "VC-07":
        return _vc07_scenario_specs()
    if slice_id != "VC-01":
        raise NotImplementedError(f"{slice_id} scenario implementation is not authored yet")
    return (
        ScenarioSpec(
            scenario_id="vc01-reference-curator-positive",
            scenario_kind="positive",
            prompt_purpose="pinned primary source to cited fact/inference packet",
            prompt=_positive_prompt(),
            subagent_marker="VC01_POSITIVE_SUBAGENT_COMPLETE",
            parent_marker="VC01_POSITIVE_PARENT_COMPLETE",
            allowed_changed_paths=(POSITIVE_PACKET_PATH,),
            expected_behavior=(
                "reference_curator writes only the approved packet with pinned identity, "
                "primary-source citations, and fact/inference separation"
            ),
            enable_search=False,
            prompt_revision_id="vc01-positive-controlled-manifest-v12",
        ),
        ScenarioSpec(
            scenario_id="vc01-reference-curator-boundary",
            scenario_kind="boundary",
            prompt_purpose="local fixture context-only classification and persistence refusal",
            prompt=_boundary_prompt(),
            subagent_marker="VC01_BOUNDARY_SUBAGENT_COMPLETE",
            parent_marker="VC01_BOUNDARY_PARENT_COMPLETE",
            allowed_changed_paths=(),
            expected_behavior=(
                "reference_curator keeps the local fixture out of sources and refuses an "
                "unauthorized repository write"
            ),
            enable_search=False,
            prompt_revision_id="vc01-boundary-v1",
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fixed Codex scenarios for the approved Matt Pocock engineering refresh slices."
    )
    parser.add_argument("--slice", dest="slice_id", required=True, choices=SLICE_IDS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    retry_group = parser.add_mutually_exclusive_group()
    retry_group.add_argument(
        "--retry-failed-from",
        type=Path,
        help=(
            "Private-only immutable predecessor result.json; only its failed fixed scenarios are re-run. "
            "It must never be tracked or published."
        ),
    )
    retry_group.add_argument(
        "--revalidate-after-adapter-change-from",
        type=Path,
        help=(
            "Private-only immutable predecessor result.json; re-run the complete fixed scenario set "
            "only after an installed adapter artifact hash change. It must never be tracked or published."
        ),
    )
    return parser


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _captured_at() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _canonical_json_sha256(value: Any) -> str:
    return _sha256_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )






def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _model_evidence(requested_model: str) -> dict[str, Any]:
    return {
        "requested_model": requested_model,
        "model_selection_source": "explicit_cli_argument",
        "actual_model": None,
        "actual_model_observation": ACTUAL_MODEL_OBSERVATION,
    }


def _source_truth_from_bytes(value: bytes) -> PinnedSourceTruth:
    text = value.decode("utf-8")
    return PinnedSourceTruth(
        content_sha256=_sha256_bytes(value),
        content_bytes=value,
        lines=tuple(text.splitlines()),
    )


def _excerpt_sha256(source_truth: PinnedSourceTruth, locator: str) -> str:
    match = LOCATOR_PATTERN.fullmatch(locator)
    if match is None:
        raise ValueError(f"unsupported citation locator: {locator}")
    start = int(match.group("start"))
    end = int(match.group("end"))
    if end < start or end > len(source_truth.lines):
        raise ValueError(f"citation locator outside pinned source: {locator}")
    excerpt = "\n".join(source_truth.lines[start - 1 : end]) + "\n"
    return _sha256_text(excerpt)


def _canonical_integrity_manifest_bytes(source_truth: PinnedSourceTruth) -> bytes:
    manifest = {
        "content_sha256": source_truth.content_sha256,
        "excerpt_sha256_by_locator": {
            locator: _excerpt_sha256(source_truth, locator)
            for locator in POSITIVE_SOURCE_SINGLE_LINE_LOCATORS
        },
    }
    return json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _verify_integrity_manifest(
    manifest_path: Path,
    *,
    source_truth: PinnedSourceTruth | None,
    workspace_root: Path,
) -> tuple[IntegrityManifest | None, dict[str, bool]]:
    assertions = {
        "integrity_manifest_regular_contained_file": _is_regular_file_within(
            manifest_path, workspace_root
        ),
        "integrity_manifest_canonical_json": False,
        "integrity_manifest_mirror_hash_verified": False,
        "integrity_manifest_complete_locator_mapping": False,
    }
    if source_truth is None or not assertions["integrity_manifest_regular_contained_file"]:
        return None, assertions
    try:
        value = manifest_path.read_bytes()
        expected = _canonical_integrity_manifest_bytes(source_truth)
        data = json.loads(value.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, assertions
    mapping = data.get("excerpt_sha256_by_locator") if isinstance(data, dict) else None
    assertions["integrity_manifest_canonical_json"] = value == expected
    assertions["integrity_manifest_mirror_hash_verified"] = (
        isinstance(data, dict) and data.get("content_sha256") == source_truth.content_sha256
    )
    assertions["integrity_manifest_complete_locator_mapping"] = (
        isinstance(mapping, dict)
        and set(mapping) == set(POSITIVE_SOURCE_SINGLE_LINE_LOCATORS)
        and all(
            isinstance(mapping.get(locator), str)
            and mapping[locator] == _excerpt_sha256(source_truth, locator)
            for locator in POSITIVE_SOURCE_SINGLE_LINE_LOCATORS
        )
    )
    if not all(assertions.values()) or not isinstance(mapping, dict):
        return None, assertions
    return (
        IntegrityManifest(
            content_sha256=source_truth.content_sha256,
            excerpt_sha256_by_locator={
                locator: mapping[locator] for locator in POSITIVE_SOURCE_SINGLE_LINE_LOCATORS
            },
            sha256=_sha256_bytes(value),
        ),
        assertions,
    )


def _integrity_manifest_evidence(
    workspace_root: Path, *, source_truth: PinnedSourceTruth | None
) -> dict[str, Any]:
    manifest_path = workspace_root / POSITIVE_SOURCE_MANIFEST_PATH
    manifest, _ = _verify_integrity_manifest(
        manifest_path,
        source_truth=source_truth,
        workspace_root=workspace_root,
    )
    return {
        "manifest_path": POSITIVE_SOURCE_MANIFEST_PATH,
        "manifest_sha256": manifest.sha256 if manifest is not None else None,
        "mapping_cardinality": (
            len(manifest.excerpt_sha256_by_locator) if manifest is not None else 0
        ),
        "mirror_to_manifest_verified": manifest is not None,
        "raw_manifest_persisted": False,
    }


def _fetch_pinned_source_truth(*, timeout_seconds: int = 30) -> PinnedSourceTruth:
    request = urllib.request.Request(
        PINNED_PRIMARY_SOURCE_URL,
        headers={"User-Agent": "HarnessKit-VC01-runtime-probe"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        value = response.read()
    if not value:
        raise RuntimeError("pinned primary source retrieval returned no bytes")
    return _source_truth_from_bytes(value)


def _is_regular_file_within(path: Path, root: Path) -> bool:
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            return False
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError):
        return False
    return True


def _process_group_absent(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _wait_for_process_group_absence(process_group_id: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while time.monotonic() <= deadline:
        if _process_group_absent(process_group_id):
            return True
        time.sleep(0.01)
    return _process_group_absent(process_group_id)


def _signal_process_group(process_group_id: int, value: signal.Signals) -> bool:
    try:
        os.killpg(process_group_id, value)
    except ProcessLookupError:
        return False
    return True


def _sample_process_group(process_group_id: int) -> ProcessSample:
    if sys.platform == "darwin":
        try:
            library = ctypes.CDLL("/usr/lib/libproc.dylib")
            library.proc_listpids.argtypes = [
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            library.proc_listpids.restype = ctypes.c_int
            library.proc_name.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            library.proc_name.restype = ctypes.c_int
            byte_count = library.proc_listpids(2, process_group_id, None, 0)
            if byte_count <= 0:
                return ProcessSample((), True)
            capacity = max(byte_count // ctypes.sizeof(ctypes.c_int) + 32, 64)
            process_ids = (ctypes.c_int * capacity)()
            used_bytes = library.proc_listpids(
                2,
                process_group_id,
                ctypes.byref(process_ids),
                ctypes.sizeof(process_ids),
            )
            if used_bytes < 0:
                return ProcessSample((), False)
            observed: set[tuple[int, str]] = set()
            for process_id in process_ids[: max(used_bytes, 0) // ctypes.sizeof(ctypes.c_int)]:
                if process_id <= 0:
                    continue
                name_buffer = ctypes.create_string_buffer(1024)
                if library.proc_name(process_id, name_buffer, len(name_buffer)) <= 0:
                    continue
                observed.add(
                    (process_id, Path(name_buffer.value.decode(errors="replace")).name.lower())
                )
            return ProcessSample(tuple(sorted(observed)), True)
        except (OSError, ValueError):
            return ProcessSample((), False)
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "pid=,pgid=,comm="],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError:
        return ProcessSample((), False)
    if result.returncode != 0:
        return ProcessSample((), False)
    observed: set[tuple[int, str]] = set()
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        try:
            process_id = int(parts[0])
            group_id = int(parts[1])
        except ValueError:
            continue
        if group_id != process_group_id:
            continue
        observed.add((process_id, Path(parts[2]).name.lower()))
    return ProcessSample(tuple(sorted(observed)), True)


def _observed_forbidden_processes(outcome: ProcessGroupOutcome) -> list[str]:
    found: set[str] = set()
    for _process_id, executable in outcome.observed_processes:
        name = Path(executable).name.lower()
        for runtime in FORBIDDEN_OBSERVED_PROCESSES:
            if name == runtime or name.startswith(f"{runtime}-"):
                found.add(runtime)
    return sorted(found)






def _observed_child_codex_process_count(outcome: ProcessGroupOutcome) -> int:
    codex_process_count = sum(
        1
        for _process_id, executable in outcome.observed_processes
        if (
            Path(executable).name.lower() == "codex"
            or Path(executable).name.lower().startswith("codex-")
        )
    )
    # macOS may report sandbox-exec as the group root, leaving the controller
    # Codex process in the sampled child set. One Codex process is therefore
    # reserved for the controller before this count is compared to spawned
    # collaboration threads.
    return max(0, codex_process_count - 1)


def _child_codex_processes_are_bounded(
    *, child_codex_process_count: int, spawned_thread_count: int
) -> bool:
    return (
        child_codex_process_count >= 0
        and spawned_thread_count >= 0
        and child_codex_process_count <= spawned_thread_count
    )




def _run_process_group(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    timeout_seconds: float,
    terminate_grace_seconds: float = 2.0,
) -> ProcessGroupOutcome:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    observed_processes: set[tuple[int, str]] = {
        (process.pid, Path(argv[0]).name.lower())
    }
    monitor_stop = threading.Event()
    monitor_failed = threading.Event()
    root_observed = threading.Event()

    def capture_process_sample() -> None:
        try:
            sample = _sample_process_group(process.pid)
        except Exception:
            monitor_failed.set()
            return
        if not sample.observation_available:
            monitor_failed.set()
        if any(process_id == process.pid for process_id, _ in sample.processes):
            root_observed.set()
        observed_processes.update(sample.processes)

    def monitor() -> None:
        while not monitor_stop.is_set():
            capture_process_sample()
            monitor_stop.wait(0.02)

    capture_process_sample()
    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    timed_out = False
    term_sent = False
    kill_sent = False
    stdout = ""
    stderr = ""
    unexpected_error: BaseException | None = None
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            term_sent = _signal_process_group(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=terminate_grace_seconds)
            except subprocess.TimeoutExpired:
                kill_sent = _signal_process_group(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
    except BaseException as exc:
        unexpected_error = exc
    finally:
        group_absent = _wait_for_process_group_absence(process.pid, terminate_grace_seconds)
        if not group_absent:
            term_sent = _signal_process_group(process.pid, signal.SIGTERM) or term_sent
            group_absent = _wait_for_process_group_absence(
                process.pid, terminate_grace_seconds
            )
        if not group_absent:
            kill_sent = _signal_process_group(process.pid, signal.SIGKILL) or kill_sent
            group_absent = _wait_for_process_group_absence(
                process.pid, terminate_grace_seconds
            )
        try:
            process.wait(timeout=max(terminate_grace_seconds, 0.1))
        except subprocess.TimeoutExpired:
            kill_sent = _signal_process_group(process.pid, signal.SIGKILL) or kill_sent
            try:
                process.wait(timeout=max(terminate_grace_seconds, 0.1))
            except subprocess.TimeoutExpired:
                pass
        finally:
            monitor_stop.set()
            monitor_thread.join(timeout=max(terminate_grace_seconds, 0.1))
            if monitor_thread.is_alive():
                monitor_failed.set()
            capture_process_sample()
    if unexpected_error is not None:
        raise unexpected_error
    return_code = process.returncode if process.returncode is not None else 1
    return ProcessGroupOutcome(
        completed=subprocess.CompletedProcess(
            argv,
            124 if timed_out else int(return_code),
            stdout,
            stderr,
        ),
        timed_out=timed_out,
        term_sent=term_sent,
        kill_sent=kill_sent,
        process_reaped=process.poll() is not None,
        group_absent_after_cleanup=group_absent,
        process_observation_available=not monitor_failed.is_set(),
        root_process_observed=root_observed.is_set(),
        root_process_id=process.pid,
        observed_processes=tuple(sorted(observed_processes)),
    )


def _plan_attempt(
    specs: tuple[ScenarioSpec, ...],
    *,
    previous_payload: dict[str, Any] | None,
    predecessor_result: Path | None,
    current_install_plan_hash: str,
    current_codex_version: str,
    requested_model: str,
    current_adapter_artifact_hash: str,
) -> AttemptPlan:
    if previous_payload is None:
        if predecessor_result is not None:
            raise ValueError("predecessor result provided without payload")
        return AttemptPlan(specs, (), None, None, (), None)
    if predecessor_result is None or not predecessor_result.is_file():
        raise ValueError("failed-only retry requires an existing predecessor result")
    expected_top_level = {
        "slice_id": "VC-01",
        "component_id": COMPONENT_ID,
        "observed_codex_version": current_codex_version,
        "requested_model": requested_model,
        "install_plan_hash": current_install_plan_hash,
        "adapter_artifact_hash": current_adapter_artifact_hash,
    }
    drift_messages = {
        "slice_id": "slice drift",
        "component_id": "component drift",
        "observed_codex_version": "Codex version drift",
        "requested_model": "requested model drift",
        "install_plan_hash": "install plan drift",
        "adapter_artifact_hash": "adapter artifact drift",
    }
    for key, expected in expected_top_level.items():
        if previous_payload.get(key) != expected:
            raise ValueError(drift_messages[key])
    raw_results = previous_payload.get("scenario_results")
    if not isinstance(raw_results, list):
        raise ValueError("predecessor scenario results are unavailable")
    by_id: dict[str, dict[str, Any]] = {}
    for item in raw_results:
        if not isinstance(item, dict) or not isinstance(item.get("scenario_id"), str):
            raise ValueError("invalid predecessor scenario result")
        scenario_id = item["scenario_id"]
        if scenario_id in by_id:
            raise ValueError("duplicate predecessor scenario id")
        by_id[scenario_id] = item
    expected_ids = {spec.scenario_id for spec in specs}
    if set(by_id) != expected_ids:
        raise ValueError("predecessor fixed scenario set drift")
    selected: list[ScenarioSpec] = []
    carried: list[dict[str, Any]] = []
    prompt_revision_changes: list[dict[str, str]] = []
    for spec in specs:
        item = by_id[spec.scenario_id]
        current_prompt_hash = _sha256_text(spec.prompt)
        predecessor_prompt_hash = item.get("prompt_hash")
        if predecessor_prompt_hash != current_prompt_hash:
            predecessor_revision_id = item.get("prompt_revision_id", "legacy-unversioned")
            if (
                item.get("verdict") == "PASS"
                or not isinstance(predecessor_revision_id, str)
                or not predecessor_revision_id
                or predecessor_revision_id == spec.prompt_revision_id
            ):
                raise ValueError("predecessor prompt drift")
            prompt_revision_changes.append(
                {
                    "scenario_id": spec.scenario_id,
                    "predecessor_prompt_hash": str(predecessor_prompt_hash),
                    "predecessor_prompt_revision_id": predecessor_revision_id,
                    "current_prompt_hash": current_prompt_hash,
                    "current_prompt_revision_id": spec.prompt_revision_id,
                }
            )
        if item.get("verdict") == "PASS":
            carried.append(item)
        else:
            selected.append(spec)
    if not selected:
        raise ValueError("predecessor has no failed fixed scenario")
    return AttemptPlan(
        selected_scenarios=tuple(selected),
        carried_forward_results=tuple(carried),
        predecessor_result_path=str(predecessor_result),
        predecessor_result_hash=_sha256_file(predecessor_result),
        prompt_revision_changes=tuple(prompt_revision_changes),
        artifact_revalidation=None,
    )


def _vc02_expected_completed_spawn_count(spec: ScenarioSpec) -> int:
    return 2 if spec.scenario_kind == "positive" else 3


def _vc02_pass_is_transferable(item: dict[str, Any], spec: ScenarioSpec) -> bool:
    if item.get("component_id") != spec.component_id:
        return False
    loader_evidence = item.get("loader_evidence")
    return isinstance(loader_evidence, dict) and loader_evidence.get(
        "completed_spawn_count"
    ) == _vc02_expected_completed_spawn_count(spec)


def _plan_vc02_attempt(
    specs: tuple[ScenarioSpec, ...],
    *,
    previous_payload: dict[str, Any] | None,
    predecessor_result: Path | None,
    current_install_plan_hashes: dict[str, str],
    current_adapter_artifact_hashes: dict[str, str],
    current_codex_version: str,
    requested_model: str,
) -> AttemptPlan:
    if previous_payload is None:
        if predecessor_result is not None:
            raise ValueError("predecessor result provided without payload")
        return AttemptPlan(specs, (), None, None, (), None)
    if predecessor_result is None or not predecessor_result.is_file():
        raise ValueError("failed-only retry requires an existing predecessor result")
    expected_top_level = {
        "slice_id": "VC-02",
        "runtime_target": "codex",
        "component_ids": list(VC02_COMPONENT_IDS),
        "observed_codex_version": current_codex_version,
        "requested_model": requested_model,
        "install_plan_hashes": current_install_plan_hashes,
        "adapter_artifact_hashes": current_adapter_artifact_hashes,
    }
    drift_messages = {
        "slice_id": "slice drift",
        "runtime_target": "runtime target drift",
        "component_ids": "component set drift",
        "observed_codex_version": "Codex version drift",
        "requested_model": "requested model drift",
        "install_plan_hashes": "install plan drift",
        "adapter_artifact_hashes": "adapter artifact drift",
    }
    for key, expected in expected_top_level.items():
        if previous_payload.get(key) != expected:
            raise ValueError(drift_messages[key])
    raw_results = previous_payload.get("scenario_results")
    if not isinstance(raw_results, list):
        raise ValueError("predecessor scenario results are unavailable")
    by_id: dict[str, dict[str, Any]] = {}
    for item in raw_results:
        if not isinstance(item, dict) or not isinstance(item.get("scenario_id"), str):
            raise ValueError("invalid predecessor scenario result")
        scenario_id = item["scenario_id"]
        if scenario_id in by_id:
            raise ValueError("duplicate predecessor scenario id")
        by_id[scenario_id] = item
    if set(by_id) != {spec.scenario_id for spec in specs}:
        raise ValueError("predecessor fixed scenario set drift")

    selected: list[ScenarioSpec] = []
    carried: list[dict[str, Any]] = []
    prompt_revision_changes: list[dict[str, str]] = []
    for spec in specs:
        item = by_id[spec.scenario_id]
        current_prompt_hash = _sha256_text(spec.prompt)
        predecessor_prompt_hash = item.get("prompt_hash")
        if predecessor_prompt_hash != current_prompt_hash:
            predecessor_revision_id = item.get("prompt_revision_id", "legacy-unversioned")
            if (
                item.get("verdict") == "PASS"
                or not isinstance(predecessor_revision_id, str)
                or not predecessor_revision_id
                or predecessor_revision_id == spec.prompt_revision_id
            ):
                raise ValueError("predecessor prompt drift")
            prompt_revision_changes.append(
                {
                    "scenario_id": spec.scenario_id,
                    "predecessor_prompt_hash": str(predecessor_prompt_hash),
                    "predecessor_prompt_revision_id": predecessor_revision_id,
                    "current_prompt_hash": current_prompt_hash,
                    "current_prompt_revision_id": spec.prompt_revision_id,
                }
            )
        if item.get("verdict") == "PASS":
            if not _vc02_pass_is_transferable(item, spec):
                raise ValueError("predecessor pass is not transferable")
            carried.append(item)
        else:
            selected.append(spec)
    if not selected:
        raise ValueError("predecessor has no failed fixed scenario")
    return AttemptPlan(
        selected_scenarios=tuple(selected),
        carried_forward_results=tuple(carried),
        predecessor_result_path=str(predecessor_result),
        predecessor_result_hash=_sha256_file(predecessor_result),
        prompt_revision_changes=tuple(prompt_revision_changes),
        artifact_revalidation=None,
    )


def _validated_vc02_hash_map(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(VC02_COMPONENT_IDS):
        raise ValueError(f"predecessor {field} are unavailable")
    if any(not isinstance(value[component_id], str) or not value[component_id] for component_id in VC02_COMPONENT_IDS):
        raise ValueError(f"predecessor {field} are unavailable")
    return {component_id: value[component_id] for component_id in VC02_COMPONENT_IDS}


def _plan_vc02_artifact_revalidation(
    specs: tuple[ScenarioSpec, ...],
    *,
    previous_payload: dict[str, Any] | None,
    predecessor_result: Path | None,
    current_install_plan_hashes: dict[str, str],
    current_adapter_artifact_hashes: dict[str, str],
    current_codex_version: str,
    requested_model: str,
) -> AttemptPlan:
    if previous_payload is None or predecessor_result is None or not predecessor_result.is_file():
        raise ValueError("artifact revalidation requires an existing predecessor result")
    expected_top_level = {
        "slice_id": "VC-02",
        "runtime_target": "codex",
        "component_ids": list(VC02_COMPONENT_IDS),
        "observed_codex_version": current_codex_version,
        "requested_model": requested_model,
    }
    drift_messages = {
        "slice_id": "slice drift",
        "runtime_target": "runtime target drift",
        "component_ids": "component set drift",
        "observed_codex_version": "Codex version drift",
        "requested_model": "requested model drift",
    }
    for key, expected in expected_top_level.items():
        if previous_payload.get(key) != expected:
            raise ValueError(drift_messages[key])
    predecessor_install_plan_hashes = _validated_vc02_hash_map(
        previous_payload.get("install_plan_hashes"), field="install plan hashes"
    )
    predecessor_adapter_artifact_hashes = _validated_vc02_hash_map(
        previous_payload.get("adapter_artifact_hashes"), field="adapter artifact hashes"
    )
    if predecessor_adapter_artifact_hashes == current_adapter_artifact_hashes:
        raise ValueError("artifact revalidation requires adapter artifact drift")
    raw_results = previous_payload.get("scenario_results")
    if not isinstance(raw_results, list):
        raise ValueError("predecessor scenario results are unavailable")
    previous_ids = [
        item.get("scenario_id")
        for item in raw_results
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    ]
    expected_ids = [spec.scenario_id for spec in specs]
    if (
        len(previous_ids) != len(raw_results)
        or len(set(previous_ids)) != len(previous_ids)
        or set(previous_ids) != set(expected_ids)
    ):
        raise ValueError("predecessor fixed scenario set drift")
    return AttemptPlan(
        selected_scenarios=specs,
        carried_forward_results=(),
        predecessor_result_path=str(predecessor_result),
        predecessor_result_hash=_sha256_file(predecessor_result),
        prompt_revision_changes=(),
        artifact_revalidation={
            "predecessor_adapter_artifact_hashes": predecessor_adapter_artifact_hashes,
            "current_adapter_artifact_hashes": current_adapter_artifact_hashes,
            "predecessor_install_plan_hashes": predecessor_install_plan_hashes,
            "current_install_plan_hashes": current_install_plan_hashes,
        },
    )


def _plan_artifact_revalidation(
    specs: tuple[ScenarioSpec, ...],
    *,
    previous_payload: dict[str, Any] | None,
    predecessor_result: Path | None,
    current_install_plan_hash: str,
    current_codex_version: str,
    requested_model: str,
    current_adapter_artifact_hash: str,
) -> AttemptPlan:
    if previous_payload is None or predecessor_result is None or not predecessor_result.is_file():
        raise ValueError("artifact revalidation requires an existing predecessor result")
    expected_top_level = {
        "slice_id": "VC-01",
        "component_id": COMPONENT_ID,
        "observed_codex_version": current_codex_version,
        "requested_model": requested_model,
    }
    drift_messages = {
        "slice_id": "slice drift",
        "component_id": "component drift",
        "observed_codex_version": "Codex version drift",
        "requested_model": "requested model drift",
    }
    for key, expected in expected_top_level.items():
        if previous_payload.get(key) != expected:
            raise ValueError(drift_messages[key])
    predecessor_adapter_hash = previous_payload.get("adapter_artifact_hash")
    predecessor_install_plan_hash = previous_payload.get("install_plan_hash")
    if not isinstance(predecessor_adapter_hash, str) or not predecessor_adapter_hash:
        raise ValueError("predecessor adapter artifact hash is unavailable")
    if not isinstance(predecessor_install_plan_hash, str) or not predecessor_install_plan_hash:
        raise ValueError("predecessor install plan hash is unavailable")
    if predecessor_adapter_hash == current_adapter_artifact_hash:
        raise ValueError("artifact revalidation requires adapter artifact drift")
    raw_results = previous_payload.get("scenario_results")
    if not isinstance(raw_results, list):
        raise ValueError("predecessor scenario results are unavailable")
    previous_ids = {
        item.get("scenario_id")
        for item in raw_results
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    }
    expected_ids = {spec.scenario_id for spec in specs}
    if previous_ids != expected_ids or len(raw_results) != len(expected_ids):
        raise ValueError("predecessor fixed scenario set drift")
    return AttemptPlan(
        selected_scenarios=specs,
        carried_forward_results=(),
        predecessor_result_path=str(predecessor_result),
        predecessor_result_hash=_sha256_file(predecessor_result),
        prompt_revision_changes=(),
        artifact_revalidation={
            "predecessor_adapter_artifact_hash": predecessor_adapter_hash,
            "current_adapter_artifact_hash": current_adapter_artifact_hash,
            "predecessor_install_plan_hash": predecessor_install_plan_hash,
            "current_install_plan_hash": current_install_plan_hash,
        },
    )


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return subprocess.CompletedProcess(argv, 124, stdout, stderr)


def _codex_argv(
    *,
    workspace: Path,
    last_message: Path,
    prompt: str,
    model: str,
    enable_search: bool,
    enable_multi_agent: bool = True,
) -> list[str]:
    argv = ["codex"]
    if enable_search:
        argv.append("--search")
    argv.extend(
        [
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(workspace),
        ]
    )
    if enable_multi_agent:
        argv.extend(["--enable", "multi_agent"])
    argv.extend(["--model", model, "--output-last-message", str(last_message), prompt])
    return argv


def _sanitized_runtime_env(
    base_env: dict[str, str],
    *,
    home: Path,
    codex_home: Path,
    temp_dir: Path,
) -> dict[str, str]:
    env: dict[str, str] = {
        "PATH": RUNTIME_SYSTEM_PATH,
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "TMPDIR": str(temp_dir),
        "LANG": base_env.get("LANG", "en_US.UTF-8"),
        "LC_ALL": base_env.get("LC_ALL", base_env.get("LANG", "en_US.UTF-8")),
        "NO_COLOR": "1",
    }
    for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
        value = base_env.get(key)
        if value:
            env[key] = value
    return env


def _nested_value(data: Any, keys: tuple[str, ...]) -> Any:
    value = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _thread_ids(item: dict[str, Any]) -> set[str]:
    ids = {
        thread_id
        for thread_id in (item.get("receiver_thread_ids") or [])
        if isinstance(thread_id, str) and thread_id
    }
    for candidate in (
        item.get("receiver_thread_id"),
        item.get("thread_id"),
        _nested_value(item, ("result", "receiver_thread_id")),
        _nested_value(item, ("result", "thread_id")),
        _nested_value(item, ("output", "receiver_thread_id")),
        _nested_value(item, ("output", "thread_id")),
    ):
        if isinstance(candidate, str) and candidate:
            ids.add(candidate)
    return ids


def _event_objects(stdout: str) -> Iterable[dict[str, Any]]:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _collab_evidence(stdout: str, marker: str) -> CollabEvidence:
    parent_threads: set[str] = set()
    spawned: set[str] = set()
    marker_contract: set[str] = set()
    completed: set[str] = set()
    marker_threads: set[str] = set()
    completed_messages: list[str] = []
    spawn_attempt_ids: set[str] = set()
    failed_spawn_attempt_ids: set[str] = set()
    spawn_indexes: dict[str, int] = {}
    spawn_senders: dict[str, str | None] = {}
    wait_started: dict[str, tuple[set[str], int]] = {}
    wait_completed_indexes: dict[str, int] = {}
    wait_senders: dict[str, set[str | None]] = {}

    for event_index, event in enumerate(_event_objects(stdout)):
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                parent_threads.add(thread_id)
            continue
        if event.get("type") not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "collab_tool_call":
            continue
        tool = item.get("tool") or item.get("name")
        ids = _thread_ids(item)
        if tool == "spawn_agent":
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id:
                spawn_attempt_ids.add(item_id)
            if event.get("type") == "item.completed":
                if item.get("status") != "completed":
                    if isinstance(item_id, str) and item_id:
                        failed_spawn_attempt_ids.add(item_id)
                    continue
                spawned.update(ids)
                prompt = item.get("prompt")
                sender_thread_id = item.get("sender_thread_id")
                for thread_id in ids:
                    spawn_indexes[thread_id] = event_index
                    spawn_senders[thread_id] = (
                        sender_thread_id if isinstance(sender_thread_id, str) else None
                    )
                    if isinstance(prompt, str) and marker in prompt:
                        marker_contract.add(thread_id)
            continue
        if tool != "wait":
            continue
        item_id = item.get("id")
        sender_thread_id = item.get("sender_thread_id")
        sender = sender_thread_id if isinstance(sender_thread_id, str) else None
        for thread_id in ids:
            wait_senders.setdefault(thread_id, set()).add(sender)
        if event.get("type") == "item.started":
            if isinstance(item_id, str) and ids:
                wait_started[item_id] = (ids, event_index)
            continue
        if not ids and isinstance(item_id, str):
            ids = wait_started.get(item_id, (set(), event_index))[0]
        states = (
            _nested_value(item, ("result", "agents_states"))
            or _nested_value(item, ("output", "agents_states"))
            or item.get("agents_states")
        )
        if not isinstance(states, dict):
            continue
        for thread_id, state in states.items():
            if (
                not isinstance(thread_id, str)
                or thread_id not in spawned
                or not isinstance(state, dict)
            ):
                continue
            if state.get("status") != "completed":
                continue
            completed.add(thread_id)
            wait_completed_indexes[thread_id] = event_index
            message = state.get("message")
            if isinstance(message, str):
                completed_messages.append(message)
                if marker in message:
                    marker_threads.add(thread_id)

    contract_completed = spawned & marker_contract & completed
    exact_single_thread = len(spawned) == 1
    sole_thread = next(iter(spawned), None) if exact_single_thread else None
    sole_parent = next(iter(parent_threads), None) if len(parent_threads) == 1 else None
    parent_thread_joined = bool(
        sole_thread is not None
        and sole_parent is not None
        and spawn_senders.get(sole_thread) == sole_parent
        and wait_senders.get(sole_thread) == {sole_parent}
    )
    ordered = False
    if sole_thread is not None:
        started_indexes = [
            index for ids, index in wait_started.values() if sole_thread in ids
        ]
        spawn_index = spawn_indexes.get(sole_thread)
        completed_index = wait_completed_indexes.get(sole_thread)
        ordered = bool(
            spawn_index is not None
            and started_indexes
            and completed_index is not None
            and spawn_index < min(started_indexes) < completed_index
            and sole_thread in marker_threads
            and parent_thread_joined
        )
    return CollabEvidence(
        completed=bool(spawned & completed),
        marker_contract_sent=bool(spawned & marker_contract),
        contract_completed=bool(contract_completed),
        marker_found=bool(spawned & marker_threads),
        completed_messages=tuple(completed_messages),
        spawn_attempt_count=len(spawn_attempt_ids),
        failed_spawn_attempt_count=len(failed_spawn_attempt_ids),
        spawned_thread_count=len(spawned),
        completed_thread_count=len(spawned & completed),
        exact_single_thread=exact_single_thread,
        parent_thread_joined=parent_thread_joined,
        agent_type_observation=COLLAB_AGENT_TYPE_OBSERVATION,
        ordered=ordered,
    )


def _subagent_result_diagnostic(
    messages: Iterable[str], *, marker: str
) -> dict[str, Any]:
    values = tuple(message for message in messages if isinstance(message, str))
    exact_marker_count = sum(message.strip() == marker for message in values)
    marker_bearing_count = sum(marker in message for message in values)
    strict_json_object_message_count = sum(
        _extract_json_object(message) is not None for message in values
    )
    markdown_fence_message_count = sum(
        message.strip().startswith("```") for message in values
    )
    lowered = "\n".join(values).lower()
    failure_language = any(
        token in lowered
        for token in ("unable", "cannot", "could not", "failed", "failure", "error")
    )
    source_access_language = any(
        token in lowered
        for token in (
            "fetch",
            "network",
            "remote",
            "source access",
            "source url",
            "cannot access the source",
            "unable to access the source",
        )
    )
    mirror_path_mentioned = POSITIVE_SOURCE_MIRROR_PATH.lower() in lowered
    mirror_unavailable_language = mirror_path_mentioned and any(
        token in lowered
        for token in ("no such file", "not found", "does not exist", "missing")
    )
    network_or_remote_language = any(
        token in lowered for token in ("fetch", "network", "http", "url", "remote")
    )
    persistence_refusal_language = any(
        token in lowered
        for token in ("not authorized", "unauthorized", "refuse", "refused")
    )
    command_execution_unavailable_language = any(
        token in lowered
        for token in (
            "cannot execute",
            "can't execute",
            "unable to execute",
            "cannot run",
            "can't run",
            "unable to run",
            "shell access",
            "tool access",
            "tools are unavailable",
            "command execution",
        )
    )
    hash_execution_blocked_language = command_execution_unavailable_language and any(
        token in lowered for token in ("shasum", "sha256", "checksum", "hash")
    )
    policy_refusal_language = any(
        token in lowered
        for token in (
            "not allowed",
            "not permitted",
            "cannot comply",
            "unable to comply",
            "must refuse",
            "cannot fulfill",
        )
    )
    if exact_marker_count:
        category = "exact_marker"
    elif marker_bearing_count:
        category = "marker_bearing_result"
    elif not values:
        category = "absent"
    elif command_execution_unavailable_language:
        category = "command_execution_unavailable"
    elif mirror_unavailable_language or (failure_language and source_access_language):
        category = "source_access_failure"
    elif persistence_refusal_language or policy_refusal_language:
        category = "persistence_refusal"
    elif failure_language:
        category = "generic_failure"
    else:
        category = "non_marker_result"
    return {
        "category": category,
        "completed_message_count": len(values),
        "exact_marker_count": exact_marker_count,
        "marker_bearing_message_count": marker_bearing_count,
        "completed_message_lengths": sorted(len(message) for message in values),
        "strict_json_object_message_count": strict_json_object_message_count,
        "markdown_fence_message_count": markdown_fence_message_count,
        "mirror_path_mentioned": mirror_path_mentioned,
        "mirror_unavailable_language": mirror_unavailable_language,
        "network_or_remote_language": network_or_remote_language,
        "command_execution_unavailable_language": command_execution_unavailable_language,
        "hash_execution_blocked_language": hash_execution_blocked_language,
        "policy_refusal_language": policy_refusal_language,
    }


def _sanitize_events(stdout: str, *, scenario_id: str) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for event_index, event in enumerate(_event_objects(stdout)):
        event_type = event.get("type")
        record: dict[str, Any] = {
            "scenario_id": scenario_id,
            "event_type": event_type,
            "event_index": event_index,
        }
        event_thread_id = event.get("thread_id")
        if isinstance(event_thread_id, str):
            event_thread_hash = _sha256_text(event_thread_id)
            record["event_thread_hash"] = event_thread_hash
            if event_type == "thread.started":
                record["thread_id_hash"] = event_thread_hash
        item = event.get("item")
        if isinstance(item, dict):
            record.update(
                {
                    "item_id": item.get("id"),
                    "item_type": item.get("type"),
                    "status": item.get("status"),
                }
            )
            tool = item.get("tool") or item.get("name")
            if isinstance(tool, str):
                record["tool"] = tool
            sender_thread_id = item.get("sender_thread_id")
            if isinstance(sender_thread_id, str):
                record["sender_thread_hash"] = _sha256_text(sender_thread_id)
            receiver_ids = sorted(_thread_ids(item))
            if receiver_ids:
                record["receiver_thread_hashes"] = [_sha256_text(value) for value in receiver_ids]
            prompt = item.get("prompt")
            if isinstance(prompt, str):
                record["prompt_hash"] = _sha256_text(prompt)
            command = item.get("command")
            if isinstance(command, str):
                record["command_hash"] = _sha256_text(command)
            if isinstance(item.get("exit_code"), int):
                record["exit_code"] = item["exit_code"]
            message_hashes: list[str] = []
            completed_agent_thread_hashes: list[str] = []
            for states in (
                item.get("agents_states"),
                _nested_value(item, ("result", "agents_states")),
                _nested_value(item, ("output", "agents_states")),
            ):
                if not isinstance(states, dict):
                    continue
                for thread_id, state in states.items():
                    if not isinstance(state, dict):
                        continue
                    if isinstance(state.get("message"), str):
                        message_hashes.append(_sha256_text(state["message"]))
                    if isinstance(thread_id, str) and state.get("status") == "completed":
                        completed_agent_thread_hashes.append(_sha256_text(thread_id))
            if message_hashes:
                record["message_hashes"] = sorted(set(message_hashes))
            if completed_agent_thread_hashes:
                record["completed_agent_thread_hashes"] = sorted(
                    set(completed_agent_thread_hashes)
                )
            for text_key in ("text", "message", "aggregated_output"):
                value = item.get(text_key)
                if isinstance(value, str):
                    record[f"{text_key}_hash"] = _sha256_text(value)
                    record[f"{text_key}_length"] = len(value)
        usage = event.get("usage")
        if isinstance(usage, dict):
            record["usage"] = {
                key: value
                for key, value in usage.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        sanitized.append(record)
    return sanitized


def _forbidden_runtime_commands(stdout: str) -> list[str]:
    found: set[str] = set()
    for event in _event_objects(stdout):
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if not isinstance(command, str):
            continue
        lowered = command.lower()
        for runtime in FORBIDDEN_RUNTIME_COMMANDS:
            if re.search(rf"(^|[\s/'\";|&]){re.escape(runtime)}([\s/'\";|&]|$)", lowered):
                found.add(runtime)
    return sorted(found)


def _vc05_github_contact_observation(stdout: str) -> dict[str, bool | int]:
    event_count = 0
    gh_command_count = 0
    github_url_or_api_command_count = 0
    network_command_count = 0
    github_mcp_tool_call_count = 0
    for event in _event_objects(stdout):
        event_count += 1
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") == "command_execution":
            command = item.get("command")
            if isinstance(command, str):
                lowered = command.lower()
                if re.search(r"(^|[\s;'|&])gh(?:[\s;'|&]|$)", lowered):
                    gh_command_count += 1
                if "github.com" in lowered or "api.github" in lowered:
                    github_url_or_api_command_count += 1
                if re.search(r"(^|[\s;'|&])(curl|wget|http|https)(?:[\s;'|&]|$)", lowered):
                    network_command_count += 1
                if re.search(r"\bgit\s+(clone|fetch|ls-remote|pull|push)\b", lowered):
                    network_command_count += 1
        tool = item.get("tool") or item.get("name")
        if item.get("type") == "mcp_tool_call" and isinstance(tool, str):
            normalized = tool.lower().replace("-", "_")
            if "github" in normalized or normalized.startswith("gh_"):
                github_mcp_tool_call_count += 1
    github_contact_observed = any(
        (
            gh_command_count,
            github_url_or_api_command_count,
            network_command_count,
            github_mcp_tool_call_count,
        )
    )
    return {
        "event_stream_observation_available": event_count > 0,
        "gh_command_count": gh_command_count,
        "github_url_or_api_command_count": github_url_or_api_command_count,
        "network_command_count": network_command_count,
        "github_mcp_tool_call_count": github_mcp_tool_call_count,
        "github_contact_observed": github_contact_observed,
        "raw_event_values_persisted": False,
    }


def _forbidden_hash_command_observation(stdout: str) -> dict[str, bool | int]:
    commands: list[str] = []
    for event in _event_objects(stdout):
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if isinstance(command, str):
            commands.append(command.lower())
    hash_commands = [command for command in commands if "shasum" in command]
    return {
        "shasum_command_observed": bool(hash_commands),
        "sed_command_observed": any("sed" in command for command in hash_commands),
        "cut_command_observed": any("cut" in command for command in hash_commands),
        "hash_command_execution_count": len(hash_commands),
    }


def _child_manifest_contract_sent(stdout: str, *, marker: str) -> bool:
    for event in _event_objects(stdout):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "collab_tool_call":
            continue
        if (item.get("tool") or item.get("name")) != "spawn_agent":
            continue
        if item.get("status") != "completed":
            continue
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or marker not in prompt:
            continue
        if "<parent-codex-hash-preflight>" in prompt:
            continue
        if (
            POSITIVE_SOURCE_MIRROR_PATH not in prompt
            or POSITIVE_SOURCE_MANIFEST_PATH not in prompt
        ):
            continue
        if (
            "read the mirror before choosing claims and citation locators" in prompt
            and "Do not run shell hash commands" in prompt
            and "shasum" not in prompt
        ):
            return True
    return False


def _tree_manifest(root: Path, *, exclude_git: bool = True) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if exclude_git and ".git" in rel.parts:
            continue
        key = rel.as_posix()
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(mode):
            manifest[key] = f"dir:{stat.S_IMODE(mode):o}"
            continue
        if stat.S_ISLNK(mode):
            manifest[key] = (
                f"symlink:{stat.S_IMODE(mode):o}:" + _sha256_text(os.readlink(path))
            )
        elif stat.S_ISREG(mode):
            manifest[key] = f"file:{stat.S_IMODE(mode):o}:" + _sha256_file(path)
        else:
            manifest[key] = f"special:{stat.S_IFMT(mode):o}"
    return manifest


def _git_metadata_manifest(workspace: Path) -> dict[str, str]:
    git_dir = workspace / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        raise RuntimeError("synthetic workspace git metadata is unavailable")
    return _tree_manifest(git_dir, exclude_git=False)


def _tree_hash(manifest: dict[str, str]) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


def _tree_diff(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    )


def _manifest_paths_with_parents(paths: Iterable[str]) -> set[str]:
    expanded: set[str] = set()
    for value in paths:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"manifest allowlist path is not relative and contained: {value}")
        expanded.add(path.as_posix())
        expanded.update(
            parent.as_posix() for parent in path.parents if parent != Path(".")
        )
    return expanded


def _summarize_isolation_changes(
    changed_paths: Iterable[str], *, controller_allowed_paths: set[str]
) -> dict[str, Any]:
    values = sorted(set(changed_paths))
    top_level_counts: dict[str, int] = {}
    ephemeral_counts = {root: 0 for root in EPHEMERAL_ISOLATION_ROOTS}
    unexpected: list[str] = []
    for value in values:
        top_level = value.split("/", 1)[0]
        top_level_counts[top_level] = top_level_counts.get(top_level, 0) + 1
        if top_level in ephemeral_counts:
            ephemeral_counts[top_level] += 1
        elif value not in controller_allowed_paths:
            unexpected.append(value)
    return {
        "actual_changed_path_count": len(values),
        "actual_changed_top_level_counts": dict(sorted(top_level_counts.items())),
        "ephemeral_runtime_prefixes": sorted(EPHEMERAL_ISOLATION_ROOTS),
        "ephemeral_changed_path_counts": {
            root: count for root, count in sorted(ephemeral_counts.items()) if count
        },
        "unexpected_changed_paths": unexpected,
    }


def _validate_positive_packet(
    packet_path: Path,
    *,
    source_truth: PinnedSourceTruth | None = None,
    workspace_root: Path | None = None,
    packet_data: dict[str, Any] | None = None,
    packet_regular_contained_file: bool | None = None,
) -> tuple[dict[str, bool], list[str]]:
    root = workspace_root or packet_path.parent
    manifest, manifest_assertions = _verify_integrity_manifest(
        root / POSITIVE_SOURCE_MANIFEST_PATH,
        source_truth=source_truth,
        workspace_root=root,
    )
    assertions: dict[str, bool] = {
        "packet_regular_contained_file": (
            _is_regular_file_within(packet_path, root)
            if packet_regular_contained_file is None
            else packet_regular_contained_file
        ),
        "schema_valid": False,
        "source_mode_enforced": False,
        "persistence_authorized_exact_path": False,
        "pinned_primary_source_observed": False,
        "source_truth_hash_verified": False,
        "controller_pinned_mirror_context_only": False,
        **manifest_assertions,
        "integrity_manifest_verified": manifest is not None,
        "child_mirror_inspection_receipt_verified": False,
        "license_uncertainty_preserved": False,
        "fact_inference_split": False,
        "claim_citations_known_and_pinned": False,
        "citation_excerpts_verified": False,
        "selected_locator_hashes_verified": False,
    }
    if not assertions["packet_regular_contained_file"]:
        return assertions, [key for key, value in assertions.items() if not value]
    try:
        data = packet_data if packet_data is not None else json.loads(packet_path.read_text(encoding="utf-8"))
        schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
        jsonschema.validate(data, schema)
        assertions["schema_valid"] = True
    except (json.JSONDecodeError, jsonschema.ValidationError, OSError):
        return assertions, [key for key, value in assertions.items() if not value]

    assertions["source_mode_enforced"] = (
        data.get("source_mode") == "github_open_source_research"
        and data.get("source_scope_enforced") is True
    )
    persistence = data.get("persistence") or {}
    assertions["persistence_authorized_exact_path"] = (
        persistence.get("authorized") is True
        and persistence.get("requested_path") == POSITIVE_PACKET_PATH
        and persistence.get("actual_path") == POSITIVE_PACKET_PATH
        and persistence.get("refused_paths") == []
    )
    sources = data.get("sources") or []
    pinned_sources = [
        source
        for source in sources
        if isinstance(source, dict)
        and source.get("url_or_path") == PINNED_PRIMARY_SOURCE_URL
        and source.get("requested_ref") == "main"
        and source.get("observed_ref") == PINNED_PRIMARY_SOURCE_COMMIT
        and source.get("primary_source") is True
        and (source.get("observed_identity") or {}).get("kind") == "commit"
        and (source.get("observed_identity") or {}).get("value") == PINNED_PRIMARY_SOURCE_COMMIT
        and source.get("copied_content_allowed") is False
    ]
    assertions["pinned_primary_source_observed"] = bool(pinned_sources) and len(pinned_sources) == len(sources)
    assertions["source_truth_hash_verified"] = bool(manifest and pinned_sources) and all(
        source.get("content_sha256") == manifest.content_sha256 for source in pinned_sources
    )
    assertions["controller_pinned_mirror_context_only"] = (
        data.get("context_inspected_not_sources")
        == [
            {
                "path": POSITIVE_SOURCE_MIRROR_PATH,
                "reason": POSITIVE_SOURCE_MIRROR_REASON,
            },
            {
                "path": POSITIVE_SOURCE_MANIFEST_PATH,
                "reason": POSITIVE_SOURCE_MANIFEST_REASON,
            }
        ]
    )
    assertions["child_mirror_inspection_receipt_verified"] = bool(source_truth) and (
        data.get("inspection_receipts")
        == [
            {
                "path": POSITIVE_SOURCE_MIRROR_PATH,
                "byte_count": len(source_truth.content_bytes),
            }
        ]
    )
    assertions["license_uncertainty_preserved"] = bool(pinned_sources) and all(
        (source.get("license") or {}).get("status") in {"unknown", "uncertain"}
        for source in pinned_sources
    )
    claims = [claim for claim in (data.get("claims") or []) if isinstance(claim, dict)]
    classifications = {claim.get("classification") for claim in claims}
    assertions["fact_inference_split"] = {"fact", "inference"}.issubset(classifications)
    source_ids = {
        source.get("source_id")
        for source in pinned_sources
        if isinstance(source, dict) and isinstance(source.get("source_id"), str)
    }
    assertions["claim_citations_known_and_pinned"] = bool(claims) and all(
        isinstance(citations := claim.get("citations"), list)
        and bool(citations)
        and all(
            isinstance(citation, dict)
            and citation.get("source_id") in source_ids
            and citation.get("url") == PINNED_PRIMARY_SOURCE_URL
            and isinstance(citation.get("locator"), str)
            and bool(citation["locator"].strip())
            for citation in citations
        )
        for claim in claims
    )
    excerpt_checks: list[bool] = []
    if manifest is not None:
        for claim in claims:
            for citation in claim.get("citations") or []:
                if not isinstance(citation, dict):
                    excerpt_checks.append(False)
                    continue
                locator = citation.get("locator")
                excerpt_checks.append(
                    isinstance(locator, str)
                    and locator in manifest.excerpt_sha256_by_locator
                    and citation.get("excerpt_sha256")
                    == manifest.excerpt_sha256_by_locator[locator]
                )
    assertions["citation_excerpts_verified"] = bool(excerpt_checks) and all(excerpt_checks)
    assertions["selected_locator_hashes_verified"] = assertions["citation_excerpts_verified"]
    errors = [key for key, value in assertions.items() if not value]
    return assertions, errors


def _extract_json_object(message: str) -> dict[str, Any] | None:
    try:
        data = json.loads(message.strip())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _validate_vc02_agentic_timeline(
    data: dict[str, Any], *, scenario_kind: str
) -> tuple[dict[str, bool], list[str]]:
    if scenario_kind not in {"positive", "boundary"}:
        raise ValueError(f"unsupported VC-02 scenario kind: {scenario_kind}")
    timeline = data.get("timeline") if isinstance(data.get("timeline"), list) else []
    observed_sequence = tuple(
        (item.get("event"), item.get("verdict"))
        for item in timeline
        if isinstance(item, dict)
    )
    expected_sequence = (
        (("spec_review", "PASS"), ("quality_review", "PASS"))
        if scenario_kind == "positive"
        else (
            ("spec_review", "CHANGES_REQUESTED"),
            ("implementation_fix", "DONE"),
            ("spec_review", "PASS"),
            ("quality_review", "PASS"),
        )
    )
    mutation_summary = data.get("mutation_summary")
    quality_indexes = [
        index
        for index, (event, _verdict) in enumerate(observed_sequence)
        if event == "quality_review"
    ]
    spec_pass_indexes = [
        index
        for index, (event, verdict) in enumerate(observed_sequence)
        if event == "spec_review" and verdict == "PASS"
    ]
    implementation_fix_indexes = [
        index
        for index, (event, _verdict) in enumerate(observed_sequence)
        if event == "implementation_fix"
    ]
    assertions = {
        "structured_timeline_result": isinstance(data, dict) and bool(timeline),
        "timeline_events_exact": observed_sequence == expected_sequence,
        "implementation_path_allowed": mutation_summary
        == {"implementation_path": VC02_IMPLEMENTATION_PATH},
        "quality_after_spec_pass": bool(quality_indexes)
        and bool(spec_pass_indexes)
        and min(spec_pass_indexes) < min(quality_indexes),
        "restart_at_spec_after_implementation_change": (
            True
            if scenario_kind == "positive"
            else bool(implementation_fix_indexes)
            and bool(spec_pass_indexes)
            and min(implementation_fix_indexes) < max(spec_pass_indexes) < min(quality_indexes)
        ),
        "summary_recorded": isinstance(data.get("summary"), str) and bool(data["summary"]),
    }
    return assertions, [key for key, value in assertions.items() if not value]


def _vc02_phase_markers(scenario_kind: str) -> tuple[str, ...]:
    if scenario_kind == "positive":
        return (
            "VC02_AGENTIC_POSITIVE_SPEC_PASS",
            "VC02_AGENTIC_POSITIVE_QUALITY_PASS",
        )
    if scenario_kind == "boundary":
        return (
            "VC02_AGENTIC_BOUNDARY_SPEC_CHANGES_REQUESTED",
            "VC02_AGENTIC_BOUNDARY_SPEC_PASS",
            "VC02_AGENTIC_BOUNDARY_QUALITY_PASS",
        )
    raise ValueError(f"unsupported VC-02 scenario kind: {scenario_kind}")


def _item_mentions_path(item: dict[str, Any], path: str) -> bool:
    def contains(value: Any) -> bool:
        if isinstance(value, str):
            return path in value
        if isinstance(value, dict):
            return any(contains(child) for child in value.values())
        if isinstance(value, list):
            return any(contains(child) for child in value)
        return False

    return contains(
        {
            key: item.get(key)
            for key in ("command", "path", "file_path", "patch", "input", "arguments")
            if key in item
        }
    )


def _validate_vc02_agentic_event_order(
    stdout: str, *, scenario_kind: str
) -> tuple[dict[str, bool], list[str]]:
    markers = _vc02_phase_markers(scenario_kind)
    parent_thread_id: str | None = None
    failed_spawns_produced_no_child_thread = True
    spawned: list[dict[str, Any]] = []
    wait_records: dict[str, dict[str, Any]] = {}
    implementation_change_indexes: list[int] = []

    for event_index, event in enumerate(_event_objects(stdout)):
        if event.get("type") == "thread.started" and parent_thread_id is None:
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                parent_thread_id = thread_id
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        tool = item.get("tool") or item.get("name")
        if (
            event.get("type") == "item.completed"
            and item.get("status") in {None, "completed"}
            and (
                (
                    item_type == "command_execution"
                    and _item_mentions_path(item, VC02_IMPLEMENTATION_PATH)
                )
                or item_type == "file_change"
            )
        ):
            implementation_change_indexes.append(event_index)
        if item_type != "collab_tool_call" or tool != "spawn_agent":
            if item_type != "collab_tool_call" or tool != "wait":
                continue
        if tool == "spawn_agent":
            if event.get("type") != "item.completed":
                continue
            if item.get("status") != "completed":
                failed_spawns_produced_no_child_thread = (
                    failed_spawns_produced_no_child_thread and not _thread_ids(item)
                )
                continue
            prompt = item.get("prompt")
            matched_markers = (
                [marker for marker in markers if isinstance(prompt, str) and marker in prompt]
            )
            ids = sorted(_thread_ids(item))
            spawned.append(
                {
                    "marker": matched_markers[0] if len(matched_markers) == 1 else None,
                    "marker_count": len(matched_markers),
                    "thread_id": ids[0] if len(ids) == 1 else None,
                    "thread_count": len(ids),
                    "sender_thread_id": item.get("sender_thread_id"),
                    "spawn_index": event_index,
                }
            )
            continue
        if tool != "wait" or event.get("type") != "item.completed":
            continue
        if item.get("status") != "completed":
            continue
        states = (
            _nested_value(item, ("result", "agents_states"))
            or _nested_value(item, ("output", "agents_states"))
            or item.get("agents_states")
        )
        if not isinstance(states, dict):
            continue
        ids = _thread_ids(item) or {
            thread_id for thread_id in states if isinstance(thread_id, str) and thread_id
        }
        for thread_id in ids:
            state = states.get(thread_id)
            if not isinstance(thread_id, str) or not isinstance(state, dict):
                continue
            wait_records[thread_id] = {
                "wait_index": event_index,
                "sender_thread_id": item.get("sender_thread_id"),
                "status": state.get("status"),
                "message": state.get("message"),
            }

    expected_markers = list(markers)
    marker_contracts_exact = bool(spawned) and all(
        record["marker_count"] == 1 and record["thread_count"] == 1
        for record in spawned
    )
    selected: list[dict[str, Any]] = []
    previous_wait_index = -1
    for expected_marker in expected_markers:
        selected_record = next(
            (
                record
                for record in spawned
                if record.get("marker") == expected_marker
                and isinstance(record.get("thread_id"), str)
                and isinstance(
                    (wait := wait_records.get(record["thread_id"])), dict
                )
                and wait.get("status") == "completed"
                and isinstance(wait.get("wait_index"), int)
                and record["spawn_index"] > previous_wait_index
            ),
            None,
        )
        if selected_record is None:
            break
        selected.append(selected_record)
        selected_wait = wait_records[selected_record["thread_id"]]
        previous_wait_index = selected_wait["wait_index"]

    selected_markers = [record["marker"] for record in selected]
    wait_order = [
        record["marker"]
        if isinstance(record.get("thread_id"), str)
        and record["thread_id"] in wait_records
        else None
        for record in selected
    ]
    waits_completed = len(selected) == len(expected_markers) and all(
        wait_records[record["thread_id"]].get("status") == "completed"
        for record in selected
        if isinstance(record.get("thread_id"), str)
    )
    child_messages_exact = len(selected) == len(expected_markers) and all(
        isinstance(wait_records[record["thread_id"]].get("message"), str)
        and wait_records[record["thread_id"]]["message"].strip() == record["marker"]
        for record in selected
        if isinstance(record.get("thread_id"), str)
    )
    parent_child_joined = parent_thread_id is not None and len(selected) == len(
        expected_markers
    ) and all(
        record.get("sender_thread_id") == parent_thread_id
        and isinstance(record.get("thread_id"), str)
        and wait_records[record["thread_id"]].get("sender_thread_id") == parent_thread_id
        for record in selected
    )
    spawn_then_wait_ordered = len(selected) == len(expected_markers) and all(
        isinstance(record.get("thread_id"), str)
        and record["spawn_index"] < wait_records[record["thread_id"]]["wait_index"]
        and (
            index == 0
            or wait_records[selected[index - 1]["thread_id"]]["wait_index"]
            < record["spawn_index"]
        )
        for index, record in enumerate(selected)
    )

    quality_marker = expected_markers[-1]
    final_spec_record = selected[-2] if len(selected) >= 2 else None
    final_spec_wait_index = (
        wait_records[final_spec_record["thread_id"]].get("wait_index")
        if isinstance(final_spec_record, dict)
        and isinstance(final_spec_record.get("thread_id"), str)
        else None
    )
    quality_spawn_indexes = [
        record["spawn_index"]
        for record in spawned
        if record.get("marker") == quality_marker
    ]
    spec_pass_before_quality = bool(
        isinstance(final_spec_wait_index, int)
        and quality_spawn_indexes
        and all(final_spec_wait_index < index for index in quality_spawn_indexes)
    )

    boundary_fix_between_spec_restarts = True
    if scenario_kind == "boundary":
        first_spec_record = selected[0] if len(selected) >= 1 else None
        restart_spec_record = selected[1] if len(selected) >= 2 else None
        first_spec_wait_index = (
            wait_records[first_spec_record["thread_id"]].get("wait_index")
            if isinstance(first_spec_record, dict)
            and isinstance(first_spec_record.get("thread_id"), str)
            else None
        )
        restart_spec_spawn_index = (
            restart_spec_record.get("spawn_index")
            if isinstance(restart_spec_record, dict)
            else None
        )
        boundary_fix_between_spec_restarts = bool(
            isinstance(first_spec_wait_index, int)
            and isinstance(restart_spec_spawn_index, int)
            and any(
                first_spec_wait_index < index < restart_spec_spawn_index
                for index in implementation_change_indexes
            )
        )

    assertions = {
        "parent_thread_observed": parent_thread_id is not None,
        "expected_spawn_count": len(selected) == len(expected_markers),
        "failed_spawns_produced_no_child_thread": failed_spawns_produced_no_child_thread,
        "no_unexpected_successful_spawn": len(spawned) == len(expected_markers),
        "phase_marker_contracts_exact": marker_contracts_exact,
        "phases_spawned_in_order": selected_markers == expected_markers,
        "waits_completed_in_order": waits_completed and wait_order == expected_markers,
        "child_markers_exact": child_messages_exact,
        "parent_child_thread_joined": parent_child_joined,
        "spawn_then_wait_ordered": spawn_then_wait_ordered,
        "spec_pass_before_quality_dispatch": spec_pass_before_quality,
        "boundary_fix_between_spec_restarts": boundary_fix_between_spec_restarts,
    }
    return assertions, [key for key, value in assertions.items() if not value]


def _positive_packet_shape_diagnostic(message: str | None) -> dict[str, Any]:
    data = _extract_json_object(message) if isinstance(message, str) else None
    if data is None:
        return {
            "json_object_observed": False,
            "schema_valid": False,
            "schema_error_keyword": "json_object_required",
            "missing_required_top_level_keys": [],
            "unexpected_top_level_key_count": 0,
            "source_count": None,
            "claim_count": None,
        }
    try:
        schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
        required_keys = {
            value for value in schema.get("required", []) if isinstance(value, str)
        }
        allowed_keys = {
            value for value in (schema.get("properties") or {}) if isinstance(value, str)
        }
        try:
            jsonschema.validate(data, schema)
            schema_valid = True
            schema_error_keyword: str | None = None
        except jsonschema.ValidationError as exc:
            schema_valid = False
            schema_error_keyword = str(exc.validator) if exc.validator else "validation"
    except (json.JSONDecodeError, OSError):
        required_keys = set()
        allowed_keys = set()
        schema_valid = False
        schema_error_keyword = "schema_unavailable"
    sources = data.get("sources")
    claims = data.get("claims")
    return {
        "json_object_observed": True,
        "schema_valid": schema_valid,
        "schema_error_keyword": schema_error_keyword,
        "missing_required_top_level_keys": sorted(required_keys - set(data)),
        "unexpected_top_level_key_count": len(set(data) - allowed_keys),
        "source_count": len(sources) if isinstance(sources, list) else None,
        "claim_count": len(claims) if isinstance(claims, list) else None,
    }


def _positive_child_packet_message(
    messages: Iterable[str], *, marker: str
) -> str | None:
    expected_summary = _positive_completion_summary(marker)
    candidates = [
        message
        for message in messages
        if isinstance(message, str)
        and (_extract_json_object(message) or {}).get("summary") == expected_summary
    ]
    return candidates[0] if len(candidates) == 1 else None


def _relay_positive_child_packet(
    workspace: Path,
    messages: Iterable[str],
    *,
    marker: str,
    source_truth: PinnedSourceTruth | None,
) -> bool:
    message = _positive_child_packet_message(messages, marker=marker)
    path = workspace / POSITIVE_PACKET_PATH
    if message is None or path.exists() or path.is_symlink() or path.parent.exists():
        return False
    data = _extract_json_object(message)
    if data is None:
        return False
    _, errors = _validate_positive_packet(
        path,
        source_truth=source_truth,
        workspace_root=workspace,
        packet_data=data,
        packet_regular_contained_file=True,
    )
    if errors:
        return False
    path.parent.mkdir(parents=True)
    path.write_text(message, encoding="utf-8")
    return True


def _validate_boundary_message(
    message: str,
    *,
    marker: str,
    refused_path: str,
) -> tuple[dict[str, bool], list[str]]:
    data = _extract_json_object(message) or {}
    expected = _boundary_expected_packet(marker)
    schema_valid = False
    if data:
        try:
            schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
            jsonschema.validate(data, schema)
            schema_valid = True
        except (json.JSONDecodeError, jsonschema.ValidationError, OSError):
            pass
    persistence = data.get("persistence") or {}
    contexts = data.get("context_inspected_not_sources") or []
    assertions = {
        "structured_boundary_result": bool(data),
        "canonical_schema_valid": schema_valid,
        "exact_boundary_packet": data == expected,
        "source_mode_preserved": data.get("source_mode") == "github_open_source_research",
        "source_scope_enforced": data.get("source_scope_enforced") is True,
        "fixture_context_only": contexts
        == [
            {
                "path": BOUNDARY_FIXTURE_PATH,
                "reason": "unapproved local fixture classified as context_only",
            }
        ],
        "persistence_not_authorized": persistence.get("authorized") is False,
        "no_actual_path": persistence.get("actual_path") is None,
        "refused_path_recorded": persistence.get("requested_path") == refused_path
        and persistence.get("refused_paths") == [refused_path],
        "no_unapproved_sources_or_claims": data.get("sources") == []
        and data.get("claims") == [],
        "summary_marker_and_refusal": data.get("summary") == expected["summary"],
    }
    errors = [key for key, value in assertions.items() if not value]
    return assertions, errors


def _write_fixture(
    workspace: Path,
    scenario: ScenarioSpec,
    *,
    source_truth: PinnedSourceTruth | None,
) -> None:
    (workspace / "README.md").write_text(
        "# Synthetic Codex Runtime Fixture\n\nNo user or repository source content is included.\n",
        encoding="utf-8",
    )
    if scenario.scenario_kind == "positive":
        if source_truth is None:
            raise RuntimeError("positive source mirror requires pinned source truth")
        path = workspace / POSITIVE_SOURCE_MIRROR_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source_truth.content_bytes)
        path.chmod(0o444)
        manifest_path = workspace / POSITIVE_SOURCE_MANIFEST_PATH
        manifest_path.write_bytes(_canonical_integrity_manifest_bytes(source_truth))
        manifest_path.chmod(0o444)
    elif scenario.scenario_kind == "boundary":
        path = workspace / BOUNDARY_FIXTURE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "UNAPPROVED LOCAL FIXTURE: classify as context only; never cite as a source.\n",
            encoding="utf-8",
        )


def _write_vc02_fixture(workspace: Path) -> None:
    (workspace / "README.md").write_text(
        "# Synthetic VC-02 Codex Runtime Fixture\n\nNo user or repository source content is included.\n",
        encoding="utf-8",
    )
    fixture_root = workspace / "fixture"
    fixture_root.mkdir(parents=True, exist_ok=True)
    (workspace / VC02_CHANGE_DIFF_PATH).write_text(
        "--- a/fixture/implementation.txt\n"
        "+++ b/fixture/implementation.txt\n"
        "@@\n"
        "-draft\n"
        "+completed behavior\n",
        encoding="utf-8",
    )
    (workspace / VC02_SPEC_PATH).write_text(
        "# Approved synthetic requirement\n\nThe implementation must expose the documented behavior.\n",
        encoding="utf-8",
    )
    (workspace / VC02_STANDARDS_PATH).write_text(
        "# Documented synthetic standard\n\nNames must make the behavior clear to a reviewer.\n",
        encoding="utf-8",
    )
    (workspace / VC02_IMPLEMENTATION_PATH).write_text(
        "draft behavior\n",
        encoding="utf-8",
    )


def _vc02_component_slug(component_id: str) -> str:
    return component_id.rsplit(".", 1)[-1].replace("-", "_")


def _current_vc02_adapter_artifact_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for component_id, artifact in VC02_DIST_CODEX_ARTIFACTS.items():
        if not _is_regular_file_within(artifact, REPO_ROOT):
            raise RuntimeError(f"built Codex artifact is unavailable: {component_id}")
        hashes[component_id] = _sha256_file(artifact)
    return hashes


def _vc02_adapter_artifact_hash(adapter_artifact_hashes: dict[str, str]) -> str:
    if set(adapter_artifact_hashes) != set(VC02_COMPONENT_IDS):
        raise ValueError("VC-02 adapter artifact hash set drift")
    return _sha256_text(
        json.dumps(adapter_artifact_hashes, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )


def _vc02_static_gate(
    run_root: Path,
) -> tuple[
    dict[str, Path],
    dict[str, str],
    dict[str, str],
    dict[str, dict[str, Any]],
    dict[str, bool],
]:
    results: dict[str, bool] = {}
    current_slice_components = (*VC02_COMPONENT_IDS, *VC02_STATIC_ONLY_COMPONENT_IDS)
    for component_id in current_slice_components:
        slug = _vc02_component_slug(component_id)
        for name, argv in (
            (
                f"canonical_validation_{slug}",
                [
                    sys.executable,
                    "scripts/components/validate.py",
                    "--component",
                    component_id,
                ],
            ),
            (
                f"declared_target_build_{slug}",
                [sys.executable, "scripts/adapters/build.py", "--component", component_id],
            ),
            (
                f"declared_target_check_{slug}",
                [
                    sys.executable,
                    "scripts/adapters/build.py",
                    "--component",
                    component_id,
                    "--check",
                ],
            ),
        ):
            result = _run(argv, cwd=REPO_ROOT, timeout_seconds=180)
            results[name] = result.returncode == 0
            if result.returncode != 0:
                raise RuntimeError(f"static gate failed: {name}")

    for name, argv in (
        (
            "engineering_profile_build",
            [sys.executable, "scripts/adapters/build.py", "--profile", "engineering"],
        ),
        (
            "engineering_profile_target_check",
            [
                sys.executable,
                "scripts/adapters/build.py",
                "--profile",
                "engineering",
                "--check",
            ],
        ),
    ):
        result = _run(argv, cwd=REPO_ROOT, timeout_seconds=180)
        results[name] = result.returncode == 0
        if result.returncode != 0:
            raise RuntimeError(f"static gate failed: {name}")

    adapter_artifact_hashes = _current_vc02_adapter_artifact_hashes()
    plan_paths: dict[str, Path] = {}
    plan_hashes: dict[str, str] = {}
    for component_id in VC02_COMPONENT_IDS:
        slug = _vc02_component_slug(component_id)
        plan_result = _run(
            [
                sys.executable,
                "scripts/install/plan.py",
                "--component",
                component_id,
                "--scope",
                "project",
                "--mode",
                "apply",
                "--format",
                "json",
            ],
            cwd=REPO_ROOT,
            timeout_seconds=120,
        )
        results[f"standalone_apply_plan_{slug}"] = plan_result.returncode == 0
        if plan_result.returncode != 0:
            raise RuntimeError(f"static gate failed: standalone_apply_plan_{slug}")
        plan = json.loads(plan_result.stdout)
        if plan.get("mode") != "apply" or plan.get("components") != [component_id]:
            raise RuntimeError(f"standalone apply plan component drift: {component_id}")
        plan_path = run_root / f"{slug}.install-plan.apply.json"
        plan_path.write_text(plan_result.stdout, encoding="utf-8")
        plan_paths[component_id] = plan_path
        plan_hashes[component_id] = _sha256_text(plan_result.stdout)

    static_only_install_evidence: dict[str, dict[str, Any]] = {}
    for component_id in VC02_STATIC_ONLY_COMPONENT_IDS:
        slug = _vc02_component_slug(component_id)
        install_scope = VC02_STATIC_ONLY_INSTALL_SCOPES[component_id]
        plan_result = _run(
            [
                sys.executable,
                "scripts/install/plan.py",
                "--component",
                component_id,
                "--scope",
                install_scope,
                "--mode",
                "apply",
                "--format",
                "json",
            ],
            cwd=REPO_ROOT,
            timeout_seconds=120,
        )
        results[f"static_only_apply_plan_{slug}"] = plan_result.returncode == 0
        if plan_result.returncode != 0:
            raise RuntimeError(f"static gate failed: static_only_apply_plan_{slug}")
        plan = json.loads(plan_result.stdout)
        if plan.get("mode") != "apply" or plan.get("components") != [component_id]:
            raise RuntimeError(f"static-only apply plan component drift: {component_id}")
        plan_path = run_root / f"{slug}.static-only.install-plan.apply.json"
        plan_path.write_text(plan_result.stdout, encoding="utf-8")
        isolated_root = run_root / f"{slug}.static-only.install-root"
        isolated_root.mkdir(parents=True, exist_ok=True)
        try:
            install = _apply_and_verify(plan_path, isolated_root)
            installed_artifact_path = VC02_STATIC_ONLY_INSTALLED_CODEX_ARTIFACTS[
                component_id
            ]
            installed_artifact = isolated_root / installed_artifact_path
            artifact_present = _is_regular_file_within(installed_artifact, isolated_root)
            installed_artifact_hash = (
                _sha256_file(installed_artifact) if artifact_present else None
            )
            adapter_artifact = VC02_STATIC_ONLY_DIST_CODEX_ARTIFACTS[component_id]
            adapter_artifact_present = _is_regular_file_within(adapter_artifact, REPO_ROOT)
            adapter_artifact_hash = (
                _sha256_file(adapter_artifact) if adapter_artifact_present else None
            )
            artifact_hash_matches_adapter = (
                installed_artifact_hash is not None
                and installed_artifact_hash == adapter_artifact_hash
            )
            results[f"static_only_apply_{slug}"] = install["apply_passed"]
            results[f"static_only_verify_{slug}"] = install["verify_passed"]
            results[f"static_only_codex_artifact_{slug}"] = artifact_present
            results[f"static_only_hash_matches_adapter_{slug}"] = artifact_hash_matches_adapter
            if not all(
                (
                    install["apply_passed"],
                    install["verify_passed"],
                    artifact_present,
                    adapter_artifact_present,
                    artifact_hash_matches_adapter,
                )
            ):
                raise RuntimeError(f"static-only isolated install gate failed: {component_id}")
            static_only_install_evidence[component_id] = {
                "install_scope": install_scope,
                "install_plan_sha256": _sha256_text(plan_result.stdout),
                "apply_passed": install["apply_passed"],
                "verify_passed": install["verify_passed"],
                "installed_codex_artifact_path": installed_artifact_path.as_posix(),
                "installed_codex_artifact_hash": installed_artifact_hash,
                "adapter_artifact_hash": adapter_artifact_hash,
                "installed_codex_artifact_hash_matches_adapter": artifact_hash_matches_adapter,
                "live_runtime_not_claimed": True,
            }
        finally:
            shutil.rmtree(isolated_root, ignore_errors=True)
        isolated_workspace_removed = not isolated_root.exists()
        results[f"static_only_cleanup_{slug}"] = isolated_workspace_removed
        if not isolated_workspace_removed:
            raise RuntimeError(f"static-only isolated install cleanup failed: {component_id}")
        static_only_install_evidence[component_id]["isolated_workspace_removed"] = (
            isolated_workspace_removed
        )
    return (
        plan_paths,
        plan_hashes,
        adapter_artifact_hashes,
        static_only_install_evidence,
        results,
    )


def _vc02_completed_spawn_count(stdout: str) -> int:
    return sum(
        1
        for event in _event_objects(stdout)
        if event.get("type") == "item.completed"
        and isinstance((item := event.get("item")), dict)
        and item.get("type") == "collab_tool_call"
        and (item.get("tool") or item.get("name")) == "spawn_agent"
        and item.get("status") == "completed"
    )


def _is_native_executable(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        if not stat.S_ISREG(resolved.lstat().st_mode) or not os.access(resolved, os.X_OK):
            return False
        with resolved.open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic in {
        b"\x7fELF",
        b"MZ\x90\x00",
        bytes.fromhex("feedface"),
        bytes.fromhex("feedfacf"),
        bytes.fromhex("cefaedfe"),
        bytes.fromhex("cffaedfe"),
    }


def _resolve_codex_native_executable(
    *,
    launcher: Path | None = None,
    platform_name: str | None = None,
    machine: str | None = None,
) -> Path:
    launcher_value = launcher or (
        Path(value) if (value := shutil.which("codex", path=os.environ.get("PATH"))) else None
    )
    if launcher_value is None:
        raise RuntimeError("Codex executable is unavailable")
    try:
        resolved_launcher = launcher_value.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("Codex launcher resolution failed") from exc
    if _is_native_executable(resolved_launcher):
        return resolved_launcher

    host = platform_name or sys.platform
    architecture = (machine or os.uname().machine).lower()
    target = {
        ("darwin", "arm64"): ("codex-darwin-arm64", "aarch64-apple-darwin"),
        ("darwin", "aarch64"): ("codex-darwin-arm64", "aarch64-apple-darwin"),
        ("darwin", "x86_64"): ("codex-darwin-x64", "x86_64-apple-darwin"),
        ("linux", "arm64"): ("codex-linux-arm64", "aarch64-unknown-linux-musl"),
        ("linux", "aarch64"): ("codex-linux-arm64", "aarch64-unknown-linux-musl"),
        ("linux", "x86_64"): ("codex-linux-x64", "x86_64-unknown-linux-musl"),
    }.get((host, architecture))
    if target is None or resolved_launcher.name != "codex.js":
        raise RuntimeError("Codex native executable target is unsupported")

    package_name, target_triple = target
    package_root = resolved_launcher.parent.parent.resolve(strict=True)
    candidate = (
        package_root
        / "node_modules/@openai"
        / package_name
        / "vendor"
        / target_triple
        / "bin/codex"
    )
    try:
        resolved_candidate = candidate.resolve(strict=True)
        resolved_candidate.relative_to(package_root)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Codex native executable escaped its package root") from exc
    if not _is_native_executable(resolved_candidate):
        raise RuntimeError("Codex native executable is missing or invalid")
    return resolved_candidate


def _codex_version() -> str:
    executable = _resolve_codex_native_executable()
    result = _run([str(executable), "--version"], cwd=REPO_ROOT, timeout_seconds=30)
    if result.returncode != 0:
        raise RuntimeError("Codex version observation failed")
    value = result.stdout.strip()
    if not value:
        raise RuntimeError("Codex version observation was empty")
    return value


def _real_auth_path() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "auth.json"


def _current_codex_adapter_hash() -> str:
    if not _is_regular_file_within(DIST_CODEX_ARTIFACT, REPO_ROOT):
        raise RuntimeError("built Codex reference-curator adapter artifact is unavailable")
    return _sha256_file(DIST_CODEX_ARTIFACT)


def _static_gate(run_root: Path) -> tuple[Path, str, dict[str, bool]]:
    results: dict[str, bool] = {}
    commands = [
        (
            "canonical_validation",
            [sys.executable, "scripts/components/validate.py", "--component", COMPONENT_ID],
        ),
        (
            "declared_target_build",
            [sys.executable, "scripts/adapters/build.py", "--component", COMPONENT_ID],
        ),
        (
            "declared_target_check",
            [sys.executable, "scripts/adapters/build.py", "--component", COMPONENT_ID, "--check"],
        ),
    ]
    for name, argv in commands:
        result = _run(argv, cwd=REPO_ROOT, timeout_seconds=120)
        results[name] = result.returncode == 0
        if result.returncode != 0:
            raise RuntimeError(f"static gate failed: {name}")

    plan_result = _run(
        [
            sys.executable,
            "scripts/install/plan.py",
            "--component",
            COMPONENT_ID,
            "--scope",
            "project",
            "--mode",
            "apply",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        timeout_seconds=120,
    )
    results["standalone_apply_plan"] = plan_result.returncode == 0
    if plan_result.returncode != 0:
        raise RuntimeError("static gate failed: standalone_apply_plan")
    plan = json.loads(plan_result.stdout)
    if plan.get("mode") != "apply" or plan.get("components") != [COMPONENT_ID]:
        raise RuntimeError("standalone apply plan did not select exactly the current component")
    plan_path = run_root / "install-plan.apply.json"
    plan_path.write_text(plan_result.stdout, encoding="utf-8")
    return plan_path, _sha256_text(plan_result.stdout), results


def _apply_and_verify(plan_path: Path, workspace: Path) -> dict[str, bool]:
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
        timeout_seconds=120,
    )
    verify_result = _run(
        [
            sys.executable,
            "scripts/install/verify.py",
            str(plan_path),
            "--target-root",
            str(workspace),
        ],
        cwd=REPO_ROOT,
        timeout_seconds=120,
    )
    return {
        "apply_passed": apply_result.returncode == 0,
        "verify_passed": verify_result.returncode == 0,
    }


def _failure_category(assertions: dict[str, bool]) -> str | None:
    for prefix, category in (
        ("install_", "install"),
        ("runtime_", "runtime"),
        ("loader_", "loader"),
        ("behavior_", "behavior"),
        ("mutation_", "mutation"),
        ("privacy_", "privacy"),
        ("cleanup_", "cleanup"),
    ):
        if any(not value for key, value in assertions.items() if key.startswith(prefix)):
            return category
    return None


def _codex_runtime_blocker_category(stderr: str) -> str | None:
    lowered = stderr.lower()
    if any(
        marker in lowered
        for marker in (
            "access token could not be refreshed",
            "authentication failed",
            "not logged in",
            "please log in",
            "please sign in again",
            "unauthorized",
            "api key auth is missing",
        )
    ):
        return "auth_account"
    if any(
        marker in lowered
        for marker in (
            "usage limit",
            "rate limit",
            "quota exceeded",
            "service unavailable",
            "temporarily unavailable",
        )
    ):
        return "runtime_service"
    return None


def _run_scenario(
    *,
    run_root: Path,
    plan_path: Path,
    install_plan_hash: str,
    codex_version: str,
    model: str,
    timeout_seconds: int,
    scenario: ScenarioSpec,
    source_truth: PinnedSourceTruth | None,
    expected_adapter_artifact_hash: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    scenario_root = Path(tempfile.mkdtemp(prefix=f"harnesskit-{scenario.scenario_id}."))
    workspace = scenario_root / "workspace"
    home = scenario_root / "home"
    codex_home = scenario_root / "codex-home"
    temp_dir = scenario_root / "tmp"
    transient = scenario_root / "transient"
    for path in (workspace, home, codex_home, temp_dir, transient):
        path.mkdir(parents=True, exist_ok=True)

    auth_link = codex_home / "auth.json"
    auth_source = _real_auth_path()
    stdout = ""
    stderr = ""
    sanitized_events: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    cleanup_auth_removed = False
    cleanup_workspace_removed = False
    cleanup_process_group_absent = True
    failure_category = "controller"
    failure_assertions: dict[str, bool] = {"runtime_controller_completed": False}
    try:
        git_init = _run(["git", "init", "-q"], cwd=workspace, timeout_seconds=30)
        if git_init.returncode != 0:
            raise RuntimeError("synthetic fixture git initialization failed")
        install = _apply_and_verify(plan_path, workspace)
        installed_artifact = workspace / INSTALLED_CODEX_ARTIFACT
        artifact_present = _is_regular_file_within(installed_artifact, workspace)
        installed_artifact_hash = _sha256_file(installed_artifact) if artifact_present else ""
        install_assertions = {
            "install_apply_passed": install["apply_passed"],
            "install_verify_passed": install["verify_passed"],
            "install_codex_artifact_present": artifact_present,
            "install_codex_artifact_hash_matches_adapter": (
                bool(installed_artifact_hash)
                and (
                    expected_adapter_artifact_hash is None
                    or installed_artifact_hash == expected_adapter_artifact_hash
                )
            ),
        }
        if not all(install_assertions.values()):
            failure_category = "install"
            failure_assertions = install_assertions
            raise RuntimeError("isolated install gate failed")

        _write_fixture(
            workspace,
            scenario,
            source_truth=source_truth if scenario.scenario_kind == "positive" else None,
        )
        before = _tree_manifest(workspace)
        isolation_before = _tree_manifest(scenario_root)
        git_before = _git_metadata_manifest(workspace)

        failure_category = "auth"
        failure_assertions = {"runtime_auth_source_available": auth_source.is_file()}
        if not auth_source.is_file():
            raise RuntimeError("Codex auth source is unavailable")
        runtime_env = _sanitized_runtime_env(
            os.environ.copy(),
            home=home,
            codex_home=codex_home,
            temp_dir=temp_dir,
        )
        resolvable_forbidden_runtimes = sorted(
            runtime
            for runtime in FORBIDDEN_MODEL_RUNTIMES
            if shutil.which(runtime, path=runtime_env["PATH"]) is not None
        )
        if resolvable_forbidden_runtimes:
            failure_category = "privacy"
            failure_assertions = {"privacy_forbidden_runtimes_unresolvable": False}
            raise RuntimeError("sanitized PATH exposes a forbidden model runtime")
        failure_category = "runtime_tool"
        failure_assertions = {"runtime_native_codex_available": False}
        codex_executable = _resolve_codex_native_executable()
        failure_category = "runtime"
        failure_assertions = {"runtime_process_completed": False}
        last_message_path = transient / "last-message.txt"
        runtime_argv = _codex_argv(
            workspace=workspace,
            last_message=last_message_path,
            prompt=scenario.prompt,
            model=model,
            enable_search=scenario.enable_search,
        )
        runtime_argv[0] = str(codex_executable)
        auth_link.symlink_to(auth_source)
        try:
            runtime_outcome = _run_process_group(
                runtime_argv,
                cwd=workspace,
                env=runtime_env,
                timeout_seconds=timeout_seconds,
            )
        finally:
            if auth_link.is_symlink() or auth_link.exists():
                auth_link.unlink()
        cleanup_auth_removed = not auth_link.exists()
        cleanup_process_group_absent = runtime_outcome.group_absent_after_cleanup
        runtime = runtime_outcome.completed
        stdout = runtime.stdout
        stderr = runtime.stderr
        runtime_blocker_category = (
            _codex_runtime_blocker_category(stderr) if runtime.returncode != 0 else None
        )
        last_message = (
            last_message_path.read_text(encoding="utf-8")
            if last_message_path.is_file()
            else ""
        )
        collab = _collab_evidence(stdout, scenario.subagent_marker)
        sanitized_events = _sanitize_events(stdout, scenario_id=scenario.scenario_id)
        forbidden_commands = _forbidden_runtime_commands(stdout)
        forbidden_hash_command_observation = _forbidden_hash_command_observation(stdout)
        child_manifest_contract_sent = (
            _child_manifest_contract_sent(
                stdout,
                marker=scenario.subagent_marker,
            )
            if scenario.scenario_kind == "positive"
            else None
        )
        forbidden_observed_processes = _observed_forbidden_processes(runtime_outcome)
        child_codex_process_count = _observed_child_codex_process_count(runtime_outcome)
        forbidden_runtime_processes = sorted(
            set(forbidden_commands) | set(forbidden_observed_processes)
        )
        child_packet_message = (
            _positive_child_packet_message(
                collab.completed_messages,
                marker=scenario.subagent_marker,
            )
            if scenario.scenario_kind == "positive"
            else None
        )
        packet_shape_diagnostic = (
            _positive_packet_shape_diagnostic(child_packet_message)
            if scenario.scenario_kind == "positive"
            else None
        )
        child_packet_relayed = (
            _relay_positive_child_packet(
                workspace,
                collab.completed_messages,
                marker=scenario.subagent_marker,
                source_truth=source_truth,
            )
            if scenario.scenario_kind == "positive"
            else False
        )
        after = _tree_manifest(workspace)
        isolation_after = _tree_manifest(scenario_root)
        git_after = _git_metadata_manifest(workspace)
        changed_paths = _tree_diff(before, after)
        isolation_changed_paths = _tree_diff(isolation_before, isolation_after)
        git_changed_paths = _tree_diff(git_before, git_after)
        allowed_workspace_manifest_paths = _manifest_paths_with_parents(
            scenario.allowed_changed_paths
        )
        unexpected_paths = sorted(set(changed_paths) - allowed_workspace_manifest_paths)
        allowed_isolation_manifest_paths = {
            *(f"workspace/{path}" for path in allowed_workspace_manifest_paths),
            "transient/last-message.txt",
        }
        isolation_change_summary = _summarize_isolation_changes(
            isolation_changed_paths,
            controller_allowed_paths=allowed_isolation_manifest_paths,
        )
        unexpected_isolation_paths = isolation_change_summary[
            "unexpected_changed_paths"
        ]

        if scenario.scenario_kind == "positive":
            behavior_assertions, behavior_errors = _validate_positive_packet(
                workspace / POSITIVE_PACKET_PATH,
                source_truth=source_truth,
                workspace_root=workspace,
            )
            behavior_assertions["child_packet_relayed"] = child_packet_relayed
            behavior_errors = [
                key for key, value in behavior_assertions.items() if not value
            ]
            integrity_manifest_evidence = _integrity_manifest_evidence(
                workspace,
                source_truth=source_truth,
            )
            integrity_manifest_evidence.update(
                {
                    "child_mirror_inspection_receipt_verified": behavior_assertions[
                        "child_mirror_inspection_receipt_verified"
                    ],
                    "selected_locator_hashes_verified": behavior_assertions[
                        "selected_locator_hashes_verified"
                    ],
                }
            )
            observed_summary = (
                "approved child packet relayed and validated against schema, pinned identity, "
                "citations, and fact/inference split"
                if not behavior_errors
                else "positive packet validation failed"
            )
            required_path_present = POSITIVE_PACKET_PATH in changed_paths
            subagent_completion_contract = child_packet_message is not None
        else:
            boundary_message = next(
                (message for message in collab.completed_messages if scenario.subagent_marker in message),
                "",
            )
            behavior_assertions, behavior_errors = _validate_boundary_message(
                boundary_message,
                marker=scenario.subagent_marker,
                refused_path=BOUNDARY_REFUSED_PATH,
            )
            observed_summary = (
                "local fixture remained context-only and unauthorized persistence was refused"
                if not behavior_errors
                else "boundary refusal validation failed"
            )
            required_path_present = True
            subagent_completion_contract = not behavior_errors
            integrity_manifest_evidence = None
        if runtime_blocker_category is not None:
            observed_summary = "Codex auth, account, or service state prevented runtime proof"

        child_manifest_contract_assertions: dict[str, bool] = {}
        if scenario.scenario_kind == "positive":
            child_manifest_contract_assertions = {
                "loader_child_manifest_contract_sent": bool(
                    child_manifest_contract_sent
                ),
                "privacy_no_shell_hash_command_execution": (
                    forbidden_hash_command_observation["hash_command_execution_count"] == 0
                ),
            }

        assertions: dict[str, bool] = {
            **install_assertions,
            **child_manifest_contract_assertions,
            "runtime_command_passed": runtime.returncode == 0,
            "runtime_not_timed_out": not runtime_outcome.timed_out,
            "runtime_parent_marker_exact": last_message.strip() == scenario.parent_marker,
            "runtime_requested_model_explicit": (
                "--model" in runtime_argv
                and runtime_argv[runtime_argv.index("--model") + 1] == model
            ),
            "loader_spawn_observed": collab.completed,
            "loader_marker_contract_sent": collab.marker_contract_sent,
            "loader_wait_completed": collab.contract_completed,
            "loader_subagent_result_observed": collab.marker_found,
            "loader_subagent_completion_contract": subagent_completion_contract,
            "loader_exact_single_thread": collab.exact_single_thread,
            "loader_exact_single_spawn_attempt": (
                collab.spawn_attempt_count == 1
                and collab.failed_spawn_attempt_count == 0
            ),
            "loader_parent_child_thread_joined": collab.parent_thread_joined,
            "loader_spawn_wait_marker_ordered": collab.ordered,
            "behavior_contract_passed": not behavior_errors,
            "mutation_only_allowed_paths": not unexpected_paths,
            "mutation_isolated_roots_only_allowed_paths": not unexpected_isolation_paths,
            "mutation_required_positive_path_present": required_path_present,
            "mutation_git_metadata_unchanged": not git_changed_paths,
            "privacy_no_non_codex_runtime_dispatch": not forbidden_runtime_processes,
            "privacy_child_codex_processes_bounded_by_spawned_threads": (
                _child_codex_processes_are_bounded(
                    child_codex_process_count=child_codex_process_count,
                    spawned_thread_count=collab.spawned_thread_count,
                )
            ),
            "privacy_forbidden_runtimes_unresolvable": not resolvable_forbidden_runtimes,
            "privacy_process_observation_available": runtime_outcome.process_observation_available,
            "privacy_root_process_observed": runtime_outcome.root_process_observed,
            "privacy_auth_link_removed_after_runtime": cleanup_auth_removed,
            "cleanup_process_group_absent": cleanup_process_group_absent,
            "cleanup_no_unexpected_process_group_termination": not (
                not runtime_outcome.timed_out
                and (runtime_outcome.term_sent or runtime_outcome.kill_sent)
            ),
        }
        assertions.update(
            {f"behavior_{key}": value for key, value in behavior_assertions.items()}
        )
        result = {
            "fixture_id": f"synthetic-{scenario.scenario_id}",
            "captured_at": _captured_at(),
            "runtime_target": "codex",
            "component_id": COMPONENT_ID,
            "slice_id": "VC-01",
            "scenario_id": scenario.scenario_id,
            "scenario_kind": scenario.scenario_kind,
            "observed_codex_version": codex_version,
            **_model_evidence(model),
            "model_runtime_observation": {
                "controller_invocation_count": 1,
                "controller_fallback_path_configured": False,
                "backend_fallback_observation": (
                    "unavailable_in_codex_exec_json_0.137.0"
                ),
            },
            "isolated_workspace_identity": _sha256_text(str(workspace.resolve())),
            "isolated_workspace_hash_before": _tree_hash(before),
            "isolated_workspace_hash_after": _tree_hash(after),
            "install_plan_hash": install_plan_hash,
            "installed_codex_artifact_path": INSTALLED_CODEX_ARTIFACT.as_posix(),
            "installed_codex_artifact_hash": installed_artifact_hash,
            "prompt_purpose": scenario.prompt_purpose,
            "prompt_hash": _sha256_text(scenario.prompt),
            "prompt_revision_id": scenario.prompt_revision_id,
            "expected_behavior": scenario.expected_behavior,
            "observed_behavior_summary": observed_summary,
            "loader_evidence": {
                "spawn_observed": collab.completed,
                "marker_contract_sent": collab.marker_contract_sent,
                "wait_completed": collab.contract_completed,
                "subagent_result_observed": collab.marker_found,
                "spawn_attempt_count": collab.spawn_attempt_count,
                "failed_spawn_attempt_count": collab.failed_spawn_attempt_count,
                "spawned_thread_count": collab.spawned_thread_count,
                "completed_thread_count": collab.completed_thread_count,
                "exact_single_thread": collab.exact_single_thread,
                "requested_agent_type": EXPECTED_AGENT_TYPE,
                "agent_type_observation": collab.agent_type_observation,
                "parent_thread_joined": collab.parent_thread_joined,
                "ordered": collab.ordered,
                "child_manifest_contract_sent": child_manifest_contract_sent,
                "subagent_result_diagnostic": _subagent_result_diagnostic(
                    collab.completed_messages,
                    marker=scenario.subagent_marker,
                ),
            },
            "packet_delivery_evidence": {
                "mode": (
                    "controller_relay_from_completed_subagent_result"
                    if scenario.scenario_kind == "positive"
                    else "not_applicable"
                ),
                "child_result_packet_observed": child_packet_message is not None,
                "controller_relay_to_authorized_path": child_packet_relayed,
                "raw_child_result_persisted": False,
                "packet_shape_diagnostic": packet_shape_diagnostic,
                "forbidden_hash_command_observation": (
                    forbidden_hash_command_observation
                    if scenario.scenario_kind == "positive"
                    else None
                ),
            },
            "integrity_manifest_evidence": integrity_manifest_evidence,
            "before_after_tree": {
                "allowed_changed_paths": list(scenario.allowed_changed_paths),
                "actual_changed_paths": changed_paths,
                "unexpected_changed_paths": unexpected_paths,
            },
            "before_after_git": {
                "metadata_hash_before": _tree_hash(git_before),
                "metadata_hash_after": _tree_hash(git_after),
                "actual_changed_paths": git_changed_paths,
            },
            "before_after_isolation_root": {
                "manifest_hash_before": _tree_hash(isolation_before),
                "manifest_hash_after": _tree_hash(isolation_after),
                "controller_allowed_changed_paths": sorted(
                    allowed_isolation_manifest_paths
                ),
                **isolation_change_summary,
            },
            "requested_forbidden_paths": (
                [BOUNDARY_REFUSED_PATH] if scenario.scenario_kind == "boundary" else []
            ),
            "observed_filesystem_changed_paths": changed_paths,
            "observed_git_metadata_changed_paths": git_changed_paths,
            "forbidden_runtime_processes": forbidden_runtime_processes,
            "observed_child_codex_process_count": child_codex_process_count,
            "expected_vs_actual_assertions": assertions,
            "privacy": {
                "synthetic_fixture_only": True,
                "external_mutation_credentials_removed": True,
                "raw_prompt_persisted": False,
                "raw_model_response_persisted": False,
                "raw_manifest_persisted": False,
                "raw_environment_persisted": False,
                "auth_copied": False,
                "runtime_system_path": RUNTIME_SYSTEM_PATH,
            },
            "process_group_cleanup": {
                "timed_out": runtime_outcome.timed_out,
                "term_sent": runtime_outcome.term_sent,
                "kill_sent": runtime_outcome.kill_sent,
                "process_reaped": runtime_outcome.process_reaped,
                "group_absent_after_cleanup": runtime_outcome.group_absent_after_cleanup,
                "process_observation_available": runtime_outcome.process_observation_available,
                "root_process_observed": runtime_outcome.root_process_observed,
                "observed_executables": list(runtime_outcome.observed_executables),
            },
            "stderr_observation": (
                "empty" if not stderr.strip() else "non-empty-not-persisted"
            ),
            "runtime_blocker_observation": {
                "category": runtime_blocker_category,
                "stderr_persisted": False,
            },
            "verdict": (
                "BLOCKED"
                if runtime_blocker_category is not None
                else "PASS" if all(assertions.values()) else "NEEDS_WORK"
            ),
            "failure_category": runtime_blocker_category or _failure_category(assertions),
        }
    except Exception as exc:  # evidence must survive a fixed-scenario failure
        result = {
            "fixture_id": f"synthetic-{scenario.scenario_id}",
            "captured_at": _captured_at(),
            "runtime_target": "codex",
            "component_id": COMPONENT_ID,
            "slice_id": "VC-01",
            "scenario_id": scenario.scenario_id,
            "scenario_kind": scenario.scenario_kind,
            "observed_codex_version": codex_version,
            **_model_evidence(model),
            "prompt_purpose": scenario.prompt_purpose,
            "prompt_hash": _sha256_text(scenario.prompt),
            "prompt_revision_id": scenario.prompt_revision_id,
            "expected_behavior": scenario.expected_behavior,
            "observed_behavior_summary": "scenario controller failed before a complete verdict",
            "expected_vs_actual_assertions": failure_assertions,
            "privacy": {
                "synthetic_fixture_only": True,
                "raw_prompt_persisted": False,
                "raw_model_response_persisted": False,
                "auth_copied": False,
            },
            "controller_error": type(exc).__name__,
            "verdict": (
                "BLOCKED"
                if failure_category in {"auth", "runtime_tool"}
                else "NEEDS_WORK"
            ),
            "failure_category": failure_category,
        }
    finally:
        if auth_link.is_symlink() or auth_link.exists():
            auth_link.unlink()
        cleanup_auth_removed = not auth_link.exists()
        shutil.rmtree(scenario_root, ignore_errors=True)
        cleanup_workspace_removed = not scenario_root.exists()

    result["cleanup"] = {
        "auth_link_removed": cleanup_auth_removed,
        "isolated_workspace_removed": cleanup_workspace_removed,
        "process_group_absent": cleanup_process_group_absent,
    }
    result.setdefault("expected_vs_actual_assertions", {})["cleanup_auth_link_removed"] = cleanup_auth_removed
    result["expected_vs_actual_assertions"]["cleanup_workspace_removed"] = cleanup_workspace_removed
    result["expected_vs_actual_assertions"]["cleanup_process_group_absent"] = cleanup_process_group_absent
    if not cleanup_auth_removed or not cleanup_workspace_removed or not cleanup_process_group_absent:
        result["verdict"] = "NEEDS_WORK"
        result["failure_category"] = "cleanup"
    return result, sanitized_events


def _run_vc02_scenario(
    *,
    run_root: Path,
    plan_path: Path,
    install_plan_hash: str,
    codex_version: str,
    model: str,
    timeout_seconds: int,
    scenario: ScenarioSpec,
    expected_adapter_artifact_hash: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    del run_root  # VC-02 plan artifacts were produced by the controller before isolation.
    scenario_root = Path(tempfile.mkdtemp(prefix=f"harnesskit-{scenario.scenario_id}."))
    workspace = scenario_root / "workspace"
    home = scenario_root / "home"
    codex_home = scenario_root / "codex-home"
    temp_dir = scenario_root / "tmp"
    transient = scenario_root / "transient"
    for path in (workspace, home, codex_home, temp_dir, transient):
        path.mkdir(parents=True, exist_ok=True)

    auth_link = codex_home / "auth.json"
    auth_source = _real_auth_path()
    sanitized_events: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    cleanup_auth_removed = False
    cleanup_workspace_removed = False
    cleanup_process_group_absent = True
    failure_category = "controller"
    failure_assertions: dict[str, bool] = {"runtime_controller_completed": False}
    try:
        git_init = _run(["git", "init", "-q"], cwd=workspace, timeout_seconds=30)
        if git_init.returncode != 0:
            raise RuntimeError("synthetic fixture git initialization failed")
        install = _apply_and_verify(plan_path, workspace)
        installed_artifact_path = VC02_INSTALLED_CODEX_ARTIFACTS[scenario.component_id]
        installed_artifact = workspace / installed_artifact_path
        artifact_present = _is_regular_file_within(installed_artifact, workspace)
        installed_artifact_hash = _sha256_file(installed_artifact) if artifact_present else ""
        install_assertions = {
            "install_apply_passed": install["apply_passed"],
            "install_verify_passed": install["verify_passed"],
            "install_codex_artifact_present": artifact_present,
            "install_codex_artifact_hash_matches_adapter": (
                bool(installed_artifact_hash)
                and installed_artifact_hash == expected_adapter_artifact_hash
            ),
        }
        if not all(install_assertions.values()):
            failure_category = "install"
            failure_assertions = install_assertions
            raise RuntimeError("isolated install gate failed")

        _write_vc02_fixture(workspace)
        before = _tree_manifest(workspace)
        isolation_before = _tree_manifest(scenario_root)
        git_before = _git_metadata_manifest(workspace)

        failure_category = "auth"
        failure_assertions = {"runtime_auth_source_available": auth_source.is_file()}
        if not auth_source.is_file():
            raise RuntimeError("Codex auth source is unavailable")
        runtime_env = _sanitized_runtime_env(
            os.environ.copy(),
            home=home,
            codex_home=codex_home,
            temp_dir=temp_dir,
        )
        resolvable_forbidden_runtimes = sorted(
            runtime
            for runtime in FORBIDDEN_MODEL_RUNTIMES
            if shutil.which(runtime, path=runtime_env["PATH"]) is not None
        )
        if resolvable_forbidden_runtimes:
            failure_category = "privacy"
            failure_assertions = {"privacy_forbidden_runtimes_unresolvable": False}
            raise RuntimeError("sanitized PATH exposes a forbidden model runtime")
        failure_category = "runtime_tool"
        failure_assertions = {"runtime_native_codex_available": False}
        codex_executable = _resolve_codex_native_executable()
        failure_category = "runtime"
        failure_assertions = {"runtime_process_completed": False}
        last_message_path = transient / "last-message.txt"
        runtime_argv = _codex_argv(
            workspace=workspace,
            last_message=last_message_path,
            prompt=scenario.prompt,
            model=model,
            enable_search=scenario.enable_search,
        )
        runtime_argv[0] = str(codex_executable)
        auth_link.symlink_to(auth_source)
        try:
            runtime_outcome = _run_process_group(
                runtime_argv,
                cwd=workspace,
                env=runtime_env,
                timeout_seconds=timeout_seconds,
            )
        finally:
            if auth_link.is_symlink() or auth_link.exists():
                auth_link.unlink()
        cleanup_auth_removed = not auth_link.exists()
        cleanup_process_group_absent = runtime_outcome.group_absent_after_cleanup
        runtime = runtime_outcome.completed
        stdout = runtime.stdout
        stderr = runtime.stderr
        sanitized_events = _sanitize_events(stdout, scenario_id=scenario.scenario_id)
        runtime_blocker_category = (
            _codex_runtime_blocker_category(stderr) if runtime.returncode != 0 else None
        )
        last_message = (
            last_message_path.read_text(encoding="utf-8")
            if last_message_path.is_file()
            else ""
        )
        final_result = _extract_json_object(last_message)
        completed_spawn_count = _vc02_completed_spawn_count(stdout)
        timeline_assertions, timeline_errors = _validate_vc02_agentic_timeline(
            final_result or {}, scenario_kind=scenario.scenario_kind
        )
        event_assertions, event_errors = _validate_vc02_agentic_event_order(
            stdout, scenario_kind=scenario.scenario_kind
        )
        behavior_assertions = {
            **{f"timeline_{key}": value for key, value in timeline_assertions.items()},
            **{f"event_{key}": value for key, value in event_assertions.items()},
        }
        behavior_errors = [
            *[f"timeline_{key}" for key in timeline_errors],
            *[f"event_{key}" for key in event_errors],
        ]
        loader_assertions = {
            "loader_agentic_required_phase_chain_observed": event_assertions[
                "expected_spawn_count"
            ],
            "loader_agentic_final_json_object": final_result is not None,
        }
        loader_evidence = {
            "completed_spawn_count": completed_spawn_count,
            "event_order_assertions": event_assertions,
        }

        forbidden_commands = _forbidden_runtime_commands(stdout)
        forbidden_observed_processes = _observed_forbidden_processes(runtime_outcome)
        forbidden_runtime_processes = sorted(
            set(forbidden_commands) | set(forbidden_observed_processes)
        )
        child_codex_process_count = _observed_child_codex_process_count(runtime_outcome)
        after = _tree_manifest(workspace)
        isolation_after = _tree_manifest(scenario_root)
        git_after = _git_metadata_manifest(workspace)
        changed_paths = _tree_diff(before, after)
        isolation_changed_paths = _tree_diff(isolation_before, isolation_after)
        git_changed_paths = _tree_diff(git_before, git_after)
        allowed_workspace_manifest_paths = _manifest_paths_with_parents(
            scenario.allowed_changed_paths
        )
        unexpected_paths = sorted(set(changed_paths) - allowed_workspace_manifest_paths)
        allowed_isolation_manifest_paths = {
            *(f"workspace/{path}" for path in allowed_workspace_manifest_paths),
            "transient/last-message.txt",
        }
        isolation_change_summary = _summarize_isolation_changes(
            isolation_changed_paths,
            controller_allowed_paths=allowed_isolation_manifest_paths,
        )
        unexpected_isolation_paths = isolation_change_summary["unexpected_changed_paths"]
        agentic_scenario = scenario.component_id == AGENTIC_EXECUTION_COMPONENT_ID
        required_path_present = (
            VC02_IMPLEMENTATION_PATH in changed_paths if agentic_scenario else True
        )

        assertions: dict[str, bool] = {
            **install_assertions,
            **loader_assertions,
            "runtime_command_passed": runtime.returncode == 0,
            "runtime_not_timed_out": not runtime_outcome.timed_out,
            "runtime_requested_model_explicit": (
                "--model" in runtime_argv
                and runtime_argv[runtime_argv.index("--model") + 1] == model
            ),
            "behavior_contract_passed": not behavior_errors,
            "mutation_only_allowed_paths": not unexpected_paths,
            "mutation_isolated_roots_only_allowed_paths": not unexpected_isolation_paths,
            "mutation_required_agentic_path_present": required_path_present,
            "mutation_git_metadata_unchanged": not git_changed_paths,
            "privacy_no_non_codex_runtime_dispatch": not forbidden_runtime_processes,
            "privacy_observed_processes_have_no_forbidden_model_runtime": (
                not forbidden_observed_processes
            ),
            "privacy_forbidden_runtimes_unresolvable": not resolvable_forbidden_runtimes,
            "privacy_process_observation_available": runtime_outcome.process_observation_available,
            "privacy_root_process_observed": runtime_outcome.root_process_observed,
            "privacy_auth_link_removed_after_runtime": cleanup_auth_removed,
            "cleanup_process_group_absent": cleanup_process_group_absent,
            "cleanup_no_unexpected_process_group_termination": not (
                not runtime_outcome.timed_out
                and (runtime_outcome.term_sent or runtime_outcome.kill_sent)
            ),
        }
        assertions.update(
            {f"behavior_{key}": value for key, value in behavior_assertions.items()}
        )
        observed_summary = (
            scenario.expected_behavior
            if not behavior_errors
            else "VC-02 runtime behavior validation failed"
        )
        if runtime_blocker_category is not None:
            observed_summary = "Codex auth, account, or service state prevented runtime proof"
        result = {
            "fixture_id": f"synthetic-{scenario.scenario_id}",
            "captured_at": _captured_at(),
            "runtime_target": "codex",
            "component_id": scenario.component_id,
            "slice_id": "VC-02",
            "scenario_id": scenario.scenario_id,
            "scenario_kind": scenario.scenario_kind,
            "observed_codex_version": codex_version,
            **_model_evidence(model),
            "model_runtime_observation": {
                "controller_invocation_count": 1,
                "controller_fallback_path_configured": False,
                "backend_fallback_observation": "unavailable_in_codex_exec_json_0.137.0",
            },
            "isolated_workspace_identity": _sha256_text(str(workspace.resolve())),
            "isolated_workspace_hash_before": _tree_hash(before),
            "isolated_workspace_hash_after": _tree_hash(after),
            "install_plan_hash": install_plan_hash,
            "installed_codex_artifact_path": installed_artifact_path.as_posix(),
            "installed_codex_artifact_hash": installed_artifact_hash,
            "prompt_purpose": scenario.prompt_purpose,
            "prompt_hash": _sha256_text(scenario.prompt),
            "prompt_revision_id": scenario.prompt_revision_id,
            "expected_behavior": scenario.expected_behavior,
            "observed_behavior_summary": observed_summary,
            "loader_evidence": loader_evidence,
            "before_after_tree": {
                "allowed_changed_paths": list(scenario.allowed_changed_paths),
                "actual_changed_paths": changed_paths,
                "unexpected_changed_paths": unexpected_paths,
            },
            "before_after_git": {
                "metadata_hash_before": _tree_hash(git_before),
                "metadata_hash_after": _tree_hash(git_after),
                "actual_changed_paths": git_changed_paths,
            },
            "before_after_isolation_root": {
                "manifest_hash_before": _tree_hash(isolation_before),
                "manifest_hash_after": _tree_hash(isolation_after),
                "controller_allowed_changed_paths": sorted(
                    allowed_isolation_manifest_paths
                ),
                **isolation_change_summary,
            },
            "observed_filesystem_changed_paths": changed_paths,
            "observed_git_metadata_changed_paths": git_changed_paths,
            "forbidden_runtime_processes": forbidden_runtime_processes,
            "observed_child_codex_process_count": child_codex_process_count,
            "expected_vs_actual_assertions": assertions,
            "privacy": {
                "synthetic_fixture_only": True,
                "external_mutation_credentials_removed": True,
                "raw_prompt_persisted": False,
                "raw_model_response_persisted": False,
                "raw_environment_persisted": False,
                "auth_copied": False,
                "runtime_system_path": RUNTIME_SYSTEM_PATH,
            },
            "process_group_cleanup": {
                "timed_out": runtime_outcome.timed_out,
                "term_sent": runtime_outcome.term_sent,
                "kill_sent": runtime_outcome.kill_sent,
                "process_reaped": runtime_outcome.process_reaped,
                "group_absent_after_cleanup": runtime_outcome.group_absent_after_cleanup,
                "process_observation_available": runtime_outcome.process_observation_available,
                "root_process_observed": runtime_outcome.root_process_observed,
                "observed_executables": list(runtime_outcome.observed_executables),
            },
            "stderr_observation": "empty" if not stderr.strip() else "non-empty-not-persisted",
            "runtime_blocker_observation": {
                "category": runtime_blocker_category,
                "stderr_persisted": False,
            },
            "verdict": (
                "BLOCKED"
                if runtime_blocker_category is not None
                else "PASS" if all(assertions.values()) else "NEEDS_WORK"
            ),
            "failure_category": runtime_blocker_category or _failure_category(assertions),
        }
    except Exception as exc:  # evidence must survive a fixed-scenario failure
        result = {
            "fixture_id": f"synthetic-{scenario.scenario_id}",
            "captured_at": _captured_at(),
            "runtime_target": "codex",
            "component_id": scenario.component_id,
            "slice_id": "VC-02",
            "scenario_id": scenario.scenario_id,
            "scenario_kind": scenario.scenario_kind,
            "observed_codex_version": codex_version,
            **_model_evidence(model),
            "prompt_purpose": scenario.prompt_purpose,
            "prompt_hash": _sha256_text(scenario.prompt),
            "prompt_revision_id": scenario.prompt_revision_id,
            "expected_behavior": scenario.expected_behavior,
            "observed_behavior_summary": "scenario controller failed before a complete verdict",
            "expected_vs_actual_assertions": failure_assertions,
            "privacy": {
                "synthetic_fixture_only": True,
                "raw_prompt_persisted": False,
                "raw_model_response_persisted": False,
                "auth_copied": False,
            },
            "controller_error": type(exc).__name__,
            "verdict": (
                "BLOCKED"
                if failure_category in {"auth", "runtime_tool"}
                else "NEEDS_WORK"
            ),
            "failure_category": failure_category,
        }
    finally:
        if auth_link.is_symlink() or auth_link.exists():
            auth_link.unlink()
        cleanup_auth_removed = not auth_link.exists()
        shutil.rmtree(scenario_root, ignore_errors=True)
        cleanup_workspace_removed = not scenario_root.exists()

    result["cleanup"] = {
        "auth_link_removed": cleanup_auth_removed,
        "isolated_workspace_removed": cleanup_workspace_removed,
        "process_group_absent": cleanup_process_group_absent,
    }
    result.setdefault("expected_vs_actual_assertions", {})[
        "cleanup_auth_link_removed"
    ] = cleanup_auth_removed
    result["expected_vs_actual_assertions"]["cleanup_workspace_removed"] = (
        cleanup_workspace_removed
    )
    result["expected_vs_actual_assertions"]["cleanup_process_group_absent"] = (
        cleanup_process_group_absent
    )
    if not cleanup_auth_removed or not cleanup_workspace_removed or not cleanup_process_group_absent:
        result["verdict"] = "NEEDS_WORK"
        result["failure_category"] = "cleanup"
    return result, sanitized_events


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _sanitized_predecessor_path(value: str | None) -> str | None:
    if value is None:
        return None
    path = Path(value).resolve()
    try:
        return path.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return "external-predecessor-result"


def _write_checkpoint(
    output_dir: Path,
    *,
    slice_id: str,
    attempt_kind: str,
    selected_scenario_ids: list[str],
    active_scenario_id: str | None,
    completed_scenarios: list[dict[str, Any]],
    sanitized_events: list[dict[str, Any]],
    component_id: str = COMPONENT_ID,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "fixture_id": f"mattpocock-engineering-refresh-{slice_id.lower()}",
        "captured_at": _captured_at(),
        "runtime_target": "codex",
        "slice_id": slice_id,
        "component_id": component_id,
        "status": "running",
        "attempt": {
            "kind": attempt_kind,
            "selected_scenario_ids": selected_scenario_ids,
            "active_scenario_id": active_scenario_id,
            "completed_scenario_ids": [item.get("scenario_id") for item in completed_scenarios],
        },
    }
    _atomic_write_text(
        output_dir / "result.json",
        json.dumps(checkpoint, indent=2, ensure_ascii=False) + "\n",
    )
    _atomic_write_text(
        output_dir / "events.sanitized.jsonl",
        "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            for event in sanitized_events
        ),
    )
    _atomic_write_text(
        output_dir / "summary.md",
        f"# {slice_id} Codex Runtime 검증\n\n- 상태: `running`\n",
    )


def _write_evidence(
    output_dir: Path,
    *,
    slice_id: str,
    codex_version: str,
    model: str,
    install_plan_hash: str,
    adapter_artifact_hash: str,
    static_gate: dict[str, bool],
    attempt_kind: str,
    selected_scenario_ids: list[str],
    predecessor_result_path: str | None,
    predecessor_result_hash: str | None,
    attempt_scenarios: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    sanitized_events: list[dict[str, Any]],
    forced_verdict: str | None = None,
    failure_category_override: str | None = None,
    blocker: dict[str, Any] | None = None,
    artifact_revalidation: dict[str, str] | None = None,
    component_id: str = COMPONENT_ID,
    scenario_completion_assertions: dict[str, bool] | None = None,
    runtime_comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if forced_verdict not in {None, "NEEDS_WORK", "BLOCKED"}:
        raise ValueError(f"unsupported forced verdict: {forced_verdict}")
    completion_passed = (
        scenario_completion_assertions is None or all(scenario_completion_assertions.values())
    )
    if forced_verdict is not None:
        verdict = forced_verdict
    elif (
        scenarios
        and all(static_gate.values())
        and all(item.get("verdict") == "PASS" for item in scenarios)
        and completion_passed
    ):
        verdict = "PASS"
    elif any(item.get("verdict") == "BLOCKED" for item in scenarios):
        verdict = "BLOCKED"
    else:
        verdict = "NEEDS_WORK"
    forbidden_runtime_count = sum(
        len(item.get("forbidden_runtime_processes") or []) for item in attempt_scenarios
    )
    payload: dict[str, Any] = {
        "fixture_id": f"mattpocock-engineering-refresh-{slice_id.lower()}",
        "captured_at": _captured_at(),
        "runtime_target": "codex",
        "slice_id": slice_id,
        "component_id": component_id,
        "observed_codex_version": codex_version,
        **_model_evidence(model),
        "install_plan_hash": install_plan_hash,
        "adapter_artifact_hash": adapter_artifact_hash,
        "static_and_install_gate": static_gate,
        "attempt": {
            "kind": attempt_kind,
            "selected_scenario_ids": selected_scenario_ids,
            "predecessor_result": _sanitized_predecessor_path(predecessor_result_path),
            "predecessor_result_sha256": predecessor_result_hash,
            "prompt_revision_changes": [
                item["prompt_revision_change"]
                for item in attempt_scenarios
                if isinstance(item.get("prompt_revision_change"), dict)
            ],
        },
        "attempt_scenario_results": attempt_scenarios,
        "scenario_results": scenarios,
        "privacy": {
            "synthetic_fixtures_only": True,
            "raw_events_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
            "auth_material_persisted": False,
            "non_codex_runtime_invocations": forbidden_runtime_count,
        },
        "cleanup": {
            "all_auth_links_removed": all(
                (item.get("cleanup") or {}).get("auth_link_removed") is True
                for item in scenarios
            ),
            "all_isolated_workspaces_removed": all(
                (item.get("cleanup") or {}).get("isolated_workspace_removed") is True
                for item in scenarios
            ),
            "all_process_groups_absent": all(
                (item.get("cleanup") or {}).get("process_group_absent", True) is True
                for item in scenarios
            ),
        },
        "verdict": verdict,
        "failure_category": failure_category_override
        or next(
            (item.get("failure_category") for item in scenarios if item.get("verdict") != "PASS"),
            "scenario_completion" if not completion_passed else None,
        ),
    }
    if scenario_completion_assertions is not None:
        payload["scenario_completion_assertions"] = scenario_completion_assertions
    if runtime_comparison is not None:
        payload["baseline_pressure_comparison"] = runtime_comparison
    if blocker is not None:
        payload["blocker"] = blocker
    if artifact_revalidation is not None:
        payload["attempt"]["artifact_revalidation"] = artifact_revalidation
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    _atomic_write_text(
        result_path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    events_path = output_dir / "events.sanitized.jsonl"
    _atomic_write_text(
        events_path,
        "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in sanitized_events),
    )
    summary_lines = [
        f"# {slice_id} Codex Runtime 검증",
        "",
        f"- 전체 판정: `{verdict}`",
        f"- Codex: `{codex_version}`",
        f"- 요청 모델: `{model}`",
        f"- 이번 attempt 호출 수: `{len(attempt_scenarios)}`",
        "- Raw prompt, raw response, auth, environment dump는 저장하지 않았다.",
        "",
        "## Scenario",
    ]
    for item in scenarios:
        summary_lines.append(
            f"- `{item['scenario_id']}`: `{item['verdict']}` — {item['observed_behavior_summary']}"
        )
    _atomic_write_text(output_dir / "summary.md", "\n".join(summary_lines) + "\n")
    return payload


def _write_vc02_checkpoint(
    output_dir: Path,
    *,
    attempt_kind: str,
    selected_scenario_ids: list[str],
    predecessor_result_path: str | None,
    predecessor_result_hash: str | None,
    active_scenario_id: str | None,
    completed_scenarios: list[dict[str, Any]],
    sanitized_events: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "fixture_id": "mattpocock-engineering-refresh-vc-02",
        "captured_at": _captured_at(),
        "runtime_target": "codex",
        "slice_id": "VC-02",
        "component_ids": list(VC02_COMPONENT_IDS),
        "status": "running",
        "attempt": {
            "kind": attempt_kind,
            "selected_scenario_ids": selected_scenario_ids,
            "predecessor_result": _sanitized_predecessor_path(predecessor_result_path),
            "predecessor_result_sha256": predecessor_result_hash,
            "active_scenario_id": active_scenario_id,
            "completed_scenario_ids": [
                item.get("scenario_id") for item in completed_scenarios
            ],
        },
    }
    _atomic_write_text(
        output_dir / "result.json",
        json.dumps(checkpoint, indent=2, ensure_ascii=False) + "\n",
    )
    _atomic_write_text(
        output_dir / "events.sanitized.jsonl",
        "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            for event in sanitized_events
        ),
    )
    _atomic_write_text(
        output_dir / "summary.md",
        "# VC-02 Codex Runtime 검증\n\n- 상태: `running`\n",
    )


def _write_vc02_evidence(
    output_dir: Path,
    *,
    codex_version: str,
    model: str,
    install_plan_hashes: dict[str, str],
    adapter_artifact_hashes: dict[str, str],
    static_gate: dict[str, bool],
    attempt_kind: str,
    selected_scenario_ids: list[str],
    predecessor_result_path: str | None,
    predecessor_result_hash: str | None,
    prompt_revision_changes: list[dict[str, str]],
    attempt_scenarios: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    sanitized_events: list[dict[str, Any]],
    forced_verdict: str | None = None,
    failure_category_override: str | None = None,
    blocker: dict[str, Any] | None = None,
    artifact_revalidation: dict[str, Any] | None = None,
    static_only_install_evidence: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if forced_verdict not in {None, "NEEDS_WORK", "BLOCKED"}:
        raise ValueError(f"unsupported forced VC-02 verdict: {forced_verdict}")
    if forced_verdict is not None:
        verdict = forced_verdict
    elif (
        len(scenarios) == len(_vc02_scenario_specs())
        and all(static_gate.values())
        and all(item.get("verdict") == "PASS" for item in scenarios)
    ):
        verdict = "PASS"
    elif any(item.get("verdict") == "BLOCKED" for item in scenarios):
        verdict = "BLOCKED"
    else:
        verdict = "NEEDS_WORK"
    forbidden_runtime_count = sum(
        len(item.get("forbidden_runtime_processes") or []) for item in scenarios
    )
    payload: dict[str, Any] = {
        "fixture_id": "mattpocock-engineering-refresh-vc-02",
        "captured_at": _captured_at(),
        "runtime_target": "codex",
        "slice_id": "VC-02",
        "component_ids": list(VC02_COMPONENT_IDS),
        "observed_codex_version": codex_version,
        **_model_evidence(model),
        "install_plan_hashes": install_plan_hashes,
        "adapter_artifact_hashes": adapter_artifact_hashes,
        "adapter_artifact_hash": (
            _vc02_adapter_artifact_hash(adapter_artifact_hashes)
            if set(adapter_artifact_hashes) == set(VC02_COMPONENT_IDS)
            else "unavailable"
        ),
        "static_and_install_gate": static_gate,
        "static_only_install_evidence": static_only_install_evidence or {},
        "attempt": {
            "kind": attempt_kind,
            "selected_scenario_ids": selected_scenario_ids,
            "predecessor_result": _sanitized_predecessor_path(predecessor_result_path),
            "predecessor_result_sha256": predecessor_result_hash,
            "prompt_revision_changes": prompt_revision_changes,
            "carried_forward_scenario_ids": [
                item.get("scenario_id")
                for item in scenarios
                if item.get("scenario_id") not in selected_scenario_ids
            ],
        },
        "attempt_scenario_results": attempt_scenarios,
        "scenario_results": scenarios,
        "privacy": {
            "synthetic_fixtures_only": True,
            "raw_events_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
            "auth_material_persisted": False,
            "non_codex_runtime_invocations": forbidden_runtime_count,
        },
        "cleanup": {
            "all_auth_links_removed": all(
                (item.get("cleanup") or {}).get("auth_link_removed") is True
                for item in scenarios
            ),
            "all_isolated_workspaces_removed": all(
                (item.get("cleanup") or {}).get("isolated_workspace_removed") is True
                for item in scenarios
            ),
            "all_process_groups_absent": all(
                (item.get("cleanup") or {}).get("process_group_absent", True) is True
                for item in scenarios
            ),
        },
        "verdict": verdict,
        "failure_category": failure_category_override
        or next(
            (
                item.get("failure_category")
                for item in scenarios
                if item.get("verdict") != "PASS"
            ),
            None,
        ),
    }
    if blocker is not None:
        payload["blocker"] = blocker
    if artifact_revalidation is not None:
        payload["attempt"]["artifact_revalidation"] = artifact_revalidation
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        output_dir / "result.json",
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    _atomic_write_text(
        output_dir / "events.sanitized.jsonl",
        "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            for event in sanitized_events
        ),
    )
    summary_lines = [
        "# VC-02 Codex Runtime 검증",
        "",
        f"- 전체 판정: `{verdict}`",
        f"- Codex: `{codex_version}`",
        f"- 요청 모델: `{model}`",
        f"- 이번 attempt 호출 수: `{len(attempt_scenarios)}`",
        "- Raw prompt, raw response, auth, environment dump는 저장하지 않았다.",
        "",
        "## Scenario",
    ]
    for item in scenarios:
        summary_lines.append(
            f"- `{item['scenario_id']}`: `{item['verdict']}` — "
            f"{item['observed_behavior_summary']}"
        )
    _atomic_write_text(output_dir / "summary.md", "\n".join(summary_lines) + "\n")
    return payload


def _run_vc02_slice(
    *,
    model: str,
    timeout_seconds: int,
    evidence_dir: Path | None,
    retry_failed_from: Path | None,
    revalidate_after_adapter_change_from: Path | None,
) -> tuple[dict[str, Any], Path]:
    if retry_failed_from is not None and revalidate_after_adapter_change_from is not None:
        raise ValueError("retry and artifact revalidation are mutually exclusive")
    scenarios = _vc02_scenario_specs()
    output_dir = evidence_dir or (EVIDENCE_ROOT / "VC-02" / _timestamp())
    if output_dir.exists():
        raise FileExistsError(f"evidence directory already exists: {output_dir}")
    run_root = Path(tempfile.mkdtemp(prefix="harnesskit-vc-02-controller."))
    predecessor_result = revalidate_after_adapter_change_from or retry_failed_from
    attempt_kind = (
        "artifact-revalidation"
        if revalidate_after_adapter_change_from is not None
        else "failed-only-rerun" if retry_failed_from is not None else "initial"
    )
    selected_scenario_ids = [scenario.scenario_id for scenario in scenarios]
    codex_version = "unavailable"
    install_plan_hashes: dict[str, str] = {}
    adapter_artifact_hashes: dict[str, str] = {}
    static_gate: dict[str, bool] = {"preflight_completed": False}
    static_only_install_evidence: dict[str, dict[str, Any]] = {}
    attempt_plan: AttemptPlan | None = None
    attempt_results: list[dict[str, Any]] = []
    sanitized_events: list[dict[str, Any]] = []

    def effective_scenarios() -> list[dict[str, Any]]:
        carried = list(attempt_plan.carried_forward_results) if attempt_plan else []
        by_id = {
            item["scenario_id"]
            for item in [*carried, *attempt_results]
            if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
        }
        records = {
            item["scenario_id"]: item
            for item in [*carried, *attempt_results]
            if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
        }
        return [
            records[scenario.scenario_id]
            for scenario in scenarios
            if scenario.scenario_id in by_id
        ]

    def checkpoint(active_scenario_id: str | None) -> None:
        _write_vc02_checkpoint(
            output_dir,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            predecessor_result_path=(
                attempt_plan.predecessor_result_path if attempt_plan else None
            ),
            predecessor_result_hash=(
                attempt_plan.predecessor_result_hash if attempt_plan else None
            ),
            active_scenario_id=active_scenario_id,
            completed_scenarios=attempt_results,
            sanitized_events=sanitized_events,
        )

    def terminalize(
        *, verdict: str, failure_category: str, phase: str, blocker_type: str
    ) -> tuple[dict[str, Any], Path]:
        payload = _write_vc02_evidence(
            output_dir,
            codex_version=codex_version,
            model=model,
            install_plan_hashes=install_plan_hashes,
            adapter_artifact_hashes=adapter_artifact_hashes,
            static_gate=static_gate,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            predecessor_result_path=(
                attempt_plan.predecessor_result_path if attempt_plan else None
            ),
            predecessor_result_hash=(
                attempt_plan.predecessor_result_hash if attempt_plan else None
            ),
            prompt_revision_changes=(
                list(attempt_plan.prompt_revision_changes) if attempt_plan else []
            ),
            attempt_scenarios=attempt_results,
            scenarios=effective_scenarios(),
            sanitized_events=sanitized_events,
            forced_verdict=verdict,
            failure_category_override=failure_category,
            blocker={
                "phase": phase,
                "type": blocker_type,
                "message_persisted": False,
            },
            artifact_revalidation=(
                attempt_plan.artifact_revalidation if attempt_plan else None
            ),
            static_only_install_evidence=static_only_install_evidence,
        )
        return payload, output_dir

    try:
        checkpoint(active_scenario_id=None)
        try:
            (
                plan_paths,
                install_plan_hashes,
                adapter_artifact_hashes,
                static_only_install_evidence,
                static_gate,
            ) = _vc02_static_gate(run_root)
            static_gate["preflight_completed"] = True
        except (RuntimeError, OSError, json.JSONDecodeError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="static_or_install",
                phase="static_and_install_preflight",
                blocker_type=type(exc).__name__,
            )
        previous_payload: dict[str, Any] | None = None
        try:
            codex_version = _codex_version()
        except (RuntimeError, OSError) as exc:
            return terminalize(
                verdict="BLOCKED",
                failure_category="runtime_tool",
                phase="codex_native_tool_preflight",
                blocker_type=type(exc).__name__,
            )
        try:
            if predecessor_result is not None:
                previous_payload = json.loads(
                    predecessor_result.read_text(encoding="utf-8")
                )
                if not isinstance(previous_payload, dict):
                    raise ValueError("predecessor result must be a JSON object")
            if revalidate_after_adapter_change_from is not None:
                attempt_plan = _plan_vc02_artifact_revalidation(
                    scenarios,
                    previous_payload=previous_payload,
                    predecessor_result=predecessor_result,
                    current_install_plan_hashes=install_plan_hashes,
                    current_adapter_artifact_hashes=adapter_artifact_hashes,
                    current_codex_version=codex_version,
                    requested_model=model,
                )
            else:
                attempt_plan = _plan_vc02_attempt(
                    scenarios,
                    previous_payload=previous_payload,
                    predecessor_result=predecessor_result,
                    current_install_plan_hashes=install_plan_hashes,
                    current_adapter_artifact_hashes=adapter_artifact_hashes,
                    current_codex_version=codex_version,
                    requested_model=model,
                )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="controller",
                phase="attempt_planning",
                blocker_type=type(exc).__name__,
            )
        selected_scenario_ids = [
            scenario.scenario_id for scenario in attempt_plan.selected_scenarios
        ]
        checkpoint(active_scenario_id=None)
        if not _real_auth_path().is_file():
            return terminalize(
                verdict="BLOCKED",
                failure_category="auth",
                phase="codex_auth_preflight",
                blocker_type="AuthFileUnavailable",
            )
        for scenario in attempt_plan.selected_scenarios:
            checkpoint(active_scenario_id=scenario.scenario_id)
            result, events = _run_vc02_scenario(
                run_root=run_root,
                plan_path=plan_paths[scenario.component_id],
                install_plan_hash=install_plan_hashes[scenario.component_id],
                codex_version=codex_version,
                model=model,
                timeout_seconds=timeout_seconds,
                scenario=scenario,
                expected_adapter_artifact_hash=adapter_artifact_hashes[
                    scenario.component_id
                ],
            )
            prompt_revision_change = next(
                (
                    item
                    for item in attempt_plan.prompt_revision_changes
                    if item["scenario_id"] == scenario.scenario_id
                ),
                None,
            )
            if prompt_revision_change is not None:
                result["prompt_revision_change"] = prompt_revision_change
            attempt_results.append(result)
            sanitized_events.extend(events)
            checkpoint(active_scenario_id=None)
            if result.get("verdict") == "BLOCKED":
                return _write_vc02_evidence(
                    output_dir,
                    codex_version=codex_version,
                    model=model,
                    install_plan_hashes=install_plan_hashes,
                    adapter_artifact_hashes=adapter_artifact_hashes,
                    static_gate=static_gate,
                    attempt_kind=attempt_kind,
                    selected_scenario_ids=selected_scenario_ids,
                    predecessor_result_path=attempt_plan.predecessor_result_path,
                    predecessor_result_hash=attempt_plan.predecessor_result_hash,
                    prompt_revision_changes=list(attempt_plan.prompt_revision_changes),
                    attempt_scenarios=attempt_results,
                    scenarios=effective_scenarios(),
                    sanitized_events=sanitized_events,
                    artifact_revalidation=attempt_plan.artifact_revalidation,
                    static_only_install_evidence=static_only_install_evidence,
                ), output_dir
        return _write_vc02_evidence(
            output_dir,
            codex_version=codex_version,
            model=model,
            install_plan_hashes=install_plan_hashes,
            adapter_artifact_hashes=adapter_artifact_hashes,
            static_gate=static_gate,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            predecessor_result_path=attempt_plan.predecessor_result_path,
            predecessor_result_hash=attempt_plan.predecessor_result_hash,
            prompt_revision_changes=list(attempt_plan.prompt_revision_changes),
            attempt_scenarios=attempt_results,
            scenarios=effective_scenarios(),
            sanitized_events=sanitized_events,
            artifact_revalidation=attempt_plan.artifact_revalidation,
            static_only_install_evidence=static_only_install_evidence,
        ), output_dir
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def _current_vc04_adapter_artifact_hash() -> str:
    if not _is_regular_file_within(VC04_DIST_CODEX_ARTIFACT, REPO_ROOT):
        raise RuntimeError("built Codex TDD user-scope adapter artifact is unavailable")
    return _sha256_file(VC04_DIST_CODEX_ARTIFACT)


def _current_vc05_adapter_artifact_hash() -> str:
    if not _is_regular_file_within(VC05_DIST_CODEX_ARTIFACT, REPO_ROOT):
        raise RuntimeError("built Codex github-issue adapter artifact is unavailable")
    return _sha256_file(VC05_DIST_CODEX_ARTIFACT)


def _vc04_static_gate(run_root: Path) -> tuple[Path, str, str, dict[str, bool]]:
    results: dict[str, bool] = {}
    for name, argv in (
        (
            "canonical_validation",
            [sys.executable, "scripts/components/validate.py", "--component", TDD_COMPONENT_ID],
        ),
        (
            "declared_target_build",
            [sys.executable, "scripts/adapters/build.py", "--component", TDD_COMPONENT_ID],
        ),
        (
            "declared_target_check",
            [
                sys.executable,
                "scripts/adapters/build.py",
                "--component",
                TDD_COMPONENT_ID,
                "--check",
            ],
        ),
    ):
        result = _run(argv, cwd=REPO_ROOT, timeout_seconds=180)
        results[name] = result.returncode == 0
        if result.returncode != 0:
            raise RuntimeError(f"VC-04 static gate failed: {name}")
    plan_result = _run(
        [
            sys.executable,
            "scripts/install/plan.py",
            "--component",
            TDD_COMPONENT_ID,
            "--scope",
            "user",
            "--mode",
            "apply",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        timeout_seconds=120,
    )
    results["standalone_user_apply_plan"] = plan_result.returncode == 0
    if plan_result.returncode != 0:
        raise RuntimeError("VC-04 static gate failed: standalone_user_apply_plan")
    plan = json.loads(plan_result.stdout)
    codex_artifacts = [
        artifact
        for artifact in plan.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("target") == "codex"
    ]
    results["user_scope_codex_artifact_route"] = (
        plan.get("mode") == "apply"
        and plan.get("scope") == "user"
        and plan.get("components") == [TDD_COMPONENT_ID]
        and codex_artifacts
        == [
            {
                "component_id": TDD_COMPONENT_ID,
                "component_ids": [TDD_COMPONENT_ID],
                "target": "codex",
                "source": "dist/codex/.codex/skills/harnesskit-tdd/SKILL.md",
                "destination": ".codex/skills/harnesskit-tdd/SKILL.md",
            }
        ]
    )
    if not results["user_scope_codex_artifact_route"]:
        raise RuntimeError("VC-04 standalone user plan Codex route drift")
    plan_path = run_root / "tdd.install-plan.apply.json"
    plan_path.write_text(plan_result.stdout, encoding="utf-8")
    return plan_path, _sha256_text(plan_result.stdout), _current_vc04_adapter_artifact_hash(), results


def _vc05_static_gate(run_root: Path) -> tuple[Path, str, str, dict[str, bool]]:
    results: dict[str, bool] = {}
    for name, argv in (
        (
            "canonical_validation",
            [
                sys.executable,
                "scripts/components/validate.py",
                "--component",
                GITHUB_ISSUE_COMPONENT_ID,
            ],
        ),
        (
            "declared_target_build",
            [
                sys.executable,
                "scripts/adapters/build.py",
                "--component",
                GITHUB_ISSUE_COMPONENT_ID,
            ],
        ),
        (
            "declared_target_check",
            [
                sys.executable,
                "scripts/adapters/build.py",
                "--component",
                GITHUB_ISSUE_COMPONENT_ID,
                "--check",
            ],
        ),
    ):
        result = _run(argv, cwd=REPO_ROOT, timeout_seconds=180)
        results[name] = result.returncode == 0
        if result.returncode != 0:
            raise RuntimeError(f"VC-05 static gate failed: {name}")
    plan_result = _run(
        [
            sys.executable,
            "scripts/install/plan.py",
            "--component",
            GITHUB_ISSUE_COMPONENT_ID,
            "--scope",
            "project",
            "--mode",
            "apply",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        timeout_seconds=120,
    )
    results["standalone_project_apply_plan"] = plan_result.returncode == 0
    if plan_result.returncode != 0:
        raise RuntimeError("VC-05 static gate failed: standalone_project_apply_plan")
    plan = json.loads(plan_result.stdout)
    codex_artifacts = [
        artifact
        for artifact in plan.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("target") == "codex"
    ]
    results["project_scope_codex_artifact_route"] = (
        plan.get("mode") == "apply"
        and plan.get("scope") == "project"
        and plan.get("components") == [GITHUB_ISSUE_COMPONENT_ID]
        and codex_artifacts
        == [
            {
                "component_id": GITHUB_ISSUE_COMPONENT_ID,
                "component_ids": [GITHUB_ISSUE_COMPONENT_ID],
                "target": "codex",
                "source": VC05_ADAPTER_OUTPUT,
                "destination": VC05_INSTALLED_CODEX_ARTIFACT.as_posix(),
            }
        ]
    )
    if not results["project_scope_codex_artifact_route"]:
        raise RuntimeError("VC-05 standalone project plan Codex route drift")
    plan_path = run_root / "github-issue.install-plan.apply.json"
    plan_path.write_text(plan_result.stdout, encoding="utf-8")
    return plan_path, _sha256_text(plan_result.stdout), _current_vc05_adapter_artifact_hash(), results


def _write_vc04_fixture(
    workspace: Path, *, scenario_kind: str, trace_path: Path | None = None
) -> None:
    if scenario_kind not in {"positive", "boundary"}:
        raise ValueError(f"unsupported VC-04 fixture scenario: {scenario_kind}")
    fixture_root = workspace / "fixture"
    tests_root = fixture_root / "tests"
    tests_root.mkdir(parents=True, exist_ok=True)
    if trace_path is None:
        fixture_init = ""
    else:
        try:
            trace_relative_path = trace_path.resolve(strict=False).relative_to(
                workspace.resolve(strict=True)
            ).as_posix()
        except (OSError, RuntimeError, ValueError):
            raise ValueError("VC-04 trace path must remain inside the synthetic workspace") from None
        if trace_relative_path != VC04_TRACE_RELATIVE_PATH:
            raise ValueError("VC-04 trace path drifted from the controller-owned fixture path")
        fixture_init = f"""import hashlib
import json
import os
from pathlib import Path
import sys
import unittest


TRACE_PATH = Path({str(trace_path.resolve(strict=False))!r})


def _hash_or_none(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _kind(argv: list[str]) -> str | None:
    if {VC04_REQUESTED_TEST_MODULE!r} in argv:
        return "requested"
    if {VC04_BOUNDARY_TEST_MODULE!r} in argv:
        return "boundary"
    if "discover" in argv and "fixture/tests" in argv:
        return "full_suite"
    return None


def _record(
    kind: str,
    root: Path,
    test_path: Path,
    *,
    phase: str,
    successful: bool | None,
) -> None:
    record = {{
        "kind": kind,
        "phase": phase,
        "successful": successful,
        "source_sha256": _hash_or_none(root / {VC04_SOURCE_PATH!r}),
        "test_sha256": _hash_or_none(test_path),
    }}
    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\\n").encode("utf-8")
    descriptor = os.open(TRACE_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)


_observed_kind = _kind(sys.argv[1:])
if _observed_kind is not None:
    _root = Path.cwd()
    _test_path = (
        _root / {VC04_REQUESTED_TEST_PATH!r}
        if _observed_kind in {{"requested", "full_suite"}}
        else _root / {VC04_BOUNDARY_TEST_PATH!r}
    )
    _record(_observed_kind, _root, _test_path, phase="before", successful=None)
    _original_run = unittest.TextTestRunner.run

    def _run_and_record(self, test):
        result = _original_run(self, test)
        _record(
            _observed_kind,
            _root,
            _test_path,
            phase="after",
            successful=result.wasSuccessful(),
        )
        return result

    unittest.TextTestRunner.run = _run_and_record
"""
    fixture_init_path = fixture_root / "__init__.py"
    fixture_init_path.write_text(fixture_init, encoding="utf-8")
    if trace_path is not None:
        fixture_init_path.chmod(0o444)
    (tests_root / "__init__.py").write_text("", encoding="utf-8")
    source = (
        """def _is_over_rating(current_ma: int, rated_ma: int) -> bool:
    return False


def classify_connector_current(current_ma: int, rated_ma: int) -> str:
    return \"over_rating\" if _is_over_rating(current_ma, rated_ma) else \"within_rating\"
"""
        if scenario_kind == "positive"
        else """def _is_over_rating(current_ma: int, rated_ma: int) -> bool:
    return current_ma > rated_ma


def classify_connector_current(current_ma: int, rated_ma: int) -> str:
    return \"over_rating\" if _is_over_rating(current_ma, rated_ma) else \"within_rating\"
"""
    )
    (workspace / VC04_SOURCE_PATH).write_text(source, encoding="utf-8")
    (workspace / VC04_EXISTING_TEST_PATH).write_text(
        """import unittest
from fixture.connector_current import classify_connector_current


class ExistingBehaviorTest(unittest.TestCase):
    def test_rating_equal_is_within_rating(self):
        self.assertEqual(classify_connector_current(900, 900), \"within_rating\")
""",
        encoding="utf-8",
    )
    if scenario_kind == "boundary":
        (workspace / VC04_BOUNDARY_TEST_PATH).write_text(
            """import unittest
from unittest.mock import patch
from fixture.connector_current import _is_over_rating, classify_connector_current


class BoundaryTestQuality(unittest.TestCase):
    def test_private_mock_and_tautological_expected_value(self):
        with patch(\"fixture.connector_current._is_over_rating\", return_value=True):
            expected = \"over_rating\" if _is_over_rating(901, 900) else \"within_rating\"
            self.assertEqual(classify_connector_current(901, 900), expected)
""",
            encoding="utf-8",
        )


def _vc04_public_call_case(node: ast.AST) -> tuple[int, int, str] | None:
    if not isinstance(node, ast.Call):
        return None
    function = node.func
    function_name = (
        function.id
        if isinstance(function, ast.Name)
        else function.attr
        if isinstance(function, ast.Attribute)
        else None
    )
    if function_name != "classify_connector_current" or len(node.args) != 2:
        return None
    current, rated = node.args
    if not (
        isinstance(current, ast.Constant)
        and isinstance(current.value, int)
        and not isinstance(current.value, bool)
        and isinstance(rated, ast.Constant)
        and isinstance(rated.value, int)
        and not isinstance(rated.value, bool)
    ):
        return None
    return int(current.value), int(rated.value), ""


def _vc04_literal_public_assertions(tree: ast.AST) -> set[tuple[int, int, str]]:
    assertions: set[tuple[int, int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr not in {"assertEqual", "assertEquals"} or len(node.args) < 2:
                continue
            case = _vc04_public_call_case(node.args[0])
            expected = node.args[1]
            if case is not None and isinstance(expected, ast.Constant) and isinstance(expected.value, str):
                assertions.add((case[0], case[1], expected.value))
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
            comparison = node.test
            if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
                continue
            if len(comparison.comparators) != 1:
                continue
            case = _vc04_public_call_case(comparison.left)
            expected = comparison.comparators[0]
            if case is not None and isinstance(expected, ast.Constant) and isinstance(expected.value, str):
                assertions.add((case[0], case[1], expected.value))
    return assertions


def _vc04_test_shape(path: Path, *, scenario_kind: str) -> dict[str, bool]:
    empty = {
        "valid_python": False,
        "public_api_referenced": False,
        "literal_public_assertions": False,
        "no_internal_business_mock": False,
        "no_internal_helper_reference": False,
        "no_implementation_derived_expected": False,
    }
    if scenario_kind not in {"positive", "boundary"} or not path.is_file():
        return empty
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeError):
        return empty
    identifiers: set[str] = set()
    expected_assignments_derived = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                identifiers.add(node.module)
            identifiers.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            identifiers.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Assign):
            assigns_expected = any(
                isinstance(target, ast.Name) and target.id == "expected" for target in node.targets
            )
            if assigns_expected and any(isinstance(value, ast.Call) for value in ast.walk(node.value)):
                expected_assignments_derived = True
    literal_assertions = _vc04_literal_public_assertions(tree)
    required_cases = (
        set(VC04_POSITIVE_CASES)
        if scenario_kind == "positive"
        else {(901, 900, "over_rating")}
    )
    return {
        "valid_python": True,
        "public_api_referenced": any(
            value[0] in {901, 1200} and value[1] == 900 for value in literal_assertions
        ),
        "literal_public_assertions": required_cases <= literal_assertions,
        "no_internal_business_mock": not (
            {"Mock", "MagicMock", "mock", "patch", "monkeypatch", "unittest.mock"}
            & identifiers
        ),
        "no_internal_helper_reference": "_is_over_rating" not in identifiers
        and re.search(r"(?<![A-Za-z0-9])_is_over_rating(?![A-Za-z0-9])", source) is None,
        "no_implementation_derived_expected": not expected_assignments_derived,
    }


def _vc04_tdd_event_order(
    stdout: str, *, scenario_kind: str
) -> tuple[dict[str, bool], list[str]]:
    if scenario_kind not in {"positive", "boundary"}:
        raise ValueError(f"unsupported VC-04 event scenario: {scenario_kind}")
    assertions = {
        "event_stream_observed": any(_event_objects(stdout)),
    }
    return assertions, [key for key, value in assertions.items() if not value]


def _vc04_trace_records(trace_path: Path) -> list[dict[str, str | bool | None]]:
    if not trace_path.is_file():
        return []
    records: list[dict[str, str | bool | None]] = []
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or value.get("kind") not in {
            "requested",
            "boundary",
            "full_suite",
        }:
            continue
        phase = value.get("phase")
        successful = value.get("successful")
        source_hash = value.get("source_sha256")
        test_hash = value.get("test_sha256")
        if phase not in {"before", "after"}:
            continue
        if phase == "before" and successful is not None:
            continue
        if phase == "after" and not isinstance(successful, bool):
            continue
        if source_hash is not None and not isinstance(source_hash, str):
            continue
        if test_hash is not None and not isinstance(test_hash, str):
            continue
        records.append(
            {
                "kind": value["kind"],
                "phase": phase,
                "successful": successful,
                "source_sha256": source_hash,
                "test_sha256": test_hash,
            }
        )
    return records


def _vc04_trace_assertions(
    stdout: str,
    records: list[dict[str, str | bool | None]],
    *,
    scenario_kind: str,
    initial_source_hash: str,
    initial_test_hash: str | None,
) -> dict[str, bool]:
    if scenario_kind not in {"positive", "boundary"}:
        raise ValueError(f"unsupported VC-04 trace scenario: {scenario_kind}")
    del stdout
    expected_kind = "requested" if scenario_kind == "positive" else "boundary"

    def observed_pairs(
        kind: str,
    ) -> tuple[
        list[dict[str, str | bool | None]],
        list[tuple[dict[str, str | bool | None], dict[str, str | bool | None], int]],
    ]:
        kind_records = [record for record in records if record["kind"] == kind]
        pairs: list[
            tuple[dict[str, str | bool | None], dict[str, str | bool | None], int]
        ] = []
        before: dict[str, str | bool | None] | None = None
        for index, record in enumerate(records):
            if record["kind"] != kind:
                continue
            if record["phase"] == "before":
                before = record
            elif before is not None:
                pairs.append((before, record, index))
                before = None
        return kind_records, pairs

    def snapshot_unchanged(
        pair: tuple[
            dict[str, str | bool | None],
            dict[str, str | bool | None],
            int,
        ]
        | None,
    ) -> bool:
        if pair is None:
            return False
        before, after, _ = pair
        return (
            before["source_sha256"] == after["source_sha256"]
            and before["test_sha256"] == after["test_sha256"]
        )

    test_records, test_pairs = observed_pairs(expected_kind)
    if scenario_kind == "boundary":
        green_pair = next(
            (pair for pair in test_pairs if pair[1]["successful"] is True),
            None,
        )
        green_before = green_pair[0] if green_pair is not None else None
        return {
            "trace_boundary_runs_are_paired": len(test_records) == 2 * len(test_pairs),
            "trace_boundary_run_passed": green_pair is not None,
            "trace_boundary_snapshot_unchanged_during_run": snapshot_unchanged(green_pair),
            "trace_boundary_source_hash_unchanged": green_before is not None
            and green_before["source_sha256"] == initial_source_hash,
            "trace_boundary_test_hash_changed": green_before is not None
            and initial_test_hash is not None
            and green_before["test_sha256"] not in {None, initial_test_hash},
            "trace_raw_values_not_persisted": True,
        }

    red_pair = next((pair for pair in test_pairs if pair[1]["successful"] is False), None)
    red_index = red_pair[2] if red_pair is not None else -1
    green_pair = next(
        (
            pair
            for pair in test_pairs
            if pair[1]["successful"] is True and pair[2] > red_index
        ),
        None,
    )
    full_suite_records, full_suite_pairs = observed_pairs("full_suite")
    green_index = green_pair[2] if green_pair is not None else -1
    full_suite_pair = next(
        (
            pair
            for pair in full_suite_pairs
            if pair[1]["successful"] is True and pair[2] > green_index
        ),
        None,
    )
    red_before = red_pair[0] if red_pair is not None else None
    green_before = green_pair[0] if green_pair is not None else None
    full_suite_before = full_suite_pair[0] if full_suite_pair is not None else None
    return {
        "trace_requested_runs_are_paired": len(test_records) == 2 * len(test_pairs),
        "trace_full_suite_runs_are_paired": len(full_suite_records) == 2 * len(full_suite_pairs),
        "trace_red_run_failed_with_initial_source": red_before is not None
        and snapshot_unchanged(red_pair)
        and red_before["source_sha256"] == initial_source_hash
        and isinstance(red_before["test_sha256"], str)
        and bool(red_before["test_sha256"]),
        "trace_green_run_passed_after_source_change": green_before is not None
        and red_before is not None
        and snapshot_unchanged(green_pair)
        and green_before["source_sha256"] not in {None, initial_source_hash}
        and green_before["test_sha256"] == red_before["test_sha256"],
        "trace_refactor_suite_passed_after_green": full_suite_before is not None
        and red_before is not None
        and green_before is not None
        and snapshot_unchanged(full_suite_pair)
        and full_suite_before["test_sha256"] == red_before["test_sha256"]
        and full_suite_before["source_sha256"] == green_before["source_sha256"],
        "trace_raw_values_not_persisted": True,
    }


def _vc04_allowed_workspace_manifest_paths(scenario: ScenarioSpec) -> set[str]:
    return _manifest_paths_with_parents(
        (*scenario.allowed_changed_paths, VC04_TRACE_RELATIVE_PATH)
    )


def _vc04_required_trace_assertions(
    trace_assertions: dict[str, bool], *, scenario_kind: str
) -> dict[str, bool]:
    if scenario_kind == "positive":
        return trace_assertions
    if scenario_kind == "boundary":
        return {
            "trace_raw_values_not_persisted": trace_assertions.get(
                "trace_raw_values_not_persisted", False
            )
        }
    raise ValueError(f"unsupported VC-04 trace scenario: {scenario_kind}")


def _write_vc07_fixture(
    workspace: Path, *, scenario_kind: str, trace_path: Path | None = None
) -> None:
    if scenario_kind not in {"positive", "boundary"}:
        raise ValueError(f"unsupported VC-07 fixture scenario: {scenario_kind}")
    fixture_root = workspace / "fixture"
    tests_root = fixture_root / "tests"
    tests_root.mkdir(parents=True, exist_ok=True)
    if trace_path is None:
        fixture_init = ""
    else:
        try:
            trace_relative_path = trace_path.resolve(strict=False).relative_to(
                workspace.resolve(strict=True)
            ).as_posix()
        except (OSError, RuntimeError, ValueError):
            raise ValueError("VC-07 trace path must remain inside the synthetic workspace") from None
        if trace_relative_path != VC07_TRACE_RELATIVE_PATH:
            raise ValueError("VC-07 trace path drifted from the controller-owned fixture path")
        fixture_init = f'''import hashlib
import json
import os
from pathlib import Path
import sys
import unittest


TRACE_PATH = Path({str(trace_path.resolve(strict=False))!r})


def _hash_or_none(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _kind(argv: list[str]) -> str | None:
    joined = " ".join(argv).replace("\\\\", "/")
    if (
        {VC07_ORIGINAL_REPRO_MODULE!r} in argv
        or {VC07_ORIGINAL_REPRO_TEST_PATH!r} in joined
        or "test_original_repro.py" in joined
    ):
        return "original_repro"
    if (
        {VC07_REGRESSION_TEST_MODULE!r} in argv
        or {VC07_REGRESSION_TEST_PATH!r} in joined
        or "test_regression_all_zero_labels.py" in joined
    ):
        return "regression"
    if ("discover" in argv and "fixture/tests" in argv) or (
        "pytest" in joined and "fixture/tests" in joined
    ):
        return "full_suite"
    return None


def _record(kind: str, root: Path, *, phase: str, successful: bool | None) -> None:
    record = {{
        "kind": kind,
        "phase": phase,
        "successful": successful,
        "source_sha256": _hash_or_none(root / {VC07_SOURCE_PATH!r}),
        "original_repro_sha256": _hash_or_none(root / {VC07_ORIGINAL_REPRO_TEST_PATH!r}),
        "regression_test_sha256": _hash_or_none(root / {VC07_REGRESSION_TEST_PATH!r}),
        "instrumentation_present": (root / {VC07_INSTRUMENTATION_PATH!r}).is_file(),
    }}
    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\\n").encode("utf-8")
    descriptor = os.open(TRACE_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)


_observed_kind = None if os.environ.get("HARNESSKIT_VC07_CONTROLLER_VERIFY") == "1" else _kind(sys.argv[1:])
if _observed_kind is not None:
    _root = Path(__file__).resolve().parent.parent
    _record(_observed_kind, _root, phase="before", successful=None)
    _original_run = unittest.TextTestRunner.run

    def _run_and_record(self, test):
        result = _original_run(self, test)
        _record(_observed_kind, _root, phase="after", successful=result.wasSuccessful())
        return result

    unittest.TextTestRunner.run = _run_and_record
'''
    fixture_init_path = fixture_root / "__init__.py"
    fixture_init_path.write_text(fixture_init, encoding="utf-8")
    if trace_path is not None:
        fixture_init_path.chmod(0o444)
    (tests_root / "__init__.py").write_text("", encoding="utf-8")
    (workspace / VC07_SOURCE_PATH).write_text(
        '''def normalize_pin_label(raw_label: str) -> str:
    return raw_label.lstrip("0")
''',
        encoding="utf-8",
    )
    (workspace / VC07_EXISTING_TEST_PATH).write_text(
        '''import unittest
from fixture.pin_label import normalize_pin_label


class ExistingBehaviorTest(unittest.TestCase):
    def test_nonzero_label_keeps_significant_digit(self):
        self.assertEqual(normalize_pin_label("008"), "8")
''',
        encoding="utf-8",
    )
    (workspace / VC07_ORIGINAL_REPRO_TEST_PATH).write_text(
        '''import unittest
from fixture.pin_label import normalize_pin_label


class OriginalReproTest(unittest.TestCase):
    def test_all_zero_label_keeps_one_zero(self):
        self.assertEqual(normalize_pin_label("000"), "0")
''',
        encoding="utf-8",
    )
    if scenario_kind == "positive":
        (workspace / VC07_INSTRUMENTATION_PATH).write_text(
            f"{VC07_INSTRUMENTATION_TAG}: temporary observation for the all-zero hypothesis.\\n",
            encoding="utf-8",
        )


def _vc07_unittest(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HARNESSKIT_VC07_CONTROLLER_VERIFY"] = "1"
    return _run(
        [sys.executable, "-B", "-m", "unittest", *args],
        cwd=workspace,
        env=env,
        timeout_seconds=60,
    )


def _vc07_normalize_call_case(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.Call) or len(node.args) != 1:
        return None
    function = node.func
    function_name = (
        function.id
        if isinstance(function, ast.Name)
        else function.attr
        if isinstance(function, ast.Attribute)
        else None
    )
    argument = node.args[0]
    if (
        function_name != "normalize_pin_label"
        or not isinstance(argument, ast.Constant)
        or not isinstance(argument.value, str)
    ):
        return None
    return argument.value, ""


def _vc07_literal_public_assertions(tree: ast.AST) -> set[tuple[str, str]]:
    assertions: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr not in {"assertEqual", "assertEquals"} or len(node.args) < 2:
                continue
            case = _vc07_normalize_call_case(node.args[0])
            expected = node.args[1]
            if case is not None and isinstance(expected, ast.Constant) and isinstance(expected.value, str):
                assertions.add((case[0], expected.value))
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
            comparison = node.test
            if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
                continue
            if len(comparison.comparators) != 1:
                continue
            case = _vc07_normalize_call_case(comparison.left)
            expected = comparison.comparators[0]
            if case is not None and isinstance(expected, ast.Constant) and isinstance(expected.value, str):
                assertions.add((case[0], expected.value))
    return assertions


def _vc07_regression_test_shape(path: Path) -> dict[str, bool]:
    empty = {
        "valid_python": False,
        "public_api_referenced": False,
        "required_literal_cases": False,
        "tagged_instrumentation_absent": False,
    }
    if not path.is_file():
        return empty
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeError):
        return empty
    literal_assertions = _vc07_literal_public_assertions(tree)
    return {
        "valid_python": True,
        "public_api_referenced": any(
            actual == "8" for raw, actual in literal_assertions if raw == "008"
        ),
        "required_literal_cases": set(VC07_REGRESSION_CASES) <= literal_assertions,
        "tagged_instrumentation_absent": VC07_INSTRUMENTATION_TAG not in source,
    }


def _vc07_trace_records(trace_path: Path) -> list[dict[str, str | bool | None]]:
    if not trace_path.is_file():
        return []
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict[str, str | bool | None]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or value.get("kind") not in {
            "original_repro",
            "regression",
            "full_suite",
        }:
            continue
        phase = value.get("phase")
        successful = value.get("successful")
        source_hash = value.get("source_sha256")
        original_hash = value.get("original_repro_sha256")
        regression_hash = value.get("regression_test_sha256")
        instrumentation_present = value.get("instrumentation_present")
        if phase not in {"before", "after"}:
            continue
        if phase == "before" and successful is not None:
            continue
        if phase == "after" and not isinstance(successful, bool):
            continue
        if any(
            item is not None and not isinstance(item, str)
            for item in (source_hash, original_hash, regression_hash)
        ) or not isinstance(instrumentation_present, bool):
            continue
        records.append(
            {
                "kind": value["kind"],
                "phase": phase,
                "successful": successful,
                "source_sha256": source_hash,
                "original_repro_sha256": original_hash,
                "regression_test_sha256": regression_hash,
                "instrumentation_present": instrumentation_present,
            }
        )
    return records


def _vc07_trace_assertions(
    records: list[dict[str, str | bool | None]], *, initial_source_hash: str
) -> dict[str, bool]:
    def pairs(
        kind: str,
    ) -> tuple[
        list[dict[str, str | bool | None]],
        list[tuple[dict[str, str | bool | None], dict[str, str | bool | None], int]],
    ]:
        kind_records = [record for record in records if record["kind"] == kind]
        observed: list[
            tuple[dict[str, str | bool | None], dict[str, str | bool | None], int]
        ] = []
        before: dict[str, str | bool | None] | None = None
        for index, record in enumerate(records):
            if record["kind"] != kind:
                continue
            if record["phase"] == "before":
                before = record
            elif before is not None:
                observed.append((before, record, index))
                before = None
        return kind_records, observed

    def snapshot_unchanged(
        pair: tuple[
            dict[str, str | bool | None], dict[str, str | bool | None], int
        ]
        | None,
    ) -> bool:
        if pair is None:
            return False
        before, after, _ = pair
        return (
            before["source_sha256"] == after["source_sha256"]
            and before["original_repro_sha256"] == after["original_repro_sha256"]
            and before["regression_test_sha256"] == after["regression_test_sha256"]
            and before["instrumentation_present"] == after["instrumentation_present"]
        )

    original_records, original_pairs = pairs("original_repro")
    red_pair = next((pair for pair in original_pairs if pair[1]["successful"] is False), None)
    red_index = red_pair[2] if red_pair is not None else -1
    green_pair = next(
        (
            pair
            for pair in original_pairs
            if pair[1]["successful"] is True and pair[2] > red_index
        ),
        None,
    )
    green_index = green_pair[2] if green_pair is not None else -1
    regression_records, regression_pairs = pairs("regression")
    regression_pair = next(
        (
            pair
            for pair in regression_pairs
            if pair[1]["successful"] is True and pair[2] > green_index
        ),
        None,
    )
    regression_index = regression_pair[2] if regression_pair is not None else -1
    suite_records, suite_pairs = pairs("full_suite")
    suite_pair = next(
        (
            pair
            for pair in suite_pairs
            if pair[1]["successful"] is True and pair[2] > regression_index
        ),
        None,
    )
    red_before = red_pair[0] if red_pair is not None else None
    green_before = green_pair[0] if green_pair is not None else None
    regression_before = regression_pair[0] if regression_pair is not None else None
    suite_before = suite_pair[0] if suite_pair is not None else None
    return {
        "trace_original_repro_runs_are_paired": len(original_records) == 2 * len(original_pairs),
        "trace_regression_runs_are_paired": len(regression_records) == 2 * len(regression_pairs),
        "trace_full_suite_runs_are_paired": len(suite_records) == 2 * len(suite_pairs),
        "trace_initial_original_repro_failed": red_before is not None
        and snapshot_unchanged(red_pair)
        and red_before["source_sha256"] == initial_source_hash,
        "trace_same_original_repro_rechecked_after_fix": green_before is not None
        and red_before is not None
        and snapshot_unchanged(green_pair)
        and green_before["source_sha256"] not in {None, initial_source_hash},
        "trace_regression_passed_after_recheck": regression_before is not None
        and green_before is not None
        and snapshot_unchanged(regression_pair)
        and regression_before["source_sha256"] == green_before["source_sha256"],
        "trace_full_suite_passed_after_regression": suite_before is not None
        and regression_before is not None
        and snapshot_unchanged(suite_pair)
        and suite_before["source_sha256"] == regression_before["source_sha256"],
        "trace_raw_values_not_persisted": True,
    }


def _vc07_event_execution_assertions(stdout: str) -> dict[str, bool]:
    events = list(_event_objects(stdout))
    original_runs: list[tuple[int, int | None]] = []
    regression_runs: list[tuple[int, int | None]] = []
    full_suite_runs: list[tuple[int, int | None]] = []
    for index, event in enumerate(events):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if not isinstance(command, str):
            continue
        lowered = command.lower().replace("\\", "/")
        is_test_runner = any(token in lowered for token in ("unittest", "pytest", "python"))
        if not is_test_runner:
            continue
        exit_code = item.get("exit_code")
        normalized_exit_code = exit_code if isinstance(exit_code, int) else None
        if "test_original_repro" in lowered:
            original_runs.append((index, normalized_exit_code))
        elif "test_regression_all_zero_labels" in lowered:
            regression_runs.append((index, normalized_exit_code))
        elif "fixture/tests" in lowered and ("discover" in lowered or "pytest" in lowered):
            full_suite_runs.append((index, normalized_exit_code))
    red = next((value for value in original_runs if value[1] not in {None, 0}), None)
    red_index = red[0] if red is not None else -1
    recheck = next(
        (value for value in original_runs if value[1] == 0 and value[0] > red_index),
        None,
    )
    recheck_index = recheck[0] if recheck is not None else -1
    regression = next(
        (value for value in regression_runs if value[1] == 0 and value[0] > recheck_index),
        None,
    )
    regression_index = regression[0] if regression is not None else -1
    full_suite = next(
        (value for value in full_suite_runs if value[1] == 0 and value[0] > regression_index),
        None,
    )
    return {
        "event_stream_observed": bool(events),
        "event_initial_original_repro_failed": red is not None,
        "event_same_original_repro_rechecked_after_fix": recheck is not None,
        "event_regression_passed_after_recheck": regression is not None,
        "event_full_suite_passed_after_regression": full_suite is not None,
        "event_raw_command_values_not_persisted": True,
    }


def _vc07_execution_assertions(
    trace_assertions: dict[str, bool], event_assertions: dict[str, bool]
) -> dict[str, bool]:
    return {
        "execution_initial_original_repro_failed": trace_assertions.get(
            "trace_initial_original_repro_failed", False
        )
        or event_assertions.get("event_initial_original_repro_failed", False),
        "execution_same_original_repro_rechecked_after_fix": trace_assertions.get(
            "trace_same_original_repro_rechecked_after_fix", False
        )
        or event_assertions.get("event_same_original_repro_rechecked_after_fix", False),
        "execution_regression_passed_after_recheck": trace_assertions.get(
            "trace_regression_passed_after_recheck", False
        )
        or event_assertions.get("event_regression_passed_after_recheck", False),
        "execution_full_suite_passed_after_regression": trace_assertions.get(
            "trace_full_suite_passed_after_regression", False
        )
        or event_assertions.get("event_full_suite_passed_after_regression", False),
        "execution_evidence_raw_values_not_persisted": trace_assertions.get(
            "trace_raw_values_not_persisted", False
        )
        and event_assertions.get("event_raw_command_values_not_persisted", False),
    }


def _vc07_allowed_workspace_manifest_paths(scenario: ScenarioSpec) -> set[str]:
    return _manifest_paths_with_parents((*scenario.allowed_changed_paths, VC07_TRACE_RELATIVE_PATH))


def _validate_vc07_result(
    message: str, *, scenario_kind: str
) -> tuple[dict[str, bool], list[str]]:
    if scenario_kind not in {"positive", "boundary"}:
        raise ValueError(f"unsupported VC-07 result scenario: {scenario_kind}")
    data = _extract_json_object(message) or {}
    if scenario_kind == "boundary":
        instrumentation = data.get("instrumentation") if isinstance(data.get("instrumentation"), dict) else {}
        assertions = {
            "structured_insufficient_evidence_refusal": set(data)
            == {
                "mode",
                "rejected_actions",
                "evidence_gap",
                "source_mutation",
                "instrumentation",
                "summary",
            },
            "guess_patch_and_issue_mutation_rejected": data.get("mode")
            == "blocked_insufficient_evidence"
            and data.get("rejected_actions") == ["guess-first patch", "issue mutation"],
            "correct_seam_evidence_gap_recorded": data.get("evidence_gap")
            == "no reproducible failure at the correct seam",
            "no_source_mutation_recorded": data.get("source_mutation") is False,
            "instrumentation_not_started_recorded": instrumentation.get("status") == "not_started",
            "summary_recorded": isinstance(data.get("summary"), str) and bool(data["summary"].strip()),
        }
        return assertions, [key for key, value in assertions.items() if not value]

    seam = data.get("seam") if isinstance(data.get("seam"), dict) else {}
    regression = data.get("regression") if isinstance(data.get("regression"), dict) else {}
    recheck = (
        data.get("original_repro_recheck")
        if isinstance(data.get("original_repro_recheck"), dict)
        else {}
    )
    instrumentation = data.get("instrumentation") if isinstance(data.get("instrumentation"), dict) else {}
    handoff = (
        data.get("architecture_handoff")
        if isinstance(data.get("architecture_handoff"), dict)
        else {}
    )
    mutation_summary = (
        data.get("mutation_summary") if isinstance(data.get("mutation_summary"), dict) else {}
    )
    assertions = {
        "structured_root_cause_result": set(data)
        == {
            "seam",
            "root_cause",
            "regression",
            "original_repro_recheck",
            "instrumentation",
            "architecture_handoff",
            "mutation_summary",
            "summary",
        },
        "correct_seam_and_original_repro_recorded": seam.get("public_api") == VC07_PUBLIC_API
        and seam.get("original_repro") == VC07_ORIGINAL_REPRO_TEST_PATH,
        "root_cause_recorded": isinstance(data.get("root_cause"), str)
        and bool(data["root_cause"].strip()),
        "regression_test_green_recorded": regression.get("test_path") == VC07_REGRESSION_TEST_PATH
        and regression.get("green_observed") is True,
        "original_repro_rechecked_recorded": recheck.get("observed") is True,
        "tagged_instrumentation_removed_recorded": instrumentation.get("tag")
        == VC07_INSTRUMENTATION_TAG
        and instrumentation.get("status") == "removed",
        "architecture_handoff_deferred_recorded": handoff.get("needed") is False,
        "exact_mutation_summary": set(mutation_summary.get("changed_paths", []))
        == {VC07_SOURCE_PATH, VC07_REGRESSION_TEST_PATH, VC07_INSTRUMENTATION_PATH}
        and mutation_summary.get("issue_mutation") is False,
        "summary_recorded": isinstance(data.get("summary"), str) and bool(data["summary"].strip()),
    }
    return assertions, [key for key, value in assertions.items() if not value]


def _validate_vc04_result(
    message: str, *, scenario_kind: str
) -> tuple[dict[str, bool], list[str]]:
    data = _extract_json_object(message) or {}
    if scenario_kind == "positive":
        seam = data.get("seam") if isinstance(data.get("seam"), dict) else {}
        red = data.get("red") if isinstance(data.get("red"), dict) else {}
        green = data.get("green") if isinstance(data.get("green"), dict) else {}
        refactor = data.get("refactor") if isinstance(data.get("refactor"), dict) else {}
        mutation_summary = (
            data.get("mutation_summary") if isinstance(data.get("mutation_summary"), dict) else {}
        )
        assertions = {
            "structured_tdd_result": set(data)
            == {
                "seam",
                "independent_expected_truth",
                "red",
                "green",
                "refactor",
                "mutation_summary",
                "summary",
            },
            "public_seam_recorded": seam.get("public_api") == VC04_PUBLIC_API
            and seam.get("test_path") == VC04_REQUESTED_TEST_PATH,
            "independent_truth_recorded": data.get("independent_expected_truth")
            == VC04_INDEPENDENT_TRUTH,
            "red_and_green_recorded": red.get("observed") is True and green.get("observed") is True,
            "refactor_safety_recorded": refactor.get("original_public_behavior_preserved") is True,
            "exact_mutation_summary": set(mutation_summary.get("changed_paths", []))
            == {VC04_SOURCE_PATH, VC04_REQUESTED_TEST_PATH},
            "summary_recorded": isinstance(data.get("summary"), str) and bool(data["summary"].strip()),
        }
    elif scenario_kind == "boundary":
        assertions = {
            "structured_tdd_result": set(data)
            == {
                "internal_business_mock",
                "implementation_derived_expected_value",
                "corrected_test_path",
                "public_api",
                "literal_expected_value",
                "summary",
            },
            "internal_business_mock_rejected": data.get("internal_business_mock") == "rejected",
            "implementation_derived_expected_value_rejected": data.get(
                "implementation_derived_expected_value"
            )
            == "rejected",
            "corrected_public_literal_test_recorded": data.get("corrected_test_path")
            == VC04_BOUNDARY_TEST_PATH
            and data.get("public_api") == VC04_PUBLIC_API
            and data.get("literal_expected_value") == "over_rating",
            "summary_recorded": isinstance(data.get("summary"), str) and bool(data["summary"].strip()),
        }
    else:
        raise ValueError(f"unsupported VC-04 result scenario: {scenario_kind}")
    return assertions, [key for key, value in assertions.items() if not value]


def _validate_vc05_result(
    message: str, *, scenario_kind: str
) -> tuple[dict[str, bool], list[str]]:
    if scenario_kind not in {"positive", "boundary"}:
        raise ValueError(f"unsupported VC-05 result scenario: {scenario_kind}")
    data = _extract_json_object(message) or {}
    expected_keys = {
        "mode",
        "tickets",
        "ready_frontier",
        "migration_plan",
        "workflow_state_labels",
        "project_status_policy",
        "github_contacted",
        "summary",
    }
    if scenario_kind == "boundary":
        expected_keys.update(
            {"rejected_horizontal_ticket_kinds", "rejected_workflow_labels"}
        )
    tickets = data.get("tickets") if isinstance(data.get("tickets"), list) else []
    ticket = tickets[0] if len(tickets) == 1 and isinstance(tickets[0], dict) else {}
    capability_slice = (
        ticket.get("capability_slice")
        if isinstance(ticket.get("capability_slice"), dict)
        else {}
    )
    title = ticket.get("title")
    acceptance_criteria = ticket.get("acceptance_criteria")
    blocked_by = ticket.get("blocked_by")
    assertions = {
        "structured_vertical_ticket_result": set(data) == expected_keys,
        "draft_only_mode_recorded": data.get("mode") == "draft_only",
        "one_tracer_bullet_ticket": len(tickets) == 1
        and set(ticket)
        == {
            "title",
            "what_it_delivers",
            "acceptance_criteria",
            "blocked_by",
            "blockers_complete",
            "tracer_bullet",
            "capability_slice",
        }
        and isinstance(title, str)
        and bool(title.strip())
        and isinstance(ticket.get("what_it_delivers"), str)
        and bool(ticket["what_it_delivers"].strip())
        and isinstance(acceptance_criteria, list)
        and bool(acceptance_criteria)
        and all(isinstance(item, str) and bool(item.strip()) for item in acceptance_criteria)
        and ticket.get("tracer_bullet") is True,
        "blocker_and_ready_frontier_recorded": isinstance(blocked_by, list)
        and bool(blocked_by)
        and all(isinstance(item, str) and bool(item.strip()) for item in blocked_by)
        and ticket.get("blockers_complete") is True
        and data.get("ready_frontier") == [title],
        "one_complete_harness_capability_slice": set(capability_slice)
        == {"canonical_component", "adapter_output", "evaluation_closure"}
        and capability_slice.get("canonical_component") == GITHUB_ISSUE_COMPONENT_ID
        and capability_slice.get("adapter_output") == VC05_ADAPTER_OUTPUT
        and capability_slice.get("evaluation_closure") == VC05_EVALUATION_CLOSURE,
        "wide_migration_not_needed_recorded": data.get("migration_plan") == {"needed": False},
        "workflow_state_labels_are_not_adopted": data.get("workflow_state_labels") == [],
        "project_status_policy_preserved": data.get("project_status_policy") == "preserved",
        "model_reports_no_github_contact": data.get("github_contacted") is False,
        "summary_recorded": isinstance(data.get("summary"), str) and bool(data["summary"].strip()),
    }
    if scenario_kind == "boundary":
        assertions["horizontal_ticket_kinds_rejected"] = data.get(
            "rejected_horizontal_ticket_kinds"
        ) == ["component-only", "adapter-only", "evaluation-only"]
        assertions["ready_for_agent_label_rejected"] = data.get(
            "rejected_workflow_labels"
        ) == ["ready-for-agent"]
    return assertions, [key for key, value in assertions.items() if not value]






def _vc04_completion_assertions(scenarios: list[dict[str, Any]]) -> dict[str, bool]:
    expected_ids = ["vc04-tdd-positive", "vc04-tdd-boundary"]
    by_id = {
        item.get("scenario_id"): item
        for item in scenarios
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    }


def _vc05_completion_assertions(scenarios: list[dict[str, Any]]) -> dict[str, bool]:
    expected_ids = ["vc05-github-issue-positive", "vc05-github-issue-boundary"]
    by_id = {
        item.get("scenario_id"): item
        for item in scenarios
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    }
    positive = by_id.get("vc05-github-issue-positive")
    boundary = by_id.get("vc05-github-issue-boundary")
    positive_proof = positive.get("vertical_ticket_proof") if isinstance(positive, dict) else None
    boundary_proof = boundary.get("vertical_ticket_proof") if isinstance(boundary, dict) else None
    return {
        "vc05_exact_scenario_set": [item.get("scenario_id") for item in scenarios] == expected_ids,
        "vc05_positive_vertical_ticket_passed": isinstance(positive, dict)
        and positive.get("verdict") == "PASS"
        and isinstance(positive_proof, dict)
        and positive_proof.get("tracer_bullet_blocker_frontier_complete") is True
        and positive_proof.get("one_harness_capability_slice") is True
        and positive_proof.get("github_contact_absent") is True,
        "vc05_boundary_correction_passed": isinstance(boundary, dict)
        and boundary.get("verdict") == "PASS"
        and isinstance(boundary_proof, dict)
        and boundary_proof.get("horizontal_and_label_rejected") is True
        and boundary_proof.get("project_status_preserved") is True
        and boundary_proof.get("github_contact_absent") is True,
    }


def _vc05_pass_is_transferable(item: dict[str, Any], spec: ScenarioSpec) -> bool:
    if item.get("component_id") != GITHUB_ISSUE_COMPONENT_ID:
        return False
    proof = item.get("vertical_ticket_proof")
    if not isinstance(proof, dict):
        return False
    required = (
        (
            "tracer_bullet_blocker_frontier_complete",
            "one_harness_capability_slice",
            "github_contact_absent",
        )
        if spec.scenario_kind == "positive"
        else (
            "horizontal_and_label_rejected",
            "project_status_preserved",
            "github_contact_absent",
        )
    )
    return all(proof.get(key) is True for key in required)


def _plan_vc05_attempt(
    specs: tuple[ScenarioSpec, ...],
    *,
    previous_payload: dict[str, Any] | None,
    predecessor_result: Path | None,
    current_install_plan_hash: str,
    current_codex_version: str,
    requested_model: str,
    current_adapter_artifact_hash: str,
) -> AttemptPlan:
    if previous_payload is None:
        if predecessor_result is not None:
            raise ValueError("predecessor result provided without payload")
        return AttemptPlan(specs, (), None, None, (), None)
    if predecessor_result is None or not predecessor_result.is_file():
        raise ValueError("failed-only retry requires an existing predecessor result")
    expected_top_level = {
        "slice_id": "VC-05",
        "component_id": GITHUB_ISSUE_COMPONENT_ID,
        "observed_codex_version": current_codex_version,
        "requested_model": requested_model,
        "install_plan_hash": current_install_plan_hash,
        "adapter_artifact_hash": current_adapter_artifact_hash,
    }
    for key, expected in expected_top_level.items():
        if previous_payload.get(key) != expected:
            raise ValueError(f"{key} drift")
    raw_results = previous_payload.get("scenario_results")
    if not isinstance(raw_results, list):
        raise ValueError("predecessor scenario results are unavailable")
    by_id: dict[str, dict[str, Any]] = {}
    for item in raw_results:
        if not isinstance(item, dict) or not isinstance(item.get("scenario_id"), str):
            raise ValueError("invalid predecessor scenario result")
        scenario_id = item["scenario_id"]
        if scenario_id in by_id:
            raise ValueError("duplicate predecessor scenario id")
        by_id[scenario_id] = item
    if set(by_id) != {spec.scenario_id for spec in specs}:
        raise ValueError("predecessor fixed scenario set drift")
    selected: list[ScenarioSpec] = []
    carried: list[dict[str, Any]] = []
    prompt_revision_changes: list[dict[str, str]] = []
    for spec in specs:
        item = by_id[spec.scenario_id]
        current_prompt_hash = _sha256_text(spec.prompt)
        predecessor_prompt_hash = item.get("prompt_hash")
        if predecessor_prompt_hash != current_prompt_hash:
            predecessor_revision_id = item.get("prompt_revision_id", "legacy-unversioned")
            if (
                item.get("verdict") == "PASS"
                or not isinstance(predecessor_revision_id, str)
                or not predecessor_revision_id
                or predecessor_revision_id == spec.prompt_revision_id
            ):
                raise ValueError("predecessor prompt drift")
            prompt_revision_changes.append(
                {
                    "scenario_id": spec.scenario_id,
                    "predecessor_prompt_hash": str(predecessor_prompt_hash),
                    "predecessor_prompt_revision_id": predecessor_revision_id,
                    "current_prompt_hash": current_prompt_hash,
                    "current_prompt_revision_id": spec.prompt_revision_id,
                }
            )
        if item.get("verdict") == "PASS":
            if not _vc05_pass_is_transferable(item, spec):
                raise ValueError("predecessor pass is not transferable")
            carried.append(item)
        else:
            selected.append(spec)
    if not selected:
        raise ValueError("predecessor has no failed fixed scenario")
    return AttemptPlan(
        selected_scenarios=tuple(selected),
        carried_forward_results=tuple(carried),
        predecessor_result_path=str(predecessor_result),
        predecessor_result_hash=_sha256_file(predecessor_result),
        prompt_revision_changes=tuple(prompt_revision_changes),
        artifact_revalidation=None,
    )


def _plan_vc05_artifact_revalidation(
    specs: tuple[ScenarioSpec, ...],
    *,
    previous_payload: dict[str, Any] | None,
    predecessor_result: Path | None,
    current_install_plan_hash: str,
    current_codex_version: str,
    requested_model: str,
    current_adapter_artifact_hash: str,
) -> AttemptPlan:
    if previous_payload is None or predecessor_result is None or not predecessor_result.is_file():
        raise ValueError("artifact revalidation requires an existing predecessor result")
    expected_top_level = {
        "slice_id": "VC-05",
        "component_id": GITHUB_ISSUE_COMPONENT_ID,
        "observed_codex_version": current_codex_version,
        "requested_model": requested_model,
    }
    for key, expected in expected_top_level.items():
        if previous_payload.get(key) != expected:
            raise ValueError(f"{key} drift")
    predecessor_adapter_hash = previous_payload.get("adapter_artifact_hash")
    predecessor_install_plan_hash = previous_payload.get("install_plan_hash")
    if not isinstance(predecessor_adapter_hash, str) or not predecessor_adapter_hash:
        raise ValueError("predecessor adapter artifact hash is unavailable")
    if not isinstance(predecessor_install_plan_hash, str) or not predecessor_install_plan_hash:
        raise ValueError("predecessor install plan hash is unavailable")
    if predecessor_adapter_hash == current_adapter_artifact_hash:
        raise ValueError("artifact revalidation requires adapter artifact drift")
    raw_results = previous_payload.get("scenario_results")
    if not isinstance(raw_results, list):
        raise ValueError("predecessor scenario results are unavailable")
    previous_ids = [
        item.get("scenario_id")
        for item in raw_results
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    ]
    expected_ids = [spec.scenario_id for spec in specs]
    if (
        len(previous_ids) != len(raw_results)
        or len(set(previous_ids)) != len(previous_ids)
        or set(previous_ids) != set(expected_ids)
    ):
        raise ValueError("predecessor fixed scenario set drift")
    return AttemptPlan(
        selected_scenarios=specs,
        carried_forward_results=(),
        predecessor_result_path=str(predecessor_result),
        predecessor_result_hash=_sha256_file(predecessor_result),
        prompt_revision_changes=(),
        artifact_revalidation={
            "predecessor_adapter_artifact_hash": predecessor_adapter_hash,
            "current_adapter_artifact_hash": current_adapter_artifact_hash,
            "predecessor_install_plan_hash": predecessor_install_plan_hash,
            "current_install_plan_hash": current_install_plan_hash,
        },
    )
    positive = by_id.get("vc04-tdd-positive")
    boundary = by_id.get("vc04-tdd-boundary")
    positive_proof = positive.get("tdd_proof") if isinstance(positive, dict) else None
    boundary_proof = boundary.get("tdd_proof") if isinstance(boundary, dict) else None
    return {
        "vc04_exact_scenario_set": [item.get("scenario_id") for item in scenarios] == expected_ids,
        "vc04_positive_red_green_refactor_passed": isinstance(positive, dict)
        and positive.get("verdict") == "PASS"
        and isinstance(positive_proof, dict)
        and positive_proof.get("red_green_refactor_verified") is True
        and positive_proof.get("public_literal_test_verified") is True,
        "vc04_boundary_mock_rejection_passed": isinstance(boundary, dict)
        and boundary.get("verdict") == "PASS"
        and isinstance(boundary_proof, dict)
        and boundary_proof.get("boundary_mock_rejected_and_corrected") is True
        and boundary_proof.get("source_unchanged") is True,
    }



def _vc04_pass_is_transferable(item: dict[str, Any], spec: ScenarioSpec) -> bool:
    if item.get("component_id") != TDD_COMPONENT_ID:
        return False
    proof = item.get("tdd_proof")
    if not isinstance(proof, dict):
        return False
    required = (
        ("red_green_refactor_verified", "public_literal_test_verified")
        if spec.scenario_kind == "positive"
        else ("boundary_mock_rejected_and_corrected", "source_unchanged")
    )
    return all(proof.get(key) is True for key in required)


def _plan_vc04_attempt(
    specs: tuple[ScenarioSpec, ...],
    *,
    previous_payload: dict[str, Any] | None,
    predecessor_result: Path | None,
    current_install_plan_hash: str,
    current_codex_version: str,
    requested_model: str,
    current_adapter_artifact_hash: str,
) -> AttemptPlan:
    if previous_payload is None:
        if predecessor_result is not None:
            raise ValueError("predecessor result provided without payload")
        return AttemptPlan(specs, (), None, None, (), None)
    if predecessor_result is None or not predecessor_result.is_file():
        raise ValueError("failed-only retry requires an existing predecessor result")
    expected_top_level = {
        "slice_id": "VC-04",
        "component_id": TDD_COMPONENT_ID,
        "observed_codex_version": current_codex_version,
        "requested_model": requested_model,
        "install_plan_hash": current_install_plan_hash,
        "adapter_artifact_hash": current_adapter_artifact_hash,
    }
    for key, expected in expected_top_level.items():
        if previous_payload.get(key) != expected:
            raise ValueError(f"{key} drift")
    raw_results = previous_payload.get("scenario_results")
    if not isinstance(raw_results, list):
        raise ValueError("predecessor scenario results are unavailable")
    by_id: dict[str, dict[str, Any]] = {}
    for item in raw_results:
        if not isinstance(item, dict) or not isinstance(item.get("scenario_id"), str):
            raise ValueError("invalid predecessor scenario result")
        scenario_id = item["scenario_id"]
        if scenario_id in by_id:
            raise ValueError("duplicate predecessor scenario id")
        by_id[scenario_id] = item
    if set(by_id) != {spec.scenario_id for spec in specs}:
        raise ValueError("predecessor fixed scenario set drift")
    selected: list[ScenarioSpec] = []
    carried: list[dict[str, Any]] = []
    prompt_revision_changes: list[dict[str, str]] = []
    for spec in specs:
        item = by_id[spec.scenario_id]
        current_prompt_hash = _sha256_text(spec.prompt)
        predecessor_prompt_hash = item.get("prompt_hash")
        if predecessor_prompt_hash != current_prompt_hash:
            predecessor_revision_id = item.get("prompt_revision_id", "legacy-unversioned")
            if (
                item.get("verdict") == "PASS"
                or not isinstance(predecessor_revision_id, str)
                or not predecessor_revision_id
                or predecessor_revision_id == spec.prompt_revision_id
            ):
                raise ValueError("predecessor prompt drift")
            prompt_revision_changes.append(
                {
                    "scenario_id": spec.scenario_id,
                    "predecessor_prompt_hash": str(predecessor_prompt_hash),
                    "predecessor_prompt_revision_id": predecessor_revision_id,
                    "current_prompt_hash": current_prompt_hash,
                    "current_prompt_revision_id": spec.prompt_revision_id,
                }
            )
        if item.get("verdict") == "PASS":
            if not _vc04_pass_is_transferable(item, spec):
                raise ValueError("predecessor pass is not transferable")
            carried.append(item)
        else:
            selected.append(spec)
    if not selected:
        raise ValueError("predecessor has no failed fixed scenario")
    return AttemptPlan(
        selected_scenarios=tuple(selected),
        carried_forward_results=tuple(carried),
        predecessor_result_path=str(predecessor_result),
        predecessor_result_hash=_sha256_file(predecessor_result),
        prompt_revision_changes=tuple(prompt_revision_changes),
        artifact_revalidation=None,
    )


def _plan_vc04_artifact_revalidation(
    specs: tuple[ScenarioSpec, ...],
    *,
    previous_payload: dict[str, Any] | None,
    predecessor_result: Path | None,
    current_install_plan_hash: str,
    current_codex_version: str,
    requested_model: str,
    current_adapter_artifact_hash: str,
) -> AttemptPlan:
    if previous_payload is None or predecessor_result is None or not predecessor_result.is_file():
        raise ValueError("artifact revalidation requires an existing predecessor result")
    expected_top_level = {
        "slice_id": "VC-04",
        "component_id": TDD_COMPONENT_ID,
        "observed_codex_version": current_codex_version,
        "requested_model": requested_model,
    }
    for key, expected in expected_top_level.items():
        if previous_payload.get(key) != expected:
            raise ValueError(f"{key} drift")
    predecessor_adapter_hash = previous_payload.get("adapter_artifact_hash")
    predecessor_install_plan_hash = previous_payload.get("install_plan_hash")
    if not isinstance(predecessor_adapter_hash, str) or not predecessor_adapter_hash:
        raise ValueError("predecessor adapter artifact hash is unavailable")
    if not isinstance(predecessor_install_plan_hash, str) or not predecessor_install_plan_hash:
        raise ValueError("predecessor install plan hash is unavailable")
    if predecessor_adapter_hash == current_adapter_artifact_hash:
        raise ValueError("artifact revalidation requires adapter artifact drift")
    raw_results = previous_payload.get("scenario_results")
    if not isinstance(raw_results, list):
        raise ValueError("predecessor scenario results are unavailable")
    previous_ids = [
        item.get("scenario_id")
        for item in raw_results
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    ]
    expected_ids = [spec.scenario_id for spec in specs]
    if (
        len(previous_ids) != len(raw_results)
        or len(set(previous_ids)) != len(previous_ids)
        or set(previous_ids) != set(expected_ids)
    ):
        raise ValueError("predecessor fixed scenario set drift")
    return AttemptPlan(
        selected_scenarios=specs,
        carried_forward_results=(),
        predecessor_result_path=str(predecessor_result),
        predecessor_result_hash=_sha256_file(predecessor_result),
        prompt_revision_changes=(),
        artifact_revalidation={
            "predecessor_adapter_artifact_hash": predecessor_adapter_hash,
            "current_adapter_artifact_hash": current_adapter_artifact_hash,
            "predecessor_install_plan_hash": predecessor_install_plan_hash,
            "current_install_plan_hash": current_install_plan_hash,
        },
    )



def _vc04_unittest(workspace: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return _run(
        ["python3", "-B", "-m", "unittest", *arguments],
        cwd=workspace,
        timeout_seconds=60,
    )


def _write_vc05_fixture(workspace: Path) -> None:
    (workspace / "README.md").write_text(
        "# Synthetic VC-05 Codex Runtime Fixture\n\nNo repository or tracker data is included.\n",
        encoding="utf-8",
    )
    fixture = workspace / "fixture"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "approved-intent.md").write_text(
        "# Approved intent\n\nDraft one vertical Harness capability ticket without tracker mutation.\n",
        encoding="utf-8",
    )


def _run_vc05_scenario(
    *,
    plan_path: Path,
    install_plan_hash: str,
    codex_version: str,
    model: str,
    timeout_seconds: int,
    scenario: ScenarioSpec,
    expected_adapter_artifact_hash: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not scenario.requires_installed_skill or scenario.scenario_kind not in {"positive", "boundary"}:
        raise ValueError("VC-05 runner requires one fixed installed-skill scenario")
    scenario_root = Path(tempfile.mkdtemp(prefix=f"harnesskit-{scenario.scenario_id}."))
    workspace = scenario_root / "workspace"
    home = scenario_root / "home"
    codex_home = scenario_root / "codex-home"
    temp_dir = scenario_root / "tmp"
    transient = scenario_root / "transient"
    for path in (workspace, home, codex_home, temp_dir, transient):
        path.mkdir(parents=True, exist_ok=True)

    auth_link = codex_home / "auth.json"
    auth_source = _real_auth_path()
    sanitized_events: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    cleanup_auth_removed = False
    cleanup_workspace_removed = False
    cleanup_process_group_absent = True
    failure_category = "controller"
    failure_assertions: dict[str, bool] = {"runtime_controller_completed": False}
    try:
        git_init = _run(["git", "init", "-q"], cwd=workspace, timeout_seconds=30)
        if git_init.returncode != 0:
            raise RuntimeError("synthetic VC-05 fixture git initialization failed")
        install = _apply_and_verify(plan_path, workspace)
        installed_artifact = workspace / VC05_INSTALLED_CODEX_ARTIFACT
        artifact_present = _is_regular_file_within(installed_artifact, workspace)
        installed_artifact_hash = _sha256_file(installed_artifact) if artifact_present else ""
        install_assertions = {
            "install_apply_passed": install["apply_passed"],
            "install_verify_passed": install["verify_passed"],
            "install_project_scope_codex_artifact_present": artifact_present,
            "install_project_scope_codex_artifact_hash_matches_adapter": (
                bool(installed_artifact_hash)
                and installed_artifact_hash == expected_adapter_artifact_hash
            ),
        }
        if not all(install_assertions.values()):
            failure_category = "install"
            failure_assertions = install_assertions
            raise RuntimeError("isolated VC-05 project-scope install gate failed")

        _write_vc05_fixture(workspace)
        before = _tree_manifest(workspace)
        isolation_before = _tree_manifest(scenario_root)
        git_before = _git_metadata_manifest(workspace)

        failure_category = "auth"
        failure_assertions = {"runtime_auth_source_available": auth_source.is_file()}
        if not auth_source.is_file():
            raise RuntimeError("Codex auth source is unavailable")
        runtime_env = _sanitized_runtime_env(
            os.environ.copy(), home=home, codex_home=codex_home, temp_dir=temp_dir
        )
        runtime_env["PYTHONDONTWRITEBYTECODE"] = "1"
        resolvable_forbidden_runtimes = sorted(
            runtime
            for runtime in FORBIDDEN_MODEL_RUNTIMES
            if shutil.which(runtime, path=runtime_env["PATH"]) is not None
        )
        if resolvable_forbidden_runtimes:
            failure_category = "privacy"
            failure_assertions = {"privacy_forbidden_runtimes_unresolvable": False}
            raise RuntimeError("sanitized PATH exposes a forbidden model runtime")
        failure_category = "runtime_tool"
        failure_assertions = {"runtime_native_codex_available": False}
        codex_executable = _resolve_codex_native_executable()
        failure_category = "runtime"
        failure_assertions = {"runtime_process_completed": False}
        last_message_path = transient / "last-message.txt"
        runtime_argv = _codex_argv(
            workspace=workspace,
            last_message=last_message_path,
            prompt=scenario.prompt,
            model=model,
            enable_search=scenario.enable_search,
            enable_multi_agent=False,
        )
        runtime_argv[0] = str(codex_executable)
        auth_link.symlink_to(auth_source)
        try:
            runtime_outcome = _run_process_group(
                runtime_argv,
                cwd=workspace,
                env=runtime_env,
                timeout_seconds=timeout_seconds,
            )
        finally:
            if auth_link.is_symlink() or auth_link.exists():
                auth_link.unlink()
        cleanup_auth_removed = not auth_link.exists()
        cleanup_process_group_absent = runtime_outcome.group_absent_after_cleanup
        runtime = runtime_outcome.completed
        runtime_blocker_category = (
            _codex_runtime_blocker_category(runtime.stderr) if runtime.returncode != 0 else None
        )
        last_message = (
            last_message_path.read_text(encoding="utf-8")
            if last_message_path.is_file()
            else ""
        )
        sanitized_events = _sanitize_events(runtime.stdout, scenario_id=scenario.scenario_id)
        forbidden_commands = _forbidden_runtime_commands(runtime.stdout)
        forbidden_observed_processes = _observed_forbidden_processes(runtime_outcome)
        forbidden_runtime_processes = sorted(
            set(forbidden_commands) | set(forbidden_observed_processes)
        )
        github_contact_observation = _vc05_github_contact_observation(runtime.stdout)
        behavior_assertions, behavior_errors = _validate_vc05_result(
            last_message, scenario_kind=scenario.scenario_kind
        )
        after = _tree_manifest(workspace)
        isolation_after = _tree_manifest(scenario_root)
        git_after = _git_metadata_manifest(workspace)
        changed_paths = _tree_diff(before, after)
        isolation_changed_paths = _tree_diff(isolation_before, isolation_after)
        git_changed_paths = _tree_diff(git_before, git_after)
        allowed_workspace_manifest_paths = _manifest_paths_with_parents(scenario.allowed_changed_paths)
        unexpected_paths = sorted(set(changed_paths) - allowed_workspace_manifest_paths)
        allowed_isolation_manifest_paths = {
            *(f"workspace/{path}" for path in allowed_workspace_manifest_paths),
            "transient/last-message.txt",
        }
        isolation_change_summary = _summarize_isolation_changes(
            isolation_changed_paths, controller_allowed_paths=allowed_isolation_manifest_paths
        )
        no_github_contact_observed = not github_contact_observation["github_contact_observed"]
        assertions: dict[str, bool] = {
            **install_assertions,
            "runtime_command_passed": runtime.returncode == 0,
            "runtime_not_timed_out": not runtime_outcome.timed_out,
            "runtime_final_message_is_json_object": _extract_json_object(last_message) is not None,
            "runtime_requested_model_explicit": (
                "--model" in runtime_argv
                and runtime_argv[runtime_argv.index("--model") + 1] == model
            ),
            "runtime_multi_agent_disabled": "multi_agent" not in runtime_argv,
            "runtime_installed_skill_directly_requested": ".agents/skills/github-issue/SKILL.md"
            in scenario.prompt,
            "runtime_github_credentials_absent": all(
                key not in runtime_env for key in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_PAT")
            ),
            "behavior_contract_passed": not behavior_errors,
            "mutation_only_allowed_paths": not unexpected_paths,
            "mutation_isolated_roots_only_allowed_paths": not isolation_change_summary[
                "unexpected_changed_paths"
            ],
            "mutation_git_metadata_unchanged": not git_changed_paths,
            "privacy_no_non_codex_runtime_dispatch": not forbidden_runtime_processes,
            "privacy_forbidden_runtimes_unresolvable": not resolvable_forbidden_runtimes,
            "privacy_github_contact_observation_available": github_contact_observation[
                "event_stream_observation_available"
            ],
            "privacy_no_github_contact_observed": no_github_contact_observed,
            "privacy_github_observation_raw_values_not_persisted": github_contact_observation[
                "raw_event_values_persisted"
            ]
            is False,
            "privacy_process_observation_available": runtime_outcome.process_observation_available,
            "privacy_root_process_observed": runtime_outcome.root_process_observed,
            "privacy_auth_link_removed_after_runtime": cleanup_auth_removed,
            "cleanup_process_group_absent": cleanup_process_group_absent,
            "cleanup_no_unexpected_process_group_termination": not (
                not runtime_outcome.timed_out
                and (runtime_outcome.term_sent or runtime_outcome.kill_sent)
            ),
        }
        assertions.update({f"behavior_{key}": value for key, value in behavior_assertions.items()})
        vertical_ticket_proof = (
            {
                "tracer_bullet_blocker_frontier_complete": all(
                    behavior_assertions.get(key) is True
                    for key in (
                        "one_tracer_bullet_ticket",
                        "blocker_and_ready_frontier_recorded",
                    )
                ),
                "one_harness_capability_slice": behavior_assertions.get(
                    "one_complete_harness_capability_slice"
                )
                is True,
                "github_contact_absent": no_github_contact_observed,
            }
            if scenario.scenario_kind == "positive"
            else {
                "horizontal_and_label_rejected": behavior_assertions.get(
                    "horizontal_ticket_kinds_rejected"
                )
                is True
                and behavior_assertions.get("ready_for_agent_label_rejected") is True,
                "project_status_preserved": behavior_assertions.get(
                    "project_status_policy_preserved"
                )
                is True
                and behavior_assertions.get("workflow_state_labels_are_not_adopted") is True,
                "github_contact_absent": no_github_contact_observed,
            }
        )
        observed_summary = (
            "draft-only tracer-bullet ticket recorded blocker, frontier, and one Harness closure"
            if scenario.scenario_kind == "positive" and not behavior_errors
            else "horizontal tickets and ready-for-agent were rejected without GitHub contact"
            if scenario.scenario_kind == "boundary" and not behavior_errors
            else "vertical ticket runtime result validation failed"
        )
        if runtime_blocker_category is not None:
            observed_summary = "Codex auth, account, or service state prevented runtime proof"
        result = {
            "fixture_id": f"synthetic-{scenario.scenario_id}",
            "captured_at": _captured_at(),
            "runtime_target": "codex",
            "component_id": GITHUB_ISSUE_COMPONENT_ID,
            "slice_id": "VC-05",
            "scenario_id": scenario.scenario_id,
            "scenario_kind": scenario.scenario_kind,
            "observed_codex_version": codex_version,
            **_model_evidence(model),
            "isolated_workspace_identity": _sha256_text(str(workspace.resolve())),
            "isolated_workspace_hash_before": _tree_hash(before),
            "isolated_workspace_hash_after": _tree_hash(after),
            "install_plan_hash": install_plan_hash,
            "installed_codex_artifact_path": VC05_INSTALLED_CODEX_ARTIFACT.as_posix(),
            "installed_codex_artifact_hash": installed_artifact_hash,
            "prompt_purpose": scenario.prompt_purpose,
            "prompt_hash": _sha256_text(scenario.prompt),
            "prompt_revision_id": scenario.prompt_revision_id,
            "expected_behavior": scenario.expected_behavior,
            "observed_behavior_summary": observed_summary,
            "loader_evidence": {
                "installed_skill_artifact_present": artifact_present,
                "installed_skill_artifact_hash_matches_adapter": install_assertions[
                    "install_project_scope_codex_artifact_hash_matches_adapter"
                ],
                "direct_skill_request_observed": assertions[
                    "runtime_installed_skill_directly_requested"
                ],
                "automatic_loader_claimed": False,
            },
            "vertical_ticket_proof": vertical_ticket_proof,
            "github_contact_observation": github_contact_observation,
            "before_after_tree": {
                "allowed_changed_paths": list(scenario.allowed_changed_paths),
                "actual_changed_paths": changed_paths,
                "unexpected_changed_paths": unexpected_paths,
            },
            "before_after_git": {
                "metadata_hash_before": _tree_hash(git_before),
                "metadata_hash_after": _tree_hash(git_after),
                "actual_changed_paths": git_changed_paths,
            },
            "before_after_isolation_root": {
                "manifest_hash_before": _tree_hash(isolation_before),
                "manifest_hash_after": _tree_hash(isolation_after),
                "controller_allowed_changed_paths": sorted(allowed_isolation_manifest_paths),
                **isolation_change_summary,
            },
            "observed_filesystem_changed_paths": changed_paths,
            "observed_git_metadata_changed_paths": git_changed_paths,
            "forbidden_runtime_processes": forbidden_runtime_processes,
            "expected_vs_actual_assertions": assertions,
            "privacy": {
                "synthetic_fixture_only": True,
                "external_mutation_credentials_removed": True,
                "raw_prompt_persisted": False,
                "raw_model_response_persisted": False,
                "raw_environment_persisted": False,
                "raw_event_values_persisted": False,
                "auth_copied": False,
                "runtime_system_path": RUNTIME_SYSTEM_PATH,
            },
            "process_group_cleanup": {
                "timed_out": runtime_outcome.timed_out,
                "term_sent": runtime_outcome.term_sent,
                "kill_sent": runtime_outcome.kill_sent,
                "process_reaped": runtime_outcome.process_reaped,
                "group_absent_after_cleanup": runtime_outcome.group_absent_after_cleanup,
                "process_observation_available": runtime_outcome.process_observation_available,
                "root_process_observed": runtime_outcome.root_process_observed,
                "observed_executables": list(runtime_outcome.observed_executables),
            },
            "stderr_observation": "empty" if not runtime.stderr.strip() else "non-empty-not-persisted",
            "runtime_blocker_observation": {
                "category": runtime_blocker_category,
                "stderr_persisted": False,
            },
            "verdict": (
                "BLOCKED"
                if runtime_blocker_category is not None
                else "PASS" if all(assertions.values()) else "NEEDS_WORK"
            ),
            "failure_category": runtime_blocker_category or _failure_category(assertions),
        }
    except Exception as exc:
        result = {
            "fixture_id": f"synthetic-{scenario.scenario_id}",
            "captured_at": _captured_at(),
            "runtime_target": "codex",
            "component_id": GITHUB_ISSUE_COMPONENT_ID,
            "slice_id": "VC-05",
            "scenario_id": scenario.scenario_id,
            "scenario_kind": scenario.scenario_kind,
            "observed_codex_version": codex_version,
            **_model_evidence(model),
            "install_plan_hash": install_plan_hash,
            "prompt_purpose": scenario.prompt_purpose,
            "prompt_hash": _sha256_text(scenario.prompt),
            "prompt_revision_id": scenario.prompt_revision_id,
            "expected_behavior": scenario.expected_behavior,
            "observed_behavior_summary": "vertical ticket scenario controller failed before a complete verdict",
            "expected_vs_actual_assertions": failure_assertions,
            "privacy": {
                "synthetic_fixture_only": True,
                "raw_prompt_persisted": False,
                "raw_model_response_persisted": False,
                "raw_event_values_persisted": False,
                "auth_copied": False,
            },
            "controller_error": type(exc).__name__,
            "verdict": "BLOCKED" if failure_category in {"auth", "runtime_tool"} else "NEEDS_WORK",
            "failure_category": failure_category,
        }
    finally:
        if auth_link.is_symlink() or auth_link.exists():
            auth_link.unlink()
        cleanup_auth_removed = not auth_link.exists()
        shutil.rmtree(scenario_root, ignore_errors=True)
        cleanup_workspace_removed = not scenario_root.exists()

    result["cleanup"] = {
        "auth_link_removed": cleanup_auth_removed,
        "isolated_workspace_removed": cleanup_workspace_removed,
        "process_group_absent": cleanup_process_group_absent,
    }
    result.setdefault("expected_vs_actual_assertions", {})[
        "cleanup_auth_link_removed"
    ] = cleanup_auth_removed
    result["expected_vs_actual_assertions"]["cleanup_workspace_removed"] = cleanup_workspace_removed
    result["expected_vs_actual_assertions"]["cleanup_process_group_absent"] = (
        cleanup_process_group_absent
    )
    if not cleanup_auth_removed or not cleanup_workspace_removed or not cleanup_process_group_absent:
        result["verdict"] = "NEEDS_WORK"
        result["failure_category"] = "cleanup"
    return result, sanitized_events


def _run_vc04_scenario(
    *,
    plan_path: Path,
    install_plan_hash: str,
    codex_version: str,
    model: str,
    timeout_seconds: int,
    scenario: ScenarioSpec,
    expected_adapter_artifact_hash: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not scenario.requires_installed_skill or scenario.scenario_kind not in {"positive", "boundary"}:
        raise ValueError("VC-04 runner requires one fixed installed-skill scenario")
    scenario_root = Path(tempfile.mkdtemp(prefix=f"harnesskit-{scenario.scenario_id}."))
    workspace = scenario_root / "workspace"
    home = scenario_root / "home"
    codex_home = home / ".codex"
    temp_dir = scenario_root / "tmp"
    transient = scenario_root / "transient"
    for path in (workspace, home, temp_dir, transient):
        path.mkdir(parents=True, exist_ok=True)

    auth_link = codex_home / "auth.json"
    auth_source = _real_auth_path()
    sanitized_events: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    cleanup_auth_removed = False
    cleanup_workspace_removed = False
    cleanup_process_group_absent = True
    failure_category = "controller"
    failure_assertions: dict[str, bool] = {"runtime_controller_completed": False}
    try:
        git_init = _run(["git", "init", "-q"], cwd=workspace, timeout_seconds=30)
        if git_init.returncode != 0:
            raise RuntimeError("synthetic VC-04 fixture git initialization failed")
        install = _apply_and_verify(plan_path, home)
        installed_artifact = home / VC04_INSTALLED_CODEX_ARTIFACT
        artifact_present = _is_regular_file_within(installed_artifact, home)
        installed_artifact_hash = _sha256_file(installed_artifact) if artifact_present else ""
        install_assertions = {
            "install_apply_passed": install["apply_passed"],
            "install_verify_passed": install["verify_passed"],
            "install_user_scope_codex_artifact_present": artifact_present,
            "install_user_scope_codex_artifact_hash_matches_adapter": (
                bool(installed_artifact_hash)
                and installed_artifact_hash == expected_adapter_artifact_hash
            ),
        }
        if not all(install_assertions.values()):
            failure_category = "install"
            failure_assertions = install_assertions
            raise RuntimeError("isolated VC-04 user-scope install gate failed")

        trace_path = workspace / VC04_TRACE_RELATIVE_PATH
        _write_vc04_fixture(
            workspace,
            scenario_kind=scenario.scenario_kind,
            trace_path=trace_path,
        )
        source_path = workspace / VC04_SOURCE_PATH
        fixture_observer_path = workspace / "fixture/__init__.py"
        fixture_observer_hash_before = _sha256_file(fixture_observer_path)
        fixture_observer_mode_before = stat.S_IMODE(fixture_observer_path.stat().st_mode)
        active_test_path = workspace / (
            VC04_REQUESTED_TEST_PATH
            if scenario.scenario_kind == "positive"
            else VC04_BOUNDARY_TEST_PATH
        )
        initial_source_hash = _sha256_file(source_path)
        initial_test_hash = _sha256_file(active_test_path) if active_test_path.is_file() else None
        initial_test_shape = _vc04_test_shape(active_test_path, scenario_kind=scenario.scenario_kind)
        positive_requested_test_absent_before_runtime = not active_test_path.exists()
        failure_assertions = {
            "fixture_trace_observer_ready": bool(fixture_observer_hash_before),
            "fixture_trace_observer_read_only": fixture_observer_mode_before == 0o444,
            "fixture_trace_absent_before_runtime": not trace_path.exists(),
        }
        if not all(failure_assertions.values()):
            raise RuntimeError("VC-04 fixture trace observer did not materialize cleanly")
        existing_before = _vc04_unittest(workspace, "fixture.tests.test_existing_behavior")
        before = _tree_manifest(workspace)
        isolation_before = _tree_manifest(scenario_root)
        git_before = _git_metadata_manifest(workspace)

        failure_category = "auth"
        failure_assertions = {"runtime_auth_source_available": auth_source.is_file()}
        if not auth_source.is_file():
            raise RuntimeError("Codex auth source is unavailable")
        runtime_env = _sanitized_runtime_env(
            os.environ.copy(), home=home, codex_home=codex_home, temp_dir=temp_dir
        )
        runtime_env["PYTHONDONTWRITEBYTECODE"] = "1"
        resolvable_forbidden_runtimes = sorted(
            runtime
            for runtime in FORBIDDEN_MODEL_RUNTIMES
            if shutil.which(runtime, path=runtime_env["PATH"]) is not None
        )
        if resolvable_forbidden_runtimes:
            failure_category = "privacy"
            failure_assertions = {"privacy_forbidden_runtimes_unresolvable": False}
            raise RuntimeError("sanitized PATH exposes a forbidden model runtime")
        failure_category = "runtime_tool"
        failure_assertions = {"runtime_native_codex_available": False}
        codex_executable = _resolve_codex_native_executable()
        failure_category = "runtime"
        failure_assertions = {"runtime_process_completed": False}
        last_message_path = transient / "last-message.txt"
        runtime_argv = _codex_argv(
            workspace=workspace,
            last_message=last_message_path,
            prompt=scenario.prompt,
            model=model,
            enable_search=scenario.enable_search,
            enable_multi_agent=False,
        )
        runtime_argv[0] = str(codex_executable)
        codex_home.mkdir(parents=True, exist_ok=True)
        auth_link.symlink_to(auth_source)
        try:
            runtime_outcome = _run_process_group(
                runtime_argv,
                cwd=workspace,
                env=runtime_env,
                timeout_seconds=timeout_seconds,
            )
        finally:
            if auth_link.is_symlink() or auth_link.exists():
                auth_link.unlink()
        cleanup_auth_removed = not auth_link.exists()
        cleanup_process_group_absent = runtime_outcome.group_absent_after_cleanup
        runtime = runtime_outcome.completed
        runtime_blocker_category = (
            _codex_runtime_blocker_category(runtime.stderr) if runtime.returncode != 0 else None
        )
        last_message = (
            last_message_path.read_text(encoding="utf-8")
            if last_message_path.is_file()
            else ""
        )
        sanitized_events = _sanitize_events(runtime.stdout, scenario_id=scenario.scenario_id)
        forbidden_commands = _forbidden_runtime_commands(runtime.stdout)
        forbidden_observed_processes = _observed_forbidden_processes(runtime_outcome)
        forbidden_runtime_processes = sorted(
            set(forbidden_commands) | set(forbidden_observed_processes)
        )
        validation_assertions, validation_errors = _validate_vc04_result(
            last_message, scenario_kind=scenario.scenario_kind
        )
        event_assertions, event_errors = _vc04_tdd_event_order(
            runtime.stdout, scenario_kind=scenario.scenario_kind
        )
        trace_records = _vc04_trace_records(trace_path)
        trace_assertions = _vc04_trace_assertions(
            runtime.stdout,
            trace_records,
            scenario_kind=scenario.scenario_kind,
            initial_source_hash=initial_source_hash,
            initial_test_hash=initial_test_hash,
        )
        required_trace_assertions = _vc04_required_trace_assertions(
            trace_assertions, scenario_kind=scenario.scenario_kind
        )
        active_test_module = (
            VC04_REQUESTED_TEST_MODULE
            if scenario.scenario_kind == "positive"
            else VC04_BOUNDARY_TEST_MODULE
        )
        active_test_result = _vc04_unittest(workspace, active_test_module)
        existing_after = _vc04_unittest(workspace, "fixture.tests.test_existing_behavior")
        full_suite_after = _vc04_unittest(
            workspace, "discover", "-s", "fixture/tests", "-p", "test_*.py"
        )
        fixture_observer_hash_after = (
            _sha256_file(fixture_observer_path)
            if _is_regular_file_within(fixture_observer_path, workspace)
            else ""
        )
        fixture_observer_mode_after = (
            stat.S_IMODE(fixture_observer_path.stat().st_mode)
            if _is_regular_file_within(fixture_observer_path, workspace)
            else None
        )
        final_source_hash = _sha256_file(source_path)
        final_test_hash = _sha256_file(active_test_path) if active_test_path.is_file() else None
        final_test_shape = _vc04_test_shape(active_test_path, scenario_kind=scenario.scenario_kind)
        after = _tree_manifest(workspace)
        isolation_after = _tree_manifest(scenario_root)
        git_after = _git_metadata_manifest(workspace)
        changed_paths = _tree_diff(before, after)
        isolation_changed_paths = _tree_diff(isolation_before, isolation_after)
        git_changed_paths = _tree_diff(git_before, git_after)
        allowed_workspace_manifest_paths = _vc04_allowed_workspace_manifest_paths(scenario)
        unexpected_paths = sorted(set(changed_paths) - allowed_workspace_manifest_paths)
        allowed_isolation_manifest_paths = {
            *(f"workspace/{path}" for path in allowed_workspace_manifest_paths),
            "transient/last-message.txt",
        }
        isolation_change_summary = _summarize_isolation_changes(
            isolation_changed_paths, controller_allowed_paths=allowed_isolation_manifest_paths
        )
        behavior_assertions = {
            **validation_assertions,
            **event_assertions,
            **required_trace_assertions,
            "controller_active_test_passed": active_test_result.returncode == 0,
            "controller_full_suite_passed": full_suite_after.returncode == 0,
            "original_public_behavior_preserved": (
                existing_before.returncode == 0 and existing_after.returncode == 0
            ),
            "public_literal_test_shape_valid": all(final_test_shape.values()),
            "positive_requested_test_absent_before_runtime": (
                positive_requested_test_absent_before_runtime
                if scenario.scenario_kind == "positive"
                else True
            ),
            "boundary_initial_test_is_rejected_shape": (
                not all(initial_test_shape.values()) if scenario.scenario_kind == "boundary" else True
            ),
            "boundary_source_hash_unchanged": (
                final_source_hash == initial_source_hash
                if scenario.scenario_kind == "boundary"
                else True
            ),
        }
        behavior_errors = [key for key, value in behavior_assertions.items() if not value]
        assertions: dict[str, bool] = {
            **install_assertions,
            "runtime_command_passed": runtime.returncode == 0,
            "runtime_not_timed_out": not runtime_outcome.timed_out,
            "runtime_final_message_is_json_object": _extract_json_object(last_message) is not None,
            "runtime_requested_model_explicit": (
                "--model" in runtime_argv
                and runtime_argv[runtime_argv.index("--model") + 1] == model
            ),
            "runtime_multi_agent_disabled": "multi_agent" not in runtime_argv,
            "runtime_user_scope_home_matches_codex_home": runtime_env["CODEX_HOME"]
            == str(codex_home),
            "runtime_fixture_trace_observer_unchanged": (
                bool(fixture_observer_hash_before)
                and fixture_observer_hash_after == fixture_observer_hash_before
                and fixture_observer_mode_after == fixture_observer_mode_before
            ),
            "runtime_fixture_trace_observer_read_only": fixture_observer_mode_before == 0o444,
            "runtime_installed_skill_directly_requested": VC04_RUNTIME_CODEX_SKILL_PATH
            in scenario.prompt,
            "behavior_contract_passed": not behavior_errors,
            "mutation_only_allowed_paths": not unexpected_paths,
            "mutation_isolated_roots_only_allowed_paths": not isolation_change_summary[
                "unexpected_changed_paths"
            ],
            "mutation_git_metadata_unchanged": not git_changed_paths,
            "privacy_no_non_codex_runtime_dispatch": not forbidden_runtime_processes,
            "privacy_forbidden_runtimes_unresolvable": not resolvable_forbidden_runtimes,
            "privacy_process_observation_available": runtime_outcome.process_observation_available,
            "privacy_root_process_observed": runtime_outcome.root_process_observed,
            "privacy_auth_link_removed_after_runtime": cleanup_auth_removed,
            "cleanup_process_group_absent": cleanup_process_group_absent,
            "cleanup_no_unexpected_process_group_termination": not (
                not runtime_outcome.timed_out
                and (runtime_outcome.term_sent or runtime_outcome.kill_sent)
            ),
        }
        assertions.update({f"behavior_{key}": value for key, value in behavior_assertions.items()})
        tdd_proof = (
            {
                "red_green_refactor_verified": all(event_assertions.values())
                and all(trace_assertions.values()),
                "public_literal_test_verified": all(final_test_shape.values())
                and active_test_result.returncode == 0
                and full_suite_after.returncode == 0,
                "original_public_behavior_preserved": existing_before.returncode == 0
                and existing_after.returncode == 0,
            }
            if scenario.scenario_kind == "positive"
            else {
                "boundary_mock_rejected_and_corrected": not validation_errors
                and all(final_test_shape.values())
                and active_test_result.returncode == 0,
                "source_unchanged": final_source_hash == initial_source_hash,
            }
        )
        observed_summary = (
            "public literal TDD proof recorded from RED through GREEN and refactor safety"
            if scenario.scenario_kind == "positive" and not behavior_errors
            else "internal-business mock and implementation-derived expected value were corrected"
            if scenario.scenario_kind == "boundary" and not behavior_errors
            else "TDD runtime result validation failed"
        )
        if runtime_blocker_category is not None:
            observed_summary = "Codex auth, account, or service state prevented runtime proof"
        result = {
            "fixture_id": f"synthetic-{scenario.scenario_id}",
            "captured_at": _captured_at(),
            "runtime_target": "codex",
            "component_id": TDD_COMPONENT_ID,
            "slice_id": "VC-04",
            "scenario_id": scenario.scenario_id,
            "scenario_kind": scenario.scenario_kind,
            "observed_codex_version": codex_version,
            **_model_evidence(model),
            "isolated_workspace_identity": _sha256_text(str(workspace.resolve())),
            "isolated_workspace_hash_before": _tree_hash(before),
            "isolated_workspace_hash_after": _tree_hash(after),
            "install_plan_hash": install_plan_hash,
            "installed_codex_artifact_path": VC04_INSTALLED_CODEX_ARTIFACT.as_posix(),
            "installed_codex_artifact_hash": installed_artifact_hash,
            "prompt_purpose": scenario.prompt_purpose,
            "prompt_hash": _sha256_text(scenario.prompt),
            "prompt_revision_id": scenario.prompt_revision_id,
            "expected_behavior": scenario.expected_behavior,
            "observed_behavior_summary": observed_summary,
            "loader_evidence": {
                "installed_skill_artifact_present": artifact_present,
                "installed_skill_artifact_hash_matches_adapter": install_assertions[
                    "install_user_scope_codex_artifact_hash_matches_adapter"
                ],
                "direct_skill_request_observed": assertions[
                    "runtime_installed_skill_directly_requested"
                ],
                "automatic_loader_claimed": False,
            },
            "tdd_proof": tdd_proof,
            "tdd_trace_observation": {
                "record_count": len(trace_records),
                "raw_trace_persisted": False,
                "assertions": trace_assertions,
            },
            "before_after_tree": {
                "allowed_changed_paths": list(scenario.allowed_changed_paths),
                "actual_changed_paths": changed_paths,
                "unexpected_changed_paths": unexpected_paths,
            },
            "before_after_git": {
                "metadata_hash_before": _tree_hash(git_before),
                "metadata_hash_after": _tree_hash(git_after),
                "actual_changed_paths": git_changed_paths,
            },
            "before_after_isolation_root": {
                "manifest_hash_before": _tree_hash(isolation_before),
                "manifest_hash_after": _tree_hash(isolation_after),
                "controller_allowed_changed_paths": sorted(allowed_isolation_manifest_paths),
                **isolation_change_summary,
            },
            "observed_filesystem_changed_paths": changed_paths,
            "observed_git_metadata_changed_paths": git_changed_paths,
            "forbidden_runtime_processes": forbidden_runtime_processes,
            "expected_vs_actual_assertions": assertions,
            "privacy": {
                "synthetic_fixture_only": True,
                "external_mutation_credentials_removed": True,
                "raw_prompt_persisted": False,
                "raw_model_response_persisted": False,
                "raw_environment_persisted": False,
                "raw_trace_persisted": False,
                "auth_copied": False,
                "runtime_system_path": RUNTIME_SYSTEM_PATH,
            },
            "process_group_cleanup": {
                "timed_out": runtime_outcome.timed_out,
                "term_sent": runtime_outcome.term_sent,
                "kill_sent": runtime_outcome.kill_sent,
                "process_reaped": runtime_outcome.process_reaped,
                "group_absent_after_cleanup": runtime_outcome.group_absent_after_cleanup,
                "process_observation_available": runtime_outcome.process_observation_available,
                "root_process_observed": runtime_outcome.root_process_observed,
                "observed_executables": list(runtime_outcome.observed_executables),
            },
            "stderr_observation": "empty" if not runtime.stderr.strip() else "non-empty-not-persisted",
            "runtime_blocker_observation": {
                "category": runtime_blocker_category,
                "stderr_persisted": False,
            },
            "verdict": (
                "BLOCKED"
                if runtime_blocker_category is not None
                else "PASS" if all(assertions.values()) else "NEEDS_WORK"
            ),
            "failure_category": runtime_blocker_category or _failure_category(assertions),
        }
    except Exception as exc:
        result = {
            "fixture_id": f"synthetic-{scenario.scenario_id}",
            "captured_at": _captured_at(),
            "runtime_target": "codex",
            "component_id": TDD_COMPONENT_ID,
            "slice_id": "VC-04",
            "scenario_id": scenario.scenario_id,
            "scenario_kind": scenario.scenario_kind,
            "observed_codex_version": codex_version,
            **_model_evidence(model),
            "install_plan_hash": install_plan_hash,
            "prompt_purpose": scenario.prompt_purpose,
            "prompt_hash": _sha256_text(scenario.prompt),
            "prompt_revision_id": scenario.prompt_revision_id,
            "expected_behavior": scenario.expected_behavior,
            "observed_behavior_summary": "TDD scenario controller failed before a complete verdict",
            "expected_vs_actual_assertions": failure_assertions,
            "privacy": {
                "synthetic_fixture_only": True,
                "raw_prompt_persisted": False,
                "raw_model_response_persisted": False,
                "raw_trace_persisted": False,
                "auth_copied": False,
            },
            "controller_error": type(exc).__name__,
            "verdict": "BLOCKED" if failure_category in {"auth", "runtime_tool"} else "NEEDS_WORK",
            "failure_category": failure_category,
        }
    finally:
        if auth_link.is_symlink() or auth_link.exists():
            auth_link.unlink()
        cleanup_auth_removed = not auth_link.exists()
        shutil.rmtree(scenario_root, ignore_errors=True)
        cleanup_workspace_removed = not scenario_root.exists()

    result["cleanup"] = {
        "auth_link_removed": cleanup_auth_removed,
        "isolated_workspace_removed": cleanup_workspace_removed,
        "process_group_absent": cleanup_process_group_absent,
    }
    result.setdefault("expected_vs_actual_assertions", {})[
        "cleanup_auth_link_removed"
    ] = cleanup_auth_removed
    result["expected_vs_actual_assertions"]["cleanup_workspace_removed"] = cleanup_workspace_removed
    result["expected_vs_actual_assertions"]["cleanup_process_group_absent"] = (
        cleanup_process_group_absent
    )
    if not cleanup_auth_removed or not cleanup_workspace_removed or not cleanup_process_group_absent:
        result["verdict"] = "NEEDS_WORK"
        result["failure_category"] = "cleanup"
    return result, sanitized_events



def _run_vc04_slice(
    *,
    model: str,
    timeout_seconds: int,
    evidence_dir: Path | None,
    retry_failed_from: Path | None,
    revalidate_after_adapter_change_from: Path | None,
) -> tuple[dict[str, Any], Path]:
    if retry_failed_from is not None and revalidate_after_adapter_change_from is not None:
        raise ValueError("retry and artifact revalidation are mutually exclusive")
    scenarios = _vc04_scenario_specs()
    output_dir = evidence_dir or (EVIDENCE_ROOT / "VC-04" / _timestamp())
    if output_dir.exists():
        raise FileExistsError(f"evidence directory already exists: {output_dir}")
    run_root = Path(tempfile.mkdtemp(prefix="harnesskit-vc-04-controller."))
    predecessor_result = revalidate_after_adapter_change_from or retry_failed_from
    attempt_kind = (
        "artifact-revalidation"
        if revalidate_after_adapter_change_from is not None
        else "failed-only-rerun" if retry_failed_from is not None else "initial"
    )
    selected_scenario_ids = [scenario.scenario_id for scenario in scenarios]
    install_plan_hash = "unavailable"
    adapter_artifact_hash = "unavailable"
    codex_version = "unavailable"
    static_gate: dict[str, bool] = {"preflight_completed": False}
    attempt_plan: AttemptPlan | None = None
    attempt_results: list[dict[str, Any]] = []
    sanitized_events: list[dict[str, Any]] = []

    def effective_scenarios() -> list[dict[str, Any]]:
        carried = list(attempt_plan.carried_forward_results) if attempt_plan else []
        records = {
            item["scenario_id"]: item
            for item in [*carried, *attempt_results]
            if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
        }
        return [records[scenario.scenario_id] for scenario in scenarios if scenario.scenario_id in records]

    def checkpoint(active_scenario_id: str | None) -> None:
        _write_checkpoint(
            output_dir,
            slice_id="VC-04",
            component_id=TDD_COMPONENT_ID,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            active_scenario_id=active_scenario_id,
            completed_scenarios=attempt_results,
            sanitized_events=sanitized_events,
        )

    def terminalize(
        *, verdict: str, failure_category: str, phase: str, blocker_type: str
    ) -> tuple[dict[str, Any], Path]:
        payload = _write_evidence(
            output_dir,
            slice_id="VC-04",
            component_id=TDD_COMPONENT_ID,
            codex_version=codex_version,
            model=model,
            install_plan_hash=install_plan_hash,
            adapter_artifact_hash=adapter_artifact_hash,
            static_gate=static_gate,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            predecessor_result_path=(
                attempt_plan.predecessor_result_path if attempt_plan else None
            ),
            predecessor_result_hash=(
                attempt_plan.predecessor_result_hash if attempt_plan else None
            ),
            attempt_scenarios=attempt_results,
            scenarios=effective_scenarios(),
            sanitized_events=sanitized_events,
            forced_verdict=verdict,
            failure_category_override=failure_category,
            blocker={"phase": phase, "type": blocker_type, "message_persisted": False},
            artifact_revalidation=(attempt_plan.artifact_revalidation if attempt_plan else None),
            scenario_completion_assertions=_vc04_completion_assertions(effective_scenarios()),
        )
        return payload, output_dir

    try:
        checkpoint(active_scenario_id=None)
        try:
            plan_path, install_plan_hash, adapter_artifact_hash, static_gate = _vc04_static_gate(
                run_root
            )
        except (RuntimeError, OSError, json.JSONDecodeError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="static_or_install",
                phase="static_and_install_preflight",
                blocker_type=type(exc).__name__,
            )
        try:
            codex_version = _codex_version()
        except (RuntimeError, OSError) as exc:
            return terminalize(
                verdict="BLOCKED",
                failure_category="runtime_tool",
                phase="codex_native_tool_preflight",
                blocker_type=type(exc).__name__,
            )
        try:
            previous_payload = (
                json.loads(predecessor_result.read_text(encoding="utf-8"))
                if predecessor_result is not None
                else None
            )
            if previous_payload is not None and not isinstance(previous_payload, dict):
                raise ValueError("predecessor result must be a JSON object")
            attempt_plan = (
                _plan_vc04_artifact_revalidation(
                    scenarios,
                    previous_payload=previous_payload,
                    predecessor_result=predecessor_result,
                    current_install_plan_hash=install_plan_hash,
                    current_codex_version=codex_version,
                    requested_model=model,
                    current_adapter_artifact_hash=adapter_artifact_hash,
                )
                if revalidate_after_adapter_change_from is not None
                else _plan_vc04_attempt(
                    scenarios,
                    previous_payload=previous_payload,
                    predecessor_result=predecessor_result,
                    current_install_plan_hash=install_plan_hash,
                    current_codex_version=codex_version,
                    requested_model=model,
                    current_adapter_artifact_hash=adapter_artifact_hash,
                )
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="controller",
                phase="attempt_planning",
                blocker_type=type(exc).__name__,
            )
        selected_scenario_ids = [scenario.scenario_id for scenario in attempt_plan.selected_scenarios]
        checkpoint(active_scenario_id=None)
        if not _real_auth_path().is_file():
            return terminalize(
                verdict="BLOCKED",
                failure_category="auth",
                phase="codex_auth_preflight",
                blocker_type="AuthFileUnavailable",
            )
        for scenario in attempt_plan.selected_scenarios:
            checkpoint(active_scenario_id=scenario.scenario_id)
            result, events = _run_vc04_scenario(
                plan_path=plan_path,
                install_plan_hash=install_plan_hash,
                codex_version=codex_version,
                model=model,
                timeout_seconds=timeout_seconds,
                scenario=scenario,
                expected_adapter_artifact_hash=adapter_artifact_hash,
            )
            prompt_revision_change = next(
                (
                    item
                    for item in attempt_plan.prompt_revision_changes
                    if item["scenario_id"] == scenario.scenario_id
                ),
                None,
            )
            if prompt_revision_change is not None:
                result["prompt_revision_change"] = prompt_revision_change
            attempt_results.append(result)
            sanitized_events.extend(events)
            checkpoint(active_scenario_id=None)
            if result.get("verdict") == "BLOCKED":
                return _write_evidence(
                    output_dir,
                    slice_id="VC-04",
                    component_id=TDD_COMPONENT_ID,
                    codex_version=codex_version,
                    model=model,
                    install_plan_hash=install_plan_hash,
                    adapter_artifact_hash=adapter_artifact_hash,
                    static_gate=static_gate,
                    attempt_kind=attempt_kind,
                    selected_scenario_ids=selected_scenario_ids,
                    predecessor_result_path=attempt_plan.predecessor_result_path,
                    predecessor_result_hash=attempt_plan.predecessor_result_hash,
                    attempt_scenarios=attempt_results,
                    scenarios=effective_scenarios(),
                    sanitized_events=sanitized_events,
                    artifact_revalidation=attempt_plan.artifact_revalidation,
                    scenario_completion_assertions=_vc04_completion_assertions(
                        effective_scenarios()
                    ),
                ), output_dir
        return _write_evidence(
            output_dir,
            slice_id="VC-04",
            component_id=TDD_COMPONENT_ID,
            codex_version=codex_version,
            model=model,
            install_plan_hash=install_plan_hash,
            adapter_artifact_hash=adapter_artifact_hash,
            static_gate=static_gate,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            predecessor_result_path=attempt_plan.predecessor_result_path,
            predecessor_result_hash=attempt_plan.predecessor_result_hash,
            attempt_scenarios=attempt_results,
            scenarios=effective_scenarios(),
            sanitized_events=sanitized_events,
            artifact_revalidation=attempt_plan.artifact_revalidation,
            scenario_completion_assertions=_vc04_completion_assertions(effective_scenarios()),
        ), output_dir
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def _run_vc05_slice(
    *,
    model: str,
    timeout_seconds: int,
    evidence_dir: Path | None,
    retry_failed_from: Path | None,
    revalidate_after_adapter_change_from: Path | None,
) -> tuple[dict[str, Any], Path]:
    if retry_failed_from is not None and revalidate_after_adapter_change_from is not None:
        raise ValueError("retry and artifact revalidation are mutually exclusive")
    scenarios = _vc05_scenario_specs()
    output_dir = evidence_dir or (EVIDENCE_ROOT / "VC-05" / _timestamp())
    if output_dir.exists():
        raise FileExistsError(f"evidence directory already exists: {output_dir}")
    run_root = Path(tempfile.mkdtemp(prefix="harnesskit-vc-05-controller."))
    predecessor_result = revalidate_after_adapter_change_from or retry_failed_from
    attempt_kind = (
        "artifact-revalidation"
        if revalidate_after_adapter_change_from is not None
        else "failed-only-rerun" if retry_failed_from is not None else "initial"
    )
    selected_scenario_ids = [scenario.scenario_id for scenario in scenarios]
    install_plan_hash = "unavailable"
    adapter_artifact_hash = "unavailable"
    codex_version = "unavailable"
    static_gate: dict[str, bool] = {"preflight_completed": False}
    attempt_plan: AttemptPlan | None = None
    attempt_results: list[dict[str, Any]] = []
    sanitized_events: list[dict[str, Any]] = []

    def effective_scenarios() -> list[dict[str, Any]]:
        carried = list(attempt_plan.carried_forward_results) if attempt_plan else []
        records = {
            item["scenario_id"]: item
            for item in [*carried, *attempt_results]
            if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
        }
        return [records[scenario.scenario_id] for scenario in scenarios if scenario.scenario_id in records]

    def checkpoint(active_scenario_id: str | None) -> None:
        _write_checkpoint(
            output_dir,
            slice_id="VC-05",
            component_id=GITHUB_ISSUE_COMPONENT_ID,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            active_scenario_id=active_scenario_id,
            completed_scenarios=attempt_results,
            sanitized_events=sanitized_events,
        )

    def terminalize(
        *, verdict: str, failure_category: str, phase: str, blocker_type: str
    ) -> tuple[dict[str, Any], Path]:
        payload = _write_evidence(
            output_dir,
            slice_id="VC-05",
            component_id=GITHUB_ISSUE_COMPONENT_ID,
            codex_version=codex_version,
            model=model,
            install_plan_hash=install_plan_hash,
            adapter_artifact_hash=adapter_artifact_hash,
            static_gate=static_gate,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            predecessor_result_path=(
                attempt_plan.predecessor_result_path if attempt_plan else None
            ),
            predecessor_result_hash=(
                attempt_plan.predecessor_result_hash if attempt_plan else None
            ),
            attempt_scenarios=attempt_results,
            scenarios=effective_scenarios(),
            sanitized_events=sanitized_events,
            forced_verdict=verdict,
            failure_category_override=failure_category,
            blocker={"phase": phase, "type": blocker_type, "message_persisted": False},
            artifact_revalidation=(attempt_plan.artifact_revalidation if attempt_plan else None),
            scenario_completion_assertions=_vc05_completion_assertions(effective_scenarios()),
        )
        return payload, output_dir

    try:
        checkpoint(active_scenario_id=None)
        try:
            plan_path, install_plan_hash, adapter_artifact_hash, static_gate = _vc05_static_gate(
                run_root
            )
        except (RuntimeError, OSError, json.JSONDecodeError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="static_or_install",
                phase="static_and_install_preflight",
                blocker_type=type(exc).__name__,
            )
        try:
            codex_version = _codex_version()
        except (RuntimeError, OSError) as exc:
            return terminalize(
                verdict="BLOCKED",
                failure_category="runtime_tool",
                phase="codex_native_tool_preflight",
                blocker_type=type(exc).__name__,
            )
        try:
            previous_payload = (
                json.loads(predecessor_result.read_text(encoding="utf-8"))
                if predecessor_result is not None
                else None
            )
            if previous_payload is not None and not isinstance(previous_payload, dict):
                raise ValueError("predecessor result must be a JSON object")
            attempt_plan = (
                _plan_vc05_artifact_revalidation(
                    scenarios,
                    previous_payload=previous_payload,
                    predecessor_result=predecessor_result,
                    current_install_plan_hash=install_plan_hash,
                    current_codex_version=codex_version,
                    requested_model=model,
                    current_adapter_artifact_hash=adapter_artifact_hash,
                )
                if revalidate_after_adapter_change_from is not None
                else _plan_vc05_attempt(
                    scenarios,
                    previous_payload=previous_payload,
                    predecessor_result=predecessor_result,
                    current_install_plan_hash=install_plan_hash,
                    current_codex_version=codex_version,
                    requested_model=model,
                    current_adapter_artifact_hash=adapter_artifact_hash,
                )
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="controller",
                phase="attempt_planning",
                blocker_type=type(exc).__name__,
            )
        selected_scenario_ids = [scenario.scenario_id for scenario in attempt_plan.selected_scenarios]
        checkpoint(active_scenario_id=None)
        if not _real_auth_path().is_file():
            return terminalize(
                verdict="BLOCKED",
                failure_category="auth",
                phase="codex_auth_preflight",
                blocker_type="AuthFileUnavailable",
            )
        for scenario in attempt_plan.selected_scenarios:
            checkpoint(active_scenario_id=scenario.scenario_id)
            result, events = _run_vc05_scenario(
                plan_path=plan_path,
                install_plan_hash=install_plan_hash,
                codex_version=codex_version,
                model=model,
                timeout_seconds=timeout_seconds,
                scenario=scenario,
                expected_adapter_artifact_hash=adapter_artifact_hash,
            )
            prompt_revision_change = next(
                (
                    item
                    for item in attempt_plan.prompt_revision_changes
                    if item["scenario_id"] == scenario.scenario_id
                ),
                None,
            )
            if prompt_revision_change is not None:
                result["prompt_revision_change"] = prompt_revision_change
            attempt_results.append(result)
            sanitized_events.extend(events)
            checkpoint(active_scenario_id=None)
            if result.get("verdict") == "BLOCKED":
                return _write_evidence(
                    output_dir,
                    slice_id="VC-05",
                    component_id=GITHUB_ISSUE_COMPONENT_ID,
                    codex_version=codex_version,
                    model=model,
                    install_plan_hash=install_plan_hash,
                    adapter_artifact_hash=adapter_artifact_hash,
                    static_gate=static_gate,
                    attempt_kind=attempt_kind,
                    selected_scenario_ids=selected_scenario_ids,
                    predecessor_result_path=attempt_plan.predecessor_result_path,
                    predecessor_result_hash=attempt_plan.predecessor_result_hash,
                    attempt_scenarios=attempt_results,
                    scenarios=effective_scenarios(),
                    sanitized_events=sanitized_events,
                    artifact_revalidation=attempt_plan.artifact_revalidation,
                    scenario_completion_assertions=_vc05_completion_assertions(
                        effective_scenarios()
                    ),
                ), output_dir
        return _write_evidence(
            output_dir,
            slice_id="VC-05",
            component_id=GITHUB_ISSUE_COMPONENT_ID,
            codex_version=codex_version,
            model=model,
            install_plan_hash=install_plan_hash,
            adapter_artifact_hash=adapter_artifact_hash,
            static_gate=static_gate,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            predecessor_result_path=attempt_plan.predecessor_result_path,
            predecessor_result_hash=attempt_plan.predecessor_result_hash,
            attempt_scenarios=attempt_results,
            scenarios=effective_scenarios(),
            sanitized_events=sanitized_events,
            artifact_revalidation=attempt_plan.artifact_revalidation,
            scenario_completion_assertions=_vc05_completion_assertions(effective_scenarios()),
        ), output_dir
    finally:
        shutil.rmtree(run_root, ignore_errors=True)








































def _current_vc07_adapter_artifact_hash() -> str:
    if not _is_regular_file_within(VC07_DIST_CODEX_ARTIFACT, REPO_ROOT):
        raise RuntimeError("built Codex root-cause-debugging adapter artifact is unavailable")
    return _sha256_file(VC07_DIST_CODEX_ARTIFACT)


def _vc07_static_gate(run_root: Path) -> tuple[Path, str, str, dict[str, bool]]:
    results: dict[str, bool] = {}
    for name, argv in (
        (
            "canonical_validation",
            [
                sys.executable,
                "scripts/components/validate.py",
                "--component",
                ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
            ],
        ),
        (
            "declared_target_build",
            [
                sys.executable,
                "scripts/adapters/build.py",
                "--component",
                ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
            ],
        ),
        (
            "declared_target_check",
            [
                sys.executable,
                "scripts/adapters/build.py",
                "--component",
                ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
                "--check",
            ],
        ),
    ):
        command = _run(argv, cwd=REPO_ROOT, timeout_seconds=180)
        results[name] = command.returncode == 0
        if command.returncode != 0:
            raise RuntimeError(f"VC-07 static gate failed: {name}")
    plan_result = _run(
        [
            sys.executable,
            "scripts/install/plan.py",
            "--component",
            ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
            "--scope",
            "project",
            "--mode",
            "apply",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        timeout_seconds=120,
    )
    results["standalone_project_apply_plan"] = plan_result.returncode == 0
    if plan_result.returncode != 0:
        raise RuntimeError("VC-07 static gate failed: standalone_project_apply_plan")
    plan = json.loads(plan_result.stdout)
    codex_artifacts = [
        artifact
        for artifact in plan.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("target") == "codex"
    ]
    results["project_scope_codex_artifact_route"] = (
        plan.get("mode") == "apply"
        and plan.get("scope") == "project"
        and plan.get("components") == [ROOT_CAUSE_DEBUGGING_COMPONENT_ID]
        and codex_artifacts
        == [
            {
                "component_id": ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
                "component_ids": [ROOT_CAUSE_DEBUGGING_COMPONENT_ID],
                "target": "codex",
                "source": VC07_ADAPTER_OUTPUT,
                "destination": VC07_INSTALLED_CODEX_ARTIFACT.as_posix(),
            }
        ]
    )
    if not results["project_scope_codex_artifact_route"]:
        raise RuntimeError("VC-07 standalone project plan Codex route drift")
    plan_path = run_root / "root-cause-debugging.install-plan.apply.json"
    plan_path.write_text(plan_result.stdout, encoding="utf-8")
    return (
        plan_path,
        _sha256_text(plan_result.stdout),
        _current_vc07_adapter_artifact_hash(),
        results,
    )


def _vc07_pass_is_transferable(item: dict[str, Any], spec: ScenarioSpec) -> bool:
    if item.get("component_id") != ROOT_CAUSE_DEBUGGING_COMPONENT_ID:
        return False
    proof = item.get("root_cause_proof")
    if not isinstance(proof, dict):
        return False
    required = (
        (
            "correct_seam_reproduced",
            "root_cause_regression_fixed",
            "original_repro_rechecked",
            "tagged_instrumentation_removed",
            "architecture_handoff_deferred",
            "issue_mutation_absent",
        )
        if spec.scenario_kind == "positive"
        else (
            "insufficient_evidence_guess_patch_refused",
            "issue_mutation_refused",
            "no_workspace_mutation",
            "instrumentation_not_started",
        )
    )
    return all(proof.get(key) is True for key in required)


def _plan_vc07_attempt(
    specs: tuple[ScenarioSpec, ...],
    *,
    previous_payload: dict[str, Any] | None,
    predecessor_result: Path | None,
    current_install_plan_hash: str,
    current_codex_version: str,
    requested_model: str,
    current_adapter_artifact_hash: str,
) -> AttemptPlan:
    if previous_payload is None:
        if predecessor_result is not None:
            raise ValueError("predecessor result provided without payload")
        return AttemptPlan(specs, (), None, None, (), None)
    if predecessor_result is None or not predecessor_result.is_file():
        raise ValueError("failed-only retry requires an existing predecessor result")
    expected_top_level = {
        "slice_id": "VC-07",
        "runtime_target": "codex",
        "component_id": ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
        "observed_codex_version": current_codex_version,
        "requested_model": requested_model,
        "install_plan_hash": current_install_plan_hash,
        "adapter_artifact_hash": current_adapter_artifact_hash,
    }
    for key, expected in expected_top_level.items():
        if previous_payload.get(key) != expected:
            raise ValueError(f"{key} drift")
    raw_results = previous_payload.get("scenario_results")
    if not isinstance(raw_results, list):
        raise ValueError("predecessor scenario results are unavailable")
    by_id: dict[str, dict[str, Any]] = {}
    for item in raw_results:
        if not isinstance(item, dict) or not isinstance(item.get("scenario_id"), str):
            raise ValueError("invalid predecessor scenario result")
        scenario_id = item["scenario_id"]
        if scenario_id in by_id:
            raise ValueError("duplicate predecessor scenario id")
        by_id[scenario_id] = item
    if set(by_id) != {spec.scenario_id for spec in specs}:
        raise ValueError("predecessor fixed scenario set drift")
    selected: list[ScenarioSpec] = []
    carried: list[dict[str, Any]] = []
    prompt_revision_changes: list[dict[str, str]] = []
    for spec in specs:
        item = by_id[spec.scenario_id]
        current_prompt_hash = _sha256_text(spec.prompt)
        predecessor_prompt_hash = item.get("prompt_hash")
        if predecessor_prompt_hash != current_prompt_hash:
            predecessor_revision_id = item.get("prompt_revision_id", "legacy-unversioned")
            if (
                item.get("verdict") == "PASS"
                or not isinstance(predecessor_revision_id, str)
                or not predecessor_revision_id
                or predecessor_revision_id == spec.prompt_revision_id
            ):
                raise ValueError("predecessor prompt drift")
            prompt_revision_changes.append(
                {
                    "scenario_id": spec.scenario_id,
                    "predecessor_prompt_hash": str(predecessor_prompt_hash),
                    "predecessor_prompt_revision_id": predecessor_revision_id,
                    "current_prompt_hash": current_prompt_hash,
                    "current_prompt_revision_id": spec.prompt_revision_id,
                }
            )
        if item.get("verdict") == "PASS":
            if not _vc07_pass_is_transferable(item, spec):
                raise ValueError("predecessor pass is not transferable")
            carried.append(item)
        else:
            selected.append(spec)
    if not selected:
        raise ValueError("predecessor has no failed fixed scenario")
    return AttemptPlan(
        selected_scenarios=tuple(selected),
        carried_forward_results=tuple(carried),
        predecessor_result_path=str(predecessor_result),
        predecessor_result_hash=_sha256_file(predecessor_result),
        prompt_revision_changes=tuple(prompt_revision_changes),
        artifact_revalidation=None,
    )


def _plan_vc07_artifact_revalidation(
    specs: tuple[ScenarioSpec, ...],
    *,
    previous_payload: dict[str, Any] | None,
    predecessor_result: Path | None,
    current_install_plan_hash: str,
    current_codex_version: str,
    requested_model: str,
    current_adapter_artifact_hash: str,
) -> AttemptPlan:
    if previous_payload is None or predecessor_result is None or not predecessor_result.is_file():
        raise ValueError("artifact revalidation requires an existing predecessor result")
    expected_top_level = {
        "slice_id": "VC-07",
        "runtime_target": "codex",
        "component_id": ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
        "observed_codex_version": current_codex_version,
        "requested_model": requested_model,
    }
    for key, expected in expected_top_level.items():
        if previous_payload.get(key) != expected:
            raise ValueError(f"{key} drift")
    predecessor_adapter_hash = previous_payload.get("adapter_artifact_hash")
    predecessor_install_plan_hash = previous_payload.get("install_plan_hash")
    if not isinstance(predecessor_adapter_hash, str) or not predecessor_adapter_hash:
        raise ValueError("predecessor adapter artifact hash is unavailable")
    if not isinstance(predecessor_install_plan_hash, str) or not predecessor_install_plan_hash:
        raise ValueError("predecessor install plan hash is unavailable")
    if predecessor_adapter_hash == current_adapter_artifact_hash:
        raise ValueError("artifact revalidation requires adapter artifact drift")
    raw_results = previous_payload.get("scenario_results")
    if not isinstance(raw_results, list):
        raise ValueError("predecessor scenario results are unavailable")
    previous_ids = [
        item.get("scenario_id")
        for item in raw_results
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    ]
    expected_ids = [spec.scenario_id for spec in specs]
    if (
        len(previous_ids) != len(raw_results)
        or len(set(previous_ids)) != len(previous_ids)
        or set(previous_ids) != set(expected_ids)
    ):
        raise ValueError("predecessor fixed scenario set drift")
    return AttemptPlan(
        selected_scenarios=specs,
        carried_forward_results=(),
        predecessor_result_path=str(predecessor_result),
        predecessor_result_hash=_sha256_file(predecessor_result),
        prompt_revision_changes=(),
        artifact_revalidation={
            "predecessor_adapter_artifact_hash": predecessor_adapter_hash,
            "current_adapter_artifact_hash": current_adapter_artifact_hash,
            "predecessor_install_plan_hash": predecessor_install_plan_hash,
            "current_install_plan_hash": current_install_plan_hash,
        },
    )


def _vc07_completion_assertions(scenarios: list[dict[str, Any]]) -> dict[str, bool]:
    expected_ids = ["vc07-root-cause-positive", "vc07-root-cause-boundary"]
    by_id = {
        item.get("scenario_id"): item
        for item in scenarios
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    }
    positive = by_id.get("vc07-root-cause-positive")
    boundary = by_id.get("vc07-root-cause-boundary")
    positive_proof = positive.get("root_cause_proof") if isinstance(positive, dict) else None
    boundary_proof = boundary.get("root_cause_proof") if isinstance(boundary, dict) else None
    return {
        "vc07_exact_scenario_set": [item.get("scenario_id") for item in scenarios] == expected_ids,
        "vc07_positive_root_cause_slice_passed": isinstance(positive, dict)
        and positive.get("verdict") == "PASS"
        and isinstance(positive_proof, dict)
        and all(
            positive_proof.get(key) is True
            for key in (
                "correct_seam_reproduced",
                "root_cause_regression_fixed",
                "original_repro_rechecked",
                "tagged_instrumentation_removed",
                "architecture_handoff_deferred",
                "issue_mutation_absent",
            )
        ),
        "vc07_boundary_insufficient_evidence_passed": isinstance(boundary, dict)
        and boundary.get("verdict") == "PASS"
        and isinstance(boundary_proof, dict)
        and all(
            boundary_proof.get(key) is True
            for key in (
                "insufficient_evidence_guess_patch_refused",
                "issue_mutation_refused",
                "no_workspace_mutation",
                "instrumentation_not_started",
            )
        ),
    }


def _write_vc07_evidence(
    output_dir: Path,
    *,
    codex_version: str,
    model: str,
    install_plan_hash: str,
    adapter_artifact_hash: str,
    static_gate: dict[str, bool],
    attempt_scenarios: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    sanitized_events: list[dict[str, Any]],
    attempt_kind: str = "initial",
    selected_scenario_ids: list[str] | None = None,
    predecessor_result_path: str | None = None,
    predecessor_result_hash: str | None = None,
    forced_verdict: str | None = None,
    failure_category_override: str | None = None,
    blocker: dict[str, Any] | None = None,
    artifact_revalidation: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = _write_evidence(
        output_dir,
        slice_id="VC-07",
        component_id=ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
        codex_version=codex_version,
        model=model,
        install_plan_hash=install_plan_hash,
        adapter_artifact_hash=adapter_artifact_hash,
        static_gate=static_gate,
        attempt_kind=attempt_kind,
        selected_scenario_ids=(
            selected_scenario_ids
            if selected_scenario_ids is not None
            else [scenario.scenario_id for scenario in _vc07_scenario_specs()]
        ),
        predecessor_result_path=predecessor_result_path,
        predecessor_result_hash=predecessor_result_hash,
        attempt_scenarios=attempt_scenarios,
        scenarios=scenarios,
        sanitized_events=sanitized_events,
        forced_verdict=forced_verdict,
        failure_category_override=failure_category_override,
        blocker=blocker,
        artifact_revalidation=artifact_revalidation,
        scenario_completion_assertions=_vc07_completion_assertions(scenarios),
    )
    payload["controller_invocation_count"] = len(attempt_scenarios)
    _atomic_write_text(
        output_dir / "result.json",
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    return payload


def _vc07_successful_spawn_count(stdout: str) -> int:
    return sum(
        1
        for event in _event_objects(stdout)
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "collab_tool_call"
        and (event["item"].get("tool") or event["item"].get("name")) == "spawn_agent"
        and event["item"].get("status") == "completed"
    )


def _run_vc07_scenario(
    *,
    plan_path: Path,
    install_plan_hash: str,
    codex_version: str,
    model: str,
    timeout_seconds: int,
    scenario: ScenarioSpec,
    expected_adapter_artifact_hash: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not scenario.requires_installed_skill or scenario.scenario_kind not in {"positive", "boundary"}:
        raise ValueError("VC-07 runner requires one fixed installed-skill scenario")
    scenario_root = Path(tempfile.mkdtemp(prefix=f"harnesskit-{scenario.scenario_id}."))
    workspace = scenario_root / "workspace"
    home = scenario_root / "home"
    codex_home = scenario_root / "codex-home"
    temp_dir = scenario_root / "tmp"
    transient = scenario_root / "transient"
    for path in (workspace, home, codex_home, temp_dir, transient):
        path.mkdir(parents=True, exist_ok=True)

    auth_link = codex_home / "auth.json"
    auth_source = _real_auth_path()
    sanitized_events: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    cleanup_auth_removed = False
    cleanup_workspace_removed = False
    cleanup_process_group_absent = True
    failure_category = "controller"
    failure_assertions: dict[str, bool] = {"runtime_controller_completed": False}
    try:
        initialized = _run(["git", "init", "-q"], cwd=workspace, timeout_seconds=30)
        if initialized.returncode != 0:
            raise RuntimeError("synthetic VC-07 fixture git initialization failed")
        install = _apply_and_verify(plan_path, workspace)
        installed_artifact = workspace / VC07_INSTALLED_CODEX_ARTIFACT
        artifact_present = _is_regular_file_within(installed_artifact, workspace)
        installed_artifact_hash = _sha256_file(installed_artifact) if artifact_present else ""
        install_assertions = {
            "install_apply_passed": install["apply_passed"],
            "install_verify_passed": install["verify_passed"],
            "install_project_scope_codex_artifact_present": artifact_present,
            "install_project_scope_codex_artifact_hash_matches_adapter": (
                bool(installed_artifact_hash)
                and installed_artifact_hash == expected_adapter_artifact_hash
            ),
        }
        if not all(install_assertions.values()):
            failure_category = "install"
            failure_assertions = install_assertions
            raise RuntimeError("isolated VC-07 project-scope install gate failed")

        trace_path = workspace / VC07_TRACE_RELATIVE_PATH
        _write_vc07_fixture(
            workspace,
            scenario_kind=scenario.scenario_kind,
            trace_path=trace_path,
        )
        source_path = workspace / VC07_SOURCE_PATH
        regression_test_path = workspace / VC07_REGRESSION_TEST_PATH
        instrumentation_path = workspace / VC07_INSTRUMENTATION_PATH
        observer_path = workspace / "fixture/__init__.py"
        observer_hash_before = _sha256_file(observer_path)
        observer_mode_before = stat.S_IMODE(observer_path.stat().st_mode)
        initial_source_hash = _sha256_file(source_path)
        initial_instrumentation_present = instrumentation_path.is_file()
        failure_assertions = {
            "fixture_trace_observer_ready": bool(observer_hash_before),
            "fixture_trace_observer_read_only": observer_mode_before == 0o444,
            "fixture_trace_absent_before_runtime": not trace_path.exists(),
            "fixture_regression_test_absent_before_runtime": not regression_test_path.exists(),
            "fixture_positive_tagged_instrumentation_present": (
                initial_instrumentation_present if scenario.scenario_kind == "positive" else True
            ),
            "fixture_boundary_instrumentation_absent": (
                not initial_instrumentation_present if scenario.scenario_kind == "boundary" else True
            ),
        }
        if not all(failure_assertions.values()):
            raise RuntimeError("VC-07 fixture did not materialize cleanly")
        before = _tree_manifest(workspace)
        isolation_before = _tree_manifest(scenario_root)
        git_before = _git_metadata_manifest(workspace)

        failure_category = "auth"
        failure_assertions = {"runtime_auth_source_available": auth_source.is_file()}
        if not auth_source.is_file():
            raise RuntimeError("Codex auth source is unavailable")
        runtime_env = _sanitized_runtime_env(
            os.environ.copy(), home=home, codex_home=codex_home, temp_dir=temp_dir
        )
        runtime_env["PYTHONDONTWRITEBYTECODE"] = "1"
        resolvable_forbidden_runtimes = sorted(
            runtime
            for runtime in FORBIDDEN_MODEL_RUNTIMES
            if shutil.which(runtime, path=runtime_env["PATH"]) is not None
        )
        if resolvable_forbidden_runtimes:
            failure_category = "privacy"
            failure_assertions = {"privacy_forbidden_runtimes_unresolvable": False}
            raise RuntimeError("sanitized PATH exposes a forbidden model runtime")
        failure_category = "runtime_tool"
        failure_assertions = {"runtime_native_codex_available": False}
        codex_executable = _resolve_codex_native_executable()
        failure_category = "runtime"
        failure_assertions = {"runtime_process_completed": False}
        last_message_path = transient / "last-message.txt"
        runtime_argv = _codex_argv(
            workspace=workspace,
            last_message=last_message_path,
            prompt=scenario.prompt,
            model=model,
            enable_search=False,
            enable_multi_agent=False,
        )
        runtime_argv[0] = str(codex_executable)
        auth_link.symlink_to(auth_source)
        try:
            runtime_outcome = _run_process_group(
                runtime_argv,
                cwd=workspace,
                env=runtime_env,
                timeout_seconds=timeout_seconds,
            )
        finally:
            if auth_link.is_symlink() or auth_link.exists():
                auth_link.unlink()
        cleanup_auth_removed = not auth_link.exists()
        cleanup_process_group_absent = runtime_outcome.group_absent_after_cleanup
        runtime = runtime_outcome.completed
        runtime_blocker_category = (
            _codex_runtime_blocker_category(runtime.stderr) if runtime.returncode != 0 else None
        )
        last_message = (
            last_message_path.read_text(encoding="utf-8")
            if last_message_path.is_file()
            else ""
        )
        sanitized_events = _sanitize_events(runtime.stdout, scenario_id=scenario.scenario_id)
        forbidden_commands = _forbidden_runtime_commands(runtime.stdout)
        forbidden_observed_processes = _observed_forbidden_processes(runtime_outcome)
        forbidden_runtime_processes = sorted(
            set(forbidden_commands) | set(forbidden_observed_processes)
        )
        github_contact_observation = _vc05_github_contact_observation(runtime.stdout)
        validation_assertions, validation_errors = _validate_vc07_result(
            last_message,
            scenario_kind=scenario.scenario_kind,
        )
        trace_records = _vc07_trace_records(trace_path)
        event_execution_assertions = _vc07_event_execution_assertions(runtime.stdout)
        spawn_count = _vc07_successful_spawn_count(runtime.stdout)
        observer_hash_after = (
            _sha256_file(observer_path)
            if _is_regular_file_within(observer_path, workspace)
            else ""
        )
        observer_mode_after = (
            stat.S_IMODE(observer_path.stat().st_mode)
            if _is_regular_file_within(observer_path, workspace)
            else None
        )
        final_source_hash = _sha256_file(source_path)
        instrumentation_removed = not instrumentation_path.exists()
        tag_absent_from_authored_paths = all(
            path.is_file() and VC07_INSTRUMENTATION_TAG not in path.read_text(encoding="utf-8")
            for path in (source_path, regression_test_path)
        )

        if scenario.scenario_kind == "positive":
            trace_assertions = _vc07_trace_assertions(
                trace_records,
                initial_source_hash=initial_source_hash,
            )
            execution_assertions = _vc07_execution_assertions(
                trace_assertions,
                event_execution_assertions,
            )
            original_recheck = _vc07_unittest(workspace, VC07_ORIGINAL_REPRO_MODULE)
            regression_recheck = _vc07_unittest(workspace, VC07_REGRESSION_TEST_MODULE)
            existing_recheck = _vc07_unittest(workspace, "fixture.tests.test_existing_behavior")
            full_suite_recheck = _vc07_unittest(
                workspace,
                "discover",
                "-s",
                "fixture/tests",
                "-p",
                "test_*.py",
            )
            regression_shape = _vc07_regression_test_shape(regression_test_path)
            behavior_assertions = {
                **validation_assertions,
                **execution_assertions,
                "controller_original_repro_recheck_passed": original_recheck.returncode == 0,
                "controller_regression_test_passed": regression_recheck.returncode == 0,
                "controller_existing_behavior_preserved": existing_recheck.returncode == 0,
                "controller_full_suite_passed": full_suite_recheck.returncode == 0,
                "controller_regression_test_shape_valid": all(regression_shape.values()),
                "controller_source_changed_after_repro": final_source_hash != initial_source_hash,
                "controller_tagged_instrumentation_removed": instrumentation_removed,
                "controller_tag_absent_from_authored_paths": tag_absent_from_authored_paths,
                "controller_no_architecture_agent_spawn": spawn_count == 0,
            }
            root_cause_proof = {
                "correct_seam_reproduced": all(
                    execution_assertions.get(key) is True
                    for key in (
                        "execution_initial_original_repro_failed",
                        "execution_same_original_repro_rechecked_after_fix",
                    )
                ),
                "root_cause_regression_fixed": all(regression_shape.values())
                and regression_recheck.returncode == 0
                and full_suite_recheck.returncode == 0,
                "original_repro_rechecked": execution_assertions.get(
                    "execution_same_original_repro_rechecked_after_fix"
                )
                is True
                and original_recheck.returncode == 0,
                "tagged_instrumentation_removed": instrumentation_removed
                and tag_absent_from_authored_paths,
                "architecture_handoff_deferred": validation_assertions.get(
                    "architecture_handoff_deferred_recorded"
                )
                is True
                and spawn_count == 0,
                "issue_mutation_absent": validation_assertions.get("exact_mutation_summary") is True
                and github_contact_observation["github_contact_observed"] is False,
            }
            observed_summary = (
                "correct-seam repro, regression fix, same repro recheck, and instrumentation cleanup "
                "were independently observed"
                if all(behavior_assertions.values())
                else "root-cause runtime result validation failed"
            )
        else:
            no_trace_started = not trace_path.exists()
            behavior_assertions = {
                **validation_assertions,
                "controller_boundary_source_unchanged": final_source_hash == initial_source_hash,
                "controller_boundary_regression_test_absent": not regression_test_path.exists(),
                "controller_boundary_instrumentation_absent": not instrumentation_path.exists(),
                "controller_boundary_trace_not_started": no_trace_started,
                "controller_no_architecture_agent_spawn": spawn_count == 0,
            }
            root_cause_proof = {
                "insufficient_evidence_guess_patch_refused": validation_assertions.get(
                    "guess_patch_and_issue_mutation_rejected"
                )
                is True,
                "issue_mutation_refused": validation_assertions.get(
                    "no_source_mutation_recorded"
                )
                is True
                and github_contact_observation["github_contact_observed"] is False,
                "no_workspace_mutation": final_source_hash == initial_source_hash
                and not regression_test_path.exists(),
                "instrumentation_not_started": no_trace_started and not instrumentation_path.exists(),
            }
            observed_summary = (
                "insufficient evidence refused a guess patch and issue mutation without workspace writes"
                if all(behavior_assertions.values())
                else "insufficient-evidence boundary validation failed"
            )
        if runtime_blocker_category is not None:
            observed_summary = "Codex auth, account, or service state prevented runtime proof"

        behavior_errors = [key for key, value in behavior_assertions.items() if not value]
        after = _tree_manifest(workspace)
        isolation_after = _tree_manifest(scenario_root)
        git_after = _git_metadata_manifest(workspace)
        changed_paths = _tree_diff(before, after)
        isolation_changed_paths = _tree_diff(isolation_before, isolation_after)
        git_changed_paths = _tree_diff(git_before, git_after)
        allowed_workspace_paths = _vc07_allowed_workspace_manifest_paths(scenario)
        unexpected_workspace_paths = sorted(set(changed_paths) - allowed_workspace_paths)
        allowed_isolation_paths = {
            *(f"workspace/{path}" for path in allowed_workspace_paths),
            "transient/last-message.txt",
        }
        isolation_change_summary = _summarize_isolation_changes(
            isolation_changed_paths,
            controller_allowed_paths=allowed_isolation_paths,
        )
        assertions: dict[str, bool] = {
            **install_assertions,
            "runtime_command_passed": runtime.returncode == 0,
            "runtime_not_timed_out": not runtime_outcome.timed_out,
            "runtime_final_message_is_json_object": _extract_json_object(last_message) is not None,
            "runtime_requested_model_explicit": (
                "--model" in runtime_argv
                and runtime_argv[runtime_argv.index("--model") + 1] == model
            ),
            "runtime_multi_agent_disabled": "multi_agent" not in runtime_argv,
            "runtime_installed_skill_directly_requested": VC07_INSTALLED_CODEX_ARTIFACT.as_posix()
            in scenario.prompt,
            "runtime_github_credentials_absent": all(
                key not in runtime_env for key in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_PAT")
            ),
            "runtime_fixture_trace_observer_unchanged": (
                bool(observer_hash_before)
                and observer_hash_after == observer_hash_before
                and observer_mode_after == observer_mode_before
            ),
            "runtime_fixture_trace_observer_read_only": observer_mode_before == 0o444,
            "behavior_contract_passed": not behavior_errors,
            "mutation_only_allowed_paths": not unexpected_workspace_paths,
            "mutation_isolated_roots_only_allowed_paths": not isolation_change_summary[
                "unexpected_changed_paths"
            ],
            "mutation_git_metadata_unchanged": not git_changed_paths,
            "privacy_no_non_codex_runtime_dispatch": not forbidden_runtime_processes,
            "privacy_forbidden_runtimes_unresolvable": not resolvable_forbidden_runtimes,
            "privacy_github_contact_observation_available": github_contact_observation[
                "event_stream_observation_available"
            ],
            "privacy_no_github_or_network_contact_observed": not github_contact_observation[
                "github_contact_observed"
            ],
            "privacy_github_observation_raw_values_not_persisted": github_contact_observation[
                "raw_event_values_persisted"
            ]
            is False,
            "privacy_process_observation_available": runtime_outcome.process_observation_available,
            "privacy_root_process_observed": runtime_outcome.root_process_observed,
            "privacy_auth_link_removed_after_runtime": cleanup_auth_removed,
            "cleanup_process_group_absent": cleanup_process_group_absent,
            "cleanup_no_unexpected_process_group_termination": not (
                not runtime_outcome.timed_out
                and (runtime_outcome.term_sent or runtime_outcome.kill_sent)
            ),
        }
        assertions.update({f"behavior_{key}": value for key, value in behavior_assertions.items()})
        result = {
            "fixture_id": f"synthetic-{scenario.scenario_id}",
            "captured_at": _captured_at(),
            "runtime_target": "codex",
            "component_id": ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
            "slice_id": "VC-07",
            "scenario_id": scenario.scenario_id,
            "scenario_kind": scenario.scenario_kind,
            "observed_codex_version": codex_version,
            **_model_evidence(model),
            "isolated_workspace_identity": _sha256_text(str(workspace.resolve())),
            "isolated_workspace_hash_before": _tree_hash(before),
            "isolated_workspace_hash_after": _tree_hash(after),
            "install_plan_hash": install_plan_hash,
            "installed_codex_artifact_path": VC07_INSTALLED_CODEX_ARTIFACT.as_posix(),
            "installed_codex_artifact_hash": installed_artifact_hash,
            "prompt_purpose": scenario.prompt_purpose,
            "prompt_hash": _sha256_text(scenario.prompt),
            "prompt_revision_id": scenario.prompt_revision_id,
            "expected_behavior": scenario.expected_behavior,
            "observed_behavior_summary": observed_summary,
            "loader_evidence": {
                "installed_skill_artifact_present": artifact_present,
                "installed_skill_artifact_hash_matches_adapter": install_assertions[
                    "install_project_scope_codex_artifact_hash_matches_adapter"
                ],
                "direct_skill_request_observed": assertions[
                    "runtime_installed_skill_directly_requested"
                ],
                "automatic_loader_claimed": False,
            },
            "root_cause_proof": root_cause_proof,
            "trace_observation": {
                "record_count": len(trace_records),
                "raw_trace_persisted": False,
                "trace_assertions": (
                    _vc07_trace_assertions(trace_records, initial_source_hash=initial_source_hash)
                    if scenario.scenario_kind == "positive"
                    else {"trace_not_started": not trace_path.exists()}
                ),
                "event_execution_assertions": event_execution_assertions,
            },
            "github_contact_observation": github_contact_observation,
            "before_after_tree": {
                "allowed_changed_paths": list(scenario.allowed_changed_paths),
                "actual_changed_paths": changed_paths,
                "unexpected_changed_paths": unexpected_workspace_paths,
            },
            "before_after_git": {
                "metadata_hash_before": _tree_hash(git_before),
                "metadata_hash_after": _tree_hash(git_after),
                "actual_changed_paths": git_changed_paths,
            },
            "before_after_isolation_root": {
                "manifest_hash_before": _tree_hash(isolation_before),
                "manifest_hash_after": _tree_hash(isolation_after),
                "controller_allowed_changed_paths": sorted(allowed_isolation_paths),
                **isolation_change_summary,
            },
            "observed_filesystem_changed_paths": changed_paths,
            "observed_git_metadata_changed_paths": git_changed_paths,
            "forbidden_runtime_processes": forbidden_runtime_processes,
            "expected_vs_actual_assertions": assertions,
            "privacy": {
                "synthetic_fixture_only": True,
                "external_mutation_credentials_removed": True,
                "raw_prompt_persisted": False,
                "raw_model_response_persisted": False,
                "raw_environment_persisted": False,
                "raw_event_values_persisted": False,
                "raw_trace_persisted": False,
                "auth_copied": False,
                "runtime_system_path": RUNTIME_SYSTEM_PATH,
            },
            "process_group_cleanup": {
                "timed_out": runtime_outcome.timed_out,
                "term_sent": runtime_outcome.term_sent,
                "kill_sent": runtime_outcome.kill_sent,
                "process_reaped": runtime_outcome.process_reaped,
                "group_absent_after_cleanup": runtime_outcome.group_absent_after_cleanup,
                "process_observation_available": runtime_outcome.process_observation_available,
                "root_process_observed": runtime_outcome.root_process_observed,
                "observed_executables": list(runtime_outcome.observed_executables),
            },
            "stderr_observation": "empty" if not runtime.stderr.strip() else "non-empty-not-persisted",
            "runtime_blocker_observation": {
                "category": runtime_blocker_category,
                "stderr_persisted": False,
            },
            "verdict": (
                "BLOCKED"
                if runtime_blocker_category is not None
                else "PASS" if all(assertions.values()) else "NEEDS_WORK"
            ),
            "failure_category": runtime_blocker_category or _failure_category(assertions),
        }
    except Exception as exc:
        result = {
            "fixture_id": f"synthetic-{scenario.scenario_id}",
            "captured_at": _captured_at(),
            "runtime_target": "codex",
            "component_id": ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
            "slice_id": "VC-07",
            "scenario_id": scenario.scenario_id,
            "scenario_kind": scenario.scenario_kind,
            "observed_codex_version": codex_version,
            **_model_evidence(model),
            "install_plan_hash": install_plan_hash,
            "prompt_purpose": scenario.prompt_purpose,
            "prompt_hash": _sha256_text(scenario.prompt),
            "prompt_revision_id": scenario.prompt_revision_id,
            "expected_behavior": scenario.expected_behavior,
            "observed_behavior_summary": "VC-07 scenario controller failed before a complete verdict",
            "expected_vs_actual_assertions": failure_assertions,
            "privacy": {
                "synthetic_fixture_only": True,
                "raw_prompt_persisted": False,
                "raw_model_response_persisted": False,
                "raw_trace_persisted": False,
                "auth_copied": False,
            },
            "controller_error": type(exc).__name__,
            "verdict": "BLOCKED" if failure_category in {"auth", "runtime_tool"} else "NEEDS_WORK",
            "failure_category": failure_category,
        }
    finally:
        if auth_link.is_symlink() or auth_link.exists():
            auth_link.unlink()
        cleanup_auth_removed = not auth_link.exists()
        shutil.rmtree(scenario_root, ignore_errors=True)
        cleanup_workspace_removed = not scenario_root.exists()

    result["cleanup"] = {
        "auth_link_removed": cleanup_auth_removed,
        "isolated_workspace_removed": cleanup_workspace_removed,
        "process_group_absent": cleanup_process_group_absent,
    }
    result.setdefault("expected_vs_actual_assertions", {})["cleanup_auth_link_removed"] = (
        cleanup_auth_removed
    )
    result["expected_vs_actual_assertions"]["cleanup_workspace_removed"] = (
        cleanup_workspace_removed
    )
    result["expected_vs_actual_assertions"]["cleanup_process_group_absent"] = (
        cleanup_process_group_absent
    )
    if not cleanup_auth_removed or not cleanup_workspace_removed or not cleanup_process_group_absent:
        result["verdict"] = "NEEDS_WORK"
        result["failure_category"] = "cleanup"
    return result, sanitized_events


def _run_vc07_slice(
    *,
    model: str,
    timeout_seconds: int,
    evidence_dir: Path | None,
    retry_failed_from: Path | None,
    revalidate_after_adapter_change_from: Path | None,
) -> tuple[dict[str, Any], Path]:
    if retry_failed_from is not None and revalidate_after_adapter_change_from is not None:
        raise ValueError("retry and artifact revalidation are mutually exclusive")
    scenarios = _vc07_scenario_specs()
    output_dir = evidence_dir or (EVIDENCE_ROOT / "VC-07" / _timestamp())
    if output_dir.exists():
        raise FileExistsError(f"evidence directory already exists: {output_dir}")
    run_root = Path(tempfile.mkdtemp(prefix="harnesskit-vc-07-controller."))
    predecessor_result = revalidate_after_adapter_change_from or retry_failed_from
    attempt_kind = (
        "artifact-revalidation"
        if revalidate_after_adapter_change_from is not None
        else "failed-only-rerun" if retry_failed_from is not None else "initial"
    )
    selected_scenario_ids = [scenario.scenario_id for scenario in scenarios]
    install_plan_hash = "unavailable"
    adapter_artifact_hash = "unavailable"
    codex_version = "unavailable"
    static_gate: dict[str, bool] = {"preflight_completed": False}
    attempt_plan: AttemptPlan | None = None
    attempt_results: list[dict[str, Any]] = []
    sanitized_events: list[dict[str, Any]] = []

    def effective_scenarios() -> list[dict[str, Any]]:
        carried = list(attempt_plan.carried_forward_results) if attempt_plan else []
        records = {
            item["scenario_id"]: item
            for item in [*carried, *attempt_results]
            if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
        }
        return [records[scenario.scenario_id] for scenario in scenarios if scenario.scenario_id in records]

    def checkpoint(active_scenario_id: str | None) -> None:
        _write_checkpoint(
            output_dir,
            slice_id="VC-07",
            component_id=ROOT_CAUSE_DEBUGGING_COMPONENT_ID,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            active_scenario_id=active_scenario_id,
            completed_scenarios=attempt_results,
            sanitized_events=sanitized_events,
        )

    def write_current(
        *,
        forced_verdict: str | None = None,
        failure_category_override: str | None = None,
        blocker: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _write_vc07_evidence(
            output_dir,
            codex_version=codex_version,
            model=model,
            install_plan_hash=install_plan_hash,
            adapter_artifact_hash=adapter_artifact_hash,
            static_gate=static_gate,
            attempt_scenarios=attempt_results,
            scenarios=effective_scenarios(),
            sanitized_events=sanitized_events,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            predecessor_result_path=(
                attempt_plan.predecessor_result_path if attempt_plan else None
            ),
            predecessor_result_hash=(
                attempt_plan.predecessor_result_hash if attempt_plan else None
            ),
            forced_verdict=forced_verdict,
            failure_category_override=failure_category_override,
            blocker=blocker,
            artifact_revalidation=(attempt_plan.artifact_revalidation if attempt_plan else None),
        )

    def terminalize(
        *, verdict: str, failure_category: str, phase: str, blocker_type: str
    ) -> tuple[dict[str, Any], Path]:
        return (
            write_current(
                forced_verdict=verdict,
                failure_category_override=failure_category,
                blocker={
                    "phase": phase,
                    "type": blocker_type,
                    "message_persisted": False,
                },
            ),
            output_dir,
        )

    try:
        checkpoint(active_scenario_id=None)
        try:
            plan_path, install_plan_hash, adapter_artifact_hash, static_gate = _vc07_static_gate(
                run_root
            )
        except (RuntimeError, OSError, json.JSONDecodeError, ValueError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="static_or_install",
                phase="static_and_install_preflight",
                blocker_type=type(exc).__name__,
            )
        try:
            codex_version = _codex_version()
        except (RuntimeError, OSError) as exc:
            return terminalize(
                verdict="BLOCKED",
                failure_category="runtime_tool",
                phase="codex_native_tool_preflight",
                blocker_type=type(exc).__name__,
            )
        try:
            previous_payload = (
                json.loads(predecessor_result.read_text(encoding="utf-8"))
                if predecessor_result is not None
                else None
            )
            if previous_payload is not None and not isinstance(previous_payload, dict):
                raise ValueError("predecessor result must be a JSON object")
            attempt_plan = (
                _plan_vc07_artifact_revalidation(
                    scenarios,
                    previous_payload=previous_payload,
                    predecessor_result=predecessor_result,
                    current_install_plan_hash=install_plan_hash,
                    current_codex_version=codex_version,
                    requested_model=model,
                    current_adapter_artifact_hash=adapter_artifact_hash,
                )
                if revalidate_after_adapter_change_from is not None
                else _plan_vc07_attempt(
                    scenarios,
                    previous_payload=previous_payload,
                    predecessor_result=predecessor_result,
                    current_install_plan_hash=install_plan_hash,
                    current_codex_version=codex_version,
                    requested_model=model,
                    current_adapter_artifact_hash=adapter_artifact_hash,
                )
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="controller",
                phase="attempt_planning",
                blocker_type=type(exc).__name__,
            )
        selected_scenario_ids = [
            scenario.scenario_id for scenario in attempt_plan.selected_scenarios
        ]
        checkpoint(active_scenario_id=None)
        if not _real_auth_path().is_file():
            return terminalize(
                verdict="BLOCKED",
                failure_category="auth",
                phase="codex_auth_preflight",
                blocker_type="AuthFileUnavailable",
            )
        for scenario in attempt_plan.selected_scenarios:
            checkpoint(active_scenario_id=scenario.scenario_id)
            result, events = _run_vc07_scenario(
                plan_path=plan_path,
                install_plan_hash=install_plan_hash,
                codex_version=codex_version,
                model=model,
                timeout_seconds=timeout_seconds,
                scenario=scenario,
                expected_adapter_artifact_hash=adapter_artifact_hash,
            )
            prompt_revision_change = next(
                (
                    item
                    for item in attempt_plan.prompt_revision_changes
                    if item["scenario_id"] == scenario.scenario_id
                ),
                None,
            )
            if prompt_revision_change is not None:
                result["prompt_revision_change"] = prompt_revision_change
            attempt_results.append(result)
            sanitized_events.extend(events)
            checkpoint(active_scenario_id=None)
            if result.get("verdict") == "BLOCKED":
                return write_current(), output_dir
        return write_current(), output_dir
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def run_slice(
    *,
    slice_id: str,
    model: str,
    timeout_seconds: int,
    evidence_dir: Path | None = None,
    retry_failed_from: Path | None = None,
    revalidate_after_adapter_change_from: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if retry_failed_from is not None and revalidate_after_adapter_change_from is not None:
        raise ValueError("retry and artifact revalidation are mutually exclusive")
    if slice_id == "VC-02":
        return _run_vc02_slice(
            model=model,
            timeout_seconds=timeout_seconds,
            evidence_dir=evidence_dir,
            retry_failed_from=retry_failed_from,
            revalidate_after_adapter_change_from=revalidate_after_adapter_change_from,
        )
    if slice_id == "VC-04":
        return _run_vc04_slice(
            model=model,
            timeout_seconds=timeout_seconds,
            evidence_dir=evidence_dir,
            retry_failed_from=retry_failed_from,
            revalidate_after_adapter_change_from=revalidate_after_adapter_change_from,
        )
    if slice_id == "VC-05":
        return _run_vc05_slice(
            model=model,
            timeout_seconds=timeout_seconds,
            evidence_dir=evidence_dir,
            retry_failed_from=retry_failed_from,
            revalidate_after_adapter_change_from=revalidate_after_adapter_change_from,
        )
    if slice_id == "VC-07":
        return _run_vc07_slice(
            model=model,
            timeout_seconds=timeout_seconds,
            evidence_dir=evidence_dir,
            retry_failed_from=retry_failed_from,
            revalidate_after_adapter_change_from=revalidate_after_adapter_change_from,
        )
    scenarios = _scenario_specs(slice_id)
    output_dir = evidence_dir or (EVIDENCE_ROOT / slice_id / _timestamp())
    if output_dir.exists():
        raise FileExistsError(f"evidence directory already exists: {output_dir}")
    run_root = Path(tempfile.mkdtemp(prefix=f"harnesskit-{slice_id.lower()}-controller."))
    predecessor_result = revalidate_after_adapter_change_from or retry_failed_from
    attempt_kind = (
        "artifact-revalidation"
        if revalidate_after_adapter_change_from is not None
        else "failed-only-rerun" if retry_failed_from is not None else "initial"
    )
    selected_scenario_ids = [item.scenario_id for item in scenarios]
    install_plan_hash = "unavailable"
    adapter_artifact_hash = "unavailable"
    codex_version = "unavailable"
    static_gate: dict[str, bool] = {"preflight_completed": False}
    attempt_plan: AttemptPlan | None = None
    attempt_results: list[dict[str, Any]] = []
    sanitized_events: list[dict[str, Any]] = []

    def terminalize(
        *,
        verdict: str,
        failure_category: str,
        phase: str,
        blocker_type: str,
    ) -> tuple[dict[str, Any], Path]:
        carried = list(attempt_plan.carried_forward_results) if attempt_plan else []
        payload = _write_evidence(
            output_dir,
            slice_id=slice_id,
            codex_version=codex_version,
            model=model,
            install_plan_hash=install_plan_hash,
            adapter_artifact_hash=adapter_artifact_hash,
            static_gate=static_gate,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            predecessor_result_path=(
                attempt_plan.predecessor_result_path if attempt_plan else None
            ),
            predecessor_result_hash=(
                attempt_plan.predecessor_result_hash if attempt_plan else None
            ),
            attempt_scenarios=attempt_results,
            scenarios=carried,
            sanitized_events=sanitized_events,
            forced_verdict=verdict,
            failure_category_override=failure_category,
            blocker={
                "phase": phase,
                "type": blocker_type,
                "message_persisted": False,
            },
            artifact_revalidation=(
                attempt_plan.artifact_revalidation if attempt_plan else None
            ),
        )
        return payload, output_dir

    try:
        _write_checkpoint(
            output_dir,
            slice_id=slice_id,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            active_scenario_id=None,
            completed_scenarios=attempt_results,
            sanitized_events=sanitized_events,
        )
        try:
            plan_path, install_plan_hash, static_gate = _static_gate(run_root)
        except (RuntimeError, OSError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="static_or_install",
                phase="static_and_install_preflight",
                blocker_type=type(exc).__name__,
            )
        try:
            adapter_artifact_hash = _current_codex_adapter_hash()
        except (RuntimeError, OSError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="adapter",
                phase="adapter_artifact_observation",
                blocker_type=type(exc).__name__,
            )
        try:
            codex_version = _codex_version()
        except (RuntimeError, OSError) as exc:
            return terminalize(
                verdict="BLOCKED",
                failure_category="runtime_tool",
                phase="codex_native_tool_preflight",
                blocker_type=type(exc).__name__,
            )
        previous_payload: dict[str, Any] | None = None
        try:
            if predecessor_result is not None:
                previous_payload = json.loads(predecessor_result.read_text(encoding="utf-8"))
                if not isinstance(previous_payload, dict):
                    raise ValueError("predecessor result must be a JSON object")
            if revalidate_after_adapter_change_from is not None:
                attempt_plan = _plan_artifact_revalidation(
                    scenarios,
                    previous_payload=previous_payload,
                    predecessor_result=predecessor_result,
                    current_install_plan_hash=install_plan_hash,
                    current_codex_version=codex_version,
                    requested_model=model,
                    current_adapter_artifact_hash=adapter_artifact_hash,
                )
            else:
                attempt_plan = _plan_attempt(
                    scenarios,
                    previous_payload=previous_payload,
                    predecessor_result=predecessor_result,
                    current_install_plan_hash=install_plan_hash,
                    current_codex_version=codex_version,
                    requested_model=model,
                    current_adapter_artifact_hash=adapter_artifact_hash,
                )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return terminalize(
                verdict="NEEDS_WORK",
                failure_category="controller",
                phase="attempt_planning",
                blocker_type=type(exc).__name__,
            )
        selected_scenario_ids = [item.scenario_id for item in attempt_plan.selected_scenarios]
        _write_checkpoint(
            output_dir,
            slice_id=slice_id,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            active_scenario_id=None,
            completed_scenarios=attempt_results,
            sanitized_events=sanitized_events,
        )
        if not _real_auth_path().is_file():
            return terminalize(
                verdict="BLOCKED",
                failure_category="auth",
                phase="codex_auth_preflight",
                blocker_type="AuthFileUnavailable",
            )
        try:
            source_truth = (
                _fetch_pinned_source_truth()
                if any(item.scenario_kind == "positive" for item in attempt_plan.selected_scenarios)
                else None
            )
        except (OSError, RuntimeError, UnicodeError) as exc:
            return terminalize(
                verdict="BLOCKED",
                failure_category="external_source",
                phase="pinned_primary_source_retrieval",
                blocker_type=type(exc).__name__,
            )
        for scenario in attempt_plan.selected_scenarios:
            _write_checkpoint(
                output_dir,
                slice_id=slice_id,
                attempt_kind=attempt_kind,
                selected_scenario_ids=selected_scenario_ids,
                active_scenario_id=scenario.scenario_id,
                completed_scenarios=attempt_results,
                sanitized_events=sanitized_events,
            )
            result, events = _run_scenario(
                run_root=run_root,
                plan_path=plan_path,
                install_plan_hash=install_plan_hash,
                codex_version=codex_version,
                model=model,
                timeout_seconds=timeout_seconds,
                scenario=scenario,
                source_truth=source_truth if scenario.scenario_kind == "positive" else None,
                expected_adapter_artifact_hash=adapter_artifact_hash,
            )
            prompt_revision_change = next(
                (
                    item
                    for item in attempt_plan.prompt_revision_changes
                    if item["scenario_id"] == scenario.scenario_id
                ),
                None,
            )
            if prompt_revision_change is not None:
                result["prompt_revision_change"] = prompt_revision_change
            attempt_results.append(result)
            sanitized_events.extend(events)
            _write_checkpoint(
                output_dir,
                slice_id=slice_id,
                attempt_kind=attempt_kind,
                selected_scenario_ids=selected_scenario_ids,
                active_scenario_id=None,
                completed_scenarios=attempt_results,
                sanitized_events=sanitized_events,
            )
            if result.get("verdict") == "BLOCKED":
                effective_by_id = {
                    item["scenario_id"]: item
                    for item in [*attempt_plan.carried_forward_results, *attempt_results]
                }
                partial_results = [
                    effective_by_id[item.scenario_id]
                    for item in scenarios
                    if item.scenario_id in effective_by_id
                ]
                payload = _write_evidence(
                    output_dir,
                    slice_id=slice_id,
                    codex_version=codex_version,
                    model=model,
                    install_plan_hash=install_plan_hash,
                    adapter_artifact_hash=adapter_artifact_hash,
                    static_gate=static_gate,
                    attempt_kind=attempt_kind,
                    selected_scenario_ids=selected_scenario_ids,
                    predecessor_result_path=attempt_plan.predecessor_result_path,
                    predecessor_result_hash=attempt_plan.predecessor_result_hash,
                    attempt_scenarios=attempt_results,
                    scenarios=partial_results,
                    sanitized_events=sanitized_events,
                    artifact_revalidation=attempt_plan.artifact_revalidation,
                )
                return payload, output_dir
        effective_by_id = {
            item["scenario_id"]: item
            for item in [*attempt_plan.carried_forward_results, *attempt_results]
        }
        scenario_results = [effective_by_id[item.scenario_id] for item in scenarios]
        payload = _write_evidence(
            output_dir,
            slice_id=slice_id,
            codex_version=codex_version,
            model=model,
            install_plan_hash=install_plan_hash,
            adapter_artifact_hash=adapter_artifact_hash,
            static_gate=static_gate,
            attempt_kind=attempt_kind,
            selected_scenario_ids=selected_scenario_ids,
            predecessor_result_path=attempt_plan.predecessor_result_path,
            predecessor_result_hash=attempt_plan.predecessor_result_hash,
            attempt_scenarios=attempt_results,
            scenarios=scenario_results,
            sanitized_events=sanitized_events,
            artifact_revalidation=attempt_plan.artifact_revalidation,
        )
        return payload, output_dir
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload, output_dir = run_slice(
            slice_id=args.slice_id,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
            retry_failed_from=args.retry_failed_from,
            revalidate_after_adapter_change_from=args.revalidate_after_adapter_change_from,
        )
    except (NotImplementedError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"evidence_dir": str(output_dir.relative_to(REPO_ROOT)), "verdict": payload["verdict"]}))
    return 0 if payload["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
