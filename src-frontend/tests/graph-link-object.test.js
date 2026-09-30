import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import {
  createGraphLinkObjectFactory,
  describeGraphLink,
} from "../graph/link-object.js";

function workflowLink() {
  return {
    semantic: "workflow-step",
    link_id: "workflow:implementer",
    source_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
    target_node_id: "component:harnesskit.agent.implementer",
    directionality: "directed",
    workflow_node_id: "workflow:harnesskit.workflow.spec-to-tdd",
    component_node_id: "component:harnesskit.agent.implementer",
    occurrences: [
      { ordinal: 5, step_id: "repair" },
      { ordinal: 2, step_id: "implement" },
      { ordinal: 5, step_id: "repair-duplicate" },
    ],
  };
}

function canvasHarness() {
  const drawnText = [];
  const fillStyles = [];
  return {
    drawnText,
    fillStyles,
    canvasFactory: () => ({
      width: 1,
      height: 1,
      getContext() {
        return {
          clearRect() {},
          fillText(value) { drawnText.push(String(value)); },
          set fillStyle(value) { fillStyles.push(value); },
          set font(_value) {},
          set textAlign(_value) {},
          set textBaseline(_value) {},
        };
      },
    }),
  };
}

function findRole(object, role) {
  return object.children.find((child) => child.userData?.harnesskitRole === role) ?? null;
}

function findParticle(object, ownerLinkId) {
  return object.children.find(
    (child) => child.userData?.harnesskitParticleOwnerLinkId === ownerLinkId,
  ) ?? null;
}

function pointIsOnSegment(point, start, end) {
  const ab = end.clone().sub(start);
  const ap = point.clone().sub(start);
  if (ab.lengthSq() === 0) return point.distanceTo(start) < 1e-9;
  const t = ap.dot(ab) / ab.lengthSq();
  const closest = start.clone().addScaledVector(ab, t);
  return t >= 0 && t <= 1 && closest.distanceTo(point) < 1e-9;
}

test("Workflow incidence is directional and emits distinct ordered ordinals only while active", () => {
  const link = workflowLink();
  const idle = describeGraphLink(link, { activeLinkIds: new Set(), hasFocus: false });
  const active = describeGraphLink(link, {
    activeLinkIds: new Set([link.link_id]),
    hasFocus: true,
  });

  assert.equal(idle.directional, true);
  assert.equal(idle.ordinalLabel, null);
  assert.equal(active.ordinalLabel, "2 · 5");
  assert.equal(active.emphasis, "active");
});

test("focus one-hop visual emphasis is independent from semantic active workflow meaning", () => {
  const link = workflowLink();
  const focused = describeGraphLink(link, {
    activeLinkIds: new Set(),
    selectedOneHopLinkIds: new Set([link.link_id]),
    hasFocus: true,
  });

  assert.equal(focused.semanticActive, false);
  assert.equal(focused.focusOneHop, true);
  assert.equal(focused.emphasis, "focus");
  assert.equal(focused.ordinalLabel, null);
});

test("Profile membership is solid while component cross-link is dashed", () => {
  assert.equal(describeGraphLink({ semantic: "profile-membership" }).dashed, false);
  assert.equal(describeGraphLink({ semantic: "component-cross-link" }).dashed, true);
});

test("typed directionality, not the semantic name, owns direction and particle motion", () => {
  const directedMembership = describeGraphLink({
    semantic: "profile-membership",
    directionality: "directed",
  });
  const unorderedWorkflow = describeGraphLink({
    semantic: "workflow-step",
    directionality: "unordered",
  });
  const missingDirectionality = describeGraphLink({ semantic: "workflow-step" });

  assert.equal(directedMembership.directional, true);
  assert.equal(directedMembership.particleMotion, "source-to-target");
  assert.equal(unorderedWorkflow.directional, false);
  assert.equal(unorderedWorkflow.particleMotion, "ping-pong");
  assert.equal(missingDirectionality.directional, false);
  assert.equal(missingDirectionality.particleMotion, null);
});

test("particles belong only to typed real links and remain on the exact owner path", () => {
  const factory = createGraphLinkObjectFactory({ canvasFactory: canvasHarness().canvasFactory });
  const directed = workflowLink();
  const unordered = {
    semantic: "profile-membership",
    link_id: "profile:engineering:implementer",
    source_node_id: "profile:harnesskit.profile.engineering",
    target_node_id: "component:harnesskit.agent.implementer",
    directionality: "unordered",
  };
  const invalid = {
    semantic: "workflow-step",
    link_id: "missing-directionality",
    source_node_id: directed.source_node_id,
    target_node_id: directed.target_node_id,
  };
  const directedObject = factory.create(directed);
  const unorderedObject = factory.create(unordered);
  const invalidObject = factory.create(invalid);
  const start = { x: -10, y: 2, z: 1 };
  const end = { x: 20, y: 8, z: 7 };

  factory.updatePosition(directedObject, { start, end }, directed);
  factory.updatePosition(unorderedObject, { start, end }, unordered);
  factory.updatePosition(invalidObject, { start, end }, invalid);

  const directedParticle = findParticle(directedObject, directed.link_id);
  const unorderedParticle = findParticle(unorderedObject, unordered.link_id);
  assert.ok(directedParticle, "directed typed link owns a pooled particle");
  assert.ok(unorderedParticle, "unordered typed membership owns a pooled particle");
  assert.equal(findParticle(invalidObject, invalid.link_id), null);
  assert.ok(pointIsOnSegment(
    directedParticle.position,
    new directedParticle.position.constructor(start.x, start.y, start.z),
    new directedParticle.position.constructor(end.x, end.y, end.z),
  ));
  assert.ok(pointIsOnSegment(
    unorderedParticle.position,
    new unorderedParticle.position.constructor(start.x, start.y, start.z),
    new unorderedParticle.position.constructor(end.x, end.y, end.z),
  ));
  factory.dispose();
});

test("ordinal Sprite is preallocated hidden and becomes visible on active Workflow sync", () => {
  const canvas = canvasHarness();
  const factory = createGraphLinkObjectFactory({ canvasFactory: canvas.canvasFactory });
  const link = workflowLink();
  const object = factory.create(link);

  assert.equal(canvas.drawnText.at(-1), "2 · 5");
  assert.equal(findRole(object, "ordinal")?.visible, false);

  assert.equal(factory.updatePosition(object, {
    start: { x: -10, y: 2, z: 1 },
    end: { x: 20, y: 8, z: 7 },
  }, link), true);
  const line = findRole(object, "line");
  assert.deepEqual(Array.from(line.geometry.getAttribute("position").array), [
    -10, 2, 1, 20, 8, 7,
  ]);

  factory.syncAll({
    activeLinkIds: new Set([link.link_id]),
    hasFocus: true,
  });
  assert.equal(canvas.drawnText.at(-1), "2 · 5");
  assert.ok(findRole(object, "ordinal")?.visible);
  assert.deepEqual(findRole(object, "ordinal")?.position.toArray(), [5, 8.2, 4]);
  assert.ok(findRole(object, "direction")?.visible);
  assert.ok(findRole(object, "active-stroke")?.visible);

  factory.syncAll({ activeLinkIds: new Set(), hasFocus: false });
  assert.equal(findRole(object, "ordinal")?.visible, false);
  assert.equal(findRole(object, "direction")?.visible, false);
  assert.equal(findRole(object, "active-stroke")?.visible, false);
  factory.dispose();
});

test("idle and retained relation links use the same neutral color", () => {
  const factory = createGraphLinkObjectFactory({ canvasFactory: canvasHarness().canvasFactory });
  const profileLink = {
    semantic: "profile-membership",
    link_id: "profile:component",
  };
  const workflowStep = workflowLink();
  const profileObject = factory.create(profileLink);
  const workflowObject = factory.create(workflowStep);

  assert.equal(
    findRole(profileObject, "line").material.color.getHex(),
    findRole(workflowObject, "line").material.color.getHex(),
  );

  factory.syncAll({
    activeLinkIds: new Set([profileLink.link_id, workflowStep.link_id]),
    hasFocus: true,
  });
  assert.equal(
    findRole(profileObject, "line").material.color.getHex(),
    findRole(workflowObject, "line").material.color.getHex(),
  );
  assert.ok(findRole(profileObject, "line").material.opacity >= 0.9);
  assert.ok(findRole(workflowObject, "line").material.opacity >= 0.9);
  factory.dispose();
});

test("focus one-hop and semantic active links retain neutral visual weight without sharing meaning", () => {
  const canvas = canvasHarness();
  const factory = createGraphLinkObjectFactory({ canvasFactory: canvas.canvasFactory });
  const link = workflowLink();
  const object = factory.create(link);

  factory.syncAll({
    activeLinkIds: new Set(),
    selectedOneHopLinkIds: new Set([link.link_id]),
    hasFocus: true,
  });

  const focusLine = findRole(object, "line");
  const focusStroke = findRole(object, "active-stroke");
  const focusDirection = findRole(object, "direction");
  const focusParticle = findParticle(object, link.link_id);
  assert.equal(focusLine.userData.harnesskitEmphasis, "focus");
  assert.ok(focusLine.material.opacity >= 0.9);
  assert.ok(focusStroke.visible);
  assert.ok(focusDirection.visible);
  assert.ok(focusParticle.material.opacity >= 0.9);
  assert.equal(findRole(object, "ordinal")?.visible, false);

  factory.syncAll({
    activeLinkIds: new Set([link.link_id]),
    selectedOneHopLinkIds: new Set(),
    hasFocus: true,
  });

  const neutralHex = 0x64748b;
  for (const role of ["line", "active-stroke", "direction"]) {
    assert.equal(findRole(object, role).material.color.getHex(), neutralHex);
  }
  assert.equal(focusParticle.material.color.getHex(), neutralHex);
  assert.equal(canvas.fillStyles.at(-1), "#64748b");
  assert.ok(findRole(object, "ordinal")?.visible);
  factory.dispose();
});

test("presentation transition continuously releases focus one-hop line and stroke weight", () => {
  const factory = createGraphLinkObjectFactory({ canvasFactory: canvasHarness().canvasFactory });
  const link = workflowLink();
  const object = factory.create(link);

  factory.syncAll({
    activeLinkIds: new Set(),
    selectedOneHopLinkIds: new Set([link.link_id]),
    hasFocus: true,
  });
  const transition = factory.createPresentationTransition({
    activeLinkIds: new Set(),
    selectedOneHopLinkIds: new Set(),
    hasFocus: true,
  });

  assert.equal(typeof transition, "function");
  transition(0);
  const startLineOpacity = findRole(object, "line").material.opacity;
  const startStrokeOpacity = findRole(object, "active-stroke").material.opacity;
  const startStrokeWeight = findRole(object, "active-stroke").scale.x;
  transition(0.5);
  const middleLineOpacity = findRole(object, "line").material.opacity;
  const middleStrokeOpacity = findRole(object, "active-stroke").material.opacity;
  const middleStrokeWeight = findRole(object, "active-stroke").scale.x;
  transition(1);
  const finalLineOpacity = findRole(object, "line").material.opacity;
  const finalStrokeWeight = findRole(object, "active-stroke").scale.x;

  assert.equal(startLineOpacity, 1);
  assert.equal(startStrokeOpacity, 1);
  assert.equal(startStrokeWeight, 1);
  assert.ok(middleLineOpacity > 0.3 && middleLineOpacity < 1);
  assert.ok(middleStrokeOpacity > 0 && middleStrokeOpacity < 1);
  assert.ok(middleStrokeWeight > 0.65 && middleStrokeWeight < 1);
  assert.equal(finalLineOpacity, 0.3);
  assert.equal(finalStrokeWeight, 0.65);
  assert.equal(findRole(object, "active-stroke").visible, false);
  factory.dispose();
});

test("presentation material pool is fully preallocated and transitions swap pooled identities", () => {
  const allocations = {
    arrowOrParticle: 0,
    dashedLine: 0,
    solidLine: 0,
  };
  class CountingLineBasicMaterial extends THREE.LineBasicMaterial {
    constructor(...args) {
      super(...args);
      allocations.solidLine += 1;
    }
  }
  class CountingLineDashedMaterial extends THREE.LineDashedMaterial {
    constructor(...args) {
      super(...args);
      allocations.dashedLine += 1;
    }
  }
  class CountingMeshBasicMaterial extends THREE.MeshBasicMaterial {
    constructor(...args) {
      super(...args);
      allocations.arrowOrParticle += 1;
    }
  }
  const factory = createGraphLinkObjectFactory({
    canvasFactory: canvasHarness().canvasFactory,
    three: {
      ...THREE,
      LineBasicMaterial: CountingLineBasicMaterial,
      LineDashedMaterial: CountingLineDashedMaterial,
      MeshBasicMaterial: CountingMeshBasicMaterial,
    },
  });
  const preallocated = { ...allocations };
  assert.deepEqual(preallocated, {
    arrowOrParticle: 24,
    dashedLine: 21,
    solidLine: 21,
  });

  const link = workflowLink();
  const object = factory.create(link);
  factory.syncAll({
    activeLinkIds: new Set(),
    selectedOneHopLinkIds: new Set([link.link_id]),
    hasFocus: true,
  });
  const transition = factory.createPresentationTransition({
    activeLinkIds: new Set(),
    selectedOneHopLinkIds: new Set(),
    hasFocus: true,
  });

  transition(0.5);
  const middleLineMaterial = findRole(object, "line").material;
  const middleStrokeMaterial = findRole(object, "active-stroke").material;
  const middleDirectionMaterial = findRole(object, "direction").material;
  assert.strictEqual(middleStrokeMaterial, middleDirectionMaterial);
  assert.deepEqual(allocations, preallocated, "creating and starting a transition allocate no materials");

  transition(0.51);
  assert.strictEqual(findRole(object, "line").material, middleLineMaterial);
  assert.strictEqual(findRole(object, "active-stroke").material, middleStrokeMaterial);
  assert.strictEqual(findRole(object, "direction").material, middleDirectionMaterial);
  assert.deepEqual(allocations, preallocated, "transition frames only reuse bounded pooled materials");
  factory.dispose();
});

test("focused graph keeps unrelated links visible", () => {
  const factory = createGraphLinkObjectFactory({ canvasFactory: canvasHarness().canvasFactory });
  const link = {
    semantic: "profile-membership",
    link_id: "profile:unrelated-component",
  };
  const object = factory.create(link);

  factory.syncAll({ activeLinkIds: new Set(), hasFocus: true });

  assert.equal(object.userData.harnesskitLinkId, link.link_id);
  assert.ok(findRole(object, "line").material.opacity >= 0.3);
  factory.dispose();
});

test("particle clock advances only while active and stays stable for paused or reduced-motion refreshes", () => {
  let now = 1_000;
  const factory = createGraphLinkObjectFactory({
    canvasFactory: canvasHarness().canvasFactory,
    monotonicClock: () => now,
  });
  const link = workflowLink();
  const object = factory.create(link);
  factory.updatePosition(object, {
    start: { x: 0, y: 0, z: 0 },
    end: { x: 100, y: 0, z: 0 },
  });
  const particle = findParticle(object, link.link_id);

  factory.setActivityState({ active: true, reducedMotion: false });
  particle.onBeforeRender();
  now += 500;
  particle.onBeforeRender();
  const movingX = particle.position.x;

  factory.setActivityState({ active: false });
  now += 30_000;
  particle.onBeforeRender();
  assert.equal(particle.position.x, movingX);

  factory.setActivityState({ active: true });
  particle.onBeforeRender();
  assert.equal(particle.position.x, movingX, "resume excludes inactive wall-clock time");

  factory.setActivityState({ reducedMotion: true });
  now += 30_000;
  particle.onBeforeRender();
  const reducedX = particle.position.x;
  now += 30_000;
  particle.onBeforeRender();
  assert.equal(particle.position.x, reducedX);
  factory.dispose();
});

function relativeLuminance(hex) {
  const channels = [16, 8, 0].map((shift) => ((hex >> shift) & 0xff) / 255)
    .map((channel) => channel <= 0.04045
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4);
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrastRatio(left, right) {
  const brightest = Math.max(relativeLuminance(left), relativeLuminance(right));
  const darkest = Math.min(relativeLuminance(left), relativeLuminance(right));
  return (brightest + 0.05) / (darkest + 0.05);
}

test("idle and unfocused neutral links keep at least 3:1 contrast in light and dark canvases", () => {
  const factory = createGraphLinkObjectFactory({ canvasFactory: canvasHarness().canvasFactory });
  const object = factory.create({ semantic: "profile-membership", link_id: "profile:contrast" });
  const line = findRole(object, "line");
  const backgrounds = [0xf8fafc, 0x0f172a];

  assert.equal(line.material.opacity, 1);
  for (const background of backgrounds) {
    assert.ok(contrastRatio(line.material.color.getHex(), background) >= 3);
  }
  factory.syncAll({ activeLinkIds: new Set(), hasFocus: true });
  assert.equal(line.material.opacity, 0.3);
  for (const background of backgrounds) {
    assert.ok(contrastRatio(line.material.color.getHex(), background) >= 3);
  }
  factory.dispose();
});
