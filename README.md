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

For a separately managed proxy, set both `CC_OPENAI_PROXY_URL` and
`CC_OPENAI_PROXY_AUTH_TOKEN`. An explicit but unusable configuration is an error;
the launcher does not silently fall back. Use TLS or a trusted local tunnel when
the proxy is not on localhost.

The local bearer is stored in
`${XDG_STATE_HOME:-$HOME/.local/state}/cc-openai-proxy/auth-token` on Linux, or
`~/Library/Application Support/cc-openai-proxy/auth-token` on macOS. The
`cc-openai-proxy-auth` helper creates it with private permissions.

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
