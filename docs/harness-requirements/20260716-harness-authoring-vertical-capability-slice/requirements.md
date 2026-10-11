# Harness Authoring Vertical Capability Slice Requirements

## Goal

기존 HarnessKit authoring chain이 승인된 blueprint 뒤의 작업을 기술 phase별 batch가 아니라
작고 완결된 capability slice별로 진행하게 한다.

하나의 capability slice는 사용자가 확인할 수 있는 좁은 authoring outcome 또는 Use Case다.
현재 slice에 필요한 canonical component 묶음, 기존 target adapter output, evaluation을 같은
범위로 끝까지 연결하고 `PASS`를 받은 뒤에만 다음 slice를 선택한다.

이 목표는 Harness Maintenance profile 전체를 재구축하거나 새 runtime orchestration을 만드는
것이 아니다. 기존 manual authoring workflow와 역할 경계를 유지하면서 vertical slice 작업
규칙만 강화한다.

기존 component authoring은 이미 blueprint slice를 하나씩 처리한다. 이 요구사항은 그 방식을
교체하지 않는다. Component authoring에서 끝나던 명시적 completion boundary를 같은 slice의
adapter output과 evaluation `PASS`까지 연결한다.

### Terminology

- `capability slice`: 승인된 blueprint가 정의한 좁고 완결된 authoring outcome. 필요한 경우
  하나 이상의 component를 포함하며, 그 자체로 static evidence를 검토할 수 있어야 한다.
- `current slice`: workflow가 지금 처리 대상으로 선택한 capability slice 하나.
- `slice path`: current slice가 canonical authoring, adapter output, evaluation을 차례로 통과하는
  동일 범위의 작업 흐름.
- `horizontal phase batching`: 여러 slice의 canonical 작업을 먼저 몰아서 하고, adapter output과
  evaluation을 나중에 일괄 처리하는 방식.
- `slice completion`: current slice의 required canonical output, approved existing target output,
  required static evidence가 준비되고 evaluation verdict가 `PASS`인 상태.

## Source Mode

- `user_supplied_refs`
- `reference_packet`: `not_requested`
- authority: user-approved local HarnessKit requirements

사용자가 제공한 GitHub repository, YouTube URL, 영상 분석 본문은 vertical slice 개념을
이해하기 위한 contextual reference다. 이 요구사항의 권위 source는 사용자 의도와 승인된 local
requirements다.

외부 reference는 canonical component의 formal upstream influence로 채택하지 않는다. Fresh
snapshot 등록, source registry 변경, upstream prose 또는 file layout 복사는 요구하지 않는다.

## Inputs And References

### User Intent

- 기존 harness authoring chain에 vertical slice 작업 규칙만 적용한다.
- Slice는 component 개수나 기술 layer가 아니라 독립적으로 확인 가능한 Use Case 또는 capability다.
- 하나의 slice에 outcome 완성에 필요한 여러 component가 포함될 수 있다.
- 승인된 blueprint 뒤에는 current slice 하나만 선택한다.
- Current slice는 canonical component 묶음, 기존 adapter output, evaluation까지 같은 범위로
  끝나야 한다.
- Evaluation verdict가 `PASS`일 때만 다음 slice를 선택한다.
- `PASS_WITH_DEFERRED`, `NEEDS_WORK`, `BLOCKED`는 current slice에서 흐름을 멈춘다.
- 외부 reference는 참고용이며 local requirements가 authority다.
- Evidence 범위는 canonical instruction, manifest, provenance, 기존 generated adapter output,
  최소 static/smoke verification이다.
- Claude Code 또는 Codex live runtime proof는 요구하지 않는다.

### Contextual References

- [mattpocock/skills](https://github.com/mattpocock/skills)
- [Vertical slice 설명 영상](https://youtu.be/A8mokin_YOs?si=zmPXp16qo-xU_GMk)
- 사용자가 제공한 영상 분석 본문:
  - 기능 또는 Use Case 단위로 slice를 정한다.
  - 하나의 slice가 필요한 모든 layer를 end-to-end로 관통한다.
  - 각 slice는 작동하거나 독립적으로 검증 가능한 결과를 만든다.
  - 기술 stack 또는 artifact 종류별 ticket 분할은 horizontal anti-pattern이다.
  - 다음 slice는 현재 slice에서 얻은 feedback 뒤에 진행한다.

위 내용은 개념 해석에만 사용한다. 물리적 Wire Harness의 DB, API, UI, 핀, 전압, 전류 규칙은
HarnessKit 요구사항이 아니다.

### Existing Authoring Chain In Scope

사용자가 지정한 범위는 다음 기존 authoring capability와 그 paired role이다.

- requirements authoring과 requirements analyst;
- blueprint authoring과 blueprint author;
- component authoring과 component author;
- adapter authoring과 adapter author;
- skill evaluation과 harness evaluator;
- manual harness creation workflow.

이 목록은 기존 chain의 적용 대상을 기록한다. 정확한 canonical file allowlist, component별
변경 내용, generated output allowlist, test file은 뒤의 blueprint가 결정해야 한다.

### Local Evidence Inspected

- `components/skills/harness-blueprint/SKILL.md`는 multi-component blueprint를 authoring slice로
  나누고 component author가 한 slice씩 처리하도록 이미 요구한다.
- 현재 blueprint slice contract는 component id, kind, output family, files, non-responsibilities,
  evaluation gates 중심이며 capability outcome의 end-to-end closure를 명시하지 않는다.
- `components/skills/component-authoring/SKILL.md`는 blueprint slice 하나를 canonical authoring
  contract로 사용하고 static component validation을 요구한다.
- `components/skills/adapter-authoring/SKILL.md`는 canonical validation 뒤 approved target의 static
  adapter output을 작성하지만, current slice와 next-slice gate를 직접 연결하지 않는다.
- `components/skills/skill-evaluation/SKILL.md`는 `PASS`, `PASS_WITH_DEFERRED`, `NEEDS_WORK`,
  `BLOCKED` verdict를 이미 정의한다.
- `components/workflows/harness-creation/workflow.yml`과 `card.md`는 requirements, blueprint,
  canonical authoring, adapter authoring, evaluation을 전체 phase 순서로 표현한다. 이 순서는
  horizontal batching을 강제하지 않지만, current slice가 adapter와 evaluation까지 닫히기 전에
  다음 canonical slice로 이동하는 행동도 명시적으로 막지 않는다.
- Harness creation workflow는 `runtime_implemented: false`, `installable: false`인 manual
  definition이며 실행 engine이 아니다.

### Requirements Inferences

- 새 workflow runner 없이도 manual instruction과 handoff contract를 강화해 vertical slice
  순서를 표현할 수 있다.
- 기존 verdict vocabulary를 바꿀 필요가 없다. Workflow가 next-slice progression에 허용할
  verdict만 `PASS`로 제한하면 된다.
- Capability slice는 component 하나와 같지 않다. Outcome에 paired components가 모두
  필요하면 같은 slice에 포함될 수 있다.
- Requirements와 blueprint approval은 전체 작업의 선행 gate다. 각 component 종류를 별도
  horizontal phase로 완성하는 것이 slice가 아니다.
- 운영자가 이미 component마다 canonical, adapter output, evaluation을 끝냈다면 실제 작업 방식은
  vertical slice였다. 이번 변경은 그 동작을 canonical workflow contract와 static evidence로
  명문화하는 contract hardening이다.
- External reference가 contextual input에 머무르므로 source registry와 upstream snapshot을
  갱신할 필요가 없다.

## Must-Have Requirements

### Capability Slice Definition

1. Blueprint 이후의 authoring 단위는 component 종류, file 종류, agent 종류, adapter target 또는
   기술 phase가 아니라 관찰 가능한 authoring outcome 또는 Use Case여야 한다.
2. 각 capability slice는 좁지만 완결된 결과를 정의해야 하며, slice 하나의 evidence만으로 그
   결과가 충족됐는지 독립적으로 평가할 수 있어야 한다.
3. 각 capability slice는 outcome 완성에 필요한 component만 포함해야 한다.
4. 하나의 outcome에 여러 component가 필요하면 같은 slice에 포함할 수 있어야 한다.
5. 관계없는 component를 편의상 같은 slice에 묶거나, component 하나라는 이유만으로 불완전한
   outcome을 별도 slice로 만들면 안 된다.
6. Approved blueprint는 각 slice의 outcome, 범위, 필요한 canonical component 집합,
   non-responsibilities, dependencies, approved existing adapter targets, evidence gate를
   평가 가능한 수준으로 정의해야 한다.
7. Actual component grouping, file allowlist, target output allowlist는 requirements가 아니라
   approved blueprint가 결정해야 한다.

### Slice Selection And Ownership

8. Requirements가 `approved-for-blueprint`가 아니거나 blueprint가 승인되지 않았으면 capability
   slice authoring을 시작하면 안 된다.
9. Manual harness creation workflow는 ready slice 중 current slice 하나만 선택해야 한다.
10. Current slice 선택, 다음 slice 선택, slice 순서 변경은 workflow 책임이어야 한다.
11. Component author, adapter author, evaluator는 다음 slice를 선택하거나 current slice를 임의로
    merge, split, reorder하면 안 된다.
12. Slice 범위가 모호하거나 blueprint와 충돌하면 author가 추정으로 넓히지 말고 current slice를
    `BLOCKED`로 보고해야 한다.

### End-To-End Slice Path

13. Workflow는 current slice의 outcome, component scope, write boundary, target scope,
    non-responsibilities, required evidence를 canonical authoring, adapter authoring, evaluation에
    동일한 의미로 전달해야 한다.
14. Canonical authoring은 current slice에 포함된 component만 작성하거나 수정해야 한다.
15. Current slice의 canonical component가 static validation을 통과하기 전에는 그 component의
    adapter authoring으로 진행하면 안 된다.
16. Adapter authoring은 current slice의 validated canonical component와 approved existing target
    output만 처리해야 한다.
17. Adapter authoring은 future slice output을 미리 만들거나 여러 slice의 generated output을
    batch로 갱신하면 안 된다.
18. Current slice의 required static adapter evidence가 준비되기 전에는 evaluation을
    `PASS`로 끝내면 안 된다.
19. Evaluation은 current slice의 requirements coverage, blueprint conformance, canonical output,
    provenance, approved existing adapter output, required static evidence만 하나의 evidence set으로
    평가해야 한다.
20. Slice path의 모든 phase는 같은 current slice를 가리켜야 하며, 다른 slice의 artifact를
    completion evidence로 대신 사용하면 안 된다.

### Progression And Failure Behavior

21. Evaluation verdict가 `PASS`일 때만 workflow가 다음 slice를 선택할 수 있다.
22. `PASS_WITH_DEFERRED`, `NEEDS_WORK`, `BLOCKED`는 모두 next-slice progression을 막아야 한다.
23. Non-`PASS` verdict가 나오면 workflow는 current slice를 유지하고, 미충족 gate, 필요한 수정,
    재평가 시작점을 보고해야 한다.
24. Component validation 실패 시 adapter authoring을 시작하면 안 된다.
25. Adapter static validation 실패 시 evaluation을 `PASS`로 처리하면 안 된다.
26. Current slice가 `PASS`되기 전에 독립적으로 보이는 future slice를 건너뛰어 처리하면 안 된다.
27. 모든 canonical component를 먼저 작성하고, 모든 adapter output을 나중에 만들고, 전체
    evaluation을 마지막에 실행하는 horizontal phase batching을 금지해야 한다.
28. 기존 evaluator의 verdict 의미 자체는 바꾸지 않아야 한다. 이 요구사항은 manual workflow의
    progression rule만 제한한다.

### Evidence And Runtime Truth

29. 각 completed slice는 canonical component validation, approved existing target output의 static
    generation 또는 consistency check, slice-scoped evaluation result로 증명돼야 한다.
30. Minimal static/smoke verification은 current-slice ordering, phase별 write boundary, 같은 slice
    범위의 handoff, `PASS` next-slice gate, horizontal batching 금지를 확인해야 한다.
31. Requirements 또는 blueprint 문구만으로 slice implementation 완료를 주장하면 안 된다.
32. Generated adapter output은 static adapter proof이며 Claude Code 또는 Codex live runtime
    loading proof로 취급하면 안 된다.
33. Live runtime probe가 이번 scope에서 제외됐다는 사실은 required static gate 누락을
    `PASS_WITH_DEFERRED`로 숨기는 근거가 되면 안 된다.
34. Runtime support, route reliability, hook execution, provider behavior는 기존 runtime evidence
    contract를 충족한 별도 evidence 없이는 주장하면 안 된다.

### Compatibility And Scope Preservation

35. Existing Harness Maintenance profile membership을 변경하면 안 된다.
36. Existing Claude Code와 Codex target 범위를 넓히거나 새 target support를 추가하면 안 된다.
37. Harness creation workflow는 manual definition으로 유지해야 하며 executable orchestration
    authority를 새로 가져서는 안 된다.
38. Tauri app은 기존 output consumer로 유지해야 하며 authoring orchestration 권한을 새로
    가져서는 안 된다.
39. External reference prose, prompt, code, command surface 또는 file layout을 canonical
    instruction으로 복사하면 안 된다.
40. Exact component/file/test changes는 approved blueprint에서 이 requirements 범위를 추적 가능하게
    mapping해야 하며, 범위를 넓히려면 requirements로 되돌아와 재승인받아야 한다.

## Should-Have Requirements

- Capability slice 이름은 내부 file 작업보다 사람이 확인할 수 있는 outcome을 먼저 설명해야 한다.
- Blueprint는 적은 수의 두꺼운 slice보다 검증 가능한 여러 개의 얇은 slice를 선호해야 한다.
- 기존 one-component blueprint slice가 독립적으로 완결된 outcome이면 그대로 유지할 수 있어야 한다.
- Slice를 얇게 만든다는 이유로 독립적으로 의미 없는 manifest-only, prompt-only, adapter-only
  slice를 만들지 않아야 한다.
- 각 slice handoff는 outcome, input, owned output, write boundary, non-responsibilities,
  dependencies, evidence gate를 짧고 일관된 형식으로 보여줘야 한다.
- 각 phase의 final report는 현재 처리한 slice와 남은 progression blocker를 사람이 이해할 수 있는
  말로 먼저 설명해야 한다.
- Existing validation commands, adapter build/check paths, verdict vocabulary를 재사용해야 한다.
- Current slice를 완성하기 전에 future slice를 위한 speculative abstraction이나 공용 contract를
  만들지 않아야 한다.
- Provenance는 local requirements authority와 contextual external references의 차이를 유지해야 한다.
- Minimal smoke evidence는 instruction 문구 존재뿐 아니라 phase 순서와 stop condition의 조합을
  확인해야 한다.

## Non-Goals

- Harness Maintenance profile 전체 재구축.
- 새로운 component kind, profile membership 또는 adapter target 결정.
- Public capability, result, evidence 또는 runtime schema 변경.
- Approval contract 또는 evaluator verdict contract 재설계.
- Runtime controller, scheduler, workflow runner 또는 automatic multi-agent execution loop 추가.
- Hook, prompt interception 또는 새 실행 상태 시스템 추가.
- Credential, archive, retention 또는 runtime evidence storage policy 추가.
- Tauri app, API, read model 또는 orchestration authority 변경.
- 새 install, update 또는 runtime infrastructure 추가.
- Claude Code 또는 Codex live runtime proof 수행.
- Git history rewrite, merge, push, branch cleanup 또는 unrelated commit 변경.
- Upstream source registry refresh, fresh snapshot 채택 또는 upstream implementation 복사.
- Physical Wire Harness의 DB, API, UI, 핀 맵핑, 전압 또는 전류 기능 구현.
- Issue tracker ticket 생성, publish 또는 external mutation.

## Safety And Boundary Rules

- 이 phase에서 작성할 source of truth는 이 `requirements.md` 하나다.
- `Approval Status`는 사용자 승인 전까지 `draft`여야 한다.
- `requirements.md`가 승인되기 전에는 `blueprint.md`, canonical component, provenance, adapter,
  generated output, test를 수정하면 안 된다.
- HTML visualization은 review companion일 뿐 source of truth가 아니다.
- Local user intent와 승인된 requirements가 authority다. Contextual reference는 scope를
  자동으로 넓히지 않는다.
- Static evidence와 runtime evidence를 섞으면 안 된다.
- Secret, credential, raw environment dump, private command log를 requirements 또는 evidence에
  기록하면 안 된다.
- 새로운 요구가 public schema, runtime system, profile membership, install infrastructure,
  Tauri 또는 live runtime proof를 필요로 하면 작업을 멈추고 requirements 재승인을 받아야 한다.

## Evidence Expectations

### Requirements Phase

- 이 파일이
  `docs/harness-requirements/20260716-harness-authoring-vertical-capability-slice/requirements.md`에
  존재한다.
- Source mode, contextual reference boundary, local authority가 분리되어 있다.
- Capability slice, horizontal phase batching, slice completion이 평가 가능한 말로 정의되어 있다.
- Must-have requirements가 slice selection, end-to-end path, progression, failure behavior,
  static evidence, runtime truth, compatibility boundary를 포함한다.
- Explicit non-goals가 overbroad runtime, schema, profile, Tauri, history 범위를 차단한다.
- User approval 전 status는 `draft`다.

### Blueprint Phase

- Approved blueprint는 각 must-have requirement를 existing authoring chain surface와 evidence
  gate에 mapping해야 한다.
- Approved blueprint는 capability outcome 중심 slice 예시와 horizontal anti-pattern을
  구분해야 한다.
- Approved blueprint는 current slice의 write/read boundary와 exact static/smoke verification을
  정해야 한다.
- Approved blueprint는 public schema, runtime controller, profile membership, Tauri, new install
  infrastructure 또는 live runtime proof를 추가하면 안 된다.

### Canonical And Adapter Authoring Phases

- Canonical authoring evidence는 approved current slice에 포함된 file만 변경됐음을 보여야 한다.
- Adapter authoring evidence는 같은 current slice의 approved existing target output만
  생성·검사됐음을 보여야 한다.
- Generated output과 canonical source의 consistency는 existing build/check path로 확인해야 한다.
- Static validation은 runtime support proof로 보고하면 안 된다.

### Evaluation Phase

- Evaluation은 current slice와 동일한 requirements, blueprint boundary, canonical output,
  provenance, adapter output, static evidence를 검토해야 한다.
- `PASS`만 next-slice progression evidence로 인정한다.
- 다른 verdict는 blocker와 재평가 조건을 남겨야 한다.
- Required evidence가 빠졌으면 `PASS`를 주면 안 된다.

### Runtime Evidence

- Live runtime evidence는 이번 요구사항에서 `not_requested`다.
- Runtime proof 부재는 static completion claim을 자동으로 실패시키지 않지만, runtime success
  claim은 계속 금지한다.

## Open Questions

- 없음.

## Approval Status

`approved-for-blueprint`

사용자가 2026-07-16에 requirements 내용을 승인했다.

## Handoff Packet For harness-blueprint-author

- `requirements_md`:
  `docs/harness-requirements/20260716-harness-authoring-vertical-capability-slice/requirements.md`
- `source_mode`: `user_supplied_refs`
- `reference_packet`: `not_requested`
- `open_questions`: 없음
- `explicit_deferrals`: exact capability slice grouping and order; exact canonical file allowlist;
  exact generated output allowlist; exact static/smoke test selection; canonical authoring; adapter
  authoring; evaluation execution; runtime probes
- `evidence_expectations`: blueprint traceability next; current-slice canonical validation, approved
  existing target output consistency, slice-scoped evaluation, and minimal static/smoke proof after
  blueprint approval; live runtime proof not requested
