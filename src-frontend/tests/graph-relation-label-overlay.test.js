import assert from "node:assert/strict";
import test from "node:test";
import { graphSpatialPolicyFor } from "../graph/spatial-policy.js";

const spatialPolicy = graphSpatialPolicyFor({
  projection_id: "projection-relation-label-tests",
  snapshot_id: "snapshot-relation-label-tests",
  nodes: [],
  links: [],
});

async function loadRelationLabelOverlay() {
  try {
    return await import("../graph/relation-label-overlay.js");
  } catch (error) {
    if (error?.code === "ERR_MODULE_NOT_FOUND") {
      assert.fail("approved relation-label-overlay public seam is not implemented");
    }
    throw error;
  }
}

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  contains(name) {
    return this.values.has(name);
  }

  toggle(name, force) {
    const enabled = force ?? !this.values.has(name);
    if (enabled) this.values.add(name);
    else this.values.delete(name);
    return enabled;
  }
}

class FakeElement {
  constructor(tagName, ownerDocument) {
    this.tagName = String(tagName).toUpperCase();
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.classList = new FakeClassList();
    this.attributes = new Map();
    this.textContent = "";
    this.clientWidth = 640;
    this.clientHeight = 360;
  }

  appendChild(child) {
    child.parentNode?.removeChild?.(child);
    this.children.push(child);
    child.parentNode = this;
    return child;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    child.parentNode = null;
    return child;
  }

  remove() {
    this.parentNode?.removeChild?.(this);
  }

  setAttribute(name, value) {
    this.attributes.set(String(name), String(value));
  }

  getBoundingClientRect() {
    return { x: 0, y: 0, width: 120, height: 28, top: 0, right: 120, bottom: 28, left: 0 };
  }
}

function domHarness() {
  const documentObject = {
    createElement(tagName) {
      return new FakeElement(tagName, documentObject);
    },
  };
  const root = new FakeElement("div", documentObject);
  root.clientWidth = 640;
  root.clientHeight = 360;
  return { documentObject, root };
}

function relation(overrides = {}) {
  return {
    node_type: "relation",
    node_id: "workflow:harnesskit.workflow.harness-creation",
    canonical_id: "harnesskit.workflow.harness-creation",
    source_path: "components/workflows/harness-creation/workflow.yml",
    relation_kind: "workflow",
    name: "Harness Creation",
    exact_count: 6,
    x: 20,
    y: 30,
    z: 40,
    ...overrides,
  };
}

function component() {
  return {
    node_type: "component",
    node_id: "component:harnesskit.agent.component-author",
    component_id: "harnesskit.agent.component-author",
    name: "Component Author",
    x: -20,
    y: -30,
    z: -40,
  };
}

function coherentFrame(overrides = {}) {
  return {
    geometryRevision: 4,
    relations: [relation(), component()],
    selectedId: null,
    focusedId: null,
    camera: { id: "settled-camera" },
    safeInset: Object.freeze({ geometryRevision: 4, top: 12, right: 12, bottom: 12, left: 12 }),
    ...overrides,
  };
}

function presentationFrame(transactionId, progress, reducedMotion = false) {
  return Object.freeze({ transactionId, progress, reducedMotion });
}

test("settled relation anchors project to display-only labels while Component labels stay zero", async () => {
  const { bindRelationLabelOverlay } = await loadRelationLabelOverlay();
  const { root } = domHarness();
  const projections = [];
  const binding = bindRelationLabelOverlay(root, {
    spatialPolicy,
    projectWorldToScreen(world, camera, safeInset) {
      projections.push({ world: { ...world }, camera, safeInset });
      return { x: 300, y: 180, visible: true };
    },
  });

  binding.sync(coherentFrame());

  assert.equal(root.children.length, 1);
  const labels = root.children[0].children;
  assert.equal(labels.length, 1);
  assert.equal(labels[0].textContent, "Harness Creation · 6 steps");
  assert.equal(labels[0].textContent.includes("harnesskit.workflow"), false);
  assert.equal(labels[0].textContent.includes("components/workflows"), false);
  assert.equal(labels[0].textContent.includes("Component Author"), false);
  assert.equal(labels[0].style.transform, "translate3d(240px, 144px, 0)");
  const focusedRelationRadius = spatialPolicy.visualGeometryScale.relationBodyRadius
    * spatialPolicy.visualGeometryScale.focusRimScale
    * spatialPolicy.visualGeometryScale.focusPresentationScale;
  assert.equal(projections.length, 1);
  assert.deepEqual(projections[0].camera, coherentFrame().camera);
  assert.deepEqual(projections[0].safeInset, coherentFrame().safeInset);
  assert.deepEqual(
    { x: projections[0].world.x, y: projections[0].world.y, z: projections[0].world.z },
    { x: 20, y: 30, z: 40 },
  );
  const radiusTolerance = Number.EPSILON * Math.max(1, Math.abs(focusedRelationRadius)) * 8;
  assert.ok(Math.abs(projections[0].world.radius - focusedRelationRadius) <= radiusTolerance);
});

test("collision suppression is deterministic while selected and focused labels are always retained", async () => {
  const { bindRelationLabelOverlay } = await loadRelationLabelOverlay();
  const { root } = domHarness();
  const binding = bindRelationLabelOverlay(root, {
    spatialPolicy,
    projectWorldToScreen() {
      return { x: 320, y: 180, visible: true };
    },
  });
  const alpha = relation({
    node_id: "profile:alpha",
    canonical_id: "harnesskit.profile.alpha",
    relation_kind: "profile",
    name: "Alpha",
    exact_count: 3,
  });
  const beta = relation({
    node_id: "workflow:beta",
    canonical_id: "harnesskit.workflow.beta",
    name: "Beta",
    exact_count: 4,
  });
  const gamma = relation({
    node_id: "profile:gamma",
    canonical_id: "harnesskit.profile.gamma",
    relation_kind: "profile",
    name: "Gamma",
    exact_count: 5,
  });
  const visibleText = () => root.children[0].children
    .filter((label) => label.dataset.relationLabelPresence !== "exiting")
    .map((label) => label.textContent);

  binding.sync(coherentFrame({ relations: [gamma, alpha, beta] }));
  assert.deepEqual(visibleText(), ["Alpha · 3 components"]);

  binding.sync(coherentFrame({ relations: [beta, gamma, alpha] }));
  assert.deepEqual(visibleText(), ["Alpha · 3 components"]);

  binding.sync(coherentFrame({
    relations: [gamma, alpha, beta],
    selectedId: "profile:gamma",
    focusedId: "workflow:beta",
  }));
  assert.deepEqual(visibleText(), [
    "Gamma · 5 components",
    "Beta · 4 steps",
  ]);
});

test("collision exit retains a label for 240ms and unsuppression reuses it without stale cleanup", async () => {
  const { bindRelationLabelOverlay } = await loadRelationLabelOverlay();
  const { root } = domHarness();
  let overlap = false;
  const binding = bindRelationLabelOverlay(root, {
    spatialPolicy,
    projectWorldToScreen(world) {
      return { x: overlap ? 320 : world.x < 50 ? 180 : 460, y: 180, visible: true };
    },
  });
  const alpha = relation({
    node_id: "profile:alpha",
    canonical_id: "harnesskit.profile.alpha",
    relation_kind: "profile",
    name: "Alpha",
  });
  const beta = relation({
    node_id: "workflow:beta",
    canonical_id: "harnesskit.workflow.beta",
    name: "Beta",
    x: 80,
  });

  binding.sync(coherentFrame({
    relations: [alpha, beta],
    presentationFrame: presentationFrame("initial", 0),
  }));
  const container = root.children[0];
  const alphaLabel = container.children.find(
    (label) => label.dataset.relationLabelId === alpha.node_id,
  );
  const betaLabel = container.children.find(
    (label) => label.dataset.relationLabelId === beta.node_id,
  );
  assert.equal(betaLabel.style["--graph-relation-label-opacity"], "0");
  binding.sync(coherentFrame({
    relations: [alpha, beta],
    presentationFrame: presentationFrame("initial", 1),
  }));
  assert.equal(betaLabel.style["--graph-relation-label-opacity"], "1");

  overlap = true;
  const collisionRequest = binding.sync(coherentFrame({
    relations: [beta, alpha],
    presentationFrame: presentationFrame("initial", 1),
  }));
  assert.equal(collisionRequest.transitionRequired, true);
  binding.sync(coherentFrame({
    relations: [beta, alpha],
    presentationFrame: presentationFrame("collision", 0),
  }));
  assert.equal(betaLabel.dataset.relationLabelPresence, "exiting");
  assert.equal(betaLabel.parentNode, container);
  assert.equal(betaLabel.style["--graph-relation-label-opacity"], "1");
  binding.sync(coherentFrame({
    relations: [beta, alpha],
    presentationFrame: presentationFrame("collision", 0.5),
  }));
  assert.equal(betaLabel.style["--graph-relation-label-opacity"], "0.5");
  assert.equal(betaLabel.parentNode, container);

  const retargetRequest = binding.sync(coherentFrame({
    relations: [alpha, { ...beta, name: "Beta updated" }],
    selectedId: beta.node_id,
    presentationFrame: presentationFrame("collision", 0.5),
  }));
  assert.equal(retargetRequest.transitionRequired, true);
  binding.sync(coherentFrame({
    relations: [alpha, { ...beta, name: "Beta updated" }],
    selectedId: beta.node_id,
    presentationFrame: presentationFrame("retarget", 0),
  }));

  const selectedBeta = container.children.find(
    (label) => label.dataset.relationLabelId === beta.node_id,
  );
  assert.equal(selectedBeta, betaLabel);
  assert.equal(selectedBeta.textContent, "Beta updated · 6 steps");
  assert.equal(selectedBeta.dataset.relationLabelState, "selected");
  assert.equal(selectedBeta.dataset.relationLabelPresence, "entering");
  assert.equal(selectedBeta.style["--graph-relation-label-opacity"], "0.5");
  assert.equal(alphaLabel.dataset.relationLabelPresence, "exiting");

  binding.sync(coherentFrame({
    relations: [alpha, beta],
    selectedId: beta.node_id,
    presentationFrame: presentationFrame("retarget", 0.5),
  }));
  assert.equal(selectedBeta.style["--graph-relation-label-opacity"], "0.75");
  assert.equal(selectedBeta.style["--graph-relation-label-emphasis"], "0.5");
  assert.equal(betaLabel.parentNode, container);
  binding.sync(coherentFrame({
    relations: [alpha, beta],
    selectedId: beta.node_id,
    presentationFrame: presentationFrame("retarget", 1),
  }));
  assert.equal(container.children.length, 1);
  assert.equal(container.children[0], betaLabel);
  assert.equal(selectedBeta.style["--graph-relation-label-opacity"], "1");
  assert.equal(selectedBeta.style["--graph-relation-label-emphasis"], "1");
});

test("reduced motion removes a collision-suppressed label immediately", async () => {
  const { bindRelationLabelOverlay } = await loadRelationLabelOverlay();
  const { root } = domHarness();
  let overlap = false;
  const binding = bindRelationLabelOverlay(root, {
    spatialPolicy,
    projectWorldToScreen(world) {
      return { x: overlap ? 320 : world.x < 50 ? 180 : 460, y: 180, visible: true };
    },
  });
  const alpha = relation({ node_id: "profile:alpha", relation_kind: "profile" });
  const beta = relation({ node_id: "workflow:beta", x: 80 });

  binding.sync(coherentFrame({
    relations: [alpha, beta],
    presentationFrame: presentationFrame("initial", 1),
  }));
  const betaLabel = root.children[0].children.find(
    (label) => label.dataset.relationLabelId === beta.node_id,
  );
  overlap = true;
  binding.sync(coherentFrame({
    relations: [beta, alpha],
    presentationFrame: presentationFrame("reduced", 1, true),
  }));

  assert.equal(betaLabel.parentNode, null);
  assert.equal(root.children[0].children.length, 1);
});

test("safe inset geometry revision mismatch skips the complete presentation frame", async () => {
  const { bindRelationLabelOverlay } = await loadRelationLabelOverlay();
  const { root } = domHarness();
  let screenX = 200;
  let projectionCalls = 0;
  const binding = bindRelationLabelOverlay(root, {
    spatialPolicy,
    projectWorldToScreen() {
      projectionCalls += 1;
      return { x: screenX, y: 180, visible: true };
    },
  });

  binding.sync(coherentFrame());
  const previousLabel = root.children[0].children[0];
  const previousTransform = previousLabel.style.transform;
  assert.equal(projectionCalls, 1);

  screenX = 500;
  binding.sync(coherentFrame({
    geometryRevision: 5,
    safeInset: Object.freeze({ geometryRevision: 4, top: 12, right: 12, bottom: 12, left: 12 }),
  }));
  assert.equal(projectionCalls, 1);
  assert.equal(root.children[0].children[0], previousLabel);
  assert.equal(root.children[0].children[0].style.transform, previousTransform);

  binding.sync(coherentFrame({
    geometryRevision: 5,
    safeInset: Object.freeze({ geometryRevision: 5, top: 12, right: 12, bottom: 12, left: 12 }),
  }));
  assert.equal(projectionCalls, 2);
  assert.equal(root.children[0].children[0].style.transform, "translate3d(440px, 144px, 0)");
});

test("refreshOnce, clear, and release remain bounded and idempotent", async () => {
  const { bindRelationLabelOverlay } = await loadRelationLabelOverlay();
  const { root } = domHarness();
  let projectionCalls = 0;
  const binding = bindRelationLabelOverlay(root, {
    spatialPolicy,
    projectWorldToScreen() {
      projectionCalls += 1;
      return { x: 300, y: 180, visible: true };
    },
  });

  binding.sync(coherentFrame());
  const container = root.children[0];
  assert.equal(projectionCalls, 1);
  assert.equal(container.children.length, 1);

  binding.refreshOnce("camera");
  binding.refreshOnce("selection");
  assert.equal(projectionCalls, 3);
  assert.equal(root.children.length, 1);
  assert.equal(root.children[0], container);
  assert.equal(container.children.length, 1);

  binding.clear();
  binding.clear();
  assert.equal(container.children.length, 0);
  binding.refreshOnce("after-clear");
  assert.equal(projectionCalls, 3);
  assert.equal(container.children.length, 0);

  binding.sync(coherentFrame());
  assert.equal(projectionCalls, 4);
  assert.equal(container.children.length, 1);

  binding.release();
  binding.release();
  binding.clear();
  binding.refreshOnce("after-release");
  binding.sync(coherentFrame());
  assert.equal(root.children.length, 0);
  assert.equal(projectionCalls, 4);
});

test("projected orb radiusPx and full label rectangle stay outside the HUD safe inset", async () => {
  const { bindRelationLabelOverlay } = await loadRelationLabelOverlay();
  const { root } = domHarness();
  const binding = bindRelationLabelOverlay(root, {
    spatialPolicy,
    projectWorldToScreen() {
      return { x: 70, y: 92, radiusPx: 24, visible: true };
    },
  });

  binding.sync(coherentFrame({
    safeInset: Object.freeze({
      geometryRevision: 4,
      top: 80,
      right: 20,
      bottom: 20,
      left: 20,
    }),
  }));

  const label = root.children[0].children[0];
  assert.equal(label.style.transform, "translate3d(20px, 80px, 0)");
});

test("selected and focused long identities remain complete and receive distinct protected rectangles", async () => {
  const { bindRelationLabelOverlay } = await loadRelationLabelOverlay();
  const { root } = domHarness();
  const binding = bindRelationLabelOverlay(root, {
    spatialPolicy,
    projectWorldToScreen() {
      return { x: 320, y: 180, radius: 18, visible: true };
    },
  });
  const selected = relation({
    node_id: "profile:selected",
    name: "A complete selected relation identity that must never be truncated",
  });
  const focused = relation({
    node_id: "workflow:focused",
    name: "A complete focused relation identity that must never be truncated",
  });

  binding.sync(coherentFrame({
    relations: [selected, focused],
    selectedId: selected.node_id,
    focusedId: focused.node_id,
  }));

  const labels = root.children[0].children;
  assert.equal(labels.length, 2);
  assert.notEqual(labels[0].style.transform, labels[1].style.transform);
  assert.equal(labels[0].dataset.relationLabelState, "selected");
  assert.equal(labels[1].dataset.relationLabelState, "focused");
  assert.equal(labels[0].textContent, `${selected.name} · 6 steps`);
  assert.equal(labels[1].textContent, `${focused.name} · 6 steps`);
});
