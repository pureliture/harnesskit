# Worktree Lifecycle

Use this skill when starting, resuming, pausing, handing off, finishing, or reviewing cleanup for branch/worktree-based repository work.

This skill is tool-neutral. It may be used by Claude Code, Codex, Antigravity, or another coding tool, but it does not claim runtime support for any target. Treat git as truth. Treat `.worktrees/ledger.json` as ignored local operational state only.

## Non-Negotiable Invariants

- Keep `main` and `master` checkout states clean.
- Create, modify, and delete task files only inside a task branch and dedicated linked worktree.
- Never use ledger data as the only authority for handoff, merged status, or cleanup.
- Never create or update `.worktrees/ledger.json` unless the repository policy confirms it is ignored local operational state.
- Never store secrets, credentials, full environment dumps, raw private command logs, or secret-like field names in the ledger.
- Never stash, reset, revert, delete branches, delete worktrees, clean files, merge, or push as part of rescue or cleanup without explicit user approval and fresh git-truth verification.

## Required Git-Truth Snapshot

Before trusting a ledger record or changing lifecycle state, collect current evidence from the relevant repository root:

1. Current path and repository root.
2. Current branch and detached-HEAD status.
3. `git status --porcelain=v1 -b` for the source checkout and task worktree.
4. `git worktree list --porcelain`.
5. Branch existence for task branch and base branch.
6. Current `HEAD` for source checkout, task worktree, task branch, and base branch when available.
7. Merge-base or ancestry proof for cleanup or merged-state review.
8. Confirmation that `.worktrees/` is ignored before using `.worktrees/ledger.json`.

When a native worktree tool is available, prefer it for creation and selection. When falling back to manual `git worktree add`, confirm the project-local worktree directory is ignored before creating a worktree.

## Worktree Location

An existing task worktree registered by git may be reused at its current path after the required
git-truth snapshot verifies its worktree, branch, and repository identity. Reuse does not require
migrating it to the new-worktree default.

When creating a worktree and the user did not supply a path, use exactly
`<canonical-source-root>/.worktrees/<task-slug>`. Resolve `canonical-source-root` as the source
checkout associated with the common git directory, not the top-level of the linked worktree from
which the command was invoked. Confirm that `<canonical-source-root>/.worktrees/` is ignored before
creation.

Apply this same location rule whether a native worktree tool performs creation or the flow falls
back to manual `git worktree add`. A sibling path matching `<repo>.worktrees/**` is not an implicit
default.

## Lifecycle States

Use only these states:

- `active`: a clean source checkout and selected or created task worktree are ready for work.
- `paused`: work is intentionally stopped and current dirty state is either clean or explicitly reported.
- `handoff`: another tool or human should continue after fresh git-truth revalidation.
- `blocked`: safety or verification failed.
- `ready_to_merge`: work is complete and required checks have passed, but merge proof does not yet exist.
- `merged`: merge or equivalent ancestry proof exists.
- `cleanup_candidate`: branch is merged, worktree is clean, path exists, branch/path verification is fresh, and deletion would not discard unmerged work.
- `abandoned`: explicit user decision to abandon, with cleanup still blocked until git safety checks pass.

## Start Or Resume

1. Identify whether the current location is a normal checkout, linked worktree, submodule, or detached HEAD.
2. If the source checkout is `main` or `master`, verify it is clean before any write.
3. If the source checkout is dirty, stop and run dirty-state rescue.
4. Select an existing task worktree or create a dedicated task branch/worktree.
5. Recheck status and worktree list after selection or creation.
6. Read ledger data only after git truth is collected. If ledger and git truth disagree, trust git truth and classify as `blocked` or `handoff`.
7. Mark the lifecycle `active` only when the source checkout is clean and the task worktree/branch identity has been verified.

## Dirty-State Rescue

Dirty source checkout means no new task writes may start.

Report:

- repository root and current path;
- current branch or detached-HEAD status;
- changed file summary from sanitized status;
- whether the dirty checkout is `main`, `master`, another normal branch, or a linked worktree;
- preservation options that require user approval.

Allowed preservation options:

- leave changes in place and record a handoff note;
- user-approved rescue branch or commit;
- user-approved labeled stash only after the user sees and chooses it over the safer rescue-branch
  option;
- user cleans up manually, then asks for revalidation.

After the user resolves the dirty state, collect a fresh git-truth snapshot before continuing. Do not run stash, reset, revert, cleanup, branch deletion, or worktree deletion automatically.

## Ledger Read Rules

The ledger is a cache and handoff layer. It is stale for cleanup review when any condition is true:

- `last_verified_at` is missing or older than 15 minutes;
- `last_verified_head` differs from current git truth;
- recorded repository root, worktree path, branch name, or base branch differs from current git truth;
- `record_version` is missing;
- the worktree path or branch is missing;
- branch ancestry cannot be verified;
- a previous write conflict was recorded.

If stale, do not classify as `cleanup_candidate`. Rescan git truth and classify as `blocked` or `handoff` until verification is fresh.

## Ledger Write Rules

Ledger writes must use record-version compare-and-swap or equivalent conflict detection.

Write flow:

1. Read the current record and note `record_version`.
2. Revalidate git truth.
3. Prepare the updated record with incremented `record_version`, fresh `updated_at`, `last_verified_at`, `last_verified_head`, `writer_tool`, and `writer_session`.
4. Write only if the stored version still matches the version read in step 1.
5. If the version changed, do not auto-merge. Classify as `blocked` or `handoff`, preserve both summaries, and ask the next operator to revalidate.

Ledger records must conform to `schemas/worktree-ledger.schema.json`. Policy details live in `policy/worktree-lifecycle-policy.md`.

## Handoff

For cross-tool handoff, produce a short, sanitized handoff note:

- repository root;
- task id or slug;
- worktree path;
- branch and base branch;
- lifecycle state;
- last verified commit/head;
- dirty or risk summary;
- checks already run;
- next expected action.

The receiving tool must ignore any runtime-specific confidence claim and repeat git-truth revalidation before writing files, merging, or cleanup review.

## Finish And Cleanup Review

Use `ready_to_merge` only after required checks pass and remaining work is clear.

Use `merged` only when merge or equivalent ancestry proof exists.

Use `cleanup_candidate` only when all are true:

- source checkout is clean;
- task worktree is clean;
- task branch exists or its merged/deleted status is explained by fresh git truth;
- base branch exists;
- ancestry or merge-state proof shows task commits are merged into the intended base/source branch;
- worktree path exists and matches the record;
- verification is fresh for cleanup review;
- ledger has no missing version token, stale verification, or write conflict.

If unmerged commits exist, ancestry check fails, the worktree is dirty, the branch is divergent, or verification is stale, deletion is blocked and state becomes `blocked` or `handoff`.

## Testing And Proof Expectations

Static checks should verify:

- schema accepts a minimal valid record and rejects missing required fields, invalid states, invalid timestamps, missing version tokens, unknown top-level fields, and forbidden secret/env/log fields;
- policy text covers main-clean, worktree-only writes, dirty rescue, stale ledger, compare-and-swap conflict, cleanup guard, no-secret ledger, and cross-tool handoff;
- provenance links the policy and schema to approved requirements.

Hermetic git fixture checks should use temporary repositories, not the real repository. Fixtures should cover clean start, dirty source checkout, linked worktree, submodule guard, detached HEAD, missing worktree, missing branch, divergent branch, unmerged commits, merged clean cleanup candidate, stale ledger, write conflict, and forbidden ledger data.

Each non-runtime proof artifact should be small and sanitized. Include fixture id, repository root under test, expected and actual lifecycle state, sanitized git status, sanitized worktree list, relevant rev-parse and ancestry result, ledger schema validation result, stale/conflict decision, no-secret validation result, and command exit statuses without raw environment dumps or secret values.

Runtime proof for Claude Code, Codex, Antigravity, or cross-tool invocation is deferred to adapter/runtime evaluation.
