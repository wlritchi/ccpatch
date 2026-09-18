// Opt-in extracted .206-.208 transport test. This does not launch the CLI or PTY host.
// Native adoption, framing, reconnect, endpoint, HMAC and environment code run
// unchanged. UI, telemetry, PTY and transcript services remain test adapters.
import assert from "node:assert/strict";
import crypto from "node:crypto";
import { fork, spawnSync } from "node:child_process";
import { once } from "node:events";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { StringDecoder } from "node:string_decoder";
import vm from "node:vm";

const mode = process.argv[4];
const scenario = process.argv[3];
const source = process.argv[2];
const socketPath = path.join(process.cwd(), "worker.sock");
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check) {
  const deadline = Date.now() + 5000;
  while (!check()) {
    assert.ok(Date.now() < deadline, "condition timed out");
    await pause(10);
  }
}

if (!mode) {
  const children = [];
  function child(role, extra = {}) {
    const proc = fork(import.meta.filename, [source, scenario, role], {
      env: { ...process.env, ...extra },
      stdio: ["ignore", "inherit", "inherit", "ipc"],
    });
    children.push(proc);
    return proc;
  }
  async function message(proc) {
    return await Promise.race([
      once(proc, "message").then(([value]) => value),
      once(proc, "exit").then(([code]) => {
        throw Error(`child exited before result: ${code}`);
      }),
    ]);
  }
  async function stop(proc) {
    if (proc.exitCode !== null || proc.signalCode !== null) return;
    const exited = once(proc, "exit");
    const escalation = setTimeout(() => proc.kill("SIGKILL"), 1000);
    try {
      proc.kill("SIGTERM");
      await exited;
    } finally {
      clearTimeout(escalation);
    }
  }
  const watchdog = setTimeout(() => {
    for (const proc of children) proc.kill("SIGKILL");
    process.exitCode = 1;
  }, 20000);
  try {
    const worker = child("worker");
    const ready = await message(worker);
    assert.equal(ready.pid, worker.pid);
    const first = child("supervisor", { WORKER_PID: String(worker.pid), AUTH: "native-auth" });
    const before = await message(first);
    assert.equal(before.recovered, true);
    await stop(first);
    process.kill(worker.pid, 0);
    const successor = child("supervisor", {
      WORKER_PID: String(worker.pid),
      AUTH: scenario === "wrong-auth" ? "wrong-auth" : "native-auth",
      SUCCESSOR: "1",
    });
    const after = await message(successor);
    assert.equal(after.pid, worker.pid);
    assert.equal(after.recovered, scenario !== "wrong-auth");
    assert.equal(after.probed, scenario !== "wrong-auth");
    if (after.recovered) {
      assert.notEqual(before.nonce, after.nonce);
      assert.match(after.nonce, /^[a-f0-9]{64}$/);
    }
    process.kill(worker.pid, 0);
    await stop(successor);
    await stop(worker);
  } finally {
    await Promise.all(children.map(stop));
    clearTimeout(watchdog);
  }
} else {
  process.argv[3] = `process-${mode}`;
  const { ctx, Worker, fixture, job, roster, payload, errors } =
    await import("./provider_native_lifecycle.mjs");
  Object.assign(ctx, {
    process,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    Re: JSON.stringify,
    Ft: JSON.parse,
    qC_: 1048576,
    w_p: { StringDecoder },
    WRp: net,
    GRp: 3,
    jRp: [10, 20, 50],
  });
  if (mode === "worker") {
    process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT = JSON.stringify(payload);
    process.env.CLAUDE_BG_RENDEZVOUS_SOCK = socketPath;
    process.env.CLAUDE_BG_RV_AUTH = "native-auth";
    Object.assign(ctx, {
      Te: process.env, // codespell:ignore te
      Sir: fs.promises,
      ekd: net,
      tkd: { StringDecoder },
      ZWe: undefined,
      tye: undefined, // codespell:ignore tye
      U7r: undefined,
      bir: undefined,
      j7r: false,
      Kyo: 0,
      vir: false,
      xCs: undefined,
      Ct: () => job.sessionId,
      Yxd: crypto,
      Zxd() {},
      m6o() {},
      KSc() {},
      rkd() {},
      eky: async () => {},
      sky: async () => {},
    });
    vm.runInContext(
      "_ccProviderWorkerEnv=_ccProviderCaptureTransport();" + fixture.auth + fixture.endpoint,
      ctx,
    );
    await ctx.Jxy();
    await until(() => fs.existsSync(socketPath));
    process.send({ pid: process.pid });
  } else {
    const pid = Number(process.env.WORKER_PID);
    const identity = (value) => {
      const stat = fs.readFileSync(`/proc/${value}/stat`, "utf8");
      return stat.slice(stat.lastIndexOf(")") + 2).split(" ")[19];
    };
    Object.assign(roster, {
      pid,
      procStart: identity(pid),
      cliVersion: `2.1.${fixture.version ?? "206"}`,
      rendezvousSock: socketPath,
      rvAuth: process.env.AUTH,
      ptySock: undefined,
    });
    Object.assign(ctx, {
      X0: (value) => {
        try {
          process.kill(value, 0);
          return true;
        } catch {
          return false;
        }
      },
      wC: async (value) => identity(value),
    });
    vm.runInContext(fixture.lines + fixture.client, ctx);
    const frames = [];
    const connect = ctx.qRp;
    ctx.qRp = (address, receive, ...rest) =>
      connect(
        address,
        (frame) => {
          frames.push(frame);
          receive(frame);
        },
        ...rest,
      );
    const worker = await Worker.adopt(job.short, roster, ctx.spawnPty);
    assert.ok(worker);
    process.once("SIGTERM", () => {
      worker.stop();
      process.exit(0);
    });
    if (process.env.AUTH === "native-auth") {
      await until(() => worker.providerEnv !== null);
      assert.deepEqual(JSON.parse(JSON.stringify(worker.providerEnv)), payload);
      assert.ok(Object.isFrozen(worker.providerEnv));
    } else {
      // Wait past the native recovery deadline, not just the first socket event.
      await pause(5500);
      assert.equal(worker.providerEnv, null);
      assert.equal(worker._ccProviderBlocked(), true);
      assert.ok(frames.some((frame) => frame.type === "auth-rejected"));
      assert.ok(!frames.some((frame) => frame.type === "cc-provider-snapshot"));
      assert.ok(!JSON.stringify(frames).includes(payload.ANTHROPIC_API_KEY));
      let attemptedSpawn = false;
      worker.spawnPty = () => {
        attemptedSpawn = true;
        return ctx.spawnPty();
      };
      const attempt = worker.attempt;
      await worker.doSpawn();
      assert.equal(attemptedSpawn, false);
      assert.equal(worker.attempt, attempt);
    }
    assert.equal(worker.record.pid, pid);
    assert.ok(!JSON.stringify(worker.rosterEntry()).includes(payload.ANTHROPIC_API_KEY));
    let probed = false;
    if (process.env.SUCCESSOR && worker.providerEnv) {
      Object.assign(ctx, {
        bsn: [],
        Kua: [],
        cTr: [],
        Rde: [],
        Rgt: [],
        VUt: () => false,
        ut: () => false,
        yke: async () => ({ hasMessages: true, path: "/synthetic-transcript" }),
      });
      process.env.ANTHROPIC_API_KEY = "successor-key";
      process.env.ANTHROPIC_AUTH_TOKEN = "must-be-deleted";
      process.env.ANTHROPIC_BASE_URL = "must-be-empty";
      vm.runInContext(fixture.environment, ctx);
      const hostEnv = { ...process.env };
      const execEnv = ctx.eLp(
        { ...job, launch: { mode: "exec", args: ["synthetic-command"] } },
        "/synthetic-job",
        undefined,
        socketPath,
        undefined,
        worker.providerEnv,
      );
      assert.equal(execEnv.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT, undefined);
      assert.equal(execEnv.CLAUDE_BG_RENDEZVOUS_SOCK, undefined);
      assert.equal(execEnv.CLAUDE_PTY_HOST_EXEC, "1");
      assert.equal(execEnv.ANTHROPIC_API_KEY, payload.ANTHROPIC_API_KEY);
      assert.equal(execEnv.ANTHROPIC_AUTH_TOKEN, undefined);
      assert.deepEqual({ ...process.env }, hostEnv);
      worker.spawnPty = (_cmd, _args, options) => {
        const probe = spawnSync(
          process.execPath,
          ["-e", "process.stdout.write(JSON.stringify(process.env))"],
          {
            env: options.env,
            encoding: "utf8",
            timeout: 3000,
          },
        );
        assert.equal(probe.status, 0, probe.stderr);
        const env = JSON.parse(probe.stdout);
        assert.equal(env.ANTHROPIC_API_KEY, payload.ANTHROPIC_API_KEY);
        assert.equal(env.ANTHROPIC_AUTH_TOKEN, undefined);
        assert.equal(env.ANTHROPIC_BASE_URL, "");
        assert.equal(env.CC_OPENAI_PROXY_AUTH_TOKEN, payload.CC_OPENAI_PROXY_AUTH_TOKEN);
        probed = true;
        // No PTY replacement occurs. Keep identity checks on the test worker.
        return { ...ctx.spawnPty(), pid };
      };
      await worker.doSpawn();
      assert.equal(probed, true);
      assert.deepEqual({ ...process.env }, hostEnv);
    }
    assert.deepEqual(errors, []);
    const snapshot = frames.find((frame) => frame.type === "cc-provider-snapshot");
    process.send({ pid, recovered: worker.providerEnv !== null, probed, nonce: snapshot?.nonce });
  }
  // Keep the extracted endpoint alive until the controller explicitly stops it.
  setInterval(() => {}, 1000);
}
