"""Tests for the ported patch catalog (channels, dev-channel, syntax).

Each test uses a synthetic snippet that matches the real 2.1.170 minification.
The integration tests apply COMPACT_SESSION to a real binary.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from ccpatch.patches import (
    _PROVIDER_ENV_EXPLICIT_KEYS,
    _PROVIDER_ENV_VERTEX_REGION_KEYS,
    _SYNTAX_DARK_MAP,
    AUTO_MODE_LOCAL_FALLBACK,
    BACKGROUND_PROVIDER_ENV,
    BACKGROUND_PROVIDER_ENV_198,
    CATPPUCCIN_SYNTAX,
    CHANNELS_ENABLED,
    COMPACT_SESSION,
    DEV_CHANNEL_INHERITANCE,
    MULTI_PROVIDER_SDK,
    RETRACTION_ARCHIVE,
    THINKING_SUMMARIES_NONINTERACTIVE,
    THINKING_SUMMARIES_NONINTERACTIVE_198,
    Patch,
    PatchError,
    PatchSet,
    _model_costs_patch,
    default_patch_sets,
)

_DEV_CHANNEL_SRC = (
    # respawn-flag allowlists the bg-worker dispatch filters argv through
    'RfH=new Set(["--advisor","--channels","--permission-prompt-tool","--tools"]),'
    'HE6=new Set(["--add-dir","--file","--channels"]);'
    # the live-dispatch arg serializer ($UH) that builds dispatchExtraArgs
    "function $UH(H){return [...H.settings?[\"--settings\",H.settings]:[],"
    '...H.pluginDir.flatMap(($)=>["--plugin-dir",$]),'
    '...H.mcpConfig.flatMap(($)=>["--mcp-config",$]),'
    '...H.strictMcpConfig?["--strict-mcp-config"]:[]]}'
    # the parent-side parse block, gated on !isNonInteractiveSession (XH)
    'if(W$&&W$.length>0)r$=c$(W$,"--channels"),n9H(r$);'
    "if(!XH){if(U$&&U$.length>0)"
    'Y$=c$(U$,"--dangerously-load-development-channels")}'
    'if(r$.length>0){d("tengu_mcp_channel_flags",{})}'
)

_MULTI_PROVIDER_RESUME_SRC = (
    'let f=_.message.model,A=Yl();if(YD6(A)&&!N5H(f)&&QOA(A,qK(f)))'
    'return{kind:"mode_dependent_setting"};'
    'let reason=!(NATIVE_FAMILY(f))?"unknown_family":!ALLOW(f)?"not_allowed":null;'
    'return{kind:"native",model:f,reason};'
)
_MULTI_PROVIDER_AGENT_IDENTIFIERS_SRC = (
    'ALIASES=["sonnet","opus","haiku","fable","best","sonnet[1m]",'
    '"opus[1m]","fable[1m]","opusplan"];'
    'FIRST_PARTY=Object.values(TABLE).map((ENTRY)=>ENTRY.firstParty);'
    'process.env.ANTHROPIC_DEFAULT_FABLE_MODEL||MODELS().fable5;'
    'function NATIVE_PICKER(FLAG=!1){let SEEN=new Set,'
    'ROWS=OPTIONS(FLAG).filter((ROW)=>{if(ROW.value===null)return!0;'
    'if(SEEN.has(ROW.value))return LOG(`model options: dropping duplicate row ${ROW.value}`);'
    'SEEN.add(ROW.value);return!0});return ROWS}'
)
_MULTI_PROVIDER_AGENT_SRC = (
    'model:k.enum(["sonnet","opus","haiku","fable"]).optional().describe('
    '"Optional model override for this agent. Takes precedence over frontmatter.")'
)
_MULTI_PROVIDER_SRC = (
    _MULTI_PROVIDER_AGENT_IDENTIFIERS_SRC
    + _MULTI_PROVIDER_RESUME_SRC
    + _MULTI_PROVIDER_AGENT_SRC
    + "},COST_HELPER=READY;COSTS={[modelKey(NATIVE.firstParty)]:NATIVE_COST};"
    "let OPT={apiKey:key};return new SDK(OPT)}async function NEXT(){}"
    "function TOP_WINDOW(MODEL,HEADERS){let OVERRIDE=DEBUG_WINDOW();if(OVERRIDE!==void 0)"
    "return OVERRIDE;if(EXTENDED(MODEL,HEADERS))return EXTENDED_WINDOW;return "
    "WINDOW(MODEL,HEADERS)}"
    "function WINDOW(MODEL,HEADERS){if(NATIVE1M(MODEL))return 1e6;if(HEADERS?.includes("
    "BETA.header)&&ELIGIBLE(MODEL))return 1e6;if(ENTITLED(MODEL))return 1e6;let CUSTOM="
    "CUSTOM_WINDOW(MODEL);if(CUSTOM!==null)return CUSTOM;return FALLBACK}"
    "function OUTPUT(MODEL){let DEFAULT,UPPER,NORMALIZED=NORMALIZE_OUTPUT(MODEL);if("
    'NORMALIZED==="native")DEFAULT=32000,UPPER=128000;else DEFAULT=32000,UPPER=128000;'
    "let CONFIG=MODEL_CONFIG(MODEL);if(CONFIG?.max_tokens&&CONFIG.max_tokens>=4096)"
    "UPPER=CONFIG.max_tokens,DEFAULT=Math.min(DEFAULT,UPPER);"
    "return{default:DEFAULT,upperLimit:UPPER}}"
    'function EXPLICIT(MODEL,SETTING){let{source:SOURCE}=RESOLVE(MODEL,SETTING);return SOURCE==="env"||SOURCE==="settings"||SOURCE==="model-default"}'
    "function FILTER(MSGS,MODEL){return STRIP(MSGS,(MESSAGE)=>"
    "MESSAGE.message.model!==SYNTHETIC&&MESSAGE.message.model!==MODEL)}"
    "let R1=await N1.beta.messages.create({...REQ1,model:KA(REQ1.model)},"
    "{signal:SIG1.signal,timeout:TIME1,...Object.keys(HDR1).length>0&&{headers:HDR1}})"
    "let R2=await N2.beta.messages.create({...REQ2,...CREDIT!==void 0&&"
    "{fallback_credit_token:CREDIT},stream:!0},{signal:SIG2,...Object.keys(HDR2).length>0&&"
    "{headers:HDR2}})"
    "let START=performance.now(),R3=await N3.beta.messages.create(REQ3,"
    "{signal:SIG3,...TIME3!==void 0&&{timeout:TIME3}})"
    'async function TOKENS(MSGS,TOOLS,MODEL){return WRAP(MSGS,TOOLS,async()=>{try{'
    'let EFFECTIVE=MODEL??DEFAULT();let CLIENT=await LF({maxRetries:1,model:EFFECTIVE,'
    'source:"count_tokens"}),'
    "BETAS=RAW.filter((BETA)=>ij6.has(BETA)),RESULT=await CLIENT.beta.messages."
    "countTokens({model:KA(EFFECTIVE),messages:MSGS,tools:TOOLS});return RESULT.input_tokens"
    "}catch(ERROR){return N(`countTokens API call failed: ${ERROR.message}`),null}})}"
    'function ATTR(){if(MODE()==="remote"){if(ENV.CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION)return{commit:"",pr:""};return REMOTE()}let H=CURRENT(),$=ISFIRST(H)?DISPLAY(FIRST.firstParty):ISNATIVE(H)?DISPLAY(H):"Claude",q=`\\uD83E\\uDD16 Generated with [Claude Code](${URL})`,K=`Co-Authored-By: ${$} <noreply@anthropic.com>`,_=SETTINGS();if(_.attribution)return{commit:_.attribution.commit??K,pr:_.attribution.pr??q};if(_.includeCoAuthoredBy===!1)return{commit:"",pr:""};return{commit:K,pr:q}}'
    'function COMPACT_GIT(F){if(!GIT())return"";let n="",{commit:r,pr:o}=ATTR();return r+o}'
    'function FULL_GIT(F){if(!GIT())return"";let n="",{commit:r,pr:o}=ATTR();return r+o}'
    'function COMPACT_BASH(F){return COMPACT_GIT(F)}'
    'function BASH_DISPATCH(M,F){if(SHORT(M))return COMPACT_BASH(F);return FULL_GIT(F)}'
    'var BASH={async prompt({model:M,tools:T}){let F=[];return BASH_DISPATCH(M,F)},isConcurrencySafe(){return!1}};'
    'async function SERIALIZE_NATIVE(E,T){let o="",s="",a=o+s+""+("inputJSONSchema"in E&&E.inputJSONSchema?`${E.name}:${HASH(E.inputJSONSchema)}`:E.name),l=CACHE(),c=l.get(a);return c}'
    "function PICK(FLAG){let OPTIONS=NATIVE(FLAG),CUSTOM=process.env."
    "ANTHROPIC_CUSTOM_MODEL_OPTION;"
    "function RECOGNIZE(MODEL){let NAME=DISPLAY(MODEL);if(!NAME)return null;"
    'let NORMALIZED=NORMALIZE(MODEL),ALIAS=null;if(NORMALIZED.includes("fable"))'
    "SCHEMAS=await Promise.all(TOOLS.map((TOOL)=>SERIALIZE(TOOL,{"
    "getToolPermissionContext:CTX.getToolPermissionContext,tools:ALLTOOLS,agents:CTX.agents,"
    "allowedAgentTypes:CTX.allowedAgentTypes,model:MODELID,deferLoading:DEFER(TOOL)})));"
)

_PROVIDER_ENV_SRC = (
    'SEL=["CLAUDE_CODE_USE_BEDROCK","CLAUDE_CODE_USE_VERTEX"],'
    'URLS=["ANTHROPIC_BASE_URL","ANTHROPIC_VERTEX_BASE_URL"],'
    'CREDS=["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","CLAUDE_CODE_OAUTH_TOKEN"],'
    'SKIP=["CLAUDE_CODE_SKIP_BEDROCK_AUTH","CLAUDE_CODE_SKIP_VERTEX_AUTH",'
    '"CLAUDE_CODE_SKIP_FOUNDRY_AUTH","CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH",'
    '"CLAUDE_CODE_SKIP_MANTLE_AUTH"],'
    'MODELS=["ANTHROPIC_MODEL","ANTHROPIC_DEFAULT_SONNET_MODEL"],'
    'CUSTOM=["ANTHROPIC_CUSTOM_MODEL_OPTION","ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"],'
    'KNOWN=new Set;'
    'function SNAP(){let out={};for(let key of OLD){let value=process.env[key];'
    'if(value===void 0)continue;if(value===""&&key!=="CLAUDE_SECURESTORAGE_CONFIG_DIR")'
    'continue;out[key]=value}return out}'
    'iso=source==="repl"?"none":opts?.bgIsolation,provider=opts?.providerEnv??SNAP(),'
    'async function write(dir,state){let{pinned:a,sortOrder:b,stateSortOrder:c,...rest}=state;'
    'bgIsolation:job.bgIsolation,providerEnv:job.providerEnv,'
    'providerEnv:z.record(z.string(),z.string()).transform(filter).optional(),'
    'providerEnv:prior?.providerEnv,providerEnv:SNAP(),sessionPermissionRules:'
    '||job.providerEnv,...job.providerEnv&&{providerEnv:job.providerEnv}'
    'z.object({proto:p,op:z.literal("dispatch"),d:dispatch(),timeoutMs:z.number(),auth:'
    'send({proto:PROTO,op:"dispatch",d:{...job,nonce:nonce},timeoutMs:5000,'
    'auth:await auth()}'
    'if(reply.ok&&reply.op==="dispatch")return success();if("code"in reply&&'
    'send({proto:PROTO,op:"dispatch",d:{...retryJob,nonce:retryNonce},timeoutMs:5000,'
    'auth:await retryAuth()}'
    'if(retryReply.ok&&retryReply.op==="dispatch")return log(),await metric(),'
    '{ok:!0,short:shortName,sessionId:sessionId,idle:idle,name:name,rescued:!0}'
    # Captured releases acknowledge both fresh and recovered dispatches.
    'return respond(sock,{ok:!0,op:operation,short:short,pid:worker.record.pid,'
    'return respond(retrySock,{ok:!0,op:retryOperation,short:retryShort,pid:retryWorker.record.pid,'
    'case"dispatch":if(!check(req.auth,control))return respond(sock,{ok:!1,'
    'error:"bad",code:"EAUTH"});if(await pause(0),sock.readableEnded||sock.destroyed)'
    '{metric();return}return wait(handles,sock,"dispatch",req.d.short,req.d.nonce,'
    'req.timeoutMs,dispatchCb(req.d)'
    'class Worker{dispatch;spawnPty;getAuthSnapshot;via;record;'
    'constructor(job,spawn,authFn,via,record){this.dispatch=job;'
    'this.spawnPty=spawn;this.getAuthSnapshot=authFn;this.via=via;'
    'this.record={sessionId:job.sessionId,...record}}'
    'static spawn(job,spawn,authFn,options){let worker=new Worker(job,spawn??defaultSpawn(),'
    'authFn,"cold");worker.doSpawn(job.reattachEnv);return worker}'
    'static claim(job,options){let worker=new Worker(job,options.spawnPty,'
    'options.getAuthSnapshot,"spare",{pid:spare.pid,cliVersion:BUILD.VERSION});return worker}'
    'static adopt(job,roster){let worker=new Worker(job,void 0,void 0,"adopted",roster);'
    'worker.rvAuth=roster.rvAuth;'
    'if(worker.connectRv(),roster.pendingRespawn==="upgrade")'
    'worker.transitionTo({kind:"upgrading"});return worker}'
    'static buildClaimFrame(job,snapshot,auth){let dir=jobDir(job.short),'
    'env=buildEnv(job,dir,snapshot,rvSock(job.short),auth);return{env,argv:[]}}'
    'async doSpawn(reattachEnv,afterUpgrade){'
    'let argv=buildArgv(job,this.attempt,messages,session,flags),'
    'env=buildEnv(job,dir,snapshot,this.rvSockPath??rvSock(job.short),this.socketAuth());'
    'this.spawnPty(argv,env)}'
    'async respawnIfIdleStale(pinned,trigger="sweep"){'
    'if(this.dispatch.launch.mode==="exec")return{respawned:!1,reason:"not-stale"};'
    'return this.shutdownWorker(),{respawned:!0}}'
    'async retireIfSettled(grace,pinned){return{retired:!1,reason:"not-settled"}}'
    'fireAuthRekey(){this.transitionTo({kind:"upgrading"});this.sigtermWorker()}'
    'rekeyForAuthMismatch(reason){this.fireAuthRekey()}'
    'onExit(code,signal){if(this.isDetached)return;if(this.phase.kind==="retired")return;'
    'this.doSpawn()}'
    'connectRv(){if(this.rv||this.isDetached||this.record.outcome)return;'
    'this.rv=connectRv(this.rvSockPath??rvSock(this.dispatch.short),(event)=>{'
    'if(event.type==="heartbeat")this.lastRvHeartbeat=Date.now();'
    'else if(event.type==="done")this.settle(event.outcome)},'
    '()=>void this.checkPid(),()=>{this.workerReady=!0},this.rvAuth)}'
    'clearLiveness(){if(this.pidPoll)clearInterval(this.pidPoll);'
    'this.rv?.close(),this.rv=void 0}}'
    'function stall(worker,socket,dispatch,closed,pending){let job=worker.dispatch;'
    'worker.kill("SIGTERM");dispatch({...job,attachStallRespawns:1,launch:ok?next:job.launch})}'
    'function rvServer(){net.createServer((socket)=>{current?.destroy(),current=socket;'
    'socket.on("data",(data)=>{receiveRv(data)})})}'
    'function receiveRv(request){if(request.role==="supervisor"){'
    'if("auth"in request&&validateRvAuth(request.auth,rvToken))authenticated=!0;return}'
    'if(rvToken&&!authenticated&&request.type!=="repaint"){'
    'sendRv({type:"reply-rejected"});return}'
    'if(request.type==="shutdown"){sendRv({type:"shutting-down"});return}}'
    'function rvHello(){return{proto:RV_PROTO,role:"supervisor",supervisorPid:process.pid}}'
    'function rvState(){return{sessionId:currentSession(),gates:{}}}'
    'function W0q(job,spare,spawn,auth){let worker=Worker.claim(job,{pid:spare.hostPid,'
    'ptySockPath:spare.ptySock,spawnPty:spawn,getAuthSnapshot:auth});'
    'return fetchSnapshot(job.short,auth?.()).then((snapshot)=>send(spare.claimSock,'
    'UVA(job,snapshot,worker.socketAuth(),spare.claimAuth)))'
    'function UVA(job,snapshot,auth,claimAuth){let{env:built,argv:args}='
    'Worker.buildClaimFrame(job,snapshot,auth);'
    'manage=async(job,retry=0,afterUpgrade)=>{'
    'return await delay(100),manage(job,retry+1,afterUpgrade)'
    'pendingUpgrade=()=>manage(saved.dispatch,0,!0),'
    'dispatchFiles=watch((incoming)=>void manage(incoming).catch(logError)),'
    'internal={dispatch:(incoming)=>void manage(incoming).catch(logError)},'
    'adoptedRespawn=(incoming)=>manage({...incoming,source:"respawn"}),'
    'let worker=W0q(job,spare,spawn,authObj.getAuthSnapshot)'
    'Worker.spawn(job,spawn,authObj.getAuthSnapshot,afterUpgrade?'
    '{afterUpgrade:afterUpgrade}:void 0)'
    'function buildEnv(job,dir,snapshot,rvSock,socketAuth){let ambient={...process.env},'
    'env={...ambient,...job.env};if(process.env.CLAUDE_CONFIG_DIR)'
    'env.CLAUDE_CONFIG_DIR=process.env.CLAUDE_CONFIG_DIR;for(let key of UXq)'
    'if(!job.env?.[key])delete env[key];for(let key of FXq)if(!job.env?.[key])'
    'delete env[key];for(let key of Object.keys(env))if(VERTEX.some((prefix)=>'
    'key.startsWith(prefix))&&!job.env?.[key])delete env[key];if(providerManaged(ambient))'
    '{for(let key of CREDS)delete env[key]}return env}'
    'async function O09(claimFrame,mainModule){let cwd=await resolveCwd(claimFrame.cwd);'
    'process.chdir(cwd),sessionSetup(),Object.assign(process.env,claimFrame.env),'
    'process.argv=[process.argv[0],...claimFrame.argv],It8(),Da8(),lT6(),Yq8(),Wy$();'
    'let{main:workerMain}=await mainModule;await workerMain()}'
    'function CB$(){if(remote()){log("Waiting for remote managed settings before telemetry init"),'
    'Vy$().then(async()=>{log("Remote managed settings loaded, initializing telemetry"),'
    'Ko(),await vLq()}).catch(logError)}else vLq().catch(logError)}'
    '.hook("preAction",async(command,action)=>{mark("preAction_start");let start=now();'
    'if(await initializeMdm(),mark("preAction_after_mdm"),await settingsInit(),'
    'mark("preAction_after_init"),terminalTitle())setTitle();'
    'if(noninteractive){setMode(),Ko(),CB$();let operationalStart=performance.now(),'
)


def test_channels_enabled() -> None:
    src = 'a&&H?.channelsEnabled!==!0;function zH(){return M$("tengu_harbor",!1)}'
    out = CHANNELS_ENABLED.apply(src)
    assert "a&&!1;" in out
    assert "return !!1}" in out
    assert "tengu_harbor" not in out


def test_dev_channel_inheritance_threads_natively() -> None:
    out = DEV_CHANNEL_INHERITANCE.apply(_DEV_CHANNEL_SRC)
    # flag forwarded through both respawn allowlists (value + multi-value)
    assert (
        '"--channels","--dangerously-load-development-channels",'
        '"--permission-prompt-tool"' in out
    )
    assert '"--file","--channels","--dangerously-load-development-channels"]' in out
    # live dispatch: $UH serializer appends dev-channels (scanned from argv)
    assert 'strictMcpConfig?["--strict-mcp-config"]:[],...(()=>' in out
    assert (
        'return _dc.length?["--dangerously-load-development-channels",..._dc]:[]' in out
    )
    # bg worker registers dev channels from its OWN parsed flag (no env round-trip),
    # reusing the registrar/base/parse identifiers captured from the block
    assert (
        'CLAUDE_CODE_SESSION_KIND==="bg"&&U$&&U$.length>0){'
        'n9H([...r$,...c$(U$,"--dangerously-load-development-channels")' in out
    )
    assert "devEntry,dev:!0}))])}" in out  # brace-terminated (no `)if(` syntax error)
    assert "CLAUDE_DEV_CHANNELS" not in out  # the env round-trip is gone
    # the original telemetry block is preserved (length-free append)
    assert 'd("tengu_mcp_channel_flags",{})' in out


def test_dev_channel_preserves_restricted_dispatch() -> None:
    tail = '...H.restricted?["--restricted"]:[]'
    source = _DEV_CHANNEL_SRC.replace(
        '...H.strictMcpConfig?["--strict-mcp-config"]:[]]',
        '...H.strictMcpConfig?["--strict-mcp-config"]:[],' + tail + "]",
    )
    patched = DEV_CHANNEL_INHERITANCE.apply(source)
    assert tail + "]}" in patched
    assert source.count(tail) == patched.count(tail) == 1


def test_dev_channel_preserves_artifact_watch_flags() -> None:
    prefix = '"--channels","--watch-artifact","--watch-artifact-no-autoreact",'
    source = _DEV_CHANNEL_SRC.replace(
        '"--channels","--permission-prompt-tool"', prefix + '"--permission-prompt-tool"'
    )
    patched = DEV_CHANNEL_INHERITANCE.apply(source)
    assert prefix + '"--dangerously-load-development-channels",' in patched


def test_dev_channel_required_no_op_fails() -> None:
    with pytest.raises(PatchError, match="dev-channel-inheritance"):
        DEV_CHANNEL_INHERITANCE.apply("unrelated source")


@pytest.mark.parametrize(
    ("version", "expected"),
    (
        (None, False),
        ((2, 1, 173), False),
        ((2, 1, 174), True),
        ((2, 1, 175), True),
        ((2, 1, 176), True),
        ((2, 1, 177), True),
        ((2, 1, 178), True),
        ((2, 1, 179), True),
        ((2, 1, 181), True),
        ((2, 1, 182), True),
        ((2, 1, 183), True),
        ((2, 1, 185), True),
        ((2, 1, 186), True),
        ((2, 1, 187), True),
        ((2, 1, 190), True),
        ((2, 1, 191), True),
        ((2, 1, 193), True),
        ((2, 1, 195), True),
        ((2, 1, 196), True),
        ((2, 1, 197), True),
        ((2, 1, 198), False),
        ((2, 1, 199), False),
        ((2, 1, 200), False),
        ((2, 1, 201), False),
        ((2, 1, 202), False),
        ((2, 1, 203), False),
        ((2, 1, 204), False),
    ),
)
def test_background_provider_environment_is_version_gated(
    version: tuple[int, ...] | None, expected: bool
) -> None:
    assert BACKGROUND_PROVIDER_ENV.applies_to(version) is expected
    assert MULTI_PROVIDER_SDK.applies_to(version) is (
        expected
        or version
        in (
            (2, 1, 198),
            (2, 1, 199),
            (2, 1, 200),
            (2, 1, 201),
            (2, 1, 202),
            (2, 1, 203),
            (2, 1, 204),
            (2, 1, 205),
            (2, 1, 206),
        )
    )


@pytest.mark.parametrize("agent_context", ["", ",agentContext:CONTEXT()"])
def test_count_tokens_preserves_agent_context(agent_context: str) -> None:
    source = _MULTI_PROVIDER_SRC.replace(
        'source:"count_tokens"}', f'source:"count_tokens"{agent_context}}}'
    )
    patched = MULTI_PROVIDER_SDK.apply(source)
    assert f'source:"count_tokens"{agent_context}}}' in patched
    assert '_ccMultiProviderRoute(CLIENT,_ccRequest,{},!0)' in patched


@pytest.mark.parametrize(
    "cloud_branch",
    ["", 'if(cloud){checkAuth();' + 'validate();' * 80 + 'print(`sent\\n`);return}'],
)
@pytest.mark.parametrize(
    "warning",
    [
        "",
        'let warning=(args.continue||args.resume||session)&&!enabled()?null:check(model??fallback);'
        'if(warning&&format!=="json"&&format!=="stream-json")print(warning);',
    ],
)
def test_operational_entry_preserves_cloud_security_branch(
    cloud_branch: str, warning: str
) -> None:
    source = _PROVIDER_ENV_SRC.replace(
        "if(noninteractive){", "if(noninteractive){" + cloud_branch
    ).replace("let operationalStart", warning + "let operationalStart")
    patched = BACKGROUND_PROVIDER_ENV.apply(source)
    assert "if(noninteractive){" + cloud_branch in patched
    assert "Ko(),CB$(_ccProviderWorkerEnv);_ccProviderApplyWorkerFinal();" in patched
    assert warning + "let operationalStart" in patched


def test_patch_sets_allow_omitted_version_by_default() -> None:
    assert CHANNELS_ENABLED.applies_to(None)


@pytest.mark.parametrize("transcript_path", ["", "transcriptPath,"])
def test_background_provider_respawn_preserves_transcript_path(
    transcript_path: str,
) -> None:
    argv = f"buildArgv(job,this.attempt,messages,session,{transcript_path}flags)"
    source = _PROVIDER_ENV_SRC.replace(
        "buildArgv(job,this.attempt,messages,session,flags)", argv
    )
    patched = BACKGROUND_PROVIDER_ENV.apply(source)
    assert argv in patched
    assert "this.socketAuth(),this.providerEnv);" in patched


def test_background_provider_environment_transforms_complete_fixture() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)

    assert (
        "function _ccProviderKeys(){if(OLD==null||SEL==null||URLS==null||"
        "CREDS==null||SKIP==null||MODELS==null||CUSTOM==null)throw " in patched
    )
    assert (
        "return [...OLD,...SEL,...URLS,...CREDS,...SKIP,...MODELS,...CUSTOM" in patched
    )
    assert "function _ccRequireProviderEnv(_ccProviderEnv)" in patched
    assert "_ccProviderEnv=_ccProviderRetain(_ccProviderEnv);" in patched
    assert "let _ccAllowed=new Set(_ccProviderKeys())" in patched
    for key in _PROVIDER_ENV_EXPLICIT_KEYS:
        assert f'"{key}"' in patched
    assert "out[key]=value===void 0?null:value" in patched
    assert patched.count("providerEnvVersion:3,providerEnv:SNAP()") == 2
    assert (
        "z.record(z.enum(_ccProviderKeys()),z.union([z.string(),z.null()]))" in patched
    )
    assert "req.providerEnvVersion!==3||req.providerEnv===void 0" in patched
    assert "op:operation,providerEnvVersion:3,short:short" in patched
    assert "reply.providerEnvVersion!==3" in patched
    assert "retryReply.providerEnvVersion!==3" in patched
    assert "Restart the stale Claude Code daemon and try again" in patched
    assert 'reply.code==="EPROVIDERENV"' in patched
    assert 'retryReply.code==="EPROVIDERENV"' in patched
    assert 'reason:"daemon-unreachable",detail:reply.error' in patched
    assert (
        'throw Object.assign(Error(retryReply.error),{code:"EPROVIDERENV"})' in patched
    )
    assert "dispatchCb(req.d,0,void 0,req.providerEnv)" in patched
    assert (
        'new Worker(job,spawn??defaultSpawn(),authFn,"cold",void 0,_ccProviderEnv)'
        in patched
    )
    assert "this.socketAuth(),this.providerEnv" in patched
    assert "Worker.claim(job," in patched and "_ccProviderEnv" in patched
    assert "function W0q(job,spare,spawn,auth,_ccProviderEnv)" in patched
    assert (
        "UVA(job,snapshot,worker.socketAuth(),spare.claimAuth,_ccProviderEnv)"
        in patched
    )
    assert "function UVA(job,snapshot,auth,claimAuth,_ccProviderEnv)" in patched
    assert "Worker.buildClaimFrame(job,snapshot,auth,_ccProviderEnv)" in patched
    assert "manage(job,retry+1,afterUpgrade,_ccProviderEnv)" in patched
    assert "for(let _ccKey of _ccProviderKeys())delete env[_ccKey]" in patched
    assert "Object.entries(_ccProviderEnv)" in patched
    assert "for(let _ccKey of _ccProviderKeys())delete process.env[_ccKey]" in patched
    assert "providerEnv:_ccProviderEnv,...rest" in patched
    assert "providerEnv:job.providerEnv" not in patched
    assert "providerEnv:prior?.providerEnv" not in patched
    assert "providerEnv:SNAP(),sessionPermissionRules" not in patched
    assert "...job.providerEnv&&{providerEnv:job.providerEnv}" not in patched
    file_fallback = 'filePayload=serialize({...job,nonce})'
    assert "providerEnv" not in file_fallback


def test_background_provider_environment_startup_defers_key_spreads() -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to evaluate the generated provider key function")
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    start = patched.index("function _ccProviderKeys()")
    end = patched.index("function SNAP()", start)
    generated = patched[start:end]
    script = (
        'let OLD,SEL,URLS,CREDS,SKIP,MODELS,CUSTOM;'
        + generated
        + ';console.log("startup-ok");'
        + 'try{_ccProviderKeys()}catch(error){'
        + 'console.log(`${error.code}:${error.message}`)}'
    )

    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "startup-ok",
        "EPROVIDERENV:Background provider environment key registry is unavailable",
    ]


def test_background_provider_environment_validates_transient_payload() -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to evaluate the generated provider validator")
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    start = patched.index("function _ccProviderKeys()")
    end = patched.index("function SNAP()", start)
    generated = patched[start:end]
    script = (
        'let OLD=["ANTHROPIC_API_KEY"],SEL=[],URLS=[],CREDS=[],SKIP=[],'
        + "MODELS=[],CUSTOM=[];"
        + generated
        + ';for(const [label,value] of [["valid",{ANTHROPIC_API_KEY:"current"}],'
        + '["cleared",{ANTHROPIC_API_KEY:null}],["absent",undefined],'
        + '["null",null],["array",[]],["unknown",{OTHER:"stale"}],'
        + '["wrong-type",{ANTHROPIC_API_KEY:7}]]){try{'
        + '_ccRequireProviderEnv(value);console.log(`${label}:ok`)}catch(error){'
        + 'console.log(`${label}:${error.code}:${error.message}`)}}'
    )

    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stderr
    unavailable = (
        "EPROVIDERENV:Background provider environment transient payload is unavailable; "
        "dispatch again from the current Claude Code session"
    )
    invalid = (
        "EPROVIDERENV:Background provider environment transient payload is invalid; "
        "dispatch again from the current Claude Code session"
    )
    assert proc.stdout.splitlines() == [
        "valid:ok",
        "cleared:ok",
        f"absent:{unavailable}",
        f"null:{unavailable}",
        f"array:{unavailable}",
        f"unknown:{invalid}",
        f"wrong-type:{invalid}",
    ]


@pytest.mark.parametrize(
    ("caller", "call"),
    (
        ("pending-upgrade recovery", "manage(saved.dispatch,0,!0)"),
        ("dispatch-file ingestion", "manage(incoming)"),
        ("exposed internal dispatch callback", "manage(incoming)"),
        ("adopted-worker respawn", 'manage({...incoming,source:"respawn"})'),
    ),
)
def test_background_provider_environment_rejects_manager_callers_without_snapshot(
    caller: str, call: str
) -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    assert call in patched, caller
    manager_start = patched.index(
        "manage=async(job,retry=0,afterUpgrade,_ccProviderEnv)=>{"
    )
    manager_body = patched[manager_start : manager_start + 250]
    assert "_ccProviderEnv=_ccProviderRetain(_ccProviderEnv);" in manager_body, caller


@pytest.mark.parametrize(
    "path",
    (
        "socket primary",
        "socket retry/recovery",
        "claimed spare",
        "cold worker",
    ),
)
def test_background_provider_environment_threads_current_requester_snapshot(
    path: str,
) -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    markers = {
        "socket primary": "providerEnvVersion:3,providerEnv:SNAP()",
        "socket retry/recovery": "providerEnvVersion:3,providerEnv:SNAP()",
        "claimed spare": "W0q(job,spare,spawn,authObj.getAuthSnapshot,_ccProviderEnv)",
        "cold worker": (
            "Worker.spawn(job,spawn,authObj.getAuthSnapshot,afterUpgrade?"
            "{afterUpgrade:afterUpgrade}:void 0,_ccProviderEnv)"
        ),
    }
    minimum_counts = {"socket retry/recovery": 2}
    assert patched.count(markers[path]) >= minimum_counts.get(path, 1)


@pytest.mark.parametrize(
    "worker_path",
    ("claimed spare", "cold worker", "worker respawn"),
)
def test_background_provider_environment_worker_creation_rejects_absent_payload(
    worker_path: str,
) -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    markers = {
        "claimed spare": "Worker.buildClaimFrame(job,snapshot,auth,_ccProviderEnv)",
        "cold worker": 'new Worker(job,spawn??defaultSpawn(),authFn,"cold",void 0,_ccProviderEnv)',
        "worker respawn": "this.socketAuth(),this.providerEnv",
    }
    assert markers[worker_path] in patched
    assert "_ccProviderEnv=_ccProviderRetain(_ccProviderEnv);" in patched


@pytest.mark.parametrize("via", ["cold", "spare", "adopted"])
def test_background_provider_environment_constructor_handles_missing_payload(
    via: str,
) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to evaluate the provider constructor")
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    helpers = patched[
        patched.index("function _ccProviderKeys()") : patched.index("function SNAP()")
    ]
    constructor = patched[
        patched.index("constructor(job,") : patched.index("static spawn(job,")
    ]
    guards = patched[
        patched.index("_ccProviderWarn(){") : patched.index("_ccProviderCancel(){")
    ]
    script = (
        'let OLD=[],SEL=[],URLS=[],CREDS=[],SKIP=[],MODELS=[],CUSTOM=[];'
        + helpers
        + 'class Worker{'
        + constructor
        + guards
        + '};try{let worker=new Worker({launch:{mode:"claude"}},undefined,undefined,'
        + json.dumps(via)
        + ',{pid:123});console.log(JSON.stringify({providerEnv:worker.providerEnv,'
        + 'pid:worker.record.pid,blocked:worker._ccProviderBlocked()}));'
        + 'worker._ccProviderBlocked()}catch(error){console.log(error.code)}'
    )
    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    if via == "adopted":
        assert json.loads(proc.stdout) == {
            "providerEnv": None,
            "pid": 123,
            "blocked": True,
        }
        assert proc.stderr.count("Live session retained") == 1
        assert "automatic respawn disabled" in proc.stderr
        assert (
            'async doSpawn(reattachEnv,afterUpgrade){if(this._ccProviderBlocked())return;'
            in patched
        )
        assert (
            'async respawnIfIdleStale(pinned,trigger="sweep"){if(this._ccProviderBlocked())return{respawned:!1,reason:"provider-unavailable"};'
            in patched
        )
        assert (
            'roster.pendingRespawn==="upgrade"&&!worker._ccProviderBlocked()' in patched
        )
        assert 'fireAuthRekey(){if(this._ccProviderBlocked())return;' in patched
        assert (
            'rekeyForAuthMismatch(reason){if(this._ccProviderBlocked())return;'
            in patched
        )
        assert 'this._ccProviderBlocked()&&this.phase.kind!=="retiring"' in patched
    else:
        assert proc.stdout.strip() == "EPROVIDERENV"
        assert proc.stderr == ""


def test_background_provider_environment_covers_upstream_vertex_region_keys() -> None:
    assert _PROVIDER_ENV_VERTEX_REGION_KEYS == (
        "VERTEX_REGION_CLAUDE_FABLE_5",
        "VERTEX_REGION_CLAUDE_HAIKU_4_5",
        "VERTEX_REGION_CLAUDE_3_5_HAIKU",
        "VERTEX_REGION_CLAUDE_3_5_SONNET",
        "VERTEX_REGION_CLAUDE_3_7_SONNET",
        "VERTEX_REGION_CLAUDE_4_8_OPUS",
        "VERTEX_REGION_CLAUDE_4_7_OPUS",
        "VERTEX_REGION_CLAUDE_4_6_OPUS",
        "VERTEX_REGION_CLAUDE_4_5_OPUS",
        "VERTEX_REGION_CLAUDE_4_1_OPUS",
        "VERTEX_REGION_CLAUDE_4_0_OPUS",
        "VERTEX_REGION_CLAUDE_5_SONNET",
        "VERTEX_REGION_CLAUDE_4_6_SONNET",
        "VERTEX_REGION_CLAUDE_4_5_SONNET",
        "VERTEX_REGION_CLAUDE_4_0_SONNET",
    )


def test_background_provider_environment_final_apply_follows_initializers() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)

    claimed_start = patched.index("async function O09")
    claimed = patched[claimed_start : claimed_start + 900]
    capture = "_ccProviderWorkerEnv=_ccProviderCaptureTransport(claimFrame.env)"
    assert (
        claimed.index(capture)
        < claimed.index("It8(),Da8(),lT6(),Yq8(),Wy$()")
        < claimed.index("_ccProviderApplyWorkerFinal()")
        < claimed.index("await workerMain()")
    )
    assert "_ccProviderSnapshotFromEnv" not in claimed

    builder_start = patched.index("function buildEnv(")
    builder = patched[
        builder_start : patched.index("async function O09", builder_start)
    ]
    payload = "let _ccProviderPayload=_ccProviderRetain(_ccProviderEnv)"
    initial_apply = "Object.entries(_ccProviderPayload)"
    native_scrub = "for(let key of FXq)"
    final_apply = "Object.entries(_ccProviderPayload)"
    assert builder.index(payload) < builder.index(initial_apply)
    assert builder.index(initial_apply) < builder.index(native_scrub)
    assert builder.index(native_scrub) < builder.rindex(final_apply)
    assert builder.rindex(final_apply) < builder.index("return env}")
    assert "_ccProviderSnapshotFromEnv" not in builder

    preaction = patched[
        patched.index('.hook("preAction"') : patched.index("setTitle()")
    ]
    assert preaction.index("await settingsInit()") < preaction.index(
        "_ccProviderApplyWorkerFinal()"
    )

    operational = patched[
        patched.index("if(noninteractive)") : patched.index("operationalStart")
    ]
    assert operational.index("Ko(),CB$(_ccProviderWorkerEnv)") < operational.index(
        "_ccProviderApplyWorkerFinal()"
    )

    delayed_start = patched.index("function CB$")
    delayed = patched[delayed_start : delayed_start + 700]
    assert (
        delayed.index("Ko()")
        < delayed.index("_ccProviderApplyWorkerFinal()")
        < delayed.index("await vLq()")
    )


@pytest.mark.parametrize(
    "worker_path",
    ("cold", "claimed", "respawn", "retry", "adopted-respawn"),
)
def test_background_provider_environment_mutable_worker_paths_reach_final_apply(
    worker_path: str,
) -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    path_markers = {
        "cold": "Worker.spawn(job,spawn,authObj.getAuthSnapshot",
        "claimed": "W0q(job,spare,spawn,authObj.getAuthSnapshot,_ccProviderEnv)",
        "respawn": "this.socketAuth(),this.providerEnv",
        "retry": "manage(job,retry+1,afterUpgrade,_ccProviderEnv)",
        "adopted-respawn": 'manage({...incoming,source:"respawn"})',
    }

    assert path_markers[worker_path] in patched
    assert "let _ccProviderWorkerEnv=_ccProviderCaptureTransport()" in patched
    assert (
        "CLAUDE_CODE_PROVIDER_ENV_TRANSIENT=JSON.stringify(_ccProviderPayload)"
        in patched
    )
    assert "_ccProviderSnapshotFromEnv" not in patched
    assert "Ko(),CB$(_ccProviderWorkerEnv);_ccProviderApplyWorkerFinal()" in patched


def test_background_provider_environment_regression_harness() -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to run the provider environment regression harness")
    harness = Path(__file__).with_name("provider_env_regression.mjs")

    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, str(harness)], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stderr
    assert (
        proc.stdout.strip() == "provider environment transient transport ordering: ok"
    )


def test_background_provider_environment_new_keys_cover_full_lifecycle() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    keys = ("CLAUDE_CODE_CERT_STORE", *_PROVIDER_ENV_VERTEX_REGION_KEYS)

    for key in keys:
        assert patched.count(f'"{key}"') == 1
    assert "out[key]=value===void 0?null:value" in patched
    assert "for(let _ccKey of _ccProviderKeys())delete env[_ccKey]" in patched
    assert "for(let _ccKey of _ccProviderKeys())delete process.env[_ccKey]" in patched
    assert "Object.entries(_ccProviderEnv)" in patched
    assert "if(_ccValue!==null)env[_ccKey]=_ccValue" in patched
    assert (
        "z.record(z.enum(_ccProviderKeys()),z.union([z.string(),z.null()]))" in patched
    )


def test_background_provider_environment_strict_receiver_rejects_unknown_keys() -> None:
    schema = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    assert "z.record(z.enum(_ccProviderKeys())" in schema
    assert "z.record(z.string()" not in schema[schema.index("providerEnvVersion:") :]


def test_background_provider_environment_rejects_protocol_skew() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    assert "req.providerEnvVersion!==3" in patched
    assert "req.providerEnv===void 0" in patched
    assert 'code:"EPROVIDERENV"' in patched
    assert 'reply.code==="EPROVIDERENV"' in patched


def test_background_provider_environment_rejects_old_daemon_success() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    old_daemon_success = (
        'reply={ok:!0,op:"dispatch"};'
        + patched[patched.index('if(reply.ok&&reply.op==="dispatch")') :]
    )
    assert "reply.providerEnvVersion!==3" in old_daemon_success
    assert 'reply={ok:!1,error:"Background provider environment protocol mismatch.' in (
        old_daemon_success
    )
    assert 'code:"EPROVIDERENV"' in old_daemon_success


def test_background_provider_environment_rejects_old_daemon_redispatch() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    redispatch = patched[
        patched.index('if(retryReply.ok&&retryReply.op==="dispatch")') :
    ]

    assert "retryReply.providerEnvVersion!==3" in redispatch
    assert (
        'retryReply={ok:!1,error:"Background provider environment protocol mismatch.'
        in redispatch
    )
    assert 'retryReply.code==="EPROVIDERENV"' in redispatch
    assert (
        'throw Object.assign(Error(retryReply.error),{code:"EPROVIDERENV"})'
        in redispatch
    )
    assert redispatch.index("retryReply.providerEnvVersion!==3") < redispatch.index(
        "return log(),await metric()"
    )


def test_background_provider_environment_required_no_op_fails() -> None:
    with pytest.raises(PatchError, match="background-provider-environment"):
        BACKGROUND_PROVIDER_ENV.apply("unrelated source")


def test_background_provider_environment_rejects_partial_source() -> None:
    source = _PROVIDER_ENV_SRC.replace(
        '"ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN",', '"ANTHROPIC_API_KEY",'
    )
    with pytest.raises(PatchError, match="provider group credentials absent"):
        BACKGROUND_PROVIDER_ENV.apply(source)


def test_background_provider_environment_removes_sanitized_state_schema() -> None:
    source = _PROVIDER_ENV_SRC.replace(
        ".transform(filter).optional(),",
        ".transform((value)=>{let filtered=filter(value);"
        "return filtered&&mapValues(filtered,neutralize)}).optional(),",
    )
    assert source != _PROVIDER_ENV_SRC
    patched = BACKGROUND_PROVIDER_ENV.apply(source)
    assert "providerEnv:z.record(z.string(),z.string())" not in patched
    assert "mapValues(filtered,neutralize)" not in patched


def test_multi_provider_side_query_preserves_retry_callback() -> None:
    native = (
        "let START=performance.now(),R3=await N3.beta.messages.create(REQ3,"
        "{signal:SIG3,...TIME3!==void 0&&{timeout:TIME3}})"
    )
    handler = ".catch((ERROR)=>{RECORD(ERROR);throw ERROR})"
    retry = (
        ",RESULT;try{RESULT=await R3(N3)}catch(ERROR){RESULT=await R3(await REFRESH())}"
    )
    source = _MULTI_PROVIDER_SRC.replace(
        native,
        native.replace("await N3.", "(CLIENT)=>CLIENT.") + handler + retry,
    )
    patched = MULTI_PROVIDER_SDK.apply(source)
    assert (
        "R3=(CLIENT)=>(([_ccClient,_ccOutbound,_ccOutboundOptions])=>"
        "_ccClient.beta.messages.create(_ccOutbound,_ccOutboundOptions))"
        "(_ccMultiProviderRoute(CLIENT,REQ3,"
        "{signal:SIG3,...TIME3!==void 0&&{timeout:TIME3}}))" + handler + retry
    ) in patched


def test_multi_provider_agent_catalogue_accepts_template_description() -> None:
    source = _MULTI_PROVIDER_SRC.replace(
        '"Optional model override for this agent. Takes precedence over frontmatter."',
        "`Optional model override for this agent. Takes precedence over frontmatter.`",
    )
    assert source != _MULTI_PROVIDER_SRC
    patched = MULTI_PROVIDER_SDK.apply(source)
    assert "model:k.enum([...new Set(" in patched
    assert "describe(`Optional model override" in patched


@pytest.mark.parametrize("source_name", ("clientdata", "unknown"))
def test_multi_provider_compaction_source_preserves_known_branch(
    source_name: str,
) -> None:
    source = _MULTI_PROVIDER_SRC.replace(
        'SOURCE==="model-default"',
        f'SOURCE==="{source_name}"||SOURCE==="model-default"',
    )
    if source_name == "unknown":
        with pytest.raises(PatchError, match="mark-provider-compaction-source"):
            MULTI_PROVIDER_SDK.apply(source)
        return
    patched = MULTI_PROVIDER_SDK.apply(source)
    assert (
        'SOURCE==="env"||SOURCE==="settings"||SOURCE==="clientdata"||'
        'SOURCE==="model-default"||(SOURCE==="auto"&&'
        '_ccMultiProviderCatalogInfo(MODEL)!==null)'
    ) in patched


def test_multi_provider_compaction_source_preserves_direct_predicate() -> None:
    source = _MULTI_PROVIDER_SRC.replace(
        'let{source:SOURCE}=RESOLVE(MODEL,SETTING);return SOURCE==="env"||'
        'SOURCE==="settings"||SOURCE==="model-default"',
        'return RESOLVE(MODEL,SETTING).source!=="auto"',
    )
    patched = MULTI_PROVIDER_SDK.apply(source)
    assert (
        'let _ccSource=RESOLVE(MODEL,SETTING).source;return _ccSource!=="auto"||'
        '(_ccSource==="auto"&&_ccMultiProviderCatalogInfo(MODEL)!==null)'
    ) in patched


def test_multi_provider_count_tokens_preserves_native_fallback() -> None:
    body = (
        'if(N(`countTokens API call failed: ${ERROR.message}`),ENABLED())'
        'return FALLBACK(MSGS,TOOLS).catch(()=>null);return null'
    )
    source = _MULTI_PROVIDER_SRC.replace(
        'return N(`countTokens API call failed: ${ERROR.message}`),null', body
    )
    patched = MULTI_PROVIDER_SDK.apply(source)
    assert (
        'if(_ccMultiProviderModelInfo(_ccEffectiveModel))throw ERROR;' + body in patched
    )


@pytest.mark.parametrize("version", [174, 195, 196])
def test_count_tokens_preprocessing_and_response_validation(version: int) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to evaluate countTokens")
    source = _MULTI_PROVIDER_SRC
    prefix = "return WRAP(MSGS,TOOLS,async()=>{"
    if version == 195:
        prefix = "return MSGS=PREPARE(MSGS),WRAP(MSGS,TOOLS,async()=>{"
    elif version == 196:
        prefix = (
            "MSGS=PREPARE(MSGS);let PREPARED=PREPARE_TOOLS(TOOLS);"
            "return WRAP(MSGS,PREPARED,async()=>{"
        )
        source = source.replace(
            "messages:MSGS,tools:TOOLS", "messages:MSGS,tools:PREPARED"
        )
    source = source.replace("return WRAP(MSGS,TOOLS,async()=>{", prefix)
    if version >= 195:
        source = source.replace(
            "return RESULT.input_tokens",
            'if(typeof RESULT.input_tokens!=="number")return null;return RESULT.input_tokens',
        )
    patched = MULTI_PROVIDER_SDK.apply(source)
    assert prefix + "let _ccEffectiveModel;try{" in patched
    tokens = patched[
        patched.index("async function TOKENS(") : patched.index("function ATTR(")
    ]
    validator = patched[
        patched.index("function _ccMultiProviderInputTokens(") : patched.index(
            "function _ccMultiProviderRoute("
        )
    ]
    harness = r'''
const assert=require("node:assert/strict");
let response,request,wrapped,preparations=[];
const RAW=[],ij6=new Set;
function PREPARE(messages){preparations.push("messages");return [...messages,"prepared"]}
function PREPARE_TOOLS(tools){preparations.push("tools");return [...tools,"prepared"]}
function WRAP(messages,tools,callback){wrapped={messages,tools};return callback()}
function DEFAULT(){return "external"}
function KA(model){return model}
function N(){}
function _ccMultiProviderModelInfo(model){return model==="external"?{}:null}
function _ccMultiProviderPreflight(){}
function _ccMultiProviderRoute(client,outbound){request=outbound;return [client,outbound]}
async function LF(){return {beta:{messages:{countTokens:async()=>response}}}}
(async()=>{
    for(const model of ["native","external",undefined]){
        for(const value of [12,"12",null,undefined]){
            response={input_tokens:value};
            if(model!=="native"&&typeof value!=="number"){
                await assert.rejects(()=>TOKENS([],[],model),{code:"EPROVIDERINCOMPATIBLE"});
            }else{
                assert.equal(await TOKENS([],[],model),typeof value==="number"?value:NATIVE_INVALID);
            }
            assert.deepEqual(request.messages,wrapped.messages);
            assert.deepEqual(request.tools,wrapped.tools);
            assert.deepEqual(request.messages,VERSION>=195?["prepared"]:[]);
            assert.deepEqual(request.tools,VERSION>=196?["prepared"]:[]);
        }
    }
    assert.deepEqual(preparations,Array.from({length:12},()=>
        VERSION===196?["messages","tools"]:VERSION===195?["messages"]:[]).flat());
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    harness = harness.replace("NATIVE_INVALID", "null" if version >= 195 else "value")
    harness = harness.replace("VERSION", str(version))
    result = subprocess.run(  # noqa: S603 - local runtime regression
        [runtime, "-e", validator + tokens + harness],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_multi_provider_sdk_transforms_complete_fixture() -> None:
    patched = MULTI_PROVIDER_SDK.apply(_MULTI_PROVIDER_SRC)

    assert "const _ccMultiProviderSDK=()=>SDK" in patched
    assert patched.count("_ccMultiProviderRoute(") == 5
    assert (
        "if(OVERRIDE!==void 0)return OVERRIDE;let _ccProviderModel="
        "_ccMultiProviderCatalogInfo(MODEL);if(_ccProviderModel)return "
        "_ccProviderModel.contextWindow;if(EXTENDED(MODEL,HEADERS))" in patched
    )
    assert "if(NATIVE1M(MODEL))return 1e6" in patched
    assert "if(HEADERS?.includes(BETA.header)&&ELIGIBLE(MODEL))return 1e6" in patched
    assert "if(ENTITLED(MODEL))return 1e6" in patched
    assert (
        "let _ccProviderModel=_ccMultiProviderCatalogInfo(MODEL);if(_ccProviderModel)"
        "UPPER=_ccProviderModel.maxOutputTokens,DEFAULT=Math.min(DEFAULT,UPPER);"
        "return{default:DEFAULT,upperLimit:UPPER}" in patched
    )
    assert "DEFAULT=UPPER=_ccProviderModel.maxOutputTokens" not in patched
    assert (
        'SOURCE==="model-default"||(SOURCE==="auto"&&'
        "_ccMultiProviderCatalogInfo(MODEL)!==null)" in patched
    )
    assert "_ccMultiProviderRoute(N1,_ccRequest,_ccOptions)" in patched
    assert "_ccMultiProviderRoute(N2,_ccRequest,_ccOptions)" in patched
    assert "_ccMultiProviderRoute(N3,_ccRequest,_ccOptions)" in patched
    assert "_ccMultiProviderRoute(CLIENT,_ccRequest,{},!0)" in patched
    assert "MESSAGE.message.model!==MODEL" not in patched
    assert "MESSAGE.message.model!==SYNTHETIC" in patched
    assert (
        "_ccMultiProviderModelProvider(MESSAGE.message.model)!==_ccProvider" in patched
    )
    assert "countTokens(_ccOutbound)" in patched
    assert "model:KA(_ccEffectiveModel)" in patched
    assert "_ccEffectiveModelA(_ccEffectiveModel)" not in patched
    assert (
        "return _ccMultiProviderInputTokens(_ccEffectiveModel,RESULT)}catch(ERROR)"
        in patched
    )
    assert "if(_ccMultiProviderModelInfo(_ccEffectiveModel))throw ERROR" in patched
    assert 'code:"EPROVIDERINCOMPATIBLE"' in patched
    assert "model:KA(REQ1.model)" in patched
    assert "OPTIONS.push(..._ccMultiProviderPickerCatalog())" in patched
    assert "_ccMultiProviderCatalog.find" in patched
    assert "_ccMultiProviderCatalog.filter" in patched
    assert "CC_KIMI_AUTH_TOKEN" in patched
    assert "CC_ZAI_AUTH_TOKEN" in patched
    assert "CC_MINIMAX_AUTH_TOKEN" in patched
    assert "CC_OPENAI_PROXY_AUTH_TOKEN" in patched
    assert "CC_OPENAI_AVAILABLE" in patched
    assert '"baseURL":"http://127.0.0.1:17780"' in patched
    assert '"tokenEnv":"CC_OPENAI_PROXY_AUTH_TOKEN"' in patched
    assert '"availabilityEnv":"CC_OPENAI_AVAILABLE"' in patched
    assert "cc-openai-local" not in patched
    for provider, domain in (
        ("moonshot", "moonshot.ai"),
        ("zai", "z.ai"),
        ("minimax", "minimax.io"),
        ("openai", "openai.com"),
    ):
        assert f'"{provider}":{{"attributionDomain":"{domain}"' in patched
    assert "_ccMultiProviderAttribution(H,_ccNativeAttributionLabel)" in patched
    assert "K=`Co-Authored-By: ${$} <noreply@${_ccAttributionDomain}>`" in patched
    assert (
        "if(_.attribution)return{commit:_.attribution.commit??K,pr:_.attribution.pr??q}"
        in patched
    )
    assert 'if(_.includeCoAuthoredBy===!1)return{commit:"",pr:""}' in patched
    assert (
        '"value":"openai:gpt-6-astra","label":"GPT-6 Astra",'
        '"attributionDomain":"openai.com","description":"OpenAI Codex model"' in patched
    )
    assert "openai:gpt-6-sol" in patched
    assert "openai:gpt-6-luna" in patched
    assert "openai:gpt-5.6-sol" in patched
    assert "openai:gpt-5.6-terra" in patched
    assert "openai:gpt-5.6-luna" in patched
    for priced_model in (
        '"moonshot:kimi-k3":{inputTokens:3,outputTokens:15,promptCacheWriteTokens:3,',
        '"moonshot:kimi-k2.7-code":{inputTokens:0.95,outputTokens:4,',
        '"kimi:kimi-k3":{inputTokens:3,outputTokens:15,promptCacheWriteTokens:3,',
        '"kimi:kimi-k2.7-code":{inputTokens:0.95,outputTokens:4,',
        '"zai:glm-5.3":{inputTokens:1.4,outputTokens:4.4,',
        '"zai:glm-5.3-flash":{inputTokens:0.15,outputTokens:0.5,',
        '"zai:glm-4.7":{inputTokens:0.6,outputTokens:2.2,',
        '"minimax:minimax-m3":{inputTokens:0.3,outputTokens:1.2,',
        '"openai:gpt-6-astra":{inputTokens:10,outputTokens:50,promptCacheWriteTokens:12.5,promptCacheReadTokens:1,',
        '"openai:gpt-6-sol":{inputTokens:2,outputTokens:10,promptCacheWriteTokens:2.5,promptCacheReadTokens:0.2,',
        '"openai:gpt-6-luna":{inputTokens:0.1,outputTokens:0.5,promptCacheWriteTokens:0.125,promptCacheReadTokens:0.01,',
        '"openai:gpt-5.6-sol":{inputTokens:4,outputTokens:20,',
    ):
        assert priced_model in patched
    assert '"kimi-k3":{inputTokens:' not in patched
    assert '"glm-5.3":{inputTokens:' not in patched
    assert "_ccMultiProviderToolAllowed(MODELID,TOOL)" in patched
    assert '_ccTool.name!=="WebSearch"' in patched
    assert "TOOLS.filter((TOOL)=>" in patched
    assert "apiKey:null,authToken:_ccToken,maxRetries:0" in patched
    assert "defaultHeaders:{..._ccInfo.definition.defaultHeaders}" in patched
    assert '_ccMultiProviderDeniedRequestFields=["fallback_credit_token"]' in patched
    assert 'code:"EPROVIDERCREDENTIAL"' in patched
    assert "_ccMultiProviderTraceHeaders.includes(_ccName.toLowerCase())" in patched


def test_multi_provider_sdk_runtime_context_and_output_precedence() -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to evaluate provider limit resolvers")
    patched = MULTI_PROVIDER_SDK.apply(_MULTI_PROVIDER_SRC)
    context_start = patched.index("function TOP_WINDOW(")
    context_end = patched.index("function OUTPUT(", context_start)
    output_start = context_end
    output_end = patched.index("function EXPLICIT(", output_start)
    resolvers = patched[context_start:context_end] + patched[output_start:output_end]
    script = (
        "let debugWindow,anthropicGateCalls=0,CONFIG;"
        "const DEBUG_WINDOW=()=>debugWindow,EXTENDED=()=>{anthropicGateCalls++;return true},"
        "EXTENDED_WINDOW=999999,NATIVE1M=()=>{throw Error('native 1m gate reached')},"
        "ELIGIBLE=()=>true,ENTITLED=()=>true,BETA={header:'beta'},"
        "CUSTOM_WINDOW=()=>null,FALLBACK=200000,NORMALIZE_OUTPUT=()=>\"other\","
        "MODEL_CONFIG=()=>CONFIG;"
        "const _ccMultiProviderCatalogInfo=(model)=>model===\"external\"?"
        "{contextWindow:262144,maxOutputTokens:32768}:null;"
        + resolvers
        + "debugWindow=123456;const debug=TOP_WINDOW('external',[]);"
        + "debugWindow=undefined;const external=TOP_WINDOW('external',[]);"
        + "CONFIG=undefined;const ordinary=OUTPUT('external');"
        + "CONFIG={max_tokens:131072};const configured=OUTPUT('external');"
        + "console.log(JSON.stringify({debug,external,anthropicGateCalls,ordinary,configured}));"
    )

    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == (
        '{"debug":123456,"external":262144,"anthropicGateCalls":0,'
        '"ordinary":{"default":32000,"upperLimit":32768},'
        '"configured":{"default":32000,"upperLimit":32768}}'
    )


@pytest.mark.parametrize(
    "bindings", ["link=LINK(),value=ATTR()", "value=ATTR(),link=LINK()"]
)
@pytest.mark.parametrize("suppress_remote", [False, True])
def test_attribution_wrapper_preserves_native_order(
    bindings: str, suppress_remote: bool
) -> None:
    guard = (
        'if(MODE()==="remote"&&ENV.CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION)'
        'return{commit:"",pr:""};'
        if suppress_remote
        else ""
    )
    wrapper = (
        f"function EFFECTIVE(){{{guard}let {bindings};"
        "return link?APPEND(value,link):value}"
    )
    source = _MULTI_PROVIDER_SRC.replace("}=ATTR();", "}=EFFECTIVE();")
    source = source.replace("function ATTR(){", wrapper + "function ATTR(){")
    patched = MULTI_PROVIDER_SDK.apply(source)
    assert (
        wrapper.replace("EFFECTIVE()", "EFFECTIVE(_ccAttributionModel)").replace(
            "ATTR()", "ATTR(_ccAttributionModel)"
        )
        in patched
    )
    assert "_ccAttributionSnapshot??EFFECTIVE(_ccAttributionModel)" in patched


@pytest.mark.parametrize("hoisted_settings", [False, True])
def test_multi_provider_attribution_runtime_tracks_worker_model_and_settings(
    hoisted_settings: bool,
) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to evaluate provider attribution")
    source = _MULTI_PROVIDER_SRC
    if hoisted_settings:
        source = source.replace(
            '_=SETTINGS();if(_.attribution)return{commit:_.attribution.commit??K,pr:_.attribution.pr??q}',
            '_=SETTINGS(),s=_.attribution;if(s&&(s.commit!==void 0||s.pr!==void 0))return{commit:s.commit??K,pr:s.pr??q}',
        )
    patched = MULTI_PROVIDER_SDK.apply(source)
    helper_end = patched.index("function TOP_WINDOW(")
    attribution_start = patched.index("function ATTR(_ccAttributionModel)")
    attribution_end = patched.index("function PICK(", attribution_start)
    script = (
        patched[patched.index("const _ccMultiProviderDefinitions=") : helper_end]
        + "let settings={};const process={env:{ANTHROPIC_MODEL:'openai:gpt-5.6-sol'}},"
        "FIRST={firstParty:'claude-opus-4-8'},URL='https://claude.com/claude-code',"
        "ENV=process.env,MODE=()=>process.env.MODE??'local',"
        "REMOTE=()=>({commit:'remote',pr:'remote'}),"
        "CURRENT=()=>process.env.ANTHROPIC_MODEL,ISFIRST=()=>false,"
        "ISNATIVE=(model)=>model.startsWith('claude-'),DISPLAY=(model)=>"
        "model==='claude-opus-4-8'?'Claude Opus 4.8':'Claude',SETTINGS=()=>settings;"
        + patched[attribution_start:attribution_end]
        + "const result=[];result.push(ATTR());"
        "process.env.ANTHROPIC_MODEL='moonshot:kimi-k3';result.push(ATTR());"
        "process.env.ANTHROPIC_MODEL='zai:glm-5.3';result.push(ATTR());"
        "process.env.ANTHROPIC_MODEL='minimax:MiniMax-M3';result.push(ATTR());"
        "process.env.ANTHROPIC_MODEL='claude-opus-4-8';result.push(ATTR());"
        "process.env.ANTHROPIC_MODEL='future-native-model';result.push(ATTR());"
        "settings={attribution:{commit:'custom commit',pr:'custom pr'}};result.push(ATTR());"
        "settings={includeCoAuthoredBy:false};result.push(ATTR());"
        "process.env.MODE='remote';settings={};result.push(ATTR());"
        "process.env.CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION='1';result.push(ATTR());"
        "console.log(JSON.stringify(result));"
    )

    proc = subprocess.run(  # noqa: S603 - test intentionally runs the detected JS runtime
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == (
        '[{"commit":"Co-Authored-By: GPT-5.6 Sol <noreply@openai.com>",'
        '"pr":"🤖 Generated with [Claude Code](https://claude.com/claude-code)"},'
        '{"commit":"Co-Authored-By: Kimi K3 <noreply@moonshot.ai>",'
        '"pr":"🤖 Generated with [Claude Code](https://claude.com/claude-code)"},'
        '{"commit":"Co-Authored-By: GLM 5.3 <noreply@z.ai>",'
        '"pr":"🤖 Generated with [Claude Code](https://claude.com/claude-code)"},'
        '{"commit":"Co-Authored-By: MiniMax M3 <noreply@minimax.io>",'
        '"pr":"🤖 Generated with [Claude Code](https://claude.com/claude-code)"},'
        '{"commit":"Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>",'
        '"pr":"🤖 Generated with [Claude Code](https://claude.com/claude-code)"},'
        '{"commit":"Co-Authored-By: Claude <noreply@anthropic.com>",'
        '"pr":"🤖 Generated with [Claude Code](https://claude.com/claude-code)"},'
        '{"commit":"custom commit","pr":"custom pr"},{"commit":"","pr":""},'
        '{"commit":"remote","pr":"remote"},{"commit":"","pr":""}]'
    )


def test_multi_provider_sdk_context_anchors_fail_loudly() -> None:
    for anchor in (
        "if(OVERRIDE!==void 0)return OVERRIDE;if(EXTENDED(MODEL,HEADERS))",
        "if(CONFIG?.max_tokens&&CONFIG.max_tokens>=4096)UPPER=CONFIG.max_tokens,DEFAULT=Math.min(DEFAULT,UPPER);",
        'function EXPLICIT(MODEL,SETTING){let{source:SOURCE}=RESOLVE(MODEL,SETTING);',
    ):
        source = _MULTI_PROVIDER_SRC.replace(anchor, "changed", 1)
        with pytest.raises(PatchError, match="multi-provider-sdk"):
            MULTI_PROVIDER_SDK.apply(source)


@pytest.mark.parametrize(
    "fragment",
    [
        'function COMPACT_GIT(F){if(!GIT())return"";let n="",{commit:r,pr:o}=ATTR();return r+o}',
        'function FULL_GIT(F){if(!GIT())return"";let n="",{commit:r,pr:o}=ATTR();return r+o}',
        'function COMPACT_BASH(F){return COMPACT_GIT(F)}',
        'function BASH_DISPATCH(M,F){if(SHORT(M))return COMPACT_BASH(F);return FULL_GIT(F)}',
        'async prompt({model:M,tools:T}){let F=[];return BASH_DISPATCH(M,F)},isConcurrencySafe',
        'async function SERIALIZE_NATIVE(E,T){let o="",s="",a=o+s+""+("inputJSONSchema"in E&&E.inputJSONSchema?`${E.name}:${HASH(E.inputJSONSchema)}`:E.name),l=CACHE(),c=l.get(a);return c}',
    ],
)
@pytest.mark.parametrize("duplicate", [False, True])
def test_multi_provider_attribution_captures_fail_closed(
    fragment: str, duplicate: bool
) -> None:
    source = _MULTI_PROVIDER_SRC.replace(
        fragment, fragment + fragment if duplicate else "", 1
    )
    with pytest.raises(PatchError):
        MULTI_PROVIDER_SDK.apply(source)


@pytest.mark.parametrize(
    "anchor",
    [
        'ALIASES=["sonnet","opus","haiku","fable","best","sonnet[1m]","opus[1m]","fable[1m]","opusplan"]',
        "FIRST_PARTY=Object.values(TABLE).map((ENTRY)=>ENTRY.firstParty)",
        "process.env.ANTHROPIC_DEFAULT_FABLE_MODEL||MODELS().fable5",
        'function NATIVE_PICKER(FLAG=!1){let SEEN=new Set,ROWS=OPTIONS(FLAG).filter((ROW)=>{if(ROW.value===null)return!0;if(SEEN.has(ROW.value))return LOG(`model options: dropping duplicate row ',
    ],
)
@pytest.mark.parametrize("ambiguous", [False, True], ids=["missing", "ambiguous"])
def test_multi_provider_agent_discovery_fails_closed(
    anchor: str, ambiguous: bool
) -> None:
    assert anchor in _MULTI_PROVIDER_SRC
    source = (
        _MULTI_PROVIDER_SRC + ";" + anchor
        if ambiguous
        else _MULTI_PROVIDER_SRC.replace(anchor, "changed", 1)
    )
    with pytest.raises(PatchError, match="identifier discovery: expected one match"):
        MULTI_PROVIDER_SDK.apply(source)


def test_multi_provider_resume_requires_allowlist_anchor() -> None:
    source = _MULTI_PROVIDER_SRC.replace(
        '!ALLOW(f)?"not_allowed"', '!ALLOW(f)?"changed"'
    )
    with pytest.raises(PatchError, match="multi-provider-sdk"):
        MULTI_PROVIDER_SDK.apply(source)


@pytest.mark.parametrize("default_override", ["", 'OVERRIDE("fable",CATALOG)??'])
@pytest.mark.parametrize("exempt", [False, True])
@pytest.mark.parametrize("allowed", [False, True])
def test_multi_provider_resume_preserves_default_exemption(
    exempt: bool, allowed: bool, default_override: str
) -> None:
    runtime = shutil.which("node")
    if runtime is None:
        pytest.skip("node is not installed")
    source = _MULTI_PROVIDER_SRC.replace(
        '!ALLOW(f)?"not_allowed"', '!EXEMPT(f)&&!ALLOW(f)?"not_allowed"'
    ).replace(
        "process.env.ANTHROPIC_DEFAULT_FABLE_MODEL||MODELS().fable5;",
        "function FABLE(CATALOG=MODELS()){let MODEL="
        + default_override
        + "CATALOG.fable5;return MODEL}",
    )
    patched = MULTI_PROVIDER_SDK.apply(source)
    resume = patched[
        patched.index("let f=_.message.model;") : patched.index("model:k.enum")
    ]
    script = (
        'const _ccMultiProviderCatalog=[{value:"openai:gpt-6-astra"}];'
        'const _ccMultiProviderCanonicalModel=(model)=>model;'
        f"const EXEMPT=()=>{json.dumps(exempt)},ALLOW=()=>{json.dumps(allowed)};"
        f"function resume(_){{{resume}}}"
        'console.log(JSON.stringify(resume({message:{model:"gpt-6-astra"}})));'
    )
    result = subprocess.run(  # noqa: S603 - local runtime regression
        [runtime, "-e", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    restored = json.loads(result.stdout)
    assert restored["model"] == "openai:gpt-6-astra"
    assert restored["kind"] == ("ok" if exempt or allowed else "declined")
    if not exempt and not allowed:
        assert restored["reason"] == "not_allowed"


def test_multi_provider_sdk_required_no_op_fails() -> None:
    with pytest.raises(PatchError, match="multi-provider-sdk"):
        MULTI_PROVIDER_SDK.apply("unrelated source")


@pytest.mark.parametrize(
    "renamed", [False, True], ids=["original", "dollar-identifiers"]
)
def test_multi_provider_sdk_regression_harness(tmp_path: Path, renamed: bool) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to run the multi-provider regression harness")
    harness = Path(__file__).with_name("multi_provider_regression.mjs")
    helper = tmp_path / "multi_provider_helper.js"
    generated = subprocess.run(  # noqa: S603 - current Python interpreter
        [
            sys.executable,
            "-m",
            "ccpatch.cli",
            "generate-multi-provider-helper",
            "-o",
            str(helper),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert generated.returncode == 0, generated.stderr
    bindings = {
        name: f"${index}$" if renamed else name
        for index, name in enumerate(
            (
                "ALIASES",
                "FIRST_PARTY",
                "MODELS",
                "NATIVE_PICKER",
                "k",
                "ALLOW",
                "EFFECTIVE",
                "LF",
                "KA",
                "RAW",
                "ij6",
                "N",
            )
        )
    }
    source = re.sub(
        r"[\w$]+", lambda match: bindings.get(match[0], match[0]), _MULTI_PROVIDER_SRC
    )
    patched = MULTI_PROVIDER_SDK.apply(source)
    snippets = tmp_path / "patched_snippets.json"
    snippets.write_text(
        json.dumps(
            {
                "bindings": bindings,
                "tokens": patched[
                    patched.index("async function TOKENS(") : patched.index(
                        "function ATTR(_ccAttributionModel)"
                    )
                ],
                "resume": patched[
                    patched.index("let f=_.message.model;") : patched.index(
                        f"model:{bindings['k']}.enum"
                    )
                ],
                "agent": patched[
                    patched.index(f"model:{bindings['k']}.enum") : patched.index(
                        "},COST_HELPER"
                    )
                ],
            }
        )
    )
    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, str(harness), str(helper), str(snippets)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "multi-provider SDK routing: ok"


def test_thinking_summaries_ungated_for_noninteractive() -> None:
    src = (
        'n8.type!=="disabled"){if(A.thinkingDisplay==="summarized"||'
        'A.thinkingDisplay==="omitted")n8.display=A.thinkingDisplay;'
        'else if(!F6()&&O78())n8.display="summarized"}rest'
    )
    out = THINKING_SUMMARIES_NONINTERACTIVE.apply(src)
    assert 'else if(O78())n8.display="summarized"' in out  # interactive gate gone
    assert "!F6()&&" not in out
    # the explicit --thinking-display branch is untouched
    assert (
        'A.thinkingDisplay==="summarized"||A.thinkingDisplay==="omitted")'
        "n8.display=A.thinkingDisplay" in out
    )


_THINKING_198_SRC = (
    'function SETTINGS(){return config().showThinkingSummaries??!1}'
    'function DISPLAY({explicitDisplay:e,isNonInteractive:t,outputFormat:n,verbose:r})'
    '{if(e)return e;if(!t)return SETTINGS()?"summarized":void 0;'
    'if(n==="text"||n==="json"&&!r)return"omitted";return}'
    'function AGENT(e,{useExactTools:t,forwardSubagentText:n,isAsync:r,'
    'isNonInteractiveSession:o,sessionDisplayExplicit:s})'
    '{if(s||!o||t||n||r||e.type==="disabled"||e.display==="omitted")return e;'
    'return{...e,display:"omitted"}}'
)


def test_198_variants_are_narrowly_selected() -> None:
    for version in (None, (2, 1, 174), (2, 1, 197)):
        sets = default_patch_sets(version)
        assert sets[4].name == BACKGROUND_PROVIDER_ENV.name
        assert sets[7] is THINKING_SUMMARIES_NONINTERACTIVE
    for version in (
        (2, 1, 198),
        (2, 1, 199),
        (2, 1, 200),
        (2, 1, 201),
        (2, 1, 202),
        (2, 1, 203),
        (2, 1, 204),
        (2, 1, 205),
        (2, 1, 206),
        (2, 1, 207),
        (2, 1, 208),
        (2, 1, 209),
        (2, 1, 210),
        (2, 1, 211),
    ):
        sets = default_patch_sets(version)
        assert sets[4].name == BACKGROUND_PROVIDER_ENV_198.name
        assert sets[7] is THINKING_SUMMARIES_NONINTERACTIVE_198
        assert len(sets) == 9
        assert all(patch_set.applies_to(version) for patch_set in sets)
        for patch_set in (sets[4], sets[7]):
            assert not patch_set.applies_to((2, 1, 197))
            assert not patch_set.applies_to(None)
        assert sets[4].applies_to((2, 1, 274))
        assert not sets[4].applies_to((2, 1, 275))
        assert sets[4].max_version == (2, 1, 275)
        assert sets[7].applies_to((2, 1, 274))
        assert not sets[7].applies_to((2, 1, 275))
        assert sets[7].max_version == (2, 1, 275)
        assert MULTI_PROVIDER_SDK.applies_to((2, 1, 274))
        assert not MULTI_PROVIDER_SDK.applies_to((2, 1, 275))
        assert MULTI_PROVIDER_SDK.max_version == (2, 1, 275)
    sets = default_patch_sets((2, 1, 274))
    assert len(sets) == 11
    assert sets[9] is RETRACTION_ARCHIVE
    assert sets[10] is AUTO_MODE_LOCAL_FALLBACK
    assert all(patch_set.applies_to((2, 1, 274)) for patch_set in sets)
    assert AUTO_MODE_LOCAL_FALLBACK not in default_patch_sets((2, 1, 273))
    assert AUTO_MODE_LOCAL_FALLBACK not in default_patch_sets((2, 1, 275))
    assert AUTO_MODE_LOCAL_FALLBACK not in default_patch_sets(None)


def test_198_provider_respawn_uses_transient_environment() -> None:
    original = '||job.providerEnv,...job.providerEnv&&{providerEnv:job.providerEnv}'
    updated = (
        'try{await fs.access(tombstone(id)),host=!0}catch(error){'
        'if(!missing(error))return{error:`host-managed tombstone unreadable: ${error}`};host=!1}'
        'let{CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:ignored,...env}=job.providerEnv??{};'
        'if(host)for(let key of keys)delete env[key];'
        'let managed=host?{...env,CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:"1"}:env,'
        'options={...intent&&{intent},providerEnv:managed,'
        '...job.sessionPermissionRules&&{sessionPermissionRules:job.sessionPermissionRules}};'
    )
    source = _PROVIDER_ENV_SRC.replace(original, updated).replace(
        'fetchSnapshot(job.short,auth?.())',
        'fetchSnapshot(job.short,hostManaged(job)?void 0:auth?.())',
    )
    assert source != _PROVIDER_ENV_SRC
    patched = BACKGROUND_PROVIDER_ENV_198.apply(source)
    assert 'fs.access(tombstone(id))' in patched
    assert 'host-managed tombstone unreadable:' in patched
    assert 'let options={...intent&&{intent},...job.sessionPermissionRules' in patched
    assert 'job.providerEnv' not in patched
    assert 'providerEnv:managed' not in patched
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    guard = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV_198.patches
        if patch.name == "remove-provider-env-from-respawn-guard"
    )
    options_patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV_198.patches
        if patch.name == "remove-provider-env-from-respawn-options"
    )
    body = PatchSet(name="respawn", patches=(guard, options_patch)).apply(updated)
    script = (
        'async function respawn(hostState){let host;const id="test",intent=null;'
        'const job=new Proxy({},{get(){throw Error("persisted job read")}});'
        'const tombstone=x=>x,missing=e=>e.code==="ENOENT";'
        'const fs={access:async()=>{if(hostState!=="present")'
        'throw Object.assign(Error(hostState),{code:hostState})}};'
        + body.replace(
            '...job.sessionPermissionRules&&{sessionPermissionRules:job.sessionPermissionRules}',
            '',
        )
        + 'return {ok:true}};'
        + '''
(async()=>{
for(const state of ["present","ENOENT","EACCES"])
for(const value of [undefined,"","0","false","1","true","yes","on"," TRUE "]){
    if(value===undefined)delete process.env.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST;
    else process.env.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST=value;
    const result=await respawn(state);
    const trusted=["1","true","yes","on"," TRUE "].includes(value);
    const expected=state==="ENOENT"||state==="present"&&trusted;
    if(!!result.ok!==expected)throw Error(JSON.stringify({state,value,result}));
}
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    )
    result = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert 'fetchSnapshot(job.short,hostManaged(job)?void 0:auth?.())' in patched
    assert (
        'UVA(job,snapshot,worker.socketAuth(),spare.claimAuth,_ccProviderEnv)'
        in patched
    )
    assert 'providerEnvVersion:3,providerEnv:SNAP()' in patched
    with pytest.raises(PatchError, match="respawn-guard"):
        BACKGROUND_PROVIDER_ENV.apply(source)
    with pytest.raises(PatchError, match="respawn-guard"):
        BACKGROUND_PROVIDER_ENV_198.apply(_PROVIDER_ENV_SRC)


@pytest.mark.parametrize(
    ("setting", "config", "display", "agent", "modern_locals"),
    [
        ("SETTINGS", "config", "DISPLAY", "AGENT", False),
        ("XJr", "Rr", "yOi", "_Oi", False),
        ("JXr", "Pr", "yLi", "_Li", False),
        ("N7n", "Ln", "PLs", "MLs", True),
        ("BVn", "On", "MPs", "NPs", True),
    ],
    ids=[
        "synthetic-198",
        "201-linux-x64",
        "201-linux-arm64",
        "202-linux-x64",
        "202-linux-arm64",
    ],
)
def test_198_thinking_defaults_and_subagent_filter(
    setting: str, config: str, display: str, agent: str, modern_locals: bool
) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to check thinking defaults")
    # Use helper names and local bindings from sweep-resume native captures.
    bindings = {
        "SETTINGS": setting,
        "config": config,
        "DISPLAY": display,
        "AGENT": agent,
    }
    if modern_locals:
        bindings.update({"n": "r", "r": "n", "s": "i"})
    source = re.sub(
        r"[\w$]+", lambda match: bindings.get(match[0], match[0]), _THINKING_198_SRC
    )
    patched = THINKING_SUMMARIES_NONINTERACTIVE_198.apply(source)
    script = (
        patched
        + '''
let enabled;
function config(){return {showThinkingSummaries:enabled}}
for(enabled of [undefined,false,true])
for(const explicitDisplay of [undefined,"omitted","summarized"])
for(const isNonInteractive of [false,true])
for(const outputFormat of ["text","json","stream-json"])
for(const verbose of [false,true]){
    const actual=DISPLAY({explicitDisplay,isNonInteractive,outputFormat,verbose});
    const expected=explicitDisplay??(enabled?"summarized":
        isNonInteractive&&(outputFormat==="text"||outputFormat==="json"&&!verbose)
        ?"omitted":undefined);
    if(actual!==expected)throw Error(JSON.stringify({actual,expected}));
    const thinking={type:"enabled",display:actual};
    const options={useExactTools:false,forwardSubagentText:false,isAsync:false,
        isNonInteractiveSession:isNonInteractive,sessionDisplayExplicit:!!explicitDisplay};
    const agent=AGENT(thinking,options);
    const agentExpected=enabled||explicitDisplay||!isNonInteractive||actual==="omitted"
        ?actual:"omitted";
    if(agent.display!==agentExpected)throw Error("subagent display mismatch");
    for(const flag of ["useExactTools","forwardSubagentText","isAsync"]){
        if(AGENT(thinking,{...options,[flag]:true})!==thinking)throw Error(flag);
    }
    const disabled={type:"disabled"};
    if(AGENT(disabled,options)!==disabled)throw Error("disabled thinking changed");
    const omitted={type:"enabled",display:"omitted"};
    if(AGENT(omitted,options)!==omitted)throw Error("omitted thinking changed");
    const explicit={type:"enabled",display:"summarized"};
    if(AGENT(explicit,{...options,sessionDisplayExplicit:true})!==explicit)
        throw Error("explicit display changed");
}
'''
    )
    harness_bindings = {"config": config, "DISPLAY": display, "AGENT": agent}
    script = re.sub(
        r"[\w$]+", lambda match: harness_bindings.get(match[0], match[0]), script
    )
    result = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    with pytest.raises(PatchError, match="ungate-thinking-display-default"):
        THINKING_SUMMARIES_NONINTERACTIVE.apply(_THINKING_198_SRC)
    with pytest.raises(PatchError, match="ungate-thinking-display-default"):
        THINKING_SUMMARIES_NONINTERACTIVE_198.apply(
            'else if(!F6()&&O78())n8.display="summarized"'
        )


def _original_scope_map(var: str = "R9") -> str:
    entries = ",".join(
        f'["{scope}",{var}({r},{g},{b})]' for scope, r, g, b in _SYNTAX_DARK_MAP
    )
    return f"new Map([{entries}])"


def test_catppuccin_recolors_full_map() -> None:
    out = CATPPUCCIN_SYNTAX.apply("prefix=" + _original_scope_map() + ";suffix")
    assert "R9(198,160,246)" in out  # keyword -> Catppuccin mauve
    assert "R9(249,38,114)" not in out  # Monokai pink gone
    assert '["title.function",R9(' in out  # every entry kept (not dropped)


def test_version_gating_skips_below_2_1_151() -> None:
    assert not CHANNELS_ENABLED.applies_to((2, 1, 150))
    assert CHANNELS_ENABLED.applies_to((2, 1, 151))
    assert CATPPUCCIN_SYNTAX.applies_to((2, 1, 170))


@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [
        ("},Xz_=v8H;ej$={", "}"),
        ("},Xz_=v8H;ids=new Set(native);ej$=Object.assign(Object.create(null),{", "})"),
        ("var ej$=Object.assign(Object.create(null),{", "})"),
    ],
)
def test_model_costs_prepends_provider_models(prefix: str, suffix: str) -> None:
    src = prefix + "[sJ(FY6.firstParty)]:Xw6,[sJ(UY6.firstParty)]:V_H" + suffix
    out = PatchSet(
        name="c",
        patches=(
            _model_costs_patch(
                {
                    "gpt-5.5": {
                        "inputTokens": 5,
                        "outputTokens": 30,
                        "promptCacheWriteTokens": 0,
                        "promptCacheReadTokens": 0.5,
                        "webSearchRequests": 0.01,
                    },
                }
            ),
        ),
    ).apply(src)
    assert prefix + '"gpt-5.5":{inputTokens:5,outputTokens:30,' in out
    assert out.endswith(suffix)
    assert "promptCacheWriteTokens:0,promptCacheReadTokens:0.5" in out
    assert ',[sJ(FY6.firstParty)]:Xw6' in out


def test_bound_patch_resolves_native_module_alias() -> None:
    owner = "/$bunfs/root/owner.js"
    consumer = "/$bunfs/root/consumer.js"
    source = (
        f"\n/* ccpatch-module:{owner.encode().hex()} */\n"
        "const native=42;export{native as publicValue};"
        f"\n/* ccpatch-module:{consumer.encode().hex()} */\n"
        'import{publicValue as localValue}from"./owner.js";'
        "const native=99;USE"
    )

    def replacement(match: re.Match[str], bindings: dict[str, str]) -> str:
        return f"consume({bindings['value']})"

    patch = Patch(
        "qualified",
        re.compile("USE"),
        "",
        identifiers=(re.compile(r"const (?P<value>native)=42"),),
        bound_replacement=replacement,
    )
    result = PatchSet("qualified", (patch,)).apply(source)
    assert "consume(localValue)" in result
    assert "consume(native)" not in result


class _CompactFlavor(NamedTuple):
    label: str
    schema_ns: str  # zod-like namespace (N on linux-x64, k on darwin-arm64)
    describe: str  # todo-item schema helper (ZK$ / W9_)
    builder: str  # tool constructor (aK / a9)
    todo_tool: str  # TodoWrite tool var (JZH / M0H)
    registry_fn: str  # tool-registry function (qg / TQ)
    tool0: str  # first tool in the registry array (yh8 / Cv6)
    tool1: str  # second tool (KS8 / AE6)
    verdict: str  # auto-compact verdict var (A / z)


_COMPACT_FLAVORS = (
    _CompactFlavor("linux-x64", "N", "ZK$", "aK", "JZH", "qg", "yh8", "KS8", "A"),
    _CompactFlavor("darwin-arm64", "k", "W9_", "a9", "M0H", "TQ", "Cv6", "AE6", "z"),
)


def _compact_src(f: _CompactFlavor) -> str:
    ns, dsc, bld, tool = f.schema_ns, f.describe, f.builder, f.todo_tool
    return (
        "_x=yH(()=>"
        + ns
        + ".object({oldTodos:"
        + dsc
        + '().describe("The todo list before the update"),newTodos:'
        + dsc
        + '().describe("The todo list after the update")})),'
        + tool
        + "="
        + bld
        + "({name:wk,async description(){return e24},"
        "get inputSchema(){return _x()},async call({todos:H},$){return{data:{}}}});"
        # Preserve the enabled-name collector from captured registry call sites.
        f"function NAMES(){{let tools={f.registry_fn}(),"
        "enabled=tools.map((tool)=>tool.isEnabled());return tools.filter("
        "(tool,index)=>enabled[index]).map((tool)=>tool.name)}"
        "function "
        + f.registry_fn
        + "(){return["
        + f.tool0
        + ","
        + f.tool1
        + ",vD,RB,eb,"
        + tool
        + ",...cS4?[cS4]:[]]}"
        # Real xXf guard chain: the querySource + autocompact-enabled guards, then the
        # source=="auto" skip guard (Ue()&&!ni()&&!X4$($,q)) that returns BEFORE the
        # verdict line -- so the flag must be consumed ahead of it, not at the verdict.
        "function xXf(H,$,q,K,_=0){"
        'if(K==="compact")return!1;if(!aT())return!1;'
        "if(Ue()&&!ni()&&!X4$($,q))return!1;let g=PX(H)-_,"
        + f.verdict
        + "=FuH(g,$,q);return k(`autocompact`),"
        + f.verdict
        + '.level==="compact"||'
        + f.verdict
        + '.level==="blocked"}'
    )


@pytest.mark.parametrize(
    "f", _COMPACT_FLAVORS, ids=[fl.label for fl in _COMPACT_FLAVORS]
)
@pytest.mark.parametrize("model_guard", ["ni()", "ni(normalize($))", ""])
def test_compact_session_applies(f: _CompactFlavor, model_guard: str) -> None:
    middle = f"!{model_guard}&&" if model_guard else ""
    out = COMPACT_SESSION.apply(_compact_src(f).replace("!ni()&&", middle))
    assert (
        'if(K==="compact")return!1;if(!aT())return!1;if(globalThis.__ccPendingCompact)'
        in out
    )
    # tool defined with THIS build's constructor and schema namespace, not a literal
    assert f'globalThis.__ccCompactTool={f.builder}({{name:"compact_session"' in out
    assert f"get inputSchema(){{return {f.schema_ns}.object({{}})}}" in out
    # exposes prompt()/searchHint like every real tool, so API tool serialization
    # (gtf -> H.prompt) doesn't throw `H.prompt is not a function` on every request
    assert '"compact_session",searchHint:"' in out
    assert 'async prompt(){return"Schedule compaction' in out
    # result mapping reads the .data payload the framework passes (map(t.data,id)),
    # i.e. H.message -- not H.data.message (that double-dip threw at call time)
    assert 'content:H.message}' in out
    assert "content:H.data.message" not in out
    # the generic tool-use renderer calls renderToolUseMessage() unconditionally on
    # render; it is not an aK/base default, so omitting it throws `undefined(...)`
    assert "renderToolUseMessage(){return null}" in out
    # registered at the head of the registry array, before its original first tools
    assert (
        f"function {f.registry_fn}(){{return"
        "[...(globalThis.__ccCompactTool?[globalThis.__ccCompactTool]:[]),"
        f"{f.tool0},{f.tool1}," in out
    )
    # consumes the pending-compact flag BEFORE the source=="auto" skip guard, so an
    # explicit compact_session forces compaction even on models that skip proactive
    # autocompact (opus-4-8 1M window, haiku absent from SXf); the untouched guard
    # (and verdict line) still drive the normal token-threshold path afterwards
    assert (
        "if(globalThis.__ccPendingCompact)"
        "return globalThis.__ccPendingCompact=!1,!0;"
        f"if(Ue()&&{middle}!X4$($,q))return!1" in out
    )
    assert f'{f.verdict}.level==="compact"||{f.verdict}.level==="blocked"' in out
    # the TodoWrite build is preserved immediately after the injected tool
    assert f",{f.todo_tool}={f.builder}({{name:wk" in out


def test_compact_session_preserves_native_safety_checks() -> None:
    src = _compact_src(_COMPACT_FLAVORS[0])
    src = src.replace(
        'if(K==="compact")return!1;if(!aT())return!1;',
        "if(EWr(K))return!1;if(QNt(K))return!1;if(!mL())return!1;",
    ).replace("Ue()&&!ni()&&!X4$($,q)", "VSe()&&!X3e($,q)")
    out = COMPACT_SESSION.apply(src)
    start = out.index("function xXf")
    native = src[src.index("function xXf") :]
    patched = out[start:]
    injection = (
        "if(globalThis.__ccPendingCompact)return globalThis.__ccPendingCompact=!1,!0;"
    )
    assert patched.replace(injection, "") == native
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("node or bun is required for safety-check execution")
    script = (
        "let denied=false,failed=false,enabled=true;"
        "const EWr=()=>denied,QNt=()=>failed,mL=()=>enabled,"
        "VSe=()=>true,X3e=()=>false;"
        + patched
        + ";for(const reason of ['denied','failed','disabled','allowed']){"
        "denied=reason==='denied';failed=reason==='failed';"
        "enabled=reason!=='disabled';globalThis.__ccPendingCompact=true;"
        "const allowed=reason==='allowed';"
        "if(xXf([],null,null,null)!==allowed)throw Error(reason);"
        "if(globalThis.__ccPendingCompact===allowed)throw Error('flag '+reason);"
        "}if(xXf([],null,null,null)!==false)throw Error('native threshold guard');"
    )
    result = subprocess.run(  # noqa: S603 - Execute only the fixed safety fixture.
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_compact_session_declaration_list_syntax() -> None:
    f = _COMPACT_FLAVORS[0]
    src = "var " + _compact_src(f)
    out = COMPACT_SESSION.apply(src)
    assert f",{f.todo_tool}=globalThis.__ccCompactTool=" in out
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("node or bun is required for syntax validation")
    result = subprocess.run(  # noqa: S603 - Parse only the fixed compact fixture.
        [runtime, "-e", "new Function(" + json.dumps(out) + ")"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("factory", ["ye", "$object", "z.object"])
@pytest.mark.parametrize("contract", ["legacy", "dual", "factory"])
def test_compact_session_object_factory_alias(factory: str, contract: str) -> None:
    f = _COMPACT_FLAVORS[0]
    src = _compact_src(f).replace(f"{f.schema_ns}.object", factory)
    if contract != "legacy":
        src = src.replace(
            "async call({todos:H},$){return{data:{}}}",
            "create(){return{async call({todos:H},$){return{data:{}}}}}",
        )
    out = COMPACT_SESSION.apply(src)
    assert f"get inputSchema(){{return {factory}({{}})}}" in out
    assert src[: src.index(f",{f.todo_tool}=") + 1] in out
    assert f",{f.todo_tool}={f.builder}({{name:wk" in out
    with pytest.raises(PatchError, match="define-compact-session-tool"):
        COMPACT_SESSION.apply(out)

    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("node or bun is required for tool execution")
    start = out.index("globalThis.__ccCompactTool=")
    tool = out[start : out.index(f",{f.todo_tool}=", start)]
    declaration = (
        "const z={object:objectFactory};"
        if factory == "z.object"
        else f"const {factory}=objectFactory;"
    )
    # Reproduce legacy descriptor copying and the factory probe and call adapter.
    builder = (
        "value=>value"
        if contract == "legacy"
        else "value=>{const create=value.create;"
        "if(value.call)throw Error('mixed contracts');"
        + (
            "const probe=create(new Proxy({},{get:()=>undefined}));"
            "if('permissionCheckFailureDecision' in probe)throw Error('unexpected hook');"
            if contract == "factory"
            else "if(!create)throw Error('missing contract');"
        )
        + "return Object.assign(Object.defineProperties({},"
        "Object.getOwnPropertyDescriptors(value)),{"
        "call:(input,context)=>create.call(value,context).call(input,{}),"
        "validateInput:async(input,context)=>{const tool=create.call(value,context);"
        "return tool.validateInput?tool.validateInput(input,{}):{result:true}},"
        "checkPermissions:async(input,context)=>{const tool=create.call(value,context);"
        "return tool.checkPermissions?tool.checkPermissions(input,{}):"
        "{behavior:'allow',updatedInput:input}}})}"
    )
    script = (
        "function objectFactory(shape){return {parse(value){"
        "if(Object.keys(shape).length)throw Error('nonempty schema');return value}}}"
        + declaration
        + f"const {f.builder}={builder};"
        + tool
        + ";(async()=>{const t=globalThis.__ccCompactTool;"
        "await t.validateInput?.({},{});await t.checkPermissions?.({},{});"
        "if(globalThis.__ccPendingCompact!==undefined||"
        "globalThis.__ccLastSelfCompact!==undefined)throw Error('factory side effects');"
        "t.inputSchema.parse({});"
        "if(!(await t.prompt()).includes('Schedule compaction'))throw Error('prompt');"
        "if(t.renderToolUseMessage()!==null)throw Error('renderer');"
        "const result=await t.call({},{});"
        "if(!globalThis.__ccPendingCompact)throw Error('not scheduled');"
        "if(t.mapToolResultToToolResultBlockParam(result.data,'id').content"
        "!==result.data.message)throw Error('result');"
        "globalThis.__ccPendingCompact=false;await t.call({},{});"
        "if(globalThis.__ccPendingCompact)throw Error('cooldown');"
        "})().catch(error=>{console.error(error);process.exitCode=1});"
    )
    result = subprocess.run(  # noqa: S603 - Execute only the fixed compact fixture.
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("registry", "loader", "tool0", "tool1"),
    [
        ("n3", "CUf", "frr", "xMl"),
        ("n4", "x$m", "mrr", "ANl"),
        ("sq", "O$m", "_rr", "FLl"),
        ("s5", "P2f", "yrr", "FPl"),
    ],
    ids=["linux-x64", "linux-arm64", "darwin-arm64", "darwin-x64"],
)
def test_compact_session_design_tool_registry(
    registry: str, loader: str, tool0: str, tool1: str
) -> None:
    f = _COMPACT_FLAVORS[0]
    native = (
        f"function {registry}(){{let e={loader}();return[{tool0},{tool1},...e?[e]:[]]}}"
    )
    src = _compact_src(f)
    start = src.index(f"function {f.registry_fn}()")
    end = src.index("function xXf", start)
    collector = src[:start].replace(f"tools={f.registry_fn}()", f"tools={registry}()")
    out = COMPACT_SESSION.apply(collector + native + src[end:])
    spread = "...(globalThis.__ccCompactTool?[globalThis.__ccCompactTool]:[]),"
    patched = native.replace("return[", "return[" + spread)
    assert patched in out
    assert not COMPACT_SESSION.verify_absent[1].search(out)
    assert COMPACT_SESSION.verify_absent[1].search(collector + native)
    with pytest.raises(PatchError, match="define-compact-session-tool"):
        COMPACT_SESSION.apply(out)

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for registry execution")
    script = (
        "let calls=0,design=null;"
        f"const {tool0}='first',{tool1}='second';"
        f"function {loader}(){{calls++;return design}}"
        + patched
        + f";const results=[{registry}()];"
        "globalThis.__ccCompactTool='compact';design='design';"
        f"results.push({registry}());"
        "console.log(JSON.stringify({calls,results}));"
    )
    result = subprocess.run(  # noqa: S603 - Execute only the fixed registry fixture.
        [node, "-e", script], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == {
        "calls": 2,
        "results": [["first", "second"], ["compact", "first", "second", "design"]],
    }


# Members the 2.1.170 harness reads/calls UNCONDITIONALLY on a tool object and that
# the aK/base defaults do NOT provide -- so the injected tool must define each or it
# TypeErrors at invoke/render time (which apply()+verify, a static string transform,
# cannot see). Derived from cli.js: gtf reads prompt()/searchHint per request; the
# generic tool-use renderer calls renderToolUseMessage(); plus call, result mapping,
# and the input schema. Each earlier gap here was a shipped runtime bug
# (be69994 prompt, 3300266 mapping, this one renderToolUseMessage).
_COMPACT_TOOL_REQUIRED_MEMBERS = (
    "searchHint:",
    "async description(",
    "async prompt(",
    "get inputSchema(",
    "renderToolUseMessage(",
    "async call(",
    "mapToolResultToToolResultBlockParam(",
)


@pytest.mark.parametrize(
    "f", _COMPACT_FLAVORS, ids=[fl.label for fl in _COMPACT_FLAVORS]
)
def test_compact_session_tool_shape_complete(f: _CompactFlavor) -> None:
    out = COMPACT_SESSION.apply(_compact_src(f))
    start = out.index(f"globalThis.__ccCompactTool={f.builder}(")
    tool = out[start : out.index(f",{f.todo_tool}={f.builder}(", start)]
    for member in _COMPACT_TOOL_REQUIRED_MEMBERS:
        assert member in tool, f"injected compact_session tool missing {member!r}"


@pytest.mark.parametrize(
    "f", _COMPACT_FLAVORS, ids=[fl.label for fl in _COMPACT_FLAVORS]
)
def test_compact_session_idempotent(f: _CompactFlavor) -> None:
    once = COMPACT_SESSION.apply(_compact_src(f))
    # re-match-proofed: on a second apply the define patch matches nothing and the
    # required-match guard raises, so no double-injection is possible.
    with pytest.raises(PatchError, match="define-compact-session-tool"):
        COMPACT_SESSION.apply(once)


_AUTO_MODE_SRC = (
    'function MODE(e){if(!AUTO(e)||!ELIGIBLE())return"off";'
    'if(DEPLOYMENT()==="thirdParty")return OPT_IN()&&!REFUSED()&&!DISABLED()'
    '?"arbiterWithLocalFallback":"off";'
    'if(FLAG()&&!KILL()&&!DISABLED())return"arbiter";return"off"}'
    'function IS_SERVER(e){return e==="arbiter"||e==="arbiterWithLocalFallback"}'
    'function FALLBACK(e,n){return e==="arbiterWithLocalFallback"&&NONE(n)}'
)


def test_auto_mode_first_party_selects_local_fallback() -> None:
    out = AUTO_MODE_LOCAL_FALLBACK.apply(_AUTO_MODE_SRC)
    assert (
        'if(FLAG()&&!KILL()&&!DISABLED())return"arbiterWithLocalFallback";'
        'return"off"}' in out
    )
    # the third-party branch and the mode predicates are untouched
    assert (
        'return OPT_IN()&&!REFUSED()&&!DISABLED()?"arbiterWithLocalFallback":"off";'
        in out
    )
    assert 'e==="arbiter"||e==="arbiterWithLocalFallback"' in out
    assert 'return"arbiter";' not in out
    for pattern in AUTO_MODE_LOCAL_FALLBACK.verify_present:
        assert pattern.search(out)
    for pattern in AUTO_MODE_LOCAL_FALLBACK.verify_absent:
        assert not pattern.search(out)
    with pytest.raises(PatchError):
        AUTO_MODE_LOCAL_FALLBACK.apply(out)
