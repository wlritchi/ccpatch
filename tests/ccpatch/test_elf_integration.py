"""Integration: parse/rebuild the Bun blob of a real installed Claude binary.

Skipped unless a binary is found (``CCPATCH_TEST_BINARY`` or the Nix profile).
Does not execute the Claude binary itself: it validates that the
container + blob round-trips structurally on real data, and that the injected
compact_session tool is runtime shape-complete against the real cli.js (the
latter node-evals the extracted tool object, and skips without node/bun).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ccpatch.bunfmt import parse_blob, rebuild_blob
from ccpatch.container import load_container
from ccpatch.module_runtime import finalize_module_runtime
from ccpatch.patches import (
    _PROVIDER_ENV_EXPLICIT_KEYS,
    BACKGROUND_PROVIDER_ENV,
    BACKGROUND_PROVIDER_ENV_198,
    COMPACT_SESSION,
    MULTI_PROVIDER_SDK,
    PatchError,
    background_provider_environment,
    default_patch_sets,
)


def test_unified_claude_uses_proxy_aware_launcher() -> None:
    root = Path(__file__).parents[2]
    launcher = (root / "nix/claude-code.nix").read_text()

    assert "cc_openai_proxy_configure optional claude" in launcher
    assert "CC_OPENAI_PROXY_URL+x" in launcher
    assert "exit 1" in launcher


def _find_binary() -> tuple[Path | None, bool]:
    env = os.environ.get("CCPATCH_TEST_BINARY")
    if env and Path(env).is_file():
        return Path(env), True

    launcher = Path.home() / ".nix-profile/bin/claude"
    if launcher.is_file():
        binary = launcher.resolve().parent.parent / "libexec/claude-code/claude"
        if binary.is_file():
            return binary, False

    return None, False


@pytest.fixture(scope="module")
def binary_info() -> tuple[bytes, bool]:
    path, explicit = _find_binary()
    if path is None:
        pytest.skip("no Claude Bun binary found (set CCPATCH_TEST_BINARY)")
    assert path is not None  # narrow type: pytest.skip above does not return
    return path.read_bytes(), explicit


@pytest.fixture(scope="module")
def binary_bytes(binary_info: tuple[bytes, bool]) -> bytes:
    return binary_info[0]


def test_real_blob_round_trips(binary_bytes: bytes) -> None:
    container = load_container(binary_bytes)
    blob = parse_blob(container.read_blob())
    assert parse_blob(rebuild_blob(blob)) == blob


def test_real_binary_has_entrypoint_source(binary_bytes: bytes) -> None:
    blob = parse_blob(load_container(binary_bytes).read_blob())
    entry = [m for m in blob.modules if m.is_entrypoint()]
    assert len(entry) == 1
    assert len(entry[0].contents) > 1_000
    assert sum(len(module.contents) for module in blob.modules) > 1_000_000


def test_write_blob_is_loadable_again(binary_bytes: bytes) -> None:
    container = load_container(binary_bytes)
    blob = parse_blob(container.read_blob())
    new_file = container.write_blob(rebuild_blob(blob))
    # The rewritten file must re-parse as a valid container + blob.
    blob2 = parse_blob(load_container(new_file).read_blob())
    assert blob2 == blob


_ID = r"[A-Za-z_$][\w$]*"


def _match(pattern: str, source: str) -> re.Match[str]:
    matches = list(re.finditer(pattern, source))
    assert len(matches) == 1, (
        f"expected one semantic match for {pattern!r}, got {len(matches)}"
    )
    return matches[0]


def _function_at(source: str, position: int) -> tuple[str, str]:
    declarations = list(
        re.finditer(rf"(?:async )?function ({_ID})\(", source[:position])
    )
    assert declarations, "no function declaration before semantic anchor"
    declaration = declarations[-1]
    following = re.search(rf"(?:async )?function {_ID}\(", source[position:])
    assert following is not None, "no function declaration after semantic anchor"
    return declaration[1], source[declaration.start() : position + following.start()]


def _same_function(source: str, patched: str, anchor: str) -> tuple[str, str]:
    match = _match(anchor, source)
    name, original = _function_at(source, match.end())
    declarations = list(
        re.finditer(rf"(?:async )?function {re.escape(name)}\(", patched)
    )
    if len(declarations) > 1:
        original_start = source.rfind(original, 0, match.end() + len(original))
        assert original_start >= 0
        context = source[max(0, original_start - 100) : original_start]
        declarations = [
            declaration
            for declaration in declarations
            if patched[: declaration.start()].endswith(context)
        ]
    assert len(declarations) == 1, f"ambiguous function declaration for {name}"
    _, replacement = _function_at(patched, declarations[0].end())
    return original, replacement


@pytest.mark.parametrize("name", ["$", "$worker", "worker$", "renamedWorker"])
def test_semantic_function_discovery_accepts_dollar_names(name: str) -> None:
    source = f'function {name}(){{return "semantic anchor"}}function next(){{}}'
    patched = source.replace('return "semantic anchor"', 'return "patched anchor"')
    original, replacement = _same_function(source, patched, "semantic anchor")
    assert original == f'function {name}(){{return "semantic anchor"}}'
    assert replacement == f'function {name}(){{return "patched anchor"}}'


def test_semantic_function_discovery_disambiguates_reused_names() -> None:
    source = (
        'function worker(){return "other"}function boundary(){}'
        'function worker(){return "semantic anchor"}function next(){}'
    )
    patched = source.replace('return "semantic anchor"', 'return "patched anchor"')
    original, replacement = _same_function(source, patched, "semantic anchor")
    assert original == 'function worker(){return "semantic anchor"}'
    assert replacement == 'function worker(){return "patched anchor"}'


def _provider_sources(binary_info: tuple[bytes, bool]) -> tuple[str, str]:
    binary_bytes, explicit = binary_info
    source = _entry_source(binary_bytes)
    if not re.search(
        r'VERSION:"2\.1\.(?:174|175|176|177|178|179|181|182|183|185|186|187|190|191|193|195|196|197|198|199|200|201|202|203|204|205|206|207|208|209|210|211)"',
        source,
    ):
        if explicit:
            pytest.fail(
                "CCPATCH_TEST_BINARY must be pristine Claude Code 2.1.174-2.1.211"
            )
        pytest.skip("installed binary is not pristine Claude Code 2.1.174-2.1.211")
    if re.search(rf"providerEnvVersion:\d+,providerEnv:{_ID}\(\)", source):
        if explicit:
            pytest.fail(
                "CCPATCH_TEST_BINARY must be unpatched Claude Code 2.1.174-2.1.211"
            )
        pytest.skip("installed Claude Code 2.1.174-2.1.211 binary is already patched")
    patch_set = (
        BACKGROUND_PROVIDER_ENV_198
        if re.search(
            r'VERSION:"2\.1\.(?:198|199|200|201|202|203|204|205|206|207|208|209|210|211)"',
            source,
        )
        else BACKGROUND_PROVIDER_ENV
    )
    if match := re.search(r'VERSION:"2\.1\.(207|208|209|210|211)"', source):
        patch_set = background_provider_environment((2, 1, int(match[1])))
        patch_set = replace(
            patch_set,
            patches=tuple(
                patch
                for patch in patch_set.patches
                if patch.name != "carry-provider-env-to-agents-fallback"
            ),
        )
    return source, patch_set.apply(source)


def test_198_207_all_patch_sets_preserve_remote_control_choice(
    binary_info: tuple[bytes, bool],
) -> None:
    source = _entry_source(binary_info[0])
    version_match = re.search(
        r'VERSION:"2\.1\.(198|199|200|201|202|203|204|205|206|207|208|209|210|211)"',
        source,
    )
    if version_match is None:
        pytest.skip("requires Claude Code 2.1.198-2.1.207")
    version = (2, 1, int(version_match[1]))
    patch_sets = default_patch_sets(version)
    assert len(patch_sets) == 9
    assert all(patch_set.applies_to(version) for patch_set in patch_sets)
    patched = source
    for patch_set in patch_sets:
        patched = patch_set.apply(patched)

    choice = _match(
        rf'function (?P<choice>{_ID})\(\)\{{return (?P<settings>{_ID})\(\)'
        rf'\?\.settings\.remoteControlAtStartup\?\?'
        rf'(?P<config>{_ID})\(\)\.remoteControlAtStartup\}}',
        source,
    )
    default = _match(
        rf'function (?P<default>{_ID})\(\)\{{let (?P<value>{_ID})='
        rf'{re.escape(choice["choice"])}\(\);if\((?P=value)!==void 0\)'
        rf'return (?P=value);return[^{{}}]+\.getCcrAutoConnectDefault\(\)\}}',
        source,
    )
    assert choice[0] in patched
    assert default[0] in patched
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    script = (
        'let setting,config;'
        f'function {choice["settings"]}(){{return {{settings:{{remoteControlAtStartup:setting}}}}}}'
        f'function {choice["config"]}(){{return {{remoteControlAtStartup:config}}}}'
        + choice[0]
        + default[0]
        + 'for(setting of [false,true])for(config of [false,true,undefined])'
        + f'if({default["default"]}()!==setting)throw Error("RC choice overridden");'
        + 'setting=undefined;for(config of [false,true])'
        + f'if({default["default"]}()!==config)throw Error("RC config overridden");'
    )
    result = subprocess.run(  # noqa: S603 - Execute only the extracted choice functions.
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_181_preserves_cloud_branch_and_token_count_context(
    binary_info: tuple[bytes, bool],
) -> None:
    source = _entry_source(binary_info[0])
    if not re.search(
        r'VERSION:"2\.1\.(?:181|182|183|185|186|187|190|191|193|195|196|197|198|199|200|201|202|203|204|205|206|207|208|209|210|211)"',
        source,
    ):
        pytest.skip("requires Claude Code 2.1.181-2.1.207")
    patched = source
    version_match = re.search(r'VERSION:"2\.1\.(\d+)"', source)
    assert version_match is not None
    version = (2, 1, int(version_match[1]))
    for patch_set in default_patch_sets(version):
        patched = patch_set.apply(patched)
    branch = _match(
        r'if\([\w$]+!==null\)\{let [\w$]+=await [\w$]+\(\);'
        r'if\(![\w$]+\.valid\)[\s\S]{0,2500}?'
        r'await [\w$]+\(0\);return\}if\([\w$]+==="stream-json"',
        source,
    )[0]
    assert branch in patched
    contexts = re.findall(r'source:"count_tokens",agentContext:[\w$]+\(\)', source)
    assert len(contexts) == 2
    for context in set(contexts):
        assert patched.count(context) == source.count(context)


def test_patched_binary_help_initializes_on_opt_in_host() -> None:
    configured = os.environ.get("CCPATCH_TEST_PATCHED_BINARY")
    if configured is None:
        pytest.skip("set CCPATCH_TEST_PATCHED_BINARY for the host-side help test")
    path = Path(configured)
    if not path.is_file():
        pytest.fail("CCPATCH_TEST_PATCHED_BINARY must name a patched binary")
    blob = parse_blob(load_container(path.read_bytes()).read_blob())
    source = b"\n".join(module.contents for module in blob.modules)
    version_match = re.search(rb'VERSION:"2\.1\.(\d+)"', source)
    if (
        version_match is None
        or not 174 <= int(version_match[1])
        or b"providerEnvVersion:3" not in source
    ):
        pytest.fail(
            "CCPATCH_TEST_PATCHED_BINARY must be fully patched Claude Code 2.1.174 or newer"
        )

    try:
        proc = subprocess.run(  # noqa: S603 - explicit opt-in test binary
            [str(path), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "DISABLE_AUTOUPDATER": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"patched binary --help timed out: {exc}")
    except OSError as exc:
        if exc.errno in {8, 13}:
            pytest.skip(f"patched binary loader is unsupported on this host: {exc}")
        raise

    assert proc.returncode == 0, proc.stderr
    assert "Usage:" in proc.stdout


def test_real_source_secures_background_provider_environment(
    binary_info: tuple[bytes, bool],
) -> None:
    source, patched = _provider_sources(binary_info)
    vertex_region_keys = set(re.findall(r"VERTEX_REGION_CLAUDE_[A-Z0-9_]+", source))
    supported_region_keys = {
        key for key in _PROVIDER_ENV_EXPLICIT_KEYS if key.startswith("VERTEX_REGION_")
    }
    if not re.search(
        r'VERSION:"2\.1\.(?:197|198|199|200|201|202|203|204|205|206|207|208|209|210|211)"',
        source,
    ):
        supported_region_keys.remove("VERTEX_REGION_CLAUDE_5_SONNET")
    assert vertex_region_keys == supported_region_keys
    assert '"CLAUDE_CODE_CERT_STORE"' in patched
    snapshot = _match(
        rf"function ({_ID})\(\)\{{let {_ID}=\{{\}};for\(let {_ID} of {_ID}\)"
        rf"\{{let {_ID}=process\.env\[{_ID}\];if\({_ID}===void 0\)continue;"
        rf'if\({_ID}===""&&{_ID}!=="CLAUDE_SECURESTORAGE_CONFIG_DIR"\)',
        source,
    )[1]
    assert patched.count(f"providerEnvVersion:3,providerEnv:{snapshot}()") == 2
    assert "providerEnvVersion:3,short:" in patched
    assert patched.count(".providerEnvVersion!==3") == 3
    assert patched.count('code==="EPROVIDERENV"') == 2
    assert 'throw Object.assign(Error(' in patched
    finalized = finalize_module_runtime(patched)
    for key in _PROVIDER_ENV_EXPLICIT_KEYS:
        assert f'"{key}"' in finalized
    patched = finalized.replace("globalThis.__ccpatchRuntime.provider.", "")
    for pattern in (
        rf"{_ID}\({_ID},{_ID},{_ID}\.socketAuth\(\),{_ID}\.claimAuth\)",
        rf"{_ID}\.buildClaimFrame\({_ID},{_ID},{_ID}\)",
        rf"return await {_ID}\(100\),{_ID}\({_ID},{_ID}\+1,{_ID}\)",
    ):
        call = _match(pattern, source)[0]
        assert call[:-1] + ",_ccProviderEnv)" in patched
    manager = _match(rf"({_ID})=async\({_ID},{_ID}=0,{_ID}\)=>\{{", source)
    manager_prefix = manager[0].replace(")=>{", ",_ccProviderEnv)=>{")
    manager_start = patched.index(manager_prefix)
    assert patched[manager_start:].startswith(
        manager_prefix + "_ccProviderEnv=_ccProviderRetain(_ccProviderEnv);"
    )
    for pattern in (
        rf"{re.escape(manager[1])}\({_ID}\.dispatch,0,!0\)",
        rf"{_ID}\(\({_ID}\)=>void {re.escape(manager[1])}\({_ID}\)",
        rf"dispatch:\({_ID}\)=>void {re.escape(manager[1])}\({_ID}\)",
    ):
        assert _match(pattern, source)[0] in patched
    assert "for(let _ccKey of _ccProviderKeys())delete process.env[_ccKey]" in patched
    schema = _match(
        rf'({_ID})\.object\(\{{proto:{_ID},op:\1\.literal\("dispatch"\)', source
    )[1]
    assert f"providerEnv:{schema}.record({schema}.enum(_ccProviderKeys())" in patched
    for pattern in (
        rf"{_ID}\?\.providerEnv\?\?{re.escape(snapshot)}\(\)",
        rf"providerEnv:{re.escape(snapshot)}\(\),sessionPermissionRules",
        rf"providerEnv:{_ID}\?\.providerEnv",
        rf"providerEnv:{_ID}\.providerEnv",
        (
            rf'{_ID}\.providerEnv\?\?\{{\}}'
            if re.search(
                r'VERSION:"2\.1\.(?:198|199|200|201|202|203|204|205|206|207|208|209|210|211)"',
                source,
            )
            else rf"\.\.\.{_ID}\.providerEnv&&\{{providerEnv:{_ID}\.providerEnv\}}"
        ),
    ):
        assert re.search(pattern, source) is not None
        assert re.search(pattern, patched) is None
    fallback = _match(
        rf"let {_ID}={_ID}\(\{{\.\.\.{_ID},nonce:{_ID}\}}\);await {_ID}\({_ID},{_ID},384\)",
        source,
    )[0]
    assert fallback in patched
    assert "Restart the stale Claude Code daemon and try again" in patched

    original_builder, builder = _same_function(
        source,
        patched,
        rf'\.\.\.{_ID}\.env,CLAUDE_CODE_SESSION_KIND:"bg",CLAUDE_BG_BACKEND:"daemon"',
    )
    payload = "let _ccProviderPayload=_ccProviderRetain(_ccProviderEnv)"
    initial_apply = "Object.entries(_ccProviderPayload)"
    native_scrubs = re.findall(
        rf"for\(let {_ID} of {_ID}\)|{_ID}\.some", original_builder
    )
    assert len(native_scrubs) >= 3
    final_apply = "Object.entries(_ccProviderPayload)"
    assert builder.index(payload) < builder.index(initial_apply)
    for native_scrub in native_scrubs:
        assert builder.index(initial_apply) < builder.index(native_scrub)
        assert builder.index(native_scrub) < builder.rindex(final_apply)
    env = _match(rf"return ({_ID})\}}", original_builder)[1]
    assert builder.rindex(final_apply) < builder.index(f"return {env}}}")
    assert "_ccProviderSnapshotFromEnv" not in builder
    assert (
        f"{env}.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT=JSON.stringify(_ccProviderPayload)"
        in builder
    )

    capture = "_ccProviderWorkerEnv=_ccProviderCaptureTransport("
    _, claimed = _same_function(
        source,
        patched,
        r'"spare_claim"\);',
    )
    assign = claimed.index("Object.assign(process.env,", claimed.index(capture))
    if re.search(r'VERSION:"2\.1\.(207|208|209|210|211)"', source):
        assert (
            claimed.index(capture)
            < claimed.index("_ccProviderInitialized=false;")
            < assign
        )
        assert "_ccProviderApplyWorkerFinal()" not in claimed
        assert patched.count("_ccProviderInitialize(") >= 3
        assert "_ccProviderValidateManaged(_ccPolicy,\"policySettings\")" in patched
        assert "_ccProviderSnapshotFromEnv" not in patched
        return
    final_apply = claimed.index("_ccProviderApplyWorkerFinal()", assign)
    assert claimed.index(capture) < assign < final_apply
    assert final_apply < claimed.index("await ", final_apply)
    assert "_ccProviderSnapshotFromEnv" not in patched

    native_delayed, delayed = _same_function(
        source, patched, "Waiting for remote managed settings before telemetry init"
    )
    telemetry = _match(rf"function ({_ID})\(", native_delayed)[1]
    initializers = _match(
        rf'Remote managed settings loaded, initializing telemetry"\),({_ID})\(\)'
        rf'(?:,|;let\[[^;]+;if\([^;]+;)await ({_ID})\(\)',
        native_delayed,
    )
    assert (
        delayed.index(f"{initializers[1]}()")
        < delayed.index("_ccProviderApplyWorkerFinal()")
        < delayed.index(f"await {initializers[2]}()")
    )
    operational_start = patched.index(
        f"{initializers[1]}(),{telemetry}(_ccProviderWorkerEnv)"
    )
    operational = patched[operational_start : operational_start + 250]
    assert operational.index(f"{telemetry}(_ccProviderWorkerEnv)") < operational.index(
        "_ccProviderApplyWorkerFinal()"
    )


def test_178_preserves_upstream_security_and_compaction_fallback(
    binary_info: tuple[bytes, bool],
) -> None:
    source = _entry_source(binary_info[0])
    if not re.search(
        r'VERSION:"2\.1\.(?:178|179|181|182|183|185|186|187|190|191|193|195|196|197|198|199|200|201|202|203|204|205|206|207|208|209|210|211)"',
        source,
    ):
        pytest.skip("requires Claude Code 2.1.178-2.1.207")
    patched = source
    version_match = re.search(r'VERSION:"2\.1\.(\d+)"', source)
    assert version_match is not None
    version = (2, 1, int(version_match[1]))
    for patch_set in default_patch_sets(version):
        patched = patch_set.apply(patched)

    for anchor in (
        "Auto mode classifier transcript too long, falling back",
        rf'function {_ID}\({_ID}\)\{{let {_ID}=new Set,{_ID}=new Set,{_ID}=new Set,{_ID}=!1;for\(let {_ID} of {_ID}\?\?\[\]\)',
        "Tool use is not allowed during compaction",
        "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR must be a valid",
    ):
        original, replacement = _same_function(source, patched, anchor)
        assert replacement == original

    for label in ("compact", "reactive-compact"):
        fallback = _match(
            rf'forkLabel:"{label}",maxTurns:1,fallbackModel:{_ID}\('
            rf'{_ID}\.(?:toolUseContext\.)?options\.mainLoopModel,'
            rf'{_ID}\.(?:toolUseContext\.)?options\.fallbackModel\),'
            rf'(?:maxOutputTokens:Math\.min\({_ID},{_ID}\('
            rf'{_ID}\.(?:toolUseContext\.)?options\.mainLoopModel\)\),)?'
            rf'skip(?:Transcript|CacheWrite):!0,skip(?:Transcript|CacheWrite):!0',
            source,
        )[0]
        assert fallback in patched
    retry_chain = _match(
        rf'let {_ID}={_ID}\({_ID},{_ID}\.options\.fallbackModel\),'
        rf'{_ID}=\[{_ID},\.\.\.{_ID}\.filter\(\({_ID}\)=>{_ID}!=={_ID}\)\],{_ID}=0;while\(!0\)',
        source,
    )[0]
    assert retry_chain in patched

    native_builder, builder = _same_function(
        source,
        patched,
        rf'\.\.\.{_ID}\.env,CLAUDE_CODE_SESSION_KIND:"bg",CLAUDE_BG_BACKEND:"daemon"',
    )
    scrub_pattern = (
        rf'else if\(({_ID})\.ANTHROPIC_BASE_URL!==({_ID})\.ANTHROPIC_BASE_URL\)'
        rf'\{{for\(let ({_ID}) of {_ID}\)delete \1\[\3\];'
        rf'if\(\2\.ANTHROPIC_BASE_URL\)delete \1\.ANTHROPIC_AUTH_TOKEN\}}'
        if version >= (2, 1, 203)
        else rf'else if\({_ID}\.ANTHROPIC_BASE_URL\)delete {_ID}\.ANTHROPIC_AUTH_TOKEN;'
    )
    scrub = _match(scrub_pattern, native_builder)[0]
    assert scrub in builder
    assert builder.index(scrub) < builder.rindex("Object.entries(_ccProviderPayload)")
    assert "_ccProviderSnapshotFromEnv" not in patched


def test_179_preserves_clientdata_and_partial_stream_recovery(
    binary_info: tuple[bytes, bool],
) -> None:
    source = _entry_source(binary_info[0])
    if 'VERSION:"2.1.179"' not in source:
        pytest.skip("requires Claude Code 2.1.179")
    patched = source
    for patch_set in default_patch_sets((2, 1, 179)):
        patched = patch_set.apply(patched)
    native, replacement = _same_function(
        source,
        patched,
        rf'return {_ID}==="env"\|\|{_ID}==="settings"\|\|{_ID}==="clientdata"',
    )
    assert replacement.startswith(native[:-1] + "||(")
    recovery = _match(
        rf'if\({_ID}&&{_ID}\)\{{let {_ID}={_ID}\.some\([^\n]+?'
        rf'tengu_streaming_partial_finalized[^\n]+?break {_ID}\}}',
        source,
    )[0]
    assert recovery in patched


def test_real_source_routes_multi_provider_sdk(
    binary_info: tuple[bytes, bool],
) -> None:
    source, provider_patched = _provider_sources(binary_info)
    patched = MULTI_PROVIDER_SDK.apply(provider_patched)

    sdk = _match(
        rf"let ({_ID})=\{{apiKey:.{{0,500}}?\}};return new ({_ID})\(\1\)\}}async function",
        source,
    )[2]
    assert f"const _ccMultiProviderSDK=()=>{sdk}" in patched
    assert patched.count("_ccMultiProviderRoute(") == 5
    assert patched.count("let _ccRequest=") >= 2
    assert "_ccMultiProviderCatalog.find" in patched
    for model in (
        "moonshot:kimi-k3",
        "moonshot:kimi-k2.7-code",
        "zai:glm-5.3",
        "zai:glm-5.3-flash",
        "zai:glm-5.2",
        "zai:glm-5-turbo",
        "zai:glm-4.7",
        "zai:glm-4.5-air",
        "minimax:MiniMax-M3",
        "minimax:MiniMax-M2.7",
        "openai:gpt-6-astra",
        "openai:gpt-6-sol",
        "openai:gpt-6-luna",
        "openai:gpt-5.6-sol",
        "openai:gpt-5.6-terra",
        "openai:gpt-5.6-luna",
    ):
        assert model in patched
    assert "CC_KIMI_AUTH_TOKEN" in patched
    assert "CC_ZAI_AUTH_TOKEN" in patched
    assert "CC_MINIMAX_AUTH_TOKEN" in patched
    assert "CC_OPENAI_PROXY_AUTH_TOKEN" in patched
    assert "CC_OPENAI_AVAILABLE" in patched
    assert '"baseURL":"http://127.0.0.1:17780"' in patched
    assert '"tokenEnv":"CC_OPENAI_PROXY_AUTH_TOKEN"' in patched
    assert '"availabilityEnv":"CC_OPENAI_AVAILABLE"' in patched
    assert "cc-openai-local" not in patched
    assert "apiKey:null,authToken:_ccToken,maxRetries:0" in patched
    assert "defaultHeaders:{..._ccInfo.definition.defaultHeaders}" in patched
    count_client = _match(
        rf'let ({_ID})=await {_ID}\(\{{maxRetries:1,model:{_ID},source:"count_tokens"',
        source,
    )[1]
    assert f"_ccMultiProviderRoute({count_client},_ccRequest,{{}},!0)" in patched
    assert "_ccClient.beta.messages.countTokens(_ccOutbound)" in patched
    native_tokens, patched_tokens = _same_function(
        source, patched, "`countTokens API call failed:"
    )
    native_prefix = native_tokens[: native_tokens.index("async()=>{try{")]
    assert patched_tokens.startswith(native_prefix)
    native_guard = re.search(
        rf'if\(typeof ({_ID})\.input_tokens!=="number"\)return null;', native_tokens
    )
    if native_guard:
        assert (
            f"_ccMultiProviderInputTokens(_ccEffectiveModel,{native_guard[1]});"
            + native_guard[0]
        ) in patched_tokens
    assert '_ccMultiProviderDeniedRequestFields=["fallback_credit_token"]' in patched
    assert "_ccMultiProviderTraceHeaders.includes(_ccName.toLowerCase())" in patched
    assert "function _ccMultiProviderModelProvider(" in patched
    assert "function _ccMultiProviderCatalogInfo(" in patched
    assert "function _ccMultiProviderAttribution(" in patched
    assert '"attributionDomain":"moonshot.ai"' in patched
    assert '"attributionDomain":"z.ai"' in patched
    assert '"attributionDomain":"minimax.io"' in patched
    assert '"attributionDomain":"openai.com"' in patched
    native_attribution, attribution = _same_function(
        source, patched, r"Co-Authored-By: \$\{[^}]+\} <noreply@anthropic\.com>"
    )
    attribution_vars = _match(
        rf"let ({_ID})={_ID}\(\),({_ID})=[^;]+,({_ID})=`Co-Authored-By:",
        native_attribution,
    )
    assert (
        f"_ccMultiProviderAttribution({attribution_vars[1]},_ccNativeAttributionLabel)"
        in attribution
    )
    assert (
        f"{attribution_vars[3]}=`Co-Authored-By: ${{{attribution_vars[2]}}} <noreply@${{_ccAttributionDomain}}>`"
        in attribution
    )
    assert "noreply@anthropic.com" not in attribution
    assert (
        _match(
            rf'if\({_ID}\.includeCoAuthoredBy===!1\)return\{{commit:"",pr:""\}}',
            native_attribution,
        )[0]
        in attribution
    )
    native_context, context_resolver = _same_function(
        source,
        patched,
        rf'function {_ID}\({_ID},{_ID}\)\{{let ({_ID})={_ID}\(\);if\(\1!==void 0\)return \1;if\({_ID}\({_ID},{_ID}\)\)return {_ID};return {_ID}\({_ID},{_ID}\)\}}',
    )
    override = _match(rf"if\(({_ID})!==void 0\)return \1;", native_context)[0]
    native_fallback = native_context[native_context.index(override) + len(override) :]
    assert context_resolver.index(override) < context_resolver.index(
        "_ccProviderModel.contextWindow"
    )
    assert context_resolver.index(
        "_ccProviderModel.contextWindow"
    ) < context_resolver.index(native_fallback)
    native_output, output_resolver = _same_function(
        source, patched, rf'return\{{default:{_ID},upperLimit:{_ID}\}}'
    )
    limits = _match(rf'return\{{default:({_ID}),upperLimit:({_ID})\}}', native_output)
    assert (
        f"{limits[2]}=_ccProviderModel.maxOutputTokens,{limits[1]}=Math.min({limits[1]},{limits[2]})"
        in output_resolver
    )
    assert (
        f"{limits[1]}={limits[2]}=_ccProviderModel.maxOutputTokens"
        not in output_resolver
    )
    if re.search(
        r'VERSION:"2\.1\.(?:199|200|201|202|203|204|205|206|207|208|209|210|211)"',
        source,
    ):
        native_limits = _match(
            rf'{_ID}={_ID}\({_ID}\)\?\.max_output_tokens;'
            rf'if\(({_ID})\){_ID}=\1\.default,{_ID}=\1\.upper;',
            native_output,
        )[0]
        assert native_limits in output_resolver
        native_override = _match(
            rf'let ({_ID})={_ID}\({_ID}\);if\(\1!==null\)'
            rf'{_ID}=Math\.min\(\1,{_ID}\);',
            native_output,
        )[0]
        assert native_override in output_resolver
    else:
        assert (
            _match(rf'if\({_ID}==="claude-fable-5"', native_output)[0]
            in output_resolver
        )
    assert (
        _match(
            rf'if\(({_ID})\?\.max_tokens&&\1\.max_tokens>=4096\)[^;]+;', native_output
        )[0]
        in output_resolver
    )
    native_compact, compact_source = _same_function(
        source,
        patched,
        rf'return (?:(?:({_ID})==="env"\|\|\1==="settings"\|\|'
        rf'(?:\1==="clientdata"\|\|)?\1==="model-default")|'
        rf'{_ID}\({_ID},{_ID}\)\.source!=="auto")',
    )
    model = _match(rf'function {_ID}\(({_ID}),{_ID}\)', native_compact)[1]
    assert f'==="auto"&&_ccMultiProviderCatalogInfo({model})!==null' in compact_source
    if '.source!=="auto"' in native_compact:
        assert 'return _ccSource!=="auto"||' in compact_source
    thinking = _match(
        rf'function {_ID}\({_ID},{_ID}\)\{{return {_ID}\({_ID},\(({_ID})\)=>\1\.message\.model!=={_ID}&&\1\.message\.model!=={_ID}\)\}}',
        source,
    )
    assert (
        patched.count(
            f"_ccMultiProviderModelProvider({thinking[1]}.message.model)!==_ccProvider"
        )
        == 1
    )
    assert thinking[0] not in patched
    tools = _match(
        rf'({_ID})\.map\(\(({_ID})\)=>({_ID})\(\2,\{{getToolPermissionContext:{_ID}\.getToolPermissionContext,tools:{_ID},agents:{_ID}\.agents,allowedAgentTypes:{_ID}\.allowedAgentTypes,model:({_ID}),deferLoading:',
        source,
    )
    assert (
        f"{tools[1]}.filter(({tools[2]})=>_ccMultiProviderToolAllowed({tools[4]},{tools[2]})).map(({tools[2]})=>{tools[3]}"
        in patched
    )
    assert '_ccTool.isMcp===!0||_ccTool.name!=="WebSearch"' in patched
    assert '"moonshot:kimi-k3":{inputTokens:3,outputTokens:15' in patched
    assert '"zai:glm-5.3-flash":{inputTokens:0.15,outputTokens:0.5' in patched
    assert '"minimax:minimax-m3":{inputTokens:0.3,outputTokens:1.2' in patched
    assert '"kimi-k3":{inputTokens:' not in patched
    _, verification = _same_function(source, patched, 'source:"verify_api_key"')
    assert "_ccMultiProviderRoute" not in verification
    assert 'source:"verify_api_key"' in verification


def test_provider_resume_and_agent_catalogue_runtime(
    binary_info: tuple[bytes, bool],
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    pristine, source = _provider_sources(binary_info)
    patched = MULTI_PROVIDER_SDK.apply(source)
    native_resume, resume = _same_function(pristine, patched, r'\?"unknown_family":')
    native_resolver, resolver = _same_function(
        pristine,
        patched,
        rf'function {_ID}\({_ID},{_ID},{_ID},{_ID}(?:,{_ID})?\)\{{let {_ID}=\(\)=>{_ID}\(\{{permissionMode:',
    )
    resume_name = _match(rf"function ({_ID})\(", native_resume)[1]
    resolver_name = _match(rf"function ({_ID})\(", native_resolver)[1]
    schema_match = _match(
        rf'model:{_ID}\.enum\((.+?)\)\.optional\(\)\.describe\(["`]Optional model override for this agent\.',
        patched,
    )
    schema = schema_match[1]
    catalogue_start = patched.index("const _ccMultiProviderCatalog=")
    catalogue = patched[catalogue_start : patched.index(";", catalogue_start) + 1]
    stubs: dict[str, str] = {}

    def stub(pattern: str, body: str, context: str = native_resume) -> str:
        name = _match(pattern, context)[1]
        stubs[name] = body
        return name

    native_models = stub(rf"new Set\(({_ID})\.map", '["claude-opus-4-8"]')
    stub(rf"\.has\(({_ID})\({_ID}\)\)", "(m)=>m")
    stub(rf"\.message\.model===({_ID})\)continue", '"synthetic"')
    allowlist = _match(
        rf'"unknown_family":!(?:({_ID})\({_ID}\)&&!)?({_ID})\({_ID}\)\?"not_allowed"',
        native_resume,
    )
    if allowlist[1]:
        stubs[allowlist[1]] = "()=>false"
    stubs[allowlist[2]] = "()=>allowed"
    stub(rf'"not_allowed":({_ID})\(', "()=>false")
    stub(rf"\.message\.model,{_ID}=({_ID})\(\)", '()=>"opus"')
    stub(rf";if\(({_ID})\({_ID}\)&&!", "()=>false")
    stub(rf"&&!({_ID})\({_ID}\)&&", "()=>false")
    stub(rf"&&({_ID})\({_ID},{_ID}\({_ID}\)\)\)return", "()=>false")
    if re.search(rf"\|\|{_ID}!==void 0", native_resume):
        stub(rf"if\(\({_ID}&&({_ID})\({_ID}\)\|\|", "()=>false")
        stub(rf"\)\)&&({_ID})\({_ID}\)&&", "()=>false")
    else:
        for pattern in (
            rf"if\({_ID}&&({_ID})\({_ID}\)&&",
            rf"&&({_ID})\({_ID}\)&&{_ID}\({_ID}\({_ID}\(",
        ):
            stub(pattern, "()=>false")
    nested = _match(rf"&&{_ID}\(({_ID})\(({_ID})\({_ID}\)\)\)", native_resume)
    stubs[nested[1]] = stubs[nested[2]] = "(m)=>m"
    stub(
        rf"\(\)=>({_ID})\(\{{permissionMode:", "({mainLoopModel:m})=>m", native_resolver
    )
    stub(rf"let {_ID}=({_ID})\({_ID}\),{_ID}=\(", "()=>null", native_resolver)
    stub(rf'&&({_ID})\({_ID}\)==="bedrock"', '()=>"firstParty"', native_resolver)
    stub(rf"let {_ID}={_ID}\?\?({_ID})\(\)", '()=>"inherit"', native_resolver)
    inherited = re.findall(rf"if\(({_ID})\({_ID},{_ID}\)\)return", native_resolver)
    assert len(set(inherited)) == 1
    stubs[inherited[0]] = "()=>false"
    normalization = re.findall(
        rf"let {_ID}={_ID}\(({_ID})\(({_ID})\({_ID}\)\),{_ID}\)", native_resolver
    )
    assert len(set(normalization)) == 1
    for name in normalization[0]:
        stubs[name] = "(m)=>m"
    aliases = stub(
        rf'(?<![\w$])({_ID})=\["sonnet","opus","haiku","fable","best",',
        '["sonnet","opus","haiku","fable","best"]',
        pristine,
    )
    native_catalogues = set(re.findall(rf"({_ID})\(\)\.opus48", pristine))
    assert len(native_catalogues) == 1
    stubs[native_catalogues.pop()] = '()=>({opus48:"provider-native-opus"})'
    picker, _ = _function_at(
        pristine, pristine.index("model options: dropping duplicate row")
    )
    stubs[picker] = '()=>[{value:null},{value:"custom-model"}]'
    if re.search(
        r'VERSION:"2\.1\.(?:175|176|177|178|179|181|182|183|185|186|187|190|191|193|195|196|197|198|199|200|201|202|203|204|205|206|207|208|209|210|211)"',
        pristine,
    ):
        denied = _match(
            rf'if\(!({_ID})\({_ID}\)\)return {_ID}\({_ID}\);', native_resolver
        )
        stubs[denied[1]] = "()=>allowed"
    bindings = (
        ";".join(
            f"globalThis[{json.dumps(name)}]={value}" for name, value in stubs.items()
        )
        + ";"
    )
    script = (
        'const assert=require("node:assert/strict");'
        + catalogue
        + _function_at(
            patched,
            patched.index("function _ccMultiProviderCanonicalModel(")
            + len("function _ccMultiProviderCanonicalModel("),
        )[1]
        + "let allowed=true;"
        + bindings
        + resume
        + resolver
        + f"const models={schema},resumeModel={resume_name},resolveModel={resolver_name};"
        + f"const aliases={aliases},nativeModels={native_models};"
        'const message=(model)=>({type:"assistant",message:{model}});'
        'delete process.env.CLAUDE_CODE_SUBAGENT_MODEL;'
        'for(const entry of _ccMultiProviderCatalog){'
        'for(const model of [entry.value,entry.value.split(":")[1]])'
        'assert.deepEqual(resumeModel([message(model)]),{kind:"ok",model:entry.value});'
        'assert(models.includes(entry.value));'
        'assert.equal(resolveModel(undefined,"claude-opus-4-8",entry.value),entry.value);}'
        'for(const model of [...aliases,...nativeModels,"provider-native-opus","custom-model"])'
        'assert(models.includes(model));'
        'assert(!models.includes("openai:not-a-model"));'
        'assert(!models.includes("gpt-6-astra"));'
        'assert.deepEqual(resumeModel([message("claude-opus-4-8")]),'
        '{kind:"ok",model:"claude-opus-4-8"});'
        'assert.equal(resumeModel([message("unknown")]).reason,"unknown_family");'
        'assert.equal(resumeModel([message("synthetic")]).kind,"none");'
        'assert.equal(resumeModel([message("gpt-6-astra"),{...message("unknown"),isMeta:true}]).model,'
        '"openai:gpt-6-astra");'
        'allowed=false;assert.deepEqual(resumeModel([message("gpt-6-astra")]),'
        '{kind:"declined",model:"openai:gpt-6-astra",reason:"not_allowed"});'
        'allowed=true;_ccMultiProviderCatalog.push({value:"other:gpt-6-astra"});'
        'assert.equal(resumeModel([message("gpt-6-astra")]).reason,"unknown_family");'
        'assert.equal(resumeModel([message("openai:gpt-6-astra")]).model,"openai:gpt-6-astra");'
    )
    result = subprocess.run(  # noqa: S603 - local runtime regression
        [node, "-e", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


# --- runtime shape-completeness of the injected compact_session tool ----------
#
# The static catalog test (test_compact_session_tool_shape_complete) pins a member
# list. This integration test instead derives the required members MECHANICALLY from
# the real cli.js and node-evals the actually-injected tool object, so it self-updates
# when a future CC build direct-calls a new method on tools -- the exact failure mode
# that shipped three runtime bugs (missing prompt/renderToolUseMessage, wrong result
# shape) past the static apply()+verify checks.

# Members the aK tool constructor / base object provide by default; the injected tool
# need not define these.
_AK_DEFAULT_MEMBERS = frozenset(
    {
        "isEnabled",
        "isConcurrencySafe",
        "isReadOnly",
        "isDestructive",
        "checkPermissions",
        "toAutoClassifierInput",
        "userFacingName",
    }
)
# The invocation-contract methods the harness calls on every tool but that are too
# noisy to derive mechanically (`.call(` alone is Function.prototype.call, 1000+ hits).
# The volatile render/dispatch layer IS derived, from the unambiguous `.tool.M(`.
_PROTOCOL_CORE_MEMBERS = frozenset(
    {
        "description",
        "inputSchema",
        "prompt",
        "call",
        "mapToolResultToToolResultBlockParam",
    }
)

_EVAL_TEMPLATE = """\
globalThis.%(builder)s=(o)=>Object.defineProperties(\
{isEnabled:()=>!0,isConcurrencySafe:()=>!1,isReadOnly:()=>!1,isDestructive:()=>!1,\
checkPermissions:()=>({}),toAutoClassifierInput:()=>"",userFacingName:()=>""},\
Object.getOwnPropertyDescriptors(o));
globalThis.%(ns)s={object:()=>({}),strictObject:()=>({})};
%(tool)s;
const t=globalThis.__ccCompactTool,req=%(req)s;
console.log(JSON.stringify(req.filter((m)=>t[m]===undefined)));
"""


def _entry_source(binary: bytes) -> str:
    blob = parse_blob(load_container(binary).read_blob())
    entry = next(m for m in blob.modules if m.is_entrypoint())
    data = entry.contents
    if isinstance(data, (bytes, bytearray)):
        return data.decode("utf-8", "surrogatepass")
    return data


def _required_tool_members(source: str) -> list[str]:
    # Methods the generic renderer/dispatcher calls UNCONDITIONALLY on a tool object,
    # scraped from `X.tool.M(` (dropping optional-chained `.tool.M?.(`), unioned with
    # the pinned invocation core, minus what the constructor already supplies.
    accessed = set(re.findall(r"(?<!\?)\.tool\.([A-Za-z_$][\w$]*)\(", source))
    accessed -= {m for m in accessed if f".tool.{m}?.(" in source}
    return sorted((accessed | _PROTOCOL_CORE_MEMBERS) - _AK_DEFAULT_MEMBERS)


def _slice_injected_tool(patched: str) -> str:
    # The injected `globalThis.__ccCompactTool=<builder>({...})` assignment, sliced by
    # a balanced-paren scan (the tool object contains no unbalanced parens in strings).
    start = patched.index("globalThis.__ccCompactTool=")
    depth = 0
    for i in range(patched.index("(", start), len(patched)):
        if patched[i] == "(":
            depth += 1
        elif patched[i] == ")":
            depth -= 1
            if depth == 0:
                return patched[start : i + 1]
    raise AssertionError("unbalanced injected compact_session tool object")


def test_compact_session_tool_runtime_shape_complete(binary_bytes: bytes) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to eval the injected tool object")
    source = _entry_source(binary_bytes)
    try:
        patched = COMPACT_SESSION.apply(source)
    except PatchError as exc:
        pytest.skip(f"compact_session patch does not apply to this binary: {exc}")
    required = _required_tool_members(patched)
    tool = _slice_injected_tool(patched)
    m_builder = re.search(r"globalThis\.__ccCompactTool=([\w$]+)\(", tool)
    m_ns = re.search(r"inputSchema\(\)\{return ([\w$]+)\.object", tool)
    assert m_builder is not None and m_ns is not None
    js = _EVAL_TEMPLATE % {
        "builder": m_builder.group(1),
        "ns": m_ns.group(1),
        "tool": tool,
        "req": json.dumps(required),
    }
    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, "-e", js], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"{runtime} eval failed: {proc.stderr.strip()}"
    missing = json.loads(proc.stdout.strip())
    assert not missing, (
        f"injected compact_session tool missing harness-called members: {missing}"
    )
