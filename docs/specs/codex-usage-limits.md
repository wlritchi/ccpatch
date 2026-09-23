# Codex usage limits and account pooling in cc-openai-proxy

Status: draft, 2026-09-22.

## Problem

When a ChatGPT subscription reaches its Codex usage limit, the proxy today
returns a generic upstream failure. Claude Code renders it as
`API Error: 502 Codex error: The usage limit has been reached. This is a
server-side issue, usually temporary ...`. Claude Code has a dedicated
usage-limit path for Anthropic responses: it shows a limit dialog with the
reset time, can wait for the reset, and can auto-continue the task. That path
never triggers for the proxy because the proxy does not speak the Anthropic
rate-limit protocol.

Codex subscriptions also differ from Claude subscriptions in window shape.
A `prolite` plan observed on 2026-09-22 has one weekly window and no 5-hour
window, so a reset can be days away. Waiting is often not viable, but a second
subscription can be.

## Findings

### Claude Code (2.1.274) usage-limit protocol

Established by reading the patched binary.

- A 429 response is treated as a usage limit only when the client is in
  claude.ai OAuth mode (`St()`), which ccpatch preserves because the OpenAI
  provider uses a per-provider client rather than `ANTHROPIC_AUTH_TOKEN`.
- The 429 must carry `anthropic-ratelimit-unified-representative-claim` or
  `anthropic-ratelimit-unified-overage-status`. Claude Code then reads
  `anthropic-ratelimit-unified-reset` (unix seconds) as `resetsAt` and
  `anthropic-ratelimit-unified-status` (`rejected`).
- `representative-claim` selects the wording: `five_hour` is "session limit",
  `seven_day` is "weekly limit". Other values suppress the reset text.
- With `anthropic-ratelimit-unified-status: rejected` present, Claude Code
  does not retry the 429. `x-should-retry: false` also prevents SDK retries.
- Auto-continue (`autoContinueAtUsageLimit`) only arms when the reset is at
  most 24 hours away. The dialog's manual wait has no such cap.
- On successful responses Claude Code reads `anthropic-ratelimit-unified-status`
  (`allowed`, `allowed_warning`), `-representative-claim`, `-reset`,
  `-5h-utilization`, `-5h-reset`, `-7d-utilization`, `-7d-reset`. Utilization
  is a fraction in `[0, 1]`. It derives "You've used N% of your weekly limit"
  warnings from these itself.
- An SSE `error` event mid-stream becomes an `APIError` without a status and
  is never treated as a usage limit. A usage limit must therefore be reported
  as a real HTTP 429 before the response head is written.

### pi-ai (0.85.1) `openai-codex` provider

- The Codex backend answers an exhausted account with HTTP 429 and body
  `{"error":{"type":"usage_limit_reached","message":"The usage limit has been
  reached","plan_type":"prolite","resets_at":1790414443,
  "resets_in_seconds":350094}}`. The response also carries
  `x-codex-plan-type`, `x-codex-primary-used-percent`,
  `x-codex-primary-window-minutes`, `x-codex-primary-reset-at`,
  `x-codex-primary-reset-after-seconds` and the `x-codex-secondary-*`
  equivalents (blank or zero when the plan has no second window).
- On the SSE transport pi-ai reduces this to the string
  `You have hit your ChatGPT usage limit (prolite plan). Try again in ~N min.`
  On the WebSocket transport (tried first under `transport: auto`) the error
  frame becomes `Codex error: The usage limit has been reached` with no reset
  information. Neither transport exposes the structured body to callers.
- pi-ai does expose `options.fetch` (SSE request function) and
  `options.onResponse({status, headers})` (SSE only). The proxy can capture the
  429 body and `x-codex-*` headers through these on SSE requests.
- Both transports fail before the `start` event, so the proxy can inspect the
  first stream event before it writes the response head.
- pi-ai keys credentials by provider id inside a `CredentialStore`. Multiple
  accounts for the same provider are not modelled. A second `Models` instance
  with its own store gives a second account with no shared state; the
  WebSocket session cache is keyed by `(sessionId, accountId)` so instances do
  not collide.
- The pi CLI stores one credential per provider in `$PI_CODING_AGENT_DIR/auth.json`
  (`~/.pi/agent/auth.json` by default). A second account is created with
  `PI_CODING_AGENT_DIR=~/.pi/agent-2 pi` followed by `/login`. The pi
  changelog through 0.87.0 lists no multi-account feature.

### Querying Codex usage

- `GET https://chatgpt.com/backend-api/wham/usage` with `Authorization:
  Bearer <access token>` and `chatgpt-account-id: <id>` returns the plan and
  windows without consuming quota. Observed shape:
  `plan_type`, `rate_limit.{allowed,limit_reached}`,
  `rate_limit.primary_window.{used_percent,limit_window_seconds,
  reset_after_seconds,reset_at}`, `rate_limit.secondary_window` (nullable),
  `credits.{has_credits,unlimited,balance}`, `model_usage.<model>.available`.
  The Codex CLI (0.155.1) uses this endpoint for its status display.
- CLIProxyAPI does not call `/wham/usage`. It observes the `x-codex-*` headers
  on ordinary responses and the `codex.rate_limits` WebSocket frames, cools an
  account down using `resets_at` / `resets_in_seconds` from the
  `usage_limit_reached` body, and fails over to another account. Its
  Anthropic-format error path forwards only `Retry-After`; it does not emit the
  `anthropic-ratelimit-unified-*` headers.

## Design

### Account pool

The proxy manages a list of accounts. Each account is one pi auth file and one
pi-ai `Models` instance backed by a `CredentialStore` for that file.

- `CC_OPENAI_AUTH_FILES` lists auth files separated by the platform path
  delimiter. `CC_OPENAI_AUTH_FILE` / `PI_AUTH_FILE` remain the single-file
  configuration. An explicit `CC_OPENAI_CODEX_TOKEN` is a single static
  account, as today.
- Account identity is the ChatGPT account id from the access token JWT claim,
  falling back to the credential's `accountId`, then the file path.
- Each account keeps a usage snapshot: plan type, per-window `usedPercent`,
  `windowSeconds`, `resetAt`, and a `limitedUntil` timestamp when the account
  is known to be exhausted.

### Usage snapshots

Sources, in priority order for a given field:

1. The `usage_limit_reached` error body captured through `options.fetch`.
2. `x-codex-*` headers captured through `options.onResponse` (SSE only).
3. `/wham/usage`, fetched with the account's current access token.

The pool refreshes `/wham/usage` lazily: when a request is being routed and the
snapshot is older than `CC_OPENAI_USAGE_TTL_MS` (default 300000), a refresh
runs in the background. A missing snapshot is fetched before the first
selection. A usage-limit error forces an immediate refresh when the error did
not carry a reset time. Refresh failures are logged and leave the previous
snapshot in place; they never fail a request.

### Weighting

For each window `w` of an account with a fresh snapshot:

    rate(w) = (1 - usedPercent/100) * (windowSeconds / 3600) / hoursUntilReset

The account weight is `capacity(plan) * min over windows rate(w)`, where
`capacity` is a per-plan multiplier from `CC_OPENAI_PLAN_CAPACITY`
(`plan=multiplier,...`) over a built-in default table. An account with
`limitedUntil` in the future, or with `limit_reached` in its snapshot, has
weight 0 and is unavailable. An account with no snapshot has weight 1.

Two accounts with the same window type, the same remaining fraction and
capacities of 20 and 10 with resets in 6 and 3 hours get equal weights, which
is the requested behaviour. Windows of different lengths are compared by
assuming quota scales with window length; this is a stated heuristic.

### Selection and stickiness

Selection is weighted random over available accounts. A session (the
`X-Claude-Code-Session-Id` header, falling back to `x-client-request-id`, then
a per-process id) is bound to the selected account for
`CC_OPENAI_SESSION_STICKY_MS` (default 6 hours, refreshed on use). A bound
account that becomes unavailable is replaced and the session rebound.

A request whose first stream event is a usage-limit error marks that account
unavailable and is retried on the next available account. The retry reuses the
same converted context; no partial output has been sent because the head is
not written until the first event.

### Client-facing responses

When no account is available, the proxy answers 429 with:

- `anthropic-ratelimit-unified-status: rejected`
- `anthropic-ratelimit-unified-representative-claim`: `five_hour` when the
  binding window is at most 6 hours, otherwise `seven_day`
- `anthropic-ratelimit-unified-reset`: the earliest known reset among accounts
- `retry-after`: seconds until that reset
- `x-should-retry: false`
- per-window `anthropic-ratelimit-unified-{5h,7d}-{utilization,reset}` when
  known
- body `{"type":"error","error":{"type":"rate_limit_error","message":...}}`

Windows map to Anthropic names by length: at most 6 hours is `5h`, more than
6 hours is `7d`.

Successful responses carry `anthropic-ratelimit-unified-status: allowed`, the
representative claim and reset for the most-used window, and the per-window
utilization and reset headers from the serving account's snapshot.
`CC_OPENAI_USAGE_HEADERS=0` disables these.

Other errors that occur before the first stream event become JSON error
responses with a real status (502 for upstream failures, 499 for client
aborts) instead of a 200 SSE stream that carries an error event.

### Out of scope

- Editing pi's own account switching; the pi CLI keeps one account per
  directory.
- Modelling Codex credits (`credits.balance`) as extra capacity.
- Rewriting the Anthropic overage protocol; `overage-*` headers are not sent.
