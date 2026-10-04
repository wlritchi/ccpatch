import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  defaultProxyAuthPath,
  loadOrCreateProxyToken,
  readExternalToken,
  readSecureToken,
  resolveProxyAuthConfig,
  validateToken,
} from "../bin/proxy-auth.js";
import { parseArgs as parseHelperArgs } from "../bin/cc-openai-proxy-auth.js";
import { parseArgs as parseProxyArgs } from "../bin/cc-openai-proxy.js";

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
  assert.deepEqual(resolveProxyAuthConfig(undefined, undefined, {}), {
    authTokenFile: defaultProxyAuthPath({}),
    externalAuthToken: false,
  });
  for (const [key, externalAuthToken] of [
    ["CC_OPENAI_PROXY_AUTH_FILE", false],
    ["CC_OPENAI_PROXY_AUTH_SOURCE_FILE", true],
  ]) {
    assert.deepEqual(resolveProxyAuthConfig(undefined, undefined, { [key]: "/env" }), {
      authTokenFile: "/env",
      externalAuthToken,
    });
  }
  const env = {
    CC_OPENAI_PROXY_AUTH_FILE: "/managed-env",
    CC_OPENAI_PROXY_AUTH_SOURCE_FILE: "/external-env",
  };
  assert.deepEqual(resolveProxyAuthConfig("/cli", undefined, env), {
    authTokenFile: "/cli",
    externalAuthToken: false,
  });
  assert.deepEqual(resolveProxyAuthConfig(undefined, "/cli", env), {
    authTokenFile: "/cli",
    externalAuthToken: true,
  });
  assert.throws(() => resolveProxyAuthConfig(undefined, undefined, env), /mutually exclusive/);
  assert.throws(() => resolveProxyAuthConfig("/managed", "/external", {}), /mutually exclusive/);
  assert.throws(() => resolveProxyAuthConfig("", undefined, {}), /must not be empty/);
  assert.throws(() => resolveProxyAuthConfig(undefined, "", {}), /must not be empty/);
});

test("proxy and helper select the same auth mode with CLI precedence", (t) => {
  const original = { ...process.env };
  t.after(() => {
    for (const key of ["CC_OPENAI_PROXY_AUTH_FILE", "CC_OPENAI_PROXY_AUTH_SOURCE_FILE"]) {
      if (original[key] === undefined) delete process.env[key];
      else process.env[key] = original[key];
    }
  });
  for (const parseArgs of [parseProxyArgs, parseHelperArgs]) {
    delete process.env.CC_OPENAI_PROXY_AUTH_FILE;
    process.env.CC_OPENAI_PROXY_AUTH_SOURCE_FILE = "/external-env";
    assert.equal(parseArgs([]).externalAuthToken, true);
    assert.equal(parseArgs([]).authTokenFile, "/external-env");
    const managed = parseArgs(["--auth-token-file", "/managed-cli"]);
    assert.equal(managed.externalAuthToken, false);
    assert.equal(managed.authTokenFile, "/managed-cli");
    process.env.CC_OPENAI_PROXY_AUTH_FILE = "/managed-env";
    assert.throws(() => parseArgs([]), /mutually exclusive/);
    const external = parseArgs(["--auth-token-source-file", "/external-cli"]);
    assert.equal(external.externalAuthToken, true);
    assert.equal(external.authTokenFile, "/external-cli");
    for (const option of ["--auth-token-file", "--auth-token-source-file"]) {
      assert.throws(() => parseArgs([option]), /must not be empty/);
      assert.throws(() => parseArgs([option, ""]), /must not be empty/);
    }
    assert.throws(
      () => parseArgs(["--auth-token-file", "/managed", "--auth-token-source-file", "/external"]),
      /mutually exclusive/,
    );
    delete process.env.CC_OPENAI_PROXY_AUTH_FILE;
    process.env.CC_OPENAI_PROXY_AUTH_SOURCE_FILE = "";
    assert.throws(() => parseArgs([]), /must not be empty/);
  }
});

test("token validation accepts variable lengths and printable non-whitespace ASCII", () => {
  for (const token of ["a", "x".repeat(256), "token+/=.:~!#$%&'()*,-;<>?@[\\]^_`{|}\"", TOKEN]) {
    assert.equal(validateToken(token), token);
  }
  for (const token of [
    undefined,
    null,
    1,
    "",
    " ",
    "a b",
    " a",
    "a ",
    "a\t",
    "a\n",
    "a\r",
    "a\0",
    "a\x7f",
    "café",
    "🔑",
  ]) {
    assert.throws(() => validateToken(token), /empty or malformed/);
  }
});

test("both file modes accept variable-length tokens and one final LF or CRLF", async () => {
  const { parent, path } = await temporaryPath();
  await mkdir(parent, { mode: 0o700 });
  for (const token of ["a", "x".repeat(256), "token+/=.:~", TOKEN]) {
    for (const ending of ["", "\n", "\r\n"]) {
      await writeFile(path, `${token}${ending}`, { mode: 0o600 });
      assert.equal(await loadOrCreateProxyToken(path), token);
      assert.equal(await readExternalToken(path), token);
    }
  }
});

test("external token reads follow Secret-volume symlinks without changing modes or contents", async () => {
  const { root, parent, path } = await temporaryPath();
  const version = join(root, "..version");
  await mkdir(version);
  const target = join(version, "bearer");
  await writeFile(target, "mounted-secret\r\n", { mode: 0o444 });
  await chmod(version, 0o555);
  await symlink("..version", join(root, "..data"));
  await symlink("..data", parent);
  const before = await lstat(target);
  assert.equal(await readExternalToken(path), "mounted-secret");
  const after = await lstat(target);
  assert.equal(after.mode, before.mode);
  assert.equal(after.uid, before.uid);
  assert.equal(after.mtimeMs, before.mtimeMs);
  assert.equal(await readFile(target, "utf8"), "mounted-secret\r\n");
  assert.equal((await lstat(version)).mode & 0o777, 0o555);
  await assert.rejects(loadOrCreateProxyToken(path), /parent is not a directory/);
  const link = join(root, "bearer-link");
  await symlink("..data/bearer", link);
  assert.equal(await readExternalToken(link), "mounted-secret");
});

test("external tokens permit a different owner while managed tokens reject it", async (t) => {
  if (typeof process.getuid !== "function") return t.skip("requires POSIX ownership");
  const { parent, path } = await temporaryPath();
  await mkdir(parent, { mode: 0o700 });
  await writeFile(path, "external-owner-token", { mode: 0o600 });
  const uid = process.getuid();
  t.mock.method(process, "getuid", () => uid + 1);
  assert.equal(await readExternalToken(path), "external-owner-token");
  await assert.rejects(readSecureToken(path), /not owned by the current user/);
  await assert.rejects(loadOrCreateProxyToken(path), /not owned by the current user/);
});

test(
  "external token reads reject FIFOs without waiting for a writer",
  { timeout: 5000 },
  async (t) => {
    if (process.platform === "win32") return t.skip("requires POSIX FIFOs");
    const { root } = await temporaryPath();
    const path = join(root, "fifo");
    await execFileAsync("mkfifo", [path]);
    await assert.rejects(readExternalToken(path), /not a regular file/);
  },
);

test("external token reads reject missing or non-regular sources without creating files", async () => {
  const { root, parent, path } = await temporaryPath();
  await assert.rejects(readExternalToken(path), { code: "ENOENT" });
  await assert.rejects(lstat(parent), { code: "ENOENT" });
  await assert.rejects(readExternalToken(root), /not a regular file/);
  const dangling = join(root, "dangling");
  await symlink(path, dangling);
  await assert.rejects(readExternalToken(dangling), { code: "ENOENT" });
  await assert.rejects(lstat(path), { code: "ENOENT" });
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
    ["whitespace", "not a valid bearer\n"],
    ["only newline", "\r\n"],
    ["extra lines", `${TOKEN}\nextra\n`],
    ["extra newline", `${TOKEN}\n\n`],
    ["bare carriage return", `${TOKEN}\r`],
    ["control character", `${TOKEN}\0`],
    ["non-ASCII", "secret-é"],
    ["invalid UTF-8", Buffer.from([0xff])],
  ]) {
    await t.test(name, async () => {
      const { parent, path } = await temporaryPath();
      await mkdir(parent, { mode: 0o700 });
      await writeFile(path, contents, { mode: 0o600 });
      await assert.rejects(readSecureToken(path), /empty or malformed/);
      await assert.rejects(readExternalToken(path), /empty or malformed/);
      await assert.rejects(loadOrCreateProxyToken(path), /empty or malformed/);
      assert.deepEqual(await readFile(path), Buffer.from(contents));
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

test("auth helper reads external files selected by CLI or environment", async () => {
  const { root } = await temporaryPath();
  const target = join(root, "secret");
  const path = join(root, "source");
  await writeFile(target, "external-token\n", { mode: 0o444 });
  await symlink(target, path);
  const helper = fileURLToPath(new URL("../bin/cc-openai-proxy-auth.js", import.meta.url));
  for (const args of [["--auth-token-source-file", path], []]) {
    const result = await execFileAsync(process.execPath, [helper, ...args], {
      env: {
        ...process.env,
        CC_OPENAI_PROXY_AUTH_FILE: undefined,
        CC_OPENAI_PROXY_AUTH_SOURCE_FILE: path,
      },
    });
    assert.equal(result.stdout, "external-token\n");
    assert.equal(result.stderr, "");
  }
});

test("external startup fails without exposing invalid secrets or creating missing sources", async () => {
  const { root } = await temporaryPath();
  const path = join(root, "invalid");
  const missing = join(root, "missing", "token");
  const secret = "private invalid secret";
  await writeFile(path, secret, { mode: 0o444 });
  for (const name of ["cc-openai-proxy-auth.js", "cc-openai-proxy.js"]) {
    const executable = fileURLToPath(new URL(`../bin/${name}`, import.meta.url));
    for (const [source, category] of [
      [path, "malformed_token"],
      [missing, "filesystem_error"],
    ]) {
      await assert.rejects(
        execFileAsync(process.execPath, [executable, "--auth-token-source-file", source]),
        (error) => {
          assert.equal(error.code, 2);
          assert.equal(error.stdout, "");
          const diagnostic = JSON.parse(error.stderr);
          assert.equal(diagnostic.category, category);
          assert.equal(diagnostic.path, source);
          assert.equal(error.stderr.includes(secret), false);
          assert.equal(error.stderr.includes("server_started"), false);
          return true;
        },
      );
    }
  }
  await assert.rejects(lstat(missing), { code: "ENOENT" });
  assert.equal(await readFile(path, "utf8"), secret);
});

test("auth helper rejects an insecure file without logging its contents", async () => {
  const { parent, path } = await temporaryPath();
  const secret = "private malformed secret";
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
