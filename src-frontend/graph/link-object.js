import * as THREE from "three";

const LINK_COLOR_NEUTRAL = 0x64748b;
const PARTICLE_SPEED_PER_SECOND = 0.16;
const PRESENTATION_OPACITY_STEPS = 20;
const UNRELATED_LINE_OPACITY = 0.3;

function retainsVisualWeight(emphasis) {
  return emphasis === "active" || emphasis === "focus";
}

function boundedOpacity(value) {
  return Math.min(1, Math.max(0, Number(value) || 0));
}

function opacityForLine(emphasis) {
  return emphasis === "faded" ? UNRELATED_LINE_OPACITY : 1;
}

function opacityForStroke(emphasis) {
  return retainsVisualWeight(emphasis) ? 1 : 0;
}

function semantic(link) {
  return String(link?.semantic ?? "unknown");
}

function typedDirectionality(link) {
  const value = String(link?.directionality ?? "").toLowerCase();
  return value === "directed" || value === "unordered" ? value : null;
}

function stableUnit(value) {
  let hash = 0x811c9dc5;
  for (const character of String(value)) {
    hash ^= character.codePointAt(0);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0) / 0x100000000;
}

function orderedDistinctOrdinals(link) {
  return [...new Set((Array.isArray(link?.occurrences) ? link.occurrences : [])
    .map((occurrence) => Number(occurrence?.ordinal))
    .filter((ordinal) => Number.isInteger(ordinal) && ordinal > 0))]
    .sort((left, right) => left - right);
}

export function describeGraphLink(link, interactionModel = {}) {
  const linkSemantic = semantic(link);
  const directionality = typedDirectionality(link);
  const semanticActive = interactionModel?.activeLinkIds?.has(link?.link_id) ?? false;
  const focusOneHop = interactionModel?.selectedOneHopLinkIds?.has(link?.link_id) ?? false;
  const emphasis = interactionModel?.hasFocus
    ? semanticActive ? "active" : focusOneHop ? "focus" : "faded"
    : "idle";
  const workflowIncidence = linkSemantic === "workflow-step";
  const ordinals = orderedDistinctOrdinals(link);
  return {
    semantic: linkSemantic,
    dashed: linkSemantic === "component-cross-link" || linkSemantic === "invoked-by",
    directionality,
    directional: directionality === "directed",
    particleMotion: directionality === "directed"
      ? "source-to-target"
      : directionality === "unordered"
        ? "ping-pong"
        : null,
    emphasis,
    focusOneHop,
    semanticActive,
    ordinalLabel: workflowIncidence && semanticActive && ordinals.length > 0
      ? ordinals.join(" · ")
      : null,
  };
}

function defaultCanvasFactory() {
  if (typeof document === "undefined") {
    throw new Error("Canvas is unavailable outside a browser renderer");
  }
  return document.createElement("canvas");
}

export function createGraphLinkObjectFactory({
  canvasFactory = defaultCanvasFactory,
  monotonicClock = () => globalThis.performance?.now?.() ?? Date.now(),
  three = THREE,
} = {}) {
  const arrowGeometry = new three.ConeGeometry(1.25, 3.6, 8);
  const activeStrokeGeometry = new three.CylinderGeometry(0.38, 0.38, 1, 8);
  const particleGeometry = new three.SphereGeometry(0.72, 10, 8);
  const materials = new Map();
  const textures = new Set();
  const spriteMaterials = new Set();
  const geometries = new Set();
  const records = new Map();
  const yAxis = new three.Vector3(0, 1, 0);
  let activityState = {
    active: true,
    reducedMotion: false,
  };
  let activeElapsedMilliseconds = 0;
  let lastParticleClockMilliseconds = Math.max(0, Number(monotonicClock()) || 0);
  let disposed = false;

  function opacityBucket(opacity) {
    return Math.round(boundedOpacity(opacity) * PRESENTATION_OPACITY_STEPS);
  }

  function bucketOpacity(bucket) {
    return bucket / PRESENTATION_OPACITY_STEPS;
  }

  function materialFor(dashed = false, opacity = 1) {
    const bucket = opacityBucket(opacity);
    const key = `line:${dashed ? "dashed" : "solid"}:${bucket}`;
    if (materials.has(key)) return materials.get(key);
    const options = {
      color: LINK_COLOR_NEUTRAL,
      opacity: bucketOpacity(bucket),
      transparent: true,
    };
    const material = dashed
      ? new three.LineDashedMaterial({ ...options, dashSize: 2.3, gapSize: 1.5 })
      : new three.LineBasicMaterial(options);
    materials.set(key, material);
    return material;
  }

  function arrowMaterial(opacity = 0) {
    const bucket = opacityBucket(opacity);
    const key = `arrow:${bucket}`;
    if (materials.has(key)) return materials.get(key);
    const material = new three.MeshBasicMaterial({
      color: LINK_COLOR_NEUTRAL,
      opacity: bucketOpacity(bucket),
      transparent: true,
    });
    materials.set(key, material);
    return material;
  }

  function particleMaterial(emphasis) {
    const opacity = emphasis === "faded" ? 0.42 : retainsVisualWeight(emphasis) ? 1 : 0.72;
    const bucket = opacityBucket(opacity);
    const key = `particle:${bucket}`;
    if (materials.has(key)) return materials.get(key);
    const material = new three.MeshBasicMaterial({
      color: LINK_COLOR_NEUTRAL,
      opacity: bucketOpacity(bucket),
      transparent: true,
    });
    materials.set(key, material);
    return material;
  }

  for (let bucket = 0; bucket <= PRESENTATION_OPACITY_STEPS; bucket += 1) {
    const opacity = bucketOpacity(bucket);
    materialFor(false, opacity);
    materialFor(true, opacity);
    arrowMaterial(opacity);
  }
  ["idle", "faded", "active"].forEach((emphasis) => particleMaterial(emphasis));

  function advanceParticleClock(timeMilliseconds = monotonicClock()) {
    const now = Math.max(0, Number(timeMilliseconds) || 0);
    const elapsed = Math.max(0, now - lastParticleClockMilliseconds);
    if (activityState.active && !activityState.reducedMotion) {
      activeElapsedMilliseconds += elapsed;
    }
    lastParticleClockMilliseconds = now;
    return activeElapsedMilliseconds;
  }

  function particleProgress(record, timeMilliseconds = monotonicClock()) {
    const phase = stableUnit(record.link?.link_id);
    const elapsed = advanceParticleClock(timeMilliseconds) / 1000;
    const progress = phase + elapsed * PARTICLE_SPEED_PER_SECOND;
    if (record.description.particleMotion === "source-to-target") return progress % 1;
    if (record.description.particleMotion === "ping-pong") {
      const cycle = progress % 2;
      return cycle <= 1 ? cycle : 2 - cycle;
    }
    return null;
  }

  function positionParticle(record, timeMilliseconds) {
    if (!record.particle || !record.lastStart || !record.lastEnd) return false;
    const progress = particleProgress(record, timeMilliseconds);
    if (progress === null) return false;
    record.particle.position
      .copy(record.lastStart)
      .lerp(record.lastEnd, progress);
    return true;
  }

  function createOrdinalSprite(label) {
    const canvas = canvasFactory();
    canvas.width = 256;
    canvas.height = 80;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("2D canvas context is unavailable");
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#64748b";
    context.font = "700 38px ui-monospace, monospace";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(label, canvas.width / 2, canvas.height / 2);
    const texture = new three.CanvasTexture(canvas);
    if ("colorSpace" in texture && three.SRGBColorSpace) texture.colorSpace = three.SRGBColorSpace;
    texture.needsUpdate = true;
    textures.add(texture);
    const material = new three.SpriteMaterial({
      depthTest: false,
      map: texture,
      transparent: true,
    });
    spriteMaterials.add(material);
    const sprite = new three.Sprite(material);
    sprite.userData.harnesskitRole = "ordinal";
    sprite.renderOrder = 30;
    sprite.scale.set(11, 3.4, 1);
    return sprite;
  }

  function create(link) {
    if (disposed) throw new Error("link object factory is disposed");
    const description = describeGraphLink(link);
    const geometry = new three.BufferGeometry();
    geometry.setAttribute("position", new three.Float32BufferAttribute([
      0, 0, 0,
      0, 0, 0,
    ], 3));
    geometries.add(geometry);

    const group = new three.Group();
    group.userData.harnesskitLinkId = String(link?.link_id ?? "");
    const line = new three.Line(
      geometry,
      materialFor(description.dashed, opacityForLine(description.emphasis)),
    );
    line.userData.harnesskitRole = "line";
    group.add(line);

    const activeStroke = new three.Mesh(
      activeStrokeGeometry,
      arrowMaterial(opacityForStroke(description.emphasis)),
    );
    activeStroke.userData.harnesskitRole = "active-stroke";
    activeStroke.visible = false;
    group.add(activeStroke);

    let direction = null;
    if (description.directional) {
      direction = new three.Mesh(
        arrowGeometry,
        arrowMaterial(opacityForStroke(description.emphasis)),
      );
      direction.userData.harnesskitRole = "direction";
      direction.visible = false;
      group.add(direction);
    }

    let particle = null;
    const hasRealTypedPath = description.particleMotion
      && typeof link?.link_id === "string"
      && link.link_id.length > 0
      && typeof link?.source_node_id === "string"
      && link.source_node_id.length > 0
      && typeof link?.target_node_id === "string"
      && link.target_node_id.length > 0;
    if (hasRealTypedPath) {
      particle = new three.Mesh(particleGeometry, particleMaterial(description.emphasis));
      particle.userData.harnesskitRole = "link-particle";
      particle.userData.harnesskitParticleOwnerLinkId = link.link_id;
      particle.renderOrder = 12;
      group.add(particle);
    }

    const ordinalText = description.semantic === "workflow-step"
      ? orderedDistinctOrdinals(link).join(" · ") || null
      : null;
    const ordinal = ordinalText ? createOrdinalSprite(ordinalText) : null;
    if (ordinal) {
      ordinal.visible = false;
      group.add(ordinal);
    }

    const record = {
      description,
      activeStroke,
      direction,
      group,
      line,
      link,
      lastEnd: null,
      lastStart: null,
      lineOpacity: opacityForLine(description.emphasis),
      ordinal,
      ordinalLabel: ordinalText,
      particle,
      strokeOpacity: opacityForStroke(description.emphasis),
    };
    if (particle) {
      particle.onBeforeRender = () => {
        positionParticle(record);
      };
    }
    records.set(String(link?.link_id ?? ""), record);
    return group;
  }

  function updatePosition(object, positions) {
    const record = records.get(object?.userData?.harnesskitLinkId);
    if (!record) return false;
    const start = new three.Vector3(
      Number(positions?.start?.x) || 0,
      Number(positions?.start?.y) || 0,
      Number(positions?.start?.z) || 0,
    );
    const end = new three.Vector3(
      Number(positions?.end?.x) || 0,
      Number(positions?.end?.y) || 0,
      Number(positions?.end?.z) || 0,
    );
    record.lastStart = start.clone();
    record.lastEnd = end.clone();
    const attribute = record.line.geometry.getAttribute("position");
    attribute.setXYZ(0, start.x, start.y, start.z);
    attribute.setXYZ(1, end.x, end.y, end.z);
    attribute.needsUpdate = true;
    if (record.description.dashed) record.line.computeLineDistances();

    const directionVector = end.clone().sub(start);
    if (directionVector.lengthSq() > 0) {
      record.activeStroke.position.copy(start).addScaledVector(directionVector, 0.5);
      record.activeStroke.quaternion.setFromUnitVectors(yAxis, directionVector.clone().normalize());
      record.activeStroke.scale.set(1, directionVector.length(), 1);
    }
    if (record.direction && directionVector.lengthSq() > 0) {
      record.direction.position.copy(start).addScaledVector(directionVector, 0.72);
      record.direction.quaternion.setFromUnitVectors(yAxis, directionVector.clone().normalize());
    }
    if (record.ordinal) {
      record.ordinal.position.copy(start).addScaledVector(directionVector, 0.5);
      record.ordinal.position.y += 3.2;
    }
    positionParticle(record);
    return true;
  }

  function applyPresentation(record, next, lineOpacity, strokeOpacity) {
    const resolvedLineOpacity = boundedOpacity(lineOpacity);
    const resolvedStrokeOpacity = boundedOpacity(strokeOpacity);
    record.description = next;
    record.lineOpacity = resolvedLineOpacity;
    record.strokeOpacity = resolvedStrokeOpacity;
    record.line.material = materialFor(next.dashed, resolvedLineOpacity);
    record.line.userData.harnesskitEmphasis = next.emphasis;
    record.activeStroke.material = arrowMaterial(resolvedStrokeOpacity);
    record.activeStroke.visible = resolvedStrokeOpacity > 0;
    const strokeWeight = 0.65 + (0.35 * resolvedStrokeOpacity);
    record.activeStroke.scale.x = strokeWeight;
    record.activeStroke.scale.z = strokeWeight;
    if (record.particle) record.particle.material = particleMaterial(next.emphasis);
    if (record.direction) {
      record.direction.material = arrowMaterial(resolvedStrokeOpacity);
      record.direction.visible = resolvedStrokeOpacity > 0;
    }
    if (record.ordinal) record.ordinal.visible = Boolean(next.ordinalLabel);
  }

  function syncAll(interactionModel) {
    records.forEach((record) => {
      const next = describeGraphLink(record.link, interactionModel);
      applyPresentation(
        record,
        next,
        opacityForLine(next.emphasis),
        opacityForStroke(next.emphasis),
      );
    });
  }

  function createPresentationTransition(interactionModel) {
    records.forEach((record) => {
      record.transitionFromLineOpacity = record.lineOpacity;
      record.transitionFromStrokeOpacity = record.strokeOpacity;
      record.transitionNext = describeGraphLink(record.link, interactionModel);
    });
    return (progress) => {
      const normalized = boundedOpacity(progress);
      records.forEach((record) => {
        const next = record.transitionNext;
        if (!next) return;
        const targetLineOpacity = opacityForLine(next.emphasis);
        const targetStrokeOpacity = opacityForStroke(next.emphasis);
        const lineOpacity = record.transitionFromLineOpacity
          + ((targetLineOpacity - record.transitionFromLineOpacity) * normalized);
        const strokeOpacity = record.transitionFromStrokeOpacity
          + ((targetStrokeOpacity - record.transitionFromStrokeOpacity) * normalized);
        applyPresentation(record, next, lineOpacity, strokeOpacity);
        if (normalized >= 1) record.transitionNext = null;
      });
    };
  }

  function setActivityState(nextState = {}) {
    advanceParticleClock();
    activityState = {
      active: Object.hasOwn(nextState, "active")
        ? Boolean(nextState.active)
        : activityState.active,
      reducedMotion: Object.hasOwn(nextState, "reducedMotion")
        ? Boolean(nextState.reducedMotion)
        : activityState.reducedMotion,
    };
  }

  function dispose() {
    if (disposed) return;
    records.clear();
    geometries.forEach((geometry) => geometry.dispose());
    arrowGeometry.dispose();
    activeStrokeGeometry.dispose();
    particleGeometry.dispose();
    materials.forEach((material) => material.dispose());
    spriteMaterials.forEach((material) => material.dispose());
    textures.forEach((texture) => texture.dispose());
    disposed = true;
  }

  return {
    create,
    createPresentationTransition,
    dispose,
    setActivityState,
    syncAll,
    updatePosition,
  };
}
