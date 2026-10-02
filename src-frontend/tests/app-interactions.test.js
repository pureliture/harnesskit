import assert from "node:assert/strict";
import test from "node:test";

import {
  focusWorkflowOverview,
  matchesLocalRequestIdentity,
  mountApp,
} from "../app.js";
import { createLocalViewState } from "../local-state.js";

const snapshot = {
  snapshot_id: "snapshot-interactions",
  checkout_summary: {
    source_revision: "abc123",
    branch: "main",
    detached: false,
    dirty: false,
    recent_commits: [],
  },
  components: [
    { component_id: "harnesskit.agent.router", kind: "agent", status: "draft", title: "Router", summary: "Routes work", domain: "core", targets: [], provenance: {}, owned_files: [], profile_ids: [] },
    { component_id: "harnesskit.skill.beta", kind: "skill", status: "draft", title: "Beta", summary: "Beta skill", domain: "work", targets: [], provenance: {}, owned_files: [], profile_ids: [] },
  ],
  profiles: [],
  workflows: [],
  unprofiled_component_ids: ["harnesskit.agent.router", "harnesskit.skill.beta"],
  relations: [],
  graph_projection: {
    schema_version: 2,
    snapshot_id: "snapshot-interactions",
    layout_seed: "seed-interactions",
    nodes: [
      { node_type: "relation", node_id: "unprofiled:__unprofiled__", relation_kind: "unprofiled", canonical_id: "__unprofiled__", name: "Unprofiled", exact_count: 2, anchor_ordinal: 1, size_scale: 1 },
      { node_type: "component", node_id: "component:harnesskit.agent.router", component_id: "harnesskit.agent.router", kind: "agent", domain: "core", relation_degree: 1, profile_ids: [], workflow_ids: [] },
      { node_type: "component", node_id: "component:harnesskit.skill.beta", component_id: "harnesskit.skill.beta", kind: "skill", domain: "work", relation_degree: 1, profile_ids: [], workflow_ids: [] },
    ],
    links: [
      { semantic: "profile-membership", link_id: "profile-membership:__unprofiled__:harnesskit.agent.router", profile_node_id: "unprofiled:__unprofiled__", component_node_id: "component:harnesskit.agent.router", source_node_id: "unprofiled:__unprofiled__", target_node_id: "component:harnesskit.agent.router", directionality: "unordered", provenance: "DerivedUnprofiled" },
      { semantic: "profile-membership", link_id: "profile-membership:__unprofiled__:harnesskit.skill.beta", profile_node_id: "unprofiled:__unprofiled__", component_node_id: "component:harnesskit.skill.beta", source_node_id: "unprofiled:__unprofiled__", target_node_id: "component:harnesskit.skill.beta", directionality: "unordered", provenance: "DerivedUnprofiled" },
    ],
  },
  navigation_projection: { all_component_ids: [], groups: [] },
  issues: [],
};

function snapshotWithAuthoringFlow() {
  return {
    ...structuredClone(snapshot),
    workflows: [{
      workflow_id: "harnesskit.workflow.harness-creation",
      title: "Component Dev Guide",
      description: "요구사항부터 검증까지 한 기능 단위씩 진행합니다.",
      steps: [
        "reference-mode",
        "requirements",
        "blueprint",
        "select-current-slice",
        "canonical-authoring",
        "adapter-authoring",
        "evaluation",
        "advance-after-pass",
      ].map((stepId, index) => ({
        workflow_id: "harnesskit.workflow.harness-creation",
        ordinal: index + 1,
        step_id: stepId,
        title: `${index + 1}단계`,
        description: `${index + 1}단계 설명`,
        authored_fields: {
          agent: index < 5 ? `harnesskit.agent.${stepId}` : null,
          skill: index > 0 ? `harnesskit.skill.${stepId}` : null,
          output: `${stepId}.md`,
        },
        resolved_component_ids: [],
        unresolved_references: [],
        source_path: "components/workflows/harness-creation/workflow.yml",
      })),
    }],
  };
}

test("Workflow 전체 보기는 일반 선택과 분리된 명시적 graph focus를 요청한다", () => {
  const focused = [];
  focusWorkflowOverview({
    focusNode(nodeId) {
      focused.push(nodeId);
    },
  }, "harnesskit.workflow.harness-creation");

  assert.deepEqual(focused, ["workflow:harnesskit.workflow.harness-creation"]);
  focusWorkflowOverview({ focusNode() { focused.push("invalid"); } }, "  ");
  assert.deepEqual(focused, ["workflow:harnesskit.workflow.harness-creation"]);
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

function createControl(ownerDocument, tag) {
  const decodeAttribute = (value) => value
    ?.replaceAll("&quot;", '"')
    .replaceAll("&#039;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&amp;", "&") ?? null;
  const attribute = (name) => decodeAttribute(tag.match(new RegExp(`${name}="([^"]*)"`))?.[1]);
  const hasAttribute = (name) => new RegExp(`(?:^|\\s)${name}(?:\\s|=|>)`).test(tag);
  const attributes = new Map([
    ["role", attribute("role")],
    ["tabindex", attribute("tabindex")],
    ["aria-selected", attribute("aria-selected")],
    ["aria-pressed", attribute("aria-pressed")],
    ["aria-expanded", attribute("aria-expanded")],
    ["aria-level", attribute("aria-level")],
    ["aria-disabled", attribute("aria-disabled")],
    ["name", attribute("name")],
    ["type", attribute("type")],
  ].filter(([, value]) => value !== null));
  const componentId = attribute("data-component-id");
  const semanticNodeId = attribute("data-semantic-node-id");
  const semanticWorkflowStep = attribute("data-semantic-workflow-step");
  const workflowId = attribute("data-workflow-id");
  const graphZoom = attribute("data-graph-zoom");
  const graphRetry = hasAttribute("data-graph-retry") ? "" : undefined;
  const graphReturn = hasAttribute("data-graph-return") ? "" : undefined;
  const profileId = attribute("data-profile-id");
  const profileLabel = attribute("data-profile-label");
  const dashboardSegment = attribute("data-dashboard-segment");
  const localAction = attribute("data-local-action");
  const instanceId = attribute("data-instance-id");
  const localInstance = attribute("data-local-instance");
  const snapshotId = attribute("data-snapshot-id");
  const sotTreeNode = attribute("data-sot-tree-node");
  const sotTreeProfile = attribute("data-sot-tree-profile");
  const sotGraphToggle = hasAttribute("data-sot-graph-toggle") ? "" : undefined;
  const sotMatrixToggle = hasAttribute("data-sot-matrix-toggle") ? "" : undefined;
  const openAiSettings = hasAttribute("data-open-ai-settings") ? "" : undefined;
  const aiProviderClose = hasAttribute("data-ai-provider-close") ? "" : undefined;
  const aiProviderKeyDelete = hasAttribute("data-ai-provider-key-delete") ? "" : undefined;
  const aiExplain = hasAttribute("data-ai-explain") ? "" : undefined;
  const openAuthoringFlow = hasAttribute("data-open-authoring-flow") ? "" : undefined;
  const closeAuthoringFlow = hasAttribute("data-close-authoring-flow") ? "" : undefined;
  const localRemovalSelection = attribute("data-local-removal-selection");
  const enterLocalRemoval = hasAttribute("data-enter-local-removal") ? "" : undefined;
  const prepareLocalRemoval = hasAttribute("data-prepare-local-removal") ? "" : undefined;
  const cancelLocalRemovalSelection = hasAttribute("data-cancel-local-removal-selection") ? "" : undefined;
  const closeLocalRemoval = hasAttribute("data-close-local-removal") ? "" : undefined;
  const applyLocalRemoval = hasAttribute("data-apply-local-removal") ? "" : undefined;
  const localRemovalAck = hasAttribute("data-local-removal-ack") ? "" : undefined;
  const listeners = new Map();
  return {
    inAppRoot: true,
    disabled: hasAttribute("disabled"),
    name: attribute("name"),
    type: attribute("type"),
    value: decodeAttribute(attribute("value") ?? ""),
    checked: hasAttribute("checked"),
    dataset: {
      componentId,
      semanticNodeId,
      semanticWorkflowStep,
      workflowId,
      graphZoom,
      graphRetry,
      graphReturn,
      profileId,
      profileLabel,
      dashboardSegment,
      localAction,
      instanceId,
      localInstance,
      snapshotId,
      sotTreeNode,
      sotTreeProfile,
      sotGraphToggle,
      sotMatrixToggle,
      openAiSettings,
      aiProviderClose,
      aiProviderKeyDelete,
      aiExplain,
      openAuthoringFlow,
      closeAuthoringFlow,
      localRemovalSelection,
      enterLocalRemoval,
      prepareLocalRemoval,
      cancelLocalRemovalSelection,
      closeLocalRemoval,
      applyLocalRemoval,
      localRemovalAck,
    },
    classList: createClassList(attribute("class") ?? ""),
    getAttribute: (name) => attributes.get(name) ?? null,
    setAttribute: (name, value) => attributes.set(name, String(value)),
    removeAttribute: (name) => attributes.delete(name),
    querySelector() { return null; },
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    dispatch(type, event = {}) {
      listeners.get(type)?.({ ...event, currentTarget: this, target: event.target ?? this });
    },
    focus() {
      if (this.disabled) return;
      ownerDocument.activeElement = this;
    },
    closest(selector) {
      if (componentId && selector.includes("data-component-id")) return this;
      if (semanticNodeId && selector === "[data-semantic-node-id]") return this;
      if (semanticWorkflowStep && selector === "[data-semantic-workflow-step]") return this;
      if (graphRetry !== undefined && selector === "[data-graph-retry]") return this;
      if (graphReturn !== undefined && selector === "[data-graph-return]") return this;
      if (profileId && selector.includes("data-profile-id")) return this;
      if (localInstance && selector.includes("data-local-instance")) return this;
      if (localAction && selector.includes("data-local-action")) return this;
      if (sotTreeNode && selector.includes("data-sot-tree-node")) return this;
      if (sotGraphToggle !== undefined && selector === "[data-sot-graph-toggle]") return this;
      if (sotMatrixToggle !== undefined && selector === "[data-sot-matrix-toggle]") return this;
      if (openAiSettings !== undefined && selector === "[data-open-ai-settings]") return this;
      if (aiProviderClose !== undefined && selector === "[data-ai-provider-close]") return this;
      if (aiProviderKeyDelete !== undefined && selector === "[data-ai-provider-key-delete]") return this;
      if (aiExplain !== undefined && selector === "[data-ai-explain]") return this;
      if (this.name === "baseUrl" && selector === "[data-ai-provider-form]") return this;
      if (openAuthoringFlow !== undefined && selector === "[data-open-authoring-flow]") return this;
      if (closeAuthoringFlow !== undefined && selector === "[data-close-authoring-flow]") return this;
      if (localRemovalSelection && selector === "[data-local-removal-selection]") return this;
      if (enterLocalRemoval !== undefined && selector === "[data-enter-local-removal]") return this;
      if (prepareLocalRemoval !== undefined && selector === "[data-prepare-local-removal]") return this;
      if (cancelLocalRemovalSelection !== undefined && selector === "[data-cancel-local-removal-selection]") return this;
      if (closeLocalRemoval !== undefined && selector === "[data-close-local-removal]") return this;
      if (applyLocalRemoval !== undefined && selector === "[data-apply-local-removal]") return this;
      if (localRemovalAck !== undefined && selector === "[data-local-removal-ack]") return this;
      if (attributes.get("role") === "treeitem" && selector.includes("role=treeitem")) return this;
      return null;
    },
  };
}

function createMountRoot(options = {}) {
  const liveRegions = [];
  let ownerDocument;
  const body = {
    inAppRoot: false,
    appendChild(node) {
      liveRegions.push(node);
    },
    focus() {
      ownerDocument.activeElement = this;
    },
  };
  ownerDocument = {
    activeElement: body,
    body,
    createElement() {
      const attributes = new Map();
      const elementListeners = new Map();
      return {
        dataset: {},
        className: "",
        textContent: "",
        children: [],
        parentNode: null,
        setAttribute(name, value) {
          attributes.set(name, String(value));
        },
        getAttribute(name) {
          return attributes.get(name) ?? null;
        },
        appendChild(child) {
          child.remove?.();
          this.children.push(child);
          child.parentNode = this;
          return child;
        },
        replaceChildren() {
          this.children.forEach((child) => { child.parentNode = null; });
          this.children = [];
        },
        addEventListener(type, listener) {
          elementListeners.set(type, listener);
        },
        removeEventListener(type, listener) {
          if (elementListeners.get(type) === listener) elementListeners.delete(type);
        },
        remove() {
          if (this.parentNode?.children) {
            this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
            this.parentNode = null;
          }
          const index = liveRegions.indexOf(this);
          if (index >= 0) liveRegions.splice(index, 1);
        },
      };
    },
  };
  const listeners = new Map();
  const rootListeners = new Map();
  let aiControls = [];
  let aiModalControls = [];
  let aiProviderForm = null;
  let aiProviderTransportWarning = null;
  let aiDialog = null;
  let authoringControls = [];
  let authoringModalControls = [];
  let authoringDialog = null;
  let localRemovalControls = [];
  let localRemovalModalControls = [];
  let localRemovalDialog = null;
  const applyDefaultTabNavigation = (event) => {
    if (options.realisticFocusLifecycle !== true
      || event.key !== "Tab"
      || event.defaultPrevented) return;
    const focusable = (localRemovalModalControls.length
      ? localRemovalModalControls
      : authoringModalControls.length ? authoringModalControls : aiModalControls)
      .filter((control) => !control.disabled);
    if (!focusable.length) return;
    const currentIndex = focusable.indexOf(ownerDocument.activeElement);
    const direction = event.shiftKey === true ? -1 : 1;
    const nextIndex = currentIndex < 0
      ? (direction > 0 ? 0 : focusable.length - 1)
      : (currentIndex + direction + focusable.length) % focusable.length;
    focusable[nextIndex].focus();
  };
  const shell = {
    addEventListener(type, listener) {
      const typeListeners = listeners.get(type) ?? [];
      typeListeners.push(listener);
      listeners.set(type, typeListeners);
    },
    dispatch(type, event) {
      const originalPreventDefault = event?.preventDefault;
      const dispatchedEvent = {
        ...event,
        currentTarget: this,
        defaultPrevented: event?.defaultPrevented === true,
        preventDefault() {
          this.defaultPrevented = true;
          originalPreventDefault?.();
        },
      };
      for (const listener of listeners.get(type) ?? []) {
        listener(dispatchedEvent);
      }
      if (type === "keydown") applyDefaultTabNavigation(dispatchedEvent);
      return dispatchedEvent;
    },
  };
  const filter = {
    id: "sot-tree-filter",
    value: "",
    focus() {
      ownerDocument.activeElement = this;
    },
    setSelectionRange() {},
  };
  const eventControl = () => {
    const controlListeners = new Map();
    return {
      addEventListener(type, listener) {
        controlListeners.set(type, listener);
      },
      dispatch(type, event = {}) {
        return controlListeners.get(type)?.({ ...event, currentTarget: this, target: event.target ?? this });
      },
    };
  };
  const createCheckoutInput = () => ({
    inAppRoot: true,
    value: "",
    focus() {
      ownerDocument.activeElement = this;
    },
  });
  let checkoutInput = createCheckoutInput();
  const projectSearch = {
    id: "local-project-search",
    inAppRoot: true,
    value: "",
    selectionStart: 0,
    selectionEnd: 0,
    selectionDirection: "none",
    focus() {
      ownerDocument.activeElement = this;
    },
  };
  const projectList = { innerHTML: "" };
  const preferredWidths = {
    textContent: "",
    ariaLabel: "",
    setAttribute(name, value) {
      if (name === "aria-label") this.ariaLabel = String(value);
    },
    getAttribute(name) {
      return name === "aria-label" ? this.ariaLabel : null;
    },
  };
  const workspaceLayoutNotice = {
    hidden: true,
    textContent: "",
    dataset: {},
    classList: createClassList(),
  };
  const repoForm = eventControl();
  repoForm.elements = {
    namedItem(name) {
      return name === "checkoutPath" ? checkoutInput : null;
    },
  };
  const cloneControl = eventControl();
  const dashboardRefreshControl = eventControl();
  let graphToggle = null;
  let matrixToggle = null;
  let mapBody = null;
  let matrixBody = null;
  let componentMap = null;
  let sotWorkbench = null;
  let sceneHost = null;
  let semanticHost = null;
  let rendererState = null;
  let graphScale = null;
  let identityOverlay = null;
  let identityTitle = null;
  let identityKind = null;
  let identityCount = null;
  let treeItems = [];
  let semanticGraphItems = [];
  let semanticWorkflowItems = [];
  let graphRetryControl = null;
  let graphZoomItems = [];
  let profileItems = [];
  let dashboardItems = [];
  let localActionItems = [];
  let localInstanceItems = [];
  const createActionStatus = () => {
    const attributes = new Map();
    return {
      dataset: {},
      classList: createClassList("local-action-status local-action-status--idle"),
      hidden: true,
      textContent: "",
      getAttribute: (name) => attributes.get(name) ?? null,
      setAttribute: (name, value) => attributes.set(name, String(value)),
    };
  };
  let actionStatus = createActionStatus();
  let workbenchLayoutBlocked = false;
  const pendingWorkbenchClampScrollEvents = [];
  const createWorkbench = () => {
    const workbenchListeners = new Map();
    let scrollTop = 0;
    return {
      get scrollTop() {
        return scrollTop;
      },
      set scrollTop(value) {
        const requestedScrollTop = Number(value) || 0;
        scrollTop = workbenchLayoutBlocked ? 0 : requestedScrollTop;
        if (options.emitWorkbenchClampScrollAfterRender === true
          && workbenchLayoutBlocked
          && requestedScrollTop > 0) {
          pendingWorkbenchClampScrollEvents.push(() => {
            workbenchListeners.get("scroll")?.({ currentTarget: this, target: this });
          });
        }
      },
      getBoundingClientRect() {
        return { top: 120, left: 0, width: 960, height: 640, right: 960, bottom: 760 };
      },
      addEventListener(type, listener) {
        workbenchListeners.set(type, listener);
      },
      dispatch(type) {
        workbenchListeners.get(type)?.({ currentTarget: this, target: this });
      },
    };
  };
  let workbench = createWorkbench();
  const createDisclosureElement = (tag = "") => {
    const attributes = new Map(
      [...tag.matchAll(/([\w-]+)="([^"]*)"/g)].map(([, name, value]) => [name, value]),
    );
    const elementListeners = new Map();
    return {
      ownerDocument,
      hidden: /(?:^|\s)hidden(?:\s|>)/.test(tag),
      textContent: "",
      classList: createClassList(attributes.get("class") ?? ""),
      setAttribute(name, value) {
        attributes.set(name, String(value));
      },
      getAttribute(name) {
        return attributes.get(name) ?? null;
      },
      contains() {
        return false;
      },
      focus() {
        ownerDocument.activeElement = this;
      },
      matches(selector) {
        return selector === ".sot-workbench"
          ? this === sotWorkbench
          : selector === ".pane--workbench" && this === workbench;
      },
      closest(selector) {
        return selector === ".pane--workbench" ? workbench : null;
      },
      addEventListener(type, listener) {
        elementListeners.set(type, listener);
      },
      removeEventListener(type, listener) {
        if (elementListeners.get(type) === listener) elementListeners.delete(type);
      },
      dispatch(type, event) {
        elementListeners.get(type)?.(event);
      },
    };
  };
  const createSceneHost = (tag = "") => {
    const host = createDisclosureElement(tag);
    host.children = [];
    host.appendChild = function appendChild(child) {
      child.remove?.();
      this.children.push(child);
      child.parentNode = this;
      return child;
    };
    host.getBoundingClientRect = () => ({ width: 960, height: 540 });
    return host;
  };
  const createSemanticHost = (tag = "") => {
    const host = createDisclosureElement(tag);
    let markup = "";
    Object.defineProperty(host, "innerHTML", {
      get() {
        return markup;
      },
      set(nextMarkup) {
        markup = String(nextMarkup ?? "");
        const controls = [...markup.matchAll(
          /<button\b(?=[^>]*(?:data-semantic-node-id|data-semantic-workflow-step|data-graph-retry))[^>]*>/g,
        )].map(([controlTag]) => createControl(ownerDocument, controlTag));
        semanticGraphItems = controls.filter((control) => control.dataset.semanticNodeId);
        semanticWorkflowItems = controls.filter((control) => control.dataset.semanticWorkflowStep);
        graphRetryControl = controls.find(
          (control) => control.dataset.graphRetry !== undefined,
        ) ?? null;
      },
    });
    return host;
  };
  const createTreeScroll = () => {
    const treeScrollListeners = new Map();
    return {
      scrollTop: 0,
      addEventListener(type, listener) {
        treeScrollListeners.set(type, listener);
      },
      dispatch(type) {
        treeScrollListeners.get(type)?.({ currentTarget: this, target: this });
      },
    };
  };
  let treeScroll = createTreeScroll();
  const parseTreeItems = (markup) => {
    treeItems = [...markup.matchAll(/<button(?=[^>]*role="treeitem")[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag));
  };
  const parseProfileItems = (markup) => {
    profileItems = [...markup.matchAll(/<button(?=[^>]*data-profile-id="[^"]+")[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag));
  };
  const parseGraphToggle = (markup) => {
    const match = markup.match(
      /(<button(?=[^>]*data-sot-graph-toggle)[^>]*>)([^<]*)<\/button>/,
    );
    graphToggle = match ? createControl(ownerDocument, match[1]) : null;
    if (graphToggle) graphToggle.textContent = match[2];
  };
  const parseDisclosureElements = (markup) => {
    semanticGraphItems = [];
    semanticWorkflowItems = [];
    graphRetryControl = null;
    graphZoomItems = [];
    const matrixMatch = markup.match(
      /(<button(?=[^>]*data-sot-matrix-toggle)[^>]*>)([^<]*)<\/button>/,
    );
    matrixToggle = matrixMatch ? createControl(ownerDocument, matrixMatch[1]) : null;
    if (matrixToggle) matrixToggle.textContent = matrixMatch[2];
    const mapBodyTag = markup.match(/<div(?=[^>]*id="component-map-body")[^>]*>/)?.[0];
    const matrixBodyTag = markup.match(/<section(?=[^>]*id="profile-matrix-body")[^>]*>/)?.[0];
    const componentMapTag = markup.match(/<section(?=[^>]*class="[^"]*component-map)[^>]*>/)?.[0];
    const sotWorkbenchTag = markup.match(/<div(?=[^>]*class="[^"]*sot-workbench)[^>]*>/)?.[0];
    const sceneHostTag = markup.match(/<div(?=[^>]*data-component-map-scene-host)[^>]*>/)?.[0];
    const semanticHostTag = markup.match(/<div(?=[^>]*data-component-map-semantic-host)[^>]*>/)?.[0];
    const rendererStateTag = markup.match(/<div(?=[^>]*data-graph-renderer-state)[^>]*>/)?.[0];
    const graphScaleTag = markup.match(/<output(?=[^>]*data-graph-scale)[^>]*>/)?.[0];
    const identityOverlayTag = markup.match(/<div(?=[^>]*data-graph-identity-overlay)[^>]*>/)?.[0];
    const identityTitleTag = markup.match(/<strong(?=[^>]*data-graph-identity-title)[^>]*>/)?.[0];
    const identityKindTag = markup.match(/<span(?=[^>]*data-graph-identity-kind)[^>]*>/)?.[0];
    const identityCountTag = markup.match(/<span(?=[^>]*data-graph-identity-count)[^>]*>/)?.[0];
    mapBody = mapBodyTag ? createDisclosureElement(mapBodyTag) : null;
    matrixBody = matrixBodyTag ? createDisclosureElement(matrixBodyTag) : null;
    componentMap = componentMapTag ? createDisclosureElement(componentMapTag) : null;
    sotWorkbench = sotWorkbenchTag ? createDisclosureElement(sotWorkbenchTag) : null;
    sceneHost = sceneHostTag ? createSceneHost(sceneHostTag) : null;
    semanticHost = semanticHostTag ? createSemanticHost(semanticHostTag) : null;
    rendererState = rendererStateTag ? createDisclosureElement(rendererStateTag) : null;
    graphScale = graphScaleTag ? createDisclosureElement(graphScaleTag) : null;
    identityOverlay = identityOverlayTag ? createDisclosureElement(identityOverlayTag) : null;
    identityTitle = identityTitleTag ? createDisclosureElement(identityTitleTag) : null;
    identityKind = identityKindTag ? createDisclosureElement(identityKindTag) : null;
    identityCount = identityCountTag ? createDisclosureElement(identityCountTag) : null;
    graphZoomItems = [...markup.matchAll(/<button(?=[^>]*data-graph-zoom="[^"]+")[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag));
    const querySelector = (selector) => ({
      "[data-sot-graph-toggle]": graphToggle,
      "[data-sot-matrix-toggle]": matrixToggle,
      "#component-map-body": mapBody,
      "#profile-matrix-body": matrixBody,
      ".component-map": componentMap,
      ".pane--workbench": workbench,
      "[data-component-map-scene-host]": sceneHost,
      "[data-component-map-semantic-host]": semanticHost,
      "[data-graph-renderer-state]": rendererState,
      "[data-graph-scale]": graphScale,
      "[data-graph-identity-overlay]": identityOverlay,
      "[data-graph-identity-title]": identityTitle,
      "[data-graph-identity-kind]": identityKind,
      "[data-graph-identity-count]": identityCount,
    })[selector] ?? null;
    if (sotWorkbench) {
      sotWorkbench.querySelector = querySelector;
      sotWorkbench.querySelectorAll = (selector) => {
        const match = querySelector(selector);
        if (options.disclosureFault === "missing-map-body"
          && selector === "#component-map-body") return [];
        if (options.disclosureFault === "duplicate-matrix-toggle"
          && selector === "[data-sot-matrix-toggle]" && match) return [match, match];
        if (selector === "[data-graph-zoom]") return graphZoomItems;
        return match ? [match] : [];
      };
    }
  };
  const parseDashboardItems = (markup) => {
    dashboardItems = [...markup.matchAll(/<button(?=[^>]*data-dashboard-segment="[^"]+")[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag));
  };
  const parseLocalActionItems = (markup) => {
    localActionItems = [...markup.matchAll(/<button(?=[^>]*data-local-action="[^"]+")[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag));
  };
  const parseLocalInstanceItems = (markup) => {
    const hasSourceChunkNavigation = markup.includes("data-source-chunk-range");
    localInstanceItems = [...markup.matchAll(/<button(?=[^>]*data-local-instance="[^"]+")[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag));
    localInstanceItems.forEach((control, index) => {
      control.getBoundingClientRect = () => {
        const workbenchTop = workbench.getBoundingClientRect().top;
        const sourcePreviewReflow = hasSourceChunkNavigation ? 48 : 0;
        const top = workbenchTop + 180 + (index * 72) + sourcePreviewReflow - workbench.scrollTop;
        return { top, left: 0, width: 680, height: 64, right: 680, bottom: top + 64 };
      };
    });
  };
  const findAiControl = (selector, controls = aiControls) => {
    if (selector === "[data-open-ai-settings]") {
      return controls.find((control) => control.dataset.openAiSettings !== undefined) ?? null;
    }
    if (selector === "[data-ai-provider-close]") {
      return controls.find((control) => control.dataset.aiProviderClose !== undefined) ?? null;
    }
    if (selector === "[data-ai-provider-key-delete]") {
      return controls.find((control) => control.dataset.aiProviderKeyDelete !== undefined) ?? null;
    }
    if (selector === "[data-ai-explain]") {
      return controls.find((control) => control.dataset.aiExplain !== undefined) ?? null;
    }
    if (selector === '[data-ai-provider-form] input[name="baseUrl"]') {
      return controls.find((control) => control.name === "baseUrl") ?? null;
    }
    if (selector === '[data-ai-provider-form] button[type="submit"]') {
      return controls.find((control) => control.type === "submit") ?? null;
    }
    return null;
  };
  const parseAiControls = (markup) => {
    const backgroundControls = [...markup.matchAll(/<(?:button|input)\b[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag))
      .filter((control) => control.dataset.openAiSettings !== undefined
        || control.dataset.aiExplain !== undefined);
    const modalMarker = markup.indexOf("data-ai-provider-dialog");
    const modalMarkup = modalMarker >= 0 ? markup.slice(modalMarker) : "";
    aiModalControls = [...modalMarkup.matchAll(/<(?:button|input)\b[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag));
    aiControls = [...backgroundControls, ...aiModalControls];
    const warningTag = modalMarkup.match(/<p(?=[^>]*data-ai-provider-transport-warning)[^>]*>/)?.[0];
    aiProviderTransportWarning = warningTag ? {
      hidden: /\bhidden\b/.test(warningTag),
      textContent: modalMarkup.match(/<p[^>]*data-ai-provider-transport-warning[^>]*>([^<]*)<\/p>/)?.[1] ?? "",
      dataset: {
        aiProviderTransportWarning: warningTag.match(/data-ai-provider-transport-warning="([^"]*)"/)?.[1] ?? "",
      },
    } : null;
    if (modalMarker < 0) {
      aiProviderForm = null;
      aiDialog = null;
      return;
    }
    aiProviderForm = {
      inAppRoot: true,
      elements: {
        namedItem(name) {
          return aiModalControls.find((control) => control.name === name) ?? null;
        },
      },
      closest(selector) {
        return selector === "[data-ai-provider-form]" ? this : null;
      },
    };
    aiDialog = {
      inAppRoot: true,
      contains(node) {
        return aiModalControls.includes(node);
      },
      querySelector(selector) {
        return findAiControl(selector, aiModalControls);
      },
      querySelectorAll() {
        return aiModalControls.filter((control) => !control.disabled);
      },
    };
  };
  const parseAuthoringControls = (markup) => {
    authoringControls = [...markup.matchAll(/<button\b[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag))
      .filter((control) => control.dataset.openAuthoringFlow !== undefined);
    const modalMarker = markup.indexOf("data-authoring-flow-dialog");
    const modalMarkup = modalMarker >= 0 ? markup.slice(modalMarker) : "";
    authoringModalControls = [...modalMarkup.matchAll(/<button\b[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag))
      .filter((control) => control.dataset.closeAuthoringFlow !== undefined);
    if (modalMarker < 0) {
      authoringDialog = null;
      return;
    }
    authoringDialog = {
      inAppRoot: true,
      contains(node) {
        return authoringModalControls.includes(node);
      },
      querySelectorAll() {
        return authoringModalControls.filter((control) => !control.disabled);
      },
    };
  };
  const parseLocalRemovalControls = (markup) => {
    localRemovalControls = [...markup.matchAll(/<(?:button|input)\b[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag))
      .filter((control) => control.dataset.localRemovalSelection
        || control.dataset.enterLocalRemoval !== undefined
        || control.dataset.prepareLocalRemoval !== undefined
        || control.dataset.cancelLocalRemovalSelection !== undefined
        || control.dataset.closeLocalRemoval !== undefined
        || control.dataset.applyLocalRemoval !== undefined
        || control.dataset.localRemovalAck !== undefined);
    const modalMarker = markup.indexOf("data-local-removal-dialog");
    const modalMarkup = modalMarker >= 0 ? markup.slice(modalMarker) : "";
    localRemovalModalControls = [...modalMarkup.matchAll(/<(?:button|input)\b[^>]*>/g)]
      .map(([tag]) => createControl(ownerDocument, tag));
    if (modalMarker < 0) {
      localRemovalDialog = null;
      return;
    }
    localRemovalDialog = {
      inAppRoot: true,
      contains(node) {
        return localRemovalModalControls.includes(node);
      },
      querySelector(selector) {
        return localRemovalModalControls.find((control) => control.closest(selector)) ?? null;
      },
      querySelectorAll() {
        return localRemovalModalControls.filter((control) => !control.disabled);
      },
    };
  };
  const treeRegion = {
    set innerHTML(markup) {
      parseTreeItems(markup);
      treeScroll = createTreeScroll();
    },
  };
  const inertRegion = { innerHTML: "" };
  let localInspectorMarkup = "";
  const localInspectorRegion = {
    get innerHTML() {
      return localInspectorMarkup;
    },
    set innerHTML(markup) {
      localInspectorMarkup = String(markup ?? "");
      parseLocalActionItems(localInspectorMarkup);
      parseAiControls(localInspectorMarkup);
    },
    querySelector() {
      return null;
    },
  };
  const localStatusStack = { innerHTML: "" };
  const localRuntimeStatus = { textContent: "" };
  let shellMarkup = "";
  const root = {
    ownerDocument,
    shell,
    addEventListener(type, listener) {
      rootListeners.set(type, listener);
    },
    removeEventListener(type, listener) {
      if (rootListeners.get(type) === listener) rootListeners.delete(type);
    },
    matches() {
      return false;
    },
    closest() {
      return null;
    },
    dispatch(type, event = {}) {
      const dispatchedEvent = {
        ...event,
        target: event.target,
        preventDefault: event.preventDefault ?? (() => {}),
      };
      rootListeners.get(type)?.(dispatchedEvent);
      sotWorkbench?.dispatch(type, dispatchedEvent);
    },
    get treeItems() {
      return treeItems;
    },
    get semanticGraphItems() {
      return semanticGraphItems;
    },
    get semanticWorkflowItems() {
      return semanticWorkflowItems;
    },
    get semanticHost() {
      return semanticHost;
    },
    get graphRetryControl() {
      return graphRetryControl;
    },
    get graphScale() {
      return graphScale;
    },
    get identityTitle() {
      return identityTitle;
    },
    get identityOverlay() {
      return identityOverlay;
    },
    get identityKind() {
      return identityKind;
    },
    get identityCount() {
      return identityCount;
    },
    get profileItems() {
      return profileItems;
    },
    get graphToggle() {
      return graphToggle;
    },
    get matrixToggle() {
      return matrixToggle;
    },
    get sotWorkbench() {
      return sotWorkbench;
    },
    get dashboardItems() {
      return dashboardItems;
    },
    get localActionItems() {
      return localActionItems;
    },
    get localInstanceItems() {
      return localInstanceItems;
    },
    get liveRegion() {
      return liveRegions.find((node) => node.dataset.localActionAnnouncer !== undefined) ?? null;
    },
    get aiStatusAnnouncer() {
      return liveRegions.find((node) => node.dataset.localActionAnnouncer === undefined
        && node.getAttribute("role") === "status") ?? null;
    },
    get aiModalControls() {
      return aiModalControls;
    },
    get authoringOpenControl() {
      return authoringControls[0] ?? null;
    },
    get authoringModalControls() {
      return authoringModalControls;
    },
    get localRemovalControls() {
      return localRemovalControls;
    },
    get localRemovalModalControls() {
      return localRemovalModalControls;
    },
    get workbench() {
      return workbench;
    },
    blockWorkbenchLayout() {
      workbenchLayoutBlocked = true;
    },
    releaseWorkbenchLayout() {
      workbenchLayoutBlocked = false;
    },
    flushWorkbenchClampScrollEvents() {
      while (pendingWorkbenchClampScrollEvents.length) {
        pendingWorkbenchClampScrollEvents.shift()();
      }
    },
    get treeScroll() {
      return treeScroll;
    },
    get actionStatus() {
      return actionStatus;
    },
    get cloneControl() {
      return cloneControl;
    },
    get repoForm() {
      return repoForm;
    },
    get checkoutInput() {
      return checkoutInput;
    },
    get projectSearch() {
      return projectSearch;
    },
    get projectList() {
      return projectList;
    },
    get preferredWidths() {
      return preferredWidths;
    },
    get markup() {
      return `${shellMarkup}\n${localInspectorMarkup}`;
    },
    renderCount: 0,
    set innerHTML(markup) {
      this.renderCount += 1;
      listeners.clear();
      shellMarkup = String(markup ?? "");
      localInspectorMarkup = "";
      if (options.realisticFocusLifecycle === true
        && ownerDocument.activeElement?.inAppRoot === true) {
        ownerDocument.activeElement = ownerDocument.body;
      } else if (options.replaceCheckoutInputOnRender === true) {
        if (ownerDocument.activeElement?.inAppRoot === true) ownerDocument.activeElement = null;
      }
      if (options.replaceCheckoutInputOnRender === true) checkoutInput = createCheckoutInput();
      checkoutInput.value = markup
        .match(/id="checkout-path"[^>]*value="([^"]*)"/)?.[1]
        ?.replaceAll("&quot;", '"')
        .replaceAll("&#039;", "'")
        .replaceAll("&lt;", "<")
        .replaceAll("&gt;", ">")
        .replaceAll("&amp;", "&") ?? "";
      actionStatus = createActionStatus();
      workbench = createWorkbench();
      treeScroll = createTreeScroll();
      parseTreeItems(markup);
      parseProfileItems(markup);
      parseGraphToggle(markup);
      parseDisclosureElements(markup);
      parseDashboardItems(markup);
      parseLocalActionItems(markup);
      parseLocalInstanceItems(markup);
      parseAiControls(markup);
      parseAuthoringControls(markup);
      parseLocalRemovalControls(markup);
      const preferredMatch = markup.match(
        /id="workspace-preferred-widths"[^>]*aria-label="([^"]*)"[^>]*>([^<]*)<\/span>/,
      );
      preferredWidths.ariaLabel = preferredMatch?.[1] ?? "";
      preferredWidths.textContent = preferredMatch?.[2] ?? "";
    },
    querySelectorAll(selector) {
      if (selector === "[data-sot-graph-toggle]") return graphToggle ? [graphToggle] : [];
      if (selector === "[data-sot-matrix-toggle]") return matrixToggle ? [matrixToggle] : [];
      if (selector === "#component-map-body") return mapBody ? [mapBody] : [];
      if (selector === "#profile-matrix-body") return matrixBody ? [matrixBody] : [];
      if (selector === "[data-component-id]") return treeItems.filter((item) => item.dataset.componentId);
      if (selector.includes("[role=treeitem]")) return treeItems;
      if (selector === "[data-profile-id]") {
        return profileItems;
      }
      if (selector === "[data-dashboard-segment]") return dashboardItems;
      if (selector === "[data-local-action]") return localActionItems;
      if (selector === "[data-local-instance]") return localInstanceItems;
      if (selector === "[data-local-removal-selection]") {
        return localRemovalControls.filter((control) => control.dataset.localRemovalSelection);
      }
      if (selector.includes("[data-ai-provider-dialog]")) {
        return aiModalControls.filter((control) => !control.disabled);
      }
      if (selector.includes("[data-authoring-flow-dialog]")) {
        return authoringModalControls.filter((control) => !control.disabled);
      }
      return [];
    },
    querySelector(selector) {
      if (selector === ".desktop-app") return shell;
      if (selector === "[data-sot-tree]") return treeRegion;
      if (selector === "#sot-tree-filter") return filter;
      if (selector === "[data-sot-tree-scroll]") return treeScroll;
      if (selector === "[data-sot-graph-toggle]") return graphToggle;
      if (selector === "[data-sot-matrix-toggle]") return matrixToggle;
      if (selector === "#component-map-body" || selector === "[data-component-map-body]") return mapBody;
      if (selector === "#profile-matrix-body") return matrixBody;
      if (selector === ".component-map") return componentMap;
      if (selector === ".sot-workbench") return sotWorkbench;
      if (selector === "[data-component-map-scene-host]") return sceneHost;
      if (selector === "[data-component-map-semantic-host]") return semanticHost;
      if (selector === "[data-graph-renderer-state]") return rendererState;
      if (selector === "[data-graph-scale]") return graphScale;
      if (selector === "[data-graph-identity-overlay]") return identityOverlay;
      if (selector === "[data-graph-identity-title]") return identityTitle;
      if (selector === "[data-graph-identity-kind]") return identityKind;
      if (selector === "[data-graph-identity-count]") return identityCount;
      if (selector === ".pane--workbench") return workbench;
      if (selector === "#clone-checkout") return cloneControl;
      if (selector === "#repo-form") return repoForm;
      if (selector === "#checkout-path") return checkoutInput;
      if (selector === "#local-project-search") return projectSearch;
      if (selector === "[data-local-project-list]") return projectList;
      if (selector === "#dashboard-refresh") return dashboardRefreshControl;
      if (selector === "#workspace-preferred-widths") return preferredWidths;
      if (selector === ".workspace-layout-save-state") return workspaceLayoutNotice;
      if (selector === "[data-sot-inspector]" || selector === ".profile-member-region") return inertRegion;
      if (selector === "[data-local-inspector]") return localInspectorRegion;
      if (selector === "[data-local-status-stack]") return localStatusStack;
      if (selector === "#local-runtime-status") return localRuntimeStatus;
      if (selector === "[data-sot-install-host]") return inertRegion;
      if (selector === "#workbench") return workbench;
      if (selector === "[data-local-action-status]") return actionStatus;
      if (selector === "[data-ai-provider-dialog]") return aiDialog;
      if (selector === "[data-ai-provider-transport-warning]") return aiProviderTransportWarning;
      if (selector === "[data-authoring-flow-dialog]") return authoringDialog;
      if (selector === "[data-local-removal-dialog]") return localRemovalDialog;
      if (selector === "[data-open-authoring-flow]") return authoringControls[0] ?? null;
      if (selector === "[data-close-authoring-flow]") return authoringModalControls[0] ?? null;
      if (selector === "[data-enter-local-removal]") {
        return localRemovalControls.find((control) => control.dataset.enterLocalRemoval !== undefined) ?? null;
      }
      if (selector === "[data-prepare-local-removal]") {
        return localRemovalControls.find((control) => control.dataset.prepareLocalRemoval !== undefined) ?? null;
      }
      if (selector === "[data-local-removal-ack]") {
        return localRemovalModalControls.find((control) => control.dataset.localRemovalAck !== undefined) ?? null;
      }
      if (selector === "[data-ai-provider-form]") return aiProviderForm;
      const aiControl = findAiControl(selector);
      if (aiControl) return aiControl;
      if (selector.startsWith('[data-dashboard-segment="')) {
        const dashboardSegment = selector.slice(25, -2);
        return dashboardItems.find((item) => item.dataset.dashboardSegment === dashboardSegment) ?? null;
      }
      return null;
    },
  };
  return root;
}

function createAnimationFrameHarness() {
  const pending = new Map();
  let nextFrameId = 1;

  const flushNext = () => {
    const entry = pending.entries().next().value;
    assert.ok(entry, "expected a pending animation frame");
    const [frameId, callback] = entry;
    pending.delete(frameId);
    callback(0);
    return frameId;
  };

  return {
    get size() {
      return pending.size;
    },
    requestAnimationFrame(callback) {
      const frameId = nextFrameId;
      nextFrameId += 1;
      pending.set(frameId, callback);
      return frameId;
    },
    cancelAnimationFrame(frameId) {
      pending.delete(frameId);
    },
    flushNext,
    flushAll() {
      while (pending.size) flushNext();
    },
    peekNext() {
      return pending.entries().next().value;
    },
  };
}

function createLocalInstanceItems(prefix, displayLabel, count = 24) {
  return Array.from({ length: count }, (_, index) => ({
    instanceId: `instance-${prefix}-${index}`,
    displayName: `${displayLabel} result ${index}`,
    adapterId: "codex",
    adapterVersion: "1",
    toolId: "codex",
    surfaceId: "skills",
    scope: "user",
    projectId: null,
    safeLocator: `skills/${prefix}-${index}/SKILL.md`,
    kind: "skill",
    parseState: "parsed",
    issueCodes: [],
    correlation: { state: "uncorrelated" },
  }));
}

function delegatedTarget(selector, dataset) {
  return {
    closest(candidate) {
      return candidate === selector ? { dataset } : null;
    },
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolveValue, rejectValue) => {
    resolve = resolveValue;
    reject = rejectValue;
  });
  return { promise, resolve, reject };
}

async function flushMicrotasks(rounds = 12) {
  for (let index = 0; index < rounds; index += 1) await Promise.resolve();
}

function aiEndpoint(scheme, authority) {
  return `${scheme}://${authority}/v1`;
}

function aiProviderForm(values) {
  const fields = Object.fromEntries(
    ["baseUrl", "model", "apiKey"].map((name) => [name, { value: values[name] ?? "" }]),
  );
  const form = {
    id: "",
    dataset: { aiProviderForm: "" },
    elements: {
      namedItem(name) {
        return fields[name] ?? null;
      },
    },
    closest(selector) {
      return selector === "[data-ai-provider-form]" ? this : null;
    },
  };
  return { form, fields };
}

function configuredAiProvider(revision = "provider-ai-1", keyPresent = true) {
  return {
    state: "configured",
    base_url: "https://provider.example/v1",
    model: "gpt-4.1-mini",
    api_key_present: keyPresent,
    provider_revision: revision,
  };
}

function configuredAiExplanation(request) {
  return {
    snapshot_id: request.snapshotId,
    instance_id: request.instanceId,
    source_revision: request.sourceRevision,
    provider_revision: request.providerRevision,
    doing: `doing ${request.instanceId}`,
    when_used: `when ${request.instanceId}`,
    capabilities: `capabilities ${request.instanceId}`,
    cautions: `cautions ${request.instanceId}`,
  };
}

function localAiItem(instanceId) {
  return {
    instanceId,
    adapterId: "codex",
    adapterVersion: "1",
    toolId: "codex",
    surfaceId: "skills",
    scope: "user",
    projectId: null,
    safeLocator: `skills/${instanceId}/SKILL.md`,
    kind: "skill",
    name: instanceId,
    parseState: "parsed",
    issueCodes: [],
    settings: [],
    correlation: { state: "uncorrelated" },
  };
}

test("Local skill import preview requires explicit confirmation then refreshes SoT", async () => {
  const root = createMountRoot();
  let previews = 0;
  let confirmations = 0;
  let loads = 0;
  const item = localAiItem("fixture-import");
  const imported = { ...snapshot, snapshot_id: "imported", components: [...snapshot.components, { ...snapshot.components[1], component_id: "harnesskit.skill.fixture-import" }] };
  const app = mountApp(root, {
    async getSotSessionState() { return { checkout_id: "checkout-import", sot_snapshot_id: snapshot.snapshot_id }; },
    async loadSotSnapshot() { loads += 1; return loads > 1 ? imported : snapshot; },
    async getLocalScanState() { return { state_revision: 1, current_attempt: null, latest_terminal_report: { attempt_id: "scan-import", state: "complete", error_code: null }, latest_complete: { snapshot_id: "local-import", attempt_id: "scan-import", status: "complete" }, latest_partial: null }; },
    async queryLocalInstances() { return { snapshotId: "local-import", items: [item], kindCounts: [{ kind: "skill", count: 1 }], counts: { totalInstances: 1, matchedInstances: 1 } }; },
    async getLocalInstanceDetail() { return item; },
    async openLocalSourcePreview(r) { return { preview_session_id: "source-import", view_generation: r.viewGeneration, snapshot_id: "local-import", instance_id: item.instanceId, canonical_path: "/fixture/skills/fixture-import/SKILL.md", changed_since_snapshot: false, issue: null, source_revision: "a".repeat(64), format: "text", total_bytes: 10, total_chunks: 1, chunk_bytes: 65536, selected_chunk_index: 0 }; },
    async readLocalSourcePreviewChunk(r) { return { preview_session_id: r.previewSessionId, source_revision: r.sourceRevision, chunk_index: 0, is_last: true, content: { format: "text", before: "fixture", selected: null, after: "" } }; },
    async closeLocalSourcePreview() { return {}; },
    async previewComponentImport(r) { previews += 1; assert.equal(r.instanceId, item.instanceId); assert.equal(r.sourceRevision, "a".repeat(64)); assert.equal(r.sourcePath, undefined); return { preview_id: "preview-import", fingerprint: "b".repeat(64), component_id: "harnesskit.skill.fixture-import", name: "fixture-import", kind: "skill", content: "# Fixture", manifest: "kind: skill", generated_artifact_count: 2 }; },
    async confirmComponentImport(r) { confirmations += 1; assert.equal(r.confirmed, true); return imported; },
  });
  await flushMicrotasks(24);
  root.dashboardItems.find((c) => c.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: root.localInstanceItems[0] });
  await flushMicrotasks(24);
  assert.equal(app.getState().sourcePreview?.phase, "ready", JSON.stringify(app.getState().sourcePreview));
  assert.equal(app.getState().repo.checkoutId, "checkout-import", JSON.stringify(app.getState().repo));
  root.shell.dispatch("click", { target: delegatedTarget("[data-preview-component-import]", {}) });
  await flushMicrotasks(24);
  assert.equal(previews, 1);
  assert.equal(confirmations, 0);
  assert.equal(app.getState().componentImport.preview.component_id, "harnesskit.skill.fixture-import");
  root.shell.dispatch("keydown", { key: "Escape", target: delegatedTarget("[data-cancel-component-import]", {}) });
  assert.equal(app.getState().componentImport, null);
  assert.equal(confirmations, 0);
  root.shell.dispatch("click", { target: delegatedTarget("[data-preview-component-import]", {}) });
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: delegatedTarget("[data-confirm-component-import]", {}) });
  await flushMicrotasks(24);
  assert.equal(confirmations, 1);
  assert.equal(app.getState().ui.activeSegment, "sot");
  assert.equal(app.getState().sot.snapshot.snapshot_id, "imported");
  assert.equal(loads, 2);
  app.destroy();
});

test("Local Claude agent import preview requires explicit confirmation then refreshes SoT", async () => {
  const root = createMountRoot();
  let previews = 0;
  let confirmations = 0;
  let loads = 0;
  const item = { ...localAiItem("fixture-import"), kind: "agent", toolId: "claude_code" };
  const imported = { ...snapshot, snapshot_id: "imported", components: [...snapshot.components, { ...snapshot.components[1], component_id: "harnesskit.agent.fixture-import" }] };
  const app = mountApp(root, {
    async getSotSessionState() { return { checkout_id: "checkout-import", sot_snapshot_id: snapshot.snapshot_id }; },
    async loadSotSnapshot() { loads += 1; return loads > 1 ? imported : snapshot; },
    async getLocalScanState() { return { state_revision: 1, current_attempt: null, latest_terminal_report: { attempt_id: "scan-import", state: "complete", error_code: null }, latest_complete: { snapshot_id: "local-import", attempt_id: "scan-import", status: "complete" }, latest_partial: null }; },
    async queryLocalInstances() { return { snapshotId: "local-import", items: [item], kindCounts: [{ kind: "agent", count: 1 }], counts: { totalInstances: 1, matchedInstances: 1 } }; },
    async getLocalInstanceDetail() { return item; },
    async openLocalSourcePreview(r) { return { preview_session_id: "source-import", view_generation: r.viewGeneration, snapshot_id: "local-import", instance_id: item.instanceId, canonical_path: "/fixture/.claude/agents/fixture-import.md", changed_since_snapshot: false, issue: null, source_revision: "a".repeat(64), format: "text", total_bytes: 10, total_chunks: 1, chunk_bytes: 65536, selected_chunk_index: 0 }; },
    async readLocalSourcePreviewChunk(r) { return { preview_session_id: r.previewSessionId, source_revision: r.sourceRevision, chunk_index: 0, is_last: true, content: { format: "text", before: "fixture", selected: null, after: "" } }; },
    async closeLocalSourcePreview() { return {}; },
    async previewComponentImport(r) { previews += 1; assert.equal(r.instanceId, item.instanceId); assert.equal(r.sourceRevision, "a".repeat(64)); assert.equal(r.sourcePath, undefined); return { preview_id: "preview-import", fingerprint: "b".repeat(64), component_id: "harnesskit.agent.fixture-import", name: "fixture-import", kind: "agent", content: "# Fixture", manifest: "kind: agent", generated_artifact_count: 2 }; },
    async confirmComponentImport(r) { confirmations += 1; assert.equal(r.confirmed, true); return imported; },
  });
  await flushMicrotasks(24);
  root.dashboardItems.find((c) => c.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: root.localInstanceItems[0] });
  await flushMicrotasks(24);
  assert.equal(app.getState().sourcePreview?.phase, "ready", JSON.stringify(app.getState().sourcePreview));
  assert.equal(app.getState().repo.checkoutId, "checkout-import", JSON.stringify(app.getState().repo));
  root.shell.dispatch("click", { target: delegatedTarget("[data-preview-component-import]", {}) });
  await flushMicrotasks(24);
  assert.equal(previews, 1);
  assert.equal(confirmations, 0);
  assert.equal(app.getState().componentImport.preview.component_id, "harnesskit.agent.fixture-import");
  root.shell.dispatch("keydown", { key: "Escape", target: delegatedTarget("[data-cancel-component-import]", {}) });
  assert.equal(app.getState().componentImport, null);
  assert.equal(confirmations, 0);
  root.shell.dispatch("click", { target: delegatedTarget("[data-preview-component-import]", {}) });
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: delegatedTarget("[data-confirm-component-import]", {}) });
  await flushMicrotasks(24);
  assert.equal(confirmations, 1);
  assert.equal(app.getState().ui.activeSegment, "sot");
  assert.equal(app.getState().sot.snapshot.snapshot_id, "imported");
  assert.equal(loads, 2);
  app.destroy();
});

test("Local Antigravity project agent and CLI managed rule import require explicit confirmation then refresh SoT", async () => {
  for (const isRule of [false, true]) {
    const root = createMountRoot();
    let previews = 0;
    let confirmations = 0;
    let loads = 0;
    const id = isRule ? "harnesskit.rule.imported-agents-antigravity-cli" : "harnesskit.agent.fixture-import";
    const item = { ...localAiItem("fixture-import"), kind: isRule ? "rule" : "agent", toolId: isRule ? "antigravity_cli" : "antigravity", scope: "project", safeLocator: isRule ? "AGENTS.md" : ".agents/agents/fixture-import.md" };
    const imported = { ...snapshot, snapshot_id: "imported", components: [...snapshot.components, { ...snapshot.components[1], component_id: id }] };
    const app = mountApp(root, {
      async getSotSessionState() { return { checkout_id: "checkout-import", sot_snapshot_id: snapshot.snapshot_id }; },
      async loadSotSnapshot() { loads += 1; return loads > 1 ? imported : snapshot; },
      async getLocalScanState() { return { state_revision: 1, current_attempt: null, latest_terminal_report: { attempt_id: "scan-import", state: "complete", error_code: null }, latest_complete: { snapshot_id: "local-import", attempt_id: "scan-import", status: "complete" }, latest_partial: null }; },
      async queryLocalInstances() { return { snapshotId: "local-import", items: [item], kindCounts: [{ kind: item.kind, count: 1 }], counts: { totalInstances: 1, matchedInstances: 1 } }; },
      async getLocalInstanceDetail() { return item; },
      async openLocalSourcePreview(r) { return { preview_session_id: "source-import", view_generation: r.viewGeneration, snapshot_id: "local-import", instance_id: item.instanceId, canonical_path: isRule ? "/fixture/AGENTS.md" : "/fixture/.agents/agents/fixture-import.md", changed_since_snapshot: false, issue: null, source_revision: "a".repeat(64), format: "text", total_bytes: 10, total_chunks: 1, chunk_bytes: 65536, selected_chunk_index: 0 }; },
      async readLocalSourcePreviewChunk(r) { return { preview_session_id: r.previewSessionId, source_revision: r.sourceRevision, chunk_index: 0, is_last: true, content: { format: "text", before: "fixture", selected: null, after: "" } }; },
      async closeLocalSourcePreview() { return {}; },
      async previewComponentImport(r) { previews += 1; assert.equal(r.instanceId, item.instanceId); assert.equal(r.sourceRevision, "a".repeat(64)); assert.equal(r.sourcePath, undefined); return { preview_id: "preview-import", fingerprint: "b".repeat(64), component_id: id, name: "fixture-import", kind: item.kind, content: "# Fixture", manifest: `kind: ${item.kind}`, generated_artifact_count: isRule ? 1 : 2 }; },
      async confirmComponentImport(r) { confirmations += 1; assert.equal(r.confirmed, true); return imported; },
    });
    await flushMicrotasks(24);
    root.dashboardItems.find((c) => c.dataset.dashboardSegment === "local").dispatch("click");
    await flushMicrotasks(24);
    root.shell.dispatch("click", { target: root.localInstanceItems[0] });
    await flushMicrotasks(24);
    assert.equal(app.getState().sourcePreview?.phase, "ready", JSON.stringify(app.getState().sourcePreview));
    assert.equal(app.getState().repo.checkoutId, "checkout-import", JSON.stringify(app.getState().repo));
    assert.match(root.markup, /data-preview-component-import/);
    root.shell.dispatch("click", { target: delegatedTarget("[data-preview-component-import]", {}) });
    await flushMicrotasks(24);
    assert.equal(previews, 1);
    assert.equal(confirmations, 0);
    assert.equal(app.getState().componentImport.preview.component_id, id);
    root.shell.dispatch("keydown", { key: "Escape", target: delegatedTarget("[data-cancel-component-import]", {}) });
    assert.equal(app.getState().componentImport, null);
    assert.equal(confirmations, 0);
    root.shell.dispatch("click", { target: delegatedTarget("[data-preview-component-import]", {}) });
    await flushMicrotasks(24);
    root.shell.dispatch("click", { target: delegatedTarget("[data-confirm-component-import]", {}) });
    await flushMicrotasks(24);
    assert.equal(confirmations, 1);
    assert.equal(app.getState().ui.activeSegment, "sot");
    assert.equal(app.getState().sot.snapshot.snapshot_id, "imported");
    assert.equal(loads, 2);
    app.destroy();
  }
});

async function mountConfiguredLocalAiFixture(options = {}) {
  const root = createMountRoot({
    realisticFocusLifecycle: options.realisticFocusLifecycle === true,
  });
  const controls = {
    providerConfigCalls: 0,
    saveCalls: [],
    deleteCalls: [],
    explainCalls: [],
    localEventHandler: null,
  };
  const items = [localAiItem("instance-ai-a"), localAiItem("instance-ai-b")];
  let currentSnapshotId = "snapshot-ai-mount-1";
  let currentStateRevision = 1;

  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async getAiProviderConfig() {
      controls.providerConfigCalls += 1;
      return options.initialAiProvider ?? configuredAiProvider();
    },
    async saveAiProviderConfig(request) {
      controls.saveCalls.push(structuredClone(request));
      if (typeof options.saveAiProviderConfig === "function") {
        return options.saveAiProviderConfig(request);
      }
      return configuredAiProvider("provider-ai-2", true);
    },
    async deleteAiProviderKey(providerRevision) {
      controls.deleteCalls.push(providerRevision);
      if (typeof options.deleteAiProviderKey === "function") {
        return options.deleteAiProviderKey(providerRevision);
      }
      return configuredAiProvider("provider-ai-3", false);
    },
    async explainLocalSource(request) {
      controls.explainCalls.push(structuredClone(request));
      if (typeof options.explainLocalSource === "function") {
        return options.explainLocalSource(request);
      }
      return {
        snapshot_id: request.snapshotId,
        instance_id: request.instanceId,
        source_revision: request.sourceRevision,
        provider_revision: request.providerRevision,
        doing: `doing ${request.instanceId}`,
        when_used: `when ${request.instanceId}`,
        capabilities: `capabilities ${request.instanceId}`,
        cautions: `cautions ${request.instanceId}`,
      };
    },
    onLocalScanChanged(handler) {
      controls.localEventHandler = handler;
      return () => {};
    },
    async getLocalScanState() {
      return {
        state_revision: currentStateRevision,
        current_attempt: null,
        latest_terminal_report: {
          attempt_id: `attempt-${currentSnapshotId}`,
          state: "complete",
          error_code: null,
        },
        latest_complete: {
          snapshot_id: currentSnapshotId,
          attempt_id: `attempt-${currentSnapshotId}`,
          status: "complete",
        },
        latest_partial: null,
      };
    },
    async queryLocalInstances(request) {
      return {
        snapshotId: request.snapshotId,
        items,
        kindCounts: [{ kind: "skill", count: items.length }],
        counts: { totalInstances: items.length, matchedInstances: items.length },
      };
    },
    async getLocalInstanceDetail({ instanceId }) {
      return items.find((item) => item.instanceId === instanceId);
    },
    async openLocalSourcePreview(request) {
      return {
        preview_session_id: `preview-${request.snapshotId}-${request.instanceId}`,
        view_generation: request.viewGeneration,
        snapshot_id: request.snapshotId,
        instance_id: request.instanceId,
        canonical_path: `/fixture-home/.codex/skills/${request.instanceId}/SKILL.md`,
        source_revision: `source-${request.snapshotId}-${request.instanceId}`,
        changed_since_snapshot: false,
        format: "text",
        total_bytes: 64,
        total_chunks: 1,
        chunk_bytes: 65536,
        selected_chunk_index: 0,
        issue: null,
      };
    },
    async readLocalSourcePreviewChunk(request) {
      return {
        preview_session_id: request.previewSessionId,
        source_revision: request.sourceRevision,
        chunk_index: request.chunkIndex,
        is_last: true,
        content: {
          format: "text",
          before: "source fixture",
          selected: null,
          after: "",
        },
      };
    },
    async closeLocalSourcePreview() {
      return { closed: true, cancelled: true };
    },
  });

  controls.replaceSnapshot = async (snapshotId) => {
    currentSnapshotId = snapshotId;
    currentStateRevision += 1;
    assert.equal(typeof controls.localEventHandler, "function");
    await controls.localEventHandler({
      attemptId: `attempt-${snapshotId}`,
      sequence: 1,
      stateRevision: currentStateRevision,
      payload: { state: "complete", snapshotId },
    });
    await flushMicrotasks(16);
  };

  await flushMicrotasks(8);
  root.dashboardItems
    .find((control) => control.dataset.dashboardSegment === "local")
    .dispatch("click");
  await flushMicrotasks(16);
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-instance]", { localInstance: "instance-ai-a" }),
  });
  await flushMicrotasks(16);

  return { app, controls, root };
}

function assertPendingAiControlFocus(root, selector, label) {
  const control = root.querySelector(selector);
  assert.ok(control, `${label} must remain rendered while pending`);
  assert.equal(control.disabled, false, `${label} must remain natively focusable while pending`);
  assert.equal(control.getAttribute("aria-disabled"), "true", `${label} must expose pending state`);
  assert.equal(root.ownerDocument.activeElement, control, `${label} must retain logical focus`);
}

test("support document selection edit reaches file-listed adoption apply", async () => {
  const root = createMountRoot();
  const id = "harnesskit.skill.fixture-support";
  const imported = JSON.parse(JSON.stringify(snapshot).replaceAll("harnesskit.skill.beta", id));
  imported.snapshot_id = "imported-edit";
  imported.components[1].kind = "skill";
  imported.components[1].targets = [{ target_id: "claude" }];
  let saved = 0; let applied = 0;
  const app = mountApp(root, {
    async getSotSessionState() { return { checkout_id: "checkout-import" }; },
    async loadSotSnapshot() { return imported; },
    async readImportedSkill(r) { assert.equal(r.componentId, id); return { content: "# Original canonical", managed: false, sourceLocator: ".claude/skills/fixture-support/SKILL.md", documents: [{ path: "references/guide.md", content: "Original guide", managed: false }] }; },
    async saveImportedSkill(r) { saved++; assert.equal(r.document, "references/guide.md"); assert.equal(r.content, "# Edited canonical"); assert.equal(r.sourcePath, undefined); return { ...imported, snapshot_id: "edited" }; },
    async previewInstall(r) { assert.equal(r.profileId, `component:${id}`); assert.equal(r.targetRoot, "import-source"); return { previewId: "adoption-review", fingerprint: "a".repeat(64), ...r, requiredApprovals: { overwrite: true, managementAdoption: true }, components: [id], artifacts: [{ componentId: id, target: "claude", destination: ".claude/settings.json" }] }; },
    async applyInstall(r) { applied++; assert.equal(r.approvals.adoptManagement, true); return { status: "complete", installEvidenceId: "verified", destinations: [{ target: "claude", destination: ".claude/settings.json", applyState: "changed", verifyState: "verified" }] }; },
  });
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: delegatedTarget("[data-component-id]", { componentId: id }) });
  root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
  await flushMicrotasks(24);
  assert.ok(app.getState().importedSkillEditor, JSON.stringify(app.getState()));
  assert.equal(app.getState().importedSkillEditor.content, "# Original canonical");
  root.shell.dispatch("change", { target: { value: "references/guide.md", closest(s) { return s === "[data-select-imported-document]" ? this : null; } } });
  assert.equal(app.getState().importedSkillEditor.content, "Original guide");
  root.shell.dispatch("input", { target: { value: "# Edited canonical", closest(s) { return s === "[data-imported-skill-content]" ? this : null; } } });
  root.shell.dispatch("click", { target: delegatedTarget("[data-save-imported-skill]", {}) });
  await flushMicrotasks(24);
  assert.equal(saved, 1);
  assert.equal(app.getState().sot.snapshot.snapshot_id, "edited");
  root.shell.dispatch("submit", { target: installForm({ profileId: `component:${id}`, scope: "user", targetId: "claude", targetRoot: "import-source" }) });
  await flushMicrotasks(24);
  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("change", { target: approvalTarget("overwrite", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  await flushMicrotasks(12); assert.equal(applied, 0);
  root.shell.dispatch("change", { target: approvalTarget("adoptManagement", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  await flushMicrotasks(24); assert.equal(applied, 1);
  assert.equal(app.getState().install.execution.status, "success");
  root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
  await flushMicrotasks(24);
  root.shell.dispatch("keydown", { key: "Escape", target: delegatedTarget("[data-cancel-imported-skill]", {}) });
  assert.equal(app.getState().importedSkillEditor, null, "Escape closes the canonical editor");
  app.destroy();
});

test("shared hook canonical edit reaches existing handle-bound adoption apply", async () => {
  const root = createMountRoot();
  const id = "harnesskit.hook.imported-stop";
  const imported = JSON.parse(JSON.stringify(snapshot).replaceAll("harnesskit.skill.beta", id));
  imported.snapshot_id = "imported-edit";
  imported.components[1].kind = "hook";
  imported.components[1].targets = [{ target_id: "claude" }];
  let saved = 0; let applied = 0;
  const app = mountApp(root, {
    async getSotSessionState() { return { checkout_id: "checkout-import" }; },
    async loadSotSnapshot() { return imported; },
    async readImportedSkill(r) { assert.equal(r.componentId, id); return { content: "# Original canonical", managed: false, sourceLocator: ".claude/settings.json" }; },
    async saveImportedSkill(r) { saved++; assert.equal(r.content, "# Edited canonical"); assert.equal(r.sourcePath, undefined); return { ...imported, snapshot_id: "edited" }; },
    async previewInstall(r) { assert.equal(r.profileId, `component:${id}`); assert.equal(r.targetRoot, "import-source"); return { previewId: "adoption-review", fingerprint: "a".repeat(64), ...r, requiredApprovals: { overwrite: true, managementAdoption: true }, components: [id], artifacts: [{ componentId: id, target: "claude", destination: ".claude/settings.json" }] }; },
    async applyInstall(r) { applied++; assert.equal(r.approvals.adoptManagement, true); return { status: "complete", installEvidenceId: "verified", destinations: [{ target: "claude", destination: ".claude/settings.json", applyState: "changed", verifyState: "verified" }] }; },
  });
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: delegatedTarget("[data-component-id]", { componentId: id }) });
  root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
  await flushMicrotasks(24);
  assert.ok(app.getState().importedSkillEditor, JSON.stringify(app.getState()));
  assert.equal(app.getState().importedSkillEditor.content, "# Original canonical");
  root.shell.dispatch("input", { target: { value: "# Edited canonical", closest(s) { return s === "[data-imported-skill-content]" ? this : null; } } });
  root.shell.dispatch("click", { target: delegatedTarget("[data-save-imported-skill]", {}) });
  await flushMicrotasks(24);
  assert.equal(saved, 1);
  assert.equal(app.getState().sot.snapshot.snapshot_id, "edited");
  root.shell.dispatch("submit", { target: installForm({ profileId: `component:${id}`, scope: "user", targetId: "claude", targetRoot: "import-source" }) });
  await flushMicrotasks(24);
  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("change", { target: approvalTarget("overwrite", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  await flushMicrotasks(12); assert.equal(applied, 0);
  root.shell.dispatch("change", { target: approvalTarget("adoptManagement", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  await flushMicrotasks(24); assert.equal(applied, 1);
  assert.equal(app.getState().install.execution.status, "success");
  root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
  await flushMicrotasks(24);
  root.shell.dispatch("keydown", { key: "Escape", target: delegatedTarget("[data-cancel-imported-skill]", {}) });
  assert.equal(app.getState().importedSkillEditor, null, "Escape closes the canonical editor");
  app.destroy();
});

test("Claude agent prompt edit reaches handle-bound adoption apply", async () => {
  const root = createMountRoot();
  const id = "harnesskit.agent.fixture-import";
  const imported = JSON.parse(JSON.stringify(snapshot).replaceAll("harnesskit.skill.beta", id));
  imported.snapshot_id = "imported-edit";
  imported.components[1].kind = "agent";
  imported.components[1].targets = [{ target_id: "claude" }];
  let saved = 0; let applied = 0;
  const app = mountApp(root, {
    async getSotSessionState() { return { checkout_id: "checkout-import" }; },
    async loadSotSnapshot() { return imported; },
    async readImportedSkill(r) { assert.equal(r.componentId, id); return { content: "# Original canonical", managed: false, sourceLocator: ".claude/agents/fixture-import.md" }; },
    async saveImportedSkill(r) { saved++; assert.equal(r.content, "# Edited canonical"); assert.equal(r.sourcePath, undefined); return { ...imported, snapshot_id: "edited" }; },
    async previewInstall(r) { assert.equal(r.profileId, `component:${id}`); assert.equal(r.targetRoot, "import-source"); return { previewId: "adoption-review", fingerprint: "a".repeat(64), ...r, requiredApprovals: { overwrite: true, managementAdoption: true }, components: [id], artifacts: [{ componentId: id, target: "claude", destination: ".claude/agents/fixture-import.md" }] }; },
    async applyInstall(r) { applied++; assert.equal(r.approvals.adoptManagement, true); return { status: "complete", installEvidenceId: "verified", destinations: [{ target: "claude", destination: ".claude/agents/fixture-import.md", applyState: "changed", verifyState: "verified" }] }; },
  });
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: delegatedTarget("[data-component-id]", { componentId: id }) });
  root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
  await flushMicrotasks(24);
  assert.ok(app.getState().importedSkillEditor, JSON.stringify(app.getState()));
  assert.equal(app.getState().importedSkillEditor.content, "# Original canonical");
  root.shell.dispatch("input", { target: { value: "# Edited canonical", closest(s) { return s === "[data-imported-skill-content]" ? this : null; } } });
  root.shell.dispatch("click", { target: delegatedTarget("[data-save-imported-skill]", {}) });
  await flushMicrotasks(24);
  assert.equal(saved, 1);
  assert.equal(app.getState().sot.snapshot.snapshot_id, "edited");
  root.shell.dispatch("submit", { target: installForm({ profileId: `component:${id}`, scope: "user", targetId: "claude", targetRoot: "import-source" }) });
  await flushMicrotasks(24);
  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("change", { target: approvalTarget("overwrite", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  await flushMicrotasks(12); assert.equal(applied, 0);
  root.shell.dispatch("change", { target: approvalTarget("adoptManagement", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  await flushMicrotasks(24); assert.equal(applied, 1);
  assert.equal(app.getState().install.execution.status, "success");
  root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
  await flushMicrotasks(24);
  root.shell.dispatch("keydown", { key: "Escape", target: delegatedTarget("[data-cancel-imported-skill]", {}) });
  assert.equal(app.getState().importedSkillEditor, null, "Escape closes the canonical editor");
  app.destroy();
});

test("project rule body edit reaches handle-bound adoption apply", async () => {
  for (const tool of ["codex", "antigravity-cli"]) {
    const root = createMountRoot();
    const id = `harnesskit.rule.imported-agents-${tool}`;
    const imported = JSON.parse(JSON.stringify(snapshot).replaceAll("harnesskit.skill.beta", id));
    imported.snapshot_id = "imported-edit";
    imported.components[1].kind = "rule";
    imported.components[1].targets = [{ target_id: "project" }];
    let saved = 0; let applied = 0;
    const app = mountApp(root, {
      async getSotSessionState() { return { checkout_id: "checkout-import" }; },
      async loadSotSnapshot() { return imported; },
      async readImportedSkill(r) { assert.equal(r.componentId, id); return { content: "# Original canonical", managed: false, sourceLocator: "AGENTS.md" }; },
      async saveImportedSkill(r) { saved++; assert.equal(r.content, "# Edited canonical"); assert.equal(r.sourcePath, undefined); return { ...imported, snapshot_id: "edited" }; },
      async previewInstall(r) { assert.equal(r.profileId, `component:${id}`); assert.equal(r.targetRoot, "import-source"); return { previewId: "adoption-review", fingerprint: "a".repeat(64), ...r, requiredApprovals: { overwrite: true, managementAdoption: true }, components: [id], artifacts: [{ componentId: id, target: "project", destination: "AGENTS.md" }] }; },
      async applyInstall(r) { applied++; assert.equal(r.approvals.adoptManagement, true); return { status: "complete", installEvidenceId: "verified", destinations: [{ target: "project", destination: "AGENTS.md", applyState: "changed", verifyState: "verified" }] }; },
    });
    await flushMicrotasks(24);
    root.shell.dispatch("click", { target: delegatedTarget("[data-component-id]", { componentId: id }) });
    root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
    await flushMicrotasks(24);
    assert.ok(app.getState().importedSkillEditor, JSON.stringify(app.getState()));
    assert.equal(app.getState().importedSkillEditor.content, "# Original canonical");
    root.shell.dispatch("input", { target: { value: "# Edited canonical", closest(s) { return s === "[data-imported-skill-content]" ? this : null; } } });
    root.shell.dispatch("click", { target: delegatedTarget("[data-save-imported-skill]", {}) });
    await flushMicrotasks(24);
    assert.equal(saved, 1);
    assert.equal(app.getState().sot.snapshot.snapshot_id, "edited");
    root.shell.dispatch("submit", { target: installForm({ profileId: `component:${id}`, scope: "project", targetId: "project", targetRoot: "import-source" }) });
    await flushMicrotasks(24);
    root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
    root.shell.dispatch("change", { target: approvalTarget("overwrite", true) });
    root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
    await flushMicrotasks(12); assert.equal(applied, 0);
    root.shell.dispatch("change", { target: approvalTarget("adoptManagement", true) });
    root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
    await flushMicrotasks(24); assert.equal(applied, 1);
    assert.equal(app.getState().install.execution.status, "success");
    root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
    await flushMicrotasks(24);
    root.shell.dispatch("keydown", { key: "Escape", target: delegatedTarget("[data-cancel-imported-skill]", {}) });
    assert.equal(app.getState().importedSkillEditor, null, "Escape closes the canonical editor");
    app.destroy();
  }
});

test("imported unprofiled canonical edit reaches preview and explicit adoption apply", async () => {
  const root = createMountRoot();
  const id = "harnesskit.skill.fixture-import";
  const imported = JSON.parse(JSON.stringify(snapshot).replaceAll("harnesskit.skill.beta", id));
  imported.snapshot_id = "imported-edit";
  imported.components[1].targets = [{ target_id: "codex" }];
  let saved = 0; let applied = 0;
  const app = mountApp(root, {
    async getSotSessionState() { return { checkout_id: "checkout-import" }; },
    async loadSotSnapshot() { return imported; },
    async readImportedSkill(r) { assert.equal(r.componentId, id); return { content: "# Original canonical", managed: false, sourceLocator: ".codex/skills/fixture-import/SKILL.md" }; },
    async saveImportedSkill(r) { saved++; assert.equal(r.content, "# Edited canonical"); assert.equal(r.sourcePath, undefined); return { ...imported, snapshot_id: "edited" }; },
    async previewInstall(r) { assert.equal(r.profileId, `component:${id}`); assert.equal(r.targetRoot, "import-source"); return { previewId: "adoption-review", fingerprint: "a".repeat(64), ...r, requiredApprovals: { overwrite: true, managementAdoption: true }, components: [id], artifacts: [{ componentId: id, target: "codex", destination: ".codex/skills/fixture-import/SKILL.md" }] }; },
    async applyInstall(r) { applied++; assert.equal(r.approvals.adoptManagement, true); return { status: "complete", installEvidenceId: "verified", destinations: [{ target: "codex", destination: ".codex/skills/fixture-import/SKILL.md", applyState: "changed", verifyState: "verified" }] }; },
  });
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: delegatedTarget("[data-component-id]", { componentId: id }) });
  root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
  await flushMicrotasks(24);
  assert.ok(app.getState().importedSkillEditor, JSON.stringify(app.getState()));
  assert.equal(app.getState().importedSkillEditor.content, "# Original canonical");
  root.shell.dispatch("input", { target: { value: "# Edited canonical", closest(s) { return s === "[data-imported-skill-content]" ? this : null; } } });
  root.shell.dispatch("click", { target: delegatedTarget("[data-save-imported-skill]", {}) });
  await flushMicrotasks(24);
  assert.equal(saved, 1);
  assert.equal(app.getState().sot.snapshot.snapshot_id, "edited");
  root.shell.dispatch("submit", { target: installForm({ profileId: `component:${id}`, scope: "user", targetId: "codex", targetRoot: "import-source" }) });
  await flushMicrotasks(24);
  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("change", { target: approvalTarget("overwrite", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  await flushMicrotasks(12); assert.equal(applied, 0);
  root.shell.dispatch("change", { target: approvalTarget("adoptManagement", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  await flushMicrotasks(24); assert.equal(applied, 1);
  assert.equal(app.getState().install.execution.status, "success");
  root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
  await flushMicrotasks(24);
  root.shell.dispatch("keydown", { key: "Escape", target: delegatedTarget("[data-cancel-imported-skill]", {}) });
  assert.equal(app.getState().importedSkillEditor, null, "Escape closes the canonical editor");
  app.destroy();
});

test("imported canonical validation error can be corrected and saved without reopening", async () => {
  const root = createMountRoot();
  const id = "harnesskit.skill.fixture-import";
  const imported = JSON.parse(JSON.stringify(snapshot).replaceAll("harnesskit.skill.beta", id));
  let attempts = 0;
  const app = mountApp(root, {
    async getSotSessionState() { return { checkout_id: "checkout-import" }; },
    async loadSotSnapshot() { return imported; },
    async readImportedSkill() { return { content: "# Original", managed: false }; },
    async saveImportedSkill(request) {
      attempts++;
      if (attempts === 1) throw { code: "import_frontmatter_required" };
      assert.equal(request.content, "# Corrected");
      return { ...imported, snapshot_id: "corrected" };
    },
  });
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: delegatedTarget("[data-component-id]", { componentId: id }) });
  root.shell.dispatch("click", { target: delegatedTarget("[data-edit-imported-skill]", {}) });
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: delegatedTarget("[data-save-imported-skill]", {}) });
  await flushMicrotasks(24);
  assert.match(app.getState().importedSkillEditor.message, /import_frontmatter_required/);
  root.shell.dispatch("input", { target: { value: "# Corrected", closest(s) { return s === "[data-imported-skill-content]" ? this : null; } } });
  root.shell.dispatch("click", { target: delegatedTarget("[data-save-imported-skill]", {}) });
  await flushMicrotasks(24);
  assert.equal(attempts, 2, "corrected content must be retryable");
  assert.equal(app.getState().sot.snapshot.snapshot_id, "corrected");
  assert.equal(app.getState().importedSkillEditor, null);
  app.destroy();
});

test("managed external review keeps without apply and replacement approval reaches backend", async () => {
  const root = createMountRoot();
  const id = "harnesskit.skill.fixture-import";
  const imported = JSON.parse(JSON.stringify(snapshot).replaceAll("harnesskit.skill.beta", id));
  imported.components[1].targets = [{ target_id: "codex" }];
  let applied = 0; let reviews = 0;
  const app = mountApp(root, {
    async getSotSessionState() { return { checkout_id: "checkout-import" }; },
    async loadSotSnapshot() { return imported; },
    async previewInstall(r) { reviews++; return { ...r, previewId: `review-${reviews}`, fingerprint: String(reviews).repeat(64), requiredApprovals: { overwrite: true, managedReplacement: true }, warnings: ["외부 수정\n-<script>external()</script>\n+canonical"], components: [id], artifacts: [] }; },
    async applyInstall(r) { applied++; assert.equal(r.previewId, "review-2"); assert.equal(r.approvals.replaceManaged, true); assert.equal(r.approvals.adoptManagement, undefined); assert.equal(r.approvals.allowRuntimeHooks, false); return { status: "complete", installEvidenceId: "verified", destinations: [{ target: "codex", destination: ".codex/skills/fixture-import/SKILL.md", applyState: "changed", verifyState: "verified" }] }; },
  });
  await flushMicrotasks(24);
  root.shell.dispatch("click", { target: delegatedTarget("[data-component-id]", { componentId: id }) });
  const form = { profileId: `component:${id}`, scope: "user", targetId: "codex", targetRoot: "import-source" };
  root.shell.dispatch("submit", { target: installForm(form) });
  await flushMicrotasks(24);
  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("change", { target: approvalTarget("overwrite", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  await flushMicrotasks(12); assert.equal(applied, 0);
  root.shell.dispatch("click", { target: delegatedTarget("[data-keep-managed-source]", {}) });
  assert.equal(app.getState().install.preview, null);
  assert.equal(applied, 0);
  root.shell.dispatch("submit", { target: installForm(form) });
  await flushMicrotasks(24);
  root.shell.dispatch("change", { target: approvalTarget("replaceManaged", true) });
  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("change", { target: approvalTarget("overwrite", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  await flushMicrotasks(24);
  assert.equal(applied, 1);
  assert.equal(app.getState().install.execution.status, "success");
  app.destroy();
});

function installForm(values) {
  return {
    id: "install-form",
    elements: {
      namedItem(name) {
        return { value: values[name] ?? "" };
      },
    },
  };
}

function installFieldTarget(form, name) {
  return {
    form,
    dataset: { installField: name },
    closest(selector) {
      return selector === "[data-install-field]" ? this : null;
    },
  };
}

function approvalTarget(name, checked) {
  return {
    checked,
    dataset: { installApproval: name },
    closest(selector) {
      return selector === "[data-install-approval]" ? this : null;
    },
  };
}

async function mountReadyApp(snapshotFixture = snapshot, rootOptions = {}) {
  const root = createMountRoot(rootOptions);
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-interactions", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshotFixture);
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  assert.equal(app.getState().sot.phase, "ready");
  return { app, root };
}

test("SoT authoring dialog traps focus, closes with Escape, and restores the trigger", async () => {
  const { app, root } = await mountReadyApp(
    snapshotWithAuthoringFlow(),
    { realisticFocusLifecycle: true },
  );
  const trigger = root.authoringOpenControl;
  assert.ok(trigger);

  root.shell.dispatch("click", { target: trigger });

  assert.equal(app.getState().ui.authoringFlowOpen, true);
  assert.match(root.markup, /data-authoring-flow-dialog/);
  const close = root.authoringModalControls[0];
  assert.ok(close);
  assert.equal(root.ownerDocument.activeElement, close);

  root.ownerDocument.activeElement = root.ownerDocument.body;
  const tabEvent = root.shell.dispatch("keydown", {
    target: root.shell,
    key: "Tab",
  });
  assert.equal(tabEvent.defaultPrevented, true);
  assert.equal(root.ownerDocument.activeElement, close);

  const escapeEvent = root.shell.dispatch("keydown", {
    target: root.shell,
    key: "Escape",
  });
  assert.equal(escapeEvent.defaultPrevented, true);
  assert.equal(app.getState().ui.authoringFlowOpen, false);
  assert.doesNotMatch(root.markup, /data-authoring-flow-dialog/);
  assert.equal(root.ownerDocument.activeElement, root.authoringOpenControl);
  app.destroy();
});

test("dashboard-row authoring action opens from Local without replacing Local state", async () => {
  const { app, root } = await mountReadyApp(
    snapshotWithAuthoringFlow(),
    { realisticFocusLifecycle: true },
  );
  root.dashboardItems
    .find((control) => control.dataset.dashboardSegment === "local")
    .dispatch("click");

  const localData = app.getState().localData;
  const localView = app.getState().localView;
  const trigger = root.authoringOpenControl;
  assert.ok(trigger);
  assert.equal(trigger.disabled, false);

  root.shell.dispatch("click", { target: trigger });

  assert.equal(app.getState().ui.activeSegment, "local");
  assert.equal(app.getState().ui.authoringFlowOpen, true);
  assert.equal(app.getState().localData, localData);
  assert.equal(app.getState().localView, localView);
  assert.match(root.markup, /data-authoring-flow-dialog/);

  root.shell.dispatch("click", { target: root.authoringModalControls[0] });
  assert.equal(app.getState().ui.activeSegment, "local");
  assert.equal(app.getState().ui.authoringFlowOpen, false);
  app.destroy();
});

test("saved checkout restore failure remains a visible unavailable boundary", async () => {
  const root = createMountRoot();
  const app = mountApp(root, {
    async getSotSessionState() {
      throw new Error("private backend detail");
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(app.getState().repo.phase, "error");
  assert.match(app.getState().repo.message, /checkout 상태/);
  assert.doesNotMatch(app.getState().repo.message, /private backend detail/);
});

test("workspace layout change patches the preferred-width semantic without a full render", () => {
  const root = createMountRoot();
  const app = mountApp(root, {}, { deferDomainRestore: true });
  const renderCount = root.renderCount;

  app.applyWorkspaceLayoutChanged({
    preferredLeftWidthPx: 500,
    preferredRightWidthPx: 550,
    revision: 9,
    persisted: true,
    diagnostic: null,
  });

  assert.equal(root.renderCount, renderCount);
  assert.equal(
    root.preferredWidths.textContent,
    "선호 패널 너비 좌측 500픽셀, 우측 550픽셀",
  );
  assert.equal(
    root.preferredWidths.getAttribute("aria-label"),
    "선호 패널 너비 좌측 500픽셀, 우측 550픽셀",
  );
});

test("independent disclosure state survives shell rerender and the SoT to Local round trip", async () => {
  const { app, root } = await mountReadyApp();
  assert.deepEqual(app.getState().ui.sotDisclosures, {
    componentMap: true,
    profileMatrix: false,
  });

  root.dispatch("click", { target: root.graphToggle });
  assert.deepEqual(app.getState().ui.sotDisclosures, {
    componentMap: false,
    profileMatrix: false,
  });
  assert.equal(root.graphToggle.getAttribute("aria-expanded"), "false");

  root.dispatch("click", { target: root.matrixToggle });
  assert.deepEqual(app.getState().ui.sotDisclosures, {
    componentMap: false,
    profileMatrix: true,
  });

  const renderCountBeforePatchOnlyUpdates = root.renderCount;
  app.applyAppearanceChanged({
    logical_mode: "Light",
    resolved_mode: "Light",
    revision: 1,
    persisted: true,
  });
  app.applyWorkspaceLayoutChanged({
    preferredLeftWidthPx: 480,
    preferredRightWidthPx: 520,
    revision: 1,
    persisted: true,
    diagnostic: null,
  });
  assert.deepEqual(app.getState().ui.sotDisclosures, {
    componentMap: false,
    profileMatrix: true,
  });
  assert.equal(root.renderCount, renderCountBeforePatchOnlyUpdates);

  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "local").dispatch("click");
  assert.equal(app.getState().ui.activeSegment, "local");
  assert.deepEqual(app.getState().ui.sotDisclosures, {
    componentMap: false,
    profileMatrix: true,
  });
  assert.ok(root.renderCount > renderCountBeforePatchOnlyUpdates);

  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "sot").dispatch("click");
  assert.equal(app.getState().ui.activeSegment, "sot");
  assert.deepEqual(app.getState().ui.sotDisclosures, {
    componentMap: false,
    profileMatrix: true,
  });
  assert.equal(root.graphToggle.getAttribute("aria-expanded"), "false");
  assert.equal(root.matrixToggle.getAttribute("aria-expanded"), "true");
});

test("invalid disclosure DOM renders a safe center notice without discarding the ready snapshot", async () => {
  for (const [disclosureFault, safeCode] of [
    ["missing-map-body", "invalid_map_body"],
    ["duplicate-matrix-toggle", "invalid_matrix_toggle"],
  ]) {
    const { app, root } = await mountReadyApp(snapshot, { disclosureFault });

    assert.equal(app.getState().sot.phase, "ready");
    assert.equal(app.getState().sot.snapshot.snapshot_id, snapshot.snapshot_id);
    assert.equal(root.sotWorkbench.getAttribute("data-disclosure-degraded"), safeCode);
    assert.match(root.sotWorkbench.innerHTML, /Component Map과 Profile Matrix를 표시하지 못했습니다/);
    assert.doesNotMatch(root.sotWorkbench.innerHTML, /stack|selector|private/i);
  }
});

test("graph session failure remains local and preserves the ready SoT snapshot", async () => {
  const root = createMountRoot();
  let graphSessionCreations = 0;
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-interactions", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  }, {
    createComponentMapSession() {
      graphSessionCreations += 1;
      return {
        setExpanded() {},
        attach() { throw new Error("graph attach failed"); },
        syncPresentation() {},
        detach() {},
        dispose() {},
      };
    },
  });

  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(graphSessionCreations, 1);
  assert.equal(app.getState().sot.phase, "ready");
  assert.equal(app.getState().sot.snapshot.snapshot_id, snapshot.snapshot_id);
  assert.doesNotMatch(app.getState().sot.message, /불러오지 못했습니다/);
  assert.equal(
    root.sotWorkbench.getAttribute("data-disclosure-degraded"),
    "component_map_session_failed",
  );
  assert.match(root.sotWorkbench.innerHTML, /Component Map과 Profile Matrix를 표시하지 못했습니다/);
  assert.match(root.sotWorkbench.innerHTML, /Component Map diagnostic: graph attach failed/);
});

test("resolved appearance reaches one graph session before attach and updates without reload", async () => {
  const root = createMountRoot();
  const events = [];
  let graphSessionCreations = 0;
  let snapshotLoads = 0;
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-interactions", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      snapshotLoads += 1;
      return structuredClone(snapshot);
    },
  }, {
    appearance: {
      logical_mode: "Light",
      resolved_mode: "Light",
      revision: 4,
      persisted: true,
    },
    createComponentMapSession() {
      graphSessionCreations += 1;
      return {
        setResolvedAppearance(mode) { events.push(`appearance:${mode}`); },
        setExpanded() { events.push("expanded"); },
        attach() { events.push("attach"); return true; },
        syncPresentation() { events.push("presentation"); },
        detach() {},
        dispose() {},
      };
    },
  });

  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(graphSessionCreations, 1);
  assert.equal(snapshotLoads, 1);
  assert.ok(events.indexOf("appearance:light") < events.indexOf("attach"));

  const renderCountBeforeAppearanceUpdate = root.renderCount;
  app.applyAppearanceChanged({
    logical_mode: "Dark",
    resolved_mode: "Dark",
    revision: 5,
    persisted: true,
  });

  assert.deepEqual(events.filter((event) => event.startsWith("appearance:")), [
    "appearance:light",
    "appearance:dark",
  ]);
  assert.equal(graphSessionCreations, 1);
  assert.equal(snapshotLoads, 1);
  assert.equal(root.renderCount, renderCountBeforeAppearanceUpdate);
  app.destroy();
});

test("graph presentation lifecycle reaches app state without replacing snapshot or selection", async () => {
  const root = createMountRoot();
  const presentations = [];
  let onGraphAction = null;
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-interactions", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  }, {
    createComponentMapSession(options) {
      onGraphAction = options.onAction;
      return {
        setExpanded() {},
        attach() { return true; },
        syncPresentation(view) { presentations.push(structuredClone(view)); },
        detach() {},
        dispose() {},
      };
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  onGraphAction({ type: "select_graph_node", componentId: "harnesskit.agent.router" });
  const snapshotBeforeTransition = app.getState().sot.snapshot;
  onGraphAction({ type: "show_graph_semantic" });
  assert.deepEqual(app.getState().sotView.graphPresentation, {
    viewMode: "semantic",
    rendererLifecycle: "absent",
    failure: null,
    focusNodeId: "component:harnesskit.agent.router",
  });
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.agent.router");
  assert.equal(app.getState().sot.snapshot, snapshotBeforeTransition);

  onGraphAction({ type: "return_graph_three_d" });
  assert.equal(app.getState().sotView.graphPresentation.rendererLifecycle, "creating");
  onGraphAction({ type: "graph_renderer_failed", failure: "webglcontextlost" });
  assert.deepEqual(app.getState().sotView.graphPresentation, {
    viewMode: "semantic",
    rendererLifecycle: "failed",
    failure: "webglcontextlost",
    focusNodeId: "component:harnesskit.agent.router",
  });
  onGraphAction({ type: "graph_renderer_ready" });
  assert.deepEqual(app.getState().sotView.graphPresentation, {
    viewMode: "three_d",
    rendererLifecycle: "ready",
    failure: null,
    focusNodeId: "component:harnesskit.agent.router",
  });
  assert.equal(presentations.at(-1).selectedComponentId, "harnesskit.agent.router");
  assert.equal(app.getState().sot.snapshot, snapshotBeforeTransition);
  app.destroy();
});

test("graph host resolver rejects unchanged selection without mutating host state or presentation", async () => {
  const root = createMountRoot();
  const presentations = [];
  const actionCalls = [];
  let onGraphAction = null;
  let resolveSelection = null;
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-interactions", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  }, {
    createComponentMapSession(options) {
      onGraphAction = (action) => {
        actionCalls.push(action);
        options.onAction(action);
      };
      resolveSelection = options.resolveSelection;
      return {
        setExpanded() {},
        attach() { return true; },
        syncPresentation(view) { presentations.push(structuredClone(view)); },
        detach() {},
        dispose() {},
      };
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  onGraphAction({ type: "select_graph_node", componentId: "harnesskit.agent.router" });
  const stateBeforeRejection = app.getState();
  const presentationCountBeforeRejection = presentations.length;
  const actionCountBeforeRejection = actionCalls.length;

  const resolution = resolveSelection({
    action: { type: "select_graph_node", componentId: "harnesskit.agent.router" },
    intent: { nodeId: "component:harnesskit.agent.router", source: "pointer" },
  });

  assert.equal(resolution, false);
  assert.equal(app.getState(), stateBeforeRejection);
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.agent.router");
  assert.equal(presentations.length, presentationCountBeforeRejection);
  assert.equal(actionCalls.length, actionCountBeforeRejection);
  app.destroy();
});

test("accepted graph selection resolves before one host presentation sync", async () => {
  const root = createMountRoot();
  const events = [];
  const inspector = { innerHTML: "" };
  const querySelector = root.querySelector.bind(root);
  root.querySelector = (selector) => (
    selector === "[data-sot-inspector]" ? inspector : querySelector(selector)
  );
  let onGraphAction = null;
  let resolveSelection = null;
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-interactions", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  }, {
    createComponentMapSession(options) {
      onGraphAction = options.onAction;
      resolveSelection = options.resolveSelection;
      return {
        setExpanded() {},
        attach() { return true; },
        syncPresentation(view) {
          const selectedTitleIsCommitted = inspector.innerHTML.includes("Router");
          events.push(`sync:${view.selectedComponentId ?? "none"}:${selectedTitleIsCommitted}`);
        },
        detach() {},
        dispose() {},
      };
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  const eventsBeforeSelection = events.length;
  const action = { type: "select_graph_node", componentId: "harnesskit.agent.router" };
  const resolution = resolveSelection({
    action,
    intent: { nodeId: "component:harnesskit.agent.router", source: "pointer" },
  });
  events.push("resolution-returned");

  assert.deepEqual(resolution, {
    layout: "preserve",
    camera: { kind: "contextual", durationMs: 240 },
  });
  assert.equal(app.getState().sotView.selectedComponentId, null);
  assert.equal(events.length, eventsBeforeSelection + 1);

  events.push("commit");
  onGraphAction(action);

  assert.deepEqual(events.slice(eventsBeforeSelection), [
    "resolution-returned",
    "commit",
    "sync:harnesskit.agent.router:true",
  ]);
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.agent.router");
  assert.deepEqual(resolveSelection({
    action: { type: "clear_graph_selection" },
    intent: { nodeId: null, source: "background" },
  }), {
    layout: "preserve",
    camera: { kind: "restore", durationMs: 240 },
  });
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.agent.router");
  app.destroy();
});

test("shell Escape restores a restorable graph camera before clearing graph-owned selection", async () => {
  const root = createMountRoot();
  const events = [];
  const presentedSelections = [];
  let onGraphAction = null;
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-interactions", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  }, {
    createComponentMapSession(options) {
      onGraphAction = options.onAction;
      return {
        setExpanded() {},
        attach() { return true; },
        syncPresentation(view) {
          presentedSelections.push(view.selectedComponentId ?? null);
        },
        detach() {},
        dispose() {},
        restoreCamera() {
          events.push("restore-camera");
          return true;
        },
      };
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  onGraphAction({ type: "select_graph_node", componentId: "harnesskit.agent.router" });
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.agent.router");
  const presentationCountBeforeEscape = presentedSelections.length;

  root.shell.dispatch("keydown", {
    target: root.shell,
    key: "Escape",
    preventDefault() { events.push("prevent-default"); },
  });

  assert.deepEqual(events.slice(0, 2), ["restore-camera", "prevent-default"]);
  assert.equal(events.filter((event) => event === "restore-camera").length, 1);
  assert.deepEqual(presentedSelections.slice(presentationCountBeforeEscape), [null]);
  assert.equal(app.getState().sotView.selectedComponentId, null);
  app.destroy();
});

test("Matrix toggle changes only its key and preserves selection and snapshot state", async () => {
  const { app, root } = await mountReadyApp();
  const beta = root.semanticGraphItems.find(
    (item) => item.dataset.semanticNodeId === "component:harnesskit.skill.beta",
  );
  assert.ok(beta, "WebGL-unavailable fallback must expose the Beta component");
  assert.equal(root.semanticHost.hidden, false);
  assert.equal(root.graphScale.textContent, "3D · 목록");
  root.semanticHost.dispatch("click", { target: beta });
  const selectedBefore = app.getState().sotView;
  const snapshotBefore = app.getState().sot.snapshot;
  const semanticHostBefore = root.semanticHost;
  const renderCountBefore = root.renderCount;

  root.dispatch("click", { target: root.matrixToggle });

  assert.deepEqual(app.getState().ui.sotDisclosures, {
    componentMap: true,
    profileMatrix: true,
  });
  assert.equal(app.getState().sotView, selectedBefore);
  assert.equal(app.getState().sot.snapshot, snapshotBefore);
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.skill.beta");
  assert.equal(root.semanticHost, semanticHostBefore);
  assert.equal(root.renderCount, renderCountBefore);
});

test("selection and same-process snapshot refresh preserve both disclosure keys", async () => {
  let loadCount = 0;
  const root = createMountRoot();
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-graph-session", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      loadCount += 1;
      const nextSnapshot = structuredClone(snapshot);
      nextSnapshot.snapshot_id = `snapshot-graph-session-${loadCount}`;
      return nextSnapshot;
    },
  });
  await flushMicrotasks();
  assert.equal(app.getState().sot.phase, "ready");

  const beta = root.semanticGraphItems.find(
    (item) => item.dataset.semanticNodeId === "component:harnesskit.skill.beta",
  );
  assert.ok(beta, "fallback must expose the component before the graph is collapsed");
  root.semanticHost.dispatch("click", { target: beta });
  root.dispatch("click", { target: root.graphToggle });
  root.dispatch("click", { target: root.matrixToggle });
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.skill.beta");
  assert.deepEqual(app.getState().ui.sotDisclosures, {
    componentMap: false,
    profileMatrix: true,
  });

  root.querySelector("#dashboard-refresh").dispatch("click");
  await flushMicrotasks();
  assert.equal(loadCount, 2);
  assert.equal(app.getState().sot.snapshot.snapshot_id, "snapshot-graph-session-2");
  assert.deepEqual(app.getState().ui.sotDisclosures, {
    componentMap: false,
    profileMatrix: true,
  });
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.skill.beta");
  assert.equal(root.graphToggle.getAttribute("aria-expanded"), "false");
  assert.equal(root.matrixToggle.getAttribute("aria-expanded"), "true");
});

test("dashboard SoT refresh starts one checkout load and does not start a Local scan", async () => {
  const pending = deferred();
  let loadCount = 0;
  let localScanCount = 0;
  const root = createMountRoot();
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-refresh", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot(checkoutId) {
      assert.equal(checkoutId, "checkout-refresh");
      loadCount += 1;
      return loadCount === 1 ? structuredClone(snapshot) : pending.promise;
    },
    async startLocalScan() {
      localScanCount += 1;
      throw new Error("SoT refresh must not request a Local scan");
    },
  });
  await flushMicrotasks();
  assert.equal(app.getState().sot.phase, "ready");

  root.querySelector("#dashboard-refresh").dispatch("click");
  assert.equal(app.getState().sot.phase, "loading");
  root.querySelector("#dashboard-refresh").dispatch("click");
  assert.equal(loadCount, 2);
  assert.equal(localScanCount, 0);

  const refreshed = structuredClone(snapshot);
  refreshed.snapshot_id = "snapshot-refreshed";
  pending.resolve(refreshed);
  await flushMicrotasks();
  assert.equal(app.getState().sot.phase, "ready");
  assert.equal(app.getState().sot.snapshot.snapshot_id, "snapshot-refreshed");
  assert.equal(app.getState().ui.activeSegment, "sot");
  assert.equal(localScanCount, 0);
  app.destroy();
});

test("dashboard SoT refresh requires a checkout and retains the error state on load failure", async () => {
  const noCheckoutRoot = createMountRoot();
  let unregisteredLoadCount = 0;
  const noCheckoutApp = mountApp(noCheckoutRoot, {
    async getSotSessionState() { return null; },
    async loadSotSnapshot() { unregisteredLoadCount += 1; },
  });
  await flushMicrotasks();
  noCheckoutRoot.querySelector("#dashboard-refresh").dispatch("click");
  assert.equal(unregisteredLoadCount, 0);
  assert.equal(noCheckoutApp.getState().repo.checkoutId, null);
  noCheckoutApp.destroy();

  const root = createMountRoot();
  let loadCount = 0;
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-error", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      loadCount += 1;
      if (loadCount === 1) return structuredClone(snapshot);
      throw new Error("private backend details");
    },
  });
  await flushMicrotasks();
  root.querySelector("#dashboard-refresh").dispatch("click");
  await flushMicrotasks();
  assert.equal(loadCount, 2);
  assert.equal(app.getState().repo.checkoutId, "checkout-error");
  assert.equal(app.getState().sot.phase, "error");
  assert.match(app.getState().sot.message, /SoT snapshot을 불러오지 못했습니다/);
  assert.doesNotMatch(app.getState().sot.message, /private backend details/);
  app.destroy();
});

test("fresh app instances reset both disclosure keys", () => {
  const first = mountApp(createMountRoot(), {}, { deferDomainRestore: true });
  const second = mountApp(createMountRoot(), {}, { deferDomainRestore: true });

  assert.deepEqual(first.getState().ui.sotDisclosures, {
    componentMap: true,
    profileMatrix: false,
  });
  assert.deepEqual(second.getState().ui.sotDisclosures, {
    componentMap: true,
    profileMatrix: false,
  });
});

test("empty checkout submit selects a directory and immediately reuses registration", async () => {
  const calls = [];
  const root = createMountRoot();
  const app = mountApp(root, {
    async pickCheckoutDirectory() {
      calls.push("pick");
      return { outcome: "selected", path: "/fixture-home/Projects/harnesskit " };
    },
    async registerCheckout(path) {
      calls.push(`register:${path}`);
      return { checkout_id: "checkout-picked", canonical_path: path, repo_status: {} };
    },
    async loadSotSnapshot() {
      calls.push("load");
      return structuredClone(snapshot);
    },
  });

  root.checkoutInput.value = "";
  await root.repoForm.dispatch("submit", { preventDefault() {} });

  assert.deepEqual(calls, [
    "pick",
    "register:/fixture-home/Projects/harnesskit ",
    "load",
  ]);
  assert.equal(app.getState().repo.checkoutPath, "/fixture-home/Projects/harnesskit ");
  assert.equal(app.getState().repo.checkoutId, "checkout-picked");
  assert.equal(app.getState().sot.phase, "ready");
});

test("picker cancellation preserves all state and restores checkout input focus", async () => {
  let registrationCalls = 0;
  const root = createMountRoot();
  const app = mountApp(root, {
    async pickCheckoutDirectory() {
      return { outcome: "cancelled" };
    },
    async registerCheckout() {
      registrationCalls += 1;
      throw new Error("must not register after cancellation");
    },
  });
  const before = app.getState();

  root.checkoutInput.value = "";
  await root.repoForm.dispatch("submit", { preventDefault() {} });

  assert.equal(app.getState(), before);
  assert.equal(registrationCalls, 0);
  assert.equal(root.ownerDocument.activeElement, root.checkoutInput);
});

test("a pending native picker is single-flight without mutating repo state", async () => {
  let resolvePicker;
  let pickerCalls = 0;
  const root = createMountRoot();
  const app = mountApp(root, {
    pickCheckoutDirectory() {
      pickerCalls += 1;
      return new Promise((resolve) => {
        resolvePicker = resolve;
      });
    },
  });
  const before = app.getState();
  root.checkoutInput.value = "";

  const first = root.repoForm.dispatch("submit", { preventDefault() {} });
  const second = root.repoForm.dispatch("submit", { preventDefault() {} });
  await Promise.resolve();

  assert.equal(pickerCalls, 1);
  assert.equal(app.getState().repo.checkoutPath, before.repo.checkoutPath);
  assert.equal(app.getState().repo.checkoutId, before.repo.checkoutId);
  assert.equal(app.getState().sot, before.sot);
  assert.equal(app.getState().repo.message, "폴더 선택기가 이미 열려 있습니다.");
  resolvePicker({ outcome: "cancelled" });
  await Promise.all([first, second]);
  assert.equal(app.getState().repo.message, "폴더 선택기가 이미 열려 있습니다.");
});

test("picker cancellation does not invalidate an in-flight saved checkout restore", async () => {
  let resolvePicker;
  let resolveRestore;
  const root = createMountRoot({ replaceCheckoutInputOnRender: true });
  const app = mountApp(root, {
    getSotSessionState() {
      return new Promise((resolve) => {
        resolveRestore = resolve;
      });
    },
    pickCheckoutDirectory() {
      return new Promise((resolve) => {
        resolvePicker = resolve;
      });
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  });

  root.checkoutInput.value = "";
  const submit = root.repoForm.dispatch("submit", { preventDefault() {} });
  resolvePicker({ outcome: "cancelled" });
  await submit;
  resolveRestore({
    checkout_id: "checkout-restored",
    canonical_path: "/private/tmp/restored",
    repo_status: {},
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(app.getState().repo.checkoutId, "checkout-restored");
  assert.equal(app.getState().repo.checkoutPath, "/private/tmp/restored");
  assert.equal(app.getState().sot.phase, "ready");
  assert.equal(root.ownerDocument.activeElement, root.checkoutInput);
});

test("a saved restore that settles first waits for picker cancellation", async () => {
  let resolvePicker;
  let resolveRestore;
  const root = createMountRoot({ replaceCheckoutInputOnRender: true });
  const app = mountApp(root, {
    getSotSessionState() {
      return new Promise((resolve) => {
        resolveRestore = resolve;
      });
    },
    pickCheckoutDirectory() {
      return new Promise((resolve) => {
        resolvePicker = resolve;
      });
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  });

  root.checkoutInput.value = "";
  const submit = root.repoForm.dispatch("submit", { preventDefault() {} });
  resolveRestore({
    checkout_id: "checkout-held",
    canonical_path: "/private/tmp/held",
    repo_status: {},
  });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(app.getState().repo.phase, "idle");

  resolvePicker({ outcome: "cancelled" });
  await submit;
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(app.getState().repo.checkoutId, "checkout-held");
  assert.equal(app.getState().sot.phase, "ready");
  assert.equal(root.ownerDocument.activeElement, root.checkoutInput);
});

test("restore completion never steals focus moved elsewhere after picker cancellation", async () => {
  let resolvePicker;
  let resolveRestore;
  let resolveSnapshot;
  const root = createMountRoot({ replaceCheckoutInputOnRender: true });
  mountApp(root, {
    getSotSessionState() {
      return new Promise((resolve) => {
        resolveRestore = resolve;
      });
    },
    pickCheckoutDirectory() {
      return new Promise((resolve) => {
        resolvePicker = resolve;
      });
    },
    loadSotSnapshot() {
      return new Promise((resolve) => {
        resolveSnapshot = resolve;
      });
    },
  });

  root.checkoutInput.value = "";
  const submit = root.repoForm.dispatch("submit", { preventDefault() {} });
  resolvePicker({ outcome: "cancelled" });
  await submit;
  resolveRestore({
    checkout_id: "checkout-focus-race",
    canonical_path: "/private/tmp/focus-race",
    repo_status: {},
  });
  for (let index = 0; index < 4; index += 1) await Promise.resolve();

  const laterControl = { id: "later-control", inAppRoot: true };
  root.ownerDocument.activeElement = laterControl;
  resolveSnapshot(structuredClone(snapshot));
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.notEqual(root.ownerDocument.activeElement, root.checkoutInput);
});

test("explicit picker selection wins when a saved restore settles first", async () => {
  let resolvePicker;
  let resolveRestore;
  const registrations = [];
  const root = createMountRoot();
  const app = mountApp(root, {
    getSotSessionState() {
      return new Promise((resolve) => {
        resolveRestore = resolve;
      });
    },
    pickCheckoutDirectory() {
      return new Promise((resolve) => {
        resolvePicker = resolve;
      });
    },
    async registerCheckout(path) {
      registrations.push(path);
      return { checkout_id: "checkout-selected", canonical_path: path, repo_status: {} };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  });

  root.checkoutInput.value = "";
  const submit = root.repoForm.dispatch("submit", { preventDefault() {} });
  resolveRestore({
    checkout_id: "checkout-background",
    canonical_path: "/private/tmp/background",
    repo_status: {},
  });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(app.getState().repo.phase, "idle");

  resolvePicker({ outcome: "selected", path: "/private/tmp/explicit " });
  await submit;

  assert.deepEqual(registrations, ["/private/tmp/explicit "]);
  assert.equal(app.getState().repo.checkoutId, "checkout-selected");
  assert.equal(app.getState().repo.checkoutPath, "/private/tmp/explicit ");
});

test("explicit picker selection wins when a saved restore error settles first", async () => {
  let rejectRestore;
  let resolvePicker;
  const registrations = [];
  const root = createMountRoot();
  const app = mountApp(root, {
    getSotSessionState() {
      return new Promise((_resolve, reject) => {
        rejectRestore = reject;
      });
    },
    pickCheckoutDirectory() {
      return new Promise((resolve) => {
        resolvePicker = resolve;
      });
    },
    async registerCheckout(path) {
      registrations.push(path);
      return { checkout_id: "checkout-after-error", canonical_path: path, repo_status: {} };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  });

  root.checkoutInput.value = "";
  const submit = root.repoForm.dispatch("submit", { preventDefault() {} });
  rejectRestore(new Error("saved restore failed"));
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(app.getState().repo.phase, "idle");

  resolvePicker({ outcome: "selected", path: "/private/tmp/explicit-after-error" });
  await submit;

  assert.deepEqual(registrations, ["/private/tmp/explicit-after-error"]);
  assert.equal(app.getState().repo.checkoutId, "checkout-after-error");
});

test("a stale picker failure cannot overwrite a newer clone registration", async () => {
  let resolvePicker;
  let registrationCalls = 0;
  const root = createMountRoot();
  const app = mountApp(root, {
    pickCheckoutDirectory() {
      return new Promise((resolve) => {
        resolvePicker = resolve;
      });
    },
    async registerCheckout() {
      registrationCalls += 1;
      throw new Error("stale picker must not register");
    },
    async cloneCheckout() {
      return {
        checkout_id: "checkout-cloned",
        canonical_path: "/private/app-data/harnesskit",
        repo_status: {},
      };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  });

  root.checkoutInput.value = "";
  const pickerSubmit = root.repoForm.dispatch("submit", { preventDefault() {} });
  await Promise.resolve();
  await root.cloneControl.dispatch("click");
  resolvePicker({ outcome: "failed", reason: "늦게 도착한 실패" });
  await pickerSubmit;

  assert.equal(registrationCalls, 0);
  assert.equal(app.getState().repo.phase, "registered");
  assert.equal(app.getState().repo.checkoutId, "checkout-cloned");
  assert.equal(app.getState().repo.checkoutPath, "/private/app-data/harnesskit");
  assert.equal(app.getState().sot.phase, "ready");
});

test("a stale selected picker cannot register over a newer clone", async () => {
  let resolvePicker;
  let registrationCalls = 0;
  const root = createMountRoot();
  const app = mountApp(root, {
    pickCheckoutDirectory() {
      return new Promise((resolve) => {
        resolvePicker = resolve;
      });
    },
    async registerCheckout() {
      registrationCalls += 1;
      throw new Error("stale picker must not register");
    },
    async cloneCheckout() {
      return {
        checkout_id: "checkout-cloned-selected-race",
        canonical_path: "/private/app-data/harnesskit",
        repo_status: {},
      };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  });

  root.checkoutInput.value = "";
  const pickerSubmit = root.repoForm.dispatch("submit", { preventDefault() {} });
  await Promise.resolve();
  await root.cloneControl.dispatch("click");
  resolvePicker({ outcome: "selected", path: "/private/tmp/stale-selected" });
  await pickerSubmit;

  assert.equal(registrationCalls, 0);
  assert.equal(app.getState().repo.checkoutId, "checkout-cloned-selected-race");
});

test("picker failure is a safe error and never attempts registration", async () => {
  let registrationCalls = 0;
  const root = createMountRoot();
  const app = mountApp(root, {
    async pickCheckoutDirectory() {
      return { outcome: "failed", reason: "폴더 선택기를 열지 못했습니다." };
    },
    async registerCheckout() {
      registrationCalls += 1;
      throw new Error("must not register after picker failure");
    },
  });

  root.checkoutInput.value = "";
  await root.repoForm.dispatch("submit", { preventDefault() {} });

  assert.equal(registrationCalls, 0);
  assert.equal(app.getState().repo.phase, "error");
  assert.match(app.getState().repo.message, /폴더 선택기를 열지 못했습니다/);
});

test("non-empty checkout submit bypasses the picker and registers directly", async () => {
  let pickerCalls = 0;
  const calls = [];
  const root = createMountRoot();
  const app = mountApp(root, {
    async pickCheckoutDirectory() {
      pickerCalls += 1;
      return { outcome: "cancelled" };
    },
    async registerCheckout(path) {
      calls.push(path);
      return { checkout_id: "checkout-direct", canonical_path: path, repo_status: {} };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  });

  root.checkoutInput.value = "/private/tmp/direct-harnesskit ";
  await root.repoForm.dispatch("submit", { preventDefault() {} });

  assert.equal(pickerCalls, 0);
  assert.deepEqual(calls, ["/private/tmp/direct-harnesskit "]);
  assert.equal(app.getState().repo.checkoutId, "checkout-direct");
});

test("invalid selected checkout keeps the exact candidate and prior SoT", async () => {
  const root = createMountRoot();
  const app = mountApp(root, {
    async getSotSessionState() {
      return {
        checkout_id: "checkout-existing",
        canonical_path: "/private/tmp/existing",
        repo_status: {},
      };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
    async pickCheckoutDirectory() {
      return { outcome: "selected", path: "/private/tmp/not-a-checkout " };
    },
    async registerCheckout() {
      throw new Error("invalid checkout");
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  const priorSot = app.getState().sot;

  root.checkoutInput.value = "";
  await root.repoForm.dispatch("submit", { preventDefault() {} });

  assert.equal(app.getState().repo.phase, "error");
  assert.equal(app.getState().repo.checkoutId, "checkout-existing");
  assert.equal(app.getState().repo.canonicalPath, "/private/tmp/existing");
  assert.equal(app.getState().repo.checkoutPath, "/private/tmp/not-a-checkout ");
  assert.equal(root.checkoutInput.value, "/private/tmp/not-a-checkout ");
  assert.equal(app.getState().sot, priorSot);
});

test("SoT reload does not restore install evidence invalidated by the backend load", async () => {
  const root = createMountRoot();
  const app = mountApp(root, {
    async getSotSessionState() {
      return {
        checkout_id: "checkout-stale-evidence",
        canonical_path: "/tmp/harnesskit",
        snapshot_id: snapshot.snapshot_id,
        install_evidence_id: "evidence-invalidated-by-load",
      };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(app.getState().sot.phase, "ready");
  assert.equal(app.getState().sot.installEvidenceId, null);
});

test("stale SoT snapshot response cannot cross a checkout identity change", async () => {
  let resolveCheckoutA;
  const checkoutAResponse = new Promise((resolve) => {
    resolveCheckoutA = resolve;
  });
  const snapshotB = structuredClone(snapshot);
  snapshotB.snapshot_id = "snapshot-checkout-b";
  const root = createMountRoot();
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-a", canonical_path: "/tmp/a" };
    },
    async loadSotSnapshot(checkoutId) {
      return checkoutId === "checkout-a" ? checkoutAResponse : structuredClone(snapshotB);
    },
    async cloneCheckout() {
      return {
        checkout_id: "checkout-b",
        canonical_path: "/tmp/b",
        repo_status: null,
      };
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  assert.equal(app.getState().repo.checkoutId, "checkout-a");

  root.cloneControl.dispatch("click");
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
  assert.equal(app.getState().repo.checkoutId, "checkout-b");
  assert.equal(app.getState().sot.snapshot.snapshot_id, "snapshot-checkout-b");

  const snapshotA = structuredClone(snapshot);
  snapshotA.snapshot_id = "snapshot-checkout-a-stale";
  resolveCheckoutA(snapshotA);
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(app.getState().repo.checkoutId, "checkout-b");
  assert.equal(app.getState().sot.snapshot.snapshot_id, "snapshot-checkout-b");
});

test("delegated SoT filter reads the input target value", async () => {
  const { app, root } = await mountReadyApp();

  root.shell.dispatch("input", {
    target: { id: "sot-tree-filter", value: "router" },
  });

  assert.equal(app.getState().sotView.filter, "router");
});

test("project-name input patches only project rows and preserves focus, caret, and query identity", async () => {
  const root = createMountRoot({ realisticFocusLifecycle: true });
  let localQueries = 0;
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async queryLocalInstances() {
      localQueries += 1;
      throw new Error("project explorer input must not query the backend");
    },
  });
  await flushMicrotasks();
  const shell = root.shell;
  const workbench = root.workbench;
  const renderCount = root.renderCount;
  const selectedInstanceId = app.getState().localView.selectedInstanceId;
  const scrollTop = app.getState().localView.scrollTop;
  const search = root.projectSearch;
  search.value = "Routine";
  search.selectionStart = 2;
  search.selectionEnd = 5;
  search.selectionDirection = "forward";
  search.focus();

  root.shell.dispatch("input", { target: search });

  assert.equal(app.getState().localView.projectExplorerQuery, "Routine");
  assert.equal(app.getState().localView.selectedInstanceId, selectedInstanceId);
  assert.equal(app.getState().localView.scrollTop, scrollTop);
  assert.equal(localQueries, 0);
  assert.equal(root.renderCount, renderCount);
  assert.equal(root.shell, shell);
  assert.equal(root.workbench, workbench);
  assert.equal(root.ownerDocument.activeElement, search);
  assert.deepEqual(
    [search.selectionStart, search.selectionEnd, search.selectionDirection],
    [2, 5, "forward"],
  );
  assert.match(root.projectList.innerHTML, /이름이 일치하는 project 없음/);
  app.destroy();
});

test("filtering out the selected component keeps one visible tree roving tab stop", async () => {
  const { root } = await mountReadyApp();
  const beta = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.skill.beta");

  root.shell.dispatch("click", { target: beta });
  root.shell.dispatch("input", {
    target: { id: "sot-tree-filter", value: "router" },
  });

  const componentItems = root.treeItems.filter((item) => item.dataset.componentId);
  const tabStops = root.treeItems.filter((item) => item.getAttribute("tabindex") === "0");
  assert.equal(componentItems.length, 1);
  assert.equal(componentItems[0].dataset.componentId, "harnesskit.agent.router");
  assert.equal(tabStops.length, 1);
  assert.equal(tabStops[0].dataset.sotTreeNode, "root");
});

test("semantic fallback component selection keeps the active profile and updates the viewport identity overlay", async () => {
  const { app, root } = await mountReadyApp();
  const activeProfileId = app.getState().sotView.activeProfileId;
  const router = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.agent.router");

  root.shell.dispatch("click", { target: router });
  assert.equal(app.getState().sotView.treeSelectedId, "harnesskit.agent.router");
  const renderCountBeforeSelection = root.renderCount;
  const semanticHostBeforeSelection = root.semanticHost;
  const beta = root.semanticGraphItems.find(
    (item) => item.dataset.semanticNodeId === "component:harnesskit.skill.beta",
  );
  assert.ok(beta);
  root.semanticHost.dispatch("click", { target: beta });

  assert.equal(app.getState().sotView.activeProfileId, activeProfileId);
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.skill.beta");
  assert.equal(app.getState().sotView.treeSelectedId, "harnesskit.agent.router");
  assert.equal(root.renderCount, renderCountBeforeSelection);
  assert.equal(root.semanticHost, semanticHostBeforeSelection);
  const selectedBeta = root.semanticGraphItems.find(
    (item) => item.dataset.semanticNodeId === "component:harnesskit.skill.beta",
  );
  assert.equal(selectedBeta?.getAttribute("aria-pressed"), "true");
  assert.equal(root.identityOverlay.hidden, false);
  assert.equal(root.identityTitle.textContent, "Beta");
  assert.equal(root.identityKind.textContent, "SKILL");
  assert.doesNotMatch(root.identityCount.textContent, /harnesskit\./);
});

test("Profile relation selection changes only active profile and preserves component and camera state", async () => {
  const profileSnapshot = structuredClone(snapshot);
  profileSnapshot.components[0].profile_ids = ["harnesskit.profile.engineering"];
  profileSnapshot.profiles = [{
    profile_id: "harnesskit.profile.engineering",
    status: "draft",
    title: "Engineering",
    summary: null,
    component_ids: ["harnesskit.agent.router"],
  }];
  profileSnapshot.unprofiled_component_ids = ["harnesskit.skill.beta"];
  profileSnapshot.graph_projection = {
    schema_version: 2,
    snapshot_id: profileSnapshot.snapshot_id,
    layout_seed: "seed-profile-interactions",
    nodes: [
      { node_type: "relation", node_id: "profile:harnesskit.profile.engineering", relation_kind: "profile", canonical_id: "harnesskit.profile.engineering", name: "Engineering", exact_count: 1, anchor_ordinal: 1, size_scale: 1 },
      { node_type: "relation", node_id: "unprofiled:__unprofiled__", relation_kind: "unprofiled", canonical_id: "__unprofiled__", name: "Unprofiled", exact_count: 1, anchor_ordinal: 2, size_scale: 1 },
      { node_type: "component", node_id: "component:harnesskit.agent.router", component_id: "harnesskit.agent.router", kind: "agent", domain: "core", relation_degree: 1, profile_ids: ["harnesskit.profile.engineering"], workflow_ids: [] },
      { node_type: "component", node_id: "component:harnesskit.skill.beta", component_id: "harnesskit.skill.beta", kind: "skill", domain: "work", relation_degree: 1, profile_ids: [], workflow_ids: [] },
    ],
    links: [
      { semantic: "profile-membership", link_id: "profile-membership:harnesskit.profile.engineering:harnesskit.agent.router", profile_node_id: "profile:harnesskit.profile.engineering", component_node_id: "component:harnesskit.agent.router", source_node_id: "profile:harnesskit.profile.engineering", target_node_id: "component:harnesskit.agent.router", directionality: "unordered", provenance: "CanonicalProfile" },
      { semantic: "profile-membership", link_id: "profile-membership:__unprofiled__:harnesskit.skill.beta", profile_node_id: "unprofiled:__unprofiled__", component_node_id: "component:harnesskit.skill.beta", source_node_id: "unprofiled:__unprofiled__", target_node_id: "component:harnesskit.skill.beta", directionality: "unordered", provenance: "DerivedUnprofiled" },
    ],
  };
  const projectionBefore = structuredClone(profileSnapshot.graph_projection);
  const { app, root } = await mountReadyApp(profileSnapshot);
  const beta = root.semanticGraphItems.find(
    (item) => item.dataset.semanticNodeId === "component:harnesskit.skill.beta",
  );
  root.semanticHost.dispatch("click", { target: beta });
  const unprofiled = root.semanticGraphItems.find(
    (item) => item.dataset.semanticNodeId === "unprofiled:__unprofiled__",
  );

  root.semanticHost.dispatch("click", { target: unprofiled });

  assert.equal(app.getState().sotView.activeProfileId, "__unprofiled__");
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.skill.beta");
  assert.equal(app.getState().sotView.selectedRelationNodeId, "unprofiled:__unprofiled__");
  assert.equal("graphTransform" in app.getState().sotView, false);
  assert.deepEqual(profileSnapshot.graph_projection, projectionBefore);
});

test("tree profile selection uses the same transition as matrix and keeps install profile synchronized", async () => {
  const profileSnapshot = structuredClone(snapshot);
  profileSnapshot.components[0].profile_ids = [
    "harnesskit.profile.engineering",
    "harnesskit.profile.work",
  ];
  profileSnapshot.components[0].targets = [{
    target_id: "codex",
    support_status: "runtime_supported",
  }];
  profileSnapshot.profiles = [
    {
      profile_id: "harnesskit.profile.engineering",
      status: "draft",
      title: "Engineering",
      summary: null,
      component_ids: ["harnesskit.agent.router"],
    },
    {
      profile_id: "harnesskit.profile.work",
      status: "draft",
      title: "Work",
      summary: null,
      component_ids: ["harnesskit.agent.router"],
    },
  ];
  profileSnapshot.unprofiled_component_ids = ["harnesskit.skill.beta"];
  profileSnapshot.graph_projection = {
    schema_version: 2,
    snapshot_id: profileSnapshot.snapshot_id,
    layout_seed: "seed-tree-profile-interactions",
    nodes: [
      { node_type: "relation", node_id: "profile:harnesskit.profile.engineering", relation_kind: "profile", canonical_id: "harnesskit.profile.engineering", name: "Engineering", exact_count: 1, anchor_ordinal: 1, size_scale: 1 },
      { node_type: "relation", node_id: "profile:harnesskit.profile.work", relation_kind: "profile", canonical_id: "harnesskit.profile.work", name: "Work", exact_count: 1, anchor_ordinal: 2, size_scale: 1 },
      { node_type: "relation", node_id: "unprofiled:__unprofiled__", relation_kind: "unprofiled", canonical_id: "__unprofiled__", name: "Unprofiled", exact_count: 1, anchor_ordinal: 3, size_scale: 1 },
      { node_type: "component", node_id: "component:harnesskit.agent.router", component_id: "harnesskit.agent.router", kind: "agent", domain: "core", relation_degree: 2, profile_ids: ["harnesskit.profile.engineering", "harnesskit.profile.work"], workflow_ids: [] },
      { node_type: "component", node_id: "component:harnesskit.skill.beta", component_id: "harnesskit.skill.beta", kind: "skill", domain: "work", relation_degree: 1, profile_ids: [], workflow_ids: [] },
    ],
    links: [
      { semantic: "profile-membership", link_id: "profile-membership:engineering:router", profile_node_id: "profile:harnesskit.profile.engineering", component_node_id: "component:harnesskit.agent.router", source_node_id: "profile:harnesskit.profile.engineering", target_node_id: "component:harnesskit.agent.router", directionality: "unordered", provenance: "CanonicalProfile" },
      { semantic: "profile-membership", link_id: "profile-membership:work:router", profile_node_id: "profile:harnesskit.profile.work", component_node_id: "component:harnesskit.agent.router", source_node_id: "profile:harnesskit.profile.work", target_node_id: "component:harnesskit.agent.router", directionality: "unordered", provenance: "CanonicalProfile" },
      { semantic: "profile-membership", link_id: "profile-membership:unprofiled:beta", profile_node_id: "unprofiled:__unprofiled__", component_node_id: "component:harnesskit.skill.beta", source_node_id: "unprofiled:__unprofiled__", target_node_id: "component:harnesskit.skill.beta", directionality: "unordered", provenance: "DerivedUnprofiled" },
    ],
  };
  const { app, root } = await mountReadyApp(profileSnapshot);
  const router = root.semanticGraphItems.find(
    (item) => item.dataset.semanticNodeId === "component:harnesskit.agent.router",
  );
  root.semanticHost.dispatch("click", { target: router });
  assert.equal(app.getState().install.form.profileId, "harnesskit.profile.engineering");
  const workProfile = root.treeItems.find(
    (item) => item.dataset.sotTreeProfile === "harnesskit.profile.work",
  );

  root.shell.dispatch("click", { target: workProfile });

  assert.equal(app.getState().sotView.activeProfileId, "harnesskit.profile.work");
  assert.equal(app.getState().install.form.profileId, "harnesskit.profile.work");
});

test("left structural tree selection and expansion never change central component selection", async () => {
  const { app, root } = await mountReadyApp();
  const beta = root.semanticGraphItems.find(
    (item) => item.dataset.semanticNodeId === "component:harnesskit.skill.beta",
  );
  root.semanticHost.dispatch("click", { target: beta });
  const selectedComponentId = app.getState().sotView.selectedComponentId;
  const components = root.treeItems.find((item) => item.dataset.sotTreeNode === "components");

  root.shell.dispatch("click", { target: components });

  assert.equal(app.getState().sotView.treeSelectedId, "components");
  assert.equal(app.getState().sotView.selectedComponentId, selectedComponentId);
  assert.deepEqual(app.getState().sotView.treeExpandedIds, ["root", "profiles"]);
});

test("SoT structural tree supports standard ArrowLeft and ArrowRight expansion", async () => {
  const { app, root } = await mountReadyApp();
  let components = root.treeItems.find((item) => item.dataset.sotTreeNode === "components");
  let prevented = false;
  root.shell.dispatch("keydown", {
    target: components,
    key: "ArrowLeft",
    preventDefault() { prevented = true; },
  });

  assert.equal(prevented, true);
  assert.deepEqual(app.getState().sotView.treeExpandedIds, ["root", "profiles"]);
  assert.equal(root.ownerDocument.activeElement?.dataset.sotTreeNode, "components");
  components = root.treeItems.find((item) => item.dataset.sotTreeNode === "components");
  root.shell.dispatch("keydown", {
    target: components,
    key: "ArrowRight",
    preventDefault() {},
  });
  assert.deepEqual(app.getState().sotView.treeExpandedIds, ["root", "components", "profiles"]);
  assert.equal(root.ownerDocument.activeElement?.dataset.sotTreeNode, "components");
});

test("SoT tree scroll and navigation state survive graph selection and segment round trip", async () => {
  const { app, root } = await mountReadyApp();
  const router = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.agent.router");
  root.shell.dispatch("click", { target: router });
  root.treeScroll.scrollTop = 93;
  root.treeScroll.dispatch("scroll");
  const treeState = {
    treeSelectedId: app.getState().sotView.treeSelectedId,
    treeExpandedIds: [...app.getState().sotView.treeExpandedIds],
    unfilteredTreeScrollTop: app.getState().sotView.unfilteredTreeScrollTop,
    filteredTreeScrollTop: app.getState().sotView.filteredTreeScrollTop,
    filter: app.getState().sotView.filter,
  };
  const beta = root.semanticGraphItems.find(
    (item) => item.dataset.semanticNodeId === "component:harnesskit.skill.beta",
  );
  root.semanticHost.dispatch("click", { target: beta });
  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "local").dispatch("click");
  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "sot").dispatch("click");

  assert.deepEqual({
    treeSelectedId: app.getState().sotView.treeSelectedId,
    treeExpandedIds: [...app.getState().sotView.treeExpandedIds],
    unfilteredTreeScrollTop: app.getState().sotView.unfilteredTreeScrollTop,
    filteredTreeScrollTop: app.getState().sotView.filteredTreeScrollTop,
    filter: app.getState().sotView.filter,
  }, treeState);
  assert.equal(root.treeScroll.scrollTop, 93);
});

test("semantic fallback safely selects escaped component ids and Escape clears graph-owned state", async () => {
  const unsafeId = 'harnesskit.skill.bad"node';
  const unsafeSnapshot = structuredClone(snapshot);
  unsafeSnapshot.components[1].component_id = unsafeId;
  unsafeSnapshot.unprofiled_component_ids[1] = unsafeId;
  unsafeSnapshot.graph_projection.nodes[2].component_id = unsafeId;
  unsafeSnapshot.graph_projection.nodes[2].node_id = `component:${unsafeId}`;
  unsafeSnapshot.graph_projection.links[1].link_id = `profile-membership:__unprofiled__:${unsafeId}`;
  unsafeSnapshot.graph_projection.links[1].component_node_id = `component:${unsafeId}`;
  unsafeSnapshot.graph_projection.links[1].target_node_id = `component:${unsafeId}`;
  const { app, root } = await mountReadyApp(unsafeSnapshot);
  const unsafeComponent = root.semanticGraphItems.find(
    (item) => item.dataset.semanticNodeId === `component:${unsafeId}`,
  );
  assert.ok(unsafeComponent);

  assert.doesNotThrow(() => root.semanticHost.dispatch("click", { target: unsafeComponent }));
  assert.equal(app.getState().sotView.selectedComponentId, unsafeId);
  assert.equal(root.identityOverlay.hidden, false);
  assert.equal(root.identityTitle.textContent, "Beta");
  assert.equal(root.identityKind.textContent, "SKILL");
  assert.doesNotMatch(
    [root.identityTitle.textContent, root.identityKind.textContent, root.identityCount.textContent].join(" "),
    /harnesskit\./,
  );

  let prevented = false;
  root.semanticHost.dispatch("keydown", {
    target: root.semanticHost,
    key: "Escape",
    preventDefault() {
      prevented = true;
    },
  });
  assert.equal(prevented, true);
  assert.equal(app.getState().sotView.selectedComponentId, null);

  root.semanticHost.dispatch("click", { target: unsafeComponent });
  assert.equal(app.getState().sotView.selectedComponentId, unsafeId);
  let shellPrevented = false;
  root.shell.dispatch("keydown", {
    target: root.shell,
    key: "Escape",
    preventDefault() {
      shellPrevented = true;
    },
  });
  assert.equal(shellPrevented, true);
  assert.equal(app.getState().sotView.selectedComponentId, null);
});

test("switching dashboard segments restores each segment workbench scroll position", async () => {
  const { root } = await mountReadyApp();
  root.workbench.scrollTop = 321;

  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "local").dispatch("click");
  root.workbench.scrollTop = 47;
  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "sot").dispatch("click");

  assert.equal(root.workbench.scrollTop, 321);
  assert.equal(root.ownerDocument.activeElement?.dataset.dashboardSegment, "sot");
});

test("general Local entry resets view and scroll, starts a missing session snapshot, then reconciles Accepted", async () => {
  const root = createMountRoot();
  const calls = [];
  const scanStates = [
    {
      state_revision: 0,
      current_attempt: null,
      latest_terminal_report: null,
      latest_complete: null,
      latest_partial: null,
    },
    {
      state_revision: 1,
      current_attempt: { attempt_id: "attempt-local-1", state: "running", error_code: null },
      latest_terminal_report: null,
      latest_complete: null,
      latest_partial: null,
    },
  ];
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async getLocalScanState() {
      calls.push("get_local_scan_state");
      return scanStates.shift();
    },
    async startLocalScan() {
      calls.push("start_local_scan");
      return { status: "accepted", attempt_id: "attempt-local-1" };
    },
  });
  for (let index = 0; index < 5; index += 1) await Promise.resolve();
  root.workbench.scrollTop = 212;

  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "local").dispatch("click");
  for (let index = 0; index < 12; index += 1) await Promise.resolve();

  assert.deepEqual(calls, ["get_local_scan_state", "start_local_scan", "get_local_scan_state"]);
  assert.equal(app.getState().ui.activeSegment, "local");
  assert.deepEqual(app.getState().localView.locationFilter, { scope: "all", projectId: null });
  assert.equal(app.getState().localView.toolId, null);
  assert.equal(app.getState().localView.kind, null);
  assert.equal(app.getState().localView.query, "");
  assert.equal(app.getState().localView.selectedInstanceId, null);
  assert.equal(app.getState().localView.scrollTop, 0);
  assert.equal(root.workbench.scrollTop, 0);
  assert.equal(app.getState().localData.currentAttempt.attemptId, "attempt-local-1");
});

test("Local scan falls back to bounded store reconcile when every event is missed", async () => {
  const root = createMountRoot();
  const scheduled = [];
  const states = [
    {
      state_revision: 0,
      current_attempt: null,
      latest_terminal_report: null,
      latest_complete: null,
      latest_partial: null,
    },
    {
      state_revision: 1,
      current_attempt: { attempt_id: "attempt-fallback", state: "running", error_code: null },
      latest_terminal_report: null,
      latest_complete: null,
      latest_partial: null,
    },
    {
      state_revision: 2,
      current_attempt: null,
      latest_terminal_report: { attempt_id: "attempt-fallback", state: "complete", error_code: null },
      latest_complete: { snapshot_id: "snapshot-fallback", attempt_id: "attempt-fallback", status: "complete" },
      latest_partial: null,
    },
  ];
  const app = mountApp(root, {
    async getSotSessionState() { return null; },
    async getLocalScanState() { return states.shift() ?? states.at(-1); },
    async startLocalScan() { return { status: "accepted", attempt_id: "attempt-fallback" }; },
    async queryLocalInstances(request) {
      return {
        snapshotId: request.snapshotId,
        qualifiedTools: [],
        projects: [],
        items: [],
        kindCounts: [],
        counts: { totalInstances: 0, matchedInstances: 0 },
      };
    },
  }, {
    setTimeout(callback) {
      scheduled.push(callback);
      return callback;
    },
    clearTimeout(handle) {
      const index = scheduled.indexOf(handle);
      if (index >= 0) scheduled.splice(index, 1);
    },
    localReconcileIntervalMs: 10,
  });
  for (let index = 0; index < 5; index += 1) await Promise.resolve();

  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "local").dispatch("click");
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
  assert.equal(app.getState().localData.phase, "running");
  assert.equal(scheduled.length, 1);

  scheduled.shift()();
  for (let index = 0; index < 12; index += 1) await Promise.resolve();

  assert.equal(app.getState().localData.phase, "ready");
  assert.equal(app.getState().localData.latestComplete.snapshotId, "snapshot-fallback");
  assert.equal(scheduled.length, 0);
  app.destroy();
});

test("fallback reconcile continues while a rescan retains the previous complete snapshot", async () => {
  const root = createMountRoot();
  const scheduled = [];
  const states = [
    {
      state_revision: 5,
      current_attempt: { attempt_id: "attempt-rescan", state: "running", error_code: null },
      latest_terminal_report: { attempt_id: "attempt-old", state: "complete", error_code: null },
      latest_complete: { snapshot_id: "snapshot-old", attempt_id: "attempt-old", status: "complete" },
      latest_partial: null,
    },
    {
      state_revision: 6,
      current_attempt: null,
      latest_terminal_report: { attempt_id: "attempt-rescan", state: "complete", error_code: null },
      latest_complete: { snapshot_id: "snapshot-new", attempt_id: "attempt-rescan", status: "complete" },
      latest_partial: null,
    },
  ];
  const app = mountApp(root, {
    async getSotSessionState() { return null; },
    async getLocalScanState() { return states.shift(); },
    async queryLocalInstances(request) {
      return {
        snapshotId: request.snapshotId,
        qualifiedTools: [],
        projects: [],
        items: [],
        kindCounts: [],
        counts: { totalInstances: 0, matchedInstances: 0 },
      };
    },
  }, {
    setTimeout(callback) {
      scheduled.push(callback);
      return callback;
    },
    clearTimeout(handle) {
      const index = scheduled.indexOf(handle);
      if (index >= 0) scheduled.splice(index, 1);
    },
    localReconcileIntervalMs: 10,
  });
  for (let index = 0; index < 5; index += 1) await Promise.resolve();

  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "local").dispatch("click");
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
  assert.equal(app.getState().localData.latestComplete.snapshotId, "snapshot-old");
  assert.equal(app.getState().localData.currentAttempt.attemptId, "attempt-rescan");
  assert.equal(scheduled.length, 1);

  scheduled.shift()();
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
  assert.equal(app.getState().localData.latestComplete.snapshotId, "snapshot-new");
  assert.equal(app.getState().localData.currentAttempt, null);
  assert.equal(scheduled.length, 0);
  app.destroy();
});

test("Local re-entry uses the latest complete snapshot immediately and never starts a replacement scan", async () => {
  const root = createMountRoot();
  const calls = [];
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async getLocalScanState() {
      calls.push("get_local_scan_state");
      return {
        state_revision: 4,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-local-4", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-local-4", attempt_id: "attempt-local-4", status: "complete" },
        latest_partial: null,
      };
    },
    async startLocalScan() {
      calls.push("start_local_scan");
      return { status: "accepted", attempt_id: "unexpected" };
    },
    async queryLocalInstances(request) {
      calls.push({ query: request });
      return {
        snapshotId: request.snapshotId,
        items: [],
        kindCounts: [],
        counts: { totalInstances: 0, matchedInstances: 0 },
      };
    },
  });
  for (let index = 0; index < 5; index += 1) await Promise.resolve();

  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "local").dispatch("click");
  for (let index = 0; index < 12; index += 1) await Promise.resolve();

  assert.equal(calls.includes("start_local_scan"), false);
  assert.equal(calls[0], "get_local_scan_state");
  assert.deepEqual(calls[1], {
    query: {
      snapshotId: "snapshot-local-4",
      locationFilter: { scope: "all", projectId: null },
      toolId: null,
      kind: null,
      query: "",
      correlationProjectionId: null,
      verifiedComponentId: null,
    },
  });
  assert.equal(app.getState().localData.latestComplete.snapshotId, "snapshot-local-4");
  assert.equal(app.getState().localData.queryResult.snapshotId, "snapshot-local-4");

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-tool]", { localTool: "codex" }),
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  assert.equal(app.getState().localView.toolId, "codex");
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-reset-local-filters]", {}),
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(calls.includes("start_local_scan"), false);
  assert.deepEqual(app.getState().localView, createLocalViewState());
  assert.deepEqual(calls.at(-1), {
    query: {
      snapshotId: "snapshot-local-4",
      locationFilter: { scope: "all", projectId: null },
      toolId: null,
      kind: null,
      query: "",
      correlationProjectionId: null,
      verifiedComponentId: null,
    },
  });
});

test("Local dashboard refresh rescans an existing snapshot without a checkout and keeps late results in Local state", async () => {
  const root = createMountRoot();
  const pending = deferred();
  let emitScan;
  let startCount = 0;
  let sotLoadCount = 0;
  const queries = [];
  let scanState = {
    state_revision: 4,
    current_attempt: null,
    latest_terminal_report: { attempt_id: "attempt-old", state: "complete" },
    latest_complete: { snapshot_id: "snapshot-old", attempt_id: "attempt-old", status: "complete" },
  };
  const app = mountApp(root, {
    async getSotSessionState() { return null; },
    async loadSotSnapshot() { sotLoadCount += 1; throw new Error("SoT must stay untouched"); },
    async getLocalScanState() { return scanState; },
    startLocalScan() { startCount += 1; return pending.promise; },
    onLocalScanChanged(handler) { emitScan = handler; return () => {}; },
    async queryLocalInstances(request) {
      queries.push(request.snapshotId);
      return { snapshotId: request.snapshotId, items: [], counts: { matchedInstances: 0 } };
    },
  });
  await flushMicrotasks();
  root.dashboardItems.find((tab) => tab.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks();
  assert.equal(app.getState().repo.checkoutId, null);
  assert.equal(app.getState().localData.latestComplete.snapshotId, "snapshot-old");
  assert.equal(startCount, 0);
  const originalSot = app.getState().sot;

  root.querySelector("#dashboard-refresh").dispatch("click");
  assert.equal(startCount, 1);
  assert.equal(app.getState().localData.scanStartPending, true);
  assert.match(root.markup, /aria-label="PC 설치 하네스 새로고침 중"[^>]*disabled/);
  root.querySelector("#dashboard-refresh").dispatch("click");
  assert.equal(startCount, 1);
  assert.equal(app.getState().localData.latestComplete.snapshotId, "snapshot-old");
  assert.equal(app.getState().sot, originalSot);

  root.dashboardItems.find((tab) => tab.dataset.dashboardSegment === "sot").dispatch("click");
  scanState = {
    ...scanState,
    state_revision: 5,
    current_attempt: { attempt_id: "attempt-new", state: "running" },
  };
  pending.resolve({ status: "accepted", attempt_id: "attempt-new" });
  await flushMicrotasks();
  assert.equal(app.getState().ui.activeSegment, "sot");
  assert.equal(app.getState().localData.phase, "running");
  assert.equal(app.getState().sot, originalSot);
  assert.equal(sotLoadCount, 0);

  scanState = {
    state_revision: 6,
    current_attempt: null,
    latest_terminal_report: { attempt_id: "attempt-new", state: "complete" },
    latest_complete: { snapshot_id: "snapshot-new", attempt_id: "attempt-new", status: "complete" },
  };
  await emitScan({
    attempt_id: "attempt-new",
    sequence: 0,
    state_revision: 6,
    payload: { state: "complete", snapshot_id: "snapshot-new" },
  });
  assert.equal(app.getState().ui.activeSegment, "sot");
  assert.equal(app.getState().localData.latestComplete.snapshotId, "snapshot-new");
  assert.equal(app.getState().sot, originalSot);
  assert.equal(sotLoadCount, 0);

  root.dashboardItems.find((tab) => tab.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks();
  assert.equal(app.getState().localData.queryResult.snapshotId, "snapshot-new");
  assert.equal(queries.at(-1), "snapshot-new");
  assert.equal(startCount, 1);
  app.destroy();
});

test("Local query completion preserves the current workbench scroll outside a general entry reset", async () => {
  const root = createMountRoot();
  let resolveQuery;
  const queryResponse = new Promise((resolve) => {
    resolveQuery = resolve;
  });
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async getLocalScanState() {
      return {
        state_revision: 5,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-local-5", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-local-5", attempt_id: "attempt-local-5", status: "complete" },
        latest_partial: null,
      };
    },
    async queryLocalInstances() {
      return queryResponse;
    },
  });
  for (let index = 0; index < 5; index += 1) await Promise.resolve();
  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "local").dispatch("click");
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  root.workbench.scrollTop = 144;
  root.workbench.dispatch("scroll");

  resolveQuery({
    snapshotId: "snapshot-local-5",
    items: [],
    kindCounts: [],
    counts: { totalInstances: 0, matchedInstances: 0 },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(app.getState().localView.scrollTop, 144);
  assert.equal(root.workbench.scrollTop, 144);
});

test("Local detail and source preview rendering keep a deeply selected row at its viewport anchor", async () => {
  const root = createMountRoot();
  const animationFrames = createAnimationFrameHarness();
  const detail = deferred();
  const sourceHeader = deferred();
  const sourceChunk = deferred();
  const sourceRequests = [];
  const items = createLocalInstanceItems("deep", "Deep");
  const selectedId = "instance-deep-18";
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async getLocalScanState() {
      return {
        state_revision: 18,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-deep", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-deep", attempt_id: "attempt-deep", status: "complete" },
        latest_partial: null,
      };
    },
    async queryLocalInstances() {
      return {
        snapshotId: "snapshot-deep",
        items,
        kindCounts: [{ kind: "skill", count: items.length }],
        counts: { totalInstances: items.length, matchedInstances: items.length },
      };
    },
    async getLocalInstanceDetail() {
      return detail.promise;
    },
    async openLocalSourcePreview(request) {
      sourceRequests.push(request);
      return sourceHeader.promise;
    },
    async readLocalSourcePreviewChunk() {
      return sourceChunk.promise;
    },
  }, {
    requestAnimationFrame: animationFrames.requestAnimationFrame,
    cancelAnimationFrame: animationFrames.cancelAnimationFrame,
  });
  await flushMicrotasks(8);
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks(16);

  root.workbench.scrollTop = 1330;
  root.workbench.dispatch("scroll");
  const stableWorkbench = root.workbench;
  const renderCountBeforeSelection = root.renderCount;
  root.blockWorkbenchLayout();
  const selected = root.localInstanceItems.find((control) => control.dataset.localInstance === selectedId);
  assert.ok(selected);
  root.shell.dispatch("click", { target: selected });
  await flushMicrotasks(8);
  assert.equal(sourceRequests.length, 1);
  assert.equal(sourceRequests[0].snapshotId, "snapshot-deep");
  assert.equal(sourceRequests[0].instanceId, selectedId);

  assert.equal(root.workbench, stableWorkbench);
  assert.equal(root.workbench.scrollTop, 1330);
  assert.equal(root.renderCount, renderCountBeforeSelection);
  assert.equal(animationFrames.size, 0);
  root.releaseWorkbenchLayout();

  const viewportOffset = () => {
    const control = root.localInstanceItems.find((candidate) => candidate.dataset.localInstance === selectedId);
    assert.ok(control);
    return control.getBoundingClientRect().top - root.workbench.getBoundingClientRect().top;
  };
  const expectedOffset = viewportOffset();
  assert.equal(root.workbench.scrollTop, 1330);

  detail.resolve(items[18]);
  sourceHeader.resolve({
    preview_session_id: "preview-deep",
    view_generation: sourceRequests[0].viewGeneration,
    snapshot_id: "snapshot-deep",
    instance_id: selectedId,
    canonical_path: `/fixture-home/.codex/skills/${selectedId}/SKILL.md`,
    source_revision: "source-deep",
    changed_since_snapshot: false,
    format: "text",
    total_bytes: 32,
    total_chunks: 1,
    chunk_bytes: 65536,
    selected_chunk_index: 0,
    issue: null,
  });
  sourceChunk.resolve({
    preview_session_id: "preview-deep",
    source_revision: "source-deep",
    chunk_index: 0,
    is_last: true,
    content: { format: "text", before: "fixture", selected: null, after: "" },
  });
  await flushMicrotasks(20);

  assert.equal(app.getState().localView.selectedInstanceId, selectedId);
  assert.equal(app.getState().sourcePreview.phase, "ready", app.getState().sourcePreview.error);
  assert.match(root.markup, /data-source-chunk-range/);
  assert.equal(root.workbench, stableWorkbench);
  assert.equal(root.renderCount, renderCountBeforeSelection);
  assert.ok(root.workbench.scrollTop > 0);
  assert.equal(app.getState().localView.scrollTop, root.workbench.scrollTop);
  assert.equal(viewportOffset(), expectedOffset);
  assert.equal(animationFrames.size, 0);

  const userScrollTop = root.workbench.scrollTop - 120;
  root.workbench.scrollTop = userScrollTop;
  root.workbench.dispatch("wheel");
  root.workbench.dispatch("scroll");
  assert.equal(animationFrames.size, 0);
  const userViewportOffset = viewportOffset();
  const openAiSettings = root.querySelector("[data-open-ai-settings]");
  assert.ok(openAiSettings);
  const renderCountBeforeAiSettings = root.renderCount;
  root.shell.dispatch("click", { target: openAiSettings });
  assert.ok(root.renderCount > renderCountBeforeAiSettings);
  assert.equal(animationFrames.size, 1);
  animationFrames.flushAll();
  assert.equal(viewportOffset(), userViewportOffset);
  assert.equal(app.getState().localView.scrollTop, root.workbench.scrollTop);
  app.destroy();
  assert.equal(animationFrames.size, 0);
});

test("Local selection keeps the existing workbench visible while detail and source load", async () => {
  const root = createMountRoot({ emitWorkbenchClampScrollAfterRender: true });
  const animationFrames = createAnimationFrameHarness();
  const detail = deferred();
  const sourceHeader = deferred();
  const sourceChunk = deferred();
  const sourceRequests = [];
  const items = createLocalInstanceItems("stable", "Stable");
  const selectedId = "instance-stable-18";
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async getLocalScanState() {
      return {
        state_revision: 20,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-stable", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-stable", attempt_id: "attempt-stable", status: "complete" },
        latest_partial: null,
      };
    },
    async queryLocalInstances() {
      return {
        snapshotId: "snapshot-stable",
        items,
        kindCounts: [{ kind: "skill", count: items.length }],
        counts: { totalInstances: items.length, matchedInstances: items.length },
      };
    },
    async getLocalInstanceDetail() {
      return detail.promise;
    },
    async openLocalSourcePreview(request) {
      sourceRequests.push(request);
      return sourceHeader.promise;
    },
    async readLocalSourcePreviewChunk() {
      return sourceChunk.promise;
    },
  }, {
    requestAnimationFrame: animationFrames.requestAnimationFrame,
    cancelAnimationFrame: animationFrames.cancelAnimationFrame,
  });
  await flushMicrotasks(8);
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks(16);

  const expectedScrollTop = 1330;
  root.workbench.scrollTop = expectedScrollTop;
  root.workbench.dispatch("scroll");
  const stableWorkbench = root.workbench;
  const renderCountBeforeSelection = root.renderCount;
  root.blockWorkbenchLayout();

  const selected = root.localInstanceItems.find(
    (control) => control.dataset.localInstance === selectedId,
  );
  assert.ok(selected);
  root.shell.dispatch("click", { target: selected });
  await flushMicrotasks(8);

  assert.equal(root.workbench, stableWorkbench);
  assert.equal(root.workbench.scrollTop, expectedScrollTop);
  assert.equal(root.renderCount, renderCountBeforeSelection);
  assert.equal(animationFrames.size, 0);
  assert.equal(app.getState().localView.selectedInstanceId, selectedId);
  root.releaseWorkbenchLayout();

  detail.resolve(items[18]);
  sourceHeader.resolve({
    preview_session_id: "preview-stable",
    view_generation: sourceRequests[0].viewGeneration,
    snapshot_id: "snapshot-stable",
    instance_id: selectedId,
    canonical_path: `/fixture-home/.codex/skills/${selectedId}/SKILL.md`,
    source_revision: "source-stable",
    changed_since_snapshot: false,
    format: "text",
    total_bytes: 16,
    total_chunks: 1,
    chunk_bytes: 65536,
    selected_chunk_index: 0,
    issue: null,
  });
  sourceChunk.resolve({
    preview_session_id: "preview-stable",
    source_revision: "source-stable",
    chunk_index: 0,
    is_last: true,
    content: { format: "text", before: "stable", selected: null, after: "" },
  });
  await flushMicrotasks(20);

  assert.equal(app.getState().sourcePreview.phase, "ready", app.getState().sourcePreview.error);
  assert.equal(root.workbench, stableWorkbench);
  assert.equal(root.workbench.scrollTop, expectedScrollTop);
  assert.equal(root.renderCount, renderCountBeforeSelection);
  assert.equal(animationFrames.size, 0);
});

test("Local detail failure stays visible without replacing the workbench", async () => {
  const root = createMountRoot();
  const items = createLocalInstanceItems("failure", "Failure", 1);
  const expectedMessage = "선택한 Local instance detail을 불러오지 못했습니다.";
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async getLocalScanState() {
      return {
        state_revision: 21,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-failure", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-failure", attempt_id: "attempt-failure", status: "complete" },
        latest_partial: null,
      };
    },
    async queryLocalInstances() {
      return {
        snapshotId: "snapshot-failure",
        items,
        kindCounts: [{ kind: "skill", count: items.length }],
        counts: { totalInstances: items.length, matchedInstances: items.length },
      };
    },
    async getLocalInstanceDetail() {
      throw new Error("detail unavailable");
    },
  });
  await flushMicrotasks(8);
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks(16);

  const stableWorkbench = root.workbench;
  const renderCountBeforeSelection = root.renderCount;
  root.shell.dispatch("click", { target: root.localInstanceItems[0] });
  await flushMicrotasks(12);

  assert.equal(app.getState().localData.message, expectedMessage);
  assert.equal(root.workbench, stableWorkbench);
  assert.equal(root.renderCount, renderCountBeforeSelection);
  assert.match(root.querySelector("[data-local-status-stack]").innerHTML, new RegExp(expectedMessage));
  assert.equal(root.querySelector("#local-runtime-status").textContent, expectedMessage);
});

test("Local selection bypasses WebKit scroll clamping by retaining the workbench", async () => {
  const root = createMountRoot({ emitWorkbenchClampScrollAfterRender: true });
  const animationFrames = createAnimationFrameHarness();
  const items = createLocalInstanceItems("webkit", "WebKit");
  const selectedId = "instance-webkit-18";
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async getLocalScanState() {
      return {
        state_revision: 19,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-webkit", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-webkit", attempt_id: "attempt-webkit", status: "complete" },
        latest_partial: null,
      };
    },
    async queryLocalInstances() {
      return {
        snapshotId: "snapshot-webkit",
        items,
        kindCounts: [{ kind: "skill", count: items.length }],
        counts: { totalInstances: items.length, matchedInstances: items.length },
      };
    },
    async getLocalInstanceDetail() {
      return items[18];
    },
  }, {
    requestAnimationFrame: animationFrames.requestAnimationFrame,
    cancelAnimationFrame: animationFrames.cancelAnimationFrame,
  });
  await flushMicrotasks(8);
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks(16);

  const expectedScrollTop = 1330;
  root.workbench.scrollTop = expectedScrollTop;
  root.workbench.dispatch("scroll");
  const stableWorkbench = root.workbench;
  const renderCountBeforeSelection = root.renderCount;
  root.blockWorkbenchLayout();
  const selected = root.localInstanceItems.find(
    (control) => control.dataset.localInstance === selectedId,
  );
  assert.ok(selected);
  root.shell.dispatch("click", { target: selected });
  await flushMicrotasks(8);
  assert.equal(animationFrames.size, 0);
  assert.equal(root.workbench, stableWorkbench);
  assert.equal(root.workbench.scrollTop, expectedScrollTop);
  assert.equal(app.getState().localView.scrollTop, expectedScrollTop);
  assert.equal(root.renderCount, renderCountBeforeSelection);

  const selectedAgain = root.localInstanceItems.find(
    (control) => control.dataset.localInstance === selectedId,
  );
  root.shell.dispatch("click", { target: selectedAgain });
  await flushMicrotasks(8);
  root.releaseWorkbenchLayout();
  root.flushWorkbenchClampScrollEvents();
  assert.equal(animationFrames.size, 0);
  assert.equal(root.workbench, stableWorkbench);
  assert.equal(root.workbench.scrollTop, expectedScrollTop);
  assert.equal(app.getState().localView.scrollTop, expectedScrollTop);
  assert.equal(root.renderCount, renderCountBeforeSelection);
});

test("Local async identity includes snapshot projection and instance", () => {
  const localData = {
    latestComplete: { snapshotId: "snapshot-a", attemptId: "attempt-a", status: "complete" },
    latestPartial: null,
  };
  const localView = {
    selectedInstanceId: "instance-a",
    correlationProjectionId: "projection-a",
  };

  assert.equal(matchesLocalRequestIdentity(localData, localView, {
    snapshotId: "snapshot-a",
    instanceId: "instance-a",
    correlationProjectionId: "projection-a",
  }), true);
  assert.equal(matchesLocalRequestIdentity(localData, localView, {
    snapshotId: "snapshot-b",
    instanceId: "instance-a",
    correlationProjectionId: "projection-a",
  }), false);
  assert.equal(matchesLocalRequestIdentity(localData, localView, {
    snapshotId: "snapshot-a",
    instanceId: "instance-a",
    correlationProjectionId: "projection-b",
  }), false);
});

test("Local batch removal reconciles an early terminal rescan event before clearing confirmed IDs", async () => {
  const root = createMountRoot({ realisticFocusLifecycle: true });
  const calls = { prepare: [], apply: [], reconcile: [], localEventHandler: null };
  const items = createLocalInstanceItems("removal", "Removal", 2);
  const applyResponse = deferred();
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    onLocalScanChanged(handler) {
      calls.localEventHandler = handler;
      return () => {};
    },
    async getLocalScanState() {
      return {
        state_revision: 1,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-removal-1", state: "complete" },
        latest_complete: { snapshot_id: "snapshot-removal-1", attempt_id: "attempt-removal-1", status: "complete" },
        latest_partial: null,
      };
    },
    async queryLocalInstances(request) {
      return {
        snapshotId: request.snapshotId,
        items,
        kindCounts: [{ kind: "skill", count: 2 }],
        counts: { totalInstances: 2, matchedInstances: 2 },
      };
    },
    async prepareLocalRemoval(request) {
      calls.prepare.push(structuredClone(request));
      return {
        planId: "plan-removal-1",
        planDigest: "digest-removal-1",
        eligibleMembers: items.map((item, index) => ({
          instanceId: item.instanceId,
          displayName: item.displayName,
          effect: index === 0 ? "shared_config_entry" : "dedicated_file",
        })),
        blockedMembers: [],
        sharedConfigEntryRemovalCount: 1,
        dedicatedFileDeletionCount: 1,
        parentFolderDeletionCount: 0,
      };
    },
    async applyLocalRemoval(request) {
      calls.apply.push(structuredClone(request));
      return applyResponse.promise;
    },
    async reconcileLocalRemoval(request) {
      calls.reconcile.push(structuredClone(request));
      return {
        snapshotId: request.snapshotId,
        state: "confirmed",
        confirmedAbsentInstanceIds: [items[0].instanceId],
      };
    },
  });

  await flushMicrotasks(8);
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks(16);

  root.shell.dispatch("click", { target: root.querySelector("[data-enter-local-removal]") });
  await flushMicrotasks(4);

  for (const checkbox of root.localRemovalControls.filter(
    (control) => control.dataset.localRemovalSelection,
  )) {
    checkbox.checked = true;
    root.shell.dispatch("change", { target: checkbox });
  }
  assert.deepEqual(app.getState().localRemoval.selectedInstanceIds, items.map((item) => item.instanceId));

  root.shell.dispatch("click", { target: root.querySelector("[data-prepare-local-removal]") });
  await flushMicrotasks(12);
  assert.deepEqual(calls.prepare, [{
    snapshotId: "snapshot-removal-1",
    instanceIds: items.map((item) => item.instanceId),
  }]);
  assert.equal(app.getState().localRemoval.phase, "confirming");
  assert.equal(root.localRemovalModalControls.find(
    (control) => control.dataset.applyLocalRemoval !== undefined,
  ).disabled, true);

  root.shell.dispatch("keydown", { key: "Escape", target: root.ownerDocument.activeElement });
  assert.equal(app.getState().localRemoval.phase, "ready");
  assert.equal(calls.apply.length, 0);

  root.shell.dispatch("click", { target: root.querySelector("[data-prepare-local-removal]") });
  await flushMicrotasks(12);
  const acknowledgement = root.querySelector("[data-local-removal-ack]");
  acknowledgement.checked = true;
  root.shell.dispatch("change", { target: acknowledgement });
  const apply = root.localRemovalModalControls.find(
    (control) => control.dataset.applyLocalRemoval !== undefined,
  );
  assert.equal(apply.disabled, false);
  root.shell.dispatch("click", { target: apply });
  await flushMicrotasks(12);

  assert.deepEqual(calls.apply, [{
    planId: "plan-removal-1",
    planDigest: "digest-removal-1",
    confirmed: true,
  }]);
  assert.equal(app.getState().localRemoval.phase, "applying");
  assert.equal(app.getState().localRemoval.selectionMode, "inactive");
  assert.deepEqual(app.getState().localRemoval.selectedInstanceIds, []);
  assert.equal(root.localRemovalControls.filter(
    (control) => control.dataset.localRemovalSelection,
  ).length, 0);

  await calls.localEventHandler({
    attemptId: "attempt-removal-2",
    sequence: 1,
    stateRevision: 2,
    payload: { state: "complete", snapshotId: "snapshot-removal-2" },
  });
  await flushMicrotasks(16);
  assert.equal(app.getState().localRemoval.phase, "applying");
  assert.deepEqual(calls.reconcile, []);

  applyResponse.resolve({
    sourceGroupOutcomes: [
      { state: "success", memberIds: [items[0].instanceId] },
      { state: "failed_unchanged", memberIds: [items[1].instanceId], code: "changed_since_scan" },
    ],
    rescanState: "accepted",
    rescanAttemptId: "attempt-removal-2",
  });
  await flushMicrotasks(16);

  assert.deepEqual(calls.reconcile, [{
    snapshotId: "snapshot-removal-2",
    expectedAttemptId: "attempt-removal-2",
    instanceIds: [items[0].instanceId],
  }]);
  assert.equal(app.getState().localRemoval.rescan.state, "confirmed");
  assert.deepEqual(app.getState().localRemoval.selectedInstanceIds, []);
  app.destroy();
});

test("Local batch removal unlocks safely when early and in-flight rescans are superseded", async () => {
  const root = createMountRoot({ realisticFocusLifecycle: true });
  const calls = { reconcile: [], localEventHandler: null };
  const items = createLocalInstanceItems("removal-superseded", "Removal superseded", 1);
  const applyResponse = deferred();
  const reconciliationResponse = deferred();
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    onLocalScanChanged(handler) {
      calls.localEventHandler = handler;
      return () => {};
    },
    async getLocalScanState() {
      return {
        state_revision: 1,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-removal-1", state: "complete" },
        latest_complete: {
          snapshot_id: "snapshot-removal-1",
          attempt_id: "attempt-removal-1",
          status: "complete",
        },
        latest_partial: null,
      };
    },
    async queryLocalInstances(request) {
      return {
        snapshotId: request.snapshotId,
        items,
        kindCounts: [{ kind: "skill", count: 1 }],
        counts: { totalInstances: 1, matchedInstances: 1 },
      };
    },
    async prepareLocalRemoval() {
      return {
        planId: "plan-removal-superseded-1",
        planDigest: "digest-removal-superseded-1",
        eligibleMembers: [{
          instanceId: items[0].instanceId,
          displayName: items[0].displayName,
          effect: "dedicated_file",
        }],
        blockedMembers: [],
        sharedConfigEntryRemovalCount: 0,
        dedicatedFileDeletionCount: 1,
        parentFolderDeletionCount: 0,
      };
    },
    async applyLocalRemoval() {
      return applyResponse.promise;
    },
    reconcileLocalRemoval(request) {
      calls.reconcile.push(structuredClone(request));
      return reconciliationResponse.promise;
    },
  });

  await flushMicrotasks(8);
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks(16);
  root.shell.dispatch("click", { target: root.querySelector("[data-enter-local-removal]") });
  await flushMicrotasks(4);
  const checkbox = root.localRemovalControls.find(
    (control) => control.dataset.localRemovalSelection,
  );
  checkbox.checked = true;
  root.shell.dispatch("change", { target: checkbox });
  root.shell.dispatch("click", { target: root.querySelector("[data-prepare-local-removal]") });
  await flushMicrotasks(12);
  const acknowledgement = root.querySelector("[data-local-removal-ack]");
  acknowledgement.checked = true;
  root.shell.dispatch("change", { target: acknowledgement });
  root.shell.dispatch("click", {
    target: root.localRemovalModalControls.find(
      (control) => control.dataset.applyLocalRemoval !== undefined,
    ),
  });
  await flushMicrotasks(12);
  assert.equal(app.getState().localRemoval.phase, "applying");

  await calls.localEventHandler({
    attemptId: "attempt-removal-b",
    sequence: 1,
    stateRevision: 2,
    payload: { state: "complete", snapshotId: "snapshot-removal-b" },
  });
  await calls.localEventHandler({
    attemptId: "attempt-follow-up-c",
    sequence: 2,
    stateRevision: 3,
    payload: { state: "complete", snapshotId: "snapshot-follow-up-c" },
  });
  await flushMicrotasks(16);
  assert.equal(app.getState().localRemoval.phase, "applying");

  applyResponse.resolve({
    sourceGroupOutcomes: [{ state: "success", memberIds: [items[0].instanceId] }],
    rescanState: "accepted",
    rescanAttemptId: "attempt-removal-b",
  });
  await flushMicrotasks(20);

  assert.deepEqual(calls.reconcile, [{
    snapshotId: "snapshot-removal-b",
    expectedAttemptId: "attempt-removal-b",
    instanceIds: [items[0].instanceId],
  }]);
  assert.equal(app.getState().localRemoval.phase, "reconciling");

  await calls.localEventHandler({
    attemptId: "attempt-follow-up-d",
    sequence: 3,
    stateRevision: 4,
    payload: { state: "complete", snapshotId: "snapshot-follow-up-d" },
  });
  await flushMicrotasks(16);

  assert.equal(app.getState().localRemoval.phase, "complete");
  assert.equal(app.getState().localRemoval.rescan.state, "unverified");
  assert.deepEqual(app.getState().localRemoval.selectedInstanceIds, []);
  assert.equal(root.querySelector("[data-enter-local-removal]").disabled, false);
  reconciliationResponse.reject(new Error("newer terminal publication superseded the expected attempt"));
  await flushMicrotasks(12);
  assert.equal(app.getState().localRemoval.phase, "complete");
  app.destroy();
});

test("stale native action response cannot cross an A to B to A selection cycle", async () => {
  const root = createMountRoot();
  let resolveAction;
  const actionResponse = new Promise((resolve) => {
    resolveAction = resolve;
  });
  const item = (instanceId) => ({
    instanceId,
    adapterId: "codex",
    adapterVersion: "1",
    toolId: "codex",
    surfaceId: "skills",
    scope: "user",
    projectId: null,
    safeLocator: `skills/${instanceId}/SKILL.md`,
    kind: "skill",
    name: instanceId,
    parseState: "parsed",
    issueCodes: [],
    correlation: { state: "uncorrelated" },
  });
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async getLocalScanState() {
      return {
        state_revision: 7,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-7", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-7", attempt_id: "attempt-7", status: "complete" },
        latest_partial: null,
      };
    },
    async queryLocalInstances() {
      return {
        snapshotId: "snapshot-7",
        items: [item("instance-a"), item("instance-b")],
        kindCounts: [{ kind: "skill", count: 2 }],
        counts: { totalInstances: 2, matchedInstances: 2 },
      };
    },
    async getLocalInstanceDetail({ instanceId }) {
      return item(instanceId);
    },
    async actOnLocalInstance() {
      return actionResponse;
    },
  });
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  for (let index = 0; index < 12; index += 1) await Promise.resolve();

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-instance]", { localInstance: "instance-a" }),
  });
  for (let index = 0; index < 4; index += 1) await Promise.resolve();
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-action]", {
      snapshotId: "snapshot-7",
      instanceId: "instance-a",
      localAction: "reveal",
    }),
  });
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-instance]", { localInstance: "instance-b" }),
  });
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-instance]", { localInstance: "instance-a" }),
  });

  resolveAction({ outcome: "revealed" });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(app.getState().localView.selectedInstanceId, "instance-a");
  assert.equal(app.getState().localView.actionStatus, null);
});

test("Local native action outcomes and safe failures publish identity-bound accessible status", async () => {
  const root = createMountRoot();
  const outcomes = [
    async () => ({ outcome: "revealed" }),
    async () => ({ outcome: "path_copied" }),
    async () => ({ outcome: "revealed", unexpected: true }),
    async () => { throw { code: "stale_path_handle", safe_message: "private backend detail" }; },
  ];
  const item = {
    instanceId: "instance-action",
    adapterId: "codex",
    adapterVersion: "1",
    toolId: "codex",
    surfaceId: "skills",
    scope: "user",
    projectId: null,
    safeLocator: "skills/action/SKILL.md",
    kind: "skill",
    name: "Action Fixture",
    parseState: "parsed",
    issueCodes: [],
    correlation: { state: "uncorrelated" },
  };
  const app = mountApp(root, {
    async getSotSessionState() {
      return null;
    },
    async getLocalScanState() {
      return {
        state_revision: 9,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-action", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-action", attempt_id: "attempt-action", status: "complete" },
        latest_partial: null,
      };
    },
    async queryLocalInstances() {
      return {
        snapshotId: "snapshot-action",
        items: [item],
        kindCounts: [{ kind: "skill", count: 1 }],
        counts: { totalInstances: 1, matchedInstances: 1 },
      };
    },
    async getLocalInstanceDetail() {
      return item;
    },
    async actOnLocalInstance() {
      return outcomes.shift()();
    },
  });
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-instance]", { localInstance: "instance-action" }),
  });
  for (let index = 0; index < 4; index += 1) await Promise.resolve();

  const action = (localAction) => root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-action]", {
      snapshotId: "snapshot-action",
      instanceId: "instance-action",
      localAction,
    }),
  });

  action("reveal");
  for (let index = 0; index < 4; index += 1) await Promise.resolve();
  assert.deepEqual(app.getState().localView.actionStatus, {
    state: "success",
    code: "revealed",
    message: "Finder에서 위치를 표시했습니다.",
    instanceId: "instance-action",
  });
  assert.equal(root.actionStatus.dataset.localActionCode, "revealed");

  action("copy_path");
  for (let index = 0; index < 4; index += 1) await Promise.resolve();
  assert.deepEqual(app.getState().localView.actionStatus, {
    state: "success",
    code: "path_copied",
    message: "경로를 클립보드에 복사했습니다.",
    instanceId: "instance-action",
  });
  assert.equal(root.actionStatus.dataset.localActionCode, "path_copied");

  action("reveal");
  for (let index = 0; index < 4; index += 1) await Promise.resolve();
  assert.deepEqual(app.getState().localView.actionStatus, {
    state: "error",
    code: "unexpected_action_outcome",
    message: "Local action 결과를 확인하지 못했습니다.",
    instanceId: "instance-action",
  });

  action("reveal");
  for (let index = 0; index < 4; index += 1) await Promise.resolve();
  assert.deepEqual(app.getState().localView.actionStatus, {
    state: "error",
    code: "stale_path_handle",
    message: "항목이 스캔 이후 변경되었습니다. 다시 스캔하세요.",
    instanceId: "instance-action",
  });
  assert.equal(root.actionStatus.dataset.localActionCode, "stale_path_handle");
  assert.doesNotMatch(root.actionStatus.textContent, /private backend detail/);
});

test("Local actions are single-flight and status updates preserve the focused control", async () => {
  const root = createMountRoot();
  const pending = [];
  const calls = [];
  let localEventHandler = null;
  let currentSnapshotId = "snapshot-flight";
  const item = (instanceId) => ({
    instanceId,
    adapterId: "codex",
    adapterVersion: "1",
    toolId: "codex",
    surfaceId: "skills",
    scope: "user",
    projectId: null,
    safeLocator: `skills/${instanceId}/SKILL.md`,
    kind: "skill",
    name: instanceId,
    parseState: "parsed",
    issueCodes: [],
    correlation: { state: "uncorrelated" },
  });
  const app = mountApp(root, {
    async getSotSessionState() { return null; },
    async getLocalScanState() {
      return {
        state_revision: 11,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-flight", state: "complete", error_code: null },
        latest_complete: { snapshot_id: currentSnapshotId, attempt_id: "attempt-flight", status: "complete" },
        latest_partial: null,
      };
    },
    async queryLocalInstances() {
      return {
        snapshotId: "snapshot-flight",
        items: [item("instance-a"), item("instance-b")],
        kindCounts: [{ kind: "skill", count: 2 }],
        counts: { totalInstances: 2, matchedInstances: 2 },
      };
    },
    async getLocalInstanceDetail({ instanceId }) { return item(instanceId); },
    onLocalScanChanged(handler) {
      localEventHandler = handler;
      return () => {};
    },
    actOnLocalInstance(request) {
      calls.push(request);
      return new Promise((resolve) => pending.push(resolve));
    },
  });
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-instance]", { localInstance: "instance-a" }),
  });
  for (let index = 0; index < 4; index += 1) await Promise.resolve();

  const focusedControl = root.localActionItems.find(
    (control) => control.dataset.localAction === "copy_path",
  );
  const statusNode = root.actionStatus;
  const liveRegion = root.liveRegion;
  focusedControl.focus();
  root.shell.dispatch("click", { target: focusedControl });

  assert.equal(calls.length, 1);
  assert.equal(app.getState().ui.localActionBusy, true);
  assert.equal(root.ownerDocument.activeElement, focusedControl);
  assert.equal(root.actionStatus, statusNode);
  assert.equal(liveRegion?.getAttribute("role"), "status");
  assert.equal(liveRegion?.getAttribute("aria-live"), "polite");
  assert.equal(liveRegion?.textContent, "경로를 복사하는 중입니다.");

  await localEventHandler({
    attemptId: "attempt-concurrent",
    sequence: 1,
    stateRevision: 12,
    payload: { state: "running" },
  });
  const restoredControl = root.ownerDocument.activeElement;
  assert.notEqual(restoredControl, focusedControl);
  assert.equal(restoredControl?.dataset.localAction, "copy_path");
  assert.equal(restoredControl?.dataset.instanceId, "instance-a");
  assert.equal(root.liveRegion, liveRegion);
  assert.equal(root.liveRegion?.textContent, "경로를 복사하는 중입니다.");

  root.ownerDocument.activeElement = null;
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-instance]", { localInstance: "instance-b" }),
  });
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-action]", {
      snapshotId: "snapshot-flight",
      instanceId: "instance-b",
      localAction: "copy_path",
    }),
  });
  assert.equal(calls.length, 1);

  pending.shift()({ outcome: "path_copied" });
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  assert.equal(app.getState().ui.localActionBusy, false);

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-action]", {
      snapshotId: "snapshot-flight",
      instanceId: "instance-b",
      localAction: "copy_path",
    }),
  });
  assert.equal(calls.length, 2);
  pending.shift()({ outcome: "path_copied" });
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  assert.equal(app.getState().localView.actionStatus.code, "path_copied");

  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "sot").dispatch("click");
  currentSnapshotId = "snapshot-flight-new";
  await localEventHandler({
    attemptId: "attempt-flight-new",
    sequence: 1,
    stateRevision: 13,
    payload: { state: "complete", snapshotId: currentSnapshotId },
  });
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  assert.equal(app.getState().localView.actionStatus, null);
  assert.equal(root.liveRegion?.textContent, "");
});

test("destroy makes an in-flight Local action inert before the same root is remounted", async () => {
  const root = createMountRoot();
  let resolveAction;
  let resolveSubscription;
  let resolveRestore;
  let unlistenCalls = 0;
  let staleRestoreLoadCalls = 0;
  const item = {
    instanceId: "instance-destroy",
    adapterId: "codex",
    adapterVersion: "1",
    toolId: "codex",
    surfaceId: "skills",
    scope: "user",
    projectId: null,
    safeLocator: "skills/destroy/SKILL.md",
    kind: "skill",
    name: "Destroy Fixture",
    parseState: "parsed",
    issueCodes: [],
    correlation: { state: "uncorrelated" },
  };
  const app = mountApp(root, {
    getSotSessionState() {
      return new Promise((resolve) => { resolveRestore = resolve; });
    },
    onLocalScanChanged() {
      return new Promise((resolve) => { resolveSubscription = resolve; });
    },
    async loadSotSnapshot() {
      staleRestoreLoadCalls += 1;
      return null;
    },
    async getLocalScanState() {
      return {
        state_revision: 21,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-destroy", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-destroy", attempt_id: "attempt-destroy", status: "complete" },
        latest_partial: null,
      };
    },
    async queryLocalInstances() {
      return {
        snapshotId: "snapshot-destroy",
        items: [item],
        kindCounts: [{ kind: "skill", count: 1 }],
        counts: { totalInstances: 1, matchedInstances: 1 },
      };
    },
    async getLocalInstanceDetail() { return item; },
    actOnLocalInstance() {
      return new Promise((resolve) => { resolveAction = resolve; });
    },
  });
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-instance]", { localInstance: "instance-destroy" }),
  });
  for (let index = 0; index < 4; index += 1) await Promise.resolve();
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-action]", {
      snapshotId: "snapshot-destroy",
      instanceId: "instance-destroy",
      localAction: "reveal",
    }),
  });
  assert.equal(app.getState().ui.localActionBusy, true);

  app.destroy();
  const replacement = mountApp(root, {
    async getSotSessionState() { return null; },
  });
  const replacementAnnouncer = root.liveRegion;
  resolveSubscription(() => { unlistenCalls += 1; });
  resolveRestore({
    checkout_id: "stale-checkout",
    canonical_path: "/private/stale-checkout",
  });
  resolveAction({ outcome: "revealed" });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(root.liveRegion, replacementAnnouncer);
  assert.equal(root.liveRegion?.textContent, "");
  assert.equal(replacement.getState().ui.localActionBusy, false);
  assert.equal(unlistenCalls, 1);
  assert.equal(staleRestoreLoadCalls, 0);
  replacement.destroy();
});

test("destroy prevents a delayed Local reconcile from starting scan or query work", async () => {
  const root = createMountRoot();
  let resolveScanState;
  let startCalls = 0;
  let queryCalls = 0;
  const app = mountApp(root, {
    async getSotSessionState() { return null; },
    getLocalScanState() {
      return new Promise((resolve) => { resolveScanState = resolve; });
    },
    async startLocalScan() {
      startCalls += 1;
      return { status: "accepted", attemptId: "late-attempt" };
    },
    async queryLocalInstances() {
      queryCalls += 1;
      return null;
    },
  });
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  root.dashboardItems.find((control) => control.dataset.dashboardSegment === "local").dispatch("click");
  for (let index = 0; index < 4; index += 1) await Promise.resolve();

  app.destroy();
  resolveScanState({
    state_revision: 1,
    current_attempt: null,
    latest_terminal_report: null,
    latest_complete: null,
    latest_partial: null,
  });
  for (let index = 0; index < 10; index += 1) await Promise.resolve();

  assert.equal(startCalls, 0);
  assert.equal(queryCalls, 0);
});

test("SoT install action sends the approved typed request and preserves partial apply truth", async () => {
  const installSnapshot = structuredClone(snapshot);
  installSnapshot.components[0].targets = [{ target_id: "codex", support_status: "runtime_supported" }];
  installSnapshot.components[0].profile_ids = ["harnesskit.profile.engineering"];
  installSnapshot.profiles = [{
    profile_id: "harnesskit.profile.engineering",
    title: "Engineering",
    summary: "Engineering profile",
    component_ids: ["harnesskit.agent.router"],
  }];
  installSnapshot.unprofiled_component_ids = ["harnesskit.skill.beta"];
  const calls = [];
  const root = createMountRoot();
  const app = mountApp(root, {
    async getSotSessionState() {
      return {
        checkout_id: "checkout-install",
        canonical_path: "/tmp/harnesskit",
        snapshot_id: "snapshot-interactions",
        install_evidence_id: "evidence-before-partial-apply",
      };
    },
    async loadSotSnapshot() {
      return structuredClone(installSnapshot);
    },
    async previewInstall(request) {
      calls.push({ preview: request });
      return {
        previewId: "preview-install-1",
        fingerprint: "sha256:install-preview-1",
        sotSnapshotId: request.sotSnapshotId,
        profileId: request.profileId,
        scope: request.scope,
        targetRoot: request.targetRoot,
        targets: request.targetIds,
        components: ["harnesskit.agent.router"],
        artifacts: [{ target: "codex", componentId: "harnesskit.agent.router", destination: ".codex/agents/router.toml" }],
        skippedWrites: [],
        warnings: [],
        runtimeGates: [],
        nonAtomicBoundary: true,
        requiredApprovals: { overwrite: false, runtimeHooks: false },
      };
    },
    async applyInstall(request) {
      calls.push({ apply: request });
      return {
        operationId: "operation-install-1",
        status: "partial",
        destinations: [{
          target: "codex",
          destination: ".codex/agents/router.toml",
          applyState: "changed",
          verifyState: "failed",
          code: "verify_mismatch",
        }],
        installEvidenceId: null,
      };
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  const router = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.agent.router");
  root.shell.dispatch("click", { target: router });

  const form = installForm({
    profileId: "harnesskit.profile.engineering",
    targetId: "codex",
    scope: "project",
    targetRoot: "/tmp/project",
  });
  root.shell.dispatch("change", { target: installFieldTarget(form, "targetRoot") });
  let prevented = false;
  root.shell.dispatch("submit", {
    target: form,
    preventDefault() {
      prevented = true;
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(prevented, true);
  assert.equal(app.getState().install.phase, "preview-ready");
  assert.deepEqual(calls[0], {
    preview: {
      checkoutId: "checkout-install",
      sotSnapshotId: "snapshot-interactions",
      profileId: "harnesskit.profile.engineering",
      scope: "project",
      targetRoot: "/tmp/project",
      targetIds: ["codex"],
    },
  });

  const nonAppearanceBefore = structuredClone({
    ui: app.getState().ui,
    sotView: app.getState().sotView,
    localView: app.getState().localView,
    install: app.getState().install,
  });
  app.applyAppearanceChanged({
    logical_mode: "System",
    resolved_mode: "Light",
    revision: 1,
    persisted: true,
  });
  assert.deepEqual({
    ui: app.getState().ui,
    sotView: app.getState().sotView,
    localView: app.getState().localView,
    install: app.getState().install,
  }, nonAppearanceBefore);

  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(app.getState().install.phase, "result");
  assert.equal(app.getState().install.execution.status, "partial");
  assert.equal(app.getState().sot.installEvidenceId, null);
  assert.equal(app.getState().install.confirmed, false);
  assert.deepEqual(calls[1], {
    apply: {
      previewId: "preview-install-1",
      approvals: {
        confirmed: true,
        semanticFingerprint: "sha256:install-preview-1",
        overwrite: false,
        allowRuntimeHooks: false,
      },
    },
  });
});

test("latest successful install evidence is used by the next SoT to Local projection", async () => {
  const installSnapshot = structuredClone(snapshot);
  installSnapshot.components[0].targets = [{ target_id: "codex", support_status: "runtime_supported" }];
  installSnapshot.components[0].profile_ids = ["harnesskit.profile.engineering"];
  installSnapshot.profiles = [{
    profile_id: "harnesskit.profile.engineering",
    title: "Engineering",
    summary: "Engineering profile",
    component_ids: ["harnesskit.agent.router"],
  }];
  installSnapshot.unprofiled_component_ids = ["harnesskit.skill.beta"];
  const root = createMountRoot();
  const projectionRequests = [];
  const app = mountApp(root, {
    async getSotSessionState() {
      return {
        checkout_id: "checkout-success-evidence",
        canonical_path: "/tmp/harnesskit",
        snapshot_id: "snapshot-interactions",
        install_evidence_id: null,
      };
    },
    async loadSotSnapshot() {
      return structuredClone(installSnapshot);
    },
    async previewInstall(request) {
      return {
        previewId: "preview-success-evidence",
        fingerprint: "sha256:success-evidence",
        sotSnapshotId: request.sotSnapshotId,
        profileId: request.profileId,
        scope: request.scope,
        targetRoot: request.targetRoot,
        targets: request.targetIds,
        components: ["harnesskit.agent.router"],
        artifacts: [{
          target: "codex",
          componentId: "harnesskit.agent.router",
          destination: ".codex/agents/router.toml",
        }],
        skippedWrites: [],
        warnings: [],
        runtimeGates: [],
        nonAtomicBoundary: true,
        requiredApprovals: { overwrite: false, runtimeHooks: false },
      };
    },
    async applyInstall() {
      return {
        operationId: "operation-success-evidence",
        status: "success",
        applyStatus: "applied",
        verifyStatus: "verified",
        destinations: [{
          target: "codex",
          destination: ".codex/agents/router.toml",
          applyState: "changed",
          verifyState: "verified",
          code: null,
        }],
        installEvidenceId: "evidence-from-successful-apply",
      };
    },
    async getLocalScanState() {
      return {
        state_revision: 12,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-success", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-success", attempt_id: "attempt-success", status: "complete" },
        latest_partial: null,
      };
    },
    async getCorrelationProjection(request) {
      projectionRequests.push(structuredClone(request));
      return {
        projectionId: "projection-success",
        localSnapshotId: request.localSnapshotId,
        sotSnapshotId: request.sotSnapshotId,
        installEvidenceId: request.installEvidenceId,
        correlations: [],
      };
    },
    async queryLocalInstances(request) {
      return {
        snapshotId: request.snapshotId,
        items: [],
        kindCounts: [],
        counts: { totalInstances: 0, matchedInstances: 0 },
      };
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  const router = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.agent.router");
  root.shell.dispatch("click", { target: router });
  const form = installForm({
    profileId: "harnesskit.profile.engineering",
    targetId: "codex",
    scope: "user",
    targetRoot: "/tmp/user",
  });
  root.shell.dispatch("change", { target: installFieldTarget(form, "targetRoot") });
  root.shell.dispatch("submit", { target: form, preventDefault() {} });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(app.getState().install.execution.installEvidenceId, "evidence-from-successful-apply");
  assert.equal(app.getState().sot.installEvidenceId, "evidence-from-successful-apply");
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-show-local-install]", { showLocalInstall: "harnesskit.agent.router" }),
  });
  for (let index = 0; index < 16; index += 1) await Promise.resolve();

  assert.deepEqual(projectionRequests.at(-1), {
    localSnapshotId: "snapshot-success",
    sotSnapshotId: "snapshot-interactions",
    installEvidenceId: "evidence-from-successful-apply",
  });
  assert.equal(app.getState().localView.correlationProjectionId, "projection-success");
});

test("stale install preview cannot cross a SoT component selection change", async () => {
  const installSnapshot = structuredClone(snapshot);
  installSnapshot.components.forEach((component) => {
    component.targets = [{ target_id: "codex", support_status: "runtime_supported" }];
    component.profile_ids = ["harnesskit.profile.engineering"];
  });
  installSnapshot.profiles = [{
    profile_id: "harnesskit.profile.engineering",
    title: "Engineering",
    summary: "Engineering profile",
    component_ids: installSnapshot.components.map((component) => component.component_id),
  }];
  installSnapshot.unprofiled_component_ids = [];
  let resolvePreview;
  const response = new Promise((resolve) => {
    resolvePreview = resolve;
  });
  const root = createMountRoot();
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-stale-preview", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      return structuredClone(installSnapshot);
    },
    async previewInstall() {
      return response;
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  const router = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.agent.router");
  const beta = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.skill.beta");
  root.shell.dispatch("click", { target: router });
  const form = installForm({
    profileId: "harnesskit.profile.engineering",
    targetId: "codex",
    scope: "user",
    targetRoot: "/tmp/user",
  });
  root.shell.dispatch("change", { target: installFieldTarget(form, "targetRoot") });
  root.shell.dispatch("submit", { target: form, preventDefault() {} });
  root.shell.dispatch("click", { target: beta });

  resolvePreview({
    previewId: "stale-preview",
    fingerprint: "sha256:stale-preview",
    sotSnapshotId: "snapshot-interactions",
    profileId: "harnesskit.profile.engineering",
    scope: "user",
    targetRoot: "/tmp/user",
    targets: ["codex"],
    components: ["harnesskit.agent.router"],
    artifacts: [],
    skippedWrites: [],
    warnings: [],
    runtimeGates: [],
    nonAtomicBoundary: true,
    requiredApprovals: { overwrite: false, runtimeHooks: false },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();

  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.skill.beta");
  assert.equal(app.getState().install.subject.componentId, "harnesskit.skill.beta");
  assert.equal(app.getState().install.preview, null);
  assert.equal(app.getState().install.confirmed, false);
});

test("Verified cross-panel navigation restores departure and SoT reload invalidates stale Local context", async () => {
  const linkedSnapshot = structuredClone(snapshot);
  linkedSnapshot.components[0].targets = [{ target_id: "codex", support_status: "runtime_supported" }];
  linkedSnapshot.components[0].profile_ids = ["harnesskit.profile.engineering"];
  linkedSnapshot.profiles = [{
    profile_id: "harnesskit.profile.engineering",
    title: "Engineering",
    summary: "Engineering profile",
    component_ids: ["harnesskit.agent.router"],
  }];
  linkedSnapshot.unprofiled_component_ids = ["harnesskit.skill.beta"];
  const queryRequests = [];
  const localItem = {
    instanceId: "instance-router",
    adapterId: "codex-builtin",
    adapterVersion: "1.0.0",
    toolId: "codex",
    surfaceId: "project-agents",
    scope: "project",
    projectId: "project-router",
    safeLocator: ".codex/agents/router.toml",
    kind: "agent",
    name: "Router",
    parseState: "parsed",
    issueCodes: [],
    correlation: { state: "verified", componentId: "harnesskit.agent.router", method: "exact_destination" },
  };
  const root = createMountRoot();
  const projectionRequests = [];
  let resolveStaleProjection;
  const staleProjection = new Promise((resolve) => {
    resolveStaleProjection = resolve;
  });
  const app = mountApp(root, {
    async getSotSessionState() {
      return {
        checkout_id: "checkout-linked",
        canonical_path: "/tmp/harnesskit",
        snapshot_id: "snapshot-interactions",
        install_evidence_id: null,
      };
    },
    async loadSotSnapshot() {
      return structuredClone(linkedSnapshot);
    },
    async previewInstall(request) {
      return {
        previewId: "preview-linked",
        fingerprint: "sha256:linked",
        sotSnapshotId: request.sotSnapshotId,
        profileId: request.profileId,
        scope: request.scope,
        targetRoot: request.targetRoot,
        targets: request.targetIds,
        components: ["harnesskit.agent.router"],
        artifacts: [{
          target: "codex",
          componentId: "harnesskit.agent.router",
          destination: ".codex/agents/router.toml",
        }],
        skippedWrites: [],
        warnings: [],
        runtimeGates: [],
        nonAtomicBoundary: true,
        requiredApprovals: { overwrite: false, runtimeHooks: false },
      };
    },
    async applyInstall() {
      return {
        operationId: "operation-linked",
        status: "success",
        applyStatus: "applied",
        verifyStatus: "verified",
        destinations: [{
          target: "codex",
          destination: ".codex/agents/router.toml",
          applyState: "changed",
          verifyState: "verified",
          code: null,
        }],
        installEvidenceId: "evidence-linked",
      };
    },
    async getLocalScanState() {
      return {
        state_revision: 9,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-linked", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-linked", attempt_id: "attempt-linked", status: "complete" },
        latest_partial: null,
      };
    },
    async getCorrelationProjection(request) {
      projectionRequests.push(structuredClone(request));
      if (projectionRequests.length === 2) return staleProjection;
      return {
        projection_id: "projection-linked",
        local_snapshot_id: "snapshot-linked",
        sot_snapshot_id: "snapshot-interactions",
        install_evidence_id: "evidence-linked",
        correlations: [],
      };
    },
    async queryLocalInstances(request) {
      queryRequests.push(structuredClone(request));
      return {
        snapshotId: "snapshot-linked",
        items: [{
          ...localItem,
          correlation: request.correlationProjectionId
            ? localItem.correlation
            : { state: "uncorrelated" },
        }],
        kindCounts: [{ kind: "agent", count: 1 }],
        counts: { totalInstances: 1, matchedInstances: 1 },
      };
    },
    async getLocalInstanceDetail() {
      return localItem;
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  const router = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.agent.router");
  root.shell.dispatch("click", { target: router });
  const form = installForm({
    profileId: "harnesskit.profile.engineering",
    targetId: "codex",
    scope: "user",
    targetRoot: "/tmp/user",
  });
  root.shell.dispatch("change", { target: installFieldTarget(form, "targetRoot") });
  root.shell.dispatch("submit", { target: form, preventDefault() {} });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  assert.equal(app.getState().sot.installEvidenceId, "evidence-linked");
  root.shell.dispatch("input", { target: { id: "sot-tree-filter", value: "router" } });
  root.workbench.scrollTop = 333;
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-show-local-install]", { showLocalInstall: "harnesskit.agent.router" }),
  });
  for (let index = 0; index < 16; index += 1) await Promise.resolve();

  assert.equal(app.getState().ui.activeSegment, "local");
  assert.equal(app.getState().localView.correlationProjectionId, "projection-linked");
  assert.equal(app.getState().localView.verifiedComponentId, "harnesskit.agent.router");
  assert.deepEqual(projectionRequests.at(-1), {
    localSnapshotId: "snapshot-linked",
    sotSnapshotId: "snapshot-interactions",
    installEvidenceId: "evidence-linked",
  });
  assert.equal(app.getState().localData.correlationProjection.installEvidenceId, "evidence-linked");
  assert.equal(queryRequests.at(-1).verifiedComponentId, "harnesskit.agent.router");

  root.cloneControl.dispatch("click");
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  assert.equal(app.getState().repo.checkoutId, "checkout-linked");
  assert.equal(app.getState().repo.canonicalPath, "/tmp/harnesskit");
  assert.equal(app.getState().sot.snapshot.snapshot_id, "snapshot-interactions");
  assert.equal(app.getState().sot.installEvidenceId, "evidence-linked");
  assert.equal(app.getState().localView.correlationProjectionId, "projection-linked");

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-instance]", { localInstance: "instance-router" }),
  });
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-open-sot-component]", { openSotComponent: "harnesskit.agent.router" }),
  });

  assert.equal(app.getState().ui.activeSegment, "sot");
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.agent.router");
  assert.equal(app.getState().sotView.filter, "router");
  assert.equal(root.workbench.scrollTop, 333);

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-show-local-install]", { showLocalInstall: "harnesskit.agent.router" }),
  });
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  assert.equal(projectionRequests.length, 2);

  root.dashboardItems.find((tab) => tab.dataset.dashboardSegment === "sot").dispatch("click");
  assert.equal(app.getState().ui.activeSegment, "sot");
  root.querySelector("#dashboard-refresh").dispatch("click");
  assert.equal(app.getState().localData.queryResult, null);
  assert.equal(app.getState().localData.selectedDetail, null);
  assert.equal(app.getState().localData.correlationProjection, null);
  assert.equal(app.getState().localView.correlationProjectionId, null);
  assert.equal(app.getState().localView.verifiedComponentId, null);

  resolveStaleProjection({
    projection_id: "projection-stale-after-reload",
    local_snapshot_id: "snapshot-linked",
    sot_snapshot_id: "snapshot-interactions",
    install_evidence_id: "evidence-linked",
    correlations: [],
  });
  for (let index = 0; index < 16; index += 1) await Promise.resolve();

  assert.equal(app.getState().sot.installEvidenceId, null);
  assert.equal(app.getState().localData.correlationProjection, null);
  assert.equal(app.getState().localView.correlationProjectionId, null);
  assert.equal(app.getState().localView.verifiedComponentId, null);

  root.dashboardItems.find((tab) => tab.dataset.dashboardSegment === "local").dispatch("click");
  await flushMicrotasks(16);
  assert.equal(queryRequests.at(-1).correlationProjectionId, null);
  assert.equal(queryRequests.at(-1).verifiedComponentId, null);
  assert.equal(app.getState().localData.queryResult.items[0].correlation.state, "uncorrelated");
});

test("a replacement Local snapshot clears stale correlation immediately and reprojects current evidence", async () => {
  const linkedSnapshot = structuredClone(snapshot);
  linkedSnapshot.components[0].targets = [{ target_id: "codex", support_status: "runtime_supported" }];
  linkedSnapshot.components[0].profile_ids = ["harnesskit.profile.engineering"];
  linkedSnapshot.profiles = [{
    profile_id: "harnesskit.profile.engineering",
    title: "Engineering",
    summary: "Engineering profile",
    component_ids: ["harnesskit.agent.router"],
  }];
  linkedSnapshot.unprofiled_component_ids = ["harnesskit.skill.beta"];
  const root = createMountRoot();
  const projectionRequests = [];
  let localSnapshotId = "snapshot-before-rescan";
  let scanChanged;
  const app = mountApp(root, {
    async getSotSessionState() {
      return { checkout_id: "checkout-rescan", canonical_path: "/tmp/harnesskit" };
    },
    async loadSotSnapshot() {
      return structuredClone(linkedSnapshot);
    },
    async previewInstall(request) {
      return {
        previewId: "preview-rescan",
        fingerprint: "sha256:rescan",
        sotSnapshotId: request.sotSnapshotId,
        profileId: request.profileId,
        scope: request.scope,
        targetRoot: request.targetRoot,
        targets: request.targetIds,
        components: ["harnesskit.agent.router"],
        artifacts: [{
          target: "codex",
          componentId: "harnesskit.agent.router",
          destination: ".codex/agents/router.toml",
        }],
        skippedWrites: [],
        warnings: [],
        runtimeGates: [],
        nonAtomicBoundary: true,
        requiredApprovals: { overwrite: false, runtimeHooks: false },
      };
    },
    async applyInstall() {
      return {
        operationId: "operation-rescan",
        status: "success",
        applyStatus: "applied",
        verifyStatus: "verified",
        destinations: [{
          target: "codex",
          destination: ".codex/agents/router.toml",
          applyState: "changed",
          verifyState: "verified",
          code: null,
        }],
        installEvidenceId: "evidence-rescan",
      };
    },
    async getLocalScanState() {
      return {
        state_revision: localSnapshotId === "snapshot-before-rescan" ? 20 : 22,
        current_attempt: null,
        latest_terminal_report: {
          attempt_id: localSnapshotId === "snapshot-before-rescan" ? "attempt-before" : "attempt-after",
          state: "complete",
          error_code: null,
        },
        latest_complete: {
          snapshot_id: localSnapshotId,
          attempt_id: localSnapshotId === "snapshot-before-rescan" ? "attempt-before" : "attempt-after",
          status: "complete",
        },
        latest_partial: null,
      };
    },
    async onLocalScanChanged(handler) {
      scanChanged = handler;
      return () => {};
    },
    async getCorrelationProjection(request) {
      projectionRequests.push(structuredClone(request));
      return {
        projectionId: `projection-${request.localSnapshotId}`,
        localSnapshotId: request.localSnapshotId,
        sotSnapshotId: request.sotSnapshotId,
        installEvidenceId: request.installEvidenceId,
        correlations: [],
      };
    },
    async queryLocalInstances(request) {
      return {
        snapshotId: request.snapshotId,
        items: [],
        kindCounts: [],
        counts: { totalInstances: 0, matchedInstances: 0 },
      };
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  const router = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.agent.router");
  root.shell.dispatch("click", { target: router });
  const form = installForm({
    profileId: "harnesskit.profile.engineering",
    targetId: "codex",
    scope: "user",
    targetRoot: "/tmp/user",
  });
  root.shell.dispatch("change", { target: installFieldTarget(form, "targetRoot") });
  root.shell.dispatch("submit", { target: form, preventDefault() {} });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-show-local-install]", { showLocalInstall: "harnesskit.agent.router" }),
  });
  for (let index = 0; index < 16; index += 1) await Promise.resolve();
  assert.equal(app.getState().localView.correlationProjectionId, "projection-snapshot-before-rescan");

  localSnapshotId = "snapshot-after-rescan";
  const replacement = scanChanged({
    attemptId: "attempt-after",
    sequence: 2,
    stateRevision: 22,
    payload: { state: "complete", snapshotId: localSnapshotId },
  });
  assert.equal(app.getState().localData.correlationProjection, null);
  assert.equal(app.getState().localView.correlationProjectionId, null);
  assert.equal(app.getState().localView.verifiedComponentId, null);
  await replacement;
  for (let index = 0; index < 16; index += 1) await Promise.resolve();

  assert.deepEqual(projectionRequests.at(-1), {
    localSnapshotId: "snapshot-after-rescan",
    sotSnapshotId: "snapshot-interactions",
    installEvidenceId: "evidence-rescan",
  });
  assert.equal(app.getState().localView.correlationProjectionId, "projection-snapshot-after-rescan");
  assert.equal(app.getState().localView.verifiedComponentId, "harnesskit.agent.router");
});

test("SoT to Local navigation without install evidence stays in the general Local context", async () => {
  const root = createMountRoot();
  let projectionCalls = 0;
  const queryRequests = [];
  const app = mountApp(root, {
    async getSotSessionState() {
      return {
        checkout_id: "checkout-without-evidence",
        canonical_path: "/tmp/harnesskit",
        snapshot_id: "snapshot-interactions",
        install_evidence_id: null,
      };
    },
    async loadSotSnapshot() {
      return structuredClone(snapshot);
    },
    async getLocalScanState() {
      return {
        state_revision: 10,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-general", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-general", attempt_id: "attempt-general", status: "complete" },
        latest_partial: null,
      };
    },
    async getCorrelationProjection() {
      projectionCalls += 1;
      return null;
    },
    async queryLocalInstances(request) {
      queryRequests.push(structuredClone(request));
      return {
        snapshotId: request.snapshotId,
        items: [],
        kindCounts: [],
        counts: { totalInstances: 0, matchedInstances: 0 },
      };
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  const router = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.agent.router");
  root.shell.dispatch("click", { target: router });
  root.shell.dispatch("input", { target: { id: "sot-tree-filter", value: "router" } });
  root.workbench.scrollTop = 271;

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-show-local-install]", { showLocalInstall: "harnesskit.agent.router" }),
  });
  for (let index = 0; index < 16; index += 1) await Promise.resolve();

  assert.equal(app.getState().ui.activeSegment, "local");
  assert.equal(app.getState().ui.sotDeparture, null);
  assert.equal(app.getState().localView.correlationProjectionId, null);
  assert.equal(app.getState().localView.verifiedComponentId, null);
  assert.equal(app.getState().localData.correlationProjection, null);
  assert.equal(projectionCalls, 0);
  assert.equal(queryRequests.at(-1).verifiedComponentId, null);

  root.dashboardItems.find((item) => item.dataset.dashboardSegment === "sot").dispatch("click");
  assert.equal(app.getState().sotView.selectedComponentId, "harnesskit.agent.router");
  assert.equal(app.getState().sotView.filter, "router");
  assert.equal(root.workbench.scrollTop, 271);
});

test("a correlation response with mismatched install evidence fails closed", async () => {
  const mismatchSnapshot = structuredClone(snapshot);
  mismatchSnapshot.components[0].targets = [{ target_id: "codex", support_status: "runtime_supported" }];
  mismatchSnapshot.components[0].profile_ids = ["harnesskit.profile.engineering"];
  mismatchSnapshot.profiles = [{
    profile_id: "harnesskit.profile.engineering",
    title: "Engineering",
    summary: "Engineering profile",
    component_ids: ["harnesskit.agent.router"],
  }];
  mismatchSnapshot.unprofiled_component_ids = ["harnesskit.skill.beta"];
  const root = createMountRoot();
  const projectionRequests = [];
  const app = mountApp(root, {
    async getSotSessionState() {
      return {
        checkout_id: "checkout-mismatched-evidence",
        canonical_path: "/tmp/harnesskit",
        snapshot_id: "snapshot-interactions",
        install_evidence_id: null,
      };
    },
    async loadSotSnapshot() {
      return structuredClone(mismatchSnapshot);
    },
    async previewInstall(request) {
      return {
        previewId: "preview-mismatch",
        fingerprint: "sha256:mismatch",
        sotSnapshotId: request.sotSnapshotId,
        profileId: request.profileId,
        scope: request.scope,
        targetRoot: request.targetRoot,
        targets: request.targetIds,
        components: ["harnesskit.agent.router"],
        artifacts: [{
          target: "codex",
          componentId: "harnesskit.agent.router",
          destination: ".codex/agents/router.toml",
        }],
        skippedWrites: [],
        warnings: [],
        runtimeGates: [],
        nonAtomicBoundary: true,
        requiredApprovals: { overwrite: false, runtimeHooks: false },
      };
    },
    async applyInstall() {
      return {
        operationId: "operation-mismatch",
        status: "success",
        applyStatus: "applied",
        verifyStatus: "verified",
        destinations: [{
          target: "codex",
          destination: ".codex/agents/router.toml",
          applyState: "changed",
          verifyState: "verified",
          code: null,
        }],
        installEvidenceId: "evidence-requested",
      };
    },
    async getLocalScanState() {
      return {
        state_revision: 11,
        current_attempt: null,
        latest_terminal_report: { attempt_id: "attempt-mismatch", state: "complete", error_code: null },
        latest_complete: { snapshot_id: "snapshot-mismatch", attempt_id: "attempt-mismatch", status: "complete" },
        latest_partial: null,
      };
    },
    async getCorrelationProjection(request) {
      projectionRequests.push(structuredClone(request));
      return {
        projectionId: "projection-mismatch",
        localSnapshotId: "snapshot-mismatch",
        sotSnapshotId: "snapshot-interactions",
        installEvidenceId: "evidence-other",
        correlations: [],
      };
    },
    async queryLocalInstances(request) {
      return {
        snapshotId: request.snapshotId,
        items: [],
        kindCounts: [],
        counts: { totalInstances: 0, matchedInstances: 0 },
      };
    },
  });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  const router = root.treeItems.find((item) => item.dataset.componentId === "harnesskit.agent.router");
  root.shell.dispatch("click", { target: router });
  const form = installForm({
    profileId: "harnesskit.profile.engineering",
    targetId: "codex",
    scope: "user",
    targetRoot: "/tmp/user",
  });
  root.shell.dispatch("change", { target: installFieldTarget(form, "targetRoot") });
  root.shell.dispatch("submit", { target: form, preventDefault() {} });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  root.shell.dispatch("change", { target: approvalTarget("confirmed", true) });
  root.shell.dispatch("click", { target: delegatedTarget("#apply-install", {}) });
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  assert.equal(app.getState().sot.installEvidenceId, "evidence-requested");
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-show-local-install]", { showLocalInstall: "harnesskit.agent.router" }),
  });
  for (let index = 0; index < 16; index += 1) await Promise.resolve();

  assert.deepEqual(projectionRequests, [{
    localSnapshotId: "snapshot-mismatch",
    sotSnapshotId: "snapshot-interactions",
    installEvidenceId: "evidence-requested",
  }]);
  assert.equal(app.getState().localView.correlationProjectionId, null);
  assert.equal(app.getState().localView.verifiedComponentId, null);
  assert.equal(app.getState().localData.correlationProjection, null);
  assert.match(app.getState().localData.message, /구성하지 못했습니다/);
});

test("mount loads the active AI provider exactly once across Local renders", async () => {
  const { app, controls, root } = await mountConfiguredLocalAiFixture();

  assert.equal(controls.providerConfigCalls, 1);
  assert.equal(app.getState().aiProvider.providerRevision, "provider-ai-1");

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-kind]", { localKind: "skill" }),
  });
  await flushMicrotasks(12);
  assert.equal(controls.providerConfigCalls, 1);
});

test("AI provider settings open and close without leaking Base URL into the inspector", async () => {
  const { root } = await mountConfiguredLocalAiFixture();

  assert.match(root.markup, /data-open-ai-settings/);
  assert.doesNotMatch(root.markup, /https:\/\/provider\.example\/v1/);
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-open-ai-settings]", {}),
  });

  assert.match(root.markup, /data-ai-provider-form/);
  assert.match(root.markup, /https:\/\/provider\.example\/v1/);
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-ai-provider-close]", {}),
  });

  assert.doesNotMatch(root.markup, /data-ai-provider-form/);
  assert.doesNotMatch(root.markup, /https:\/\/provider\.example\/v1/);
});

test("non-loopback HTTP warns beside AI explanation and in settings without adding save, request, or confirmation gates", async () => {
  const baseUrl = aiEndpoint("http", "192.168.1.18:8080");
  const replacementKey = ["replacement", "key"].join("-");
  const { app, controls, root } = await mountConfiguredLocalAiFixture({
    initialAiProvider: { ...configuredAiProvider(), base_url: baseUrl },
    saveAiProviderConfig: () => ({ ...configuredAiProvider("provider-ai-2"), base_url: baseUrl }),
  });
  try {
    assert.equal(app.getState().aiProvider.transportWarning, "unencrypted_non_loopback");
    assert.match(root.markup, /<button(?=[^>]*data-ai-explain)(?=[^>]*aria-describedby="ai-explain-transport-warning")[^>]*>AI 설명 생성 · gpt-4\.1-mini<\/button>/);
    assert.match(root.markup, /<p(?=[^>]*id="ai-explain-transport-warning")(?=[^>]*data-transport-warning="unencrypted_non_loopback")[^>]*>API key[^<]*전체 원문[^<]*암호화되지 않은/);
    assert.ok(root.markup.indexOf('id="ai-explain-transport-warning"') > root.markup.indexOf("data-ai-explain"));
    assert.doesNotMatch(root.markup, /192\.168\.1\.18|role="alertdialog"/);
    assert.equal((root.markup.match(/data-ai-explain\b/g) ?? []).length, 1);
    assert.equal(controls.explainCalls.length, 0);
    assert.equal(controls.saveCalls.length, 0);

    const shellRenderCount = root.renderCount;
    root.shell.dispatch("click", { target: delegatedTarget("[data-ai-explain]", {}) });
    await flushMicrotasks(12);
    assert.equal(controls.explainCalls.length, 1);
    assert.equal(app.getState().aiExplanation.phase, "ready");
    assert.equal(root.renderCount, shellRenderCount, "explanation updates must patch the inspector without rebuilding the shell");
    const inspector = root.querySelector("[data-local-inspector]").innerHTML;
    assert.equal((inspector.match(/data-transport-warning="unencrypted_non_loopback"/g) ?? []).length, 1);
    assert.match(inspector, /data-ai-explain[^>]*aria-describedby="ai-explain-transport-warning"/);
    assert.match(root.markup, /data-transport-warning="unencrypted_non_loopback"/);

    root.shell.dispatch("click", { target: delegatedTarget("[data-open-ai-settings]", {}) });
    assert.match(root.markup, /data-ai-provider-transport-warning="unencrypted_non_loopback"/);
    assert.match(root.markup, /API key[^<]*전체 원문[^<]*암호화되지 않은/);
    const { form, fields } = aiProviderForm({ baseUrl, model: "gpt-4.1-mini", apiKey: replacementKey });
    root.shell.dispatch("submit", { target: form, preventDefault() {} });
    assert.equal(fields.apiKey.value, "");
    assert.deepEqual(controls.saveCalls, [{
      expectedProviderRevision: "provider-ai-1",
      baseUrl,
      model: "gpt-4.1-mini",
      apiKey: replacementKey,
    }]);
    await flushMicrotasks(12);
    assert.equal(app.getState().aiProvider.providerRevision, "provider-ai-2");
    assert.equal(controls.explainCalls.length, 1);
    assert.doesNotMatch(root.markup, /replacement-key|role="alertdialog"/);

    root.shell.dispatch("click", { target: delegatedTarget("[data-ai-provider-close]", {}) });
    root.shell.dispatch("click", { target: delegatedTarget("[data-ai-explain]", {}) });
    await flushMicrotasks(12);
    assert.equal(controls.explainCalls.length, 2);
    assert.equal(controls.explainCalls[1].providerRevision, "provider-ai-2");
    assert.match(root.markup, /data-transport-warning="unencrypted_non_loopback"/);
    assert.doesNotMatch(root.markup, /192\.168\.1\.18|role="alertdialog"/);
  } finally {
    app.destroy();
  }
});

test("HTTPS and loopback HTTP never show transport warnings at the action or in settings", async () => {
  for (const baseUrl of [
    "https://provider.example/v1",
    "http://localhost:11434/v1",
    aiEndpoint("http", "127.9.8.7:11434"),
    "http://[::1]:11434/v1",
  ]) {
    const { app, controls, root } = await mountConfiguredLocalAiFixture({
      initialAiProvider: { ...configuredAiProvider(), base_url: baseUrl },
    });
    try {
      assert.equal(app.getState().aiProvider.transportWarning, null, baseUrl);
      assert.doesNotMatch(root.markup, /unencrypted_non_loopback|암호화되지 않은/, baseUrl);
      root.shell.dispatch("click", { target: delegatedTarget("[data-open-ai-settings]", {}) });
      assert.doesNotMatch(root.markup, /unencrypted_non_loopback|암호화되지 않은/, baseUrl);
      root.shell.dispatch("click", { target: delegatedTarget("[data-ai-provider-close]", {}) });
      root.shell.dispatch("click", { target: delegatedTarget("[data-ai-explain]", {}) });
      await flushMicrotasks(12);
      assert.equal(controls.explainCalls.length, 1, baseUrl);
      assert.doesNotMatch(root.markup, /unencrypted_non_loopback|암호화되지 않은|role="alertdialog"/, baseUrl);
    } finally {
      app.destroy();
    }
  }
});

test("settings transport warning follows an unsaved URL edit without rerendering or clearing the key", async () => {
  const { app, controls, root } = await mountConfiguredLocalAiFixture({ realisticFocusLifecycle: true });
  try {
    root.shell.dispatch("click", { target: root.querySelector("[data-open-ai-settings]") });
    const input = root.querySelector('[data-ai-provider-form] input[name="baseUrl"]');
    const key = root.aiModalControls.find((control) => control.name === "apiKey");
    const warning = root.querySelector("[data-ai-provider-transport-warning]");
    assert.ok(warning, "settings must include a warning slot for unsaved URL edits");
    assert.equal(warning.hidden, true);
    key.value = "unsaved-secret";
    input.focus();
    const renderCount = root.renderCount;

    input.value = aiEndpoint("http", "provider.example");
    root.shell.dispatch("input", { target: input });
    assert.equal(warning.hidden, false);
    assert.equal(warning.dataset.aiProviderTransportWarning, "unencrypted_non_loopback");
    assert.match(warning.textContent, /API key[^<]*전체 원문[^<]*암호화되지 않은/);

    for (const baseUrl of ["http://127.0.0.1:11434/v1", "https://provider.example/v1"]) {
      input.value = baseUrl;
      root.shell.dispatch("input", { target: input });
      assert.equal(warning.hidden, true, baseUrl);
      assert.equal(warning.textContent, "", baseUrl);
    }
    assert.equal(root.renderCount, renderCount);
    assert.equal(root.ownerDocument.activeElement, input);
    assert.equal(key.value, "unsaved-secret");
    assert.equal(controls.saveCalls.length, 0);
    assert.equal(controls.explainCalls.length, 0);
  } finally {
    app.destroy();
  }
});

test("provider save sends the CAS tuple, clears key input immediately, and delete uses the new revision", async () => {
  const savePending = deferred();
  const { controls, root } = await mountConfiguredLocalAiFixture({
    saveAiProviderConfig: () => savePending.promise,
  });
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-open-ai-settings]", {}),
  });
  const { form, fields } = aiProviderForm({
    baseUrl: "http://127.0.0.1:11434/v1",
    model: "qwen3:8b",
    apiKey: "replace-" + "ment-key",
  });
  let prevented = false;

  root.shell.dispatch("submit", {
    target: form,
    preventDefault() {
      prevented = true;
    },
  });

  assert.equal(prevented, true);
  assert.equal(fields.apiKey.value, "");
  assert.deepEqual(controls.saveCalls, [{
    expectedProviderRevision: "provider-ai-1",
    baseUrl: "http://127.0.0.1:11434/v1",
    model: "qwen3:8b",
    apiKey: "replace-" + "ment-key",
  }]);

  savePending.resolve({
    state: "configured",
    base_url: "http://127.0.0.1:11434/v1",
    model: "qwen3:8b",
    api_key_present: true,
    provider_revision: "provider-ai-2",
  });
  await flushMicrotasks(12);
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-ai-provider-key-delete]", {}),
  });
  await flushMicrotasks(12);

  assert.deepEqual(controls.deleteCalls, ["provider-ai-2"]);
});

test("only AI explain click sends the bound tuple and every identity disposal clears its card", async () => {
  const { app, controls, root } = await mountConfiguredLocalAiFixture();
  const providerUrl = "https://provider.example/v1";

  assert.equal(controls.explainCalls.length, 0);
  assert.equal(app.getState().sourcePreview.phase, "ready");
  assert.match(root.markup, /data-ai-explain/);
  assert.match(root.markup, /AI 설명 생성 · gpt-4\.1-mini/);
  assert.doesNotMatch(root.markup, new RegExp(providerUrl.replaceAll(".", "\\.")));

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-ai-explain]", {}),
  });
  await flushMicrotasks(12);
  assert.deepEqual(controls.explainCalls, [{
    snapshotId: "snapshot-ai-mount-1",
    instanceId: "instance-ai-a",
    sourceRevision: "source-snapshot-ai-mount-1-instance-ai-a",
    providerRevision: "provider-ai-1",
  }]);
  assert.equal(app.getState().aiExplanation.phase, "ready");
  assert.match(root.markup, /AI 생성 설명 · runtime 검증 아님/);
  assert.doesNotMatch(root.markup, new RegExp(providerUrl.replaceAll(".", "\\.")));

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-local-instance]", { localInstance: "instance-ai-b" }),
  });
  await flushMicrotasks(16);
  assert.equal(app.getState().aiExplanation.result, null);
  assert.doesNotMatch(root.markup, /AI 생성 설명 · runtime 검증 아님/);

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-ai-explain]", {}),
  });
  await flushMicrotasks(12);
  assert.equal(app.getState().aiExplanation.phase, "ready");

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-open-ai-settings]", {}),
  });
  const { form, fields } = aiProviderForm({
    baseUrl: providerUrl,
    model: "gpt-4.1-mini",
    apiKey: "",
  });
  root.shell.dispatch("submit", { target: form, preventDefault() {} });
  assert.equal(fields.apiKey.value, "");
  await flushMicrotasks(12);
  assert.equal(app.getState().aiExplanation.result, null);

  root.shell.dispatch("click", {
    target: delegatedTarget("[data-ai-provider-close]", {}),
  });
  root.shell.dispatch("click", {
    target: delegatedTarget("[data-ai-explain]", {}),
  });
  await flushMicrotasks(12);
  assert.deepEqual(controls.explainCalls.at(-1), {
    snapshotId: "snapshot-ai-mount-1",
    instanceId: "instance-ai-b",
    sourceRevision: "source-snapshot-ai-mount-1-instance-ai-b",
    providerRevision: "provider-ai-2",
  });
  assert.equal(app.getState().aiExplanation.phase, "ready");

  await controls.replaceSnapshot("snapshot-ai-mount-2");
  assert.equal(app.getState().aiExplanation.result, null);
  assert.doesNotMatch(root.markup, /AI 생성 설명 · runtime 검증 아님/);
});

test("AI provider modal contains forward and reverse Tab focus and restores its logical opener", async () => {
  const { app, root } = await mountConfiguredLocalAiFixture({
    realisticFocusLifecycle: true,
  });
  try {
    const opener = root.querySelector("[data-open-ai-settings]");
    assert.ok(opener);
    opener.focus();
    root.shell.dispatch("click", { target: opener });

    const dialog = root.querySelector("[data-ai-provider-dialog]");
    assert.ok(dialog);
    assert.equal(dialog.contains(root.ownerDocument.activeElement), true);
    const focusable = root.aiModalControls.filter((control) => !control.disabled);
    assert.ok(focusable.length >= 2);

    const last = focusable.at(-1);
    last.focus();
    root.shell.dispatch("keydown", { key: "Tab", shiftKey: false, target: last });
    assert.equal(
      dialog.contains(root.ownerDocument.activeElement),
      true,
      "forward Tab must not reach the obscured dashboard",
    );

    const first = focusable[0];
    first.focus();
    root.shell.dispatch("keydown", { key: "Tab", shiftKey: true, target: first });
    assert.equal(
      dialog.contains(root.ownerDocument.activeElement),
      true,
      "reverse Tab must not reach the obscured dashboard",
    );

    root.shell.dispatch("keydown", {
      key: "Escape",
      target: root.ownerDocument.activeElement,
    });
    assert.equal(root.querySelector("[data-ai-provider-dialog]"), null);
    assert.equal(
      root.ownerDocument.activeElement?.dataset.openAiSettings,
      "",
      "closing the modal must restore the logical opener",
    );
  } finally {
    app.destroy();
  }
});

test("AI explanation request retains a focusable aria-disabled logical trigger across render", async () => {
  const pending = deferred();
  const { app, controls, root } = await mountConfiguredLocalAiFixture({
    realisticFocusLifecycle: true,
    explainLocalSource: () => pending.promise,
  });
  try {
    const explain = root.querySelector("[data-ai-explain]");
    assert.ok(explain);
    explain.focus();
    root.shell.dispatch("click", { target: explain });

    assertPendingAiControlFocus(root, "[data-ai-explain]", "AI explanation trigger");
  } finally {
    const request = controls.explainCalls[0] ?? {
      snapshotId: "snapshot-ai-mount-1",
      instanceId: "instance-ai-a",
      sourceRevision: "source-snapshot-ai-mount-1-instance-ai-a",
      providerRevision: "provider-ai-1",
    };
    pending.resolve(configuredAiExplanation(request));
    await flushMicrotasks(12);
    app.destroy();
  }
});

test("non-loopback HTTP warning stays beside the focused explanation action while pending", async () => {
  const pending = deferred();
  const { app, controls, root } = await mountConfiguredLocalAiFixture({
    initialAiProvider: { ...configuredAiProvider(), base_url: aiEndpoint("http", "192.168.1.18:8080") },
    realisticFocusLifecycle: true,
    explainLocalSource: () => pending.promise,
  });
  try {
    const explain = root.querySelector("[data-ai-explain]");
    explain.focus();
    root.shell.dispatch("click", { target: explain });

    assertPendingAiControlFocus(root, "[data-ai-explain]", "warned AI explanation trigger");
    assert.equal(controls.explainCalls.length, 1);
    assert.match(root.markup, /data-transport-warning="unencrypted_non_loopback"/);
  } finally {
    pending.resolve(configuredAiExplanation(controls.explainCalls[0]));
    await flushMicrotasks(12);
    app.destroy();
  }
});

test("AI provider save retains a focusable aria-disabled submit control across render", async () => {
  const pending = deferred();
  const { app, root } = await mountConfiguredLocalAiFixture({
    realisticFocusLifecycle: true,
    saveAiProviderConfig: () => pending.promise,
  });
  try {
    root.shell.dispatch("click", {
      target: root.querySelector("[data-open-ai-settings]"),
    });
    const form = root.querySelector("[data-ai-provider-form]");
    form.elements.namedItem("baseUrl").value = "http://127.0.0.1:11434/v1";
    form.elements.namedItem("model").value = "qwen3:8b";
    form.elements.namedItem("apiKey").value = "replacement-key";
    const submit = root.querySelector('[data-ai-provider-form] button[type="submit"]');
    submit.focus();
    root.shell.dispatch("submit", { target: form });

    assertPendingAiControlFocus(
      root,
      '[data-ai-provider-form] button[type="submit"]',
      "AI provider save control",
    );
  } finally {
    pending.resolve(configuredAiProvider("provider-ai-2", true));
    await flushMicrotasks(12);
    app.destroy();
  }
});

test("AI provider key deletion retains a focusable aria-disabled logical trigger across render", async () => {
  const pending = deferred();
  const { app, root } = await mountConfiguredLocalAiFixture({
    realisticFocusLifecycle: true,
    deleteAiProviderKey: () => pending.promise,
  });
  try {
    root.shell.dispatch("click", {
      target: root.querySelector("[data-open-ai-settings]"),
    });
    const deleteKey = root.querySelector("[data-ai-provider-key-delete]");
    assert.ok(deleteKey);
    deleteKey.focus();
    root.shell.dispatch("click", { target: deleteKey });

    assertPendingAiControlFocus(
      root,
      "[data-ai-provider-key-delete]",
      "AI provider key delete control",
    );
  } finally {
    pending.resolve(configuredAiProvider("provider-ai-2", false));
    await flushMicrotasks(12);
    app.destroy();
  }
});

test("one persistent AI status announcer reports pending, ready, and error outcomes", async () => {
  const first = deferred();
  let attempt = 0;
  const { app, controls, root } = await mountConfiguredLocalAiFixture({
    explainLocalSource() {
      attempt += 1;
      if (attempt === 1) return first.promise;
      return Promise.reject(Object.assign(new Error("private provider detail"), {
        code: "provider_request_failed",
      }));
    },
  });
  try {
    root.shell.dispatch("click", {
      target: root.querySelector("[data-ai-explain]"),
    });
    const announcer = root.aiStatusAnnouncer;
    assert.ok(announcer, "AI status announcer must be mounted outside render replacement");
    assert.equal(announcer.getAttribute("role"), "status");
    assert.equal(announcer.getAttribute("aria-live"), "polite");
    assert.equal(announcer.textContent, "AI 설명을 생성하고 있습니다.");

    first.resolve(configuredAiExplanation(controls.explainCalls[0]));
    await flushMicrotasks(12);
    assert.equal(root.aiStatusAnnouncer, announcer);
    assert.equal(announcer.textContent, "AI 설명을 생성했습니다.");

    root.shell.dispatch("click", {
      target: root.querySelector("[data-ai-explain]"),
    });
    await flushMicrotasks(12);
    assert.equal(root.aiStatusAnnouncer, announcer);
    assert.equal(announcer.textContent, "AI provider에 연결하지 못했습니다.");
  } finally {
    const request = controls.explainCalls[0];
    if (request) first.resolve(configuredAiExplanation(request));
    await flushMicrotasks(12);
    app.destroy();
  }
});
