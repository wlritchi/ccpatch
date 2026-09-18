import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { AsyncLocalStorage } from "node:async_hooks";

const payload = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (payload.modern) {
  const scope = new AsyncLocalStorage();
  let policyCalls = 0;
  const sandbox = {
    _ccAttributionScope: scope,
    [payload.effective]: () => {
      policyCalls++;
      throw new Error("Captured prompt recomputed its attribution snapshot");
    },
  };
  // Stub unrelated prompt guidance, not attribution or its request-local scope.
  for (const match of payload.source.matchAll(/(?<![\w$.])([\w$]+)\(/g)) {
    const name = match[1];
    if (!["if", "function", "Boolean", ...payload.prompts].includes(name) && !(name in sandbox))
      sandbox[name] = () => "";
  }
  for (const match of payload.source.matchAll(/if\(!([\w$]+)\([^)]*\)\)return\s*(?:""|[\w$]+\()/g))
    sandbox[match[1]] = () => true;
  const context = vm.createContext(
    new Proxy(sandbox, {
      get(target, key) {
        if (key in target) return target[key];
        if (key in globalThis) return globalThis[key];
        return "native guidance";
      },
    }),
  );
  vm.runInContext(payload.source + "\n" + payload.systemHelper, context);
  for (const name of payload.prompts) {
    const snapshots = [
      { commit: "Co-Authored-By: GPT-6 Astra <noreply@openai.com>", pr: "custom PR" },
      { commit: "Co-Authored-By: GLM 5.3 <noreply@z.ai>", pr: "second PR" },
      { commit: "", pr: "" },
      ...(payload.source.includes("===null?") ? [null] : []),
    ];
    const results = await Promise.all(
      snapshots.map((snapshot) => scope.run({ snapshot }, () => context[name]([]))),
    );
    for (let i = 0; i < snapshots.length; i++) {
      const snapshot = snapshots[i];
      const result = results[i];
      assert.equal(typeof result, "string");
      assert.ok(result.includes("Git") || result.includes("git"));
      if (snapshot?.commit) assert.ok(result.includes(snapshot.commit));
      else assert.ok(!result.includes("Co-Authored-By:"));
      if (snapshot?.pr) assert.ok(result.includes(snapshot.pr));
      const blocks = context._ccMultiProviderSystemAttribution("native", "zai:test", snapshot);
      assert.equal(blocks.length, snapshot?.commit || snapshot?.pr ? 2 : 1);
      if (snapshot?.commit) assert.ok(blocks[1].text.includes(snapshot.commit));
    }
  }
  assert.equal(policyCalls, 0);
  process.exit(0);
}
const { source, surfaceFooter } = payload;
let footerGate = false;
let clientKind = "cli";
let settings = {};
let session = null;
let currentModel = "claude-opus-4-8";
let compact = true;
let pending = null;
let remote = false;
let stripBetas = false;
const cache = new Map();
const context = vm.createContext({
  process: { env: {} },
  Ge: {},
  Gs: () => currentModel,
  AT: () => false,
  RJa: (model) => model.startsWith("claude-"),
  ndn: () => "Claude Opus 4.8",
  WHe: { firstParty: "claude-opus-4-8" }, // codespell:ignore whe
  A2e: "https://claude.com/claude-code",
  jr: () => settings,
  syt: () => (remote ? "remote" : "local"),
  Sk: () => session !== null,
  TS: () => session,
  gRt: () => false,
  vb: (id) => `https://claude.ai/code/${id}`,
  Pg: () => compact,
  nC: () => true,
  NJa: () => null,
  OJa: () => null,
  FJa: () => "",
  H4t: () => 120000,
  opt: () => 600000,
  cG: () => false,
  _Mt: () => true,
  BJa: () => "",
  pGn: () => "",
  SH: () => false,
  SR: "Write",
  Xw: "Edit",
  vs: "Read",
  ns: "Bash",
  bu: "Glob", // codespell:ignore bu
  Uc: "Grep",
  Ws: "Read",
  Fa: "Edit",
  Kc: "Write",
  EV: (items) => items.flat(Infinity),
  ct: (gate) => gate === "tengu_pr_footer_surface_suffix" && footerGate,
  _nativeClientKind: () => clientKind,
  _nativeMinimalBash: () => false,
  xr: () => "firstParty",
  fSf: () => ({}),
  _Sf: JSON.stringify,
  cni: () => cache,
  Sl: () => true,
  yl: () => false,
  Pu: () => false,
  st: () => false,
  ZNe: () => stripBetas,
  ySf: () => {},
  eBe: () => true,
  _nativeStrict: () => false,
  _nativeValidate: (schema) => ({ ok: true, schema }),
  _nativeWarn: () => {},
  R0o: () => false,
  Rc: () => true,
  gH: "Agent",
  Sc: () => "cwd",
  Ftt: () => pending ?? [],
  yAo: () => ({}),
});
vm.runInContext(
  `${source}\nglobalThis.serialize=(model,tools=[],options={})=>jWn(bash,{model,tools,...options});`,
  context,
);
const serialize = async (model = currentModel, tools = []) =>
  (await context.serialize(model, tools)).description;
const expected = [
  ["openai:gpt-6-astra", "GPT-6 Astra <noreply@openai.com>"],
  ["zai:glm-5.3", "GLM 5.3 <noreply@z.ai>"],
  ["zai:glm-5.3-flash", "GLM 5.3 Flash <noreply@z.ai>"],
  ["moonshot:kimi-k3", "Kimi K3 <noreply@moonshot.ai>"],
  ["minimax:MiniMax-M3", "MiniMax M3 <noreply@minimax.io>"],
  ["claude-opus-4-8", "Claude Opus 4.8 <noreply@anthropic.com>"],
];
for (const mode of [true, false]) {
  compact = mode;
  cache.clear();
  for (const [model, attribution] of expected) {
    assert.ok((await serialize(model)).includes(attribution), model);
  }
  cache.clear();
  const results = await Promise.all(expected.map(([model]) => serialize(model)));
  results.forEach((result, index) => assert.ok(result.includes(expected[index][1])));
  settings = { attribution: { commit: "custom commit" } };
  assert.ok((await serialize()).includes("custom commit"));
  assert.ok((await serialize()).includes("Generated with [Claude Code]"));
  settings = { attribution: { pr: "custom PR" } };
  assert.ok((await serialize()).includes("custom PR"));
  assert.ok((await serialize()).includes("Co-Authored-By:"));
  settings = { attribution: { commit: "", pr: "" } };
  assert.ok(!(await serialize()).includes("Co-Authored-By:"));
  assert.ok(!(await serialize()).includes("Generated with [Claude Code]"));
  settings = { includeCoAuthoredBy: false };
  assert.ok(!(await serialize()).includes("Co-Authored-By:"));
  settings = { includeCoAuthoredBy: false, attribution: { sessionUrl: false } };
  assert.ok(!(await serialize()).includes("Co-Authored-By:"));
  settings = { includeCoAuthoredBy: false, attribution: { commit: "override" } };
  assert.ok((await serialize()).includes("override"));
  assert.ok((await serialize()).includes("Generated with [Claude Code]"));
  settings = {};
  session = { bridgeSessionId: "connected", sessionIngressUrl: "https://ingress.invalid" };
  assert.ok((await serialize()).includes("Claude-Session: https://claude.ai/code/connected"));
  settings = { attribution: { sessionUrl: false } };
  assert.ok(!(await serialize()).includes("Claude-Session:"));
  settings = {};
  session = { ...session, outboundOnly: true };
  assert.ok(!(await serialize()).includes("Claude-Session:"));
  session = null;
  assert.ok(!(await serialize()).includes("Claude-Session:"));
}
cache.clear();
let release;
pending = new Promise((resolve) => {
  release = resolve;
});
const waiting = serialize("zai:glm-5.3", [{}]);
await Promise.resolve();
settings = { attribution: { commit: "changed while awaiting skills" } };
release([]);
assert.ok((await waiting).includes("GLM 5.3 <noreply@z.ai>"));
assert.ok((await serialize("zai:glm-5.3")).includes("changed while awaiting skills"));
pending = null;
settings = {};
assert.ok((await serialize("zai:glm-5.3")).includes("GLM 5.3 <noreply@z.ai>"));
context._ccMultiProviderSDK = () =>
  class {
    constructor() {
      this.beta = {};
    }
  };
Object.assign(context.process.env, {
  CC_ZAI_AUTH_TOKEN: "test-only",
  CC_KIMI_AUTH_TOKEN: "test-only",
  CC_MINIMAX_AUTH_TOKEN: "test-only",
  CC_OPENAI_PROXY_AUTH_TOKEN: "test-only",
  CC_OPENAI_AVAILABLE: "1",
  CC_OPENAI_PROXY_EFFECTIVE_URL: "http://invalid.local",
});
vm.runInContext("globalThis.route=_ccMultiProviderRoute", context);
const nativeClient = {};
const system = Object.freeze([
  { type: "text", text: "Original system", cache_control: { type: "ephemeral" } },
]);
for (const [model, attribution] of expected) {
  const request = Object.freeze({ model, system, messages: [] });
  const outbound = context.route(nativeClient, request)[1];
  if (model.startsWith("zai:")) {
    assert.ok(outbound.system.at(-1).text.includes(attribution));
    assert.equal(outbound.system[0], system[0]);
    assert.equal(outbound.system.length, 2);
    assert.equal(
      context.route(nativeClient, { ...request, system: outbound.system })[1].system.length,
      2,
    );
    assert.equal(context.route(nativeClient, request, {}, true)[1].system, system);
  } else {
    assert.equal(outbound.system, system);
    if (model.startsWith("claude-")) assert.equal(outbound, request);
  }
}
settings = { attribution: { commit: "only custom commit", pr: "" } };
let outbound = context.route(nativeClient, { model: "zai:glm-5.3-flash", system })[1];
assert.ok(outbound.system.at(-1).text.includes("only custom commit"));
assert.ok(!outbound.system.at(-1).text.includes("End PR bodies"));
settings = { attribution: { commit: "", pr: "" } };
outbound = context.route(nativeClient, { model: "zai:glm-5.3", system })[1];
assert.equal(outbound.system.length, 1);
settings = {};
currentModel = "zai:glm-5.3";
outbound = context.route(nativeClient, { model: "zai:glm-5.3-flash", system: "String system" })[1];
assert.ok(outbound.system.at(-1).text.includes("GLM 5.3 Flash <noreply@z.ai>"));
assert.equal(outbound.system[0].text, "String system");
// Route the captured schema after settings and session state change.
for (const mode of [true, false]) {
  compact = mode;
  cache.clear();
  settings = {};
  session = { bridgeSessionId: "snapshot", sessionIngressUrl: "https://ingress.invalid" };
  const schema = await context.serialize("zai:glm-5.3");
  const cachedSchema = await context.serialize("zai:glm-5.3");
  assert.notEqual(schema, cachedSchema);
  assert.equal(Object.getOwnPropertySymbols(schema).length, 1);
  const snapshot = schema[Object.getOwnPropertySymbols(schema)[0]];
  assert.ok(Object.isFrozen(snapshot));
  assert.ok(!JSON.stringify(schema).includes("ccpatch.attribution"));
  stripBetas = true;
  const strippedSchema = await context.serialize("zai:glm-5.3", [], { deferLoading: true });
  stripBetas = false;
  assert.ok(!("defer_loading" in strippedSchema));
  settings = { attribution: { commit: "later custom", pr: "later PR", sessionUrl: false } };
  session = null;
  for (const tool of [
    schema,
    cachedSchema,
    strippedSchema,
    { ...schema, cache_control: { type: "ephemeral" } },
  ]) {
    const routed = context.route(nativeClient, { model: "zai:glm-5.3", system, tools: [tool] })[1];
    assert.ok(routed.system.at(-1).text.includes(snapshot.commit));
    assert.ok(routed.system.at(-1).text.includes(snapshot.pr));
    assert.ok(!routed.system.at(-1).text.includes("later custom"));
    assert.ok(tool.description.includes(snapshot.commit));
  }
  const custom = await context.serialize("zai:glm-5.3");
  settings = { attribution: { commit: "", pr: "" } };
  const disabled = await context.serialize("zai:glm-5.3");
  settings = {};
  assert.equal(
    context.route(nativeClient, { model: "zai:glm-5.3", system, tools: [disabled] })[1].system
      .length,
    1,
  );
  assert.ok(
    context
      .route(nativeClient, { model: "zai:glm-5.3", system, tools: [custom] })[1]
      .system.at(-1)
      .text.includes("later custom"),
  );
  remote = true;
  session = { bridgeSessionId: "suppressed", sessionIngressUrl: "https://ingress.invalid" };
  context.Ge.CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION = true;
  const suppressed = await context.serialize("zai:glm-5.3");
  remote = false;
  context.Ge.CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION = false;
  const suppressedSystem = context
    .route(nativeClient, { model: "zai:glm-5.3", system, tools: [suppressed] })[1]
    .system.at(-1).text;
  assert.ok(!suppressed.description.includes("Claude-Session:"));
  assert.ok(!suppressedSystem.includes("Claude-Session:"));
  session = null;

  pending = new Promise((resolve) => {
    release = resolve;
  });
  const calls = expected.map(([model]) => context.serialize(model, [{}]));
  await Promise.resolve();
  settings = { includeCoAuthoredBy: false };
  release([]);
  const schemas = await Promise.all(calls);
  pending = null;
  schemas.forEach((tool, index) => {
    const [model, attribution] = expected[index];
    assert.ok(tool.description.includes(attribution));
    const routed = context.route(nativeClient, { model, system, tools: [tool] })[1];
    if (model.startsWith("zai:")) assert.ok(routed.system.at(-1).text.includes(attribution));
  });
  settings = {};
}
const surfaces = [
  ["claude-desktop", "Claude Desktop"],
  ["claude-desktop-3p", "Claude Desktop"],
  ["remote_desktop", "Claude Desktop"],
  ["remote_mobile", "Mobile"],
  ["local-agent", "Cowork"],
  ["remote_cowork", "Cowork"],
  ["claude_in_slack", "Claude Tag in Slack"],
  ["claude-in-slack", "Claude Tag in Slack"],
  ["claude-in-teams", "Claude Tag in Teams"],
  ["claude-code-github-action", "GitHub Actions"],
  ["cli", undefined],
  ["unknown-client", undefined],
];
for (compact of [true, false]) {
  for (footerGate of [false, true]) {
    for (const [kind, surface] of surfaces) {
      clientKind = kind;
      const nativePR =
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)" +
        (surfaceFooter && footerGate && surface ? ` via ${surface}` : "");
      for (const attribution of [
        undefined,
        { commit: "custom commit" },
        { pr: "custom PR" },
        { pr: "" },
        { commit: "", pr: "" },
      ]) {
        settings = attribution ? { attribution } : {};
        const expectedPR = attribution?.pr ?? nativePR;
        for (const model of ["claude-opus-4-8", "zai:glm-5.3"]) {
          const schema = await context.serialize(model);
          const snapshot = schema[Object.getOwnPropertySymbols(schema)[0]];
          assert.equal(snapshot.pr, expectedPR, `${kind}/${footerGate}/${model}`);
          if (expectedPR) assert.ok(schema.description.includes(expectedPR));
          else assert.ok(!schema.description.includes("Generated with [Claude Code]"));
          if (model.startsWith("zai:")) {
            const routed = context.route(nativeClient, { model, system, tools: [schema] })[1];
            if (expectedPR) assert.ok(routed.system.at(-1).text.includes(expectedPR));
            else assert.ok(!JSON.stringify(routed.system).includes("End PR bodies"));
          }
        }
      }
    }
  }
}
console.log("Native attribution serialization and outgoing routing passed");
