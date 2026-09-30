import assert from "node:assert/strict";
import test from "node:test";

import { createInitialState } from "../app-shell.js";

async function loadController() {
  try {
    return await import("../local-workflow-controller.js");
  } catch (error) {
    if (error?.code === "ERR_MODULE_NOT_FOUND") {
      assert.fail("approved local-workflow-controller public seam is not implemented");
    }
    throw error;
  }
}

function completeLocalData(overrides = {}) {
  return {
    phase: "ready",
    stateRevision: 7,
    latestComplete: {
      snapshotId: "snapshot-current",
      attemptId: "attempt-current",
      status: "complete",
    },
    ...overrides,
  };
}

function workflowHarness({ backend, state: initialState } = {}) {
  let state = initialState;
  const commits = [];
  return {
    async create() {
      const { createLocalWorkflowController } = await loadController();
      assert.equal(typeof createLocalWorkflowController, "function");
      return createLocalWorkflowController({
        backend,
        readContext: () => ({
          localData: state.localData,
          localView: state.localView,
          activeSegment: state.ui.activeSegment,
          sotSnapshotId: state.sot?.snapshot?.snapshot_id,
          installEvidenceId: state.sot?.installEvidenceId,
          ignoreEditor: state.ui?.ignoreEditor,
        }),
        commitLocalSlices({ localData, localView }) {
          state = { ...state, localData, localView };
          commits.push(state);
        },
        commitIgnoreEditor(ignoreEditor) {
          state = { ...state, ui: { ...state.ui, ignoreEditor } };
          commits.push(state);
        },
      });
    },
    commits,
    state: () => state,
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolveValue, rejectValue) => {
    resolve = resolveValue;
    reject = rejectValue;
  });
  return { promise, resolve, reject };
}

test("local workflow controller exports the approved factory", async () => {
  const { createLocalWorkflowController } = await loadController();

  assert.equal(typeof createLocalWorkflowController, "function");
});

test("manual Local refresh starts a new scan with an existing snapshot and coalesces repeated clicks", async () => {
  const pending = deferred();
  const queries = [];
  let startCalls = 0;
  const harness = workflowHarness({
    backend: {
      startLocalScan() {
        startCalls += 1;
        return pending.promise;
      },
      async getLocalScanState() {
        return {
          state_revision: 8,
          current_attempt: { attempt_id: "attempt-new", state: "running" },
          latest_complete: {
            snapshot_id: "snapshot-current",
            attempt_id: "attempt-current",
            status: "complete",
          },
        };
      },
      async queryLocalInstances(request) {
        queries.push(request);
        return { snapshotId: request.snapshotId, items: [], counts: { matchedInstances: 0 } };
      },
    },
    state: createInitialState({
      repo: { checkoutId: null },
      localData: completeLocalData(),
      ui: { activeSegment: "local" },
    }),
  });
  const controller = await harness.create();

  const refresh = controller.refreshScan();
  assert.equal(startCalls, 1);
  assert.equal(harness.state().localData.scanStartPending, true);
  assert.match(harness.state().localData.message, /Local scan.*시작/);
  assert.equal(harness.state().localData.latestComplete.snapshotId, "snapshot-current");
  await controller.refreshScan();
  assert.equal(startCalls, 1);

  pending.resolve({ status: "accepted", attempt_id: "attempt-new" });
  await refresh;
  assert.equal(harness.state().localData.scanStartPending, false);
  assert.equal(harness.state().localData.phase, "running");
  assert.equal(harness.state().localData.currentAttempt.attemptId, "attempt-new");
  assert.equal(harness.state().localData.latestComplete.snapshotId, "snapshot-current");
  assert.equal(queries.at(-1).snapshotId, "snapshot-current");
  await controller.refreshScan();
  assert.equal(startCalls, 1);
  controller.dispose();
});

test("manual refresh and automatic session bootstrap share the scan start guard", async () => {
  const session = deferred();
  const start = deferred();
  let startCalls = 0;
  const harness = workflowHarness({
    backend: {
      getLocalScanState() {
        return startCalls === 0 ? session.promise : Promise.resolve({
          state_revision: 1,
          current_attempt: { attempt_id: "attempt-boot", state: "running" },
          latest_complete: null,
          latest_partial: null,
        });
      },
      startLocalScan() {
        startCalls += 1;
        return start.promise;
      },
    },
    state: createInitialState({ ui: { activeSegment: "local" } }),
  });
  const controller = await harness.create();

  const ensuring = controller.ensureSession();
  const manual = controller.refreshScan();
  assert.equal(startCalls, 1);
  assert.equal(harness.state().localData.scanStartPending, true);
  session.resolve({ state_revision: 0, current_attempt: null, latest_complete: null });
  await ensuring;
  assert.equal(startCalls, 1);

  start.resolve({ status: "accepted", attempt_id: "attempt-boot" });
  await manual;
  assert.equal(harness.state().localData.currentAttempt.attemptId, "attempt-boot");
  controller.dispose();
});

test("manual Local refresh reports operation busy and rejected starts without losing the last snapshot", async () => {
  let calls = 0;
  const harness = workflowHarness({
    backend: {
      async startLocalScan() {
        calls += 1;
        if (calls === 1) return { status: "operation_busy" };
        throw new Error("private backend details");
      },
    },
    state: createInitialState({
      localData: completeLocalData(),
      ui: { activeSegment: "local" },
    }),
  });
  const controller = await harness.create();

  await controller.refreshScan();
  assert.equal(harness.state().localData.phase, "busy");
  assert.equal(harness.state().localData.scanStartPending, false);
  assert.match(harness.state().localData.message, /다른 filesystem 작업/);
  assert.equal(harness.state().localData.latestComplete.snapshotId, "snapshot-current");

  await controller.refreshScan();
  assert.equal(calls, 2);
  assert.equal(harness.state().localData.phase, "error");
  assert.equal(harness.state().localData.scanStartPending, false);
  assert.match(harness.state().localData.message, /Local scan을 시작하지 못했습니다/);
  assert.doesNotMatch(harness.state().localData.message, /private backend details/);
  assert.equal(harness.state().localData.latestComplete.snapshotId, "snapshot-current");
  controller.dispose();
});

test("a terminal scan event before a delayed start response cannot revive a completed attempt", async () => {
  const pending = deferred();
  let emit;
  const harness = workflowHarness({
    backend: {
      startLocalScan() { return pending.promise; },
      onLocalScanChanged(handler) { emit = handler; return () => {}; },
      async getLocalScanState() {
        return {
          state_revision: 9,
          current_attempt: null,
          latest_terminal_report: { attempt_id: "attempt-fast", state: "complete" },
          latest_complete: {
            snapshot_id: "snapshot-new",
            attempt_id: "attempt-fast",
            status: "complete",
          },
        };
      },
      async queryLocalInstances(request) {
        return { snapshotId: request.snapshotId, items: [], counts: { matchedInstances: 0 } };
      },
    },
    state: createInitialState({
      localData: completeLocalData(),
      ui: { activeSegment: "local" },
    }),
  });
  const controller = await harness.create();
  controller.start();
  const refresh = controller.refreshScan();
  await emit({
    attempt_id: "attempt-fast",
    sequence: 0,
    state_revision: 9,
    payload: { state: "complete", snapshot_id: "snapshot-new" },
  });
  pending.resolve({ status: "accepted", attempt_id: "attempt-fast" });
  await refresh;

  assert.equal(harness.state().localData.scanStartPending, false);
  assert.equal(harness.state().localData.currentAttempt, null);
  assert.equal(harness.state().localData.phase, "ready");
  assert.equal(harness.state().localData.latestComplete.snapshotId, "snapshot-new");
  controller.dispose();
});

test("manual Local refresh accepts a partial snapshot and preserves it after a later failed attempt", async () => {
  let emit;
  let started = 0;
  const queries = [];
  let scanState = {
    state_revision: 5,
    current_attempt: null,
    latest_terminal_report: { attempt_id: "attempt-old", state: "partial" },
    latest_partial: { snapshot_id: "snapshot-old", attempt_id: "attempt-old", status: "partial" },
  };
  const harness = workflowHarness({
    backend: {
      onLocalScanChanged(handler) { emit = handler; return () => {}; },
      async startLocalScan() {
        started += 1;
        const attemptId = started === 1 ? "attempt-partial" : "attempt-failed";
        scanState = {
          ...scanState,
          state_revision: started === 1 ? 6 : 8,
          current_attempt: { attempt_id: attemptId, state: "running" },
        };
        return { status: "accepted", attempt_id: attemptId };
      },
      async getLocalScanState() { return scanState; },
      async queryLocalInstances(request) {
        queries.push(request.snapshotId);
        return { snapshotId: request.snapshotId, items: [], counts: { matchedInstances: 0 } };
      },
    },
    state: createInitialState({
      localData: { phase: "partial", latestPartial: {
        snapshotId: "snapshot-old", attemptId: "attempt-old", status: "partial",
      } },
      ui: { activeSegment: "local" },
    }),
  });
  const controller = await harness.create();
  controller.start();

  await controller.refreshScan();
  assert.equal(harness.state().localData.phase, "running");
  assert.equal(harness.state().localData.latestPartial.snapshotId, "snapshot-old");

  scanState = {
    state_revision: 7,
    current_attempt: null,
    latest_terminal_report: { attempt_id: "attempt-partial", state: "partial" },
    latest_partial: { snapshot_id: "snapshot-partial", attempt_id: "attempt-partial", status: "partial" },
  };
  await emit({
    attempt_id: "attempt-partial",
    sequence: 0,
    state_revision: 7,
    payload: { state: "partial", snapshot_id: "snapshot-partial" },
  });
  assert.equal(harness.state().localData.phase, "partial");
  assert.equal(harness.state().localData.latestPartial.snapshotId, "snapshot-partial");
  assert.equal(queries.at(-1), "snapshot-partial");

  await controller.refreshScan();
  assert.equal(started, 2);
  scanState = {
    state_revision: 9,
    current_attempt: null,
    latest_terminal_report: { attempt_id: "attempt-failed", state: "failed", error_code: "scan_failed" },
    latest_partial: { snapshot_id: "snapshot-partial", attempt_id: "attempt-partial", status: "partial" },
  };
  await emit({
    attempt_id: "attempt-failed",
    sequence: 0,
    state_revision: 9,
    payload: { state: "failed", code: "scan_failed" },
  });
  assert.equal(harness.state().localData.phase, "partial");
  assert.equal(harness.state().localData.latestTerminalReport.state, "failed");
  assert.match(harness.state().localData.message, /스캔이 실패했습니다/);
  assert.doesNotMatch(harness.state().localData.message, /완료되었습니다/);
  assert.equal(harness.state().localData.latestPartial.snapshotId, "snapshot-partial");
  assert.equal(queries.at(-1), "snapshot-partial");
  controller.dispose();
});

test("project ignore open reads only the verified project context and commits normalized editor state", async () => {
  const calls = [];
  const harness = workflowHarness({
    backend: {
      async getProjectIgnore(request) {
        calls.push(request);
        return {
          source_revision: "ignore-r7",
          exact_text: "dist/\n",
          issue: { safe_message: "규칙을 확인하세요." },
          untrusted: "must not reach editor state",
        };
      },
    },
    state: createInitialState({
      localData: completeLocalData(),
      localView: { locationFilter: { scope: "project", projectId: "project-alpha" } },
      ui: { activeSegment: "local" },
    }),
  });
  const controller = await harness.create();

  await controller.openProjectIgnoreEditor();

  assert.deepEqual(calls, [{ snapshotId: "snapshot-current", projectId: "project-alpha" }]);
  assert.deepEqual(harness.state().ui.ignoreEditor, {
    open: true,
    busy: false,
    snapshotId: "snapshot-current",
    projectId: "project-alpha",
    sourceRevision: "ignore-r7",
    exactText: "dist/\n",
    issue: "규칙을 확인하세요.",
  });
  assert.equal("untrusted" in harness.state().ui.ignoreEditor, false);
});

test("project ignore save sends only the CAS payload, retains the current complete snapshot, and reconciles queued follow-up", async () => {
  const calls = [];
  let scanStateCalls = 0;
  const latestComplete = {
    snapshotId: "snapshot-before-save",
    attemptId: "attempt-before-save",
    status: "complete",
  };
  const harness = workflowHarness({
    backend: {
      async saveProjectIgnoreAndRescan(request) {
        calls.push(request);
        return {
          rescan: {
            status: "queued_after_current",
            currentAttemptId: "attempt-running",
            ignored: "must not enter local state",
          },
        };
      },
      async getLocalScanState() {
        scanStateCalls += 1;
        return {
          state_revision: 8,
          current_attempt: {
            attempt_id: "attempt-running",
            state: "running",
          },
          latest_complete: {
            snapshot_id: "snapshot-before-save",
            attempt_id: "attempt-before-save",
            status: "complete",
          },
          latest_partial: null,
          latest_terminal_report: null,
        };
      },
    },
    state: createInitialState({
      localData: completeLocalData({
        phase: "running",
        currentAttempt: { attemptId: "attempt-running", state: "running" },
        latestComplete,
      }),
      localView: { locationFilter: { scope: "project", projectId: "project-alpha" } },
      ui: {
        activeSegment: "local",
        ignoreEditor: {
          open: true,
          busy: false,
          snapshotId: "snapshot-before-save",
          projectId: "project-alpha",
          sourceRevision: "ignore-r7",
          exactText: "old/\n",
          issue: null,
        },
      },
    }),
  });
  const controller = await harness.create();

  await controller.saveProjectIgnore({ exactText: "generated/\n" });

  assert.deepEqual(calls, [{
    snapshotId: "snapshot-before-save",
    projectId: "project-alpha",
    sourceRevision: "ignore-r7",
    exactText: "generated/\n",
  }]);
  assert.deepEqual(harness.state().localData.latestComplete, latestComplete);
  assert.equal(harness.state().localData.ignoreRescanStatus, "queued");
  assert.equal(
    harness.state().localData.message,
    "현재 스캔 완료 후 제외 규칙 반영 스캔을 실행합니다.",
  );
  assert.equal(harness.state().ui.ignoreEditor, null);
  assert.equal(scanStateCalls, 1);
});

test("correlation identity mismatch fails closed without committing the response projection", async () => {
  const harness = workflowHarness({
    backend: {
      async getLocalScanState() {
        return {
          state_revision: 7,
          current_attempt: null,
          latest_complete: {
            snapshot_id: "snapshot-current",
            attempt_id: "attempt-current",
            status: "complete",
          },
          latest_partial: null,
          latest_terminal_report: null,
        };
      },
      async getCorrelationProjection(request) {
        assert.deepEqual(request, {
          localSnapshotId: "snapshot-current",
          sotSnapshotId: "sot-snapshot-current",
          installEvidenceId: "install-evidence-current",
        });
        return {
          projectionId: "projection-stale",
          localSnapshotId: "snapshot-stale",
          sotSnapshotId: "sot-snapshot-current",
          installEvidenceId: "install-evidence-current",
        };
      },
    },
    state: createInitialState({
      localData: completeLocalData(),
      localView: { locationFilter: { scope: "project", projectId: "project-alpha" } },
      sot: {
        snapshot: { snapshot_id: "sot-snapshot-current" },
        installEvidenceId: "install-evidence-current",
      },
      ui: { activeSegment: "local" },
    }),
  });
  const controller = await harness.create();

  await controller.openVerifiedContext({
    componentId: "component-alpha",
    installEvidenceId: "install-evidence-current",
  });

  assert.ok(harness.commits.length > 0, "mismatch should surface a safe state update");
  for (const commit of harness.commits) {
    assert.equal(commit.localData.correlationProjection, null);
    assert.equal(commit.localView.correlationProjectionId, null);
    assert.equal(commit.localView.verifiedComponentId, null);
  }
});
