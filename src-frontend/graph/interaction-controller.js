import { deriveGraphVisualEmphasis } from "./visual-emphasis.js";

const CAMERA_INTENTS = new Set(["contextual", "explicit", "restore"]);

function nodeId(node) {
  return typeof node?.node_id === "string" ? node.node_id : null;
}

function relationKind(node) {
  return String(node?.relation_kind ?? "").toLowerCase();
}

function relationForCanonicalId(nodes, relation, canonicalId) {
  if (!canonicalId) return null;
  return nodes.find((node) => (
    relationKind(node) === relation
    && node.canonical_id === canonicalId
  )) ?? null;
}

export function graphLinkEndpoints(link) {
  const source = typeof link?.source_node_id === "string" && link.source_node_id.length > 0
    ? link.source_node_id
    : null;
  const target = typeof link?.target_node_id === "string" && link.target_node_id.length > 0
    ? link.target_node_id
    : null;
  return [source, target];
}

function linkContainsOrdinal(link, ordinal) {
  return Array.isArray(link?.occurrences)
    && link.occurrences.some((occurrence) => Number(occurrence?.ordinal) === ordinal);
}

function workflowStepOrdinal(presentation, workflowId) {
  const locked = presentation.lockedWorkflowStep;
  if (locked?.workflowId === workflowId && Number.isInteger(Number(locked.ordinal))) {
    return Number(locked.ordinal);
  }

  const hovered = presentation.hoveredWorkflowStep;
  if (hovered?.workflowId === workflowId && Number.isInteger(Number(hovered.ordinal))) {
    return Number(hovered.ordinal);
  }

  return null;
}

export function deriveGraphInteractionModel(projection, presentation = {}) {
  const nodes = Array.isArray(projection?.nodes) ? projection.nodes : [];
  const links = Array.isArray(projection?.links) ? projection.links : [];
  const nodeById = new Map(nodes.map((node) => [nodeId(node), node]));
  const activeNodeIds = new Set();
  const activeLinkIds = new Set();
  const selectedNodeIds = new Set();
  let workflowNodeId = null;
  let activeOrdinal = null;

  const hoveredWorkflowNode = nodes.find((node) => (
    nodeId(node) === presentation.hoveredNodeId && relationKind(node) === "workflow"
  )) ?? null;
  const selectedWorkflowId = presentation.selectedWorkflowId ?? null;
  const effectiveWorkflowId = selectedWorkflowId ?? hoveredWorkflowNode?.canonical_id ?? null;
  if (effectiveWorkflowId) {
    const workflowNode = relationForCanonicalId(nodes, "workflow", effectiveWorkflowId);
    workflowNodeId = nodeId(workflowNode);
    if (workflowNodeId) {
      activeNodeIds.add(workflowNodeId);
      selectedNodeIds.add(workflowNodeId);
    }

    activeOrdinal = workflowStepOrdinal(presentation, effectiveWorkflowId);

    links.forEach((link) => {
      if (link.semantic !== "workflow-step" || link.workflow_node_id !== workflowNodeId) return;
      if (activeOrdinal !== null && !linkContainsOrdinal(link, activeOrdinal)) return;
      activeLinkIds.add(link.link_id);
      activeNodeIds.add(link.component_node_id);
      if (activeOrdinal !== null) selectedNodeIds.add(link.component_node_id);
    });
  } else {
    const selectedRelationId = presentation.selectedRelationNodeId ?? null;
    const profileNode = relationForCanonicalId(nodes, "profile", presentation.activeProfileId);
    const relationNodeId = selectedRelationId ?? nodeId(profileNode);
    if (relationNodeId && nodeById.has(relationNodeId)) {
      activeNodeIds.add(relationNodeId);
      selectedNodeIds.add(relationNodeId);
      links.forEach((link) => {
        const [source, target] = graphLinkEndpoints(link);
        if (source !== relationNodeId && target !== relationNodeId) return;
        activeLinkIds.add(link.link_id);
        if (source) activeNodeIds.add(source);
        if (target) activeNodeIds.add(target);
      });
    }
  }

  const selectedComponentNodeId = presentation.selectedComponentId
    ? `component:${presentation.selectedComponentId}`
    : null;
  if (selectedComponentNodeId && nodeById.has(selectedComponentNodeId)) {
    activeNodeIds.add(selectedComponentNodeId);
    selectedNodeIds.add(selectedComponentNodeId);
    links.forEach((link) => {
      if (link.semantic !== "profile-membership"
        || link.component_node_id !== selectedComponentNodeId) return;
      activeLinkIds.add(link.link_id);
      if (nodeById.has(link.profile_node_id)) activeNodeIds.add(link.profile_node_id);
    });
  }
  if (presentation.hoveredNodeId && nodeById.has(presentation.hoveredNodeId)) {
    activeNodeIds.add(presentation.hoveredNodeId);
    selectedNodeIds.add(presentation.hoveredNodeId);
  }

  const visualEmphasis = deriveGraphVisualEmphasis({
    links,
    nodes,
    selectedNodeIds,
    focusNodeId: presentation.focusNodeId ?? null,
  });

  return {
    activeLinkIds,
    activeNodeIds,
    activeOrdinal,
    emphasisByNodeId: visualEmphasis.emphasisByNodeId,
    focusNodeId: visualEmphasis.focusNodeId,
    hasFocus: activeNodeIds.size > 0,
    selectedOneHopLinkIds: visualEmphasis.selectedOneHopLinkIds,
    workflowNodeId,
  };
}

export function createGraphInteractionController({
  graph,
  nodeObjects = null,
  linkObjects = null,
  onNodeHover = () => {},
  onNodeSelect = () => {},
  onBackgroundSelect = () => {},
  onCameraTransition = () => {},
  onCameraFrame = () => {},
  reducedMotion = false,
  resolveFramePose,
  clock = globalThis.performance ?? { now: () => Date.now() },
  frameScheduler = {
    requestAnimationFrame: (callback) => globalThis.requestAnimationFrame?.(callback)
      ?? globalThis.setTimeout?.(() => callback(clock?.now?.() ?? Date.now()), 16),
    cancelAnimationFrame: (token) => {
      if (globalThis.cancelAnimationFrame) globalThis.cancelAnimationFrame(token);
      else globalThis.clearTimeout?.(token);
    },
  },
} = {}) {
  if (!graph || !["function", "object"].includes(typeof graph)) {
    throw new TypeError("graph is required");
  }
  if (typeof resolveFramePose !== "function") {
    throw new TypeError("resolveFramePose is required");
  }

  const cameraVector = (value) => {
    const x = Number(value?.x);
    const y = Number(value?.y);
    const z = Number(value?.z);
    return [x, y, z].every(Number.isFinite) ? { x, y, z } : null;
  };

  let previousCamera = null;
  let focusKey = null;
  let reducedMotionEnabled = Boolean(reducedMotion);
  let focusIntent = null;
  let transitionGeneration = 0;
  let activeTransition = null;
  let availabilityActive = true;
  let terminal = false;
  let resolvedAppearance = "dark";

  const now = () => Number(clock?.now?.()) || 0;

  const diagnostics = () => ({
    cameraRafRunning: activeTransition?.frameToken === null
      || activeTransition?.frameToken === undefined
      ? 0
      : 1,
    generation: transitionGeneration,
    pendingCameraTransactionCount: activeTransition ? 1 : 0,
  });

  const interpolateVector = (from, to, progress) => ({
    x: from.x + ((to.x - from.x) * progress),
    y: from.y + ((to.y - from.y) * progress),
    z: from.z + ((to.z - from.z) * progress),
  });

  const easeInOutCubic = (progress) => (
    progress < 0.5
      ? 4 * progress * progress * progress
      : 1 - (Math.pow(-2 * progress + 2, 3) / 2)
  );

  const exportState = () => ({
    previousCamera: previousCamera
      ? {
        position: { ...previousCamera.position },
        target: previousCamera.target ? { ...previousCamera.target } : null,
      }
      : null,
    focusKey,
    focusIntent,
  });

  const importState = (state) => {
    if (!state || typeof state !== "object") return false;
    const previousPosition = cameraVector(state.previousCamera?.position);
    const previousTarget = state.previousCamera?.target == null
      ? null
      : cameraVector(state.previousCamera.target);
    if (state.previousCamera && (!previousPosition
      || (state.previousCamera.target != null && !previousTarget))) {
      return false;
    }
    cancelCameraTransition();
    previousCamera = previousPosition
      ? {
        position: previousPosition,
        target: previousTarget,
      }
      : null;
    focusKey = typeof state.focusKey === "string" ? state.focusKey : null;
    focusIntent = ["contextual", "explicit"].includes(state.focusIntent)
      ? state.focusIntent
      : null;
    return true;
  };

  const captureCamera = () => {
    const position = cameraVector(graph.cameraPosition?.());
    if (!position) return null;
    return {
      position,
      target: cameraVector(graph.controls?.()?.target),
    };
  };

  const publishCameraFrame = (pose, progress = 1) => {
    onCameraFrame({
      position: { ...pose.position },
      target: pose.target ? { ...pose.target } : null,
    }, progress);
  };

  const applyCameraPose = (pose, progress = 1) => {
    graph.cameraPosition?.(pose.position, pose.target ?? undefined, 0);
    publishCameraFrame(pose, progress);
  };

  const cancelCameraTransition = ({
    notifyAbort = true,
    publishTerminalFrame = true,
  } = {}) => {
    transitionGeneration += 1;
    const transition = activeTransition;
    if (transition?.frameToken !== null
      && transition?.frameToken !== undefined) {
      frameScheduler.cancelAnimationFrame?.(transition.frameToken);
    }
    activeTransition = null;
    if (!transition) return false;
    if (publishTerminalFrame) {
      const currentPose = captureCamera();
      if (currentPose) publishCameraFrame(currentPose, 1);
    }
    if (notifyAbort) transition.onAbort?.();
    return true;
  };

  const startCameraTransition = (targetPose, {
    duration = 250,
    onAbort = () => {},
    onComplete = () => {},
  } = {}) => {
    if (terminal || !availabilityActive) return false;
    cancelCameraTransition();
    const fromPose = captureCamera();
    if (!fromPose) return false;
    const safeDuration = reducedMotionEnabled || !availabilityActive
      ? 0
      : Math.max(0, Number(duration) || 0);
    onCameraTransition(safeDuration, targetPose);
    if (safeDuration === 0) {
      applyCameraPose(targetPose, 1);
      onComplete();
      return true;
    }

    const token = transitionGeneration;
    const startedAt = now();
    const transition = {
      frameToken: null,
      fromPose,
      onAbort,
      onComplete,
      targetPose,
      token,
    };
    const tick = () => {
      if (token !== transitionGeneration || activeTransition !== transition) return;
      const elapsed = Math.max(0, now() - startedAt);
      const progress = Math.min(1, elapsed / safeDuration);
      const eased = easeInOutCubic(progress);
      const position = interpolateVector(fromPose.position, targetPose.position, eased);
      const fromTarget = fromPose.target ?? targetPose.target;
      const target = fromTarget && targetPose.target
        ? interpolateVector(fromTarget, targetPose.target, eased)
        : targetPose.target;
      applyCameraPose({ position, target: target ?? null }, progress);
      if (progress >= 1) {
        activeTransition = null;
        onComplete();
        return;
      }
      transition.frameToken = frameScheduler.requestAnimationFrame?.(tick) ?? null;
    };
    activeTransition = transition;
    transition.frameToken = frameScheduler.requestAnimationFrame?.(tick) ?? null;
    return true;
  };

  const finishActiveTransition = () => {
    if (!activeTransition) return false;
    const transition = activeTransition;
    cancelCameraTransition({ notifyAbort: false, publishTerminalFrame: false });
    applyCameraPose(transition.targetPose, 1);
    transition.onComplete?.();
    return true;
  };

  const transitionCamera = (targetPose, {
    duration = 250,
    focusKey: nextFocusKey = null,
    intent = "explicit",
    onAbort = () => {},
    onComplete = () => {},
  } = {}) => {
    if (terminal || !availabilityActive) return false;
    const position = cameraVector(targetPose?.position);
    const target = targetPose?.target == null ? null : cameraVector(targetPose.target);
    if (!position || (targetPose?.target != null && !target)) return false;
    if (!CAMERA_INTENTS.has(intent)) {
      throw new TypeError("camera intent must be contextual, explicit, or restore");
    }

    if (intent !== "restore") {
      if (focusIntent !== intent || !previousCamera) previousCamera = captureCamera();
      focusIntent = intent;
      focusKey = `${intent}:${nextFocusKey ?? "camera"}`;
    }

    return startCameraTransition({ position, target }, { duration, onAbort, onComplete });
  };

  const restoreCamera = () => {
    if (!previousCamera) return false;
    const restorePose = previousCamera;
    return transitionCamera(restorePose, {
      duration: 250,
      intent: "restore",
      onComplete() {
        previousCamera = null;
        focusKey = null;
        focusIntent = null;
      },
    });
  };

  graph
    .onNodeHover?.((node) => onNodeHover(nodeId(node), node ?? null))
    ?.onNodeClick?.((node) => onNodeSelect(node))
    ?.onBackgroundClick?.(() => {
      restoreCamera();
      onBackgroundSelect();
    });

  function syncPresentation(presentation, projection) {
    const transition = preparePresentationTransition(presentation, projection);
    transition.apply(1);
    return transition.model;
  }

  function preparePresentationTransition(presentation, projection) {
    const model = deriveGraphInteractionModel(projection, presentation);
    const appearance = Object.freeze({ appearance: resolvedAppearance });
    const nodeTransition = nodeObjects?.createPresentationTransition?.(
      model,
      presentation,
      appearance,
    );
    const linkTransition = linkObjects?.createPresentationTransition?.(
      model,
      presentation,
      appearance,
    );
    return Object.freeze({
      model,
      apply(progress) {
        const normalized = Math.min(1, Math.max(0, Number(progress) || 0));
        if (typeof nodeTransition === "function") nodeTransition(normalized);
        else nodeObjects?.syncAll?.(model, presentation);
        if (typeof linkTransition === "function") linkTransition(normalized);
        else linkObjects?.syncAll?.(model, presentation);
      },
    });
  }

  function setReducedMotion(nextReducedMotion) {
    reducedMotionEnabled = Boolean(nextReducedMotion);
    if (!reducedMotionEnabled || !activeTransition) return;
    finishActiveTransition();
  }

  function setResolvedAppearance(appearance) {
    if (terminal) return false;
    const normalized = String(appearance ?? "").toLowerCase();
    if (!new Set(["light", "dark"]).has(normalized)) {
      throw new TypeError("resolved appearance must be light or dark");
    }
    if (resolvedAppearance === normalized) return false;
    resolvedAppearance = normalized;
    return true;
  }

  function syncAvailability(next = {}) {
    if (terminal) return diagnostics();
    if (Boolean(next.terminal)) {
      terminal = true;
      availabilityActive = false;
      resetCameraContext();
      return diagnostics();
    }
    if (Object.hasOwn(next, "active")) {
      availabilityActive = Boolean(next.active);
      if (!availabilityActive && activeTransition) cancelCameraTransition();
    }
    if (Object.hasOwn(next, "reducedMotion")) {
      setReducedMotion(Boolean(next.reducedMotion));
    }
    return diagnostics();
  }

  function focusNodes(targetNodes, explicitFocusKey = null, { intent = "explicit" } = {}) {
    if (terminal || !availabilityActive) return false;
    const nodes = Array.isArray(targetNodes) ? targetNodes.filter(Boolean) : [];
    const frame = resolveFramePose(nodes);
    if (!frame) return false;
    return transitionCamera(frame, {
      duration: intent === "contextual" ? 250 : 420,
      focusKey: explicitFocusKey ?? nodeId(nodes[0]) ?? "nodes",
      intent,
    });
  }

  function focusNode(targetNode, options) {
    return focusNodes([targetNode], nodeId(targetNode), options);
  }

  function resetCameraContext() {
    cancelCameraTransition();
    previousCamera = null;
    focusKey = null;
    focusIntent = null;
  }

  function release() {
    if (terminal) return diagnostics();
    return syncAvailability({ active: false, terminal: true });
  }

  return {
    diagnostics,
    exportState,
    focusNode,
    focusNodes,
    importState,
    preparePresentationTransition,
    release,
    resetCameraContext,
    restoreCamera,
    setReducedMotion,
    setResolvedAppearance,
    syncAvailability,
    syncPresentation,
    transitionCamera,
  };
}
