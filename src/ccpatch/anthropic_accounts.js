const _ccMultiProviderAnthropicCatalog = { models: [], aliases: {} };
function _ccMultiProviderAnthropicAccount(model) {
  if (typeof model !== "string") return null;
  const match = /^(anthropic[1-9][0-9]*):(.*)$/.exec(model);
  return match ? { provider: match[1], model: match[2] } : null;
}
function _ccMultiProviderAnthropicDefinition(provider) {
  return {
    baseURL: "https://api.anthropic.com",
    tokenEnv: "CLAUDE_CODE_OAUTH_TOKEN_" + provider.slice(9),
    defaultHeaders: {},
    oauth: true,
  };
}
function _ccMultiProviderAnthropicModel(model) {
  const account = _ccMultiProviderAnthropicAccount(model);
  if (!account) return null;
  const bare = account.model.replace(/\[1m\]$/i, "");
  const aliasName = bare === "best" ? _ccMultiProviderAnthropicCatalog.best : bare;
  const alias = _ccMultiProviderAnthropicCatalog.aliases[aliasName];
  const id = alias?.per_provider?.first_party ?? alias?.default ?? bare;
  const entry = _ccMultiProviderAnthropicCatalog.models.find(
    (row) => row.id === id || row.provider_ids.first_party === id,
  );
  if (
    !entry ||
    (bare !== account.model && !entry.context?.supports_1m_suffix && !entry.context?.native_1m)
  ) {
    throw _ccMultiProviderModelError("Unknown qualified model: " + model);
  }
  return {
    provider: account.provider,
    wireModel: entry.provider_ids.first_party,
    definition: _ccMultiProviderAnthropicDefinition(account.provider),
    entry,
    extended: bare !== account.model,
  };
}
function _ccMultiProviderAnthropicInfo(model) {
  const info = _ccMultiProviderAnthropicModel(model);
  if (!info) return null;
  return {
    value: model,
    label: info.entry.display_name,
    description: "Anthropic OAuth account " + info.provider.slice(9),
    attributionDomain: "anthropic.com",
    contextWindow: info.extended ? 1000000 : (info.entry.context?.window ?? 200000),
    maxOutputTokens: info.entry.max_output_tokens?.upper ?? 32000,
  };
}
function _ccMultiProviderAnthropicPicker() {
  return Object.keys(process.env)
    .filter((key) => /^CLAUDE_CODE_OAUTH_TOKEN_[1-9][0-9]*$/.test(key) && process.env[key]?.trim())
    .sort((a, b) => a.length - b.length || a.localeCompare(b))
    .flatMap((key) => {
      const provider = "anthropic" + key.slice("CLAUDE_CODE_OAUTH_TOKEN_".length);
      return _ccMultiProviderAnthropicCatalog.models.map((entry) => ({
        ..._ccMultiProviderAnthropicInfo(provider + ":" + entry.provider_ids.first_party),
        label: entry.display_name + " (" + provider + ")",
      }));
    });
}
function _ccMultiProviderAnthropicIdentifiers() {
  const providers = new Set(
    _ccMultiProviderAnthropicPicker().map((row) => row.value.split(":")[0]),
  );
  const models = _ccMultiProviderAnthropicCatalog.models.flatMap((entry) => {
    const names = [entry.id, entry.provider_ids.first_party];
    if (entry.context?.supports_1m_suffix || entry.context?.native_1m) {
      names.push(...names.map((name) => name + "[1m]"));
    }
    return names;
  });
  models.push(...Object.keys(_ccMultiProviderAnthropicCatalog.aliases));
  if (_ccMultiProviderAnthropicCatalog.best) models.push("best");
  return [...providers].flatMap((provider) => models.map((model) => provider + ":" + model));
}
function _ccMultiProviderAnthropicHeaders(headers) {
  const safe = {};
  const allowed = new Set([
    "user-agent",
    "x-app",
    "anthropic-version",
    "anthropic-beta",
    "x-client-app",
    "x-client-request-id",
    "x-claude-code-session-id",
    "x-claude-code-agent-id",
    "x-claude-code-parent-agent-id",
    "traceparent",
    "tracestate",
    "baggage",
  ]);
  for (const [name, value] of Object.entries(headers ?? {})) {
    if (allowed.has(name.toLowerCase())) safe[name.toLowerCase()] = value;
  }
  safe["x-app"] ??= "cli";
  return safe;
}
function _ccMultiProviderAnthropicRequest(request) {
  const outbound = { ...request };
  delete outbound.metadata;
  delete outbound.fallback_credit_token;
  delete outbound.thread_id;
  delete outbound.parent_message_id;
  return outbound;
}
function _ccMultiProviderAnthropicResponse(value, provider) {
  if (value?.type === "message" && typeof value.model === "string") {
    return { ...value, model: provider + ":" + value.model };
  }
  if (value?.type === "message_start" && value.message) {
    return { ...value, message: _ccMultiProviderAnthropicResponse(value.message, provider) };
  }
  return value;
}
function _ccMultiProviderAnthropicFetch(provider, transport = globalThis.fetch) {
  return async (url, options) => {
    const requestHeaders = new Headers(options?.headers);
    requestHeaders.set(
      "anthropic-beta",
      [
        ...new Set([
          ...(requestHeaders.get("anthropic-beta") ?? "").split(",").filter(Boolean),
          "oauth-2025-04-20",
        ]),
      ].join(","),
    );
    const response = await transport(url, { ...options, headers: requestHeaders });
    if (!response.ok || !response.body) return response;
    const headers = new Headers(response.headers);
    headers.delete("content-length");
    headers.delete("content-encoding");
    const type = headers.get("content-type") ?? "";
    if (type.includes("application/json")) {
      return new Response(
        JSON.stringify(_ccMultiProviderAnthropicResponse(await response.json(), provider)),
        {
          status: response.status,
          statusText: response.statusText,
          headers,
        },
      );
    }
    if (!type.includes("text/event-stream")) return response;
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    let pending = "";
    const line = (text) => {
      if (!text.startsWith("data:")) return text;
      try {
        return (
          "data: " +
          JSON.stringify(_ccMultiProviderAnthropicResponse(JSON.parse(text.slice(5)), provider))
        );
      } catch {
        return text;
      }
    };
    const body = response.body.pipeThrough(
      new TransformStream({
        transform(chunk, controller) {
          pending += decoder.decode(chunk, { stream: true });
          let end;
          while ((end = pending.indexOf("\n")) !== -1) {
            controller.enqueue(encoder.encode(line(pending.slice(0, end)) + "\n"));
            pending = pending.slice(end + 1);
          }
        },
        flush(controller) {
          pending += decoder.decode();
          if (pending) controller.enqueue(encoder.encode(line(pending)));
        },
      }),
    );
    return new Response(body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  };
}
