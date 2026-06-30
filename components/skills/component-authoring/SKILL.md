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

All components live flat under `components/<kind>s/<slug>/` (there are no per-bucket subtrees). Record the bucket a component belongs to with the single `domain` field in its manifest: `domain: harness` for harness-maintenance components, `domain: scm` or `domain: work` for those toolchains, and `domain: core` otherwise.

## Authoring Rules

- Keep canonical body files adapter-neutral.
- Record target-specific fields only as non-binding notes when needed; never emit target output files in this phase.
- Add exactly one `components/registry.yml` entry per component.
- Set new component status to `draft`.
- Do not add `targets`, `adapter`, `runtime_supported`, or `adapter_generated` claims for newly authored canonical-only components unless the user has separately requested adapter-authoring.
- Add provenance for every component. Record source mode, copied or adapted content, attribution, license uncertainty, and review triggers.
- Prefer concise procedure over long background. Put only content the future agent needs to do the work.

## Prose Craft Rules

When authoring a skill, agent prompt, or workflow body, write reusable reference, not a story. The body must teach a future model the technique, not narrate how this task went.

Avoid these prose anti-patterns:

- Narrative example: do not write "in this session we found that an empty dir caused …". It is too specific to reuse. Write the general pattern and its trigger instead.
- Multi-language dilution: do not restate the same example in several languages. One excellent, complete, well-commented example in the most fitting language beats many mediocre ones.
- Fill-in-the-blank templates and contrived examples: prefer a real, ready-to-adapt example over a generic placeholder skeleton.
- Code or logic packed into a flowchart, and generic labels (`step1`, `helper2`): use a flowchart only for a non-obvious decision point, and give every node a meaning.

For discipline-enforcing components, state the rule, then close the obvious workarounds explicitly rather than only naming the rule. A full rationalization-resistance catalogue (loophole closing, red-flags lists) is an evaluation concern; here keep it to explicit counters for the workarounds you can foresee. See `harnesskit.skill.skill-evaluation` for the evaluation-side craft lenses.

### Canonical Body Quality Gate

Before finishing a skill body, check that it gives the next agent a predictable process:

- The description names the real trigger and does not list duplicate synonyms as separate branches.
- Each ordered step has a checkable completion criterion.
- Shared rules live in one source of truth; do not restate policy text from another component when a reference is enough.
- Put material every run needs in `SKILL.md`; push branch-only reference to a named sibling file only when that file is part of the canonical owned output family.
- Co-locate each concept with its caveats and stop conditions.
- Remove no-op advice that would not change model behavior.
- Keep the skill tied to its mission: what work it is for, who continues from it, and what durable artifact proves the work happened.

### Handoff Notes

If component authoring cannot be completed in one session, write a compact handoff in the user's requested location or report text. Reference existing artifacts by path instead of duplicating them, redact sensitive details, and list suggested next skills such as `adapter-authoring` or `skill-evaluation` only when those phases are actually next.

## Textless Prompt-Submit Hook Contract

When authoring a hook for prompt-submit, pre-invocation, or any similar user-submission lifecycle:

- Treat missing, empty, whitespace-only, image-only or attachment-only input as a valid user submission.
- If no prompt text or transcript text can be derived, specify no-op/pass-through behavior.
- State that inability to build query/context must not block the user's main message flow.
- Limit fatal behavior to malformed hook envelopes, wrong event wiring, broken registration, missing executable dependencies, or other wiring failures.
- Do not encode image moderation, OCR, attachment parsing, or runtime support claims unless those are explicitly in the approved requirements and evidence gates.

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
