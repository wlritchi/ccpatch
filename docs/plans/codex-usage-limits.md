# Implementation plan: Codex usage limits and account pooling

Spec: `docs/specs/codex-usage-limits.md`.

## Steps

1. `proxy/bin/codex-accounts.js` (new module)
   - `parseUsageResponse(json)` and `parseCodexHeaders(headers)` produce a
     normalized `UsageSnapshot`.
   - `accountWeight(snapshot, now, capacityTable)` implements the weighting.
   - `classifyStreamError(message)` recognizes pi-ai usage-limit messages on
     both transports.
   - `createAccountPool({authFiles, loadModels, fetch, now, ...})` owns the
     accounts, snapshots, sticky sessions, selection, and failover state.
   - `anthropicRateLimitHeaders(snapshot|pool, {rejected})` builds the
     `anthropic-ratelimit-unified-*` header set.
2. `proxy/bin/cc-openai-proxy.js`
   - Replace the single `loadModels()` with the pool; keep `probeOpenAiAuth`
     semantics (usable when any account resolves auth).
   - `handleMessages`: select account, build options with `fetch`/`onResponse`
     capture, await the first stream event, fail over on usage limit, write
     the head with usage headers, then stream.
   - Read `x-claude-code-session-id` for the session id.
   - 429 rendering with the header set and `x-should-retry: false`.
3. Tests in `proxy/test/accounts.test.js` and additions to existing suites,
   all with fake `Models` objects (no network).
4. README: configuration, second-account login recipe, limit behaviour.
5. Validation: `npm test`, `nix develop -c oxfmt`, `nix build .#cc-openai-proxy`,
   run the built proxy on the host against the currently exhausted account and
   check the 429 with `curl`, then run the patched `claude -p` against it.
