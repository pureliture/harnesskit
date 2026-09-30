import assert from "node:assert/strict";
import test from "node:test";

async function loadActivityController() {
  try {
    return await import("../graph/activity-controller.js");
  } catch (error) {
    if (error?.code === "ERR_MODULE_NOT_FOUND") {
      assert.fail("approved activity-controller public seam is not implemented");
    }
    throw error;
  }
}

function diagnosticsState(controller) {
  const value = controller.diagnostics();
  const read = (...names) => {
    for (const name of names) {
      if (name in value) return Number(value[name]);
    }
    return Number.NaN;
  };
  const state = [
    read("logicalLease", "logical_lease", "lease"),
    read("internalRendererRunning", "internal_renderer_running", "internal"),
    read("appRafRunning", "app_raf_running", "appRaf", "app"),
  ];
  assert.ok(state.every((item) => item === 0 || item === 1), "diagnostics must expose exact 0|1 state");
  return state;
}

function eventTarget(initial = {}) {
  const listeners = new Map();
  return {
    ...initial,
    addEventListener(type, listener) {
      const values = listeners.get(type) ?? new Set();
      values.add(listener);
      listeners.set(type, values);
    },
    removeEventListener(type, listener) {
      listeners.get(type)?.delete(listener);
    },
    listenerCount() {
      return [...listeners.values()].reduce((total, values) => total + values.size, 0);
    },
  };
}

function schedulerHarness() {
  let nextId = 1;
  const pending = new Map();
  const request = (callback) => {
    const id = nextId;
    nextId += 1;
    pending.set(id, callback);
    return id;
  };
  const cancel = (id) => pending.delete(id);
  return {
    request,
    cancel,
    requestAnimationFrame: request,
    cancelAnimationFrame: cancel,
    flush(timestamp) {
      const callbacks = [...pending.entries()];
      pending.clear();
      callbacks.forEach(([, callback]) => callback(timestamp));
    },
    pendingCount() {
      return pending.size;
    },
  };
}

async function controllerHarness() {
  const { createGraphActivityController } = await loadActivityController();
  assert.equal(typeof createGraphActivityController, "function");
  const renderer = {
    running: false,
    resumeCalls: 0,
    pauseCalls: 0,
    resumeAnimation() { this.running = true; this.resumeCalls += 1; },
    pauseAnimation() { this.running = false; this.pauseCalls += 1; },
    start() { this.resumeAnimation(); },
    stop() { this.pauseAnimation(); },
    resume() { this.resumeAnimation(); },
    pause() { this.pauseAnimation(); },
  };
  const scheduler = schedulerHarness();
  const documentObject = eventTarget({ visibilityState: "visible" });
  const motionQuery = eventTarget({ matches: false });
  let now = 0;
  const clock = () => now;
  clock.now = () => now;
  const frames = [];
  const controller = createGraphActivityController({
    rendererAnimationPort: renderer,
    appFrameScheduler: scheduler,
    clock,
    motionQuery,
    documentObject,
    onAppFrame: (frame) => frames.push(frame),
  });
  return {
    controller,
    documentObject,
    frames,
    motionQuery,
    renderer,
    scheduler,
    setNow(value) { now = value; },
  };
}

const ACTIVE = Object.freeze({
  expanded: true,
  intersecting: true,
  foreground: true,
  reducedMotion: false,
});

test("active steady, active transition, and transition completion are exactly 1/1/0 -> 1/1/1 -> 1/1/0", async () => {
  const harness = await controllerHarness();
  harness.controller.sync(ACTIVE);
  assert.deepEqual(diagnosticsState(harness.controller), [1, 1, 0]);

  const applied = [];
  harness.controller.transition([{
    id: "selected-node-opacity",
    from: 0,
    to: 1,
    durationMs: 240,
    apply: (value) => applied.push(value),
  }]);
  assert.deepEqual(diagnosticsState(harness.controller), [1, 1, 1]);

  harness.setNow(240);
  harness.scheduler.flush(240);
  assert.deepEqual(diagnosticsState(harness.controller), [1, 1, 0]);
  assert.equal(harness.scheduler.pendingCount(), 0);
  harness.controller.release();
});

test("hidden, collapsed, background, and reduced-motion states are exactly 0/0/0", async (t) => {
  const inactiveCases = [
    ["hidden", { ...ACTIVE, intersecting: false }],
    ["collapsed", { ...ACTIVE, expanded: false }],
    ["background", { ...ACTIVE, foreground: false }],
    ["reduced", { ...ACTIVE, reducedMotion: true }],
  ];
  for (const [name, state] of inactiveCases) {
    await t.test(name, async () => {
      const harness = await controllerHarness();
      harness.controller.sync(ACTIVE);
      assert.deepEqual(diagnosticsState(harness.controller), [1, 1, 0]);
      harness.controller.sync(state);
      assert.deepEqual(diagnosticsState(harness.controller), [0, 0, 0]);
      assert.equal(harness.scheduler.pendingCount(), 0);
      harness.controller.release();
    });
  }
});

test("repeated resume owns one lease and one internal renderer loop without duplicate listeners", async () => {
  const harness = await controllerHarness();
  harness.controller.sync({ ...ACTIVE, expanded: false });
  assert.deepEqual(diagnosticsState(harness.controller), [0, 0, 0]);

  harness.controller.sync(ACTIVE);
  harness.controller.sync(ACTIVE);
  harness.controller.sync(ACTIVE);

  assert.deepEqual(diagnosticsState(harness.controller), [1, 1, 0]);
  assert.equal(harness.renderer.resumeCalls, 1);
  assert.ok(harness.documentObject.listenerCount() <= 1);
  assert.ok(harness.motionQuery.listenerCount() <= 1);
  harness.controller.release();
});

test("inactive one-shot refresh and idempotent release leave no persistent callback or listener", async () => {
  const harness = await controllerHarness();
  harness.controller.sync({ ...ACTIVE, expanded: false });
  assert.deepEqual(diagnosticsState(harness.controller), [0, 0, 0]);

  harness.controller.refreshOnce("fit");
  assert.deepEqual(diagnosticsState(harness.controller), [0, 0, 0]);
  assert.equal(harness.scheduler.pendingCount(), 0);

  harness.controller.release();
  harness.controller.release();
  assert.equal(harness.scheduler.pendingCount(), 0);
  assert.equal(harness.documentObject.listenerCount(), 0);
  assert.equal(harness.motionQuery.listenerCount(), 0);
});
