import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

const {
  MAX_CAMERA_SCALE,
  MIN_CAMERA_SCALE,
  createBoundedCollisionForce,
  createSceneFitCameraPose,
  createSceneFitReport,
  createThreeGraphRenderer: createRawThreeGraphRenderer,
  prepareSceneGraph,
  stabilizeSettledSceneGraph,
} = await import("../graph/component-map.js");
const { createSphericalLayoutMetrics } = await import("../graph/spherical-layout.js");
const {
  graphNodeLayoutEnvelope,
  graphNodeVisualEnvelope,
} = await import("../graph/visual-metrics.js");
const { graphSpatialPolicyFor } = await import("../graph/spatial-policy.js");

function sceneFixture() {
  return {
    schema_version: 2,
    projection_id: "projection-component-map",
    layout_seed: "atlas-seed-a",
    nodes: [
      {
        node_type: "relation",
        node_id: "profile:harnesskit.profile.engineering",
        relation_kind: "Profile",
        canonical_id: "harnesskit.profile.engineering",
        anchor_ordinal: 0,
      },
      {
        node_type: "relation",
        node_id: "workflow:harnesskit.workflow.spec-to-tdd",
        relation_kind: "Workflow",
        canonical_id: "harnesskit.workflow.spec-to-tdd",
        anchor_ordinal: 0,
      },
      {
        node_type: "component",
        node_id: "component:harnesskit.agent.writer",
        component_id: "harnesskit.agent.writer",
        kind: "agent",
      },
    ],
    links: [
      {
        semantic: "profile-membership",
        link_id: "profile:writer",
        profile_node_id: "profile:harnesskit.profile.engineering",
        component_node_id: "component:harnesskit.agent.writer",
        source_node_id: "profile:harnesskit.profile.engineering",
        target_node_id: "component:harnesskit.agent.writer",
        directionality: "unordered",
      },
      {
        semantic: "workflow-step",
        link_id: "workflow:writer",
        workflow_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
        component_node_id: "component:harnesskit.agent.writer",
        source_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
        target_node_id: "component:harnesskit.agent.writer",
        directionality: "directed",
        occurrences: [{ ordinal: 1 }],
      },
    ],
  };
}

function prepareScene(scene, layoutSeed) {
  scene.layout_seed = layoutSeed;
  return prepareSceneGraph(scene, graphSpatialPolicyFor(scene));
}

function createThreeGraphRenderer(options = {}) {
  return createRawThreeGraphRenderer({
    spatialPolicy: graphSpatialPolicyFor(sceneFixture()),
    ...options,
  });
}

test("stable seed prepares one dense spherical volume with fixed reusable anchors", () => {
  const first = prepareScene(structuredClone(sceneFixture()), "atlas-seed-a");
  const second = prepareScene(structuredClone(sceneFixture()), "atlas-seed-a");
  const different = prepareScene(structuredClone(sceneFixture()), "atlas-seed-b");

  assert.deepEqual(first.nodes.map(({ x, y, z, fx, fy, fz }) => ({ x, y, z, fx, fy, fz })),
    second.nodes.map(({ x, y, z, fx, fy, fz }) => ({ x, y, z, fx, fy, fz })));
  assert.ok(first.nodes.every((node) => node.x === node.fx
    && node.y === node.fy
    && node.z === node.fz));
  assert.ok(first.nodes.every((node) => !("shell" in node)));
  const metrics = createSphericalLayoutMetrics(first.nodes);
  assert.ok(Math.max(...Object.values(metrics.spans)) > 0);
  assert.ok(first.nodes.every((node) => Math.hypot(node.x, node.y, node.z) <= 72));
  assert.notDeepEqual(
    { x: first.nodes[2].x, y: first.nodes[2].y, z: first.nodes[2].z },
    { x: different.nodes[2].x, y: different.nodes[2].y, z: different.nodes[2].z },
  );
  assert.deepEqual(first.links.map(({ source, target }) => ({ source, target })), [
    {
      source: "profile:harnesskit.profile.engineering",
      target: "component:harnesskit.agent.writer",
    },
    {
      source: "workflow:harnesskit.workflow.spec-to-tdd",
      target: "component:harnesskit.agent.writer",
    },
  ]);
});

test("component cross-links resolve canonical component identities to graph node endpoints", () => {
  const source = {
    nodes: [
      {
        node_type: "component",
        node_id: "component:harnesskit.composite.atlassian-acli",
        component_id: "harnesskit.composite.atlassian-acli",
        kind: "composite",
      },
      {
        node_type: "component",
        node_id: "component:harnesskit.skill.acli-gateway",
        component_id: "harnesskit.skill.acli-gateway",
        kind: "skill",
      },
    ],
    links: [
      {
        semantic: "component-cross-link",
        link_id: "component-cross-link:composite_member:harnesskit.composite.atlassian-acli:harnesskit.skill.acli-gateway:members[0]",
        source_component_id: "harnesskit.composite.atlassian-acli",
        target_component_id: "harnesskit.skill.acli-gateway",
        relation_type: "composite_member",
        source_node_id: "component:harnesskit.composite.atlassian-acli",
        target_node_id: "component:harnesskit.skill.acli-gateway",
        directionality: "directed",
      },
    ],
  };
  const graph = prepareScene(source, "atlas-seed-cross-link");

  assert.deepEqual(graph.links.map(({ source, target }) => ({ source, target })), [
    {
      source: "component:harnesskit.composite.atlassian-acli",
      target: "component:harnesskit.skill.acli-gateway",
    },
  ]);
});

test("Unprofiled is one ordinary relation anchor in the shared spherical volume", () => {
  const source = {
    nodes: [
      {
        node_type: "relation",
        node_id: "profile:engineering",
        relation_kind: "profile",
        anchor_ordinal: 1,
      },
      {
        node_type: "relation",
        node_id: "profile:unprofiled",
        relation_kind: "unprofiled",
        anchor_ordinal: 2,
      },
    ],
    links: [],
  };
  const graph = prepareScene(source, "seed");

  assert.equal(graph.nodes.length, 2);
  assert.ok(graph.nodes.every((node) => node.x === node.fx
    && node.y === node.fy
    && node.z === node.fz));
  assert.notDeepEqual(
    graph.nodes.slice(0, 1).map(({ x, y, z }) => ({ x, y, z })),
    graph.nodes.slice(1).map(({ x, y, z }) => ({ x, y, z })),
  );
});

test("scene Fit report binds exact typed identity counts to envelope bounds and camera inclusion", () => {
  const scene = structuredClone(sceneFixture());
  Object.assign(scene.nodes[0], { x: -100, y: 0, z: 0 });
  Object.assign(scene.nodes[1], { x: 100, y: 0, z: 0 });
  Object.assign(scene.nodes[2], { x: 0, y: 20, z: 0 });
  const camera = new THREE.PerspectiveCamera(50, 2, 1, 2_000);
  camera.position.set(0, 0, 500);
  camera.lookAt(0, 0, 0);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  const spatialPolicy = graphSpatialPolicyFor(scene);

  const report = createSceneFitReport(scene, camera, null, spatialPolicy);

  assert.equal(report.identityCount, 3);
  assert.equal(report.relationCount, 2);
  assert.equal(report.componentCount, 1);
  assert.equal(report.envelopeCount, 3);
  assert.equal(Object.isFrozen(report), true);
  assert.equal(Object.isFrozen(report.sceneBounds), true);
  assert.equal(Object.isFrozen(report.sceneBounds.min), true);
  assert.equal(Object.isFrozen(report.sceneBounds.max), true);
  assert.equal(Object.isFrozen(report.camera), true);
  const relationEnvelope = graphNodeVisualEnvelope(scene.nodes[0], spatialPolicy);
  const relationEnvelopeHalfWidth = (relationEnvelope.max.x - relationEnvelope.min.x) / 2;
  assert.ok(Math.abs(report.sceneBounds.min.x - (-100 - relationEnvelopeHalfWidth)) < 1e-9);
  assert.ok(Math.abs(report.sceneBounds.max.x - (100 + relationEnvelopeHalfWidth)) < 1e-9);
  assert.deepEqual(
    { inFrustum: report.camera.inFrustumEnvelopeCount,
      status: report.camera.status,
      total: report.camera.totalEnvelopeCount },
    { inFrustum: 3, status: "complete", total: 3 },
  );
  assert.ok(report.camera.largestDimensionOccupancy > 0);

  camera.position.set(0, 0, 30);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  const cropped = createSceneFitReport(scene, camera, null, spatialPolicy);
  assert.equal(cropped.camera.status, "cropped");
  assert.ok(cropped.camera.inFrustumEnvelopeCount < cropped.camera.totalEnvelopeCount);
});

test("scene Fit bounds use the idle outline envelope rather than the selection halo", () => {
  const scene = {
    nodes: [{
      node_type: "component",
      node_id: "component:harnesskit.skill.tdd",
      component_id: "harnesskit.skill.tdd",
      kind: "skill",
      x: 0,
      y: 0,
      z: 0,
    }],
    links: [],
  };
  const spatialPolicy = graphSpatialPolicyFor(scene);

  const report = createSceneFitReport(scene, null, null, spatialPolicy);
  const envelope = graphNodeVisualEnvelope(scene.nodes[0], spatialPolicy);
  const expectedRadius = (envelope.max.x - envelope.min.x) / 2;

  for (const axis of ["x", "y", "z"]) {
    assert.ok(Math.abs(report.sceneBounds.min[axis] + expectedRadius) < 1e-9);
    assert.ok(Math.abs(report.sceneBounds.max[axis] - expectedRadius) < 1e-9);
  }
});

test("scene Fit bounds use the 3D relation orb while DOM labels stay outside world geometry", () => {
  const scene = {
    nodes: [{
      node_type: "relation",
      node_id: "workflow:harnesskit.workflow.harness-creation",
      relation_kind: "workflow",
      size_scale: 1.2,
      x: 0,
      y: 0,
      z: 0,
    }],
    links: [],
  };
  const spatialPolicy = graphSpatialPolicyFor(scene);

  const report = createSceneFitReport(scene, null, null, spatialPolicy);
  const envelope = graphNodeVisualEnvelope(scene.nodes[0], spatialPolicy);
  const radius = (envelope.max.x - envelope.min.x) / 2;

  for (const axis of ["x", "y", "z"]) {
    assert.ok(Math.abs(report.sceneBounds.min[axis] + radius) < 1e-9);
    assert.ok(Math.abs(report.sceneBounds.max[axis] - radius) < 1e-9);
  }
});

test("collision derives separation from the legacy layout envelope plus one bounded two-unit gap", () => {
  const spatialPolicy = graphSpatialPolicyFor(sceneFixture());
  const relation = {
    node_type: "relation",
    node_id: "workflow:a",
    size_scale: 1.2,
    x: 0,
    y: 0,
    z: 0,
  };
  const outside = {
    node_type: "component",
    node_id: "component:outside",
    x: 0,
    y: 0,
    z: 0,
  };
  const relationEnvelope = graphNodeLayoutEnvelope(relation, spatialPolicy);
  const componentEnvelope = graphNodeLayoutEnvelope(outside, spatialPolicy);
  const relationRadius = (relationEnvelope.max.x - relationEnvelope.min.x) / 2;
  const componentRadius = (componentEnvelope.max.x - componentEnvelope.min.x) / 2;
  const minimumDistance = relationRadius + componentRadius + 2;
  outside.x = minimumDistance + 0.1;
  const noCollision = createBoundedCollisionForce({ spatialPolicy });
  noCollision.initialize([relation, outside]);
  noCollision(1);

  assert.equal(Number(relation.vx) || 0, 0);
  assert.equal(Number(outside.vx) || 0, 0);

  const inside = { ...outside, node_id: "component:inside", x: minimumDistance - 0.1 };
  const collision = createBoundedCollisionForce({ spatialPolicy });
  collision.initialize([{ ...relation }, inside]);
  collision(1);
  assert.ok(Math.abs(Number(inside.vx) || 0) > 0);
});

test("collision impulse strength comes only from the projection spatial policy", () => {
  const base = graphSpatialPolicyFor(sceneFixture());
  const spatialPolicy = Object.freeze({
    ...base,
    collision: Object.freeze({ ...base.collision, strength: 0 }),
  });
  const left = {
    node_type: "component",
    node_id: "component:left",
    x: 0,
    y: 0,
    z: 0,
  };
  const right = {
    node_type: "component",
    node_id: "component:right",
    x: 0.1,
    y: 0,
    z: 0,
  };
  const collision = createBoundedCollisionForce({ spatialPolicy });
  collision.initialize([left, right]);
  collision(1);

  assert.equal(Number(left.vx) || 0, 0);
  assert.equal(Number(right.vx) || 0, 0);
});

test("settled layout keeps simulation coordinates unchanged and typed camera Fit keeps every envelope visible", () => {
  const scene = {
    nodes: [
      { node_type: "relation", node_id: "profile:a", x: -150, y: -120, z: -20, fx: -150, fy: -120, fz: -20 },
      { node_type: "relation", node_id: "profile:b", x: -150, y: 120, z: 20, fx: -150, fy: 120, fz: 20 },
      { node_type: "relation", node_id: "workflow:a", x: 150, y: -48, z: -20, fx: 150, fy: -48, fz: -20 },
      { node_type: "relation", node_id: "workflow:b", x: 150, y: 48, z: 20, fx: 150, fy: 48, fz: 20 },
      { node_type: "component", node_id: "component:a", x: -42, y: -238, z: -147 },
      { node_type: "component", node_id: "component:b", x: 42, y: 238, z: 147 },
    ],
    links: [],
  };
  const first = stabilizeSettledSceneGraph(structuredClone(scene));
  const second = stabilizeSettledSceneGraph(structuredClone(scene));
  const spatialPolicy = graphSpatialPolicyFor(first);

  assert.deepEqual(first, second);
  assert.deepEqual(first, scene);

  const camera = new THREE.PerspectiveCamera(50, 1.5, 1, 10_000);
  const pose = createSceneFitCameraPose(first, camera, { spatialPolicy });
  camera.position.set(pose.position.x, pose.position.y, pose.position.z);
  camera.lookAt(pose.target.x, pose.target.y, pose.target.z);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  const report = createSceneFitReport(first, camera, null, spatialPolicy);

  assert.equal(report.camera.status, "complete");
  assert.equal(report.camera.inFrustumEnvelopeCount, first.nodes.length);
  assert.ok(report.camera.largestDimensionOccupancy > 0);
  assert.ok(report.camera.largestDimensionOccupancy <= 0.9);
});

test("typed camera Fit places the dense scene inside the shared HUD safe inset", () => {
  const scene = {
    nodes: [-1, 1].flatMap((x) => [-1, 1].flatMap((y) => [-1, 1].map((z, index) => ({
      node_type: "component",
      node_id: `component:${x}:${y}:${z}:${index}`,
      kind: "skill",
      x: x * 40,
      y: y * 40,
      z: z * 40,
    })))),
    links: [],
  };
  const spatialPolicy = graphSpatialPolicyFor(scene);
  const viewportGeometry = {
    viewportRect: { x: 0, y: 0, width: 1000, height: 600 },
    safeInset: {
      geometryRevision: 9,
      top: 100,
      right: 20,
      bottom: 20,
      left: 20,
    },
  };
  const camera = new THREE.PerspectiveCamera(50, 1000 / 600, 1, 10_000);
  const pose = createSceneFitCameraPose(scene, camera, { viewportGeometry, spatialPolicy });

  camera.position.set(pose.position.x, pose.position.y, pose.position.z);
  camera.lookAt(pose.target.x, pose.target.y, pose.target.z);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  const report = createSceneFitReport(scene, camera, viewportGeometry, spatialPolicy);

  assert.equal(report.cropCount, 0);
  assert.ok(report.projectedShortAxisOccupancy >= 0.7);
  assert.ok(report.projectedShortAxisOccupancy <= 0.88);
  assert.equal(report.safeExtent.geometryRevision, 9);
});

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.children = [];
    this.listeners = new Map();
    this.parentNode = null;
    this.dataset = {};
  }
  appendChild(child) {
    child.remove?.();
    this.children.push(child);
    child.parentNode = this;
    return child;
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
  replaceChildren() {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children = [];
  }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  removeEventListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
  }
  setAttribute(name, value) { this[name] = String(value); }
  emit(type, detail = {}) {
    this.listeners.get(type)?.({ preventDefault() {}, ...detail });
  }
}

function rendererHarness() {
  const graphs = [];
  const canvasByGraph = [];
  const cameraDurations = [];
  const calls = [];
  const forceRecords = new Map();
  function forceGraphFactory(container) {
    const canvas = new FakeElement("canvas");
    const controls = new FakeElement("controls");
    controls.target = { x: 0, y: 0, z: 0 };
    let graph = null;
    const webglContext = {
      getExtension(name) {
        if (name !== "WEBGL_lose_context") return null;
        return {
          loseContext() {
            calls.push("WEBGL_lose_context");
            canvas.emit("webglcontextlost");
          },
        };
      },
    };
    const browserRenderer = {
      domElement: canvas,
      getContext() { return webglContext; },
      render() { calls.push("render"); },
    };
    const browserScene = {
      kind: "scene",
      updateMatrixWorld(force) { calls.push(`updateMatrixWorld:${force}`); },
    };
    container.appendChild(canvas);
    canvasByGraph.push(canvas);
    graph = {
      _camera: { x: 0, y: 0, z: 500 },
      _destructor() { calls.push("destructor"); },
      cameraPosition(next, _target, duration) {
        if (!next) return this._camera;
        this._camera = { ...this._camera, ...next };
        cameraDurations.push(duration);
        calls.push("camera");
        return this;
      },
      camera() { return { kind: "camera" }; },
      controls() { return controls; },
      renderer() { return browserRenderer; },
      scene() { return browserScene; },
      zoomToFit(duration, padding) {
        calls.push(`fit:${duration}:${padding}`);
        return this;
      },
    };
    [
      "backgroundColor", "cooldownTicks", "cooldownTime", "d3AlphaDecay",
      "enableNodeDrag", "height", "linkPositionUpdate", "linkSource",
      "linkTarget", "linkThreeObject", "nodeId", "nodeLabel", "nodeThreeObject",
      "onEngineStop", "showNavInfo", "warmupTicks", "width", "forceEngine",
    ].forEach((method) => {
      graph[method] = (value) => {
        graph[`_${method}`] = value;
        calls.push(method);
        return graph;
      };
    });
    graph.d3Force = (name, installedForce) => {
      const record = forceRecords.get(name) ?? {};
      if (installedForce !== undefined) record.installedForce = installedForce;
      forceRecords.set(name, record);
      return {
        distance(value) { record.distance = value; return this; },
        distanceMax(value) { record.distanceMax = value; return this; },
        strength(value) { record.strength = value; return this; },
      };
    };
    graph.graphData = (value) => {
      graph._graphData = value;
      calls.push("graphData");
      return graph;
    };
    graph.pauseAnimation = () => { calls.push("pause"); return graph; };
    graph.resumeAnimation = () => { calls.push("resume"); return graph; };
    graph.refresh = () => { calls.push("refresh"); return graph; };
    graph.onNodeHover = (callback) => { graph._hover = callback; return graph; };
    graph.onNodeClick = (callback) => { graph._click = callback; return graph; };
    graph.onBackgroundClick = (callback) => { graph._background = callback; return graph; };
    graphs.push(graph);
    return graph;
  }
  return { calls, cameraDurations, canvasByGraph, forceGraphFactory, forceRecords, graphs };
}

function objectFactoryHarness(kind, calls) {
  return {
    create(value) { return { kind, value }; },
    dispose() { calls.push(`${kind}:dispose`); },
    syncAll() { calls.push(`${kind}:sync`); },
    updatePosition() { return true; },
  };
}

function settleHarnessGraph(graph, scene = sceneFixture()) {
  graph._nodeThreeObject?.(scene.nodes[0]);
  graph._onEngineStop?.();
}

test("renderer publishes one immutable scene layout identity without a diagnostic command", () => {
  const harness = rendererHarness();
  const identities = [];
  const projection = sceneFixture();
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    onSceneLayoutIdentityChange: (identity) => identities.push(identity),
    spatialPolicy: graphSpatialPolicyFor(projection),
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(projection);

  assert.equal(identities.length, 1);
  assert.deepEqual(Object.keys(identities[0]).sort(), [
    "projectionId",
    "settledNodePositionsHash",
  ]);
  assert.equal(identities[0].projectionId, projection.projection_id);
  assert.match(identities[0].settledNodePositionsHash, /^[0-9a-f]{16}$/);
  assert.equal(Object.isFrozen(identities[0]), true);
  assert.equal("runtimeEvidenceSnapshot" in renderer, false);
  renderer.dispose();
});

test("renderer reports the exact initialization stage when construction fails", () => {
  const rendererStates = [];
  const calls = [];
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory() {
      throw new Error("renderer constructor rejected");
    },
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", calls),
    createLinkObjects: () => objectFactoryHarness("link", calls),
    onRendererStateChange: (state) => rendererStates.push(state),
    spatialPolicy: graphSpatialPolicyFor(sceneFixture()),
  });

  renderer.attach(new FakeElement("section"));

  assert.deepEqual(rendererStates.at(-1), {
    availability: "unavailable",
    canRetry: true,
    reason: "webgl_initialization_failed:create-force-graph:renderer constructor rejected",
  });
  renderer.dispose();
});

test("renderer shares one required projection policy across node, collision, and force consumers", () => {
  const harness = rendererHarness();
  const calls = [];
  const source = sceneFixture();
  const base = graphSpatialPolicyFor(source);
  const spatialPolicy = Object.freeze({
    ...base,
    collision: Object.freeze({ ...base.collision, gap: 19 }),
    force: Object.freeze({
      ...base.force,
      chargeDistanceMaximum: 123,
      chargeStrength: -17,
      componentCrossLinkDistance: 29,
      linkStrength: 0.19,
      relationLinkDistance: 37,
    }),
  });
  let nodeFactoryPolicy = null;
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects(options) {
      nodeFactoryPolicy = options.spatialPolicy;
      return objectFactoryHarness("node", calls);
    },
    createLinkObjects: () => objectFactoryHarness("link", calls),
    spatialPolicy,
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(source);

  assert.equal(nodeFactoryPolicy, spatialPolicy);
  assert.equal(harness.forceRecords.get("charge").strength, -17);
  assert.equal(harness.forceRecords.get("charge").distanceMax, 123);
  assert.equal(harness.forceRecords.get("link").strength, 0.19);
  assert.equal(harness.forceRecords.get("link").distance({ semantic: "component-cross-link" }), 29);
  assert.equal(harness.forceRecords.get("link").distance({ semantic: "workflow-step" }), 37);
  const collision = harness.forceRecords.get("collision").installedForce;
  assert.equal(typeof collision, "function");
  renderer.dispose();
});

test("initial Fit waits until three-force-graph commits node object positions", () => {
  const harness = rendererHarness();
  const scheduledLayoutCommits = [];
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    reducedMotion: true,
    scheduleLayoutCommit(callback) {
      scheduledLayoutCommits.push(callback);
      return scheduledLayoutCommits.length;
    },
    cancelLayoutCommit() {},
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  renderer.pauseAnimation();
  renderer.fitGraph();
  settleHarnessGraph(harness.graphs[0]);

  assert.equal(harness.calls.some((call) => call.startsWith("fit:")), false);
  assert.equal(scheduledLayoutCommits.length, 1);

  scheduledLayoutCommits[0]();

  assert.ok(harness.calls.includes("updateMatrixWorld:true"));
  assert.ok(harness.calls.includes("camera"));
  assert.ok(
    harness.calls.indexOf("updateMatrixWorld:true")
      < harness.calls.indexOf("camera"),
  );
  renderer.dispose();
});

test("Fit and zoom delegate target poses to the interaction camera owner without ActivityController camera transitions", () => {
  const harness = rendererHarness();
  const cameraTransitions = [];
  const activityTransitionIds = [];
  const camera = new THREE.PerspectiveCamera(50, 2, 1, 2_000);
  camera.position.set(0, 0, 500);
  camera.lookAt(0, 0, 0);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory(container) {
      const graph = harness.forceGraphFactory(container);
      graph.camera = () => camera;
      return graph;
    },
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    activityControllerFactory() {
      return {
        diagnostics: () => ({}),
        refreshOnce() {},
        release() {},
        sync() {},
        transition(specifications) {
          activityTransitionIds.push(...specifications.map((specification) => specification.id));
          specifications.forEach((specification) => specification.apply(1));
        },
      };
    },
    interactionControllerFactory(configuration) {
      return {
        diagnostics: () => ({}),
        setResolvedAppearance() {},
        syncAvailability() {},
        syncPresentation() {},
        transitionCamera(pose, options) {
          cameraTransitions.push({ pose: structuredClone(pose), options: { ...options } });
          harness.graphs[0].cameraPosition(pose.position, pose.target, 0);
          configuration.onCameraFrame(pose, 1);
          options.onComplete?.();
          return true;
        },
      };
    },
    reducedMotion: false,
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);

  assert.equal(renderer.fitGraph(), true);
  assert.equal(renderer.zoomBy(0.8), true);

  assert.deepEqual(cameraTransitions.map((transition) => ({
    duration: transition.options.duration,
    focusKey: transition.options.focusKey,
    intent: transition.options.intent,
  })), [
    { duration: 360, focusKey: "fit", intent: "explicit" },
    { duration: 180, focusKey: "zoom", intent: "explicit" },
  ]);
  assert.equal(activityTransitionIds.includes("camera-transition"), false);
  assert.ok(harness.cameraDurations.every((duration) => duration === 0));
  renderer.dispose();
});

test("collapsed mid-Fit abort restores controls, publishes current terminal pose, and unlocks zoom", () => {
  const harness = rendererHarness();
  let now = 0;
  let nextFrame = 1;
  const pendingFrames = new Map();
  const cameraFrames = [];
  const scheduledLayoutCommits = [];
  const appFrameScheduler = {
    requestAnimationFrame(callback) {
      const token = nextFrame;
      nextFrame += 1;
      pendingFrames.set(token, callback);
      return token;
    },
    cancelAnimationFrame(token) { pendingFrames.delete(token); },
    flush(timestamp) {
      now = timestamp;
      const callbacks = [...pendingFrames.values()];
      pendingFrames.clear();
      callbacks.forEach((callback) => callback(timestamp));
    },
  };
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    appFrameScheduler,
    clock: { now: () => now },
    scheduleLayoutCommit(callback) {
      scheduledLayoutCommits.push(callback);
      return scheduledLayoutCommits.length;
    },
    cancelLayoutCommit() {},
    onCameraPoseChange(pose, frame) {
      cameraFrames.push({ pose: structuredClone(pose), frame: { ...frame } });
    },
    reducedMotion: false,
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  renderer.pauseAnimation();
  settleHarnessGraph(harness.graphs[0]);
  assert.equal(renderer.fitGraph(), true);
  assert.equal(harness.graphs[0].controls().enabled, false);

  appFrameScheduler.flush(180);
  const currentPosition = structuredClone(harness.graphs[0].cameraPosition());
  renderer.setActivityState({ expanded: false });

  assert.equal(harness.graphs[0].controls().enabled, true);
  assert.deepEqual(cameraFrames.at(-1), {
    pose: { position: currentPosition, target: { x: 0, y: 0, z: 0 } },
    frame: { progress: 1 },
  });

  renderer.setActivityState({ expanded: true });
  assert.equal(renderer.zoomBy(0.8), true);
  renderer.dispose();
});

test("inactive and invalid viewport states hard-reject focus, Fit, and zoom before camera ownership", () => {
  const harness = rendererHarness();
  const cameraRequests = [];
  const scheduledLayoutCommits = [];
  const validGeometry = {
    viewportRect: { x: 0, y: 0, width: 960, height: 540 },
    safeInset: { geometryRevision: 1, top: 80, right: 12, bottom: 12, left: 12 },
  };
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    interactionControllerFactory(configuration) {
      return {
        diagnostics: () => ({}),
        focusNodes() { cameraRequests.push("focus"); return true; },
        release() {},
        setResolvedAppearance() {},
        syncAvailability() {},
        syncPresentation() {},
        transitionCamera(pose, options) {
          cameraRequests.push(options.focusKey);
          harness.graphs[0].cameraPosition(pose.position, pose.target, 0);
          configuration.onCameraFrame(pose, 1);
          options.onComplete?.();
          return true;
        },
      };
    },
    scheduleLayoutCommit(callback) {
      scheduledLayoutCommits.push(callback);
      return scheduledLayoutCommits.length;
    },
    cancelLayoutCommit() {},
    reducedMotion: true,
  });
  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.setViewportGeometry(validGeometry);
  renderer.settle(180);
  renderer.pauseAnimation();
  settleHarnessGraph(harness.graphs[0]);

  const assertRejected = (name, configure) => {
    renderer.setActivityState({ expanded: true, foreground: true, intersecting: true });
    renderer.setViewportGeometry(validGeometry);
    configure();
    const requestCount = cameraRequests.length;
    const position = structuredClone(harness.graphs[0].cameraPosition());

    assert.equal(renderer.focusNode("component:harnesskit.agent.writer", {
      intent: "contextual",
    }), false, `${name}: focus`);
    assert.equal(renderer.fitGraph(), false, `${name}: Fit`);
    assert.equal(renderer.zoomBy(0.8), false, `${name}: zoom`);
    assert.equal(cameraRequests.length, requestCount, name);
    assert.deepEqual(harness.graphs[0].cameraPosition(), position, name);
  };

  assertRejected("collapsed", () => renderer.setActivityState({ expanded: false }));
  assertRejected("hidden", () => renderer.setActivityState({ foreground: false }));
  assertRejected("non-intersecting", () => renderer.setActivityState({ intersecting: false }));
  assertRejected("zero viewport", () => renderer.setViewportGeometry({
    viewportRect: { x: 0, y: 0, width: 0, height: 540 },
    safeInset: validGeometry.safeInset,
  }));
  assertRejected("non-finite viewport", () => renderer.setViewportGeometry({
    viewportRect: { x: 0, y: 0, width: Number.POSITIVE_INFINITY, height: 540 },
    safeInset: validGeometry.safeInset,
  }));
  renderer.dispose();
});

test("custom scene presentation never rebuilds positioned node objects through graph refresh", () => {
  const harness = rendererHarness();
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    reducedMotion: true,
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);
  renderer.syncPresentation({
    activeProfileId: "harnesskit.profile.engineering",
    selectedComponentId: "harnesskit.agent.writer",
  });

  assert.equal(harness.calls.filter((call) => call === "refresh").length, 0);
  assert.ok(harness.calls.includes("node:sync"));
  assert.ok(harness.calls.includes("link:sync"));
  renderer.dispose();
});

test("resolved appearance reaches the retained node presentation without rebuilding the scene", () => {
  const harness = rendererHarness();
  const appearances = [];
  let createdNodes = 0;
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => ({
      create(value) { createdNodes += 1; return { kind: "node", value }; },
      createPresentationTransition(_model, _presentation, options) {
        appearances.push(options.appearance);
        return () => {};
      },
      dispose() {},
    }),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    reducedMotion: true,
  });

  renderer.setResolvedAppearance("light");
  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);
  renderer.syncPresentation({ focusNodeId: null });
  renderer.setResolvedAppearance("dark");

  assert.deepEqual(appearances, ["light", "light", "dark"]);
  assert.equal(createdNodes, 1);
  assert.equal(harness.calls.filter((call) => call === "graphData").length, 1);
  assert.equal(harness.calls.filter((call) => call === "destructor").length, 0);
  renderer.dispose();
});

test("explicit Workflow focus passes its relation and every participant to camera framing", () => {
  const harness = rendererHarness();
  let focusedNodeIds = [];
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    interactionControllerFactory() {
      return {
        focusNodes(nodes) {
          focusedNodeIds = nodes.map((node) => node.node_id).sort();
        },
        syncPresentation() {},
      };
    },
    reducedMotion: true,
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);
  renderer.focusNode("workflow:harnesskit.workflow.spec-to-tdd");

  assert.deepEqual(focusedNodeIds, [
    "component:harnesskit.agent.writer",
    "workflow:harnesskit.workflow.spec-to-tdd",
  ]);
  renderer.dispose();
});

test("contextual Component focus frames the selected node and every actual one-hop endpoint", () => {
  const harness = rendererHarness();
  const focusCommands = [];
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    interactionControllerFactory() {
      return {
        focusNodes(nodes, focusKey, options) {
          focusCommands.push({
            focusKey,
            nodeIds: nodes.map((node) => node.node_id).sort(),
            options,
          });
        },
        syncPresentation() {},
      };
    },
    reducedMotion: true,
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);
  renderer.focusNode("component:harnesskit.agent.writer", { intent: "contextual" });

  assert.deepEqual(focusCommands, [{
    focusKey: "component:harnesskit.agent.writer",
    nodeIds: [
      "component:harnesskit.agent.writer",
      "profile:harnesskit.profile.engineering",
      "workflow:harnesskit.workflow.spec-to-tdd",
    ],
    options: { intent: "contextual" },
  }]);
  renderer.dispose();
});

test("ordinary and explicit focus share visual-envelope framing inside the current HUD safe inset", () => {
  const harness = rendererHarness();
  const source = sceneFixture();
  const spatialPolicy = graphSpatialPolicyFor(source);
  const camera = new THREE.PerspectiveCamera(50, 2, 1, 2_000);
  const viewportGeometry = {
    viewportRect: { x: 0, y: 0, width: 1_000, height: 500 },
    safeInset: {
      geometryRevision: 12,
      top: 150,
      right: 32,
      bottom: 24,
      left: 28,
    },
  };
  const focusReports = [];
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory(container) {
      const graph = harness.forceGraphFactory(container);
      graph.camera = () => camera;
      return graph;
    },
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    interactionControllerFactory(configuration) {
      return {
        focusNodes(nodes) {
          const pose = configuration.resolveFramePose(nodes);
          assert.ok(pose);
          camera.position.set(pose.position.x, pose.position.y, pose.position.z);
          camera.lookAt(pose.target.x, pose.target.y, pose.target.z);
          camera.updateProjectionMatrix();
          camera.updateMatrixWorld(true);
          focusReports.push(createSceneFitReport(
            { nodes },
            camera,
            viewportGeometry,
            spatialPolicy,
          ));
        },
        syncPresentation() {},
      };
    },
    reducedMotion: true,
    spatialPolicy,
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(source);
  renderer.setViewportGeometry(viewportGeometry);
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);

  renderer.focusNode("component:harnesskit.agent.writer", { intent: "contextual" });
  renderer.focusNode("workflow:harnesskit.workflow.spec-to-tdd", { intent: "explicit" });

  assert.equal(focusReports.length, 2);
  assert.deepEqual(focusReports.map((report) => report.cropCount), [0, 0]);
  assert.ok(focusReports.every((report) => report.safeExtent.geometryRevision === 12));
  renderer.dispose();
});

test("renderer container leaves Escape ownership to the app shell", () => {
  const harness = rendererHarness();
  let backgroundSelections = 0;
  let cameraRestores = 0;
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    interactionControllerFactory() {
      return {
        restoreCamera() { cameraRestores += 1; return true; },
        syncPresentation() {},
      };
    },
    onBackgroundSelect() { backgroundSelections += 1; },
    reducedMotion: true,
  });
  const graphTarget = new FakeElement("section");

  renderer.attach(graphTarget);
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);
  const persistentContainer = graphTarget.children[0];
  persistentContainer.emit("keydown", { key: "Escape" });

  assert.equal(cameraRestores, 0);
  assert.equal(backgroundSelections, 0);
  assert.equal(persistentContainer.listeners.has("keydown"), false);
  renderer.dispose();
});

test("renderer settles once, moves one persistent host, never reheats on presentation, and retries the same scene after context loss", () => {
  const harness = rendererHarness();
  const rendererStates = [];
  const documentObject = { createElement: (tag) => new FakeElement(tag) };
  const renderer = createThreeGraphRenderer({
    documentObject,
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    onRendererStateChange: (state) => rendererStates.push(state),
    reducedMotion: true,
  });
  const firstHost = new FakeElement("section");
  const secondHost = new FakeElement("section");

  renderer.attach(firstHost);
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  renderer.pauseAnimation();

  assert.equal(harness.graphs.length, 1);
  assert.equal(harness.graphs[0]._forceEngine, "d3");
  assert.equal(harness.graphs[0]._warmupTicks, 180);
  assert.equal(harness.forceRecords.get("charge").strength, -28);
  assert.equal(harness.forceRecords.get("charge").distanceMax, 160);
  assert.equal(harness.forceRecords.get("link").strength, 0.28);
  assert.equal(harness.forceRecords.get("link").distance({ semantic: "component-cross-link" }), 34);
  assert.equal(harness.forceRecords.get("link").distance({ semantic: "workflow-step" }), 42);
  assert.equal(harness.calls.filter((call) => call === "graphData").length, 1);
  assert.equal(firstHost.children.length, 1);
  assert.equal(firstHost.children[0].role, "presentation");
  assert.equal(firstHost.children[0]["aria-hidden"], "true");
  assert.equal(firstHost.children[0].tabIndex, -1);
  assert.equal(harness.canvasByGraph[0].role, "presentation");
  assert.equal(harness.canvasByGraph[0]["aria-hidden"], "true");
  assert.equal(harness.canvasByGraph[0].tabindex, "-1");
  assert.equal(harness.calls.includes("pause"), false);

  renderer.syncPresentation({
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
    lockedWorkflowStep: null,
  });
  assert.equal(harness.calls.includes("node:sync"), false);
  settleHarnessGraph(harness.graphs[0]);
  assert.ok(harness.calls.includes("node:sync"));
  assert.ok(harness.calls.includes("link:sync"));
  assert.equal(harness.calls.includes("d3ReheatSimulation"), false);
  assert.equal(renderer.zoomBy(0.8), true);
  assert.equal(renderer.fitGraph(), true);
  assert.ok(harness.calls.includes("camera"));
  assert.equal(harness.calls.some((call) => call.startsWith("fit:")), false);

  renderer.detach();
  renderer.attach(secondHost);
  assert.equal(harness.graphs.length, 1);
  assert.equal(firstHost.children.length, 0);
  assert.equal(secondHost.children.length, 1);

  harness.graphs[0]
    .renderer()
    .getContext()
    .getExtension("WEBGL_lose_context")
    .loseContext();
  assert.ok(harness.calls.includes("WEBGL_lose_context"));
  assert.equal(rendererStates.at(-1).availability, "context_lost");
  assert.equal(rendererStates.at(-1).reason, "3D 그래프 연결이 중단되어 텍스트 보기로 전환했습니다.");
  assert.equal(rendererStates.at(-1).canRetry, true);
  assert.equal(harness.calls.filter((call) => call === "destructor").length, 1);

  assert.equal(renderer.retry(), true);
  assert.equal(harness.graphs.length, 2);
  assert.equal(harness.graphs[1]._warmupTicks, 0);
  assert.equal(
    harness.graphs[1]._graphData.nodes.find((node) => node.node_type === "component")?.component_id,
    "harnesskit.agent.writer",
  );
  assert.equal(rendererStates.at(-1).availability, "pending");
  settleHarnessGraph(harness.graphs[1]);
  assert.equal(rendererStates.at(-1).availability, "ready");
  renderer.dispose();
});

test("manual text fallback preserves anchors, pose, presentation, and contextual restore baseline", () => {
  const harness = rendererHarness();
  const rendererStates = [];
  const interactionControllers = [];
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    interactionControllerFactory(options) {
      const record = {
        importedStates: [],
        presentations: [],
        resets: 0,
        restores: 0,
      };
      interactionControllers.push(record);
      return {
        exportState() {
          return {
            focusKey: "workflow:harnesskit.workflow.spec-to-tdd",
            focusIntent: "contextual",
            previousCamera: {
              position: { x: 0, y: 0, z: 500 },
              target: { x: 0, y: 0, z: 0 },
            },
          };
        },
        importState(state) {
          record.importedStates.push(structuredClone(state));
          return true;
        },
        restoreCamera() {
          record.restores += 1;
          return record.importedStates.length > 0;
        },
        resetCameraContext() { record.resets += 1; },
        syncPresentation(presentation) {
          record.presentations.push(structuredClone(presentation));
        },
      };
    },
    onRendererStateChange: (state) => rendererStates.push(state),
    reducedMotion: true,
  });
  const host = new FakeElement("section");
  const presentation = {
    activeProfileId: "harnesskit.profile.engineering",
    selectedComponentId: "harnesskit.agent.writer",
    selectedRelationNodeId: "workflow:harnesskit.workflow.spec-to-tdd",
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
    hoveredWorkflowStep: { workflowId: "harnesskit.workflow.spec-to-tdd", ordinal: 1 },
    lockedWorkflowStep: { workflowId: "harnesskit.workflow.spec-to-tdd", ordinal: 1 },
  };

  renderer.attach(host);
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);
  const settledAnchors = harness.graphs[0]._graphData.nodes.map(({ node_id, x, y, z }) => ({
    node_id,
    x,
    y,
    z,
  }));
  renderer.syncPresentation(presentation);
  harness.graphs[0]._camera = { x: 32, y: -18, z: 240 };
  harness.graphs[0].controls().target = { x: 4, y: 5, z: 6 };
  const recoveryCapsule = renderer.captureRecoveryCapsule();
  assert.equal(recoveryCapsule.projectionId, "projection-component-map");
  assert.ok(recoveryCapsule.settledAnchorMap instanceof Map);
  assert.equal(recoveryCapsule.restoreBaseline.focusIntent, "contextual");

  assert.equal(renderer.showSemanticFallback(), true);
  assert.deepEqual(rendererStates.at(-1), {
    availability: "manual_fallback",
    reason: "사용자가 텍스트 보기를 선택했습니다.",
    canRetry: true,
  });
  assert.equal(harness.calls.filter((call) => call === "destructor").length, 1);
  assert.equal(renderer.restoreRecoveryCapsule({
    ...recoveryCapsule,
    projectionId: "projection-other",
  }), false);
  assert.equal(renderer.restoreRecoveryCapsule(recoveryCapsule), true);

  assert.equal(renderer.retry(), true);
  assert.equal(harness.graphs.length, 2);
  assert.deepEqual(harness.graphs[1]._camera, { x: 32, y: -18, z: 240 });
  assert.deepEqual(harness.graphs[1].controls().target, { x: 4, y: 5, z: 6 });
  assert.deepEqual(
    harness.graphs[1]._graphData.nodes.map(({ node_id, x, y, z }) => ({ node_id, x, y, z })),
    settledAnchors,
  );
  assert.equal(interactionControllers[0].resets, 1);
  assert.deepEqual(interactionControllers[1].importedStates, [{
    focusKey: "workflow:harnesskit.workflow.spec-to-tdd",
    focusIntent: "contextual",
    previousCamera: {
      position: { x: 0, y: 0, z: 500 },
      target: { x: 0, y: 0, z: 0 },
    },
  }]);
  assert.equal(renderer.restoreCamera(), true);
  assert.equal(interactionControllers[1].restores, 1);
  settleHarnessGraph(harness.graphs[1]);
  assert.deepEqual(interactionControllers[1].presentations.at(-1), presentation);
  assert.equal(
    harness.graphs[1]._graphData.nodes.find((node) => node.node_type === "component")?.component_id,
    "harnesskit.agent.writer",
  );
  renderer.dispose();
  assert.equal(renderer.captureRecoveryCapsule(), null);
});

test("renderer publishes and clamps camera scale between 0.75x and 8x around the last Fit", () => {
  const harness = rendererHarness();
  const cameraStates = [];
  const fitReports = [];
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    onCameraScaleChange: (state) => cameraStates.push(state),
    onSceneFitReportChange: (report) => fitReports.push(report),
    reducedMotion: true,
  });
  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);
  renderer.fitGraph();

  assert.equal(fitReports.length, 1);
  assert.deepEqual(
    { identities: fitReports[0].identityCount,
      status: fitReports[0].camera.status },
    { identities: 3, status: "unavailable" },
  );

  assert.deepEqual(cameraStates.at(-1), {
    scale: 1,
    minScale: MIN_CAMERA_SCALE,
    maxScale: MAX_CAMERA_SCALE,
    level: "overview",
  });
  assert.equal(harness.graphs[0].controls().minDistance, 500 / MAX_CAMERA_SCALE);
  assert.equal(harness.graphs[0].controls().maxDistance, 500 / MIN_CAMERA_SCALE);

  for (let index = 0; index < 20; index += 1) renderer.zoomBy(0.8);
  assert.equal(cameraStates.at(-1).scale, MAX_CAMERA_SCALE);
  assert.equal(cameraStates.at(-1).level, "maximum");
  assert.equal(harness.graphs[0]._camera.z, 500 / MAX_CAMERA_SCALE);

  for (let index = 0; index < 40; index += 1) renderer.zoomBy(1.2);
  assert.equal(cameraStates.at(-1).scale, MIN_CAMERA_SCALE);
  assert.equal(cameraStates.at(-1).level, "overview");
  assert.ok(Math.abs(harness.graphs[0]._camera.z - (500 / MIN_CAMERA_SCALE)) < 1e-9);

  renderer.fitGraph();
  assert.equal(cameraStates.at(-1).scale, 1);
  renderer.dispose();
});

test("production renderer exposes recovery behavior without test-only evidence ports", () => {
  const harness = rendererHarness();
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    reducedMotion: true,
  });
  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);

  assert.equal("runtimeEvidenceSnapshot" in renderer, false);
  assert.equal("triggerContextLossForEvidence" in renderer, false);
  assert.equal("restoreCameraForEvidence" in renderer, false);

  harness.canvasByGraph[0].emit("webglcontextlost");
  assert.equal(renderer.retry(), true);
  settleHarnessGraph(harness.graphs[1]);
  assert.equal(
    harness.graphs[1]._graphData.nodes.find((node) => node.node_type === "component")?.component_id,
    "harnesskit.agent.writer",
  );
  renderer.dispose();
});

test("WebGL-unavailable attach reports a retryable safe state without creating a graph", () => {
  const harness = rendererHarness();
  const states = [];
  let available = false;
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => available,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    onRendererStateChange: (state) => states.push(state),
    spatialPolicy: graphSpatialPolicyFor(sceneFixture()),
  });

  renderer.attach(new FakeElement("section"));
  assert.equal(harness.graphs.length, 0);
  assert.deepEqual(states.at(-1), {
    availability: "unavailable",
    reason: "webgl_unavailable",
    canRetry: true,
  });
  available = true;
  assert.equal(renderer.retry(), true);
  assert.equal(harness.graphs.length, 1);
  renderer.dispose();
});

test("WebGL-unavailable retry keeps the authored 180-tick settle budget", () => {
  const harness = rendererHarness();
  let available = false;
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => available,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  renderer.pauseAnimation();
  assert.equal(harness.graphs.length, 0);

  available = true;
  assert.equal(renderer.retry(), true);
  assert.equal(harness.graphs[0]._warmupTicks, 180);
  renderer.dispose();
});

test("recovery state survives a late retry initialization failure", () => {
  const harness = rendererHarness();
  let attempt = 0;
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory(container, configuration) {
      const graph = harness.forceGraphFactory(container, configuration);
      attempt += 1;
      if (attempt === 2) {
        graph.controls().addEventListener = () => {
          throw new Error("late controls binding failure");
        };
      }
      return graph;
    },
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    reducedMotion: true,
  });

  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  settleHarnessGraph(harness.graphs[0]);
  harness.graphs[0]._camera = { x: 31, y: -17, z: 241 };
  harness.graphs[0].controls().target = { x: 4, y: 5, z: 6 };
  harness.canvasByGraph[0].emit("webglcontextlost");

  assert.equal(renderer.retry(), false);
  assert.equal(renderer.retry(), true);
  assert.deepEqual(harness.graphs[2]._camera, { x: 31, y: -17, z: 241 });
  assert.deepEqual(harness.graphs[2].controls().target, { x: 4, y: 5, z: 6 });
  renderer.dispose();
});

test("renderer separates one presentation RAF from one camera RAF without control leases", () => {
  const harness = rendererHarness();
  let nextFrameId = 1;
  let frameNow = 0;
  const pendingFrames = new Map();
  const appFrameScheduler = {
    requestAnimationFrame(callback) {
      const id = nextFrameId;
      nextFrameId += 1;
      pendingFrames.set(id, callback);
      return id;
    },
    cancelAnimationFrame(id) { pendingFrames.delete(id); },
    flush(timestamp = frameNow + 1_000) {
      frameNow = timestamp;
      const callbacks = [...pendingFrames.values()];
      pendingFrames.clear();
      callbacks.forEach((callback) => callback(timestamp));
    },
  };
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory(container) {
      const graph = harness.forceGraphFactory(container);
      const camera = new THREE.PerspectiveCamera(50, 16 / 9, 1, 2_000);
      camera.position.set(0, 0, 500);
      camera.lookAt(0, 0, 0);
      camera.updateProjectionMatrix();
      camera.updateMatrixWorld(true);
      graph.camera = () => camera;
      return graph;
    },
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    reducedMotion: false,
    appFrameScheduler,
    clock: { now: () => frameNow },
  });

  const host = new FakeElement("section");
  renderer.attach(host);
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  renderer.pauseAnimation();
  const graph = harness.graphs[0];
  settleHarnessGraph(graph);
  const resumesAtSteady = harness.calls.filter((call) => call === "resume").length;
  assert.ok(resumesAtSteady >= 1);
  assert.equal(pendingFrames.size, 0);

  renderer.syncPresentation({ selectedComponentId: "harnesskit.agent.writer" });
  assert.equal(pendingFrames.size, 1);
  appFrameScheduler.flush();
  assert.equal(pendingFrames.size, 0);

  renderer.setActivityState({ expanded: false });
  const pausesAfterCollapse = harness.calls.filter((call) => call === "pause").length;
  assert.ok(pausesAfterCollapse >= 1);
  renderer.setActivityState({ expanded: true });
  renderer.setActivityState({ expanded: true });
  assert.equal(
    harness.calls.filter((call) => call === "resume").length,
    resumesAtSteady + 1,
  );

  const persistentHost = graph.renderer().domElement.parentNode;
  persistentHost.emit("pointermove");
  assert.equal(pendingFrames.size, 0);

  graph.controls().emit("start");
  assert.equal(pendingFrames.size, 0);
  graph.controls().emit("end");
  assert.equal(pendingFrames.size, 0);

  renderer.syncPresentation({
    selectedComponentId: "harnesskit.agent.writer",
    focusNodeId: "component:harnesskit.agent.writer",
  });
  renderer.focusNode("component:harnesskit.agent.writer", { intent: "contextual" });
  assert.equal(pendingFrames.size, 2);
  assert.deepEqual(renderer.diagnostics(), {
    applicationRafTotal: 2,
    cameraRafRunning: 1,
    internalRendererRunning: 1,
    presentationRafRunning: 1,
  });
  appFrameScheduler.flush();
  assert.equal(pendingFrames.size, 0);
  assert.deepEqual(renderer.diagnostics(), {
    applicationRafTotal: 0,
    cameraRafRunning: 0,
    internalRendererRunning: 1,
    presentationRafRunning: 0,
  });

  const pausesBeforeDetach = harness.calls.filter((call) => call === "pause").length;
  renderer.detach();
  assert.equal(harness.calls.filter((call) => call === "pause").length, pausesBeforeDetach + 1);
  assert.equal(host.children.length, 0);

  renderer.dispose();
  assert.equal(graph.controls().listeners.size, 0);
});

test("renderer presentation port shares the injected app RAF clock with exact retargetable progress", () => {
  const harness = rendererHarness();
  let nextFrameId = 1;
  let frameNow = 0;
  const pendingFrames = new Map();
  const presentationFrames = [];
  const appFrameScheduler = {
    requestAnimationFrame(callback) {
      const id = nextFrameId;
      nextFrameId += 1;
      pendingFrames.set(id, callback);
      return id;
    },
    cancelAnimationFrame(id) { pendingFrames.delete(id); },
    flush(timestamp) {
      frameNow = timestamp;
      const callbacks = [...pendingFrames.values()];
      pendingFrames.clear();
      callbacks.forEach((callback) => callback(timestamp));
    },
  };
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => objectFactoryHarness("node", harness.calls),
    createLinkObjects: () => objectFactoryHarness("link", harness.calls),
    appFrameScheduler,
    clock: { now: () => frameNow },
    onPresentationFrameChange(frame) { presentationFrames.push({ ...frame }); },
  });
  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  renderer.pauseAnimation();
  settleHarnessGraph(harness.graphs[0]);

  renderer.syncPresentation({ selectedComponentId: "harnesskit.agent.writer" });
  assert.equal(presentationFrames.at(-1).progress, 0);
  assert.equal(pendingFrames.size, 1);
  assert.equal(renderer.diagnostics().presentationRafRunning, 1);

  appFrameScheduler.flush(120);
  assert.equal(presentationFrames.at(-1).progress, 0.5);
  assert.equal(pendingFrames.size, 1);

  const previousTransactionId = presentationFrames.at(-1).transactionId;
  renderer.syncPresentation({ selectedRelationNodeId: "profile:harnesskit.profile.engineering" });
  assert.equal(presentationFrames.at(-1).progress, 0);
  assert.notEqual(presentationFrames.at(-1).transactionId, previousTransactionId);
  assert.equal(pendingFrames.size, 1);

  appFrameScheduler.flush(240);
  assert.equal(presentationFrames.at(-1).progress, 0.5);
  appFrameScheduler.flush(360);
  assert.equal(presentationFrames.at(-1).progress, 1);
  assert.equal(pendingFrames.size, 0);

  renderer.setActivityState({ reducedMotion: true });
  renderer.syncPresentation({ selectedComponentId: "harnesskit.agent.writer" });
  assert.equal(presentationFrames.at(-1).progress, 1);
  assert.equal(presentationFrames.at(-1).reducedMotion, true);
  assert.equal(pendingFrames.size, 0);
  renderer.dispose();
});

test("DOM-only collision retarget preserves the active scene transition through its terminal pose", () => {
  const harness = rendererHarness();
  let nextFrameId = 1;
  let frameNow = 0;
  const pendingFrames = new Map();
  const nodeProgress = [];
  const linkProgress = [];
  const appFrameScheduler = {
    requestAnimationFrame(callback) {
      const id = nextFrameId;
      nextFrameId += 1;
      pendingFrames.set(id, callback);
      return id;
    },
    cancelAnimationFrame(id) { pendingFrames.delete(id); },
    flush(timestamp) {
      frameNow = timestamp;
      const callbacks = [...pendingFrames.values()];
      pendingFrames.clear();
      callbacks.forEach((callback) => callback(timestamp));
    },
  };
  const presentationFactory = (kind, progressValues) => ({
    create(value) { return { kind, value }; },
    createPresentationTransition() {
      return (progress) => progressValues.push(progress);
    },
    dispose() {},
    updatePosition() { return true; },
  });
  const renderer = createThreeGraphRenderer({
    documentObject: { createElement: (tag) => new FakeElement(tag) },
    forceGraphFactory: harness.forceGraphFactory,
    webglProbe: () => true,
    createNodeObjects: () => presentationFactory("node", nodeProgress),
    createLinkObjects: () => presentationFactory("link", linkProgress),
    appFrameScheduler,
    clock: { now: () => frameNow },
  });
  renderer.attach(new FakeElement("section"));
  renderer.setGraphData(sceneFixture());
  renderer.settle(180);
  renderer.pauseAnimation();
  settleHarnessGraph(harness.graphs[0]);
  nodeProgress.length = 0;
  linkProgress.length = 0;

  renderer.syncPresentation({ selectedComponentId: "harnesskit.agent.writer" });
  appFrameScheduler.flush(120);
  assert.equal(nodeProgress.at(-1), 0.5);
  assert.equal(linkProgress.at(-1), 0.5);

  renderer.requestPresentationTransition("relation-label-collision");
  assert.equal(pendingFrames.size, 1);
  appFrameScheduler.flush(240);
  assert.equal(nodeProgress.at(-1), 0.75);
  assert.equal(linkProgress.at(-1), 0.75);
  appFrameScheduler.flush(360);
  assert.equal(nodeProgress.at(-1), 1);
  assert.equal(linkProgress.at(-1), 1);
  assert.equal(pendingFrames.size, 0);
  renderer.dispose();
});
