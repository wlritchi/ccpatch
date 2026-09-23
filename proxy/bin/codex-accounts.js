// Codex account pool: usage snapshots, weighting, sticky selection, and the
// Anthropic unified rate-limit header set. See docs/specs/codex-usage-limits.md.

const CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage";
const CODEX_PROVIDER = "openai-codex";
const JWT_CLAIM_PATH = "https://api.openai.com/auth";
const FIVE_HOUR_MAX_SECONDS = 6 * 3600;
const DEFAULT_USAGE_TTL_MS = 5 * 60 * 1000;
const DEFAULT_STICKY_MS = 6 * 60 * 60 * 1000;
const DEFAULT_LIMIT_FALLBACK_MS = 30 * 60 * 1000;
const AUTH_FAILURE_COOLDOWN_MS = 60 * 1000;
const USAGE_FAILURE_COOLDOWN_MS = 60 * 1000;
const TRANSIENT_COOLDOWN_MS = 15 * 1000;
const USAGE_FETCH_TIMEOUT_MS = 10 * 1000;
const MAX_STICKY_SESSIONS = 10_000;
const MIN_HOURS_UNTIL_RESET = 1 / 60;

// Relative subscription capacity per ChatGPT plan type. OpenAI publishes
// message ranges rather than exact quotas, so these are order-of-magnitude
// ratios (Pro is 20 times Plus). CC_OPENAI_PLAN_CAPACITY overrides them.
const DEFAULT_PLAN_CAPACITY = Object.freeze({
  free: 0.25,
  plus: 1,
  team: 1,
  business: 1,
  edu: 1,
  prolite: 5,
  pro: 20,
  enterprise: 20,
});

function parsePlanCapacity(spec, defaults = DEFAULT_PLAN_CAPACITY) {
  const table = { ...defaults };
  for (const entry of String(spec || "").split(",")) {
    const trimmed = entry.trim();
    if (!trimmed) continue;
    const separator = trimmed.indexOf("=");
    if (separator <= 0) throw new Error(`invalid plan capacity entry: ${trimmed}`);
    const plan = trimmed.slice(0, separator).trim().toLowerCase();
    const value = Number(trimmed.slice(separator + 1));
    if (!plan || !Number.isFinite(value) || value < 0) {
      throw new Error(`invalid plan capacity entry: ${trimmed}`);
    }
    table[plan] = value;
  }
  return table;
}

function capacityForPlan(table, planType) {
  const plan = String(planType || "").toLowerCase();
  const value = table[plan];
  return typeof value === "number" && Number.isFinite(value) ? value : 1;
}

function finiteNumber(value) {
  if (value === undefined || value === null || value === "") return undefined;
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function nowSeconds(nowMs) {
  return Math.floor(nowMs / 1000);
}

function makeWindow(usedPercent, windowSeconds, resetAt, resetAfterSeconds, nowMs) {
  const used = finiteNumber(usedPercent);
  const seconds = finiteNumber(windowSeconds);
  let reset = finiteNumber(resetAt);
  const after = finiteNumber(resetAfterSeconds);
  if (reset === undefined || reset <= 0) {
    reset = after !== undefined && after >= 0 ? nowSeconds(nowMs) + after : undefined;
  }
  if (used === undefined || seconds === undefined || seconds <= 0 || reset === undefined) {
    return undefined;
  }
  return {
    usedPercent: Math.min(100, Math.max(0, used)),
    windowSeconds: seconds,
    resetAt: Math.round(reset),
  };
}

// /wham/usage response to a snapshot.
function parseUsageResponse(json, nowMs = Date.now()) {
  if (!json || typeof json !== "object") return undefined;
  const rateLimit = json.rate_limit && typeof json.rate_limit === "object" ? json.rate_limit : {};
  const windows = [];
  for (const key of ["primary_window", "secondary_window"]) {
    const raw = rateLimit[key];
    if (!raw || typeof raw !== "object") continue;
    const window = makeWindow(
      raw.used_percent,
      raw.limit_window_seconds ??
        (raw.window_minutes !== undefined ? Number(raw.window_minutes) * 60 : undefined),
      raw.reset_at,
      raw.reset_after_seconds,
      nowMs,
    );
    if (window) windows.push(window);
  }
  return {
    planType: typeof json.plan_type === "string" ? json.plan_type : undefined,
    limitReached: rateLimit.limit_reached === true || rateLimit.allowed === false,
    windows,
    observedAt: nowMs,
    source: "usage",
  };
}

// x-codex-* response headers (lowercase keys) to a snapshot.
function parseCodexHeaders(headers, nowMs = Date.now()) {
  if (!headers || typeof headers !== "object") return undefined;
  const get = (name) => {
    const value = headers[name];
    return Array.isArray(value) ? value[0] : value;
  };
  const windows = [];
  for (const prefix of ["x-codex-primary", "x-codex-secondary"]) {
    const minutes = finiteNumber(get(`${prefix}-window-minutes`));
    if (minutes === undefined || minutes <= 0) continue;
    const window = makeWindow(
      get(`${prefix}-used-percent`),
      minutes * 60,
      get(`${prefix}-reset-at`),
      get(`${prefix}-reset-after-seconds`),
      nowMs,
    );
    if (window) windows.push(window);
  }
  const planType = get("x-codex-plan-type");
  if (windows.length === 0 && !planType) return undefined;
  return {
    planType: typeof planType === "string" && planType ? planType : undefined,
    limitReached:
      windows.length > 0 ? windows.some((window) => window.usedPercent >= 100) : undefined,
    windows,
    observedAt: nowMs,
    source: "headers",
  };
}

// Codex 429 error body. Distinguishes plan exhaustion from transient
// per-minute limits, which Claude Code should retry rather than wait out.
function parseLimitErrorBody(text, nowMs = Date.now()) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return undefined;
  }
  const error = parsed?.error && typeof parsed.error === "object" ? parsed.error : parsed;
  if (!error || typeof error !== "object") return undefined;
  const code = String(error.type || error.code || "").toLowerCase();
  let kind;
  if (code === "usage_limit_reached") kind = "usage_limit";
  else if (code === "rate_limit_exceeded") kind = "transient";
  else return undefined;
  let resetAt = finiteNumber(error.resets_at);
  if (resetAt === undefined || resetAt <= 0) {
    const after = finiteNumber(error.resets_in_seconds);
    resetAt = after !== undefined && after >= 0 ? nowSeconds(nowMs) + after : undefined;
  }
  return {
    kind,
    planType: typeof error.plan_type === "string" ? error.plan_type : undefined,
    resetAt: resetAt !== undefined ? Math.round(resetAt) : undefined,
    message: typeof error.message === "string" ? error.message : undefined,
  };
}

// pi-ai collapses the Codex error to a message. The SSE transport produces
// "You have hit your ChatGPT usage limit ..." for any 429; the WebSocket
// transport produces "Codex error: <upstream message>".
function classifyStreamError(message) {
  const text = String(message || "");
  if (/usage_limit_reached|usage limit/i.test(text)) return "usage_limit";
  if (/rate[ _-]?limit/i.test(text)) return "transient";
  return undefined;
}

function decodeAccountId(token) {
  try {
    const parts = String(token).split(".");
    if (parts.length !== 3) return undefined;
    const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
    const accountId = payload?.[JWT_CLAIM_PATH]?.chatgpt_account_id;
    return typeof accountId === "string" && accountId ? accountId : undefined;
  } catch {
    return undefined;
  }
}

function activeWindows(snapshot, nowMs) {
  if (!snapshot) return [];
  const now = nowSeconds(nowMs);
  return snapshot.windows.filter((window) => window.resetAt > now);
}

function windowRate(window, nowMs) {
  const hoursUntilReset = Math.max(MIN_HOURS_UNTIL_RESET, (window.resetAt - nowMs / 1000) / 3600);
  const remaining = 1 - window.usedPercent / 100;
  return (remaining * (window.windowSeconds / 3600)) / hoursUntilReset;
}

// Remaining capacity per hour until reset, scaled by plan capacity. Zero when
// the snapshot says the account is exhausted. Windows that already reset are
// ignored, and an account without a usable snapshot counts as fully available.
function accountWeight(snapshot, nowMs, capacityTable = DEFAULT_PLAN_CAPACITY) {
  const capacity = capacityForPlan(capacityTable, snapshot?.planType);
  const windows = activeWindows(snapshot, nowMs);
  if (windows.length === 0) return capacity;
  if (snapshot.limitReached && windows.some((window) => window.usedPercent >= 100)) return 0;
  return capacity * Math.min(...windows.map((window) => windowRate(window, nowMs)));
}

// The window that blocks an exhausted account: the earliest reset among fully
// used windows, or the most used window when the API rounds below 100.
function exhaustedResetAt(snapshot, nowMs) {
  if (!snapshot?.limitReached) return undefined;
  const windows = activeWindows(snapshot, nowMs);
  if (windows.length === 0) return undefined;
  const full = windows.filter((window) => window.usedPercent >= 100);
  if (full.length > 0) return Math.min(...full.map((window) => window.resetAt));
  return windows.reduce((best, window) => (window.usedPercent > best.usedPercent ? window : best))
    .resetAt;
}

function windowLabel(window) {
  return window.windowSeconds <= FIVE_HOUR_MAX_SECONDS ? "5h" : "7d";
}

function windowClaim(window) {
  return window.windowSeconds <= FIVE_HOUR_MAX_SECONDS ? "five_hour" : "seven_day";
}

function representativeWindow(windows, rejected) {
  if (windows.length === 0) return undefined;
  if (rejected) {
    const exhausted = windows.filter((window) => window.usedPercent >= 100);
    const candidates = exhausted.length > 0 ? exhausted : windows;
    return candidates.reduce((best, window) => (window.resetAt < best.resetAt ? window : best));
  }
  return windows.reduce((best, window) => (window.usedPercent > best.usedPercent ? window : best));
}

// Anthropic unified rate-limit headers as Claude Code 2.1.274 reads them.
function rateLimitHeaders({ windows = [], rejected = false, resetAt, nowMs = Date.now() }) {
  const headers = {
    "anthropic-ratelimit-unified-status": rejected ? "rejected" : "allowed",
  };
  const active = windows.filter((window) => window.resetAt > nowSeconds(nowMs));
  const representative = representativeWindow(active, rejected);
  let reset = finiteNumber(resetAt);
  if (reset === undefined && representative) reset = representative.resetAt;
  if (rejected && reset === undefined) reset = nowSeconds(nowMs) + DEFAULT_LIMIT_FALLBACK_MS / 1000;
  if (representative) {
    headers["anthropic-ratelimit-unified-representative-claim"] = windowClaim(representative);
  } else if (rejected) {
    headers["anthropic-ratelimit-unified-representative-claim"] = "seven_day";
  }
  if (reset !== undefined) headers["anthropic-ratelimit-unified-reset"] = String(Math.round(reset));
  const byLabel = new Map();
  for (const window of active) {
    const label = windowLabel(window);
    const current = byLabel.get(label);
    if (!current || window.usedPercent > current.usedPercent) byLabel.set(label, window);
  }
  for (const [label, window] of byLabel) {
    headers[`anthropic-ratelimit-unified-${label}-utilization`] = (
      window.usedPercent / 100
    ).toFixed(4);
    headers[`anthropic-ratelimit-unified-${label}-reset`] = String(window.resetAt);
  }
  if (rejected) {
    headers["retry-after"] = String(Math.max(0, Math.round(reset) - nowSeconds(nowMs)));
    headers["x-should-retry"] = "false";
  }
  return headers;
}

async function fetchUsage({ token, accountId, fetch: fetchImpl = globalThis.fetch, timeoutMs }) {
  const response = await fetchImpl(CODEX_USAGE_URL, {
    method: "GET",
    headers: {
      authorization: `Bearer ${token}`,
      "chatgpt-account-id": accountId,
      accept: "application/json",
      "user-agent": "cc-openai-proxy",
    },
    signal: AbortSignal.timeout(timeoutMs ?? USAGE_FETCH_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`codex usage request failed with status ${response.status}`);
  }
  return response.json();
}

function weightedPick(candidates, random) {
  const total = candidates.reduce((sum, candidate) => sum + candidate.weight, 0);
  if (total <= 0) return candidates[0]?.account;
  let cursor = random() * total;
  for (const candidate of candidates) {
    cursor -= candidate.weight;
    if (cursor < 0) return candidate.account;
  }
  return candidates[candidates.length - 1].account;
}

/**
 * @param {object} options
 * @param {Array<{label: string, models: object, readCredential?: () => Promise<object|undefined>}>} options.accounts
 */
function createAccountPool(options) {
  const now = options.now ?? Date.now;
  const random = options.random ?? Math.random;
  const fetchImpl = options.fetch ?? globalThis.fetch;
  const log = options.log ?? (() => {});
  const usageTtlMs = options.usageTtlMs ?? DEFAULT_USAGE_TTL_MS;
  const stickyMs = options.stickyMs ?? DEFAULT_STICKY_MS;
  const limitFallbackMs = options.limitFallbackMs ?? DEFAULT_LIMIT_FALLBACK_MS;
  const capacityTable = options.capacityTable ?? DEFAULT_PLAN_CAPACITY;
  const provider = options.provider ?? CODEX_PROVIDER;
  const defaultModelId = options.defaultModelId;

  const accounts = options.accounts.map((entry, index) => ({
    index,
    label: entry.label,
    models: entry.models,
    readCredential: entry.readCredential,
    id: undefined,
    snapshot: undefined,
    limitedUntil: 0,
    transientUntil: 0,
    authFailedUntil: 0,
    usageFailedUntil: 0,
    usageSupported: undefined,
    refresh: undefined,
  }));
  const sessions = new Map();

  function catalogModel(account) {
    const models = account.models;
    return (
      (defaultModelId && models.getModel?.(provider, defaultModelId)) ||
      models.getModels?.(provider)?.[0]
    );
  }

  function logAccount(account, fields) {
    log({
      category: "account_pool",
      account: account.index,
      ...(account.snapshot?.planType ? { planType: account.snapshot.planType } : {}),
      ...fields,
    });
  }

  async function resolveAuth(account) {
    const model = catalogModel(account);
    if (!model) return undefined;
    const result = await account.models.getAuth(model);
    const token = result?.auth?.apiKey;
    if (typeof token !== "string" || token.trim() === "") return undefined;
    if (!account.id) {
      let id = decodeAccountId(token);
      if (!id && account.readCredential) {
        const credential = await account.readCredential().catch(() => undefined);
        if (typeof credential?.accountId === "string") id = credential.accountId;
      }
      // The usage endpoint needs the ChatGPT account id. Without one the
      // account still serves requests, with no usage snapshot.
      account.usageSupported = Boolean(id);
      account.id = id || `account-${account.index}`;
    }
    return token;
  }

  function mergeSnapshot(account, snapshot) {
    if (!snapshot) return;
    const previous = account.snapshot;
    account.snapshot = {
      ...snapshot,
      planType: snapshot.planType ?? previous?.planType,
      limitReached: snapshot.limitReached ?? previous?.limitReached ?? false,
      windows: snapshot.windows.length > 0 ? snapshot.windows : (previous?.windows ?? []),
    };
    const resetAt = exhaustedResetAt(account.snapshot, now());
    if (resetAt !== undefined) {
      account.limitedUntil = Math.max(account.limitedUntil, resetAt * 1000);
    }
  }

  async function refreshUsage(account, { force = false } = {}) {
    const age =
      account.snapshot?.source === "usage" ? now() - account.snapshot.observedAt : Infinity;
    if (!force && (age < usageTtlMs || account.usageFailedUntil > now())) return account.snapshot;
    if (account.refresh) return account.refresh;
    account.refresh = (async () => {
      try {
        const token = await resolveAuth(account);
        if (!token || !account.usageSupported) return account.snapshot;
        const json = await fetchUsage({ token, accountId: account.id, fetch: fetchImpl });
        const snapshot = parseUsageResponse(json, now());
        if (snapshot) {
          mergeSnapshot(account, snapshot);
          logAccount(account, {
            event: "usage_refreshed",
            limitReached: snapshot.limitReached,
            windows: snapshot.windows.map((window) => ({
              usedPercent: window.usedPercent,
              windowSeconds: window.windowSeconds,
              resetAt: window.resetAt,
            })),
          });
        }
        return account.snapshot;
      } catch (error) {
        account.usageFailedUntil = now() + USAGE_FAILURE_COOLDOWN_MS;
        logAccount(account, {
          event: "usage_refresh_failed",
          reason: error instanceof Error ? error.message : String(error),
        });
        return account.snapshot;
      } finally {
        account.refresh = undefined;
      }
    })();
    return account.refresh;
  }

  function isLimited(account) {
    return account.limitedUntil > now();
  }

  function availabilityAt(account) {
    const current = now();
    const candidates = [];
    if (account.limitedUntil > current) candidates.push(Math.floor(account.limitedUntil / 1000));
    const resetAt = exhaustedResetAt(account.snapshot, current);
    if (resetAt !== undefined) candidates.push(resetAt);
    return candidates.length > 0 ? Math.min(...candidates) : undefined;
  }

  function candidateWeight(account) {
    const current = now();
    if (
      isLimited(account) ||
      account.transientUntil > current ||
      account.authFailedUntil > current
    ) {
      return 0;
    }
    return accountWeight(account.snapshot, now(), capacityTable);
  }

  function pruneSessions() {
    if (sessions.size <= MAX_STICKY_SESSIONS) return;
    const oldest = [...sessions.entries()].sort((a, b) => a[1].lastUsed - b[1].lastUsed);
    for (const [key] of oldest.slice(0, sessions.size - MAX_STICKY_SESSIONS)) sessions.delete(key);
  }

  function bind(sessionKey, account) {
    if (!sessionKey) return;
    sessions.set(sessionKey, { index: account.index, lastUsed: now() });
    pruneSessions();
  }

  // Selection: sticky session binding first, weighted random otherwise. The
  // returned account has resolved credentials. Accounts whose credentials fail
  // are cooled down briefly so one broken login does not block the pool.
  async function select({ sessionKey, exclude } = {}) {
    const excluded = exclude ?? new Set();
    let eligible = [];
    for (const account of accounts) {
      if (excluded.has(account.index) || account.authFailedUntil > now()) continue;
      if (!account.snapshot) {
        await refreshUsage(account);
      } else if (
        account.snapshot.source !== "usage" ||
        now() - account.snapshot.observedAt >= usageTtlMs
      ) {
        void refreshUsage(account);
      }
      const weight = candidateWeight(account);
      if (weight > 0) eligible.push({ account, weight });
    }

    const bound = sessionKey ? sessions.get(sessionKey) : undefined;
    const sticky = bound && bound.lastUsed + stickyMs > now() ? bound.index : undefined;
    while (eligible.length > 0) {
      let chosen = eligible.find((candidate) => candidate.account.index === sticky)?.account;
      chosen ??= weightedPick(eligible, random);
      try {
        if (!(await resolveAuth(chosen))) throw new Error("no credentials");
      } catch (error) {
        chosen.authFailedUntil = now() + AUTH_FAILURE_COOLDOWN_MS;
        logAccount(chosen, {
          event: "auth_unavailable",
          reason: error instanceof Error ? error.message : String(error),
        });
        eligible = eligible.filter((candidate) => candidate.account !== chosen);
        continue;
      }
      bind(sessionKey, chosen);
      return chosen;
    }
    if (accounts.every((account) => account.authFailedUntil > now())) {
      const failure = new Error("no account has usable credentials");
      failure.code = "auth";
      throw failure;
    }
    return undefined;
  }

  function observeHeaders(account, headers) {
    const snapshot = parseCodexHeaders(headers, now());
    if (!snapshot) return;
    if (account.snapshot?.source === "usage" && now() - account.snapshot.observedAt < usageTtlMs) {
      // Header windows are per request and may lag; keep plan type only.
      account.snapshot.planType ??= snapshot.planType;
      return;
    }
    mergeSnapshot(account, snapshot);
  }

  // Records a usage-limit failure. A reset time from the error body wins;
  // the usage endpoint is asked for the windows either way, and a fixed
  // cooldown applies when neither source gives a reset time.
  async function markLimited(account, limit) {
    const current = now();
    let resetAt =
      limit?.resetAt !== undefined && limit.resetAt * 1000 > current ? limit.resetAt : undefined;
    let source = resetAt !== undefined ? "error" : "fallback";
    const snapshot = await refreshUsage(account, { force: true });
    if (limit?.planType && account.snapshot) account.snapshot.planType = limit.planType;
    if (resetAt === undefined) {
      const fromUsage = exhaustedResetAt(snapshot, current);
      if (fromUsage !== undefined) {
        resetAt = fromUsage;
        source = "usage";
      }
    }
    account.limitedUntil = Math.max(
      current + 1000,
      resetAt !== undefined ? resetAt * 1000 : current + limitFallbackMs,
    );
    if (account.snapshot) account.snapshot.limitReached = true;
    logAccount(account, {
      event: "usage_limit",
      resetAt: Math.floor(account.limitedUntil / 1000),
      source,
    });
    for (const [key, binding] of sessions) {
      if (binding.index === account.index) sessions.delete(key);
    }
  }

  function markTransient(account, retryAfterMs) {
    account.transientUntil = Math.max(
      account.transientUntil,
      now() + (retryAfterMs ?? TRANSIENT_COOLDOWN_MS),
    );
    logAccount(account, { event: "transient_rate_limit" });
  }

  // State for the 429 answer when nothing can serve: the earliest reset across
  // accounts and the windows of the account that resets first. A transient
  // limit is reported separately so the client retries instead of waiting.
  function exhaustedState() {
    let best;
    let transientUntil;
    const current = now();
    for (const account of accounts) {
      if (account.transientUntil > current) {
        transientUntil = Math.min(transientUntil ?? Infinity, account.transientUntil);
      }
      const at = availabilityAt(account);
      if (at === undefined) continue;
      if (!best || at < best.resetAt) best = { resetAt: at, account };
    }
    const plans = [
      ...new Set(accounts.map((account) => account.snapshot?.planType).filter(Boolean)),
    ];
    return {
      resetAt: best?.resetAt,
      windows: best?.account.snapshot?.windows ?? [],
      plans,
      accountCount: accounts.length,
      transientUntil,
    };
  }

  async function probeAuth() {
    let usable = false;
    let lastError;
    for (const account of accounts) {
      try {
        if (await resolveAuth(account)) usable = true;
      } catch (error) {
        lastError = error;
      }
    }
    if (!usable && lastError) throw lastError;
    return usable;
  }

  function primaryModels() {
    return accounts[0]?.models;
  }

  return {
    accounts,
    now,
    select,
    refreshUsage,
    observeHeaders,
    markLimited,
    markTransient,
    exhaustedState,
    probeAuth,
    primaryModels,
    availabilityAt,
    candidateWeight,
    sessions,
  };
}

export {
  CODEX_USAGE_URL,
  DEFAULT_PLAN_CAPACITY,
  accountWeight,
  capacityForPlan,
  classifyStreamError,
  createAccountPool,
  decodeAccountId,
  fetchUsage,
  parseCodexHeaders,
  parseLimitErrorBody,
  parsePlanCapacity,
  parseUsageResponse,
  rateLimitHeaders,
};
