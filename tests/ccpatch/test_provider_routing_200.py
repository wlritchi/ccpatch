"""Preserve upstream resolution before late extra-body overrides."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import (
    _MULTI_PROVIDER_HELPER,
    _MULTI_PROVIDER_NONSTREAMING,
    _MULTI_PROVIDER_SIDE_QUERY,
    _MULTI_PROVIDER_STREAMING,
    _MULTI_PROVIDER_TOOL_SCHEMA,
    _filter_multi_provider_tool_schemas,
    _route_multi_provider_request,
    _route_multi_provider_side_query,
)

# Capture from the pristine .200 Linux x64 bundle. Do not resolve again after EXTRA_BODY.
_RESOLVER = 'function GX(e){let t=Bd(e),n=t.toLowerCase();if(!Object.hasOwn(Wce,n))return t;let r=Wce[n];if(r===void 0)return t;let o=Abe()[r];if(e9r().state==="refused")return t;let i;try{i=In("policySettings")}catch{return t}let a=i?.availableModels,l=i?.modelOverrides??{},c,u;if(a===void 0)c=Wd()[r],u=c!==o;else{if(!Ya(n,{allowlist:a,overridesMap:l,envFreeAliasResolution:!0}))return t;let p=yRd(l,r);c=p??o,u=p!==void 0}let d=fr();if(u||d!=="foundry"&&Sa[r][d]!==null)return Bd(c);return t}'
_OVERRIDE = 'function yRd(e,t){for(let[n,r]of Object.entries(e))if(Object.hasOwn(Wce,n)&&Wce[n]===t&&r)return r;return}'
_NONSTREAM = 'let y=await u.beta.messages.create(g,{signal:t.signal,timeout:i,...Object.keys(h).length>0&&{headers:h}})'
_LEGACY = 'let y=await u.beta.messages.create({...g,model:Vu(g.model)},{signal:t.signal,timeout:i,...Object.keys(h).length>0&&{headers:h}})'
_STREAM = 'let po=await Ct.beta.messages.create({...Qn,...Bi!==void 0&&{fallback_credit_token:Bi},stream:!0},{signal:o,...Object.keys(no).length>0&&{headers:no}})'


def test_native_200_routing_evidence() -> None:
    root = os.environ.get("CCPATCH_NATIVE_SOURCE_ROOT")
    if root is None:
        pytest.skip("requires pristine .200 CCPATCH_NATIVE_SOURCE_ROOT")
    path = Path(root) / "linux-x64/original.js"
    if Path(root).name != "2.1.200":
        captured = Path(root).parent / "2.1.200/linux-x64/original.js"
        if captured.is_file():
            path = captured
    source = path.read_text()
    if 'VERSION:"2.1.200"' not in source:
        pytest.skip("requires pristine .200 CCPATCH_NATIVE_SOURCE_ROOT")
    assert _RESOLVER + _OVERRIDE in source
    assert _NONSTREAM in source
    assert _STREAM in source
    assert 'let Hr={model:GX(s.model),messages:' in source
    assert '...go,...Mr,...Object.keys(Sr).length>0' in source
    assert 'Mr=Rze(yt),Sr={...Mr.output_config??{}}' in source
    assert 'function Rze(e){let t=process.env.CLAUDE_CODE_EXTRA_BODY' in source


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("explicit_stream", [False, True])
def test_finalized_routing_preserves_resolution(
    tmp_path: Path, streaming: bool, legacy: bool, explicit_stream: bool
) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("requires node or bun")
    source = _STREAM if streaming else _LEGACY if legacy else _NONSTREAM
    if explicit_stream and not streaming and not legacy:
        source = source.replace("create(g,", "create({...g,stream:!1},")
    pattern = _MULTI_PROVIDER_STREAMING if streaming else _MULTI_PROVIDER_NONSTREAMING
    patched, count = pattern.subn(_route_multi_provider_request, source)
    assert count == 1
    script = (
        'const assert=require("node:assert/strict");'
        + _MULTI_PROVIDER_HELPER
        + _RESOLVER
        + _OVERRIDE
        + f"const legacy={json.dumps(legacy and not streaming)};"
        + r"""
let policy, refused, fail, provider, allowedCalls, envCalls;
const Wce={opus:'opus'}, Sa={opus:{firstParty:'native',bedrock:'native',vertex:null,foundry:'native'}};
function Bd(e){return e.replace(/\[(1|2)m\]/gi,'')}
function Vu(e){return Bd(e)}
function Abe(){return {opus:'claude-opus-default'}}
function e9r(){return {state:refused?'refused':'accepted'}}
function In(key){assert.equal(key,'policySettings');if(fail)throw Error('unavailable');return policy}
function Wd(){envCalls++;return {opus:'claude-opus-env'}}
function Ya(name,options){allowedCalls++;assert.equal(name,'opus');assert.equal(options.envFreeAliasResolution,true);assert.equal(options.overridesMap,policy.modelOverrides??options.overridesMap);return options.allowlist.includes(name)}
function fr(){return provider}
class SDK {
  constructor(options){this.options=options;this.beta={messages:{create:async(request,options)=>({request,options,native:false})}}}
}
function _ccMultiProviderSDK(){return SDK}
const native={beta:{messages:{create:async(request,options)=>({request,options,native:true})}}};
process.env.CC_ZAI_AUTH_TOKEN='test-token';
async function send(request){
 const u=native,Ct=native,g=request,Qn=request,t={signal:{}},i=100,h={},o={},no={},Bi='credit';
 """
        + patched
        + (";return po}" if streaming else ";return y}")
        + """
async function check(selected,extra,expected,external=false){
 // Upstream resolves the selected model before it merges the extra body.
 const request={model:GX(selected),max_tokens:64,...extra};
 const result=await send(request);
 assert.equal(result.native,!external);
 assert.equal(result.request.model,expected);
 assert.equal(result.request.max_tokens,extra.max_tokens??64);
 assert.equal(request.model,extra.model??GX(selected));
 if(external)assert.equal(result.request.fallback_credit_token,undefined);
 return result;
}
(async()=>{
 provider='firstParty';policy=undefined;refused=false;fail=false;envCalls=0;allowedCalls=0;
 await check('opus',{},'claude-opus-env');
 assert.ok(envCalls>0);
 await check('zai:glm-5.3',{},'glm-5.3',true);
 await check('opus',{model:'zai:glm-5.3',max_tokens:123},'glm-5.3',true);
 await check('zai:glm-5.3',{model:'claude-sonnet-4-6'},'claude-sonnet-4-6');
 await check('opus',{model:'opus'},'opus');
 await check('opus',{model:'claude-opus-4-8[1m]'},legacy?'claude-opus-4-8':'claude-opus-4-8[1m]');
 // Managed resolution must not read environment-based model overrides.
 policy={availableModels:['opus'],modelOverrides:{opus:'zai:glm-5.3'}};envCalls=0;
 await check('opus',{},'glm-5.3',true);assert.equal(envCalls,0);assert.ok(allowedCalls>0);
 policy={availableModels:[],modelOverrides:{opus:'zai:glm-5.3'}};
 await check('opus',{},'opus');assert.equal(envCalls,0);
 // Refused or unavailable policy leaves the native alias unchanged.
 refused=true;await check('opus',{},'opus');refused=false;
 fail=true;await check('opus',{},'opus');fail=false;
 policy={availableModels:['opus']};
 provider='firstParty';await check('opus',{},'claude-opus-default');
 provider='bedrock';await check('opus',{},'claude-opus-default');
 provider='vertex';await check('opus',{},'opus');
 provider='foundry';await check('opus',{},'opus');
 policy.modelOverrides={opus:'zai:glm-5.3'};
 await check('opus',{},'glm-5.3',true);
 // Late overrides remain literal even when the alias resolver has managed overrides.
 await check('opus',{model:'opus'},'opus');
 // The routing boundary must not add a second policy lookup for finalized requests.
 const previousCalls=allowedCalls;
 await send({model:'zai:glm-5.3'});assert.equal(allowedCalls,previousCalls);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    )
    path = tmp_path / "routing.cjs"
    path.write_text(script)
    result = subprocess.run(  # noqa: S603 - execute the local test fixture
        [runtime, str(path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_side_query_retains_dispatch_header_and_native_catch() -> None:
    source = (
        'return client.beta.messages.create(request,{signal:signal,'
        '...timeout!==void 0&&{timeout:timeout},...dispatch!==null&&{maxRetries:0},'
        '...dispatch!==null&&{headers:{[header]:dispatch}}}).catch(nativeCatch)'
    )
    transformed, count = _MULTI_PROVIDER_SIDE_QUERY.subn(
        _route_multi_provider_side_query, source
    )
    assert count == 1
    assert transformed.endswith(').catch(nativeCatch)')
    assert '_ccMultiProviderRoute(client,request,{signal:signal,' in transformed
    assert '...dispatch!==null&&{headers:{[header]:dispatch}}' in transformed


@pytest.mark.parametrize("query_source", [True, False])
def test_recorded_tool_snapshot_fields_and_indices_are_preserved(
    query_source: bool,
) -> None:
    source = (
        'schemas=await Promise.all(tools.map((tool)=>serialize(tool,{'
        'getToolPermissionContext:ctx.getToolPermissionContext,tools:all,'
        'agents:ctx.agents,allowedAgentTypes:ctx.allowedAgentTypes,model:model,'
        'querySource:ctx.querySource,recordedDescription:deferred(tool)?void 0:'
        'ctx.recordedToolDescriptions?.get(tool.name),recordedEntry:deferred(tool)||'
        'isAgent(tool,Agent)?void 0:ctx.recordedToolEntries?.get(tool.name),'
        'deferLoading:deferred(tool)}))),index=new Map(tools.map((tool,i)=>'
        '[tool.name,{schema:schemas[i]}]));'
    )
    if not query_source:
        source = source.replace('querySource:ctx.querySource,', '')
    transformed, count = _MULTI_PROVIDER_TOOL_SCHEMA.subn(
        _filter_multi_provider_tool_schemas, source
    )
    assert count == 1
    assert transformed == source.replace(
        'Promise.all(tools.map(',
        'Promise.all((tools=tools.filter((tool)=>'
        '_ccMultiProviderToolAllowed(model,tool))).map(',
    )
