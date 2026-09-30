import assert from "node:assert/strict";
import test from "node:test";

import {
  applyResolvedMode,
  patchAppearanceControls,
  reduceAppearanceChanged,
  reduceAppearanceResponse,
  requestAppearanceMode,
} from "../appearance/controller.js";
import { createInitialState } from "../app-shell.js";

test("newer appearance events replace appearance without touching domain state", () => {
  const state = createInitialState({
    appearance: {
      logical_mode: "System",
      resolved_mode: "Light",
      revision: 4,
      persisted: true,
    },
    repo: { checkoutId: "checkout-7", checkoutPath: "/tmp/harnesskit" },
    scan: { phase: "success", inventory: { items: [] } },
    tree: { filter: "skill", selectedId: "component-1" },
    ui: { activePanel: "install" },
    install: { phase: "preview-ready", confirmed: true },
  });
  const event = {
    logical_mode: "Dark",
    resolved_mode: "Dark",
    revision: 5,
    persisted: false,
    source: "User",
  };

  const next = reduceAppearanceChanged(state, event);

  assert.deepEqual(next.appearance, {
    logical_mode: "Dark",
    resolved_mode: "Dark",
    revision: 5,
    persisted: false,
    diagnostic: null,
  });
  for (const key of ["repo", "scan", "tree", "ui", "install"]) {
    assert.deepEqual(next[key], state[key]);
    assert.equal(next[key], state[key]);
  }
});

test("user mode request invokes only the appearance command", async () => {
  const calls = [];
  const state = createInitialState({
    appearance: {
      logical_mode: "System",
      resolved_mode: "Light",
      revision: 2,
      persisted: true,
    },
  });
  const backend = {
    async setAppearanceMode(logicalMode) {
      calls.push(["appearance", logicalMode]);
      return {
        appearance: {
          logical_mode: logicalMode,
          resolved_mode: "Dark",
          revision: 3,
          persisted: false,
        },
        diagnostic: {
          code: "appearance_preference_write_failed",
          safe_message: "설정 저장 실패",
        },
      };
    },
    async loadSotSnapshot() { calls.push(["sot"]); },
    async startLocalScan() { calls.push(["local"]); },
    async previewInstall() { calls.push(["install"]); },
  };
  const documentRoot = { dataset: {} };

  const response = await requestAppearanceMode({ logicalMode: "Dark", backend });
  const next = reduceAppearanceResponse(state, response);
  applyResolvedMode(documentRoot, next.appearance);

  assert.deepEqual(calls, [["appearance", "Dark"]]);
  assert.equal(documentRoot.dataset.resolvedMode, "dark");
  assert.equal(next.appearance.logical_mode, "Dark");
  assert.equal(next.appearance.diagnostic.safe_message, "설정 저장 실패");
  for (const key of ["repo", "scan", "tree", "ui", "install"]) {
    assert.equal(next[key], state[key]);
  }
});

test("late command responses never overwrite a newer event revision", () => {
  const state = createInitialState({
    appearance: {
      logical_mode: "Light",
      resolved_mode: "Light",
      revision: 8,
      persisted: true,
    },
  });
  const stale = reduceAppearanceResponse(state, {
    appearance: {
      logical_mode: "Dark",
      resolved_mode: "Dark",
      revision: 7,
      persisted: true,
    },
    diagnostic: null,
  });

  assert.equal(stale, state);
});

test("unpersisted diagnostic survives a newer System event", () => {
  const state = createInitialState({
    appearance: {
      logical_mode: "System",
      resolved_mode: "Light",
      revision: 3,
      persisted: false,
      diagnostic: {
        code: "appearance_preference_write_failed",
        safe_message: "설정 저장 실패",
      },
    },
  });
  const next = reduceAppearanceChanged(state, {
    logical_mode: "System",
    resolved_mode: "Dark",
    revision: 4,
    persisted: false,
    source: "System",
  });

  assert.equal(next.appearance.diagnostic.safe_message, "설정 저장 실패");
});

test("appearance DOM patch changes controls only and preserves workbench scroll", () => {
  const controls = ["System", "Light", "Dark"].map((value) => ({ value, checked: false }));
  const notice = { hidden: true, textContent: "" };
  const workbench = { scrollTop: 412 };
  const root = {
    querySelectorAll(selector) {
      assert.equal(selector, 'input[name="appearance-mode"]');
      return controls;
    },
    querySelector(selector) {
      if (selector === ".appearance-save-state") return notice;
      if (selector === "#workbench") return workbench;
      return null;
    },
  };

  patchAppearanceControls(root, {
    logical_mode: "Dark",
    resolved_mode: "Dark",
    revision: 6,
    persisted: false,
    diagnostic: { safe_message: "설정 저장 실패" },
  });

  assert.deepEqual(controls.map((control) => control.checked), [false, false, true]);
  assert.equal(notice.hidden, false);
  assert.equal(notice.textContent, "설정 저장 실패");
  assert.equal(workbench.scrollTop, 412);
});

test("stale or equal revision events preserve the entire state object", () => {
  const state = createInitialState({
    appearance: {
      logical_mode: "Dark",
      resolved_mode: "Dark",
      revision: 9,
      persisted: true,
    },
  });

  for (const revision of [8, 9]) {
    const next = reduceAppearanceChanged(state, {
      logical_mode: "Light",
      resolved_mode: "Light",
      revision,
      persisted: true,
      source: "System",
    });
    assert.equal(next, state);
  }
});

test("resolved mode application accepts only Light or Dark", () => {
  const root = { dataset: {} };

  applyResolvedMode(root, { resolved_mode: "Light" });
  assert.equal(root.dataset.resolvedMode, "light");
  applyResolvedMode(root, { resolved_mode: "Dark" });
  assert.equal(root.dataset.resolvedMode, "dark");
  assert.throws(
    () => applyResolvedMode(root, { resolved_mode: "System" }),
    /resolved appearance mode/i,
  );
});
