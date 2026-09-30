import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_LEFT_WIDTH_PX,
  DEFAULT_RIGHT_WIDTH_PX,
  MIN_LEFT_WIDTH_PX,
  MIN_RIGHT_WIDTH_PX,
  projectWorkspaceLayout,
  normalizeWorkspaceLayoutState,
} from "../layout/state.js";

function preferred(overrides = {}) {
  return {
    preferredLeftWidthPx: DEFAULT_LEFT_WIDTH_PX,
    preferredRightWidthPx: DEFAULT_RIGHT_WIDTH_PX,
    ...overrides,
  };
}

test("projection uses approved breakpoints, defaults, and deterministic shrink rounding", () => {
  assert.equal(MIN_LEFT_WIDTH_PX, 200);
  assert.equal(MIN_RIGHT_WIDTH_PX, 184);
  assert.deepEqual(
    projectWorkspaceLayout({ shellWidth: 1200, ...preferred() }),
    {
      mode: "three-pane",
      preferredLeftWidthPx: 304,
      preferredRightWidthPx: 368,
      effectiveLeftWidthPx: 304,
      effectiveRightWidthPx: 368,
      centerWidthPx: 504,
      leftMinimumPx: 200,
      leftMaximumPx: 328,
      rightMinimumPx: 184,
      rightMaximumPx: 392,
      separatorsActive: true,
    },
  );

  const tight = projectWorkspaceLayout({ shellWidth: 1057, ...preferred() });
  assert.equal(tight.mode, "three-pane");
  assert.equal(tight.effectiveLeftWidthPx, 261);
  assert.equal(tight.effectiveRightWidthPx, 292);
  assert.equal(tight.centerWidthPx, 480);

  const tie = projectWorkspaceLayout({ shellWidth: 1172, ...preferred() });
  assert.equal(tie.effectiveLeftWidthPx, 303);
  assert.equal(tie.effectiveRightWidthPx, 365);
  assert.equal(tie.centerWidthPx, 480);

  assert.equal(projectWorkspaceLayout({ shellWidth: 1056, ...preferred() }).mode, "two-column");
  assert.equal(projectWorkspaceLayout({ shellWidth: 865, ...preferred() }).mode, "two-column");
  assert.equal(projectWorkspaceLayout({ shellWidth: 864, ...preferred() }).mode, "stacked");
});

test("active drag clamps only its side and preserves the preferred pair", () => {
  const leftDrag = projectWorkspaceLayout({
    shellWidth: 1200,
    ...preferred(),
    activeSide: "left",
    activeWidthPx: 999,
    oppositeEffectiveWidthPx: 368,
  });
  assert.equal(leftDrag.effectiveLeftWidthPx, 328);
  assert.equal(leftDrag.effectiveRightWidthPx, 368);
  assert.equal(leftDrag.preferredLeftWidthPx, 304);
  assert.equal(leftDrag.preferredRightWidthPx, 368);

  const rightDrag = projectWorkspaceLayout({
    shellWidth: 1200,
    ...preferred(),
    activeSide: "right",
    activeWidthPx: 0,
    oppositeEffectiveWidthPx: 304,
  });
  assert.equal(rightDrag.effectiveLeftWidthPx, 304);
  assert.equal(rightDrag.effectiveRightWidthPx, 184);
  assert.equal(rightDrag.preferredLeftWidthPx, 304);
  assert.equal(rightDrag.preferredRightWidthPx, 368);

  const resizedDuringLeftDrag = projectWorkspaceLayout({
    shellWidth: 1057,
    ...preferred(),
    activeSide: "left",
    activeWidthPx: -1000,
    oppositeEffectiveWidthPx: 368,
  });
  assert.ok(resizedDuringLeftDrag.effectiveLeftWidthPx >= resizedDuringLeftDrag.leftMinimumPx);
  assert.ok(resizedDuringLeftDrag.leftMaximumPx >= resizedDuringLeftDrag.leftMinimumPx);
  assert.ok(resizedDuringLeftDrag.effectiveRightWidthPx >= resizedDuringLeftDrag.rightMinimumPx);
  assert.equal(resizedDuringLeftDrag.centerWidthPx, 480);
});

test("responsive projection never mutates the preferred pair", () => {
  const twoColumn = projectWorkspaceLayout({
    shellWidth: 1000,
    ...preferred({ preferredLeftWidthPx: 500, preferredRightWidthPx: 550 }),
  });
  assert.equal(twoColumn.mode, "two-column");
  assert.equal(twoColumn.preferredLeftWidthPx, 500);
  assert.equal(twoColumn.preferredRightWidthPx, 550);
  assert.equal(twoColumn.effectiveLeftWidthPx, 500);
  assert.equal(twoColumn.centerWidthPx, 500);
  assert.equal(twoColumn.separatorsActive, false);

  const stacked = projectWorkspaceLayout({
    shellWidth: 820,
    ...preferred({ preferredLeftWidthPx: 500, preferredRightWidthPx: 550 }),
  });
  assert.equal(stacked.mode, "stacked");
  assert.equal(stacked.preferredLeftWidthPx, 500);
  assert.equal(stacked.preferredRightWidthPx, 550);
  assert.equal(stacked.effectiveLeftWidthPx, 820);
  assert.equal(stacked.effectiveRightWidthPx, 820);
  assert.equal(stacked.centerWidthPx, 820);
  assert.equal(stacked.separatorsActive, false);
});

test("normalization accepts the backend envelope and fails safe as one default pair", () => {
  assert.deepEqual(
    normalizeWorkspaceLayoutState({
      preferred_left_width_px: 320,
      preferred_right_width_px: 400,
      revision: 9,
      persisted: false,
    }),
    {
      preferredLeftWidthPx: 320,
      preferredRightWidthPx: 400,
      leftCollapsed: false,
      rightCollapsed: false,
      revision: 9,
      persisted: false,
    },
  );

  for (const invalid of [
    null,
    {},
    {
      preferred_left_width_px: 320,
      preferred_right_width_px: 999,
      revision: 9,
      persisted: true,
    },
    {
      preferred_left_width_px: 320,
      revision: 9,
      persisted: true,
    },
  ]) {
    assert.deepEqual(normalizeWorkspaceLayoutState(invalid), {
      preferredLeftWidthPx: 304,
      preferredRightWidthPx: 368,
      leftCollapsed: false,
      rightCollapsed: false,
      revision: 0,
      persisted: false,
    });
  }
});
