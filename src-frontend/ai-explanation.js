function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function optionalText(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

export const AI_TRANSPORT_WARNING = Object.freeze({
  code: "unencrypted_non_loopback",
  message: "API key와 선택한 전체 원문이 암호화되지 않은 HTTP로 전송될 수 있습니다.",
});

export function aiProviderTransportWarning(baseUrl) {
  try {
    const url = new URL(String(baseUrl ?? "").trim());
    if (url.protocol !== "http:") return null;
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    if (host === "localhost" || host === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(host)) {
      return null;
    }
    return AI_TRANSPORT_WARNING.code;
  } catch (_error) {
    return null;
  }
}

export function createAiProviderState(value = {}) {
  const state = String(value?.state ?? "unconfigured").toLowerCase();
  if (state !== "configured") {
    return Object.freeze({
      state: "unconfigured",
      baseUrl: "",
      model: "",
      apiKeyPresent: false,
      providerRevision: null,
      transportWarning: null,
    });
  }
  const baseUrl = optionalText(value.baseUrl ?? value.base_url);
  const model = optionalText(value.model);
  const providerRevision = optionalText(
    value.providerRevision ?? value.provider_revision,
  );
  if (!baseUrl || !model || !providerRevision) {
    return createAiProviderState();
  }
  return Object.freeze({
    state: "configured",
    baseUrl,
    model,
    apiKeyPresent: value.apiKeyPresent === true || value.api_key_present === true,
    providerRevision,
    transportWarning: aiProviderTransportWarning(baseUrl),
  });
}

export function renderAiProviderSettings(providerValue, options = {}) {
  const provider = createAiProviderState(providerValue);
  const configured = provider.state === "configured";
  const busy = options.busy === true;
  return `<section class="ai-provider-settings" aria-labelledby="ai-provider-title">
    <header><div><p class="eyebrow">OpenAI-compatible provider</p><h2 id="ai-provider-title">AI 연결 설정</h2></div><button type="button" class="button button--quiet" data-ai-provider-close aria-label="AI 연결 설정 닫기">닫기</button></header>
    <p>AI 설명 생성을 눌렀을 때만 선택한 전체 원문을 연결된 AI 모델로 보냅니다.</p>
    <form data-ai-provider-form aria-busy="${busy}">
      <label>Base URL<input name="baseUrl" type="url" required value="${escapeHtml(provider.baseUrl)}" placeholder="http://127.0.0.1:11434/v1" autocomplete="url" spellcheck="false" aria-describedby="ai-provider-transport-warning"></label>
      <p id="ai-provider-transport-warning" class="ai-transport-warning" data-ai-provider-transport-warning="${provider.transportWarning ?? ""}"${provider.transportWarning ? "" : " hidden"}>${provider.transportWarning ? AI_TRANSPORT_WARNING.message : ""}</p>
      <label>Model<input name="model" type="text" required value="${escapeHtml(provider.model)}" placeholder="model-name" autocomplete="off" spellcheck="false"></label>
      <label>API key<input name="apiKey" type="password" value="" placeholder="${configured && provider.apiKeyPresent ? "저장된 API key 유지" : "필요한 경우 입력"}" autocomplete="new-password" spellcheck="false" aria-describedby="ai-provider-key-state"></label>
      <p id="ai-provider-key-state" class="ai-provider-key-state">${configured && provider.apiKeyPresent ? "저장된 API key 있음 · 빈 입력으로 저장하면 유지됩니다." : "저장된 API key 없음"}</p>
      <div class="ai-provider-actions"><button type="submit" class="button button--primary"${busy ? ' aria-disabled="true"' : ""}>${busy ? "저장 중…" : "설정 저장"}</button>${configured && provider.apiKeyPresent ? `<button type="button" class="button button--quiet" data-ai-provider-key-delete${busy ? ' aria-disabled="true"' : ""}>API key 삭제</button>` : ""}</div>
    </form>
  </section>`;
}

function normalizeBinding(value) {
  const binding = {
    snapshotId: optionalText(value?.snapshotId ?? value?.snapshot_id),
    instanceId: optionalText(value?.instanceId ?? value?.instance_id),
    sourceRevision: optionalText(value?.sourceRevision ?? value?.source_revision),
    providerRevision: optionalText(
      value?.providerRevision ?? value?.provider_revision,
    ),
  };
  return Object.values(binding).every(Boolean) ? Object.freeze(binding) : null;
}

function sameBinding(left, right) {
  return Boolean(left && right)
    && left.snapshotId === right.snapshotId
    && left.instanceId === right.instanceId
    && left.sourceRevision === right.sourceRevision
    && left.providerRevision === right.providerRevision;
}

function normalizeExplanation(value, binding) {
  const responseBinding = normalizeBinding(value);
  if (!sameBinding(responseBinding, binding)) return null;
  const result = {
    doing: optionalText(value?.doing),
    whenUsed: optionalText(value?.whenUsed ?? value?.when_used),
    capabilities: optionalText(value?.capabilities),
    cautions: optionalText(value?.cautions),
  };
  return Object.values(result).every(Boolean) ? Object.freeze(result) : null;
}

const ERROR_MESSAGES = Object.freeze({
  source_too_large_for_ai: "이 모델이 전체 파일을 처리할 수 없습니다",
  provider_stale: "AI 설정이 변경되었습니다. 최신 설정으로 다시 시도하세요.",
  source_stale: "파일이 변경되었습니다. 최신 원문을 다시 여세요.",
  provider_auth_invalid: "API key를 확인한 뒤 다시 시도하세요.",
  provider_auth_failed: "API key를 확인한 뒤 다시 시도하세요.",
  provider_request_failed: "AI provider에 연결하지 못했습니다.",
  provider_response_invalid: "AI provider 응답 형식을 확인할 수 없습니다.",
  provider_transport_unavailable: "AI provider에 연결하지 못했습니다.",
});

function idleState(binding = null) {
  return Object.freeze({
    phase: "idle",
    binding,
    result: null,
    errorCode: null,
    message: "",
  });
}

export function createAiExplanationController({ backend = {}, onChange = () => {} } = {}) {
  let state = idleState();
  let generation = 0;
  let pending = null;

  const publish = (next) => {
    state = Object.freeze(next);
    onChange(state);
  };

  const bind = (value) => {
    const binding = normalizeBinding(value);
    if (sameBinding(state.binding, binding)) return state;
    generation += 1;
    pending = null;
    publish(idleState(binding));
    return state;
  };

  const clear = () => {
    generation += 1;
    pending = null;
    publish(idleState());
    return state;
  };

  const request = () => {
    if (pending) return pending;
    const binding = state.binding;
    if (!binding || typeof backend.explainLocalSource !== "function") {
      publish({
        ...idleState(binding),
        phase: "error",
        errorCode: "ai_explanation_unavailable",
        message: "AI 설명을 생성할 수 없습니다.",
      });
      return Promise.resolve(state);
    }
    const requestGeneration = generation;
    publish({
      ...idleState(binding),
      phase: "pending",
      message: "AI 설명을 생성하고 있습니다.",
    });
    let backendRequest;
    try {
      backendRequest = Promise.resolve(backend.explainLocalSource(binding));
    } catch (error) {
      backendRequest = Promise.reject(error);
    }
    pending = backendRequest
      .then((value) => {
        if (requestGeneration !== generation || !sameBinding(state.binding, binding)) {
          return state;
        }
        const result = normalizeExplanation(value, binding);
        if (!result) {
          publish({
            ...idleState(binding),
            phase: "error",
            errorCode: "provider_response_invalid",
            message: ERROR_MESSAGES.provider_response_invalid,
          });
          return state;
        }
        publish({
          phase: "ready",
          binding,
          result,
          errorCode: null,
          message: "AI 설명을 생성했습니다.",
        });
        return state;
      })
      .catch((error) => {
        if (requestGeneration !== generation || !sameBinding(state.binding, binding)) {
          return state;
        }
        const requestedCode = optionalText(error?.code);
        const errorCode = Object.hasOwn(ERROR_MESSAGES, requestedCode)
          ? requestedCode
          : "ai_explanation_failed";
        publish({
          ...idleState(binding),
          phase: "error",
          errorCode,
          message: ERROR_MESSAGES[errorCode] ?? "AI 설명을 생성하지 못했습니다.",
        });
        return state;
      })
      .finally(() => {
        if (requestGeneration === generation) pending = null;
      });
    return pending;
  };

  return Object.freeze({
    bind,
    clear,
    request,
    getState: () => state,
  });
}
