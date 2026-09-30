import { graphProjectionIdentity } from "./spatial-policy.js";

const SCENE_PROJECTION_ID_PATTERN = /^[A-Za-z0-9_.:-]{1,200}$/;
const SETTLED_NODE_POSITIONS_HASH_PATTERN = /^[0-9a-f]{16}$/;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function identifierToken(value) {
  const text = String(value ?? "");
  const wellFormed = typeof text.toWellFormed === "function" ? text.toWellFormed() : text;
  return encodeURIComponent(wellFormed);
}

function nodes(projection) {
  return Array.isArray(projection?.nodes) ? projection.nodes : [];
}

function links(projection) {
  return Array.isArray(projection?.links) ? projection.links : [];
}

function validatedSceneLayoutIdentity(sceneLayoutIdentity, projection) {
  const projectionId = String(sceneLayoutIdentity?.projectionId ?? "");
  const settledNodePositionsHash = String(
    sceneLayoutIdentity?.settledNodePositionsHash ?? "",
  );
  if (projectionId !== graphProjectionIdentity(projection)
    || !SCENE_PROJECTION_ID_PATTERN.test(projectionId)
    || !SETTLED_NODE_POSITIONS_HASH_PATTERN.test(settledNodePositionsHash)) {
    return null;
  }
  return { projectionId, settledNodePositionsHash };
}

function humanizeIdentifier(value, fallback = "Unknown") {
  const source = String(value ?? "").trim();
  if (!source) return fallback;
  const leaf = source.split(/[.:/]/).filter(Boolean).at(-1) ?? source;
  const words = leaf.replaceAll(/[-_]+/g, " ").replaceAll(/\s+/g, " ").trim();
  if (!words) return fallback;
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
}

function kindValue(value, componentId = "") {
  const explicit = String(value ?? "").trim().toLowerCase();
  if (explicit) return explicit;
  const inferred = String(componentId).split(".").at(-2);
  return inferred && inferred !== "harnesskit" ? inferred.toLowerCase() : "component";
}

function kindLabel(value) {
  return String(value || "component").toUpperCase();
}

function componentPresentation(componentId, componentNodesById, componentCatalogById) {
  const node = componentNodesById.get(componentId);
  const catalog = componentCatalogById.get(componentId);
  const kind = kindValue(catalog?.kind ?? node?.kind, componentId);
  const degree = Math.max(0, Number(node?.relation_degree) || 0);
  return {
    degree,
    kind,
    kindLabel: kindLabel(kind),
    title: String(catalog?.title ?? node?.title ?? "").trim()
      || humanizeIdentifier(componentId, "Component"),
  };
}

function relationMemberIds(relation, projectionLinks) {
  const values = [];
  projectionLinks.forEach((link) => {
    if (link.profile_node_id === relation.node_id || link.workflow_node_id === relation.node_id) {
      if (link.component_node_id) values.push(link.component_node_id);
    }
  });
  return [...new Set(values)].sort();
}

function relationSelected(relation, presentation, projectionLinks) {
  if (presentation?.selectedRelationNodeId === relation.node_id
    || (
      String(relation.relation_kind).toLowerCase() === "workflow"
      && presentation?.selectedWorkflowId === relation.canonical_id
    )) return true;
  const relationKind = String(relation.relation_kind).toLowerCase();
  const selectedComponentNodeId = presentation?.selectedComponentId
    ? `component:${presentation.selectedComponentId}`
    : null;
  return Boolean(
    selectedComponentNodeId
    && ["profile", "unprofiled"].includes(relationKind)
    && projectionLinks.some((link) => (
      link.semantic === "profile-membership"
      && link.profile_node_id === relation.node_id
      && link.component_node_id === selectedComponentNodeId
    )),
  );
}

function relationCountLabel(relation) {
  const count = Number(relation?.exact_count) || 0;
  const kind = String(relation?.relation_kind ?? "relation").toLowerCase();
  const unit = kind === "workflow"
    ? `authored step${count === 1 ? "" : "s"}`
    : `component${count === 1 ? "" : "s"}`;
  return `${count} ${unit}`;
}

function fitReportAxLabel(report) {
  const counts = [
    report?.identityCount,
    report?.relationCount,
    report?.componentCount,
    report?.envelopeCount,
    report?.camera?.inFrustumEnvelopeCount,
    report?.camera?.totalEnvelopeCount,
  ];
  const bounds = [
    report?.sceneBounds?.min?.x,
    report?.sceneBounds?.min?.y,
    report?.sceneBounds?.min?.z,
    report?.sceneBounds?.max?.x,
    report?.sceneBounds?.max?.y,
    report?.sceneBounds?.max?.z,
  ];
  const cameraStatus = report?.camera?.status;
  const largestDimensionOccupancy = report?.camera?.largestDimensionOccupancy;
  if (!counts.every((value) => Number.isInteger(value) && value >= 0)
    || !bounds.every(Number.isFinite)
    || !["complete", "cropped"].includes(cameraStatus)
    || !Number.isFinite(largestDimensionOccupancy)) return null;
  const [minX, minY, minZ, maxX, maxY, maxZ] = bounds;
  return [
    "Component Map Fit report",
    `identity count=${report.identityCount}`,
    `relation count=${report.relationCount}`,
    `component count=${report.componentCount}`,
    `envelope count=${report.envelopeCount}`,
    `camera status=${cameraStatus}`,
    `in-frustum=${report.camera.inFrustumEnvelopeCount}`,
    `total envelopes=${report.camera.totalEnvelopeCount}`,
    `largest dimension occupancy=${largestDimensionOccupancy.toFixed(6)}`,
    `bounds min=${minX.toFixed(6)},${minY.toFixed(6)},${minZ.toFixed(6)}`,
    `bounds max=${maxX.toFixed(6)},${maxY.toFixed(6)},${maxZ.toFixed(6)}`,
  ].join("; ");
}

function cameraPoseAxLabel(pose) {
  const values = [
    pose?.position?.x,
    pose?.position?.y,
    pose?.position?.z,
    pose?.target?.x,
    pose?.target?.y,
    pose?.target?.z,
  ];
  if (!values.every(Number.isFinite)) return null;
  const [positionX, positionY, positionZ, targetX, targetY, targetZ] = values;
  return [
    "Camera pose",
    `position=${positionX.toFixed(6)},${positionY.toFixed(6)},${positionZ.toFixed(6)}`,
    `target=${targetX.toFixed(6)},${targetY.toFixed(6)},${targetZ.toFixed(6)}`,
  ].join("; ");
}

function workflowStepGroups(relation, projectionLinks) {
  const groups = new Map();
  projectionLinks
    .filter((link) => link.semantic === "workflow-step" && link.workflow_node_id === relation.node_id)
    .forEach((link) => {
      const componentId = String(link.component_node_id ?? "").replace(/^component:/, "");
      (link.occurrences ?? []).forEach((occurrence) => {
        const ordinal = Number(occurrence.ordinal);
        if (!Number.isInteger(ordinal) || ordinal < 1) return;
        const stepId = String(occurrence.step_id ?? "").trim() || `ordinal-${ordinal}`;
        const key = `${ordinal}\u0000${stepId}`;
        if (!groups.has(key)) {
          groups.set(key, {
            ordinal,
            occurrences: new Map(),
            stepId,
          });
        }
        const sourceField = String(occurrence.source_field ?? "").trim();
        groups.get(key).occurrences.set(`${componentId}\u0000${sourceField}`, {
          componentId,
          sourceField,
        });
      });
    });
  return [...groups.values()]
    .map((group) => ({
      ...group,
      occurrences: [...group.occurrences.values()].sort((left, right) => (
        left.componentId.localeCompare(right.componentId)
        || left.sourceField.localeCompare(right.sourceField)
      )),
    }))
    .sort((left, right) => (
      left.ordinal - right.ordinal || left.stepId.localeCompare(right.stepId)
    ));
}

function renderWorkflowSteps(
  relation,
  projectionLinks,
  presentation,
  componentNodesById,
  componentCatalogById,
) {
  if (String(relation.relation_kind).toLowerCase() !== "workflow") return "";
  const groups = workflowStepGroups(relation, projectionLinks);
  if (groups.length === 0) return "";
  return `<ol class="semantic-graph__steps" aria-label="Workflow steps">
${groups.map((group) => {
    const selected = presentation?.lockedWorkflowStep?.workflowId === relation.canonical_id
      && Number(presentation.lockedWorkflowStep.ordinal) === group.ordinal;
    const stepToken = identifierToken(group.stepId);
    const controlId = `semantic-workflow-step:${relation.canonical_id}:${group.ordinal}:${stepToken}`;
    const stepTitle = humanizeIdentifier(group.stepId, `Step ${group.ordinal}`);
    const occurrences = group.occurrences.map(({ componentId, sourceField }) => ({
      componentId,
      presentation: componentPresentation(
        componentId,
        componentNodesById,
        componentCatalogById,
      ),
      sourceField,
    }));
    const stepKinds = [...new Set(occurrences.map(({ presentation: value }) => value.kindLabel))];
    const controlLabel = [
      `Step ${group.ordinal} ${stepTitle}`,
      ...stepKinds.map((value) => value.charAt(0) + value.slice(1).toLowerCase()),
    ].join(", ");
    return `<li><button type="button"
id="${escapeHtml(controlId)}"
aria-label="${escapeHtml(controlLabel)}"
data-workflow-id="${escapeHtml(relation.canonical_id)}"
data-semantic-workflow-step="${escapeHtml(group.ordinal)}"
data-semantic-workflow-step-id="${escapeHtml(stepToken)}"
aria-pressed="${selected}">
<span>${escapeHtml(group.ordinal)}.</span>
<strong>${escapeHtml(stepTitle)}</strong>
${stepKinds.map((value) => `<span class="component-kind-badge">${escapeHtml(value)}</span>`).join("")}
</button>
<ul class="sr-only" aria-label="${escapeHtml(stepTitle)} components">${occurrences.map(({ componentId, presentation: value, sourceField }) => (
    `<li id="semantic-workflow-occurrence:${escapeHtml(relation.canonical_id)}:${escapeHtml(group.ordinal)}:${escapeHtml(componentId)}|${escapeHtml(stepToken)}|${escapeHtml(identifierToken(sourceField))}"><span class="component-kind-badge">${escapeHtml(value.kindLabel)}</span> <span>${escapeHtml(value.title)}</span></li>`
  )).join("")}</ul>
</li>`;
  }).join("\n")}
</ol>`;
}

export function renderSemanticGraphFallback({
  projection,
  componentCatalog = [],
  presentation = {},
  rendererState = {},
} = {}) {
  const projectionNodes = nodes(projection);
  const projectionLinks = links(projection);
  const relations = projectionNodes.filter((node) => node.node_type === "relation");
  const components = projectionNodes.filter((node) => node.node_type === "component");
  const componentNodesById = new Map(components.map((component) => (
    [component.component_id, component]
  )));
  const componentCatalogById = new Map(
    (Array.isArray(componentCatalog) ? componentCatalog : [])
      .filter((component) => component?.component_id)
      .map((component) => [component.component_id, component]),
  );
  const availability = rendererState.availability ?? "unavailable";
  const presentationMode = rendererState.presentationMode ?? "failure";
  const pending = availability === "pending";
  const reason = availability === "ready"
    ? null
    : rendererState.reason ?? "WebGL renderer를 시작할 수 없습니다.";
  const showReturn = presentationMode === "manual";
  const showRetry = !showReturn && !pending && availability !== "ready";
  const semanticTitle = showReturn
    ? "Component Map 텍스트 보기"
    : availability === "ready"
    ? "Component Map 키보드 탐색 목록"
    : pending
      ? "Component Map 준비 중"
      : "3D Component Map 대체 목록";
  const fitReport = rendererState.fitReport;
  const fitReportAxText = fitReportAxLabel(fitReport);
  const cameraPoseAxText = cameraPoseAxLabel(rendererState.cameraPose);
  const sceneLayoutIdentity = validatedSceneLayoutIdentity(
    rendererState.sceneLayoutIdentity,
    projection,
  );
  const activeProfileId = String(presentation.activeProfileId ?? "").trim();
  const identityCount = fitReport?.identityCount;
  const inFrustumCount = fitReport?.camera?.inFrustumEnvelopeCount;
  const totalEnvelopeCount = fitReport?.camera?.totalEnvelopeCount;
  const largestDimensionOccupancy = fitReport?.camera?.largestDimensionOccupancy;
  const fitReportText = Number.isInteger(identityCount) && identityCount >= 0
    ? [
      `Component Map Fit: identity ${identityCount}개`,
      Number.isInteger(inFrustumCount) && Number.isInteger(totalEnvelopeCount)
        ? `camera envelope ${inFrustumCount}/${totalEnvelopeCount}`
        : "camera coverage 계산 불가",
      Number.isFinite(largestDimensionOccupancy)
        ? `최대 축 사용 ${Math.round(largestDimensionOccupancy * 100)}%`
        : null,
    ].filter(Boolean).join(" · ")
    : null;

  return `<section id="component-map-semantic-view" class="semantic-graph" role="region" tabindex="-1" aria-busy="${pending}" aria-label="Component Map semantic view">
<header class="semantic-graph__status">
<strong>${semanticTitle}</strong>
${reason ? `<span role="status">${escapeHtml(reason)}</span>` : ""}
${showReturn ? '<button id="component-map-renderer-return" type="button" aria-label="3D 그래프로 돌아가기" data-graph-return>3D 그래프로 돌아가기</button>' : ""}
${showRetry ? '<button id="component-map-renderer-retry" type="button" aria-label="Component Map renderer retry" data-graph-retry>3D 다시 시도</button>' : ""}
</header>
${fitReportText ? `<p${fitReportAxText ? ` id="component-map-fit-report" aria-label="${escapeHtml(fitReportAxText)}"` : ""} class="sr-only" data-graph-fit-report>${escapeHtml(fitReportText)}</p>` : ""}
<p id="component-map-camera-pose"${cameraPoseAxText
  ? ` aria-label="${escapeHtml(cameraPoseAxText)}" aria-hidden="false"`
  : ' aria-hidden="true"'} class="sr-only" data-graph-camera-pose>Camera pose</p>
${sceneLayoutIdentity ? `<p id="component-map-projection:${escapeHtml(identifierToken(sceneLayoutIdentity.projectionId))}" aria-label="Graph projection identity" class="sr-only" data-graph-projection-id>Graph projection identity</p>
<p id="component-map-settled-hash:${escapeHtml(sceneLayoutIdentity.settledNodePositionsHash)}" aria-label="Graph settled positions identity" class="sr-only" data-graph-settled-node-positions-hash>Graph settled positions identity</p>` : ""}
${activeProfileId ? `<p id="component-map-active-profile:${escapeHtml(identifierToken(activeProfileId))}" aria-label="Active profile" class="sr-only" data-graph-active-profile>Active profile</p>` : ""}
<div class="semantic-graph__relations">
${relations.map((relation) => {
    const memberIds = relationMemberIds(relation, projectionLinks);
    const relationKind = String(relation.relation_kind ?? "relation").toLowerCase();
    const relationKindLabel = kindLabel(relationKind);
    const spokenRelationKind = humanizeIdentifier(relationKind, "Relation");
    const relationName = String(relation.name ?? "").trim()
      || humanizeIdentifier(relation.canonical_id ?? relation.node_id, "Relation");
    const relationLabel = `${relationName}, ${spokenRelationKind} relation, ${relationCountLabel(relation)}`;
    return `<article id="semantic-relation-group:${escapeHtml(relation.node_id)}" role="group" aria-label="${escapeHtml(`${spokenRelationKind} relation ${relationName}, ${relationCountLabel(relation)}`)}" data-semantic-relation-kind="${escapeHtml(relationKind)}">
<button type="button"
id="semantic-relation:${escapeHtml(relation.node_id)}"
aria-label="${escapeHtml(relationLabel)}"
data-semantic-node-id="${escapeHtml(relation.node_id)}"
aria-pressed="${relationSelected(relation, presentation, projectionLinks)}">
<strong>${escapeHtml(relationName)}</strong>
<span class="component-kind-badge">${escapeHtml(relationKindLabel)}</span>
<span>${escapeHtml(relationCountLabel(relation))}</span>
</button>
<ul id="semantic-relation-members:${escapeHtml(relation.node_id)}" aria-label="${escapeHtml(relationName)} connected components">${memberIds.map((id) => {
      const componentId = id.replace(/^component:/, "");
      const value = componentPresentation(componentId, componentNodesById, componentCatalogById);
      return `<li id="semantic-relation-member:${escapeHtml(relation.node_id)}:${escapeHtml(componentId)}"><span class="component-kind-badge">${escapeHtml(value.kindLabel)}</span> <span>${escapeHtml(value.title)}</span></li>`;
    }).join("")}</ul>
${renderWorkflowSteps(
      relation,
      projectionLinks,
      presentation,
      componentNodesById,
      componentCatalogById,
    )}
</article>`;
  }).join("\n")}
</div>
<ul class="semantic-graph__components" aria-label="Component entities">
${components.map((component) => {
    const selected = presentation.selectedComponentId === component.component_id;
    const value = componentPresentation(
      component.component_id,
      componentNodesById,
      componentCatalogById,
    );
    const spokenKind = value.kindLabel.charAt(0) + value.kindLabel.slice(1).toLowerCase();
    const degreeLabel = `${value.degree} ${value.degree === 1 ? "relation" : "relations"}`;
    return `<li><button type="button"
id="semantic-component:${escapeHtml(component.component_id)}"
aria-label="${escapeHtml(`${value.title}, ${spokenKind}, ${degreeLabel}`)}"
data-semantic-node-id="${escapeHtml(component.node_id)}"
aria-pressed="${selected}">
<strong>${escapeHtml(value.title)}</strong>
<span class="component-kind-badge">${escapeHtml(value.kindLabel)}</span>
<span>${escapeHtml(degreeLabel)}</span>
</button></li>`;
  }).join("\n")}
</ul>
</section>`;
}

export function bindSemanticGraphFallback(root, {
  projection,
  componentCatalog = [],
  getPresentation = () => ({}),
  getRendererState = () => ({}),
  onNodeHover = () => {},
  onNodeSelect = () => {},
  onWorkflowStepHover = () => {},
  onWorkflowStepSelect = () => {},
  onRetry = () => {},
  onReturn = () => {},
  onBackgroundSelect = () => {},
} = {}) {
  if (!root || typeof root.addEventListener !== "function") {
    throw new TypeError("semantic fallback root is required");
  }
  const projectionNodes = nodes(projection);
  const nodeById = new Map(projectionNodes.map((node) => [node.node_id, node]));
  let suppressInteraction = false;

  function focusedIdentity() {
    const active = root.ownerDocument?.activeElement;
    if (!active || (typeof root.contains === "function" && !root.contains(active))) return null;
    if (active.dataset?.semanticWorkflowStep && active.dataset?.workflowId) {
      return {
        semanticWorkflowStep: active.dataset.semanticWorkflowStep,
        semanticWorkflowStepId: active.dataset.semanticWorkflowStepId ?? "",
        workflowId: active.dataset.workflowId,
      };
    }
    if (active.dataset?.semanticNodeId) {
      return { semanticNodeId: active.dataset.semanticNodeId };
    }
    return null;
  }

  function restoreFocusedIdentity(identity) {
    if (!identity) return;
    const candidates = [...(root.querySelectorAll?.(
      "[data-semantic-node-id], [data-semantic-workflow-step]",
    ) ?? [])];
    const target = candidates.find((candidate) => Object.entries(identity)
      .every(([key, value]) => candidate.dataset?.[key] === value));
    target?.focus?.({ preventScroll: true });
  }

  function render() {
    const identity = focusedIdentity();
    const scrollTop = Number(root.scrollTop);
    suppressInteraction = true;
    try {
      root.innerHTML = renderSemanticGraphFallback({
        projection,
        componentCatalog,
        presentation: getPresentation(),
        rendererState: getRendererState(),
      });
      restoreFocusedIdentity(identity);
      if (Number.isFinite(scrollTop) && scrollTop >= 0) root.scrollTop = scrollTop;
    } finally {
      suppressInteraction = false;
    }
  }

  function syncCameraPose(pose) {
    const target = root.querySelector?.("[data-graph-camera-pose]");
    if (!target) return false;
    const label = cameraPoseAxLabel(pose);
    if (!label) {
      target.removeAttribute?.("aria-label");
      target.setAttribute?.("aria-hidden", "true");
      return true;
    }
    target.setAttribute?.("aria-label", label);
    target.setAttribute?.("aria-hidden", "false");
    return true;
  }

  function nodeTarget(event) {
    return event.target?.closest?.("[data-semantic-node-id]") ?? null;
  }

  function workflowStepTarget(event) {
    return event.target?.closest?.("[data-semantic-workflow-step]") ?? null;
  }

  function workflowStepValue(target) {
    const ordinal = Number(target?.dataset?.semanticWorkflowStep);
    if (!target?.dataset?.workflowId || !Number.isInteger(ordinal) || ordinal < 1) return null;
    return { workflowId: target.dataset.workflowId, ordinal };
  }

  function handleClick(event) {
    if (event.target?.closest?.("[data-graph-return]")) {
      onReturn();
      return;
    }
    if (event.target?.closest?.("[data-graph-retry]")) {
      onRetry();
      return;
    }
    const step = workflowStepTarget(event);
    if (step) {
      const value = workflowStepValue(step);
      if (value) onWorkflowStepSelect(value);
      return;
    }
    const target = nodeTarget(event);
    const node = nodeById.get(target?.dataset?.semanticNodeId);
    if (node) onNodeSelect(node);
  }

  function handleHover(event) {
    if (suppressInteraction) return;
    const step = workflowStepValue(workflowStepTarget(event));
    if (step) {
      onWorkflowStepHover(step);
      return;
    }
    const target = nodeTarget(event);
    if (target?.dataset?.semanticNodeId) onNodeHover(target.dataset.semanticNodeId);
  }

  function handleLeave(event) {
    if (suppressInteraction) return;
    if (workflowStepTarget(event)) {
      onWorkflowStepHover(null);
      return;
    }
    const target = nodeTarget(event);
    if (target?.dataset?.semanticNodeId) onNodeHover(null);
  }

  function interactionBoundary(event) {
    const step = workflowStepTarget(event);
    if (step) return { selector: "[data-semantic-workflow-step]", target: step };
    const node = nodeTarget(event);
    return node ? { selector: "[data-semantic-node-id]", target: node } : null;
  }

  function relatedTargetStaysInside(event, boundary) {
    const relatedTarget = event.relatedTarget;
    if (!boundary || !relatedTarget) return false;
    if (boundary.target === relatedTarget || boundary.target.contains?.(relatedTarget)) return true;
    return relatedTarget.closest?.(boundary.selector) === boundary.target;
  }

  function handleMouseOver(event) {
    if (relatedTargetStaysInside(event, interactionBoundary(event))) return;
    handleHover(event);
  }

  function handleMouseOut(event) {
    if (relatedTargetStaysInside(event, interactionBoundary(event))) return;
    handleLeave(event);
  }

  function handleKeydown(event) {
    if (event.key !== "Escape") return;
    event.preventDefault();
    onBackgroundSelect();
  }

  const listeners = {
    click: handleClick,
    focusin: handleHover,
    focusout: handleLeave,
    keydown: handleKeydown,
    mouseout: handleMouseOut,
    mouseover: handleMouseOver,
  };
  Object.entries(listeners).forEach(([type, listener]) => root.addEventListener(type, listener));
  render();

  return {
    render,
    syncCameraPose,
    release() {
      Object.entries(listeners).forEach(([type, listener]) => root.removeEventListener(type, listener));
    },
  };
}
