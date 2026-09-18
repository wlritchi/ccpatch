{
  description = "Claude Code patches and provider compatibility proxy";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/release-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor =
        system:
        import nixpkgs {
          inherit system;
          config.allowUnfreePredicate = pkg: nixpkgs.lib.getName pkg == "claude-code-patched";
        };
    in
    {
      lib.mkPackages = pkgs: import ./nix/packages.nix { inherit pkgs; };
      overlays.default = final: _prev: self.lib.mkPackages final;
      homeManagerModules.default = import ./nix/home-manager.nix;

      packages = forAllSystems (
        system:
        let
          packages = self.lib.mkPackages (pkgsFor system);
        in
        packages // { default = packages.claude-code-patched; }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          inherit (self.packages.${system}) ccpatch cc-openai-proxy claude-code-patched;
          unit-tests =
            pkgs.runCommand "ccpatch-tests"
              {
                nativeBuildInputs = [
                  (pkgs.python3.withPackages (ps: [
                    ps.pytest
                    ps.pytest-mock
                  ]))
                  pkgs.nodejs_24
                ];
              }
              ''
                export HOME="$TMPDIR/home"
                mkdir -p "$HOME"
                cp -R ${./tests} tests
                chmod -R u+w tests
                export PYTHONPATH=${./src}
                mkdir nix
                cp ${./nix/claude-code.nix} nix/claude-code.nix
                pytest tests
                touch "$out"
              '';
        }
      );

      formatter = forAllSystems (system: (pkgsFor system).nixfmt);
      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.python3
              pkgs.uv
              pkgs.nodejs_24
              pkgs.nixfmt
              pkgs.oxfmt
            ];
          };
        }
      );
    };
}
