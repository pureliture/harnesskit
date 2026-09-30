import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

import {
  activeLocalSnapshotHeader,
  createLocalDataState,
  createLocalQueryRequest,
  createLocalViewState,
  LOCAL_ROOT_ID,
  localRenderFingerprint,
  reduceLocalScanState,
  reduceLocalView,
} from "../local-state.js";

const moduleUrl = new URL("../local-state.js", import.meta.url);

test("Local dashboard owns an isolated state module", () => {
  assert.equal(existsSync(moduleUrl), true);
});

test("general Local entry resets only view authority to the approved root defaults", () => {
  const previous = createLocalViewState({
    explorerId: "project-7",
    locationFilter: { scope: "project", projectId: "project-7" },
    toolId: "codex",
    kind: "skill",
    query: "release",
    projectExplorerQuery: "routine",
    selectedInstanceId: "instance-9",
    correlationProjectionId: "projection-4",
    verifiedComponentId: "harnesskit.skill.release",
    scrollTop: 318,
  });

  const reset = reduceLocalView(previous, { type: "reset" });

  assert.deepEqual(reset, {
    explorerId: LOCAL_ROOT_ID,
    locationFilter: { scope: "all", projectId: null },
    toolId: null,
    kind: null,
    query: "",
    projectExplorerQuery: "",
    selectedInstanceId: null,
    actionStatus: null,
    correlationProjectionId: null,
    verifiedComponentId: null,
    scrollTop: 0,
    resultOffset: 0,
    resultPageSize: 100,
  });
});

test("project explorer query changes only frontend explorer state", () => {
  const data = createLocalDataState({
    latestComplete: {
      snapshotId: "snapshot-project-search",
      attemptId: "attempt-project-search",
      status: "complete",
    },
  });
  const before = createLocalViewState({
    explorerId: "project-selected",
    locationFilter: { scope: "project", projectId: "project-selected" },
    toolId: "codex",
    kind: "skill",
    query: "release",
    selectedInstanceId: "instance-selected",
    projectExplorerQuery: "before",
  });
  const beforeRequest = createLocalQueryRequest(data, before);

  const after = reduceLocalView(before, {
    type: "set_project_explorer_query",
    query: "AFTER",
  });

  assert.equal(after.projectExplorerQuery, "AFTER");
  assert.equal(after.explorerId, "project-selected");
  assert.deepEqual(after.locationFilter, before.locationFilter);
  assert.equal(after.selectedInstanceId, "instance-selected");
  assert.deepEqual(createLocalQueryRequest(data, after), beforeRequest);
  assert.equal(Object.hasOwn(beforeRequest, "projectExplorerQuery"), false);
});

test("scan reducer upserts unknown attempts and rejects stale revision or sequence", () => {
  const initial = createLocalDataState();
  const running = reduceLocalScanState(initial, {
    type: "event",
    envelope: {
      attempt_id: "attempt-unknown",
      sequence: 3,
      state_revision: 7,
      payload: { state: "running" },
    },
  });
  const duplicate = reduceLocalScanState(running, {
    type: "event",
    envelope: {
      attempt_id: "attempt-unknown",
      sequence: 3,
      state_revision: 8,
      payload: { state: "failed", code: "should_not_replace" },
    },
  });
  const staleRevision = reduceLocalScanState(running, {
    type: "event",
    envelope: {
      attempt_id: "attempt-other",
      sequence: 99,
      state_revision: 6,
      payload: { state: "failed", code: "stale" },
    },
  });

  assert.equal(running.phase, "running");
  assert.equal(running.currentAttempt.attemptId, "attempt-unknown");
  assert.equal(running.stateRevision, 7);
  assert.equal(running.attemptSequences["attempt-unknown"], 3);
  assert.equal(duplicate, running);
  assert.equal(staleRevision, running);
});

test("ignore save outcome keeps an accepted or queued rescan visible without attempt identity", () => {
  const running = createLocalDataState({
    phase: "running",
    currentAttempt: {
      attemptId: "attempt-private",
      state: "running",
    },
    latestComplete: {
      snapshotId: "snapshot-before",
      attemptId: "attempt-before",
      status: "complete",
    },
  });

  const queued = reduceLocalScanState(running, {
    type: "ignore_save_outcome",
    outcome: {
      status: "queued_after_current",
      currentAttemptId: "attempt-private",
    },
  });
  assert.equal(queued.ignoreRescanStatus, "queued");
  assert.equal(queued.message, "현재 스캔 완료 후 제외 규칙 반영 스캔을 실행합니다.");
  assert.doesNotMatch(queued.message, /attempt-private|snapshot-before/);

  const reconciled = reduceLocalScanState(queued, {
    type: "reconcile",
    scanState: {
      stateRevision: 1,
      currentAttempt: { attemptId: "attempt-private", state: "running" },
      latestComplete: {
        snapshotId: "snapshot-before",
        attemptId: "attempt-before",
        status: "complete",
      },
    },
  });
  assert.equal(reconciled.ignoreRescanStatus, "queued");
  assert.equal(reconciled.message, "현재 스캔 완료 후 제외 규칙 반영 스캔을 실행합니다.");

  const accepted = reduceLocalScanState(createLocalDataState(), {
    type: "ignore_save_outcome",
    outcome: { status: "accepted", attemptId: "attempt-hidden" },
  });
  assert.equal(accepted.ignoreRescanStatus, "accepted");
  assert.equal(accepted.message, "제외 규칙을 저장하고 새 스캔을 시작했습니다.");
  assert.doesNotMatch(accepted.message, /attempt-hidden/);

  const settled = reduceLocalScanState(accepted, {
    type: "reconcile",
    scanState: { stateRevision: 2, currentAttempt: null },
  });
  assert.equal(settled.ignoreRescanStatus, null);
});

test("reconcile restores progress that was missed on the event channel", () => {
  const reconciled = reduceLocalScanState(createLocalDataState(), {
    type: "reconcile",
    scanState: {
      state_revision: 2,
      current_attempt: {
        attempt_id: "attempt-reconcile",
        state: "running",
        error_code: null,
        progress: {
          adapter_id: "claude-settings",
          surface_id: "user-hooks",
          coverage_id: "coverage-user-hooks",
          item_count: 3,
        },
      },
      latest_terminal_report: null,
      latest_complete: null,
      latest_partial: null,
    },
  });

  assert.deepEqual(reconciled.currentAttempt.progress, {
    attemptId: "attempt-reconcile",
    adapterId: "claude-settings",
    surfaceId: "user-hooks",
    coverageId: "coverage-user-hooks",
    itemCount: 3,
  });
});

test("scan progress retains only safe adapter surface coverage and count fields", () => {
  const running = reduceLocalScanState(createLocalDataState(), {
    type: "event",
    envelope: {
      attempt_id: "attempt-progress",
      sequence: 2,
      state_revision: 1,
      payload: {
        state: "progress",
        adapter_id: "codex-builtin",
        surface_id: "project-skills",
        coverage_id: "coverage-project-skills",
        item_count: 7,
      },
    },
  });

  assert.equal(running.phase, "running");
  assert.deepEqual(running.currentAttempt.progress, {
    attemptId: "attempt-progress",
    adapterId: "codex-builtin",
    surfaceId: "project-skills",
    coverageId: "coverage-project-skills",
    itemCount: 7,
  });
  assert.equal(running.message, "스캔 중: codex-builtin / project-skills · 7개 확인");
});

test("reconcile keeps the latest complete snapshot and invalidates mismatched query data", () => {
  const queried = reduceLocalScanState(createLocalDataState(), {
    type: "query_result",
    result: { snapshotId: "snapshot-old", items: [{ instanceId: "old" }] },
  });
  const reconciled = reduceLocalScanState(queried, {
    type: "reconcile",
    scanState: {
      state_revision: 11,
      current_attempt: null,
      latest_terminal_report: { attempt_id: "attempt-11", state: "complete", error_code: null },
      latest_complete: { snapshot_id: "snapshot-11", attempt_id: "attempt-11", status: "complete" },
      latest_partial: null,
    },
  });

  assert.equal(reconciled.phase, "ready");
  assert.deepEqual(activeLocalSnapshotHeader(reconciled), {
    snapshotId: "snapshot-11",
    attemptId: "attempt-11",
    status: "complete",
  });
  assert.equal(reconciled.queryResult, null);
});

test("query request is revision-bound and contains only approved filter fields", () => {
  const data = createLocalDataState({
    latestComplete: {
      snapshotId: "snapshot-22",
      attemptId: "attempt-22",
      status: "complete",
    },
  });
  const view = createLocalViewState({
    explorerId: "project-a",
    locationFilter: { scope: "project", projectId: "project-a" },
    toolId: "claude_code",
    kind: "hook",
    query: "deploy",
    correlationProjectionId: "projection-22",
    verifiedComponentId: "harnesskit.hook.deploy",
  });

  assert.deepEqual(createLocalQueryRequest(data, view), {
    snapshotId: "snapshot-22",
    locationFilter: { scope: "project", projectId: "project-a" },
    toolId: "claude_code",
    kind: "hook",
    query: "deploy",
    correlationProjectionId: "projection-22",
    verifiedComponentId: "harnesskit.hook.deploy",
  });
});

test("correlation evidence does not invalidate Local list render continuity", () => {
  const data = createLocalDataState({
    latestComplete: {
      snapshotId: "snapshot-continuity",
      attemptId: "attempt-continuity",
      status: "complete",
    },
  });
  const before = createLocalViewState({
    locationFilter: { scope: "project", projectId: "project-a" },
    toolId: "codex",
    kind: "skill",
    query: "release",
    resultOffset: 200,
  });
  const correlated = {
    ...before,
    correlationProjectionId: "projection-a",
    verifiedComponentId: "harnesskit.skill.release",
  };

  assert.equal(
    localRenderFingerprint(data, before),
    localRenderFingerprint(data, correlated),
  );
  assert.notEqual(
    localRenderFingerprint(data, before),
    localRenderFingerprint(data, { ...before, query: "docs" }),
  );
  assert.notEqual(
    localRenderFingerprint(data, before),
    localRenderFingerprint(data, { ...before, resultOffset: 300 }),
  );
});

test("tool selection is a filter change and never becomes a page or group state", () => {
  const previous = createLocalViewState({
    locationFilter: { scope: "user", projectId: null },
    query: "docs",
    kind: "rule",
    selectedInstanceId: "instance-1",
    scrollTop: 90,
  });

  const selected = reduceLocalView(previous, { type: "select_tool", toolId: "codex" });

  assert.equal(selected.toolId, "codex");
  assert.equal(selected.query, "docs");
  assert.equal(selected.kind, "rule");
  assert.deepEqual(selected.locationFilter, { scope: "user", projectId: null });
  assert.equal(selected.selectedInstanceId, null);
  assert.equal(selected.resultOffset, 0);
  assert.equal("page" in selected, false);
  assert.equal("group" in selected, false);
});

test("Local result window is bounded and every filter transition returns to the first window", () => {
  const paged = createLocalViewState({ resultOffset: 200, resultPageSize: 100 });
  const next = reduceLocalView(paged, { type: "next_result_window", totalCount: 450 });
  const previous = reduceLocalView(next, { type: "previous_result_window" });

  assert.equal(next.resultOffset, 300);
  assert.equal(previous.resultOffset, 200);
  for (const action of [
    { type: "select_location", scope: "user" },
    { type: "select_tool", toolId: "codex" },
    { type: "select_kind", kind: "skill" },
    { type: "set_query", query: "docs" },
    { type: "set_correlation", projectionId: "projection", verifiedComponentId: "component" },
  ]) {
    assert.equal(reduceLocalView(paged, action).resultOffset, 0);
  }
});

test("Local action status is identity-bound and clears on selection or filter changes", () => {
  const selected = createLocalViewState({ selectedInstanceId: "instance-1" });
  const pending = reduceLocalView(selected, {
    type: "set_action_status",
    instanceId: "instance-1",
    status: {
      state: "pending",
      code: "reveal_pending",
      message: "Finder에서 위치를 확인하는 중입니다.",
    },
  });
  const stale = reduceLocalView(pending, {
    type: "set_action_status",
    instanceId: "instance-other",
    status: {
      state: "success",
      code: "revealed",
      message: "Finder에서 위치를 표시했습니다.",
    },
  });

  assert.deepEqual(pending.actionStatus, {
    state: "pending",
    code: "reveal_pending",
    message: "Finder에서 위치를 확인하는 중입니다.",
    instanceId: "instance-1",
  });
  assert.equal(stale, pending);
  assert.equal(reduceLocalView(pending, {
    type: "select_instance",
    instanceId: "instance-2",
  }).actionStatus, null);
  assert.equal(reduceLocalView(pending, {
    type: "select_tool",
    toolId: "codex",
  }).actionStatus, null);
});

test("clearing a Verified context returns to the general Local search defaults", () => {
  const linked = createLocalViewState({
    explorerId: "project-a",
    locationFilter: { scope: "project", projectId: "project-a" },
    toolId: "codex",
    kind: "skill",
    query: "release",
    selectedInstanceId: "instance-a",
    correlationProjectionId: "projection-a",
    verifiedComponentId: "harnesskit.skill.release",
    scrollTop: 220,
  });

  assert.deepEqual(reduceLocalView(linked, { type: "clear_correlation" }), createLocalViewState());
});

test("successive filtered queries retain the snapshot-level tool and project rails", () => {
  const initial = createLocalDataState({
    latestComplete: {
      snapshotId: "snapshot-rails",
      attemptId: "attempt-rails",
      status: "complete",
    },
  });
  const all = reduceLocalScanState(initial, {
    type: "query_result",
    result: {
      snapshotId: "snapshot-rails",
      qualifiedTools: [
        { toolId: "codex", label: "Codex" },
        { toolId: "claude_code", label: "Claude Code" },
      ],
      items: [
        { instanceId: "codex-a", toolId: "codex", projectId: "project-a" },
        { instanceId: "claude-b", toolId: "claude_code", projectId: "project-b" },
      ],
      projects: [
        {
          projectId: "project-a",
          label: "alpha",
          canonicalPath: "/fixture-home/alpha",
        },
        {
          projectId: "project-b",
          label: "beta",
          canonicalPath: "/fixture-home/beta",
        },
      ],
      snapshotSummary: {
        coverage: [
          { projectId: "project-a" },
          { projectId: "project-b" },
        ],
      },
    },
  });
  const filtered = reduceLocalScanState(all, {
    type: "query_result",
    result: {
      snapshotId: "snapshot-rails",
      qualifiedTools: [
        { toolId: "codex", label: "Codex" },
        { toolId: "claude_code", label: "Claude Code" },
      ],
      projects: [
        {
          projectId: "project-a",
          label: "alpha",
          canonicalPath: "/fixture-home/alpha",
        },
        {
          projectId: "project-b",
          label: "beta",
          canonicalPath: "/fixture-home/beta",
        },
      ],
      items: [{ instanceId: "codex-a", toolId: "codex", projectId: "project-a" }],
      snapshotSummary: { coverage: [{ projectId: "project-a" }, { projectId: "project-b" }] },
    },
  });

  assert.deepEqual(filtered.qualifiedTools.map(({ toolId }) => toolId), ["codex", "claude_code"]);
  assert.deepEqual(filtered.projects.map(({ projectId }) => projectId), ["project-a", "project-b"]);
  assert.deepEqual(
    filtered.projects.map(({ canonicalPath }) => canonicalPath),
    ["/fixture-home/alpha", "/fixture-home/beta"],
  );
});

test("declared qualified tools and projects never widen from item or coverage inference", () => {
  const data = reduceLocalScanState(createLocalDataState({
    latestComplete: {
      snapshotId: "snapshot-authority",
      attemptId: "attempt-authority",
      status: "complete",
    },
  }), {
    type: "query_result",
    result: {
      snapshotId: "snapshot-authority",
      qualifiedTools: [],
      projects: [],
      items: [{
        instanceId: "invalid-item",
        toolId: "unqualified-tool",
        projectId: "undeclared-project",
      }],
      snapshotSummary: { coverage: [{ projectId: "undeclared-project" }] },
    },
  });

  assert.deepEqual(data.qualifiedTools, []);
  assert.deepEqual(data.projects, []);
});
