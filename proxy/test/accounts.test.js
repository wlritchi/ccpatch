import test from "node:test";
import assert from "node:assert/strict";

import {
  CODEX_USAGE_URL,
  accountWeight,
  classifyStreamError,
  createAccountPool,
  parseCodexHeaders,
  parseLimitErrorBody,
  parsePlanCapacity,
  parseUsageResponse,
  rateLimitHeaders,
} from "../bin/codex-accounts.js";
import { errorType, route, sessionIdFor, usageLimitError } from "../bin/cc-openai-proxy.js";

const NOW_MS = 1_790_064_000_000; // 2026-09-22T06:40:00Z
const NOW_S = NOW_MS / 1000;
const WEEK = 604_800;
const FIVE_HOURS = 18_000;
const MODEL = { id: "gpt-5.6-sol", provider: "openai-codex" };

// Observed on 2026-09-22 from an exhausted prolite account.
const usagePayload = {
  plan_type: "prolite",
  rate_limit: {
    allowed: false,
    limit_reached: true,
    primary_window: {
      used_percent: 100,
      limit_window_seconds: WEEK,
      reset_after_seconds: 350_364,
      reset_at: NOW_S + 350_364,
    },
    secondary_window: null,
  },
  credits: { has_credits: false, unlimited: false, balance: "0" },
};

const limitBody = JSON.stringify({
  error: {
    type: "usage_limit_reached",
    message: "The usage limit has been reached",
    plan_type: "prolite",
    resets_at: NOW_S + 350_094,
    eligible_promo: null,
    resets_in_seconds: 350_094,
  },
});

function window(usedPercent, windowSeconds, resetInSeconds) {
  return { usedPercent, windowSeconds, resetAt: NOW_S + resetInSeconds };
}

test("parses the Codex usage endpoint payload", () => {
  const snapshot = parseUsageResponse(usagePayload, NOW_MS);
  assert.equal(snapshot.planType, "prolite");
  assert.equal(snapshot.limitReached, true);
  assert.deepEqual(snapshot.windows, [window(100, WEEK, 350_364)]);
  assert.equal(snapshot.source, "usage");
  assert.equal(parseUsageResponse(null), undefined);
  const twoWindows = parseUsageResponse(
    {
      plan_type: "plus",
      rate_limit: {
        allowed: true,
        limit_reached: false,
        primary_window: {
          used_percent: 12.5,
          limit_window_seconds: FIVE_HOURS,
          reset_after_seconds: 900,
        },
        secondary_window: { used_percent: 40, limit_window_seconds: WEEK, reset_at: NOW_S + 3600 },
      },
    },
    NOW_MS,
  );
  assert.deepEqual(twoWindows.windows, [window(12.5, FIVE_HOURS, 900), window(40, WEEK, 3600)]);
  assert.equal(twoWindows.limitReached, false);
});

test("parses x-codex response headers and ignores blank secondary windows", () => {
  const snapshot = parseCodexHeaders(
    {
      "x-codex-plan-type": "prolite",
      "x-codex-primary-used-percent": "100",
      "x-codex-primary-window-minutes": "10080",
      "x-codex-primary-reset-at": String(NOW_S + 350_095),
      "x-codex-primary-reset-after-seconds": "350095",
      "x-codex-secondary-used-percent": "0",
      "x-codex-secondary-window-minutes": "0",
      "x-codex-secondary-reset-at": "",
      "x-codex-secondary-reset-after-seconds": "0",
    },
    NOW_MS,
  );
  assert.equal(snapshot.planType, "prolite");
  assert.equal(snapshot.limitReached, true);
  assert.deepEqual(snapshot.windows, [window(100, WEEK, 350_095)]);
  assert.equal(parseCodexHeaders({ "content-type": "application/json" }), undefined);
  const partial = parseCodexHeaders(
    { "x-codex-primary-used-percent": "30", "x-codex-primary-window-minutes": "300" },
    NOW_MS,
  );
  assert.equal(partial, undefined);
});

test("classifies Codex 429 bodies and pi-ai error messages", () => {
  const limit = parseLimitErrorBody(limitBody, NOW_MS);
  assert.equal(limit.kind, "usage_limit");
  assert.equal(limit.planType, "prolite");
  assert.equal(limit.resetAt, NOW_S + 350_094);
  assert.equal(
    parseLimitErrorBody(
      JSON.stringify({ error: { code: "rate_limit_exceeded", resets_in_seconds: 20 } }),
      NOW_MS,
    ).kind,
    "transient",
  );
  assert.equal(parseLimitErrorBody("not json"), undefined);
  assert.equal(parseLimitErrorBody(JSON.stringify({ error: { type: "server_error" } })), undefined);

  assert.equal(
    classifyStreamError(
      "You have hit your ChatGPT usage limit (prolite plan). Try again in ~5835 min.",
    ),
    "usage_limit",
  );
  assert.equal(classifyStreamError("Codex error: The usage limit has been reached"), "usage_limit");
  assert.equal(classifyStreamError("Codex error: Rate limit reached for gpt-5.6-sol"), "transient");
  assert.equal(classifyStreamError("Codex error: model is at capacity"), undefined);
  assert.equal(classifyStreamError(undefined), undefined);
});

test("weights accounts by remaining capacity per hour until reset", () => {
  const table = parsePlanCapacity("pro=20,plus=10");
  const bigger = { planType: "pro", limitReached: false, windows: [window(90, WEEK, 6 * 3600)] };
  const smaller = { planType: "plus", limitReached: false, windows: [window(90, WEEK, 3 * 3600)] };
  assert.ok(
    Math.abs(accountWeight(bigger, NOW_MS, table) - accountWeight(smaller, NOW_MS, table)) < 1e-9,
  );
  assert.ok(accountWeight(bigger, NOW_MS, table) > 0);

  const binding = {
    planType: "plus",
    limitReached: false,
    windows: [window(0, FIVE_HOURS, FIVE_HOURS), window(99, WEEK, WEEK)],
  };
  assert.ok(accountWeight(binding, NOW_MS, table) < accountWeight(bigger, NOW_MS, table));
  assert.equal(
    accountWeight(
      { planType: "pro", limitReached: true, windows: [window(100, WEEK, 100)] },
      NOW_MS,
      table,
    ),
    0,
  );
  assert.equal(accountWeight(undefined, NOW_MS, table), 1);
  assert.equal(
    accountWeight(
      { planType: "pro", limitReached: true, windows: [window(100, WEEK, -1)] },
      NOW_MS,
      table,
    ),
    20,
  );
  assert.throws(() => parsePlanCapacity("pro"), /invalid plan capacity/);
  assert.throws(() => parsePlanCapacity("pro=-1"), /invalid plan capacity/);
});

test("builds the unified rate-limit headers Claude Code reads", () => {
  const rejected = rateLimitHeaders({
    windows: [window(100, WEEK, 350_000)],
    rejected: true,
    nowMs: NOW_MS,
  });
  assert.equal(rejected["anthropic-ratelimit-unified-status"], "rejected");
  assert.equal(rejected["anthropic-ratelimit-unified-representative-claim"], "seven_day");
  assert.equal(rejected["anthropic-ratelimit-unified-reset"], String(NOW_S + 350_000));
  assert.equal(rejected["anthropic-ratelimit-unified-7d-utilization"], "1.0000");
  assert.equal(rejected["anthropic-ratelimit-unified-7d-reset"], String(NOW_S + 350_000));
  assert.equal(rejected["retry-after"], "350000");
  assert.equal(rejected["x-should-retry"], "false");
  assert.equal("anthropic-ratelimit-unified-5h-utilization" in rejected, false);

  const allowed = rateLimitHeaders({
    windows: [window(25, FIVE_HOURS, 1200), window(60, WEEK, 90_000)],
    rejected: false,
    nowMs: NOW_MS,
  });
  assert.equal(allowed["anthropic-ratelimit-unified-status"], "allowed");
  assert.equal(allowed["anthropic-ratelimit-unified-representative-claim"], "seven_day");
  assert.equal(allowed["anthropic-ratelimit-unified-reset"], String(NOW_S + 90_000));
  assert.equal(allowed["anthropic-ratelimit-unified-5h-utilization"], "0.2500");
  assert.equal(allowed["anthropic-ratelimit-unified-5h-reset"], String(NOW_S + 1200));
  assert.equal(allowed["anthropic-ratelimit-unified-7d-utilization"], "0.6000");
  assert.equal("retry-after" in allowed, false);

  const fiveHour = rateLimitHeaders({
    windows: [window(100, FIVE_HOURS, 600)],
    rejected: true,
    nowMs: NOW_MS,
  });
  assert.equal(fiveHour["anthropic-ratelimit-unified-representative-claim"], "five_hour");

  const unknown = rateLimitHeaders({ rejected: true, nowMs: NOW_MS });
  assert.equal(unknown["anthropic-ratelimit-unified-representative-claim"], "seven_day");
  assert.equal(unknown["anthropic-ratelimit-unified-reset"], String(NOW_S + 1800));
  assert.equal(errorType(429), "rate_limit_error");
});

function jwt(accountId) {
  const payload = Buffer.from(
    JSON.stringify({ "https://api.openai.com/auth": { chatgpt_account_id: accountId } }),
  ).toString("base64url");
  return `header.${payload}.signature`;
}

function fakeModels({ token = jwt("account-a"), events, streamCalls = [] } = {}) {
  return {
    getModel: (provider, id) =>
      provider === MODEL.provider && id === MODEL.id ? MODEL : undefined,
    getModels: (provider) => (provider === MODEL.provider ? [MODEL] : []),
    async getAuth() {
      if (token instanceof Error) throw token;
      return token ? { auth: { apiKey: token } } : undefined;
    },
    streamSimple(model, context, options) {
      streamCalls.push({ model, context, options });
      const source = typeof events === "function" ? events(options) : events;
      return (async function* stream() {
        for (const event of source) yield event;
      })();
    },
  };
}

function usageFetch(payloads, calls = []) {
  return async (url, init) => {
    calls.push({ url, init });
    assert.equal(url, CODEX_USAGE_URL);
    assert.match(init.headers.authorization, /^Bearer /);
    const payload = payloads[calls.length - 1] ?? payloads[payloads.length - 1];
    if (payload instanceof Error) throw payload;
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
}

const freshUsage = (planType, usedPercent, windowSeconds = WEEK, resetIn = WEEK) => ({
  plan_type: planType,
  rate_limit: {
    allowed: usedPercent < 100,
    limit_reached: usedPercent >= 100,
    primary_window: {
      used_percent: usedPercent,
      limit_window_seconds: windowSeconds,
      reset_at: NOW_S + resetIn,
    },
    secondary_window: null,
  },
});

test("pool binds sessions to one account and rebinds when it is exhausted", async () => {
  let clock = NOW_MS;
  const randoms = [0.99, 0.01, 0.99];
  const calls = [];
  const pool = createAccountPool({
    accounts: [
      { label: "a", models: fakeModels() },
      { label: "b", models: fakeModels() },
    ],
    fetch: usageFetch([freshUsage("plus", 10), freshUsage("plus", 10)], calls),
    now: () => clock,
    random: () => randoms.shift() ?? 0.5,
    defaultModelId: MODEL.id,
  });
  const first = await pool.select({ sessionKey: "session-1" });
  assert.equal(first.index, 1);
  assert.equal(calls.length, 2);
  for (let i = 0; i < 5; i += 1) {
    assert.equal((await pool.select({ sessionKey: "session-1" })).index, 1);
  }
  assert.equal((await pool.select({ sessionKey: "session-2" })).index, 0);
  assert.equal(calls.length, 2);

  clock += 6 * 60 * 1000;
  await pool.markLimited(pool.accounts[1], {
    kind: "usage_limit",
    resetAt: NOW_S + 3600,
    planType: "plus",
  });
  assert.equal(pool.accounts[1].limitedUntil, (NOW_S + 3600) * 1000);
  assert.equal((await pool.select({ sessionKey: "session-1" })).index, 0);
  assert.equal(pool.sessions.get("session-1").index, 0);
  assert.equal(pool.exhaustedState().resetAt, NOW_S + 3600);

  clock = (NOW_S + 3601) * 1000;
  assert.equal(pool.candidateWeight(pool.accounts[1]) > 0, true);
});

test("pool refreshes usage after the TTL and uses the usage reset when the error has none", async () => {
  let clock = NOW_MS;
  const calls = [];
  const pool = createAccountPool({
    accounts: [{ label: "a", models: fakeModels() }],
    fetch: usageFetch([freshUsage("prolite", 50), freshUsage("prolite", 100, WEEK, 5000)], calls),
    now: () => clock,
    usageTtlMs: 1000,
    defaultModelId: MODEL.id,
  });
  assert.equal((await pool.select({})).index, 0);
  assert.equal(calls.length, 1);
  clock += 2000;
  await pool.select({});
  if (pool.accounts[0].refresh) await pool.accounts[0].refresh;
  assert.equal(calls.length, 2);
  assert.equal(pool.accounts[0].snapshot.windows[0].usedPercent, 100);
  assert.equal(await pool.select({}), undefined);

  const withFallback = createAccountPool({
    accounts: [{ label: "a", models: fakeModels() }],
    fetch: usageFetch([new Error("offline")]),
    now: () => clock,
    limitFallbackMs: 60_000,
    defaultModelId: MODEL.id,
  });
  await withFallback.markLimited(withFallback.accounts[0], undefined);
  assert.equal(withFallback.accounts[0].limitedUntil, clock + 60_000);
  assert.equal(withFallback.exhaustedState().resetAt, Math.floor((clock + 60_000) / 1000));
});

test("pool skips accounts without credentials and reports when none work", async () => {
  const logs = [];
  const pool = createAccountPool({
    accounts: [
      { label: "broken", models: fakeModels({ token: new Error("refresh failed") }) },
      { label: "good", models: fakeModels() },
    ],
    fetch: usageFetch([freshUsage("plus", 0)]),
    now: () => NOW_MS,
    random: () => 0,
    log: (entry) => logs.push(entry),
    defaultModelId: MODEL.id,
  });
  assert.equal((await pool.select({})).index, 1);
  assert.ok(logs.some((entry) => entry.event === "auth_unavailable" && entry.account === 0));
  assert.equal(await pool.probeAuth(), true);

  const none = createAccountPool({
    accounts: [{ label: "missing", models: fakeModels({ token: "" }) }],
    fetch: usageFetch([freshUsage("plus", 0)]),
    now: () => NOW_MS,
    defaultModelId: MODEL.id,
  });
  await assert.rejects(none.select({}), (error) => error.code === "auth");
  assert.equal(await none.probeAuth(), false);
});

function fakeRequest(body, headers = {}) {
  const chunks = [Buffer.from(JSON.stringify(body))];
  return {
    method: "POST",
    url: "/v1/messages",
    headers: { authorization: "Bearer proxy-token", ...headers },
    on() {},
    async *[Symbol.asyncIterator]() {
      for (const chunk of chunks) yield chunk;
    },
  };
}

function fakeResponse() {
  const response = {
    headersSent: false,
    status: undefined,
    headers: undefined,
    body: "",
    ended: false,
    writeHead(status, headers) {
      this.headersSent = true;
      this.status = status;
      this.headers = headers;
    },
    write(chunk) {
      this.body += chunk;
    },
    end(chunk) {
      if (chunk) this.body += chunk;
      this.ended = true;
    },
    on() {},
  };
  return response;
}

const doneMessage = {
  role: "assistant",
  content: [{ type: "text", text: "hello" }],
  stopReason: "stop",
  usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0 },
};
const successEvents = [
  { type: "start", partial: doneMessage },
  { type: "text_start", contentIndex: 0, partial: doneMessage },
  { type: "text_delta", contentIndex: 0, delta: "hello", partial: doneMessage },
  { type: "text_end", contentIndex: 0 },
  { type: "done", message: doneMessage },
];
const limitEvents = [
  {
    type: "error",
    reason: "error",
    error: {
      ...doneMessage,
      stopReason: "error",
      errorMessage: "Codex error: The usage limit has been reached",
    },
  },
];

async function run(pool, body, headers = {}) {
  const req = fakeRequest(body, headers);
  const res = fakeResponse();
  await route(req, res, "proxy-token", async () => true, pool);
  return res;
}

test("messages fail over to another account before the response head is written", async (t) => {
  t.mock.method(process.stderr, "write", () => true);
  const streamCalls = [];
  const pool = createAccountPool({
    accounts: [
      { label: "a", models: fakeModels({ events: limitEvents, streamCalls }) },
      { label: "b", models: fakeModels({ events: successEvents, streamCalls }) },
    ],
    fetch: usageFetch([
      freshUsage("plus", 10),
      freshUsage("pro", 10),
      freshUsage("plus", 100, WEEK, 4000),
    ]),
    now: () => NOW_MS,
    random: () => 0,
    defaultModelId: MODEL.id,
  });
  const res = await run(
    pool,
    {
      model: "claude-opus-4-7",
      messages: [{ role: "user", content: "hi" }],
      max_tokens: 16,
      stream: true,
    },
    { "x-claude-code-session-id": "session-9" },
  );
  assert.equal(res.status, 200);
  assert.equal(res.headers["content-type"], "text/event-stream");
  assert.equal(res.headers["x-cc-openai-account"], "1");
  assert.equal(res.headers["anthropic-ratelimit-unified-status"], "allowed");
  assert.equal(res.headers["anthropic-ratelimit-unified-7d-utilization"], "0.1000");
  assert.match(res.body, /event: message_stop/);
  assert.equal(streamCalls.length, 2);
  assert.equal(streamCalls[0].options.sessionId, "session-9");
  assert.equal(typeof streamCalls[0].options.fetch, "function");
  assert.equal(typeof streamCalls[0].options.onResponse, "function");
  assert.equal(pool.accounts[0].limitedUntil, (NOW_S + 4000) * 1000);
  assert.equal(pool.sessions.get("session-9").index, 1);

  const nonStreaming = await run(pool, {
    model: "claude-opus-4-7",
    messages: [{ role: "user", content: "hi" }],
    max_tokens: 16,
  });
  assert.equal(nonStreaming.status, 200);
  assert.equal(nonStreaming.headers["content-type"], "application/json");
  assert.equal(JSON.parse(nonStreaming.body).content[0].text, "hello");
});

test("messages answer 429 with Anthropic usage-limit headers when every account is exhausted", async (t) => {
  t.mock.method(process.stderr, "write", () => true);
  const pool = createAccountPool({
    accounts: [
      { label: "a", models: fakeModels({ events: limitEvents }) },
      { label: "b", models: fakeModels({ events: limitEvents }) },
    ],
    fetch: usageFetch([
      freshUsage("prolite", 20),
      freshUsage("plus", 20),
      freshUsage("prolite", 100, WEEK, 350_000),
      freshUsage("plus", 100, FIVE_HOURS, 1200),
    ]),
    now: () => NOW_MS,
    random: () => 0,
    defaultModelId: MODEL.id,
  });
  const res = await run(pool, {
    model: "claude-opus-4-7",
    messages: [{ role: "user", content: "hi" }],
    max_tokens: 16,
    stream: true,
  });
  assert.equal(res.status, 429);
  assert.equal(res.headers["content-type"], "application/json");
  assert.equal(res.headers["anthropic-ratelimit-unified-status"], "rejected");
  assert.equal(res.headers["anthropic-ratelimit-unified-representative-claim"], "five_hour");
  assert.equal(res.headers["anthropic-ratelimit-unified-reset"], String(NOW_S + 1200));
  assert.equal(res.headers["anthropic-ratelimit-unified-5h-utilization"], "1.0000");
  assert.equal(res.headers["retry-after"], "1200");
  assert.equal(res.headers["x-should-retry"], "false");
  const body = JSON.parse(res.body);
  assert.equal(body.error.type, "rate_limit_error");
  assert.match(body.error.message, /usage limit reached on 2 accounts \(prolite, plus\)/);
  assert.match(body.error.message, new RegExp(new Date((NOW_S + 1200) * 1000).toISOString()));

  const again = await run(pool, {
    model: "claude-opus-4-7",
    messages: [{ role: "user", content: "hi" }],
    max_tokens: 16,
  });
  assert.equal(again.status, 429);
  assert.equal(again.headers["anthropic-ratelimit-unified-reset"], String(NOW_S + 1200));
});

test("messages surface transient limits and other pre-stream failures as plain HTTP errors", async (t) => {
  t.mock.method(process.stderr, "write", () => true);
  const transient = createAccountPool({
    accounts: [
      {
        label: "a",
        models: fakeModels({
          events: [
            {
              type: "error",
              reason: "error",
              error: {
                ...doneMessage,
                stopReason: "error",
                errorMessage: "Codex error: Rate limit reached",
              },
            },
          ],
        }),
      },
    ],
    fetch: usageFetch([freshUsage("plus", 5)]),
    now: () => NOW_MS,
    defaultModelId: MODEL.id,
  });
  const limited = await run(transient, {
    model: "claude-opus-4-7",
    messages: [{ role: "user", content: "hi" }],
    max_tokens: 16,
    stream: true,
  });
  assert.equal(limited.status, 429);
  assert.equal(limited.headers["retry-after"], "15");
  assert.equal("anthropic-ratelimit-unified-status" in limited.headers, false);
  assert.equal("x-should-retry" in limited.headers, false);

  const failing = createAccountPool({
    accounts: [
      {
        label: "a",
        models: fakeModels({
          events: [
            {
              type: "error",
              reason: "error",
              error: {
                ...doneMessage,
                stopReason: "error",
                errorMessage: "Codex error: model is at capacity",
              },
            },
          ],
        }),
      },
    ],
    fetch: usageFetch([freshUsage("plus", 5)]),
    now: () => NOW_MS,
    defaultModelId: MODEL.id,
  });
  const failed = await run(failing, {
    model: "claude-opus-4-7",
    messages: [{ role: "user", content: "hi" }],
    max_tokens: 16,
    stream: true,
  });
  assert.equal(failed.status, 502);
  assert.equal(failed.headers["content-type"], "application/json");
  assert.deepEqual(JSON.parse(failed.body), {
    type: "error",
    error: { type: "api_error", message: "Codex error: model is at capacity" },
  });
  assert.equal(failing.accounts[0].limitedUntil, 0);
});

test("usageLimitError falls back to a fixed reset when nothing is known", () => {
  const pool = createAccountPool({
    accounts: [{ label: "a", models: fakeModels() }],
    now: () => NOW_MS,
    defaultModelId: MODEL.id,
  });
  const error = usageLimitError(pool);
  assert.equal(error.status, 429);
  assert.equal(error.headers["anthropic-ratelimit-unified-status"], "rejected");
  assert.match(error.message, /usage limit reached on 1 account\./);
});

test("sessionIdFor prefers the Claude Code session header", () => {
  const original = process.env.CC_OPENAI_SESSION_ID;
  delete process.env.CC_OPENAI_SESSION_ID;
  try {
    assert.equal(
      sessionIdFor({
        headers: { "x-claude-code-session-id": " abc ", "x-client-request-id": "req" },
      }),
      "abc",
    );
    assert.equal(sessionIdFor({ headers: { "x-client-request-id": "req" } }), "req");
    assert.match(sessionIdFor({ headers: {} }), /^cc-openai-/);
  } finally {
    if (original !== undefined) process.env.CC_OPENAI_SESSION_ID = original;
  }
});
