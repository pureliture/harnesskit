import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveGraphNodeEmphasis,
  deriveGraphVisualEmphasis,
} from "../graph/visual-emphasis.js";

const nodes = [
  { node_id: "component:selected", node_type: "component" },
  { node_id: "component:adjacent", node_type: "component" },
  { node_id: "profile:contextual", node_type: "relation" },
  { node_id: "component:unrelated", node_type: "component" },
];

test("typed real links classify selected, adjacent, contextual and unrelated nodes by distance", () => {
  const emphasis = deriveGraphNodeEmphasis({
    nodes,
    links: [
      {
        link_id: "membership",
        source_node_id: "component:selected",
        target_node_id: "component:adjacent",
        semantic: "profile-membership",
      },
      {
        link_id: "cross-link",
        source_node_id: "component:adjacent",
        target_node_id: "profile:contextual",
        semantic: "component-cross-link",
      },
      {
        link_id: "ignored-without-endpoints",
        semantic: "workflow-step",
      },
    ],
    selectedNodeIds: new Set(["component:selected"]),
  });

  assert.equal(emphasis.get("component:selected"), "selected");
  assert.equal(emphasis.get("component:adjacent"), "adjacent");
  assert.equal(emphasis.get("profile:contextual"), "contextual");
  assert.equal(emphasis.get("component:unrelated"), "unrelated");
});

test("multiple selected real nodes retain selected emphasis without inventing graph links", () => {
  const emphasis = deriveGraphNodeEmphasis({
    nodes,
    links: [],
    selectedNodeIds: new Set(["component:selected", "profile:contextual"]),
  });

  assert.equal(emphasis.get("component:selected"), "selected");
  assert.equal(emphasis.get("profile:contextual"), "selected");
  assert.equal(emphasis.get("component:adjacent"), "unrelated");
});

test("primary focus is independent from plural selection and selects only its actual one-hop links", () => {
  const visual = deriveGraphVisualEmphasis({
    nodes,
    links: [
      {
        link_id: "focus-membership",
        source_node_id: "component:selected",
        target_node_id: "component:adjacent",
        semantic: "profile-membership",
      },
      {
        link_id: "selected-but-not-focused",
        source_node_id: "profile:contextual",
        target_node_id: "component:unrelated",
        semantic: "profile-membership",
      },
      {
        link_id: "missing-endpoint",
        source_node_id: "component:selected",
        semantic: "workflow-step",
      },
    ],
    selectedNodeIds: new Set(["component:selected", "profile:contextual"]),
    focusNodeId: "component:selected",
  });

  assert.equal(visual.focusNodeId, "component:selected");
  assert.equal(visual.emphasisByNodeId.get("component:selected"), "selected");
  assert.equal(visual.emphasisByNodeId.get("profile:contextual"), "selected");
  assert.equal(visual.emphasisByNodeId.get("component:adjacent"), "adjacent");
  assert.deepEqual([...visual.selectedOneHopLinkIds], ["focus-membership"]);
});

test("plural selection never infers a primary focus", () => {
  const visual = deriveGraphVisualEmphasis({
    nodes,
    links: [],
    selectedNodeIds: new Set(["component:selected", "profile:contextual"]),
    focusNodeId: null,
  });

  assert.equal(visual.focusNodeId, null);
  assert.equal(visual.emphasisByNodeId.get("component:selected"), "selected");
  assert.equal(visual.emphasisByNodeId.get("profile:contextual"), "selected");
  assert.equal(visual.selectedOneHopLinkIds.size, 0);
});

test("no selection keeps every node idle even when focus is absent", () => {
  const visual = deriveGraphVisualEmphasis({
    nodes,
    links: [],
    selectedNodeIds: new Set(),
    focusNodeId: null,
  });

  assert.deepEqual(
    [...visual.emphasisByNodeId.values()],
    ["idle", "idle", "idle", "idle"],
  );
});
