import assert from "node:assert/strict";
import test from "node:test";

import {
  createGraphInteractionController as createRawGraphInteractionController,
  deriveGraphInteractionModel,
  graphLinkEndpoints,
} from "../graph/interaction-controller.js";

function resolveTestFrame(nodes) {
  if (!Array.isArray(nodes) || nodes.length === 0) return null;
  const points = nodes.map((node) => ({
    x: Number(node.x) || 0,
    y: Number(node.y) || 0,
    z: Number(node.z) || 0,
  }));
  const bounds = points.reduce((accumulator, point) => ({
    minX: Math.min(accumulator.minX, point.x),
    maxX: Math.max(accumulator.maxX, point.x),
    minY: Math.min(accumulator.minY, point.y),
    maxY: Math.max(accumulator.maxY, point.y),
    minZ: Math.min(accumulator.minZ, point.z),
    maxZ: Math.max(accumulator.maxZ, point.z),
  }), {
    minX: Infinity,
    maxX: -Infinity,
    minY: Infinity,
    maxY: -Infinity,
    minZ: Infinity,
    maxZ: -Infinity,
  });
  const target = {
    x: (bounds.minX + bounds.maxX) / 2,
    y: (bounds.minY + bounds.maxY) / 2,
    z: (bounds.minZ + bounds.maxZ) / 2,
  };
  const span = Math.max(
    bounds.maxX - bounds.minX,
    bounds.maxY - bounds.minY,
    bounds.maxZ - bounds.minZ,
  );
  return {
    position: { x: target.x, y: target.y, z: target.z + Math.max(120, span * 1.8) },
    target,
  };
}

function createGraphInteractionController(options = {}) {
  return createRawGraphInteractionController({
    resolveFramePose: resolveTestFrame,
    ...options,
  });
}

test("typed explicit endpoints are the only frontend direction authority", () => {
  assert.deepEqual(graphLinkEndpoints({
    semantic: "invoked-by",
    source_node_id: "component:harnesskit.agent.caller",
    target_node_id: "workflow:harnesskit.workflow.target",
    workflow_node_id: "workflow:legacy-reversed",
    component_node_id: "component:legacy-reversed",
  }), [
    "component:harnesskit.agent.caller",
    "workflow:harnesskit.workflow.target",
  ]);
  assert.deepEqual(graphLinkEndpoints({
    semantic: "workflow-step",
    workflow_node_id: "workflow:legacy",
    component_node_id: "component:legacy",
  }), [null, null]);
});

function graphFixture() {
  return {
    schema_version: 2,
    nodes: [
      {
        node_type: "relation",
        node_id: "workflow:harnesskit.workflow.spec-to-tdd",
        relation_kind: "Workflow",
        canonical_id: "harnesskit.workflow.spec-to-tdd",
        x: 120,
        y: 0,
        z: 0,
      },
      {
        node_type: "component",
        node_id: "component:harnesskit.agent.writer",
        component_id: "harnesskit.agent.writer",
        x: 0,
        y: -20,
        z: 6,
      },
      {
        node_type: "component",
        node_id: "component:harnesskit.agent.implementer",
        component_id: "harnesskit.agent.implementer",
        x: 0,
        y: 20,
        z: -6,
      },
      {
        node_type: "relation",
        node_id: "profile:harnesskit.profile.engineering",
        relation_kind: "Profile",
        canonical_id: "harnesskit.profile.engineering",
        x: -120,
        y: 0,
        z: 0,
      },
    ],
    links: [
      {
        semantic: "workflow-step",
        link_id: "workflow:writer",
        workflow_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
        component_node_id: "component:harnesskit.agent.writer",
        source_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
        target_node_id: "component:harnesskit.agent.writer",
        directionality: "directed",
        occurrences: [{ ordinal: 1 }, { ordinal: 4 }],
      },
      {
        semantic: "workflow-step",
        link_id: "workflow:implementer",
        workflow_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
        component_node_id: "component:harnesskit.agent.implementer",
        source_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
        target_node_id: "component:harnesskit.agent.implementer",
        directionality: "directed",
        occurrences: [{ ordinal: 2 }],
      },
      {
        semantic: "profile-membership",
        link_id: "profile:writer",
        profile_node_id: "profile:harnesskit.profile.engineering",
        component_node_id: "component:harnesskit.agent.writer",
        source_node_id: "profile:harnesskit.profile.engineering",
        target_node_id: "component:harnesskit.agent.writer",
        directionality: "unordered",
      },
    ],
  };
}

test("interaction controller requires the renderer's policy-aware Fit resolver", () => {
  const harness = graphHarness();

  assert.throws(
    () => createRawGraphInteractionController({ graph: harness.graph }),
    /resolveFramePose is required/,
  );
});

test("Workflow overview highlights one relation, every participant, and bundled incidence without changing Profile state", () => {
  const projection = graphFixture();
  const presentation = {
    activeProfileId: "harnesskit.profile.engineering",
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
    lockedWorkflowStep: null,
  };
  const profileBytes = JSON.stringify({ activeProfileId: presentation.activeProfileId });

  const model = deriveGraphInteractionModel(projection, presentation);

  assert.deepEqual([...model.activeNodeIds].sort(), [
    "component:harnesskit.agent.implementer",
    "component:harnesskit.agent.writer",
    "workflow:harnesskit.workflow.spec-to-tdd",
  ]);
  assert.deepEqual([...model.activeLinkIds].sort(), [
    "workflow:implementer",
    "workflow:writer",
  ]);
  assert.equal(model.workflowNodeId, "workflow:harnesskit.workflow.spec-to-tdd");
  assert.equal(JSON.stringify({ activeProfileId: presentation.activeProfileId }), profileBytes);
});

test("locked Workflow step narrows participants by authored ordinal while keeping a repeated bundled edge", () => {
  const model = deriveGraphInteractionModel(graphFixture(), {
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
    lockedWorkflowStep: {
      workflowId: "harnesskit.workflow.spec-to-tdd",
      ordinal: 4,
    },
  });

  assert.deepEqual([...model.activeNodeIds].sort(), [
    "component:harnesskit.agent.writer",
    "workflow:harnesskit.workflow.spec-to-tdd",
  ]);
  assert.deepEqual([...model.activeLinkIds], ["workflow:writer"]);
  assert.equal(model.activeOrdinal, 4);
});

test("Component selection highlights every owning Profile relation and membership edge", () => {
  const projection = graphFixture();
  projection.nodes.push({
    node_type: "relation",
    node_id: "profile:harnesskit.profile.work",
    relation_kind: "Profile",
    canonical_id: "harnesskit.profile.work",
    x: -120,
    y: 80,
    z: 0,
  });
  projection.links.push({
    semantic: "profile-membership",
    link_id: "profile-work:writer",
    profile_node_id: "profile:harnesskit.profile.work",
    component_node_id: "component:harnesskit.agent.writer",
    source_node_id: "profile:harnesskit.profile.work",
    target_node_id: "component:harnesskit.agent.writer",
    directionality: "unordered",
  });

  const model = deriveGraphInteractionModel(projection, {
    activeProfileId: "harnesskit.profile.engineering",
    selectedComponentId: "harnesskit.agent.writer",
  });

  assert.ok(model.activeNodeIds.has("component:harnesskit.agent.writer"));
  assert.ok(model.activeNodeIds.has("profile:harnesskit.profile.engineering"));
  assert.ok(model.activeNodeIds.has("profile:harnesskit.profile.work"));
  assert.ok(model.activeLinkIds.has("profile:writer"));
  assert.ok(model.activeLinkIds.has("profile-work:writer"));
});

test("Workflow node hover highlights all participants and step hover narrows temporarily without a camera selection", () => {
  const projection = graphFixture();
  const overview = deriveGraphInteractionModel(projection, {
    hoveredNodeId: "workflow:harnesskit.workflow.spec-to-tdd",
  });
  const step = deriveGraphInteractionModel(projection, {
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
    hoveredWorkflowStep: {
      workflowId: "harnesskit.workflow.spec-to-tdd",
      ordinal: 2,
    },
  });

  assert.deepEqual([...overview.activeLinkIds].sort(), [
    "workflow:implementer",
    "workflow:writer",
  ]);
  assert.deepEqual([...step.activeLinkIds], ["workflow:implementer"]);
  assert.equal(step.activeOrdinal, 2);
});

test("interaction model keeps semantic active links separate from the primary focus one-hop links", () => {
  const projection = graphFixture();
  const focused = deriveGraphInteractionModel(projection, {
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
    focusNodeId: "workflow:harnesskit.workflow.spec-to-tdd",
  });
  const stepWithoutFocus = deriveGraphInteractionModel(projection, {
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
    lockedWorkflowStep: {
      workflowId: "harnesskit.workflow.spec-to-tdd",
      ordinal: 2,
    },
    focusNodeId: null,
  });

  assert.deepEqual([...focused.selectedOneHopLinkIds].sort(), [
    "workflow:implementer",
    "workflow:writer",
  ]);
  assert.deepEqual([...focused.activeLinkIds].sort(), [
    "workflow:implementer",
    "workflow:writer",
  ]);
  assert.equal(focused.focusNodeId, "workflow:harnesskit.workflow.spec-to-tdd");
  assert.deepEqual([...stepWithoutFocus.activeLinkIds], ["workflow:implementer"]);
  assert.deepEqual([...stepWithoutFocus.selectedOneHopLinkIds], []);
  assert.equal(stepWithoutFocus.focusNodeId, null);
});

function graphHarness() {
  const handlers = {};
  const cameraCalls = [];
  let pose = { x: 0, y: 0, z: 500 };
  let target = { x: 11, y: 22, z: 33 };
  const graph = {
    onNodeHover(handler) {
      handlers.hover = handler;
      return this;
    },
    onNodeClick(handler) {
      handlers.click = handler;
      return this;
    },
    onBackgroundClick(handler) {
      handlers.background = handler;
      return this;
    },
    cameraPosition(next, lookAt, duration) {
      if (!next) return pose;
      pose = { ...pose, ...next };
      if (lookAt) target = { ...lookAt };
      cameraCalls.push({ next, lookAt, duration });
      return this;
    },
    controls() {
      return { target };
    },
  };
  return {
    cameraCalls,
    graph,
    handlers,
    setPose(nextPose, nextTarget = target) {
      pose = { ...nextPose };
      target = { ...nextTarget };
    },
  };
}

function frameClockHarness() {
  let now = 0;
  let nextToken = 1;
  const pending = new Map();
  const cancelled = [];
  return {
    cancelled,
    clock: { now: () => now },
    scheduler: {
      requestAnimationFrame(callback) {
        const token = nextToken;
        nextToken += 1;
        pending.set(token, callback);
        return token;
      },
      cancelAnimationFrame(token) {
        cancelled.push(token);
        pending.delete(token);
      },
    },
    advance(milliseconds) {
      now += milliseconds;
      const callbacks = [...pending.values()];
      pending.clear();
      callbacks.forEach((callback) => callback(now));
    },
    get pendingCount() {
      return pending.size;
    },
  };
}

test("interaction controller accepts the callable Kapsule graph returned by 3d-force-graph", () => {
  const harness = graphHarness();
  const kapsuleGraph = Object.assign(function kapsuleGraph() {}, harness.graph);

  const controller = createGraphInteractionController({ graph: kapsuleGraph });

  assert.equal(typeof controller.syncPresentation, "function");
  assert.equal(typeof harness.handlers.hover, "function");
  assert.equal(typeof harness.handlers.click, "function");
  assert.equal(typeof harness.handlers.background, "function");
});

test("ordinary relation, step, and component selection updates emphasis without moving the camera", () => {
  const harness = graphHarness();
  const hovered = [];
  const selected = [];
  let backgrounds = 0;
  const controller = createGraphInteractionController({
    graph: harness.graph,
    onNodeHover: (nodeId) => hovered.push(nodeId),
    onNodeSelect: (node) => selected.push(node.node_id),
    onBackgroundSelect: () => { backgrounds += 1; },
    reducedMotion: true,
  });
  const projection = graphFixture();

  harness.handlers.hover(projection.nodes[1]);
  harness.handlers.click(projection.nodes[0]);
  assert.deepEqual(hovered, ["component:harnesskit.agent.writer"]);
  assert.deepEqual(selected, ["workflow:harnesskit.workflow.spec-to-tdd"]);
  assert.equal(harness.cameraCalls.length, 0);

  controller.syncPresentation({
    hoveredNodeId: "workflow:harnesskit.workflow.spec-to-tdd",
  }, projection);
  assert.equal(harness.cameraCalls.length, 0);

  controller.syncPresentation({
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
  }, projection);
  controller.syncPresentation({
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
    lockedWorkflowStep: {
      workflowId: "harnesskit.workflow.spec-to-tdd",
      ordinal: 2,
    },
  }, projection);
  controller.syncPresentation({
    selectedComponentId: "harnesskit.agent.writer",
  }, projection);
  assert.equal(harness.cameraCalls.length, 0);

  harness.handlers.background();
  assert.equal(backgrounds, 1);
  assert.equal(harness.cameraCalls.length, 0);
});

test("default active Profile highlights context without hijacking Fit camera or background restore", () => {
  const harness = graphHarness();
  const controller = createGraphInteractionController({
    graph: harness.graph,
    reducedMotion: true,
  });
  const projection = graphFixture();

  controller.syncPresentation({
    activeProfileId: "harnesskit.profile.engineering",
    selectedRelationNodeId: null,
    selectedComponentId: null,
    selectedWorkflowId: null,
  }, projection);
  assert.equal(harness.cameraCalls.length, 0);

  controller.syncPresentation({
    activeProfileId: "harnesskit.profile.engineering",
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
  }, projection);
  assert.equal(harness.cameraCalls.length, 0);

  harness.handlers.background();
  controller.syncPresentation({
    activeProfileId: "harnesskit.profile.engineering",
    selectedRelationNodeId: null,
    selectedComponentId: null,
    selectedWorkflowId: null,
  }, projection);
  assert.equal(harness.cameraCalls.length, 0);
});

test("explicit workflow focus frames every participant and background restores the previous camera", () => {
  const harness = graphHarness();
  const frames = frameClockHarness();
  const transitions = [];
  const controller = createGraphInteractionController({
    graph: harness.graph,
    clock: frames.clock,
    frameScheduler: frames.scheduler,
    onCameraTransition: (duration, pose) => transitions.push({ duration, pose }),
    reducedMotion: false,
  });
  const projection = graphFixture();

  controller.focusNodes(projection.nodes.slice(0, 3), "workflow:harnesskit.workflow.spec-to-tdd");
  frames.advance(420);
  harness.handlers.background();
  frames.advance(250);

  assert.deepEqual(harness.cameraCalls[0].lookAt, { x: 60, y: 0, z: 0 });

  assert.deepEqual(transitions, [
    {
      duration: 420,
      pose: {
        position: harness.cameraCalls[0].next,
        target: harness.cameraCalls[0].lookAt,
      },
    },
    {
      duration: 250,
      pose: {
        position: harness.cameraCalls[1].next,
        target: harness.cameraCalls[1].lookAt,
      },
    },
  ]);
});

test("live reduced-motion changes make every later explicit focus and restore instantaneous", () => {
  const harness = graphHarness();
  const transitions = [];
  const controller = createGraphInteractionController({
    graph: harness.graph,
    onCameraTransition: (duration) => transitions.push(duration),
    reducedMotion: false,
  });
  const projection = graphFixture();

  controller.setReducedMotion(true);
  controller.focusNode(projection.nodes[0]);
  controller.restoreCamera();

  assert.deepEqual(transitions, [0, 0]);
  assert.deepEqual(harness.cameraCalls.map((call) => call.duration), [0, 0]);
});

test("contextual focus retargets from the live pose, cancels the stale frame, and restores the first baseline", () => {
  const harness = graphHarness();
  const frames = frameClockHarness();
  const projection = graphFixture();
  const controller = createGraphInteractionController({
    graph: harness.graph,
    clock: frames.clock,
    frameScheduler: frames.scheduler,
    reducedMotion: false,
  });

  controller.focusNodes(projection.nodes.slice(0, 2), "first", { intent: "contextual" });
  assert.equal(frames.pendingCount, 1);
  frames.advance(125);
  assert.equal(harness.cameraCalls.at(-1).duration, 0);

  harness.setPose({ x: 25, y: 10, z: 300 }, { x: 10, y: 0, z: 0 });
  controller.focusNodes(projection.nodes.slice(1, 3), "second", { intent: "contextual" });
  assert.equal(frames.cancelled.length, 1);
  assert.equal(frames.pendingCount, 1);

  frames.advance(250);
  assert.equal(frames.pendingCount, 0);
  assert.equal(controller.restoreCamera(), true);
  frames.advance(250);

  assert.deepEqual(harness.cameraCalls.at(-1).next, { x: 0, y: 0, z: 500 });
  assert.deepEqual(harness.cameraCalls.at(-1).lookAt, { x: 11, y: 22, z: 33 });
  assert.equal(controller.restoreCamera(), false);
});

test("explicit camera intent retargets a live contextual pose through the same RAF and owns one new restore baseline", () => {
  const harness = graphHarness();
  const frames = frameClockHarness();
  const controller = createGraphInteractionController({
    graph: harness.graph,
    clock: frames.clock,
    frameScheduler: frames.scheduler,
    reducedMotion: false,
  });

  controller.focusNode(graphFixture().nodes[0], { intent: "contextual" });
  frames.advance(125);
  const explicitBaseline = structuredClone(harness.graph.cameraPosition());

  assert.equal(controller.transitionCamera({
    position: { x: 90, y: 40, z: 210 },
    target: { x: 12, y: 8, z: 0 },
  }, {
    duration: 180,
    focusKey: "fit",
    intent: "explicit",
  }), true);
  assert.equal(frames.cancelled.length, 1);
  assert.equal(frames.pendingCount, 1);

  frames.advance(90);
  assert.equal(harness.cameraCalls.at(-1).duration, 0);
  frames.advance(90);
  assert.deepEqual(harness.graph.cameraPosition(), { x: 90, y: 40, z: 210 });
  assert.equal(controller.restoreCamera(), true);
  frames.advance(250);

  assert.deepEqual(harness.graph.cameraPosition(), explicitBaseline);
  assert.equal(controller.restoreCamera(), false);
  assert.ok(harness.cameraCalls.every((call) => call.duration === 0));
});

test("repeated explicit Fit and zoom retarget from the live pose without stacking restore baselines", () => {
  const harness = graphHarness();
  const frames = frameClockHarness();
  const controller = createGraphInteractionController({
    graph: harness.graph,
    clock: frames.clock,
    frameScheduler: frames.scheduler,
  });

  controller.transitionCamera({
    position: { x: 40, y: 10, z: 320 },
    target: { x: 4, y: 2, z: 0 },
  }, { duration: 360, focusKey: "fit", intent: "explicit" });
  frames.advance(180);
  controller.transitionCamera({
    position: { x: 30, y: 8, z: 240 },
    target: { x: 4, y: 2, z: 0 },
  }, { duration: 180, focusKey: "zoom", intent: "explicit" });
  frames.advance(180);
  controller.restoreCamera();
  frames.advance(250);

  assert.deepEqual(harness.graph.cameraPosition(), { x: 0, y: 0, z: 500 });
  assert.deepEqual(harness.graph.controls().target, { x: 11, y: 22, z: 33 });
  assert.equal(controller.restoreCamera(), false);
  assert.ok(harness.cameraCalls.every((call) => call.duration === 0));
});

test("contextual camera owns one 250ms RAF and reports its transaction independently", () => {
  const harness = graphHarness();
  const frames = frameClockHarness();
  const transitions = [];
  const controller = createGraphInteractionController({
    graph: harness.graph,
    clock: frames.clock,
    frameScheduler: frames.scheduler,
    onCameraTransition: (duration) => transitions.push(duration),
  });

  controller.focusNode(graphFixture().nodes[0], { intent: "contextual" });

  assert.deepEqual(controller.diagnostics(), {
    cameraRafRunning: 1,
    generation: 1,
    pendingCameraTransactionCount: 1,
  });
  assert.deepEqual(transitions, [250]);
  frames.advance(125);
  assert.equal(controller.diagnostics().cameraRafRunning, 1);
  frames.advance(125);
  assert.deepEqual(controller.diagnostics(), {
    cameraRafRunning: 0,
    generation: 1,
    pendingCameraTransactionCount: 0,
  });
});

test("inactive availability suspends live contextual and explicit camera poses without losing restore", () => {
  const cases = [
    {
      name: "contextual",
      start(controller) {
        return controller.focusNode(graphFixture().nodes[0], { intent: "contextual" });
      },
      target: { x: 120, y: 0, z: 120 },
    },
    {
      name: "explicit Fit",
      start(controller) {
        return controller.transitionCamera({
          position: { x: 90, y: 40, z: 210 },
          target: { x: 12, y: 8, z: 0 },
        }, {
          duration: 420,
          focusKey: "fit",
          intent: "explicit",
        });
      },
      target: { x: 90, y: 40, z: 210 },
    },
  ];

  for (const cameraCase of cases) {
    const harness = graphHarness();
    const frames = frameClockHarness();
    const controller = createGraphInteractionController({
      graph: harness.graph,
      clock: frames.clock,
      frameScheduler: frames.scheduler,
    });

    assert.equal(cameraCase.start(controller), true, cameraCase.name);
    frames.advance(100);
    const suspendedPosition = structuredClone(harness.graph.cameraPosition());
    const suspendedTarget = structuredClone(harness.graph.controls().target);
    assert.notDeepEqual(suspendedPosition, cameraCase.target, cameraCase.name);

    controller.syncAvailability({ active: false, reducedMotion: false, terminal: false });

    assert.equal(frames.pendingCount, 0, cameraCase.name);
    assert.equal(frames.cancelled.length, 1, cameraCase.name);
    assert.deepEqual(harness.graph.cameraPosition(), suspendedPosition, cameraCase.name);
    assert.deepEqual(harness.graph.controls().target, suspendedTarget, cameraCase.name);
    assert.equal(controller.diagnostics().pendingCameraTransactionCount, 0, cameraCase.name);

    controller.syncAvailability({ active: true, reducedMotion: false, terminal: false });
    assert.equal(frames.pendingCount, 0, cameraCase.name);
    assert.deepEqual(harness.graph.cameraPosition(), suspendedPosition, cameraCase.name);
    assert.deepEqual(harness.graph.controls().target, suspendedTarget, cameraCase.name);

    assert.equal(controller.restoreCamera(), true, cameraCase.name);
    assert.equal(frames.pendingCount, 1, cameraCase.name);
    frames.advance(250);
    assert.deepEqual(harness.graph.cameraPosition(), { x: 0, y: 0, z: 500 }, cameraCase.name);
    assert.deepEqual(harness.graph.controls().target, { x: 11, y: 22, z: 33 }, cameraCase.name);
    assert.equal(controller.restoreCamera(), false, cameraCase.name);
  }
});

test("camera transactions publish one terminal outcome for abort, completion, reduced motion, and destroy", () => {
  const createCase = () => {
    const harness = graphHarness();
    const frames = frameClockHarness();
    const cameraFrames = [];
    const outcomes = [];
    const controller = createGraphInteractionController({
      graph: harness.graph,
      clock: frames.clock,
      frameScheduler: frames.scheduler,
      onCameraFrame: (pose, progress) => cameraFrames.push({
        pose: structuredClone(pose),
        progress,
      }),
    });
    const start = () => controller.transitionCamera({
      position: { x: 90, y: 40, z: 210 },
      target: { x: 12, y: 8, z: 0 },
    }, {
      duration: 200,
      focusKey: "fit",
      intent: "explicit",
      onAbort: () => outcomes.push("abort"),
      onComplete: () => outcomes.push("complete"),
    });
    return { cameraFrames, controller, frames, harness, outcomes, start };
  };

  const aborted = createCase();
  aborted.start();
  aborted.frames.advance(100);
  const abortedPose = {
    position: structuredClone(aborted.harness.graph.cameraPosition()),
    target: structuredClone(aborted.harness.graph.controls().target),
  };
  aborted.controller.syncAvailability({ active: false, reducedMotion: true });
  aborted.controller.syncAvailability({ active: false });
  aborted.controller.resetCameraContext();
  assert.deepEqual(aborted.outcomes, ["abort"]);
  assert.deepEqual(aborted.cameraFrames.at(-1), { pose: abortedPose, progress: 1 });

  const completed = createCase();
  completed.start();
  completed.frames.advance(200);
  completed.controller.release();
  assert.deepEqual(completed.outcomes, ["complete"]);

  const reduced = createCase();
  reduced.start();
  reduced.frames.advance(50);
  reduced.controller.setReducedMotion(true);
  reduced.controller.release();
  assert.deepEqual(reduced.outcomes, ["complete"]);

  const destroyed = createCase();
  destroyed.start();
  destroyed.controller.release();
  destroyed.controller.release();
  assert.deepEqual(destroyed.outcomes, ["abort"]);
});

test("inactive availability rejects new contextual and explicit camera requests without moving", () => {
  const harness = graphHarness();
  const frames = frameClockHarness();
  const transitions = [];
  const controller = createGraphInteractionController({
    graph: harness.graph,
    clock: frames.clock,
    frameScheduler: frames.scheduler,
    onCameraTransition: (...transition) => transitions.push(transition),
  });

  controller.syncAvailability({ active: false, reducedMotion: false, terminal: false });
  const position = structuredClone(harness.graph.cameraPosition());
  const target = structuredClone(harness.graph.controls().target);
  const cameraCallCount = harness.cameraCalls.length;

  assert.equal(controller.focusNode(graphFixture().nodes[0], { intent: "contextual" }), false);
  assert.equal(controller.transitionCamera({
    position: { x: 90, y: 40, z: 210 },
    target: { x: 12, y: 8, z: 0 },
  }, {
    duration: 420,
    focusKey: "fit",
    intent: "explicit",
  }), false);

  assert.equal(frames.pendingCount, 0);
  assert.equal(controller.diagnostics().cameraRafRunning, 0);
  assert.equal(harness.cameraCalls.length, cameraCallCount);
  assert.deepEqual(harness.graph.cameraPosition(), position);
  assert.deepEqual(harness.graph.controls().target, target);
  assert.deepEqual(transitions, []);
  assert.equal(controller.restoreCamera(), false);
});

test("terminal availability cancels camera RAF and discards the restore baseline", () => {
  const harness = graphHarness();
  const frames = frameClockHarness();
  const controller = createGraphInteractionController({
    graph: harness.graph,
    clock: frames.clock,
    frameScheduler: frames.scheduler,
  });

  controller.focusNode(graphFixture().nodes[0], { intent: "contextual" });
  controller.syncAvailability({ active: false, reducedMotion: false, terminal: true });

  assert.equal(frames.pendingCount, 0);
  assert.equal(controller.diagnostics().cameraRafRunning, 0);
  assert.equal(controller.restoreCamera(), false);
});

test("live reduced motion finishes the active contextual intent immediately without leaving a stale frame", () => {
  const harness = graphHarness();
  const frames = frameClockHarness();
  const controller = createGraphInteractionController({
    graph: harness.graph,
    clock: frames.clock,
    frameScheduler: frames.scheduler,
    reducedMotion: false,
  });

  controller.focusNode(graphFixture().nodes[0], { intent: "contextual" });
  assert.equal(frames.pendingCount, 1);
  controller.setReducedMotion(true);

  assert.equal(frames.pendingCount, 0);
  assert.equal(harness.cameraCalls.at(-1).duration, 0);
  assert.equal(controller.restoreCamera(), true);
  assert.equal(controller.restoreCamera(), false);
});

test("camera context reset cancels the active frame and drops the stale restore baseline", () => {
  const harness = graphHarness();
  const frames = frameClockHarness();
  const controller = createGraphInteractionController({
    graph: harness.graph,
    clock: frames.clock,
    frameScheduler: frames.scheduler,
  });

  controller.focusNode(graphFixture().nodes[0], { intent: "contextual" });
  assert.equal(frames.pendingCount, 1);
  controller.resetCameraContext();

  assert.equal(frames.pendingCount, 0);
  assert.equal(frames.cancelled.length, 1);
  assert.equal(controller.restoreCamera(), false);
});

test("release is idempotent and terminally rejects new camera work", () => {
  const harness = graphHarness();
  const frames = frameClockHarness();
  const controller = createGraphInteractionController({
    graph: harness.graph,
    clock: frames.clock,
    frameScheduler: frames.scheduler,
  });

  controller.focusNode(graphFixture().nodes[0], { intent: "contextual" });
  controller.release();
  controller.release();

  assert.equal(frames.pendingCount, 0);
  assert.deepEqual(controller.diagnostics(), {
    cameraRafRunning: 0,
    generation: 2,
    pendingCameraTransactionCount: 0,
  });
  assert.equal(controller.restoreCamera(), false);
  assert.equal(
    controller.focusNode(graphFixture().nodes[1], { intent: "contextual" }),
    false,
  );
});

test("contextual restore baseline survives renderer replacement through an explicit capsule", () => {
  const firstHarness = graphHarness();
  const first = createGraphInteractionController({
    graph: firstHarness.graph,
    reducedMotion: true,
  });
  first.focusNode(graphFixture().nodes[0], { intent: "contextual" });
  const restoreBaseline = first.exportState();

  const replacementHarness = graphHarness();
  replacementHarness.setPose(
    firstHarness.cameraCalls.at(-1).next,
    firstHarness.cameraCalls.at(-1).lookAt,
  );
  const replacement = createGraphInteractionController({
    graph: replacementHarness.graph,
    reducedMotion: true,
  });

  assert.equal(replacement.importState(restoreBaseline), true);
  assert.equal(replacement.restoreCamera(), true);
  assert.deepEqual(replacementHarness.cameraCalls.at(-1).next, { x: 0, y: 0, z: 500 });
  assert.deepEqual(replacementHarness.cameraCalls.at(-1).lookAt, { x: 11, y: 22, z: 33 });
});

test("presentation transition applies continuous intermediate node and link state", () => {
  const harness = graphHarness();
  const applied = [];
  const nodeObjects = {
    createPresentationTransition() {
      return (progress) => applied.push(["node", progress]);
    },
  };
  const linkObjects = {
    createPresentationTransition() {
      return (progress) => applied.push(["link", progress]);
    },
  };
  const controller = createGraphInteractionController({
    graph: harness.graph,
    nodeObjects,
    linkObjects,
  });

  const transition = controller.preparePresentationTransition({
    selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
  }, graphFixture());
  transition.apply(0);
  transition.apply(0.5);
  transition.apply(1);

  assert.deepEqual(applied, [
    ["node", 0], ["link", 0],
    ["node", 0.5], ["link", 0.5],
    ["node", 1], ["link", 1],
  ]);
});
