const LOGICAL_MODES = new Set(["System", "Light", "Dark"]);
const RESOLVED_MODES = new Set(["Light", "Dark"]);

export function normalizeAppearanceState(value) {
  if (!value || !LOGICAL_MODES.has(value.logical_mode)) {
    throw new Error("Invalid logical appearance mode.");
  }
  if (!RESOLVED_MODES.has(value.resolved_mode)) {
    throw new Error("Invalid resolved appearance mode.");
  }
  if (!Number.isSafeInteger(value.revision) || value.revision < 0) {
    throw new Error("Invalid appearance revision.");
  }
  if (typeof value.persisted !== "boolean") {
    throw new Error("Invalid appearance persistence state.");
  }

  return Object.freeze({
    logical_mode: value.logical_mode,
    resolved_mode: value.resolved_mode,
    revision: value.revision,
    persisted: value.persisted,
  });
}

export function applyResolvedMode(documentRoot, appearance) {
  const normalized = normalizeAppearanceState({
    logical_mode: appearance.logical_mode ?? "System",
    resolved_mode: appearance.resolved_mode,
    revision: appearance.revision ?? 0,
    persisted: appearance.persisted ?? true,
  });
  documentRoot.dataset.resolvedMode = normalized.resolved_mode.toLowerCase();
  return normalized;
}

export function reduceAppearanceChanged(state, event) {
  const appearance = normalizeAppearanceState(event);
  if (appearance.revision <= state.appearance.revision) return state;
  return {
    ...state,
    appearance: withDiagnostic(
      appearance,
      appearance.persisted ? null : state.appearance.diagnostic,
    ),
  };
}

export function reduceAppearanceResponse(state, response) {
  const appearance = normalizeAppearanceState(response?.appearance);
  const current = state.appearance;
  if (appearance.revision < current.revision) return state;
  if (
    appearance.revision === current.revision
    && (
      appearance.logical_mode !== current.logical_mode
      || appearance.resolved_mode !== current.resolved_mode
      || appearance.persisted !== current.persisted
    )
  ) {
    return state;
  }
  const diagnostic = appearance.persisted
    ? response?.diagnostic ?? null
    : response?.diagnostic ?? current.diagnostic ?? defaultWriteDiagnostic();
  return {
    ...state,
    appearance: withDiagnostic(appearance, diagnostic),
  };
}

export async function requestAppearanceMode({ logicalMode, backend }) {
  if (!LOGICAL_MODES.has(logicalMode)) {
    throw new Error("Invalid logical appearance mode.");
  }
  return backend.setAppearanceMode(logicalMode);
}

export function patchAppearanceControls(root, appearance) {
  root.querySelectorAll('input[name="appearance-mode"]').forEach((control) => {
    control.checked = control.value === appearance.logical_mode;
  });
  const notice = root.querySelector(".appearance-save-state");
  if (!notice) return;
  const diagnostic = appearance.diagnostic
    ?? (appearance.persisted ? null : defaultWriteDiagnostic());
  notice.hidden = !diagnostic;
  notice.textContent = diagnostic?.safe_message ?? "";
}

function withDiagnostic(appearance, diagnostic) {
  return Object.freeze({ ...appearance, diagnostic: diagnostic ?? null });
}

function defaultWriteDiagnostic() {
  return Object.freeze({
    code: "appearance_preference_write_failed",
    safe_message: "설정 저장 실패",
  });
}
