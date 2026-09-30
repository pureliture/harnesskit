import assert from "node:assert/strict";
import test from "node:test";
import { BackSide, Raycaster, Vector3 } from "three";

import {
  GRAPH_NODE_VISUAL_METRICS,
  createGraphNodeObjectFactory as createRawGraphNodeObjectFactory,
  describeGraphNode as describeRawGraphNode,
} from "../graph/node-object.js";
import {
  graphNodeVisualEnvelope as rawGraphNodeVisualEnvelope,
  normalizeRelationSizeScale as rawNormalizeRelationSizeScale,
} from "../graph/visual-metrics.js";
import { graphSpatialPolicyFor } from "../graph/spatial-policy.js";

const spatialPolicy = graphSpatialPolicyFor({
  projection_id: "projection-node-object-tests",
  snapshot_id: "snapshot-node-object-tests",
  layout_seed: "node-object-tests",
  nodes: [],
  links: [],
});

function createGraphNodeObjectFactory(options = {}) {
  return createRawGraphNodeObjectFactory({ ...options, spatialPolicy });
}

function describeGraphNode(node) {
  return describeRawGraphNode(node, spatialPolicy);
}

function graphNodeVisualEnvelope(node) {
  return rawGraphNodeVisualEnvelope(node, spatialPolicy);
}

function normalizeRelationSizeScale(value) {
  return rawNormalizeRelationSizeScale(value, spatialPolicy);
}

function canvasHarness() {
  const drawnText = [];
  const drawnCalls = [];
  const strokeCalls = [];
  const canvasFactory = () => {
    let currentFont = "10px sans-serif";
    let currentFillStyle = "#000000";
    let currentStrokeStyle = "#000000";
    let currentLineWidth = 1;
    const canvas = {
      width: 1,
      height: 1,
      getContext() {
      return {
        clearRect() {},
        fillRect() {},
        fillText(text) {
          const value = String(text);
          drawnText.push(value);
          drawnCalls.push({
            fillStyle: currentFillStyle,
            font: currentFont,
            text: value,
            width: this.measureText(value).width,
          });
        },
        strokeText(text) {
          strokeCalls.push({
            font: currentFont,
            lineWidth: currentLineWidth,
            strokeStyle: currentStrokeStyle,
            text: String(text),
          });
        },
        measureText(text) {
          const fontSize = Number(currentFont.match(/([0-9.]+)px/)?.[1] ?? 10);
          return { width: String(text).length * fontSize * 0.6 };
        },
        set fillStyle(value) { currentFillStyle = String(value); },
        get fillStyle() { return currentFillStyle; },
        set strokeStyle(value) { currentStrokeStyle = String(value); },
        get strokeStyle() { return currentStrokeStyle; },
        set lineWidth(value) { currentLineWidth = Number(value); },
        get lineWidth() { return currentLineWidth; },
        set lineJoin(_value) {},
        set font(value) { currentFont = String(value); },
        get font() { return currentFont; },
        set textAlign(_value) {},
        set textBaseline(_value) {},
      };
    },
    };
    return canvas;
  };
  return { canvasFactory, drawnCalls, drawnText, strokeCalls };
}

function findRole(object, role) {
  let found = null;
  object.traverse((child) => {
    if (child.userData?.harnesskitRole === role) found = child;
  });
  return found;
}

function collectDisposableResources(objects) {
  const resources = new Set();
  objects.forEach((object) => {
    object.traverse((child) => {
      // Three.js owns one module-global Sprite geometry; the node factory must not
      // dispose that shared renderer resource. Track only factory-owned geometry.
      if (!child.isSprite && child.geometry?.addEventListener) resources.add(child.geometry);
      const materials = Array.isArray(child.material)
        ? child.material
        : child.material ? [child.material] : [];
      materials.forEach((material) => {
        if (material?.addEventListener) resources.add(material);
        if (material?.map?.addEventListener) resources.add(material.map);
      });
    });
  });
  return resources;
}

test("relation nodes expose canonical label and exact count with bounded featured size", () => {
  const description = describeGraphNode({
    node_type: "relation",
    node_id: "workflow:harnesskit.workflow.spec-to-tdd",
    relation_kind: "Workflow",
    name: "Spec to TDD",
    exact_count: 5,
    size_scale: 9,
  });

  assert.equal(description.tier, "relation");
  assert.equal(description.label, "Spec to TDD");
  assert.equal(description.countLabel, "5 steps");
  assert.equal(description.sizeScale, 1.15);
  assert.equal(description.kindMark, "W");
});

test("component nodes have a kind mark and small orb geometry but never create an inline title texture", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const component = {
    node_type: "component",
    node_id: "component:harnesskit.agent.code-simplifier",
    component_id: "harnesskit.agent.code-simplifier",
    kind: "agent",
  };

  const object = factory.create(component);
  const body = findRole(object, "body");
  const mark = findRole(object, "kind-mark");
  const relationLabel = findRole(object, "relation-label");

  assert.equal(body.geometry.type, "SphereGeometry");
  assert.equal(
    body.geometry.parameters.radius,
    GRAPH_NODE_VISUAL_METRICS.componentGeometry.radius,
  );
  assert.ok(mark?.isSprite);
  assert.equal(relationLabel, null);
  assert.deepEqual(canvas.drawnText, ["A"]);
  assert.equal(canvas.drawnText.some((text) => /code.simplifier/i.test(text)), false);
  factory.dispose();
});

test("Component kind marks accept only the six canonical kinds and keep Workflow relation-only", () => {
  const expectedMarks = new Map([
    ["agent", "A"],
    ["command", "C"],
    ["composite", "M"],
    ["hook", "H"],
    ["rule", "R"],
    ["skill", "S"],
  ]);
  for (const [kind, mark] of expectedMarks) {
    assert.deepEqual(
      { kind: describeGraphNode({ node_type: "component", kind }).kind,
        mark: describeGraphNode({ node_type: "component", kind }).kindMark },
      { kind, mark },
    );
  }
  for (const invalidKind of ["mode", "workflow", "other"]) {
    const description = describeGraphNode({ node_type: "component", kind: invalidKind });
    assert.equal(description.kind, "unknown");
    assert.equal(description.kindMark, "?");
  }
  assert.deepEqual(
    { kind: describeGraphNode({ node_type: "relation", relation_kind: "workflow" }).kind,
      mark: describeGraphNode({ node_type: "relation", relation_kind: "workflow" }).kindMark },
    { kind: "workflow", mark: "W" },
  );
});

test("relation and Component nodes use distinct pooled tiers in one spherical family", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const first = factory.create({
    node_type: "relation",
    node_id: "profile:harnesskit.profile.engineering",
    relation_kind: "Profile",
    name: "Engineering",
    exact_count: 29,
    size_scale: 1.1,
  });
  const second = factory.create({
    node_type: "relation",
    node_id: "workflow:harnesskit.workflow.spec-to-tdd",
    relation_kind: "Workflow",
    name: "Spec to TDD",
    exact_count: 5,
    size_scale: 1.2,
  });
  const firstComponent = factory.create({
    node_type: "component",
    node_id: "component:harnesskit.agent.reference-curator",
    component_id: "harnesskit.agent.reference-curator",
    kind: "agent",
  });
  const secondComponent = factory.create({
    node_type: "component",
    node_id: "component:harnesskit.agent.component-author",
    component_id: "harnesskit.agent.component-author",
    kind: "agent",
  });

  const relationGeometry = findRole(first, "body").geometry;
  const componentGeometry = findRole(firstComponent, "body").geometry;

  assert.equal(relationGeometry.type, "SphereGeometry");
  assert.equal(componentGeometry.type, "SphereGeometry");
  assert.equal(relationGeometry, findRole(second, "body").geometry);
  assert.equal(componentGeometry, findRole(secondComponent, "body").geometry);
  assert.notEqual(relationGeometry, componentGeometry);
  assert.equal(
    relationGeometry.parameters.radius,
    GRAPH_NODE_VISUAL_METRICS.relationGeometry.radius,
  );
  assert.equal(
    componentGeometry.parameters.radius,
    GRAPH_NODE_VISUAL_METRICS.componentGeometry.radius,
  );
  assert.ok(relationGeometry.parameters.radius > componentGeometry.parameters.radius);
  assert.notEqual(firstComponent, secondComponent);
  assert.equal(
    firstComponent.userData.harnesskitNodeId,
    "component:harnesskit.agent.reference-curator",
  );
  assert.equal(firstComponent.userData.harnesskitNodeTier, "component");
  assert.equal(first.userData.harnesskitNodeTier, "relation");
  assert.equal(findRole(first, "relation-label"), null);
  assert.ok(findRole(first, "kind-mark")?.isSprite);
  assert.deepEqual(
    canvas.drawnText.slice(0, 4),
    ["P", "W", "A", "A"],
  );
  factory.dispose();
});

test("relation kind marks stay on the orb while human labels remain DOM-overlay only", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const relation = factory.create({
    node_type: "relation",
    node_id: "workflow:harnesskit.workflow.harness-creation",
    relation_kind: "workflow",
    name: "Harness Creation",
    exact_count: 6,
    size_scale: 1.2,
  });
  const mark = findRole(relation, "kind-mark");

  assert.ok(mark?.isSprite);
  assert.equal(mark.position.x, 0);
  assert.equal(mark.position.y, 0);
  assert.ok(mark.position.z > GRAPH_NODE_VISUAL_METRICS.relationGeometry.radius);
  assert.equal(findRole(relation, "relation-label"), null);
  assert.deepEqual(canvas.drawnText, ["W"]);
  factory.dispose();
});

test("relation and Component kind marks share the neutral light-on-dark treatment", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  factory.create({
    node_type: "relation",
    node_id: "workflow:harnesskit.workflow.harness-creation",
    relation_kind: "workflow",
    name: "Harness Creation",
    exact_count: 6,
  });
  factory.create({
    node_type: "component",
    node_id: "component:harnesskit.agent.reference-curator",
    kind: "agent",
  });

  assert.deepEqual(
    canvas.drawnCalls.map(({ fillStyle, text }) => ({ fillStyle, text })),
    [
      { fillStyle: "#f8fafc", text: "W" },
      { fillStyle: "#f8fafc", text: "A" },
    ],
  );
  assert.deepEqual(canvas.strokeCalls, []);
  factory.dispose();
});

test("idle node bodies and outlines share one neutral hue while relation material stays distinct", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const nodes = [
    factory.create({
      node_type: "component",
      node_id: "component:harnesskit.agent.reference-curator",
      kind: "agent",
    }),
    factory.create({
      node_type: "component",
      node_id: "component:harnesskit.skill.harness-requirements",
      kind: "skill",
    }),
    factory.create({
      node_type: "relation",
      node_id: "profile:harnesskit.profile.engineering",
      relation_kind: "profile",
      name: "Engineering",
      exact_count: 29,
    }),
    factory.create({
      node_type: "relation",
      node_id: "workflow:harnesskit.workflow.harness-creation",
      relation_kind: "workflow",
      name: "Harness Creation",
      exact_count: 6,
    }),
  ];

  const bodies = nodes.map((node) => findRole(node, "body"));
  const outlines = nodes.map((node) => findRole(node, "outline"));
  assert.equal(new Set(bodies.map((body) => body.material)).size, 2);
  assert.equal(new Set(outlines.map((outline) => outline.material)).size, 2);
  assert.equal(new Set(bodies.map((body) => body.material.color.getHex())).size, 1);
  assert.equal(new Set(outlines.map((outline) => outline.material.color.getHex())).size, 1);
  assert.deepEqual(canvas.drawnText, [
    "A",
    "S",
    "P",
    "W",
  ]);
  factory.dispose();
});

test("focused graph keeps unrelated node bodies outlines and labels legible", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const component = factory.create({
    node_type: "component",
    node_id: "component:harnesskit.agent.reference-curator",
    kind: "agent",
  });
  const relation = factory.create({
    node_type: "relation",
    node_id: "profile:harnesskit.profile.engineering",
    relation_kind: "profile",
    name: "Engineering",
    exact_count: 29,
  });

  factory.syncAll({
    activeNodeIds: new Set(["component:harnesskit.agent.reference-curator"]),
    hasFocus: true,
  }, {});

  assert.ok(findRole(relation, "body").material.opacity >= 0.78);
  assert.ok(findRole(relation, "outline").material.opacity >= 0.54);
  assert.ok(findRole(relation, "kind-mark").material.opacity >= 0.88);
  assert.equal(relation.userData.harnesskitEmphasis, "unrelated");
  assert.equal(component.userData.harnesskitEmphasis, "selected");
  factory.dispose();
});

test("visual metrics keep compact relation geometry and non-layout presentation shells", () => {
  assert.equal(GRAPH_NODE_VISUAL_METRICS.componentGeometry.radius, 3);
  assert.equal(GRAPH_NODE_VISUAL_METRICS.relationGeometry.radius, 7.5);
  assert.equal(GRAPH_NODE_VISUAL_METRICS.relationMaximumScale, 1.15);
  assert.equal(GRAPH_NODE_VISUAL_METRICS.outlineShellScale, 1.08);
  assert.equal(GRAPH_NODE_VISUAL_METRICS.focusRimScale, 1.16);
  assert.equal(GRAPH_NODE_VISUAL_METRICS.focusPresentationScale, 1.06);
  assert.equal(GRAPH_NODE_VISUAL_METRICS.hitTargetScale, 1.35);
  assert.equal(normalizeRelationSizeScale(1), 1);
  assert.equal(normalizeRelationSizeScale(1.1), 1.075);
  assert.equal(normalizeRelationSizeScale(1.2), 1.15);
  const componentEnvelope = graphNodeVisualEnvelope({ node_type: "component" });
  const relationEnvelope = graphNodeVisualEnvelope({ node_type: "relation", size_scale: 1.2 });
  assert.ok(Math.abs(componentEnvelope.max.x - (3 * 1.16 * 1.06)) < 1e-9);
  assert.ok(Math.abs(relationEnvelope.max.x - (7.5 * 1.15 * 1.16 * 1.06)) < 1e-9);
});

test("idle hit target raycasts outside the outline and misses outside its 1.35 sphere", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const component = factory.create({
    node_type: "component",
    node_id: "component:hit-target",
    kind: "skill",
  });
  const hitTarget = findRole(component, "hit-target");
  const outlineRadius = GRAPH_NODE_VISUAL_METRICS.componentGeometry.radius
    * GRAPH_NODE_VISUAL_METRICS.outlineShellScale;
  const hitRadius = GRAPH_NODE_VISUAL_METRICS.componentGeometry.radius
    * GRAPH_NODE_VISUAL_METRICS.hitTargetScale;
  const raycaster = new Raycaster();

  assert.ok(hitTarget?.isMesh);
  assert.equal(hitTarget.visible, true);
  assert.equal(hitTarget.material.colorWrite, false);
  assert.equal(hitTarget.scale.x, GRAPH_NODE_VISUAL_METRICS.hitTargetScale);
  component.updateMatrixWorld(true);

  const insideX = (outlineRadius + hitRadius) / 2;
  raycaster.set(new Vector3(insideX, 0, 10), new Vector3(0, 0, -1));
  assert.ok(raycaster.intersectObject(hitTarget, false).length > 0);

  raycaster.set(new Vector3(hitRadius + 0.05, 0, 10), new Vector3(0, 0, -1));
  assert.equal(raycaster.intersectObject(hitTarget, false).length, 0);

  factory.syncAll({ activeNodeIds: new Set(), hasFocus: true });
  assert.equal(hitTarget.visible, true);
  factory.dispose();
});

test("node factory keeps selected bodies neutral and reserves the single rim and scale for primary focus", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const selected = factory.create({
    node_type: "component",
    node_id: "component:selected",
    kind: "agent",
  });
  const adjacent = factory.create({
    node_type: "component",
    node_id: "component:adjacent",
    kind: "skill",
  });
  const contextual = factory.create({
    node_type: "relation",
    node_id: "profile:contextual",
    relation_kind: "profile",
    name: "Contextual",
  });
  const unrelated = factory.create({
    node_type: "component",
    node_id: "component:unrelated",
    kind: "rule",
  });

  factory.syncAll({
    emphasisByNodeId: new Map([
      ["component:selected", "selected"],
      ["component:adjacent", "adjacent"],
      ["profile:contextual", "contextual"],
      ["component:unrelated", "unrelated"],
    ]),
    hasFocus: true,
  }, { focusNodeId: "component:selected" });

  assert.equal(selected.userData.harnesskitEmphasis, "selected");
  assert.equal(adjacent.userData.harnesskitEmphasis, "adjacent");
  assert.equal(contextual.userData.harnesskitEmphasis, "contextual");
  assert.equal(unrelated.userData.harnesskitEmphasis, "unrelated");
  assert.equal(findRole(selected, "body").material.emissive.getHex(), 0x000000);
  assert.equal(
    findRole(selected, "body").material,
    findRole(selected, "body").userData.harnesskitIdleMaterial,
  );
  assert.equal(findRole(adjacent, "body").material.emissive.getHex(), 0x000000);
  assert.equal(findRole(contextual, "body").material.opacity >= 0.8, true);
  assert.equal(findRole(unrelated, "body").material.opacity >= 0.78, true);
  assert.equal(findRole(unrelated, "kind-mark").material.opacity >= 0.88, true);
  assert.equal(findRole(selected, "focus-rim").visible, true);
  assert.equal(findRole(adjacent, "focus-rim").visible, false);
  assert.equal(findRole(selected, "presentation-group").scale.x, 1.06);
  assert.equal(findRole(adjacent, "presentation-group").scale.x, 1);
  factory.dispose();
});

test("long canonical relation labels are never rasterized into the 3D scene", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });

  factory.create({
    node_type: "relation",
    node_id: "workflow:harnesskit.workflow.review-gated-implementation",
    relation_kind: "Workflow",
    name: "Review Gated Implementation",
    exact_count: 6,
    size_scale: 1.2,
  });

  assert.deepEqual(canvas.drawnText, ["W"]);
  assert.equal(
    canvas.drawnText.some((text) => text.includes("Review Gated Implementation")),
    false,
  );
  factory.dispose();
});

test("direct Component selection uses one neutral focus rim without changing the root hit envelope", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const component = factory.create({
    node_type: "component",
    node_id: "component:harnesskit.skill.tdd",
    component_id: "harnesskit.skill.tdd",
    kind: "skill",
  });
  const body = findRole(component, "body");
  const outline = findRole(component, "outline");
  const rim = findRole(component, "focus-rim");
  const presentationGroup = findRole(component, "presentation-group");
  const hitTarget = findRole(component, "hit-target");

  assert.equal(outline.isMesh, true);
  assert.equal(outline.geometry, body.geometry);
  assert.equal(outline.material.side, BackSide);
  assert.equal(outline.scale.x, GRAPH_NODE_VISUAL_METRICS.outlineShellScale);
  assert.ok(outline.scale.x > 1);
  assert.equal(rim.isMesh, true);
  assert.equal(rim.geometry, body.geometry);
  assert.equal(rim.visible, false);
  assert.equal(presentationGroup.scale.x, 1);
  assert.equal(hitTarget.parent, component);
  factory.syncAll({
    activeNodeIds: new Set(["component:harnesskit.skill.tdd"]),
    hasFocus: true,
  }, {
    focusNodeId: "component:harnesskit.skill.tdd",
  });

  assert.equal(rim.visible, true);
  assert.equal(rim.scale.x, GRAPH_NODE_VISUAL_METRICS.focusRimScale);
  assert.equal(presentationGroup.scale.x, 1.06);
  assert.equal(hitTarget.scale.x, GRAPH_NODE_VISUAL_METRICS.hitTargetScale);
  assert.equal(body.material.emissive.getHex(), 0x000000);
  assert.equal(outline.material, outline.userData.harnesskitIdleOutlineMaterial);

  factory.syncAll({ activeNodeIds: new Set(), hasFocus: false }, {});
  assert.equal(rim.visible, false);
  assert.equal(presentationGroup.scale.x, 1);
  factory.dispose();
});

test("selected emphasis without an explicit focus node leaves every rim and presentation scale inactive", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const component = factory.create({
    node_type: "component",
    node_id: "component:no-primary-focus",
    kind: "skill",
  });

  factory.syncAll({
    emphasisByNodeId: new Map([["component:no-primary-focus", "selected"]]),
    hasFocus: true,
  }, { selectedComponentId: "no-primary-focus" });

  assert.equal(findRole(component, "focus-rim").visible, false);
  assert.equal(findRole(component, "presentation-group").scale.x, 1);
  factory.dispose();
});

test("presentation transition holds initial body and outline materials before easing their emphasis", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const selected = factory.create({
    node_type: "component",
    node_id: "component:transition-selected",
    kind: "skill",
  });
  const unrelated = factory.create({
    node_type: "component",
    node_id: "component:transition-unrelated",
    kind: "agent",
  });
  const selectedOutline = findRole(selected, "outline");
  const unrelatedBody = findRole(unrelated, "body");
  const unrelatedOutline = findRole(unrelated, "outline");

  factory.syncAll({ activeNodeIds: new Set(), hasFocus: false }, {});
  const initialSelectedOutline = selectedOutline.material;
  const initialUnrelatedBody = unrelatedBody.material;
  const initialUnrelatedOutline = unrelatedOutline.material;
  const transition = factory.createPresentationTransition({
    emphasisByNodeId: new Map([
      ["component:transition-selected", "selected"],
      ["component:transition-unrelated", "unrelated"],
    ]),
    hasFocus: true,
  }, {});

  transition(0);
  assert.equal(selectedOutline.material, initialSelectedOutline);
  assert.equal(unrelatedBody.material, initialUnrelatedBody);
  assert.equal(unrelatedOutline.material, initialUnrelatedOutline);

  transition(0.5);
  assert.notEqual(selectedOutline.material, initialSelectedOutline);
  assert.notEqual(selectedOutline.material.color.getHex(), 0xcbd5e1);
  assert.notEqual(selectedOutline.material.color.getHex(), 0xf8fafc);
  assert.ok(unrelatedBody.material.opacity > 0.78);
  assert.ok(unrelatedBody.material.opacity < 0.96);
  assert.ok(unrelatedOutline.material.opacity > 0.54);
  assert.ok(unrelatedOutline.material.opacity < 0.68);

  transition(1);
  assert.equal(selectedOutline.material.color.getHex(), 0xf8fafc);
  assert.equal(unrelatedBody.material.opacity, 0.78);
  assert.equal(unrelatedOutline.material.opacity, 0.54);
  factory.dispose();
});

test("selected outline exits before a focus rim begins its entry tween", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const component = factory.create({
    node_type: "component",
    node_id: "component:selected-to-focus",
    kind: "skill",
  });
  const outline = findRole(component, "outline");
  const rim = findRole(component, "focus-rim");
  const presentationGroup = findRole(component, "presentation-group");
  const selectedModel = {
    emphasisByNodeId: new Map([["component:selected-to-focus", "selected"]]),
    hasFocus: true,
  };

  factory.syncAll(selectedModel, {});
  const selectedOutline = outline.material;
  const idleOutline = outline.userData.harnesskitIdleOutlineMaterial;
  const sharedFinalRimMaterial = rim.material;
  assert.notEqual(selectedOutline, idleOutline);
  assert.equal(rim.visible, false);

  const acquire = factory.createPresentationTransition(
    selectedModel,
    { focusNodeId: "component:selected-to-focus" },
  );

  acquire(0);
  assert.equal(outline.material, selectedOutline);
  assert.equal(rim.visible, false);
  assert.equal(presentationGroup.scale.x, 1);

  acquire(0.25);
  assert.notEqual(outline.material, idleOutline);
  assert.equal(rim.visible, false);
  assert.equal(presentationGroup.scale.x, 1);

  acquire(0.5);
  assert.equal(outline.material, idleOutline);
  assert.equal(rim.visible, false);
  assert.equal(presentationGroup.scale.x, 1);
  assert.equal(rim.material.opacity, 0);

  acquire(0.5001);
  assert.equal(outline.material, idleOutline);
  assert.equal(rim.visible, true);
  assert.ok(presentationGroup.scale.x > 1);
  assert.ok(presentationGroup.scale.x < 1.0001);

  acquire(0.51);
  assert.equal(outline.material, idleOutline);
  assert.equal(rim.visible, true);
  assert.ok(rim.material.opacity > 0);
  assert.ok(rim.material.opacity < 0.1);

  acquire(0.75);
  assert.equal(outline.material, idleOutline);
  assert.equal(rim.visible, true);
  assert.equal(presentationGroup.scale.x, 1.03);
  assert.equal(rim.material.opacity, 0.5);
  acquire(1);
  assert.equal(rim.material, sharedFinalRimMaterial);
  assert.equal(rim.material.opacity, 1);
  factory.dispose();
});

test("presentation transition preserves pooled targets while easing per-node presentation state", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const component = factory.create({
    node_type: "component",
    node_id: "component:transition-focus",
    kind: "skill",
  });
  const body = findRole(component, "body");
  const outline = findRole(component, "outline");
  const rim = findRole(component, "focus-rim");
  const mark = findRole(component, "kind-mark");
  const presentationGroup = findRole(component, "presentation-group");
  const bodyGeometry = body.geometry;
  const markTexture = mark.material.map;
  const resourcesBefore = collectDisposableResources([component]);

  factory.syncAll({ activeNodeIds: new Set(), hasFocus: false }, {});
  const transition = factory.createPresentationTransition({
    emphasisByNodeId: new Map([["component:transition-focus", "selected"]]),
    hasFocus: true,
  }, { focusNodeId: "component:transition-focus" }, { appearance: "dark" });

  assert.equal(body.material, body.userData.harnesskitIdleMaterial);
  assert.equal(outline.material, outline.userData.harnesskitIdleOutlineMaterial);
  assert.equal(body.geometry, bodyGeometry);
  assert.equal(mark.material.map, markTexture);
  assert.equal(collectDisposableResources([component]).size, resourcesBefore.size);
  transition(0.5);
  assert.equal(presentationGroup.scale.x, 1);
  assert.equal(rim.visible, false);
  assert.equal(rim.material.opacity, 0);
  assert.equal(mark.material.opacity, 1);
  transition(0.75);
  assert.equal(presentationGroup.scale.x, 1.03);
  transition(1);
  assert.equal(presentationGroup.scale.x, 1.06);
  assert.equal(rim.material.opacity, 1);
  const sharedFinalRimMaterial = rim.material;

  const clear = factory.createPresentationTransition({ activeNodeIds: new Set(), hasFocus: false }, {});
  clear(0);
  assert.equal(rim.visible, true);
  assert.equal(presentationGroup.scale.x, 1.06);
  assert.equal(rim.scale.x, 1.16);
  assert.equal(rim.material, sharedFinalRimMaterial);
  clear(0.5);
  assert.equal(rim.visible, true);
  assert.equal(presentationGroup.scale.x, 1.03);
  assert.equal(rim.scale.x, 1.12);
  assert.equal(rim.material.opacity, 0.5);
  const halfwayClearMaterial = rim.material;
  clear(0.5);
  assert.equal(rim.material, halfwayClearMaterial);
  clear(0.99);
  assert.equal(rim.visible, true);
  assert.ok(presentationGroup.scale.x > 1);
  assert.ok(rim.scale.x > 1.08);
  assert.ok(rim.material.opacity > 0);
  assert.ok(rim.material.opacity < 0.05);
  clear(1);
  assert.equal(rim.visible, false);
  assert.equal(presentationGroup.scale.x, 1);
  assert.equal(rim.scale.x, 1.08);
  assert.equal(rim.material.opacity, 0);
  assert.equal(collectDisposableResources([component]).size, resourcesBefore.size);
  factory.dispose();
});

test("240ms focus retarget reaches a neutral atomic midpoint regardless of node order", () => {
  for (const nodeOrder of [
    ["component:focus-a", "component:focus-b"],
    ["component:focus-b", "component:focus-a"],
  ]) {
    const canvas = canvasHarness();
    const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
    const nodes = new Map(nodeOrder.map((nodeId) => [
      nodeId,
      factory.create({
        node_type: "component",
        node_id: nodeId,
        kind: "skill",
      }),
    ]));
    const focusA = nodes.get("component:focus-a");
    const focusB = nodes.get("component:focus-b");

    factory.syncAll({
      emphasisByNodeId: new Map([
        ["component:focus-a", "selected"],
        ["component:focus-b", "adjacent"],
      ]),
      hasFocus: true,
    }, { focusNodeId: "component:focus-a" });

    const pooledRimMaterial = findRole(focusA, "focus-rim").material;
    assert.equal(findRole(focusB, "focus-rim").material, pooledRimMaterial);
    assert.equal(pooledRimMaterial.opacity, 1);

    const retarget = factory.createPresentationTransition({
      emphasisByNodeId: new Map([
        ["component:focus-a", "adjacent"],
        ["component:focus-b", "selected"],
      ]),
      hasFocus: true,
    }, { focusNodeId: "component:focus-b" });

    const transactionDurationMs = 240;
    const midpointMs = transactionDurationMs / 2;
    const epsilonMs = 2.4;
    const cueMagnitude = (node) => {
      const rim = findRole(node, "focus-rim");
      const group = findRole(node, "presentation-group");
      return (group.scale.x - 1) + (rim.visible ? rim.scale.x - 1.08 : 0);
    };
    for (const elapsedMs of [
      0,
      60,
      midpointMs - epsilonMs,
      midpointMs,
      midpointMs + epsilonMs,
      180,
      transactionDurationMs,
    ]) {
      retarget(elapsedMs / transactionDurationMs);
      let expectedOwner = [];
      if (elapsedMs < midpointMs) expectedOwner = ["component:focus-a"];
      if (elapsedMs > midpointMs) expectedOwner = ["component:focus-b"];
      const visibleRims = [focusA, focusB]
        .filter((node) => findRole(node, "focus-rim").visible)
        .map((node) => node.userData.harnesskitNodeId);
      const scaledGroups = [focusA, focusB]
        .filter((node) => findRole(node, "presentation-group").scale.x > 1)
        .map((node) => node.userData.harnesskitNodeId);
      assert.deepEqual(visibleRims, expectedOwner);
      assert.deepEqual(scaledGroups, expectedOwner);
      assert.ok(visibleRims.length <= 1);
      assert.ok(scaledGroups.length <= 1);
      assert.equal(pooledRimMaterial.opacity, 1);
    }

    retarget((midpointMs - epsilonMs) / transactionDurationMs);
    const beforeHandoffMagnitude = cueMagnitude(focusA);
    const beforeHandoffOpacity = findRole(focusA, "focus-rim").material.opacity;
    retarget(midpointMs / transactionDurationMs);
    const handoffMagnitude = cueMagnitude(focusA) + cueMagnitude(focusB);
    const handoffOpacities = [focusA, focusB].map(
      (node) => findRole(node, "focus-rim").material.opacity,
    );
    retarget((midpointMs + epsilonMs) / transactionDurationMs);
    const afterHandoffMagnitude = cueMagnitude(focusB);
    const afterHandoffOpacity = findRole(focusB, "focus-rim").material.opacity;
    assert.ok(beforeHandoffMagnitude > 0 && beforeHandoffMagnitude < 0.01);
    assert.ok(beforeHandoffOpacity > 0 && beforeHandoffOpacity < 0.1);
    assert.equal(handoffMagnitude, 0);
    assert.deepEqual(handoffOpacities, [0, 0]);
    assert.ok(afterHandoffMagnitude > 0 && afterHandoffMagnitude < 0.01);
    assert.ok(afterHandoffOpacity > 0 && afterHandoffOpacity < 0.1);
    assert.ok(Math.abs(beforeHandoffMagnitude - afterHandoffMagnitude) < 1e-12);
    assert.equal(beforeHandoffOpacity, afterHandoffOpacity);

    retarget(0.25);
    const pooledFrameMaterials = {
      focusABody: findRole(focusA, "body").material,
      focusAOutline: findRole(focusA, "outline").material,
      focusBBody: findRole(focusB, "body").material,
      focusBOutline: findRole(focusB, "outline").material,
    };
    retarget(0.25);
    assert.equal(findRole(focusA, "body").material, pooledFrameMaterials.focusABody);
    assert.equal(findRole(focusA, "outline").material, pooledFrameMaterials.focusAOutline);
    assert.equal(findRole(focusB, "body").material, pooledFrameMaterials.focusBBody);
    assert.equal(findRole(focusB, "outline").material, pooledFrameMaterials.focusBOutline);
    assert.equal(findRole(focusA, "presentation-group").scale.x, 1.03);
    assert.equal(findRole(focusA, "focus-rim").scale.x, 1.12);
    assert.equal(findRole(focusA, "focus-rim").material.opacity, 0.5);
    assert.equal(findRole(focusB, "presentation-group").scale.x, 1);

    retarget(0.5);
    assert.equal(findRole(focusA, "presentation-group").scale.x, 1);
    assert.equal(findRole(focusA, "focus-rim").visible, false);
    assert.equal(findRole(focusB, "presentation-group").scale.x, 1);
    assert.equal(findRole(focusB, "focus-rim").scale.x, 1.08);
    assert.equal(findRole(focusB, "focus-rim").visible, false);
    assert.equal(findRole(focusA, "focus-rim").material.opacity, 0);
    assert.equal(findRole(focusB, "focus-rim").material.opacity, 0);
    assert.equal(
      findRole(focusB, "outline").material,
      findRole(focusB, "outline").userData.harnesskitIdleOutlineMaterial,
    );
    assert.equal(pooledRimMaterial.opacity, 1);

    retarget(0.75);
    assert.equal(findRole(focusB, "presentation-group").scale.x, 1.03);
    assert.equal(findRole(focusB, "focus-rim").scale.x, 1.12);
    assert.equal(findRole(focusB, "focus-rim").material.opacity, 0.5);

    retarget(1);
    assert.equal(findRole(focusB, "presentation-group").scale.x, 1.06);
    assert.equal(findRole(focusB, "focus-rim").material, pooledRimMaterial);
    assert.equal(pooledRimMaterial.opacity, 1);

    const clear = factory.createPresentationTransition({
      emphasisByNodeId: new Map(),
      hasFocus: false,
    }, { focusNodeId: null });
    clear(0.5);
    assert.deepEqual([focusA, focusB]
      .filter((node) => findRole(node, "focus-rim").visible)
      .map((node) => node.userData.harnesskitNodeId), ["component:focus-b"]);
    assert.deepEqual([focusA, focusB]
      .filter((node) => findRole(node, "presentation-group").scale.x > 1)
      .map((node) => node.userData.harnesskitNodeId), ["component:focus-b"]);
    assert.equal(pooledRimMaterial.opacity, 1);

    factory.dispose();
  }
});

test("replacement retarget continues from the exact current visual owner pose", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const nodes = new Map(["component:cancel-a", "component:cancel-b", "component:cancel-c"].map(
    (nodeId) => [nodeId, factory.create({
      node_type: "component",
      node_id: nodeId,
      kind: "skill",
    })],
  ));
  const focusA = nodes.get("component:cancel-a");
  const focusB = nodes.get("component:cancel-b");
  const focusC = nodes.get("component:cancel-c");

  factory.syncAll({
    emphasisByNodeId: new Map([["component:cancel-a", "selected"]]),
    hasFocus: true,
  }, { focusNodeId: "component:cancel-a" });

  const firstRetarget = factory.createPresentationTransition({
    emphasisByNodeId: new Map([["component:cancel-b", "selected"]]),
    hasFocus: true,
  }, { focusNodeId: "component:cancel-b" });
  firstRetarget(0.25);
  const interruptedScale = findRole(focusA, "presentation-group").scale.x;
  const interruptedRimScale = findRole(focusA, "focus-rim").scale.x;
  assert.equal(interruptedScale, 1.03);
  assert.equal(interruptedRimScale, 1.12);

  const replacement = factory.createPresentationTransition({
    emphasisByNodeId: new Map([["component:cancel-c", "selected"]]),
    hasFocus: true,
  }, { focusNodeId: "component:cancel-c" });
  assert.equal(findRole(focusA, "presentation-group").scale.x, interruptedScale);
  assert.equal(findRole(focusA, "focus-rim").scale.x, interruptedRimScale);
  assert.equal(findRole(focusA, "focus-rim").visible, true);
  assert.equal(findRole(focusB, "focus-rim").visible, false);
  assert.equal(findRole(focusC, "focus-rim").visible, false);

  replacement(0);
  assert.equal(findRole(focusA, "presentation-group").scale.x, interruptedScale);
  assert.equal(findRole(focusA, "focus-rim").scale.x, interruptedRimScale);

  replacement(0.5);
  assert.equal(findRole(focusA, "focus-rim").visible, false);
  assert.equal(findRole(focusA, "presentation-group").scale.x, 1);
  assert.equal(findRole(focusC, "focus-rim").visible, false);
  assert.equal(findRole(focusC, "presentation-group").scale.x, 1);
  assert.equal(findRole(focusC, "focus-rim").scale.x, 1.08);
  factory.dispose();
});

test("same-focus replacement continues from the current pooled rim opacity", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const component = factory.create({
    node_type: "component",
    node_id: "component:hold-current-rim",
    kind: "skill",
  });
  const rim = findRole(component, "focus-rim");
  const model = {
    emphasisByNodeId: new Map([["component:hold-current-rim", "selected"]]),
    hasFocus: true,
  };
  const sharedFinalRimMaterial = rim.material;
  const acquire = factory.createPresentationTransition(
    model,
    { focusNodeId: "component:hold-current-rim" },
  );
  acquire(0.75);
  const currentRimMaterial = rim.material;
  assert.equal(currentRimMaterial.opacity, 0.5);

  const replacement = factory.createPresentationTransition(
    model,
    { focusNodeId: "component:hold-current-rim" },
  );
  assert.equal(rim.material, currentRimMaterial);
  replacement(0);
  assert.equal(rim.material, currentRimMaterial);
  replacement(0.5);
  assert.equal(rim.material.opacity, 0.75);
  replacement(1);
  assert.equal(rim.material, sharedFinalRimMaterial);
  assert.equal(rim.material.opacity, 1);
  factory.dispose();
});

test("appearance switches reuse preallocated neutral materials without rebuilding focus resources", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const component = factory.create({
    node_type: "component",
    node_id: "component:appearance",
    kind: "skill",
  });
  const body = findRole(component, "body");
  const rim = findRole(component, "focus-rim");

  factory.syncAll({ activeNodeIds: new Set(["component:appearance"]), hasFocus: true }, {
    focusNodeId: "component:appearance",
  }, { appearance: "dark" });
  const darkBody = body.material;
  const darkRim = rim.material;
  assert.equal(darkBody.color.getHex(), 0x475569);
  assert.equal(darkRim.color.getHex(), 0xf8fafc);

  factory.syncAll({ activeNodeIds: new Set(["component:appearance"]), hasFocus: true }, {
    focusNodeId: "component:appearance",
  }, { appearance: "light" });
  assert.equal(body.material.color.getHex(), 0x64748b);
  assert.equal(rim.material.color.getHex(), 0x0f172a);

  factory.syncAll({ activeNodeIds: new Set(["component:appearance"]), hasFocus: true }, {
    focusNodeId: "component:appearance",
  }, { appearance: "dark" });
  assert.equal(body.material, darkBody);
  assert.equal(rim.material, darkRim);
  factory.dispose();
});

test("pooled orb resources preserve node identity and dispose exactly once", () => {
  const canvas = canvasHarness();
  const factory = createGraphNodeObjectFactory({ canvasFactory: canvas.canvasFactory });
  const objects = [
    factory.create({
      node_type: "component",
      node_id: "component:harnesskit.agent.reference-curator",
      component_id: "harnesskit.agent.reference-curator",
      kind: "agent",
    }),
    factory.create({
      node_type: "component",
      node_id: "component:harnesskit.agent.component-author",
      component_id: "harnesskit.agent.component-author",
      kind: "agent",
    }),
    factory.create({
      node_type: "relation",
      node_id: "profile:harnesskit.profile.engineering",
      relation_kind: "Profile",
      name: "Engineering",
      exact_count: 29,
    }),
    factory.create({
      node_type: "relation",
      node_id: "profile:harnesskit.profile.harness-maintenance",
      relation_kind: "Profile",
      name: "Harness Maintenance",
      exact_count: 11,
    }),
  ];
  const orbRoles = [
    "body",
    "hit-target",
    "outline",
    "focus-rim",
  ];
  const orbGeometries = new Set(objects.flatMap((object) => (
    orbRoles.map((role) => findRole(object, role).geometry)
  )));
  const resources = collectDisposableResources(objects);
  const disposeCount = new Map([...resources].map((resource) => [resource, 0]));
  resources.forEach((resource) => {
    resource.addEventListener("dispose", () => {
      disposeCount.set(resource, disposeCount.get(resource) + 1);
    });
  });

  const [firstComponent, secondComponent, firstRelation, secondRelation] = objects;
  assert.equal(
    findRole(firstComponent, "body").material,
    findRole(secondComponent, "body").material,
  );
  assert.equal(
    findRole(firstComponent, "outline").material,
    findRole(secondComponent, "outline").material,
  );
  assert.equal(
    findRole(firstRelation, "body").material,
    findRole(secondRelation, "body").material,
  );
  assert.equal(findRole(firstComponent, "focus-rim").material, findRole(firstRelation, "focus-rim").material);

  factory.dispose();
  factory.dispose();

  assert.equal(orbGeometries.size, 2);
  assert.ok(resources.size > 0);
  assert.deepEqual(new Set(disposeCount.values()), new Set([1]));
  assert.throws(
    () => factory.create({ node_type: "component", node_id: "component:late" }),
    /node object factory is disposed/,
  );
});
