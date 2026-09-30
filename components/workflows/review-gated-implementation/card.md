# Historical Workflow Record: review-gated-implementation

`harnesskit.workflow.review-gated-implementation`는 과거의 high-stakes 구현 마일스톤
2단계 순서 리뷰 패턴을 보존한 역사 기록입니다. 이 카드는 실행 엔진, adapter target,
또는 사용자-facing entrypoint가 아닙니다.

## 보존 이유

승인된 spec/design을 따르는 단일 마일스톤 변경을, 통과 순서가 강제된 두 개의
적대적(adversarial) 리뷰 단계로 검증합니다. spec-compliance 리뷰가 PASS하기
전에는 code-quality 리뷰가 실행되지 않으며, 어느 단계든 변경 요청을 내면
implementer로 되돌아가는 fix -> re-review 루프를 돕니다.

이 규칙은 과거의 실험적 순서 리뷰 방식입니다. 현재 어떤 실행 skill도 이 정책을
소유하거나 자동 호출하지 않습니다. 새 작업은 승인된 현재 contract와 repository policy에
맞는 review owner를 별도로 선택하며, 이 카드는 그 선택을 하지 않습니다.

## 현재 경계

- 이 기록은 review 실행, 순서 강제, 자동 multi-agent routing 또는 user-facing handoff를
  만들지 않는다.
- local fixed-diff finding은 필요할 때 `local-change-review`가 만들 수 있고, GitHub PR
  thread mutation은 `github-pr-followup`이 소유한다. 어느 것도 이 카드가 자동 선택하지 않는다.

## 게이트 흐름

```
implement (implementer status)
   │  DONE → 게이트 진입
   │  CONCERNS / NEEDS_CONTEXT / BLOCKED → caller에게 반환 (게이트 미진입)
   ▼
[stage 1] spec-compliance review  (adversarial)
   │  CHANGES_REQUESTED → fix-and-re-review 루프 → stage 1 재진입
   │  PASS ▼
[stage 2] code-quality review     (adversarial, stage 1 PASS 후에만 실행)
   │  CHANGES_REQUESTED → fix-and-re-review 루프 → stage 1 재진입
   │  PASS ▼
both PASS → 마일스톤 게이트 통과 (caller로 결과 반환)
```

순서 불변식: **spec-compliance가 PASS하기 전에는 code-quality 리뷰를 절대
실행하지 않습니다.** fix 이후 재리뷰는 항상 stage 1부터 다시 시작합니다.

## Implementer Status 프로토콜

implementer는 변경 산출 시 정확히 하나의 status 신호를 반환합니다.

| status | 의미 | 게이트 처리 |
| --- | --- | --- |
| `DONE` | 변경 완료, 리뷰 게이트 진입 가능 | stage 1로 진입 |
| `CONCERNS` | 변경했으나 위험 요소를 명시 | caller가 진행 여부 결정 |
| `NEEDS_CONTEXT` | 정보 부족으로 완료 불가 | review가 아니라 caller에게 컨텍스트 요청 |
| `BLOCKED` | 하드 블로커로 변경 불가 | 게이트 중단, 블로커를 표면화 |

`DONE`이 아닌 status는 게이트로 진입하지 않고 caller로 라우팅됩니다.

## Reviewer Stance

두 리뷰어 모두 **적대적(adversarial)** 입장을 취합니다. 통과를 가정하지 않고
명시적으로 실패 근거를 찾으며, 근거가 없을 때만 PASS를 반환합니다. 리뷰어는
구현하지 않고 findings만 반환합니다. 수정은 implementer가 fix 루프에서 수행합니다.

## Review Axis Boundary

두 리뷰 축은 분리해서 보고합니다. spec-compliance는 승인된 spec/design 충족 여부만
판정하고, code-quality는 correctness, edge case, tests, maintainability, documented
repository standards를 봅니다. 한 축의 PASS가 다른 축의 실패를 덮지 않습니다.

각 finding은 diff, spec/design, test evidence, documented standard 중 어떤 근거에서
나왔는지 보여야 합니다. 근거가 없으면 PASS를 보류하지 말고 해당 축에서 finding을
내지 않습니다.

## 프롬프트 템플릿 (핵심 자산)

이 카드의 핵심 자산은 아래 3개 load-bearing 프롬프트 템플릿입니다. caller는
이 템플릿을 채워 각 역할에 전달합니다. 템플릿은 runtime-neutral하며 특정
런타임 형식을 가정하지 않습니다.

### Template 1 — implementer

```
ROLE: Implementer for a single high-stakes milestone.

CONTEXT:
- Approved spec/design (authoritative): <spec/design 발췌 또는 경로>
- Milestone scope (이 단위에서만 변경): <milestone 정의>
- Working isolation: <worktree/branch 또는 변경 경계>

INSTRUCTIONS:
1. 승인된 spec/design 범위 안에서만 이 마일스톤을 구현한다.
2. spec/design을 바꿔야 한다고 판단되면 구현하지 말고 NEEDS_CONTEXT로 멈춘다
   (SoT 변경은 grill-to-spec 상류 책임).
3. 구현이 끝나면 변경 diff와 함께 정확히 하나의 status를 보고한다:
   DONE | CONCERNS | NEEDS_CONTEXT | BLOCKED.
4. CONCERNS면 위험 요소를, NEEDS_CONTEXT면 필요한 정보를, BLOCKED면 블로커를
   한 문단으로 명시한다.

OUTPUT:
- implementation diff
- status: <DONE | CONCERNS | NEEDS_CONTEXT | BLOCKED>
- status 사유(해당 시)
```

### Template 2 — spec-reviewer (stage 1)

```
ROLE: Adversarial spec-compliance reviewer. 통과를 가정하지 말 것.

CONTEXT:
- Approved spec/design (acceptance 기준의 단일 출처): <spec/design>
- Implementation diff: <diff>

INSTRUCTIONS:
1. 변경을 spec과 acceptance criteria에만 비추어 검토한다. 코드 스타일/품질은
   이 단계의 범위가 아니다(stage 2).
2. 누락된 요구사항, 잘못 해석된 요구사항, 범위를 벗어난 변경을 찾는다.
3. 실패 근거를 적극적으로 탐색하고, 근거가 없을 때만 PASS를 낸다.

OUTPUT:
- verdict: PASS | CHANGES_REQUESTED
- findings: 요구사항별 (충족 / 미충족 / 모호) + 근거
- CHANGES_REQUESTED면 implementer가 처리할 구체적 변경 목록
```

### Template 3 — code-quality-reviewer (stage 2, stage 1 PASS 후에만)

```
ROLE: Adversarial code-quality reviewer. spec-compliance가 PASS한 변경에만 실행.

PRECONDITION: stage 1 spec-compliance = PASS. (아니면 실행하지 않는다.)

CONTEXT:
- Implementation diff: <diff>
- Spec-review verdict (PASS): <stage 1 결과>

INSTRUCTIONS:
1. 정확성, edge case, 에러 처리, 테스트 충분성, 유지보수성을 검토한다.
2. 문서화된 repository standards가 있으면 해당 기준 위반을 별도로 표시한다.
3. 요구사항 충족 여부는 stage 1에서 확정되었으므로 재심하지 않는다.
4. 실패 근거를 적극적으로 탐색하고, 근거가 없을 때만 PASS를 낸다.

OUTPUT:
- verdict: PASS | CHANGES_REQUESTED
- findings: 영역별(정확성 / edge case / 테스트 / 유지보수성 / documented standards) + 근거
- CHANGES_REQUESTED면 implementer가 처리할 구체적 변경 목록
```

## Fix and Re-Review 루프

- 어느 단계든 `CHANGES_REQUESTED`면 implementer 템플릿으로 돌아가 findings를
  처리한다.
- 수정된 변경은 **항상 stage 1(spec-compliance)부터** 다시 진입한다. stage 2만
  부분 재실행하지 않는다.
- 두 단계가 모두 PASS하거나 caller가 마일스톤을 중단할 때까지 루프한다.

## 상태

- **status**: deprecated
- **runtime_implemented**: false
- **installable**: false
- **adapter_output_policy**: fail-if-selected
- 이 workflow는 역사 기록이며 런타임 엔진에서 실행되지 않고, 어떤 adapter target에도
  출력되지 않습니다.

## Non-Goals

- workflow runner 아님.
- 자동 multi-agent 실행 루프 아님.
- 강제 게이트 아님. 어떤 execution skill도 이 카드를 선택 호출하지 않는다.
- adapter output 없음(`fail-if-selected`).
- branch / commit / push / PR 조작 없음.
- static 카드만으로 runtime 성공 주장 없음.

## 현재 경계

- 새 user-facing 호출은 없다.
- 이 순차 review 규칙은 현재 실행 policy가 아니라 보존된 역사 정보다.
- 이 기록만으로 runtime support 또는 실행 권한을 주장하지 않는다.
