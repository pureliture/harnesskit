# Harness Evaluator Requirements

## Goal
HarnessKit 하네스 평가 체계의 top-level 총괄자는 하네스 평가 에이전트 1개로 둔다. 이 에이전트는 E2E 테스트 스킬/도구, static validation, runtime proof, report generation, fixture isolation, profile boundary 검증을 조율하는 orchestrator여야 한다.

이 요구사항은 agent monolith를 요구하지 않는다. 평가 agent는 작은 tools, skills, runners, validators, reporters를 호출해 평가를 조립해야 하며, 하위 실행 단위의 최종 component kind와 배치는 blueprint 단계에서 결정한다.

기존 `skill-evaluation`은 gate/report 절차와 판정 기준으로 재사용한다. 단, `skill-evaluation`은 신규 평가 체계의 top-level runner가 아니며, 하네스 평가 agent가 필요 시 호출하거나 참조하는 평가 절차로 취급한다.

## Source Mode
`research_requested`

사용자가 공개 OSS reference packet을 요구사항에 반영하도록 요청했고, 현재 문서는 사용자 제공 의도, 로컬 reference packet 요약, 공개 OSS reference 요약을 함께 반영한다. 추가 웹 조사나 새 reference 품질 평가는 수행하지 않는다.

## Inputs And References
사용자 제공 사실:

- 하네스 평가 에이전트 1개가 top-level 총괄자다.
- 평가 agent는 E2E 테스트 스킬/도구, static validation, runtime proof, report generation, fixture isolation, profile boundary 검증을 조율한다.
- 평가 agent는 작은 tools/skills/runners를 호출하는 orchestrator이며 monolith가 아니다.
- 기존 `skill-evaluation`은 gate/report 절차로 재사용하되 top-level runner가 아니다.
- profile이 다르면 해당 profile에 없는 skill은 설치/복사되면 안 된다.
- `optimal-response`가 `harness-maintenance` E2E workspace에 나타나는 것은 fixture/profile-boundary contamination으로 실패해야 한다.
- 평가 agent는 source, canonical, component files를 자동 수정하지 않는다.
- output allowlist는 report, log, json, html 등 평가 산출물로 제한한다.
- static proof와 runtime proof는 분리해야 한다.

로컬 reference packet:

- `sources/harness-evaluation-reference.yml`: `skill-evaluation`, runtime probe, Codex app-server probe, smoke test 패턴 요약.
- `sources/harness-evaluation-oss-reference.yml`: OpenAI Evals, promptfoo, AgentBench, SWE-bench 요약.

공개 OSS reference packet 요약:

- Inspect AI: Task, Solver, Scorer, Sandbox, Eval Log 분리를 통해 평가 대상, 실행자, 채점자, 격리 환경, 로그를 구분하는 패턴을 참고한다.
- SWE-bench harness: 실제 코드 변경 검증을 fixture/runtime 환경과 gold test 결과로 판정하는 패턴을 참고한다.
- promptfoo: assertions, CI 연동, report 생성 패턴을 참고한다.
- DeepEval: pytest-like 평가 테스트 구조와 assertion 중심 평가 패턴을 참고한다.
- Langfuse: trace와 score를 분리해 관측 로그와 평가 점수를 혼동하지 않는 패턴을 참고한다.
- OpenAI Evals: system-under-test adapter를 통해 평가 프레임워크와 대상 시스템을 분리하는 패턴을 참고한다.
- AgentBench: interactive environments 기반 agent 평가에서 환경 상태와 agent action을 분리하는 패턴을 참고한다.

출처, license, runtime support는 이 요구사항 문서에서 승인된 것으로 간주하지 않는다. reference는 설계 참고 패턴이며, 복사 허용 여부와 adapter/runtime 지원 여부는 별도 검증 대상이다.

## Must-Have Requirements
1. Top-level 평가 agent는 단일 총괄 진입점이어야 하며, 평가 실행 계획 수립, 하위 runner 호출, 증적 수집, 판정 집계, report 생성을 조율해야 한다.

2. 평가 agent는 monolith가 아니어야 한다. E2E 실행, static validation, runtime proof 수집, report generation, fixture isolation, profile boundary 검증은 분리 가능한 하위 실행 단위로 호출되어야 한다.

3. 기존 `skill-evaluation`은 요구사항 coverage, blueprint conformance, source/provenance, body quality, boundary safety, adapter static proof, runtime truth, regression prompts 같은 gate/report 절차로 재사용해야 한다.

4. `skill-evaluation` 자체를 신규 top-level runner로 취급하면 안 된다. 신규 평가 agent가 top-level orchestration 책임을 가진다.

5. static proof와 runtime proof는 입력, 실행, 증적, 판정 필드가 분리되어야 한다. static proof 통과가 runtime proof 통과로 기록되면 안 되고, runtime proof 유예가 static proof 실패를 가리면 안 된다.

6. static validation은 registry, requirements, blueprint, provenance, profile closure, install-plan/profile 출력 범위, adapter static output의 일관성을 검증할 수 있어야 한다.

7. runtime proof는 격리 fixture 또는 임시 workspace에서만 수행되어야 하며, 실제 사용자 repo나 source/canonical/component files를 평가 대상 agent가 자동 수정하면 안 된다.

8. profile boundary 검증은 선택된 profile에 포함되지 않은 skill, agent, hook, workflow, rule, command가 평가 fixture나 E2E workspace에 설치/복사되지 않았음을 확인해야 한다.

9. `harness-maintenance` E2E workspace에서 `optimal-response` skill 또는 그 adapter output이 나타나면 profile-boundary contamination으로 실패 판정해야 한다.

10. 평가 agent의 쓰기 allowlist는 평가 산출물로 제한해야 한다. 허용 산출물은 evaluation report, log, JSON, JSONL, HTML report, sanitized runtime evidence, fixture-local metadata로 제한한다.

11. 평가 agent는 `sources/**`, `components/**`, `components/registry.yml`, `profiles/**`, `scripts/**`, `dist/**`, `.agents/**`, `.claude/**` 같은 source/canonical/adapter/generated install surfaces를 자동 수정하면 안 된다.

12. 평가 결과는 최소 `PASS`, `PASS_WITH_DEFERRED`, `NEEDS_WORK`, `BLOCKED` verdict를 표현해야 하며, verdict는 gate별 증적과 결함 목록에서 재현 가능해야 한다.

13. 평가 report는 static result, runtime result, skipped/deferred proof, profile contamination findings, output allowlist 위반, required fixes를 분리해 기록해야 한다.

14. E2E 평가 fixture는 deterministic setup/teardown을 가져야 하며, fixture 외부 파일 변경 여부를 감지할 수 있어야 한다.

15. Runtime/E2E 테스트는 각 provider의 승인된 lightest 모델만 사용해야 한다. Codex 계열은 mini/nano class, Claude 계열은 haiku class를 기본 허용군으로 두며, provider별 lightest model이 명시되지 않았거나 더 비싼 기본 모델로 fallback되면 실행 전 실패해야 한다.

16. 평가 gate는 단순 pass/fail 목록이 아니라 재현 가능한 evidence checklist여야 한다. 최소 gate는 requirements coverage, blueprint conformance, source/provenance, static proof, fixture isolation, profile closure, profile contamination, output allowlist, model/budget, runtime proof, report integrity, regression-negative canary를 분리해야 한다.

17. 공개 reference에서 차용하는 것은 구조적 패턴으로 제한해야 하며, code/content 복사, license approval, vendor runtime support 주장을 요구사항에서 확정하면 안 된다.

## Should-Have Requirements
1. 평가 agent는 대상 범위를 component 단위, profile 단위, workflow 단위, full E2E 단위로 선택할 수 있어야 한다.

2. E2E runtime proof는 live model/API 비용 상한, timeout, process cleanup, max step budget, sanitized event capture를 지원해야 한다.

3. report generation은 machine-readable JSON과 human-readable Markdown 또는 HTML을 함께 낼 수 있어야 한다.

4. fixture isolation은 임시 디렉터리, 임시 runtime home, explicit allowlist, forbidden path watcher를 조합해 오염을 탐지해야 한다.

5. profile boundary 검증은 positive fixture와 negative fixture를 모두 포함해야 한다. 예를 들어 `harness-maintenance` profile에 기대 component는 존재해야 하고, `optimal-response` 같은 비포함 component는 존재하면 실패해야 한다.

6. runtime trace와 score/verdict는 별도 구조로 저장해야 한다. trace 존재만으로 score 통과를 의미하면 안 된다.

7. CI에서 사용할 수 있도록 non-interactive 실행과 deterministic exit code를 제공해야 한다.

8. Provider별 lightest model mapping은 평가 설정에 명시되어야 하며, CI/local 실행 모두 같은 budget policy를 사용해야 한다.

## Non-Goals
1. 요구사항 단계에서 하위 component kind, 파일 배치, registry id, profile 배치, adapter target을 결정하지 않는다.

2. 평가 agent는 발견한 결함을 자동 수정하지 않는다.

3. 평가 agent는 source markdown, canonical component files, registry, profile, adapter output, install plan을 자동 편집하지 않는다.

4. 평가 체계는 공개 OSS reference의 code/content를 복사하지 않는다.

5. 이 문서는 reference source quality, license approval, runtime support, adapter support를 승인하지 않는다.

6. 원격 SaaS 평가 인프라나 장기 실행 benchmark farm 구축은 현재 요구사항 범위가 아니다.

## Safety And Boundary Rules
1. 평가 실행은 기본적으로 read-only source inspection과 fixture-local output writing으로 제한해야 한다.

2. output allowlist 밖의 파일 생성, 수정, 삭제가 감지되면 평가를 실패 처리해야 한다.

3. fixture setup은 실제 repo source를 오염시키지 않아야 하며, profile별 install/apply 결과는 fixture 내부에서만 검증해야 한다.

4. profile boundary contamination은 safety failure다. 특히 `harness-maintenance` E2E workspace에서 `optimal-response`가 보이면 실패해야 한다.

5. static proof와 runtime proof의 evidence namespace를 분리해야 한다. 하나의 proof artifact가 다른 proof type의 성공으로 재사용되면 안 된다.

6. runtime proof는 secret 값, raw environment dump, private path 전체 목록, credential-like string을 report/log에 남기면 안 된다.

7. 평가 agent는 destructive git 명령, 자동 commit, 자동 PR, 자동 merge, 자동 cleanup을 수행하면 안 된다.

8. live runtime proof는 timeout, budget, process cleanup, failure artifact capture를 가져야 하며, 실패 시 source/canonical 파일을 고치지 않고 report로 종료해야 한다.

9. live runtime proof는 provider별 lightest model allowlist, max step/call budget, max effort, timeout을 실행 전에 검증해야 한다.

## Evidence Expectations
1. Requirements evidence: 이 파일은 하네스 평가 agent의 approved requirements SoT로 사용되어야 한다.

2. Static proof evidence: registry/profile/blueprint/provenance/install output closure 검증 결과는 runtime proof와 별도 파일 또는 별도 report section에 기록되어야 한다.

3. Runtime proof evidence: fixture id, runtime target, command summary, sanitized event log path, timeout/budget result, process cleanup result, verdict를 기록해야 한다.

4. Profile boundary evidence: 선택 profile, expected included artifacts, forbidden artifacts, fixture path, actual installed/copied artifacts, contamination verdict를 기록해야 한다.

5. Negative evidence: `harness-maintenance` fixture에서 `optimal-response`가 발견되는 경우 fail fixture로 보존되어야 한다.

6. Report evidence: final report는 verdict, gate result, static proof, runtime proof, profile boundary result, findings, deferred items, required fixes, generated artifact list를 포함해야 한다.

7. Output allowlist evidence: 평가 실행 후 생성/수정된 파일 목록을 수집하고 allowlist 위반 여부를 판정해야 한다.

8. Source uncertainty evidence: reference URL, observed ref, license field, copied-content policy, runtime support 여부는 승인된 사실이 아니라 검증 대기 metadata로 남겨야 한다.

9. Model budget evidence: provider, selected model, allowed lightest model class, effort, timeout, live call ceiling, rejection reason을 기록해야 한다.

## Open Questions
현재 blueprint 진행을 막는 open question은 없다.

다만 blueprint 단계에서 다음 사항을 결정해야 한다:

- top-level 평가 agent가 호출할 하위 tools/skills/runners의 component kind와 경계.
- static validator와 runtime runner의 입력/출력 schema.
- 평가 산출물의 기본 저장 경로와 cleanup 정책.
- CI와 local interactive 실행의 기본 budget/timeout 값과 provider별 lightest model allowlist.

## Approval Status
approved-for-blueprint
