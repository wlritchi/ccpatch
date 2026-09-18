import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import { StringDecoder } from "node:string_decoder";

const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const scenario = process.argv[3];
const timers = [];
const kills = [];
const spawns = [];
const errors = [];
const connections = [];
const unlinks = [];
const sent = [];
let alive = scenario !== "dead";
let identity = scenario === "identity-mismatch" ? "reused" : "original";
const payload = {
  ANTHROPIC_API_KEY: "original-secret",
  ANTHROPIC_AUTH_TOKEN: null,
  ANTHROPIC_BASE_URL: "",
  CC_OPENAI_PROXY_AUTH_TOKEN: "openai-original",
};
const job = {
  short: "test",
  nonce: "dispatch-nonce",
  sessionId: "full-session-id",
  launch: { mode: "resume", sessionId: "full-session-id" },
  cwd: "/work",
  createdAt: 1,
  source: "shell",
};
const roster = {
  dispatch: job,
  pid: 1234,
  procStart: "original",
  attempt: 2,
  startedAt: 1,
  cliVersion: "2.1.198",
  ptySock: "/socks/test.pty.sock",
  rendezvousSock: "/rv/test",
  rvAuth: "native-auth",
  ptyAuth: "pty-auth",
};
if (scenario === "pending-upgrade") roster.pendingRespawn = "upgrade";
const timer = (fn, delay, ...args) => {
  const entry = { fn: () => fn(...args), delay, unref() {}, cancelled: false };
  timers.push(entry);
  return entry;
};
const event = () => ({
  emit() {},
  subscribe() {
    return () => {};
  },
});
const pty = () => ({
  pid: 1234,
  kill(signal) {
    kills.push(signal);
  },
  write() {},
  resize() {},
  dispose() {},
  onData() {
    return { dispose() {} };
  },
  onExit() {
    return { dispose() {} };
  },
});
const disk = {
  mkdir: async () => {},
  unlink: async (name) => {
    unlinks.push(name);
  },
  readdir: async () => ["test.pty.sock"],
};
const ctx = vm.createContext({
  console,
  require: createRequire(import.meta.url),
  queueMicrotask,
  Bm: 1,
  Buffer,
  crypto,
  structuredClone,
  process: {
    argv: ["node", "cli.js"],
    env: { ANTHROPIC_API_KEY: "daemon-secret" },
    kill(pid, signal) {
      if (signal === 0) {
        if (!alive) throw Object.assign(Error("gone"), { code: "ESRCH" });
      } else kills.push([pid, signal]);
    },
  },
  setTimeout: timer,
  setInterval: timer,
  clearTimeout: (entry) => {
    if (entry) entry.cancelled = true;
  },
  clearInterval: (entry) => {
    if (entry) entry.cancelled = true;
  },
  _sn: crypto,
  As: event,
  wnn: () => ({ seed() {}, snapshot: () => ({}), feed: () => false }),
  X0: () => alive,
  wC: async () => identity,
  S2e: () => identity,
  xDs: () => ({}),
  Y3_: () => true,
  rLp: JSON.stringify,
  w() {},
  O() {},
  Ee() {},
  xe: (value) => value,
  Pe: (value) => value,
  Yo: (value) => value,
  Ce: (error) => {
    errors.push(error);
  },
  cpr: (error) => error,
  pc: (short) => `/jobs/${short}`,
  upr: (short) => `/rv/${short}`,
  yM: (short) => `/socks/${short}.pty.sock`,
  gD: (value) => value,
  d4: (value) => value + ".err",
  w_e: (value) => value + ".late",
  Ann: (value) => value + ".json",
  vnn: (value) => value + ".sock",
  u8e: (value) => value + ".pid",
  mMo: pty,
  Nt: () => "linux",
  uJe: "",
  OSt: "",
  $le: "\0",
  qRp: (...args) => {
    connections.push(args);
    return {
      send: (message) => {
        sent.push(message);
        return true;
      },
      close() {},
    };
  },
  Ca: async () => ({ tempo: "idle", state: "running" }),
  oy: () => false,
  Yua: () => false,
  ZHe: () => false,
  tLp: () => null,
  CRo: async () => false,
  HRo: async () => null,
  vEt() {},
  gYe: (value) => {
    kills.push(value);
  },
  mMt: async (value) => {
    kills.push(value);
    return true;
  },
  Tce: () => "/socks",
  cMt: () => "/worker-socks",
  L4: disk,
  Lf: { ...disk, readdir: async () => ["test.sock"] },
  lJ: disk,
  umn: path,
  dOa: path,
  hMo: path,
  A5o: {
    connect: (name) => {
      throw Error(`orphan sweep killed live worker ${name}`);
    },
  },
  Rgt: [],
  QRp: 4096,
  Wua: 5000,
  XRp: 15000,
  q3_: 120000,
  JRp: 120000,
  z3_: 3600000,
  j3_: 3,
  U3_: 10000,
  KRp: 20,
  W3_: 60000,
  YRp: 5000,
  G3_: 300000,
  yXr: 1000000,
  jua: 200,
  cKo: () => undefined,
  fs: (value) => value,
  qt: (error) => error.code,
  Gua: { statSync: () => ({ isDirectory: () => true }) },
  qua: () => {
    throw Error("unexpected default spawn");
  },
  Vua: async () => undefined,
  zua: async () => undefined,
  k_p: async () => {},
  ZRp: () => [],
  eLp: (...args) => {
    spawns.push(args);
    return {};
  },
  dV: () => ({ cmd: "mock-claude", prefixArgs: [] }),
  fr: () => false,
  ne: String,
  payload,
  job,
  roster,
  spawnPty: (...args) => {
    spawns.push(args);
    return pty();
  },
});
vm.runInContext(fixture.helpers + fixture.worker + fixture.sweeps + ";globalThis.Worker=Bce;", ctx);
const Worker = ctx.Worker;
const flush = async () => {
  for (let index = 0; index < 20; index++) await Promise.resolve();
};

export { ctx, Worker, fixture, job, roster, payload, errors };

if (scenario.startsWith("process-")) {
  // The process harness replaces transport and OS boundaries before adoption.
} else if (scenario === "stall-unresolved" || scenario === "stall-recovered") {
  const worker = await Worker.adopt("test", roster, ctx.spawnPty);
  if (scenario === "stall-recovered") worker.providerEnv = Object.freeze({ ...payload });
  Object.assign(ctx, {
    yx_: -1,
    yke: async () => ({ hasMessages: true, path: "/transcript" }),
    S$i: (value) => value,
    u$r: (value) => value,
  });
  vm.runInContext(fixture.stall, ctx);
  const pending = new Set();
  const dispatches = [];
  ctx.bx_(
    worker,
    { destroyed: false },
    async (...args) => dispatches.push(args),
    () => false,
    pending,
  );
  await flush();
  assert.deepEqual(errors, []);
  if (scenario === "stall-unresolved") {
    assert.equal(kills.length, 0);
    assert.equal(dispatches.length, 0);
  } else {
    assert.equal(kills.length, 1);
    assert.equal(dispatches.length, 1);
    assert.equal(dispatches[0][3], worker.providerEnv);
    assert.equal(dispatches[0][0].providerEnv, undefined);
  }
} else if (scenario === "replaced-socket") {
  let accept;
  const endpoint = vm.createContext({
    require: createRequire(import.meta.url),
    Buffer,
    process: {
      argv: ["node", "cli.js"],
      env: { CLAUDE_CODE_PROVIDER_ENV_TRANSIENT: JSON.stringify(payload) },
    },
    Te: { CLAUDE_BG_RENDEZVOUS_SOCK: "/rv/test", CLAUDE_BG_RV_AUTH: "native-auth" }, // codespell:ignore te
    ZWe: undefined,
    tye: undefined, // codespell:ignore tye
    xCs: undefined,
    bir: undefined,
    j7r: false,
    Kyo: 0,
    vir: false,
    U7r: undefined,
    Sir: { unlink: async () => {} },
    ekd: {
      createServer: (callback) => {
        accept = callback;
        return { on() {}, listen() {}, unref() {} };
      },
    },
    tkd: { StringDecoder },
    eky: async () => {},
    sky: async () => {},
    setInterval: timer,
    Bm: 1,
    Ct: () => job.sessionId,
    Ft: JSON.parse,
    Re: JSON.stringify,
    QTe: (a, b) => typeof a === "string" && a === b,
    w() {},
    Ee() {},
    Zxd() {},
  });
  vm.runInContext(fixture.helpers + fixture.endpoint, endpoint);
  await endpoint.Jxy();
  const socket = () => ({
    handlers: {},
    frames: [],
    destroyed: false,
    on(name, callback) {
      this.handlers[name] = callback;
    },
    once(name, callback) {
      this.handlers[name] = callback;
    },
    destroy() {
      this.destroyed = true;
    },
    write(frame) {
      this.frames.push(JSON.parse(frame));
    },
  });
  const old = socket();
  accept(old);
  const replacement = socket();
  accept(replacement);
  const data = (peer, frame) => peer.handlers.data(Buffer.from(JSON.stringify(frame) + "\n"));
  data(old, { role: "supervisor", auth: "native-auth", proto: 1 });
  data(old, {
    type: "cc-provider-snapshot-request",
    auth: "native-auth",
    proto: 1,
    version: 3,
    sessionId: job.sessionId,
    nonce: "a".repeat(64),
  });
  assert.equal(endpoint.j7r, false, "a replaced socket must not authenticate its successor");
  assert.equal(replacement.frames.length, 0, "a stale request must not leak to a new socket");
  data(replacement, { role: "supervisor", auth: "native-auth", proto: 1 });
  data(replacement, {
    type: "cc-provider-snapshot-request",
    auth: "native-auth",
    proto: 1,
    version: 3,
    sessionId: job.sessionId,
    nonce: "b".repeat(64),
  });
  assert.equal(replacement.frames[0].payload.ANTHROPIC_API_KEY, "original-secret");
} else if (scenario === "constructor") {
  const override = { pid: 9876, state: "adopted", detail: "native record override" };
  const worker = new Worker(job, ctx.spawnPty, undefined, "cold", override, payload);
  assert.equal(worker.record.pid, 9876);
  assert.equal(worker.record.detail, "native record override");
  assert.deepEqual(JSON.parse(JSON.stringify(worker.providerEnv)), payload);
  assert.ok(Object.isFrozen(worker.providerEnv));
  assert.equal(worker.record.ANTHROPIC_API_KEY, undefined);
  payload.ANTHROPIC_API_KEY = "mutated";
  assert.equal(worker.providerEnv.ANTHROPIC_API_KEY, "original-secret");
} else if (scenario.startsWith("wrapper-")) {
  ctx.wrapperCommand = ["/trusted/wrapper"];
  ctx.ge = () => {};
  ctx.ie = String;
  ctx.lJ.access = async () => {};
  ctx.fr = (error) => error.code === "ENOENT";
  ctx.jh = (value) => value;
  ctx.JT = (value) => value;
  ctx.jtt = 10000;
  ctx.uKo = () => undefined;
  ctx.h8e = () => false;
  const worker = new Worker(job, ctx.spawnPty, undefined, "cold", undefined, payload);
  let outcome;
  let retries = 0;
  worker.settle = (value) => {
    outcome = value;
  };
  worker.scheduleRespawn = () => {
    retries++;
  };
  if (scenario === "wrapper-refusal") {
    ctx.wrapperRefusal = "synthetic launcher refusal";
    await worker.doSpawn();
    assert.equal(spawns.length, 0);
    assert.equal(worker.record.detail, ctx.wrapperRefusal);
  } else if (["wrapper-ENOENT", "wrapper-EACCES", "wrapper-EPERM"].includes(scenario)) {
    const code = scenario.slice("wrapper-".length);
    worker.spawnPty = () => {
      throw Object.assign(Error(code), { code });
    };
    await worker.doSpawn();
    assert.match(worker.record.detail, /launcher.*\/trusted\/wrapper/);
    assert.ok(worker.record.detail.includes(code));
  } else {
    worker.lastSpawnAt = Date.now();
    worker.lastCheckPidAt = Date.now();
    worker.workerReady = false;
    worker.phase = { kind: "running" };
    worker.preInitErrorTail = () => undefined;
    worker.onExit(scenario === "wrapper-fork-exit" ? 0 : 1, undefined, "synthetic host stderr");
    assert.match(worker.record.detail, /synthetic host stderr/);
    if (scenario === "wrapper-fork-exit")
      assert.match(worker.record.detail, /must exec, not daemonize/);
  }
  assert.equal(outcome, "crashed");
  assert.equal(retries, 0);
  assert.deepEqual(errors, []);
} else if (scenario === "cold") {
  const worker = Worker.spawn(job, ctx.spawnPty, undefined, undefined, payload);
  await flush();
  assert.equal(worker.record.short, "test");
  assert.equal(worker.record.ANTHROPIC_API_KEY, undefined);
  assert.equal(worker.providerEnv.ANTHROPIC_API_KEY, "original-secret");
  assert.ok(Object.isFrozen(worker.providerEnv));
  assert.ok(spawns.length > 0, "native doSpawn must reach the external spawn boundary");
  assert.deepEqual(errors, []);
} else if (scenario === "dead" || scenario === "identity-mismatch") {
  assert.equal(await Worker.adopt("test", roster, ctx.spawnPty), null);
  assert.equal(connections.length, 0);
  assert.equal(kills.length, 0, "adopt must not signal a reused PID");
  Object.assign(ctx, {
    A: { workers: { test: roster } },
    a: ctx.spawnPty,
    t: {},
    o: new Map(),
    T: 0,
    x: 0,
    I: 0,
    uOa() {},
    l() {},
    s() {},
    e() {},
    _: async () => {
      throw Error("unexpected successor dispatch");
    },
  });
  await vm.runInContext(`(async()=>{${fixture.adoption};})()`, ctx);
  await flush();
  assert.equal(ctx.o.size, 0);
  assert.equal(ctx.x, 1, "native manager must account for dead/recycled worker");
  assert.ok(unlinks.includes(roster.ptySock), "native dead-worker socket cleanup must run");
  if (scenario === "identity-mismatch") assert.equal(kills.length, 0);
} else if (
  [
    "handoff",
    "wrong-mac",
    "wrong-proto",
    "wrong-version",
    "wrong-session",
    "wrong-nonce",
    "partial",
    "timeout",
    "replay",
    "reconnect",
    "crash-response",
    "missing-auth",
    "wrong-auth",
    "unauthenticated",
    "unsupported",
  ].includes(scenario)
) {
  const replies = [];
  const server = vm.createContext({
    require: createRequire(import.meta.url),
    Buffer,
    process: {
      argv: ["node", "cli.js"],
      env: { CLAUDE_CODE_PROVIDER_ENV_TRANSIENT: JSON.stringify(payload) },
    },
    Bm: 1,
    Ct: () => job.sessionId,
    bir: "native-auth",
    j7r: false,
    vir: false,
    Ft: JSON.parse,
    QTe: (a, b) => typeof a === "string" && a === b,
    _X: (reply) => {
      replies.push(JSON.parse(JSON.stringify(reply)));
      return true;
    },
    w() {},
    Ee() {},
    Zxd() {},
  });
  vm.runInContext(fixture.helpers + fixture.server, server);
  const worker = await Worker.adopt("test", roster, ctx.spawnPty);
  const transport = connections.at(-1);
  transport[3]();
  await flush();
  const request = JSON.parse(
    JSON.stringify(sent.find((item) => item.type === "cc-provider-snapshot-request")),
  );
  assert.equal(request.sessionId, job.sessionId);
  assert.equal(request.version, 3);
  assert.equal(request.auth, roster.rvAuth);
  assert.match(request.nonce, /^[a-f0-9]{64}$/);
  if (scenario !== "unauthenticated")
    server.Zxy(JSON.stringify({ role: "supervisor", auth: "native-auth", proto: 1 }));
  if (scenario === "missing-auth") delete request.auth;
  if (scenario === "wrong-auth") request.auth = "wrong";
  if (scenario !== "unsupported") server.Zxy(JSON.stringify(request));
  if (["missing-auth", "wrong-auth", "unauthenticated", "unsupported"].includes(scenario)) {
    assert.equal(replies.filter((item) => item.type === "cc-provider-snapshot").length, 0);
  } else {
    const reply = replies.find((item) => item.type === "cc-provider-snapshot");
    assert.ok(reply, "authenticated native server must provide a memory-only snapshot");
    const expected = crypto
      .createHmac("sha256", roster.rvAuth)
      .update(
        JSON.stringify([
          "cc-provider-snapshot",
          reply.proto,
          reply.version,
          reply.sessionId,
          reply.nonce,
          reply.payload,
        ]),
      )
      .digest("hex");
    assert.equal(reply.mac, expected);
    if (scenario === "wrong-mac") reply.mac = "0".repeat(64);
    if (scenario === "wrong-proto") reply.proto++;
    if (scenario === "wrong-version") reply.version++;
    if (scenario === "wrong-session") reply.sessionId = "other-session";
    if (scenario === "wrong-nonce") reply.nonce = "a".repeat(64);
    if (scenario === "partial") delete reply.payload;
    if (scenario === "timeout") {
      for (const entry of [...timers]) if (!entry.cancelled && entry.delay === 5000) entry.fn();
    }
    if (scenario === "reconnect") {
      transport[2]();
      transport[3]();
      await flush();
      assert.notEqual(worker._ccProviderNonce, reply.nonce);
    }
    if (scenario === "crash-response") worker.settle("crashed");
    transport[1](reply);
    if (["handoff", "replay"].includes(scenario)) {
      assert.deepEqual(JSON.parse(JSON.stringify(worker.providerEnv)), payload);
      assert.ok(Object.isFrozen(worker.providerEnv));
      const retained = worker.providerEnv;
      reply.payload.ANTHROPIC_API_KEY = "replayed-secret";
      transport[1](reply);
      assert.equal(worker.providerEnv, retained);
      assert.equal(retained.ANTHROPIC_API_KEY, "original-secret");
      assert.equal(retained.ANTHROPIC_AUTH_TOKEN, null);
      assert.equal(retained.ANTHROPIC_BASE_URL, "");
      assert.equal(worker._ccProviderBlocked(), false);
      assert.ok(!JSON.stringify(worker.rosterEntry()).includes("original-secret"));
      if (scenario === "handoff") {
        const otherPayload = {
          ...payload,
          ANTHROPIC_API_KEY: null,
          ANTHROPIC_AUTH_TOKEN: "second-provider-token",
          ANTHROPIC_BASE_URL: "http://provider.invalid",
        };
        const otherJob = { ...job, short: "other", sessionId: "other-full-session" };
        const otherRoster = { ...roster, dispatch: otherJob, rvAuth: "other-native-auth" };
        const otherServer = vm.createContext({
          ...server,
          process: {
            argv: ["node", "cli.js"],
            env: { CLAUDE_CODE_PROVIDER_ENV_TRANSIENT: JSON.stringify(otherPayload) },
          },
          Ct: () => otherJob.sessionId,
          bir: otherRoster.rvAuth,
          j7r: false,
        });
        vm.runInContext(fixture.helpers + fixture.server, otherServer);
        const otherWorker = await Worker.adopt("other", otherRoster, ctx.spawnPty);
        const otherTransport = connections.at(-1);
        otherTransport[3]();
        await flush();
        const otherRequest = sent.findLast((item) => item.type === "cc-provider-snapshot-request");
        otherServer.Zxy(JSON.stringify({ role: "supervisor", auth: otherRoster.rvAuth, proto: 1 }));
        otherServer.Zxy(JSON.stringify(otherRequest));
        otherTransport[1](replies.at(-1));
        assert.deepEqual(JSON.parse(JSON.stringify(otherWorker.providerEnv)), otherPayload);
        assert.equal(worker.providerEnv.ANTHROPIC_API_KEY, "original-secret");
        assert.equal(otherWorker.providerEnv.ANTHROPIC_AUTH_TOKEN, "second-provider-token");
        for (const handle of [worker, otherWorker]) {
          const persisted = JSON.stringify(handle.rosterEntry());
          assert.ok(!persisted.includes("original-secret"));
          assert.ok(!persisted.includes("second-provider-token"));
          assert.ok(!persisted.includes("providerEnv"));
        }
      }
    } else assert.equal(worker.providerEnv, null);
  }
  assert.equal(kills.length, 0);
  assert.equal(spawns.length, 0);
} else {
  Object.assign(ctx, {
    A: { workers: { test: roster } },
    a: ctx.spawnPty,
    t: {},
    o: new Map(),
    T: 0,
    x: 0,
    I: 0,
    uOa() {},
    l() {},
    s() {},
    e() {},
    _: async () => {
      throw Error("unexpected successor dispatch");
    },
  });
  await vm.runInContext(`(async()=>{${fixture.adoption};})()`, ctx);
  const worker = ctx.o.get("test");
  assert.equal(ctx.T, 1);
  assert.ok(worker, "verified live adoption must not require an unavailable snapshot");
  assert.equal(worker.record.pid, 1234);
  assert.equal(worker.record.state, "adopted");
  assert.equal(worker.providerEnv == null, true);
  assert.equal(worker.record.outcome, undefined);
  const handles = ctx.o;
  ctx.handles = handles;
  await vm.runInContext("rOa(handles,()=>{})", ctx);
  await vm.runInContext("nNb(handles,()=>{})", ctx);
  assert.equal(handles.get("test"), worker);
  assert.equal(kills.length, 0);
  assert.equal(unlinks.length, 0);
  const persisted = JSON.stringify(worker.rosterEntry());
  for (const value of ["original-secret", "daemon-secret", "providerEnv", "PROVIDER_ENV_TRANSIENT"])
    assert.ok(!persisted.includes(value), `persisted provider material: ${value}`);
  if (scenario === "rekey") {
    worker.rekeyForAuthMismatch("rv-auth-rejected");
    await flush();
    worker.fireAuthRekey();
  } else if (scenario === "stale") {
    worker.adoptedAt = 0;
    const result = await worker.respawnIfIdleStale(undefined, "attach");
    assert.equal(result.respawned, false);
  } else if (scenario === "retire") {
    worker.adoptedAt = 0;
    const result = await worker.retireIfSettled(0);
    assert.equal(result.retired, false);
    assert.equal(result.reason, "provider-unavailable");
  } else if (scenario === "crash") {
    worker.pty = undefined;
    worker.onExit(1, undefined);
    await flush();
  }
  for (const entry of [...timers]) {
    if (!entry.cancelled && entry.delay === 5000) entry.fn();
  }
  await flush();
  assert.equal(kills.length, 0, "automatic successor must not kill before snapshot recovery");
  assert.equal(spawns.length, 0, "automatic successor must not use daemon credentials");
  assert.deepEqual(errors, []);
}
console.log(`native lifecycle: ${scenario}`);
