import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const helperPath = process.argv[2];
assert.ok(helperPath, "exact injected helper path is required");
const helper = fs.readFileSync(helperPath, "utf8");

const snippets = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const bindings = snippets.bindings;
const normalize = (value) => JSON.parse(JSON.stringify(value));
const observed = { factories: [], requests: [], logs: [], allowed: [] };
let allowRestored = true;
let tokenFailure;
const tokenClient = {
  beta: {
    messages: {
      countTokens: async (request) => {
        observed.requests.push(request);
        if (tokenFailure) throw tokenFailure;
        return { input_tokens: 23 };
      },
    },
  },
};
const portableContext = vm.createContext({
  process: { env: {} },
  WRAP: (_messages, _tools, callback) => callback(),
  DEFAULT: () => "claude-default",
  [bindings.LF]: async (options) => {
    observed.factories.push(options);
    return tokenClient;
  },
  [bindings.KA]: (model) => model,
  [bindings.RAW]: ["keep", "drop"],
  [bindings.ij6]: new Set(["keep"]),
  [bindings.N]: (message) => observed.logs.push(message),
  [bindings.ALLOW]: (model) => {
    observed.allowed.push(model);
    return allowRestored;
  },
  Yl: () => "native-setting",
  YD6: () => false,
  N5H: () => false,
  QOA: () => false,
  qK: (model) => model,
  NATIVE_FAMILY: () => true,
  [bindings.ALIASES]: ["sonnet", "best", "opus[1m]", "opusplan"],
  [bindings.FIRST_PARTY]: ["claude-native", "sonnet"],
  [bindings.MODELS]: () => ({ fable5: "claude-configured" }),
  [bindings.NATIVE_PICKER]: () => [{ value: null }, { value: "gateway:native" }],
  [bindings.k]: {
    enum: (values) => ({
      values,
      optional() {
        return this;
      },
      describe() {
        return this;
      },
    }),
  },
});
vm.runInContext(
  `${helper};${snippets.tokens};globalThis.restore=function(_){${snippets.resume}};globalThis.schema=({${snippets.agent}}).model;globalThis.tokens=TOKENS;`,
  portableContext,
);
assert.equal(await portableContext.tokens([], [], "claude-native"), 23);
assert.equal(await portableContext.tokens([], [], undefined), 23);
assert.deepEqual(
  observed.factories.map((options) => options.model),
  ["claude-native", "claude-default"],
);
assert.deepEqual(
  observed.requests.map((request) => request.model),
  ["claude-native", "claude-default"],
);
tokenFailure = Error("native failure");
assert.equal(await portableContext.tokens([], [], "claude-native"), null);
assert.equal(observed.logs.length, 1);
await assert.rejects(portableContext.tokens([], [], "zai:glm-5.3"), {
  code: "EPROVIDERCREDENTIAL",
});
assert.equal(observed.factories.length, 3, "external preflight precedes native client creation");
for (const model of ["glm-5.3", "zai:glm-5.3"]) {
  allowRestored = true;
  assert.deepEqual(normalize(portableContext.restore({ message: { model } })), {
    kind: "ok",
    model: "zai:glm-5.3",
  });
  allowRestored = false;
  assert.deepEqual(normalize(portableContext.restore({ message: { model } })), {
    kind: "declined",
    model: "zai:glm-5.3",
    reason: "not_allowed",
  });
}
assert.deepEqual(observed.allowed, Array(4).fill("zai:glm-5.3"));
for (const model of ["kimi:kimi-k3", "moonshot:kimi-k3", "kimi-k3"]) {
  allowRestored = true;
  assert.deepEqual(normalize(portableContext.restore({ message: { model } })), {
    kind: "ok",
    model: "moonshot:kimi-k3",
  });
  allowRestored = false;
  assert.deepEqual(normalize(portableContext.restore({ message: { model } })), {
    kind: "declined",
    model: "moonshot:kimi-k3",
    reason: "not_allowed",
  });
}
assert.equal(portableContext.restore({ message: { model: "claude-native" } }).kind, "native");
const schemaValues = Array.from(portableContext.schema.values);
for (const model of [
  "sonnet",
  "best",
  "opus[1m]",
  "opusplan",
  "claude-native",
  "claude-configured",
  "gateway:native",
  "moonshot:kimi-k3",
  "kimi:kimi-k3",
  "zai:glm-5.3",
  "openai:gpt-6-astra",
]) {
  assert.ok(schemaValues.includes(model), model);
}
assert.equal(schemaValues.includes(null), false);
assert.equal(new Set(schemaValues).size, schemaValues.length);

const credentials = {};
const reads = [];
const environment = new Proxy(credentials, {
  get(target, key) {
    reads.push(key);
    return target[key];
  },
});
const fakeProcess = { env: environment };
const constructed = [];
let SDK;
class FakeSDK {
  constructor(options) {
    this.options = options;
    this.timeout = options.timeout;
    this.fetchOptions = options.fetchOptions;
    this.fetch = options.fetch;
    this._options = {
      ...options,
      defaultHeaders: {
        Authorization: "custom-secret",
        "X-Api-Key": "custom-key",
        ...options.defaultHeaders,
      },
    };
    this.calls = [];
    this.beta = {
      messages: {
        create: async (request, requestOptions) => {
          this.calls.push(["create", request, requestOptions]);
          return { provider: options.baseURL };
        },
        countTokens: async (request, requestOptions) => {
          this.calls.push(["countTokens", request, requestOptions]);
          return { input_tokens: 17 };
        },
      },
    };
    constructed.push(this);
  }
}
const context = vm.createContext({
  _ccMultiProviderSDK: () => SDK,
  process: fakeProcess,
});
const script = new vm.Script(
  `${helper};globalThis.__api={route:_ccMultiProviderRoute,modelInfo:_ccMultiProviderModelInfo,catalogInfo:_ccMultiProviderCatalogInfo,modelProvider:_ccMultiProviderModelProvider,attribution:_ccMultiProviderAttribution,inputTokens:_ccMultiProviderInputTokens,toolAllowed:_ccMultiProviderToolAllowed,clients:_ccMultiProviderClients,catalog:_ccMultiProviderCatalog,pickerCatalog:_ccMultiProviderPickerCatalog};`,
  { filename: helperPath },
);
script.runInContext(context);
const api = context.__api;

const ordinaryTools = [
  { name: "WebSearch" },
  { name: "WebFetch" },
  { name: "Read" },
  { name: "mcp__docs__search", isMcp: true },
];
const allowedTools = (model) => ordinaryTools.filter((tool) => api.toolAllowed(model, tool));
assert.deepEqual(
  Array.from(allowedTools("claude-sonnet-4-6"), ({ name }) => name),
  ["WebSearch", "WebFetch", "Read", "mcp__docs__search"],
);
assert.deepEqual(
  Array.from(allowedTools("zai:glm-5.3"), ({ name }) => name),
  ["WebFetch", "Read", "mcp__docs__search"],
);

assert.equal(api.catalog.length, 14);
assert.ok(api.catalog.every(({ value }) => !value.startsWith("kimi:")));
assert.equal(api.catalogInfo("kimi:kimi-k3"), api.catalogInfo("moonshot:kimi-k3"));
assert.equal(api.catalogInfo("moonshot:kimi-k3").description, "Moonshot general-purpose model");
assert.equal(api.modelInfo("kimi:kimi-k3").provider, "moonshot");
assert.equal(api.modelProvider("kimi:kimi-k3"), "moonshot");
assert.deepEqual(normalize(api.attribution("kimi:kimi-k3", "Claude")), {
  label: "Kimi K3",
  domain: "moonshot.ai",
});
assert.equal(api.toolAllowed("kimi:kimi-k3", { name: "WebSearch" }), false);
for (const [model, label, domain] of [
  ["openai:gpt-5.6-sol", "GPT-5.6 Sol", "openai.com"],
  ["openai:gpt-6-astra", "GPT-6 Astra", "openai.com"],
  ["moonshot:kimi-k3", "Kimi K3", "moonshot.ai"],
  ["zai:glm-5.3", "GLM 5.3", "z.ai"],
  ["minimax:MiniMax-M3", "MiniMax M3", "minimax.io"],
]) {
  assert.deepEqual({ ...api.attribution(model, "Claude") }, { label, domain }, model);
}
for (const model of ["claude-sonnet-4-6", "future-native-model", "gateway:model", undefined]) {
  assert.deepEqual(
    { ...api.attribution(model, "Claude Opus 4.8") },
    { label: "Claude Opus 4.8", domain: "anthropic.com" },
    String(model),
  );
}
const expectedLimits = new Map([
  ["moonshot:kimi-k3", [1048576, 131072]],
  ["moonshot:kimi-k2.7-code", [262144, 32768]],
  ["zai:glm-5.3", [1000000, 131072]],
  ["zai:glm-5.3-flash", [1000000, 131072]],
  ["zai:glm-5.2", [1000000, 131072]],
  ["zai:glm-5-turbo", [200000, 131072]],
  ["zai:glm-4.7", [204800, 131072]],
  ["zai:glm-4.5-air", [131072, 98304]],
  ["minimax:MiniMax-M3", [1048576, 512000]],
  ["minimax:MiniMax-M2.7", [204800, 131072]],
  ["openai:gpt-6-astra", [272000, 128000]],
  ["openai:gpt-5.6-sol", [272000, 128000]],
  ["openai:gpt-5.6-terra", [272000, 128000]],
  ["openai:gpt-5.6-luna", [272000, 128000]],
]);
for (const [model, limits] of expectedLimits) {
  const info = api.catalogInfo(model);
  assert.ok(info, model);
  assert.deepEqual([info.contextWindow, info.maxOutputTokens], limits, model);
}
for (const model of [undefined, null, 42, "claude-sonnet-4-6", "zai:unknown", "Zai:glm-5.3"]) {
  assert.equal(api.catalogInfo(model), null, String(model));
}
assert.deepEqual(
  Array.from(api.pickerCatalog(), ({ value }) => value),
  [],
);
credentials.CC_KIMI_AUTH_TOKEN = "   ";
credentials.CC_ZAI_AUTH_TOKEN = "zai-picker-token";
credentials.CC_MINIMAX_AUTH_TOKEN = "minimax-picker-token";
credentials.CC_OPENAI_PROXY_AUTH_TOKEN = "openai-picker-token";
credentials.CC_OPENAI_PROXY_EFFECTIVE_URL = "http://127.0.0.1:17780";
credentials.CC_OPENAI_AVAILABLE = "0";
assert.deepEqual(
  Array.from(api.pickerCatalog(), ({ value }) => value),
  [
    "zai:glm-5.3",
    "zai:glm-5.3-flash",
    "zai:glm-5.2",
    "zai:glm-5-turbo",
    "zai:glm-4.7",
    "zai:glm-4.5-air",
    "minimax:MiniMax-M3",
    "minimax:MiniMax-M2.7",
  ],
);
credentials.CC_KIMI_AUTH_TOKEN = "kimi-picker-token";
credentials.CC_OPENAI_AVAILABLE = "1";
assert.equal(api.pickerCatalog().length, 14);
delete credentials.CC_ZAI_AUTH_TOKEN;
delete credentials.CC_MINIMAX_AUTH_TOKEN;
delete credentials.CC_OPENAI_PROXY_AUTH_TOKEN;
assert.deepEqual(
  Array.from(api.pickerCatalog(), ({ value }) => value),
  ["moonshot:kimi-k3", "moonshot:kimi-k2.7-code"],
);
delete credentials.CC_KIMI_AUTH_TOKEN;
delete credentials.CC_OPENAI_AVAILABLE;
reads.length = 0;

const fetchOptions = Object.freeze({
  dispatcher: "proxy-and-ca",
  headers: Object.freeze({ Authorization: "native-transport-secret" }),
});
const fetchOverride = Object.freeze(() => {});
const nativeClient = Object.freeze({
  native: true,
  timeout: 1234,
  fetchOptions,
  fetch: fetchOverride,
});
const nativeRequest = Object.freeze({
  model: "claude-sonnet-4-6",
  messages: [],
  fallback_credit_token: "native-credit",
});
const nativeOptions = Object.freeze({
  signal: Object.freeze({ native: true }),
  timeout: 99,
  headers: Object.freeze({ Authorization: "native-secret" }),
});
const nativeRoute = api.route(nativeClient, nativeRequest, nativeOptions);
assert.equal(nativeRoute[0], nativeClient);
assert.equal(nativeRoute[1], nativeRequest);
assert.equal(nativeRoute[2], nativeOptions);
assert.deepEqual(reads, []);

credentials.CC_KIMI_AUTH_TOKEN = " first-token ";
assert.throws(
  () => api.route(nativeClient, { model: "moonshot:kimi-k2.7-code" }),
  /not a constructor/,
);
assert.equal(constructed.length, 0);
SDK = FakeSDK;

const request = Object.freeze({
  model: "moonshot:kimi-k2.7-code",
  messages: Object.freeze([]),
  fallback_credit_token: "external-credit",
});
const signal = Object.freeze({ external: true });
const options = Object.freeze({
  signal,
  timeout: 77,
  headers: Object.freeze({
    authorization: "bearer-secret",
    "X-API-KEY": "api-secret",
    Cookie: "cookie-secret",
    traceparent: "00-trace",
    TraceState: "state",
    "x-safe-looking-but-not-allowlisted": "drop-me",
  }),
});
const [firstClient, firstOutbound, firstOptions] = api.route(nativeClient, request, options);
assert.equal(firstOutbound.model, "kimi-k2.7-code");
assert.ok(!("fallback_credit_token" in firstOutbound));
assert.equal(request.fallback_credit_token, "external-credit");
assert.equal(firstOptions.signal, signal);
assert.equal(firstOptions.timeout, 77);
assert.equal(
  JSON.stringify(firstOptions.headers),
  JSON.stringify({
    traceparent: "00-trace",
    TraceState: "state",
  }),
);
assert.equal(options.headers.authorization, "bearer-secret");
assert.equal(firstClient.options.maxRetries, 0);
assert.equal(firstClient.options.timeout, 1234);
assert.notEqual(firstClient.options.fetchOptions, fetchOptions);
assert.equal(firstClient.options.fetchOptions.dispatcher, "proxy-and-ca");
assert.ok(!("headers" in firstClient.options.fetchOptions));
assert.equal(firstClient.options.fetch, fetchOverride);
assert.equal(
  JSON.stringify(firstClient._options.defaultHeaders),
  JSON.stringify({ "User-Agent": "KimiCLI/1.5" }),
);
assert.equal(firstClient.options.authToken, "first-token");
assert.equal(firstClient.options.baseURL, "https://api.kimi.com/coding");
const [legacyClient, legacyOutbound] = api.route(nativeClient, { model: "kimi:kimi-k2.7-code" });
assert.equal(legacyClient, firstClient);
assert.equal(legacyOutbound.model, "kimi-k2.7-code");

const [, repeatedOutbound] = api.route(nativeClient, request, options);
assert.notEqual(repeatedOutbound, firstOutbound);
assert.equal(constructed.length, 1);
await Promise.all(
  Array.from({ length: 20 }, async () =>
    api.route(nativeClient, { model: "moonshot:kimi-k2.7-code" }),
  ),
);
assert.equal(constructed.length, 1);

credentials.CC_KIMI_AUTH_TOKEN = "second-token";
const [secondClient] = api.route(nativeClient, request, options);
assert.notEqual(secondClient, firstClient);
assert.equal(secondClient.options.authToken, "second-token");
assert.equal(constructed.length, 2);
assert.equal(api.clients.size, 1);
assert.equal(api.clients.get("moonshot").client, secondClient);

const changedFetchOptions = Object.freeze({
  dispatcher: "rotated-proxy-and-ca",
});
const changedNativeClient = Object.freeze({
  ...nativeClient,
  fetchOptions: changedFetchOptions,
});
const [transportClient] = api.route(changedNativeClient, request, options);
assert.notEqual(transportClient, secondClient);
assert.notEqual(transportClient.options.fetchOptions, changedFetchOptions);
assert.equal(transportClient.options.fetchOptions.dispatcher, "rotated-proxy-and-ca");
assert.equal(constructed.length, 3);
const changedTimeoutClient = Object.freeze({
  ...changedNativeClient,
  timeout: 4321,
});
const [timeoutClient] = api.route(changedTimeoutClient, request, options);
assert.notEqual(timeoutClient, transportClient);
assert.equal(timeoutClient.options.timeout, 4321);
assert.equal(constructed.length, 4);

credentials.CC_KIMI_AUTH_TOKEN = "   ";
let missingError;
try {
  api.route(nativeClient, request, options);
} catch (error) {
  missingError = error;
}
assert.equal(missingError.code, "EPROVIDERCREDENTIAL");
assert.match(missingError.message, /CC_KIMI_AUTH_TOKEN/);
assert.ok(!missingError.message.includes("first-token"));
assert.ok(!missingError.message.includes("second-token"));
assert.equal(api.clients.has("moonshot"), false);
assert.equal(constructed.length, 4);

for (const model of [
  "arn:aws:bedrock:us-east-1:model/example",
  "gateway:model",
  "custom:model",
  "future-unqualified-model",
]) {
  const unknown = Object.freeze({ model });
  assert.equal(api.route(nativeClient, unknown)[1], unknown);
}
for (const model of [
  "moonshot:",
  "kimi:",
  "kimi:unknown",
  "Moonshot:kimi-k2.7-code",
  "MOONSHOT:kimi-k2.7-code",
  "zai:",
  "minimax:",
  "openai:",
  "zai:unknown",
  "openai:unknown",
  "Kimi:kimi-k2.7-code",
  "KIMI:kimi-k2.7-code",
  "kim:kimi-k2.7-code",
  "gateway:kimi-k2.7-code",
  "kimi-k2.7-code",
  "glm-5.2",
  "minimax:MiniMax-M2.7-typo",
]) {
  assert.throws(
    () => api.route(nativeClient, { model }),
    (error) => error.code === "EPROVIDERMODEL",
    model,
  );
}
for (const model of [
  "opus",
  "claude-opus-4-8",
  "fable",
  "claude-fable-5",
  "future-native-model",
  "gateway:model",
]) {
  assert.equal(api.modelProvider(model), "anthropic", model);
}
for (const [model, provider] of [
  ["moonshot:kimi-k2.7-code", "moonshot"],
  ["kimi-k2.7-code", "moonshot"],
  ["zai:glm-5.2", "zai"],
  ["glm-5.2", "zai"],
  ["zai:glm-5-turbo", "zai"],
  ["glm-5-turbo", "zai"],
  ["minimax:MiniMax-M2.7", "minimax"],
  ["MiniMax-M2.7", "minimax"],
  ["openai:gpt-6-astra", "openai"],
  ["gpt-6-astra", "openai"],
  ["openai:gpt-5.6-sol", "openai"],
  ["gpt-5.6-sol", "openai"],
  ["openai:gpt-5.6-terra", "openai"],
  ["gpt-5.6-terra", "openai"],
  ["openai:gpt-5.6-luna", "openai"],
  ["gpt-5.6-luna", "openai"],
]) {
  assert.equal(api.modelProvider(model), provider, model);
}

credentials.CC_OPENAI_PROXY_EFFECTIVE_URL = "http://127.0.0.1:17780";
for (const [unavailable, missing] of [
  [{}, "CC_OPENAI_PROXY_AUTH_TOKEN"],
  [{ CC_OPENAI_PROXY_AUTH_TOKEN: "openai-token" }, "CC_OPENAI_AVAILABLE"],
  [{ CC_OPENAI_AVAILABLE: "1" }, "CC_OPENAI_PROXY_AUTH_TOKEN"],
  [
    {
      CC_OPENAI_PROXY_AUTH_TOKEN: "   ",
      CC_OPENAI_AVAILABLE: "1",
    },
    "CC_OPENAI_PROXY_AUTH_TOKEN",
  ],
]) {
  Object.assign(credentials, unavailable);
  assert.throws(
    () => api.route(nativeClient, { model: "openai:gpt-5.6-sol" }),
    (error) => error.code === "EPROVIDERCREDENTIAL" && error.message.includes(missing),
  );
  delete credentials.CC_OPENAI_PROXY_AUTH_TOKEN;
  delete credentials.CC_OPENAI_AVAILABLE;
}
credentials.CC_OPENAI_PROXY_AUTH_TOKEN = " openai-runtime-token ";
credentials.CC_OPENAI_AVAILABLE = "1";
const [openAIClient, openAIRequest, openAIOptions] = api.route(
  nativeClient,
  Object.freeze({
    model: "openai:gpt-5.6-sol",
    messages: [],
    fallback_credit_token: "never-forward",
  }),
  options,
);
assert.equal(openAIRequest.model, "gpt-5.6-sol");
assert.ok(!("fallback_credit_token" in openAIRequest));
assert.equal(openAIClient.options.baseURL, "http://127.0.0.1:17780");
assert.equal(openAIClient.options.authToken, "openai-runtime-token");
credentials.CC_OPENAI_PROXY_EFFECTIVE_URL = "https://proxy.example.test";
const [customOpenAIClient] = api.route(nativeClient, {
  model: "openai:gpt-5.6-sol",
});
assert.notEqual(customOpenAIClient, openAIClient);
assert.equal(customOpenAIClient.options.baseURL, "https://proxy.example.test");
credentials.CC_OPENAI_PROXY_EFFECTIVE_URL = "http://127.0.0.1:17780";
credentials.CC_OPENAI_AVAILABLE = "0";
assert.throws(
  () => api.route(nativeClient, { model: "openai:gpt-5.6-sol" }),
  (error) => error.code === "EPROVIDERCREDENTIAL",
);
assert.equal(api.clients.has("openai"), false);
credentials.CC_OPENAI_AVAILABLE = "1";
assert.equal(JSON.stringify(openAIClient._options.defaultHeaders), "{}");
assert.equal(
  JSON.stringify(openAIOptions.headers),
  JSON.stringify({ traceparent: "00-trace", TraceState: "state" }),
);
assert.ok(!reads.includes(undefined));

credentials.CC_ZAI_AUTH_TOKEN = "zai-token";
const [countClient, countRequest, countOptions] = api.route(
  nativeClient,
  Object.freeze({ model: "zai:glm-5.2", messages: [] }),
  Object.freeze({ headers: { Authorization: "never-forward" } }),
);
const countResult = await countClient.beta.messages.countTokens(countRequest, countOptions);
assert.equal(countResult.input_tokens, 17);
assert.equal(countClient.calls[0][0], "countTokens");
assert.equal(countClient.calls[0][1], countRequest);
assert.equal(Object.keys(countClient.calls[0][2]).length, 0);
assert.notEqual(countClient, nativeClient);
assert.equal(api.inputTokens("zai:glm-5.2", countResult), 17);
assert.throws(
  () => api.inputTokens("zai:glm-5.2", {}),
  (error) =>
    error.code === "EPROVIDERINCOMPATIBLE" && error.message.includes("numeric input_tokens"),
);
assert.equal(api.inputTokens("claude-sonnet-4-6", {}), undefined);

const allText = JSON.stringify({ constructed, missing: missingError.message });
for (const secret of [
  "native-secret",
  "bearer-secret",
  "api-secret",
  "cookie-secret",
  "custom-secret",
  "custom-key",
]) {
  assert.ok(!firstClient._options.defaultHeaders[secret]);
}
assert.ok(!missingError.message.includes("token".repeat(10)));
assert.ok(reads.length >= 25);
assert.ok(allText.includes("second-token"));

const deepFreeze = (value) => {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
};
const initialTool = {
  name: "mcp__docs__batch",
  description: "Create a document",
  input_schema: { type: "object", properties: { title: { type: "string" } } },
  defer_loading: true,
  eager_input_streaming: true,
};
const addedTool = {
  name: "mcp__docs__guide",
  description: "Read a guide",
  input_schema: { type: "object", properties: {} },
};
const updatedTool = { ...addedTool, description: "Read an updated guide" };
const reference = (name) => ({ type: "tool_addition", tool: { type: "tool_reference", name } });
const addition = (tool) => ({ type: "tool_addition", tool });
const unchangedMessage = { role: "user", content: "Read the guide" };
const textBlock = {
  type: "text",
  text: "Tools are available",
  cache_control: { type: "ephemeral" },
};
const toolHistory = deepFreeze({
  tools: [initialTool],
  system: [{ type: "text", text: "Use the document tools" }],
  messages: [
    unchangedMessage,
    { role: "system", content: [reference(initialTool.name), reference(initialTool.name)] },
    { role: "system", content: [reference(addedTool.name)] },
    { role: "system", content: [addition(addedTool), textBlock], extra: "preserve" },
    { role: "system", content: [addition(updatedTool), reference(addedTool.name)] },
    { role: "assistant", content: [{ type: "text", text: "Ready" }] },
    { role: "user", content: [{ type: "tool_result", tool_use_id: "call_1", content: "ok" }] },
  ],
});
const originalHistory = normalize(toolHistory);
credentials.CC_KIMI_AUTH_TOKEN = "normalization-token";
credentials.CC_MINIMAX_AUTH_TOKEN = "minimax-token";
for (const model of [
  "moonshot:kimi-k3",
  "moonshot:kimi-k2.7-code",
  "kimi:kimi-k3",
  "kimi:kimi-k2.7-code",
]) {
  const input = deepFreeze({ ...toolHistory, model });
  const [client, outbound] = api.route(nativeClient, input);
  assert.deepEqual(normalize(outbound.tools), [initialTool, updatedTool]);
  assert.equal(outbound.tools[0], initialTool, "references must not replace full definitions");
  assert.equal(outbound.tools[1], updatedTool, "the last full addition wins");
  assert.deepEqual(normalize(outbound.messages), [
    unchangedMessage,
    { role: "system", content: [textBlock], extra: "preserve" },
    toolHistory.messages[5],
    toolHistory.messages[6],
  ]);
  assert.equal(outbound.messages[0], unchangedMessage);
  assert.equal(outbound.messages[1].content[0], textBlock);
  assert.equal(outbound.system, toolHistory.system);
  assert.deepEqual(normalize(toolHistory), originalHistory, "the caller's history must not change");
  const [, repeated] = api.route(nativeClient, { ...outbound, model });
  assert.deepEqual(normalize(repeated), normalize(outbound), "normalization must be idempotent");
  await client.beta.messages.create(outbound);
  await client.beta.messages.countTokens(outbound);
  assert.equal(client.calls.at(-2)[1], outbound);
  assert.equal(client.calls.at(-1)[1], outbound);

  const [, noTools] = api.route(nativeClient, deepFreeze({ model, messages: [unchangedMessage] }));
  assert.ok(!("tools" in noTools), "requests without additions must not gain a tools field");
  const [, addedOnly] = api.route(
    nativeClient,
    deepFreeze({
      model,
      messages: [
        { role: "system", content: [addition(addedTool), addition(updatedTool)] },
        unchangedMessage,
      ],
    }),
  );
  assert.deepEqual(normalize(addedOnly.tools), [updatedTool]);
  assert.deepEqual(normalize(addedOnly.messages), [unchangedMessage]);
  for (const block of [reference("missing_tool"), { type: "tool_addition" }]) {
    assert.throws(
      () =>
        api.route(
          nativeClient,
          deepFreeze({ model, messages: [{ role: "system", content: [block] }] }),
        ),
      (error) => error.code === "EPROVIDERINCOMPATIBLE",
      "unresolved additions must not silently lose tools",
    );
  }
}
for (const model of [
  "claude-sonnet-4-6",
  "zai:glm-5.3",
  "minimax:MiniMax-M3",
  "openai:gpt-6-astra",
]) {
  const input = deepFreeze({ ...toolHistory, model });
  const [, outbound] = api.route(nativeClient, input);
  assert.equal(outbound.messages, input.messages, model);
  assert.equal(outbound.tools, input.tools, model);
}

console.log("multi-provider SDK routing: ok");
