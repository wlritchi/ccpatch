"""Check the native agents fallback without launching a CLI or daemon."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.agents_handoff import agents_view_handoff
from ccpatch.patches import (
    BACKGROUND_PROVIDER_ENV,
    Patch,
    background_provider_environment,
)

# This gate and fallback are extracted from the 2.1.203 UXp function.
_NATIVE_GATE = (
    'if(Ze("tengu_bg_leftarrow_inprocess",!0))'
    'try{return await OXp(m,u,{dispatchDefaults:I})}catch(R){xe(R)}'
    'return $Oe({args:["agents",...Jtn(I)],'
    'env:{CLAUDE_AGENTS_SELECT:m,...zYe()}})'
)
_HELPERS = (
    'const selection=["CLAUDE_CODE_USE_BEDROCK","CLAUDE_CODE_USE_VERTEX"],'
    'urls=["ANTHROPIC_BASE_URL","ANTHROPIC_VERTEX_BASE_URL"],'
    'credentials=["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","AWS_BEARER_TOKEN_BEDROCK"],'
    'skip=["CLAUDE_CODE_SKIP_BEDROCK_AUTH","CLAUDE_CODE_SKIP_VERTEX_AUTH"],'
    'models=["ANTHROPIC_MODEL","ANTHROPIC_SMALL_FAST_MODEL"],'
    'custom=["ANTHROPIC_CUSTOM_MODEL_OPTION","ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"],'
    'recognized=new Set(),allowlist=[];'
    'function php(){let result={};for(let key of allowlist){let value=process.env[key];'
    'if(value===void 0)continue;'
    'if(value===""&&key!=="CLAUDE_SECURESTORAGE_CONFIG_DIR")continue;'
    'result[key]=value}return result}'
)


def _fallback_patch(version: tuple[int, int, int] = (2, 1, 203)) -> Patch:
    return next(
        patch
        for patch in background_provider_environment(version).patches
        if patch.name == "carry-provider-env-to-agents-fallback"
    )


@pytest.mark.parametrize("mode", ["disabled", "success", "throws"])
@pytest.mark.parametrize("channels", [False, True])
@pytest.mark.parametrize("selection", ["legacy", "unset", "explicit", "lifecycle"])
def test_native_fallback_preserves_provider_snapshot(
    mode: str, channels: bool, selection: str
) -> None:
    snapshot_patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV.patches
        if patch.name == "snapshot-transient-provider-env"
    )
    helpers, count = snapshot_patch.pattern.subn(snapshot_patch.replacement, _HELPERS)
    assert count == 1
    gate = _NATIVE_GATE
    if selection != "legacy":
        gate = gate.replace(
            "dispatchDefaults:I",
            "dispatchDefaults:I,...a?.autoOpenJobId!==void 0&&{autoOpenJobId:a.autoOpenJobId}",
        ).replace("CLAUDE_AGENTS_SELECT:m", "CLAUDE_AGENTS_SELECT:a?.autoOpenJobId??m")
    if selection == "lifecycle":
        gate = gate.replace(
            "autoOpenJobId:a.autoOpenJobId}})",
            "autoOpenJobId:a.autoOpenJobId},originSpawn:u,storageV5:u,credentials:u,fleetNudgeStore:a?.fleetNudgeStore})",
        ).replace("return $Oe(", "let result=await $Oe(")
        gate += ';cleaned++;return result'
    if channels:
        handoff = next(
            patch
            for patch in agents_view_handoff((2, 1, 216)).patches
            if patch.name == "agents-launch-config"
        )
        gate, count = handoff.pattern.subn(handoff.replacement, gate)
        assert count == 1
    patch = _fallback_patch()
    source, count = patch.pattern.subn(
        patch.replacement, helpers + "async function run(){" + gate + "}"
    )
    assert count == 1
    assert source.count("CLAUDE_CODE_PROVIDER_ENV_TRANSIENT:JSON.stringify(php())") == 1
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    script = (
        'const process={env:{ANTHROPIC_API_KEY:"live",ANTHROPIC_AUTH_TOKEN:"",'
        'ANTHROPIC_BASE_URL:"https://live.invalid",apiKeyHelper:"untouched"}};'
        + source
        + f'const mode="{mode}",channels={json.dumps(channels)},lifecycle={json.dumps(selection == "lifecycle")};'
        + f'const a={json.dumps({"autoOpenJobId": "selected"} if selection == "explicit" else {})};'
        + '''
const m="job",u={},I={model:"current"};
let spawned=0,inprocess=0,logged=0,snapshots=0,cleaned=0;
const originalSnapshot=php;
php=()=>{snapshots++;return originalSnapshot()};
const before=JSON.stringify(process.env);
function Ze(){return mode!=="disabled"}
function _ccAgentsDispatchArgs(){return ["--dangerously-load-development-channels","server:test"]}
async function OXp(job,context,options){
    inprocess++;
    if(job!==m||context!==u||options.dispatchDefaults!==I)throw Error("dispatch changed");
    if(options.autoOpenJobId!==a.autoOpenJobId)throw Error("selection changed");
    if(lifecycle&&(options.originSpawn!==u||options.storageV5!==u||options.credentials!==u))throw Error("lifecycle lost");
    if(channels&&JSON.stringify(options.dispatchExtraArgs)!==JSON.stringify(_ccAgentsDispatchArgs()))throw Error("config lost");
    if(mode==="throws")throw Error("render failed");
    return "inprocess";
}
function xe(){logged++}
function Jtn(defaults){if(defaults!==I)throw Error("defaults changed");return ["--model","current"]}
function zYe(){return {ACCESSIBILITY:"retained"}}
function $Oe(options){spawned++;return options}
(async()=>{
    const result=await run();
    if(JSON.stringify(process.env)!==before)throw Error("mutated requester");
    if(mode==="success"){
        if(result!=="inprocess"||spawned||snapshots||inprocess!==1)throw Error("inprocess changed");
        return;
    }
    if(spawned!==1||snapshots!==1||logged!==(mode==="throws"?1:0))throw Error("fallback changed");
    if(cleaned!==(lifecycle?1:0))throw Error("cleanup changed");
    if(result.env.CLAUDE_AGENTS_SELECT!==(a.autoOpenJobId??m)||result.env.ACCESSIBILITY!=="retained")throw Error("env lost");
    if(JSON.stringify(result.args)!==JSON.stringify(["agents","--model","current",...(channels?_ccAgentsDispatchArgs():[])]))throw Error("argv changed");
    const child={...process.env,...result.env};
    const captured=_ccProviderCaptureTransport(child);
    if("CLAUDE_CODE_PROVIDER_ENV_TRANSIENT" in child)throw Error("transport leaked");
    if(captured.ANTHROPIC_API_KEY!=="live"||captured.ANTHROPIC_AUTH_TOKEN!==""
        ||captured.ANTHROPIC_BASE_URL!=="https://live.invalid"
        ||captured.ANTHROPIC_MODEL!==null)throw Error("incorrect transport");
})().catch(error=>{console.error(error);globalThis.process.exitCode=1});
'''
    )
    result = subprocess.run(  # noqa: S603 - runtime is a resolved node/bun executable
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "source",
    [
        _NATIVE_GATE.replace("Jtn(I)", "Jtn(other)"),
        _NATIVE_GATE.replace("CLAUDE_AGENTS_SELECT:m", "CLAUDE_AGENTS_SELECT:other"),
        _NATIVE_GATE.replace("xe(R)", "xe(other)"),
        _NATIVE_GATE.replace("tengu_bg_leftarrow_inprocess", "other_gate"),
    ],
)
def test_fallback_rejects_unrelated_dispatch(source: str) -> None:
    assert _fallback_patch().pattern.search(source) is None


@pytest.mark.parametrize(
    "version",
    [
        (2, 1, 182),
        (2, 1, 193),
        (2, 1, 195),
        (2, 1, 203),
        (2, 1, 207),
        (2, 1, 208),
        (2, 1, 209),
        (2, 1, 210),
        (2, 1, 211),
    ],
)
def test_fallback_version_boundary(version: tuple[int, int, int]) -> None:
    names = {patch.name for patch in background_provider_environment(version).patches}
    assert ("carry-provider-env-to-agents-fallback" in names) == (
        version >= (2, 1, 195)
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {
            "ANTHROPIC_API_KEY": "requester",
            "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_MODEL": None,
        },
    ],
)
@pytest.mark.parametrize("selected", [False, True])
def test_extracted_native_agents_settings_path(
    payload: dict[str, str | None] | None, selected: bool
) -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "build/sweep-resume/2.1.203/linux-x64/original.js"
    )
    if not path.exists():
        pytest.skip("No cached native 2.1.203 source")
    native = path.read_text()
    safe = native.split("function Ztt(){", 1)[1].split("function GTg", 1)[0]
    full = native.split("function cY(){", 1)[1].split("var D0t", 1)[0]
    telemetry = native.split("function Mpr(){", 1)[1].split("async function Wca", 1)[0]
    refresh = native.split("async function UXi(){", 1)[1].split(
        "async function VXi", 1
    )[0]
    callback = re.search(
        r'\(\[\{setupGracefulShutdown:ie\}.*?return ne\("tengu_fleetview".*?le\(\)\}',
        native,
    )
    assert callback is not None
    source = _HELPERS + "function Ztt(){" + safe + "function cY(){" + full
    source += "function Mpr(){" + telemetry + "async function UXi(){" + refresh
    source += "const deferred=" + callback.group(0) + ";"
    names = {
        "snapshot-transient-provider-env",
        "retain-agents-provider-settings-authority",
        "restore-agents-provider-after-settings-writes",
    }
    for patch in background_provider_environment((2, 1, 203)).patches:
        if patch.name in names:
            source, count = patch.pattern.subn(patch.replacement, source)
            assert count in patch.expected_matches, patch.name
    env: dict[str, str] = {}
    if payload is not None:
        env["CLAUDE_CODE_PROVIDER_ENV_TRANSIENT"] = json.dumps(payload)
    if selected:
        env["CLAUDE_AGENTS_SELECT"] = "job"
    script = (
        "const process={env:"
        + json.dumps(env)
        + "};"
        + source
        + '''
const events=[];
const settings={ANTHROPIC_API_KEY:"settings",ANTHROPIC_AUTH_TOKEN:"settings",ANTHROPIC_MODEL:"settings-model",
 NODE_EXTRA_CA_CERTS:"ca",CLAUDE_CODE_CLIENT_CERT:"cert",CLAUDE_CODE_CLIENT_KEY:"key",SECURITY:"native"};
const T\\u0065=process.env;
let jYt={},h3r,D0t={hostOrchestrated:false},jTg=["userSettings","policySettings"],j2t=new Set();
function xIu(){events.push("host-security")}
function _t(){return {env:settings}}
function UYt(env){return env}
function Cr(){return {env:settings}}
function iC(){return ["userSettings","policySettings"]}
function ag(){return true}
function she(){return true}
function j5n(){events.push("snapshot");check()}
function Twr(){events.push("mtls-cache");check()}
function zZo(){return {certPath:"cert",keyPath:"key"}}
function SAn(){events.push("mtls-config");check()}
function VKe(){events.push("proxy-cache");check()}
function xFe(){events.push("agents-config");check()}
async function uUt(){events.push("ca-load");check();return true}
async function fUt(){events.push("mtls-load");check();return true}
function xe(error){throw error}
function w(){}
function oe(error){return String(error)}
function GXi(){return true}
function Cn(){return false}
function RL(){return false}
async function P0t(){await Promise.resolve()}
async function Wca(){events.push("telemetry");check()}
function u5e(){}
function rn(){}
let trusted=true;
const FXi={applyConfigEnvironmentVariables:cY,applySafeConfigEnvironmentVariables:Ztt};
const ase={checkHasTrustDialogAccepted:()=>trusted};
function ib(){}
function tKe(){}
function nQ(){}
const zKe={clearProxyCache:VKe,configureGlobalAgents:xFe};
const qQa={clearCACertsCache:()=>events.push("ca-cache"),loadExtraCACerts:uUt};
const QQa={clearMTLSCache:()=>events.push("mtls-cache"),loadMTLSClientMaterial:fUt};
const S=true,B=false;
function check(){
 const next=php();
 const expected=EXPECTED;
 for(const key of ["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","ANTHROPIC_MODEL"]){
  if(next[key]!==expected[key])throw Error(key+" next snapshot: "+JSON.stringify(next));
 }
 if(process.env.SECURITY!=="native"||process.env.NODE_EXTRA_CA_CERTS!=="ca"
 ||process.env.CLAUDE_CODE_CLIENT_CERT!=="cert"||process.env.CLAUDE_CODE_CLIENT_KEY!=="key")throw Error("security env lost");
 if("CLAUDE_CODE_PROVIDER_ENV_TRANSIENT" in process.env)throw Error("transport leaked");
}
(async()=>{
 Ztt();
 deferred([
  {setupGracefulShutdown:()=>events.push("shutdown")},
  {initializeErrorLogSink:()=>events.push("errors")},
  {initializeAnalyticsSink:()=>events.push("analytics")},
  {initialize1PEventLogging:()=>events.push("logging")},
  {logEvent:()=>events.push("fleetview")},
  {captureTeammateModeSnapshotIfEnabled:()=>events.push("teammate")},
  {initializeGrowthBook:async()=>events.push("growth")},
  {initializeTelemetryAfterTrust:Mpr},
  {checkHasTrustDialogAccepted:()=>true},
  FXi
 ]);
 await new Promise(resolve=>setImmediate(resolve));
 check();
 await UXi();
 trusted=false;
 await UXi();
 await new Promise(resolve=>setImmediate(resolve));
 check();
 for(const event of ["host-security","shutdown","errors","analytics","logging","fleetview","teammate","growth","telemetry","mtls-config","proxy-cache","agents-config","ca-load","mtls-load","ca-cache"])
  if(!events.includes(event))throw Error("missing native step "+event);
})().catch(error=>{console.error(error);globalThis.process.exitCode=1});
'''
    )
    keys = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"]
    expected = (
        {key: payload.get(key) for key in keys}
        if selected and payload is not None
        else dict(zip(keys, ["settings", "settings", "settings-model"], strict=True))
    )
    script = script.replace("EXPECTED", json.dumps(expected))
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    result = subprocess.run(  # noqa: S603 - runtime is a resolved node/bun executable
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_cached_native_fallback_cardinality() -> None:
    root = Path(__file__).resolve().parents[2] / "build" / "sweep-resume"
    paths = sorted(
        path
        for path in root.glob("2.*/*/original.js")
        if tuple(int(part) for part in path.parent.parent.name.split("."))
        <= (2, 1, 211)
    )
    if not paths:
        pytest.skip("No cached native release sources")
    for path in paths:
        major, minor, patch = (int(part) for part in path.parent.parent.name.split("."))
        version = (major, minor, patch)
        source = path.read_text()
        matches = list(_fallback_patch().pattern.finditer(source))
        assert len(matches) == int(version >= (2, 1, 195)), path
        if version >= (2, 1, 195):
            settings_name = (
                "initialize-provider-before-native-settings"
                if version >= (2, 1, 207)
                else "restore-agents-provider-after-settings-writes"
            )
            settings_patch = next(
                patch
                for patch in background_provider_environment(version).patches
                if patch.name == settings_name
            )
            settings_matches = list(settings_patch.pattern.finditer(source))
            assert len(settings_matches) == 2, path
            exports = re.search(
                r"applySafeConfigEnvironmentVariables:\(\)=>([\w$]+),"
                r"applyConfigEnvironmentVariables:\(\)=>([\w$]+)",
                source,
            )
            assert exports is not None, path
            assert all(
                match.group(0).startswith(f"function {name}()")
                for match, name in zip(settings_matches, exports.groups(), strict=True)
            ), path
            handoff = next(
                patch
                for patch in agents_view_handoff(version).patches
                if patch.name == "agents-launch-config"
            )
            forwarded, count = handoff.pattern.subn(handoff.replacement, source)
            assert count == 1, path
            fallback = _fallback_patch(version)
            assert len(list(fallback.pattern.finditer(forwarded))) == 1, path
            snapshot = next(
                patch
                for patch in background_provider_environment(version).patches
                if patch.name == "snapshot-transient-provider-env"
            )
            forwarded, count = snapshot.pattern.subn(snapshot.replacement, forwarded)
            assert count == 1, path
            patched, count = fallback.pattern.subn(fallback.replacement, forwarded)
            assert count == 1, path
            assert patched != forwarded
            assert fallback.pattern.search(patched) is None, path
