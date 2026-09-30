import {
  KEYBOARD_STEP_PX,
  normalizeWorkspaceLayoutState,
  projectWorkspaceLayout,
} from "./state.js";

function samePair(left, right) {
  return left?.preferredLeftWidthPx === right?.preferredLeftWidthPx
    && left?.preferredRightWidthPx === right?.preferredRightWidthPx
    && left?.leftCollapsed === right?.leftCollapsed
    && left?.rightCollapsed === right?.rightCollapsed;
}

function findWorkspaceStyleRule(document) {
  const pending = [];
  for (const sheet of document?.styleSheets ?? []) {
    try {
      pending.push(...(sheet.cssRules ?? []));
    } catch (_error) {
      // Cross-origin sheets are not eligible presentation authorities.
    }
  }
  while (pending.length) {
    const rule = pending.shift();
    if (rule?.selectorText?.split(",").map((value) => value.trim()).includes(".desktop-shell")) {
      return rule;
    }
    if (rule?.cssRules) pending.push(...rule.cssRules);
  }
  return null;
}

function integerFrame(element) {
  const frame = element?.getBoundingClientRect?.() ?? {};
  const value = (candidate) => Math.max(0, Math.round(Number(candidate) || 0));
  return Object.freeze({
    x: value(frame.x),
    y: value(frame.y),
    width: value(frame.width),
    height: value(frame.height),
  });
}

function preferredWidthsText(projection) {
  return `선호 패널 너비 좌측 ${projection.preferredLeftWidthPx}픽셀, 우측 ${projection.preferredRightWidthPx}픽셀`;
}

export function createWorkspaceLayoutController({
  root,
  backend,
  initialState,
  styleRule = findWorkspaceStyleRule(root?.ownerDocument),
  windowTarget = globalThis.window,
  onStateChange = () => {},
} = {}) {
  if (!root?.addEventListener || !styleRule?.style?.setProperty) {
    throw new Error("Workspace layout presentation authority is unavailable.");
  }

  let authoritative = normalizeWorkspaceLayoutState(initialState);
  let desiredPair = null;
  let currentProjection = null;
  let activeDrag = null;
  let writeInFlight = false;
  let released = false;

  styleRule.style.setProperty(
    "--workspace-left-width",
    `${authoritative.preferredLeftWidthPx}px`,
  );
  styleRule.style.setProperty(
    "--workspace-right-width",
    `${authoritative.preferredRightWidthPx}px`,
  );

  const displayedPair = () => desiredPair ?? authoritative;

  const patchDividerValueText = (divider, value) => {
    if (!divider) return;
    const activeSuffix = divider.getAttribute?.("data-dragging") === "true"
      ? " · 드래그 중"
      : "";
    divider.setAttribute("aria-valuetext", `${value}픽셀${activeSuffix}`);
  };

  const patchDivider = (divider, side, projection) => {
    if (!divider) return;
    const active = projection.separatorsActive;
    const value = side === "left"
      ? projection.effectiveLeftWidthPx
      : projection.effectiveRightWidthPx;
    const minimum = side === "left" ? projection.leftMinimumPx : projection.rightMinimumPx;
    const maximum = side === "left" ? projection.leftMaximumPx : projection.rightMaximumPx;
    divider.setAttribute("aria-disabled", String(!active));
    divider.setAttribute("aria-valuemin", String(minimum));
    divider.setAttribute("aria-valuemax", String(maximum));
    divider.setAttribute("aria-valuenow", String(value));
    patchDividerValueText(divider, value);
    divider.tabIndex = active ? 0 : -1;
    if (!active && root.ownerDocument?.activeElement === divider) {
      divider.blur?.();
    }
    divider.classList?.toggle?.("workspace-divider--disabled", !active);
  };

  const patchProjection = (projection) => {
    const shell = root.querySelector("[data-workspace-shell]");
    if (!shell) return null;
    styleRule.style.setProperty(
      "--workspace-left-width",
      `${projection.effectiveLeftWidthPx}px`,
    );
    styleRule.style.setProperty(
      "--workspace-right-width",
      `${projection.effectiveRightWidthPx}px`,
    );
    shell.dataset.layoutMode = projection.mode;
    const preferredWidths = root.querySelector("#workspace-preferred-widths");
    if (preferredWidths) {
      const text = preferredWidthsText(projection);
      preferredWidths.textContent = text;
      preferredWidths.setAttribute("aria-label", text);
    }
    patchDivider(
      root.querySelector('[data-workspace-divider="left"]'),
      "left",
      projection,
    );
    patchDivider(
      root.querySelector('[data-workspace-divider="right"]'),
      "right",
      projection,
    );
    currentProjection = projection;
    return projection;
  };

  const projectCurrent = (drag = null) => {
    const shell = root.querySelector("[data-workspace-shell]");
    const shellWidth = Math.round(Number(shell?.getBoundingClientRect?.().width) || 0);
    if (shellWidth <= 0) return null;
    const pair = displayedPair();
    return projectWorkspaceLayout({
      shellWidth,
      preferredLeftWidthPx: pair.preferredLeftWidthPx,
      preferredRightWidthPx: pair.preferredRightWidthPx,
      ...(drag ?? {}),
    });
  };

  const syncAfterRender = () => {
    if (released) return null;
    const projection = projectCurrent();
    return projection ? patchProjection(projection) : null;
  };

  const notify = (diagnostic = null) => {
    onStateChange(Object.freeze({ ...authoritative, diagnostic }));
  };

  const pumpPersistence = () => {
    if (released || writeInFlight || !desiredPair) return;
    const sentPair = { ...desiredPair };
    const request = {
      expectedLayoutRevision: authoritative.revision,
      preferredLeftWidthPx: sentPair.preferredLeftWidthPx,
      preferredRightWidthPx: sentPair.preferredRightWidthPx,
      leftCollapsed: sentPair.leftCollapsed,
      rightCollapsed: sentPair.rightCollapsed,
    };
    writeInFlight = true;
    Promise.resolve(backend.setWorkspaceLayout(request))
      .then((response) => {
        if (released) return;
        authoritative = normalizeWorkspaceLayoutState(response?.workspace_layout);
        if (samePair(desiredPair, sentPair)) desiredPair = null;
        notify(response?.diagnostic ?? null);
        syncAfterRender();
      })
      .catch((error) => {
        if (released) return;
        if (samePair(desiredPair, sentPair)) desiredPair = null;
        notify({
          code: String(error?.code ?? "workspace_layout_update_failed"),
          safe_message: String(error?.safe_message ?? "레이아웃 설정을 저장하지 못했습니다."),
        });
        syncAfterRender();
      })
      .finally(() => {
        writeInFlight = false;
        if (!released) pumpPersistence();
      });
  };

  const commitPair = (preferredLeftWidthPx, preferredRightWidthPx, collapsed = displayedPair()) => {
    desiredPair = {
      preferredLeftWidthPx,
      preferredRightWidthPx,
      leftCollapsed: collapsed.leftCollapsed === true,
      rightCollapsed: collapsed.rightCollapsed === true,
    };
    syncAfterRender();
    pumpPersistence();
  };

  const endDragVisualState = (drag) => {
    const divider = drag?.divider;
    divider?.classList?.remove?.("workspace-divider--dragging");
    divider?.setAttribute?.("data-dragging", "false");
    const value = Number(divider?.getAttribute?.("aria-valuenow"));
    if (Number.isFinite(value)) patchDividerValueText(divider, value);
    root.ownerDocument?.body?.classList?.remove?.("is-resizing");
  };

  const cancelActiveDrag = ({ releaseCapture = true, resync = true } = {}) => {
    const drag = activeDrag;
    if (!drag) return false;
    activeDrag = null;
    if (releaseCapture) drag.divider.releasePointerCapture?.(drag.pointerId);
    endDragVisualState(drag);
    if (resync) syncAfterRender();
    return true;
  };

  const onPointerDown = (event) => {
    if (released || event.button !== 0) return;
    const divider = event.target?.closest?.("[data-workspace-divider]");
    const side = divider?.dataset?.workspaceDivider;
    if (!divider || !["left", "right"].includes(side)) return;
    const projection = currentProjection ?? syncAfterRender();
    if (!projection?.separatorsActive) return;
    divider.focus?.({ preventScroll: true });
    event.preventDefault?.();
    activeDrag = {
      side,
      divider,
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: side === "left"
        ? projection.effectiveLeftWidthPx
        : projection.effectiveRightWidthPx,
      oppositeWidth: side === "left"
        ? projection.effectiveRightWidthPx
        : projection.effectiveLeftWidthPx,
    };
    divider.setPointerCapture?.(event.pointerId);
    divider.classList?.add?.("workspace-divider--dragging");
    divider.setAttribute?.("data-dragging", "true");
    patchDividerValueText(divider, activeDrag.startWidth);
    root.ownerDocument?.body?.classList?.add?.("is-resizing");
  };

  const onPointerMove = (event) => {
    if (!activeDrag || event.pointerId !== activeDrag.pointerId) return;
    event.preventDefault?.();
    const delta = event.clientX - activeDrag.startX;
    const activeWidthPx = activeDrag.startWidth
      + (activeDrag.side === "left" ? delta : -delta);
    const projection = projectCurrent({
      activeSide: activeDrag.side,
      activeWidthPx: Math.round(activeWidthPx),
      oppositeEffectiveWidthPx: activeDrag.oppositeWidth,
    });
    if (projection) patchProjection(projection);
  };

  const finishPointer = (event, commit) => {
    if (!activeDrag || event.pointerId !== activeDrag.pointerId) return;
    const drag = activeDrag;
    activeDrag = null;
    drag.divider.releasePointerCapture?.(event.pointerId);
    endDragVisualState(drag);
    if (!commit || !currentProjection) {
      syncAfterRender();
      return;
    }
    const committedWidth = drag.side === "left"
      ? currentProjection.effectiveLeftWidthPx
      : currentProjection.effectiveRightWidthPx;
    if (committedWidth === drag.startWidth) {
      syncAfterRender();
      return;
    }
    const pair = displayedPair();
    commitPair(
      drag.side === "left" ? currentProjection.effectiveLeftWidthPx : pair.preferredLeftWidthPx,
      drag.side === "right" ? currentProjection.effectiveRightWidthPx : pair.preferredRightWidthPx,
      pair,
    );
  };

  const onPointerUp = (event) => finishPointer(event, true);
  const onPointerCancel = (event) => finishPointer(event, false);
  const onLostPointerCapture = (event) => {
    if (!activeDrag || event.pointerId !== activeDrag.pointerId) return;
    cancelActiveDrag({ releaseCapture: false });
  };
  const onResize = () => {
    if (!cancelActiveDrag()) syncAfterRender();
  };

  const onKeyDown = (event) => {
    const divider = event.target?.closest?.("[data-workspace-divider]");
    const side = divider?.dataset?.workspaceDivider;
    if (!divider || !["left", "right"].includes(side)) return;
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const projection = currentProjection ?? syncAfterRender();
    if (!projection?.separatorsActive) return;
    event.preventDefault?.();
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const signedStep = side === "left"
      ? direction * KEYBOARD_STEP_PX
      : -direction * KEYBOARD_STEP_PX;
    const currentWidth = side === "left"
      ? projection.effectiveLeftWidthPx
      : projection.effectiveRightWidthPx;
    const oppositeWidth = side === "left"
      ? projection.effectiveRightWidthPx
      : projection.effectiveLeftWidthPx;
    const next = projectCurrent({
      activeSide: side,
      activeWidthPx: currentWidth + signedStep,
      oppositeEffectiveWidthPx: oppositeWidth,
    });
    if (!next) return;
    const nextWidth = side === "left"
      ? next.effectiveLeftWidthPx
      : next.effectiveRightWidthPx;
    if (nextWidth === currentWidth) return;
    patchProjection(next);
    const pair = displayedPair();
    commitPair(
      side === "left" ? next.effectiveLeftWidthPx : pair.preferredLeftWidthPx,
      side === "right" ? next.effectiveRightWidthPx : pair.preferredRightWidthPx,
      pair,
    );
  };

  root.addEventListener("pointerdown", onPointerDown);
  root.addEventListener("keydown", onKeyDown);
  root.addEventListener("lostpointercapture", onLostPointerCapture);
  windowTarget?.addEventListener?.("pointermove", onPointerMove);
  windowTarget?.addEventListener?.("pointerup", onPointerUp);
  windowTarget?.addEventListener?.("pointercancel", onPointerCancel);
  windowTarget?.addEventListener?.("resize", onResize);

  return Object.freeze({
    syncAfterRender,
    replaceAuthoritativeState(nextState) {
      authoritative = normalizeWorkspaceLayoutState(nextState);
      desiredPair = null;
      return syncAfterRender();
    },
    setCollapsed(side, collapsed) {
      if (!['left', 'right'].includes(side)) return;
      const pair = displayedPair();
      const next = {
        ...pair,
        leftCollapsed: side === 'left' ? collapsed === true : pair.leftCollapsed === true,
        rightCollapsed: side === 'right' ? collapsed === true : pair.rightCollapsed === true,
      };
      if (samePair(pair, next)) return;
      desiredPair = next;
      onStateChange(Object.freeze({ ...authoritative, ...next, persisted: authoritative.persisted }));
      pumpPersistence();
    },
    getBootstrapProbe() {
      const projection = currentProjection ?? syncAfterRender();
      if (!projection) throw new Error("Workspace layout has no measurable shell.");
      const shell = root.querySelector("[data-workspace-shell]");
      const left = root.querySelector("#workspace-left-pane");
      const center = root.querySelector("#workbench");
      const right = root.querySelector("#workspace-right-pane");
      const pair = displayedPair();
      const visibleSideCount = Number(pair.leftCollapsed !== true)
        + Number(pair.rightCollapsed !== true);
      return Object.freeze({
        appliedLayoutRevision: authoritative.revision,
        appliedPreferredPair: Object.freeze({
          preferredLeftWidthPx: pair.preferredLeftWidthPx,
          preferredRightWidthPx: pair.preferredRightWidthPx,
        }),
        appliedCollapsedPair: Object.freeze({
          leftCollapsed: pair.leftCollapsed === true,
          rightCollapsed: pair.rightCollapsed === true,
        }),
        layoutMode: projection.mode,
        shellFrame: integerFrame(shell),
        paneFrames: Object.freeze({
          left: pair.leftCollapsed === true ? null : integerFrame(left),
          center: integerFrame(center),
          right: pair.rightCollapsed === true ? null : integerFrame(right),
        }),
        activeSeparatorCount: projection.separatorsActive ? visibleSideCount : 0,
        disabledSeparatorCount: projection.separatorsActive ? 0 : visibleSideCount,
      });
    },
    release() {
      if (released) return;
      cancelActiveDrag({ resync: false });
      released = true;
      root.removeEventListener("pointerdown", onPointerDown);
      root.removeEventListener("keydown", onKeyDown);
      root.removeEventListener("lostpointercapture", onLostPointerCapture);
      windowTarget?.removeEventListener?.("pointermove", onPointerMove);
      windowTarget?.removeEventListener?.("pointerup", onPointerUp);
      windowTarget?.removeEventListener?.("pointercancel", onPointerCancel);
      windowTarget?.removeEventListener?.("resize", onResize);
    },
  });
}
