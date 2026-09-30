import {
  activeLocalSnapshotHeader,
  LOCAL_RESULT_PAGE_SIZE,
  LOCAL_ROOT_ID,
} from "./local-state.js";
import { AI_TRANSPORT_WARNING } from "./ai-explanation.js";
import { renderToolIdentity } from "./tool-identities.js";

const TOOL_LABELS = Object.freeze({
  codex: "Codex",
  claude_code: "Claude Code",
  antigravity: "Antigravity IDE",
  antigravity_ide: "Antigravity IDE",
  antigravity_cli: "Antigravity CLI",
  hermes: "Hermes",
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function optionalText(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

function queryResult(data) {
  return data?.queryResult ?? null;
}

function normalizeCorrelation(value) {
  const correlation = value && typeof value === "object" ? value : {};
  return {
    state: (optionalText(correlation.state ?? correlation.status) ?? "uncorrelated").toLowerCase(),
    componentId: optionalText(correlation.componentId ?? correlation.component_id),
    method: optionalText(correlation.method),
    evidenceRef: optionalText(correlation.evidenceRef ?? correlation.evidence_ref),
    safeReason: optionalText(correlation.safeReason ?? correlation.safe_reason),
  };
}

function normalizeItem(value) {
  if (!value || typeof value !== "object") return null;
  const instanceId = optionalText(value.instanceId ?? value.instance_id);
  if (!instanceId) return null;
  return {
    instanceId,
    adapterId: optionalText(value.adapterId ?? value.adapter_id),
    adapterVersion: optionalText(value.adapterVersion ?? value.adapter_version),
    toolId: optionalText(value.toolId ?? value.tool_id),
    surfaceId: optionalText(value.surfaceId ?? value.surface_id),
    scope: optionalText(value.scope) ?? "unknown",
    projectId: optionalText(value.projectId ?? value.project_id),
    safeLocator: optionalText(
      value.safeLocator
        ?? value.safe_locator
        ?? value.stableSourceLocator
        ?? value.stable_source_locator,
    ),
    kind: optionalText(value.kind) ?? "unclassified",
    displayName: optionalText(value.displayName ?? value.display_name)
      ?? "이름 정보 없음",
    name: optionalText(value.name),
    description: optionalText(value.description),
    descriptionSource: optionalText(value.descriptionSource ?? value.description_source),
    parseState: optionalText(value.parseState ?? value.parse_state) ?? "unknown",
    removable: value.removable !== false && value.removalEligible !== false
      && value.removal_eligible !== false,
    issueCodes: Array.isArray(value.issueCodes ?? value.issue_codes)
      ? (value.issueCodes ?? value.issue_codes).map(optionalText).filter(Boolean)
      : [],
    settings: Array.isArray(value.settings) ? value.settings : [],
    size: Number.isFinite(value.size) ? value.size : null,
    modifiedUnixMillis: Number.isFinite(value.modifiedUnixMillis ?? value.modified_unix_millis)
      ? value.modifiedUnixMillis ?? value.modified_unix_millis
      : null,
    correlation: normalizeCorrelation(value.correlation),
  };
}

function localItems(data) {
  const values = queryResult(data)?.items;
  return Array.isArray(values) ? values.map(normalizeItem).filter(Boolean) : [];
}

function localRemovalStatusText(removalState) {
  const rescanState = removalState?.rescan?.state;
  if (removalState?.phase === "awaiting_rescan") {
    if (removalState?.outcome?.indeterminateSourceGroupCount > 0) {
      return "외부 변경으로 제거 결과를 확정할 수 없어 새 Local snapshot으로 확인 중입니다.";
    }
    return "제거 결과를 확인하는 새 Local snapshot을 기다리고 있습니다. 기존 항목은 아직 목록에 남아 있습니다.";
  }
  if (removalState?.phase === "reconciling") {
    return "새 Local snapshot에서 실제 제거 여부를 확인 중입니다.";
  }
  if (rescanState === "failed") {
    return "재스캔을 시작하지 못했습니다. 목록의 항목을 확인한 뒤 다시 제거할 수 있습니다.";
  }
  if (rescanState === "coverage_incomplete") {
    return "스캔 coverage가 불완전해 제거를 확정하지 않았습니다.";
  }
  if (rescanState === "unverified") {
    return "새 Local snapshot을 확인하지 못해 제거를 확정하지 않았습니다.";
  }
  if (removalState?.phase === "complete" && removalState?.outcome?.failedSourceGroupCount > 0) {
    return `제거하지 못한 ${removalState.outcome.failedSourceGroupCount}개 source group의 항목은 목록에 남아 있습니다.`;
  }
  return null;
}

function localRemovalOutcomeText(removalState, instanceId) {
  const groups = removalState?.outcome?.sourceGroupOutcomes;
  if (!Array.isArray(groups)) return null;
  const group = groups.find((candidate) => candidate?.memberIds?.includes(instanceId));
  if (!group || group.state === "success") return null;
  if (group.state === "indeterminate") return "제거 결과를 확정하지 못했습니다.";
  return "제거하지 못했습니다. 항목은 유지됩니다.";
}

function toolFilters(data) {
  const result = queryResult(data) ?? {};
  const declared = Array.isArray(data?.qualifiedTools) && data.qualifiedTools.length
    ? data.qualifiedTools
    : result.qualifiedTools ?? result.qualified_tools ?? result.tools;
  const tools = Array.isArray(declared)
    ? declared
      .filter((tool) => {
        const status = optionalText(tool?.status)?.toLowerCase();
        return status !== "unavailable" && tool?.qualified !== false;
      })
      .map((tool) => ({
        toolId: optionalText(tool?.toolId ?? tool?.tool_id ?? tool?.id),
        label: optionalText(tool?.label ?? tool?.name),
      }))
    : localItems(data).map((item) => ({ toolId: item.toolId, label: null }));
  const unique = new Map();
  tools.forEach(({ toolId, label }) => {
    if (toolId && !unique.has(toolId)) unique.set(toolId, label ?? TOOL_LABELS[toolId] ?? toolId);
  });
  return [...unique].map(([toolId, label]) => ({ toolId, label }));
}

function kindFilters(data) {
  const result = queryResult(data) ?? {};
  const declared = result.kindCounts ?? result.kind_counts;
  const entries = Array.isArray(declared)
    ? declared.map((entry) => ({
      kind: optionalText(entry?.kind),
      count: Number(entry?.count ?? 0),
    }))
    : Object.entries(declared ?? {}).map(([kind, count]) => ({ kind, count: Number(count) }));
  if (entries.length) return entries.filter(({ kind, count }) => kind && count > 0);
  const counts = new Map();
  localItems(data).forEach((item) => counts.set(item.kind, (counts.get(item.kind) ?? 0) + 1));
  return [...counts].map(([kind, count]) => ({ kind, count }));
}

function projectFilters(data) {
  const result = queryResult(data) ?? {};
  const declared = Array.isArray(data?.projects) && data.projects.length
    ? data.projects
    : result.projects;
  const values = Array.isArray(declared)
    ? declared.map((project) => ({
      projectId: optionalText(project?.projectId ?? project?.project_id ?? project?.id),
      label: optionalText(project?.displayName ?? project?.display_name
        ?? project?.label ?? project?.name ?? project?.title),
      canonicalPath: optionalText(project?.canonicalPath ?? project?.canonical_path),
    }))
    : localItems(data).map((item) => ({ projectId: item.projectId, label: null, canonicalPath: null }));
  const unique = new Map();
  values.forEach(({ projectId, label, canonicalPath }) => {
    if (projectId && !unique.has(projectId)) unique.set(projectId, {
      projectId,
      label: label ?? "이름을 불러올 수 없음",
      canonicalPath,
    });
  });
  return [...unique.values()];
}

function treeButton({
  label,
  canonicalPath = null,
  selected,
  scope,
  projectId = null,
  level,
  pinned = false,
  controlId = null,
}) {
  const accessibleLabel = `${canonicalPath ? `${label} · ${canonicalPath}` : label}${pinned ? " · 현재 선택 유지" : ""}`;
  const copy = canonicalPath
    ? `<span class="local-project-copy"><strong>${escapeHtml(label)}</strong>${pinned ? '<em class="local-project-pin">선택 유지</em>' : ""}<small>${escapeHtml(canonicalPath)}</small></span>`
    : `<span>${escapeHtml(label)}</span>`;
  const id = controlId ?? (scope === "all" ? "local-scope-all" : null);
  return `<button${id ? ` id="${escapeHtml(id)}"` : ""} type="button" class="local-explorer-row local-explorer-row--level-${level}" role="treeitem" aria-level="${level}" aria-selected="${selected}" aria-label="${escapeHtml(accessibleLabel)}" tabindex="${selected ? "0" : "-1"}" data-local-scope="${escapeHtml(scope)}"${projectId ? ` data-local-project-id="${escapeHtml(projectId)}"` : ""}${pinned ? ' data-local-project-pinned="true"' : ""} title="${escapeHtml(accessibleLabel)}"><span aria-hidden="true">${level === 1 ? "◇" : "·"}</span>${copy}</button>`;
}

function localProjectExplorerProjection(data, view) {
  const allProjects = projectFilters(data);
  const requestedId = view?.explorerId ?? LOCAL_ROOT_ID;
  const projectQuery = String(view?.projectExplorerQuery ?? "");
  const normalizedQuery = projectQuery.trim().toLowerCase();
  const matchingProjects = normalizedQuery
    ? allProjects.filter(({ label }) => label.toLowerCase().includes(normalizedQuery))
    : allProjects;
  const selectedProject = allProjects.find(({ projectId }) => projectId === requestedId) ?? null;
  const pinnedProject = selectedProject
    && !matchingProjects.some(({ projectId }) => projectId === selectedProject.projectId)
    ? selectedProject
    : null;
  const projects = pinnedProject ? [pinnedProject, ...matchingProjects] : matchingProjects;
  const availableIds = new Set([
    LOCAL_ROOT_ID,
    "local-user",
    "local-projects",
    ...allProjects.map(({ projectId }) => projectId),
  ]);
  const selectedId = availableIds.has(requestedId) ? requestedId : LOCAL_ROOT_ID;
  return {
    matchingProjects,
    normalizedQuery,
    pinnedProject,
    projects,
    selectedId,
  };
}

export function renderLocalProjectRows(data, view) {
  const {
    matchingProjects,
    normalizedQuery,
    pinnedProject,
    projects,
    selectedId,
  } = localProjectExplorerProjection(data, view);
  const rows = projects.map(({ projectId, label, canonicalPath }, index) => treeButton({
    label,
    canonicalPath,
    selected: selectedId === projectId,
    scope: "project",
    projectId,
    level: 2,
    pinned: pinnedProject?.projectId === projectId,
    controlId: `local-project-scope-${index}`,
  })).join("");
  const empty = normalizedQuery && matchingProjects.length === 0
    ? '<p class="local-explorer-empty">이름이 일치하는 project 없음</p>'
    : !normalizedQuery && projects.length === 0
      ? '<p class="local-explorer-empty">탐지된 project 없음</p>'
      : "";
  return `${rows}${empty}`;
}

export function renderLocalExplorer(data, view) {
  const projectQuery = String(view?.projectExplorerQuery ?? "");
  const { selectedId } = localProjectExplorerProjection(data, view);
  return `<div class="local-explorer-shell">
    <div class="local-explorer-search">
      <label for="local-project-search">프로젝트 이름 검색</label>
      <input id="local-project-search" type="search" value="${escapeHtml(projectQuery)}" placeholder="프로젝트 이름" autocomplete="off" spellcheck="false" aria-describedby="local-project-search-help" />
      <small id="local-project-search-help">경로는 검색하지 않습니다.</small>
    </div>
    <div class="local-explorer" role="tree" aria-label="Local scan location">
      ${treeButton({ label: "전체 위치", selected: selectedId === LOCAL_ROOT_ID, scope: "all", level: 1 })}
      ${treeButton({ label: "User", selected: selectedId === "local-user", scope: "user", level: 2 })}
      ${treeButton({ label: "Project 전체", selected: selectedId === "local-projects", scope: "project", level: 2 })}
      <div class="local-explorer-projects" role="group" aria-label="Detected projects" data-local-project-list>
        ${renderLocalProjectRows(data, view)}
      </div>
    </div>
  </div>`;
}

function toolButton(toolId, label, selected) {
  const content = toolId
    ? renderToolIdentity(toolId, { context: "local_tool_filter", label })
    : escapeHtml(label);
  return `<button type="button" class="local-tool-button${selected ? " local-tool-button--active" : ""}" data-local-tool="${escapeHtml(toolId ?? "")}" aria-pressed="${selected}">${content}</button>`;
}

function kindButton(kind, label, count, selected) {
  return `<button type="button" class="local-filter-badge${selected ? " local-filter-badge--active" : ""}" data-local-kind="${escapeHtml(kind ?? "")}" aria-pressed="${selected}"><span>${escapeHtml(label)}</span>${Number.isFinite(count) ? `<strong>${count}</strong>` : ""}</button>`;
}

function projectLabelMap(data) {
  return new Map(projectFilters(data).map(({ projectId, label }) => [projectId, label]));
}

function projectDisplayLabel(projectId, projectLabels) {
  if (!projectId) return "User";
  return projectLabels.get(projectId) ?? "프로젝트 정보 없음";
}

function correlationLabel(correlation) {
  const state = correlation?.state ?? "uncorrelated";
  if (state === "verified") return "SoT 일치";
  if (state === "drift") return "SoT에서 변경됨";
  if (state === "ambiguous") return "연결 확인 필요";
  return "확인된 SoT 연결 없음";
}

function renderResult(item, selected, projectLabels, removalState) {
  const title = item.displayName;
  const project = projectDisplayLabel(item.projectId, projectLabels);
  const removalMode = removalState?.selectionMode === "selecting";
  const removalSelection = new Set(removalState?.selectedInstanceIds ?? []);
  const removalSelected = removalSelection.has(item.instanceId);
  const removalLabel = `${title} 제거 선택`;
  const selectionControl = removalMode
    ? item.removable
      ? `<label class="local-removal-selection"><input type="checkbox" data-local-removal-selection="${escapeHtml(item.instanceId)}" aria-label="${escapeHtml(removalLabel)}"${removalSelected ? " checked" : ""} /><span class="sr-only">${escapeHtml(removalLabel)}</span></label>`
      : '<span class="local-removal-selection-spacer" aria-hidden="true"></span>'
    : "";
  const removalOutcome = localRemovalOutcomeText(removalState, item.instanceId);
  return `<div class="local-result-item${removalMode ? " local-result-item--removal-mode" : ""}" role="listitem">${selectionControl}<button type="button" class="local-result-panel${selected ? " local-result-panel--selected" : ""}" data-local-instance="${escapeHtml(item.instanceId)}" aria-pressed="${selected}" title="${escapeHtml(title)}">
    <span class="local-result-main"><strong>${escapeHtml(title)}</strong>${item.description ? `<span>${escapeHtml(item.description)}</span>` : ""}</span>
    <span class="local-result-badges" aria-label="Instance metadata">
      ${renderToolIdentity(item.toolId, { context: "local_result_badge", label: TOOL_LABELS[item.toolId] ?? item.toolId ?? "Unknown tool" })}
      <span>${escapeHtml(item.kind)}</span>
      <span>${escapeHtml(item.scope)}</span>
      <span>${escapeHtml(item.parseState)}</span>
      ${item.correlation.state === "uncorrelated" ? "" : `<span data-correlation-state="${escapeHtml(item.correlation.state)}">${escapeHtml(correlationLabel(item.correlation))}</span>`}
    </span>
    <span class="local-result-meta"><span>${escapeHtml(project)}</span><code>${escapeHtml(item.safeLocator ?? "locator 없음")}</code>${removalOutcome ? `<span class="local-removal-result" data-local-removal-outcome>${escapeHtml(removalOutcome)}</span>` : ""}</span>
  </button></div>`;
}

function activeFilterSummary(view, projectLabels) {
  const location = view.locationFilter.scope === "project"
    ? `Project · ${projectDisplayLabel(view.locationFilter.projectId, projectLabels)}`
    : view.locationFilter.scope === "user" ? "User" : "전체 위치";
  return [
    location,
    view.toolId ? TOOL_LABELS[view.toolId] ?? view.toolId : "전체 tool",
    view.kind ?? "전체 kind",
    view.query ? `검색: ${view.query}` : "검색어 없음",
  ].join(" · ");
}

function localEmptyState(data, view, projectLabels) {
  if (data?.phase === "running") {
    const progress = data.currentAttempt?.progress;
    const progressCopy = progress
      ? `<p><code>${escapeHtml(progress.adapterId)}</code> · <code>${escapeHtml(progress.surfaceId)}</code> · ${escapeHtml(progress.itemCount)}개 확인</p>`
      : "<p>승인된 discovery adapter 범위를 확인하고 있습니다.</p>";
    return `<div class="local-results-empty" role="status"><span class="atlas-loader" aria-hidden="true"></span><strong>로컬 하네스 스캔 중</strong>${progressCopy}</div>`;
  }
  if (data?.phase === "busy") {
    return '<div class="local-results-empty" role="status"><strong>Filesystem 작업 대기 중</strong><p>현재 작업이 끝나면 다시 스캔할 수 있습니다.</p></div>';
  }
  if (data?.phase === "error") {
    return '<div class="local-results-empty" role="alert"><strong>사용 가능한 Local snapshot이 없습니다</strong><p>스캔 상태를 확인한 뒤 다시 시도하세요.</p></div>';
  }
  if (activeLocalSnapshotHeader(data)) {
    return `<div class="local-results-empty" role="status"><strong>조건에 맞는 하네스가 없습니다</strong><p>${escapeHtml(activeFilterSummary(view, projectLabels))}</p><button type="button" class="button button--quiet" data-reset-local-filters>필터 해제</button></div>`;
  }
  return '<div class="local-results-empty" role="status"><strong>첫 Local snapshot 대기 중</strong><p>대시보드 진입 시 자동 scan을 시작합니다.</p></div>';
}

export function renderLocalWorkbench(data, view, removalState = null) {
  const tools = toolFilters(data);
  const kinds = kindFilters(data);
  const items = localItems(data);
  const projectLabels = projectLabelMap(data);
  const scopeLabel = view.locationFilter.scope === "project"
    ? `Project · ${projectDisplayLabel(view.locationFilter.projectId, projectLabels)}`
    : view.locationFilter.scope === "user" ? "User" : "전체 위치";
  const selectedProjectId = view.locationFilter.scope === "project"
    ? view.locationFilter.projectId
    : null;
  const ignoreSummary = selectedProjectId
    ? renderProjectIgnoreSummary(data, selectedProjectId)
    : "";
  const busy = data?.phase === "running";
  const pageSize = Math.min(
    LOCAL_RESULT_PAGE_SIZE,
    Number.isSafeInteger(view.resultPageSize) && view.resultPageSize > 0
      ? view.resultPageSize
      : LOCAL_RESULT_PAGE_SIZE,
  );
  const lastOffset = items.length
    ? Math.floor((items.length - 1) / pageSize) * pageSize
    : 0;
  const offset = Math.min(
    Number.isSafeInteger(view.resultOffset) && view.resultOffset > 0 ? view.resultOffset : 0,
    lastOffset,
  );
  const visibleItems = items.slice(offset, offset + pageSize);
  const totalCount = localResultCount(data);
  const rangeStart = visibleItems.length ? offset + 1 : 0;
  const rangeEnd = offset + visibleItems.length;
  const removalMode = removalState?.selectionMode === "selecting";
  const removalCount = removalState?.selectedInstanceIds?.length ?? 0;
  const removalStatusText = localRemovalStatusText(removalState);
  const removableItemCount = items.filter((item) => item.removable).length;
  const activeSnapshot = activeLocalSnapshotHeader(data);
  const hasRemovalEntry = !removalMode
    && removableItemCount > 0
    && Boolean(activeSnapshot);
  const canEnterRemoval = hasRemovalEntry
    && ["idle", "complete"].includes(removalState?.phase ?? "idle");
  const canPrepareRemoval = removalMode
    && removalCount > 0
    && Boolean(activeSnapshot)
    && !["preparing", "applying", "awaiting_rescan", "reconciling"].includes(removalState?.phase);

  return `<section id="local-workspace" class="local-workspace" role="tabpanel" aria-labelledby="local-segment local-workspace-title" aria-busy="${busy}">
    <header class="local-filter-surface">
      ${view.correlationProjectionId && view.verifiedComponentId ? `<div class="local-correlation-context" role="status">
        <span><strong>Verified SoT context</strong><code title="${escapeHtml(view.verifiedComponentId)}">${escapeHtml(view.verifiedComponentId)}</code></span>
        <button type="button" class="button button--quiet" data-clear-local-correlation>전체 Local 검색으로 돌아가기</button>
      </div>` : ""}
      <div class="local-workspace-title"><div><p class="eyebrow">Qualified discovery adapters</p><h2 id="local-workspace-title">Local Harness Atlas</h2></div><span>${localResultCount(data)} results</span></div>
      <div class="local-tool-rail" role="toolbar" aria-label="AI tool filter">
        ${toolButton(null, "전체", view.toolId === null)}
        ${tools.map(({ toolId, label }) => toolButton(toolId, label, view.toolId === toolId)).join("")}
      </div>
      <div class="local-search-row">
        <label for="local-search">설치된 하네스 검색</label>
        <input id="local-search" type="search" value="${escapeHtml(view.query)}" placeholder="name, description, safe locator" autocomplete="off" spellcheck="false" />
      </div>
      <div class="local-badge-row" aria-label="Scope and kind filters">
        <span class="local-scope-badge">${escapeHtml(scopeLabel)}</span>
        ${hasRemovalEntry ? `<button type="button" class="button button--quiet local-removal-entry" data-enter-local-removal${canEnterRemoval ? "" : " disabled"}>설치 하네스 제거</button>` : ""}
        ${ignoreSummary}
        ${selectedProjectId ? '<button id="project-ignore-open" type="button" class="button button--quiet local-ignore-editor-trigger" data-open-project-ignore>스캔 제외 규칙 편집</button>' : ""}
        ${kindButton(null, "All kinds", localResultCount(data), view.kind === null)}
        ${kinds.map(({ kind, count }) => kindButton(kind, kind, count, view.kind === kind)).join("")}
      </div>
      ${removalMode ? `<div class="local-removal-toolbar" role="group" aria-label="Local 하네스 제거">
        <span class="local-removal-count" role="status" aria-live="polite">${removalCount}개 선택됨</span><button type="button" class="button button--quiet" data-cancel-local-removal-selection>선택 취소</button><button type="button" class="button button--primary" data-prepare-local-removal${canPrepareRemoval ? "" : " disabled"}>선택한 ${removalCount}개 제거</button>
        ${removalStatusText ? `<p role="status">${escapeHtml(removalStatusText)}</p>` : ""}
      </div>` : removalStatusText ? `<p class="local-removal-status" role="status">${escapeHtml(removalStatusText)}</p>` : ""}
    </header>
    <div id="local-results" class="local-results" role="list" aria-label="Discovered local harnesses">
      ${visibleItems.length
        ? visibleItems.map((item) => renderResult(
          item,
          view.selectedInstanceId === item.instanceId,
          projectLabels,
          removalState,
        )).join("")
        : localEmptyState(data, view, projectLabels)}
    </div>
    ${items.length && (offset > 0 || rangeEnd < totalCount) ? `<nav class="local-result-window" aria-label="Local result window">
      <button type="button" class="button button--quiet" data-local-result-window="previous" aria-controls="local-results"${offset === 0 ? " disabled" : ""}>이전</button>
      <span role="status" aria-live="polite">${rangeStart}–${rangeEnd} / ${totalCount}</span>
      <button type="button" class="button button--quiet" data-local-result-window="next" aria-controls="local-results"${rangeEnd >= totalCount ? " disabled" : ""}>다음</button>
    </nav>` : ""}
  </section>`;
}

function snapshotSummary(data) {
  const result = queryResult(data) ?? {};
  return result.snapshotSummary ?? result.snapshot_summary ?? {};
}

function summaryArray(summary, camel, snake) {
  const value = summary?.[camel] ?? summary?.[snake];
  return Array.isArray(value) ? value : [];
}

function summaryCount(record, camel, snake) {
  const value = record?.[camel] ?? record?.[snake];
  return Number.isSafeInteger(value) ? value : 0;
}

function projectIgnorePresentation(data, projectId) {
  const summary = snapshotSummary(data);
  const issue = summaryArray(summary, "issues", "issues").find((candidate) => {
    const candidateProjectId = candidate?.projectId ?? candidate?.project_id;
    const code = optionalText(candidate?.code) ?? "";
    return candidateProjectId === projectId && code.startsWith("project_ignore_");
  });
  if (issue) {
    return {
      state: "invalid",
      copy: `제외 규칙 확인 필요 · ${optionalText(issue.safeMessage ?? issue.safe_message) ?? "파일 상태를 확인하세요."}`,
    };
  }

  const records = summaryArray(
    summary,
    "projectIgnoreSummaries",
    "project_ignore_summaries",
  );
  const record = records.find((candidate) => (
    candidate?.projectId ?? candidate?.project_id
  ) === projectId);
  if (!record) {
    return {
      state: "unavailable",
      copy: ".harnesskitignore 없음 · 제외 규칙 미적용",
    };
  }

  const rawRevision = optionalText(record.sourceRevision ?? record.source_revision) ?? "unknown";
  const revision = rawRevision.replace(/^sha256:/i, "").slice(0, 8) || "unknown";
  const ruleCount = summaryCount(record, "ruleCount", "rule_count");
  const excludedPathCount = summaryCount(
    record,
    "excludedPathCount",
    "excluded_path_count",
  );
  return {
    state: "valid",
    copy: `.harnesskitignore · revision ${revision} · 규칙 ${ruleCount}개 · 경로 ${excludedPathCount}개 제외`,
  };
}

function renderProjectIgnoreSummary(data, projectId) {
  const presentation = projectIgnorePresentation(data, projectId);
  return `<span class="local-ignore-summary" role="status" data-project-ignore-state="${presentation.state}" title="${escapeHtml(presentation.copy)}">${escapeHtml(presentation.copy)}</span>`;
}

function renderProjectIgnoreIssue(data, projectId) {
  if (!projectId) return "";
  const presentation = projectIgnorePresentation(data, projectId);
  if (presentation.state !== "invalid") return "";
  return `<p class="local-project-ignore-issue state-warning" role="status" data-project-ignore-state="invalid"><strong>Project 제외 규칙</strong> ${escapeHtml(presentation.copy)}</p>`;
}

function renderCoverage(summary) {
  const coverage = summaryArray(summary, "coverage", "coverage");
  if (!coverage.length) return '<p class="local-inspector-empty">Coverage summary 없음</p>';
  return `<ul class="local-evidence-list">${coverage.map((record) => `<li><strong>${escapeHtml(record.adapterId ?? record.adapter_id ?? "adapter")}</strong><span>${escapeHtml(record.status ?? "unknown")} · ${escapeHtml(record.presence ?? "unknown")}</span><span>${escapeHtml(record.itemCount ?? record.item_count ?? 0)} items</span></li>`).join("")}</ul>`;
}

function renderSkipped(summary) {
  const skipped = summaryArray(summary, "skippedPaths", "skipped_paths");
  if (!skipped.length) return '<p class="local-inspector-empty">Skipped path 없음</p>';
  return `<ul class="local-evidence-list">${skipped.map((record) => `<li><code>${escapeHtml(record.safeLocator ?? record.safe_locator ?? record.safeRelativeLocator ?? record.safe_relative_locator ?? "locator 없음")}</code><span>${escapeHtml(record.reasonCode ?? record.reason_code ?? "unknown")}</span></li>`).join("")}</ul>`;
}

function renderIssues(summary) {
  const issues = summaryArray(summary, "issues", "issues");
  if (!issues.length) return '<p class="local-inspector-empty">Safe issue 없음</p>';
  return `<ul class="local-evidence-list local-evidence-list--issues">${issues.map((issue) => `<li><strong>${escapeHtml(issue.code ?? "local_issue")}</strong><span>${escapeHtml(issue.safeMessage ?? issue.safe_message ?? "세부 정보 없음")}</span></li>`).join("")}</ul>`;
}

function renderSnapshotHeader(data) {
  const snapshot = activeLocalSnapshotHeader(data);
  const summary = snapshotSummary(data);
  const currentAttempt = data?.currentAttempt;
  const terminalAttempt = data?.latestTerminalReport;
  return `<div class="local-snapshot-header">
    <p class="detail-kicker">${escapeHtml(snapshot?.status ?? "No snapshot")}</p>
    <h3 class="detail-name">${escapeHtml(snapshot?.snapshotId ?? "Local snapshot 대기")}</h3>
    <dl class="detail-list detail-list--compact">
      <div><dt>Scan time</dt><dd>${escapeHtml(summary.scanTimestamp ?? summary.scan_timestamp ?? "데이터 없음")}</dd></div>
      <div><dt>Current attempt</dt><dd>${escapeHtml(currentAttempt?.attemptId ?? "없음")}</dd></div>
      <div><dt>Latest attempt</dt><dd>${escapeHtml(terminalAttempt?.attemptId ?? snapshot?.attemptId ?? "없음")}</dd></div>
    </dl>
  </div>`;
}

function renderSummaryInspector(data) {
  const summary = snapshotSummary(data);
  return `${renderSnapshotHeader(data)}
    <section class="local-inspector-block" aria-labelledby="local-coverage-title"><h4 id="local-coverage-title">Coverage</h4>${renderCoverage(summary)}</section>
    <section class="local-inspector-block" aria-labelledby="local-skipped-title"><h4 id="local-skipped-title">Skipped</h4>${renderSkipped(summary)}</section>
    <section class="local-inspector-block" aria-labelledby="local-issues-title"><h4 id="local-issues-title">Issues</h4>${renderIssues(summary)}</section>`;
}

function selectedItem(data, instanceId) {
  const detail = normalizeItem(data?.selectedDetail);
  if (detail?.instanceId === instanceId) return detail;
  return localItems(data).find((item) => item.instanceId === instanceId) ?? null;
}

function renderSettings(settings) {
  if (!settings.length) return "데이터 없음";
  return `<ul>${settings.map((setting) => {
    const key = setting?.key ?? "setting";
    const state = setting?.redacted === true
      ? "present · redacted"
      : setting?.present === false ? "not present" : "present";
    return `<li><code>${escapeHtml(key)}</code> ${escapeHtml(state)}</li>`;
  }).join("")}</ul>`;
}

function renderLocalActionStatus(view, item, snapshot) {
  const status = view?.actionStatus;
  const active = status?.instanceId === item.instanceId
    && typeof status?.message === "string"
    && typeof status?.code === "string";
  const state = active ? status.state : "idle";
  const code = active ? status.code : "none";
  const message = active ? status.message : "";
  const accessibleLabel = active ? `${message} · code ${code}` : "Local action 상태 없음";
  return `<p class="local-action-status local-action-status--${escapeHtml(state)}" data-local-action-status data-local-action-state="${escapeHtml(state)}" data-local-action-code="${escapeHtml(code)}" aria-label="${escapeHtml(accessibleLabel)}"${active ? "" : " hidden"}>${escapeHtml(message)}</p>`;
}

function activeSourcePreview(view, item) {
  const preview = view?.sourcePreview;
  if (preview?.selection?.instanceId !== item.instanceId) return null;
  return preview;
}

function renderSourcePreviewSurface(preview) {
  const header = preview?.header;
  const totalChunks = Number(header?.totalChunks ?? 0);
  const currentIndex = Number(preview?.currentChunkIndex ?? 0);
  const ready = header
    && Number.isSafeInteger(totalChunks)
    && totalChunks > 0
    && Number.isSafeInteger(currentIndex)
    && currentIndex >= 0
    && currentIndex < totalChunks;
  const issue = header?.issue;
  return `<section class="local-source-preview" aria-labelledby="local-source-preview-title" aria-busy="${preview?.phase === "opening"}">
    <header class="local-source-preview-heading">
      <div><p class="detail-kicker">Read-only source</p><h4 id="local-source-preview-title">전체 원문</h4></div>
      ${header?.changedSinceSnapshot ? '<span class="state-warning">스캔 후 변경됨</span>' : ""}
    </header>
    ${issue ? `<p class="local-source-issue" role="status"><strong>${escapeHtml(issue.code ?? "source_issue")}</strong> ${escapeHtml(issue.safe_message ?? issue.safeMessage ?? "원문 위치를 다시 확인하세요.")}</p>` : ""}
    ${ready ? `<nav class="local-source-navigation" aria-label="원문 구간 이동">
      <button type="button" class="button button--quiet" data-source-chunk="${Math.max(0, currentIndex - 1)}"${currentIndex <= 0 ? " disabled" : ""}>이전</button>
      <input type="range" min="0" max="${totalChunks - 1}" step="1" value="${currentIndex}" data-source-chunk-range aria-label="원문 위치" aria-valuemin="0" aria-valuemax="${totalChunks - 1}" aria-valuenow="${currentIndex}" aria-valuetext="${currentIndex + 1} / ${totalChunks}">
      <span role="status" aria-live="polite">${currentIndex + 1} / ${totalChunks}</span>
      <button type="button" class="button button--quiet" data-source-chunk="${Math.min(totalChunks - 1, currentIndex + 1)}"${currentIndex + 1 >= totalChunks ? " disabled" : ""}>다음</button>
    </nav>` : ""}
    <div id="typography-source-preview-body" class="local-source-content" data-local-source-content tabindex="0" role="region" aria-label="선택한 하네스 전체 원문"></div>
  </section>`;
}

function renderAiExplanationSurface(view, sourceHeader) {
  const provider = view?.aiProvider;
  const explanation = view?.aiExplanation;
  const configured = provider?.state === "configured"
    && String(provider?.model ?? "").trim()
    && String(provider?.providerRevision ?? "").trim();
  const transportWarning = configured && provider.transportWarning === AI_TRANSPORT_WARNING.code;
  const readySource = sourceHeader?.sourceRevision;
  const pending = explanation?.phase === "pending";
  let feedback = "";
  if (pending) {
    feedback = '<p class="local-ai-status" role="status">AI 설명을 생성하고 있습니다.</p>';
  } else if (explanation?.phase === "error") {
    const message = explanation.errorCode === "source_too_large_for_ai"
      ? "이 모델이 전체 파일을 처리할 수 없습니다"
      : explanation.message || "AI 설명을 생성하지 못했습니다.";
    feedback = `<p class="local-ai-status local-ai-status--error" role="alert">${escapeHtml(message)}</p>`;
  }
  let card = "";
  if (explanation?.phase === "ready" && explanation.result) {
    const result = explanation.result;
    card = `<article class="local-ai-card" aria-labelledby="local-ai-card-title">
      <header><p class="detail-kicker">AI 생성 설명 · runtime 검증 아님</p><h4 id="local-ai-card-title">파일 설명</h4></header>
      <dl><div><dt>하는 일</dt><dd>${escapeHtml(result.doing)}</dd></div><div><dt>언제 사용되는지</dt><dd>${escapeHtml(result.whenUsed ?? result.when_used)}</dd></div><div><dt>접근 가능한 기능</dt><dd>${escapeHtml(result.capabilities)}</dd></div><div><dt>주의할 점</dt><dd>${escapeHtml(result.cautions)}</dd></div></dl>
    </article>`;
  }
  return `<section class="local-ai-explanation" aria-label="AI 설명">
    <div class="local-ai-actions"><button type="button" class="button button--quiet" data-open-ai-settings>AI 연결 설정</button>${configured ? `<button type="button" class="button button--secondary" data-ai-explain${transportWarning ? ' aria-describedby="ai-explain-transport-warning"' : ""}${readySource && !pending ? "" : ' aria-disabled="true"'}>AI 설명 생성 · ${escapeHtml(provider.model)}</button>` : ""}</div>
    ${transportWarning ? `<p id="ai-explain-transport-warning" class="ai-transport-warning" data-transport-warning="${AI_TRANSPORT_WARNING.code}">${AI_TRANSPORT_WARNING.message}</p>` : ""}
    <p>원문은 AI 설명 생성을 눌렀을 때만 전송됩니다.</p>
    ${feedback}
  </section>${card}`;
}

function renderSelectedInspector(data, view, item) {
  const snapshot = activeLocalSnapshotHeader(data);
  const sourcePreview = activeSourcePreview(view, item);
  const sourceHeader = sourcePreview?.header;
  const project = projectDisplayLabel(item.projectId, projectLabelMap(data));
  const visiblePath = sourceHeader?.canonicalPath ?? item.safeLocator ?? "원문 경로 확인 중";
  const correlation = item.correlation;
  const correlationValue = correlation.componentId
    ? `${correlationLabel(correlation)} · ${correlation.componentId}`
    : correlationLabel(correlation);
  return `<div class="local-instance-detail local-instance-detail--source-first">
      <p class="detail-kicker">${renderToolIdentity(item.toolId, { context: "local_selected_inspector", label: TOOL_LABELS[item.toolId] ?? item.toolId ?? "Local harness" })}<span aria-hidden="true"> · </span>${escapeHtml(item.kind)}</p>
      <h3 class="detail-name">${escapeHtml(item.displayName)}</h3>
      <p class="local-instance-project">${escapeHtml(project)}</p>
      <p class="path-value local-instance-path">${escapeHtml(visiblePath)}</p>
      ${renderProjectIgnoreIssue(data, item.projectId)}
      <div class="local-instance-state-row"><span>${escapeHtml(snapshot?.status ?? "No snapshot")}</span><span>${escapeHtml(item.parseState)}</span><span>${escapeHtml(correlationLabel(correlation))}</span></div>
      <div class="local-read-actions" role="group" aria-label="Read-only instance actions" aria-busy="${view?.actionBusy === true}">
        ${[
          ["reveal", "Finder에서 보기"],
          ["copy_path", "경로 복사"],
        ].map(([action, label]) => `<button type="button" class="button button--quiet" data-local-action="${action}" data-snapshot-id="${escapeHtml(snapshot?.snapshotId ?? "")}" data-instance-id="${escapeHtml(item.instanceId)}"${view?.actionBusy === true ? ' aria-disabled="true"' : ""}${snapshot ? "" : " disabled"}>${label}</button>`).join("")}
        ${correlation.state === "verified" && correlation.componentId ? `<button type="button" class="button button--secondary" data-open-sot-component="${escapeHtml(correlation.componentId)}">SoT 명세 보기</button>` : ""}
      </div>
      ${renderLocalActionStatus(view, item, snapshot)}
      ${renderAiExplanationSurface(view, sourceHeader)}
      ${renderSourcePreviewSurface(sourcePreview)}
      <section class="local-supporting-state" aria-label="원문 보조 상태">
        <p><strong>Parse</strong> ${escapeHtml(item.parseState)}${item.issueCodes.length ? ` · ${escapeHtml(item.issueCodes.join(", "))}` : ""}</p>
        <p id="local-correlation-detail"><strong>Correlation</strong> ${escapeHtml(correlationValue)}${correlation.safeReason ? ` · ${escapeHtml(correlation.safeReason)}` : ""}</p>
      </section>
      <details class="local-technical-info"><summary>스캔 정보</summary><dl class="detail-list">
        <div><dt>Instance ID</dt><dd class="path-value">${escapeHtml(item.instanceId)}</dd></div>
        <div><dt>Project ID</dt><dd class="path-value">${escapeHtml(item.projectId ?? "User")}</dd></div>
        <div><dt>Adapter</dt><dd>${escapeHtml(item.adapterId ?? "데이터 없음")} · ${escapeHtml(item.adapterVersion ?? "unknown")}</dd></div>
        <div><dt>Surface / scope</dt><dd>${escapeHtml(item.surfaceId ?? "unknown")} · ${escapeHtml(item.scope)}</dd></div>
        <div><dt>Snapshot</dt><dd class="path-value">${escapeHtml(snapshot?.snapshotId ?? "없음")} · ${escapeHtml(item.modifiedUnixMillis ?? "unknown")}</dd></div>
        <div><dt>Size</dt><dd>${escapeHtml(item.size ?? "unknown")} bytes</dd></div>
        <div><dt>Settings</dt><dd>${renderSettings(item.settings)}</dd></div>
        ${correlation.method ? `<div><dt>Evidence method</dt><dd>${escapeHtml(correlation.method)}</dd></div>` : ""}
        ${correlation.evidenceRef ? `<div><dt>Evidence ref</dt><dd class="path-value">${escapeHtml(correlation.evidenceRef)}</dd></div>` : ""}
      </dl></details>
    </div>`;
}

export function renderLocalInspector(data, view) {
  const instanceId = view?.selectedInstanceId;
  if (!instanceId) return renderSummaryInspector(data);
  const item = selectedItem(data, instanceId);
  if (!item) {
    return `${renderSnapshotHeader(data)}<p class="empty-state empty-state--compact">선택한 instance가 현재 snapshot에 없습니다.</p>`;
  }
  return renderSelectedInspector(data, view, item);
}

export function localResultCount(data) {
  const counts = queryResult(data)?.counts ?? {};
  const matched = Number(counts.matchedInstances ?? counts.matched_instances);
  return Number.isSafeInteger(matched) && matched >= 0 ? matched : localItems(data).length;
}

export function verifiedSotComponentId(data, view) {
  const item = selectedItem(data, view?.selectedInstanceId);
  if (!item || item.correlation.state !== "verified") return null;
  return item.correlation.componentId;
}
