function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function integerOrdinal(value) {
  const ordinal = Number.parseInt(String(value), 10);
  return Number.isInteger(ordinal) && ordinal > 0 ? ordinal : null;
}

function encodeIdentitySegment(value) {
  const text = String(value ?? "").trim();
  const wellFormed = typeof text.toWellFormed === "function" ? text.toWellFormed() : text;
  return encodeURIComponent(wellFormed);
}

const HARNESS_KIND_LABELS = new Map([
  ["agent", "AGENT"],
  ["skill", "SKILL"],
  ["workflow", "WORKFLOW"],
  ["hook", "HOOK"],
  ["rule", "RULE"],
  ["command", "COMMAND"],
  ["prompt", "PROMPT"],
  ["mcp", "MCP"],
]);

const HARNESS_KIND_ORDER = [...HARNESS_KIND_LABELS.keys()];

function supportedKind(value) {
  const kind = String(value ?? "").trim().toLowerCase();
  return HARNESS_KIND_LABELS.has(kind) ? kind : null;
}

function componentKindIndex(components) {
  return new Map((Array.isArray(components) ? components : [])
    .map((component) => [
      String(component?.component_id ?? ""),
      supportedKind(component?.kind),
    ])
    .filter(([componentId, kind]) => componentId && kind));
}

function stepKindLabels(step, componentKinds) {
  const kinds = new Set();
  (step.resolved_component_ids ?? []).forEach((componentId) => {
    const kind = componentKinds.get(String(componentId ?? ""));
    if (kind) kinds.add(kind);
  });
  (step.unresolved_references ?? []).forEach((reference) => {
    const kind = supportedKind(reference?.reference_kind);
    if (kind) kinds.add(kind);
  });
  return HARNESS_KIND_ORDER
    .filter((kind) => kinds.has(kind))
    .map((kind) => ({ kind, label: HARNESS_KIND_LABELS.get(kind) }));
}

function renderKindBadges(kinds) {
  if (kinds.length === 0) return "";
  return `<span class="workflow-step__kinds">${kinds
    .map(({ kind, label }) => `<span class="component-kind-badge" data-workflow-step-kind="${escapeHtml(kind)}">${escapeHtml(label)}</span>`)
    .join("")}</span>`;
}

function renderUnresolvedReferences(workflowId, step) {
  const references = Array.isArray(step.unresolved_references)
    ? step.unresolved_references
    : [];
  if (references.length === 0) return "";

  return `<ul class="workflow-step__warnings" aria-label="미해결 Workflow reference">${references
    .map((reference) => {
      const warningIdentity = [
        reference.reference_kind,
        reference.source_field,
        reference.reference,
      ].map(encodeIdentitySegment).join("|");
      return `<li id="workflow-warning:${escapeHtml(workflowId)}:${escapeHtml(step.ordinal)}:${escapeHtml(warningIdentity)}">
      <strong>연결할 Component를 찾지 못함</strong>
      <span>원본 정의는 기술 세부 정보에서 확인할 수 있습니다.</span>
    </li>`;
    })
    .join("")}</ul>`;
}

function renderStep(workflowId, step, lockedStepOrdinal, componentKinds) {
  const ordinal = integerOrdinal(step.ordinal);
  const isLocked = ordinal === integerOrdinal(lockedStepOrdinal);
  const title = step.title || step.step_id || `Step ${ordinal ?? "-"}`;
  const stepIdentity = step.step_id || `ordinal-${ordinal ?? "unknown"}`;
  const controlId = `workflow-inspector-step:${workflowId}:${ordinal ?? "unknown"}:${encodeIdentitySegment(stepIdentity)}`;
  const kinds = stepKindLabels(step, componentKinds);
  const kindLabel = kinds.map(({ label }) => label).join(" ");
  const controlLabel = `Workflow step ${ordinal ?? "unknown"} ${title}${kindLabel ? ` ${kindLabel}` : ""}`;

  return `<li class="workflow-step${isLocked ? " is-locked" : ""}">
    <button
      type="button"
      class="workflow-step__action"
      id="${escapeHtml(controlId)}"
      aria-label="${escapeHtml(controlLabel)}"
      data-workflow-id="${escapeHtml(workflowId)}"
      data-workflow-step="${escapeHtml(ordinal ?? "") }"
      aria-pressed="${isLocked ? "true" : "false"}"
    >
      <span class="workflow-step__ordinal">${escapeHtml(ordinal ?? "-")}</span>
      <span class="workflow-step__body">
        <strong id="typography-workflow-card-title:${escapeHtml(controlId)}">${escapeHtml(title)}</strong>
        ${renderKindBadges(kinds)}
        ${step.description ? `<span id="typography-workflow-card-body:${escapeHtml(controlId)}">${escapeHtml(step.description)}</span>` : ""}
      </span>
    </button>
    ${renderUnresolvedReferences(workflowId, step)}
  </li>`;
}

export function renderOrderedStepInspector(input) {
  const workflow = input?.workflow;
  if (!workflow) {
    return '<ol class="workflow-inspector__steps is-empty" aria-label="선택된 Workflow의 정의된 순서 · 0 steps"></ol>';
  }

  const workflowId = workflow.workflow_id ?? "";
  const steps = Array.isArray(workflow.steps) ? workflow.steps : [];
  const componentKinds = componentKindIndex(input?.components);
  const workflowName = workflow.title || workflow.name || "선택한 Workflow";

  return `<ol class="workflow-inspector__steps" aria-label="${escapeHtml(workflowName)}의 정의된 순서 · ${steps.length} steps">
    ${steps.map((step) => renderStep(workflowId, step, input.lockedStepOrdinal, componentKinds)).join("")}
  </ol>`;
}

function closestDatasetTarget(event, selector) {
  return event?.target?.closest?.(selector) ?? null;
}

function remainsWithinTarget(event, target, selector) {
  return Boolean(target)
    && event?.relatedTarget?.closest?.(selector) === target;
}

export function bindOrderedStepInspector(root, callbacks = {}) {
  if (!root || typeof root.addEventListener !== "function") {
    throw new TypeError("root must support DOM event listeners");
  }

  let released = false;

  const pointerOver = (event) => {
    const target = closestDatasetTarget(event, "[data-workflow-step]");
    if (remainsWithinTarget(event, target, "[data-workflow-step]")) return;
    const ordinal = integerOrdinal(target?.dataset?.workflowStep);
    const workflowId = target?.dataset?.workflowId;
    if (!workflowId || ordinal === null) return;
    callbacks.onStepHover?.({ workflowId, ordinal });
  };

  const pointerOut = (event) => {
    const target = closestDatasetTarget(event, "[data-workflow-step]");
    if (remainsWithinTarget(event, target, "[data-workflow-step]")) return;
    if (target) callbacks.onStepHover?.(null);
  };

  const click = (event) => {
    const step = closestDatasetTarget(event, "[data-workflow-step]");
    const ordinal = integerOrdinal(step?.dataset?.workflowStep);
    const workflowId = step?.dataset?.workflowId;
    if (workflowId && ordinal !== null) {
      event.preventDefault?.();
      callbacks.onStepSelect?.({ workflowId, ordinal });
      return;
    }

    const overview = closestDatasetTarget(event, "[data-workflow-overview]");
    const overviewWorkflowId = overview?.dataset?.workflowOverview;
    if (overviewWorkflowId) {
      event.preventDefault?.();
      callbacks.onWorkflowOverview?.(overviewWorkflowId);
    }
  };

  root.addEventListener("pointerover", pointerOver);
  root.addEventListener("pointerout", pointerOut);
  root.addEventListener("focusin", pointerOver);
  root.addEventListener("focusout", pointerOut);
  root.addEventListener("click", click);

  return {
    sync(input) {
      if (released) return "";
      const html = renderOrderedStepInspector(input);
      root.innerHTML = html;
      return html;
    },
    release() {
      if (released) return;
      root.removeEventListener("pointerover", pointerOver);
      root.removeEventListener("pointerout", pointerOut);
      root.removeEventListener("focusin", pointerOver);
      root.removeEventListener("focusout", pointerOut);
      root.removeEventListener("click", click);
      released = true;
    },
  };
}
