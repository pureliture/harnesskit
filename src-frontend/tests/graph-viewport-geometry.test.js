import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";

async function loadViewportGeometry() {
  try {
    return await import("../graph/viewport-geometry.js");
  } catch (error) {
    if (error?.code === "ERR_MODULE_NOT_FOUND") {
      assert.fail("approved viewport-geometry public seam is not implemented");
    }
    throw error;
  }
}

function orthographicCamera(horizontalHalfSpan, verticalHalfSpan) {
  const camera = new THREE.OrthographicCamera(
    -horizontalHalfSpan,
    horizontalHalfSpan,
    verticalHalfSpan,
    -verticalHalfSpan,
    0.1,
    100,
  );
  camera.position.set(0, 0, 10);
  camera.lookAt(0, 0, 0);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  return camera;
}

function anchorFixture() {
  return new Map([
    ["north-west", { x: -40, y: 24, z: 0 }],
    ["north-east", { x: 40, y: 24, z: 0 }],
    ["south-west", { x: -40, y: -24, z: 0 }],
    ["south-east", { x: 40, y: -24, z: 0 }],
  ]);
}

function envelopeFixture() {
  return new Map([...anchorFixture().keys()].map((nodeId) => [nodeId, {
    bodyRadius: 0,
    hitRadius: 0,
    fitRadius: 0,
  }]));
}

function occupancyRatio(report) {
  const raw = Number(
    report?.projectedShortAxisOccupancy
      ?? report?.projected_short_axis_occupancy,
  );
  assert.ok(Number.isFinite(raw), "projected short-axis occupancy is required");
  return raw > 1 ? raw / 100 : raw;
}

function cropCount(report) {
  const value = Number(report?.cropCount ?? report?.crop_count);
  assert.ok(Number.isInteger(value), "crop count is required");
  return value;
}

test("HUD geometry produces one revision-bound immutable safe inset snapshot", async () => {
  const { createGraphSafeInset } = await loadViewportGeometry();
  assert.equal(typeof createGraphSafeInset, "function");

  const inset = createGraphSafeInset({
    geometryRevision: 7,
    viewportRect: { x: 0, y: 0, width: 1000, height: 600 },
    hudRect: { x: 20, y: 20, width: 300, height: 80 },
    relationLabelMargin: 12,
  });

  assert.deepEqual({
    geometryRevision: inset.geometryRevision,
    top: inset.top,
    right: inset.right,
    bottom: inset.bottom,
    left: inset.left,
  }, {
    geometryRevision: 7,
    top: 112,
    right: 12,
    bottom: 12,
    left: 12,
  });
  assert.ok(Object.isFrozen(inset));
});

test("Fit report uses projected short-axis occupancy and reports zero crop inside the safe viewport", async () => {
  const { createSphericalSceneFitReport } = await loadViewportGeometry();
  assert.equal(typeof createSphericalSceneFitReport, "function");

  const report = createSphericalSceneFitReport({
    settledAnchorMap: anchorFixture(),
    nodeEnvelopes: envelopeFixture(),
    camera: orthographicCamera(50, 30),
    viewportRect: { x: 0, y: 0, width: 1000, height: 600 },
    safeInset: Object.freeze({ geometryRevision: 1, top: 0, right: 0, bottom: 0, left: 0 }),
  });

  assert.ok(Math.abs(occupancyRatio(report) - 0.8) < 1e-9);
  assert.equal(cropCount(report), 0);
  assert.equal("depthToLongest" in report, false);
  assert.equal("innerDensity" in report, false);
});

test("camera framing changes Fit occupancy without mutating settled anchors", async () => {
  const { createSphericalSceneFitReport } = await loadViewportGeometry();
  const anchors = anchorFixture();
  const anchorsBefore = structuredClone([...anchors]);
  const closeFit = createSphericalSceneFitReport({
    settledAnchorMap: anchors,
    nodeEnvelopes: envelopeFixture(),
    camera: orthographicCamera(50, 30),
    viewportRect: { x: 0, y: 0, width: 1000, height: 600 },
    safeInset: Object.freeze({ geometryRevision: 2, top: 0, right: 0, bottom: 0, left: 0 }),
  });
  const farFit = createSphericalSceneFitReport({
    settledAnchorMap: anchors,
    nodeEnvelopes: envelopeFixture(),
    camera: orthographicCamera(100, 60),
    viewportRect: { x: 0, y: 0, width: 1000, height: 600 },
    safeInset: Object.freeze({ geometryRevision: 3, top: 0, right: 0, bottom: 0, left: 0 }),
  });

  assert.deepEqual([...anchors], anchorsBefore);
  assert.ok(Math.abs(occupancyRatio(closeFit) - 0.8) < 1e-9);
  assert.ok(Math.abs(occupancyRatio(farFit) - 0.4) < 1e-9);
});

test("node extent entering the HUD safe inset is reported as crop", async () => {
  const { createSphericalSceneFitReport } = await loadViewportGeometry();
  const anchors = new Map([["under-hud", { x: 0, y: 29, z: 0 }]]);
  const report = createSphericalSceneFitReport({
    settledAnchorMap: anchors,
    nodeEnvelopes: new Map([["under-hud", {
      bodyRadius: 2,
      hitRadius: 2,
      fitRadius: 2,
    }]]),
    camera: orthographicCamera(50, 30),
    viewportRect: { x: 0, y: 0, width: 1000, height: 600 },
    safeInset: Object.freeze({ geometryRevision: 4, top: 112, right: 12, bottom: 12, left: 12 }),
  });

  assert.ok(cropCount(report) > 0);
});
