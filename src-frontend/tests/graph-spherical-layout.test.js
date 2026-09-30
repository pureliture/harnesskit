import assert from "node:assert/strict";
import test from "node:test";

import {
  graphNodeLayoutEnvelope,
  graphNodeVisualEnvelope,
} from "../graph/visual-metrics.js";
import { graphSpatialPolicyFor } from "../graph/spatial-policy.js";

const metricSpatialPolicy = graphSpatialPolicyFor({
  projection_id: "projection-spherical-metric-tests",
  snapshot_id: "snapshot-spherical-metric-tests",
  nodes: [],
  links: [],
});

async function loadSphericalLayout() {
  try {
    return await import("../graph/spherical-layout.js");
  } catch (error) {
    if (error?.code === "ERR_MODULE_NOT_FOUND") {
      assert.fail("approved spherical-layout public seam is not implemented");
    }
    throw error;
  }
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.freeze(value);
  Object.values(value).forEach(deepFreeze);
  return value;
}

function largeProjectionFixture() {
  const nodes = Array.from({ length: 93 }, (_, index) => {
    const nodeId = `node-${String(index).padStart(3, "0")}`;
    if (index < 6) {
      return {
        node_type: "relation",
        node_id: nodeId,
        relation_kind: index === 5 ? "Unprofiled" : index % 2 === 0 ? "Profile" : "Workflow",
        name: `Relation ${index}`,
        exact_count: 12 + index,
        anchor_ordinal: index,
        size_scale: 1 + index * 0.02,
      };
    }
    return {
      node_type: "component",
      node_id: nodeId,
      component_id: `harnesskit.agent.fixture-${index}`,
      kind: index % 2 === 0 ? "agent" : "skill",
      domain: "engineering",
      relation_degree: 2,
    };
  });
  const links = Array.from({ length: 128 }, (_, index) => ({
    link_id: `link-${String(index).padStart(3, "0")}`,
    source_node_id: nodes[index % nodes.length].node_id,
    target_node_id: nodes[(index * 7 + 11) % nodes.length].node_id,
    directionality: index % 3 === 0 ? "directed" : "unordered",
    semantic: index % 3 === 0 ? "workflow-step" : "profile-membership",
  }));
  return {
    schema_version: 2,
    projection_id: "projection-spherical-large",
    snapshot_id: "snapshot-spherical-large",
    layout_seed: "approved-spherical-seed",
    nodes: [...nodes].reverse(),
    links: [...links].reverse(),
    workflows: [],
    issues: [],
  };
}

function sortedIdentity(values, key) {
  return values.map((value) => String(value?.[key] ?? "")).sort();
}

function normalizedAnchorBytes(anchorMap) {
  const entries = anchorMap instanceof Map
    ? [...anchorMap.entries()]
    : Array.isArray(anchorMap)
      ? anchorMap.map((value) => [value.node_id, value])
      : Object.entries(anchorMap ?? {});
  return JSON.stringify(entries
    .map(([nodeId, value]) => [String(nodeId), {
      x: Number(value?.x),
      y: Number(value?.y),
      z: Number(value?.z),
    }])
    .sort(([left], [right]) => left.localeCompare(right)));
}

function anchors(points) {
  return new Map(points.map(([nodeId, x, y, z]) => [nodeId, { x, y, z }]));
}

function visualEnvelopeRadius(node) {
  const envelope = graphNodeVisualEnvelope(node, metricSpatialPolicy);
  return (envelope.max.x - envelope.min.x) / 2;
}

test("compact visual geometry does not alter the legacy layout envelope contract", () => {
  const component = graphNodeLayoutEnvelope({
    node_type: "component",
    node_id: "component:layout",
    x: 0,
    y: 0,
    z: 0,
  }, metricSpatialPolicy);
  const relation = graphNodeLayoutEnvelope({
    node_type: "relation",
    node_id: "workflow:layout",
    size_scale: 1.2,
    x: 0,
    y: 0,
    z: 0,
  }, metricSpatialPolicy);
  const visualRelation = graphNodeVisualEnvelope({
    node_type: "relation",
    node_id: "workflow:visual",
    size_scale: 1.2,
    x: 0,
    y: 0,
    z: 0,
  }, metricSpatialPolicy);

  assert.ok(Math.abs(component.max.x - 5.36) < 1e-12);
  assert.ok(Math.abs(relation.max.x - 22.512) < 1e-12);
  assert.ok(visualRelation.max.x < relation.max.x);
});

function visualEnvelopeOverlapReport(nodes) {
  let overlapCount = 0;
  let maximumOverlap = 0;
  for (let leftIndex = 0; leftIndex < nodes.length; leftIndex += 1) {
    const left = nodes[leftIndex];
    for (let rightIndex = leftIndex + 1; rightIndex < nodes.length; rightIndex += 1) {
      const right = nodes[rightIndex];
      const distance = Math.hypot(
        Number(right.x) - Number(left.x),
        Number(right.y) - Number(left.y),
        Number(right.z) - Number(left.z),
      );
      const overlap = visualEnvelopeRadius(left) + visualEnvelopeRadius(right) - distance;
      if (overlap <= 1e-6) continue;
      overlapCount += 1;
      maximumOverlap = Math.max(maximumOverlap, overlap);
    }
  }
  return { overlapCount, maximumOverlap };
}

function metric(report, name) {
  const aliases = {
    depthToLongest: ["depthToLongest", "depth_to_longest"],
    shortestToLongest: ["shortestToLongest", "shortest_to_longest"],
    p90ToMedian: ["p90ToMedian", "p90_to_median", "radiusP90ToMedian"],
    maximumToP95: ["maximumToP95", "maximum_to_p95", "radiusMaximumToP95"],
    innerDensity: ["innerDensity", "inner_density"],
  };
  for (const key of aliases[name] ?? [name]) {
    const direct = report?.[key];
    if (Number.isFinite(Number(direct))) return Number(direct);
    const nested = report?.ratios?.[key] ?? report?.radius?.[key];
    if (Number.isFinite(Number(nested))) return Number(nested);
  }
  assert.fail(`layout metric ${name} is missing`);
}

function acceptedDenseVolume() {
  return anchors([
    ["outer-x+", 10, 0, 0],
    ["outer-x-", -10, 0, 0],
    ["outer-y+", 0, 10, 0],
    ["outer-y-", 0, -10, 0],
    ["outer-z+", 0, 0, 10],
    ["outer-z-", 0, 0, -10],
    ["inner-x+", 2, 0, 0],
    ["inner-x-", -2, 0, 0],
    ["inner-y+", 0, 2, 0],
    ["inner-z+", 0, 0, 2],
  ]);
}

test("prepared scene preserves the exact 93-node and 128-link projection without synthetic identity", async () => {
  const { prepareSphericalScene } = await loadSphericalLayout();
  assert.equal(typeof prepareSphericalScene, "function");
  const projection = largeProjectionFixture();
  const sourceBytes = JSON.stringify(projection);
  deepFreeze(projection);

  const prepared = prepareSphericalScene(projection, {
    spatialPolicy: graphSpatialPolicyFor(projection),
  });

  assert.deepEqual(
    sortedIdentity(prepared.sceneGraph.nodes, "node_id"),
    sortedIdentity(projection.nodes, "node_id"),
  );
  assert.deepEqual(
    sortedIdentity(prepared.sceneGraph.links, "link_id"),
    sortedIdentity(projection.links, "link_id"),
  );
  assert.equal(prepared.sceneGraph.nodes.length - projection.nodes.length, 0);
  assert.equal(prepared.sceneGraph.links.length - projection.links.length, 0);
  assert.equal(new Set(prepared.sceneGraph.nodes.map((node) => node.node_id)).size, 93);
  assert.equal(new Set(prepared.sceneGraph.links.map((link) => link.link_id)).size, 128);
  assert.equal(JSON.stringify(projection), sourceBytes);
});

test("same projection and seed produce byte-stable settled anchors", async () => {
  const {
    prepareSphericalScene,
    settledNodePositionsHash,
  } = await loadSphericalLayout();
  const projection = deepFreeze(largeProjectionFixture());
  const options = Object.freeze({
    spatialPolicy: graphSpatialPolicyFor(projection),
  });

  const first = prepareSphericalScene(projection, options);
  const second = prepareSphericalScene(projection, options);

  assert.equal(
    normalizedAnchorBytes(first.settledAnchorMap),
    normalizedAnchorBytes(second.settledAnchorMap),
  );
  assert.match(first.settledNodePositionsHash, /^[0-9a-f]{16}$/);
  assert.equal(first.settledNodePositionsHash, second.settledNodePositionsHash);
  assert.equal(
    first.settledNodePositionsHash,
    settledNodePositionsHash(first.settledAnchorMap),
  );
});

test("settled position hash is order-independent and changes with canonical coordinates", async () => {
  const { settledNodePositionsHash } = await loadSphericalLayout();
  assert.equal(typeof settledNodePositionsHash, "function");
  const first = anchors([
    ["component:b", -4.125, 2, 9.75],
    ["component:a", 1.5, 0, -3.25],
  ]);
  const reordered = anchors([
    ["component:a", 1.5, 0, -3.25],
    ["component:b", -4.125, 2, 9.75],
  ]);
  const moved = anchors([
    ["component:a", 1.5, 0, -3.25],
    ["component:b", -4.125, 2, 9.751],
  ]);

  const fingerprint = settledNodePositionsHash(first);
  assert.match(fingerprint, /^[0-9a-f]{16}$/);
  assert.equal(settledNodePositionsHash(reordered), fingerprint);
  assert.notEqual(settledNodePositionsHash(moved), fingerprint);
  assert.throws(
    () => settledNodePositionsHash(anchors([["component:a", Number.NaN, 0, 0]])),
    /finite/,
  );
});

test("compact visual geometry preserves the legacy settled anchor bytes", async () => {
  const { prepareSphericalScene } = await loadSphericalLayout();
  const projection = deepFreeze({
    layout_seed: "layout-envelope-regression",
    nodes: [
      { node_type: "relation", node_id: "workflow:a", size_scale: 1.2, anchor_ordinal: 1 },
      { node_type: "component", node_id: "component:a" },
      { node_type: "component", node_id: "component:b" },
    ],
    links: [
      { link_id: "a", source_node_id: "workflow:a", target_node_id: "component:a" },
      { link_id: "b", source_node_id: "component:a", target_node_id: "component:b" },
    ],
  });

  const prepared = prepareSphericalScene(projection, {
    spatialPolicy: graphSpatialPolicyFor(projection),
  });
  assert.equal(normalizedAnchorBytes(prepared.settledAnchorMap), JSON.stringify([
    ["component:a", { x: -5.389205861255077, y: -12.02933895992329, z: 5.451201803505405 }],
    ["component:b", { x: 9.168636526995506, y: 3.7442930988248118, z: 10.418157542237891 }],
    ["workflow:a", { x: -3.9281886120377725, y: 8.18343788544576, z: -15.843963382942562 }],
  ]));
});

test("production-like 93-node volume passes SOT-65 density and visual-envelope collision gates", async () => {
  const { prepareSphericalScene } = await loadSphericalLayout();
  const projection = deepFreeze(largeProjectionFixture());
  const prepared = prepareSphericalScene(projection, {
    spatialPolicy: graphSpatialPolicyFor(projection),
  });
  const metrics = prepared.layoutMetrics;
  const overlaps = visualEnvelopeOverlapReport(prepared.sceneGraph.nodes);

  assert.ok(metric(metrics, "depthToLongest") >= 0.35, JSON.stringify(metrics));
  assert.ok(metric(metrics, "shortestToLongest") >= 0.5, JSON.stringify(metrics));
  assert.ok(metric(metrics, "p90ToMedian") <= 2.2, JSON.stringify(metrics));
  assert.ok(metric(metrics, "maximumToP95") <= 1.35, JSON.stringify(metrics));
  assert.ok(metric(metrics, "innerDensity") >= 0.35, JSON.stringify(metrics));
  assert.deepEqual(overlaps, { overlapCount: 0, maximumOverlap: 0 });
});

test("dense 3D volume satisfies the approved layout-only ratios", async () => {
  const { createSphericalLayoutMetrics } = await loadSphericalLayout();
  assert.equal(typeof createSphericalLayoutMetrics, "function");
  const report = createSphericalLayoutMetrics(acceptedDenseVolume());

  assert.ok(metric(report, "depthToLongest") >= 0.35);
  assert.ok(metric(report, "shortestToLongest") >= 0.5);
  assert.ok(metric(report, "p90ToMedian") <= 2.2);
  assert.ok(metric(report, "maximumToP95") <= 1.35);
  assert.ok(metric(report, "innerDensity") >= 0.35);
  assert.equal("projectedShortAxisOccupancy" in report, false);
  assert.equal("cropCount" in report, false);
});

test("layout metrics independently reject a plane, column, hollow shell, and dominant outlier", async () => {
  const { createSphericalLayoutMetrics } = await loadSphericalLayout();
  const plane = createSphericalLayoutMetrics(anchors([
    ["p1", -10, -10, 0], ["p2", 10, -10, 0],
    ["p3", -10, 10, 0], ["p4", 10, 10, 0],
    ["p5", -2, 0, 0], ["p6", 2, 0, 0],
  ]));
  const column = createSphericalLayoutMetrics(anchors([
    ["c1", 0, 0, -10], ["c2", 0, 0, -6], ["c3", 0, 0, -2],
    ["c4", 0, 0, 2], ["c5", 0, 0, 6], ["c6", 0, 0, 10],
  ]));
  const shell = createSphericalLayoutMetrics(anchors([
    ["sx+", 10, 0, 0], ["sx-", -10, 0, 0],
    ["sy+", 0, 10, 0], ["sy-", 0, -10, 0],
    ["sz+", 0, 0, 10], ["sz-", 0, 0, -10],
  ]));
  const outlierPoints = Array.from({ length: 19 }, (_, index) => {
    const axis = index % 3;
    const sign = index % 2 === 0 ? 1 : -1;
    const point = [0, 0, 0];
    point[axis] = sign * 10;
    return [`o${index}`, ...point];
  });
  outlierPoints.push(["dominant-outlier", 100, 0, 0]);
  const outlier = createSphericalLayoutMetrics(anchors(outlierPoints));

  assert.ok(metric(plane, "depthToLongest") < 0.35);
  assert.ok(metric(column, "shortestToLongest") < 0.5);
  assert.ok(metric(shell, "innerDensity") < 0.35);
  assert.ok(metric(outlier, "maximumToP95") > 1.35);
});
