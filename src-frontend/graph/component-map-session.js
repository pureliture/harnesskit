import { createThreeGraphRenderer } from "./component-map.js";
import { createGraphSceneController } from "./scene-adapter.js";
import { bindSemanticGraphFallback } from "./semantic-fallback.js";
import { bindRelationLabelOverlay } from "./relation-label-overlay.js";
import {
  graphProjectionIdentity,
  graphSpatialPolicyFor,
} from "./spatial-policy.js";
import { createGraphSafeInset } from "./viewport-geometry.js";
import { Vector3 } from "three";

const EMPTY_RENDERER_STATE = Object.freeze({
  availability: "pending",
  reason: "3D 그래프를 준비하고 있습니다.",
});

const EMPTY_CAMERA_SCALE = Object.freeze({
  scale: 1,
  minScale: 0.75,
  maxScale: 8,
  level: "overview",
});

const INITIAL_GRAPH_PRESENTATION = Object.freeze({
  viewMode: "three_d",
  rendererLifecycle: "creating",
  failure: null,
  focusNodeId: null,
});

const SETTLED_NODE_POSITIONS_HASH_PATTERN = /^[0-9a-f]{16}$/;

const RENDERER_REASON_COPY = Object.freeze({
  webgl_initialization_failed: "3D 그래프를 시작하지 못해 텍스트 보기로 전환했습니다.",
  webgl_unavailable: "WebGL을 사용할 수 없어 텍스트 보기로 전환했습니다.",
  webglcontextlost: "3D 그래프 연결이 중단되어 텍스트 보기로 전환했습니다.",
});

function cameraScaleText(state) {
  const scale = Number(state?.scale);
  const formatted = Number.isInteger(scale)
    ? String(scale)
    : scale.toFixed(2).replace(/0$/, "");
  const level = state?.level === "maximum"
    ? "최대"
    : state?.level === "detail"
      ? "상세"
      : "개요";
  return `${formatted}× · ${level}`;
}

function cameraPose(value) {
  const vector = (candidate) => {
    const x = Number(candidate?.x);
    const y = Number(candidate?.y);
    const z = Number(candidate?.z);
    return [x, y, z].every(Number.isFinite) ? { x, y, z } : null;
  };
  const position = vector(value?.position);
  const target = vector(value?.target);
  return position && target ? { position, target } : null;
}

function presentationForScene(view = {}) {
  return {
    activeProfileId: view.activeProfileId ?? null,
    selectedComponentId: view.selectedComponentId ?? null,
    selectedRelationNodeId: view.selectedRelationNodeId ?? null,
    selectedWorkflowId: view.selectedWorkflowId ?? null,
    hoveredNodeId: view.hoveredGraphNodeId ?? null,
    hoveredWorkflowStep: view.hoveredWorkflowStep ?? null,
    lockedWorkflowStep: view.lockedWorkflowStep ?? null,
    focusNodeId: view.graphPresentation?.focusNodeId ?? null,
  };
}

function presentationState(value, fallback = INITIAL_GRAPH_PRESENTATION) {
  const viewMode = value?.viewMode;
  const rendererLifecycle = value?.rendererLifecycle;
  if (!["three_d", "semantic"].includes(viewMode)
    || !["creating", "ready", "absent", "failed"].includes(rendererLifecycle)) {
    return { ...fallback };
  }
  return {
    viewMode,
    rendererLifecycle,
    failure: rendererLifecycle === "failed"
      ? String(value?.failure ?? "3D 그래프를 표시할 수 없습니다.")
      : null,
    focusNodeId: Object.hasOwn(value ?? {}, "focusNodeId")
      ? typeof value.focusNodeId === "string" && value.focusNodeId.length > 0
        ? value.focusNodeId
        : null
      : fallback.focusNodeId ?? null,
  };
}

function rendererState(value) {
  const availability = typeof value?.availability === "string"
    ? value.availability
    : "unavailable";
  const suppliedReason = typeof value?.reason === "string" && value.reason.length > 0
    ? value.reason
    : null;
  return {
    availability,
    reason: suppliedReason
      ? RENDERER_REASON_COPY[suppliedReason] ?? suppliedReason
      : availability === "ready"
        ? null
        : "3D 그래프를 표시할 수 없습니다.",
  };
}

function relationKind(node) {
  return String(node?.relation_kind ?? "").toLowerCase();
}

function relationForActiveProfile(nodes, profileId) {
  if (!profileId) return null;
  return nodes.find((node) => (
    node?.node_type === "relation"
    && (
      (relationKind(node) === "profile" && node.canonical_id === profileId)
      || (relationKind(node) === "unprofiled" && profileId === "__unprofiled__")
    )
  )) ?? null;
}

function identityNode(nodes, view) {
  const focused = nodes.find((node) => node.node_id === view?.graphPresentation?.focusNodeId);
  if (focused) return focused;
  const hovered = nodes.find((node) => node.node_id === view?.hoveredGraphNodeId);
  if (hovered) return hovered;
  const relation = nodes.find((node) => node.node_id === view?.selectedRelationNodeId);
  if (relation) return relation;
  const component = nodes.find((node) => (
    node.node_type === "component"
    && node.component_id === view?.selectedComponentId
  ));
  return component ?? relationForActiveProfile(nodes, view?.activeProfileId);
}

function identityPresentation(snapshot, node) {
  if (!node) return null;
  if (node.node_type === "relation") {
    const kind = relationKind(node) || "relation";
    const count = Math.max(0, Number(node.exact_count) || 0);
    return {
      title: node.name || node.canonical_id || node.node_id,
      kind: kind.toUpperCase(),
      count: `${count} ${kind === "workflow" ? (count === 1 ? "step" : "steps") : (count === 1 ? "component" : "components")}`,
    };
  }
  const component = snapshot?.components?.find(
    (candidate) => candidate.component_id === node.component_id,
  );
  const degree = Math.max(0, Number(node.relation_degree) || 0);
  return {
    title: component?.title || node.component_id || node.node_id,
    kind: String(node.kind || component?.kind || "unknown").toUpperCase(),
    count: `${degree} ${degree === 1 ? "relation" : "relations"}`,
  };
}

function actionForNode(node) {
  if (node?.node_type === "component" && node.component_id) {
    return { type: "select_graph_node", componentId: node.component_id };
  }
  if (node?.node_type === "relation" && node.node_id && node.canonical_id) {
    return {
      type: "select_graph_relation",
      nodeId: node.node_id,
      relationKind: relationKind(node),
      canonicalId: node.canonical_id,
    };
  }
  return null;
}

export function createComponentMapSession({
  snapshot,
  rendererFactory = createThreeGraphRenderer,
  resizeObserverFactory = typeof globalThis.ResizeObserver === "function"
    ? (callback) => new globalThis.ResizeObserver(callback)
    : null,
  intersectionObserverFactory = typeof globalThis.IntersectionObserver === "function"
    ? (callback) => new globalThis.IntersectionObserver(callback, {
      threshold: [0, 0.01],
    })
    : null,
  motionQuery = null,
  relationLabelOverlayFactory = bindRelationLabelOverlay,
  onAction = () => {},
  onRendererStateChange = () => {},
} = {}) {
  const projection = snapshot?.graph_projection;
  if (!projection || typeof projection !== "object") {
    throw new TypeError("snapshot graph_projection is required");
  }
  if (typeof rendererFactory !== "function") {
    throw new TypeError("rendererFactory must be a function");
  }

  const nodes = Array.isArray(projection.nodes) ? projection.nodes : [];
  const projectionIdentity = graphProjectionIdentity(projection);
  const spatialPolicy = graphSpatialPolicyFor(projection);
  let currentRoot = null;
  let currentView = {};
  let currentRendererState = { ...EMPTY_RENDERER_STATE };
  let currentCameraScale = { ...EMPTY_CAMERA_SCALE };
  let currentCameraPose = null;
  let currentSceneFitReport = null;
  let currentSceneLayoutIdentity = null;
  let currentResolvedAppearance = "dark";
  let graphPresentation = { ...INITIAL_GRAPH_PRESENTATION };
  let recoveryCapsule = null;
  let semanticBinding = null;
  let semanticRenderFingerprint = null;
  let semanticViewBinding = null;
  let cameraBindings = [];
  let resizeObserver = null;
  let intersectionObserver = null;
  let activityDocument = null;
  let activityDocumentVisibilityListener = null;
  let activityMotionQuery = null;
  let activityMotionChangeListener = null;
  let identityElements = null;
  let identityVisual = { opacity: 0, emphasis: 0 };
  let identityTransition = null;
  let currentPresentationFrame = null;
  let relationLabelBinding = null;
  let relationLabelFrame = null;
  let relationLabelGeometry = null;
  let relationLabelGeometryRevision = 0;
  let relationTransitionRequesting = false;
  let semanticPresentationRevision = 0;
  let rendererRetryPending = false;
  let focusSemanticAfterFallback = false;
  let focusRendererControlAfterReady = false;

  const semanticFingerprint = () => {
    const presentation = presentationForScene(currentView);
    return JSON.stringify([
      presentation.activeProfileId,
      presentation.selectedComponentId,
      presentation.selectedRelationNodeId,
      presentation.selectedWorkflowId,
      presentation.lockedWorkflowStep?.workflowId ?? null,
      presentation.lockedWorkflowStep?.ordinal ?? null,
      currentRendererState.availability,
      currentRendererState.reason,
      currentSceneFitReport,
      currentSceneLayoutIdentity,
      graphPresentation,
    ]);
  };

  const renderSemanticSurface = () => {
    if (!semanticBinding) return false;
    const fingerprint = semanticFingerprint();
    if (fingerprint === semanticRenderFingerprint) return false;
    semanticBinding.render();
    semanticRenderFingerprint = fingerprint;
    return true;
  };

  const identityElementsForCurrentRoot = () => {
    if (!currentRoot) return null;
    if (identityElements?.root === currentRoot) return identityElements;
    identityElements = {
      root: currentRoot,
      overlay: currentRoot.querySelector?.("[data-graph-identity-overlay]") ?? null,
      title: currentRoot.querySelector?.("[data-graph-identity-title]") ?? null,
      kind: currentRoot.querySelector?.("[data-graph-identity-kind]") ?? null,
      count: currentRoot.querySelector?.("[data-graph-identity-count]") ?? null,
    };
    return identityElements;
  };

  const normalizedProgress = (value) => (
    Math.min(1, Math.max(0, Number(value) || 0))
  );

  const formattedNumber = (value) => String(Number(Number(value).toFixed(6)));

  const setStyleProperty = (element, name, value) => {
    if (typeof element?.style?.setProperty === "function") {
      element.style.setProperty(name, value);
    } else if (element?.style) {
      element.style[name] = value;
    }
  };

  const applyIdentityVisual = (overlay, opacity, emphasis) => {
    identityVisual = {
      opacity: normalizedProgress(opacity),
      emphasis: normalizedProgress(emphasis),
    };
    setStyleProperty(
      overlay,
      "--graph-identity-opacity",
      formattedNumber(identityVisual.opacity),
    );
    setStyleProperty(
      overlay,
      "--graph-identity-emphasis",
      formattedNumber(identityVisual.emphasis),
    );
    setStyleProperty(
      overlay,
      "--graph-identity-emphasis-percent",
      `${formattedNumber(identityVisual.emphasis * 100)}%`,
    );
    setStyleProperty(
      overlay,
      "--graph-identity-background-percent",
      `${formattedNumber(identityVisual.emphasis * 8)}%`,
    );
  };

  const finishIdentityExit = (elements = identityElements) => {
    if (!elements?.overlay) return;
    applyIdentityVisual(elements.overlay, 0, 0);
    elements.overlay.hidden = true;
    elements.overlay.setAttribute?.("aria-hidden", "true");
    if (elements.overlay.dataset) {
      elements.overlay.dataset.identityPresence = "hidden";
      elements.overlay.dataset.identityState = "idle";
    } else {
      elements.overlay.setAttribute?.("data-identity-presence", "hidden");
      elements.overlay.setAttribute?.("data-identity-state", "idle");
    }
    if (elements.title) elements.title.textContent = "";
    if (elements.kind) elements.kind.textContent = "";
    if (elements.count) elements.count.textContent = "";
  };

  const resetIdentityOverlay = () => {
    if (identityElements) finishIdentityExit(identityElements);
    identityElements = null;
    identityVisual = { opacity: 0, emphasis: 0 };
    identityTransition = null;
    currentPresentationFrame = null;
  };

  const beginIdentityTransition = (transactionId) => {
    const node = identityNode(nodes, currentView);
    const identity = identityPresentation(snapshot, node);
    const elements = identityElementsForCurrentRoot();
    const { overlay, title, kind, count } = elements ?? {};
    if (!overlay) {
      identityTransition = null;
      return;
    }
    let targetEmphasis = 0;
    if (identity) {
      const focused = Boolean(
        graphPresentation.focusNodeId
        && node.node_id === graphPresentation.focusNodeId,
      );
      const selected = Boolean(
        (node.node_type === "relation" && node.node_id === currentView.selectedRelationNodeId)
        || (node.node_type === "component" && node.component_id === currentView.selectedComponentId)
      );
      const identityState = focused ? "focused" : selected ? "selected" : "idle";
      targetEmphasis = focused || selected ? 1 : 0;
      overlay.hidden = false;
      overlay.setAttribute?.("aria-hidden", "false");
      if (overlay.dataset) overlay.dataset.identityState = identityState;
      else overlay.setAttribute?.("data-identity-state", identityState);
      if (title) title.textContent = identity.title;
      if (kind) kind.textContent = identity.kind;
      if (count) count.textContent = identity.count;
    } else {
      overlay.setAttribute?.("aria-hidden", "true");
    }
    identityTransition = {
      transactionId,
      elements,
      targetPresent: Boolean(identity),
      fromOpacity: identityVisual.opacity,
      toOpacity: identity ? 1 : 0,
      fromEmphasis: identityVisual.emphasis,
      toEmphasis: targetEmphasis,
    };
  };

  const applyIdentityPresentationFrame = (frame) => {
    if (!currentRoot || frame?.transactionId == null) return;
    const transactionId = String(frame.transactionId);
    if (identityTransition?.transactionId !== transactionId) {
      beginIdentityTransition(transactionId);
    }
    const transition = identityTransition;
    if (!transition || transition.transactionId !== transactionId) return;
    const progress = normalizedProgress(frame.progress);
    const opacity = transition.fromOpacity
      + ((transition.toOpacity - transition.fromOpacity) * progress);
    const emphasis = transition.fromEmphasis
      + ((transition.toEmphasis - transition.fromEmphasis) * progress);
    const { overlay } = transition.elements;
    applyIdentityVisual(overlay, opacity, emphasis);
    if (transition.targetPresent) {
      overlay.hidden = false;
      overlay.setAttribute?.("aria-hidden", "false");
      if (overlay.dataset) {
        overlay.dataset.identityPresence = progress >= 1 ? "visible" : "entering";
      }
    } else if (progress >= 1) {
      finishIdentityExit(transition.elements);
    } else {
      overlay.setAttribute?.("aria-hidden", "true");
      if (overlay.dataset) overlay.dataset.identityPresence = "exiting";
    }
  };

  const syncRendererSurfaces = () => {
    if (!currentRoot) return;
    const scene = currentRoot.querySelector?.("[data-component-map-scene-host]");
    const semantic = currentRoot.querySelector?.("[data-component-map-semantic-host]");
    const status = currentRoot.querySelector?.("[data-graph-renderer-state]");
    const scale = currentRoot.querySelector?.("[data-graph-scale]");
    const semanticView = currentRoot.querySelector?.("[data-graph-semantic-view]");
    const fallbackActive = graphPresentation.viewMode === "semantic"
      || !["pending", "ready"].includes(currentRendererState.availability);
    if (scene) {
      scene.hidden = fallbackActive;
      scene.setAttribute?.("aria-hidden", "true");
    }
    if (semantic) {
      const pending = graphPresentation.viewMode === "three_d"
        && currentRendererState.availability === "pending";
      semantic.hidden = false;
      semantic.inert = pending;
      semantic.setAttribute?.("aria-hidden", String(pending));
      if (semantic.dataset) {
        semantic.dataset.rendererAvailability = currentRendererState.availability;
      } else {
        semantic.setAttribute?.(
          "data-renderer-availability",
          currentRendererState.availability,
        );
      }
    }
    if (status) {
      status.hidden = graphPresentation.rendererLifecycle !== "failed";
      status.textContent = graphPresentation.failure ?? "";
    }
    if (scale) {
      scale.textContent = currentRendererState.availability === "ready"
        ? cameraScaleText(currentCameraScale)
        : fallbackActive
          ? "3D · 목록"
          : "3D · 준비";
    }
    if (semanticView) {
      const rendererReady = graphPresentation.viewMode === "three_d"
        && currentRendererState.availability === "ready";
      semanticView.hidden = !rendererReady;
      semanticView.disabled = !rendererReady;
    }
    [...(currentRoot.querySelectorAll?.("[data-graph-zoom]") ?? [])].forEach((control) => {
      const action = control.dataset?.graphZoom;
      const atMinimum = currentCameraScale.scale <= currentCameraScale.minScale + 1e-6;
      const atMaximum = currentCameraScale.scale >= currentCameraScale.maxScale - 1e-6;
      control.disabled = currentRendererState.availability !== "ready"
        || (action === "out" && atMinimum)
        || (action === "in" && atMaximum);
    });
    renderSemanticSurface();
  };

  const focusSemanticSurface = () => {
    const semantic = currentRoot?.querySelector?.("[data-component-map-semantic-host]");
    const target = semantic?.querySelector?.("#component-map-semantic-view") ?? semantic;
    target?.setAttribute?.("tabindex", "-1");
    target?.focus?.({ preventScroll: true });
  };

  const focusRendererControl = () => {
    currentRoot?.querySelector?.("[data-graph-semantic-view]")
      ?.focus?.({ preventScroll: true });
  };

  const handleRendererState = (nextState) => {
    const nextRendererState = rendererState(nextState);
    if (rendererRetryPending && nextRendererState.availability === "pending") return;
    rendererRetryPending = false;
    currentRendererState = nextRendererState;
    let presentationAction = null;
    if (nextRendererState.availability === "ready") {
      graphPresentation = {
        ...graphPresentation,
        viewMode: "three_d",
        rendererLifecycle: "ready",
        failure: null,
      };
      recoveryCapsule = null;
      presentationAction = { type: "graph_renderer_ready" };
    } else if (nextRendererState.availability === "manual_fallback") {
      graphPresentation = {
        ...graphPresentation,
        viewMode: "semantic",
        rendererLifecycle: "absent",
        failure: null,
      };
      presentationAction = { type: "show_graph_semantic" };
    } else if (nextRendererState.availability === "pending") {
      graphPresentation = {
        ...graphPresentation,
        viewMode: "three_d",
        rendererLifecycle: "creating",
        failure: null,
      };
    } else {
      const captured = sceneController.captureRecoveryCapsule();
      if (captured?.projectionId === projectionIdentity) {
        recoveryCapsule = captured;
      }
      graphPresentation = {
        ...graphPresentation,
        viewMode: "semantic",
        rendererLifecycle: "failed",
        failure: nextRendererState.reason,
      };
      presentationAction = {
        type: "graph_renderer_failed",
        failure: nextRendererState.reason,
      };
    }
    if (presentationAction) onAction(presentationAction);
    syncRendererSurfaces();
    if (currentRendererState.availability === "ready") syncRelationLabelOverlay();
    else relationLabelBinding?.clear?.();
    if (focusSemanticAfterFallback && currentRendererState.availability !== "ready") {
      focusSemanticAfterFallback = false;
      focusSemanticSurface();
    }
    if (focusRendererControlAfterReady && currentRendererState.availability === "ready") {
      focusRendererControlAfterReady = false;
      focusRendererControl();
    } else if (focusRendererControlAfterReady
      && currentRendererState.availability !== "pending"
      && currentRendererState.availability !== "ready") {
      focusRendererControlAfterReady = false;
      focusSemanticSurface();
    }
    onRendererStateChange({ ...currentRendererState });
  };

  const handleCameraScaleChange = (nextState) => {
    const scale = Number(nextState?.scale);
    const minScale = Number(nextState?.minScale);
    const maxScale = Number(nextState?.maxScale);
    if (!Number.isFinite(scale) || !Number.isFinite(minScale) || !Number.isFinite(maxScale)
      || minScale <= 0 || maxScale < minScale) return;
    currentCameraScale = {
      scale: Math.min(maxScale, Math.max(minScale, scale)),
      minScale,
      maxScale,
      level: ["overview", "detail", "maximum"].includes(nextState?.level)
        ? nextState.level
        : "overview",
    };
    syncRendererSurfaces();
  };

  const handleSceneFitReportChange = (report) => {
    currentSceneFitReport = report && typeof report === "object" ? report : null;
    renderSemanticSurface();
  };

  const handleSceneLayoutIdentityChange = (identity) => {
    const projectionId = String(identity?.projectionId ?? "");
    const settledNodePositionsHash = String(identity?.settledNodePositionsHash ?? "");
    currentSceneLayoutIdentity = projectionId === projectionIdentity
      && SETTLED_NODE_POSITIONS_HASH_PATTERN.test(settledNodePositionsHash)
      ? Object.freeze({ projectionId, settledNodePositionsHash })
      : null;
    renderSemanticSurface();
  };

  const handleCameraPoseChange = (nextPose, frame = {}) => {
    const next = cameraPose(nextPose);
    if (!next) return;
    currentCameraPose = next;
    const progress = Number(frame?.progress);
    if (!Number.isFinite(progress) || progress >= 1) {
      semanticBinding?.syncCameraPose?.(currentCameraPose);
    }
  };

  const handleNodeHover = (nodeId) => {
    currentView = { ...currentView, hoveredGraphNodeId: nodeId ?? null };
    syncScenePresentation("semantic-hover");
    onAction({ type: "hover_graph_node", nodeId: nodeId ?? null });
  };

  const handleNodeSelect = (node) => {
    const action = actionForNode(node);
    if (!action) return;
    onAction(action);
    sceneController.focusNode(node.node_id, { intent: "contextual" });
  };

  const handleBackgroundSelect = () => onAction({ type: "clear_graph_selection" });

  const sceneController = createGraphSceneController({
    projection,
    createRenderer(metadata) {
      return rendererFactory({
        ...metadata,
        documentObject: currentRoot?.ownerDocument ?? globalThis.document,
        onBackgroundSelect: handleBackgroundSelect,
        onNodeHover: handleNodeHover,
        onNodeSelect: handleNodeSelect,
        onCameraPoseChange: handleCameraPoseChange,
        onCameraScaleChange: handleCameraScaleChange,
        onRendererStateChange: handleRendererState,
        onSceneFitReportChange: handleSceneFitReportChange,
        onSceneLayoutIdentityChange: handleSceneLayoutIdentityChange,
        onRelationLabelFrameChange: handleRelationLabelFrameChange,
        onPresentationFrameChange: handlePresentationFrameChange,
      });
    },
  });
  sceneController.setResolvedAppearance(currentResolvedAppearance);

  function syncScenePresentation(fallbackReason = "semantic-presentation") {
    const previousFrame = currentPresentationFrame;
    sceneController.syncPresentation(presentationForScene(currentView));
    if (currentRendererState.availability === "ready"
      || currentPresentationFrame !== previousFrame) return;
    semanticPresentationRevision += 1;
    handlePresentationFrameChange({
      transactionId: `semantic:${semanticPresentationRevision}`,
      progress: 1,
      durationMs: 0,
      reducedMotion: true,
      reason: fallbackReason,
    });
  }

  function retryRenderer() {
    rendererRetryPending = true;
    focusRendererControlAfterReady = true;
    if (recoveryCapsule
      && recoveryCapsule.projectionId !== projectionIdentity) {
      recoveryCapsule = null;
    }
    const result = sceneController.retry(recoveryCapsule);
    if (result === false) {
      rendererRetryPending = false;
      focusRendererControlAfterReady = false;
    }
    return result;
  }

  function returnToThreeD() {
    graphPresentation = {
      ...INITIAL_GRAPH_PRESENTATION,
      focusNodeId: graphPresentation.focusNodeId ?? null,
    };
    currentRendererState = { availability: "pending", reason: null };
    onAction({ type: "return_graph_three_d" });
    syncRendererSurfaces();
    return retryRenderer();
  }

  function releaseBindings() {
    resetIdentityOverlay();
    semanticBinding?.release?.();
    semanticBinding = null;
    semanticRenderFingerprint = null;
    if (semanticViewBinding) {
      semanticViewBinding.control.removeEventListener?.("click", semanticViewBinding.listener);
      semanticViewBinding = null;
    }
    cameraBindings.forEach(({ control, listener }) => control.removeEventListener?.("click", listener));
    cameraBindings = [];
    resizeObserver?.disconnect?.();
    resizeObserver = null;
    intersectionObserver?.disconnect?.();
    intersectionObserver = null;
    activityDocument?.removeEventListener?.(
      "visibilitychange",
      activityDocumentVisibilityListener,
    );
    activityDocument = null;
    activityDocumentVisibilityListener = null;
    if (activityMotionQuery && activityMotionChangeListener) {
      if (typeof activityMotionQuery.removeEventListener === "function") {
        activityMotionQuery.removeEventListener("change", activityMotionChangeListener);
      } else {
        activityMotionQuery.removeListener?.(activityMotionChangeListener);
      }
    }
    activityMotionQuery = null;
    activityMotionChangeListener = null;
    relationLabelBinding?.release?.();
    relationLabelBinding = null;
    relationLabelGeometry = null;
  }

  function projectRelationWorldToScreen(world, camera, safeInset) {
    if (!camera?.isCamera || !relationLabelGeometry) return null;
    const worldPosition = new Vector3(
      Number(world?.x) || 0,
      Number(world?.y) || 0,
      Number(world?.z) || 0,
    );
    const projected = worldPosition.clone().project(camera);
    if (![projected.x, projected.y, projected.z].every(Number.isFinite)) return null;
    const { width, height } = relationLabelGeometry;
    const x = ((projected.x + 1) / 2) * width;
    const y = ((1 - projected.y) / 2) * height;
    const worldRadius = Math.max(0, Number(world?.radius) || 0);
    const projectedEdge = worldPosition.clone().add(
      new Vector3(1, 0, 0).applyQuaternion(camera.quaternion).multiplyScalar(worldRadius),
    ).project(camera);
    const edgeX = ((projectedEdge.x + 1) / 2) * width;
    const edgeY = ((1 - projectedEdge.y) / 2) * height;
    const radiusPx = [edgeX, edgeY].every(Number.isFinite)
      ? Math.hypot(edgeX - x, edgeY - y)
      : 0;
    const left = Math.max(0, Number(safeInset?.left) || 0);
    const right = width - Math.max(0, Number(safeInset?.right) || 0);
    const top = Math.max(0, Number(safeInset?.top) || 0);
    const bottom = height - Math.max(0, Number(safeInset?.bottom) || 0);
    return {
      x,
      y,
      radiusPx,
      visible: projected.z >= -1
        && projected.z <= 1
        && x >= left
        && x <= right
        && y >= top
        && y <= bottom,
    };
  }

  function updateRelationLabelGeometry(sceneHost) {
    if (!currentRoot) return;
    const viewport = currentRoot.querySelector?.("[data-component-map-viewport]") ?? sceneHost;
    const viewportRect = viewport?.getBoundingClientRect?.() ?? {};
    const width = Number(viewportRect.width);
    const height = Number(viewportRect.height);
    const viewportX = Number(viewportRect.left ?? viewportRect.x ?? 0);
    const viewportY = Number(viewportRect.top ?? viewportRect.y ?? 0);
    if (![width, height, viewportX, viewportY].every(Number.isFinite)
      || width <= 0 || height <= 0) {
      relationLabelGeometry = null;
      relationLabelBinding?.clear?.();
      sceneController.setViewportGeometry(null);
      return;
    }
    const hudRect = currentRoot.querySelector?.("[data-component-map-hud]")
      ?.getBoundingClientRect?.() ?? {};
    relationLabelGeometryRevision += 1;
    relationLabelGeometry = Object.freeze({
      width,
      height,
      geometryRevision: relationLabelGeometryRevision,
      safeInset: createGraphSafeInset({
        geometryRevision: relationLabelGeometryRevision,
        viewportRect: { x: 0, y: 0, width, height },
        hudRect: {
          x: (Number(hudRect.left ?? hudRect.x) || 0) - viewportX,
          y: (Number(hudRect.top ?? hudRect.y) || 0) - viewportY,
          width: Math.max(0, Number(hudRect.width) || 0),
          height: Math.max(0, Number(hudRect.height) || 0),
        },
        relationLabelMargin: 12,
      }),
    });
    const identityOverlay = currentRoot.querySelector?.("[data-graph-identity-overlay]");
    if (identityOverlay?.style) {
      identityOverlay.style.insetBlockStart = `${relationLabelGeometry.safeInset.top}px`;
      identityOverlay.style.insetInlineStart = `${relationLabelGeometry.safeInset.left}px`;
    }
    sceneController.setViewportGeometry({
      viewportRect: Object.freeze({ x: 0, y: 0, width, height }),
      safeInset: relationLabelGeometry.safeInset,
    });
    syncRelationLabelOverlay();
  }

  function syncRelationLabelOverlay(presentationFrame = currentPresentationFrame) {
    if (!relationLabelBinding
      || !relationLabelFrame
      || !relationLabelGeometry
      || !presentationFrame) return;
    const result = relationLabelBinding.sync({
      ...relationLabelFrame,
      geometryRevision: relationLabelGeometry.geometryRevision,
      safeInset: relationLabelGeometry.safeInset,
      selectedId: currentView.selectedRelationNodeId ?? null,
      focusedId: currentView.graphPresentation?.focusNodeId ?? null,
      presentationFrame,
    });
    if (!result?.transitionRequired || relationTransitionRequesting) return;
    relationTransitionRequesting = true;
    try {
      sceneController.requestPresentationTransition("relation-label-collision");
    } finally {
      relationTransitionRequesting = false;
    }
  }

  function handlePresentationFrameChange(frame) {
    if (!frame || frame.transactionId == null) return;
    currentPresentationFrame = Object.freeze({
      ...frame,
      transactionId: String(frame.transactionId),
      progress: normalizedProgress(frame.progress),
      reducedMotion: Boolean(frame.reducedMotion),
    });
    applyIdentityPresentationFrame(currentPresentationFrame);
    syncRelationLabelOverlay(currentPresentationFrame);
  }

  function handleRelationLabelFrameChange(frame) {
    if (!frame || !Array.isArray(frame.relations) || !frame.camera) {
      relationLabelFrame = null;
      relationLabelBinding?.clear?.();
      return;
    }
    relationLabelFrame = frame;
    syncRelationLabelOverlay();
  }

  function bindRelationLabels(sceneHost) {
    const labelRoot = currentRoot?.querySelector?.("[data-graph-relation-label-root]");
    if (!labelRoot || typeof relationLabelOverlayFactory !== "function") return;
    relationLabelBinding = relationLabelOverlayFactory(labelRoot, {
      projectWorldToScreen: projectRelationWorldToScreen,
      spatialPolicy,
    });
    updateRelationLabelGeometry(sceneHost);
  }

  function bindActivitySources(sceneHost) {
    activityDocument = currentRoot?.ownerDocument ?? globalThis.document ?? null;
    const syncDocumentVisibility = () => {
      sceneController.setDocumentVisible(activityDocument?.visibilityState !== "hidden");
    };
    activityDocumentVisibilityListener = syncDocumentVisibility;
    syncDocumentVisibility();
    activityDocument?.addEventListener?.("visibilitychange", syncDocumentVisibility);

    activityMotionQuery = motionQuery
      ?? activityDocument?.defaultView?.matchMedia?.("(prefers-reduced-motion: reduce)")
      ?? null;
    const syncReducedMotion = (event) => {
      const reduced = typeof event?.matches === "boolean"
        ? event.matches
        : Boolean(activityMotionQuery?.matches);
      sceneController.setReducedMotion(reduced);
    };
    activityMotionChangeListener = syncReducedMotion;
    syncReducedMotion();
    if (typeof activityMotionQuery?.addEventListener === "function") {
      activityMotionQuery.addEventListener("change", syncReducedMotion);
    } else {
      activityMotionQuery?.addListener?.(syncReducedMotion);
    }

    if (typeof intersectionObserverFactory !== "function") {
      sceneController.setIntersecting(true);
      return;
    }
    intersectionObserver = intersectionObserverFactory((entries = []) => {
      const entry = [...entries].find((candidate) => candidate?.target === sceneHost);
      if (!entry) return;
      sceneController.setIntersecting(Boolean(
        entry.isIntersecting && Number(entry.intersectionRatio) > 0,
      ));
    });
    intersectionObserver?.observe?.(sceneHost);
  }

  function bindCameraControls() {
    cameraBindings = [...(currentRoot?.querySelectorAll?.("[data-graph-zoom]") ?? [])]
      .map((control) => {
        const listener = () => {
          if (control.dataset.graphZoom === "out") sceneController.zoomBy(1.2);
          if (control.dataset.graphZoom === "in") sceneController.zoomBy(0.8);
          if (control.dataset.graphZoom === "reset") sceneController.fitGraph();
        };
        control.addEventListener?.("click", listener);
        return { control, listener };
      });
  }

  function bindSemanticViewControl() {
    const control = currentRoot?.querySelector?.("[data-graph-semantic-view]");
    if (!control) return;
    const listener = () => {
      if (currentRendererState.availability !== "ready") return;
      const captured = sceneController.captureRecoveryCapsule();
      recoveryCapsule = captured?.projectionId === projectionIdentity
        ? captured
        : null;
      focusSemanticAfterFallback = true;
      if (sceneController.showSemanticFallback() === false) {
        focusSemanticAfterFallback = false;
      }
    };
    control.addEventListener?.("click", listener);
    semanticViewBinding = { control, listener };
  }

  function attach(root, view = {}) {
    if (!root?.querySelector) return false;
    releaseBindings();
    sceneController.detach();
    currentRoot = root;
    currentView = { ...view };
    graphPresentation = presentationState(view.graphPresentation, graphPresentation);
    const sceneHost = root.querySelector("[data-component-map-scene-host]");
    const semanticHost = root.querySelector("[data-component-map-semantic-host]");
    if (!sceneHost || !semanticHost) {
      currentRoot = null;
      return false;
    }
    semanticBinding = bindSemanticGraphFallback(semanticHost, {
      projection,
      componentCatalog: snapshot?.components,
      getPresentation: () => presentationForScene(currentView),
      getRendererState: () => ({
        ...currentRendererState,
        presentationMode: graphPresentation.rendererLifecycle === "absent" ? "manual" : "failure",
        cameraPose: currentCameraPose,
        fitReport: currentSceneFitReport,
        sceneLayoutIdentity: currentSceneLayoutIdentity,
      }),
      onBackgroundSelect: handleBackgroundSelect,
      onNodeHover: (nodeId) => handleNodeHover(
        nodeId,
        nodes.find((node) => node.node_id === nodeId) ?? null,
      ),
      onNodeSelect: handleNodeSelect,
      onRetry: retryRenderer,
      onReturn: returnToThreeD,
      onWorkflowStepHover: (step) => onAction({
        type: "hover_workflow_step",
        step,
      }),
      onWorkflowStepSelect: ({ workflowId, ordinal }) => onAction({
        type: "select_workflow_step",
        workflowId,
        ordinal,
      }),
    });
    semanticRenderFingerprint = semanticFingerprint();
    bindCameraControls();
    bindSemanticViewControl();
    bindActivitySources(sceneHost);
    bindRelationLabels(sceneHost);
    sceneController.setTarget(sceneHost);
    if (graphPresentation.viewMode === "three_d") {
      sceneController.attach(sceneHost);
    } else if (graphPresentation.rendererLifecycle === "failed") {
      currentRendererState = {
        availability: "unavailable",
        reason: graphPresentation.failure,
      };
    } else {
      currentRendererState = {
        availability: "manual_fallback",
        reason: "사용자가 텍스트 보기를 선택했습니다.",
      };
    }
    if (typeof resizeObserverFactory === "function") {
      resizeObserver = resizeObserverFactory(() => {
        updateRelationLabelGeometry(sceneHost);
        sceneController.resize();
      });
      resizeObserver?.observe?.(sceneHost);
      const hud = root.querySelector("[data-component-map-hud]");
      if (hud && hud !== sceneHost) resizeObserver?.observe?.(hud);
    }
    syncScenePresentation("semantic-attach");
    syncRendererSurfaces();
    return true;
  }

  function detach() {
    releaseBindings();
    sceneController.detach();
    currentRoot = null;
  }

  function syncPresentation(view = {}) {
    currentView = { ...view };
    graphPresentation = presentationState(view.graphPresentation, graphPresentation);
    syncScenePresentation("semantic-selection");
    renderSemanticSurface();
    syncRendererSurfaces();
  }

  function setExpanded(expanded) {
    sceneController.setExpanded(expanded);
  }

  function setResolvedAppearance(appearance) {
    const normalized = String(appearance ?? "").toLowerCase();
    if (!new Set(["light", "dark"]).has(normalized)) {
      throw new TypeError("resolved appearance must be light or dark");
    }
    if (currentResolvedAppearance === normalized) return false;
    currentResolvedAppearance = normalized;
    sceneController.setResolvedAppearance(normalized);
    return true;
  }

  function focusNode(nodeId, options = { intent: "explicit" }) {
    return sceneController.focusNode(nodeId, options);
  }

  function restoreCamera() {
    return sceneController.restoreCamera();
  }

  function dispose() {
    detach();
    sceneController.dispose();
    recoveryCapsule = null;
  }

  return Object.freeze({
    attach,
    detach,
    dispose,
    focusNode,
    refreshAfterLayout: () => {
      const sceneHost = currentRoot?.querySelector?.("[data-component-map-scene-host]");
      updateRelationLabelGeometry(sceneHost);
      sceneController.resize();
    },
    retry: retryRenderer,
    returnToThreeD,
    restoreCamera,
    setExpanded,
    setResolvedAppearance,
    syncPresentation,
  });
}
