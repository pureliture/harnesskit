import assert from "node:assert/strict";
import test from "node:test";

import {
  bindOrderedStepInspector,
  renderOrderedStepInspector,
} from "../graph/ordered-step-inspector.js";

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.freeze(value);
  Object.values(value).forEach(deepFreeze);
  return value;
}

function workflowFixture() {
  return {
    workflow_id: "harnesskit.workflow.review-gated-implementation",
    name: "Review-gated implementation",
    description: "승인된 계획을 순서대로 실행합니다.",
    authored_state: "approved",
    runtime_implemented: false,
    source_path: "components/workflows/review-gated-implementation/workflow.yml",
    raw_yaml: [
      "name: Review-gated implementation",
      "description: '<script>window.evil = true</script>'",
      "steps:",
      "  - id: plan",
      "    agent: harnesskit.agent.plan-writer",
    ].join("\n"),
    steps: [
      {
        ordinal: 1,
        step_id: "plan",
        title: "Plan",
        description: "변경 계획을 작성합니다.",
        authored_fields: { agent: "harnesskit.agent.plan-writer" },
        resolved_component_ids: ["harnesskit.agent.plan-writer"],
        unresolved_references: [],
        source_path: "components/workflows/review-gated-implementation/workflow.yml",
      },
      {
        ordinal: 2,
        step_id: "implement",
        title: "Implement",
        description: "구현합니다.",
        authored_fields: { agent: "harnesskit.agent.tdd-implementer" },
        resolved_component_ids: ["harnesskit.agent.tdd-implementer"],
        unresolved_references: [],
        source_path: "components/workflows/review-gated-implementation/workflow.yml",
      },
      {
        ordinal: 3,
        step_id: "manual-gate",
        title: "Manual gate",
        description: "Component reference가 없는 authored step도 유지합니다.",
        authored_fields: {
          agent: null,
          skill: "",
          mode_fanout: {},
          gate_order: 0,
          optional: false,
        },
        resolved_component_ids: [],
        unresolved_references: [],
        source_path: "components/workflows/review-gated-implementation/workflow.yml",
      },
      {
        ordinal: 4,
        step_id: "review",
        title: "Review",
        description: "검토합니다.",
        authored_fields: { agent: "harnesskit.agent.code-reviewer" },
        resolved_component_ids: ["harnesskit.agent.code-reviewer"],
        unresolved_references: [],
        source_path: "components/workflows/review-gated-implementation/workflow.yml",
      },
      {
        ordinal: 5,
        step_id: "repair",
        title: "Repair",
        description: "같은 구현 Component를 다시 사용합니다.",
        authored_fields: {
          agent: "harnesskit.agent.tdd-implementer",
          loop_back_to: "implement",
        },
        resolved_component_ids: ["harnesskit.agent.tdd-implementer"],
        unresolved_references: [],
        source_path: "components/workflows/review-gated-implementation/workflow.yml",
      },
      {
        ordinal: 6,
        step_id: "missing",
        title: "Missing reference",
        description: "Registry 밖 reference를 보존합니다.",
        authored_fields: { agent: "harnesskit.agent.missing" },
        resolved_component_ids: [],
        unresolved_references: [{
          source_field: "steps[5].agent",
          reference: "harnesskit.agent.missing",
          reference_kind: "component",
        }],
        source_path: "components/workflows/review-gated-implementation/workflow.yml#steps[5]",
      },
    ],
  };
}

function incidenceBundlesFixture() {
  return [
    {
      link_id: "workflow-incidence:review-gated:tdd-implementer",
      workflow_node_id: "workflow:harnesskit.workflow.review-gated-implementation",
      component_node_id: "component:harnesskit.agent.tdd-implementer",
      semantic: "workflow-step",
      occurrences: [
        { ordinal: 2, step_id: "implement", source_field: "agent", role: "implementation" },
        { ordinal: 5, step_id: "repair", source_field: "agent" },
      ],
    },
  ];
}

function inspectorInput() {
  return deepFreeze({
    workflow: workflowFixture(),
    components: [
      { component_id: "harnesskit.agent.plan-writer", kind: "agent" },
      { component_id: "harnesskit.agent.tdd-implementer", kind: "agent" },
      { component_id: "harnesskit.agent.code-reviewer", kind: "agent" },
      { component_id: "harnesskit.skill.planning", kind: "skill" },
    ],
    incidenceBundles: incidenceBundlesFixture(),
    lockedStepOrdinal: null,
  });
}

function visibleText(html) {
  return String(html)
    .replace(/<[^>]+>/g, " ")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&amp;", "&")
    .replace(/\s+/g, " ")
    .trim();
}

function primaryStepsMarkup(html) {
  const start = html.indexOf('<ol class="workflow-inspector__steps"');
  const end = html.indexOf("</ol>", start);
  assert.ok(start >= 0 && end > start, "ordered primary flow must be present");
  return html.slice(start, end + "</ol>".length);
}

function stepMarkup(html, ordinal) {
  const marker = `data-workflow-step="${ordinal}"`;
  const markerIndex = html.indexOf(marker);
  assert.ok(markerIndex >= 0, `step ${ordinal} must be present`);
  const start = html.lastIndexOf('<li class="workflow-step', markerIndex);
  const next = html.indexOf('<li class="workflow-step', markerIndex + marker.length);
  const end = next >= 0 ? next : html.indexOf("</ol>", markerIndex);
  assert.ok(start >= 0 && end > start, `step ${ordinal} card must be bounded`);
  return html.slice(start, end);
}

function ariaLabelForId(html, id) {
  const start = html.indexOf(`id="${id}"`);
  const end = html.indexOf(">", start);
  assert.ok(start >= 0 && end > start, `${id} must be present`);
  const label = html.slice(start, end).match(/\saria-label="([^"]+)"/)?.[1];
  assert.ok(label, `${id} must have an accessible label`);
  return label;
}

class FakeRoot {
  constructor() {
    this.innerHTML = "";
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  removeEventListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
  }

  emit(type, target, { relatedTarget = null } = {}) {
    this.listeners.get(type)?.({ target, relatedTarget, preventDefault() {} });
  }
}

function stepTarget(workflowId, ordinal) {
  const step = {
    dataset: {
      workflowId,
      workflowStep: String(ordinal),
    },
  };
  return {
    closest(selector) {
      return selector === "[data-workflow-step]" ? step : null;
    },
  };
}

function stepChildTargets(workflowId, ordinal) {
  const step = {
    dataset: {
      workflowId,
      workflowStep: String(ordinal),
    },
  };
  const child = () => ({
    closest(selector) {
      return selector === "[data-workflow-step]" ? step : null;
    },
  });
  return {
    first: child(),
    second: child(),
    outside: {
      closest() {
        return null;
      },
    },
  };
}

function overviewTarget(workflowId) {
  const overview = { dataset: { workflowOverview: workflowId } };
  return {
    closest(selector) {
      return selector === "[data-workflow-overview]" ? overview : null;
    },
  };
}

test("authored steps stay in source order without a separate Component occurrence list", () => {
  const html = renderOrderedStepInspector(inspectorInput());
  const primary = primaryStepsMarkup(html);
  const text = visibleText(primary);

  const positions = ["Plan", "Implement", "Manual gate", "Review", "Repair", "Missing reference"]
    .map((title) => text.indexOf(title));
  assert.ok(positions.every((position) => position >= 0));
  assert.deepEqual([...positions].sort((left, right) => left - right), positions);
  assert.doesNotMatch(html, /class="workflow-incidences"|Component 연결 순서|2\s*·\s*5/);
  assert.equal((html.match(/data-workflow-step=/g) ?? []).length, 6);
});

test("primary Workflow cards expose only ordinal, name, kind badges, and authored description", () => {
  const input = inspectorInput();
  const workflow = {
    ...input.workflow,
    steps: input.workflow.steps.map((step, index) => index === 0
      ? {
        ...step,
        authored_fields: {
          ...step.authored_fields,
          skill: "harnesskit.skill.planning",
          output: "plan.md",
        },
        resolved_component_ids: [
          ...step.resolved_component_ids,
          "harnesskit.skill.planning",
        ],
      }
      : step),
  };
  const html = renderOrderedStepInspector({ ...input, workflow });
  const plan = stepMarkup(html, 1);

  assert.match(plan, /class="workflow-step__ordinal"[^>]*>1<\/span>/);
  assert.match(plan, /<strong\b[^>]*>Plan<\/strong>/);
  assert.match(plan, /변경 계획을 작성합니다\./);
  assert.match(
    plan,
    /<span\b(?=[^>]*\bclass="[^"]*\bcomponent-kind-badge\b[^"]*")(?=[^>]*\bdata-workflow-step-kind="agent")[^>]*>AGENT<\/span>/,
  );
  assert.match(
    plan,
    /<span\b(?=[^>]*\bclass="[^"]*\bcomponent-kind-badge\b[^"]*")(?=[^>]*\bdata-workflow-step-kind="skill")[^>]*>SKILL<\/span>/,
  );
  assert.doesNotMatch(visibleText(plan), /harnesskit\./);
  assert.doesNotMatch(visibleText(plan), /plan\.md/);
  assert.doesNotMatch(
    html,
    /class="workflow-step__fields"|class="workflow-step__references"|id="workflow-occurrence:|id="semantic-workflow-role:/,
  );
});

test("unresolved reference keeps warning and kind badge without dumping technical identity into the card", () => {
  const input = inspectorInput();
  const workflow = {
    ...input.workflow,
    steps: input.workflow.steps.map((step, index) => index === 5
      ? {
        ...step,
        unresolved_references: step.unresolved_references.map((reference) => ({
          ...reference,
          reference_kind: "agent",
        })),
      }
      : step),
  };
  const html = renderOrderedStepInspector({ ...input, workflow });
  const missing = stepMarkup(html, 6);
  const text = visibleText(missing);

  assert.match(text, /연결할 Component를 찾지 못함/);
  assert.match(missing, /data-workflow-step-kind="agent"[^>]*>AGENT<\/span>/);
  assert.doesNotMatch(text, /harnesskit\.|steps\[5\]\.agent|components\/workflows\//);
});

test("kind badges use typed registry joins and unresolved reference_kind only", () => {
  const input = inspectorInput();
  const workflow = {
    ...input.workflow,
    steps: input.workflow.steps.map((step, index) => {
      if (index === 0) {
        return {
          ...step,
          authored_fields: {
            agent: "harnesskit.agent.not-registry-authority",
            skill: "harnesskit.skill.not-registry-authority",
          },
          resolved_component_ids: ["harnesskit.agent.plan-writer"],
        };
      }
      if (index === 5) {
        return {
          ...step,
          authored_fields: { agent: "harnesskit.agent.missing" },
          unresolved_references: [{
            source_field: "steps[5].agent",
            reference: "harnesskit.skill.misleading-prefix",
            reference_kind: "rule",
          }],
        };
      }
      return step;
    }),
  };
  const components = input.components.map((component) => (
    component.component_id === "harnesskit.agent.plan-writer"
      ? { ...component, kind: "command" }
      : component
  ));
  const html = renderOrderedStepInspector({ ...input, workflow, components });
  const resolved = stepMarkup(html, 1);
  const unresolved = stepMarkup(html, 6);

  assert.match(resolved, /data-workflow-step-kind="command"[^>]*>COMMAND<\/span>/);
  assert.doesNotMatch(resolved, /data-workflow-step-kind="agent"|data-workflow-step-kind="skill"/);
  assert.match(unresolved, /data-workflow-step-kind="rule"[^>]*>RULE<\/span>/);
  assert.doesNotMatch(unresolved, /data-workflow-step-kind="agent"|data-workflow-step-kind="skill"/);
});

test("unknown registry and unresolved kinds do not invent a badge", () => {
  const input = inspectorInput();
  const workflow = {
    ...input.workflow,
    steps: input.workflow.steps.map((step, index) => {
      if (index === 0) {
        return {
          ...step,
          authored_fields: { agent: "harnesskit.agent.plan-writer" },
          resolved_component_ids: ["harnesskit.agent.not-in-snapshot"],
        };
      }
      if (index === 5) {
        return {
          ...step,
          unresolved_references: [{
            source_field: "steps[5].agent",
            reference: "harnesskit.agent.missing",
            reference_kind: "component",
          }],
        };
      }
      return step;
    }),
  };
  const html = renderOrderedStepInspector({ ...input, workflow });

  assert.doesNotMatch(stepMarkup(html, 1), /data-workflow-step-kind=/);
  assert.doesNotMatch(stepMarkup(html, 6), /data-workflow-step-kind=/);
});

test("renderer owns only the ordered cards and leaves Workflow identity and technical disclosure to its parent", () => {
  const html = renderOrderedStepInspector(inspectorInput());
  assert.match(html, /^<ol\b[^>]*class="workflow-inspector__steps"/);
  assert.match(html, /aria-label="Review-gated implementation의 정의된 순서 · 6 steps"/);
  assert.doesNotMatch(html, /workflow-inspector__header|workflow-inspector__runtime-state/);
  assert.doesNotMatch(html, /workflow-inspector__definition-meta|workflow-inspector__definition/);
  assert.doesNotMatch(html, /Workflow 전체 보기|Raw YAML 보기/);
  assert.doesNotMatch(visibleText(html), /authored state|source|components\/workflows\//i);
  assert.equal(html.trimEnd().endsWith("</ol>"), true);
});

test("ordered card accessible labels use display names and kind vocabulary instead of canonical IDs", () => {
  const html = renderOrderedStepInspector(inspectorInput());
  const workflowId = "harnesskit.workflow.review-gated-implementation";
  const stepLabel = ariaLabelForId(html, `workflow-inspector-step:${workflowId}:2:implement`);

  assert.match(stepLabel, /2/);
  assert.match(stepLabel, /Implement/);
  assert.match(stepLabel, /AGENT/);
  const accessibleLabels = [...html.matchAll(/\saria-label="([^"]+)"/g)]
    .map((match) => match[1]);
  assert.equal(accessibleLabels.some((label) => label.includes("harnesskit.")), false);
  assert.doesNotMatch(html, /id="workflow-occurrence:|id="semantic-workflow-role:/);
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length);
});

test("incidence occurrence metadata cannot add a second order authority beside the cards", () => {
  const input = inspectorInput();
  const incidenceBundles = [{
    ...input.incidenceBundles[0],
    occurrences: [
      {
        ordinal: 2,
        step_id: "implement phase",
        source_field: "agent",
        role: "implementation/review",
      },
      {
        ordinal: 2,
        step_id: "implement phase",
        source_field: "skill|fallback",
        role: "implementation/review",
      },
    ],
  }];
  const baseline = renderOrderedStepInspector(input);
  const varied = renderOrderedStepInspector({ ...input, incidenceBundles });

  assert.equal(primaryStepsMarkup(varied), primaryStepsMarkup(baseline));
  assert.doesNotMatch(varied, /class="workflow-incidences"|workflow-occurrence:|semantic-workflow-role:/);
});

test("authored step AX identifier percent-encodes free-form step_id", () => {
  const input = inspectorInput();
  const workflow = {
    ...input.workflow,
    steps: input.workflow.steps.map((step, index) => index === 0
      ? { ...step, step_id: "quality review|β" }
      : step),
  };
  const html = renderOrderedStepInspector({ ...input, workflow });

  assert.ok(html.includes(
    `id="workflow-inspector-step:${workflow.workflow_id}:1:quality%20review%7C%CE%B2"`,
  ));
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(ids.some((id) => /\s/.test(id)), false);
});

test("Workflow technical metadata and raw source never leak into ordered cards", () => {
  const html = renderOrderedStepInspector(inspectorInput());
  const text = visibleText(html);

  assert.doesNotMatch(text, /approved|components\/workflows|runtime|raw yaml/i);
  assert.doesNotMatch(html, /window\.evil|<pre|<code|<details/);
  assert.doesNotMatch(text, /harnesskit\./);
});

test("runtime capability does not alter the ordered-card projection", () => {
  const input = inspectorInput();
  const baseline = renderOrderedStepInspector(input);
  const runtimeCapable = renderOrderedStepInspector({
    ...input,
    workflow: { ...input.workflow, runtime_implemented: true },
  });
  assert.equal(runtimeCapable, baseline);
});

test("step hover and lock actions remain graph-only and leave Matrix state byte-identical", () => {
  const root = new FakeRoot();
  const matrixState = deepFreeze({
    activeProfileId: "harnesskit.profile.engineering",
    selectedProfileId: "harnesskit.profile.engineering",
    expandedProfileId: "harnesskit.profile.engineering",
    profileMatrixExpanded: true,
    scrollTop: 240,
  });
  const matrixBytes = JSON.stringify(matrixState);
  const hovered = [];
  const selected = [];
  const binding = bindOrderedStepInspector(root, {
    onStepHover(value) {
      hovered.push(value);
    },
    onStepSelect(value) {
      selected.push(value);
    },
  });
  const input = inspectorInput();
  binding.sync(input);

  root.emit("pointerover", stepTarget(input.workflow.workflow_id, 2));
  root.emit("click", stepTarget(input.workflow.workflow_id, 2));

  assert.deepEqual(hovered, [{ workflowId: input.workflow.workflow_id, ordinal: 2 }]);
  assert.deepEqual(selected, [{ workflowId: input.workflow.workflow_id, ordinal: 2 }]);
  assert.equal(JSON.stringify(matrixState), matrixBytes);
  assert.equal(Object.hasOwn(hovered[0], "activeProfileId"), false);
  assert.equal(Object.hasOwn(selected[0], "expandedProfileId"), false);

  binding.release();
  binding.release();
  root.emit("click", stepTarget(input.workflow.workflow_id, 5));
  assert.equal(selected.length, 1);
});

test("step focus emits the same graph-only hover action as pointer hover", () => {
  const root = new FakeRoot();
  const hovered = [];
  const binding = bindOrderedStepInspector(root, {
    onStepHover(value) {
      hovered.push(value);
    },
  });
  const input = inspectorInput();
  binding.sync(input);
  const target = stepTarget(input.workflow.workflow_id, 3);

  root.emit("focusin", target);
  root.emit("focusout", target);

  assert.deepEqual(hovered, [
    { workflowId: input.workflow.workflow_id, ordinal: 3 },
    null,
  ]);
});

test("pointer and focus transitions within one step keep one stable hover action", () => {
  const root = new FakeRoot();
  const hovered = [];
  const binding = bindOrderedStepInspector(root, {
    onStepHover(value) {
      hovered.push(value);
    },
  });
  const input = inspectorInput();
  binding.sync(input);
  const targets = stepChildTargets(input.workflow.workflow_id, 4);

  root.emit("pointerover", targets.first, { relatedTarget: targets.outside });
  root.emit("pointerout", targets.first, { relatedTarget: targets.second });
  root.emit("pointerover", targets.second, { relatedTarget: targets.first });
  root.emit("pointerout", targets.second, { relatedTarget: targets.outside });
  root.emit("focusin", targets.first, { relatedTarget: targets.outside });
  root.emit("focusout", targets.first, { relatedTarget: targets.second });
  root.emit("focusin", targets.second, { relatedTarget: targets.first });
  root.emit("focusout", targets.second, { relatedTarget: targets.outside });

  assert.deepEqual(hovered, [
    { workflowId: input.workflow.workflow_id, ordinal: 4 },
    null,
    { workflowId: input.workflow.workflow_id, ordinal: 4 },
    null,
  ]);
});
