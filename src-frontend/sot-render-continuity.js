const FOCUS_IDENTITIES = Object.freeze([
  ["semanticWorkflowStep", "workflowId"],
  ["workflowStep", "workflowId"],
  ["semanticNodeId"],
  ["graphNode"],
  ["workflowOverview"],
  ["componentId"],
  ["profileId"],
  ["sotTreeNode"],
]);

const FOCUS_SURFACES = Object.freeze([
  ["semantic-mirror", "[data-component-map-semantic-host]"],
  ["workflow-inspector", ".workflow-inspector"],
  ["sot-tree", "[data-sot-tree-scroll]"],
  ["profile-members", "#profile-member-region"],
  ["profile-matrix", "#profile-matrix-body"],
  ["component-map", "[data-component-map-body]"],
]);

function focusSurface(element) {
  return FOCUS_SURFACES.find(([, selector]) => element?.closest?.(selector))?.[0] ?? null;
}

function logicalFocusIdentity(element) {
  const dataset = element?.dataset;
  if (!dataset) return null;
  const fields = FOCUS_IDENTITIES.find((candidate) => candidate
    .every((field) => typeof dataset[field] === "string" && dataset[field].length > 0));
  if (!fields) return null;
  return Object.freeze({
    control: fields.join("+"),
    fields: Object.freeze(Object.fromEntries(fields.map((field) => [field, dataset[field]]))),
    surface: focusSurface(element),
  });
}

function nonNegativeScrollTop(element) {
  const scrollTop = Number(element?.scrollTop);
  return Number.isFinite(scrollTop) && scrollTop >= 0 ? scrollTop : 0;
}

function isOutsideVerticalViewport(target, viewport) {
  const targetRect = target?.getBoundingClientRect?.();
  const viewportRect = viewport?.getBoundingClientRect?.();
  if (!targetRect || !viewportRect) return false;
  const edges = [targetRect.top, targetRect.bottom, viewportRect.top, viewportRect.bottom];
  if (!edges.every(Number.isFinite)) return false;
  return targetRect.top < viewportRect.top || targetRect.bottom > viewportRect.bottom;
}

export function captureSotRenderContinuity(root, activeSegment) {
  if (activeSegment !== "sot") return null;
  const workbench = root?.querySelector?.("#workbench");
  if (!workbench) return null;
  const inspectorPane = root.querySelector?.("#workspace-right-pane");
  return Object.freeze({
    focusIdentity: logicalFocusIdentity(root.ownerDocument?.activeElement),
    inspectorScrollTop: nonNegativeScrollTop(inspectorPane),
    scrollTop: nonNegativeScrollTop(workbench),
  });
}

export function restoreSotRenderContinuity(root, continuity) {
  if (!continuity) return false;
  const workbench = root?.querySelector?.("#workbench");
  if (!workbench) return false;
  const inspectorPane = root.querySelector?.("#workspace-right-pane");
  if (inspectorPane) inspectorPane.scrollTop = continuity.inspectorScrollTop ?? 0;
  const identity = continuity.focusIdentity;
  if (identity) {
    const candidates = [...(root.querySelectorAll?.(
      "[data-semantic-workflow-step], [data-workflow-step], [data-semantic-node-id], [data-workflow-overview], [data-component-id], [data-profile-id], [data-sot-tree-node]",
    ) ?? [])];
    const fields = identity.fields ?? identity;
    const target = candidates.find((candidate) => (
      Object.entries(fields).every(([field, value]) => candidate.dataset?.[field] === value)
      && (!identity.surface || focusSurface(candidate) === identity.surface)
      && (!identity.control || logicalFocusIdentity(candidate)?.control === identity.control)
    ));
    target?.focus?.({ preventScroll: true });
    if (
      focusSurface(target) === "workflow-inspector"
      && isOutsideVerticalViewport(target, inspectorPane)
    ) {
      target.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    }
  }
  workbench.scrollTop = continuity.scrollTop;
  return true;
}
