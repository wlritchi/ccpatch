{
  writeText,
  cc-openai-proxy,
  curl,
  coreutils,
  jq,
}:

writeText "cc-openai-proxy-launcher.sh" ''
  cc_openai_proxy_note() {
    if [ "$cc_openai_proxy_mode" = required ] || [ "$cc_openai_proxy_explicit" = 1 ]; then
      printf '%s\n' "$cc_openai_proxy_command: $1" >&2
    fi
  }

  cc_openai_proxy_default_auth_file() {
    case "$(${coreutils}/bin/uname -s)" in
      Darwin)
        printf '%s\n' "$HOME/Library/Application Support/cc-openai-proxy/auth-token"
        ;;
      *)
        printf '%s\n' "''${XDG_STATE_HOME:-$HOME/.local/state}/cc-openai-proxy/auth-token"
        ;;
    esac
  }

  cc_openai_proxy_is_nonblank() {
    case "$1" in
      *[![:space:]]*) return 0 ;;
      *) return 1 ;;
    esac
  }

  cc_openai_proxy_is_running() {
    ${curl}/bin/curl --fail --silent --max-time 1 \
      "$cc_openai_proxy_base_url/health" >/dev/null 2>&1
  }

  cc_openai_proxy_manage() {
    cc_openai_proxy_action="$1"
    case "$(${coreutils}/bin/uname -s)" in
      Linux)
        if ! command -v systemctl >/dev/null 2>&1; then
          cc_openai_proxy_note "systemctl is unavailable"
          return 1
        fi
        systemctl --user "$cc_openai_proxy_action" cc-openai-proxy.service
        ;;
      Darwin)
        cc_openai_proxy_kickstart_flags=""
        if [ "$cc_openai_proxy_action" = restart ]; then
          cc_openai_proxy_kickstart_flags="-k"
        fi
        # shellcheck disable=SC2086
        /bin/launchctl kickstart $cc_openai_proxy_kickstart_flags \
          "gui/$(${coreutils}/bin/id -u)/org.nix-community.home.cc-openai-proxy"
        ;;
      *)
        cc_openai_proxy_note "the platform service manager is unsupported"
        return 1
        ;;
    esac
  }

  cc_openai_proxy_wait_for_managed() {
    cc_openai_proxy_attempt=0
    while [ "$cc_openai_proxy_attempt" -lt 50 ]; do
      if cc_openai_proxy_is_running; then
        return 0
      fi
      cc_openai_proxy_attempt=$((cc_openai_proxy_attempt + 1))
      ${coreutils}/bin/sleep 0.1
    done
    return 1
  }

  cc_openai_proxy_probe() {
    cc_openai_proxy_probe_result=transport
    cc_openai_proxy_response="$(${coreutils}/bin/mktemp)" || return 1
    cc_openai_proxy_http_code="$({
      printf 'header = "Authorization: Bearer %s"\n' "$cc_openai_proxy_token"
    } | ${curl}/bin/curl --config - --silent --max-time 2 \
      --output "$cc_openai_proxy_response" --write-out '%{http_code}' \
      "$cc_openai_proxy_base_url/capabilities" 2>/dev/null)" || {
      ${coreutils}/bin/rm -f "$cc_openai_proxy_response"
      return 1
    }

    case "$cc_openai_proxy_http_code" in
      200)
        if ${jq}/bin/jq --exit-status '.openaiAuthUsable == true' \
          "$cc_openai_proxy_response" >/dev/null 2>&1; then
          cc_openai_proxy_probe_result=ok
        elif ${jq}/bin/jq --exit-status \
          'type == "object" and .openaiAuthUsable == false' \
          "$cc_openai_proxy_response" >/dev/null 2>&1; then
          cc_openai_proxy_probe_result=auth-false
        else
          cc_openai_proxy_probe_result=malformed
        fi
        ;;
      401)
        cc_openai_proxy_probe_result=unauthorized
        ;;
      503)
        cc_openai_proxy_probe_result=unavailable
        ;;
      *)
        cc_openai_proxy_probe_result="http-$cc_openai_proxy_http_code"
        ;;
    esac
    ${coreutils}/bin/rm -f "$cc_openai_proxy_response"
    [ "$cc_openai_proxy_probe_result" = ok ]
  }

  cc_openai_proxy_probe_note() {
    case "$cc_openai_proxy_probe_result" in
      transport)
        cc_openai_proxy_note "proxy transport is unavailable"
        ;;
      unauthorized)
        cc_openai_proxy_note "proxy rejected the configured bearer (HTTP 401)"
        ;;
      auth-false)
        cc_openai_proxy_note "proxy reports that OpenAI authentication is not usable"
        ;;
      unavailable)
        cc_openai_proxy_note "proxy capability probe is unavailable (HTTP 503)"
        ;;
      malformed)
        cc_openai_proxy_note "proxy returned a malformed capability response"
        ;;
      http-*)
        cc_openai_proxy_note "proxy capability probe failed (HTTP ''${cc_openai_proxy_probe_result#http-})"
        ;;
      *)
        cc_openai_proxy_note "proxy capability probe failed"
        ;;
    esac
  }

  cc_openai_proxy_configure() {
    cc_openai_proxy_mode="$1"
    cc_openai_proxy_command="$2"
    cc_openai_proxy_default_url="http://127.0.0.1:17780"
    cc_openai_proxy_explicit=0
    cc_openai_proxy_managed=0
    unset CC_OPENAI_PROXY_EFFECTIVE_URL CC_OPENAI_AVAILABLE

    if [ "''${CC_OPENAI_PROXY_URL+x}" = x ]; then
      cc_openai_proxy_explicit=1
      cc_openai_proxy_base_url="$CC_OPENAI_PROXY_URL"
      cc_openai_proxy_token="''${CC_OPENAI_PROXY_AUTH_TOKEN:-}"
      if ! cc_openai_proxy_is_nonblank "$cc_openai_proxy_base_url"; then
        unset CC_OPENAI_PROXY_AUTH_TOKEN
        cc_openai_proxy_note "CC_OPENAI_PROXY_URL must not be blank"
        return 1
      fi
      if ! cc_openai_proxy_is_nonblank "$cc_openai_proxy_token"; then
        unset CC_OPENAI_PROXY_AUTH_TOKEN
        cc_openai_proxy_note \
          "CC_OPENAI_PROXY_URL requires CC_OPENAI_PROXY_AUTH_TOKEN"
        return 1
      fi
    else
      cc_openai_proxy_managed=1
      cc_openai_proxy_base_url="$cc_openai_proxy_default_url"
      unset CC_OPENAI_PROXY_AUTH_TOKEN
      cc_openai_proxy_auth_file="$(cc_openai_proxy_default_auth_file)" || return 1
      cc_openai_proxy_token="$(${cc-openai-proxy}/bin/cc-openai-proxy-auth \
        --auth-token-file "$cc_openai_proxy_auth_file")" || {
        cc_openai_proxy_note "the local proxy bearer is unavailable"
        return 1
      }

      if ! cc_openai_proxy_is_running; then
        if [ "''${CC_OPENAI_PROXY_AUTOSTART:-1}" != "1" ]; then
          cc_openai_proxy_note "the managed proxy is not running"
          return 1
        fi
        if ! cc_openai_proxy_manage start; then
          cc_openai_proxy_note "the managed proxy could not be started"
          return 1
        fi
        if ! cc_openai_proxy_wait_for_managed; then
          cc_openai_proxy_note "the managed proxy did not become ready"
          return 1
        fi
      fi
    fi

    if ! cc_openai_proxy_probe; then
      if [ "$cc_openai_proxy_managed" = 1 ] && \
        [ "$cc_openai_proxy_probe_result" = unauthorized ] && \
        [ "''${CC_OPENAI_PROXY_AUTOSTART:-1}" = 1 ]; then
        if cc_openai_proxy_manage restart && cc_openai_proxy_wait_for_managed; then
          cc_openai_proxy_probe || true
        else
          cc_openai_proxy_probe_result=transport
        fi
      fi
    fi

    if [ "$cc_openai_proxy_probe_result" != ok ]; then
      unset CC_OPENAI_PROXY_AUTH_TOKEN CC_OPENAI_PROXY_EFFECTIVE_URL CC_OPENAI_AVAILABLE
      cc_openai_proxy_probe_note
      return 1
    fi

    export CC_OPENAI_PROXY_AUTH_TOKEN="$cc_openai_proxy_token"
    export CC_OPENAI_AVAILABLE=1
    export CC_OPENAI_PROXY_EFFECTIVE_URL="$cc_openai_proxy_base_url"
  }
''
