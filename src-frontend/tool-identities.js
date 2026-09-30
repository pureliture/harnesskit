import { TOOL_IDENTITY_CATALOG } from "./tool-identities.catalog.js";

const ALLOWED_CONTEXTS = new Set([
  "install_preview",
  "install_result",
  "install_selected_target",
  "local_result_badge",
  "local_selected_inspector",
  "local_tool_filter",
  "sot_target",
]);

const CATALOG = Object.freeze(
  TOOL_IDENTITY_CATALOG.map((entry) =>
    Object.freeze({ ...entry, aliases: Object.freeze([...entry.aliases]) }),
  ),
);

const BY_ALIAS = new Map(
  CATALOG.flatMap((entry) => entry.aliases.map((alias) => [alias, entry])),
);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function visibleLabel(value, fallback) {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

export function qualifiedToolIdentityCatalog() {
  return CATALOG.map((entry) => ({
    canonicalToolId: entry.canonicalToolId,
    aliases: [...entry.aliases],
    displayName: entry.displayName,
    mode: entry.mode,
    reason: entry.reason,
    src: entry.src,
  }));
}

export function resolveToolIdentity(toolId, label = null) {
  const alias = String(toolId ?? "").trim().toLowerCase();
  const entry = BY_ALIAS.get(alias);
  if (!entry) {
    return Object.freeze({
      canonicalToolId: alias || null,
      displayName: visibleLabel(label, alias || "Unknown tool"),
      mode: "text",
      src: null,
    });
  }
  return Object.freeze({
    canonicalToolId: entry.canonicalToolId,
    displayName: visibleLabel(label, entry.displayName),
    mode: entry.mode === "qualified" ? "qualified" : "text",
    reason: entry.reason ?? null,
    src: entry.mode === "qualified" ? entry.src : null,
  });
}

export function renderToolIdentity(toolId, { context, label = null } = {}) {
  const resolved = resolveToolIdentity(toolId, label);
  const visible = escapeHtml(resolved.displayName);
  if (!ALLOWED_CONTEXTS.has(context)) {
    return `<span class="tool-identity-label">${visible}</span>`;
  }
  const identity = escapeHtml(resolved.canonicalToolId ?? String(toolId ?? ""));
  if (resolved.mode !== "qualified") {
    return `<span class="tool-identity tool-identity--${escapeHtml(context)}" data-tool-identity="${identity}" data-tool-identity-context="${escapeHtml(context)}" data-tool-identity-mode="text"><span class="tool-identity-label">${visible}</span></span>`;
  }
  return `<span class="tool-identity tool-identity--${escapeHtml(context)}" data-tool-identity="${identity}" data-tool-identity-context="${escapeHtml(context)}" data-tool-identity-mode="qualified"><span class="tool-identity-well"><img src="${escapeHtml(resolved.src)}" alt="" aria-hidden="true" data-tool-identity-image /></span><span class="tool-identity-label">${visible}</span></span>`;
}

export function applyToolIdentityImageFailure(event) {
  const image = event?.target;
  if (!image?.dataset || !Object.hasOwn(image.dataset, "toolIdentityImage")) return false;
  const identity = image.closest?.("[data-tool-identity-mode]");
  if (!identity?.dataset) return false;
  identity.dataset.toolIdentityMode = "text";
  image.hidden = true;
  return true;
}

export function bindToolIdentityAssetFallback(root) {
  if (!root?.addEventListener) return () => {};
  const onError = (event) => applyToolIdentityImageFailure(event);
  root.addEventListener("error", onError, true);
  return () => root.removeEventListener?.("error", onError, true);
}
