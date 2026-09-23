import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const helper = fs.readFileSync(process.argv[2], "utf8");
const env = {
  CLAUDE_CODE_OAUTH_TOKEN: "primary-secret",
  CLAUDE_CODE_OAUTH_TOKEN_1: "first-secret",
  CLAUDE_CODE_OAUTH_TOKEN_12: "second-secret",
  CLAUDE_CODE_OAUTH_TOKEN_2: " ",
  CLAUDE_CODE_OAUTH_TOKEN_0: "invalid",
  CLAUDE_CODE_OAUTH_TOKEN_01: "invalid",
};
const requests = [];
let fail = false;
let cancelled = false;
const transport = async (url, options) => {
  requests.push({ url, options });
  if (fail) return new Response('{"error":{"type":"authentication_error"}}', { status: 401 });
  const request = JSON.parse(options.body);
  if (url.endsWith("count_tokens")) {
    return Response.json({ input_tokens: 27 });
  }
  const message = {
    type: "message",
    model: request.model,
    content: [],
    usage: { input_tokens: 1 },
  };
  if (!request.stream) return Response.json(message, { headers: { "request-id": "test-request" } });
  const sse =
    "event: message_start\r\ndata: " +
    JSON.stringify({ type: "message_start", message }) +
    '\r\n\r\nevent: message_stop\ndata: {"type":"message_stop"}\n\n';
  const bytes = new TextEncoder().encode(sse);
  return new Response(
    new ReadableStream({
      start(controller) {
        for (let i = 0; i < bytes.length; i += 7) controller.enqueue(bytes.slice(i, i + 7));
        controller.close();
      },
      cancel() {
        cancelled = true;
      },
    }),
    { headers: { "content-type": "text/event-stream", "request-id": "stream-request" } },
  );
};
class SDK {
  constructor(options) {
    this._options = options;
    this.options = options;
    this.beta = {
      messages: {
        create: (request, requestOptions) => this.send("/v1/messages", request, requestOptions),
        countTokens: (request) => this.send("/v1/messages/count_tokens", request),
      },
    };
  }
  async send(path, request, options = {}) {
    const headers = {
      ...this.options.defaultHeaders,
      "anthropic-beta": request.betas?.join(",") ?? "native-beta",
      Authorization: "Bearer " + this.options.authToken,
      ...options.headers,
    };
    return this.options.fetch(this.options.baseURL + path, {
      method: "POST",
      headers,
      body: JSON.stringify(request),
      signal: options.signal,
    });
  }
}
const context = vm.createContext({
  process: { env },
  SDK,
  Headers,
  Response,
  TransformStream,
  TextDecoder,
  TextEncoder,
  fetch: transport,
});
vm.runInContext(
  helper +
    `
function _ccMultiProviderSDK(){return SDK}
globalThis.api={route:_ccMultiProviderRoute,info:_ccMultiProviderModelInfo,
 catalog:_ccMultiProviderCatalogInfo,picker:_ccMultiProviderPickerCatalog,
 allowed:_ccMultiProviderToolAllowed,provider:_ccMultiProviderModelProvider,
 preflight:_ccMultiProviderPreflight,fetch:_ccMultiProviderAnthropicFetch};`,
  context,
);
const api = context.api;
const native = {
  timeout: 1200,
  fetch: () => {
    throw Error("primary transport must not be used");
  },
  fetchOptions: { headers: { Authorization: "primary" } },
  _options: {
    defaultHeaders: {
      Authorization: "primary",
      "X-Api-Key": "primary",
      Cookie: "primary",
      "x-organization-id": "primary",
      "User-Agent": "claude-code/test",
      "x-app": "cli",
    },
  },
};
const one = "anthropic1:claude-fable-5-1";
const two = "anthropic12:claude-fable-5-1";
assert.deepEqual(
  Array.from(api.picker(), (row) => row.value),
  [one, "anthropic1:claude-haiku-4-5-20251001", two, "anthropic12:claude-haiku-4-5-20251001"],
);
assert.equal(api.info("claude-fable-5-1"), null);
assert.equal(api.provider(one), "anthropic1");
assert.equal(api.info("anthropic1:fable").wireModel, "claude-fable-5-1");
assert.equal(api.info("anthropic1:claude-haiku-4-5").wireModel, "claude-haiku-4-5-20251001");
assert.equal(api.catalog(one).contextWindow, 1000000);
assert.equal(api.catalog("anthropic1:claude-haiku-4-5[1m]").contextWindow, 1000000);
assert.equal(api.catalog(one).maxOutputTokens, 128000);
for (const model of [
  "anthropic0:fable",
  "Anthropic1:fable",
  "anthropic01:fable",
  "anthropic1:unknown",
  "anthropic1:",
]) {
  assert.throws(() => api.preflight(model), { code: "EPROVIDERMODEL" });
}
assert.throws(() => api.preflight("anthropic99:fable"), { code: "EPROVIDERCREDENTIAL" });
assert.equal(api.allowed(one, { name: "WebSearch" }), true);
assert.equal(api.allowed("zai:glm-5.3", { name: "WebSearch" }), false);
const original = {
  model: one,
  metadata: { user_id: "primary-account" },
  fallback_credit_token: "primary-credit",
  tools: [{ name: "WebSearch" }],
  betas: ["thinking-beta"],
  stream: false,
};
const options = {
  headers: {
    Authorization: "primary",
    "x-api-key": "primary",
    "x-organization-id": "primary",
    traceparent: "trace",
  },
  timeout: 42,
};
const [client, outbound, safe] = api.route(native, original, options);
assert.equal(client.options.authToken, "first-secret");
assert.equal(client.options.apiKey, null);
assert.equal(client.options.baseURL, "https://api.anthropic.com");
assert.equal(client.options.defaultHeaders["user-agent"], "claude-code/test");
assert.equal(client.options.defaultHeaders.Authorization, undefined);
assert.equal(client.options.fetchOptions.headers, undefined);
assert.equal(outbound.model, "claude-fable-5-1");
assert.equal(outbound.metadata, undefined);
assert.equal(outbound.fallback_credit_token, undefined);
assert.equal(original.metadata.user_id, "primary-account");
assert.equal(safe.headers.Authorization, undefined);
assert.equal(safe.timeout, 42);
assert.equal(api.route(native, original)[0], client);
const [other, otherBody, otherOptions] = api.route(native, { ...original, model: two }, options);
assert.notEqual(client, other);
const responses = await Promise.all([
  client.beta.messages.create(outbound, safe),
  other.beta.messages.create(otherBody, otherOptions),
]);
assert.equal((await responses[0].json()).model, one);
assert.equal((await responses[1].json()).model, two);
for (const [index, token] of ["first-secret", "second-secret"].entries()) {
  const request = requests[index];
  assert.equal(request.options.headers.get("authorization"), "Bearer " + token);
  assert.equal(request.options.headers.get("x-api-key"), null);
  assert.equal(request.options.headers.get("x-organization-id"), null);
  assert.equal(request.options.headers.get("anthropic-beta"), "thinking-beta,oauth-2025-04-20");
}
const count = await client.beta.messages.countTokens({ model: outbound.model });
assert.equal((await count.json()).input_tokens, 27);
const stream = await client.beta.messages.create({ ...outbound, stream: true }, safe);
assert.match(await stream.text(), /"model":"anthropic1:claude-fable-5-1"/);
assert.equal(stream.headers.get("request-id"), "stream-request");
fail = true;
assert.equal((await client.beta.messages.create(outbound, safe)).status, 401);
fail = false;
env.CLAUDE_CODE_OAUTH_TOKEN_1 = "rotated-secret";
const rotated = api.route(native, original)[0];
assert.notEqual(rotated, client);
assert.equal(rotated.options.authToken, "rotated-secret");
delete env.CLAUDE_CODE_OAUTH_TOKEN_1;
assert.throws(() => api.route(native, original), { code: "EPROVIDERCREDENTIAL" });
assert.equal(api.picker().length, 2);
assert.equal(env.CLAUDE_CODE_OAUTH_TOKEN, "primary-secret");
assert.equal(api.route(native, { model: "claude-fable-5-1" })[0], native);
const hanging = api.fetch(
  "anthropic12",
  async () =>
    new Response(
      new ReadableStream({
        cancel() {
          cancelled = true;
        },
      }),
      { headers: { "content-type": "text/event-stream" } },
    ),
);
const pending = await hanging("https://api.anthropic.com/v1/messages", {});
await pending.body.cancel();
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(cancelled, true);
