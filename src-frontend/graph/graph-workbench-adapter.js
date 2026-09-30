import { validateGraphInput } from "@pureliture/graph-workbench";

import { bindSemanticGraphFallback } from "./semantic-fallback.js";

const GRAPH_PROJECTION_SCHEMA_VERSION = 2;

const NODE_TOKEN_BY_TYPE = Object.freeze({
  agent: "agent",
  command: "command",
  composite: "composite",
  hook: "hook",
  profile: "profile",
  rule: "rule",
  skill: "skill",
  workflow: "workflow",
});

const RELATION_TYPE_BY_KIND = Object.freeze({
  profile: "profile",
  unprofiled: "profile",
  workflow: "workflow",
});

const RENDERER_COPY = Object.freeze({
  failed: "3D 그래프를 시작할 수 없어 텍스트 보기로 전환했습니다.",
  loading: "3D 그래프를 준비하고 있습니다.",
  unavailable: "3D 그래프를 불러올 수 없어 텍스트 보기로 전환했습니다.",
  manual: "사용자가 텍스트 보기를 선택했습니다.",
});

function nonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringList(value) {
  return Array.isArray(value) ? value.map(nonEmptyString).filter(Boolean) : [];
}

function requireProjection(snapshot) {
  const projection = snapshot?.graph_projection;
  if (!projection || typeof projection !== "object") {
    throw new TypeError("snapshot graph_projection is required");
  }
  if (projection.schema_version !== GRAPH_PROJECTION_SCHEMA_VERSION) {
    throw new TypeError("snapshot graph_projection schema_version 2 is required");
  }
  if (!nonEmptyString(projection.layout_seed)) {
    throw new TypeError("snapshot graph_projection layout_seed is required");
  }
  if (!Array.isArray(projection.nodes) || !Array.isArray(projection.links)) {
    throw new TypeError("snapshot graph_projection nodes and links are required");
  }
  return projection;
}

function componentCatalogById(snapshot) {
  return new Map((Array.isArray(snapshot?.components) ? snapshot.components : [])
    .map((component) => [nonEmptyString(component?.component_id), component])
    .filter(([componentId]) => componentId));
}

function componentNode(node, catalog, masterNodeId) {
  const id = nonEmptyString(node?.node_id);
  const componentId = nonEmptyString(node?.component_id);
  const kind = nonEmptyString(node?.kind);
  const component = componentId ? catalog.get(componentId) : null;
  return {
    id,
    type: kind,
    kind,
    label: nonEmptyString(component?.title) ?? componentId,
    ...(id === masterNodeId ? { roles: ["master"] } : {}),
    metadata: {
      componentId,
      domain: nonEmptyString(node?.domain),
      entityType: "component",
      profileIds: stringList(node?.profile_ids),
      relationDegree: finiteNumber(node?.relation_degree),
      status: nonEmptyString(component?.status),
      summary: nonEmptyString(component?.summary),
      workflowIds: stringList(node?.workflow_ids),
    },
  };
}

function relationNode(node, masterNodeId) {
  const id = nonEmptyString(node?.node_id);
  const relationKind = nonEmptyString(node?.relation_kind);
  const canonicalId = nonEmptyString(node?.canonical_id);
  return {
    id,
    type: RELATION_TYPE_BY_KIND[relationKind] ?? "relation",
    kind: relationKind,
    label: nonEmptyString(node?.name) ?? canonicalId,
    ...(id === masterNodeId ? { roles: ["master"] } : {}),
    metadata: {
      anchorOrdinal: finiteNumber(node?.anchor_ordinal),
      canonicalId,
      entityType: "relation",
      exactCount: finiteNumber(node?.exact_count),
      relationKind,
      sizeScale: finiteNumber(node?.size_scale),
    },
  };
}

function projectionNode(node, catalog, masterNodeId) {
  if (node?.node_type === "component") return componentNode(node, catalog, masterNodeId);
  if (node?.node_type === "relation") return relationNode(node, masterNodeId);
  throw new TypeError("snapshot graph_projection node_type must be component or relation");
}

function normalizedOccurrences(link) {
  if (!Array.isArray(link?.occurrences) || link.occurrences.length === 0) return undefined;
  return link.occurrences.map((occurrence, index) => {
    const sourceOrdinal = Number.isInteger(occurrence?.ordinal) && occurrence.ordinal >= 0
      ? occurrence.ordinal
      : null;
    return {
      // graph-workbench requires an ordinal unique within each link. The SoT
      // ordinal is a workflow step position and can legitimately repeat when
      // one step has multiple references to the same component.
      ordinal: index,
      ...(nonEmptyString(occurrence?.step_id) ? { id: occurrence.step_id } : {}),
      metadata: {
        gateOrder: Number.isInteger(occurrence?.gate_order) ? occurrence.gate_order : null,
        loopBackTo: nonEmptyString(occurrence?.loop_back_to),
        mode: nonEmptyString(occurrence?.mode),
        role: nonEmptyString(occurrence?.role),
        sourceField: nonEmptyString(occurrence?.source_field),
        sourceOrdinal,
      },
    };
  });
}

function projectionLink(link) {
  const occurrences = normalizedOccurrences(link);
  return {
    id: nonEmptyString(link?.link_id),
    source: nonEmptyString(link?.source_node_id),
    target: nonEmptyString(link?.target_node_id),
    relationKind: nonEmptyString(link?.semantic),
    ...(occurrences?.[0] ? { ordinal: occurrences[0].ordinal, occurrences } : {}),
    metadata: {
      componentNodeId: nonEmptyString(link?.component_node_id),
      directionality: nonEmptyString(link?.directionality),
      occurrenceCount: occurrences?.length ?? 0,
      profileNodeId: nonEmptyString(link?.profile_node_id),
      sourceComponentId: nonEmptyString(link?.source_component_id),
      targetComponentId: nonEmptyString(link?.target_component_id),
      workflowNodeId: nonEmptyString(link?.workflow_node_id),
    },
  };
}

export function graphInputFromSotSnapshot(snapshot, { masterNodeId = null } = {}) {
  const projection = requireProjection(snapshot);
  const nodes = projection.nodes.map((node) => (
    projectionNode(node, componentCatalogById(snapshot), masterNodeId)
  ));
  const knownNodeIds = new Set(nodes.map((node) => node.id));
  if (masterNodeId !== null && (!nonEmptyString(masterNodeId) || !knownNodeIds.has(masterNodeId))) {
    throw new TypeError("masterNodeId must identify a graph projection node");
  }
  return validateGraphInput({
    schemaVersion: 1,
    layout: { seed: projection.layout_seed },
    nodes,
    links: projection.links.map(projectionLink),
    extensions: {
      snapshotId: nonEmptyString(projection.snapshot_id),
      sourceSchemaVersion: GRAPH_PROJECTION_SCHEMA_VERSION,
    },
  });
}

function selectedNodeId(input, view = {}) {
  const knownNodeIds = new Set(input.nodes.map((node) => node.id));
  const componentId = nonEmptyString(view?.selectedComponentId);
  const componentNodeId = input.nodes.find((node) => (
    node.metadata?.entityType === "component"
    && node.metadata.componentId === componentId
  ))?.id ?? null;
  return [
    nonEmptyString(view?.selectedRelationNodeId),
    componentNodeId,
  ].find((candidate) => knownNodeIds.has(candidate)) ?? null;
}

function nodeColorToken(node) {
  if (node.metadata?.entityType === "relation" && node.kind === "unprofiled") return "other";
  return NODE_TOKEN_BY_TYPE[node.type] ?? "other";
}

function linkColorToken(link) {
  if (link.relationKind === "profile-membership") return "profileEdge";
  if (link.relationKind === "workflow-step") return "workflowEdge";
  return "edge";
}

function usableColor(value) {
  return nonEmptyString(value) ?? undefined;
}

export function graphThemeTokensFromElement(element) {
  if (!element || typeof globalThis.getComputedStyle !== "function") return {};
  const computed = globalThis.getComputedStyle(element);
  const read = (name) => nonEmptyString(computed?.getPropertyValue?.(name)) ?? undefined;
  return {
    agent: read("--graph-kind-agent-stroke"),
    command: read("--graph-kind-command-stroke"),
    composite: read("--graph-kind-composite-stroke"),
    edge: read("--graph-edge"),
    hook: read("--graph-kind-hook-stroke"),
    other: read("--graph-kind-other-stroke"),
    profile: read("--graph-member-stroke"),
    profileEdge: read("--graph-member-stroke"),
    rule: read("--graph-kind-rule-stroke"),
    selected: read("--graph-edge-active"),
    skill: read("--graph-kind-skill-stroke"),
    workflow: read("--graph-kind-workflow-stroke"),
    workflowEdge: read("--graph-kind-workflow-stroke"),
  };
}

function graphPresentationForInput(input, view = {}, appearance = "dark", themeTokens = {}) {
  const selected = selectedNodeId(input, view);
  const known = new Set(input.nodes.map((node) => node.id));
  const focusCandidate = [
    nonEmptyString(view?.graphPresentation?.focusNodeId),
    nonEmptyString(view?.hoveredGraphNodeId),
    selected,
  ].find((candidate) => known.has(candidate)) ?? null;
  const theme = appearance === "light" ? "light" : "dark";
  const nodeDescriptors = Object.fromEntries(input.nodes.map((node) => {
    const color = usableColor(node.id === selected ? themeTokens.selected : themeTokens[nodeColorToken(node)]);
    return [node.id, { label: node.label, ...(color ? { color } : {}) }];
  }));
  const linkDescriptors = Object.fromEntries(input.links.map((link) => {
    const selectedLink = selected && (link.source === selected || link.target === selected);
    const color = usableColor(selectedLink ? themeTokens.selected : themeTokens[linkColorToken(link)]);
    return [link.id, {
      ...(color ? { color } : {}),
      ...(selectedLink ? { width: 1.6 } : {}),
    }];
  }));
  return {
    focusNodeId: focusCandidate,
    linkDescriptors,
    nodeDescriptors,
    selectedNodeIds: selected ? [selected] : [],
    theme,
  };
}

export function graphPresentationFromSotView(snapshot, view = {}, appearance = "dark", themeTokens = {}) {
  return graphPresentationForInput(
    graphInputFromSotSnapshot(snapshot),
    view,
    appearance,
    themeTokens,
  );
}

function graphActionForNode(input, nodeId) {
  const node = input.nodes.find((candidate) => candidate.id === nodeId);
  const metadata = node?.metadata ?? {};
  if (metadata.entityType === "component" && nonEmptyString(metadata.componentId)) {
    return { type: "select_graph_node", componentId: metadata.componentId };
  }
  if (metadata.entityType === "relation"
    && nonEmptyString(metadata.relationKind)
    && nonEmptyString(metadata.canonicalId)) {
    return {
      type: "select_graph_relation",
      nodeId,
      relationKind: metadata.relationKind,
      canonicalId: metadata.canonicalId,
    };
  }
  return null;
}

function browserRuntimeAvailable() {
  return Boolean(globalThis.window && globalThis.document);
}

function keyboardTarget(input, currentNodeId, direction) {
  if (input.nodes.length === 0) return null;
  const nodeIds = input.nodes.map((node) => node.id);
  const currentIndex = currentNodeId ? nodeIds.indexOf(currentNodeId) : -1;
  const nextIndex = currentIndex < 0
    ? direction === 1 ? 0 : nodeIds.length - 1
    : (currentIndex + direction + nodeIds.length) % nodeIds.length;
  return nodeIds[nextIndex] ?? null;
}

function createHostControlledRendererFactory({
  createRenderer,
  input,
  resolveHostSelection,
  onAcceptedSelection,
} = {}) {
  return (rendererOptions = {}) => {
    if (typeof createRenderer !== "function") {
      throw new TypeError("browser graph renderer factory is required");
    }
    const callbacks = rendererOptions.callbacks;
    if (!callbacks || typeof callbacks.onBackgroundClick !== "function"
      || typeof callbacks.onNodeClick !== "function") {
      throw new TypeError("graph renderer callbacks are required");
    }

    const resolveAndDispatch = (nodeId, source, dispatch) => {
      const action = nodeId
        ? graphActionForNode(input, nodeId)
        : { type: "clear_graph_selection" };
      if (!action) return false;
      const intent = { nodeId: nodeId ?? null, source };
      if (typeof resolveHostSelection === "function") {
        const acceptance = resolveHostSelection({ action, intent });
        if (acceptance === false) return false;
        onAcceptedSelection?.({ action, nodeId: nodeId ?? null });
      }
      dispatch();
      return true;
    };

    return createRenderer({
      ...rendererOptions,
      callbacks: {
        ...callbacks,
        onBackgroundClick: () => resolveAndDispatch(
          null,
          "background",
          callbacks.onBackgroundClick,
        ),
        onNodeClick: (nodeId) => resolveAndDispatch(
          nodeId,
          "mouse",
          () => callbacks.onNodeClick(nodeId),
        ),
        onNodeHover: callbacks.onNodeHover,
      },
    });
  };
}

function sceneHostFor(root) {
  return root?.querySelector?.("[data-component-map-scene-host]") ?? root;
}

function semanticHostFor(root) {
  return root?.querySelector?.("[data-component-map-semantic-host]") ?? null;
}

function rendererStatusFor(root) {
  return root?.querySelector?.("[data-graph-renderer-state]") ?? null;
}

function scaleFor(root) {
  return root?.querySelector?.("[data-graph-scale]") ?? null;
}

function setStyleProperty(element, name, value) {
  if (typeof element?.style?.setProperty === "function") {
    element.style.setProperty(name, value);
  } else if (element?.style) {
    element.style[name] = value;
  }
}

function positiveViewportSize(host, contentRect = null) {
  const measuredRect = host?.getBoundingClientRect?.() ?? null;
  const width = [
    finiteNumber(contentRect?.width),
    finiteNumber(measuredRect?.width),
    finiteNumber(host?.clientWidth),
  ].find((value) => value > 0) ?? null;
  const height = [
    finiteNumber(contentRect?.height),
    finiteNumber(measuredRect?.height),
    finiteNumber(host?.clientHeight),
  ].find((value) => value > 0) ?? null;
  return width && height ? { width, height } : null;
}

function setRendererAvailability(host, availability) {
  if (!host) return;
  if (host.dataset) host.dataset.rendererAvailability = availability;
  else host.setAttribute?.("data-renderer-availability", availability);
}

function showGraphState(root, state, message) {
  const sceneHost = sceneHostFor(root);
  if (sceneHost) {
    if (sceneHost.dataset) sceneHost.dataset.graphWorkbenchState = state;
    sceneHost.classList?.add?.("component-map-scene-host--message");
    sceneHost.textContent = message;
    sceneHost.hidden = false;
  }
  const semanticHost = semanticHostFor(root);
  if (semanticHost) {
    semanticHost.hidden = true;
    semanticHost.inert = true;
    semanticHost.setAttribute?.("aria-hidden", "true");
    setRendererAvailability(semanticHost, "unavailable");
  }
  const status = rendererStatusFor(root);
  if (status) {
    status.textContent = message;
    status.hidden = false;
  }
}

function clearGraphState(root) {
  const sceneHost = sceneHostFor(root);
  if (sceneHost) {
    sceneHost.classList?.remove?.("component-map-scene-host--message");
    sceneHost.textContent = "";
  }
  const status = rendererStatusFor(root);
  if (status) {
    status.textContent = "";
    status.hidden = true;
  }
}

function semanticPresentation(view = {}) {
  return {
    activeProfileId: view.activeProfileId ?? null,
    hoveredGraphNodeId: view.hoveredGraphNodeId ?? null,
    hoveredWorkflowStep: view.hoveredWorkflowStep ?? null,
    lockedWorkflowStep: view.lockedWorkflowStep ?? null,
    selectedComponentId: view.selectedComponentId ?? null,
    selectedRelationNodeId: view.selectedRelationNodeId ?? null,
    selectedWorkflowId: view.selectedWorkflowId ?? null,
  };
}

function graphIdentityNode(input, view = {}) {
  const byId = new Map(input.nodes.map((node) => [node.id, node]));
  const componentId = nonEmptyString(view?.selectedComponentId);
  return [
    nonEmptyString(view?.graphPresentation?.focusNodeId),
    nonEmptyString(view?.hoveredGraphNodeId),
    nonEmptyString(view?.selectedRelationNodeId),
    input.nodes.find((node) => (
      node.metadata?.entityType === "component" && node.metadata.componentId === componentId
    ))?.id,
  ].map((nodeId) => byId.get(nodeId) ?? null).find(Boolean) ?? null;
}

function safeIdentityTitle(...candidates) {
  const value = candidates.map(nonEmptyString).find(Boolean) ?? "Component";
  if (!/^harnesskit\./i.test(value)) return value;
  return value.split(".").at(-1) || "Component";
}

function graphIdentitySurfaceFor(root) {
  return root?.closest?.("[data-component-map-viewport]") ?? root;
}

function graphIdentityViewportFor(root, surface) {
  return root?.querySelector?.("[data-component-map-viewport]") ?? surface;
}

function graphIdentityHudFor(root, viewport) {
  return root?.querySelector?.("[data-component-map-hud]")
    ?? viewport?.closest?.(".component-map")?.querySelector?.("[data-component-map-hud]")
    ?? null;
}

function syncGraphIdentityOverlayGeometry(root, surface, overlay) {
  const viewport = graphIdentityViewportFor(root, surface);
  const viewportRect = viewport?.getBoundingClientRect?.() ?? null;
  const hudRect = graphIdentityHudFor(root, viewport)?.getBoundingClientRect?.() ?? null;
  const rectNumber = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : NaN;
  };
  const viewportTop = rectNumber(viewportRect?.top ?? viewportRect?.y);
  const viewportHeight = rectNumber(viewportRect?.height);
  const hudTop = rectNumber(hudRect?.top ?? hudRect?.y);
  const hudHeight = rectNumber(hudRect?.height);
  if (![viewportTop, viewportHeight, hudTop, hudHeight].every(Number.isFinite)
    || viewportHeight <= 0 || hudHeight <= 0) {
    return;
  }
  const viewportBottom = viewportTop + viewportHeight;
  const hudBottom = hudTop + hudHeight;
  const hudOverlapsViewport = hudBottom > viewportTop && hudTop < viewportBottom;
  const topInset = hudOverlapsViewport
    ? Math.max(12, hudBottom - viewportTop + 12)
    : 12;
  setStyleProperty(overlay, "inset-block-start", `${topInset}px`);
  setStyleProperty(overlay, "inset-inline-start", "12px");
}

function graphIdentityPresentation(snapshot, input, view = {}) {
  const node = graphIdentityNode(input, view);
  if (!node) return null;
  if (node.metadata?.entityType === "relation") {
    const kind = nonEmptyString(node.metadata.relationKind) ?? "relation";
    const count = Math.max(0, finiteNumber(node.metadata.exactCount) ?? 0);
    return {
      count: `${count} ${kind === "workflow" ? (count === 1 ? "step" : "steps") : (count === 1 ? "component" : "components")}`,
      kind: kind.toUpperCase(),
      title: safeIdentityTitle(node.label, node.metadata.canonicalId),
    };
  }
  const component = (Array.isArray(snapshot?.components) ? snapshot.components : []).find(
    (candidate) => candidate?.component_id === node.metadata?.componentId,
  );
  const kind = nonEmptyString(node.kind) ?? nonEmptyString(component?.kind) ?? "component";
  const degree = Math.max(0, finiteNumber(node.metadata?.relationDegree) ?? 0);
  return {
    count: `${degree} ${degree === 1 ? "relation" : "relations"}`,
    kind: kind.toUpperCase(),
    title: safeIdentityTitle(component?.title, node.label, node.metadata?.componentId),
  };
}

function syncGraphIdentityOverlay(root, snapshot, input, view) {
  const surface = graphIdentitySurfaceFor(root);
  const overlay = surface?.querySelector?.("[data-graph-identity-overlay]") ?? null;
  if (!overlay) return;
  syncGraphIdentityOverlayGeometry(root, surface, overlay);
  const title = surface.querySelector?.("[data-graph-identity-title]") ?? null;
  const kind = surface.querySelector?.("[data-graph-identity-kind]") ?? null;
  const count = surface.querySelector?.("[data-graph-identity-count]") ?? null;
  const identity = graphIdentityPresentation(snapshot, input, view);
  const identityNode = graphIdentityNode(input, view);
  const selected = selectedNodeId(input, view);
  const focused = nonEmptyString(view?.graphPresentation?.focusNodeId);
  const emphasized = Boolean(identityNode && (identityNode.id === selected || identityNode.id === focused));
  overlay.hidden = !identity;
  overlay.setAttribute?.("aria-hidden", String(!identity));
  setStyleProperty(overlay, "--graph-identity-opacity", identity ? "1" : "0");
  setStyleProperty(
    overlay,
    "--graph-identity-emphasis-percent",
    emphasized ? "100%" : "0%",
  );
  setStyleProperty(
    overlay,
    "--graph-identity-background-percent",
    emphasized ? "8%" : "0%",
  );
  if (overlay.dataset) {
    overlay.dataset.identityPresence = identity ? "visible" : "hidden";
    overlay.dataset.identityState = focused === identityNode?.id
      ? "focused"
      : selected === identityNode?.id
        ? "selected"
        : "idle";
  }
  if (title) title.textContent = identity?.title ?? "";
  if (kind) kind.textContent = identity?.kind ?? "";
  if (count) count.textContent = identity?.count ?? "";
}

function createInvalidGraphSession(message) {
  let attached = false;
  let expanded = true;
  let root = null;

  const render = () => {
    if (attached && expanded && root) showGraphState(root, "invalid", message);
  };

  return {
    attach(nextRoot) {
      root = nextRoot ?? root;
      attached = Boolean(root);
      render();
      return false;
    },
    detach() {
      attached = false;
      root = null;
      return true;
    },
    dispose() {
      attached = false;
      root = null;
    },
    fitGraph: () => false,
    focusNode: () => false,
    refreshAfterLayout: () => false,
    resize: () => false,
    restoreCamera: () => false,
    retry: () => false,
    returnToThreeD: () => false,
    setExpanded(nextExpanded) {
      expanded = Boolean(nextExpanded);
      render();
      return false;
    },
    setResolvedAppearance: () => true,
    syncPresentation: () => true,
  };
}

function rendererAvailabilityForMode(mode) {
  if (mode === "ready") return "ready";
  if (mode === "pending") return "pending";
  if (mode === "manual" || mode === "returning") return "manual_fallback";
  return "unavailable";
}

export function createGraphWorkbenchAdapter({
  snapshot,
  onAction = () => {},
  masterNodeId = null,
  browserRuntime = browserRuntimeAvailable(),
  loadBrowserWorkbench = async () => {
    const [coreModule, browserModule] = await Promise.all([
      import("@pureliture/graph-workbench"),
      import("@pureliture/graph-workbench/browser"),
    ]);
    return { ...coreModule, ...browserModule };
  },
  legacySessionFactory = null,
  resolveSelection: resolveHostSelection,
  resizeObserverFactory = typeof globalThis.ResizeObserver === "function"
    ? (callback) => new globalThis.ResizeObserver(callback)
    : null,
  requestFrame = typeof globalThis.requestAnimationFrame === "function"
    ? (callback) => globalThis.requestAnimationFrame(callback)
    : null,
  cancelFrame = typeof globalThis.cancelAnimationFrame === "function"
    ? (frameId) => globalThis.cancelAnimationFrame(frameId)
    : null,
  semanticFallbackFactory = bindSemanticGraphFallback,
} = {}) {
  if (!browserRuntime && typeof legacySessionFactory === "function") {
    return legacySessionFactory({ snapshot, onAction });
  }

  let input;
  try {
    input = graphInputFromSotSnapshot(snapshot, { masterNodeId });
  } catch {
    return createInvalidGraphSession(
      "그래프 데이터를 확인할 수 없습니다. 기존 상세 정보는 계속 사용할 수 있습니다.",
    );
  }

  const projection = requireProjection(snapshot);
  const knownNodeIds = new Set(input.nodes.map((node) => node.id));
  const hasGraphData = input.nodes.length > 0;
  let appearance = "dark";
  let attached = false;
  let attachmentGeneration = 0;
  let bindingRoot = null;
  let controlBindings = [];
  let disposed = false;
  let expanded = true;
  let loading = null;
  let mode = browserRuntime ? "pending" : "failed";
  let reason = browserRuntime ? RENDERER_COPY.loading : RENDERER_COPY.unavailable;
  let root = null;
  let semanticBinding = null;
  let currentView = {};
  let workbench = null;
  let acceptedSelection = null;
  let keyboardBinding = null;
  let rendererSuspended = false;
  let resizeObserver = null;
  let observedSceneHost = null;
  let initialFitFrame = null;
  let initialFitFollowupFrame = null;
  let initialFitCandidate = null;
  const initiallyFramedWorkbenches = new WeakSet();
  let activityState = {
    expanded: true,
    foreground: true,
    intersecting: true,
    reducedMotion: false,
  };

  const isCurrent = (lease) => (
    !disposed
    && attached
    && expanded
    && root
    && lease.generation === attachmentGeneration
    && lease.root === root
  );

  const leaseForCurrentAttachment = () => ({ generation: attachmentGeneration, root });

  const isLoadingMode = () => ["pending", "retrying", "returning"].includes(mode);

  const cancelInitialFit = () => {
    if (initialFitFrame !== null) cancelFrame?.(initialFitFrame);
    if (initialFitFollowupFrame !== null) cancelFrame?.(initialFitFollowupFrame);
    initialFitFrame = null;
    initialFitFollowupFrame = null;
    initialFitCandidate = null;
  };

  const releaseViewportBinding = () => {
    cancelInitialFit();
    resizeObserver?.disconnect?.();
    resizeObserver = null;
    observedSceneHost = null;
  };

  const fallbackRendererState = () => {
    const manual = mode === "manual" || mode === "returning";
    return {
      availability: rendererAvailabilityForMode(mode),
      presentationMode: manual ? "manual" : "failure",
      reason: mode === "ready" ? null : reason,
    };
  };

  const releaseRootBindings = () => {
    releaseViewportBinding();
    semanticBinding?.release?.();
    semanticBinding = null;
    bindingRoot = null;
    controlBindings.forEach(({ control, listener }) => {
      control.removeEventListener?.("click", listener);
    });
    controlBindings = [];
  };

  const syncSurfaceState = () => {
    if (!root) return;
    const ready = mode === "ready";
    const fallback = fallbackRendererState();
    const sceneHost = sceneHostFor(root);
    if (sceneHost) {
      if (sceneHost.dataset) sceneHost.dataset.graphWorkbenchState = mode;
      sceneHost.hidden = !ready;
      sceneHost.setAttribute?.("aria-hidden", "true");
    }
    const semanticHost = semanticHostFor(root);
    if (semanticHost) {
      semanticHost.hidden = false;
      semanticHost.inert = mode === "pending";
      semanticHost.setAttribute?.("aria-hidden", String(mode === "pending"));
      setRendererAvailability(semanticHost, fallback.availability);
    }
    const status = rendererStatusFor(root);
    if (status) {
      status.hidden = !["failed", "retrying"].includes(mode);
      status.textContent = status.hidden ? "" : reason;
    }
    const scale = scaleFor(root);
    if (scale) {
      scale.textContent = ready ? "3D" : mode === "pending" ? "3D · 준비" : "3D · 목록";
    }
    const semanticView = root.querySelector?.("[data-graph-semantic-view]");
    if (semanticView) {
      semanticView.hidden = !ready;
      semanticView.disabled = !ready;
    }
    [...(root.querySelectorAll?.("[data-graph-zoom]") ?? [])].forEach((control) => {
      control.disabled = !ready;
    });
    syncGraphIdentityOverlay(root, snapshot, input, currentView);
    semanticBinding?.render?.();
  };

  const syncPresentation = () => {
    const themeTokens = graphThemeTokensFromElement(sceneHostFor(root));
    workbench?.setPresentation?.(graphPresentationForInput(
      input,
      currentView,
      appearance,
      themeTokens,
    ));
    syncSurfaceState();
  };

  const resizeWorkbenchToHost = (candidate, sceneHost, contentRect = null) => {
    if (!candidate || workbench !== candidate || mode !== "ready" || !attached || !expanded) return false;
    const viewport = positiveViewportSize(sceneHost, contentRect);
    if (!viewport) return false;
    candidate.resize?.(viewport.width, viewport.height);
    return true;
  };

  const bindViewportResize = () => {
    const sceneHost = sceneHostFor(root);
    if (!sceneHost || typeof resizeObserverFactory !== "function") return false;
    resizeObserver?.disconnect?.();
    observedSceneHost = sceneHost;
    resizeObserver = resizeObserverFactory((entries = []) => {
      if (sceneHost !== observedSceneHost || sceneHost !== sceneHostFor(root)) return;
      const entry = entries.find((candidate) => candidate?.target === sceneHost) ?? entries[0] ?? null;
      resizeWorkbenchToHost(workbench, sceneHost, entry?.contentRect ?? null);
      const surface = graphIdentitySurfaceFor(sceneHost);
      const overlay = surface?.querySelector?.("[data-graph-identity-overlay]") ?? null;
      if (overlay) syncGraphIdentityOverlayGeometry(root, surface, overlay);
    });
    resizeObserver?.observe?.(sceneHost);
    return Boolean(resizeObserver);
  };

  const scheduleInitialFit = (candidate, lease, sceneHost) => {
    if (initiallyFramedWorkbenches.has(candidate) || typeof requestFrame !== "function") return false;
    cancelInitialFit();
    initialFitCandidate = candidate;
    initialFitFrame = requestFrame(() => {
      initialFitFrame = null;
      initialFitFollowupFrame = requestFrame(() => {
        initialFitFollowupFrame = null;
        if (initialFitCandidate !== candidate
          || !isCurrent(lease)
          || workbench !== candidate
          || mode !== "ready") {
          initialFitCandidate = null;
          return;
        }
        const selected = selectedNodeId(input, currentView);
        const focused = nonEmptyString(currentView?.graphPresentation?.focusNodeId);
        if (!selected && !focused && resizeWorkbenchToHost(candidate, sceneHost)) {
          candidate.fit?.(0);
        }
        initiallyFramedWorkbenches.add(candidate);
        initialFitCandidate = null;
      });
    });
    return true;
  };

  const releaseKeyboardBinding = (candidate = null) => {
    if (!keyboardBinding || (candidate && keyboardBinding.candidate !== candidate)) return false;
    keyboardBinding.host.removeEventListener?.(
      "keydown",
      keyboardBinding.listener,
      true,
    );
    keyboardBinding = null;
    return true;
  };

  const retireWorkbench = (candidate = workbench) => {
    if (!candidate) return;
    if (initialFitCandidate === candidate) cancelInitialFit();
    releaseKeyboardBinding(candidate);
    if (workbench === candidate) workbench = null;
    rendererSuspended = false;
    candidate.destroy?.();
  };

  const syncActivityState = (candidate = workbench) => {
    if (typeof candidate?.setReducedMotion === "function") {
      candidate.setReducedMotion(Boolean(activityState.reducedMotion));
      return true;
    }
    return false;
  };

  const transitionToFailure = (lease, candidate, nextReason) => {
    if (!isCurrent(lease) || mode === "failed") return false;
    if (candidate && workbench !== candidate) return false;
    retireWorkbench(candidate);
    mode = "failed";
    reason = nextReason;
    syncSurfaceState();
    onAction({ type: "graph_renderer_failed", failure: nextReason });
    return true;
  };

  const transitionToReady = (lease, candidate) => {
    if (!isCurrent(lease) || workbench !== candidate) return false;
    const wasReady = mode === "ready";
    mode = "ready";
    reason = null;
    rendererSuspended = false;
    syncSurfaceState();
    if (!wasReady) onAction({ type: "graph_renderer_ready" });
    return true;
  };

  const selectFromKeyboard = (candidate, nodeId, source) => {
    const action = nodeId
      ? graphActionForNode(input, nodeId)
      : { type: "clear_graph_selection" };
    if (!action || typeof candidate?.selectNode !== "function") return false;
    const intent = { nodeId: nodeId ?? null, source };
    if (typeof resolveHostSelection === "function") {
      const acceptance = resolveHostSelection({ action, intent });
      if (acceptance === false) return false;
      acceptedSelection = { action, nodeId: nodeId ?? null };
    }
    candidate.selectNode(nodeId, source);
    return true;
  };

  const bindKeyboardSelection = (candidate, lease, sceneHost) => {
    releaseKeyboardBinding();
    if (typeof sceneHost?.addEventListener !== "function"
      || typeof candidate?.selectNode !== "function") return false;
    const listener = (event) => {
      if (!isCurrent(lease) || workbench !== candidate || mode !== "ready") return;
      const currentNodeId = candidate.getSelectionState?.()?.nodeId
        ?? selectedNodeId(input, currentView);
      let nextNodeId;
      let source = "keyboard";
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        nextNodeId = keyboardTarget(input, currentNodeId, 1);
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        nextNodeId = keyboardTarget(input, currentNodeId, -1);
      } else if (event.key === "Enter" && currentNodeId) {
        nextNodeId = currentNodeId;
      } else if (event.key === "Escape") {
        nextNodeId = null;
        source = "background";
      } else {
        return;
      }
      event.preventDefault?.();
      event.stopImmediatePropagation?.();
      selectFromKeyboard(candidate, nextNodeId, source);
    };
    sceneHost.addEventListener("keydown", listener, true);
    keyboardBinding = { candidate, host: sceneHost, listener };
    return true;
  };

  const enterManualFallback = () => {
    if (!attached || !expanded || !root || mode !== "ready") return false;
    attachmentGeneration += 1;
    retireWorkbench();
    mode = "manual";
    reason = RENDERER_COPY.manual;
    syncSurfaceState();
    onAction({ type: "show_graph_semantic" });
    return true;
  };

  const mountWorkbench = (candidate, lease) => {
    if (!isCurrent(lease) || workbench !== candidate || !["ready", "pending", "retrying", "returning"].includes(mode)) {
      return false;
    }
    try {
      releaseKeyboardBinding(candidate);
      clearGraphState(root);
      const sceneHost = sceneHostFor(root);
      // graph-workbench snapshots its viewport during mount. Reveal the host before
      // that snapshot so an initial or retried graph cannot be fixed at 1×1.
      sceneHost.hidden = false;
      candidate.mount(sceneHost);
      if (!isCurrent(lease) || workbench !== candidate) return false;
      candidate.resize?.();
      syncPresentation();
      syncActivityState(candidate);
      bindKeyboardSelection(candidate, lease, sceneHost);
      rendererSuspended = false;
      scheduleInitialFit(candidate, lease, sceneHost);
      return true;
    } catch {
      transitionToFailure(lease, candidate, RENDERER_COPY.failed);
      return false;
    }
  };

  const ensureWorkbench = () => {
    if (!attached || !expanded || !root || disposed || !hasGraphData || !isLoadingMode()) {
      return Promise.resolve();
    }
    if (workbench) return Promise.resolve();
    const lease = leaseForCurrentAttachment();
    if (loading?.lease.generation === lease.generation && loading.lease.root === lease.root) {
      return loading.promise;
    }
    const task = { lease, promise: null };
    task.promise = (async () => {
      let candidate = null;
      try {
        const browserModule = await loadBrowserWorkbench();
        if (!isCurrent(lease) || !isLoadingMode() || workbench) return;
        const workbenchOptions = {
          input,
          onSelectionChange: ({ nodeId, source }) => {
            if (!isCurrent(lease) || workbench !== candidate) return;
            const action = nodeId
              ? graphActionForNode(input, nodeId)
              : { type: "clear_graph_selection" };
            const accepted = acceptedSelection?.nodeId === (nodeId ?? null)
              ? acceptedSelection.action
              : null;
            acceptedSelection = null;
            if (accepted) {
              if (accepted.type === "clear_graph_selection") candidate.restoreCamera?.();
              onAction(accepted);
              return;
            }
            if (source !== "programmatic" && action) onAction(action);
          },
          onNodeHover: ({ nodeId }) => {
            if (isCurrent(lease) && workbench === candidate) {
              onAction({ type: "hover_graph_node", nodeId });
            }
          },
          onRendererStateChange: ({ status }) => {
            if (!isCurrent(lease) || workbench !== candidate) return;
            if (status === "mounted") transitionToReady(lease, candidate);
            if (status === "failed") transitionToFailure(lease, candidate, RENDERER_COPY.failed);
          },
        };
        if (typeof browserModule?.createGraphWorkbench !== "function") {
          throw new TypeError("graph workbench public factory is required");
        }
        candidate = browserModule.createGraphWorkbench({
          ...workbenchOptions,
          rendererFactory: createHostControlledRendererFactory({
            createRenderer: browserModule.createThreeForceGraphRenderer,
            input,
            resolveHostSelection,
            onAcceptedSelection(selection) {
              acceptedSelection = selection;
            },
          }),
        });
        if (!isCurrent(lease) || !isLoadingMode()) {
          candidate.destroy?.();
          return;
        }
        workbench = candidate;
        mountWorkbench(candidate, lease);
      } catch {
        if (candidate && workbench === candidate) {
          transitionToFailure(lease, candidate, RENDERER_COPY.failed);
        } else if (isCurrent(lease) && isLoadingMode()) {
          transitionToFailure(lease, null, RENDERER_COPY.unavailable);
        }
      } finally {
        if (loading === task) loading = null;
        if (isCurrent(lease) && isLoadingMode() && !workbench && !loading) {
          void ensureWorkbench();
        }
      }
    })();
    loading = task;
    return task.promise;
  };

  const startFreshContext = (nextMode, action = null) => {
    if (!attached || !expanded || !root || !hasGraphData || disposed) return false;
    attachmentGeneration += 1;
    retireWorkbench();
    mode = nextMode;
    reason = nextMode === "returning" ? RENDERER_COPY.manual : reason;
    syncSurfaceState();
    if (action) onAction(action);
    void ensureWorkbench();
    return true;
  };

  const retry = () => (
    browserRuntime && ["failed", "retrying"].includes(mode) && startFreshContext("retrying")
  );

  const returnToThreeD = () => {
    if (["manual", "returning"].includes(mode)) {
      return startFreshContext("returning", { type: "return_graph_three_d" });
    }
    return retry();
  };

  const bindControls = () => {
    if (!root) return;
    const semanticView = root.querySelector?.("[data-graph-semantic-view]");
    if (semanticView?.addEventListener) {
      const listener = () => { enterManualFallback(); };
      semanticView.addEventListener("click", listener);
      controlBindings.push({ control: semanticView, listener });
    }
    [...(root.querySelectorAll?.("[data-graph-zoom]") ?? [])].forEach((control) => {
      if (!control?.addEventListener) return;
      const listener = () => {
        if (mode !== "ready") return;
        if (control.dataset?.graphZoom === "out") workbench?.zoom?.(0.8);
        if (control.dataset?.graphZoom === "in") workbench?.zoom?.(1.2);
        if (control.dataset?.graphZoom === "reset") workbench?.fit?.();
      };
      control.addEventListener("click", listener);
      controlBindings.push({ control, listener });
    });
  };

  const bindSemanticSurface = () => {
    const host = semanticHostFor(root);
    if (!host || (semanticBinding && bindingRoot === host)) return Boolean(semanticBinding);
    releaseRootBindings();
    bindingRoot = host;
    semanticBinding = semanticFallbackFactory(host, {
      projection,
      componentCatalog: snapshot?.components,
      getPresentation: () => semanticPresentation(currentView),
      getRendererState: fallbackRendererState,
      onBackgroundSelect: () => onAction({ type: "clear_graph_selection" }),
      onNodeHover: (nodeId) => onAction({ type: "hover_graph_node", nodeId }),
      onNodeSelect: (node) => {
        const action = graphActionForNode(input, node?.node_id);
        if (action) onAction(action);
      },
      onRetry: retry,
      onReturn: returnToThreeD,
      onWorkflowStepHover: (step) => onAction({ type: "hover_workflow_step", step }),
      onWorkflowStepSelect: ({ workflowId, ordinal }) => onAction({
        type: "select_workflow_step",
        workflowId,
        ordinal,
      }),
    });
    bindControls();
    return true;
  };

  return {
    attach(nextRoot, view = currentView) {
      root = nextRoot ?? root;
      currentView = view ?? currentView;
      attached = Boolean(root);
      attachmentGeneration += 1;
      if (!root || !expanded) return false;
      if (!hasGraphData) {
        showGraphState(root, "empty", "표시할 Profile, Workflow 또는 component 데이터가 없습니다.");
        return false;
      }
      bindSemanticSurface();
      bindViewportResize();
      syncSurfaceState();
      const lease = leaseForCurrentAttachment();
      if (workbench && mode === "ready") mountWorkbench(workbench, lease);
      else if (isLoadingMode()) void ensureWorkbench();
      return true;
    },
    detach() {
      attachmentGeneration += 1;
      attached = false;
      releaseRootBindings();
      releaseKeyboardBinding();
      workbench?.unmount?.();
      rendererSuspended = false;
      root = null;
      return true;
    },
    dispose() {
      if (disposed) return;
      attachmentGeneration += 1;
      disposed = true;
      attached = false;
      releaseRootBindings();
      retireWorkbench();
      root = null;
    },
    fitGraph() {
      if (mode !== "ready" || !workbench) return false;
      initiallyFramedWorkbenches.add(workbench);
      cancelInitialFit();
      workbench.fit?.();
      return true;
    },
    focusNode(nodeId) {
      if (mode !== "ready" || !workbench || !knownNodeIds.has(nodeId)) return false;
      initiallyFramedWorkbenches.add(workbench);
      cancelInitialFit();
      workbench.focusNode(nodeId);
      return true;
    },
    refreshAfterLayout() {
      if (mode !== "ready" || !workbench) return false;
      workbench.resize?.();
      return true;
    },
    retry,
    returnToThreeD,
    resize() {
      if (mode !== "ready" || !workbench) return false;
      workbench.resize?.();
      return true;
    },
    restoreCamera() {
      if (mode !== "ready" || !workbench) return false;
      initiallyFramedWorkbenches.add(workbench);
      cancelInitialFit();
      workbench.restoreCamera();
      return true;
    },
    setExpanded(nextExpanded) {
      expanded = Boolean(nextExpanded);
      activityState = { ...activityState, expanded };
      attachmentGeneration += 1;
      if (!expanded) {
        syncActivityState();
        releaseKeyboardBinding();
        workbench?.unmount?.();
        rendererSuspended = Boolean(workbench);
        syncSurfaceState();
        return false;
      }
      if (!attached || !root) return false;
      if (!hasGraphData) {
        showGraphState(root, "empty", "표시할 Profile, Workflow 또는 component 데이터가 없습니다.");
        return false;
      }
      if (workbench && mode === "ready") {
        if (rendererSuspended) {
          mountWorkbench(workbench, leaseForCurrentAttachment());
        } else {
          syncActivityState();
          workbench.resize?.();
          syncPresentation();
        }
      }
      else if (isLoadingMode()) void ensureWorkbench();
      syncSurfaceState();
      return true;
    },
    setResolvedAppearance(nextAppearance) {
      appearance = String(nextAppearance).toLowerCase() === "light" ? "light" : "dark";
      syncPresentation();
      return true;
    },
    syncPresentation(view) {
      currentView = view ?? {};
      syncPresentation();
      return true;
    },
    setActivityState(nextState = {}) {
      activityState = { ...activityState, ...nextState };
      return syncActivityState();
    },
    suspend() {
      if (!workbench || mode !== "ready") return false;
      releaseKeyboardBinding();
      workbench.unmount?.();
      rendererSuspended = true;
      return true;
    },
    resume() {
      if (!workbench || !attached || !expanded || mode !== "ready") return false;
      if (rendererSuspended) return mountWorkbench(workbench, leaseForCurrentAttachment());
      return syncActivityState();
    },
  };
}
