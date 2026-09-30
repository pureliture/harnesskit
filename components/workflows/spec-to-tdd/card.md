# Historical Workflow Record: spec-to-tdd

`harnesskit.workflow.spec-to-tdd`는 과거의 requirements-to-spec-to-TDD 순서를 남긴
역사 기록입니다. 이 카드는 실행 엔진, adapter target, 또는 사용자-facing entrypoint가
아닙니다.

## 지금은 무엇을 하지 않는가

- TDD를 선택·시작·승인하지 않는다.
- requirements, design, plan, agent dispatch, 또는 implementation을 자동으로 시작하지 않는다.
- 운영 작업을 TDD, Python helper, test harness, 또는 RED cycle로 바꾸지 않는다.
- 설치, discovery, runtime support를 주장하지 않는다.

현재 TDD는 사용자가 명시적으로 선택했거나 이미 알려진 repository의 필수 policy가 있을 때만
시작할 수 있다. 이 역사 카드는 그 authority를 제공하지 않는다.

## 남아 있는 단계 목록의 의미

`workflow.yml`의 단계 목록은 과거 구조를 이해하고 기존 mode-policy reference를 추적하기 위한
문서다. runner가 읽어 실행할 지시나 새로운 작업의 routing rule이 아니다.

## 상태

- **status**: draft historical record
- **runtime_implemented**: false
- **installable**: false
- **adapter_output_policy**: fail-if-selected
- **invocation_policy**: historical-record-only

이 기록만으로 TDD나 어떤 실행 경로도 활성화되지 않는다.
