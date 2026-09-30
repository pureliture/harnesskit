import assert from "node:assert/strict";
import test from "node:test";

import {
  assertGraphSpatialPolicyProjection,
  createGraphSpatialPolicy,
  graphProjectionIdentity,
  graphSpatialPolicyFor,
} from "../graph/spatial-policy.js";
import { prepareSphericalScene } from "../graph/spherical-layout.js";
import {
  createSceneFitReport,
  createThreeGraphRenderer,
} from "../graph/component-map.js";
import { createGraphNodeObjectFactory } from "../graph/node-object.js";
import {
  graphNodeLayoutEnvelope,
  graphNodeVisualEnvelope,
  normalizeRelationSizeScale,
} from "../graph/visual-metrics.js";

const projection = Object.freeze({
  projection_id: "projection-policy-fixture",
  snapshot_id: "snapshot-policy-fixture",
  layout_seed: "policy-fixture-seed",
  nodes: Object.freeze([
    Object.freeze({ node_type: "relation", node_id: "workflow:review", size_scale: 1 }),
    Object.freeze({ node_type: "component", node_id: "component:reviewer" }),
  ]),
  links: Object.freeze([
    Object.freeze({
      link_id: "workflow-step:review:reviewer",
      source_node_id: "workflow:review",
      target_node_id: "component:reviewer",
    }),
  ]),
});

test("production DTO derives one graph projection identity from snapshot and layout seed", () => {
  const productionProjection = {
    snapshot_id: "snapshot-production",
    layout_seed: "layout-production",
    nodes: [],
    links: [],
  };

  assert.equal(
    graphProjectionIdentity(productionProjection),
    "graph:snapshot-production:layout-production",
  );
  assert.equal(
    createGraphSpatialPolicy(productionProjection).identity.projectionId,
    "graph:snapshot-production:layout-production",
  );
  assert.equal(graphProjectionIdentity({ snapshot_id: "snapshot-only" }), "");
});

test("spatial policy is a frozen deterministic primitive value with no synthetic identity", () => {
  const first = createGraphSpatialPolicy(projection);
  const second = createGraphSpatialPolicy(projection);

  assert.deepEqual(first, second);
  assert.ok(Object.isFrozen(first));
  assert.ok(Object.isFrozen(first.settle));
  assert.ok(Object.isFrozen(first.layout));
  assert.ok(Object.isFrozen(first.visualGeometryScale));
  assert.equal(first.identity.nodeCount, 2);
  assert.equal(first.identity.linkCount, 1);
  assert.equal(first.identity.syntheticNodeCount, 0);
  assert.equal(first.identity.syntheticLinkCount, 0);
  assert.equal(graphSpatialPolicyFor(projection), graphSpatialPolicyFor(projection));
});

test("prepared spherical scene preserves the caller policy identity for layout consumers", () => {
  const spatialPolicy = createGraphSpatialPolicy(projection);
  const prepared = prepareSphericalScene(projection, { spatialPolicy });

  assert.equal(prepared.spatialPolicy, spatialPolicy);
  assert.equal(prepared.sceneGraph.nodes.length, 2);
  assert.equal(prepared.sceneGraph.links.length, 1);
});

test("layout consumers cannot override the projection spatial policy", () => {
  const spatialPolicy = createGraphSpatialPolicy(projection);
  const baseline = prepareSphericalScene(projection, { spatialPolicy });
  const attemptedOverride = prepareSphericalScene(projection, {
    constants: { sphereRadius: 1 },
    layoutSeed: "consumer-owned-seed",
    settleTicks: 1,
    spatialPolicy,
  });

  assert.deepEqual(
    [...attemptedOverride.settledAnchorMap.entries()],
    [...baseline.settledAnchorMap.entries()],
  );
});

test("projection policy is required and owns every force and geometry primitive", () => {
  assert.throws(
    () => graphSpatialPolicyFor(null),
    /graph projection is required/,
  );

  const spatialPolicy = graphSpatialPolicyFor(projection);
  assert.deepEqual(spatialPolicy.force, {
    alphaDecay: 0.035,
    chargeDistanceMaximum: 160,
    chargeStrength: -28,
    componentCrossLinkDistance: 34,
    relationLinkDistance: 42,
    linkStrength: 0.28,
  });
  assert.equal(spatialPolicy.visualGeometryScale.componentBodyRadius, 3);
  assert.equal(spatialPolicy.visualGeometryScale.focusRimScale, 1.16);
  assert.equal(spatialPolicy.visualGeometryScale.focusPresentationScale, 1.06);
  assert.equal("selectionHaloScale" in spatialPolicy.visualGeometryScale, false);
  assert.equal("selectionHaloContrastScale" in spatialPolicy.visualGeometryScale, false);
  assert.equal(spatialPolicy.layout.collisionEnvelopeScale, 1.34);
  assert.equal("selectionHaloContrastScale" in spatialPolicy.layout, false);
  assert.equal(spatialPolicy.layout.componentRadius, 4);
  assert.equal(spatialPolicy.collision.gap, 2);
  assert.ok(Number.isInteger(spatialPolicy.settle.ticks));
  assert.ok(spatialPolicy.settle.ticks > 0);
  assert.throws(
    () => assertGraphSpatialPolicyProjection(spatialPolicy, {
      ...projection,
      projection_id: "projection-other",
    }),
    /spatialPolicy identity differs.*projectionId/,
  );
  assert.throws(() => prepareSphericalScene(projection), /spatialPolicy is required/);
  assert.throws(() => normalizeRelationSizeScale(1.1), /spatialPolicy is required/);
  assert.throws(() => graphNodeVisualEnvelope(projection.nodes[0]), /spatialPolicy is required/);
  assert.throws(() => graphNodeLayoutEnvelope(projection.nodes[0]), /spatialPolicy is required/);
  assert.throws(() => createGraphNodeObjectFactory(), /spatialPolicy is required/);
  assert.throws(
    () => createSceneFitReport(projection, null),
    /spatialPolicy is required/,
  );
  assert.throws(
    () => createThreeGraphRenderer({
      documentObject: {
        createElement() {
          return { setAttribute() {} };
        },
      },
    }),
    /spatialPolicy is required/,
  );
});
