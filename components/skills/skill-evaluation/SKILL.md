# Skill Evaluation

Use this skill after a HarnessKit component has been authored or adapted and needs evidence-bound evaluation against requirements, references, provenance, adapter outputs, or runtime truth.

## Scope

Evaluate completed HarnessKit skill, agent, hook, rule, command, workflow, or profile work.

Allowed:

- read requirements, blueprint, component files, provenance, registry, profiles, capabilities, adapter outputs, and existing runtime evidence;
- run static validation commands;
- summarize existing runtime evidence when it is already captured;
- write or report an evaluation result.

Do not fix files unless the user asks. Do not run live runtime probes unless explicitly requested. Do not mark `PASS` if required evidence is missing.

Static adapter output is not runtime proof. A generated `dist/<target>/...` file proves static adapter shape only.

Source/provenance traceability is static proof. Missing registry snapshot identity, missing component provenance linkage, or mismatched snapshot references are static failures; do not hide them as runtime deferrals.

## Inputs

Use the relevant subset:

- `requirements.md`;
- `blueprint.md`;
- component id or component files;
- `provenance.map.yml`;
- optional adapter target;
- optional runtime evidence directory;
- commands the user wants counted as evidence.

If approved requirements or blueprint are required but missing, return `BLOCKED` instead of inventing them.

## Evaluation Gates

| gate | check | status |
| --- | --- | --- |
| requirements coverage | Every must/should is mapped to component behavior or explicit deferral. | pass/fail/deferred |
| blueprint conformance | Kind, responsibility, file set, profile plan, and handoff slice match the blueprint. | pass/fail |
| source/provenance | Source refs, reference-time snapshot traceability, influence, copied/adapted flags, and license notes are present. | pass/fail |
| skill/agent body quality | Trigger, scope, intake, workflow, validation, and final report contract are present. | pass/fail |
| skill authoring quality | Trigger clarity, bounded scope, progressive disclosure, justified examples/templates, no-copy evidence, and runtime truth separation are explicit. | pass/fail/deferred |
| boundary safety | No hidden hooks, live installs, global interception, branch mutation, or remote mutation. | pass/fail |
| adapter static proof | Build/check/tests prove target output shape. | pass/fail/deferred |
| runtime truth | Runtime probe evidence exists when runtime is claimed or required. | pass/fail/deferred |
| regression prompts | Positive and negative prompt scenarios are documented or tested. | pass/fail/deferred |

## Source And Provenance Traceability

Treat accepted external GitHub/open-source references as a static traceability chain:

1. `sources/registry.yml` records the source id, requested tracking ref, and accepted reference-time snapshot or an explicit deferred/unavailable state with reason.
2. The snapshot records the durable observed identity: commit, tag, release, package version, version, or explicit unavailable marker.
3. The component `provenance.map.yml` cites both the registry `source_id` and the specific `snapshot_id` when claiming external source influence.

The reference-time snapshot is durable truth for the evaluated component. A later upstream `main`, latest tag, latest release, or package latest value is a freshness comparison target only; it does not replace or excuse the recorded observed identity.

Separate failure sides in the report:

- Registry-side failure: the source registry or snapshot inventory lacks the accepted observed identity, has `active_snapshot: null` without an explicit deferred/unavailable reason, points to a missing snapshot, or cannot distinguish requested ref from observed identity.
- Component-side failure: the component provenance cites an unknown `source_id`, omits the required `snapshot_id`, references a snapshot owned by another source, uses a legacy string to claim external traceability, or cites deferred/unavailable source state as fully traceable.

Each source/provenance finding must name the source id when known, the provenance path when known, the missing or mismatched field, the side (`registry-side` or `component-side`), and one remediation class: add accepted snapshot, mark deferred with reason, mark unavailable with reason, correct `snapshot_id`, or migrate legacy external provenance.

Runtime proof is orthogonal. If static traceability is required and fails, the verdict is `NEEDS_WORK` or `BLOCKED`; it is not `PASS_WITH_DEFERRED` merely because runtime proof is absent or deferred.

## Skill Craft Heuristics

When the component under evaluation is a skill, apply these craft checks as part of the `skill/agent body quality` gate. They are evaluation lenses, not authoring steps.

### Description Quality (CSO)

A skill description must state *when to use* the skill, not *what it does*. A description that summarizes the workflow becomes a shortcut the model follows instead of reading the body, so the body is skipped and multi-step procedures collapse.

Flag a description as failing when it:

- summarizes the skill's process, steps, or workflow;
- is written in first person rather than third person;
- names a specific technology when the skill is not technology-specific;
- is vague ("for async testing") instead of stating concrete triggers, symptoms, and situations.

Pass a description when it starts from a triggering condition ("Use when …"), is third person, and stays technology-agnostic unless the skill itself is technology-bound.

### Baseline And Pressure-Test Evidence

A discipline-enforcing skill claims behavior change. The only evidence that it works is a recorded baseline (the model's behavior without the skill) compared against behavior with the skill present.

When evaluating such a skill, treat the absence of baseline/pressure-test evidence as a deferred or failing `regression prompts` gate, not as a pass. Pressure scenarios should combine stressors (time, sunk cost, authority, exhaustion) so compliance is proven under load, not only in calm review. Reading the skill is not the same as a model using it; require application or pressure scenarios, not academic review alone.

The full rationalization-resistance catalogue (loophole closing, red-flags lists, spirit-vs-letter counters) lives in authoring craft; here it is one check: does a discipline skill carry explicit counters for the rationalizations its baseline surfaced?

## Skill Authoring Quality Rubric

Apply this rubric to HarnessKit skill components and to agent/workflow prompts that behave like reusable instruction bodies. This is an evaluation gate; do not rewrite the component unless the user requested fixes.

### Trigger And Scope

Pass only when the component says when it should be used and where it must stop. The trigger should be specific enough to avoid stealing adjacent work, and the scope should name the owned output family, allowed reads/writes, and explicit non-responsibilities.

Fail when a skill is justified by topic affinity alone, lacks an activation condition, or can silently expand into authoring, adapter output, runtime probing, git mutation, or unrelated review work.

### Bounded Instruction Quality

A skill should carry the minimum instruction needed for repeatable execution. Check that each major step has an observable completion criterion and that the body separates required procedure from optional reference.

Flag vague imperatives, repeated meanings, stale background, or broad "be thorough" language as quality findings when they do not change model behavior.

### Progressive Disclosure

Inline material every invocation needs. Move branch-only reference, examples, schemas, templates, or long background behind clear local pointers. The pointer text must say when the evaluator expects a future model to open that material.

Fail progressive disclosure when the top-level body is overloaded with branch-only detail, or when required instructions are hidden behind a pointer that has no clear trigger.

### Examples And Templates

Examples and templates are acceptable only when they reduce ambiguity for a real branch. They should be ready to adapt, scoped to the component's domain, and should not create a second source of truth that conflicts with the main rule.

Flag generic placeholders, ornamental examples, duplicated templates, or examples that imply unsupported adapter/runtime behavior.

### No-Copy And Provenance

For external references, require the component provenance to show `source_id`, `snapshot_id`, exact `source_path`, `copied_content:false`, and `runtime_dependency_on_sources:false` unless an approved source explicitly allows otherwise.

If direct upstream prose, code, prompts, file layout, or copied structure appears where copying is not approved, the verdict is `NEEDS_WORK` or `BLOCKED` according to the slice contract. Do not soften a no-copy failure into an optional cleanup item.

### Runtime Truth Separation

A canonical skill can define procedure, static validation, and evidence requirements. It cannot claim target runtime behavior, route reliability, hook execution, provider behavior, or adapter support without captured runtime evidence for that exact claim.

Static component validation, generated adapter files, or a well-written prompt body may pass static gates while runtime proof remains deferred. Keep those states separate in the verdict.

## Output

Use this shape for `evaluation.md` or final report:

```markdown
# <Component> Evaluation

## Verdict
PASS | PASS_WITH_DEFERRED | NEEDS_WORK | BLOCKED

## Evidence Reviewed
## Gate Results
## Findings
## Deferred Runtime Claims
## Required Fixes
## Suggested Follow-ups
## Commands Run
```

## Verdict Rules

- `PASS`: all required gates passed and no required gate is deferred.
- `PASS_WITH_DEFERRED`: static authoring is acceptable and runtime or optional gates are explicitly deferred.
- `NEEDS_WORK`: fixable body, provenance, coverage, adapter, or report issues remain.
- `BLOCKED`: approved requirements, blueprint, source permission, or target support is missing.

## Final Report

Report:

- verdict;
- evidence reviewed;
- commands run and exact result;
- failed or deferred gates;
- skill authoring quality findings, when a skill-like body was evaluated;
- required fixes;
- whether runtime proof was present, absent, or not requested.
