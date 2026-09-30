import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = globalThis.window ?? {};
const { default: ThreeForceGraph } = await import("three-forcegraph");

test("upstream graphData digest is asynchronous and applies the exact configured warmup tick count", async () => {
  const graph = new ThreeForceGraph();
  let forceCalls = 0;
  const contractForce = () => { forceCalls += 1; };
  contractForce.initialize = () => {};

  graph
    .warmupTicks(7)
    .cooldownTicks(0)
    .d3Force("contract-evidence", contractForce);
  const finished = new Promise((resolve) => graph.onFinishUpdate(resolve));
  graph.graphData({
    nodes: [{ id: "alpha" }, { id: "beta" }],
    links: [],
  });

  assert.equal(forceCalls, 0);
  assert.equal(graph.getGraphBbox(), null);

  await finished;
  assert.equal(forceCalls, 7);
  assert.ok(graph.getGraphBbox());
  graph._destructor?.();
});
