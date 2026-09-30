import assert from "node:assert/strict";
import test from "node:test";

import {
  createInstallState,
  normalizeApplyInstallResponse,
  normalizeInstallPreviewResponse,
  reduceInstallState,
} from "../install-flow.js";
import { renderInstallActionSurface } from "../install-view.js";

const component = {
  component_id: "harnesskit.skill.<release>",
  title: "Release <Safety>",
  targets: [{ target_id: 'codex"unsafe', support_status: "runtime_supported" }],
};

function readyInstallState(targetId = 'codex"unsafe') {
  const request = {
    sotSnapshotId: "snapshot-install-view",
    profileId: "harnesskit.profile.engineering",
    targetIds: [targetId],
    scope: "project",
    targetRoot: "/tmp/<project>",
  };
  const preview = normalizeInstallPreviewResponse({
    previewId: "preview-view",
    fingerprint: "sha256:<unsafe>",
    ...request,
    targets: request.targetIds,
    components: [component.component_id],
    artifacts: [{
      componentId: component.component_id,
      target: targetId,
      destination: ".agents/skills/<release>/SKILL.md",
      sourcePath: "dist/codex/<must-not-render>",
      contentHash: "must-not-render",
    }],
    skippedWrites: [{ destination: ".codex/<skip>", reasonCode: "foreign_region" }],
    warnings: ["Merge <warning>"],
    runtimeGates: [{
      gateId: "runtime-hook-approval",
      target: targetId,
      safeReason: "Runtime <hook> approval required.",
      requiredBeforeApply: true,
      requiredBeforeRuntime: true,
    }],
    runtimeSurfaces: ["project-skills"],
    nonAtomicBoundary: true,
    requiredApprovals: { overwrite: false, runtimeHooks: true },
  }, request);
  const state = createInstallState({
    phase: "preview-ready",
    subject: { sotSnapshotId: request.sotSnapshotId, componentId: component.component_id },
    form: {
      profileId: request.profileId,
      targetId: request.targetIds[0],
      scope: request.scope,
      targetRoot: request.targetRoot,
    },
    preview,
  });
  return reduceInstallState(state, {
    type: "set_approval",
    confirmed: true,
    overwrite: false,
    allowRuntimeHooks: true,
  });
}

test("SoT install surface renders typed preview and explicit fingerprint-bound approval", () => {
  const markup = renderInstallActionSurface({
    install: readyInstallState(),
    component,
    operationBlocked: false,
  });

  assert.match(markup, /id="install-form"/);
  assert.match(markup, /Install preview/);
  assert.match(markup, /Preview fingerprint/);
  assert.match(markup, /sha256:&lt;unsafe&gt;/);
  assert.match(markup, /\.agents\/skills\/&lt;release&gt;\/SKILL\.md/);
  assert.match(markup, /Merge &lt;warning&gt;/);
  assert.match(markup, /Runtime &lt;hook&gt; approval required/);
  assert.match(markup, /자동 rollback을 제공하지 않습니다/);
  assert.match(markup, /id="confirm-install"[^>]* checked/);
  assert.match(markup, /id="apply-install"[^>]*(?<! disabled)>/);
  assert.match(markup, /data-show-local-install="harnesskit\.skill\.&lt;release&gt;"/);
  assert.doesNotMatch(markup, /must-not-render|sourcePath|contentHash|<release>|<warning>|<hook>|codex"unsafe/);
});

test("partial apply truth is an alert and never rendered as verified success", () => {
  const execution = normalizeApplyInstallResponse({
    status: "partial",
    destinations: [
      { target: "codex", destination: ".agents/skills/release/SKILL.md", applyState: "changed", verifyState: "verified", code: null },
      { target: "codex", destination: ".codex/agents/release.toml", applyState: "changed", verifyState: "failed", code: "verify_mismatch" },
    ],
  });
  const markup = renderInstallActionSurface({
    install: { ...readyInstallState(), phase: "result", confirmed: false, execution },
    component,
    operationBlocked: false,
  });

  assert.match(markup, /execution-result--partial/);
  assert.match(markup, /role="alert"/);
  assert.match(markup, /changed/);
  assert.match(markup, /failed/);
  assert.match(markup, /verify_mismatch/);
  assert.doesNotMatch(markup, /Install verified|검증 성공|설치 성공/);
});

test("unverified target artwork stays absent from companion preview and result while native options stay text-only", () => {
  const qualifiedComponent = {
    ...component,
    targets: [{ target_id: "codex", support_status: "runtime_supported" }],
  };
  const execution = normalizeApplyInstallResponse({
    status: "success",
    installEvidenceId: "evidence-qualified-target",
    destinations: [{
      target: "codex",
      destination: ".agents/skills/release/SKILL.md",
      applyState: "changed",
      verifyState: "verified",
      code: null,
    }],
  });
  const markup = renderInstallActionSurface({
    install: { ...readyInstallState("codex"), phase: "result", execution },
    component: qualifiedComponent,
    operationBlocked: false,
  });

  assert.equal((markup.match(/data-tool-identity-context="install_selected_target"/g) ?? []).length, 1);
  assert.equal((markup.match(/data-tool-identity-context="install_preview"/g) ?? []).length, 1);
  assert.equal((markup.match(/data-tool-identity-context="install_result"/g) ?? []).length, 1);
  assert.equal((markup.match(/data-tool-identity="codex"/g) ?? []).length, 3);
  assert.equal((markup.match(/data-tool-identity-mode="text"/g) ?? []).length, 3);
  assert.doesNotMatch(markup, /<img|data-tool-identity-image/);
  const nativeTargetSelect = markup.match(/<select id="install-target"[\s\S]*?<\/select>/)?.[0] ?? "";
  assert.match(nativeTargetSelect, /<option[^>]*>codex · runtime_supported<\/option>/);
  assert.doesNotMatch(nativeTargetSelect, /<img|data-tool-identity/);
});
