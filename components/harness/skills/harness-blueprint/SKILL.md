# Harness Blueprint

Use this skill after approved HarnessKit requirements exist and before canonical authoring, when deciding component kinds, boundaries, profile placement, adapter targets, and evaluation gates.

## Scope

Convert approved `requirements.md` into a component graph and authoring plan.

Allowed decisions:

- component kind decision;
- component boundary split;
- per-component responsibility;
- profile and install surface plan;
- provenance plan;
- evaluation gates;
- adapter target intent;
- deferred runtime gates.

Do not rewrite requirements except to quote or cite the approved source of truth.

Do not write final component body files, adapter outputs, generated `dist/**` artifacts, or live runtime files.

Do not claim runtime success. A blueprint can require a runtime gate, but it is not runtime evidence.

## Inputs

Read these before writing the blueprint:

1. Approved `requirements.md`.
2. Optional `reference-packet`.
3. Current `components/registry.yml`.
4. Current `profiles/*.yml`.
5. Current `capabilities.yml`.

If requirements are not approved for blueprint, stop and return to `harness-requirements`.

## Component Kind Decision Matrix

| need | choose | avoid |
| --- | --- | --- |
| Reusable procedure triggered by user intent | `skill` | `agent` if no isolated execution is needed |
| Bounded delegated worker with its own context and output family | `agent` | huge all-purpose agent |
| Lifecycle enforcement or deterministic prompt/session mutation | `hook` | hook for optional workflow selection |
| Installation bundle or persona/package | `profile` | profile for one component only |
| Human-readable multi-step flow | `workflow` card | workflow runner |
| Deterministic CLI entrypoint | `command` | command before target support is proven |
| Persistent policy/instruction | `rule` | rule before adapter/runtime support is proven |

When requirements ask "should this be a skill or an agent?", answer here, not in requirements or component authoring.

The blueprint may choose skill, agent, hook, workflow, rule, or command as the component kind.

## Blueprint Output

Write `blueprint.md` using this shape:

```markdown
# <Capability Name> Harness Blueprint

## Inputs
## Component Graph
## Component Decisions
## Responsibility Boundaries
## Files To Author
## Profile And Install Plan
## Adapter Target Plan
## Provenance Plan
## Evaluation Gates
## Deferred Work
## Handoff To Component Authoring
```

Each `Component Decisions` row must include:

| id | kind | reason | owner | inputs | outputs | non-responsibilities |
| --- | --- | --- | --- | --- | --- | --- |

## Handoff To Component Authoring

Split a multi-component blueprint into authoring slices.

Each slice must include:

- component id;
- kind;
- title and summary;
- source influence;
- owned output family;
- files to create;
- explicit non-responsibilities;
- evaluation gates relevant to that slice.

`component-author` must author one slice at a time unless the user explicitly asks for batch authoring.

If the blueprint conflicts with schema, registry, capabilities, or provenance constraints, do not silently change the component shape. Report the conflict.

## Final Report

Report:

- blueprint file path;
- components proposed;
- existing components reused;
- new components required;
- deferred runtime gates;
- handoff slices;
- explicit exclusions: requirements rewrite, canonical authoring, adapter implementation, runtime probes.
