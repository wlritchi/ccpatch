"""Exercise isolated numbered Anthropic OAuth clients and native model metadata."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import (
    _MULTI_PROVIDER_HELPER,
    _MULTI_PROVIDER_SDK_TAIL,
    _install_anthropic_accounts,
    _replace_multi_provider_sdk_tail,
)

_CATALOG = {
    "models": [
        {
            "id": "claude-fable-5-1",
            "display_name": "Fable 5.1",
            "provider_ids": {"first_party": "claude-fable-5-1"},
            "context": {"window": 1000000},
            "max_output_tokens": {"default": 64000, "upper": 128000},
        },
        {
            "id": "claude-haiku-4-5",
            "display_name": "Haiku 4.5",
            "provider_ids": {"first_party": "claude-haiku-4-5-20251001"},
            "context": {"window": 200000, "supports_1m_suffix": True},
            "max_output_tokens": {"default": 32000, "upper": 64000},
        },
    ],
    "aliases": {"fable": {"default": "claude-fable-5-1"}},
}


def test_numbered_anthropic_accounts(tmp_path: Path) -> None:
    runtime = shutil.which("node")
    if runtime is None:
        pytest.skip("requires node")
    helper = _MULTI_PROVIDER_HELPER.replace(
        "const _ccMultiProviderAnthropicCatalog = { models: [], aliases: {} };",
        "const _ccMultiProviderAnthropicCatalog = " + json.dumps(_CATALOG) + ";",
    )
    path = tmp_path / "helper.js"
    path.write_text(helper)
    subprocess.run(  # noqa: S603
        [
            runtime,
            str(Path(__file__).with_name("anthropic_accounts_regression.mjs")),
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_account_factory_isolation(tmp_path: Path) -> None:
    runtime = shutil.which("node")
    if runtime is None:
        pytest.skip("requires node")
    catalog = (
        '{"//":"Hand-maintained baked-in model catalog",' + json.dumps(_CATALOG)[1:]
    )
    source = (
        f"var CATALOG={catalog};"
        'async function factory({model:m,fetchOverride:f}){'
        'let h={"x-app":"cli","User-Agent":ua()},o={fetchOptions:transport({forAnthropicAPI:!0,model:m})};'
        'throw Error("primary authentication invoked");}'
        'const _ccMultiProviderSDK=()=>SDK;'
        + _MULTI_PROVIDER_HELPER
        + 'function identity(m,o){let v=normalize(m,o);if(o?.identity===!0)return v;return v}'
        + 'function secret(e){let k=e.replace(/^INPUT_/,"");return first(k)||second(k)||k.startsWith("OTEL_")}'
        + 'function fallback(m,n){if(disabled())return[m];let v=other(m,n);return[m,...v]}'
    )
    patched = _install_anthropic_accounts(source)
    script = tmp_path / "factory.mjs"
    script.write_text(
        'import assert from "node:assert/strict";'
        'class SDK{constructor(o){this._options=o}}'
        'const ua=()=>"native-user-agent",transport=o=>({tls:{ca:"test"}});'
        'const normalize=m=>m,first=()=>false,second=()=>false,disabled=()=>false,other=()=>["primary"];'
        + patched
        + 'process.env.CLAUDE_CODE_OAUTH_TOKEN_1="test-token";'
        + 'const client=await factory({model:"anthropic1:fable"});'
        + 'assert.equal(client._options.defaultHeaders["User-Agent"],"native-user-agent");'
        + 'assert.equal(client._options.fetchOptions.tls.ca,"test");'
        + 'assert.equal(identity("anthropic1:fable"),"claude-fable-5-1");'
        + 'assert.equal(identity("claude-fable-5-1"),"claude-fable-5-1");'
        + 'assert.equal(secret("CLAUDE_CODE_OAUTH_TOKEN_999"),true);'
        + 'assert.equal(secret("INPUT_CLAUDE_CODE_OAUTH_TOKEN_999"),true);'
        + 'assert.equal(secret("UNRELATED"),false);'
        + 'assert.deepEqual(fallback("anthropic1:fable"),["anthropic1:fable"]);'
        + 'assert.deepEqual(fallback("native"),["native","primary"]);'
        + 'delete process.env.CLAUDE_CODE_OAUTH_TOKEN_1;'
        + 'await assert.rejects(factory({model:"anthropic1:fable"}),{code:"EPROVIDERCREDENTIAL"});'
    )
    subprocess.run([runtime, str(script)], check=True, capture_output=True, text=True)  # noqa: S603


def test_real_upstream_account_factory(tmp_path: Path) -> None:
    root = os.environ.get("CCPATCH_NATIVE_SOURCE_ROOT")
    if root is None:
        pytest.skip("requires pristine native source")
    source = (Path(root) / "linux-x64/original.js").read_text()
    if 'VERSION:"2.1.280"' not in source:
        pytest.skip("requires .280 native source")
    patched = _MULTI_PROVIDER_SDK_TAIL.sub(_replace_multi_provider_sdk_tail, source)
    patched = _install_anthropic_accounts(patched)
    assert '"oauth-2025-04-20"' in patched
    assert 'const _ccMultiProviderAnthropicCatalog = {"//":' in patched
    assert re.search(r'async function [\w$]+\([^;]+?_ccMultiProviderPreflight', patched)
    assert '/^CLAUDE_CODE_OAUTH_TOKEN_[1-9][0-9]*$/.test(' in patched
