# HarnessKit 제작 흐름

이 Workflow card는 HarnessKit 컴포넌트를 만드는 수동 절차를 설명한다. 실행 엔진이 아니다.

## 목적

사용자 의도와 선택적인 참고자료에서 시작해, 한 번에 하나의 기능 단위를 끝까지 처리한다.

1. 요청이 있을 때만 참고자료를 정리한다.
2. 요구사항 source of truth를 승인한다.
3. 컴포넌트 청사진을 확정한다.
4. 현재 처리할 기능 단위 하나만 선택한다.
5. 같은 기능 단위의 기준 컴포넌트를 작성한다.
6. 같은 기능 단위의 정적 adapter 산출물을 작성한다.
7. 기능 단위 전체를 근거와 함께 평가한다.
8. `PASS` 이후에만 다음 기능 단위를 선택한다.

## 사용 시점

새 HarnessKit skill, agent, hook, rule, command, Workflow card 또는 profile 기반 기능을 요구사항부터 청사진, 작성,
평가까지 순서대로 만들 때 사용한다.

참고자료 조사는 선택 사항이다. 외부 참고자료가 필요하지 않으면 `no_external` mode로 `harness-requirements`부터 시작한다.

## 산출물

| 단계 | 산출물 |
| --- | --- |
| 참고자료 준비 | 요청된 경우 `reference-packet.md` 또는 `reference-packet.yml` |
| 요구사항 확정 | `approved-for-blueprint` 상태의 `requirements.md` |
| 청사진 작성 | 컴포넌트 결정과 작성 단위를 담은 `blueprint.md` |
| 현재 기능 단위 선택 | 승인된 청사진에서 선택한 기능 단위 packet 하나 |
| 기준 컴포넌트 작성 | `component.yml`, `SKILL.md` 또는 `prompt.md`, `workflow.yml`, `provenance.map.yml` |
| 어댑터 산출물 작성 | target metadata, adapter template 또는 검증된 `dist/<target>/...` output |
| 근거 평가 | gate 결과를 담은 `evaluation.md` 또는 최종 보고 |

## 현재 기능 단위 반복

청사진이 승인되면 준비된 기능 단위 하나만 선택한다. 기준 컴포넌트 작성, adapter 작성과 평가가 끝날 때까지
같은 current slice packet을 유지한다. 평가는 기능 단위 전체를 대상으로 하며 컴포넌트 하나의 결과만으로 닫지 않는다.

- `PASS`는 현재 기능 단위를 닫고 다음 준비된 기능 단위를 선택할 수 있게 한다.
- `PASS_WITH_DEFERRED`는 현재 기능 단위를 유지한다.
- `NEEDS_WORK`는 수정과 재평가를 위해 현재 기능 단위를 유지한다.
- `BLOCKED`는 경계 또는 근거 blocker가 해결될 때까지 현재 기능 단위를 유지한다.

`PASS`가 아닌 모든 보고는 충족하지 못한 gate, 필요한 수정과 재평가 시작점을 명시한다. 모든 기준 컴포넌트를 먼저
몰아서 작성한 뒤 모든 adapter를 작성하지 않는다. 한 기능 단위를 평가까지 끝낸 후 다음 기능 단위를 선택한다.

## 중단 조건

요구사항이 승인되지 않았으면 청사진 작성 전에 중단한다.

청사진이 유효한 컴포넌트 기능 단위를 정의하지 않았으면 컴포넌트 작성 전에 중단한다.

기준 컴포넌트 기록이 검증되지 않으면 adapter 작성 전에 중단한다.

현재 평가 결과가 정확히 `PASS`가 아니면 다음 기능 단위를 선택하기 전에 중단한다.

probe 근거가 없으면 runtime 성공을 주장하기 전에 중단한다.

## 비목표

- Workflow runner를 만들지 않는다.
- 자동 multi-agent 실행 loop를 만들지 않는다.
- runtime 상태나 scheduler를 저장하지 않는다.
- 생성 hook이나 prompt interception을 추가하지 않는다.
- 숨겨진 live install을 실행하지 않는다.
- branch, commit, push 또는 PR 작업을 수행하지 않는다.
- 정적 adapter output만으로 runtime support를 주장하지 않는다.

## Runtime 상태

`runtime_implemented: false`. 별도 runtime runner가 명시적으로 설계·구현·검증되기 전까지 이 Workflow는 정의 전용이다.
