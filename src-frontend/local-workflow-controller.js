import {
  activeLocalSnapshotHeader,
  createLocalQueryRequest,
  reduceLocalScanState,
  reduceLocalView,
} from "./local-state.js";

function optionalId(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

export function normalizeCorrelationProjection(value, request = {}) {
  if (!value || typeof value !== "object") return null;
  const projectionId = optionalId(value.projectionId ?? value.projection_id);
  const localSnapshotId = optionalId(value.localSnapshotId ?? value.local_snapshot_id);
  const sotSnapshotId = optionalId(value.sotSnapshotId ?? value.sot_snapshot_id);
  const installEvidenceId = optionalId(value.installEvidenceId ?? value.install_evidence_id);
  if (!projectionId
    || localSnapshotId !== optionalId(request.localSnapshotId)
    || sotSnapshotId !== optionalId(request.sotSnapshotId)
    || installEvidenceId !== optionalId(request.installEvidenceId)) return null;
  return {
    ...value,
    projectionId,
    localSnapshotId,
    sotSnapshotId,
    installEvidenceId,
  };
}

export function matchesLocalRequestIdentity(localData, localView, request) {
  const snapshot = activeLocalSnapshotHeader(localData);
  return snapshot?.snapshotId === request?.snapshotId
    && localView?.selectedInstanceId === request?.instanceId
    && (localView?.correlationProjectionId ?? null)
      === (request?.correlationProjectionId ?? null);
}

function clearLocalCorrelationState(localData, localView) {
  return {
    localData: {
      ...localData,
      queryResult: null,
      selectedDetail: null,
      correlationProjection: null,
    },
    localView: {
      ...localView,
      selectedInstanceId: null,
      correlationProjectionId: null,
      verifiedComponentId: null,
    },
  };
}

export function createLocalWorkflowController({
  backend = {},
  readContext,
  commitLocalSlices,
  commitIgnoreEditor,
  onSnapshotReplaced = () => {},
  onTerminalScanEvent = () => {},
  onIgnoreEditorReady = () => {},
  setTimeout: scheduleTimeout = globalThis.setTimeout,
  clearTimeout: cancelTimeout = globalThis.clearTimeout,
  reconcileIntervalMs = 1000,
} = {}) {
  if (typeof readContext !== "function") {
    throw new TypeError("readContext must be a function");
  }
  if (typeof commitLocalSlices !== "function") {
    throw new TypeError("commitLocalSlices must be a function");
  }
  if (typeof commitIgnoreEditor !== "function") {
    throw new TypeError("commitIgnoreEditor must be a function");
  }

  const fallbackIntervalMs = Number.isFinite(reconcileIntervalMs)
    ? Math.max(1, reconcileIntervalMs)
    : 1000;
  let disposed = false;
  let releaseScanEvents = () => {};
  let eventSubscriptionStarted = false;
  let reconcileTimer = null;
  let reconcileInFlight = false;
  let queryGeneration = 0;
  let correlationGeneration = 0;
  let ignoreGeneration = 0;
  let scanStartInFlight = false;
  let pendingVerifiedContext = null;

  const context = () => {
    const value = readContext() ?? {};
    return {
      localData: value.localData,
      localView: value.localView,
      activeSegment: value.activeSegment,
      sotSnapshotId: optionalId(value.sotSnapshotId),
      installEvidenceId: optionalId(value.installEvidenceId),
      ignoreEditor: value.ignoreEditor ?? null,
    };
  };

  const commitLocalData = (localData, requestedLocalView = null) => {
    if (disposed) return null;
    const current = context();
    const previousSnapshotId = activeLocalSnapshotHeader(current.localData)?.snapshotId ?? null;
    const nextSnapshotId = activeLocalSnapshotHeader(localData)?.snapshotId ?? null;
    const snapshotReplaced = previousSnapshotId !== null
      && nextSnapshotId !== null
      && previousSnapshotId !== nextSnapshotId;
    let localView = requestedLocalView ?? current.localView;
    let reproject = null;
    if (snapshotReplaced) {
      queryGeneration += 1;
      onSnapshotReplaced();
      const generation = ++correlationGeneration;
      const componentId = current.activeSegment === "local"
        ? optionalId(
          pendingVerifiedContext?.componentId ?? current.localView?.verifiedComponentId,
        )
        : null;
      reproject = componentId && current.installEvidenceId
        ? {
          componentId,
          installEvidenceId: current.installEvidenceId,
          generation,
        }
        : null;
      pendingVerifiedContext = reproject;
      ({ localData, localView } = clearLocalCorrelationState(localData, localView));
      localView = { ...localView, actionStatus: null };
    }
    commitLocalSlices({ localData, localView });
    return reproject;
  };

  const refreshQuery = async () => {
    if (disposed || typeof backend.queryLocalInstances !== "function") return null;
    const current = context();
    const request = createLocalQueryRequest(current.localData, current.localView);
    if (!request) return null;
    const generation = ++queryGeneration;
    try {
      const result = await backend.queryLocalInstances(request);
      const latest = context();
      const currentSnapshot = activeLocalSnapshotHeader(latest.localData);
      if (disposed
        || generation !== queryGeneration
        || currentSnapshot?.snapshotId !== request.snapshotId) {
        return null;
      }
      const localData = reduceLocalScanState(
        latest.localData,
        { type: "query_result", result },
      );
      const totalCount = Number(
        result?.counts?.matchedInstances ?? result?.counts?.matched_instances ?? 0,
      );
      const localView = reduceLocalView(latest.localView, {
        type: "reconcile_result_window",
        totalCount,
      });
      commitLocalData(localData, localView);
      return result;
    } catch (_error) {
      if (disposed || generation !== queryGeneration) return null;
      const latest = context();
      commitLocalData(reduceLocalScanState(latest.localData, { type: "query_error" }));
      return null;
    }
  };

  const establishVerifiedContext = async (
    componentId,
    installEvidenceId,
    generation,
  ) => {
    const current = context();
    if (disposed
      || generation !== correlationGeneration
      || current.activeSegment !== "local") return;
    const snapshot = activeLocalSnapshotHeader(current.localData);
    if (!snapshot
      || !current.sotSnapshotId
      || current.installEvidenceId !== installEvidenceId
      || typeof backend.getCorrelationProjection !== "function") {
      pendingVerifiedContext = snapshot
        ? null
        : { componentId, installEvidenceId, generation };
      commitLocalData({
        ...current.localData,
        message: snapshot
          ? "Verified correlation projection을 만들 수 없습니다."
          : "Local snapshot이 준비되면 Verified context를 적용합니다.",
      });
      return;
    }
    try {
      const request = {
        localSnapshotId: snapshot.snapshotId,
        sotSnapshotId: current.sotSnapshotId,
        installEvidenceId,
      };
      const projection = normalizeCorrelationProjection(
        await backend.getCorrelationProjection(request),
        request,
      );
      if (disposed) return;
      if (!projection) throw new Error("correlation projection identity mismatch");
      const latest = context();
      const currentSnapshot = activeLocalSnapshotHeader(latest.localData);
      if (generation !== correlationGeneration
        || latest.activeSegment !== "local"
        || currentSnapshot?.snapshotId !== snapshot.snapshotId
        || latest.sotSnapshotId !== current.sotSnapshotId
        || latest.installEvidenceId !== installEvidenceId) return;
      pendingVerifiedContext = null;
      commitLocalSlices({
        localData: {
          ...reduceLocalScanState(latest.localData, { type: "clear_detail" }),
          correlationProjection: projection,
        },
        localView: reduceLocalView(latest.localView, {
          type: "set_correlation",
          projectionId: projection.projectionId,
          verifiedComponentId: componentId,
        }),
      });
      await refreshQuery();
    } catch (_error) {
      const latest = context();
      if (disposed
        || generation !== correlationGeneration
        || latest.activeSegment !== "local") return;
      pendingVerifiedContext = null;
      commitLocalData({
        ...latest.localData,
        message: "Verified correlation context를 구성하지 못했습니다.",
      });
    }
  };

  const reconcile = async ({ query = true } = {}) => {
    const initial = context();
    if (disposed || typeof backend.getLocalScanState !== "function") {
      return initial.localData;
    }
    try {
      const scanState = await backend.getLocalScanState();
      if (disposed) return context().localData;
      const current = context();
      const localData = reduceLocalScanState(current.localData, {
        type: "reconcile",
        scanState,
      });
      const reproject = commitLocalData(localData);
      if (disposed) return context().localData;
      if (reproject) {
        await establishVerifiedContext(
          reproject.componentId,
          reproject.installEvidenceId,
          reproject.generation,
        );
      } else if (query && activeLocalSnapshotHeader(localData)) {
        await refreshQuery();
      }
      return localData;
    } catch (_error) {
      if (disposed) return context().localData;
      const current = context();
      const snapshot = activeLocalSnapshotHeader(current.localData);
      commitLocalData({
        ...current.localData,
        phase: snapshot ? current.localData.phase : "error",
        message: "Local scan session 상태를 불러오지 못했습니다.",
      });
      return current.localData;
    }
  };

  const stopReconcileFallback = () => {
    if (reconcileTimer !== null && typeof cancelTimeout === "function") {
      cancelTimeout(reconcileTimer);
    }
    reconcileTimer = null;
  };

  const scheduleReconcileFallback = () => {
    const current = context();
    if (disposed
      || reconcileTimer !== null
      || current.localData?.currentAttempt?.state !== "running"
      || typeof scheduleTimeout !== "function") return;
    reconcileTimer = scheduleTimeout(async () => {
      reconcileTimer = null;
      if (disposed || reconcileInFlight) return;
      reconcileInFlight = true;
      try {
        await reconcile({ query: context().activeSegment === "local" });
      } finally {
        reconcileInFlight = false;
      }
      if (context().localData?.currentAttempt?.state === "running") {
        scheduleReconcileFallback();
      } else {
        stopReconcileFallback();
      }
    }, fallbackIntervalMs);
    reconcileTimer?.unref?.();
  };

  const startScan = async () => {
    const current = context();
    if (disposed
      || current.activeSegment !== "local"
      || scanStartInFlight
      || current.localData?.currentAttempt?.state === "running") return;
    if (typeof backend.startLocalScan !== "function") {
      commitLocalData({
        ...current.localData,
        phase: "error",
        message: "Local scan을 시작하지 못했습니다.",
      });
      return;
    }

    scanStartInFlight = true;
    commitLocalData({
      ...current.localData,
      scanStartPending: true,
      message: "Local scan 시작을 요청하고 있습니다.",
    });
    try {
      const outcome = await backend.startLocalScan();
      if (disposed) return;
      const latest = context();
      const attemptId = optionalId(outcome?.attemptId ?? outcome?.attempt_id);
      const status = String(outcome?.status ?? "").toLowerCase();
      const withoutPending = { ...latest.localData, scanStartPending: false };
      if (attemptId && latest.localData.latestTerminalReport?.attemptId === attemptId) {
        commitLocalData(withoutPending);
        return;
      }
      if (!(["accepted", "already_running"].includes(status) && attemptId)
        && status !== "operation_busy") throw new Error("invalid scan start outcome");
      commitLocalData(reduceLocalScanState(withoutPending, { type: "start_outcome", outcome }));
      if (status === "accepted" || status === "already_running") {
        await reconcile({ query: context().activeSegment === "local" });
        scheduleReconcileFallback();
      }
    } catch (_error) {
      if (disposed) return;
      const latest = context();
      commitLocalData({
        ...latest.localData,
        scanStartPending: false,
        phase: "error",
        message: "Local scan을 시작하지 못했습니다.",
      });
    } finally {
      scanStartInFlight = false;
    }
  };

  const ensureSession = async () => {
    if (disposed) return;
    const reconciled = await reconcile({ query: false });
    if (disposed) return;
    if (activeLocalSnapshotHeader(reconciled)) {
      await refreshQuery();
      if (reconciled.currentAttempt?.state === "running") {
        scheduleReconcileFallback();
      }
      return;
    }
    if (reconciled.currentAttempt?.state === "running") {
      scheduleReconcileFallback();
      return;
    }
    await startScan();
  };

  const refreshScan = async () => startScan();

  const openVerifiedContext = async ({ componentId, installEvidenceId } = {}) => {
    const normalizedComponentId = optionalId(componentId);
    const normalizedInstallEvidenceId = optionalId(installEvidenceId);
    if (disposed || !normalizedComponentId || !normalizedInstallEvidenceId) return;
    const generation = ++correlationGeneration;
    pendingVerifiedContext = {
      componentId: normalizedComponentId,
      installEvidenceId: normalizedInstallEvidenceId,
      generation,
    };
    await ensureSession();
    if (disposed || generation !== correlationGeneration) return;
    await establishVerifiedContext(
      normalizedComponentId,
      normalizedInstallEvidenceId,
      generation,
    );
  };

  const resetLocalContext = ({ localData, localView } = {}) => {
    const current = context();
    queryGeneration += 1;
    correlationGeneration += 1;
    pendingVerifiedContext = null;
    onSnapshotReplaced();
    return clearLocalCorrelationState(
      localData ?? current.localData,
      localView ?? current.localView,
    );
  };

  const leaveLocalContext = () => {
    correlationGeneration += 1;
    pendingVerifiedContext = null;
  };

  const openProjectIgnoreEditor = async () => {
    const current = context();
    const snapshot = activeLocalSnapshotHeader(current.localData);
    const projectId = current.localView?.locationFilter?.scope === "project"
      ? optionalId(current.localView.locationFilter.projectId)
      : null;
    if (disposed
      || !snapshot
      || !projectId
      || typeof backend.getProjectIgnore !== "function") return;
    const generation = ++ignoreGeneration;
    commitIgnoreEditor({ open: true, busy: true, exactText: "", issue: null });
    try {
      const response = await backend.getProjectIgnore({
        snapshotId: snapshot.snapshotId,
        projectId,
      });
      if (disposed || generation !== ignoreGeneration) return;
      const sourceRevision = optionalId(
        response?.sourceRevision ?? response?.source_revision,
      );
      if (!sourceRevision) throw new Error("missing ignore source revision");
      commitIgnoreEditor({
        open: true,
        busy: false,
        snapshotId: snapshot.snapshotId,
        projectId,
        sourceRevision,
        exactText: String(response?.exactText ?? response?.exact_text ?? ""),
        issue: optionalId(response?.issue?.safeMessage ?? response?.issue?.safe_message),
      });
      onIgnoreEditorReady();
    } catch (_error) {
      if (disposed || generation !== ignoreGeneration) return;
      commitIgnoreEditor({
        open: true,
        busy: false,
        exactText: "",
        issue: ".harnesskitignore를 읽지 못했습니다.",
      });
    }
  };

  const closeProjectIgnoreEditor = () => {
    if (disposed) return;
    ignoreGeneration += 1;
    commitIgnoreEditor(null);
  };

  const saveProjectIgnore = async ({ exactText = "" } = {}) => {
    const current = context();
    const editor = current.ignoreEditor;
    if (disposed
      || !editor?.open
      || editor.busy
      || !editor.snapshotId
      || !editor.projectId
      || !editor.sourceRevision
      || typeof backend.saveProjectIgnoreAndRescan !== "function") return;
    const normalizedExactText = String(exactText);
    const generation = ++ignoreGeneration;
    commitIgnoreEditor({
      ...editor,
      busy: true,
      exactText: normalizedExactText,
      issue: null,
    });
    try {
      const response = await backend.saveProjectIgnoreAndRescan({
        snapshotId: editor.snapshotId,
        projectId: editor.projectId,
        sourceRevision: editor.sourceRevision,
        exactText: normalizedExactText,
      });
      if (disposed || generation !== ignoreGeneration) return;
      const latest = context();
      commitLocalData(reduceLocalScanState(latest.localData, {
        type: "ignore_save_outcome",
        outcome: response?.rescan,
      }));
      commitIgnoreEditor(null);
      await reconcile({ query: context().activeSegment === "local" });
    } catch (_error) {
      if (disposed || generation !== ignoreGeneration) return;
      commitIgnoreEditor({
        ...editor,
        busy: false,
        exactText: normalizedExactText,
        issue: "규칙을 저장하지 못했습니다. 규칙과 현재 scan 상태를 확인하세요.",
      });
    }
  };

  const handleScanEvent = async (event) => {
    if (disposed) return;
    const current = context();
    const localData = reduceLocalScanState(
      current.localData,
      { type: "event", envelope: event },
    );
    if (localData === current.localData) return;
    commitLocalData(localData);
    if (disposed) return;
    const terminalState = String(event?.payload?.state ?? "").toLowerCase();
    if (["complete", "partial", "failed"].includes(terminalState)) {
      onTerminalScanEvent({
        attemptId: optionalId(event?.attemptId ?? event?.attempt_id),
        state: terminalState,
        snapshotId: optionalId(event?.payload?.snapshotId ?? event?.payload?.snapshot_id),
      });
      stopReconcileFallback();
      await reconcile({
        query: context().activeSegment === "local" && !pendingVerifiedContext,
      });
      if (disposed) return;
      const pending = pendingVerifiedContext;
      if (pending && activeLocalSnapshotHeader(context().localData)) {
        await establishVerifiedContext(
          pending.componentId,
          pending.installEvidenceId,
          pending.generation,
        );
      }
    } else if (["running", "progress"].includes(terminalState)) {
      scheduleReconcileFallback();
    }
  };

  const start = () => {
    if (disposed
      || eventSubscriptionStarted
      || typeof backend.onLocalScanChanged !== "function") return;
    eventSubscriptionStarted = true;
    let subscription;
    try {
      subscription = backend.onLocalScanChanged(handleScanEvent);
    } catch (_error) {
      eventSubscriptionStarted = false;
      return;
    }
    Promise.resolve(subscription)
      .then((unlisten) => {
        if (typeof unlisten !== "function") return;
        if (disposed) {
          unlisten();
          return;
        }
        releaseScanEvents = unlisten;
      })
      .catch(() => {
        if (!disposed) eventSubscriptionStarted = false;
      });
  };

  const dispose = () => {
    if (disposed) return;
    disposed = true;
    queryGeneration += 1;
    correlationGeneration += 1;
    ignoreGeneration += 1;
    pendingVerifiedContext = null;
    stopReconcileFallback();
    try {
      releaseScanEvents();
    } catch (_error) {
      // Subscription cleanup is best effort during terminal teardown.
    }
    releaseScanEvents = () => {};
  };

  return Object.freeze({
    closeProjectIgnoreEditor,
    dispose,
    ensureSession,
    leaveLocalContext,
    openProjectIgnoreEditor,
    openVerifiedContext,
    refreshQuery,
    refreshScan,
    resetLocalContext,
    saveProjectIgnore,
    start,
  });
}
