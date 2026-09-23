import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer, request } from "node:http";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  assertInboundAuth,
  errorType,
  extractInboundBearer,
  logError,
  probeOpenAiAuth,
  route,
  serverErrorDiagnostic,
} from "../bin/cc-openai-proxy.js";

function req(headers = {}) {
  return { headers };
}

async function unusedPort() {
  const server = createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.notEqual(address, null);
  assert.equal(typeof address, "object");
  const port = address.port;
  server.close();
  await once(server, "close");
  return port;
}

function get(port, path, host, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = request(
      {
        host: "127.0.0.1",
        port,
        path,
        headers: { host, ...headers },
      },
      (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          resolve({
            status: res.statusCode,
            body: Buffer.concat(chunks).toString("utf8"),
          });
        });
      },
    );
    req.on("error", reject);
    req.end();
  });
}

function post(port, path, host, headers, body) {
  return new Promise((resolve, reject) => {
    const req = request(
      {
        host: "127.0.0.1",
        port,
        path,
        method: "POST",
        headers: {
          host,
          "content-type": "application/json",
          "content-length": Buffer.byteLength(body),
          ...headers,
        },
      },
      (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          resolve({
            status: res.statusCode,
            body: Buffer.concat(chunks).toString("utf8"),
          });
        });
      },
    );
    req.on("error", reject);
    req.end(body);
  });
}

test("extractInboundBearer reads Authorization then x-api-key, trims, defaults empty", () => {
  assert.equal(extractInboundBearer(req({ authorization: "Bearer  secret " })), "secret");
  assert.equal(extractInboundBearer(req({ "x-api-key": "  key123 " })), "key123");
  assert.equal(extractInboundBearer(req({})), "");
  // Authorization wins over x-api-key when both present.
  assert.equal(extractInboundBearer(req({ authorization: "Bearer a", "x-api-key": "b" })), "a");
});

test("assertInboundAuth fails secure without the loaded bearer", () => {
  assert.throws(
    () => assertInboundAuth(req({ authorization: "Bearer anything" })),
    (error) => error.status === 401,
  );
});

test("assertInboundAuth enforces the loaded bearer via either header", () => {
  assert.doesNotThrow(() => assertInboundAuth(req({ authorization: "Bearer s3cret" }), "s3cret"));
  assert.doesNotThrow(() => assertInboundAuth(req({ "x-api-key": "s3cret" }), "s3cret"));
  assert.throws(
    () => assertInboundAuth(req({ authorization: "Bearer wrong" }), "s3cret"),
    (error) => error.status === 401,
  );
  assert.throws(
    () => assertInboundAuth(req({}), "s3cret"),
    (error) => error.status === 401,
  );
});

test("probeOpenAiAuth resolves a real descriptor and shares an in-flight probe", async () => {
  const model = { id: "gpt-5.6-sol", provider: "openai-codex" };
  let calls = 0;
  let resolveAuth;
  const auth = new Promise((resolve) => {
    resolveAuth = resolve;
  });
  const load = async () => ({
    getModel(provider, id) {
      assert.equal(provider, "openai-codex");
      assert.equal(id, "gpt-5.6-sol");
      return model;
    },
    async getAuth(argument) {
      assert.equal(argument, model);
      calls += 1;
      return auth;
    },
  });
  const first = probeOpenAiAuth(load);
  const second = probeOpenAiAuth(load);
  await Promise.resolve();
  assert.equal(calls, 1);
  resolveAuth({ auth: { apiKey: "upstream-secret" } });
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
});

test("probeOpenAiAuth distinguishes missing credentials from operational failure", async () => {
  const model = { id: "gpt-5.6-sol", provider: "openai-codex" };
  assert.equal(
    await probeOpenAiAuth(async () => ({
      getModel: () => model,
      getAuth: async (argument) => {
        assert.equal(argument, model);
        return undefined;
      },
    })),
    false,
  );

  const secret = "private-upstream-failure";
  await assert.rejects(
    probeOpenAiAuth(async () => ({
      getModel: () => model,
      getAuth: async () => {
        const error = new Error(secret);
        error.code = "oauth";
        throw error;
      },
    })),
    (error) => {
      assert.equal(error.status, 503);
      assert.equal(error.message, "authentication capability probe failed");
      assert.equal(error.diagnostic.category, "capability_error");
      assert.equal(error.diagnostic.code, "oauth");
      return true;
    },
  );
});

test("probeOpenAiAuth rejects malformed or blank successful auth results", async () => {
  const model = { id: "gpt-5.6-sol", provider: "openai-codex" };
  for (const result of [
    null,
    {},
    { auth: {} },
    { auth: { apiKey: 7 } },
    { auth: { apiKey: "" } },
    { auth: { apiKey: "   " } },
  ]) {
    await assert.rejects(
      probeOpenAiAuth(async () => ({
        getModel: () => model,
        getAuth: async () => result,
      })),
      (error) => {
        assert.equal(error.status, 503);
        assert.equal(error.diagnostic.category, "capability_error");
        assert.equal(error.diagnostic.code, "malformed_auth_result");
        return true;
      },
    );
  }
});

test("probeOpenAiAuth accepts the installed pi-ai Models API shape", async () => {
  const { createModels } = await import("@earendil-works/pi-ai");
  const { openaiCodexProvider } = await import("@earendil-works/pi-ai/providers/openai-codex");
  const models = createModels();
  models.setProvider(openaiCodexProvider());
  const model = models.getModel("openai-codex", "gpt-5.6-sol");
  assert.equal(model?.provider, "openai-codex");
  assert.equal(await models.getAuth(model), undefined);

  const astra = models.getModel("openai-codex", "gpt-6-astra");
  assert.equal(astra?.contextWindow, 272000);
  assert.deepEqual(astra?.cost, {
    input: 10,
    output: 50,
    cacheRead: 1,
    cacheWrite: 12.5,
    tiers: [
      {
        inputTokensAbove: 272000,
        input: 20,
        output: 75,
        cacheRead: 2,
        cacheWrite: 25,
      },
    ],
  });
  for (const [id, cost] of [
    ["gpt-6-sol", { input: 2, output: 10, cacheRead: 0.2, cacheWrite: 2.5 }],
    ["gpt-6-luna", { input: 0.1, output: 0.5, cacheRead: 0.01, cacheWrite: 0.125 }],
  ]) {
    const model = models.getModel("openai-codex", id);
    assert.equal(model?.contextWindow, 272000, id);
    const { tiers, ...base } = model?.cost ?? {};
    assert.deepEqual(base, cost, id);
  }
});

test("capability route sanitizes operational probe failures", async (t) => {
  const secret = "refresh-token-secret";
  let output = "";
  t.mock.method(process.stderr, "write", (chunk) => {
    output += chunk;
    return true;
  });
  const request = {
    method: "GET",
    url: "/capabilities",
    headers: { authorization: "Bearer proxy-token" },
  };
  let status;
  let body;
  const response = {
    headersSent: false,
    writeHead(value) {
      status = value;
      this.headersSent = true;
    },
    end(value) {
      body = value;
    },
  };
  await route(request, response, "proxy-token", async () => {
    const error = new Error(secret);
    error.status = 503;
    throw error;
  });
  assert.equal(status, 503);
  assert.deepEqual(JSON.parse(body), {
    type: "error",
    error: {
      type: "api_error",
      message: "authentication capability probe failed",
    },
  });
  assert.equal(body.includes(secret), false);
  assert.deepEqual(JSON.parse(output), {
    category: "capability_error",
    method: "GET",
    pathname: "/capabilities",
    status: 503,
    errorType: "api_error",
    code: "probe_failed",
  });
  assert.equal(output.includes(secret), false);
});

test("errorType maps status classes for the Anthropic error envelope", () => {
  assert.equal(errorType(500), "api_error");
  assert.equal(errorType(502), "api_error");
  assert.equal(errorType(401), "authentication_error");
  assert.equal(errorType(403), "authentication_error");
  assert.equal(errorType(400), "invalid_request_error");
  assert.equal(errorType(404), "invalid_request_error");
});

test("server creates a bearer before listen and gates all routes except health", async (t) => {
  const port = await unusedPort();
  const directory = await mkdtemp(join(tmpdir(), "cc-openai-proxy-test-"));
  const authFile = join(directory, "private", "bearer");
  const proxyPath = fileURLToPath(new URL("../bin/cc-openai-proxy.js", import.meta.url));
  const child = spawn(
    process.execPath,
    [proxyPath, "--host", "127.0.0.1", "--port", String(port), "--auth-token-file", authFile],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  let output = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    output += chunk;
  });
  child.stderr.on("data", (chunk) => {
    output += chunk;
  });
  t.after(async () => {
    if (child.exitCode !== null) return;
    child.kill();
    await once(child, "exit");
  });

  await new Promise((resolve, reject) => {
    const onData = (chunk) => {
      if (chunk.includes('"category":"server_started"')) {
        child.stderr.off("data", onData);
        resolve();
      }
    };
    child.stderr.on("data", onData);
    child.once("exit", (code) =>
      reject(new Error(`proxy exited before startup with code ${code}`)),
    );
  });

  const token = (await readFile(authFile, "utf8")).trim();
  const malformedHost = "host-secret.invalid:bad-port";
  const querySecret = "query-secret-value";
  assert.equal((await get(port, `/health?token=${querySecret}`, malformedHost)).status, 200);
  assert.equal((await get(port, "/", "localhost")).status, 401);
  assert.equal((await get(port, "/capabilities", "localhost")).status, 401);
  assert.equal((await get(port, "/v1/models", "localhost")).status, 401);
  assert.equal(
    (await get(port, "/", "localhost", { authorization: `Bearer ${token}` })).status,
    200,
  );
  const capabilities = await get(port, "/capabilities", "localhost", {
    authorization: `Bearer ${token}`,
  });
  assert.equal(capabilities.status, 200);
  assert.deepEqual(Object.keys(JSON.parse(capabilities.body)), ["openaiAuthUsable"]);
  assert.equal(typeof JSON.parse(capabilities.body).openaiAuthUsable, "boolean");
  const longCountBody = JSON.stringify({
    model: "gpt-5.6-sol",
    messages: [{ role: "user", content: "x".repeat(1024 * 1024) }],
  });
  const countTokensPromise = post(
    port,
    "/v1/messages/count_tokens",
    "localhost",
    { authorization: `Bearer ${token}` },
    longCountBody,
  );
  await new Promise((resolve) => setTimeout(resolve, 100));
  const healthWhileCounting = await Promise.race([
    get(port, "/health", "localhost"),
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error("health blocked by tokenization")), 1000),
    ),
  ]);
  assert.equal(healthWhileCounting.status, 200);
  const countTokens = await countTokensPromise;
  assert.equal(countTokens.status, 200, countTokens.body);
  assert.ok(JSON.parse(countTokens.body).input_tokens > 100_000);
  assert.equal(child.exitCode, null);
  assert.equal(output.includes(malformedHost), false);
  assert.equal(output.includes(querySecret), false);
});

test("logError writes only allowlisted request metadata", (t) => {
  let output = "";
  t.mock.method(process.stderr, "write", (chunk) => {
    output += chunk;
    return true;
  });

  logError(502, {
    method: "POST",
    url: "/v1/messages?api_key=query-secret&prompt=private-message",
    headers: { authorization: "Bearer header-secret" },
    body: { token: "body-secret", message: "private-message" },
    error: new Error("arbitrary upstream message"),
  });

  assert.deepEqual(JSON.parse(output), {
    category: "request_error",
    method: "POST",
    pathname: "/v1/messages",
    status: 502,
    errorType: "api_error",
  });
  for (const secret of [
    "query-secret",
    "private-message",
    "header-secret",
    "body-secret",
    "arbitrary upstream message",
  ]) {
    assert.equal(output.includes(secret), false);
  }
});

test("server error diagnostics allowlist network fields and omit messages", () => {
  const secret = "bind failure includes a secret";
  const error = new Error(secret);
  error.code = "EADDRINUSE";
  error.syscall = "listen";
  error.address = "attacker-controlled.invalid";
  error.port = 9999;

  const diagnostic = serverErrorDiagnostic(error, {
    host: "127.0.0.1",
    port: 17780,
  });
  assert.deepEqual(diagnostic, {
    origin: "cc-openai-proxy",
    category: "server_error",
    errorType: "network_error",
    code: "EADDRINUSE",
    syscall: "listen",
    host: "127.0.0.1",
    port: 17780,
  });
  assert.equal(JSON.stringify(diagnostic).includes(secret), false);
  assert.equal(JSON.stringify(diagnostic).includes(error.address), false);
});

test("logError replaces unallowlisted request metadata", (t) => {
  let output = "";
  t.mock.method(process.stderr, "write", (chunk) => {
    output += chunk;
    return true;
  });

  logError(200, {
    method: "PRIVATE-METHOD",
    url: "/customer/alice@example.com?token=query-secret",
  });

  assert.deepEqual(JSON.parse(output), {
    category: "request_error",
    method: "UNKNOWN",
    pathname: "<unknown>",
    status: 500,
    errorType: "api_error",
  });
  assert.equal(output.includes("alice@example.com"), false);
  assert.equal(output.includes("query-secret"), false);
  assert.equal(output.includes("PRIVATE-METHOD"), false);
});
