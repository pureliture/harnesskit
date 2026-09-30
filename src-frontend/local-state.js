export const LOCAL_ROOT_ID = "local-root";
export const LOCAL_RESULT_PAGE_SIZE = 100;

const LOCATION_SCOPES = new Set(["all", "user", "project"]);
const LOCAL_ACTION_STATES = new Set(["pending", "success", "error"]);

function asOptionalId(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

function normalizeAttempt(value) {
  if (!value) return null;
  const attemptId = asOptionalId(value.attemptId ?? value.attempt_id);
  if (!attemptId) return null;
  const progress = normalizeScanProgress(value.progress, attemptId);
  return {
    attemptId,
    state: String(value.state ?? "").toLowerCase() || "running",
    errorCode: asOptionalId(value.errorCode ?? value.error_code),
    progress,
  };
}

function normalizeSnapshotHeader(value) {
  if (!value) return null;
  const snapshotId = asOptionalId(value.snapshotId ?? value.snapshot_id);
  const attemptId = asOptionalId(value.attemptId ?? value.attempt_id);
  if (!snapshotId || !attemptId) return null;
  return {
    snapshotId,
    attemptId,
    status: String(value.status ?? "").toLowerCase() === "partial" ? "partial" : "complete",
  };
}

function normalizeScanProgress(value, attemptId = null) {
  if (!value) return null;
  const normalizedAttemptId = asOptionalId(value.attemptId ?? value.attempt_id) ?? attemptId;
  const adapterId = asOptionalId(value.adapterId ?? value.adapter_id);
  const surfaceId = asOptionalId(value.surfaceId ?? value.surface_id);
  const coverageId = asOptionalId(value.coverageId ?? value.coverage_id);
  const itemCount = Number(value.itemCount ?? value.item_count);
  if (!normalizedAttemptId || !adapterId || !surfaceId || !coverageId
    || !Number.isSafeInteger(itemCount) || itemCount < 0) return null;
  return { attemptId: normalizedAttemptId, adapterId, surfaceId, coverageId, itemCount };
}

function normalizeActionStatus(value, selectedInstanceId) {
  if (!value || !selectedInstanceId) return null;
  const state = String(value.state ?? "").toLowerCase();
  const code = asOptionalId(value.code);
  const message = String(value.message ?? "").trim();
  const instanceId = asOptionalId(value.instanceId) ?? selectedInstanceId;
  if (!LOCAL_ACTION_STATES.has(state)
    || !code
    || !message
    || instanceId !== selectedInstanceId) return null;
  return { state, code, message, instanceId };
}

function phaseFromState({ currentAttempt, latestComplete, latestPartial, latestTerminalReport }) {
  if (currentAttempt?.state === "running") return "running";
  if (latestComplete) return "ready";
  if (latestPartial) return "partial";
  if (latestTerminalReport?.state === "failed") return "error";
  return "idle";
}

function mergeFilterRecords(current, incoming, idField) {
  const merged = new Map();
  [...(Array.isArray(current) ? current : []), ...(Array.isArray(incoming) ? incoming : [])]
    .forEach((record) => {
      const id = asOptionalId(record?.[idField]);
      if (!id) return;
      const previous = merged.get(id) ?? {};
      const defined = Object.fromEntries(
        Object.entries(record ?? {}).filter(([, value]) => value !== null && value !== undefined),
      );
      merged.set(id, { ...previous, ...defined, [idField]: id });
    });
  return [...merged.values()];
}

function queryFilterRecords(result) {
  const declaredTools = result?.qualifiedTools ?? result?.qualified_tools ?? result?.tools;
  const declaredProjects = result?.projects;
  return {
    qualifiedTools: Array.isArray(declaredTools) ? declaredTools.map((tool) => ({
        toolId: tool?.toolId ?? tool?.tool_id ?? tool?.id,
        label: tool?.label ?? tool?.name ?? null,
        status: tool?.status,
        qualified: tool?.qualified,
      })) : [],
    projects: Array.isArray(declaredProjects) ? declaredProjects.map((project) => ({
        projectId: project?.projectId ?? project?.project_id ?? project?.id,
        label: project?.displayName ?? project?.display_name
          ?? project?.label ?? project?.name ?? project?.title ?? null,
        canonicalPath: project?.canonicalPath ?? project?.canonical_path ?? null,
      })) : [],
  };
}

export function createLocalDataState(overrides = {}) {
  const state = {
    phase: "idle",
    stateRevision: 0,
    attemptSequences: {},
    scanStartPending: false,
    currentAttempt: null,
    latestTerminalReport: null,
    latestComplete: null,
    latestPartial: null,
    queryResult: null,
    qualifiedTools: [],
    projects: [],
    selectedDetail: null,
    correlationProjection: null,
    ignoreRescanStatus: null,
    message: "",
    ...overrides,
  };
  state.currentAttempt = normalizeAttempt(state.currentAttempt);
  state.latestTerminalReport = normalizeAttempt(state.latestTerminalReport);
  state.latestComplete = normalizeSnapshotHeader(state.latestComplete);
  state.latestPartial = normalizeSnapshotHeader(state.latestPartial);
  state.attemptSequences = { ...(state.attemptSequences ?? {}) };
  return state;
}

export function createLocalViewState(overrides = {}) {
  const requestedScope = String(overrides.locationFilter?.scope ?? "all").toLowerCase();
  const scope = LOCATION_SCOPES.has(requestedScope) ? requestedScope : "all";
  const projectId = scope === "project"
    ? asOptionalId(overrides.locationFilter?.projectId)
    : null;

  const selectedInstanceId = asOptionalId(overrides.selectedInstanceId);
  const resultPageSize = Math.min(
    LOCAL_RESULT_PAGE_SIZE,
    Number.isSafeInteger(overrides.resultPageSize) && overrides.resultPageSize > 0
      ? overrides.resultPageSize
      : LOCAL_RESULT_PAGE_SIZE,
  );
  const requestedOffset = Number.isSafeInteger(overrides.resultOffset) && overrides.resultOffset > 0
    ? overrides.resultOffset
    : 0;
  return {
    explorerId: asOptionalId(overrides.explorerId) ?? LOCAL_ROOT_ID,
    locationFilter: {
      scope,
      projectId: scope === "project" ? projectId : null,
    },
    toolId: asOptionalId(overrides.toolId),
    kind: asOptionalId(overrides.kind),
    query: String(overrides.query ?? ""),
    projectExplorerQuery: String(overrides.projectExplorerQuery ?? ""),
    selectedInstanceId,
    actionStatus: normalizeActionStatus(overrides.actionStatus, selectedInstanceId),
    correlationProjectionId: asOptionalId(overrides.correlationProjectionId),
    verifiedComponentId: asOptionalId(overrides.verifiedComponentId),
    scrollTop: Number.isFinite(overrides.scrollTop) && overrides.scrollTop > 0
      ? overrides.scrollTop
      : 0,
    resultOffset: Math.floor(requestedOffset / resultPageSize) * resultPageSize,
    resultPageSize,
  };
}

export function reduceLocalScanState(state, action) {
  if (!action || !state) return state;

  if (action.type === "reconcile") {
    const value = action.scanState ?? {};
    const incomingRevision = Number(value.stateRevision ?? value.state_revision ?? 0);
    if (!Number.isSafeInteger(incomingRevision) || incomingRevision < state.stateRevision) {
      return state;
    }
    const currentAttempt = normalizeAttempt(value.currentAttempt ?? value.current_attempt);
    const latestTerminalReport = normalizeAttempt(
      value.latestTerminalReport ?? value.latest_terminal_report,
    );
    const latestComplete = normalizeSnapshotHeader(value.latestComplete ?? value.latest_complete);
    const latestPartial = normalizeSnapshotHeader(value.latestPartial ?? value.latest_partial);
    const activeSnapshot = latestComplete ?? latestPartial;
    const querySnapshotId = asOptionalId(
      state.queryResult?.snapshotId ?? state.queryResult?.snapshot_id,
    );
    const queryResult = activeSnapshot && querySnapshotId === activeSnapshot.snapshotId
      ? state.queryResult
      : null;
    const previousSnapshot = activeLocalSnapshotHeader(state);
    const snapshotChanged = previousSnapshot?.snapshotId !== activeSnapshot?.snapshotId;
    const next = {
      ...state,
      stateRevision: incomingRevision,
      currentAttempt,
      latestTerminalReport,
      latestComplete,
      latestPartial,
      ignoreRescanStatus: currentAttempt ? state.ignoreRescanStatus : null,
      queryResult,
      qualifiedTools: snapshotChanged ? [] : state.qualifiedTools,
      projects: snapshotChanged ? [] : state.projects,
    };
    return {
      ...next,
      phase: phaseFromState(next),
      message: currentAttempt
        ? ignoreRescanMessage(state.ignoreRescanStatus) ?? "로컬 하네스 스캔 중"
        : latestTerminalReport?.state === "failed"
          ? "최근 스캔이 실패했습니다. 마지막 유효 snapshot은 유지됩니다."
          : activeSnapshot
            ? "로컬 하네스 snapshot을 사용할 수 있습니다."
            : "로컬 하네스 스캔 전",
    };
  }

  if (action.type === "event") {
    const envelope = action.envelope ?? {};
    const attemptId = asOptionalId(envelope.attemptId ?? envelope.attempt_id);
    const sequence = Number(envelope.sequence);
    const incomingRevision = Number(envelope.stateRevision ?? envelope.state_revision);
    if (
      !attemptId
      || !Number.isSafeInteger(sequence)
      || sequence < 0
      || !Number.isSafeInteger(incomingRevision)
      || incomingRevision < state.stateRevision
      || sequence <= (state.attemptSequences[attemptId] ?? -1)
    ) {
      return state;
    }

    const payload = envelope.payload ?? {};
    const eventState = String(payload.state ?? "").toLowerCase();
    const attemptSequences = { ...state.attemptSequences, [attemptId]: sequence };
    const next = {
      ...state,
      stateRevision: Math.max(state.stateRevision, incomingRevision),
      attemptSequences,
    };
    if (eventState === "running") {
      next.currentAttempt = { attemptId, state: "running", errorCode: null, progress: null };
      next.phase = "running";
      next.message = ignoreRescanMessage(state.ignoreRescanStatus) ?? "로컬 하네스 스캔 중";
      return next;
    }

    if (eventState === "progress") {
      const progress = normalizeScanProgress(payload, attemptId);
      if (!progress) return state;
      next.currentAttempt = { attemptId, state: "running", errorCode: null, progress };
      next.phase = "running";
      next.message = ignoreRescanMessage(state.ignoreRescanStatus)
        ?? `스캔 중: ${progress.adapterId} / ${progress.surfaceId} · ${progress.itemCount}개 확인`;
      return next;
    }

    if (eventState === "complete" || eventState === "partial") {
      const snapshotId = asOptionalId(payload.snapshotId ?? payload.snapshot_id);
      if (!snapshotId) return state;
      const terminal = { attemptId, state: eventState, errorCode: null };
      const header = { snapshotId, attemptId, status: eventState };
      next.currentAttempt = null;
      next.ignoreRescanStatus = null;
      next.latestTerminalReport = terminal;
      if (eventState === "complete") next.latestComplete = header;
      else next.latestPartial = header;
      next.queryResult = null;
      if (eventState === "complete") {
        next.qualifiedTools = [];
        next.projects = [];
      }
      next.phase = eventState === "complete" ? "ready" : "partial";
      next.message = eventState === "complete"
        ? "로컬 하네스 스캔이 완료되었습니다."
        : "일부 coverage issue가 있는 snapshot입니다.";
      return next;
    }

    if (eventState === "failed") {
      next.currentAttempt = null;
      next.ignoreRescanStatus = null;
      next.latestTerminalReport = {
        attemptId,
        state: "failed",
        errorCode: asOptionalId(payload.code),
      };
      next.phase = phaseFromState(next);
      next.message = "최근 로컬 스캔이 실패했습니다. 마지막 유효 snapshot은 유지됩니다.";
      return next;
    }
    return state;
  }

  if (action.type === "start_outcome") {
    const status = String(action.outcome?.status ?? "").toLowerCase();
    const attemptId = asOptionalId(action.outcome?.attemptId ?? action.outcome?.attempt_id);
    if ((status === "accepted" || status === "already_running") && attemptId) {
      return {
        ...state,
        phase: "running",
        currentAttempt: {
          attemptId,
          state: "running",
          errorCode: null,
          progress: state.currentAttempt?.attemptId === attemptId
            ? state.currentAttempt.progress
            : null,
        },
        message: status === "accepted" ? "로컬 하네스 스캔을 시작했습니다." : "이미 로컬 하네스 스캔 중입니다.",
      };
    }
    if (status === "operation_busy") {
      return { ...state, phase: "busy", message: "다른 filesystem 작업이 진행 중입니다." };
    }
    return state;
  }

  if (action.type === "ignore_save_outcome") {
    const status = String(action.outcome?.status ?? "").toLowerCase();
    const normalized = status === "accepted"
      ? "accepted"
      : status === "queued_after_current"
        ? "queued"
        : status === "operation_busy"
          ? "busy"
          : null;
    if (!normalized) return state;
    return {
      ...state,
      ignoreRescanStatus: normalized,
      message: ignoreRescanMessage(normalized),
    };
  }

  if (action.type === "query_result") {
    const result = action.result ?? {};
    const snapshotId = asOptionalId(result.snapshotId ?? result.snapshot_id);
    if (!snapshotId) return state;
    const activeSnapshot = activeLocalSnapshotHeader(state);
    if (activeSnapshot && activeSnapshot.snapshotId !== snapshotId) return state;
    const filters = queryFilterRecords(result);
    return {
      ...state,
      queryResult: { ...result, snapshotId },
      qualifiedTools: mergeFilterRecords([], filters.qualifiedTools, "toolId"),
      projects: mergeFilterRecords([], filters.projects, "projectId"),
    };
  }

  if (action.type === "query_error") {
    return {
      ...state,
      queryResult: null,
      message: "로컬 하네스 검색 결과를 불러오지 못했습니다.",
    };
  }

  if (action.type === "detail_result") {
    const instanceId = asOptionalId(
      action.detail?.instanceId ?? action.detail?.instance_id,
    );
    if (!instanceId) return state;
    return { ...state, selectedDetail: { ...action.detail, instanceId } };
  }

  if (action.type === "clear_detail") {
    return { ...state, selectedDetail: null };
  }

  return state;
}

function ignoreRescanMessage(status) {
  if (status === "accepted") return "제외 규칙을 저장하고 새 스캔을 시작했습니다.";
  if (status === "queued") return "현재 스캔 완료 후 제외 규칙 반영 스캔을 실행합니다.";
  if (status === "busy") return "제외 규칙은 저장했지만 다른 작업 중이라 스캔을 시작하지 못했습니다.";
  return null;
}

export function reduceLocalView(state, action) {
  if (!state || !action) return state;
  if (action.type === "reset") return createLocalViewState();
  if (action.type === "clear_correlation") return createLocalViewState();

  if (action.type === "set_action_status") {
    const instanceId = asOptionalId(action.instanceId);
    if (!instanceId || instanceId !== state.selectedInstanceId) return state;
    const actionStatus = normalizeActionStatus(action.status, instanceId);
    return actionStatus ? { ...state, actionStatus } : state;
  }

  if (action.type === "select_location") {
    const requestedScope = String(action.scope ?? "all").toLowerCase();
    const projectId = requestedScope === "project" ? asOptionalId(action.projectId) : null;
    const scope = LOCATION_SCOPES.has(requestedScope) ? requestedScope : "all";
    return {
      ...state,
      explorerId: scope === "project"
        ? projectId ?? "local-projects"
        : scope === "user" ? "local-user" : LOCAL_ROOT_ID,
      locationFilter: { scope, projectId: scope === "project" ? projectId : null },
      selectedInstanceId: null,
      actionStatus: null,
      scrollTop: 0,
      resultOffset: 0,
    };
  }

  if (action.type === "select_tool") {
    return {
      ...state,
      toolId: asOptionalId(action.toolId),
      selectedInstanceId: null,
      actionStatus: null,
      scrollTop: 0,
      resultOffset: 0,
    };
  }

  if (action.type === "select_kind") {
    return {
      ...state,
      kind: asOptionalId(action.kind),
      selectedInstanceId: null,
      actionStatus: null,
      scrollTop: 0,
      resultOffset: 0,
    };
  }

  if (action.type === "set_query") {
    return {
      ...state,
      query: String(action.query ?? ""),
      selectedInstanceId: null,
      actionStatus: null,
      scrollTop: 0,
      resultOffset: 0,
    };
  }

  if (action.type === "set_project_explorer_query") {
    return {
      ...state,
      projectExplorerQuery: String(action.query ?? ""),
    };
  }

  if (action.type === "select_instance") {
    return {
      ...state,
      selectedInstanceId: asOptionalId(action.instanceId),
      actionStatus: null,
    };
  }

  if (action.type === "next_result_window") {
    const totalCount = Number(action.totalCount);
    const nextOffset = state.resultOffset + state.resultPageSize;
    if (!Number.isSafeInteger(totalCount) || totalCount < 0 || nextOffset >= totalCount) return state;
    return {
      ...state,
      resultOffset: nextOffset,
      selectedInstanceId: null,
      actionStatus: null,
      scrollTop: 0,
    };
  }

  if (action.type === "previous_result_window") {
    if (state.resultOffset <= 0) return state;
    return {
      ...state,
      resultOffset: Math.max(0, state.resultOffset - state.resultPageSize),
      selectedInstanceId: null,
      actionStatus: null,
      scrollTop: 0,
    };
  }

  if (action.type === "reconcile_result_window") {
    const totalCount = Number(action.totalCount);
    if (!Number.isSafeInteger(totalCount) || totalCount < 0) return state;
    const lastOffset = totalCount > 0
      ? Math.floor((totalCount - 1) / state.resultPageSize) * state.resultPageSize
      : 0;
    if (state.resultOffset <= lastOffset) return state;
    return {
      ...state,
      resultOffset: lastOffset,
      selectedInstanceId: null,
      actionStatus: null,
      scrollTop: 0,
    };
  }

  if (action.type === "set_scroll") {
    return {
      ...state,
      scrollTop: Number.isFinite(action.scrollTop) && action.scrollTop > 0 ? action.scrollTop : 0,
    };
  }

  if (action.type === "set_correlation") {
    return {
      ...state,
      correlationProjectionId: asOptionalId(action.projectionId),
      verifiedComponentId: asOptionalId(action.verifiedComponentId),
      selectedInstanceId: null,
      actionStatus: null,
      scrollTop: 0,
      resultOffset: 0,
    };
  }

  return state;
}

export function activeLocalSnapshotHeader(state) {
  return state?.latestComplete ?? state?.latestPartial ?? null;
}

export function createLocalQueryRequest(data, view) {
  const snapshot = activeLocalSnapshotHeader(data);
  if (!snapshot?.snapshotId) return null;
  return {
    snapshotId: snapshot.snapshotId,
    locationFilter: {
      scope: view.locationFilter.scope,
      projectId: view.locationFilter.scope === "project"
        ? view.locationFilter.projectId
        : null,
    },
    toolId: view.toolId,
    kind: view.kind,
    query: view.query,
    correlationProjectionId: view.correlationProjectionId,
    verifiedComponentId: view.verifiedComponentId,
  };
}

export function localRenderFingerprint(data, view) {
  const snapshotId = activeLocalSnapshotHeader(data)?.snapshotId ?? null;
  return JSON.stringify({
    snapshotId,
    location: view?.locationFilter?.scope ?? "all",
    projectId: view?.locationFilter?.projectId ?? null,
    toolId: view?.toolId ?? null,
    kind: view?.kind ?? null,
    query: view?.query ?? "",
    resultOffset: view?.resultOffset ?? 0,
  });
}
