import { createGraphInteractionController } from "./interaction-controller.js";
import { createGraphActivityController } from "./activity-controller.js";
import { createGraphLinkObjectFactory } from "./link-object.js";
import { createGraphNodeObjectFactory } from "./node-object.js";
import {
  prepareSphericalScene,
  settledNodePositionsHash as hashSettledNodePositions,
} from "./spherical-layout.js";
import {
  assertGraphSpatialPolicyProjection,
  requireGraphSpatialPolicy,
} from "./spatial-policy.js";
import { graphNodeLayoutEnvelope, graphNodeVisualEnvelope } from "./visual-metrics.js";
import { createSphericalSceneFitReport } from "./viewport-geometry.js";
import { Vector3 } from "three";

let BrowserForceGraph3D = null;
if (typeof window !== "undefined") {
  BrowserForceGraph3D = (await import("3d-force-graph")).default;
}

export const MIN_CAMERA_SCALE = 0.75;
export const MAX_CAMERA_SCALE = 8;

const SETTLED_NODE_POSITIONS_HASH_PATTERN = /^[0-9a-f]{16}$/;

export function prepareSceneGraph(
  sceneGraph,
  spatialPolicy,
) {
  return prepareSphericalScene(sceneGraph, {
    spatialPolicy: requireGraphSpatialPolicy(spatialPolicy),
  }).sceneGraph;
}

export function stabilizeSettledSceneGraph(sceneGraph) {
  const nodes = Array.isArray(sceneGraph?.nodes) ? sceneGraph.nodes : [];
  nodes.forEach((node) => {
    for (const axis of ["x", "y", "z"]) {
      const velocityAxis = `v${axis}`;
      if (Number.isFinite(Number(node?.[velocityAxis]))) node[velocityAxis] = 0;
    }
  });
  return sceneGraph;
}

export function canUseWebGL(documentObject = globalThis.document) {
  try {
    const canvas = documentObject?.createElement?.("canvas");
    return Boolean(
      canvas?.getContext?.("webgl2", { failIfMajorPerformanceCaveat: true })
      || canvas?.getContext?.("webgl", { failIfMajorPerformanceCaveat: true }),
    );
  } catch {
    return false;
  }
}

function collisionRadius(node, spatialPolicy) {
  const envelope = graphNodeLayoutEnvelope(node, spatialPolicy);
  return Math.max(0, (envelope.max.x - envelope.min.x) / 2);
}

export function createBoundedCollisionForce({ spatialPolicy } = {}) {
  requireGraphSpatialPolicy(spatialPolicy);
  let forceNodes = [];
  function force(alpha = 1) {
    for (let leftIndex = 0; leftIndex < forceNodes.length; leftIndex += 1) {
      const left = forceNodes[leftIndex];
      for (let rightIndex = leftIndex + 1; rightIndex < forceNodes.length; rightIndex += 1) {
        const right = forceNodes[rightIndex];
        let dx = (Number(right.x) || 0) - (Number(left.x) || 0);
        let dy = (Number(right.y) || 0) - (Number(left.y) || 0);
        let dz = (Number(right.z) || 0) - (Number(left.z) || 0);
        let distanceSquared = dx * dx + dy * dy + dz * dz;
        if (distanceSquared === 0) {
          dx = 0.001;
          dy = 0.001;
          dz = 0.001;
          distanceSquared = 0.000003;
        }
        const minimum = collisionRadius(left, spatialPolicy)
          + collisionRadius(right, spatialPolicy)
          + spatialPolicy.collision.gap;
        if (distanceSquared >= minimum * minimum) continue;
        const distance = Math.sqrt(distanceSquared);
        const impulse = ((minimum - distance) / distance)
          * spatialPolicy.collision.strength
          * alpha;
        const x = dx * impulse;
        const y = dy * impulse;
        const z = dz * impulse;
        if (left.fx == null) {
          left.vx = (Number(left.vx) || 0) - x;
          left.vy = (Number(left.vy) || 0) - y;
          left.vz = (Number(left.vz) || 0) - z;
        }
        if (right.fx == null) {
          right.vx = (Number(right.vx) || 0) + x;
          right.vy = (Number(right.vy) || 0) + y;
          right.vz = (Number(right.vz) || 0) + z;
        }
      }
    }
  }
  force.initialize = (nodes) => { forceNodes = Array.isArray(nodes) ? nodes : []; };
  return force;
}

function envelopeCorners(envelope) {
  const corners = [];
  for (const x of [envelope.min.x, envelope.max.x]) {
    for (const y of [envelope.min.y, envelope.max.y]) {
      for (const z of [envelope.min.z, envelope.max.z]) corners.push({ x, y, z });
    }
  }
  return corners;
}

function finiteProjection(point, camera) {
  const projected = new Vector3(point.x, point.y, point.z).project(camera);
  return [projected.x, projected.y, projected.z].every(Number.isFinite)
    ? projected
    : null;
}

function fitViewportGeometry(value) {
  const viewportRect = value?.viewportRect;
  const x = Number(viewportRect?.x ?? 0);
  const y = Number(viewportRect?.y ?? 0);
  const width = Number(viewportRect?.width ?? 0);
  const height = Number(viewportRect?.height ?? 0);
  const top = Number(value?.safeInset?.top ?? 0);
  const right = Number(value?.safeInset?.right ?? 0);
  const bottom = Number(value?.safeInset?.bottom ?? 0);
  const left = Number(value?.safeInset?.left ?? 0);
  if (![x, y, width, height, top, right, bottom, left].every(Number.isFinite)
    || width <= 0 || height <= 0) return null;
  return {
    viewportRect: {
      x,
      y,
      width,
      height,
    },
    safeInset: {
      geometryRevision: Number(value?.safeInset?.geometryRevision) || 0,
      top: Math.max(0, top),
      right: Math.max(0, right),
      bottom: Math.max(0, bottom),
      left: Math.max(0, left),
    },
  };
}

export function createSceneFitReport(
  sceneGraph,
  camera,
  viewportGeometry = null,
  spatialPolicy,
) {
  requireGraphSpatialPolicy(spatialPolicy);
  const nodes = Array.isArray(sceneGraph?.nodes) ? sceneGraph.nodes : [];
  const envelopes = nodes.map((node) => graphNodeVisualEnvelope(node, spatialPolicy));
  const calculatedBounds = envelopes.length > 0
    ? envelopes.reduce((bounds, envelope) => ({
      min: {
        x: Math.min(bounds.min.x, envelope.min.x),
        y: Math.min(bounds.min.y, envelope.min.y),
        z: Math.min(bounds.min.z, envelope.min.z),
      },
      max: {
        x: Math.max(bounds.max.x, envelope.max.x),
        y: Math.max(bounds.max.y, envelope.max.y),
        z: Math.max(bounds.max.z, envelope.max.z),
      },
    }), {
      min: { x: Infinity, y: Infinity, z: Infinity },
      max: { x: -Infinity, y: -Infinity, z: -Infinity },
    })
    : null;
  const sceneBounds = calculatedBounds
    ? Object.freeze({
      min: Object.freeze({ ...calculatedBounds.min }),
      max: Object.freeze({ ...calculatedBounds.max }),
    })
    : null;
  const base = {
    identityCount: nodes.length,
    relationCount: nodes.filter((node) => node?.node_type === "relation").length,
    componentCount: nodes.filter((node) => node?.node_type === "component").length,
    envelopeCount: envelopes.length,
    sceneBounds,
  };
  if (!camera?.isCamera) {
    return Object.freeze({
      ...base,
      camera: Object.freeze({
        status: "unavailable",
        inFrustumEnvelopeCount: null,
        totalEnvelopeCount: envelopes.length,
        largestDimensionOccupancy: null,
      }),
    });
  }
  camera.updateProjectionMatrix?.();
  camera.updateMatrixWorld?.();
  const projectedEnvelopes = envelopes.map((envelope) => (
    envelopeCorners(envelope).map((point) => finiteProjection(point, camera))
  ));
  const inFrustumEnvelopeCount = projectedEnvelopes.filter((corners) => (
    corners.every((point) => point
      && Math.abs(point.x) <= 1
      && Math.abs(point.y) <= 1
      && Math.abs(point.z) <= 1)
  )).length;
  const projectedPoints = projectedEnvelopes.flat().filter(Boolean);
  const horizontalUtilization = projectedPoints.length > 0
    ? Math.min(1, Math.max(0,
      (Math.max(...projectedPoints.map((point) => point.x))
        - Math.min(...projectedPoints.map((point) => point.x))) / 2,
    ))
    : 0;
  const verticalUtilization = projectedPoints.length > 0
    ? Math.min(1, Math.max(0,
      (Math.max(...projectedPoints.map((point) => point.y))
        - Math.min(...projectedPoints.map((point) => point.y))) / 2,
    ))
    : 0;
  const geometry = fitViewportGeometry(viewportGeometry);
  const sphericalFit = geometry
    ? createSphericalSceneFitReport({
      settledAnchorMap: new Map(nodes.map((node) => [node.node_id, node])),
      nodeEnvelopes: new Map(nodes.map((node) => [
        node.node_id,
        graphNodeVisualEnvelope(node, spatialPolicy),
      ])),
      camera,
      viewportRect: geometry.viewportRect,
      safeInset: geometry.safeInset,
    })
    : null;
  return Object.freeze({
    ...base,
    ...(sphericalFit ?? {}),
    camera: Object.freeze({
      status: inFrustumEnvelopeCount === envelopes.length ? "complete" : "cropped",
      inFrustumEnvelopeCount,
      totalEnvelopeCount: envelopes.length,
      largestDimensionOccupancy: Math.max(
        horizontalUtilization,
        verticalUtilization,
      ),
    }),
  });
}

export function createSceneFitCameraPose(
  sceneGraph,
  camera,
  {
    horizontalUtilization = 0.82,
    verticalUtilization = 0.82,
    viewportGeometry = null,
    spatialPolicy,
  } = {},
) {
  requireGraphSpatialPolicy(spatialPolicy);
  if (!camera?.isPerspectiveCamera) return null;
  const nodes = Array.isArray(sceneGraph?.nodes) ? sceneGraph.nodes : [];
  const envelopes = nodes.map((node) => graphNodeVisualEnvelope(node, spatialPolicy));
  const bounds = createSceneFitReport(sceneGraph, null, null, spatialPolicy).sceneBounds;
  if (!bounds) return null;
  const aspect = Number(camera.aspect);
  const fov = Number(camera.fov);
  if (!Number.isFinite(aspect) || aspect <= 0 || !Number.isFinite(fov) || fov <= 0) return null;
  const geometry = fitViewportGeometry(viewportGeometry);
  const safeWidthFraction = geometry
    ? Math.max(0.05, (
      geometry.viewportRect.width
      - geometry.safeInset.left
      - geometry.safeInset.right
    ) / geometry.viewportRect.width)
    : 1;
  const safeHeightFraction = geometry
    ? Math.max(0.05, (
      geometry.viewportRect.height
      - geometry.safeInset.top
      - geometry.safeInset.bottom
    ) / geometry.viewportRect.height)
    : 1;
  const horizontalTarget = Math.min(0.95, Math.max(0.5,
    Number(horizontalUtilization) || 0.82)) * safeWidthFraction;
  const verticalTarget = Math.min(0.95, Math.max(0.5,
    Number(verticalUtilization) || 0.82)) * safeHeightFraction;
  const verticalTangent = Math.tan((fov * Math.PI / 180) / 2);
  const horizontalTangent = verticalTangent * aspect;
  const target = {
    x: (bounds.min.x + bounds.max.x) / 2,
    y: (bounds.min.y + bounds.max.y) / 2,
    z: (bounds.min.z + bounds.max.z) / 2,
  };
  const near = Number(camera.near);
  const far = Number(camera.far);
  const minimumNearDistance = Number.isFinite(near) && near > 0 ? near : 0;
  const distanceConstraints = envelopes.flatMap((envelope) => (
    envelopeCorners(envelope).flatMap((point) => {
      const relativeDepth = point.z - target.z;
      return [
        relativeDepth + Math.abs(point.x - target.x) / (horizontalTangent * horizontalTarget),
        relativeDepth + Math.abs(point.y - target.y) / (verticalTangent * verticalTarget),
        relativeDepth + minimumNearDistance,
      ];
    })
  ));
  const distance = Math.max(...distanceConstraints) * (1 + Number.EPSILON * 16);
  const maximumDistance = Number.isFinite(far) && far > minimumNearDistance
    ? far + bounds.min.z - target.z
    : Infinity;
  if (!Number.isFinite(distance) || distance <= 0 || distance > maximumDistance) return null;
  const safeCenterNdcX = geometry
    ? (
      geometry.safeInset.left
      + (geometry.viewportRect.width - geometry.safeInset.right)
    ) / geometry.viewportRect.width - 1
    : 0;
  const safeCenterNdcY = geometry
    ? 1 - (
      geometry.safeInset.top
      + (geometry.viewportRect.height - geometry.safeInset.bottom)
    ) / geometry.viewportRect.height
    : 0;
  const shiftedTarget = {
    x: target.x - safeCenterNdcX * distance * horizontalTangent,
    y: target.y - safeCenterNdcY * distance * verticalTangent,
    z: target.z,
  };
  return Object.freeze({
    position: Object.freeze({
      x: shiftedTarget.x,
      y: shiftedTarget.y,
      z: shiftedTarget.z + distance,
    }),
    target: Object.freeze(shiftedTarget),
  });
}

function defaultForceGraphFactory(container, configuration) {
  if (!BrowserForceGraph3D) {
    throw new Error("3d-force-graph is unavailable outside the browser renderer");
  }
  return new BrowserForceGraph3D(container, configuration);
}

function rendererState(availability, reason = null) {
  return {
    availability,
    reason,
    canRetry: availability !== "ready",
  };
}

function cameraDistance(position, target) {
  const dx = (Number(position?.x) || 0) - (Number(target?.x) || 0);
  const dy = (Number(position?.y) || 0) - (Number(target?.y) || 0);
  const dz = (Number(position?.z) || 0) - (Number(target?.z) || 0);
  return Math.hypot(dx, dy, dz);
}

function clampCameraScale(value) {
  return Math.min(MAX_CAMERA_SCALE, Math.max(MIN_CAMERA_SCALE, Number(value) || 1));
}

function cameraScaleLevel(scale) {
  if (scale >= MAX_CAMERA_SCALE - 1e-6) return "maximum";
  return scale > 1.25 ? "detail" : "overview";
}

function scheduleBrowserLayoutCommit(callback) {
  if (typeof globalThis.requestAnimationFrame === "function") {
    return { kind: "frame", token: globalThis.requestAnimationFrame(callback) };
  }
  if (typeof globalThis.window === "undefined") {
    callback();
    return null;
  }
  return { kind: "timeout", token: globalThis.setTimeout?.(callback, 0) ?? null };
}

function cancelBrowserLayoutCommit(handle) {
  if (!handle) return;
  if (handle.kind === "frame") globalThis.cancelAnimationFrame?.(handle.token);
  if (handle.kind === "timeout") globalThis.clearTimeout?.(handle.token);
}

function defaultAppFrameScheduler() {
  return {
    requestAnimationFrame(callback) {
      return globalThis.requestAnimationFrame?.(callback)
        ?? globalThis.setTimeout?.(() => callback(globalThis.performance?.now?.() ?? Date.now()), 16);
    },
    cancelAnimationFrame(token) {
      if (globalThis.cancelAnimationFrame) globalThis.cancelAnimationFrame(token);
      else globalThis.clearTimeout?.(token);
    },
  };
}

export function createThreeGraphRenderer({
  documentObject = globalThis.document,
  forceGraphFactory = defaultForceGraphFactory,
  webglProbe = () => canUseWebGL(documentObject),
  createNodeObjects = ({ spatialPolicy: policy }) => createGraphNodeObjectFactory({
    spatialPolicy: policy,
  }),
  createLinkObjects = () => createGraphLinkObjectFactory(),
  interactionControllerFactory = createGraphInteractionController,
  activityControllerFactory = createGraphActivityController,
  appFrameScheduler = defaultAppFrameScheduler(),
  clock = globalThis.performance,
  onNodeHover = () => {},
  onNodeSelect = () => {},
  onBackgroundSelect = () => {},
  onRendererStateChange = () => {},
  onCameraScaleChange = () => {},
  onCameraPoseChange = () => {},
  onSceneFitReportChange = () => {},
  onSceneLayoutIdentityChange = () => {},
  onRelationLabelFrameChange = () => {},
  onPresentationFrameChange = () => {},
  reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false,
  motionQuery = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)") ?? null,
  scheduleLayoutCommit = scheduleBrowserLayoutCommit,
  cancelLayoutCommit = cancelBrowserLayoutCommit,
  spatialPolicy,
} = {}) {
  if (!documentObject?.createElement) {
    throw new TypeError("document is required to create a 3D graph renderer");
  }
  const persistentContainer = documentObject.createElement("div");
  persistentContainer.className = "harnesskit-graph-scene";
  persistentContainer.tabIndex = -1;
  persistentContainer.setAttribute?.("role", "presentation");
  persistentContainer.setAttribute?.("aria-hidden", "true");

  let graph = null;
  let graphTarget = null;
  let graphDataMounted = false;
  let sceneGraph = null;
  let activeSpatialPolicy = requireGraphSpatialPolicy(spatialPolicy);
  let layoutSettled = false;
  let rendererSceneReady = false;
  let sceneObjectsMaterialized = false;
  let settledLayoutStabilized = false;
  let pendingFit = false;
  let nodeObjects = null;
  let linkObjects = null;
  let interaction = null;
  let canvas = null;
  let controls = null;
  let layoutCommitToken = null;
  let layoutCommitScheduled = false;
  let activityController = null;
  let rendererAttached = false;
  let activityState = {
    expanded: true,
    intersecting: true,
    foreground: documentObject?.visibilityState !== "hidden",
    reducedMotion: Boolean(reducedMotion || motionQuery?.matches),
  };
  let disposed = false;
  let lastPresentation = {};
  let fitCameraDistance = null;
  let currentCameraScale = 1;
  let fitTransitionPending = false;
  let fitTransitionRevision = 0;
  let viewportGeometryObserved = false;
  let requestedSettleTicks = null;
  let recoveryState = null;
  let settledAnchorMap = null;
  let sceneLayoutIdentity = null;
  let viewportGeometry = null;
  let resolvedAppearance = "dark";
  let presentationTransactionRevision = 0;
  let activeScenePresentation = null;

  function cameraVector(value) {
    const x = Number(value?.x);
    const y = Number(value?.y);
    const z = Number(value?.z);
    return [x, y, z].every(Number.isFinite) ? { x, y, z } : null;
  }

  function immutableAnchorSnapshot(source = settledAnchorMap) {
    const entries = source instanceof Map
      ? [...source.entries()]
      : (sceneGraph?.nodes ?? []).map((node) => [node.node_id, node]);
    const anchors = new Map(entries.map(([nodeId, point]) => [String(nodeId), Object.freeze({
      x: Number(point?.x) || 0,
      y: Number(point?.y) || 0,
      z: Number(point?.z) || 0,
    })]));
    Object.defineProperties(anchors, {
      set: { value: () => { throw new TypeError("recovery anchors are immutable"); } },
      delete: { value: () => { throw new TypeError("recovery anchors are immutable"); } },
      clear: { value: () => { throw new TypeError("recovery anchors are immutable"); } },
    });
    return Object.freeze(anchors);
  }

  function sceneLayoutIdentityForHash(settledNodePositionsHash) {
    return Object.freeze({
      projectionId: activeSpatialPolicy.identity.projectionId,
      settledNodePositionsHash,
    });
  }

  function applyRecoveryAnchors(anchorMap, expectedHash = null) {
    if (!(anchorMap instanceof Map) || !sceneGraph) return false;
    const nodes = Array.isArray(sceneGraph.nodes) ? sceneGraph.nodes : [];
    if (anchorMap.size !== nodes.length
      || nodes.some((node) => !anchorMap.has(node.node_id))) return false;
    const actualHash = hashSettledNodePositions(anchorMap);
    if (expectedHash !== null && actualHash !== expectedHash) return false;
    nodes.forEach((node) => {
      const anchor = anchorMap.get(node.node_id);
      for (const axis of ["x", "y", "z"]) {
        const coordinate = Number(anchor?.[axis]);
        if (!Number.isFinite(coordinate)) {
          throw new TypeError(`recovery anchor ${node.node_id}.${axis} must be finite`);
        }
        node[axis] = coordinate;
        node[`f${axis}`] = coordinate;
        node[`v${axis}`] = 0;
      }
    });
    settledAnchorMap = immutableAnchorSnapshot(anchorMap);
    sceneLayoutIdentity = sceneLayoutIdentityForHash(actualHash);
    return true;
  }

  function publishCameraPose(position, target, frame) {
    const safePosition = cameraVector(position);
    const safeTarget = cameraVector(target);
    if (!safePosition || !safeTarget) return null;
    const pose = { position: safePosition, target: safeTarget };
    onCameraPoseChange(pose, frame);
    return pose;
  }

  function captureRecoveryState() {
    const position = cameraVector(graph?.cameraPosition?.());
    const target = cameraVector(currentCameraTarget());
    return Object.freeze({
      projectionId: activeSpatialPolicy?.identity?.projectionId ?? null,
      settledAnchorMap: immutableAnchorSnapshot(),
      settledNodePositionsHash: sceneLayoutIdentity?.settledNodePositionsHash ?? null,
      cameraPose: position && target
        ? Object.freeze({ position: Object.freeze(position), target: Object.freeze(target) })
        : null,
      restoreBaseline: interaction?.exportState?.() ?? null,
      currentCameraScale,
      fitCameraDistance,
    });
  }

  function restoreRecoveryState(state) {
    if (!state) return;
    if (!applyRecoveryAnchors(
      state.settledAnchorMap,
      state.settledNodePositionsHash,
    )) {
      throw new TypeError("recovery settled scene identity does not match");
    }
    const target = cameraVector(state.cameraPose?.target);
    if (target && controls?.target) {
      if (typeof controls.target.copy === "function") controls.target.copy(target);
      else Object.assign(controls.target, target);
    }
    const position = cameraVector(state.cameraPose?.position);
    if (position) graph?.cameraPosition?.(position, target ?? undefined, 0);
    publishCameraPose(position, target);
    const distance = Number(state.fitCameraDistance);
    fitCameraDistance = Number.isFinite(distance) && distance > 0 ? distance : null;
    if (fitCameraDistance && controls) {
      controls.minDistance = fitCameraDistance / MAX_CAMERA_SCALE;
      controls.maxDistance = fitCameraDistance / MIN_CAMERA_SCALE;
    }
    publishCameraScale(state.currentCameraScale);
    interaction?.importState?.(state.restoreBaseline);
  }

  function captureRecoveryCapsule() {
    if (disposed || !activeSpatialPolicy || !sceneGraph) return null;
    return recoveryState ?? captureRecoveryState();
  }

  function restoreRecoveryCapsule(capsule) {
    if (disposed || !capsule || !activeSpatialPolicy || !sceneGraph) return false;
    if (String(capsule.projectionId ?? "") !== activeSpatialPolicy.identity.projectionId) {
      return false;
    }
    if (!(capsule.settledAnchorMap instanceof Map)) return false;
    if (!SETTLED_NODE_POSITIONS_HASH_PATTERN.test(
      String(capsule.settledNodePositionsHash ?? ""),
    )) return false;
    if (!applyRecoveryAnchors(
      capsule.settledAnchorMap,
      capsule.settledNodePositionsHash,
    )) return false;
    recoveryState = capsule;
    return true;
  }

  function publishCameraScale(scale = currentCameraScale) {
    currentCameraScale = Math.round(clampCameraScale(scale) * 100) / 100;
    onCameraScaleChange({
      scale: currentCameraScale,
      minScale: MIN_CAMERA_SCALE,
      maxScale: MAX_CAMERA_SCALE,
      level: cameraScaleLevel(currentCameraScale),
    });
  }

  function currentCameraTarget() {
    return controls?.target ?? graph?.controls?.()?.target ?? { x: 0, y: 0, z: 0 };
  }

  function readCameraScale() {
    if (!graph || !Number.isFinite(fitCameraDistance) || fitCameraDistance <= 0) return null;
    const distance = cameraDistance(graph.cameraPosition?.(), currentCameraTarget());
    if (!Number.isFinite(distance) || distance <= 0) return null;
    return clampCameraScale(fitCameraDistance / distance);
  }

  function captureFitCamera() {
    if (!graph) return false;
    const distance = cameraDistance(graph.cameraPosition?.(), currentCameraTarget());
    if (!Number.isFinite(distance) || distance <= 0) return false;
    fitCameraDistance = distance;
    if (controls) {
      controls.minDistance = fitCameraDistance / MAX_CAMERA_SCALE;
      controls.maxDistance = fitCameraDistance / MIN_CAMERA_SCALE;
    }
    publishCameraScale(1);
    onSceneFitReportChange(createSceneFitReport(
      sceneGraph,
      graph.camera?.(),
      viewportGeometry,
      activeSpatialPolicy,
    ));
    publishCameraPose(graph.cameraPosition?.(), currentCameraTarget());
    publishRelationLabelFrame();
    return true;
  }

  function clearActivityLeases() {
    if (layoutCommitScheduled) cancelLayoutCommit(layoutCommitToken);
    layoutCommitToken = null;
    layoutCommitScheduled = false;
    activityController?.release?.();
    activityController = null;
  }

  function renderOnce() {
    if (!graph) return;
    graph.renderer?.()?.render?.(graph.scene?.(), graph.camera?.());
  }

  function publishRelationLabelFrame() {
    if (!graph || !sceneGraph || !rendererSceneReady) return;
    onRelationLabelFrameChange({
      relations: (sceneGraph.nodes ?? []).filter((node) => node?.node_type === "relation"),
      camera: graph.camera?.() ?? null,
    });
  }

  function effectiveActivityState() {
    return {
      ...activityState,
      expanded: activityState.expanded && rendererAttached && rendererSceneReady,
      intersecting: activityState.intersecting && rendererAttached && rendererSceneReady,
    };
  }

  function cameraRequestsActive({ requireSceneReady = true } = {}) {
    return !disposed
      && Boolean(graph)
      && rendererAttached
      && (!requireSceneReady || rendererSceneReady)
      && activityState.expanded
      && activityState.intersecting
      && activityState.foreground
      && (!viewportGeometryObserved || Boolean(viewportGeometry));
  }

  function syncActivityState() {
    const state = effectiveActivityState();
    activityController?.sync?.(state);
    interaction?.syncAvailability?.({
      active: state.expanded
        && state.intersecting
        && state.foreground
        && (!viewportGeometryObserved || Boolean(viewportGeometry)),
      reducedMotion: state.reducedMotion,
      terminal: false,
    });
    linkObjects?.setActivityState?.({
      active: state.expanded && state.intersecting && state.foreground,
      reducedMotion: state.reducedMotion,
    });
  }

  function initializeActivityController() {
    activityController?.release?.();
    activityController = activityControllerFactory({
      rendererAnimationPort: {
        resumeAnimation: () => graph?.resumeAnimation?.(),
        pauseAnimation: () => graph?.pauseAnimation?.(),
        renderOnce,
      },
      appFrameScheduler,
      clock,
      // The persistent Component Map session owns live document/motion
      // subscriptions and forwards one normalized activity state here.
      documentObject: null,
      motionQuery: null,
      onAppFrame: () => {
        renderOnce();
        publishRelationLabelFrame();
      },
    });
    syncActivityState();
  }

  function handleControlsStart() {
    if (!graph || !rendererSceneReady) return;
  }

  function handleControlsChange() {
    const scale = readCameraScale();
    if (scale !== null) publishCameraScale(scale);
    publishCameraPose(graph?.cameraPosition?.(), currentCameraTarget());
    publishRelationLabelFrame();
    activityController?.refreshOnce?.("camera-interaction");
  }

  function handleControlsEnd() {
    activityController?.refreshOnce?.("camera-interaction-end");
  }

  function publishRendererState(availability, reason = null) {
    const state = rendererState(availability, reason);
    persistentContainer.dataset.rendererAvailability = availability;
    onRendererStateChange(state);
    return state;
  }

  function destroyGraph({ preserveForRetry = false, keepRecoveryState = false } = {}) {
    if (preserveForRetry) recoveryState = captureRecoveryState();
    else if (!keepRecoveryState) recoveryState = null;
    clearActivityLeases();
    activeScenePresentation = null;
    if (canvas) {
      canvas.removeEventListener?.("webglcontextlost", handleContextLoss);
    }
    if (controls) {
      controls.removeEventListener?.("start", handleControlsStart);
      controls.removeEventListener?.("change", handleControlsChange);
      controls.removeEventListener?.("end", handleControlsEnd);
    }
    canvas = null;
    controls = null;
    if (typeof interaction?.release === "function") interaction.release();
    else interaction?.resetCameraContext?.();
    if (graph) {
      graph._destructor?.();
    }
    if (nodeObjects) {
      nodeObjects.dispose?.();
    }
    if (linkObjects) {
      linkObjects.dispose?.();
    }
    graph = null;
    nodeObjects = null;
    linkObjects = null;
    interaction = null;
    graphDataMounted = false;
    rendererSceneReady = false;
    sceneObjectsMaterialized = false;
    fitCameraDistance = null;
    currentCameraScale = 1;
    fitTransitionPending = false;
    persistentContainer.replaceChildren?.();
    onRelationLabelFrameChange(null);
  }

  function handleContextLoss(event) {
    event?.preventDefault?.();
    destroyGraph({ preserveForRetry: true });
    publishRendererState(
      "context_lost",
      "3D 그래프 연결이 중단되어 텍스트 보기로 전환했습니다.",
    );
  }

  function configureForces() {
    if (!graph || !activeSpatialPolicy) return;
    const force = activeSpatialPolicy.force;
    graph.d3Force?.("collision", createBoundedCollisionForce({
      spatialPolicy: activeSpatialPolicy,
    }));
    graph.d3Force?.("charge")
      ?.strength?.(force.chargeStrength)
      ?.distanceMax?.(force.chargeDistanceMaximum);
    graph.d3Force?.("link")?.distance?.((link) => (
      link?.semantic === "component-cross-link"
        ? force.componentCrossLinkDistance
        : force.relationLinkDistance
    ))?.strength?.(force.linkStrength);
    graph.d3AlphaDecay?.(force.alphaDecay);
  }

  function mountGraphData(warmupTicks) {
    if (!graph || !sceneGraph || graphDataMounted) return;
    rendererSceneReady = false;
    sceneObjectsMaterialized = (sceneGraph.nodes?.length ?? 0) === 0;
    graph.warmupTicks?.(warmupTicks);
    graph.cooldownTicks?.(0);
    graph.cooldownTime?.(Number.POSITIVE_INFINITY);
    graph.onEngineStop?.(handleEngineStop);
    graph.graphData?.(sceneGraph);
    graphDataMounted = true;
  }

  function executeFit() {
    if (!cameraRequestsActive()) return false;
    if (layoutCommitScheduled) cancelLayoutCommit(layoutCommitToken);
    layoutCommitToken = null;
    layoutCommitScheduled = false;
    pendingFit = false;
    const duration = activityState.reducedMotion ? 0 : 360;
    fitTransitionRevision += 1;
    const transitionRevision = fitTransitionRevision;
    let transitionFinalized = false;
    const finalizeTransition = () => {
      if (transitionFinalized) return false;
      transitionFinalized = true;
      if (transitionRevision !== fitTransitionRevision) return false;
      fitTransitionPending = false;
      if (controls) controls.enabled = true;
      return true;
    };
    fitCameraDistance = null;
    fitTransitionPending = duration > 0;
    if (controls && fitTransitionPending) controls.enabled = false;
    graph.scene?.()?.updateMatrixWorld?.(true);
    const cameraPose = createSceneFitCameraPose(sceneGraph, graph.camera?.(), {
      viewportGeometry,
      spatialPolicy: activeSpatialPolicy,
    }) ?? {
      position: cameraVector(graph.cameraPosition?.()),
      target: cameraVector(currentCameraTarget()),
    };
    if (!cameraPose.position || !cameraPose.target) {
      finalizeTransition();
      return false;
    }
    const completed = interaction?.transitionCamera?.(cameraPose, {
      duration,
      focusKey: "fit",
      intent: "explicit",
      onAbort() {
        finalizeTransition();
      },
      onComplete() {
        if (!finalizeTransition()) return;
        captureFitCamera();
        renderOnce();
      },
    }) ?? false;
    if (!completed) {
      finalizeTransition();
      return false;
    }
    return true;
  }

  function scheduleInitialFitAfterLayoutCommit() {
    if (!pendingFit || layoutCommitScheduled || !graph || !rendererSceneReady) return;
    layoutCommitScheduled = true;
    layoutCommitToken = scheduleLayoutCommit(() => {
      layoutCommitToken = null;
      layoutCommitScheduled = false;
      if (!pendingFit || !graph || !rendererSceneReady) return;
      executeFit();
    });
  }

  function handleEngineStop() {
    if (!graph || !graphDataMounted || rendererSceneReady) return;
    if ((sceneGraph?.nodes?.length ?? 0) > 0 && !sceneObjectsMaterialized) return;
    if (!layoutSettled && !settledLayoutStabilized) {
      stabilizeSettledSceneGraph(sceneGraph);
      settledLayoutStabilized = true;
    }
    rendererSceneReady = true;
    layoutSettled = Boolean(sceneGraph);
    publishRendererState("ready");
    if (pendingFit) scheduleInitialFitAfterLayoutCommit();
    interaction?.syncPresentation?.(lastPresentation, sceneGraph);
    syncActivityState();
    publishRelationLabelFrame();
  }

  function initializeGraph() {
    if (disposed || graph || !graphTarget || !activeSpatialPolicy) return Boolean(graph);
    if (!webglProbe()) {
      publishRendererState("unavailable", "webgl_unavailable");
      return false;
    }
    let initializationStage = "prepare";
    try {
      persistentContainer.replaceChildren?.();
      initializationStage = "create-node-objects";
      nodeObjects = createNodeObjects({ spatialPolicy: activeSpatialPolicy });
      initializationStage = "create-link-objects";
      linkObjects = createLinkObjects();
      initializationStage = "create-force-graph";
      graph = forceGraphFactory(persistentContainer, {
        controlType: "orbit",
        rendererConfig: { alpha: true, antialias: true, powerPreference: "high-performance" },
      });
      initializationStage = "configure-force-graph";
      graph.forceEngine?.("d3");
      graph
        .nodeId?.("node_id")
        ?.linkSource?.("source")
        ?.linkTarget?.("target")
        ?.nodeLabel?.(() => "")
        ?.nodeThreeObject?.((node) => {
          sceneObjectsMaterialized = true;
          return nodeObjects.create(node);
        })
        ?.linkThreeObject?.((link) => linkObjects.create(link))
        ?.linkPositionUpdate?.((object, positions, link) => (
          linkObjects.updatePosition(object, positions, link)
        ))
        ?.enableNodeDrag?.(false)
        ?.showNavInfo?.(false)
        ?.backgroundColor?.("rgba(0,0,0,0)");
      initializationStage = "configure-forces";
      configureForces();
      const pendingRecoveryState = recoveryState;
      initializationStage = "create-interaction";
      interaction = interactionControllerFactory({
        clock,
        frameScheduler: appFrameScheduler,
        graph,
        linkObjects,
        nodeObjects,
        onBackgroundSelect,
        onCameraFrame(pose, progress) {
          publishCameraPose(pose?.position, pose?.target, { progress });
          const scale = readCameraScale();
          if (scale !== null) publishCameraScale(scale);
          renderOnce();
          publishRelationLabelFrame();
        },
        onNodeHover,
        onNodeSelect,
        resolveFramePose(nodes) {
          return createSceneFitCameraPose({ nodes }, graph.camera?.(), {
            viewportGeometry,
            spatialPolicy: activeSpatialPolicy,
          });
        },
        reducedMotion: activityState.reducedMotion,
      });
      interaction?.setResolvedAppearance?.(resolvedAppearance);
      initializationStage = "bind-controls";
      controls = graph.controls?.() ?? null;
      restoreRecoveryState(pendingRecoveryState);
      controls?.addEventListener?.("start", handleControlsStart);
      controls?.addEventListener?.("change", handleControlsChange);
      controls?.addEventListener?.("end", handleControlsEnd);
      initializationStage = "bind-canvas";
      canvas = graph.renderer?.()?.domElement ?? persistentContainer.children?.[0] ?? null;
      canvas?.setAttribute?.("aria-hidden", "true");
      canvas?.setAttribute?.("role", "presentation");
      canvas?.setAttribute?.("tabindex", "-1");
      canvas?.addEventListener?.("webglcontextlost", handleContextLoss);
      initializationStage = "create-activity-controller";
      initializeActivityController();
      initializationStage = "publish-pending";
      publishRendererState("pending", "3D 그래프를 정리하고 있습니다.");
      if (sceneGraph && requestedSettleTicks !== null) {
        initializationStage = "mount-graph-data";
        mountGraphData(layoutSettled ? 0 : (requestedSettleTicks ?? 0));
        graph.resumeAnimation?.();
      }
      recoveryState = null;
      return true;
    } catch (error) {
      destroyGraph({ keepRecoveryState: true });
      const message = error instanceof Error ? error.message : String(error ?? "unknown");
      publishRendererState(
        "unavailable",
        `webgl_initialization_failed:${initializationStage}:${message.slice(0, 160)}`,
      );
      return false;
    }
  }

  function attach(target) {
    if (disposed || !target?.appendChild) return;
    graphTarget = target;
    rendererAttached = true;
    if (persistentContainer.parentNode !== target) target.appendChild(persistentContainer);
    if (activeSpatialPolicy) initializeGraph();
    if (graphDataMounted && !rendererSceneReady) graph?.resumeAnimation?.();
    else if (rendererSceneReady) {
      syncActivityState();
      activityController?.refreshOnce?.("attach");
      publishRelationLabelFrame();
    }
  }

  function detach() {
    if (disposed) return;
    rendererAttached = false;
    syncActivityState();
    if (rendererSceneReady) renderOnce();
    persistentContainer.remove?.();
  }

  function setGraphData(nextSceneGraph) {
    if (disposed) return;
    activeSpatialPolicy = assertGraphSpatialPolicyProjection(
      activeSpatialPolicy,
      nextSceneGraph,
    );
    const prepared = prepareSphericalScene(nextSceneGraph, {
      spatialPolicy: activeSpatialPolicy,
    });
    sceneGraph = prepared.sceneGraph;
    settledAnchorMap = prepared.settledAnchorMap;
    sceneLayoutIdentity = sceneLayoutIdentityForHash(prepared.settledNodePositionsHash);
    onSceneLayoutIdentityChange(sceneLayoutIdentity);
    graphDataMounted = false;
    layoutSettled = false;
    rendererSceneReady = false;
    sceneObjectsMaterialized = false;
    settledLayoutStabilized = false;
    pendingFit = false;
    if (graphTarget && !graph) initializeGraph();
    configureForces();
    syncActivityState();
  }

  function settle(ticks) {
    if (disposed || !sceneGraph) return;
    if (!Number.isInteger(ticks) || ticks < 1) {
      throw new TypeError("settle ticks must be a positive integer");
    }
    requestedSettleTicks = ticks;
    if (!graph) return;
    mountGraphData(ticks);
  }

  function pauseAnimation() {
    if (disposed || !graph) return;
    mountGraphData(requestedSettleTicks ?? 0);
    if (rendererSceneReady) syncActivityState();
  }

  function resize(width, height) {
    if (disposed || !graph) return;
    const safeWidth = Math.max(1, Number(width) || 1);
    const safeHeight = Math.max(1, Number(height) || 1);
    graph.width?.(safeWidth);
    graph.height?.(safeHeight);
    if (rendererSceneReady) {
      activityController?.refreshOnce?.("resize");
      publishRelationLabelFrame();
    }
  }

  function setViewportGeometry(nextGeometry) {
    if (disposed) return;
    viewportGeometryObserved = true;
    viewportGeometry = fitViewportGeometry(nextGeometry);
    syncActivityState();
    if (!graph || !rendererSceneReady) return;
    onSceneFitReportChange(createSceneFitReport(
      sceneGraph,
      graph.camera?.(),
      viewportGeometry,
      activeSpatialPolicy,
    ));
    activityController?.refreshOnce?.("viewport-geometry");
    publishRelationLabelFrame();
  }

  function syncPresentation(presentation = {}) {
    if (disposed) return;
    lastPresentation = presentation && typeof presentation === "object" ? presentation : {};
    if (!graph || !sceneGraph || !rendererSceneReady) return;
    const presentationTransition = interaction?.preparePresentationTransition?.(
      lastPresentation,
      sceneGraph,
    ) ?? null;
    if (!presentationTransition) interaction?.syncPresentation?.(lastPresentation, sceneGraph);
    startPresentationTransaction(presentationTransition, "selection");
    activityController?.refreshOnce?.("selection");
  }

  function startPresentationTransaction(sceneTransition = null, reason = "presentation") {
    if (disposed || !graph || !sceneGraph || !rendererSceneReady) return false;
    if (sceneTransition) {
      activeScenePresentation = {
        progress: 0,
        transition: sceneTransition,
      };
    }
    const scenePresentation = activeScenePresentation;
    const sceneProgressAtRetarget = scenePresentation?.progress ?? 1;
    presentationTransactionRevision += 1;
    const transactionId = `presentation:${presentationTransactionRevision}`;
    const durationMs = activityState.reducedMotion ? 0 : 240;
    const apply = (value) => {
      const progress = Math.min(1, Math.max(0, Number(value) || 0));
      if (scenePresentation) {
        const sceneProgress = sceneProgressAtRetarget
          + ((1 - sceneProgressAtRetarget) * progress);
        scenePresentation.transition.apply?.(sceneProgress);
        scenePresentation.progress = sceneProgress;
        if (sceneProgress >= 1 && activeScenePresentation === scenePresentation) {
          activeScenePresentation = null;
        }
      }
      onPresentationFrameChange(Object.freeze({
        transactionId,
        progress,
        durationMs,
        reducedMotion: activityState.reducedMotion,
        reason,
      }));
    };
    if (activityController) {
      activityController.transition([{
        id: "presentation",
        from: 0,
        to: 1,
        durationMs,
        apply,
      }]);
    } else {
      apply(1);
    }
    return true;
  }

  function requestPresentationTransition(reason = "dom-presentation") {
    return startPresentationTransaction(null, String(reason || "dom-presentation"));
  }

  function setResolvedAppearance(appearance) {
    if (disposed) return false;
    const normalized = String(appearance ?? "").toLowerCase();
    if (!new Set(["light", "dark"]).has(normalized)) {
      throw new TypeError("resolved appearance must be light or dark");
    }
    if (resolvedAppearance === normalized) return false;
    resolvedAppearance = normalized;
    interaction?.setResolvedAppearance?.(normalized);
    if (graph && sceneGraph && rendererSceneReady) syncPresentation(lastPresentation);
    return true;
  }

  function diagnostics() {
    const presentation = activityController?.diagnostics?.() ?? {};
    const camera = interaction?.diagnostics?.() ?? {};
    const presentationRafRunning = Number(presentation.appRafRunning) === 1 ? 1 : 0;
    const cameraRafRunning = Number(camera.cameraRafRunning) === 1 ? 1 : 0;
    return Object.freeze({
      applicationRafTotal: presentationRafRunning + cameraRafRunning,
      cameraRafRunning,
      internalRendererRunning: Number(presentation.internalRendererRunning) === 1 ? 1 : 0,
      presentationRafRunning,
    });
  }

  function setActivityState(nextState = {}) {
    activityState = {
      ...activityState,
      ...(Object.hasOwn(nextState, "expanded")
        ? { expanded: Boolean(nextState.expanded) }
        : {}),
      ...(Object.hasOwn(nextState, "intersecting")
        ? { intersecting: Boolean(nextState.intersecting) }
        : {}),
      ...(Object.hasOwn(nextState, "foreground")
        ? { foreground: Boolean(nextState.foreground) }
        : {}),
      ...(Object.hasOwn(nextState, "reducedMotion")
        ? { reducedMotion: Boolean(nextState.reducedMotion) }
        : {}),
    };
    syncActivityState();
  }

  function focusNode(nodeOrId, options = {}) {
    if (!sceneGraph || !cameraRequestsActive()) return false;
    const targetNode = typeof nodeOrId === "string"
      ? sceneGraph.nodes?.find((node) => node.node_id === nodeOrId)
      : nodeOrId;
    if (!targetNode) return false;
    const focusNodeIds = new Set([targetNode.node_id]);
    (sceneGraph.links ?? []).forEach((link) => {
      const sourceId = typeof link?.source_node_id === "string"
        ? link.source_node_id
        : typeof link?.source?.node_id === "string" ? link.source.node_id : null;
      const targetId = typeof link?.target_node_id === "string"
        ? link.target_node_id
        : typeof link?.target?.node_id === "string" ? link.target.node_id : null;
      if (sourceId !== targetNode.node_id && targetId !== targetNode.node_id) return;
      if (sourceId) focusNodeIds.add(sourceId);
      if (targetId) focusNodeIds.add(targetId);
    });
    const focusNodes = (sceneGraph.nodes ?? []).filter((node) => focusNodeIds.has(node.node_id));
    return interaction?.focusNodes?.(focusNodes, targetNode.node_id, options) ?? false;
  }

  function restoreCamera() {
    return interaction?.restoreCamera?.() ?? false;
  }

  function zoomBy(factor) {
    if (!cameraRequestsActive() || fitTransitionPending) return false;
    const scale = Number(factor);
    if (!Number.isFinite(scale) || scale <= 0) return false;
    const current = graph.cameraPosition?.();
    if (!current) return false;
    const target = currentCameraTarget();
    if (!Number.isFinite(fitCameraDistance) || fitCameraDistance <= 0) {
      captureFitCamera();
    }
    const measuredScale = readCameraScale() ?? currentCameraScale;
    const desiredScale = clampCameraScale(measuredScale / scale);
    const distanceFactor = measuredScale / desiredScale;
    const next = {
      x: (Number(target.x) || 0) + ((Number(current.x) || 0) - (Number(target.x) || 0)) * distanceFactor,
      y: (Number(target.y) || 0) + ((Number(current.y) || 0) - (Number(target.y) || 0)) * distanceFactor,
      z: (Number(target.z) || 0) + ((Number(current.z) || 0) - (Number(target.z) || 0)) * distanceFactor,
    };
    const completed = interaction?.transitionCamera?.({ position: next, target }, {
      duration: activityState.reducedMotion ? 0 : 180,
      focusKey: "zoom",
      intent: "explicit",
      onComplete() {
        publishCameraScale(desiredScale);
        renderOnce();
      },
    }) ?? false;
    if (!completed) {
      return false;
    }
    return true;
  }

  function fitGraph() {
    if (disposed || !graph) return false;
    if (!rendererSceneReady) {
      if (!cameraRequestsActive({ requireSceneReady: false })) return false;
      pendingFit = true;
      return true;
    }
    return executeFit();
  }

  function retry() {
    if (disposed || graph || !graphTarget) return Boolean(graph);
    const initialized = initializeGraph();
    if (initialized && sceneGraph && !graphDataMounted) {
      pendingFit = true;
      mountGraphData(layoutSettled ? 0 : (requestedSettleTicks ?? 0));
      graph.resumeAnimation?.();
    }
    return initialized;
  }

  function showSemanticFallback() {
    if (disposed || !graph) return false;
    destroyGraph({ preserveForRetry: true });
    publishRendererState(
      "manual_fallback",
      "사용자가 텍스트 보기를 선택했습니다.",
    );
    return true;
  }

  function dispose() {
    if (disposed) return;
    destroyGraph();
    persistentContainer.remove?.();
    graphTarget = null;
    sceneGraph = null;
    settledAnchorMap = null;
    sceneLayoutIdentity = null;
    recoveryState = null;
    disposed = true;
  }

  return {
    attach,
    captureRecoveryCapsule,
    detach,
    diagnostics,
    dispose,
    fitGraph,
    focusNode,
    pauseAnimation,
    resize,
    requestPresentationTransition,
    restoreCamera,
    restoreRecoveryCapsule,
    retry,
    showSemanticFallback,
    setGraphData,
    setActivityState,
    setResolvedAppearance,
    setViewportGeometry,
    settle,
    syncPresentation,
    zoomBy,
  };
}
