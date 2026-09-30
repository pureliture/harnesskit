import assert from "node:assert/strict";
import test from "node:test";

import { createBackendClient } from "../backend.js";

test("repo and install methods forward exact approved commands and camelCase payloads", async () => {
  const calls = [];
  const client = createBackendClient(async (command, payload) => {
    calls.push({ command, payload });
    return { command };
  });

  await client.getBootstrapState();
  await client.setAppearanceMode("Dark");
  await client.setWorkspaceLayout({
    expectedLayoutRevision: 7,
    preferredLeftWidthPx: 320,
    preferredRightWidthPx: 400,
  });
  await client.completeBootstrap(4, 7, 9, {
    childElementCount: 1,
    textLength: 321,
    desktopAppPresent: true,
    resolvedMode: "dark",
    viewportWidth: 1200,
    viewportHeight: 800,
    desktopWidth: 1200,
    desktopHeight: 800,
    layoutVisible: true,
    appliedLayoutRevision: 7,
    appliedTypographyRevision: 9,
    appliedPreferredPair: {
      preferredLeftWidthPx: 320,
      preferredRightWidthPx: 400,
    },
    appliedCollapsedPair: {
      leftCollapsed: false,
      rightCollapsed: false,
    },
    appliedTypographyPreset: "Default",
    layoutMode: "three-pane",
    shellFrame: { x: 0, y: 0, width: 1200, height: 800 },
    paneFrames: {
      left: { x: 0, y: 0, width: 311, height: 800 },
      center: { x: 323, y: 0, width: 480, height: 800 },
      right: { x: 815, y: 0, width: 385, height: 800 },
    },
    activeSeparatorCount: 2,
    disabledSeparatorCount: 0,
  });
  await client.getSotSessionState();
  await client.loadSotSnapshot("checkout-7");
  await client.cloneCheckout();
  await client.registerCheckout("/tmp/harnesskit");
  await client.previewInstall({
    checkoutId: "checkout-7",
    sotSnapshotId: "sot-snapshot-7",
    profileId: "harnesskit.profile.engineering",
    targetIds: ["codex"],
    scope: "project",
    targetRoot: "/tmp/target",
    sourceCheckoutPath: "/must/not/pass",
  });
  await client.applyInstall({
    previewId: "preview-9",
    approvals: {
      confirmed: true,
      semanticFingerprint: "sha256:preview-9",
      overwrite: false,
      allowRuntimeHooks: true,
      rawPlan: "must not pass",
    },
  });

  assert.deepEqual(calls, [
    {
      command: "get_bootstrap_state",
      payload: undefined,
    },
    {
      command: "set_appearance_mode",
      payload: { logicalMode: "Dark" },
    },
    {
      command: "set_workspace_layout",
      payload: {
        request: {
          expectedLayoutRevision: 7,
          preferredLeftWidthPx: 320,
          preferredRightWidthPx: 400,
          leftCollapsed: false,
          rightCollapsed: false,
        },
      },
    },
    {
      command: "complete_bootstrap",
      payload: {
        appearanceRevision: 4,
        layoutRevision: 7,
        typographyRevision: 9,
        uiProbe: {
          childElementCount: 1,
          textLength: 321,
          desktopAppPresent: true,
          resolvedMode: "dark",
          viewportWidth: 1200,
          viewportHeight: 800,
          desktopWidth: 1200,
          desktopHeight: 800,
          layoutVisible: true,
          appliedLayoutRevision: 7,
          appliedTypographyRevision: 9,
          appliedPreferredPair: {
            preferredLeftWidthPx: 320,
            preferredRightWidthPx: 400,
          },
          appliedCollapsedPair: {
            leftCollapsed: false,
            rightCollapsed: false,
          },
          appliedTypographyPreset: "Default",
          layoutMode: "three-pane",
          shellFrame: { x: 0, y: 0, width: 1200, height: 800 },
          paneFrames: {
            left: { x: 0, y: 0, width: 311, height: 800 },
            center: { x: 323, y: 0, width: 480, height: 800 },
            right: { x: 815, y: 0, width: 385, height: 800 },
          },
          activeSeparatorCount: 2,
          disabledSeparatorCount: 0,
        },
      },
    },
    {
      command: "get_sot_session_state",
      payload: undefined,
    },
    {
      command: "load_sot_snapshot",
      payload: { checkoutId: "checkout-7" },
    },
    {
      command: "clone_checkout",
      payload: undefined,
    },
    {
      command: "register_checkout",
      payload: { checkoutPath: "/tmp/harnesskit" },
    },
    {
      command: "preview_install",
      payload: {
        checkoutId: "checkout-7",
        request: {
          sotSnapshotId: "sot-snapshot-7",
          profileId: "harnesskit.profile.engineering",
          targetIds: ["codex"],
          scope: "project",
          targetRoot: "/tmp/target",
        },
      },
    },
    {
      command: "apply_install",
      payload: {
        previewId: "preview-9",
        approvals: {
          confirmed: true,
          semanticFingerprint: "sha256:preview-9",
          overwrite: false,
          allowRuntimeHooks: true,
        },
      },
    },
  ]);
});

test("static mode fails closed when the Tauri invoke bridge is absent", async () => {
  const client = createBackendClient(undefined);

  await assert.rejects(
    client.registerCheckout("/tmp/harnesskit"),
    /Tauri runtime에서만 이 작업을 실행할 수 있습니다/,
  );
  await assert.rejects(
    client.cloneCheckout(),
    /Tauri runtime에서만 이 작업을 실행할 수 있습니다/,
  );
  assert.equal("restoreCheckout" in client, false);
  assert.equal("scanHarness" in client, false);
});

test("workspace layout accepts the approved compact panel minimums", async () => {
  const calls = [];
  const client = createBackendClient(async (command, payload) => {
    calls.push({ command, payload });
    return {};
  });

  await client.setWorkspaceLayout({
    expectedLayoutRevision: 0,
    preferredLeftWidthPx: 200,
    preferredRightWidthPx: 184,
  });

  assert.deepEqual(calls, [{
    command: "set_workspace_layout",
    payload: {
      request: {
        expectedLayoutRevision: 0,
        preferredLeftWidthPx: 200,
        preferredRightWidthPx: 184,
        leftCollapsed: false,
        rightCollapsed: false,
      },
    },
  }]);
  await assert.rejects(
    client.setWorkspaceLayout({
      expectedLayoutRevision: 0,
      preferredLeftWidthPx: 199,
      preferredRightWidthPx: 184,
    }),
    /workspace layout preference/,
  );
  await assert.rejects(
    client.setWorkspaceLayout({
      expectedLayoutRevision: 0,
      preferredLeftWidthPx: 200,
      preferredRightWidthPx: 183,
    }),
    /workspace layout preference/,
  );
});

test("registration validates emptiness without rewriting an exact picker path", async () => {
  const calls = [];
  const client = createBackendClient(async (command, payload) => {
    calls.push({ command, payload });
    return {};
  });

  await client.registerCheckout("/private/tmp/harnesskit ");
  await assert.rejects(client.registerCheckout("   "), /checkout 경로/);

  assert.deepEqual(calls, [{
    command: "register_checkout",
    payload: { checkoutPath: "/private/tmp/harnesskit " },
  }]);
});

test("Local methods forward revision-bound safe DTOs and the exact scan event name", async () => {
  const calls = [];
  const subscriptions = [];
  const client = createBackendClient(
    async (command, payload) => {
      calls.push({ command, payload });
      return { command };
    },
    async (eventName, handler) => {
      subscriptions.push({ eventName, handler });
      return () => {};
    },
  );

  await client.getLocalScanState();
  await client.startLocalScan();
  await client.getCorrelationProjection({
    localSnapshotId: "snapshot-7",
    sotSnapshotId: "sot-3",
    installEvidenceId: "install-evidence-5",
  });
  await client.queryLocalInstances({
    snapshotId: "snapshot-7",
    locationFilter: { scope: "project", projectId: "project-9" },
    toolId: "codex",
    kind: "skill",
    query: "release",
    correlationProjectionId: "projection-7",
    verifiedComponentId: "harnesskit.skill.release",
    rawPath: "/must/not/pass",
    sourceBody: "must not pass",
  });
  await client.getLocalInstanceDetail({
    snapshotId: "snapshot-7",
    correlationProjectionId: "projection-7",
    instanceId: "instance-4",
    rawPath: "/must/not/pass",
  });
  await client.actOnLocalInstance({
    snapshotId: "snapshot-7",
    instanceId: "instance-4",
    action: "reveal",
    path: "/must/not/pass",
    body: "must not pass",
  });
  await client.prepareLocalRemoval({
    snapshotId: "snapshot-7",
    instanceIds: ["instance-4", "instance-5"],
    rawPath: "/must/not/pass",
    sourceBody: "must not pass",
  });
  await client.applyLocalRemoval({
    planId: "removal-plan-2",
    planDigest: "digest-2",
    confirmed: true,
    rawPath: "/must/not/pass",
  });
  await client.reconcileLocalRemoval({
    snapshotId: "snapshot-8",
    expectedAttemptId: "attempt-8",
    instanceIds: ["instance-4", "instance-5"],
    rawPath: "/must/not/pass",
  });
  const events = [];
  await client.onLocalScanChanged((event) => events.push(event));
  subscriptions[0].handler({ payload: { attempt_id: "attempt-3", sequence: 2 } });

  assert.deepEqual(calls, [
    { command: "get_local_scan_state", payload: undefined },
    { command: "start_local_scan", payload: undefined },
    {
      command: "get_correlation_projection",
      payload: {
        localSnapshotId: "snapshot-7",
        sotSnapshotId: "sot-3",
        installEvidenceId: "install-evidence-5",
      },
    },
    {
      command: "query_local_instances",
      payload: {
        request: {
          snapshotId: "snapshot-7",
          locationFilter: { scope: "project", projectId: "project-9" },
          toolId: "codex",
          kind: "skill",
          query: "release",
          correlationProjectionId: "projection-7",
          verifiedComponentId: "harnesskit.skill.release",
        },
      },
    },
    {
      command: "get_local_instance_detail",
      payload: {
        snapshotId: "snapshot-7",
        correlationProjectionId: "projection-7",
        instanceId: "instance-4",
      },
    },
    {
      command: "act_on_local_instance",
      payload: { snapshotId: "snapshot-7", instanceId: "instance-4", action: "reveal" },
    },
    {
      command: "prepare_local_removal",
      payload: {
        request: { snapshotId: "snapshot-7", instanceIds: ["instance-4", "instance-5"] },
      },
    },
    {
      command: "apply_local_removal",
      payload: {
        request: { planId: "removal-plan-2", planDigest: "digest-2", confirmed: true },
      },
    },
    {
      command: "reconcile_local_removal",
      payload: {
        request: {
          snapshotId: "snapshot-8",
          expectedAttemptId: "attempt-8",
          instanceIds: ["instance-4", "instance-5"],
        },
      },
    },
  ]);
  assert.equal(subscriptions[0].eventName, "local_scan_changed");
  assert.deepEqual(events, [{ attempt_id: "attempt-3", sequence: 2 }]);
});

test("picker, source preview, and AI methods forward only approved revision-bound fields", async () => {
  const calls = [];
  const client = createBackendClient(async (command, payload) => {
    calls.push({ command, payload });
    return { command };
  });

  await client.pickCheckoutDirectory();
  await client.openLocalSourcePreview({
    snapshotId: "snapshot-7",
    instanceId: "instance-4",
    viewGeneration: 11,
    path: "/must/not/pass",
    sourceBody: "must not pass",
  });
  await client.readLocalSourcePreviewChunk({
    previewSessionId: "preview-session-3",
    sourceRevision: "source-revision-5",
    chunkIndex: 2,
    viewGeneration: 999,
    path: "/must/not/pass",
  });
  await client.closeLocalSourcePreview({
    viewGeneration: 12,
    previewSessionId: "preview-session-3",
  });
  await client.getAiProviderConfig();
  await client.saveAiProviderConfig({
    expectedProviderRevision: "provider-revision-2",
    baseUrl: "https://provider.example/v1",
    model: "model-a",
    apiKey: "secret-key",
    sourceBody: "must not pass",
  });
  await client.deleteAiProviderKey("provider-revision-3");
  await client.explainLocalSource({
    snapshotId: "snapshot-7",
    instanceId: "instance-4",
    sourceRevision: "source-revision-5",
    providerRevision: "provider-revision-3",
    sourceBody: "must not pass",
    baseUrl: "https://must.not/pass",
    apiKey: "must not pass",
  });

  assert.deepEqual(calls, [
    { command: "pick_checkout_directory", payload: undefined },
    {
      command: "open_local_source_preview",
      payload: {
        request: {
          snapshotId: "snapshot-7",
          instanceId: "instance-4",
          viewGeneration: 11,
        },
      },
    },
    {
      command: "read_local_source_preview_chunk",
      payload: {
        request: {
          previewSessionId: "preview-session-3",
          sourceRevision: "source-revision-5",
          chunkIndex: 2,
        },
      },
    },
    {
      command: "close_local_source_preview",
      payload: {
        request: {
          viewGeneration: 12,
          previewSessionId: "preview-session-3",
        },
      },
    },
    { command: "get_ai_provider_config", payload: undefined },
    {
      command: "save_ai_provider_config",
      payload: {
        request: {
          expectedProviderRevision: "provider-revision-2",
          baseUrl: "https://provider.example/v1",
          model: "model-a",
          apiKey: "secret-key",
        },
      },
    },
    {
      command: "delete_ai_provider_key",
      payload: { request: { providerRevision: "provider-revision-3" } },
    },
    {
      command: "explain_local_source",
      payload: {
        request: {
          snapshotId: "snapshot-7",
          instanceId: "instance-4",
          sourceRevision: "source-revision-5",
          providerRevision: "provider-revision-3",
        },
      },
    },
  ]);
});

test("project ignore editor commands use opaque snapshot and project identities", async () => {
  const calls = [];
  const client = createBackendClient(async (command, payload) => {
    calls.push({ command, payload });
    return {};
  });

  await client.getProjectIgnore({ snapshotId: "snapshot-1", projectId: "project-1" });
  await client.saveProjectIgnoreAndRescan({
    snapshotId: "snapshot-1",
    projectId: "project-1",
    sourceRevision: "missing-v1",
    exactText: "dist/\n",
  });

  assert.deepEqual(calls, [
    { command: "get_project_ignore", payload: { snapshotId: "snapshot-1", projectId: "project-1" } },
    {
      command: "save_project_ignore_and_rescan",
      payload: {
        request: {
          snapshotId: "snapshot-1",
          projectId: "project-1",
          sourceRevision: "missing-v1",
          exactText: "dist/\n",
        },
      },
    },
  ]);
});

test("Local client fails closed on invalid filters, correlation, or action enums", async () => {
  const client = createBackendClient(async () => ({}));

  await assert.rejects(
    client.queryLocalInstances({
      snapshotId: "snapshot-7",
      locationFilter: { scope: "machine", projectId: null },
      query: "",
    }),
    /location scope/,
  );
  await assert.doesNotReject(client.queryLocalInstances({
    snapshotId: "snapshot-7",
    locationFilter: { scope: "project", projectId: null },
    query: "",
  }));
  await assert.rejects(
    client.queryLocalInstances({
      snapshotId: "snapshot-7",
      locationFilter: { scope: "all", projectId: null },
      query: "",
      verifiedComponentId: "harnesskit.skill.release",
    }),
    /correlation projection/,
  );
  await assert.rejects(
    client.actOnLocalInstance({
      snapshotId: "snapshot-7",
      instanceId: "instance-4",
      action: "viewer",
    }),
    /read-only action/,
  );
  await assert.rejects(
    client.prepareLocalRemoval({
      snapshotId: "snapshot-7",
      instanceIds: ["instance-4", "instance-4"],
    }),
    /중복 ID/,
  );
  await assert.rejects(
    client.applyLocalRemoval({
      planId: "removal-plan-2",
      planDigest: "digest-2",
      confirmed: false,
    }),
    /제거 확인/,
  );
  await assert.rejects(
    client.reconcileLocalRemoval({
      snapshotId: "snapshot-7",
      expectedAttemptId: "attempt-7",
      instanceIds: ["instance-4", "instance-4"],
    }),
    /중복 ID/,
  );
});
