"""Regression coverage for the 2.1.280 upstream layouts."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.module_runtime import source_modules
from ccpatch.patches import (
    _MULTI_PROVIDER_HELPER,
    AUTO_MODE_LOCAL_FALLBACK,
    BACKGROUND_PROVIDER_ENV_198,
    COMPACT_SESSION,
    MULTI_PROVIDER_SDK,
    PatchError,
    PatchSet,
    _disable_provider_message_threads,
)
from ccpatch.retractions import _RUNTIME, patch_retractions


def _node(tmp_path: Path, source: str) -> None:
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is unavailable')
    script = tmp_path / 'native.mjs'
    script.write_text('import assert from "node:assert/strict";\n' + source)
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def _between(source: str, start: str, end: str) -> str:
    assert source.count(start) == 1
    offset = source.index(start)
    return source[offset : source.index(end, offset + len(start))]


def test_provider_state_validation_wrapper() -> None:
    patch = next(
        p
        for p in BACKGROUND_PROVIDER_ENV_198.patches
        if p.name == 'remove-provider-env-from-state-schema'
    )
    source = 'providerEnv:pe(o(),o()).transform((e)=>{let r=filter(e);return r&&pick(validate(r,"the saved job state (state.json providerEnv)"),keys)}).optional(),next:true'
    assert PatchSet('test', (patch,)).apply(source) == 'next:true'


def test_claimed_worker_preserves_validation_and_initializers(tmp_path: Path) -> None:
    patch = next(
        p
        for p in BACKGROUND_PROVIDER_ENV_198.patches
        if p.name == 'apply-provider-env-after-claimed-initializers'
    )
    source = 'async function enter(claim,main){let{kept:env,dropped:bad}=validate(claim.env);if(Object.assign(process.env,env),process.argv=[process.argv[0],process.argv[1],...claim.argv],reset(),bad.length>0)warn(bad);await initialize(claim.argv),auth();let{main:worker}=await main;await worker()}'
    result = PatchSet('test', (patch,)).apply(source)
    assert 'validate(claim.env)' in result
    _node(
        tmp_path,
        result
        + '''
const events=[],process={env:{},argv:["node","worker"]};let _ccProviderWorkerEnv;
const _ccProviderCaptureTransport=env=>(events.push("capture"),env);
const validate=env=>(events.push("validate"),{kept:env,dropped:[]});
const reset=()=>events.push("reset"),warn=()=>{throw Error("unexpected warning")};
const initialize=async()=>events.push("initialize"),auth=()=>events.push("auth");
const _ccProviderApplyWorkerFinal=()=>events.push("apply");
await enter({env:{KEY:"value"},argv:[]},Promise.resolve({main:async()=>events.push("main")}));
assert.deepEqual(events,["capture","validate","reset","initialize","auth","apply","main"]);
''',
    )


def test_compact_registry_multiple_initializers() -> None:
    patch = next(
        p
        for p in COMPACT_SESSION.patches
        if p.name == 'register-compact-session-in-toollist'
    )
    source = 'function registry(){let design=initDesign(),repl=initRepl();return[first,...repl?[repl]:[]]}register(registry);'
    result = PatchSet('test', (patch,)).apply(source)
    assert (
        'return[...(globalThis.__ccCompactTool?[globalThis.__ccCompactTool]:[]),first,'
        in result
    )


def test_tool_schema_preserves_engine_fields(tmp_path: Path) -> None:
    patch = next(
        p
        for p in MULTI_PROVIDER_SDK.patches
        if p.name == 'allow-web-search-only-for-anthropic-models'
    )
    source = 'schemas=await Promise.all(tools.map((tool)=>serialize(tool,{getToolPermissionContext:ctx.getToolPermissionContext,tools:all,agents:ctx.agents,allowedAgentTypes:ctx.allowedAgentTypes,model:model,enginePlacement:deferred(tool)?"deferred":"listed",querySource:ctx.querySource,withoutToolDescribeHooks:ctx.withoutToolDescribeHooks,recordedDescription:loading(tool)?void 0:ctx.recordedToolDescriptions?.get(tool.name),recordedEntry:loading(tool)?void 0:ctx.recordedToolEntries?.get(tool.name),deferLoading:loading(tool)})));'
    result = PatchSet('test', (patch,)).apply(source)
    _node(
        tmp_path,
        '''
let tools=[{name:"WebSearch"},{name:"Read"}],schemas,all=[...tools,{name:"Write"}];
const ctx={querySource:"sdk",withoutToolDescribeHooks:true},model="openai:gpt-6-sol";
const deferred=()=>true,loading=()=>false;
const _ccMultiProviderToolAllowed=(model,tool)=>tool.name!=="WebSearch";
const serialize=(tool,options)=>({name:tool.name,...options});
'''
        + result
        + '''
assert.deepEqual(tools.map(t=>t.name),["Read"]);
assert.deepEqual(all.map(t=>t.name),["Read","Write"]);
assert.equal(schemas.length,tools.length);
assert.equal(schemas[0].enginePlacement,"deferred");
assert.equal(schemas[0].withoutToolDescribeHooks,true);
assert.equal(schemas[0].querySource,"sdk");
''',
    )


def test_restored_web_search_is_filtered_at_provider_boundary(tmp_path: Path) -> None:
    _node(
        tmp_path,
        _MULTI_PROVIDER_HELPER
        + '''
class SDK{constructor(){this.beta={messages:{}}}}
function _ccMultiProviderSDK(){return SDK}
process.env.CC_OPENAI_PROXY_AUTH_TOKEN="test-token";
process.env.CC_OPENAI_PROXY_EFFECTIVE_URL="http://127.0.0.1:17782";
process.env.CC_OPENAI_AVAILABLE="1";
const native=new SDK(),tools=[{name:"WebSearch",input_schema:{}},{name:"Read",input_schema:{}},{name:"mcp__search__WebSearch",input_schema:{}}];
const request={model:"openai:gpt-6-sol",tools};
const [,body]=_ccMultiProviderRoute(native,request);
assert.deepEqual(body.tools.map(t=>t.name),["Read","mcp__search__WebSearch"]);
assert.equal(request.tools.length,3);
const nativeRequest={model:"claude-opus-5-5",tools};
assert.equal(_ccMultiProviderRoute(native,nativeRequest)[1],nativeRequest);
process.env.CC_KIMI_AUTH_TOKEN="test-token";
const moonshot={model:"moonshot:kimi-k3",tools:[],messages:[{role:"user",content:[{type:"text",text:"hi"},{type:"tool_addition",tool:tools[0]}]}]};
assert.deepEqual(_ccMultiProviderRoute(native,moonshot)[1].tools,[]);
''',
    )


def test_message_thread_guard_is_module_scoped() -> None:
    source = 'function duplicate(e){return e}function duplicate(e){let x=flag("tengu_curious_tower_stateless_models","");return x}'
    result = _disable_provider_message_threads(source)
    assert result.startswith('function duplicate(e){return e}')
    assert result.count('_ccMultiProviderModelProvider(e)') == 1


def test_native_fallback_preserves_verdict_semantics(tmp_path: Path) -> None:
    source = 'function enabled(){return flag("tengu_quiet_lantern",!0)}function fallback(mode,result){if(mode==="arbiter")return enabled()&&result.kind==="none"&&(result.why==="server_no_result"||result.why==="server_unsupported");return false}'
    patched = AUTO_MODE_LOCAL_FALLBACK.apply(source)
    assert (
        patched[patched.index('function fallback') :]
        == source[source.index('function fallback') :]
    )
    with pytest.raises(PatchError):
        AUTO_MODE_LOCAL_FALLBACK.apply(patched)
    _node(
        tmp_path,
        patched
        + '''
const flag=()=>false;
for(const why of ["server_no_result","server_unsupported"])
 assert(fallback("arbiter",{kind:"none",why}));
for(const why of ["server_unavailable_error","server_unrecognized_result","server_stream_ended"])
 assert(!fallback("arbiter",{kind:"none",why}));
assert(!fallback("arbiter",{kind:"per_call",statuses:{}}));
''',
    )


@pytest.fixture(scope='module')
def native() -> tuple[str, str]:
    path = os.environ.get('CCPATCH_280_SOURCE')
    if not path:
        pytest.skip('set CCPATCH_280_SOURCE to pristine 2.1.280 source')
    source = Path(path).read_text()
    assert 'VERSION:"2.1.280"' in source
    return source, patch_retractions(source)


def test_native_auto_mode_retains_backoff_and_refusals(
    native: tuple[str, str], tmp_path: Path
) -> None:
    source, _ = native
    source = next(
        module.source
        for module in source_modules(source)
        if '"tengu_quiet_lantern"' in module.source
    )
    patched = AUTO_MODE_LOCAL_FALLBACK.apply(source)
    for start, end in [
        ('function Tte(', 'function HKn('),
        ('function Dpt(', 'function BKn('),
        ('function Npt(', 'function '),
    ]:
        assert _between(source, start, end) == _between(patched, start, end)
    enabled = _between(patched, 'function wpt()', 'var TKn=')
    decide = _between(patched, 'function Dpt(', 'function BKn(')
    _node(
        tmp_path,
        enabled
        + decide
        + '''
for(const why of ["server_no_result","server_unsupported"])
 assert(Dpt("arbiter",{kind:"none",why},"tool"));
for(const why of ["server_unavailable_error","server_unrecognized_result","server_stream_ended"])
 assert(!Dpt("arbiter",{kind:"none",why},"tool"));
assert(!Dpt("arbiter",{kind:"per_call",statuses:new Map([["tool",{type:"unavailable",reason:"refused"}]])},"tool"));
''',
    )


def test_native_loader_excludes_archives(
    native: tuple[str, str], tmp_path: Path
) -> None:
    source, _ = native
    loader = _between(source, 'function W8t(e){', 'function NCo(')
    _node(
        tmp_path,
        _RUNTIME
        + loader
        + '''
const archive=createRetractionArchive();
const original={type:"assistant",uuid:"old",parentUuid:null,message:{role:"assistant",content:[{type:"text",text:"NEVER_SEND"}]}};
const record=archive.capture("session",original,[original]);
const boundary={type:"system",subtype:"compact_boundary",uuid:"boundary",parentUuid:null,compactMetadata:{}};
const current={type:"user",uuid:"current",parentUuid:"boundary",message:{role:"user",content:"hello"}};
const L9t=x=>x!==null&&typeof x==="object",H0e=()=>false;
const HR=x=>["user","assistant","attachment","system"].includes(x.type);
const $ve=()=>{},WJr=()=>false,ba=x=>x.subtype==="compact_boundary",zQr=()=>false;
const JFt=()=>({admit:()=>true,finish:()=>{}}),i=()=>{};
const loader=W8t(false);
loader.processEntry(record);loader.processEntry(boundary);loader.processEntry(current);
const loaded=loader.finish();
assert(!JSON.stringify([...loaded.messages.values()]).includes("NEVER_SEND"));
assert.equal(loaded.messages.has("old"),false);
assert.equal(loaded.messages.has("current"),true);
''',
    )


def test_native_sdk_eviction(native: tuple[str, str], tmp_path: Path) -> None:
    _, patched = native
    tracker = _between(
        patched,
        'function Ig({conversation:e,messages:r,persistSession:n,storageV5:s})',
        'function Fg(',
    )
    _node(
        tmp_path,
        _RUNTIME
        + tracker
        + '''
const archive=createRetractionArchive(), calls=[];
function Pht(uuid,storage,message,rows,persist=true){archive.capture("s",message,rows);if(persist)calls.push(uuid)}
const i=()=>{},c=x=>x;
for(const persist of [false,true]){
 const message={type:"assistant",uuid:String(persist),message:{content:[{type:"thinking",thinking:"SECRET"}]}};
 const conversation=[message],messages=[message];
 const tracker=Ig({conversation,messages,persistSession:persist});
 tracker.evict(message.uuid);
 assert.equal(conversation.length,0);assert.equal(messages.length,0);
 assert(archive.snapshot("s").entries.some(x=>x.originalUuid===message.uuid));
}
assert.deepEqual(calls,["true"]);
''',
    )


def test_native_ui_capture(native: tuple[str, str], tmp_path: Path) -> None:
    _, patched = native
    tombstone = _between(patched, 'onTombstone:(mt)=>{Pht(', ',onRefusalContinuation:')
    continuation = _between(
        patched, 'for(let Je of Be){let _ccRows=', '}}Pe.setSalvage(null)'
    )
    _node(
        tmp_path,
        _RUNTIME
        + '''
const archive=createRetractionArchive();
let rows=[{type:"user",uuid:"u"},{type:"assistant",uuid:"a",message:{content:[{type:"text",text:"SECRET"}]}}];
const calls=[],De={},Se={};
function Pht(uuid,storage,message,snapshot){archive.capture("s",message,snapshot);calls.push(uuid)}
const rQn=Pht,Be=action=>{rows=rows.filter(row=>row.uuid!==action.uuid)};
const host={stream:{transcriptRetractedUuids:new Set()},_requireHost:()=>({transcript:{getSnapshot:()=>rows}})};
const factory=function(){return {'''
        + tombstone
        + '''}};
factory.call(host).onTombstone(rows[1]);
assert.deepEqual(rows.map(x=>x.uuid),["u"]);
assert.equal(archive.snapshot("s").entries[0].placement.previousUuid,"u");
rows.push({type:"assistant",uuid:"b",message:{content:[{type:"thinking",thinking:"SECRET_THINKING"}]}});
(function(){let Be=["b"],Re=action=>{rows=rows.filter(row=>row.uuid!==action.uuid)};'''
        + continuation
        + '''}).call(host);
assert.equal(rows.length,1);assert.deepEqual(calls,["a","b"]);
assert.equal(archive.snapshot("s").entries.length,2);
''',
    )


def test_native_local_only_writer(native: tuple[str, str], tmp_path: Path) -> None:
    _, patched = native
    mirror = _between(patched, 'fireMirror(e,n){', 'incrementPendingWrites()')
    append = _between(
        patched,
        'async appendEntry(e,n=this.store.getSessionId(),r,s,g,h){',
        'beginTranscriptRelocation()',
    )
    remove = _between(
        patched, 'async removeMessageByUuid(e,n){', 'async performRemoveByUuid('
    )
    policies = _between(patched, 'var eSo=', ',K3t=') + ';'
    _node(
        tmp_path,
        _RUNTIME
        + policies
        + 'class Writer{'
        + mirror
        + append
        + remove
        + '''
store={getSessionId:()=>"s"};sessionFile="file";mirrors=[];pendingEntries=[];
shouldSkipPersistence(){return false}trackWrite(fn){return fn()}
enqueueWrite(path,entry){this.writes.push(entry);this.fireMirror(path,[entry])}
writes=[];
}
const archive=createRetractionArchive();globalThis.__ccpatchRuntime={retractions:archive};
const HR=x=>["assistant","user","system","attachment"].includes(x.type),cRe=()=>false;
const fJr=()=>undefined,dae=()=>undefined;
const row={type:"assistant",uuid:"a",message:{content:[{type:"text",text:"SECRET"}]}};
const record=archive.capture("s",row,[row]);
const writer=new Writer(),mirrored=[];writer.mirrors.push((path,entries)=>mirrored.push(...entries));
await writer.appendEntry(record);
assert.equal(writer.writes.length,1);assert.equal(mirrored.length,0);
await writer.appendEntry(row);await writer.appendEntry(record,undefined,undefined,{remote:true});
assert.equal(writer.writes.length,1);
writer.sessionFile=null;writer.pendingEntries=[{entry:row}];
await writer.removeMessageByUuid("a");await writer.removeMessageByUuid("a");
assert.equal(writer.pendingEntries.length,1);assert.equal(writer.pendingEntries[0].entry.type,"ccpatch-retracted");
writer.pendingEntries=[{entry:row,storageV5:{remote:true}}];await writer.removeMessageByUuid("a",{remote:true});
assert.equal(writer.pendingEntries.length,0);
const pendingRow={type:"assistant",uuid:"late",message:{content:[{type:"text",text:"LATE_SECRET"}]}};
const lateWriter=new Writer();let resolveMessages;
const messages=new Promise(resolve=>{resolveMessages=resolve});lateWriter.store.sessionMessages=()=>messages;
lateWriter.persistToRemote=async()=>{throw Error("retracted message uploaded")};
const inFlight=lateWriter.appendEntry(pendingRow);archive.capture("s",pendingRow,[pendingRow]);
resolveMessages(new Set());await inFlight;assert.equal(lateWriter.writes.length,0);
''',
    )


def test_native_compactor_preserves_archives(
    native: tuple[str, str], tmp_path: Path
) -> None:
    source, _ = native
    policies = _between(source, 'var e7r=', 'var rae=')
    compact = _between(source, 'async function k0e(', 'function v0e(')
    _node(
        tmp_path,
        _RUNTIME
        + policies
        + compact
        + '''
const archive=createRetractionArchive();
const original={type:"assistant",uuid:"old",parentUuid:null,message:{role:"assistant",content:[{type:"text",text:"NEVER_SEND"}]}};
const record=archive.capture("session",original,[original]);
const boundary={type:"system",subtype:"compact_boundary",uuid:"boundary",parentUuid:null,compactMetadata:{}};
const current={type:"user",uuid:"current",parentUuid:"boundary",message:{role:"user",content:"hello"}};
const Go=JSON.parse,Q=JSON.stringify,ZXr=x=>x.subtype==="compact_boundary",i=()=>{};
class sae{finish(){return new Map()}}
const lines=[record,boundary,current].map(JSON.stringify),plan=await k0e(lines);
assert.equal(plan.kind,"plan");const emit=w0e(plan.plan);
assert(lines.flatMap((line,index)=>emit(index,line)).includes(JSON.stringify(record)));
''',
    )


def test_native_v5_purge(native: tuple[str, str], tmp_path: Path) -> None:
    _, patched = native
    remove = _between(patched, 'async performRemoveByUuid(', 'async removeByUuidV5(')
    _node(
        tmp_path,
        'class Writer{'
        + remove
        + '''
store={getSessionId:()=>"s"};async removeByUuidV5(backend,key,uuid){calls.push({backend,key,uuid})}}
const calls=[],Eg=()=>"key",N=()=>true;
const __ccpatchRetractionStorage={replace:()=>{throw Error("archive must not be called")}};
const backend={remote:true};await new Writer().performRemoveByUuid("path","old",backend);
assert.deepEqual(calls,[{backend,key:"key",uuid:"old"}]);
''',
    )
