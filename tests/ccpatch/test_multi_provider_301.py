"""Regression coverage for the multi-provider anchors changed in 2.1.301."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ccpatch.patches import (
    _MULTI_PROVIDER_CATALOG,
    _MULTI_PROVIDER_HELPER,
    MULTI_PROVIDER_SDK,
    PatchError,
    PatchSet,
    _recognize_provider_window,
)

# Excerpts from the 2.1.301 bundle. Identifiers are as minified upstream.
_COUNT_TOKENS = (
    "async function Y$e(e,n,r,s){if(ce())return null;e=AOt(nHe(e));let h=POt(n);"
    "return J0e(e,h,async()=>{try{let y=kOe(r??et()),S=ote(y),w=COt(e);"
    'if(bd(y)==="bedrock")return Nko({model:kI(y),configuredModel:y,messages:e,'
    "tools:h,betas:S.filter((ye)=>mFt.has(ye)),containsThinking:w});"
    'let K=await OU({maxRetries:1,model:y,source:"count_tokens",agentContext:El(),'
    "credentials:s?.credentials}),V=S.filter((ye)=>mFt.has(ye)),"
    "Q=await K.beta.messages.countTokens({model:kI(y),messages:e.length>0?e:"
    '[{role:"user",content:"foo"}],tools:h,...tMe(V),...w&&{thinking:Z0e(y)}});'
    'if(typeof Q.input_tokens!=="number")return null;return Q.input_tokens}'
    "catch(y){if(t(`countTokens API call failed: ${y instanceof Error?y.message:"
    'String(y)}`,{level:"error"}),Ur()&&y instanceof Pt&&y.status===501)'
    "return Eko(e,n,s?.credentials).catch((S)=>(t(`gateway sample count failed: "
    '${String(S)}`,{level:"error"}),null));return null}})}'
)
_RESUME = (
    "function L(e,r){let o=new Set(rGs.map((d)=>Ke(d))),n=r?xt(r):void 0,"
    "s=n?Mn(n):void 0;for(let d=e.length-1;d>=0;d--){let u=e[d];"
    'if(u?.type!=="assistant"||u.isMeta||typeof u.message?.model!=="string"||'
    "u.message.model===Ci)continue;let m=u.message.model,p=ay(),"
    'h=typeof p==="string"?Ht(p):p;if(TM(h)&&!_Ie(m)&&Ie(h,Ke(m)))'
    'return{kind:"mode_dependent_setting"};let y=zMr(m),S=!(o.has(Ke(m))||_Ie(m)||'
    'y!==void 0&&tHr(y)||Mn(m)===s)?"unknown_family":!lb(m)&&!qr(m)?"not_allowed":'
    'Jee(m)?"retired":void 0;if(S)return{kind:"declined",model:m,reason:S};'
    'return{kind:"ok",model:m}}return{kind:"none"}}'
)
_TOOL_SCHEMAS = (
    "async function q(Vr,s,Q,zt,Us,gr,tn,$n,Kr){let Ko=await Promise.all("
    "Vr.map((Jn)=>w$(Jn,{getToolPermissionContext:Q.getToolPermissionContext,"
    "tools:s,agents:Q.agents,allowedAgentTypes:Q.allowedAgentTypes,model:zt,"
    'enginePlacement:wUe(Jn,Kr)?"deferred":"listed",querySource:Q.querySource,'
    "withoutToolDescribeHooks:Q.withoutToolDescribeHooks,recordedDescription:"
    "gr(Jn)?void 0:Q.recordedToolDescriptions?.get(Jn.name),recordedEntry:"
    "gr(Jn)||wt(Jn,Fa)?void 0:Q.recordedToolEntries?.get(Jn.name),"
    "isRedescribed:Us?.has(Jn.name)||void 0,deferLoading:gr(Jn),"
    "loadedThroughToolSearch:tn&&gr(Jn)&&$n(Jn,Kr)})));return[Vr,s,Ko]}"
)
_WINDOW = (
    "function Hso(e,n){let{contextWindow:s}=e,S=n;"
    "if(Gp()&&!a.CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT&&cqt(e,S))"
    'return{window:s,configured:s,source:"unknown-model"};'
    'return{window:s,configured:s,source:"auto"}}'
    "function cqt(e,n){return!e.optIn1mHonored&&!J1e(e.model)&&!n&&!e.recognized}"
    'function mus(e){if(xe()!=="firstParty")return!1;if(e.contextWindow>=1e6)'
    "return!1;return cqt(e,!1)}"
)


def _patch(name: str) -> PatchSet:
    patches = tuple(p for p in MULTI_PROVIDER_SDK.patches if p.name == name)
    assert len(patches) == 1
    return PatchSet("test", patches)


def _node() -> str:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("node/bun is required to evaluate patched JavaScript")
    return runtime


def _run(script: str) -> None:
    subprocess.run(  # noqa: S603
        [_node(), "-e", script], check=True, capture_output=True, text=True
    )


def test_count_tokens_routes_after_diskless_guard() -> None:
    source = _patch("route-count-tokens").apply(_COUNT_TOKENS)
    source = _patch("surface-count-tokens-provider-errors").apply(source)
    assert source.startswith(
        "async function Y$e(e,n,r,s){if(ce())return null;e=AOt(nHe(e));"
    )
    assert (
        "async()=>{let _ccEffectiveModel;try{let y=_ccEffectiveModel=kOe(r??et())"
        in (source)
    )
    assert "_ccMultiProviderRoute(K,_ccRequest,{},!0)" in source
    assert "{model:kI(_ccEffectiveModel)," in source
    _run(f"new Function({json.dumps(source)});")


def test_count_tokens_rejects_unknown_early_return() -> None:
    source = _COUNT_TOKENS.replace("if(ce())return null;", "if(ce())return 0;")
    with pytest.raises(PatchError, match="surface-count-tokens-provider-errors"):
        _patch("surface-count-tokens-provider-errors").apply(source)


def test_resume_restores_provider_model_before_served_catalog_lookup() -> None:
    source = _patch("restore-provider-qualified-session-model").apply(_RESUME)
    assert "let y=zMr(m),S=!(o.has(Ke(m))||_Ie(m)||y!==void 0&&tHr(y)" in source
    _run(
        'const assert=require("node:assert/strict");'
        + _MULTI_PROVIDER_HELPER
        + "const rGs=[],Ke=x=>x,xt=x=>x,Mn=x=>x,Ci=\"<synthetic>\",ay=()=>undefined,"
        + "Ht=x=>x,TM=()=>false,_Ie=()=>false,Ie=()=>false,tHr=()=>false,"
        + "lb=()=>true,qr=()=>false,Jee=()=>false;let served=0;"
        + "const zMr=()=>{served++;return undefined};"
        + source
        + 'const turn=m=>[{type:"assistant",message:{model:m}}];'
        + 'assert.deepEqual(L(turn("gpt-6-astra")),{kind:"ok",model:"openai:gpt-6-astra"});'
        + "assert.equal(served,0);"
        + 'assert.deepEqual(L(turn("claude-fable-5")),'
        + '{kind:"declined",model:"claude-fable-5",reason:"unknown_family"});'
        + "assert.equal(served,1);"
    )


def test_tool_schemas_drop_web_search_for_provider_models() -> None:
    source = _patch("allow-web-search-only-for-anthropic-models").apply(_TOOL_SCHEMAS)
    assert "(s=s.filter((Jn)=>_ccMultiProviderToolAllowed(zt,Jn))," in source
    assert "Vr=Vr.filter((Jn)=>_ccMultiProviderToolAllowed(zt,Jn))).map(" in source
    _run(
        'const assert=require("node:assert/strict");'
        + _MULTI_PROVIDER_HELPER
        + "const w$=async(t)=>t.name,wUe=()=>false,wt=()=>false,Fa=null;"
        + source
        + "(async()=>{"
        + 'const tools=()=>[{name:"WebSearch"},{name:"Bash"}],Q={};'
        + 'let[kept,pool,schemas]=await q(tools(),tools(),Q,"openai:gpt-6-astra",'
        + "undefined,()=>false,false,()=>false,null);"
        + 'assert.deepEqual(schemas,["Bash"]);assert.equal(kept.length,1);'
        + "assert.equal(pool.length,1);"
        + '[kept,pool,schemas]=await q(tools(),tools(),Q,"claude-opus-5-5",'
        + "undefined,()=>false,false,()=>false,null);"
        + 'assert.deepEqual(schemas,["WebSearch","Bash"]);'
        + "})().catch(e=>{console.error(e);process.exit(1)});"
    )


@pytest.mark.parametrize("patched", [False, True])
def test_unknown_window_helper_recognizes_catalog(patched: bool) -> None:
    source = _recognize_provider_window(_WINDOW) if patched else _WINDOW
    assert (
        "function cqt(e,n){return _ccMultiProviderCatalogInfo(e.model)===null&&"
        "!e.optIn1mHonored" in source
    ) is patched
    rows = (
        f"for(const row of {_MULTI_PROVIDER_CATALOG}){{const info={{model:row.value,"
        "contextWindow:row.contextWindow};"
        + (
            'assert.equal(Hso(info).source,"auto");assert.equal(mus(info),false);}'
            if patched
            else 'assert.equal(Hso(info).source,"unknown-model");}'
        )
    )
    _run(
        'const assert=require("node:assert/strict");'
        + _MULTI_PROVIDER_HELPER
        + 'const a={},Gp=()=>true,xe=()=>"firstParty",J1e=()=>false;'
        + source
        + rows
        + 'const future={model:"openai:future",contextWindow:200000};'
        + 'assert.equal(Hso(future).source,"unknown-model");'
        + "assert.equal(mus(future),true);"
        + 'assert.equal(Hso({model:"claude-fable-5",contextWindow:200000,'
        + 'recognized:true}).source,"auto");'
        + "a.CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=true;"
        + 'assert.equal(Hso(future).source,"auto");'
    )


@pytest.mark.parametrize(
    "source",
    [
        _WINDOW.replace("&&!e.recognized}", "&&!e.recognized&&!e.served}"),
        _WINDOW.replace("function cqt(", "function other("),
        _WINDOW.replace("cqt(e,S))", "cqt(e,S)&&x())"),
        _WINDOW + _WINDOW,
    ],
)
def test_unknown_window_helper_fails_closed_on_drift(source: str) -> None:
    with pytest.raises(PatchError, match="unknown model window guard"):
        _recognize_provider_window(source)
