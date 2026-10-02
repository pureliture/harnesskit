function optionalText(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

function stringList(value, projector = (item) => item) {
  if (!Array.isArray(value)) return [];
  return value.map(projector).map(optionalText).filter(Boolean);
}

function componentId(value) {
  if (typeof value === "string") return value;
  return value?.componentId ?? value?.component_id ?? value?.id;
}

function targetId(value) {
  if (typeof value === "string") return value;
  return value?.targetId ?? value?.target_id ?? value?.target ?? value?.id;
}

function normalizedScope(value) {
  const scope = String(value ?? "user").trim().toLowerCase();
  return ["user", "project"].includes(scope) ? scope : "user";
}

function requestValue(value, camel, snake, fallback) {
  return value?.[camel] ?? value?.[snake] ?? fallback?.[camel] ?? fallback?.[snake];
}

export function normalizeInstallRequest(value = {}, fallback = {}) {
  const targets = requestValue(value, "targetIds", "target_ids")
    ?? value.targets
    ?? fallback.targetIds
    ?? fallback.target_ids
    ?? [];
  return {
    sotSnapshotId: optionalText(requestValue(value, "sotSnapshotId", "sot_snapshot_id", fallback)),
    profileId: optionalText(requestValue(value, "profileId", "profile_id", fallback)),
    targetIds: stringList(targets, targetId),
    scope: normalizedScope(requestValue(value, "scope", "scope", fallback)),
    targetRoot: optionalText(requestValue(value, "targetRoot", "target_root", fallback)) ?? "",
  };
}

export function createInstallRequest(state) {
  return normalizeInstallRequest({
    sotSnapshotId: state?.subject?.sotSnapshotId,
    profileId: state?.form?.profileId,
    targetIds: state?.form?.targetId ? [state.form.targetId] : [],
    scope: state?.form?.scope,
    targetRoot: state?.form?.targetRoot,
  });
}

function normalizeArtifact(value) {
  if (!value || typeof value !== "object") return null;
  const destination = optionalText(value.destination ?? value.destination_key);
  if (!destination) return null;
  return {
    componentId: optionalText(value.componentId ?? value.component_id),
    targetId: optionalText(value.targetId ?? value.target_id ?? value.target),
    scope: optionalText(value.scope),
    destination,
    mergeStrategy: optionalText(value.mergeStrategy ?? value.merge_strategy),
    mode: Number.isSafeInteger(value.mode) ? value.mode : null,
  };
}

function normalizeSkippedWrite(value) {
  if (typeof value === "string") return { destination: value, reasonCode: null };
  if (!value || typeof value !== "object") return null;
  const destination = optionalText(value.destination ?? value.path);
  if (!destination) return null;
  return {
    destination,
    reasonCode: optionalText(value.reasonCode ?? value.reason_code ?? value.reason),
  };
}

function normalizeWarning(value) {
  if (typeof value === "string") return optionalText(value);
  return optionalText(value?.safeMessage ?? value?.safe_message ?? value?.code);
}

function normalizeRuntimeGate(value) {
  if (typeof value === "string") {
    const gateId = optionalText(value);
    return gateId ? {
      gateId,
      target: null,
      safeReason: null,
      requiredBeforeApply: false,
      requiredBeforeRuntime: false,
    } : null;
  }
  if (!value || typeof value !== "object") return null;
  const gateId = optionalText(value.gateId ?? value.gate_id);
  if (!gateId) return null;
  return {
    gateId,
    target: optionalText(value.target),
    safeReason: optionalText(value.safeReason ?? value.safe_reason),
    requiredBeforeApply: value.requiredBeforeApply === true || value.required_before_apply === true,
    requiredBeforeRuntime: value.requiredBeforeRuntime === true || value.required_before_runtime === true,
  };
}

function normalizeRequiredApprovals(value) {
  if (Array.isArray(value)) {
    const approvals = new Set(value.map(optionalText).filter(Boolean));
    return {
      overwrite: approvals.has("overwrite"),
      runtimeHooks: approvals.has("runtime_hooks")
        || approvals.has("allow_runtime_hooks")
        || approvals.has("runtimeHooks")
        || approvals.has("allowRuntimeHooks"),
    };
  }
  const approvals = value && typeof value === "object" ? value : {};
  return {
    ...((approvals.managementAdoption === true || approvals.management_adoption === true) ? { managementAdoption: true } : {}),
    ...((approvals.managedReplacement === true || approvals.managed_replacement === true) ? { managedReplacement: true } : {}),
    overwrite: approvals.overwrite === true,
    runtimeHooks: approvals.runtimeHooks === true
      || approvals.runtime_hooks === true
      || approvals.allowRuntimeHooks === true
      || approvals.allow_runtime_hooks === true,
  };
}

function normalizePlan(value = {}, request) {
  return {
    scope: normalizedScope(value.scope ?? request.scope),
    targetRoot: optionalText(value.targetRoot ?? value.target_root ?? request.targetRoot) ?? "",
    targetIds: stringList(value.targetIds ?? value.target_ids ?? value.targets ?? request.targetIds, targetId),
    componentIds: stringList(
      value.componentIds ?? value.component_ids ?? value.components,
      componentId,
    ),
    runtimeSurfaces: stringList(value.runtimeSurfaces ?? value.runtime_surfaces),
    runtimeGates: (Array.isArray(
      value.runtimeGates ?? value.runtime_gates ?? value.activationGates ?? value.activation_gates,
    ) ? value.runtimeGates ?? value.runtime_gates ?? value.activationGates ?? value.activation_gates : [])
      .map(normalizeRuntimeGate)
      .filter(Boolean),
    artifacts: (Array.isArray(value.artifacts) ? value.artifacts : []).map(normalizeArtifact).filter(Boolean),
    skippedWrites: (Array.isArray(value.skippedWrites ?? value.skipped_writes)
      ? value.skippedWrites ?? value.skipped_writes
      : []).map(normalizeSkippedWrite).filter(Boolean),
    warnings: (Array.isArray(value.warnings) ? value.warnings : []).map(normalizeWarning).filter(Boolean),
    nonAtomicBoundary: value.nonAtomicBoundary ?? value.non_atomic_boundary ?? null,
    requiredApprovals: normalizeRequiredApprovals(
      value.requiredApprovals ?? value.required_approvals,
    ),
  };
}

export function normalizeInstallPreviewResponse(value, fallbackRequest = {}) {
  if (!value || typeof value !== "object") return null;
  const previewId = optionalText(value.previewId ?? value.preview_id);
  const semanticFingerprint = optionalText(
    value.fingerprint ?? value.semanticFingerprint ?? value.semantic_fingerprint,
  );
  if (!previewId || !semanticFingerprint) return null;
  const request = normalizeInstallRequest(value.request ?? value, fallbackRequest);
  return {
    previewId,
    semanticFingerprint,
    request,
    plan: normalizePlan(value.plan ?? value.previewPlan ?? value.preview_plan ?? value, request),
  };
}

function normalizeOutcomeStatus(value) {
  const status = String(value ?? "").trim().toLowerCase();
  if (["success", "succeeded", "complete", "verified"].includes(status)) return "success";
  if (["partial", "partially_applied", "partially-applied"].includes(status)) return "partial";
  if (["failure", "failed", "error"].includes(status)) return "failure";
  return null;
}

function normalizeStepStatus(value) {
  const status = String(value ?? "").trim().toLowerCase();
  if (["success", "succeeded", "complete", "completed", "applied", "verified"].includes(status)) {
    return status === "verified" ? "verified" : status === "applied" ? "applied" : "success";
  }
  if (["failure", "failed", "error", "unverified", "timed_out", "timeout"].includes(status)) {
    return status === "unverified" ? "unverified" : "failed";
  }
  return null;
}

function normalizeDestination(value) {
  if (!value || typeof value !== "object") return null;
  const destination = optionalText(value.destination ?? value.destinationKey ?? value.destination_key);
  if (!destination) return null;
  return {
    targetId: optionalText(value.targetId ?? value.target_id ?? value.target),
    destination,
    applyState: optionalText(value.applyState ?? value.apply_state ?? value.status)?.toLowerCase()
      ?? "unknown",
    verifyState: optionalText(value.verifyState ?? value.verify_state)?.toLowerCase()
      ?? "unknown",
    issueCode: optionalText(value.code ?? value.issueCode ?? value.issue_code),
  };
}

export function normalizeApplyInstallResponse(value) {
  if (!value || typeof value !== "object") return null;
  const destinations = (Array.isArray(value.destinations) ? value.destinations : [])
    .map(normalizeDestination)
    .filter(Boolean);
  const applyStatus = normalizeStepStatus(
    value.applyStatus ?? value.apply_status ?? value.apply?.status,
  );
  const verifyStatus = normalizeStepStatus(
    value.verifyStatus ?? value.verify_status ?? value.verify?.status,
  );
  const destinationFailed = destinations.some((destination) => [
    "failed", "error", "unknown", "not_attempted",
  ].includes(destination.applyState) || [
    "failed", "unverified", "error", "unknown", "not_applied", "not_attempted",
  ].includes(destination.verifyState));
  const destinationChanged = destinations.some(
    (destination) => ["changed", "unchanged", "skipped"].includes(destination.applyState),
  );
  let status = normalizeOutcomeStatus(value.status ?? value.outcome ?? value.result);
  if (!status) {
    if (applyStatus === "failed") status = destinationChanged ? "partial" : "failure";
    else if (destinationFailed || verifyStatus === "failed" || verifyStatus === "unverified") status = "partial";
    else if (["applied", "success"].includes(applyStatus) && ["verified", "success"].includes(verifyStatus)) {
      status = "success";
    } else status = "partial";
  }
  if (status === "success" && (destinationFailed || destinations.length === 0)) status = "partial";
  if (status === "failure" && destinationChanged) status = "partial";
  return {
    operationId: optionalText(value.operationId ?? value.operation_id),
    previewId: optionalText(value.previewId ?? value.preview_id),
    status,
    applyStatus,
    verifyStatus,
    destinations,
    installEvidenceId: optionalText(value.installEvidenceId ?? value.install_evidence_id),
  };
}

function sameRequest(left, right) {
  if (!left || !right) return false;
  return left.sotSnapshotId === right.sotSnapshotId
    && left.scope === right.scope
    && left.targetRoot === right.targetRoot
    && left.profileId === right.profileId
    && left.targetIds.join("\u0000") === right.targetIds.join("\u0000");
}

export function createInstallState(overrides = {}) {
  const {
    subject: subjectOverride,
    form: formOverride,
    ...stateOverrides
  } = overrides;
  const state = {
    phase: "idle",
    preview: null,
    confirmed: false,
    approvalPreviewId: null,
    approvalFingerprint: null,
    overwrite: false,
    adoptManagement: false,
    replaceManaged: false,
    allowRuntimeHooks: false,
    execution: null,
    message: "",
    ...stateOverrides,
    subject: {
      sotSnapshotId: optionalText(subjectOverride?.sotSnapshotId),
      componentId: optionalText(subjectOverride?.componentId),
    },
    form: {
      profileId: "",
      targetId: "",
      scope: "user",
      targetRoot: "",
      ...formOverride,
    },
  };
  state.form.targetId = optionalText(state.form.targetId) ?? "";
  state.form.profileId = optionalText(state.form.profileId) ?? "";
  state.form.scope = normalizedScope(state.form.scope);
  state.form.targetRoot = String(state.form.targetRoot ?? "").trim();
  return state;
}

export function canApplyInstall(state) {
  const currentRequest = createInstallRequest(state);
  const preview = normalizeInstallPreviewResponse(state?.preview, currentRequest);
  return Boolean(
    preview
    && state?.phase === "preview-ready"
    && state?.confirmed === true
    && state?.approvalPreviewId === preview.previewId
    && state?.approvalFingerprint === preview.semanticFingerprint
    && (!preview.plan.requiredApprovals.managedReplacement || state?.replaceManaged === true)
    && (!preview.plan.requiredApprovals.managementAdoption || state?.adoptManagement === true)
    && (!preview.plan.requiredApprovals.overwrite || state?.overwrite === true)
    && (!preview.plan.requiredApprovals.runtimeHooks || state?.allowRuntimeHooks === true)
    && sameRequest(preview.request, currentRequest),
  );
}

export function reduceInstallState(state, action) {
  if (!state || !action) return state;
  if (action.type === "select_subject") {
    const nextSubject = {
      sotSnapshotId: optionalText(action.sotSnapshotId),
      componentId: optionalText(action.componentId),
    };
    const target = optionalText(action.targetId) ?? "";
    const profile = optionalText(action.profileId) ?? state.form.profileId;
    if (state.subject.sotSnapshotId === nextSubject.sotSnapshotId
      && state.subject.componentId === nextSubject.componentId
      && state.form.profileId === profile
      && state.form.targetId === target) return state;
    return createInstallState({
      subject: nextSubject,
      form: {
        profileId: profile,
        targetId: target,
        scope: state.form.scope,
        targetRoot: state.form.targetRoot,
      },
    });
  }
  if (action.type === "update_form") {
    const form = {
      profileId: optionalText(action.form?.profileId) ?? "",
      targetId: optionalText(action.form?.targetId) ?? "",
      scope: normalizedScope(action.form?.scope),
      targetRoot: String(action.form?.targetRoot ?? "").trim(),
    };
    if (form.profileId === state.form.profileId
      && form.targetId === state.form.targetId
      && form.scope === state.form.scope
      && form.targetRoot === state.form.targetRoot) return state;
    return createInstallState({
      subject: state.subject,
      form,
      message: state.preview ? "입력이 변경되어 기존 preview와 승인이 폐기되었습니다." : "",
    });
  }
  if (action.type === "preview_started") {
    return {
      ...state,
      phase: "previewing",
      preview: null,
      confirmed: false,
      adoptManagement: false,
      replaceManaged: false,
      approvalPreviewId: null,
      approvalFingerprint: null,
      execution: null,
      message: "Install preview를 만들고 있습니다.",
    };
  }
  if (action.type === "preview_ready") {
    return {
      ...state,
      phase: "preview-ready",
      preview: action.preview,
      confirmed: false,
      adoptManagement: false,
      replaceManaged: false,
      approvalPreviewId: null,
      approvalFingerprint: null,
      execution: null,
      message: "Preview가 준비되었습니다. 대상과 경고를 확인하세요.",
    };
  }
  if (action.type === "preview_error") {
    return {
      ...state,
      phase: "error",
      preview: null,
      confirmed: false,
      adoptManagement: false,
      replaceManaged: false,
      approvalPreviewId: null,
      approvalFingerprint: null,
      execution: null,
      message: String(action.message ?? "Install preview를 만들지 못했습니다."),
    };
  }
  if (action.type === "set_approval") {
    const preview = normalizeInstallPreviewResponse(state.preview, createInstallRequest(state));
    const confirmed = action.confirmed === true && Boolean(preview);
    return {
      ...state,
      confirmed,
      approvalPreviewId: confirmed ? preview.previewId : null,
      approvalFingerprint: confirmed ? preview.semanticFingerprint : null,
      overwrite: action.overwrite === true,
      adoptManagement: action.adoptManagement === true,
      replaceManaged: action.replaceManaged === true,
      allowRuntimeHooks: action.allowRuntimeHooks === true,
    };
  }
  if (action.type === "keep_managed_source" && state.phase === "preview-ready") {
    return createInstallState({ subject: state.subject, form: state.form, message: "도구 쪽 변경을 유지했습니다. 이번 적용을 취소했습니다." });
  }
  if (action.type === "apply_started") {
    return { ...state, phase: "applying", execution: null, message: "승인된 preview를 적용하고 검증하고 있습니다." };
  }
  if (action.type === "apply_result") {
    const outcome = classifyExecution(action.execution);
    return {
      ...state,
      phase: "result",
      execution: action.execution,
      confirmed: false,
      adoptManagement: false,
      replaceManaged: false,
      approvalPreviewId: null,
      approvalFingerprint: null,
      message: outcome.message,
    };
  }
  if (action.type === "apply_error") {
    return {
      ...state,
      phase: "error",
      execution: action.execution ?? null,
      confirmed: false,
      adoptManagement: false,
      replaceManaged: false,
      approvalPreviewId: null,
      approvalFingerprint: null,
      message: String(action.message ?? "Install 응답을 받지 못했습니다."),
    };
  }
  return state;
}

export function classifyExecution(value) {
  const execution = normalizeApplyInstallResponse(value);
  if (!execution) {
    return { kind: "failure", message: "Apply 결과가 유효하지 않습니다. 대상 상태를 확인하세요." };
  }
  const failedDestinations = execution.destinations.filter(
    ({ applyState, verifyState }) => [
      "failed", "error", "unknown", "not_attempted",
    ].includes(applyState) || [
      "failed", "error", "unverified", "unknown", "not_applied", "not_attempted",
    ].includes(verifyState),
  );
  const changedDestinations = execution.destinations.filter(
    ({ applyState }) => ["changed", "unchanged", "skipped"].includes(applyState),
  );
  const verified = ["verified", "success"].includes(execution.verifyStatus)
    || (execution.destinations.length > 0 && execution.destinations.every(
      ({ verifyState }) => ["verified", "success", "matched", "skipped"].includes(verifyState),
    ));
  const applied = ["applied", "success"].includes(execution.applyStatus)
    || (execution.destinations.length > 0 && execution.destinations.every(
      ({ applyState }) => ["changed", "unchanged", "skipped", "applied", "success"].includes(applyState),
    ));
  if (execution.status === "success" && applied && verified && failedDestinations.length === 0) {
    return { kind: "success", message: "Apply와 verify가 모두 통과했습니다." };
  }
  if (execution.status === "failure" && changedDestinations.length === 0) {
    return { kind: "failure", message: "Apply가 실패했습니다. 대상 상태를 확인한 뒤 다시 시도하세요." };
  }
  return {
    kind: "partial",
    message: "일부 destination이 반영됐거나 verify되지 않았습니다. 각 destination 상태를 확인하세요.",
  };
}
