"""Execute the provider bootstrap with real ESM dependency ordering."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.module_runtime import (
    ensure_module_reference,
    finalize_module_runtime,
    module_reference,
    register_module_bootstrap,
    source_modules,
)
from ccpatch.patches import (
    _provider_binding_owner,
    _provider_dispatch_padding,
    _provider_ensure_reference,
    _provider_reference,
    _ProviderPatchSet,
    background_provider_environment,
)


@pytest.fixture(
    scope="module",
    params=[None, 272, 273, 274],
    ids=["synthetic", "native-272", "native-273", "native-274"],
)
def provider_bootstrap(request: pytest.FixtureRequest) -> str | None:
    if request.param is None:
        return None
    path = (
        Path(__file__).resolve().parents[2]
        / f"build/sweep-resume/2.1.{request.param}/linux-x64/original.js"
    )
    if not path.is_file():
        pytest.skip(f"requires captured pristine .{request.param} linux-x64 source")
    source = background_provider_environment((2, 1, request.param)).apply(
        path.read_text()
    )
    registrations = [
        json.loads(bytes.fromhex(match[1]))
        for match in re.finditer(r"/\* ccpatch-bootstrap:([0-9a-f ]+) \*/", source)
    ]
    matches = [code for key, code in registrations if key == "provider"]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("pty", [False, True])
def test_provider_bootstrap_capture_claim_and_policy(
    tmp_path: Path, pty: bool, provider_bootstrap: str | None
) -> None:
    native = (
        'const selection=["CLAUDE_CODE_USE_BEDROCK","CLAUDE_CODE_USE_VERTEX"],'
        'urls=["ANTHROPIC_BASE_URL","ANTHROPIC_VERTEX_BASE_URL"],'
        'credentials=["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN"],'
        'skip=["CLAUDE_CODE_SKIP_BEDROCK_AUTH","CLAUDE_CODE_SKIP_VERTEX_AUTH"],'
        'models=["ANTHROPIC_MODEL","ANTHROPIC_SMALL_FAST_MODEL"],'
        'custom=["ANTHROPIC_CUSTOM_MODEL_OPTION","ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"],'
        'allowlist=new Set([...selection,...credentials]);'
        'function snapshot(){let result={};for(let key of allowlist){let value=process.env[key];'
        'if(value===void 0)continue;'
        'if(value===""&&key!=="CLAUDE_SECURESTORAGE_CONFIG_DIR")continue;'
        'result[key]=value}return result}'
    )
    base = background_provider_environment((2, 1, 212))
    subset = _ProviderPatchSet(
        name=base.name,
        patches=tuple(
            patch
            for patch in base.patches
            if patch.name
            in {
                "snapshot-transient-provider-env",
                "preserve-provider-transport-in-pty-host",
                "install-provider-native-policy-boundary",
            }
        ),
    )
    settings = (
        'import "./cycle.mjs";'
        'const s=globalThis.__ccpatchRuntime.provider;'
        's._ccProviderInitialize({});'
        'if(process.env.ANTHROPIC_API_KEY!=="daemon")throw Error("preclaim restore");'
        'globalThis.providerState=s;'
    )
    checks = (
        'const s=globalThis.__ccpatchRuntime.provider;'
        'if(s!==globalThis.providerState)throw Error("singleton reset");'
        'for(const payload of [null,[],{UNTRUSTED_KEY:"value"},{ANTHROPIC_API_KEY:42}]){'
        'let rejected=false;try{s._ccProviderRetain(payload)}catch(e){rejected=e.code==="EPROVIDERENV"}'
        'if(!rejected)throw Error("invalid retained snapshot accepted");}'
        'const retained=s._ccProviderRetain({ANTHROPIC_API_KEY:"retained",ANTHROPIC_AUTH_TOKEN:null});'
        'if(!Object.isFrozen(retained)||retained.ANTHROPIC_AUTH_TOKEN!==null)throw Error("retained snapshot changed");'
        'if(s._ccProviderPtyHost){'
        'if(!process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT)throw Error("PTY transport lost");'
        '}else{'
        'if(process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT)throw Error("transport leaked");'
        'if(!Object.isFrozen(s._ccProviderWorkerEnv))throw Error("mutable snapshot");'
        's._ccProviderWorkerEnv=s._ccProviderCaptureTransport({'
        'CLAUDE_CODE_PROVIDER_ENV_TRANSIENT:JSON.stringify({ANTHROPIC_API_KEY:"claimed",ANTHROPIC_AUTH_TOKEN:null,CLAUDE_CODE_OAUTH_TOKEN_12:"claimed-account"})});'
        's._ccProviderAwaitingClaim=false;s._ccProviderInitialized=false;'
        's._ccProviderInitialize({});'
        'if(process.env.ANTHROPIC_API_KEY!=="claimed"||process.env.ANTHROPIC_AUTH_TOKEN)throw Error("claim restore");'
        'if(process.env.CLAUDE_CODE_OAUTH_TOKEN_12!=="claimed-account"||process.env.CLAUDE_CODE_OAUTH_TOKEN_3)throw Error("numbered account restore");'
        'const numbered=s._ccProviderFilterSettings({CLAUDE_CODE_OAUTH_TOKEN_99:"local"},"localSettings");'
        'if(numbered.CLAUDE_CODE_OAUTH_TOKEN_99)throw Error("numbered settings leak");'
        'let conflict=false;try{s._ccProviderValidateManaged({CLAUDE_CODE_OAUTH_TOKEN_99:"managed"},"policySettings")}catch(e){conflict=e.code==="EPROVIDERENV"}'
        'if(!conflict)throw Error("numbered policy conflict accepted");'
        'const filtered=s._ccProviderFilterSettings({ANTHROPIC_API_KEY:"local",SECURITY:"native"},"localSettings");'
        'if(filtered.ANTHROPIC_API_KEY||filtered.SECURITY!=="native")throw Error("settings filter");'
        'let rejected=false;try{s._ccProviderValidateManaged({ANTHROPIC_API_KEY:"managed"},"policySettings")}catch(e){rejected=e.code==="EPROVIDERENV"}'
        'if(!rejected)throw Error("managed conflict accepted");'
        'if(s._ccProviderWorkerEnv.ANTHROPIC_API_KEY!=="claimed")throw Error("claim state reset");'
        '}'
    )
    modules = {
        "entry.mjs": 'import "./settings.mjs";import "./snapshot.mjs";' + checks,
        "settings.mjs": settings,
        "cycle.mjs": 'import "./settings.mjs";',
        "snapshot.mjs": native,
    }
    source = "".join(
        f"\n/* ccpatch-module:{name.encode().hex()} */\n{body}"
        for name, body in modules.items()
    )
    source = finalize_module_runtime(
        subset.apply(source)
        if provider_bootstrap is None
        else register_module_bootstrap(source, "provider", provider_bootstrap)
    )
    for module in source_modules(source):
        (tmp_path / module.name).write_text(module.source)
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required")
    env = {
        **os.environ,
        "ANTHROPIC_API_KEY": "daemon",
        "ANTHROPIC_AUTH_TOKEN": "daemon-token",
        "CLAUDE_CODE_OAUTH_TOKEN_3": "stale-daemon-account",
        "CLAUDE_CODE_PROVIDER_ENV_TRANSIENT": '{"ANTHROPIC_API_KEY":"requester"}',
    }
    subprocess.run(  # noqa: S603 - Execute the local ESM fixture.
        [node, str(tmp_path / "entry.mjs"), "--bg-pty-host" if pty else "--bg-spare"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_manager_retains_native_probe_and_storage_arguments() -> None:
    assert _provider_dispatch_padding("z=async(p,A=0,K,probe=!1)=>{") == ",!1"
    assert (
        _provider_dispatch_padding("z=async(p,A=0,K,probe=!1,_ccProviderEnv)=>{")
        == ",!1"
    )
    assert _provider_dispatch_padding("z=async(p,A=0,K)=>{") == ""
    patches = {
        patch.name: patch
        for patch in background_provider_environment((2, 1, 272)).patches
    }
    cases = {
        "accept-transient-provider-env-in-manager": (
            "z=async(p,A=0,K,probe=!1)=>{",
            "z=async(p,A=0,K,probe=!1,_ccProviderEnv)=>{",
        ),
        "retain-provider-env-while-worker-settles": (
            "return await Q(100),z(p,A+1,K,de)",
            "return await Q(100),z(p,A+1,K,de,_ccProviderEnv)",
        ),
        "pass-provider-env-to-cold-worker": (
            "Worker.spawn(p,d,e.getAuthSnapshot,K?{afterUpgrade:K}:void 0,e.storageV5,e.credentials)",
            "Worker.spawn(p,d,e.getAuthSnapshot,K?{afterUpgrade:K}:void 0,e.storageV5,e.credentials,_ccProviderEnv)",
        ),
        "pass-provider-env-to-claimed-spare": (
            "let C=claim(p,s,d,e.getAuthSnapshot,e.storageV5,e.credentials)",
            "let C=claim(p,s,d,e.getAuthSnapshot,e.storageV5,e.credentials,_ccProviderEnv)",
        ),
    }
    for name, (source, expected) in cases.items():
        patch = patches[name]
        output, count = patch.pattern.subn(patch.replacement, source)
        assert count == 1
        assert output.startswith(expected)


def test_rv_protocol_follows_native_import_owner() -> None:
    modules = {
        "protocol.mjs": "const protocol=1;export{protocol};",
        "supervisor.mjs": 'import{protocol as tl}from"protocol.mjs";const frame={proto:tl,role:"supervisor"};',
        "rv.mjs": 'import{protocol as other}from"protocol.mjs";const tl="wrong";const handler=true;',
    }
    source = "".join(
        f"\n/* ccpatch-module:{name.encode().hex()} */\n{body}"
        for name, body in modules.items()
    )
    owner, binding = _provider_binding_owner(source, source.index("proto:tl"), "tl")
    source, resolved = ensure_module_reference(
        source, owner, binding, source.index("handler")
    )
    assert resolved == "other"
    assert module_reference(source, owner, binding, source.index("handler")) == "other"


def test_rv_binding_crosses_existing_native_dependency_path() -> None:
    modules = {
        "protocol.mjs": "const protocol=1;const unused=0;export{protocol,unused};",
        "bridge.mjs": 'import{unused}from"protocol.mjs";const bridge=unused;export{bridge};',
        "rv.mjs": 'import{bridge}from"bridge.mjs";const protocol="wrong";const handler=bridge;',
    }
    source = "".join(
        f"\n/* ccpatch-module:{name.encode().hex()} */\n{body}"
        for name, body in modules.items()
    )
    source = _provider_ensure_reference(
        source, source.index("const protocol"), "protocol", source.index("handler")
    )
    resolved = _provider_reference(
        source, source.index("const protocol"), "protocol", source.index("handler")
    )
    assert resolved.startswith("__ccpatchNativeBinding")
    assert source.count('from"protocol.mjs"') == 1
    assert source.count('from"bridge.mjs"') == 1


def test_provider_matches_module_handoff_fallback() -> None:
    patch = next(
        p
        for p in background_provider_environment((2, 1, 272)).patches
        if p.name == "carry-provider-env-to-agents-fallback"
    )
    source = (
        'if(gate("tengu_bg_leftarrow_inprocess",!0))try{return await mount(job,context,'
        '{dispatchDefaults:defaults,dispatchExtraArgs:globalThis.__ccpatchRuntime.agentsHandoff.dispatchArgs()})}'
        'catch(error){log(error)}return spawn({args:["agents",...serialize(defaults),'
        '...globalThis.__ccpatchRuntime.agentsHandoff.dispatchArgs()],'
        'env:{CLAUDE_AGENTS_SELECT:job,...accessibility()}})'
    )
    assert patch.pattern.fullmatch(source) is not None
