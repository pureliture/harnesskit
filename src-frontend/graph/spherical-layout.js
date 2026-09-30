import { graphNodeLayoutEnvelope } from "./visual-metrics.js";
import { requireGraphSpatialPolicy } from "./spatial-policy.js";

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
const SETTLED_ANCHOR_AXES = Object.freeze(["x", "y", "z"]);

function hash32(value) {
  let hash = 0x811c9dc5;
  for (const character of String(value)) {
    hash ^= character.codePointAt(0);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

function seededUnit(value) {
  let state = hash32(value) || 0x6d2b79f5;
  state ^= state << 13;
  state ^= state >>> 17;
  state ^= state << 5;
  return (state >>> 0) / 0x100000000;
}

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function nodeIdentity(node) {
  return typeof node?.node_id === "string" && node.node_id.length > 0
    ? node.node_id
    : null;
}

function linkIdentity(link) {
  return typeof link?.link_id === "string" && link.link_id.length > 0
    ? link.link_id
    : null;
}

function exactUniqueIdentities(values, readIdentity, label) {
  const identities = values.map(readIdentity);
  if (identities.some((identity) => identity === null)) {
    throw new TypeError(`${label} identity is required`);
  }
  if (new Set(identities).size !== identities.length) {
    throw new TypeError(`${label} identities must be unique`);
  }
  return identities;
}

function cloneProjection(projection) {
  return JSON.parse(JSON.stringify(projection));
}

function relationSeed(node, index, count, layoutSeed, radius) {
  const stableOrdinal = Math.max(0, finiteNumber(node?.anchor_ordinal, index + 1) - 1);
  const phase = seededUnit(`${layoutSeed}|relation-phase`) * Math.PI * 2;
  const yUnit = count <= 1 ? 0 : 1 - (2 * (stableOrdinal + 0.5)) / count;
  const ring = Math.sqrt(Math.max(0, 1 - yUnit * yUnit));
  const angle = phase + stableOrdinal * GOLDEN_ANGLE;
  return {
    x: Math.cos(angle) * ring * radius,
    y: yUnit * radius,
    z: Math.sin(angle) * ring * radius,
  };
}

function componentSeed(node, layoutSeed, radius) {
  const identity = nodeIdentity(node);
  const azimuth = seededUnit(`${layoutSeed}|${identity}|azimuth`) * Math.PI * 2;
  const cosine = 1 - 2 * seededUnit(`${layoutSeed}|${identity}|cosine`);
  const radial = radius * Math.cbrt(seededUnit(`${layoutSeed}|${identity}|radius`));
  const ring = Math.sqrt(Math.max(0, 1 - cosine * cosine));
  return {
    x: Math.cos(azimuth) * ring * radial,
    y: cosine * radial,
    z: Math.sin(azimuth) * ring * radial,
  };
}

function visualRadius(node, spatialPolicy) {
  const envelope = graphNodeLayoutEnvelope(node, spatialPolicy);
  return Math.max(0, (envelope.max.x - envelope.min.x) / 2);
}

function clampVelocity(value, maximum) {
  return Math.max(-maximum, Math.min(maximum, value));
}

function settle(nodes, links, spatialPolicy) {
  const { collision, settle: settlePolicy } = spatialPolicy;
  const state = new Map(nodes.map((node) => [node.node_id, {
    node,
    visualRadius: visualRadius(node, spatialPolicy),
    vx: 0,
    vy: 0,
    vz: 0,
  }]));
  const linkDistance = settlePolicy.sphereRadius * settlePolicy.linkDistanceRatio;
  const maximumVelocity = settlePolicy.sphereRadius * settlePolicy.maximumVelocityRatio;

  for (let tick = 0; tick < settlePolicy.ticks; tick += 1) {
    for (const item of state.values()) {
      item.vx -= item.node.x * settlePolicy.centerStrength;
      item.vy -= item.node.y * settlePolicy.centerStrength;
      item.vz -= item.node.z * settlePolicy.centerStrength;
    }

    for (const link of links) {
      const source = state.get(link.source_node_id);
      const target = state.get(link.target_node_id);
      if (!source || !target || source === target) continue;
      let dx = target.node.x - source.node.x;
      let dy = target.node.y - source.node.y;
      let dz = target.node.z - source.node.z;
      let distance = Math.hypot(dx, dy, dz);
      if (distance < 1e-9) {
        const phase = seededUnit(link.link_id) * Math.PI * 2;
        dx = Math.cos(phase) * 1e-3;
        dy = Math.sin(phase) * 1e-3;
        dz = 1e-3;
        distance = Math.hypot(dx, dy, dz);
      }
      const impulse = (distance - linkDistance) * settlePolicy.linkStrength / distance;
      const ix = dx * impulse;
      const iy = dy * impulse;
      const iz = dz * impulse;
      source.vx += ix;
      source.vy += iy;
      source.vz += iz;
      target.vx -= ix;
      target.vy -= iy;
      target.vz -= iz;
    }

    for (let leftIndex = 0; leftIndex < nodes.length; leftIndex += 1) {
      const left = state.get(nodes[leftIndex].node_id);
      for (let rightIndex = leftIndex + 1; rightIndex < nodes.length; rightIndex += 1) {
        const right = state.get(nodes[rightIndex].node_id);
        let dx = right.node.x - left.node.x;
        let dy = right.node.y - left.node.y;
        let dz = right.node.z - left.node.z;
        let distance = Math.hypot(dx, dy, dz);
        if (distance < 1e-9) {
          const phase = seededUnit(`${left.node.node_id}|${right.node.node_id}`) * Math.PI * 2;
          dx = Math.cos(phase) * 1e-3;
          dy = Math.sin(phase) * 1e-3;
          dz = 1e-3;
          distance = Math.hypot(dx, dy, dz);
        }
        const minimum = left.visualRadius
          + right.visualRadius
          + collision.gap;
        if (distance >= minimum) continue;
        const impulse = (minimum - distance) * collision.strength / distance;
        const ix = dx * impulse * 0.5;
        const iy = dy * impulse * 0.5;
        const iz = dz * impulse * 0.5;
        left.vx -= ix;
        left.vy -= iy;
        left.vz -= iz;
        right.vx += ix;
        right.vy += iy;
        right.vz += iz;
      }
    }

    for (const item of state.values()) {
      item.vx = clampVelocity(item.vx * settlePolicy.damping, maximumVelocity);
      item.vy = clampVelocity(item.vy * settlePolicy.damping, maximumVelocity);
      item.vz = clampVelocity(item.vz * settlePolicy.damping, maximumVelocity);
      item.node.x += item.vx;
      item.node.y += item.vy;
      item.node.z += item.vz;

      const radius = Math.hypot(item.node.x, item.node.y, item.node.z);
      const maximumRadius = Math.max(0, settlePolicy.sphereRadius - item.visualRadius);
      if (radius > maximumRadius && radius > 0) {
        const scale = maximumRadius / radius;
        item.node.x *= scale;
        item.node.y *= scale;
        item.node.z *= scale;
        item.vx *= 0.35;
        item.vy *= 0.35;
        item.vz *= 0.35;
      }
    }
  }
}

function immutableAnchorMap(nodes) {
  const anchors = new Map(nodes.map((node) => [node.node_id, Object.freeze({
    x: finiteNumber(node.x),
    y: finiteNumber(node.y),
    z: finiteNumber(node.z),
  })]));
  Object.defineProperties(anchors, {
    set: { value: () => { throw new TypeError("settled anchors are immutable"); } },
    delete: { value: () => { throw new TypeError("settled anchors are immutable"); } },
    clear: { value: () => { throw new TypeError("settled anchors are immutable"); } },
  });
  return Object.freeze(anchors);
}

function settledAnchorEntries(input) {
  if (input instanceof Map) return [...input.entries()];
  if (Array.isArray(input)) {
    return input.map((point) => [point?.node_id, point]);
  }
  return Object.entries(input ?? {});
}

function canonicalSettledAnchorEntries(input) {
  const entries = settledAnchorEntries(input);
  return entries.map(([nodeId, point]) => {
    const identity = String(nodeId ?? "");
    if (!identity) throw new TypeError("settled anchor identity is required");
    const coordinates = SETTLED_ANCHOR_AXES.map((axis) => {
      const coordinate = Number(point?.[axis]);
      if (!Number.isFinite(coordinate)) {
        throw new TypeError(`settled anchor ${identity}.${axis} must be finite`);
      }
      return coordinate.toFixed(9);
    });
    return [identity, ...coordinates];
  }).sort(([left], [right]) => left.localeCompare(right));
}

export function settledNodePositionsHash(settledAnchorMap) {
  const canonical = JSON.stringify(canonicalSettledAnchorEntries(settledAnchorMap));
  let hash = 0xcbf29ce484222325n;
  for (let index = 0; index < canonical.length; index += 1) {
    hash ^= BigInt(canonical.charCodeAt(index));
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return hash.toString(16).padStart(16, "0");
}

function anchorValues(input) {
  if (input instanceof Map) return [...input.values()];
  if (Array.isArray(input)) return input;
  if (input && typeof input === "object") return Object.values(input);
  return [];
}

function quantile(sorted, percentile) {
  if (sorted.length === 0) return 0;
  if (sorted.length === 1) return sorted[0];
  const index = (sorted.length - 1) * percentile;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  const fraction = index - lower;
  return sorted[lower] + (sorted[upper] - sorted[lower]) * fraction;
}

export function createSphericalLayoutMetrics(settledAnchorMap) {
  const points = anchorValues(settledAnchorMap).map((point) => ({
    x: finiteNumber(point?.x),
    y: finiteNumber(point?.y),
    z: finiteNumber(point?.z),
  }));
  if (points.length === 0) {
    return Object.freeze({
      nodeCount: 0,
      depthToLongest: 0,
      shortestToLongest: 0,
      p90ToMedian: Infinity,
      maximumToP95: Infinity,
      innerDensity: 0,
    });
  }
  const centroid = points.reduce((sum, point) => ({
    x: sum.x + point.x / points.length,
    y: sum.y + point.y / points.length,
    z: sum.z + point.z / points.length,
  }), { x: 0, y: 0, z: 0 });
  const spans = ["x", "y", "z"].map((axis) => {
    const values = points.map((point) => point[axis]);
    return Math.max(...values) - Math.min(...values);
  });
  const longest = Math.max(...spans);
  const shortest = Math.min(...spans);
  const radii = points.map((point) => Math.hypot(
    point.x - centroid.x,
    point.y - centroid.y,
    point.z - centroid.z,
  )).sort((left, right) => left - right);
  const median = quantile(radii, 0.5);
  const p90 = quantile(radii, 0.9);
  const p95 = quantile(radii, 0.95);
  const maximum = radii.at(-1) ?? 0;
  const innerLimit = p95 * 0.55;
  const ratios = Object.freeze({
    depthToLongest: longest > 0 ? spans[2] / longest : 0,
    shortestToLongest: longest > 0 ? shortest / longest : 0,
    p90ToMedian: median > 0 ? p90 / median : p90 === 0 ? 1 : Infinity,
    maximumToP95: p95 > 0 ? maximum / p95 : maximum === 0 ? 1 : Infinity,
    innerDensity: radii.filter((radius) => radius <= innerLimit).length / radii.length,
  });
  return Object.freeze({
    nodeCount: points.length,
    centroid: Object.freeze(centroid),
    spans: Object.freeze({ x: spans[0], y: spans[1], z: spans[2] }),
    radius: Object.freeze({ median, p90, p95, maximum }),
    ratios,
    ...ratios,
  });
}

export function prepareSphericalScene(
  frozenProjection,
  { spatialPolicy } = {},
) {
  if (!frozenProjection || typeof frozenProjection !== "object") {
    throw new TypeError("graph projection is required");
  }
  const sourceNodes = Array.isArray(frozenProjection.nodes) ? frozenProjection.nodes : [];
  const sourceLinks = Array.isArray(frozenProjection.links) ? frozenProjection.links : [];
  const sourceNodeIds = exactUniqueIdentities(sourceNodes, nodeIdentity, "graph node");
  const sourceLinkIds = exactUniqueIdentities(sourceLinks, linkIdentity, "graph link");
  const sceneGraph = cloneProjection(frozenProjection);
  sceneGraph.nodes.sort((left, right) => left.node_id.localeCompare(right.node_id));
  sceneGraph.links.sort((left, right) => left.link_id.localeCompare(right.link_id));
  const nodeIdSet = new Set(sourceNodeIds);
  for (const link of sceneGraph.links) {
    if (!nodeIdSet.has(link.source_node_id) || !nodeIdSet.has(link.target_node_id)) {
      throw new TypeError(`graph link has an unresolved endpoint: ${link.link_id}`);
    }
    link.source = link.source_node_id;
    link.target = link.target_node_id;
  }
  if (new Set(sceneGraph.nodes.map(nodeIdentity)).size !== sourceNodeIds.length
    || new Set(sceneGraph.links.map(linkIdentity)).size !== sourceLinkIds.length) {
    throw new TypeError("renderer scene identity differs from the frozen projection");
  }

  const policy = requireGraphSpatialPolicy(spatialPolicy);
  const relations = sceneGraph.nodes.filter((node) => node.node_type === "relation");
  const relationIndex = new Map(relations.map((node, index) => [node.node_id, index]));
  for (const node of sceneGraph.nodes) {
    const seed = node.node_type === "relation"
      ? relationSeed(
        node,
        relationIndex.get(node.node_id),
        relations.length,
        policy.seed,
        policy.settle.sphereRadius * policy.settle.relationRadiusRatio,
      )
      : componentSeed(node, policy.seed, policy.settle.sphereRadius);
    Object.assign(node, seed);
    delete node.fx;
    delete node.fy;
    delete node.fz;
  }
  settle(sceneGraph.nodes, sceneGraph.links, policy);
  const settledAnchorMap = immutableAnchorMap(sceneGraph.nodes);
  const settledPositionsHash = settledNodePositionsHash(settledAnchorMap);
  for (const node of sceneGraph.nodes) {
    const anchor = settledAnchorMap.get(node.node_id);
    node.x = anchor.x;
    node.y = anchor.y;
    node.z = anchor.z;
    node.fx = anchor.x;
    node.fy = anchor.y;
    node.fz = anchor.z;
  }
  return Object.freeze({
    sceneGraph,
    spatialPolicy: policy,
    settledAnchorMap,
    settledNodePositionsHash: settledPositionsHash,
    layoutMetrics: createSphericalLayoutMetrics(settledAnchorMap),
  });
}
