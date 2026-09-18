#!/usr/bin/env node
import { loadOrCreateProxyToken, proxyAuthDiagnostic, resolveProxyAuthPath } from "./proxy-auth.js";

function usage() {
  return `usage: cc-openai-proxy-auth [--auth-token-file PATH]

Environment:
  CC_OPENAI_PROXY_AUTH_FILE  Proxy bearer file (platform default when unset)
`;
}

function parseArgs(argv) {
  let authTokenFile;
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--auth-token-file") {
      authTokenFile = argv[++i];
      if (!authTokenFile) throw new Error("auth token file must not be empty");
    } else if (arg === "--help" || arg === "-h") {
      process.stdout.write(usage());
      return undefined;
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  if (authTokenFile === "") throw new Error("auth token file must not be empty");
  return resolveProxyAuthPath(authTokenFile);
}

async function main() {
  const path = parseArgs(process.argv.slice(2));
  if (path === undefined) return;
  try {
    const token = await loadOrCreateProxyToken(path);
    process.stdout.write(`${token}\n`);
  } catch (error) {
    error.authPath = path;
    throw error;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    process.stderr.write(
      `${JSON.stringify({
        origin: "cc-openai-proxy-auth",
        ...proxyAuthDiagnostic(error, error?.authPath),
      })}\n`,
    );
    process.exitCode = 2;
  });
}

export { parseArgs };
