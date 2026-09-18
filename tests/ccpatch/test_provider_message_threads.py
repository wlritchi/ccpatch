"""Provider requests must not depend on Anthropic's server-side thread state."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import (
    _MULTI_PROVIDER_CATALOG,
    _MULTI_PROVIDER_HELPER,
    PatchError,
    _disable_provider_message_threads,
)

# Native 2.1.274 decision, inheritance, and payload construction.
_FIXTURE = {
    'guard': 'function Zrt(e){let n=I("tengu_curious_tower_stateless_models","");if(typeof n!=="string"||n.trim().length===0)return!1;let r={};for(let s of n.split(",")){let m=Vt(s.trim());if(m!==""&&m!==xne)r[m]=!0}return SVe(r,e)!==void 0}',
    'planner': 'function zRn({engine:e,previousMessageId:n,anchorRewritten:r,messages:s,agentId:m,model:h,modelHeldStateless:y,relayHeldStateless:S,classifierHeldStateless:O}){let D=s.length,B=n===void 0?-1:s.findLastIndex((Ln)=>Ln.type==="assistant"&&Ln.message.id===n),U=B>=0?s[B]:void 0,he=U?.type==="assistant"&&U.message.stop_reason!=null&&!nkn().has(U.message.id)&&!rkn().has(U.message.id),ye=e===null?"no_engine_result":null,Ee=null,ve=null,xe=!1,Ie=null,Me=b0e(m,h),De=o2o(s),Le=!1,Be=!1,Ke=!1,ze=!1,ht=!1,st,ft=!1,_t=!1,Rt=0,At=!1,qt,Ot,on=(Ln)=>{if(qt===void 0||qt.ref!==Ln){let ln=w(Ln);qt={ref:Ln,json:ln,hash:nn(ln)}}return qt},Jt=(Ln)=>{if(Ot?.ref!==Ln)Ot={ref:Ln,bytes:w(Ln.filter((ln)=>!YK(ln))).length};return Ot.bytes};function On(Ln,ln=!1){if(Be=!1,Ke=Ln.some((ur)=>ur.type==="api_system"&&(ur.toolAdditions?.length??0)>0),ze=Ke&&!e2o(),Me||y||S||O||Ie!==null||De||ze)return Ee=null,null;if(Ln.some((ur)=>ur.type==="user"&&ur.ephemeral||!ln&&ur.type==="api_system"&&(ur.ephemeral||ur.ephemeralSuffix!==void 0)))return Ee=null,Le=!0,null;let Nn;if(ye!==null||e===null)Nn={thread:{type:"create"},sliceFrom:0,reason:ye??"no_engine_result"};else if(e.decision!=="continue")Nn={thread:{type:"create"},sliceFrom:0,reason:e.reason};else if(n===void 0||B<0)Nn={thread:{type:"create"},sliceFrom:0,reason:"anchor_missing"};else if(B+1>=D)Nn={thread:{type:"create"},sliceFrom:0,reason:"anchor_is_tail"};else if(!he)Nn={thread:{type:"create"},sliceFrom:0,reason:"anchor_incomplete"};else if(r)Nn={thread:{type:"create"},sliceFrom:0,reason:"anchor_rewritten"};else if(Ln!==s||Ln.length!==D)Nn={thread:{type:"create"},sliceFrom:0,reason:"transcript_drift"};else Nn={thread:{type:"continue",previous_message_id:n},sliceFrom:B+1,reason:e.reason};Ee=Nn;let Tn=Ln.at(-1);return Be=ln&&Tn?.type==="api_system"&&(Tn.ephemeral===!0||Tn.ephemeralSuffix!==void 0),Nn}function bn(Ln,ln){ft=!1,_t=!1,Rt=0,st=void 0;try{if(Ln===null)return{system:ln.system,tools:ln.tools};if(!t2o())return MRn(m),{system:ln.system,tools:ln.tools};let{json:Nn,hash:Tn}=on(ln.tools);if(Ln.thread.type!=="continue")return st=Tn,{system:ln.system,tools:ln.tools};let ur=n2o(ln.system),nr=ur.length>0&&ur.length<ln.system.length?ur:ln.system;if(nr!==ln.system)ft=!0,Rt+=Jt(ln.system);if(Ya().threadInheritedFields.storedToolsHashByThread.get(SU(m))===Tn)return _t=!0,Rt+=Nn.length,{system:nr,tools:void 0};return st=Tn,{system:nr,tools:ln.tools}}catch(Nn){return d(Nn),ft=!1,_t=!1,Rt=0,st=void 0,MRn(m),{system:ln.system,tools:ln.tools}}}function En(Ln){if(ye===null)ye=Ln,t(`[tether] forcing create for retry: ${Ln}`)}function Pn(Ln){try{let ln=DRn(Ln);if(ln!==null&&ve===null)ve=ln;if(ln==="unsupported_request"||ln==="other_thread_400"&&Ee?.thread.type==="create"){if(Ie!==null||Ee===null)return;return Ie=Ee.thread.type,JWo(m,h),t(`[tether] ${ln}: resending this turn stateless; this thread stays stateless on this model for the session`,{level:"warn"}),"retry:tether-stateless"}if(Ee?.thread.type!=="continue")return;if(!(Ln instanceof It))return;if(Ln.status!==400&&Ln.status!==404)return;if(Ln.status===404&&ln!=="not_found")return;if(ln===null&&(mU(Ln)||pae(Ln)))return;switch(ln){case"not_found":return xe=!0,En("replay_not_found"),"retry:tether-replay";case"fingerprint_mismatch":if(xe=!0,a2o(Ln,{system:ft,tools:_t}))lr();return En("replay_fingerprint"),"retry:tether-replay";case"already_continued":case"other_thread_400":case null:return xe=!0,En("replay_other_400"),"retry:tether-replay"}}catch(ln){d(ln);return}}function ar(Ln){ORn(nkn(),s2o,Ln)}function ir(Ln,ln,Nn){if(ht)return;if(ht=!0,Le||y||S||O||ze)ORn(rkn(),i2o,Nn);if(Ln.kind==="ok"&&Ee!==null&&st!==void 0)l2o(m,st);try{let Tn=Ln.kind==="error"?DRn(Ln.error)??"error_non_thread":Ln.kind;i("tengu_tether_live_outcome",{requestId:be(ln),sentThreadType:c(Ee?.thread.type??"none"),sourceCategory:c(e?.sourceCategory??"none"),planReason:c(Ee?.reason??"none"),engineDecision:c(e?.decision??"none"),engineReason:c(e?.reason??"none"),firstThreadError:c(ve??"none"),finalOutcome:c(Tn),replayed:xe,droppedFrom:c(Ie??"none"),threadUnsupported:Me,modelHeldStateless:y,relayHeldStateless:S,classifierHeldStateless:O,serverToolHistory:De,toolAdditionHistory:Ke,toolChangeHistory:ze,requestScopedStateless:Le,keptReminderClearAt:Be,keptReminderScope:c($Rn(h)),deltaMessageCount:B>=0?D-B-1:0,messageCount:D,turnsInThread:e?.turnsInThread??0,omittedSystem:ft,omittedTools:_t,omittedBytes:Rt,inheritBreakerTripped:At})}catch(Tn){d(Tn)}}function lr(){let Ln=Ya().threadInheritedFields;if(Ln.breakerTripped)return;Ln.breakerTripped=!0,At=!0,t("[tether] a continue that left system/tools to the thread was refused (fingerprint); sending full fields for the rest of the session",{level:"warn"}),g("tether_inherited_fields","fingerprint_mismatch")}return{plan:On,shapeInheritedFields:bn,forceCreate:En,resolveRetry:Pn,noteClientTruncated:ar,settle:ir}}',  # codespell:ignore ot
    'engine': 'function d2o(){let e=new Map;function n(s){let m=kG(s.querySource);if(m===null)return null;let h=SU(s.agentId),y=m==="main"?FTn():[],S={model:AH(s.model),system:AH(s.system.filter((Ie)=>!YK(Ie))),tools:AH(s.toolSchemas),betas:AH([...s.betas].sort()),latchedHeaders:AH(s.latchedHeaders),thinking:AH(s.thinkingConfig),toolChoice:AH(s.toolChoice??null),effort:AH(s.effortValue??null),extraBody:AH(s.extraBodyParams??null)},O=s.getMessageHashes(),D=e.get(h),B=D?.messageHashes.length??0,U=D?c2o.filter((Ie)=>S[Ie]!==D.configHashes[Ie]):[],he,ye=-1;if(!D)he="first_request";else if(s.previousMessageId===void 0)he="no_continue_pointer";else if(U.length>0)he="config_changed";else if(ye=D.messageHashes.findIndex((Ie,Me)=>Ie!==aft&&Me<O.length&&O[Me]!==Ie),ye!==-1)he="history_reshaped";else if(O.length<=D.messageHashes.length)he="history_rewound";else he="append";let Ee=he==="append"?"continue":"create",ve=Ee==="continue"&&D?D.turnsInThread+1:1,xe=!1;if(e.has(h))e.delete(h);else if(e.size>=u2o){for(let Ie of e.keys())if(Ie!==PRn){e.delete(Ie),xe=!0;break}}return e.set(h,{configHashes:S,messageHashes:O,turnsInThread:ve}),{decision:Ee,reason:he,sourceCategory:m,evictedOther:xe,changedConfigFields:U,turnsInThread:ve,messageCount:O.length,prevMessageCount:B,firstChangedIndex:ye,deltaMessageCount:Ee==="continue"?O.length-B:0,historyEditClaims:D?y:[]}}function r(s){e.delete(SU(s))}return{evaluate:n,forget:r}}',
    'builder': 'let qu=rgn(ud,h.model),Dy=Ms!==null?Ms.shapeInheritedFields(zu,{system:Gl,tools:qu}):{system:Gl,tools:qu};Hm=Gl,xm=qu,f_=Dy.system!==Gl;let Cg={model:ZI(h.model),messages:zu!==null&&zu.sliceFrom>0?Xy.slice(Yp[zu.sliceFrom]):Xy,system:Dy.system,...Dy.tools!==void 0&&{tools:Dy.tools},tool_choice:$T,...Ka&&{betas:PC(Pd)},metadata:K8({agentContext:h.agentContext}),max_tokens:sN,thinking:Yg,...B_!==void 0&&{temperature:B_},...BC&&Td&&hs.includes(rYe)&&{context_management:BC},...UQ&&of!==void 0&&{safeguards:[{type:Lrt,classifier_context:of}]},...!Oi&&S1?S1:{},...!Oi&&dN?dN:{},...Kp,...hc,...{},...Object.keys(Wc).length>0&&{output_config:Wc},...uN!==void 0&&{speed:uN},...zu!==null&&{thread:zu.thread},..._u&&ye&&Td&&!Oi?{diagnostics:{previous_message_id:U??null}}:{}}',
}


@pytest.mark.parametrize("patched", [False, True])
def test_native_two_turn_requests_keep_provider_history(
    tmp_path: Path, patched: bool
) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("node/bun is required to evaluate native thread requests")
    guard = _FIXTURE["guard"]
    if patched:
        guard = _disable_provider_message_threads(guard)
    script = (
        'const assert=require("node:assert/strict");'
        + _MULTI_PROVIDER_HELPER
        + guard
        + _FIXTURE["engine"]
        + _FIXTURE["planner"]
        + f"const patched={json.dumps(patched)},catalog={_MULTI_PROVIDER_CATALOG};"
        + r"""
let statelessModels='',state;
const I=()=>statelessModels,Vt=e=>e,xne='default';
const SVe=(map,model)=>Object.hasOwn(map,model)?[model,map[model]]:undefined;
const w=JSON.stringify,nn=e=>e,AH=JSON.stringify;
const YK=block=>block.text.startsWith('Tokens remaining:');
const kG=()=> 'main',SU=id=>id??'root',FTn=()=>[];
const c2o=['model','system','tools','betas','latchedHeaders','thinking','toolChoice','effort','extraBody'];
const u2o=128,PRn='root',aft='*';
const nkn=()=>new Set(),rkn=()=>new Set(),b0e=()=>false,o2o=()=>false;
const e2o=()=>true,t2o=()=>true,n2o=system=>system.filter(YK),Ya=()=>state;
const MRn=id=>state.threadInheritedFields.storedToolsHashByThread.delete(SU(id));
const l2o=(id,hash)=>state.threadInheritedFields.storedToolsHashByThread.set(SU(id),hash);
const s2o=256,i2o=256,ORn=()=>{},i=()=>{},be=e=>e,c=e=>e,$Rn=()=> 'off';
const d=error=>{throw error};
const rgn=tools=>tools,ZI=model=>model,K8=()=>({}),PC=betas=>betas;
class SDK {
  constructor(){this.beta={messages:{create:async body=>JSON.parse(JSON.stringify(body))}}}
}
const native=new SDK();
function _ccMultiProviderSDK(){return SDK}
for(const definition of Object.values(_ccMultiProviderDefinitions)){
  process.env[definition.tokenEnv]='test-token';
  if(definition.baseURLEnv)process.env[definition.baseURLEnv]='http://127.0.0.1:17782';
  if(definition.availabilityEnv)process.env[definition.availabilityEnv]='1';
}
async function check(model){
  state={threadInheritedFields:{storedToolsHashByThread:new Map()}};
  const engine=d2o();
  const tools=[{name:'Read',input_schema:{type:'object'}}];
  const baseSystem=[{type:'text',text:'Remember the original system instructions.'}];
  const first={type:'user',message:{role:'user',content:[{type:'text',text:'The secret is plum.'}]}};
  const assistant={type:'assistant',message:{id:'msg_first',model,role:'assistant',stop_reason:'end_turn',content:[{type:'text',text:'Understood.'}]}};
  const second={type:'user',message:{role:'user',content:[{type:'text',text:'What is the secret?'}]}};
  async function send(messages,previousMessageId){
    const system=[...baseSystem,{type:'text',text:`Tokens remaining: ${previousMessageId?900:1000}`}];
    const held=Zrt(model);
    const decision=engine.evaluate({querySource:'repl_main_thread',model,system,toolSchemas:tools,betas:[],latchedHeaders:{},thinkingConfig:null,getMessageHashes:()=>messages.map(AH),previousMessageId});
    const Ms=zRn({engine:decision,previousMessageId,anchorRewritten:false,messages,model,modelHeldStateless:held,relayHeldStateless:false,classifierHeldStateless:false});
    const zu=Ms.plan(messages),h={model},ud=tools,Gl=system,Xy=messages.map(m=>m.message),Yp=messages.map((_,index)=>index);
    let Hm,xm,f_;
    const $T=undefined,Ka=false,Pd=[],sN=64,Yg=undefined,B_=undefined,BC=false,Td=false,hs=[],rYe=null,UQ=false,of=undefined,Oi=false,S1=null,dN=null,Kp={},hc={},Wc={},uN=undefined,_u=false,ye=false,U=previousMessageId;
"""
        + _FIXTURE["builder"]
        + r""";
    const [client,body,options]=_ccMultiProviderRoute(native,Cg);
    const result=await client.beta.messages.create(body,options);
    Ms.settle({kind:'ok'},'req_test','msg_first');
    return result;
  }
  const before=await send([first]);
  const after=await send([first,assistant,second],'msg_first');
  assert.equal(before.messages.length,1);
  assert.equal(before.system.length,2);
  const external=_ccMultiProviderModelProvider(model)!=='anthropic';
  if(patched&&external){
    assert.deepEqual(after.messages,[first.message,assistant.message,second.message]);
    assert.deepEqual(after.system,[...baseSystem,{type:'text',text:'Tokens remaining: 900'}]);
    assert.deepEqual(after.tools,tools);
    assert.equal(before.thread,undefined);
    assert.equal(after.thread,undefined);
  }else{
    assert.deepEqual(after.messages,[second.message]);
    assert.deepEqual(after.system,[{type:'text',text:'Tokens remaining: 900'}]);
    assert.equal(after.tools,undefined);
    assert.deepEqual(after.thread,{type:'continue',previous_message_id:'msg_first'});
  }
}
(async()=>{
  for(const row of catalog)await check(row.value);
  await check('kimi:kimi-k3');
  await check('claude-fable-5');
  statelessModels='claude-native-stateless';
  assert.equal(Zrt('claude-native-stateless'),true);
  assert.equal(Zrt('claude-fable-5'),false);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    )
    path = tmp_path / "threads.cjs"
    path.write_text(script)
    result = subprocess.run(  # noqa: S603
        [runtime, str(path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_native_thread_gate_rejects_custom_endpoints() -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("node/bun is required to evaluate native endpoint eligibility")
    native_gate = 'function ND(){if(!sy())return!1;if(He()!=="firstParty"||!dw())return!1;let e=Hs();return e.tetherLiveGate??=zn.CLAUDE_CODE_TETHER_LIVE??I("tengu_curious_tower",!1),e.tetherLiveGate}'  # codespell:ignore nd
    native_endpoint = 'function dw(){let e=process.env.ANTHROPIC_BASE_URL;if(!e)return!0;return av(e)}function av(e){try{let t=new URL(e).host;return["api.anthropic.com"].includes(t)}catch{return!1}}'
    script = (
        'const assert=require("node:assert/strict");'
        + native_gate
        + native_endpoint
        + r"""
let state,firstPartyBetas=true,provider='firstParty',flag=true,zn={};
const sy=()=>firstPartyBetas,He=()=>provider,Hs=()=>state;
const I=(name,fallback)=>{assert.equal(name,'tengu_curious_tower');assert.equal(fallback,false);return flag};
function enabled(url){
  state={};
  if(url===undefined)delete process.env.ANTHROPIC_BASE_URL;
  else process.env.ANTHROPIC_BASE_URL=url;
  return ND(); // codespell:ignore nd
}
for(const url of [undefined,'','https://api.anthropic.com','https://api.anthropic.com/']){
  assert.equal(enabled(url),true);
}
for(const url of ['http://127.0.0.1:17781','https://proxy.example/v1','https://api.anthropic.com.evil.test','not-a-url']){
  assert.equal(enabled(url),false);
  zn.CLAUDE_CODE_TETHER_LIVE=true;
  assert.equal(enabled(url),false);
  delete zn.CLAUDE_CODE_TETHER_LIVE;
}
provider='bedrock';assert.equal(enabled(),false);provider='firstParty';
firstPartyBetas=false;assert.equal(enabled(),false);firstPartyBetas=true;
flag=false;assert.equal(enabled(),false);
zn.CLAUDE_CODE_TETHER_LIVE=true;assert.equal(enabled(),true);
flag=true;zn.CLAUDE_CODE_TETHER_LIVE=false;assert.equal(enabled(),false);
"""
    )
    result = subprocess.run(  # noqa: S603
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_older_build_without_message_threads_is_unchanged() -> None:
    source = "function send(request){return request}"
    assert _disable_provider_message_threads(source) == source


@pytest.mark.parametrize(
    "source",
    [
        '"CLAUDE_CODE_TETHER_LIVE";',
        _FIXTURE["guard"].replace('stateless_models",""', 'stateless_models",[]')
        + '"CLAUDE_CODE_TETHER_LIVE";',
        _FIXTURE["guard"] * 2,
    ],
)
def test_message_thread_guard_fails_closed_on_drift(source: str) -> None:
    with pytest.raises(PatchError, match="message threads model guard"):
        _disable_provider_message_threads(source)


def test_message_thread_guard_is_not_applied_twice() -> None:
    source = _FIXTURE["guard"] + '"CLAUDE_CODE_TETHER_LIVE";'
    with pytest.raises(PatchError, match="message threads model guard"):
        _disable_provider_message_threads(_disable_provider_message_threads(source))
