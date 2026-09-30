export const GRAPH_SPATIAL_DEFAULTS = Object.freeze({
  collision: Object.freeze({ gap: 2, strength: 0.62 }),
  force: Object.freeze({
    alphaDecay: 0.035,
    chargeDistanceMaximum: 160,
    chargeStrength: -28,
    componentCrossLinkDistance: 34,
    relationLinkDistance: 42,
    linkStrength: 0.28,
  }),
  layout: Object.freeze({
    componentRadius: 4,
    collisionEnvelopeScale: 1.34,
    relationRadius: 14,
    relationMaximumScale: 1.2,
  }),
  settle: Object.freeze({
    centerStrength: 0.006,
    damping: 0.82,
    linkDistanceRatio: 0.34,
    linkStrength: 0.018,
    maximumVelocityRatio: 0.045,
    relationRadiusRatio: 0.58,
    sphereRadius: 72,
    ticks: 180,
  }),
  visualGeometryScale: Object.freeze({
    componentBodyRadius: 3,
    focusPresentationScale: 1.06,
    focusRimScale: 1.16,
    hitTargetScale: 1.35,
    outlineShellScale: 1.08,
    relationBodyRadius: 7.5,
    relationMaximumScale: 1.15,
  }),
});

const policyByProjection = new WeakMap();

export function graphProjectionIdentity(projection) {
  const explicit = String(projection?.projection_id ?? "").trim();
  if (explicit) return explicit;
  const snapshotId = String(projection?.snapshot_id ?? "").trim();
  const layoutSeed = String(projection?.layout_seed ?? "").trim();
  return snapshotId && layoutSeed ? `graph:${snapshotId}:${layoutSeed}` : "";
}

function projectionIdentity(projection) {
  const nodes = Array.isArray(projection?.nodes) ? projection.nodes : [];
  const links = Array.isArray(projection?.links) ? projection.links : [];
  return Object.freeze({
    linkCount: links.length,
    nodeCount: nodes.length,
    projectionId: graphProjectionIdentity(projection),
    snapshotId: String(projection?.snapshot_id ?? ""),
    syntheticLinkCount: 0,
    syntheticNodeCount: 0,
  });
}

/**
 * Creates the I/O-free spatial authority for one immutable graph projection.
 * Consumer-specific geometry is derived from this value rather than owning
 * independent density constants.
 */
export function createGraphSpatialPolicy(projection = null) {
  if (!projection || typeof projection !== "object") {
    throw new TypeError("graph projection is required");
  }
  return Object.freeze({
    collision: GRAPH_SPATIAL_DEFAULTS.collision,
    force: GRAPH_SPATIAL_DEFAULTS.force,
    identity: projectionIdentity(projection),
    layout: GRAPH_SPATIAL_DEFAULTS.layout,
    seed: String(projection?.layout_seed ?? ""),
    settle: GRAPH_SPATIAL_DEFAULTS.settle,
    visualGeometryScale: GRAPH_SPATIAL_DEFAULTS.visualGeometryScale,
  });
}

/**
 * Keeps one policy object identity for consumers sharing one projection object.
 */
export function graphSpatialPolicyFor(projection = null) {
  if (!projection || typeof projection !== "object") {
    throw new TypeError("graph projection is required");
  }
  const existing = policyByProjection.get(projection);
  if (existing) return existing;
  const created = createGraphSpatialPolicy(projection);
  policyByProjection.set(projection, created);
  return created;
}

export function requireGraphSpatialPolicy(spatialPolicy) {
  if (!spatialPolicy || typeof spatialPolicy !== "object") {
    throw new TypeError("spatialPolicy is required");
  }
  for (const key of ["collision", "force", "identity", "layout", "settle", "visualGeometryScale"]) {
    if (!spatialPolicy[key] || typeof spatialPolicy[key] !== "object") {
      throw new TypeError(`spatialPolicy.${key} is required`);
    }
  }
  return spatialPolicy;
}

export function assertGraphSpatialPolicyProjection(spatialPolicy, projection) {
  const policy = requireGraphSpatialPolicy(spatialPolicy);
  if (!projection || typeof projection !== "object") {
    throw new TypeError("graph projection is required");
  }
  const expected = projectionIdentity(projection);
  for (const key of ["projectionId", "snapshotId", "nodeCount", "linkCount"]) {
    if (policy.identity[key] !== expected[key]) {
      throw new TypeError(`spatialPolicy identity differs from graph projection: ${key}`);
    }
  }
  return policy;
}
