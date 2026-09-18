"""Keep native catalogue validation separate from provider cost accounting."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import (
    _MULTI_PROVIDER_MODEL_COSTS,
    _model_costs_patch,
)

# Capture the cost catalogue and its callers from pristine .206 Linux x64.
_CATALOGUE = r"""function o3l(e){let t=G0(e),r=t===void 0?void 0:BVo(t);return r===void 0?void 0:A2e(i3l(e,r))}function i3l(e,t){let{input:r,output:n,cache_write_5m:o,cache_write_1h:i,cache_read:s,web_search:a}=t;if(o===void 0||s===void 0||a===void 0)throw new ro(`model catalog entry '${e}' has incomplete pricing — baked entries need the full ModelCosts shape`,"model catalog entry has incomplete pricing");return{inputTokens:r,outputTokens:n,promptCacheWriteTokens:o,...i!==void 0&&{promptCacheWrite1hTokens:i},promptCacheReadTokens:s,webSearchRequests:a}}function s9m(e){return i9m.has(e)}function a9m(){let e={};for(let t of rK().models){let r=BVo(t);if(r===void 0)continue;if(!s9m(t.id))throw new ro(`model catalog id '${t.id}' missing from CATALOG_MODEL_IDS — regenerate with 'bun run generate:model-catalog'`,"model catalog id missing from CATALOG_MODEL_IDS");e[t.id]=i3l(t.id,r)}return e}"""
_LOOKUP = 'function Rpi(e,t){let r=so(e);if(t.speed==="fast"){if(r==="claude-opus-4-8")return HLn;if(r==="claude-opus-4-6"||r==="claude-opus-4-7")return n3l}let n=bYe[r];if(n)return n;let o=St().additionalModelCostsCache,i=o?.[e]??o?.[r];if(i)return i;return c9m(e,r),bYe[so(sP())]??CLn}'
_DISPLAY = 'function s3l(e){let t=so(e),r=bYe[t];if(!r)return;return A2e(r)}'
_INITIALIZER = 'o9m={inputTokens:5,outputTokens:25,promptCacheWriteTokens:6.25,promptCacheWrite1hTokens:10,promptCacheReadTokens:0.5,webSearchRequests:0.01},n3l={inputTokens:30,outputTokens:150,promptCacheWriteTokens:37.5,promptCacheWrite1hTokens:60,promptCacheReadTokens:3,webSearchRequests:0.01},HLn={inputTokens:10,outputTokens:50,promptCacheWriteTokens:12.5,promptCacheWrite1hTokens:20,promptCacheReadTokens:1,webSearchRequests:0.01},CLn=o9m;i9m=new Set(EDl);bYe={[n$(Tyt.firstParty)]:HLn,[n$(SDl.firstParty)]:HLn,...a9m()}'


def _run_js(tmp_path: Path, source: str) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("requires node or bun")
    path = tmp_path / "costs.cjs"
    path.write_text(source)
    result = subprocess.run(  # noqa: S603 - execute the local test fixture
        [runtime, str(path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("null_prototype", [False, True])
def test_catalogue_cost_runtime(tmp_path: Path, null_prototype: bool) -> None:
    patch = _model_costs_patch(_MULTI_PROVIDER_MODEL_COSTS)
    assert callable(patch.replacement)
    initializer = _INITIALIZER
    if null_prototype:
        initializer = (
            initializer.replace("bYe={", "bYe=Object.assign(Object.create(null),{")
            + ")"
        )
    patched, count = patch.pattern.subn(patch.replacement, initializer)
    assert count == 1
    _run_js(
        tmp_path,
        'const assert=require("node:assert/strict");'
        + f"const providerCosts={json.dumps(_MULTI_PROVIDER_MODEL_COSTS)};"
        + _CATALOGUE
        + _LOOKUP
        + _DISPLAY
        + r"""
const ro=Error;
let models=[],cache={},unknown=0;
const EDl=['claude-opus-4-8','claude-sonnet-4-6'];
const Tyt={firstParty:'claude-opus-4-8'},SDl={firstParty:'legacy-fast'};
let o9m,n3l,HLn,CLn,i9m,bYe;
function rK(){return {models}}
function G0(id){return models.find(entry=>entry.id===id)}
function BVo(entry){return entry.pricing}
function so(id){return id.toLowerCase()}
function n$(id){return so(id)}
function St(){return {additionalModelCostsCache:cache}}
function sP(){return 'claude-sonnet-4-6'}
function c9m(){unknown++}
function A2e(cost){return `${cost.inputTokens}/${cost.outputTokens}`}
function init(){
"""
        + patched
        + r"""
}
const pricing={input:3,output:15,cache_write_5m:3.75,cache_write_1h:6,cache_read:.3,web_search:.01};
models=[{id:'claude-opus-4-8',pricing},{id:'claude-sonnet-4-6',pricing},
        {id:'unpriced-native'},{id:'zai:glm-5.3'}];
init();
assert.deepEqual([...i9m],EDl);
assert.equal(s9m('unpriced-native'),false);
assert.equal(s9m('zai:glm-5.3'),false);
assert.equal(o3l('zai:glm-5.3'),undefined);
assert.equal(o3l('claude-sonnet-4-6'),'3/15');
for(const [id,cost] of Object.entries(providerCosts)){
  assert.deepEqual(Rpi(id.toUpperCase(),{}),cost);
  assert.deepEqual(Rpi(id,{speed:'fast'}),cost);
  assert.equal(s3l(id.toUpperCase()),`${cost.inputTokens}/${cost.outputTokens}`);
  assert.equal(s9m(id),false);
  assert.equal(o3l(id),undefined);
}
// Native catalogue prices override the earlier native special entry.
assert.equal(Rpi('claude-opus-4-8',{}).inputTokens,3);
assert.equal(Rpi('claude-opus-4-8',{speed:'fast'}),HLn);
assert.equal(Rpi('claude-opus-4-7',{speed:'fast'}),n3l);
assert.equal(Rpi('legacy-fast',{}),HLn);
assert.equal(Rpi('claude-sonnet-4-6',{}).promptCacheWrite1hTokens,6);
cache={'zai:glm-5.3':{inputTokens:999},'Extra':{inputTokens:12},'extra':{inputTokens:13}};
assert.deepEqual(Rpi('zai:glm-5.3',{}),providerCosts['zai:glm-5.3']);
assert.equal(Rpi('Extra',{}).inputTokens,12);
assert.equal(Rpi('EXTRA',{}).inputTokens,13);
assert.equal(unknown,0);
assert.equal(Rpi('unknown',{}),bYe['claude-sonnet-4-6']);
assert.equal(unknown,1);
assert.equal(s3l('unknown'),undefined);
// Priced unknown IDs must still fail, including configured provider IDs.
for(const id of ['new-native','zai:glm-5.3']){
  models=[{id,pricing}];
  assert.throws(init,/missing from CATALOG_MODEL_IDS/);
}
models=[{id:'claude-opus-4-8',pricing:{input:3,output:15}}];
assert.throws(init,/incomplete pricing/);
""",
    )


@pytest.mark.parametrize("catalogue", [False, True])
def test_native_entries_override_injected_costs(
    tmp_path: Path, catalogue: bool
) -> None:
    costs = dict(_MULTI_PROVIDER_MODEL_COSTS)
    costs["claude-opus-4-8"] = costs["zai:glm-5.3"]
    patch = _model_costs_patch(costs)
    assert callable(patch.replacement)
    source = _INITIALIZER
    if not catalogue:
        source = source.replace("i9m=new Set(EDl);", "").replace(",...a9m()", "")
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    _run_js(
        tmp_path,
        'const assert=require("node:assert/strict");'
        'let o9m,n3l,HLn,CLn,i9m,bYe;const EDl=[];'
        'const Tyt={firstParty:"claude-opus-4-8"},SDl={firstParty:"legacy"};'
        'function n$(id){return id}const native={inputTokens:123};'
        'function a9m(){return {"claude-opus-4-8":native}};'
        + patched
        + ';assert.equal(bYe["claude-opus-4-8"],'
        + ("native" if catalogue else "HLn")
        + ');assert.equal(bYe["zai:glm-5.3"].inputTokens,'
        + str(costs["zai:glm-5.3"]["inputTokens"])
        + ");",
    )


def test_native_206_cost_evidence() -> None:
    root = os.environ.get("CCPATCH_NATIVE_SOURCE_ROOT")
    if root is None:
        pytest.skip("requires pristine .206 CCPATCH_NATIVE_SOURCE_ROOT")
    directory = Path(root)
    if directory.name != "2.1.206":
        directory = directory.parent / "2.1.206"
    paths = sorted(directory.glob("*/original.js"))
    if not paths:
        pytest.skip("requires pristine .206 CCPATCH_NATIVE_SOURCE_ROOT")
    patch = _model_costs_patch(_MULTI_PROVIDER_MODEL_COSTS)
    for path in paths:
        source = path.read_text()
        assert len(list(patch.pattern.finditer(source))) == 1, path
        if path.parent.name == "linux-x64":
            for capture in (_CATALOGUE, _LOOKUP, _DISPLAY, _INITIALIZER):
                assert capture.replace("—", "\\u2014") in source
