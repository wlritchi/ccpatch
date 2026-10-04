#!/usr/bin/env node
import { loadProxyToken, proxyAuthDiagnostic, resolveProxyAuthConfig } from "./proxy-auth.js";

function usage() {
  return `usage: cc-openai-proxy-auth [--auth-token-file PATH | --auth-token-source-file PATH]

Environment:
  CC_OPENAI_PROXY_AUTH_FILE         Managed proxy bearer file (platform default when unset)
  CC_OPENAI_PROXY_AUTH_SOURCE_FILE  Existing external bearer file (read only; follows symlinks)

The file options are mutually exclusive. A CLI file option overrides both environment variables.
`;
}

function parseArgs(argv) {
  let authTokenFile;
  let authTokenSourceFile;
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--auth-token-file") {
      authTokenFile = argv[++i];
      if (!authTokenFile) throw new Error("auth token file must not be empty");
    } else if (arg === "--auth-token-source-file") {
      authTokenSourceFile = argv[++i];
      if (!authTokenSourceFile) throw new Error("auth token source file must not be empty");
    } else if (arg === "--help" || arg === "-h") {
      process.stdout.write(usage());
      return undefined;
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return resolveProxyAuthConfig(authTokenFile, authTokenSourceFile);
}

async function main() {
  const config = parseArgs(process.argv.slice(2));
  if (config === undefined) return;
  try {
    const token = await loadProxyToken(config);
    process.stdout.write(`${token}\n`);
  } catch (error) {
    error.authPath = config.authTokenFile;
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
