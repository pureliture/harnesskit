import { Vector3 } from "three";

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function nonNegative(value) {
  return Math.max(0, finiteNumber(value));
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function normalizedRect(rect) {
  return Object.freeze({
    x: finiteNumber(rect?.x),
    y: finiteNumber(rect?.y),
    width: nonNegative(rect?.width),
    height: nonNegative(rect?.height),
  });
}

function rectEdges(rect) {
  return {
    top: rect.y,
    right: rect.x + rect.width,
    bottom: rect.y + rect.height,
    left: rect.x,
  };
}

function overlaps(left, right) {
  const leftEdges = rectEdges(left);
  const rightEdges = rectEdges(right);
  return leftEdges.left < rightEdges.right
    && leftEdges.right > rightEdges.left
    && leftEdges.top < rightEdges.bottom
    && leftEdges.bottom > rightEdges.top;
}

function nearestViewportEdge(viewport, overlay) {
  const viewportEdges = rectEdges(viewport);
  const overlayEdges = rectEdges(overlay);
  const distances = [
    ["top", Math.abs(overlayEdges.top - viewportEdges.top)],
    ["right", Math.abs(viewportEdges.right - overlayEdges.right)],
    ["bottom", Math.abs(viewportEdges.bottom - overlayEdges.bottom)],
    ["left", Math.abs(overlayEdges.left - viewportEdges.left)],
  ];
  return distances.reduce((nearest, candidate) => (
    candidate[1] < nearest[1] ? candidate : nearest
  ))[0];
}

export function createGraphSafeInset({
  geometryRevision = 0,
  viewportRect,
  hudRect,
  relationLabelMargin = 0,
} = {}) {
  const viewport = normalizedRect(viewportRect);
  const hud = normalizedRect(hudRect);
  const margin = nonNegative(relationLabelMargin);
  const inset = {
    geometryRevision: finiteNumber(geometryRevision),
    top: clamp(margin, 0, viewport.height),
    right: clamp(margin, 0, viewport.width),
    bottom: clamp(margin, 0, viewport.height),
    left: clamp(margin, 0, viewport.width),
  };

  if (hud.width > 0 && hud.height > 0 && overlaps(viewport, hud)) {
    const viewportEdges = rectEdges(viewport);
    const hudEdges = rectEdges(hud);
    switch (nearestViewportEdge(viewport, hud)) {
      case "right":
        inset.right = clamp(viewportEdges.right - hudEdges.left + margin, 0, viewport.width);
        break;
      case "bottom":
        inset.bottom = clamp(viewportEdges.bottom - hudEdges.top + margin, 0, viewport.height);
        break;
      case "left":
        inset.left = clamp(hudEdges.right - viewportEdges.left + margin, 0, viewport.width);
        break;
      case "top":
      default:
        inset.top = clamp(hudEdges.bottom - viewportEdges.top + margin, 0, viewport.height);
        break;
    }
  }

  return Object.freeze(inset);
}

function entries(value) {
  if (value instanceof Map) return [...value.entries()];
  if (Array.isArray(value)) {
    return value.map((item, index) => [
      item?.nodeId ?? item?.node_id ?? item?.id ?? index,
      item,
    ]);
  }
  if (value && typeof value === "object") return Object.entries(value);
  return [];
}

function keyedValue(collection, key) {
  if (collection instanceof Map) return collection.get(key);
  if (Array.isArray(collection)) {
    return collection.find((item) => (
      item?.nodeId === key || item?.node_id === key || item?.id === key
    ));
  }
  return collection?.[key];
}

function point(value) {
  return {
    x: finiteNumber(value?.x),
    y: finiteNumber(value?.y),
    z: finiteNumber(value?.z),
  };
}

function envelopeCorners(anchor, envelope) {
  const minimum = envelope?.min;
  const maximum = envelope?.max;
  if (minimum && maximum) {
    const min = point(minimum);
    const max = point(maximum);
    return [
      [min.x, min.y, min.z],
      [min.x, min.y, max.z],
      [min.x, max.y, min.z],
      [min.x, max.y, max.z],
      [max.x, min.y, min.z],
      [max.x, min.y, max.z],
      [max.x, max.y, min.z],
      [max.x, max.y, max.z],
    ];
  }

  const radius = Math.max(
    nonNegative(envelope?.bodyRadius),
    nonNegative(envelope?.hitRadius),
    nonNegative(envelope?.fitRadius),
    nonNegative(envelope?.radius),
  );
  if (radius === 0) return [[anchor.x, anchor.y, anchor.z]];
  return [-1, 1].flatMap((xDirection) => (
    [-1, 1].flatMap((yDirection) => (
      [-1, 1].map((zDirection) => [
        anchor.x + radius * xDirection,
        anchor.y + radius * yDirection,
        anchor.z + radius * zDirection,
      ])
    ))
  ));
}

function projectCorner(corner, camera, viewport) {
  const projected = new Vector3(...corner).project(camera);
  return {
    x: viewport.x + ((projected.x + 1) / 2) * viewport.width,
    y: viewport.y + ((1 - projected.y) / 2) * viewport.height,
    z: projected.z,
  };
}

function safeExtent(viewport, safeInset) {
  const left = viewport.x + clamp(nonNegative(safeInset?.left), 0, viewport.width);
  const right = viewport.x + viewport.width
    - clamp(nonNegative(safeInset?.right), 0, viewport.width);
  const top = viewport.y + clamp(nonNegative(safeInset?.top), 0, viewport.height);
  const bottom = viewport.y + viewport.height
    - clamp(nonNegative(safeInset?.bottom), 0, viewport.height);
  return Object.freeze({
    geometryRevision: finiteNumber(safeInset?.geometryRevision),
    top,
    right,
    bottom,
    left,
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top),
  });
}

function emptyProjectedExtent() {
  return Object.freeze({
    minX: 0,
    maxX: 0,
    minY: 0,
    maxY: 0,
    width: 0,
    height: 0,
  });
}

export function createSphericalSceneFitReport({
  settledAnchorMap,
  nodeEnvelopes,
  camera,
  viewportRect,
  safeInset,
} = {}) {
  if (!camera || typeof camera !== "object") {
    throw new TypeError("camera is required");
  }

  const viewport = normalizedRect(viewportRect);
  const safe = safeExtent(viewport, safeInset);
  const anchors = entries(settledAnchorMap);
  let minX = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  let cropCount = 0;

  for (const [nodeId, value] of anchors) {
    const anchor = point(value);
    const envelope = keyedValue(nodeEnvelopes, nodeId);
    const projected = envelopeCorners(anchor, envelope)
      .map((corner) => projectCorner(corner, camera, viewport));
    const nodeMinX = Math.min(...projected.map((item) => item.x));
    const nodeMaxX = Math.max(...projected.map((item) => item.x));
    const nodeMinY = Math.min(...projected.map((item) => item.y));
    const nodeMaxY = Math.max(...projected.map((item) => item.y));
    const invalidProjection = projected.some((item) => (
      !Number.isFinite(item.x)
      || !Number.isFinite(item.y)
      || !Number.isFinite(item.z)
      || item.z < -1
      || item.z > 1
    ));

    if (
      invalidProjection
      || safe.width <= 0
      || safe.height <= 0
      || nodeMinX < safe.left
      || nodeMaxX > safe.right
      || nodeMinY < safe.top
      || nodeMaxY > safe.bottom
    ) {
      cropCount += 1;
    }

    minX = Math.min(minX, nodeMinX);
    maxX = Math.max(maxX, nodeMaxX);
    minY = Math.min(minY, nodeMinY);
    maxY = Math.max(maxY, nodeMaxY);
  }

  const projectedExtent = anchors.length === 0
    ? emptyProjectedExtent()
    : Object.freeze({
      minX,
      maxX,
      minY,
      maxY,
      width: Math.max(0, maxX - minX),
      height: Math.max(0, maxY - minY),
    });
  const safeShortAxis = Math.min(safe.width, safe.height);
  const projectedShortAxis = Math.min(projectedExtent.width, projectedExtent.height);

  return Object.freeze({
    projectedShortAxisOccupancy: safeShortAxis > 0
      ? projectedShortAxis / safeShortAxis
      : 0,
    cropCount,
    safeExtent: safe,
    projectedExtent,
  });
}
