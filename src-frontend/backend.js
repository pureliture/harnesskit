function resolveTauriInvoke() {
  const invoke = globalThis.__TAURI__?.core?.invoke;
  return typeof invoke === "function" ? invoke.bind(globalThis.__TAURI__.core) : undefined;
}

function resolveTauriListen() {
  const listen = globalThis.__TAURI__?.event?.listen;
  return typeof listen === "function" ? listen.bind(globalThis.__TAURI__.event) : undefined;
}

export function createBackendClient(invoke = resolveTauriInvoke(), listen = resolveTauriListen()) {
  const invokeCommand = (command, payload) => {
    if (typeof invoke !== "function") {
      throw new Error("Tauri runtime에서만 이 작업을 실행할 수 있습니다.");
    }
    return invoke(command, payload);
  };

  const requireId = (value, label) => {
    const normalized = String(value ?? "").trim();
    if (!normalized) throw new Error(`${label}가 필요합니다.`);
    return normalized;
  };

  const optionalId = (value) => {
    const normalized = String(value ?? "").trim();
    return normalized || null;
  };

  const requireIdList = (value, label) => {
    if (!Array.isArray(value) || value.length === 0) {
      throw new Error(`${label}가 필요합니다.`);
    }
    const normalized = value.map((item) => requireId(item, label));
    if (new Set(normalized).size !== normalized.length) {
      throw new Error(`${label}에 중복 ID가 있습니다.`);
    }
    return normalized;
  };

  return Object.freeze({
    async getBootstrapState() {
      return invokeCommand("get_bootstrap_state");
    },

    async setAppearanceMode(logicalMode) {
      if (!["System", "Light", "Dark"].includes(logicalMode)) {
        throw new Error("지원하지 않는 외관 모드입니다.");
      }
      return invokeCommand("set_appearance_mode", { logicalMode });
    },

    async setTypographyPreset(request = {}) {
      const expectedTypographyRevision = Number(request.expectedTypographyRevision);
      const preset = String(request.preset ?? "");
      if (!Number.isSafeInteger(expectedTypographyRevision)
        || expectedTypographyRevision < 0
        || !["Small", "Default", "Large"].includes(preset)) {
        throw new Error("유효한 글자 크기 설정이 필요합니다.");
      }
      return invokeCommand("set_typography_preset", {
        request: { expectedTypographyRevision, preset },
      });
    },

    async setWorkspaceLayout(request = {}) {
      const expectedLayoutRevision = Number(request.expectedLayoutRevision);
      const preferredLeftWidthPx = Number(request.preferredLeftWidthPx);
      const preferredRightWidthPx = Number(request.preferredRightWidthPx);
      const leftCollapsed = request.leftCollapsed === true;
      const rightCollapsed = request.rightCollapsed === true;
      if (!Number.isSafeInteger(expectedLayoutRevision) || expectedLayoutRevision < 0
        || !Number.isSafeInteger(preferredLeftWidthPx)
        || preferredLeftWidthPx < 200
        || preferredLeftWidthPx > 512
        || !Number.isSafeInteger(preferredRightWidthPx)
        || preferredRightWidthPx < 184
        || preferredRightWidthPx > 560) {
        throw new Error("유효한 workspace layout preference가 필요합니다.");
      }
      return invokeCommand("set_workspace_layout", {
        request: {
          expectedLayoutRevision,
          preferredLeftWidthPx,
          preferredRightWidthPx,
          leftCollapsed,
          rightCollapsed,
        },
      });
    },

    async completeBootstrap(appearanceRevision, layoutRevision, typographyRevision, uiProbe) {
      if (!Number.isSafeInteger(appearanceRevision) || appearanceRevision < 0) {
        throw new Error("유효한 외관 revision이 필요합니다.");
      }
      if (!Number.isSafeInteger(layoutRevision) || layoutRevision < 0) {
        throw new Error("유효한 layout revision이 필요합니다.");
      }
      if (!Number.isSafeInteger(typographyRevision) || typographyRevision < 0) {
        throw new Error("유효한 typography revision이 필요합니다.");
      }
      const childElementCount = Number(uiProbe?.childElementCount);
      const textLength = Number(uiProbe?.textLength);
      const desktopAppPresent = uiProbe?.desktopAppPresent === true;
      const resolvedMode = String(uiProbe?.resolvedMode ?? "").toLowerCase();
      const viewportWidth = Number(uiProbe?.viewportWidth);
      const viewportHeight = Number(uiProbe?.viewportHeight);
      const desktopWidth = Number(uiProbe?.desktopWidth);
      const desktopHeight = Number(uiProbe?.desktopHeight);
      const layoutVisible = uiProbe?.layoutVisible === true;
      const appliedLayoutRevision = Number(uiProbe?.appliedLayoutRevision);
      const appliedTypographyRevision = Number(uiProbe?.appliedTypographyRevision);
      const preferredLeftWidthPx = Number(uiProbe?.appliedPreferredPair?.preferredLeftWidthPx);
      const preferredRightWidthPx = Number(uiProbe?.appliedPreferredPair?.preferredRightWidthPx);
      const leftCollapsed = uiProbe?.appliedCollapsedPair?.leftCollapsed;
      const rightCollapsed = uiProbe?.appliedCollapsedPair?.rightCollapsed;
      const appliedTypographyPreset = String(uiProbe?.appliedTypographyPreset ?? "");
      const layoutMode = String(uiProbe?.layoutMode ?? "");
      const normalizeFrame = (value) => Object.freeze({
        x: Number(value?.x),
        y: Number(value?.y),
        width: Number(value?.width),
        height: Number(value?.height),
      });
      const shellFrame = normalizeFrame(uiProbe?.shellFrame);
      const paneFrames = Object.freeze({
        left: uiProbe?.paneFrames?.left == null
          ? null
          : normalizeFrame(uiProbe.paneFrames.left),
        center: normalizeFrame(uiProbe?.paneFrames?.center),
        right: uiProbe?.paneFrames?.right == null
          ? null
          : normalizeFrame(uiProbe.paneFrames.right),
      });
      const activeSeparatorCount = Number(uiProbe?.activeSeparatorCount);
      const disabledSeparatorCount = Number(uiProbe?.disabledSeparatorCount);
      const frameValues = [shellFrame, paneFrames.left, paneFrames.center, paneFrames.right]
        .filter(Boolean)
        .flatMap((frame) => [frame.x, frame.y, frame.width, frame.height]);
      if (!Number.isSafeInteger(childElementCount)
        || childElementCount < 1
        || !Number.isSafeInteger(textLength)
        || textLength < 1
        || !desktopAppPresent
        || ![viewportWidth, viewportHeight, desktopWidth, desktopHeight]
          .every((value) => Number.isFinite(value) && value > 0)
        || !layoutVisible
        || !["light", "dark"].includes(resolvedMode)
        || !Number.isSafeInteger(appliedLayoutRevision)
        || appliedLayoutRevision < 0
        || !Number.isSafeInteger(appliedTypographyRevision)
        || appliedTypographyRevision < 0
        || !Number.isSafeInteger(preferredLeftWidthPx)
        || !Number.isSafeInteger(preferredRightWidthPx)
        || typeof leftCollapsed !== "boolean"
        || typeof rightCollapsed !== "boolean"
        || leftCollapsed !== (paneFrames.left == null)
        || rightCollapsed !== (paneFrames.right == null)
        || !["Small", "Default", "Large"].includes(appliedTypographyPreset)
        || !["three-pane", "two-column", "stacked"].includes(layoutMode)
        || !frameValues.every((value) => Number.isSafeInteger(value) && value >= 0)
        || !Number.isSafeInteger(activeSeparatorCount)
        || activeSeparatorCount < 0
        || !Number.isSafeInteger(disabledSeparatorCount)
        || disabledSeparatorCount < 0) {
        throw new Error("렌더링된 앱 shell evidence가 필요합니다.");
      }
      return invokeCommand("complete_bootstrap", {
        appearanceRevision,
        layoutRevision,
        typographyRevision,
        uiProbe: {
          childElementCount,
          textLength,
          desktopAppPresent,
          resolvedMode,
          viewportWidth,
          viewportHeight,
          desktopWidth,
          desktopHeight,
          layoutVisible,
          appliedLayoutRevision,
          appliedTypographyRevision,
          appliedPreferredPair: {
            preferredLeftWidthPx,
            preferredRightWidthPx,
          },
          appliedCollapsedPair: {
            leftCollapsed,
            rightCollapsed,
          },
          appliedTypographyPreset,
          layoutMode,
          shellFrame,
          paneFrames,
          activeSeparatorCount,
          disabledSeparatorCount,
        },
      });
    },

    async onAppearanceChanged(handler) {
      if (typeof listen !== "function") {
        throw new Error("Tauri runtime에서만 appearance event를 구독할 수 있습니다.");
      }
      return listen("appearance_changed", (event) => handler(event.payload));
    },

    async getSotSessionState() {
      return invokeCommand("get_sot_session_state");
    },

    async loadSotSnapshot(checkoutId) {
      const normalizedId = String(checkoutId ?? "").trim();
      if (!normalizedId) {
        throw new Error("SoT snapshot을 읽을 등록 checkout ID가 필요합니다.");
      }
      return invokeCommand("load_sot_snapshot", { checkoutId: normalizedId });
    },

    async getLocalScanState() {
      return invokeCommand("get_local_scan_state");
    },

    async startLocalScan() {
      return invokeCommand("start_local_scan");
    },

    async getProjectIgnore({ snapshotId, projectId } = {}) {
      return invokeCommand("get_project_ignore", {
        snapshotId: requireId(snapshotId, "Local snapshot ID"),
        projectId: requireId(projectId, "Project ID"),
      });
    },

    async saveProjectIgnoreAndRescan({
      snapshotId,
      projectId,
      sourceRevision,
      exactText,
    } = {}) {
      return invokeCommand("save_project_ignore_and_rescan", {
        request: {
          snapshotId: requireId(snapshotId, "Local snapshot ID"),
          projectId: requireId(projectId, "Project ID"),
          sourceRevision: requireId(sourceRevision, "Ignore source revision"),
          exactText: String(exactText ?? ""),
        },
      });
    },

    async onLocalScanChanged(handler) {
      if (typeof listen !== "function") {
        throw new Error("Tauri runtime에서만 Local scan event를 구독할 수 있습니다.");
      }
      return listen("local_scan_changed", (event) => handler(event.payload));
    },

    async getCorrelationProjection({
      localSnapshotId,
      sotSnapshotId = null,
      installEvidenceId = null,
    }) {
      return invokeCommand("get_correlation_projection", {
        localSnapshotId: requireId(localSnapshotId, "Local snapshot ID"),
        sotSnapshotId: optionalId(sotSnapshotId),
        installEvidenceId: optionalId(installEvidenceId),
      });
    },

    async queryLocalInstances(request = {}) {
      const scope = String(request.locationFilter?.scope ?? "all").toLowerCase();
      if (!["all", "user", "project"].includes(scope)) {
        throw new Error("지원하지 않는 Local location scope입니다.");
      }
      const projectId = scope === "project"
        ? optionalId(request.locationFilter?.projectId)
        : null;
      const correlationProjectionId = optionalId(request.correlationProjectionId);
      const verifiedComponentId = optionalId(request.verifiedComponentId);
      if (verifiedComponentId && !correlationProjectionId) {
        throw new Error("Verified component filter에는 correlation projection ID가 필요합니다.");
      }
      return invokeCommand("query_local_instances", {
        request: {
          snapshotId: requireId(request.snapshotId, "Local snapshot ID"),
          locationFilter: { scope, projectId },
          toolId: optionalId(request.toolId),
          kind: optionalId(request.kind),
          query: String(request.query ?? ""),
          correlationProjectionId,
          verifiedComponentId,
        },
      });
    },

    async getLocalInstanceDetail({ snapshotId, correlationProjectionId = null, instanceId }) {
      return invokeCommand("get_local_instance_detail", {
        snapshotId: requireId(snapshotId, "Local snapshot ID"),
        correlationProjectionId: optionalId(correlationProjectionId),
        instanceId: requireId(instanceId, "Local instance ID"),
      });
    },

    async openLocalSourcePreview(request = {}) {
      const viewGeneration = Number(request.viewGeneration);
      if (!Number.isSafeInteger(viewGeneration) || viewGeneration < 0) {
        throw new Error("유효한 source view generation이 필요합니다.");
      }
      return invokeCommand("open_local_source_preview", {
        request: {
          snapshotId: requireId(request.snapshotId, "Local snapshot ID"),
          instanceId: requireId(request.instanceId, "Local instance ID"),
          viewGeneration,
        },
      });
    },

    async readLocalSourcePreviewChunk(request = {}) {
      const chunkIndex = Number(request.chunkIndex);
      if (!Number.isSafeInteger(chunkIndex) || chunkIndex < 0) {
        throw new Error("유효한 source chunk index가 필요합니다.");
      }
      return invokeCommand("read_local_source_preview_chunk", {
        request: {
          previewSessionId: requireId(request.previewSessionId, "Preview session ID"),
          sourceRevision: requireId(request.sourceRevision, "Source revision"),
          chunkIndex,
        },
      });
    },

    async closeLocalSourcePreview(request = {}) {
      const viewGeneration = Number(request.viewGeneration);
      if (!Number.isSafeInteger(viewGeneration) || viewGeneration < 0) {
        throw new Error("유효한 source view generation이 필요합니다.");
      }
      return invokeCommand("close_local_source_preview", {
        request: {
          viewGeneration,
          previewSessionId: optionalId(request.previewSessionId),
        },
      });
    },

    async actOnLocalInstance({ snapshotId, instanceId, action }) {
      if (!["reveal", "copy_path"].includes(action)) {
        throw new Error("지원하지 않는 read-only action입니다.");
      }
      return invokeCommand("act_on_local_instance", {
        snapshotId: requireId(snapshotId, "Local snapshot ID"),
        instanceId: requireId(instanceId, "Local instance ID"),
        action,
      });
    },

    async prepareLocalRemoval({ snapshotId, instanceIds }) {
      return invokeCommand("prepare_local_removal", {
        request: {
          snapshotId: requireId(snapshotId, "Local snapshot ID"),
          instanceIds: requireIdList(instanceIds, "Local instance ID"),
        },
      });
    },

    async applyLocalRemoval({ planId, planDigest, confirmed }) {
      if (confirmed !== true) {
        throw new Error("하네스 제거 확인이 필요합니다.");
      }
      return invokeCommand("apply_local_removal", {
        request: {
          planId: requireId(planId, "Removal plan ID"),
          planDigest: requireId(planDigest, "Removal plan digest"),
          confirmed: true,
        },
      });
    },

    async reconcileLocalRemoval({ snapshotId, expectedAttemptId, instanceIds }) {
      return invokeCommand("reconcile_local_removal", {
        request: {
          snapshotId: requireId(snapshotId, "Local snapshot ID"),
          expectedAttemptId: requireId(expectedAttemptId, "Local scan attempt ID"),
          instanceIds: requireIdList(instanceIds, "Local instance ID"),
        },
      });
    },

    async getAiProviderConfig() {
      return invokeCommand("get_ai_provider_config");
    },

    async saveAiProviderConfig(request = {}) {
      return invokeCommand("save_ai_provider_config", {
        request: {
          expectedProviderRevision: optionalId(request.expectedProviderRevision),
          baseUrl: requireId(request.baseUrl, "AI provider Base URL"),
          model: requireId(request.model, "AI provider model"),
          apiKey: String(request.apiKey ?? ""),
        },
      });
    },

    async deleteAiProviderKey(providerRevision) {
      return invokeCommand("delete_ai_provider_key", {
        request: {
          providerRevision: requireId(providerRevision, "AI provider revision"),
        },
      });
    },

    async explainLocalSource(request = {}) {
      return invokeCommand("explain_local_source", {
        request: {
          snapshotId: requireId(request.snapshotId, "Local snapshot ID"),
          instanceId: requireId(request.instanceId, "Local instance ID"),
          sourceRevision: requireId(request.sourceRevision, "Source revision"),
          providerRevision: requireId(request.providerRevision, "AI provider revision"),
        },
      });
    },

    async cloneCheckout() {
      return invokeCommand("clone_checkout");
    },

    async pickCheckoutDirectory() {
      return invokeCommand("pick_checkout_directory");
    },

    async registerCheckout(checkoutPath) {
      const path = String(checkoutPath ?? "");
      if (!path.trim()) {
        throw new Error("등록할 checkout 경로가 필요합니다.");
      }
      return invokeCommand("register_checkout", { checkoutPath: path });
    },

    async previewInstall({ checkoutId, sotSnapshotId, profileId, scope: requestedScope, targetRoot, targetIds }) {
      const scope = String(requestedScope ?? "").trim().toLowerCase();
      if (!["user", "project"].includes(scope)) {
        throw new Error("지원하지 않는 install scope입니다.");
      }
      return invokeCommand("preview_install", {
        checkoutId: requireId(checkoutId, "Checkout ID"),
        request: {
          sotSnapshotId: requireId(sotSnapshotId, "SoT snapshot ID"),
          profileId: requireId(profileId, "Profile ID"),
          targetIds: requireIdList(targetIds, "Target ID"),
          scope,
          targetRoot: requireId(targetRoot, "Target root"),
        },
      });
    },

    async applyInstall({ previewId, approvals = {} }) {
      if (approvals.confirmed !== true) {
        throw new Error("Install apply에는 명시적 확인이 필요합니다.");
      }
      return invokeCommand("apply_install", {
        previewId: requireId(previewId, "Preview ID"),
        approvals: {
          confirmed: true,
          semanticFingerprint: requireId(
            approvals.semanticFingerprint,
            "Preview semantic fingerprint",
          ),
          overwrite: approvals.overwrite === true,
          allowRuntimeHooks: approvals.allowRuntimeHooks === true,
        },
      });
    },
  });
}
