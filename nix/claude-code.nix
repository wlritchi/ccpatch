# Preserve the Bun container: do not strip or patch the ELF interpreter.
# Linux execution requires a host FHS loader. Darwin repacking is experimental.
{
  lib,
  stdenvNoCC,
  fetchurl,
  bash,
  ccpatch,
  cc-openai-proxy-launcher,
}:

let
  release = builtins.fromJSON (builtins.readFile ../upstream.json);
  inherit (release) version sources;
  system = stdenvNoCC.hostPlatform.system;
  source = sources.${system} or (throw "claude-code: unsupported system ${system}");
in
stdenvNoCC.mkDerivation {
  pname = "claude-code-patched";
  inherit version;

  src = fetchurl {
    url = "https://registry.npmjs.org/@anthropic-ai/claude-code-${source.platform}/-/claude-code-${source.platform}-${version}.tgz";
    inherit (source) hash;
  };

  dontConfigure = true;
  nativeBuildInputs = [ ccpatch ];

  buildPhase = ''
    runHook preBuild
    ccpatch apply ./claude -o ./claude-patched --version ${version} --no-smoke
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -Dm755 ./claude-patched "$out/libexec/claude-code/claude"
    mkdir -p "$out/bin"
    cat > "$out/bin/claude" <<EOF
    #!${bash}/bin/bash
    set -euo pipefail
    source ${cc-openai-proxy-launcher}
    if ! cc_openai_proxy_configure optional claude && [ "\''${CC_OPENAI_PROXY_URL+x}" = x ]; then
      exit 1
    fi

    # Enable truecolor when the terminal supports it.
    if [ -z "\''${CLAUDE_CODE_TMUX_TRUECOLOR:-}" ] && { [ "\''${COLORTERM:-}" = truecolor ] || [ "\''${COLORTERM:-}" = 24bit ]; }; then
      export CLAUDE_CODE_TMUX_TRUECOLOR=1
    fi
    exec "$out/libexec/claude-code/claude" "\$@"
    EOF
    chmod +x "$out/bin/claude"
    runHook postInstall
  '';

  dontStrip = true;
  dontPatchELF = true;
  dontFixup = true;

  meta = {
    description = "Claude Code with the ccpatch compatibility layer";
    homepage = "https://github.com/wlritchi/ccpatch";
    license = lib.licenses.unfree;
    mainProgram = "claude";
    platforms = builtins.attrNames sources;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
