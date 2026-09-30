import assert from "node:assert/strict";
import test from "node:test";

import { mountApp } from "../app.js";

const LAST_COMPONENT_ID = "harnesskit.skill.zeta";

const snapshot = Object.freeze({
  snapshot_id: "snapshot-tree-end",
  checkout_summary: {
    source_revision: "abc123",
    branch: "main",
    detached: false,
    dirty: false,
    recent_commits: [],
  },
  components: [
    {
      component_id: "harnesskit.agent.alpha",
      kind: "agent",
      status: "draft",
      title: "Alpha agent",
      summary: null,
      domain: "core",
      targets: [],
      provenance: {},
      owned_files: [],
      profile_ids: ["harnesskit.profile.engineering"],
    },
    {
      component_id: "harnesskit.hook.middle",
      kind: "hook",
      status: "draft",
      title: "Middle hook",
      summary: null,
      domain: "core",
      targets: [],
      provenance: {},
      owned_files: [],
      profile_ids: [],
    },
    {
      component_id: LAST_COMPONENT_ID,
      kind: "skill",
      status: "draft",
      title: "Zeta skill",
      summary: null,
      domain: "core",
      targets: [],
      provenance: {},
      owned_files: [],
      profile_ids: [],
    },
  ],
  profiles: [{
    profile_id: "harnesskit.profile.engineering",
    status: "draft",
    title: "Engineering",
    summary: null,
    component_ids: ["harnesskit.agent.alpha"],
  }],
  unprofiled_component_ids: ["harnesskit.hook.middle", LAST_COMPONENT_ID],
  relations: [],
  graph_projection: {
    logical_width: 1200,
    logical_height: 360,
    view_box: [0, 0, 1200, 360],
    content_extent: { min_x: 48, min_y: 32, max_x: 392, max_y: 244 },
    nodes: [],
    edges: [],
  },
  navigation_projection: { all_component_ids: [], groups: [] },
  issues: [],
});

function createClassList(classNames = "") {
  const values = new Set(classNames.split(/\s+/).filter(Boolean));
  return {
    contains: (className) => values.has(className),
    toggle(className, force) {
      if (force) values.add(className);
      else values.delete(className);
    },
  };
}

function createTreeControl(ownerDocument, tag, scrollCalls) {
  const attribute = (name) => tag.match(new RegExp(`${name}="([^"]*)"`))?.[1] ?? null;
  const attributes = new Map([
    ["role", attribute("role")],
    ["tabindex", attribute("tabindex")],
    ["aria-selected", attribute("aria-selected")],
    ["aria-expanded", attribute("aria-expanded")],
    ["aria-level", attribute("aria-level")],
  ].filter(([, value]) => value !== null));
  const componentId = attribute("data-component-id");
  const treeNodeId = attribute("data-sot-tree-node");

  return {
    inAppRoot: true,
    dataset: {
      componentId: componentId ?? undefined,
      sotTreeNode: treeNodeId ?? undefined,
      sotTreeProfile: attribute("data-sot-tree-profile") ?? undefined,
    },
    classList: createClassList(attribute("class") ?? ""),
    getAttribute(name) {
      return attributes.get(name) ?? null;
    },
    setAttribute(name, value) {
      attributes.set(name, String(value));
    },
    querySelector() {
      return null;
    },
    closest(selector) {
      if (selector.includes("[role=treeitem]")) return this;
      if (componentId && selector.includes("[data-component-id]")) return this;
      if (treeNodeId && selector.includes("[data-sot-tree-node]")) return this;
      return null;
    },
    focus() {
      ownerDocument.activeElement = this;
    },
    scrollIntoView(options) {
      scrollCalls.push({ componentId, treeNodeId, options });
    },
  };
}

function createMountRoot() {
  const scrollCalls = [];
  const liveRegions = [];
  const body = {
    appendChild(node) {
      liveRegions.push(node);
    },
  };
  const ownerDocument = {
    activeElement: body,
    body,
    createElement() {
      const attributes = new Map();
      return {
        dataset: {},
        className: "",
        textContent: "",
        setAttribute(name, value) {
          attributes.set(name, String(value));
        },
        getAttribute(name) {
          return attributes.get(name) ?? null;
        },
        remove() {
          const index = liveRegions.indexOf(this);
          if (index >= 0) liveRegions.splice(index, 1);
        },
      };
    },
  };
  const listeners = new Map();
  const shell = {
    addEventListener(type, listener) {
      const current = listeners.get(type) ?? [];
      current.push(listener);
      listeners.set(type, current);
    },
    dispatch(type, event) {
      const originalPreventDefault = event.preventDefault;
      const dispatched = {
        ...event,
        currentTarget: this,
        defaultPrevented: false,
        preventDefault() {
          this.defaultPrevented = true;
          originalPreventDefault?.();
        },
      };
      for (const listener of listeners.get(type) ?? []) listener(dispatched);
      return dispatched;
    },
  };
  let treeItems = [];
  const parseTreeItems = (markup) => {
    treeItems = [...markup.matchAll(/<button(?=[^>]*role="treeitem")[^>]*>/g)]
      .map(([tag]) => createTreeControl(ownerDocument, tag, scrollCalls));
  };
  const treeRegion = {
    set innerHTML(markup) {
      parseTreeItems(markup);
    },
  };
  const treeScroll = {
    scrollTop: 0,
    addEventListener() {},
  };
  const inertRegion = { innerHTML: "" };

  return {
    ownerDocument,
    shell,
    scrollCalls,
    get treeItems() {
      return treeItems;
    },
    set innerHTML(markup) {
      listeners.clear();
      parseTreeItems(markup);
    },
    querySelectorAll(selector) {
      if (selector.includes("[role=treeitem]")) return treeItems;
      if (selector === "[data-component-id]") {
        return treeItems.filter((item) => item.dataset.componentId);
      }
      return [];
    },
    querySelector(selector) {
      if (selector === ".desktop-app") return shell;
      if (selector === "[data-sot-tree]") return treeRegion;
      if (selector === "[data-sot-tree-scroll]") return treeScroll;
      if (selector === "[data-sot-inspector]" || selector === "[data-sot-install-host]") {
        return inertRegion;
      }
      return null;
    },
  };
}

async function mountReadyApp() {
  const root = createMountRoot();
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-tree-end", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  assert.equal(app.getState().sot.phase, "ready");
  return { app, root };
}

test("SoT tree End selects, focuses, and reveals the last visible component", async (t) => {
  const startingRows = [
    { label: "root treeitem", find: (item) => item.dataset.sotTreeNode === "root" },
    {
      label: "component row",
      find: (item) => item.dataset.componentId === "harnesskit.agent.alpha",
    },
  ];

  for (const startingRow of startingRows) {
    await t.test(startingRow.label, async () => {
      const { app, root } = await mountReadyApp();
      const start = root.treeItems.find(startingRow.find);
      assert.ok(start);
      assert.ok(root.treeItems.some((item) => item.dataset.sotTreeNode === "capabilities.yml"));

      const event = root.shell.dispatch("keydown", { key: "End", target: start });

      const selected = root.treeItems.find(
        (item) => item.dataset.componentId === LAST_COMPONENT_ID,
      );
      assert.equal(event.defaultPrevented, true);
      assert.equal(app.getState().sotView.treeSelectedId, LAST_COMPONENT_ID);
      assert.equal(app.getState().sotView.selectedComponentId, LAST_COMPONENT_ID);
      assert.equal(selected?.getAttribute("aria-selected"), "true");
      assert.equal(root.ownerDocument.activeElement, selected);
      assert.equal(
        root.scrollCalls.some((call) => call.componentId === LAST_COMPONENT_ID),
        true,
      );
    });
  }
});
