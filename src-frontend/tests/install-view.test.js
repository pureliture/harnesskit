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

test("imported source shows locator and one scope without an editable internal handle", () => {
  const install = createInstallState({
    subject: { sotSnapshotId: "snapshot", componentId: component.component_id },
    sourceBinding: { scope: "project", locator: ".agents/skills/<source>/SKILL.md" },
    form: { profileId: `component:${component.component_id}`, targetId: "codex", scope: "project", targetRoot: "import-source" },
  });
  const html = renderInstallActionSurface({ install, component });
  assert.match(html, /적용 위치: 가져온 원본/);
  assert.match(html, /\.agents\/skills\/&lt;source&gt;\/SKILL.md/);
  assert.match(html, /type="hidden" name="targetRoot" value="import-source"/);
  assert.doesNotMatch(html, /data-install-field="targetRoot"/);
  assert.doesNotMatch(html, /option value="user"/);
  assert.match(html, /option value="project" selected/);
  assert.match(html, /미리보기와 적용 승인을 모두 하기 전에는 원본 파일을 바꾸지 않습니다/);
  const updated = reduceInstallState(install, { type: "update_form", form: { ...install.form, targetId: "claude" } });
  assert.deepEqual(updated.sourceBinding, install.sourceBinding);
});

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
  assert.match(markup, /미리보기의 파일 변경을 표시된 적용 위치에 기록하는 것입니다/);
  assert.match(markup, /대상과 파일 변경 내용을 확인했고, 이 미리보기를 표시된 적용 위치에 기록하도록 승인합니다/);
  assert.match(markup, /이번 적용 취소 · 파일 변경 안 함/);
  assert.match(markup, /id="apply-install"[^>]*(?<! disabled)>/);
  assert.match(markup, /data-show-local-install="harnesskit\.skill\.&lt;release&gt;"/);
  assert.doesNotMatch(markup, /must-not-render|sourcePath|contentHash|<release>|<warning>|<hook>|codex"unsafe/);
});

test("canceling a ready preview clears its approval without changing the selected source", () => {
  const sourceBinding = { scope: "project", locator: ".agents/skills/sample/SKILL.md" };
  const ready = createInstallState({
    ...readyInstallState(),
    sourceBinding,
  });
  const canceled = reduceInstallState(ready, { type: "cancel_preview" });
  assert.equal(canceled.phase, "idle");
  assert.equal(canceled.preview, null);
  assert.equal(canceled.confirmed, false);
  assert.equal(canceled.approvalFingerprint, null);
  assert.deepEqual(canceled.subject, ready.subject);
  assert.deepEqual(canceled.form, ready.form);
  assert.deepEqual(canceled.sourceBinding, sourceBinding);
  assert.equal(canceled.message, "이번 적용을 취소했습니다. 원본 파일은 변경하지 않았습니다.");
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
