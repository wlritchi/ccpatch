import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { lstat, mkdir, mkdtemp, readFile, readdir, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  defaultProxyAuthPath,
  loadOrCreateProxyToken,
  readSecureToken,
  resolveProxyAuthPath,
} from "../bin/proxy-auth.js";

const execFileAsync = promisify(execFile);
const TOKEN = "A".repeat(43);

async function temporaryPath() {
  const root = await mkdtemp(join(tmpdir(), "cc-openai-proxy-auth-test-"));
  return {
    root,
    parent: join(root, "state"),
    path: join(root, "state", "bearer"),
  };
}

test("platform defaults and explicit configuration resolve predictably", () => {
  assert.equal(
    defaultProxyAuthPath({}, "linux", "/home/alice"),
    "/home/alice/.local/state/cc-openai-proxy/auth-token",
  );
  assert.equal(
    defaultProxyAuthPath({ XDG_STATE_HOME: "/state" }, "linux", "/home/alice"),
    "/state/cc-openai-proxy/auth-token",
  );
  assert.equal(
    defaultProxyAuthPath({}, "darwin", "/Users/alice"),
    "/Users/alice/Library/Application Support/cc-openai-proxy/auth-token",
  );
  assert.equal(resolveProxyAuthPath(undefined, { CC_OPENAI_PROXY_AUTH_FILE: "/env" }), "/env");
  assert.equal(resolveProxyAuthPath("/cli", { CC_OPENAI_PROXY_AUTH_FILE: "/env" }), "/cli");
});

test("loadOrCreateProxyToken creates and reuses a secure random bearer", async () => {
  const { parent, path } = await temporaryPath();
  const first = await loadOrCreateProxyToken(path);
  const second = await loadOrCreateProxyToken(path);
  assert.match(first, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(second, first);
  assert.equal((await lstat(parent)).mode & 0o777, 0o700);
  const stat = await lstat(path);
  assert.equal(stat.isFile(), true);
  assert.equal(stat.mode & 0o777, 0o600);
  assert.equal((await readFile(path, "utf8")).trim(), first);
});

test("concurrent creators publish one complete token and clean temporary files", async () => {
  const { parent, path } = await temporaryPath();
  const tokens = await Promise.all(Array.from({ length: 32 }, () => loadOrCreateProxyToken(path)));
  assert.equal(new Set(tokens).size, 1);
  assert.equal((await readFile(path, "utf8")).trim(), tokens[0]);
  assert.deepEqual(await readdir(parent), ["bearer"]);
});

test("concurrent helper processes converge on one complete token", async () => {
  const { parent, path } = await temporaryPath();
  const helper = fileURLToPath(new URL("../bin/cc-openai-proxy-auth.js", import.meta.url));
  const results = await Promise.all(
    Array.from({ length: 12 }, () =>
      execFileAsync(process.execPath, [helper, "--auth-token-file", path]),
    ),
  );
  assert.equal(new Set(results.map(({ stdout }) => stdout)).size, 1);
  assert.match(results[0].stdout, /^[A-Za-z0-9_-]{43}\n$/);
  assert.equal(await readFile(path, "utf8"), results[0].stdout);
  assert.deepEqual(await readdir(parent), ["bearer"]);
});

test("secure token reads reject wrong modes, links, empty, and malformed data", async (t) => {
  await t.test("wrong parent mode", async () => {
    const { parent, path } = await temporaryPath();
    await mkdir(parent, { mode: 0o755 });
    await assert.rejects(loadOrCreateProxyToken(path), /parent mode must be 0700/);
  });

  await t.test("wrong file mode", async () => {
    const { parent, path } = await temporaryPath();
    await mkdir(parent, { mode: 0o700 });
    await writeFile(path, `${TOKEN}\n`, { mode: 0o644 });
    await assert.rejects(readSecureToken(path), /token mode must be 0600/);
  });

  await t.test("symbolic link", async () => {
    const { root, parent, path } = await temporaryPath();
    const target = join(root, "target");
    await mkdir(parent, { mode: 0o700 });
    await writeFile(target, `${TOKEN}\n`, { mode: 0o600 });
    await symlink(target, path);
    await assert.rejects(readSecureToken(path));
  });

  for (const [name, contents] of [
    ["empty", ""],
    ["malformed", "not-a-valid-bearer\n"],
    ["extra lines", `${TOKEN}\nextra\n`],
  ]) {
    await t.test(name, async () => {
      const { parent, path } = await temporaryPath();
      await mkdir(parent, { mode: 0o700 });
      await writeFile(path, contents, { mode: 0o600 });
      await assert.rejects(readSecureToken(path), /empty or malformed/);
    });
  }
});

test("auth helper prints the persistent bearer without diagnostics", async () => {
  const { path } = await temporaryPath();
  const helper = fileURLToPath(new URL("../bin/cc-openai-proxy-auth.js", import.meta.url));
  const first = await execFileAsync(process.execPath, [helper, "--auth-token-file", path]);
  const second = await execFileAsync(process.execPath, [helper, "--auth-token-file", path]);
  assert.match(first.stdout, /^[A-Za-z0-9_-]{43}\n$/);
  assert.equal(second.stdout, first.stdout);
  assert.equal(first.stderr, "");
  assert.equal(second.stderr, "");
});

test("auth helper rejects an insecure file without logging its contents", async () => {
  const { parent, path } = await temporaryPath();
  const secret = "private-malformed-secret";
  const helper = fileURLToPath(new URL("../bin/cc-openai-proxy-auth.js", import.meta.url));
  await mkdir(parent, { mode: 0o700 });
  await writeFile(path, secret, { mode: 0o600 });
  await assert.rejects(
    execFileAsync(process.execPath, [helper, "--auth-token-file", path]),
    (error) => {
      const diagnostic = JSON.parse(error.stderr);
      assert.deepEqual(diagnostic, {
        origin: "cc-openai-proxy-auth",
        category: "malformed_token",
        path,
      });
      assert.equal(error.stderr.includes(secret), false);
      assert.equal(error.stderr.includes("empty or malformed"), false);
      return true;
    },
  );
});
