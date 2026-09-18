"""Check provider state writes and delayed settings in 2.1.199-2.1.200."""

import shutil
import subprocess

import pytest

from ccpatch.patches import BACKGROUND_PROVIDER_ENV_198

_STATE_PREFIX = (
    'async function write(dir,state){let '
    'cron=state.inFlight?.kinds.includes("session_cron")===!0,'
    'normalized=cron&&!state.selfWake?{...state,selfWake:!0}:'
    '!cron&&state.selfWake&&canClear(state)?{...state,selfWake:void 0}:state,'
    '{pinned:p,sortOrder:s,stateSortOrder:i,group:g,...rest}=normalized;'
)


def test_199_state_write_removes_credentials_after_normalization() -> None:
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV_198.patches
        if patch.name == "remove-provider-env-from-state-writes"
    )
    patched, count = patch.pattern.subn(patch.replacement, _STATE_PREFIX)
    assert count == 1
    assert patched == _STATE_PREFIX.replace(
        "...rest}=normalized", "providerEnv:_ccProviderEnv,...rest}=normalized"
    )
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    script = (
        'const canClear=state=>state.clear;'
        + patched
        + 'return JSON.parse(JSON.stringify(rest))}'
        + '''
(async()=>{
    for(const cron of [false,true])
    for(const selfWake of [false,true])
    for(const clear of [false,true]){
        const state={inFlight:{kinds:cron?["session_cron"]:[]},selfWake,clear,
            providerEnv:{ANTHROPIC_API_KEY:"secret",ANTHROPIC_AUTH_TOKEN:"token",
                CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:"1"},
            pinned:true,sortOrder:1,stateSortOrder:2,group:"group",name:"kept"};
        const result=await write("unused",state);
        for(const key of ["providerEnv","pinned","sortOrder","stateSortOrder","group"])
            if(key in result)throw Error("persisted "+key);
        if(JSON.stringify(result).includes("secret"))throw Error("persisted credential");
        const expected=cron?true:selfWake&&clear?undefined:selfWake;
        if(result.selfWake!==expected||result.name!=="kept")throw Error("state changed");
        if(state.providerEnv.ANTHROPIC_AUTH_TOKEN!=="token")throw Error("input changed");
    }
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    )
    result = subprocess.run(  # noqa: S603 - runtime is a resolved node/bun executable
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "source",
    [
        _STATE_PREFIX.replace("...rest}=normalized", "...rest}=state"),
        _STATE_PREFIX.replace("group:g,", ""),
        _STATE_PREFIX.replace('"session_cron"', '"other_cron"'),
    ],
)
def test_199_state_write_rejects_unrecognized_layout(source: str) -> None:
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV_198.patches
        if patch.name == "remove-provider-env-from-state-writes"
    )
    assert patch.pattern.search(source) is None


_DELAYED_PREFIX = (
    'function telemetry(){log("Waiting for remote managed settings before telemetry init"),'
    'wait().then(async()=>{log("Remote managed settings loaded, initializing telemetry"),'
)
_DELAYED_ASYNC = (
    'settings();let[caChanged,mtlsChanged]=await Promise.all([loadCA(),loadMTLS()]);'
    'if(caChanged||mtlsChanged)clearProxy(),configureAgents();'
)
_PROVIDER_HELPERS = (
    'const selection=["CLAUDE_CODE_USE_BEDROCK","CLAUDE_CODE_USE_VERTEX"],'
    'urls=["ANTHROPIC_BASE_URL","ANTHROPIC_VERTEX_BASE_URL"],'
    'credentials=["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","AWS_BEARER_TOKEN_BEDROCK"],'
    'skip=["CLAUDE_CODE_SKIP_BEDROCK_AUTH","CLAUDE_CODE_SKIP_VERTEX_AUTH"],'
    'models=["ANTHROPIC_MODEL","ANTHROPIC_SMALL_FAST_MODEL"],'
    'custom=["ANTHROPIC_CUSTOM_MODEL_OPTION","ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"],'
    'recognized=new Set(),allowlist=[];'
    'function snapshot(){let result={};for(let key of allowlist){let value=process.env[key];'
    'if(value===void 0)continue;'
    'if(value===""&&key!=="CLAUDE_SECURESTORAGE_CONFIG_DIR")continue;'
    'result[key]=value}return result}'
)


@pytest.mark.parametrize("cloud_groups", [False, True], ids=["198-201", "202"])
def test_provider_snapshot_retains_cloud_credentials_and_tombstones(
    cloud_groups: bool,
) -> None:
    source = _PROVIDER_HELPERS
    if cloud_groups:
        source = source.replace(
            'recognized=new Set(),allowlist=[];',
            'cloudCredentials=["AWS_ACCESS_KEY_ID","AWS_SECRET_ACCESS_KEY",'
            '"AWS_SESSION_TOKEN"],cloudConfig=[...cloudCredentials,"AWS_PROFILE",'
            '"AWS_CONFIG_FILE","AWS_SHARED_CREDENTIALS_FILE",'
            '"GOOGLE_APPLICATION_CREDENTIALS","GOOGLE_CLOUD_PROJECT"];'
            'recognized=new Set(),allowlist=[];',
        )
        source = 'let recognized,allowlist;' + source
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV_198.patches
        if patch.name == "snapshot-transient-provider-env"
    )
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    script = (
        'const process={env:{AWS_ACCESS_KEY_ID:"requester",'
        'AWS_SECRET_ACCESS_KEY:"secret",AWS_SESSION_TOKEN:""}};'
        + patched
        + f'const cloudGroups={str(cloud_groups).lower()};'
        + '''
const payload=snapshot();
if(cloudGroups){
    if(payload.AWS_ACCESS_KEY_ID!=="requester"||payload.AWS_SECRET_ACCESS_KEY!=="secret"
        ||payload.AWS_SESSION_TOKEN!=="")throw Error("cloud credentials missing");
    const retained=_ccProviderRetain(payload);
    payload.AWS_ACCESS_KEY_ID="mutated";
    process.env={AWS_ACCESS_KEY_ID:"daemon",AWS_PROFILE:"stale"};
    _ccProviderApplyFinal(retained);
    if(process.env.AWS_ACCESS_KEY_ID!=="requester"||"AWS_PROFILE" in process.env)
        throw Error("snapshot or tombstone changed");
    process.env={};
    const empty=snapshot();
    for(const key of cloudCredentials)if(empty[key]!==null)throw Error("missing tombstone");
    process.env={AWS_ACCESS_KEY_ID:"daemon",AWS_SECRET_ACCESS_KEY:"daemon",
        AWS_SESSION_TOKEN:"daemon"};
    _ccProviderApplyFinal(empty);
    for(const key of cloudCredentials)if(key in process.env)throw Error("stale credential");
}else if("AWS_ACCESS_KEY_ID" in payload)throw Error("legacy registry changed");
'''
    )
    result = subprocess.run(  # noqa: S603 - runtime is a resolved node/bun executable
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("asynchronous", [False, True], ids=["199", "200"])
@pytest.mark.parametrize(
    "ca_changed,mtls_changed",
    [(False, False), (True, False), (False, True), (True, True)],
)
@pytest.mark.parametrize("worker", ["live", "foreground", "missing"])
def test_delayed_settings_preserves_order_and_live_provider(
    asynchronous: bool, ca_changed: bool, mtls_changed: bool, worker: str
) -> None:
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV_198.patches
        if patch.name == "restore-provider-env-after-delayed-settings"
    )
    snapshot_patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV_198.patches
        if patch.pattern.search(_PROVIDER_HELPERS)
    )
    helpers, count = snapshot_patch.pattern.subn(
        snapshot_patch.replacement, _PROVIDER_HELPERS
    )
    assert count == 1
    native = _DELAYED_ASYNC if asynchronous else "settings(),"
    source = _DELAYED_PREFIX + native + "await initialize()}).catch(caught)}"
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    separator = ";" if asynchronous else ","
    assert (
        native + "_ccProviderApplyWorkerFinal()" + separator + "await initialize()"
        in patched
    )
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    script = (
        'const assert=require("node:assert/strict");'
        'delete process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT;'
        'delete process.env.CLAUDE_CODE_SESSION_KIND;'
        + helpers
        + f'const asynchronous={str(asynchronous).lower()},caChanged={str(ca_changed).lower()},'
        + f'mtlsChanged={str(mtls_changed).lower()},worker="{worker}";'
        + '''
const events=[],caches={ca:null,mtls:null,proxy:0,agents:0};
let finish,resolveCA,resolveMTLS;
const done=new Promise(resolve=>{finish=resolve});
const caReady=new Promise(resolve=>{resolveCA=resolve});
const mtlsReady=new Promise(resolve=>{resolveMTLS=resolve});
const log=()=>{},wait=()=>Promise.resolve();
const live=Object.freeze({ANTHROPIC_BASE_URL:"https://live.invalid",
    ANTHROPIC_API_KEY:null,ANTHROPIC_AUTH_TOKEN:"live-token",
    CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:"1",CLAUDE_CODE_USE_BEDROCK:null});
function override(){
    Object.assign(process.env,{ANTHROPIC_BASE_URL:"https://settings.invalid",
        ANTHROPIC_API_KEY:"settings-key",ANTHROPIC_AUTH_TOKEN:"settings-token",
        CLAUDE_CODE_USE_BEDROCK:"1",CLAUDE_CODE_USE_VERTEX:"1",
        CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:"0",UNRELATED_SETTING:"kept"});
}
function settings(){events.push("settings");override()}
async function loadCA(){
    events.push("ca-start");await caReady;caches.ca="native-ca";
    events.push("ca-end");return caChanged;
}
async function loadMTLS(){
    events.push("mtls-start");await mtlsReady;caches.mtls="native-mtls";
    events.push("mtls-end");return mtlsChanged;
}
function clearProxy(){events.push("clear-proxy");caches.proxy++}
function configureAgents(){events.push("configure-agents");caches.agents++}
const applyFinal=_ccProviderApplyWorkerFinal;
_ccProviderApplyWorkerFinal=()=>{events.push("restore");applyFinal()};
async function initialize(){
    events.push("initialize");
    if(worker==="live"){
        assert.equal(process.env.ANTHROPIC_BASE_URL,live.ANTHROPIC_BASE_URL);
        assert.equal(process.env.ANTHROPIC_AUTH_TOKEN,"live-token");
        assert.equal(process.env.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST,"1");
        for(const key of ["ANTHROPIC_API_KEY","CLAUDE_CODE_USE_BEDROCK","CLAUDE_CODE_USE_VERTEX"])
            assert.equal(key in process.env,false,key);
    }else{
        assert.equal(worker,"foreground");
        assert.equal(process.env.ANTHROPIC_API_KEY,"settings-key");
        assert.equal(process.env.ANTHROPIC_AUTH_TOKEN,"settings-token");
    }
    assert.equal(process.env.UNRELATED_SETTING,"kept");
    finish();
}
function caught(error){events.push("caught");finish(error)}
'''
        + patched
        + '''
(async()=>{
    telemetry(null);
    if(worker!=="foreground")process.env.CLAUDE_CODE_SESSION_KIND="bg";
    if(worker==="live")_ccProviderWorkerEnv=live;
    await Promise.resolve();
    if(asynchronous){
        assert.deepEqual(events,["settings","ca-start","mtls-start"]);
        resolveMTLS();await Promise.resolve();await Promise.resolve();
        assert.deepEqual(events,["settings","ca-start","mtls-start","mtls-end"]);
        override();resolveCA();
    }
    const error=await done;
    if(worker==="missing")assert.equal(error?.code,"EPROVIDERENV");
    else assert.equal(error,undefined);
    const expected=["settings"];
    if(asynchronous){
        expected.push("ca-start","mtls-start","mtls-end","ca-end");
        assert.equal(caches.ca,"native-ca");assert.equal(caches.mtls,"native-mtls");
        if(caChanged||mtlsChanged)expected.push("clear-proxy","configure-agents");
    }
    expected.push("restore",worker==="missing"?"caught":"initialize");
    assert.deepEqual(events,expected);
    assert.equal(caches.proxy,Number(asynchronous&&(caChanged||mtlsChanged)));
    assert.equal(caches.agents,caches.proxy);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    )
    result = subprocess.run(  # noqa: S603 - runtime is a resolved node/bun executable
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "native",
    [
        _DELAYED_ASYNC.replace("caChanged||mtlsChanged", "caChanged&&mtlsChanged"),
        _DELAYED_ASYNC.replace("[loadCA(),loadMTLS()]", "[loadCA(),loadMTLS(),auth()]"),
        _DELAYED_ASYNC.replace("if(caChanged||mtlsChanged)", "if(other||mtlsChanged)"),
        _DELAYED_ASYNC.replace("configureAgents();", "configureAgents();override();"),
    ],
)
def test_delayed_settings_rejects_unknown_async_layout(native: str) -> None:
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV_198.patches
        if patch.name == "restore-provider-env-after-delayed-settings"
    )
    assert patch.pattern.search(_DELAYED_PREFIX + native + "await initialize()") is None


_WORKER_203 = (
    'function build(job,dir,snapshot,sock,auth){let ambient={...process.env},'
    'env={...ambient,...job.env,CLAUDE_CODE_SESSION_KIND:"bg",BROWSER:"true"},'
    'pathKey=Object.hasOwn(ambient,"PATH")?"PATH":Object.keys(ambient).find((key)=>'
    'key.toUpperCase()==="PATH"),pathValue=job.env?.PATH||(pathKey?ambient[pathKey]:void 0);'
    'for(let key of Object.keys(env))if(key.toUpperCase()==="PATH")delete env[key];'
    'if(pathValue)env[pathKey??"PATH"]=pathValue;'
    'if(process.env.CLAUDE_CONFIG_DIR)env.CLAUDE_CONFIG_DIR=process.env.CLAUDE_CONFIG_DIR;'
    'for(let key of ["CLAUDE_CODE_BRIDGE_SESSION_ID","CLAUDE_BG_RV_AUTH"])'
    'if(!job.env?.[key])delete env[key];'
    'if(ambient.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST){'
    'delete env.ANTHROPIC_AUTH_TOKEN;'
    'const key=ambient.CLAUDE_CODE_HOST_AUTH_ENV_VAR;if(key)delete env[key];'
    'for(let key of urls)delete env[key]'
    '}else if(env.ANTHROPIC_BASE_URL!==ambient.ANTHROPIC_BASE_URL){'
    'for(let key of urls)delete env[key];'
    'if(ambient.ANTHROPIC_BASE_URL)delete env.ANTHROPIC_AUTH_TOKEN}'
    'if(auth)env.CLAUDE_BG_RV_AUTH=auth.rvAuth,env.CLAUDE_BG_PTY_AUTH=auth.ptyAuth;'
    'if(snapshot)delete env.CLAUDE_CODE_OAUTH_TOKEN;'
    'if(job.launch.mode==="exec"){for(let key of Object.keys(env))'
    'if(key.startsWith("CLAUDE_")&&key!=="CLAUDE_JOB_DIR"&&key!=="CLAUDE_CONFIG_DIR"'
    '&&key!=="CLAUDE_BG_PTY_AUTH"||key.startsWith("OTEL_"))delete env[key];'
    'if(delete env.BROWSER,env.ANTHROPIC_BASE_URL)delete env.ANTHROPIC_AUTH_TOKEN;'
    'for(let key of urls)delete env[key];env.CLAUDE_PTY_HOST_EXEC="1"}return env}'
)


def test_203_worker_preserves_path_security_and_transient_auth() -> None:
    source = (
        _PROVIDER_HELPERS.replace(
            'urls=["ANTHROPIC_BASE_URL","ANTHROPIC_VERTEX_BASE_URL"]',
            'urls=["ANTHROPIC_BASE_URL","_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL",'
            '"ANTHROPIC_CUSTOM_HEADERS"]',
        )
        + _WORKER_203
    )
    names = (
        "snapshot-transient-provider-env",
        "scrub-worker-provider-env",
        "restore-provider-env-after-native-worker-scrubs",
    )
    for name in names:
        patch = next(p for p in BACKGROUND_PROVIDER_ENV_198.patches if p.name == name)
        source, count = patch.pattern.subn(patch.replacement, source)
        assert count == 1, name
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    script = (
        source
        + '''
    const assert=require("node:assert/strict");
    const original=process.env;
    for(const casing of ["PATH","Path"])
    for(const mode of ["claude","exec"])
    for(const host of [false,true])
    for(const suppliedPath of [false,true]){
        process.env={ANTHROPIC_BASE_URL:"https://daemon.invalid",
            ANTHROPIC_AUTH_TOKEN:"stale-token",AWS_BEARER_TOKEN_BEDROCK:"stale-cloud",
            CLAUDE_CODE_BRIDGE_SESSION_ID:"private-bridge",CLAUDE_BG_RV_AUTH:"stale-rv",
            OTEL_SECRET:"private-telemetry",[casing]:"/daemon/bin"};
        if(host)Object.assign(process.env,{CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:"1",
            CLAUDE_CODE_HOST_AUTH_ENV_VAR:"HOST_SECRET",HOST_SECRET:"private-host"});
        const payload=Object.fromEntries(_ccProviderKeys().map(key=>[key,null]));
        Object.assign(payload,{ANTHROPIC_BASE_URL:"https://request.invalid",
            ANTHROPIC_AUTH_TOKEN:"request-token",ANTHROPIC_API_KEY:"request-key",
            ANTHROPIC_CUSTOM_HEADERS:"X-Test: requester",
            _CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL:"1"});
        const job={launch:{mode},env:suppliedPath?{PATH:"/request/bin"}:{}};
        const env=build(job,"dir",null,"sock",{rvAuth:"fresh-rv",ptyAuth:"fresh-pty"},payload);
        assert.equal(env[casing],suppliedPath?"/request/bin":"/daemon/bin");
        assert.deepEqual(Object.keys(env).filter(k=>k.toUpperCase()==="PATH"),[casing]);
        assert.equal(env.HOST_SECRET,undefined);
        assert.equal(env.CLAUDE_CODE_BRIDGE_SESSION_ID,undefined);
        assert.equal(env.CLAUDE_BG_PTY_AUTH,"fresh-pty");
        if(mode==="exec"){
            for(const key of ["ANTHROPIC_BASE_URL","ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_CUSTOM_HEADERS","_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL",
                "CLAUDE_CODE_PROVIDER_ENV_TRANSIENT","CLAUDE_BG_RV_AUTH","OTEL_SECRET"])
                assert.equal(env[key],undefined,key);
            assert.equal(env.CLAUDE_PTY_HOST_EXEC,"1");
        }else{
            assert.equal(env.ANTHROPIC_BASE_URL,payload.ANTHROPIC_BASE_URL);
            assert.equal(env.ANTHROPIC_AUTH_TOKEN,payload.ANTHROPIC_AUTH_TOKEN);
            assert.equal(env.AWS_BEARER_TOKEN_BEDROCK,undefined);
            assert.equal(env.CLAUDE_BG_RV_AUTH,"fresh-rv");
            assert.deepEqual(JSON.parse(env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT),payload);
        }
        for(const key of ["CLAUDE_CODE_SESSION_ACCESS_TOKEN","CLAUDE_CODE_BRIDGE_SESSION_ID"]){
            assert(!_ccProviderKeys().includes(key));
            assert.throws(()=>_ccRequireProviderEnv({[key]:"private"}));
        }
    }
    process.env=original;
    '''
    )
    result = subprocess.run(  # noqa: S603 - runtime is a resolved node/bun executable
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
