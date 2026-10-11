# Wayfinder

Wayfinder는 사용자가 명시적으로 선택했고, 여러 session에 걸친 evidence와 user-owned decision이 서로
다음 선택을 막을 때만 쓴다. 해야 할 일을 실행하는 skill이 아니라, 먼저 확인할 것과 멈출 조건을 짧은 map으로
보여 주는 skill이다.

## Map Admission

먼저 현재 대화와 제공된 evidence를 읽는다. 다음 세 가지가 모두 맞을 때만 map을 만든다.

1. 한 session에서 닫을 수 없는 복수의 evidence gap 또는 user-owned decision이 있다.
2. 그 gap과 decision의 순서나 결과가 서로 다음 선택을 막는다.
3. 각 evidence task가 여는 결정과 `decision-ready` 종료 조건을 말할 수 있다.

어려운 요청, 긴 설명, 여러 repository, 여러 후보만으로는 충분하지 않다. 이 조건이 하나라도 없으면 map을
만들지 않는다. 빠진 dependency 또는 decision을 구체적으로 말하고 control을 사용자에게 반환한다. 다른 skill을
추천하거나 호출하지 않는다.

## Build The Smallest Useful Map

1. **확인된 것부터 분리한다.** supplied evidence를 `confirmed fact`, `closed user decision`, `unknown`,
   `access gap`으로 나눈다. 모르는 내용을 추측으로 채우지 않는다.
2. **막힌 선택만 적는다.** 각 unresolved blocker에는 필요한 evidence와 그 결과가 열어 주는 결정 하나를
   함께 적는다. 현재 결정을 바꾸지 않는 조사, 모든 solution class, 미래 운영 요구는 넣지 않는다.
3. **다음 frontier를 짧게 적는다.** 지금 확인할 수 있는 evidence와 아직 기다려야 하는 blocker를 구분한다.
4. **끝을 정한다.** 사용자가 다음 선택을 할 만큼 evidence가 모이면 `decision-ready`로 끝낸다. access가
   막혔으면 `blocked`, 최신 지시가 미뤘으면 `deferred`, 중단되었으면 `closed`로 끝낸다.

Map은 다음 정보만 가진 conversation-local 정리다.

```md
## Wayfinder Map

- Objective:
- Confirmed facts:
- Closed user decisions:
- Unresolved blockers:
  - Needed evidence → decision it unlocks
- Current frontier:
- Out of scope:
- Terminal condition: decision-ready | blocked | deferred | closed
```

## Return Control

- evidence task는 실제 결정을 하나 열 때만 이름을 적는다. research, prototype, operation, source mutation,
  purchase, requirements 작성 또는 implementation을 자동 실행하지 않는다.
- `decision-ready`는 solution이 정해졌다는 뜻이 아니다. 사용자가 다음 결정을 할 수 있다는 뜻이다.
- 최신 `stop`, `defer`, `operation-first`, `no-implementation`, scope reduction, option exclusion은 앞선 map이나
  미승인 evidence task보다 우선한다.
- 새 evidence가 승인된 requirements나 design과 충돌하면 조용히 바꾸지 않는다. 충돌을 밝히고 reapproval이
  필요하다고 말한 뒤 control을 반환한다.

## Boundaries

- Intent Guide의 entry recommendation이나 어떤 다른 user-facing skill의 route를 소유하지 않는다.
- solution option을 구매, 채택, custom build로 고르거나 새 비교를 시작하지 않는다.
- 질문 workflow, tracker, file, issue, database, queue, scheduler, route history 또는 persistent map을 만들지
  않는다.
- target discovery, adapter support, installed behavior, runtime success를 주장하지 않는다.
