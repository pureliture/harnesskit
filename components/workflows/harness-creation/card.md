# Harness Creation Workflow

This workflow card shows the manual HarnessKit creation sequence. It is not an execution engine.

## Purpose

Create a complete HarnessKit capability from user intent and optional references while keeping each phase bounded:

1. optional reference packet;
2. approved requirements source of truth;
3. component blueprint;
4. canonical component authoring;
5. static adapter authoring;
6. evidence-bound evaluation.

## When To Use

Use this workflow when the user wants to add a new HarnessKit skill, agent, hook, rule, command, workflow card, or profile-backed capability and the work should proceed from requirements to blueprint to authoring.

Reference research is optional. If the user wants to skip external references, start with `harness-requirements` using `no_external`.

## Artifacts

| step | artifact |
| --- | --- |
| Reference Mode | `reference-packet.md` or `reference-packet.yml` when requested |
| Requirements | `requirements.md` with `approved-for-blueprint` status |
| Blueprint | `blueprint.md` with component decisions and authoring slices |
| Canonical Authoring | `component.yml`, `SKILL.md` or `prompt.md`, `workflow.yml`, and `provenance.map.yml` |
| Adapter Authoring | target metadata, adapter templates or checked `dist/<target>/...` outputs |
| Evaluation | `evaluation.md` or final report with gate results |

## Stop Conditions

Stop before blueprint if requirements are not approved.

Stop before component authoring if the blueprint does not define a valid component slice.

Stop before adapter authoring if canonical component records do not validate.

Stop before runtime claims if probe evidence is missing.

## Non-Goals

- No workflow runner.
- No automatic multi-agent execution loop.
- No creation hook or prompt interception.
- No hidden live install.
- No branch, commit, push, or PR operation.
- No runtime support claim from static adapter output alone.

## Runtime Status

`runtime_implemented: false`. This workflow is definition-only until a separate runtime runner is explicitly designed, implemented, and verified.
