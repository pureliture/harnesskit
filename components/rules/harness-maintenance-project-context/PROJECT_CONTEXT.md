## HarnessKit

이 프로젝트에는 HarnessKit `harness-maintenance` profile이 project-level로 설치되어 있다.

## SoT First

- 파일 변경은 승인된 requirements → blueprint → canonical → adapter 순서로 진행한다.
- Requirements와 blueprint를 먼저 갱신하고, canonical source를 반영한 뒤 adapter output을 갱신한다.
- Canonical source보다 adapter output을 먼저 수정하거나 생성 결과를 source of truth로 취급하지 않는다.

## Verification Before Completion

- 작업 완료를 보고하기 전에 그 주장을 뒷받침하는 evidence를 확보한다.
- 실행하지 않은 test, build, dry-run, validation을 통과한 것처럼 말하지 않는다.
- 검증이 실패했거나 일부만 실행됐으면 실제 상태와 미검증 범위를 그대로 보고한다.
- Runtime 성공, route 신뢰성, hook 실행, adapter support는 직접 runtime evidence로 확인하기 전까지 주장하지 않는다(`strict_runtime_truth`).

## Merge / CI Gate

- `main` 또는 `master`로 merge하거나 push하기 전에 required CI가 green인지 live 상태를 확인한다.
- Required CI가 failed, pending, cancelled, unknown이면 merge하거나 push하지 않는다.

## Safe Worktrees

- `main` 또는 `master` checkout에서는 파일을 직접 변경하지 않는다.
- 변경은 dedicated branch와 dedicated worktree에서 수행한다.
- Worktree나 branch cleanup 전에는 fresh `git status`, `git worktree list`, merge ancestry를 확인한다.
- Stale ledger나 과거 보고만 근거로 worktree 또는 branch를 정리하지 않는다.
