import { renderToolIdentity } from "./tool-identities.js";
import { renderOrderedStepInspector } from "./graph/ordered-step-inspector.js";

const UNPROFILED_ID = "__unprofiled__";
const DEFAULT_TREE_EXPANDED_IDS = Object.freeze(["root", "components", "profiles"]);
const TREE_GROUP_IDS = new Set(DEFAULT_TREE_EXPANDED_IDS);
const INITIAL_GRAPH_PRESENTATION = Object.freeze({
  viewMode: "three_d",
  rendererLifecycle: "creating",
  failure: null,
  focusNodeId: null,
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function componentById(snapshot, componentId) {
  return snapshot?.components?.find((component) => component.component_id === componentId) ?? null;
}

function workflowFocusNodeId(snapshot, workflowId) {
  return snapshot?.graph_projection?.nodes?.find((node) => (
    node?.node_type === "relation"
    && node.relation_kind === "workflow"
    && node.canonical_id === workflowId
  ))?.node_id ?? null;
}

function profileMembers(snapshot, profileId) {
  if (profileId === UNPROFILED_ID) return snapshot?.unprofiled_component_ids ?? [];
  return snapshot?.profiles?.find((profile) => profile.profile_id === profileId)?.component_ids ?? [];
}

function allProfiles(snapshot) {
  const profiles = Array.isArray(snapshot?.profiles) ? snapshot.profiles : [];
  return [
    ...profiles,
    {
      profile_id: UNPROFILED_ID,
      title: "Unprofiled",
      summary: "현재 source profile membership이 없는 component의 neutral projection입니다.",
      component_ids: snapshot?.unprofiled_component_ids ?? [],
      neutral: true,
    },
  ];
}

export function createSotViewState(snapshot, overrides = {}) {
  const {
    graphPresentation: previousGraphPresentation,
    ...stateOverrides
  } = overrides;
  return {
    activeProfileId: snapshot?.profiles?.[0]?.profile_id ?? UNPROFILED_ID,
    selectedComponentId: null,
    selectedRelationNodeId: null,
    selectedWorkflowId: null,
    hoveredGraphNodeId: null,
    hoveredWorkflowStep: null,
    lockedWorkflowStep: null,
    filter: "",
    treeSelectedId: null,
    treeExpandedIds: [...DEFAULT_TREE_EXPANDED_IDS],
    unfilteredTreeScrollTop: 0,
    filteredTreeScrollTop: 0,
    ...stateOverrides,
    graphPresentation: {
      ...INITIAL_GRAPH_PRESENTATION,
      ...previousGraphPresentation,
      focusNodeId: null,
    },
  };
}

function validTreeNodeIds(snapshot) {
  return new Set([
    "root",
    "components",
    "profiles",
    "registry.yml",
    "capabilities.yml",
    ...(snapshot?.components ?? []).map((component) => component.component_id),
    ...(snapshot?.profiles ?? []).map((profile) => `profile:${profile.profile_id}`),
  ]);
}

export function reduceSotView(state, action, snapshot) {
  if (!action || typeof action.type !== "string") return state;
  if (action.type === "show_graph_semantic") {
    return {
      ...state,
      graphPresentation: {
        ...state.graphPresentation,
        viewMode: "semantic",
        rendererLifecycle: "absent",
        failure: null,
      },
    };
  }
  if (action.type === "return_graph_three_d") {
    return {
      ...state,
      graphPresentation: {
        ...state.graphPresentation,
        viewMode: "three_d",
        rendererLifecycle: "creating",
        failure: null,
      },
    };
  }
  if (action.type === "graph_renderer_failed") {
    return {
      ...state,
      graphPresentation: {
        ...state.graphPresentation,
        viewMode: "semantic",
        rendererLifecycle: "failed",
        failure: String(action.failure ?? "3D 그래프를 표시할 수 없습니다."),
      },
    };
  }
  if (action.type === "graph_renderer_ready") {
    return {
      ...state,
      graphPresentation: {
        ...state.graphPresentation,
        viewMode: "three_d",
        rendererLifecycle: "ready",
        failure: null,
      },
    };
  }
  if (action.type === "select_profile") {
    const valid = allProfiles(snapshot).some((profile) => profile.profile_id === action.profileId);
    const selectedRelationNodeId = action.profileId === UNPROFILED_ID
      ? "unprofiled:projection:unprofiled"
      : `profile:${action.profileId}`;
    return valid ? {
      ...state,
      activeProfileId: action.profileId,
      selectedRelationNodeId,
      selectedWorkflowId: null,
      lockedWorkflowStep: null,
      graphPresentation: {
        ...state.graphPresentation,
        focusNodeId: selectedRelationNodeId,
      },
    } : state;
  }
  if (action.type === "select_component" || action.type === "select_graph_node") {
    const valid = componentById(snapshot, action.componentId);
    return valid ? {
      ...state,
      selectedComponentId: action.componentId,
      selectedRelationNodeId: null,
      selectedWorkflowId: null,
      lockedWorkflowStep: null,
      graphPresentation: {
        ...state.graphPresentation,
        focusNodeId: `component:${action.componentId}`,
      },
    } : state;
  }
  if (action.type === "select_graph_relation") {
    const node = snapshot?.graph_projection?.nodes?.find((candidate) => (
      candidate?.node_type === "relation"
      && candidate.node_id === action.nodeId
      && candidate.relation_kind === action.relationKind
      && candidate.canonical_id === action.canonicalId
    ));
    if (!node) return state;
    if (node.relation_kind === "workflow") {
      const workflow = snapshot?.workflows?.find(
        (candidate) => candidate.workflow_id === node.canonical_id,
      );
      return workflow ? {
        ...state,
        selectedComponentId: null,
        selectedRelationNodeId: node.node_id,
        selectedWorkflowId: workflow.workflow_id,
        lockedWorkflowStep: null,
        graphPresentation: {
          ...state.graphPresentation,
          focusNodeId: node.node_id,
        },
      } : state;
    }
    const profileId = node.relation_kind === "unprofiled"
      ? UNPROFILED_ID
      : node.canonical_id;
    return allProfiles(snapshot).some((profile) => profile.profile_id === profileId)
      ? {
        ...state,
        activeProfileId: profileId,
        selectedRelationNodeId: node.node_id,
        selectedWorkflowId: null,
        lockedWorkflowStep: null,
        graphPresentation: {
          ...state.graphPresentation,
          focusNodeId: node.node_id,
        },
      }
      : state;
  }
  if (action.type === "select_workflow_step") {
    const ordinal = Number(action.ordinal);
    const workflow = snapshot?.workflows?.find(
      (candidate) => candidate.workflow_id === action.workflowId,
    );
    if (!workflow?.steps?.some((step) => step.ordinal === ordinal)) return state;
    const parentFocusNodeId = workflowFocusNodeId(snapshot, workflow.workflow_id);
    const focusNodeId = state.graphPresentation?.focusNodeId === parentFocusNodeId
      ? parentFocusNodeId
      : null;
    return {
      ...state,
      selectedComponentId: null,
      selectedWorkflowId: workflow.workflow_id,
      selectedRelationNodeId: `workflow:${workflow.workflow_id}`,
      lockedWorkflowStep: { workflowId: workflow.workflow_id, ordinal },
      graphPresentation: {
        ...state.graphPresentation,
        focusNodeId,
      },
    };
  }
  if (action.type === "workflow_overview") {
    const workflow = snapshot?.workflows?.find(
      (candidate) => candidate.workflow_id === action.workflowId,
    );
    return workflow ? {
      ...state,
      selectedComponentId: null,
      selectedRelationNodeId: `workflow:${workflow.workflow_id}`,
      selectedWorkflowId: workflow.workflow_id,
      hoveredWorkflowStep: null,
      lockedWorkflowStep: null,
      graphPresentation: {
        ...state.graphPresentation,
        focusNodeId: `workflow:${workflow.workflow_id}`,
      },
    } : state;
  }
  if (action.type === "clear_graph_selection") {
    return {
      ...state,
      selectedComponentId: null,
      selectedRelationNodeId: null,
      selectedWorkflowId: null,
      hoveredGraphNodeId: null,
      hoveredWorkflowStep: null,
      lockedWorkflowStep: null,
      graphPresentation: {
        ...state.graphPresentation,
        focusNodeId: null,
      },
    };
  }
  if (action.type === "hover_graph_node") {
    return { ...state, hoveredGraphNodeId: action.nodeId ?? null };
  }
  if (action.type === "hover_workflow_step") {
    return { ...state, hoveredWorkflowStep: action.step ?? null };
  }
  if (action.type === "select_tree_component") {
    const valid = componentById(snapshot, action.componentId);
    return valid ? {
      ...state,
      treeSelectedId: action.componentId,
      selectedComponentId: action.componentId,
      selectedRelationNodeId: null,
      selectedWorkflowId: null,
      lockedWorkflowStep: null,
      graphPresentation: {
        ...state.graphPresentation,
        focusNodeId: `component:${action.componentId}`,
      },
    } : state;
  }
  if (action.type === "select_tree_node") {
    const treeNodeId = String(action.treeNodeId ?? "");
    return validTreeNodeIds(snapshot).has(treeNodeId)
      ? { ...state, treeSelectedId: treeNodeId }
      : state;
  }
  if (action.type === "toggle_tree_node") {
    const treeNodeId = String(action.treeNodeId ?? "");
    if (!TREE_GROUP_IDS.has(treeNodeId)) return state;
    const expanded = new Set(Array.isArray(state.treeExpandedIds)
      ? state.treeExpandedIds.filter((id) => TREE_GROUP_IDS.has(id))
      : DEFAULT_TREE_EXPANDED_IDS);
    if (expanded.has(treeNodeId)) expanded.delete(treeNodeId);
    else expanded.add(treeNodeId);
    return {
      ...state,
      treeExpandedIds: DEFAULT_TREE_EXPANDED_IDS.filter((id) => expanded.has(id)),
    };
  }
  if (action.type === "set_tree_scroll") {
    const scrollTop = Number(action.scrollTop);
    if (!Number.isFinite(scrollTop) || scrollTop < 0) return state;
    return String(state.filter ?? "").trim()
      ? { ...state, filteredTreeScrollTop: scrollTop }
      : { ...state, unfilteredTreeScrollTop: scrollTop };
  }
  if (action.type === "set_filter") {
    return { ...state, filter: String(action.filter ?? "") };
  }
  return state;
}

export function renderComponentMap(snapshot, view, graphExpanded = true) {
  const projection = snapshot?.graph_projection;
  if (!projection) return '<p class="empty-state">Component Map projection이 없습니다.</p>';
  const nodes = Array.isArray(projection.nodes) ? projection.nodes : [];
  const links = Array.isArray(projection.links) ? projection.links : [];
  const expanded = graphExpanded !== false;
  const snapshotIdentityDescription = `snapshot ${snapshot.snapshot_id} · source ${snapshot.checkout_summary?.source_revision ?? "unknown"}`;

  return `<section class="component-map component-map--three${expanded ? "" : " component-map--collapsed"}" data-graph-expanded="${expanded}" aria-labelledby="component-map-title">
    <div class="component-map-hud" data-component-map-hud>
      <div class="component-map-heading">
        <div><p class="eyebrow">Registry identity atlas</p><h2 id="component-map-title">Component Map</h2></div>
        <div class="component-map-tools">
          <span class="workspace-meta">${nodes.length} nodes · ${links.length} links</span>
          <button type="button" class="button button--quiet graph-collapse-toggle" data-sot-graph-toggle aria-expanded="${expanded}" aria-controls="component-map-body">${expanded ? "⌃ 그래프 접기" : "⌄ 그래프 펼치기"}</button>
        </div>
      </div>
      <div class="component-map-toolbar" id="component-map-toolbar" role="group" aria-label="Component Map 범례와 카메라 제어">
        <span class="component-map-key" id="component-map-legend" role="group" aria-label="Component Map 시각 범례">
          <span class="graph-key-item"><i class="graph-key-swatch graph-key-swatch--profile" aria-hidden="true"></i><span class="graph-key-label">Profile relation</span></span>
          <span class="graph-key-item"><i class="graph-key-swatch graph-key-swatch--workflow" aria-hidden="true"></i><span class="graph-key-label">Workflow relation</span></span>
          <span class="graph-key-item"><i class="graph-key-line graph-key-line--membership" aria-hidden="true"></i><span class="graph-key-label">Profile membership</span></span>
          <span class="graph-key-item"><i class="graph-key-line graph-key-line--workflow" aria-hidden="true"></i><span class="graph-key-label">Ordered workflow step</span></span>
          <span class="graph-key-item"><i class="graph-key-kind" aria-hidden="true">S</i><span class="graph-key-label">Component kind</span></span>
        </span>
        <div class="graph-zoom-controls" id="component-map-camera-controls" role="group" aria-label="Component Map 카메라">
          <button type="button" data-graph-semantic-view aria-label="Component Map 텍스트 보기" hidden disabled>텍스트 보기</button>
          <button type="button" data-graph-zoom="out" aria-label="축소">−</button>
          <button id="typography-toolbar-control" type="button" data-graph-zoom="reset" aria-label="전체 보기">Fit</button>
          <button type="button" data-graph-zoom="in" aria-label="확대">＋</button>
          <output id="component-map-camera-scale" data-graph-scale aria-live="polite">3D · 준비</output>
        </div>
      </div>
    </div>
    <div id="component-map-body" class="component-map-body" data-component-map-body role="group" aria-label="Component Map 본문"${expanded ? "" : " hidden"}>
      <div id="component-map-viewport" class="component-map-viewport component-map-viewport--three" data-component-map-viewport role="group" aria-label="Component Map 3D viewport" aria-description="${escapeHtml(snapshotIdentityDescription)}">
        <div class="component-map-scene-host" data-component-map-scene-host aria-hidden="true"></div>
        <div class="component-map-relation-label-root" data-graph-relation-label-root aria-hidden="true"></div>
        <div id="component-map-identity-overlay" class="component-map-identity-overlay" data-graph-identity-overlay role="status" aria-live="polite" aria-atomic="true" hidden>
          <strong id="component-map-identity-title" data-graph-identity-title></strong>
          <span id="component-map-identity-kind" class="component-kind-badge" data-graph-identity-kind></span>
          <span id="component-map-identity-count" data-graph-identity-count></span>
        </div>
        <div id="component-map-renderer-status" class="component-map-renderer-state" data-graph-renderer-state role="status" hidden></div>
      </div>
      <div class="component-map-semantic-host" data-component-map-semantic-host data-semantic-graph-fallback data-renderer-availability="pending"></div>
    </div>
  </section>`;
}

export function renderProfileMembers(snapshot, view) {
  const profile = allProfiles(snapshot).find((candidate) => candidate.profile_id === view.activeProfileId);
  if (!profile) return '<p class="empty-state">선택된 profile이 없습니다.</p>';
  const members = profile.component_ids
    .map((componentId) => componentById(snapshot, componentId))
    .filter(Boolean);
  return `<div class="profile-member-heading">
      <div><p class="eyebrow">Expanded profile</p><h3>${escapeHtml(profile.title)}</h3></div>
      <span class="count">${members.length}</span>
    </div>
    ${members.length ? `<div id="profile-member-grid" class="profile-member-grid" role="group" aria-label="Profile member cards">${members.map((component) => `<button
      type="button"
      class="component-panel${view.selectedComponentId === component.component_id ? " component-panel--selected" : ""}"
      data-component-id="${escapeHtml(component.component_id)}"
      aria-pressed="${view.selectedComponentId === component.component_id}"
      title="${escapeHtml(component.component_id)}"
    ><strong>${escapeHtml(component.title)}</strong><span aria-hidden="true">${escapeHtml(component.kind)} · ${escapeHtml(component.status)}</span><span class="sr-only">${escapeHtml(`${component.component_id} · component · ${component.kind} · ${component.status}`)}</span></button>`).join("")}</div>`
      : '<p class="empty-state empty-state--compact">이 profile에는 component가 없습니다.</p>'}`;
}

export function renderProfileMatrixHeader(expanded = true) {
  const isExpanded = expanded === true;
  return `<section id="profile-matrix" class="profile-matrix-header" aria-labelledby="profile-matrix-title">
    <div class="profile-matrix-heading">
      <div><p class="eyebrow">Profile membership projection</p><h2 id="profile-matrix-title">Profile Matrix</h2></div>
      <button type="button" class="button button--quiet profile-matrix-toggle" data-sot-matrix-toggle aria-expanded="${isExpanded}" aria-controls="profile-matrix-body">${isExpanded ? "⌃ 매트릭스 접기" : "⌄ 매트릭스 펼치기"}</button>
    </div>
  </section>`;
}

export function renderProfileMatrixBody(snapshot, view, expanded = true) {
  const profiles = allProfiles(snapshot);
  const owningProfileIds = new Set(
    componentById(snapshot, view.selectedComponentId)?.profile_ids ?? [],
  );
  if (view.selectedComponentId && (snapshot.unprofiled_component_ids ?? []).includes(view.selectedComponentId)) {
    owningProfileIds.add(UNPROFILED_ID);
  }
  return `<section id="profile-matrix-body" class="profile-matrix-body" aria-labelledby="profile-matrix-title"${expanded === true ? "" : " hidden"}>
    <div id="profile-grid" class="profile-grid" role="group" aria-label="Profile cards">${profiles.map((profile) => {
      const ownsSelected = owningProfileIds.has(profile.profile_id);
      const baseLabel = `${profile.title} · ${profile.profile_id} · ${profile.component_ids.length} components`;
      const profileIdentity = `${profile.profile_id} · profile · ${profile.status || "unknown"} · ${profile.component_ids.length} components · ${ownsSelected ? "선택 component 포함" : "선택 component 미포함"}`;
      return `<button
      type="button"
      class="profile-panel${view.activeProfileId === profile.profile_id ? " profile-panel--active" : ""}${ownsSelected ? " profile-panel--owns-selected" : ""}${profile.neutral ? " profile-panel--neutral" : ""}"
      data-profile-id="${escapeHtml(profile.profile_id)}"
      data-profile-label="${escapeHtml(baseLabel)}"
      data-owning-selected="${ownsSelected}"
      aria-pressed="${view.activeProfileId === profile.profile_id}"
      title="${escapeHtml(profile.profile_id)}"
    ><strong>${escapeHtml(profile.title)}</strong><span aria-hidden="true">${profile.component_ids.length} components</span><span class="sr-only">${escapeHtml(profileIdentity)}</span>${ownsSelected ? '<small class="profile-owner-status" aria-hidden="true">선택 component 포함</small>' : '<small class="profile-owner-status" aria-hidden="true" hidden></small>'}</button>`;
    }).join("")}</div>
    <div id="profile-member-region" class="profile-member-region" role="region" aria-label="Selected profile members">${renderProfileMembers(snapshot, view)}</div>
  </section>`;
}

export function renderProfileMatrix(snapshot, view, expanded = true) {
  return `${renderProfileMatrixHeader(expanded)}${renderProfileMatrixBody(snapshot, view, expanded)}`;
}

function renderList(values, projector = (value) => value) {
  if (!Array.isArray(values) || values.length === 0) return "데이터 없음";
  return `<ul>${values.map((value) => `<li>${escapeHtml(projector(value))}</li>`).join("")}</ul>`;
}

function renderTargets(values) {
  if (!Array.isArray(values) || values.length === 0) return "데이터 없음";
  return `<ul>${values.map((target) => `<li>${renderToolIdentity(target.target_id, {
    context: "sot_target",
  })}${target.support_status ? ` <span>· ${escapeHtml(target.support_status)}</span>` : ""}</li>`).join("")}</ul>`;
}

const INSPECTOR_KIND_LABELS = Object.freeze({
  agent: "AGENT",
  skill: "SKILL",
  workflow: "WORKFLOW",
  hook: "HOOK",
  rule: "RULE",
  command: "COMMAND",
  prompt: "PROMPT",
  mcp: "MCP",
  profile: "PROFILE",
});

function inspectorKindLabel(kind) {
  const normalized = String(kind ?? "").trim().toLowerCase();
  return INSPECTOR_KIND_LABELS[normalized] ?? (normalized ? normalized.toUpperCase() : "COMPONENT");
}

function renderInspectorKindBadge(kind) {
  const normalized = String(kind ?? "unknown").trim().toLowerCase();
  return `<span class="component-kind-badge" data-inspector-kind="${escapeHtml(normalized)}">${escapeHtml(inspectorKindLabel(normalized))}</span>`;
}

function uniqueByIdentity(values, identity) {
  const seen = new Set();
  return values.filter((value) => {
    const key = identity(value);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function renderRelatedGroup(kind, title, items) {
  const visibleItems = uniqueByIdentity(items, (item) => item.id);
  return `<section class="sot-related-group" data-related-kind="${escapeHtml(kind)}">
    <h4>${escapeHtml(title)}</h4>
    ${visibleItems.length === 0
      ? '<p class="sot-related-empty">없음</p>'
      : `<ul>${visibleItems.map((item) => `<li><span>${escapeHtml(item.title)}</span>${renderInspectorKindBadge(item.kind)}</li>`).join("")}</ul>`}
  </section>`;
}

function componentRelatedWorkflows(snapshot, componentId) {
  return (snapshot?.workflows ?? [])
    .filter((workflow) => (workflow.steps ?? []).some(
      (step) => (step.resolved_component_ids ?? []).includes(componentId),
    ))
    .map((workflow) => ({
      id: workflow.workflow_id,
      title: workflow.title || workflow.workflow_id,
      kind: "workflow",
    }));
}

function componentRelatedProfiles(snapshot, component) {
  const profileIds = new Set(component.profile_ids ?? []);
  return (snapshot?.profiles ?? [])
    .filter((profile) => profileIds.has(profile.profile_id)
      || (profile.component_ids ?? []).includes(component.component_id))
    .map((profile) => ({
      id: profile.profile_id,
      title: profile.title || profile.profile_id,
      kind: "profile",
    }));
}

function componentRelatedComponents(snapshot, componentId) {
  const relatedIds = (snapshot?.relations ?? []).flatMap((relation) => {
    if (relation.source === componentId) return [relation.target];
    if (relation.target === componentId) return [relation.source];
    return [];
  });
  return uniqueByIdentity(
    relatedIds.map((relatedId) => componentById(snapshot, relatedId)).filter(Boolean),
    (component) => component.component_id,
  ).map((component) => ({
    id: component.component_id,
    title: component.title || component.component_id,
    kind: component.kind,
  }));
}

function workflowRelatedComponents(snapshot, workflow) {
  const componentIds = (workflow.steps ?? []).flatMap(
    (step) => step.resolved_component_ids ?? [],
  );
  return uniqueByIdentity(
    componentIds.map((componentId) => componentById(snapshot, componentId)).filter(Boolean),
    (component) => component.component_id,
  ).map((component) => ({
    id: component.component_id,
    title: component.title || component.component_id,
    kind: component.kind,
  }));
}

function renderComponentTechnicalDetails(component, relations) {
  return `<details class="sot-technical-info">
    <summary>기술 정보</summary>
    <dl class="detail-list">
      <div><dt>Canonical ID</dt><dd id="selected-component-id" class="path-value">${escapeHtml(component.component_id)}</dd></div>
      <div><dt>Kind / domain</dt><dd>${escapeHtml(component.kind)} / ${escapeHtml(component.domain || "unknown")}</dd></div>
      <div><dt>Status</dt><dd>${escapeHtml(component.status || "데이터 없음")}</dd></div>
      <div><dt>Targets</dt><dd>${renderTargets(component.targets)}</dd></div>
      <div><dt>Profile IDs</dt><dd>${renderList(component.profile_ids)}</dd></div>
      <div><dt>Owned files</dt><dd>${renderList(component.owned_files)}</dd></div>
      <div><dt>Provenance</dt><dd>${escapeHtml(component.provenance?.mode || component.provenance?.source_classification || "데이터 없음")}</dd></div>
      <div><dt>Relation evidence</dt><dd>${relations.length ? `<ul>${relations.map((relation) => `<li><strong>${escapeHtml(relation.relation_type)}</strong> · ${escapeHtml(relation.source === component.component_id ? "outbound" : "inbound")} · declarative<br /><code>${escapeHtml(relation.source_field)}</code></li>`).join("")}</ul>` : "데이터 없음"}</dd></div>
    </dl>
  </details>`;
}

function renderWorkflowTechnicalDetails(workflow) {
  const resolvedComponentIds = (workflow.steps ?? []).flatMap(
    (step) => step.resolved_component_ids ?? [],
  );
  return `<details class="sot-technical-info">
    <summary>기술 정보</summary>
    <dl class="detail-list">
      <div><dt>Canonical ID</dt><dd class="path-value">${escapeHtml(workflow.workflow_id)}</dd></div>
      <div><dt>Source path</dt><dd class="path-value">${escapeHtml(workflow.source_path || "데이터 없음")}</dd></div>
      <div><dt>Domain</dt><dd>${escapeHtml(workflow.domain || "데이터 없음")}</dd></div>
      <div><dt>Invoked by</dt><dd class="path-value">${escapeHtml(workflow.optional_invoked_by || "데이터 없음")}</dd></div>
      <div><dt>Resolved component IDs</dt><dd>${renderList([...new Set(resolvedComponentIds)])}</dd></div>
    </dl>
    <pre aria-label="Raw Workflow YAML"><code>${escapeHtml(workflow.raw_yaml || "데이터 없음")}</code></pre>
  </details>`;
}

export function renderSotInspector(snapshot, view) {
  const workflow = snapshot?.workflows?.find(
    (candidate) => candidate.workflow_id === view.selectedWorkflowId,
  );
  if (workflow) {
    const lockedStepOrdinal = view.lockedWorkflowStep?.workflowId === workflow.workflow_id
      ? view.lockedWorkflowStep.ordinal
      : null;
    const orderedSteps = renderOrderedStepInspector({
      workflow: {
        ...workflow,
        name: workflow.title,
        authored_state: workflow.status,
      },
      components: snapshot?.components ?? [],
      lockedStepOrdinal,
    });
    const relatedComponents = workflowRelatedComponents(snapshot, workflow);
    return `<div class="workflow-inspector">
      <header class="sot-inspector-header">
        <div class="sot-inspector-title-row">
          <h3 class="detail-name">${escapeHtml(workflow.title || "선택한 Workflow")}</h3>
          ${renderInspectorKindBadge("workflow")}
        </div>
        <p class="detail-summary">${escapeHtml(workflow.description || "설명 데이터 없음")}</p>
      </header>
      <section class="workflow-inspector__ordered" aria-label="정의된 순서">
        ${orderedSteps}
      </section>
      ${renderRelatedGroup("component", "관련 Component", relatedComponents)}
      <section class="workflow-truth" data-workflow-truth>
        <h4>정의 상태</h4>
        <dl>
          <div><dt>Authored</dt><dd>작성된 정의 · ${escapeHtml(workflow.status || "상태 미지정")}</dd></div>
          <div><dt>Runtime</dt><dd>${workflow.runtime_implemented ? "Runtime 구현" : "Runtime 미구현"}</dd></div>
        </dl>
      </section>
      ${renderWorkflowTechnicalDetails(workflow)}
    </div>`;
  }
  const component = componentById(snapshot, view.selectedComponentId);
  if (!component) {
    const summary = snapshot?.checkout_summary ?? {};
    return `<div class="snapshot-inspector">
      <p class="detail-kicker">Snapshot</p>
      <h3 class="detail-name">${escapeHtml(snapshot?.components?.length ?? 0)} registry components</h3>
      <dl class="detail-list">
        <div><dt>Snapshot ID</dt><dd class="path-value">${escapeHtml(snapshot?.snapshot_id || "데이터 없음")}</dd></div>
        <div><dt>Source revision</dt><dd class="path-value">${escapeHtml(summary.source_revision || "데이터 없음")}</dd></div>
        <div><dt>Branch</dt><dd>${escapeHtml(summary.branch || (summary.detached ? "detached" : "데이터 없음"))}</dd></div>
        <div><dt>Working tree</dt><dd>${summary.dirty === true ? "변경 있음" : summary.dirty === false ? "Clean" : "데이터 없음"}</dd></div>
        <div><dt>Issues</dt><dd>${snapshot?.issues?.length ?? 0}</dd></div>
      </dl>
    </div>`;
  }
  const relations = (snapshot.relations ?? []).filter(
    (relation) => relation.source === component.component_id || relation.target === component.component_id,
  );
  return `<div class="component-inspector">
    <header class="sot-inspector-header">
      <div class="sot-inspector-title-row">
        <h3 class="detail-name">${escapeHtml(component.title || "선택한 Component")}</h3>
        ${renderInspectorKindBadge(component.kind)}
      </div>
      <p class="detail-summary">${escapeHtml(component.summary || "설명 데이터 없음")}</p>
    </header>
    ${renderRelatedGroup("workflow", "관련 Workflow", componentRelatedWorkflows(snapshot, component.component_id))}
    ${renderRelatedGroup("profile", "관련 Profile", componentRelatedProfiles(snapshot, component))}
    ${renderRelatedGroup("component", "관련 Component", componentRelatedComponents(snapshot, component.component_id))}
    ${renderComponentTechnicalDetails(component, relations)}
  </div>`;
}

export function renderSotTree(snapshot, view) {
  const filter = String(view.filter ?? "").trim().toLocaleLowerCase("ko");
  const components = [];
  for (const component of snapshot?.components ?? []) {
    const searchable = `${component.component_id} ${component.title} ${component.kind} ${component.domain ?? ""}`.toLocaleLowerCase("ko");
    if (filter && !searchable.includes(filter)) continue;
    components.push(component);
  }
  components.sort((left, right) => (left.kind || "unknown").localeCompare(right.kind || "unknown")
    || left.component_id.localeCompare(right.component_id));
  const profiles = (snapshot?.profiles ?? [])
    .filter((profile) => {
      if (!filter) return true;
      const searchable = `${profile.profile_id} ${profile.title} ${profile.summary ?? ""}`.toLocaleLowerCase("ko");
      return searchable.includes(filter);
    })
    .sort((left, right) => left.profile_id.localeCompare(right.profile_id));
  const expanded = new Set(Array.isArray(view.treeExpandedIds)
    ? view.treeExpandedIds
    : DEFAULT_TREE_EXPANDED_IDS);
  const rootExpanded = expanded.has("root") || Boolean(filter);
  const componentsExpanded = expanded.has("components") || Boolean(filter);
  const profilesExpanded = expanded.has("profiles") || Boolean(filter);
  const visibleIds = ["root"];
  if (rootExpanded) {
    visibleIds.push("components");
    if (componentsExpanded) visibleIds.push(...components.map((component) => component.component_id));
    visibleIds.push("profiles");
    if (profilesExpanded) visibleIds.push(...profiles.map((profile) => `profile:${profile.profile_id}`));
    visibleIds.push("registry.yml", "capabilities.yml");
  }
  const treeFocusId = visibleIds.includes(view.treeSelectedId)
    ? view.treeSelectedId
    : visibleIds[0];
  const selectedClass = (id) => view.treeSelectedId === id ? " tree-row--selected" : "";
  const selected = (id) => String(view.treeSelectedId === id);
  const tabIndex = (id) => treeFocusId === id ? "0" : "-1";
  const groupRow = (id, label, level, count, effectiveExpanded) => `<button${id === "root" ? ' id="typography-tree-row"' : ""} type="button" class="tree-row tree-row--group tree-row--level-${level}${selectedClass(id)}" role="treeitem" aria-level="${level}" aria-expanded="${effectiveExpanded}" aria-selected="${selected(id)}" tabindex="${tabIndex(id)}" data-sot-tree-node="${id}"><span class="tree-disclosure" aria-hidden="true">${effectiveExpanded ? "−" : "+"}</span><span class="tree-label">${label}</span><span class="tree-status">${count}</span></button>`;
  return `<div class="tree-tools">
    <label for="sot-tree-filter">Component filter</label>
    <input id="sot-tree-filter" type="search" value="${escapeHtml(view.filter)}" placeholder="name, kind, domain" />
  </div>
  <div class="tree-scroll" id="sot-tree-scroll" role="tree" aria-label="HarnessKit SoT" data-sot-tree-scroll>
    ${groupRow("root", "HarnessKit SoT", 1, snapshot?.components?.length ?? 0, rootExpanded)}
    ${rootExpanded ? `<div role="group" aria-label="HarnessKit SoT contents">
      ${groupRow("components", "components", 2, components.length, componentsExpanded)}
      ${componentsExpanded ? `<div role="group" aria-label="components">${components.map((component) => `<button type="button" class="tree-row tree-row--component tree-row--level-3${selectedClass(component.component_id)}" role="treeitem" aria-level="3" aria-selected="${selected(component.component_id)}" aria-label="${escapeHtml(`${component.title} · ${component.component_id} · ${component.kind || "unknown"}`)}" tabindex="${tabIndex(component.component_id)}" data-component-id="${escapeHtml(component.component_id)}" title="${escapeHtml(component.component_id)}"><span class="tree-disclosure" aria-hidden="true">·</span><span class="tree-label">${escapeHtml(component.title)}</span><span class="tree-status">${escapeHtml(component.kind || "unknown")}</span></button>`).join("") || '<p class="empty-state">일치하는 component가 없습니다.</p>'}</div>` : ""}
      ${groupRow("profiles", "profiles", 2, profiles.length, profilesExpanded)}
      ${profilesExpanded ? `<div role="group" aria-label="profiles">${profiles.map((profile) => {
        const id = `profile:${profile.profile_id}`;
        return `<button type="button" class="tree-row tree-row--level-3${selectedClass(id)}" role="treeitem" aria-level="3" aria-selected="${selected(id)}" tabindex="${tabIndex(id)}" data-sot-tree-node="${escapeHtml(id)}" data-sot-tree-profile="${escapeHtml(profile.profile_id)}"><span class="tree-disclosure" aria-hidden="true">·</span><span class="tree-label">${escapeHtml(profile.title)}</span><span class="tree-status">${profile.component_ids.length}</span></button>`;
      }).join("") || '<p class="empty-state">일치하는 profile이 없습니다.</p>'}</div>` : ""}
      ${["registry.yml", "capabilities.yml"].map((id) => `<button type="button" class="tree-row tree-row--level-2${selectedClass(id)}" role="treeitem" aria-level="2" aria-selected="${selected(id)}" tabindex="${tabIndex(id)}" data-sot-tree-node="${id}"><span class="tree-disclosure" aria-hidden="true">·</span><span class="tree-label">${id}</span><span class="tree-status">source</span></button>`).join("")}
    </div>` : ""}
  </div>`;
}

export { UNPROFILED_ID };
