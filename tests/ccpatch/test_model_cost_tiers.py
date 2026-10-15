"""Long-context price tiers in the injected model cost table."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import _MULTI_PROVIDER_MODEL_COSTS, _model_costs_patch


def _node(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    script = tmp_path / "cost-tiers.mjs"
    script.write_text('import assert from "node:assert/strict";\n' + source)
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_long_prompt_tiers_use_native_selection(tmp_path: Path) -> None:
    patch = _model_costs_patch(_MULTI_PROVIDER_MODEL_COSTS)
    source = (
        "var NX=Object.assign(Object.create(null),"
        "{[xS(gyt.firstParty)]:va,...catalog()});"
    )
    patched = patch.pattern.sub(patch.replacement, source)
    _node(
        tmp_path,
        'const xS=x=>x,gyt={firstParty:"claude"},va={},catalog=()=>({});'
        + patched
        # Same tier selection and arithmetic as the 2.1.301 cost function.
        + """
const tier=(e,n)=>{let r=e.longPrompt;if(r===void 0)return e;
 return n.input_tokens+(n.cache_read_input_tokens??0)+(n.cache_creation_input_tokens??0)>r.abovePromptTokens?r:e};
const usd=(e,n)=>{const r=tier(e,n);return n.input_tokens/1e6*r.inputTokens
 +n.output_tokens/1e6*r.outputTokens+(n.cache_read_input_tokens??0)/1e6*r.promptCacheReadTokens
 +(n.cache_creation_input_tokens??0)/1e6*r.promptCacheWriteTokens};
const sol=NX["openai:gpt-6.1-sol"];
const at=(read)=>usd(sol,{input_tokens:100000,output_tokens:1e6,cache_read_input_tokens:read});
assert.equal(at(172000).toFixed(4),"10.2172");
assert.equal(at(172001).toFixed(4),"15.4344");
assert.equal(sol.longPrompt.webSearchRequests,sol.webSearchRequests);
const m3=NX["minimax:minimax-m3"];
assert.equal(usd(m3,{input_tokens:512000,output_tokens:0}).toFixed(4),"0.1536");
assert.equal(usd(m3,{input_tokens:512001,output_tokens:0}).toFixed(4),"0.3072");
assert.equal(NX["moonshot:kimi-k3"].longPrompt,undefined);
""",
    )
