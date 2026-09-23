#!/usr/bin/env node
import { createServer } from "node:http";
import { readdirSync, realpathSync } from "node:fs";
import { readFile, rename, writeFile } from "node:fs/promises";
import { randomUUID, timingSafeEqual } from "node:crypto";
import { homedir } from "node:os";
import { basename, delimiter, dirname, join } from "node:path";
import { isMainThread, parentPort, Worker, workerData } from "node:worker_threads";

import {
  convertResponsesMessages,
  convertResponsesTools,
} from "@earendil-works/pi-ai/api/openai-responses-shared";
import { get_encoding as getEncoding } from "tiktoken";

import {
  classifyStreamError,
  createAccountPool,
  parseLimitErrorBody,
  parsePlanCapacity,
  rateLimitHeaders,
} from "./codex-accounts.js";
import { loadOrCreateProxyToken, proxyAuthDiagnostic, resolveProxyAuthPath } from "./proxy-auth.js";

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 17780;
const DEFAULT_PROVIDER = "openai-codex";
const DEFAULT_MODEL = "gpt-5.6-sol";
const DEFAULT_SONNET_MODEL = "gpt-5.6-terra";
const DEFAULT_HAIKU_MODEL = "gpt-5.6-luna";
const MAX_BODY_BYTES = 64 * 1024 * 1024;
const TOOL_ID_PREFIX = "ccpatch_tc1_";
const CODEX_TOOL_CALL_PROVIDERS = new Set(["openai", "openai-codex", "opencode"]);
const RESPONSE_ITEM_OVERHEAD_TOKENS = 6;
const IMAGE_OVERHEAD_TOKENS = 2048;
const ENCRYPTED_REASONING_OVERHEAD_TOKENS = 64;
const TOKENIZER_CHUNK_CODE_UNITS = 4 * 1024;
const TOKENIZER_ENCODING_BY_MODEL = Object.freeze({
  // OpenAI maps GPT-5 to o200k_base in tiktoken. Apply that encoding to newer
  // pi GPT-5 and GPT-6 catalog entries as provisional policy until OpenAI
  // publishes model-specific mappings for these exact IDs.
  "gpt-5.3-codex-spark": "o200k_base",
  "gpt-5.5": "o200k_base",
  "gpt-5.6-luna": "o200k_base",
  "gpt-5.6-sol": "o200k_base",
  "gpt-5.6-terra": "o200k_base",
  "gpt-6-astra": "o200k_base",
  "gpt-6-luna": "o200k_base",
  "gpt-6-sol": "o200k_base",
});

let accountPoolPromise;
let o200kEncoding;
let authProbePromise;
const processSessionId = `cc-openai-${randomUUID()}`;

function usage() {
  return `usage: cc-openai-proxy [--host HOST] [--port PORT] [--auth-token-file PATH]

Environment:
  CC_OPENAI_MODEL            Override all requested models
  CC_OPENAI_OPUS_MODEL       Model for Anthropic opus and fable requests (${DEFAULT_MODEL})
  CC_OPENAI_SONNET_MODEL     Model for Anthropic sonnet requests (${DEFAULT_SONNET_MODEL})
  CC_OPENAI_HAIKU_MODEL      Model for Anthropic haiku requests (${DEFAULT_HAIKU_MODEL})
  CC_OPENAI_AUTH_FILE        Auth file (default ~/.pi/agent/auth.json); sibling auth.*.json files are added
  CC_OPENAI_AUTH_FILES       Explicit auth files (one Codex account each), separated by "${delimiter}"
  CC_OPENAI_PLAN_CAPACITY    Relative plan capacities, e.g. "plus=1,pro=20"
  CC_OPENAI_USAGE_TTL_MS     Age before a Codex usage snapshot is refreshed (300000)
  CC_OPENAI_USAGE_HEADERS    Set to 0 to omit usage headers on successful responses
  CC_OPENAI_PROXY_AUTH_FILE  Proxy bearer file (platform default when unset)
  CC_OPENAI_TRANSPORT        pi-ai transport: auto, sse, websocket, websocket-cached
  CC_OPENAI_CACHE_RETENTION  pi-ai cache retention: short, long, none
`;
}

function parseArgs(argv) {
  const config = {
    host: process.env.CC_OPENAI_PROXY_HOST || DEFAULT_HOST,
    port: Number.parseInt(process.env.CC_OPENAI_PROXY_PORT || String(DEFAULT_PORT), 10),
    authTokenFile: process.env.CC_OPENAI_PROXY_AUTH_FILE,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--host") {
      config.host = argv[++i];
    } else if (arg === "--port") {
      config.port = Number.parseInt(argv[++i], 10);
    } else if (arg === "--auth-token-file") {
      config.authTokenFile = argv[++i];
      if (!config.authTokenFile) throw new Error("auth token file must not be empty");
    } else if (arg === "--help" || arg === "-h") {
      process.stdout.write(usage());
      process.exit(0);
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }

  if (!config.host) throw new Error("host must not be empty");
  if (!Number.isInteger(config.port) || config.port <= 0 || config.port > 65535) {
    throw new Error(`invalid port: ${config.port}`);
  }
  if (config.authTokenFile === "") throw new Error("auth token file must not be empty");
  config.authTokenFile = resolveProxyAuthPath(config.authTokenFile);
  return config;
}

// Register only openai-codex to use its generated catalog and OAuth support.
// This provider gets credentials from a CredentialStore, so back one store
// with each pi auth file. pi-ai refreshes and persists OAuth tokens through
// the store's modify() operation. One Models instance per auth file gives one
// Codex account per file with no shared credential state.
async function createAccountModels(path) {
  const { createModels } = await import("@earendil-works/pi-ai");
  const { openaiCodexProvider } = await import("@earendil-works/pi-ai/providers/openai-codex");
  const models = createModels({ credentials: authFileCredentialStore(path) });
  models.setProvider(openaiCodexProvider());
  return models;
}

function logEvent(fields) {
  process.stderr.write(`${JSON.stringify({ origin: "cc-openai-proxy", ...fields })}\n`);
}

async function loadAccountPool() {
  accountPoolPromise ??= (async () => {
    const paths = explicitToken() ? [authPath()] : authFilePaths();
    const accounts = [];
    for (const path of paths) {
      accounts.push({
        label: basename(path),
        models: await createAccountModels(path),
        readCredential: async () => toCredential((await readAuthData(path))?.[DEFAULT_PROVIDER]),
      });
    }
    const ttl = Number.parseInt(process.env.CC_OPENAI_USAGE_TTL_MS || "", 10);
    return createAccountPool({
      accounts,
      defaultModelId: DEFAULT_MODEL,
      capacityTable: parsePlanCapacity(process.env.CC_OPENAI_PLAN_CAPACITY),
      ...(Number.isInteger(ttl) && ttl >= 0 ? { usageTtlMs: ttl } : {}),
      log: logEvent,
    });
  })();
  return accountPoolPromise;
}

// Models facade over the pool: the catalog of the first account, and auth
// resolution that succeeds when any account has usable credentials.
async function loadModels() {
  const pool = await loadAccountPool();
  const primary = pool.primaryModels();
  return {
    getModel: (provider, id) => primary.getModel(provider, id),
    getModels: (provider) => primary.getModels(provider),
    async getAuth(model) {
      let failure;
      for (const account of pool.accounts) {
        try {
          const result = await account.models.getAuth(model);
          if (result?.auth?.apiKey) return result;
        } catch (error) {
          failure = error;
        }
      }
      if (failure) throw failure;
      return undefined;
    },
  };
}

function authPath() {
  return (
    process.env.CC_OPENAI_AUTH_FILE ||
    process.env.PI_AUTH_FILE ||
    join(homedir(), ".pi", "agent", "auth.json")
  );
}

const SIBLING_AUTH_FILE = /^auth\.[^./][^/]*\.json$/;

// Without an explicit list, every auth.*.json next to the primary auth file
// is one more account. Refresh temp files (auth.json.<pid>.<ts>.tmp) and
// backups do not match the pattern. Symlinks resolve to their targets because
// a token refresh replaces the file by rename, which would break the link.
function authFilePaths() {
  const listed = (process.env.CC_OPENAI_AUTH_FILES || "")
    .split(delimiter)
    .map((entry) => entry.trim())
    .filter(Boolean);
  if (listed.length > 0) return [...new Set(listed)];
  const primary = authPath();
  const directory = dirname(primary);
  let siblings = [];
  try {
    siblings = readdirSync(directory)
      .filter((name) => SIBLING_AUTH_FILE.test(name))
      .sort()
      .map((name) => {
        const path = join(directory, name);
        try {
          return realpathSync(path);
        } catch {
          return path;
        }
      });
  } catch {
    // A missing directory means only the primary path, which reports its
    // own absence when credentials are read.
  }
  return [...new Set([primary, ...siblings])];
}

async function readAuthData(path) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return undefined;
    throw new Error(
      `failed to read pi auth file ${path}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

async function writeAuthFile(path, data) {
  const tmp = `${path}.${process.pid}.${Date.now()}.tmp`;
  await writeFile(tmp, `${JSON.stringify(data, null, 2)}\n`, { mode: 0o600 });
  await rename(tmp, path);
}

// The codex provider declares only oauth auth, so a directly supplied bearer
// (env var, or an api_key entry in auth.json) is served as an oauth
// credential with a far-future expiry: pi-ai's refresh path stays idle and
// toAuth() forwards the token as-is.
const STATIC_TOKEN_EXPIRES = 4102444800000; // 2100-01-01

function staticCredential(token) {
  return {
    type: "oauth",
    access: token,
    refresh: "",
    expires: STATIC_TOKEN_EXPIRES,
  };
}

function explicitToken() {
  return (
    process.env.CC_OPENAI_CODEX_TOKEN ||
    process.env.OPENAI_CODEX_TOKEN ||
    process.env.OPENAI_CODEX_API_KEY ||
    ""
  );
}

function toCredential(entry) {
  if (!entry || typeof entry !== "object") return undefined;
  if (entry.type === "api_key") {
    return typeof entry.key === "string" && entry.key ? staticCredential(entry.key) : undefined;
  }
  return entry;
}

// CredentialStore over one pi auth.json. pi-ai runs oauth refresh inside
// modify(), so the rotated token is persisted for the pi CLI too. Writes are
// serialized through a promise chain per the CredentialStore contract.
function authFileCredentialStore(path) {
  let credentialWriteChain = Promise.resolve();
  const chained = (task) => {
    const result = credentialWriteChain.then(task);
    credentialWriteChain = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  };

  return {
    async read(providerId) {
      if (providerId === DEFAULT_PROVIDER && explicitToken()) {
        return staticCredential(explicitToken());
      }
      return toCredential((await readAuthData(path))?.[providerId]);
    },
    async list() {
      const data = (await readAuthData(path)) ?? {};
      return Object.entries(data)
        .filter(([, entry]) => entry?.type)
        .map(([providerId, entry]) => ({ providerId, type: entry.type }));
    },
    modify(providerId, fn) {
      return chained(async () => {
        if (providerId === DEFAULT_PROVIDER && explicitToken()) {
          // Env-supplied tokens are read-only; never persist over them.
          await fn(staticCredential(explicitToken()));
          return staticCredential(explicitToken());
        }
        const data = (await readAuthData(path)) ?? {};
        const current = toCredential(data[providerId]);
        const next = await fn(current);
        if (next === undefined) return current;
        data[providerId] = next;
        await writeAuthFile(path, data);
        return next;
      });
    },
    delete(providerId) {
      return chained(async () => {
        const data = await readAuthData(path);
        if (!data || !(providerId in data)) return;
        delete data[providerId];
        await writeAuthFile(path, data);
      });
    },
  };
}

function missingCredentialsError() {
  const paths = explicitToken() ? [authPath()] : authFilePaths();
  return httpError(
    401,
    `missing ${DEFAULT_PROVIDER} credentials in ${paths.join(", ")}. Run pi /login for ChatGPT Plus/Pro first.`,
  );
}

function resolveModelId(requestedModel) {
  if (process.env.CC_OPENAI_MODEL) return process.env.CC_OPENAI_MODEL;
  const model = String(requestedModel || "").toLowerCase();
  if (model.includes("haiku")) {
    return process.env.CC_OPENAI_HAIKU_MODEL || DEFAULT_HAIKU_MODEL;
  }
  if (model.includes("opus") || model.includes("fable")) {
    return process.env.CC_OPENAI_OPUS_MODEL || process.env.CC_OPENAI_DEFAULT_MODEL || DEFAULT_MODEL;
  }
  if (model.includes("sonnet")) {
    return (
      process.env.CC_OPENAI_SONNET_MODEL ||
      process.env.CC_OPENAI_DEFAULT_MODEL ||
      DEFAULT_SONNET_MODEL
    );
  }
  return process.env.CC_OPENAI_DEFAULT_MODEL || requestedModel || DEFAULT_MODEL;
}

function thinkingToReasoning(thinking) {
  const forced = process.env.CC_OPENAI_REASONING;
  if (forced) return forced === "none" ? "off" : forced;
  if (!thinking || typeof thinking !== "object") return undefined;
  if (thinking.type === "disabled") return "off";
  if (thinking.type !== "enabled" && thinking.type !== "adaptive") return undefined;

  if (typeof thinking.effort === "string") {
    return thinking.effort === "none" ? "off" : thinking.effort;
  }

  const budget = Number(thinking.budget_tokens || 0);
  if (budget <= 0) return "low";
  if (budget <= 1024) return "low";
  if (budget <= 8192) return "medium";
  if (budget <= 32768) return "high";
  return "xhigh";
}

function normalizeSystemPrompt(system) {
  if (!system) return undefined;
  if (typeof system === "string") return system;
  if (!Array.isArray(system)) return JSON.stringify(system);
  return system
    .filter((block) => block?.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n\n");
}

/** @param {string} id @returns {string} */
function encodeToolId(id) {
  if (/^[a-zA-Z0-9_-]+$/.test(id) && !id.startsWith(TOOL_ID_PREFIX)) return id;
  // Escape the reserved prefix too, so distinct provider IDs stay distinct.
  return TOOL_ID_PREFIX + Buffer.from(id, "utf8").toString("base64url");
}

/** @param {string} id @returns {string} */
function decodeToolId(id) {
  if (!id.startsWith(TOOL_ID_PREFIX) || id === TOOL_ID_PREFIX) return id;
  const payload = id.slice(TOOL_ID_PREFIX.length);
  const decoded = Buffer.from(payload, "base64url").toString("utf8");
  // Decode one canonical envelope. Preserve legacy IDs and malformed prefixes.
  if (Buffer.from(decoded, "utf8").toString("base64url") !== payload) return id;
  return encodeToolId(decoded) === id ? decoded : id;
}

function anthropicToContext(request) {
  const toolNames = new Map();
  const messages = [];
  let timestamp = Date.now();

  for (const message of request.messages || []) {
    if (message?.role === "assistant") {
      const assistant = anthropicAssistantToPi(message, request.model, toolNames, timestamp++);
      if (assistant.content.length > 0) messages.push(assistant);
    } else if (message?.role === "user") {
      pushUserMessage(messages, message.content, toolNames, timestamp);
      timestamp += 1;
    }
  }

  const context = {
    systemPrompt: normalizeSystemPrompt(request.system),
    messages: pairToolResults(messages),
  };
  const tools = anthropicToolsToPi(request.tools);
  if (tools.length > 0) context.tools = tools;
  return context;
}

function pairToolResults(messages) {
  const paired = [];
  let pendingIds = new Set();
  let results = [];
  let userMessages = [];
  const flush = () => {
    // pi-ai inserts synthetic results when user text interrupts a tool turn.
    paired.push(...results, ...userMessages);
    results = [];
    userMessages = [];
  };

  for (const message of messages) {
    if (message.role === "assistant") {
      flush();
      paired.push(message);
      pendingIds = new Set(
        message.content.filter((block) => block.type === "toolCall").map((block) => block.id),
      );
    } else if (message.role === "toolResult") {
      if (pendingIds.delete(message.toolCallId)) {
        results.push(message);
      } else {
        // Keep unmatched output as context, not an invalid tool response.
        userMessages.push({
          role: "user",
          content: [
            {
              type: "text",
              text: `[Unmatched tool result: ${message.toolName} (${message.toolCallId})]`,
            },
            ...message.content,
          ],
          timestamp: message.timestamp,
        });
      }
    } else {
      userMessages.push(message);
    }
  }
  flush();
  return paired;
}

function pushUserMessage(messages, content, toolNames, timestamp) {
  if (typeof content === "string") {
    messages.push({ role: "user", content, timestamp });
    return;
  }
  if (!Array.isArray(content)) {
    messages.push({
      role: "user",
      content: stringifyUnknown(content),
      timestamp,
    });
    return;
  }

  let batch = [];
  const flushBatch = () => {
    if (batch.length === 0) return;
    messages.push({
      role: "user",
      content: collapseUserContent(batch),
      timestamp: timestamp++,
    });
    batch = [];
  };

  for (const block of content) {
    if (block?.type === "tool_result") {
      flushBatch();
      const toolCallId = decodeToolId(String(block.tool_use_id || ""));
      messages.push({
        role: "toolResult",
        toolCallId,
        toolName: toolNames.get(toolCallId) || "tool",
        content: anthropicToolResultContentToPi(block.content),
        isError: Boolean(block.is_error),
        timestamp: timestamp++,
      });
    } else {
      const converted = anthropicInputBlockToPi(block);
      if (converted) batch.push(converted);
    }
  }
  flushBatch();
}

function collapseUserContent(blocks) {
  if (blocks.every((block) => block.type === "text")) {
    return blocks.map((block) => block.text).join("\n");
  }
  return blocks;
}

function anthropicInputBlockToPi(block) {
  if (!block) return undefined;
  if (block.type === "text") {
    return { type: "text", text: String(block.text || "") };
  }
  if (block.type === "image" && block.source) {
    if (block.source.type === "base64") {
      return {
        type: "image",
        data: String(block.source.data || ""),
        mimeType: String(block.source.media_type || "image/png"),
      };
    }
    if (block.source.url) {
      return { type: "text", text: `[image: ${block.source.url}]` };
    }
  }
  return { type: "text", text: stringifyUnknown(block) };
}

function anthropicToolResultContentToPi(content) {
  if (typeof content === "string") return [{ type: "text", text: content }];
  if (!Array.isArray(content)) return [{ type: "text", text: stringifyUnknown(content) }];
  const blocks = content.map(anthropicInputBlockToPi).filter(Boolean);
  return blocks.length > 0 ? blocks : [{ type: "text", text: "" }];
}

function anthropicAssistantToPi(message, requestModel, toolNames, timestamp) {
  const content = [];
  const blocks =
    typeof message.content === "string"
      ? [{ type: "text", text: message.content }]
      : message.content || [];

  for (const block of blocks) {
    if (block?.type === "text") {
      content.push({ type: "text", text: String(block.text || "") });
    } else if (block?.type === "thinking") {
      content.push({
        type: "thinking",
        thinking: String(block.thinking || ""),
        ...(block.signature ? { thinkingSignature: String(block.signature) } : {}),
      });
    } else if (block?.type === "redacted_thinking") {
      content.push({
        type: "thinking",
        thinking: "[Reasoning redacted]",
        thinkingSignature: String(block.data || ""),
        redacted: true,
      });
    } else if (block?.type === "tool_use") {
      const id = decodeToolId(String(block.id || `toolu_${randomUUID().replaceAll("-", "")}`));
      const name = String(block.name || "tool");
      toolNames.set(id, name);
      content.push({
        type: "toolCall",
        id,
        name,
        arguments: isPlainObject(block.input) ? block.input : {},
      });
    }
  }

  return {
    role: "assistant",
    content,
    api: "anthropic-messages",
    provider: "anthropic",
    model: String(requestModel || "unknown"),
    usage: emptyUsage(),
    stopReason: "stop",
    timestamp,
  };
}

function anthropicToolsToPi(tools) {
  if (!Array.isArray(tools)) return [];
  return tools
    .filter((tool) => tool?.name)
    .map((tool) => ({
      name: String(tool.name),
      description: String(tool.description || ""),
      parameters: tool.input_schema || { type: "object", properties: {} },
    }));
}

function emptyUsage() {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function piContentToAnthropic(content) {
  return (content || []).map((block) => {
    if (block.type === "text") {
      return { type: "text", text: block.text || "" };
    }
    if (block.type === "thinking") {
      if (block.redacted) {
        return {
          type: "redacted_thinking",
          data: block.thinkingSignature || block.thinking || "",
        };
      }
      return {
        type: "thinking",
        thinking: block.thinking || "",
        ...(block.thinkingSignature ? { signature: block.thinkingSignature } : {}),
      };
    }
    return {
      type: "tool_use",
      id: encodeToolId(block.id || `toolu_${randomUUID().replaceAll("-", "")}`),
      name: block.name,
      input: isPlainObject(block.arguments) ? block.arguments : {},
    };
  });
}

function mapStopReason(reason) {
  if (reason === "length") return "max_tokens";
  if (reason === "toolUse") return "tool_use";
  if (reason === "aborted") return "stop_sequence";
  return "end_turn";
}

function anthropicUsage(usage = emptyUsage()) {
  return {
    input_tokens: usage.input || 0,
    output_tokens: usage.output || 0,
    cache_creation_input_tokens: usage.cacheWrite || 0,
    cache_read_input_tokens: usage.cacheRead || 0,
  };
}

function tokenizerForModel(modelId) {
  const encoding = TOKENIZER_ENCODING_BY_MODEL[modelId];
  if (!encoding) {
    throw httpError(400, `no local tokenizer mapping for ${modelId}`);
  }
  o200kEncoding ??= getEncoding(encoding);
  return o200kEncoding;
}

function assertTokenizerMappings(models) {
  const missing = models
    .filter((model) => !TOKENIZER_ENCODING_BY_MODEL[model.id])
    .map((model) => model.id);
  if (missing.length > 0) {
    throw new Error(`missing local tokenizer mappings: ${missing.join(", ")}`);
  }
}

function omitCanonicalInputImages(input) {
  let imageCount = 0;
  const omitImagesFromBlocks = (blocks) =>
    blocks.map((block) => {
      if (
        !isPlainObject(block) ||
        block.type !== "input_image" ||
        typeof block.image_url !== "string"
      ) {
        return block;
      }
      imageCount += 1;
      return { ...block, image_url: "[image payload counted separately]" };
    });
  const countableInput = input.map((item) => {
    if (!isPlainObject(item)) return item;
    if (Array.isArray(item.content)) {
      return { ...item, content: omitImagesFromBlocks(item.content) };
    }
    if (item.type === "function_call_output" && Array.isArray(item.output)) {
      return { ...item, output: omitImagesFromBlocks(item.output) };
    }
    return item;
  });
  return { countableInput, imageCount };
}

function separateEncryptedReasoning(context) {
  const encryptedReasoning = [];
  const messages = context.messages.map((message) => {
    if (message.role !== "assistant") return message;
    return {
      ...message,
      content: message.content.filter((block) => {
        if (block.type !== "thinking") return true;
        encryptedReasoning.push({
          thinking: block.thinking || "",
          signature: block.thinkingSignature || "",
          redacted: Boolean(block.redacted),
        });
        return false;
      }),
    };
  });
  return { context: { ...context, messages }, encryptedReasoning };
}

function canonicalResponsesPayload(model, request) {
  const { context, encryptedReasoning } = separateEncryptedReasoning(anthropicToContext(request));
  const supportsStrictMode = model.compat?.supportsStrictMode ?? true;
  const input = convertResponsesMessages(model, context, CODEX_TOOL_CALL_PROVIDERS, {
    includeSystemPrompt: false,
    toolOptions: { strict: null, supportsStrictMode },
  });
  const tools = context.tools?.length
    ? convertResponsesTools(context.tools, {
        strict: null,
        supportsStrictMode,
        supportsOpenAIGrammarTools: model.compat?.supportsOpenAIGrammarTools ?? false,
      })
    : undefined;
  return {
    payload: {
      instructions: context.systemPrompt || "You are a helpful assistant.",
      input,
      ...(tools ? { tools } : {}),
    },
    encryptedReasoning,
  };
}

function encodedLength(tokenizer, text) {
  let total = 0;
  let start = 0;
  while (start < text.length) {
    let end = Math.min(start + TOKENIZER_CHUNK_CODE_UNITS, text.length);
    if (
      end < text.length &&
      /[\uD800-\uDBFF]/u.test(text[end - 1]) &&
      /[\uDC00-\uDFFF]/u.test(text[end])
    ) {
      end -= 1;
    }
    total += tokenizer.encode(text.slice(start, end)).length;
    start = end;
  }
  return total;
}

// Bounded chunks limit individual tokenizer calls. Because token boundaries can
// cross chunks and Responses framing constants are unpublished, this is an estimate.
function estimateInputTokens(model, request) {
  const tokenizer = tokenizerForModel(model.id);
  const { payload, encryptedReasoning } = canonicalResponsesPayload(model, request);
  const { countableInput, imageCount } = omitCanonicalInputImages(payload.input);
  const countablePayload = { ...payload, input: countableInput };
  const serializedTokens = encodedLength(tokenizer, JSON.stringify(countablePayload));
  const structuralItems = payload.input.length + (payload.tools?.length || 0) + 1;
  const reasoningTokens = encryptedReasoning.reduce(
    (total, item) =>
      total + encodedLength(tokenizer, JSON.stringify(item)) + ENCRYPTED_REASONING_OVERHEAD_TOKENS,
    0,
  );
  return Math.max(
    1,
    serializedTokens +
      structuralItems * RESPONSE_ITEM_OVERHEAD_TOKENS +
      imageCount * IMAGE_OVERHEAD_TOKENS +
      reasoningTokens,
  );
}

function estimateInputTokensOffThread(model, request) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL(import.meta.url), {
      workerData: { operation: "estimateInputTokens", model, request },
    });
    worker.once("message", (message) => {
      if (message?.error) reject(new Error(message.error));
      else resolve(message.inputTokens);
    });
    worker.once("error", reject);
    worker.once("exit", (code) => {
      if (code !== 0) reject(new Error(`tokenizer worker exited with code ${code}`));
    });
  });
}

function countTokensResponse(inputTokens) {
  return {
    input_tokens: inputTokens,
    usage: {
      input_tokens: inputTokens,
      output_tokens: 0,
      cache_creation_input_tokens: 0,
      cache_read_input_tokens: 0,
    },
  };
}

function piMessageToAnthropic(message, fallbackModel) {
  return {
    id: message.responseId || `msg_${randomUUID().replaceAll("-", "")}`,
    type: "message",
    role: "assistant",
    model: message.responseModel || message.model || fallbackModel,
    content: piContentToAnthropic(message.content),
    stop_reason: mapStopReason(message.stopReason),
    stop_sequence: null,
    usage: anthropicUsage(message.usage),
  };
}

async function readJsonBody(req, maxBodyBytes = MAX_BODY_BYTES) {
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    total += chunk.byteLength;
    if (total > maxBodyBytes) throw httpError(413, "request body too large");
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  try {
    const body = text ? JSON.parse(text) : {};
    return body;
  } catch (error) {
    throw httpError(400, `invalid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function httpError(status, message, diagnostic = undefined) {
  const error = new Error(message);
  error.status = status;
  if (diagnostic) error.diagnostic = diagnostic;
  return error;
}

function capabilityError(code, cause = undefined) {
  return httpError(503, "authentication capability probe failed", {
    category: "capability_error",
    code,
    cause,
  });
}

function sendJson(res, status, body, headers = undefined) {
  res.writeHead(status, {
    ...headers,
    "content-type": "application/json",
    "cache-control": "no-store",
  });
  res.end(`${JSON.stringify(body)}\n`);
}

function errorType(status) {
  if (status === 529) return "overloaded_error";
  if (status >= 500) return "api_error";
  if (status === 401 || status === 403) return "authentication_error";
  if (status === 429) return "rate_limit_error";
  return "invalid_request_error";
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

const LOG_PATHS = new Set([
  "/",
  "/health",
  "/capabilities",
  "/v1/models",
  "/models",
  "/v1/messages/count_tokens",
  "/messages/count_tokens",
  "/v1/messages",
  "/messages",
]);
const LOG_METHODS = new Set(["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]);

function logPath(req) {
  try {
    const pathname = new URL(req?.url || "/", "http://localhost").pathname;
    return LOG_PATHS.has(pathname) ? pathname : "<unknown>";
  } catch {
    return "<invalid>";
  }
}

function logMethod(req) {
  return LOG_METHODS.has(req?.method) ? req.method : "UNKNOWN";
}

function logStatus(status) {
  return Number.isInteger(status) && status >= 400 && status <= 599 ? status : 500;
}

const SAFE_DIAGNOSTIC_CATEGORIES = new Set(["capability_error"]);
const SAFE_DIAGNOSTIC_CODES = new Set([
  "auth",
  "malformed_auth_result",
  "missing_model",
  "oauth",
  "probe_failed",
  "provider",
]);

function safeDiagnostic(error) {
  const category = error?.diagnostic?.category;
  const code = error?.diagnostic?.code;
  return {
    ...(SAFE_DIAGNOSTIC_CATEGORIES.has(category) ? { category } : {}),
    ...(SAFE_DIAGNOSTIC_CODES.has(code) ? { code } : {}),
  };
}

const SAFE_SERVER_ERROR_CODES = new Set([
  "EACCES",
  "EADDRINUSE",
  "EADDRNOTAVAIL",
  "EAFNOSUPPORT",
  "EINVAL",
  "ENETUNREACH",
]);
const SAFE_SERVER_ERROR_SYSCALLS = new Set(["listen"]);

function serverErrorDiagnostic(error, config) {
  const code = SAFE_SERVER_ERROR_CODES.has(error?.code) ? error.code : undefined;
  const syscall = SAFE_SERVER_ERROR_SYSCALLS.has(error?.syscall) ? error.syscall : undefined;
  return {
    origin: "cc-openai-proxy",
    category: "server_error",
    errorType: "network_error",
    ...(code ? { code } : {}),
    ...(syscall ? { syscall } : {}),
    host: config.host,
    port: config.port,
  };
}

function logError(status, req, error = undefined) {
  const safeStatus = logStatus(status);
  process.stderr.write(
    `${JSON.stringify({
      category: "request_error",
      method: logMethod(req),
      pathname: logPath(req),
      status: safeStatus,
      errorType: errorType(safeStatus),
      ...safeDiagnostic(error),
    })}\n`,
  );
}

function sendError(res, error, req) {
  const status = error?.status || 500;
  const message = errorMessage(error);
  logError(status, req, error);
  sendJson(
    res,
    status,
    {
      type: "error",
      error: {
        type: errorType(status),
        message,
      },
    },
    isPlainObject(error?.headers) ? error.headers : undefined,
  );
}

function writeSse(res, event, data) {
  res.write(`event: ${event}\n`);
  res.write(`data: ${JSON.stringify(data)}\n\n`);
}

function contentBlockFromPartial(event) {
  return event.partial?.content?.[event.contentIndex];
}

async function streamAnthropicResponse(req, res, piStream, modelId, headers = undefined) {
  res.writeHead(200, {
    ...headers,
    "content-type": "text/event-stream",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
  });

  const messageId = `msg_${randomUUID().replaceAll("-", "")}`;
  const openBlocks = new Set();
  const toolDeltaSeen = new Set();
  let messageStarted = false;

  const ensureMessageStart = (partial) => {
    if (messageStarted) return;
    messageStarted = true;
    writeSse(res, "message_start", {
      type: "message_start",
      message: {
        id: partial?.responseId || messageId,
        type: "message",
        role: "assistant",
        content: [],
        model: partial?.responseModel || partial?.model || modelId,
        stop_reason: null,
        stop_sequence: null,
        usage: anthropicUsage(partial?.usage),
      },
    });
  };

  const closeBlock = (index) => {
    if (!openBlocks.has(index)) return;
    writeSse(res, "content_block_stop", { type: "content_block_stop", index });
    openBlocks.delete(index);
  };

  for await (const event of piStream) {
    if (event.type === "start") {
      ensureMessageStart(event.partial);
    } else if (event.type === "text_start") {
      ensureMessageStart(event.partial);
      openBlocks.add(event.contentIndex);
      writeSse(res, "content_block_start", {
        type: "content_block_start",
        index: event.contentIndex,
        content_block: { type: "text", text: "" },
      });
    } else if (event.type === "text_delta") {
      ensureMessageStart(event.partial);
      writeSse(res, "content_block_delta", {
        type: "content_block_delta",
        index: event.contentIndex,
        delta: { type: "text_delta", text: event.delta },
      });
    } else if (event.type === "text_end") {
      closeBlock(event.contentIndex);
    } else if (event.type === "thinking_start") {
      ensureMessageStart(event.partial);
      openBlocks.add(event.contentIndex);
      writeSse(res, "content_block_start", {
        type: "content_block_start",
        index: event.contentIndex,
        content_block: { type: "thinking", thinking: "" },
      });
    } else if (event.type === "thinking_delta") {
      ensureMessageStart(event.partial);
      writeSse(res, "content_block_delta", {
        type: "content_block_delta",
        index: event.contentIndex,
        delta: { type: "thinking_delta", thinking: event.delta },
      });
    } else if (event.type === "thinking_end") {
      const block = contentBlockFromPartial(event);
      if (block?.type === "thinking" && block.thinkingSignature && !block.redacted) {
        writeSse(res, "content_block_delta", {
          type: "content_block_delta",
          index: event.contentIndex,
          delta: {
            type: "signature_delta",
            signature: block.thinkingSignature,
          },
        });
      }
      closeBlock(event.contentIndex);
    } else if (event.type === "toolcall_start") {
      ensureMessageStart(event.partial);
      const block = contentBlockFromPartial(event) || {};
      openBlocks.add(event.contentIndex);
      writeSse(res, "content_block_start", {
        type: "content_block_start",
        index: event.contentIndex,
        content_block: {
          type: "tool_use",
          id: encodeToolId(block.id || `toolu_${randomUUID().replaceAll("-", "")}`),
          name: block.name || "tool",
          input: {},
        },
      });
    } else if (event.type === "toolcall_delta") {
      ensureMessageStart(event.partial);
      toolDeltaSeen.add(event.contentIndex);
      writeSse(res, "content_block_delta", {
        type: "content_block_delta",
        index: event.contentIndex,
        delta: { type: "input_json_delta", partial_json: event.delta },
      });
    } else if (event.type === "toolcall_end") {
      if (!toolDeltaSeen.has(event.contentIndex)) {
        writeSse(res, "content_block_delta", {
          type: "content_block_delta",
          index: event.contentIndex,
          delta: {
            type: "input_json_delta",
            partial_json: JSON.stringify(event.toolCall?.arguments || {}),
          },
        });
      }
      closeBlock(event.contentIndex);
    } else if (event.type === "done") {
      ensureMessageStart(event.message);
      for (const index of [...openBlocks].sort((a, b) => a - b)) closeBlock(index);
      writeSse(res, "message_delta", {
        type: "message_delta",
        delta: {
          stop_reason: mapStopReason(event.message.stopReason),
          stop_sequence: null,
        },
        usage: anthropicUsage(event.message.usage),
      });
      writeSse(res, "message_stop", { type: "message_stop" });
    } else if (event.type === "error") {
      const status = event.reason === "aborted" ? 499 : 502;
      const message = event.error?.errorMessage || "upstream error";
      logError(status, req);
      writeSse(res, "error", {
        type: "error",
        error: {
          type: event.reason === "aborted" ? "request_aborted" : "api_error",
          message,
        },
      });
    }
  }

  res.end();
}

// Betas that Claude Code 2.1.274 can put on a request. The proxy honors none
// of them and forwards none of them. Most select first-party API behaviors
// that the translation to Codex loses without harm. A beta outside this set
// can change the request or response protocol, as dangerous-tool-use did: the
// server-side auto mode classifier returns its verdict in
// message_delta.safeguard_results, and the proxy sends none. Log each unknown
// beta once so a new protocol shows up before it breaks a session.
const KNOWN_BETAS = new Set([
  "advanced-tool-use-2025-11-20",
  "advisor-tool-2026-03-01",
  "afk-mode-2026-01-31",
  "agent-memory-2026-07-22",
  "auto-mode-classifier-2026-07-16",
  "cache-diagnosis-2026-04-07",
  "context-1m-2025-08-07",
  "context-hint-2026-04-09",
  "context-management-2025-06-27",
  "dangerous-tool-use-2026-09-03",
  "effort-2025-11-24",
  "extended-cache-ttl-2025-04-11",
  "fallback-credit-2026-06-01",
  "fast-mode-2026-02-01",
  "files-api-2025-04-14",
  "interleaved-thinking-2025-05-14",
  "mcp-servers-2025-12-04",
  "message-threads-2026-08-12",
  "mid-conversation-system-2026-04-07",
  "mid-conversation-system-clear-at-2026-08-21",
  "mid-conversation-tool-changes-2026-07-01",
  "oauth-2025-04-20",
  "per-turn-control-2026-07-01",
  "prompt-caching-evict-2026-05-12",
  "prompt-caching-scope-2026-01-05",
  "redact-thinking-2026-02-12",
  "server-side-fallback-2026-06-01",
  "server-side-fallback-2026-07-01",
  "skills-2025-10-02",
  "structured-outputs-2025-12-15",
  "task-budgets-2026-03-13",
  "thinking-binding-controls-2026-08-01",
  "thinking-display-updates-2026-08-18",
  "thinking-resumption-2026-07-17",
  "thinking-token-count-2026-05-13",
  "token-counting-2024-11-01",
  "tool-search-tool-2025-10-19",
  "web-search-2025-03-05",
]);
const reportedBetas = new Set();

// The SDK sends betas as a comma-separated anthropic-beta header. A body
// `betas` array is accepted as well for clients that bypass the SDK.
function requestBetas(req, body) {
  const values = [];
  const header = req.headers?.["anthropic-beta"];
  for (const value of Array.isArray(header) ? header : [header]) {
    if (typeof value === "string") values.push(...value.split(","));
  }
  if (Array.isArray(body?.betas)) {
    values.push(...body.betas.filter((value) => typeof value === "string"));
  }
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

function warnUnknownBetas(req, body, reported = reportedBetas, log = logEvent) {
  const unknown = [];
  for (const beta of requestBetas(req, body)) {
    if (KNOWN_BETAS.has(beta) || reported.has(beta)) continue;
    reported.add(beta);
    unknown.push(beta);
    log({ category: "unknown_beta", beta, path: logPath(req) });
  }
  return unknown;
}

// Claude Code sends X-Claude-Code-Session-Id on every API request. The same
// value keys the Codex prompt cache and the account binding.
function sessionIdFor(req) {
  const headerValue = (name) => {
    const value = req.headers?.[name];
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
  };
  return (
    process.env.CC_OPENAI_SESSION_ID ||
    headerValue("x-claude-code-session-id") ||
    headerValue("x-claude-session-id") ||
    headerValue("x-client-request-id") ||
    processSessionId
  );
}

function buildOptions(request, req, signal, sessionId = sessionIdFor(req), hooks = undefined) {
  const reasoning = thinkingToReasoning(request.thinking);
  return {
    maxTokens: request.max_tokens,
    temperature: request.temperature,
    signal,
    ...(reasoning ? { reasoning } : {}),
    transport: process.env.CC_OPENAI_TRANSPORT || "auto",
    cacheRetention: process.env.CC_OPENAI_CACHE_RETENTION || "short",
    sessionId,
    timeoutMs: process.env.CC_OPENAI_TIMEOUT_MS
      ? Number.parseInt(process.env.CC_OPENAI_TIMEOUT_MS, 10)
      : undefined,
    ...(hooks?.onResponse ? { onResponse: hooks.onResponse } : {}),
    ...(hooks?.fetch ? { fetch: hooks.fetch } : {}),
  };
}

// pi-ai reports Codex failures as one message. These hooks keep the raw 429
// body and the x-codex-* headers of the SSE request for the account pool.
function requestHooks(pool, account) {
  const captured = {};
  return {
    captured,
    onResponse: ({ status, headers }) => {
      if (isPlainObject(headers)) pool.observeHeaders(account, headers);
      if (status === 429) {
        const retryAfter = Number(headers?.["retry-after"]);
        if (Number.isFinite(retryAfter) && retryAfter > 0)
          captured.retryAfterMs = retryAfter * 1000;
      }
    },
    fetch: async (url, init) => {
      const response = await globalThis.fetch(url, init);
      if (response.status === 429) {
        const text = await response
          .clone()
          .text()
          .catch(() => "");
        captured.limit = parseLimitErrorBody(text);
      }
      return response;
    },
  };
}

function usageHeaders(pool, account) {
  if (process.env.CC_OPENAI_USAGE_HEADERS === "0" || !account.snapshot) return {};
  return rateLimitHeaders({
    windows: account.snapshot.windows,
    rejected: false,
    nowMs: pool.now(),
  });
}

// The 429 Claude Code understands: unified rate-limit headers with the
// earliest reset across accounts. Transient per-minute limits get a plain 429
// that Claude Code retries by itself.
function usageLimitError(pool) {
  const state = pool.exhaustedState();
  const nowMs = pool.now();
  if (state.resetAt === undefined && state.transientUntil !== undefined) {
    const seconds = Math.max(1, Math.ceil((state.transientUntil - nowMs) / 1000));
    const error = httpError(429, "OpenAI Codex rate limited the request. Retry shortly.");
    error.headers = { "retry-after": String(seconds) };
    return error;
  }
  const headers = rateLimitHeaders({
    windows: state.windows,
    rejected: true,
    resetAt: state.resetAt,
    nowMs,
  });
  const resetAt = Number(headers["anthropic-ratelimit-unified-reset"]);
  const plans = state.plans.length > 0 ? ` (${state.plans.join(", ")})` : "";
  const noun = state.accountCount === 1 ? "account" : "accounts";
  const error = httpError(
    429,
    `OpenAI Codex usage limit reached on ${state.accountCount} ${noun}${plans}. Resets at ${new Date(resetAt * 1000).toISOString()}.`,
  );
  error.headers = headers;
  return error;
}

async function takeFirstEvent(piStream) {
  const iterator = piStream[Symbol.asyncIterator]();
  const first = await iterator.next();
  if (first.done) throw httpError(502, "upstream stream ended without events");
  return { event: first.value, iterator };
}

async function* resumeStream(event, iterator) {
  yield event;
  for (;;) {
    const next = await iterator.next();
    if (next.done) return;
    yield next.value;
  }
}

async function collectMessage(events) {
  for await (const event of events) {
    if (event.type === "done") return event.message;
    if (event.type === "error") {
      const status = event.reason === "aborted" ? 499 : 502;
      throw httpError(status, event.error?.errorMessage || "upstream error");
    }
  }
  throw httpError(502, "upstream stream ended without a result");
}

function extractInboundBearer(req) {
  const auth = req.headers["authorization"];
  if (typeof auth === "string" && auth.startsWith("Bearer ")) return auth.slice(7).trim();
  const apiKey = req.headers["x-api-key"];
  if (typeof apiKey === "string") return apiKey.trim();
  return "";
}

// Compare equal-length bearer or API-key credentials with a timing-safe operation.
// The server loads the expected credential before it listens. Health bypasses this check.
function assertInboundAuth(req, expected) {
  if (!expected) throw httpError(401, "proxy auth is not available");
  const got = Buffer.from(extractInboundBearer(req));
  const want = Buffer.from(expected);
  if (got.length !== want.length || !timingSafeEqual(got, want)) {
    throw httpError(401, "unauthorized");
  }
}

// Anthropic-shaped model discovery so Claude Code can list the codex catalog by
// real ids. Gated like /v1/messages (never spends: it only reads the static
// provider catalog, no backend call).
async function handleModels(req, res) {
  const models = await loadModels();
  const supportedModels = models.getModels(DEFAULT_PROVIDER);
  assertTokenizerMappings(supportedModels);
  const data = supportedModels.map((model) => ({
    type: "model",
    id: model.id,
    display_name: model.name,
  }));
  sendJson(res, 200, {
    data,
    has_more: false,
    first_id: data[0]?.id ?? null,
    last_id: data[data.length - 1]?.id ?? null,
  });
}

async function assertKnownModel(modelName, catalog = undefined) {
  const models = catalog ?? (await loadModels());
  const modelId = resolveModelId(modelName);
  const model = models.getModel(DEFAULT_PROVIDER, modelId);
  // Unknown ids 400 here. Future option (P3): synthesize an
  // openai-codex-responses descriptor on this miss and optimistically route it,
  // so a model works before the next pi-ai bump ships its descriptor -- at the
  // cost of placeholder pricing/metadata and a 502 (not 400) for ids the
  // backend rejects.
  if (!model) {
    throw httpError(400, `unknown ${DEFAULT_PROVIDER} model: ${modelId}`);
  }
  assertTokenizerMappings([model]);
  return { model, modelId, models };
}

async function handleCountTokens(req, res) {
  const body = await readJsonBody(req);
  warnUnknownBetas(req, body);
  const { model } = await assertKnownModel(body.model);
  const inputTokens = await estimateInputTokensOffThread(model, body);
  sendJson(res, 200, countTokensResponse(inputTokens));
}

// The Anthropic API defaults `stream` to false, and Claude Code's SDK omits
// the field on non-streaming requests (for example /model validation probes).
// An SSE reply to such a request makes the SDK resolve to the raw event text.
function wantsStreaming(body) {
  return body.stream === true;
}

// The response head waits for the first stream event. Both Codex transports
// fail before that event on a usage limit, so the failure can move to another
// account or become a real 429 instead of an SSE error after a 200.
async function handleMessages(req, res, pool) {
  const body = await readJsonBody(req);
  warnUnknownBetas(req, body);
  const { model, modelId } = await assertKnownModel(body.model, pool.primaryModels());

  const controller = new AbortController();
  let complete = false;
  req.on("aborted", () => controller.abort(new Error("request aborted")));
  res.on("close", () => {
    if (!complete) controller.abort(new Error("client disconnected"));
  });

  const sessionId = sessionIdFor(req);
  const context = anthropicToContext(body);
  const tried = new Set();

  for (;;) {
    let account;
    try {
      account = await pool.select({ sessionKey: sessionId, exclude: tried });
    } catch (error) {
      if (error?.code === "auth") throw missingCredentialsError();
      throw error;
    }
    if (!account) throw usageLimitError(pool);
    tried.add(account.index);

    const hooks = requestHooks(pool, account);
    const options = buildOptions(body, req, controller.signal, sessionId, hooks);
    const { event, iterator } = await takeFirstEvent(
      account.models.streamSimple(model, context, options),
    );
    if (event.type === "error") {
      const message = event.error?.errorMessage || "upstream error";
      if (event.reason === "aborted" || controller.signal.aborted) throw httpError(499, message);
      const kind = hooks.captured.limit?.kind ?? classifyStreamError(message);
      if (kind === "usage_limit") {
        await pool.markLimited(account, hooks.captured.limit);
        continue;
      }
      if (kind === "transient") {
        pool.markTransient(account, hooks.captured.retryAfterMs);
        continue;
      }
      throw httpError(502, message);
    }

    const headers = {
      ...usageHeaders(pool, account),
      "x-cc-openai-account": String(account.index),
    };
    const events = resumeStream(event, iterator);
    if (wantsStreaming(body)) {
      await streamAnthropicResponse(req, res, events, modelId, headers);
      complete = true;
      return;
    }
    const message = await collectMessage(events);
    complete = true;
    sendJson(res, 200, piMessageToAnthropic(message, modelId), headers);
    return;
  }
}

async function probeOpenAiAuth(load = loadModels) {
  authProbePromise ??= (async () => {
    try {
      const models = await load();
      const model = models.getModel(DEFAULT_PROVIDER, DEFAULT_MODEL);
      if (!model) throw capabilityError("missing_model");

      let result;
      try {
        result = await models.getAuth(model);
      } catch (error) {
        const code = SAFE_DIAGNOSTIC_CODES.has(error?.code) ? error.code : "probe_failed";
        throw capabilityError(code, error);
      }
      // pi-ai documents undefined as the unknown or unconfigured outcome.
      if (result === undefined) return false;
      if (
        !isPlainObject(result) ||
        !isPlainObject(result.auth) ||
        typeof result.auth.apiKey !== "string" ||
        result.auth.apiKey.trim() === ""
      ) {
        throw capabilityError("malformed_auth_result");
      }
      return true;
    } finally {
      authProbePromise = undefined;
    }
  })();
  return authProbePromise;
}

async function route(req, res, expectedBearer, probeAuth = probeOpenAiAuth, pool = undefined) {
  try {
    const url = new URL(req.url || "/", "http://localhost");
    if (req.method === "GET" && url.pathname === "/health") {
      sendJson(res, 200, { ok: true });
      return;
    }

    assertInboundAuth(req, expectedBearer);
    if (req.method === "GET" && url.pathname === "/") {
      sendJson(res, 200, {
        ok: true,
        provider: DEFAULT_PROVIDER,
        defaultModel: process.env.CC_OPENAI_DEFAULT_MODEL || DEFAULT_MODEL,
      });
    } else if (req.method === "GET" && url.pathname === "/capabilities") {
      try {
        sendJson(res, 200, { openaiAuthUsable: await probeAuth() });
      } catch (error) {
        if (error?.diagnostic?.category === "capability_error") throw error;
        throw capabilityError("probe_failed", error);
      }
    } else if (
      req.method === "GET" &&
      (url.pathname === "/v1/models" || url.pathname === "/models")
    ) {
      await handleModels(req, res);
    } else if (
      req.method === "POST" &&
      (url.pathname === "/v1/messages/count_tokens" || url.pathname === "/messages/count_tokens")
    ) {
      await handleCountTokens(req, res);
    } else if (
      req.method === "POST" &&
      (url.pathname === "/v1/messages" || url.pathname === "/messages")
    ) {
      await handleMessages(req, res, pool ?? (await loadAccountPool()));
    } else {
      throw httpError(404, `not found: ${req.method} ${url.pathname}`);
    }
  } catch (error) {
    if (!res.headersSent) {
      sendError(res, error, req);
    } else {
      const message = errorMessage(error);
      logError(error?.status || 500, req);
      writeSse(res, "error", {
        type: "error",
        error: {
          type: "api_error",
          message,
        },
      });
      res.end();
    }
  }
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringifyUnknown(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export {
  anthropicToContext,
  anthropicToolsToPi,
  assertInboundAuth,
  assertTokenizerMappings,
  authFilePaths,
  buildOptions,
  canonicalResponsesPayload,
  countTokensResponse,
  errorType,
  estimateInputTokens,
  extractInboundBearer,
  handleMessages,
  logError,
  parseArgs,
  piContentToAnthropic,
  probeOpenAiAuth,
  requestBetas,
  route,
  piMessageToAnthropic,
  resolveModelId,
  serverErrorDiagnostic,
  sessionIdFor,
  streamAnthropicResponse,
  thinkingToReasoning,
  usageLimitError,
  wantsStreaming,
  warnUnknownBetas,
};

async function main() {
  const config = parseArgs(process.argv.slice(2));
  let expectedBearer;
  try {
    expectedBearer = await loadOrCreateProxyToken(config.authTokenFile);
  } catch (error) {
    error.authPath = config.authTokenFile;
    throw error;
  }
  const server = createServer((req, res) => {
    void route(req, res, expectedBearer);
  });
  server.on("error", (error) => {
    process.stderr.write(`${JSON.stringify(serverErrorDiagnostic(error, config))}\n`);
    process.exit(1);
  });
  server.listen(config.port, config.host, () => {
    process.stderr.write(
      `${JSON.stringify({ origin: "cc-openai-proxy", category: "server_started" })}\n`,
    );
  });
}

if (!isMainThread && workerData?.operation === "estimateInputTokens") {
  try {
    parentPort.postMessage({
      inputTokens: estimateInputTokens(workerData.model, workerData.request),
    });
  } catch (error) {
    parentPort.postMessage({
      error: error instanceof Error ? error.message : String(error),
    });
  }
} else if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    process.stderr.write(
      `${JSON.stringify({
        origin: "cc-openai-proxy",
        phase: "startup",
        ...proxyAuthDiagnostic(error, error?.authPath),
      })}\n`,
    );
    process.exit(2);
  });
}
