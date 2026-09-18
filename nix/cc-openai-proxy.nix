{
  lib,
  buildNpmPackage,
  makeWrapper,
  nodejs_24,
}:

buildNpmPackage {
  pname = "cc-openai-proxy";
  version = "0.1.0";

  src = lib.fileset.toSource {
    root = ../proxy;
    fileset = lib.fileset.unions [
      ../proxy/bin
      ../proxy/test
      ../proxy/package.json
      ../proxy/package-lock.json
    ];
  };
  npmDepsHash = "sha256-VA3ZDuSe8JHpORk7DWB+7EnaOOM+dPlCEAzNuRlSJEc=";
  nodejs = nodejs_24;

  dontNpmBuild = true;
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    npm test
    runHook postCheck
  '';
  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/libexec/cc-openai-proxy" "$out/bin"
    cp -R bin package.json node_modules "$out/libexec/cc-openai-proxy/"
    makeWrapper ${nodejs_24}/bin/node "$out/bin/cc-openai-proxy" \
      --add-flags "$out/libexec/cc-openai-proxy/bin/cc-openai-proxy.js"
    makeWrapper ${nodejs_24}/bin/node "$out/bin/cc-openai-proxy-auth" \
      --add-flags "$out/libexec/cc-openai-proxy/bin/cc-openai-proxy-auth.js"

    runHook postInstall
  '';

  meta = {
    description = "Anthropic Messages proxy from Claude Code to pi-ai OpenAI Codex";
    mainProgram = "cc-openai-proxy";
    platforms = lib.platforms.unix;
  };
}
