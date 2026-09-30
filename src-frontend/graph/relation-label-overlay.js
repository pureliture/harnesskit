import { graphNodeVisualEnvelope } from "./visual-metrics.js";
import { requireGraphSpatialPolicy } from "./spatial-policy.js";

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function relationIdentity(relation) {
  return String(relation?.node_id ?? relation?.id ?? relation?.canonical_id ?? "");
}

function relationCountLabel(relation) {
  const count = Math.max(0, Math.trunc(finiteNumber(
    relation?.exact_count ?? relation?.count,
  )));
  const kind = String(relation?.relation_kind ?? "").toLowerCase();
  const unit = kind === "workflow"
    ? count === 1 ? "step" : "steps"
    : count === 1 ? "component" : "components";
  return `${count} ${unit}`;
}

function displayText(relation) {
  const name = String(relation?.name ?? relation?.display_name ?? "").trim();
  return `${name} · ${relationCountLabel(relation)}`;
}

function worldCoordinate(relation, spatialPolicy) {
  const envelope = graphNodeVisualEnvelope(relation, spatialPolicy);
  return {
    x: finiteNumber(relation?.x),
    y: finiteNumber(relation?.y),
    z: finiteNumber(relation?.z),
    radius: Math.max(0, (envelope.max.x - envelope.min.x) / 2),
  };
}

function measure(element) {
  const rect = element.getBoundingClientRect?.() ?? {};
  return {
    width: Math.max(0, finiteNumber(rect.width, finiteNumber(element.offsetWidth))),
    height: Math.max(0, finiteNumber(rect.height, finiteNumber(element.offsetHeight))),
  };
}

function identityMatches(relation, identity) {
  if (identity == null) return false;
  const expected = String(identity);
  return [relation?.node_id, relation?.id, relation?.canonical_id]
    .some((candidate) => candidate != null && String(candidate) === expected);
}

function collisionPriority(relation, selectedId, focusedId) {
  if (identityMatches(relation, selectedId)) return 0;
  if (identityMatches(relation, focusedId)) return 1;
  return 2;
}

function intersects(left, right) {
  return left.left < right.right
    && left.right > right.left
    && left.top < right.bottom
    && left.bottom > right.top;
}

function safeBounds(root, safeInset = {}) {
  const width = Math.max(0, finiteNumber(root?.clientWidth, finiteNumber(root?.getBoundingClientRect?.().width)));
  const height = Math.max(0, finiteNumber(root?.clientHeight, finiteNumber(root?.getBoundingClientRect?.().height)));
  const left = Math.max(0, finiteNumber(safeInset.left));
  const top = Math.max(0, finiteNumber(safeInset.top));
  const right = Math.max(left, width - Math.max(0, finiteNumber(safeInset.right)));
  const bottom = Math.max(top, height - Math.max(0, finiteNumber(safeInset.bottom)));
  return { left, top, right, bottom };
}

function clampLabelRectangle({ left, top, width, height }, bounds) {
  const maximumLeft = Math.max(bounds.left, bounds.right - width);
  const maximumTop = Math.max(bounds.top, bounds.bottom - height);
  const clampedLeft = Math.min(maximumLeft, Math.max(bounds.left, left));
  const clampedTop = Math.min(maximumTop, Math.max(bounds.top, top));
  return {
    left: clampedLeft,
    top: clampedTop,
    right: clampedLeft + width,
    bottom: clampedTop + height,
  };
}

function displaceProtectedRectangle(rect, accepted, bounds) {
  let candidate = rect;
  const rowGap = 8;
  for (let attempt = 0; attempt < 24 && accepted.some((item) => intersects(candidate, item.rect)); attempt += 1) {
    const movedDown = {
      ...candidate,
      top: candidate.top + (candidate.bottom - candidate.top) + rowGap,
      bottom: candidate.bottom + (candidate.bottom - candidate.top) + rowGap,
    };
    candidate = movedDown.bottom <= bounds.bottom
      ? movedDown
      : clampLabelRectangle({
        left: candidate.left,
        top: bounds.top - (attempt + 1) * ((candidate.bottom - candidate.top) + rowGap),
        width: candidate.right - candidate.left,
        height: candidate.bottom - candidate.top,
      }, bounds);
  }
  return candidate;
}

export function bindRelationLabelOverlay(root, {
  projectWorldToScreen,
  spatialPolicy,
} = {}) {
  if (!root?.ownerDocument?.createElement || typeof root.appendChild !== "function") {
    throw new TypeError("relation label overlay root is required");
  }
  if (typeof projectWorldToScreen !== "function") {
    throw new TypeError("projectWorldToScreen must be a function");
  }
  const policy = requireGraphSpatialPolicy(spatialPolicy);

  const container = root.ownerDocument.createElement("div");
  container.className = "graph-relation-label-overlay";
  container.setAttribute?.("aria-hidden", "true");
  root.appendChild(container);
  let lastFrame = null;
  let released = false;
  const labelsByIdentity = new Map();
  let currentTransactionId = null;
  let currentTargetSignature = null;
  let staticRevision = 0;

  function normalizedProgress(value) {
    return Math.min(1, Math.max(0, Number(value) || 0));
  }

  function formattedNumber(value) {
    return String(Number(Number(value).toFixed(6)));
  }

  function setStyleProperty(element, name, value) {
    if (typeof element?.style?.setProperty === "function") {
      element.style.setProperty(name, value);
    } else if (element?.style) {
      element.style[name] = value;
    }
  }

  function presentationFor(frame) {
    const presentation = frame?.presentationFrame;
    if (!presentation || presentation.transactionId == null) {
      staticRevision += 1;
      return { transactionId: `static:${staticRevision}`, progress: 1 };
    }
    return {
      transactionId: String(presentation.transactionId),
      progress: normalizedProgress(presentation.progress),
    };
  }

  function applyRecordVisual(record, opacity, emphasis) {
    record.opacity = normalizedProgress(opacity);
    record.emphasis = normalizedProgress(emphasis);
    setStyleProperty(
      record.label,
      "--graph-relation-label-opacity",
      formattedNumber(record.opacity),
    );
    setStyleProperty(
      record.label,
      "--graph-relation-label-emphasis",
      formattedNumber(record.emphasis),
    );
    setStyleProperty(
      record.label,
      "--graph-relation-label-emphasis-percent",
      `${formattedNumber(record.emphasis * 100)}%`,
    );
    setStyleProperty(
      record.label,
      "--graph-relation-label-background-percent",
      `${formattedNumber(record.emphasis * 8)}%`,
    );
  }

  function removeRecord(record) {
    record.label.remove?.();
    if (labelsByIdentity.get(record.identity) === record) {
      labelsByIdentity.delete(record.identity);
    }
  }

  function labelFor(relation) {
    const identity = relationIdentity(relation);
    let record = labelsByIdentity.get(identity);
    if (!record) {
      const label = root.ownerDocument.createElement("div");
      label.className = "graph-relation-label";
      label.dataset.relationLabelPresence = "entering";
      record = {
        identity,
        label,
        presented: false,
        opacity: 0,
        emphasis: 0,
        transition: null,
      };
      labelsByIdentity.set(identity, record);
      applyRecordVisual(record, 0, 0);
    }
    const { label } = record;
    label.dataset.relationLabelId = identity;
    label.textContent = displayText(relation);
    if (label.parentNode !== container) container.appendChild(label);
    return record;
  }

  function targetSignature(accepted) {
    return JSON.stringify(accepted.map(({ identity, priority }) => [identity, priority]));
  }

  function beginTransition(transactionId, accepted) {
    const acceptedByIdentity = new Map(accepted.map((candidate) => [
      candidate.identity,
      { opacity: 1, emphasis: candidate.priority < 2 ? 1 : 0 },
    ]));
    for (const record of [...labelsByIdentity.values()]) {
      const target = acceptedByIdentity.get(record.identity) ?? { opacity: 0, emphasis: 0 };
      if (!record.presented && target.opacity === 0) {
        removeRecord(record);
        continue;
      }
      if (target.opacity === 1) record.presented = true;
      record.transition = {
        transactionId,
        fromOpacity: record.opacity,
        toOpacity: target.opacity,
        fromEmphasis: record.emphasis,
        toEmphasis: target.emphasis,
      };
    }
  }

  function applyTransition(transactionId, progress) {
    for (const record of [...labelsByIdentity.values()]) {
      const transition = record.transition;
      if (!transition || transition.transactionId !== transactionId) continue;
      const opacity = transition.fromOpacity
        + ((transition.toOpacity - transition.fromOpacity) * progress);
      const emphasis = transition.fromEmphasis
        + ((transition.toEmphasis - transition.fromEmphasis) * progress);
      applyRecordVisual(record, opacity, emphasis);
      record.label.dataset.relationLabelPresence = transition.toOpacity === 0
        ? "exiting"
        : progress >= 1 ? "visible" : "entering";
      if (progress >= 1 && transition.toOpacity === 0) removeRecord(record);
    }
  }

  function render({
    relations = [],
    selectedId = null,
    focusedId = null,
    camera,
    safeInset,
    presentationFrame,
  } = {}) {
    const bounds = safeBounds(root, safeInset);
    const candidates = relations
      .filter((relation) => relation?.node_type === "relation")
      .map((relation) => {
        const projected = projectWorldToScreen(
          worldCoordinate(relation, policy),
          camera,
          safeInset,
        );
        if (!projected || projected.visible === false) return null;
        const x = Number(projected.x);
        const y = Number(projected.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;

        const record = labelFor(relation);
        const { label } = record;
        const priority = collisionPriority(relation, selectedId, focusedId);
        label.dataset.relationLabelState = priority === 0
          ? "selected"
          : priority === 1 ? "focused" : "idle";
        const size = measure(label);
        const radius = Math.max(0, finiteNumber(
          projected.radiusPx,
          finiteNumber(projected.radius),
        ));
        const rect = clampLabelRectangle({
          left: x - size.width / 2,
          top: y - radius - size.height - 8,
          width: size.width,
          height: size.height,
        }, bounds);
        return {
          identity: relationIdentity(relation),
          label,
          record,
          priority,
          rect,
        };
      })
      .filter(Boolean)
      .sort((left, right) => (
        left.priority - right.priority
        || (left.identity < right.identity ? -1 : left.identity > right.identity ? 1 : 0)
      ));

    const accepted = [];
    for (const candidate of candidates) {
      const protectedLabel = candidate.priority < 2;
      if (protectedLabel) {
        candidate.rect = displaceProtectedRectangle(candidate.rect, accepted, bounds);
        accepted.push(candidate);
      } else if (accepted.every((item) => !intersects(candidate.rect, item.rect))) {
        accepted.push(candidate);
      }
    }
    const presentation = presentationFor({ presentationFrame });
    const signature = targetSignature(accepted);
    const transactionChanged = presentation.transactionId !== currentTransactionId;
    const transitionRequired = !transactionChanged
      && currentTargetSignature !== null
      && signature !== currentTargetSignature;
    if (transactionChanged) {
      currentTransactionId = presentation.transactionId;
      currentTargetSignature = signature;
      beginTransition(currentTransactionId, accepted);
    }
    if (!transitionRequired) applyTransition(currentTransactionId, presentation.progress);
    accepted.forEach(({ label, rect }) => {
      label.style.transform = `translate3d(${rect.left}px, ${rect.top}px, 0)`;
      container.appendChild(label);
    });
    const acceptedIdentities = new Set(accepted.map(({ identity }) => identity));
    [...labelsByIdentity.values()]
      .filter((record) => !acceptedIdentities.has(record.identity))
      .sort((left, right) => (
        left.identity < right.identity ? -1 : left.identity > right.identity ? 1 : 0
      ))
      .forEach((record) => container.appendChild(record.label));
    return { transitionRequired };
  }

  function sync(frame = {}) {
    if (released) return;
    const frameRevision = Number(frame?.geometryRevision);
    const insetRevision = Number(frame?.safeInset?.geometryRevision);
    if (
      Number.isFinite(frameRevision)
      && Number.isFinite(insetRevision)
      && frameRevision !== insetRevision
    ) {
      return;
    }
    lastFrame = frame;
    return render(frame);
  }

  function refreshOnce(_reason = "refresh") {
    if (released || lastFrame === null) return;
    return render(lastFrame);
  }

  function clear() {
    if (released) return;
    lastFrame = null;
    [...labelsByIdentity.values()].forEach(removeRecord);
    currentTransactionId = null;
    currentTargetSignature = null;
  }

  function release() {
    if (released) return;
    clear();
    container.remove?.();
    released = true;
  }

  return Object.freeze({ sync, refreshOnce, clear, release });
}
