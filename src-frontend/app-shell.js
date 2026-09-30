import { createInstallState } from "./install-flow.js";
import { renderInstallActionSurface } from "./install-view.js";
import { createAiProviderState, renderAiProviderSettings } from "./ai-explanation.js";
import { createLocalDataState, createLocalViewState } from "./local-state.js";
import { createLocalRemovalState } from "./local-removal-state.js";
import { createSourcePreviewState } from "./source-preview.js";
import {
  DEFAULT_LEFT_WIDTH_PX,
  DEFAULT_RIGHT_WIDTH_PX,
} from "./layout/state.js";
import {
  localResultCount,
  renderLocalExplorer,
  renderLocalInspector,
  renderLocalWorkbench,
} from "./local-view.js";
import {
  createSotViewState,
  renderComponentMap,
  renderProfileMatrixBody,
  renderProfileMatrixHeader,
  renderSotInspector,
  renderSotTree,
} from "./sot-view.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function projectLocalInspectorView(state) {
  return {
    ...state.localView,
    actionBusy: state.ui?.localActionBusy === true,
    sourcePreview: state.sourcePreview,
    aiProvider: state.aiProvider,
    aiExplanation: state.aiExplanation,
  };
}

export function localRuntimeStatusText(localData) {
  return localData?.message || "Local discovery adapter scan 준비 중";
}

function renderLocalRemovalDialog(removal) {
  if (removal?.phase !== "confirming" || removal.confirmation?.open !== true || !removal.plan) {
    return "";
  }
  const plan = removal.plan;
  const effectLabel = (effect) => ({
    shared_config_entry: "Shared config entry 제거",
    dedicated_file: "Dedicated harness file 삭제",
  })[effect] ?? "제거 효과 확인";
  const memberRows = plan.eligibleMembers.map((member) => `<li><strong>${escapeHtml(member.displayName)}</strong><span>${escapeHtml(effectLabel(member.effect))}</span></li>`).join("");
  const blocked = plan.blockedMembers.length
    ? `<p class="notice" role="status">제거할 수 없는 ${plan.blockedMembers.length}개 항목은 변경하지 않고 목록에 유지합니다.</p>`
    : "";
  return `<div class="modal-backdrop local-removal-modal" data-local-removal-dialog role="dialog" aria-modal="true" aria-labelledby="local-removal-title" aria-describedby="local-removal-description">
    <section class="modal-surface local-removal-dialog-surface">
      <header><p class="eyebrow">Local batch removal</p><h2 id="local-removal-title">선택한 ${plan.eligibleMembers.length}개 하네스 제거</h2><p id="local-removal-description">이 작업은 되돌릴 수 없습니다. source group별로 처리되므로 일부 항목만 성공할 수 있습니다.</p></header>
      <ul class="local-removal-members" aria-label="제거 예정 항목">${memberRows}</ul>
      <dl class="local-removal-effects"><div><dt>Shared config entry 제거</dt><dd>${plan.effects.sharedConfigEntryRemovalCount}개</dd></div><div><dt>Dedicated harness file 삭제</dt><dd>${plan.effects.dedicatedFileDeletionCount}개</dd></div><div><dt>부모 폴더 삭제</dt><dd>0개</dd></div></dl>
      <p>성공한 source group 뒤에는 authoritative Local rescan을 자동으로 시작합니다. 새 snapshot이 확인될 때까지 기존 목록 행은 숨기지 않습니다.</p>
      <p>실패한 항목은 목록에 유지하며, 성공한 항목도 새 snapshot에서 실제 부재가 확인된 뒤에만 목록에서 사라집니다.</p>
      ${blocked}
      <label class="local-removal-confirmation"><input type="checkbox" data-local-removal-ack${removal.confirmation.acknowledged ? " checked" : ""} /> 이 작업이 되돌릴 수 없는 제거임을 확인했습니다.</label>
      <div class="ai-provider-actions"><button type="button" class="button button--quiet" data-close-local-removal>취소</button><button type="button" class="button button--primary" data-apply-local-removal${removal.confirmation.acknowledged ? "" : " disabled"}>선택한 항목 제거</button></div>
    </section>
  </div>`;
}

export function renderLocalStatusStack(localData) {
  if (!localData?.message) return "";
  const error = localData.phase === "error";
  return `<p class="notice ${error ? "notice--error" : ""}" role="${error ? "alert" : "status"}">${escapeHtml(localData.message)}</p>`;
}

export function workspaceLayoutStatusPresentation(layout) {
  const revision = Number.isSafeInteger(layout?.revision) && layout.revision >= 0
    ? layout.revision
    : 0;
  const failure = layout?.diagnostic?.safe_message
    ?? (revision > 0 && layout?.persisted === false ? "레이아웃 설정 저장 실패" : "");
  if (failure) {
    return {
      revision,
      hidden: false,
      visuallyHidden: false,
      text: String(failure),
    };
  }
  if (revision > 0 && layout?.persisted === true) {
    return {
      revision,
      hidden: false,
      visuallyHidden: true,
      text: `레이아웃 설정 저장 완료 · revision ${revision}`,
    };
  }
  return { revision, hidden: true, visuallyHidden: false, text: "" };
}

export function workspacePreferredWidthsText(layout) {
  const left = Number.isSafeInteger(layout?.preferredLeftWidthPx)
    ? layout.preferredLeftWidthPx
    : DEFAULT_LEFT_WIDTH_PX;
  const right = Number.isSafeInteger(layout?.preferredRightWidthPx)
    ? layout.preferredRightWidthPx
    : DEFAULT_RIGHT_WIDTH_PX;
  return `선호 패널 너비 좌측 ${left}픽셀, 우측 ${right}픽셀`;
}

export function createInitialState(overrides = {}) {
  return {
    appearance: {
      logical_mode: "System",
      resolved_mode: "Dark",
      revision: 0,
      persisted: true,
      diagnostic: null,
      ...overrides.appearance,
    },
    workspaceLayout: {
      preferredLeftWidthPx: DEFAULT_LEFT_WIDTH_PX,
      preferredRightWidthPx: DEFAULT_RIGHT_WIDTH_PX,
      leftCollapsed: false,
      rightCollapsed: false,
      revision: 0,
      persisted: true,
      diagnostic: null,
      ...overrides.workspaceLayout,
    },
    typography: {
      preset: "Default",
      revision: 0,
      persisted: true,
      diagnostic: null,
      ...overrides.typography,
    },
    repo: {
      checkoutPath: "",
      phase: "idle",
      checkoutId: null,
      canonicalPath: "",
      repoStatus: null,
      message: "",
      ...overrides.repo,
    },
    scan: {
      phase: "idle",
      message: "",
      inventory: null,
      ...overrides.scan,
    },
    ui: {
      activePanel: "dashboard",
      activeSegment: "sot",
      sotDeparture: null,
      localActionBusy: false,
      authoringFlowOpen: false,
      aiSettingsOpen: false,
      aiProviderBusy: false,
      aiProviderMessage: "",
      ignoreEditor: null,
      ...overrides.ui,
      sotDisclosures: {
        componentMap: typeof overrides.ui?.sotDisclosures?.componentMap === "boolean"
          ? overrides.ui.sotDisclosures.componentMap
          : true,
        profileMatrix: typeof overrides.ui?.sotDisclosures?.profileMatrix === "boolean"
          ? overrides.ui.sotDisclosures.profileMatrix
          : false,
      },
    },
    sot: {
      phase: "idle",
      message: "",
      snapshot: null,
      installEvidenceId: null,
      ...overrides.sot,
    },
    sotView: {
      ...createSotViewState(overrides.sot?.snapshot),
      ...overrides.sotView,
    },
    localData: createLocalDataState(overrides.localData),
    localView: createLocalViewState(overrides.localView),
    localRemoval: createLocalRemovalState(overrides.localRemoval),
    sourcePreview: overrides.sourcePreview ?? createSourcePreviewState(),
    aiProvider: createAiProviderState(overrides.aiProvider),
    aiExplanation: overrides.aiExplanation ?? {
      phase: "idle",
      binding: null,
      result: null,
      errorCode: null,
      message: "",
    },
    install: createInstallState(overrides.install),
  };
}

function renderInventoryIssues(inventory) {
  const issues = Array.isArray(inventory?.issues) ? inventory.issues : [];
  if (!issues.length) return "";

  return `<section class="notice notice--error inventory-issues" role="alert" aria-label="스캔 문제">
    <p class="inventory-issues__summary">일부 registry 또는 scan 경로를 처리하지 못했습니다. <strong class="inventory-issues__count">${issues.length}개 문제</strong></p>
    <details class="inventory-issues__details">
      <summary>처리하지 못한 경로 상세</summary>
      <div class="inventory-issues__detail-list"><ul>${issues.map((issue) => `<li>
      <strong>${escapeHtml(issue.category || "scan_issue")}</strong>
      <code>${escapeHtml(issue.path || "경로 없음")}</code>
      <span>${escapeHtml(issue.message || "세부 정보 없음")}</span>
      </li>`).join("")}</ul></div>
    </details>
  </section>`;
}

function renderRecentCommit(commit) {
  if (typeof commit === "string") return escapeHtml(commit);
  const id = commit?.short_id ?? commit?.id ?? commit?.hash ?? "";
  const summary = commit?.summary ?? commit?.message ?? commit?.title ?? "";
  return escapeHtml([id, summary].filter(Boolean).join(" ") || "데이터 없음");
}

function renderRepoFacts(repo) {
  if (!repo.checkoutId) return "";
  const status = repo.repoStatus ?? {};
  const commits = Array.isArray(status.recent_commits) ? status.recent_commits : [];
  const workingTree = status.dirty === true ? "변경 있음" : status.dirty === false ? "Clean" : "데이터 없음";
  const workingTreeClass = status.dirty === true ? "state-warning" : status.dirty === false ? "state-ok" : "";

  return `<dl class="repo-facts">
    <div><dt>Local branch</dt><dd>${escapeHtml(status.branch || "데이터 없음")}</dd></div>
    <div><dt>Working tree</dt><dd class="${workingTreeClass}">${workingTree}</dd></div>
    <div><dt>Recent commits</dt><dd>${commits.length ? `<ul>${commits.slice(0, 5).map((commit) => `<li>${renderRecentCommit(commit)}</li>`).join("")}</ul>` : "데이터 없음"}</dd></div>
  </dl>`;
}

function renderCheckoutSummaryFacts(summary) {
  const commits = Array.isArray(summary?.recent_commits) ? summary.recent_commits : [];
  const branch = summary?.branch || (summary?.detached ? "detached" : "데이터 없음");
  const workingTree = summary?.dirty === true ? "변경 있음" : summary?.dirty === false ? "Clean" : "데이터 없음";

  return `<dl class="repo-facts">
    <div><dt>Branch</dt><dd>${escapeHtml(branch)}</dd></div>
    <div><dt>Working tree</dt><dd>${workingTree}</dd></div>
    <div><dt>Recent commits</dt><dd>${commits.length ? `<ul>${commits.slice(0, 5).map((commit) => `<li>${renderRecentCommit(commit)}</li>`).join("")}</ul>` : "데이터 없음"}</dd></div>
  </dl>`;
}

function renderSegmentedControl(activeSegment, {
  underlayAttributes = "",
  authoringAvailable = false,
  refreshDisabled = true,
  refreshBusy = false,
} = {}) {
  const refreshLabel = activeSegment === "sot"
    ? `Harness Components 새로고침${refreshBusy ? " 중" : ""}`
    : `PC 설치 하네스 새로고침${refreshBusy ? " 중" : ""}`;
  return `<div class="dashboard-navigation"${underlayAttributes}>
    <nav class="dashboard-segments" role="tablist" aria-label="Dashboard sections">
      <button id="sot-segment" type="button" role="tab" class="dashboard-segment${activeSegment === "sot" ? " dashboard-segment--active" : ""}" aria-selected="${activeSegment === "sot"}" tabindex="${activeSegment === "sot" ? "0" : "-1"}" aria-controls="sot-workspace" data-dashboard-segment="sot">Harness Components</button>
      <button id="local-segment" type="button" role="tab" class="dashboard-segment${activeSegment === "local" ? " dashboard-segment--active" : ""}" aria-selected="${activeSegment === "local"}" tabindex="${activeSegment === "local" ? "0" : "-1"}" aria-controls="local-workspace" data-dashboard-segment="local">PC 설치 하네스</button>
    </nav>
    <button id="dashboard-refresh" type="button" class="button button--secondary dashboard-refresh" aria-label="${refreshLabel}" title="${refreshLabel}"${refreshBusy ? ' aria-busy="true"' : ""}${refreshDisabled ? " disabled" : ""}><span class="dashboard-refresh__glyph" aria-hidden="true"></span></button>
    <button type="button" class="button button--secondary dashboard-authoring-action" data-open-authoring-flow${authoringAvailable ? "" : " disabled"}>Component Dev Guide</button>
  </div>`;
}

function sotInventoryStatus(state, snapshot) {
  if (!state.repo.checkoutId) {
    return { dotClass: "", message: "HarnessKit checkout이 연결되지 않았습니다." };
  }
  if (state.sot.phase === "loading") {
    return { dotClass: "", message: state.sot.message || "SoT snapshot을 읽고 있습니다." };
  }
  if (state.sot.phase === "ready") {
    const issueCount = Array.isArray(snapshot?.issues) ? snapshot.issues.length : 0;
    if (issueCount > 0) {
      return {
        dotClass: "state-dot--warning",
        message: `일부 registry 또는 scan 경로를 처리하지 못했습니다. ${issueCount}개 문제`,
      };
    }
    return {
      dotClass: "state-dot--ready",
      message: state.sot.message || `${snapshot?.components?.length ?? 0}개 registry component를 불러왔습니다.`,
    };
  }
  if (state.sot.phase === "error") {
    return {
      dotClass: "state-dot--error",
      message: state.sot.message || "SoT snapshot을 불러오지 못했습니다.",
    };
  }
  if (["registering", "cloning", "error"].includes(state.repo.phase)) {
    return {
      dotClass: state.repo.phase === "error" ? "state-dot--error" : "",
      message: state.repo.message || "HarnessKit checkout을 연결하고 있습니다.",
    };
  }
  return { dotClass: "", message: state.repo.message || "SoT snapshot을 불러올 준비가 되었습니다." };
}

function renderSotUnavailable(state) {
  if (state.sot.phase === "loading") {
    return `<section class="sot-unavailable" aria-busy="true"><span class="atlas-loader" aria-hidden="true"></span><h2>SoT snapshot을 읽고 있습니다</h2><p>Registry, canonical manifests와 profile membership을 typed projection으로 구성합니다.</p></section>`;
  }
  return `<section class="sot-unavailable"${state.sot.phase === "error" ? ' role="alert"' : ""}>
    <p class="eyebrow">Source unavailable</p>
    <h2>${state.repo.checkoutId ? "SoT snapshot을 불러오지 못했습니다" : "SoT 명세를 보려면 HarnessKit 저장소가 필요합니다."}</h2>
    <p>${escapeHtml(state.sot.message || (state.repo.checkoutId
      ? "명세 불러오기를 다시 실행하거나 checkout 상태를 확인하세요."
      : "기존 폴더를 선택하거나 새로 내려받으세요."))}</p>
    ${state.repo.checkoutId ? "" : `<div class="sot-unavailable-actions">
      <div class="sot-unavailable-action"><button type="button" class="button button--secondary" data-open-checkout-picker>기존 HarnessKit 폴더 선택</button></div>
      <div class="sot-unavailable-action"><button id="clone-checkout" type="button" class="button button--primary">HarnessKit 저장소 내려받기</button><small>공개 저장소를 앱 전용 폴더에 내려받아 SoT로 연결합니다.</small></div>
    </div>`}
  </section>`;
}

const AUTHORING_WORKFLOW_ID = "harnesskit.workflow.harness-creation";

function findAuthoringWorkflow(snapshot) {
  return snapshot?.workflows?.find(
    (workflow) => workflow.workflow_id === AUTHORING_WORKFLOW_ID,
  ) ?? null;
}

function authoringValueLabel(snapshot, value) {
  const normalized = String(value ?? "").trim();
  if (!normalized) return "";
  const component = snapshot?.components?.find(
    (candidate) => candidate.component_id === normalized,
  );
  if (component?.title) return `${component.title} · ${normalized}`;
  return normalized;
}

function renderAuthoringField(snapshot, field, label, value) {
  const displayValue = field === "output"
    ? String(value ?? "").trim()
    : authoringValueLabel(snapshot, value);
  if (!displayValue) return "";
  return `<div class="authoring-field"><dt>${label}</dt><dd>${escapeHtml(displayValue)}</dd></div>`;
}

function renderAuthoringStep(snapshot, step) {
  const fields = [
    renderAuthoringField(snapshot, "agent", "에이전트", step.authored_fields?.agent),
    renderAuthoringField(snapshot, "skill", "스킬", step.authored_fields?.skill),
    renderAuthoringField(snapshot, "output", "산출물", step.authored_fields?.output),
  ].join("");
  const details = fields
    ? `<details class="authoring-step-details"><summary>담당과 산출물</summary><dl>${fields}</dl></details>`
    : "";
  return `<li class="authoring-step" data-authoring-step="${escapeHtml(step.step_id)}">
    <span class="authoring-step-ordinal" aria-hidden="true">${escapeHtml(step.ordinal)}</span>
    <div class="authoring-step-content"><h3>${escapeHtml(step.title || step.step_id)}</h3><p>${escapeHtml(step.description || "설명 정보 없음")}</p>${details}</div>
  </li>`;
}

function renderAuthoringFlowDialog(snapshot, workflow) {
  const steps = Array.isArray(workflow?.steps) ? workflow.steps : [];
  return `<div class="modal-backdrop authoring-flow-modal" data-authoring-flow-dialog role="dialog" aria-modal="true" aria-labelledby="authoring-flow-title" aria-describedby="authoring-flow-description">
    <section class="modal-surface authoring-flow-surface">
      <header><div><p class="eyebrow">컴포넌트 제작 안내 · ${escapeHtml(steps.length)}단계</p><h2 id="authoring-flow-title">${escapeHtml(workflow.title || "Component Dev Guide")}</h2></div><button type="button" class="button button--quiet" data-close-authoring-flow aria-label="제작 흐름 닫기">닫기</button></header>
      <p id="authoring-flow-description" class="authoring-flow-description">${escapeHtml(workflow.description || "요구사항부터 검증까지의 제작 순서를 안내합니다.")}</p>
      <ol class="authoring-steps">${steps.map((step) => renderAuthoringStep(snapshot, step)).join("")}</ol>
    </section>
  </div>`;
}

export function renderAppShell(state) {
  const activeSegment = state.ui?.activeSegment === "local" ? "local" : "sot";
  const snapshot = state.sot?.snapshot;
  const sotReady = state.sot?.phase === "ready" && snapshot;
  const selectedSotComponent = sotReady
    && !state.sotView?.selectedWorkflowId
    ? snapshot.components.find(
      (component) => component.component_id === state.sotView?.selectedComponentId,
    ) ?? null
    : null;
  const selectedSotWorkflow = sotReady
    ? snapshot.workflows?.find(
      (workflow) => workflow.workflow_id === state.sotView?.selectedWorkflowId,
    ) ?? null
    : null;
  const authoringWorkflow = sotReady ? findAuthoringWorkflow(snapshot) : null;
  const authoringFlowOpen = Boolean(authoringWorkflow)
    && state.ui?.authoringFlowOpen === true;
  const componentMapExpanded = state.ui?.sotDisclosures?.componentMap !== false;
  const profileMatrixExpanded = state.ui?.sotDisclosures?.profileMatrix === true;
  const checkoutPath = String(state.repo.checkoutPath ?? "");
  const repoBusy = ["cloning", "registering"].includes(state.repo.phase) || state.sot.phase === "loading";
  const folderLabel = ["cloning", "registering"].includes(state.repo.phase)
    ? "HarnessKit 연결 중"
    : "HarnessKit 폴더 연결";
  const localStartPending = state.localData?.scanStartPending === true;
  const localScanBusy = localStartPending || state.localData?.currentAttempt?.state === "running";
  const localStatus = localStartPending
    ? { ...state.localData, phase: "pending", message: "Local scan 시작을 요청하고 있습니다." }
    : state.localData;
  const appearanceMode = ["System", "Light", "Dark"].includes(state.appearance?.logical_mode)
    ? state.appearance.logical_mode
    : "System";
  const localView = projectLocalInspectorView(state);
  const inventoryStatus = activeSegment === "sot" ? sotInventoryStatus(state, snapshot) : null;
  const count = activeSegment === "sot"
    ? snapshot?.components?.length ?? 0
    : localResultCount(state.localData);
  const statusText = activeSegment === "sot"
    ? inventoryStatus?.message || "SoT checkout 연결 대기"
    : localRuntimeStatusText(localStatus);
  const localRemovalDialogOpen = state.localRemoval?.phase === "confirming"
    && state.localRemoval.confirmation?.open === true;
  const underlayAttributes = state.ui?.aiSettingsOpen || authoringFlowOpen || localRemovalDialogOpen
    ? ' inert aria-hidden="true"'
    : "";
  const layoutStatus = workspaceLayoutStatusPresentation(state.workspaceLayout);
  const preferredWidthsText = workspacePreferredWidthsText(state.workspaceLayout);
  const leftCollapsed = state.workspaceLayout?.leftCollapsed === true;
  const rightCollapsed = state.workspaceLayout?.rightCollapsed === true;
  const typographyPreset = ["Small", "Default", "Large"].includes(state.typography?.preset)
    ? state.typography.preset
    : "Default";
  const typographyLabel = {
    Small: "작게",
    Default: "기본",
    Large: "크게",
  }[typographyPreset];
  return `<div class="desktop-app">
    <header class="command-bar"${underlayAttributes}>
      <button id="left-pane-disclosure" type="button" class="pane-disclosure pane-disclosure--icon" data-pane-disclosure="left" aria-label="탐색 패널 ${leftCollapsed ? "열기" : "닫기"}" aria-controls="workspace-left-pane" aria-expanded="${!leftCollapsed}" title="탐색 패널 ${leftCollapsed ? "열기" : "닫기"}"><span class="pane-disclosure__glyph" aria-hidden="true"></span></button>
      <div class="app-identity"><img class="app-mark" src="./assets/branding/harness-desktop-64.png" alt="" aria-hidden="true" /><div><h1>HarnessKit</h1><p>HarnessKit specification atlas</p></div></div>
      <form id="repo-form" class="command-repo" aria-labelledby="repo-title">
        <label id="repo-title" class="sr-only" for="checkout-path">로컬 checkout 경로</label>
        <span class="repo-indicator" aria-hidden="true"></span>
        <input id="checkout-path" name="checkoutPath" type="text" value="${escapeHtml(checkoutPath)}" placeholder="~/Projects/harnesskit" autocomplete="off" spellcheck="false" aria-describedby="checkout-help" />
        <button id="register-checkout" type="submit" class="button button--secondary command-repo__folder" aria-label="${folderLabel}" title="${folderLabel}"${repoBusy ? " disabled" : ""}><span class="repo-folder-glyph" aria-hidden="true"></span></button>
      </form>
      <div class="command-meta">
        <fieldset class="appearance-control"><legend class="sr-only">컬러 모드</legend>${["System", "Light", "Dark"].map((mode) => `<label><input type="radio" name="appearance-mode" value="${mode}"${appearanceMode === mode ? " checked" : ""} /><span>${mode}</span></label>`).join("")}</fieldset>
        <div class="typography-menu-control"><button id="typography-menu-trigger" type="button" class="typography-menu-trigger" aria-haspopup="menu" aria-expanded="false" aria-controls="typography-menu" aria-label="글자 크기: ${typographyLabel}" title="글자 크기: ${typographyLabel}"><span aria-hidden="true">Aᴬ</span></button><div id="typography-menu" class="typography-menu" role="menu" aria-label="글자 크기 선택" hidden><button type="button" role="menuitemradio" tabindex="-1" data-typography-preset="Small" aria-checked="${typographyPreset === "Small"}">작게</button><button type="button" role="menuitemradio" tabindex="-1" data-typography-preset="Default" aria-checked="${typographyPreset === "Default"}">기본</button><button type="button" role="menuitemradio" tabindex="-1" data-typography-preset="Large" aria-checked="${typographyPreset === "Large"}">크게</button></div></div>
        <span class="runtime-badge" aria-label="초기 지원 운영체제 macOS">macOS · local</span>
        <a class="license-link" href="./legal/third-party-notices.html" target="_self">오픈소스 라이선스</a>
        <span class="appearance-save-state" role="status"${state.appearance?.diagnostic || state.appearance?.persisted === false ? "" : " hidden"}>${escapeHtml(state.appearance?.diagnostic?.safe_message || (state.appearance?.persisted === false ? "설정 저장 실패" : ""))}</span>
        <span class="workspace-layout-save-state${layoutStatus.visuallyHidden ? " sr-only" : ""}" role="status" data-layout-revision="${layoutStatus.revision}"${layoutStatus.hidden ? " hidden" : ""}>${escapeHtml(layoutStatus.text)}</span>
      </div>
      <button id="right-pane-disclosure" type="button" class="pane-disclosure pane-disclosure--icon" data-pane-disclosure="right" aria-label="상세 패널 ${rightCollapsed ? "열기" : "닫기"}" aria-controls="workspace-right-pane" aria-expanded="${!rightCollapsed}" title="상세 패널 ${rightCollapsed ? "열기" : "닫기"}"><span class="pane-disclosure__glyph" aria-hidden="true"></span></button>
    </header>

    ${renderSegmentedControl(activeSegment, {
      underlayAttributes,
      authoringAvailable: Boolean(authoringWorkflow),
      refreshDisabled: activeSegment === "sot" ? !state.repo.checkoutId || repoBusy : localScanBusy,
      refreshBusy: activeSegment === "sot" ? state.sot.phase === "loading" : localScanBusy,
    })}

    <div class="desktop-shell" data-workspace-shell data-layout-mode="three-pane" data-left-collapsed="${leftCollapsed}" data-right-collapsed="${rightCollapsed}" aria-label="Harness dashboard workspace"${underlayAttributes}>
      <span id="workspace-preferred-widths" class="sr-only" aria-label="${preferredWidthsText}">${preferredWidthsText}</span>
      <aside id="workspace-left-pane" class="pane pane--inventory" aria-labelledby="tree-title"${leftCollapsed ? " hidden inert" : ""}>
        <div class="pane-heading"><div><p class="eyebrow">${activeSegment === "sot" ? "Source of truth" : "Local filesystem"}</p><h2 id="tree-title">${activeSegment === "sot" ? "Component inventory" : "Discovered harnesses"}</h2></div><span class="count">${count}</span></div>
        <div class="inventory-state" role="status" aria-live="polite"><span class="state-dot ${activeSegment === "sot" ? inventoryStatus.dotClass : ["ready", "partial"].includes(localStatus.phase) ? "state-dot--ready" : localStatus.phase === "error" ? "state-dot--error" : ""}" aria-hidden="true"></span><span class="inventory-state-text">${escapeHtml(activeSegment === "sot" ? inventoryStatus.message : "Checkout-independent scan")}</span></div>
        <p id="checkout-help" class="field-help">${activeSegment === "sot" ? "HarnessKit 저장소의 컴포넌트 명세를 읽기 전용으로 보여줍니다. 이 화면에서는 파일을 설치하거나 변경하지 않습니다." : "Local scan은 SoT checkout과 분리된 discovery adapter만 사용합니다."}</p>
        ${activeSegment === "local" ? `<div class="status-stack" data-local-status-stack>${renderLocalStatusStack(localStatus)}</div>` : ""}
        ${activeSegment === "sot" && snapshot ? renderInventoryIssues({ issues: snapshot.issues.map((issue) => ({ category: issue.code, path: issue.source_path, message: issue.safe_message })) }) : ""}
        ${activeSegment === "sot"
          ? (sotReady ? `<div data-sot-tree>${renderSotTree(snapshot, state.sotView)}</div>` : '<p class="empty-state">Checkout을 연결하면 kind별 component tree가 표시됩니다.</p>')
          : renderLocalExplorer(state.localData, state.localView)}
      </aside>

      ${leftCollapsed ? "" : '<div class="workspace-divider workspace-divider--left" role="separator" data-workspace-divider="left" tabindex="0" aria-label="좌측 탐색 패널 너비" aria-orientation="vertical" aria-controls="workspace-left-pane" aria-valuemin="200" aria-valuemax="328" aria-valuenow="304" aria-valuetext="304픽셀"></div>'}

      <main id="workbench" class="pane pane--workbench" aria-label="중앙 작업 영역" data-workbench-scroll>
        ${activeSegment === "sot"
          ? `<div id="sot-workspace" class="sot-workbench" data-component-map-expanded="${componentMapExpanded}" data-profile-matrix-expanded="${profileMatrixExpanded}" role="tabpanel" aria-labelledby="sot-segment">${sotReady ? `<div id="sot-workbench-first-viewport" class="sot-workbench__first-viewport" role="group" aria-label="Component Map first viewport">${renderComponentMap(snapshot, state.sotView, componentMapExpanded)}${renderProfileMatrixHeader(profileMatrixExpanded)}</div>${renderProfileMatrixBody(snapshot, state.sotView, profileMatrixExpanded)}` : renderSotUnavailable(state)}</div><div id="local-workspace" role="tabpanel" aria-labelledby="local-segment" hidden></div>`
          : `<div id="sot-workspace" role="tabpanel" aria-labelledby="sot-segment" hidden></div>${renderLocalWorkbench(state.localData, localView, state.localRemoval)}`}
      </main>

      ${rightCollapsed ? "" : '<div class="workspace-divider workspace-divider--right" role="separator" data-workspace-divider="right" tabindex="0" aria-label="우측 상세 패널 너비" aria-orientation="vertical" aria-controls="workspace-right-pane" aria-valuemin="184" aria-valuemax="392" aria-valuenow="368" aria-valuetext="368픽셀"></div>'}

      <aside id="workspace-right-pane" class="pane pane--inspector" aria-labelledby="detail-title"${rightCollapsed ? " hidden inert" : ""}>
        <section class="inspector-section"><div class="pane-heading"><div><p class="eyebrow">${activeSegment === "sot" ? "SoT selection" : "PC 설치 하네스"}</p><h2 id="detail-title">${activeSegment === "sot" ? selectedSotWorkflow ? "Workflow detail" : "Component detail" : "하네스 상세"}</h2></div></div><div class="inspector-content" ${activeSegment === "sot" ? 'data-sot-inspector aria-live="polite" aria-atomic="false"' : "data-local-inspector"}>${activeSegment === "sot" && sotReady ? renderSotInspector(snapshot, state.sotView) : activeSegment === "local" ? renderLocalInspector(state.localData, localView) : '<p class="empty-state">Snapshot을 불러오면 deterministic metadata가 표시됩니다.</p>'}</div>${activeSegment === "sot" && sotReady && !selectedSotWorkflow ? `<div data-sot-install-host>${renderInstallActionSurface({ install: state.install, component: selectedSotComponent, operationBlocked: localScanBusy })}</div>` : ""}</section>
        ${activeSegment === "sot" && state.repo.checkoutId ? `<section class="inspector-section inspector-section--repo" aria-label="Checkout state">${sotReady ? `<p class="eyebrow">Repository state</p>${renderCheckoutSummaryFacts(snapshot.checkout_summary)}` : renderRepoFacts(state.repo)}</section>` : ""}
      </aside>
    </div>

    <footer class="app-statusbar"${underlayAttributes}><span class="state-dot ${activeSegment === "sot" ? inventoryStatus?.dotClass || "" : ["ready", "partial"].includes(localStatus.phase) ? "state-dot--ready" : localStatus.phase === "error" ? "state-dot--error" : ""}" aria-hidden="true"></span><p${activeSegment === "local" ? ' id="local-runtime-status"' : ""} role="status" aria-live="polite">${escapeHtml(statusText)}</p><span${activeSegment === "local" ? ' id="local-runtime-snapshot"' : ""}>${activeSegment === "sot" ? escapeHtml(snapshot?.snapshot_id ? `snapshot ${snapshot.snapshot_id.slice(0, 12)}` : "checkout 미등록 · snapshot 대기") : escapeHtml(state.localData.latestComplete?.snapshotId ? `snapshot ${state.localData.latestComplete.snapshotId.slice(0, 12)}` : "local adapter scan · checkout independent")}</span></footer>
    ${state.ui?.aiSettingsOpen ? `<div class="modal-backdrop ai-provider-modal" data-ai-provider-dialog role="dialog" aria-modal="true" aria-labelledby="ai-provider-title"><div class="modal-surface">${renderAiProviderSettings(state.aiProvider, { busy: state.ui.aiProviderBusy })}${state.ui.aiProviderMessage ? `<p class="ai-provider-message" role="status">${escapeHtml(state.ui.aiProviderMessage)}</p>` : ""}</div></div>` : ""}
    ${authoringFlowOpen ? renderAuthoringFlowDialog(snapshot, authoringWorkflow) : ""}
    ${renderLocalRemovalDialog(state.localRemoval)}
    ${state.ui?.ignoreEditor?.open ? `<div class="modal-backdrop project-ignore-modal" data-project-ignore-dialog role="dialog" aria-modal="true" aria-labelledby="project-ignore-title"><form class="modal-surface project-ignore-editor" data-project-ignore-form><header><p class="eyebrow">Project local scan policy</p><h2 id="project-ignore-title">스캔 제외 규칙 편집</h2><p>.harnesskitignore의 정확한 텍스트만 저장합니다. 저장 뒤 전체 Local scan을 다시 시작합니다.</p></header><label for="project-ignore-text">.harnesskitignore</label><textarea id="project-ignore-text" name="exactText" rows="14" spellcheck="false"${state.ui.ignoreEditor.busy ? " disabled" : ""}>${escapeHtml(state.ui.ignoreEditor.exactText ?? "")}</textarea>${state.ui.ignoreEditor.issue ? `<p class="notice notice--error" role="alert">${escapeHtml(state.ui.ignoreEditor.issue)}</p>` : ""}<div class="ai-provider-actions"><button type="button" class="button button--quiet" data-close-project-ignore${state.ui.ignoreEditor.busy ? " disabled" : ""}>취소</button><button id="project-ignore-save" type="submit" class="button button--primary"${state.ui.ignoreEditor.busy || !state.ui.ignoreEditor.sourceRevision ? " disabled" : ""}>저장하고 다시 스캔</button></div></form></div>` : ""}
  </div>`;
}
