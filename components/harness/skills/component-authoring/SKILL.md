# Component Authoring

Use this skill when the user asks to create or migrate HarnessKit canonical component records for an already-approved skill, agent, hook, rule, command, or workflow.

## Scope

- Ideation is out of scope. Do not brainstorm whether the component should exist, rename the product direction, or expand the request into new capabilities.
- Author only canonical HarnessKit records under `components/**` for the component currently being authored.
- Do not create adapter outputs for the component currently being authored under `.claude/`, `.codex/`, `.agents/`, `dist/`, or any target runtime directory.
- This `component-authoring` harness component may itself be ported by `adapter-authoring`; that is a separate self-porting step.
- Do not claim runtime support, trigger reliability, or adapter support.
- Do not run live provider, Codex, Claude Code, or hook runtime probes.

## Intake

Confirm the minimum authoring inputs from the user's prompt and referenced files:

1. Component kind: `skill`, `agent`, `hook`, `rule`, `command`, or `workflow`.
2. Source mode: `blank`, `local`, `oss`, or `adapter_migration`.
3. Canonical identity: slug, title, summary, and `harnesskit.<kind>.<slug>` id.
4. Review boundary: copied, adapted, rewritten, or newly authored content.

Ask one narrow question only when these inputs cannot be inferred from local files and the wrong assumption would create the wrong component.

## Harness Blueprint Handoff

When an approved `blueprint.md` exists, treat it as the authoring contract.

Before writing canonical files, extract one blueprint slice and confirm:

- component id and kind;
- owned output family;
- files to create;
- source influence and provenance notes;
- explicit non-responsibilities;
- evaluation gates that must remain visible to later phases.

Do not re-decide whether the component should be a skill, agent, hook, workflow, rule, or command. If the blueprint slice is ambiguous or conflicts with current schema, registry, or provenance rules, stop and report the conflict instead of silently changing the design.

## Required Outputs

Write the smallest canonical record set for the component kind.

- Skill: `component.yml`, `SKILL.md`, `provenance.map.yml`.
- Agent: `agent.yml`, `prompt.md`, `provenance.map.yml`, and optional `output.schema.json`.
- Hook: `component.yml`, `hook.md`, `provenance.map.yml`.
- Workflow: `workflow.yml`, `card.md`, `provenance.map.yml`.

Harness-maintenance components belong under `components/harness/<kind>s/<slug>/`. General reusable engineering components stay under the existing `components/<kind>s/<slug>/` trees unless the user explicitly chooses another category.

## Authoring Rules

- Keep canonical body files adapter-neutral.
- Record target-specific fields only as non-binding notes when needed; never emit target output files in this phase.
- Add exactly one `components/registry.yml` entry per component.
- Set new component status to `draft`.
- Do not add `targets`, `adapter`, `runtime_supported`, or `adapter_generated` claims for newly authored canonical-only components unless the user has separately requested adapter-authoring.
- Add provenance for every component. Record source mode, copied or adapted content, attribution, license uncertainty, and review triggers.
- Prefer concise procedure over long background. Put only content the future agent needs to do the work.

## Validation

Run static validation when available:

```bash
uv run python scripts/components/validate.py --component <component-id>
```

Static validation proves registry, file shape, provenance, and authoring boundary compliance. It does not prove runtime behavior.

## Final Report

Report:

- canonical files created or changed;
- registry/profile changes;
- static validation command and result;
- explicit exclusions: ideation, adapter porting, runtime probes.

If the user asks whether it is complete, answer only for the canonical authoring phase unless adapter/runtime work was separately requested and actually verified.
