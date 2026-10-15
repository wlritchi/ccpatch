"""Regression coverage for Claude Code 2.1.301 patch anchors."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import (
    CHANNELS_ENABLED,
    COMPACT_SESSION,
    MODEL_SESSION_ONLY,
    PatchSet,
)


def _node(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    script = tmp_path / "upstream-301.mjs"
    script.write_text('import assert from "node:assert/strict";\n' + source)
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_channels_flag_reads_keep_org_policy_gate(tmp_path: Path) -> None:
    source = (
        'let st={channelsEnabled:!1};let off=st?.channelsEnabled!==!0;'
        'function enabled(){return allowed("allow_channels")&&k("tengu_harbor",!1)}'
        'function disabled(){if(!k("tengu_harbor",!1))return!0;'
        'let e=verdict("allow_channels");'
        'return e!==null&&e!=="cache_miss"&&e!=="route_missing"}'
    )
    patched = CHANNELS_ENABLED.apply(source)
    assert "tengu_harbor" not in patched
    _node(
        tmp_path,
        'const k=()=>{throw Error("flag must not be read")};let policy=null;'
        'const allowed=()=>policy===null,verdict=()=>policy;'
        + patched
        + """
assert.equal(off,false);
assert.equal(enabled(),true);assert.equal(disabled(),false);
policy="org_denied";
assert.equal(enabled(),false);assert.equal(disabled(),true);
policy="cache_miss";assert.equal(disabled(),false);
""",
    )


def test_compaction_flag_precedes_guard_with_bypass(tmp_path: Path) -> None:
    (force,) = [
        p for p in COMPACT_SESSION.patches if p.name == "force-compaction-on-flag"
    ]
    patch_set = PatchSet(name="force-compaction", patches=(force,))
    source = (
        "async function auto(e,n,r,s,h=0,y,S=!1){if(denied(s))return!1;"
        "if(!enabled())return!1;if(gated()&&!fits(n,r)&&!S)return!1;"
        "return over(e)}"
    )
    patched = patch_set.apply(source)
    _node(
        tmp_path,
        "let deny=false;const denied=()=>deny,enabled=()=>true,gated=()=>true,"
        "fits=()=>false,over=()=>false;"
        + patched
        + """
assert.equal(await auto([],0,0,0,0,undefined,false),false);
assert.equal(await auto([],0,0,0,0,undefined,true),false);
globalThis.__ccPendingCompact=true;
assert.equal(await auto([],0,0,0),true);
assert.equal(globalThis.__ccPendingCompact,false);
deny=true;globalThis.__ccPendingCompact=true;
assert.equal(await auto([],0,0,0),false);
assert.equal(globalThis.__ccPendingCompact,true);
""",
    )


def test_model_command_skips_saved_default_route(tmp_path: Path) -> None:
    source = (
        "function command(fe,ce){let De=!remote();clear(null);let failed=!1;"
        "let run=()=>{let Be=switchModel(session,fe,()=>store.getState(),setState,"
        'De,"command",ce,storage,(Ve)=>{failed=Ve?.kind==="failed"});return Be};'
        "return De?configSet(fe,run):run()}"
        "const props={onSelect:select,onSetDefault:(m)=>{saveRef.current=!0},"
        "onCancel:cancel,isStandaloneCommand:!0,showFastModeNotice:!0};"
    )
    patched = MODEL_SESSION_ONLY.apply(source)
    _node(
        tmp_path,
        'let state={mainLoopModel:"old"};const session={},storage={},'
        "store={getState:()=>state},saveRef={current:false};"
        "const setState=v=>{state=v},clear=()=>{},cancel=()=>{},select=()=>{};"
        'const remote=()=>{throw Error("must not consult persistence flag")};'
        'const configSet=()=>{throw Error("session-only pick must skip config.set")};'
        "const switchModel=(s,model,get,set,save)=>{assert.equal(save,false);"
        "set({...get(),mainLoopModel:model});return model};"
        + patched
        + """
assert.equal(command("openai:gpt-6.1-sol"),"openai:gpt-6.1-sol");
assert.equal(state.mainLoopModel,"openai:gpt-6.1-sol");
assert.equal(props.onSetDefault,undefined);
assert.match(props.headerText,/this session only/);
""",
    )
