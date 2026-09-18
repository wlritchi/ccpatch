{
  lib,
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "ccpatch";
  version = (builtins.fromTOML (builtins.readFile ../pyproject.toml)).project.version;
  pyproject = true;
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../src/ccpatch
    ];
  };
  build-system = [ python3Packages.hatchling ];
  dependencies = lib.optionals python3Packages.stdenv.hostPlatform.isDarwin [
    python3Packages.lief
  ];
  pythonImportsCheck = [ "ccpatch" ];
  meta = {
    description = "Patch and repack Claude Code Bun executables";
    mainProgram = "ccpatch";
    platforms = lib.platforms.unix;
  };
}
