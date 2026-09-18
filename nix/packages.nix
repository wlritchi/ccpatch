{ pkgs }:

rec {
  ccpatch = pkgs.callPackage ./ccpatch.nix { };
  cc-openai-proxy = pkgs.callPackage ./cc-openai-proxy.nix { };
  cc-openai-proxy-launcher = pkgs.callPackage ./cc-openai-proxy-launcher.nix {
    inherit cc-openai-proxy;
  };
  claude-code-patched = pkgs.callPackage ./claude-code.nix {
    inherit ccpatch cc-openai-proxy-launcher;
  };
}
