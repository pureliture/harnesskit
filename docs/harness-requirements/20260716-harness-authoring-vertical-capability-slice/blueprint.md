# Harness Authoring Vertical Capability Slice Blueprint

## Inputs

### Approved Source Of Truth

- requirements:
  `docs/harness-requirements/20260716-harness-authoring-vertical-capability-slice/requirements.md`
- requirements status: `approved-for-blueprint`
- source mode: `user_supplied_refs`
- reference packet: `not_requested`
- authority: approved local requirements

사용자가 제공한 GitHub repository, YouTube 영상, 영상 분석 본문은 vertical slice 개념을
이해하기 위한 contextual input이다. Canonical component provenance의 새 upstream source로
채택하지 않으며 source registry 또는 snapshot을 갱신하지 않는다.

### Design Outcome

승인된 blueprint 뒤에서 manual harness creation workflow가 capability slice 하나를 선택하고,
그 slice의 canonical component, 기존 Claude/Codex adapter output, static evaluation을 같은 범위로
완료한 뒤 `PASS`일 때만 다음 slice를 선택하게 한다.

이번 설계는 vertical slicing을 처음 도입하는 작업이 아니다. 기존 component authoring의
one-slice-at-a-time 규칙을 유지하면서, 명시적인 완료 경계를 canonical validation에서 같은
capability slice의 adapter output과 evaluation `PASS`까지 확장한다.

### Fixed Constraints

- 기존 component만 재사용한다. 새 skill, agent, workflow, hook, rule, command를 만들지 않는다.
- Harness Maintenance profile의 membership, 순서, target을 변경하지 않는다.
- Existing target은 `claude`, `codex`뿐이다.
- Public schema, capability/result/evidence schema, runtime controller, hook, execution state를
  추가하거나 변경하지 않는다.
- Harness creation workflow는 `runtime_implemented: false`, `installable: false`인 manual
  definition으로 유지한다.
- Tauri app, API, read model, credential, archive, retention, install infrastructure를 변경하지
  않는다.
- Live Claude Code/Codex runtime proof를 실행하거나 성공을 주장하지 않는다.
- Requirements pair의 기존 approval boundary는 유지하되, future requirements가 observable
  outcome을 정의하고 actual grouping을 blueprint에 넘기도록 instruction만 명확히 한다.

### Terminology Refinement

- `capability slice`: 사용자가 확인할 수 있는 하나의 authoring outcome이다. 하나 이상의
  component authoring unit을 포함할 수 있다.
- `component authoring unit`: `component-author`가 한 invocation에서 작성하고 검증하는 canonical
  component 하나다.
- `slice_id`: blueprint와 handoff report에서 같은 capability slice를 식별하는 사람이 읽을 수
  있는 값이다. 새 database key, runtime state 또는 public schema field가 아니다.
- `current slice packet`: outcome, dependencies, component set, ordered units, target, read/write
  boundary, non-responsibilities, evidence gate를 담은 blueprint section이다.
- `slice closure`: current slice의 모든 required unit과 existing target output이 static gate를
  통과하고 evaluator verdict가 `PASS`인 상태다.

## Component Graph

### Authoring Lifecycle After This Change

```text
approved requirements
        |
        v
approved blueprint defines ordered capability slices
        |
        v
manual workflow selects exactly one current slice
        |
        v
component-author writes one listed component unit at a time
        |
        v
all required canonical units validate
        |
        v
adapter-author builds only same-slice Claude/Codex outputs
        |
        v
harness-evaluator reviews one same-slice evidence set
        |
        +-- PASS ----------------------> workflow selects next ready slice
        |
        +-- PASS_WITH_DEFERRED --------> keep current slice; report blocker
        +-- NEEDS_WORK ----------------> keep current slice; fix and re-evaluate
        +-- BLOCKED -------------------> keep current slice; resolve boundary
```

Requirements와 blueprint approval은 전체 authoring의 선행 gate다. Vertical 반복은 approved
blueprint 뒤에서 시작한다. Capability slice 안에 component가 여러 개 있어도 current `slice_id`는
바뀌지 않으며, component author는 component unit을 하나씩 처리한다.

### Static Handoff Contract

각 approved blueprint의 capability slice는 최소한 다음 정보를 가져야 한다.

- `slice_id`와 사람이 확인할 수 있는 observable outcome;
- dependencies와 execution order;
- required canonical component set과 ordered component authoring units;
- approved existing adapter targets;
- phase별 read allowlist와 write allowlist;
- non-responsibilities와 future-slice exclusions;
- canonical validation, adapter static proof, evaluation gate;
- `PASS` progression rule과 non-`PASS` stop rule.

이 형식은 Markdown handoff contract다. 별도 YAML/JSON schema나 persisted runtime state를 만들지
않는다.

### Requirements Traceability

| Requirement group | Design owner | Evidence |
| --- | --- | --- |
| Must 1-7: outcome-based slice definition | blueprint skill + author | required current slice packet fields |
| Must 8-12: selection and ownership | manual workflow + all author roles | one current slice, role-specific non-responsibilities |
| Must 13-20: same-slice end-to-end path | component, adapter, evaluator pairs | shared `slice_id`, scoped inputs/outputs, ordered gates |
| Must 21-28: progression and failure | evaluator + manual workflow | `PASS` only advance; three other verdicts stop |
| Must 29-34: static evidence and runtime truth | evaluator + smoke tests | canonical, adapter, evaluation evidence kept separate from runtime |
| Must 35-40: compatibility and scope | file allowlists + regression guards | unchanged profile, registry, capabilities, schema, runtime surfaces |

## Component Decisions

| id | kind | reason | owner | inputs | outputs | non-responsibilities |
| --- | --- | --- | --- | --- | --- | --- |
| `harnesskit.skill.harness-requirements` | skill | Existing goal/must/evidence contract에 observable outcome 기준과 blueprint deferral을 명확히 한다 | requirements phase | user intent, optional references | approved outcome-oriented `requirements.md` | actual slice grouping, selection, authoring |
| `harnesskit.agent.harness-requirements-analyst` | agent | Outcome가 기술 layer/file 목록으로 바뀌지 않았는지 approval 전에 검토한다 | requirements phase | intent, requirements draft | reviewed outcome-oriented requirements handoff | component graph, current slice selection |
| `harnesskit.skill.harness-blueprint` | skill | Must define outcome slices rather than component-only authoring slices | blueprint phase | approved requirements, local registry/profile/capabilities | ordered capability slice packets | canonical authoring, slice execution, runtime state |
| `harnesskit.agent.harness-blueprint-author` | agent | Must produce the same complete packet and identify horizontal anti-patterns | blueprint phase | approved requirements, inspected local contracts | `blueprint.md` with ordered slices | implementation, next-slice progression |
| `harnesskit.skill.component-authoring` | skill | Must preserve current capability outcome while authoring one component unit | canonical phase | approved current slice packet, one selected unit | validating canonical files and unit handoff | adapter output, slice merge/split/reorder/advance |
| `harnesskit.agent.component-author` | agent | Must report the same `slice_id`, unit status, and remaining units | canonical phase | one current-slice component unit | canonical change report and validation evidence | whole-slice PASS, adapter/runtime claim |
| `harnesskit.skill.adapter-authoring` | skill | Must build only validated same-slice units and approved existing targets | adapter phase | current slice packet, validated canonical unit | scoped static `dist` output/check evidence | future-slice batching, live install mutation, runtime proof |
| `harnesskit.agent.adapter-author` | agent | Must preserve the slice target/output allowlist and separate static/runtime truth | adapter phase | current slice id, validated unit, target plan | static adapter report | component redesign, slice selection, runtime verification |
| `harnesskit.skill.skill-evaluation` | skill | Must evaluate the entire same-slice evidence set and gate progression | evaluation phase | requirements, blueprint packet, canonical/provenance/output evidence | one verdict and blockers | source mutation, next-slice selection, live probes |
| `harnesskit.agent.harness-evaluator` | agent | Must verify slice identity, closure, and forbidden cross-slice evidence | evaluation phase | same-slice evidence set | read-only evaluation report | fixes, adapter generation, workflow advancement |
| `harnesskit.workflow.harness-creation` | workflow | Existing manual workflow is the correct owner for selection and PASS-only advancement | workflow/operator | approved blueprint, phase reports, evaluator verdict | current-slice selection and manual progression instructions | executable runner, persisted state, scheduling |

### Chosen Boundary

기존 paired skill/agent 책임과 manual workflow에 distributed handoff contract를 추가한다. Blueprint는
slice의 의미와 순서를 정하고, 각 author는 같은 current slice를 보존하며, evaluator는 closure를
판정하고, workflow만 `PASS` 뒤 다음 slice를 선택한다.

### Alternatives Considered

- Workflow card만 수정: 변경량은 작지만 component/adapter/evaluator prompt가 같은 slice identity와
  write boundary를 보존하지 못하므로 선택하지 않는다.
- Component 하나를 slice 하나로 고정: 구현은 단순하지만 하나의 outcome에 여러 component가 필요한
  requirements와 충돌하므로 선택하지 않는다.
- 새 vertical-slice skill, agent, controller 추가: 기존 blueprint와 workflow 책임을 중복하고 새
  orchestration/state 범위를 만들므로 선택하지 않는다.
- 모든 canonical instruction을 먼저 고친 뒤 output과 evaluation을 일괄 처리: 바로 금지하려는
  horizontal phase batching이므로 선택하지 않는다.
- Post-blueprint 역할을 blueprint/component/adapter/evaluation 네 implementation slice로 분리:
  역할별 기술 phase 묶음으로 읽혀 이번 전략 자체가 horizontal해지므로 선택하지 않는다.

## Responsibility Boundaries

### Two-Level Work Boundary

1. Workflow는 approved blueprint의 ready slice 중 하나를 current slice로 선택한다.
2. Component author는 current slice에 listed된 component unit 하나만 작성한다.
3. 여러 unit이 필요하면 같은 `slice_id`를 유지한 채 unit authoring을 반복한다.
4. 모든 required canonical unit이 validate된 뒤 adapter phase가 같은 slice의 output만 만든다.
5. Evaluator는 unit별 결과를 따로 PASS시키지 않고 전체 current slice closure를 판정한다.
6. Workflow는 evaluator의 exact `PASS` 뒤에만 다음 ready slice를 고른다.

### Ownership Matrix

| Decision | Sole owner | Other roles |
| --- | --- | --- |
| requirements approval | user + requirements phase | blueprint waits |
| slice definition and order | approved blueprint | authors do not reinterpret |
| current/next slice selection | manual workflow/operator | authors and evaluator report only |
| component unit authoring | component-author | adapter/evaluator do not mutate |
| static target generation | adapter-author through existing build path | component-author does not emit output |
| project output materialization | operator through existing install path | adapter-author does not write live paths directly |
| slice verdict | evaluator | workflow consumes exact verdict |
| runtime support claim | existing runtime evidence contract | this blueprint does not request proof |

### Failure Boundaries

- Missing or unapproved requirements/blueprint: authoring is `BLOCKED`.
- Ambiguous slice packet or conflicting file boundary: keep current slice and return `BLOCKED`.
- Canonical validation failure: do not begin adapter work for that unit.
- Adapter static check failure: do not produce evaluation `PASS`.
- Missing required same-slice evidence: `NEEDS_WORK` or `BLOCKED`; not
  `PASS_WITH_DEFERRED` merely because runtime is excluded.
- `PASS_WITH_DEFERRED`, `NEEDS_WORK`, `BLOCKED`: do not select or pre-author a future slice.
- Live runtime proof not requested: report `not_requested`; do not turn it into a runtime success claim.

### Manual Workflow Manifest Contract

`workflow.yml`은 실행 상태를 저장하지 않고 다음 정적 계약만 선언한다. Existing steps에는
`select-current-slice`를 blueprint 뒤에, `advance-after-pass`를 evaluation 뒤에 추가하고,
evaluation step은 existing `harnesskit.agent.harness-evaluator`를 명시한다.

```yaml
- id: select-current-slice
  output: current capability slice packet
  description: Select exactly one ready slice from the approved blueprint.
- id: advance-after-pass
  output: next-slice eligibility
  description: Allow next-slice selection only after the current evaluator verdict is PASS.
```

위 두 record는 existing `steps` list에 삽입되는 exact records다. Existing evaluation record에는
`agent: harnesskit.agent.harness-evaluator`만 추가한다.

```yaml
routine_harness:
  category: harness-maintenance
  runner_deferred: true
  hook_interception_in_scope: false
  capability_slice_contract:
    selection_owner: harnesskit.workflow.harness-creation
    active_slice_limit: 1
    path:
      - canonical-authoring
      - adapter-authoring
      - evaluation
    advance_on:
      - PASS
    stop_on:
      - PASS_WITH_DEFERRED
      - NEEDS_WORK
      - BLOCKED
    horizontal_phase_batching: forbidden
    persisted_runtime_state: false
```

이 mapping은 smoke test가 읽는 declarative policy다. Runner, state machine, result schema 또는
automatic loop로 해석하지 않는다.

## Files To Author

Post-approval implementation may change only the following canonical files and focused tests. Component
manifests are reviewed inputs but do not need changes because id, kind, title, summary, owned files,
targets, adapter metadata, and profile placement stay the same.

### VC-01 — User Intent Becomes Ordered Outcome Slices

Observable outcome: user intent becomes approved outcome-oriented requirements, then an approved blueprint
turns those outcomes into complete ordered capability slice packets without guessing technical layers.

- `components/skills/harness-requirements/SKILL.md`
- `components/skills/harness-requirements/provenance.map.yml`
- `components/agents/harness-requirements-analyst/prompt.md`
- `components/agents/harness-requirements-analyst/provenance.map.yml`
- `components/skills/harness-blueprint/SKILL.md`
- `components/skills/harness-blueprint/provenance.map.yml`
- `components/agents/harness-blueprint-author/prompt.md`
- `components/agents/harness-blueprint-author/provenance.map.yml`
- `tests/smoke/test_harness_vertical_capability_slice.py` — create outcome and slice-packet assertions

### VC-02 — One Current Outcome Reaches Both Canonical And Target Outputs

Observable outcome: authors preserve one current capability slice while its listed component units are
written, validated, and converted only into the approved existing Claude/Codex static outputs. Future
slice files and outputs stay untouched.

- `components/skills/component-authoring/SKILL.md`
- `components/skills/component-authoring/provenance.map.yml`
- `components/agents/component-author/prompt.md`
- `components/agents/component-author/provenance.map.yml`
- `components/skills/adapter-authoring/SKILL.md`
- `components/skills/adapter-authoring/provenance.map.yml`
- `components/agents/adapter-author/prompt.md`
- `components/agents/adapter-author/provenance.map.yml`
- `tests/smoke/test_harness_vertical_capability_slice.py` — add author and adapter boundary assertions
- `tests/smoke/test_authoring_flow_integration.py` — add current/future unit and output isolation proof

### VC-03 — One Outcome Is Evaluated And Closed Before The Next

Observable outcome: one same-slice evidence set receives a verdict, and the manual workflow advances only
on `PASS` while all other verdicts retain the current slice.

- `components/skills/skill-evaluation/SKILL.md`
- `components/skills/skill-evaluation/provenance.map.yml`
- `components/agents/harness-evaluator/prompt.md`
- `components/agents/harness-evaluator/provenance.map.yml`
- `components/workflows/harness-creation/workflow.yml`
- `components/workflows/harness-creation/card.md`
- `components/workflows/harness-creation/provenance.map.yml`
- `tests/smoke/test_harness_vertical_capability_slice.py` — add evaluation identity/closure assertions
- `tests/smoke/test_harness_creation_workflow.py` — add structured PASS/stop/order assertions

### Explicit No-Change Guard

The following surfaces must remain unchanged unless a conflict forces a requirements regression and new
approval.

- `components/registry.yml`
- `profiles/harness-maintenance.yml`
- `capabilities.yml`
- all component manifests: `component.yml`, `agent.yml`
- `schemas/**`
- `adapters/**`
- `scripts/adapters/**`
- `scripts/install/**`
- `scripts/harness/**`
- `.codex/config.toml`
- `.claude/settings.json`
- `.codex/hooks.json`
- `AGENTS.md`, `CLAUDE.md`
- `tests/smoke/test_harness_creation_runtime_probe.py`
- Tauri application and API files

## Profile And Install Plan

- Profile: existing `harness-maintenance` only.
- Membership and order: byte-for-byte unchanged.
- Scope for generated project outputs: `project` only.
- Targets: existing `claude` and `codex` only.
- New install infrastructure: none.
- New runtime activation gate: none.
- User-level installation: not part of this blueprint.

During each rollout slice, build/check only the component ids selected by that slice. Do not use a
profile-wide write build to refresh future-slice output. After component-scoped static outputs pass,
materialize only those components through the existing project-scope install plan/apply path. Inspect the
diff against that slice's generated-output allowlist before evaluation.

After all three rollout slices receive `PASS`, a full `harness-maintenance` profile build/check and a
temporary-project install/verify may run as final regression. This final regression does not reopen or
rewrite earlier slices.

## Adapter Target Plan

No adapter implementation, template, capability entry, or target mapping changes are needed. Existing
component manifests already declare Claude and Codex outputs.

### Generated Output Allowlist

#### VC-01

- `.claude/skills/harness-requirements/SKILL.md`
- `.agents/skills/harness-requirements/SKILL.md`
- `.claude/agents/harness-requirements-analyst.md`
- `.codex/agents/harness_requirements_analyst.toml`
- `.claude/skills/harness-blueprint/SKILL.md`
- `.agents/skills/harness-blueprint/SKILL.md`
- `.claude/agents/harness-blueprint-author.md`
- `.codex/agents/harness_blueprint_author.toml`

#### VC-02

- `.claude/skills/component-authoring/SKILL.md`
- `.agents/skills/component-authoring/SKILL.md`
- `.claude/agents/component-author.md`
- `.codex/agents/component_author.toml`
- `.claude/skills/adapter-authoring/SKILL.md`
- `.agents/skills/adapter-authoring/SKILL.md`
- `.claude/agents/adapter-author.md`
- `.codex/agents/adapter_author.toml`

#### VC-03

- `.claude/skills/skill-evaluation/SKILL.md`
- `.agents/skills/skill-evaluation/SKILL.md`
- `.claude/agents/harness-evaluator.md`
- `.codex/agents/harness_evaluator.toml`

Harness creation workflow는 non-installable이므로 generated adapter output이 없다. Agent manifest의
summary와 runtime registration name은 바뀌지 않으므로 `.codex/config.toml`도 변경하지 않는다.

### Static Truth Boundary

- Canonical source is under `components/**`.
- `dist/<target>/**` proves generated static shape only and remains ignored build evidence.
- Tracked project surfaces above prove existing installer materialization/consistency only.
- Neither surface proves Claude Code or Codex loaded or executed the component.
- Runtime status for this work is `not_requested` and `unverified`.

## Provenance Plan

For each modified canonical component provenance map:

- preserve every existing source, snapshot id, influence, attribution, and license note;
- add the approved requirements path as local requirements SoT;
- add this approved blueprint path as the selected authoring contract after user approval;
- record only the local handoff/progression behavior actually adapted by that component;
- set copied content to false and runtime dependency on sources to false;
- add a review trigger for capability-slice handoff or progression contract changes;
- do not add the GitHub repository or YouTube URL as a component source;
- do not refresh `sources/registry.yml` or create a fresh upstream snapshot.

Local provenance paths:

- `docs/harness-requirements/20260716-harness-authoring-vertical-capability-slice/requirements.md`
- `docs/harness-requirements/20260716-harness-authoring-vertical-capability-slice/blueprint.md`

The blueprint must be approved before its path is recorded as an approved provenance influence.

## Evaluation Gates

### Pre-Authoring Gates

- requirements status is `approved-for-blueprint`;
- this blueprint status is approved by the user;
- current rollout slice is exactly one of `VC-01` through `VC-03`;
- earlier rollout slice has `PASS` before the next starts;
- worktree diff matches the current slice allowlist.

### Per-Slice TDD And Static Gates

Each rollout slice follows the same order:

1. Add or tighten the focused failing smoke/integration assertion for the current outcome.
2. Change only current-slice canonical instructions and provenance.
3. Validate each changed component with the existing component validator.
4. Build/check only current-slice component ids for Claude and Codex.
5. Materialize and compare only current-slice tracked generated outputs.
6. Run the focused test set and inspect the diff allowlist.
7. Evaluate requirements coverage, blueprint conformance, provenance, static output, and runtime-truth
   wording as one same-slice evidence set.
8. Advance only after exact verdict `PASS`.

### Focused Behavioral Assertions

- Blueprint contract requires outcome, `slice_id`, dependencies, component set, ordered units, targets,
  read/write boundaries, non-responsibilities, and evidence gates.
- Component author accepts one unit while preserving the current slice; it cannot select or claim the
  next slice.
- Adapter author accepts only validated same-slice units and approved existing targets.
- Future-slice canonical files and generated outputs remain unchanged or absent.
- Evaluator rejects evidence from a different `slice_id` and does not mutate artifacts.
- Workflow has one current slice, ordered canonical→adapter→evaluation path, `PASS` advance rule, and
  three explicit stop verdicts.
- Horizontal phase batching is forbidden.
- `runtime_implemented` stays false and runtime success is never inferred from static output.

### Targeted Tests

- `tests/smoke/test_harness_vertical_capability_slice.py`
- `tests/smoke/test_authoring_flow_integration.py`
- `tests/smoke/test_harness_creation_workflow.py`

### Unchanged Regression Tests

- `tests/smoke/test_component_authoring_harness.py`
- `tests/smoke/test_adapter_authoring_harness.py`
- `tests/harness/test_evaluation_report.py`
- `tests/smoke/test_profiles.py`
- `tests/smoke/test_provenance.py`
- `tests/smoke/test_registry_integrity.py`
- `tests/smoke/test_capability_matrix.py`

Final regression also runs the existing all-component validator, full Harness Maintenance adapter static
build/check, and project-scope install-plan dry-run/temporary-project verify. Live runtime probe tests are
not part of the requested evidence.

### Verdict And Progression

| Verdict | Current slice | Next slice |
| --- | --- | --- |
| `PASS` | closed | may be selected by workflow |
| `PASS_WITH_DEFERRED` | retained | forbidden |
| `NEEDS_WORK` | retained for fixes | forbidden |
| `BLOCKED` | retained until boundary is resolved | forbidden |

Static required gates cannot be hidden behind `PASS_WITH_DEFERRED`. Runtime proof excluded by requirements
is recorded as `not_requested`, not as a missing static gate and not as runtime success.

## Deferred Work

- Claude Code or Codex live runtime loading/execution proof;
- public capability/result/evidence/runtime schema changes;
- executable workflow runner, controller, scheduler, hooks, or persisted slice state;
- approval/verdict vocabulary redesign;
- Harness Maintenance profile membership or order changes;
- new targets, adapter templates, adapter scripts, install infrastructure, or user-level install;
- Tauri app/API/read-model/orchestration changes;
- source registry refresh or new upstream snapshot;
- Git history rewrite, merge, push, branch cleanup, or unrelated worktree cleanup.

Any need for these items regresses to requirements and requires new user approval.

## Handoff To Component Authoring

### Rollout Order

| Slice | Outcome | Canonical components | Depends on | Closure evidence |
| --- | --- | --- | --- | --- |
| `VC-01` | User intent becomes ordered outcome slices | requirements + blueprint skill/agent pairs | approved blueprint | validators, 8 generated outputs, focused smoke, evaluator `PASS` |
| `VC-02` | One current outcome reaches canonical and target outputs | component + adapter skill/agent pairs | `VC-01 PASS` | validators, 8 generated outputs, isolation/build checks, evaluator `PASS` |
| `VC-03` | One outcome is evaluated and closed before the next | evaluation skill/evaluator + workflow | `VC-02 PASS` | validators, 4 generated outputs, PASS/stop workflow smoke, evaluator `PASS` |

### Current-Slice Execution Rule

- Select only one rollout slice.
- Start with its failing test.
- Author its canonical component units one at a time.
- Build and materialize only its approved Claude/Codex outputs.
- Evaluate the same `slice_id` evidence set.
- Keep the slice current for any non-`PASS` verdict.
- Do not open the next slice until exact `PASS`.

This rollout order itself must follow the vertical strategy it installs. It is forbidden to edit all
canonical components first, regenerate all outputs later, and evaluate only at the end.

### Approval Gate

Canonical authoring, adapter output refresh, tests, install materialization, evaluation execution, runtime
probe, commit, and merge remain blocked until the user approves this blueprint.

## Approval Status

`approved-for-authoring`

사용자가 2026-07-16에 이 `blueprint.md`를 승인했다.
