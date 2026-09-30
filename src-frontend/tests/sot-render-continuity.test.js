import assert from "node:assert/strict";
import test from "node:test";

import {
  captureSotRenderContinuity,
  restoreSotRenderContinuity,
} from "../sot-render-continuity.js";

function element(dataset = {}, { surfaceSelector = null, rect = null } = {}) {
  return {
    dataset: { ...dataset },
    focusOptions: null,
    scrollIntoViewOptions: null,
    focus(options) { this.focusOptions = options; },
    getBoundingClientRect() { return rect; },
    scrollIntoView(options) { this.scrollIntoViewOptions = options; },
    closest(selector) {
      return selector === surfaceSelector ? { dataset: {} } : null;
    },
  };
}

function rootHarness({
  activeElement,
  scrollTop = 0,
  inspectorScrollTop = 0,
  inspectorRect = null,
  candidates = [],
} = {}) {
  const workbench = { scrollTop };
  const inspectorPane = {
    scrollTop: inspectorScrollTop,
    getBoundingClientRect() { return inspectorRect; },
  };
  return {
    workbench,
    inspectorPane,
    root: {
      ownerDocument: { activeElement },
      querySelector(selector) {
        if (selector === "#workbench") return workbench;
        if (selector === "#workspace-right-pane") return inspectorPane;
        return null;
      },
      querySelectorAll() { return candidates; },
    },
  };
}

test("Workflow step activation rerender restores right Inspector scroll and logical focus", () => {
  const dataset = {
    workflowId: "harnesskit.workflow.review",
    workflowStep: "2",
  };
  const before = element(dataset, { surfaceSelector: ".workflow-inspector" });
  const departure = rootHarness({
    activeElement: before,
    scrollTop: 417,
    inspectorScrollTop: 263,
  });
  const continuity = captureSotRenderContinuity(departure.root, "sot");
  const replacement = element(dataset, { surfaceSelector: ".workflow-inspector" });
  const arrival = rootHarness({
    activeElement: null,
    scrollTop: 0,
    inspectorScrollTop: 0,
    candidates: [replacement],
  });

  restoreSotRenderContinuity(arrival.root, continuity);

  assert.equal(arrival.workbench.scrollTop, 417);
  assert.equal(arrival.inspectorPane.scrollTop, 263);
  assert.deepEqual(replacement.focusOptions, { preventScroll: true });
});

test("restored Workflow step scrolls minimally when it is outside the Inspector viewport", () => {
  const dataset = {
    workflowId: "harnesskit.workflow.review",
    workflowStep: "5",
  };
  const before = element(dataset, { surfaceSelector: ".workflow-inspector" });
  const departure = rootHarness({
    activeElement: before,
    inspectorScrollTop: 263,
  });
  const continuity = captureSotRenderContinuity(departure.root, "sot");
  const replacement = element(dataset, {
    surfaceSelector: ".workflow-inspector",
    rect: { top: 740, right: 480, bottom: 790, left: 320 },
  });
  const arrival = rootHarness({
    candidates: [replacement],
    inspectorRect: { top: 100, right: 500, bottom: 700, left: 300 },
  });

  restoreSotRenderContinuity(arrival.root, continuity);

  assert.deepEqual(replacement.focusOptions, { preventScroll: true });
  assert.deepEqual(replacement.scrollIntoViewOptions, {
    block: "nearest",
    inline: "nearest",
  });
});

test("restored Workflow step does not scroll when it remains inside the Inspector viewport", () => {
  const dataset = {
    workflowId: "harnesskit.workflow.review",
    workflowStep: "3",
  };
  const before = element(dataset, { surfaceSelector: ".workflow-inspector" });
  const continuity = captureSotRenderContinuity(rootHarness({
    activeElement: before,
    inspectorScrollTop: 180,
  }).root, "sot");
  const replacement = element(dataset, {
    surfaceSelector: ".workflow-inspector",
    rect: { top: 260, right: 480, bottom: 320, left: 320 },
  });
  const arrival = rootHarness({
    candidates: [replacement],
    inspectorRect: { top: 100, right: 500, bottom: 700, left: 300 },
  });

  restoreSotRenderContinuity(arrival.root, continuity);

  assert.deepEqual(replacement.focusOptions, { preventScroll: true });
  assert.equal(replacement.scrollIntoViewOptions, null);
});

for (const [label, dataset] of [
  ["Component", { semanticNodeId: "component:harnesskit.agent.writer" }],
  ["Workflow", { semanticNodeId: "workflow:harnesskit.workflow.review" }],
  ["Step", { workflowId: "harnesskit.workflow.review", workflowStep: "2" }],
]) {
  test(`${label} selection rerender restores SoT workbench scroll and logical focus identity`, () => {
    const before = element(dataset);
    const departure = rootHarness({ activeElement: before, scrollTop: 417 });
    const continuity = captureSotRenderContinuity(departure.root, "sot");
    const replacement = element(dataset);
    const arrival = rootHarness({ activeElement: null, scrollTop: 0, candidates: [replacement] });

    restoreSotRenderContinuity(arrival.root, continuity);

    assert.equal(arrival.workbench.scrollTop, 417);
    assert.deepEqual(replacement.focusOptions, { preventScroll: true });
  });
}

test("non-SoT rerenders do not capture or overwrite local workbench state", () => {
  const active = element({ semanticNodeId: "component:harnesskit.agent.writer" });
  const harness = rootHarness({ activeElement: active, scrollTop: 312 });

  const continuity = captureSotRenderContinuity(harness.root, "local");
  harness.workbench.scrollTop = 99;
  restoreSotRenderContinuity(harness.root, continuity);

  assert.equal(continuity, null);
  assert.equal(harness.workbench.scrollTop, 99);
});

for (const scenario of [
  {
    label: "SoT tree",
    expectedSurface: "[data-sot-tree-scroll]",
    competingSurface: "#profile-member-region",
  },
  {
    label: "Profile Matrix member",
    expectedSurface: "#profile-member-region",
    competingSurface: "[data-sot-tree-scroll]",
  },
]) {
  test(`${scenario.label} component focus returns to the same surface when duplicate component controls exist`, () => {
    const componentId = "harnesskit.agent.writer";
    const before = element(
      { componentId },
      { surfaceSelector: scenario.expectedSurface },
    );
    const departure = rootHarness({ activeElement: before, scrollTop: 219 });
    const continuity = captureSotRenderContinuity(departure.root, "sot");
    const competing = element(
      { componentId },
      { surfaceSelector: scenario.competingSurface },
    );
    const expected = element(
      { componentId },
      { surfaceSelector: scenario.expectedSurface },
    );
    const arrival = rootHarness({
      activeElement: null,
      scrollTop: 0,
      candidates: [competing, expected],
    });

    restoreSotRenderContinuity(arrival.root, continuity);

    assert.equal(competing.focusOptions, null);
    assert.deepEqual(expected.focusOptions, { preventScroll: true });
    assert.equal(arrival.workbench.scrollTop, 219);
  });
}
