import { randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, unlink } from "node:fs/promises";
import { homedir, platform } from "node:os";
import { basename, dirname, join } from "node:path";

const DIRECTORY_MODE = 0o700;
const FILE_MODE = 0o600;
const INVALID_TOKEN_CHARACTER = /[^\x21-\x7e]/;
const SAFE_ERROR_CODE = /^[A-Z][A-Z0-9_]{0,31}$/;

function authError(category, message) {
  const error = new Error(message);
  error.category = category;
  return error;
}

function proxyAuthDiagnostic(error, path) {
  const code =
    typeof error?.code === "string" && SAFE_ERROR_CODE.test(error.code) ? error.code : undefined;
  return {
    category: error?.category || (code ? "filesystem_error" : "configuration_error"),
    ...(code ? { code } : {}),
    ...(path ? { path } : {}),
  };
}

function defaultProxyAuthPath(env = process.env, os = platform(), home = homedir()) {
  if (os === "darwin") {
    return join(home, "Library", "Application Support", "cc-openai-proxy", "auth-token");
  }
  const stateHome = env.XDG_STATE_HOME || join(home, ".local", "state");
  return join(stateHome, "cc-openai-proxy", "auth-token");
}

function resolveProxyAuthConfig(authTokenFile, authTokenSourceFile, env = process.env) {
  if (authTokenFile === undefined && authTokenSourceFile === undefined) {
    authTokenFile = env.CC_OPENAI_PROXY_AUTH_FILE;
    authTokenSourceFile = env.CC_OPENAI_PROXY_AUTH_SOURCE_FILE;
  }
  if (authTokenFile !== undefined && authTokenSourceFile !== undefined) {
    throw authError("configuration_error", "proxy auth file options are mutually exclusive");
  }
  if (authTokenFile === "" || authTokenSourceFile === "") {
    throw authError("configuration_error", "proxy auth file path must not be empty");
  }
  return {
    authTokenFile: authTokenSourceFile ?? authTokenFile ?? defaultProxyAuthPath(env),
    externalAuthToken: authTokenSourceFile !== undefined,
  };
}

function assertOwned(stat, description) {
  if (typeof process.getuid === "function" && stat.uid !== process.getuid()) {
    throw authError("ownership_error", `${description} is not owned by the current user`);
  }
}

async function ensureSecureParent(path) {
  const parent = dirname(path);
  await mkdir(parent, { recursive: true, mode: DIRECTORY_MODE });
  const stat = await lstat(parent);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw authError("file_type_error", "proxy auth parent is not a directory");
  }
  assertOwned(stat, "proxy auth parent");
  if ((stat.mode & 0o777) !== DIRECTORY_MODE) {
    throw authError("permission_error", "proxy auth parent mode must be 0700");
  }
}

function validateToken(token) {
  if (typeof token !== "string" || token.length === 0 || INVALID_TOKEN_CHARACTER.test(token)) {
    throw authError("malformed_token", "proxy auth token is empty or malformed");
  }
  return token;
}

function parseTokenFile(contents) {
  const token = contents.endsWith("\r\n")
    ? contents.slice(0, -2)
    : contents.endsWith("\n")
      ? contents.slice(0, -1)
      : contents;
  return validateToken(token);
}

async function readExternalToken(path) {
  const handle = await open(path, constants.O_RDONLY | constants.O_NONBLOCK);
  try {
    if (!(await handle.stat()).isFile()) {
      throw authError("file_type_error", "proxy auth token source is not a regular file");
    }
    return parseTokenFile(await handle.readFile("utf8"));
  } finally {
    await handle.close();
  }
}

async function loadProxyToken(config) {
  return config.externalAuthToken
    ? readExternalToken(config.authTokenFile)
    : loadOrCreateProxyToken(config.authTokenFile);
}

async function readSecureToken(path) {
  const symbolicLinkGuard = constants.O_NOFOLLOW ?? 0;
  const handle = await open(path, constants.O_RDONLY | symbolicLinkGuard);
  try {
    const stat = await handle.stat();
    if (!stat.isFile())
      throw authError("file_type_error", "proxy auth token is not a regular file");
    assertOwned(stat, "proxy auth token");
    if ((stat.mode & 0o777) !== FILE_MODE) {
      throw authError("permission_error", "proxy auth token mode must be 0600");
    }
    return parseTokenFile(await handle.readFile("utf8"));
  } finally {
    await handle.close();
  }
}

async function syncDirectory(path) {
  const handle = await open(path, constants.O_RDONLY);
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function createSecureToken(path) {
  const token = randomBytes(32).toString("base64url");
  const parent = dirname(path);
  const temporaryPath = join(
    parent,
    `.${basename(path)}.${process.pid}.${randomBytes(12).toString("hex")}.tmp`,
  );
  const symbolicLinkGuard = constants.O_NOFOLLOW ?? 0;
  let handle;

  try {
    handle = await open(
      temporaryPath,
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | symbolicLinkGuard,
      FILE_MODE,
    );
    const stat = await handle.stat();
    if (!stat.isFile())
      throw authError("file_type_error", "proxy auth temporary file is not a regular file");
    assertOwned(stat, "proxy auth temporary file");
    if ((stat.mode & 0o777) !== FILE_MODE) {
      throw authError("permission_error", "proxy auth temporary file mode must be 0600");
    }
    await handle.writeFile(`${token}\n`, "utf8");
    await handle.sync();
    await handle.close();
    handle = undefined;

    try {
      await link(temporaryPath, path);
      await syncDirectory(parent);
      return token;
    } catch (error) {
      if (error?.code === "EEXIST") return await readSecureToken(path);
      throw error;
    }
  } finally {
    if (handle) await handle.close();
    await unlink(temporaryPath).catch((error) => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
}

async function loadOrCreateProxyToken(path) {
  await ensureSecureParent(path);
  try {
    return await readSecureToken(path);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    return createSecureToken(path);
  }
}

export {
  defaultProxyAuthPath,
  loadOrCreateProxyToken,
  loadProxyToken,
  proxyAuthDiagnostic,
  readExternalToken,
  readSecureToken,
  resolveProxyAuthConfig,
  validateToken,
};
