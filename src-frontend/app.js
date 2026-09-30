import {
  createInitialState,
  localRuntimeStatusText,
  projectLocalInspectorView,
  renderAppShell,
  renderLocalStatusStack,
  workspaceLayoutStatusPresentation,
  workspacePreferredWidthsText,
} from "./app-shell.js";
import {
  applyResolvedMode,
  patchAppearanceControls,
  reduceAppearanceChanged,
  reduceAppearanceResponse,
  requestAppearanceMode,
} from "./appearance/controller.js";
import {
  applyTypographyPreset,
  bindTypographyMenu,
  reduceTypographyResponse,
  requestTypographyPreset,
} from "./typography/controller.js";
import { normalizeTypographyState } from "./typography/state.js";
import { createBackendClient } from "./backend.js";
import {
  AI_TRANSPORT_WARNING,
  aiProviderTransportWarning,
  createAiExplanationController,
  createAiProviderState,
} from "./ai-explanation.js";
import { createGraphWorkbenchAdapter } from "./graph/graph-workbench-adapter.js";
import { bindOrderedStepInspector } from "./graph/ordered-step-inspector.js";
import {
  bindSotWorkbenchDisclosure,
  SotWorkbenchDisclosureContractError,
} from "./sot-workbench-disclosure.js";
import {
  createSourcePreviewController,
  renderSourcePreviewContent,
} from "./source-preview.js";
import {
  canApplyInstall,
  createInstallRequest,
  createInstallState,
  normalizeApplyInstallResponse,
  normalizeInstallPreviewResponse,
  reduceInstallState,
} from "./install-flow.js";
import { renderInstallActionSurface } from "./install-view.js";
import { bindToolIdentityAssetFallback } from "./tool-identities.js";
import {
  activeLocalSnapshotHeader,
  createLocalViewState,
  localRenderFingerprint,
  reduceLocalScanState,
  reduceLocalView,
} from "./local-state.js";
import {
  acceptLocalRemovalReconciliation,
  acceptLocalRemovalRescanFailure,
  acceptLocalRemovalOutcome,
  acceptLocalRemovalPlan,
  acknowledgeLocalRemovalConfirmation,
  beginLocalRemovalApply,
  beginLocalRemovalPreparation,
  beginLocalRemovalReconciliation,
  dismissLocalRemovalConfirmation,
  enterLocalRemovalSelectionMode,
  exitLocalRemovalSelectionMode,
  openLocalRemovalConfirmation,
  rejectLocalRemovalReconciliation,
  toggleLocalRemovalSelection,
} from "./local-removal-state.js";
import {
  createLocalWorkflowController,
  matchesLocalRequestIdentity,
  normalizeCorrelationProjection,
} from "./local-workflow-controller.js";
import { normalizeWorkspaceLayoutState } from "./layout/state.js";
import {
  renderLocalInspector,
  renderLocalProjectRows,
  verifiedSotComponentId,
} from "./local-view.js";
import {
  createSotViewState,
  reduceSotView,
  renderProfileMembers,
  renderSotInspector,
  renderSotTree,
  UNPROFILED_ID,
} from "./sot-view.js";
import {
  captureSotRenderContinuity,
  restoreSotRenderContinuity,
} from "./sot-render-continuity.js";

export {
  matchesLocalRequestIdentity,
  normalizeCorrelationProjection,
};

function safeOperationError(operation, error) {
  const message = error instanceof Error ? error.message : "";
  if (message === "Tauri runtime에서만 이 작업을 실행할 수 있습니다.") return message;
  if (operation === "register") {
    return "Checkout을 등록하지 못했습니다. 경로와 repository 상태를 확인한 뒤 다시 시도하세요.";
  }
  if (operation === "clone") {
    return "Public HarnessKit clone에 실패했습니다. 네트워크와 app data directory 상태를 확인하세요.";
  }
  if (operation === "preview") {
    return "Install preview를 만들지 못했습니다. 입력값과 checkout 상태를 확인한 뒤 다시 시도하세요.";
  }
  return "Install 응답을 받지 못했습니다. 일부 변경이 남았을 수 있습니다. 대상 상태를 확인하세요.";
}

function projectSotWorkbenchDisclosureFailure(root, error) {
  const diagnostic = String(error?.diagnostic ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  root.setAttribute("data-disclosure-degraded", error.code);
  root.innerHTML = `
    <section class="sot-workbench__degraded notice notice--error" role="alert">
      <strong>중앙 보기를 표시하지 못했습니다.</strong>
      <p>Component Map과 Profile Matrix를 표시하지 못했습니다. 앱을 다시 실행해 주세요.</p>
      ${diagnostic ? `<p class="sr-only" data-component-map-diagnostic>Component Map diagnostic: ${diagnostic}</p>` : ""}
    </section>
  `;
}

const LOCAL_ACTION_COPY = Object.freeze({
  reveal: Object.freeze({
    pending: { code: "reveal_pending", message: "Finder에서 위치를 확인하는 중입니다." },
    success: { code: "revealed", message: "Finder에서 위치를 표시했습니다." },
  }),
  copy_path: Object.freeze({
    pending: { code: "copy_path_pending", message: "경로를 복사하는 중입니다." },
    success: { code: "path_copied", message: "경로를 클립보드에 복사했습니다." },
  }),
});

const LOCAL_ACTION_ERRORS = Object.freeze({
  stale_path_handle: "항목이 스캔 이후 변경되었습니다. 다시 스캔하세요.",
  snapshot_expired: "사용 중인 snapshot이 만료되었습니다. 다시 스캔하세요.",
  instance_handle_missing: "선택한 항목을 현재 snapshot에서 찾을 수 없습니다.",
  reveal_unavailable: "이 환경에서는 Finder에서 보기를 사용할 수 없습니다.",
  reveal_failed: "Finder에서 위치를 표시하지 못했습니다.",
  copy_path_unavailable: "이 환경에서는 경로 복사를 사용할 수 없습니다.",
  copy_path_failed: "경로를 클립보드에 복사하지 못했습니다.",
  operation_busy: "설치 작업이 진행 중입니다. 완료된 뒤 다시 시도하세요.",
  path_action_capacity_exhausted: "동시에 처리할 수 있는 Local action 수를 초과했습니다.",
});

function localActionPending(action) {
  const copy = LOCAL_ACTION_COPY[action]?.pending
    ?? { code: "local_action_pending", message: "Local action을 처리하는 중입니다." };
  return { state: "pending", ...copy };
}

function hasExactKeys(value, expectedKeys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const observed = Object.keys(value).sort();
  const expected = [...expectedKeys].sort();
  return observed.length === expected.length
    && observed.every((key, index) => key === expected[index]);
}

function normalizeLocalActionOutcome(action, outcome) {
  const expected = LOCAL_ACTION_COPY[action]?.success;
  if (!expected || outcome?.outcome !== expected.code || !hasExactKeys(outcome, ["outcome"])) {
    return { status: {
      state: "error",
      code: "unexpected_action_outcome",
      message: "Local action 결과를 확인하지 못했습니다.",
    } };
  }
  return {
    status: { state: "success", ...expected },
  };
}

function localActionFailure(error) {
  const requestedCode = String(error?.code ?? "").trim();
  const message = LOCAL_ACTION_ERRORS[requestedCode];
  if (!message) {
    return {
      state: "error",
      code: "local_action_failed",
      message: "요청한 read-only Local action을 수행하지 못했습니다.",
    };
  }
  return { state: "error", code: requestedCode, message };
}

function selectedSotComponent(state) {
  return state.sot?.snapshot?.components?.find(
    (component) => component.component_id === state.sotView?.selectedComponentId,
  ) ?? null;
}

function firstTargetId(component) {
  const first = Array.isArray(component?.targets) ? component.targets[0] : null;
  return String(first?.targetId ?? first?.target_id ?? first?.id ?? "").trim();
}

function installProfileId(component, activeProfileId) {
  const profiles = Array.isArray(component?.profileIds ?? component?.profile_ids)
    ? component.profileIds ?? component.profile_ids
    : [];
  if (profiles.includes(activeProfileId)) return activeProfileId;
  return String(profiles[0] ?? "").trim();
}

function readInstallForm(form) {
  const value = (name) => String(form?.elements?.namedItem?.(name)?.value ?? "").trim();
  return {
    profileId: value("profileId"),
    targetId: value("targetId"),
    scope: value("scope") || "user",
    targetRoot: value("targetRoot"),
  };
}

function optionalId(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

export function focusWorkflowOverview(graphSession, workflowId) {
  const normalizedWorkflowId = optionalId(workflowId);
  if (!normalizedWorkflowId) return;
  graphSession?.focusNode?.(`workflow:${normalizedWorkflowId}`);
}

function localSelectedRowViewportAnchor(root, workbench, instanceId) {
  if (!root || !workbench || !instanceId) return null;
  const selected = [...(root.querySelectorAll?.("[data-local-instance]") ?? [])]
    .find((control) => control.dataset?.localInstance === instanceId);
  const workbenchRect = workbench.getBoundingClientRect?.();
  const selectedRect = selected?.getBoundingClientRect?.();
  const offset = selectedRect?.top - workbenchRect?.top;
  return Number.isFinite(offset) ? { instanceId, offset } : null;
}

function activeInstallEvidenceId(state) {
  return optionalId(state.sot?.installEvidenceId);
}

function createStatusAnnouncer(root, datasetKey) {
  const document = root?.ownerDocument;
  if (!document?.createElement || !document.body?.appendChild) return null;
  const announcer = document.createElement("p");
  announcer.className = "sr-only";
  announcer.dataset[datasetKey] = "";
  announcer.setAttribute("role", "status");
  announcer.setAttribute("aria-live", "polite");
  announcer.setAttribute("aria-atomic", "true");
  announcer.textContent = "";
  document.body.appendChild(announcer);
  return announcer;
}

function aiFocusTarget(element) {
  const dataset = element?.dataset ?? {};
  if (dataset.aiExplain !== undefined) return { selector: "[data-ai-explain]" };
  if (dataset.openAiSettings !== undefined) return { selector: "[data-open-ai-settings]" };
  if (dataset.aiProviderClose !== undefined) return { selector: "[data-ai-provider-close]" };
  if (dataset.aiProviderKeyDelete !== undefined) {
    return {
      selector: "[data-ai-provider-key-delete]",
      fallback: "[data-ai-provider-close]",
    };
  }
  if (element?.type === "submit") {
    return { selector: '[data-ai-provider-form] button[type="submit"]' };
  }
  if (["baseUrl", "model", "apiKey"].includes(element?.name)) {
    return {
      selector: `[data-ai-provider-form] input[name="${element.name}"]`,
      fallback: '[data-ai-provider-form] input[name="baseUrl"]',
    };
  }
  return null;
}

function restoreAiFocus(root, target) {
  if (!target) return;
  const control = root.querySelector(target.selector)
    ?? (target.fallback ? root.querySelector(target.fallback) : null);
  control?.focus();
}

export function mountApp(root, backend = createBackendClient(), options = {}) {
  let state = createInitialState({
    appearance: options.appearance,
    workspaceLayout: options.workspaceLayout,
    typography: options.typography,
  });
  const workspaceLayoutController = options.workspaceLayoutController ?? {
    syncAfterRender() {},
  };
  const createGraphSession = options.createComponentMapSession ?? createGraphWorkbenchAdapter;
  const localActionAnnouncer = createStatusAnnouncer(root, "localActionAnnouncer");
  const aiStatusAnnouncer = createStatusAnnouncer(root, "aiStatusAnnouncer");
  const releaseToolIdentityFallback = bindToolIdentityAssetFallback(root);
  let destroyed = false;
  let graphSession = null;
  let graphSessionSnapshotId = null;
  const syncGraphSessionAppearance = () => {
    const resolvedMode = String(state.appearance?.resolved_mode ?? "").toLowerCase();
    if (!['light', 'dark'].includes(resolvedMode)) return;
    graphSession?.setResolvedAppearance?.(resolvedMode);
  };
  let workflowInspectorBinding = { release() {} };
  let typographyMenuBinding = { sync() {}, release() {} };
  let workbenchDisclosure = {
    sync() {},
    release() {},
  };
  const ownerWindow = root.ownerDocument?.defaultView;
  const scheduleAnimationFrame = options.requestAnimationFrame
    ?? ownerWindow?.requestAnimationFrame?.bind(ownerWindow)
    ?? globalThis.requestAnimationFrame?.bind(globalThis);
  const cancelAnimationFrame = options.cancelAnimationFrame
    ?? ownerWindow?.cancelAnimationFrame?.bind(ownerWindow)
    ?? globalThis.cancelAnimationFrame?.bind(globalThis);
  let pendingLocalScrollRestore = null;
  const cancelPendingLocalScrollRestore = () => {
    const pendingRestore = pendingLocalScrollRestore;
    if (pendingRestore?.handle !== null
      && pendingRestore?.handle !== undefined
      && typeof cancelAnimationFrame === "function") {
      cancelAnimationFrame(pendingRestore.handle);
    }
    pendingLocalScrollRestore = null;
  };
  let localDetailGeneration = 0;
  let localActionGeneration = 0;
  let localSelectionGeneration = 0;
  let localRemovalPreparationGeneration = 0;
  let localRemovalApplyGeneration = 0;
  let localRemovalReconciliationGeneration = 0;
  let installGeneration = 0;
  let sotGeneration = 0;
  let repoGeneration = 0;
  let checkoutSubmissionPending = false;
  let checkoutPickerPending = false;
  let pendingSavedRestore = null;
  const segmentScrollPositions = new Map([
    ["sot", 0],
    ["local", 0],
  ]);
  let aiProviderGeneration = 0;
  let syncAiExplanationBinding = () => {};

  const syncLocalActionAnnouncer = () => {
    const activeStatus = state.localView.actionStatus?.instanceId
      === state.localView.selectedInstanceId
      ? state.localView.actionStatus
      : null;
    const announcement = activeStatus?.message ?? "";
    if (localActionAnnouncer && localActionAnnouncer.textContent !== announcement) {
      localActionAnnouncer.textContent = announcement;
    }
  };

  const focusedLocalActionTarget = (dataset) => LOCAL_ACTION_COPY[dataset?.localAction]
    ? {
      action: dataset.localAction,
      instanceId: dataset.instanceId,
      snapshotId: dataset.snapshotId,
    }
    : null;

  const captureLocalFocusTarget = ({ retainLocalAction = true } = {}) => {
    const activeElement = root.ownerDocument?.activeElement;
    return {
      retainedAiFocus: aiFocusTarget(activeElement),
      restoreSourceRangeFocus: activeElement?.matches?.("[data-source-chunk-range]") === true,
      focusedLocalAction: retainLocalAction
        ? focusedLocalActionTarget(activeElement?.dataset)
        : null,
    };
  };

  const restoreLocalFocusTarget = ({
    retainedAiFocus,
    restoreSourceRangeFocus,
    focusedLocalAction,
  }) => {
    restoreAiFocus(root, retainedAiFocus);
    if (focusedLocalAction) {
      [...root.querySelectorAll("[data-local-action]")]
        .find((control) => control.dataset.localAction === focusedLocalAction.action
          && control.dataset.instanceId === focusedLocalAction.instanceId
          && control.dataset.snapshotId === focusedLocalAction.snapshotId)
        ?.focus();
    }
    if (restoreSourceRangeFocus) {
      root.querySelector("[data-source-chunk-range]")?.focus();
    }
  };

  const patchLocalSelectionSurface = () => {
    if (destroyed || state.ui?.activeSegment !== "local") return false;
    const inspector = root.querySelector("[data-local-inspector]");
    if (!inspector) return false;
    const focusTarget = captureLocalFocusTarget();
    [...root.querySelectorAll("[data-local-instance]")].forEach((control) => {
      const selected = control.dataset.localInstance === state.localView.selectedInstanceId;
      control.classList.toggle("local-result-panel--selected", selected);
      control.setAttribute("aria-pressed", String(selected));
    });
    inspector.innerHTML = renderLocalInspector(state.localData, projectLocalInspectorView(state));
    const sourceHost = inspector.querySelector?.("[data-local-source-content]")
      ?? root.querySelector("[data-local-source-content]");
    if (sourceHost) renderSourcePreviewContent(sourceHost, state.sourcePreview);
    const statusStack = root.querySelector("[data-local-status-stack]");
    if (statusStack) statusStack.innerHTML = renderLocalStatusStack(state.localData);
    const runtimeStatus = root.querySelector("#local-runtime-status");
    if (runtimeStatus) runtimeStatus.textContent = localRuntimeStatusText(state.localData);
    restoreLocalFocusTarget(focusTarget);
    syncLocalActionAnnouncer();
    return true;
  };

  const updateLocalSelectionSurface = (nextState) => {
    if (destroyed) return;
    state = nextState;
    syncAiExplanationBinding();
    syncLocalActionAnnouncer();
    if (!patchLocalSelectionSurface()) render();
  };

  const update = (nextState) => {
    if (destroyed) return;
    state = nextState;
    syncAiExplanationBinding();
    syncLocalActionAnnouncer();
    render();
  };

  const sourcePreviewController = createSourcePreviewController({
    backend,
    onChange(sourcePreview) {
      if (destroyed) return;
      updateLocalSelectionSurface({ ...state, sourcePreview });
    },
  });

  const aiExplanationController = createAiExplanationController({
    backend,
    onChange(aiExplanation) {
      if (destroyed) return;
      updateLocalSelectionSurface({ ...state, aiExplanation });
      if (aiStatusAnnouncer) aiStatusAnnouncer.textContent = aiExplanation.message ?? "";
    },
  });

  const activeAiBinding = () => {
    const provider = state.aiProvider;
    const preview = state.sourcePreview;
    const header = preview?.header;
    if (state.ui?.activeSegment !== "local"
      || provider?.state !== "configured"
      || preview?.phase !== "ready"
      || header?.snapshotId !== activeLocalSnapshotHeader(state.localData)?.snapshotId
      || header?.instanceId !== state.localView?.selectedInstanceId) return null;
    const binding = {
      snapshotId: optionalId(header.snapshotId),
      instanceId: optionalId(header.instanceId),
      sourceRevision: optionalId(header.sourceRevision),
      providerRevision: optionalId(provider.providerRevision),
    };
    return Object.values(binding).every(Boolean) ? binding : null;
  };

  syncAiExplanationBinding = () => {
    const binding = activeAiBinding();
    if (binding) aiExplanationController.bind(binding);
    else if (aiExplanationController.getState().binding) aiExplanationController.clear();
  };

  const loadAiProvider = async () => {
    if (destroyed || typeof backend.getAiProviderConfig !== "function") return;
    const generation = ++aiProviderGeneration;
    try {
      const value = await backend.getAiProviderConfig();
      if (destroyed || generation !== aiProviderGeneration) return;
      update({
        ...state,
        aiProvider: createAiProviderState(value),
        ui: { ...state.ui, aiProviderMessage: "" },
      });
    } catch (_error) {
      if (destroyed || generation !== aiProviderGeneration) return;
      update({
        ...state,
        aiProvider: createAiProviderState(),
        ui: {
          ...state.ui,
          aiProviderMessage: "AI 연결 설정을 안전하게 불러오지 못했습니다.",
        },
      });
    }
  };

  const saveAiProvider = async (form) => {
    if (destroyed
      || state.ui.aiProviderBusy === true
      || typeof backend.saveAiProviderConfig !== "function") return;
    const field = (name) => form?.elements?.namedItem?.(name) ?? null;
    const value = (name) => String(field(name)?.value ?? "");
    const apiKeyInput = field("apiKey");
    const request = {
      expectedProviderRevision: state.aiProvider?.state === "configured"
        ? state.aiProvider.providerRevision
        : null,
      baseUrl: value("baseUrl").trim(),
      model: value("model").trim(),
      apiKey: value("apiKey"),
    };
    if (apiKeyInput) apiKeyInput.value = "";
    const generation = ++aiProviderGeneration;
    update({
      ...state,
      ui: {
        ...state.ui,
        aiProviderBusy: true,
        aiProviderMessage: "AI 연결 설정을 저장하고 있습니다.",
      },
    });
    try {
      const value = await backend.saveAiProviderConfig(request);
      if (destroyed || generation !== aiProviderGeneration) return;
      update({
        ...state,
        aiProvider: createAiProviderState(value),
        ui: {
          ...state.ui,
          aiProviderBusy: false,
          aiProviderMessage: "AI 연결 설정을 저장했습니다.",
        },
      });
      root.querySelector('[data-ai-provider-form] input[name="baseUrl"]')?.focus();
    } catch (error) {
      if (destroyed || generation !== aiProviderGeneration) return;
      const stale = error?.code === "provider_stale";
      update({
        ...state,
        ui: {
          ...state.ui,
          aiProviderBusy: false,
          aiProviderMessage: stale
            ? "AI 연결 설정이 변경되었습니다. 최신 설정을 다시 불러오세요."
            : "AI 연결 설정을 저장하지 못했습니다.",
        },
      });
    }
  };

  const deleteAiProviderKey = async () => {
    if (destroyed
      || state.ui.aiProviderBusy === true
      || state.aiProvider?.state !== "configured"
      || typeof backend.deleteAiProviderKey !== "function") return;
    const generation = ++aiProviderGeneration;
    update({
      ...state,
      ui: {
        ...state.ui,
        aiProviderBusy: true,
        aiProviderMessage: "저장된 API key를 삭제하고 있습니다.",
      },
    });
    try {
      const value = await backend.deleteAiProviderKey(state.aiProvider.providerRevision);
      if (destroyed || generation !== aiProviderGeneration) return;
      update({
        ...state,
        aiProvider: createAiProviderState(value),
        ui: {
          ...state.ui,
          aiProviderBusy: false,
          aiProviderMessage: "저장된 API key를 삭제했습니다.",
        },
      });
    } catch (_error) {
      if (destroyed || generation !== aiProviderGeneration) return;
      update({
        ...state,
        ui: {
          ...state.ui,
          aiProviderBusy: false,
          aiProviderMessage: "저장된 API key를 삭제하지 못했습니다.",
        },
      });
    }
  };

  const commitLocalActionSurface = (nextState) => {
    if (destroyed) return;
    state = nextState;
    const busy = state.ui.localActionBusy === true;
    const group = root.querySelector(".local-read-actions");
    group?.setAttribute("aria-busy", String(busy));
    root.querySelectorAll("[data-local-action]").forEach((control) => {
      control.setAttribute("aria-disabled", String(busy));
    });

    const status = state.localView.actionStatus;
    const active = status?.instanceId === state.localView.selectedInstanceId;
    syncLocalActionAnnouncer();
    const statusNode = root.querySelector("[data-local-action-status]");
    if (statusNode) {
      const visualState = active ? status.state : "idle";
      ["idle", "pending", "success", "error"].forEach((candidate) => {
        statusNode.classList.toggle(
          `local-action-status--${candidate}`,
          candidate === visualState,
        );
      });
      statusNode.dataset.localActionState = visualState;
      statusNode.dataset.localActionCode = active ? status.code : "none";
      statusNode.setAttribute(
        "aria-label",
        active ? `${status.message} · code ${status.code}` : "Local action 상태 없음",
      );
      statusNode.hidden = !active;
      statusNode.textContent = active ? status.message : "";
    }
  };

  const applyAppearanceState = (nextState) => {
    if (destroyed || nextState === state) return;
    state = nextState;
    if (options.documentRoot) {
      applyResolvedMode(options.documentRoot, state.appearance);
    }
    patchAppearanceControls(root, state.appearance);
    syncGraphSessionAppearance();
  };

  const applyTypographyState = (nextState) => {
    if (destroyed || nextState === state) return;
    state = nextState;
    applyTypographyPreset(options.documentRoot, state.typography);
    typographyMenuBinding.sync(state.typography);
  };

  const selectTypographyPreset = async (preset) => {
    try {
      const response = await requestTypographyPreset({
        preset,
        expectedRevision: state.typography.revision,
        backend,
      });
      applyTypographyState(reduceTypographyResponse(state, response));
    } catch (_error) {
      applyTypographyState({
        ...state,
        typography: {
          ...state.typography,
          diagnostic: {
            code: "typography_update_failed",
            safe_message: "글자 크기 설정을 저장하지 못했습니다.",
          },
        },
      });
    }
  };

  let localRemovalOpener = null;
  const restoreLocalRemovalOpener = () => {
    const fallback = state.localRemoval?.selectionMode === "selecting"
      ? root.querySelector("[data-prepare-local-removal]")
      : root.querySelector("[data-enter-local-removal]");
    const opener = localRemovalOpener?.isConnected === false ? fallback : localRemovalOpener ?? fallback;
    opener?.focus?.();
    localRemovalOpener = null;
  };
  const pendingLocalRemovalTerminals = new Map();
  const rememberPendingLocalRemovalTerminal = (terminal) => {
    const attemptId = String(terminal?.attemptId ?? "").trim();
    const terminalState = String(terminal?.state ?? "").trim().toLowerCase();
    if (!attemptId || !["complete", "partial", "failed"].includes(terminalState)) return;
    if (pendingLocalRemovalTerminals.has(attemptId)) {
      pendingLocalRemovalTerminals.delete(attemptId);
    }
    while (pendingLocalRemovalTerminals.size >= 4) {
      const oldestAttemptId = pendingLocalRemovalTerminals.keys().next().value;
      pendingLocalRemovalTerminals.delete(oldestAttemptId);
    }
    pendingLocalRemovalTerminals.set(attemptId, {
      attemptId,
      state: terminalState,
      snapshotId: String(terminal?.snapshotId ?? "").trim() || null,
    });
  };
  const takePendingLocalRemovalTerminal = (attemptId) => {
    const normalizedAttemptId = String(attemptId ?? "").trim();
    const terminal = pendingLocalRemovalTerminals.get(normalizedAttemptId) ?? null;
    pendingLocalRemovalTerminals.clear();
    return terminal;
  };
  const currentLocalRemovalTerminal = () => {
    const terminal = state.localData?.latestTerminalReport;
    if (!terminal?.attemptId || !terminal?.state) return null;
    let snapshot = null;
    if (terminal.state === "complete") {
      snapshot = state.localData.latestComplete;
    } else if (terminal.state === "partial") {
      snapshot = state.localData.latestPartial;
    }
    return {
      attemptId: terminal.attemptId,
      state: terminal.state,
      snapshotId: snapshot?.snapshotId ?? null,
    };
  };
  const handleLocalRemovalRescanTerminal = (terminal) => {
    const terminalState = String(terminal?.state ?? "").toLowerCase();
    const attemptId = String(terminal?.attemptId ?? "").trim();
    if (!attemptId) return;
    if (state.localRemoval.phase === "applying") {
      rememberPendingLocalRemovalTerminal(terminal);
      return;
    }
    if (terminalState === "failed") {
      const localRemoval = acceptLocalRemovalRescanFailure(state.localRemoval, attemptId);
      if (localRemoval === state.localRemoval) return;
      update({ ...state, localRemoval });
      restoreLocalRemovalOpener();
      return;
    }
    if (!["complete", "partial"].includes(terminalState)) return;
    const started = beginLocalRemovalReconciliation(
      state.localRemoval,
      terminal?.snapshotId,
      attemptId,
    );
    if (!started.request) {
      if (started.state !== state.localRemoval) {
        update({ ...state, localRemoval: started.state });
        restoreLocalRemovalOpener();
      }
      return;
    }
    const generation = ++localRemovalReconciliationGeneration;
    update({ ...state, localRemoval: started.state });
    if (typeof backend.reconcileLocalRemoval !== "function") {
      const localRemoval = rejectLocalRemovalReconciliation(state.localRemoval, started.token);
      update({ ...state, localRemoval });
      restoreLocalRemovalOpener();
      return;
    }
    void Promise.resolve(backend.reconcileLocalRemoval(started.request))
      .then((response) => {
        if (destroyed || generation !== localRemovalReconciliationGeneration) return;
        const localRemoval = acceptLocalRemovalReconciliation(
          state.localRemoval,
          response,
          started.token,
        );
        update({ ...state, localRemoval });
        restoreLocalRemovalOpener();
      })
      .catch(() => {
        if (destroyed || generation !== localRemovalReconciliationGeneration) return;
        const localRemoval = rejectLocalRemovalReconciliation(state.localRemoval, started.token);
        update({ ...state, localRemoval });
        restoreLocalRemovalOpener();
      });
  };

  const localWorkflowController = createLocalWorkflowController({
    backend,
    readContext: () => ({
      localData: state.localData,
      localView: state.localView,
      activeSegment: state.ui.activeSegment,
      sotSnapshotId: state.sot?.snapshot?.snapshot_id,
      installEvidenceId: activeInstallEvidenceId(state),
      ignoreEditor: state.ui?.ignoreEditor,
    }),
    commitLocalSlices({ localData, localView }) {
      if (destroyed) return;
      const nextState = { ...state, localData, localView };
      if (state.ui.activeSegment === "local") update(nextState);
      else {
        state = nextState;
        syncLocalActionAnnouncer();
      }
    },
    commitIgnoreEditor(ignoreEditor) {
      if (destroyed) return;
      update({ ...state, ui: { ...state.ui, ignoreEditor } });
    },
    onSnapshotReplaced() {
      localDetailGeneration += 1;
      localActionGeneration += 1;
      localSelectionGeneration += 1;
      localRemovalPreparationGeneration += 1;
      localRemovalReconciliationGeneration += 1;
      if (state.localRemoval.phase === "reconciling") {
        const localRemoval = rejectLocalRemovalReconciliation(state.localRemoval);
        if (localRemoval !== state.localRemoval) {
          pendingLocalRemovalTerminals.clear();
          state = { ...state, localRemoval };
          restoreLocalRemovalOpener();
        }
      }
    },
    onTerminalScanEvent(terminal) {
      handleLocalRemovalRescanTerminal(terminal);
    },
    onIgnoreEditorReady() {
      root.querySelector("#project-ignore-text")?.focus();
    },
    setTimeout: options.setTimeout,
    clearTimeout: options.clearTimeout,
    reconcileIntervalMs: options.localReconcileIntervalMs,
  });

  const beginCorrelationRevisionAttempt = () => {
    segmentScrollPositions.set("sot", 0);
    const invalidated = localWorkflowController.resetLocalContext();
    return {
      ...state,
      ...invalidated,
      ui: {
        ...state.ui,
        sotDeparture: null,
      },
    };
  };

  const isCurrentLocalIdentity = (request) => matchesLocalRequestIdentity(
    state.localData,
    state.localView,
    request,
  );

  const loadLocalDetail = async (instanceId) => {
    if (destroyed || typeof backend.getLocalInstanceDetail !== "function") return;
    const snapshot = activeLocalSnapshotHeader(state.localData);
    if (!snapshot || state.localView.selectedInstanceId !== instanceId) return;
    const request = {
      snapshotId: snapshot.snapshotId,
      correlationProjectionId: state.localView.correlationProjectionId,
      instanceId,
    };
    const generation = ++localDetailGeneration;
    try {
      const detail = await backend.getLocalInstanceDetail({
        ...request,
      });
      if (destroyed
        || generation !== localDetailGeneration
        || !isCurrentLocalIdentity(request)) return;
      updateLocalSelectionSurface({
        ...state,
        localData: reduceLocalScanState(state.localData, { type: "detail_result", detail }),
      });
    } catch (_error) {
      if (destroyed
        || generation !== localDetailGeneration
        || !isCurrentLocalIdentity(request)) return;
      updateLocalSelectionSurface({
        ...state,
        localData: {
          ...state.localData,
          message: "선택한 Local instance detail을 불러오지 못했습니다.",
        },
      });
    }
  };

  const loadSotSnapshot = async (checkoutId, options = {}) => {
    if (destroyed) return;
    let checkoutFocusOwned = options.preserveCheckoutFocus === true;
    const updateWithCheckoutFocus = (nextState) => {
      const activeElement = root.ownerDocument?.activeElement;
      const checkoutInput = root.querySelector("#checkout-path");
      if (checkoutFocusOwned
        && activeElement
        && activeElement !== checkoutInput
        && activeElement !== root.ownerDocument?.body) {
        checkoutFocusOwned = false;
      }
      update(nextState);
      if (checkoutFocusOwned) root.querySelector("#checkout-path")?.focus();
    };
    const generation = ++sotGeneration;
    installGeneration += 1;
    const invalidated = beginCorrelationRevisionAttempt();
    updateWithCheckoutFocus({
      ...invalidated,
      sot: {
        phase: "loading",
        message: "SoT snapshot을 읽고 있습니다.",
        snapshot: null,
        installEvidenceId: null,
      },
      install: createInstallState(),
    });
    if (state.ui.activeSegment === "local") {
      void localWorkflowController.refreshQuery();
    }
    let snapshot;
    let refreshedView;
    try {
      snapshot = await backend.loadSotSnapshot(checkoutId);
      if (destroyed
        || generation !== sotGeneration
        || state.repo.checkoutId !== checkoutId) return;
      if (!snapshot?.snapshot_id || !Array.isArray(snapshot.components)) {
        throw new Error("invalid snapshot");
      }
      refreshedView = createSotViewState(snapshot, state.sotView);
      if (!snapshot.components.some(
        (component) => component.component_id === refreshedView.selectedComponentId,
      )) {
        refreshedView.selectedComponentId = null;
      }
      const validProfileIds = new Set([
        ...(snapshot.profiles ?? []).map((profile) => profile.profile_id),
        "__unprofiled__",
      ]);
      if (!validProfileIds.has(refreshedView.activeProfileId)) {
        refreshedView.activeProfileId = snapshot.profiles?.[0]?.profile_id ?? "__unprofiled__";
      }
    } catch (_error) {
      if (destroyed
        || generation !== sotGeneration
        || state.repo.checkoutId !== checkoutId) return;
      updateWithCheckoutFocus({
        ...state,
        sot: {
          phase: "error",
          message: "SoT snapshot을 불러오지 못했습니다. checkout과 registry source를 확인하세요.",
          snapshot: null,
          installEvidenceId: null,
        },
      });
      return;
    }
    updateWithCheckoutFocus({
      ...state,
      sot: {
        phase: "ready",
        message: `${snapshot.components.length}개 registry component를 불러왔습니다.`,
        snapshot,
        installEvidenceId: null,
      },
      sotView: refreshedView,
      install: createInstallState(),
    });
  };

  const applySavedRestore = (outcome, focusCheckoutInput = false) => {
    if (!outcome || outcome.generation !== repoGeneration || destroyed) return;
    const preserveCheckoutFocus = focusCheckoutInput
      || root.ownerDocument?.activeElement === root.querySelector("#checkout-path");
    repoGeneration += 1;
    if (outcome.registration) {
      const registration = outcome.registration;
      update({
        ...state,
        repo: {
          checkoutPath: registration.canonical_path ?? "",
          phase: "registered",
          checkoutId: registration.checkout_id,
          canonicalPath: registration.canonical_path ?? "",
          repoStatus: registration.repo_status ?? null,
          message: "저장된 HarnessKit checkout을 복원했습니다.",
        },
      });
      if (preserveCheckoutFocus) {
        root.querySelector("#checkout-path")?.focus();
      }
      void loadSotSnapshot(registration.checkout_id, {
        preserveCheckoutFocus,
      });
      return;
    }
    update({
      ...state,
      repo: {
        ...state.repo,
        phase: "error",
        message: "저장된 checkout 상태를 불러오지 못했습니다. Checkout을 다시 등록해 주세요.",
      },
    });
    if (preserveCheckoutFocus) root.querySelector("#checkout-path")?.focus();
  };

  const applyPendingSavedRestore = (focusCheckoutInput = false) => {
    const pending = pendingSavedRestore;
    pendingSavedRestore = null;
    applySavedRestore(pending, focusCheckoutInput);
  };

  const patchInstallSurface = () => {
    const host = root.querySelector("[data-sot-install-host]");
    if (!host) return;
    host.innerHTML = renderInstallActionSurface({
      install: state.install,
      component: selectedSotComponent(state),
      operationBlocked: state.localData?.phase === "running",
    });
  };

  const commitInstall = (install, installEvidenceId = undefined) => {
    state = {
      ...state,
      install,
      sot: installEvidenceId === undefined
        ? state.sot
        : { ...state.sot, installEvidenceId: optionalId(installEvidenceId) },
    };
    patchInstallSurface();
  };

  const installStateForSotView = (sotView, previousInstall = state.install) => {
    const component = state.sot.snapshot?.components?.find(
      (candidate) => candidate.component_id === sotView.selectedComponentId,
    );
    if (!component) {
      return reduceInstallState(previousInstall, {
        type: "select_subject",
        sotSnapshotId: state.sot.snapshot?.snapshot_id ?? null,
        componentId: null,
        profileId: sotView.activeProfileId,
        targetId: null,
      });
    }
    return reduceInstallState(previousInstall, {
      type: "select_subject",
      sotSnapshotId: state.sot.snapshot?.snapshot_id ?? null,
      componentId: component.component_id,
      profileId: installProfileId(component, sotView.activeProfileId),
      targetId: firstTargetId(component),
    });
  };

  const graphActionTransition = (action) => {
    if (!state.sot.snapshot) return;
    const previousSotView = state.sotView;
    const sotView = reduceSotView(state.sotView, action, state.sot.snapshot);
    if (sotView === state.sotView) return null;
    return { previousSotView, sotView };
  };

  const graphSelectionUnchanged = (previousSotView, sotView) => (
    previousSotView.selectedComponentId === sotView.selectedComponentId
    && previousSotView.selectedRelationNodeId === sotView.selectedRelationNodeId
    && previousSotView.selectedWorkflowId === sotView.selectedWorkflowId
    && previousSotView.activeProfileId === sotView.activeProfileId
    && previousSotView.lockedWorkflowStep === sotView.lockedWorkflowStep
    && previousSotView.hoveredGraphNodeId === sotView.hoveredGraphNodeId
    && previousSotView.hoveredWorkflowStep === sotView.hoveredWorkflowStep
    && previousSotView.graphPresentation?.focusNodeId === sotView.graphPresentation?.focusNodeId
  );

  const resolveGraphSelection = ({ action, intent } = {}) => {
    if (![
      "select_graph_node",
      "select_graph_relation",
      "clear_graph_selection",
    ].includes(action?.type)) return false;
    const transition = graphActionTransition(action);
    if (!transition || graphSelectionUnchanged(
      transition.previousSotView,
      transition.sotView,
    )) return false;

    const nodeId = optionalId(intent?.nodeId);
    if (nodeId && action.type !== "clear_graph_selection") {
      return {
        layout: "preserve",
        camera: { kind: "contextual", durationMs: 240 },
      };
    }
    if (!nodeId && action.type === "clear_graph_selection") {
      return {
        layout: "preserve",
        camera: { kind: "restore", durationMs: 240 },
      };
    }
    return false;
  };

  const applyGraphAction = (action) => {
    const transition = graphActionTransition(action);
    if (!transition) return;
    const { previousSotView, sotView } = transition;
    if (["hover_graph_node", "hover_workflow_step"].includes(action.type)) {
      state = { ...state, sotView };
      graphSession?.syncPresentation(sotView);
      return;
    }
    const install = installStateForSotView(sotView);
    if (install !== state.install) installGeneration += 1;
    state = { ...state, sotView, install };
    patchSotSelection({
      profileChanged: sotView.activeProfileId !== previousSotView.activeProfileId,
    });
  };

  const bindWorkflowInspector = () => {
    workflowInspectorBinding.release();
    const workflowInspectorRoot = state.sotView.selectedWorkflowId
      ? root.querySelector("[data-sot-inspector]")
      : null;
    workflowInspectorBinding = workflowInspectorRoot
      ? bindOrderedStepInspector(workflowInspectorRoot, {
        onStepHover(step) {
          applyGraphAction(step
            ? { type: "hover_workflow_step", step }
            : { type: "hover_workflow_step", step: null });
        },
        onStepSelect({ workflowId, ordinal }) {
          applyGraphAction({ type: "select_workflow_step", workflowId, ordinal });
        },
        onWorkflowOverview(workflowId) {
          applyGraphAction({ type: "workflow_overview", workflowId });
          focusWorkflowOverview(graphSession, workflowId);
        },
      })
      : { release() {} };
  };

  const bindSotTreeScroll = () => {
    const treeScroll = root.querySelector("[data-sot-tree-scroll]");
    if (!treeScroll) return;
    const filtered = String(state.sotView.filter ?? "").trim().length > 0;
    const offset = filtered
      ? state.sotView.filteredTreeScrollTop
      : state.sotView.unfilteredTreeScrollTop;
    treeScroll.scrollTop = Number.isFinite(offset) ? offset : 0;
    treeScroll.addEventListener?.("scroll", () => {
      state = {
        ...state,
        sotView: reduceSotView(state.sotView, {
          type: "set_tree_scroll",
          scrollTop: treeScroll.scrollTop,
        }, state.sot.snapshot),
      };
    });
  };

  const patchSotSelection = ({
    profileChanged = false,
    filterChanged = false,
    treeChanged = false,
    focusTreeId = null,
  } = {}) => {
    const snapshot = state.sot.snapshot;
    if (!snapshot) return;
    const selectedId = state.sotView.selectedComponentId;
    root.querySelectorAll("[data-profile-id]").forEach((panel) => {
      const profileId = panel.dataset.profileId;
      const ownsSelected = selectedId && (profileId === UNPROFILED_ID
        ? snapshot.unprofiled_component_ids.includes(selectedId)
        : snapshot.profiles.find((profile) => profile.profile_id === profileId)?.component_ids.includes(selectedId));
      panel.classList.toggle("profile-panel--active", profileId === state.sotView.activeProfileId);
      panel.classList.toggle("profile-panel--owns-selected", Boolean(ownsSelected));
      panel.dataset.owningSelected = String(Boolean(ownsSelected));
      const active = profileId === state.sotView.activeProfileId;
      if (panel.dataset.profileLabel) {
        panel.setAttribute("aria-label", `${panel.dataset.profileLabel} · ${active ? "현재 profile" : "profile"}${ownsSelected ? " · 선택 component 포함" : ""}`);
      }
      panel.setAttribute("aria-pressed", String(active));
      if (panel.dataset.graphNode) {
        panel.dataset.graphStatus = active ? "active" : "available";
      }
      const ownerStatus = panel.querySelector(".profile-owner-status");
      if (ownerStatus) {
        ownerStatus.hidden = !ownsSelected;
        ownerStatus.textContent = ownsSelected ? "선택 component 포함" : "";
      }
    });
    if (profileChanged) {
      const memberRegion = root.querySelector(".profile-member-region");
      if (memberRegion) memberRegion.innerHTML = renderProfileMembers(snapshot, state.sotView);
    }
    if (filterChanged || treeChanged) {
      const tree = root.querySelector("[data-sot-tree]");
      if (tree) tree.innerHTML = renderSotTree(snapshot, state.sotView);
      bindSotTreeScroll();
      if (focusTreeId) {
        const focusTarget = [...root.querySelectorAll("[data-sot-tree] [role=treeitem]")]
          .find((item) => item.dataset.componentId === focusTreeId
            || item.dataset.sotTreeNode === focusTreeId);
        focusTarget?.focus();
        focusTarget?.scrollIntoView?.({ block: "nearest" });
      }
    }
    const componentControls = [...root.querySelectorAll("[data-component-id]")];
    componentControls.forEach((control) => {
      if (control.getAttribute("role") === "treeitem") return;
      const selected = control.dataset.componentId === selectedId;
      control.classList.toggle("component-panel--selected", selected && control.classList.contains("component-panel"));
      control.setAttribute("aria-pressed", String(selected));
    });
    const inspector = root.querySelector("[data-sot-inspector]");
    if (inspector) inspector.innerHTML = renderSotInspector(snapshot, state.sotView);
    patchInstallSurface();
    bindWorkflowInspector();
    graphSession?.syncPresentation(state.sotView);
  };

  const render = () => {
    if (destroyed) return;
    const pendingScrollContinuity = pendingLocalScrollRestore?.continuity ?? null;
    cancelPendingLocalScrollRestore();
    const sotRenderContinuity = captureSotRenderContinuity(root, state.ui.activeSegment);
    const priorWorkbench = root.querySelector("#workbench");
    const localFingerprint = state.ui.activeSegment === "local"
      ? localRenderFingerprint(state.localData, state.localView)
      : null;
    const canCarryPendingLocalScroll = pendingScrollContinuity?.fingerprint === localFingerprint
      && pendingScrollContinuity?.selectedInstanceId === state.localView.selectedInstanceId;
    const localRenderContinuity = state.ui.activeSegment === "local"
      ? (canCarryPendingLocalScroll ? pendingScrollContinuity : null) ?? {
        fingerprint: localFingerprint,
        scrollTop: Math.max(
          0,
          Number(priorWorkbench?.scrollTop) || 0,
          Number(state.localView.scrollTop) || 0,
        ),
        selectedInstanceId: state.localView.selectedInstanceId,
        selectedRowAnchor: localSelectedRowViewportAnchor(
          root,
          priorWorkbench,
          state.localView.selectedInstanceId,
        ),
      }
      : null;
    const focusTarget = captureLocalFocusTarget({
      retainLocalAction: state.ui.localActionBusy === true,
    });
    workbenchDisclosure.release();
    workflowInspectorBinding.release();
    typographyMenuBinding.release();
    graphSession?.detach();
    root.innerHTML = renderAppShell(state);
    workspaceLayoutController.syncAfterRender?.();
    typographyMenuBinding = bindTypographyMenu({
      trigger: root.querySelector("#typography-menu-trigger"),
      menu: root.querySelector("#typography-menu"),
      options: [...root.querySelectorAll("[data-typography-preset]")],
      documentTarget: root.ownerDocument,
      onSelect: selectTypographyPreset,
    });
    typographyMenuBinding.sync(state.typography);
    restoreAiFocus(root, focusTarget.retainedAiFocus);
    const sourceHost = root.querySelector("[data-local-source-content]");
    if (sourceHost) {
      renderSourcePreviewContent(sourceHost, state.sourcePreview);
    }
    const disclosureRoot = root.querySelector(".sot-workbench");
    if (disclosureRoot && state.sot.snapshot) {
      try {
        const snapshotId = state.sot.snapshot?.snapshot_id ?? null;
        if (snapshotId && graphSessionSnapshotId !== snapshotId) {
          graphSession?.dispose();
          graphSession = createGraphSession({
            snapshot: state.sot.snapshot,
            onAction: applyGraphAction,
            resolveSelection: resolveGraphSelection,
          });
          graphSessionSnapshotId = snapshotId;
        }
        syncGraphSessionAppearance();
        graphSession.setExpanded(state.ui.sotDisclosures.componentMap);
        graphSession.attach(disclosureRoot, state.sotView);
        graphSession.syncPresentation(state.sotView);
        workbenchDisclosure = bindSotWorkbenchDisclosure(disclosureRoot, {
          state: state.ui.sotDisclosures,
          graphController: graphSession,
          onChange(sotDisclosures) {
            state = {
              ...state,
              ui: { ...state.ui, sotDisclosures },
            };
            workbenchDisclosure.sync(sotDisclosures);
          },
        });
        workbenchDisclosure.sync(state.ui.sotDisclosures);
      } catch (error) {
        const failureCode = error instanceof SotWorkbenchDisclosureContractError
          ? error.code
          : "component_map_session_failed";
        workbenchDisclosure.release();
        workbenchDisclosure = { sync() {}, release() {} };
        try {
          graphSession?.detach();
        } catch {
          // The local degraded projection must remain available even if cleanup fails.
        }
        projectSotWorkbenchDisclosureFailure(disclosureRoot, {
          code: failureCode,
          diagnostic: error instanceof Error ? error.message : String(error ?? "unknown"),
        });
      }
    }
    bindWorkflowInspector();
    bindSotTreeScroll();
    restoreSotRenderContinuity(root, sotRenderContinuity);
    restoreLocalFocusTarget({
      ...focusTarget,
      retainedAiFocus: null,
    });
    syncLocalActionAnnouncer();
    const activeWorkbench = root.querySelector("#workbench");
    if (state.ui.activeSegment === "local" && activeWorkbench) {
      const sameLocalQuery = localRenderContinuity?.fingerprint
        === localRenderFingerprint(state.localData, state.localView);
      const priorAnchor = sameLocalQuery
        && localRenderContinuity?.selectedRowAnchor?.instanceId === state.localView.selectedInstanceId
        ? localRenderContinuity.selectedRowAnchor
        : null;
      const scrollRestore = {
        fingerprint: localRenderContinuity?.fingerprint ?? null,
        scrollTop: sameLocalQuery
          ? Math.max(0, localRenderContinuity.scrollTop)
          : Math.max(0, Number(state.localView.scrollTop) || 0),
        selectedInstanceId: state.localView.selectedInstanceId,
        selectedRowAnchor: priorAnchor,
      };
      const restoreLocalScroll = (workbench, continuity) => {
        workbench.scrollTop = continuity.scrollTop;
        const currentAnchor = continuity.selectedRowAnchor
          ? localSelectedRowViewportAnchor(
            root,
            workbench,
            continuity.selectedRowAnchor.instanceId,
          )
          : null;
        if (!currentAnchor) return;
        const adjustedScrollTop = Math.max(
          0,
          workbench.scrollTop + currentAnchor.offset - continuity.selectedRowAnchor.offset,
        );
        if (adjustedScrollTop === workbench.scrollTop) return;
        workbench.scrollTop = adjustedScrollTop;
        continuity.scrollTop = adjustedScrollTop;
        state = {
          ...state,
          localView: reduceLocalView(state.localView, {
            type: "set_scroll",
            scrollTop: adjustedScrollTop,
          }),
        };
      };
      restoreLocalScroll(activeWorkbench, scrollRestore);
      const shouldVerifyScrollAfterLayout = sameLocalQuery
        && scrollRestore.scrollTop > 0
        && typeof scheduleAnimationFrame === "function";
      if (shouldVerifyScrollAfterLayout) {
        const capturedWorkbench = activeWorkbench;
        const pendingRestore = {
          continuity: scrollRestore,
          handle: null,
          workbench: capturedWorkbench,
        };
        const isCurrentPendingRestore = () => !destroyed
          && pendingLocalScrollRestore === pendingRestore
          && state.ui.activeSegment === "local"
          && root.querySelector("#workbench") === capturedWorkbench
          && scrollRestore.fingerprint === localRenderFingerprint(state.localData, state.localView)
          && scrollRestore.selectedInstanceId === state.localView.selectedInstanceId;
        pendingLocalScrollRestore = pendingRestore;
        pendingRestore.handle = scheduleAnimationFrame(() => {
          if (!isCurrentPendingRestore()) return;
          pendingRestore.handle = scheduleAnimationFrame(() => {
            if (!isCurrentPendingRestore()) return;
            pendingLocalScrollRestore = null;
            restoreLocalScroll(capturedWorkbench, scrollRestore);
          });
        });
      } else {
        cancelPendingLocalScrollRestore();
      }
      activeWorkbench.addEventListener?.("scroll", () => {
        if (root.querySelector("#workbench") !== activeWorkbench) return;
        const pendingRestore = pendingLocalScrollRestore;
        if (pendingRestore?.workbench === activeWorkbench) return;
        state = {
          ...state,
          localView: reduceLocalView(state.localView, {
            type: "set_scroll",
            scrollTop: activeWorkbench.scrollTop,
          }),
        };
      });
      const cancelPendingRestoreForUserInput = (event) => {
        const pendingRestore = pendingLocalScrollRestore;
        if (pendingRestore?.workbench !== activeWorkbench) return;
        if (event?.type === "keydown"
          && !["ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown", " "].includes(event.key)) {
          return;
        }
        cancelPendingLocalScrollRestore();
        state = {
          ...state,
          localView: reduceLocalView(state.localView, {
            type: "set_scroll",
            scrollTop: activeWorkbench.scrollTop,
          }),
        };
      };
      ["wheel", "pointerdown", "touchstart", "keydown"].forEach((eventType) => {
        activeWorkbench.addEventListener?.(eventType, cancelPendingRestoreForUserInput);
      });
    }

    root.querySelectorAll('input[name="appearance-mode"]').forEach((control) => {
      control.addEventListener("change", async (event) => {
        if (!event.currentTarget.checked) return;
        const logicalMode = event.currentTarget.value;
        try {
          const response = await requestAppearanceMode({ logicalMode, backend });
          applyAppearanceState(reduceAppearanceResponse(state, response));
        } catch (_error) {
          applyAppearanceState({
            ...state,
            appearance: {
              ...state.appearance,
              diagnostic: {
                code: "appearance_update_failed",
                safe_message: "외관 설정을 변경하지 못했습니다.",
              },
            },
          });
        }
      });
    });

    root.querySelectorAll("[data-pane-disclosure]").forEach((control) => {
      control.addEventListener("click", () => {
        const side = control.dataset.paneDisclosure;
        const collapsed = side === "left"
          ? state.workspaceLayout.leftCollapsed !== true
          : state.workspaceLayout.rightCollapsed !== true;
        if ((side === "left" && collapsed && root.ownerDocument?.activeElement?.closest?.("#workspace-left-pane"))
          || (side === "right" && collapsed && root.ownerDocument?.activeElement?.closest?.("#workspace-right-pane"))) {
          control.focus();
        }
        workspaceLayoutController.setCollapsed?.(side, collapsed);
      });
    });

    const dashboardSegments = [...root.querySelectorAll("[data-dashboard-segment]")];
    const activateSegment = (activeSegment, focusTab = false, options = {}) => {
      if (!["sot", "local"].includes(activeSegment)) return;
      const currentWorkbench = root.querySelector("#workbench");
      const departureScroll = currentWorkbench?.scrollTop ?? 0;
      let restoreScroll = segmentScrollPositions.get(activeSegment) ?? 0;
      if (currentWorkbench) {
        segmentScrollPositions.set(state.ui.activeSegment, currentWorkbench.scrollTop);
      }
      if (activeSegment !== state.ui.activeSegment || activeSegment === "local") {
        sourcePreviewController.clear();
      }
      if (activeSegment === "local") {
        const installEvidenceId = optionalId(options.installEvidenceId);
        const verifiedComponentId = installEvidenceId
          ? optionalId(options.verifiedComponentId)
          : null;
        const localContext = localWorkflowController.resetLocalContext({
          localData: reduceLocalScanState(state.localData, { type: "clear_detail" }),
          localView: createLocalViewState(),
        });
        update({
          ...state,
          ui: {
            ...state.ui,
            activeSegment,
            authoringFlowOpen: false,
            sotDeparture: verifiedComponentId ? {
              sotView: { ...state.sotView },
              scrollTop: departureScroll,
            } : null,
          },
          ...localContext,
        });
        segmentScrollPositions.set("local", 0);
        if (verifiedComponentId) {
          void localWorkflowController.openVerifiedContext({
            componentId: verifiedComponentId,
            installEvidenceId,
          });
        } else {
          void localWorkflowController.ensureSession();
        }
      } else {
        localWorkflowController.leaveLocalContext();
        const departure = state.ui.sotDeparture;
        restoreScroll = departure?.scrollTop ?? restoreScroll;
        const sotView = departure?.sotView
          ? { ...departure.sotView }
          : options.componentId && state.sot.snapshot
            ? reduceSotView(state.sotView, {
              type: "select_component",
              componentId: options.componentId,
            }, state.sot.snapshot)
            : state.sotView;
        const component = state.sot.snapshot?.components?.find(
          (candidate) => candidate.component_id === sotView.selectedComponentId,
        );
        const install = !departure && component ? reduceInstallState(state.install, {
          type: "select_subject",
          sotSnapshotId: state.sot.snapshot.snapshot_id,
          componentId: component.component_id,
          profileId: installProfileId(component, sotView.activeProfileId),
          targetId: firstTargetId(component),
        }) : state.install;
        if (install !== state.install) installGeneration += 1;
        update({
          ...state,
          ui: {
            ...state.ui,
            activeSegment,
            authoringFlowOpen: false,
            sotDeparture: null,
          },
          sotView,
          install,
        });
      }
      const nextWorkbench = root.querySelector("#workbench");
      if (nextWorkbench) {
        nextWorkbench.scrollTop = activeSegment === "local"
          ? 0
          : restoreScroll;
      }
      if (focusTab) {
        root.querySelector(`[data-dashboard-segment="${activeSegment}"]`)?.focus();
      }
    };
    dashboardSegments.forEach((tab, index) => {
      tab.addEventListener("click", () => activateSegment(tab.dataset.dashboardSegment, true));
      tab.addEventListener("keydown", (event) => {
        const nextIndex = event.key === "ArrowRight"
          ? (index + 1) % dashboardSegments.length
          : event.key === "ArrowLeft"
            ? (index + dashboardSegments.length - 1) % dashboardSegments.length
            : event.key === "Home"
              ? 0
              : event.key === "End"
                ? dashboardSegments.length - 1
                : null;
        if (nextIndex === null) return;
        event.preventDefault();
        activateSegment(dashboardSegments[nextIndex].dataset.dashboardSegment, true);
      });
    });

    root.querySelector("#dashboard-refresh")?.addEventListener("click", () => {
      const activeSegment = state.ui.activeSegment;
      if (activeSegment === "local") {
        if (state.localData?.scanStartPending === true
          || state.localData?.currentAttempt?.state === "running") return;
        void localWorkflowController.refreshScan();
        return;
      }
      if (activeSegment !== "sot"
        || !state.repo.checkoutId
        || ["cloning", "registering"].includes(state.repo.phase)
        || state.sot.phase === "loading") return;
      void loadSotSnapshot(state.repo.checkoutId);
    });

    root.querySelector("[data-open-checkout-picker]")?.addEventListener("click", () => {
      const input = root.querySelector("#checkout-path");
      if (input) input.value = "";
      root.querySelector("#repo-form")?.requestSubmit?.();
    });

    root.querySelector("[data-open-project-ignore]")?.addEventListener("click", () => {
      void localWorkflowController.openProjectIgnoreEditor();
    });
    root.querySelector("[data-close-project-ignore]")?.addEventListener("click", () => {
      localWorkflowController.closeProjectIgnoreEditor();
    });
    root.querySelector("[data-project-ignore-form]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const exactText = String(
        event.currentTarget?.elements?.namedItem?.("exactText")?.value ?? "",
      );
      void localWorkflowController.saveProjectIgnore({ exactText });
    });

    const shell = root.querySelector(".desktop-app");
    const dismissLocalRemoval = () => {
      const localRemoval = dismissLocalRemovalConfirmation(state.localRemoval);
      if (localRemoval === state.localRemoval) return;
      update({ ...state, localRemoval });
      restoreLocalRemovalOpener();
    };
    const enterLocalRemovalSelection = (opener) => {
      const localRemoval = enterLocalRemovalSelectionMode(state.localRemoval);
      if (localRemoval === state.localRemoval) return;
      localRemovalOpener = opener;
      update({ ...state, localRemoval });
      root.querySelector("[data-local-removal-selection]")?.focus();
    };
    const exitLocalRemovalSelection = () => {
      const localRemoval = exitLocalRemovalSelectionMode(state.localRemoval);
      if (localRemoval === state.localRemoval) return;
      update({ ...state, localRemoval });
      root.querySelector("[data-enter-local-removal]")?.focus();
      localRemovalOpener = null;
    };
    const prepareLocalRemoval = (opener) => {
      const snapshot = activeLocalSnapshotHeader(state.localData);
      if (!snapshot || typeof backend.prepareLocalRemoval !== "function") return;
      const started = beginLocalRemovalPreparation(state.localRemoval, snapshot.snapshotId);
      if (!started.request) return;
      localRemovalOpener = opener;
      const generation = ++localRemovalPreparationGeneration;
      update({ ...state, localRemoval: started.state });
      void Promise.resolve(backend.prepareLocalRemoval(started.request))
        .then((response) => {
          if (destroyed || generation !== localRemovalPreparationGeneration) return;
          const planned = acceptLocalRemovalPlan(state.localRemoval, response, started.token);
          const confirming = openLocalRemovalConfirmation(planned);
          update({ ...state, localRemoval: confirming });
          if (confirming.phase === "confirming") {
            root.querySelector("[data-local-removal-ack]")?.focus();
          } else {
            restoreLocalRemovalOpener();
          }
        })
        .catch(() => {
          if (destroyed || generation !== localRemovalPreparationGeneration) return;
          update({
            ...state,
            localRemoval: acceptLocalRemovalPlan(state.localRemoval, null, started.token),
          });
          restoreLocalRemovalOpener();
        });
    };
    const applyLocalRemoval = () => {
      if (typeof backend.applyLocalRemoval !== "function") return;
      const started = beginLocalRemovalApply(state.localRemoval);
      if (!started.request) return;
      pendingLocalRemovalTerminals.clear();
      const generation = ++localRemovalApplyGeneration;
      update({ ...state, localRemoval: started.state });
      root.querySelector("[data-enter-local-removal]")?.focus();
      void Promise.resolve(backend.applyLocalRemoval(started.request))
        .then((response) => {
          if (destroyed || generation !== localRemovalApplyGeneration) return;
          const localRemoval = acceptLocalRemovalOutcome(state.localRemoval, response);
          update({
            ...state,
            localRemoval,
          });
          if (localRemoval.phase === "awaiting_rescan") {
            const terminal = takePendingLocalRemovalTerminal(localRemoval.rescan.attemptId)
              ?? currentLocalRemovalTerminal();
            handleLocalRemovalRescanTerminal(terminal);
          } else if (localRemoval.phase === "complete") {
            pendingLocalRemovalTerminals.clear();
            restoreLocalRemovalOpener();
          }
        })
        .catch(() => {
          if (destroyed || generation !== localRemovalApplyGeneration) return;
          pendingLocalRemovalTerminals.clear();
          update({
            ...state,
            localRemoval: acceptLocalRemovalOutcome(state.localRemoval, null),
          });
          restoreLocalRemovalOpener();
        });
    };
    const applyLocalFilter = (action) => {
      sourcePreviewController.clear();
      localDetailGeneration += 1;
      localActionGeneration += 1;
      localSelectionGeneration += 1;
      update({
        ...state,
        localData: reduceLocalScanState(state.localData, { type: "clear_detail" }),
        localView: reduceLocalView(state.localView, action),
      });
      void localWorkflowController.refreshQuery();
    };
    const resetLocalSearch = () => {
      sourcePreviewController.clear();
      const localContext = localWorkflowController.resetLocalContext({
        localData: reduceLocalScanState(state.localData, { type: "clear_detail" }),
        localView: reduceLocalView(state.localView, { type: "reset" }),
      });
      update({
        ...state,
        ...localContext,
      });
      void localWorkflowController.refreshQuery();
      root.querySelector("#local-search")?.focus();
    };
    const selectLocalInstance = (instanceId, options = {}) => {
      if (!instanceId) return;
      localDetailGeneration += 1;
      localActionGeneration += 1;
      localSelectionGeneration += 1;
      updateLocalSelectionSurface({
        ...state,
        localData: reduceLocalScanState(state.localData, { type: "clear_detail" }),
        localView: reduceLocalView(state.localView, { type: "select_instance", instanceId }),
      });
      if (options.reveal === true) {
        const selectedControl = [...root.querySelectorAll("[data-local-instance]")]
          .find((control) => control.dataset.localInstance === instanceId);
        selectedControl?.focus?.({ preventScroll: true });
        selectedControl?.scrollIntoView?.({ block: "nearest" });
      }
      const snapshot = activeLocalSnapshotHeader(state.localData);
      if (snapshot) {
        void sourcePreviewController.select({
          snapshotId: snapshot.snapshotId,
          instanceId,
        });
      }
      void loadLocalDetail(instanceId);
    };
    const selectFromControl = (control) => {
      const componentId = control?.dataset?.componentId;
      if (!componentId || !state.sot.snapshot) return;
      const treeSelection = control.getAttribute?.("role") === "treeitem";
      const sotView = reduceSotView(state.sotView, {
        type: treeSelection ? "select_tree_component" : "select_component",
        componentId,
      }, state.sot.snapshot);
      const component = state.sot.snapshot.components.find(
        (candidate) => candidate.component_id === sotView.selectedComponentId,
      );
      const install = reduceInstallState(state.install, {
        type: "select_subject",
        sotSnapshotId: state.sot.snapshot.snapshot_id,
        componentId: sotView.selectedComponentId,
        profileId: installProfileId(component, sotView.activeProfileId),
        targetId: firstTargetId(component),
      });
      if (install !== state.install) installGeneration += 1;
      state = {
        ...state,
        sotView,
        install,
      };
      render();
      if (treeSelection) {
        const selectedTreeItem = [...root.querySelectorAll("[data-sot-tree] [role=treeitem]")]
          .find((item) => item.dataset.componentId === componentId);
        selectedTreeItem?.focus();
        selectedTreeItem?.scrollIntoView?.({ block: "nearest" });
      }
    };

    const selectProfileById = (profileId, patchOptions = {}) => {
      if (!profileId || !state.sot.snapshot) return;
      const sotView = reduceSotView(state.sotView, {
        type: "select_profile",
        profileId,
      }, state.sot.snapshot);
      const component = state.sot.snapshot.components.find(
        (candidate) => candidate.component_id === sotView.selectedComponentId,
      );
      const install = component ? reduceInstallState(state.install, {
        type: "select_subject",
        sotSnapshotId: state.sot.snapshot.snapshot_id,
        componentId: component.component_id,
        profileId: installProfileId(component, sotView.activeProfileId),
        targetId: state.install.form.targetId || firstTargetId(component),
      }) : state.install;
      if (install !== state.install) installGeneration += 1;
      state = { ...state, sotView, install };
      patchSotSelection({ profileChanged: true, ...patchOptions });
    };

    const activateSotTreeControl = (control, toggleGroup = true) => {
      if (!control || !state.sot.snapshot) return;
      if (control.dataset.componentId) {
        selectFromControl(control);
        return;
      }
      const treeNodeId = control.dataset.sotTreeNode;
      if (!treeNodeId) return;
      let sotView = reduceSotView(state.sotView, {
        type: "select_tree_node",
        treeNodeId,
      }, state.sot.snapshot);
      if (toggleGroup && control.getAttribute?.("aria-expanded") !== null) {
        sotView = reduceSotView(sotView, {
          type: "toggle_tree_node",
          treeNodeId,
        }, state.sot.snapshot);
      }
      state = { ...state, sotView };
      if (control.dataset.sotTreeProfile) {
        selectProfileById(control.dataset.sotTreeProfile, {
          treeChanged: true,
          focusTreeId: treeNodeId,
        });
        return;
      }
      patchSotSelection({
        treeChanged: true,
        focusTreeId: treeNodeId,
      });
    };

    shell?.addEventListener("submit", (event) => {
      if (event.target?.id !== "install-form") return;
      event.preventDefault();
      const form = readInstallForm(event.target);
      const formState = reduceInstallState(state.install, { type: "update_form", form });
      if (formState !== state.install) {
        installGeneration += 1;
        commitInstall(formState);
      }
      const request = createInstallRequest(state.install);
      if (!state.repo.checkoutId
        || !request.sotSnapshotId
        || !request.profileId
        || request.targetIds.length === 0
        || !request.targetRoot
        || typeof backend.previewInstall !== "function") return;
      const generation = ++installGeneration;
      commitInstall(reduceInstallState(state.install, { type: "preview_started" }));
      root.querySelector("[data-sot-install]")?.focus();
      void Promise.resolve(backend.previewInstall({
        checkoutId: state.repo.checkoutId,
        sotSnapshotId: request.sotSnapshotId,
        profileId: request.profileId,
        scope: request.scope,
        targetRoot: request.targetRoot,
        targetIds: request.targetIds,
      })).then((response) => {
        if (generation !== installGeneration) return;
        const preview = normalizeInstallPreviewResponse(response, request);
        if (!preview) throw new Error("invalid preview response");
        commitInstall(reduceInstallState(state.install, { type: "preview_ready", preview }));
        root.querySelector(".plan-preview")?.focus();
      }).catch((error) => {
        if (generation !== installGeneration) return;
        commitInstall(reduceInstallState(state.install, {
          type: "preview_error",
          message: safeOperationError("preview", error),
        }));
        root.querySelector("[data-sot-install]")?.focus();
      });
    });

    shell?.addEventListener("submit", (event) => {
      const form = event.target.closest?.("[data-ai-provider-form]");
      if (!form) return;
      event.preventDefault();
      void saveAiProvider(form);
    });

    shell?.addEventListener("input", (event) => {
      const input = event.target;
      if (input?.name !== "baseUrl" || !input.closest?.("[data-ai-provider-form]")) return;
      const warning = root.querySelector("[data-ai-provider-transport-warning]");
      if (!warning) return;
      const code = aiProviderTransportWarning(input.value);
      warning.dataset.aiProviderTransportWarning = code ?? "";
      warning.textContent = code ? AI_TRANSPORT_WARNING.message : "";
      warning.hidden = !code;
    });

    shell?.addEventListener("change", (event) => {
      const removalSelection = event.target.closest?.("[data-local-removal-selection]");
      if (removalSelection) {
        const localRemoval = toggleLocalRemovalSelection(
          state.localRemoval,
          removalSelection.dataset.localRemovalSelection,
        );
        if (localRemoval !== state.localRemoval) update({ ...state, localRemoval });
        return;
      }
      const removalAcknowledgement = event.target.closest?.("[data-local-removal-ack]");
      if (removalAcknowledgement) {
        update({
          ...state,
          localRemoval: acknowledgeLocalRemovalConfirmation(
            state.localRemoval,
            removalAcknowledgement.checked === true,
          ),
        });
        root.querySelector("[data-local-removal-ack]")?.focus();
        return;
      }
      const installField = event.target.closest?.("[data-install-field]");
      if (installField) {
        const next = reduceInstallState(state.install, {
          type: "update_form",
          form: readInstallForm(installField.form),
        });
        if (next !== state.install) {
          installGeneration += 1;
          commitInstall(next);
        }
        return;
      }
      const approval = event.target.closest?.("[data-install-approval]");
      if (!approval) return;
      const values = {
        confirmed: state.install.confirmed,
        overwrite: state.install.overwrite,
        allowRuntimeHooks: state.install.allowRuntimeHooks,
        [approval.dataset.installApproval]: approval.checked === true,
      };
      commitInstall(reduceInstallState(state.install, { type: "set_approval", ...values }));
    });

    shell?.addEventListener("click", (event) => {
      const closeLocalRemoval = event.target.closest?.("[data-close-local-removal]");
      const localRemovalBackdrop = event.target.closest?.("[data-local-removal-dialog]");
      if (closeLocalRemoval || (localRemovalBackdrop && event.target === localRemovalBackdrop)) {
        dismissLocalRemoval();
        return;
      }

      const cancelLocalRemovalSelection = event.target.closest?.("[data-cancel-local-removal-selection]");
      if (cancelLocalRemovalSelection && !cancelLocalRemovalSelection.disabled) {
        exitLocalRemovalSelection();
        return;
      }

      const enterLocalRemoval = event.target.closest?.("[data-enter-local-removal]");
      if (enterLocalRemoval && !enterLocalRemoval.disabled) {
        enterLocalRemovalSelection(enterLocalRemoval);
        return;
      }

      const prepareLocalRemovalControl = event.target.closest?.("[data-prepare-local-removal]");
      if (prepareLocalRemovalControl && !prepareLocalRemovalControl.disabled) {
        prepareLocalRemoval(prepareLocalRemovalControl);
        return;
      }

      const applyRemoval = event.target.closest?.("[data-apply-local-removal]");
      if (applyRemoval && !applyRemoval.disabled) {
        applyLocalRemoval();
        return;
      }

      const openAuthoringFlow = event.target.closest?.("[data-open-authoring-flow]");
      if (openAuthoringFlow && !openAuthoringFlow.disabled) {
        update({
          ...state,
          ui: { ...state.ui, authoringFlowOpen: true },
        });
        root.querySelector("[data-close-authoring-flow]")?.focus();
        return;
      }

      const closeAuthoringFlow = event.target.closest?.("[data-close-authoring-flow]");
      const authoringBackdrop = event.target.closest?.("[data-authoring-flow-dialog]");
      if (closeAuthoringFlow || (authoringBackdrop && event.target === authoringBackdrop)) {
        update({
          ...state,
          ui: { ...state.ui, authoringFlowOpen: false },
        });
        root.querySelector("[data-open-authoring-flow]")?.focus();
        return;
      }

      const openAiSettings = event.target.closest?.("[data-open-ai-settings]");
      if (openAiSettings) {
        update({
          ...state,
          ui: { ...state.ui, aiSettingsOpen: true, aiProviderMessage: "" },
        });
        root.querySelector('[data-ai-provider-form] input[name="baseUrl"]')?.focus();
        return;
      }

      const closeAiSettings = event.target.closest?.("[data-ai-provider-close]");
      if (closeAiSettings) {
        update({
          ...state,
          ui: { ...state.ui, aiSettingsOpen: false, aiProviderMessage: "" },
        });
        root.querySelector("[data-open-ai-settings]")?.focus();
        return;
      }

      const deleteAiKey = event.target.closest?.("[data-ai-provider-key-delete]");
      if (deleteAiKey) {
        void deleteAiProviderKey();
        return;
      }

      const explainWithAi = event.target.closest?.("[data-ai-explain]");
      if (explainWithAi
        && !explainWithAi.disabled
        && explainWithAi.getAttribute?.("aria-disabled") !== "true") {
        syncAiExplanationBinding();
        void aiExplanationController.request();
        return;
      }

      const applyInstallControl = event.target.closest?.("#apply-install");
      if (applyInstallControl) {
        const preview = normalizeInstallPreviewResponse(
          state.install.preview,
          createInstallRequest(state.install),
        );
        if (!preview || !canApplyInstall(state.install) || typeof backend.applyInstall !== "function") return;
        const generation = ++installGeneration;
        commitInstall(reduceInstallState(state.install, { type: "apply_started" }), null);
        root.querySelector("[data-sot-install]")?.focus();
        void Promise.resolve(backend.applyInstall({
          previewId: preview.previewId,
          approvals: {
            confirmed: true,
            semanticFingerprint: preview.semanticFingerprint,
            overwrite: state.install.overwrite,
            allowRuntimeHooks: state.install.allowRuntimeHooks,
          },
        })).then((response) => {
          if (generation !== installGeneration) return;
          const execution = normalizeApplyInstallResponse(response);
          if (!execution) throw new Error("invalid install response");
          commitInstall(
            reduceInstallState(state.install, { type: "apply_result", execution }),
            execution.status === "success" ? execution.installEvidenceId : null,
          );
          root.querySelector(".execution-result")?.focus();
        }).catch((error) => {
          if (generation !== installGeneration) return;
          commitInstall(reduceInstallState(state.install, {
            type: "apply_error",
            message: safeOperationError("apply", error),
          }));
          root.querySelector("[data-sot-install]")?.focus();
        });
        return;
      }

      const localInstallControl = event.target.closest?.("[data-show-local-install]");
      if (localInstallControl) {
        const componentId = localInstallControl.dataset.showLocalInstall;
        if (!componentId || componentId !== state.sotView.selectedComponentId) return;
        const installEvidenceId = activeInstallEvidenceId(state);
        activateSegment("local", true, installEvidenceId ? {
          verifiedComponentId: componentId,
          installEvidenceId,
        } : {});
        return;
      }

      const clearCorrelationControl = event.target.closest?.("[data-clear-local-correlation]");
      if (clearCorrelationControl) {
        resetLocalSearch();
        return;
      }

      const resetLocalFilters = event.target.closest?.("[data-reset-local-filters]");
      if (resetLocalFilters) {
        resetLocalSearch();
        return;
      }

      const resultWindow = event.target.closest?.("[data-local-result-window]");
      if (resultWindow) {
        const direction = resultWindow.dataset.localResultWindow;
        const localView = reduceLocalView(state.localView, {
          type: direction === "previous" ? "previous_result_window" : "next_result_window",
          totalCount: state.localData.queryResult?.counts?.matchedInstances
            ?? state.localData.queryResult?.counts?.matched_instances
            ?? 0,
        });
        if (localView === state.localView) return;
        sourcePreviewController.clear();
        localDetailGeneration += 1;
        localActionGeneration += 1;
        localSelectionGeneration += 1;
        update({
          ...state,
          localData: reduceLocalScanState(state.localData, { type: "clear_detail" }),
          localView,
        });
        const controls = [...root.querySelectorAll("[data-local-result-window]")];
        (controls.find((control) => control.dataset.localResultWindow === direction && !control.disabled)
          ?? controls.find((control) => !control.disabled))
          ?.focus();
        return;
      }

      const openSotControl = event.target.closest?.("[data-open-sot-component]");
      if (openSotControl) {
        const componentId = openSotControl.dataset.openSotComponent;
        if (!componentId || verifiedSotComponentId(state.localData, state.localView) !== componentId) return;
        activateSegment("sot", true, { componentId });
        return;
      }

      const sourceChunkControl = event.target.closest?.("[data-source-chunk]");
      if (sourceChunkControl && !sourceChunkControl.disabled) {
        void sourcePreviewController.requestChunk(Number(sourceChunkControl.dataset.sourceChunk));
        return;
      }

      const actionControl = event.target.closest?.("[data-local-action]");
      if (actionControl && typeof backend.actOnLocalInstance === "function") {
        if (state.ui.localActionBusy === true) return;
        const request = {
          snapshotId: actionControl.dataset.snapshotId,
          instanceId: actionControl.dataset.instanceId,
          action: actionControl.dataset.localAction,
        };
        if (!LOCAL_ACTION_COPY[request.action]) return;
        const actionGeneration = ++localActionGeneration;
        const selectionGeneration = localSelectionGeneration;
        const identity = {
          snapshotId: request.snapshotId,
          instanceId: request.instanceId,
          correlationProjectionId: state.localView.correlationProjectionId,
        };
        commitLocalActionSurface({
          ...state,
          ui: { ...state.ui, localActionBusy: true },
          localView: reduceLocalView(state.localView, {
            type: "set_action_status",
            instanceId: request.instanceId,
            status: localActionPending(request.action),
          }),
        });
        let actionRequest;
        try {
          actionRequest = backend.actOnLocalInstance(request);
        } catch (error) {
          actionRequest = Promise.reject(error);
        }
        void Promise.resolve(actionRequest)
          .then((outcome) => {
            const current = actionGeneration === localActionGeneration
              && selectionGeneration === localSelectionGeneration
              && isCurrentLocalIdentity(identity);
            const normalized = normalizeLocalActionOutcome(request.action, outcome);
            const localView = current
              ? reduceLocalView(state.localView, {
                type: "set_action_status",
                instanceId: request.instanceId,
                status: normalized.status,
              })
              : state.localView;
            commitLocalActionSurface({
              ...state,
              ui: { ...state.ui, localActionBusy: false },
              localView,
            });
          })
          .catch((error) => {
            const current = actionGeneration === localActionGeneration
              && selectionGeneration === localSelectionGeneration
              && isCurrentLocalIdentity(identity);
            const localView = current
              ? reduceLocalView(state.localView, {
                type: "set_action_status",
                instanceId: request.instanceId,
                status: localActionFailure(error),
              })
              : state.localView;
            commitLocalActionSurface({
              ...state,
              ui: { ...state.ui, localActionBusy: false },
              localView,
            });
          });
        return;
      }

      const location = event.target.closest?.("[data-local-scope]");
      if (location) {
        applyLocalFilter({
          type: "select_location",
          scope: location.dataset.localScope,
          projectId: location.dataset.localProjectId ?? null,
        });
        return;
      }

      const tool = event.target.closest?.("[data-local-tool]");
      if (tool) {
        applyLocalFilter({ type: "select_tool", toolId: tool.dataset.localTool || null });
        return;
      }

      const kind = event.target.closest?.("[data-local-kind]");
      if (kind) {
        applyLocalFilter({ type: "select_kind", kind: kind.dataset.localKind || null });
        return;
      }

      const instance = event.target.closest?.("[data-local-instance]");
      if (instance) {
        selectLocalInstance(instance.dataset.localInstance);
        return;
      }

      const profile = event.target.closest?.("[data-profile-id]");
      if (profile && state.sot.snapshot) {
        selectProfileById(profile.dataset.profileId);
        return;
      }
      const treeNode = event.target.closest?.("[data-sot-tree-node]");
      if (treeNode) {
        activateSotTreeControl(treeNode);
        return;
      }
      selectFromControl(event.target.closest?.("[data-component-id]"));
    });
    shell?.addEventListener("keydown", (event) => {
      if (event.key === "Tab" && state.localRemoval?.phase === "confirming") {
        const dialog = root.querySelector("[data-local-removal-dialog]");
        const focusable = [...(dialog?.querySelectorAll?.(
          'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [])];
        if (!dialog || focusable.length === 0) return;
        const active = root.ownerDocument?.activeElement;
        const first = focusable[0];
        const last = focusable.at(-1);
        if (!dialog.contains(active)
          || (event.shiftKey === true && active === first)
          || (event.shiftKey !== true && active === last)) {
          event.preventDefault();
          (event.shiftKey === true ? last : first).focus();
        }
        return;
      }
      if (event.key === "Escape" && state.localRemoval?.phase === "confirming") {
        event.preventDefault();
        dismissLocalRemoval();
        return;
      }
      if (event.key === "Tab" && state.ui.authoringFlowOpen) {
        const dialog = root.querySelector("[data-authoring-flow-dialog]");
        const focusable = [...(dialog?.querySelectorAll?.(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [])];
        if (!dialog || focusable.length === 0) return;
        const active = root.ownerDocument?.activeElement;
        const first = focusable[0];
        const last = focusable.at(-1);
        if (!dialog.contains(active)
          || (event.shiftKey === true && active === first)
          || (event.shiftKey !== true && active === last)) {
          event.preventDefault();
          (event.shiftKey === true ? last : first).focus();
        }
        return;
      }
      if (event.key === "Escape" && state.ui.authoringFlowOpen) {
        event.preventDefault();
        update({
          ...state,
          ui: { ...state.ui, authoringFlowOpen: false },
        });
        root.querySelector("[data-open-authoring-flow]")?.focus();
        return;
      }
      if (event.key === "Tab" && state.ui.aiSettingsOpen) {
        const dialog = root.querySelector("[data-ai-provider-dialog]");
        const focusable = [...(dialog?.querySelectorAll?.(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [])];
        if (!dialog || focusable.length === 0) return;
        const active = root.ownerDocument?.activeElement;
        const first = focusable[0];
        const last = focusable.at(-1);
        if (!dialog.contains(active)
          || (event.shiftKey === true && active === first)
          || (event.shiftKey !== true && active === last)) {
          event.preventDefault();
          (event.shiftKey === true ? last : first).focus();
        }
        return;
      }
      if (event.key === "Escape" && state.ui.aiSettingsOpen) {
        event.preventDefault();
        update({
          ...state,
          ui: { ...state.ui, aiSettingsOpen: false, aiProviderMessage: "" },
        });
        root.querySelector("[data-open-ai-settings]")?.focus();
        return;
      }
      if (event.key === "Escape" && state.ui.activeSegment === "sot") {
        const cameraRestored = graphSession?.restoreCamera?.() === true;
        const graphState = state.sotView;
        const hasGraphOwnedState = Boolean(
          graphState.selectedComponentId
          || graphState.selectedRelationNodeId
          || graphState.selectedWorkflowId
          || graphState.hoveredGraphNodeId
          || graphState.hoveredWorkflowStep
          || graphState.lockedWorkflowStep,
        );
        if (cameraRestored || hasGraphOwnedState) {
          event.preventDefault();
          if (hasGraphOwnedState) applyGraphAction({ type: "clear_graph_selection" });
          return;
        }
      }
      const localExplorerRow = event.target.closest?.("[data-local-scope]");
      if (localExplorerRow && ["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
        const rows = [...root.querySelectorAll("[data-local-scope]")];
        const currentIndex = rows.indexOf(localExplorerRow);
        const nextIndex = event.key === "Home"
          ? 0
          : event.key === "End"
            ? rows.length - 1
            : event.key === "ArrowUp"
              ? Math.max(0, currentIndex - 1)
              : Math.min(rows.length - 1, currentIndex + 1);
        const nextRow = rows[nextIndex];
        if (!nextRow) return;
        event.preventDefault();
        const nextScope = nextRow.dataset.localScope;
        const nextProjectId = nextRow.dataset.localProjectId ?? null;
        applyLocalFilter({ type: "select_location", scope: nextScope, projectId: nextProjectId });
        [...root.querySelectorAll("[data-local-scope]")]
          .find((row) => row.dataset.localScope === nextScope
            && (row.dataset.localProjectId ?? null) === nextProjectId)
          ?.focus();
        return;
      }

      const localResult = event.target.closest?.("[data-local-instance]");
      if (localResult && ["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
        const results = [...root.querySelectorAll("[data-local-instance]")];
        const currentIndex = results.indexOf(localResult);
        const nextIndex = event.key === "Home"
          ? 0
          : event.key === "End"
            ? results.length - 1
            : event.key === "ArrowUp"
              ? Math.max(0, currentIndex - 1)
              : Math.min(results.length - 1, currentIndex + 1);
        const nextResult = results[nextIndex];
        if (!nextResult) return;
        event.preventDefault();
        selectLocalInstance(nextResult.dataset.localInstance, { reveal: true });
        return;
      }

      const treeItem = event.target.closest?.("[data-sot-tree] [role=treeitem]");
      if (!treeItem) return;
      if (["Enter", " "].includes(event.key)) {
        event.preventDefault();
        activateSotTreeControl(treeItem);
        return;
      }
      if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
        const expanded = treeItem.getAttribute?.("aria-expanded");
        if ((event.key === "ArrowLeft" && expanded === "true")
          || (event.key === "ArrowRight" && expanded === "false")) {
          event.preventDefault();
          activateSotTreeControl(treeItem);
          return;
        }
        const items = [...root.querySelectorAll("[data-sot-tree] [role=treeitem]")];
        const currentIndex = items.indexOf(treeItem);
        const currentLevel = Number(treeItem.getAttribute?.("aria-level"));
        let nextItem = null;
        if (event.key === "ArrowRight" && expanded === "true") {
          const candidate = items[currentIndex + 1];
          if (Number(candidate?.getAttribute?.("aria-level")) > currentLevel) nextItem = candidate;
        } else if (event.key === "ArrowLeft" && Number.isFinite(currentLevel)) {
          for (let index = currentIndex - 1; index >= 0; index -= 1) {
            const candidateLevel = Number(items[index].getAttribute?.("aria-level"));
            if (candidateLevel < currentLevel) {
              nextItem = items[index];
              break;
            }
          }
        }
        if (nextItem) {
          event.preventDefault();
          activateSotTreeControl(nextItem, false);
        }
        return;
      }
      if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      const items = [...root.querySelectorAll("[data-sot-tree] [role=treeitem]")];
      const currentIndex = items.indexOf(treeItem);
      const nextIndex = event.key === "Home"
        ? 0
        : event.key === "ArrowUp"
          ? Math.max(0, currentIndex - 1)
          : Math.min(items.length - 1, currentIndex + 1);
      const nextItem = event.key === "End"
        ? items.filter((item) => Boolean(item.dataset?.componentId)).at(-1)
        : items[nextIndex];
      if (!nextItem) return;
      event.preventDefault();
      activateSotTreeControl(nextItem, false);
    });
    shell?.addEventListener("input", (event) => {
      const sourceChunkRange = event.target.closest?.("[data-source-chunk-range]");
      if (sourceChunkRange) {
        void sourcePreviewController.requestChunk(Number(sourceChunkRange.value));
        return;
      }
      if (event.target.id === "local-search") {
        const query = event.target.value;
        applyLocalFilter({ type: "set_query", query });
        const search = root.querySelector("#local-search");
        search?.focus();
        search?.setSelectionRange(query.length, query.length);
        return;
      }
      if (event.target.id === "local-project-search") {
        const query = event.target.value;
        state = {
          ...state,
          localView: reduceLocalView(state.localView, {
            type: "set_project_explorer_query",
            query,
          }),
        };
        const projectList = root.querySelector("[data-local-project-list]");
        if (projectList) {
          projectList.innerHTML = renderLocalProjectRows(state.localData, state.localView);
        }
        return;
      }
      if (event.target.id !== "sot-tree-filter") return;
      if (!state.sot.snapshot) return;
      state = {
        ...state,
        sotView: reduceSotView(state.sotView, {
          type: "set_filter",
          filter: event.target.value,
        }, state.sot.snapshot),
      };
      patchSotSelection({ filterChanged: true });
      const filter = root.querySelector("#sot-tree-filter");
      filter?.focus();
      filter?.setSelectionRange(state.sotView.filter.length, state.sotView.filter.length);
    });

    root.querySelector("#repo-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const checkoutInput = event.currentTarget?.elements?.namedItem?.("checkoutPath")
        ?? root.querySelector("#checkout-path");
      if (checkoutSubmissionPending) {
        update({
          ...state,
          repo: {
            ...state.repo,
            message: checkoutPickerPending
              ? "폴더 선택기가 이미 열려 있습니다."
              : "Checkout을 이미 확인하고 있습니다.",
          },
        });
        root.querySelector("#checkout-path")?.focus();
        return;
      }

      checkoutSubmissionPending = true;
      const observedRepoGeneration = repoGeneration;
      let registrationGeneration = null;
      let checkoutPath = String(checkoutInput?.value ?? "");
      const previousRepo = state.repo;

      try {
        if (!checkoutPath.trim()) {
          checkoutPickerPending = true;
          let pickerOutcome;
          try {
            pickerOutcome = await backend.pickCheckoutDirectory();
          } catch (error) {
            if (destroyed || observedRepoGeneration !== repoGeneration) return;
            checkoutPickerPending = false;
            applyPendingSavedRestore(true);
            update({
              ...state,
              repo: {
                ...state.repo,
                phase: state.repo.phase === "idle" ? "error" : state.repo.phase,
                message: safeOperationError("register", error),
              },
            });
            root.querySelector("#checkout-path")?.focus();
            return;
          }
          if (destroyed || observedRepoGeneration !== repoGeneration) return;
          checkoutPickerPending = false;
          if (pickerOutcome?.outcome === "cancelled") {
            applyPendingSavedRestore(true);
            root.querySelector("#checkout-path")?.focus();
            return;
          }
          if (pickerOutcome?.outcome === "failed") {
            applyPendingSavedRestore(true);
            update({
              ...state,
              repo: {
                ...state.repo,
                phase: state.repo.phase === "idle" ? "error" : state.repo.phase,
                message: String(pickerOutcome.reason || "폴더 선택기를 열지 못했습니다."),
              },
            });
            root.querySelector("#checkout-path")?.focus();
            return;
          }
          const selectedPath = pickerOutcome?.outcome === "selected"
            ? String(pickerOutcome.path ?? "")
            : "";
          if (!selectedPath.trim()) {
            applyPendingSavedRestore(true);
            update({
              ...state,
              repo: {
                ...state.repo,
                phase: state.repo.phase === "idle" ? "error" : state.repo.phase,
                message: "선택한 폴더를 읽지 못했습니다.",
              },
            });
            root.querySelector("#checkout-path")?.focus();
            return;
          }
          checkoutPath = selectedPath;
          if (checkoutInput) checkoutInput.value = checkoutPath;
        }
        pendingSavedRestore = null;
        registrationGeneration = ++repoGeneration;

        update({
          ...state,
          repo: { ...state.repo, checkoutPath, phase: "registering", message: "Checkout을 확인하고 있습니다." },
        });

        try {
          const registration = await backend.registerCheckout(checkoutPath);
          if (destroyed || registrationGeneration !== repoGeneration) return;
          if (!registration?.checkout_id) throw new Error("missing checkout_id");
          update({
            ...state,
            repo: {
              checkoutPath,
              phase: "registered",
              checkoutId: registration.checkout_id,
              canonicalPath: registration.canonical_path ?? checkoutPath,
              repoStatus: registration.repo_status ?? null,
              message: "Checkout이 등록되었습니다. SoT snapshot을 구성합니다.",
            },
          });
          await loadSotSnapshot(registration.checkout_id);
        } catch (error) {
          if (destroyed || registrationGeneration !== repoGeneration) return;
          update({
            ...state,
            repo: {
              ...previousRepo,
              checkoutPath,
              phase: "error",
              message: safeOperationError("register", error),
            },
          });
        }
      } finally {
        checkoutSubmissionPending = false;
        checkoutPickerPending = false;
      }
    });

    root.querySelector("#clone-checkout")?.addEventListener("click", async () => {
      pendingSavedRestore = null;
      const generation = ++repoGeneration;
      const previousRepo = state.repo;
      update({
        ...state,
        repo: { ...state.repo, phase: "cloning", message: "Public HarnessKit을 app data directory에 clone하고 있습니다." },
      });

      try {
        const registration = await backend.cloneCheckout();
        if (destroyed || generation !== repoGeneration) return;
        if (!registration?.checkout_id) throw new Error("missing checkout_id");
        update({
          ...state,
          repo: {
            checkoutPath: registration.canonical_path ?? "",
            phase: "registered",
            checkoutId: registration.checkout_id,
            canonicalPath: registration.canonical_path ?? "",
            repoStatus: registration.repo_status ?? null,
            message: "Public HarnessKit checkout이 clone·등록되었습니다.",
          },
        });
        await loadSotSnapshot(registration.checkout_id);
      } catch (error) {
        if (destroyed || generation !== repoGeneration) return;
        update({
          ...state,
          repo: {
            ...previousRepo,
            phase: "error",
            message: safeOperationError("clone", error),
          },
        });
      }
    });

  };
  applyTypographyPreset(options.documentRoot, state.typography);

  render();
  let domainRestoreStarted = false;
  const startAfterBootstrap = () => {
    if (destroyed || domainRestoreStarted) return;
    domainRestoreStarted = true;
    void loadAiProvider();
    localWorkflowController.start();
    const restoreGeneration = repoGeneration;
    const restore = backend.getSotSessionState?.();
    if (!restore) return;
    Promise.resolve(restore)
      .then((registration) => {
        if (destroyed
          || restoreGeneration !== repoGeneration
          || !registration?.checkout_id) return;
        const outcome = { generation: restoreGeneration, registration };
        if (checkoutPickerPending) {
          pendingSavedRestore = outcome;
          return;
        }
        applySavedRestore(outcome);
      })
      .catch(() => {
        if (destroyed
          || restoreGeneration !== repoGeneration) return;
        const outcome = { generation: restoreGeneration, registration: null };
        if (checkoutPickerPending) {
          pendingSavedRestore = outcome;
          return;
        }
        applySavedRestore(outcome);
      });
  };
  if (!options.deferDomainRestore) startAfterBootstrap();
  return Object.freeze({
    getState: () => state,
    startAfterBootstrap,
    applyAppearanceChanged(event) {
      const next = reduceAppearanceChanged(state, event);
      applyAppearanceState(next);
    },
    applyWorkspaceLayoutChanged(workspaceLayout) {
      const nextWorkspaceLayout = normalizeWorkspaceLayoutState(workspaceLayout);
      const disclosureChanged = state.workspaceLayout.leftCollapsed !== nextWorkspaceLayout.leftCollapsed
        || state.workspaceLayout.rightCollapsed !== nextWorkspaceLayout.rightCollapsed;
      state = { ...state, workspaceLayout: nextWorkspaceLayout };
      if (disclosureChanged) {
        render();
        return;
      }
      const preferredWidths = root.querySelector("#workspace-preferred-widths");
      if (preferredWidths) {
        const preferredText = workspacePreferredWidthsText(nextWorkspaceLayout);
        preferredWidths.textContent = preferredText;
        preferredWidths.setAttribute("aria-label", preferredText);
      }
      const notice = root.querySelector(".workspace-layout-save-state");
      if (!notice) return;
      const presentation = workspaceLayoutStatusPresentation(nextWorkspaceLayout);
      notice.hidden = presentation.hidden;
      notice.textContent = presentation.text;
      notice.dataset.layoutRevision = String(presentation.revision);
      notice.classList?.toggle?.("sr-only", presentation.visuallyHidden);
    },
    applyTypographyChanged(typography) {
      applyTypographyState({
        ...state,
        typography: normalizeTypographyState(typography),
      });
    },
    destroy() {
      if (destroyed) return;
      sourcePreviewController.dispose();
      localWorkflowController.dispose();
      destroyed = true;
      cancelPendingLocalScrollRestore();
      localDetailGeneration += 1;
      localActionGeneration += 1;
      localSelectionGeneration += 1;
      installGeneration += 1;
      sotGeneration += 1;
      aiProviderGeneration += 1;
      state = {
        ...state,
        ui: { ...state.ui, localActionBusy: false },
        localView: { ...state.localView, actionStatus: null },
      };
      if (localActionAnnouncer) localActionAnnouncer.textContent = "";
      if (aiStatusAnnouncer) aiStatusAnnouncer.textContent = "";
      releaseToolIdentityFallback();
      try { workflowInspectorBinding.release(); } catch (_error) { /* terminal cleanup */ }
      try { typographyMenuBinding.release(); } catch (_error) { /* terminal cleanup */ }
      try { workbenchDisclosure.release(); } catch (_error) { /* terminal cleanup */ }
      try { graphSession?.dispose(); } catch (_error) { /* terminal cleanup */ }
      localActionAnnouncer?.remove();
      aiStatusAnnouncer?.remove();
      if (typeof root.replaceChildren === "function") root.replaceChildren();
      else root.innerHTML = "";
    },
  });
}
