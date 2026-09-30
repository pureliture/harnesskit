import assert from "node:assert/strict";
import test from "node:test";

import { createGraphSceneController } from "../graph/scene-adapter.js";
import { graphSpatialPolicyFor } from "../graph/spatial-policy.js";

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.freeze(value);
  Object.values(value).forEach(deepFreeze);
  return value;
}

function projectionFixture(projectionId = "projection-a") {
  return {
    schema_version: 2,
    projection_id: projectionId,
    snapshot_id: "snapshot-a",
    layout_seed: "seed-a",
    nodes: [
      {
        node_type: "relation",
        node_id: "profile:harnesskit.profile.engineering",
        relation_kind: "Profile",
        canonical_id: "harnesskit.profile.engineering",
        name: "Engineering",
        exact_count: 1,
        anchor_ordinal: 0,
        size_scale: 1.05,
      },
      {
        node_type: "component",
        node_id: "component:harnesskit.agent.code-simplifier",
        component_id: "harnesskit.agent.code-simplifier",
        kind: "agent",
        domain: "engineering",
        relation_degree: 2,
        profile_ids: ["harnesskit.profile.engineering"],
        workflow_ids: ["harnesskit.workflow.review-gated-implementation"],
      },
    ],
    links: [
      {
        link_id: "profile-membership:engineering:code-simplifier",
        profile_node_id: "profile:harnesskit.profile.engineering",
        component_node_id: "component:harnesskit.agent.code-simplifier",
        source_node_id: "profile:harnesskit.profile.engineering",
        target_node_id: "component:harnesskit.agent.code-simplifier",
        directionality: "unordered",
        provenance: "profiles/engineering.yml",
        semantic: "profile-membership",
      },
    ],
    workflows: [],
    issues: [],
  };
}

const expectedPolicySettleTicks = graphSpatialPolicyFor(projectionFixture()).settle.ticks;

function target(width = 960, height = 540) {
  return {
    getBoundingClientRect() {
      return { width, height };
    },
  };
}

function rendererHarness({
  mutateScene = false,
  events = [],
  fitResults = [],
  recoveryCapsule = null,
  retryResult,
} = {}) {
  const counts = {
    create: 0,
    attach: 0,
    detach: 0,
    graphData: 0,
    settle: [],
    pause: 0,
    resize: 0,
    syncPresentation: 0,
    presentationTransitionRequests: [],
    reheat: 0,
    resume: 0,
    dispose: 0,
    fitGraph: 0,
    retry: 0,
    zoomBy: [],
    focusNode: [],
    restoreCamera: 0,
    captureRecoveryCapsule: 0,
    restoreRecoveryCapsules: [],
    activityStates: [],
    viewportGeometries: [],
  };
  let receivedScene = null;

  const createRenderer = () => {
    counts.create += 1;
    events.push("renderer:create");
    return {
      attach() {
        counts.attach += 1;
        events.push("renderer:attach");
      },
      detach() {
        counts.detach += 1;
        events.push("renderer:detach");
      },
      setGraphData(sceneGraph) {
        counts.graphData += 1;
        receivedScene = sceneGraph;
        events.push("renderer:graph-data");
        if (mutateScene) {
          sceneGraph.nodes[0].x = 42;
          sceneGraph.links[0].source = sceneGraph.nodes[0];
          sceneGraph.links[0].target = sceneGraph.nodes[1];
        }
      },
      settle(ticks) {
        counts.settle.push(ticks);
      },
      pauseAnimation() {
        counts.pause += 1;
        events.push("renderer:pause");
      },
      resize() {
        counts.resize += 1;
      },
      syncPresentation() {
        counts.syncPresentation += 1;
      },
      requestPresentationTransition(reason) {
        counts.presentationTransitionRequests.push(reason);
        return true;
      },
      reheat() {
        counts.reheat += 1;
      },
      resumeAnimation() {
        counts.resume += 1;
      },
      dispose() {
        counts.dispose += 1;
        events.push("renderer:dispose");
      },
      fitGraph() {
        counts.fitGraph += 1;
        return fitResults.shift();
      },
      retry() {
        counts.retry += 1;
        return retryResult;
      },
      zoomBy(factor) {
        counts.zoomBy.push(factor);
      },
      focusNode(nodeId) {
        counts.focusNode.push(nodeId);
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
        counts.restoreRecoveryCapsules.push(capsule);
        return capsule === recoveryCapsule;
      },
      setActivityState(state) {
        counts.activityStates.push({ ...state });
      },
      setViewportGeometry(geometry) {
        counts.viewportGeometries.push(structuredClone(geometry));
      },
    };
  };

  return {
    counts,
    createRenderer,
    events,
    get receivedScene() {
      return receivedScene;
    },
  };
}

test("renderer mutation is confined to a deep scene clone and never reaches the frozen backend projection", () => {
  const projection = projectionFixture();
  const canonicalBytes = JSON.stringify(projection);
  deepFreeze(projection);
  const harness = rendererHarness({ mutateScene: true });
  const controller = createGraphSceneController({
    projection,
    createRenderer: harness.createRenderer,
  });

  controller.attach(target());

  assert.notEqual(harness.receivedScene, projection);
  assert.notEqual(harness.receivedScene.nodes, projection.nodes);
  assert.notEqual(harness.receivedScene.nodes[0], projection.nodes[0]);
  assert.equal(harness.receivedScene.nodes[0].x, 42);
  assert.equal(JSON.stringify(projection), canonicalBytes);
  assert.equal("x" in projection.nodes[0], false);
  assert.equal(typeof projection.links[0].source, "undefined");
  controller.dispose();
});

test("one projection settles once and detach or reattach reuses the same paused renderer context", () => {
  const harness = rendererHarness();
  const controller = createGraphSceneController({
    projection: deepFreeze(projectionFixture()),
    createRenderer: harness.createRenderer,
  });

  controller.attach(target(960, 540));
  assert.ok(Number.isInteger(expectedPolicySettleTicks) && expectedPolicySettleTicks > 0);
  assert.equal(harness.counts.create, 1);
  assert.equal(harness.counts.graphData, 1);
  assert.deepEqual(harness.counts.settle, [expectedPolicySettleTicks]);
  assert.equal(harness.counts.pause, 1);
  assert.equal(harness.counts.fitGraph, 1);

  controller.detach();
  controller.attach(target(720, 480));

  assert.equal(harness.counts.create, 1);
  assert.equal(harness.counts.graphData, 1);
  assert.deepEqual(harness.counts.settle, [expectedPolicySettleTicks]);
  assert.equal(harness.counts.pause, 1);
  assert.equal(harness.counts.fitGraph, 1);
  assert.equal(harness.counts.attach, 2);
  assert.equal(harness.counts.detach, 1);
  controller.dispose();
});

test("hover, selection, collapse round-trip, and resize update presentation without reheating layout", () => {
  const harness = rendererHarness();
  const controller = createGraphSceneController({
    projection: deepFreeze(projectionFixture()),
    createRenderer: harness.createRenderer,
  });
  controller.attach(target());
  const settleCalls = [...harness.counts.settle];

  controller.syncPresentation({
    hoveredNodeId: "component:harnesskit.agent.code-simplifier",
    selectedWorkflowId: null,
    lockedWorkflowStep: null,
  });
  assert.equal(controller.requestPresentationTransition("relation-label-collision"), true);
  controller.syncPresentation({
    hoveredNodeId: null,
    selectedWorkflowId: "harnesskit.workflow.review-gated-implementation",
    lockedWorkflowStep: { workflowId: "harnesskit.workflow.review-gated-implementation", ordinal: 2 },
  });
  controller.resize();
  controller.setExpanded(false);
  controller.setExpanded(true);
  controller.resize();

  assert.equal(harness.counts.syncPresentation, 2);
  assert.deepEqual(
    harness.counts.presentationTransitionRequests,
    ["relation-label-collision"],
  );
  assert.ok(harness.counts.resize >= 1);
  assert.deepEqual(harness.counts.settle, settleCalls);
  assert.equal(harness.counts.reheat, 0);
  assert.equal(harness.counts.resume, 0);
  controller.dispose();
});

test("one revision-bound viewport geometry reaches the renderer before Fit", () => {
  const harness = rendererHarness();
  const controller = createGraphSceneController({
    projection: deepFreeze(projectionFixture()),
    createRenderer: harness.createRenderer,
  });
  const geometry = Object.freeze({
    viewportRect: Object.freeze({ x: 0, y: 0, width: 960, height: 540 }),
    safeInset: Object.freeze({
      geometryRevision: 4,
      top: 92,
      right: 12,
      bottom: 12,
      left: 12,
    }),
  });

  controller.setViewportGeometry(geometry);
  controller.attach(target());

  assert.deepEqual(harness.counts.viewportGeometries, [geometry]);
  assert.equal(harness.counts.fitGraph, 1);
  controller.dispose();
});

test("projection replacement disposes the old renderer before the next projection creates a context", () => {
  const events = [];
  const firstHarness = rendererHarness({ events });
  const first = createGraphSceneController({
    projection: deepFreeze(projectionFixture("projection-a")),
    createRenderer: firstHarness.createRenderer,
  });
  first.attach(target());

  first.dispose();
  first.dispose();
  events.push("projection:replace");

  const secondHarness = rendererHarness({ events });
  const second = createGraphSceneController({
    projection: deepFreeze(projectionFixture("projection-b")),
    createRenderer: secondHarness.createRenderer,
  });
  second.attach(target());

  assert.equal(firstHarness.counts.dispose, 1);
  assert.equal(secondHarness.counts.create, 1);
  assert.ok(events.indexOf("renderer:dispose") < events.lastIndexOf("renderer:create"));
  second.dispose();
});

test("camera and WebGL recovery commands stay behind the persistent scene controller", () => {
  const harness = rendererHarness();
  const controller = createGraphSceneController({
    projection: deepFreeze(projectionFixture()),
    createRenderer: harness.createRenderer,
  });

  controller.attach(target());
  controller.zoomBy(1.2);
  controller.zoomBy(0.8);
  controller.fitGraph();
  controller.retry();

  assert.equal("runtimeEvidenceSnapshot" in controller, false);
  assert.equal("triggerContextLossForEvidence" in controller, false);
  assert.equal("restoreCameraForEvidence" in controller, false);
  assert.deepEqual(harness.counts.zoomBy, [1.2, 0.8]);
  assert.equal(harness.counts.fitGraph, 2);
  assert.equal(harness.counts.retry, 1);
  assert.deepEqual(harness.counts.settle, [expectedPolicySettleTicks]);
  controller.dispose();
});

test("same-projection recovery capsule crosses the scene controller without translation", () => {
  const capsule = Object.freeze({
    projectionId: "projection-a",
    settledAnchorMap: new Map(),
    cameraPose: null,
    restoreBaseline: null,
  });
  const harness = rendererHarness({ recoveryCapsule: capsule, retryResult: true });
  const controller = createGraphSceneController({
    projection: deepFreeze(projectionFixture()),
    createRenderer: harness.createRenderer,
  });
  controller.attach(target());

  assert.equal(controller.captureRecoveryCapsule(), capsule);
  assert.equal(controller.retry(capsule), true);
  assert.equal(harness.counts.captureRecoveryCapsule, 1);
  assert.deepEqual(harness.counts.restoreRecoveryCapsules, [capsule]);
  assert.equal(harness.counts.retry, 1);
  controller.dispose();
});

test("explicit node focus is forwarded without mixing camera focus into presentation sync", () => {
  const harness = rendererHarness();
  const controller = createGraphSceneController({
    projection: deepFreeze(projectionFixture()),
    createRenderer: harness.createRenderer,
  });
  controller.attach(target());

  controller.syncPresentation({
    selectedRelationNodeId: "profile:harnesskit.profile.engineering",
  });
  controller.focusNode("workflow:harnesskit.workflow.review-gated-implementation");

  assert.equal(harness.counts.syncPresentation, 1);
  assert.deepEqual(harness.counts.focusNode, [
    "workflow:harnesskit.workflow.review-gated-implementation",
  ]);
  assert.equal(controller.restoreCamera(), true);
  assert.equal(harness.counts.restoreCamera, 1);
  controller.dispose();
});

test("a failed initial Fit remains pending and is retried after WebGL recovery", () => {
  const harness = rendererHarness({
    fitResults: [false, true],
    retryResult: true,
  });
  const controller = createGraphSceneController({
    projection: deepFreeze(projectionFixture()),
    createRenderer: harness.createRenderer,
  });

  controller.attach(target());
  assert.equal(harness.counts.fitGraph, 1);

  assert.equal(controller.retry(), true);
  assert.equal(harness.counts.retry, 1);
  assert.equal(harness.counts.fitGraph, 2);
  controller.dispose();
});

test("a rejected WebGL retry does not consume the pending initial Fit", () => {
  const harness = rendererHarness({
    fitResults: [false],
    retryResult: false,
  });
  const controller = createGraphSceneController({
    projection: deepFreeze(projectionFixture()),
    createRenderer: harness.createRenderer,
  });

  controller.attach(target());
  assert.equal(controller.retry(), false);
  assert.equal(harness.counts.retry, 1);
  assert.equal(harness.counts.fitGraph, 1);
  controller.dispose();
});

test("scene controller forwards disclosure and visibility activity without recreating the renderer", () => {
  const harness = rendererHarness();
  const controller = createGraphSceneController({
    projection: deepFreeze(projectionFixture()),
    createRenderer: harness.createRenderer,
  });

  controller.setIntersecting(false);
  controller.setDocumentVisible(false);
  controller.setReducedMotion(true);
  controller.attach(target());
  controller.setExpanded(false);
  controller.setExpanded(true);
  controller.setIntersecting(true);
  controller.setDocumentVisible(true);

  assert.equal(harness.counts.create, 1);
  assert.deepEqual(harness.counts.activityStates.at(-1), {
    expanded: true,
    foreground: true,
    intersecting: true,
    reducedMotion: true,
  });
  controller.dispose();
});
