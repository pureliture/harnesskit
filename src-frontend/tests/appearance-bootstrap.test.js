import assert from "node:assert/strict";
import test from "node:test";

import { bootstrapAppearance } from "../appearance/bootstrap.js";

function appearance(logicalMode, resolvedMode, revision) {
  return {
    logical_mode: logicalMode,
    resolved_mode: resolvedMode,
    revision,
    persisted: true,
  };
}

function workspaceLayout(revision = 7, left = 304, right = 368) {
  return {
    preferred_left_width_px: left,
    preferred_right_width_px: right,
    left_collapsed: false,
    right_collapsed: false,
    revision,
    persisted: true,
  };
}

function typography(preset = "Default", revision = 0) {
  return { preset, revision, persisted: true };
}

function createLayoutControllerStub(order = null, onRelease = () => {}) {
  return ({ initialState, onStateChange }) => {
    let current = initialState ?? workspaceLayout(0);
    order?.push("layout:init");
    return {
      syncAfterRender() { order?.push("layout:sync"); },
      replaceAuthoritativeState(next) {
        current = next;
        order?.push(`layout:replace:${next.revision}`);
        onStateChange?.({
          preferredLeftWidthPx: next.preferred_left_width_px,
          preferredRightWidthPx: next.preferred_right_width_px,
          revision: next.revision,
          persisted: next.persisted,
          diagnostic: null,
        });
      },
      getBootstrapProbe() {
        return {
          appliedLayoutRevision: current.revision,
          appliedPreferredPair: {
            preferredLeftWidthPx: current.preferred_left_width_px,
            preferredRightWidthPx: current.preferred_right_width_px,
          },
          appliedCollapsedPair: {
            leftCollapsed: current.left_collapsed === true,
            rightCollapsed: current.right_collapsed === true,
          },
          layoutMode: "three-pane",
          shellFrame: { x: 0, y: 0, width: 1200, height: 768 },
          paneFrames: {
            left: { x: 0, y: 0, width: 304, height: 768 },
            center: { x: 316, y: 0, width: 504, height: 768 },
            right: { x: 832, y: 0, width: 368, height: 768 },
          },
          activeSeparatorCount: 2,
          disabledSeparatorCount: 0,
        };
      },
      release() { onRelease(); },
    };
  };
}

function readyRoot() {
  return {
    childElementCount: 1,
    textContent: "mounted shell",
    querySelector(selector) {
      return selector === ".desktop-app" ? {
        hidden: false,
        getBoundingClientRect() { return { width: 1200, height: 768 }; },
      } : null;
    },
  };
}

test("bootstrap applies resolved mode before mount and completes before domain restore", async () => {
  const order = [];
  const documentRoot = {
    clientWidth: 1200,
    clientHeight: 800,
    dataset: new Proxy({}, {
      set(target, key, value) {
        order.push(`attr:${value}`);
        target[key] = value;
        return true;
      },
    }),
  };
  const backend = {
    async getBootstrapState() {
      order.push("get");
      return {
        appearance: appearance("System", "Dark", 3),
        workspace_layout: workspaceLayout(7),
        typography: typography("Default", 9),
        app_version: "0.1.0",
      };
    },
    async completeBootstrap(appearanceRevision, layoutRevision, typographyRevision, probe) {
      order.push(`complete:${appearanceRevision}:${layoutRevision}:${typographyRevision}:${probe.childElementCount}:${probe.desktopAppPresent}:${probe.appliedTypographyRevision}:${probe.appliedTypographyPreset}`);
      return {
        instruction: "Show",
        appearance: appearance("System", "Dark", 3),
        workspace_layout: workspaceLayout(7),
        typography: typography("Default", 9),
      };
    },
  };

  const result = await bootstrapAppearance({
    backend,
    documentRoot,
    root: {
      childElementCount: 1,
      textContent: "mounted shell",
      querySelector(selector) {
        return selector === ".desktop-app" ? {
          hidden: false,
          getBoundingClientRect() { return { width: 1200, height: 800 }; },
        } : null;
      },
    },
    createLayoutController: createLayoutControllerStub(order),
    mount() {
      order.push("mount");
      return {
        startAfterBootstrap() {
          order.push("domain");
        },
      };
    },
  });

  assert.deepEqual(order, [
    "get",
    "attr:dark",
    "attr:Default",
    "layout:init",
    "mount",
    "layout:sync",
    "complete:3:7:9:1:true:9:Default",
    "domain",
  ]);
  assert.equal(result.appearance.revision, 3);
  assert.equal(result.workspaceLayout.revision, 7);
  assert.equal(result.typography.revision, 9);
});

test("stale completion reapplies latest mode and retries without remounting", async () => {
  const applied = [];
  let completionCalls = 0;
  let mountCalls = 0;
  const documentRoot = {
    clientWidth: 1200,
    clientHeight: 768,
    dataset: new Proxy({}, {
      set(target, key, value) {
        applied.push(value);
        target[key] = value;
        return true;
      },
    }),
  };
  const backend = {
    async getBootstrapState() {
      return {
        appearance: appearance("Dark", "Dark", 1),
        workspace_layout: workspaceLayout(1),
        typography: typography("Default", 1),
        app_version: "0.1.0",
      };
    },
    async completeBootstrap(revision, layoutRevision, typographyRevision, probe) {
      assert.equal(probe.layoutVisible, true);
      assert.equal(probe.desktopWidth, 1200);
      assert.equal(probe.desktopHeight, 768);
      completionCalls += 1;
      if (revision === 1) {
        assert.equal(layoutRevision, 1);
        assert.equal(typographyRevision, 1);
        return {
          instruction: "Refresh",
          appearance: appearance("System", "Light", 2),
          workspace_layout: workspaceLayout(2, 320, 400),
          typography: typography("Large", 2),
        };
      }
      assert.equal(layoutRevision, 2);
      assert.equal(typographyRevision, 2);
      return {
        instruction: "Show",
        appearance: appearance("System", "Light", 2),
        workspace_layout: workspaceLayout(2, 320, 400),
        typography: typography("Large", 2),
      };
    },
  };

  const result = await bootstrapAppearance({
    backend,
    documentRoot,
    root: readyRoot(),
    createLayoutController: createLayoutControllerStub(),
    mount() {
      mountCalls += 1;
      return { startAfterBootstrap() {} };
    },
  });

  assert.deepEqual(applied, ["dark", "Default", "light", "Large"]);
  assert.equal(completionCalls, 2);
  assert.equal(mountCalls, 1);
  assert.equal(result.appearance.revision, 2);
  assert.equal(result.typography.revision, 2);
});

test("bootstrap failure or invalid resolved mode never mounts the shell", async () => {
  let mountCalls = 0;
  const mount = () => {
    mountCalls += 1;
  };

  await assert.rejects(
    bootstrapAppearance({
      backend: { async getBootstrapState() { throw new Error("offline"); } },
      documentRoot: { dataset: {} },
      root: {},
      mount,
    }),
    /offline/,
  );
  await assert.rejects(
    bootstrapAppearance({
      backend: {
        async getBootstrapState() {
          return { appearance: appearance("System", "System", 0), app_version: "0.1.0" };
        },
      },
      documentRoot: { dataset: {} },
      root: {},
      mount,
    }),
    /resolved appearance mode/i,
  );
  assert.equal(mountCalls, 0);
});

test("window focus reconciles a missed System event from bootstrap state", async () => {
  let current = appearance("System", "Dark", 1);
  let focusListener;
  const appliedEvents = [];
  const documentRoot = { clientWidth: 1200, clientHeight: 768, dataset: {} };
  const backend = {
    async getBootstrapState() {
      return { appearance: current, workspace_layout: workspaceLayout(1), app_version: "0.1.0" };
    },
    async completeBootstrap(_revision, _layoutRevision, _typographyRevision, probe) {
      assert.equal(probe.layoutVisible, true);
      return { instruction: "Show", appearance: current, workspace_layout: workspaceLayout(1) };
    },
  };
  const focusTarget = {
    addEventListener(type, listener) {
      assert.equal(type, "focus");
      focusListener = listener;
    },
  };

  await bootstrapAppearance({
    backend,
    documentRoot,
    root: readyRoot(),
    focusTarget,
    createLayoutController: createLayoutControllerStub(),
    mount() {
      return {
        startAfterBootstrap() {},
        applyAppearanceChanged(event) { appliedEvents.push(event); },
      };
    },
  });
  current = appearance("System", "Light", 2);
  focusListener();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(documentRoot.dataset.resolvedMode, "light");
  assert.equal(appliedEvents.at(-1).revision, 2);
});

test("bootstrap dispose closes listeners and the mounted app exactly once", async () => {
  let destroyCalls = 0;
  let unlistenCalls = 0;
  let removeFocusCalls = 0;
  let releaseLayoutCalls = 0;
  const focusTarget = {
    addEventListener() {},
    removeEventListener(type) {
      assert.equal(type, "focus");
      removeFocusCalls += 1;
    },
  };
  const result = await bootstrapAppearance({
    backend: {
      async getBootstrapState() {
        return {
          appearance: appearance("System", "Dark", 1),
          workspace_layout: workspaceLayout(1),
          app_version: "0.1.0",
        };
      },
      async onAppearanceChanged() {
        return () => { unlistenCalls += 1; };
      },
      async completeBootstrap() {
        return {
          instruction: "Show",
          appearance: appearance("System", "Dark", 1),
          workspace_layout: workspaceLayout(1),
        };
      },
    },
    documentRoot: { clientWidth: 1200, clientHeight: 768, dataset: {} },
    root: readyRoot(),
    focusTarget,
    createLayoutController: createLayoutControllerStub(null, () => { releaseLayoutCalls += 1; }),
    mount() {
      return {
        startAfterBootstrap() {},
        destroy() { destroyCalls += 1; },
      };
    },
  });

  result.dispose();
  result.dispose();
  assert.equal(destroyCalls, 1);
  assert.equal(unlistenCalls, 1);
  assert.equal(removeFocusCalls, 1);
  assert.equal(releaseLayoutCalls, 1);
});

test("bootstrap failure after mount destroys the partial app", async () => {
  let destroyCalls = 0;
  await assert.rejects(
    bootstrapAppearance({
      backend: {
        async getBootstrapState() {
          return {
            appearance: appearance("System", "Dark", 1),
            workspace_layout: workspaceLayout(1),
            app_version: "0.1.0",
          };
        },
        async completeBootstrap() {
          return {
            instruction: "Invalid",
            appearance: appearance("System", "Dark", 1),
            workspace_layout: workspaceLayout(1),
          };
        },
      },
      documentRoot: { clientWidth: 1200, clientHeight: 768, dataset: {} },
      root: readyRoot(),
      createLayoutController: createLayoutControllerStub(),
      mount() {
        return { destroy() { destroyCalls += 1; } };
      },
    }),
    /Invalid bootstrap completion instruction/,
  );
  assert.equal(destroyCalls, 1);
});
