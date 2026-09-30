import assert from "node:assert/strict";
import test from "node:test";

import {
  createSotViewState,
  reduceSotView,
  renderComponentMap,
  renderSotInspector,
} from "../sot-view.js";
import { createInitialState, renderAppShell } from "../app-shell.js";

const workflowId = "harnesskit.workflow.review";
const componentId = "harnesskit.agent.reviewer";
const workflowNodeId = `workflow:${workflowId}`;

const snapshot = Object.freeze({
  snapshot_id: "snapshot-3d",
  checkout_summary: { source_revision: "abc123" },
  components: [
    {
      component_id: componentId,
      kind: "agent",
      status: "draft",
      title: "Reviewer",
      summary: "Reviews a change",
      domain: "engineering",
      targets: [],
      provenance: {},
      owned_files: [],
      profile_ids: ["harnesskit.profile.engineering"],
    },
  ],
  profiles: [
    {
      profile_id: "harnesskit.profile.engineering",
      title: "Engineering",
      component_ids: [componentId],
    },
  ],
  workflows: [
    {
      workflow_id: workflowId,
      title: "Review",
      description: "Review in authored order",
      runtime_implemented: false,
      source_path: "components/workflows/review/workflow.yml",
      raw_yaml: "kind: workflow\nsteps:\n  - id: review\n",
      steps: [
        {
          workflow_id: workflowId,
          ordinal: 1,
          step_id: "review",
          title: "Review",
          description: "Review the change",
          authored_fields: { agent: componentId },
          resolved_component_ids: [componentId],
          unresolved_references: [],
          source_path: "components/workflows/review/workflow.yml",
        },
      ],
    },
  ],
  unprofiled_component_ids: [],
  relations: [],
  graph_projection: {
    schema_version: 2,
    snapshot_id: "snapshot-3d",
    layout_seed: "seed-3d",
    nodes: [
      {
        node_type: "relation",
        node_id: "profile:harnesskit.profile.engineering",
        relation_kind: "profile",
        canonical_id: "harnesskit.profile.engineering",
        name: "Engineering",
        exact_count: 1,
        anchor_ordinal: 1,
        size_scale: 1,
      },
      {
        node_type: "relation",
        node_id: workflowNodeId,
        relation_kind: "workflow",
        canonical_id: workflowId,
        name: "Review",
        exact_count: 1,
        anchor_ordinal: 1,
        size_scale: 1,
      },
      {
        node_type: "component",
        node_id: `component:${componentId}`,
        component_id: componentId,
        kind: "agent",
        domain: "engineering",
        relation_degree: 2,
        profile_ids: ["harnesskit.profile.engineering"],
        workflow_ids: [workflowId],
      },
    ],
    links: [
      {
        semantic: "profile-membership",
        link_id: `profile-membership:harnesskit.profile.engineering:${componentId}`,
        profile_node_id: "profile:harnesskit.profile.engineering",
        component_node_id: `component:${componentId}`,
        source_node_id: "profile:harnesskit.profile.engineering",
        target_node_id: `component:${componentId}`,
        directionality: "unordered",
        provenance: "CanonicalProfile",
      },
      {
        semantic: "workflow-step",
        link_id: `workflow-step:${workflowId}:${componentId}`,
        workflow_node_id: workflowNodeId,
        component_node_id: `component:${componentId}`,
        source_node_id: workflowNodeId,
        target_node_id: `component:${componentId}`,
        directionality: "directed",
        occurrences: [{ ordinal: 1, step_id: "review", source_field: "agent" }],
      },
    ],
  },
  navigation_projection: { all_component_ids: [componentId], groups: [] },
  issues: [],
});

test("final relation atlas renders a persistent WebGL host and semantic fallback without legacy SVG", () => {
  const markup = renderComponentMap(snapshot, createSotViewState(snapshot), true);

  assert.match(markup, /data-component-map-scene-host/);
  assert.match(markup, /data-semantic-graph-fallback/);
  assert.match(markup, /data-graph-identity-overlay/);
  assert.match(markup, /data-graph-identity-kind/);
  assert.match(markup, /data-graph-identity-count/);
  assert.doesNotMatch(markup, /data-graph-identity-(?:rail|id|meta)/);
  assert.match(markup, /3 nodes · 2 links/);
  assert.doesNotMatch(markup, /<svg\b|data-graph-scene/);
});

test("Workflow relation selection is graph-only and opens the ordered inspector", () => {
  const initial = createSotViewState(snapshot);
  const selected = reduceSotView(initial, {
    type: "select_graph_relation",
    nodeId: workflowNodeId,
    relationKind: "workflow",
    canonicalId: workflowId,
  }, snapshot);
  const locked = reduceSotView(selected, {
    type: "select_workflow_step",
    workflowId,
    ordinal: 1,
  }, snapshot);
  const lockedFromComponent = reduceSotView(createSotViewState(snapshot, {
    selectedComponentId: componentId,
  }), {
    type: "select_workflow_step",
    workflowId,
    ordinal: 1,
  }, snapshot);

  assert.equal(selected.selectedWorkflowId, workflowId);
  assert.equal(selected.selectedRelationNodeId, workflowNodeId);
  assert.equal(selected.activeProfileId, initial.activeProfileId);
  assert.deepEqual(locked.lockedWorkflowStep, { workflowId, ordinal: 1 });
  assert.equal(lockedFromComponent.selectedComponentId, null);
  assert.equal(lockedFromComponent.selectedWorkflowId, workflowId);

  const inspector = renderSotInspector(snapshot, locked);
  const [primary, technical = ""] = inspector.split('<details class="sot-technical-info"');
  const primaryText = primary.replace(/<[^>]+>/g, " ");
  assert.match(primary, /<h3 class="detail-name">Review<\/h3>/);
  assert.match(primary, /class="component-kind-badge"[^>]*>WORKFLOW<\/span>/);
  assert.match(primary, /Review in authored order/);
  assert.match(inspector, /data-workflow-step="1"/);
  assert.match(primary, /data-related-kind="component"[\s\S]*>Reviewer</);
  assert.match(primary, /data-workflow-truth[\s\S]*작성된 정의[\s\S]*Runtime 미구현/);
  assert.doesNotMatch(primaryText, /harnesskit\.|components\/workflows\/review\/workflow\.yml|kind: workflow/);
  assert.match(inspector, /<details class="sot-technical-info">[\s\S]*<summary>기술 정보<\/summary>/);
  assert.match(technical, /harnesskit\.workflow\.review/);
  assert.match(technical, /components\/workflows\/review\/workflow\.yml/);
  assert.match(technical, /kind: workflow/);
});

test("Workflow relation selection owns the right inspector without leaking Component install actions", () => {
  const selected = reduceSotView(createSotViewState(snapshot, {
    selectedComponentId: componentId,
  }), {
    type: "select_graph_relation",
    nodeId: workflowNodeId,
    relationKind: "workflow",
    canonicalId: workflowId,
  }, snapshot);
  const markup = renderAppShell(createInitialState({
    repo: {
      phase: "registered",
      checkoutId: "checkout-3d",
      checkoutPath: "/private/tmp/harnesskit",
      canonicalPath: "/private/tmp/harnesskit",
    },
    sot: { phase: "ready", snapshot, message: "3D relation atlas ready" },
    sotView: selected,
  }));

  assert.equal(selected.selectedComponentId, null);
  assert.match(markup, /<h2 id="detail-title">Workflow detail<\/h2>/);
  assert.match(markup, /<h3 class="detail-name">Review<\/h3>/);
  assert.match(markup, /data-workflow-step="1"/);
  assert.doesNotMatch(markup, /data-sot-install-host/);
});

test("Workflow overview and graph background clear only graph-owned focus state", () => {
  const initial = createSotViewState(snapshot, {
    selectedComponentId: componentId,
    selectedRelationNodeId: workflowNodeId,
    selectedWorkflowId: workflowId,
    hoveredGraphNodeId: `component:${componentId}`,
    hoveredWorkflowStep: { workflowId, ordinal: 1 },
    lockedWorkflowStep: { workflowId, ordinal: 1 },
  });
  const overview = reduceSotView(initial, {
    type: "workflow_overview",
    workflowId,
  }, snapshot);
  const cleared = reduceSotView(overview, { type: "clear_graph_selection" }, snapshot);

  assert.equal(overview.selectedWorkflowId, workflowId);
  assert.equal(overview.lockedWorkflowStep, null);
  assert.equal(cleared.activeProfileId, initial.activeProfileId);
  assert.equal(cleared.selectedComponentId, null);
  assert.equal(cleared.selectedRelationNodeId, null);
  assert.equal(cleared.selectedWorkflowId, null);
  assert.equal(cleared.hoveredGraphNodeId, null);
  assert.equal(cleared.hoveredWorkflowStep, null);
});
