import {
  canApplyInstall,
  classifyExecution,
  createInstallRequest,
  normalizeApplyInstallResponse,
  normalizeInstallPreviewResponse,
} from "./install-flow.js";
import { renderToolIdentity } from "./tool-identities.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function optionalText(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

function componentTargets(component) {
  const values = Array.isArray(component?.targets) ? component.targets : [];
  return values.map((target) => ({
    targetId: optionalText(target?.targetId ?? target?.target_id ?? target?.id),
    supportStatus: optionalText(target?.supportStatus ?? target?.support_status),
  })).filter(({ targetId }) => targetId);
}

function componentProfiles(component) {
  const values = Array.isArray(component?.profileIds ?? component?.profile_ids)
    ? component.profileIds ?? component.profile_ids
    : [];
  return values.map(optionalText).filter(Boolean);
}

function renderValueList(values, empty) {
  if (!Array.isArray(values) || values.length === 0) {
    return `<p class="plan-empty">${escapeHtml(empty)}</p>`;
  }
  return `<ul>${values.map((value) => `<li>${escapeHtml(value)}</li>`).join("")}</ul>`;
}

function renderArtifacts(artifacts) {
  if (!artifacts.length) return '<p class="plan-empty">Planned artifact가 없습니다.</p>';
  return `<ol>${artifacts.map((artifact) => `<li>
    <span>${escapeHtml(artifact.targetId ?? "target")} · ${escapeHtml(artifact.componentId ?? "component")}</span>
    <code>${escapeHtml(artifact.destination)}</code>
  </li>`).join("")}</ol>`;
}

function renderSkippedWrites(skippedWrites) {
  if (!skippedWrites.length) return '<p class="plan-empty">Skipped write가 없습니다.</p>';
  return `<ul>${skippedWrites.map((entry) => `<li><code>${escapeHtml(entry.destination)}</code>${entry.reasonCode ? ` · ${escapeHtml(entry.reasonCode)}` : ""}</li>`).join("")}</ul>`;
}

function renderRuntimeGates(gates) {
  if (!gates.length) return '<p class="plan-empty">Runtime gate가 없습니다.</p>';
  return `<ul>${gates.map((gate) => `<li><strong>${escapeHtml(gate.gateId)}</strong>${gate.target ? ` · ${escapeHtml(gate.target)}` : ""}${gate.safeReason ? `<br /><span>${escapeHtml(gate.safeReason)}</span>` : ""}</li>`).join("")}</ul>`;
}

function approvalLabels(approvals) {
  return [
    approvals.overwrite ? "overwrite" : null,
    approvals.runtimeHooks ? "runtime hooks" : null,
  ].filter(Boolean);
}

function renderNonAtomicBoundary(boundary) {
  if (boundary === true) {
    return "Apply는 원자적이지 않으며 자동 rollback을 제공하지 않습니다. 실패하면 일부 destination 변경이 남을 수 있습니다.";
  }
  if (typeof boundary === "string" && boundary.trim()) return boundary.trim();
  if (boundary && typeof boundary === "object") {
    return optionalText(boundary.safeMessage ?? boundary.safe_message)
      ?? "Non-atomic apply boundary가 선언됐습니다. 자동 rollback은 제공되지 않습니다.";
  }
  return "Backend가 non-atomic boundary evidence를 제공하지 않았습니다. Apply를 성공으로 가정하지 마세요.";
}

function renderTargetIdentities(targetIds, context, empty = "evidence 없음") {
  if (!Array.isArray(targetIds) || targetIds.length === 0) return escapeHtml(empty);
  return `<span class="install-target-identities">${targetIds.map((targetId) => renderToolIdentity(targetId, {
    context,
  })).join('<span class="install-target-separator" aria-hidden="true">, </span>')}</span>`;
}

function renderPreview(install) {
  const request = createInstallRequest(install);
  const preview = normalizeInstallPreviewResponse(install?.preview, request);
  if (!preview) {
    return '<p class="empty-state empty-state--compact">Preview를 만들면 target과 destination을 쓰기 전에 검토할 수 있습니다.</p>';
  }
  const { plan } = preview;
  return `<section class="plan-preview" tabindex="-1" aria-labelledby="install-preview-title">
    <div class="install-preview-heading"><div><p class="eyebrow">Dry-run plan</p><h4 id="install-preview-title">Install preview</h4></div><code title="${escapeHtml(preview.semanticFingerprint)}">${escapeHtml(preview.semanticFingerprint)}</code></div>
    <dl class="plan-summary">
      <div><dt>Preview fingerprint</dt><dd class="path-value">${escapeHtml(preview.semanticFingerprint)}</dd></div>
      <div><dt>Scope</dt><dd>${escapeHtml(plan.scope)}</dd></div>
      <div><dt>Profile</dt><dd class="path-value">${escapeHtml(preview.request.profileId ?? "evidence 없음")}</dd></div>
      <div><dt>Target tool</dt><dd>${renderTargetIdentities(plan.targetIds, "install_preview")}</dd></div>
      <div><dt>Target root</dt><dd class="path-value">${escapeHtml(plan.targetRoot || "evidence 없음")}</dd></div>
    </dl>
    <div class="plan-grid">
      <section><h4>Components</h4>${renderValueList(plan.componentIds, "Component evidence가 없습니다.")}</section>
      <section><h4>Runtime gates</h4>${renderRuntimeGates(plan.runtimeGates)}</section>
      <section><h4>Required approvals</h4>${renderValueList(approvalLabels(plan.requiredApprovals), "추가 approval이 없습니다.")}</section>
      <section><h4>Warnings</h4>${renderValueList(plan.warnings, "Backend warning이 없습니다.")}</section>
    </div>
    <section class="artifact-list" aria-labelledby="install-artifacts-title"><h4 id="install-artifacts-title">Planned artifacts</h4>${renderArtifacts(plan.artifacts)}</section>
    <section class="artifact-list" aria-labelledby="install-skipped-title"><h4 id="install-skipped-title">Skipped writes</h4>${renderSkippedWrites(plan.skippedWrites)}</section>
    <p class="rollback-warning">${escapeHtml(renderNonAtomicBoundary(plan.nonAtomicBoundary))}</p>
  </section>`;
}

function renderExecution(value) {
  const execution = normalizeApplyInstallResponse(value);
  if (!execution) return "";
  const outcome = classifyExecution(execution);
  const title = outcome.kind === "success"
    ? "Install verified"
    : outcome.kind === "partial" ? "Apply / verify partial" : "Install failed";
  const role = outcome.kind === "success" ? "status" : "alert";
  return `<section class="execution-result execution-result--${outcome.kind}" role="${role}" tabindex="-1" aria-labelledby="install-result-title">
    <h4 id="install-result-title">${title}</h4>
    <p>${escapeHtml(outcome.message)}</p>
    <dl>
      <div><dt>Operation</dt><dd>${escapeHtml(execution.status)}</dd></div>
      <div><dt>Evidence</dt><dd>${escapeHtml(execution.installEvidenceId ?? "없음")}</dd></div>
    </dl>
    <div class="destination-results"><h5>Destination outcomes</h5>${execution.destinations.length
      ? `<ul>${execution.destinations.map((result) => `<li><span class="destination-target">${renderToolIdentity(result.targetId ?? "target", { context: "install_result" })}</span><code>${escapeHtml(result.destination)}</code><strong>apply ${escapeHtml(result.applyState)} · verify ${escapeHtml(result.verifyState)}</strong>${result.issueCode ? `<span class="destination-issue">${escapeHtml(result.issueCode)}</span>` : ""}</li>`).join("")}</ul>`
      : '<p class="plan-empty">Destination outcome evidence가 없습니다.</p>'}</div>
    ${outcome.kind === "success" && execution.installEvidenceId ? `<p class="install-evidence">Verified evidence <code>${escapeHtml(execution.installEvidenceId)}</code></p>` : ""}
  </section>`;
}

export function renderInstallActionSurface({ install, component, operationBlocked = false }) {
  if (!component) {
    return `<section class="install-action-surface" data-sot-install tabindex="-1" aria-labelledby="install-action-title">
      <div class="install-action-heading"><p class="eyebrow">Confirmed apply</p><h3 id="install-action-title">Install action</h3></div>
      <p class="empty-state empty-state--compact">Component를 선택하면 target별 preview action이 표시됩니다.</p>
    </section>`;
  }
  const targets = componentTargets(component);
  const profiles = componentProfiles(component);
  const preview = normalizeInstallPreviewResponse(install?.preview, createInstallRequest(install));
  const requiredApprovals = preview?.plan.requiredApprovals ?? {
    overwrite: false,
    runtimeHooks: false,
  };
  const busy = ["previewing", "applying"].includes(install?.phase);
  const previewDisabled = operationBlocked || busy || !install?.subject?.sotSnapshotId
    || !install?.subject?.componentId || !install?.form?.profileId
    || !install?.form?.targetId || !install?.form?.targetRoot;
  const applyDisabled = operationBlocked || !canApplyInstall(install);
  return `<section class="install-action-surface" data-sot-install tabindex="-1" aria-labelledby="install-action-title">
    <div class="install-action-heading">
      <div><p class="eyebrow">Confirmed apply</p><h3 id="install-action-title">Install action</h3></div>
      <button type="button" class="button button--quiet" data-show-local-install="${escapeHtml(component.component_id)}">Local 설치 보기</button>
    </div>
    <p class="install-subject" title="${escapeHtml(component.component_id)}"><strong>${escapeHtml(component.title ?? component.component_id)}</strong><code>${escapeHtml(component.component_id)}</code></p>
    <form id="install-form" class="install-form">
      <div class="install-fields">
        <label for="install-profile">Profile</label>
        <select id="install-profile" name="profileId" data-install-field="profileId"${busy ? " disabled" : ""}>
          <option value="">Profile 선택</option>
          ${profiles.map((profileId) => `<option value="${escapeHtml(profileId)}"${install.form.profileId === profileId ? " selected" : ""}>${escapeHtml(profileId)}</option>`).join("")}
        </select>
        <label for="install-target">Target tool</label>
        <select id="install-target" name="targetId" data-install-field="targetId"${busy ? " disabled" : ""}>
          <option value="">Target 선택</option>
          ${targets.map((target) => `<option value="${escapeHtml(target.targetId)}"${install.form.targetId === target.targetId ? " selected" : ""}>${escapeHtml(target.targetId)}${target.supportStatus ? ` · ${escapeHtml(target.supportStatus)}` : ""}</option>`).join("")}
        </select>
        ${install.form.targetId ? `<div class="install-target-companion" data-install-target-companion>${renderToolIdentity(install.form.targetId, { context: "install_selected_target" })}</div>` : ""}
        <label for="install-scope">Scope</label>
        <select id="install-scope" name="scope" data-install-field="scope"${busy ? " disabled" : ""}>
          <option value="user"${install.form.scope === "user" ? " selected" : ""}>User</option>
          <option value="project"${install.form.scope === "project" ? " selected" : ""}>Project</option>
        </select>
        <label for="install-target-root">Target root</label>
        <input id="install-target-root" name="targetRoot" data-install-field="targetRoot" value="${escapeHtml(install.form.targetRoot)}" autocomplete="off" spellcheck="false" required${busy ? " disabled" : ""} />
      </div>
      <button id="preview-install" type="submit" class="button button--secondary"${previewDisabled ? " disabled" : ""}>${install.phase === "previewing" ? "Preview 생성 중…" : "Install preview"}</button>
    </form>
    ${install.message ? `<p class="install-message${install.phase === "error" ? " install-message--error" : ""}" role="${install.phase === "error" ? "alert" : "status"}" aria-live="polite">${escapeHtml(install.message)}</p>` : ""}
    ${renderPreview(install)}
    ${preview ? `<fieldset class="approval-gates"${busy ? " disabled" : ""}>
      <legend>Preview-bound approvals</legend>
      <label><input id="confirm-install" type="checkbox" data-install-approval="confirmed"${install.confirmed ? " checked" : ""} /> 이 fingerprint의 target, destination, warning을 확인했습니다.</label>
      ${requiredApprovals.overwrite ? `<label><input id="allow-overwrite" type="checkbox" data-install-approval="overwrite"${install.overwrite ? " checked" : ""} /> Preview가 요구한 기존 destination overwrite를 허용합니다.</label>` : ""}
      ${requiredApprovals.runtimeHooks ? `<label><input id="allow-runtime-hooks" type="checkbox" data-install-approval="allowRuntimeHooks"${install.allowRuntimeHooks ? " checked" : ""} /> Preview에 표시된 runtime gate를 허용합니다.</label>` : ""}
    </fieldset>` : ""}
    <button id="apply-install" type="button" class="button button--primary"${applyDisabled ? " disabled" : ""}>${install.phase === "applying" ? "Apply + verify 중…" : "승인한 preview 적용"}</button>
    ${renderExecution(install.execution)}
  </section>`;
}
