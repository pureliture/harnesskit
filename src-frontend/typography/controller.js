import { normalizeTypographyState } from "./state.js";

const PRESET_LABELS = Object.freeze({
  Small: "작게",
  Default: "기본",
  Large: "크게",
});

function presetFromControl(control) {
  const preset = control?.dataset?.typographyPreset;
  return Object.hasOwn(PRESET_LABELS, preset) ? preset : null;
}

function typographyPresetLabel(preset) {
  return PRESET_LABELS[Object.hasOwn(PRESET_LABELS, preset) ? preset : "Default"];
}

function nextMenuControlIndex(key, currentIndex, controlCount) {
  if (key === "ArrowDown") return Math.min(controlCount - 1, currentIndex + 1);
  if (key === "ArrowUp") return Math.max(0, currentIndex - 1);
  if (key === "Home") return 0;
  return controlCount - 1;
}

export function bindTypographyMenu({
  trigger,
  menu,
  options,
  documentTarget = trigger?.ownerDocument,
  onSelect = async () => {},
} = {}) {
  const controls = Array.isArray(options)
    ? options.filter((control) => presetFromControl(control))
    : [];
  if (!trigger?.addEventListener || !menu?.addEventListener || controls.length === 0) {
    return Object.freeze({ sync() {}, release() {} });
  }

  let open = false;
  let selectedPreset = "Default";
  let released = false;
  let selectionPending = false;

  const selectedControl = () => controls.find(
    (control) => presetFromControl(control) === selectedPreset,
  ) ?? controls[0];
  const controlIndex = (control) => Math.max(0, controls.indexOf(control));

  const syncPresentation = () => {
    const label = `글자 크기: ${typographyPresetLabel(selectedPreset)}`;
    trigger.setAttribute("aria-label", label);
    trigger.setAttribute("title", label);
    trigger.setAttribute("aria-expanded", String(open));
    menu.hidden = !open;
    controls.forEach((control) => {
      control.setAttribute("aria-checked", String(presetFromControl(control) === selectedPreset));
    });
  };

  const close = ({ focusTrigger = false } = {}) => {
    if (!open) return;
    open = false;
    syncPresentation();
    if (focusTrigger) trigger.focus?.();
  };

  const openMenu = () => {
    if (open) return;
    open = true;
    syncPresentation();
    selectedControl()?.focus?.();
  };

  const selectPreset = async (preset) => {
    if (!Object.hasOwn(PRESET_LABELS, preset) || selectionPending) return;
    selectionPending = true;
    try {
      await onSelect(preset);
    } finally {
      selectionPending = false;
      close({ focusTrigger: true });
    }
  };

  const onTriggerClick = () => {
    if (open) close();
    else openMenu();
  };
  const onTriggerKeyDown = (event) => {
    if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault?.();
      openMenu();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault?.();
      event.stopPropagation?.();
      close({ focusTrigger: true });
    }
  };
  const onMenuKeyDown = (event) => {
    const currentIndex = controlIndex(event.target);
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault?.();
      const nextIndex = nextMenuControlIndex(event.key, currentIndex, controls.length);
      controls[nextIndex]?.focus?.();
      return;
    }
    if (["Enter", " ", "Spacebar"].includes(event.key)) {
      event.preventDefault?.();
      void selectPreset(presetFromControl(event.target));
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault?.();
      event.stopPropagation?.();
      close({ focusTrigger: true });
      return;
    }
    if (event.key === "Tab") close();
  };
  const onOutsidePointerDown = (event) => {
    if (!open) return;
    const target = event.target;
    const containsTarget = target === trigger
      || target === menu
      || controls.includes(target)
      || trigger.contains?.(target)
      || menu.contains?.(target);
    if (!containsTarget) close({ focusTrigger: true });
  };

  trigger.addEventListener("click", onTriggerClick);
  trigger.addEventListener("keydown", onTriggerKeyDown);
  menu.addEventListener("keydown", onMenuKeyDown);
  const optionHandlers = new Map(controls.map((control) => [
    control,
    () => {
      void selectPreset(presetFromControl(control));
    },
  ]));
  controls.forEach((control) => {
    control.addEventListener("click", optionHandlers.get(control));
  });
  documentTarget?.addEventListener?.("pointerdown", onOutsidePointerDown);
  syncPresentation();

  return Object.freeze({
    sync(typography) {
      selectedPreset = normalizeTypographyState(typography).preset;
      syncPresentation();
    },
    release() {
      if (released) return;
      released = true;
      trigger.removeEventListener?.("click", onTriggerClick);
      trigger.removeEventListener?.("keydown", onTriggerKeyDown);
      menu.removeEventListener?.("keydown", onMenuKeyDown);
      controls.forEach((control) => {
        control.removeEventListener?.("click", optionHandlers.get(control));
      });
      documentTarget?.removeEventListener?.("pointerdown", onOutsidePointerDown);
    },
  });
}

export function applyTypographyPreset(documentRoot, typography) {
  if (!documentRoot?.dataset) return;
  documentRoot.dataset.typographyPreset = normalizeTypographyState(typography).preset;
}

export function reduceTypographyResponse(state, response) {
  return {
    ...state,
    typography: normalizeTypographyState(response?.typography ?? response),
  };
}

export async function requestTypographyPreset({ preset, expectedRevision, backend }) {
  if (typeof backend?.setTypographyPreset !== "function") {
    throw new Error("Typography preference command is unavailable.");
  }
  return backend.setTypographyPreset({ preset, expectedTypographyRevision: expectedRevision });
}
