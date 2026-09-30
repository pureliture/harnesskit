export const DEFAULT_TYPOGRAPHY_PRESET = "Default";

const PRESET_TOKENS = Object.freeze({
  Small: Object.freeze({
    meta: "0.6875rem",
    body: "0.75rem",
    control: "0.75rem",
    title: "0.8125rem",
    section: "0.9375rem",
  }),
  Default: Object.freeze({
    meta: "0.75rem",
    body: "0.8125rem",
    control: "0.8125rem",
    title: "0.875rem",
    section: "1rem",
  }),
  Large: Object.freeze({
    meta: "0.8125rem",
    body: "0.875rem",
    control: "0.875rem",
    title: "0.9375rem",
    section: "1.0625rem",
  }),
});

export function normalizeTypographyState(value) {
  const preset = Object.hasOwn(PRESET_TOKENS, value?.preset)
    ? value.preset
    : DEFAULT_TYPOGRAPHY_PRESET;
  const revision = Number.isSafeInteger(value?.revision) && value.revision >= 0
    ? value.revision
    : 0;
  const persisted = typeof value?.persisted === "boolean" ? value.persisted : false;
  return {
    preset,
    revision,
    persisted,
    diagnostic: value?.diagnostic ?? null,
  };
}

export function typographyPresetTokens(preset) {
  return PRESET_TOKENS[Object.hasOwn(PRESET_TOKENS, preset) ? preset : DEFAULT_TYPOGRAPHY_PRESET];
}
