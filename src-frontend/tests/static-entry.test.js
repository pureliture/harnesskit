import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendUrl = new URL("../", import.meta.url);

function palette(css, mode) {
  const body = css.match(new RegExp(`html\\[data-resolved-mode="${mode}"\\]\\s*\\{([^}]*)\\}`, "s"))?.[1] ?? "";
  return new Map([...body.matchAll(/(--[a-z0-9-]+):\s*(#[0-9a-f]{6})/gi)]
    .map(([, token, value]) => [token, value.toLowerCase()]));
}

function relativeLuminance(hex) {
  const channels = [1, 3, 5]
    .map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255)
    .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrastRatio(left, right) {
  const leftLuminance = relativeLuminance(left);
  const rightLuminance = relativeLuminance(right);
  return (Math.max(leftLuminance, rightLuminance) + 0.05)
    / (Math.min(leftLuminance, rightLuminance) + 0.05);
}

function rawPalette(css, mode) {
  const body = css.match(new RegExp(`html\\[data-resolved-mode="${mode}"\\]\\s*\\{([^}]*)\\}`, "s"))?.[1] ?? "";
  return new Map([...body.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/gi)]
    .map(([, token, value]) => [token, value.trim().toLowerCase()]));
}

function cssRule(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "s"))?.[1] ?? "";
}

function cssProperty(rule, property) {
  return rule.match(new RegExp(`(?:^|;)\\s*${property}:\\s*([^;]+);`, "i"))?.[1]?.trim() ?? null;
}

function parseColor(value, tokens) {
  const variable = value?.match(/^var\((--[a-z0-9-]+)\)$/i)?.[1];
  const resolved = variable ? tokens.get(variable) : value;
  const hex = resolved?.match(/^#([0-9a-f]{6})$/i)?.[1];
  if (hex) {
    return {
      channels: [0, 2, 4].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16)),
      alpha: 1,
    };
  }
  const rgb = resolved?.match(/^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*\/\s*([0-9.]+)\s*\)$/i);
  assert.ok(rgb, `unsupported CSS color: ${resolved ?? value}`);
  return {
    channels: rgb.slice(1, 4).map(Number),
    alpha: Number(rgb[4]),
  };
}

function compositeHex(foreground, background) {
  const channels = foreground.channels.map((value, index) => Math.round(
    value * foreground.alpha + background.channels[index] * (1 - foreground.alpha),
  ));
  return `#${channels.map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function resolvedHex(value, tokens, backgroundHex) {
  const foreground = parseColor(value, tokens);
  if (foreground.alpha === 1) return compositeHex(foreground, foreground);
  return compositeHex(foreground, parseColor(backgroundHex, tokens));
}

test("browser entry imports without DOM or a Tauri runtime", async () => {
  await assert.doesNotReject(import("../app.js"));
});

test("app lifecycle does not use accessibility text as verifier telemetry", async () => {
  const app = await readFile(new URL("app.js", frontendUrl), "utf8");

  assert.doesNotMatch(app, /bindSourcePreviewRuntimeTelemetry|sourcePreviewTelemetryBinding/);
  await assert.rejects(
    readFile(new URL("source-preview-runtime-telemetry.js", frontendUrl), "utf8"),
    (error) => error?.code === "ENOENT",
  );
});

test("production stylesheet has balanced block delimiters", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  const openingBraces = css.match(/\{/g) ?? [];
  const closingBraces = css.match(/\}/g) ?? [];

  assert.equal(closingBraces.length, openingBraces.length);
});

test("production selection routing has no removed SVG graph selector path", async () => {
  const [app, continuity] = await Promise.all([
    readFile(new URL("app.js", frontendUrl), "utf8"),
    readFile(new URL("sot-render-continuity.js", frontendUrl), "utf8"),
  ]);

  assert.doesNotMatch(app, /data-graph-node|data-graph-edge|graph_projection\?\.edges/);
  assert.doesNotMatch(continuity, /data-graph-node|data-graph-edge/);
});

test("static index connects the app root, stylesheet, and appearance bootstrap entry", async () => {
  const html = await readFile(new URL("index.html", frontendUrl), "utf8");
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  assert.match(html, /<title>HarnessKit<\/title>/);
  assert.match(html, /HarnessKit을 준비하고 있습니다/);
  assert.doesNotMatch(html, /Harness Desktop/);
  assert.match(html, /<div id="app"/);
  assert.match(html, /href="\.\/styles\.css"/);
  assert.match(html, /type="module" src="\.\/assets\/app\.bundle\.js"/);
  assert.doesNotMatch(html, /content="dark"/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /\.metric-card\s*\{[^}]*animation:\s*metric-enter/s);
  assert.match(css, /@keyframes metric-enter/);
  assert.match(css, /animation-duration:\s*0\.01ms\s*!important/);
});

test("static shell exposes one semantic workbench and the reference-led desktop grid", async () => {
  const html = await readFile(new URL("index.html", frontendUrl), "utf8");
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  assert.match(html, /<div id="app"/);
  assert.match(css, /--accent:\s*#7c5cff/);
  assert.match(css, /\.desktop-shell\s*\{[^}]*display:\s*grid/s);
  assert.match(css, /\.desktop-shell\s*\{[^}]*grid-template-columns:/s);
  assert.doesNotMatch(css, /linear-gradient\(90deg/);
});

test("strict CSP frontend has attribute-owned palettes and no inline style producers", async () => {
  const sources = await Promise.all([
    "index.html",
    "app.js",
    "app-shell.js",
    "sot-view.js",
    "install-view.js",
    "appearance/bootstrap.js",
    "appearance/controller.js",
    "layout/controller.js",
    "layout/state.js",
  ].map((path) => readFile(new URL(path, frontendUrl), "utf8")));
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  assert.match(css, /html\[data-resolved-mode="light"\]\s*\{[^}]*color-scheme:\s*light/s);
  assert.match(css, /html\[data-resolved-mode="dark"\]\s*\{[^}]*color-scheme:\s*dark/s);
  assert.doesNotMatch(css, /:root\s*\{[^}]*color-scheme:\s*dark/s);
  assert.doesNotMatch(sources.join("\n"), /\sstyle\s*=/i);
});

test("desktop tokens retain AA text and required control-boundary contrast", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  for (const mode of ["light", "dark"]) {
    const tokens = palette(css, mode);
    const token = (name) => {
      const value = tokens.get(name);
      assert.ok(value, `${mode} ${name} must be a solid hex token`);
      return value;
    };
    for (const foreground of ["--text", "--muted", "--quiet", "--success", "--warning", "--danger"]) {
      assert.ok(
        contrastRatio(token(foreground), token("--surface-0")) >= 4.5,
        `${mode} ${foreground} text contrast must be at least 4.5:1`,
      );
    }
    assert.ok(contrastRatio(token("--on-accent"), token("--accent-action")) >= 4.5);
    for (const [foreground, background] of [
      ["--line-strong", "--surface-0"],
      ["--accent-strong", "--surface-0"],
      ["--accent-border", "--surface-0"],
      ["--success-border", "--surface-0"],
      ["--warning-border", "--surface-0"],
      ["--danger-border", "--surface-0"],
      ["--graph-edge", "--surface-0"],
      ["--graph-node-stroke", "--graph-node"],
      ["--graph-kind-skill-stroke", "--graph-kind-skill-fill"],
      ["--graph-kind-agent-stroke", "--graph-kind-agent-fill"],
      ["--graph-kind-hook-stroke", "--graph-kind-hook-fill"],
      ["--graph-kind-rule-stroke", "--graph-kind-rule-fill"],
      ["--graph-kind-command-stroke", "--graph-kind-command-fill"],
      ["--graph-kind-composite-stroke", "--graph-kind-composite-fill"],
    ]) {
      assert.ok(
        contrastRatio(token(foreground), token(background)) >= 3,
        `${mode} ${foreground}/${background} non-text contrast must be at least 3:1`,
      );
    }
  }
  assert.match(css, /\.button--primary\s*\{[^}]*background:\s*var\(--accent-action\)/s);
});

test("Light quiet text token retains AA contrast on every surface where compact metadata appears", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  const tokens = palette(css, "light");
  const quiet = tokens.get("--quiet");
  assert.ok(quiet);

  for (const backgroundToken of ["--surface-0", "--surface-1", "--surface-2", "--surface-hover"]) {
    const background = tokens.get(backgroundToken);
    assert.ok(background, `light ${backgroundToken} must be a solid hex token`);
    assert.ok(
      contrastRatio(quiet, background) >= 4.5,
      `light --quiet/${backgroundToken} text contrast must be at least 4.5:1`,
    );
  }
});

test("Light mode button hover states retain AA text contrast for quiet, secondary, and primary actions", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  const tokens = rawPalette(css, "light");
  const generalHover = cssRule(css, ".button:not(:disabled):hover");
  const primaryHover = cssRule(css, ".button--primary:not(:disabled):hover");
  const generalColor = cssProperty(generalHover, "color");
  const generalBackground = cssProperty(generalHover, "background");
  const primaryColor = cssProperty(primaryHover, "color") ?? generalColor;
  const primaryBackground = cssProperty(primaryHover, "background") ?? generalBackground;

  assert.ok(generalColor && generalBackground && primaryColor && primaryBackground);
  for (const surfaceToken of ["--surface-0", "--surface-1"]) {
    const surface = tokens.get(surfaceToken);
    assert.ok(surface, `light ${surfaceToken} must exist`);
    const quietBackground = resolvedHex(generalBackground, tokens, surface);
    const quietColor = resolvedHex(generalColor, tokens, quietBackground);
    assert.ok(
      contrastRatio(quietColor, quietBackground) >= 4.5,
      `light quiet/secondary hover on ${surfaceToken} must retain 4.5:1 text contrast`,
    );
    const primaryResolvedBackground = resolvedHex(primaryBackground, tokens, surface);
    const primaryResolvedColor = resolvedHex(primaryColor, tokens, primaryResolvedBackground);
    assert.ok(
      contrastRatio(primaryResolvedColor, primaryResolvedBackground) >= 4.5,
      `light primary hover on ${surfaceToken} must retain 4.5:1 text contrast`,
    );
  }
});

test("Local atlas reuses semantic tokens for rectangular filters and compact horizontal panels", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  assert.match(css, /\.local-tool-button\s*\{[^}]*border:\s*1px solid var\(--line-strong\)/s);
  assert.match(css, /\.local-search-row\s+input\s*\{[^}]*width:\s*100%/s);
  assert.match(css, /\.local-result-panel\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:/s);
  assert.match(css, /\.local-result-panel--selected\s*\{[^}]*border-color:\s*var\(--accent-strong\)/s);
  assert.match(css, /\.local-read-actions\s*\{[^}]*display:\s*grid/s);
});

test("Local project rows keep names and paths inside narrow rows with ellipsis", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  assert.match(css, /\.local-explorer-row\s*\{[^}]*flex:\s*0\s+0\s+auto/s);
  assert.match(css, /\.local-project-copy strong,\s*\.local-project-copy small\s*\{[^}]*min-width:\s*0/s);
  assert.match(css, /\.local-project-copy small\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(css, /\.local-project-copy small\s*\{[^}]*text-overflow:\s*ellipsis/s);
  assert.match(css, /\.local-project-copy small\s*\{[^}]*white-space:\s*nowrap/s);
  assert.doesNotMatch(css, /\.local-project-copy small\s*\{[^}]*overflow-wrap:\s*anywhere/s);
});

test("selected Local inspector uses one top inset instead of stacked blank spacing", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  assert.match(
    css,
    /\.local-instance-detail--source-first\s*\{[^}]*padding-top:\s*0/s,
  );
  assert.match(
    css,
    /\.local-instance-detail--source-first\s*>\s*\.detail-kicker\s*\{[^}]*margin:\s*0/s,
  );
});

test("workspace dividers keep col-resize and a non-color active drag cue", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  assert.match(css, /\.workspace-divider\s*\{[^}]*cursor:\s*col-resize/s);
  assert.match(
    css,
    /\.workspace-divider--dragging::before\s*\{[^}]*border-inline-start-width:\s*3px[^}]*background:\s*var\(--accent-soft\)/s,
  );
  assert.match(
    css,
    /\.workspace-divider--dragging\s*\{[^}]*box-shadow:\s*inset 0 0 0 2px var\(--accent-border\)/s,
  );
  assert.match(css, /body\.is-resizing[\s\S]*cursor:\s*col-resize\s*!important/s);
});

test("Component Map legend uses neutral material with shape and line-style cues", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  const profile = cssRule(css, ".graph-key-swatch--profile");
  const workflow = cssRule(css, ".graph-key-swatch--workflow");
  const workflowLine = cssRule(css, ".graph-key-line--workflow");
  const kind = [...css.matchAll(/\.graph-key-kind\s*\{([^}]*)\}/g)]
    .map((match) => match[1])
    .find((rule) => /display:\s*grid/.test(rule)) ?? "";

  assert.match(profile, /border:\s*2px solid var\(--line-strong\)/);
  assert.match(profile, /background:\s*var\(--surface-1\)/);
  assert.match(workflow, /border:\s*3px double var\(--line-strong\)/);
  assert.match(workflow, /border-radius:\s*50%/);
  assert.match(workflow, /background:\s*var\(--surface-1\)/);
  assert.match(workflowLine, /border-top-color:\s*var\(--line-strong\)/);
  assert.match(workflowLine, /border-top-style:\s*dashed/);
  assert.match(kind, /border:\s*1px solid var\(--line-strong\)/);
  assert.match(kind, /background:\s*var\(--text\)/);
  assert.doesNotMatch(`${profile}${workflow}${workflowLine}${kind}`, /graph-kind-(?:profile|workflow|skill)/);
});

test("production frontend stays isolated from the reference mock runtime", async () => {
  const production = (await Promise.all([
    "index.html",
    "app.js",
    "app-shell.js",
    "backend.js",
    "install-flow.js",
    "install-view.js",
    "layout/controller.js",
    "layout/state.js",
    "local-view.js",
    "sot-view.js",
  ].map((path) => readFile(new URL(path, frontendUrl), "utf8")))).join("\n");

  assert.doesNotMatch(production, /https?:\/\//);
  assert.doesNotMatch(production, /text\/babel|ReactDOM|\bBabel\b/);
  assert.doesNotMatch(production, /mock data|fake success|manual CLI/i);
});

test("segmented shell controller has no bindings or renderer for removed legacy controls", async () => {
  const app = await readFile(new URL("app.js", frontendUrl), "utf8");
  const shell = await readFile(new URL("app-shell.js", frontendUrl), "utf8");

  assert.doesNotMatch(app, /data-workspace-panel|#install-form|#scan-button/);
  assert.doesNotMatch(shell, /renderLegacyAppShell/);
});

test("workbench disclosure reattaches one persistent 3D graph session from canonical state", async () => {
  const app = await readFile(new URL("app.js", frontendUrl), "utf8");

  assert.match(app, /import \{ createGraphWorkbenchAdapter \} from "\.\/graph\/graph-workbench-adapter\.js"/);
  assert.match(
    app,
    /import \{\s*bindSotWorkbenchDisclosure,\s*SotWorkbenchDisclosureContractError,\s*\} from "\.\/sot-workbench-disclosure\.js"/s,
  );
  assert.match(
    app,
    /workbenchDisclosure\.release\(\);\s*workflowInspectorBinding\.release\(\);\s*typographyMenuBinding\.release\(\);\s*graphSession\?\.detach\(\);\s*root\.innerHTML = renderAppShell\(state\)/s,
  );
  assert.match(app, /const createGraphSession = options\.createComponentMapSession \?\? createGraphWorkbenchAdapter/);
  assert.match(app, /graphSession = createGraphSession\(\{/);
  assert.match(app, /graphSession\.attach\(disclosureRoot, state\.sotView\)/);
  assert.match(app, /graphSession\.syncPresentation\(state\.sotView\)/);
  assert.match(app, /workbenchDisclosure = bindSotWorkbenchDisclosure\(disclosureRoot,\s*\{/);
  assert.match(app, /workbenchDisclosure\.sync\(state\.ui\.sotDisclosures\)/);
  assert.doesNotMatch(app, /bindSotGraphInteraction|set_graph_transform|patchSotGraphExpansion|sotGraphExpanded/);
});

test("production graph adapter uses the latest Graph Workbench root/browser public factories", async () => {
  const adapter = await readFile(new URL("graph/graph-workbench-adapter.js", frontendUrl), "utf8");

  assert.match(adapter, /import \{ validateGraphInput \} from "@pureliture\/graph-workbench"/);
  assert.match(adapter, /import\("@pureliture\/graph-workbench"\)/);
  assert.match(adapter, /import\("@pureliture\/graph-workbench\/browser"\)/);
  assert.match(adapter, /createGraphWorkbench/);
  assert.match(adapter, /createThreeForceGraphRenderer/);
  assert.doesNotMatch(adapter, /labelVisibility:|selectionLayout:|recoveryKey:/);
  assert.doesNotMatch(adapter, /captureRecoveryCapsule|restoreRecoveryCapsule/);
  assert.doesNotMatch(adapter, /^import[\s\S]*?["']\.\/component-map(?:-session)?\.js["'];?$/m);
  assert.doesNotMatch(adapter, /^import[\s\S]*?["'](?:three|3d-force-graph)["'];?$/m);
});
