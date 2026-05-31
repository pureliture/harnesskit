# Harness Requirements

Use this skill when a user wants to create a complete HarnessKit capability and needs an approved requirements source of truth before blueprint, component authoring, adapter authoring, or evaluation.

## Scope

Turn user intent, supplied references, optional research packets, and local migration context into a `requirements.md` source of truth.

- Capture goal, source mode, constraints, must-have behavior, should-have behavior, non-goals, safety boundaries, and evidence expectations.
- Keep reference research optional. A user may choose to create requirements from their own intent only.
- Stop before component graph and implementation decisions.
- Handoff only after the requirements are approved for blueprinting.

Do not decide final component kinds. Do not choose whether the final result is a skill, agent, hook, workflow, rule, command, or profile.

Do not write component files under `components/**`. Do not edit `components/registry.yml`, profiles, adapters, install plans, live runtime surfaces, or generated `dist/**` outputs.

Do not claim source quality, license approval, adapter support, or runtime support.

## Source Modes

| mode | when | required input |
| --- | --- | --- |
| `no_external` | The user does not want reference research. | User goal and constraints. |
| `user_supplied_refs` | The user supplies GitHub URLs, local paths, docs, skillhub pages, or open-source references. | Paths or URLs and intended use. |
| `research_requested` | The user asks the LLM to find or compare references. | Research question and allowed source scope. |
| `local_migration` | An installed local skill, agent, hook, or workflow is being moved into HarnessKit. | Local paths and target harness intent. |

Reference 조사/평가가 필요 없는 경우도 정상 흐름이다. In that case, record `reference_packet: not_requested`.

## Intake

Confirm only the fields needed to write the requirements SoT:

1. User-facing capability goal.
2. Source mode.
3. Target users or tools: Codex, Claude Code, Gemini, repo-local only, or mixed.
4. Must-have behavior.
5. Explicit non-goals.
6. Evidence expectation: requirements only, static component authoring, adapter output, runtime probe, or deferred runtime proof.

Ask one narrow question only when the missing answer would materially change the requirements. If source mode is unclear, ask only for source mode first.

## Requirements SoT Output

Write or update one requirements document using this shape:

```markdown
# <Capability Name> Requirements

## Goal
## Source Mode
## Inputs And References
## Must-Have Requirements
## Should-Have Requirements
## Non-Goals
## Safety And Boundary Rules
## Evidence Expectations
## Open Questions
## Approval Status
```

`Approval Status` must be one of:

- `draft`
- `approved-for-blueprint`
- `blocked`

Requirements must be testable or evaluable. Avoid vague words such as "good", "complete", or "robust" unless the document defines how to evaluate them.

## Handoff

Do not hand off to `harness-blueprint` unless `Approval Status` is `approved-for-blueprint`.

The handoff packet must include:

- `requirements_md`;
- source mode;
- reference packet path or `not_requested`;
- open questions;
- explicit deferrals;
- evidence expectations.

## Final Report

Report:

- requirements file path;
- source mode;
- references included, skipped, or not requested;
- open questions;
- approval status;
- explicit exclusions: blueprint, component files, adapter files, runtime probes.
