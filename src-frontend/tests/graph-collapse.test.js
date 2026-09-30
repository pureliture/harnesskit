import assert from "node:assert/strict";
import test from "node:test";

import { createInitialState } from "../app-shell.js";

test("fresh app state owns both disclosure defaults outside snapshot-owned view state", () => {
  const state = createInitialState();

  assert.deepEqual(state.ui.sotDisclosures, {
    componentMap: true,
    profileMatrix: false,
  });
  assert.equal("sotGraphExpanded" in state.ui, false);
  assert.equal("sotDisclosures" in state.sotView, false);
});
