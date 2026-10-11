# Worktree Lifecycle Policy

This policy defines the canonical meaning of worktree lifecycle records owned by `harnesskit.skill.worktree-lifecycle`.

## Authority

- Git truth is authoritative.
- `.worktrees/ledger.json` is ignored local operational state only.
- The ledger is a cache and cross-tool handoff layer.
- A ledger record never proves that a branch is merged, a worktree is safe to delete, or a checkout is clean.

## Main-Clean And Worktree-Only Writes

- `main` and `master` checkouts must remain clean.
- Task writes must occur only inside a dedicated task branch and linked worktree.
- If the source checkout is dirty, new task writes are blocked until dirty-state rescue is complete and git truth is revalidated.

## Worktree Location

- A worktree already registered by git may be reused at its existing path after fresh git-truth
  verification confirms its worktree, branch, and repository identity. It does not need to be
  migrated to the new-worktree default.
- When creating a worktree without a user-supplied path, use exactly
  `<canonical-source-root>/.worktrees/<task-slug>`.
- Resolve `canonical-source-root` as the source checkout associated with the common git directory,
  not the top-level of the invoking linked worktree.
- Confirm that `<canonical-source-root>/.worktrees/` is ignored before creation.
- Native worktree creation and manual `git worktree add` fallback must use the same location rule.
- A sibling path matching `<repo>.worktrees/**` is not an implicit default.

## Dirty-State Rescue

When the source checkout is dirty, report the repository root, current path, branch or detached-HEAD status, and sanitized changed-file summary.

Do not mutate state without explicit user approval. Forbidden automatic actions include stash, reset, revert, clean, branch deletion, worktree deletion, merge, push, and cleanup.

Allowed rescue outcomes:

- keep changes in place and record a handoff note;
- preserve changes through a user-approved rescue branch or commit;
- preserve changes through a user-approved labeled stash only when the user explicitly chooses it
  after reviewing the safer rescue-branch option;
- wait while the user resolves the dirty state, then revalidate.

## Required Lifecycle States

Allowed lifecycle states:

- `active`
- `paused`
- `handoff`
- `blocked`
- `ready_to_merge`
- `merged`
- `cleanup_candidate`
- `abandoned`

## State Transition Rules

Allowed transitions:

| From | To | Required condition |
| --- | --- | --- |
| `active` | `paused` | Work intentionally stopped with clean or explicitly reported dirty state. |
| `active` | `handoff` | Another tool should continue after fresh git-truth revalidation. |
| `active` | `blocked` | Dirty source checkout, missing verification, divergent branch, failed ancestry, or unsafe operation. |
| `paused` | `active` | Task resumes after git-truth revalidation. |
| `paused` | `handoff` | Task ownership changes after git-truth revalidation. |
| `handoff` | `active` | Receiving tool verifies repository, worktree, and branch state. |
| `active` | `ready_to_merge` | Work is complete and required checks pass. |
| `ready_to_merge` | `merged` | Merge or equivalent ancestry proof exists. |
| `merged` | `cleanup_candidate` | Worktree is clean, branch is merged, path exists, and fresh verification is present. |
| any | `blocked` | Safety or verification failure. |
| `blocked` | `active` | Blocker resolved and git truth revalidated. |
| `blocked` | `handoff` | Human or another tool must resolve blocker. |
| any | `abandoned` | Explicit user decision, with cleanup still blocked until git safety checks pass. |

Bootstrap rule: enter `active` from no ledger record only after a clean source checkout and selected
or created task worktree are verified.

Forbidden transitions:

- Any state to `cleanup_candidate` without fresh branch ancestry and clean worktree proof.
- Any stale ledger record to `cleanup_candidate`.
- Any write-conflict record to `cleanup_candidate`.
- Any dirty source checkout to `active` without rescue resolution and revalidation.
- Any unmerged branch to `merged` or `cleanup_candidate`.

## Stale Ledger Rule

For cleanup review, a ledger record is stale when:

- `last_verified_at` is missing;
- verification is older than 15 minutes;
- current repository root, worktree path, branch name, base branch, or head differs from the record;
- `last_verified_head` differs from current git truth;
- `record_version` is missing;
- branch or worktree path is missing;
- ancestry verification fails;
- a write conflict was observed.

Stale records block cleanup. Re-scan git truth and classify as `blocked` or `handoff`.

## Compare-And-Swap Rule

Ledger writes must detect concurrent updates with `record_version`.

- Read current `record_version`.
- Revalidate git truth.
- Write an incremented version only if the stored version is unchanged.
- If the stored version changed, do not auto-merge records.
- Record or report the conflict as `blocked` or `handoff`.

## Cleanup Guard

Cleanup review requires fresh proof:

- source checkout clean;
- task worktree clean;
- branch and base branch verified or their absence explained by fresh git truth;
- worktree path verified;
- merge-base or ancestry proof shows task commits are merged into the intended base/source branch;
- ledger record has current version, current verification, and no write conflict.

Deletion is blocked when unmerged commits exist, ancestry check fails, the worktree is dirty, the branch is divergent, the path/branch is missing without explanation, or verification is stale.

## No-Secret Ledger

Ledger records must not include:

- secrets or credentials;
- secret-like field names such as password, token, credential, api key, private key, or access key;
- full environment dumps;
- raw private command logs;
- provider response bodies;
- unredacted URLs containing credentials.

Proof artifacts must be sanitized and must not include raw environment dumps or secret values.

## Cross-Tool Handoff

Cross-tool handoff records must use tool-neutral fields first: task id, repository root, worktree path, branch, base branch, lifecycle state, verified head, writer identity, and handoff note.

Tool-specific metadata must stay inside an optional extension object and must not override canonical lifecycle state or git identity fields.

The receiving tool must re-run git-truth checks before writing files, merging, or cleanup review.

## Proof Artifact Expectations

Non-runtime proof artifacts should contain:

- fixture id or scenario name;
- repository root under test;
- expected and actual lifecycle state;
- sanitized `git status --porcelain=v1 -b`;
- sanitized `git worktree list --porcelain`;
- relevant `git rev-parse` and merge-base or ancestry result;
- ledger schema validation result;
- stale or conflict decision when applicable;
- no-secret validation result;
- command exit statuses without raw environment dumps or secret values.

Runtime proof for target tools is not claimed by this policy.
