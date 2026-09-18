{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.programs.ccpatch;
  packages = import ./packages.nix { inherit pkgs; };
  authFile =
    if pkgs.stdenv.hostPlatform.isDarwin then
      "${config.home.homeDirectory}/Library/Application Support/cc-openai-proxy/auth-token"
    else
      "${config.xdg.stateHome}/cc-openai-proxy/auth-token";
  command = [
    "${cfg.proxy.package}/bin/cc-openai-proxy"
    "--host"
    "127.0.0.1"
    "--port"
    "17780"
    "--auth-token-file"
    authFile
  ];
in
{
  options.programs.ccpatch = {
    enable = lib.mkEnableOption "patched Claude Code and its compatibility proxy";
    package = lib.mkOption {
      type = lib.types.package;
      default = packages.claude-code-patched;
      description = "Patched Claude Code package.";
    };
    proxy = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Run the local compatibility proxy as a user service.";
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = packages.cc-openai-proxy;
        description = "Compatibility proxy package.";
      };
      environment = lib.mkOption {
        type = lib.types.attrsOf lib.types.str;
        default = { };
        description = "Proxy settings. Use credential file paths, not secret values.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [
      cfg.package
      cfg.proxy.package
    ];

    systemd.user.services.cc-openai-proxy =
      lib.mkIf (cfg.proxy.enable && pkgs.stdenv.hostPlatform.isLinux)
        {
          Unit.Description = "Claude Code compatibility proxy";
          Service = {
            Type = "simple";
            ExecStart = lib.escapeShellArgs command;
            Environment = lib.mapAttrsToList (name: value: "${name}=${value}") cfg.proxy.environment;
            UMask = "0077";
            Restart = "on-failure";
            RestartSec = 1;
          };
          Install.WantedBy = [ "default.target" ];
        };

    launchd.agents.cc-openai-proxy = lib.mkIf (cfg.proxy.enable && pkgs.stdenv.hostPlatform.isDarwin) {
      enable = true;
      config = {
        ProgramArguments = command;
        EnvironmentVariables = cfg.proxy.environment;
        Umask = 63;
        RunAtLoad = true;
        KeepAlive = true;
        ProcessType = "Background";
        ThrottleInterval = 1;
        StandardOutPath = "${config.home.homeDirectory}/Library/Logs/cc-openai-proxy.log";
        StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/cc-openai-proxy.log";
      };
    };
  };
}
