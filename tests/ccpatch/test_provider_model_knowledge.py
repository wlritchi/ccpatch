"""Keep provider metadata visible to the native unknown-model guard."""

import json
import shutil
import subprocess

import pytest

from ccpatch.patches import (
    _MULTI_PROVIDER_CATALOG,
    _MULTI_PROVIDER_HELPER,
    PatchError,
    _recognize_provider_catalog,
    _recognize_provider_window,
)

_REGISTRATION = (
    "register({isKnown:(e)=>known(identity(e,{identity:!0})),"
    "isModelId:(e)=>lookup(e)!==void 0,isClientSpelling:spelling});"
)
_GUARD = "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"


def test_provider_catalog_extends_native_knowledge() -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("node/bun is required to evaluate model knowledge")
    patched = _recognize_provider_catalog(_REGISTRATION)
    script = (
        'const assert=require("node:assert/strict");let knowledge,nativeCalls=0;'
        "const register=e=>knowledge=e,identity=e=>e,lookup=()=>undefined,spelling=()=>false;"
        'const known=e=>{nativeCalls++;return e==="claude-fable-5"};'
        + _MULTI_PROVIDER_HELPER
        + patched
        + f"const catalog={_MULTI_PROVIDER_CATALOG};"
        + "for(const row of catalog)assert.equal(knowledge.isKnown(row.value),true);"
        + 'assert.equal(knowledge.isKnown("kimi:kimi-k3"),true);'
        + "assert.equal(nativeCalls,0);"
        + 'assert.equal(knowledge.isKnown("claude-fable-5"),true);'
        + 'for(const model of ["openai:future","gpt-6-astra","unknown",'
        + '"OPENAI:gpt-6-astra","openai:gpt-6-astra[1m]"])'
        + "assert.equal(knowledge.isKnown(model),false);"
        + 'assert.equal(_ccMultiProviderCatalogInfo("openai:gpt-6-astra").contextWindow,272000);'
        + "assert.equal(nativeCalls,6);"
    )
    subprocess.run(  # noqa: S603
        [runtime, "-e", script], check=True, capture_output=True, text=True
    )


def test_older_build_without_knowledge_registry_is_unchanged() -> None:
    source = "function nativeModel(e){return e}"
    assert _recognize_provider_catalog(source) == source


@pytest.mark.parametrize(
    "registration",
    [
        "",
        _REGISTRATION.replace("isKnown:", "isRecognized:"),
        _REGISTRATION.replace("identity:!0", "identity:!1"),
        _REGISTRATION + _REGISTRATION,
    ],
)
def test_unknown_window_guard_fails_closed_if_registration_changes(
    registration: str,
) -> None:
    source = f"const guard={json.dumps(_GUARD)};{registration}"
    with pytest.raises(PatchError, match="model knowledge registration"):
        _recognize_provider_catalog(source)


def test_provider_registration_is_not_applied_twice() -> None:
    source = f"const guard={json.dumps(_GUARD)};{_REGISTRATION}"
    with pytest.raises(PatchError, match="model knowledge registration"):
        _recognize_provider_catalog(_recognize_provider_catalog(source))


_WINDOW_274 = 'function zv(e,n,r=Gp()){let s=We(e),m=Dp(e,r),h=dXn(e,s),y=h?.declared,S=h!==void 0&&h.believed===h.declared;if(process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW){let U=tZ("CLAUDE_CODE_AUTO_COMPACT_WINDOW",process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW,Z1e,kXe);if(U.status!=="invalid"){let he=Math.max(Z1e,U.effective);return{window:Math.min(m,he),configured:he,source:"env"}}}if(n!==void 0)return{window:Math.min(m,n),configured:n,source:"settings"};let O=OFo(s);if(O.window!==null)return{window:Math.min(m,O.window),configured:O.window,source:"clientdata"};let D=Itt(s);if(D!==void 0)return{window:Math.min(m,D),configured:D,source:"experiment"};if(m<1e6&&(PFo.has(s)||IFo(e,y)||pXn(e,r)))return{window:Math.min(m,sj),configured:sj,source:"model-default"};let B=O.replacesDefault?void 0:AFo(s);if(B!==void 0)return{window:Math.min(m,B),configured:B,source:"model-default"};if(m>=Mtt&&!O.replacesDefault&&Wf()&&iy(e))return{window:m,configured:m,source:"model-default"};if(Wf()&&!a.CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT&&!MFo(e,r)&&!ige(e)&&!S&&!cj(e,s))return{window:m,configured:m,source:"unknown-model"};return{window:m,configured:m,source:"auto"}}'  # codespell:ignore ofo
_NOTICE_274 = 'function yL(h,O,B){let{source:Q,window:X}=zv(h,O,B);if(Q!=="unknown-model")return null;let ee=Kjr(h),ne=a.CLAUDE_CODE_MAX_CONTEXT_TOKENS;if(ee&&ne!==void 0&&ne>0)return null;let se=X<1e6,Se=[];if(!BH()&&se)Se.push("append [1m] to the model name for 1M");if(ee)Se.push("set CLAUDE_CODE_MAX_CONTEXT_TOKENS to its real window");let Ee=Se.length>0?`; if the model accepts ${se?"more":"less"}, ${Se.join(", or ")}`:"";return`${wWt(h)} Until then auto-compact keeps this session within ${er(X)} tokens (the context window it assumes)${Ee}; CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1 restores the previous wait-for-the-API behavior.`}'


@pytest.mark.parametrize("patched", [False, True])
def test_native_window_classification_and_startup_notice(patched: bool) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("node/bun is required to evaluate the startup notice")
    classifier = _recognize_provider_window(_WINDOW_274) if patched else _WINDOW_274
    script = (
        'const assert=require("node:assert/strict");'
        + _MULTI_PROVIDER_HELPER
        + 'const a={},Gp=()=>[],We=e=>e,Dp=e=>_ccMultiProviderCatalogInfo(e)?.contextWindow??200000;'
        + 'const dXn=()=>undefined,OFo=()=>({window:null}),Itt=()=>undefined;'  # codespell:ignore ofo
        + 'const PFo=new Set(),IFo=()=>false,pXn=()=>false,AFo=()=>undefined;'
        + 'const Mtt=1000000,sj=200000,Wf=()=>true,iy=()=>false;'
        + 'const MFo=()=>false,ige=()=>false,cj=e=>e==="claude-fable-5";'
        + 'const Kjr=()=>true,BH=()=>false,wWt=e=>`${e} unknown`,er=String;'
        + classifier
        + _NOTICE_274
        + 'assert.equal(zv("openai:future").source,"unknown-model");'
        + 'assert.match(yL("openai:future"),/auto-compact keeps/);'
        + 'assert.equal(zv("claude-fable-5").source,"auto");'
        + 'assert.equal(yL("claude-fable-5"),null);'
        + 'assert.equal(zv("openai:gpt-6-astra",100000).source,"settings");'
        + 'assert.equal(zv("openai:gpt-6-astra",100000).window,100000);'
        + 'assert.equal(zv("openai:gpt-6-astra").window,272000);'
        + (
            f'for(const row of {_MULTI_PROVIDER_CATALOG})'
            + '{assert.equal(zv(row.value).source,"auto");assert.equal(yL(row.value),null);}'
            if patched
            else 'assert.match(yL("openai:gpt-6-astra"),/auto-compact keeps/);'
        )
        + 'a.CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=true;'
        + 'assert.equal(zv("openai:future").source,"auto");'
    )
    subprocess.run(  # noqa: S603
        [runtime, "-e", script], check=True, capture_output=True, text=True
    )


def test_older_build_without_unknown_window_guard_is_unchanged() -> None:
    assert _recognize_provider_window(_REGISTRATION) == _REGISTRATION


@pytest.mark.parametrize(
    "source",
    [
        _GUARD,
        _WINDOW_274.replace('source:"unknown-model"', 'source:"unknown"'),
        _WINDOW_274 + _WINDOW_274,
    ],
)
def test_unknown_window_guard_fails_closed_on_drift(source: str) -> None:
    with pytest.raises(PatchError, match="unknown model window guard"):
        _recognize_provider_window(source)
