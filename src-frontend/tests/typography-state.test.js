import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_TYPOGRAPHY_PRESET,
  normalizeTypographyState,
  typographyPresetTokens,
} from "../typography/state.js";

test("Typography preset state normalizes safe backend values and defaults invalid input", () => {
  assert.equal(DEFAULT_TYPOGRAPHY_PRESET, "Default");
  assert.deepEqual(normalizeTypographyState({
    preset: "Large",
    revision: 4,
    persisted: true,
  }), {
    preset: "Large",
    revision: 4,
    persisted: true,
    diagnostic: null,
  });
  assert.deepEqual(normalizeTypographyState({ preset: "Gigantic" }), {
    preset: "Default",
    revision: 0,
    persisted: false,
    diagnostic: null,
  });
});

test("Typography token table keeps the approved three text-only stages", () => {
  assert.deepEqual(typographyPresetTokens("Small"), {
    meta: "0.6875rem",
    body: "0.75rem",
    control: "0.75rem",
    title: "0.8125rem",
    section: "0.9375rem",
  });
  assert.deepEqual(typographyPresetTokens("Default"), {
    meta: "0.75rem",
    body: "0.8125rem",
    control: "0.8125rem",
    title: "0.875rem",
    section: "1rem",
  });
  assert.deepEqual(typographyPresetTokens("Large"), {
    meta: "0.8125rem",
    body: "0.875rem",
    control: "0.875rem",
    title: "0.9375rem",
    section: "1.0625rem",
  });
});
