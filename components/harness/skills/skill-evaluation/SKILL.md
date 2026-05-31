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
| source/provenance | Source refs, influence, copied/adapted flags, and license notes are present. | pass/fail |
| skill/agent body quality | Trigger, scope, intake, workflow, validation, and final report contract are present. | pass/fail |
| boundary safety | No hidden hooks, live installs, global interception, branch mutation, or remote mutation. | pass/fail |
| adapter static proof | Build/check/tests prove target output shape. | pass/fail/deferred |
| runtime truth | Runtime probe evidence exists when runtime is claimed or required. | pass/fail/deferred |
| regression prompts | Positive and negative prompt scenarios are documented or tested. | pass/fail/deferred |

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
- required fixes;
- whether runtime proof was present, absent, or not requested.
