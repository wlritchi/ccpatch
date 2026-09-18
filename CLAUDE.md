# ccpatch development

- Keep the patcher, proxy, launcher, and integration tests in one release.
- Update `upstream.json` for upstream versions and hashes. Release tags use
  `v<claude-version>-ccpatch.<revision>`; Python versions use `<version>.post<revision>`.
- Use `uv run ruff format`, `uv run ruff check`, `uv run ty check`, `nixfmt`,
  and `oxfmt` for changed sources. Run Python, proxy, and Nix checks.
- Do not commit upstream binaries, extracted upstream JavaScript, or credentials.
- Nix sandbox builds perform structural verification, not a host runtime smoke
  test. Exercise the built executable on the host before reporting it validated.
- Linux uses the host FHS loader. Darwin repacking is experimental.
- Use in-project `.claude/worktrees/` worktrees. Keep commits signed; do not
  bypass signing if the Yubikey is unavailable.
