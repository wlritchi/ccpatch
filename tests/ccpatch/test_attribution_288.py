"""Keep native attribution policy and serializer branches on 2.1.288."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.module_runtime import source_modules
from ccpatch.patches import _attribution_function, _thread_modern_attribution

_ROUTE = 'function _ccMultiProviderRoute(_ccNativeClient,_ccRequest,_ccOptions={}){delete _ccOutbound[_ccField];return[_ccCached.client,_ccOutbound,{}]}'
_SOURCE = '''
function BASE(model){let pr=FOOTER(),commit=`Co-Authored-By: ${LABEL(model)} <noreply@anthropic.com>`;return{commit,pr}}
async function POLICY(model){return BASE(model??CURRENT())}
async function EFFECTIVE(){return REPLAY()?null:POLICY()}
async function COMPACT(){let flags=await FLAGS(1),snapshot=await EFFECTIVE();return "- Interactive flags ("+JSON.stringify(snapshot)}
async function FULL(){let flags=await FLAGS(1),snapshot=await EFFECTIVE();return "# Committing changes with git"+JSON.stringify(snapshot)}
var BASH={async prompt({model:M,tools:T}){return COMPACT()},isConcurrencySafe(){return false}};
async function SERIALIZE(E,T){let first="",second="",key=(T.byValueListing!==void 0?`V${T.byValueListing}:`:"")+first+second+("inputJSONSchema"in E&&E.inputJSONSchema?`${E.name}:${HASH(E.inputJSONSchema)}`:E.name);return {name:E.name,description:await BASH.prompt(T),key}}
'''


def test_comma_declared_policy_keeps_replay_and_request_isolation(
    tmp_path: Path,
) -> None:
    patched = _thread_modern_attribution(_SOURCE + _ROUTE)
    assert 'async function EFFECTIVE(){return REPLAY()?null:POLICY()}' in patched
    runtime = shutil.which('node')
    if runtime is None:
        pytest.skip('node is unavailable')
    script = tmp_path / 'attribution.mjs'
    script.write_text(
        'import assert from "node:assert/strict";'
        'import {createRequire} from "node:module";import.meta.require=createRequire(import.meta.url);'
        + patched
        + '''
const FLAGS=async()=>[],CURRENT=()=>"native",FOOTER=()=>"PR",LABEL=x=>x;
const REPLAY=()=>_ccAttributionScope.getStore()?.model==="replay";
function _ccMultiProviderAttribution(model,label){return {label:model,domain:"example.test"}}
const values=await Promise.all(["openai:a","zai:b","replay"].map(model=>SERIALIZE({name:"Bash"},{model})));
assert.equal(values[0][Symbol.for("ccpatch.attribution")].commit,"Co-Authored-By: openai:a <noreply@example.test>");
assert.equal(values[1][Symbol.for("ccpatch.attribution")].commit,"Co-Authored-By: zai:b <noreply@example.test>");
assert.equal(values[2][Symbol.for("ccpatch.attribution")],null);
assert(values[2].description.endsWith("null"));
assert(Object.isFrozen(values[0][Symbol.for("ccpatch.attribution")]));
assert.notEqual(values[0].key,values[1].key);
assert(!JSON.stringify(values).includes("ccpatch.attribution"));
'''
    )
    result = subprocess.run(  # noqa: S603 - Execute local regression code.
        [runtime, str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_native_288_serializer_and_policy_preserved() -> None:
    path = os.environ.get('CCPATCH_288_SOURCE')
    if path is None:
        pytest.skip('set CCPATCH_288_SOURCE to pristine 2.1.288 source')
    source = next(
        module.source
        for module in source_modules(Path(path).read_text())
        if '# Committing changes with git' in module.source
    )
    patched = _thread_modern_attribution(source + _ROUTE)
    match = re.search(
        r'async function ([\w$]+)\(([\w$]+),([\w$]+)\)\{return _ccAttributionScope.run',
        patched,
    )
    assert match is not None
    name, _, context = match.groups()
    original = _attribution_function(source, name)
    inner = _attribution_function(patched, name + '_ccInner')
    assert (
        inner.replace(name + '_ccInner', name, 1).replace(
            f'+JSON.stringify([{context}.model??null,{context}._ccAttributionSnapshot??null])',
            '',
            1,
        )
        == original
    )
    for name in ('mbt', 'ZSt', 'IQn'):
        before = _attribution_function(source, name)
        after = _attribution_function(patched, name)
        if name == 'ZSt':
            after = after.replace('_ccAttributionScope.getStore()?.model??', '')
        assert before == after
    assert 'if(!_ccSnapshot)return _ccBlocks' in patched
