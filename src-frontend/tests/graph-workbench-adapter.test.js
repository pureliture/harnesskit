import assert from "node:assert/strict";
import test from "node:test";

import {
  createGraphWorkbenchAdapter,
  graphInputFromSotSnapshot,
  graphPresentationFromSotView,
  graphThemeTokensFromElement,
} from "../graph/graph-workbench-adapter.js";

function snapshotFixture() {
  return {
    components: [{ component_id: "api", title: "Public API", kind: "skill", status: "active" }],
    profiles: [{
      profile_id: "engineering",
      title: "Engineering",
      status: "active",
      component_ids: ["api"],
    }],
    workflows: [{
      workflow_id: "release",
      title: "Release workflow",
      status: "active",
      steps: [{ ordinal: 1, step_id: "release-api" }],
    }],
    unprofiled_component_ids: [],
    graph_projection: {
      schema_version: 2,
      snapshot_id: "snapshot-adapter",
      layout_seed: "seed-adapter",
      nodes: [
        {
          node_type: "relation",
          node_id: "profile:engineering",
          relation_kind: "profile",
          canonical_id: "engineering",
          name: "Engineering",
          exact_count: 1,
        },
        {
          node_type: "relation",
          node_id: "workflow:release",
          relation_kind: "workflow",
          canonical_id: "release",
          name: "Release workflow",
          exact_count: 2,
          anchor_ordinal: 1,
          size_scale: 1.1,
        },
        {
          node_type: "component",
          node_id: "component:api",
          component_id: "api",
          kind: "service",
          domain: "public",
          relation_degree: 1,
          profile_ids: ["engineering"],
          workflow_ids: ["release"],
        },
      ],
      links: [
        {
          link_id: "profile:engineering->component:api",
          source_node_id: "profile:engineering",
          target_node_id: "component:api",
          semantic: "profile-membership",
          directionality: "unordered",
          profile_node_id: "profile:engineering",
          component_node_id: "component:api",
        },
        {
          link_id: "workflow:release->component:api",
          source_node_id: "workflow:release",
          target_node_id: "component:api",
          semantic: "workflow-step",
          directionality: "directed",
          workflow_node_id: "workflow:release",
          component_node_id: "component:api",
          occurrences: [
            { ordinal: 1, step_id: "release-api", role: "build", mode: "sequential" },
            { ordinal: 2, step_id: "release-api-verify", role: "verify", gate_order: 1 },
          ],
        },
      ],
    },
  };
}

function fakeElement(dataset = {}) {
  const listeners = new Map();
  const styleValues = new Map();
  return {
    attributes: new Map(),
    classList: { add() {}, remove() {} },
    clientHeight: 640,
    clientWidth: 960,
    dataset: { ...dataset },
    hidden: false,
    inert: false,
    style: {
      getPropertyValue(name) { return styleValues.get(name) ?? ""; },
      setProperty(name, value) { styleValues.set(name, String(value)); },
    },
    textContent: "",
    addEventListener(type, listener) {
      listeners.set(type, [...(listeners.get(type) ?? []), listener]);
    },
    dispatch(type = "click", event = {}) {
      for (const listener of listeners.get(type) ?? []) listener({ target: this, ...event });
    },
    getBoundingClientRect() {
      return { height: this.clientHeight, width: this.clientWidth };
    },
    removeEventListener(type, listener) {
      listeners.set(type, (listeners.get(type) ?? []).filter((item) => item !== listener));
    },
    setAttribute(name, value) {
      this.attributes.set(name, String(value));
    },
  };
}

function rootHarness() {
  const scene = fakeElement();
  const semantic = fakeElement();
  const status = fakeElement();
  const scale = fakeElement();
  const identityOverlay = fakeElement();
  const identityTitle = fakeElement();
  const identityKind = fakeElement();
  const identityCount = fakeElement();
  const semanticView = fakeElement();
  const controls = [
    fakeElement({ graphZoom: "out" }),
    fakeElement({ graphZoom: "reset" }),
    fakeElement({ graphZoom: "in" }),
  ];
  const selectors = new Map([
    ["[data-component-map-scene-host]", scene],
    ["[data-component-map-semantic-host]", semantic],
    ["[data-graph-renderer-state]", status],
    ["[data-graph-scale]", scale],
    ["[data-graph-identity-overlay]", identityOverlay],
    ["[data-graph-identity-title]", identityTitle],
    ["[data-graph-identity-kind]", identityKind],
    ["[data-graph-identity-count]", identityCount],
    ["[data-graph-semantic-view]", semanticView],
  ]);
  const root = {
    querySelector(selector) {
      return selectors.get(selector) ?? null;
    },
    querySelectorAll(selector) {
      return selector === "[data-graph-zoom]" ? controls : [];
    },
  };
  return {
    controls,
    identityCount,
    identityKind,
    identityOverlay,
    identityTitle,
    root,
    scale,
    scene,
    semantic,
    semanticView,
    status,
  };
}

function sceneHostOverlayHarness() {
  const scene = fakeElement();
  const identityOverlay = fakeElement();
  const identityTitle = fakeElement();
  const identityKind = fakeElement();
  const identityCount = fakeElement();
  const hud = fakeElement();
  hud.getBoundingClientRect = () => ({ top: 0, left: 0, width: 960, height: 68 });
  const viewportSelectors = new Map([
    ["[data-graph-identity-overlay]", identityOverlay],
    ["[data-graph-identity-title]", identityTitle],
    ["[data-graph-identity-kind]", identityKind],
    ["[data-graph-identity-count]", identityCount],
  ]);
  const componentMap = {
    querySelector(selector) {
      return selector === "[data-component-map-hud]" ? hud : null;
    },
  };
  const viewport = {
    querySelector(selector) {
      return viewportSelectors.get(selector) ?? null;
    },
    getBoundingClientRect() {
      return { top: 0, left: 0, width: 960, height: 640 };
    },
    closest(selector) {
      return selector === ".component-map" ? componentMap : null;
    },
  };
  scene.closest = (selector) => (
    selector === "[data-component-map-viewport]" ? viewport : null
  );
  return {
    identityCount,
    identityKind,
    identityOverlay,
    identityTitle,
    scene,
  };
}

function semanticFallbackHarness() {
  const bindings = [];
  return {
    bindings,
    factory(host, options) {
      const binding = { host, options, released: false, renders: 0 };
      bindings.push(binding);
      return {
        release() { binding.released = true; },
        render() { binding.renders += 1; },
      };
    },
  };
}

async function flushAsyncWork() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function frameSchedulerHarness() {
  const callbacks = new Map();
  let nextId = 1;
  return {
    cancelFrame(frameId) { callbacks.delete(frameId); },
    flush() {
      while (callbacks.size > 0) {
        const batch = [...callbacks.values()];
        callbacks.clear();
        batch.forEach((callback) => callback());
      }
    },
    requestFrame(callback) {
      const frameId = nextId;
      nextId += 1;
      callbacks.set(frameId, callback);
      return frameId;
    },
    size() { return callbacks.size; },
  };
}

test("SoT graph snapshot v2 becomes a sanitized normalized GraphInput without inferred master", () => {
  const snapshot = snapshotFixture();
  const input = graphInputFromSotSnapshot(snapshot);

  assert.equal(input.schemaVersion, 1);
  assert.equal(input.layout.seed, "seed-adapter");
  assert.deepEqual(input.nodes.map((node) => [node.id, node.type, node.kind, node.label]), [
    ["profile:engineering", "profile", "profile", "Engineering"],
    ["workflow:release", "workflow", "workflow", "Release workflow"],
    ["component:api", "service", "service", "Public API"],
  ]);
  assert.deepEqual(input.nodes.find((node) => node.id === "component:api")?.metadata, {
    componentId: "api",
    domain: "public",
    entityType: "component",
    profileIds: ["engineering"],
    relationDegree: 1,
    status: "active",
    summary: null,
    workflowIds: ["release"],
  });
  assert.equal(input.nodes.some((node) => node.roles?.includes("master")), false);
  assert.deepEqual(input.links[1].occurrences?.map((item) => item.ordinal), [0, 1]);
  assert.deepEqual(input.links[1].occurrences?.map((item) => item.metadata.sourceOrdinal), [1, 2]);
  assert.equal(input.links[1].metadata.directionality, "directed");
  assert.equal("sourcePath" in input.links[1].metadata, false);

  const withMaster = graphInputFromSotSnapshot(snapshot, { masterNodeId: "workflow:release" });
  assert.deepEqual(withMaster.nodes.find((node) => node.id === "workflow:release")?.roles, ["master"]);
  assert.throws(
    () => graphInputFromSotSnapshot(snapshot, { masterNodeId: "component:missing" }),
    /masterNodeId/,
  );
});

test("projection stays the sole topology authority when catalog entries are missing or evolve", () => {
  const snapshot = snapshotFixture();
  snapshot.components = [];
  snapshot.profiles = [];
  snapshot.workflows = [];
  snapshot.graph_projection.nodes.push(
    {
      node_type: "relation",
      node_id: "unprofiled:projection:unprofiled",
      relation_kind: "unprofiled",
      canonical_id: "projection:unprofiled",
      name: "Unprofiled",
      exact_count: 0,
      anchor_ordinal: 2,
      size_scale: 1,
    },
    {
      node_type: "relation",
      node_id: "relation:future",
      relation_kind: "future_relation",
      canonical_id: "future",
      name: "Future relation",
      exact_count: 1,
      anchor_ordinal: 3,
      size_scale: 1,
    },
    {
      node_type: "component",
      node_id: "component:future",
      component_id: "future",
      kind: "future_kind",
      domain: "future",
      relation_degree: 1,
      profile_ids: [],
      workflow_ids: [],
    },
  );
  snapshot.graph_projection.links.push({
    link_id: "future-link",
    source_node_id: "relation:future",
    target_node_id: "component:future",
    semantic: "future-link",
    directionality: "directed",
  });

  const input = graphInputFromSotSnapshot(snapshot);
  assert.deepEqual(
    [...input.nodes.map((node) => node.id)].sort(),
    [...snapshot.graph_projection.nodes.map((node) => node.node_id)].sort(),
  );
  assert.deepEqual(
    [...input.links.map((link) => link.id)].sort(),
    [...snapshot.graph_projection.links.map((link) => link.link_id)].sort(),
  );
  assert.equal(input.nodes.find((node) => node.id === "workflow:release")?.label, "Release workflow");
  assert.equal(input.nodes.find((node) => node.id === "component:api")?.label, "api");
  assert.equal(input.nodes.find((node) => node.id === "unprofiled:projection:unprofiled")?.kind, "unprofiled");
  assert.deepEqual(input.nodes.find((node) => node.id === "relation:future"), {
    id: "relation:future",
    type: "relation",
    kind: "future_relation",
    label: "Future relation",
    metadata: {
      anchorOrdinal: 3,
      canonicalId: "future",
      entityType: "relation",
      exactCount: 1,
      relationKind: "future_relation",
      sizeScale: 1,
    },
  });
  assert.equal(input.nodes.find((node) => node.id === "component:future")?.type, "future_kind");
});

test("projection endpoint errors fail validation instead of silently dropping a link", () => {
  const snapshot = snapshotFixture();
  snapshot.graph_projection.links[1].target_node_id = "component:missing";

  assert.throws(
    () => graphInputFromSotSnapshot(snapshot),
    /must reference a node id/,
  );
});

test("adapter assigns graph-local occurrence ordinals when a workflow step repeats a component", () => {
  const snapshot = snapshotFixture();
  snapshot.graph_projection.links[1].occurrences = [
    { ordinal: 3, step_id: "build-api", role: "build" },
    { ordinal: 3, step_id: "verify-api", role: "verify" },
    { ordinal: 4, step_id: "publish-api", role: "publish" },
  ];

  const input = graphInputFromSotSnapshot(snapshot);
  const occurrences = input.links[1].occurrences;
  assert.deepEqual(occurrences?.map((item) => item.ordinal), [0, 1, 2]);
  assert.deepEqual(occurrences?.map((item) => item.metadata.sourceOrdinal), [3, 3, 4]);
});

test("adapter maps graph events into existing SoT actions and host presentation", async () => {
  const snapshot = snapshotFixture();
  const actions = [];
  const calls = [];
  const fallback = semanticFallbackHarness();
  const host = rootHarness();
  let browserOptions = null;
  const publicWorkbench = {
    destroy() { calls.push("destroy"); },
    fit() { calls.push("fit"); },
    focusNode(nodeId) { calls.push(["focus", nodeId]); },
    mount(root) {
      calls.push(["mount", root, root.hidden]);
      browserOptions.onRendererStateChange({ status: "mounted" });
    },
    resize() { calls.push("resize"); },
    restoreCamera() { calls.push("restore"); },
    setPresentation(presentation) { calls.push(["presentation", presentation]); },
    unmount() { calls.push("unmount"); },
    zoom(scale) { calls.push(["zoom", scale]); },
  };
  const session = createGraphWorkbenchAdapter({
    snapshot,
    browserRuntime: true,
    onAction: (action) => actions.push(action),
    loadBrowserWorkbench: async () => ({
      createGraphWorkbench(options) {
        browserOptions = options;
        return publicWorkbench;
      },
    }),
    semanticFallbackFactory: fallback.factory,
  });

  session.attach(host.root, { selectedComponentId: "api" });
  await flushAsyncWork();

  assert.equal(browserOptions.input.layout.seed, "seed-adapter");
  assert.deepEqual(calls[0], ["mount", host.scene, false]);
  assert.equal(calls.find(([kind]) => kind === "presentation")?.[1].selectedNodeIds[0], "component:api");
  assert.equal(fallback.bindings.length, 1);
  actions.length = 0;

  assert.equal(browserOptions.onNodeClick, undefined);
  browserOptions.onSelectionChange({ nodeId: "component:api", source: "keyboard" });
  browserOptions.onSelectionChange({ nodeId: "profile:engineering", source: "mouse" });
  browserOptions.onSelectionChange({ nodeId: "workflow:release", source: "keyboard" });
  browserOptions.onSelectionChange({ nodeId: null, source: "background" });
  browserOptions.onSelectionChange({ nodeId: "component:api", source: "programmatic" });
  browserOptions.onNodeHover({ nodeId: "component:api" });
  assert.deepEqual(actions, [
    { type: "select_graph_node", componentId: "api" },
    {
      type: "select_graph_relation",
      nodeId: "profile:engineering",
      relationKind: "profile",
      canonicalId: "engineering",
    },
    {
      type: "select_graph_relation",
      nodeId: "workflow:release",
      relationKind: "workflow",
      canonicalId: "release",
    },
    { type: "clear_graph_selection" },
    { type: "hover_graph_node", nodeId: "component:api" },
  ]);

  session.setResolvedAppearance("light");
  session.syncPresentation({ selectedRelationNodeId: "workflow:release" });
  const latestPresentation = calls.filter(([kind]) => kind === "presentation").at(-1)?.[1];
  assert.equal(latestPresentation.theme, "light");
  assert.deepEqual(latestPresentation.selectedNodeIds, ["workflow:release"]);
  assert.equal(session.restoreCamera(), true);
  assert.equal(session.focusNode("workflow:release"), true);
  assert.equal(session.focusNode("component:missing"), false);
  assert.equal(session.refreshAfterLayout(), true);
  host.controls[0].dispatch();
  host.controls[1].dispatch();
  host.controls[2].dispatch();
  session.dispose();
  assert.ok(calls.includes("restore"));
  assert.ok(calls.some(([kind, nodeId]) => kind === "focus" && nodeId === "workflow:release"));
  assert.ok(calls.includes("resize"));
  assert.ok(calls.includes("destroy"));
  assert.deepEqual(calls.filter(([kind]) => kind === "zoom"), [["zoom", 0.8], ["zoom", 1.2]]);
  assert.ok(calls.includes("fit"));
});

test("adapter composes the latest public workbench and renderer APIs around host selection", async () => {
  const actions = [];
  const resolverCalls = [];
  const events = [];
  const host = rootHarness();
  let workbenchOptions = null;
  let rendererCallbacks = null;
  let selectionState = { nodeId: null };
  const acceptedPolicy = {
    camera: { kind: "contextual", durationMs: 240 },
    layout: "preserve",
  };
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: true,
    onAction(action) { actions.push(action); },
    resolveSelection({ action, intent }) {
      resolverCalls.push({ action, intent });
      return intent.nodeId === "component:api" || intent.source === "keyboard" || intent.nodeId === null
        ? acceptedPolicy
        : false;
    },
    loadBrowserWorkbench: async () => ({
      createGraphWorkbench(options) {
        workbenchOptions = options;
        return {
          getSelectionState() { return selectionState; },
          destroy() {},
          mount(scene) {
            options.rendererFactory({
              container: scene,
              callbacks: {
                onBackgroundClick() {
                  events.push("core-background");
                  selectionState = { nodeId: null };
                  options.onSelectionChange({ nodeId: null, source: "background" });
                },
                onNodeClick(nodeId) {
                  events.push(["core-node", nodeId]);
                  selectionState = { nodeId };
                  options.onSelectionChange({ nodeId, source: "mouse" });
                },
                onNodeHover() {},
              },
            });
            options.onRendererStateChange({ status: "mounted" });
          },
          resize() {},
          restoreCamera() { events.push("restore-camera"); },
          selectNode(nodeId, source) {
            events.push(["core-node", nodeId, source]);
            selectionState = { nodeId };
            options.onSelectionChange({ nodeId, source });
          },
          setPresentation() {},
          unmount() {},
        };
      },
      createThreeForceGraphRenderer(options) {
        rendererCallbacks = options.callbacks;
        return {
          destroy() {},
          resize() {},
          setData() {},
          setPresentation() {},
          transitionToNode() {},
          restoreCamera() {},
          zoom() {},
        };
      },
    }),
  });

  session.attach(host.root);
  await flushAsyncWork();
  actions.length = 0;

  assert.equal(typeof workbenchOptions.rendererFactory, "function");
  assert.equal(Object.hasOwn(workbenchOptions, "resolveSelection"), false);
  assert.equal(Object.hasOwn(workbenchOptions, "labelVisibility"), false);
  const acceptedIntent = { nodeId: "component:api", source: "mouse" };
  rendererCallbacks.onNodeClick(acceptedIntent.nodeId);
  assert.deepEqual(resolverCalls, [{
    action: { type: "select_graph_node", componentId: "api" },
    intent: acceptedIntent,
  }]);
  assert.deepEqual(actions, [{ type: "select_graph_node", componentId: "api" }]);

  rendererCallbacks.onNodeClick("profile:engineering");
  assert.equal(actions.length, 1);
  assert.equal(resolverCalls.length, 2);

  host.scene.dispatch("keydown", {
    key: "ArrowLeft",
    preventDefault() {},
    stopImmediatePropagation() {},
  });
  assert.deepEqual(actions, [
    { type: "select_graph_node", componentId: "api" },
    {
      type: "select_graph_relation",
      nodeId: "workflow:release",
      relationKind: "workflow",
      canonicalId: "release",
    },
  ]);
  assert.equal(resolverCalls.at(-1).intent.source, "keyboard");

  rendererCallbacks.onBackgroundClick();
  assert.deepEqual(actions.at(-1), { type: "clear_graph_selection" });
  assert.deepEqual(events, [
    ["core-node", "component:api"],
    ["core-node", "workflow:release", "keyboard"],
    "core-background",
    "restore-camera",
  ]);
  session.dispose();
});

test("presentation uses the latest public fields and keeps display-name descriptors", () => {
  const presentation = graphPresentationFromSotView(snapshotFixture());

  assert.equal(presentation.nodeDescriptors["component:api"].label, "Public API");
  assert.doesNotMatch(presentation.nodeDescriptors["component:api"].label, /^harnesskit\./);
  assert.equal(Object.hasOwn(presentation, "labelVisibility"), false);
  assert.equal(Object.hasOwn(presentation, "selectionLayout"), false);
  assert.equal(Object.hasOwn(presentation, "recoveryKey"), false);
});

test("identity overlay follows selected, focused, and hovered public graph identities", () => {
  const host = rootHarness();
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: false,
    semanticFallbackFactory: semanticFallbackHarness().factory,
  });

  session.attach(host.root, { selectedComponentId: "api" });
  assert.equal(host.identityOverlay.hidden, false);
  assert.equal(host.identityOverlay.style.getPropertyValue("--graph-identity-opacity"), "1");
  assert.equal(host.identityOverlay.style.getPropertyValue("--graph-identity-emphasis-percent"), "100%");
  assert.equal(host.identityOverlay.dataset.identityPresence, "visible");
  assert.equal(host.identityOverlay.dataset.identityState, "selected");
  assert.equal(host.identityTitle.textContent, "Public API");
  assert.equal(host.identityKind.textContent, "SERVICE");
  assert.equal(host.identityCount.textContent, "1 relation");

  session.syncPresentation({
    graphPresentation: { focusNodeId: "profile:engineering" },
    hoveredGraphNodeId: "workflow:release",
  });
  assert.equal(host.identityTitle.textContent, "Engineering");
  assert.equal(host.identityKind.textContent, "PROFILE");
  assert.equal(host.identityCount.textContent, "1 component");
  assert.equal(host.identityOverlay.dataset.identityState, "focused");

  session.syncPresentation({});
  assert.equal(host.identityOverlay.hidden, true);
  assert.equal(host.identityOverlay.style.getPropertyValue("--graph-identity-opacity"), "0");
  assert.equal(host.identityOverlay.dataset.identityPresence, "hidden");
  assert.equal(host.identityOverlay.dataset.identityState, "idle");
  assert.equal(host.identityTitle.textContent, "");
  assert.equal(host.identityKind.textContent, "");
  assert.equal(host.identityCount.textContent, "");
  session.dispose();
});

test("scene-host attachment updates the sibling viewport identity overlay", () => {
  const host = sceneHostOverlayHarness();
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: false,
  });

  session.attach(host.scene, { selectedComponentId: "api" });

  assert.equal(host.identityOverlay.hidden, false);
  assert.equal(host.identityOverlay.style.getPropertyValue("--graph-identity-opacity"), "1");
  assert.equal(host.identityOverlay.dataset.identityPresence, "visible");
  assert.equal(host.identityOverlay.dataset.identityState, "selected");
  assert.equal(host.identityTitle.textContent, "Public API");
  assert.equal(host.identityKind.textContent, "SERVICE");
  assert.equal(host.identityCount.textContent, "1 relation");
  assert.equal(host.identityOverlay.style.getPropertyValue("inset-block-start"), "80px");
  assert.equal(host.identityOverlay.style.getPropertyValue("inset-inline-start"), "12px");
  session.dispose();
});

test("mounted graph fits after layout frames and follows the live host viewport", async () => {
  const calls = [];
  const frames = frameSchedulerHarness();
  const host = rootHarness();
  const observers = [];
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: true,
    requestFrame: frames.requestFrame,
    cancelFrame: frames.cancelFrame,
    resizeObserverFactory(callback) {
      const observer = {
        callback,
        disconnected: false,
        observed: [],
        disconnect() { this.disconnected = true; },
        observe(element) { this.observed.push(element); },
      };
      observers.push(observer);
      return observer;
    },
    loadBrowserWorkbench: async () => ({
      createGraphWorkbench(options) {
        return {
          destroy() {},
          fit(durationMs) { calls.push(["fit", durationMs]); },
          mount() { options.onRendererStateChange({ status: "mounted" }); },
          resize(...size) { calls.push(["resize", ...size]); },
          setPresentation() {},
          unmount() {},
        };
      },
    }),
  });

  session.attach(host.root);
  await flushAsyncWork();

  assert.equal(observers.length, 1);
  assert.deepEqual(observers[0].observed, [host.scene]);
  assert.equal(frames.size(), 1);
  assert.deepEqual(calls.filter(([kind]) => kind === "fit"), []);

  session.detach();
  assert.equal(observers[0].disconnected, true);
  assert.equal(frames.size(), 0);
  session.attach(host.root);
  assert.equal(observers.length, 2);
  assert.equal(frames.size(), 1);

  observers[1].callback([{
    target: host.scene,
    contentRect: { width: 1280, height: 720 },
  }]);
  assert.ok(calls.some((call) => (
    call[0] === "resize" && call[1] === 1280 && call[2] === 720
  )));

  frames.flush();
  assert.ok(calls.some((call) => (
    call[0] === "resize" && call[1] === 960 && call[2] === 640
  )));
  assert.deepEqual(calls.filter(([kind]) => kind === "fit"), [["fit", 0]]);

  session.detach();
  assert.equal(observers[1].disconnected, true);
  assert.equal(frames.size(), 0);
});

test("adapter maps activity to reduced motion and uses public unmount/remount lifecycle", async () => {
  const calls = [];
  const host = rootHarness();
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: true,
    loadBrowserWorkbench: async () => ({
      createGraphWorkbench(options) {
        return {
          destroy() {},
          mount() { options.onRendererStateChange({ status: "mounted" }); },
          setReducedMotion(value) { calls.push(["reduced-motion", value]); },
          setPresentation() {},
          unmount() { calls.push("unmount"); },
        };
      },
      createThreeForceGraphRenderer() {
        return {
          destroy() {},
          resize() {},
          setData() {},
          setPresentation() {},
          zoom() {},
        };
      },
    }),
  });

  session.attach(host.root);
  await flushAsyncWork();
  session.setActivityState({ foreground: false, reducedMotion: true });
  assert.equal(session.suspend(), true);
  assert.equal(session.resume(), true);
  assert.equal(session.setExpanded(false), false);
  assert.equal(session.setExpanded(true), true);

  assert.ok(calls.includes("unmount"));
  assert.ok(calls.some(([kind, value]) => kind === "reduced-motion" && value === true));
  session.dispose();
});

test("non-browser execution uses the existing host fallback session", () => {
  const snapshot = snapshotFixture();
  const fallback = { attach() {}, detach() {}, dispose() {} };
  let received = null;
  const session = createGraphWorkbenchAdapter({
    snapshot,
    browserRuntime: false,
    onAction() {},
    legacySessionFactory(options) {
      received = options;
      return fallback;
    },
  });
  assert.equal(session, fallback);
  assert.equal(received.snapshot, snapshot);
});

test("default non-browser execution keeps the semantic fallback interactive without legacy renderer imports", () => {
  const actions = [];
  const fallback = semanticFallbackHarness();
  const host = rootHarness();
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: false,
    onAction(action) { actions.push(action); },
    semanticFallbackFactory: fallback.factory,
  });

  assert.equal(session.attach(host.root), true);
  assert.equal(fallback.bindings.length, 1);
  assert.equal(host.semantic.dataset.rendererAvailability, "unavailable");
  assert.match(host.status.textContent, /3D 그래프를 불러올 수 없어/);
  fallback.bindings[0].options.onNodeSelect({ node_id: "component:api" });
  fallback.bindings[0].options.onBackgroundSelect();

  assert.deepEqual(actions, [
    { type: "select_graph_node", componentId: "api" },
    { type: "clear_graph_selection" },
  ]);
  assert.equal(session.retry(), false);
  session.dispose();
});

test("presentation keeps existing selection identities and only accepts known focus nodes", () => {
  const snapshot = snapshotFixture();
  const presentation = graphPresentationFromSotView(snapshot, {
    selectedComponentId: "api",
    graphPresentation: { focusNodeId: "component:missing" },
  });
  assert.deepEqual(presentation.selectedNodeIds, ["component:api"]);
  assert.equal(presentation.focusNodeId, "component:api");
});

test("presentation applies existing graph theme tokens by actual entity type", () => {
  const presentation = graphPresentationFromSotView(snapshotFixture(), {
    selectedRelationNodeId: "profile:engineering",
  }, "light", {
    edge: "edge",
    other: "other",
    profile: "profile",
    profileEdge: "profile-edge",
    selected: "selected",
    workflow: "workflow",
    workflowEdge: "workflow-edge",
  });

  assert.equal(presentation.nodeDescriptors["profile:engineering"].color, "selected");
  assert.equal(presentation.nodeDescriptors["workflow:release"].color, "workflow");
  assert.equal(presentation.nodeDescriptors["component:api"].color, "other");
  assert.equal(presentation.linkDescriptors["profile:engineering->component:api"].color, "selected");
  assert.equal(presentation.linkDescriptors["workflow:release->component:api"].color, "workflow-edge");
});

test("theme token lookup skips the scene host before the graph attaches", () => {
  const originalGetComputedStyle = globalThis.getComputedStyle;
  let calls = 0;
  globalThis.getComputedStyle = () => {
    calls += 1;
    throw new Error("getComputedStyle must not receive a missing host");
  };

  try {
    assert.deepEqual(graphThemeTokensFromElement(null), {});
    assert.equal(calls, 0);
  } finally {
    if (originalGetComputedStyle === undefined) delete globalThis.getComputedStyle;
    else globalThis.getComputedStyle = originalGetComputedStyle;
  }
});

test("empty and invalid graph inputs keep the surrounding detail UI available", () => {
  const emptyHost = rootHarness();
  let emptyLoads = 0;
  const emptySession = createGraphWorkbenchAdapter({
    snapshot: {
      components: [],
      profiles: [],
      unprofiled_component_ids: [],
      workflows: [],
      graph_projection: {
        schema_version: 2,
        snapshot_id: "empty",
        layout_seed: "empty",
        nodes: [],
        links: [],
      },
    },
    browserRuntime: true,
    loadBrowserWorkbench() {
      emptyLoads += 1;
      return Promise.resolve({});
    },
  });

  assert.equal(emptySession.attach(emptyHost.root), false);
  assert.equal(emptyLoads, 0);
  assert.match(emptyHost.scene.textContent, /표시할 Profile/);
  assert.equal(emptyHost.status.hidden, false);
  assert.equal(emptySession.setExpanded(false), false);
  assert.equal(emptySession.setExpanded(true), false);
  assert.match(emptyHost.scene.textContent, /표시할 Profile/);
  assert.equal(emptyHost.status.hidden, false);
  assert.equal(emptyLoads, 0);

  const invalidSnapshot = snapshotFixture();
  invalidSnapshot.graph_projection.links[0].target_node_id = "component:missing";
  const invalidHost = rootHarness();
  let invalidLoads = 0;
  const invalidSession = createGraphWorkbenchAdapter({
    snapshot: invalidSnapshot,
    browserRuntime: true,
    loadBrowserWorkbench() {
      invalidLoads += 1;
      return Promise.resolve({});
    },
  });

  assert.equal(invalidSession.attach(invalidHost.root), false);
  assert.equal(invalidLoads, 0);
  assert.match(invalidHost.scene.textContent, /그래프 데이터를 확인할 수 없습니다/);
  assert.equal(invalidHost.status.hidden, false);
});

test("browser module failure promotes the existing semantic fallback without breaking details", async () => {
  const actions = [];
  const fallback = semanticFallbackHarness();
  const host = rootHarness();
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: true,
    onAction(action) { actions.push(action); },
    loadBrowserWorkbench: async () => { throw new Error("network unavailable"); },
    semanticFallbackFactory: fallback.factory,
  });

  session.attach(host.root);
  await flushAsyncWork();

  assert.equal(host.scene.hidden, true);
  assert.equal(host.semantic.hidden, false);
  assert.equal(host.semantic.dataset.rendererAvailability, "unavailable");
  assert.match(host.status.textContent, /3D 그래프를 불러올 수 없어/);
  assert.equal(host.status.hidden, false);
  assert.ok(fallback.bindings[0].renders > 0);
  assert.deepEqual(actions, [{
    type: "graph_renderer_failed",
    failure: "3D 그래프를 불러올 수 없어 텍스트 보기로 전환했습니다.",
  }]);
});

test("renderer failure keeps semantic fallback primary and retry creates a fresh graph-workbench", async () => {
  const actions = [];
  const calls = [];
  const fallback = semanticFallbackHarness();
  const host = rootHarness();
  const workbenches = [];
  let loads = 0;
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: true,
    onAction(action) { actions.push(action); },
    loadBrowserWorkbench: async () => {
      loads += 1;
      return {
        createGraphWorkbench(options) {
          const index = workbenches.length;
          const workbench = {
            destroy() { calls.push(["destroy", index]); },
            fit() {},
            focusNode() {},
            mount(root) {
              calls.push(["mount", index, root]);
              options.onRendererStateChange({ status: index === 0 ? "failed" : "mounted" });
            },
            resize() {},
            restoreCamera() {},
            setPresentation() {},
            unmount() {},
          };
          workbenches.push(workbench);
          return workbench;
        },
      };
    },
    semanticFallbackFactory: fallback.factory,
  });

  session.attach(host.root);
  await flushAsyncWork();

  assert.equal(host.scene.hidden, true);
  assert.equal(host.semantic.dataset.rendererAvailability, "unavailable");
  assert.equal(workbenches.length, 1);
  assert.deepEqual(actions, [{
    type: "graph_renderer_failed",
    failure: "3D 그래프를 시작할 수 없어 텍스트 보기로 전환했습니다.",
  }]);

  assert.equal(session.retry(), true);
  await flushAsyncWork();

  assert.equal(loads, 2);
  assert.equal(workbenches.length, 2);
  assert.ok(calls.some(([kind, index]) => kind === "destroy" && index === 0));
  assert.deepEqual(calls.filter(([kind]) => kind === "mount").map(([, index]) => index), [0, 1]);
  assert.equal(host.scene.hidden, false);
  assert.equal(host.semantic.dataset.rendererAvailability, "ready");
  assert.deepEqual(actions.at(-1), { type: "graph_renderer_ready" });
  session.dispose();
});

test("manual semantic return also recreates graph-workbench instead of changing renderer implementations", async () => {
  const actions = [];
  const fallback = semanticFallbackHarness();
  const host = rootHarness();
  const workbenches = [];
  let loads = 0;
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: true,
    onAction(action) { actions.push(action); },
    loadBrowserWorkbench: async () => {
      loads += 1;
      return {
        createGraphWorkbench(options) {
          const workbench = {
            destroy() { workbench.destroyed = true; },
            fit() {},
            focusNode() {},
            mount() { options.onRendererStateChange({ status: "mounted" }); },
            resize() {},
            restoreCamera() {},
            setPresentation() {},
            unmount() {},
          };
          workbenches.push(workbench);
          return workbench;
        },
      };
    },
    semanticFallbackFactory: fallback.factory,
  });

  session.attach(host.root);
  await flushAsyncWork();
  host.semanticView.dispatch();

  assert.equal(workbenches[0].destroyed, true);
  assert.equal(host.semantic.dataset.rendererAvailability, "manual_fallback");
  assert.equal(actions.at(-1)?.type, "show_graph_semantic");
  assert.equal(session.returnToThreeD(), true);
  await flushAsyncWork();

  assert.equal(loads, 2);
  assert.equal(workbenches.length, 2);
  assert.equal(host.semantic.dataset.rendererAvailability, "ready");
  assert.deepEqual(actions.at(-2), { type: "return_graph_three_d" });
  assert.deepEqual(actions.at(-1), { type: "graph_renderer_ready" });
  session.dispose();
});

test("late browser resolution and rejection cannot mutate a detached host", async () => {
  const actions = [];
  const fallback = semanticFallbackHarness();
  const host = rootHarness();
  const calls = [];
  let resolveBrowserModule;
  let rejectBrowserModule;
  const browserModule = new Promise((resolve) => {
    resolveBrowserModule = resolve;
  });
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: true,
    onAction(action) { actions.push(action); },
    loadBrowserWorkbench: () => browserModule,
    semanticFallbackFactory: fallback.factory,
  });

  session.attach(host.root);
  session.detach();
  resolveBrowserModule({
    createGraphWorkbench() {
      calls.push("factory");
      return {
        destroy() { calls.push("destroy"); },
        mount() { calls.push("mount"); },
        setPresentation() { calls.push("presentation"); },
        unmount() { calls.push("unmount"); },
      };
    },
  });
  await flushAsyncWork();

  assert.deepEqual(calls, []);
  assert.deepEqual(actions, []);
  assert.equal(host.status.textContent, "");
  assert.equal(fallback.bindings[0].released, true);

  const rejectedHost = rootHarness();
  const rejected = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: true,
    onAction(action) { actions.push(action); },
    loadBrowserWorkbench: () => new Promise((resolve, reject) => { rejectBrowserModule = reject; }),
    semanticFallbackFactory: fallback.factory,
  });
  rejected.attach(rejectedHost.root);
  rejected.detach();
  rejectBrowserModule(new Error("late failure"));
  await flushAsyncWork();

  assert.deepEqual(actions, []);
  assert.equal(rejectedHost.status.textContent, "");
});

test("a replacement attachment starts its own import instead of waiting for a stale one", async () => {
  const fallback = semanticFallbackHarness();
  const firstHost = rootHarness();
  const secondHost = rootHarness();
  const calls = [];
  const resolvers = [];
  const session = createGraphWorkbenchAdapter({
    snapshot: snapshotFixture(),
    browserRuntime: true,
    loadBrowserWorkbench: () => new Promise((resolve) => { resolvers.push(resolve); }),
    semanticFallbackFactory: fallback.factory,
  });

  session.attach(firstHost.root);
  session.detach();
  session.attach(secondHost.root);
  assert.equal(resolvers.length, 2);

  resolvers[0]({
    createGraphWorkbench() {
      calls.push("stale factory");
      return { destroy() {}, mount() {}, setPresentation() {} };
    },
  });
  await flushAsyncWork();

  resolvers[1]({
    createGraphWorkbench(options) {
      calls.push("current factory");
      return {
        destroy() {},
        mount(root) {
          calls.push(["mount", root]);
          options.onRendererStateChange({ status: "mounted" });
        },
        setPresentation() {},
      };
    },
  });
  await flushAsyncWork();

  assert.deepEqual(calls, ["current factory", ["mount", secondHost.scene]]);
  session.dispose();
});
