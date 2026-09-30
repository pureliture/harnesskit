import {
  GRAPH_SPATIAL_DEFAULTS,
  requireGraphSpatialPolicy,
} from "./spatial-policy.js";

const COMPONENT_GEOMETRY = Object.freeze({
  radius: GRAPH_SPATIAL_DEFAULTS.visualGeometryScale.componentBodyRadius,
  widthSegments: 24,
  heightSegments: 16,
});

const RELATION_GEOMETRY = Object.freeze({
  radius: GRAPH_SPATIAL_DEFAULTS.visualGeometryScale.relationBodyRadius,
  widthSegments: 32,
  heightSegments: 20,
});

export const GRAPH_NODE_VISUAL_METRICS = Object.freeze({
  componentGeometry: COMPONENT_GEOMETRY,
  relationGeometry: RELATION_GEOMETRY,
  relationMaximumScale: GRAPH_SPATIAL_DEFAULTS.visualGeometryScale.relationMaximumScale,
  outlineShellScale: GRAPH_SPATIAL_DEFAULTS.visualGeometryScale.outlineShellScale,
  focusRimScale: GRAPH_SPATIAL_DEFAULTS.visualGeometryScale.focusRimScale,
  focusPresentationScale: GRAPH_SPATIAL_DEFAULTS.visualGeometryScale.focusPresentationScale,
  hitTargetScale: GRAPH_SPATIAL_DEFAULTS.visualGeometryScale.hitTargetScale,
});

const BACKEND_RELATION_SIZE_SCALE = Object.freeze({ minimum: 1, maximum: 1.2 });

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, Number(value) || minimum));
}

export function normalizeRelationSizeScale(value, spatialPolicy) {
  const policy = requireGraphSpatialPolicy(spatialPolicy);
  const backendScale = clamp(
    value,
    BACKEND_RELATION_SIZE_SCALE.minimum,
    BACKEND_RELATION_SIZE_SCALE.maximum,
  );
  const backendRange = BACKEND_RELATION_SIZE_SCALE.maximum - BACKEND_RELATION_SIZE_SCALE.minimum;
  const visualRange = policy.visualGeometryScale.relationMaximumScale - 1;
  return 1 + ((backendScale - 1) / backendRange) * visualRange;
}

export function graphNodeVisualEnvelope(node, spatialPolicy) {
  const policy = requireGraphSpatialPolicy(spatialPolicy);
  const relation = node?.node_type === "relation";
  const componentRadius = policy.visualGeometryScale.componentBodyRadius;
  const relationRadius = policy.visualGeometryScale.relationBodyRadius;
  const outlineShellScale = policy.visualGeometryScale.outlineShellScale;
  const focusEnvelopeScale = policy.visualGeometryScale.focusRimScale
    * policy.visualGeometryScale.focusPresentationScale;
  const groupScale = relation ? normalizeRelationSizeScale(node?.size_scale, policy) : 1;
  const envelopeScale = groupScale * Math.max(outlineShellScale, focusEnvelopeScale);
  const x = Number(node?.x) || 0;
  const y = Number(node?.y) || 0;
  const z = Number(node?.z) || 0;
  const radius = (relation ? relationRadius : componentRadius) * envelopeScale;
  return Object.freeze({
    nodeId: String(node?.node_id ?? ""),
    min: Object.freeze({ x: x - radius, y: y - radius, z: z - radius }),
    max: Object.freeze({ x: x + radius, y: y + radius, z: z + radius }),
  });
}

export function graphNodeLayoutEnvelope(node, spatialPolicy) {
  const policy = requireGraphSpatialPolicy(spatialPolicy);
  const relation = node?.node_type === "relation";
  const radius = relation
    ? policy.layout.relationRadius
    : policy.layout.componentRadius;
  const groupScale = relation
    ? clamp(
      node?.size_scale,
      1,
      policy.layout.relationMaximumScale,
    )
    : 1;
  const envelopeRadius = radius
    * groupScale
    * policy.layout.collisionEnvelopeScale;
  const x = Number(node?.x) || 0;
  const y = Number(node?.y) || 0;
  const z = Number(node?.z) || 0;
  return Object.freeze({
    nodeId: String(node?.node_id ?? ""),
    min: Object.freeze({ x: x - envelopeRadius, y: y - envelopeRadius, z: z - envelopeRadius }),
    max: Object.freeze({ x: x + envelopeRadius, y: y + envelopeRadius, z: z + envelopeRadius }),
  });
}
