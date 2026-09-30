export class SotWorkbenchDisclosureContractError extends Error {
  constructor(code) {
    super(code);
    this.name = "SotWorkbenchDisclosureContractError";
    this.code = code;
  }
}

function requireUnique(root, selector, code) {
  const matches = root?.querySelectorAll?.(selector);
  if (matches && matches.length !== 1) {
    throw new SotWorkbenchDisclosureContractError(code);
  }
  const element = matches?.[0] ?? root?.querySelector?.(selector);
  if (!element) throw new SotWorkbenchDisclosureContractError(code);
  return element;
}

function normalizeState(state) {
  if (typeof state?.componentMap !== "boolean" || typeof state?.profileMatrix !== "boolean") {
    throw new SotWorkbenchDisclosureContractError("invalid_disclosure_state");
  }
  return {
    componentMap: state.componentMap,
    profileMatrix: state.profileMatrix,
  };
}

function moveFocusBeforeHide(body, toggle, documentRoot) {
  const activeElement = documentRoot?.activeElement;
  if (activeElement && body?.contains?.(activeElement)) toggle?.focus?.();
}

function preserveOuterScroll(scrollOwner, patch) {
  if (!scrollOwner) {
    patch();
    return;
  }
  const previous = Number.isFinite(scrollOwner.scrollTop) ? scrollOwner.scrollTop : 0;
  patch();
  const maximum = Math.max(0, (scrollOwner.scrollHeight ?? 0) - (scrollOwner.clientHeight ?? 0));
  scrollOwner.scrollTop = Math.min(previous, maximum);
}

export function bindSotWorkbenchDisclosure(root, options = {}) {
  if (!root?.addEventListener || !root?.removeEventListener) {
    throw new SotWorkbenchDisclosureContractError("invalid_disclosure_root");
  }
  normalizeState(options.state);
  const graphController = options.graphController;
  if (typeof graphController?.setExpanded !== "function"
    || typeof graphController?.refreshAfterLayout !== "function") {
    throw new SotWorkbenchDisclosureContractError("invalid_graph_controller");
  }
  if (typeof options.onChange !== "function") {
    throw new SotWorkbenchDisclosureContractError("invalid_change_callback");
  }

  const workbench = root?.matches?.(".sot-workbench")
    ? root
    : root?.querySelector?.(".sot-workbench") ?? root;
  const mapToggle = requireUnique(root, "[data-sot-graph-toggle]", "invalid_map_toggle");
  const matrixToggle = requireUnique(root, "[data-sot-matrix-toggle]", "invalid_matrix_toggle");
  const mapBody = requireUnique(root, "#component-map-body", "invalid_map_body");
  const matrixBody = requireUnique(root, "#profile-matrix-body", "invalid_matrix_body");
  const map = root?.querySelector?.(".component-map");
  const scrollOwner = root?.matches?.(".pane--workbench")
    ? root
    : root?.querySelector?.(".pane--workbench") ?? root?.closest?.(".pane--workbench");
  const documentRoot = root.ownerDocument;
  const frameHost = documentRoot?.defaultView ?? globalThis;
  const requestFrame = typeof frameHost?.requestAnimationFrame === "function"
    ? frameHost.requestAnimationFrame.bind(frameHost)
    : (callback) => globalThis.setTimeout(callback, 0);
  const cancelFrame = typeof frameHost?.cancelAnimationFrame === "function"
    ? frameHost.cancelAnimationFrame.bind(frameHost)
    : (handle) => globalThis.clearTimeout(handle);
  let currentState = null;
  let released = false;
  let pendingMapResume = null;
  let matrixExpandedScrollTop = 0;

  const cancelPendingMapResume = () => {
    if (!pendingMapResume) return;
    pendingMapResume.cancelled = true;
    cancelFrame(pendingMapResume.handle);
    pendingMapResume = null;
  };

  const scheduleMapResume = () => {
    cancelPendingMapResume();
    const pending = { cancelled: false, handle: null };
    pendingMapResume = pending;
    pending.handle = requestFrame(() => {
      if (released || pending.cancelled || pendingMapResume !== pending) return;
      pendingMapResume = null;
      graphController.setExpanded(true);
      graphController.refreshAfterLayout();
    });
  };

  const patchMap = (expanded, previousExpanded) => preserveOuterScroll(scrollOwner, () => {
    if (!expanded) {
      cancelPendingMapResume();
      moveFocusBeforeHide(mapBody, mapToggle, documentRoot);
      graphController.setExpanded(false);
    }
    mapBody.hidden = !expanded;
    mapToggle.setAttribute("aria-expanded", String(expanded));
    mapToggle.textContent = expanded ? "⌃ 그래프 접기" : "⌄ 그래프 펼치기";
    workbench.setAttribute("data-component-map-expanded", String(expanded));
    map?.setAttribute?.("data-graph-expanded", String(expanded));
    map?.classList?.toggle?.("component-map--collapsed", !expanded);
    root.querySelectorAll?.("[data-graph-zoom]")?.forEach?.((control) => {
      control.disabled = !expanded;
    });
    if (expanded && previousExpanded === false) scheduleMapResume();
  });

  const patchMatrixBody = (expanded) => {
    if (!expanded) moveFocusBeforeHide(matrixBody, matrixToggle, documentRoot);
    matrixBody.hidden = !expanded;
    matrixToggle.setAttribute("aria-expanded", String(expanded));
    matrixToggle.textContent = expanded ? "⌃ 매트릭스 접기" : "⌄ 매트릭스 펼치기";
    workbench.setAttribute("data-profile-matrix-expanded", String(expanded));
  };

  const patchMatrix = (expanded, previousExpanded) => {
    if (scrollOwner && !expanded && previousExpanded === true) {
      matrixExpandedScrollTop = Number.isFinite(scrollOwner.scrollTop)
        ? scrollOwner.scrollTop
        : 0;
    }
    if (scrollOwner && expanded && previousExpanded === false) {
      patchMatrixBody(true);
      const maximum = Math.max(
        0,
        (scrollOwner.scrollHeight ?? 0) - (scrollOwner.clientHeight ?? 0),
      );
      scrollOwner.scrollTop = Math.min(matrixExpandedScrollTop, maximum);
      return;
    }
    preserveOuterScroll(scrollOwner, () => patchMatrixBody(expanded));
  };

  const onClick = (event) => {
    if (released || !currentState) return;
    const mapControl = event.target?.closest?.("[data-sot-graph-toggle]");
    const matrixControl = event.target?.closest?.("[data-sot-matrix-toggle]");
    if (!mapControl && !matrixControl) return;
    event.preventDefault?.();
    options.onChange({
      ...currentState,
      ...(mapControl ? { componentMap: !currentState.componentMap } : {}),
      ...(matrixControl ? { profileMatrix: !currentState.profileMatrix } : {}),
    });
  };
  root.addEventListener("click", onClick);

  return Object.freeze({
    sync(nextState) {
      if (released) return;
      const next = normalizeState(nextState);
      if (!currentState || currentState.componentMap !== next.componentMap) {
        patchMap(next.componentMap, currentState?.componentMap);
      }
      if (!currentState || currentState.profileMatrix !== next.profileMatrix) {
        patchMatrix(next.profileMatrix, currentState?.profileMatrix);
      }
      currentState = next;
    },
    release() {
      if (released) return;
      released = true;
      cancelPendingMapResume();
      root.removeEventListener("click", onClick);
    },
  });
}
