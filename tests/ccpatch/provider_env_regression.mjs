#!/usr/bin/env node

import assert from "node:assert/strict";

const transportKey = "CLAUDE_CODE_PROVIDER_ENV_TRANSIENT";
const providerKeys = [
  "ANTHROPIC_BASE_URL",
  "ANTHROPIC_MODEL",
  "ANTHROPIC_DEFAULT_SONNET_MODEL",
  "ANTHROPIC_AUTH_TOKEN",
  "ANTHROPIC_API_KEY",
  "CC_KIMI_AUTH_TOKEN",
  "CC_ZAI_AUTH_TOKEN",
  "CC_MINIMAX_AUTH_TOKEN",
  "CC_OPENAI_PROXY_AUTH_TOKEN",
  "CC_OPENAI_AVAILABLE",
];
const requesterProcessEnv = {
  ANTHROPIC_BASE_URL: "https://requester.example",
  ANTHROPIC_MODEL: "requester-model",
  ANTHROPIC_AUTH_TOKEN: "requester-token",
  CC_KIMI_AUTH_TOKEN: "requester-a",
  CC_OPENAI_PROXY_AUTH_TOKEN: "openai-requester-token",
  CC_OPENAI_AVAILABLE: "1",
};
const persistedJobEnv = {};
const expectedConsumerEnv = {
  ANTHROPIC_BASE_URL: "https://requester.example",
  ANTHROPIC_MODEL: "requester-model",
  ANTHROPIC_DEFAULT_SONNET_MODEL: undefined,
  ANTHROPIC_AUTH_TOKEN: "requester-token",
  ANTHROPIC_API_KEY: undefined,
  CC_KIMI_AUTH_TOKEN: "requester-a",
  CC_ZAI_AUTH_TOKEN: undefined,
  CC_MINIMAX_AUTH_TOKEN: undefined,
  CC_OPENAI_PROXY_AUTH_TOKEN: "openai-requester-token",
  CC_OPENAI_AVAILABLE: "1",
};

function requireProviderEnv(providerEnv) {
  assert.ok(providerEnv && typeof providerEnv === "object");
  assert.ok(!Array.isArray(providerEnv));
  for (const [key, value] of Object.entries(providerEnv)) {
    assert.ok(providerKeys.includes(key));
    assert.ok(value === null || typeof value === "string");
  }
  return providerEnv;
}

function snapshotRequesterTransport(processEnv) {
  return Object.fromEntries(providerKeys.map((key) => [key, processEnv[key] ?? null]));
}

function retainProviderEnv(providerEnv) {
  return Object.freeze({ ...requireProviderEnv(providerEnv) });
}

function buildWorkerEnv(processEnv, providerEnv) {
  const retainedPayload = retainProviderEnv(providerEnv);
  const workerEnv = { ...processEnv };
  workerEnv[transportKey] = JSON.stringify(retainedPayload);
  applyProviderEnv(workerEnv, retainedPayload);
  nativeProviderScrubs(workerEnv, persistedJobEnv);
  applyProviderEnv(workerEnv, retainedPayload);
  return workerEnv;
}

function captureTransport(workerEnv) {
  const serialized = workerEnv[transportKey];
  delete workerEnv[transportKey];
  if (serialized === undefined) return null;
  return retainProviderEnv(JSON.parse(serialized));
}

function applyWorkerFinal(workerEnv, providerEnv) {
  if (providerEnv !== null) return applyProviderEnv(workerEnv, providerEnv);
  if (workerEnv.CLAUDE_CODE_SESSION_KIND === "bg") {
    throw Object.assign(
      new Error(
        "Background provider environment transient payload is unavailable; dispatch again from the current Claude Code session",
      ),
      { code: "EPROVIDERENV" },
    );
  }
}

function applyProviderEnv(targetEnv, providerEnv) {
  requireProviderEnv(providerEnv);
  for (const key of providerKeys) delete targetEnv[key];
  for (const [key, value] of Object.entries(providerEnv)) {
    if (value !== null) targetEnv[key] = value;
  }
}

function nativeProviderScrubs(targetEnv, jobEnv) {
  for (const key of providerKeys) {
    if (!jobEnv[key]) delete targetEnv[key];
  }
}

function mutateProviderEnv(targetEnv, phase) {
  targetEnv.ANTHROPIC_BASE_URL = `https://${phase}.example`;
  targetEnv.ANTHROPIC_MODEL = `${phase}-model`;
  targetEnv.ANTHROPIC_DEFAULT_SONNET_MODEL = `${phase}-sonnet`;
  targetEnv.ANTHROPIC_AUTH_TOKEN = `${phase}-token`;
  targetEnv.ANTHROPIC_API_KEY = `${phase}-api-key`;
}

function assertConsumerEnv(targetEnv, phase) {
  assert.deepEqual(
    Object.fromEntries(providerKeys.map((key) => [key, targetEnv[key]])),
    expectedConsumerEnv,
    phase,
  );
  assert.equal(targetEnv.UNRELATED, "preserved", phase);
  assert.equal(targetEnv[transportKey], undefined, phase);
}

async function simulateWorkerPath(path) {
  const events = [];
  const requesterPayload = snapshotRequesterTransport(requesterProcessEnv);
  events.push("requester-capture");
  const workerEnv = buildWorkerEnv({ UNRELATED: "preserved" }, requesterPayload);
  events.push(`${path}-transport`);

  const workerPayload = captureTransport(workerEnv);
  events.push("worker-capture");
  assert.ok(Object.isFrozen(workerPayload));
  assert.notStrictEqual(workerPayload, requesterPayload);

  mutateProviderEnv(workerEnv, "before-preAction");
  events.push("before-preAction-mutation");
  applyProviderEnv(workerEnv, workerPayload);
  events.push("I09-post-scrub-reapply");
  assertConsumerEnv(workerEnv, `${path}: I09 post-scrub`);

  mutateProviderEnv(workerEnv, "settings-initializer");
  events.push("settings-initializer");
  applyProviderEnv(workerEnv, workerPayload);
  events.push("preAction-reapply");
  assertConsumerEnv(workerEnv, `${path}: preAction`);

  mutateProviderEnv(workerEnv, "Ko");
  events.push("Ko");
  applyProviderEnv(workerEnv, workerPayload);
  events.push("operational-reapply");
  assertConsumerEnv(workerEnv, `${path}: operational`);

  await Promise.resolve().then(async () => {
    mutateProviderEnv(workerEnv, "delayed-Ko");
    events.push("delayed-Ko");
    applyProviderEnv(workerEnv, workerPayload);
    events.push("delayed-reapply");
    await Promise.resolve();
    events.push("telemetry-init");
    assertConsumerEnv(workerEnv, `${path}: delayed`);
  });

  assert.deepEqual(events, [
    "requester-capture",
    `${path}-transport`,
    "worker-capture",
    "before-preAction-mutation",
    "I09-post-scrub-reapply",
    "settings-initializer",
    "preAction-reapply",
    "Ko",
    "operational-reapply",
    "delayed-Ko",
    "delayed-reapply",
    "telemetry-init",
  ]);
}

for (const path of ["cold", "claimed", "respawn", "retry"]) {
  await simulateWorkerPath(path);
}

const foregroundEnv = {};
assert.equal(captureTransport(foregroundEnv), null);
assert.doesNotThrow(() => applyWorkerFinal(foregroundEnv, null));
const missingWorkerEnv = { CLAUDE_CODE_SESSION_KIND: "bg" };
const missingWorkerPayload = captureTransport(missingWorkerEnv);
assert.throws(
  () => applyWorkerFinal(missingWorkerEnv, missingWorkerPayload),
  (error) =>
    error.code === "EPROVIDERENV" &&
    error.message.includes("dispatch again from the current Claude Code session"),
);

const rotatingWorkerEnv = {};
const requesterA = snapshotRequesterTransport({
  CC_KIMI_AUTH_TOKEN: "token-a",
});
applyProviderEnv(rotatingWorkerEnv, requesterA);
assert.equal(rotatingWorkerEnv.CC_KIMI_AUTH_TOKEN, "token-a");
const requesterB = snapshotRequesterTransport({
  CC_KIMI_AUTH_TOKEN: "token-b",
});
applyProviderEnv(rotatingWorkerEnv, requesterB);
assert.equal(rotatingWorkerEnv.CC_KIMI_AUTH_TOKEN, "token-b");
const requesterOpenAI = snapshotRequesterTransport({
  CC_OPENAI_PROXY_AUTH_TOKEN: "openai-token-a",
  CC_OPENAI_AVAILABLE: "1",
});
applyProviderEnv(rotatingWorkerEnv, requesterOpenAI);
assert.equal(rotatingWorkerEnv.CC_OPENAI_PROXY_AUTH_TOKEN, "openai-token-a");
assert.equal(rotatingWorkerEnv.CC_OPENAI_AVAILABLE, "1");
const requesterOpenAIRotated = snapshotRequesterTransport({
  CC_OPENAI_PROXY_AUTH_TOKEN: "openai-token-b",
});
applyProviderEnv(rotatingWorkerEnv, requesterOpenAIRotated);
assert.equal(rotatingWorkerEnv.CC_OPENAI_PROXY_AUTH_TOKEN, "openai-token-b");
assert.equal(rotatingWorkerEnv.CC_OPENAI_AVAILABLE, undefined);
const requesterUnset = snapshotRequesterTransport({});
applyProviderEnv(rotatingWorkerEnv, requesterUnset);
assert.equal(rotatingWorkerEnv.CC_KIMI_AUTH_TOKEN, undefined);
assert.equal(rotatingWorkerEnv.CC_OPENAI_PROXY_AUTH_TOKEN, undefined);
assert.equal(rotatingWorkerEnv.CC_OPENAI_AVAILABLE, undefined);

console.log("provider environment transient transport ordering: ok");
