import { mountApp } from "../app.js";
import { createBackendClient } from "../backend.js";
import { createWorkspaceLayoutController } from "../layout/controller.js";
import { normalizeWorkspaceLayoutState } from "../layout/state.js";
import { applyResolvedMode, normalizeAppearanceState } from "./controller.js";
import { applyTypographyPreset } from "../typography/controller.js";
import { normalizeTypographyState } from "../typography/state.js";

const MAX_REFRESH_ATTEMPTS = 8;

export async function bootstrapAppearance({
  backend,
  documentRoot,
  root,
  mount,
  focusTarget = globalThis.window,
  createLayoutController = createWorkspaceLayoutController,
}) {
  const bootstrap = await backend.getBootstrapState();
  let appearance = normalizeAppearanceState(bootstrap?.appearance);
  let workspaceLayout = {
    ...normalizeWorkspaceLayoutState(bootstrap?.workspace_layout),
    diagnostic: bootstrap?.workspace_layout_diagnostic ?? null,
  };
  let typography = normalizeTypographyState(bootstrap?.typography);
  applyResolvedMode(documentRoot, appearance);
  applyTypographyPreset(documentRoot, typography);

  let app;
  const workspaceLayoutController = createLayoutController({
    root,
    backend,
    initialState: bootstrap?.workspace_layout,
    onStateChange(next) {
      workspaceLayout = next;
      app?.applyWorkspaceLayoutChanged?.(next);
    },
  });
  app = mount(root, backend, {
    appearance,
    workspaceLayout,
    typography,
    workspaceLayoutController,
    deferDomainRestore: true,
    documentRoot,
  });
  workspaceLayoutController.syncAfterRender();

  let unlisten;
  let focusListener = null;
  let focusRegistered = false;
  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    if (focusRegistered) focusTarget?.removeEventListener?.("focus", focusListener);
    if (typeof unlisten === "function") unlisten();
    workspaceLayoutController.release?.();
    app?.destroy?.();
  };

  try {
    if (typeof backend.onAppearanceChanged === "function") {
      unlisten = await backend.onAppearanceChanged((event) => {
        if (disposed) return;
        const next = normalizeAppearanceState(event);
        if (next.revision <= appearance.revision) return;
        appearance = next;
        applyResolvedMode(documentRoot, appearance);
        app?.applyAppearanceChanged?.(event);
      });
    }

    const reconcileFromBackend = async () => {
      const latest = normalizeAppearanceState((await backend.getBootstrapState())?.appearance);
      if (disposed || latest.revision <= appearance.revision) return;
      appearance = latest;
      applyResolvedMode(documentRoot, appearance);
      app?.applyAppearanceChanged?.({ ...appearance, source: "System" });
    };
    focusListener = () => {
      void reconcileFromBackend().catch(() => {});
    };
    focusTarget?.addEventListener?.("focus", focusListener);
    focusRegistered = true;

    for (let attempt = 0; attempt < MAX_REFRESH_ATTEMPTS; attempt += 1) {
      const desktopApp = root.querySelector?.(".desktop-app");
      const desktopBounds = desktopApp?.getBoundingClientRect?.() ?? {};
      const completion = await backend.completeBootstrap(
        appearance.revision,
        workspaceLayout.revision,
        typography.revision,
        {
        childElementCount: root.childElementCount,
        textLength: String(root.textContent ?? "").length,
        desktopAppPresent: Boolean(desktopApp),
        resolvedMode: String(documentRoot.dataset?.resolvedMode ?? "").toLowerCase(),
        viewportWidth: Number(documentRoot.clientWidth ?? root.clientWidth ?? 0),
        viewportHeight: Number(documentRoot.clientHeight ?? root.clientHeight ?? 0),
        desktopWidth: Number(desktopBounds.width ?? 0),
          desktopHeight: Number(desktopBounds.height ?? 0),
          layoutVisible: Boolean(desktopApp && desktopApp.hidden !== true),
          appliedTypographyRevision: typography.revision,
          appliedTypographyPreset: typography.preset,
          ...workspaceLayoutController.getBootstrapProbe(),
        },
      );
      if (completion?.instruction === "Show") {
        app?.startAfterBootstrap?.();
        return Object.freeze({
          appearance,
          workspaceLayout,
          typography,
          app,
          workspaceLayoutController,
          unlisten,
          dispose,
        });
      }
      if (completion?.instruction !== "Refresh") {
        throw new Error("Invalid bootstrap completion instruction.");
      }

      const refreshed = normalizeAppearanceState(completion.appearance);
      if (refreshed.revision < appearance.revision) {
        throw new Error("Bootstrap refresh moved appearance revision backwards.");
      }
      appearance = refreshed;
      applyResolvedMode(documentRoot, appearance);
      app?.applyAppearanceChanged?.({ ...appearance, source: "System" });
      workspaceLayout = normalizeWorkspaceLayoutState(completion.workspace_layout);
      workspaceLayoutController.replaceAuthoritativeState(completion.workspace_layout);
      app?.applyWorkspaceLayoutChanged?.(workspaceLayout);
      typography = normalizeTypographyState(completion.typography);
      applyTypographyPreset(documentRoot, typography);
      app?.applyTypographyChanged?.(typography);
    }

    throw new Error("Appearance bootstrap did not converge.");
  } catch (error) {
    dispose();
    throw error;
  }
}

async function startBrowserBootstrap() {
  const root = document.querySelector("#app");
  if (!root) return;
  const backend = createBackendClient();
  try {
    await bootstrapAppearance({
      backend,
      documentRoot: document.documentElement,
      root,
      mount: mountApp,
    });
  } catch (_error) {
    root.textContent = "외관 초기화에 실패했습니다. 앱을 다시 실행해 주세요.";
    root.setAttribute("role", "alert");
  }
}

if (typeof document !== "undefined") {
  void startBrowserBootstrap();
}
