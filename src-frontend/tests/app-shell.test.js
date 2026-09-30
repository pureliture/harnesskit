import assert from "node:assert/strict";
import test from "node:test";

import {
  createInitialState,
  renderAppShell,
  workspaceLayoutStatusPresentation,
} from "../app-shell.js";
import { createLocalDataState, createLocalViewState } from "../local-state.js";
import { createSotViewState } from "../sot-view.js";

const authoringWorkflow = {
  workflow_id: "harnesskit.workflow.harness-creation",
  title: "Component Dev Guide",
  description: "요구사항을 확정한 뒤 한 번에 하나의 기능 단위를 설계·작성·검증합니다.",
  steps: [
    ["reference-mode", "참고자료 준비", "요청한 경우에만 참고자료를 정리합니다.", "harnesskit.agent.reference-curator", null, "reference-packet"],
    ["requirements", "요구사항 확정", "의도와 제약을 requirements.md에 정리합니다.", "harnesskit.agent.requirements-analyst", "harnesskit.skill.harness-requirements", "requirements.md"],
    ["blueprint", "청사진 작성", "컴포넌트 경계와 평가 기준을 정합니다.", "harnesskit.agent.harness-blueprint-author", "harnesskit.skill.harness-blueprint", "blueprint.md"],
    ["select-current-slice", "현재 기능 단위 선택", "끝까지 처리할 기능 단위 하나를 선택합니다.", null, null, "current capability slice packet"],
    ["canonical-authoring", "기준 컴포넌트 작성", "기준 컴포넌트 기록을 작성합니다.", "harnesskit.agent.component-author", "harnesskit.skill.component-authoring", "canonical component files"],
    ["adapter-authoring", "어댑터 산출물 작성", "대상 도구용 정적 계약을 작성합니다.", "harnesskit.agent.adapter-author", "harnesskit.skill.adapter-authoring", "adapter outputs"],
    ["evaluation", "근거 평가", "요구사항과 실행 근거를 평가합니다.", "harnesskit.agent.harness-evaluator", "harnesskit.skill.skill-evaluation", "evaluation.md"],
    ["advance-after-pass", "다음 기능 단위 진행", "PASS일 때만 다음 기능 단위를 선택합니다.", null, null, "next-slice eligibility"],
  ].map(([stepId, title, description, agent, skill, output], index) => ({
    workflow_id: "harnesskit.workflow.harness-creation",
    ordinal: index + 1,
    step_id: stepId,
    title,
    description,
    authored_fields: { agent, skill, output },
    resolved_component_ids: [agent, skill].filter(Boolean),
    unresolved_references: [],
    source_path: "components/workflows/harness-creation/workflow.yml",
  })),
};

const snapshot = {
  snapshot_id: "snapshot-fixture",
  checkout_summary: {
    source_revision: "source-fixture",
    canonical_path: "/private/secret/sot-checkout-root",
    branch: "main",
    detached: false,
    dirty: false,
    recent_commits: ["Add component atlas"],
  },
  components: [
    {
      component_id: "harnesskit.agent.router",
      kind: "agent",
      status: "draft",
      title: "Router",
      summary: "Routes approved workflow components.",
      domain: "core",
      targets: [{ target_id: "codex", support_status: "runtime_supported" }],
      provenance: { mode: "declared", source_classification: null },
      owned_files: ["components/agents/router/agent.yml"],
      profile_ids: ["harnesskit.profile.engineering"],
    },
    {
      component_id: "harnesskit.skill.alpha",
      kind: "skill",
      status: "draft",
      title: "Alpha",
      summary: "Fixture skill.",
      domain: "core",
      targets: [],
      provenance: { mode: "adapted", source_classification: null },
      owned_files: ["components/skills/alpha/SKILL.md"],
      profile_ids: ["harnesskit.profile.engineering"],
    },
    {
      component_id: "harnesskit.skill.planned",
      kind: "skill",
      status: "skeleton",
      title: "Planned",
      summary: null,
      domain: null,
      targets: [],
      provenance: {},
      owned_files: [],
      profile_ids: [],
    },
  ],
  profiles: [{
    profile_id: "harnesskit.profile.engineering",
    status: "draft",
    title: "Engineering",
    summary: "Engineering profile",
    component_ids: ["harnesskit.agent.router", "harnesskit.skill.alpha"],
  }],
  workflows: [authoringWorkflow],
  unprofiled_component_ids: ["harnesskit.skill.planned"],
  relations: [{
    source: "harnesskit.agent.router",
    target: "harnesskit.skill.alpha",
    relation_type: "router_workflow",
    source_path: "components/agents/router/agent.yml",
    source_field: "routes.workflows[0].component_id",
    declarative_only: true,
  }],
  graph_projection: {
    logical_width: 1200,
    logical_height: 460,
    view_box: [0, 0, 1200, 460],
    content_extent: { min_x: 18, min_y: 18, max_x: 270, max_y: 142 },
    nodes: [
      { node_type: "profile", node_id: "profile:harnesskit.profile.engineering", profile_id: "harnesskit.profile.engineering", name: "Engineering", member_count: 2, x: 48, y: 48, width: 216, height: 96, hit_width: 216, hit_height: 96 },
      { node_type: "unprofiled", node_id: "projection:unprofiled", name: "Unprofiled", member_count: 1, x: 48, y: 176, width: 216, height: 96, hit_width: 216, hit_height: 96 },
      { node_type: "component", node_id: "component:harnesskit.agent.router", component_id: "harnesskit.agent.router", kind: "agent", domain: "core", relation_degree: 1, profile_ids: ["harnesskit.profile.engineering"], x: 336, y: 48, width: 64, height: 28, hit_width: 72, hit_height: 48 },
      { node_type: "component", node_id: "component:harnesskit.skill.alpha", component_id: "harnesskit.skill.alpha", kind: "skill", domain: "core", relation_degree: 1, profile_ids: ["harnesskit.profile.engineering"], x: 432, y: 48, width: 64, height: 28, hit_width: 72, hit_height: 48 },
      { node_type: "component", node_id: "component:harnesskit.skill.planned", component_id: "harnesskit.skill.planned", kind: "skill", domain: "unknown", relation_degree: 0, profile_ids: [], x: 336, y: 176, width: 64, height: 28, hit_width: 72, hit_height: 48 },
    ],
    edges: [
      { semantic: "membership", edge_id: "membership-0", profile_node_id: "profile:harnesskit.profile.engineering", component_id: "harnesskit.agent.router", provenance: "CanonicalProfile" },
      { semantic: "membership", edge_id: "membership-1", profile_node_id: "profile:harnesskit.profile.engineering", component_id: "harnesskit.skill.alpha", provenance: "CanonicalProfile" },
      { semantic: "membership", edge_id: "membership-2", profile_node_id: "projection:unprofiled", component_id: "harnesskit.skill.planned", provenance: "DerivedUnprofiled" },
      { semantic: "relation", edge_id: "relation-0", source_component_id: "harnesskit.agent.router", target_component_id: "harnesskit.skill.alpha", relation_type: "router_workflow", provenance: { source_path: "components/agents/router/agent.yml", source_field: "routes.workflows[0].component_id", declarative_only: true } },
    ],
  },
  navigation_projection: { all_component_ids: [], groups: [] },
  issues: [],
};

function readyState(overrides = {}) {
  return createInitialState({
    repo: {
      phase: "registered",
      checkoutId: "checkout-7",
      checkoutPath: "/private/tmp/harnesskit",
      canonicalPath: "/private/tmp/harnesskit",
    },
    sot: { phase: "ready", snapshot, message: "3개 component를 불러왔습니다." },
    sotView: createSotViewState(snapshot),
    ...overrides,
  });
}

test("command bar exposes one persistent System Light Dark appearance control", () => {
  const markup = renderAppShell(createInitialState({
    appearance: { logical_mode: "Light", resolved_mode: "Light", revision: 3, persisted: true },
  }));

  assert.match(markup, /<h1>HarnessKit<\/h1>/);
  assert.doesNotMatch(markup, /Harness Desktop/);
  assert.match(markup, /<fieldset[^>]*class="appearance-control"/);
  assert.equal((markup.match(/name="appearance-mode"/g) ?? []).length, 3);
  assert.match(markup, /value="System"/);
  assert.match(markup, /value="Light"[^>]* checked/);
  assert.match(markup, /value="Dark"/);
  assert.match(markup, /class="appearance-save-state"[^>]* hidden/);
});

test("workspace layout publication exposes accessible completion revision and visible failure", () => {
  assert.deepEqual(workspaceLayoutStatusPresentation({
    revision: 4,
    persisted: true,
    diagnostic: null,
  }), {
    revision: 4,
    hidden: false,
    visuallyHidden: true,
    text: "레이아웃 설정 저장 완료 · revision 4",
  });
  assert.deepEqual(workspaceLayoutStatusPresentation({
    revision: 5,
    persisted: false,
    diagnostic: { safe_message: "레이아웃 설정 저장 실패" },
  }), {
    revision: 5,
    hidden: false,
    visuallyHidden: false,
    text: "레이아웃 설정 저장 실패",
  });
  assert.equal(workspaceLayoutStatusPresentation({ revision: 0, persisted: true }).hidden, true);

  const markup = renderAppShell(createInitialState({
    workspaceLayout: { revision: 4, persisted: true },
  }));
  assert.match(markup, /class="workspace-layout-save-state sr-only"[^>]*data-layout-revision="4"[^>]*>레이아웃 설정 저장 완료 · revision 4/);
});

test("workspace shell exposes the authoritative preferred panel widths to assistive technology", () => {
  const markup = renderAppShell(createInitialState({
    workspaceLayout: {
      preferredLeftWidthPx: 500,
      preferredRightWidthPx: 550,
    },
  }));

  assert.match(
    markup,
    /<span id="workspace-preferred-widths" class="sr-only" aria-label="선호 패널 너비 좌측 500픽셀, 우측 550픽셀">선호 패널 너비 좌측 500픽셀, 우측 550픽셀<\/span>/,
  );
});

test("Local footer exposes stable sanitized runtime status and snapshot anchors", () => {
  const markup = renderAppShell(createInitialState({
    ui: { activeSegment: "local" },
    localData: createLocalDataState({
      message: "현재 스캔 완료 후 제외 규칙 반영 스캔을 실행합니다.",
      latestComplete: {
        snapshotId: "snapshot-safe-prefix-opaque-tail",
        attemptId: "attempt-private",
        status: "complete",
      },
    }),
  }));

  assert.match(markup, /id="local-runtime-status"[^>]*>현재 스캔 완료 후 제외 규칙 반영 스캔을 실행합니다\./);
  assert.match(markup, /id="local-runtime-snapshot"[^>]*>snapshot snapshot-saf/);
  const footer = markup.match(/<footer class="app-statusbar"[\s\S]*?<\/footer>/)?.[0] ?? "";
  assert.doesNotMatch(footer, /attempt-private|opaque-tail/);
});

test("initial shell exposes the approved SoT unavailable boundary without success claims", () => {
  const markup = renderAppShell(createInitialState());

  assert.match(markup, /<header class="command-bar"/);
  assert.match(markup, /data-dashboard-segment="sot">Harness Components<\/button>/);
  assert.match(markup, /PC 설치 하네스/);
  assert.doesNotMatch(markup, /SoT 명세 대시보드/);
  assert.doesNotMatch(markup, /로컬 PC 설치 대시보드/);
  assert.match(markup, /aria-labelledby="tree-title"/);
  assert.match(markup, /aria-labelledby="sot-segment"/);
  assert.match(markup, /aria-labelledby="detail-title"/);
  assert.match(markup, /SoT 명세를 보려면 HarnessKit 저장소가 필요합니다/);
  assert.match(markup, /기존 폴더를 선택하거나 새로 내려받으세요/);
  assert.match(markup, /기존 HarnessKit 폴더 선택/);
  assert.match(markup, /HarnessKit 저장소 내려받기/);
  assert.match(markup, /공개 저장소를 앱 전용 폴더에 내려받아 SoT로 연결합니다/);
  assert.match(markup, /class="sot-unavailable-action"[\s\S]*기존 HarnessKit 폴더 선택/);
  assert.match(markup, /class="sot-unavailable-action"[\s\S]*HarnessKit 저장소 내려받기[\s\S]*<small>/);
  assert.equal((markup.match(/id="clone-checkout"/g) ?? []).length, 1);
  assert.match(markup, /id="typography-menu-trigger"[^>]*aria-haspopup="menu"[^>]*aria-expanded="false"[^>]*aria-controls="typography-menu"[^>]*aria-label="글자 크기: 기본"/);
  assert.match(markup, /id="typography-menu"[^>]*role="menu"[^>]*hidden/);
  assert.equal((markup.match(/role="menuitemradio"/g) ?? []).length, 3);
  assert.doesNotMatch(markup, /<select[^>]*name="typography-preset"/);
  assert.match(markup, /id="register-checkout"[^>]*aria-label="HarnessKit 폴더 연결"[^>]*title="HarnessKit 폴더 연결"[^>]*><span class="repo-folder-glyph" aria-hidden="true"><\/span><\/button>/);
  assert.doesNotMatch(markup, /id="load-sot"|HarnessKit 연결<\/button>/);
  assert.match(markup, /HarnessKit 저장소의 컴포넌트 명세를 읽기 전용으로 보여줍니다\. 이 화면에서는 파일을 설치하거나 변경하지 않습니다\./);
  assert.doesNotMatch(markup, /HarnessKit authoring flow|Registry와 canonical manifest|Build\/install/);
  assert.doesNotMatch(markup, /Scan 완료|snapshot loaded|local runtime · deterministic/i);
});

test("ready dashboard row places one refresh icon immediately before Component Dev Guide", () => {
  const markup = renderAppShell(readyState());

  assert.match(markup, /class="dashboard-navigation"[\s\S]*class="dashboard-segments"[\s\S]*id="dashboard-refresh"[^>]*><span class="dashboard-refresh__glyph" aria-hidden="true"><\/span><\/button>\s*<button[^>]*data-open-authoring-flow[^>]*>Component Dev Guide<\/button>/);
  assert.equal((markup.match(/id="dashboard-refresh"/g) ?? []).length, 1);
  assert.doesNotMatch(markup, /authoring-flow-launch/);
  assert.match(markup, /data-open-authoring-flow[^>]*>Component Dev Guide<\/button>/);
  assert.doesNotMatch(markup, /HarnessKit 제작 흐름/);
  assert.doesNotMatch(markup, /HarnessKit authoring flow/);
  assert.doesNotMatch(markup, /data-authoring-flow-dialog/);
});

test("ready Local dashboard keeps the same enabled authoring action and modal host", () => {
  const localMarkup = renderAppShell(readyState({ ui: { activeSegment: "local" } }));
  const localDialogMarkup = renderAppShell(readyState({
    ui: { activeSegment: "local", authoringFlowOpen: true },
  }));

  assert.match(localMarkup, /data-open-authoring-flow>Component Dev Guide<\/button>/);
  assert.doesNotMatch(localMarkup, /data-open-authoring-flow disabled/);
  assert.match(localMarkup, /id="dashboard-refresh"[^>]*aria-label="PC 설치 하네스 새로고침"/);
  assert.match(localMarkup, /id="local-workspace" class="local-workspace"/);
  assert.doesNotMatch(localMarkup, /authoring-flow-launch/);
  assert.match(localDialogMarkup, /data-authoring-flow-dialog[^>]*role="dialog"/);
  assert.match(localDialogMarkup, /<h2 id="authoring-flow-title">Component Dev Guide<\/h2>/);
  assert.doesNotMatch(localDialogMarkup, /HarnessKit 제작 흐름/);
});

test("authoring modal presents the current eight-step workflow in Korean with progressive details", () => {
  const markup = renderAppShell(readyState({
    ui: { authoringFlowOpen: true },
  }));

  assert.match(markup, /data-authoring-flow-dialog[^>]*role="dialog"[^>]*aria-modal="true"/);
  assert.match(markup, /컴포넌트 제작 안내 · 8단계/);
  assert.match(markup, /<h2 id="authoring-flow-title">Component Dev Guide<\/h2>/);
  assert.doesNotMatch(markup, /HarnessKit 제작 흐름/);
  assert.match(markup, /한 번에 하나의 기능 단위를 설계·작성·검증/);
  assert.equal((markup.match(/class="authoring-step"/g) ?? []).length, 8);
  for (const stepId of ["reference-mode", "requirements", "blueprint", "select-current-slice", "canonical-authoring", "adapter-authoring", "evaluation", "advance-after-pass"]) {
    assert.match(markup, new RegExp(`data-authoring-step="${stepId}"`));
  }
  assert.match(markup, /<details class="authoring-step-details"><summary>담당과 산출물<\/summary><dl>/);
  assert.doesNotMatch(markup, /<details class="authoring-step-details"[^>]*\sopen(?:\s|>)/);
  assert.match(markup, /<dt>에이전트<\/dt>/);
  assert.match(markup, /<dt>스킬<\/dt>/);
  assert.match(markup, /<dt>산출물<\/dt>/);
  assert.match(markup, /<dd>harnesskit\.agent\.reference-curator<\/dd>/);
  assert.match(markup, /<dd>harnesskit\.agent\.harness-evaluator<\/dd>/);
  assert.match(markup, /<dd>harnesskit\.skill\.harness-requirements<\/dd>/);
  assert.doesNotMatch(markup, /<dd>Reference Curator<\/dd>/);
  assert.doesNotMatch(markup, /authoring-field-badge|>Agent<|>Skill<|>Output</);
  assert.match(markup, /data-close-authoring-flow/);
  assert.doesNotMatch(markup, /components\/workflows\/harness-creation\/workflow\.yml/);
});

test("authoring modal reads the canonical workflow title and falls back for snapshots without one", () => {
  const titledSnapshot = structuredClone(snapshot);
  titledSnapshot.workflows[0].title = "Canonical Workflow Title";
  const titledMarkup = renderAppShell(readyState({
    sot: { phase: "ready", snapshot: titledSnapshot },
    ui: { authoringFlowOpen: true },
  }));
  const untitledSnapshot = structuredClone(snapshot);
  delete untitledSnapshot.workflows[0].title;
  const untitledMarkup = renderAppShell(readyState({
    sot: { phase: "ready", snapshot: untitledSnapshot },
    ui: { authoringFlowOpen: true },
  }));

  assert.match(titledMarkup, /<h2 id="authoring-flow-title">Canonical Workflow Title<\/h2>/);
  assert.match(untitledMarkup, /<h2 id="authoring-flow-title">Component Dev Guide<\/h2>/);
});

test("authoring details preserve both an authored component title and its canonical ID", () => {
  const titledSnapshot = structuredClone(snapshot);
  titledSnapshot.components.push({
    component_id: "harnesskit.agent.reference-curator",
    title: "Reference Curator",
  });
  const markup = renderAppShell(readyState({
    sot: { phase: "ready", snapshot: titledSnapshot },
    ui: { authoringFlowOpen: true },
  }));

  assert.match(markup, /<dd>Reference Curator · harnesskit\.agent\.reference-curator<\/dd>/);
});

test("Local dashboard keeps authoring guidance out of its body while retaining the disabled global action", () => {
  const markup = renderAppShell(createInitialState({ ui: { activeSegment: "local" } }));

  assert.match(markup, /PC 설치 하네스/);
  assert.match(markup, /<p class="eyebrow">PC 설치 하네스<\/p><h2 id="detail-title">하네스 상세<\/h2>/);
  assert.match(markup, /data-open-authoring-flow disabled>Component Dev Guide<\/button>/);
  assert.doesNotMatch(markup, /LOCAL EVIDENCE|Local evidence|Instance detail|HarnessKit authoring flow|authoring-flow-launch|data-authoring-flow-dialog/);
});

test("dashboard refresh icon names its SoT action and exposes unavailable and loading states", () => {
  const idleMarkup = renderAppShell(readyState());
  const loadingMarkup = renderAppShell(readyState({
    sot: { phase: "loading", snapshot: null, message: "SoT snapshot을 읽고 있습니다." },
  }));
  const disconnectedMarkup = renderAppShell(createInitialState());
  const idleControl = idleMarkup.match(/<button id="dashboard-refresh"[\s\S]*?<\/button>/)?.[0] ?? "";
  const loadingControl = loadingMarkup.match(/<button id="dashboard-refresh"[\s\S]*?<\/button>/)?.[0] ?? "";
  const disconnectedControl = disconnectedMarkup.match(/<button id="dashboard-refresh"[\s\S]*?<\/button>/)?.[0] ?? "";

  assert.match(idleControl, /aria-label="Harness Components 새로고침"[^>]*title="Harness Components 새로고침"/);
  assert.match(idleControl, /<span class="dashboard-refresh__glyph" aria-hidden="true"><\/span>/);
  assert.doesNotMatch(idleControl, / disabled|새로고침<\/button>/);
  assert.match(loadingControl, /aria-label="Harness Components 새로고침 중"[^>]*disabled/);
  assert.match(disconnectedControl, /aria-label="Harness Components 새로고침"[^>]*disabled/);
  for (const markup of [idleMarkup, loadingMarkup, disconnectedMarkup]) {
    assert.doesNotMatch(markup, /id="load-sot"|>SoT 새로고침<\/button>/);
  }
});

test("Local refresh stays available without a checkout but names and blocks a pending or running scan", () => {
  const localState = { ui: { activeSegment: "local" } };
  const idleMarkup = renderAppShell(createInitialState(localState));
  const pendingMarkup = renderAppShell(createInitialState({
    ...localState,
    localData: { scanStartPending: true, message: "Local scan 시작을 요청하고 있습니다." },
  }));
  const runningMarkup = renderAppShell(createInitialState({
    ...localState,
    localData: { phase: "running", currentAttempt: { attemptId: "attempt-7", state: "running" } },
  }));
  const failedMarkup = renderAppShell(createInitialState({
    ...localState,
    localData: { phase: "error", message: "최근 스캔이 실패했습니다." },
  }));
  const retryMarkup = renderAppShell(createInitialState({
    ...localState,
    localData: {
      phase: "error",
      scanStartPending: true,
      message: "최근 스캔이 실패했습니다.",
    },
  }));
  const refresh = (markup) => markup.match(/<button id="dashboard-refresh"[\s\S]*?<\/button>/)?.[0] ?? "";

  assert.match(refresh(idleMarkup), /aria-label="PC 설치 하네스 새로고침"/);
  assert.doesNotMatch(refresh(idleMarkup), / disabled/);
  for (const markup of [pendingMarkup, runningMarkup]) {
    assert.match(refresh(markup), /aria-label="PC 설치 하네스 새로고침 중"[^>]*aria-busy="true"[^>]*disabled/);
  }
  assert.match(pendingMarkup, /id="local-runtime-status"[^>]*>Local scan 시작을 요청하고 있습니다\./);
  assert.match(refresh(failedMarkup), /aria-label="PC 설치 하네스 새로고침"/);
  assert.doesNotMatch(refresh(failedMarkup), / disabled/);
  assert.match(failedMarkup, /최근 스캔이 실패했습니다/);
  assert.match(retryMarkup, /data-local-status-stack><p class="notice " role="status">Local scan 시작을 요청하고 있습니다\./);
  assert.doesNotMatch(retryMarkup, /state-dot--error/);
});

test("SoT inventory status follows the latest snapshot lifecycle instead of a stale checkout message", () => {
  const registeringMessage = "Checkout이 등록되었습니다. SoT snapshot을 구성합니다.";
  const loadingMarkup = renderAppShell(readyState({
    repo: { checkoutId: "checkout-7", phase: "registered", message: registeringMessage },
    sot: { phase: "loading", snapshot: null, message: "SoT snapshot을 읽고 있습니다." },
  }));
  const readyMarkup = renderAppShell(readyState({
    repo: { checkoutId: "checkout-7", phase: "registered", message: registeringMessage },
    sot: { phase: "ready", snapshot, message: "3개 component를 불러왔습니다." },
  }));
  const errorMarkup = renderAppShell(readyState({
    repo: { checkoutId: "checkout-7", phase: "registered", message: registeringMessage },
    sot: { phase: "error", snapshot: null, message: "SoT snapshot을 불러오지 못했습니다." },
  }));

  assert.match(loadingMarkup, /class="inventory-state"[^>]*role="status"[^>]*aria-live="polite"[\s\S]*SoT snapshot을 읽고 있습니다\./);
  assert.match(readyMarkup, /inventory-state-text">3개 component를 불러왔습니다\./);
  assert.match(errorMarkup, /state-dot--error[\s\S]*SoT snapshot을 불러오지 못했습니다\./);
  for (const markup of [loadingMarkup, readyMarkup, errorMarkup]) {
    assert.doesNotMatch(markup, new RegExp(registeringMessage));
  }
});

test("header offers an offline open-source license link to the bundled notice", () => {
  const markup = renderAppShell(createInitialState());
  const toolbar = markup.match(/<header class="command-bar"[\s\S]*?<\/header>/)?.[0] ?? "";

  assert.match(
    toolbar,
    /<a(?=[^>]*class="license-link")(?=[^>]*href="\.\/legal\/third-party-notices\.html")(?=[^>]*target="_self")[^>]*>오픈소스 라이선스<\/a>/,
  );
  assert.doesNotMatch(toolbar, /href="https?:[^"]*third-party/);
});

test("global toolbar DOM order stays aligned with responsive visual reading order", () => {
  const markup = renderAppShell(createInitialState());
  const toolbar = markup.match(/<header class="command-bar"[\s\S]*?<\/header>/)?.[0] ?? "";
  const orderedAnchors = [
    'id="left-pane-disclosure"',
    'class="app-identity"',
    'id="repo-form"',
    'class="command-meta"',
    'id="right-pane-disclosure"',
  ];
  const positions = orderedAnchors.map((anchor) => toolbar.indexOf(anchor));

  assert.ok(positions.every((position) => position >= 0));
  assert.deepEqual(positions, [...positions].sort((left, right) => left - right));
});

test("collapsed side panes retain stable hidden inert targets while removing disabled dividers", () => {
  const markup = renderAppShell(createInitialState({
    workspaceLayout: { leftCollapsed: true, rightCollapsed: true },
  }));

  assert.match(markup, /data-left-collapsed="true"[^>]*data-right-collapsed="true"/);
  assert.match(markup, /<button(?=[^>]*data-pane-disclosure="left")(?=[^>]*aria-label="탐색 패널 열기")(?=[^>]*aria-expanded="false")(?=[^>]*aria-controls="workspace-left-pane")/);
  assert.match(markup, /<button(?=[^>]*data-pane-disclosure="right")(?=[^>]*aria-label="상세 패널 열기")(?=[^>]*aria-expanded="false")(?=[^>]*aria-controls="workspace-right-pane")/);
  assert.match(markup, /id="workspace-left-pane"[^>]*hidden[^>]*inert/);
  assert.match(markup, /id="workspace-right-pane"[^>]*hidden[^>]*inert/);
  assert.doesNotMatch(markup, /data-workspace-divider="left"|data-workspace-divider="right"/);
  assert.match(markup, /id="workbench"/);
});

test("pane disclosures keep the same split-panel glyph across open and closed states", () => {
  const opened = renderAppShell(createInitialState());
  const collapsed = renderAppShell(createInitialState({
    workspaceLayout: { leftCollapsed: true, rightCollapsed: true },
  }));
  const buttonMarkup = (markup, side) => markup.match(new RegExp(
    `<button[^>]*data-pane-disclosure="${side}"[\\s\\S]*?<\\/button>`,
  ))?.[0] ?? "";

  for (const markup of [opened, collapsed]) {
    for (const side of ["left", "right"]) {
      const button = buttonMarkup(markup, side);
      assert.match(button, /<span class="pane-disclosure__glyph" aria-hidden="true"><\/span>/);
      assert.doesNotMatch(button, /[‹›←→]/);
    }
  }
  assert.match(buttonMarkup(opened, "left"), /aria-label="탐색 패널 닫기"[^>]*aria-expanded="true"/);
  assert.match(buttonMarkup(collapsed, "left"), /aria-label="탐색 패널 열기"[^>]*aria-expanded="false"/);
  assert.match(buttonMarkup(opened, "right"), /aria-label="상세 패널 닫기"[^>]*aria-expanded="true"/);
  assert.match(buttonMarkup(collapsed, "right"), /aria-label="상세 패널 열기"[^>]*aria-expanded="false"/);
});

test("desktop shell preserves the left panel and three persistent work panes", () => {
  const markup = renderAppShell(createInitialState());

  assert.match(markup, /<div class="desktop-shell"[^>]*data-workspace-shell[^>]*data-layout-mode="three-pane"/);
  assert.match(markup, /<aside id="workspace-left-pane" class="pane pane--inventory"/);
  assert.match(markup, /<main id="workbench" class="pane pane--workbench"[^>]*aria-label="중앙 작업 영역"/);
  assert.match(markup, /<aside id="workspace-right-pane" class="pane pane--inspector"/);
  assert.match(markup, /role="separator"[^>]*data-workspace-divider="left"[^>]*aria-controls="workspace-left-pane"/);
  assert.match(markup, /role="separator"[^>]*data-workspace-divider="right"[^>]*aria-controls="workspace-right-pane"/);
  assert.match(markup, /data-workspace-divider="left"[^>]*aria-valuetext="304픽셀"/);
  assert.match(markup, /data-workspace-divider="right"[^>]*aria-valuetext="368픽셀"/);
  assert.match(markup, /data-workspace-divider="left"[^>]*aria-valuemin="200"/);
  assert.match(markup, /data-workspace-divider="right"[^>]*aria-valuemin="184"/);
  assert.match(
    markup,
    /id="workspace-left-pane"[\s\S]*data-workspace-divider="left"[\s\S]*id="workbench"[\s\S]*data-workspace-divider="right"[\s\S]*id="workspace-right-pane"/,
  );
  assert.match(markup, /<footer class="app-statusbar"/);
  assert.equal((markup.match(/<main[\s>]/g) ?? []).length, 1);
  assert.doesNotMatch(markup, /class="repo-panel"|class="workspace"/);
});

test("ready SoT UI never renders the snapshot checkout absolute path", () => {
  const markup = renderAppShell(readyState());

  assert.doesNotMatch(markup, /\/private\/secret\/sot-checkout-root/);
  assert.doesNotMatch(markup, /<dt>Canonical path<\/dt>/);
  assert.match(markup, /value="\/private\/tmp\/harnesskit"/);
});

test("registered SoT UI keeps the allowed checkout input without duplicating its canonical path", () => {
  const markup = renderAppShell(createInitialState({
    repo: {
      phase: "registered",
      checkoutId: "checkout-7",
      checkoutPath: "/private/tmp/user-selected-harnesskit",
      canonicalPath: "/private/secret/canonicalized-checkout-root",
      repoStatus: { branch: "main", detached: false, dirty: false, recent_commits: [] },
    },
  }));

  assert.match(markup, /value="\/private\/tmp\/user-selected-harnesskit"/);
  assert.doesNotMatch(markup, /\/private\/secret\/canonicalized-checkout-root/);
  assert.doesNotMatch(markup, /<dt>Canonical path<\/dt>/);
});

test("dashboard segments expose exactly one selected panel", () => {
  const sot = renderAppShell(createInitialState());
  const local = renderAppShell(createInitialState({ ui: { activeSegment: "local" } }));

  assert.match(sot, /id="sot-segment"[^>]*aria-selected="true"[^>]*tabindex="0"/);
  assert.match(sot, /id="local-segment"[^>]*aria-selected="false"[^>]*tabindex="-1"/);
  assert.match(sot, /id="sot-workspace"[^>]*aria-labelledby="sot-segment"/);
  assert.match(local, /id="sot-segment"[^>]*aria-selected="false"[^>]*tabindex="-1"/);
  assert.match(local, /id="local-segment"[^>]*aria-selected="true"[^>]*tabindex="0"/);
  assert.match(local, /id="local-workspace"/);
  assert.match(local, /id="sot-workspace"[^>]* hidden/);
});

test("fresh SoT workbench gives the expanded map the first viewport and keeps only the collapsed matrix header visible", () => {
  const markup = renderAppShell(readyState());

  assert.match(
    markup,
    /id="sot-workbench-first-viewport"[^>]*class="sot-workbench__first-viewport"[^>]*role="group"[^>]*aria-label="Component Map first viewport"/,
    "Component Map and the collapsed Profile Matrix header must share the first viewport",
  );
  assert.match(markup, /data-sot-graph-toggle[^>]*aria-expanded="true"[^>]*aria-controls="component-map-body"/);
  assert.match(markup, /data-sot-matrix-toggle[^>]*aria-expanded="false"[^>]*aria-controls="profile-matrix-body"/);
  assert.match(markup, /id="profile-matrix-body"[^>]* hidden/);
});

test("ready SoT state renders the persistent 3D host plus fixed graph matrix and inspector", () => {
  const state = readyState({
    sotView: {
      ...createSotViewState(snapshot),
      selectedComponentId: "harnesskit.skill.alpha",
    },
  });
  const markup = renderAppShell(state);

  assert.equal((markup.match(/data-component-map-scene-host/g) ?? []).length, 1);
  assert.match(markup, /5 nodes · 0 links/);
  assert.doesNotMatch(markup, /<svg\b|data-graph-scene=/);
  assert.match(markup, /Component Map/);
  assert.match(markup, /Profile Matrix/);
  assert.match(markup, /profile-panel/);
  assert.match(markup, /profile-member-region/);
  assert.match(markup, /harnesskit\.skill\.alpha/);
  assert.match(markup, /components\/skills\/alpha\/SKILL\.md/);
  assert.ok(markup.includes("/private/tmp/harnesskit"));
  assert.match(markup, /Recent commits/);
  assert.match(markup, /Add component atlas/);
  assert.match(markup, /id="dashboard-refresh"[^>]*aria-label="Harness Components 새로고침"/);
  assert.match(markup, /data-sot-install/);
  assert.match(markup, /id="install-form"/);
  assert.match(markup, /id="preview-install"/);
  assert.match(markup, /data-show-local-install="harnesskit\.skill\.alpha"/);
  assert.doesNotMatch(markup, /id="scan-button"/);
});

test("SoT issues keep their summary and count ahead of the bounded detail list", () => {
  const issueSnapshot = {
    ...snapshot,
    issues: [
      { code: "missing_manifest", source_path: "components/<unsafe>.yml", safe_message: "Manifest 없음" },
      { code: "scan_unreadable", source_path: "components/blocked.yml", safe_message: "경로를 읽을 수 없습니다." },
    ],
  };
  const markup = renderAppShell(readyState({
    sot: { phase: "ready", snapshot: issueSnapshot, message: "일부 source issue가 있습니다." },
    sotView: createSotViewState(issueSnapshot),
  }));
  const statusStack = markup.match(/<div class="status-stack"[^>]*>([\s\S]*?)<\/div>/)?.[1] ?? "";
  const issueNotice = markup.match(/<section class="notice notice--error inventory-issues"[\s\S]*?<\/section>/)?.[0] ?? "";

  assert.match(issueNotice, /일부 registry 또는 scan 경로를 처리하지 못했습니다\./);
  assert.match(issueNotice, /2개 문제/);
  assert.match(issueNotice, /<details class="inventory-issues__details">/);
  assert.equal(issueNotice.includes("<details class=\"inventory-issues__details\" open"), false);
  assert.match(markup, /state-dot--warning[\s\S]*inventory-state-text">일부 registry 또는 scan 경로를 처리하지 못했습니다\. 2개 문제/);
  assert.equal((markup.match(/state-dot--ready/g) ?? []).length, 0);
  assert.doesNotMatch(markup, /0개 registry component를 불러왔습니다\./);
  assert.doesNotMatch(statusStack, /registry 또는 scan 경로/);
  assert.ok(markup.indexOf('class="inventory-issues"') < markup.indexOf("data-sot-tree"));
  assert.match(markup, /missing_manifest/);
  assert.match(markup, /components\/&lt;unsafe&gt;\.yml/);
  assert.match(markup, /role="alert"/);
  assert.doesNotMatch(markup, /components\/<unsafe>\.yml/);
});

test("SoT load failure is an alert and never rewritten as success", () => {
  const markup = renderAppShell(createInitialState({
    repo: { checkoutId: "checkout-7", phase: "registered" },
    sot: { phase: "error", message: "Registry source가 유효하지 않습니다.", snapshot: null },
  }));

  assert.match(markup, /role="alert"/);
  assert.match(markup, /Registry source가 유효하지 않습니다/);
  assert.doesNotMatch(markup, /불러왔습니다|완료/);
});

test("Local segment preserves checkout independence and mounts the fixed explorer and search surface", () => {
  const markup = renderAppShell(createInitialState({ ui: { activeSegment: "local" } }));
  const localInspector = markup.match(/<div class="inspector-content"[^>]*data-local-inspector[^>]*>/)?.[0] ?? "";

  assert.match(markup, /Local Harness Atlas/);
  assert.match(markup, /id="local-search"/);
  assert.match(markup, /role="tree" aria-label="Local scan location"/);
  assert.match(markup, /data-local-scope="all"/);
  assert.match(markup, /Checkout-independent scan/);
  assert.doesNotMatch(localInspector, /aria-live/);
  assert.doesNotMatch(markup, /id="scan-button"/);
  assert.doesNotMatch(markup, /id="apply-install"|id="preview-install"/);
});

test("Local ready state renders tool filters, compact results, and the scan evidence inspector", () => {
  const queryResult = {
    snapshotId: "snapshot-local-shell",
    qualifiedTools: [{ toolId: "codex", label: "Codex" }],
    projects: [{ projectId: "project-shell", label: "Routine Harness" }],
    items: [{
      instanceId: "instance-shell",
      adapterId: "codex-builtin",
      adapterVersion: "1.0.0",
      toolId: "codex",
      surfaceId: "project-skills",
      scope: "project",
      projectId: "project-shell",
      safeLocator: ".agents/skills/local/SKILL.md",
      kind: "skill",
      displayName: "Local Skill",
      name: "Local Skill",
      parseState: "parsed",
      issueCodes: [],
      correlation: { state: "uncorrelated" },
    }],
    kindCounts: [{ kind: "skill", count: 1 }],
    snapshotSummary: {
      scanTimestamp: "2026-07-12T00:00:00Z",
      coverage: [{ adapterId: "codex-builtin", status: "complete", presence: "present", itemCount: 1 }],
      skippedPaths: [],
      issues: [],
    },
  };
  const markup = renderAppShell(createInitialState({
    ui: { activeSegment: "local" },
    localData: createLocalDataState({
      phase: "ready",
      message: "로컬 하네스 snapshot을 사용할 수 있습니다.",
      latestComplete: {
        snapshotId: "snapshot-local-shell",
        attemptId: "attempt-local-shell",
        status: "complete",
      },
      latestTerminalReport: {
        attemptId: "attempt-local-shell",
        state: "complete",
      },
      queryResult,
    }),
    localView: createLocalViewState(),
  }));

  assert.equal((markup.match(/data-local-tool=/g) ?? []).length, 2);
  assert.equal((markup.match(/data-local-instance=/g) ?? []).length, 1);
  assert.match(markup, /Local Skill/);
  assert.match(markup, /Coverage/);
  assert.match(markup, /snapshot-local-shell/);
  assert.match(markup, /<span class="count">1<\/span>/);
  assert.match(markup, /data-local-inspector/);
});
