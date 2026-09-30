import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

import { createLocalDataState, createLocalViewState } from "../local-state.js";
import {
  createLocalRemovalState,
  enterLocalRemovalSelectionMode,
  toggleLocalRemovalSelection,
} from "../local-removal-state.js";
import {
  localResultCount,
  renderLocalExplorer,
  renderLocalInspector,
  renderLocalProjectRows,
  renderLocalWorkbench,
  verifiedSotComponentId,
} from "../local-view.js";

const moduleUrl = new URL("../local-view.js", import.meta.url);

test("Local dashboard owns an isolated view module", () => {
  assert.equal(existsSync(moduleUrl), true);
});

const queryResult = {
  snapshotId: "snapshot-local-7",
  qualifiedTools: [
    { toolId: "codex", label: "Codex" },
    { toolId: "claude_code", label: "Claude Code" },
  ],
  projects: [
    {
      projectId: "project-safe-7",
      label: "routine-harness",
      canonicalPath: "/fixture-home/Projects/routine-harness",
    },
  ],
  items: [
    {
      instanceId: "instance-skill",
      adapterId: "codex-builtin",
      adapterVersion: "1.0.0",
      toolId: "codex",
      surfaceId: "project-skills",
      scope: "project",
      projectId: "project-safe-7",
      safeLocator: ".agents/skills/release/SKILL.md",
      kind: "skill",
      displayName: "Release <Safety>",
      name: "Release <Safety>",
      description: "Approved release workflow",
      descriptionSource: "frontmatter",
      parseState: "parsed",
      issueCodes: [],
      correlation: { state: "verified", componentId: "harnesskit.skill.release" },
    },
    {
      instanceId: "instance-hook",
      adapterId: "claude-settings",
      adapterVersion: "2.0.0",
      toolId: "claude_code",
      surfaceId: "user-hooks",
      scope: "user",
      projectId: null,
      safeLocator: ".claude/settings.json#/hooks/pre_tool",
      kind: "hook",
      displayName: "Pre Tool Guard",
      name: "Pre Tool Guard",
      description: null,
      descriptionSource: null,
      parseState: "parsed",
      issueCodes: ["missing_description"],
      correlation: { state: "uncorrelated" },
    },
  ],
  kindCounts: [
    { kind: "skill", count: 1 },
    { kind: "hook", count: 1 },
    { kind: "agent", count: 0 },
  ],
  counts: { totalInstances: 2, matchedInstances: 2 },
  snapshotSummary: {
    status: "complete",
    scanTimestamp: "2026-07-12T02:03:04Z",
    coverage: [
      { adapterId: "codex-builtin", status: "complete", presence: "present", itemCount: 1 },
      { adapterId: "claude-settings", status: "partial", presence: "present", itemCount: 1 },
    ],
    skippedPaths: [{ safeLocator: ".claude/<redacted>", reasonCode: "permission_denied" }],
    issues: [{ code: "permission_denied", safeMessage: "일부 위치를 읽을 수 없습니다." }],
  },
};

function readyData(overrides = {}) {
  return createLocalDataState({
    phase: "ready",
    latestComplete: {
      snapshotId: "snapshot-local-7",
      attemptId: "attempt-local-7",
      status: "complete",
    },
    latestTerminalReport: {
      attemptId: "attempt-local-7",
      state: "complete",
      errorCode: null,
    },
    queryResult,
    ...overrides,
  });
}

test("workbench keeps one search surface and uses rectangular tool filters, kind badges, and compact results", () => {
  const data = readyData();
  const view = createLocalViewState({
    toolId: "codex",
    query: "release <safe>",
  });
  const markup = renderLocalWorkbench(data, view);

  assert.equal((markup.match(/data-local-tool=/g) ?? []).length, 3);
  assert.equal((markup.match(/data-local-tool="codex"[^>]*aria-pressed="true"/g) ?? []).length, 1);
  assert.equal((markup.match(/id="local-search"/g) ?? []).length, 1);
  assert.match(markup, /value="release &lt;safe&gt;"/);
  assert.match(markup, /data-local-kind="skill"/);
  assert.doesNotMatch(markup, /data-local-kind="agent"/);
  assert.equal((markup.match(/data-local-instance=/g) ?? []).length, 2);
  assert.equal((markup.match(/class="local-result-item" role="listitem"/g) ?? []).length, 2);
  assert.equal((markup.match(/data-tool-identity-context="local_tool_filter"/g) ?? []).length, 2);
  assert.equal((markup.match(/data-tool-identity-context="local_result_badge"/g) ?? []).length, 2);
  const allToolButton = markup.match(/<button(?=[^>]*data-local-tool="")[^>]*>[\s\S]*?<\/button>/)?.[0] ?? "";
  assert.doesNotMatch(allToolButton, /<img|data-tool-identity/);
  assert.equal((markup.match(/data-tool-identity-mode="text"/g) ?? []).length, 4);
  assert.doesNotMatch(markup, /<img|data-tool-identity-image/);
  assert.doesNotMatch(markup, /<button[^>]*role="listitem"/);
  assert.match(markup, /Release &lt;Safety&gt;/);
  assert.match(markup, /\.agents\/skills\/release\/SKILL\.md/);
  assert.doesNotMatch(markup, /contentHash|content_hash|HASH_ONLY/);
  assert.equal(localResultCount(data), 2);
assert.match(markup, /SoT 일치/);
assert.doesNotMatch(markup, /SoT 연결 없음/);
});

test("Local removal keeps checkboxes hidden until explicit selection mode and renders sibling controls", () => {
  const data = readyData();
  let removal = createLocalRemovalState();
  const defaultMarkup = renderLocalWorkbench(data, createLocalViewState(), removal);
  const badgeRow = defaultMarkup.match(/<div class="local-badge-row"[^>]*>[\s\S]*?<\/div>/)?.[0] ?? "";
  assert.equal((defaultMarkup.match(/data-local-removal-selection=/g) ?? []).length, 0);
  assert.match(badgeRow, /local-scope-badge[\s\S]*data-enter-local-removal>설치 하네스 제거[\s\S]*data-local-kind="skill"/);
  assert.doesNotMatch(defaultMarkup, /class="local-removal-toolbar"/);

  const noRemovableMarkup = renderLocalWorkbench(readyData({
    queryResult: {
      ...queryResult,
      items: queryResult.items.map((item) => ({ ...item, removable: false })),
    },
  }), createLocalViewState(), removal);
  const noSnapshotMarkup = renderLocalWorkbench(
    readyData({ latestComplete: null }),
    createLocalViewState(),
    removal,
  );
  assert.doesNotMatch(noRemovableMarkup, /data-enter-local-removal/);
  assert.doesNotMatch(noSnapshotMarkup, /data-enter-local-removal/);

  removal = enterLocalRemovalSelectionMode(removal);
  removal = toggleLocalRemovalSelection(removal, "instance-skill");
  const markup = renderLocalWorkbench(data, createLocalViewState(), removal);
  const removalToolbar = markup.match(/<div class="local-removal-toolbar"[^>]*>[\s\S]*?<\/div>/)?.[0] ?? "";

  assert.equal((markup.match(/data-local-removal-selection=/g) ?? []).length, 2);
  assert.match(removalToolbar, /1개 선택됨/);
  assert.match(markup, /data-cancel-local-removal-selection>선택 취소/);
  assert.match(markup, /data-prepare-local-removal>선택한 1개 제거/);
  assert.match(markup, /data-local-removal-selection="instance-skill"[^>]*checked/);
  assert.match(markup, /<div class="local-result-item local-result-item--removal-mode"[^>]*><label[^>]*>.*?<\/label><button[^>]*data-local-instance/s);
  const panels = markup.match(/<button(?=[^>]*data-local-instance)[^>]*>[\s\S]*?<\/button>/g) ?? [];
  panels.forEach((panel) => assert.doesNotMatch(panel, /data-local-removal-selection/));
});

test("Local cards and inspector use backend displayName as primary identity and locator as secondary metadata", () => {
  const item = {
    ...queryResult.items[0],
    displayName: "Release Sentinel",
    name: "Legacy Parser Name",
    safeLocator: ".agents/skills/release/SKILL.md",
  };
  const data = readyData({
    queryResult: {
      ...queryResult,
      items: [item],
      counts: { totalInstances: 1, matchedInstances: 1 },
    },
    selectedDetail: item,
  });
  const view = createLocalViewState({ selectedInstanceId: item.instanceId });

  const workbench = renderLocalWorkbench(data, view);
  const inspector = renderLocalInspector(data, view);

  assert.match(workbench, /class="local-result-main"><strong>Release Sentinel<\/strong>/);
  assert.match(workbench, /class="local-result-meta">[\s\S]*<code>\.agents\/skills\/release\/SKILL\.md<\/code>/);
  assert.doesNotMatch(workbench, /<strong>Legacy Parser Name<\/strong>/);
  assert.doesNotMatch(workbench, /<strong>\.agents\/skills\/release\/SKILL\.md<\/strong>/);
  assert.match(inspector, /<h3 class="detail-name">Release Sentinel<\/h3>/);
  assert.doesNotMatch(inspector, /<h3 class="detail-name">(?:Legacy Parser Name|\.agents\/skills\/release\/SKILL\.md)<\/h3>/);
});

test("Local frontend never promotes a legacy name, locator, or opaque id when displayName is absent", () => {
  const item = {
    ...queryResult.items[0],
    displayName: null,
    display_name: null,
    name: "Legacy Parser Name",
    safeLocator: ".agents/skills/release/SKILL.md",
    instanceId: "opaque-instance-id",
  };
  const data = readyData({
    queryResult: {
      ...queryResult,
      items: [item],
      counts: { totalInstances: 1, matchedInstances: 1 },
    },
    selectedDetail: item,
  });
  const view = createLocalViewState({ selectedInstanceId: item.instanceId });

  const workbench = renderLocalWorkbench(data, view);
  const inspector = renderLocalInspector(data, view);

  assert.match(workbench, /class="local-result-main"><strong>이름 정보 없음<\/strong>/);
  assert.match(inspector, /<h3 class="detail-name">이름 정보 없음<\/h3>/);
  assert.doesNotMatch(workbench, /<strong>(?:Legacy Parser Name|\.agents\/skills\/release\/SKILL\.md|opaque-instance-id)<\/strong>/);
});

test("qualified AI tool buttons remain visible when the snapshot has zero items", () => {
  const data = readyData({
    queryResult: {
      ...queryResult,
      qualifiedTools: [
        { toolId: "codex", label: "Codex" },
        { toolId: "claude_code", label: "Claude Code" },
        { toolId: "antigravity", label: "Antigravity" },
        { toolId: "antigravity_cli", label: "Antigravity CLI" },
        { toolId: "hermes", label: "Hermes" },
      ],
      items: [],
      counts: { totalInstances: 0, matchedInstances: 0 },
    },
  });

  const markup = renderLocalWorkbench(data, createLocalViewState());

  assert.equal((markup.match(/data-local-tool=/g) ?? []).length, 6);
  for (const label of ["Codex", "Claude Code", "Antigravity", "Antigravity CLI", "Hermes"]) {
    assert.match(markup, new RegExp(`>${label}<`));
  }
});

test("large Local inventories render a bounded navigable result window", () => {
  const items = Array.from({ length: 205 }, (_, index) => ({
    ...queryResult.items[0],
    instanceId: `instance-${String(index).padStart(3, "0")}`,
    name: `Harness ${index}`,
  }));
  const data = readyData({
    queryResult: {
      ...queryResult,
      items,
      counts: { totalInstances: 205, matchedInstances: 205 },
    },
  });
  const first = renderLocalWorkbench(data, createLocalViewState());
  const last = renderLocalWorkbench(data, createLocalViewState({ resultOffset: 200 }));

  assert.equal((first.match(/data-local-instance=/g) ?? []).length, 100);
  assert.equal((last.match(/data-local-instance=/g) ?? []).length, 5);
  assert.match(first, /data-local-result-window="next"/);
  assert.match(first, /1–100 \/ 205/);
  assert.match(last, /data-local-result-window="previous"/);
  assert.match(last, /201–205 \/ 205/);
  assert.equal((first.match(/id="local-search"/g) ?? []).length, 1);
});

test("zero result state identifies active filters and offers one reset action", () => {
  const data = readyData({
    queryResult: {
      ...queryResult,
      items: [],
      counts: { totalInstances: 2, matchedInstances: 0 },
    },
  });
  const markup = renderLocalWorkbench(data, createLocalViewState({
    locationFilter: { scope: "project", projectId: "project-safe-7" },
    toolId: "codex",
    kind: "hook",
    query: "missing",
  }));

  assert.match(markup, /조건에 맞는 하네스가 없습니다/);
  assert.match(markup, /Project/);
  assert.match(markup, /Codex/);
  assert.match(markup, /hook/);
  assert.match(markup, /missing/);
  assert.equal((markup.match(/data-reset-local-filters/g) ?? []).length, 1);
});

test("search field remains mounted before the first session snapshot", () => {
  const markup = renderLocalWorkbench(createLocalDataState({
    phase: "running",
    currentAttempt: {
      attemptId: "attempt-progress",
      state: "running",
      errorCode: null,
      progress: {
        attemptId: "attempt-progress",
        adapterId: "codex-builtin",
        surfaceId: "project-skills",
        coverageId: "coverage-project-skills",
        itemCount: 7,
      },
    },
  }), createLocalViewState());

  assert.equal((markup.match(/id="local-search"/g) ?? []).length, 1);
  assert.match(markup, /aria-busy="true"/);
  assert.match(markup, /스캔 중/);
  assert.match(markup, /codex-builtin/);
  assert.match(markup, /project-skills/);
  assert.match(markup, /7개 확인/);
});

test("left explorer selects by backend project ID and shows folder name plus full local path", () => {
  const markup = renderLocalExplorer(
    readyData(),
    createLocalViewState({
      explorerId: "project-safe-7",
      locationFilter: { scope: "project", projectId: "project-safe-7" },
    }),
  );

  assert.match(markup, /role="tree"/);
  assert.match(markup, /data-local-scope="all"/);
  assert.match(markup, /data-local-scope="user"/);
  assert.match(markup, /data-local-project-id="project-safe-7"/);
  assert.match(markup, /aria-selected="true"/);
  assert.match(markup, /routine-harness/);
  assert.match(markup, /\/fixture-home\/Projects\/routine-harness/);
  assert.doesNotMatch(markup, /Project project-/);
  assert.doesNotMatch(markup, /data-tool-identity|<img/);
});

test("unapproved tool identity stays visible as text without a broken or inferred icon", () => {
  const data = readyData({
    queryResult: {
      ...queryResult,
      qualifiedTools: [{ toolId: "gemini", label: "Gemini" }],
      items: [{ ...queryResult.items[0], toolId: "gemini" }],
      counts: { totalInstances: 1, matchedInstances: 1 },
    },
  });

  const markup = renderLocalWorkbench(data, createLocalViewState());

  assert.equal((markup.match(/data-tool-identity="gemini"/g) ?? []).length, 2);
  assert.equal((markup.match(/data-tool-identity-mode="text"/g) ?? []).length, 2);
  assert.match(markup, />Gemini</);
  assert.doesNotMatch(markup, /<img|data:image|https?:\/\//);
});

test("duplicate folder names remain distinguishable by their full local paths", () => {
  const data = readyData({
    queryResult: {
      ...queryResult,
      projects: [
        {
          projectId: "project-work",
          label: "routine-harness",
          canonicalPath: "/fixture-home/Work/routine-harness",
        },
        {
          projectId: "project-lab",
          label: "routine-harness",
          canonicalPath: "/fixture-home/Lab/routine-harness",
        },
      ],
    },
  });

  const markup = renderLocalExplorer(data, createLocalViewState());

  assert.equal((markup.match(/<strong>routine-harness<\/strong>/g) ?? []).length, 2);
  assert.match(markup, /\/fixture-home\/Work\/routine-harness/);
  assert.match(markup, /\/fixture-home\/Lab\/routine-harness/);
  assert.match(markup, /aria-label="routine-harness · \/fixture-home\/Work\/routine-harness"/);
  assert.match(markup, /title="routine-harness · \/fixture-home\/Lab\/routine-harness"/);
});

test("project explorer search matches names only and pins the current selection", () => {
  const data = readyData({
    projects: [
      {
        projectId: "project-selected",
        label: "Alpha Workspace",
        canonicalPath: "/fixture-home/needle-only-in-selected-path",
      },
      {
        projectId: "project-name-match",
        label: "Needle Service",
        canonicalPath: "/fixture-home/services/ordinary",
      },
      {
        projectId: "project-path-only",
        label: "Gamma Workspace",
        canonicalPath: "/fixture-home/needle-only-in-path",
      },
    ],
  });
  const markup = renderLocalExplorer(data, createLocalViewState({
    explorerId: "project-selected",
    locationFilter: { scope: "project", projectId: "project-selected" },
    projectExplorerQuery: "NEEDLE",
  }));

  assert.equal((markup.match(/id="local-project-search"/g) ?? []).length, 1);
  assert.match(markup, /value="NEEDLE"/);
  assert.match(markup, /data-local-project-id="project-selected"/);
  assert.match(markup, /data-local-project-pinned="true"/);
  assert.match(markup, /data-local-project-id="project-name-match"/);
  assert.doesNotMatch(markup, /data-local-project-id="project-path-only"/);
  assert.match(markup, /Alpha Workspace/);
  assert.match(markup, /\/fixture-home\/needle-only-in-selected-path/);
});

test("project scope exposes the explicit .harnesskitignore editor action", () => {
  const view = createLocalViewState({
    locationFilter: { scope: "project", projectId: "project-routine" },
  });
  const markup = renderLocalWorkbench(readyData(), view);

  assert.match(markup, /data-open-project-ignore/);
  assert.match(markup, /스캔 제외 규칙 편집/);
});

test("project scope shows a compact ignore summary without rule text or excluded paths", () => {
  const data = readyData({
    queryResult: {
      ...queryResult,
      snapshotSummary: {
        ...queryResult.snapshotSummary,
        projectIgnoreSummaries: [{
          projectId: "project-safe-7",
          sourceRevision: "f3a6c91d04729e",
          ruleCount: 4,
          excludedPathCount: 17,
          exactText: "generated/**",
          excludedPaths: ["generated/private-fixture"],
        }],
      },
    },
  });
  const markup = renderLocalWorkbench(data, createLocalViewState({
    locationFilter: { scope: "project", projectId: "project-safe-7" },
  }));

  assert.match(markup, /\.harnesskitignore/);
  assert.match(markup, /revision f3a6c91d/);
  assert.match(markup, /규칙 4개/);
  assert.match(markup, /경로 17개 제외/);
  assert.doesNotMatch(markup, /generated\/\*\*/);
  assert.doesNotMatch(markup, /generated\/private-fixture/);
});

test("project scope keeps an invalid ignore source visibly bound to the selected project", () => {
  const data = readyData({
    queryResult: {
      ...queryResult,
      snapshotSummary: {
        ...queryResult.snapshotSummary,
        projectIgnoreSummaries: [],
        issues: [
          {
            projectId: "project-other",
            code: "project_ignore_invalid",
            safeMessage: "다른 project 오류",
          },
          {
            projectId: "project-safe-7",
            code: "project_ignore_invalid",
            safeMessage: "3번째 줄의 제외 규칙을 확인하세요.",
          },
        ],
      },
    },
  });
  const markup = renderLocalWorkbench(data, createLocalViewState({
    locationFilter: { scope: "project", projectId: "project-safe-7" },
  }));

  assert.match(markup, /data-project-ignore-state="invalid"/);
  assert.match(markup, /3번째 줄의 제외 규칙을 확인하세요/);
  assert.doesNotMatch(markup, /다른 project 오류/);
});

test("selected instance inspector retains its project-bound ignore issue", () => {
  const data = readyData({
    queryResult: {
      ...queryResult,
      snapshotSummary: {
        ...queryResult.snapshotSummary,
        projectIgnoreSummaries: [],
        issues: [{
          projectId: "project-safe-7",
          code: "project_ignore_unreadable",
          safeMessage: "제외 규칙 파일을 읽을 수 없습니다.",
        }],
      },
    },
  });
  const markup = renderLocalInspector(data, createLocalViewState({
    selectedInstanceId: "instance-skill",
  }));

  assert.match(markup, /data-project-ignore-state="invalid"/);
  assert.match(markup, /제외 규칙 파일을 읽을 수 없습니다/);
});

test("project explorer rows expose bounded stable AX identifiers without path or project identity", () => {
  const markup = renderLocalProjectRows(readyData({
    projects: [
      { projectId: "opaque-project-a", label: "Alpha", canonicalPath: "/private/alpha" },
      { projectId: "opaque-project-b", label: "Beta", canonicalPath: "/private/beta" },
    ],
  }), createLocalViewState());

  assert.match(markup, /id="local-project-scope-0"/);
  assert.match(markup, /id="local-project-scope-1"/);
  assert.doesNotMatch(markup, /<button id="[^"]*(?:opaque-project|private)/);
});

test("project explorer keeps a nonmatching selection visible and reports zero name matches", () => {
  const data = readyData({
    projects: [
      {
        projectId: "project-selected",
        label: "Alpha Workspace",
        canonicalPath: "/fixture-home/needle-only-in-path",
      },
    ],
  });
  const markup = renderLocalProjectRows(data, createLocalViewState({
    explorerId: "project-selected",
    locationFilter: { scope: "project", projectId: "project-selected" },
    projectExplorerQuery: "missing-name",
  }));

  assert.match(markup, /data-local-project-id="project-selected"/);
  assert.match(markup, /data-local-project-pinned="true"/);
  assert.match(markup, /이름이 일치하는 project 없음/);
});

test("missing project metadata never exposes the hash project ID as a visible label", () => {
  const opaqueProjectId = "107edff5931fbd3444c8ae5ab1e9cf66";
  const data = readyData({
    queryResult: {
      ...queryResult,
      projects: [],
      items: [{ ...queryResult.items[0], projectId: opaqueProjectId }],
      counts: { totalInstances: 1, matchedInstances: 1 },
    },
  });
  const markup = renderLocalWorkbench(data, createLocalViewState({
    locationFilter: { scope: "project", projectId: opaqueProjectId },
  }));

  assert.doesNotMatch(markup, new RegExp(opaqueProjectId));
  assert.match(markup, /프로젝트 정보 없음/);
});

test("left explorer falls back to one root roving tab stop when a selected project expires", () => {
  const markup = renderLocalExplorer(
    readyData(),
    createLocalViewState({
      explorerId: "project-expired",
      locationFilter: { scope: "project", projectId: "project-expired" },
    }),
  );

  assert.equal((markup.match(/tabindex="0"/g) ?? []).length, 1);
  assert.equal((markup.match(/aria-selected="true"/g) ?? []).length, 1);
  assert.match(markup, /<button(?=[^>]*data-local-scope="all")(?=[^>]*aria-selected="true")/);
});

test("unselected inspector reports snapshot freshness, attempt, coverage, skipped paths, and safe issues", () => {
  const markup = renderLocalInspector(readyData(), createLocalViewState());

  assert.match(markup, /snapshot-local-7/);
  assert.match(markup, /2026-07-12T02:03:04Z/);
  assert.match(markup, /attempt-local-7/);
  assert.match(markup, /Coverage/);
  assert.match(markup, /Skipped/);
  assert.match(markup, /\.claude\/&lt;redacted&gt;/);
  assert.match(markup, /permission_denied/);
  assert.match(markup, /일부 위치를 읽을 수 없습니다/);
});

test("selected inspector makes full source primary and keeps only native read actions", () => {
  const data = readyData({
    selectedDetail: {
      instanceId: "instance-skill",
      adapterId: "codex-builtin",
      adapterVersion: "1.0.0",
      toolId: "codex",
      surfaceId: "project-skills",
      scope: "project",
      projectId: "project-safe-7",
      safeLocator: ".agents/skills/release/SKILL.md",
      kind: "skill",
      displayName: "Release Safety",
      name: "Release Safety",
      description: "Approved release workflow",
      descriptionSource: "frontmatter",
      parseState: "parsed",
      issueCodes: [],
      settings: [{ key: "model", present: true, redacted: true, value: null }],
      correlation: { state: "verified", componentId: "harnesskit.skill.release" },
      rawPath: "/fixture-home/private/secret.md",
      contentHash: "HASH_ONLY_NEEDLE",
    },
  });
  const markup = renderLocalInspector(
    data,
    createLocalViewState({ selectedInstanceId: "instance-skill" }),
  );

  assert.match(markup, /Release Safety/);
  assert.match(markup, /codex-builtin/);
  assert.match(markup, /harnesskit\.skill\.release/);
  assert.doesNotMatch(markup, /data-local-action="viewer"/);
  assert.match(markup, /data-local-action="reveal"/);
  assert.match(markup, /data-local-action="copy_path"/);
  assert.equal((markup.match(/data-snapshot-id="snapshot-local-7"/g) ?? []).length, 2);
  assert.equal((markup.match(/data-instance-id="instance-skill"/g) ?? []).length, 2);
  assert.match(markup, /data-local-source-content/);
  assert.match(markup, /전체 원문/);
  assert.match(markup, />AI 연결 설정</);
  assert.match(markup, /원문은 AI 설명 생성을 눌렀을 때만 전송됩니다/);
  assert.doesNotMatch(markup, /AI 설명 설정/);
  assert.match(markup, /<details class="local-technical-info">/);
  assert.equal((markup.match(/data-tool-identity-context="local_selected_inspector"/g) ?? []).length, 1);
  assert.match(markup, /data-tool-identity="codex"[^>]*data-tool-identity-mode="text"/);
  assert.doesNotMatch(markup, /<img|data-tool-identity-image/);
  assert.doesNotMatch(markup, /\/Users\/me\/private|HASH_ONLY_NEEDLE|rawPath|contentHash/);
});

test("selected inspector keeps collapsed scan information after the full source preview", () => {
  const markup = renderLocalInspector(
    readyData(),
    createLocalViewState({ selectedInstanceId: "instance-skill" }),
  );

  const sourceIndex = markup.indexOf('class="local-source-preview"');
  const scanIndex = markup.indexOf('<details class="local-technical-info">');
  const scanDisclosure = markup.slice(scanIndex, markup.indexOf("</details>", scanIndex) + 10);

  assert.ok(sourceIndex >= 0);
  assert.ok(scanIndex > sourceIndex);
  assert.match(scanDisclosure, /^<details class="local-technical-info"><summary>스캔 정보<\/summary>/);
  assert.doesNotMatch(scanDisclosure, /<details[^>]*\sopen(?:\s|>)/);
  assert.doesNotMatch(markup, /기술 정보/);
});

test("selected inspector exposes a typed accessible Local action status without path data", () => {
  const data = readyData();
  const view = createLocalViewState({
    selectedInstanceId: "instance-skill",
    actionStatus: {
      state: "error",
      code: "stale_path_handle",
      message: "항목이 스캔 이후 변경되었습니다. 다시 스캔하세요.",
      instanceId: "instance-skill",
    },
  });
  const markup = renderLocalInspector(data, view);

  assert.match(markup, /data-local-action-status/);
  const visualStatus = markup.match(/<p class="local-action-status[^"]*"[^>]*>/)?.[0] ?? "";
  assert.match(visualStatus, /data-local-action-status/);
  assert.doesNotMatch(visualStatus, /role="status"|aria-live/);
  assert.match(markup, /data-local-action-state="error"/);
  assert.match(markup, /data-local-action-code="stale_path_handle"/);
  assert.match(markup, /code stale_path_handle/);
  assert.match(markup, /항목이 스캔 이후 변경되었습니다/);
  assert.doesNotMatch(markup, /Users\//);
});

test("busy Local actions stay focusable but reject a second intent", () => {
  const markup = renderLocalInspector(
    readyData(),
    {
      ...createLocalViewState({ selectedInstanceId: "instance-skill" }),
      actionBusy: true,
    },
  );

  assert.match(markup, /class="local-read-actions"[^>]*aria-busy="true"/);
  assert.equal((markup.match(/data-local-action="[^"]+"[^>]*aria-disabled="true"/g) ?? []).length, 2);
  assert.doesNotMatch(markup, /data-local-action="[^"]+"[^>]* disabled/);
});

test("source preview is an explicit navigation region and never a live announcement", () => {
  const markup = renderLocalInspector(
    readyData(),
    createLocalViewState({ selectedInstanceId: "instance-skill" }),
  );

  const preview = markup.match(/<div\b(?=[^>]*\bclass="local-source-content")[^>]*>/)?.[0] ?? "";
  const previewSection = markup.match(/<section class="local-source-preview"[\s\S]*?<\/section>/)?.[0] ?? "";
  assert.match(preview, /role="region"/);
  assert.match(preview, /tabindex="0"/);
  assert.match(preview, /aria-label="선택한 하네스 전체 원문"/);
  assert.doesNotMatch(preview, /aria-live/);
  assert.doesNotMatch(previewSection, /data-tool-identity|<img/);
});

test("source preview exposes accessible total-chunk range geometry and keeps previous-next controls", () => {
  const markup = renderLocalInspector(
    readyData(),
    {
      ...createLocalViewState({ selectedInstanceId: "instance-skill" }),
      sourcePreview: {
        phase: "ready",
        viewGeneration: 7,
        selection: { snapshotId: "snapshot-local-7", instanceId: "instance-skill" },
        header: {
          previewSessionId: "session-source-7",
          snapshotId: "snapshot-local-7",
          instanceId: "instance-skill",
          canonicalPath: "/fixture-home/Projects/routine-harness/.agents/skills/release/SKILL.md",
          sourceRevision: "revision-source-7",
          totalChunks: 12,
          selectedChunkIndex: 3,
          issue: null,
        },
        currentChunkIndex: 3,
        chunks: new Map(),
        error: null,
      },
    },
  );

  const range = markup.match(/<input[^>]*data-source-chunk-range[^>]*>/)?.[0] ?? "";
  assert.match(range, /type="range"/);
  assert.match(range, /min="0"/);
  assert.match(range, /max="11"/);
  assert.match(range, /value="3"/);
  assert.match(range, /aria-label="원문 위치"/);
  assert.match(range, /aria-valuetext="4 \/ 12"/);
  assert.match(markup, /data-source-chunk="2"/);
  assert.match(markup, /data-source-chunk="4"/);
  assert.match(markup, /<span role="status" aria-live="polite">4 \/ 12<\/span>/);
});

test("Verified context is removable and only Verified correlation exposes a SoT link", () => {
  const data = readyData();
  const verifiedView = createLocalViewState({
    selectedInstanceId: "instance-skill",
    correlationProjectionId: "projection-verified-7",
    verifiedComponentId: "harnesskit.skill.release",
  });
  const workbench = renderLocalWorkbench(data, verifiedView);
  const inspector = renderLocalInspector(data, verifiedView);

  assert.match(workbench, /class="local-correlation-context"/);
  assert.match(workbench, /harnesskit\.skill\.release/);
  assert.match(workbench, /data-clear-local-correlation/);
  assert.match(inspector, /data-open-sot-component="harnesskit\.skill\.release"/);
  assert.equal(verifiedSotComponentId(data, verifiedView), "harnesskit.skill.release");

  const ambiguous = structuredClone(queryResult);
  ambiguous.items[0].correlation = {
    state: "ambiguous",
    componentId: "harnesskit.skill.release",
    safeReason: "multiple candidates",
  };
  const ambiguousData = readyData({ queryResult: ambiguous });
  const ambiguousMarkup = renderLocalInspector(ambiguousData, verifiedView);
  assert.match(ambiguousMarkup, /연결 확인 필요/);
  assert.doesNotMatch(ambiguousMarkup, /data-open-sot-component/);
  assert.equal(verifiedSotComponentId(ambiguousData, verifiedView), null);
});
