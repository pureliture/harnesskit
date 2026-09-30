const REMOVAL_PHASES = new Set([
  "idle",
  "preparing",
  "ready",
  "confirming",
  "applying",
  "awaiting_rescan",
  "reconciling",
  "complete",
]);

const SUCCESSFUL_GROUP_STATES = new Set(["success", "removed", "applied"]);
const INDETERMINATE_GROUP_STATES = new Set(["indeterminate", "state_indeterminate"]);
const SELECTION_MODES = new Set(["inactive", "selecting"]);

function optionalId(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

function nonNegativeInteger(value, fallback = 0) {
  return Number.isSafeInteger(value) && value >= 0 ? value : fallback;
}

function uniqueInstanceIds(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(optionalId).filter(Boolean))];
}

function normalizeMember(value, fallbackEffect) {
  if (!value || typeof value !== "object") return null;
  const instanceId = optionalId(value.instanceId ?? value.instance_id ?? value.id);
  if (!instanceId) return null;
  return {
    instanceId,
    displayName: String(value.displayName ?? value.display_name ?? "").trim() || "이름 정보 없음",
    effect: optionalId(value.effect ?? value.effect_kind) ?? fallbackEffect,
    code: optionalId(value.code ?? value.reasonCode ?? value.reason_code),
  };
}

function normalizeMembers(value, fallbackEffect) {
  if (!Array.isArray(value)) return [];
  const members = new Map();
  value.map((member) => normalizeMember(member, fallbackEffect)).filter(Boolean).forEach((member) => {
    if (!members.has(member.instanceId)) members.set(member.instanceId, member);
  });
  return [...members.values()];
}

function normalizePlan(value, preparation) {
  if (!value || typeof value !== "object" || !preparation) return null;
  const planId = optionalId(value.planId ?? value.plan_id);
  const planDigest = optionalId(value.planDigest ?? value.plan_digest ?? value.digest);
  if (!planId || !planDigest) return null;

  const eligibleMembers = normalizeMembers(
    value.eligibleMembers ?? value.eligible_members ?? value.eligible,
    "eligible",
  );
  const blockedMembers = normalizeMembers(
    value.blockedMembers ?? value.blocked_members ?? value.blocked,
    "blocked",
  );
  return {
    planId,
    planDigest,
    snapshotId: preparation.snapshotId,
    selectionGeneration: preparation.selectionGeneration,
    eligibleMembers,
    blockedMembers,
    effects: {
      sharedConfigEntryRemovalCount: nonNegativeInteger(
        value.sharedConfigEntryRemovalCount ?? value.shared_config_entry_removal_count
          ?? value.effects?.sharedConfigEntryRemovalCount
          ?? value.effects?.shared_config_entry_removal_count,
      ),
      dedicatedFileDeletionCount: nonNegativeInteger(
        value.dedicatedFileDeletionCount ?? value.dedicated_file_deletion_count
          ?? value.effects?.dedicatedFileDeletionCount
          ?? value.effects?.dedicated_file_deletion_count,
      ),
      parentFolderDeletionCount: 0,
    },
    nonAtomicSourceGroups: true,
    automaticRescan: true,
  };
}

function normalizeGroupOutcome(value) {
  if (!value || typeof value !== "object") return null;
  const state = String(value.state ?? value.outcome ?? value.status ?? "").trim().toLowerCase();
  if (!state) return null;
  let normalizedState = "failed_unchanged";
  if (SUCCESSFUL_GROUP_STATES.has(state)) normalizedState = "success";
  else if (INDETERMINATE_GROUP_STATES.has(state)) normalizedState = "indeterminate";
  return {
    state: normalizedState,
    memberIds: uniqueInstanceIds(value.memberIds ?? value.member_ids ?? value.instanceIds ?? value.instance_ids),
    code: optionalId(value.code ?? value.reasonCode ?? value.reason_code),
  };
}

function normalizeRescan(value) {
  const state = String(value?.state ?? value?.status ?? value ?? "").trim().toLowerCase();
  if (["accepted", "started"].includes(state)) return "accepted";
  if (["queued", "queued_after_current"].includes(state)) return "queued";
  if (["failed_to_start", "failed", "unavailable"].includes(state)) return "failed";
  return "not_started";
}

function normalizeOutcome(value) {
  if (!value || typeof value !== "object") return null;
  const sourceGroupOutcomes = (Array.isArray(value.sourceGroupOutcomes ?? value.source_group_outcomes)
    ? value.sourceGroupOutcomes ?? value.source_group_outcomes
    : []).map(normalizeGroupOutcome).filter(Boolean);
  return {
    sourceGroupOutcomes,
    successfulSourceGroupCount: sourceGroupOutcomes.filter((group) => group.state === "success").length,
    indeterminateSourceGroupCount: sourceGroupOutcomes.filter((group) => group.state === "indeterminate").length,
    failedSourceGroupCount: sourceGroupOutcomes.filter((group) => group.state === "failed_unchanged").length,
    rescan: normalizeRescan(
      value.rescan ?? value.rescanState ?? value.rescan_state ?? value.rescanStatus ?? value.rescan_status,
    ),
    rescanAttemptId: optionalId(
      value.rescanAttemptId ?? value.rescan_attempt_id ?? value.rescan?.attemptId,
    ),
  };
}

function isPlanCurrent(state) {
  return state.plan !== null
    && state.plan.selectionGeneration === state.selectionGeneration
    && state.plan.snapshotId !== null;
}

function closeConfirmation() {
  return { open: false, acknowledged: false };
}

function canChangeSelection(state) {
  return !["applying", "awaiting_rescan", "reconciling"].includes(state.phase);
}

function selectionMode(value) {
  return SELECTION_MODES.has(value) ? value : "inactive";
}

export function isLocalRemovalSelectionMode(state) {
  return selectionMode(state?.selectionMode) === "selecting";
}

export function enterLocalRemovalSelectionMode(state) {
  if (isLocalRemovalSelectionMode(state) || !["idle", "complete"].includes(state.phase)) return state;
  return {
    ...state,
    selectionMode: "selecting",
    selectedInstanceIds: [],
    selectionGeneration: state.selectionGeneration + 1,
    phase: "idle",
    preparation: null,
    plan: null,
    confirmation: closeConfirmation(),
    outcome: null,
    rescan: { state: "idle", snapshotId: null, attemptId: null },
  };
}

export function exitLocalRemovalSelectionMode(state) {
  if (!isLocalRemovalSelectionMode(state) || !canChangeSelection(state)) return state;
  return {
    ...state,
    selectionMode: "inactive",
    selectedInstanceIds: [],
    selectionGeneration: state.selectionGeneration + 1,
    phase: "idle",
    preparation: null,
    plan: null,
    confirmation: closeConfirmation(),
  };
}

export function createLocalRemovalState(overrides = {}) {
  const selectedInstanceIds = uniqueInstanceIds(overrides.selectedInstanceIds);
  const selectionGeneration = nonNegativeInteger(overrides.selectionGeneration);
  const phase = REMOVAL_PHASES.has(overrides.phase) ? overrides.phase : "idle";
  return {
    selectionMode: selectionMode(overrides.selectionMode),
    selectedInstanceIds,
    selectionGeneration,
    phase,
    preparation: null,
    plan: null,
    confirmation: closeConfirmation(),
    outcome: null,
    rescan: { state: "idle", snapshotId: null, attemptId: null },
  };
}

export function toggleLocalRemovalSelection(state, instanceId) {
  const normalizedId = optionalId(instanceId);
  if (!normalizedId || !isLocalRemovalSelectionMode(state) || !canChangeSelection(state)) return state;
  const selected = new Set(state.selectedInstanceIds);
  if (selected.has(normalizedId)) selected.delete(normalizedId);
  else selected.add(normalizedId);
  return {
    ...state,
    selectedInstanceIds: [...selected],
    selectionGeneration: state.selectionGeneration + 1,
    phase: "idle",
    preparation: null,
    plan: null,
    confirmation: closeConfirmation(),
    outcome: null,
    rescan: { state: "idle", snapshotId: null, attemptId: null },
  };
}

export function clearLocalRemovalSelection(state) {
  if (!state.selectedInstanceIds.length || !isLocalRemovalSelectionMode(state) || !canChangeSelection(state)) return state;
  return {
    ...state,
    selectedInstanceIds: [],
    selectionGeneration: state.selectionGeneration + 1,
    phase: "idle",
    preparation: null,
    plan: null,
    confirmation: closeConfirmation(),
  };
}

export function beginLocalRemovalPreparation(state, snapshotId) {
  const normalizedSnapshotId = optionalId(snapshotId);
  if (!normalizedSnapshotId || !isLocalRemovalSelectionMode(state)
    || !state.selectedInstanceIds.length || !canChangeSelection(state)) {
    return { state, request: null, token: null };
  }
  const preparation = {
    snapshotId: normalizedSnapshotId,
    selectionGeneration: state.selectionGeneration,
  };
  return {
    state: {
      ...state,
      phase: "preparing",
      preparation,
      plan: null,
      confirmation: closeConfirmation(),
    },
    request: {
      snapshotId: normalizedSnapshotId,
      instanceIds: [...state.selectedInstanceIds],
    },
    token: preparation,
  };
}

export function acceptLocalRemovalPlan(state, response, token = state.preparation) {
  if (state.phase !== "preparing" || !token || token.selectionGeneration !== state.selectionGeneration
    || token.snapshotId !== state.preparation?.snapshotId) return state;
  const plan = normalizePlan(response, token);
  if (!plan) return {
    ...state,
    phase: "idle",
    preparation: null,
  };
  return {
    ...state,
    phase: "ready",
    preparation: null,
    plan,
  };
}

export function openLocalRemovalConfirmation(state) {
  if (!isLocalRemovalSelectionMode(state) || !isPlanCurrent(state)
    || !state.plan.eligibleMembers.length || state.phase !== "ready") {
    return state;
  }
  return {
    ...state,
    phase: "confirming",
    confirmation: { open: true, acknowledged: false },
  };
}

export function dismissLocalRemovalConfirmation(state) {
  if (state.phase !== "confirming") return state;
  return {
    ...state,
    phase: "ready",
    confirmation: closeConfirmation(),
  };
}

export function acknowledgeLocalRemovalConfirmation(state, acknowledged) {
  if (state.phase !== "confirming" || !state.confirmation.open) return state;
  return {
    ...state,
    confirmation: { ...state.confirmation, acknowledged: acknowledged === true },
  };
}

export function canApplyLocalRemoval(state) {
  return isLocalRemovalSelectionMode(state)
    && state.phase === "confirming"
    && state.confirmation.open
    && state.confirmation.acknowledged
    && isPlanCurrent(state)
    && state.plan.eligibleMembers.length > 0;
}

export function beginLocalRemovalApply(state) {
  if (!canApplyLocalRemoval(state)) return { state, request: null };
  return {
    state: {
      ...state,
      phase: "applying",
      selectionMode: "inactive",
      selectedInstanceIds: [],
      selectionGeneration: state.selectionGeneration + 1,
      confirmation: closeConfirmation(),
    },
    request: {
      planId: state.plan.planId,
      planDigest: state.plan.planDigest,
      confirmed: true,
    },
  };
}

export function acceptLocalRemovalOutcome(state, response) {
  if (state.phase !== "applying") return state;
  const outcome = normalizeOutcome(response);
  if (!outcome) return { ...state, phase: "complete" };
  const requiresReconciliation = outcome.successfulSourceGroupCount > 0
    || outcome.indeterminateSourceGroupCount > 0;
  const awaitingRescan = requiresReconciliation
    && ["accepted", "queued"].includes(outcome.rescan)
    && outcome.rescanAttemptId !== null;
  let rescanState = "not_started";
  if (awaitingRescan) {
    rescanState = "waiting";
  } else if (requiresReconciliation && ["accepted", "queued"].includes(outcome.rescan)) {
    rescanState = "unverified";
  } else if (requiresReconciliation) {
    rescanState = outcome.rescan;
  }
  return {
    ...state,
    phase: awaitingRescan ? "awaiting_rescan" : "complete",
    outcome,
    rescan: {
      state: rescanState,
      snapshotId: null,
      attemptId: outcome.rescanAttemptId,
    },
  };
}

function reconciliationMemberIds(state) {
  const groups = state.outcome?.sourceGroupOutcomes ?? [];
  return uniqueInstanceIds(groups
    .filter((group) => ["success", "indeterminate"].includes(group.state))
    .flatMap((group) => group.memberIds));
}

function finalizeReconciliation(state, rescan) {
  return {
    ...state,
    selectionMode: "inactive",
    selectedInstanceIds: [],
    phase: "complete",
    preparation: null,
    plan: null,
    confirmation: closeConfirmation(),
    rescan,
  };
}

export function beginLocalRemovalReconciliation(state, snapshotId, attemptId) {
  const normalizedSnapshotId = optionalId(snapshotId);
  const normalizedAttemptId = optionalId(attemptId);
  if (state.phase !== "awaiting_rescan"
    || !normalizedSnapshotId
    || !normalizedAttemptId
    || normalizedSnapshotId === state.plan?.snapshotId
    || normalizedAttemptId !== state.rescan?.attemptId) {
    return { state, request: null, token: null };
  }
  const instanceIds = reconciliationMemberIds(state);
  if (!instanceIds.length) {
    return {
      state: finalizeReconciliation(state, {
        state: "unverified",
        snapshotId: normalizedSnapshotId,
        attemptId: normalizedAttemptId,
      }),
      request: null,
      token: null,
    };
  }
  const token = {
    snapshotId: normalizedSnapshotId,
    attemptId: normalizedAttemptId,
  };
  return {
    state: {
      ...state,
      phase: "reconciling",
      rescan: { state: "reconciling", ...token },
    },
    request: {
      snapshotId: normalizedSnapshotId,
      expectedAttemptId: normalizedAttemptId,
      instanceIds,
    },
    token,
  };
}

function normalizeReconciliation(value) {
  if (!value || typeof value !== "object") return null;
  const snapshotId = optionalId(value.snapshotId ?? value.snapshot_id);
  const state = String(value.state ?? value.status ?? "").trim().toLowerCase();
  if (!snapshotId || !["confirmed", "coverage_incomplete"].includes(state)) return null;
  return {
    snapshotId,
    state,
    confirmedAbsentInstanceIds: uniqueInstanceIds(
      value.confirmedAbsentInstanceIds ?? value.confirmed_absent_instance_ids,
    ),
  };
}

export function acceptLocalRemovalReconciliation(state, response, token = state.rescan) {
  if (state.phase !== "reconciling" || !token
    || token.snapshotId !== state.rescan?.snapshotId
    || token.attemptId !== state.rescan?.attemptId) return state;
  const reconciliation = normalizeReconciliation(response);
  if (!reconciliation || reconciliation.snapshotId !== token.snapshotId) {
    return finalizeReconciliation(state, {
      state: "unverified",
      snapshotId: token.snapshotId,
      attemptId: token.attemptId,
    });
  }
  return finalizeReconciliation({
    ...state,
  }, {
    state: reconciliation.state,
    snapshotId: token.snapshotId,
    attemptId: token.attemptId,
  });
}

export function rejectLocalRemovalReconciliation(state, token = state.rescan) {
  if (state.phase !== "reconciling" || !token
    || token.snapshotId !== state.rescan?.snapshotId
    || token.attemptId !== state.rescan?.attemptId) return state;
  return finalizeReconciliation(state, {
    state: "unverified",
    snapshotId: token.snapshotId,
    attemptId: token.attemptId,
  });
}

export function acceptLocalRemovalRescanFailure(state, attemptId) {
  const normalizedAttemptId = optionalId(attemptId);
  if (state.phase !== "awaiting_rescan"
    || !normalizedAttemptId
    || normalizedAttemptId !== state.rescan?.attemptId) return state;
  return finalizeReconciliation(state, {
    state: "failed",
    snapshotId: null,
    attemptId: normalizedAttemptId,
  });
}
