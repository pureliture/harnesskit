import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const cssUrl = new URL("../styles.css", import.meta.url);

function declarationsFor(css, selector) {
  let fallback = "";
  for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = match[1].split(",").map((value) => value.trim());
    if (!selectors.includes(selector)) continue;
    fallback = match[2];
    if (/font-size\s*:/.test(match[2])) return match[2];
  }
  return fallback;
}

test("semantic typography tokens keep the dense desktop default readable without scaling rem geometry", async () => {
  const css = await readFile(cssUrl, "utf8");
  const root = declarationsFor(css, ":root");

  assert.match(root, /--text-meta:\s*0\.75rem/);
  assert.match(root, /--text-body:\s*0\.8125rem/);
  assert.match(root, /--text-control:\s*0\.8125rem/);
  assert.match(root, /--text-title:\s*0\.875rem/);
  assert.match(root, /--text-section:\s*1rem/);
  assert.match(root, /--leading-tight:\s*1\.35/);
  assert.match(root, /--leading-body:\s*1\.45/);
  assert.doesNotMatch(root, /(?:^|;)\s*font-size\s*:/);

  assert.match(declarationsFor(css, "body"), /font-size:\s*var\(--text-body\)/);
  assert.match(declarationsFor(css, "body"), /line-height:\s*var\(--leading-body\)/);
  assert.match(declarationsFor(css, ".button"), /font-size:\s*var\(--text-control\)/);
  assert.match(declarationsFor(css, ".eyebrow"), /font-size:\s*var\(--text-meta\)/);
  assert.match(
    declarationsFor(css, ".component-map-heading h2"),
    /font-size:\s*var\(--text-section\)/,
  );
  assert.match(
    declarationsFor(css, ".profile-panel strong"),
    /font-size:\s*var\(--text-title\)/,
  );
  for (const selector of [
    ".detail-name",
    ".sot-unavailable h2",
    ".local-placeholder h2",
  ]) {
    assert.match(
      declarationsFor(css, selector),
      /font-size:\s*var\(--text-section\)/,
      `${selector} must use the section token`,
    );
  }
  for (const selector of [
    ".component-map-key",
    ".graph-relation-label",
    ".tree-tools label",
    ".component-kind-badge",
  ]) {
    assert.match(
      declarationsFor(css, selector),
      /font-size:\s*var\(--text-meta\)/,
      `${selector} must use the metadata token`,
    );
  }
  assert.match(
    declarationsFor(css, ".component-map-identity-overlay strong"),
    /font-size:\s*var\(--text-title\)/,
  );

  for (const selector of [
    ".dashboard-segment",
    "input",
    ".appearance-control span",
    ".workspace-tab",
    ".local-tool-button",
    ".local-filter-badge",
    ".local-technical-info summary",
  ]) {
    assert.match(
      declarationsFor(css, selector),
      /font-size:\s*var\(--text-control\)/,
      `${selector} must use the control token`,
    );
  }
  for (const selector of [
    ".pane-heading h2",
    ".local-workspace-title h2",
    ".local-inspector-block h4",
    ".local-source-content h3",
    ".install-action-heading h3",
  ]) {
    assert.match(
      declarationsFor(css, selector),
      /font-size:\s*var\(--text-section\)/,
      `${selector} must use the section token`,
    );
  }
  for (const selector of [
    ".app-identity h1",
    ".metric-card h3",
    ".local-project-copy strong",
    ".local-result-main strong",
    ".local-ai-card > header h4",
    ".local-source-preview-heading h4",
  ]) {
    assert.match(
      declarationsFor(css, selector),
      /font-size:\s*var\(--text-title\)/,
      `${selector} must use the title token`,
    );
  }
  for (const selector of [
    ".field-help",
    ".notice",
    ".tree-row",
    ".authoring-step p",
    ".authoring-field dd",
    ".workflow-step__body span",
    ".local-result-main > span",
    ".local-ai-explanation p",
    ".local-source-content p",
    ".install-message",
  ]) {
    assert.match(
      declarationsFor(css, selector),
      /font-size:\s*var\(--text-body\)/,
      `${selector} must use the body token`,
    );
  }
});

test("visible CSS does not retain text below the corrected metadata floor", async () => {
  const css = await readFile(cssUrl, "utf8");
  const undersized = [];
  const blockPattern = /([^{}]+)\{([^{}]*)\}/g;
  for (const match of css.matchAll(blockPattern)) {
    const selector = match[1].trim();
    for (const declaration of match[2].matchAll(/font-size:\s*([0-9.]+)rem/g)) {
      const size = Number(declaration[1]);
      if (size < 0.75) undersized.push(`${selector}=${size}rem`);
    }
  }

  assert.deepEqual(undersized, []);
});
