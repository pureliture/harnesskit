import assert from "node:assert/strict";
import test from "node:test";

import {
  createSotViewState,
  reduceSotView,
  renderComponentMap,
  renderProfileMatrix,
  renderSotInspector,
  renderSotTree,
} from "../sot-view.js";

const snapshot = Object.freeze({
  snapshot_id: "snapshot-7",
  checkout_summary: {
    source_revision: "abc123",
    branch: "main",
    detached: false,
    dirty: false,
    recent_commits: ["Add fixture"],
  },
  components: [
    { component_id: "harnesskit.agent.router", kind: "agent", status: "draft", title: "Router", summary: "Routes work", domain: "core", targets: [], provenance: {}, owned_files: [], profile_ids: ["harnesskit.profile.engineering"] },
    { component_id: "harnesskit.skill.alpha", kind: "skill", status: "draft", title: "Alpha", summary: "Alpha skill", domain: "core", targets: [{ target_id: "codex", support_status: "runtime_supported" }], provenance: { mode: "adapted" }, owned_files: ["components/skills/alpha/SKILL.md"], profile_ids: ["harnesskit.profile.engineering", "harnesskit.profile.work"] },
    { component_id: "harnesskit.skill.beta", kind: "skill", status: "draft", title: "Beta", summary: null, domain: "work", targets: [], provenance: {}, owned_files: [], profile_ids: ["harnesskit.profile.work"] },
    { component_id: "harnesskit.skill.unprofiled", kind: "skill", status: "planned", title: "Unprofiled", summary: null, domain: null, targets: [], provenance: {}, owned_files: [], profile_ids: [] },
  ],
  profiles: [
    { profile_id: "harnesskit.profile.engineering", status: "draft", title: "Engineering", summary: "Engineering profile", component_ids: ["harnesskit.agent.router", "harnesskit.skill.alpha"] },
    { profile_id: "harnesskit.profile.work", status: "draft", title: "Work", summary: "Work profile", component_ids: ["harnesskit.skill.alpha", "harnesskit.skill.beta"] },
  ],
  workflows: [],
  unprofiled_component_ids: ["harnesskit.skill.unprofiled"],
  relations: [
    { source: "harnesskit.agent.router", target: "harnesskit.skill.alpha", relation_type: "router_workflow", source_path: "components/agents/router/agent.yml", source_field: "routes.workflows[0].component_id", declarative_only: true },
  ],
  graph_projection: {
    schema_version: 1,
    snapshot_id: "snapshot-7",
    layout_seed: "seed-7",
    nodes: [
      { node_type: "component", node_id: "component:harnesskit.agent.router", component_id: "harnesskit.agent.router", kind: "agent", domain: "core", relation_degree: 1, profile_ids: ["harnesskit.profile.engineering"], workflow_ids: [], shell: "center" },
      { node_type: "component", node_id: "component:harnesskit.skill.alpha", component_id: "harnesskit.skill.alpha", kind: "skill", domain: "core", relation_degree: 1, profile_ids: ["harnesskit.profile.engineering", "harnesskit.profile.work"], workflow_ids: [], shell: "center" },
      { node_type: "component", node_id: "component:harnesskit.skill.beta", component_id: "harnesskit.skill.beta", kind: "skill", domain: "work", relation_degree: 0, profile_ids: ["harnesskit.profile.work"], workflow_ids: [], shell: "center" },
      { node_type: "component", node_id: "component:harnesskit.skill.unprofiled", component_id: "harnesskit.skill.unprofiled", kind: "skill", domain: "unknown", relation_degree: 0, profile_ids: [], workflow_ids: [], shell: "center" },
    ],
    links: [{ semantic: "component-cross-link", link_id: "relation-0", source_component_id: "harnesskit.agent.router", target_component_id: "harnesskit.skill.alpha", relation_type: "router_workflow", provenance: { source_path: "components/agents/router/agent.yml", source_field: "routes.workflows[0].component_id", declarative_only: true } }],
  },
  navigation_projection: { all_component_ids: [], groups: [] },
  issues: [],
});

test("profile and component selection stay independent and never mutate the final graph DTO", () => {
  const initial = createSotViewState(snapshot);
  const projectionNodes = structuredClone(snapshot.graph_projection.nodes);
  const profileSelected = reduceSotView(initial, {
    type: "select_profile",
    profileId: "harnesskit.profile.work",
  }, snapshot);
  const componentSelected = reduceSotView(profileSelected, {
    type: "select_component",
    componentId: "harnesskit.skill.alpha",
  }, snapshot);
  const treeSelected = reduceSotView(componentSelected, {
    type: "select_tree_component",
    componentId: "harnesskit.skill.alpha",
  }, snapshot);
  const graphSelected = reduceSotView(componentSelected, {
    type: "select_graph_node",
    componentId: "harnesskit.agent.router",
  }, snapshot);

  assert.equal(profileSelected.activeProfileId, "harnesskit.profile.work");
  assert.equal(profileSelected.selectedComponentId, initial.selectedComponentId);
  assert.equal(componentSelected.activeProfileId, "harnesskit.profile.work");
  assert.equal(componentSelected.selectedComponentId, "harnesskit.skill.alpha");
  assert.equal(treeSelected.treeSelectedId, "harnesskit.skill.alpha");
  assert.equal(treeSelected.selectedComponentId, "harnesskit.skill.alpha");
  assert.equal(graphSelected.activeProfileId, "harnesskit.profile.work");
  assert.equal(graphSelected.selectedComponentId, "harnesskit.agent.router");
  assert.equal(graphSelected.treeSelectedId, componentSelected.treeSelectedId);
  assert.deepEqual(snapshot.graph_projection.nodes, projectionNodes);
});

test("SoT view leaves camera pose inside the persistent renderer session", () => {
  const initial = createSotViewState(snapshot);
  const transformed = reduceSotView(initial, {
    type: "set_graph_transform",
    transform: { scale: 8, x: -120, y: 48 },
  }, snapshot);

  assert.equal("graphTransform" in initial, false);
  assert.equal(transformed, initial);
});

test("Component activation records the exact session-local graph focus identity", () => {
  const initial = createSotViewState(snapshot);

  const activated = reduceSotView(initial, {
    type: "select_component",
    componentId: "harnesskit.skill.alpha",
  }, snapshot);

  assert.equal(activated.graphPresentation.focusNodeId, "component:harnesskit.skill.alpha");
});

test("graph focus follows activation sources, survives same-projection transitions, and clears at terminal boundaries", () => {
  const initial = createSotViewState(snapshot);
  const profileActivated = reduceSotView(initial, {
    type: "select_profile",
    profileId: "harnesskit.profile.work",
  }, snapshot);
  const componentActivated = reduceSotView(profileActivated, {
    type: "select_tree_component",
    componentId: "harnesskit.skill.alpha",
  }, snapshot);
  const semantic = reduceSotView(componentActivated, { type: "show_graph_semantic" }, snapshot);
  const hover = reduceSotView(semantic, {
    type: "hover_graph_node",
    nodeId: "component:harnesskit.agent.router",
  }, snapshot);
  const cleared = reduceSotView(hover, { type: "clear_graph_selection" }, snapshot);
  const nextProjection = createSotViewState({
    ...snapshot,
    snapshot_id: "snapshot-8",
  }, componentActivated);

  assert.equal(profileActivated.graphPresentation.focusNodeId, "profile:harnesskit.profile.work");
  assert.equal(componentActivated.graphPresentation.focusNodeId, "component:harnesskit.skill.alpha");
  assert.equal(semantic.graphPresentation.focusNodeId, "component:harnesskit.skill.alpha");
  assert.equal(hover.graphPresentation.focusNodeId, "component:harnesskit.skill.alpha");
  assert.equal(cleared.graphPresentation.focusNodeId, null);
  assert.equal(nextProjection.graphPresentation.focusNodeId, null);
});

test("Workflow step activation preserves only its parent Workflow focus and never creates focus", () => {
  const typedSnapshot = m2cProjectionSnapshot();
  const initial = createSotViewState(typedSnapshot);
  const componentActivated = reduceSotView(initial, {
    type: "select_component",
    componentId: "harnesskit.skill.alpha",
  }, typedSnapshot);
  const semantic = reduceSotView(componentActivated, {
    type: "show_graph_semantic",
  }, typedSnapshot);
  const activateStep = (state) => reduceSotView(state, {
    type: "select_workflow_step",
    workflowId: "harnesskit.workflow.review",
    ordinal: 1,
  }, typedSnapshot);
  const parentFocused = {
    ...semantic,
    graphPresentation: {
      ...semantic.graphPresentation,
      focusNodeId: "workflow:harnesskit.workflow.review",
    },
  };
  const otherRelationFocused = {
    ...semantic,
    graphPresentation: {
      ...semantic.graphPresentation,
      focusNodeId: "profile:harnesskit.profile.work",
    },
  };

  assert.equal(componentActivated.graphPresentation.focusNodeId, "component:harnesskit.skill.alpha");
  assert.equal(activateStep(parentFocused).graphPresentation.focusNodeId, "workflow:harnesskit.workflow.review");
  assert.equal(activateStep(semantic).graphPresentation.focusNodeId, null);
  assert.equal(activateStep(otherRelationFocused).graphPresentation.focusNodeId, null);
  assert.equal(activateStep(initial).graphPresentation.focusNodeId, null);
});

test("graph presentation separates a manual semantic return from renderer failure recovery", () => {
  const initial = createSotViewState(snapshot);
  const semantic = reduceSotView(initial, { type: "show_graph_semantic" }, snapshot);
  const returning = reduceSotView(semantic, { type: "return_graph_three_d" }, snapshot);
  const failed = reduceSotView(returning, {
    type: "graph_renderer_failed",
    failure: "webglcontextlost",
  }, snapshot);

  assert.deepEqual(initial.graphPresentation, {
    viewMode: "three_d",
    rendererLifecycle: "creating",
    failure: null,
    focusNodeId: null,
  });
  assert.deepEqual(semantic.graphPresentation, {
    viewMode: "semantic",
    rendererLifecycle: "absent",
    failure: null,
    focusNodeId: null,
  });
  assert.deepEqual(returning.graphPresentation, {
    viewMode: "three_d",
    rendererLifecycle: "creating",
    failure: null,
    focusNodeId: null,
  });
  assert.deepEqual(failed.graphPresentation, {
    viewMode: "semantic",
    rendererLifecycle: "failed",
    failure: "webglcontextlost",
    focusNodeId: null,
  });
});

test("SoT left tree keeps independent unfiltered and filtered scroll offsets", () => {
  const initial = createSotViewState(snapshot);
  const collapsed = reduceSotView(initial, {
    type: "toggle_tree_node",
    treeNodeId: "components",
  }, snapshot);
  const selected = reduceSotView(collapsed, {
    type: "select_tree_node",
    treeNodeId: "registry.yml",
  }, snapshot);
  const unfilteredScrolled = reduceSotView(selected, {
    type: "set_tree_scroll",
    scrollTop: 147,
  }, snapshot);
  const componentSelected = reduceSotView(unfilteredScrolled, {
    type: "select_component",
    componentId: "harnesskit.skill.alpha",
  }, snapshot);
  const filtered = reduceSotView(componentSelected, {
    type: "set_filter",
    filter: "alpha",
  }, snapshot);
  const filteredScrolled = reduceSotView(filtered, {
    type: "set_tree_scroll",
    scrollTop: 23,
  }, snapshot);
  const cleared = reduceSotView(filteredScrolled, {
    type: "set_filter",
    filter: "",
  }, snapshot);

  assert.deepEqual(initial.treeExpandedIds, ["root", "components", "profiles"]);
  assert.equal(initial.treeSelectedId, null);
  assert.equal(initial.unfilteredTreeScrollTop, 0);
  assert.equal(initial.filteredTreeScrollTop, 0);
  assert.deepEqual(collapsed.treeExpandedIds, ["root", "profiles"]);
  assert.equal(collapsed.selectedComponentId, null);
  assert.equal(selected.treeSelectedId, "registry.yml");
  assert.equal(selected.selectedComponentId, null);
  assert.equal(unfilteredScrolled.unfilteredTreeScrollTop, 147);
  assert.equal(unfilteredScrolled.filteredTreeScrollTop, 0);
  assert.equal(componentSelected.unfilteredTreeScrollTop, 147);
  assert.equal(filtered.unfilteredTreeScrollTop, 147);
  assert.equal(filtered.filteredTreeScrollTop, 0);
  assert.equal(filteredScrolled.unfilteredTreeScrollTop, 147);
  assert.equal(filteredScrolled.filteredTreeScrollTop, 23);
  assert.equal(cleared.unfilteredTreeScrollTop, 147);
  assert.equal(cleared.filteredTreeScrollTop, 23);
});

test("SoT tree filter reveals matching ancestry without mutating persisted expansion", () => {
  const initial = createSotViewState(snapshot, {
    treeExpandedIds: ["root", "profiles"],
    filter: "alpha",
  });
  const markup = renderSotTree(snapshot, initial);

  assert.deepEqual(initial.treeExpandedIds, ["root", "profiles"]);
  assert.match(markup, /aria-expanded="true"[^>]*data-sot-tree-node="components"/);
  assert.match(markup, /data-component-id="harnesskit\.skill\.alpha"/);
  assert.doesNotMatch(markup, /data-component-id="harnesskit\.skill\.beta"/);
});

test("component map renders a WebGL host with a hidden viewport identity overlay", () => {
  const typedSnapshot = m2cProjectionSnapshot();
  const markup = renderComponentMap(typedSnapshot, {
    ...createSotViewState(typedSnapshot),
    activeProfileId: "harnesskit.profile.engineering",
    selectedComponentId: "harnesskit.skill.alpha",
  });

  assert.match(markup, /class="component-map component-map--three"/);
  assert.match(markup, /8 nodes · 8 links/);
  assert.match(markup, /id="component-map-viewport"[^>]*aria-label="Component Map 3D viewport"/);
  assert.match(markup, /id="component-map-viewport"[^>]*data-component-map-viewport/);
  assert.match(markup, /class="component-map-scene-host"[^>]*data-component-map-scene-host[^>]*aria-hidden="true"><\/div>/);
  assert.equal((markup.match(/data-graph-relation-label-root/g) ?? []).length, 1);
  assert.match(markup, /class="component-map-relation-label-root"[^>]*data-graph-relation-label-root[^>]*aria-hidden="true"><\/div>/);
  assert.equal((markup.match(/data-graph-identity-overlay/g) ?? []).length, 1);
  assert.match(
    markup,
    /<div\b(?=[^>]*\bid="component-map-identity-overlay")(?=[^>]*\bclass="[^"]*\bcomponent-map-identity-overlay\b[^"]*")(?=[^>]*\bdata-graph-identity-overlay)(?=[^>]*\brole="status")(?=[^>]*\baria-live="polite")(?=[^>]*\baria-atomic="true")(?=[^>]*\bhidden)[^>]*>/,
  );
  assert.match(markup, /id="component-map-identity-title"[^>]*data-graph-identity-title>\s*<\/strong>/);
  assert.match(markup, /id="component-map-identity-kind"[^>]*class="[^"]*\bcomponent-kind-badge\b[^"]*"[^>]*data-graph-identity-kind>\s*<\/span>/);
  assert.match(markup, /id="component-map-identity-count"[^>]*data-graph-identity-count>\s*<\/span>/);
  assert.doesNotMatch(markup, /data-graph-identity-rail|data-graph-identity-id|data-graph-identity-meta/);
  const viewportStart = markup.indexOf('id="component-map-viewport"');
  const overlayStart = markup.indexOf("data-graph-identity-overlay");
  const semanticStart = markup.indexOf("data-component-map-semantic-host");
  assert.ok(viewportStart >= 0 && viewportStart < overlayStart && overlayStart < semanticStart);
  assert.match(markup, /id="component-map-renderer-status"[^>]*data-graph-renderer-state[^>]*role="status"[^>]*hidden/);
  assert.equal((markup.match(/data-semantic-graph-fallback/g) ?? []).length, 1);
  assert.match(markup, /data-component-map-semantic-host[^>]*data-semantic-graph-fallback[^>]*data-renderer-availability="pending"><\/div>/);
  assert.doesNotMatch(markup, /data-component-map-semantic-host[^>]*\shidden(?:\s|>)/);
  assert.doesNotMatch(markup, /data-graph-node=|data-relation-kind=|data-component-id=/);
  assert.equal((markup.match(/data-graph-zoom=/g) ?? []).length, 3);
  assert.match(markup, /data-graph-scale[^>]*aria-live="polite"/);
  assert.match(markup, /data-sot-graph-toggle[^>]*aria-expanded="true"[^>]*aria-controls="component-map-body"/);
  assert.match(markup, /⌃ 그래프 접기/);
  assert.match(markup, /id="component-map-body"[^>]*data-component-map-body[^>]*role="group"[^>]*aria-label="Component Map 본문"/);
  assert.match(markup, /id="component-map-viewport"[^>]*aria-description="snapshot snapshot-7 · source abc123"/);
  assert.doesNotMatch(markup, /<svg\b|viewBox=|data-graph-content-|data-node-width=|data-node-height=/);
  assert.doesNotMatch(markup, /<canvas\b/);
  assert.doesNotMatch(markup, /\sstyle=/i);
  assert.doesNotMatch(markup, /https?:\/\//);
});

test("component kind metadata stays in the DTO while static markup emits no inline component titles", () => {
  const kinds = [
    "agent",
    "command",
    "composite",
    "hook",
    "mode",
    "rule",
    "skill",
    "workflow",
    "future-kind",
  ];
  const components = kinds.map((kind, index) => ({
    component_id: `harnesskit.${kind}.kind-${index}`,
    kind,
    status: "draft",
    title: `Visible title must stay off canvas ${index}`,
    summary: null,
    domain: "fixture",
    targets: [],
    provenance: {},
    owned_files: [],
    profile_ids: [],
  }));
  const kindSnapshot = {
    ...snapshot,
    components,
    profiles: [],
    unprofiled_component_ids: components.map((component) => component.component_id),
    relations: [],
    graph_projection: {
      schema_version: 1,
      snapshot_id: "snapshot-kinds",
      layout_seed: "seed-kinds",
      nodes: [
        {
          node_type: "relation",
          node_id: "unprofiled:__unprofiled__",
          relation_kind: "unprofiled",
          canonical_id: "__unprofiled__",
          name: "Unprofiled",
          exact_count: components.length,
          shell: "profile",
          anchor_ordinal: 1,
          size_scale: 1.2,
        },
        ...components.map((component) => ({
          node_type: "component",
          node_id: `component:${component.component_id}`,
          component_id: component.component_id,
          kind: component.kind,
          domain: component.domain,
          relation_degree: 1,
          profile_ids: [],
          workflow_ids: [],
          shell: "center",
        })),
      ],
      links: components.map((component) => ({
        semantic: "profile-membership",
        link_id: `profile-membership:__unprofiled__:${component.component_id}`,
        profile_node_id: "unprofiled:__unprofiled__",
        component_node_id: `component:${component.component_id}`,
        provenance: "DerivedUnprofiled",
      })),
    },
  };

  const markup = renderComponentMap(kindSnapshot, createSotViewState(kindSnapshot));

  const viewport = markup.slice(
    markup.indexOf('id="component-map-viewport"'),
    markup.indexOf('data-semantic-graph-fallback'),
  );
  assert.match(viewport, /data-component-map-scene-host[^>]*><\/div>/);
  assert.deepEqual(
    kindSnapshot.graph_projection.nodes
      .filter((node) => node.node_type === "component")
      .map((node) => node.kind),
    kinds,
  );
  assert.doesNotMatch(viewport, /Visible title must stay off canvas/);
  assert.doesNotMatch(markup, /Visible title must stay off canvas/);
  assert.doesNotMatch(markup, /<svg\b|class="node-label"|class="node-kind-mark"/);
});

function m2cProjectionSnapshot() {
  const workflowId = "harnesskit.workflow.review";
  const workflow = {
    workflow_id: workflowId,
    title: "Review",
    description: "Review in authored order",
    runtime_implemented: false,
    source_path: "components/workflows/review/workflow.yml",
    raw_yaml: "kind: workflow\nsteps:\n  - id: route\n  - id: review\n",
    steps: [
      {
        workflow_id: workflowId,
        ordinal: 1,
        step_id: "route",
        title: "Route",
        description: "Route the work",
        authored_fields: { agent: "harnesskit.agent.router" },
        resolved_component_ids: ["harnesskit.agent.router"],
        unresolved_references: [],
        source_path: "components/workflows/review/workflow.yml",
      },
      {
        workflow_id: workflowId,
        ordinal: 2,
        step_id: "review",
        title: "Review",
        description: "Review the result",
        authored_fields: { skill: "harnesskit.skill.alpha" },
        resolved_component_ids: ["harnesskit.skill.alpha"],
        unresolved_references: [],
        source_path: "components/workflows/review/workflow.yml",
      },
    ],
  };
  return {
    ...snapshot,
    workflows: [workflow],
    graph_projection: {
      schema_version: 1,
      snapshot_id: snapshot.snapshot_id,
      layout_seed: "seed-m2c",
      nodes: [
        {
          node_type: "relation",
          node_id: "profile:harnesskit.profile.engineering",
          relation_kind: "profile",
          canonical_id: "harnesskit.profile.engineering",
          name: "Engineering",
          exact_count: 2,
          shell: "profile",
          anchor_ordinal: 1,
          size_scale: 1.2,
        },
        {
          node_type: "relation",
          node_id: "profile:harnesskit.profile.work",
          relation_kind: "profile",
          canonical_id: "harnesskit.profile.work",
          name: "Work",
          exact_count: 2,
          shell: "profile",
          anchor_ordinal: 2,
          size_scale: 1.2,
        },
        {
          node_type: "relation",
          node_id: "unprofiled:__unprofiled__",
          relation_kind: "unprofiled",
          canonical_id: "__unprofiled__",
          name: "Unprofiled",
          exact_count: 1,
          shell: "profile",
          anchor_ordinal: 3,
          size_scale: 1,
        },
        {
          node_type: "relation",
          node_id: `workflow:${workflowId}`,
          relation_kind: "workflow",
          canonical_id: workflowId,
          name: "Review",
          exact_count: 2,
          shell: "workflow",
          anchor_ordinal: 1,
          size_scale: 1.2,
        },
        ...snapshot.components.map((component) => ({
          node_type: "component",
          node_id: `component:${component.component_id}`,
          component_id: component.component_id,
          kind: component.kind,
          domain: component.domain ?? "unknown",
          relation_degree: component.component_id === "harnesskit.agent.router"
            || component.component_id === "harnesskit.skill.alpha" ? 1 : 0,
          profile_ids: component.profile_ids,
          workflow_ids: ["harnesskit.agent.router", "harnesskit.skill.alpha"].includes(component.component_id)
            ? [workflowId]
            : [],
          shell: "center",
        })),
      ],
      links: [
        {
          semantic: "profile-membership",
          link_id: "profile-membership:harnesskit.profile.engineering:harnesskit.agent.router",
          profile_node_id: "profile:harnesskit.profile.engineering",
          component_node_id: "component:harnesskit.agent.router",
          provenance: "CanonicalProfile",
        },
        {
          semantic: "profile-membership",
          link_id: "profile-membership:harnesskit.profile.engineering:harnesskit.skill.alpha",
          profile_node_id: "profile:harnesskit.profile.engineering",
          component_node_id: "component:harnesskit.skill.alpha",
          provenance: "CanonicalProfile",
        },
        {
          semantic: "profile-membership",
          link_id: "profile-membership:harnesskit.profile.work:harnesskit.skill.alpha",
          profile_node_id: "profile:harnesskit.profile.work",
          component_node_id: "component:harnesskit.skill.alpha",
          provenance: "CanonicalProfile",
        },
        {
          semantic: "profile-membership",
          link_id: "profile-membership:harnesskit.profile.work:harnesskit.skill.beta",
          profile_node_id: "profile:harnesskit.profile.work",
          component_node_id: "component:harnesskit.skill.beta",
          provenance: "CanonicalProfile",
        },
        {
          semantic: "profile-membership",
          link_id: "profile-membership:__unprofiled__:harnesskit.skill.unprofiled",
          profile_node_id: "unprofiled:__unprofiled__",
          component_node_id: "component:harnesskit.skill.unprofiled",
          provenance: "DerivedUnprofiled",
        },
        {
          semantic: "workflow-step",
          link_id: `workflow-step:${workflowId}:harnesskit.agent.router`,
          workflow_node_id: `workflow:${workflowId}`,
          component_node_id: "component:harnesskit.agent.router",
          occurrences: [{ ordinal: 1, step_id: "route", source_field: "agent" }],
        },
        {
          semantic: "workflow-step",
          link_id: `workflow-step:${workflowId}:harnesskit.skill.alpha`,
          workflow_node_id: `workflow:${workflowId}`,
          component_node_id: "component:harnesskit.skill.alpha",
          occurrences: [{ ordinal: 2, step_id: "review", source_field: "skill" }],
        },
        {
          semantic: "component-cross-link",
          link_id: "component-cross-link:harnesskit.agent.router:harnesskit.skill.alpha",
          source_component_id: "harnesskit.agent.router",
          target_component_id: "harnesskit.skill.alpha",
          relation_type: "router_workflow",
          provenance: {
            source_path: "components/agents/router/agent.yml",
            source_field: "routes.workflows[0].component_id",
            declarative_only: true,
          },
        },
      ],
    },
  };
}

test("final graph DTO partitions unique Profile and Workflow relations from Component entities", () => {
  const typedSnapshot = m2cProjectionSnapshot();
  const { nodes, links } = typedSnapshot.graph_projection;
  const relationNodes = nodes.filter((node) => node.node_type === "relation");
  const componentNodes = nodes.filter((node) => node.node_type === "component");
  const nodeById = new Map(nodes.map((node) => [node.node_id, node]));

  assert.equal(relationNodes.length, 4);
  assert.equal(componentNodes.length, 4);
  assert.equal(new Set(nodes.map((node) => node.node_id)).size, nodes.length);
  assert.equal(new Set(componentNodes.map((node) => node.component_id)).size, componentNodes.length);
  assert.equal(new Set(relationNodes.map((node) => node.canonical_id)).size, relationNodes.length);
  assert.deepEqual(
    relationNodes.map((node) => node.relation_kind).sort(),
    ["profile", "profile", "unprofiled", "workflow"],
  );
  assert.equal(new Set(links.map((link) => link.link_id)).size, links.length);
  assert.equal(links.filter((link) => link.semantic === "profile-membership").length, 5);
  assert.equal(links.filter((link) => link.semantic === "workflow-step").length, 2);
  assert.equal(links.filter((link) => link.semantic === "component-cross-link").length, 1);

  for (const link of links.filter((candidate) => candidate.semantic === "profile-membership")) {
    assert.ok(["profile", "unprofiled"].includes(nodeById.get(link.profile_node_id)?.relation_kind));
    assert.equal(nodeById.get(link.component_node_id)?.node_type, "component");
  }
  for (const link of links.filter((candidate) => candidate.semantic === "workflow-step")) {
    assert.equal(nodeById.get(link.workflow_node_id)?.relation_kind, "workflow");
    assert.equal(nodeById.get(link.component_node_id)?.node_type, "component");
  }
  assert.equal(
    links.some((link) => link.profile_node_id && link.workflow_node_id),
    false,
    "Profile and Workflow relations must never be directly connected",
  );
});

test("component map exposes one renderer-owned semantic fallback surface", () => {
  const typedSnapshot = m2cProjectionSnapshot();
  const markup = renderComponentMap(typedSnapshot, createSotViewState(typedSnapshot));

  assert.equal((markup.match(/data-component-map-semantic-host/g) ?? []).length, 1);
  assert.equal((markup.match(/data-semantic-graph-fallback/g) ?? []).length, 1);
  assert.match(
    markup,
    /class="component-map-semantic-host"[^>]*data-component-map-semantic-host[^>]*data-semantic-graph-fallback[^>]*data-renderer-availability="pending"><\/div>/,
  );
  assert.doesNotMatch(markup, /data-component-map-semantic-host[^>]*\shidden(?:\s|>)/);
  assert.doesNotMatch(markup, /<details class="component-semantic-list"|data-semantic-node-id=/);
});

test("legacy 2D geometry fields cannot leak into the final WebGL host markup", () => {
  const typedSnapshot = m2cProjectionSnapshot();
  typedSnapshot.graph_projection.view_box = [0, 0, 1200, 460];
  typedSnapshot.graph_projection.nodes[0] = {
    ...typedSnapshot.graph_projection.nodes[0],
    x: 140,
    y: 80,
    width: 216,
    height: 96,
  };

  const markup = renderComponentMap(typedSnapshot, createSotViewState(typedSnapshot));

  assert.match(markup, /data-component-map-scene-host/);
  assert.doesNotMatch(markup, /viewBox=|data-node-width=|data-node-height=|translate\(|<svg\b/);
});

test("expanded component map places one compact HUD over the full-bleed body", () => {
  const typedSnapshot = m2cProjectionSnapshot();
  const markup = renderComponentMap(typedSnapshot, createSotViewState(typedSnapshot));
  const toolbar = markup.match(/<div class="component-map-toolbar"[^>]*>[\s\S]*?<\/div>/)?.[0];
  const hudStart = markup.indexOf('class="component-map-hud"');
  const bodyStart = markup.indexOf('id="component-map-body"');

  assert.ok(toolbar, "expanded graph must expose one compact toolbar");
  assert.ok(hudStart >= 0 && hudStart < bodyStart, "floating HUD must precede the full-bleed body");
  assert.equal((markup.match(/class="component-map-hud"/g) ?? []).length, 1);
  assert.equal((markup.match(/data-sot-graph-toggle/g) ?? []).length, 1);
  assert.match(
    markup,
    /<div\b(?=[^>]*\bclass="component-map-hud")(?=[^>]*\bdata-component-map-hud)[^>]*>[\s\S]*?id="component-map-title"[\s\S]*?id="component-map-toolbar"[\s\S]*?<\/div>\s*<div\b(?=[^>]*\bid="component-map-body")/,
  );
  assert.match(toolbar, /class="component-map-key"[^>]*role="group"[\s\S]*class="graph-zoom-controls"/);
  assert.match(toolbar, /aria-label="Component Map 카메라"/);
  assert.equal((toolbar.match(/class="graph-key-item/g) ?? []).length, 5);
  assert.equal((toolbar.match(/class="graph-key-label/g) ?? []).length, 5);
  for (const label of [
    "Profile relation",
    "Workflow relation",
    "Profile membership",
    "Ordered workflow step",
    "Component kind",
  ]) {
    assert.ok(
      toolbar.includes(`<span class="graph-key-label">${label}</span>`),
      `${label} must stay in the compact legend`,
    );
  }
  assert.equal((toolbar.match(/data-graph-zoom=/g) ?? []).length, 3);
  assert.match(
    toolbar,
    /<button\b(?=[^>]*\bdata-graph-semantic-view)(?=[^>]*\bhidden)(?=[^>]*\bdisabled)(?=[^>]*\baria-label="Component Map 텍스트 보기")[^>]*>텍스트 보기<\/button>/,
  );
  assert.match(toolbar, /data-graph-scale[^>]*aria-live="polite">3D · 준비<\/output>/);
  assert.equal((markup.match(/class="component-map-toolbar"/g) ?? []).length, 1);
});

test("packaged AX collection can identify Component Map groups without production injection", () => {
  const typedSnapshot = m2cProjectionSnapshot();
  const markup = renderComponentMap(typedSnapshot, createSotViewState(typedSnapshot));

  assert.match(
    markup,
    /<div\b(?=[^>]*\bid="component-map-toolbar")(?=[^>]*\bclass="component-map-toolbar")(?=[^>]*\brole="group")(?=[^>]*\baria-label="Component Map 범례와 카메라 제어")[^>]*>/,
  );
  assert.match(
    markup,
    /<span\b(?=[^>]*\bid="component-map-legend")(?=[^>]*\bclass="component-map-key")(?=[^>]*\brole="group")(?=[^>]*\baria-label="Component Map 시각 범례")[^>]*>/,
  );
  assert.match(
    markup,
    /<div\b(?=[^>]*\bid="component-map-camera-controls")(?=[^>]*\bclass="graph-zoom-controls")(?=[^>]*\brole="group")(?=[^>]*\baria-label="Component Map 카메라")[^>]*>/,
  );
  assert.match(markup, /<output\b(?=[^>]*\bid="component-map-camera-scale")(?=[^>]*\bdata-graph-scale)(?=[^>]*\baria-live="polite")[^>]*>/);
  assert.match(
    markup,
    /<div\b(?=[^>]*\bid="component-map-identity-overlay")(?=[^>]*\bclass="[^"]*\bcomponent-map-identity-overlay\b[^"]*")(?=[^>]*\bdata-graph-identity-overlay)(?=[^>]*\brole="status")(?=[^>]*\baria-live="polite")(?=[^>]*\baria-atomic="true")(?=[^>]*\bhidden)[^>]*>/,
  );
  assert.match(markup, /id="component-map-identity-title"[^>]*data-graph-identity-title/);
  assert.match(markup, /data-graph-identity-kind/);
  assert.match(markup, /data-graph-identity-count/);
  assert.doesNotMatch(markup, /data-graph-identity-id|data-graph-identity-meta/);
  assert.match(markup, /id="component-map-renderer-status"[^>]*data-graph-renderer-state/);
  assert.match(
    markup,
    /<div\b(?=[^>]*\bid="component-map-viewport")(?=[^>]*\brole="group")(?=[^>]*\baria-label="Component Map 3D viewport")[^>]*>/,
  );
});

test("Component titles stay out of the fixed 3D host and hidden identity overlay markup", () => {
  const typedSnapshot = m2cProjectionSnapshot();
  const titledSnapshot = {
    ...typedSnapshot,
    components: typedSnapshot.components.map((component, index) => ({
      ...component,
      title: `AX component inline title ${index}`,
    })),
  };
  const titledMarkup = renderComponentMap(titledSnapshot, createSotViewState(titledSnapshot));
  const viewport = titledMarkup.slice(
    titledMarkup.indexOf('id="component-map-viewport"'),
    titledMarkup.indexOf('data-component-map-semantic-host'),
  );

  assert.match(viewport, /data-component-map-scene-host[^>]*><\/div>/);
  assert.match(viewport, /data-graph-identity-overlay[^>]*hidden/);
  assert.match(viewport, /data-graph-identity-title>\s*<\/strong>/);
  assert.doesNotMatch(titledMarkup, /data-graph-identity-rail|Node를 가리키거나 선택하세요\./);
  for (const component of titledSnapshot.components) {
    assert.equal(
      titledMarkup.includes(component.title),
      false,
      `${component.component_id} title must stay renderer-owned instead of inline markup`,
    );
  }
  assert.doesNotMatch(titledMarkup, /<svg\b|class="node-label"/);
});

test("component map collapse keeps one minimal static rail and removes the graph body", () => {
  const typedSnapshot = m2cProjectionSnapshot();
  const markup = renderComponentMap(typedSnapshot, createSotViewState(typedSnapshot), false);

  assert.match(markup, /id="component-map-title">Component Map/);
  assert.match(markup, /class="component-map component-map--three component-map--collapsed"/);
  assert.equal((markup.match(/class="component-map-hud"/g) ?? []).length, 1);
  assert.equal((markup.match(/data-sot-graph-toggle/g) ?? []).length, 1);
  assert.match(markup, /data-sot-graph-toggle[^>]*aria-expanded="false"/);
  assert.match(markup, /⌄ 그래프 펼치기/);
  assert.match(markup, /id="component-map-body"[^>]*data-component-map-body[^>]* hidden/);
  assert.ok(
    markup.indexOf('data-graph-zoom="in"') < markup.indexOf('id="component-map-body"'),
    "collapsed graph controls stay in the CSS-hidden HUD instead of the hidden body",
  );
});

test("SoT tree preserves the reference root structure and independent roving selection", () => {
  const markup = renderSotTree(snapshot, {
    ...createSotViewState(snapshot),
    selectedComponentId: "harnesskit.skill.beta",
    treeSelectedId: "harnesskit.agent.router",
  });

  assert.equal((markup.match(/data-component-id=/g) ?? []).length, 4);
  assert.equal((markup.match(/tabindex="0"/g) ?? []).length, 1);
  assert.match(markup, /data-sot-tree-node="root"/);
  assert.match(markup, /data-sot-tree-node="components"/);
  assert.match(markup, /data-sot-tree-node="profiles"/);
  assert.match(markup, />registry\.yml</);
  assert.match(markup, />capabilities\.yml</);
  assert.match(markup, /aria-expanded="true"/);
  assert.match(markup, /tabindex="0"[^>]*data-component-id="harnesskit\.agent\.router"/);
  assert.match(markup, /aria-selected="true"[^>]*data-component-id="harnesskit\.agent\.router"/);
  assert.match(markup, /aria-selected="false"[^>]*data-component-id="harnesskit\.skill\.beta"/);
  assert.match(markup, /data-sot-tree-scroll/);
});

test("packaged AX collection can identify the SoT tree scroll body and preserved filter", () => {
  const markup = renderSotTree(snapshot, createSotViewState(snapshot));

  assert.match(
    markup,
    /<input\b(?=[^>]*\bid="sot-tree-filter")(?=[^>]*\btype="search")[^>]*>/,
  );
  assert.match(
    markup,
    /<div\b(?=[^>]*\bid="sot-tree-scroll")(?=[^>]*\bclass="tree-scroll")(?=[^>]*\brole="tree")(?=[^>]*\baria-label="HarnessKit SoT")(?=[^>]*\bdata-sot-tree-scroll)[^>]*>/,
  );
});

test("profile matrix stays rectangular and neutral Unprofiled is not a source profile", () => {
  const markup = renderProfileMatrix(snapshot, {
    ...createSotViewState(snapshot),
    activeProfileId: "harnesskit.profile.engineering",
    selectedComponentId: "harnesskit.skill.alpha",
  });

  assert.equal((markup.match(/data-profile-id=/g) ?? []).length, 3);
  assert.match(markup, /profile-panel--active/);
  assert.match(markup, /profile-panel--owns-selected/);
  assert.equal((markup.match(/data-owning-selected="true"/g) ?? []).length, 2);
  assert.equal((markup.match(/선택 component 포함/g) ?? []).length, 4);
  assert.match(markup, /data-profile-id="__unprofiled__"/);
  assert.match(markup, /id="profile-matrix"[^>]*aria-labelledby="profile-matrix-title"/);
  assert.match(markup, /id="profile-grid"[^>]*role="group"[^>]*aria-label="Profile cards"/);
  assert.match(markup, /id="profile-member-region"[^>]*role="region"[^>]*aria-label="Selected profile members"/);
  assert.match(markup, /id="profile-member-grid"[^>]*role="group"[^>]*aria-label="Profile member cards"/);
  const engineeringProfileButton = markup.match(
    /<button\b(?=[^>]*data-profile-id="harnesskit\.profile\.engineering")[\s\S]*?<\/button>/,
  )?.[0] ?? "";
  assert.ok(engineeringProfileButton, "Engineering profile card must be present");
  assert.doesNotMatch(engineeringProfileButton, /aria-label=/);
  assert.match(engineeringProfileButton, /<strong>Engineering<\/strong>/);
  assert.match(
    engineeringProfileButton,
    /<span class="sr-only">harnesskit\.profile\.engineering · profile · draft · 2 components · 선택 component 포함<\/span>/,
  );
  assert.match(
    engineeringProfileButton,
    /<small class="profile-owner-status" aria-hidden="true">선택 component 포함<\/small>/,
  );

  const routerComponentButton = markup.match(
    /<button\b(?=[^>]*data-component-id="harnesskit\.agent\.router")[\s\S]*?<\/button>/,
  )?.[0] ?? "";
  assert.ok(routerComponentButton, "Router component card must be present");
  assert.doesNotMatch(routerComponentButton, /aria-label=/);
  assert.match(routerComponentButton, /<strong>Router<\/strong>/);
  assert.match(
    routerComponentButton,
    /<span class="sr-only">harnesskit\.agent\.router · component · agent · draft<\/span>/,
  );
  assert.match(markup, />Unprofiled</);
  assert.match(markup, /class="profile-member-region"/);
  assert.doesNotMatch(markup, /polygon|orb|hexagon/i);
  assert.doesNotMatch(markup, /data-tool-identity|<img/);
});

test("right Component inspector is human-first and keeps canonical metadata inside collapsed technical disclosure", () => {
  const typedSnapshot = m2cProjectionSnapshot();
  const markup = renderSotInspector(typedSnapshot, {
    ...createSotViewState(typedSnapshot),
    selectedComponentId: "harnesskit.skill.alpha",
  });
  const [primary, technical = ""] = markup.split('<details class="sot-technical-info"');
  const primaryText = primary.replace(/<[^>]+>/g, " ");

  assert.match(primary, /<h3 class="detail-name">Alpha<\/h3>/);
  assert.match(primary, /class="component-kind-badge"[^>]*>SKILL<\/span>/);
  assert.match(primary, /Alpha skill/);
  assert.match(primary, /data-related-kind="workflow"[\s\S]*>Review</);
  assert.match(primary, /data-related-kind="profile"[\s\S]*>Engineering<[\s\S]*>Work</);
  assert.match(primary, /data-related-kind="component"[\s\S]*>Router</);
  assert.doesNotMatch(primaryText, /harnesskit\.|components\/skills\/alpha\/SKILL\.md|runtime_supported|declarative/);

  assert.match(markup, /<details class="sot-technical-info">[\s\S]*<summary>기술 정보<\/summary>/);
  assert.match(technical, /harnesskit\.skill\.alpha/);
  assert.match(technical, /runtime_supported/);
  assert.equal((markup.match(/data-tool-identity-context="sot_target"/g) ?? []).length, 1);
  assert.match(markup, /data-tool-identity="codex"[^>]*data-tool-identity-mode="text"/);
  assert.doesNotMatch(markup, /<img|data-tool-identity-image/);
  assert.match(technical, /components\/skills\/alpha\/SKILL\.md/);
  assert.match(technical, /declarative/);
  assert.doesNotMatch(markup, /install status|local path|hash/i);
});
