import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  applyToolIdentityImageFailure,
  qualifiedToolIdentityCatalog,
  renderToolIdentity,
  resolveToolIdentity,
} from "../tool-identities.js";

const manifestUrl = new URL("../../assets/tool-identities/manifest.json", import.meta.url);
const resolverUrl = new URL("../tool-identities.js", import.meta.url);
const catalogModuleUrl = new URL("../tool-identities.catalog.js", import.meta.url);

test("runtime resolver consumes the manifest-generated catalog module instead of a manual catalog", async () => {
  const [resolverSource, catalogSource] = await Promise.all([
    readFile(resolverUrl, "utf8"),
    readFile(catalogModuleUrl, "utf8"),
  ]);

  assert.match(resolverSource, /from\s+["']\.\/tool-identities\.catalog\.js["']/);
  assert.doesNotMatch(resolverSource, /const\s+CATALOG\s*=\s*Object\.freeze\(\[/);
  assert.match(catalogSource, /generated from assets\/tool-identities\/manifest\.json/i);
  assert.doesNotMatch(catalogSource, /\/Users\/|https?:\/\/|data:image|official|endorsement/i);
});

test("static resolver preserves aliases while unverified vendor images remain text-only", async () => {
  const manifest = JSON.parse(await readFile(manifestUrl, "utf8"));
  const catalog = qualifiedToolIdentityCatalog();

  assert.equal(manifest.schema_version, 1);
  assert.equal(catalog.length, 4);
  assert.ok(manifest.assets.every(({ copyright_status, license_status, trademark_review_status }) => (
    copyright_status === "not_asserted"
    && license_status === "not_asserted"
    && trademark_review_status === "not_asserted"
  )));
  assert.deepEqual(
    catalog.map(({ canonicalToolId, aliases, displayName, mode, src }) => ({
      canonical_tool_id: canonicalToolId,
      aliases,
      display_name: displayName,
      mode,
      bundled_relative_url: src,
    })),
    manifest.assets.map((asset) => ({
      canonical_tool_id: asset.canonical_tool_id,
      aliases: asset.aliases,
      display_name: asset.display_name,
      mode: "text",
      bundled_relative_url: null,
    })),
  );

  assert.equal(resolveToolIdentity("codex").canonicalToolId, "codex");
  assert.equal(resolveToolIdentity("claude-code").canonicalToolId, "claude_code");
  assert.equal(resolveToolIdentity("antigravity_cli").canonicalToolId, "antigravity");
  assert.equal(resolveToolIdentity("hermesagent").canonicalToolId, "hermes");
});

test("unknown and unapproved aliases remain text-only without an inferred generic icon", () => {
  for (const toolId of ["project", "gemini", "future-ai", ""]) {
    const resolved = resolveToolIdentity(toolId, "Visible tool");
    assert.equal(resolved.mode, "text");
    assert.equal(resolved.displayName, "Visible tool");
    assert.equal(resolved.src, null);
  }

  const markup = renderToolIdentity("gemini", {
    context: "local_result_badge",
    label: "Gemini",
  });
  assert.match(markup, /data-tool-identity-mode="text"/);
  assert.match(markup, />Gemini</);
  assert.doesNotMatch(markup, /<img|data:image|https?:\/\//);
});

test("known identity renders a visible label without unverified vendor artwork", () => {
  const markup = renderToolIdentity("claude_code", {
    context: "local_tool_filter",
    label: "Claude Code",
  });

  assert.match(markup, /data-tool-identity="claude_code"/);
  assert.match(markup, /data-tool-identity-context="local_tool_filter"/);
  assert.match(markup, /data-tool-identity-mode="text"/);
  assert.doesNotMatch(markup, /<img|\.\/assets\/tool-identities\/claude\.png/);
  assert.match(markup, /class="tool-identity-label">Claude Code</);
  assert.doesNotMatch(markup, /\/Users\/|https?:\/\/|data:image|official|endorsement/i);
});

test("image load failure converts only that identity to text-only", () => {
  const container = { dataset: { toolIdentityMode: "qualified" } };
  const image = {
    hidden: false,
    dataset: { toolIdentityImage: "" },
    closest(selector) {
      return selector === "[data-tool-identity-mode]" ? container : null;
    },
  };

  assert.equal(applyToolIdentityImageFailure({ target: image }), true);
  assert.equal(container.dataset.toolIdentityMode, "text");
  assert.equal(image.hidden, true);
  assert.equal(applyToolIdentityImageFailure({ target: {} }), false);
});

test("tool artwork uses compact aspect containment and a neutral bounded well", async () => {
  const css = await readFile(new URL("../styles.css", import.meta.url), "utf8");
  const imageRule = css.match(/\.tool-identity-well img\s*\{([^}]*)\}/s)?.[1] ?? "";
  const wellRule = css.match(/\.tool-identity-well\s*\{([^}]*)\}/s)?.[1] ?? "";

  assert.match(imageRule, /object-fit:\s*contain/);
  assert.match(imageRule, /width:\s*100%/);
  assert.match(imageRule, /height:\s*100%/);
  assert.doesNotMatch(imageRule, /filter:|transform:|object-fit:\s*cover/);
  assert.match(wellRule, /inline-size:/);
  assert.match(wellRule, /block-size:/);
  assert.match(wellRule, /background:\s*var\(--surface-0\)/);
  assert.match(css, /\[data-tool-identity-mode="text"\] \.tool-identity-well\s*\{[^}]*display:\s*none/s);
});
