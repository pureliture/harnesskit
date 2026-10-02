import test from "node:test";
import assert from "node:assert/strict";
import { renderLocalInspector } from "../local-view.js";
import { createBackendClient } from "../backend.js";
import { createInitialState, renderAppShell } from "../app-shell.js";


test("support document preview and registered editor selection stay inert", async () => {
  const state = createInitialState();
  state.componentImport = { preview: { component_id: "harnesskit.skill.fixture", content: "main", manifest: "kind: skill", support_documents: [{ path: "references/guide.md", content: "<script>not executable</script>" }] } };
  let html = renderAppShell(state);
  assert.match(html, /data-import-support-document/);
  assert.match(html, /references\/guide.md/);
  assert.match(html, /&lt;script&gt;not executable/);
  state.componentImport = null;
  state.importedSkillEditor = { content: "main", documents: [{ path: "references/guide.md", content: "guide", managed: true }] };
  html = renderAppShell(state);
  assert.match(html, /data-select-imported-document/);
  const backend = createBackendClient(async (command, payload) => ({ command, payload }));
  const saved = await backend.saveImportedSkill({ checkoutId: "checkout", sotSnapshotId: "snapshot", componentId: "harnesskit.skill.fixture", content: "guide", document: "references/guide.md" });
  assert.equal(saved.payload.request.document, "references/guide.md");
});

test("import confirmation renders inert source and disables the workspace underneath", () => {
  const state = createInitialState();
  state.componentImport = { preview: { component_id: "harnesskit.skill.fixture", kind: "skill", name: "fixture", manifest: "kind: skill", content: "<script>private</script>", generated_artifact_count: 2 } };
  const html = renderAppShell(state);
  assert.match(html, /data-component-import-dialog/);
  assert.match(html, /command-bar[^>]*inert aria-hidden="true"/);
  assert.match(html, /&lt;script&gt;private&lt;\/script&gt;/);
  assert.match(html, /data-confirm-component-import/);
});

test("standalone skill preview gates first management adoption independently", async () => {
  const { createInstallState, reduceInstallState, canApplyInstall } = await import("../install-flow.js");
  const { renderInstallActionSurface } = await import("../install-view.js");
  let state = createInstallState({ subject: { sotSnapshotId: "sot", componentId: "harnesskit.skill.fixture" }, form: { profileId: "component:harnesskit.skill.fixture", targetId: "codex", scope: "user", targetRoot: "import-source" } });
  state = reduceInstallState(state, { type: "preview_ready", preview: { previewId: "review", fingerprint: "a".repeat(64), requiredApprovals: { overwrite: true, managementAdoption: true }, components: ["harnesskit.skill.fixture"], artifacts: [] } });
  state = reduceInstallState(state, { type: "set_approval", confirmed: true, overwrite: true });
  assert.equal(canApplyInstall(state), false);
  assert.match(renderInstallActionSurface({ install: state, component: { component_id: "harnesskit.skill.fixture", kind: "skill", profile_ids: [], targets: [{ target_id: "codex" }] } }), /data-install-approval="adoptManagement"/);
  state = reduceInstallState(state, { type: "set_approval", confirmed: true, overwrite: true, adoptManagement: true });
  assert.equal(canApplyInstall(state), true);
  const backend = createBackendClient(async (command, payload) => ({ command, payload }));
  const result = await backend.applyInstall({ previewId: "review", approvals: { confirmed: true, semanticFingerprint: "a".repeat(64), overwrite: true, adoptManagement: true } });
  assert.equal(result.payload.approvals.adoptManagement, true);
  state = reduceInstallState(state, { type: "preview_ready", preview: { ...state.preview, previewId: "new-review", fingerprint: "b".repeat(64) } });
  state = reduceInstallState(state, { type: "set_approval", confirmed: true, overwrite: true, adoptManagement: state.adoptManagement });
  assert.equal(canApplyInstall(state), false, "new review must discard prior adoption approval");
});

test("external managed diff is inert and requires reviewed replacement or explicit keep", async () => {
  const { createInstallState, reduceInstallState, canApplyInstall } = await import("../install-flow.js");
  const { renderInstallActionSurface } = await import("../install-view.js");
  let state = createInstallState({ subject: { sotSnapshotId: "sot", componentId: "harnesskit.skill.fixture" }, form: { profileId: "component:harnesskit.skill.fixture", targetId: "codex", scope: "user", targetRoot: "import-source" } });
  const preview = { previewId: "review", fingerprint: "c".repeat(64), requiredApprovals: { overwrite: true, managedReplacement: true }, warnings: ["외부 수정\n-<script>external()</script>\n+Canonical"], components: ["harnesskit.skill.fixture"], artifacts: [] };
  state = reduceInstallState(state, { type: "preview_ready", preview });
  state = reduceInstallState(state, { type: "set_approval", confirmed: true, overwrite: true });
  assert.equal(canApplyInstall(state), false, "generic overwrite cannot approve an external edit");
  const html = renderInstallActionSurface({ install: state, component: { component_id: "harnesskit.skill.fixture", kind: "skill", targets: [{ target_id: "codex" }] } });
  assert.match(html, /&lt;script&gt;external\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /data-install-approval="replaceManaged"/);
  assert.match(html, /data-keep-managed-source/);
  assert.match(html, /<pre class="install-review-diff">/, "diff line breaks must remain visible");
  state = reduceInstallState(state, { type: "set_approval", confirmed: true, overwrite: true, replaceManaged: true });
  assert.equal(canApplyInstall(state), true);
  const backend = createBackendClient(async (command, payload) => ({ command, payload }));
  const result = await backend.applyInstall({ previewId: "review", approvals: { confirmed: true, semanticFingerprint: preview.fingerprint, overwrite: true, replaceManaged: true } });
  assert.equal(result.payload.approvals.replaceManaged, true);
  assert.equal(result.payload.approvals.allowRuntimeHooks, false);
  const kept = reduceInstallState(state, { type: "keep_managed_source" });
  assert.equal(kept.preview, null);
  assert.equal(canApplyInstall(kept), false);
  state = reduceInstallState(state, { type: "preview_ready", preview: { ...preview, previewId: "new", fingerprint: "d".repeat(64) } });
  state = reduceInstallState(state, { type: "set_approval", confirmed: true, overwrite: true, replaceManaged: state.replaceManaged });
  assert.equal(canApplyInstall(state), false, "new content review discards replacement approval");
});

test("shared hook offers the existing import and canonical edit/install controls", async () => {
  const hookData = { ...data, queryResult: { items: [{ ...data.queryResult.items[0], kind: "hook" }] } };
  assert.match(renderLocalInspector(hookData, view), /data-preview-component-import/);
  const { renderInstallActionSurface } = await import("../install-view.js");
  const { createInstallState } = await import("../install-flow.js");
  const html = renderInstallActionSurface({ install: createInstallState(), component: { component_id: "harnesskit.hook.imported-stop", kind: "hook", targets: [{target_id:"claude"}], profile_ids: [] } });
  assert.match(html, /data-edit-imported-skill/);
  assert.match(html, /component:harnesskit.hook.imported-stop/);
});

test("project managed rules expose bounded import and profile-free canonical editor", async () => {
  const { renderInstallActionSurface } = await import("../install-view.js");
  const { createInstallState } = await import("../install-flow.js");
  for (const toolId of ["codex", "claude_code", "antigravity", "antigravity_cli"]) {
    const selected = { ...data, queryResult: { items: [{ ...data.queryResult.items[0], kind: "rule", toolId, scope: "project", safeLocator: toolId === "claude_code" ? "CLAUDE.md" : "AGENTS.md" }] } };
    const html = renderLocalInspector(selected, view);
    assert.match(html, /data-preview-component-import/);
    assert.match(html, /고유한.*관리 블록/);
    if (toolId === "codex") { assert.match(html, /marker 없는 AGENTS.md.*파일 전체/); assert.match(html, /자동.*확대하지/); }
    assert.match(html, /runtime 미검증/);
  }
  const html = renderInstallActionSurface({ install: createInstallState(), component: { component_id: "harnesskit.rule.imported-agents-codex", kind: "rule", targets: [{target_id:"project"}], profile_ids: [] } });
  assert.match(html, /data-edit-imported-skill/);
  assert.match(html, /component:harnesskit.rule.imported-agents-codex/);
  for (const toolId of ["codex", "antigravity_cli"]) {
    for (const scope of ["user", "project"]) {
      const selected = { ...data, queryResult: { items: [{ ...data.queryResult.items[0], kind: "rule", toolId, scope, safeLocator: scope === "user" ? "AGENTS.md" : ".agents/rules/custom.md" }] } };
      assert.doesNotMatch(renderLocalInspector(selected, view), /data-preview-component-import/);
    }
  }
});

test("Claude standalone agent exposes import and prompt editor in both scopes", async () => {
  const { renderInstallActionSurface } = await import("../install-view.js");
  const { createInstallState } = await import("../install-flow.js");
  for (const scope of ["user", "project"]) {
    const selected = { ...data, queryResult: { items: [{ ...data.queryResult.items[0], kind: "agent", toolId: "claude_code", scope }] } };
    assert.match(renderLocalInspector(selected, view), /data-preview-component-import/);
    const html = renderInstallActionSurface({ install: createInstallState(), component: { component_id: "harnesskit.agent.fixture", kind: "agent", targets: [{target_id:"claude"}], profile_ids: [] } });
    assert.match(html, /data-edit-imported-skill/);
    assert.match(html, /component:harnesskit.agent.fixture/);
  }
});

const data = {
  latestComplete: { snapshotId: "local-1", status: "complete" },
  queryResult: { items: [{ instanceId: "skill-1", kind: "skill", displayName: "Release", parseState: "parsed", scope: "user" }] },
};
const view = {
  selectedInstanceId: "skill-1", checkoutId: "checkout-1",
  sourcePreview: { phase: "ready", selection: { instanceId: "skill-1" }, header: { snapshotId: "local-1", instanceId: "skill-1", sourceRevision: "a".repeat(64) } },
};
test("backend confirmation sends explicit approval and the reviewed fingerprint", async () => {
  const calls = [];
  const backend = createBackendClient(async (...args) => { calls.push(args); return {}; });
  await backend.confirmComponentImport({ previewId: "preview-1", fingerprint: "b".repeat(64), confirmed: true });
  assert.deepEqual(calls[0], ["confirm_component_import", { request: { previewId: "preview-1", fingerprint: "b".repeat(64), confirmed: true } }]);
});
test("backend import preview sends handles, never arbitrary source paths", async () => {
  const calls = [];
  const backend = createBackendClient(async (...args) => { calls.push(args); return {}; });
  await backend.previewComponentImport({ checkoutId: "checkout-1", sotSnapshotId: "sot-1", snapshotId: "local-1", instanceId: "skill-1", sourceRevision: "a".repeat(64), sourcePath: "/private/source" });
  assert.deepEqual(calls[0], ["preview_component_import", { request: { checkoutId: "checkout-1", sotSnapshotId: "sot-1", snapshotId: "local-1", instanceId: "skill-1", sourceRevision: "a".repeat(64) } }]);
});
test("selected items expose qualification reasons without declaring candidates runtime supported", () => {
  for (const [kind, toolId, scope, reason] of [
    ["agent", "claude_code", "user", "후보 검증 가능"],
    ["agent", "codex", "project", "후보 검증 가능"],
    ["rule", "codex", "project", "변환기 구현 대기"],
    ["command", "claude_code", "user", "설치 가능한 종류가 아닙니다"],
    ["workflow", "antigravity", "project", "설치 가능한 종류가 아닙니다"],
    ["skill", "hermes", "user", "외부 패키지 정책"],
    ["skill", "antigravity_cli", "user", "발견 경로와 설치 경로"],
    ["hook", "codex", "user", "변환기 구현 대기"],
    ["skill", "claude_code", "user", "후보 검증 가능"],
  ]) {
    const selected = { ...data, queryResult: { items: [{ ...data.queryResult.items[0], kind, toolId, scope }] } };
    const html = renderLocalInspector(selected, view);
    assert.match(html, /data-import-qualification/);
    assert.ok(html.includes(reason), `${toolId}/${kind}/${scope}: ${reason}`);
    assert.match(html, /도구 runtime 미검증/);
    if (["command", "workflow", "rule"].includes(kind) || (kind === "agent" && !["claude_code", "codex"].includes(toolId)) || toolId === "hermes" || toolId === "antigravity_cli") assert.doesNotMatch(html, /data-preview-component-import/);
  }
});

test("skill and non-installable qualification describes the implemented subset without automatic conversion advice", () => {
  const selected = (kind) => ({ ...data, queryResult: { items: [{ ...data.queryResult.items[0], kind, toolId: "claude_code", scope: "project" }] } });
  const skill = renderLocalInspector(selected("skill"), view);
  assert.match(skill, /검증 가능한.*메타데이터/);
  assert.match(skill, /inline.*Markdown/);
  assert.doesNotMatch(skill, /추가 메타데이터와 지원 파일 변환은 구현 대기/);
  for (const kind of ["command", "workflow"]) {
    const html = renderLocalInspector(selected(kind), view);
    assert.match(html, /자동.*변환하지 않습니다/);
    assert.doesNotMatch(html, /workflow_trigger 스킬로 명시적으로 모델링해야/);
    assert.doesNotMatch(html, /data-preview-component-import/);
  }
});

test("backend refusal reasons are translated without pretending portable content is impossible", async () => {
  const { componentImportReason } = await import("../component-import-support.js");
  assert.match(componentImportReason("import_frontmatter_unsupported"), /알 수 없는.*필드/);
  assert.match(componentImportReason("import_skill_option_invalid"), /필드.*형식/);
  assert.match(componentImportReason("import_skill_dependency_unresolved"), /의존성.*검증/);
  assert.match(componentImportReason("import_skill_metadata_edit_unsupported"), /본문.*메타데이터/);
  assert.match(componentImportReason("import_support_files_unsupported"), /지원 파일.*범위/);
  assert.match(componentImportReason("import_support_reference_unsupported"), /inline.*Markdown/);
  assert.match(componentImportReason("import_sensitive_or_dependency_content"), /비밀정보.*의존성/);
  assert.match(componentImportReason("import_skill_install_path_conflict"), /발견 경로와 설치 경로/);
  assert.match(componentImportReason("import_hook_item_ambiguous"), /모호/);
  assert.equal(componentImportReason("source_stale"), "source_stale");
});

test("selected Local skill offers import only with a checkout and verified source", () => {
  assert.match(renderLocalInspector(data, view), /data-preview-component-import/);
  assert.doesNotMatch(renderLocalInspector(data, { ...view, checkoutId: null }), /data-preview-component-import/);
});
test("Antigravity agent name/path mismatch explains fail-closed refusal", async () => {
  const { componentImportReason } = await import("../component-import-support.js");
  assert.match(componentImportReason("import_agent_name_path_mismatch"), /이름.*파일명/);
});

test("Codex agent TOML and selected registration offer profile-free editor in both scopes", async () => {
  const { renderInstallActionSurface } = await import("../install-view.js");
  const { createInstallState } = await import("../install-flow.js");
  for (const scope of ["user", "project"]) {
    const selected = { ...data, queryResult: { items: [{ ...data.queryResult.items[0], kind: "agent", toolId: "codex", scope }] } };
    const html = renderLocalInspector(selected, view);
    assert.match(html, /data-preview-component-import/);
    assert.match(html, /config.toml/);
    assert.match(html, /runtime 미검증/);
    const install = renderInstallActionSurface({ install: createInstallState(), component: { component_id: "harnesskit.agent.fixture", kind: "agent", targets: [{target_id:"codex"}], profile_ids: [] } });
    assert.match(install, /data-edit-imported-skill/);
    assert.match(install, /component:harnesskit.agent.fixture/);
  }
});

test("Antigravity IDE project agent alone exposes qualified import", () => {
  for (const [toolId, scope, candidate] of [["antigravity", "project", true], ["antigravity", "user", false], ["antigravity_cli", "project", false], ["antigravity_cli", "user", false]]) {
    const selected = { ...data, queryResult: { items: [{ ...data.queryResult.items[0], kind: "agent", toolId, scope }] } };
    const html = renderLocalInspector(selected, view);
    assert.equal(html.includes("data-preview-component-import"), candidate, `${toolId}/${scope}`);
    assert.match(html, /runtime 미검증/);
  }
});
