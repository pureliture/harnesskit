import assert from "node:assert/strict";
import test from "node:test";

import { bindTypographyMenu } from "../typography/controller.js";

function createElement({ preset = null } = {}) {
  const attributes = new Map();
  const listeners = new Map();
  return {
    dataset: preset ? { typographyPreset: preset } : {},
    hidden: true,
    focused: false,
    setAttribute(name, value) {
      attributes.set(name, String(value));
    },
    getAttribute(name) {
      return attributes.get(name) ?? null;
    },
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    removeEventListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
    async dispatch(type, event = {}) {
      return listeners.get(type)?.({
        key: event.key,
        target: event.target ?? this,
        currentTarget: this,
        preventDefault() {},
        stopPropagation() {},
      });
    },
    focus() {
      this.focused = true;
    },
    contains(candidate) {
      return candidate === this;
    },
  };
}

function createHarness() {
  const trigger = createElement();
  const menu = createElement();
  const options = ["Small", "Default", "Large"].map((preset) => createElement({ preset }));
  const documentTarget = createElement();
  return { trigger, menu, options, documentTarget };
}

test("typography menu exposes current preset, arrow navigation, selection focus return, and outside close", async () => {
  const harness = createHarness();
  const selected = [];
  const controller = bindTypographyMenu({
    ...harness,
    onSelect: async (preset) => selected.push(preset),
  });

  controller.sync({ preset: "Default" });

  assert.equal(harness.trigger.getAttribute("aria-label"), "글자 크기: 기본");
  assert.equal(harness.trigger.getAttribute("aria-expanded"), "false");
  assert.equal(harness.menu.hidden, true);
  assert.equal(harness.options[1].getAttribute("aria-checked"), "true");

  await harness.trigger.dispatch("keydown", { key: "ArrowDown" });
  assert.equal(harness.menu.hidden, false);
  assert.equal(harness.trigger.getAttribute("aria-expanded"), "true");
  assert.equal(harness.options[1].focused, true);

  await harness.menu.dispatch("keydown", { key: "ArrowDown", target: harness.options[1] });
  assert.equal(harness.options[2].focused, true);

  await harness.menu.dispatch("keydown", { key: "Enter", target: harness.options[2] });
  assert.deepEqual(selected, ["Large"]);
  assert.equal(harness.menu.hidden, true);
  assert.equal(harness.trigger.focused, true);

  controller.sync({ preset: "Large" });
  assert.equal(harness.trigger.getAttribute("aria-label"), "글자 크기: 크게");
  assert.equal(harness.trigger.getAttribute("title"), "글자 크기: 크게");
  assert.equal(harness.options[2].getAttribute("aria-checked"), "true");

  harness.trigger.focused = false;
  await harness.trigger.dispatch("click");
  const outside = createElement();
  outside.focus();
  await harness.documentTarget.dispatch("pointerdown", { target: outside });
  assert.equal(harness.menu.hidden, true);
  assert.equal(harness.trigger.focused, true);

  controller.release();
});

test("typography menu closes on Escape and Tab without trapping focus", async () => {
  const harness = createHarness();
  const controller = bindTypographyMenu({ ...harness, onSelect: async () => {} });
  controller.sync({ preset: "Small" });

  await harness.trigger.dispatch("click");
  await harness.menu.dispatch("keydown", { key: "Escape", target: harness.options[0] });
  assert.equal(harness.menu.hidden, true);
  assert.equal(harness.trigger.focused, true);

  harness.trigger.focused = false;
  await harness.trigger.dispatch("click");
  await harness.menu.dispatch("keydown", { key: "Tab", target: harness.options[0] });
  assert.equal(harness.menu.hidden, true);
  assert.equal(harness.trigger.focused, false);

  controller.release();
});

test("typography menu release detaches every trigger and option listener", async () => {
  const harness = createHarness();
  const selected = [];
  const controller = bindTypographyMenu({
    ...harness,
    onSelect: async (preset) => selected.push(preset),
  });
  controller.sync({ preset: "Default" });
  controller.release();

  await harness.trigger.dispatch("click");
  await harness.options[2].dispatch("click");

  assert.equal(harness.menu.hidden, true);
  assert.deepEqual(selected, []);
});
