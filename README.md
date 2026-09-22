# ccpatch

A compatibility layer for Claude Code: a Python binary patcher, a local provider
proxy, and a coordinated launcher.

The patcher rewrites embedded JavaScript and repacks the Bun executable. The
proxy translates Anthropic Messages requests to the pi-ai OpenAI Codex provider.
The launcher checks proxy authentication before it enables OpenAI models.

## Build

```sh
nix build
./result/bin/claude --version
nix build .#ccpatch .#cc-openai-proxy
```

The default package is `claude-code-patched`. Other outputs are `ccpatch`,
`cc-openai-proxy`, and `cc-openai-proxy-launcher`. All four platform sources are
pinned in `upstream.json`; `flake.lock` pins the Nix build dependencies.

Linux execution currently requires an FHS host loader. The executable is not
modified with `patchelf`, which could damage the Bun container. NixOS needs a
separate loader/FHS setup. Darwin repacking remains experimental; evaluating a
Darwin derivation does not validate it on a Mac.

## Nix integration

Consumers can pin an immutable release tag, for example
`github:wlritchi/ccpatch/v2.1.274-ccpatch.1`, once that release is published. The
lockfile records the exact commit. A release tag versions the patcher, proxy,
launcher, and upstream binary together. Never move an existing release tag;
increment the `ccpatch` revision for another release of the same Claude version.
The Python package uses the corresponding PEP 440 version `2.1.274.post1`.

Use `packages.${system}.default` directly, or use `lib.mkPackages pkgs` or
`overlays.default` to build with the consumer's package set. Consumers of the
latter interfaces must allow the unfree `claude-code-patched` package.

For Home Manager, import `homeManagerModules.default` and set:

```nix
programs.ccpatch.enable = true;
nixpkgs.config.allowUnfreePredicate = pkg:
  builtins.elem (lib.getName pkg) [ "claude-code-patched" ];
```

This installs Claude and the proxy and registers a systemd user service on Linux
or a launchd agent on macOS. It does not import credentials or personal model
preferences. Set `programs.ccpatch.proxy.enable = false` to manage the service
separately. `programs.ccpatch.proxy.environment` accepts non-secret settings such
as `CC_OPENAI_AUTH_FILE`; do not put credentials in Nix values or the Nix store.

## Proxy configuration

The managed service listens on `127.0.0.1:17780`. The launcher starts it when
needed. Set `CC_OPENAI_PROXY_AUTOSTART=0` to disable service startup. Without an
available authenticated proxy, ordinary Claude use continues but OpenAI models
are unavailable.

The proxy reads provider credentials from `~/.pi/agent/auth.json` by default.
`CC_OPENAI_AUTH_FILE` or `PI_AUTH_FILE` can select another credential file. OAuth
refreshes are persisted there. Run `cc-openai-proxy --help` for model, transport,
and cache settings.

### Several Codex subscriptions

`CC_OPENAI_AUTH_FILES` lists several pi auth files separated by the platform path
delimiter (`:` on Linux and macOS). Each file is one ChatGPT account. To log a
second account in with the pi CLI, point it at another directory and run
`/login`:

```sh
PI_CODING_AGENT_DIR=~/.pi/agent-2 pi
```

Then set `CC_OPENAI_AUTH_FILES=~/.pi/agent/auth.json:~/.pi/agent-2/auth.json`
(expanded, not literal `~`) in the proxy environment.

The proxy reads each account's usage from the Codex usage endpoint (no quota is
consumed) and refreshes it every `CC_OPENAI_USAGE_TTL_MS` milliseconds (default
300000). Requests are spread over accounts in proportion to remaining capacity
per hour until reset: an account at 10% remaining with a reset in six hours
weighs the same as one with half the plan capacity, 10% remaining, and a reset
in three hours. Plan capacities are relative multipliers from a built-in table
(`plus` 1, `prolite` 5, `pro` 10, ...); `CC_OPENAI_PLAN_CAPACITY=plus=1,pro=10`
overrides entries. A Claude Code session stays on the account it first used
while that account is available, which keeps the Codex prompt cache warm.

### Usage limits

When an account reports a usage limit, the proxy marks it unavailable until the
reset time and retries the request on another account. When no account is
available, the proxy answers with an Anthropic-shaped 429 (`rate_limit_error`)
carrying the `anthropic-ratelimit-unified-*` headers, with the earliest reset
across accounts. Claude Code then shows its usage-limit dialog ("You've hit your
weekly limit · resets ...") and can wait for the reset instead of reporting a
server error. Claude Code only waits automatically when the reset is less than
24 hours away; weekly Codex windows usually exceed that.

Successful responses carry the same headers with `allowed` status and the
serving account's window utilization, so Claude Code's usage warnings reflect
the Codex plan. Set `CC_OPENAI_USAGE_HEADERS=0` to omit them. Transient
per-minute rate limits are returned as a plain 429 with `retry-after`, which
Claude Code retries by itself.

For a separately managed proxy, set both `CC_OPENAI_PROXY_URL` and
`CC_OPENAI_PROXY_AUTH_TOKEN`. An explicit but unusable configuration is an error;
the launcher does not silently fall back. Use TLS or a trusted local tunnel when
the proxy is not on localhost.

The local bearer is stored in
`${XDG_STATE_HOME:-$HOME/.local/state}/cc-openai-proxy/auth-token` on Linux, or
`~/Library/Application Support/cc-openai-proxy/auth-token` on macOS. The
`cc-openai-proxy-auth` helper creates it with private permissions.

### Tool call IDs

The proxy encodes tool call IDs that contain characters outside `A-Z`, `a-z`,
`0-9`, `_`, and `-`. The wire format is `ccpatch_tc1_` followed by the original
UTF-8 ID in unpadded base64url. IDs that already start with this reserved prefix
are encoded too. Other valid IDs are unchanged. The proxy decodes one layer on
both tool calls and tool results before conversion to OpenAI requests.

pi-ai combines the OpenAI tool call ID and Responses item ID as
`call_id|item_id`. The encoding preserves both IDs without truncation; two
64-character IDs produce a 184-character wire ID. It does not store account or
routing information and does not require a persistent lookup table.

Legacy unencoded IDs remain supported on input. Malformed encoding envelopes
are left unchanged. The prefix is reserved: an old external ID that exactly
matches a canonical envelope cannot be distinguished from an encoded ID.
New responses escape that case. Stored sessions are not rewritten, so old
OpenAI tool calls can still prevent a direct switch to Claude.

### Migrate existing transcripts

Preview all transcripts under `~/.claude/projects`, including subagent transcripts:

```sh
ccpatch migrate-tool-ids
# From this repository: uv run ccpatch migrate-tool-ids
```

Stop all Claude sessions that write to this directory, then apply the migration
from a separate terminal:

```sh
ccpatch migrate-tool-ids --apply
```

Use `--projects-dir PATH` for another machine's copied transcript directory or a
custom Claude configuration directory. `--dry-run` explicitly selects the default
read-only preview. The tool does not change credentials or send transcript data
anywhere.

The migration recognizes `call_…|fc_…` and `call_…|ctc_…` IDs. It encodes tool-call
and tool-result fields with the proxy's wire format, including repeated messages,
embedded progress messages, and tool-reference metadata. It leaves native IDs,
already encoded IDs, message text, tool inputs, and tool-output bodies unchanged.
Other unknown composite formats are not migrated. Existing OpenAI sessions remain
compatible with the updated proxy after migration.

Each affected file gets an exact, private (`0600`) adjacent backup named
`SESSION.jsonl.ccpatch-tool-ids-UNIQUE.bak` before atomic replacement. Backups are
not scanned on subsequent runs; rerunning the migration is safe. To restore a
file, stop its Claude session and copy the corresponding backup over the original
`.jsonl` file. Backups contain conversation data: protect and retain them until
you have verified the migrated sessions.

Unchanged JSONL lines remain byte-for-byte identical. Changed lines are
reserialized, with their line endings and all unrelated JSON values preserved.
The replacement retains the file's permission mode, but not its timestamps,
extended attributes, or other filesystem metadata. Malformed records (including
duplicate JSON keys) cause the entire file to be left untouched. Errors are
reported per file with a nonzero exit status; other files can still be migrated.
Symlink files/directories are not followed, and hardlinked files are rejected.

The tool checks for concurrent changes before replacement, but cannot lock out
Claude's writers. **Do not apply it to active sessions**: a writer could append
between that check and the replacement, or retain an open handle to the old file.
It does not modify the messages already loaded in a running Claude process.

## Development and validation

```sh
nix develop
uv sync --locked
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check
uv run pytest
npm --prefix proxy ci
npm --prefix proxy test
nix flake check
```

The Nix proxy build runs its JavaScript tests. Flake checks build all packages
and run the Python suite. Tests that need historical upstream captures are
explicitly skipped when captures are absent; a green unit run is not a full
historical compatibility sweep.

After building on a supported host, exercise the actual patched executable:

```sh
nix build
CCPATCH_TEST_PATCHED_BINARY="$PWD/result/libexec/claude-code/claude" \
  uv run pytest tests/ccpatch/test_elf_integration.py \
    -k patched_binary_help
```

Historical source tests use local, untracked `build/sweep-resume/` captures or
the documented `CCPATCH_*` environment variables in each test. Upstream Claude
binaries and extracted upstream source are not included.

## Upstream software

Claude Code itself is proprietary and its fetched/patched package is marked
unfree. This repository contains build recipes and compatibility code, not
redistributed upstream executables.
