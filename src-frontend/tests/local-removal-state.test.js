import assert from "node:assert/strict";
import test from "node:test";

import {
  acceptLocalRemovalReconciliation,
  acceptLocalRemovalRescanFailure,
  acceptLocalRemovalOutcome,
  acceptLocalRemovalPlan,
  acknowledgeLocalRemovalConfirmation,
  beginLocalRemovalApply,
  beginLocalRemovalPreparation,
  beginLocalRemovalReconciliation,
  canApplyLocalRemoval,
  clearLocalRemovalSelection,
  createLocalRemovalState,
  dismissLocalRemovalConfirmation,
  enterLocalRemovalSelectionMode,
  exitLocalRemovalSelectionMode,
  isLocalRemovalSelectionMode,
  openLocalRemovalConfirmation,
  rejectLocalRemovalReconciliation,
  toggleLocalRemovalSelection,
} from "../local-removal-state.js";

function preparedState() {
  let state = createLocalRemovalState();
  state = enterLocalRemovalSelectionMode(state);
  state = toggleLocalRemovalSelection(state, "instance-1");
  state = toggleLocalRemovalSelection(state, "instance-2");
  const prepared = beginLocalRemovalPreparation(state, "snapshot-1");
  return acceptLocalRemovalPlan(prepared.state, {
    plan_id: "plan-1",
    plan_digest: "digest-1",
    eligible_members: [
      { instance_id: "instance-1", display_name: "첫 번째 정의", effect: "shared_config_entry" },
      { instance_id: "instance-2", display_name: "두 번째 정의", effect: "dedicated_file" },
    ],
    blocked_members: [{ instance_id: "instance-3", display_name: "제외된 정의", code: "unsupported" }],
    shared_config_entry_removal_count: 1,
    dedicated_file_deletion_count: 1,
    parent_folder_deletion_count: 9,
    unexpected_data: "must/not/be/stored",
  }, prepared.token);
}

test("selection mode gates opaque checkbox selection and exit clears it without a removal request", () => {
  let state = createLocalRemovalState();
  assert.equal(toggleLocalRemovalSelection(state, "instance-1"), state);
  state = enterLocalRemovalSelectionMode(state);
  assert.equal(isLocalRemovalSelectionMode(state), true);
  state = toggleLocalRemovalSelection(state, "instance-1");
  state = toggleLocalRemovalSelection(state, "instance-2");
  state = toggleLocalRemovalSelection(state, "instance-1");

  assert.deepEqual(state.selectedInstanceIds, ["instance-2"]);
  assert.equal(state.selectionGeneration, 4);
  assert.equal(clearLocalRemovalSelection(state).selectionGeneration, 5);
  assert.deepEqual(clearLocalRemovalSelection(state).selectedInstanceIds, []);
  assert.equal(exitLocalRemovalSelectionMode(state).selectionMode, "inactive");
});

test("prepare request contains only the snapshot and selected opaque instance IDs", () => {
  let state = createLocalRemovalState();
  state = enterLocalRemovalSelectionMode(state);
  state = toggleLocalRemovalSelection(state, "instance-1");
  state = toggleLocalRemovalSelection(state, "instance-2");
  const prepared = beginLocalRemovalPreparation(state, "snapshot-1");

  assert.deepEqual(prepared.request, {
    snapshotId: "snapshot-1",
    instanceIds: ["instance-1", "instance-2"],
  });
  assert.deepEqual(prepared.token, { snapshotId: "snapshot-1", selectionGeneration: 3 });
});

test("prepared plan is a safe summary and never retains raw path or source body fields", () => {
  const state = preparedState();

  assert.equal(state.phase, "ready");
  assert.equal(state.plan.effects.parentFolderDeletionCount, 0);
  assert.equal(state.plan.nonAtomicSourceGroups, true);
  assert.equal(state.plan.automaticRescan, true);
  assert.equal("rawPath" in state.plan, false);
  assert.equal("sourceBody" in state.plan, false);
  assert.doesNotMatch(JSON.stringify(state), /must\/not\/be\/stored/);
});

test("selection generation mismatch invalidates a stale prepare response without a confirmation", () => {
  let state = createLocalRemovalState();
  state = enterLocalRemovalSelectionMode(state);
  state = toggleLocalRemovalSelection(state, "instance-1");
  const prepared = beginLocalRemovalPreparation(state, "snapshot-1");
  state = toggleLocalRemovalSelection(prepared.state, "instance-2");
  const stale = acceptLocalRemovalPlan(state, {
    planId: "plan-stale",
    digest: "digest-stale",
    eligibleMembers: [{ instanceId: "instance-1", displayName: "정의" }],
  }, prepared.token);

  assert.equal(stale, state);
  assert.equal(stale.plan, null);
  assert.equal(stale.confirmation.open, false);
});

test("apply is gated by an explicit confirmation and can start only once", () => {
  const ready = preparedState();
  const confirming = openLocalRemovalConfirmation(ready);
  const acknowledged = acknowledgeLocalRemovalConfirmation(confirming, true);
  const apply = beginLocalRemovalApply(acknowledged);

  assert.equal(canApplyLocalRemoval(ready), false);
  assert.equal(canApplyLocalRemoval(confirming), false);
  assert.equal(canApplyLocalRemoval(acknowledged), true);
  assert.deepEqual(apply.request, { planId: "plan-1", planDigest: "digest-1", confirmed: true });
  assert.equal(beginLocalRemovalApply(apply.state).request, null);
  assert.equal(dismissLocalRemovalConfirmation(confirming).phase, "ready");
});

test("successful source groups stay as ordinary rows until a matching complete rescan confirms absence", () => {
  const acknowledged = acknowledgeLocalRemovalConfirmation(
    openLocalRemovalConfirmation(preparedState()),
    true,
  );
  const applying = beginLocalRemovalApply(acknowledged).state;
  const waiting = acceptLocalRemovalOutcome(applying, {
    source_group_outcomes: [
      { state: "success", instance_ids: ["instance-1"] },
      { state: "failed_unchanged", instance_ids: ["instance-2"], code: "changed_since_scan" },
    ],
    rescan_state: "accepted",
    rescan_attempt_id: "attempt-2",
    unexpected_data: "must/not/be/stored",
  });

  assert.equal(waiting.phase, "awaiting_rescan");
  assert.equal(waiting.rescan.state, "waiting");
  assert.equal(waiting.selectionMode, "inactive");
  assert.deepEqual(waiting.selectedInstanceIds, []);
  assert.doesNotMatch(JSON.stringify(waiting), /must\/not\/be\/stored/);

  assert.equal(beginLocalRemovalReconciliation(waiting, "snapshot-1", "attempt-2").request, null);
  const reconciling = beginLocalRemovalReconciliation(waiting, "snapshot-2", "attempt-2");
  assert.deepEqual(reconciling.request, {
    snapshotId: "snapshot-2",
    expectedAttemptId: "attempt-2",
    instanceIds: ["instance-1"],
  });
  const completed = acceptLocalRemovalReconciliation(reconciling.state, {
    snapshot_id: "snapshot-2",
    state: "confirmed",
    confirmed_absent_instance_ids: [],
  }, reconciling.token);
  assert.equal(completed.phase, "complete");
  assert.equal(completed.rescan.state, "confirmed");
  assert.deepEqual(completed.selectedInstanceIds, []);
  assert.equal(completed.plan, null);
});

test("matching complete reconciliation leaves interactive selection empty", () => {
  const acknowledged = acknowledgeLocalRemovalConfirmation(
    openLocalRemovalConfirmation(preparedState()),
    true,
  );
  const waiting = acceptLocalRemovalOutcome(beginLocalRemovalApply(acknowledged).state, {
    sourceGroupOutcomes: [
      { state: "success", memberIds: ["instance-1"] },
      { state: "failed_unchanged", memberIds: ["instance-2"] },
    ],
    rescan: "accepted",
    rescanAttemptId: "attempt-2",
  });
  const reconciling = beginLocalRemovalReconciliation(waiting, "snapshot-2", "attempt-2");
  const completed = acceptLocalRemovalReconciliation(reconciling.state, {
    snapshotId: "snapshot-2",
    state: "confirmed",
    confirmedAbsentInstanceIds: ["instance-1", "instance-2"],
  }, reconciling.token);

  assert.deepEqual(completed.selectedInstanceIds, []);
});

test("no successful source group does not claim or wait for an automatic rescan", () => {
  const acknowledged = acknowledgeLocalRemovalConfirmation(
    openLocalRemovalConfirmation(preparedState()),
    true,
  );
  const complete = acceptLocalRemovalOutcome(beginLocalRemovalApply(acknowledged).state, {
    sourceGroupOutcomes: [{ state: "failed_unchanged", instanceIds: ["instance-1"] }],
    rescan: "accepted",
  });

  assert.equal(complete.phase, "complete");
  assert.equal(complete.rescan.state, "not_started");
  assert.equal(complete.outcome.successfulSourceGroupCount, 0);
});

test("indeterminate source groups close selection mode and wait for authoritative reconciliation", () => {
  const acknowledged = acknowledgeLocalRemovalConfirmation(
    openLocalRemovalConfirmation(preparedState()),
    true,
  );
  const waiting = acceptLocalRemovalOutcome(beginLocalRemovalApply(acknowledged).state, {
    sourceGroupOutcomes: [
      { state: "indeterminate", instanceIds: ["instance-1"], code: "removal_state_indeterminate" },
      { state: "failed_unchanged", instanceIds: ["instance-2"], code: "changed_since_scan" },
    ],
    rescan: "accepted",
    rescanAttemptId: "attempt-2",
  });

  assert.equal(waiting.phase, "awaiting_rescan");
  assert.equal(waiting.outcome.indeterminateSourceGroupCount, 1);
  const reconciling = beginLocalRemovalReconciliation(waiting, "snapshot-2", "attempt-2");
  const completed = acceptLocalRemovalReconciliation(reconciling.state, {
    snapshotId: "snapshot-2",
    state: "confirmed",
    confirmedAbsentInstanceIds: ["instance-1"],
  }, reconciling.token);
  assert.equal(completed.phase, "complete");
  assert.deepEqual(completed.selectedInstanceIds, []);
});

test("failed or unverifiable rescans leave ordinary result rows without restoring selection mode", () => {
  const acknowledged = acknowledgeLocalRemovalConfirmation(
    openLocalRemovalConfirmation(preparedState()),
    true,
  );
  const waiting = acceptLocalRemovalOutcome(beginLocalRemovalApply(acknowledged).state, {
    sourceGroupOutcomes: [{ state: "success", memberIds: ["instance-1"] }],
    rescan: "accepted",
    rescanAttemptId: "attempt-2",
  });

  const failed = acceptLocalRemovalRescanFailure(waiting, "attempt-2");
  assert.equal(failed.phase, "complete");
  assert.equal(failed.rescan.state, "failed");
  assert.equal(failed.selectionMode, "inactive");
  assert.deepEqual(failed.selectedInstanceIds, []);

  const reconciling = beginLocalRemovalReconciliation(waiting, "snapshot-2", "attempt-2");
  const unverified = rejectLocalRemovalReconciliation(reconciling.state, reconciling.token);
  assert.equal(unverified.phase, "complete");
  assert.equal(unverified.rescan.state, "unverified");
  assert.equal(unverified.selectionMode, "inactive");
  assert.deepEqual(unverified.selectedInstanceIds, []);
});

test("partial coverage never claims success or restores selection mode", () => {
  const acknowledged = acknowledgeLocalRemovalConfirmation(
    openLocalRemovalConfirmation(preparedState()),
    true,
  );
  const waiting = acceptLocalRemovalOutcome(beginLocalRemovalApply(acknowledged).state, {
    sourceGroupOutcomes: [{ state: "success", memberIds: ["instance-1"] }],
    rescan: "accepted",
    rescanAttemptId: "attempt-2",
  });
  const reconciling = beginLocalRemovalReconciliation(waiting, "snapshot-2", "attempt-2");
  const completed = acceptLocalRemovalReconciliation(reconciling.state, {
    snapshotId: "snapshot-2",
    state: "coverage_incomplete",
    confirmedAbsentInstanceIds: ["instance-1"],
  }, reconciling.token);

  assert.equal(completed.phase, "complete");
  assert.equal(completed.rescan.state, "coverage_incomplete");
  assert.equal(completed.selectionMode, "inactive");
  assert.deepEqual(completed.selectedInstanceIds, []);
});
