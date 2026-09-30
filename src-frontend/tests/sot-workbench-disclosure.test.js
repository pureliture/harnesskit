import assert from "node:assert/strict";
import test from "node:test";

import { bindSotWorkbenchDisclosure } from "../sot-workbench-disclosure.js";

function dataAttributeName(property) {
  return `data-${property.replace(/[A-Z]/g, (character) => `-${character.toLowerCase()}`)}`;
}

class FakeElement {
  constructor(name, operations) {
    this.name = name;
    this.operations = operations;
    this.attributes = new Map();
    this.listeners = new Map();
    this.containedElements = new Set();
    this.textContent = "";
    this._hidden = false;
    this.dataset = new Proxy({}, {
      set: (target, property, value) => {
        const normalized = String(value);
        target[property] = normalized;
        this.attributes.set(dataAttributeName(property), normalized);
        return true;
      },
    });
  }

  get hidden() {
    return this._hidden;
  }

  set hidden(value) {
    const normalized = Boolean(value);
    if (this._hidden !== normalized) {
      this.operations.push(`${this.name}:hidden:${normalized}`);
    }
    this._hidden = normalized;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === "hidden") this.hidden = false;
  }

  toggleAttribute(name, force) {
    const enabled = force ?? !this.attributes.has(name);
    if (name === "hidden") this.hidden = enabled;
    if (enabled) this.attributes.set(name, "");
    else this.attributes.delete(name);
    return enabled;
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  removeEventListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
  }

  matches(selector) {
    return this.attributes.has(selector.slice(1, -1));
  }

  closest(selector) {
    return this.matches(selector) ? this : null;
  }

  contains(element) {
    return this.containedElements.has(element);
  }

  focus() {
    this.operations.push(`${this.name}:focus`);
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
}

function createDisclosureHarness() {
  const operations = [];
  const changes = [];
  const animationFrames = new Map();
  let nextAnimationFrameId = 1;
  const ownerDocument = {
    activeElement: null,
    defaultView: {
      requestAnimationFrame(callback) {
        const id = nextAnimationFrameId;
        nextAnimationFrameId += 1;
        animationFrames.set(id, callback);
        operations.push(`frame:request:${id}`);
        return id;
      },
      cancelAnimationFrame(id) {
        if (animationFrames.delete(id)) operations.push(`frame:cancel:${id}`);
      },
    },
  };
  const root = new FakeElement("root", operations);
  const mapToggle = new FakeElement("map-toggle", operations);
  const matrixToggle = new FakeElement("matrix-toggle", operations);
  const mapBody = new FakeElement("map-body", operations);
  const matrixBody = new FakeElement("matrix-body", operations);
  const scrollOwner = {
    scrollTop: 0,
    scrollHeight: 300,
    clientHeight: 100,
  };
  [root, mapToggle, matrixToggle, mapBody, matrixBody].forEach((element) => {
    element.ownerDocument = ownerDocument;
  });

  mapToggle.setAttribute("data-sot-graph-toggle", "");
  matrixToggle.setAttribute("data-sot-matrix-toggle", "");
  mapBody.setAttribute("id", "component-map-body");
  matrixBody.setAttribute("id", "profile-matrix-body");

  const selectors = new Map([
    ["[data-sot-graph-toggle]", mapToggle],
    ["[data-sot-matrix-toggle]", matrixToggle],
    ["#component-map-body", mapBody],
    ["#profile-matrix-body", matrixBody],
    [".pane--workbench", scrollOwner],
  ]);
  root.querySelector = (selector) => selectors.get(selector) ?? null;
  root.querySelectorAll = (selector) => selectors.has(selector) ? [selectors.get(selector)] : [];
  root.contains = (element) => [...selectors.values()].includes(element);
  root.dispatchClick = (target) => {
    root.listeners.get("click")?.({ target, preventDefault() {} });
  };

  const graphController = {
    setExpanded(expanded) {
      operations.push(`graph:${expanded}`);
    },
    refreshAfterLayout() {
      operations.push("graph:refresh");
    },
  };

  return {
    root,
    mapToggle,
    matrixToggle,
    mapBody,
    matrixBody,
    scrollOwner,
    ownerDocument,
    graphController,
    animationFrames,
    operations,
    changes,
    flushAnimationFrame() {
      const pending = [...animationFrames.entries()];
      animationFrames.clear();
      pending.forEach(([, callback]) => callback());
    },
    bind(state = { componentMap: true, profileMatrix: false }) {
      const binding = bindSotWorkbenchDisclosure(root, {
        state,
        graphController,
        onChange(nextState) {
          changes.push(nextState);
        },
      });
      binding.sync(state);
      return binding;
    },
  };
}

test("initial sync projects disclosure state to bodies, toggles, and workbench data", () => {
  const harness = createDisclosureHarness();

  harness.bind();

  assert.equal(harness.mapBody.hidden, false);
  assert.equal(harness.matrixBody.hidden, true);
  assert.equal(harness.mapToggle.getAttribute("aria-expanded"), "true");
  assert.equal(harness.mapToggle.textContent, "⌃ 그래프 접기");
  assert.equal(harness.matrixToggle.getAttribute("aria-expanded"), "false");
  assert.equal(harness.matrixToggle.textContent, "⌄ 매트릭스 펼치기");
  assert.equal(harness.root.getAttribute("data-component-map-expanded"), "true");
  assert.equal(harness.root.getAttribute("data-profile-matrix-expanded"), "false");
});

test("Map sync suspends before hide and resumes with one refresh on the next visible frame", () => {
  const harness = createDisclosureHarness();
  const binding = harness.bind();
  harness.operations.length = 0;

  binding.sync({ componentMap: false, profileMatrix: false });
  assert.ok(
    harness.operations.indexOf("graph:false") < harness.operations.indexOf("map-body:hidden:true"),
    `collapse order was ${harness.operations.join(", ")}`,
  );

  harness.operations.length = 0;
  binding.sync({ componentMap: true, profileMatrix: false });
  assert.equal(harness.mapBody.hidden, false);
  assert.deepEqual(
    harness.operations.filter((operation) => operation.startsWith("graph:")),
    [],
    `graph resumed before the visible frame: ${harness.operations.join(", ")}`,
  );
  assert.equal(harness.animationFrames.size, 1);

  harness.flushAnimationFrame();
  assert.ok(
    harness.operations.indexOf("map-body:hidden:false") < harness.operations.indexOf("graph:true"),
    `expand order was ${harness.operations.join(", ")}`,
  );
  assert.deepEqual(
    harness.operations.filter((operation) => operation.startsWith("graph:")),
    ["graph:true", "graph:refresh"],
  );
});

test("pending Map resume is cancelled by rapid collapse and release", () => {
  const collapseHarness = createDisclosureHarness();
  const collapseBinding = collapseHarness.bind();

  collapseBinding.sync({ componentMap: false, profileMatrix: false });
  collapseHarness.operations.length = 0;
  collapseBinding.sync({ componentMap: true, profileMatrix: false });
  collapseBinding.sync({ componentMap: false, profileMatrix: false });
  collapseHarness.flushAnimationFrame();

  assert.equal(collapseHarness.animationFrames.size, 0);
  assert.deepEqual(
    collapseHarness.operations.filter((operation) => operation === "graph:true" || operation === "graph:refresh"),
    [],
  );

  const releaseHarness = createDisclosureHarness();
  const releaseBinding = releaseHarness.bind();
  releaseBinding.sync({ componentMap: false, profileMatrix: false });
  releaseHarness.operations.length = 0;
  releaseBinding.sync({ componentMap: true, profileMatrix: false });
  releaseBinding.release();
  releaseHarness.flushAnimationFrame();

  assert.equal(releaseHarness.animationFrames.size, 0);
  assert.deepEqual(
    releaseHarness.operations.filter((operation) => operation === "graph:true" || operation === "graph:refresh"),
    [],
  );
});

test("Matrix click publishes only its key without touching the graph, and release is idempotent", () => {
  const harness = createDisclosureHarness();
  const binding = harness.bind();
  harness.operations.length = 0;

  harness.root.dispatchClick(harness.matrixToggle);

  assert.deepEqual(harness.changes, [{ componentMap: true, profileMatrix: true }]);
  assert.deepEqual(harness.operations.filter((operation) => operation.startsWith("graph:")), []);

  binding.release();
  binding.release();
  harness.root.dispatchClick(harness.matrixToggle);
  assert.equal(harness.changes.length, 1);
});

test("collapse moves focus to its toggle before hiding the active body", () => {
  const harness = createDisclosureHarness();
  const binding = harness.bind();
  const focusedNode = {};
  harness.mapBody.containedElements.add(focusedNode);
  harness.ownerDocument.activeElement = focusedNode;
  harness.operations.length = 0;

  binding.sync({ componentMap: false, profileMatrix: false });

  assert.equal(harness.ownerDocument.activeElement, harness.mapToggle);
  assert.ok(
    harness.operations.indexOf("map-toggle:focus") < harness.operations.indexOf("map-body:hidden:true"),
    `focus/hide order was ${harness.operations.join(", ")}`,
  );
});

test("outer workbench scroll is preserved within its valid range and clamped when invalid", () => {
  const harness = createDisclosureHarness();
  const binding = harness.bind();

  harness.scrollOwner.scrollTop = 150;
  binding.sync({ componentMap: false, profileMatrix: false });
  assert.equal(harness.scrollOwner.scrollTop, 150);

  harness.scrollOwner.scrollTop = 260;
  binding.sync({ componentMap: true, profileMatrix: false });
  assert.equal(harness.scrollOwner.scrollTop, 200);
});

test("Matrix collapse and re-expand restores its pre-collapse outer scroll within the new maximum", () => {
  const harness = createDisclosureHarness();
  let matrixHidden = false;
  Object.defineProperty(harness.matrixBody, "hidden", {
    configurable: true,
    get() {
      return matrixHidden;
    },
    set(value) {
      matrixHidden = Boolean(value);
      harness.scrollOwner.scrollHeight = matrixHidden ? 100 : 600;
    },
  });
  const binding = harness.bind({ componentMap: true, profileMatrix: true });
  harness.scrollOwner.scrollTop = 260;

  binding.sync({ componentMap: true, profileMatrix: false });
  assert.equal(harness.scrollOwner.scrollTop, 0);

  binding.sync({ componentMap: true, profileMatrix: true });
  assert.equal(harness.scrollOwner.scrollTop, 260);
});

test("invalid disclosure state fails with a safe contract code", () => {
  const harness = createDisclosureHarness();
  const binding = harness.bind();

  assert.throws(
    () => binding.sync({ componentMap: "expanded", profileMatrix: false }),
    (error) => error?.code === "invalid_disclosure_state"
      && error?.message === "invalid_disclosure_state",
  );
});

test("graph controller must expose the explicit layout refresh contract", () => {
  const harness = createDisclosureHarness();
  harness.graphController.refreshAfterLayout = undefined;

  assert.throws(
    () => harness.bind(),
    (error) => error?.code === "invalid_graph_controller"
      && error?.message === "invalid_graph_controller",
  );
});
