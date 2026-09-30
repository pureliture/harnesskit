import assert from "node:assert/strict";
import test from "node:test";
import { PerspectiveCamera } from "three";

import { createComponentMapSession } from "../graph/component-map-session.js";
import { graphSpatialPolicyFor } from "../graph/spatial-policy.js";

function snapshotFixture() {
  return {
    snapshot_id: "snapshot-session",
    components: [{
      component_id: "harnesskit.agent.reviewer",
      title: "Reviewer",
      kind: "agent",
      domain: "engineering",
      status: "draft",
    }],
    graph_projection: {
      schema_version: 2,
      projection_id: "projection-session",
      snapshot_id: "snapshot-session",
      layout_seed: "seed-session",
      nodes: [
        {
          node_type: "relation",
          node_id: "profile:harnesskit.profile.engineering",
          relation_kind: "profile",
          canonical_id: "harnesskit.profile.engineering",
          name: "Engineering",
          exact_count: 1,
          anchor_ordinal: 0,
          size_scale: 1,
        },
        {
          node_type: "relation",
          node_id: "workflow:harnesskit.workflow.review",
          relation_kind: "workflow",
          canonical_id: "harnesskit.workflow.review",
          name: "Review",
          exact_count: 1,
          anchor_ordinal: 0,
          size_scale: 1,
        },
        {
          node_type: "component",
          node_id: "component:harnesskit.agent.reviewer",
          component_id: "harnesskit.agent.reviewer",
          kind: "agent",
          domain: "engineering",
          relation_degree: 2,
        },
      ],
      links: [{
        semantic: "workflow-step",
        link_id: "workflow-step:review:reviewer",
        workflow_node_id: "workflow:harnesskit.workflow.review",
        component_node_id: "component:harnesskit.agent.reviewer",
        source_node_id: "workflow:harnesskit.workflow.review",
        target_node_id: "component:harnesskit.agent.reviewer",
        directionality: "directed",
        occurrences: [{ ordinal: 1, step_id: "review", source_field: "agent" }],
      }],
    },
  };
}

class FakeElement {
  constructor(dataset = {}) {
    this.dataset = { ...dataset };
    this.hidden = false;
    this.children = [];
    this.className = "";
    this.innerHTML = "";
    this.textContent = "";
    this.style = {};
    this.listeners = new Map();
    this.ownerDocument = { createElement() {} };
    this.attributes = new Map();
    this.focused = false;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.set(type, (this.listeners.get(type) ?? []).filter((value) => value !== listener));
  }

  appendChild(child) {
    child.remove?.();
    this.children.push(child);
    child.parentNode = this;
    return child;
  }

  removeChild(child) {
    this.children = this.children.filter((candidate) => candidate !== child);
    child.parentNode = null;
    return child;
  }

  remove() {
    this.parentNode?.removeChild?.(this);
  }

  getBoundingClientRect() {
    return { width: 960, height: 540 };
  }

  focus() {
    this.focused = true;
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }

  dispatch(type, target = this) {
    for (const listener of this.listeners.get(type) ?? []) listener({ target, preventDefault() {} });
  }
}

function rootHarness() {
  const scene = new FakeElement();
  const semantic = new FakeElement();
  const cameraPose = new FakeElement();
  const status = new FakeElement();
  const scale = new FakeElement();
  const semanticView = new FakeElement();
  const overlay = new FakeElement();
  overlay.hidden = true;
  const title = new FakeElement();
  const kind = new FakeElement();
  const count = new FakeElement();
  const viewport = new FakeElement();
  const hud = new FakeElement();
  viewport.getBoundingClientRect = () => ({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    width: 960,
    height: 540,
  });
  hud.getBoundingClientRect = () => ({
    x: 12,
    y: 12,
    left: 12,
    top: 12,
    width: 936,
    height: 68,
  });
  const relationLabelRoot = new FakeElement();
  const controls = ["out", "reset", "in"].map((graphZoom) => new FakeElement({ graphZoom }));
  const selectors = new Map([
    ["[data-component-map-scene-host]", scene],
    ["[data-component-map-semantic-host]", semantic],
    ["[data-graph-renderer-state]", status],
    ["[data-graph-scale]", scale],
    ["[data-graph-semantic-view]", semanticView],
    ["[data-graph-identity-overlay]", overlay],
    ["[data-graph-identity-title]", title],
    ["[data-graph-identity-kind]", kind],
    ["[data-graph-identity-count]", count],
    ["[data-component-map-viewport]", viewport],
    ["[data-component-map-hud]", hud],
    ["[data-graph-relation-label-root]", relationLabelRoot],
  ]);
  const root = new FakeElement();
  const documentObject = {
    activeElement: null,
    createElement() {
      const element = new FakeElement();
      element.ownerDocument = documentObject;
      return element;
    },
  };
  [root, scene, semantic, cameraPose, status, scale, semanticView, overlay, title, kind, count,
    viewport, hud, relationLabelRoot, ...controls]
    .forEach((element) => { element.ownerDocument = documentObject; });
  semantic.querySelector = (selector) => (
    selector === "[data-graph-camera-pose]" ? cameraPose : null
  );
  root.querySelector = (selector) => selectors.get(selector) ?? null;
  root.querySelectorAll = (selector) => selector === "[data-graph-zoom]" ? controls : [];
  root.ownerDocument = documentObject;
  return {
    cameraPose,
    controls,
    count,
    hud,
    kind,
    overlay,
    relationLabelRoot,
    root,
    scale,
    scene,
    semantic,
    semanticView,
    status,
    title,
    viewport,
  };
}

function rendererHarness({
  attachAvailability = "ready",
  retryAvailability = "ready",
  autoPresentationFrames = true,
} = {}) {
  const recoveryCapsule = Object.freeze({
    projectionId: "projection-session",
    settledAnchorMap: new Map([
      ["component:harnesskit.agent.reviewer", Object.freeze({ x: 1, y: 2, z: 3 })],
    ]),
    cameraPose: Object.freeze({
      position: Object.freeze({ x: 31, y: -17, z: 241 }),
      target: Object.freeze({ x: 4, y: 5, z: 6 }),
    }),
    restoreBaseline: Object.freeze({
      previousCamera: Object.freeze({
        position: Object.freeze({ x: 0, y: 0, z: 500 }),
        target: Object.freeze({ x: 0, y: 0, z: 0 }),
      }),
      focusKey: "explicit:component:harnesskit.agent.reviewer",
      focusIntent: "contextual",
    }),
  });
  const counts = {
    attach: 0,
    detach: 0,
    dispose: 0,
    fit: 0,
    retry: 0,
    showSemanticFallback: 0,
    resize: 0,
    setGraphData: 0,
    sync: 0,
    presentations: [],
    presentationTransitionRequests: [],
    appearances: [],
    settle: [],
    zoom: [],
    focusNode: [],
    focusOptions: [],
    restoreCamera: 0,
    activityStates: [],
    viewportGeometries: [],
    captureRecoveryCapsule: 0,
    restoredRecoveryCapsules: [],
  };
  let callbacks = null;
  let presentationRevision = 0;
  const publishPresentationFrame = (options, reason) => {
    presentationRevision += 1;
    options.onPresentationFrameChange?.({
      transactionId: `test:${presentationRevision}`,
      progress: 1,
      durationMs: 0,
      reducedMotion: true,
      reason,
    });
  };
  const rendererFactory = (options) => {
    callbacks = options;
    return {
      attach() {
        counts.attach += 1;
        options.onRendererStateChange({ availability: attachAvailability, reason: null });
      },
      detach() { counts.detach += 1; },
      dispose() { counts.dispose += 1; },
      fitGraph() { counts.fit += 1; },
      pauseAnimation() {},
      resize() { counts.resize += 1; },
      retry() {
        counts.retry += 1;
        options.onRendererStateChange({ availability: retryAvailability, reason: null });
      },
      showSemanticFallback() {
        counts.showSemanticFallback += 1;
        options.onRendererStateChange({
          availability: "manual_fallback",
          reason: "사용자가 텍스트 보기를 선택했습니다.",
        });
        return true;
      },
      restoreCameraForEvidence() { counts.restore = (counts.restore ?? 0) + 1; return true; },
      runtimeEvidenceSnapshot() {
        return {
          renderer: { availability: "ready", reason: null },
          camera: { position: { x: 1, y: 2, z: 3 }, target: { x: 0, y: 0, z: 0 } },
          settled_node_positions_hash: "positions-a",
          canvas: { present: true, width: 960, height: 540 },
          resources: { graph_generation: 1, active_canvas_listener_count: 1 },
        };
      },
      setGraphData() { counts.setGraphData += 1; },
      syncPresentation(nextPresentation) {
        counts.sync += 1;
        counts.presentations.push(structuredClone(nextPresentation));
        if (autoPresentationFrames) publishPresentationFrame(options, "selection");
      },
      requestPresentationTransition(reason) {
        counts.presentationTransitionRequests.push(reason);
        if (autoPresentationFrames) publishPresentationFrame(options, reason);
        return true;
      },
      setResolvedAppearance(appearance) { counts.appearances.push(appearance); },
      settle(ticks) { counts.settle.push(ticks); },
      zoomBy(factor) { counts.zoom.push(factor); },
      focusNode(nodeId, options) {
        counts.focusNode.push(nodeId);
        counts.focusOptions.push(options ?? null);
      },
      restoreCamera() {
        counts.restoreCamera += 1;
        return true;
      },
      captureRecoveryCapsule() {
        counts.captureRecoveryCapsule += 1;
        return recoveryCapsule;
      },
      restoreRecoveryCapsule(capsule) {
        counts.restoredRecoveryCapsules.push(capsule);
        return capsule === recoveryCapsule;
      },
      setActivityState(state) { counts.activityStates.push({ ...state }); },
      setViewportGeometry(geometry) {
        counts.viewportGeometries.push(structuredClone(geometry));
      },
    };
  };
  return {
    get callbacks() { return callbacks; },
    counts,
    recoveryCapsule,
    rendererFactory,
  };
}

function presentation(overrides = {}) {
  return {
    activeProfileId: "harnesskit.profile.engineering",
    selectedComponentId: null,
    selectedRelationNodeId: null,
    selectedWorkflowId: null,
    hoveredGraphNodeId: null,
    hoveredWorkflowStep: null,
    lockedWorkflowStep: null,
    graphPresentation: {
      viewMode: "three_d",
      rendererLifecycle: "ready",
      failure: null,
      focusNodeId: null,
    },
    ...overrides,
  };
}

function presentationFrame(transactionId, progress, reducedMotion = false) {
  return {
    transactionId,
    progress,
    durationMs: reducedMotion ? 0 : 240,
    reducedMotion,
    reason: "test",
  };
}

test("session forwards one primary focus and resolved appearance without rebuilding the renderer", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });

  session.setResolvedAppearance("light");
  session.attach(root.root, presentation({
    graphPresentation: {
      viewMode: "three_d",
      rendererLifecycle: "ready",
      failure: null,
      focusNodeId: "component:harnesskit.agent.reviewer",
    },
  }));
  session.setResolvedAppearance("dark");

  assert.deepEqual(renderer.counts.appearances, ["light", "dark"]);
  assert.equal(
    renderer.counts.presentations.at(-1).focusNodeId,
    "component:harnesskit.agent.reviewer",
  );
  assert.equal(renderer.counts.setGraphData, 1);
  assert.equal(renderer.counts.attach, 1);
  session.dispose();
});

test("one Component Map session reattaches the same settled renderer and routes camera controls", () => {
  const renderer = rendererHarness();
  const first = rootHarness();
  const second = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });

  session.attach(first.root, presentation());
  session.detach();
  session.attach(second.root, presentation());
  second.controls[0].dispatch("click");
  second.controls[1].dispatch("click");
  second.controls[2].dispatch("click");

  assert.equal(renderer.counts.setGraphData, 1);
  assert.deepEqual(renderer.counts.settle, [180]);
  assert.equal(renderer.counts.attach, 2);
  assert.deepEqual(renderer.counts.zoom, [1.2, 0.8]);
  assert.equal(renderer.counts.fit, 2);
  session.dispose();
  assert.equal(renderer.counts.dispose, 1);
});

test("session creates one projection policy before renderer construction", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const snapshot = snapshotFixture();
  const expectedPolicy = graphSpatialPolicyFor(snapshot.graph_projection);
  const session = createComponentMapSession({
    snapshot,
    rendererFactory: renderer.rendererFactory,
  });

  session.attach(root.root, presentation());

  assert.equal(renderer.callbacks.spatialPolicy, expectedPolicy);
  assert.equal(renderer.callbacks.spatialPolicy.identity.projectionId, "projection-session");
  assert.equal(renderer.callbacks.spatialPolicy.identity.nodeCount, 3);
  assert.equal(renderer.callbacks.spatialPolicy.identity.linkCount, 1);
  session.dispose();
});

test("session forwards intersection, document visibility, and live reduced motion to one renderer", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const documentListeners = new Map();
  const motionListeners = new Map();
  const documentObject = {
    ...root.root.ownerDocument,
    visibilityState: "visible",
    addEventListener(type, listener) { documentListeners.set(type, listener); },
    removeEventListener(type, listener) {
      if (documentListeners.get(type) === listener) documentListeners.delete(type);
    },
  };
  root.root.ownerDocument = documentObject;
  root.scene.ownerDocument = documentObject;
  const motionQuery = {
    matches: false,
    addEventListener(type, listener) { motionListeners.set(type, listener); },
    removeEventListener(type, listener) {
      if (motionListeners.get(type) === listener) motionListeners.delete(type);
    },
  };
  let intersectionCallback = null;
  let disconnects = 0;
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    intersectionObserverFactory(callback) {
      intersectionCallback = callback;
      return {
        observe() {},
        disconnect() { disconnects += 1; },
      };
    },
    motionQuery,
  });

  session.attach(root.root, presentation());
  intersectionCallback([{ target: root.scene, isIntersecting: false, intersectionRatio: 0 }]);
  documentObject.visibilityState = "hidden";
  documentListeners.get("visibilitychange")();
  motionQuery.matches = true;
  motionListeners.get("change")({ matches: true });

  assert.deepEqual(renderer.counts.activityStates.at(-1), {
    expanded: true,
    foreground: false,
    intersecting: false,
    reducedMotion: true,
  });
  session.dispose();
  assert.equal(disconnects, 1);
  assert.equal(documentListeners.size, 0);
  assert.equal(motionListeners.size, 0);
});

test("session binds settled relation labels to one HUD-safe overlay and current selection", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const snapshot = snapshotFixture();
  const expectedPolicy = graphSpatialPolicyFor(snapshot.graph_projection);
  const overlayFrames = [];
  let overlayReleases = 0;
  const session = createComponentMapSession({
    snapshot,
    rendererFactory: renderer.rendererFactory,
    relationLabelOverlayFactory(target, options) {
      assert.equal(target, root.relationLabelRoot);
      assert.equal(typeof options.projectWorldToScreen, "function");
      assert.equal(options.spatialPolicy, expectedPolicy);
      return {
        sync(frame) { overlayFrames.push(frame); },
        refreshOnce() {},
        clear() {},
        release() { overlayReleases += 1; },
      };
    },
  });

  session.attach(root.root, presentation());
  assert.equal(renderer.callbacks.spatialPolicy, expectedPolicy);
  const relation = snapshot.graph_projection.nodes.find(
    (node) => node.node_type === "relation" && node.relation_kind === "workflow",
  );
  renderer.callbacks.onRelationLabelFrameChange({
    relations: [{ ...relation, x: 12, y: 24, z: 36 }],
    camera: { isCamera: true },
  });

  assert.equal(overlayFrames.at(-1).relations.length, 1);
  assert.equal(overlayFrames.at(-1).selectedId, null);
  assert.equal(overlayFrames.at(-1).presentationFrame.transactionId, "test:1");
  assert.equal(overlayFrames.at(-1).geometryRevision, 1);
  assert.equal(overlayFrames.at(-1).safeInset.geometryRevision, 1);
  assert.equal(renderer.counts.viewportGeometries.length, 1);
  assert.deepEqual(
    renderer.counts.viewportGeometries[0].safeInset,
    overlayFrames.at(-1).safeInset,
  );
  session.syncPresentation(presentation({
    selectedRelationNodeId: relation.node_id,
    hoveredGraphNodeId: relation.node_id,
    graphPresentation: {
      viewMode: "three_d",
      rendererLifecycle: "ready",
      failure: null,
      focusNodeId: relation.node_id,
    },
  }));
  assert.equal(overlayFrames.at(-1).selectedId, relation.node_id);
  assert.equal(overlayFrames.at(-1).focusedId, relation.node_id);
  assert.equal(overlayFrames.at(-1).presentationFrame.transactionId, "test:2");

  session.dispose();
  assert.equal(overlayReleases, 1);
});

test("relation-label collision retargets through the renderer presentation driver", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const frames = [];
  let collisionDetected = false;
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    relationLabelOverlayFactory() {
      return {
        sync(frame) {
          frames.push(frame.presentationFrame);
          if (!collisionDetected) {
            collisionDetected = true;
            return { transitionRequired: true };
          }
          return { transitionRequired: false };
        },
        clear() {},
        release() {},
      };
    },
  });
  session.attach(root.root, presentation());
  const relation = snapshotFixture().graph_projection.nodes[1];

  renderer.callbacks.onRelationLabelFrameChange({
    relations: [{ ...relation, x: 12, y: 24, z: 36 }],
    camera: { isCamera: true },
  });

  assert.deepEqual(renderer.counts.presentationTransitionRequests, [
    "relation-label-collision",
  ]);
  assert.deepEqual(frames.map((frame) => frame.transactionId), ["test:1", "test:2"]);
  session.dispose();
});

test("relation label projector converts the world orb radius into a finite screen radius", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  let projectWorldToScreen = null;
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    relationLabelOverlayFactory(_target, options) {
      projectWorldToScreen = options.projectWorldToScreen;
      return { sync() {}, clear() {}, release() {} };
    },
  });
  session.attach(root.root, presentation());
  const camera = new PerspectiveCamera(50, 960 / 540, 1, 2_000);
  camera.position.set(0, 0, 500);
  camera.lookAt(0, 0, 0);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();

  const projected = projectWorldToScreen(
    { x: 0, y: 0, z: 0, radius: 7.5 },
    camera,
    { top: 0, right: 0, bottom: 0, left: 0 },
  );

  assert.deepEqual(Object.keys(projected).sort(), ["radiusPx", "visible", "x", "y"]);
  assert.ok(Number.isFinite(projected.radiusPx) && projected.radiusPx > 0);
  assert.ok(Math.abs(projected.x - 480) < 1e-9);
  assert.ok(Math.abs(projected.y - 270) < 1e-9);
  session.dispose();
});

test("HUD and scene resize share one geometry observer so font reflow refreshes the safe inset", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const observed = [];
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    resizeObserverFactory() {
      return {
        observe(target) { observed.push(target); },
        disconnect() {},
      };
    },
  });

  session.attach(root.root, presentation());

  assert.deepEqual(observed, [root.scene, root.hud]);
  assert.equal(root.overlay.style.insetBlockStart, "92px");
  session.dispose();
});

test("zero and non-finite viewport resize invalidates the renderer camera geometry", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  let resize = null;
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    resizeObserverFactory(callback) {
      resize = callback;
      return { observe() {}, disconnect() {} };
    },
  });
  session.attach(root.root, presentation());
  const geometryCount = renderer.counts.viewportGeometries.length;

  root.viewport.getBoundingClientRect = () => ({
    x: 0, y: 0, left: 0, top: 0, width: 0, height: 540,
  });
  resize();
  assert.equal(renderer.counts.viewportGeometries.length, geometryCount + 1);
  assert.equal(renderer.counts.viewportGeometries.at(-1), null);

  root.viewport.getBoundingClientRect = () => ({
    x: 0, y: 0, left: 0, top: 0, width: Number.POSITIVE_INFINITY, height: 540,
  });
  resize();
  assert.equal(renderer.counts.viewportGeometries.length, geometryCount + 2);
  assert.equal(renderer.counts.viewportGeometries.at(-1), null);
  session.dispose();
});

test("explicit Workflow overview focus reaches the renderer while presentation sync stays camera-neutral", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });
  session.attach(root.root, presentation());

  session.syncPresentation(presentation({
    selectedRelationNodeId: "workflow:harnesskit.workflow.review",
    selectedWorkflowId: "harnesskit.workflow.review",
  }));
  assert.deepEqual(renderer.counts.focusNode, []);

  session.focusNode("workflow:harnesskit.workflow.review");

  assert.deepEqual(renderer.counts.focusNode, ["workflow:harnesskit.workflow.review"]);
  assert.equal(session.restoreCamera(), true);
  assert.equal(renderer.counts.restoreCamera, 1);
  session.dispose();
});

test("camera scale output reports the bounded 3D level and disables the reached zoom boundary", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });
  session.attach(root.root, presentation());

  renderer.callbacks.onCameraScaleChange({
    scale: 8,
    minScale: 0.75,
    maxScale: 8,
    level: "maximum",
  });
  assert.equal(root.scale.textContent, "8× · 최대");
  assert.equal(root.controls[2].disabled, true);
  assert.equal(root.controls[0].disabled, false);

  renderer.callbacks.onCameraScaleChange({
    scale: 0.75,
    minScale: 0.75,
    maxScale: 8,
    level: "overview",
  });
  assert.equal(root.scale.textContent, "0.75× · 개요");
  assert.equal(root.controls[0].disabled, true);
  assert.equal(root.controls[2].disabled, false);
  session.dispose();
});

test("renderer node callbacks become typed reducer actions and expose human identity in the viewport overlay", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const actions = [];
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    onAction: (action) => actions.push(action),
  });
  session.attach(root.root, presentation());
  assert.deepEqual(actions, [{ type: "graph_renderer_ready" }]);
  actions.length = 0;

  const component = snapshotFixture().graph_projection.nodes[2];
  const workflow = snapshotFixture().graph_projection.nodes[1];
  renderer.callbacks.onNodeHover(component.node_id, component);
  assert.equal(root.overlay.hidden, false);
  assert.equal(root.overlay.dataset.identityState, "idle");
  assert.equal(root.title.textContent, "Reviewer");
  assert.equal(root.kind.textContent, "AGENT");
  assert.match(root.count.textContent, /2/);
  assert.doesNotMatch(
    [root.title.textContent, root.kind.textContent, root.count.textContent].join(" "),
    /harnesskit\./,
  );
  renderer.callbacks.onNodeSelect(workflow);
  renderer.callbacks.onBackgroundSelect();

  assert.deepEqual(renderer.counts.focusNode, [workflow.node_id]);
  assert.deepEqual(renderer.counts.focusOptions, [{ intent: "contextual" }]);

  assert.deepEqual(actions, [
    { type: "hover_graph_node", nodeId: component.node_id },
    {
      type: "select_graph_relation",
      nodeId: workflow.node_id,
      relationKind: "workflow",
      canonicalId: "harnesskit.workflow.review",
    },
    { type: "clear_graph_selection" },
  ]);
  session.dispose();
});

test("identity overlay reserves no empty space and relation identity uses name, kind badge, and count", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    motionQuery: { matches: true },
  });
  session.attach(root.root, presentation({ activeProfileId: null }));

  assert.equal(root.overlay.hidden, true);

  const workflow = snapshotFixture().graph_projection.nodes[1];
  session.syncPresentation(presentation({
    activeProfileId: null,
    selectedRelationNodeId: workflow.node_id,
    selectedWorkflowId: workflow.canonical_id,
    graphPresentation: {
      viewMode: "three_d",
      rendererLifecycle: "ready",
      failure: null,
      focusNodeId: workflow.node_id,
    },
  }));

  assert.equal(root.overlay.hidden, false);
  assert.equal(root.overlay.dataset.identityState, "focused");
  assert.equal(root.title.textContent, "Review");
  assert.equal(root.kind.textContent, "WORKFLOW");
  assert.match(root.count.textContent, /1/);
  assert.doesNotMatch(
    [root.title.textContent, root.kind.textContent, root.count.textContent].join(" "),
    /harnesskit\./,
  );

  session.syncPresentation(presentation({ activeProfileId: null }));
  assert.equal(root.overlay.hidden, true);
  assert.equal(root.overlay.dataset.identityState, "idle");
  session.dispose();
});

test("selection updates preserve the viewport identity overlay and its content elements", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });
  session.attach(root.root, presentation({ activeProfileId: null }));
  const overlay = root.root.querySelector("[data-graph-identity-overlay]");
  const title = root.root.querySelector("[data-graph-identity-title]");
  const workflow = snapshotFixture().graph_projection.nodes[1];

  session.syncPresentation(presentation({
    activeProfileId: null,
    selectedRelationNodeId: workflow.node_id,
  }));
  session.syncPresentation(presentation({
    activeProfileId: null,
    selectedComponentId: "harnesskit.agent.reviewer",
  }));

  assert.equal(root.root.querySelector("[data-graph-identity-overlay]"), overlay);
  assert.equal(root.root.querySelector("[data-graph-identity-title]"), title);
  assert.equal(overlay.dataset.identityState, "selected");
  assert.equal(title.textContent, "Reviewer");
  session.dispose();
});

test("identity presentation follows exact renderer progress and retargets from the current value", () => {
  const renderer = rendererHarness({ autoPresentationFrames: false });
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });
  session.attach(root.root, presentation({ activeProfileId: null }));
  const overlay = root.overlay;
  const workflow = snapshotFixture().graph_projection.nodes[1];
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:initial", 1));
  assert.equal(overlay.hidden, true);
  assert.equal(overlay.getAttribute("aria-hidden"), "true");

  session.syncPresentation(presentation({
    activeProfileId: null,
    selectedRelationNodeId: workflow.node_id,
  }));
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:enter", 0));
  assert.equal(overlay.hidden, false);
  assert.equal(overlay.getAttribute("aria-hidden"), "false");
  assert.equal(overlay.dataset.identityPresence, "entering");
  assert.equal(overlay.style["--graph-identity-opacity"], "0");
  assert.equal(overlay.style["--graph-identity-emphasis"], "0");
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:enter", 0.5));
  assert.equal(overlay.style["--graph-identity-opacity"], "0.5");
  assert.equal(overlay.style["--graph-identity-emphasis"], "0.5");
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:enter", 1));
  assert.equal(overlay.dataset.identityPresence, "visible");

  session.syncPresentation(presentation({ activeProfileId: null }));
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:exit", 0));
  assert.equal(overlay.hidden, false);
  assert.equal(overlay.getAttribute("aria-hidden"), "true");
  assert.equal(overlay.dataset.identityPresence, "exiting");
  assert.equal(root.title.textContent, "Review");
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:exit", 0.5));
  assert.equal(overlay.hidden, false);
  assert.equal(overlay.style["--graph-identity-opacity"], "0.5");

  session.syncPresentation(presentation({
    activeProfileId: null,
    selectedComponentId: "harnesskit.agent.reviewer",
  }));
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:retarget", 0));
  assert.equal(root.root.querySelector("[data-graph-identity-overlay]"), overlay);
  assert.equal(overlay.hidden, false);
  assert.equal(overlay.getAttribute("aria-hidden"), "false");
  assert.equal(overlay.dataset.identityPresence, "entering");
  assert.equal(root.title.textContent, "Reviewer");
  assert.equal(overlay.style["--graph-identity-opacity"], "0.5");
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:retarget", 0.5));
  assert.equal(overlay.style["--graph-identity-opacity"], "0.75");
  assert.equal(overlay.style["--graph-identity-emphasis"], "0.75");
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:retarget", 1));
  assert.equal(overlay.dataset.identityPresence, "visible");

  session.syncPresentation(presentation({ activeProfileId: null }));
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:final-exit", 0.5));
  assert.equal(overlay.hidden, false);
  assert.equal(root.title.textContent, "Reviewer");
  assert.equal(overlay.style["--graph-identity-opacity"], "0.5");
  renderer.callbacks.onPresentationFrameChange(presentationFrame("identity:final-exit", 1));
  assert.equal(overlay.hidden, true);
  assert.equal(overlay.dataset.identityPresence, "hidden");
  assert.equal(root.title.textContent, "");
  session.dispose();
});

test("the renderer reduced-motion completion frame finishes identity exit immediately", () => {
  const renderer = rendererHarness({ autoPresentationFrames: false });
  const root = rootHarness();
  let motionChange = null;
  const motionQuery = {
    matches: false,
    addEventListener(_type, listener) { motionChange = listener; },
    removeEventListener() { motionChange = null; },
  };
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    motionQuery,
  });
  session.attach(root.root, presentation({ activeProfileId: null }));
  session.syncPresentation(presentation({
    activeProfileId: null,
    selectedComponentId: "harnesskit.agent.reviewer",
  }));
  renderer.callbacks.onPresentationFrameChange(presentationFrame("reduced:enter", 1));
  session.syncPresentation(presentation({ activeProfileId: null }));
  renderer.callbacks.onPresentationFrameChange(presentationFrame("reduced:exit", 0));
  assert.equal(root.overlay.hidden, false);
  assert.equal(root.overlay.dataset.identityPresence, "exiting");

  motionChange({ matches: true });
  renderer.callbacks.onPresentationFrameChange(presentationFrame("reduced:exit", 1, true));

  assert.equal(root.overlay.hidden, true);
  assert.equal(root.overlay.getAttribute("aria-hidden"), "true");
  assert.equal(root.overlay.dataset.identityPresence, "hidden");
  assert.equal(root.title.textContent, "");
  session.dispose();
  assert.equal(motionChange, null);
});

test("focused Component identity remains authoritative over an unrelated graph hover", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });
  session.attach(root.root, presentation({
    activeProfileId: null,
    selectedComponentId: "harnesskit.agent.reviewer",
    graphPresentation: {
      viewMode: "three_d",
      rendererLifecycle: "ready",
      failure: null,
      focusNodeId: "component:harnesskit.agent.reviewer",
    },
  }));
  const workflow = snapshotFixture().graph_projection.nodes[1];
  renderer.callbacks.onNodeHover(workflow.node_id, workflow);

  assert.equal(root.overlay.hidden, false);
  assert.equal(root.overlay.dataset.identityState, "focused");
  assert.equal(root.title.textContent, "Reviewer");
  assert.equal(root.kind.textContent, "AGENT");
  session.dispose();
});

test("WebGL ready keeps the keyboard and screen-reader semantic mirror available beside a decorative scene", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });

  session.attach(root.root, presentation());

  assert.equal(root.scene.hidden, false);
  assert.equal(root.scene.getAttribute("aria-hidden"), "true");
  assert.equal(root.semantic.hidden, false);
  assert.equal(root.semantic.dataset.rendererAvailability, "ready");
  assert.match(root.semantic.innerHTML, /Component Map 키보드 탐색 목록/);
  assert.match(root.semantic.innerHTML, /<strong>Reviewer<\/strong>/);
  assert.match(root.semantic.innerHTML, /class="component-kind-badge"[^>]*>AGENT<\/span>/);
  assert.match(root.semantic.innerHTML, /aria-label="Reviewer, Agent, 2 relations"/);
  assert.equal(
    [...root.semantic.innerHTML.matchAll(/\saria-label="([^"]*)"/g)]
      .some((match) => match[1].includes("harnesskit.")),
    false,
  );
  assert.doesNotMatch(root.semantic.innerHTML, /data-graph-retry/);
  session.dispose();
});

test("active Profile-only changes refresh the semantic identity marker", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });

  session.attach(root.root, presentation());
  assert.match(
    root.semantic.innerHTML,
    /id="component-map-active-profile:harnesskit\.profile\.engineering"/,
  );

  session.syncPresentation(presentation({
    activeProfileId: "harnesskit.profile.work",
  }));

  assert.match(
    root.semantic.innerHTML,
    /id="component-map-active-profile:harnesskit\.profile\.work"/,
  );
  assert.doesNotMatch(
    root.semantic.innerHTML,
    /id="component-map-active-profile:harnesskit\.profile\.engineering"/,
  );
  session.dispose();
});

test("settled scene identity refreshes the semantic mirror without exposing a runtime evidence API", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });

  session.attach(root.root, presentation());
  assert.doesNotMatch(root.semantic.innerHTML, /data-graph-settled-node-positions-hash/);

  renderer.callbacks.onSceneLayoutIdentityChange({
    projectionId: "projection-session",
    settledNodePositionsHash: "0123456789abcdef",
  });

  assert.match(
    root.semantic.innerHTML,
    /id="component-map-projection:projection-session"[^>]*data-graph-projection-id/,
  );
  assert.match(
    root.semantic.innerHTML,
    /id="component-map-settled-hash:0123456789abcdef"[^>]*data-graph-settled-node-positions-hash/,
  );
  assert.equal("runtimeEvidenceSnapshot" in session, false);

  renderer.callbacks.onSceneLayoutIdentityChange({
    projectionId: "projection-other",
    settledNodePositionsHash: "fedcba9876543210",
  });
  assert.doesNotMatch(root.semantic.innerHTML, /data-graph-projection-id/);
  assert.doesNotMatch(root.semantic.innerHTML, /data-graph-settled-node-positions-hash/);
  session.dispose();
});

test("initial renderer pending keeps the semantic mirror inert until it is ready", () => {
  const renderer = rendererHarness({ attachAvailability: "pending" });
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });

  session.attach(root.root, presentation());

  assert.equal(root.semantic.inert, true);
  assert.equal(root.semantic.getAttribute("aria-hidden"), "true");
  assert.match(root.semantic.innerHTML, /Component Map 준비 중/);
  assert.doesNotMatch(root.semantic.innerHTML, /data-graph-retry/);
  renderer.callbacks.onRendererStateChange({ availability: "ready", reason: null });
  assert.equal(root.semantic.inert, false);
  assert.equal(root.semantic.getAttribute("aria-hidden"), "false");
  session.dispose();
});

test("settled Fit report is exposed through the existing product semantic graph surface", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });
  session.attach(root.root, presentation());

  renderer.callbacks.onSceneFitReportChange({
    identityCount: 3,
    relationCount: 2,
    componentCount: 1,
    envelopeCount: 3,
    sceneBounds: {
      min: { x: -100, y: -20, z: -4 },
      max: { x: 100, y: 20, z: 5.2 },
    },
    camera: {
      status: "complete",
      inFrustumEnvelopeCount: 3,
      totalEnvelopeCount: 3,
      largestDimensionOccupancy: 0.82,
    },
  });

  assert.match(root.semantic.innerHTML, /data-graph-fit-report/);
  assert.match(root.semantic.innerHTML, /identity 3개/);
  assert.match(root.semantic.innerHTML, /camera envelope 3\/3/);
  assert.match(root.semantic.innerHTML, /최대 축 사용 82%/);
  assert.equal("runtimeEvidenceSnapshot" in session, false);
  session.dispose();
});

test("camera frames preserve the semantic tree and focus while terminal pose updates in place", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  let semanticMarkup = "";
  let semanticWrites = 0;
  let replacementControl = null;
  Object.defineProperty(root.semantic, "innerHTML", {
    configurable: true,
    get() { return semanticMarkup; },
    set(value) {
      semanticMarkup = value;
      semanticWrites += 1;
      if (root.semantic.ownerDocument.activeElement) {
        replacementControl = new FakeElement({
          semanticNodeId: "component:harnesskit.agent.reviewer",
        });
        replacementControl.ownerDocument = root.semantic.ownerDocument;
        root.semantic.ownerDocument.activeElement = null;
      }
    },
  });
  root.semantic.contains = (candidate) => candidate === root.semantic.ownerDocument.activeElement;
  root.semantic.querySelectorAll = () => replacementControl ? [replacementControl] : [];
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });
  session.attach(root.root, presentation());
  const writesAfterAttach = semanticWrites;
  const focusedControl = new FakeElement({
    semanticNodeId: "component:harnesskit.agent.reviewer",
  });
  focusedControl.ownerDocument = root.semantic.ownerDocument;
  focusedControl.focus();

  const cameraFrames = [
    {
      progress: 0,
      position: { x: 0, y: 0, z: 500 },
      target: { x: 0, y: 0, z: 0 },
    },
    {
      progress: 0.5,
      position: { x: 26, y: -9, z: 370 },
      target: { x: 2, y: 2.5, z: 3 },
    },
    {
      progress: 1,
      position: { x: 52, y: -18, z: 240 },
      target: { x: 4, y: 5, z: 6 },
    },
  ];
  cameraFrames.forEach(({ progress, position, target }) => {
    renderer.callbacks.onCameraPoseChange({ position, target }, { progress });
  });

  assert.equal(semanticWrites, writesAfterAttach);
  assert.equal(root.semantic.ownerDocument.activeElement, focusedControl);
  assert.equal(
    root.cameraPose.getAttribute("aria-label"),
    "Camera pose; position=52.000000,-18.000000,240.000000; target=4.000000,5.000000,6.000000",
  );
  assert.equal(root.cameraPose.getAttribute("aria-hidden"), "false");

  session.syncPresentation(presentation({
    hoveredGraphNodeId: "component:harnesskit.agent.reviewer",
  }));
  renderer.callbacks.onCameraScaleChange({
    scale: 2,
    minScale: 0.75,
    maxScale: 8,
    level: "detail",
  });
  assert.equal(semanticWrites, writesAfterAttach);

  session.syncPresentation(presentation({
    selectedComponentId: "harnesskit.agent.reviewer",
  }));
  assert.equal(semanticWrites, writesAfterAttach + 1);
  session.dispose();
});

test("semantic Workflow step keyboard focus publishes the same hover action as the 3D scene", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const actions = [];
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    onAction: (action) => actions.push(action),
  });
  session.attach(root.root, presentation());
  assert.deepEqual(actions, [{ type: "graph_renderer_ready" }]);
  actions.length = 0;
  const stepControl = {
    dataset: {
      semanticWorkflowStep: "1",
      workflowId: "harnesskit.workflow.review",
    },
  };
  const stepLabel = {
    closest(selector) {
      return selector === "[data-semantic-workflow-step]" ? stepControl : null;
    },
  };

  root.semantic.dispatch("focusin", stepLabel);

  assert.deepEqual(actions, [{
    type: "hover_workflow_step",
    step: { workflowId: "harnesskit.workflow.review", ordinal: 1 },
  }]);
  session.dispose();
});

test("WebGL failure promotes the visible semantic surface and retry uses the same snapshot session", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const stateChanges = [];
  const actions = [];
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    onAction: (action) => actions.push(action),
    onRendererStateChange: (state) => stateChanges.push(state),
  });
  session.attach(root.root, presentation());

  renderer.callbacks.onRendererStateChange({
    availability: "unavailable",
    reason: "webgl_unavailable",
  });
  assert.equal(root.scene.hidden, true);
  assert.equal(root.semantic.hidden, false);
  assert.match(root.semantic.innerHTML, /3D Component Map 대체 목록/);
  assert.match(root.semantic.innerHTML, /WebGL을 사용할 수 없어 텍스트 보기로 전환했습니다\./);
  assert.equal(root.status.hidden, false);
  assert.deepEqual(stateChanges.at(-1), {
    availability: "unavailable",
    reason: "WebGL을 사용할 수 없어 텍스트 보기로 전환했습니다.",
  });
  assert.deepEqual(actions.at(-1), {
    type: "graph_renderer_failed",
    failure: "WebGL을 사용할 수 없어 텍스트 보기로 전환했습니다.",
  });

  session.retry();
  assert.equal(renderer.counts.retry, 1);
  assert.equal(root.scene.hidden, false);
  assert.equal(root.semantic.hidden, false);
  assert.equal(root.semantic.dataset.rendererAvailability, "ready");
  assert.equal(actions.at(-1)?.type, "graph_renderer_ready");
  session.dispose();
});

test("semantic fallback stays primary while a renderer retry is pending", () => {
  const renderer = rendererHarness({ retryAvailability: "pending" });
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });
  session.attach(root.root, presentation());
  renderer.callbacks.onRendererStateChange({
    availability: "context_lost",
    reason: "webglcontextlost",
  });

  session.retry();

  assert.equal(root.scene.hidden, true);
  assert.equal(root.semantic.dataset.rendererAvailability, "context_lost");
  assert.match(root.semantic.innerHTML, /3D Component Map 대체 목록/);
  renderer.callbacks.onRendererStateChange({ availability: "ready", reason: null });
  assert.equal(root.scene.hidden, false);
  assert.equal(root.semantic.dataset.rendererAvailability, "ready");
  session.dispose();
});

test("failed renderer retry hands keyboard focus back to the semantic surface", () => {
  const renderer = rendererHarness({ retryAvailability: "unavailable" });
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });
  session.attach(root.root, presentation());
  renderer.callbacks.onRendererStateChange({
    availability: "context_lost",
    reason: "webglcontextlost",
  });
  root.semantic.focused = false;

  session.retry();

  assert.equal(root.scene.hidden, true);
  assert.equal(root.semantic.focused, true);
  assert.match(root.semantic.innerHTML, /3D 다시 시도/);
  session.dispose();
});

test("manual text mode returns to 3D with the same selection instead of presenting failure retry", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const stateChanges = [];
  const actions = [];
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    onAction: (action) => actions.push(action),
    onRendererStateChange: (state) => stateChanges.push(state),
  });
  const currentPresentation = presentation({
    activeProfileId: "harnesskit.profile.engineering",
    selectedComponentId: "harnesskit.agent.reviewer",
    selectedRelationNodeId: "workflow:harnesskit.workflow.review",
    selectedWorkflowId: "harnesskit.workflow.review",
    hoveredWorkflowStep: { workflowId: "harnesskit.workflow.review", ordinal: 1 },
    lockedWorkflowStep: { workflowId: "harnesskit.workflow.review", ordinal: 1 },
  });

  session.attach(root.root, currentPresentation);
  assert.equal(root.semanticView.hidden, false);
  assert.equal(root.semanticView.disabled, false);

  root.semanticView.dispatch("click");

  assert.equal(renderer.counts.showSemanticFallback, 1);
  assert.equal(root.scene.hidden, true);
  assert.equal(root.semantic.dataset.rendererAvailability, "manual_fallback");
  assert.match(root.semantic.innerHTML, /사용자가 텍스트 보기를 선택했습니다\./);
  assert.match(root.semantic.innerHTML, /3D 그래프로 돌아가기/);
  assert.doesNotMatch(root.semantic.innerHTML, /3D 다시 시도/);
  assert.equal(root.semanticView.hidden, true);
  assert.equal(root.semanticView.disabled, true);
  assert.equal(root.semantic.focused, true);
  assert.deepEqual(stateChanges.at(-1), {
    availability: "manual_fallback",
    reason: "사용자가 텍스트 보기를 선택했습니다.",
  });
  assert.equal(renderer.counts.setGraphData, 1);
  assert.deepEqual(renderer.counts.settle, [180]);
  assert.equal(actions.at(-1)?.type, "show_graph_semantic");

  root.semanticView.focused = false;
  const actionCountBeforeReturn = actions.length;
  root.semantic.dispatch("click", {
    closest(selector) {
      return selector === "[data-graph-return]" ? this : null;
    },
  });
  assert.equal(renderer.counts.retry, 1);
  assert.equal(renderer.counts.captureRecoveryCapsule, 1);
  assert.deepEqual(renderer.counts.restoredRecoveryCapsules, [renderer.recoveryCapsule]);
  assert.equal(renderer.counts.restoredRecoveryCapsules[0].projectionId, "projection-session");
  assert.ok(renderer.counts.restoredRecoveryCapsules[0].settledAnchorMap instanceof Map);
  assert.equal(
    renderer.counts.restoredRecoveryCapsules[0].restoreBaseline.focusIntent,
    "contextual",
  );
  assert.equal(root.scene.hidden, false);
  assert.equal(root.semanticView.focused, true);
  assert.deepEqual(
    actions.slice(actionCountBeforeReturn).map((action) => action.type),
    ["return_graph_three_d", "graph_renderer_ready"],
  );
  session.dispose();
});

test("context-loss retry consumes the matching scene recovery capsule before renderer retry", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
  });
  session.attach(root.root, presentation());

  renderer.callbacks.onRendererStateChange({
    availability: "context_lost",
    reason: "webglcontextlost",
  });
  session.retry();

  assert.equal(renderer.counts.captureRecoveryCapsule, 1);
  assert.equal(renderer.counts.restoredRecoveryCapsules.length, 1);
  assert.equal(renderer.counts.restoredRecoveryCapsules[0].projectionId, "projection-session");
  session.dispose();
});

test("host resize observation resizes the settled renderer without replacing the scene", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  let resizeCallback = null;
  let disconnects = 0;
  const session = createComponentMapSession({
    snapshot: snapshotFixture(),
    rendererFactory: renderer.rendererFactory,
    resizeObserverFactory(callback) {
      resizeCallback = callback;
      return {
        disconnect() { disconnects += 1; },
        observe() {},
      };
    },
  });
  session.attach(root.root, presentation());
  const initialResizeCount = renderer.counts.resize;

  resizeCallback();

  assert.equal(renderer.counts.resize, initialResizeCount + 1);
  assert.equal(renderer.counts.setGraphData, 1);
  assert.deepEqual(renderer.counts.settle, [180]);
  session.detach();
  assert.equal(disconnects, 1);
  session.dispose();
});

test("production component map session never registers or exposes a runtime evidence bridge", () => {
  const renderer = rendererHarness();
  const root = rootHarness();
  const registrations = [];
  const previousBridge = globalThis.__HARNESS_RUNTIME_EVIDENCE__;
  globalThis.__HARNESS_RUNTIME_EVIDENCE__ = {
    protocol: "harnesskit-runtime-graph-v1",
    registerGraphSession(value) { registrations.push(value); },
  };
  try {
    const session = createComponentMapSession({
      snapshot: snapshotFixture(),
      rendererFactory: renderer.rendererFactory,
    });
    session.attach(root.root, presentation());

    assert.equal(registrations.length, 0);
    assert.equal("runtimeEvidenceCommand" in session, false);
    assert.equal("runtimeEvidenceSnapshot" in session, false);
    session.dispose();
  } finally {
    if (previousBridge === undefined) delete globalThis.__HARNESS_RUNTIME_EVIDENCE__;
    else globalThis.__HARNESS_RUNTIME_EVIDENCE__ = previousBridge;
  }
});
