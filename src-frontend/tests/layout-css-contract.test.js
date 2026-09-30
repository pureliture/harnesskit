import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendUrl = new URL("../", import.meta.url);

function declarationsFor(css, selector) {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, selectors]) => selectors
      .split(",")
      .some((candidate) => candidate.trim() === selector))
    .map(([, , declarations]) => declarations)
    .join("\n");
}

function balancedBlockFor(css, marker) {
  const markerIndex = css.indexOf(marker);
  assert.notEqual(markerIndex, -1, `${marker} block must exist`);
  const openIndex = css.indexOf("{", markerIndex);
  assert.notEqual(openIndex, -1, `${marker} block must open`);
  let depth = 0;
  for (let index = openIndex; index < css.length; index += 1) {
    if (css[index] === "{") depth += 1;
    if (css[index] === "}") depth -= 1;
    if (depth === 0) return css.slice(openIndex + 1, index);
  }
  assert.fail(`${marker} block must close`);
}

test("global toolbar stays on one visual row and shrinks nonessential identity copy first", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  assert.match(
    css,
    /\.command-bar\s*\{[^}]*grid-template-areas:\s*"left identity repository meta right"/s,
  );
  assert.doesNotMatch(
    css,
    /@media[^{}]*\{[\s\S]*?\.command-bar\s*\{[^}]*grid-template-areas:\s*[^;}]*\n\s*"/s,
  );
  assert.match(
    css,
    /\.app-identity\s*\{[^}]*min-inline-size:\s*0/s,
  );
  assert.match(
    css,
    /\.app-identity p\s*\{[^}]*overflow:\s*hidden[^}]*text-overflow:\s*ellipsis[^}]*white-space:\s*nowrap/s,
  );
  assert.match(
    css,
    /\.command-repo\s*\{[^}]*grid-template-columns:\s*auto\s+minmax\([^,]+,\s*1fr\)\s+max-content[^}]*min-inline-size:\s*0/s,
  );
});

test("dashboard row keeps refresh next to the guide while only tabs can scroll", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  const navigation = declarationsFor(css, ".dashboard-navigation");
  const segments = declarationsFor(css, ".dashboard-segments");
  const refresh = declarationsFor(css, ".dashboard-refresh");
  const action = declarationsFor(css, ".dashboard-authoring-action");

  assert.match(navigation, /display:\s*flex/);
  assert.match(navigation, /align-items:\s*center/);
  assert.match(segments, /flex:\s*1\s+1\s+auto/);
  assert.match(segments, /min-width:\s*0/);
  assert.match(refresh, /flex:\s*0\s+0\s+auto/);
  assert.match(refresh, /margin-left:\s*auto/);
  assert.match(action, /flex:\s*0\s+0\s+auto/);
  assert.doesNotMatch(action, /margin-left:\s*auto/);
  assert.match(action, /white-space:\s*nowrap/);
  assert.match(
    css,
    /@media \(max-width:\s*54rem\)\s*\{[\s\S]*?\.dashboard-segments\s*\{[^}]*overflow-x:\s*auto/s,
  );
  assert.doesNotMatch(
    css,
    /@media \(max-width:\s*54rem\)\s*\{[\s\S]*?\.dashboard-navigation\s*\{[^}]*overflow-x:/s,
  );
});

test("icon-only repository and dashboard actions keep focusable hit targets", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  const folder = declarationsFor(css, ".command-repo__folder");
  const refresh = declarationsFor(css, ".dashboard-refresh");

  assert.match(folder, /min-width:\s*2\.25rem/);
  assert.match(folder, /min-height:\s*2\.25rem/);
  assert.match(refresh, /min-width:\s*2\.25rem/);
  assert.match(refresh, /min-height:\s*2\.25rem/);
  assert.match(css, /button:focus-visible[\s\S]*?outline:\s*0\.12rem\s+solid\s+var\(--accent-strong\)/);
  assert.match(css, /@media \(max-width:\s*54rem\)[\s\S]*?\.command-repo\s*\{[^}]*grid-template-columns:\s*auto\s+minmax\([^,]+,\s*1fr\)\s+max-content/s);
});

test("authoring flow keeps core steps neutral and reflows progressive details", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  const step = declarationsFor(css, ".authoring-step");
  const ordinal = declarationsFor(css, ".authoring-step-ordinal");
  const openDetails = declarationsFor(css, ".authoring-step-details[open]");
  const fieldValue = declarationsFor(css, ".authoring-field dd");
  const compactFlow = balancedBlockFor(css, "@media (max-width: 44rem)");
  const compactSurface = declarationsFor(compactFlow, ".authoring-flow-surface");
  const compactField = declarationsFor(compactFlow, ".authoring-field");

  assert.match(step, /border-bottom:\s*1px solid var\(--line\)/);
  assert.doesNotMatch(step, /accent|gradient|box-shadow/);
  assert.match(ordinal, /border:\s*1px solid var\(--line-strong\)/);
  assert.match(ordinal, /color:\s*var\(--quiet\)/);
  assert.doesNotMatch(ordinal, /accent|gradient|box-shadow/);
  assert.match(openDetails, /border-color:\s*var\(--accent-border\)/);
  assert.match(fieldValue, /overflow-wrap:\s*anywhere/);
  assert.match(compactSurface, /gap:\s*var\(--space-2\)/);
  assert.match(compactSurface, /padding:\s*var\(--space-3\)/);
  assert.match(compactField, /grid-template-columns:\s*1fr/);
});

test("inventory status can wrap inside the left pane rather than clipping stale text", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  const state = declarationsFor(css, ".inventory-state");
  const dot = declarationsFor(css, ".inventory-state .state-dot");
  const text = declarationsFor(css, ".inventory-state-text");

  assert.match(state, /display:\s*flex/);
  assert.match(state, /min-width:\s*0/);
  assert.match(dot, /flex:\s*0\s+0\s+auto/);
  assert.match(text, /min-width:\s*0/);
  assert.match(text, /overflow-wrap:\s*anywhere/);
  assert.doesNotMatch(text, /text-overflow:\s*ellipsis|white-space:\s*nowrap/);
});

test("Local filters wrap with the removal entry action instead of creating a horizontal scroll lane", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  const badges = declarationsFor(css, ".local-badge-row");
  const issueDetails = declarationsFor(css, ".inventory-issues__details[open] .inventory-issues__detail-list");
  const closedIssueDetails = declarationsFor(css, ".inventory-issues__detail-list");

  assert.match(badges, /display:\s*flex/);
  assert.match(badges, /flex-wrap:\s*wrap/);
  assert.match(badges, /min-width:\s*0/);
  assert.doesNotMatch(badges, /overflow-x:\s*(auto|scroll)/);
  assert.match(issueDetails, /max-block-size:\s*10rem/);
  assert.match(issueDetails, /overflow:\s*auto/);
  assert.doesNotMatch(closedIssueDetails, /overflow:/);
});

test("pane disclosures use stable mirrored split-panel thumbnails in both color modes", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");
  const button = declarationsFor(css, ".pane-disclosure");
  const glyph = declarationsFor(css, ".pane-disclosure--icon > .pane-disclosure__glyph");
  const rail = declarationsFor(css, ".pane-disclosure__glyph::before");
  const leftRail = declarationsFor(
    css,
    '.pane-disclosure[data-pane-disclosure="left"] .pane-disclosure__glyph::before',
  );
  const rightRail = declarationsFor(
    css,
    '.pane-disclosure[data-pane-disclosure="right"] .pane-disclosure__glyph::before',
  );
  const hover = declarationsFor(css, ".pane-disclosure:hover");
  const focus = declarationsFor(css, ".pane-disclosure:focus-visible");

  assert.match(button, /border:\s*0/);
  assert.match(button, /background:\s*transparent/);
  assert.match(glyph, /border:\s*1px\s+solid\s+var\(--pane-disclosure-outline\)/);
  assert.match(glyph, /background:\s*var\(--pane-disclosure-surface\)/);
  assert.match(rail, /background:\s*var\(--pane-disclosure-rail\)/);
  assert.match(leftRail, /inset-inline-start:\s*0/);
  assert.match(rightRail, /inset-inline-end:\s*0/);
  assert.match(hover, /background:\s*var\(--accent-soft\)/);
  assert.match(focus, /outline:\s*2px\s+solid\s+var\(--accent-border\)/);
  assert.match(focus, /outline-offset:\s*2px/);
  assert.match(
    css,
    /html\[data-resolved-mode="light"\]\s*\{[^}]*--pane-disclosure-rail:\s*#7b879a[^}]*--pane-disclosure-surface:\s*#f8fafd[^}]*--pane-disclosure-outline:\s*#7b879a/s,
  );
  assert.match(
    css,
    /html\[data-resolved-mode="dark"\]\s*\{[^}]*--pane-disclosure-rail:\s*#aaa7c2[^}]*--pane-disclosure-surface:\s*#171726[^}]*--pane-disclosure-outline:\s*#aaa7c2/s,
  );
  assert.doesNotMatch(css, /aria-expanded=["'](?:true|false)["'][^}]*pane-disclosure__glyph/);
});

test("stacked workspace keeps all panes in the fixed app row with internal scrolling", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  assert.match(css, /body\s*\{[^}]*height:\s*100vh[^}]*overflow:\s*hidden/s);
  assert.match(css, /#app\s*\{[^}]*height:\s*100dvh/s);
  assert.match(css, /\.desktop-app\s*\{[^}]*height:\s*100dvh[^}]*overflow:\s*hidden/s);
  assert.match(
    css,
    /\.desktop-shell\[data-layout-mode="stacked"\]\s*\{[^}]*grid-template-rows:\s*minmax\(0,\s*[\d.]+fr\)\s+minmax\(0,\s*[\d.]+fr\)\s+minmax\(0,\s*[\d.]+fr\)[^}]*overflow:\s*hidden/s,
  );
  assert.match(
    css,
    /\.desktop-shell\[data-layout-mode="stacked"\] \.pane--inventory,\s*\.desktop-shell\[data-layout-mode="stacked"\] \.pane--workbench,\s*\.desktop-shell\[data-layout-mode="stacked"\] \.pane--inspector\s*\{[^}]*min-height:\s*0[^}]*overflow:\s*auto/s,
  );
  assert.doesNotMatch(
    css,
    /@media \(max-width: 66rem\)\s*\{\s*body\s*\{[^}]*height:\s*auto/s,
  );
  assert.doesNotMatch(
    css,
    /@media \(max-width: 66rem\)[\s\S]*?#app,\s*\.desktop-app\s*\{[^}]*height:\s*auto/s,
  );
});

test("expanded graph uses a full-bleed body with one non-layout floating HUD", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  const componentMap = declarationsFor(css, ".component-map");
  const componentMapBody = declarationsFor(css, ".component-map-body");
  const hud = declarationsFor(css, ".component-map-hud");
  const collapsedHud = declarationsFor(css, ".component-map--collapsed .component-map-hud");
  const collapsedToolbar = declarationsFor(
    css,
    ".component-map--collapsed .component-map-toolbar",
  );

  assert.match(
    css,
    /\.component-map\s*\{[^}]*container-type:\s*inline-size[^}]*container-name:\s*component-map/s,
  );
  assert.match(componentMap, /grid-template-rows:\s*minmax\(0,\s*1fr\)/);
  assert.doesNotMatch(componentMap, /grid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)/);
  assert.match(componentMapBody, /grid-template-rows:\s*minmax\(0,\s*1fr\)\s+auto/);
  assert.doesNotMatch(componentMapBody, /grid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)/);
  assert.match(hud, /position:\s*absolute/);
  assert.match(hud, /inset:\s*[^;]+/);
  assert.match(hud, /z-index:\s*\d+/);
  assert.match(hud, /pointer-events:\s*none/);
  assert.match(collapsedHud, /position:\s*relative/);
  assert.match(collapsedHud, /inset:\s*auto/);
  assert.match(collapsedToolbar, /display:\s*none/);
  assert.match(
    css,
    /\.component-map-hud (?:button|\.graph-zoom-controls)\s*\{[^}]*pointer-events:\s*auto/s,
  );
  assert.match(
    css,
    /\.component-map-toolbar\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto[^}]*align-items:\s*center[^}]*min-width:\s*0/s,
  );
  assert.match(
    css,
    /\.component-map-toolbar \.component-map-key\s*\{[^}]*min-width:\s*0[^}]*white-space:\s*nowrap[^}]*overflow:\s*hidden/s,
  );
  assert.match(
    css,
    /\.component-map-toolbar \.graph-zoom-controls\s*\{[^}]*min-width:\s*max-content[^}]*justify-self:\s*end[^}]*white-space:\s*nowrap/s,
  );
  assert.match(
    css,
    /@container component-map \(max-width:\s*58rem\)\s*\{[\s\S]*?\.graph-key-label--detail\s*\{[^}]*display:\s*none/s,
  );
  assert.match(
    css,
    /@container component-map \(max-width:\s*46rem\)\s*\{[\s\S]*?\.graph-key-label\s*\{[^}]*display:\s*none/s,
  );
});

test("graph identity uses scene labels plus a visually hidden live status", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  const viewport = declarationsFor(css, ".component-map-viewport");
  const overlay = declarationsFor(css, ".component-map-identity-overlay");
  const badge = declarationsFor(css, ".component-kind-badge");

  assert.match(viewport, /position:\s*relative/);
  assert.match(overlay, /position:\s*absolute/);
  assert.match(overlay, /inset:\s*[^;]+/);
  assert.match(overlay, /pointer-events:\s*none/);
  assert.match(overlay, /max-inline-size:\s*[^;]+/);
  assert.match(overlay, /inline-size:\s*1px/);
  assert.match(overlay, /block-size:\s*1px/);
  assert.match(overlay, /clip-path:\s*inset\(50%\)/);
  assert.match(overlay, /white-space:\s*nowrap/);
  assert.match(badge, /display:\s*inline-flex/);
  assert.match(badge, /border:\s*1px\s+solid/);
  assert.match(badge, /border-radius:\s*(?:0|[\d.]+rem|var\(--[^)]+\))/);
  assert.match(
    css,
    /\.component-map-scene-host\s+\.float-tooltip-kap\s*\{[^}]*display:\s*none\s*!important/s,
  );
});

test("protected relation identities wrap without ellipsis and the identity overlay sits above labels", async () => {
  const css = await readFile(new URL("../styles.css", import.meta.url), "utf8");
  const selected = declarationsFor(
    css,
    '.graph-relation-label[data-relation-label-state="selected"]',
  );
  const focused = declarationsFor(
    css,
    '.graph-relation-label[data-relation-label-state="focused"]',
  );
  const identity = declarationsFor(css, ".component-map-identity-overlay");
  const labelRoot = declarationsFor(
    css,
    ".component-map-relation-label-root",
  );

  assert.match(selected, /white-space:\s*normal/);
  assert.match(selected, /text-overflow:\s*clip/);
  assert.match(focused, /white-space:\s*normal/);
  assert.match(focused, /text-overflow:\s*clip/);
  assert.ok(Number(identity.match(/z-index:\s*(\d+)/)?.[1])
    > Number(labelRoot.match(/z-index:\s*(\d+)/)?.[1]));
});

test("graph scene and progress-driven identity cues use the approved neutral appearance pair", async () => {
  const css = await readFile(new URL("../styles.css", import.meta.url), "utf8");
  const scene = declarationsFor(css, ".component-map-viewport--three");
  const identity = declarationsFor(css, ".component-map-identity-overlay");
  const label = declarationsFor(css, ".graph-relation-label");

  assert.match(css, /html\[data-resolved-mode="light"\]\s*\{[^}]*--graph-scene-background:\s*#f6f9fe[^}]*--graph-focus-rim:\s*#0f172a/s);
  assert.match(css, /html\[data-resolved-mode="dark"\]\s*\{[^}]*--graph-scene-background:\s*#19192b[^}]*--graph-focus-rim:\s*#f8fafc/s);
  assert.match(scene, /background:\s*var\(--graph-scene-background\)/);
  assert.doesNotMatch(scene, /radial-gradient/);
  assert.match(identity, /var\(--graph-focus-rim\)\s+var\(--graph-identity-emphasis-percent/);
  assert.match(label, /var\(--graph-focus-rim\)\s+var\(--graph-relation-label-emphasis-percent/);
});

test("selection emphasis CSS displays renderer-owned progress without timing authority", async () => {
  const css = await readFile(new URL("../styles.css", import.meta.url), "utf8");
  const sessionSource = await readFile(
    new URL("../graph/component-map-session.js", import.meta.url),
    "utf8",
  );
  const labelSource = await readFile(
    new URL("../graph/relation-label-overlay.js", import.meta.url),
    "utf8",
  );
  const identity = declarationsFor(css, ".component-map-identity-overlay");
  const hiddenIdentity = declarationsFor(css, ".component-map-identity-overlay[hidden]");
  const label = declarationsFor(css, ".graph-relation-label");

  assert.match(identity, /opacity:\s*var\(--graph-identity-opacity,\s*0\)/);
  assert.match(label, /opacity:\s*var\(--graph-relation-label-opacity,\s*0\)/);
  assert.match(identity, /pointer-events:\s*none/);
  assert.match(hiddenIdentity, /display:\s*none/);
  for (const declarations of [identity, label]) {
    assert.doesNotMatch(declarations, /transition(?:-duration|-delay)?:/);
  }
  assert.doesNotMatch(css, /data-identity-presence="(?:entering|exiting)"[^}]*\{[^}]*opacity:/s);
  assert.doesNotMatch(css, /data-relation-label-presence="(?:entering|exiting)"[^}]*\{[^}]*opacity:/s);
  for (const source of [sessionSource, labelSource]) {
    assert.doesNotMatch(source, /setTimeout|requestAnimationFrame|cancelScheduled|\bschedule\b/);
  }
});

test("SoT workbench owns vertical scrolling and uses bounded-fluid profile cards", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  const workbench = declarationsFor(css, ".pane--workbench");
  const sotWorkbench = declarationsFor(css, ".sot-workbench");
  const firstViewport = declarationsFor(css, ".sot-workbench__first-viewport");
  const collapsedFirstViewport = declarationsFor(
    css,
    '.sot-workbench[data-component-map-expanded="false"] .sot-workbench__first-viewport',
  );
  const componentMapBody = [
    declarationsFor(css, ".component-map-body"),
    declarationsFor(css, ".component-map--three .component-map-body"),
  ].join("\n");
  const graphViewport = [
    declarationsFor(css, ".component-map-viewport"),
    declarationsFor(css, ".component-map-viewport--three"),
  ].join("\n");
  const matrixBody = declarationsFor(css, ".profile-matrix-body");
  const profileGrid = declarationsFor(css, ".profile-grid");
  const profilePanel = declarationsFor(css, ".profile-panel");
  const memberGrid = declarationsFor(css, ".profile-member-grid");
  const memberPanel = declarationsFor(css, ".component-panel");
  const titleRules = [
    declarationsFor(css, ".profile-panel strong"),
    declarationsFor(css, ".component-panel strong"),
  ].join("\n");
  const metadataRules = [
    declarationsFor(css, ".profile-panel span"),
    declarationsFor(css, ".component-panel span"),
  ].join("\n");

  assert.match(workbench, /overflow-y:\s*auto/);
  for (const selector of [
    ".sot-workbench",
    ".sot-workbench__first-viewport",
    ".component-map",
    ".component-map-body",
    ".profile-matrix-body",
    ".profile-grid",
    ".profile-member-grid",
    ".profile-member-region",
  ]) {
    const declarations = declarationsFor(css, selector);
    assert.doesNotMatch(
      declarations,
      /(?:^|;)\s*(?:overflow|overflow-y):\s*(?:auto|scroll)\b/,
      `${selector} must not become a nested vertical scroll owner`,
    );
  }

  assert.match(sotWorkbench, /(?:^|;)\s*block-size:\s*100%/);
  assert.match(sotWorkbench, /min-block-size:\s*100%/);
  assert.match(firstViewport, /display:\s*grid/);
  assert.match(firstViewport, /(?:^|;)\s*block-size:\s*100%/);
  assert.match(firstViewport, /min-block-size:\s*100%/);
  assert.match(firstViewport, /grid-template-rows:\s*minmax\(0,\s*1fr\)\s+auto/);
  assert.match(collapsedFirstViewport, /(?:^|;)\s*block-size:\s*auto/);
  assert.match(collapsedFirstViewport, /min-block-size:\s*auto/);
  assert.match(collapsedFirstViewport, /grid-template-rows:\s*auto\s+auto/);
  assert.match(componentMapBody, /grid-template-rows:\s*minmax\(0,\s*1fr\)\s+auto/);
  assert.doesNotMatch(componentMapBody, /grid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)/);
  assert.doesNotMatch(componentMapBody, /4\.25rem|minmax\(22rem/);
  assert.doesNotMatch(graphViewport, /min-(?:block-size|height):\s*22rem/);
  assert.doesNotMatch(matrixBody, /max-(?:block-size|height)\s*:/);
  assert.doesNotMatch(matrixBody, /(?:block-size|height)\s*:\s*[^;}]*(?:vh|dvh|svh|lvh)/);
  assert.doesNotMatch(matrixBody, /(?:overflow|overflow-y):\s*(?:auto|scroll)\b/);

  assert.match(profileGrid, /display:\s*flex/);
  assert.match(profileGrid, /flex-wrap:\s*wrap/);
  assert.match(profileGrid, /align-items:\s*stretch/);
  assert.match(profilePanel, /flex:\s*1\s+1\s+13\.5rem/);
  assert.match(profilePanel, /min-inline-size:\s*min\(100%,\s*12\.5rem\)/);
  assert.match(profilePanel, /max-inline-size:\s*15rem/);
  assert.match(profilePanel, /min-(?:block-size|height):\s*4\.5rem/);
  assert.match(profilePanel, /justify-content:\s*flex-start/);

  assert.match(memberGrid, /display:\s*flex/);
  assert.match(memberGrid, /flex-wrap:\s*wrap/);
  assert.match(memberPanel, /flex:\s*1\s+1\s+15rem/);
  assert.match(memberPanel, /min-inline-size:\s*min\(100%,\s*13\.5rem\)/);
  assert.match(memberPanel, /max-inline-size:\s*18rem/);
  assert.match(memberPanel, /justify-content:\s*flex-start/);
  assert.match(titleRules, /font-size:\s*var\(--text-title\)/);
  assert.match(metadataRules, /font-size:\s*var\(--text-meta\)/);

  assert.doesNotMatch(profileGrid, /grid-template-columns\s*:/);
  assert.doesNotMatch(memberGrid, /grid-template-columns\s*:/);
  assert.doesNotMatch(css, /@container\s+profile-matrix\s*\(/);
});

test("SoT inventory bounds the wrapper and gives scroll ownership only to the tree body", async () => {
  const css = await readFile(new URL("styles.css", frontendUrl), "utf8");

  assert.match(
    css,
    /\[data-sot-tree\]\s*\{[^}]*display:\s*flex[^}]*flex-direction:\s*column[^}]*flex:\s*1\s+1\s+auto[^}]*min-height:\s*0[^}]*overflow:\s*hidden/s,
  );
  assert.match(
    css,
    /\[data-sot-tree\] \.tree-scroll\s*\{[^}]*flex:\s*1\s+1\s+auto[^}]*min-height:\s*0[^}]*overflow:\s*auto[^}]*overscroll-behavior:\s*contain/s,
  );
});
