import assert from "node:assert/strict";
import test from "node:test";

import { createWorkspaceLayoutController } from "../layout/controller.js";

function fakeClassList() {
  const values = new Set();
  return {
    add: (...names) => names.forEach((name) => values.add(name)),
    remove: (...names) => names.forEach((name) => values.delete(name)),
    toggle(name, force) {
      if (force === true) values.add(name);
      else if (force === false) values.delete(name);
      else if (values.has(name)) values.delete(name);
      else values.add(name);
    },
    contains: (name) => values.has(name),
  };
}

function fakeElement({ side = null, frame }) {
  const attributes = new Map();
  return {
    dataset: side ? { workspaceDivider: side } : {},
    classList: fakeClassList(),
    tabIndex: 0,
    hidden: false,
    captureCount: 0,
    releaseCount: 0,
    setAttribute(name, value) { attributes.set(name, String(value)); },
    getAttribute(name) { return attributes.get(name) ?? null; },
    closest(selector) {
      return selector === "[data-workspace-divider]" && side ? this : null;
    },
    getBoundingClientRect() { return { ...frame }; },
    setPointerCapture() { this.captureCount += 1; },
    releasePointerCapture() { this.releaseCount += 1; },
    textContent: "",
  };
}

function createHarness(shellWidth = 1200) {
  const rootListeners = new Map();
  const windowListeners = new Map();
  const styleValues = new Map();
  const body = { classList: fakeClassList() };
  const styleRule = {
    style: {
      setProperty(name, value) { styleValues.set(name, value); },
    },
  };
  let elements;
  const replaceElements = (width = shellWidth) => {
    shellWidth = width;
    elements = {
      shell: fakeElement({ frame: { x: 0, y: 0, width, height: 800 } }),
      left: fakeElement({ frame: { x: 0, y: 0, width: 304, height: 800 } }),
      center: fakeElement({ frame: { x: 316, y: 0, width: 504, height: 800 } }),
      right: fakeElement({ frame: { x: 832, y: 0, width: 368, height: 800 } }),
      leftDivider: fakeElement({ side: "left", frame: { x: 304, y: 0, width: 12, height: 800 } }),
      rightDivider: fakeElement({ side: "right", frame: { x: 820, y: 0, width: 12, height: 800 } }),
      preferredWidths: fakeElement({ frame: { x: 0, y: 0, width: 1, height: 1 } }),
    };
    elements.shell.dataset = {};
  };
  replaceElements();
  const root = {
    ownerDocument: { body, styleSheets: [] },
    addEventListener(type, handler) { rootListeners.set(type, handler); },
    removeEventListener(type) { rootListeners.delete(type); },
    querySelector(selector) {
      return {
        "[data-workspace-shell]": elements.shell,
        "#workspace-left-pane": elements.left,
        "#workbench": elements.center,
        "#workspace-right-pane": elements.right,
        '[data-workspace-divider="left"]': elements.leftDivider,
        '[data-workspace-divider="right"]': elements.rightDivider,
        "#workspace-preferred-widths": elements.preferredWidths,
      }[selector] ?? null;
    },
    querySelectorAll(selector) {
      return selector === "[data-workspace-divider]"
        ? [elements.leftDivider, elements.rightDivider]
        : [];
    },
  };
  const windowTarget = {
    addEventListener(type, handler) { windowListeners.set(type, handler); },
    removeEventListener(type) { windowListeners.delete(type); },
  };
  const dispatchRoot = (type, target, extra = {}) => rootListeners.get(type)?.({
    target,
    button: 0,
    pointerId: 1,
    clientX: 0,
    key: "",
    preventDefault() {},
    ...extra,
  });
  const dispatchWindow = (type, extra = {}) => windowListeners.get(type)?.({
    pointerId: 1,
    clientX: 0,
    preventDefault() {},
    ...extra,
  });
  return {
    root,
    windowTarget,
    styleRule,
    styleValues,
    body,
    elements: () => elements,
    replaceElements,
    dispatchRoot,
    dispatchWindow,
  };
}

function initialState(overrides = {}) {
  return {
    preferred_left_width_px: 304,
    preferred_right_width_px: 368,
    revision: 0,
    persisted: true,
    ...overrides,
  };
}

function response(request, revision) {
  return {
    workspace_layout: {
      preferred_left_width_px: request.preferredLeftWidthPx,
      preferred_right_width_px: request.preferredRightWidthPx,
      left_collapsed: request.leftCollapsed === true,
      right_collapsed: request.rightCollapsed === true,
      revision,
      persisted: true,
    },
    diagnostic: null,
  };
}

test("sync patches CSSOM and ARIA after shell replacement without rebinding root listeners", () => {
  const harness = createHarness();
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: { setWorkspaceLayout: async (request) => response(request, 1) },
    initialState: initialState(),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
  });

  controller.syncAfterRender();
  assert.equal(harness.styleValues.get("--workspace-left-width"), "304px");
  assert.equal(harness.styleValues.get("--workspace-right-width"), "368px");
  assert.equal(harness.elements().shell.dataset.layoutMode, "three-pane");
  assert.equal(harness.elements().leftDivider.getAttribute("aria-valuenow"), "304");
  assert.equal(harness.elements().leftDivider.getAttribute("aria-valuetext"), "304픽셀");
  assert.equal(harness.elements().rightDivider.getAttribute("aria-valuemax"), "392");
  assert.equal(
    harness.elements().preferredWidths.textContent,
    "선호 패널 너비 좌측 304픽셀, 우측 368픽셀",
  );
  assert.equal(
    harness.elements().preferredWidths.getAttribute("aria-label"),
    "선호 패널 너비 좌측 304픽셀, 우측 368픽셀",
  );

  harness.replaceElements(1000);
  controller.syncAfterRender();
  assert.equal(harness.elements().shell.dataset.layoutMode, "two-column");
  assert.equal(harness.elements().leftDivider.getAttribute("aria-disabled"), "true");
  assert.equal(harness.elements().leftDivider.tabIndex, -1);
  assert.equal(
    harness.elements().preferredWidths.textContent,
    "선호 패널 너비 좌측 304픽셀, 우측 368픽셀",
  );
  controller.release();
});

test("bootstrap probe preserves collapsed pane absence and the applied pair", () => {
  const harness = createHarness();
  harness.elements().left = null;
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: { setWorkspaceLayout: async (request) => response(request, 1) },
    initialState: initialState({ left_collapsed: true, right_collapsed: false }),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
  });

  controller.syncAfterRender();

  assert.deepEqual(controller.getBootstrapProbe(), {
    appliedLayoutRevision: 0,
    appliedPreferredPair: {
      preferredLeftWidthPx: 304,
      preferredRightWidthPx: 368,
    },
    appliedCollapsedPair: {
      leftCollapsed: true,
      rightCollapsed: false,
    },
    layoutMode: "three-pane",
    shellFrame: { x: 0, y: 0, width: 1200, height: 800 },
    paneFrames: {
      left: null,
      center: { x: 316, y: 0, width: 504, height: 800 },
      right: { x: 832, y: 0, width: 368, height: 800 },
    },
    activeSeparatorCount: 1,
    disabledSeparatorCount: 0,
  });
  controller.release();
});

test("responsive projection blurs a divider that just became disabled", () => {
  const harness = createHarness();
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: { setWorkspaceLayout: async (request) => response(request, 1) },
    initialState: initialState(),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
  });
  controller.syncAfterRender();
  const divider = harness.elements().rightDivider;
  let blurCount = 0;
  divider.blur = () => {
    blurCount += 1;
    harness.root.ownerDocument.activeElement = null;
  };
  harness.root.ownerDocument.activeElement = divider;
  harness.elements().shell.getBoundingClientRect = () => ({
    x: 0,
    y: 0,
    width: 1000,
    height: 720,
  });

  harness.dispatchWindow("resize");

  assert.equal(blurCount, 1);
  assert.equal(harness.root.ownerDocument.activeElement, null);
  assert.equal(divider.getAttribute("aria-disabled"), "true");
  assert.equal(divider.tabIndex, -1);
  controller.release();
});

test("pointerdown focuses an active divider before starting drag", () => {
  const harness = createHarness();
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: { setWorkspaceLayout: async (request) => response(request, 1) },
    initialState: initialState(),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
  });
  controller.syncAfterRender();
  const divider = harness.elements().leftDivider;
  let focusOptions = null;
  divider.focus = (options) => {
    focusOptions = options;
    harness.root.ownerDocument.activeElement = divider;
  };

  harness.dispatchRoot("pointerdown", divider, { clientX: 304 });

  assert.equal(harness.root.ownerDocument.activeElement, divider);
  assert.deepEqual(focusOptions, { preventScroll: true });
  controller.release();
});

test("pointer move patches geometry only and pointerup commits one independent pair", async () => {
  const harness = createHarness();
  const calls = [];
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: {
      async setWorkspaceLayout(request) {
        calls.push(request);
        return response(request, calls.length);
      },
    },
    initialState: initialState(),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
  });
  controller.syncAfterRender();

  harness.dispatchRoot("pointerdown", harness.elements().leftDivider, { clientX: 100 });
  assert.equal(
    harness.elements().leftDivider.getAttribute("aria-valuetext"),
    "304픽셀 · 드래그 중",
  );
  harness.dispatchWindow("pointermove", { clientX: 140 });
  assert.equal(calls.length, 0);
  assert.equal(harness.styleValues.get("--workspace-left-width"), "328px");
  assert.equal(harness.styleValues.get("--workspace-right-width"), "368px");
  assert.equal(
    harness.elements().leftDivider.getAttribute("aria-valuetext"),
    "328픽셀 · 드래그 중",
  );
  assert.equal(harness.body.classList.contains("is-resizing"), true);

  harness.dispatchWindow("pointerup", { clientX: 140 });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, [{
    expectedLayoutRevision: 0,
    preferredLeftWidthPx: 328,
    preferredRightWidthPx: 368,
    leftCollapsed: false,
    rightCollapsed: false,
  }]);
  assert.equal(harness.body.classList.contains("is-resizing"), false);
  assert.equal(harness.elements().leftDivider.classList.contains("workspace-divider--dragging"), false);
  assert.equal(harness.elements().leftDivider.getAttribute("data-dragging"), "false");
  assert.equal(harness.elements().leftDivider.getAttribute("aria-valuetext"), "328픽셀");
  assert.equal(
    harness.elements().preferredWidths.textContent,
    "선호 패널 너비 좌측 328픽셀, 우측 368픽셀",
  );
  controller.release();
});

test("click-only drag and clamped keyboard input never persist responsive effective widths", async () => {
  const harness = createHarness(1057);
  const calls = [];
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: { async setWorkspaceLayout(request) { calls.push(request); return response(request, 1); } },
    initialState: initialState({ preferred_left_width_px: 200 }),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
  });
  controller.syncAfterRender();

  harness.dispatchRoot("pointerdown", harness.elements().leftDivider, { clientX: 100 });
  harness.dispatchWindow("pointerup", { clientX: 100 });
  harness.dispatchRoot("keydown", harness.elements().leftDivider, { key: "ArrowLeft" });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(calls.length, 0);
  controller.release();
});

test("resize, lost capture, and release cancel an active drag without persistence or stale visuals", () => {
  const harness = createHarness(1200);
  const calls = [];
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: { async setWorkspaceLayout(request) { calls.push(request); } },
    initialState: initialState(),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
  });
  controller.syncAfterRender();

  harness.dispatchRoot("pointerdown", harness.elements().leftDivider, { clientX: 100 });
  harness.dispatchWindow("pointermove", { clientX: 120 });
  harness.replaceElements(1057);
  harness.dispatchWindow("resize");
  harness.dispatchWindow("pointermove", { clientX: -1000 });
  harness.dispatchWindow("pointerup", { clientX: -1000 });
  assert.equal(calls.length, 0);
  assert.equal(harness.body.classList.contains("is-resizing"), false);

  harness.replaceElements(1200);
  controller.syncAfterRender();
  const divider = harness.elements().rightDivider;
  harness.dispatchRoot("pointerdown", divider, { clientX: 800 });
  harness.dispatchRoot("lostpointercapture", divider, { pointerId: 1 });
  assert.equal(divider.classList.contains("workspace-divider--dragging"), false);
  assert.equal(harness.body.classList.contains("is-resizing"), false);

  harness.dispatchRoot("pointerdown", divider, { clientX: 800 });
  controller.release();
  assert.equal(divider.classList.contains("workspace-divider--dragging"), false);
  assert.equal(harness.body.classList.contains("is-resizing"), false);
  assert.equal(calls.length, 0);
});

test("pointercancel restores the authoritative pair without persistence", () => {
  const harness = createHarness();
  const calls = [];
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: { async setWorkspaceLayout(request) { calls.push(request); } },
    initialState: initialState(),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
  });
  controller.syncAfterRender();
  harness.dispatchRoot("pointerdown", harness.elements().rightDivider, { clientX: 800 });
  harness.dispatchWindow("pointermove", { clientX: 760 });
  assert.equal(harness.styleValues.get("--workspace-right-width"), "392px");
  harness.dispatchWindow("pointercancel", { clientX: 760 });
  assert.equal(harness.styleValues.get("--workspace-right-width"), "368px");
  assert.equal(calls.length, 0);
  controller.release();
});

test("keyboard directions use 16px steps while responsive separators stay inert", async () => {
  const harness = createHarness();
  const calls = [];
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: {
      async setWorkspaceLayout(request) {
        calls.push(request);
        return response(request, calls.length);
      },
    },
    initialState: initialState(),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
  });
  controller.syncAfterRender();
  harness.dispatchRoot("keydown", harness.elements().rightDivider, { key: "ArrowLeft" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls[0].preferredLeftWidthPx, 304);
  assert.equal(calls[0].preferredRightWidthPx, 384);

  harness.replaceElements(1000);
  controller.syncAfterRender();
  harness.dispatchRoot("keydown", harness.elements().leftDivider, { key: "ArrowRight" });
  harness.dispatchRoot("pointerdown", harness.elements().leftDivider, { clientX: 100 });
  assert.equal(calls.length, 1);
  assert.equal(harness.elements().leftDivider.captureCount, 0);
  controller.release();
});

test("persistence keeps one write in flight and sends only the latest desired pair next", async () => {
  const harness = createHarness(1280);
  const calls = [];
  const completions = [];
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: {
      setWorkspaceLayout(request) {
        calls.push(request);
        return new Promise((resolve) => completions.push(() => resolve(response(request, calls.length))));
      },
    },
    initialState: initialState(),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
  });
  controller.syncAfterRender();
  harness.dispatchRoot("keydown", harness.elements().leftDivider, { key: "ArrowRight" });
  harness.dispatchRoot("keydown", harness.elements().leftDivider, { key: "ArrowRight" });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].preferredLeftWidthPx, 320);

  completions.shift()();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 2);
  assert.equal(calls[1].expectedLayoutRevision, 1);
  assert.equal(calls[1].preferredLeftWidthPx, 336);
  completions.shift()();
  await new Promise((resolve) => setImmediate(resolve));
  controller.release();
});

test("pane disclosure persists only the requested side while preserving both preferred widths", async () => {
  const harness = createHarness(1400);
  const calls = [];
  const updates = [];
  const controller = createWorkspaceLayoutController({
    root: harness.root,
    backend: {
      async setWorkspaceLayout(request) {
        calls.push(request);
        return response(request, 1);
      },
    },
    initialState: initialState({
      preferred_left_width_px: 312,
      preferred_right_width_px: 352,
    }),
    styleRule: harness.styleRule,
    windowTarget: harness.windowTarget,
    onStateChange(next) {
      updates.push(next);
    },
  });

  controller.setCollapsed("left", true);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [{
    expectedLayoutRevision: 0,
    preferredLeftWidthPx: 312,
    preferredRightWidthPx: 352,
    leftCollapsed: true,
    rightCollapsed: false,
  }]);
  assert.equal(updates.at(-1)?.leftCollapsed, true);
  assert.equal(updates.at(-1)?.rightCollapsed, false);
  controller.release();
});
