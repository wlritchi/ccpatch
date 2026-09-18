"""Check the provider output-limit hook across the 2.1.199 registry change."""

import shutil
import subprocess

import pytest

from ccpatch.patches import MULTI_PROVIDER_SDK

_OUTPUT_PATCH = next(
    patch
    for patch in MULTI_PROVIDER_SDK.patches
    if patch.name == "resolve-provider-max-output"
)


@pytest.mark.parametrize("registry", [False, True], ids=["198", "199"])
def test_provider_output_limits_preserve_native_semantics(registry: bool) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("node/bun is required to evaluate output limits")
    declaration = (
        "let t,n,r=normalize(e),o=lookup(r)?.max_output_tokens;"
        if registry
        else "let t,n,r=normalize(e);"
    )
    selection = (
        "if(o)t=o.default,n=o.upper;"
        if registry
        else 'if(r==="claude-fable-5")t=64000,n=128000;'
    )
    source = (
        "function output(e){"
        + declaration
        + selection
        + 'else if(r==="claude-3-opus"||r==="claude-3-haiku")t=4096,n=4096;'
        + 'else if(r==="claude-3-sonnet")t=8192,n=8192;'
        + "else t=32000,n=128000;let s=override(r);"
        + "if(s!==null)t=Math.min(s,n);let i=config(e);"
        + "if(i?.max_tokens&&i.max_tokens>=4096)n=i.max_tokens,t=Math.min(t,n);"
        + "return{default:t,upperLimit:n}}"
    )
    patched, count = _OUTPUT_PATCH.pattern.subn(_OUTPUT_PATCH.replacement, source)
    assert count == 1
    assert (
        patched.replace(
            "let _ccProviderModel=_ccMultiProviderCatalogInfo(e);"
            "if(_ccProviderModel)n=_ccProviderModel.maxOutputTokens,t=Math.min(t,n);",
            "",
        )
        == source
    )
    script = (
        'const assert=require("node:assert/strict");'
        'const normalize=e=>e.replace("alias:","");'
        'const lookup=r=>r==="claude-fable-5"?'
        "{max_output_tokens:{default:64000,upper:128000}}:undefined;"
        "let overrideValue=null,configValue;"
        "const override=()=>overrideValue,config=()=>configValue;"
        'const _ccMultiProviderCatalogInfo=e=>e==="provider:small"?'
        '{maxOutputTokens:16000}:e==="provider:large"?'
        "{maxOutputTokens:200000}:null;"
        + source.replace("function output(", "function native(")
        + patched
        + "for(overrideValue of [null,2048,90000,250000]){"
        + "for(configValue of [undefined,{}, {max_tokens:2048},"
        + "{max_tokens:4096},{max_tokens:100000},{max_tokens:180000}]){"
        + 'for(const model of ["claude-fable-5","alias:claude-fable-5",'
        + '"claude-3-opus","claude-3-haiku","claude-3-sonnet","unknown"]){'
        + "assert.deepEqual(output(model),native(model));}"
        + 'for(const model of ["provider:small","provider:large"]){'
        + "const limit=_ccMultiProviderCatalogInfo(model).maxOutputTokens;"
        + "assert.deepEqual(output(model),"
        + "{default:Math.min(native(model).default,limit),upperLimit:limit});}}}"
    )
    subprocess.run(  # noqa: S603 - runtime is resolved through shutil.which
        [runtime, "-e", script], check=True, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "declaration",
    [
        "let t,n,r=normalize(e),o=lookup(e)?.max_output_tokens;",
        "let t,n,r=normalize(e),o=lookup(r)?.context_window;",
    ],
)
def test_output_limit_registry_match_rejects_unrelated_shapes(declaration: str) -> None:
    source = (
        "function output(e){"
        + declaration
        + "t=32000,n=128000;let i=config(e);"
        + "if(i?.max_tokens&&i.max_tokens>=4096)n=i.max_tokens,t=Math.min(t,n);"
        + "return{default:t,upperLimit:n}}"
    )
    assert _OUTPUT_PATCH.pattern.search(source) is None
