import assert from "node:assert/strict";
import test from "node:test";

import {
  canApplyInstall,
  classifyExecution,
  createInstallState,
  normalizeApplyInstallResponse,
  normalizeInstallPreviewResponse,
  reduceInstallState,
} from "../install-flow.js";

test("approved install DTOs normalize into one typed frontend truth boundary", () => {
  const request = {
    sotSnapshotId: "sot-snapshot-7",
    profileId: "harnesskit.profile.engineering",
    targetIds: ["codex"],
    scope: "project",
    targetRoot: "/tmp/project",
  };
  const preview = normalizeInstallPreviewResponse({
    previewId: "preview-typed-7",
    fingerprint: "sha256:typed",
    sotSnapshotId: request.sotSnapshotId,
    profileId: request.profileId,
    scope: request.scope,
    targetRoot: request.targetRoot,
    targets: ["codex"],
    components: ["harnesskit.skill.release"],
    runtimeSurfaces: ["project-skills"],
    runtimeGates: [{
      gateId: "runtime-hook-approval",
      target: "codex",
      safeReason: "Runtime hook execution requires approval.",
      requiredBeforeApply: true,
      requiredBeforeRuntime: true,
    }],
    artifacts: [{
      componentId: "harnesskit.skill.release",
      target: "codex",
      destination: ".agents/skills/release/SKILL.md",
      sourcePath: "/must/not/normalize",
    }],
    skippedWrites: [],
    warnings: ["기존 파일은 merge될 수 있습니다."],
    nonAtomicBoundary: true,
    requiredApprovals: { overwrite: false, runtimeHooks: true },
  }, request);
  const execution = normalizeApplyInstallResponse({
    operationId: "install-operation-7",
    previewId: "preview-typed-7",
    status: "partial",
    destinations: [
      { target: "codex", destination: ".agents/skills/release/SKILL.md", applyState: "changed", verifyState: "verified", code: null },
      { target: "codex", destination: ".codex/agents/release.toml", applyState: "changed", verifyState: "failed", code: "verify_mismatch" },
    ],
    installEvidenceId: null,
  });

  assert.equal(preview.previewId, "preview-typed-7");
  assert.equal(preview.semanticFingerprint, "sha256:typed");
  assert.deepEqual(preview.request, request);
  assert.equal(preview.plan.artifacts[0].componentId, "harnesskit.skill.release");
  assert.deepEqual(preview.plan.requiredApprovals, { overwrite: false, runtimeHooks: true });
  assert.equal(preview.plan.runtimeGates[0].gateId, "runtime-hook-approval");
  assert.equal(preview.plan.runtimeGates[0].safeReason, "Runtime hook execution requires approval.");
  assert.equal(execution.status, "partial");
  assert.equal(execution.destinations[0].applyState, "changed");
  assert.equal(execution.destinations[1].verifyState, "failed");
  assert.equal(classifyExecution(execution).kind, "partial");
  assert.doesNotMatch(classifyExecution(execution).message, /성공|완료/);
});

test("install normalization accepts snake case transport without widening the safe shape", () => {
  const preview = normalizeInstallPreviewResponse({
    preview_id: "preview-snake",
    fingerprint: "sha256:snake",
    sot_snapshot_id: "sot-snake",
    profile_id: "harnesskit.profile.engineering",
    scope: "user",
    target_root: "/tmp/user",
    targets: [{ target_id: "codex" }],
    components: [{ component_id: "harnesskit.skill.release" }],
    artifacts: [{
      target_id: "codex",
      component_id: "harnesskit.skill.release",
      destination: ".codex/skills/release/SKILL.md",
      source_path: "/must/not/escape",
      content_hash: "must-not-escape",
    }],
    skipped_writes: [],
    runtime_gates: [],
    non_atomic_boundary: true,
    required_approvals: ["confirmed"],
  });
  const execution = normalizeApplyInstallResponse({
    operation_id: "operation-snake",
    status: "success",
    destinations: [{
      target: "codex",
      destination: ".codex/skills/release/SKILL.md",
      apply_state: "changed",
      verify_state: "verified",
      code: null,
    }],
    install_evidence_id: "evidence-snake",
  });

  assert.equal(preview.request.profileId, "harnesskit.profile.engineering");
  assert.equal(preview.plan.artifacts[0].targetId, "codex");
  assert.equal("sourcePath" in preview.plan.artifacts[0], false);
  assert.equal("contentHash" in preview.plan.artifacts[0], false);
  assert.equal(execution.destinations[0].verifyState, "verified");
  assert.equal(execution.installEvidenceId, "evidence-snake");
  assert.equal(classifyExecution(execution).kind, "success");
});

test("apply stays gated until a preview and explicit confirmation exist", () => {
  const subject = {
    sotSnapshotId: "sot-snapshot-9",
    componentId: "harnesskit.skill.engineering",
  };
  const form = {
    profileId: "harnesskit.profile.engineering",
    targetId: "codex",
    scope: "user",
    targetRoot: "/tmp/target",
  };
  const preview = {
    previewId: "preview-9",
    fingerprint: "sha256:preview-9",
    sotSnapshotId: "sot-snapshot-9",
    profileId: "harnesskit.profile.engineering",
    targets: ["codex"],
    components: ["harnesskit.skill.engineering"],
    scope: "user",
    targetRoot: "/tmp/target",
    artifacts: [],
    skippedWrites: [],
    warnings: [],
    runtimeGates: [],
    nonAtomicBoundary: true,
  };
  const ready = createInstallState({ phase: "preview-ready", subject, form, preview });
  const confirmed = reduceInstallState(ready, {
    type: "set_approval",
    confirmed: true,
    overwrite: false,
    allowRuntimeHooks: false,
  });

  assert.equal(canApplyInstall(createInstallState()), false);
  assert.equal(canApplyInstall(ready), false);
  assert.equal(canApplyInstall(confirmed), true);
  assert.equal(
    canApplyInstall({ ...confirmed, overwrite: true }),
    true,
  );
  assert.equal(
    canApplyInstall({ ...confirmed, form: { ...form, targetRoot: "/tmp/other" } }),
    false,
  );
  assert.equal(
    canApplyInstall({ ...confirmed, phase: "result" }),
    false,
  );

  const overwritePreview = {
    ...preview,
    requiredApprovals: { overwrite: true, runtimeHooks: false },
  };
  const overwriteReady = createInstallState({
    phase: "preview-ready",
    subject,
    form,
    preview: overwritePreview,
  });
  const confirmedOnly = reduceInstallState(overwriteReady, {
    type: "set_approval",
    confirmed: true,
    overwrite: false,
    allowRuntimeHooks: false,
  });
  assert.equal(canApplyInstall(confirmedOnly), false);
  assert.equal(canApplyInstall(reduceInstallState(confirmedOnly, {
    type: "set_approval",
    confirmed: true,
    overwrite: true,
    allowRuntimeHooks: false,
  })), true);
});

test("execution classification never promotes apply or verify failures to success", () => {
  const contradictory = normalizeApplyInstallResponse({
    status: "success",
    destinations: [{ target: "codex", destination: "a", applyState: "changed", verifyState: "failed" }],
  });
  assert.equal(contradictory.status, "partial");
  assert.equal(
    classifyExecution({
      status: "success",
      destinations: [{ target: "codex", destination: "a", applyState: "changed", verifyState: "verified" }],
    }).kind,
    "success",
  );
  assert.equal(
    classifyExecution({
      status: "partial",
      destinations: [{ target: "codex", destination: "a", applyState: "changed", verifyState: "failed" }],
    }).kind,
    "partial",
  );
  assert.equal(
    classifyExecution({
      status: "failure",
      destinations: [{ target: "codex", destination: "a", applyState: "failed", verifyState: "unverified" }],
    }).kind,
    "failure",
  );
  assert.equal(
    classifyExecution({
      status: "success",
      destinations: [{ target: "codex", destination: ".codex/missing", applyState: "changed", verifyState: "failed" }],
    }).kind,
    "partial",
  );
  assert.doesNotMatch(
    classifyExecution({
      status: "partial",
      destinations: [{ target: "codex", destination: "a", applyState: "changed", verifyState: "failed" }],
    }).message,
    /성공|완료/,
  );
});
