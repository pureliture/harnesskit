import {
  graphProjectionIdentity,
  graphSpatialPolicyFor,
} from "./spatial-policy.js";

function cloneJsonValue(value) {
  if (value === undefined) return undefined;
  return JSON.parse(JSON.stringify(value));
}

function measuredSize(target) {
  const bounds = target?.getBoundingClientRect?.();
  return {
    width: Math.max(0, Number(bounds?.width) || 0),
    height: Math.max(0, Number(bounds?.height) || 0),
  };
}

function call(renderer, method, ...args) {
  return renderer?.[method]?.(...args);
}

/**
 * Owns one mutable renderer scene for one immutable backend projection.
 *
 * The renderer is intentionally long-lived across disclosure collapse and
 * reattachment. Force simulation is settled exactly once; interaction only
 * changes presentation state after that point.
 */
export function createGraphSceneController({ projection, createRenderer }) {
  if (!projection || typeof projection !== "object") {
    throw new TypeError("projection must be an object");
  }
  if (typeof createRenderer !== "function") {
    throw new TypeError("createRenderer must be a function");
  }

  const sceneGraph = cloneJsonValue(projection);
  const spatialPolicy = graphSpatialPolicyFor(projection);
  let renderer = null;
  let target = null;
  let attached = false;
  let expanded = true;
  let activityState = {
    expanded: true,
    foreground: true,
    intersecting: true,
    reducedMotion: false,
  };
  let settled = false;
  let initialFitApplied = false;
  let viewportGeometry = null;
  let resolvedAppearance = "dark";
  let currentPresentation = {};
  let hasPresentation = false;
  let disposed = false;

  function ensureRenderer() {
    if (!renderer) {
      renderer = createRenderer({
        projectionId: graphProjectionIdentity(projection) || null,
        snapshotId: projection.snapshot_id ?? null,
        spatialPolicy,
      });
      if (!renderer || typeof renderer !== "object") {
        throw new TypeError("createRenderer must return a renderer context");
      }
      call(renderer, "setResolvedAppearance", resolvedAppearance);
      if (hasPresentation) {
        call(renderer, "syncPresentation", cloneJsonValue(currentPresentation));
      }
      call(renderer, "setActivityState", activityState);
      call(renderer, "setViewportGeometry", viewportGeometry);
    }
    return renderer;
  }

  function settleOnce(context) {
    if (settled) return;

    call(context, "setGraphData", sceneGraph);
    call(context, "settle", spatialPolicy.settle.ticks);
    call(context, "pauseAnimation");
    settled = true;
  }

  function applyInitialFit(context) {
    if (initialFitApplied || !target) return;
    const { width, height } = measuredSize(target);
    if (width <= 0 || height <= 0) return;

    const result = call(context, "fitGraph");
    if (result !== false) initialFitApplied = true;
  }

  function attach(nextTarget) {
    if (disposed) return;
    if (nextTarget) target = nextTarget;
    if (!target || !expanded) return;

    const context = ensureRenderer();
    if (!attached) {
      call(context, "attach", target);
      attached = true;
    }
    settleOnce(context);
    const { width, height } = measuredSize(target);
    call(context, "resize", width, height);
    applyInitialFit(context);
  }

  function setTarget(nextTarget) {
    if (disposed || !nextTarget) return false;
    target = nextTarget;
    return true;
  }

  function detach() {
    if (disposed || !renderer || !attached) return;
    call(renderer, "detach");
    attached = false;
  }

  function resize() {
    if (disposed || !renderer || !attached || !target) return;
    const { width, height } = measuredSize(target);
    call(renderer, "resize", width, height);
    applyInitialFit(renderer);
  }

  function syncPresentation(presentation) {
    if (disposed) return;
    currentPresentation = cloneJsonValue(presentation) ?? {};
    hasPresentation = true;
    if (!renderer) return;
    call(renderer, "syncPresentation", cloneJsonValue(currentPresentation));
  }

  function requestPresentationTransition(reason = "dom-presentation") {
    if (disposed || !renderer || !attached) return false;
    return call(renderer, "requestPresentationTransition", reason) ?? false;
  }

  function setResolvedAppearance(appearance) {
    if (disposed) return false;
    const normalized = String(appearance ?? "").toLowerCase();
    if (!new Set(["light", "dark"]).has(normalized)) {
      throw new TypeError("resolved appearance must be light or dark");
    }
    if (resolvedAppearance === normalized) return false;
    resolvedAppearance = normalized;
    call(renderer, "setResolvedAppearance", normalized);
    return true;
  }

  function setViewportGeometry(geometry) {
    if (disposed) return;
    viewportGeometry = cloneJsonValue(geometry);
    call(renderer, "setViewportGeometry", viewportGeometry);
  }

  function fitGraph() {
    if (disposed || !renderer || !attached) return;
    call(renderer, "fitGraph");
  }

  function focusNode(nodeId, options = {}) {
    if (disposed || !renderer || !attached || typeof nodeId !== "string" || !nodeId) return;
    return call(renderer, "focusNode", nodeId, options);
  }

  function restoreCamera() {
    if (disposed || !renderer || !attached) return false;
    return call(renderer, "restoreCamera") ?? false;
  }

  function captureRecoveryCapsule() {
    if (disposed || !renderer) return null;
    return call(renderer, "captureRecoveryCapsule") ?? null;
  }

  function retry(recoveryCapsule = null) {
    if (disposed || !target) return false;
    if (!renderer || !attached) {
      const context = ensureRenderer();
      if (recoveryCapsule && call(context, "restoreRecoveryCapsule", recoveryCapsule) === false) {
        return false;
      }
      attach(target);
      return attached;
    }
    if (recoveryCapsule && call(renderer, "restoreRecoveryCapsule", recoveryCapsule) === false) {
      return false;
    }
    const result = call(renderer, "retry");
    if (result !== false) applyInitialFit(renderer);
    return result;
  }

  function showSemanticFallback() {
    if (disposed || !renderer || !attached) return false;
    return call(renderer, "showSemanticFallback") ?? false;
  }

  function zoomBy(factor) {
    if (disposed || !renderer || !attached || !Number.isFinite(Number(factor))) return;
    call(renderer, "zoomBy", Number(factor));
  }

  function setExpanded(nextExpanded) {
    if (disposed) return;
    const shouldExpand = Boolean(nextExpanded);
    if (expanded === shouldExpand) return;

    expanded = shouldExpand;
    activityState = { ...activityState, expanded };
    call(renderer, "setActivityState", activityState);
    if (!expanded) {
      detach();
      return;
    }
    attach(target);
  }

  function setIntersecting(visible) {
    activityState = { ...activityState, intersecting: Boolean(visible) };
    call(renderer, "setActivityState", activityState);
  }

  function setDocumentVisible(visible) {
    activityState = { ...activityState, foreground: Boolean(visible) };
    call(renderer, "setActivityState", activityState);
  }

  function setReducedMotion(reduced) {
    activityState = { ...activityState, reducedMotion: Boolean(reduced) };
    call(renderer, "setActivityState", activityState);
  }

  function dispose() {
    if (disposed) return;
    if (renderer && attached) {
      call(renderer, "detach");
      attached = false;
    }
    call(renderer, "dispose");
    renderer = null;
    target = null;
    disposed = true;
  }

  return {
    attach,
    captureRecoveryCapsule,
    detach,
    dispose,
    fitGraph,
    focusNode,
    resize,
    requestPresentationTransition,
    restoreCamera,
    retry,
    showSemanticFallback,
    setDocumentVisible,
    setExpanded,
    setIntersecting,
    setReducedMotion,
    setResolvedAppearance,
    setTarget,
    setViewportGeometry,
    syncPresentation,
    zoomBy,
  };
}
