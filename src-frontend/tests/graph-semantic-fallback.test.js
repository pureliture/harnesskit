import assert from "node:assert/strict";
import test from "node:test";

import {
  bindSemanticGraphFallback,
  renderSemanticGraphFallback,
} from "../graph/semantic-fallback.js";

function projectionFixture() {
  return {
    schema_version: 2,
    nodes: [
      {
        node_type: "relation",
        node_id: "profile:harnesskit.profile.engineering",
        relation_kind: "Profile",
        canonical_id: "harnesskit.profile.engineering",
        name: "Engineering",
        exact_count: 1,
      },
      {
        node_type: "relation",
        node_id: "workflow:harnesskit.workflow.spec-to-tdd",
        relation_kind: "Workflow",
        canonical_id: "harnesskit.workflow.spec-to-tdd",
        name: "Spec to TDD",
        exact_count: 2,
      },
      {
        node_type: "component",
        node_id: "component:harnesskit.agent.writer",
        component_id: "harnesskit.agent.writer",
        kind: "agent",
        relation_degree: 2,
        profile_ids: ["harnesskit.profile.engineering"],
        workflow_ids: ["harnesskit.workflow.spec-to-tdd"],
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
        occurrences: [
          { ordinal: 1, step_id: "plan" },
          { ordinal: 2, step_id: "review" },
        ],
      },
    ],
  };
}

function componentCatalogFixture() {
  return [
    {
      component_id: "harnesskit.agent.writer",
      title: "Requirements Writer",
      kind: "agent",
    },
    {
      component_id: "harnesskit.skill.reviewer",
      title: "Quality Reviewer",
      kind: "skill",
    },
  ];
}

function visibleText(markup) {
  return markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function ariaLabels(markup) {
  return [...markup.matchAll(/\saria-label="([^"]*)"/g)].map((match) => match[1]);
}

test("semantic fallback renders every entity once, relation memberships, ordered steps, and retry without SVG", () => {
  const html = renderSemanticGraphFallback({
    projection: projectionFixture(),
    presentation: {
      selectedWorkflowId: "harnesskit.workflow.spec-to-tdd",
      lockedWorkflowStep: {
        workflowId: "harnesskit.workflow.spec-to-tdd",
        ordinal: 2,
      },
    },
    rendererState: { availability: "context_lost", reason: "webglcontextlost" },
  });

  assert.equal((html.match(/data-semantic-node-id="component:harnesskit\.agent\.writer"/g) ?? []).length, 1);
  assert.match(html, /Engineering/);
  assert.match(html, /Spec to TDD/);
  assert.match(html, /data-semantic-workflow-step="1"/);
  assert.match(html, /data-semantic-workflow-step="2"[^>]*aria-pressed="true"/);
  assert.match(html, /data-graph-retry/);
  assert.doesNotMatch(html, /<svg|data-graph-edge/);
  assert.match(
    html,
    /<ul[^>]*class="semantic-graph__components"[^>]*>\s*<li>\s*<button[^>]*data-semantic-node-id=/s,
  );
  assert.doesNotMatch(html, /<button[^>]*role="listitem"/);
});

test("semantic controls keep canonical keys internal while exposing human names, kind badges, and useful counts", () => {
  const html = renderSemanticGraphFallback({
    projection: projectionFixture(),
    componentCatalog: componentCatalogFixture(),
    presentation: {},
    rendererState: { availability: "context_lost", reason: "webglcontextlost" },
  });

  assert.match(
    html,
    /<section[^>]*id="component-map-semantic-view"[^>]*tabindex="-1"[^>]*aria-label="Component Map semantic view"/,
  );
  assert.match(
    html,
    /id="semantic-relation:profile:harnesskit\.profile\.engineering"[^>]*aria-label="Engineering, Profile relation, 1 component"/,
  );
  assert.match(
    html,
    /<article[^>]*id="semantic-relation-group:profile:harnesskit\.profile\.engineering"[^>]*role="group"[^>]*aria-label="Profile relation Engineering, 1 component"/,
  );
  assert.match(
    html,
    /<ul[^>]*id="semantic-relation-members:profile:harnesskit\.profile\.engineering"[^>]*aria-label="Engineering connected components"[^>]*>\s*<li[^>]*id="semantic-relation-member:profile:harnesskit\.profile\.engineering:harnesskit\.agent\.writer"[^>]*>\s*<span[^>]*class="component-kind-badge"[^>]*>AGENT<\/span>\s*<span>Requirements Writer<\/span>/s,
  );
  assert.match(
    html,
    /<article[^>]*id="semantic-relation-group:workflow:harnesskit\.workflow\.spec-to-tdd"[^>]*aria-label="Workflow relation Spec to TDD, 2 authored steps"/,
  );
  assert.match(
    html,
    /id="semantic-component:harnesskit\.agent\.writer"[^>]*aria-label="Requirements Writer, Agent, 2 relations"/,
  );
  assert.match(
    html,
    /id="semantic-workflow-step:harnesskit\.workflow\.spec-to-tdd:1:plan"[^>]*aria-label="Step 1 Plan, Agent"/,
  );
  assert.match(html, /<strong>Requirements Writer<\/strong>/);
  assert.match(html, /class="component-kind-badge"[^>]*>AGENT<\/span>/);
  assert.match(html, /<span>2 relations<\/span>/);
  assert.equal(ariaLabels(html).some((label) => label.includes("harnesskit.")), false);
  assert.doesNotMatch(visibleText(html), /harnesskit\./);
  assert.match(
    html,
    /id="component-map-renderer-retry"[^>]*aria-label="Component Map renderer retry"/,
  );
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length);
});

test("same authored Workflow step is one control with encoded, unique Component occurrence evidence", () => {
  const projection = projectionFixture();
  projection.nodes.push({
    node_type: "component",
    node_id: "component:harnesskit.skill.reviewer",
    component_id: "harnesskit.skill.reviewer",
    kind: "skill",
  });
  projection.links = projection.links.filter((link) => link.semantic !== "workflow-step");
  projection.links.push(
    {
      semantic: "workflow-step",
      link_id: "workflow:writer",
      workflow_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
      component_node_id: "component:harnesskit.agent.writer",
      source_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
      target_node_id: "component:harnesskit.agent.writer",
      directionality: "directed",
      occurrences: [{
        ordinal: 1,
        step_id: "quality review|β",
        source_field: "steps[0].agent refs",
      }],
    },
    {
      semantic: "workflow-step",
      link_id: "workflow:reviewer",
      workflow_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
      component_node_id: "component:harnesskit.skill.reviewer",
      source_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
      target_node_id: "component:harnesskit.skill.reviewer",
      directionality: "directed",
      occurrences: [{
        ordinal: 1,
        step_id: "quality review|β",
        source_field: "steps[0].skill|refs",
      }],
    },
  );

  const html = renderSemanticGraphFallback({
    projection,
    presentation: {},
    rendererState: { availability: "ready", reason: null },
  });

  assert.equal((html.match(/id="semantic-workflow-step:/g) ?? []).length, 1);
  assert.match(
    html,
    /id="semantic-workflow-step:harnesskit\.workflow\.spec-to-tdd:1:quality%20review%7C%CE%B2"/,
  );
  assert.match(
    html,
    /id="semantic-workflow-occurrence:harnesskit\.workflow\.spec-to-tdd:1:harnesskit\.agent\.writer\|quality%20review%7C%CE%B2\|steps%5B0%5D\.agent%20refs"/,
  );
  assert.match(
    html,
    /id="semantic-workflow-occurrence:harnesskit\.workflow\.spec-to-tdd:1:harnesskit\.skill\.reviewer\|quality%20review%7C%CE%B2\|steps%5B0%5D\.skill%7Crefs"/,
  );
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(ids.some((id) => /\s/.test(id)), false);
});

test("ready renderer keeps an equivalent keyboard list without fallback-only retry copy", () => {
  const html = renderSemanticGraphFallback({
    projection: projectionFixture(),
    presentation: {},
    rendererState: { availability: "ready", reason: null },
  });

  assert.match(html, /Component Map 키보드 탐색 목록/);
  assert.doesNotMatch(html, /3D Component Map 대체 목록/);
  assert.doesNotMatch(html, /data-graph-retry/);
});

test("pending renderer is a non-retryable preparation state, not an error fallback", () => {
  const html = renderSemanticGraphFallback({
    projection: projectionFixture(),
    presentation: {},
    rendererState: {
      availability: "pending",
      reason: "3D 그래프를 준비하고 있습니다.",
    },
  });

  assert.match(html, /Component Map 준비 중/);
  assert.doesNotMatch(html, /3D Component Map 대체 목록|data-graph-retry/);
});

test("semantic Fit report does not coerce unavailable camera metrics to zero", () => {
  const html = renderSemanticGraphFallback({
    projection: projectionFixture(),
    presentation: {},
    rendererState: {
      availability: "ready",
      reason: null,
      fitReport: {
        identityCount: 3,
        camera: {
          status: "unavailable",
          inFrustumEnvelopeCount: null,
          totalEnvelopeCount: null,
          largestDimensionOccupancy: null,
        },
      },
    },
  });

  assert.match(html, /Component Map Fit: identity 3개/);
  assert.match(html, /camera coverage 계산 불가/);
  assert.doesNotMatch(html, /camera envelope 0\/0|최대 축 사용 0%/);
});

test("complete Fit report exposes stable typed AX counts, camera coverage, and 3D bounds", () => {
  const html = renderSemanticGraphFallback({
    projection: projectionFixture(),
    presentation: {},
    rendererState: {
      availability: "ready",
      reason: null,
      fitReport: {
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
      },
    },
  });

  assert.match(html, /id="component-map-fit-report"/);
  assert.match(html, /identity count=3/);
  assert.match(html, /relation count=2/);
  assert.match(html, /component count=1/);
  assert.match(html, /envelope count=3/);
  assert.match(html, /camera status=complete/);
  assert.match(html, /in-frustum=3/);
  assert.match(html, /total envelopes=3/);
  assert.match(html, /largest dimension occupancy=0\.820000/);
  assert.match(html, /bounds min=-100\.000000,-20\.000000,-4\.000000/);
  assert.match(html, /bounds max=100\.000000,20\.000000,5\.200000/);
});

test("camera pose exposes stable typed AX position and target vectors", () => {
  const html = renderSemanticGraphFallback({
    projection: projectionFixture(),
    rendererState: {
      availability: "ready",
      cameraPose: {
        position: { x: 52, y: -18, z: 240 },
        target: { x: 4, y: 5, z: 6 },
      },
    },
  });

  assert.match(html, /id="component-map-camera-pose"/);
  assert.match(
    html,
    /aria-label="Camera pose; position=52\.000000,-18\.000000,240\.000000; target=4\.000000,5\.000000,6\.000000"/,
  );
});

test("semantic mirror publishes canonical projection and settled-position identity only as a valid pair", () => {
  const projection = projectionFixture();
  projection.projection_id = "projection-semantic";
  const valid = renderSemanticGraphFallback({
    projection,
    rendererState: {
      availability: "ready",
      sceneLayoutIdentity: {
        projectionId: projection.projection_id,
        settledNodePositionsHash: "0123456789abcdef",
      },
    },
  });

  assert.match(
    valid,
    /id="component-map-projection:projection-semantic"[^>]*data-graph-projection-id/,
  );
  assert.match(
    valid,
    /id="component-map-settled-hash:0123456789abcdef"[^>]*data-graph-settled-node-positions-hash/,
  );

  for (const sceneLayoutIdentity of [
    { projectionId: "projection-other", settledNodePositionsHash: "0123456789abcdef" },
    { projectionId: projection.projection_id, settledNodePositionsHash: "not-a-hash" },
    { projectionId: projection.projection_id },
  ]) {
    const invalid = renderSemanticGraphFallback({
      projection,
      rendererState: { availability: "ready", sceneLayoutIdentity },
    });
    assert.doesNotMatch(invalid, /data-graph-projection-id/);
    assert.doesNotMatch(invalid, /data-graph-settled-node-positions-hash/);
  }
});

test("semantic Component selection marks every owning Profile relation without selecting Workflow", () => {
  const projection = projectionFixture();
  projection.nodes.push({
    node_type: "relation",
    node_id: "profile:harnesskit.profile.work",
    relation_kind: "Profile",
    canonical_id: "harnesskit.profile.work",
    name: "Work",
    exact_count: 1,
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
  const html = renderSemanticGraphFallback({
    projection,
    presentation: { selectedComponentId: "harnesskit.agent.writer" },
    rendererState: { availability: "ready", reason: null },
  });

  assert.match(html, /<button(?=[^>]*id="semantic-relation:profile:harnesskit\.profile\.engineering")(?=[^>]*aria-pressed="true")[^>]*>/);
  assert.match(html, /<button(?=[^>]*id="semantic-relation:profile:harnesskit\.profile\.work")(?=[^>]*aria-pressed="true")[^>]*>/);
  assert.match(html, /<button(?=[^>]*id="semantic-relation:workflow:harnesskit\.workflow\.spec-to-tdd")(?=[^>]*aria-pressed="false")[^>]*>/);
});

test("semantic fallback exposes one explicit active Profile identity when a Component has multiple owners", () => {
  const projection = projectionFixture();
  projection.nodes.push({
    node_type: "relation",
    node_id: "profile:harnesskit.profile.work",
    relation_kind: "Profile",
    canonical_id: "harnesskit.profile.work",
    name: "Work",
    exact_count: 1,
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

  const html = renderSemanticGraphFallback({
    projection,
    presentation: {
      activeProfileId: "harnesskit.profile.work",
      selectedComponentId: "harnesskit.agent.writer",
    },
    rendererState: { availability: "ready", reason: null },
  });

  assert.equal((html.match(/id="component-map-active-profile:/g) ?? []).length, 1);
  assert.match(
    html,
    /id="component-map-active-profile:harnesskit\.profile\.work"[^>]*aria-label="Active profile"/,
  );
});

class FakeRoot {
  constructor() {
    this.innerHTML = "";
    this.listeners = new Map();
    this.scrollTop = 0;
  }

  addEventListener(type, listener) { this.listeners.set(type, listener); }
  removeEventListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
  }
  emit(type, target, key = null, relatedTarget = null) {
    this.listeners.get(type)?.({
      type,
      key,
      target,
      preventDefault() {},
      relatedTarget,
    });
  }
}

function targetFor(dataset) {
  const candidate = { dataset };
  return {
    closest(selector) {
      const mapping = {
        "[data-semantic-node-id]": "semanticNodeId",
        "[data-semantic-workflow-step]": "semanticWorkflowStep",
        "[data-graph-retry]": "graphRetry",
      };
      return mapping[selector] in dataset ? candidate : null;
    },
  };
}

test("fallback delegates node, step, retry, hover, and Escape through the graph action callbacks", () => {
  const root = new FakeRoot();
  const selected = [];
  const hovered = [];
  const steps = [];
  const stepHovers = [];
  let retries = 0;
  let backgrounds = 0;
  const binding = bindSemanticGraphFallback(root, {
    projection: projectionFixture(),
    getPresentation: () => ({}),
    getRendererState: () => ({ availability: "unavailable", reason: "webgl" }),
    onNodeHover: (nodeId) => hovered.push(nodeId),
    onNodeSelect: (node) => selected.push(node.node_id),
    onWorkflowStepSelect: (step) => steps.push(step),
    onWorkflowStepHover: (step) => stepHovers.push(step),
    onRetry: () => { retries += 1; },
    onBackgroundSelect: () => { backgrounds += 1; },
  });

  const component = targetFor({ semanticNodeId: "component:harnesskit.agent.writer" });
  root.emit("focusin", component);
  root.emit("click", component);
  const step = targetFor({
    semanticWorkflowStep: "2",
    workflowId: "harnesskit.workflow.spec-to-tdd",
  });
  root.emit("focusin", step);
  root.emit("click", step);
  root.emit("focusout", step);
  root.emit("click", targetFor({ graphRetry: "" }));
  root.emit("keydown", targetFor({}), "Escape");

  assert.deepEqual(hovered, ["component:harnesskit.agent.writer"]);
  assert.deepEqual(selected, ["component:harnesskit.agent.writer"]);
  assert.deepEqual(steps, [{
    workflowId: "harnesskit.workflow.spec-to-tdd",
    ordinal: 2,
  }]);
  assert.deepEqual(stepHovers, [{
    workflowId: "harnesskit.workflow.spec-to-tdd",
    ordinal: 2,
  }, null]);
  assert.equal(retries, 1);
  assert.equal(backgrounds, 1);
  assert.match(root.innerHTML, /role="region"/);
  binding.release();
  assert.equal(root.listeners.size, 0);
});

test("semantic mirror rerender restores the same logical keyboard focus without publishing a duplicate hover", () => {
  const root = new FakeRoot();
  root.ownerDocument = { activeElement: null };
  root.contains = (candidate) => candidate === root.ownerDocument.activeElement;
  let focused = null;
  let replacement = null;
  root.querySelectorAll = () => replacement ? [replacement] : [];
  const originalInnerHtml = Object.getOwnPropertyDescriptor(root, "innerHTML");
  let markup = originalInnerHtml?.value ?? "";
  Object.defineProperty(root, "innerHTML", {
    configurable: true,
    get() { return markup; },
    set(value) {
      markup = value;
      root.scrollTop = 0;
      replacement = {
        dataset: { semanticNodeId: "component:harnesskit.agent.writer" },
        focus(options) {
          focused = options;
          root.ownerDocument.activeElement = this;
          root.emit("focusin", targetFor(this.dataset));
        },
      };
    },
  });
  let hoverCount = 0;
  const binding = bindSemanticGraphFallback(root, {
    projection: projectionFixture(),
    getPresentation: () => ({ selectedComponentId: "harnesskit.agent.writer" }),
    getRendererState: () => ({ availability: "ready", reason: null }),
    onNodeHover: () => { hoverCount += 1; },
  });
  const active = replacement;
  root.ownerDocument.activeElement = active;
  root.scrollTop = 287;

  binding.render();

  assert.notEqual(replacement, active);
  assert.deepEqual(focused, { preventScroll: true });
  assert.equal(hoverCount, 0);
  assert.equal(root.scrollTop, 287);
  binding.release();
});

test("semantic Workflow focus restoration includes encoded step identity, not ordinal alone", () => {
  const root = new FakeRoot();
  root.ownerDocument = { activeElement: null };
  root.contains = (candidate) => candidate === root.ownerDocument.activeElement;
  let replacements = [];
  let focusedStepId = null;
  root.querySelectorAll = () => replacements;
  let markup = "";
  Object.defineProperty(root, "innerHTML", {
    configurable: true,
    get() { return markup; },
    set(value) {
      markup = value;
      replacements = ["plan", "review"].map((semanticWorkflowStepId) => ({
        dataset: {
          semanticWorkflowStep: "1",
          semanticWorkflowStepId,
          workflowId: "harnesskit.workflow.spec-to-tdd",
        },
        focus() {
          focusedStepId = semanticWorkflowStepId;
          root.ownerDocument.activeElement = this;
        },
      }));
    },
  });
  const binding = bindSemanticGraphFallback(root, {
    projection: projectionFixture(),
    getPresentation: () => ({}),
    getRendererState: () => ({ availability: "ready", reason: null }),
  });
  root.ownerDocument.activeElement = replacements[1];

  binding.render();

  assert.equal(focusedStepId, "review");
  binding.release();
});

test("nested child mouse transitions stay inside one semantic control without hover churn", () => {
  const root = new FakeRoot();
  const hovered = [];
  const binding = bindSemanticGraphFallback(root, {
    projection: projectionFixture(),
    getPresentation: () => ({}),
    getRendererState: () => ({ availability: "ready", reason: null }),
    onNodeHover: (nodeId) => hovered.push(nodeId),
  });
  const control = {
    dataset: { semanticNodeId: "component:harnesskit.agent.writer" },
    contains(candidate) { return candidate?.semanticControl === this; },
  };
  const child = () => ({
    semanticControl: control,
    closest(selector) {
      return selector === "[data-semantic-node-id]" ? control : null;
    },
  });
  const label = child();
  const metadata = child();

  root.emit("mouseover", label);
  root.emit("mouseout", label, null, metadata);
  root.emit("mouseover", metadata, null, label);

  assert.deepEqual(hovered, ["component:harnesskit.agent.writer"]);

  root.emit("mouseout", metadata);
  assert.deepEqual(hovered, ["component:harnesskit.agent.writer", null]);
  binding.release();
});
