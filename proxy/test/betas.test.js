import test from "node:test";
import assert from "node:assert/strict";

import { requestBetas, warnUnknownBetas } from "../bin/cc-openai-proxy.js";

const request = (headers = {}) => ({ headers, url: "/v1/messages" });

test("requestBetas merges the header and the body, trimmed and deduplicated", () => {
  const req = request({
    "anthropic-beta": "effort-2025-11-24, fast-mode-2026-02-01,,effort-2025-11-24",
  });
  assert.deepEqual(
    requestBetas(req, { betas: ["context-1m-2025-08-07", 7, "fast-mode-2026-02-01"] }),
    ["effort-2025-11-24", "fast-mode-2026-02-01", "context-1m-2025-08-07"],
  );
  assert.deepEqual(requestBetas(request(), {}), []);
  assert.deepEqual(
    requestBetas(request({ "anthropic-beta": ["a-2026-01-01", "b-2026-01-01"] }), {}),
    ["a-2026-01-01", "b-2026-01-01"],
  );
});

test("warnUnknownBetas logs each unknown beta once per process", () => {
  const events = [];
  const reported = new Set();
  const log = (fields) => events.push(fields);
  const req = request({
    "anthropic-beta": "effort-2025-11-24,dangerous-tool-use-2026-09-03,new-protocol-2026-10-01",
  });
  assert.deepEqual(warnUnknownBetas(req, {}, reported, log), ["new-protocol-2026-10-01"]);
  assert.deepEqual(events, [
    { category: "unknown_beta", beta: "new-protocol-2026-10-01", path: "/v1/messages" },
  ]);
  assert.deepEqual(warnUnknownBetas(req, {}, reported, log), []);
  assert.equal(events.length, 1);
  assert.deepEqual(warnUnknownBetas(req, { betas: ["other-2026-10-02"] }, reported, log), [
    "other-2026-10-02",
  ]);
});
