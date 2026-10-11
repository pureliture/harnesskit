# Agentic Execution

Use this contract for a long implementation goal when the user explicitly chooses the AI-autopilot authority for
requirements-preserving design amendments. It is not a default for a generic request and it never changes the approved
goal, non-goals, or user-owned boundaries.

## Admission

1. Confirm four separate facts before changing code: approved requirements, approved design, the user's latest explicit
   implementation authorization, and an explicit `agentic-execution` choice for this goal.
   - If a generic request only says “implement the approved design,” briefly explain the two choices: Agentic may decide a
     requirements-preserving design amendment; Guided stops for the user's decision. Ask the user to choose one.
   - Never select Agentic from token budget, design detail, prior habit, or a hidden mode. The selected contract stays
     fixed until the user explicitly changes it. Do not default or switch contracts silently.
2. Honor the latest user instruction immediately. `stop`, `shrink`, `defer`, `operation first`, `do not implement`, and
   `no TDD` narrow or stop this work even when an earlier plan says otherwise. If it conflicts with approved SoT, surface
   the conflict for re-approval; do not silently rewrite either source.
3. Do not admit a known operation: Nomad task, deploy, smoke check, status query, diagnosis, or one-off recovery stays
   with its existing operation procedure and is verified by appropriate before/after runtime state. Do not make a
   Python helper, queue, runner, test harness, or RED cycle before it.
4. Do not admit a clear small regression with a known cause and bounded fix. Keep it with the direct fix owner and the
   smallest risk-appropriate proof. If an existing operation tool is insufficient, stop and reclassify the newly found
   code need upstream; do not invent a helper here.

## Work One Observable Slice

1. Split an admitted goal into observable end-to-end vertical slices, not separate domain, DTO, adapter, repository,
   or test milestones. Keep exactly one slice active.
2. For a long goal, keep a simple `milestones.md` that links the approved requirements and design, names this selected
   contract, lists slice status and observable result, records the active slice and its evidence, records approved
   amendments or pending decisions, and states the next action or stop reason. Do not create a schema, parser, codec,
   repository, or validator for this file.
3. Implement only the active slice, obtain evidence that its observable result works, then update the state before
   choosing the next slice. Evidence may shrink, reorder, or defer remaining slices when the approved goal remains the
   same.

## Decide A Requirements-Preserving Amendment

You may decide a design amendment only when an observed fact blocks the active slice while the required behavior and
non-goals remain unchanged. A prediction about future scale, reliability, or platform needs is not enough.

1. State the observed blocker and the behavior it prevents.
2. Compare only viable alternatives in the current context: reuse, configuration, managed solution, open source,
   custom build, and defer. Do not turn this into automatic market research; name an unavailable or boundary-crossing
   option honestly.
3. Choose the smallest alternative that reaches the current observable result. Do not prepay for a distributed
   platform, queue, scheduler, recovery system, or compatibility path that the slice does not need.
4. Name the canonical owner, operation responsibility, any legacy path's end condition and deletion condition, then run
   or obtain the smallest end-to-end evidence that can challenge the choice. If it does not confirm the required
   observable result, reassess the viable alternatives or stop; do not continue on a planned-evidence claim.
5. Only after that evidence confirms the result, record the reason, design amendment, and evidence in the design and
   `milestones.md`, then continue the active slice. This gate does not require a human approval at every milestone.

## Stop At Authority Boundaries

Stop and return to the needed requirement or user approval before deciding any change to the user goal, required
behavior, non-goal, paid-service/cost boundary, privacy, data handling, trust, security, legal boundary, breaking
public contract, irreversible production action, or user-owned product/policy/ownership decision. Show the observed
evidence and material impact; do not relabel it as an internal amendment.

## Evidence And Completion

- Use the smallest evidence that addresses the actual slice risk. A separately selected TDD, Testing Strategy, review,
  worktree, CI, or operation owner supplies its own procedure; this skill neither starts nor copies it.
- At slice and goal completion, separately report responsibilities added, deleted or reused, remaining compatibility
  paths and end conditions, obsolete tests considered or removed, remaining test gaps, direct evidence, unverified
  range, and work deferred outside approval.
- Static wording or generated output is not evidence that a target installed, selected, or ran this skill.
