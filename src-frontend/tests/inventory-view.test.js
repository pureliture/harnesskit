import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_VISIBLE_TREE_ROWS,
  buildTreeRows,
  reduceTreeKeyboard,
  visibleTreeRows,
} from "../inventory-view.js";
import { inventoryFixture } from "./fixtures/inventory.js";

test("tree is stable scope → target → kind → component and duplicates identity by location scope", () => {
  const rows = buildTreeRows(inventoryFixture);
  const components = rows.filter((row) => row.type === "component");

  assert.deepEqual(
    components.map(({ scope, target, kind, componentId }) => [scope, target, kind, componentId]),
    [
      ["User", "claude", "agent", "beta-agent"],
      ["User", "codex", "skill", "alpha-skill"],
      ["Project", "codex", "skill", "alpha-skill"],
    ],
  );
  assert.equal(components.filter((row) => row.componentId === "alpha-skill").length, 2);
  assert.equal(rows.filter((row) => row.type === "orphan").length, 2);
  assert.ok(rows.find((row) => row.id === "orphans" && row.type === "orphan-group"));
});

test("filter retains matching branches and stable row order", () => {
  const first = buildTreeRows(inventoryFixture, { filter: "alpha" });
  const second = buildTreeRows(inventoryFixture, { filter: "ALPHA" });

  assert.deepEqual(first, second);
  assert.deepEqual(
    first.filter((row) => row.type === "component").map((row) => row.scope),
    ["User", "Project"],
  );
  assert.ok(first.every((row) => !row.label.includes("beta-agent")));
});

test("keyboard reducer moves selection and expands or collapses groups", () => {
  const rows = buildTreeRows(inventoryFixture);
  const expandedIds = rows.filter((row) => row.expandable).map((row) => row.id);
  const visible = visibleTreeRows(rows, expandedIds).rows;
  let state = { selectedId: visible[0].id, expandedIds };

  state = reduceTreeKeyboard(state, "ArrowDown", rows);
  assert.equal(state.selectedId, visible[1].id);
  state = reduceTreeKeyboard(state, "ArrowUp", rows);
  assert.equal(state.selectedId, visible[0].id);
  state = reduceTreeKeyboard(state, "End", rows);
  assert.equal(state.selectedId, visible.at(-1).id);
  state = reduceTreeKeyboard(state, "Home", rows);
  assert.equal(state.selectedId, visible[0].id);

  state = reduceTreeKeyboard(state, "ArrowLeft", rows);
  assert.ok(!state.expandedIds.includes(visible[0].id));
  state = reduceTreeKeyboard(state, "ArrowRight", rows);
  assert.ok(state.expandedIds.includes(visible[0].id));
});

test("visible rows are capped without changing deterministic total", () => {
  const items = Array.from({ length: 220 }, (_, index) => ({
    ...inventoryFixture.items[0],
    component_id: `skill-${String(index).padStart(3, "0")}`,
    title: `Skill ${index}`,
    locations: [{ scope: "User", path: `/user/skill-${index}`, install_status: "Match" }],
  }));
  const rows = buildTreeRows({ ...inventoryFixture, items, orphan_discoveries: [] });
  const expandedIds = rows.filter((row) => row.expandable).map((row) => row.id);
  const result = visibleTreeRows(rows, expandedIds);

  assert.equal(result.rows.length, MAX_VISIBLE_TREE_ROWS);
  assert.ok(result.total > result.rows.length);
  assert.equal(result.truncated, true);
});
