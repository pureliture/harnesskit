import assert from "node:assert/strict";
import test from "node:test";

import { createLocalViewState } from "../local-state.js";
import { renderLocalInspector } from "../local-view.js";

const aiModule = await import("../ai-explanation.js").catch(() => Object.freeze({}));

function requireAiApi(name) {
  assert.equal(
    typeof aiModule[name],
    "function",
    `ai-explanation.js must export ${name}`,
  );
  return aiModule[name];
}

function aiEndpoint(scheme, authority) {
  return `${scheme}://${authority}/v1`;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolveValue, rejectValue) => {
    resolve = resolveValue;
    reject = rejectValue;
  });
  return { promise, resolve, reject };
}

function identity(overrides = {}) {
  return {
    snapshotId: "snapshot-ai-1",
    instanceId: "instance-ai-1",
    sourceRevision: "source-ai-1",
    providerRevision: "provider-ai-1",
    ...overrides,
  };
}

function explanationDto(binding = identity(), overrides = {}) {
  return {
    snapshot_id: binding.snapshotId,
    instance_id: binding.instanceId,
    source_revision: binding.sourceRevision,
    provider_revision: binding.providerRevision,
    doing: "선택한 하네스가 하는 일",
    when_used: "이 하네스를 사용할 때",
    capabilities: "읽기 전용 기능",
    cautions: "runtime 검증 결과가 아님",
    ...overrides,
  };
}

function configuredProvider(overrides = {}) {
  return {
    state: "configured",
    baseUrl: "https://provider.example/v1",
    model: "gpt-4.1-mini",
    apiKeyPresent: true,
    providerRevision: "provider-ai-1",
    ...overrides,
  };
}

function selectedLocalData() {
  const item = {
    instanceId: "instance-ai-1",
    adapterId: "codex-builtin",
    adapterVersion: "1.0.0",
    toolId: "codex",
    surfaceId: "project-skills",
    scope: "project",
    projectId: "project-ai-1",
    safeLocator: ".agents/skills/release/SKILL.md",
    kind: "skill",
    name: "Release Safety",
    description: "Approved release workflow",
    parseState: "parsed",
    issueCodes: [],
    settings: [],
    correlation: { state: "uncorrelated" },
  };
  return {
    phase: "ready",
    latestComplete: {
      snapshotId: "snapshot-ai-1",
      attemptId: "attempt-ai-1",
      status: "complete",
    },
    latestTerminalReport: {
      attemptId: "attempt-ai-1",
      state: "complete",
    },
    queryResult: {
      snapshotId: "snapshot-ai-1",
      items: [item],
      projects: [{
        projectId: "project-ai-1",
        displayName: "routine-harness",
        canonicalPath: "/fixture-home/Projects/routine-harness",
      }],
      counts: { matchedInstances: 1 },
    },
    selectedDetail: item,
  };
}

function selectedLocalView(overrides = {}) {
  return {
    ...createLocalViewState({ selectedInstanceId: "instance-ai-1" }),
    sourcePreview: {
      phase: "ready",
      viewGeneration: 1,
      selection: {
        snapshotId: "snapshot-ai-1",
        instanceId: "instance-ai-1",
      },
      header: {
        previewSessionId: "preview-ai-1",
        snapshotId: "snapshot-ai-1",
        instanceId: "instance-ai-1",
        canonicalPath: "/fixture-home/Projects/routine-harness/.agents/skills/release/SKILL.md",
        sourceRevision: "source-ai-1",
        totalChunks: 1,
      },
      currentChunkIndex: 0,
      chunks: new Map(),
      error: null,
    },
    aiProvider: configuredProvider(),
    aiExplanation: {
      phase: "idle",
      binding: identity(),
      result: null,
      errorCode: null,
    },
    ...overrides,
  };
}

test("configured Local inspector exposes an explicit model-only AI action", () => {
  const markup = renderLocalInspector(selectedLocalData(), selectedLocalView());

  assert.match(markup, /data-ai-explain/);
  assert.match(markup, />AI 설명 생성 · gpt-4\.1-mini</);
  assert.doesNotMatch(markup, /https:\/\/provider\.example\/v1/);
  assert.match(markup, /data-local-source-content/);
});

test("Local inspector renders the transport warning beside one explanation action only for non-loopback HTTP", () => {
  const createAiProviderState = requireAiApi("createAiProviderState");
  const data = selectedLocalData();
  const unsafe = renderLocalInspector(data, selectedLocalView({
    aiProvider: createAiProviderState(configuredProvider({
      baseUrl: aiEndpoint("http", "192.168.1.18:8080"),
    })),
  }));

  assert.match(unsafe, /<button(?=[^>]*data-ai-explain)(?=[^>]*aria-describedby="ai-explain-transport-warning")[^>]*>AI 설명 생성 · gpt-4\.1-mini<\/button><\/div>\s*<p(?=[^>]*id="ai-explain-transport-warning")(?=[^>]*data-transport-warning="unencrypted_non_loopback")[^>]*>API key[^<]*전체 원문[^<]*암호화되지 않은/);
  assert.equal((unsafe.match(/data-ai-explain\b/g) ?? []).length, 1);
  assert.equal((unsafe.match(/id="ai-explain-transport-warning"/g) ?? []).length, 1);
  assert.doesNotMatch(unsafe, /192\.168\.1\.18|role="alertdialog"/);

  for (const baseUrl of ["http://localhost:11434/v1", aiEndpoint("http", "127.9.8.7:11434"), "http://[::1]:11434/v1", "https://provider.example/v1"]) {
    const safe = renderLocalInspector(data, selectedLocalView({
      aiProvider: createAiProviderState(configuredProvider({ baseUrl })),
    }));
    assert.match(safe, /data-ai-explain/, baseUrl);
    assert.doesNotMatch(safe, /unencrypted_non_loopback|ai-explain-transport-warning|암호화되지 않은/, baseUrl);
  }
});

test("AI explanation card is inert, has four sections, and precedes source preview", () => {
  const binding = identity();
  const result = explanationDto(binding, {
    doing: '<script>alert("doing")</script>',
    when_used: '<a href="https://outside.example">사용 시점</a>',
    capabilities: '<button onclick="run()">기능 실행</button>',
    cautions: "javascript:danger",
  });
  const markup = renderLocalInspector(selectedLocalData(), selectedLocalView({
    aiExplanation: {
      phase: "ready",
      binding,
      result,
      errorCode: null,
    },
  }));

  for (const heading of ["하는 일", "언제 사용되는지", "접근 가능한 기능", "주의할 점"]) {
    assert.match(markup, new RegExp(heading));
  }
  assert.match(markup, /AI 생성 설명 · runtime 검증 아님/);
  assert.ok(markup.indexOf("AI 생성 설명") < markup.indexOf("local-source-preview"));
  assert.match(markup, /&lt;script&gt;alert\(&quot;doing&quot;\)&lt;\/script&gt;/);
  assert.doesNotMatch(markup, /<script\b|<a\b|<button[^>]+onclick=/i);
  assert.match(markup, /data-local-source-content/);
});

test("source-too-large failure keeps the preview and explains that the whole file cannot be handled", () => {
  const markup = renderLocalInspector(selectedLocalData(), selectedLocalView({
    aiExplanation: {
      phase: "error",
      binding: identity(),
      result: null,
      errorCode: "source_too_large_for_ai",
    },
  }));

  assert.match(markup, /이 모델이 전체 파일을 처리할 수 없습니다/);
  assert.match(markup, /data-local-source-content/);
  assert.doesNotMatch(markup, /일부만|앞부분|자동 분할|잘라서/);
});

test("provider settings normalize one active config without retaining a returned key value", () => {
  const createAiProviderState = requireAiApi("createAiProviderState");
  const renderAiProviderSettings = requireAiApi("renderAiProviderSettings");
  const leakedSecret = "SECRET_MUST_NOT_REACH_FRONTEND_STATE";
  const provider = createAiProviderState({
    state: "configured",
    base_url: "https://provider.example/v1",
    model: "gpt-4.1-mini",
    api_key_present: true,
    provider_revision: "provider-ai-1",
    api_key: leakedSecret,
  });
  const markup = renderAiProviderSettings(provider);

  assert.doesNotMatch(JSON.stringify(provider), new RegExp(leakedSecret));
  assert.match(
    markup,
    /<input(?=[^>]*name="baseUrl")(?=[^>]*value="https:\/\/provider\.example\/v1")[^>]*>/,
  );
  assert.match(
    markup,
    /<input(?=[^>]*name="model")(?=[^>]*value="gpt-4\.1-mini")[^>]*>/,
  );
  assert.match(
    markup,
    /<input(?=[^>]*name="apiKey")(?=[^>]*type="password")(?=[^>]*value="")[^>]*>/,
  );
  assert.match(markup, /(?:API key[^<]*(?:저장|있음)|저장된 API key)/);
  assert.match(markup, /data-ai-provider-key-delete/);
  assert.match(markup, />API key 삭제</);
  assert.match(markup, /id="ai-provider-title">AI 연결 설정</);
  assert.match(markup, /aria-label="AI 연결 설정 닫기"/);
  assert.match(markup, /AI 설명 생성을 눌렀을 때만 선택한 전체 원문을 연결된 AI 모델로 보냅니다/);
  assert.doesNotMatch(markup, /AI 설명 설정/);
  assert.doesNotMatch(markup, new RegExp(leakedSecret));
});

test("AI provider transport warning classifies parsed HTTP hosts without trusting URL substrings", () => {
  const aiProviderTransportWarning = requireAiApi("aiProviderTransportWarning");
  const createAiProviderState = requireAiApi("createAiProviderState");
  const warning = "unencrypted_non_loopback";

  for (const baseUrl of [
    aiEndpoint("http", "provider.example"),
    aiEndpoint("HTTP", "PROVIDER.EXAMPLE"),
    aiEndpoint("http", "192.168.1.18:8080"),
    aiEndpoint("http", "[2001:db8::1]:8080"),
    aiEndpoint("http", "localhost.evil.example"),
    aiEndpoint("http", "127.0.0.1.evil.example"),
    aiEndpoint("http", ["localhost", "remote.example"].join("@")),
  ]) {
    assert.equal(aiProviderTransportWarning(baseUrl), warning, baseUrl);
    assert.equal(createAiProviderState(configuredProvider({ baseUrl })).transportWarning, warning, baseUrl);
  }

  for (const baseUrl of [
    "https://provider.example/v1",
    aiEndpoint("https", "192.168.1.18"),
    "http://localhost:11434/v1",
    "http://LOCALHOST.:11434/v1",
    "http://127.0.0.1:11434/v1",
    aiEndpoint("http", "127.9.8.7:11434"),
    aiEndpoint("http", "127.1:11434"),
    "http://[::1]:11434/v1",
    "not a URL",
  ]) {
    assert.equal(aiProviderTransportWarning(baseUrl), null, baseUrl);
    assert.equal(createAiProviderState(configuredProvider({ baseUrl })).transportWarning, null, baseUrl);
  }
  assert.equal(createAiProviderState().transportWarning, null);
});

test("settings warn about plaintext API key and source only for non-loopback HTTP", () => {
  const renderAiProviderSettings = requireAiApi("renderAiProviderSettings");
  const unsafe = renderAiProviderSettings(configuredProvider({
    baseUrl: aiEndpoint("http", "192.168.1.18:8080"),
  }));
  const warning = unsafe.match(/<p(?=[^>]*data-ai-provider-transport-warning="unencrypted_non_loopback")[^>]*>/)?.[0] ?? "";

  assert.ok(warning, "non-loopback HTTP settings must show a derived warning");
  assert.doesNotMatch(warning, /\bhidden\b/);
  assert.match(unsafe, /API key[^<]*전체 원문[^<]*암호화되지 않은/);
  assert.match(unsafe, /<input(?=[^>]*name="baseUrl")(?=[^>]*aria-describedby="ai-provider-transport-warning")[^>]*>/);
  assert.match(unsafe, /data-ai-provider-form/);
  assert.match(unsafe, />설정 저장</);

  for (const baseUrl of ["http://localhost:11434/v1", "http://[::1]:11434/v1", "https://provider.example/v1"]) {
    const safe = renderAiProviderSettings(configuredProvider({ baseUrl }));
    assert.doesNotMatch(safe, /unencrypted_non_loopback|암호화되지 않은/, baseUrl);
  }
});

test("API key preservation instructions are programmatically associated with the password input", () => {
  const renderAiProviderSettings = requireAiApi("renderAiProviderSettings");
  const markup = renderAiProviderSettings(configuredProvider());
  const input = markup.match(/<input(?=[^>]*name="apiKey")[^>]*>/)?.[0] ?? "";
  const helper = markup.match(
    /<p(?=[^>]*class="[^"]*ai-provider-key-state[^"]*")(?=[^>]*id="([^"]+)")[^>]*>/,
  );

  assert.ok(helper?.[1], "API key state must expose a stable description id");
  const describedBy = input.match(/aria-describedby="([^"]+)"/)?.[1]?.split(/\s+/) ?? [];
  assert.ok(
    describedBy.includes(helper[1]),
    "API key input must reference the preservation instructions with aria-describedby",
  );
});

test("binding a source never sends it until the user explicitly requests an explanation", async () => {
  const createAiExplanationController = requireAiApi("createAiExplanationController");
  const pending = deferred();
  const calls = [];
  const controller = createAiExplanationController({
    backend: {
      explainLocalSource(request) {
        calls.push(request);
        return pending.promise;
      },
    },
  });
  const binding = identity();

  controller.bind(binding);
  await Promise.resolve();
  assert.equal(calls.length, 0);

  const first = controller.request();
  const duplicate = controller.request();
  assert.deepEqual(calls, [binding]);

  pending.resolve(explanationDto(binding));
  await Promise.all([first, duplicate]);
  assert.equal(calls.length, 1);
  assert.equal(controller.getState().phase, "ready");
  assert.equal(controller.getState().result.doing, "선택한 하네스가 하는 일");
});

test("an in-flight result is discarded when its source tuple is no longer current", async () => {
  const createAiExplanationController = requireAiApi("createAiExplanationController");
  const pending = deferred();
  const controller = createAiExplanationController({
    backend: { explainLocalSource: () => pending.promise },
  });
  const oldBinding = identity();
  const currentBinding = identity({ instanceId: "instance-ai-2" });

  controller.bind(oldBinding);
  const request = controller.request();
  controller.bind(currentBinding);
  pending.resolve(explanationDto(oldBinding));
  await request;

  assert.deepEqual(controller.getState().binding, currentBinding);
  assert.equal(controller.getState().phase, "idle");
  assert.equal(controller.getState().result, null);
});

test("snapshot, provider, and selection disposal each clear the session-only explanation", async () => {
  const createAiExplanationController = requireAiApi("createAiExplanationController");
  const mutations = [
    identity({ snapshotId: "snapshot-ai-2" }),
    identity({ providerRevision: "provider-ai-2" }),
  ];

  for (const nextBinding of mutations) {
    const controller = createAiExplanationController({
      backend: {
        explainLocalSource: async (request) => explanationDto(request),
      },
    });
    controller.bind(identity());
    await controller.request();
    assert.equal(controller.getState().phase, "ready");

    controller.bind(nextBinding);
    assert.deepEqual(controller.getState().binding, nextBinding);
    assert.equal(controller.getState().phase, "idle");
    assert.equal(controller.getState().result, null);
  }

  const controller = createAiExplanationController({
    backend: {
      explainLocalSource: async (request) => explanationDto(request),
    },
  });
  controller.bind(identity());
  await controller.request();
  controller.clear();
  assert.equal(controller.getState().binding, null);
  assert.equal(controller.getState().result, null);

  const restarted = createAiExplanationController({ backend: {} });
  assert.equal(restarted.getState().phase, "idle");
  assert.equal(restarted.getState().result, null);
});

test("source-too-large backend error becomes the approved user-visible frontend state", async () => {
  const createAiExplanationController = requireAiApi("createAiExplanationController");
  const error = Object.assign(new Error("provider detail must not be shown"), {
    code: "source_too_large_for_ai",
  });
  const controller = createAiExplanationController({
    backend: { explainLocalSource: async () => { throw error; } },
  });

  controller.bind(identity());
  await controller.request();

  assert.equal(controller.getState().phase, "error");
  assert.equal(controller.getState().errorCode, "source_too_large_for_ai");
  assert.equal(
    controller.getState().message,
    "이 모델이 전체 파일을 처리할 수 없습니다",
  );
  assert.equal(controller.getState().result, null);
});
