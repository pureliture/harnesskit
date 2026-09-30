function clockNow(clock) {
  if (typeof clock === "function") return Number(clock()) || 0;
  if (typeof clock?.now === "function") return Number(clock.now()) || 0;
  return 0;
}

function callFirst(port, names, ...args) {
  for (const name of names) {
    if (typeof port?.[name] !== "function") continue;
    port[name](...args);
    return true;
  }
  return false;
}

function finalValue(target) {
  return target?.to;
}

function interpolatedValue(target, progress) {
  const from = Number(target?.from);
  const to = Number(target?.to);
  if (!Number.isFinite(from) || !Number.isFinite(to)) {
    return progress >= 1 ? finalValue(target) : target?.from;
  }
  return from + ((to - from) * progress);
}

export function createGraphActivityController({
  rendererAnimationPort,
  appFrameScheduler,
  clock = globalThis.performance,
  motionQuery = null,
  documentObject = globalThis.document,
  onAppFrame = () => {},
} = {}) {
  if (!rendererAnimationPort) {
    throw new TypeError("rendererAnimationPort is required");
  }
  if (typeof appFrameScheduler?.requestAnimationFrame !== "function"
    || typeof appFrameScheduler?.cancelAnimationFrame !== "function") {
    throw new TypeError("appFrameScheduler must provide requestAnimationFrame and cancelAnimationFrame");
  }

  const transitions = new Map();
  let released = false;
  let logicalLease = false;
  let internalRendererRunning = false;
  let appRafId = null;
  let state = {
    expanded: false,
    intersecting: false,
    foreground: documentObject?.visibilityState !== "hidden",
    reducedMotion: Boolean(motionQuery?.matches),
  };

  const diagnostics = () => ({
    logicalLease: logicalLease ? 1 : 0,
    internalRendererRunning: internalRendererRunning ? 1 : 0,
    appRafRunning: appRafId === null ? 0 : 1,
    pendingTransitionCount: transitions.size,
  });

  const gateIsOpen = () => (
    state.expanded
    && state.intersecting
    && state.foreground
    && !state.reducedMotion
  );

  const cancelAppFrame = () => {
    if (appRafId === null) return;
    appFrameScheduler.cancelAnimationFrame(appRafId);
    appRafId = null;
  };

  const finishTransitions = (reason) => {
    if (transitions.size === 0) return false;
    transitions.forEach((target) => target.apply?.(finalValue(target)));
    transitions.clear();
    onAppFrame({ reason, timestamp: clockNow(clock), transitionCount: 0 });
    return true;
  };

  const closeLease = () => {
    cancelAppFrame();
    finishTransitions("inactive-final");
    if (internalRendererRunning) {
      callFirst(rendererAnimationPort, ["pauseAnimation", "pause", "stop"]);
      internalRendererRunning = false;
    }
    logicalLease = false;
  };

  const frame = (timestamp) => {
    appRafId = null;
    if (released || !gateIsOpen()) {
      closeLease();
      return;
    }

    const frameTime = Number.isFinite(Number(timestamp))
      ? Number(timestamp)
      : clockNow(clock);
    transitions.forEach((target, id) => {
      const durationMs = Math.max(0, Number(target.durationMs) || 0);
      const progress = durationMs === 0
        ? 1
        : Math.min(1, Math.max(0, (frameTime - target.startedAt) / durationMs));
      target.apply?.(interpolatedValue(target, progress));
      if (progress >= 1) transitions.delete(id);
    });
    onAppFrame({
      reason: "transition",
      timestamp: frameTime,
      transitionCount: transitions.size,
    });

    if (transitions.size > 0) {
      appRafId = appFrameScheduler.requestAnimationFrame(frame);
    }
  };

  const ensureAppFrame = () => {
    if (released || !gateIsOpen() || transitions.size === 0 || appRafId !== null) return;
    appRafId = appFrameScheduler.requestAnimationFrame(frame);
  };

  const reconcile = () => {
    if (released) return;
    if (!gateIsOpen()) {
      closeLease();
      return;
    }
    logicalLease = true;
    if (!internalRendererRunning) {
      callFirst(rendererAnimationPort, ["resumeAnimation", "resume", "start"]);
      internalRendererRunning = true;
    }
    ensureAppFrame();
  };

  const sync = (next = {}) => {
    if (released) return diagnostics();
    state = {
      expanded: Object.hasOwn(next, "expanded") ? Boolean(next.expanded) : state.expanded,
      intersecting: Object.hasOwn(next, "intersecting")
        ? Boolean(next.intersecting)
        : state.intersecting,
      foreground: Object.hasOwn(next, "foreground")
        ? Boolean(next.foreground)
        : state.foreground,
      reducedMotion: Object.hasOwn(next, "reducedMotion")
        ? Boolean(next.reducedMotion)
        : state.reducedMotion,
    };
    reconcile();
    return diagnostics();
  };

  const transition = (targets = []) => {
    if (released || !Array.isArray(targets)) return diagnostics();
    const startedAt = clockNow(clock);
    targets.forEach((target, index) => {
      if (!target || typeof target !== "object") return;
      const id = String(target.id ?? index);
      const durationMs = Math.max(0, Number(target.durationMs) || 0);
      transitions.delete(id);
      if (!gateIsOpen() || durationMs === 0) {
        target.apply?.(finalValue(target));
        return;
      }
      target.apply?.(target.from);
      transitions.set(id, { ...target, id, durationMs, startedAt });
    });
    if (!gateIsOpen()) {
      onAppFrame({ reason: "transition-final", timestamp: startedAt, transitionCount: 0 });
    }
    reconcile();
    if (transitions.size === 0) cancelAppFrame();
    else ensureAppFrame();
    return diagnostics();
  };

  const refreshOnce = (reason = "refresh") => {
    if (released) return diagnostics();
    callFirst(rendererAnimationPort, ["refreshOnce", "renderOnce", "render"], reason);
    onAppFrame({ reason, timestamp: clockNow(clock), transitionCount: transitions.size });
    return diagnostics();
  };

  const onVisibilityChange = () => {
    if (released) return;
    state = {
      ...state,
      foreground: documentObject?.visibilityState === "visible",
    };
    reconcile();
  };
  const onMotionChange = (event) => {
    if (released) return;
    state = {
      ...state,
      reducedMotion: Boolean(event?.matches ?? motionQuery?.matches),
    };
    reconcile();
  };
  documentObject?.addEventListener?.("visibilitychange", onVisibilityChange);
  motionQuery?.addEventListener?.("change", onMotionChange);

  const release = () => {
    if (released) return;
    closeLease();
    documentObject?.removeEventListener?.("visibilitychange", onVisibilityChange);
    motionQuery?.removeEventListener?.("change", onMotionChange);
    released = true;
  };

  return {
    diagnostics,
    refreshOnce,
    release,
    sync,
    transition,
  };
}
