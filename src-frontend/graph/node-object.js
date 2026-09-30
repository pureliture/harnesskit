import * as THREE from "three";
import {
  GRAPH_NODE_VISUAL_METRICS,
  normalizeRelationSizeScale,
} from "./visual-metrics.js";
import { graphNodeEmphasisFor } from "./visual-emphasis.js";
import { requireGraphSpatialPolicy } from "./spatial-policy.js";

export { GRAPH_NODE_VISUAL_METRICS } from "./visual-metrics.js";

const KIND_MARKS = Object.freeze({
  agent: "A",
  command: "C",
  composite: "M",
  hook: "H",
  profile: "P",
  rule: "R",
  skill: "S",
  unprofiled: "U",
  workflow: "W",
});

const APPEARANCE_PALETTES = Object.freeze({
  dark: Object.freeze({
    body: 0x475569,
    outline: 0xcbd5e1,
    rim: 0xf8fafc,
    text: "#f8fafc",
  }),
  light: Object.freeze({
    body: 0x64748b,
    outline: 0x334155,
    rim: 0x0f172a,
    text: "#0f172a",
  }),
});

const BODY_EMPHASIS = ["idle", "adjacent", "contextual", "unrelated"];
const OUTLINE_EMPHASIS = ["idle", "selected", "adjacent", "contextual", "unrelated"];
const PRESENTATION_OPACITY_STEPS = 20;
const OUTLINE_CONTRAST_STEPS = 8;
const RIM_OPACITY_STEPS = 100;

const COMPONENT_KINDS = new Set(["agent", "command", "composite", "hook", "rule", "skill"]);
const RELATION_KINDS = new Set(["profile", "unprofiled", "workflow"]);

function normalizedKind(node) {
  if (node?.node_type === "relation") {
    const relationKind = String(node.relation_kind ?? "unknown").toLowerCase();
    return RELATION_KINDS.has(relationKind) ? relationKind : "unknown";
  }
  const componentKind = String(node?.kind ?? "unknown").toLowerCase();
  return COMPONENT_KINDS.has(componentKind) ? componentKind : "unknown";
}

export function describeGraphNode(node, spatialPolicy) {
  requireGraphSpatialPolicy(spatialPolicy);
  const relation = node?.node_type === "relation";
  const kind = normalizedKind(node);
  const exactCount = Math.max(0, Number(node?.exact_count) || 0);
  return {
    nodeId: String(node?.node_id ?? ""),
    tier: relation ? "relation" : "component",
    kind,
    kindMark: KIND_MARKS[kind] ?? "?",
    label: relation ? String(node?.name ?? node?.canonical_id ?? "") : null,
    countLabel: relation
      ? `${exactCount} ${kind === "workflow" ? "steps" : "components"}`
      : null,
    sizeScale: relation ? normalizeRelationSizeScale(node?.size_scale, spatialPolicy) : 1,
  };
}

function defaultCanvasFactory() {
  if (typeof document === "undefined") {
    throw new Error("Canvas is unavailable outside a browser renderer");
  }
  return document.createElement("canvas");
}

function boundedUnit(value) {
  return Math.min(1, Math.max(0, Number(value) || 0));
}

function bucket(value, steps) {
  return Math.round(boundedUnit(value) * steps);
}

function bodyOpacityFor(emphasis) {
  return {
    adjacent: 0.96,
    contextual: 0.84,
    unrelated: 0.78,
    idle: 0.96,
    selected: 0.96,
  }[emphasis] ?? 0.96;
}

function outlineOpacityFor(tier, emphasis) {
  const relation = tier === "relation";
  return {
    selected: 1,
    adjacent: relation ? 0.82 : 0.72,
    contextual: relation ? 0.72 : 0.62,
    unrelated: relation ? 0.58 : 0.54,
    idle: relation ? 0.82 : 0.68,
  }[emphasis] ?? (relation ? 0.82 : 0.68);
}

function outlineContrastFor(emphasis) {
  return emphasis === "selected" ? 1 : 0;
}

function interpolated(start, target, progress) {
  return start + ((target - start) * boundedUnit(progress));
}

function fitFontToWidth(context, text, font, maxWidth, minimumFontSize) {
  const match = String(font).match(/([0-9.]+)px/);
  if (!match || !Number.isFinite(maxWidth) || maxWidth <= 0) return font;
  const initialFontSize = Number(match[1]);
  const minimum = Math.min(initialFontSize, Math.max(1, Number(minimumFontSize) || 1));
  for (let fontSize = initialFontSize; fontSize >= minimum; fontSize -= 1) {
    const candidate = String(font).replace(match[0], `${fontSize}px`);
    context.font = candidate;
    if (context.measureText(text).width <= maxWidth) return candidate;
  }
  return String(font).replace(match[0], `${minimum}px`);
}

function makeTextSprite({
  canvasFactory,
  lines,
  palette,
  role,
  size,
  three,
  textures,
  spriteMaterials,
}) {
  const canvas = canvasFactory();
  canvas.width = size.width;
  canvas.height = size.height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("2D canvas context is unavailable");
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = palette.text;
  context.textAlign = "center";
  context.textBaseline = "middle";
  lines.forEach((line, index) => {
    const font = index === 0 ? size.primaryFont : size.secondaryFont;
    context.font = size.maxLineWidth?.[index]
      ? fitFontToWidth(
        context,
        line,
        font,
        size.maxLineWidth[index],
        size.minimumFontSize?.[index],
      )
      : font;
    if (palette.stroke) {
      context.strokeStyle = palette.stroke;
      context.lineWidth = size.strokeWidth?.[index] ?? 6;
      context.lineJoin = "round";
      context.strokeText(line, canvas.width / 2, size.lineY[index]);
    }
    context.fillText(line, canvas.width / 2, size.lineY[index]);
  });

  const texture = new three.CanvasTexture(canvas);
  if ("colorSpace" in texture && three.SRGBColorSpace) {
    texture.colorSpace = three.SRGBColorSpace;
  }
  texture.needsUpdate = true;
  textures.add(texture);
  const material = new three.SpriteMaterial({
    color: 0xffffff,
    depthTest: false,
    map: texture,
    transparent: true,
  });
  spriteMaterials.add(material);
  const sprite = new three.Sprite(material);
  sprite.userData.harnesskitRole = role;
  sprite.renderOrder = 20;
  sprite.scale.set(size.worldWidth, size.worldHeight, 1);
  return sprite;
}

export function createGraphNodeObjectFactory({
  canvasFactory = defaultCanvasFactory,
  three = THREE,
  SphereGeometryClass = three.SphereGeometry,
  spatialPolicy,
} = {}) {
  const geometryPolicy = requireGraphSpatialPolicy(spatialPolicy).visualGeometryScale;
  const componentGeometry = {
    ...GRAPH_NODE_VISUAL_METRICS.componentGeometry,
    radius: geometryPolicy.componentBodyRadius,
  };
  const relationGeometry = {
    ...GRAPH_NODE_VISUAL_METRICS.relationGeometry,
    radius: geometryPolicy.relationBodyRadius,
  };
  const geometries = {
    component: new SphereGeometryClass(
      componentGeometry.radius,
      componentGeometry.widthSegments,
      componentGeometry.heightSegments,
    ),
    relation: new SphereGeometryClass(
      relationGeometry.radius,
      relationGeometry.widthSegments,
      relationGeometry.heightSegments,
    ),
  };
  const bodyMaterials = new Map();
  const outlineMaterials = new Map();
  const transitionBodyMaterials = new Map();
  const transitionOutlineMaterials = new Map();
  const transitionRimMaterials = new Map();
  const rimMaterials = new Map();
  const hitTargetMaterial = new three.MeshBasicMaterial({
    color: 0x000000,
    colorWrite: false,
    depthWrite: false,
    opacity: 0,
    transparent: true,
  });
  const textures = new Set();
  const spriteMaterials = new Set();
  const records = new Map();
  let disposed = false;

  function appearanceKey(appearance) {
    return appearance === "light" ? "light" : "dark";
  }

  function paletteFor(appearance) {
    return APPEARANCE_PALETTES[appearanceKey(appearance)];
  }

  function bodyMaterial(appearance, tier, emphasis = "idle") {
    const normalizedEmphasis = emphasis === "selected" ? "idle" : emphasis;
    return bodyMaterials.get(`${appearanceKey(appearance)}:${tier}:${normalizedEmphasis}`);
  }

  function outlineMaterial(appearance, tier, emphasis = "idle") {
    return outlineMaterials.get(`${appearanceKey(appearance)}:${tier}:${emphasis}`);
  }

  function transitionBodyMaterial(appearance, tier, opacity) {
    return transitionBodyMaterials.get(
      `${appearanceKey(appearance)}:${tier}:${bucket(opacity, PRESENTATION_OPACITY_STEPS)}`,
    );
  }

  function transitionOutlineMaterial(appearance, tier, opacity, contrast) {
    return transitionOutlineMaterials.get(
      `${appearanceKey(appearance)}:${tier}:${bucket(opacity, PRESENTATION_OPACITY_STEPS)}:${bucket(contrast, OUTLINE_CONTRAST_STEPS)}`,
    );
  }

  function rimMaterial(appearance) {
    return rimMaterials.get(appearanceKey(appearance));
  }

  function transitionRimMaterial(appearance, opacity) {
    const opacityBucket = bucket(opacity, RIM_OPACITY_STEPS);
    if (opacityBucket === RIM_OPACITY_STEPS) return rimMaterial(appearance);
    return transitionRimMaterials.get(appearanceKey(appearance))[opacityBucket];
  }

  for (const appearance of Object.keys(APPEARANCE_PALETTES)) {
    const palette = paletteFor(appearance);
    for (const tier of ["component", "relation"]) {
      const relation = tier === "relation";
      for (const emphasis of BODY_EMPHASIS) {
        const opacity = bodyOpacityFor(emphasis);
        bodyMaterials.set(`${appearance}:${tier}:${emphasis}`, new three.MeshStandardMaterial({
          color: palette.body,
          emissive: 0x000000,
          emissiveIntensity: 0,
          metalness: relation ? 0.36 : 0.22,
          opacity,
          roughness: relation ? 0.4 : 0.58,
          transparent: true,
        }));
      }
      for (const emphasis of OUTLINE_EMPHASIS) {
        outlineMaterials.set(`${appearance}:${tier}:${emphasis}`, new three.MeshBasicMaterial({
          color: outlineContrastFor(emphasis) ? palette.rim : palette.outline,
          depthWrite: false,
          opacity: outlineOpacityFor(tier, emphasis),
          side: three.BackSide,
          transparent: true,
        }));
      }
      for (let opacityBucket = 0; opacityBucket <= PRESENTATION_OPACITY_STEPS; opacityBucket += 1) {
        const opacity = opacityBucket / PRESENTATION_OPACITY_STEPS;
        transitionBodyMaterials.set(
          `${appearance}:${tier}:${opacityBucket}`,
          new three.MeshStandardMaterial({
            color: palette.body,
            emissive: 0x000000,
            emissiveIntensity: 0,
            metalness: relation ? 0.36 : 0.22,
            opacity,
            roughness: relation ? 0.4 : 0.58,
            transparent: true,
          }),
        );
        for (let contrastBucket = 0; contrastBucket <= OUTLINE_CONTRAST_STEPS; contrastBucket += 1) {
          const contrast = contrastBucket / OUTLINE_CONTRAST_STEPS;
          const color = new three.Color(palette.outline).lerp(
            new three.Color(palette.rim),
            contrast,
          );
          transitionOutlineMaterials.set(
            `${appearance}:${tier}:${opacityBucket}:${contrastBucket}`,
            new three.MeshBasicMaterial({
              color,
              depthWrite: false,
              opacity,
              side: three.BackSide,
              transparent: true,
            }),
          );
        }
      }
    }
    rimMaterials.set(appearance, new three.MeshBasicMaterial({
      color: palette.rim,
      depthWrite: false,
      opacity: 1,
      side: three.BackSide,
      transparent: true,
    }));
    const appearanceTransitionRimMaterials = [];
    for (let opacityBucket = 0; opacityBucket < RIM_OPACITY_STEPS; opacityBucket += 1) {
      appearanceTransitionRimMaterials.push(
        new three.MeshBasicMaterial({
          color: palette.rim,
          depthWrite: false,
          opacity: opacityBucket / RIM_OPACITY_STEPS,
          side: three.BackSide,
          transparent: true,
        }),
      );
    }
    transitionRimMaterials.set(appearance, appearanceTransitionRimMaterials);
  }

  function create(node) {
    if (disposed) throw new Error("node object factory is disposed");
    const description = describeGraphNode(node, spatialPolicy);
    const palette = paletteFor("dark");
    const group = new three.Group();
    const presentationGroup = new three.Group();
    group.userData.harnesskitNodeId = description.nodeId;
    group.userData.harnesskitNodeTier = description.tier;
    presentationGroup.userData.harnesskitRole = "presentation-group";
    group.add(presentationGroup);

    const body = new three.Mesh(
      geometries[description.tier],
      bodyMaterial("dark", description.tier),
    );
    body.userData.harnesskitRole = "body";
    presentationGroup.add(body);

    const outline = new three.Mesh(
      geometries[description.tier],
      outlineMaterial("dark", description.tier),
    );
    outline.userData.harnesskitRole = "outline";
    outline.scale.setScalar(geometryPolicy.outlineShellScale);
    presentationGroup.add(outline);

    const hitTarget = new three.Mesh(
      geometries[description.tier],
      hitTargetMaterial,
    );
    hitTarget.userData.harnesskitRole = "hit-target";
    hitTarget.scale.setScalar(geometryPolicy.hitTargetScale);
    group.add(hitTarget);

    const rim = new three.Mesh(
      geometries[description.tier],
      rimMaterial("dark"),
    );
    rim.userData.harnesskitRole = "focus-rim";
    rim.renderOrder = 28;
    rim.scale.setScalar(geometryPolicy.outlineShellScale);
    rim.visible = false;
    presentationGroup.add(rim);

    const relation = description.tier === "relation";
    const mark = makeTextSprite({
      canvasFactory,
      lines: [description.kindMark],
      palette,
      role: "kind-mark",
      size: {
        width: 128,
        height: 128,
        primaryFont: "800 76px ui-monospace, monospace",
        secondaryFont: "800 76px ui-monospace, monospace",
        lineY: [64],
        worldWidth: relation ? 7.2 : 4.2,
        worldHeight: relation ? 7.2 : 4.2,
      },
      three,
      textures,
      spriteMaterials,
    });
    mark.position.set(
      0,
      0,
      (relation ? relationGeometry.radius : componentGeometry.radius) + 0.3,
    );
    presentationGroup.add(mark);
    if (relation) group.scale.setScalar(description.sizeScale);

    records.set(description.nodeId, {
      body,
      description,
      group,
      hitTarget,
      mark,
      outline,
      presentation: {
        bodyOpacity: bodyOpacityFor("idle"),
        outlineContrast: outlineContrastFor("idle"),
        outlineOpacity: outlineOpacityFor(description.tier, "idle"),
      },
      presentationGroup,
      rim,
    });
    return group;
  }

  function spriteOpacityFor(emphasis) {
    return {
      selected: 1,
      adjacent: 0.96,
      contextual: 0.84,
      unrelated: 0.88,
      idle: 1,
    }[emphasis] ?? 1;
  }

  function createPresentationTransition(
    interactionModel,
    presentation = {},
    { appearance = "dark" } = {},
  ) {
    const focusNodeId = typeof presentation.focusNodeId === "string"
      ? presentation.focusNodeId
      : null;
    let currentFocusNodeId = null;
    records.forEach((record, id) => {
      if (record.rim.visible || record.presentationGroup.scale.x > 1) {
        currentFocusNodeId = id;
      }
    });
    const acquiringFocus = currentFocusNodeId === null && focusNodeId !== null;
    const clearingFocus = currentFocusNodeId !== null && focusNodeId === null;
    const holdingFocus = currentFocusNodeId !== null && currentFocusNodeId === focusNodeId;
    const retargetingFocus = currentFocusNodeId !== null
      && focusNodeId !== null
      && currentFocusNodeId !== focusNodeId;
    const collapsedRimScale = geometryPolicy.outlineShellScale;
    const snapshots = [];
    records.forEach((record, id) => {
      const emphasis = graphNodeEmphasisFor(interactionModel, id);
      const primaryFocus = focusNodeId === id;
      const outgoingFocus = currentFocusNodeId === id && !holdingFocus;
      const idleBody = bodyMaterial(appearance, record.description.tier);
      const idleOutline = outlineMaterial(appearance, record.description.tier);

      snapshots.push({
        outgoingFocus,
        primaryFocus,
        record,
        startBodyMaterial: record.body.material,
        startBodyOpacity: record.presentation.bodyOpacity,
        startMarkOpacity: record.mark.material.opacity,
        startOutlineContrast: record.presentation.outlineContrast,
        startOutlineMaterial: record.outline.material,
        startOutlineOpacity: record.presentation.outlineOpacity,
        startRimOpacity: record.rim.material.opacity,
        startRimMaterial: record.rim.material,
        startRimScale: record.rim.scale.x,
        startRimVisible: record.rim.visible,
        startScale: record.presentationGroup.scale.x,
        targetBodyMaterial: bodyMaterial(appearance, record.description.tier, emphasis),
        targetBodyOpacity: bodyOpacityFor(emphasis),
        targetOutlineContrast: primaryFocus ? 0 : outlineContrastFor(emphasis),
        targetOutlineMaterial: primaryFocus
          ? idleOutline
          : outlineMaterial(appearance, record.description.tier, emphasis),
        targetOutlineOpacity: primaryFocus
          ? outlineOpacityFor(record.description.tier, "idle")
          : outlineOpacityFor(record.description.tier, emphasis),
        targetEmphasis: emphasis,
        targetMarkOpacity: spriteOpacityFor(emphasis),
        targetRimMaterial: rimMaterial(appearance),
        targetScale: primaryFocus ? geometryPolicy.focusPresentationScale : 1,
      });
      record.body.userData.harnesskitIdleMaterial = idleBody;
      record.outline.userData.harnesskitIdleOutlineMaterial = idleOutline;
    });

    return (progress) => {
      const normalized = Math.min(1, Math.max(0, Number(progress) || 0));
      const firstPhaseProgress = Math.min(1, normalized * 2);
      const secondPhaseProgress = Math.max(0, (normalized - 0.5) * 2);
      for (let index = 0; index < snapshots.length; index += 1) {
        const snapshot = snapshots[index];
        const { record } = snapshot;
        const bodyOpacity = interpolated(
          snapshot.startBodyOpacity,
          snapshot.targetBodyOpacity,
          normalized,
        );
        const stagedIncomingFocus = snapshot.primaryFocus
          && (acquiringFocus || retargetingFocus);
        const outlineProgress = stagedIncomingFocus ? firstPhaseProgress : normalized;
        const outlineOpacity = interpolated(
          snapshot.startOutlineOpacity,
          snapshot.targetOutlineOpacity,
          outlineProgress,
        );
        const outlineContrast = interpolated(
          snapshot.startOutlineContrast,
          snapshot.targetOutlineContrast,
          outlineProgress,
        );
        record.body.material = normalized === 0
          ? snapshot.startBodyMaterial
          : normalized === 1
            ? snapshot.targetBodyMaterial
            : transitionBodyMaterial(appearance, record.description.tier, bodyOpacity);
        record.outline.material = outlineProgress === 0
          ? snapshot.startOutlineMaterial
          : outlineProgress === 1
            ? snapshot.targetOutlineMaterial
            : transitionOutlineMaterial(
              appearance,
              record.description.tier,
              outlineOpacity,
              outlineContrast,
        );
        record.presentation.bodyOpacity = bodyOpacity;
        record.presentation.outlineContrast = outlineContrast;
        record.presentation.outlineOpacity = outlineOpacity;
        record.group.userData.harnesskitEmphasis = snapshot.targetEmphasis;

        if (retargetingFocus && snapshot.outgoingFocus) {
          const rimOpacity = interpolated(
            snapshot.startRimOpacity,
            0,
            firstPhaseProgress,
          );
          record.rim.material = normalized === 0
            ? snapshot.startRimMaterial
            : transitionRimMaterial(appearance, rimOpacity);
          record.rim.visible = normalized < 0.5 && snapshot.startRimVisible;
          record.rim.scale.setScalar(normalized < 0.5
            ? interpolated(snapshot.startRimScale, collapsedRimScale, firstPhaseProgress)
            : collapsedRimScale);
          record.presentationGroup.scale.setScalar(normalized < 0.5
            ? interpolated(snapshot.startScale, 1, firstPhaseProgress)
            : 1);
        } else if (retargetingFocus && snapshot.primaryFocus) {
          record.rim.material = normalized === 1
            ? snapshot.targetRimMaterial
            : transitionRimMaterial(appearance, secondPhaseProgress);
          record.rim.visible = normalized > 0.5;
          record.rim.scale.setScalar(normalized < 0.5
            ? collapsedRimScale
            : interpolated(
              collapsedRimScale,
              geometryPolicy.focusRimScale,
              secondPhaseProgress,
            ));
          record.presentationGroup.scale.setScalar(normalized < 0.5
            ? 1
            : interpolated(1, snapshot.targetScale, secondPhaseProgress));
        } else if (acquiringFocus && snapshot.primaryFocus) {
          record.rim.material = normalized === 1
            ? snapshot.targetRimMaterial
            : transitionRimMaterial(appearance, secondPhaseProgress);
          record.rim.visible = normalized > 0.5;
          record.rim.scale.setScalar(interpolated(
            collapsedRimScale,
            geometryPolicy.focusRimScale,
            secondPhaseProgress,
          ));
          record.presentationGroup.scale.setScalar(interpolated(
            1,
            snapshot.targetScale,
            secondPhaseProgress,
          ));
        } else if (clearingFocus && snapshot.outgoingFocus) {
          const rimOpacity = interpolated(snapshot.startRimOpacity, 0, normalized);
          record.rim.material = normalized === 0
            ? snapshot.startRimMaterial
            : transitionRimMaterial(appearance, rimOpacity);
          record.rim.visible = normalized < 1 && snapshot.startRimVisible;
          record.rim.scale.setScalar(interpolated(
            snapshot.startRimScale,
            collapsedRimScale,
            normalized,
          ));
          record.presentationGroup.scale.setScalar(interpolated(
            snapshot.startScale,
            1,
            normalized,
          ));
        } else if (holdingFocus && snapshot.primaryFocus) {
          const rimOpacity = interpolated(snapshot.startRimOpacity, 1, normalized);
          let frameRimMaterial = transitionRimMaterial(appearance, rimOpacity);
          if (normalized === 0) frameRimMaterial = snapshot.startRimMaterial;
          if (normalized === 1) frameRimMaterial = snapshot.targetRimMaterial;
          record.rim.material = frameRimMaterial;
          record.rim.visible = snapshot.startRimVisible || normalized > 0;
          record.rim.scale.setScalar(interpolated(
            snapshot.startRimScale,
            geometryPolicy.focusRimScale,
            normalized,
          ));
          record.presentationGroup.scale.setScalar(interpolated(
            snapshot.startScale,
            snapshot.targetScale,
            normalized,
          ));
        } else {
          record.rim.material = snapshot.targetRimMaterial;
          record.rim.visible = false;
          record.rim.scale.setScalar(collapsedRimScale);
          record.presentationGroup.scale.setScalar(1);
        }
        record.mark.material.opacity = interpolated(
          snapshot.startMarkOpacity,
          snapshot.targetMarkOpacity,
          normalized,
        );
      }
    };
  }

  function syncAll(interactionModel, presentation = {}, options = {}) {
    createPresentationTransition(interactionModel, presentation, options)(1);
  }

  function dispose() {
    if (disposed) return;
    records.clear();
    Object.values(geometries).forEach((geometry) => geometry.dispose());
    bodyMaterials.forEach((material) => material.dispose());
    outlineMaterials.forEach((material) => material.dispose());
    transitionBodyMaterials.forEach((material) => material.dispose());
    transitionOutlineMaterials.forEach((material) => material.dispose());
    transitionRimMaterials.forEach((materials) => {
      materials.forEach((material) => material.dispose());
    });
    rimMaterials.forEach((material) => material.dispose());
    hitTargetMaterial.dispose();
    spriteMaterials.forEach((material) => material.dispose());
    textures.forEach((texture) => texture.dispose());
    disposed = true;
  }

  return { create, createPresentationTransition, dispose, syncAll };
}
